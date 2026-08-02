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
import tempfile

import harness  # noqa: F401 (경로 설정)
from run import skip
from pytmuxlib import plugins, pty_backend
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


#: Git for Windows 의 bash. PATH 에 없는 것이 보통이라 이름으로만 찾으면 못 만난다.
_GIT_BASH = (r"C:\Program Files\Git\bin\bash.exe",
             r"C:\Program Files\Git\usr\bin\bash.exe")


def _posix_shell():
    """**실제로 대답하는** zsh·bash. 없으면 None.

    이름으로 찾은 것을 그대로 믿으면 안 된다 — Windows 는 `%LOCALAPPDATA%\\Microsoft\\
    WindowsApps\\bash.exe`(WSL 실행 스텁)를 PATH 앞쪽에 둔다. WSL 배포판이 없으면 그건
    셸이 아니라 **설치 안내로 매달리는 프로그램**이고, `shutil.which("bash")` 는 그것을
    준다. 실제로 이 박스에서 그 스텁을 물어 테스트가 20초 타임아웃으로 죽었다
    (2026-07-27 alienware). 그래서 골라 놓고 한 번 물어본다.
    """
    seen = []
    for name in ("zsh", "bash"):
        found = shutil.which(name)
        if found:
            seen.append(found)
    seen.extend(p for p in _GIT_BASH if os.path.exists(p))
    for shell in seen:
        try:
            probe = subprocess.run([shell, "-c", "printf ok"],
                                   capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0 and probe.stdout.strip() == b"ok":
            return shell
    return None


async def test_real_shell_integration_emits_the_command_it_was_given():
    """`shell-integration.sh` 가 실제로 만드는 바이트를 패널에 먹여 본다.

    escape 규칙이 셸 쪽(파라미터 치환)과 서버 쪽(_unescape)에 **따로** 적혀 있어,
    한쪽만 고치면 조용히 어긋난다. 두 구현을 같은 문자열로 마주 세운다.
    """
    shell = _posix_shell()
    if shell is None:
        skip("동작하는 zsh·bash 없음(셸 통합 스크립트를 실행할 수 없다)")
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
    # BEL 은 **OSC 종결자**다 — escape 하지 않으면 명령줄에 든 BEL 하나가 문자열을
    # 거기서 끊어 뒤가 통째로 사라지고, 남은 글자는 화면에 그대로 쏟아진다(검수
    # 2026-07-30: 머리말은 "제어문자는 escape 한다"고 적어 뒀는데 BEL 만 빠져 있었다).
    # 서버는 표시 전에 제어문자를 공백으로 접으므로 기대값도 그 규칙을 따른다.
    typed_bel = "echo before\x07after"
    out2 = subprocess.run(
        [shell, "-c", "source %s; __pytmux_report_cmd %s"
                      % (shlex.quote(script), shlex.quote(typed_bel))],
        capture_output=True, timeout=20,
    )
    pane2 = _pane()
    pane2.feed(_osc("133", "A"))
    pane2.feed(out2.stdout)
    assert blocks_wire(pane2)[0]["cmd"] == typed_bel.replace("\x07", " "), \
        "BEL 이 OSC 를 끊어 명령이 잘렸다(셸 escape 누락)"


def _powershell_emits(expr):
    """`shell-integration.ps1` 을 실제 PowerShell 에서 돌려 나온 **바이트**.

    `-Command` 의 출력을 파이프로 받으면 PowerShell 이 BOM 을 앞에 붙이므로 벗긴다 —
    실 콘솔에서는 `[Console]::Write` 가 직접 쓰기 때문에 없는 바이트다.
    """
    shell = shutil.which("powershell") or shutil.which("pwsh")
    if shell is None:
        skip("powershell 없음(PowerShell 통합 스크립트를 실행할 수 없다)")
    script = os.path.join(os.path.dirname(blocks_pkg.__file__),
                          "shell-integration.ps1")
    out = subprocess.run(
        [shell, "-NoProfile", "-NoLogo", "-Command", '. "%s"; %s' % (script, expr)],
        capture_output=True, timeout=60,
    )
    return out.stdout.lstrip(b"\xef\xbb\xbf").rstrip(b"\r\n")


async def test_powershell_integration_emits_the_command_it_was_given():
    """`.ps1` 판도 `.sh` 와 **같은 escape 규약**인지 실제로 돌려 확인한다.

    셸이 늘 때마다 escape 를 새로 적게 되는데 서버의 `_unescape` 는 한 벌이다.
    특히 Windows 는 경로에 백슬래시가 흔해 `\\` 처리를 어기면 바로 드러난다.
    """
    typed = 'git log; findstr /c:"a b" C:\\path\\to'
    payload = typed.replace("'", "''")
    out = _powershell_emits("__pytmux_report_cmd '%s'" % payload)
    pane = _pane()
    pane.feed(_osc("133", "A"))
    pane.feed(out)
    assert blocks_wire(pane)[0]["cmd"] == typed, \
        "PowerShell escape 와 서버 unescape 가 어긋났다"
    # BEL(= OSC 종결자)도 `.sh` 와 같은 규칙으로 escape 되는지(검수 2026-07-30).
    # 문자열 안에 실제 BEL 을 넣기 위해 PowerShell 쪽에서 [char]7 로 이어 붙인다.
    out2 = _powershell_emits(
        "__pytmux_report_cmd ('echo before' + [string][char]7 + 'after')")
    pane2 = _pane()
    pane2.feed(_osc("133", "A"))
    pane2.feed(out2)
    assert blocks_wire(pane2)[0]["cmd"] == "echo before after", \
        "BEL 이 OSC 를 끊어 명령이 잘렸다(PowerShell escape 누락)"


async def test_powershell_integration_reports_a_windows_cwd_without_a_leading_slash():
    """`file:///D:/a/b` 의 URL 경로는 `/D:/a/b` 다 — 그 `/` 가 남으면 안 된다.

    남으면 네이티브 클라가 Claude 폴더 이름을 `-D--a-b` 로 만들고 실제 이름
    (`D--a-b`)과 **한 글자 차이로** 못 찾는다. 증상이 "세션이 없다"라 조용하다.

    **Windows 에서만** 유효하다 — pwsh 는 Linux/macOS 에도 깔려 있고(GHA ubuntu 러너가
    그렇다) 거기엔 `C:` 드라이브가 없어 `Set-Location` 이 조용히 실패한다. 그러면
    러너의 POSIX cwd 가 대신 실려 와 `/` 로 시작하는 것이 **정상인데** 이 단언이
    터진다. POSIX 쪽 계약은 아래 `..._a_posix_cwd_without_a_doubled_slash` 가 본다.
    """
    if not pty_backend.IS_WINDOWS:
        skip("Windows 아님(드라이브 경로가 없어 pwsh 가 `C:\\` 로 못 간다)")
    out = _powershell_emits("Set-Location C:\\Windows; __pytmux_report_cwd")
    pane = _pane()
    pane.feed(_osc("133", "A"))
    pane.feed(out)
    cwd = blocks_wire(pane)[0]["cwd"]
    assert not cwd.startswith("/"), "드라이브 경로 앞의 슬래시가 남았다: %r" % cwd
    assert cwd.lower().startswith("c:/"), cwd


async def test_powershell_integration_reports_a_posix_cwd_without_a_doubled_slash():
    """POSIX 의 pwsh 도 `.sh` 판과 **같은 cwd** 를 실어야 한다.

    `.ps1` 이 `file://$host/$p` 로 `/` 를 무조건 덧붙이던 시절, POSIX 의
    `ProviderPath`(`/home/me`)에는 `/` 가 이미 있어 `file://host//home/me` 가 나갔고
    파서에 `//home/me` 가 남았다 — 같은 디렉토리인데 `.sh` 셸(`/home/me`)과 문자열이
    달라 cwd 비교·Claude 폴더 판정이 어긋난다. pwsh 는 Windows 전용이 아니다.
    """
    if pty_backend.IS_WINDOWS:
        skip("Windows(드라이브 경로라 POSIX 절대경로 계약이 아니다)")
    # 심링크가 없는 실경로를 준다 — `ProviderPath` 가 심링크를 풀든 말든 같은 답이라야
    # 단언이 경로 정책이 아니라 **URL 조립**만 본다.
    d = os.path.realpath(tempfile.mkdtemp(prefix="pytmux-ps1-cwd-"))
    try:
        out = _powershell_emits("Set-Location '%s'; __pytmux_report_cwd" % d)
        pane = _pane()
        pane.feed(_osc("133", "A"))
        pane.feed(out)
        cwd = blocks_wire(pane)[0]["cwd"]
        assert not cwd.startswith("//"), "URL 조립이 슬래시를 겹쳤다: %r" % cwd
        assert cwd == d, cwd
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_a_windows_drive_url_loses_only_the_url_slash():
    """드라이브 경로만 벗긴다 — POSIX 절대경로의 `/` 는 경로의 일부다."""
    seg = Segmenter()
    seg.on_osc("133", "A")
    seg.on_osc("7", "file://host/D:/a/b")
    assert seg.cwd == "D:/a/b"
    seg.on_osc("7", "file://host/home/me/work")
    assert seg.cwd == "/home/me/work", "POSIX 경로에서 루트를 떼면 안 된다"


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


async def test_osc_hot_path_never_scans_the_plugin_directory():
    """OSC 는 `feed` 안에서 처리된다 — **뜨거운 경로**다. `plugins.load()` 는 호출마다
    디렉토리를 스캔하므로 그걸 훅 안에서 부르면 OSC 를 반복 방출하는 프로그램이 그
    스캔을 유발해 **그 자체가 자원 공격**이 된다(N1/F2 와 같은 부류).

    설계는 "서버가 패널 생성 시 한 번 꽂는다"인데 그 계약이 **문서에만** 있었다
    (검수 2026-07-27g). 여기서 못박는다: 패널 하나에 OSC 를 200개 먹여도 레지스트리
    스캔은 0회여야 한다."""
    pane = _pane()                       # 생성 시 훅 1회 장착(서버와 같은 방식)
    calls = []
    orig = plugins.load

    def counting_load(*a, **kw):
        calls.append(1)
        return orig(*a, **kw)

    plugins.load = counting_load
    try:
        for i in range(50):
            pane.feed(_osc("133", "A"))
            pane.feed(_osc("633", f"E;echo {i}"))
            pane.feed(_osc("133", "C"))
            pane.feed(_osc("133", "D;0"))
        assert not calls, f"핫패스에서 플러그인 디렉토리를 {len(calls)}회 스캔했다"
        # 그래도 기능은 동작한다(스캔 0 ≠ 무동작).
        wire = blocks_wire(pane)
        assert wire and wire[-1]["cmd"] == "echo 49", wire
    finally:
        plugins.load = orig


# ── 셸이 늘 때마다 갈릴 자리 — escape 표를 기계로 대조한다 ──────────────────────

def _escape_table(path, pattern):
    """셸 통합 스크립트에서 `<원문자> → <escape>` 쌍을 뽑는다.

    소스를 읽는 이유: 이 표는 **셸마다 다시 적힌다**(sh 는 `${s//…}`, PowerShell 은
    `.Replace(…)`). 실제로 돌려 보는 오라클은 그 셸이 깔린 상자에서만 도는데
    (`powershell 없음` skip 이 이 스위트에 상시 둘 있다), 표가 어긋나는 것은
    **어디서나** 잴 수 있다."""
    import re
    src = open(path, encoding="utf-8").read()
    named = {'"`n"': "\n", '"`r"': "\r",
             "$__pytmux_nl": "\n", "$__pytmux_cr": "\r",
             "$__pytmux_esc": "\x1b", "$__pytmux_bel": "\x07",
             "[string][char]27": "\x1b", "[string][char]7": "\x07",
             "$bs": "\\", r"'\'": "\\", "';'": ";", ";": ";"}
    out = {}
    for src_tok, esc in re.findall(pattern, src):
        tok = src_tok.strip()
        assert tok in named, f"모르는 토큰 {tok!r} — 표가 늘었는데 이 오라클이 낡았다"
        out[named[tok]] = esc.replace("${bs}", "\\")
    return out


async def test_every_shell_escapes_the_same_set_and_the_server_can_undo_it():
    """세 벌(sh · PowerShell · 서버 `_unescape`)이 **한 표**를 보는가.

    셸이 늘면 escape 를 새로 적게 되는데 서버의 되돌리기는 한 벌이다. 한 글자라도
    빠지면 그 글자가 든 명령에서만 조용히 틀리고(BEL 이 정확히 그랬다 — 검수
    2026-07-30), 그 셸이 없는 상자에서는 스위트가 **초록인 채로** 지나간다.
    """
    from pytmuxlib.plugins.blocks.segment import _unescape
    d = os.path.dirname(blocks_pkg.__file__)
    sh = _escape_table(os.path.join(d, "shell-integration.sh"),
                       r's=\$\{s//"([^"]+)"/"([^"]+)"\}')
    ps = _escape_table(os.path.join(d, "shell-integration.ps1"),
                       r"\$s = \$s\.Replace\((.+?),\s*'([^']+)'\)")
    assert sh, "sh 표를 못 읽었다(정규식이 낡았다 — 잴 것이 없으면 고장이다)"
    assert ps, "PowerShell 표를 못 읽었다(정규식이 낡았다)"
    assert set(sh) == set(ps), (
        "두 셸이 다른 글자를 escape 한다: sh=%r ps=%r"
        % (sorted(map(ord, sh)), sorted(map(ord, ps))))
    for raw, esc in sh.items():
        assert ps[raw] == esc, f"{raw!r} 를 sh 는 {esc!r}, ps 는 {ps[raw]!r} 로 쓴다"
        # 서버가 그 escape 를 **정확히** 되돌리는가(escape 한 자리만, 그 글자로).
        assert _unescape(esc) == raw, f"서버가 {esc!r} 를 {raw!r} 로 못 되돌린다"
    # 그리고 그 표로 escape 한 명령줄은 왕복이 항등이라야 한다 — 백슬래시가 뒤
    # 치환이 만든 것과 섞이면 여기서 죽는다(그래서 두 스크립트 다 `\` 를 먼저 한다).
    typed = 'git log; findstr /c:"a b" C:\\path\\to\x07\x1b[0m\n끝'
    s = typed.replace("\\", "\\\\")
    for raw, esc in sh.items():
        if raw != "\\":
            s = s.replace(raw, esc)
    assert _unescape(s) == typed, f"왕복이 항등이 아니다: {_unescape(s)!r}"
