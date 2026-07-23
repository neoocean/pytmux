"""DEC 2026 동기화 출력 '지원 광고'(DECRQM 응답) — perf-pytmux.md L1.

배칭 자체(?2026h~?2026l 사이 flush 지연)는 이미 model.update_sync_output·
_flush_loop 가 담당하고 test_server 가 검증한다. 여기서는 그 배칭이 Textual 앱(tx)에도
켜지도록, 자식의 지원 질의(?2026$p)에 pytmux 가 응답하는 부분만 검증한다.
"""

import harness  # noqa: F401  (sys.path 부트스트랩)
from harness import server_only, teardown


async def test_sync_query_scan_and_reply_format():
    from pytmuxlib.serverpty import SYNC_QUERY, SYNC_REPLY, _has_query

    # 응답은 Textual 의 _re_terminal_mode_response(^ESC[?<id>;<d>$y)와 일치해야
    # 하고, setting_parameter(2)>0 이어야 tx 가 지원으로 해석해 감싸기를 켠다.
    assert SYNC_REPLY == b"\x1b[?2026;2$y"
    assert _has_query(b"pre" + SYNC_QUERY + b"post", b"", SYNC_QUERY)
    assert not _has_query(b"nothing", b"", SYNC_QUERY)
    # read 경계 분할(직전 청크 꼬리 carry + 이번 청크 머리)도 감지.
    half = len(SYNC_QUERY) // 2
    assert _has_query(SYNC_QUERY[half:] + b"x", SYNC_QUERY[:half], SYNC_QUERY)


async def test_sync_query_gets_pytmux_reply():
    """자식이 ?2026$p(DECRQM)로 동기화 출력 지원을 물으면 pytmux 가 ?2026;2$y 로
    응답한다(실제 터미널과 동일). 무관 출력엔 무응답. read 경계 분할도 carry 로 감지.
    NEST(XTVERSION) 응답과 같은 진입점(_on_pane_data)·같은 계약."""
    from pytmuxlib.serverpty import SYNC_QUERY, SYNC_REPLY

    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        real = p.pty
        writes = []

        class _Spy:
            def write(self, b):
                writes.append(b)

            def pause_reader(self):
                pass

            def resume_reader(self):
                pass

        try:
            p.pty = _Spy()
            # ① 한 청크 안의 질의 → 응답
            srv._on_pane_data(p, b"hi \x1b[?2026$p there\r\n")
            assert SYNC_REPLY in writes, writes
            # ② 무관 출력 → 무응답
            writes.clear()
            srv._on_pane_data(p, b"plain output\r\n")
            assert writes == [], writes
            # ③ 경계 분할: 질의가 두 read 에 걸쳐도 carry 로 감지
            writes.clear()
            half = len(SYNC_QUERY) // 2
            srv._on_pane_data(p, b"abc" + SYNC_QUERY[:half])
            srv._on_pane_data(p, SYNC_QUERY[half:] + b" tail")
            assert SYNC_REPLY in writes, writes
        finally:
            p.pty = real
    finally:
        await teardown(srv, task, sock)
