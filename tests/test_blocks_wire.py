"""블록 메시지의 **와이어 계약** — 광고한 클라에게만 간다.

# 왜 이 테스트가 따로 있나

`test_blocks.py` 는 경계 판정(어디부터 어디까지가 블록인가)을 본다. 여기서 보는 것은
그 결과가 **누구에게 가는가**다.

블록은 §10-11 P4 에서 새로 생긴 기능이고, 기존 파이썬 Textual 클라는 이 기능을 모른다.
그 클라에 새 메시지가 흘러가면 ①모르는 `t` 를 매 프레임 파싱해 버리는 비용이 생기고
②대역폭이 늘고 ③혹시라도 처리 경로가 어긋나면 종전에 없던 오류가 난다. 그래서 계약은
**"광고한 클라에게만"** 이다 — `hello` 에 `caps: ["blocks"]` 를 실은 클라만 받는다.

이 계약이 깨지면 조용하다(기존 클라는 모르는 메시지를 그냥 무시하므로 아무도 신고하지
않는다). 그래서 테스트로 못박는다.
"""
import asyncio

import harness  # noqa: F401 (경로 설정)
from harness import running_server
from pytmuxlib import ipc
from pytmuxlib.protocol import PROTO_VERSION, read_msg, write_msg


async def _attach(sock, srv, caps=None):
    """클라 하나를 붙이고 (reader, writer) 를 준다. `caps` 를 주면 광고한다."""
    reader, writer = await ipc.open_connection(sock)
    hello = {"t": "hello", "proto": PROTO_VERSION, "cols": 80, "rows": 24,
             "token": srv.auth_token}
    if caps is not None:
        hello["caps"] = caps
    await write_msg(writer, hello)
    return reader, writer


async def _drain(reader, seconds=1.0):
    """지금 도착해 있는 메시지를 모은다. 조용해지면 멈춘다."""
    out = []
    try:
        while True:
            msg = await asyncio.wait_for(read_msg(reader), seconds)
            if msg is None:
                break
            out.append(msg)
    except asyncio.TimeoutError:
        pass
    return out


def _emit_shell_integration(srv, sess):
    """활성 패널에 셸 통합 시퀀스를 먹인다(명령 한 번의 실행)."""
    pane = sess.active_window.active_pane
    for seq in ("133;A", "133;C", "133;D;0"):
        pane.feed(f"\x1b]{seq}\x1b\\".encode())
    pane.dirty = True
    return pane


async def test_client_without_caps_never_receives_blocks():
    """계약: 기존 클라는 한 바이트도 더 받지 않는다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv)          # caps 광고 없음
        await _drain(reader)                                # 초기 attach 프레임 소비

        _emit_shell_integration(srv, sess)
        msgs = await _drain(reader, 1.5)

        kinds = {m.get("t") for m in msgs}
        assert "blocks" not in kinds, (
            f"광고하지 않은 클라에 blocks 가 갔다: {sorted(kinds)}")
        writer.close()


async def test_client_that_advertises_caps_receives_blocks():
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv, caps=["blocks"])
        await _drain(reader)

        _emit_shell_integration(srv, sess)
        msgs = await _drain(reader, 2.0)

        blocks = [m for m in msgs if m.get("t") == "blocks"]
        assert blocks, f"광고한 클라가 blocks 를 못 받았다: {[m.get('t') for m in msgs]}"
        payload = blocks[-1]["blocks"]
        assert payload, "블록 목록이 비었다"
        assert payload[-1]["state"] == "done"
        assert payload[-1]["exit"] == 0
        writer.close()


async def test_two_clients_get_different_frames_by_capability():
    """같은 세션에 둘이 붙어도 광고한 쪽에만 간다(프레임은 클라별로 조립된다)."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        old_r, old_w = await _attach(sock, srv)
        new_r, new_w = await _attach(sock, srv, caps=["blocks"])
        await _drain(old_r)
        await _drain(new_r)

        _emit_shell_integration(srv, sess)
        old_msgs = await _drain(old_r, 2.0)
        new_msgs = await _drain(new_r, 2.0)

        assert not [m for m in old_msgs if m.get("t") == "blocks"], \
            "구 클라에 blocks 가 샜다"
        assert [m for m in new_msgs if m.get("t") == "blocks"], \
            "신 클라가 blocks 를 못 받았다"
        old_w.close()
        new_w.close()


async def test_malformed_caps_does_not_break_the_handshake():
    """caps 가 목록이 아니어도 서버가 죽지 않는다(신뢰할 수 없는 입력)."""
    async with running_server() as (srv, task, sock):
        srv.ensure_default_session(80, 24)
        reader, writer = await ipc.open_connection(sock)
        await write_msg(writer, {"t": "hello", "proto": PROTO_VERSION,
                                 "cols": 80, "rows": 24, "token": srv.auth_token,
                                 "caps": "blocks"})       # 문자열(목록 아님)
        msgs = await _drain(reader, 1.5)
        assert any(m.get("t") == "layout" for m in msgs), \
            "핸드셰이크가 깨졌다 — 잘못된 caps 에도 붙어야 한다"
        assert not [m for m in msgs if m.get("t") == "blocks"], \
            "목록이 아닌 caps 를 능력으로 인정하면 안 된다"
        writer.close()


async def test_no_shell_integration_means_no_blocks_even_when_advertised():
    """계약 ①: 셸 통합을 안 깔면 광고해도 블록이 안 생긴다(우아한 저하)."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv, caps=["blocks"])
        await _drain(reader)

        pane = sess.active_window.active_pane
        pane.feed(b"echo hi\r\nhi\r\n")                    # 평범한 출력
        pane.dirty = True
        msgs = await _drain(reader, 1.5)

        assert not [m for m in msgs if m.get("t") == "blocks"], \
            "셸 통합이 없는데 블록이 생겼다"
        writer.close()


async def test_blocks_are_not_resent_every_frame():
    """30Hz 로 같은 목록을 다시 보내면 대역폭이 샌다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv, caps=["blocks"])
        await _drain(reader)

        _emit_shell_integration(srv, sess)
        await _drain(reader, 1.5)                           # 첫 전송 소비

        # 블록은 그대로 두고 화면만 계속 바꾼다. 고정 sleep 대신 **화면 프레임이
        # 실제로 오는 것**을 기다린다 — flush 가 돌았다는 증거가 그것이고, 느린 러너에서
        # 플레이크가 나지 않는다(신규 테스트 폴링 규약).
        pane = sess.active_window.active_pane
        for _ in range(3):
            pane.feed(b"x\r\n")
            pane.dirty = True
        msgs = await _drain(reader, 2.0)
        assert [m for m in msgs if m.get("t", "").startswith("screen")], \
            "화면 프레임이 안 왔다 — flush 가 안 돌아 이 테스트가 공허해진다"

        assert not [m for m in msgs if m.get("t") == "blocks"], \
            "블록이 안 바뀌었는데 다시 보냈다"
        writer.close()


async def test_a_client_attaching_later_receives_existing_blocks():
    """붙는 시점에 이미 있던 블록을 받아야 한다.

    실제로 물린 결함이다: 블록을 "바뀌었을 때만" 보내면, 나중에 붙은 클라는 다음 명령이
    실행될 때까지 아무것도 못 본다. 화면은 attach 즉시 full 로 받는데 블록만 안 오는
    비대칭이라, 사용자에겐 "블록 기능이 안 되는" 것으로 보인다.
    """
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        # 아무도 안 붙은 상태에서 명령이 하나 돌았다.
        _emit_shell_integration(srv, sess)

        reader, writer = await _attach(sock, srv, caps=["blocks"])
        msgs = await _drain(reader, 2.0)

        blocks = [m for m in msgs if m.get("t") == "blocks"]
        assert blocks, (
            "나중에 붙은 클라가 기존 블록을 못 받았다: "
            f"{sorted({m.get('t') for m in msgs})}")
        assert blocks[-1]["blocks"], "블록 목록이 비었다"
        writer.close()


async def test_a_late_client_without_caps_still_gets_nothing():
    """초기 동기화 경로에서도 광고하지 않은 클라는 제외된다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        _emit_shell_integration(srv, sess)

        reader, writer = await _attach(sock, srv)      # 광고 없음
        msgs = await _drain(reader, 1.5)
        assert not [m for m in msgs if m.get("t") == "blocks"], \
            "초기 동기화에서 구 클라에 blocks 가 샜다"
        writer.close()
