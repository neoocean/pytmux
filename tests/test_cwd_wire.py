"""패널 cwd 메시지의 **와이어 계약**(§10-21ⓧ2 / pytmux-24).

# 왜 블록과 따로 있나

값은 문자열 하나인데 블록 목록은 최대 500개다. 경로만 풀면 되는 클라(정본 Textual)가
그 목록을 통째로 받는 것은 `caps` 게이트가 막으려던 바로 그 비용이라, cwd 는 자기
프레임으로 간다. `test_blocks_wire.py` 와 같은 계약을 같은 모양으로 못박는다.

되돌리면 실패해야 하는 오라클:
  · caps 게이트를 빼면 → test_client_without_caps_never_receives_cwd 실패
  · cwd 를 blocks 의 dirty 훅에 얹으면 → test_blocks_and_cwd_do_not_starve_each_other
    실패(둘이 표식을 나눠 가지면 먼저 부른 쪽만 나간다)
  · 값 비교를 빼고 매 프레임 보내면 → test_cwd_is_not_resent_every_frame 실패
  · _send_full 의 초기 1회를 빼면 → test_a_client_attaching_later_receives_cwd 실패
"""
import asyncio

import harness  # noqa: F401 (경로 설정)
from harness import running_server
from pytmuxlib import ipc
from pytmuxlib.protocol import PROTO_VERSION, read_msg, write_msg

#: OSC 7 이 알려 주는 경로. 앞의 `/` 는 규격대로다(`file://<host><path>`).
#: 드라이브 문자를 안 써 Windows 살균 분기(`/D:/a` → `D:/a`)를 안 타므로 값이 OS 를
#: 안 탄다 — 이 테스트가 보는 것은 경로 모양이 아니라 **누구에게 가는가**다.
CWD = "/home/me/proj"


async def _attach(sock, srv, caps=None):
    reader, writer = await ipc.open_connection(sock)
    hello = {"t": "hello", "proto": PROTO_VERSION, "cols": 80, "rows": 24,
             "token": srv.auth_token}
    if caps is not None:
        hello["caps"] = caps
    await write_msg(writer, hello)
    return reader, writer


async def _drain(reader, seconds=1.0):
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


def _emit_cwd(sess, path=CWD):
    """활성 패널에 셸이 보내는 `OSC 7` 을 먹인다(= 사용자가 `cd` 를 쳤다)."""
    pane = sess.active_window.active_pane
    pane.feed(f"\x1b]7;file://host{path}\x1b\\".encode())
    pane.dirty = True
    return pane


def _cwds(msgs):
    return [m for m in msgs if m.get("t") == "cwd"]


async def test_client_without_caps_never_receives_cwd():
    """계약: 광고 안 한 클라는 한 바이트도 더 받지 않는다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv)          # caps 광고 없음
        await _drain(reader)

        _emit_cwd(sess)
        msgs = await _drain(reader, 1.5)

        assert not _cwds(msgs), \
            "광고하지 않은 클라에 cwd 가 갔다: %r" % sorted({m.get("t") for m in msgs})
        writer.close()


async def test_client_that_advertises_caps_receives_cwd():
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv, caps=["cwd"])
        await _drain(reader)

        pane = _emit_cwd(sess)
        msgs = await _drain(reader, 2.0)

        got = _cwds(msgs)
        assert got, "광고한 클라가 cwd 를 못 받았다: %r" % [m.get("t") for m in msgs]
        assert got[-1]["cwd"] == CWD, got[-1]
        assert got[-1]["pane"] == pane.id, "패널 id 가 틀리면 클라가 남의 기준으로 푼다"
        writer.close()


async def test_blocks_and_cwd_do_not_starve_each_other():
    """★ 둘 다 광고한 클라는 **둘 다** 받는다.

    함정이 여기 있다: 블록의 변경 게이트(`pane_blocks_changed`)는 **물어보면 표식을
    내린다**. cwd 를 그 게이트에 얹으면 먼저 부른 쪽이 표식을 지워 다른 쪽이 영영 안
    나간다 — 그리고 그건 조용하다(둘 중 하나만 안 오는 것을 아무도 신고하지 않는다).
    그래서 cwd 는 마지막으로 보낸 값과 비교해서 낸다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv, caps=["blocks", "cwd"])
        await _drain(reader)

        pane = sess.active_window.active_pane
        pane.feed(f"\x1b]7;file://host{CWD}\x1b\\".encode())
        for seq in ("133;A", "133;C", "133;D;0"):
            pane.feed(f"\x1b]{seq}\x1b\\".encode())
        pane.dirty = True
        msgs = await _drain(reader, 2.0)

        assert _cwds(msgs), "cwd 가 안 왔다(블록이 표식을 먼저 가져갔나)"
        assert [m for m in msgs if m.get("t") == "blocks"], \
            "blocks 가 안 왔다(cwd 가 표식을 먼저 가져갔나)"
        writer.close()


async def test_cwd_is_not_resent_every_frame():
    """같은 값이면 침묵한다 — 안 그러면 30Hz 로 같은 문자열이 흐른다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv, caps=["cwd"])
        await _drain(reader)

        pane = _emit_cwd(sess)
        first = await _drain(reader, 2.0)
        assert _cwds(first), "첫 값이 안 왔다"

        # 화면만 계속 바꾼다(cwd 는 그대로). 고정 대기를 안 끼우는 이유: `_drain` 이
        # 조용해질 때까지 읽으므로 이 뒤에 오는 프레임은 어차피 전부 잡힌다.
        for _ in range(3):
            pane.feed(b"hello\r\n")
            pane.dirty = True
        again = await _drain(reader, 1.5)

        assert not _cwds(again), "안 바뀐 cwd 를 다시 보냈다: %r" % _cwds(again)
        writer.close()


async def test_changed_cwd_is_sent_again():
    """`cd` 를 또 치면 새 값이 온다(위 침묵이 '한 번 보내고 끝'이 아니게)."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        reader, writer = await _attach(sock, srv, caps=["cwd"])
        await _drain(reader)

        _emit_cwd(sess)
        await _drain(reader, 2.0)
        _emit_cwd(sess, "/home/me/other")
        msgs = await _drain(reader, 2.0)

        got = _cwds(msgs)
        assert got and got[-1]["cwd"] == "/home/me/other", got
        writer.close()


async def test_a_client_attaching_later_receives_cwd():
    """나중에 붙은 클라도 **현재** cwd 를 받는다.

    이게 없으면 그 클라는 사용자가 `cd` 를 한 번 더 칠 때까지 그 패널의 상대경로를
    못 푼다 — 화면은 full 로 받는데 기준만 안 오는 비대칭이다."""
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        # 붙기 **전에** 바뀌었다. `feed` 가 세그멘터를 그 자리에서 세우고 `_send_full`
        # 은 그 값을 직접 읽으므로, 사이에 기다릴 것이 없다(플러시를 안 거친다).
        _emit_cwd(sess)

        reader, writer = await _attach(sock, srv, caps=["cwd"])
        msgs = await _drain(reader, 2.0)

        got = _cwds(msgs)
        assert got and got[-1]["cwd"] == CWD, \
            "늦게 붙은 클라가 현재 cwd 를 못 받았다: %r" % [m.get("t") for m in msgs]
        writer.close()


async def test_a_late_client_without_caps_still_gets_nothing():
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        _emit_cwd(sess)

        reader, writer = await _attach(sock, srv)
        msgs = await _drain(reader, 1.5)

        assert not _cwds(msgs), "초기 동기화 경로로 샜다"
        writer.close()
