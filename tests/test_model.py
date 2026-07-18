"""Pane 모델 테스트: 스크롤백 / 대체 화면 / 와이드 문자 / 리사이즈 / respawn."""
import asyncio
import os

import harness
from harness import pane_text, server_only, teardown
from pytmuxlib import proc


def _full_render(p, with_cursor=True):
    """같은 버퍼 상태에서 콜드 캐시 전체 경로 render 를 강제(빠른 경로 정합 비교용).
    버퍼를 안 건드리므로 sparse-buffer materialize 인공물 없이 fast 와 동일 상태."""
    p._row_cache = None
    p._row_cache_key = None
    return p.render(with_cursor)


async def test_native_parser_respawn_recreates_tokenizer():
    """respawn(reinit) 후에도 native 토크나이저가 새 _main 을 가리키며 정상 동작."""
    from pytmuxlib.model import Pane

    p = Pane(-1, -1, 30, 6, vt_parser="native")
    p.feed(b"old content\r\n")
    tok_before = p._tok
    p.reinit(-1, -1, 30, 6)
    assert p._tok is not None and p._tok is not tok_before
    assert p._tok.screen is p._main
    p.feed(b"\x1b[2J\x1b[Hfresh\x1b[4:0m text")
    rows, _ = _full_render(p)
    line0 = "".join(t for t, _ in rows[0])
    assert "fresh text" in line0, repr(line0)


async def test_native_parser_survives_import_state():
    """작업 보존 재시작 복원 모사: Pane 에 export_state → import_state 라운드트립 후에도
    native 토크나이저가 유지되고(새 화면을 가리키며) 복원 콘텐츠 위에 이어지는 출력이
    정상 렌더한다. (서버 복원 경로 _adopt 가 Pane 을 만들고 import_state 하는 흐름의
    핵심 불변식 — import_state 가 _tok 를 건드려 끊으면 안 된다.)"""
    from pytmuxlib.model import Pane

    src = Pane(-1, -1, 30, 6)
    src.feed(b"\x1b[2J\x1b[Hrestored\x1b[1mB\x1b[0m line")
    snap = src.export_state()
    cont = b"\r\n\x1b[4:3mcurly\x1b[0m more"   # 복원 후 이어지는 출력(콜론 SGR 포함)

    dst = Pane(-1, -1, 30, 6)
    assert dst._tok is not None
    dst.import_state(snap)
    # import_state 는 _tok 를 재생성하지 않고 유지하며, 활성 화면을 가리켜야 한다.
    assert dst._tok is not None and dst._tok.screen is dst.screen
    dst.feed(cont)

    text = "\n".join("".join(t for t, _ in row) for row in _full_render(dst)[0])
    assert "restored" in text and "curly" in text and "more" in text, repr(text)


async def test_render_dirty_path_matches_full():
    """#8 행 단위 재직렬화: 캐시 워밍 후 출력을 먹이고 **빠른 경로**(screen.dirty 행만)
    render 한 결과가 **같은 버퍼**의 전체 경로 render 와 바이트 동일해야 한다. pyte
    dirty 표기 누락이 있으면(캐시 잔상) 어긋난다. 그리기/소거/스크롤/줄삽입·삭제/문자
    삽입·삭제/SGR/와이드문자/연속 프레임을 못박는다."""
    from pytmuxlib.model import Pane

    prefix = (b"\x1b[2J\x1b[H"
              + b"".join(f"row{i:02d} content here\r\n".encode()
                        for i in range(20)))
    suffixes = [
        b"\x1b[5;3Hxx",                      # 한 줄 일부 덮어쓰기
        b"\x1b[3;1H\x1b[K",                  # 줄 끝까지 소거
        b"\x1b[2J",                          # 화면 소거
        b"more\r\nlines\r\npushed\r\nup\r\n" * 3,   # 스크롤(index)
        b"\x1b[10;1H\x1b[3L",                # 줄 삽입(CSI L)
        b"\x1b[10;1H\x1b[2M",                # 줄 삭제(CSI M)
        b"\x1b[6;3H\x1b[4@",                 # 문자 삽입(CSI @)
        b"\x1b[6;3H\x1b[4P",                 # 문자 삭제(CSI P)
        b"\x1b[1mBOLD\x1b[0m \x1b[31mRED\x1b[0m\r\n",   # SGR 변화
        "\x1b[8;1H가나다ABC\r\n".encode(),    # 와이드문자(CJK)
        b"\x1b[1;1H\x1b[7mreverse\x1b[0m",   # reverse 속성
    ]
    for suf in suffixes:
        p = Pane(-1, -1, 40, 12)
        p.feed(prefix)
        p.render(True)                        # 캐시 워밍(라이브 뷰)
        assert p._row_cache is not None
        p.feed(suf)
        rows_fast, cur_fast = p.render(True)   # 빠른 경로(dirty 행만)
        rows_full, cur_full = _full_render(p)  # 같은 버퍼, 전체 경로
        assert rows_fast == rows_full, f"suffix={suf!r}"
        assert cur_fast == cur_full
        # 연속 두 번째 프레임도(누적 dirty 처리) 일치
        p.feed(b"\x1b[2;1HTAIL\r\n")
        r2_fast = p.render(True)[0]
        r2_full = _full_render(p)[0]
        assert r2_fast == r2_full, f"2nd frame suffix={suf!r}"


async def test_render_cache_invalidation_paths():
    """캐시 무효화: alt 전환·스크롤·리사이즈 후 render 가 전체 경로로 폴백해 정확한
    화면을 낸다(같은 상태의 전체 경로와 동일). 빠른 경로가 잘못된 캐시를 재사용하지
    않는지 확인."""
    from pytmuxlib.model import Pane

    base = [b"\x1b[2J\x1b[H"] + [f"L{i} line\r\n".encode() for i in range(6)]
    # alt 전환: 캐시 워밍 후 alt 진입 → 폴백(alt 내용)
    a = Pane(-1, -1, 30, 8)
    for s in base:
        a.feed(s)
    a.render(True)
    a.feed(b"\x1b[?1049h\x1b[2J\x1b[HALT SCREEN")
    assert a.render(True)[0] == _full_render(a)[0]
    a.feed(b"\x1b[?1049l")                    # 메인 복귀도 정합
    assert a.render(True)[0] == _full_render(a)[0]
    # 스크롤: 캐시 워밍 후 위로 스크롤 → 폴백(스크롤백 뷰)
    c = Pane(-1, -1, 30, 8)
    for s in [b"\x1b[2J\x1b[H"] + [f"line{i}\r\n".encode() for i in range(30)]:
        c.feed(s)
    c.render(True)
    c.scroll_by(5)
    assert c.render(True)[0] == _full_render(c)[0]
    c.scroll_to("bottom")                     # 라이브 복귀
    assert c.render(True)[0] == _full_render(c)[0]
    # 리사이즈(크기 변경)는 _row_cache_key=(cols,lines,…) 불일치로 자명히 무효화
    # → 전체 경로. 실 fd 없는 렌더전용 패널은 resize(set_winsize) 불가라 실 fd 가
    # 있는 test_server 의 리사이즈 회귀(레이아웃/화면)가 이 경로를 함께 커버한다.


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


async def test_su_sd_scroll_region():
    """SU(`CSI Ps S`)/SD(`CSI Ps T`)가 스크롤 영역(DECSTBM) 안에서 줄을 스크롤한다.

    회귀: 표준 CSI 디스패치 테이블엔 SU/SD 가 없어 조용히 드롭됐고, Claude Code 가
    콘텐츠 영역을 SU 로 스크롤하면 무시돼 다음 줄들이 안 밀린 옛 줄에 겹쳐 격자가
    발산했다(사용자 보고: pytmux 안 Claude 화면 글자 겹침; tmux 는 SU/SD 구현해 정상).
    nativescreen.scroll_up/down + vtparse 의 S/T 매핑으로 해결."""
    from pytmuxlib.model import Pane

    p = Pane(-1, -1, 10, 6, vt_parser="native")
    p.feed(b"\x1b[2;5r")                      # 스크롤 영역 = 2~5행(1-based)
    for i, ch in enumerate((b"A", b"B", b"C", b"D")):
        p.feed(b"\x1b[%d;1H" % (i + 2) + ch)  # row2=A row3=B row4=C row5=D
    rows, _ = _full_render(p)
    t = lambda y: "".join(s for s, _ in rows[y]).rstrip()
    assert (t(1), t(2), t(3), t(4)) == ("A", "B", "C", "D"), "setup"

    p.feed(b"\x1b[S")                         # SU 1: 영역 위로
    rows, _ = _full_render(p)
    t = lambda y: "".join(s for s, _ in rows[y]).rstrip()
    assert (t(1), t(2), t(3), t(4)) == ("B", "C", "D", ""), "SU"
    assert t(0) == "" and t(5) == "", "영역 밖 불변"

    p.feed(b"\x1b[T")                         # SD 1: 영역 아래로
    rows, _ = _full_render(p)
    t = lambda y: "".join(s for s, _ in rows[y]).rstrip()
    assert (t(1), t(2), t(3), t(4)) == ("", "B", "C", "D"), "SD"

    p.feed(b"\x1b[2S")                        # SU 2: 두 줄
    rows, _ = _full_render(p)
    t = lambda y: "".join(s for s, _ in rows[y]).rstrip()
    assert (t(1), t(2), t(3), t(4)) == ("C", "D", "", ""), "SU2"


async def test_resized_pane_restores_tabstops():
    """분할 새 패널(spawn MIN_W → 실제 폭 resize)에서 탭 정렬 출력이 줄바꿈에서
    쪼개지지 않아야 한다(사용자 보고: 좁은 우측 패널의 ls 이름 첫 글자가 이전 줄
    끝에 나옴). 폭 3 에서 탭스톱을 빈 집합으로 두고 resize 가 재계산하지 않으면
    TAB 이 줄 끝으로 튀던 회귀를 못박는다(native 화면이 resize 에서 재계산)."""
    from pytmuxlib.nativescreen import NativeScrollbackScreen
    from pytmuxlib.vtparse import VTTokenizer
    from pytmuxlib.protocol import MIN_W

    scr = NativeScrollbackScreen(MIN_W, 6)   # spawn 시 임시 MIN_W(=3) → 탭스톱 빈 집합
    scr.resize(6, 26)                     # 실제 폭으로 재배치(분할 경로): (lines, columns)
    assert scr.tabstops == set(range(8, 26, 8)), scr.tabstops
    # ls 가 폭 26 에서 내는 실제 바이트(단일 탭 컬럼 구분).
    VTTokenizer(scr).feed(b"f01\tf21\tgg01\r\n")
    first = "".join((scr.buffer[0][x].data if x in scr.buffer[0] else " ")
                    for x in range(26))
    # 탭이 8-칸 정지점으로 펼쳐져 한 줄에 들어간다(쪼개짐 없음).
    assert first.startswith("f01     f21     gg01"), repr(first)
    assert "gg01" in first, repr(first)   # 마지막 컬럼이 다음 줄로 새지 않음
    assert not getattr(scr.buffer[0], "wrapped", False), "줄이 wrap 되지 않음"


async def test_conpty_prewrap_heuristic_wrap_tags():
    """ConPTY(Windows)는 conhost 가 줄을 미리 접어 하드 개행으로 재방출하므로
    DECAWM 오토랩 태그가 영원히 비어(실캡처: 꽉 찬 줄 36, 태그 0) 멀티라인 명령
    복사가 줄마다 개행됐다(사용자 07-15). _prewrap_heuristic 패널은 render 가
    '마지막 칸까지 꽉 찬 줄 + 접힌 경계 양쪽이 공백/박스문자 아님'을 연속원으로
    보강 태깅한다. 구분선(─)·테두리(│…│) 꽉 찬 줄은 제외, Unix 기본(off)은 불변."""
    from pytmuxlib.model import Pane

    W = 40
    cmd = "python3 tests/run.py test_client_and_more_stuff --flag=value"
    box = "│" + " boxed full-width line".ljust(W - 2) + "│"
    feed = (cmd[:W] + "\r\n" + cmd[W:] + "\r\n"   # row0 꽉 참+연속 → 태깅 기대
            + "─" * W + "\r\n"                     # row2 전폭 구분선 → 제외
            + "next text line\r\n"                 # row3
            + box + "\r\n"                         # row4 │…│ 테두리 → 제외
            + "short\r\n").encode()                # row5

    p = Pane(1, -1, W, 12)
    p._prewrap_heuristic = True                    # ConPTY 시뮬(플랫폼 무관 검증)
    p.feed(feed)
    p.render(True)
    assert p._last_wrap == [0], p._last_wrap

    # Unix 기본(os.name != "nt" → off): 하드 개행뿐이라 태그 없음(종전 동작 불변).
    p2 = Pane(2, -1, W, 12)
    p2._prewrap_heuristic = False
    p2.feed(feed)
    p2.render(True)
    assert p2._last_wrap == [], p2._last_wrap

    # 진짜 오토랩(정확 신호)은 휴리스틱과 무관하게 계속 잡힌다 — 합집합.
    p3 = Pane(3, -1, W, 12)
    p3._prewrap_heuristic = True
    p3.feed(("x" * (W + 10) + "\r\n").encode())    # 폭 초과 → DECAWM 오토랩
    p3.render(True)
    assert 0 in p3._last_wrap, p3._last_wrap


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


async def test_shrink_wrap_guard_truncates_not_cascades():
    """폭 축소 직후 전환 윈도우: ConPTY/Claude 가 아직 옛(넓은) 폭으로 그리는 바이트가
    좁아진 pyte 에 들어와도, 전폭 줄(예: /compact 의 ─ 구분선)이 다음 줄로
    autowrap(cascade)되지 않고 마지막 칸에서 truncate 돼야 한다. cascade 되면 Claude 의
    커서-상대 리페인트 좌표까지 어긋나 아래 줄이 전부 밀린다(/compact 진행 표시 정렬
    깨짐 회귀). draw 중 DECAWM 임시 비활성화로 흡수한다."""
    from pytmuxlib.model import Pane
    from pytmuxlib.replay import render_pane_lines

    # 140칸 ─ 구분선 + CR + CUD(다음 줄) + 마커 — 실제 /compact 진행 표시 패턴.
    payload = ("─" * 140 + "\r\x1b[1B" + "NEXT").encode("utf-8")

    # 140 에서 시작 → 139 로 축소(가드 무장) → Claude 가 아직 140 폭으로 그린다고 가정.
    p = Pane(-1, -1, 140, 10)
    p.feed(b"\x1b[2J\x1b[H")
    p.resize(139, 10)                 # shrink → autowrap 가드 무장
    p.feed(payload)
    lines = render_pane_lines(p)
    assert lines[0].rstrip().count("─") == 139, repr(lines[0][:20])
    assert lines[1].lstrip().startswith("NEXT"), repr(lines[1])
    assert not lines[1].lstrip().startswith("─"), "전폭 줄이 다음 줄로 흘러선 안 됨(cascade)"

    # 대조군: 폭 축소(가드)가 없으면(처음부터 139) 같은 오버플로 바이트는 wrap 된다
    # — 가드가 실제로 동작을 바꿨음을 증명(버그 재현).
    q = Pane(-1, -1, 139, 10)
    q.feed(b"\x1b[2J\x1b[H")
    q.feed(payload)
    qlines = render_pane_lines(q)
    assert qlines[1].lstrip().startswith("─"), "대조군: 가드 없으면 한 칸 wrap 돼야"


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


async def test_claude_terminal_protocol_sequences_no_leak():
    # 회귀 가드: capable 터미널로 감지된 Claude Code 가 내보내는 터미널 프로토콜
    # private CSI 시퀀스들이 pytmux 화면에 글자를 흘리지 않아야 한다. pyte 0.8.2 는
    # 일부 `u`/`m` 종결 private CSI 를 처리 못 해 끝 글자를 흘린다(실측: pop `\x1b[<u`
    # → 'u' 누수, push `\x1b[>1u` 는 안 샘 / XTMODKEYS `\x1b[>4;2m` → 'm' 계열).
    # feed 전처리(_PRIVATE_SGR_RE/_KITTY_KBD_RE)가 이를 막는다. 시퀀스를 MARK..ER
    # 사이에 끼워 넣어, 화면에 시퀀스 잔해 없이 "MARKER" 만 남는지 확인한다.
    seqs = [
        b"\x1b[>1u",      # kitty 키보드 프로토콜 push
        b"\x1b[<u",       # kitty 키보드 프로토콜 pop (실측 누수원 — 'u')
        b"\x1b[>0q",      # XTVERSION 질의
        b"\x1b[>4;2m",    # XTMODKEYS(modifyOtherKeys) — 'm' 누수/밑줄 오인 방지
        b"\x1b[?2026h",   # synchronized output begin
        b"\x1b[?9001h",   # win32-input-mode (Windows Terminal)
        b"\x1b[?1004h",   # focus reporting
        b"\x1b[?2031h",   # color-scheme change notification
    ]
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(20, 2)
        p = sess.active_window.active_pane
        for seq in seqs:
            p.feed(b"\x1b[2J\x1b[H\x1b[0mMARK" + seq + b"ER")
            line0 = "".join(seg[0] for seg in p.render(False)[0][0]).rstrip()
            assert line0 == "MARKER", f"{seq!r} leaked: {line0!r}"
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


async def test_feed_plain_text_and_partial_csi():
    """native feed 의 관측 속성: ①플레인 텍스트가 정확히 그려지고 _feed_seq/dirty 갱신,
    ②alt 모드에서도 현재(=alt) 화면으로 라우팅, ③feed 경계로 잘린 CSI 를 토크나이저가
    증분 상태로 흡수해 다음 feed 에서 완성한다(구 pyte 경로의 _altcarry 우회를 native
    토크나이저가 대체 — M4b)."""
    from pytmuxlib.model import Pane
    # 1) 플레인 텍스트가 정확히 그려지고 _feed_seq/dirty 가 갱신된다.
    p = Pane(-1, -1, 40, 6)
    seq0 = p._feed_seq
    p.feed(b"hello world\r\nsecond line\r\n")
    assert p._feed_seq == seq0 + 1 and p.dirty
    txt = pane_text(p)
    assert "hello world" in txt and "second line" in txt
    # 2) alt 모드에서도 텍스트는 현재(=alt) 화면으로 라우팅된다.
    p2 = Pane(-1, -1, 30, 5)
    p2.feed(b"\x1b[?1049h")             # alt 진입
    assert p2.alt_active
    p2.feed(b"ALTPLAIN")               # 텍스트 → alt 화면에
    assert "ALTPLAIN" in pane_text(p2)
    p2.feed(b"\x1b[?1049l")            # 메인 복귀
    assert not p2.alt_active and "ALTPLAIN" not in pane_text(p2)
    # 3) feed 경계로 잘린 CSI 를 증분 토크나이저가 이어 파싱해 완성한다.
    p3 = Pane(-1, -1, 20, 3)
    p3.feed(b"X\x1b[1")                # 끝에 잘린 CSI
    p3.feed(b";31mRED")               # 이어붙여 완성 → RED 정상 렌더
    assert "RED" in pane_text(p3)


def test_sync_output_tracking():
    """동기화 출력(DEC 2026, BSU/ESU) 상태 추적: ?2026h 켜짐 / ?2026l 꺼짐, 한
    슬라이스에 둘 다 오면 마지막 토글이 최종 상태(rfind), 무관한 데이터엔 무변동.
    _flush_loop 이 프레임 도중 송신을 미루는 근거(무작위 글자 겹침 근본 해결)."""
    from pytmuxlib.model import Pane
    p = Pane(-1, -1, 40, 6, vt_parser="native")
    assert p.sync_output is False
    # 프레임 시작
    assert p.update_sync_output(b"\x1b[?2026h") is True
    assert p.sync_output is True
    assert p._sync_since > 0.0
    # 같은 상태 재진입은 '변동 없음'
    assert p.update_sync_output(b"\x1b[?2026h some text") is False
    # 프레임 끝
    assert p.update_sync_output(b"more\x1b[?2026l") is True
    assert p.sync_output is False
    # 2026 이 전혀 없는 데이터는 무영향
    assert p.update_sync_output(b"plain output\r\n") is False
    assert p.sync_output is False
    # 한 슬라이스에 l 다음 h(프레임 N 끝 + N+1 시작) → 최종 켜짐
    assert p.update_sync_output(b"\x1b[?2026lTAIL\x1b[?2026hHEAD") is True
    assert p.sync_output is True
    # 한 슬라이스에 h 다음 l(완결 프레임) → 최종 꺼짐
    assert p.update_sync_output(b"\x1b[?2026hBODY\x1b[?2026l") is True
    assert p.sync_output is False


async def test_bg_gap_fill_between_same_bg_runs():
    """배경 갭 메꿈(_fill_flanked_gaps): 같은 명시 배경 런 사이의 짧은 기본-배경
    공백 런은 그 배경으로 채워 내보낸다 — Claude Code 가 트랜스크립트 블록을
    48;2;… 로 칠하면서 탭 전개 공백만 SGR 49 로 남겨, 회색 블록에 검은(터미널
    기본 배경) 구멍이 뚫려 보이던 표시 문제 수정(실박스 캡처 2026-07-10)."""
    from pytmuxlib.model import Pane
    p = Pane(-1, -1, 60, 4)
    # 캡처 실측 패턴: 명시 bg 텍스트 → 49m 공백 5칸 → 같은 bg 텍스트
    p.feed(b"\x1b[48;2;70;70;70mAAA\x1b[49m     \x1b[48;2;70;70;70mBBB\x1b[0m")
    rows, _ = p.render(with_cursor=False)
    segs = rows[0]
    joined = "".join(t for t, _ in segs)
    assert joined.startswith("AAA     BBB")
    gap = next(s for t, s in segs if t.strip(" ") == "" and "AAA" not in t and len(t) == 5)
    assert gap.get("b") == "#464646", f"갭이 이웃 배경으로 채워져야 함: {segs[:4]}"


async def test_bg_gap_fill_conservative_conditions():
    """갭 메꿈 보수 조건: ① 좌우 배경이 다르면 안 채움 ② 상한(16칸) 초과 런 안 채움
    ③ 줄 끝(한쪽만 명시 배경) 안 채움 — native 터미널과의 차이를 최소화."""
    from pytmuxlib.model import Pane, _GAP_FILL_MAX
    # ① 좌우 배경 불일치
    p = Pane(-1, -1, 60, 4)
    p.feed(b"\x1b[48;2;70;70;70mAAA\x1b[49m   \x1b[48;2;10;10;10mBBB\x1b[0m")
    rows, _ = p.render(with_cursor=False)
    gap = next(s for t, s in rows[0] if t == "   ")
    assert "b" not in gap
    # ② 상한 초과(17칸)
    p2 = Pane(-1, -1, 80, 4)
    p2.feed(b"\x1b[48;2;70;70;70mAAA\x1b[49m" + b" " * (_GAP_FILL_MAX + 1)
            + b"\x1b[48;2;70;70;70mBBB\x1b[0m")
    rows2, _ = p2.render(with_cursor=False)
    gap2 = next(s for t, s in rows2[0] if t.strip(" ") == "" and len(t) == _GAP_FILL_MAX + 1)
    assert "b" not in gap2
    # ③ 줄 끝 여백(오른쪽 이웃이 없는 기본-배경 나머지)은 그대로
    p3 = Pane(-1, -1, 30, 4)
    p3.feed(b"\x1b[48;2;70;70;70mAAA\x1b[0m")
    rows3, _ = p3.render(with_cursor=False)
    tail = rows3[0][-1]
    assert "b" not in tail[1] and tail[0].strip(" ") == ""
