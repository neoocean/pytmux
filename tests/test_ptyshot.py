"""실제 화면 스크린샷 하네스(ptyshot) 자체 + 그걸로 진짜 클라이언트를 본 시각 회귀.

핵심: 실제 pytmux 클라이언트를 PTY 아래 띄워 ① 즉시 종료(크래시)하지 않고 ② 트레이스백
없이 ③ 상태줄/테두리를 그리는지 — '눈으로 보는' 화면을 캡처해 단언한다. 부팅 시
layout.json 자동 복원 경로(과거 Session.popup 누락 크래시, CL 56607)도 이 경로로
지나가므로 회귀로서 가치가 크다(§10)."""
import os
import sys
import tempfile

import harness  # noqa: F401  (경로 설정)
import ptyshot


def _entry():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "pytmux.py")


async def test_ansi_strip_and_traceback_detect():
    # 순수 부분: ANSI 제거 + 트레이스백 감지(외부 프로세스 없이 빠르게).
    raw = b"\x1b[1;31mhello\x1b[0m \x1b[2J world\x1b]0;title\x07!"
    assert ptyshot.screen_text(raw) == "hello  world!"
    assert not ptyshot.has_traceback(raw)
    assert ptyshot.has_traceback(b"x\nTraceback (most recent call last):\n  ...")


async def test_real_client_renders_no_crash():
    """실제 클라이언트가 PTY 아래서 렌더되고 살아있으며 트레이스백이 없는지."""
    if ptyshot.IS_WINDOWS:
        return  # POSIX 전용 하네스(stdlib pty)
    sock = tempfile.mktemp(suffix=".sock")
    try:
        raw, alive = ptyshot.capture(
            [sys.executable, _entry(), "--socket", sock], seconds=4.0)
        txt = ptyshot.screen_text(raw)
        assert alive, "클라가 캡처 시간 안에 스스로 종료(즉시 종료/크래시 신호)"
        assert not ptyshot.has_traceback(raw), txt[-1500:]
        # 상태줄(시계/날짜/[+] 탭) 또는 패널 테두리가 그려졌는지
        assert any(c in txt for c in "┌─│┐└┘") or "[+]" in txt, \
            "테두리/탭바가 렌더되지 않음:\n" + txt[-800:]
    finally:
        # 이 소켓에 띄워진 데몬을 정리(테스트 격리).
        from pytmuxlib import launcher
        try:
            launcher.control_request(sock, {"t": "kill-server"})
        except Exception:
            pass


async def test_real_client_delta_render():
    """입력으로 유발한 화면 델타가 실제 클라 렌더 경로로 정확히 그려지는지(B8 회귀).

    B8: set_frame 이 직전 프레임과 행 단위 비교해 변경된 행만 region refresh 한다 —
    초기 전체 렌더 뒤 1줄 델타에도 전 화면 render_line 을 돌리지 않는다. 부분 refresh
    가 깨지면 새 출력이 안 보이거나 stale 행이 남는다. PTY 아래 진짜 클라를 띄워
    echo 명령을 흘려보내고(0.6초 뒤 feed), 그 고유 마커 출력이 화면에 나타나는지
    단언한다 — 델타 경로 end-to-end 검증."""
    if ptyshot.IS_WINDOWS:
        return  # POSIX 전용 하네스
    marker = "PYTMUX_B8_DELTA_OK"
    sock = tempfile.mktemp(suffix=".sock")
    try:
        raw, alive = ptyshot.capture(
            [sys.executable, _entry(), "--socket", sock],
            seconds=6.0, feed=("echo " + marker + "\n").encode())
        txt = ptyshot.screen_text(raw)
        assert alive, "클라가 캡처 시간 안에 종료(크래시)"
        assert not ptyshot.has_traceback(raw), txt[-1500:]
        # 입력으로 유발된 델타(echo 출력)가 부분 refresh 후 화면에 보여야 한다.
        assert marker in txt, "델타(echo 출력)가 렌더되지 않음:\n" + txt[-800:]
        # 테두리도 그대로(부분 refresh 가 테두리 행을 망치지 않음).
        assert any(c in txt for c in "┌─│┐└┘"), "테두리 손상:\n" + txt[-800:]
    finally:
        from pytmuxlib import launcher
        try:
            launcher.control_request(sock, {"t": "kill-server"})
        except Exception:
            pass
