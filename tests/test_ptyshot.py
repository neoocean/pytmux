"""실제 화면 스크린샷 하네스(ptyshot) 자체 + 그걸로 진짜 클라이언트를 본 시각 회귀.

핵심: 실제 pytmux 클라이언트를 PTY 아래 띄워 ① 즉시 종료(크래시)하지 않고 ② 트레이스백
없이 ③ 상태줄/테두리를 그리는지 — '눈으로 보는' 화면을 캡처해 단언한다. 부팅 시
layout.json 자동 복원 경로(과거 Session.popup 누락 크래시, CL 56607)도 이 경로로
지나가므로 회귀로서 가치가 크다(§10).

2026-07-25(§10-3⑤ 마무리): 이 경로는 **진짜 데몬**을 띄우므로, 클라 화면의 트레이스백
뿐 아니라 **서버가 로그로만 삼킨 예외**(`<sock>.error.log`)까지 함께 단언한다 —
서버는 stderr 가 /dev/null 이라 화면만 보면 조용한 실패를 놓친다."""
import os
import shutil
import sys
import tempfile
import time

import harness
import ptyshot

# 화면이 떴다고 볼 표식(테두리 또는 탭바) — 먹이기 전에 이것을 기다린다.
_DRAWN = lambda txt: any(c in txt for c in "┌─│┐└┘") or "[+]" in txt


def _entry():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "pytmux.py")


def _scratch_sock():
    """소켓을 **자기 디렉터리 안에** 잡는다. (dir, sock) 반환.

    `ServerPersistMixin.layout_path` 는 `dirname(ipc.state_base(sock))/layout.json`
    이라, 소켓을 `tempfile.mktemp()` 로 `$TMPDIR` 바로 밑에 두면 **tmp 소켓을 쓰는
    모든 시험 서버가 layout.json 한 장을 공유**한다. 그러면 앞서 돈(또는 지금 같이
    도는) 시험 서버가 남긴 레이아웃이 여기 데몬에서 자동 복원돼 창·활성 패널이
    통째로 달라진다 — 실측하면 탭바가 `1:win 2:win 3:zsh` 로 찍힌다(pytmux-141
    조사 2026-08-05 · 재현 2026-08-09).

    디렉터리를 따로 파면 그 공유가 **구조적으로** 없어진다. 어느 시험이 그 파일을
    남기는지 세어서 막는 길도 있지만, 그러면 새로 생기는 시험마다 다시 세어야 한다.
    """
    d = tempfile.mkdtemp(prefix="ptyshot-")
    return d, os.path.join(d, "c.sock")


def _cleanup(d, sock):
    """이 소켓에 띄워진 데몬을 내리고 스크래치를 지운다(테스트 격리)."""
    from pytmuxlib import launcher
    try:
        launcher.control_request(sock, {"t": "kill-server"})
    except Exception:
        pass
    shutil.rmtree(d, ignore_errors=True)


async def test_ansi_strip_and_traceback_detect():
    # 순수 부분: ANSI 제거 + 트레이스백 감지(외부 프로세스 없이 빠르게).
    raw = b"\x1b[1;31mhello\x1b[0m \x1b[2J world\x1b]0;title\x07!"
    assert ptyshot.screen_text(raw) == "hello  world!"
    assert not ptyshot.has_traceback(raw)
    assert ptyshot.has_traceback(b"x\nTraceback (most recent call last):\n  ...")


async def test_capture_feeds_after_ready_and_stops_at_until():
    """하네스 자체의 규약을 못 박는다 — ⑴ ready 전에는 안 먹인다 ⑵ until 이면 상한
    전에 끝낸다.

    pytmux-141 의 거짓 초록은 «상대가 뜨기도 전에 먹여 놓고, tty 가 되돌린 로컬
    에코를 렌더로 착각한 것»이었다. 그 부류는 눈에 잘 안 띄므로(시험은 초록이고
    가끔 붉을 뿐이다) 규약을 여기서 **진짜 클라 없이 빠르게** 잰다."""
    if ptyshot.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 하네스(stdlib pty)")
    # 1초 뒤에야 뜨고, 그 뒤 한 줄을 읽는 자식(= 늦게 뜨는 클라의 축소판).
    child = ("import sys,time\n"
             "time.sleep(1.0)\n"
             "sys.stdout.write('READY-MARK\\n'); sys.stdout.flush()\n"
             "sys.stdin.readline()\n"
             "sys.stdout.write('DONE-MARK\\n'); sys.stdout.flush()\n"
             "time.sleep(30)\n")
    t0 = time.time()
    raw, alive = ptyshot.capture(
        [sys.executable, "-c", child], seconds=20.0, feed=b"ping\n",
        ready=lambda t: "READY-MARK" in t, until=lambda t: "DONE-MARK" in t,
        feed_delay=0.1, feed_settle=0.05)
    elapsed = time.time() - t0
    txt = ptyshot.screen_text(raw)
    assert alive, "자식이 스스로 끝났다"
    assert "DONE-MARK" in txt, "먹인 줄이 자식에게 닿지 않았다:\n" + txt[-400:]
    # ⑴ 먹은 흔적(tty 에코 'ping')은 READY-MARK **뒤**에 있어야 한다. feed_delay
    #    (0.1초)를 따랐다면 자식이 뜨는 1.0초보다 앞에 찍혔을 것이다.
    assert "ping" in txt, "tty 에코가 없어 순서를 잴 수 없다:\n" + txt[-400:]
    assert txt.index("READY-MARK") < txt.index("ping"), \
        "ready 전에 먹였다(고정 대기로 되돌아갔다):\n" + txt[:400]
    # ⑵ 상한(20초)이 아니라 조건에서 끝났다.
    assert elapsed < 10.0, f"until 로 조기 종료하지 않았다({elapsed:.1f}s)"


#: `Multi` 시험용 아이 — 뜨자마자 표식을 내고, 그 뒤로는 받은 줄을 되돌려 준다.
#: ⚠ 되돌리는 글자를 **받은 글자와 다르게**(`ECHO:` 접두) 만든다 — 같으면 pty 로컬
#:   에코만으로 통과해 「살아서 응답한다」를 못 잰다(pytmux-141 이 겪은 그 함정).
_MULTI_CHILD = (
    "import sys\n"
    "sys.stdout.write('UP-MARK\\n'); sys.stdout.flush()\n"
    "while True:\n"
    "    line = sys.stdin.readline()\n"
    "    if not line:\n"
    "        break\n"
    "    sys.stdout.write('ECHO:' + line); sys.stdout.flush()\n"
)


async def test_multi_holds_several_children_up_at_the_same_time():
    """`Multi` 의 계약 — N 개가 **같은 시각에** 서 있고, 하나를 죽여도 나머지가 산다.

    ⛔ `capture` 를 두 번 부르는 것으로는 이 계약을 못 잰다 — 저쪽은 한 프로세스를
    상한까지 붙들고 있다 돌려주므로 둘이 겹쳐 서는 순간이 없다. pytmux 는 단일 서버 ·
    다중 클라라 「동시에 붙어 있는 동안」에만 성립하는 계약이 있고, QA T2
    (`qa/scenarios/t2_multi_client.py`)가 통째로 이 클래스 위에 선다(pytmux-146).

    ⚠ 여기서는 **진짜 클라를 안 띄운다** — 재는 것은 하네스이지 제품이 아니다.
    """
    if ptyshot.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 하네스(stdlib pty)")
    argv = [sys.executable, "-c", _MULTI_CHILD]
    with ptyshot.Multi([argv, argv, argv]) as m:
        assert len(m) == 3
        assert m.wait(lambda ts: all("UP-MARK" in t for t in ts), timeout=20), \
            "셋이 다 뜨지 않았다:\n" + "\n--\n".join(t[-200:] for t in m.texts())
        # ★ 이 한 줄이 「동시에」다 — 셋이 같은 시각에 살아 있다.
        assert all(m.alive(i) for i in range(3)), [m.alive(i) for i in range(3)]

        m.feed(1, b"ping\n")
        assert m.wait(lambda ts: "ECHO:ping" in ts[1], timeout=20), m.text(1)[-300:]
        assert "ECHO:ping" not in m.text(0) and "ECHO:ping" not in m.text(2), \
            "먹인 것이 남의 자식에게 갔다 — fd 가 섞였다"

        m.kill(0)
        assert m.wait(lambda _ts: not m.alive(0), timeout=10), "죽인 자식이 안 죽었다"
        assert m.killed(0) and not m.killed(1), "우리가 죽인 것과 스스로 죽은 것이 섞였다"
        # 하나가 죽어도 나머지는 **계속 응답한다**(살아만 있는 것과 다른 사건이다).
        m.feed(2, b"pong\n")
        assert m.wait(lambda ts: "ECHO:pong" in ts[2], timeout=20), m.text(2)[-300:]
        assert m.alive(1) and m.alive(2), "형제가 죽자 나머지가 함께 죽었다"


async def test_multi_refuses_an_empty_roster():
    """⛔ 0 개를 조용히 받아들이면 「전부 초록」과 「아무도 안 붙었다」가 같은 모양이 된다."""
    if ptyshot.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 하네스(stdlib pty)")
    try:
        ptyshot.Multi([])
    except ValueError:
        return
    raise AssertionError("빈 목록을 받아들였다 — 빈 런은 통과가 아니라 고장이다")


async def test_real_client_renders_no_crash():
    """실제 클라이언트가 PTY 아래서 렌더되고 살아있으며 트레이스백이 없는지."""
    if ptyshot.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 하네스(stdlib pty)")
    d, sock = _scratch_sock()
    try:
        # ⚠ 여기는 `until` 로 조기 종료하지 **않는다**. 이 시험의 절반은 「4초 동안
        #   살아 있나 · 그 사이 트레이스백을 안 토하나」라, 테두리를 보자마자 끊으면
        #   늦게 터지는 크래시를 못 본다 — 고정 창이 곧 이 시험의 계측기다.
        raw, alive = ptyshot.capture(
            [sys.executable, _entry(), "--socket", sock], seconds=4.0)
        txt = ptyshot.screen_text(raw)
        assert alive, "클라가 캡처 시간 안에 스스로 종료(즉시 종료/크래시 신호)"
        assert not ptyshot.has_traceback(raw), txt[-1500:]
        # 상태줄(시계/날짜/[+] 탭) 또는 패널 테두리가 그려졌는지
        assert _DRAWN(txt), "테두리/탭바가 렌더되지 않음:\n" + txt[-800:]
        # 실 데몬이 조용히 예외를 삼켰는지도 본다(화면만으로는 안 보인다).
        harness.assert_no_server_errors(sock)
    finally:
        _cleanup(d, sock)


async def test_real_client_delta_render():
    """입력으로 유발한 화면 델타가 실제 클라 렌더 경로로 정확히 그려지는지(B8 회귀).

    B8: set_frame 이 직전 프레임과 행 단위 비교해 변경된 행만 region refresh 한다 —
    초기 전체 렌더 뒤 1줄 델타에도 전 화면 render_line 을 돌리지 않는다. 부분 refresh
    가 깨지면 새 출력이 안 보이거나 stale 행이 남는다. PTY 아래 진짜 클라를 띄워
    echo 명령을 흘려보내고, 그 고유 마커 **출력**이 화면에 나타나는지 단언한다 —
    델타 경로 end-to-end 검증.

    ⛔ **타이핑하는 글자와 단언하는 글자가 달라야 한다**(pytmux-141 · 2026-08-09).
    종전에는 `echo PYTMUX_B8_DELTA_OK` 를 치고 화면에서 `PYTMUX_B8_DELTA_OK` 를
    찾았는데, 그러면 셸이 그 명령을 **한 번도 실행하지 않아도** 통과한다 — 클라가
    아직 raw 모드를 잡기 전이면 tty 가 타이핑한 바이트를 로컬 에코로 되돌리고,
    그 글자가 곧 마커이기 때문이다. 실측하면 마커는 늘 **먹인 그 순간**에
    도착했다(0.60초에 먹여 0.60초 도착 · 렌더가 아니라 에코). 그래서 이 시험의
    초록/붉음은 델타 경로가 아니라 **클라 기동이 0.6초보다 느렸나 빨랐나**로
    갈렸고, 부하가 걸린 러너에서 4회 붉었다. 따옴표를 끼워 타이핑(`PYT""MUX…`)과
    출력(`PYTMUX…`)을 가르면 **출력만이** 단언을 통과시킨다."""
    if ptyshot.IS_WINDOWS:
        from run import skip
        skip("POSIX 전용 하네스(stdlib pty)")
    marker = "PYTMUX_B8_DELTA_OK"
    typed = 'echo PYT""MUX_B8_DELTA_OK\n'      # 셸이 따옴표를 지워 marker 를 낸다
    assert marker not in typed, "타이핑이 마커를 품으면 에코만으로 통과한다"
    d, sock = _scratch_sock()
    try:
        # 화면이 뜬 뒤에 먹이고(고정 0.6초 아님), 출력이 보이면 그 자리에서 끝낸다.
        raw, alive = ptyshot.capture(
            [sys.executable, _entry(), "--socket", sock], seconds=25.0,
            feed=typed.encode(), ready=_DRAWN, until=lambda t: marker in t)
        txt = ptyshot.screen_text(raw)
        assert alive, "클라가 캡처 시간 안에 종료(크래시)"
        assert not ptyshot.has_traceback(raw), txt[-1500:]
        # 입력으로 유발된 델타(echo 출력)가 부분 refresh 후 화면에 보여야 한다.
        assert marker in txt, "델타(echo 출력)가 렌더되지 않음:\n" + txt[-800:]
        # 테두리도 그대로(부분 refresh 가 테두리 행을 망치지 않음).
        assert any(c in txt for c in "┌─│┐└┘"), "테두리 손상:\n" + txt[-800:]
        harness.assert_no_server_errors(sock)
    finally:
        _cleanup(d, sock)


def test_capture_never_calls_a_dead_child_alive():
    """⛔ **`alive` 는 짐작이 아니라 `waitpid` 다**(pytmux-425·426·427).

    소리 없이 죽는 자식은 pty 마스터에 EOF 를 내는데, 종전에는 그 자리에서 그냥
    빠져나와 `waitpid` 를 한 번도 안 지났다 — 그래서 0.03초에 죽은 자식이
    `alive=True · 화면 0자` 로 돌아왔고, 그것을 받은 QA 는 「살아 있는데 아무것도
    안 그렸다」(S2)로 신고했다. **맞는 판정인 「스스로 종료했다」(S1)에는 영영 못 닿는다.**
    ⚠ 그 셋을 가르는 것이 이 시험이므로 **양쪽을 다 잰다** — 죽은 것은 False,
    살아서 안 그리는 것은 True 여야 한다(뒤엣것까지 False 면 오라클이 통째로 뒤집힌다).
    """
    import sys

    raw, alive = ptyshot.capture([sys.executable, "-c", "raise SystemExit(0)"], seconds=6.0)
    assert raw == b"", f"출력이 없어야 하는 자식이 뭔가 냈다: {raw[:80]!r}"
    assert alive is False, "출력 없이 죽은 자식을 살아 있다고 보고했다"

    raw, alive = ptyshot.capture(
        [sys.executable, "-c", "import os; os.close(1); os.close(2); raise SystemExit(3)"],
        seconds=6.0)
    assert alive is False, "출력 없이 크래시한 자식을 살아 있다고 보고했다"

    raw, alive = ptyshot.capture([sys.executable, "-c", "import time; time.sleep(30)"],
                                 seconds=1.0)
    assert alive is True, "상한까지 살아 있던 자식을 죽었다고 보고했다 — 오라클이 뒤집혔다"


def test_capture_stops_as_soon_as_until_is_true():
    """`seconds` 는 상한이지 기다림이 아니다 — QA 가 그 차이로 오신고를 냈다.

    `until` 을 안 주면 상한을 통째로 쉬고, 그때 화면에 있는 것이 판정 재료가 된다.
    """
    import sys, time

    argv = [sys.executable, "-c",
            "import sys, time; sys.stdout.write('READY'); sys.stdout.flush(); time.sleep(30)"]
    t0 = time.time()
    raw, alive = ptyshot.capture(argv, seconds=10.0, until=lambda t: "READY" in t)
    dt = time.time() - t0
    assert "READY" in ptyshot.screen_text(raw), "조건이 참인데 화면에 그 글자가 없다"
    assert alive is True and dt < 5.0, f"조건이 참이 됐는데 상한까지 쉬었다: {dt:.1f}초"
