"""실제 화면 스크린샷 하네스 — 진짜 Textual 클라이언트를 가짜 터미널(PTY) 아래
띄워 **사용자가 실제로 보는 화면**(ANSI 프레임)을 캡처한다(§10 사용자 질문 대응).

헤드리스 단언 테스트(`run_test`/`server_only`)는 위젯 상태·합성 셀을 검증하지만,
실제 `client.py` 의 `_composite`/Textual CSS/드라이버 렌더 경로를 통과한 **터미널
출력**은 보지 못한다. 이 하네스는 그 출력을 그대로 잡아 트레이스백·테두리·프롬프트
유무 같은 "눈으로 보는" 회귀를 자동 검증한다. run-pytmux 스킬의 driver.py(서버가
합성한 스크린샷)와 상보적이다 — 이쪽은 진짜 클라 프로세스의 화면이다.

POSIX 전용(stdlib `pty`). Windows 에선 capture 가 RuntimeError 를 던지므로 호출부가
가드한다.

사용 예:
    raw, alive = ptyshot.capture([sys.executable, "pytmux.py", "--socket", sock])
    txt = ptyshot.screen_text(raw)
    assert alive and not ptyshot.has_traceback(raw)

★ **클라가 둘 이상이면 `capture` 가 아니라 `Multi` 다**(pytmux-146 · QA T2).
`capture` 는 한 프로세스를 상한까지 붙들고 있다 돌려주므로 **동시에 붙은 두 클라**를
못 만든다 — 차례로 부르면 그건 재부착이지 동시 접속이 아니고, 이 제품의 정체(단일
서버 · 다중 클라)가 정확히 그 자리에 있다. `Multi` 는 N 개를 함께 띄우고 **호출부가
운전한다**: 조건을 기다리고(`wait`), 그 사이에 조작을 끼우고, 하나를 죽여 보고, 남은
쪽 화면이 계속 자라는지 본다.

    with ptyshot.Multi([argv, argv], env={"PYTMUX_HOME": home}) as m:
        m.wait(lambda ts: all(_drawn(t) for t in ts), timeout=20)
        do_something()                         # ← 붙어 있는 동안 조작한다
        ok = m.wait(lambda ts: all("MARK" in t for t in ts), timeout=20)
"""
from __future__ import annotations

import os
import re
import select
import signal
import time

IS_WINDOWS = os.name == "nt"

# ANSI 제어 시퀀스(CSI/OSC/기타) 제거용 — 화면을 사람이 읽는 평문으로.
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[\]P^_][^\x07\x1b]*(?:\x07|\x1b\\)?"
                   r"|\x1b[=>()][A-Za-z0-9]?")


def capture(argv, *, cols: int = 100, rows: int = 30, seconds: float = 4.0,
            env: dict | None = None, feed: bytes | None = None,
            ready=None, until=None, feed_delay: float = 0.6,
            feed_settle: float = 0.4, feed_retry: float = 2.0):
    """argv 를 PTY(가짜 터미널) 아래 실행하고 출력을 모은 뒤 종료한다.

    반환: (raw_bytes, alive_at_end). alive_at_end=False 면 캡처 시간 안에 프로세스가
    스스로 종료한 것(=즉시 종료/크래시 신호).

    `seconds` 는 **상한(deadline)** 이지 고정 대기가 아니다 — `until` 이 참이 되면
    그 자리에서 끝낸다. 이 저장소가 `wait_until`(harness)로 pilot 쪽 고정 대기를
    걷어낸 것과 같은 규약을 프로세스 하네스에도 적용한 것이다.

    feed/ready/until 규약 (pytmux-141):

    - `ready(txt)` 를 주면 그것이 참이 된 뒤 `feed_settle` 초에 먹인다. 안 주면
      종전대로 `feed_delay` 초에 먹인다.
      ⛔ **먹이는 시점을 고정하면 안 된다.** 클라가 아직 안 떴을 때 쓴 바이트는
      패널 셸에 닿지 않고, tty 가 로컬 에코로 되돌리는 것이 전부다 — 그 에코가
      화면에 남아 단언을 통과시키면 **델타 경로를 한 번도 안 지난 거짓 초록**이 된다.
      반대로 클라가 먼저 raw 모드를 잡으면 에코마저 없어 같은 시험이 붉어진다.
      실측(2026-08-09): 마커 바이트는 언제나 **먹인 그 순간**에 돌아왔다(0.60s 먹임
      → 0.60s 도착) — 렌더가 아니라 에코였다는 뜻이고, 초록/붉음은 클라 기동이
      0.6초보다 느렸나 빨랐나로 갈렸다.
    - `until(txt)` 은 「다 됐다」의 판정이다. 참이 되면 즉시 끝내므로 평상시엔
      상한보다 훨씬 빨리 돌아온다(실측 6.0초 고정 → 약 2.6초).
    - `until` 이 아직 거짓이면 `feed_retry` 초마다 feed 를 **다시** 흘린다. 먹이는
      시점을 조건으로 옮겨도 「셸이 프롬프트를 내기 직전」이라는 창은 남는데,
      그때 잃은 키는 더 기다려도 돌아오지 않는다 — 다시 치는 것이 유일한 복구다.
      (그래서 feed 는 다시 쳐도 해로운 것이 아니어야 한다 — echo 같은 것.)
    """
    if IS_WINDOWS:
        raise RuntimeError("ptyshot.capture 는 POSIX 전용(stdlib pty)")
    pid, fd = _spawn(argv, cols=cols, rows=rows, env=child_env(env))
    buf = bytearray()
    t0 = time.time()
    alive = True
    fed_at = None if feed is not None else -1.0   # -1 = 먹일 것이 없다
    ready_at = None
    while time.time() - t0 < seconds:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            buf += data
        now = time.time() - t0
        txt = screen_text(bytes(buf)) if (ready or until) else ""
        if until and fed_at is not None and until(txt):
            break
        if fed_at is None:                        # 아직 안 먹였다
            if ready is None:
                due = now >= feed_delay
            else:
                if ready_at is None and ready(txt):
                    ready_at = now
                due = ready_at is not None and now >= ready_at + feed_settle
            if due:
                _write(fd, feed)
                fed_at = now
        elif (feed_retry and fed_at >= 0 and until
                and now >= fed_at + feed_retry):  # 조건 미충족 → 다시 친다
            _write(fd, feed)
            fed_at = now
        wpid, _ = os.waitpid(pid, os.WNOHANG)
        if wpid:
            alive = False
            break
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        os.waitpid(pid, 0)
    except OSError:
        pass
    return bytes(buf), alive


def _write(fd, data):
    """PTY 로 키 입력을 흘린다 — 상대가 아직 안 읽어도 여기서 죽지 않는다."""
    try:
        os.write(fd, data)
    except OSError:
        pass


def child_env(env: dict | None = None) -> dict:
    """PTY 자식이 물려받을 환경. `capture` 와 `Multi` 가 **같은 것**을 쓴다.

    ⛔ 두 벌로 적지 않는다 — 갈리면 한쪽에서만 재현되는 화면이 생기고, 그때 의심하는
    자리는 제품이지 하네스가 아니다(찾는 데 하루가 든다).
    """
    e = dict(os.environ)
    e.pop("PYTMUX", None)
    e.pop("LC_PYTMUX", None)
    # §1.7: 개발 셸이 ssh 세션이어도 캡처 자식은 "맨 로컬 터미널" 로 흉내낸다 —
    # SSH_* 가 새면 attach 의 in-band 중첩 프로브(XTVERSION)가 발화해 캡처마다
    # 0.4초 대기 + 질의 바이트가 끼어든다.
    e.pop("SSH_CONNECTION", None)
    e.pop("SSH_TTY", None)
    e["TERM"] = "xterm-256color"
    if env:
        e.update(env)
    return e


def _spawn(argv, *, cols: int, rows: int, env: dict):
    """PTY 하나를 파고 그 아래로 `argv` 를 띄운다. 반환 `(pid, master_fd)`.

    ⚠ 마스터 fd 는 **상속하지 않게** 막는다(`set_inheritable(False)` = CLOEXEC).
    안 막으면 나중에 뜨는 자식이 앞선 형제의 마스터를 물고 있게 되고, 정리 순서가
    꼬이면 그 fd 가 남아 EOF 판정이 흔들린다. `Multi` 가 N 개를 차례로 파므로 이
    한 줄이 없으면 N² 개의 상속이 생긴다.
    """
    import fcntl
    import pty
    import struct
    import termios

    pid, fd = pty.fork()
    if pid == 0:                      # 자식: 클라이언트로 exec
        # ⛔ **exec 가 실패해도 여기서 예외를 올리지 않는다.** 올리면 그 예외를 받는
        #    것이 부모가 아니라 **자식 안의 파이썬**이라, 자식이 부모의 나머지 코드를
        #    이어서 돌린다(하네스가 통째로 두 벌이 된다). `os._exit` 로 끊는다.
        try:
            os.execvpe(argv[0], list(argv), env)
        except BaseException:         # noqa: BLE001 — 여기서 살아 나가면 안 된다
            os._exit(127)
    os.set_inheritable(fd, False)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    return pid, fd


class Multi:
    """실 프로세스 **여럿**을 각자 PTY 아래 **동시에** 띄우고 함께 본다(pytmux-146).

    `capture` 와 가르는 선은 하나다 — 저쪽은 상한까지 붙들고 있다가 결과를 돌려주고,
    이쪽은 **호출부가 운전한다.** 그래서 「둘 다 떴다」와 「그 사이에 조작했다」와
    「한쪽을 죽였다」가 한 캡처 안에서 순서대로 일어날 수 있다. 다중 클라의 계약
    (한쪽 조작이 다른 쪽 화면에 반영된다 · 하나가 죽어도 나머지가 산다)은 그 순서가
    성립해야만 잴 수 있다.

    ⚠ `text(i)` 는 **그 자식이 지금까지 낸 전부**다(현재 화면이 아니다). 그래서
    판정은 「마커가 보이나」로 하고, 단계마다 **다른 마커**를 쓴다 — 같은 마커를 두
    번 쓰면 앞 단계의 흔적이 뒤 단계를 통과시킨다(거짓 초록).
    """

    def __init__(self, argvs, *, cols: int = 100, rows: int = 30,
                 env: dict | None = None):
        if IS_WINDOWS:
            raise RuntimeError("ptyshot.Multi 는 POSIX 전용(stdlib pty)")
        if not argvs:
            raise ValueError("붙일 것이 없다 — 빈 목록은 통과가 아니라 고장이다")
        e = child_env(env)
        self.pids: list[int] = []
        self.fds: list[int] = []
        self._buf: list[bytearray] = []
        self._open: list[bool] = []       # 마스터 fd 가 아직 읽히나
        self._exited: list[bool] = []     # 자식이 **스스로** 끝났나
        self._killed: list[bool] = []     # 우리가 죽였나
        # ⚠ 도중에 실패하면 **먼저 판 것을 회수하고** 던진다 — 안 그러면 반쯤 뜬
        #   클라들이 남아 다음 런의 슬롯 판정(`residue`)을 흔든다.
        try:
            for argv in argvs:
                pid, fd = _spawn(argv, cols=cols, rows=rows, env=e)
                self.pids.append(pid)
                self.fds.append(fd)
                self._buf.append(bytearray())
                self._open.append(True)
                self._exited.append(False)
                self._killed.append(False)
        except BaseException:
            self.close()
            raise

    # ── 컨텍스트 ─────────────────────────────────────────────────────────────
    def __enter__(self) -> "Multi":
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __len__(self) -> int:
        return len(self.pids)

    # ── 관찰 ─────────────────────────────────────────────────────────────────
    def pump(self, seconds: float = 0.1) -> None:
        """지금 읽을 수 있는 것을 읽는다. 살아 있는 fd 가 없으면 그냥 쉰다."""
        fds = [fd for fd, ok in zip(self.fds, self._open) if ok]
        if not fds:
            time.sleep(seconds)
            return
        r, _, _ = select.select(fds, [], [], seconds)
        for fd in r:
            i = self.fds.index(fd)
            try:
                data = os.read(fd, 65536)
            except OSError:               # 슬레이브가 전부 닫혔다(EIO)
                data = b""
            if not data:
                self._open[i] = False
                continue
            self._buf[i] += data
        self._reap()

    def _reap(self) -> None:
        """자식이 **스스로** 끝났는지 본다. 우리가 죽인 것과 구분해서 적는다 —
        그 구분이 곧 `client/alive` 판정이다."""
        for i, pid in enumerate(self.pids):
            if self._exited[i]:
                continue
            try:
                wpid, _ = os.waitpid(pid, os.WNOHANG)
            except OSError:
                wpid = pid
            if wpid:
                self._exited[i] = True

    def raw(self, i: int) -> bytes:
        return bytes(self._buf[i])

    def text(self, i: int) -> str:
        return screen_text(bytes(self._buf[i]))

    def texts(self) -> list[str]:
        return [self.text(i) for i in range(len(self.pids))]

    def alive(self, i: int) -> bool:
        """그 자식이 **스스로** 끝나지 않았나. 우리가 죽인 것은 `False` 다(사실이다)."""
        self._reap()
        return not self._exited[i]

    def wait(self, pred, timeout: float, step: float = 0.05) -> bool:
        """`pred(texts)` 가 참이 될 때까지 읽는다. 상한을 넘기면 `False`.

        ⛔ **상한을 못 지킨 것을 조용히 통과시키지 않는다** — 돌려주는 값이 판정의
        재료이고, 호출부는 그 `False` 를 결함으로 적는다(원칙 ⓑ).
        """
        end = time.time() + timeout
        while True:
            self.pump(step)
            if pred(self.texts()):
                return True
            if time.time() >= end:
                return False

    # ── 조작 ─────────────────────────────────────────────────────────────────
    def feed(self, i: int, data: bytes) -> None:
        _write(self.fds[i], data)

    def kill(self, i: int) -> None:
        """하나만 죽인다 — **우리가 쥔 pid 로만**(이름 매칭 금지 규율은 여기도 같다)."""
        self._killed[i] = True
        try:
            os.kill(self.pids[i], signal.SIGKILL)
        except OSError:
            pass

    def killed(self, i: int) -> bool:
        return self._killed[i]

    def close(self) -> None:
        """전부 회수한다. 이미 죽은 것에 두 번 걸어도 조용히 지나간다.

        ⚠ 목록 길이를 **믿지 않는다** — 기동이 도중에 깨지면 `pids` 만 늘어난 상태로
        여기 들어올 수 있고, 그때 인덱스를 그냥 쓰면 정리가 예외로 멈춘다(그래서 남는다).
        """
        for i, pid in enumerate(self.pids):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
            try:
                os.waitpid(pid, 0)
            except OSError:
                pass
            if i < len(self._exited):
                self._exited[i] = True
            if i < len(self.fds):
                try:
                    os.close(self.fds[i])
                except OSError:
                    pass
            if i < len(self._open):
                self._open[i] = False


def screen_text(raw: bytes) -> str:
    """캡처한 raw 바이트에서 ANSI 이스케이프를 제거해 화면 평문으로."""
    return _ANSI.sub("", raw.decode("utf-8", "replace"))


def has_traceback(raw: bytes) -> bool:
    """Textual 이 크래시 시 터미널에 토해내는 파이썬 트레이스백이 보이는지."""
    return "Traceback (most recent call last)" in raw.decode("utf-8", "replace")
