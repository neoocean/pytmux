"""프로토콜/설정 헬퍼 단위 테스트 (서버/클라 기동 불필요).

(Claude 휴리스틱 테스트는 docs/HANDOFF.md §11 분리로 test_claude.py 로 이전.)"""
import tempfile

import harness  # noqa: F401  (경로 설정)
import pytmux


async def test_read_msg_frame_length_bounded_and_robust():
    """read_msg 가 ① 정상 프레임 왕복 ② 상한 초과 길이 → None(OOM 방지)
    ③ 비-JSON/손상 페이로드 → None(예외 아님) 을 보장한다(견고성·DoS 가드)."""
    import asyncio
    from pytmuxlib.protocol import read_msg, frame_msg, MAX_FRAME

    # ① 정상 왕복
    r = asyncio.StreamReader()
    r.feed_data(frame_msg({"t": "hello", "x": 1}))
    r.feed_eof()
    assert (await read_msg(r)) == {"t": "hello", "x": 1}

    # ② 상한 초과 길이 헤더 → 페이로드를 읽지 않고 None
    r = asyncio.StreamReader()
    r.feed_data((MAX_FRAME + 1).to_bytes(4, "big"))
    r.feed_eof()
    assert (await read_msg(r)) is None

    # ③ 비-JSON/비-UTF8 페이로드 → 예외 없이 None
    r = asyncio.StreamReader()
    junk = b"\xff\xfe not json {"
    r.feed_data(len(junk).to_bytes(4, "big") + junk)
    r.feed_eof()
    assert (await read_msg(r)) is None

    # ④ 잘린 프레임: 헤더는 N 을 약속하지만 페이로드가 그보다 짧고 EOF →
    #    IncompleteReadError 를 None 으로(예외 전파 없이 연결 종료 신호).
    r = asyncio.StreamReader()
    body = frame_msg({"t": "hello"})
    r.feed_data(body[:-3])          # 마지막 3바이트 누락(잘림)
    r.feed_eof()
    assert (await read_msg(r)) is None

    # ⑤ 헤더만 오고 페이로드 0바이트 + EOF → None(부분 헤더/조기 EOF 견고성)
    r = asyncio.StreamReader()
    r.feed_data((10).to_bytes(4, "big"))
    r.feed_eof()
    assert (await read_msg(r)) is None

    # ⑥ 미지 타입 't' 라도 read_msg 는 dict 를 그대로 돌려준다(타입 판별은 상위 책임).
    r = asyncio.StreamReader()
    r.feed_data(frame_msg({"t": "totally-unknown", "v": 9}))
    r.feed_eof()
    assert (await read_msg(r)) == {"t": "totally-unknown", "v": 9}


async def test_write_msg_none_writer_guard():
    """종료/재연결 레이스로 writer 가 None 이어도 write_msg/write_frames 는
    AttributeError 를 던지지 않고 False 를 돌려준다(awaited 안 된 태스크 크래시 방지)."""
    from pytmuxlib.protocol import write_msg, write_frames, frame_msg

    assert (await write_msg(None, {"t": "hello"})) is False
    assert (await write_frames(None, [frame_msg({"t": "x"})])) is False
    # 빈 프레임은 writer 와 무관하게 항상 True(아무것도 안 보냄).
    assert (await write_frames(None, [])) is True


async def test_key_to_ctrl_bytes():
    assert pytmux._key_to_ctrl_bytes("ctrl+a") == b"\x01"
    assert pytmux._key_to_ctrl_bytes("ctrl+b") == b"\x02"


async def test_tmux_key_to_textual():
    assert pytmux._tmux_key_to_textual("C-a") == "ctrl+a"
    assert pytmux._tmux_key_to_textual("ctrl+x") == "ctrl+x"


async def test_load_config():
    cp = tempfile.mktemp(suffix=".conf")
    with open(cp, "w") as f:
        f.write("# c\nset prefix C-a\nset mouse off\nset mode-keys emacs\n"
                "set status-bg blue\nset status-left L#S\n"
                "bind | split-window -h\nalias v split-window -h\n"
                "hook after-new-window rename-window H\n")
    cfg = pytmux.load_config(cp)
    assert cfg["prefix"] == "ctrl+a"
    assert cfg["mouse"] is False
    assert cfg["mode_keys"] == "emacs"
    assert cfg["status_bg"] == "blue" and cfg["status_left"] == "L#S"
    assert cfg["bindings"]["|"] == "split-window -h"
    assert cfg["aliases"]["v"] == "split-window -h"
    assert cfg["hooks"]["after-new-window"] == "rename-window H"
