"""Claude 패널의 **턴 블록**(pytmux-21) — 프롬프트 마커가 경계다.

# 이 테스트가 지키는 계약

1. **경계는 프롬프트 점프와 같은 자리다.** 블록의 시작 행 = `claude_prompt_marks` 가
   주는 인덱스. 둘이 갈라지면 `esc ctrl+↑` 이 데려간 자리와 고를 수 있는 자리가 어긋나고,
   그건 화면만 봐서는 안 보인다.
2. **첫 칸 거르기는 축소일 뿐 판정이 아니다.** 스크롤백을 싸게 훑으려고 `PROMPT_MARK_FIRST`
   로 후보를 먼저 거르는데, 그 표가 정규식보다 좁아지면 마커를 **조용히 놓친다**.
3. ⛔ **셸 패널에는 안 걸린다.** `> ` 로 시작하는 인용·diff 를 프롬프트로 오인한다
   (정본 `claude_jump_prompt` 가 같은 이유로 `_claude` 를 먼저 본다).
4. **증분 스캔이 전량 재훑기와 같은 답을 낸다** — 스크롤백이 회전해 행 번호가 통째로
   밀린 뒤에도. 이 산수가 틀리면 **강조된 것과 복사되는 것이 어긋난다**(눈으로는 못 잡는
   부류다 — 화면은 맞고 클립보드만 틀리다).
5. **같은 화면을 매 프레임 다시 안 보낸다**(멱등). 블록 프레임은 flush 마다 나가므로
   여기가 새면 대역폭이 화면 프레임만큼 늘어난다.
6. **Claude 패널에서는 이쪽이 셸 블록을 이긴다.** 그 패널에서 고르고 싶은 것은 턴이지,
   그 패널을 띄운 `claude` 명령 한 덩이가 아니다.
"""
import importlib
import random

import harness  # noqa: F401 (경로 설정)
from pytmuxlib import plugins
from pytmuxlib.model import line_text
from pytmuxlib.nativescreen import NativeScrollbackScreen
from pytmuxlib.plugins.blocks import pane_osc

# 하이픈 디렉토리라 import 문법으로는 못 부른다(다른 claude-code 테스트와 같은 관례).
claude = importlib.import_module("pytmuxlib.plugins.claude-code.claude")
promptblocks = importlib.import_module("pytmuxlib.plugins.claude-code.promptblocks")


class _FakePane:
    """스크린 하나를 든 최소 패널. 진짜 `Pane` 은 PTY 를 물고 오므로 여기선 과하다."""

    def __init__(self, cols=40, rows=5, history=20, is_claude=True):
        self.screen = NativeScrollbackScreen(cols, rows, history=history)
        self._claude = "idle" if is_claude else None
        self._feed_seq = 0

    def write(self, *lines):
        """줄들을 화면에 찍는다(스크롤백으로 밀려 나갈 수 있다)."""
        for text in lines:
            self.screen.draw(text)
            self.screen.linefeed()
            self.screen.cursor.x = 0
        self._feed_seq += 1
        return self

    def texts(self):
        """스크롤백+화면을 **전량** 문자열로 — 전량 재훑기 오라클용."""
        s = self.screen
        lines = list(s.history.top) + [s.buffer[y] for y in range(s.lines)]
        return [line_text(ln, 0, s.columns - 1).rstrip() for ln in lines]


# ---- 경계 ------------------------------------------------------------------

async def test_turn_starts_where_the_prompt_jump_lands():
    """계약 ①: 블록의 시작 행 = 프롬프트 점프의 목표 행. 표는 하나다."""
    pane = _FakePane().write(
        "> 첫 프롬프트", "", "⏺ 답변 1",
        "  > 들여쓴 인용(프롬프트 아님)",
        "> 둘째 프롬프트", "  이어지는 줄(마커 없음)", "⏺ 답변 2",
    )
    starts = [b["start"] for b in promptblocks.wire(pane)]
    assert starts == claude.claude_prompt_marks(pane.texts()), starts


async def test_the_cheap_first_cell_filter_never_narrows_the_regex():
    """계약 ②: 첫 칸 거르기는 정규식이 열 0 에 못 박은 것과 **같은 집합**이라야 한다.

    좁아지면 마커를 조용히 놓치고(블록이 통째로 사라진다), 넓어지면 후보만 늘 뿐
    판정은 정규식이 하므로 무해하다 — 그래서 여기서 막는 것은 **좁아지는 쪽**이다.
    """
    for first in claude.PROMPT_MARK_FIRST:
        assert claude.claude_prompt_marks([first + " 내용"]) == [0], first
    # 표 밖의 글자로 시작하는 줄은 정규식도 절대 안 잡는다(전수는 못 세니 대표를 본다).
    for first in (">", "❯", "»", "|", "#", "$", " ", "…", "│"):
        matched = claude.claude_prompt_marks([first + " 내용"]) == [0]
        assert matched == (first in claude.PROMPT_MARK_FIRST), first


async def test_a_shell_pane_contributes_nothing():
    """계약 ③: `> ` 는 셸에서 인용·diff 이기도 하다 — 그 패널에는 안 건다."""
    shell = _FakePane(is_claude=False).write("> 인용문", "> diff 한 줄", "출력")
    assert promptblocks.marks(shell) == []
    assert promptblocks.wire(shell) is None
    assert not promptblocks.dirty(shell)


async def test_a_pane_without_any_prompt_has_no_turns():
    """아직 프롬프트를 안 보낸 Claude 패널 — 빈 목록이 아니라 **없음**(None)이다.

    클라는 None 을 "보낼 것 없음"으로 읽는다. 빈 목록을 보내면 프레임만 늘고,
    받는 쪽에서는 "블록 기능이 켜졌는데 비었다"와 구별이 안 된다.
    """
    pane = _FakePane().write("Claude 배너", "무엇을 도와드릴까요?")
    assert promptblocks.wire(pane) is None


# ---- 와이어 ----------------------------------------------------------------

async def test_the_wire_carries_the_prompt_text_and_no_verdict():
    pane = _FakePane().write("> 테스트 돌려줘", "⏺ 돌립니다", "> 다음 것도", "⏺ 네")
    wire = promptblocks.wire(pane)
    assert [b["cmd"] for b in wire] == ["테스트 돌려줘", "다음 것도"]
    assert {b["state"] for b in wire} == {"turn"}, "턴은 성패를 갖지 않는다"
    assert all("exit" not in b for b in wire), "종료코드를 지어내지 않는다"
    # `end` 는 안 싣는다 — 다음 턴의 시작이 곧 이 턴의 끝 다음 줄이라 같은 값을 두 번
    # 적는 셈이다(클라 `proto::blocks::row_span`).
    assert all("end" not in b for b in wire)


async def test_control_characters_never_reach_the_client():
    """프롬프트 글은 **패널 안 아무 프로그램**이 그린 화면에서 나온다.

    클라는 이 문자열을 요약 판에 그대로 그리므로, 살아남은 이스케이프는 사용자 단말에
    주입된다(셸 블록의 `_sanitize_cmd` 와 같은 규율).
    """
    folded = promptblocks._prompt_body("> echo \x1b]0;pwned\x07")
    assert "\x1b" not in folded and "\x07" not in folded
    assert "pwned" in folded, "글자까지 지울 필요는 없다"
    assert len(promptblocks._prompt_body("> " + "가" * 5000)) <= promptblocks.MAX_CMD_LEN


async def test_the_turn_list_is_capped():
    """계약: 상한 없는 목록은 이 저장소가 이미 클라 프리즈로 물린 부류다."""
    pane = _FakePane(history=4000)
    pane.write(*["> 프롬프트 %d" % i for i in range(promptblocks.MAX_BLOCKS + 50)])
    wire = promptblocks.wire(pane)
    assert len(wire) == promptblocks.MAX_BLOCKS
    # 잘리는 쪽은 **오래된 것**이다 — 방금 친 프롬프트를 못 고르면 첫 쓰임이 죽는다.
    assert wire[-1]["cmd"] == "프롬프트 %d" % (promptblocks.MAX_BLOCKS + 49)


# ---- 증분 스캔 -------------------------------------------------------------

async def test_the_incremental_scan_matches_a_full_rescan_across_rotation():
    """계약 ④: 스크롤백이 회전해 행 번호가 밀려도 증분 결과가 전량과 같아야 한다."""
    rng = random.Random(20260805)
    pane = _FakePane(rows=5, history=20)
    for step in range(200):
        pane.write(*[
            "> 프롬프트 %d" % step if rng.random() < 0.3 else "출력 %d-%d" % (step, k)
            for k in range(rng.randint(1, 7))
        ])
        texts = pane.texts()
        want = [(i, texts[i]) for i in claude.claude_prompt_marks(texts)]
        assert promptblocks.marks(pane) == want[-promptblocks.MAX_BLOCKS:], step


async def test_a_burst_bigger_than_the_search_window_still_lands_on_the_truth():
    """앵커를 못 찾으면 전량 재훑기로 떨어진다 — 느려도 **틀리지는 않는다**."""
    saved = promptblocks._SEARCH_BACK
    promptblocks._SEARCH_BACK = 2          # 되짚기 창을 줄여 폴백을 강제로 태운다
    try:
        rng = random.Random(7)
        pane = _FakePane(rows=5, history=20)
        for step in range(120):
            pane.write(*[
                "> p%d" % step if rng.random() < 0.3 else "out %d-%d" % (step, k)
                for k in range(rng.randint(1, 9))
            ])
            texts = pane.texts()
            want = [(i, texts[i]) for i in claude.claude_prompt_marks(texts)]
            assert promptblocks.marks(pane) == want[-promptblocks.MAX_BLOCKS:], step
    finally:
        promptblocks._SEARCH_BACK = saved


async def test_clearing_the_scrollback_does_not_leave_ghost_rows():
    """`clear-history` 뒤에 옛 마커가 남으면 **없는 행**을 가리키는 블록이 된다."""
    pane = _FakePane().write("> 프롬프트", "출력")
    assert promptblocks.wire(pane)
    pane.screen.history.top.clear()
    pane.screen.history.bottom.clear()
    pane.screen.reset()
    pane._feed_seq += 1
    assert promptblocks.wire(pane) is None


async def test_a_resize_reflows_the_prompt_text():
    """폭이 바뀌면 잘리는 자리가 달라진다 — 캐시가 옛 글자를 붙들고 있으면 안 된다."""
    pane = _FakePane(cols=40, history=50).write("> " + "가" * 30, "출력")
    assert promptblocks.wire(pane)[0]["cmd"].startswith("가")
    pane.screen.resize(5, 12)
    pane._feed_seq += 1
    texts = pane.texts()
    want = [(i, texts[i]) for i in claude.claude_prompt_marks(texts)]
    assert promptblocks.marks(pane) == want


# ---- 멱등 -----------------------------------------------------------------

async def test_the_same_screen_is_not_resent_every_frame():
    """계약 ⑤: 바뀐 것이 없으면 dirty 가 아니다."""
    pane = _FakePane().write("> 프롬프트", "출력")
    assert promptblocks.dirty(pane), "첫 목록은 보내야 한다"
    promptblocks.clear_dirty(pane)
    assert not promptblocks.dirty(pane)
    pane.write("출력이 더 붙는다")            # 턴 경계는 그대로다
    assert not promptblocks.dirty(pane), "출력만 늘었는데 목록을 다시 보냈다"
    pane.write("> 새 프롬프트")
    assert promptblocks.dirty(pane), "새 턴이 생겼는데 조용하다"


# ---- 레지스트리 순서 -------------------------------------------------------

async def test_the_claude_source_wins_in_a_claude_pane():
    """계약 ⑥: 같은 패널에 출처가 둘이면 **더 구체적인 쪽**이 이긴다.

    ⛔ 디렉터리 이름의 사전순은 결정이 아니다 — `blocks` < `claude-code` 라서 종전
    순회는 셸 블록(대개 `claude` 명령 한 덩이)만 보냈을 것이다.
    """
    reg = plugins.load()
    names = [getattr(p, "name", "") for p in reg._blocks_sources()
             if getattr(p, "blocks_wire", None) is not None]
    assert names.index("claude-code") < names.index("blocks"), names

    pane = _FakePane().write("> 프롬프트 하나", "⏺ 답")
    pane.osc_handler = reg.pane_osc
    # 이 패널을 띄운 셸도 통합이 걸려 있어 자기 블록을 만든다(`claude` 명령 한 덩이).
    for code, param in (("133", "A"), ("633", "E;claude;n"), ("133", "C")):
        pane_osc(pane, code, param)
    payload = reg.pane_blocks(pane)
    assert [b["state"] for b in payload] == ["turn"], payload
    assert payload[0]["cmd"] == "프롬프트 하나"


async def test_the_registry_dirty_protocol_reaches_this_source():
    """훅 이름·시그니처가 어긋나면 **조용히** 아무 프레임도 안 나간다.

    flush 는 `pane_blocks_changed` 로 묻고(물어보면 표식이 내려간다) `pane_blocks` 로
    가져간다. 이 왕복을 여기서 재지 않으면 위 단위 테스트가 전부 초록인데 화면에는
    턴이 하나도 안 뜨는 상태가 성립한다.
    """
    reg = plugins.load()
    pane = _FakePane().write("> 첫 프롬프트", "⏺ 답")
    assert reg.pane_blocks_changed(pane), "새 턴을 보낼 것으로 안 쳤다"
    assert reg.pane_blocks(pane), "가져갈 것이 없다"
    assert not reg.pane_blocks_changed(pane), "물어본 뒤에도 표식이 안 내려갔다"
    pane.write("> 다음 프롬프트")
    assert reg.pane_blocks_changed(pane)
