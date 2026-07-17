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


async def _assert_no(reader, pred, window=1.0, what="msg"):
    """window 초 동안 pred 에 맞는 메시지가 **오지 않음**을 단언(음성 검사). 그 사이
    온 다른 메시지(status 등)는 무시한다. 메시지 고갈(TimeoutError)/연결 종료=통과."""
    end = time.monotonic() + window
    while time.monotonic() < end:
        try:
            msg = await asyncio.wait_for(read_msg(reader),
                                         max(0.05, end - time.monotonic()))
        except asyncio.TimeoutError:
            return
        if msg is None:
            return
        assert not pred(msg), f"예상치 못한 {what}: {msg.get('t')}"


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
        # 마커가 실릴 때까지 screen 메시지를 **계속** 읽는다 — 원격 탭 진입 직후의
        # 첫 screen 이 업스트림 릴레이 타이밍에 따라 마커 이전의 초기 페인트일 수
        # 있어(전체 스위트 부하에서 간헐 재현, 격리 실행은 통과 — 2026-07-10),
        # 첫 프레임 고정 단언은 거짓 실패한다. 후속 프레임이 마커를 싣고 온다.
        scr = await _read_until(
            reader, lambda m: (m.get("t") == "screen" and m.get("pane") == rid
                               and "REMOTE-MARKER-XYZ" in _rows_text(m["rows"])),
            what="remote screen with marker")
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


async def test_remote_pin_survives_restart():
    """§12 ①: 원격(⇄) 탭에 걸어 둔 다운스트림 로컬 핀(pinned_windows)이 작업보존
    재시작을 살아남는다 — _resume_payload 가 spec 에 실어 두고 remote_restore_links
    가 새 링크에 되살린다. (재시작 후 핀 유실 회귀 방지.)"""
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
        link = next(iter(srvA._remotes_dict().values()))
        # 업스트림 status 병합으로 link.windows(안정 wid 포함)가 찰 때까지 대기 —
        # 핀은 위치가 아니라 wid 로 키잉하므로 실제 창의 wid 가 필요하다(로드맵 #3).
        for _ in range(200):
            if link.windows:
                break
            await asyncio.sleep(0.02)
        assert link.windows, "업스트림 status 병합 대기 실패"
        base = len(sessA.tabs)
        srvA.set_remote_pinned(sessA, base, True)       # 첫 원격 탭 핀(wid 로 키잉)
        key = srvA._win_key(link.windows[0])
        assert link.pinned_windows == {key}, link.pinned_windows
        specs = srvA._resume_payload().get("remotes")
        assert specs and specs[0].get("pinned_windows") == [key], specs

        # 새 서버(re-exec 후 이미지 역)가 spec 으로 복원 → 같은 업스트림(srvB 불변)에
        # 재연결하면 같은 wid 를 받아 핀이 그 탭에 되살아난다.
        srvC.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockC)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        srvC._remote_resume = specs
        srvC.remote_restore_links()
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and any(w.get("remote") and w.get("pinned") for w in m["windows"]),
            what="restored pinned remote tab")
        linkC = next(iter(srvC._remotes_dict().values()))
        assert linkC.pinned_windows == {key}, linkC.pinned_windows
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


async def test_remote_detach_single_tab():
    """단일-탭 분리: remote_detach 에 병합 전역 index 를 실으면 그 **탭 하나만**
    병합 뷰에서 사라지고, 같은 호스트의 다른 원격 탭·원격 셸은 그대로 살아 있다.
    마지막 남은 원격 탭까지 분리하면 링크(호스트) 전체가 사라진다(⇄ 전멸)."""
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
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 2,
            what="2 merged remote tabs")
        # 첫 원격 탭의 병합 전역 index 하나만 분리
        gidx = next(w["index"] for w in stm["windows"]
                    if w["name"].startswith("⇄"))
        await write_msg(writer, {"t": "cmd", "action": "remote_detach",
                                 "index": gidx})
        # ⇄ 탭이 2 → 1 로 줄고(호스트 전멸 아님), 원격 셸은 둘 다 살아 있다
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 1,
            what="one remote tab detached, one remains")
        assert len(sessB.tabs) == 2, "단일-탭 분리 후에도 원격 탭 전부 생존"
        assert all(t.window.active_pane.pty is not None for t in sessB.tabs)
        # 남은 원격 탭(이제 index=gidx)까지 분리 → ⇄ 전멸(링크 해제)
        await write_msg(writer, {"t": "cmd", "action": "remote_detach",
                                 "index": gidx})
        await _read_until(
            reader, lambda m: m.get("t") == "status"
            and not any(w["name"].startswith("⇄") for w in m["windows"]),
            what="last remote tab detached, link gone")
        assert len(sessB.tabs) == 2, "링크 해제 후에도 원격 셸 생존"
    finally:
        if writer is not None:
            writer.close()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_detach_survives_upstream_tab_close_reindex():
    """M-1(코드검수 2026-07-10): 원격 단일-탭 분리 후 상류가 **더 앞의** 탭을 닫아
    _reindex 로 index 가 재할당돼도, 숨긴 탭은 **그 탭 그대로** 유지된다(엉뚱한 탭이
    다시 나타나거나 다른 탭이 숨지 않음). 종전 위치 index 키잉은 상류 close 로
    index 가 밀리면 숨김 대상이 어긋났다 — 안정 wid 키잉으로 수정."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
        sessB = srvB.ensure_default_session(80, 24)
        srvB.new_window(sessB)
        srvB.new_window(sessB)
        assert len(sessB.tabs) == 3
        for i, t in enumerate(sessB.tabs):
            t.name = f"T{i}"                      # 구분용 고유 이름
        srvA.ensure_default_session(80, 24)
        reader, writer = await _attach_client(sockA)
        await _read_until(reader, lambda m: m.get("t") == "status",
                          what="initial status")
        await write_msg(writer, {"t": "cmd", "action": "remote_attach",
                                 "endpoint": sockB})
        stm = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 3,
            what="3 merged remote tabs")
        # 상류 index 2(T2)에 해당하는 병합 전역 index 를 분리한다.
        gidx_t2 = next(w["index"] for w in stm["windows"]
                       if w["name"].endswith(":T2"))
        await write_msg(writer, {"t": "cmd", "action": "remote_detach",
                                 "index": gidx_t2})
        st1 = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 2,
            what="T2 detached, 2 remote tabs")
        vis1 = {w["name"].split(":", 1)[1] for w in st1["windows"]
                if w["name"].startswith("⇄")}
        assert vis1 == {"T0", "T1"}, vis1        # T2 만 숨김

        # 상류가 **더 앞 탭 T1**(index 1)을 닫는다 → _reindex: T0(0), T2(1).
        t1_pane = sessB.tabs[1].window.active_pane
        srvB.kill_pane(sessB, t1_pane)
        assert [t.name for t in sessB.tabs] == ["T0", "T2"]

        # 다운스트림 병합 뷰: 여전히 T2 만 숨고 T0 만 보여야 한다(T2 재출현 금지).
        st2 = await _read_until(
            reader, lambda m: m.get("t") == "status"
            and sum(w["name"].startswith("⇄") for w in m["windows"]) == 1,
            what="after upstream close: one remote tab remains")
        vis2 = {w["name"].split(":", 1)[1] for w in st2["windows"]
                if w["name"].startswith("⇄")}
        assert vis2 == {"T0"}, f"상류 close 후 숨김 대상이 어긋남(M-1): {vis2}"
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
        # 실제 status windows 는 안정 wid + 위치 index 를 싣는다(핀은 wid 로 키잉).
        link.windows = [{"name": "shellA", "index": 0, "wid": 501, "active": True},
                        {"name": "shellB", "index": 1, "wid": 502, "active": False}]
        srv._remotes_dict()["hostA"] = link
        base = len(sess.tabs)                           # 원격 병합 index = base, base+1
        # 둘째 원격 탭(원격 로컬 index 1, wid 502) 명시 핀 → 집합엔 위치가 아니라 wid.
        srv.set_remote_pinned(sess, base + 1, True)
        assert link.pinned_windows == {502}, link.pinned_windows
        rt = srv._remote_tabs(base)
        assert [t["pinned"] for t in rt] == [False, True], rt
        # 토글로 해제(value 생략)
        srv.set_remote_pinned(sess, base + 1)
        assert link.pinned_windows == set()
        # 로컬 탭은 별개 — 원격 핀이 로컬 pinned 를 안 건드린다.
        assert all(not t.pinned for t in sess.tabs)
    finally:
        await teardown(srv, task, sock)


async def test_remote_pin_survives_upstream_tab_close_reindex():
    """로드맵 #3(M-1 과 동일 클래스): 원격 탭을 핀한 뒤 상류가 **더 앞의** 탭을 닫아
    _reindex 로 위치 index 가 재할당돼도, 핀은 **그 탭 그대로** 따라간다(위치가 아니라
    안정 wid 로 키잉). 종전 위치-index 키잉은 상류 close 로 핀이 엉뚱한 탭으로 옮겨갔다."""
    from pytmuxlib.serverremote import RemoteLink
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv._remote_status_broadcast = lambda: None
        link = RemoteLink("hostA", None, None)
        link.sess = sess
        # 상류 탭 3개(wid 501/502/503, 위치 0/1/2).
        link.windows = [{"name": "T0", "index": 0, "wid": 501, "active": False},
                        {"name": "T1", "index": 1, "wid": 502, "active": False},
                        {"name": "T2", "index": 2, "wid": 503, "active": False}]
        srv._remotes_dict()["hostA"] = link
        base = len(sess.tabs)
        srv.set_remote_pinned(sess, base + 2, True)      # T2(wid 503) 핀
        assert link.pinned_windows == {503}
        # 상류가 앞 탭 T1 을 닫음 → _reindex: T0(0), T2(1). link.windows 재송신.
        link.windows = [{"name": "T0", "index": 0, "wid": 501, "active": False},
                        {"name": "T2", "index": 1, "wid": 503, "active": False}]
        rt = srv._remote_tabs(base)
        pinned_names = [t["name"].split(":", 1)[1] for t in rt if t["pinned"]]
        assert pinned_names == ["T2"], f"핀이 엉뚱한 탭으로 이동(로드맵 #3): {rt}"
    finally:
        await teardown(srv, task, sock)


async def test_remote_pin_survives_reattach_same_host():
    """사용자 보고 2026-07-15: 원격 탭이 열린 채 **같은 서버에 다시 remote-attach**
    하면 걸어 둔 핀이 전부 풀렸다 — 같은 이름 링크 교체가 새 RemoteLink 의 빈
    pinned_windows 로 옛 집합을 덮어썼기 때문. 핀은 링크가 아니라 **호스트 수명**
    상태(_remote_sticky)라 재-attach 를 살아남고, 그 사이 상류에 **추가된 탭만**
    비고정으로 붙는다(핀은 안정 wid 키잉이라 위치와 무관)."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    try:
        sessB = srvB.ensure_default_session(80, 24)
        sessA = srvA.ensure_default_session(80, 24)
        assert await srvA.remote_attach(sessA, endpoint=sockB)
        link = srvA._remotes_dict()[sockB]
        for _ in range(200):
            if link.windows:
                break
            await asyncio.sleep(0.02)
        assert link.windows, "업스트림 status 병합 대기 실패"
        base = len(sessA.tabs)
        srvA.set_remote_pinned(sessA, base, True)        # 첫 원격 탭 핀
        key = srvA._win_key(link.windows[0])
        assert link.pinned_windows == {key}

        # 상류에 탭 하나 추가(재-attach 시 '추가분'이 될 탭).
        srvB.new_window(sessB)
        # ★ 같은 엔드포인트로 재-attach → 링크 교체.
        assert await srvA.remote_attach(sessA, endpoint=sockB)
        link2 = srvA._remotes_dict()[sockB]
        assert link2 is not link, "재-attach 는 새 링크로 교체된다(전제)"
        for _ in range(200):
            if len(link2.windows) == 2:
                break
            await asyncio.sleep(0.02)
        assert len(link2.windows) == 2, link2.windows
        assert link2.pinned_windows == {key}, \
            f"재-attach 로 핀 유실(사용자 보고 2026-07-15): {link2.pinned_windows}"
        rt = srvA._remote_tabs(len(sessA.tabs))
        assert [t["pinned"] for t in rt] == [True, False], rt  # 추가분만 비고정

        # 명시 detach(사용자 의사)는 sticky 를 버린다 → 다시 붙으면 새 뷰(전부 비고정).
        srvA.remote_detach(sockB)
        await asyncio.sleep(0)
        assert sockB not in srvA._remote_sticky_dict()
        assert await srvA.remote_attach(sessA, endpoint=sockB)
        assert srvA._remotes_dict()[sockB].pinned_windows == set()
    finally:
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_win_key_rejects_non_int_upstream_values():
    """검수 F-2: 상류 status(JSON)는 신뢰불가 입력이라 wid/index 가 해시불가(list/dict)
    거나 타입 혼재(1 과 "1")로 오면, 집합 멤버십 TypeError 로 링크가 끊기거나 핀 집합
    (int·str 혼재)의 sorted() 가 세션유지 재시작을 깨뜨린다. _win_key 는 int 만 인정하고
    (bool 도 제외) 그 외엔 None 으로 낮춘다."""
    srv, task, sock = await server_only()
    try:
        wk = srv._win_key
        assert wk({"wid": 3}) == 3
        assert wk({"wid": [1, 2]}) is None            # 해시불가 → None
        assert wk({"wid": {"x": 1}}) is None
        assert wk({"wid": "3"}) is None               # 문자열 → None(타입 혼재 차단)
        assert wk({"wid": True}) is None              # bool 제외
        assert wk({"index": 2}) == 2                  # wid 없으면 int index 폴백
        assert wk({"wid": None, "index": 5}) == 5
        assert wk({"wid": "x", "index": "y"}) is None
        assert wk({}) is None
    finally:
        await teardown(srv, task, sock)


async def test_remote_detach_all_clears_orphaned_sticky():
    """검수 F-3: detach-all(remote-detach 인자 없음)은 재연결을 이미 포기한 호스트
    (reconn·remotes 어디에도 없는 고아 sticky)까지 버려야 한다 — 안 그러면 나중에 그
    호스트에 다시 attach 할 때 옛 핀/분리가 되살아나 '명시 detach 만 버림' 계약을 어긴다."""
    srv, task, sock = await server_only()
    try:
        # 링크 객체 없이 sticky 만 남은 '고아' 호스트를 직접 주입(재연결 포기 상태 모사).
        d = srv._remote_sticky_dict()
        d["ghost-host"] = {"pinned": {1, 2}, "detached": {3}}
        assert "ghost-host" in srv._remote_sticky_dict()
        # detach-all → 살아있는 링크가 없어도 고아 sticky 가 통째로 비워져야 한다.
        srv.remote_detach(None)
        assert srv._remote_sticky_dict() == {}, \
            "detach-all 이 고아 sticky 를 남기면 재-attach 시 옛 핀이 되살아난다"
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


async def test_remote_same_host_tabs_command_merge():
    """드래그 머지(test_remote_same_host_tabs_drag_merge)의 **명령/키보드 대체 경로**
    E2E: 같은 원격 호스트의 두 원격 탭을 `:merge-remote-tab` **명령**(피커)으로 현재
    원격 탭에 pane 으로 합친다. 실 2서버 페더레이션 + 실 Textual 클라(make_app)로,
    명령 디스패치→피커 오픈→후보 필터(같은 host 비활성 원격탭만)→Enter 선택→
    select_pane_id+join_pane 릴레이→**업스트림이 실제로 병합**(탭 2→1·패널 1→2)까지
    전 구간을 구동한다. 로컬 트리는 불변(원격끼리, §1.7-c 예외)."""
    if os.name == "nt":
        from run import skip
        skip("Windows: 실 PTY 원격 탭 머지 E2E 는 macOS/Linux 권위(헤드리스 ConPTY 제외)")
    from pytmuxlib.clientscreens import MergeRemoteTabScreen
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    app = harness.make_app(sockA)
    try:
        sessB = srvB.ensure_default_session(80, 24)
        srvB.new_window(sessB)                        # B 에 원격 탭 2개
        assert len(sessB.tabs) == 2
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            nA = len([w for w in app.status.windows if not w.get("remote")])
            # ① 원격 attach(엔드포인트 직결 — 관심은 머지 '명령'이라 attach 는 직결)
            app.send_cmd("remote_attach", endpoint=sockB)
            await harness.wait_until(
                pilot,
                lambda: sum(1 for w in app.status.windows
                            if w.get("remote")) == 2,
                timeout=8.0)
            rtabs = sorted(w["index"] for w in app.status.windows
                           if w.get("remote"))
            # ② 첫 원격 탭으로 진입(view) = 머지 대상 탭
            app.send_cmd("select_window", index=rtabs[0])
            await harness.wait_until(
                pilot, lambda: app._active_remote_host() is not None,
                timeout=8.0)
            # ③ **명령**으로 머지 피커 실행(드래그 아님) → 실제로 열려야
            app._run_command("merge-remote-tab")
            await harness.wait_until(
                pilot, lambda: isinstance(app.screen, MergeRemoteTabScreen),
                timeout=4.0)
            # ④ 후보는 같은 host 의 **비활성** 원격 탭(둘째)만
            assert [it["i"] for it in app.screen._items] == [rtabs[1]], \
                app.screen._items
            # ⑤ 피커에서 실제 Enter 선택 → 콜백이 select_pane_id+join_pane 발사·릴레이
            await pilot.press("enter")
            # ⑥ 업스트림 B 가 실제로 합쳤는지: 탭 2→1, 그 창 패널 1→2
            await harness.wait_until(
                pilot,
                lambda: len(sessB.tabs) == 1
                and len(sessB.tabs[0].window.panes()) == 2,
                timeout=8.0)
            assert len(sessB.tabs) == 1, "원격 탭이 1개로 합쳐져야"
            assert len(sessB.tabs[0].window.panes()) == 2, \
                "목적지 원격 탭에 패널이 2개여야"
            # ⑦ 다운스트림 A 로컬 탭은 불변(원격끼리 머지)
            nA_after = len([w for w in app.status.windows
                            if not w.get("remote")])
            assert nA_after == nA, f"A 로컬 탭 변함: {nA}->{nA_after}"
    finally:
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


async def test_remote_token_log_only_to_requester_not_other_viewers():
    """§4.1 브로드캐스트 누출 필터: 같은 원격 호스트를 보는 다운스트림 클라가 둘일 때,
    한 클라가 연 토큰 팝업(request_token_log) 회신은 **요청한 그 클라에게만** 가고
    다른 뷰어에는 안 뜬다 — _remote_reader 패스스루가 종전엔 link.host 를 보는 **모든**
    클라에 응답을 뿌려, 클릭 안 한 클라에 토큰 팝업이 새던 것을 _req_token(요청 클라
    식별자 echo)로 라우팅해 막는다. nc_list/redraw 등 미태깅 메시지는 종전대로 뷰어
    전체 브로드캐스트(무영향)."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()     # 로컬(다운스트림)
    srvB, taskB, sockB = await server_only()     # 원격(업스트림)
    r1 = w1 = r2 = w2 = None
    try:
        srvB.ensure_default_session(80, 24)
        srvA.ensure_default_session(80, 24)
        # 클라1: attach → remote_attach → 원격 탭 진입
        r1, w1 = await _attach_client(sockA)
        await _read_until(r1, lambda m: m.get("t") == "status", what="c1 status")
        await write_msg(w1, {"t": "cmd", "action": "remote_attach",
                             "endpoint": sockB})
        stm = await _read_until(
            r1, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="c1 merged status")
        gidx = next(w["index"] for w in stm["windows"]
                    if w["name"].startswith("⇄"))
        await write_msg(w1, {"t": "cmd", "action": "select_window",
                             "index": gidx})
        await _read_until(r1, lambda m: m.get("t") == "layout", what="c1 layout")
        # 클라2: 같은 세션에 attach → 같은 원격 탭 진입(둘 다 같은 링크 뷰어)
        r2, w2 = await _attach_client(sockA)
        await _read_until(
            r2, lambda m: m.get("t") == "status"
            and any(w["name"].startswith("⇄") for w in m["windows"]),
            what="c2 merged status")
        await write_msg(w2, {"t": "cmd", "action": "select_window",
                             "index": gidx})
        await _read_until(r2, lambda m: m.get("t") == "layout", what="c2 layout")
        # 클라1만 토큰 로그 요청 → 클라1은 받고, 클라2는 안 받는다.
        await write_msg(w1, {"t": "cmd", "action": "request_token_log",
                             "limit": 5000})
        tl = await _read_until(r1, lambda m: m.get("t") == "token_log",
                               what="c1 token_log")
        # 라우팅 전용 필드는 클라에 노출되지 않는다(_remote_reader 가 제거).
        assert "_req_token" not in tl, tl
        await _assert_no(r2, lambda m: m.get("t") == "token_log",
                         window=1.5, what="c2 token_log 누출")
    finally:
        for w in (w1, w2):
            if w is not None:
                w.close()
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


async def test_remote_link_keepalive_lets_upstream_reap_dead_client():
    """다운스트림 federation 링크(A→B)가 업스트림 B 에 keepalive ping 을 보낸다.
    이 ping 이 없으면 B 의 死-클라 회수(_liveness_loop, ever_pinged 게이트)가 이
    federation 클라를 대상으로 인지하지 못해, 다운스트림 비정상 종료(랩탑 슬립·반열림
    TCP) 시 좀비 클라가 세션 공유 크기(_session_size=min)를 죽은 단말의 작은 크기로
    영구히 핀한다 — '다른 기계에서 접속했던 작은 크기로 표시' 버그. keepalive 로
    ever_pinged 가 서고, keepalive 가 끊긴(=유휴 초과) 좀비를 B 가 회수함을 검증한다."""
    if os.name == "nt":
        return
    from pytmuxlib import serverio
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    try:
        srvA._LINK_PING_INTERVAL = 0.05        # 빠른 keepalive(실 5s 대기 회피)
        srvA._RECONNECT_DELAYS = (3600,)       # 좀비 절단 후 재연결 미발화
        sessB = srvB.ensure_default_session(80, 24)
        sessA = srvA.ensure_default_session(80, 24)

        # A→B attach: B 에 A 의 federation 클라가 등록되고 keepalive ping 이 흐른다.
        assert await srvA.remote_attach(sessA, endpoint=sockB)

        async def _wait(pred, what, timeout=4.0):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                if pred():
                    return
                await asyncio.sleep(0.02)
            raise AssertionError(f"timeout: {what}")

        # 업스트림 B 가 keepalive ping 을 받아 이 클라를 회수 후보로 인지(ever_pinged).
        await _wait(lambda: bool(srvB.clients)
                    and all(getattr(c, "ever_pinged", False) for c in srvB.clients),
                    "upstream sees keepalive ping (ever_pinged)")
        assert srvB.clients, "업스트림에 federation 클라가 등록돼 있어야 함"

        # 좀비화 흉내: last_seen 을 유휴 임계 이전으로 밀어 keepalive 끊김을 재현.
        for c in srvB.clients:
            c.last_seen = time.monotonic() - serverio.CLIENT_IDLE_TIMEOUT - 1

        dropped = await srvB._evict_idle_clients()
        assert dropped >= 1, ("keepalive 끊긴 좀비 federation 클라를 업스트림이 "
                              "회수해야 세션 크기 핀이 풀린다", dropped)
        assert not srvB.clients, "회수 후 좀비 클라가 남지 않아야 함"
    finally:
        srvA.remote_shutdown()
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_remote_reader_bye_suppresses_reconnect():
    """수정(2026-07-11): _remote_reader 의 **루프 종료 사유별** 재연결 분기.
    업스트림 발 "bye"(detach_others=다른 뷰어 `detach -a`·kill-server·마지막 세션
    소멸 _notify_no_sessions = 고의 해제)는 **자동 재연결하지 않는다**. 종전엔
    bye·restarting·EOF 를 모두 reconnect=True 로 처리해, detach_others 로 원격 클라를
    떨궈도 즉시 되붙어(_session_size=min 재-핀) eviction 이 무효화되고 원격 뷰가 계속
    레터박스로 작게 남았다(kill/detach/30s대기 다 무효 보고 2026-07-11).
    "restarting"(세션유지 재시작)·EOF/오류는 종전대로 reconnect=True."""
    if os.name == "nt":
        return
    from pytmuxlib import serverremote
    from pytmuxlib.serverremote import RemoteLink
    srv, task, sock = await server_only()
    orig_read_msg = serverremote.read_msg
    try:
        captured = []

        async def _fake_drop(link, notify=True, reconnect=False):
            captured.append(reconnect)   # 재연결 인자만 포착(실 정리/재연결 미발화)
            link.alive = False
        srv.remote_drop = _fake_drop     # 인스턴스 한정 스텁

        async def _reconnect_arg_for(msgs):
            """주어진 프레임 시퀀스(소진 후 EOF)를 내보내는 reader 로 _remote_reader 를
            1회 돌리고, remote_drop 에 전달된 reconnect 인자를 돌려준다."""
            q = list(msgs)

            async def _fake_read(_reader):
                return q.pop(0) if q else None   # 소진되면 EOF(None)
            serverremote.read_msg = _fake_read
            captured.clear()
            link = RemoteLink("hostX", object(), object())
            await srv._remote_reader(link)
            return captured[-1] if captured else None

        assert await _reconnect_arg_for([{"t": "bye"}]) is False, \
            "업스트림 발 bye(고의 해제)는 자동 재연결하지 않아야 한다"
        assert await _reconnect_arg_for([{"t": "restarting"}]) is True, \
            "restarting(세션유지 재시작)은 자동 재연결 대상"
        assert await _reconnect_arg_for([]) is True, \
            "EOF(사고 끊김)는 자동 재연결 대상"
    finally:
        serverremote.read_msg = orig_read_msg
        await teardown(srv, task, sock)
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

    # 버스트 감지 시 _on_pane_data 가 드레인 경로로 돌리며 pause/resume 를 부른다
    # (PtyProcess 기본 no-op 과 동치 — 스파이도 갖춘다).
    def pause_reader(self): pass
    def resume_reader(self): pass

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


async def test_remote_attach_propagates_ambiguous_wide():
    """모호폭(East Asian Ambiguous) wide 단말의 다운스트림이 원격 attach 하면 그 wide
    모드가 업스트림 hello 에 ambig=wide 로 실려 전파된다 — 업스트림 pyte 격자도
    모호폭=2 로 맞춰 원격 Claude TUI 가 격자에 정확히 앉게 한다. 안 그러면 업스트림은
    narrow 로 레이아웃하고 다운스트림 단말은 wide 로 그려 한 줄이 1칸씩 밀려 좌우
    겹침·패널 아웃라인 침범이 난다(원격 탭만의 회귀, p4 60827 후속). narrow 단말이면
    키를 안 실어 업스트림 narrow 유지. cellwidth 전역은 인프로세스 2서버가 공유하므로
    값이 아니라 **전송된 hello** 로 A/B 를 가른다(federation 테스트 함정)."""
    if os.name == "nt":
        return
    from pytmuxlib import cellwidth, serverremote
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    captured = []
    orig_write = serverremote.write_msg

    async def _spy(writer, msg):
        if isinstance(msg, dict) and msg.get("t") == "hello":
            captured.append(dict(msg))
        return await orig_write(writer, msg)

    try:
        srvB.ensure_default_session(80, 24)
        sessA = srvA.ensure_default_session(80, 24)
        serverremote.write_msg = _spy

        # ① wide 단말: hello.ambig == "wide"
        cellwidth.set_ambiguous_wide(True)
        try:
            assert await srvA.remote_attach(sessA, endpoint=sockB)
        finally:
            cellwidth.set_ambiguous_wide(False)
        assert captured and captured[-1].get("ambig") == "wide", captured
        await srvA.remote_drop(srvA._remotes_dict()[sockB], notify=False)

        # ② narrow 단말: ambig 키 부재(현행 동작·무영향)
        captured.clear()
        assert await srvA.remote_attach(sessA, endpoint=sockB)
        assert captured and "ambig" not in captured[-1], captured
    finally:
        serverremote.write_msg = orig_write
        await teardown(srvA, taskA, sockA)
        await teardown(srvB, taskB, sockB)


async def test_relay_frame_shape_and_ctrl_strip():
    """M1/L3 페더레이션 신뢰경계 헬퍼: 업스트림(untrusted) 프레임을 다운스트림 클라에
    재브로드캐스트하기 전 필수 shape 검증 + 원격 유래 문자열 C0/C1 제거."""
    from pytmuxlib.serverremote import _relay_frame_ok, _strip_ctrl
    # screen/screen-delta 는 pane + rows(list) 필수 — 누락/오타입은 드롭
    assert _relay_frame_ok("screen", {"pane": 1, "rows": []})
    assert not _relay_frame_ok("screen", {"pane": 1})                 # rows 누락
    assert not _relay_frame_ok("screen", {"rows": []})                # pane 누락
    assert not _relay_frame_ok("screen-delta", {"pane": 1, "rows": "x"})  # 비-list
    assert not _relay_frame_ok("screen-delta", {"pane": 1})           # rows 누락
    # 기타 t 는 통과(클라가 get 기반 처리)
    assert _relay_frame_ok("layout", {"active": 3})
    assert _relay_frame_ok("nc_list", {})
    # C0/C1 제어문자 제거(상태줄 스푸핑·커서 이동 방지)
    s = _strip_ctrl("a\x1bb\x07c\x9bd")
    assert "\x1b" not in s and "\x07" not in s and "\x9b" not in s
    assert s == "a b c d", repr(s)


async def test_client_dispatch_guarded_survives_malformed_remote_frame():
    """M1 심층방어: 원격이 relay 한 손상 screen 프레임(rows 누락)이 _dispatch 까지
    닿아도 reader 워커가 죽지 않는다 — _dispatch_guarded 가 예외를 삼키고 카운터만
    올리며 앱은 생존한다. 서버측 _relay_frame_ok 가 1차 방어, 이건 심층방어."""
    srv, task, sock = await server_only()
    try:
        srv.ensure_default_session(80, 24)
        app = harness.make_app(sock)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            # 정상 프레임: 카운터 불변
            app._dispatch_guarded({"t": "screen", "pane": 0,
                                   "rows": [[0, []]], "cursor": None})
            assert getattr(app, "_dispatch_errors", 0) == 0
            # 손상 프레임(rows 누락): 예외를 삼키고 카운터 1 + 앱 생존
            app._dispatch_guarded({"t": "screen", "pane": 0})
            assert app._dispatch_errors == 1
            assert app.is_running
            # 가드 없는 직접 _dispatch 는 여전히 raise(취약점 문서화)
            raised = False
            try:
                app._dispatch({"t": "screen", "pane": 0})
            except KeyError:
                raised = True
            assert raised, "malformed screen frame should raise in raw _dispatch"
    finally:
        await teardown(srv, task, sock)


async def test_ssh_self_attach_rejected_by_token():
    """L2(보안검수 2026-07-03): ssh host 경로로 자기 자신에 되붙으면(원격=자기 데몬이
    자기 TOKEN 회신) _is_self_ssh_token 이 True → attach 거부. endpoint 경로의 self-attach
    가드(sock_path 비교)가 host 경로를 못 잡던 갭을 토큰 비교로 메운다."""
    srv, task, sock = await server_only()
    try:
        srv.auth_token = "deadbeefcafe"
        assert srv._is_self_ssh_token("deadbeefcafe") is True
        assert srv._is_self_ssh_token("some-other-token") is False
        # auth_token 미설정이면 False(구경로/테스트 안전)
        srv.auth_token = None
        assert srv._is_self_ssh_token("deadbeefcafe") is False
    finally:
        await teardown(srv, task, sock)


async def test_hostile_upstream_windows_container_does_not_wedge_status():
    """[검수 F-A 회귀, 2026-07-17] 상류가 보낸 `windows` **컨테이너 자체**가 신뢰불가다.

    F-2 는 개별 필드(wid/index)를 방어했지만 컨테이너는 무검증이라
    `link.windows = msg.get("windows", [])` 가 `["x"]`·`"abc"`·`{"a":1}` 을 그대로
    실었다 → `_visible_windows` 의 `rw.get()` 이 AttributeError. 그게 하필 **flush
    루프**(serverio `_status_msg` 호출부, try 밖)에서 터져 `_on_flush_done` 이 루프를
    재시작→재폭발을 33ms 마다 반복 → **모든 세션의 모든 클라가 프레임을 한 장도 못
    받는 영구 wedge** + error.log 무한 증식. 복구는 `:remote-detach` 를 사용자가
    알아맞히는 것뿐이었다.

    여기서는 경계 정규화(_sanitize_windows)가 이 프레임들을 걸러 `_status_msg` 가
    **예외 없이** 성립하는지 본다."""
    from pytmuxlib.serverremote import RemoteLink
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        link = RemoteLink("hostX", object(), object())
        srv._remotes_dict()["hostX"] = link
        for hostile in (["x"], "abc", {"a": 1}, [1, 2], [None], 42,
                        [{"wid": 1}, "x", {"wid": 2}]):
            link.windows = srv._sanitize_windows(hostile)
            # 정규화 뒤엔 전부 dict 여야 하고, 상태 조립이 예외 없이 성립해야 한다.
            assert all(isinstance(w, dict) for w in link.windows), hostile
            srv._status_msg(sess)                 # 회귀면 AttributeError
            srv._remote_tabs(0)
        # 정상 항목은 살리고 악성만 버린다(부분 수용 — 링크를 통째로 죽이지 않는다).
        link.windows = srv._sanitize_windows([{"wid": 1}, "x", {"wid": 2}])
        assert [w["wid"] for w in link.windows] == [1, 2]
    finally:
        await teardown(srv, task, sock)


async def test_hostile_upstream_tab_name_stripped_and_capped():
    """[검수 F-E 회귀, 2026-07-17] 상류 탭 이름은 탭바에 그대로 렌더되므로 제어문자를
    제거하고 길이를 캡한다.

    로컬 `rename_window` 는 같은 이유로 C0/C1/DEL 을 이미 제거한다(S-2, 2026-07-10) —
    그런데 **신뢰불가하다고 모듈 docstring 이 선언한 상류 경로엔 그 필터가 없었다**.
    `{"name": "\\x1b[2J\\x1b[H pwned"}` 가 탭바 Segment 까지 통과했고, 길이 상한도 없어
    "A"*1_000_000 이 매 캐시미스마다 글자별 폭 계산을 태웠다."""
    srv, task, sock = await server_only()
    try:
        wins = srv._sanitize_windows([{"wid": 1, "name": "\x1b[2J\x1b[H pwned\r\n"}])
        nm = wins[0]["name"]
        assert not any(ord(c) < 0x20 or 0x7f <= ord(c) <= 0x9f for c in nm), repr(nm)
        assert "pwned" in nm                       # 표시 자체는 보존(제어문자만 제거)
        long = srv._sanitize_windows([{"wid": 1, "name": "A" * 100_000}])
        assert len(long[0]["name"]) <= srv._REMOTE_NAME_MAX
        # 비-str 이름도 포맷 시 터지지 않게 빈 문자열로 낮춘다.
        assert srv._sanitize_windows([{"wid": 1, "name": 12345}])[0]["name"] == ""
    finally:
        await teardown(srv, task, sock)


async def test_sticky_reset_when_upstream_instance_changes():
    """[검수 F-1 회귀, 2026-07-17] 상류가 재시작하면 그 호스트의 sticky(핀/단일-탭 분리)
    를 **리셋**한다 — 옛 키를 새 탭에 오매칭시키지 않는다.

    `Tab.wid` 는 `model._win_seq` 전역 카운터에서 나오고 그 카운터는 **영속되지 않는다**
    (serverpersist 는 wid 를 저장조차 안 한다). 상류가 재시작하면 살아남은 탭들이 wid 를
    **1..N 으로 재발급**받아 하류에 남은 옛 키와 겹친다. serverpersist 의 "상류도
    재시작하면 그 detach 는 잊혀 다시 나타난다(안전한 저하)" 주석은 **사실과 정반대**
    였다 — 실증: 상류 탭이 [2,3,4]→[1,2,3] 이 되자 사용자가 핀한 T2 대신 **T3 가 핀**
    되고 T2 는 되살아났다. 새 wid 가 하필 옛 키와 **최대로** 겹치기 때문이다.

    수정: 상류가 status 에 `boot_id`(인스턴스 부팅 id)를 싣고, 하류가 그걸 latch 해
    **바뀌면** sticky 를 비운다(조용한 오매칭 → 정직한 초기화)."""
    from pytmuxlib.serverremote import RemoteLink
    srv, task, sock = await server_only()
    try:
        link = RemoteLink("hostX", object(), object())
        srv._remotes_dict()["hostX"] = link
        srv._remote_sticky_bind(link)

        # 상류 인스턴스 A: 탭 churn 뒤 wid=[2,3,4]. 사용자가 T2(wid 2)를 핀.
        srv._remote_boot_check(link, {"boot_id": "AAA"})
        link.windows = srv._sanitize_windows(
            [{"wid": 2, "name": "T2"}, {"wid": 3, "name": "T3"},
             {"wid": 4, "name": "T4"}])
        link.pinned_windows.add(srv._win_key(link.windows[0]))
        link.detached_windows.add(srv._win_key(link.windows[2]))

        # 상류 재시작 → 인스턴스 B. 같은 탭들이 wid=[1,2,3] 으로 재발급.
        srv._remote_boot_check(link, {"boot_id": "BBB"})
        link.windows = srv._sanitize_windows(
            [{"wid": 1, "name": "T2"}, {"wid": 2, "name": "T3"},
             {"wid": 3, "name": "T4"}])
        pinned = [rw["name"] for rw in link.windows
                  if srv._win_key(rw) in link.pinned_windows]
        assert pinned == [], f"상류 재시작 뒤 엉뚱한 탭이 핀됨(F-1): {pinned}"
        assert not link.detached_windows, "분리 키가 새 인스턴스에 오매칭됨(F-1)"
        assert link.boot_id == "BBB"

        # 같은 인스턴스가 계속 status 를 보내면 sticky 는 **유지**된다(과잉 리셋 금지).
        link.pinned_windows.add(srv._win_key(link.windows[0]))
        srv._remote_boot_check(link, {"boot_id": "BBB"})
        assert [rw["name"] for rw in link.windows
                if srv._win_key(rw) in link.pinned_windows] == ["T2"]

        # 구버전 상류(boot_id 미전송)·신뢰불가 타입은 종전 거동(리셋 안 함).
        for bad in ({}, {"boot_id": None}, {"boot_id": 123}, {"boot_id": ""}):
            srv._remote_boot_check(link, bad)
            assert link.pinned_windows, f"구버전/불량 boot_id({bad})에 핀이 날아감"
    finally:
        await teardown(srv, task, sock)


async def test_sticky_sets_stay_shared_refs_after_boot_reset():
    """[F-1 수정의 함정] boot 리셋은 집합을 **재대입하지 말고 clear** 해야 한다.

    핀/분리 집합은 호스트별 sticky 저장소(`_remote_sticky`)와 **객체를 공유**한다
    (_remote_sticky_bind). 재대입하면 저장소는 옛 객체를 계속 들고 있어 이후 재-attach
    가 리셋 이전 상태를 되살린다([[remote-pin-sticky-host-lifetime]] 계열)."""
    from pytmuxlib.serverremote import RemoteLink
    srv, task, sock = await server_only()
    try:
        link = RemoteLink("hostY", object(), object())
        srv._remotes_dict()["hostY"] = link
        srv._remote_sticky_bind(link)
        store = srv._remote_sticky_dict()["hostY"]
        srv._remote_boot_check(link, {"boot_id": "A"})
        link.pinned_windows.add(7)
        srv._remote_boot_check(link, {"boot_id": "B"})       # 리셋 발동
        assert link.pinned_windows is store["pinned"], "핀 집합이 재대입됨(공유 참조 깨짐)"
        assert link.detached_windows is store["detached"], "분리 집합이 재대입됨"
        assert not store["pinned"], "저장소 쪽이 안 비워짐 — 재-attach 로 되살아난다"
    finally:
        await teardown(srv, task, sock)


async def test_remote_status_override_keeps_local_opt_and_plugin_authority():
    """[검수 F-D 회귀, 2026-07-17] 원격 뷰 status 는 **로컬 서버가 권위**인 옵션·플러그인
    필드를 상류 값으로 덮지 않는다. **정상 상류에서도 발동하는 correctness 버그**다.

    회귀 전: `_remote_status_override` 가 `dict(link.last_status)` 로 상류 status 를 통째
    기반 삼고 4개 키(t·session·single_border·windows)만 되덮었다 → 상류의
    `disabled_plugins`·8개 server_opts(vt_parser·window_size·monitor_* 등)가 하류 클라로
    흘러가 `client.set_disabled` 로 **하류의 플러그인 레지스트리를 갈아치우고** `:settings`
    가 상류 값을 로컬 값으로 표시했다. 사용자가 그걸 "고치면" set_* 는 릴레이 대상이
    아니라 로컬 opts.json 에 상류 값을 썼다. 극성을 뒤집어 로컬 권위 필드를 되덮는다.

    반대 계약도 함께 고정: **원격 패널 상태와 플러그인 동적 헤더**(claude_model 등)는
    상류가 권위이므로 그대로 전달돼야 한다."""
    from pytmuxlib.serverremote import RemoteLink
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.vt_parser = "native"
        srv.plugins.disabled = set()
        link = RemoteLink("hostX", object(), object())
        srv._remotes_dict()["hostX"] = link

        class _C:
            remote_view = "hostX"
            session = sess
        link.last_status = {
            "t": "status", "disabled_plugins": ["claude-code", "rec"],
            "vt_parser": "pyte", "window_size": "largest",
            "monitor_activity": True, "auto_rename": False,
            "active_pane": 999, "zoomed": True, "pane_title": "REMOTE-PANE",
            "claude_model": "opus", "windows": [],
        }
        out = srv._remote_status_override(sess, _C())

        # 로컬 권위: 상류 값이 새면 안 된다.
        assert out["disabled_plugins"] == sorted(srv.plugins.disabled) == [], out["disabled_plugins"]
        assert out["vt_parser"] == "native", out["vt_parser"]
        assert out["window_size"] == srv.window_size, out["window_size"]
        assert out["monitor_activity"] is False, out["monitor_activity"]
        # 원격 스코프: 상류 값이 와야 한다.
        assert out["active_pane"] == 999
        assert out["pane_title"] == "REMOTE-PANE"
        assert out["zoomed"] is True
        # 플러그인 동적 헤더(로컬 _status_msg 엔 없는 키)는 상류 권위로 전달.
        assert out["claude_model"] == "opus", out.get("claude_model")
    finally:
        await teardown(srv, task, sock)
