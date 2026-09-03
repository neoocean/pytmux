"""플랜 전문·거부 사유 판(pytmux-468 · 449 ⑵) — **두 파서가 같은 글자를 내나**.

# 왜 이 시험이 이 슬라이스의 값인가

2026-07-28 사용자 결정(`clienttail.py` 머리말)은 *"상류가 원문을 보내고 클라가 파싱한다 —
파서는 하나로 남는다"* 였고, 그 근거가 **"파서가 둘이면 같은 대화가 탭에 따라 달라 보인다"**
였다. 2026-09-04 결정이 그것을 ⓐ(서버가 짓는다)로 바꾸면서 파이썬에 슬라이스 파서가 생겼다.

⇒ **그 걱정을 값으로 막는 자리가 여기다.** 러스트가 쓰는 **같은 픽스처**를 파이썬에 먹여
러스트 `claude::source::detail_lines` 의 출력과 **글자까지** 견준다. 기준값은 그 크레이트를
실제로 돌려 뜬 것이고(2026-09-04 · `cargo test -p claude`), 갈리면 여기서 운다.
"""

import importlib
import os

import harness  # noqa: F401  (경로 설정)

_FIXTURE = os.path.join(os.path.dirname(__file__), "..", "client", "crates",
                        "claude", "tests", "fixtures", "session.jsonl")

# ★ 러스트가 그 픽스처에서 실제로 낸 것(기준값). 손으로 지은 글이 아니다 —
#   `claude::source::detail_lines(Transcript::parse(session.jsonl))` 의 출력을 그대로 옮겼다.
_RUST_TRUTH = [
    ("플랜 [ok]", "plan_head"),
    ("  1. 실패하는 테스트를 고친다", "body"),
    ("  2. 게이트 3종을 돌린다", "body"),
    ("  3. 문서를 뒤집는다", "body"),
    ("", "blank"),
    ("막힌 호출", "denied_head"),
    ("  no Write /work/proj/notes.md  1줄", "body"),
    ("  사유: The user doesn't want to proceed with this tool use. "
     "The tool use was rejected (eg. if it was a file edit).", "body"),
]


def _mod():
    return importlib.import_module("pytmuxlib.plugins.claude-code.detail")


def _fixture_text():
    with open(_FIXTURE, encoding="utf-8") as fp:
        return fp.read()


async def test_the_two_parsers_say_the_same_words():
    """⛔ **이 슬라이스가 서는 자리**. 두 파서가 한 글자라도 갈리면 같은 대화가 탭에 따라
    달라 보인다 — 2026-07-28 이 ⓐ 를 안 고른 이유가 그것이었다."""
    got = _mod().detail_lines(_fixture_text())
    assert got == _RUST_TRUTH, (
        "파이썬 판이 러스트와 다른 글자를 낸다 — 어느 쪽이 옳은지 정하고 **둘 다** 고칠 것:"
        f"\n  파이썬: {got}\n  러스트: {_RUST_TRUTH}")


async def test_the_fixture_is_the_one_rust_uses():
    """기준값이 **같은 입력**에서 나온 것이라야 대조가 뜻을 갖는다. 픽스처가 사라지거나
    옮겨지면 여기서 먼저 운다(조용히 다른 파일을 읽는 것보다 낫다)."""
    assert os.path.isfile(_FIXTURE), f"러스트 픽스처가 그 자리에 없다: {_FIXTURE}"
    text = _fixture_text()
    assert "ExitPlanMode" in text, "픽스처에 플랜이 없다 — 대조가 절반만 된다"
    assert "The user doesn't want to proceed" in text, "픽스처에 거부가 없다"


async def test_a_denial_is_judged_by_prefix_not_by_containment():
    """⛔ 그 문구는 툴 **출력 안에** 인용될 수 있다(그 문구를 찾는 grep 결과가 그렇다).
    포함 검사로 하면 그 출력이 통째로 거부로 뒤집힌다 — 러스트가 접두로만 보는 이유이고,
    파이썬이 그 함정을 **똑같이** 밟지 않아야 한다."""
    d = _mod()
    assert d._denial_reason("Permission for this action was denied. Reason: 위험") == "위험"
    assert d._denial_reason("The user doesn't want to proceed with this") is not None
    # 인용된 것은 거부가 아니다.
    assert d._denial_reason(
        'grep 결과: "Permission for this action was denied" 가 3곳에 있다') is None
    assert d._denial_reason("") is None


async def test_a_plan_without_a_body_still_shows_something():
    """전문이 없으면 요약이라도 보인다 — 빈 화면은 "없는 것"과 "못 읽은 것"이 구분되지
    않는다(러스트 `detail_lines` 의 같은 갈래)."""
    d = _mod()
    line = ('{"type":"assistant","message":{"content":['
            '{"type":"tool_use","id":"t1","name":"ExitPlanMode","input":{}}]}}')
    got = d.detail_lines(line)
    assert got and got[0][1] == "plan_head", got
    assert len(got) == 2 and got[1][1] == "body", got


async def test_nothing_to_show_is_an_empty_list():
    """플랜도 거부도 없으면 빈 목록 — 호출부가 "보여 줄 것이 없다"를 그린다."""
    d = _mod()
    assert d.detail_lines("") == []
    assert d.detail_lines("깨진 줄\n{아니야}\n") == [], "깨진 줄은 그 줄만 버린다"
    assert d.detail_lines(
        '{"type":"assistant","message":{"content":['
        '{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls"}}]}}'
    ) == [], "성공한 툴만 있으면 이 판에 낼 것이 없다"


async def test_a_control_byte_cannot_reach_the_screen():
    """이 글은 그대로 화면에 그려진다 — `\\x1b` 가 살아 있으면 트랜스크립트에 담긴 아무
    바이트나 **사용자 단말에 이스케이프를 주입**할 수 있다(툴 결과에는 실제로 ANSI 가
    들어 있다). 러스트 `clip` 과 같은 규칙이다."""
    d = _mod()
    line = ('{"type":"assistant","message":{"content":['
            '{"type":"tool_use","id":"t1","name":"ExitPlanMode",'
            '"input":{"plan":"\\u001b[31m붉게\\u001b[0m"}}]}}')
    body = [text for text, kind in d.detail_lines(line) if kind == "body"]
    assert body and "\x1b" not in body[0], body
