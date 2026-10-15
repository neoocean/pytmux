"""서버 쪽 statusline 소비(pytmux-527 C1 S1 §D3) — `ccbridge` 가 떨군 `sl/*.json` 을 읽어
**기존 status 필드**에 싣는다. 와이어(필드 이름·모양)는 그대로다 → 정본·Rust 클라·픽스처 무변경.

# 무엇을 어디에

| 출처(sl 파일) | 자리 | 비고 |
|---|---|---|
| `model.display_name`/`id` | 패널 `_claude_model` | 강한 값 — 디바운스·프로브 폴백이 필요 없다 |
| `context_window.used_percentage`·`size` | 패널 `_claude_usage` = `ctx:N%/1M` | 근사 `~` 가 없다 |
| `rate_limits.five_hour` | 패널 자기 5h%(`_tok5h_pct`) | 패널마다 자기 계정 한도 |
| 가장 신선한 `rate_limits` | 전역 `server._usage`(모양 그대로) | `source: "statusline"` |
| `claude_json` → `oauthAccount.emailAddress` | 계정(별칭) | 프로브 계정 폴백의 대체 |

주간 **Sonnet only** 는 statusline 에 없다 — 사용자 결정(2026-10-15 · 선택지 (나))대로 인패널
`/usage` 캡처 값만 쓰고 여기서는 **직전 값을 그대로 둔다**(stale 표기는 `usage_age_sec`).

# 언제

플러그인 `server_background` 가 2초마다 `sl/` 의 mtime 을 보고 바뀐 파일만 읽는다(코어가
간격을 아는 구조를 늘리지 않으려고 둔 훅). opt `claude_statusline_bridge` 가 꺼져 있으면
아무것도 안 읽는다 — 끈 사람에게는 종전과 같다.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from pytmuxlib import ipc

from . import ccbridge
from .claude import _alias_email, claude_model

POLL_SEC = 2.0
GC_EVERY = 3600.0          # sl 파일 청소 주기(초)
GC_AGE = 7 * 86400.0       # 이보다 오래된 sl·titled 파일은 거둔다(c1 §D5)
# 새 세션 경계에서 «이 관측은 지난 claude 의 것» 으로 버리는 하한의 여유(초). 새 claude 는
# 부팅 중(footer 감지 전)에 statusline 을 한 번 쓰므로 경계 시각보다 조금 이른 것은 받는다.
SESSION_FLOOR_SLACK = 10.0
# 이 시간보다 오래된 titled 표식은 지난 기동의 잔재로 본다.
TITLED_MAX_AGE = 600.0


def on(srv) -> bool:
    return bool(getattr(srv, "claude_statusline_bridge", False))


def cc_dir(srv) -> str:
    """`<state>/cc` — 시험은 `srv._cc_dir_path` 로 돌린다."""
    return getattr(srv, "_cc_dir_path", None) or os.path.join(
        ipc.default_state_dir(), "cc")


def init(srv) -> None:
    srv._cc_sl_mtimes = {}
    srv._cc_sl_data = {}
    srv._cc_bridge_keys = set()
    srv._cc_acct_cache = {}
    srv._cc_ns_exported = None
    srv._cc_last_gc = 0.0
    srv._usage_probe_spawns = 0
    srv._usage_probe_skipped = 0


def _st(srv, name, default):
    v = getattr(srv, name, None)
    if v is None:
        v = default() if callable(default) else default
        setattr(srv, name, v)
    return v


# ---- 패널 env(D1·D2) ----
def pane_env(srv, env: dict, pane_key) -> None:
    """패널 셸 env 에 심 PATH 와 패널 키를 얹는다. opt off·Windows 면 **흔적 0**."""
    if not on(srv) or not pane_key:
        return
    d = ccbridge.ensure_bridge(cc_dir(srv))
    if not d:
        return                      # Windows(문서화된 무동작) 또는 쓰기 실패
    old = env.get("PATH", "")
    env["PATH"] = d + (os.pathsep + old if old else "")
    env[ccbridge.ENV_PANE_KEY] = pane_key
    _st(srv, "_cc_bridge_keys", set).add(pane_key)


def pane_bridged(srv, p) -> bool:
    """이 패널 셸이 심을 받았고 다리가 **지금도** 켜져 있나(끄면 settings 가 지워져 훅이
    안 돈다 — 그런 패널에서 표식을 기다리면 10초를 헛되이 쓴다)."""
    k = getattr(p, "pane_key", None)
    return on(srv) and bool(k) and k in (getattr(srv, "_cc_bridge_keys", None) or ())


# ---- 값 읽기 ----
def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _rec(p):
    r = getattr(p, "_cc_sl", None)
    return r if isinstance(r, dict) else None


def pane_model(p) -> "str | None":
    r = _rec(p)
    m = r.get("model") if r else None
    if not isinstance(m, dict):
        return None
    for s in (m.get("display_name"), m.get("id")):
        if isinstance(s, str) and s:
            v = claude_model(s)
            if v:
                return v
    return None


def _size_label(n) -> str:
    n = int(n)
    if n >= 1_000_000 and n % 1_000_000 == 0:
        return "%dM" % (n // 1_000_000)
    if n >= 1000:
        return "%dK" % (n // 1000)
    return str(n)


def pane_ctx(p) -> "str | None":
    """`ctx:N%/1M` — 스크랩 판(`claude_usage`)과 같은 모양, 근사 `~` 없이."""
    r = _rec(p)
    cw = r.get("context_window") if r else None
    if not isinstance(cw, dict):
        return None
    used = _num(cw.get("used_percentage"))
    if used is None:
        rem = _num(cw.get("remaining_percentage"))
        if rem is None:
            return None
        used = 100 - rem
    used = max(0, min(100, int(round(used))))
    size = _num(cw.get("context_window_size"))
    return "ctx:%d%%/%s" % (used, _size_label(size)) if size else "ctx:%d%%" % used


def _window(rec, name):
    rl = rec.get("rate_limits") if rec else None
    w = rl.get(name) if isinstance(rl, dict) else None
    return w if isinstance(w, dict) else None


def pane_5h(p, now=None) -> "int | None":
    """패널 **자기** 5h%. 리셋 시각이 지났으면 0(CLI 도 지난 창을 뺀다)."""
    w = _window(_rec(p), "five_hour")
    if w is None:
        return None
    pct = _num(w.get("used_percentage"))
    if pct is None:
        return None
    ra = _num(w.get("resets_at"))
    if ra is not None and ra <= (time.time() if now is None else now):
        return 0
    return max(0, min(100, int(round(pct))))


def account_of(srv, rec) -> "tuple[str | None, str | None]":
    """sl 관측의 `.claude.json` → (별칭, 전체 이메일). 캐시는 mtime 으로 무효화한다."""
    path = rec.get("claude_json") if isinstance(rec, dict) else None
    if not isinstance(path, str) or not path:
        return None, None
    try:
        mt = os.stat(path).st_mtime
    except OSError:
        return None, None
    cache = _st(srv, "_cc_acct_cache", dict)
    hit = cache.get(path)
    if hit and hit[0] == mt:
        return hit[1], hit[2]
    alias = full = None
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        acc = obj.get("oauthAccount") if isinstance(obj, dict) else None
        em = acc.get("emailAddress") if isinstance(acc, dict) else None
        if isinstance(em, str) and "@" in em:
            local, _, dom = em.strip().partition("@")
            if local and dom:
                full = em.strip()
                alias = _alias_email(local, dom)
    except (OSError, ValueError):
        pass
    cache[path] = (mt, alias, full)
    return alias, full


def pane_account(srv, p):
    r = _rec(p)
    return account_of(srv, r) if r else (None, None)


# ---- 리셋 문자열(/usage 표기와 같게 — DB 중복 판정·동기화가 그 문자열을 비교한다) ----
def _tz_name() -> str:
    tz = os.environ.get("TZ", "")
    if "/" in tz:
        return tz.lstrip(":")
    try:
        link = os.path.realpath("/etc/localtime")
        i = link.find("zoneinfo/")
        if i >= 0:
            return link[i + len("zoneinfo/"):]
    except OSError:
        pass
    return ""


def _clock(t) -> str:
    h = t.tm_hour % 12 or 12
    ap = "am" if t.tm_hour < 12 else "pm"
    return ("%d%s" % (h, ap)) if t.tm_min == 0 else ("%d:%02d%s" % (h, t.tm_min, ap))


def fmt_reset(epoch, kind: str) -> "str | None":
    """`2pm (Asia/Seoul)`(세션) · `Jun 13 at 3am (Asia/Seoul)`(주간) — /usage 패널 표기."""
    e = _num(epoch)
    if e is None:
        return None
    t = time.localtime(e)
    s = _clock(t) if kind == "session" else "%s at %s" % (
        time.strftime("%b ", t) + str(t.tm_mday), _clock(t))
    tz = _tz_name()
    return "%s (%s)" % (s, tz) if tz else s


# ---- 적용 ----
def _apply_panes(srv, now) -> bool:
    data = _st(srv, "_cc_sl_data", dict)
    changed = False
    for p in srv._all_panes():
        key = getattr(p, "pane_key", None)
        rec = data.get(key) if key else None
        if rec is None:
            continue
        if _num(rec.get("observed_at")) is not None and \
                rec["observed_at"] < getattr(p, "_cc_sl_floor", 0.0):
            continue                     # 지난 claude 세션의 관측
        if rec is getattr(p, "_cc_sl", None):
            continue
        p._cc_sl = rec
        if not getattr(p, "_claude", None):
            continue                     # 아직 Claude 로 안 보인다 — 스캔이 다시 고른다
        m = pane_model(p)
        if m and m != p._claude_model:
            p._claude_model = m
            p._claude_model_weak = False
            p._claude_model_cand = None
            p._claude_model_cand_n = 0
            changed = True
        c = pane_ctx(p)
        if c and c != p._claude_usage:
            p._claude_usage = c
            changed = True
        changed = True                   # 5h% 가 바뀌었을 수 있다(status 재전송)
    return changed


def _freshest_with_limits(srv):
    best = None
    for rec in _st(srv, "_cc_sl_data", dict).values():
        rl = rec.get("rate_limits")
        if not isinstance(rl, dict) or not rl:
            continue
        ts = _num(rec.get("observed_at")) or 0.0
        if best is None or ts > (_num(best.get("observed_at")) or 0.0):
            best = rec
    return best


def _apply_global(srv) -> bool:
    """가장 최근 관측의 `rate_limits` 로 `server._usage` 를 짓는다(모양 그대로)."""
    rec = _freshest_with_limits(srv)
    if rec is None:
        return False                     # rate_limits 없는 입력 — 전역 값을 유지한다
    ts = _num(rec.get("observed_at")) or time.time()
    prev = srv._usage if isinstance(getattr(srv, "_usage", None), dict) else {}
    if prev.get("source") == "statusline" and \
            (getattr(srv, "_usage_ts", None) or 0) >= ts:
        return False                     # 이미 반영한 관측
    u = {}
    for src, dst in (("five_hour", "session"), ("seven_day", "week_all")):
        w = _window(rec, src)
        pct = _num(w.get("used_percentage")) if w else None
        if pct is not None:
            ra = _num(w.get("resets_at"))
            if ra is not None and ra <= ts:
                u[dst] = {"pct": 0, "reset": None, "resets_at": None}
            else:
                u[dst] = {"pct": max(0, min(100, int(round(pct)))),
                          "reset": fmt_reset(ra, dst), "resets_at": ra}
            continue
        old = prev.get(dst)
        if isinstance(old, dict):
            ora = _num(old.get("resets_at"))
            # CLI 는 지난 창을 통째로 뺀다 — 그 창의 리셋이 지났으면 0%.
            u[dst] = ({"pct": 0, "reset": None, "resets_at": None}
                      if ora is not None and ora <= ts else dict(old))
    if not u:
        return False
    # 주간 Sonnet only 는 statusline 에 없다 — 인패널 /usage 캡처 값을 stale 로 둔다(결정 (나)).
    if isinstance(prev.get("week_sonnet"), dict):
        u["week_sonnet"] = prev["week_sonnet"]
    acct, _full = account_of(srv, rec)
    if acct or prev.get("account"):
        u["account"] = acct or prev.get("account")
    if prev.get("model"):
        u["model"] = prev["model"]
    u["source"] = "statusline"
    srv._usage = u
    srv._usage_ts = ts
    rec_fn = getattr(srv, "_record_usage_snapshot", None)
    if callable(rec_fn):
        rec_fn(u, "statusline")
    return True


def apply_resets(srv, now) -> bool:
    """`resets_at` 이 지났는데 새 관측이 없으면 그 창을 0% 로 본다."""
    changed = False
    u = getattr(srv, "_usage", None)
    if isinstance(u, dict) and u.get("source") == "statusline":
        for dst in ("session", "week_all"):
            w = u.get(dst)
            ra = _num(w.get("resets_at")) if isinstance(w, dict) else None
            if ra is not None and ra <= now and w.get("pct") != 0:
                u[dst] = {"pct": 0, "reset": None, "resets_at": None}
                changed = True
    for p in srv._all_panes():
        w = _window(_rec(p), "five_hour")
        ra = _num(w.get("resets_at")) if w else None
        if ra is not None and ra <= now and getattr(p, "_cc_reset_seen", None) != ra:
            p._cc_reset_seen = ra        # 경계를 한 번만 알린다(status 재전송)
            changed = True
    return changed


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def poll(srv, now=None) -> bool:
    """`sl/` 에서 바뀐 파일만 읽어 패널·전역에 싣는다. 무엇이든 바뀌면 True."""
    now = time.time() if now is None else now
    mtimes = _st(srv, "_cc_sl_mtimes", dict)
    data = _st(srv, "_cc_sl_data", dict)
    d = ccbridge.sl_dir(cc_dir(srv))
    fresh = False
    seen = set()
    try:
        entries = list(os.scandir(d))
    except OSError:
        entries = []
    for e in entries:
        name = e.name
        if not name.endswith(".json") or name.startswith("."):
            continue
        key = name[:-5]
        try:
            mt = e.stat().st_mtime
        except OSError:
            continue
        seen.add(key)
        if mtimes.get(key) == mt:
            continue
        rec = _read_json(e.path)
        if rec is None:
            continue
        mtimes[key] = mt
        data[key] = rec
        fresh = True
    for k in [k for k in data if k not in seen]:
        data.pop(k, None)
        mtimes.pop(k, None)
    changed = False
    if fresh:
        changed |= _apply_panes(srv, now)
        changed |= _apply_global(srv)
    changed |= apply_resets(srv, now)
    changed |= _poll_titled(srv, now)
    return changed


def _poll_titled(srv, now) -> bool:
    """SessionStart 훅의 파일 표식(`titled/<key>.json`) — OSC 가 안 나가는 경우의 대역."""
    d = ccbridge.titled_dir(cc_dir(srv))
    try:
        names = os.listdir(d)
    except OSError:
        return False
    by_key = {getattr(p, "pane_key", None): p for p in srv._all_panes()}
    for n in names:
        if not n.endswith(".json"):
            continue
        key = n[:-5]
        p = by_key.get(key)
        if p is None:
            continue
        path = os.path.join(d, n)
        rec = _read_json(path)
        try:
            os.unlink(path)
        except OSError:
            pass
        if not rec or (now - (_num(rec.get("ts")) or 0)) > TITLED_MAX_AGE:
            continue
        kw = rec.get("kw")
        if isinstance(kw, str) and kw.strip():
            p._cc_titled = kw.strip()
    return False                         # 표시 상태가 아니다


def export_namesync(srv) -> None:
    """name-sync 규칙을 훅이 읽을 `namesync.json` 으로 내보낸다(c3 §2).

    규칙의 정본은 name-sync 의 `server._namesync_rules` 다. 그 플러그인이 없거나 꺼져
    있으면 파일을 지운다 — 훅은 파일이 없으면 아무것도 안 낸다(delete-to-disable)."""
    path = ccbridge.namesync_path(cc_dir(srv))
    names = {getattr(p, "name", "") for p in getattr(
        getattr(srv, "plugins", None), "plugins", ())}
    rules = getattr(srv, "_namesync_rules", None)
    if "claude-name-sync" not in names or not isinstance(rules, list):
        if getattr(srv, "_cc_ns_exported", None) is not None or os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
            srv._cc_ns_exported = None
        return
    body = json.dumps(rules, ensure_ascii=False).encode("utf-8")
    if body == getattr(srv, "_cc_ns_exported", None) and os.path.exists(path):
        return
    try:
        ccbridge.write_if_changed(path, body)
        srv._cc_ns_exported = body
    except OSError:
        pass


def gc(srv, now) -> None:
    base = cc_dir(srv)
    for d in (ccbridge.sl_dir(base), ccbridge.titled_dir(base)):
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            try:
                if now - e.stat().st_mtime > GC_AGE:
                    os.unlink(e.path)
            except OSError:
                pass


def probe_suppressed(srv, now=None) -> bool:
    """신선한 statusline 값이 있으면 그림자 프로브를 안 띄운다(S1 — 프로브는 폴백)."""
    if not on(srv):
        return False
    u = getattr(srv, "_usage", None)
    ts = getattr(srv, "_usage_ts", None)
    if not isinstance(u, dict) or u.get("source") != "statusline" or ts is None:
        return False
    window = max(60.0, float(getattr(srv, "usage_refresh_sec", 600) or 600))
    return ((time.time() if now is None else now) - ts) < window


def tick(srv, now=None) -> bool:
    """한 바퀴 — 시험이 루프 없이 부른다. 바뀌었으면 True(호출부가 방송한다)."""
    if not on(srv):
        return False
    now = time.time() if now is None else now
    export_namesync(srv)
    changed = poll(srv, now)
    if now - (getattr(srv, "_cc_last_gc", 0.0) or 0.0) > GC_EVERY:
        srv._cc_last_gc = now
        gc(srv, now)
    return changed


def broadcast(srv) -> None:
    for s in list(getattr(srv, "sessions", {}).values()):
        srv._broadcast_session(s)


async def run_loop(srv) -> None:
    while True:
        try:
            if tick(srv):
                broadcast(srv)
        except Exception:               # noqa: BLE001 — 한 바퀴 실패가 루프를 죽이지 않게
            log = getattr(srv, "_log_error", None)
            if callable(log):
                log("cc_statusline")
        await asyncio.sleep(POLL_SEC)
