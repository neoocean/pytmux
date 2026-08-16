"""전역 검색 팬아웃의 **신뢰경계**(pytmux-27 ② 가 연 자리 · 검수 2026-08-09).

`test_search_all.py` 는 팬아웃이 **되는가**를 잰다. 이 파일은 그 옆에서 팬아웃이
**남에게 안 넘어가는가**를 잰다 — 상류는 신뢰불가라는 `serverremote` 모듈 규율을
회신 경로와 홉 카운터에도 적용한다.

1. **회신은 물어본 그 링크에서만 받는다.** 팬아웃 토큰(`sa:N`)은 서버 전역 순번이라
   **다음 값이 뻔하다.** 링크를 안 보고 토큰만으로 대기표를 열어 주면, 침해된 상류
   하나가 **다른 상류의 회신을 자기 것으로 채운다** — 그 줄은 화면에 `⇄남의호스트:탭`
   으로 뜨고(위조), 진짜 회신은 대기표가 이미 없어 조용히 버려진다(그런데 `hosts` 는
   그 호스트가 「답했다」고 적는다 = no silent caps 를 어기는 거짓말).

2. **홉 카운터는 음수를 안 받는다.** 고리(A→B→A) 방어는 `hops >= 상한` 한 줄인데
   `hops` 는 와이어에서 오고 `_int` 는 음수를 안 조인다. 음수 하나면 그 상한까지
   가는 데 수십억 홉이 걸린다 = 방어가 없는 것과 같다. 홉마다 상류가 **전 패널의
   스크롤백을 훑으므로** 값이 싸지 않다.
"""

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown, wait_for
from test_search_all import _fill


async def _triple(needle="NEEDLE"):
    """A →(직결) B·C 를 세우고 셋 다에 히트를 심는다.

    B 는 **정직한** 상류, C 는 이 시험이 침해시킬 상류다."""
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    srvC, taskC, sockC = await server_only()
    sessA = srvA.ensure_default_session(80, 24)
    await _fill(sessA.active_window.active_pane, ["로컬 " + needle])
    sessB = srvB.ensure_default_session(80, 24)
    await _fill(sessB.active_window.active_pane, ["정직한B " + needle])
    sessC = srvC.ensure_default_session(80, 24)
    await _fill(sessC.active_window.active_pane, ["침해된C " + needle])
    assert await srvA.remote_attach(sessA, endpoint=sockB), "A→B attach 실패"
    assert await srvA.remote_attach(sessA, endpoint=sockC), "A→C attach 실패"
    linkB = srvA._remotes_dict()[sockB]
    linkC = srvA._remotes_dict()[sockC]
    assert await wait_for(lambda: bool(linkB.windows) and bool(linkC.windows)), \
        "상류 첫 status 미도착"
    return (srvA, taskA, sockA, srvB, taskB, sockB, srvC, taskC, sockC,
            sessA, linkB, linkC)


def _compromise(srv, forged_text, span=4):
    """`srv` 를 「회신하면서 **이웃 토큰들**로도 한 벌씩 더 보내는」 상류로 만든다.

    실제 침해 상류가 할 수 있는 일 그대로다 — 자기 링크 위에 프레임을 더 얹는
    것뿐이고, 토큰은 자기가 받은 `sa:N` 에서 **순번을 세어** 짐작한다(전역 순번이라
    이웃이 곧 다른 상류의 대기표다)."""
    real_send = srv._send_to

    async def _send_to(c, obj):
        tok = isinstance(obj, dict) and obj.get("_req_token")
        if isinstance(tok, str) and tok.startswith("sa:"):
            n = int(tok.split(":", 1)[1])
            # ⛔ **자기 회신보다 먼저** 쏜다 — 침해된 쪽이 굳이 예의를 지킬 이유가 없다.
            for guess in range(max(1, n - span), n + span + 1):
                if guess == n:
                    continue
                await real_send(c, {
                    "t": "search_results", "_req_token": f"sa:{guess}",
                    "panes": 1, "capped_panes": 0, "truncated": False,
                    "items": [{"win": 0, "pane": 0, "line": 0, "gwin": 0,
                               "wid": None, "route": [],
                               "title": "위조", "text": forged_text}]})
        return await real_send(c, obj)

    srv._send_to = _send_to


def _add_latency(srv, delay=0.05):
    """`srv` 의 회신에 RTT 를 준다 — 진짜 상류는 ssh 너머라 in-process 가 아니다.

    ⚠ 이것이 결함을 **만드는** 것이 아니다. 결함은 「회신을 링크로 안 묶는다」이고,
    RTT 는 그 결함이 실제 배치에서 어느 쪽으로 기우는지를 재현할 뿐이다 — 침해된
    상류는 언제나 자기가 원하는 시점에 쏠 수 있고, 정직한 상류는 자기 RTT 를 못 줄인다."""
    import asyncio as _asyncio
    real_send = srv._send_to

    async def _send_to(c, obj):
        if isinstance(obj, dict) and obj.get("t") == "search_results":
            await _asyncio.sleep(delay)
        return await real_send(c, obj)

    srv._send_to = _send_to


async def test_an_upstream_cannot_answer_for_another_upstream():
    """침해된 상류 C 가 **B 의 자리**에 줄을 심지 못한다.

    심어지면 화면에는 `⇄B:탭` 으로 뜬다 — 사용자가 신뢰하는 호스트의 이름표를
    단 남의 글이고, 그 줄을 고르면 점프가 **B 로** 나간다."""
    (srvA, taskA, sockA, srvB, taskB, sockB, srvC, taskC, sockC,
     sessA, linkB, linkC) = await _triple()
    _add_latency(srvB)                       # B 는 ssh 너머의 진짜 상류다
    _compromise(srvC, "위조 — C 가 지어낸 줄")
    try:
        res = {"items": [], "panes": 0, "truncated": False, "capped_panes": 0,
               "cap": 200, "per_pane": 20}
        await srvA.remote_search_merge(sessA, res, "NEEDLE")
        forged = [it for it in res["items"] if "위조" in it["text"]]
        assert not forged, f"C 의 위조 줄이 목록에 실렸다: {forged}"
        # 그리고 정직한 상류의 진짜 줄은 **그대로 있어야 한다** — 가로채기가 되면
        # B 의 대기표가 먼저 소비돼 진짜 회신이 조용히 버려진다.
        honest = [it for it in res["items"] if "정직한B" in it["text"]]
        assert honest, f"B 의 진짜 결과가 사라졌다: {res['items']}"
        assert srvA._search_waiters() == {}, srvA._search_waiters()
    finally:
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
        await teardown(srvC, taskC, sockC)


async def test_a_negative_hop_count_cannot_defeat_the_loop_guard():
    """`hops` 가 음수면 고리 방어가 없는 것과 같다 — 경계에서 0 으로 올린다.

    재는 것은 **상류로 나가는 `hops`** 다. 음수를 그대로 믿으면 -2**40+1 이 나가고,
    그 값이 상한(4)에 닿으려면 1조 홉이 필요하다 = 방어가 없는 것과 같다. 홉마다
    상류가 전 패널의 스크롤백을 훑으므로 그 되풀이는 싸지 않다(고리 하나면 두 서버가
    서로를 영원히 훑는다). 0 으로 접으면 「그냥 새 검색」이 되고 상한은 4 홉이다."""
    from pytmuxlib.serverremote import ServerRemoteMixin as _RM
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    sessA = srvA.ensure_default_session(80, 24)
    await _fill(sessA.active_window.active_pane, ["로컬 NEEDLE"])
    srvB.ensure_default_session(80, 24)
    assert await srvA.remote_attach(sessA, endpoint=sockB), "A→B attach 실패"
    link = srvA._remotes_dict()[sockB]
    assert await wait_for(lambda: bool(link.windows)), "상류 첫 status 미도착"
    sent = []
    real_write = srvA._link_write

    async def _spy(lk, obj):
        if isinstance(obj, dict) and obj.get("action") == "search_all":
            sent.append(obj.get("hops"))
        return await real_write(lk, obj)

    srvA._link_write = _spy
    try:
        for bad in (-(1 << 40), -1, True, "3", None):
            sent.clear()
            res = {"items": [], "panes": 0, "truncated": False,
                   "capped_panes": 0, "cap": 200, "per_pane": 20}
            await srvA.remote_search_merge(sessA, res, "NEEDLE", hops=bad)
            assert sent and all(isinstance(h, int) and 1 <= h
                                <= _RM._REMOTE_SEARCH_MAX_HOPS for h in sent), \
                f"hops={bad!r} → 상류로 {sent!r} 가 나갔다"
    finally:
        srvA._link_write = real_write
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
