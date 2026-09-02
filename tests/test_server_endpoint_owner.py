"""한 엔드포인트에는 서버가 **하나**다 — 앞 주인을 거두는 자리(pytmux/pytmux-435).

종전에는 그 불변식을 아무도 지키지 않았다. 새 서버가 소켓 이름을 `os.replace` 로
(TCP 면 포트파일을 덮어써서) 가져가고 앞 주인에게는 알리지 않았으므로, 앞엣것은
**도달 불가가 된 소켓을 쥔 채** liveness·프레임·그림자 `/usage` 프로브 루프를 계속
돌았다. 실측(2026-09-02)으로 한 엔드포인트에 서버 넷(36일·17일·5.8일·13분)이 살아
있었고 하나는 RSS 172MB·누적 CPU 622분이었다. 그리고 그 프로세스들은 **옛 코드**를
쥐고 있어 그 사이에 나간 고침이 한 번도 적용되지 않았다.

되돌리면 실패해야 하는 것:
  · `serverio.serve` 의 `self._publish_server_pid()` 한 줄을 지우면
    → test_a_running_server_publishes_its_pid_at_the_endpoint 실패
  · `serverio.serve` 의 `self._evict_previous_owner()` 한 줄을 지우면
    → test_the_serve_path_evicts_before_it_binds 실패(호출부 오라클 —
      헬퍼만 단언하면 그 줄을 지워도 통과한다: 이 저장소가 말하는 «공허 통과»)
  · `server.run_server` 의 `srv._evict_stale_owner = True` 를 지우면
    → test_only_the_production_entry_turns_eviction_on 실패
  · `serverpersist._cleanup_endpoint_files` 의 pid 파일 갈래를 지우면
    → test_a_clean_shutdown_takes_its_pidfile_with_it 실패
  · `_kill_pid_only` 를 `proc.terminate` 로 되돌리면
    → test_the_kill_never_widens_to_a_process_group 실패(2026-07-26 사고의 가드)
"""
import ast
import inspect
import os
import textwrap

import harness  # noqa: F401  (경로 설정 + 위생 설치)

from pytmuxlib import ipc, proc, server, serverio, serverpersist


# ── ⑴ 게시 — 도는 서버는 자기 pid 를 엔드포인트에 남긴다 ──────────────────────

async def test_a_running_server_publishes_its_pid_at_the_endpoint():
    """앞 주인을 겨냥할 **주소**가 생긴다. 종전에는 이 파일이 아예 없었다."""
    async with harness.running_server() as (srv, _task, sock):
        path = ipc.server_pidfile(sock)
        assert os.path.isfile(path), f"pid 파일이 없다: {path}"
        assert ipc.read_server_pid(sock) == os.getpid(), (
            "게시된 pid 가 이 프로세스가 아니다", ipc.read_server_pid(sock))
        # 그 파일은 **bind 뒤에** 써야 한다 — 앞서 쓰면 bind 가 실패한 프로세스의
        # pid 가 남아 다음 주인이 엉뚱한 산 프로세스를 지목한다. 여기서는 서버가
        # 실제로 붙을 수 있다는 것으로 그 순서를 갈음한다.
        assert ipc.probe(sock), "listen 이 안 섰는데 pid 가 게시됐다"
        _ = srv


async def test_a_clean_shutdown_takes_its_pidfile_with_it():
    """질서 있게 내려간 서버의 pid 는 남지 않는다(다음 기동이 «stale»로 안 헷갈린다).

    ⚠ `shutdown()` 을 직접 부를 수 없다(루프를 멈춘다) — 그것이 부르는
    `_cleanup_endpoint_files(owned_only=True)` 를 같은 인자로 재는 것이 이 자리의
    가장 가까운 잣대다.
    """
    async with harness.running_server() as (srv, _task, sock):
        path = ipc.server_pidfile(sock)
        assert os.path.isfile(path)
        srv._cleanup_endpoint_files(owned_only=True)
        assert not os.path.exists(path), (
            "종료 정리가 내 pid 파일을 안 지웠다 — 다음 기동이 죽은 pid 를 "
            "앞 주인으로 본다")


async def test_a_foreign_pidfile_survives_my_cleanup():
    """대조군 — **남의** pid 가 실린 파일은 내 정리가 안 지운다.

    좀비의 지연 shutdown(0.2s)이 새 주인이 방금 게시한 pid 를 지우면, 그 다음 주인은
    앞 주인을 못 찾아 이 이슈가 통째로 되돌아온다.
    """
    async with harness.running_server() as (srv, _task, sock):
        path = ipc.server_pidfile(sock)
        with open(path, "w", encoding="ascii") as f:
            f.write("999999\n")       # 남의 것(살아 있든 아니든 내 것이 아니다)
        srv._cleanup_endpoint_files(owned_only=True)
        assert os.path.isfile(path), "남의 pid 파일을 지웠다"
        # 뒤 테스트가 이 파일을 stale 로 보지 않게 되돌린다.
        srv._publish_server_pid()


# ── ⑵ 판정 — 누구를 앞 주인으로 보나 ─────────────────────────────────────────

class _Probe(serverpersist.ServerPersistMixin):
    """`_evict_previous_owner` 만 떼어 재는 최소 껍데기.

    ⛔ 진짜 서버를 두 벌 띄워 재지 않는다 — 이 스위트는 전 모듈이 **한 프로세스**라
    (`run.py`), 앞 회차가 게시한 pid 는 곧 **러너 자신**이다. 그것을 앞 주인으로
    지목하는 코드를 진짜로 돌리면 러너를 죽인다.
    """

    def __init__(self, sock_path):
        self.sock_path = sock_path
        self.killed = []
        self.logs = []

    def _log_error(self, where, detail=""):
        self.logs.append((where, detail))

    def _kill_pid_only(self, pid):     # 실제로 쏘지 않는다 — 겨냥만 적는다
        self.killed.append(pid)


def _probe(tmpdir, name="own"):
    return _Probe(os.path.join(tmpdir, name + ".sock"))


async def test_no_pidfile_means_nobody_to_evict(tmp_path=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _probe(d)
        assert p._evict_previous_owner() == "none"
        assert p.killed == []


async def test_my_own_pid_is_not_a_previous_owner():
    """execv 재시작(§5.6)은 pid 를 유지한다 — 앞 주인이 곧 나다. 여기서 쏘면 자살이다."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _probe(d)
        p._publish_server_pid()
        assert p._evict_previous_owner() == "self"
        assert p.killed == [], "자기 pid 를 겨냥했다"


async def test_a_dead_pid_is_stale_not_a_victim():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _probe(d)
        with open(ipc.server_pidfile(p.sock_path), "w", encoding="ascii") as f:
            f.write("2\n")             # pid 2 가 우리 서버일 수는 없다
        with harness.patched(proc, is_alive=lambda pid: False):
            assert p._evict_previous_owner() == "stale"
        assert p.killed == []


async def test_the_ask_comes_first_and_does_not_wait():
    """⑴ 부탁은 **말로** 하고, 거기서 **기다리지 않는다**.

    ★ 이 「안 기다린다」가 이 설계의 핵심이다: 앞 주인이 질서 있게 내려가는 데 이
    Windows 상자에서 **2.33~3.72초**가 걸리는데(3회 실측), attach 쪽 기동 예산은
    `wait_server_authed` 의 4.0초다. bind 앞에서 그만큼 기다리면 첫 attach 가
    「서버 기동 실패」로 오판된다.
    """
    import tempfile
    import time as _time
    from pytmuxlib import launcher
    with tempfile.TemporaryDirectory() as d:
        p = _probe(d)
        with open(ipc.server_pidfile(p.sock_path), "w", encoding="ascii") as f:
            f.write("4242\n")
        seen = []
        with harness.patched(
                launcher,
                control_request=lambda _s, obj: seen.append(obj) or {"ok": 1}), \
                harness.patched(proc, is_alive=lambda pid: pid == 4242):
            t0 = _time.monotonic()
            assert p._evict_previous_owner() == "asked"
            spent = _time.monotonic() - t0
        assert spent < 1.0, (
            f"bind 앞에서 {spent:.2f}s 를 태웠다 — attach 의 4.0초 예산을 먹는다")
        assert seen and seen[0].get("t") == "kill-server", (
            "말로 부탁하지 않았다(그 길만이 질서 있는 종료를 돈다)", seen)
        assert p._evict_pid == 4242, "끝을 볼 상대를 안 남겼다"
        assert p.killed == [], "부탁하기도 전에 쐈다"


async def test_an_owner_that_steps_down_is_not_shot():
    """⑵ listen 뒤에 지켜보다 물러나면 거기서 끝이다(확인 사살 없음)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _probe(d)
        p._evict_pid = 4242
        alive = {"n": 3}               # 몇 폴 뒤에 죽는다(= 지연 shutdown)

        def _is_alive(pid):
            if pid != 4242:
                return False
            alive["n"] -= 1
            return alive["n"] > 0

        with harness.patched(proc, is_alive=_is_alive):
            assert await p._finish_eviction(grace=3.0) == "gone"
        assert p.killed == [], "부탁을 듣고 물러난 서버를 또 쐈다"
        assert any(w == "evict_previous_owner" for w, _ in p.logs), (
            "무슨 일이 있었나를 아무 데도 안 남겼다", p.logs)


async def test_a_wedged_owner_is_shot_by_pid():
    """이 이슈가 실제로 잡은 부류 — **응답을 안 하는** 서버(CPU 를 태우며 도는 중).

    말로 부탁하는 길만 있으면 그 서버는 영생한다. 그래서 확인 사살이 필요하다.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _probe(d)
        p._evict_pid = 4243
        with harness.patched(proc, is_alive=lambda pid: pid == 4243):
            assert await p._finish_eviction(grace=0.2) == "alive"
        assert p.killed == [4243], ("겨냥이 틀렸다", p.killed)


async def test_nobody_to_finish_when_nobody_was_asked():
    """대조군 — 앞 주인이 없었으면 뒤걸음도 아무것도 안 한다."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = _probe(d)
        assert await p._finish_eviction() == "nobody"
        assert p.killed == []


# ── ⑶ 호출부 — 헬퍼가 아니라 «불리는가» 를 잰다 ───────────────────────────────

def _calls_in(func) -> set:
    """그 함수 본문이 부르는 `self.<이름>()` 의 이름 집합."""
    tree = ast.parse(textwrap.dedent(
        "".join(inspect.getsourcelines(func)[0])))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "self":
            names.add(node.func.attr)
    return names


async def test_the_serve_path_evicts_before_it_binds():
    """`serve()` 가 그 둘을 **실제로 부른다**(호출부 오라클).

    ★ 그리고 **차례**까지 잰다: 거두는 것이 bind 뒤면 앞 주인은 도달 불가인 채로
    영생하고(소켓 이름은 이미 넘어갔다) 게시가 bind 앞이면 실패한 기동의 pid 가 남는다.
    """
    src = "".join(inspect.getsourcelines(serverio.ServerIOMixin.serve)[0])
    calls = _calls_in(serverio.ServerIOMixin.serve)
    assert "_evict_previous_owner" in calls, "앞 주인에게 부탁하는 호출이 없다"
    assert "_publish_server_pid" in calls, "pid 를 게시하는 호출이 없다"
    assert "_finish_eviction" in calls, "거두기의 끝을 보는 호출이 없다"
    i_ask = src.index("_evict_previous_owner")
    i_bind = src.index("ipc.start_server")
    i_pub = src.index("_publish_server_pid")
    i_fin = src.index("_finish_eviction")
    assert i_ask < i_bind < i_pub <= i_fin, (
        "차례가 어긋났다 — 부탁은 bind 앞, 게시와 끝보기는 bind 뒤다",
        i_ask, i_bind, i_pub, i_fin)


async def test_only_the_production_entry_turns_eviction_on():
    """⛔ 스위트에서 켜지면 안 된다 — 앞 회차가 게시한 pid 는 **러너 자신**이다."""
    src = "".join(inspect.getsourcelines(server.run_server)[0])
    assert "_evict_stale_owner = True" in src, (
        "프로덕션 진입점이 이 기능을 안 켠다 — 그러면 아무 데서도 안 돈다")
    async with harness.running_server() as (srv, _task, _sock):
        assert not getattr(srv, "_evict_stale_owner", False), (
            "하니스 서버에서 켜졌다 — 이 스위트가 자기 러너를 겨냥한다")


async def test_the_kill_never_widens_to_a_process_group():
    """⛔ 2026-07-26 사고의 가드 — 그룹·자식 트리로 **넓히지 않는다**.

    `proc.terminate` 는 POSIX 에서 `killpg(getpgid(pid))` 다. pid 파일이 낡아 pid 가
    재사용됐거나 데몬화되지 않은 서버를 가리키면 그 그룹에 **부르는 쪽**이 들어 있을
    수 있고, 그 부류로 러너와 부모 셸까지 죽인 적이 있다.
    """
    src = "".join(inspect.getsourcelines(
        serverpersist.ServerPersistMixin._kill_pid_only)[0])
    for banned in ("killpg", "getpgid", "proc.terminate", '"/T"', "'/T'"):
        assert banned not in src, (
            f"겨냥이 pid 하나보다 넓다: {banned}", src)
    assert "os.kill(pid" in src, "POSIX 에서 pid 하나를 쏘는 자리가 없다"


async def test_the_host_shutdown_does_not_rely_on_a_write_the_loop_must_flush():
    """⛔ 종료가 pty-host 를 **실제로** 내린다 — 버퍼에 넣고 루프를 멈추면 안 된다.

    실측(2026-09-02 · 이 Windows 상자 · 격리 홈 · 서버 하나):

    | 무엇을 했나 | host |
    | --- | --- |
    | 서버가 살아 있는 채 `kill-server` (= 비동기 `writer.write`) | **살아남았다** |
    | 서버를 먼저 죽인 뒤 `kill-server` (= `shutdown_host_sync`) | 죽었다 |
    | 고친 뒤, 서버가 살아 있는 채 `kill-server` | 죽었다 |

    까닭: `PtyHostClient._send` 는 `writer.write()` 로 **버퍼에 넣을 뿐**이고 전송은
    이벤트 루프가 한다. 그런데 `shutdown()` 은 몇 줄 뒤에 `loop.stop()` 을 부른다 —
    그 프레임은 나가지 않고, host 는 프레임 대신 EOF 를 보고 «재시작»으로 읽어 영원히
    산다(고아 워치독도 소유자가 살아 있었다고 보면 안 죽인다).

    ⇒ 그래서 **새 블로킹 소켓으로 붙는** 동기 경로를 쓴다. 이 연결의 바닥 소켓에
    직접 쓰는 길은 막혀 있다 — asyncio 가 주는 `TransportSocket` 에는 `sendall`·`send`
    가 없다(아래 대조군이 그 사실을 못박는다).
    """
    src = "".join(inspect.getsourcelines(serverio.ServerIOMixin.shutdown)[0])
    assert "ptyhostmgr.shutdown_host_sync(self.sock_path)" in src, (
        "host 를 내리는 동기 경로가 없다 — 버퍼에 넣은 프레임은 안 나간다")
    i_sync = src.index("ptyhostmgr.shutdown_host_sync(self.sock_path)")
    i_stop = src.index("self.loop.stop()")
    assert i_sync < i_stop, "루프를 멈춘 뒤에 보내려 한다"


async def test_an_asyncio_transport_socket_cannot_sendall():
    """대조군 — 위 설계가 서 있는 **사실** 하나. 이것이 바뀌면 그 주석이 낡는다.

    첫 시도는 `writer.get_extra_info("socket")` 에 `sendall` 을 걸었는데, 그것은
    `asyncio.trsock.TransportSocket` 이고 3.11+ 에서 그 두 메서드가 없다 —
    `AttributeError` 가 `contextlib.suppress(Exception)` 에 삼켜져 **조용히 폴백**했다.
    """
    from asyncio.trsock import TransportSocket
    for missing in ("sendall", "send"):
        assert not hasattr(TransportSocket, missing), (
            f"이제 TransportSocket 에 {missing} 가 있다 — 바닥 소켓에 직접 쓰는 길이 "
            "열렸으니 `shutdown()` 의 주석과 처방을 다시 볼 것")
    for present in ("shutdown", "fileno"):
        assert hasattr(TransportSocket, present), present


async def test_the_watch_does_not_block_the_event_loop():
    """⛔ 뒤걸음은 **루프 안**에서 돈다 — 동기 대기를 걸면 그 8초 동안 화면이 멎는다.

    서버는 단일 스레드 asyncio 루프다(루트 CLAUDE.md). `_finish_eviction` 은 그
    루프의 태스크로 뜨므로 `time.sleep` 을 쓰면 클라 프레임·플러시가 통째로 선다.
    """
    # ⚠ 글자로 찾지 않는다 — 이 자리의 **주석이 그 이름을 말하고 있어서**(왜 안
    # 쓰는지를 적어 둔 자리) 문자열 검사는 자기 주석에 걸린다. 실제로 한 번 걸렸다.
    src = textwrap.dedent("".join(inspect.getsourcelines(
        serverpersist.ServerPersistMixin._finish_eviction)[0]))
    tree = ast.parse(src)
    called, awaited = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            called.add(ast.unparse(node.func))
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited.add(ast.unparse(node.value.func))
    assert "asyncio.sleep" in awaited, ("비동기 대기가 아니다", awaited)
    assert "time.sleep" not in called, (
        "루프를 막는 동기 대기를 부른다 — 그 동안 모든 클라가 멎는다", called)
