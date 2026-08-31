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
# 머신별 총계(pytmux-371 ③ · 정본 토큰 팝업의 `[머신]` 탭). 동기화를 켜면 계정 Σ 가
# 다른 머신 것까지 합쳐 뛰므로 **어디서 온 값인지** 볼 자리가 있어야 한다.
MACHINES = ("claude-token-machines", "token-machines")
# 한도(/usage) 판. 이름은 `__init__._USAGE_PANEL` 과 **같은 셋**이다 — 그 파일은 이제
# 이 모듈로 위임하고, 이름 표는 화면을 짓는 쪽(여기)에 둔다.
LIMITS = ("usage-panel", "usage-limits", "limits")
# Claude 경고 이력(pytmux-371 ⑤ · 정본 토큰 팝업의 `[경고]` 탭). 서버가 onset 을 JSONL 로
# 쌓아 두고(`_record_warn_history`) 그 탭이 날짜별 트리로 그린다.
WARNS = ("claude-warn-history", "claude-warns", "warn-history")
# 기간별·세션별(pytmux-371 ①② · 정본 토큰 팝업의 `[기간]`·`[세션]` 탭). 집계는
# `usagelog.agg_view` 한 벌이 한다 — 정본 화면도 그 함수를 부른다.
PERIOD = ("claude-token-period", "token-period")
SESSIONS = ("claude-token-sessions", "token-sessions")

# 기간 버킷 — 정본 팝업의 h/d/w/m 과 같은 넷이고 순서도 같다.
_BUCKETS = (("hour", "pscreen.spec_bucket_hour"),
            ("day", "pscreen.spec_bucket_day"),
            ("week", "pscreen.spec_bucket_week"),
            ("month", "pscreen.spec_bucket_month"))
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
        "pscreen.spec_model_hint": "↑↓ 이동 · Enter 적용(/model 주입) · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "pscreen.spec_model_now": "지금",
        "pscreen.spec_period_title": "토큰 사용량 · 기간별",
        "pscreen.spec_period_hint": "↑↓ 이동 · Enter/←→ 펼침·접힘 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "pscreen.spec_sessions_title": "토큰 사용량 · 세션별",
        "pscreen.spec_sessions_hint": "↑↓ 이동 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "pscreen.spec_bucket_hour": "시간 단위로 보기",
        "pscreen.spec_bucket_day": "일 단위로 보기",
        "pscreen.spec_bucket_week": "주 단위로 보기",
        "pscreen.spec_bucket_month": "월 단위로 보기",
        "pscreen.spec_goto_period": "기간별 →",
        "pscreen.spec_goto_sessions": "세션별 →",
        "pscreen.spec_goto_machines": "머신별 총계 →",
        "pscreen.spec_goto_warns": "Claude 경고 이력 →",
        "pscreen.spec_goto_daily": "일별 집계 →",
        "pscreen.spec_warns_title": "Claude 경고 이력",
        "pscreen.spec_warns_hint": "↑↓ 이동 · Enter 날짜 펼침·접힘 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "pscreen.spec_warns_empty": "쌓인 Claude 경고가 없습니다.",
        "pscreen.spec_warns_nodb": "이 서버는 경고 이력을 안 쌓습니다(claude-code 플러그인 필요).",
        "pscreen.spec_goto_model": "모델·컨텍스트 고르기 →",
        "pscreen.spec_goto_limits": "한도(/usage) 보기 →",
        "pscreen.spec_goto_settings": "시나리오 설정 →",
        "pscreen.spec_machines_title": "토큰 사용량 · 머신별 (Σ{tok})",
        "pscreen.spec_machines_hint": "↑↓ 이동 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "pscreen.spec_machines_empty": "아직 다른 머신에서 온 기록이 없습니다(동기화를 켜면 채워집니다).",
        "pscreen.spec_pcq_title": "프롬프트 단위 클리어 큐",
        "pscreen.spec_pcq_hint": "c 비우기 · Esc 닫기",
        "pscreen.spec_pcq_empty": "(큐가 비어 있습니다 — `:prompt-clear-queue <명령>` 으로 쌓습니다)",
        "pscreen.spec_tklog_title": "토큰 사용량(추정) · 일별",
        "pscreen.spec_tklog_title_sum": "토큰 사용량(추정) · 일별 · Σ{tok}",
        "pscreen.spec_tklog_hint": "↑↓ 이동 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
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
        "pscreen.spec_model_hint": "↑↓ move · Enter apply (injects /model) · Esc close · p session · l limit · o machine · s scenario · u /usage",
        "pscreen.spec_model_now": "now",
        "pscreen.spec_period_title": "Token usage · by period",
        "pscreen.spec_period_hint": "↑↓ move · Enter/←→ expand·collapse · Esc close · p session · l limit · o machine · s scenario · u /usage",
        "pscreen.spec_sessions_title": "Token usage · by session",
        "pscreen.spec_sessions_hint": "↑↓ move · Esc close · p session · l limit · o machine · s scenario · u /usage",
        "pscreen.spec_bucket_hour": "Show by hour",
        "pscreen.spec_bucket_day": "Show by day",
        "pscreen.spec_bucket_week": "Show by week",
        "pscreen.spec_bucket_month": "Show by month",
        "pscreen.spec_goto_period": "By period →",
        "pscreen.spec_goto_sessions": "By session →",
        "pscreen.spec_goto_machines": "By machine →",
        "pscreen.spec_goto_warns": "Claude warning history →",
        "pscreen.spec_goto_daily": "Daily totals →",
        "pscreen.spec_warns_title": "Claude warning history",
        "pscreen.spec_warns_hint": "↑↓ move · Enter expand/collapse a day · Esc close · p session · l limit · o machine · s scenario · u /usage",
        "pscreen.spec_warns_empty": "No Claude warnings recorded.",
        "pscreen.spec_warns_nodb": "This server keeps no warning history (needs the claude-code plugin).",
        "pscreen.spec_goto_model": "Pick model/context →",
        "pscreen.spec_goto_limits": "Show limits (/usage) →",
        "pscreen.spec_goto_settings": "Scenario settings →",
        "pscreen.spec_machines_title": "Token usage · by machine (Σ{tok})",
        "pscreen.spec_machines_hint": "↑↓ move · Esc close · p session · l limit · o machine · s scenario · u /usage",
        "pscreen.spec_machines_empty": "No records from other machines yet (turn sync on to fill this).",
        "pscreen.spec_pcq_title": "Per-prompt clear queue",
        "pscreen.spec_pcq_hint": "c clear · Esc close",
        "pscreen.spec_pcq_empty": "(the queue is empty — add with `:prompt-clear-queue <command>`)",
        "pscreen.spec_tklog_title": "Token usage (estimated) · by day",
        "pscreen.spec_tklog_title_sum": "Token usage (estimated) · by day · Σ{tok}",
        "pscreen.spec_tklog_hint": "↑↓ move · Esc close · p session · l limit · o machine · s scenario · u /usage",
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
    # ★ 정본은 모델·컨텍스트와 한도를 **한 탭**에 담는다(`[한도]` 탭). GUI 는 판을 잇는
    #   줄로 같은 곳에 닿는다 — 정본의 판 구성을 흔들지 않으면서 한 자리에서 셋에 닿는다.
    rows.extend(_hub_rows("model"))
    return _spec("model", "list",
                 i18n.t("pscreen.spec_model_title"),
                 i18n.t("pscreen.spec_model_hint"),
                 rows=rows, keys=_hub_keys("model", {"enter": "apply"}),
                 selected=selected)


def _model_apply(server, sess, arg):
    """정본 `_apply_model_config` 와 **같은 결과** — 활성 패널에 `/model <인자>` 를 친다.

    서버가 치는 이유는 mdir 의 `cd` 와 같다: 그 패널을 들고 있는 것이 서버다.

    ☠ **`pane.write` 가 아니다**(pytmux-173) — 까닭과 실측은
    `plugins/ncd/__init__.py` §`_send_to_pane` **한 곳**이 쥔다. 여기만 다른 것은
    돌려주는 값이다: 못 쳤으면 `False` 라야 부르는 쪽이 「먹었다」로 안 읽는다.
    """
    pane = _active_pane(sess)
    arg = (arg or "").strip()
    if pane is None or not arg or pane.pty is None:
        return False
    try:
        pane.pty.write(("/model " + arg + "\r").encode("utf-8", "replace"))
    except OSError:
        return False
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
    rows.extend(_hub_rows("claude-token-log"))
    return _spec("claude-token-log", "table", title,
                 i18n.t("pscreen.spec_tklog_hint"),
                 rows=rows, note=note, selected=selected,
                 keys=_hub_keys("claude-token-log"),
                 carried={"title": title_spec})


# ── machines — 원산지 머신별 총계 (pytmux-371 ③) ──────────────────────────
def _machine_rows(server):
    """`(라벨, 토큰, 비율)` 목록(큰 것 먼저)과 안내 한 줄.

    집계는 **정본 팝업이 쓰는 그 질의**다(`usagedb.xc_totals_by_host` — §7.3 의 원산지
    분해). 여기서 다시 세면 두 화면의 수가 갈린다.

    ★ 비율(0~1)까지 싣는 이유: 막대를 **그리는 것은 클라**이고 그 클라가 픽셀을 안다.
    서버가 칸 수나 `█` 를 실으면 그 순간 서버가 UI 를 알게 되고(설계 §10 위험표), 격자
    없는 GUI 는 그 글자를 다시 해석해야 한다. 비율은 자료다."""
    from . import usagedb
    conn = None
    getconn = getattr(server, "_tokens_db_conn", None)
    if callable(getconn):
        conn = getconn()
    if conn is None:
        return [], i18n.t("pscreen.spec_tklog_nodb")
    if not hasattr(usagedb, "xc_totals_by_host"):
        return [], i18n.t("pscreen.spec_tklog_nodb")
    by_host = usagedb.xc_totals_by_host(conn) or {}
    top = max(by_host.values()) if by_host else 0
    rows = []
    for host, tok in sorted(by_host.items(), key=lambda kv: -kv[1]):
        label = (i18n.t("pscreen.tklog_host_local")
                 if host == getattr(usagedb, "LOCAL_HOST", "<local>") else str(host))
        rows.append((label, int(tok), (tok / top) if top else 0.0))
    return rows, ("" if rows else i18n.t("pscreen.spec_machines_empty"))


def _machines_spec(server, selected=0):
    data, note = _machine_rows(server)
    # 막대는 **천분율 정수**로 싣는다 — 클라 쪽 구조체가 `Eq` 라(프레임 변화 판정) 부동
    # 소수를 넣으면 그 파생을 잃는다. 막대 한 줄에 1‰ 보다 고운 눈금은 뜻이 없다.
    rows = [{"key": label, "label": label, "cols": [f"{tok:,}"],
             "bar": max(0, min(1000, int(round(ratio * 1000))))}
            for label, tok, ratio in data]
    total = sum(tok for _l, tok, _r in data)
    title, title_spec = i18n.phrase("pscreen.spec_machines_title", tok=f"{total:,}")
    rows.extend(_hub_rows("claude-token-machines"))
    return _spec("claude-token-machines", "table", title,
                 i18n.t("pscreen.spec_machines_hint"),
                 rows=rows, note=note, selected=selected,
                 keys=_hub_keys("claude-token-machines"),
                 carried={"title": title_spec})


# ── limits — /usage 한도(pytmux-371 ④) ────────────────────────────────────
# 이 판은 `__init__.py` 에 있었는데 여기로 옮겼다: 모델 판과 **서로 잇기** 때문이다
# (정본은 그 둘을 한 탭에 담는다 — `[한도]` 탭의 첫 두 행이 모델·컨텍스트다). 잇는 줄이
# 두 모듈에 걸치면 한쪽만 고쳐지는 날이 온다.
def _reset_epoch(reset):
    """`/usage` 리셋 표기 → epoch **초(정수)**. 못 읽으면 `0`.

    ⛔ 여기서 다시 파싱하지 않는다 — 표기 갈래(월·일 있는 것 · 시각만 있는 것 · 연도
    롤오버)와 그 함정은 `claude.parse_reset_ts` 한 벌이 이미 쥐고 있고, 정본 팝업의
    카운트다운도 그것을 부른다. 두 벌이면 두 화면이 다른 시각을 센다.

    ⚠ **과거를 그대로 돌려주는 것도 그 함수의 계약**이다(낡은 실측을 호출부가 판단하라는
    뜻) — 여기서는 그것을 그대로 싣고, 「지났다」의 판정은 그리는 쪽이 한다.
    """
    if not reset:
        return 0
    try:
        from .claude import parse_reset_ts
        ts = parse_reset_ts(reset)
    except Exception:
        return 0
    return int(ts) if ts else 0


def _limits_spec(server, selected=0):
    """한도 판을 **자료로** 준다 — 막대는 비율(천분율)이고 글자가 아니다.

    # 왜 글자 막대를 안 싣나

    종전에는 `usage_bar_lines(..., 76)` 로 **글자 막대**(`█`·`░`)를 76칸에 그려 `text` 한
    덩이로 보냈다. 격자에 사는 정본에는 그것이 옳지만, 그 줄을 받는 클라는 «텍스트 기반
    인터페이스»를 그리게 된다 — 사용자 지시(*"인터페이스는 gui 기반"*)와 어긋나고 76칸은
    그 창과 무관한 남의 숫자다. 값의 주인은 `pytmuxlib.usagebar.usage_values` 한 벌이고
    정본의 글자 판도 그것을 쓴다(두 화면의 수가 갈리지 않는다).
    """
    from pytmuxlib.usagebar import usage_values
    import time as _t
    uts = getattr(server, "_usage_ts", None)
    vals = usage_values(
        getattr(server, "_usage", None),
        age_sec=(max(0, int(_t.time() - uts)) if uts is not None else None))
    rows = []
    for r in (vals or {}).get("rows", ()):
        pct = max(0, min(100, int(r["pct"])))
        cols = [f"{pct}% {i18n.t('usage.used')}"]
        if r["reset"]:
            cols.append("↻ " + r["reset"])
        row = {"key": r["label"], "label": r["label"], "cols": cols,
               "bar": pct * 10}
        # ★ **리셋 시각을 자료로 싣는다**(pytmux-371 ④). 정본은 이 자리를 큰 글자
        #   카운트다운으로 센다 — 그 글자를 서버가 지어 보내면 초마다 프레임이 와야 하고,
        #   그건 판 하나 때문에 초당 한 번 전 세션을 다시 그리는 값이다. 언제인지만 싣고
        #   남은 시간은 클라가 **제 타이머로** 굴린다.
        # ⚠ 못 읽는 표기(빈 값·낯선 서식)면 칸을 아예 안 만든다 — `0` 을 실으면 클라가
        #   그것을 「지금 리셋」으로 그린다.
        until = _reset_epoch(r["reset"])
        if until:
            row["until"] = until
        rows.append(row)
    # 계정·신선도는 **막대가 없는 줄**이다 — 값이지만 비율이 아니다.
    for extra in ((vals or {}).get("account"), (vals or {}).get("ago")):
        if extra:
            rows.append({"key": extra, "label": extra, "cols": []})
    # ★ 정본은 모델·컨텍스트를 **같은 탭**에 담는다. GUI 는 판을 잇는 줄로 같은 곳에 닿는다
    #   (사용자 결정 ⓒ — 정본의 판 구성을 흔들지 않고 한 자리에서 셋에 닿는다).
    # ⚠ 사유(`note`)는 **자료 줄**이 있나로 정한다 — 아래 허브 줄은 늘 붙으므로 `rows` 로
    #   재면 «값이 없는데 사유도 없는» 판이 된다(빈 판과 실패를 못 가르는 그 자리다).
    has_data = bool(rows)
    rows.extend(_hub_rows("claude-usage-panel"))
    return _spec("claude-usage-panel", "table",
                 i18n.t("ccmsg.usage_title"), i18n.t("cusage.hint"),
                 rows=rows, selected=selected,
                 note=("" if has_data else i18n.t("ccmsg.usage_no_data")),
                 keys=_hub_keys("claude-usage-panel", {"enter": "apply"}))


# 판을 잇는 줄의 열쇠 — 라벨이 아니라 이 값으로 판정한다(라벨은 번역을 탄다).
_GOTO_MODEL = "goto:model"
_GOTO_LIMITS = "goto:limits"
_GOTO_MACHINES = "goto:machines"
_GOTO_WARNS = "goto:warns"
_GOTO_DAILY = "goto:daily"
_GOTO_PERIOD = "goto:period"
_GOTO_SESSIONS = "goto:sessions"

# 정본은 이 판들을 **한 팝업의 탭 띠**로 묶는다(기간·세션·머신·한도·경고). GUI 는 판이
# 여러 개이므로 같은 뜻을 **판을 잇는 줄**로 낸다 — 어느 판에서든 나머지로 한 번에 간다.
#
# ⛔ 표를 한 곳에 두는 이유: 판마다 손으로 적으면 새 판이 생길 때 **어떤 판에서는 안 보인다**.
#    그 조용한 갈림이 이 저장소가 반복해 물린 부류다(pytmux-35 의 죽은 줄).
_HUB = (
    (_GOTO_LIMITS, "pscreen.spec_goto_limits", "claude-usage-panel"),
    (_GOTO_MODEL, "pscreen.spec_goto_model", "model"),
    (_GOTO_MACHINES, "pscreen.spec_goto_machines", "claude-token-machines"),
    (_GOTO_WARNS, "pscreen.spec_goto_warns", "claude-warn-history"),
    (_GOTO_DAILY, "pscreen.spec_goto_daily", "claude-token-log"),
    (_GOTO_PERIOD, "pscreen.spec_goto_period", "claude-token-period"),
    (_GOTO_SESSIONS, "pscreen.spec_goto_sessions", "claude-token-sessions"),
)

_GOTO_SETTINGS = "goto:settings"

# 정본의 탭 띠 **끝에는 뷰가 아닌 것 둘**이 초록 배지로 붙어 있다(`/usage`·`시나리오`) —
# 그 둘은 탭이 아니라 **액션**이라 색이 다르다(pytmux-371 본문의 "일곱 번째·여덟 번째").
# 하나(`/usage`)는 GUI 에서 이미 제 판이 됐으므로(`claude-usage-panel` = `_HUB` 의 첫 줄)
# 여기 남는 것은 `시나리오`(⑥ 자동재개 설정) 하나다.
#
# ⛔ **`_HUB` 에 넣지 않는다 — 한 방향이다.** `_HUB` 는 «서로» 오가는 판들의 표이고
#    전수 오라클이 그 대칭을 강제한다. 그런데 정본에서 ⑥ 은 탭이 아니라 경고 탭 **위에
#    겹쳐 뜨는 판**이고, 그 꼬리줄이 광고하는 조작은 `Enter toggle/cycle · ESC close`
#    뿐이다 — 탭 전환이 없다. 대칭으로 만들면 정본에 없는 이동을 GUI 가 갖게 되고,
#    그것은 [[pytmux-185]] 가 결함으로 세는 갈림이다(«있다» 가 아니라 «같게 군다»).
_HUB_ACTIONS = (
    (_GOTO_SETTINGS, "pscreen.spec_goto_settings", "claude-settings"),
)


# ── 정본 토큰 팝업의 **글자 키** (pytmux-371 · pytmux-185 상호작용 계약) ──────────
#
# ★ 줄이 있다고 끝이 아니다. 위 `_HUB` 는 *«있다»* 를 채웠고 — 어느 판에서든 나머지로
#   갈 줄이 보인다 — 이 표는 *«같게 군다»* 를 채운다. 정본
#   `screens.TokenLogScreen.on_key` 가 물고 있는 글자를 **같은 뜻으로** 문다.
#   ⛔ 그 둘은 다른 질문이고, 갈림이 결함이라는 것이 루트 CLAUDE.md ★★ 의 규율이다.
#
# ⚠ **정본이 «토글»이라는 점까지 옮긴다**: 세션 탭에서 다시 `p` 를 누르면 기간으로
#   돌아온다(`self._view = "session" if was or self._view != "session" else "time"`).
#   그래서 지금 판이 목적지면 기간 판을 준다 — 안 그러면 그 키가 **아무 일도 안 하는**
#   키가 되고, 정본을 손에 익힌 사람에게는 "GUI 에서는 안 먹는다"가 된다.
_KEY_TABS = {
    "p": "claude-token-sessions",   # 정본 `k == "p"` — 세션 뷰 토글
    "l": "claude-usage-panel",      # 정본 `k == "l"` — 한도 상세 토글
    "o": "claude-token-machines",   # 정본 `k == "o"` — 머신 뷰 토글
    "s": "claude-settings",         # 정본 `k == "s"` — 시나리오(자동재개 설정) 판
}

#: 정본이 `event.stop()` 만 하고 **아무 일도 안 하는** 글자들(옛 기능의 잔재).
#:
#: ⚠ **여기에 싣지 않는다.** 정본이 이것을 예약한 까닭은 *"흔한 글자라 팝업이 닫히지 않게
#: 소비만 하고 무동작으로 둔다(머슬메모리 오타로 안 닫히게)"* 인데, 그 위험이 GUI 에는
#: 없다 — 이 클라는 **스펙 표에 없는 글자에 이미 아무 일도 안 한다**(pytmux-181·273 에서
#: `press_list` 의 `_ => close_top()` 을 걷어냈다. 재는 자리 =
#: `gui/src/session_view_tests.rs::a_letter_the_spec_does_not_declare_is_ignored_not_a_close`).
#: 실으면 **아무 일도 안 하는 왕복**만 서버에 한 번 더 갈 뿐이다.
#: ⛔ 그러니 이 표는 «싣는 목록»이 아니라 «왜 안 싣는지»의 기록이다 — 지우면 다음 사람이
#:    같은 길을 한 번 더 간다(2026-08-25 에 실제로 실었다가 되돌렸다).
_KEY_RESERVED = ("h", "d", "w", "m", "r")

#: 정본 `k == "u"` — 그림자 `/usage` 갱신 요청(결과는 status 로 온다).
_KEY_USAGE = "u"
_DO_USAGE = "refresh-usage"

#: `_hub_keys()` 가 낼 수 있는 `do` **전수**.
#:
#: ⛔ 왜 표를 또 두나: 죽은 `do` 를 잡는 자(`tests/test_plugin_do_wiring.py`)는 이 파일을
#:    **부르지 않고 읽는다**(그 모듈 머리말 §"왜 부르지 않고 읽나" — 스펙을 내는 자리의
#:    절반이 비동기이고, `do` 를 실제로 던져 보는 판정은 `delete`·`apply` 를 진짜로
#:    던진다). 그래서 함수가 만드는 표는 그 자에게 안 보이고, 안 보이면 그 자는
#:    「못 읽었다」로 운다 — 조용히 통과하지 않는 것이 이 게이트의 값이다.
#: ★ 드리프트는 `tests/test_plugin_screen.py` 의 전수 시험이 막는다(실제로 만든 표의
#:   값이 이 전수 안에 있는지, 그리고 이 전수가 남는 것 없이 쓰이는지 **양쪽으로** 잰다).
#: ⚠ **글자로 적는다**(위 `_GOTO_*` 를 참조하지 않는다): 읽는 쪽은 `ast.literal_eval`
#:    로 이 줄을 풀기 때문에 이름이 섞이면 못 푼다. 그래서 두 벌이 되는데, 갈리지
#:    않게 `tests/test_plugin_screen.py` 가 두 표를 맞대 본다.
_HUB_KEY_DOS = ("goto:sessions", "goto:limits", "goto:machines", "goto:settings",
                "goto:period", "refresh-usage")


def _hub_keys(current_sid, extra=None):
    """이 판이 무는 **글자 키** 표(`_spec(keys=…)`).

    스펙의 `keys` 는 「키 → 플러그인 액션」이고 클라는 **표에 있는 글자만** 먹는다
    (`PluginScreen::key_action`). 그래서 여기 적는 순간 그 글자는 ⑴ 판을 안 닫고
    ⑵ 그 뜻대로 움직인다 — 정본과 같아진다.

    `do` 값을 `_HUB` 의 줄 열쇠(`goto:*`)와 **같은 문자열**로 두는 것이 요령이다:
    `action()` 이 이미 그 문자열로 판을 여는 표(`_hub_open`)를 갖고 있어, 갈래를
    새로 적을 자리가 없다(적으면 줄과 키가 따로 낡는다)."""
    keys = dict(extra or {})
    for letter, sid in _KEY_TABS.items():
        # 지금 판이 목적지면 정본처럼 **기간으로 되돌아온다**(위 ⚠).
        target = _GOTO_PERIOD if sid == current_sid else _goto_of(sid)
        if target is not None:
            keys[letter] = target
    keys[_KEY_USAGE] = _DO_USAGE
    # ⛔ 여기서 낸 것이 `_HUB_KEY_DOS` 밖이면 **죽은 키**다(정적 조사기가 그 전수를 읽어
    #    「정본이 받나」를 재므로, 전수 밖 이름은 아무도 안 재게 된다). 그 어긋남은
    #    **시험이 양방향으로** 잡는다 — `tests/test_plugin_screen.py` 의
    #    `test_the_roster_of_letter_key_actions_matches_what_the_panels_actually_emit`.
    #    ⚠ 여기 `assert` 를 두지 않는다: 어긋나면 그때는 **판이 안 뜬다**(팝업 하나를
    #    죽이는 값을 개발용 검사에 치르는 셈이고, `-O` 에서는 그나마도 사라진다).
    return keys


def _refresh_usage(server):
    """정본 `k == "u"` 와 같은 일 — 그림자 `/usage` 를 다시 묻는다.

    ⛔ 여기서 값을 만들지 않는다. 묻는 방법도 결과를 어디에 싣는지도 서버가 이미 알고
    (`servermixin.refresh_usage`), 그 함수 하나가 게이트(과잉 질의 방지)까지 든다 —
    다시 적으면 그 게이트 밖으로 새는 두 번째 길이 생긴다.

    ⚠ 이 서버에 claude-code 가 없으면 조용히 아무 일도 안 한다(플러그인을 지우면 기능이
    사라지는 것이 이 저장소의 규약이고, 그때 이 키는 «무동작»으로 남는 것이 맞다)."""
    fn = getattr(server, "refresh_usage", None)
    if not callable(fn):
        return
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return          # 루프 밖(단위 시험) — 띄울 자리가 없다
    asyncio.create_task(fn())


def _goto_of(sid):
    """화면 id → 그 판을 여는 열쇠(`goto:*`). 표에 없으면 `None`."""
    for key, _label, target in _HUB + _HUB_ACTIONS:
        if target == sid:
            return key
    return None


def _hub_rows(current_sid):
    """지금 판을 뺀 **나머지로 가는 줄들** + 띠 끝의 액션 줄들. 자기 자신으로 가는 줄은
    안 만든다(누르면 아무 일도 안 나는 줄은 거짓말이다)."""
    # 칸을 다 채우는 이유: 트리 판(경고 이력)과 목록 판이 같은 줄 모양을 받아야 소비자가
    # `depth`/`expand` 를 조건부로 읽지 않는다(빠진 칸은 "안 온 것"과 "빈 것"을 못 가른다).
    #
    # ★ **글까지 재료로 싣는다**(로케일 ⓑ · `_perm_spec` 과 같은 처방). 목록 줄의 `label`
    #   은 보통 **자료**라 클라가 번역하지 않는데(`PluginRow::say_label` — 「복사」라는
    #   이름의 파일이 `Copy` 로 보이면 안 된다), 이 줄들은 자료가 아니라 **우리가 적은
    #   말**이다. 재료를 안 실으면 서버 로케일로 굳는다 — 실측(2026-08-26 · Windows
    #   프레임): 영어로 뜬 판인데 잇는 줄만 «한도(/usage) 보기 →» 로 한국어였다.
    rows = []
    for key, label_key, sid in _HUB + _HUB_ACTIONS:
        if sid == current_sid:
            continue
        text, spec = i18n.phrase(label_key)
        rows.append({"key": key, "label": text, "i18n": {"label": spec},
                     "cols": [], "depth": 0, "expand": ""})
    return rows


#: 접힘·펼침을 드는 두 자리 — 기간 트리와 경고 이력(pytmux-419 ④).
_FOLD_KEYS = ("tree_open", "warn_open")


def _folds(state, key):
    """`state` 에 적힌 «뒤집은 집합»을 꺼낸다. 없거나 상태가 없으면 빈 집합."""
    if not isinstance(state, dict):
        return set()
    return set(state.get(key) or ())


def _folds_or_none(state, key):
    """적힌 것이 **없을 때**와 «다 접었다»를 가른다.

    경고 판의 집합은 대칭차가 아니라 그냥 «펴진 날짜»라, 빈 집합은 «다 접었다»라는
    뜻이 있다. 없는 것을 빈 것으로 접으면 다 접어 둔 판이 다음 왕복에 최신 날짜만
    펴진 기본 모양으로 되살아난다.
    """
    if not isinstance(state, dict) or state.get(key) is None:
        return None
    return set(state.get(key) or ())


def _remember_folds(state, key, opened):
    """계산한 집합을 **서버가 든 그 dict 에 되쓴다**(pytmux-419 ④).

    ⛔ 이 되쓰기가 없으면 다음 누름이 다시 **빈 집합에서 출발**한다 — 방금 누른 노드
    하나만 뒤집힌 채로 다시 그려져서, 두 노드를 동시에 펼 수도 접을 수도 없다. 종전에
    이 자리 주석이 *"펼침 상태는 클라가 든다"* 라고 적고 있었는데, 그 말이 가리키던
    「클라」는 실은 **서버가 연결마다 드는 보관함**(`ClientConn.plugin_state`)이다 —
    `plugin_action` 프레임의 계약은 `id`·`do`·`row`·`input` 넷뿐이라 클라는 이 집합을
    실어 보낼 칸이 애초에 없다(`proto::Command::PluginAction`). 뜻은 그대로다(연결마다
    따로라 클라 둘이 서로의 펼침을 안 흔든다) — 다만 **적어 두는 손이 우리 쪽**이다.
    """
    if isinstance(state, dict):
        state[key] = sorted(opened)
    return opened


def _reset_folds(state):
    """새로 연 판은 **기본 접힘으로 시작한다** — 정본과 같다.

    정본은 이 다섯이 한 팝업의 탭이라 접힘을 화면 인스턴스가 들고(`_tree_toggled`·
    `_warn_open`), 팝업을 **다시 열면 새 인스턴스**라 기본값으로 돌아간다. 보관함은
    연결 수명이라 안 지우면 어제 편 노드가 오늘 판에 남는다.
    """
    if isinstance(state, dict):
        for key in _FOLD_KEYS:
            state.pop(key, None)


def _hub_open(server, sess, picked, state=None):
    """잇는 줄을 눌렀을 때 열 판. 내 줄이 아니면 `None`.

    ★ 접힘은 **탭을 옮겨도 산다** — 정본에서 이 판들은 한 팝업의 탭이라 트리를 펴 놓고
    [세션] 에 들렀다 [기간] 으로 돌아오면 편 채로 있다(화면 인스턴스가 그대로다).
    그래서 여는 판에 보관함의 집합을 그대로 건넨다(pytmux-419 ④).
    """
    for key, _label, sid in _HUB + _HUB_ACTIONS:
        if picked != key:
            continue
        if sid == "claude-settings":
            return _settings_spec(server, sess)
        if sid == "claude-usage-panel":
            return _limits_spec(server)
        if sid == "model":
            return _model_spec(server, sess)
        if sid == "claude-token-machines":
            return _machines_spec(server)
        if sid == "claude-warn-history":
            return _warn_spec(server, open_days=_folds_or_none(state, "warn_open"))
        if sid == "claude-token-log":
            return _token_log_spec(server)
        if sid == "claude-token-period":
            return _period_spec(server, opened=_folds(state, "tree_open"))
        if sid == "claude-token-sessions":
            return _sessions_spec(server)
    return None


# ── warns — Claude 경고 이력 (pytmux-371 ⑤) ───────────────────────────────
def _warn_rows(server):
    """`(날짜, [경고 …])` 목록(최신 날짜 먼저)과 안내 한 줄.

    이력의 주인은 서버다(`_read_warn_history` — onset 을 JSONL 로 쌓는다). 여기서 파일을
    다시 읽지 않는 이유는 그 함수가 정렬·상한·깨진 줄 건너뛰기를 이미 정해 두었기
    때문이다 — 두 벌이 되면 두 화면이 다른 이력을 보인다.
    """
    reader = getattr(server, "_read_warn_history", None)
    if not callable(reader):
        return [], i18n.t("pscreen.spec_warns_nodb")
    import time as _t
    recs = reader() or []
    by_day = {}
    for r in recs:
        ts = float(r.get("ts") or 0)
        day = _t.strftime("%Y-%m-%d", _t.localtime(ts)) if ts else "?"
        by_day.setdefault(day, []).append(r)
    days = sorted(by_day, reverse=True)
    return [(d, by_day[d]) for d in days], ("" if days else i18n.t("pscreen.spec_warns_empty"))


def _warn_spec(server, selected=0, open_days=None):
    """날짜별로 **접었다 펴는** 트리. 자리는 `depth`·`expand` 로 싣는다(스펙 계약).

    ★ 첫 날(최신)은 펴 둔다 — 판을 열자마자 «방금 무슨 경고가 있었나»가 보여야 하고,
    전부 접혀 있으면 한 번 더 눌러야 그것을 안다.
    """
    import time as _t
    data, note = _warn_rows(server)
    opened = set(open_days if open_days is not None else ([data[0][0]] if data else []))
    rows = []
    for day, recs in data:
        rows.append({"key": day, "label": day, "depth": 0,
                     "expand": "open" if day in opened else "shut",
                     # ⚠ 칸은 클라가 `t()` 로 다시 읽는다(`PluginRow::say_cols`) — 그래서
                     #   **서버가 포맷한 글**(`"{n}건"` → `"3건"`)은 영어 UI 에서 한국어로
                     #   뜬다(게이트 `the_number_of_strings_we_cannot_translate…` 가 그 수를
                     #   센다). 숫자는 로케일 중립이라 그대로 싣고, 뜻은 그 줄이 날짜라는
                     #   자리로 이미 말한다.
                     "cols": [str(len(recs))]})
        if day not in opened:
            continue
        for r in recs:
            ts = float(r.get("ts") or 0)
            when = _t.strftime("%H:%M", _t.localtime(ts)) if ts else "--:--"
            badge = str(r.get("badge") or r.get("kind") or "?")
            n = r.get("n")
            cols = [] if not n else [f"×{n}"]
            rows.append({"key": f"{day}/{ts}", "label": f"{when}  {badge}",
                         "depth": 1, "expand": "", "cols": cols})
    rows.extend(_hub_rows("claude-warn-history"))
    return _spec("claude-warn-history", "list",
                 i18n.t("pscreen.spec_warns_title"),
                 i18n.t("pscreen.spec_warns_hint"),
                 rows=rows, note=note, selected=selected,
                 keys=_hub_keys("claude-warn-history", {"enter": "toggle"}))


# ── period·sessions — 기간별·세션별 (pytmux-371 ①②) ──────────────────────
def _usage_records(server, limit=4000):
    """토큰 기록 목록 — 정본 팝업이 받는 것과 **같은 질의**다.

    트랜스크립트 권위(`usage_xc`)가 있으면 그것을, 없으면 스크랩 집계로 우아하게 내려간다
    (정본 `server_command`(token_log)의 그 차례 그대로). 여기서 다른 것을 고르면 두 화면의
    수가 갈린다.
    """
    from . import usagedb
    getconn = getattr(server, "_tokens_db_conn", None)
    conn = getconn() if callable(getconn) else None
    if conn is None:
        return None
    if hasattr(usagedb, "xc_count") and usagedb.xc_count(conn) > 0             and hasattr(usagedb, "xc_query_records"):
        return usagedb.xc_query_records(conn, limit=limit)
    return usagedb.query_records(conn, limit=limit)


def _agg_rows(server, bucket="day", dim="account", order="time"):
    """`agg_view` 의 한 축을 `(라벨, 토큰, 비중%)` 목록으로. 없으면 `(None, 사유)`."""
    recs = _usage_records(server)
    if recs is None:
        return None, i18n.t("pscreen.spec_tklog_nodb")
    if not recs:
        return [], i18n.t("pscreen.spec_tklog_empty")
    from . import usagelog
    weekdays = i18n.t("pscreen.weekdays").split(",")
    view = usagelog.agg_view(recs, bucket, None, dim, order,
                             weekdays=weekdays,
                             hour_suffix=i18n.t("pscreen.hour_suffix"))
    key = "groups" if dim == "session" else "buckets"
    return view.get(key) or [], ""


def _bar_rows(items):
    """`(라벨, 토큰, 비중%)` → 스펙 줄. 막대는 **그 목록의 최대값 기준**이다.

    비중%(전체 대비)를 막대로 쓰지 않는 이유: 항목이 스물이면 전부 5% 근처라 막대가
    통째로 납작해져 «어느 것이 큰가»를 못 읽는다. 정본도 막대 기준을 최대값(`bmax`)으로
    잡는다. 비중은 칸에 숫자로 함께 적는다(두 값이 다른 것을 말한다).
    """
    top = max((tok for _l, tok, _p in items), default=0)
    out = []
    for label, tok, share in items:
        cols = [f"{int(tok):,}"]
        if share:
            cols.append(f"{int(share)}%")
        out.append({"key": str(label), "label": str(label), "cols": cols,
                    "depth": 0, "expand": "",
                    "bar": (max(0, min(1000, int(round(tok / top * 1000))))
                            if top else 0)})
    return out


#: 기간 판의 기본 모양 — 정본의 첫 화면과 같은 **계층 트리**다.
#:
#: 정본 토큰 팝업의 `[기간]` 탭은 열자마자 월→주→일→시각 트리를 보인다(`_view == "time"`).
#: 종전 GUI 판은 평면 막대 + 버킷 고르개였는데, 그것은 정본의 **옛** 서브탭(h/d/w/m)에
#: 가까웠고 지금 정본에는 그 손이 남아 있지 않다(그 글자들은 `event.stop()` 만 한다).
_PERIOD_TREE = "tree"


def _limit_pcts(server):
    """시각별 **5h%·1w%** 두 표(`{시각키: 비율}`). 없으면 빈 표 둘.

    정본 `[기간]` 탭은 토큰 옆에 이 둘을 별도 칸으로 세운다 — 스크랩 Σ(토큰 칸)는 5h
    소비를 과소반영하므로 «그 시각에 창이 얼마나 찼나»의 진짜 신호는 이쪽이다(권위는
    `/usage` 스냅샷). 조인키는 트리 노드의 `bk` 다.

    ⛔ 여기서 다시 집계하지 않는다 — `usagedb` 의 두 함수가 정본 화면이 부르는 그것이다.
    """
    from . import usagedb
    getconn = getattr(server, "_tokens_db_conn", None)
    conn = getconn() if callable(getconn) else None
    if conn is None:
        return {}, {}
    try:
        return (usagedb.hourly_limit_pct(conn) or {},
                usagedb.hourly_week_pct(conn) or {})
    except Exception:
        # 옛 판 DB(limits 표 없음) — 칸 둘이 빠질 뿐 트리는 그대로 선다.
        return {}, {}


def _limit_cols(node, pct5h, pct1w):
    """한 노드의 `5h%`·`1w%` 칸. **시각 행에만** 붙는다(정본과 같은 자리).

    정본은 `show5h = (self._bucket == "hour" …)` 로 시각 뷰에서만 이 열을 켠다 — 5h 창은
    시간 단위 개념이라 일·주·월 행에 붙이면 「그 날의 5h%」라는 없는 뜻이 생긴다.
    여기서는 버킷 대신 **노드 종류**로 같은 판정을 한다(트리는 한 판에 다 있다).

    ⚠ 두 칸은 **함께 움직인다** — 하나만 있으면 뒤 칸이 앞으로 당겨져 5h% 자리에 1w%
    값이 서고, 그러면 숫자가 조용히 거짓말을 한다. 그래서 둘 다 없으면 아무것도 안 붙이고
    하나만 있으면 나머지는 빈 칸으로 자리를 지킨다.
    """
    if node.get("kind") != "hour":
        return []
    bk = node.get("bk")
    if not bk:
        return []
    a, b = pct5h.get(bk), pct1w.get(bk)
    if a is None and b is None:
        return []
    return ["" if a is None else f"{int(a)}%",
            "" if b is None else f"{int(b)}%"]


def _tree_rows(server, opened=()):
    """정본과 **같은 산수**로 만든 계층 트리 줄들(pytmux-371 ①).

    ⛔ 여기서 트리를 다시 짜지 않는다 — `usagetree.build` 한 벌을 부른다. 구역을 가르는
    규칙과 「각 날짜가 정확히 한 구역에만 든다」는 가산성이 그 트리의 뜻 자체라, 두 벌로
    적으면 같은 기간이 두 화면에서 다른 합을 보인다(그 모듈 머리말).

    ★ 막대는 **잎(일·시각)만** 그린다 — 월·주 행은 하위의 총합이라 늘 최장이 되어 기준을
    지배하고, 그러면 아래 행들의 막대가 무의미하게 짧아진다(정본 `_refresh_tree` 가 같은
    이유로 월·연 합계에 막대를 안 그린다).
    """
    recs = _usage_records(server)
    if recs is None:
        return None, i18n.t("pscreen.spec_tklog_nodb")
    if not recs:
        return [], i18n.t("pscreen.spec_tklog_empty")
    from . import usagetree
    hourly = getattr(server, "_hourly_index", None)
    nodes, _total = usagetree.build(recs, None, hourly, opened)
    pct5h, pct1w = _limit_pcts(server)
    leaf = [n["tokens"] for n in nodes if n["kind"] in ("day", "hour")]
    top = max(leaf, default=0)
    rows = []
    for n in nodes:
        if n["kind"] == "divider":
            # 접힘 안내(`— 지난 주 …`)는 **누를 것이 없는 줄**이다 — 키를 주면 눌리는
            # 것처럼 보이고, 눌러도 아무 일이 안 나는 줄은 거짓말이다.
            rows.append({"key": "", "label": n["label"], "cols": [],
                         "depth": n["level"], "expand": ""})
            continue
        tok = int(n["tokens"] or 0)
        rows.append({
            "key": n["key"] or "",
            "label": n["label"],
            "cols": [f"{tok:,}"] + _limit_cols(n, pct5h, pct1w),
            "depth": n["level"],
            # 접힘과 **잎**은 다르다 — 안 열리는 화살표를 붙이면 그 화살표가 거짓말이다.
            "expand": ("open" if n["expanded"] else "shut") if n["expandable"] else "",
            "bar": (max(0, min(1000, int(round(tok / top * 1000))))
                    if top and n["kind"] in ("day", "hour") else 0),
        })
    return rows, ""


def _period_spec(server, selected=0, opened=None):
    """기간 판 — 정본과 같은 **계층 트리**(pytmux-371 ①).

    펼침 상태(`opened`)는 **클라가 든다** — 경고 판(`_warn_spec`)과 같은 처방이다.
    서버가 들면 같은 서버를 보는 클라 둘이 서로의 펼침을 흔든다.
    """
    rows, note = _tree_rows(server, opened or ())
    rows = list(rows or [])
    rows.extend(_hub_rows("claude-token-period"))
    return _spec("claude-token-period", "table",
                 i18n.t("pscreen.spec_period_title"),
                 i18n.t("pscreen.spec_period_hint"),
                 rows=rows, note=note, selected=selected,
                 keys=_hub_keys("claude-token-period",
                                {"enter": "toggle", "right": "expand",
                                 "left": "collapse"}))


def _sessions_spec(server, selected=0):
    items, note = _agg_rows(server, bucket="day", dim="session", order="tokens")
    rows = _bar_rows(items or [])
    rows.extend(_hub_rows("claude-token-sessions"))
    return _spec("claude-token-sessions", "table",
                 i18n.t("pscreen.spec_sessions_title"),
                 i18n.t("pscreen.spec_sessions_hint"),
                 rows=rows, note=note, selected=selected,
                 keys=_hub_keys("claude-token-sessions", {"enter": "apply"}))


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
    if name in MACHINES:
        return _machines_spec(server)
    if name in LIMITS:
        return _limits_spec(server)
    if name in WARNS:
        _reset_folds(state)
        return _warn_spec(server)
    if name in PERIOD:
        _reset_folds(state)
        return _period_spec(server)
    if name in SESSIONS:
        return _sessions_spec(server)
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
       "claude-token-machines", "claude-usage-panel", "claude-warn-history",
       "claude-token-period", "claude-token-sessions",
       "claude-perm-mode",
       "claude-remote-control")


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
    # ★ **잇는 줄은 어느 판에서든 먼저 본다**(정본의 탭 띠에 해당). 판마다 갈래를 적으면
    #   새 판이 생길 때 어떤 판에서는 안 먹는다 — 표 한 벌(`_HUB`)이 그것을 막는다.
    if sid in ("model", "claude-usage-panel", "claude-token-machines",
               "claude-warn-history", "claude-token-log",
               "claude-token-period", "claude-token-sessions"):
        # ⑴ **글자 키**(`_hub_keys`) — `do` 가 곧 목적지 열쇠다. 줄보다 먼저 본다:
        #    글자를 누른 순간 클라는 «고른 줄의 열쇠»를 `input` 으로 함께 싣는데,
        #    그것을 먼저 보면 커서가 마침 잇는 줄 위에 있을 때 **엉뚱한 판**이 열린다.
        #
        #    ⚠ `_HUB_KEY_DOS` 로 **먼저 거른다**. 두 가지를 얻는다: ⓐ 아무 `do` 나
        #    목적지 열쇠로 넘겨보지 않고 ⓑ 죽은 `do` 를 잡는 자
        #    (`tests/test_plugin_do_wiring.py`)가 «이 이름들을 여기서 받는다»를
        #    **읽어서** 알 수 있다 — 그 자는 부르지 않고 읽으므로, 여기서 이름이
        #    글자로 안 보이면 이 판의 키가 통째로 그 게이트 밖으로 나간다.
        if do in _HUB_KEY_DOS:
            # 정본 `k == "u"` — 그림자 `/usage` 갱신. 판은 그대로 두고 결과는 status 로
            # 온다(정본도 제목만 "조회 중…" 으로 바꾸고 표를 안 닫는다).
            if do == _DO_USAGE:
                _refresh_usage(server)
                return None
            jumped = _hub_open(server, sess, do)
            if jumped is not None:
                return jumped
        # ⑵ 줄을 눌러 잇는 길(마우스·Enter). GUI 의 문법은 «누를 자리가 보이는 것»이라
        #    글자 키와 **둘 다** 둔다 — 정본도 탭 버튼과 글자 키를 함께 갖는다.
        jumped = _hub_open(server, sess, str(picked or ""), req.get("state"))
        if jumped is not None:
            return jumped
    if sid == "model":
        if do == "apply":
            _model_apply(server, sess, str(picked or ""))
            return _close(sid)
        return None
    if sid == "claude-warn-history":
        if do == "toggle":
            # 어느 날짜가 펴져 있나는 **클라가 든다**(스펙의 `expand`) — 서버가 그 상태를
            # 들면 클라마다 다른 판을 봐야 할 때 갈린다. 클라가 지금 펴진 날짜 목록을
            # 실어 보내고 우리는 그 목록으로 다시 짓는다.
            state = req.get("state")
            opened = _folds_or_none(state, "warn_open")
            if opened is None:
                # 판이 처음 선 모양(최신 날짜만 펴짐)이 곧 출발점이다 — 그것을 안 담고
                # 빈 집합에서 출발하면 첫 누름이 «최신 날짜를 접는다» 가 아니라 «두
                # 번째 날짜만 편다» 가 되어 판이 눈앞에서 튄다.
                data, _n = _warn_rows(server)
                opened = {data[0][0]} if data else set()
            picked_day = str(picked or "").split("/")[0]
            if picked_day:
                opened.symmetric_difference_update({picked_day})
            _remember_folds(state, "warn_open", opened)
            return _warn_spec(server, row, open_days=opened)
        return None
    if sid == "claude-token-period":
        # 정본 트리의 손 그대로다 — `Enter`/`space` 토글 · `→` 펼침 · `←` 접힘
        # (`screens.TokenLogScreen.on_key` 의 `tree_active` 갈래).
        if do in ("toggle", "expand", "collapse"):
            # 어느 노드가 펴져 있나는 **클라가 든다**(경고 판과 같은 처방 — 서버가 들면
            # 같은 서버를 보는 클라 둘이 서로의 펼침을 흔든다).
            state = req.get("state")
            opened = _folds(state, "tree_open")
            key = str(picked or "")
            if key:
                # 정본 `_tree_open` 은 `기본값 ^ 토글`이라 «펼침 집합»이 아니라
                # **«뒤집은 집합»**이다 — 그래서 대칭차다(`_warn_spec` 과 같은 자리).
                if do == "toggle":
                    opened.symmetric_difference_update({key})
                elif do == "expand":
                    opened.add(key)
                else:
                    opened.discard(key)
            _remember_folds(state, "tree_open", opened)
            return _period_spec(server, selected=row, opened=opened)
        return None
    if sid == "claude-token-sessions":
        if do == "apply":
            return _sessions_spec(server, selected=row)
        return None
    if sid == "claude-usage-panel":
        if do == "apply":
            # 다른 줄(막대·계정·신선도)은 누를 것이 없다 — 판을 그대로 둔다.
            return _limits_spec(server, row)
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
