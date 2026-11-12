"""Tier C **선언형 화면 스펙** — 이름 동기화 규칙 판(pytmux-35 · pytmux-615 · UI 무의존).

# 왜 이 파일인가

정본은 `:namesync` 로 Textual 편집기(`screen.py`)를 띄운다. 네이티브 클라는 파이썬을
못 읽어 그 화면이 없었고, 그래서 팔레트에 보이는데 눌러도 *"이 플러그인은 화면 스펙을
제공하지 않습니다"* 로 끝났다 — 죽은 줄 하나.

# 정본과 **같은 손**이다 (pytmux-615 · 사람 결정 2026-11-13 「정본 그대로」)

정본 `NameSyncScreen.on_key` 그대로다:

| 키 | 정본 | 여기 |
|---|---|---|
| `Enter` | 지금 cwd 를 채운 **추가 폼** | `addcwd` |
| `a` | 빈 추가 폼(host/os 는 이 머신 값) | `add` |
| `e` | 고른 규칙의 **네 칸 수정 폼** | `edit` |
| `d` | **확인 없이** 지운다(메모리에서) | `del` |
| `j`/`k` | ↓/↑ | 클라 로컬 `@down`/`@up` |
| `Esc` | **저장하고** 닫는다(편집은 메모리, 저장은 닫을 때 한 번) | 판의 `on_close` = `save` |

폼(정본 `RuleFormScreen`)은 키워드·경로·호스트·OS 네 칸과 저장·취소다. 정본은 칸마다
입력란이 있고 `Ctrl+S` 로 저장하며 경로·키워드가 비면 저장을 거절한다. 여기 폼은 줄마다
값이 붙은 판(`form`)이고 `Enter` 가 그 칸을 묻는다(물음의 초기값 = 지금 값). `Ctrl+S` 와
`저장` 줄이 저장이고 `Esc`·`취소` 줄이 취소다 — 정본 버튼 둘과 같다.

새 규칙의 host/os 기본값은 정본처럼 **이 머신의 값**이다(`namesync_get` 이 주는
`_this_host()`/`_this_os()` 와 같은 함수). 비우면 모든 머신/OS(와일드카드)다.

# 편집은 **초안**에서

정본은 규칙 목록의 로컬 사본을 고치다가 `Esc` 에서 한꺼번에 서버에 넘긴다. 여기서도
그 사본(초안)은 이 클라의 화면 보관함(`req["state"]`)에 살고, 클라가 판을 `Esc` 로 닫을 때
보내는 `on_close` 액션(`save`)에서 한 번 저장한다.
"""
from __future__ import annotations

from pytmuxlib import i18n

#: 이 판의 화면 id. 명령 이름과 같게 둔다(`plugin_screen` 이 이것으로 가른다).
SID = "namesync"
#: 규칙 폼(정본 `RuleFormScreen`)의 화면 id — 목록 판과 **따로** 둔다. 저장할 때 이 id 만
#: 닫으면 밑의 목록 판이 드러나고, `Esc` 로 걷으면 그것이 곧 취소다.
FORM_ID = "namesync-form"
#: 이 판을 여는 명령 이름들(정본 `handle_command` 와 같은 표).
NAMES = ("namesync", "nsync")

#: 목록에서 누를 수 있는 키(정본 `NameSyncScreen.on_key`). `@` 는 클라 로컬 손이다.
_KEYS = {"enter": "addcwd", "a": "add", "e": "edit", "d": "del",
         "j": "@down", "k": "@up"}
#: 폼의 키 — `Enter` 는 그 칸을 묻고, `Ctrl+S` 는 저장(정본 `RuleFormScreen.on_key`).
_FORM_KEYS = {"enter": "field", "ctrl-s": "save"}
#: 폼의 칸(정본 `RuleFormScreen.compose` 의 차례).
_FIELDS = ("keyword", "path", "host", "os")
#: 와일드카드 표식(정본 `_ANY` — 두 로케일 다 같은 글자라 자료로 둔다).
_ANY = "(any)"

i18n.register({
    "ko": {
        "nsync.title": "namesync — 이름 동기화 규칙",
        "nsync.hint": ("↑↓ 이동 · [a]추가 [e]수정 [d]삭제 · [Enter] 현재 cwd 로 추가 · "
                       "[Esc] 저장·닫기"),
        "nsync.empty": "규칙이 없습니다. [a] 또는 [Enter](현재 cwd)로 추가하세요.",
        "nsync.form_add": "규칙 추가 — Ctrl+S 저장 · Esc 취소",
        "nsync.form_edit": "규칙 수정 — Ctrl+S 저장 · Esc 취소",
        "nsync.form_hint": "Enter 그 칸 고치기 · Ctrl+S 저장 · Esc 취소",
        "nsync.l_keyword": "키워드(탭·패널·Claude 세션 이름)",
        "nsync.l_path": "경로(정확히 이 디렉토리에서 실행 시)",
        "nsync.l_host": "호스트(비우면 모든 머신)",
        "nsync.l_os": "OS: darwin/linux/windows(비우면 모든 OS)",
        "nsync.err_req": "경로와 키워드는 필수입니다.",
        "nsync.save": "저장",
        "nsync.cancel": "취소",
    },
    "en": {
        "nsync.title": "namesync — name-sync rules",
        "nsync.hint": ("↑↓ move · [a]add [e]edit [d]delete · [Enter] add current cwd · "
                       "[Esc] save & close"),
        "nsync.empty": "No rules. Add with [a] or [Enter] (current cwd).",
        "nsync.form_add": "Add rule — Ctrl+S save · Esc cancel",
        "nsync.form_edit": "Edit rule — Ctrl+S save · Esc cancel",
        "nsync.form_hint": "Enter edit that field · Ctrl+S save · Esc cancel",
        "nsync.l_keyword": "Keyword (tab · pane · Claude session name)",
        "nsync.l_path": "Path (matched when Claude runs in exactly this directory)",
        "nsync.l_host": "Host (blank = any machine)",
        "nsync.l_os": "OS: darwin/linux/windows (blank = any OS)",
        "nsync.err_req": "Path and keyword are required.",
        "nsync.save": "Save",
        "nsync.cancel": "Cancel",
    },
})

_LABELS = {"keyword": "nsync.l_keyword", "path": "nsync.l_path",
           "host": "nsync.l_host", "os": "nsync.l_os"}


def _rules(server):
    return list(getattr(server, "_namesync_rules", None) or [])


def _active_cwd(sess):
    """지금 패널의 디렉토리 — `Enter`(현재 cwd 로 추가)의 기본값.

    ⚠ **캐시된 값만** 읽는다(`_ns_cwd`). 여기서 `server._pane_cwd` 를 부르면 macOS
    lsof(≤2s)가 요청 핸들러에서 이벤트 루프를 막아 전 클라가 얼어붙는다 —
    `handle_server_request` 의 `namesync_get` 이 같은 이유로 같은 값을 쓴다."""
    win = getattr(sess, "active_window", None) if sess is not None else None
    pane = getattr(win, "active_pane", None) if win is not None else None
    return str(getattr(pane, "_ns_cwd", "") or "") if pane is not None else ""


def _mine(req):
    """이 클라의 화면 보관함(설계 Tier C · P5) — 초안·폼·진행 중인 물음이 여기 산다."""
    state = req.get("state")
    if not isinstance(state, dict):
        state = {}
    return state.setdefault(SID, {})


def _draft(server, mine):
    """편집 중인 규칙 목록(정본 `NameSyncScreen._rules` — 서버 규칙의 사본)."""
    if not isinstance(mine.get("draft"), list):
        mine["draft"] = [dict(r) for r in _rules(server)]
    return mine["draft"]


def _list_spec(rules, selected=0, note=""):
    rows = []
    for i, r in enumerate(rules):
        host = (r.get("host") or "").strip() or _ANY
        osn = (r.get("os") or "").strip() or _ANY
        rows.append({
            # 자리 번호를 열쇠로 나른다 — 규칙에는 자연 열쇠가 없고(같은 경로를 두 번 걸 수
            # 있다) 그래서 쓰는 쪽이 매번 **지금 초안에 대고 검증**한다(`_pick`).
            "key": str(i),
            # 칸 차례는 정본 줄(`_render_list`)과 같다 — `host/os  path  → keyword`.
            "label": f"{host}/{osn}",
            "cols": [str(r.get("path") or ""), "→ " + str(r.get("keyword") or "")],
        })
    return {
        "t": "plugin_screen", "id": SID, "kind": "table",
        "title": i18n.t("nsync.title"), "hint": i18n.t("nsync.hint"),
        "rows": rows, "text": "", "note": note or (
            "" if rows else i18n.t("nsync.empty")),
        "selected": max(0, min(int(selected), max(0, len(rows) - 1))),
        "keys": dict(_KEYS),
        # 정본 `Esc` = 저장하고 닫기 — 클라가 이 판을 `Esc` 로 걷을 때 보낸다.
        "on_close": "save",
        "i18n": {},
    }


def _form_spec(form, selected=0, note=""):
    rule = form.get("rule") or {}
    rows = [{"key": f, "label": i18n.t(_LABELS[f]), "cols": [str(rule.get(f) or "")]}
            for f in _FIELDS]
    rows.append({"key": "@save", "label": i18n.t("nsync.save"), "cols": [""]})
    rows.append({"key": "@cancel", "label": i18n.t("nsync.cancel"), "cols": [""]})
    return {
        "t": "plugin_screen", "id": FORM_ID, "kind": "form",
        "title": i18n.t("nsync.form_edit" if form.get("index") is not None
                        else "nsync.form_add"),
        "hint": i18n.t("nsync.form_hint"),
        "rows": rows, "text": "", "note": note,
        "selected": max(0, min(int(selected), len(rows) - 1)),
        "keys": dict(_FORM_KEYS), "i18n": {},
    }


def _form_ask(title, seed=""):
    """폼의 한 칸을 묻는 판. `text` 가 **입력칸 초기값**이다 — 고치는 화면인데 지금 값이
    안 실리면 '편집'이 아니라 '다시 치기'가 된다. 답은 폼 id 로 돌아온다(`apply`)."""
    return {
        "t": "plugin_screen", "id": FORM_ID, "kind": "prompt",
        "title": title, "hint": "", "rows": [], "text": seed,
        "note": "", "selected": 0, "keys": {"enter": "apply"}, "i18n": {},
    }


def _pick(rules, picked, row):
    """고른 줄의 규칙 번호 — 지금 초안 밖이면 `None`(낡은 줄을 되돌려받아도 안전하다)."""
    for cand in (picked, row):
        try:
            i = int(cand)
        except (TypeError, ValueError):
            continue
        if 0 <= i < len(rules):
            return i
    return None


def _save(server, rules):
    from . import _sanitize_rules
    server._namesync_rules = _sanitize_rules(rules)
    try:
        server._save_opts()
    except Exception:
        # 저장 실패는 이 판의 일이 아니다(디스크·권한) — 값은 이미 서버에 섰고
        # 다음 저장에 다시 시도된다. 여기서 죽으면 화면이 통째로 안 뜬다.
        pass


def open_spec(server, sess, name, state=None):
    """명령 이름 → 첫 화면. 내 이름이 아니면 `None`.

    초안은 **열 때** 서버 규칙에서 새로 뜬다(정본 `namesync_get` → `NameSyncScreen`)."""
    if name not in NAMES:
        return None
    mine = state.setdefault(SID, {}) if isinstance(state, dict) else {}
    mine.clear()
    return _list_spec(_draft(server, mine))


def _open_form(mine, index, rule):
    mine["form"] = {"index": index, "rule": dict(rule)}
    return _form_spec(mine["form"])


def action(server, sess, req):
    """판 안에서 누른 것 → 다음 스펙. 내 화면이 아니면 `None`."""
    sid = req.get("id")
    if sid not in (SID, FORM_ID):
        return None
    do = str(req.get("do") or "")
    row = int(req.get("row") or 0)
    picked = req.get("input")
    mine = _mine(req)
    rules = _draft(server, mine)
    if sid == FORM_ID:
        return _form_action(server, sess, mine, rules, do, row, picked)
    if do == "close":
        return {"t": "plugin_screen_close", "id": SID}
    if do == "save":
        # 정본 `Esc` — 초안을 한꺼번에 저장한다(`namesync_set` 과 같은 값·같은 알림).
        _save(server, rules)
        mine.pop("draft", None)
        n = len(getattr(server, "_namesync_rules", None) or [])
        text = i18n.t("nsmsg.saved").format(n=n)
        return [{"t": "plugin_screen_close", "id": SID},
                {"t": "notice", "sev": "info", "key": "nsmsg.saved",
                 "kw": {"n": n}, "text": text}]
    if do in ("add", "addcwd"):
        # 새 규칙 — host/os 는 **이 머신의 값**(정본 `_open_form(None)` 의 `self._host`·
        # `self._os` = `namesync_get` 의 `_this_host()`/`_this_os()`).
        from . import _this_host, _this_os
        path = _active_cwd(sess) if do == "addcwd" else ""
        return _open_form(mine, None, {"host": _this_host(), "os": _this_os(),
                                       "path": path, "keyword": ""})
    if do == "edit":
        i = _pick(rules, picked, row)
        if i is None:
            return _list_spec(rules, row)
        return _open_form(mine, i, rules[i])
    if do == "del":
        # 정본 `d` — **확인 없이** 초안에서 지운다(저장은 `Esc` 에서).
        i = _pick(rules, picked, row)
        if i is None:
            return _list_spec(rules, row)
        del rules[i]
        return _list_spec(rules, min(i, max(0, len(rules) - 1)))
    return None


def _form_action(server, sess, mine, rules, do, row, picked):
    form = mine.get("form")
    if not isinstance(form, dict):
        return {"t": "plugin_screen_close", "id": FORM_ID}
    if do == "field":
        key = str(picked or "")
        if key == "@save":
            do = "save"
        elif key == "@cancel":
            mine.pop("form", None)
            return {"t": "plugin_screen_close", "id": FORM_ID}
        elif key in _FIELDS:
            form["ask"] = key
            return _form_ask(i18n.t(_LABELS[key]),
                             seed=str((form.get("rule") or {}).get(key) or ""))
        else:
            return _form_spec(form, row)
    if do == "apply":
        key = form.pop("ask", None)
        if key in _FIELDS:
            form.setdefault("rule", {})[key] = str(picked or "").strip()
            return _form_spec(form, _FIELDS.index(key))
        return _form_spec(form, row)
    if do == "save":
        rule = {f: str((form.get("rule") or {}).get(f) or "").strip() for f in _FIELDS}
        if not rule["path"] or not rule["keyword"]:
            # 정본 `_try_save` — 경로·키워드가 비면 저장을 거절하고 폼에 남는다.
            return _form_spec(form, row, i18n.t("nsync.err_req"))
        index = form.get("index")
        if isinstance(index, int) and 0 <= index < len(rules):
            rules[index] = rule
            sel = index
        else:
            rules.append(rule)
            sel = len(rules) - 1
        mine.pop("form", None)
        return [{"t": "plugin_screen_close", "id": FORM_ID}, _list_spec(rules, sel)]
    return None
