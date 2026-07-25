"""F-B(검수 2026-07-17) — 상류 status 플러드의 다운스트림 증폭 상한.

상류 status 1건은 다운스트림에서 **클라 수만큼** 프레임이 된다. 상한이 없으면
망가지거나 악의적인 상류가 초당 수천 건을 뿜을 때 다운스트림이 같이 죽는다. 고치되
**정상 처리량은 건드리지 않아야** 한다(이 항목이 오래 유보된 이유) — 그래서 버리는
rate-limit 이 아니라 **합치기**로 간다: 빈도만 상한하고 마지막 상태는 반드시 나간다.

되돌리면 실패해야 하는 오라클:
  · 합치기를 빼고 매번 즉시 방송하면 → test_flood_is_coalesced 실패
  · 대기 중 방송을 보는-클라 갱신으로 덮으면 → test_pending_broadcast_wins 실패
  · 죽은 링크 가드를 빼면 → test_dead_link_does_not_emit 실패
  · 간격 게이트를 항상 켜면(정상도 지연) → test_normal_cadence_is_immediate 실패
"""
import asyncio

import harness  # noqa: F401
from pytmuxlib import serverremote


class _Link:
    """RemoteLink 의 합치기 상태만 흉내(진짜 링크는 소켓·태스크가 필요하다)."""

    def __init__(self):
        self.alive = True
        self.host = "h1"
        self._status_push_at = 0.0
        self._status_push_task = None
        self._status_pending = None


class _Srv:
    """serverremote 의 합치기 메서드만 빌려 쓰는 최소 서버."""

    _remote_status_push = serverremote.ServerRemoteMixin._remote_status_push
    _remote_status_flush = serverremote.ServerRemoteMixin._remote_status_flush

    def __init__(self, loop):
        self.loop = loop
        self.bcast = 0
        self.viewer = 0

    def _remote_status_broadcast(self):
        self.bcast += 1

    def _remote_viewer_status(self, link):
        self.viewer += 1


def _setup():
    loop = asyncio.get_running_loop()
    return _Srv(loop), _Link()


async def test_normal_cadence_is_immediate():
    """정상 상류(간격이 충분)는 **지연 없이** 매번 나간다 — 여기서 지연이 생기면
    탭바·헤더가 눈에 띄게 늦는다(이 수정이 회귀가 되는 지점)."""
    srv, link = _setup()
    for _ in range(3):
        link._status_push_at = srv.loop.time() - 10.0     # 오래 전 마지막 전송
        srv._remote_status_push(link, broadcast=True)
    assert srv.bcast == 3 and link._status_push_task is None


async def test_flood_is_coalesced():
    """폭주 200건 → 즉시 1회 + 합쳐진 1회. 200회 방송이 아니다."""
    srv, link = _setup()
    for _ in range(200):
        srv._remote_status_push(link, broadcast=True)
    assert srv.bcast == 1                    # 첫 건만 즉시
    assert link._status_push_task is not None   # 나머지는 한 번으로 합쳐 예약
    await asyncio.sleep(serverremote._STATUS_PUSH_MIN_GAP * 2)
    assert srv.bcast == 2                    # 마지막 상태가 반드시 나간다
    assert link._status_push_task is None


async def test_pending_broadcast_wins():
    """대기 중 '탭 목록 변동'이 한 번이라도 있었으면 보는-클라 갱신으로 강등되지
    않는다 — 강등되면 다른 클라 탭바가 낡은 채로 남는다."""
    srv, link = _setup()
    srv._remote_status_push(link, broadcast=False)     # 즉시(viewer)
    assert (srv.viewer, srv.bcast) == (1, 0)
    srv._remote_status_push(link, broadcast=True)      # 대기로 들어감
    srv._remote_status_push(link, broadcast=False)     # 뒤이어 약한 신호
    await asyncio.sleep(serverremote._STATUS_PUSH_MIN_GAP * 2)
    assert srv.bcast == 1 and srv.viewer == 1          # 강한 쪽으로 승격


async def test_dead_link_does_not_emit():
    """예약이 걸린 뒤 링크가 죽으면 지각 콜백이 유령 프레임을 만들지 않는다."""
    srv, link = _setup()
    srv._remote_status_push(link, broadcast=True)      # 즉시 1회
    srv._remote_status_push(link, broadcast=True)      # 예약
    link.alive = False
    await asyncio.sleep(serverremote._STATUS_PUSH_MIN_GAP * 2)
    assert srv.bcast == 1 and srv.viewer == 0


async def test_amplification_is_bounded_per_second():
    """상한이 실제로 '초당 상수'인지 — 비율로 단언한다(시간이 아니라 비율:
    느린 러너에 둔감하면서 상한 부재엔 민감)."""
    srv, link = _setup()
    burst = 500
    for _ in range(burst):
        srv._remote_status_push(link, broadcast=True)
    await asyncio.sleep(serverremote._STATUS_PUSH_MIN_GAP * 2)
    assert srv.bcast <= 2, "폭주 %d건이 방송 %d회로 증폭됐다" % (burst, srv.bcast)


async def test_link_has_coalescing_state():
    """RemoteLink 가 합치기 상태를 **자기 __init__ 에서** 들고 있어야 한다 —
    getattr 폴백에만 기대면 필드가 사라져도 조용히 동작해 회귀를 못 잡는다."""
    import inspect
    src = inspect.getsource(serverremote.RemoteLink.__init__)
    for attr in ("_status_push_at", "_status_push_task", "_status_pending"):
        assert attr in src, attr
