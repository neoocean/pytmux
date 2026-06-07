"""Claude Code 토큰 사용량 영속 로깅 + 집계(docs/HANDOFF.md §10 #7).

`tokens.py` 가 응답별 peak 를 누계에 **확정**(committed>0)하는 그 이벤트를 한 건의
로그 레코드로 적는다(중복 없이 정확히 1응답=1레코드). 레코드는 JSONL 한 줄:

  {"ts": 1717500000.0, "tab": 0, "pane": 3, "session": 7,
   "account": "wo…@woojinkim.org", "tokens": 4200}

여기 함수들은 **순수**(파일 IO 한 함수 제외)라 서버/클라/테스트 어디서나 부른다.
서버는 append 만, 클라이언트 조회 화면은 read+aggregate 로 시간/일/월 × 계정 집계.

조회 화면은 큰 로그를 다룰 수 있으니 read(limit=) 로 최근 N 줄만 증분 읽기 가능.
"""
from __future__ import annotations

import datetime as _dt
import json
import os

UNKNOWN = "unknown"

# 집계 버킷 → strftime 포맷. "hour"=시간, "day"=일, "week"=주, "month"=월.
# 주는 ISO-8601 주차(%G=ISO 연도, %V=ISO 주차, 월요일 시작) → "2023-W46".
_BUCKET_FMT = {
    "hour": "%Y-%m-%d %H:00",
    "day": "%Y-%m-%d",
    "week": "%G-W%V",
    "month": "%Y-%m",
}


def is_trusted(account, keep_accounts, keep_domains) -> bool:
    """account 가 신뢰 허용목록에 들면 True. None/빈 값/unknown 은 신뢰 아님.

    keep_accounts: 정확히 일치해야 하는 계정 별칭 집합.
    keep_domains: 별칭이 `@<domain>` 으로 끝나면 신뢰(서브도메인 아님, 정확 도메인).
    계정 정정(오검출 이메일 → unknown)의 단일 신뢰 규칙 — migrate 도구와 DB
    update_accounts 가 공유한다(docs/TOKEN_USAGE_STORAGE_DESIGN.md §2.4)."""
    if not account or account == UNKNOWN:
        return False
    if account in keep_accounts:
        return True
    for d in keep_domains:
        if account.endswith("@" + d):
            return True
    return False


def remap_account(account, keep_accounts, keep_domains) -> str:
    """신뢰 계정이면 그대로, 아니면 UNKNOWN."""
    return account if is_trusted(account, keep_accounts, keep_domains) else UNKNOWN


def make_record(ts: float, tab, pane: int, session: int,
                account: str | None, tokens: int) -> dict:
    """로그 레코드 한 건을 만든다(append 직전 서버가 호출)."""
    return {"ts": float(ts), "tab": tab, "pane": int(pane),
            "session": int(session), "account": account or UNKNOWN,
            "tokens": int(tokens)}


def append(path: str, record: dict) -> bool:
    """레코드 한 줄을 JSONL 로 append. 실패해도 조용히 False(로깅이 본 흐름을 막지 않음)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read(path: str, limit: int | None = None) -> list:
    """JSONL 로그를 레코드 리스트로 읽는다(깨진 줄은 건너뜀). limit=N 이면 최근 N 줄만."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    if limit is not None and limit >= 0:
        lines = lines[-limit:]
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if isinstance(obj, dict) and "tokens" in obj:
            out.append(obj)
    return out


def bucket_key(ts: float, bucket: str) -> str:
    """타임스탬프를 버킷 키 문자열로(로컬 시간 기준)."""
    fmt = _BUCKET_FMT.get(bucket, _BUCKET_FMT["day"])
    return _dt.datetime.fromtimestamp(ts).strftime(fmt)


def group_key(r: dict, dim: str = "account") -> str:
    """레코드의 그룹 차원 키(라벨). dim="account"=계정, dim="session"=세션 기준.

    세션은 닫히고 재사용되는 패널 id 대신 안정적인 claude 세션 id 로 묶는다
    (설계 §8 — 사용자 결정 '세션 기준 묶기')."""
    if dim == "session":
        sid = r.get("session")
        return f"세션 {sid}" if sid is not None else "세션 ?"
    return r.get("account") or UNKNOWN


def aggregate(records: list, bucket: str = "day",
              account: str | None = None, dim: str = "account") -> dict:
    """레코드를 (버킷 × 그룹차원)으로 합산.

    account 가 주어지면 그 계정만 필터(dim 과 무관하게 항상 계정 필터). dim 은
    그룹 차원("account"=계정, "session"=세션). 반환:
      {"buckets": {bucket_key: {group: tokens}},
       "groups": {group: tokens},         # 그룹별 총합(많이 쓴 순 정렬은 표시 측)
       "accounts": {group: tokens},       # 하위호환 별칭(=groups)
       "total": int}                      # 전체 총합
    """
    buckets: dict = {}
    groups: dict = {}
    total = 0
    for r in records:
        acct = r.get("account") or UNKNOWN
        if account is not None and acct != account:
            continue
        g = group_key(r, dim)
        tok = int(r.get("tokens", 0))
        bk = bucket_key(r.get("ts", 0.0), bucket)
        buckets.setdefault(bk, {})
        buckets[bk][g] = buckets[bk].get(g, 0) + tok
        groups[g] = groups.get(g, 0) + tok
        total += tok
    # "accounts" 는 하위호환 별칭(기존 호출부가 agg["accounts"] 를 읽음).
    return {"buckets": buckets, "groups": groups, "accounts": groups,
            "total": total}


def _fmt_tokens(total: int) -> str:
    """누계를 짧게 표기(tokens.fmt 와 동일 규칙, 의존 줄이려 여기 둠)."""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M".replace(".0M", "M")
    if total >= 1_000:
        return f"{total / 1_000:.1f}k".replace(".0k", "k")
    return str(total)


# 막대 게이지용 부분블록(1/8 단위) — 우측 끝 잔량을 부드럽게 표현.
_BAR_BLOCKS = " ▏▎▍▌▋▊▉█"


def bar(value: int, vmax: int, cells: int) -> str:
    """value/vmax 비율을 cells 칸 막대 문자열로(부분블록 포함). vmax<=0/cells<=0 이면
    빈 문자열. 표시 계층(DataTable/InfoScreen) 공용 — 폭은 호출부가 셀폭으로 계산."""
    if cells <= 0 or vmax <= 0 or value <= 0:
        return ""
    frac = max(0.0, min(1.0, value / vmax))
    eighths = int(round(frac * cells * 8))
    full, rem = divmod(eighths, 8)
    full = min(full, cells)
    s = "█" * full + (_BAR_BLOCKS[rem] if rem and full < cells else "")
    return s


def _bucket_short(bk: str, bucket: str) -> str:
    """버킷 키를 짧은 표시 라벨로(연도 등 반복 정보 제거). day=MM-DD, month=YYYY-MM,
    week=W##(연도는 헤더에 한 번), hour=MM-DD HH시."""
    if bucket == "day" and len(bk) == 10:          # YYYY-MM-DD → MM-DD
        return bk[5:]
    if bucket == "week" and "-W" in bk:            # YYYY-W## → W##
        return "W" + bk.split("-W", 1)[1]
    if bucket == "hour" and len(bk) >= 13:         # YYYY-MM-DD HH:00 → MM-DD HH시
        return bk[5:10] + " " + bk[11:13] + "시"
    return bk                                       # month(YYYY-MM) 등은 그대로


def _session_tabpane(records: list) -> dict:
    """세션 id → 대표 '탭T:pP' 라벨(그 세션 레코드의 최빈 tab:pane). 표시 1-based 탭."""
    by: dict = {}
    for r in records:
        sid = r.get("session")
        if sid is None:
            continue
        key = (r.get("tab"), r.get("pane"))
        by.setdefault(sid, {})
        by[sid][key] = by[sid].get(key, 0) + 1
    out = {}
    for sid, counts in by.items():
        (tab, pane), _ = max(counts.items(), key=lambda kv: kv[1])
        tlabel = (tab + 1) if isinstance(tab, int) else "?"
        out[sid] = f"탭{tlabel}:p{pane}"
    return out


def agg_view(records: list, bucket: str = "day", account: str | None = None,
             dim: str = "account", order: str = "time") -> dict:
    """표시(DataTable) 전용 집계 — 정렬·라벨·비율까지 계산해 렌더가 바로 쓰게 한다.

    반환:
      {"total": int,
       "groups": [(label, tokens, share_pct)],   # 묶음(계정/세션) 총합, 토큰 많은 순
       "buckets": [(label, tokens, share_pct)],   # 시간축, order("time"|"tokens")
       "multi": bool,                              # 그룹 2개 이상(=중복 분해 가치 있음)
       "gmax": int, "bmax": int}                   # 막대 기준(각 목록의 최대 토큰)
    세션 차원은 라벨에 대표 탭:패널을 곁들인다('세션 4 (탭2:p3)')."""
    agg = aggregate(records, bucket, account, dim)
    total = agg["total"]
    tp = _session_tabpane(records) if dim == "session" else {}

    def glabel(g):
        if dim == "session" and g.startswith("세션 "):
            try:
                sid = int(g.split(" ", 1)[1])
            except (ValueError, IndexError):
                sid = None
            tpl = tp.get(sid)
            return f"{g} ({tpl})" if tpl else g
        return g

    def pct(tok):
        return round(tok / total * 100) if total else 0

    groups = sorted(agg["groups"].items(), key=lambda kv: -kv[1])
    grows = [(glabel(g), t, pct(t)) for g, t in groups]
    bucket_tot = {bk: sum(per.values()) for bk, per in agg["buckets"].items()}
    if order == "tokens":
        bkeys = sorted(bucket_tot, key=lambda k: -bucket_tot[k])
    else:
        bkeys = sorted(bucket_tot, reverse=True)        # 시간 내림차순(최근 위)
    brows = [(_bucket_short(bk, bucket), bucket_tot[bk], pct(bucket_tot[bk]))
             for bk in bkeys]
    return {"total": total, "groups": grows, "buckets": brows,
            "multi": len(grows) > 1,
            "gmax": max((t for _, t, _ in grows), default=0),
            "bmax": max((t for _, t, _ in brows), default=0)}


def summary_lines(records: list, bucket: str = "day",
                  account: str | None = None, dim: str = "account") -> list:
    """조회 화면(InfoScreen)용 사람이 읽는 집계 줄 목록을 만든다.

    버킷별로 그룹(계정 또는 세션) 합계를 보이고, 맨 위에 그룹 총합·전체 총합
    헤더를 둔다. dim="session" 이면 세션 기준으로 묶는다([패널] 탭). 레코드가
    없으면 안내 한 줄."""
    agg = aggregate(records, bucket, account, dim)
    if not records or agg["total"] == 0:
        return ["(기록된 토큰 사용량이 없습니다)"]
    lines = []
    scope = f" · 계정={account}" if account else ""
    label = "세션" if dim == "session" else "계정"
    lines.append(f"토큰 사용량 — 단위:{bucket} · {label}별{scope}"
                 f"  전체 Σ{_fmt_tokens(agg['total'])}")
    # 그룹별 총합(많이 쓴 순)
    for g, tok in sorted(agg["groups"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  [{g}] Σ{_fmt_tokens(tok)}")
    lines.append("")
    # 버킷별(최근이 위로) × 그룹
    for bk in sorted(agg["buckets"], reverse=True):
        per = agg["buckets"][bk]
        parts = "  ".join(f"{a}:{_fmt_tokens(t)}"
                          for a, t in sorted(per.items(), key=lambda kv: -kv[1]))
        bucket_total = sum(per.values())
        lines.append(f"{bk}  Σ{_fmt_tokens(bucket_total)}   {parts}")
    return lines
