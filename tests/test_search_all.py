"""전역 검색(pytmux-27 ①·②) — 열린 **모든 탭·패널**(로컬 + 원격)을 가로지르는
검색과 점프.

# 무엇을 못박나

`search_pane` 은 **활성 패널 하나 안에서** 다음 히트로 옮겨 다니는 것이고(n/N),
이쪽은 **결과 목록**을 만든다 — 모양이 달라 명령도 다르다. 그래서 여기서 재는 것은
목록의 성질이다:

1. 탭·패널 경계를 넘어 전부 훑는가(활성 패널만 보던 종전 모델의 정확한 반대).
2. 상한에 걸렸을 때 **조용히 자르지 않고 말하는가**(루트 CLAUDE.md 의 no silent caps).
3. 점프가 탭·패널·스크롤 셋을 다 맞추는가.
4. 스크롤백이 밀려 절대 행 번호가 어긋난 뒤에도 **그 히트로** 가는가.
5. 단일 스레드 asyncio 루프를 패널마다 놓아 주는가(안 놓으면 훑는 동안 전 화면 정지).

②(원격 중계)가 더한 것 — 원격 탭의 스크롤백은 **이 서버에 없다**(상류가 갖고 있고
우리는 중계된 화면을 본다). 그래서 재는 것이 하나 더 는다:

6. 같은 검색을 상류에 중계해 **합쳐서 한 벌로** 돌려주는가(조각으로 흘리지 않는가).
7. 회신이 **안 온 상류를 말하는가** — 옛 버전 상류는 이 명령을 몰라 아무 회신도 안
   한다. 그때 조용하면 "저 서버엔 없구나"라는 거짓을 읽는다(no silent caps 의 연장).
8. 상류가 보낸 목록을 **경계에서 정규화**하는가(상류는 신뢰불가다).
9. 원격 히트를 고르면 보기가 그 링크로 돌고 점프가 **그 상류에서** 일어나는가.
10. 고리(A→B→A)에서 팬아웃이 **홉 상한에서 멈추는가**.
11. 2단(A→B→C)에서 **표시 번호**(우리 병합 탭바)와 **점프 좌표**(최종 서버의 로컬
    탭)가 갈린 채로 끝까지 가는가 — 뭉치면 "번호는 맞는데 엉뚱한 탭으로 뛴다".
"""

import asyncio
import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown, wait_for
from test_remote import _attach_client, _read_until
from pytmuxlib.protocol import read_msg, write_msg

# ⛔ 여기 있던 `_skip_if_two_servers_need_a_client_on_windows()` 는 없앴다
# (pytmux/pytmux-152). «서버 둘 + 그중 하나에 클라 attach» 가 Windows 에서 못 서던
# 이유는 ssh 도 PTY 도 아니라 **상태파일 이름**이었다 — TCP 엔드포인트의 상태
# prefix·포트파일·토큰이 전부 상태 디렉터리의 고정 이름(`default`)으로 접혀, 두 번째
# 서버가 첫 서버의 자리를 가져가고 A 에 붙는 클라가 B 의 토큰을 내밀어 `auth_failed`
# 로 끊겼다. 이제 이름이 엔드포인트에 실려 서버마다 갈린다(`ipc.tcp_endpoint`) —
# 그 규약을 못 박는 자리는 `test_endpoint_isolation.py` 이고, 거기서는 플랫폼과
# 무관하게 TCP 전송을 강제로 세워 이 시나리오를 그대로 재현한다.


async def _fill(pane, lines):
    for ln in lines:
        pane.feed((ln + "\r\n").encode())


async def test_search_all_crosses_tabs_and_panes():
    """열린 탭·패널 전부에서 찾는다 — 활성 패널만 보던 `search_pane` 과의 갈림점."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p0 = sess.active_window.active_pane
        await _fill(p0, ["머리말", "탭0 패널0 NEEDLE", "꼬리말"])
        srv.split_pane(sess, "lr")                       # 탭0 에 패널 하나 더
        p1 = sess.active_window.active_pane
        # 분할로 난 패널은 MIN_W(3열)로 태어나 붙은 클라의 레이아웃이 키운다 —
        # 클라 없는 스위트에서는 3열이라 문장이 세 글자씩 접혀 검색어가 잘린다.
        p1.resize(80, 24)
        await _fill(p1, ["탭0 패널1 NEEDLE"])
        srv.new_window(sess)                             # 탭1
        p2 = sess.active_window.active_pane
        await _fill(p2, ["탭1 패널0 NEEDLE"])

        res = await srv.search_all_panes(sess, "NEEDLE")
        got = {(it["win"], it["pane"]) for it in res["items"]}
        assert got == {(0, p0.id), (0, p1.id), (1, p2.id)}, got
        assert res["panes"] == 3, ("훑은 패널 수", res["panes"])
        assert not res["truncated"] and res["capped_panes"] == 0
        # 목록 줄은 그 자리로 되돌아갈 재료를 전부 들고 있어야 한다.
        one = next(it for it in res["items"] if it["pane"] == p2.id)
        assert one["wid"] == sess.tabs[1].wid and one["tab"] == sess.tabs[1].name
        assert "NEEDLE" in one["text"] and isinstance(one["line"], int)
    finally:
        await teardown(srv, task, sock)


async def test_search_all_finds_wide_chars_like_the_pane_search_does():
    """와이드 글자(한글)가 화면에 보이는 대로 잡혀야 한다.

    `_pane_text_lines` 가 stub 을 공백으로 접던 시절 `search_pane` 이 밟은 그 함정을
    (`뜨 면`) 전역 검색도 그대로 물려받는다 — 같은 텍스트 원천을 쓰기 때문이다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        await _fill(p, ["앞줄", "뜨면 알려주세요", "뒷줄"])
        res = await srv.search_all_panes(sess, "뜨면 알려주세요")
        assert len(res["items"]) == 1, res["items"]
        assert "뜨면 알려주세요" in res["items"][0]["text"]
    finally:
        await teardown(srv, task, sock)


async def test_search_all_says_it_when_a_cap_bites():
    """상한은 있어도 된다. **말 안 하는 것**이 안 된다(no silent caps)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        await _fill(p, [f"HIT {i}" for i in range(40)])

        # ① 패널당 상한 — 그 패널이 목록을 독차지하지 않게 자르고, 잘랐다고 센다.
        res = await srv.search_all_panes(sess, "HIT", limit=1000, per_pane=5)
        assert len(res["items"]) == 5, len(res["items"])
        assert res["capped_panes"] == 1, res
        assert not res["truncated"], "전체 상한엔 안 걸렸다"

        # ② 전체 상한 — 걸리면 truncated 로 말한다.
        res = await srv.search_all_panes(sess, "HIT", limit=3, per_pane=100)
        assert len(res["items"]) == 3 and res["truncated"], res
        assert res["cap"] == 3 and res["per_pane"] == 100, res
    finally:
        await teardown(srv, task, sock)


async def test_search_all_ignores_an_empty_query():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        await _fill(sess.active_window.active_pane, ["아무 줄"])
        for q in ("", "   ", None):
            res = await srv.search_all_panes(sess, q)
            assert res["items"] == [] and res["panes"] == 0, (q, res)
    finally:
        await teardown(srv, task, sock)


async def test_search_all_yields_the_loop_between_panes():
    """서버는 단일 스레드 asyncio 루프다 — 패널 수 × 스크롤백을 한 번에 훑으면
    그동안 **모든 화면이 멎는다**. 패널마다 루프에 양보하는지 센다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        await _fill(sess.active_window.active_pane, ["NEEDLE"])
        srv.split_pane(sess, "lr")
        srv.new_window(sess)

        # 코루틴을 **손으로 몰아** 몇 번 멈춰 서는지 센다. 전역
        # `asyncio.sleep` 을 갈아끼우지 않는 이유: 같은 루프에서 flush 루프가 함께
        # 돌아 남의 양보까지 세게 되고, 그 치환이 새면 뒤 모듈이 통째로 무너진다
        # (harness.patched 의 그 사고).
        coro = srv.search_all_panes(sess, "NEEDLE")
        stops, res = 0, None
        try:
            while True:
                coro.send(None)
                stops += 1
                assert stops < 1000, "양보만 하고 끝나지 않는다"
        except StopIteration as done:
            res = done.value
        assert stops >= res["panes"] >= 3, (stops, res["panes"])
    finally:
        await teardown(srv, task, sock)


async def test_search_goto_lands_on_the_tab_pane_and_scroll():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)                     # 탭1(활성)
        target = sess.active_window.active_pane
        await _fill(target, [f"L{i}" for i in range(80)] + ["여기 NEEDLE"]
                    + [f"M{i}" for i in range(80)])
        srv.select_window(sess, 0)               # 다른 탭에 가 있다가
        res = await srv.search_all_panes(sess, "NEEDLE")
        hit = next(it for it in res["items"] if it["pane"] == target.id)

        assert srv.search_goto(sess, wid=hit["wid"], win=hit["win"],
                               pane=hit["pane"], line=hit["line"],
                               query="NEEDLE") is True
        assert sess.active_index == 1, "그 탭으로"
        assert sess.active_window.active_pane is target, "그 패널로"
        assert target.scroll > 0, "라이브 하단이 아니라 그 자리로 스크롤"
        assert target._match_abs == hit["line"], "n/N 이 그 자리에서 이어진다"
        assert target.search_query == "NEEDLE"
    finally:
        await teardown(srv, task, sock)


async def test_search_goto_relocates_when_the_scrollback_has_moved():
    """목록의 절대 행 번호는 **검색 시점의 스냅샷**이다.

    그 뒤 그 패널이 출력을 더 내면 스크롤백 상한에서 옛 줄이 밀려 나가 번호가 통째로
    당겨진다 — 번호를 그대로 믿으면 엉뚱한 줄로 뛴다. 같은 검색어를 번호에서 가장
    가까운 곳에서 다시 찾는지 본다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        await _fill(p, ["앞", "여기 NEEDLE", "뒤"])
        res = await srv.search_all_panes(sess, "NEEDLE")
        line = res["items"][0]["line"]
        # 앞에 줄을 끼워 넣어 **번호를 어긋나게** 한다(밀림과 같은 효과).
        stale = line + 25
        assert srv.search_goto(sess, wid=sess.tabs[0].wid, win=0, pane=p.id,
                               line=stale, query="NEEDLE") is True
        assert p._match_abs == line, ("어긋난 번호를 그대로 믿었다", p._match_abs)
    finally:
        await teardown(srv, task, sock)


async def test_search_goto_refuses_a_pane_that_is_gone():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        assert srv.search_goto(sess, wid=None, win=0, pane=999999, line=0,
                               query="x") is False
        assert srv.search_goto(sess, wid=None, win=42, pane=1, line=0,
                               query="x") is False
    finally:
        await teardown(srv, task, sock)


# ─────────────────────────────────────────────────────────────────────────────
# ② 원격 중계 — 상류의 스크롤백까지 합친다
#
# 구성은 test_remote 와 같다: ssh 없이 in-process 서버 2대(이상)를 **실 소켓으로
# 직결**해 와이어 전 구간을 지난다. 이 갈래에서만 드러나는 결함(토큰 라우팅·상류
# 좌표계·시한)은 서버 API 만 불러서는 안 잡힌다.
# ─────────────────────────────────────────────────────────────────────────────


async def _linked_pair(needle="NEEDLE"):
    """A →(직결) B 를 세우고 양쪽에 히트를 심는다 → (srvA, taskA, sockA, srvB, …).

    A 는 탭 하나(로컬 히트 1건), B 는 탭 하나(원격 히트 1건)다. 병합 탭바에서 B 의
    탭은 A 의 로컬 탭 **뒤**에 붙으므로 병합 번호는 len(A.tabs) 부터다."""
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    sessA = srvA.ensure_default_session(80, 24)
    await _fill(sessA.active_window.active_pane, ["로컬 " + needle])
    sessB = srvB.ensure_default_session(80, 24)
    await _fill(sessB.active_window.active_pane, ["원격 " + needle])
    assert await srvA.remote_attach(sessA, endpoint=sockB), "A→B attach 실패"
    link = srvA._remotes_dict()[sockB]
    assert await wait_for(lambda: bool(link.windows)), "상류 첫 status 미도착"
    return srvA, taskA, sockA, srvB, taskB, sockB, sessA, sessB, link


async def test_search_all_relays_to_the_upstream_and_merges_one_list():
    """①이 못 하던 것: **원격 탭의 스크롤백**까지 한 목록에 들어온다.

    그리고 조각으로 흘리지 않는다 — 클라가 받는 `search_results` 는 **한 벌**이다
    (상류 회신을 그대로 재방송하면 결과 판이 상류 수만큼 열린다)."""
    srvA, taskA, sockA, srvB, taskB, sockB, sessA, sessB, link = \
        await _linked_pair()
    reader = writer = None
    try:
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "search_all",
                                 "query": "NEEDLE"})
        res = await _read_until(reader, lambda m: m.get("t") == "search_results",
                                what="search_results")
        kinds = [(it.get("route") or [], it["text"]) for it in res["items"]]
        assert len(kinds) == 2, kinds
        loc = next(it for it in res["items"] if not it.get("route"))
        rem = next(it for it in res["items"] if it.get("route"))
        assert "로컬" in loc["text"] and loc["wid"] == sessA.tabs[0].wid
        # 원격 줄: 홉 사슬 · ⇄ 이름 · 병합 탭 번호(gwin) · **상류 좌표**(win/wid/pane).
        assert rem["route"] == [sockB], rem
        assert rem["tab"].startswith("⇄") and "원격" in rem["text"]
        assert rem["remote"] is True
        assert rem["gwin"] == len(sessA.tabs), (rem["gwin"], len(sessA.tabs))
        assert rem["wid"] == sessB.tabs[0].wid, "상류의 탭 wid 그대로"
        assert rem["pane"] == sessB.active_window.active_pane.id
        # 상류가 답했다는 사실도 실린다(빠진 곳이 없으면 miss 도 없다).
        assert res["hosts"] == [{"host": sockB, "state": "ok", "n": 1}], res["hosts"]
        # 회신은 **한 벌뿐**이다 — 상류 회신이 따로 새지 않는다.
        await _assert_single_reply(reader)
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def _assert_single_reply(reader, window=0.4):
    """window 초 동안 `search_results` 가 **더** 오지 않음을 단언."""
    end = asyncio.get_running_loop().time() + window
    while asyncio.get_running_loop().time() < end:
        try:
            msg = await asyncio.wait_for(read_msg(reader), 0.1)
        except asyncio.TimeoutError:
            continue
        if msg is None:
            return
        assert msg.get("t") != "search_results", "결과가 두 번 왔다(조각 누출)"


async def test_search_all_says_which_upstream_did_not_answer():
    """옛 버전 상류는 이 명령을 **모른다** — 그때 아무 회신도 안 온다.

    ①이 ②를 미룬 이유가 정확히 이것이었다(무한 대기 = "쳐도 아무 일이 없다").
    기다리는 쪽이 시한을 쥐고, 안 온 곳은 **결과에 적어서** 돌려준다."""
    from pytmuxlib.servercmd import _CMD_TABLE
    srvA, taskA, sockA, srvB, taskB, sockB, sessA, sessB, link = \
        await _linked_pair()
    entry = _CMD_TABLE.pop("search_all")       # B 를 "이 명령을 모르는 서버"로
    srvA._REMOTE_SEARCH_TIMEOUT = 0.3
    try:
        res = {"items": [], "panes": 0, "truncated": False, "capped_panes": 0,
               "cap": 200, "per_pane": 20}
        await srvA.remote_search_merge(sessA, res, "NEEDLE")
        assert res["hosts"] == [{"host": sockB, "state": "timeout", "n": 0}], res
        assert res["items"] == [], "회신도 없이 원격 줄이 생겼다"
        # 기다림이 끝난 뒤 대기표가 남지 않는다(누수 = 서버 수명만큼 쌓인다).
        assert srvA._search_waiters() == {}, srvA._search_waiters()
    finally:
        _CMD_TABLE["search_all"] = entry
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_search_all_sanitizes_what_the_upstream_sends():
    """상류는 **신뢰불가**다(_sanitize_windows 와 같은 규율).

    좌표류가 int 가 아니면 그 줄을 버리고(그 값이 그대로 index 연산·릴레이에 들어
    간다), 표시 문자열의 제어문자는 접고 길이는 자른다(결과 판이 그대로 렌더한다),
    route 는 홉 상한까지만 받는다(고리가 우리 릴레이 경로가 되지 않게)."""
    from pytmuxlib.serverremote import ServerRemoteMixin as _RM
    san = _RM._sanitize_search_items
    assert san("not a list") == [] and san([1, "x", None]) == []
    # 좌표 결손/형 오류 → 버린다.
    assert san([{"win": "1", "pane": 2, "line": 3}]) == []
    assert san([{"win": 1, "pane": True, "line": 3}]) == []
    assert san([{"win": 1, "pane": 2, "line": -1}]) == []
    got = san([{"win": 1, "pane": 2, "line": 3,
                "text": "\x1b[2J\x1b[H pwned" + "x" * 900,
                "title": "탭\r줄", "route": ["a"] * 40, "wid": [1]}])[0]
    assert "\x1b" not in got["text"] and "\r" not in got["title"]
    assert len(got["text"]) <= _RM._REMOTE_SEARCH_TEXT_MAX
    assert len(got["route"]) <= _RM._REMOTE_SEARCH_MAX_HOPS
    assert got["wid"] is None, "해시불가/비int wid 는 없는 것으로 낮춘다"
    assert got["gwin"] == 1, "gwin 이 없으면 win 이 곧 표시 자리다"
    # 항목 수도 상한이 있다(무제한 목록 = 다운스트림이 그리다 언다).
    many = san([{"win": 0, "pane": 0, "line": 0}] * 5000)
    assert len(many) == _RM._REMOTE_SEARCH_ITEMS_MAX, len(many)


async def test_search_goto_into_a_remote_hit_switches_view_and_jumps_upstream():
    """원격 줄을 고르면 ⑴ 보기가 그 링크로 돌고 ⑵ 점프는 **상류에서** 일어난다.

    ⛔ 보기를 먼저 돌려야 상류 프레임이 이 클라에 전달된다 — 뒤에 돌리면 프레임이
    버려져 "골랐는데 화면이 안 바뀐다"가 된다."""
    srvA, taskA, sockA, srvB, taskB, sockB, sessA, sessB, link = \
        await _linked_pair()
    reader = writer = None
    try:
        # B 에 탭을 하나 더 만들어 **두 번째 탭**의 줄을 겨냥한다(탭 전환까지 재려고).
        srvB.new_window(sessB)
        pB = sessB.active_window.active_pane
        pB.resize(80, 24)
        # 히트를 라이브 하단에서 **충분히 멀리** 둔다 — 화면 안(24행)에 있으면
        # 점프해도 스크롤이 0 이라 "갔다"를 스크롤로 못 잰다.
        await _fill(pB, ["머리말"] * 60 + ["여기 NEEDLE"] + ["꼬리말"] * 60)
        srvB.select_window(sessB, 0)                     # 다시 첫 탭으로
        assert await wait_for(lambda: len(link.windows) == 2), link.windows

        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "search_all",
                                 "query": "여기 NEEDLE"})
        res = await _read_until(reader, lambda m: m.get("t") == "search_results",
                                what="search_results")
        hit = next(it for it in res["items"] if it.get("route"))
        await write_msg(writer, dict(hit, t="cmd", action="search_goto",
                                     query="여기 NEEDLE"))
        assert await wait_for(lambda: sessB.active_index == 1), sessB.active_index
        target = sessB.tabs[1].window.active_pane
        assert await wait_for(lambda: target.scroll > 0), "그 자리로 스크롤"
        assert target.search_query == "여기 NEEDLE"
        # 보기가 그 링크로 돌았다 → 상류 화면이 이 클라에 전달된다.
        conn = next(c for c in srvA.clients if c.remote_view)
        assert conn.remote_view == sockB, conn.remote_view
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_search_goto_refuses_a_route_whose_link_is_gone():
    """링크가 없으면 **말하고 멈춘다** — 로컬 폴백은 없다.

    남의 서버 좌표를 우리 탭에 대고 뛰면 엉뚱한 자리로 간다. select_window 의 로컬
    폴백과 갈리는 지점이다(그쪽 index 는 우리 공간이다)."""
    srv, task, sock = await server_only()
    reader = writer = None
    try:
        srv.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sock)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "search_goto",
                                 "route": ["없는호스트"], "wid": 1, "win": 0,
                                 "pane": 1, "line": 0, "query": "x"})
        note = await _read_until(reader, lambda m: m.get("t") == "notice",
                                 what="notice")
        assert note["key"] == "rnotice.search_goto_gone", note
    finally:
        if writer is not None:
            writer.close()
        await teardown(srv, task, sock)


async def test_search_all_stops_fanning_out_at_the_hop_cap():
    """고리(A→B→A)에서 팬아웃이 영원히 번지지 않는다.

    ⛔ 자기 endpoint attach 만 막혀 있지 **고리는 막혀 있지 않다**. 홉을 세어
    상한에서 멈추고 그 사실도 결과에 싣는다(안 실으면 조용한 누락이 된다)."""
    srvA, taskA, sockA, srvB, taskB, sockB, sessA, sessB, link = \
        await _linked_pair()
    try:
        res = {"items": [], "panes": 0, "truncated": False, "capped_panes": 0,
               "cap": 200, "per_pane": 20}
        await srvA.remote_search_merge(sessA, res, "NEEDLE",
                                       hops=srvA._REMOTE_SEARCH_MAX_HOPS)
        assert res["hops_capped"] is True, res
        assert res["hosts"] == [{"host": sockB, "state": "hops", "n": 0}], res
        assert res["items"] == []
    finally:
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_search_all_counts_hits_in_tabs_this_view_has_hidden():
    """이 뷰에서 **숨긴** 상류 탭(remote-detach 단일 탭)의 히트는 뛸 곳이 없다.

    탭바에 없는 자리라 목록에 실을 수 없다 — 그렇다고 조용히 지우면 "저기엔 없구나"
    가 된다. 세어서 말한다."""
    srvA, taskA, sockA, srvB, taskB, sockB, sessA, sessB, link = \
        await _linked_pair()
    try:
        link.detached_windows.add(srvA._win_key(link.windows[0]))
        res = {"items": [], "panes": 0, "truncated": False, "capped_panes": 0,
               "cap": 200, "per_pane": 20}
        await srvA.remote_search_merge(sessA, res, "NEEDLE")
        assert res["items"] == [], "숨긴 탭의 줄이 목록에 실렸다"
        assert res["hosts"][0]["hidden"] == 1, res["hosts"]
        assert res["hosts"][0]["state"] == "ok"
    finally:
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_search_all_is_no_longer_refused_while_viewing_a_remote_tab():
    """①에서는 원격 보기 중 **거부**였다(로컬만 훑던 명령이라 §1.7-c 였다).

    ②가 그 전제를 없앴다 — 이제 로컬 + 전 상류를 합쳐 돌려주고 줄마다 어느 탭인지를
    이름으로 밝힌다. 거부가 남아 있으면 "원격 탭을 보는 중엔 전역 검색이 안 된다"는
    정확히 반대의 화면이 된다."""
    from pytmuxlib.serverremote import _REMOTE_BLOCK_ACTIONS
    assert "search_all" not in _REMOTE_BLOCK_ACTIONS
    assert "search_goto" not in _REMOTE_BLOCK_ACTIONS
    srvA, taskA, sockA, srvB, taskB, sockB, sessA, sessB, link = \
        await _linked_pair()
    reader = writer = None
    try:
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": len(sessA.tabs)})   # 원격 탭 진입
        assert await wait_for(
            lambda: any(c.remote_view for c in srvA.clients)), "원격 보기 미진입"
        await write_msg(writer, {"t": "cmd", "action": "search_all",
                                 "query": "NEEDLE"})
        res = await _read_until(reader, lambda m: m.get("t") == "search_results",
                                what="search_results")
        assert len(res["items"]) == 2, res["items"]
        # ⛔ HANDLED 라 로컬 화면을 덮어쓰지 않는다 — 보기는 그대로 원격이다.
        conn = next(c for c in srvA.clients if c.remote_view)
        assert conn.remote_view == sockB
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_search_all_reaches_across_a_cascade_and_jumps_back_through_it():
    """2단(A→B→C) — 팬아웃도 점프도 **홉을 그대로 되짚는다**.

    여기가 두 좌표계를 섞으면 곧바로 드러나는 자리다: 표시 번호(`gwin`, 보는 쪽 병합
    탭바)와 점프 좌표(`win`/`wid`, **최종 서버**의 로컬 탭)가 캐스케이드에서 갈린다.
    하나로 뭉치면 "번호는 맞는데 엉뚱한 탭으로 뛴다"가 된다."""
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    srvC, taskC, sockC = await server_only()
    reader = writer = None
    try:
        sessA = srvA.ensure_default_session(80, 24)
        sessB = srvB.ensure_default_session(80, 24)
        sessC = srvC.ensure_default_session(80, 24)
        pC = sessC.active_window.active_pane
        await _fill(pC, ["머리말"] * 60 + ["끝단 NEEDLE"] + ["꼬리말"] * 60)
        # B→C 를 먼저(서버 API — remote_attach 는 릴레이 대상이 아니다), 그다음 A→B.
        assert await srvB.remote_attach(sessB, endpoint=sockC), "B→C attach 실패"
        assert await srvA.remote_attach(sessA, endpoint=sockB), "A→B attach 실패"
        link = srvA._remotes_dict()[sockB]
        assert await wait_for(lambda: len(link.windows) >= 2), link.windows

        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "search_all",
                                 "query": "끝단 NEEDLE"})
        res = await _read_until(reader, lambda m: m.get("t") == "search_results",
                                what="search_results")
        hit = next(it for it in res["items"] if it.get("route"))
        # 홉 사슬은 A 가 쥔 링크(B)부터 최종 서버(C)까지 — 한 홉도 접히지 않는다.
        assert hit["route"] == [sockB, sockC], hit["route"]
        # 이름은 ⇄ 를 한 번만 찍고 홉을 ':' 로 잇는다(§10-15 구멍 2 규약).
        assert hit["tab"].startswith(f"⇄{sockB}:{sockC}:"), hit["tab"]
        # 점프 좌표는 **C 의 것**이고 표시 번호는 **A 의 병합 탭바** 것이다.
        assert hit["wid"] == sessC.tabs[0].wid, "최종 서버의 탭 wid"
        assert hit["pane"] == pC.id
        assert hit["gwin"] >= len(sessA.tabs), hit["gwin"]

        await write_msg(writer, dict(hit, t="cmd", action="search_goto",
                                     query="끝단 NEEDLE"))
        assert await wait_for(lambda: pC.scroll > 0), "C 에서 그 자리로 스크롤"
        assert pC.search_query == "끝단 NEEDLE"
        # 보기는 A→B 로, B 안에서는 C 로 돈다(2단 뷰가 그대로 선다).
        connA = next(c for c in srvA.clients if c.remote_view)
        assert connA.remote_view == sockB
        assert await wait_for(
            lambda: any(c.remote_view == sockC for c in srvB.clients)), \
            "B 의 페더레이션 클라(=A)가 C 를 보게 돌지 않았다"
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
        await teardown(srvC, taskC, sockC)


async def test_search_all_says_how_many_upstream_hits_the_cap_dropped():
    """전체 상한이 먼저 차면 상류가 답한 줄을 못 싣는다 — **그것도 말한다**.

    두 갈래를 함께 잰다:
      ⑴ 자리가 아예 없으면(예산 0) 묻지도 않고 `skipped` 로 적는다.
      ⑵ **상류가 우리 limit 을 안 지키면**(신뢰불가 — 구버전이거나 침해) 넘치는 줄을
        버리는데, 그때 `dropped` 로 센다. ⛔ 그 자리에서 return 하면 뒤 상류들이
        `hosts` 에서 통째로 사라져 "저기엔 없구나"로 읽힌다 — 세고 끝까지 돈다.
    """
    srvA, taskA, sockA, srvB, taskB, sockB, sessA, sessB, link = \
        await _linked_pair()
    try:
        # ⑴ 예산 0 — 물어보지 않았다는 사실을 적는다.
        res = {"items": [{"자리": "이미 찼다"}], "panes": 0, "truncated": False,
               "capped_panes": 0, "cap": 1, "per_pane": 20}
        await srvA.remote_search_merge(sessA, res, "NEEDLE")
        assert res["hosts"] == [{"host": sockB, "state": "skipped", "n": 0}], res
        assert len(res["items"]) == 1, "안 물어봤는데 줄이 늘었다"

        # ⑵ limit 을 무시하는 상류 — 남은 자리(1)보다 많이 보낸다.
        wid, pane = sessB.tabs[0].wid, sessB.active_window.active_pane.id
        async def _flood(_link, _q, _hops, _limit):
            return "ok", {"t": "search_results", "panes": 1, "items": [
                {"win": 0, "wid": wid, "pane": pane, "line": i,
                 "title": "sh", "text": f"넘치는 {i}"} for i in range(6)]}
        srvA._remote_search_ask = _flood
        res = {"items": [{"자리": "이미 찼다"}], "panes": 0, "truncated": False,
               "capped_panes": 0, "cap": 2, "per_pane": 20}
        await srvA.remote_search_merge(sessA, res, "NEEDLE")
        assert len(res["items"]) == 2, ("상한을 넘겨 실었다", len(res["items"]))
        assert res["truncated"] is True
        info = res["hosts"][0]
        assert info["state"] == "ok" and info["n"] == 1, info
        assert info["dropped"] == 5, ("못 실은 수를 안 셌다", info)
    finally:
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
