"""Tier C **선언형 화면 스펙** — 이름 동기화 규칙 판(pytmux-35 · UI 무의존).

# 왜 이 파일인가

정본은 `:namesync` 로 Textual 편집기(`screen.py`)를 띄운다. 네이티브 클라는 파이썬을
못 읽어 그 화면이 없었고, 그래서 팔레트에 보이는데 눌러도 *"이 플러그인은 화면 스펙을
제공하지 않습니다"* 로 끝났다 — 죽은 줄 하나.

# 무엇을 담고 무엇을 안 담았나

담은 것은 **규칙의 목록과 그 위의 네 손**이다: 보기 · 더하기 · 키워드/경로 고치기 ·
지우기. 그 넷이 이 기능의 전부다(*"이 경로에서 Claude 를 띄우면 이 이름으로"*).

안 담은 것은 **머신/OS 좁히기**다. 정본 편집기에는 규칙마다 `host`·`os` 칸이 있어
"이 머신에서만" 을 걸 수 있는데, 그것을 여기 담으려면 한 줄에 네 값을 묻는 판이 필요하고
스펙에는 그런 모양이 없다(물음은 글 하나다). 새로 만든 규칙은 **아무 머신/OS**
(와일드카드 — 정본 편집기의 기본값과 같다)로 서고, 좁히려면 터미널 클라에서 연다.
목록에는 그 값이 칸으로 **보인다** — 안 보이면 왜 규칙이 안 먹는지 알 길이 없다.

# 되돌릴 수 없는 것은 묻는다

지우기는 `confirm` 이고 무엇이 사라지는지를 물음에 싣는다(mdir 이 먼저 낸 규율).
취소는 여기까지 오지 않는다 — 클라가 아무것도 안 보낸다.
"""
from __future__ import annotations

from pytmuxlib import i18n

#: 이 판의 화면 id. 명령 이름과 같게 둔다(`plugin_screen` 이 이것으로 가른다).
SID = "namesync"
#: 이 판을 여는 명령 이름들(정본 `handle_command` 와 같은 표).
NAMES = ("namesync", "nsync")

#: 목록에서 누를 수 있는 키. `enter` 는 가장 잦은 손(키워드 고치기)에 준다.
_KEYS = {"enter": "kw", "a": "add", "p": "path", "d": "del"}

i18n.register({
    "ko": {
        "nsync.title": "이름 동기화 규칙",
        "nsync.hint": "Enter 키워드 · p 경로 · a 추가 · d 삭제 · Esc 닫기",
        "nsync.empty": "(규칙이 없습니다 — a 로 지금 패널의 디렉토리를 추가합니다)",
        "nsync.any": "아무 곳",
        "nsync.ask_path": "규칙을 걸 디렉토리 경로",
        "nsync.ask_kw": "그 디렉토리에서 쓸 이름 키워드",
        "nsync.ask_del": "이 규칙을 지웁니다",
        "nsync.no_input": "값이 비어 있어 아무것도 안 했습니다",
        "nsync.saved_one": "규칙을 저장했습니다",
    },
    "en": {
        "nsync.title": "Name sync rules",
        "nsync.hint": "Enter keyword · p path · a add · d delete · Esc close",
        "nsync.empty": "(no rules — press a to add this pane's directory)",
        "nsync.any": "anywhere",
        "nsync.ask_path": "Directory the rule applies to",
        "nsync.ask_kw": "Name keyword to use in that directory",
        "nsync.ask_del": "Delete this rule",
        "nsync.no_input": "Empty value — nothing was done",
        "nsync.saved_one": "Rule saved",
    },
})


def _rules(server):
    return list(getattr(server, "_namesync_rules", None) or [])


def _active_cwd(sess):
    """지금 패널의 디렉토리 — 새 규칙의 기본값.

    ⚠ **캐시된 값만** 읽는다(`_ns_cwd`). 여기서 `server._pane_cwd` 를 부르면 macOS
    lsof(≤2s)가 요청 핸들러에서 이벤트 루프를 막아 전 클라가 얼어붙는다 —
    `handle_server_request` 의 `namesync_get` 이 같은 이유로 같은 값을 쓴다."""
    win = getattr(sess, "active_window", None) if sess is not None else None
    pane = getattr(win, "active_pane", None) if win is not None else None
    return str(getattr(pane, "_ns_cwd", "") or "") if pane is not None else ""


def _mine(req):
    """이 클라의 화면 보관함(설계 Tier C · P5) — 커서·진행 중인 물음이 여기 산다."""
    state = req.get("state")
    if not isinstance(state, dict):
        state = {}
    return state.setdefault(SID, {})


def _list_spec(server, selected=0, note=""):
    rows = []
    for i, r in enumerate(_rules(server)):
        where = " · ".join(x for x in ((r.get("host") or "").strip(),
                                       (r.get("os") or "").strip()) if x)
        rows.append({
            # 자리가 아니라 **자리 번호**를 열쇠로 나른다 — 목록은 다시 만들어지고,
            # 그 사이 줄이 늘거나 줄면 자리는 다른 줄을 가리킨다. 규칙에는 자연 열쇠가
            # 없으므로(같은 경로를 두 번 걸 수 있다) 번호가 최선이고, 그래서 쓰는 쪽이
            # 매번 **지금 목록에 대고 검증**한다(`_pick`).
            "key": str(i),
            "label": str(r.get("keyword") or ""),
            "cols": [str(r.get("path") or ""), where or i18n.t("nsync.any")],
        })
    return {
        "t": "plugin_screen", "id": SID, "kind": "table",
        "title": i18n.t("nsync.title"), "hint": i18n.t("nsync.hint"),
        "rows": rows, "text": "", "note": note or (
            "" if rows else i18n.t("nsync.empty")),
        "selected": max(0, min(int(selected), max(0, len(rows) - 1))),
        "keys": dict(_KEYS), "i18n": {},
    }


def _ask(kind, title, note="", seed="", act="apply"):
    """물음 한 판. `text` 가 **입력칸 초기값**이다 — 고치는 화면인데 지금 값이 안 실리면
    '편집'이 아니라 '다시 치기'가 된다."""
    return {
        "t": "plugin_screen", "id": SID, "kind": kind,
        "title": title, "hint": "", "rows": [], "text": seed,
        "note": note, "selected": 0, "keys": {"enter": act}, "i18n": {},
    }


def _pick(server, mine, picked, row):
    """고른 줄의 규칙 번호 — 지금 목록 밖이면 `None`(낡은 줄을 되돌려받아도 안전하다)."""
    rules = _rules(server)
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


def open_spec(server, sess, name):
    """명령 이름 → 첫 화면. 내 이름이 아니면 `None`."""
    if name not in NAMES:
        return None
    return _list_spec(server)


def action(server, sess, req):
    """판 안에서 누른 것 → 다음 스펙. 내 화면이 아니면 `None`."""
    if req.get("id") != SID:
        return None
    do = str(req.get("do") or "")
    row = int(req.get("row") or 0)
    picked = req.get("input")
    mine = _mine(req)
    if do == "close":
        return {"t": "plugin_screen_close", "id": SID}

    if do == "add":
        mine["ask"] = {"op": "add"}
        return _ask("prompt", i18n.t("nsync.ask_path"),
                    seed=_active_cwd(sess), act="apply")
    if do in ("kw", "path"):
        i = _pick(server, mine, picked, row)
        if i is None:
            return _list_spec(server, row)
        rules = _rules(server)
        mine["ask"] = {"op": do, "i": i}
        field = "keyword" if do == "kw" else "path"
        return _ask("prompt",
                    i18n.t("nsync.ask_kw" if do == "kw" else "nsync.ask_path"),
                    seed=str(rules[i].get(field) or ""), act="apply")
    if do == "del":
        i = _pick(server, mine, picked, row)
        if i is None:
            return _list_spec(server, row)
        r = _rules(server)[i]
        mine["ask"] = {"op": "del", "i": i}
        # 무엇이 사라지는지를 물음에 함께 싣는다(첫 줄이 물음, 나머지가 상세).
        return _ask("confirm", i18n.t("nsync.ask_del"),
                    note=f"{r.get('keyword') or ''} — {r.get('path') or ''}")

    if do != "apply":
        return None
    ask = mine.pop("ask", None)
    if not ask:
        return _list_spec(server, row)
    answer = str(picked or "").strip()
    rules = _rules(server)
    op = ask.get("op")
    if op == "add":
        if not answer:
            return _list_spec(server, row, i18n.t("nsync.no_input"))
        # 경로를 받았으니 **키워드를 마저 묻는다** — 규칙 하나에 값이 둘인데 물음은
        # 글 하나라, 두 걸음으로 나눈다.
        mine["ask"] = {"op": "add2", "path": answer}
        return _ask("prompt", i18n.t("nsync.ask_kw"), act="apply")
    if op == "add2":
        if not answer:
            return _list_spec(server, row, i18n.t("nsync.no_input"))
        rules.append({"path": ask.get("path") or "", "keyword": answer,
                      "host": "", "os": ""})
        _save(server, rules)
        return _list_spec(server, len(rules) - 1, i18n.t("nsync.saved_one"))
    i = ask.get("i")
    if not isinstance(i, int) or not (0 <= i < len(rules)):
        return _list_spec(server, row)
    if op == "del":
        del rules[i]
        _save(server, rules)
        return _list_spec(server, min(i, max(0, len(rules) - 1)))
    if op in ("kw", "path"):
        if not answer:
            return _list_spec(server, i, i18n.t("nsync.no_input"))
        rules[i] = dict(rules[i],
                        **{"keyword" if op == "kw" else "path": answer})
        _save(server, rules)
        return _list_spec(server, i, i18n.t("nsync.saved_one"))
    return _list_spec(server, row)
