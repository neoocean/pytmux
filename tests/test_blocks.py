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
5. **명령 텍스트는 셸이 말해 준 것만 싣는다**(OSC 633;E). 화면에서 긁어 추측하지
   않고, 실려 온 문자열은 제어문자·길이를 걸러 클라에 넘긴다 — 이 값은 패널 안의
   아무 프로그램이나 보낼 수 있다.
"""
import os
import shlex
import shutil
import subprocess

import harness  # noqa: F401 (경로 설정)
from run import skip
from pytmuxlib import plugins
from pytmuxlib.plugins import blocks as blocks_pkg
from pytmuxlib.model import Pane
from pytmuxlib.plugins.blocks import blocks_dirty, blocks_wire, pane_osc
from pytmuxlib.plugins.blocks.segment import MAX_BLOCKS, MAX_CMD_LEN, Segmenter


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


# ---- 명령 텍스트 (OSC 633;E) -------------------------------------------------

async def test_shell_reported_command_text_names_the_block():
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    assert seg.on_osc("633", "E;ls -la"), "명령 텍스트가 목록을 바꾼다"
    seg.on_osc("133", "C", row=1)
    seg.on_osc("133", "D;0")
    assert seg.blocks[0].cmd == "ls -la"


async def test_semicolons_in_the_command_survive_field_splitting():
    """escape 가 없으면 `git log; ls` 가 `git log` 로 잘린다 — 실제로 흔한 명령이다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("633", r"E;git log\x3b ls")
    assert seg.blocks[0].cmd == "git log; ls"


async def test_backslashes_survive_and_are_not_double_decoded():
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    # 셸이 보낸 `\\x3b` = "백슬래시 + x3b" 지 `;` 가 아니다.
    seg.on_osc("633", r"E;echo \\x3b")
    assert seg.blocks[0].cmd == r"echo \x3b"


async def test_unescaped_backslash_is_left_alone():
    """escape 를 안 하는 셸이 보낸 경로가 사라지면 안 된다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("633", r"E;type C:\path\to")
    assert seg.blocks[0].cmd == r"type C:\path\to"


async def test_trailing_nonce_field_is_not_part_of_the_command():
    """VSCode 규약의 `E;<명령줄>;<nonce>`. nonce 가 명령 이름에 붙으면 안 된다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("633", "E;ls;deadbeef")
    assert seg.blocks[0].cmd == "ls"


async def test_control_chars_cannot_ride_into_the_command_text():
    """이 문자열은 클라가 그대로 그린다 — ESC 가 살아 있으면 단말 주입이 된다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("633", r"E;echo \x1b]0\x3bpwned\x07")
    cmd = seg.blocks[0].cmd
    assert "\x1b" not in cmd and "\x07" not in cmd, f"제어문자가 살아남았다: {cmd!r}"
    assert "pwned" in cmd, "글자까지 지울 필요는 없다"


async def test_multiline_command_does_not_glue_words_together():
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("633", r"E;echo a\x0aecho b")
    assert seg.blocks[0].cmd == "echo a echo b"


async def test_command_text_is_bounded():
    """OSC 는 패널 안 아무 프로그램이나 보낸다 — 상한이 없으면 메모리를 불린다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("633", "E;" + "x" * (MAX_CMD_LEN * 3))
    assert len(seg.blocks[0].cmd) == MAX_CMD_LEN


async def test_command_text_does_not_rewrite_a_finished_block():
    """지나간 블록의 이름이 뒤늦은 한 줄로 바뀌면 사용자가 보던 목록이 뒤틀린다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("633", "E;진짜 명령")
    seg.on_osc("133", "D;0")
    assert not seg.on_osc("633", "E;나중에 온 것")
    assert seg.blocks[0].cmd == "진짜 명령"


async def test_other_633_subkinds_are_ignored_so_blocks_are_not_doubled():
    """VSCode 통합이 함께 걸려 있어도 경계는 133 한 곳에서만 판정한다."""
    seg = Segmenter()
    for kind in ("A", "B", "C", "D;0", "P;Cwd=/tmp"):
        assert not seg.on_osc("633", kind), f"633;{kind} 가 목록을 건드렸다"
    assert seg.blocks == []


async def test_command_text_reaches_the_wire_through_the_pane():
    pane = _pane()
    pane.feed(_osc("133", "A"))
    pane.feed(_osc("633", "E;echo 한글"))
    pane.feed(_osc("133", "C"))
    assert blocks_wire(pane)[0]["cmd"] == "echo 한글"


async def test_real_shell_integration_emits_the_command_it_was_given():
    """`shell-integration.sh` 가 실제로 만드는 바이트를 패널에 먹여 본다.

    escape 규칙이 셸 쪽(파라미터 치환)과 서버 쪽(_unescape)에 **따로** 적혀 있어,
    한쪽만 고치면 조용히 어긋난다. 두 구현을 같은 문자열로 마주 세운다.
    """
    shell = shutil.which("zsh") or shutil.which("bash")
    if shell is None:
        skip("zsh·bash 없음(셸 통합 스크립트를 실행할 수 없다)")
    script = os.path.join(os.path.dirname(blocks_pkg.__file__),
                          "shell-integration.sh")
    typed = 'git log; grep "a b" C:\\path'
    out = subprocess.run(
        [shell, "-c", "source %s; __pytmux_report_cmd %s"
                      % (shlex.quote(script), shlex.quote(typed))],
        capture_output=True, timeout=20,
    )
    pane = _pane()
    pane.feed(_osc("133", "A"))
    pane.feed(out.stdout)
    assert blocks_wire(pane)[0]["cmd"] == typed, "셸 escape 와 서버 unescape 가 어긋났다"


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


async def test_change_gate_falls_after_being_asked():
    """매 프레임 같은 목록을 다시 보내지 않기 위한 게이트."""
    reg = plugins.load()
    pane = _pane()
    pane.feed(_osc("133", "A"))
    assert reg.pane_blocks_changed(pane), "바뀌었는데 안 바뀌었다고 한다"
    assert not reg.pane_blocks_changed(pane), "물어본 뒤에는 표식이 내려가야 한다"

    pane.feed(_osc("133", "D;0"))
    assert reg.pane_blocks_changed(pane), "다시 바뀌면 또 알려야 한다"


async def test_current_blocks_are_readable_regardless_of_the_change_gate():
    """새로 붙는 클라는 바뀐 적이 없어도 **현재** 목록을 받아야 한다.

    화면은 `_send_full` 로 받는데 블록만 안 오면 비대칭이라, 붙자마자 빈 화면처럼 보인다.
    """
    reg = plugins.load()
    pane = _pane()
    pane.feed(_osc("133", "A"))
    reg.pane_blocks_changed(pane)              # 게이트를 내린다
    payload = reg.pane_blocks(pane)
    assert payload, "게이트와 무관하게 현재 목록은 읽혀야 한다"


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


# ---- cwd·종료코드도 같은 신뢰 등급이다(검수 2026-07-27) ---------------------

async def test_control_chars_cannot_ride_into_the_cwd():
    """`OSC 7` 은 명령 텍스트와 **같은 등급**(패널 안 아무 프로그램)이고 같은 길로
    클라 화면에 그려진다. 게다가 위험이 한 겹 더 있다 — 퍼센트 디코드가 제어문자를
    **만들어 낸다**: `%1b` 는 URL 로는 평범한 글자이므로 unquote 뒤에 걸러야 한다."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("7", "file://host/tmp/%1b]0%3bpwned%07")
    cwd = seg.blocks[0].cwd
    assert cwd and "\x1b" not in cwd and "\x07" not in cwd, f"제어문자 생존: {cwd!r}"
    assert "pwned" in cwd, "글자까지 지울 필요는 없다"


async def test_cwd_is_bounded():
    from pytmuxlib.plugins.blocks.segment import MAX_CWD_LEN
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("7", "file://host/" + "d" * (MAX_CWD_LEN * 3))
    assert len(seg.blocks[0].cwd) == MAX_CWD_LEN


async def test_absurd_exit_code_is_unknown_not_a_number():
    """와이어 정수를 i64 로 읽는 클라(네이티브)에서 범위 밖 값은 **프레임 파싱을
    통째로** 실패시킨다 — 한 줄 OSC 로 블록 표시를 끌 수 있으면 안 된다. 모르면
    모른다(None)로 떨어뜨리는 것이 이 코드베이스의 규칙이다(성공으로 넘겨짚지 않는다)."""
    seg = Segmenter()
    seg.on_osc("133", "A", row=0)
    seg.on_osc("133", "C", row=1)
    seg.on_osc("133", "D;99999999999999999999999")
    assert seg.blocks[0].exit is None, seg.blocks[0].exit
    assert seg.blocks[0].state == "done", "코드를 몰라도 끝난 것은 끝난 것이다"
    # 정상 범위는 그대로 실린다(POSIX 128+signal · Windows 32비트 코드)
    seg2 = Segmenter()
    seg2.on_osc("133", "A", row=0)
    seg2.on_osc("133", "D;3221225477")
    assert seg2.blocks[0].exit == 3221225477
