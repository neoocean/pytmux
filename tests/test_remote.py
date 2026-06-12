"""§1.7 Stage 2 페더레이션 — 원격 pytmux 탭 흡수(서버=업스트림 클라이언트).

ssh 없이 **in-process 서버 2대를 실 소켓으로 직결**해 전 구간(와이어 2홉)을 검증한다:
로컬 A 의 실 클라 연결에서 remote_attach(B 엔드포인트) → 탭바 병합(⇄) → 원격 탭
선택(전역 index) → 업스트림 화면 전달 → 입력 릴레이 → 로컬 복귀/해제/링크 사망."""
import asyncio
import base64
import os
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


async def test_remote_link_death_recovers_viewer_to_local():
    """링크 사망(원격 서버 종료): 보던 클라는 로컬 화면으로 복귀(_send_full)하고
    탭바에서 ⇄ 탭이 제거된다 — '재접속 루프' 대신 명시적 끊김 처리."""
    if os.name == "nt":
        return
    srvA, taskA, sockA = await server_only()
    srvB, taskB, sockB = await server_only()
    reader = writer = None
    try:
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
