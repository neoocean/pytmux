"""테스트 공용 하니스: 서버 기동 / 클라이언트(headless) / 정리 헬퍼.

화면 없이 동작을 검증하기 위한 도구. 각 테스트는 자체 asyncio 루프(asyncio.run)
에서 실행되며, 서버를 띄우고 PTY 패널을 만든 뒤 텍스트로 결과를 확인한다.
"""
import asyncio
import contextlib
import inspect
import os
import signal
import sys
import tempfile

# 상위 디렉토리(pytmux 패키지/진입점)를 import 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytmux  # noqa: E402
from pytmuxlib import ipc  # noqa: E402

IS_WINDOWS = os.name == "nt"

# ── 셸 히스토리 격리(테스트 잔재가 사용자 히스토리를 오염시키지 않게) ──────────────
# 테스트/벤치는 진짜 자식 셸을 띄워 `echo PY=$$`(test_restart)·`echo PYTMUX_B8_DELTA_OK`
# (test_ptyshot) 같은 프로브를 흘려보낸다. 패널 셸은 serverpty 에서 `env=dict(os.environ)`
# 로 사용자 환경을 상속하므로, 그 zsh 가 명령을 **공유 `~/.zsh_history`** 에 append 해
# pytmux 를 나온 뒤 ↑(히스토리 호출)에 테스트 프로브가 떠오른다. macOS `/etc/zshrc` 는
# `HISTFILE=${ZDOTDIR:-$HOME}/.zsh_history` 로 잡으므로, `ZDOTDIR` 를 빈 임시 디렉터리로
# 돌리면 히스토리가 그 안에 갇혀 사용자 파일을 건드리지 않는다(+ HISTFILE/SAVEHIST 도
# 비워 bash 등 다른 셸을 커버). 모듈 import 시 **1회** 설정 → 이 프로세스가 spawn 하는
# 서버/클라/셸(test_ptyshot 가 띄우는 별도 프로세스 포함, os.environ 상속)이 전부 격리된다.
# 프로덕션(실사용 pytmux 데몬)은 이 모듈을 import 하지 않으므로 패널 히스토리 공유를 유지한다.
_HIST_ISOLATE_DIR = tempfile.mkdtemp(prefix="pytmux-test-zdot-")
os.environ["ZDOTDIR"] = _HIST_ISOLATE_DIR
os.environ["HISTFILE"] = os.devnull
os.environ["SAVEHIST"] = "0"


async def server_only():
    """서버를 기동하고 listen 이 뜰 때까지 대기. (srv, task, endpoint) 반환.

    Unix: 임시 `.sock`(AF_UNIX). Windows: asyncio 의 AF_UNIX 지원이 불완전해
    `ipc` 가 TCP 루프백으로 분기하므로 여기서도 TCP 에페메럴(포트 0)을 쓴다.
    TCP 는 상태파일 prefix(`ipc.state_base`)·포트파일이 고정 경로
    (`default_state_dir/default`)라 테스트 간 충돌하므로, 매 테스트마다 유니크한
    상태 디렉터리를 `LOCALAPPDATA` 로 주입해 격리한다.
    반환값은 **확정 엔드포인트**(TCP 면 실제 포트)라 클라이언트가 그대로 접속한다.
    """
    if IS_WINDOWS:
        os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="pytmux-test-")
        endpoint = "tcp:127.0.0.1:0"
    else:
        endpoint = tempfile.mktemp(suffix=".sock")
    # 캡처(REC) 출력 격리: 테스트 엔드포인트 "tcp:127.0.0.1:0" 는 default_endpoint()
    # 와 같아 server.capture_dir 가 **공유 프로젝트 captures/default** 를 가리킨다.
    # 그러면 실사용 pytmux 데몬이 같은 파일을 캡처 중일 때 test_capture_output 이 그
    # 17MB 짜리 실제 세션 로그를 읽어 깨진다(테스트 격리 결함). PYTMUX_CAPTURE_DIR 를
    # 매 서버마다 유니크 임시 디렉터리로 주입해 캡처를 격리한다(capture_dir 가 이
    # override 를 우선한다). 실사용 captures/ 오염도 막는다.
    os.environ["PYTMUX_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="pytmux-cap-")
    # 토큰 SQLite DB 격리: 기본적으로 server.tokens_db_path 는 공유 프로젝트
    # db/claude-tokens.db 를 가리킨다. 매 서버마다 유니크 임시 파일로 주입해
    # 실사용 DB 오염·테스트 간섭을 막는다(tokens_db_path 가 이 override 를 우선).
    os.environ["PYTMUX_TOKENS_DB"] = tempfile.mktemp(suffix=".tokens.db",
                                                     prefix="pytmux-db-")
    # PTY host 모드(옵션 C)는 Windows 기본 ON이라, 그냥 두면 serve()가 매 테스트마다
    # detached pty-host 서브프로세스를 띄우고 모든 패널을 그 경유로 라우팅한다(Windows
    # 스위트가 인프로세스 PTY 가정과 어긋나 깨짐). 표준 server_only 는 host 모드를 강제
    # OFF 해 결정론적 인프로세스 백엔드를 쓴다 — host 모드 자체는 전용 테스트(test_ptyhost*)
    # 가 인프로세스 host 를 주입해 검증한다. 프로덕션 run_server 기본값(Windows ON)은 무변경.
    os.environ["PYTMUX_PTY_HOST"] = "0"
    srv = pytmux.Server(endpoint)
    task = asyncio.create_task(srv.serve())
    # listen 준비 신호: Unix=소켓 파일 생성, TCP=resolved_endpoint 가 실제 포트로 확정.
    for _ in range(300):
        if ipc.is_tcp(endpoint):
            re = srv.resolved_endpoint
            if ipc.is_tcp(re) and not re.endswith(":0"):
                break
        elif os.path.exists(endpoint):
            break
        await asyncio.sleep(0.01)
    return srv, task, srv.resolved_endpoint


def _killpg_not_self(child_pid):
    """자식 프로세스 그룹을 SIGKILL 하되 **내 그룹이면 절대 안 쏜다**(자살 방지).

    종전 코드는 `os.killpg(os.getpgid(p.child_pid), SIGKILL)` 였다. 여기엔 러너를
    통째로 죽이는 함정이 둘 있다:
      · `child_pid` 가 0 이면 `os.getpgid(0)` = **내 프로세스 그룹**이다(POSIX 규약:
        0=호출자). 그대로 killpg 하면 러너와 **부모 셸까지** SIGKILL 된다.
      · 자식이 setsid 를 안 했거나(=부모 그룹 상속) pid 가 이미 거둬져 재사용됐으면
        역시 내 그룹이 나온다.
    SIGKILL 이라 트레이스백도 종료 메시지도 없이 프로세스가 사라진다 — 실제로
    "출력 없이 exit 1 로 러너가 죽는" 미해결 증상의 후보다. 그래서 대상 pgid 를
    내 것과 대조하고, 같으면 쏘지 않고 진단만 남긴다(PYTMUX_TEST_SELFKILL_LOG).
    """
    if not isinstance(child_pid, int) or child_pid <= 0:
        return                       # 0=내 그룹, 음수=렌더 전용(-1) — 둘 다 대상 아님
    pgid = os.getpgid(child_pid)
    if pgid == os.getpgid(0):
        path = os.environ.get("PYTMUX_TEST_SELFKILL_LOG")
        msg = (f"[selfkill-guard] child_pid={child_pid} 의 pgid={pgid} 가 러너 자신의 "
               f"그룹이라 SIGKILL 을 취소했다(pid={os.getpid()})\n")
        if path:
            with open(path, "a", encoding="utf-8") as f:
                f.write(msg)
                f.flush()
                os.fsync(f.fileno())
        print("  " + msg.rstrip(), file=sys.stderr, flush=True)
        return
    os.killpg(pgid, signal.SIGKILL)


def cleanup(srv, endpoint):
    """패널 자식 프로세스를 정리하고 (Unix) 소켓 파일 제거(루프는 중단하지 않음)."""
    srv.running = False
    for s in list(srv.sessions.values()):
        for t in s.tabs:
            for p in t.window.panes():
                try:
                    if p.pty is not None:
                        # 크로스플랫폼: pty_backend 가 OS 별 종료를 추상화(Unix
                        # SIGKILL / Windows TerminateProcess).
                        p.pty.kill()
                        p.pty.close()
                    elif not IS_WINDOWS:
                        _killpg_not_self(p.child_pid)
                except Exception:
                    pass
    if not ipc.is_tcp(endpoint):
        try:
            if os.path.exists(endpoint):
                os.unlink(endpoint)
        except OSError:
            pass


# ── 서버 예외 로그 가드(§10-3⑤) ────────────────────────────────────────────
# 서버는 데몬이라 stderr 가 /dev/null 이고, 예외를 삼킨 뒤 `_log_error` 로
# `<state_base>.error.log` 에만 남긴다(그게 그 설계의 의도다 — 한 클라의 실패가 서버를
# 죽이지 않게). 그래서 **테스트가 초록불인데 서버가 매 프레임 터지고 있는** 상태가
# 성립한다: 실제로 그런 결함을 여러 번 사람이 로그를 읽어야 발견했다(§9.1 델타
# 베이스라인 레이스가 그 예 — 예외가 로그로만 끝나고 클라는 살아남았다).
# 이 가드는 그걸 **모든 테스트에 자동으로** 붙인다: teardown 시점에 error.log 에
# 트레이스백 블록이 있으면 그 테스트를 실패시킨다.
#
# 의도적으로 예외를 내는 테스트는 `teardown(..., allow_errors=True)` 또는
# `allow_errors=("where 조각", …)` 로 **좁게** 예외 처리한다(전면 off 금지 —
# 그러면 가드가 있으나 마나다).
_TB_HEAD = "Traceback (most recent call last):"


def server_error_blocks(sock) -> list:
    """서버가 남긴 예외 블록 목록(`==== 시각 [where] ====` 단위).

    `_log_error` 는 진단 로그(claude_format_unrecognized 등 예외 없는 호출)에도
    쓰이는데 그때 트레이스백 자리는 `NoneType: None` 이다 — **그건 예외가 아니므로
    세지 않는다**(안 그러면 정상 진단이 전 스위트를 빨갛게 만든다)."""
    out = []
    base = ipc.state_base(sock)
    for path in (base + ".error.log", base + ".client.crash.log"):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for chunk in text.split("\n==== "):
            if _TB_HEAD in chunk:
                out.append(("==== " + chunk).strip())
    return out


def _block_where(block: str) -> str:
    """블록 헤더 `==== <시각> [<where>] ====` 에서 where 라벨만 뽑는다(없으면 "")."""
    head = block.split("\n", 1)[0]
    if "[" in head and "]" in head:
        return head[head.index("[") + 1:head.rindex("]")]
    return ""


def assert_no_server_errors(sock, allow=False):
    """error.log 에 트레이스백이 남았으면 실패시킨다.

    allow=True 면 전부 허용(권장하지 않음), 문자열/시퀀스면 그 **where 라벨의 접두**와
    맞는 블록만 허용한다. 왜 '블록 본문 부분문자열'이 아니라 '라벨 접두'인가 —
    부분문자열이면 `expected_thing` 허용이 `unexpected_thing` 까지 허용한다(실제로
    자기검증 테스트에서 밟았다). 접두 매칭은 `remote_attach` 로
    `remote_attach(/tmp/x.sock)` 를, `slow client dropped` 로
    `slow client dropped (write backpressure)` 를 덮으면서 다른 라벨은 안 덮는다."""
    if allow is True:
        return
    allowed = (allow,) if isinstance(allow, str) else tuple(allow or ())
    bad = [b for b in server_error_blocks(sock)
           if not any(_block_where(b).startswith(a) for a in allowed)]
    if bad:
        head = bad[0].splitlines()
        raise AssertionError(
            "서버가 예외를 로그로만 삼켰다(%d블록) — 조용한 실패:\n%s"
            % (len(bad), "\n".join(head[:14])))


async def teardown(srv, task, sock, allow_errors=False):
    # 주의: 여기서 task 를 await 하지 않는다. Textual run_test 종료 직후엔 루프가
    # 정리 중이라 serve 태스크를 await 하면 "Event loop stopped" 가 난다.
    # cancel 만 하고 asyncio.run 의 마무리에 맡긴다.
    cleanup(srv, sock)
    task.cancel()
    # server_only 가 주입한 캡처 격리 override 를 해제 — 같은 프로세스의 다른
    # 테스트(capture_dir 의 비-override 동작을 검증하는 test_capture_dir_project_and_override
    # 등)에 새지 않게 한다.
    os.environ.pop("PYTMUX_CAPTURE_DIR", None)
    os.environ.pop("PYTMUX_TOKENS_DB", None)
    os.environ.pop("PYTMUX_PTY_HOST", None)
    # 정리 **뒤에** 검사한다 — 종료 경로에서 나는 예외까지 잡는다. 소켓 파일은 이미
    # 지워졌지만 error.log 는 남아 있다(경로가 다르다).
    assert_no_server_errors(sock, allow=allow_errors)


@contextlib.asynccontextmanager
async def running_server(allow_errors=False):
    """서버를 기동하고 블록 종료 시 정리하는 컨텍스트 매니저(1-6). 스위트 전반의
    `srv, task, sock = await server_only()` + try/finally `await teardown(...)`
    보일러플레이트(214곳)를 `async with running_server() as (srv, task, sock):`
    한 줄로 줄인다 — 예외 경로에서도 teardown 누락이 없다. 신규/리팩터 테스트 권장."""
    srv, task, sock = await server_only()
    try:
        yield srv, task, sock
    finally:
        await teardown(srv, task, sock, allow_errors=allow_errors)


@contextlib.contextmanager
def patched(mod, **attrs):
    """모듈 전역을 **이 블록 동안만** 갈아끼운다(끝나면 예외가 나도 되돌린다).

    테스트가 `mod.func = lambda …` 로 모듈 전역을 덮고 안 되돌리면, run.py 는 전
    모듈을 **한 프로세스**에서 돌리므로 그 치환이 뒤따르는 **모든 테스트 모듈**에
    그대로 남는다. 실측(2026-07-26): test_claude_resume_transparency/-verify 가
    servermixin.screen_text/claude_state 를 영구 치환해 그 뒤의 test_server(56) ·
    test_token_saver(5) · test_transcript_wiring(5) **66건**이 깨졌다 — 화면이 늘
    "화면"·상태가 늘 "limit" 이 되니 스크랩 계열 오라클이 통째로 무너진다. 각 모듈을
    격리 실행하면 초록이라 "기존 결함"으로 오해되기 쉬웠다(전체=적색/격리=녹색이면
    결함이 아니라 **오염**을 의심할 것).

    원래 값이 없던 속성은 블록이 끝나면 지운다(덮어쓴 게 아니라 새로 만든 것이므로).
    """
    sentinel = object()
    saved = {k: getattr(mod, k, sentinel) for k in attrs}
    for k, v in attrs.items():
        setattr(mod, k, v)
    try:
        yield mod
    finally:
        for k, v in saved.items():
            if v is sentinel:
                delattr(mod, k)
            else:
                setattr(mod, k, v)


def pane_text(pane):
    """패널의 현재 렌더 결과를 텍스트로(스타일 제외)."""
    rows, _ = pane.render(False)
    return "\n".join("".join(seg[0] for seg in row) for row in rows)


async def _poll(sleep, cond, timeout, step, snapshot=None, settle=8):
    """폴링 코어 — `wait_until`/`wait_for`(+ settled 변형) 넷의 **단일 구현**.

    sleep(step) 만 다르다(Textual 은 `pilot.pause` 로 프레임을 돌려야 렌더가 진행되고,
    서버측 테스트는 `asyncio.sleep`). snapshot 이 있으면 스톨 감지(정착-오답) 변형.
    반환 = (성공?, 마지막 스냅샷 repr 또는 None)."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    prev, stable, snap = object(), 0, None   # prev=센티넬(첫 비교는 반드시 '변함')
    while True:
        try:
            if cond():
                return True, None
        except Exception:
            pass
        if snapshot is not None:
            try:
                snap = snapshot()
            except Exception:
                snap = None
            if snap == prev:
                stable += 1
                if stable >= settle:
                    return False, repr(snap)   # 수렴했는데 조건 미충족 = 스톨
            else:
                stable, prev = 0, snap
        if loop.time() >= end:
            return False, repr(snap) if snapshot is not None else None
        await sleep(step)


async def wait_for(cond, timeout=4.0, step=0.05):
    """`wait_until` 의 **pilot 없는** 판(서버측 테스트용). cond() 참이면 True.

    Textual 앱이 없는 테스트(`server_only()`·소켓 왕복·pty-host)는 종전에 고정
    `asyncio.sleep(N)` 뒤 단언했다 — 느린/부하 높은 러너에서 정확히 같은 플레이크가
    난다(클라측 고정 `pilot.pause` 와 동형). 조건 대기는 이걸 쓴다. 호출부는 반환 후에도
    동일 조건을 단언해 실패 메시지를 보존한다.

    주의: **의미 있는 지연**(grace 타이머 만료·디바운스 경과를 *일으키는* 대기)은
    폴링으로 바꾸면 안 된다 — 그건 조건 대기가 아니라 시간 자체가 입력이다."""
    ok, _ = await _poll(asyncio.sleep, cond, timeout, step)
    return ok


async def wait_for_settled(cond, snapshot, timeout=4.0, step=0.05, settle=8):
    """`wait_until_settled`(스톨 워치독)의 pilot 없는 판 — (성공?, 진단).

    resume/재시작/페더레이션처럼 "진행하다 멈추는" 서버측 절차에서, 관측치가 연속
    불변인데 조건 미충족이면 timeout 을 다 쓰지 않고 **무엇에 멈췄는지** 돌려준다."""
    return await _poll(asyncio.sleep, cond, timeout, step, snapshot, settle)


async def wait_until(pilot, cond, timeout=4.0, step=0.05):
    """cond() 가 참이 될 때까지 pilot.pause(step) 로 폴링한다(최대 timeout). 참이 되면
    True, 시간 초과면 False. 고정 `pilot.pause(N)` + 단언 패턴의 CI 플레이크(느린
    Windows 러너에서 모달 push·키 처리·렌더가 N 초 안에 안 끝남)를 없앤다 — Unix 에선
    조건 충족 즉시 빠르고, 느린 환경에선 timeout 까지 인내한다. 호출부는 반환 후에도
    동일 조건을 단언해(실패 메시지 보존) 의미를 유지한다."""
    ok, _ = await _poll(pilot.pause, cond, timeout, step)
    return ok


async def wait_until_settled(pilot, cond, snapshot, timeout=4.0, step=0.05,
                             settle=8):
    """`wait_until` 의 **스톨 감지** 변형(로드맵 #3 test-infra 스톨 워치독).

    cond() 참까지 폴링하되, 관측 상태 `snapshot()` 이 `settle` 회 **연속 불변**인데도
    cond 가 아직 거짓이면 = 화면/상태가 **수렴했는데 조건이 안 맞음**(정착-오답 스톨)
    으로 보고, timeout 까지 안 기다리고 즉시 `(False, repr(snapshot))` 을 돌려준다 —
    바 타임아웃과 달리 **무엇에 수렴했는지** 진단을 준다. 상태가 계속 변하면(진행 중)
    timeout 까지 인내(느린 CI 흡수). cond 참이면 `(True, None)`. 호출부는 반환 후에도
    동일 조건을 단언해 실패 메시지를 보존한다.

    '수렴-오답'과 '느려서 아직'을 가르는 게 핵심: 렌더가 멈췄는데 조건 미충족이면
    더 기다려도 소용없으니 빠르게 진단 실패시키고, 아직 프레임이 흐르면 인내한다."""
    return await _poll(pilot.pause, cond, timeout, step, snapshot, settle)


async def wait_mounted(pilot, screen=None, child=None, timeout=4.0, step=0.05):
    """맨 위 화면이 `screen` 이고 **자식까지 마운트될 때까지** 기다린 뒤 그 화면을 돌려준다.

    이게 왜 따로 필요한가 — `push_screen` 직후 `len(screen_stack) > 1` 은 **이미 참**이라
    그걸로 폴링하면 0회 대기가 되고, 곧이어 `query_one(...)` 이 `NoMatches` 로 깨진다
    (실측 3건). 그래서 이 저장소는 마운트 대기를 **"폴링으로 못 옮기는 부류"** 로 분류하고
    고정 `pilot.pause(0.1~0.4)` 를 남겨 뒀다.

    그런데 못 옮기는 것이 아니라 **폴링 조건이 틀렸던 것**이다(2026-07-27j 실측): 화면
    자체가 아니라 **자식이 생겼는가**(`screen.query(child)`)를 보면 정확히 그 대기다.
    확인 방법 = 뮤테이션 — 이 대기를 통째로 지우면 `No nodes match 'TextArea' on
    ComposePromptScreen()` 로 깨지고, 이 대기로 바꾸면 통과한다. 즉 고정 pause 부채의
    가장 큰 덩어리가 이주 가능하다.

    `screen` = 클래스 또는 클래스 이름(문자열). `child` = 위젯 타입/셀렉터(생략하면
    화면 마운트까지만). 실패해도 예외를 던지지 않는다 — 호출부가 반환된 화면으로
    단언해 실패 메시지를 자기 문맥으로 남긴다(다른 wait_* 헬퍼와 같은 규약)."""
    def _ok():
        top = pilot.app.screen_stack[-1]
        if screen is not None:
            name = screen if isinstance(screen, str) else screen.__name__
            if top.__class__.__name__ != name:
                return False
        if not top.is_mounted:
            return False
        return bool(top.query(child)) if child is not None else True

    await _poll(pilot.pause, _ok, timeout, step)
    return pilot.app.screen_stack[-1]


async def drain(reader, store, timeout=0.8, until=None):
    """소켓에서 timeout 동안 들어오는 메시지를 store(list)에 모은다.

    until(store) 술어를 주면 만족 즉시 반환한다. Windows(TCP+ConPTY)는 메시지
    왕복이 느려 고정 창이 빠듯하므로, 호출부는 넉넉한 timeout + until 로 "조건 충족
    시 조기 반환"을 쓰면 Unix 에선 빠르고 Windows 에선 인내한다.
    """
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        try:
            msg = await asyncio.wait_for(pytmux.read_msg(reader),
                                         timeout=max(0.01, end - loop.time()))
        except asyncio.TimeoutError:
            break
        if msg is None:
            break
        store.append(msg)
        if until is not None and until(store):
            break


async def first_session(srv, timeout=1.0):
    """세션이 생길 때까지 대기 후 첫 세션 반환."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if srv.sessions:
            return next(iter(srv.sessions.values()))
        await asyncio.sleep(0.02)
    return next(iter(srv.sessions.values())) if srv.sessions else None


def make_app(sock, cfg=None, session=None):
    # 테스트 UI 단언은 한국어 라벨 기준(§6 i18n 이전 작성)이다. 앱은 환경 LANG 으로
    # 로케일을 정하므로(CI 는 ko 가 아닐 수 있음) cfg 에 lang=ko 를 기본 주입해
    # 결정론적으로 만든다(테스트가 명시 lang 을 주면 그대로 둔다).
    cfg = dict(cfg or {})
    cfg.setdefault("lang", "ko")
    return pytmux.build_client_app(sock, cfg, session)


async def max_loop_gap(awaitable_factory, *, step=0.005, settle=0.02):
    """`awaitable_factory()` 를 await 하는 동안 **이벤트 루프가 최대 몇 초 멈췄는지** 잰다.

    서버는 단일 스레드 asyncio 라, 어떤 경로가 동기 I/O(서브프로세스·대형 fs 스캔·
    느린 소켓)를 루프에서 돌리면 그동안 전 클라의 프레임·입력·ping 이 모두 멎는다.
    이 저장소는 그 결함(blocking-on-loop)을 **네 번** 겪었고(claude-name-sync S-3,
    autorename P8, mdir LIV, 그리고 코어 _pane_cwd/ncd/p4 — 2026-07-17), 매번 코드
    리뷰로만 잡았다. 이 헬퍼는 그걸 **런타임 단언**으로 바꾼다.

    원리: 5ms 마다 깨는 하트비트 태스크를 띄우고 실제 깬 간격의 최댓값을 본다.
    루프가 X 초 막히면 하트비트도 X 초 못 깨므로 gap≈X 가 된다. 오프로드된 경로는
    작업이 아무리 길어도 gap 이 step 수준에 머문다(실측: 400ms 작업 → 인라인
    408ms vs executor 6.5ms — 60배 넘게 벌어져 임계값 선택이 둔감하다).

    반환: 최대 gap(초). 호출부는 `assert gap < 0.15` 같은 넉넉한 상한을 건다."""
    gaps: list[float] = []
    stop = False

    async def _hb():
        last = asyncio.get_event_loop().time()
        while not stop:
            await asyncio.sleep(step)
            now = asyncio.get_event_loop().time()
            gaps.append(now - last)
            last = now

    t = asyncio.create_task(_hb())
    await asyncio.sleep(settle)          # 하트비트가 자리잡을 시간
    try:
        r = awaitable_factory()
        if inspect.isawaitable(r):
            await r
    finally:
        stop = True
        with contextlib.suppress(Exception):
            await t
    return max(gaps) if gaps else 0.0
