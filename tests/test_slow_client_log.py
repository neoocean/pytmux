"""느린 클라 떼어 내기는 진단 한 줄이지 크래시가 아니다 (pytmux-515).

QA 의 `server/no_traceback` 오라클이 `asyncio.exceptions.CancelledError` 트레이스백을 S1 로
올렸다. 출처는 `serverio._flush_to_client` 의 설계된 백프레셔 경로였다 — `write_lock` 을
시한 안에 못 잡으면 그 클라를 떼는데, 떼는 함수가 `except asyncio.TimeoutError:` 안에서
`_log_error` 를 불러 시한초과 사슬(`CancelledError` → `TimeoutError`)이 통째로 적혔다.

되돌리면 실패해야 하는 오라클:
  · `_log_error(..., exc=False)` 를 빼면 → test_the_drop_leaves_no_traceback
  · 떼는 까닭을 안 실으면 → test_the_drop_says_why_and_who
  · 호출부가 까닭을 안 넘기면(**호출부**) → test_a_held_write_lock_drops_with_that_reason
"""
import asyncio

import harness
from harness import server_only, teardown

from pytmuxlib import ipc, serverio
from pytmuxlib.model import ClientConn


class _Writer:
    transport = None

    def close(self):
        pass


def _log_text(sock):
    try:
        with open(ipc.state_base(sock) + ".error.log", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


async def test_the_drop_leaves_no_traceback():
    srv, task, sock = await server_only()
    try:
        c = ClientConn(_Writer())
        srv.clients.append(c)
        try:
            raise asyncio.TimeoutError()
        except asyncio.TimeoutError:
            srv._drop_slow_client(c, "시험")     # 실제 호출부와 같은 자리(except 안)
        assert c not in srv.clients
        text = _log_text(sock)
        assert "slow client dropped" in text, text
        assert "Traceback" not in text, text
        assert harness.server_error_blocks(sock) == []
    finally:
        await teardown(srv, task, sock)


async def test_the_drop_says_why_and_who():
    srv, task, sock = await server_only()
    try:
        c = ClientConn(_Writer())
        c.cols, c.rows = 120, 40
        c.caps = {"blocks", "cells"}
        srv.clients.append(c)
        srv._drop_slow_client(c, "까닭-표식")
        text = _log_text(sock)
        assert "why=까닭-표식" in text, text
        assert "120x40" in text, text
        assert "blocks" in text, text
    finally:
        await teardown(srv, task, sock)


async def test_a_held_write_lock_drops_with_that_reason():
    """★ 호출부 — 잠금을 쥔 채 flush 하면 그 까닭으로 떼고, 트레이스백은 없다."""
    srv, task, sock = await server_only()
    try:
        c = ClientConn(_Writer())
        srv.clients.append(c)
        await c.write_lock.acquire()          # 다른 쓰기가 drain 에 묶인 상태
        try:
            with harness.patched(serverio, _CLIENT_WRITE_TIMEOUT=0.05):
                await srv._flush_to_client(c, [{"t": "noop"}])
        finally:
            c.write_lock.release()
        assert c not in srv.clients
        text = _log_text(sock)
        assert "write_lock" in text, text
        assert "Traceback" not in text, text
    finally:
        await teardown(srv, task, sock)
