"""Pane 모델 테스트: 스크롤백 / 대체 화면 / 와이드 문자 / 리사이즈 / respawn."""
import asyncio
import os

import harness
from harness import pane_text, server_only, teardown
from pytmuxlib import proc


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


async def test_coalesce_alt_repaints_lossless_and_guards():
    # §10 대응 ②: alt-screen 다중 프레임 버스트를 coalesce 한 뒤 feed 한 최종 화면이
    # 전체 feed 와 동일해야(무손실) 하고, 안전 조건이 안 맞으면 그대로 둬야 한다.
    from pytmuxlib.model import Pane, coalesce_alt_repaints

    def frame(tag):
        # 자기 완결형 풀스크린 리페인트: 홈+클리어 → 위치+색 본문 → 리셋.
        return (b"\x1b[H\x1b[2J\x1b[2;5H\x1b[1;32m" +
                f"FRAME-{tag}".encode() + b"\x1b[0m")

    # 3 프레임이 한 버스트로 쌓임(앞 2개는 마지막 2J 로 무효화).
    body = frame("A") + frame("B") + frame("C")
    full = Pane(-1, -1, 80, 24); full.feed(b"\x1b[?1049h"); full.feed(body)
    coal = Pane(-1, -1, 80, 24); coal.feed(b"\x1b[?1049h")
    out = coalesce_alt_repaints(body, coal.alt_active)
    coal.feed(out)
    assert len(out) < len(body), "중간 프레임이 드롭돼 더 짧아야"
    assert full._export_screen() == coal._export_screen(), \
        "coalesce 결과가 전체 feed 와 동일(무손실)해야"
    assert "FRAME-C" in pane_text(coal) and "FRAME-A" not in pane_text(coal)

    # 가드: main-screen 은 절대 드롭 안 함(스크롤백 손실 방지).
    assert coalesce_alt_repaints(body, False) == body
    # 가드: 버퍼가 alt 전환을 포함하면 bail(경계 가로지름).
    crossing = frame("A") + b"\x1b[?1049l" + frame("B")
    assert coalesce_alt_repaints(crossing, True) == crossing
    # 가드: 풀클리어가 1개뿐이면 no-op(밀린 프레임 없음).
    one = b"\x1b[2J\x1b[HX"
    assert coalesce_alt_repaints(one, True) == one


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


async def test_bce_erase_drops_underline():
    # 밑줄(또는 굵게 등)을 켠 채 줄·화면을 지워도 빈 칸에 장식이 남지 않아야 한다
    # (실 터미널의 BCE = 배경색만 보존). 회귀: Claude Code 환영 화면의 가로줄.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(20, 4)
        p = sess.active_window.active_pane
        p.feed(b"\x1b[2J\x1b[H")
        # 밑줄 ON, 'Hi' 쓰고 reset 없이 줄 끝까지 지우고(EL) 화면 전체 지움(ED)
        p.feed(b"\x1b[4mHi\x1b[K\r\n\x1b[2J")
        rows, _ = p.render(False)
        blanks = [st for r in rows for text, st in r
                  if st.get("un") and text.strip() == ""]
        assert not blanks, f"빈 칸에 밑줄이 남음: {blanks}"
        # 실제 글자의 밑줄은 보존되어야 함(정상 동작)
        sess2 = srv.ensure_default_session(20, 4)
        p2 = sess2.active_window.active_pane
        p2.feed(b"\x1b[2J\x1b[H\x1b[4mHi\x1b[0m")
        rows2, _ = p2.render(False)
        assert any(st.get("un") and "H" in text for r in rows2 for text, st in r)
    finally:
        await teardown(srv, task, sock)


async def test_colon_sgr_underline_normalized():
    # 콜론식 SGR(현대 터미널): pyte 0.8.2 는 콜론을 미지 문자로 보고 시퀀스를
    # 중단해 밑줄이 꺼지지 않고 번지거나 "0m" 잔해가 찍힌다. feed 단계에서
    # 세미콜론 형태로 정규화해 막는다. 회귀: 로컬 Claude Code 전체 밑줄 버그.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(20, 2)
        p = sess.active_window.active_pane
        # 밑줄 ON → 콜론식 리셋(4:0) → 이후 글자. 밑줄이 번지면 안 되고 잔해도 없어야.
        p.feed(b"\x1b[2J\x1b[H\x1b[4mAB\x1b[4:0mCD")
        rows, _ = p.render(False)
        line0 = "".join(seg[0] for seg in rows[0])
        assert "ABCD" in line0 and "0m" not in line0, repr(line0)
        # AB 만 밑줄, CD 는 밑줄 없음
        assert any(st.get("un") and "AB" in t for t, st in rows[0]), "AB 밑줄 유지"
        assert not any(st.get("un") and "CD" in t for t, st in rows[0]), "CD 밑줄 번짐"

        # 콜론식 24bit 전경색(38:2::r:g:b)도 정상 적용되고 잔해가 없어야 함
        sess2 = srv.ensure_default_session(20, 2)
        p2 = sess2.active_window.active_pane
        p2.feed(b"\x1b[2J\x1b[H\x1b[38:2::255:0:0mXY")
        rows2, _ = p2.render(False)
        l2 = "".join(seg[0] for seg in rows2[0])
        assert "XY" in l2 and ":" not in l2 and "2;" not in l2, repr(l2)
        assert any(st.get("f") and "XY" in t for t, st in rows2[0]), "24bit 색 적용"

        # 콜론식 SGR 이 feed 경계로 쪼개져도 정규화되어야 함
        sess3 = srv.ensure_default_session(20, 2)
        p3 = sess3.active_window.active_pane
        p3.feed(b"\x1b[2J\x1b[H\x1b[4mAB\x1b[4")  # 미완성 CSI → 캐리
        p3.feed(b":0mCD")                          # 다음 feed 에서 완성
        rows3, _ = p3.render(False)
        assert not any(st.get("un") and "CD" in t for t, st in rows3[0]), "경계 분할 밑줄 번짐"
    finally:
        await teardown(srv, task, sock)


async def test_xtmodkeys_not_parsed_as_underline():
    # XTMODKEYS(`CSI > 4 ; Ps m`, modifyOtherKeys): capable 터미널을 감지한
    # Claude Code 가 내보낸다. pyte 0.8.2 는 `>` private 마커를 무시하고 이를
    # `CSI 4 ; Ps m`(=SGR 밑줄 ON)으로 잘못 읽어 이후 모든 셀에 밑줄이 번진다.
    # feed 단계에서 `CSI [<>=]..m` 을 제거해 막는다. 회귀: 로컬 Claude Code 전체 밑줄.
    # ensure_default_session 은 같은 패널을 돌려줘 커서 SGR 상태가 서브케이스 간
    # 새어나간다. 각 케이스 앞에 `CSI 0 m` 으로 SGR 을 초기화해 격리한다.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(20, 2)
        p = sess.active_window.active_pane
        p.feed(b"\x1b[2J\x1b[H\x1b[0m\x1b[>4;2mAB")  # XTMODKEYS 켜기 → 글자
        rows, _ = p.render(False)
        line0 = "".join(seg[0] for seg in rows[0])
        assert "AB" in line0 and "4" not in line0.replace("AB", ""), repr(line0)
        assert not any(st.get("un") for t, st in rows[0]), "XTMODKEYS 밑줄 오인"

        # 실제 밑줄 SGR(`CSI 4 m`)은 그대로 유지되어야 함
        p.feed(b"\x1b[2J\x1b[H\x1b[0m\x1b[4mAB")
        rows2, _ = p.render(False)
        assert any(st.get("un") and "AB" in t for t, st in rows2[0]), "정상 밑줄 유실"

        # private SGR 이 feed 경계로 쪼개져도 제거되어야 함
        p.feed(b"\x1b[2J\x1b[H\x1b[0m\x1b[>4")  # 미완성 CSI → 캐리
        p.feed(b";2mAB")                         # 다음 feed 에서 완성
        rows3, _ = p.render(False)
        assert not any(st.get("un") for t, st in rows3[0]), "경계 분할 XTMODKEYS 밑줄 오인"
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
        # 옛 프로세스 종료 확인. os.kill(pid,0) 은 Windows 에서 존재확인이 아니라
        # TerminateProcess 라 의미가 달라 쓸 수 없다 → 크로스플랫폼 proc.is_alive.
        await asyncio.sleep(0.3)  # kill/reap 반영 대기(특히 Windows taskkill 비동기)
        assert not proc.is_alive(old_pid), "respawn 후 옛 셸 종료"
    finally:
        await teardown(srv, task, sock)
