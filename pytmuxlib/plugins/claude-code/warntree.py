"""Claude 경고 탭의 **트리 한 벌** — UI 무의존(pytmux-618).

정본 토큰 팝업의 `[경고]` 탭(`screens.TokenLogScreen._refresh_warn`)과 GUI 판
(`screenspec._warn_spec`)이 **같은 함수**로 줄을 짓는다. 종전 GUI 판은 날짜 묶음에
`HH:MM 배지 ×n` 만 있고 정본의 «활성 경고» 노드와 상황·할 일 본문이 없었다 — 경고를
보러 와서 «무엇을 하라는가»를 못 읽었다.

규칙(정본 2026-06-22 항목2 그대로):
  ⑴ 활성 경고(라이브 status)는 맨 위 노드(`active`) · **기본 펼침** · 머리 = 로케일 배지 ·
     자식 = 상황·할 일 본문(빈 줄은 건너뛴다).
  ⑵ 과거 이력(시간 내림차순)이 있으면 라벨 줄 하나, 그 아래 노드마다 «MM-DD HH:MM · 배지»
     · **기본 접힘** · 같은 본문. 활성과 같은 종류인 맨 위 이력은 그 onset 이라 건너뛴다.
  ⑶ 둘 다 없으면 «경고 없음» 한 줄.

글(카탈로그)도 여기 산다 — 서버가 GUI 판을 지을 때 이 글을 읽어야 하고, 서버는
Textual 화면 모듈(`screens.py`)을 안 읽는다.
"""
from __future__ import annotations

import time as _time

from pytmuxlib import i18n

# (글 — 제목·본문·이력 라벨·꼬리줄 — 은 플러그인 `__init__.py` 의 카탈로그에 있다: 서버가
#  읽는 자리여야 한다(test_i18n 의 그 게이트 · pytmux-34).)


def resolve_kind(kind, warn):
    """종류를 모르면(구버전 서버·직접 설정) 한글 배지 글로 한 번 가린다 — 정본 그대로."""
    if kind is None and warn:
        if "포맷" in warn or "미인식" in warn:
            return "fmt_unknown"
        if "반복" in warn or "루프" in warn:
            return "repeat"
    return kind


def body_key(kind):
    """종류 → 본문 카탈로그 열쇠의 가운데 토막(`fmt`·`repeat`·`long`)."""
    return {"fmt_unknown": "fmt", "repeat": "repeat"}.get(kind, "long")


def info_text(kind, warn=""):
    """경고 종류 → (제목, 줄목록). 첫 줄엔 현재 배지 글(warn)을 그대로 둔다(있으면)."""
    kind = resolve_kind(kind, warn)
    key = body_key(kind)
    title = i18n.t(f"claude.warn_{key}_title")
    body = i18n.t(f"claude.warn_{key}_body").split("\n")
    return title, ([warn, ""] + body if warn else body)


def badge(kind, warn, n=None):
    """경고 머리에 보일 **로케일 배지**. 반복·포맷은 카탈로그, 장기 턴은 언어중립 서버 글."""
    if kind == "repeat":
        return i18n.t("claude.warn_repeat_badge", n=n or 0)
    if kind == "fmt_unknown":
        return i18n.t("claude.warn_fmt_badge")
    return warn


def badge_phrase(kind, n=None):
    """`badge` 를 클라가 **자기 로케일로** 다시 지을 재료(`i18n.phrase` 의 spec). 장기 턴처럼
    언어중립 글이면 None."""
    if kind == "repeat":
        return i18n.phrase("claude.warn_repeat_badge", n=n or 0)[1]
    if kind == "fmt_unknown":
        return i18n.phrase("claude.warn_fmt_badge")[1]
    return None


def build(warn, kind, n, hist, opened=("active",)):
    """경고 트리 → `(제목, 노드 목록)`.

    노드 = `{"type": "head"|"body"|"label", "key", "text", "kind", "n", "when", "line"}`.
    `head` 의 `text` 는 정본 표에 적히는 머리 글(▼/▶ 제외), `when` 은 이력 노드의 시각
    토막(활성은 빈 글), `kind`·`n` 은 배지 재료다. `body` 의 `line` 은 본문 카탈로그 안의
    **줄 번호**(빈 줄 포함 셈) — 클라가 그 줄을 자기 로케일로 다시 짓는 데 쓴다.
    `opened` = 펼친 노드 키 집합(정본 기본 `{"active"}`)."""
    opened = set(opened or ())
    nodes = []
    title = i18n.t("pscreen.tklog_warn_title")
    hist = list(hist or [])

    def emit(key, header, rk, rn, when):
        nodes.append({"type": "head", "key": key, "text": header, "kind": rk,
                      "n": rn, "when": when, "open": key in opened})
        if key not in opened:
            return
        lines = i18n.t(f"claude.warn_{body_key(rk)}_body").split("\n")
        for i, ln in enumerate(lines):
            if not ln:
                continue
            nodes.append({"type": "body", "key": key, "text": ln, "kind": rk,
                          "line": i})

    if warn:
        rk = resolve_kind(kind, warn)
        atitle, _abody = info_text(rk, "")
        emit("active", badge(rk, warn, n), rk, n, "")
        title = atitle
        # 활성 경고의 onset 은 이력 맨 위(최신)에도 있어 중복이므로 건너뛴다.
        if hist and hist[0].get("kind") == rk:
            hist = hist[1:]
    if hist:
        nodes.append({"type": "label", "key": None, "msg": "pscreen.warn_history_label",
                      "text": i18n.t("pscreen.warn_history_label")})
        for rec in hist:
            ts = rec.get("ts", 0) or 0
            rk = rec.get("kind")
            rbadge = badge(rk, rec.get("badge", ""), rec.get("n"))
            when = (_time.strftime("%m-%d %H:%M", _time.localtime(ts)) if ts else "?")
            emit("h%.3f" % ts, when + " · " + rbadge, rk, rec.get("n"), when)
    if not warn and not hist:
        nodes.append({"type": "label", "key": None, "msg": "pscreen.tklog_warn_empty",
                      "text": i18n.t("pscreen.tklog_warn_empty")})
    return title, nodes


def title_key(warn, kind):
    """판 제목의 카탈로그 열쇠 — 활성 경고가 있으면 그 종류의 제목, 아니면 «Claude 경고»."""
    if warn:
        return f"claude.warn_{body_key(resolve_kind(kind, warn))}_title"
    return "pscreen.tklog_warn_title"


def toggle(nodes, opened, row, mode="toggle"):
    """`row` 줄의 노드를 펴거나 접는다(정본 `_warn_toggle_at`) — `opened` 를 제자리에서
    고친다. 라벨 줄·범위 밖·이미 그 상태면 무동작. 바뀐 노드 키(없으면 None)."""
    if not (0 <= row < len(nodes)):
        return None
    key = nodes[row].get("key")
    if key is None:
        return None
    expanded = key in opened
    if mode == "expand" and expanded:
        return None
    if mode == "collapse" and not expanded:
        return None
    if expanded:
        opened.discard(key)
    else:
        opened.add(key)
    return key


def head_row(nodes, key):
    """그 노드의 머리 줄 번호(접기를 본문 줄에서 했을 때 커서를 머리로). 없으면 0."""
    for i, r in enumerate(nodes):
        if r.get("type") == "head" and r.get("key") == key:
            return i
    return 0
