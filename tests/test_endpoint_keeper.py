"""서버가 자기 게시 파일(토큰·pid·소켓)을 지킨다 — pytmux/pytmux-543.

사고(2026-10-02 · playground · 사용자 제보 «ssh 로 붙으면 옛 탭이 다 사라지고 새 세션이
뜬다 · 옛 탭의 앱은 계속 돈다»): macOS 15 의 `com.apple.tmp_cleaner` 가 매일 0시에

    find /tmp -type f -atime +3 -mtime +3 -ctime +3 -delete

를 돈다. 서버는 토큰·pid 파일을 기동 때 한 번만 써서, 며칠 쓰던 서버는 **소켓은 살아
있는데 토큰이 없는** 상태가 됐다. 다음 ssh attach 는 `auth_failed` 를 받고 「좀비」로 보아
새 서버로 교체했다 — 옛 서버는 경로 없는 소켓을 쥔 채 탭의 앱들을 계속 돌리는 **고아**가
됐다(pid 파일도 지워져 새 주인의 거두기도 안 닿았다).

되돌리면 실패해야 하는 것:
  · `_keep_endpoint_files` 의 토큰 재게시 갈래를 지우면
    → test_a_cleaned_token_is_republished_with_the_same_value
  · 시각 새로 고침(`os.utime`)을 지우면
    → test_the_keeper_keeps_the_files_out_of_the_cleaners_age_test
  · 엔드포인트 소유 확인을 지우면(남의 토큰을 덮는다)
    → test_a_server_that_lost_the_endpoint_writes_nothing
  · `serverio.handle_client` 의 `_heal_endpoint_soon` 호출을 지우면
    → test_an_auth_failure_makes_the_server_republish_its_token
    → test_attach_keeps_a_live_server_whose_files_were_cleaned (사용자 시나리오)
  · `serverio.serve` 의 keeper 태스크를 지우면
    → test_serve_runs_the_keeper_loop (호출부 오라클)
"""
import asyncio
import os
import socket
import time

import harness
from harness import running_server

from pytmuxlib import ipc, launcher, serverpersist


def _clean_like_tmp_cleaner(sock):
    """`tmp_cleaner` 가 지우는 것 — 소켓은 남기고 **일반 파일**인 토큰·pid 를 지운다."""
    for p in (ipc.token_path(sock), ipc.server_pidfile(sock)):
        if os.path.exists(p):
            os.unlink(p)


async def test_a_cleaned_token_is_republished_with_the_same_value():
    """지워진 토큰·pid 를 **메모리의 같은 값**으로 다시 쓴다 — 붙어 있던 클라가 가진 값과
    같아야 재접속도 산다."""
    async with running_server() as (srv, task, sock):
        assert ipc.read_token(sock) == srv.auth_token
        _clean_like_tmp_cleaner(sock)
        assert ipc.read_token(sock) is None and ipc.read_server_pid(sock) is None
        why = await srv._keep_endpoint_files("test")
        assert why.startswith("republished:"), why
        assert ipc.read_token(sock) == srv.auth_token, "같은 토큰으로 되살려야 한다"
        assert ipc.read_server_pid(sock) == os.getpid(), "pid 파일도 되살린다(거두기의 주소)"
        # 한 번 더 돌면 고칠 것이 없다(멱등).
        assert await srv._keep_endpoint_files("test") == "kept"


async def test_a_tampered_token_is_restored_while_the_endpoint_is_mine():
    async with running_server() as (srv, task, sock):
        ipc.write_token(sock, "0" * 64)
        assert await srv._keep_endpoint_files("test") == "republished:token"
        assert ipc.read_token(sock) == srv.auth_token


async def test_a_server_that_lost_the_endpoint_writes_nothing():
    """대조군: 엔드포인트를 뺏긴 서버(= 새 주인이 소켓 이름을 가져갔다)는 아무것도 안
    쓴다. 새 주인의 토큰을 덮으면 그 주인에게 붙던 모든 클라가 auth_failed 로 끊긴다."""
    async with running_server() as (srv, task, sock):
        if ipc.is_tcp(sock):
            from run import skip
            skip("unix 소켓 inode 로 소유를 재는 시험(TCP 는 포트파일로 잰다)")
            return
        other = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        tmp = sock + ".other.tmp"
        try:
            other.bind(tmp)
            other.listen(1)
            os.replace(tmp, sock)              # 새 주인이 이름을 가져간 모양
            ipc.write_token(sock, "1" * 64)    # 새 주인의 토큰
            assert await srv._keep_endpoint_files("test") == "not-mine"
            assert ipc.read_token(sock) == "1" * 64, "남의 토큰을 덮었다"
            os.unlink(ipc.token_path(sock))
            assert await srv._keep_endpoint_files("test") == "not-mine"
            assert ipc.read_token(sock) is None, "뺏긴 엔드포인트에 토큰을 썼다"
        finally:
            other.close()


async def test_the_keeper_keeps_the_files_out_of_the_cleaners_age_test():
    """정리 작업의 나이 조건(접근·수정 시각이 며칠 전)을 끊는다 — 지워지기 전에 막는 쪽이
    지워진 뒤 되살리는 쪽보다 낫다(그 사이에 붙는 클라가 없다)."""
    async with running_server() as (srv, task, sock):
        old = time.time() - 5 * 86400
        paths = [ipc.token_path(sock), ipc.server_pidfile(sock)]
        if not ipc.is_tcp(sock):
            paths.append(sock)
        for p in paths:
            os.utime(p, (old, old))
        assert await srv._keep_endpoint_files("test") == "kept"
        now = time.time()
        for p in paths:
            st = os.stat(p)
            assert now - st.st_mtime < 60 and now - st.st_atime < 60, \
                f"{os.path.basename(p)} 의 시각이 그대로다 — 정리 작업이 지운다"


async def test_a_cleaned_sshwrap_token_comes_back_with_the_value_panes_have():
    """`sshwrap.tok` 이 지워진 뒤 다음 패널 기동이 **새 값**을 만들면 서버 캐시와 어긋나
    ssh 중첩 자동 승격의 출처 검증이 조용히 실패한다(실측 00:36 재생성). 패널들이 가진
    값으로 되살린다."""
    async with running_server() as (srv, task, sock):
        await srv._keep_endpoint_files("test")     # 파일이 있을 때 캐시를 채운다
        tok = srv._sshwrap_tok
        assert tok, "캐시가 안 찼다"
        path = os.path.join(ipc.default_state_dir(), "sshwrap.tok")
        os.unlink(path)
        why = await srv._keep_endpoint_files("test")
        assert "sshwrap.tok" in why, why
        with open(path) as f:
            assert f.read().strip() == tok, "패널들이 가진 값과 다르다"


async def test_an_auth_failure_makes_the_server_republish_its_token():
    """거절 한 번이 자가 점검을 부른다 — 주기(30분)를 기다리지 않는다."""
    async with running_server() as (srv, task, sock):
        _clean_like_tmp_cleaner(sock)
        reply = await asyncio.to_thread(launcher.control_request, sock, {"t": "list"})
        assert isinstance(reply, dict) and reply.get("error") == "auth_failed", reply
        assert await harness.wait_for(lambda: ipc.read_token(sock) == srv.auth_token), \
            "거절 뒤에도 토큰이 다시 게시되지 않았다"


async def test_attach_keeps_a_live_server_whose_files_were_cleaned():
    """사용자 시나리오: 정리 작업이 토큰·pid 를 지운 뒤 ssh attach 의 판정.
    종전 = 「좀비 → 새 서버로 교체」(옛 탭 전부 고아). 이제 = 기존 서버를 그대로 쓴다.
    대조군: 자가 점검을 끄면 같은 판정이 교체(False)로 돌아간다 — 이 시험이 그 한 줄을
    재고 있음을 같은 자리에서 보인다."""
    async with running_server() as (srv, task, sock):
        _clean_like_tmp_cleaner(sock)
        assert ipc.probe(sock), "소켓은 살아 있다(정리 작업은 소켓을 안 지운다)"
        assert await asyncio.to_thread(launcher.existing_server_usable, sock) is True, \
            "살아 있는 서버를 좀비로 보고 교체하려 했다"
        assert ipc.read_token(sock) == srv.auth_token
        # 대조군 — 자가 점검이 없으면 교체로 간다.
        _clean_like_tmp_cleaner(sock)
        srv._heal_endpoint_soon = lambda why: None      # 인스턴스에만 — 끝나면 지운다
        try:
            with harness.patched(launcher, _HEAL_WAIT_POLLS=10):
                assert await asyncio.to_thread(
                    launcher.existing_server_usable, sock) is False
        finally:
            del srv._heal_endpoint_soon


async def test_serve_runs_the_keeper_loop():
    """호출부 오라클 — 헬퍼만 재면 serve 에서 태스크를 지워도 통과한다."""
    async with running_server() as (srv, task, sock):
        names = {getattr(t.get_coro(), "__qualname__", "") for t in asyncio.all_tasks()}
        assert any(n.endswith("_endpoint_keeper_loop") for n in names), names


async def test_the_keeper_period_stays_far_below_the_cleaners_age():
    """주기가 정리 작업의 나이(macOS 3일 · Linux 10일)보다 한참 짧아야 한다."""
    assert serverpersist._ENDPOINT_KEEP_INTERVAL <= 6 * 3600
    assert serverpersist._ENDPOINT_RECHECK_DELAY < \
        launcher._HEAL_WAIT_POLLS * 0.02, "자가 점검이 attach 의 대기 예산 안에 못 끝난다"


async def test_start_server_does_not_replace_a_live_server_whose_token_was_cleaned():
    """`pytmux start-server`(ssh <host> 로 원격을 준비하는 길)도 같은 판정이다 — 토큰만
    지워진 살아 있는 서버를 교체하지 않는다(교체는 그 머신 탭을 고아로 만든다)."""
    import contextlib
    import io
    async with running_server() as (srv, task, sock):
        _clean_like_tmp_cleaner(sock)
        spawned = []
        out = io.StringIO()
        with harness.patched(launcher, spawn_server=lambda sp: spawned.append(sp)):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                rc = await asyncio.to_thread(launcher.run_start_server, sock)
        assert rc == 0, out.getvalue()
        assert spawned == [], "토큰만 지워진 살아 있는 서버를 새 서버로 교체했다"
        assert ipc.read_token(sock) == srv.auth_token
