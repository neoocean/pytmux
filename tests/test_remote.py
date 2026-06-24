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


async def test_remote_status_relays_upstream_xc_totals():
    """§10-D P7: 원격(업스트림 B)의 트랜스크립트 권위 누계(xc_totals)가 와이어로
    다운스트림(A)에 전달된다 — A 가 B 의 원격 탭을 볼 때 status 가 B 의 정확 Σ(cache
    포함)를 싣는다. usage_limits 와 동형 패스스루(_remote_status_override 가 업스트림
    last_status 를 그대로 전달).

    주의: 한 테스트 안 두 server_only() 는 PYTMUX_TOKENS_DB(전역 env, 마지막-기록
    우선)를 공유해 A·B 가 같은 토큰 DB 를 본다 — 그래서 값으로 B↔A 출처를 가르지
    못한다(서버측 회신·dirty 게이트 검증은 test_server 의
    test_status_includes_xc_totals_cached_dirty_gated 가 권위). 여기선 **xc_totals
    필드가 원격 탭 뷰 status 에 실려 와이어를 건넌다**는 패스스루만 고정한다."""
    if os.name == "nt":
        return
    from pytmuxlib import usagedb
    srvA, taskA, sockA = await server_only()     # 로컬(다운스트림)
    srvB, taskB, sockB = await server_only()     # 원격(업스트림)
    reader = writer = None
    try:
        srvB.ensure_default_session(80, 24)
        # 공유 DB(위 주의)지만 적재로 full>0 을 만들어 필드가 dict 로 실리는지 본다.
        usagedb.insert_xc(srvB._tokens_db_conn(), {
            "xkey": "b1:r1", "ts": "2026-06-22T10:00:00.000Z",
            "session_uuid": "s1", "model": "claude-opus-4-8", "input": 10,
            "output": 5, "cache_create": 0, "cache_read": 985, "is_sidechain": 0})
        srvA._xc_totals_dirty = srvB._xc_totals_dirty = True

        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        st0 = await _read_until(reader, lambda m: m.get("t") == "status",
                                what="initial status")
        n_local = len(st0["windows"])

        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="merged status")
        # 원격 탭 진입 → 업스트림 status 패스스루에 xc_totals 가 dict 로 실린다.
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": n_local})
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and isinstance(m.get("xc_totals"), dict)
            and m["xc_totals"].get("full") == 1000,
            what="remote xc_totals relayed")
        assert stm["xc_totals"]["cache_read"] == 985, stm["xc_totals"]
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
        # 핸드셰이크 실패 알림은 3초 유지 + 수동 닫기(클릭/Enter) 가능해야 한다
        # (사용자 보고 2026-06-16: 너무 빨리 사라짐).
        assert n.get("secs") == 3.0, n
        assert n.get("dismissable") is True, n
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


async def test_autorename_broadcast_keeps_remote_active_for_viewer():
    """§10-F 원격 탭 활성 튐 회귀: status 방송은 _broadcast_status(per-client)로
    빌드돼, 원격 탭을 보는 클라는 방송 후에도 ⇄ 원격 탭이 active·로컬 탭은 비활성으로
    유지된다. 예전 auto-rename 루프는 clientless `_status_msg(sess)` 로 방송해 로컬
    active(=sess.active_index)가 새어 탭바가 로컬 탭으로 한 프레임 튀었다(복귀). 안
    보는 클라는 per-client 라 종전대로 로컬 active 를 받는다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = reader2 = writer2 = None
    try:
        srvB.ensure_default_session(80, 24)
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
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") and w["active"]
                    for w in m["windows"]),
            what="viewer on remote tab")
        # 안 보는 둘째 클라
        reader2, writer2 = await _attach_client(sockA)
        await _read_until(reader2, lambda m: m.get("t") == "status",
                          what="2nd client status")
        # auto-rename 방송 시뮬: 로컬 탭 이름 변경 + _broadcast_status
        sessA.tabs[0].name = "renamed-local"
        srvA._broadcast_status(sessA)
        # 보는 클라: 방송 후에도 ⇄ 원격 탭 active 유지·로컬 탭 비활성(튐 없음)
        stv = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="viewer status after broadcast")
        rtabs = [w for w in stv["windows"] if w["name"].startswith("⇄")]
        ltabs = [w for w in stv["windows"] if not w["name"].startswith("⇄")]
        assert any(w["active"] for w in rtabs), ("방송 후에도 원격 탭 active", rtabs)
        assert not any(w["active"] for w in ltabs), ("로컬 탭 비활성(튐 없음)", ltabs)
        # 안 보는 클라: per-client 라 로컬 active(⇄ 비활성)
        st2 = await _read_until(
            reader2, lambda m: m.get("t") == "status"
            and any(not w["name"].startswith("⇄") and w["active"]
                    for w in m["windows"]),
            what="non-viewer local active after broadcast")
        assert not any(w["active"] for w in st2["windows"]
                       if w["name"].startswith("⇄"))
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


async def test_remote_reconnect_giveup_reports_reason():
    """자동 재연결이 끝내 실패하면 '포기' notice 에 **마지막 실패 원인**(_remote_last_err)을
    함께 싣고(요청 2026-06-16) 수동 닫기(sticky=3초 유지·클릭/Enter)로 띄운다 — 핸드셰이크가
    반복 실패해 포기하는 그 순간이 원인이 가장 필요한 지점."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        srvA._RECONNECT_DELAYS = (0.02, 0.02)   # 빠르게 포기(인스턴스 한정)
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
        # B 의 소켓을 내려(재연결 open_connection 이 실패하게) 둔 뒤, A 의 업스트림
        # 연결을 끊어 EOF→자동 재연결을 유발한다 → 매 시도 실패 → 포기 경로.
        srvB.running = False
        if os.path.exists(sockB):
            os.remove(sockB)
        for cB in list(srvB.clients):
            try:
                cB.writer.close()
            except OSError:
                pass
        n = await _read_until(
            reader, lambda m: m.get("t") == "notice" and "포기" in m.get("text", ""),
            what="giveup notice")
        assert n.get("dismissable") is True, n               # sticky(수동 닫기)
        assert " — " in n["text"] and ":remote-attach" in n["text"], n  # 원인+재시도 안내
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_reconnect_requires_first_status_not_just_hello():
    """H5: 자동 재연결은 hello 성공만으로 '재연결됨'을 단정하지 않고 **실제 첫 status
    (탭 병합) 도착**을 성공 기준으로 삼는다. remote_attach 가 True(hello 송신)이고
    링크가 생겨도, 업스트림 웨지로 첫 status 가 안 오면(_remote_wait_first_status
    False) 그 시도는 실패로 간주해 재시도 후 포기하며 거짓 '재연결됨' notice 를
    띄우지 않는다(대화형 attach·중첩 승격의 first-status 게이트를 재연결까지 확장)."""
    if os.name == "nt":
        return
    import types
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv._RECONNECT_DELAYS = (0.0, 0.0)
        notices = []
        srv._remote_notice = lambda s, key, text, **kw: notices.append(key)
        srv._remote_status_broadcast = lambda: None
        link = types.SimpleNamespace(host="wedged", sess=sess,
                                     spec={"host": "wedged", "endpoint": None})
        attaches = {"n": 0}

        async def _fake_attach(*a, **k):
            attaches["n"] += 1
            srv._remotes_dict()["wedged"] = link    # hello 성공 → 링크는 생김
            return True

        async def _no_first_status(lk, **k):
            return False                             # 첫 status 미도착(웨지)

        srv.remote_attach = _fake_attach
        srv._remote_wait_first_status = _no_first_status
        await srv._remote_reconnect_loop(link)
        assert "rnotice.reconnected" not in notices, ("웨지=거짓 재연결 금지", notices)
        assert "rnotice.reconnect_giveup" in notices, ("첫 status 미도착→포기", notices)
        assert attaches["n"] == 2, "각 백오프 시도마다 재attach"
    finally:
        await teardown(srv, task, sock)


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
        # 첫 status(=탭 도착) 뒤에 오는 '병합됨' 알림을 소비해 둔다 — 이후 단계가
        # 읽는 notice 가 섞임-금지 알림임을 보장(merged notice 가 status 뒤로 이동).
        await _read_until(
            reader, lambda m: m.get("t") == "notice"
            and m.get("key") == "rnotice.attach_merged", what="merged notice")

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


async def test_remote_tab_pin_local_set_and_status():
    """§12 ①: 원격(병합) 탭 핀은 다운스트림 per-link 집합(pinned_windows)에 저장돼
    _remote_tabs 가 매 status 에 pinned 비트를 싣고, 업스트림엔 전파하지 않는다.
    토글(value=None)·명시 set 둘 다. 로컬 탭(sess.tabs)과 분기."""
    from pytmuxlib.serverremote import RemoteLink
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)     # 로컬 탭 0 (1개)
        srv._remote_status_broadcast = lambda: None    # 소켓 없는 단위 테스트
        link = RemoteLink("hostA", None, None)
        link.sess = sess
        link.windows = [{"name": "shellA", "active": True},
                        {"name": "shellB", "active": False}]
        srv._remotes_dict()["hostA"] = link
        base = len(sess.tabs)                           # 원격 병합 index = base, base+1
        # 둘째 원격 탭(원격 로컬 index 1) 명시 핀
        srv.set_remote_pinned(sess, base + 1, True)
        assert link.pinned_windows == {1}, link.pinned_windows
        rt = srv._remote_tabs(base)
        assert [t["pinned"] for t in rt] == [False, True], rt
        # 토글로 해제(value 생략)
        srv.set_remote_pinned(sess, base + 1)
        assert link.pinned_windows == set()
        # 로컬 탭은 별개 — 원격 핀이 로컬 pinned 를 안 건드린다.
        assert all(not t.pinned for t in sess.tabs)
    finally:
        await teardown(srv, task, sock)


async def test_remote_same_host_tabs_drag_merge():
    """§1.7-c 예외: **같은 호스트의 두 원격 탭**은 드래그 머지(join_pane)된다 —
    거부 대신 remote_relay_join 이 전역 src index 를 원격 로컬 index 로 변환해
    업스트림에 릴레이하고, 원격 서버가 자기 active 탭에 합친다(원격 탭 -1, 목적지
    패널 +1). 로컬 트리는 불변(원격↔로컬은 여전히 금지)."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        sessB = srvB.ensure_default_session(80, 24)
        srvB.new_window(sessB)                        # B 에 원격 탭 2개
        assert len(sessB.tabs) == 2
        sessA = srvA.ensure_default_session(80, 24)
        nA_tabs = len(sessA.tabs)
        nA_panes = len(sessA.active_window.panes())
        reader, writer = await _attach_client(sockA)
        st0 = await _read_until(reader, lambda m: m.get("t") == "status",
                                what="initial status")
        n_local = len(st0["windows"])
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 2,
            what="2 merged remote tabs")
        # 목적지 = 첫 원격 탭(전역 n_local), 끌어올 src = 둘째(n_local+1)
        dst_pid = sessB.tabs[0].window.active_pane.id
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": n_local})
        await _read_until(reader, lambda m: m.get("t") == "layout"
                          and m.get("active") == dst_pid, what="dest layout")
        # 드래그 머지 = select_pane_id(목적지 패널) + join_pane(src=원격 탭 전역 index)
        await write_msg(writer, {"t": "cmd", "action": "select_pane_id",
                                 "id": dst_pid})
        await write_msg(writer, {"t": "cmd", "action": "join_pane",
                                 "src": n_local + 1, "orient": "lr"})
        for _ in range(80):
            if (len(sessB.tabs) == 1
                    and len(sessB.tabs[0].window.panes()) == 2):
                break
            await asyncio.sleep(0.05)
        assert len(sessB.tabs) == 1, "원격 탭이 1개로 합쳐져야"
        assert len(sessB.tabs[0].window.panes()) == 2, \
            "목적지 원격 탭에 패널이 2개여야"
        # 로컬 트리는 전혀 건드리지 않는다
        assert len(sessA.tabs) == nA_tabs
        assert len(sessA.active_window.panes()) == nA_panes
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_autoresume_relays_to_remote_pane():
    """원격 탭을 보는 중 set_autoresume 는 **원격** 활성 패널에 적용된다(릴레이) —
    로컬 활성 패널(딴 탭)에 켜지던 '엉뚱한 탭에 AR' 버그 수정(사용자 보고 2026-06-15)."""
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
        localp = sessA.active_window.active_pane
        remotep = sessB.active_window.active_pane
        assert not localp.autoresume and not remotep.autoresume
        # 원격 탭 진입 → 보기 중 set_autoresume → 릴레이 → 원격 패널만 켜짐
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(reader, lambda m: m.get("t") == "layout",
                          what="remote layout")
        await write_msg(writer, {"t": "cmd", "action": "set_autoresume",
                                 "value": True})
        for _ in range(80):
            if remotep.autoresume:
                break
            await asyncio.sleep(0.05)
        assert remotep.autoresume, "원격 활성 패널에 AR 적용(릴레이)"
        assert not localp.autoresume, "로컬 패널은 불변(엉뚱한 탭 AR 금지)"
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_rename_relays_to_remote_tab_and_pane():
    """원격 탭을 보는 중 rename-tab(rename_window)·rename-pane(set_pane_title)는
    **원격** 활성 탭/패널에 적용된다(릴레이) — rename_window 가 릴레이 화이트리스트에
    없어 보이지 않는 **로컬** 탭만 바꾸고 원격엔 안 먹던 버그 수정(사용자 보고
    2026-06-17). set_pane_title 은 이미 릴레이되지만 같은 보고 범위라 함께 가드."""
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
        localtab = sessA.active_tab
        remotetab = sessB.active_tab
        localp = sessA.active_window.active_pane
        remotep = sessB.active_window.active_pane
        # 원격 탭 진입
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(reader, lambda m: m.get("t") == "layout",
                          what="remote layout")
        # rename-tab → 원격 활성 탭만 바뀜(로컬 탭 불변)
        await write_msg(writer, {"t": "cmd", "action": "rename_window",
                                 "name": "REMOTE_TAB"})
        for _ in range(80):
            if remotetab.name == "REMOTE_TAB":
                break
            await asyncio.sleep(0.05)
        assert remotetab.name == "REMOTE_TAB", "원격 활성 탭에 rename 적용(릴레이)"
        assert localtab.name != "REMOTE_TAB", "로컬 탭은 불변(엉뚱한 탭 rename 금지)"
        # rename-pane → 원격 활성 패널만 바뀜(로컬 패널 불변)
        await write_msg(writer, {"t": "cmd", "action": "set_pane_title",
                                 "title": "REMOTE_PANE"})
        for _ in range(80):
            if remotep.title == "REMOTE_PANE":
                break
            await asyncio.sleep(0.05)
        assert remotep.title == "REMOTE_PANE", "원격 활성 패널에 rename 적용(릴레이)"
        assert localp.title != "REMOTE_PANE", "로컬 패널은 불변"
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_paste_relays_to_remote_pane():
    """원격 탭을 보는 중 붙여넣기(paste=클립보드 텍스트·paste_buffer=버퍼)는 **원격**
    활성 패널에 주입된다(릴레이) — paste/paste_buffer 가 릴레이 화이트리스트에 없어
    보이지 않는 **로컬** 패널에 들어가던 버그 수정(사용자 보고 2026-06-17). 평문
    타이핑/bracketed paste(input)는 이미 릴레이됐지만 붙여넣기 cmd 는 누락이었다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        srvB.ensure_default_session(80, 24)
        srvA.ensure_default_session(80, 24)
        # 양 서버의 paste 진입점을 기록기로 감싼다(실 pty 주입 비의존 — 어느 서버가
        # 붙여넣기를 처리했는지로 릴레이를 판정).
        recA, recB = [], []
        _ptA, _pbA = srvA.paste_text, srvA.paste_buffer
        _ptB, _pbB = srvB.paste_text, srvB.paste_buffer
        srvA.paste_text = lambda s, t, _r=recA: _r.append(("text", t))
        srvA.paste_buffer = lambda s, i=0, _r=recA: _r.append(("buf", i))
        srvB.paste_text = lambda s, t, _r=recB: _r.append(("text", t))
        srvB.paste_buffer = lambda s, i=0, _r=recB: _r.append(("buf", i))
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
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(reader, lambda m: m.get("t") == "layout",
                          what="remote layout")
        # 원격 보기 중 paste(텍스트) → 업스트림 B 가 처리, 로컬 A 는 미처리
        await write_msg(writer, {"t": "cmd", "action": "paste",
                                 "text": "REMOTE_PASTE"})
        for _ in range(80):
            if recB:
                break
            await asyncio.sleep(0.05)
        assert recB == [("text", "REMOTE_PASTE")], \
            f"원격 패널에 paste 주입(릴레이): {recB!r}"
        assert recA == [], f"로컬 패널은 불변: {recA!r}"
        # paste_buffer 도 동일
        await write_msg(writer, {"t": "cmd", "action": "paste_buffer",
                                 "index": 2})
        for _ in range(80):
            if any(x[0] == "buf" for x in recB):
                break
            await asyncio.sleep(0.05)
        assert ("buf", 2) in recB, f"원격 패널에 paste_buffer(릴레이): {recB!r}"
        assert recA == [], f"로컬 패널은 여전히 불변: {recA!r}"
        srvA.paste_text, srvA.paste_buffer = _ptA, _pbA
        srvB.paste_text, srvB.paste_buffer = _ptB, _pbB
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_redraw_relays_to_remote():
    """원격 탭을 보는 중 redraw(request_redraw)는 업스트림으로 릴레이돼 **원격** 화면이
    재그려진다(§2.12) — 로컬 서버가 보이지 않는 로컬 화면만 재그리던 §1.7-c 누락 방지.
    업스트림이 _induce_redraw_all 후 _send_full 로 보낸 layout/screen 이 federation
    연결을 통해 보는 클라에 전달된다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        srvB.ensure_default_session(80, 24)
        srvA.ensure_default_session(80, 24)
        inducedA, inducedB = [], []
        _oa, _ob = srvA._induce_redraw_all, srvB._induce_redraw_all
        srvA._induce_redraw_all = lambda: (inducedA.append(1), _oa())[1]
        srvB._induce_redraw_all = lambda: (inducedB.append(1), _ob())[1]
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
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(reader, lambda m: m.get("t") == "layout",
                          what="remote layout")
        # attach/select 과정에서 든 SIGWINCH(serverio attach 직후 1회 등)는 무시 —
        # redraw 명령 자체의 효과만 본다.
        inducedA.clear()
        inducedB.clear()
        # 원격 보기 중 redraw → 업스트림 B 가 repaint 유발, 로컬 A 는 아님
        await write_msg(writer, {"t": "cmd", "action": "request_redraw"})
        for _ in range(80):
            if inducedB:
                break
            await asyncio.sleep(0.05)
        assert inducedB, "원격(업스트림 B) 화면이 재그려져야(릴레이)"
        assert not inducedA, "로컬 A 화면은 재그리지 않음(섞임 금지)"
        srvA._induce_redraw_all, srvB._induce_redraw_all = _oa, _ob
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_token_log_relays_to_upstream():
    """§10-D 잔여: 원격 탭을 보는 중 토큰 사용량 팝업 조회(request_token_log)는
    업스트림으로 릴레이돼 그 **원격** 머신의 토큰 누계를 회신한다 — 종전엔 릴레이
    화이트리스트에 없어 보이지 않는 **로컬** DB 를 회신해, 분홍 팝업 테두리(원격 보기
    표식)와 실제 데이터 출처가 어긋났다. 업스트림 B 가 token_log 를 회신하면
    _remote_reader 가 보는 클라에 그대로 전달한다(섞임 금지 §1.7-c 동형).

    공유 DB(harness 한계) 탓에 값으로 B↔A 를 못 가르므로, redraw/paste 테스트처럼
    **어느 서버가 request_token_log 를 처리했는지**(handle_server_request 호출)로
    릴레이를 판정하고, 응답 token_log 가 보는 클라까지 전달됨도 함께 고정한다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()     # 로컬(다운스트림)
    srvB, taskB, sockB = await server_only()     # 원격(업스트림)
    reader = writer = None
    try:
        srvB.ensure_default_session(80, 24)
        srvA.ensure_default_session(80, 24)
        # 양 서버의 토큰 로그 처리 진입점(레지스트리 훅)을 기록기로 감싼다 — 어느
        # 서버가 request_token_log 를 처리했는지로 릴레이를 판정(실 DB 값 비의존).
        recA, recB = [], []
        _hA = srvA.plugins.handle_server_request
        _hB = srvB.plugins.handle_server_request
        srvA.plugins.handle_server_request = (
            lambda srv, s, a, m, _o=_hA, _r=recA: (_r.append(a), _o(srv, s, a, m))[1])
        srvB.plugins.handle_server_request = (
            lambda srv, s, a, m, _o=_hB, _r=recB: (_r.append(a), _o(srv, s, a, m))[1])
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
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(reader, lambda m: m.get("t") == "layout",
                          what="remote layout")
        recA.clear()
        recB.clear()
        # 원격 보기 중 토큰 로그 조회 → 업스트림 B 가 처리, 로컬 A 는 미처리
        await write_msg(writer, {"t": "cmd", "action": "request_token_log",
                                 "limit": 5000})
        tl = await _read_until(reader, lambda m: m.get("t") == "token_log",
                               what="upstream token_log relayed back")
        assert "request_token_log" in recB, \
            f"원격(업스트림 B)이 토큰 로그를 처리(릴레이): {recB!r}"
        assert "request_token_log" not in recA, \
            f"로컬 A 는 토큰 로그를 처리하지 않음(섞임 금지): {recA!r}"
        # 응답 token_log 가 보는 클라까지 전달되어 팝업이 채워진다(필드 구조 보존).
        assert "records" in tl and "xc_totals" in tl, tl
        srvA.plugins.handle_server_request = _hA
        srvB.plugins.handle_server_request = _hB
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_ncd_relays_to_remote_cwd():
    """원격 탭을 보는 중 ncd(request_nc_list)는 **원격** 머신의 cwd/디렉토리 트리를
    회신한다(릴레이) — 로컬 서버가 자기 fs 의 cwd 를 회신하던 '원격 보는데 로컬
    디렉토리' 버그 수정(사용자 보고 2026-06-17). 업스트림 nc_list 응답은
    _remote_reader 패스스루로 보는 클라에 전달된다."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    dirA = tempfile.mkdtemp(prefix="ncdlocalA_")
    dirB = tempfile.mkdtemp(prefix="ncdremoteB_")
    try:
        # 각 서버의 활성 패널 cwd 를 서로 다른 실 디렉토리로 고정(실 pty cwd 비의존).
        srvA._resolve_start_cwd = lambda sess, path=None: dirA
        srvB._resolve_start_cwd = lambda sess, path=None: dirB
        srvB.ensure_default_session(80, 24)
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
        await write_msg(writer, {"t": "cmd", "action": "select_window",
                                 "index": gidx})
        await _read_until(reader, lambda m: m.get("t") == "layout",
                          what="remote layout")
        # 원격 보기 중 ncd → 릴레이 → 업스트림이 자기 cwd(dirB) 트리로 회신
        await write_msg(writer, {"t": "cmd", "action": "request_nc_list",
                                 "path": None})
        nc = await _read_until(reader, lambda m: m.get("t") == "nc_list",
                               what="relayed nc_list")
        assert nc.get("cwd") == dirB, \
            f"원격 cwd 여야(릴레이): {nc.get('cwd')!r} != {dirB!r}"
        assert nc.get("cwd") != dirA, "로컬 cwd 가 나오면 안 됨"
        # 사슬에 원격 cwd 가 포함(루트→dirB)
        assert any(entry[0] == dirB for entry in (nc.get("chain") or [])), \
            "원격 조상 사슬이어야"
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)
        for d in (dirA, dirB):
            try:
                os.rmdir(d)
            except OSError:
                pass


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
# ---- 원격 중첩 자동 승격(docs/internal/NESTED_ATTACH_SCENARIO.md §4·§7) ----

def _nest_req(selfreport: str) -> bytes:
    from pytmuxlib import sshwrap
    b64 = base64.b64encode(selfreport.encode()).decode().encode()
    return sshwrap.NEST_REQ_PRE + b64 + sshwrap.DCS_ST


def _server_sshwrap_token() -> str:
    from pytmuxlib import ipc, sshwrap
    return sshwrap.load_or_create_token(ipc.default_state_dir())


def _nest_dest(argv_lines: str, token: str | None = None) -> bytes:
    """NEST_DEST DCS. provenance 머리줄(token)이 서버 것과 일치해야 _ssh_dest 가
    기록된다(NEW-1). token=None 이면 실제 서버 토큰(정상 래퍼)을 쓴다."""
    from pytmuxlib import sshwrap
    if token is None:
        token = _server_sshwrap_token()
    payload = token + "\n" + argv_lines
    b64 = base64.b64encode(payload.encode()).decode().encode()
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
            # ①-a 위조 DEST(provenance 토큰 불일치 = cat/스크롤백/원격출력) → 무시.
            srvA._on_pane_data(
                pA, b"$ " + _nest_dest("ssh\n" + sockB, token="deadbeef"))
            assert pA._ssh_dest == "", "위조 토큰 DEST 는 _ssh_dest 미기록(NEW-1)"
            # ① 정상 래퍼 DEST 기록 — 같은 머신 직결(로컬 unix소켓 endpoint) 페더레이션.
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


async def test_nest_do_attach_blocks_nonlocal_endpoint():
    """NEW-1: NEST 자동 승격은 비로컬 tcp: endpoint 직결을 거부한다(위조/조작된
    _ssh_dest 의 tcp:원격호스트 직결 → 임의 아웃바운드+키 MITM 차단). 로컬 unix
    소켓·loopback tcp 는 직결 허용(같은 머신). 판정 헬퍼도 직접 단언."""
    if os.name == "nt":
        return
    from pytmuxlib.serverremote import _nest_local_endpoint
    assert _nest_local_endpoint("/tmp/x.sock")
    assert _nest_local_endpoint("tcp:127.0.0.1:7000")
    assert _nest_local_endpoint("tcp:localhost:7000")
    assert not _nest_local_endpoint("tcp:evil.com:9999")
    assert not _nest_local_endpoint("tcp:10.0.0.5:22")
    assert not _nest_local_endpoint("office1")           # ssh 호스트(미직결)

    srvA, taskA, sockA = await server_only()
    try:
        sessA = srvA.ensure_default_session(80, 24)
        # 비로컬 tcp endpoint → 거부(remote_attach 미호출, 링크 미생성).
        await srvA._nest_do_attach(sessA, "tcp:evil.com:9999")
        assert "tcp:evil.com:9999" not in srvA._remotes_dict(), \
            "비로컬 endpoint 직결 차단 — 링크 없음"
    finally:
        await teardown(srvA, taskA, sockA)


async def test_decode_remote_stderr_cp949_fallback():
    """원격(Windows) stderr 디코딩(serverremote): UTF-8 우선, 비-UTF-8(cp949 한국어
    콘솔)은 cp949 폴백으로 사람이 읽게 — `pytmux: 실행 중인 서버 없음`(office1 서버
    부재 메시지)이 `����` 로 깨지던 회귀 가드. 빈 입력/순수 UTF-8 도 보존."""
    from pytmuxlib.serverremote import _decode_remote_stderr
    msg = "pytmux: 실행 중인 서버 없음"
    assert _decode_remote_stderr(msg.encode("cp949")) == msg, "cp949 폴백"
    assert _decode_remote_stderr(msg.encode("utf-8")) == msg, "UTF-8 우선"
    assert _decode_remote_stderr(b"Permission denied (publickey)") \
        == "Permission denied (publickey)", "ASCII/UTF-8 무손상"
    assert _decode_remote_stderr(b"") == "", "빈 입력"


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


async def test_nest_do_attach_repeat_does_not_reswitch():
    """사용자 보고 2026-06-17: 이미 병합된 원격 호스트로의 중복 NEST_ATTACH_REQ
    (출력 재생·스크롤백·셸 루프가 만든 것, 디바운스를 넘긴 것)가 매번 클라를 원격
    탭으로 끌어가 로컬↔원격을 저 혼자 오가게 만들었다. 이제 _nest_do_attach 는 이미
    _remotes_dict 에 있는 호스트면 즉시 무시한다 — 재attach 도, 재전환도 없다(첫 승격
    때 이미 했다). 첫 승격(fresh) 자동 전환 자체는 별도 E2E 테스트가 검증한다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        dest = "already-merged-host"
        srv._remotes_dict()[dest] = object()   # 이미 병합돼 있다고 가정(더미 링크)
        calls = {"attach": 0, "switch": 0}

        async def _spy_attach(*a, **k):
            calls["attach"] += 1
            return True

        async def _spy_switch(*a, **k):
            calls["switch"] += 1
            return True

        srv.remote_attach = _spy_attach
        srv.remote_select_window = _spy_switch
        await srv._nest_do_attach(sess, dest)
        assert calls["attach"] == 0, "이미 병합된 호스트 — 재attach 안 함"
        assert calls["switch"] == 0, "이미 병합된 호스트 — 재전환(탭 yank) 안 함"
    finally:
        await teardown(srv, task, sock)


async def test_kill_proc_reaps_subprocess():
    """H4: _kill_proc 가 ssh 서브프로세스를 확실히 종료·reap 한다(핸드셰이크/타임아웃
    실패 경로의 <defunct> 좀비 + 파이프 fd 누수 방지). 살아있는 자식은 kill+wait,
    이미 끝난 자식도 wait 로 회수, None 도 무해."""
    if os.name == "nt":
        return
    from pytmuxlib.serverremote import _kill_proc
    p = await asyncio.create_subprocess_exec(
        "sleep", "30", stdout=asyncio.subprocess.DEVNULL)
    await _kill_proc(p)
    assert p.returncode is not None, "살아있는 자식 종료·reap"
    p2 = await asyncio.create_subprocess_exec(
        "true", stdout=asyncio.subprocess.DEVNULL)
    await p2.wait()
    await _kill_proc(p2)                       # 이미 끝남 → 예외 없이 회수
    assert p2.returncode is not None
    await _kill_proc(None)                     # None 안전


async def test_remote_transport_ssh_keepalive_args():
    """M2: federation ssh 에 ServerAliveInterval/CountMax 를 실어 half-open(좀비/웨지)
    연결을 ssh 가 감지해 끊게 한다 — stdio-proxy 파이프 EOF→자동 재연결/회수 진행."""
    srv, task, sock = await server_only()
    captured = {}

    async def _fake_exec(*args, **kw):
        captured["argv"] = args
        raise OSError("no spawn (test)")

    orig = asyncio.create_subprocess_exec
    asyncio.create_subprocess_exec = _fake_exec
    try:
        try:
            await srv._remote_transport(host="goodhost", endpoint=None)
        except OSError:
            pass
    finally:
        asyncio.create_subprocess_exec = orig
        await teardown(srv, task, sock)
    argv = captured.get("argv", ())
    assert "ServerAliveInterval=15" in argv, argv
    assert "ServerAliveCountMax=3" in argv, argv


async def test_remote_transport_rejects_ssh_option_injection():
    """S2: remote_attach 의 host 는 클라 cmd 에서 온 비신뢰 문자열이다. ssh 옵션
    인젝션('-oProxyCommand=…' → 임의 명령)·공백 호스트는 ssh 를 띄우기 **전에**
    ConnectionError 로 거부돼야 한다(argv 형이라 셸 인젝션은 없지만 ssh 가 '-…' 를
    옵션으로 해석하는 벡터를 '--' + 선행'-' 거부로 막는다)."""
    srv, task, sock = await server_only()
    try:
        for bad in ("-oProxyCommand=touch /tmp/pwned",
                    "-l", "  ", "host with space"):
            try:
                await srv._remote_transport(host=bad, endpoint=None)
                assert False, f"기대: ConnectionError for host={bad!r}"
            except ConnectionError:
                pass
    finally:
        await teardown(srv, task, sock)


async def test_remote_allowed_hosts_allowlist():
    """S2 후속: remote_allowed_hosts 가 비어 있으면(기본) 임의 host 를 허용하고(옵션
    인젝션 가드만), 설정되면 정확히 일치하는 목적지만 ssh 로 띄운다. 허용목록은 비신뢰
    클라 입력이 아니라 서버측 특권 설정(opts.json)으로 데몬 ssh egress 를 잠근다."""
    srv, task, sock = await server_only()
    try:
        # 기본(빈 목록): 허용목록 미적용 — 임의 host 는 ssh 핸드셰이크 단계까지 진행
        # (subprocess 생성을 피하려고 create_subprocess_exec 를 가로채 거부 신호만 확인).
        import pytmuxlib.serverremote as sr
        srv.remote_allowed_hosts = []
        spawned = []

        async def fake_exec(*argv, **kw):
            spawned.append(argv)
            raise ConnectionError("spawn-reached")  # 핸드셰이크 진입 신호
        monkey = asyncio.create_subprocess_exec
        sr.asyncio.create_subprocess_exec = fake_exec
        try:
            try:
                await srv._remote_transport(host="office1", endpoint=None)
            except ConnectionError as e:
                assert "spawn-reached" in str(e), str(e)
            assert spawned and spawned[0][:2] == ("ssh", "-T"), spawned
            # 허용목록 설정: 미등재 host 는 spawn 전에 거부, 등재 host 는 spawn 도달
            srv.remote_allowed_hosts = ["office1", "user@box2"]
            spawned.clear()
            try:
                await srv._remote_transport(host="evil", endpoint=None)
                assert False, "기대: 미등재 host 거부"
            except ConnectionError as e:
                assert "허용되지 않은" in str(e), str(e)
            assert not spawned, "미등재 host 가 ssh 를 띄웠다"
            try:
                await srv._remote_transport(host="office1", endpoint=None)
            except ConnectionError as e:
                assert "spawn-reached" in str(e), str(e)
            assert spawned, "등재 host 가 거부됐다"
        finally:
            sr.asyncio.create_subprocess_exec = monkey
    finally:
        await teardown(srv, task, sock)


async def test_remote_attach_silent_upstream_warns_not_merged():
    """업스트림이 hello 는 받고도 첫 status 를 안 보내는 웨지(원격 pty-host 고장 등,
    사용자 보고 2026-06-20)면, 종전엔 hello 송신 직후 '병합됨'으로 단정해 '성공인데
    탭 없음'으로 보였다. 이제 첫 status(실제 탭 도착)를 잠깐 기다려 못 받으면
    rnotice.attach_silent(연결됐지만 무응답)로 알리고 ⇄ 탭을 만들지 않는다."""
    if os.name == "nt":
        return  # in-process 페더레이션 코어는 POSIX 소켓 기준으로 검증
    srvA, taskA, sockA = await server_only()
    # 첫 status 대기를 짧게(3×0.02s) — mute 업스트림은 영영 안 보내므로 빠르게 판정.
    srvA._FIRST_STATUS_TRIES = 3
    srvA._FIRST_STATUS_DELAY = 0.02

    # mute 업스트림: hello 만 읽고 status 를 영영 안 보낸다(연결은 유지).
    mute_path = tempfile.mktemp(prefix="pytmux-mute-", suffix=".sock")
    held = []

    async def _mute(reader, writer):
        held.append(writer)             # writer 를 살려둬 EOF 가 안 나게
        try:
            await read_msg(reader)      # hello 소비 후 침묵
            while not reader.at_eof():
                await asyncio.sleep(0.05)
        except (OSError, ConnectionError):
            pass

    mute_srv = await asyncio.start_unix_server(_mute, path=mute_path)
    reader = writer = None
    try:
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")

        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": mute_path})
        note = await _read_until(reader, lambda m: m.get("t") == "notice",
                                 what="attach notice")
        assert note.get("key") == "rnotice.attach_silent", note
        assert note.get("secs"), ("무응답 알림은 sticky", note)

        # ⇄ 탭이 생기지 않았다(웨지 업스트림은 병합 안 됨).
        st = srvA._status_msg(list(srvA.sessions.values())[0])
        assert not any(w["name"].startswith("⇄") for w in st["windows"]), st
    finally:
        if writer is not None:
            writer.close()
        for w in held:
            try:
                w.close()
            except OSError:
                pass
        mute_srv.close()
        await mute_srv.wait_closed()
        try:
            os.unlink(mute_path)
        except OSError:
            pass
        await teardown(srvA, taskA, sockA)
