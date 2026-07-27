"""블록 플러그인 — 셸 통합(OSC 133)으로 명령 경계를 잡는다.

# 이 테스트가 지키는 계약

1. **셸 통합이 없으면 아무것도 안 바뀐다.** 블록은 선택 기능이고, 안 깐 사용자의
   화면·프레임은 종전과 같아야 한다.
2. **기존 클라는 한 바이트도 더 안 받는다.** `caps` 에 `blocks` 를 광고한 클라에게만
   `blocks` 메시지가 간다.
3. **블록 목록에 상한이 있다.** 스크롤백이 회전하듯 블록도 잘려야 한다 — 상한 없는
   목록은 이 저장소가 이미 클라 프리즈로 물린 부류다(HANDOFF F-G).
4. **셸이 끝을 못 알려도 목록이 굳지 않는다.** Ctrl-C·셸 재시작으로 `D` 가 안 와도
   다음 프롬프트에서 정리된다.
"""
import harness  # noqa: F401 (경로 설정)
from pytmuxlib import plugins
from pytmuxlib.model import Pane
from pytmuxlib.plugins.blocks import blocks_dirty, blocks_wire, pane_osc
from pytmuxlib.plugins.blocks.segment import MAX_BLOCKS, Segmenter


def _pane():
    """OSC 훅이 꽂힌 패널(서버가 만드는 것과 같은 상태)."""
    pane = Pane(-1, -1, 40, 10)
    pane.osc_handler = plugins.load().pane_osc
    return pane


def _osc(code, param):
    return f"\x1b]{code};{param}\x1b\\".encode()


# ---- 경계 판정 (순수 상태 기계) --------------------------------------------

async def test_full_command_cycle_makes_one_finished_block():
    seg = Segmenter()
    seg.on_osc("7", "file://host/tmp/work")
    seg.on_osc("133", "A", row=10)
    seg.on_osc("133", "B")
    seg.set_command("ls -la")
    seg.on_osc("133", "C", row=11)
    seg.on_osc("133", "D;0")

    assert len(seg.blocks) == 1
    block = seg.blocks[0]
    assert block.cmd == "ls -la"
    assert block.state == "done"
    assert block.exit == 0
    assert block.cwd == "/tmp/work"
    assert block.start_row == 10


async def test_exit_code_is_carried_and_nonzero_is_distinguishable():
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("133", "C", row=1)
    seg.on_osc("133", "D;127")
    assert seg.blocks[0].exit == 127


async def test_missing_exit_code_means_unknown_not_zero():
    """`D` 만 오고 코드가 없으면 '성공'으로 넘겨짚지 않는다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("133", "D")
    assert seg.blocks[0].exit is None
    assert seg.blocks[0].state == "done"


async def test_unterminated_block_is_closed_by_the_next_prompt():
    """Ctrl-C·셸 재시작으로 D 가 안 와도 다음 프롬프트에서 정리된다.

    안 그러면 목록이 영원히 '실행 중'으로 남아 사용자가 보기에 멈춘 것과 같다.
    """
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("133", "C", row=1)          # 실행 시작 — D 가 안 온다
    assert seg.blocks[0].state == "running"
    seg.on_osc("133", "A", row=5)          # 다음 프롬프트
    assert seg.blocks[0].state == "done"
    assert seg.blocks[0].end_row == 5
    assert len(seg.blocks) == 2


async def test_output_start_without_prompt_start_still_makes_a_block():
    """A 없이 C 만 보내는 부분 통합 셸에서도 블록이 생긴다."""
    seg = Segmenter()
    seg.on_osc("133", "C", row=3)
    assert len(seg.blocks) == 1
    assert seg.blocks[0].state == "running"


async def test_unknown_osc_133_subkinds_are_ignored_quietly():
    """셸이 확장 필드(`P;k=...`)를 보내도 블록이 깨지지 않는다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    before = len(seg.blocks)
    assert not seg.on_osc("133", "P;k=v")
    assert len(seg.blocks) == before


async def test_unrelated_osc_codes_do_not_make_blocks():
    seg = Segmenter()
    assert not seg.on_osc("9", "알림")
    assert not seg.on_osc("52", "c;base64")
    assert seg.blocks == []


async def test_cwd_parses_file_url_and_ignores_junk():
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("7", "file://host/tmp/%ED%95%9C%EA%B8%80")
    assert seg.blocks[0].cwd == "/tmp/한글", "퍼센트 인코딩을 풀어야 한다"
    assert not seg.on_osc("7", "형식이아니다")
    assert seg.blocks[0].cwd == "/tmp/한글", "잘못된 값이 기존 cwd 를 지우면 안 된다"


# ---- 상한과 회전 ------------------------------------------------------------

async def test_block_list_is_bounded():
    """상한 없는 목록은 금지다(HANDOFF F-G)."""
    seg = Segmenter(max_blocks=5)
    for i in range(50):
        seg.on_osc("133", "A", row=i)
        seg.on_osc("133", "D;0")
    assert len(seg.blocks) == 5, "상한을 넘겨 쌓였다"


async def test_default_bound_is_finite():
    assert 0 < MAX_BLOCKS < 100000


async def test_blocks_are_dropped_when_scrollback_rotates_past_them():
    """스크롤백이 회전해 그 행이 사라지면 블록도 사라져야 한다.

    안 그러면 존재하지 않는 행을 가리키는 블록이 쌓인다.
    """
    seg = Segmenter()
    for row in (0, 100, 200):
        seg.on_osc("133", "A", row=row)
        seg.on_osc("133", "C", row=row + 1)
        seg.on_osc("133", "D;0")
        seg.blocks[-1].end_row = row + 2

    assert seg.drop_before(150), "잘린 블록이 있으면 True"
    assert len(seg.blocks) == 1
    assert seg.blocks[0].start_row == 200
    assert not seg.drop_before(150), "더 자를 게 없으면 False"


# ---- 코어 배선(OSC 훅) -------------------------------------------------------

async def test_osc_133_from_the_wire_reaches_the_plugin():
    """실제 바이트를 패널에 먹여 훅까지 도달하는지 본다."""
    pane = _pane()
    pane.feed(_osc("133", "A"))
    pane.feed(_osc("133", "C"))
    pane.feed(_osc("133", "D;0"))
    wire = blocks_wire(pane)
    assert wire is not None, "블록이 안 생겼다"
    assert wire[0]["state"] == "done"
    assert wire[0]["exit"] == 0


async def test_title_osc_still_sets_the_title_and_makes_no_block():
    """타이틀(OSC 0/2)은 코어가 처리하고 블록 훅에 오지 않는다."""
    pane = _pane()
    pane.feed("\x1b]2;내 제목\x1b\\".encode())
    assert pane.screen.title == "내 제목"
    assert blocks_wire(pane) is None


async def test_pane_without_shell_integration_makes_no_blocks():
    """계약 ①: 셸 통합이 없으면 아무것도 안 생긴다."""
    pane = _pane()
    pane.feed(b"echo hi\r\nhi\r\n")
    assert blocks_wire(pane) is None
    assert not blocks_dirty(pane)


async def test_pane_without_a_handler_ignores_osc_quietly():
    """플러그인 디렉토리를 지운 상태(= 훅 미장착)에서도 죽지 않는다."""
    pane = Pane(-1, -1, 20, 5)          # osc_handler 를 안 꽂는다
    pane.feed(_osc("133", "A"))
    assert getattr(pane, "_blocks_segmenter", None) is None


async def test_blocks_dirty_clears_after_being_read():
    """매 프레임 같은 목록을 다시 보내지 않기 위한 표식."""
    pane = _pane()
    pane.feed(_osc("133", "A"))
    assert blocks_dirty(pane)
    payload = plugins.load().pane_blocks(pane)
    assert payload is not None
    assert not blocks_dirty(pane), "읽은 뒤에는 표식이 내려가야 한다"
    assert plugins.load().pane_blocks(pane) is None, "안 바뀌었으면 보낼 것 없음"


async def test_absolute_rows_do_not_move_when_the_viewport_scrolls():
    """블록 좌표는 **절대** 행이라 스크롤해도 안 움직인다."""
    pane = _pane()
    pane.feed(b"\r\n" * 30)             # 스크롤백을 쌓는다
    pane.feed(_osc("133", "A"))
    first = blocks_wire(pane)[0]["start"]
    pane.scroll = 5                      # 뷰포트만 위로
    pane.feed(_osc("133", "A"))
    second = blocks_wire(pane)[-1]["start"]
    assert second >= first, "절대 좌표가 뷰포트를 따라 움직였다"
