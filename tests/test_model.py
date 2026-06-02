"""Pane 모델 테스트: 스크롤백 / 대체 화면 / 와이드 문자 / 리사이즈 / respawn."""
import asyncio
import os

import harness
from harness import pane_text, server_only, teardown


async def test_feed_and_scrollback():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        for i in range(60):
            p.feed(f"line{i}\r\n".encode())
        assert len(p.screen.history.top) > 0, "스크롤백 누적"
        p.scroll_to("top")
        assert p.scroll == len(p.screen.history.top)
        p.scroll_to("bottom")
        assert p.scroll == 0
    finally:
        await teardown(srv, task, sock)


async def test_alt_screen_isolation():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p.feed(b"SHELL_LINE_ONE\r\n")
        assert "SHELL_LINE_ONE" in pane_text(p)
        p.feed(b"\x1b[?1049h")          # 대체 화면 진입
        assert p.alt_active is True
        p.feed(b"\x1b[2J\x1b[HALT_UI")
        t = pane_text(p)
        assert "ALT_UI" in t and "SHELL_LINE_ONE" not in t, "대체 화면 격리"
        p.feed(b"\x1b[?1049l")          # 이탈 → 메인 복원
        assert p.alt_active is False
        t = pane_text(p)
        assert "SHELL_LINE_ONE" in t and "ALT_UI" not in t, "메인 복원"
    finally:
        await teardown(srv, task, sock)


async def test_alt_marker_split_across_feeds():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p.feed(b"\x1b[?10")
        p.feed(b"49h")                  # 마커가 feed 경계로 쪼개짐
        assert p.alt_active is True
        p.feed(b"\x1b[?1049l")
        assert p.alt_active is False
    finally:
        await teardown(srv, task, sock)


async def test_wide_char_render():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(40, 10)
        p = sess.active_window.active_pane
        p.feed(b"\x1b[2J\x1b[H")
        p.feed("AB\xed\x95\x9cCD".encode() if False else "AB가CD\r\n".encode())
        rows, _ = p.render(False)
        line0 = "".join(seg[0] for seg in rows[0])
        # 와이드 문자(가) 다음 글자가 밀리지 않고 '가CD' 가 인접
        assert "가CD" in line0, repr(line0)
        # render 는 연속 셀('')을 공백으로 만들지 않는다(밀림 방지)
        assert "  CD" not in line0, "연속 셀이 공백으로 새지 않아야 함"
    finally:
        await teardown(srv, task, sock)


async def test_resize_keeps_content():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p.feed(b"RESIZE_MARKER\r\n")
        p.resize(120, 30)
        assert p.screen.columns == 120 and p.screen.lines == 30
        assert "RESIZE_MARKER" in pane_text(p)
    finally:
        await teardown(srv, task, sock)


async def test_respawn_pane():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        old_pid, old_id = p.child_pid, p.id
        await asyncio.sleep(0.2)
        srv.respawn_pane(sess)
        assert p.child_pid != old_pid and p.id == old_id
        # 옛 프로세스 종료
        try:
            os.kill(old_pid, 0)
            alive = True
        except OSError:
            alive = False
        assert not alive, "respawn 후 옛 셸 종료"
    finally:
        await teardown(srv, task, sock)
