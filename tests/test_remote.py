"""§1.7 Stage 2·3 페더레이션 — 원격 pytmux 탭 흡수(서버=업스트림 클라이언트).

ssh 없이 **in-process 서버 2대(이상)를 실 소켓으로 직결**해 전 구간(와이어 2홉)을
검증한다: 로컬 A 의 실 클라 연결에서 remote_attach(B 엔드포인트) → 탭바 병합(⇄) →
원격 탭 선택(전역 index) → 업스트림 화면 전달 → 입력 릴레이 → 로컬 복귀/해제/링크
사망. Stage 3: per-client status(원격 탭 active 하이라이트·업스트림 부가필드 전달)·
끊김 백오프 자동 재연결(+명시 detach 취소)·re-exec 복원 spec·다중 원격·자기 attach
거부."""
import asyncio
import base64
import os
import tempfile
import time

import harness
from harness import server_only, teardown
from pytmuxlib import ipc
from pytmuxlib.protocol import PROTO_VERSION, read_msg, write_msg


async def _attach_client(sock):
    """실 클라처럼 hello 로 attach 해 (reader, writer) 반환(초기 full 은 호출부가 소비)."""
    reader, writer = await ipc.open_connection(sock)
    hello = {"t": "hello", "proto": PROTO_VERSION, "cols": 80, "rows": 24}
    tok = ipc.read_token(sock)
    if tok:
        hello["token"] = tok
    await write_msg(writer, hello)
    return reader, writer


async def _read_until(reader, pred, timeout=8.0, what="msg"):
    end = time.monotonic() + timeout
    seen = []
    while time.monotonic() < end:
        msg = await asyncio.wait_for(read_msg(reader),
                                     max(0.1, end - time.monotonic()))
        if msg is None:
            raise AssertionError(f"connection closed waiting {what}: {seen}")
        seen.append(msg.get("t"))
        if pred(msg):
            return msg
    raise AssertionError(f"timeout waiting {what}: {seen}")


def _rows_text(rows):
    return "\n".join("".join(seg[0] for seg in row) for row in rows)


async def test_remote_attach_merge_select_input_detach():
    """E2E: attach→탭바 병합→원격 탭 진입(화면 전달)→입력 릴레이→로컬 복귀→해제."""
    if os.name == "nt":
        return  # in-process 페더레이션 코어는 POSIX 소켓 기준으로 검증
    srvA, taskA, sockA = await server_only()     # 로컬
    srvB, taskB, sockB = await server_only()     # 원격(같은 프로세스, 실 소켓)
    reader = writer = None
    try:
        # 원격 B: 마커 출력. (입력 스파이는 ③ 직전에 설치 — attach 시 B 의
        # _induce_redraw_all 이 pty.set_winsize 를 부르므로 실 pty 가 필요.)
        sessB = srvB.ensure_default_session(80, 24)
        pB = sessB.active_window.active_pane
        pB.feed(b"REMOTE-MARKER-XYZ\r\n")
        realB = pB.pty
        writesB = []

        class _Spy:
            def write(self, b):
                writesB.append(b)

            def set_winsize(self, rows, cols):
                pass

        # 로컬 A 에 실 클라 attach (초기 full: layout→screen…→status)
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        st0 = await _read_until(reader, lambda m: m.get("t") == "status",
                                what="initial status")
        n_local = len(st0["windows"])
        assert not any(w["name"].startswith("⇄") for w in st0["windows"])

        # ① remote_attach(B 엔드포인트 직결) → 탭바에 ⇄ 병합 status
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="merged status")
        rtabs = [w for w in stm["windows"] if w["name"].startswith("⇄")]
        assert len(rtabs) == 1 and rtabs[0]["index"] == n_local, rtabs

        # ② 원격 탭 진입(전역 index) → 업스트림 layout/screen 이 전달돼 마커가 보인다
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": n_local})
        lay = await _read_until(reader, lambda m: m.get("t") == "layout",
                                what="remote layout")
        rid = lay["active"]
        assert rid == pB.id, ("원격 레이아웃의 활성 패널 = B 패널", rid, pB.id)
        scr = await _read_until(
            reader, lambda m: m.get("t") == "screen" and m.get("pane") == rid,
            what="remote screen")
        assert "REMOTE-MARKER-XYZ" in _rows_text(scr["rows"])

        # ③ 입력 릴레이: 보는 중 input → B 패널 PTY 로 도달
        pB.pty = _Spy()
        await write_msg(writer, {"t": "input", "pane": rid,
                                 "data": base64.b64encode(b"echo hi\r").decode()})
        for _ in range(80):
            if writesB:
                break
            await asyncio.sleep(0.05)
        assert writesB and b"echo hi" in writesB[0], writesB

        # ④ 로컬 탭 복귀: select_window(0) → 로컬 layout(로컬 패널 id)
        pA = srvA.sessions and list(srvA.sessions.values())[0]
        pA_id = pA.active_window.active_pane.id
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": 0})
        lay2 = await _read_until(
            reader, lambda m: m.get("t") == "layout" and m.get("active") == pA_id,
            what="local layout back")
        assert lay2["active"] == pA_id

        # ⑤ 해제: remote_detach → ⇄ 사라진 status
        await write_msg(writer, {"t": "cmd", "action": "remote_detach"})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and not any(w["name"].startswith("⇄") for w in m["windows"]),
            what="detached status")
        pB.pty = realB
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_new_tab_spawns_window_and_attaches():
    """remote-new-tab <host>: 미어태치 호스트면 먼저 attach 한 뒤 원격에 **새 window**를
    만들어 새 ⇄ 탭(active)으로 붙이고, 그 새 창 화면을 뷰어에 전달한다. remote-attach
    가 기존 탭 병합·열람만 하는 것과 달리 원격에 새 셸을 띄우는 게 핵심."""
    if os.name == "nt":
        return  # in-process 페더레이션 코어는 POSIX 소켓 기준
    srvA, taskA, sockA = await server_only()     # 로컬
    srvB, taskB, sockB = await server_only()     # 원격(같은 프로세스, 실 소켓)
    reader = writer = None
    try:
        sessB = srvB.ensure_default_session(80, 24)
        assert len(sessB.tabs) == 1              # 원격 B 는 기본 창 1개

        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        st0 = await _read_until(reader, lambda m: m.get("t") == "status",
                                what="initial status")
        assert not any(w["name"].startswith("⇄") for w in st0["windows"])

        # remote-new-tab (B 미어태치, 엔드포인트 직결) → B attach + 새 창 생성
        await write_msg(writer, {"t": "cmd", "action": "remote_new_window",
                                 "endpoint": sockB})

        # 원격 B 에 새 window 가 생겨 창이 2개(원격측 권위 확인)
        for _ in range(160):
            if len(sessB.tabs) == 2:
                break
            await asyncio.sleep(0.05)
        assert len(sessB.tabs) == 2, "원격 B 에 새 window 생성"
        newpane = sessB.active_window.active_pane.id   # new_window 가 활성화한 새 창

        # 새 창 화면(layout)이 뷰어에 전달된다 — 활성 패널 = B 신규 창 패널.
        # (특정 pane id 로 매칭해 attach 시 흘러온 기존 창 layout 과 무관하게 견고)
        lay = await _read_until(
            reader, lambda m: m.get("t") == "layout" and m.get("active") == newpane,
            what="new remote window layout")
        assert lay["active"] == newpane

        # 병합 status 에 ⇄ 탭 2개(기존+신규), 모두 remote=True, 신규가 active.
        stm = await _read_until(
            reader,
            lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 2,
            what="merged 2 remote tabs")
        rtabs = [w for w in stm["windows"] if w["name"].startswith("⇄")]
        assert all(w.get("remote") for w in rtabs), rtabs
        assert any(w.get("active") for w in rtabs), ("신규 원격 탭 active", rtabs)
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_attach_failure_sends_notice():
    """실패가 서버 로그에만 남아 '아무 일도 안 일어남'으로 보이던 갭(사용자 보고):
    remote_attach 가 실패하면 요청 클라에 notice(원인 포함)가 회신된다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    reader = writer = None
    try:
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        # 존재하지 않는 endpoint → 즉시 실패 + notice
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": "/nonexistent/no.sock"})
        n = await _read_until(reader, lambda m: m.get("t") == "notice",
                              what="failure notice")
        assert "실패" in n.get("text", ""), n
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)


async def test_remote_tab_active_highlight_and_status_passthrough():
    """Stage 3 per-client status: 원격 탭을 보는 클라는 ① ⇄ 탭이 active(업스트림
    active 보존)·로컬 탭 전부 비활성, ② 업스트림 status 부가필드(pane_title 등 —
    Claude 헤더/토큰도 같은 경로)가 그대로 전달된다. 안 보는 클라는 종전 로컬
    status(⇄ 비활성·로컬 active)다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = reader2 = writer2 = None
    try:
        sessB = srvB.ensure_default_session(80, 24)
        pB = sessB.active_window.active_pane
        pB.title = "B-PANE-TITLE"          # 업스트림 식별 마커(passthrough 검증)
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="merged status")
        gidx = next(w["index"] for w in stm["windows"]
                    if w["name"].startswith("⇄"))
        # 진입 → 업스트림 status 기반 머지본(pane_title 마커)이 도착할 때까지
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        stv = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and m.get("pane_title") == "B-PANE-TITLE",
            what="viewer status with upstream fields")
        rtabs = [w for w in stv["windows"] if w["name"].startswith("⇄")]
        ltabs = [w for w in stv["windows"] if not w["name"].startswith("⇄")]
        assert any(w["active"] for w in rtabs), ("원격 탭 하이라이트", rtabs)
        assert not any(w["active"] for w in ltabs), ("로컬 탭 비활성", ltabs)
        # 안 보는 둘째 클라: 종전 로컬 status — 로컬 active·⇄ 비활성·로컬 pane_title
        reader2, writer2 = await _attach_client(sockA)
        st2 = await _read_until(reader2, lambda m: m.get("t") == "status",
                                what="2nd client status")
        assert st2.get("pane_title") != "B-PANE-TITLE"
        assert not any(w["active"] for w in st2["windows"]
                       if w["name"].startswith("⇄"))
        assert any(w["active"] for w in st2["windows"]
                   if not w["name"].startswith("⇄"))
    finally:
        for w in (writer, writer2):
            if w is not None:
                w.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_reconnect_backoff_remerges_tab():
    """Stage 3 자동 재연결: 링크가 비명시적으로 죽으면(EOF) 백오프 후 재연결을
    시도하고, 성공하면 notice + ⇄ 탭이 다시 병합된다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        srvA._RECONNECT_DELAYS = (0.05, 0.1)   # 테스트 가속(인스턴스 한정)
        srvB.ensure_default_session(80, 24)
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="merged status")
        # 비명시적 죽음(EOF): B 측 연결 닫기 → A 가 ⇄ 제거 후 자동 재연결
        for cB in list(srvB.clients):
            try:
                cB.writer.close()
            except OSError:
                pass
        await _read_until(
            reader, lambda m: m.get("t") == "notice"
            and "자동 재연결" in m.get("text", ""),
            what="reconnect notice")
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="re-merged status")
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_detach_cancels_pending_reconnect():
    """Stage 3: 명시 remote-detach 는 보류 중인 자동 재연결을 취소한다(사용자
    의사 우선 — 백그라운드 ssh 재시도가 남지 않는다)."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        srvA._RECONNECT_DELAYS = (30,)   # 절대 발화 전 취소되도록 길게
        srvB.ensure_default_session(80, 24)
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="merged status")
        for cB in list(srvB.clients):
            try:
                cB.writer.close()
            except OSError:
                pass
        for _ in range(80):                       # drop → 재연결 예약 대기
            if srvA._remote_reconn_dict():
                break
            await asyncio.sleep(0.05)
        assert srvA._remote_reconn_dict(), "재연결이 예약되어야 함"
        await write_msg(writer, {"t": "cmd", "action": "remote_detach"})
        for _ in range(80):
            if not srvA._remote_reconn_dict():
                break
            await asyncio.sleep(0.05)
        assert not srvA._remote_reconn_dict(), "detach 가 재연결을 취소해야 함"
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_resume_payload_and_restore_links():
    """Stage 3 re-exec 복원: ① _resume_payload 에 링크 spec 이 실리고 ② 새 서버가
    remote_restore_links 로 그 spec 을 재연결해 ⇄ 탭이 복원된다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    srvC, taskC, sockC = await server_only()
    reader = writer = None
    try:
        srvB.ensure_default_session(80, 24)
        sessA = srvA.ensure_default_session(80, 24)
        ok = await srvA.remote_attach(sessA, endpoint=sockB)
        assert ok
        specs = srvA._resume_payload().get("remotes")
        assert specs == [{"host": None, "endpoint": sockB}], specs
        # 새 서버(re-exec 후 이미지 역)가 spec 으로 복원
        srvC.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockC)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        srvC._remote_resume = specs
        srvC.remote_restore_links()
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="restored merged status")
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
        await teardown(srvC, taskC, sockC)


async def test_remote_attach_self_rejected():
    """Stage 3: 자기 자신 endpoint attach 는 거부된다(자기 ⇄ 탭 재흡수로 탭
    목록이 status 왕복마다 무한 증식하는 루프 차단)."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    reader = writer = None
    try:
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockA})
        n = await _read_until(reader, lambda m: m.get("t") == "notice",
                              what="self-attach notice")
        assert "실패" in n.get("text", "") and "자기 자신" in n.get("text", ""), n
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)


async def test_remote_multi_link_merge_and_detach_one():
    """Stage 3 다중 원격: 두 링크의 탭이 전역 index 연속으로 병합되고, 하나만
    detach 하면 나머지는 유지된다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    srvC, taskC, sockC = await server_only()
    reader = writer = None
    try:
        srvB.ensure_default_session(80, 24)
        srvC.ensure_default_session(80, 24)
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        st0 = await _read_until(reader, lambda m: m.get("t") == "status",
                                what="initial status")
        n_local = len(st0["windows"])
        for ep in (sockB, sockC):
            await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                     "endpoint": ep})
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 2,
            what="two merged remotes")
        rtabs = [w for w in stm["windows"] if w["name"].startswith("⇄")]
        assert [w["index"] for w in rtabs] == [n_local, n_local + 1], rtabs
        assert any(sockB in w["name"] for w in rtabs)
        assert any(sockC in w["name"] for w in rtabs)
        await write_msg(writer, {"t": "cmd", "action": "remote_detach",
                                 "host": sockB})
        stl = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 1,
            what="one remote left")
        left = [w for w in stl["windows"] if w["name"].startswith("⇄")]
        assert sockC in left[0]["name"], left
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
        await teardown(srvC, taskC, sockC)


async def test_remote_multi_tab_merge_switch_all():
    """§1.7-b: 원격 서버에 탭이 여러 개면 **전부** 병합되고(remote=True 플래그
    포함), 각 원격 탭을 전역 index 로 개별 전환해 그 탭의 화면을 받을 수 있다.
    로컬 탭 엔트리에는 remote 플래그가 없다(§1.7-a 분홍 구분의 와이어 기준)."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        sessB = srvB.ensure_default_session(80, 24)
        srvB.new_window(sessB)
        srvB.new_window(sessB)
        ids = [t.window.active_pane.id for t in sessB.tabs]
        assert len(ids) == 3
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        st0 = await _read_until(reader, lambda m: m.get("t") == "status",
                                what="initial status")
        n_local = len(st0["windows"])
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 3,
            what="3 merged remote tabs")
        rtabs = [w for w in stm["windows"] if w["name"].startswith("⇄")]
        assert [w["index"] for w in rtabs] == [n_local, n_local + 1,
                                               n_local + 2], rtabs
        assert all(w.get("remote") is True for w in rtabs), rtabs
        ltabs = [w for w in stm["windows"] if not w["name"].startswith("⇄")]
        assert not any(w.get("remote") for w in ltabs), ltabs
        # 각 원격 탭 전환 → 그 탭의 패널이 활성인 업스트림 layout 이 도착
        for k, want in enumerate(ids):
            await write_msg(writer, {"t": "cmd", "action": "select_window",
                                     "index": n_local + k})
            await _read_until(
                reader, lambda m, w=want: m.get("t") == "layout"
                and m.get("active") == w,
                what=f"remote tab {k} layout")
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_detach_closes_all_tabs_reattach_restores():
    """§1.7: remote-detach 는 그 원격의 병합 탭 **전부**를 닫지만 원격 서버의
    탭/셸은 살아 있고, 재attach 하면 같은 탭 세트(remote 플래그 포함)가 다시
    병합된다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        sessB = srvB.ensure_default_session(80, 24)
        srvB.new_window(sessB)
        assert len(sessB.tabs) == 2
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 2,
            what="2 merged remote tabs")
        # detach → ⇄ 전부 제거, 원격은 그대로 살아 있다
        await write_msg(writer, {"t": "cmd", "action": "remote_detach"})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and not any(w["name"].startswith("⇄") for w in m["windows"]),
            what="all remote tabs gone")
        assert len(sessB.tabs) == 2, "원격 탭은 detach 후에도 살아 있어야"
        assert all(t.window.active_pane.pty is not None for t in sessB.tabs)
        # 재attach → 동일 탭 세트 복원(remote 플래그 포함)
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 2,
            what="re-merged 2 remote tabs")
        rtabs = [w for w in stm["windows"] if w["name"].startswith("⇄")]
        assert all(w.get("remote") is True for w in rtabs), rtabs
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_no_mixing_guards():
    """§1.7-c 섞임 금지: ① 로컬 보기에서 원격 전역 index 를 겨냥한 join/move 는
    거부(notice)되고 로컬 트리는 불변. ② 원격 보기 중 split/kill_pane 은 업스트림
    으로 릴레이돼 **원격** 탭에만 작용(로컬 불변). ③ 원격 보기 중 break_pane 등
    경계 횡단 조작은 거부(notice). ④ 원격 보기 중 new_window 는 보기를 해제하고
    로컬 새 탭으로 빠져나온다(보이지 않는 로컬 탭 생성 금지)."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        sessB = srvB.ensure_default_session(80, 24)
        sessA = srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="merged status")
        gidx = next(w["index"] for w in stm["windows"]
                    if w["name"].startswith("⇄"))

        # ① 로컬 보기: 원격 index 겨냥 이동/합치기 거부 + 로컬 불변
        nA_tabs = len(sessA.tabs)
        nA_panes = len(sessA.active_window.panes())
        for cmd in ({"action": "join_pane", "src": gidx, "orient": "tb"},
                    {"action": "move_pane_to_tab", "id": 1, "to": gidx},
                    {"action": "move_tab", "index": gidx, "to": 0}):
            await write_msg(writer, {"t": "cmd", **cmd})
            n = await _read_until(reader, lambda m: m.get("t") == "notice",
                                  what=f"mixing notice for {cmd['action']}")
            assert "섞을 수 없습니다" in n.get("text", ""), n
        assert len(sessA.tabs) == nA_tabs
        assert len(sessA.active_window.panes()) == nA_panes

        # ② 원격 보기 진입 → split 릴레이: 원격 패널 +1, 로컬 불변
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(reader, lambda m: m.get("t") == "layout",
                          what="remote layout")
        nB_panes = len(sessB.active_window.panes())
        await write_msg(writer, {"t": "cmd", "action": "split", "orient": "lr"})
        for _ in range(80):
            if len(sessB.active_window.panes()) == nB_panes + 1:
                break
            await asyncio.sleep(0.05)
        assert len(sessB.active_window.panes()) == nB_panes + 1, \
            "split 은 원격 탭에 릴레이되어야"
        assert len(sessA.active_window.panes()) == nA_panes, "로컬 불변"
        # kill_pane 도 릴레이 → 원격 패널 원복
        await write_msg(writer, {"t": "cmd", "action": "kill_pane"})
        for _ in range(80):
            if len(sessB.active_window.panes()) == nB_panes:
                break
            await asyncio.sleep(0.05)
        assert len(sessB.active_window.panes()) == nB_panes

        # ③ 원격 보기 중 경계 횡단 조작 거부(notice) + 양쪽 불변
        nB_tabs = len(sessB.tabs)
        await write_msg(writer, {"t": "cmd", "action": "break_pane"})
        n = await _read_until(reader, lambda m: m.get("t") == "notice",
                              what="break_pane blocked notice")
        assert "섞을 수 없습니다" in n.get("text", ""), n
        assert len(sessA.tabs) == nA_tabs and len(sessB.tabs) == nB_tabs

        # ④ 원격 보기 중 new_window → 보기 해제 + 로컬 새 탭(보이는 채로)
        await write_msg(writer, {"t": "cmd", "action": "new_window"})
        for _ in range(80):
            if len(sessA.tabs) == nA_tabs + 1:
                break
            await asyncio.sleep(0.05)
        assert len(sessA.tabs) == nA_tabs + 1, "로컬 새 탭이 생겨야"
        cA = next(c for c in srvA.clients)
        assert cA.remote_view is None, "new_window 가 원격 보기를 해제해야"
        assert len(sessB.tabs) == nB_tabs, "원격 탭 수는 불변"
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_link_death_recovers_viewer_to_local():
    """링크 사망(원격 서버 종료): 보던 클라는 로컬 화면으로 복귀(_send_full)하고
    탭바에서 ⇄ 탭이 제거된다 — '재접속 루프' 대신 명시적 끊김 처리."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        srvA._RECONNECT_DELAYS = (3600,)   # 이 테스트는 복귀만 검증(재연결 미발화)
        srvB.ensure_default_session(80, 24)
        sessA = srvA.ensure_default_session(80, 24)
        pA_id = sessA.active_window.active_pane.id
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        st = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="merged status")
        gidx = next(w["index"] for w in st["windows"]
                    if w["name"].startswith("⇄"))
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(reader, lambda m: m.get("t") == "layout",
                          what="remote layout")
        # 원격 서버 사망(연결 EOF) — B 측이 들고 있는 링크 연결 writer 를 닫아
        # A 의 링크 reader 가 EOF 를 받게 한다(teardown 은 listen 만 닫아 기존
        # 연결이 즉시 안 끊길 수 있음). → 보던 클라 로컬 복귀 + ⇄ 제거.
        for cB in list(srvB.clients):
            try:
                cB.writer.close()
            except OSError:
                pass
        await _read_until(
            reader, lambda m: m.get("t") == "layout" and m.get("active") == pA_id,
            what="local layout after link death")
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and not any(w["name"].startswith("⇄") for w in m["windows"]),
            what="status without remote tabs")
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
# ---- 원격 중첩 자동 승격(docs/NESTED_ATTACH_SCENARIO.md §4·§7) ----

def _nest_req(selfreport: str) -> bytes:
    from pytmuxlib import sshwrap
    b64 = base64.b64encode(selfreport.encode()).decode().encode()
    return sshwrap.NEST_REQ_PRE + b64 + sshwrap.DCS_ST


def _nest_dest(argv_lines: str) -> bytes:
    from pytmuxlib import sshwrap
    b64 = base64.b64encode(argv_lines.encode()).decode().encode()
    return sshwrap.NEST_DEST_PRE + b64 + sshwrap.DCS_ST


class _AckSpy:
    def __init__(self):
        self.writes = []

    def write(self, b):
        self.writes.append(b)

    def set_winsize(self, rows, cols):
        pass

    def acks(self):
        from pytmuxlib import sshwrap
        return sum(1 for w in self.writes if sshwrap.NEST_ACK in w)


async def test_nest_attach_request_promotes_to_remote_attach():
    """E2E 승격: 래퍼 NEST_DEST(목적지 기록) → 원격 launcher NEST_ATTACH_REQ →
    ack + 자동 remote_attach(엔드포인트 직결) → ⇄ 탭 병합 + 보던 클라 자동 전환
    (㉢ ON). 직후 재요청은 디바운스로 무 ack."""
    if os.name == "nt":
        return
    from pytmuxlib import sshwrap
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    writer = None
    try:
        srvB.ensure_default_session(80, 24)
        sessA = srvA.ensure_default_session(80, 24)
        pA = sessA.active_window.active_pane
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        realA, spy = pA.pty, _AckSpy()
        try:
            pA.pty = spy
            # ① 래퍼 DEST 기록 — 같은 머신 직결(endpoint 형태) 페더레이션.
            srvA._on_pane_data(pA, b"$ " + _nest_dest("ssh\n" + sockB))
            assert pA._ssh_dest == sockB, "목적지 기록(argv b64 → parse_dest)"
            # ② 승격 요청(self-report 호스트부 == 목적지 → ㉣ 대조 통과) → ack.
            srvA._on_pane_data(pA, _nest_req("tester@" + sockB))
            assert spy.acks() == 1, ("접수 ack 1회", spy.writes)
            # ③ 자동 attach·병합·자동 전환: 클라 status 에 ⇄ 탭이 active 로 온다.
            await _read_until(
                reader,
                lambda m: m.get("t") == "status" and any(
                    w.get("remote") and w.get("active")
                    for w in m.get("windows", [])),
                what="auto-switch status")
            assert sockB in srvA._remotes_dict(), "링크 생성"
            # ④ 디바운스: 직후 같은 요청은 무 ack(출력 재생/위조 연타 완화 §7).
            srvA._on_pane_data(pA, _nest_req("tester@" + sockB))
            assert spy.acks() == 1, "디바운스 — 추가 ack 없음"
        finally:
            pA.pty = realA
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_nest_attach_request_guards():
    """승격 가드(§7 보안 원칙): ① 목적지 미기록 → 무 ack(self-report 로 attach
    하지 않음) ② 호스트 불일치(2단 ssh 의심 ㉣) → 무 ack ③ nest_auto_attach OFF
    → 무 ack ④ 통과 시 ack(attach 실패는 notice 만 — 열화 없음). 대조 의미론은
    _nest_host_match 직접 단언(소문자 정규화+접두 일치)."""
    if os.name == "nt":
        return
    from pytmuxlib.serverremote import _nest_host_match
    assert _nest_host_match("office1", "u@OFFICE1.local"), "별칭 vs 실호스트(접두)"
    assert _nest_host_match("user@office1.example.com", "u@office1")
    assert not _nest_host_match("office1", "u@office2")
    assert not _nest_host_match("office1", "") and not _nest_host_match("", "u@h")

    srvA, taskA, sockA = await server_only()
    try:
        sessA = srvA.ensure_default_session(80, 24)
        pA = sessA.active_window.active_pane
        missing = os.path.join(tempfile.mkdtemp(prefix="pytmux-nest-"), "no.sock")
        realA, spy = pA.pty, _AckSpy()
        try:
            pA.pty = spy
            srvA._on_pane_data(pA, _nest_req("tester@anyhost"))      # ① 미기록
            assert spy.acks() == 0, "목적지 미기록 → 무 ack"
            srvA._on_pane_data(pA, _nest_dest("ssh\n" + missing))   # 기록
            srvA._on_pane_data(pA, _nest_req("tester@otherhost"))    # ② 불일치
            assert spy.acks() == 0, "호스트 불일치 → 무 ack"
            srvA.nest_auto_attach = False                            # ③ OFF
            srvA._on_pane_data(pA, _nest_req("tester@" + missing))
            assert spy.acks() == 0, "기능 OFF → 무 ack"
            srvA.nest_auto_attach = True                             # ④ 통과
            srvA._on_pane_data(pA, _nest_req("tester@" + missing))
            assert spy.acks() == 1, "가드 통과 → ack"
            # attach 본체는 없는 엔드포인트라 즉시 실패(notice 경로) — 태스크 소진.
            for _ in range(20):
                await asyncio.sleep(0.02)
                if missing not in srvA._remotes_dict():
                    break
            assert missing not in srvA._remotes_dict(), "실패 attach 는 링크 없음"
        finally:
            pA.pty = realA
    finally:
        await teardown(srvA, taskA, sockA)
