"""Tier C **선언형 화면 스펙** — claude-code 의 팝업 넷을 자료로 준다(pytmux-35).

# 왜 이 파일인가

정본은 이 넷을 Textual 팝업으로 띄운다(`screens.py`). 네이티브 클라는 파이썬을 못 읽어
그 화면을 못 띄우고, 그래서 팔레트에는 보이는데 눌러도 *"이 플러그인은 화면 스펙을
제공하지 않습니다"* 로 끝났다 — 죽은 줄 넷이다:

| 명령 | 정본 화면 | 여기서 |
|---|---|---|
| `claude-settings` | `ClaudeSaverScreen` | `form` — 줄마다 현재값, Enter 로 토글·순환 |
| `claude-rules` | `RulesEditScreen` | `prompt` — 지금 규칙을 **초기값으로** 물어본다 |
| `model` | 토큰 팝업의 `[한도]` 탭 | `list` — 모델×컨텍스트, 고르면 `/model` 주입 |
| `claude-token-log` | `TokenLogScreen` | `table` — 일별 집계(전체 재현은 EXT-0008) |
| `prompt-clear-queue` | `InfoScreen` | `list` — 쌓인 명령(비우기는 `c`) |

여기에 **명령이 아닌 판 둘**이 붙어 있다 — `claude-perm-mode`·`claude-remote-control`.
팔레트에 없고 **Claude 패널 안 footer 를 눌러야** 열린다(pytmux-2 · 규칙은
[`footerzones`](footerzones)). 정본도 명령이 아니라 클릭으로만 여는 팝업이라 표면은
안 늘었고, 다만 여는 쪽이 **누른 패널 id** 를 실어 보낸다는 점이 다르다.

# 무엇을 옮기고 무엇을 안 옮겼나

**규칙은 안 옮겼다.** 토글의 전이(`SAVER_CYCLES`·`_cycle_next`)도, 모델 후보
(`ModelCtxScreen._MODELS`)도 이미 정본에 있는 것을 **그대로 부른다** — 여기서 다시
적으면 그 순간 두 벌이 되고, 갈리는 순간 같은 설정이 클라마다 다른 값을 보인다.
그것이 pytmux-35 를 만든 모양 그대로다.

**모양은 안 옮겼다.** 정본 토큰 팝업의 계층 타임라인·`[한도]` 탭·대사 뷰는 스펙이
훨씬 커서 별도 슬라이스로 남는다(EXT-0008). 여기 있는 것은 *"기간별로 얼마나 썼나"*
한 판이고, 그것만으로도 그 줄은 더 이상 죽어 있지 않다.

# UI 무의존

`screens.py`(Textual)를 import 하지 않는다 — 서버 프로세스가 이 모듈을 읽는다.
필요한 상수는 그 모듈의 **클래스 속성**을 지연 import 로 꺼내 쓴다(값만 읽는다).
"""
from __future__ import annotations

from pytmuxlib import i18n

# 화면 id = 명령 이름. 별칭은 여기 한 벌로 모은다(정본 `handle_command` 와 같은 표).
PC_QUEUE = ("prompt-clear-queue", "pc-queue")
RULES = ("claude-rules", "rules", "startup-rules")
SETTINGS = ("claude-settings",)
MODEL = ("model", "model-config", "claude-model")
TOKEN_LOG = ("claude-token-log", "token-usage")
# 이 둘은 팔레트 명령이 아니다 — **패널 안 footer 를 눌러야** 열린다(pytmux-2).
# 정본도 명령이 아니라 클릭으로만 여는 팝업이라 표면이 안 늘었다.
PERM = ("claude-perm-mode",)
RC = ("claude-remote-control",)

i18n.register({
    "ko": {
        "pscreen.spec_settings_title": "Claude 설정",
        "pscreen.spec_settings_hint": "↑↓ 이동 · Enter 바꾸기 · Esc 닫기",
        "pscreen.spec_rules_title": "Claude 시작 규칙 — 새 세션·/clear 뒤 자동 주입",
        "pscreen.spec_rules_empty": "(지금은 비어 있습니다. 빈 채로 저장하면 지웁니다.)",
        "pscreen.spec_model_title": "Claude 모델·컨텍스트",
        "pscreen.spec_model_hint": "↑↓ 이동 · Enter 적용(/model 주입) · Esc 닫기",
        "pscreen.spec_model_now": "지금",
        "pscreen.spec_pcq_title": "프롬프트 단위 클리어 큐",
        "pscreen.spec_pcq_hint": "c 비우기 · Esc 닫기",
        "pscreen.spec_pcq_empty": "(큐가 비어 있습니다 — `:prompt-clear-queue <명령>` 으로 쌓습니다)",
        "pscreen.spec_tklog_title": "토큰 사용량(추정) · 일별",
        "pscreen.spec_tklog_title_sum": "토큰 사용량(추정) · 일별 · Σ{tok}",
        "pscreen.spec_tklog_hint": "↑↓ 이동 · Esc 닫기",
        "pscreen.spec_tklog_empty": "기록된 토큰 사용량이 없습니다",
        "pscreen.spec_tklog_nodb": "토큰 DB 를 열 수 없습니다",
        "pscreen.spec_tklog_col_tok": "토큰",
        "pscreen.spec_tklog_col_pct": "5h 최대",
        "pscreen.spec_on_mark": "●",
        "pscreen.spec_off_mark": "○",
        "pscreen.rc_hint": "r 원격 제어 토글(/rc) · ↑↓ 스크롤 · Esc 닫기",
    },
    "en": {
        "pscreen.spec_settings_title": "Claude settings",
        "pscreen.spec_settings_hint": "↑↓ move · Enter change · Esc close",
        "pscreen.spec_rules_title": "Claude start rules — injected after a new session/clear",
        "pscreen.spec_rules_empty": "(empty for now. Saving it empty clears the rules.)",
        "pscreen.spec_model_title": "Claude model/context",
        "pscreen.spec_model_hint": "↑↓ move · Enter apply (injects /model) · Esc close",
        "pscreen.spec_model_now": "now",
        "pscreen.spec_pcq_title": "Per-prompt clear queue",
        "pscreen.spec_pcq_hint": "c clear · Esc close",
        "pscreen.spec_pcq_empty": "(the queue is empty — add with `:prompt-clear-queue <command>`)",
        "pscreen.spec_tklog_title": "Token usage (estimated) · by day",
        "pscreen.spec_tklog_title_sum": "Token usage (estimated) · by day · Σ{tok}",
        "pscreen.spec_tklog_hint": "↑↓ move · Esc close",
        "pscreen.spec_tklog_empty": "No token usage recorded",
        "pscreen.spec_tklog_nodb": "Cannot open the token DB",
        "pscreen.spec_tklog_col_tok": "tokens",
        "pscreen.spec_tklog_col_pct": "5h peak",
        "pscreen.spec_on_mark": "●",
        "pscreen.spec_off_mark": "○",
        "pscreen.rc_hint": "r toggle remote control (/rc) · ↑↓ scroll · Esc close",
    },
})


def _spec(sid, kind, title, hint, rows=(), text="", note="", keys=None, selected=0,
          carried=None):
    """스펙 한 판 — 칸을 빠뜨리지 않게 한 곳에서 짓는다.

    `rows`·`text` 를 늘 싣는 이유: 클라 파서가 `default` 로 채우긴 하지만, 빠진 칸은
    "안 온 것"과 "빈 것"의 구분을 사람이 못 하게 만든다."""
    return {
        "t": "plugin_screen", "id": sid, "kind": kind,
        "title": title, "hint": hint,
        "rows": list(rows), "text": text, "note": note,
        "selected": max(0, int(selected)),
        "keys": dict(keys or {}),
        "i18n": dict(carried or {}),
    }


def _close(sid):
    return {"t": "plugin_screen_close", "id": sid}


def _active_pane(sess):
    win = getattr(sess, "active_window", None) if sess is not None else None
    return getattr(win, "active_pane", None) if win is not None else None


def _as_pane_id(value):
    """와이어로 온 패널 id → `int`(못 고치면 `None` = 활성 패널).

    ⚠ **여기서 안 고치면 조용히 활성 패널이 된다.** 클릭존이 여는 화면은 패널 id 를
    `plugin_open` 의 `args` 로 받는데 그 칸은 **문자열**이고(GUI 는 `pane.to_string()`),
    `Window.pane_by_id` 는 `p.id == pid` 로 비교한다 — `3 == "3"` 은 거짓이라 못 찾고,
    부르는 쪽은 죄다 `... or win.active_pane` 으로 우아하게 내려간다. 그래서 비활성
    Claude 패널의 footer 를 눌러도 **활성 패널**의 모드가 바뀌었다(그 사고를 막으려고
    id 를 실어 보낸 것인데 그 id 가 도착해서 버려졌다). 들어오는 자리에서 한 번 고친다."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pane(sess, pane_id):
    """`pane_id` 가 가리키는 패널(없거나 못 찾으면 활성 패널).

    클릭존은 **누른 그 패널**을 가리킨다 — 비활성 Claude 패널의 footer 를 눌러도
    활성 패널의 모드를 바꾸면 안 된다(정본 `_open_perm_mode` 도 pane_id 를 받는다)."""
    win = getattr(sess, "active_window", None) if sess is not None else None
    if win is not None and pane_id is not None:
        try:
            p = win.pane_by_id(int(pane_id))
        except (TypeError, ValueError):
            p = None
        if p is not None:
            return p
    return _active_pane(sess)


# ── prompt-clear-queue — 쌓인 명령 목록 ─────────────────────────────────────
def _pc_queue_spec(server, sess, selected=0):
    pane = _active_pane(sess)
    q = list(getattr(pane, "prompt_clear_queue", ()) or ()) if pane else []
    rows = [{"key": str(i), "label": f"{i + 1}. {cmd}", "cols": []}
            for i, cmd in enumerate(q)]
    return _spec("pc-queue", "list",
                 i18n.t("pscreen.spec_pcq_title"),
                 i18n.t("pscreen.spec_pcq_hint"),
                 rows=rows,
                 note="" if rows else i18n.t("pscreen.spec_pcq_empty"),
                 keys={"c": "clear"}, selected=selected)


# ── claude-rules — 지금 규칙을 초기값으로 물어본다 ──────────────────────────
def _rules_spec(server):
    """`prompt` 판. `text` 가 **입력칸의 초기값**이다 — 규칙을 고치는 화면인데 초기값이
    없으면 사람은 지금 규칙을 다시 쳐야 하고, 그러면 '편집'이 아니라 '덮어쓰기'다."""
    cur = str(getattr(server, "claude_rules", "") or "")
    return _spec("claude-rules", "prompt",
                 i18n.t("pscreen.spec_rules_title"), "",
                 text=cur,
                 note=cur or i18n.t("pscreen.spec_rules_empty"),
                 keys={"enter": "save"})


# ── claude-settings — 정본 설정 팝업의 줄들 ────────────────────────────────
def _saver_rows():
    from . import SAVER_ROWS
    return SAVER_ROWS


def _saver_value(server, sess, key):
    """그 줄이 보일 현재값 — 값의 **원본**은 status 를 채우는 서버·활성 패널이다
    (정본 `saver_display` 는 같은 값을 클라 `status` 에서 읽는다).

    ⚠ 낱말은 정본과 같은 것을 쓴다(`REDRAW_WORDS`·`VERIFY_WORDS` — 카탈로그 키가
    한국어 원문이라 이 클라가 자기 로케일로 다시 읽는다). **수는 그냥 수로 보낸다**:
    `600초 이상` 처럼 수와 낱말이 붙은 글은 원문이 키가 못 돼 어느 카탈로그도 못 잡고
    (`PluginRow::say_cols` 는 고정 리터럴만 본다), 단위는 이미 줄 이름에 있다
    (`장기 턴 경고(초)`). 정본이 붙여 쓰는 것은 그 화면의 관례다 — 스펙은 내용을 정하고
    표현은 각 클라 관례를 따른다(설계 §6)."""
    from . import REDRAW_WORDS, VERIFY_WORDS, norm_redraw_mode, norm_resume_verify
    pane = _active_pane(sess)
    on = i18n.t("pscreen.spec_on_mark")
    off = i18n.t("pscreen.spec_off_mark")
    if key == "autoresume":
        return on if (pane is not None and getattr(pane, "autoresume", False)) else off
    if key == "prompt_clear":
        return on if (pane is not None
                      and getattr(pane, "prompt_clear_mode", False)) else off
    if key == "auto_token_on_exit":
        return on if getattr(server, "auto_token_on_exit", False) else off
    if key == "claude_auto_mode":
        return on if getattr(server, "claude_auto_mode", False) else off
    if key == "claude_auto_redraw":
        mode = norm_redraw_mode(getattr(server, "claude_auto_redraw", "off"))
        return i18n.t(REDRAW_WORDS.get(mode, REDRAW_WORDS["off"]))
    if key == "claude_resume_verify":
        mode = norm_resume_verify(getattr(server, "claude_resume_verify", "off"))
        return i18n.t(VERIFY_WORDS.get(mode, VERIFY_WORDS["off"]))
    if key == "long_turn":
        v = int(getattr(server, "claude_long_turn_sec", 0) or 0)
        return i18n.t(REDRAW_WORDS["off"]) if v <= 0 else str(v)
    if key == "repeat_alert":
        v = int(getattr(server, "claude_repeat_alert", 0) or 0)
        return i18n.t(REDRAW_WORDS["off"]) if v <= 0 else str(v)
    return ""


def _settings_spec(server, sess, selected=0):
    rows = [{"key": key, "label": i18n.t(label),
             "cols": [_saver_value(server, sess, key)]}
            for key, label, _typ in _saver_rows()]
    return _spec("claude-settings", "form",
                 i18n.t("pscreen.spec_settings_title"),
                 i18n.t("pscreen.spec_settings_hint"),
                 rows=rows, keys={"enter": "toggle"}, selected=selected)


def _settings_toggle(server, sess, key):
    """한 줄을 바꾼다 — **전이 규칙은 정본 것 그대로**(`_cycle_next`·`SAVER_CYCLES`).

    정본 `saver_action` 과 짝이다: 저쪽은 클라에서 `send_cmd` 로 같은 셋터를 부르고,
    여기서는 서버가 자기 셋터를 직접 부른다. 값을 정하는 규칙은 한 벌이다."""
    from . import _cycle_next, norm_redraw_mode, norm_resume_verify
    if key == "autoresume":
        server.set_autoresume(sess, value=None)
    elif key == "prompt_clear":
        server.set_prompt_clear(sess, None)
    elif key == "auto_token_on_exit":
        server.set_auto_token_on_exit(None)
    elif key == "claude_auto_mode":
        server.set_claude_auto_mode(None)
    elif key == "claude_auto_redraw":
        server.set_claude_auto_redraw(_cycle_next(
            "claude_auto_redraw",
            norm_redraw_mode(getattr(server, "claude_auto_redraw", "off"))))
    elif key == "claude_resume_verify":
        server.set_claude_resume_verify(_cycle_next(
            "claude_resume_verify",
            norm_resume_verify(getattr(server, "claude_resume_verify", "off"))))
    elif key == "long_turn":
        server.set_claude_turn_warn(long_sec=_cycle_next(
            "long_turn", int(getattr(server, "claude_long_turn_sec", 0) or 0)))
    elif key == "repeat_alert":
        server.set_claude_turn_warn(repeat=_cycle_next(
            "repeat_alert", int(getattr(server, "claude_repeat_alert", 0) or 0)))
    else:
        return False
    return True


# ── model — 모델 × 컨텍스트 ────────────────────────────────────────────────
def _model_choices():
    """`(인자, 라벨)` 목록. 후보의 정본은 `MODEL_CHOICES`·`CTX_CHOICES` 한 벌이다.

    ⚠ **Textual 쪽(`screens.py`)에서 읽지 않는다.** 그 목록은 오래 `ModelCtxScreen` 의
    클래스 속성이었는데, 이 함수는 **서버에서** 돌아 그걸 읽는 순간 서버 프로세스에
    Textual 이 딸려 온다(무게 규칙). 그렇다고 여기 다시 적으면 정본이 모델을 하나 더할
    때 GUI 만 옛 목록을 보인다 — 그래서 표를 UI 무의존 자리로 옮기고 둘이 그것을 읽는다."""
    from . import CTX_CHOICES, MODEL_CHOICES
    out = []
    for m in MODEL_CHOICES:
        for label, ctx in CTX_CHOICES:
            arg = m if ctx == "default" else f"{m} {ctx}"
            out.append((arg, m if ctx == "default" else f"{m} · {label}"))
    return out


def _model_spec(server, sess, selected=0):
    pane = _active_pane(sess)
    cur = (getattr(pane, "_claude_model", None) or "").lower()
    rows = []
    for arg, label in _model_choices():
        base = arg.split(" ")[0]
        now = bool(cur) and base != "default" and cur.startswith(base)
        rows.append({"key": arg, "label": label,
                     "cols": [i18n.t("pscreen.spec_model_now")] if now else []})
    return _spec("model", "list",
                 i18n.t("pscreen.spec_model_title"),
                 i18n.t("pscreen.spec_model_hint"),
                 rows=rows, keys={"enter": "apply"}, selected=selected)


def _model_apply(server, sess, arg):
    """정본 `_apply_model_config` 와 **같은 결과** — 활성 패널에 `/model <인자>` 를 친다.

    서버가 치는 이유는 mdir 의 `cd` 와 같다: 그 패널을 들고 있는 것이 서버다."""
    pane = _active_pane(sess)
    arg = (arg or "").strip()
    if pane is None or not arg:
        return False
    pane.write(("/model " + arg + "\r").encode("utf-8", "replace"))
    return True


# ── claude-perm-mode — footer 를 눌러 여는 권한모드 선택 ────────────────────
def _perm_spec(server, sess, pane_id, selected=0):
    """정본 `PermModeScreen` 과 **같은 목록**을 자료로(pytmux-2).

    목록도 bypass 노출 규칙도 `perm_modes()` 한 벌에서 온다 — 여기서 다시 고르면
    한쪽만 위험 모드를 숨기는 갈림이 생긴다. 라벨은 `pscreen.*` 키라 GUI 가 자기
    로케일로 다시 읽는다."""
    from . import perm_modes
    p = _pane(sess, pane_id)
    current = getattr(p, "_perm_mode", None) if p is not None else None
    bypass_ok = bool(getattr(p, "_bypass_seen", False)) if p is not None else False
    rows = []
    for key, label in perm_modes(current, bypass_ok):
        # ⚠ 줄의 글까지 **재료로** 싣는다(로케일 ⓑ). 목록 줄의 `label` 은 보통 자료라
        #    클라가 번역하지 않는다 — 그래야 `복사` 라는 이름의 파일이 `Copy` 로 안
        #    보인다(`PluginRow::say_cols` 의 그 갈림). 그런데 이 화면의 그 자리는
        #    **말**이다. 그래서 "이건 말이다"를 재료로 알린다.
        text, spec = i18n.phrase(label)
        rows.append({"key": key, "label": text, "i18n": {"label": spec},
                     "cols": [i18n.t("pscreen.perm_now")] if key == current else []})
    title, title_spec = i18n.phrase("pscreen.perm_title", current=current or "?")
    return _spec("claude-perm-mode", "list", title,
                 i18n.t("pscreen.perm_hint"),
                 rows=rows, keys={"enter": "apply"}, selected=selected,
                 carried={"title": title_spec})


def _perm_apply(server, sess, pane_id, target):
    """정본이 하던 것과 **같은 서버 호출** — 목표 모드를 세우면 `_scan_claude` 가
    idle 에서 shift+tab 폐루프로 거기까지 순환 주입한다(우리가 키를 세지 않는다)."""
    target = (target or "").strip()
    if not target:
        return False
    server.set_claude_perm_mode(sess, target, pane_id)
    return True


# ── claude-remote-control — footer 의 'Remote Control active' 를 눌러 여는 판 ──
def _rc_spec(server, sess, pane_id):
    """정본 `_open_remote_control` 의 InfoScreen 과 **같은 글·같은 손**(pytmux-2 잔여).

    footer 의 그 자리는 곧바로 토글이 아니다 — 원격 제어가 무엇이고 어떻게 끄는지 적은
    판을 먼저 열고, `[r]` 로 토글한다. 글은 `ccmsg.rc_*` 한 벌에서 오므로 정본과 글자까지
    같다(여기서 다시 적으면 두 클라의 설명이 갈린다).

    `pane_id` 는 스펙에 안 실린다 — `[r]` 로 돌아오는 프레임에는 패널 칸이 없어
    (`plugin_action` 의 계약이 `id`·`do`·`row`·`input` 넷이다) 여는 쪽이 `state` 에
    적어 둔다. 안 적으면 비활성 Claude 패널의 표시를 눌러 놓고 **활성 패널의 원격
    제어**를 토글한다 — 권한모드 화면이 먼저 밟은 자리 그대로다."""
    return _spec("claude-remote-control", "text",
                 i18n.t("ccmsg.rc_title"), i18n.t("pscreen.rc_hint"),
                 text=i18n.t("ccmsg.rc_body"), keys={"r": "toggle"})


# ── claude-token-log — 일별 집계 한 판 ─────────────────────────────────────
def _token_rows(server):
    """`(일자, 토큰, 5h 최대%)` 목록(최근 먼저)과 안내 한 줄.

    집계 원본은 정본 팝업이 쓰는 것과 **같은 질의**다(`usagedb`) — 트랜스크립트 권위
    (`usage_xc`, cache 포함)가 있으면 그것을, 없으면 스크랩 집계로 우아하게 내려간다.
    정본이 이미 그 순서로 고르므로 여기서 다른 순서를 고르면 두 화면의 수가 갈린다."""
    from . import usagedb
    conn = None
    getconn = getattr(server, "_tokens_db_conn", None)
    if callable(getconn):
        conn = getconn()
    if conn is None:
        return [], i18n.t("pscreen.spec_tklog_nodb")
    xc = (usagedb.xc_count(conn) if hasattr(usagedb, "xc_count") else 0)
    if xc > 0 and hasattr(usagedb, "xc_daily_breakdown"):
        recs = usagedb.xc_daily_breakdown(conn)
    else:
        recs = usagedb.daily_breakdown(conn)
    pct = usagedb.daily_limit_pct(conn) or {}
    by_day = {}
    for r in recs:
        day = r.get("day")
        if day:
            by_day[day] = by_day.get(day, 0) + int(r.get("tokens") or 0)
    rows = [(day, by_day[day], pct.get(day))
            for day in sorted(by_day, reverse=True)]
    return rows, ("" if rows else i18n.t("pscreen.spec_tklog_empty"))


def _token_log_spec(server, selected=0):
    data, note = _token_rows(server)
    rows = [{"key": day, "label": day,
             "cols": [f"{tok:,}", "" if p is None else f"{int(p)}%"]}
            for day, tok, p in data]
    total = sum(tok for _d, tok, _p in data)
    # 자리가 있는 글은 **재료까지** 싣는다(로케일 ⓑ) — 원문이 매번 달라 키가 못 된다.
    title, title_spec = i18n.phrase("pscreen.spec_tklog_title_sum", tok=f"{total:,}")
    return _spec("claude-token-log", "table", title,
                 i18n.t("pscreen.spec_tklog_hint"),
                 rows=rows, note=note, selected=selected,
                 carried={"title": title_spec})


# ── 진입점 ────────────────────────────────────────────────────────────────
def open_spec(server, sess, name, args=(), state=None):
    """명령 이름 → 첫 화면. **내 이름이 아니면 `None`**(다른 플러그인·다른 경로로).

    `args` 는 여는 쪽이 실어 보낸 것이다 — 오늘은 **패널 id** 하나뿐이고(footer 를 누른
    그 패널 · 권한모드와 원격 제어 둘이 쓴다), 나머지 화면은 활성 패널로 충분하다. 그 id 는 `state` 에
    적어 둔다: 화면 안에서 Enter 를 눌러 올 때 `plugin_action` 프레임에는 패널 칸이
    없어(계약이 `id`·`do`·`row`·`input` 넷이다), 안 적어 두면 **활성 패널의 모드를
    바꾼다** — 비활성 Claude 패널의 footer 를 눌렀을 때 딱 그 사고가 난다."""
    if name in PC_QUEUE:
        return _pc_queue_spec(server, sess)
    if name in RULES:
        return _rules_spec(server)
    if name in SETTINGS:
        return _settings_spec(server, sess)
    if name in MODEL:
        return _model_spec(server, sess)
    if name in TOKEN_LOG:
        return _token_log_spec(server)
    if name in PERM:
        pane_id = _as_pane_id(args[0] if args else None)
        if isinstance(state, dict):
            state["claude_perm_pane"] = pane_id
        return _perm_spec(server, sess, pane_id)
    if name in RC:
        pane_id = _as_pane_id(args[0] if args else None)
        if isinstance(state, dict):
            state["claude_rc_pane"] = pane_id
        return _rc_spec(server, sess, pane_id)
    return None


#: 이 모듈이 여는 화면 id 들 — `plugin_screen` 이 "내 화면인가"를 이것으로 가른다.
IDS = ("pc-queue", "claude-rules", "claude-settings", "model", "claude-token-log",
       "claude-perm-mode", "claude-remote-control")


def action(server, sess, req):
    """화면 안에서 누른 것 → 다음 스펙이거나 닫기. 내 화면이 아니면 `None`.

    ⚠ **상태가 바뀌면 알린다**: 설정·규칙은 서버 전역 값이라 다른 클라의 status 도
    같이 움직여야 한다(정본은 그 길을 `server_command` 의 `broadcast` 로 얻는다).
    안 알리면 같은 서버에 붙은 두 사람이 서로 다른 설정을 본다."""
    sid = req.get("id")
    if sid not in IDS:
        return None
    do = str(req.get("do") or "")
    row = int(req.get("row") or 0)
    picked = req.get("input")
    if do == "close":
        return _close(sid)
    if sid == "pc-queue":
        if do == "clear":
            server.pc_queue_clear(sess)
            return _pc_queue_spec(server, sess, row)
        return None
    if sid == "claude-rules":
        if do == "save":
            server.set_claude_rules(str(picked or ""))
            server._broadcast_session(sess)
            return _close(sid)
        return None
    if sid == "claude-settings":
        if do == "toggle":
            if _settings_toggle(server, sess, str(picked or "")):
                server._broadcast_session(sess)
            return _settings_spec(server, sess, row)
        return None
    if sid == "model":
        if do == "apply":
            _model_apply(server, sess, str(picked or ""))
            return _close(sid)
        return None
    if sid == "claude-perm-mode":
        if do == "apply":
            state = req.get("state")
            pane_id = (state or {}).get("claude_perm_pane") \
                if isinstance(state, dict) else None
            _perm_apply(server, sess, pane_id, str(picked or ""))
            # 닫는다 — 모드는 **바로 안 바뀐다**(서버가 idle 을 기다려 shift+tab 을
            # 순환 주입한다). 판을 열어 둔 채 다시 그리면 고른 줄에 아직 '현재' 가
            # 안 붙어 "안 먹었다"로 보인다. 정본도 고르는 즉시 닫는다.
            return _close(sid)
        return None
    if sid == "claude-remote-control":
        if do == "toggle":
            state = req.get("state")
            pane_id = (state or {}).get("claude_rc_pane") \
                if isinstance(state, dict) else None
            server.toggle_claude_remote(sess, pane_id)
            # 정본도 `[r]` 을 누르면 토글하고 **바로 닫는다**(`InfoScreen` 의
            # hide_key → hide_cb → dismiss). 판을 열어 둬 봐야 그 글은 안 바뀐다.
            return _close(sid)
        return None
    if sid == "claude-token-log":
        return None
    return None
