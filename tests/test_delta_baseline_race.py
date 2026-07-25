"""화면델타 베이스라인 레이스(검수 2026-07-17 잔여, 재조사 2026-07-25).

**재조사 결과 실재**: `_send_full` 이 `client._sent_rows[p.id] = rows` 를 **write 전에**
찍었다. 중간에 write 가 깨지면(파이프 끊김·타임아웃) 클라는 그 프레임을 못 받았는데
서버는 받은 것으로 기억한다. 이 경로는 대부분 `create_task(_send_full)` 라 예외가
로그로만 끝나고 **클라는 살아남으므로**, 다음 flush 가 허구의 기준 대비 델타를 보내
그 패널이 조용히 어긋난 채 굳는다(되돌린 줄이 영영 안 나간다).

`_flush_to_client` 쪽은 이미 안전했다 — 실패하면 클라를 통째로 떨궈 기준 자체가
사라진다. 구멍은 `_send_full` 한 곳이었다.

되돌리면 실패해야 하는 오라클:
  · 기준을 write **전에** 찍게 되돌리면 → test_failed_send_full_leaves_no_baseline 실패
  · 실패 시 기준 비우기를 빼면 → 같은 테스트 실패
  · dirty 를 write 전에 내리면 → test_failed_send_full_keeps_pane_dirty 실패
"""
import asyncio

import harness  # noqa: F401
from harness import server_only, teardown
from pytmuxlib import serverio


class _Boom(OSError):
    pass


class _Writer:
    """N 번째 write 부터 터지는 writer(파이프 끊김 시뮬)."""

    def __init__(self, fail_at):
        self.fail_at = fail_at
        self.n = 0
        self.buf = []

    def write(self, data):
        self.n += 1
        if self.n >= self.fail_at:
            raise _Boom("broken pipe")
        self.buf.append(data)

    async def drain(self):
        pass

    def close(self):
        pass

    transport = None


class _Client:
    def __init__(self, sess, fail_at):
        self.session = sess
        self.writer = _Writer(fail_at)
        self.write_lock = asyncio.Lock()
        self._sent_rows = {}
        self.remote_view = None
        self.lang = "ko"


async def test_failed_send_full_leaves_no_baseline():
    """송신이 중간에 깨지면 **기준이 남지 않아야** 한다 — 남으면 다음 델타가
    클라가 받은 적 없는 화면을 기준으로 계산돼 그 패널이 조용히 어긋난다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")               # 패널 2개 — 중간 실패를 만들기 쉽게
        c = _Client(sess, fail_at=3)             # layout → screen1 → **깨짐**
        try:
            await srv._send_full(c)
        except OSError:
            pass
        assert c._sent_rows == {}, (
            "못 보낸 프레임의 기준이 남았다 — 다음 델타가 어긋난다: %r"
            % list(c._sent_rows))
    finally:
        await teardown(srv, task, sock)


async def test_missing_baseline_falls_back_to_full_screen():
    """기준이 없으면 다음 프레임은 **full screen** 이다(= 복구 경로가 실제로 작동)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        c = _Client(sess, fail_at=99)
        rows, cursor = pane.render(True)
        frame = srv._screen_frame(c, pane.id, rows, cursor)
        assert b'"screen"' in frame and b"screen-delta" not in frame
        # 두 번째부터는 기준이 있으니 델타가 나온다(대조군 — 위가 공허하지 않게).
        rows2 = list(rows)
        rows2[0] = [["바뀐 줄", None, None]]
        frame2 = srv._screen_frame(c, pane.id, rows2, cursor)
        assert b"screen-delta" in frame2
    finally:
        await teardown(srv, task, sock)


async def test_successful_send_full_sets_baseline():
    """정상 경로는 종전대로 기준을 채운다(수정이 기능을 죽이지 않았다)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        c = _Client(sess, fail_at=99)
        await srv._send_full(c)
        assert len(c._sent_rows) == len(sess.active_window.panes())
    finally:
        await teardown(srv, task, sock)


async def test_failed_send_full_keeps_pane_dirty():
    """송신 실패 패널은 dirty 를 유지해야 다음 flush 가 다시 그린다 — 종전엔
    write 전에 dirty=False 라 재렌더조차 안 됐다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        panes = sess.active_window.panes()
        for p in panes:
            p.dirty = True
        c = _Client(sess, fail_at=2)             # layout 다음(첫 screen)에서 깨짐
        try:
            await srv._send_full(c)
        except OSError:
            pass
        assert any(p.dirty for p in panes), "실패했는데 전 패널이 clean 으로 남았다"
    finally:
        await teardown(srv, task, sock)


async def test_flush_path_drops_client_instead_of_desyncing():
    """대조군 — flush 경로는 실패 시 클라를 통째로 떨궈 기준 불일치가 원천적으로
    없다(이 계약이 깨지면 위 수정만으로는 부족해진다)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        c = _Client(sess, fail_at=1)
        srv.clients.append(c)
        await srv._flush_to_client(c, [b"frame"])
        assert c not in srv.clients
    finally:
        await teardown(srv, task, sock, allow_errors=("slow client dropped",))


# ── TAB-1: 자동 탭이름이 제어문자 세정을 우회하던 문제(재조사 2026-07-25) ──────

async def test_autorename_sanitizes_control_chars():
    """fg 명령 이름은 **프로세스가 정하는 값**(argv[0])이라 ESC/CR 을 심을 수 있다.

    수동 `rename_window` 는 2026-07-10(S-2)에 세정이 붙었는데 자동 이름 경로는
    `tab.name = cmd` 로 **직행**해 그대로 탭바에 흘렀다 — 같은 계약을 공유시킨다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        tab = sess.tabs[0]
        ap = tab.window.active_pane
        assert srv._autorename_apply(sess, tab, ap, "zsh\x1b[31m\r\nEVIL") is True
        assert tab.name == "zsh[31mEVIL"
        assert "\x1b" not in tab.name and "\r" not in tab.name
        # 전부 제어문자면 이름을 바꾸지 않는다(빈 이름으로 탭이 사라지지 않게).
        before = tab.name
        assert srv._autorename_apply(sess, tab, ap, "\x1b\x07\x00") is False
        assert tab.name == before
    finally:
        await teardown(srv, task, sock)


async def test_tab_name_length_capped():
    """거대한 이름은 탭바 파괴이자 매 status 대역 낭비 — 두 경로 모두 캡."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        tab = sess.tabs[0]
        ap = tab.window.active_pane
        srv._autorename_apply(sess, tab, ap, "x" * 5000)
        assert len(tab.name) == srv._TAB_NAME_MAX
        srv.rename_window(sess, "y" * 5000)
        assert len(sess.active_tab.name) == srv._TAB_NAME_MAX
    finally:
        await teardown(srv, task, sock)


async def test_manual_rename_still_sanitizes():
    """공용화가 기존 S-2 계약을 깨지 않았는지(대조군)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.rename_window(sess, "탭\r\n이름\x1b]0;x\x07")
        assert sess.active_tab.name == "탭이름]0;x"
    finally:
        await teardown(srv, task, sock)
