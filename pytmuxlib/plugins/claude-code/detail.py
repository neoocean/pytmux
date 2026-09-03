"""Claude 트랜스크립트에서 **플랜 전문과 거부 사유**만 뽑는다(pytmux-468 · 449 ⑵).

# 왜 파이썬에 파서가 생겼나 — 그 결정을 뒤집은 자리다

2026-07-28 사용자 결정은 *"상류가 원문을 보내고 클라가 파싱한다 — 파서는 하나로 남는다"*
(ⓑ′)였다(`clienttail.py` 머리말이 그 근거를 적는다). 그 결정의 비용 계산은 *"파이썬에
**항목 파서**를 새로 쓰는 것"*(러스트 `claude` 크레이트 약 500줄)이었다.

2026-09-04 사람 결정이 그것을 **ⓐ 로 바꿨다**: 서버가 Tier C 스펙을 짓고, 갈림은
픽스처로 못박는다. 그 결정이 성립하는 근거는 **크기가 그때 잰 것과 다르다**는 것이다 —
이 판이 쓰는 것은 항목 전부가 아니라 **둘**이다:

  · 마지막 **플랜**(`ExitPlanMode`) — 그 입력의 전문 + 상태(승인/거부)
  · 마지막 **거부** — 결과가 거부 접두로 시작하는 툴 호출의 이름·요약·사유

그래서 이 모듈은 목록 화면을 만들지 않는다(프롬프트·답변·thinking·툴 흐름은 여전히
러스트만 안다). **그 둘만** 본다.

# ⛔ 갈리면 같은 대화가 탭마다 달라 보인다 — 그것을 값으로 막는다

2026-07-28 이 걱정한 바로 그것이다. `tests/test_claude_detail.py` 가 **러스트와 같은
픽스처**(`client/crates/claude/tests/fixtures/session.jsonl`)를 먹여 **글자가 같은지**
잰다. 기준값은 러스트 `claude::source::detail_lines` 의 출력이고, 그 목록이 이 파일의
docstring 이 아니라 **시험 안에** 있다(글은 낡고 시험은 운다).

# ⛔ 거부 판정은 **접두로만** 한다

그 문구가 툴 출력 *안에* 인용될 수 있어(그 문구를 찾는 grep 결과가 그렇다) 포함 검사로
하면 그 출력이 통째로 거부로 뒤집힌다. 러스트 쪽 `DENIAL_PREFIXES` 주석이 적어 둔 함정을
여기서 **똑같이** 밟지 않는다.
"""

from __future__ import annotations

import json

from pytmuxlib import i18n

# 러스트 `claude` 크레이트와 **같은 값**이라야 한다(같은 글자를 내는 것이 이 모듈의 일).
# 시험이 두 파서의 출력을 대조하므로 이 셋이 갈리면 거기서 운다.
MAX_TEXT = 200        # 한 항목의 글자 상한
MAX_BODY_LINES = 40   # 본문(플랜 전문) 줄 상한
MAX_ITEMS = 200       # 항목 상한(오래된 것부터 버린다)

PLAN_TOOL = "ExitPlanMode"

# 거부를 알리는 Claude Code 의 정형 문구. **접두로만** 본다(위 ⛔).
DENIAL_PREFIXES = (
    "Permission for this action was denied",
    "The user doesn't want to proceed",
)


def _clip(text: str) -> str:
    """제어문자를 지우고 길이를 자른다.

    이 문자열은 그대로 화면에 그려진다 — `\\x1b` 가 살아 있으면 트랜스크립트에 담긴
    아무 바이트나 **사용자 단말에 이스케이프를 주입**할 수 있다(툴 결과에는 실제로 ANSI
    가 들어 있다)."""
    cleaned = "".join(" " if _is_control(c) else c for c in text)
    trimmed = cleaned.strip()
    if len(trimmed) > MAX_TEXT:
        return trimmed[:MAX_TEXT] + "…"
    return trimmed


def _is_control(ch: str) -> bool:
    """러스트 `char::is_control` 과 같은 판정 — C0 과 C1 이다(파이썬 `str.isprintable`
    은 공백·비ASCII 규칙이 달라 결과가 갈린다)."""
    o = ord(ch)
    return o < 0x20 or 0x7F <= o <= 0x9F


def _one_line(text: str):
    """여러 줄에서 **의미 있는 첫 줄**. 빈 문자열이면 None."""
    for line in text.splitlines():
        line = line.strip()
        if line:
            clipped = _clip(line)
            return clipped or None
    return None


def _clip_body(text: str) -> str:
    """여러 줄 본문을 화면에 그려도 되는 모양으로. 줄바꿈은 남기고 나머지 제어문자는 지운다."""
    lines = text.splitlines()
    out = [_clip(ln) for ln in lines[:MAX_BODY_LINES]]
    if len(lines) > MAX_BODY_LINES:
        out.append("…")
    return "\n".join(out)


def _field(inp, key):
    v = (inp or {}).get(key) if isinstance(inp, dict) else None
    return v if isinstance(v, str) else None


def summarize_tool(name: str, inp) -> str:
    """툴 호출 한 줄 요약.

    툴마다 **무엇이 그 호출을 설명하는가**가 다르다: 셸은 명령줄, 파일 툴은 경로, 검색은
    패턴이다. 이름만 보이면 목록이 `Bash Bash Bash` 가 되어 아무 정보가 없다.
    표는 러스트 `summarize_tool` 과 **같은 것**이다."""
    summary = None
    if name in ("Bash", "BashOutput"):
        summary = _field(inp, "command") or _field(inp, "description")
    elif name in ("Read", "NotebookEdit"):
        summary = _field(inp, "file_path")
    elif name == "Write":
        p = _field(inp, "file_path")
        if p is not None:
            content = (inp or {}).get("content") if isinstance(inp, dict) else None
            if isinstance(content, str):
                # 쓴 분량이 그 호출의 크기다 — 경로만으로는 한 줄인지 천 줄인지 모른다.
                # ⚠ 사용자 유래 값(경로)은 마지막 자리 — 값 안의 `{n}` 재치환 방지.
                summary = i18n.t("{text}  {n}줄").replace(
                    "{n}", str(len(content.splitlines()))).replace("{text}", p)
            else:
                summary = p
    elif name == "Edit":
        p = _field(inp, "file_path")
        if p is not None:
            def count(key):
                v = _field(inp, key)
                return len(v.splitlines()) if v else 0
            summary = f"{p}  -{count('old_string')}/+{count('new_string')}"
    elif name in ("Grep", "Glob"):
        summary = _field(inp, "pattern")
    elif name in ("Task", "Agent"):
        summary = _field(inp, "description")
    elif name == "AskUserQuestion":
        qs = (inp or {}).get("questions") if isinstance(inp, dict) else None
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            q = qs[0].get("question")
            summary = q if isinstance(q, str) else None
    elif name in ("WebFetch", "WebSearch"):
        summary = _field(inp, "url") or _field(inp, "query")
    if summary is None and isinstance(inp, dict):
        # 모르는 툴도 **뭔가는** 보여 준다 — 첫 문자열 인자를 쓴다. 새 툴이 생길 때마다
        # 이 표를 고쳐야만 쓸모가 있으면, 안 고친 동안 목록이 비어 보인다.
        for v in inp.values():
            if isinstance(v, str):
                summary = v
                break
    return (_one_line(summary) or "") if summary is not None else ""


def _result_text(content) -> str:
    """툴 결과 본문을 문자열로. 실측 코퍼스에서 `content` 는 **문자열 91,009건 ·
    배열 4,781건**이라 두 모양을 다 받아야 한다(배열이면 `text` 조각을 잇는다)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(p["text"] for p in content
                        if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def _denial_reason(text: str):
    """거부 결과면 사유를, 아니면 None. **접두로만** 판정한다(위 ⛔)."""
    stripped = text.lstrip()
    if not any(stripped.startswith(p) for p in DENIAL_PREFIXES):
        return None
    # 자동 모드 분류기는 `Reason:` 뒤에 진짜 이유를 적는다. 없으면 문장 자체가 이유다.
    head, sep, rest = stripped.partition("Reason:")
    reason = rest if sep else stripped
    return _one_line(reason) or _one_line(stripped)


_BADGE = {"running": "···", "ok": "ok", "failed": "err", "denied": "no"}


class _Item:
    __slots__ = ("kind", "name", "title", "detail", "state")

    def __init__(self, kind, name, title, detail=None, state="running"):
        self.kind, self.name = kind, name
        self.title, self.detail, self.state = title, detail, state


def _parse(text: str):
    """JSONL 에서 **플랜·거부 판정에 필요한 만큼만** 항목을 만든다.

    깨진 줄은 **그 줄만** 버린다 — 쓰는 중인 파일의 마지막 줄이 반쯤 쓰여 있는 것은
    정상이고, 그걸로 전체를 포기하면 화면이 빈다."""
    items: list[_Item] = []
    pending: list[tuple[str, int]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        kind = ev.get("type")
        content = (ev.get("message") or {}).get("content") \
            if isinstance(ev.get("message"), dict) else None
        if kind == "assistant" and isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_use":
                    continue
                name = block.get("name") or "tool"
                inp = block.get("input")
                if name == PLAN_TOOL:
                    body = _field(inp, "plan")
                    items.append(_Item(
                        "plan", None,
                        _plan_title(body, inp),
                        _clip_body(body) if body is not None else None))
                else:
                    items.append(_Item("tool", _clip(str(name)),
                                       summarize_tool(str(name), inp)))
                bid = block.get("id")
                if isinstance(bid, str):
                    pending.append((bid, len(items) - 1))
        elif kind == "user" and isinstance(content, list):
            # 훅·요약 등이 사용자 턴으로 들어오는 경우가 있다 — 사람이 친 것이 아니다.
            if ev.get("isMeta") is True:
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                _resolve(items, pending, block)
    if len(items) > MAX_ITEMS:
        items = items[len(items) - MAX_ITEMS:]
    return items


def _plan_title(body, inp) -> str:
    if body is None:
        return summarize_tool(PLAN_TOOL, inp)
    # 플랜은 여러 줄이다. 목록에는 첫 줄과 분량만 — 전문은 `detail` 로 간다.
    head = _one_line(body) or ""
    # ⚠ 사용자 유래 값(head)은 마지막 자리 — 값 안의 `{n}` 재치환 방지.
    return i18n.t("{text}  {n}줄").replace(
        "{n}", str(len(body.splitlines()))).replace("{text}", head)


def _resolve(items, pending, block) -> None:
    """툴 결과를 그 호출에 붙인다. 짝을 못 찾으면 버린다 — 짝 없는 결과를 새 항목으로
    만들면 목록에 출처 없는 줄이 생긴다(이어받은 세션의 앞부분이 그렇다)."""
    tid = block.get("tool_use_id")
    if not isinstance(tid, str):
        return
    for pos, (pid, index) in enumerate(pending):
        if pid == tid:
            pending.pop(pos)
            break
    else:
        return
    if not 0 <= index < len(items):
        return
    reason = _denial_reason(_result_text(block.get("content")))
    if reason is not None:
        items[index].state = "denied"
        # 사유는 **거부일 때만** 붙인다 — 플랜 전문을 결과 텍스트로 덮으면 안 된다.
        items[index].detail = reason
    elif block.get("is_error") is True:
        items[index].state = "failed"
    else:
        items[index].state = "ok"


def detail_lines(text: str):
    """플랜 전문·거부 사유를 **화면에 그릴 줄들**로. 없으면 빈 목록.

    돌려주는 것은 `(글, 갈래)` 이고 갈래는 `plan_head`/`denied_head`/`body`/`blank` 다 —
    러스트 `source::DetailKind` 와 같은 넷이다(색은 뷰가 정한다)."""
    items = _parse(text)
    plan = next((i for i in reversed(items) if i.kind == "plan"), None)
    denied = next((i for i in reversed(items) if i.state == "denied"), None)

    lines = []
    if plan is not None:
        lines.append((i18n.t("플랜 [{state}]").replace(
            "{state}", _BADGE[plan.state]), "plan_head"))
        if plan.detail is not None:
            lines.extend((f"  {ln}", "body") for ln in plan.detail.splitlines())
        else:
            # 전문이 없으면 요약이라도 보인다 — 빈 화면은 "없는 것"과 "못 읽은 것"이
            # 구분되지 않는다.
            lines.append((f"  {plan.title}", "body"))
    if denied is not None:
        if lines:
            lines.append(("", "blank"))
        lines.append((i18n.t("막힌 호출"), "denied_head"))
        name = denied.name or ""
        lines.append((f"  {_BADGE[denied.state]} {name} {denied.title}", "body"))
        if denied.detail:
            # ⚠ 사유는 사용자 유래 값 — 마지막(유일한) 자리로 끼운다. 앞 두 칸은
            # 언어가 아니라 들여쓰기라 번역 밖에 둔다.
            lines.append(("  " + i18n.t("사유: {reason}").replace(
                "{reason}", denied.detail), "body"))
    return lines
