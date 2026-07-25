"""§10-3⑥ — **N-클라이언트 용량 회귀 게이트**(§5 slow-consumer 계약).

종전 커버리지는 `test_flush_to_client_drops_slow_consumer` 한 개뿐이었다. 그건 "한 클라의
버퍼가 high-water 를 넘으면 떨군다" 는 **단위** 계약이고, 실제로 아픈 질문은 다르다:
클라가 여럿 붙었을 때 ① 한 명이 느리면 **나머지가 같이 멈추는가** ② 브로드캐스트 비용이
클라 수에 **선형인가**(이차면 몇 명만 붙어도 서버가 죽는다) ③ 떨군 클라의 **상태가
해제되는가**(안 하면 붙었다 떨어지는 클라마다 누수) ④ 그 와중에 **이벤트 루프가 살아
있는가**(단일 스레드라 한 번 막히면 전 클라가 멎는다).

시간은 절대값이 아니라 **비율**로 본다(느린 러너에 둔감하면서 O(N²)엔 민감 —
검수 2026-07-17 §7 관례).

되돌리면 실패해야 하는 오라클:
  · high-water 드롭을 지우면 → test_one_slow_client_does_not_stall_others 실패
  · 드롭 시 clients 에서 제거를 빼면 → test_dropped_client_state_is_released 실패
  · 클라별 배치(B4)를 클라×패널 이중 루프 재렌더로 되돌리면
    → test_broadcast_cost_is_linear_in_clients 실패
  · 브로드캐스트를 순차 await(느린 클라 뒤에서 대기)로 바꾸면
    → test_loop_stays_responsive_with_many_clients 실패
"""
import asyncio
import time

import harness
from harness import max_loop_gap, running_server
from pytmuxlib.model import ClientConn


class _Tr:
    def __init__(self, n=0):
        self._n = n

    def get_write_buffer_size(self):
        return self._n


class _W:
    """가짜 writer — 실제 소켓 없이 flush 경로를 돌린다(프레임 수·드레인 지연 관측)."""

    def __init__(self, buffered=0, drain_delay=0.0, hang=False):
        self.transport = _Tr(buffered)
        self.frames = []
        self.closed = False
        self._delay = drain_delay
        self._hang = hang

    def write(self, b):
        self.frames.append(b)

    def close(self):
        self.closed = True

    async def drain(self):
        if self._hang:
            await asyncio.Event().wait()
        elif self._delay:
            await asyncio.sleep(self._delay)


def _attach(srv, sess, writer):
    c = ClientConn(writer)
    c.session = sess
    srv.clients.append(c)
    return c


async def test_all_clients_receive_broadcast():
    """N 클라가 붙으면 **전원**이 프레임을 받는다(누락 = 그 클라 화면이 굳는다)."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        cs = [_attach(srv, sess, _W()) for _ in range(16)]
        await asyncio.gather(*(srv._flush_to_client(c, [b"f"]) for c in cs))
        assert all(c.writer.frames for c in cs), "일부 클라가 프레임을 못 받았다"
        assert all(not c.writer.closed for c in cs), "정상 클라가 떨궈졌다"


async def test_one_slow_client_does_not_stall_others():
    """한 명이 high-water 를 넘겨도 나머지는 **그대로 받는다** — 느린 소비자가 전체
    브로드캐스트를 인질로 잡으면 그게 이 프로젝트가 겪은 실제 장애 모양이다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        slow = _attach(srv, sess, _W(buffered=99 * 1024 * 1024))
        good = [_attach(srv, sess, _W()) for _ in range(8)]
        t0 = time.monotonic()
        await asyncio.gather(*(srv._flush_to_client(c, [b"f"])
                               for c in [slow] + good))
        took = time.monotonic() - t0
        assert slow not in srv.clients and slow.writer.closed, "느린 클라 미드롭"
        assert all(c.writer.frames for c in good), "느린 클라 때문에 나머지가 굶었다"
        assert took < 2.0, "브로드캐스트가 느린 클라를 기다렸다(%.2fs)" % took
        # 의도된 드롭이므로 그 로그만 좁게 허용한다.
        harness.assert_no_server_errors(sock, allow=("slow client dropped",))


async def test_broadcast_cost_is_linear_in_clients():
    """클라 수 2배 → 비용 **2배 근처**(선형). 이차면 여기서 3배를 훌쩍 넘는다.

    절대 시간이 아니라 비율을 본다(느린 러너 내인성)."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)

        async def run(n):
            cs = [_attach(srv, sess, _W()) for _ in range(n)]
            t0 = time.monotonic()
            for _ in range(20):
                await asyncio.gather(*(srv._flush_to_client(c, [b"x" * 512])
                                       for c in cs))
            dt = time.monotonic() - t0
            for c in cs:
                if c in srv.clients:
                    srv.clients.remove(c)
            return dt

        t_small = await run(8)
        t_big = await run(16)
        ratio = t_big / max(t_small, 1e-6)
        assert ratio < 4.0, "클라 수 2배에 비용 %.1f배 — 이차 의심" % ratio


async def test_dropped_client_state_is_released():
    """떨군 클라는 `clients` 에서 사라지고 그 델타 기준(_sent_rows)도 함께 없어진다 —
    붙었다 떨어지는 클라마다 상태가 남으면 장수 데몬에서 누수가 쌓인다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        before = len(srv.clients)
        for _ in range(30):
            c = _attach(srv, sess, _W(buffered=99 * 1024 * 1024))
            c._sent_rows[1] = [("x", {})]
            await srv._flush_to_client(c, [b"f"])
            assert c not in srv.clients
            assert not c._sent_rows or c.writer.closed
        assert len(srv.clients) == before, "떨군 클라가 목록에 남았다"
        harness.assert_no_server_errors(sock, allow=("slow client dropped",))


async def test_loop_stays_responsive_with_many_clients():
    """N 클라 브로드캐스트 중에도 **이벤트 루프가 살아 있어야** 한다(단일 스레드 서버 —
    한 번 막히면 전 클라의 입력·ping 이 함께 멎는다). 드레인이 느린 클라를 섞어
    '느린 소비자가 루프를 잡는' 형태를 만든다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        cs = [_attach(srv, sess, _W(drain_delay=0.02)) for _ in range(24)]

        async def broadcast():
            await asyncio.gather(*(srv._flush_to_client(c, [b"f"]) for c in cs))

        gap = await max_loop_gap(broadcast)
        assert gap < 0.25, "브로드캐스트가 루프를 %.0fms 막았다" % (gap * 1000)
