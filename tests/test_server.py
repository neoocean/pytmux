"""서버 기능 테스트: 패널/윈도우/세션 조작, 검색·버퍼·캡처, 영속, 제어."""
import asyncio
import base64
import json
import os
import shutil

import harness
import pytmux
from harness import first_session, pane_text, server_only, teardown
from pytmuxlib import ipc


async def test_pane_tree_ops():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")
        srv.split_pane(sess, "tb")
        assert len(win.panes()) == 3
        srv.toggle_zoom(sess)
        panes, _ = win.compute_layout(0, 0, 80, 23)
        assert win.zoomed and len(panes) == 1, "줌"
        srv.toggle_zoom(sess)
        win.apply_preset("tiled")
        assert len(win.panes()) == 3, "tiled 유지"
        srv.rotate_panes(sess, True)
        srv.swap_pane(sess, True)
        assert len(win.panes()) == 3
        for p in win.panes():
            assert p.parent is None or p in (p.parent.a, p.parent.b), "트리 일관성"
    finally:
        await teardown(srv, task, sock)


async def test_zoom_resizes_hidden_panes():
    # §2.6: 줌 중에는 활성 패널만 표시되지만, 숨은 패널도 정상 분할 크기로 미리
    # 리사이즈돼야 한다 — 안 그러면 (줌 중 창 축소 + 숨은 패널 출력) 뒤 줌 해제
    # 시점에 옛 크기→새 크기 reflow 가 한꺼번에 일어나 출력이 깨진다. 줌 상태에서
    # _layout_msg 가 작은 창으로 와도 숨은 패널이 새 분할 크기로 줄어드는지 검증.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")          # 패널 2개(좌우)
        srv._layout_msg(sess, 80, 24)        # 둘 다 80폭 분할로 리사이즈
        hidden = next(p for p in win.panes() if p is not win.active_pane)
        wide = hidden.cols
        assert wide > 25, f"넓은 분할 기대치 미달: {wide}"
        # 줌 후 더 작은 창으로 레이아웃 — 활성만 표시되지만 숨은 패널도 줄어야 함
        srv.toggle_zoom(sess)
        assert win.zoomed
        srv._layout_msg(sess, 40, 24)
        assert hidden.cols < 25, (
            f"숨은 패널이 옛 크기로 정지(stale): {hidden.cols} (기대: <25)")
        # 활성 패널은 줌 전체화면(40폭, single_border 기본 ON 이라 테두리 2 차감)
        assert win.active_pane.cols > 30, win.active_pane.cols
    finally:
        await teardown(srv, task, sock)


async def test_panes_cache_invalidates_on_tree_change():
    # §4.6: Window.panes() 는 결과를 캐시하되 트리 수술 때마다 무효화해야 한다.
    # 캐시가 stale 이면 split 후 새 패널 누락, swap/rotate 후 순서 오류(이웃 계산
    # 깨짐)가 난다. 캐시 동작 + 각 수술의 무효화를 객체 동일성으로 검증한다.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        # 같은 트리면 두 번 호출이 동일 리스트 객체를 돌려준다(캐시 적중).
        a = win.panes()
        assert win.panes() is a, "트리 불변 → 캐시된 동일 객체"
        # split → 리프 추가 + 캐시 무효화(새 객체, +1)
        srv.split_pane(sess, "lr")
        b = win.panes()
        assert b is not a and len(b) == len(a) + 1, "split → 무효화 + 새 패널"
        srv.split_pane(sess, "tb")
        assert len(win.panes()) == 3
        # swap/rotate 는 리프 집합은 같아도 순서가 바뀌므로 무효화돼야 한다
        # (이 연산들이 panes() 순서로 이웃을 계산 → stale 이면 오동작).
        before = win.panes()
        srv.swap_pane(sess, True)
        assert win.panes() is not before, "swap → 무효화(순서 변동)"
        before = win.panes()
        srv.rotate_panes(sess, True)
        assert win.panes() is not before, "rotate → 무효화(순서 변동)"
        # apply_preset(root 재구성)도 무효화
        before = win.panes()
        win.apply_preset("tiled")
        assert win.panes() is not before, "preset → 무효화"
        # 패널 제거 → 캐시 무효화 + 그 패널이 목록에서 사라짐
        victim = win.panes()[0]
        srv._remove_pane_from_tree(victim)
        after = win.panes()
        assert victim not in after and len(after) == 2, "remove → 무효화 + 제거"
    finally:
        await teardown(srv, task, sock)


async def test_tree_msg_includes_panes_and_remote():
    # _tree_msg 가 윈도우별 패널 목록(id·title·cmd·remote)을 담고, fg 명령이 ssh
    # 류면 remote=True 로 판정하는지(#14/#24 데이터 인프라).
    if ipc.IS_WINDOWS:
        return  # 패널별 fg 구분이 master_fd 에 기대는데 ConPTY 는 fd 가 -1 로 동일
                # → fd 기반 ssh/zsh 분기 불가. 원격 감지 자체가 Windows 열화 항목(§4).
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        win = sess.active_window
        ids = [p.id for p in win.panes()]
        # fg 명령을 흉내: 첫 패널은 ssh(원격), 나머지는 zsh(로컬)
        first = win.panes()[0]
        srv._fg_command = lambda pane, _f=first: ("ssh" if pane is _f
                                                  else "zsh")
        msg = srv._tree_msg()
        w = msg["sessions"][0]["windows"][0]
        assert isinstance(w["panes"], list) and len(w["panes"]) == len(ids)
        p0 = next(p for p in w["panes"] if p["id"] == first.id)
        assert p0["remote"] is True and p0["cmd"] == "ssh", "ssh 패널 → 원격"
        others = [p for p in w["panes"] if p["id"] != first.id]
        assert all(p["remote"] is False for p in others), "zsh 패널 → 로컬"
        assert "active" in w
    finally:
        await teardown(srv, task, sock)


async def test_track_prompt_sets_last_prompt():
    # _track_prompt 가 제출 프롬프트를 last_prompt(헤더 표시)로 확정하고,
    # status 의 panes_claude 에 전달한다(#4).
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        srv._track_prompt(p, b"first\r")
        srv._track_prompt(p, b"second\r")
        assert p.last_prompt == "second", p.last_prompt
        msg = srv._status_msg(sess)
        pc = next(e for e in msg["panes_claude"] if e["id"] == p.id)
        assert pc["prompt"] == "second"
    finally:
        await teardown(srv, task, sock)


async def test_pane_xtversion_query_gets_pytmux_reply():
    """§1.7 in-band 중첩 감지(외부 측): 패널 출력에 XTVERSION 질의(ESC[>0q)가 보이면
    실제 터미널처럼 `DCS >| pytmux ST` 를 그 패널 stdin 으로 응답한다. read 경계에
    질의가 쪼개져도 carry 로 감지. 무관 출력엔 무응답."""
    from pytmuxlib.serverpty import NEST_QUERY, NEST_REPLY
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        real = p.pty
        writes = []

        class _Spy:
            def write(self, b):
                writes.append(b)
            # 연속 청크가 버스트로 감지되면 _on_pane_data 가 드레인 경로로 돌리며
            # pause/resume 를 부른다(PtyProcess 기본 no-op 과 동치 — 스파이도 갖춘다).
            def pause_reader(self): pass
            def resume_reader(self): pass
        try:
            p.pty = _Spy()
            # ① 한 청크 안의 질의 → 응답
            srv._on_pane_data(p, b"hello \x1b[>0q world\r\n")
            assert writes == [NEST_REPLY], writes
            # ② 무관 출력 → 무응답
            writes.clear()
            srv._on_pane_data(p, b"plain output\r\n")
            assert writes == [], writes
            # ③ 경계 분할: 질의가 두 read 에 걸쳐도 carry 로 감지
            srv._on_pane_data(p, b"abc\x1b[>")
            srv._on_pane_data(p, b"0q tail")
            assert writes == [NEST_REPLY], writes
            assert NEST_QUERY not in b"abc\x1b[>" and NEST_QUERY not in b"0q tail"
        finally:
            p.pty = real
    finally:
        await teardown(srv, task, sock)


async def test_pane_nest_dest_dcs_records_ssh_dest():
    """NESTED_ATTACH §4: 래퍼의 NEST_DEST DCS(argv 줄단위 b64)를 패널 출력에서
    스캔해 pane._ssh_dest 에 목적지를 기록한다 — read 경계 분할 보전(carry),
    비 b64 위조 무시, DCS 는 pyte 가 소비해 화면을 오염하지 않는다."""
    import base64
    from pytmuxlib import ipc, sshwrap
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        tok = sshwrap.load_or_create_token(ipc.default_state_dir())

        def _dest(argv_lines: str, token: str = tok) -> bytes:
            # provenance 머리줄(token) + argv 줄단위 b64(NEW-1).
            b64 = base64.b64encode(
                (token + "\n" + argv_lines).encode()).decode().encode()
            return sshwrap.NEST_DEST_PRE + b64 + sshwrap.DCS_ST

        dcs = _dest("ssh\n-p\n2222\nNATGAMES\\woojinkim@office1\necho\nhi")
        # ① 경계 분할: 머리 일부 → 페이로드 나머지 두 청크로 나눠도 기록된다.
        srv._on_pane_data(p, b"login banner " + dcs[:9])
        assert p._ssh_dest == "", "미완 DCS 는 아직 미기록"
        srv._on_pane_data(p, dcs[9:] + b" tail$ ")
        assert p._ssh_dest == "NATGAMES\\woojinkim@office1", "목적지=사용자 입력 그대로"
        # ② 화면 오염 없음: DCS 본문이 렌더에 안 보인다(pyte 가 소비).
        text = "\n".join(p.screen.display)
        assert "pytmux-ssh" not in text and "NATGAMES" not in text, text
        # ③ 비 b64 위조(완결 DCS 형태) → 무시(기존 기록 유지).
        srv._on_pane_data(
            p, sshwrap.NEST_DEST_PRE + b"!!not-base64!!" + sshwrap.DCS_ST)
        assert p._ssh_dest == "NATGAMES\\woojinkim@office1"
        # ④ provenance 토큰 불일치(위조 cat/스크롤백/원격출력) → 무시(NEW-1).
        srv._on_pane_data(p, _dest("ssh\nevil-host", token="deadbeef"))
        assert p._ssh_dest == "NATGAMES\\woojinkim@office1", "위조 토큰 미기록"
        # ⑤ 정상 토큰의 새 DEST 가 기존 기록을 갱신한다(최신 1건).
        srv._on_pane_data(p, _dest("ssh\noffice2"))
        assert p._ssh_dest == "office2"
    finally:
        await teardown(srv, task, sock)


async def test_prompt_multiline_is_one_submission():
    # Shift+Enter(=LF \n)로 줄바꿈해 한 번에 제출(Enter=CR \r)한 멀티라인 프롬프트는
    # 한 개의 제출 단위가 된다. 헤더용 last_prompt 는 한 줄이라 개행을 공백으로
    # 접는다. (Enter 가 \r\n 으로 와도 1개.)
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        srv._track_prompt(p, b"line one\nline two\r")     # \n=줄바꿈, \r=제출
        assert p.last_prompt == "line one line two"       # 헤더는 한 줄(공백 접기)
        srv._track_prompt(p, b"next\r\n")                 # CRLF 제출도 1개
        assert p.last_prompt == "next", p.last_prompt
    finally:
        await teardown(srv, task, sock)


async def test_inactive_tab_claude_done_flag():
    # 비활성 탭의 Claude 패널이 busy→(안정)idle 로 끝나면 has_claude_done 이 켜지고,
    # 그 탭을 보면(select_window) 해제된다(#22). idle 은 _DONE_IDLE_FRAMES 프레임
    # 안정돼야 완료로 친다(§10 #18 플리커 방지).
    import importlib
    _DONE_IDLE_FRAMES = importlib.import_module(
        "pytmuxlib.plugins.claude-code.servermixin")._DONE_IDLE_FRAMES
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)                 # 탭 1 추가(활성=탭1)
        srv.select_window(sess, 0)           # 탭0 활성 → 탭1 비활성
        t1 = sess.tabs[1]
        p1 = t1.window.active_pane
        win = sess.active_window             # 탭0(활성)
        # 처리중(busy) 을 스캔으로 확정(_was_busy 세팅)
        p1.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p1._claude == "busy"
        # idle footer 로 전환 — 안정될 때까지(_DONE_IDLE_FRAMES) 완료 안 뜸
        p1.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        for _ in range(_DONE_IDLE_FRAMES - 1):
            srv._scan_claude(sess, win)
            assert t1.has_claude_done is False, "idle 안정 전엔 완료 안 함"
        srv._scan_claude(sess, win)          # N번째 안정 idle → 완료
        assert t1.has_claude_done is True, "비활성 탭 busy→안정 idle → 완료 알림"
        msg = srv._status_msg(sess)
        assert msg["windows"][1]["claude_done"] is True
        # 그 탭으로 전환 → 읽음 처리(해제)
        srv.select_window(sess, 1)
        assert t1.has_claude_done is False, "보면 해제"
    finally:
        await teardown(srv, task, sock)


async def test_model_badge_debounce_absorbs_transient_haiku():
    """모델 배지 오탐 수정(2026-06-22): opus 세션 중 화면에 haiku 가 한두 프레임 떠도
    (Haiku 서브에이전트/Task 출력·모델명 언급·/model 메뉴 잔상) 배지가 즉시 안 바뀐다.
    첫 확정은 즉시, 그 뒤 *변경*은 같은 새 값이 _MODEL_DEBOUNCE 회 연속 관측될 때만."""
    import importlib
    sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    N = sm._MODEL_DEBOUNCE
    assert N >= 2, "디바운스가 1이면 흡수가 안 됨"
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 첫 모델 확정은 즉시(초기 표시 지연 방지) — opus 배지가 뜬 busy Claude 패널
        # (busy 스피너는 글리프+동명사+'…'(U+2026) 앵커가 필요 — claude._BUSY_SPINNER_RE)
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s)\nOpus 4.8 · /model\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude == "busy" and p._claude_model == "opus-4.8", \
            (p._claude, p._claude_model)
        # 본문 언급 haiku(배지 서명 '(… context)'/'· /model' 없음)는 후보조차 안 된다 —
        # 대화/온보딩 텍스트의 모델명이 상태줄로 새던 버그(2026-07-04) 차단. 배지 부재라
        # 프로브 폴백만 작동, 프로브 미상이면 마지막 값(opus) 유지 + 후보 0.
        p.feed("\x1b[2J\x1b[H✽ Crunching…\n"
               "I used claude-haiku-4-5 earlier\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude_model == "opus-4.8" and p._claude_model_cand_n == 0, \
            "서명 없는 본문 haiku 언급은 무시(후보 아님)"
        # haiku **배지**(서명 있음)가 한 프레임만 등장 → 디바운스로 흡수, 유지.
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s)\nHaiku 4.5 · /model\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude_model == "opus-4.8", "1프레임 haiku 배지는 흡수돼야"
        # opus 배지로 복귀 → haiku 후보 카운트 리셋
        p.feed("\x1b[2J\x1b[H✽ Crunching…\nOpus 4.8 · /model\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude_model == "opus-4.8" and p._claude_model_cand_n == 0
        # haiku **배지**가 N 회 연속 관측(실제 /model 전환) → 그제서야 확정
        for _ in range(N):
            p.feed("\x1b[2J\x1b[H✽ Crunching…\nHaiku 4.5 · /model\r\n".encode("utf-8"))
            srv._scan_claude(sess, win)
        assert p._claude_model == "haiku-4.5", f"{N}회 연속 관측 → 모델 전환 확정"
    finally:
        await teardown(srv, task, sock)


async def test_pin_tab_moves_right_and_unpin_returns():
    """항목7: 탭 고정 → tabs 맨 뒤(고정 구역)로 이동·index 재부여·신원 유지. 언핀 →
    비고정 구역으로 복귀. 불변식 '비고정 전부 < 고정 전부' 유지."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        for _ in range(3):
            srv.new_window(sess)              # 탭 0..3 (4개)
        t1 = sess.tabs[1]
        srv.set_pinned(sess, 1, True)
        assert sess.tabs[-1] is t1, "고정 탭은 맨 뒤로"
        assert t1.pinned and t1.index == len(sess.tabs) - 1
        assert [i for i, t in enumerate(sess.tabs) if t.pinned] == \
            [len(sess.tabs) - 1], "고정은 끝에 모임"
        # 언핀 → 비고정 구역으로(전부 비고정)
        srv.set_pinned(sess, sess.tabs.index(t1), False)
        assert not t1.pinned and all(not t.pinned for t in sess.tabs)
    finally:
        await teardown(srv, task, sock)


async def test_tree_msg_carries_pinned():
    """§12 ⑤: 트리(개요) 메시지의 window 항목에 pinned 가 실려 트리 뷰가 고정 탭을
    표식할 수 있다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)                  # 탭 0,1
        srv.set_pinned(sess, 1, True)         # 탭1 고정
        msg = srv._tree_msg()
        wins = msg["sessions"][0]["windows"]
        pins = [w["pinned"] for w in wins]
        assert pins.count(True) == 1 and pins[-1] is True, pins
    finally:
        await teardown(srv, task, sock)


async def test_new_window_inserts_before_pinned():
    """항목7: 고정 탭이 있으면 새 탭은 첫 고정 탭 앞(비고정 구역 끝)에 삽입돼 고정
    구역을 침범하지 않는다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)                  # 탭 0,1
        pinned = sess.tabs[1]
        srv.set_pinned(sess, 1, True)         # 탭1 고정(맨 뒤)
        assert sess.tabs[-1] is pinned
        srv.new_window(sess)                  # 새 탭 → 고정 앞
        assert sess.tabs[-1] is pinned, "고정 탭은 계속 맨 뒤"
        assert not sess.tabs[-2].pinned, "새 탭은 비고정 구역 끝(고정 앞)"
    finally:
        await teardown(srv, task, sock)


async def test_move_tab_clamped_within_zone():
    """항목7: 비고정 탭을 고정 구역 좌표로 move_tab 해도 경계까지만(불변식 보존)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        for _ in range(3):
            srv.new_window(sess)              # 탭 0..3
        srv.set_pinned(sess, 3, True)         # 탭3 고정 → 비고정 [0,2], 고정 [3]
        moved = sess.tabs[0]
        srv.move_tab(sess, 0, 3)              # 고정 구역(3)으로 이동 시도
        # 비고정 구역 끝(index 2)까지만 클램프 — 고정 탭은 여전히 맨 뒤.
        assert sess.tabs[-1].pinned and sess.tabs.index(moved) <= 2
        assert all(not t.pinned for t in sess.tabs[:-1]), "불변식 유지"
    finally:
        await teardown(srv, task, sock)


async def test_pin_in_resume_payload():
    """항목7: 재시작 resume 페이로드 tabs 직렬화에 pinned 가 실린다(복원 후 좌/우 보존)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)
        srv.set_pinned(sess, 1, True)
        payload = srv._resume_payload()
        tabs = payload["sessions"][0]["tabs"]
        assert any(t.get("pinned") for t in tabs), "고정 비트 직렬화됨"
        # 정규화로 고정 탭이 마지막에 직렬화.
        assert tabs[-1]["pinned"] is True
    finally:
        await teardown(srv, task, sock)


async def test_warn_history_record_read_cap_and_desc():
    """항목2(2026-06-22): 경고 이력 JSONL 저장/조회 — 시간 내림차순(최신 먼저)으로
    읽고, 상한(_WARN_HIST_CAP)을 넘으면 최근 것만 남긴다."""
    import os
    import tempfile
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "wh.jsonl")
            srv._warnhist_path = lambda: path     # 실 DB 디렉터리 오염 방지
            sess = srv.ensure_default_session(80, 24)
            p = sess.active_window.active_pane
            srv._record_warn_history(p, "⚠ 5:00", "long_turn", None, 100.0)
            srv._record_warn_history(p, "⚠ 반복", "repeat", 3, 200.0)
            srv._record_warn_history(p, "⚠ 포맷", "fmt_unknown", None, 300.0)
            hist = srv._read_warn_history()
            assert [h["kind"] for h in hist] == \
                ["fmt_unknown", "repeat", "long_turn"], hist   # 최신 먼저
            assert hist[1]["n"] == 3
            # 상한 초과 → 최근 cap 건만, 가장 최신 ts 가 맨 위.
            cap = srv._WARN_HIST_CAP
            for i in range(cap + 5):
                srv._record_warn_history(p, "x", "long_turn", None, 1000.0 + i)
            allh = srv._read_warn_history(10 ** 6)
            assert len(allh) == cap, len(allh)
            assert allh[0]["ts"] == 1000.0 + cap + 5 - 1
    finally:
        await teardown(srv, task, sock)


async def test_warn_history_records_onset_once_via_scan():
    """항목2: _scan_claude 가 경고 **종류 onset**(이전과 다른 non-None kind)에서만
    이력을 1건 기록한다 — 같은 종류가 이어지는 동안엔 재기록하지 않는다(dedup)."""
    import os
    import tempfile
    import time as _t
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "wh.jsonl")
            srv._warnhist_path = lambda: path
            srv.claude_long_turn_sec = 1          # 1초 넘으면 장기 턴 경고
            sess = srv.ensure_default_session(80, 24)
            win = sess.active_window
            p = win.active_pane
            # busy 진입(스피너) — 매 프레임 새 출력이 와야 dirty-게이팅(_feed_seq)을
            # 통과해 스캔이 돈다(실제 busy 턴은 스피너/토큰이 계속 흐른다).
            p.feed("\x1b[2J\x1b[H✽ Crunching… (99s)\r\n".encode("utf-8"))
            srv._scan_claude(sess, win)
            assert p._claude == "busy"
            p._busy_since = _t.monotonic() - 9999  # 임계 초과로 만들기
            p.feed("\x1b[2J\x1b[H✽ Crunching… (100s)\r\n".encode("utf-8"))
            srv._scan_claude(sess, win)            # 장기 턴 onset → 1건 기록
            p.feed("\x1b[2J\x1b[H✽ Crunching… (101s)\r\n".encode("utf-8"))
            srv._scan_claude(sess, win)            # 같은 kind 지속 → 재기록 안 함
            hist = srv._read_warn_history()
            assert len(hist) == 1 and hist[0]["kind"] == "long_turn", hist
    finally:
        await teardown(srv, task, sock)


async def test_auto_token_on_exit_toggle_and_persist():
    """§10-F set_auto_token_on_exit: 기본 ON, 토글 반전·명시 지정이 server 속성을
    바꾸고 plugin_opts(server_opts_serialize)로 영속된다."""
    from pytmuxlib import plugins
    srv, task, sock = await server_only()
    try:
        reg = plugins.load()
        assert srv.auto_token_on_exit is True, "기본 ON"
        assert srv.set_auto_token_on_exit() is False        # 반전 → off
        assert srv.set_auto_token_on_exit() is True          # 반전 → on
        assert srv.set_auto_token_on_exit(False) is False    # 명시 off
        out = reg.server_opts_serialize(srv)
        assert out["auto_token_on_exit"] is False
    finally:
        await teardown(srv, task, sock)


async def test_claude_auto_redraw_toggle_and_persist():
    """§10-I set_claude_auto_redraw: 기본 off, 무인자 순환(off→idle→corruption→off)·
    명시 지정·구 bool 마이그레이션이 server 속성을 바꾸고 plugin_opts(server_opts_
    serialize)로 영속된다."""
    from pytmuxlib import plugins
    srv, task, sock = await server_only()
    try:
        reg = plugins.load()
        assert srv.claude_auto_redraw == "off", "기본 off"
        assert srv.set_claude_auto_redraw() == "idle"          # 순환 off→idle
        assert srv.set_claude_auto_redraw() == "corruption"    # idle→corruption
        assert srv.set_claude_auto_redraw() == "off"           # corruption→off(순환)
        assert srv.set_claude_auto_redraw("corruption") == "corruption"  # 명시
        assert srv.set_claude_auto_redraw(True) == "idle"      # 구 bool True→idle
        assert srv.set_claude_auto_redraw(False) == "off"      # 구 bool False→off
        srv.set_claude_auto_redraw("idle")
        out = reg.server_opts_serialize(srv)
        assert out["claude_auto_redraw"] == "idle"
    finally:
        await teardown(srv, task, sock)


async def test_claude_auto_redraw_triggers_at_done_boundary():
    """§10-I idle 모드: claude 패널의 busy→(안정)idle 완료 경계(_DONE_IDLE_FRAMES)에서
    _auto_redraw_pane 이 **1회** 불린다. 기본 off 면 무동작, idle 이 정적으로 머물러도
    반복 안 함, 디바운스 내 재완료도 재유발 안 함."""
    import importlib
    sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    _DONE = sm._DONE_IDLE_FRAMES
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        calls = []
        srv._auto_redraw_pane = lambda pane: calls.append(pane)   # 트리거 스파이

        def run_busy_then_idle():
            p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode())
            srv._scan_claude(sess, win)
            assert p._claude == "busy"
            p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
            for _ in range(_DONE):              # _DONE 프레임째 idle 안정=완료 경계
                srv._scan_claude(sess, win)

        # ① 기본 off → busy→안정 idle 이어도 무동작
        assert srv.claude_auto_redraw == "off"
        run_busy_then_idle()
        assert calls == [], "기본 off 면 redraw 안 함"

        # ② idle 모드 + 완료 경계 → 정확히 1회, 그 패널을 대상으로
        assert srv.set_claude_auto_redraw("idle") == "idle"
        run_busy_then_idle()
        assert calls == [p], ("완료 경계에서 1회 redraw", calls)

        # ③ idle 이 정적으로 더 머물러도 반복 유발 안 함(경계 1프레임만)
        for _ in range(_DONE + 2):
            srv._scan_claude(sess, win)
        assert calls == [p], "idle 머무는 동안 반복 안 함"

        # ④ 디바운스: 즉시 다음 busy→idle 은 _AUTO_REDRAW_DEBOUNCE_SEC 내라 재유발 안 함
        run_busy_then_idle()
        assert calls == [p], "디바운스 내 재완료는 재유발 안 함"
    finally:
        await teardown(srv, task, sock)


async def test_claude_auto_redraw_corruption_signal_unit():
    """§10-I corruption 모드 깨짐 감지(_claude_corruption_signal): U+FFFD 와 박스
    테두리 찢김(위/아래 모서리 한쪽만)은 True, 온전한 박스·박스 없음·평범한 텍스트는
    False(보수적=오탐 낮음)."""
    srv, task, sock = await server_only()
    try:
        sig = srv._claude_corruption_signal
        assert sig("hello\nworld") is False            # 박스·FFFD 없음
        assert sig("� corrupted") is True          # U+FFFD
        assert sig("╭─────╮\n│ ok  │\n╰─────╯") is False  # 온전한 박스
        assert sig("╭─────╮\n│ torn") is True           # 위만(아래 모서리 소실)
        assert sig("│ torn\n╰─────╯") is True           # 아래만(위 모서리 소실)
        assert sig("┌──┐\n└──┘") is False               # 각진 온전 박스
    finally:
        await teardown(srv, task, sock)


async def test_claude_auto_redraw_corruption_mode_gates_on_signal():
    """§10-I corruption 모드: 완료 경계라도 **깨짐 신호가 보일 때만** redraw. 깨끗한
    idle 완료는 무동작(idle 모드와 다름), 테두리 찢긴 idle 완료는 1회 유발."""
    import importlib
    sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    _DONE = sm._DONE_IDLE_FRAMES
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        calls = []
        srv._auto_redraw_pane = lambda pane: calls.append(pane)
        assert srv.set_claude_auto_redraw("corruption") == "corruption"

        def busy_then_idle(idle_bytes):
            p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode())
            srv._scan_claude(sess, win)
            assert p._claude == "busy"
            p.feed(idle_bytes)
            for _ in range(_DONE):
                srv._scan_claude(sess, win)

        # ① 깨끗한 idle 완료(박스·FFFD 없음) → corruption 모드라 무동작
        busy_then_idle(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        assert calls == [], "깨끗하면 corruption 모드는 redraw 안 함"

        # ② 테두리 찢긴 idle 완료(위 모서리만) → 깨짐 신호 → 1회 유발
        busy_then_idle("\x1b[2J\x1b[H╭ broken top edge\r\n? for shortcuts\r\n"
                       .encode())
        assert calls == [p], ("깨짐 신호 시 1회 redraw", calls)
    finally:
        await teardown(srv, task, sock)


async def test_auto_token_on_exit_emits_on_session_end():
    """§10-F: Claude 세션이 _HDR_CLAUDE_MISS 프레임 디바운스 뒤 진짜로 사라지는 순간
    (_hdr_claude True→False 확정) _emit_auto_token_log 가 **1회** 발화한다. 디바운스
    동안엔 발화하지 않고, 토글이 off 면 발화하지 않는다."""
    import importlib
    smod = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    MISS = smod._HDR_CLAUDE_MISS
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        calls = []
        # 패널 인자로 호출되는지까지 확인(주입 대상 패널 전달).
        srv._emit_auto_token_log = lambda s, pane=None: calls.append((s, pane))
        # 진짜 종료 = fg 가 셸(_claude_really_exited True) — 결정적 발화 보장.
        srv._fg_command = lambda p: "zsh"
        # busy 로 Claude 세션 확정(_hdr_claude True)
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._hdr_claude is True
        # Claude 사라짐(셸 프롬프트) — 디바운스 동안엔 발화 안 함
        p.feed(b"\x1b[2J\x1b[H$ \r\n")
        for _ in range(MISS - 1):
            srv._scan_claude(sess, win)
        assert calls == [], "디바운스 확정 전엔 발화 안 함"
        # MISS 번째 → _hdr_claude False 확정 → 1회 발화(그 패널에 주입)
        srv._scan_claude(sess, win)
        assert p._hdr_claude is False
        assert calls == [(sess, p)], calls
        # 추가 스캔으로 중복 발화하지 않는다(전이는 1회뿐)
        srv._scan_claude(sess, win)
        assert len(calls) == 1, "한 종료당 1회만"
    finally:
        await teardown(srv, task, sock)


async def test_auto_token_on_exit_off_no_emit():
    """§10-F: 토글 off 면 세션 종료 확정에도 _emit_auto_token_log 가 발화하지 않는다."""
    import importlib
    smod = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    MISS = smod._HDR_CLAUDE_MISS
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        srv.set_auto_token_on_exit(False)
        calls = []
        srv._emit_auto_token_log = lambda s, pane=None: calls.append(s)
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        p.feed(b"\x1b[2J\x1b[H$ \r\n")
        for _ in range(MISS + 2):
            srv._scan_claude(sess, win)
        assert p._hdr_claude is False
        assert calls == [], "토글 off → 무발화"
    finally:
        await teardown(srv, task, sock)


async def test_auto_token_on_exit_skips_when_fg_still_claude():
    """§10-F 화면깨짐 수정(요청 2026-06-18): _hdr_claude 거짓 종료(긴 출력이 Claude
    footer 를 샘플 화면 밖으로 밀어 claude_state→None 이 30프레임 이어진 경우)에는
    Claude 가 여전히 살아 있으므로(포그라운드=node) 토큰 그래프를 주입하지 않는다 —
    살아있는 TUI 한가운데 주입으로 화면이 깨지던 버그를 막는다. 진짜 종료(fg=셸)는
    test_auto_token_on_exit_emits_on_session_end 가 가드한다."""
    import importlib
    smod = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    MISS = smod._HDR_CLAUDE_MISS
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        calls = []
        srv._emit_auto_token_log = lambda s, pane=None: calls.append((s, pane))
        # 거짓 종료: fg 는 여전히 Claude(node) — 화면 스크레이프만 None.
        srv._fg_command = lambda pane: "node"
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._hdr_claude is True
        # footer 가 사라진 화면(긴 출력 모사) — 디바운스가 진행돼 _hdr_claude False 확정.
        p.feed(b"\x1b[2J\x1b[H(long output, no claude footer)\r\n")
        for _ in range(MISS + 2):
            srv._scan_claude(sess, win)
        assert p._hdr_claude is False, "디바운스로 _hdr_claude 는 내려가지만"
        assert calls == [], "fg 가 Claude(node)면 주입 안 함(거짓 종료 화면 보호)"
        # 대조: 같은 패널이 진짜 셸로 복귀(fg=셸)하면 다음 종료 전이에서 주입된다.
        srv._fg_command = lambda pane: "zsh"
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)          # 다시 Claude(_hdr_claude True)
        assert p._hdr_claude is True
        p.feed(b"\x1b[2J\x1b[H$ \r\n")
        for _ in range(MISS + 1):
            srv._scan_claude(sess, win)
        assert calls == [(sess, p)], ("fg=셸 진짜 종료 → 1회 주입", calls)
    finally:
        await teardown(srv, task, sock)


async def test_auto_token_on_exit_injects_usage_into_pane():
    """§10-F(2026-06-18 재설계): 세션 종료 시 토큰 사용량을 **팝업이 아니라 패널 출력
    으로 주입**한다. _emit_auto_token_log 가 self._usage 한도 요약을 그 패널 화면 모델에
    feed 해 스크롤백에 보이게 한다(클라 메시지/모달 없음). 한도 데이터 없으면 무동작."""
    import importlib
    screen_text = importlib.import_module(
        "pytmuxlib.plugins.claude-code.servermixin").screen_text
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        # 한도 데이터 없으면 주입 안 함(빈 텍스트) — 화면 무변경.
        srv._usage = None
        assert srv._usage_exit_text() == b""
        srv._emit_auto_token_log(sess, p)        # 무동작(예외 없이 통과)
        # 그림자 /usage 스냅샷 주입.
        srv._usage = {"session": {"pct": 69, "reset": "10:10pm"},
                      "week_all": {"pct": 61, "reset": "Jun 20 at 9am (Asia/Seoul)"},
                      "week_sonnet": {"pct": 0, "reset": "Jun 20"}}
        text = srv._usage_exit_text()
        assert b"69%" in text and b"61%" in text and b"0%" in text
        assert b"\x1b[2J" not in text, "전체 화면 클리어 없이 흐르는 출력이어야"
        srv._emit_auto_token_log(sess, p)
        shown = screen_text(p.screen)
        assert "69%" in shown and "61%" in shown, shown
        # pane=None 이면 무동작(가드).
        srv._emit_auto_token_log(sess, None)
    finally:
        await teardown(srv, task, sock)


async def test_auto_token_on_exit_falls_back_to_session_tokens():
    """§10-F(2026-07-05 안정화): 그림자 /usage 스냅샷이 없어도(self._usage=None) 종료
    요약이 비지 않는다 — 세션 토큰 총량(pane._session_tokens, 로컬 회계라 프로브 없이 항상
    가용)을 대신 보여 '표시될 때도 안 될 때도' 하던 불안정을 없앤다. 스냅샷이 있으면 토큰
    라인 + 한도 막대 둘 다. 토큰도 0·스냅샷도 없으면(무활동) 무동작."""
    import importlib
    screen_text = importlib.import_module(
        "pytmuxlib.plugins.claude-code.servermixin").screen_text
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        srv._usage = None                    # 한도 스냅샷 없음
        p._session_tokens = 12345
        text = srv._usage_exit_text(80, p)
        assert text != b"", "스냅샷 없어도 토큰 총량으로 비지 않아야"
        assert b"12,345" in text, text
        srv._emit_auto_token_log(sess, p)
        assert "12,345" in screen_text(p.screen)
        # 한도 스냅샷이 있으면 토큰 라인 + 막대 둘 다.
        srv._usage = {"session": {"pct": 42, "reset": "3pm"}}
        both = srv._usage_exit_text(80, p)
        assert b"12,345" in both and b"42%" in both, both
        # 토큰도 0·스냅샷도 없으면 무동작(무활동 세션).
        p._session_tokens = 0
        srv._usage = None
        assert srv._usage_exit_text(80, p) == b""
    finally:
        await teardown(srv, task, sock)


async def test_auto_token_on_exit_preserves_tokens_across_reset():
    """§10-F(2026-07-05 안정화·통합): 세션 토큰 총량은 claude None 전이 시 _scan_claude 가
    0 으로 리셋한다(리셋은 종료 확정 _HDR_CLAUDE_MISS 프레임 **전**). 종료 요약은 리셋
    직전 값(_exit_tokens)을 보존해 표시하므로, 실제 스캔 경로에서도 요약이 비지 않는다
    (직접 호출 테스트가 못 잡는 리셋 타이밍 상호작용 가드)."""
    import importlib
    smod = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    screen_text = smod.screen_text
    MISS = smod._HDR_CLAUDE_MISS
    srv, task, sock = await server_only()
    try:
        srv._usage = None                    # 한도 스냅샷 없이 토큰 폴백만 검증
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        srv._fg_command = lambda pane: "zsh"
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)          # busy → claude 확정
        p._tok_state = {"total": 4200, "peak": 0}
        p._session_tokens = 4200             # 세션 토큰 적재(모사)
        p.feed(b"\x1b[2J\x1b[H$ \r\n")       # claude 사라짐(셸 프롬프트)
        for _ in range(MISS):
            srv._scan_claude(sess, win)
        assert p._session_tokens == 0, "None 전이에 _session_tokens 리셋"
        assert getattr(p, "_exit_tokens", 0) == 4200, "리셋 직전 보존"
        assert "4,200" in screen_text(p.screen), "종료 요약에 세션 토큰 표시(비지 않음)"
    finally:
        await teardown(srv, task, sock)


async def test_auto_token_on_exit_retries_until_shell_confirmed():
    """§10-F(2026-07-05 안정화): 종료 확정 프레임에 fg 가 아직 셸로 안 잡혀도
    (_fg_command→None, tcgetpgrp/ps 일시 실패) _EXIT_TOKEN_RETRY 창 동안 재시도해 셸
    복귀가 잡히는 순간 1회 주입한다 — 일회성 발화가 fg 일시 실패로 영영 유실되던 것을 방지."""
    import importlib
    smod = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    MISS = smod._HDR_CLAUDE_MISS
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        calls = []
        srv._emit_auto_token_log = lambda s, pane=None: calls.append((s, pane))
        srv._fg_command = lambda pane: None      # 종료 확정 시점 fg 미확정
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._hdr_claude is True
        p.feed(b"\x1b[2J\x1b[H$ \r\n")
        for _ in range(MISS):                    # 종료 확정 → 예약(fg None → 미발화)
            srv._scan_claude(sess, win)
        assert p._hdr_claude is False
        assert calls == [], "fg 미확정 동안엔 발화 보류"
        assert p._exit_token_pending > 0, "예약이 살아 있어야(재시도 창)"
        # 셸 복귀가 잡히는 순간 → 다음 스캔에 1회 주입.
        srv._fg_command = lambda pane: "zsh"
        srv._scan_claude(sess, win)
        assert calls == [(sess, p)], ("셸 확정 순간 주입", calls)
        assert p._exit_token_pending == 0
        srv._scan_claude(sess, win)              # 창 소진 후 재발화 없음
        assert len(calls) == 1
    finally:
        await teardown(srv, task, sock)


async def test_startup_rules_injection():
    # #27: 저장된 시작 규칙이 새 Claude 세션의 첫 idle 에 프롬프트로 주입되고 **엔터까지
    # 눌러 제출**된다(본문 줄바꿈은 \n, 맨 끝 \r 로 제출). 빈 규칙이면 주입하지 않는다.
    srv, task, sock = await server_only()
    try:
        srv.claude_auto_launch = False   # 규칙 주입만 격리(auto-launch /rc 제외)
        srv.set_claude_rules("always do X\nand Y")
        assert srv.claude_rules == "always do X\nand Y"
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        writes = []
        p.pty.write = lambda b: writes.append(b)
        # None→claude(새 세션) + idle footer → 같은 스캔에서 예약+주입
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        # 본문은 즉시(\n 줄바꿈), Enter(\r)는 한 박자 뒤 별도 쓰기로 도착한다.
        assert b"".join(writes) == b"always do X\nand Y", writes
        assert p._rules_pending is False
        await asyncio.sleep(srv._RULES_ENTER_DELAY + 0.15)
        assert b"".join(writes) == b"always do X\nand Y\r", writes
        # 빈 규칙이면 다음 세션에서 주입 없음
        srv.set_claude_rules("")
        srv.new_window(sess)
        p2 = sess.tabs[-1].window.active_pane
        w2 = []
        p2.pty.write = lambda b: w2.append(b)
        p2.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, sess.tabs[-1].window)
        assert w2 == [], w2
    finally:
        await teardown(srv, task, sock)


async def test_feedback_prompt_no_key_injection():
    # 사용자 보고(2026-06-20): 세션 피드백 프롬프트("How is Claude doing this session?")
    # 에 대한 Esc 자동 주입이 종종 Dismiss 대신 작동 중인 턴을 **interrupt** 했다(busy 중
    # 배너 텍스트 매칭/feed 지연 stale 매칭 → 단일 Esc 가 interrupt 키로 해석). 그래서
    # 피드백 프롬프트는 더 이상 **어떤 키도 주입하지 않는다** — 비모달이라 안 닫아도 작업을
    # 막지 않고, server_filter_rows(_blank_feedback_banner)가 화면에서 가린다(표시 필터만).
    import importlib
    from pytmuxlib.claude import claude_feedback_prompt
    # serverclaude 는 claude-code 플러그인으로 이전됨(하이픈 디렉토리 → importlib).
    _sc = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    assert _sc._FEEDBACK_DISMISS_KEY == b"\x1b"
    assert claude_feedback_prompt("x How is Claude doing this session? (optional)")
    assert not claude_feedback_prompt("just normal output")
    _BANNER = (b"\x1b[2J\x1b[HHow is Claude doing this session? (optional)\r\n"
               b"  1: Bad   2: Fine   3: Good   0: Dismiss\r\n")
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        writes = []
        p.pty.write = lambda b: writes.append(b)   # 주입 캡처
        # 배너가 여러 프레임 머물러도(스피너처럼 redraw) **단 한 번도** 키를 쏘지 않는다.
        for _ in range(20):
            p.feed(_BANNER)
            srv._scan_claude(sess, win)
        assert writes == [], (writes, "피드백 프롬프트엔 키 주입 없음 — interrupt 위험 제거")
        # `/rc` 메뉴 디바운스 상태와도 무관(피드백은 그 상태를 건드리지 않는다).
        assert p._rc_menu_active is False
    finally:
        await teardown(srv, task, sock)


async def test_remote_menu_matcher_narrow():
    # claude_remote_menu 는 'Disconnect this session' 과 QR/scan 안내가 **함께** 보일
    # 때만 True — 산문에 'Disconnect' 한 단어만 섞인 경우의 오검출을 막는다.
    from pytmuxlib.claude import claude_remote_menu
    MENU = ("Remote Control\n"
            "This session is available in the Claude mobile app and at "
            "https://claude.ai/code/session_X.\n"
            "  Disconnect this session\n"
            "  Show QR code   Scan with your phone to open this session\n"
            "  Continue\n"
            "Enter to select · Esc to continue\n")
    assert claude_remote_menu(MENU)
    assert claude_remote_menu("... Show QR code ...\n Disconnect this session")
    # 한쪽만 있으면 False(오검출 방지).
    assert not claude_remote_menu("Disconnect this session when you are done.")
    assert not claude_remote_menu("Show QR code below to share the link.")
    assert not claude_remote_menu("just normal output")
    assert not claude_remote_menu("")


async def test_auto_dismiss_remote_control_menu():
    # 사용자 보고(2026-06-18): auto-launch 가 새 세션마다 /rc 를 주입하는데 현재 Claude
    # CLI 의 /rc 는 원격 제어 관리 메뉴(Continue/Disconnect/QR, "Esc to continue")를 띄워
    # 진행을 가로막는다. Esc 자동 Dismiss 로 치운다 — Esc=Continue 라 원격은 켜진 채
    # 메뉴만 닫힌다. _rc_menu_active 로 메뉴 인스턴스당 Esc 1회만 쏜다(이중 Esc=Rewind 차단).
    import importlib
    _sc = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    _DISMISS = _sc._FEEDBACK_DISMISS_KEY
    assert _DISMISS == b"\x1b"
    _MENU = (b"\x1b[2J\x1b[HRemote Control\r\n"
             b"This session is available in the Claude mobile app.\r\n"
             b"  Disconnect this session\r\n"
             b"  Show QR code   Scan with your phone to open this session\r\n"
             b"  Continue\r\n"
             b"Enter to select   Esc to continue\r\n")
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        writes = []
        p.pty.write = lambda b: writes.append(b)
        p.feed(_MENU)
        srv._scan_claude(sess, win)
        assert writes == [b"\x1b"], (writes, "메뉴 첫 감지 → 즉시 Esc 1회")
        assert p._rc_menu_active is True
        # 메뉴가 화면에 머무는 동안 redraw 돼도 두 번째 Esc 없음(이중 Esc=Rewind 차단).
        for _ in range(10):
            p.feed(_MENU)
            srv._scan_claude(sess, win)
        assert writes == [b"\x1b"], (writes, "배너당 Esc 는 딱 한 번")
        # 메뉴가 닫히면(다음 idle 화면) 재무장 — 다음 세션 메뉴에 다시 Esc.
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._rc_menu_active is False, "사라지면 재무장"
    finally:
        await teardown(srv, task, sock)


async def test_done_flag_debounced_against_flicker():
    """§10 #18: busy→idle 가 한 프레임 깜빡(idle 한 프레임 뒤 다시 busy)이면 완료
    알림(has_claude_done)이 서지 않는다 — 안정 idle 만 완료로 친다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)
        srv.select_window(sess, 0)
        t1 = sess.tabs[1]
        p1 = t1.window.active_pane
        win = sess.active_window
        p1.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))  # busy
        srv._scan_claude(sess, win)
        p1.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")                  # idle 한 프레임
        srv._scan_claude(sess, win)
        assert t1.has_claude_done is False
        p1.feed("\x1b[2J\x1b[H↑ 2k tokens\r\n".encode("utf-8"))  # 다시 busy(깜빡)
        srv._scan_claude(sess, win)
        # 안정 idle 이 아니었으므로 완료 안 섬
        assert t1.has_claude_done is False, "한 프레임 깜빡임에 완료 오검출 없음"
    finally:
        await teardown(srv, task, sock)


async def test_claude_usage_persists_while_session_alive():
    # 사용량 표시는 Claude 세션이 살아 있는 동안 유지되고(화면에서 토큰 문구가
    # 사라져도), 세션이 끝나면 비워진다(#5).
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p.feed(b"\x1b[2J\x1b[H used 45.2k tokens\r\n? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._claude == "idle" and p._claude_usage == "45.2k tok", p._claude_usage
        # 토큰 문구가 화면에서 사라져도(여전히 idle) 마지막 값 유지
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._claude_usage == "45.2k tok", "세션 살아있는 동안 유지"
        # Claude 세션 종료(평범한 셸) → 사용량 클리어
        p.feed(b"\x1b[2J\x1b[Huser@host ~ % ls\r\n")
        srv._scan_claude(sess, win)
        assert p._claude is None and p._claude_usage is None, "세션 끝 → 클리어"
    finally:
        await teardown(srv, task, sock)


async def test_usage_auto_refresh_and_account(tmp_path=None):
    """M19+: ① 자동 갱신 설정/게이트(_any_claude_pane·interval=0 즉시 반환),
    ② 그림자 probe 계정이 인패널 /usage 갱신에도 보존되는지(일치 확인용)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 기본 자동 갱신 주기(10분) + interval=0 이면 루프가 즉시 반환(행 없음).
        assert srv.usage_refresh_sec == 600, srv.usage_refresh_sec
        assert not srv._any_claude_pane(), "Claude 패널 없으면 False(프로브 스킵)"
        srv.usage_refresh_sec = 0
        await srv._usage_loop()        # 비활성 → 즉시 반환(타임아웃 없이 통과)

        # Claude 패널로 인식되면 게이트 통과
        p.feed("\x1b[2J\x1b[H me@woojinkim.org's Organization\r\n"
               "? for shortcuts\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert srv._any_claude_pane(), "Claude 패널 있으면 True"

        # 그림자 probe 가 계정을 실어 둔 상태에서, 사용자가 인패널 /usage 를 띄워
        # 퍼센트가 갱신돼도 계정(②)은 보존돼야 한다.
        srv._usage = {"session": {"pct": 5, "reset": "2pm"},
                      "account": "me@woojinkim.org"}
        p.feed("\x1b[2J\x1b[HCurrent session\r\n2% used\r\n"
               "Resets 3pm (Asia/Seoul)\r\n? for shortcuts\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert srv._usage["session"]["pct"] == 2, srv._usage
        assert srv._usage.get("account") == "me@woojinkim.org", \
            "인패널 갱신이 그림자 계정을 덮지 않음"
    finally:
        await teardown(srv, task, sock)


async def test_persisted_opt_keys_roundtrip_symmetry():
    """1-4: _PERSISTED_OPT_KEYS 의 **모든** 코어 옵션이 _save_opts 로 파일에 실린다(한 키만
    save 목록에서 빠져도 사용자 변경이 다음 기동에 리셋되던 1-3 드리프트 버그 클래스를 전
    키에 대해 가드). save 가 단일소스 튜플에서 자동 미러되는지 라운드트립으로 확인."""
    import json as _json
    srv, task, sock = await server_only()
    try:
        overrides = {}
        for k in srv._PERSISTED_OPT_KEYS:
            cur = getattr(srv, k)               # __init__ 가 전 키를 로드했음을 전제(strict)
            if isinstance(cur, bool):
                nv = not cur
            elif isinstance(cur, int):
                nv = cur + 7
            else:                               # str
                nv = (cur or "") + "_rt"
            overrides[k] = nv
            setattr(srv, k, nv)
        srv._save_opts()
        saved = _json.load(open(srv.opts_path))
        for k, nv in overrides.items():
            assert k in saved, f"save 단일소스 누락(드리프트): {k}"
            assert saved[k] == nv, f"{k}: {saved.get(k)!r} != {nv!r}"
        # 파일 재로드도 같은 값(_load_opts 로 well-formed 확인)
        reloaded = srv._load_opts()
        for k, nv in overrides.items():
            assert reloaded.get(k) == nv, f"reload 불일치: {k}"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_command_matrix_remote_view_routing():
    """§10-3① 명령×상태 매트릭스 — §1.7 원격 보기 라우팅 계약을 화이트리스트 집합 **전수**로
    검증한다. `_handle_cmd` 의 fall-through↔return↔relay 혼재는 리팩터(God-함수 분할)에서 한
    verb 만 오분류돼도 명령이 **조용히** 깨진다(로컬 트리 오염·중복/누락 broadcast) — 이 매트릭스가
    그 선결 안전망이다. space `feature_matrix` 패턴: '수락(relay)'과 '의도된 거부(block)'를 한
    어휘로 통합해 조용한 실패를 잡는다. 스텁: remote_relay→True(관측)·remote_relay_join→False
    (로컬 타깃=거부 폴백)로 실제 원격 링크 없이 라우팅만 검증한다."""
    from pytmuxlib.model import ClientConn
    from pytmuxlib import serverio as SIO
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)
        srv.new_window(sess)                 # 여러 탭(select_window 등 자연스럽게)

        class _W:
            def write(self, *_a):
                pass

            async def drain(self):
                pass

            def close(self):
                pass

        client = ClientConn(_W())
        client.session = sess
        client.cols, client.rows = 80, 24
        srv.clients.append(client)

        relayed, sent = [], []
        srv.remote_relay = lambda c, m: (relayed.append(m.get("action")) or True)
        srv.remote_relay_join = lambda c, s, m: False

        async def _cap_send_to(c, obj):
            sent.append(obj)
            return True
        srv._send_to = _cap_send_to

        def sig():                           # 로컬 트리 지문(변화 감지)
            return (len(sess.tabs),
                    tuple(len(t.window.panes()) for t in sess.tabs))

        def is_notice(key):
            return any(isinstance(m, dict) and m.get("key") == key for m in sent)

        async def run(action, view, **extra):
            client.remote_view = view
            relayed.clear()
            sent.clear()
            await srv._handle_cmd(client, {"t": "cmd", "action": action, **extra})

        # ── 집합 무결성(라우팅 모순 가드) ──
        assert not (SIO._REMOTE_RELAY_ACTIONS & SIO._REMOTE_BLOCK_ACTIONS), \
            "relay∩block 겹치면 라우팅 모순"
        both = SIO._REMOTE_RELAY_ACTIONS | SIO._REMOTE_BLOCK_ACTIONS
        assert "select_window" not in both and "new_window" not in both, \
            "select_window·new_window 는 별도 분기(집합 밖)"

        # ── ① RELAY: 원격 보기 중 relay + 로컬 트리 불변 + 거부 notice 없음 ──
        for action in sorted(SIO._REMOTE_RELAY_ACTIONS):
            before = sig()
            await run(action, "host")
            assert action in relayed, f"[relay] {action}: 원격 보기 중 릴레이돼야(로컬 미적용)"
            assert sig() == before, f"[relay] {action}: 로컬 트리 불변(조용한 로컬 실행 금지)"
            assert not is_notice("rnotice.mix_block_cmd"), \
                f"[relay] {action}: 거부 notice 없어야"

        # ── ② BLOCK: 원격 보기 중 거부(mix_block) + 릴레이 안 됨 + 트리 불변 ──
        for action in sorted(SIO._REMOTE_BLOCK_ACTIONS):
            before = sig()
            await run(action, "host")
            assert is_notice("rnotice.mix_block_cmd"), \
                f"[block] {action}: 경계 거부 notice 있어야"
            assert action not in relayed, f"[block] {action}: BLOCK 은 릴레이 안 됨"
            assert sig() == before, f"[block] {action}: 로컬 트리 불변(조용한 실행 금지)"

        # ── ③ clear-and-proceed: new_window·select_window(local idx) 는 보기 해제 후 진행 ──
        tabs0 = len(sess.tabs)
        await run("new_window", "host")
        assert client.remote_view is None, "new_window: 원격 보기 해제"
        assert len(sess.tabs) == tabs0 + 1, "new_window: 로컬 새 탭 진행"
        await run("select_window", "host", index=0)
        assert client.remote_view is None, "select_window(local idx): 원격 보기 해제"

        # ── ④ 로컬측 경계 거부(remote_view=None, 병합 원격 index 겨냥) ──
        n = len(sess.tabs)
        for action in ("join_pane", "move_pane_to_tab", "move_tab",
                       "move_window", "swap_window"):
            before = sig()
            await run(action, None, src=n + 5, to=n + 5, index=n + 5)
            assert is_notice("rnotice.mix_block_move"), \
                f"[local-mix] {action}: 병합 원격 index 겨냥은 거부돼야"
            assert sig() == before, f"[local-mix] {action}: 로컬 트리 불변"
    finally:
        await teardown(srv, task, sock)


async def test_scan_model_fallback_and_preserve():
    """모델 귀속 강화(2026-06-22): 라이브 Claude 화면은 모델 배지를 상시 표시하지
    않아(idle 푸터엔 'auto mode on …'·'? for shortcuts'뿐) 토큰이 model NULL('?')로
    적재되는 일이 잦았다.
      ① 배지가 화면에 없으면 그림자 /usage 프로브가 /status 에서 잡은 모델
         (self._usage['model'])로 pane._claude_model 을 채운다(계정 폴백과 동형).
      ② 라이브 배지가 뜨면(/model 변경 직후 등) 그 값이 우선해 폴백을 덮는다.
      ③ 인패널 /usage 갱신(parse_usage, model 없음)은 프로브 모델을 안 지운다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 프로브가 /status 에서 잡아 둔 모델(폴백 출처) — probe 자체는 외부 의존이라 주입
        srv._usage = {"session": {"pct": 5, "reset": "2pm"}, "model": "haiku-4.5"}
        # ① 배지 없는 idle 화면 → claude_model None → 프로브 모델 폴백
        p.feed(b"\x1b[2J\x1b[H Done.\r\n? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._claude == "idle"
        assert p._claude_model == "haiku-4.5", \
            f"배지 부재 시 프로브 모델 폴백 기대, got {p._claude_model!r}"
        # ② 라이브 배지가 뜨면 폴백을 덮는다(라이브 선택이 권위)
        p.feed(b"\x1b[2J\x1b[H Opus 4.8 (1M context)  /model to change\r\n"
               b"? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._claude_model == "opus-4.8", \
            f"라이브 배지 우선 기대, got {p._claude_model!r}"
        # ③ 인패널 /usage 갱신은 그림자 모델을 보존(parse_usage 엔 model 없음)
        p.feed("\x1b[2J\x1b[HCurrent session\r\n2% used\r\n"
               "Resets 3pm (Asia/Seoul)\r\n? for shortcuts\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert srv._usage["session"]["pct"] == 2, srv._usage
        assert srv._usage.get("model") == "haiku-4.5", \
            "인패널 갱신이 그림자 모델을 덮지 않음"
    finally:
        await teardown(srv, task, sock)


async def test_usage_snapshot_persisted_on_scan():
    """S6 T1: 인패널 /usage·footer 인라인 한도가 _usage 를 갱신하는 순간 limits
    스냅샷이 SQLite 에 적힌다(source=panel/inline, 그림자 계정 보존 포함). 같은
    값이 반복 관찰되면 이력이 늘지 않는다(연속 중복 skip)."""
    from pytmuxlib import usagedb
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 그림자 probe 가 계정을 실어 둔 상태(직접 주입 — probe 자체는 외부 의존)
        srv._usage = {"session": {"pct": 5, "reset": "2pm"},
                      "account": "me@woojinkim.org"}
        # ① 인패널 /usage 패널 → source=panel, 보존된 계정까지 스냅샷에 포함
        p.feed("\x1b[2J\x1b[HCurrent session\r\n2% used\r\n"
               "Resets 3pm (Asia/Seoul)\r\n? for shortcuts\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        conn = srv._tokens_db_conn()
        rows = usagedb.query_limits(conn)
        assert len(rows) == 1, rows
        assert rows[0]["session_pct"] == 2 and rows[0]["source"] == "panel"
        assert rows[0]["account"] == "me@woojinkim.org", "그림자 계정 보존"
        # 같은 패널이 계속 보여도(값 불변) 스냅샷은 안 늘어난다 — _usage 불변이라
        # 기록 분기 자체를 안 타고, 탄다 해도 insert_limits 연속 중복 skip.
        srv._scan_claude(sess, win)
        assert usagedb.limits_count(conn) == 1
        # ② footer 인라인 한도 문구 → source=inline
        p.feed("\x1b[2J\x1b[HYou've used 93% of your session limit · resets "
               "1:40pm (Asia/Seoul)\r\n? for shortcuts\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        rows = usagedb.query_limits(conn)
        assert len(rows) == 2, rows
        assert rows[1]["session_pct"] == 93 and rows[1]["source"] == "inline"
        assert usagedb.last_limits(conn)["session_pct"] == 93
    finally:
        await teardown(srv, task, sock)


async def test_token_usage_logging():
    """#7: 응답 확정(committed>0) 시 SQLite 에 ts/tab/pane/session/account/tokens
    한 건이 적히고, 새 Claude 세션마다 session id 가 증가하며, 계정은 화면 이메일
    에서 별칭으로 잡힌다. 수동 지정(set_claude_account)이 자동을 덮는다."""
    from pytmuxlib import usagedb, usagelog
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 새 Claude 세션 + busy(↑ N tokens 가 busy 신호 겸 running) + 계정 단서.
        # 계정은 Claude UI 의 신뢰 신호("<email>'s Organization")에서만 잡는다 —
        # 화면에 흩어진 임의 이메일(git URL 등)은 안 잡힘(2026-06-07 오탐 수정).
        p.feed("\x1b[2J\x1b[H me@woojinkim.org's Organization\r\n"
               "✽ Crunching… (5s · ↑ 1.9k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude == "busy", p._claude
        sid1 = p._claude_session_id
        assert sid1 > 0
        # idle 로 종료 → peak(1900) 확정 → 로그 1줄
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        recs = usagedb.query_records(srv._tokens_db_conn())
        assert len(recs) == 1, recs
        r = recs[0]
        assert r["tokens"] == 1900 and r["pane"] == p.id, r
        assert r["session"] == sid1 and r["tab"] == 0, r
        assert r["account"].endswith("@woojinkim.org"), r["account"]

        # 세션 종료 후 새 Claude 세션 → session id 증가
        p.feed(b"\x1b[2J\x1b[Huser@host ~ % ls\r\n")     # 평범한 셸(claude None)
        srv._scan_claude(sess, win)
        p.feed("\x1b[2J\x1b[H✽ Baking… (4s · ↑ 2k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude_session_id == sid1 + 1, "새 세션 id 증가"

        # 수동 계정 지정이 자동 감지를 덮는다
        srv.set_claude_account(sess, "team-acct")
        assert p._claude_account == "team-acct" and p._claude_account_manual
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        recs = usagedb.query_records(srv._tokens_db_conn())
        assert recs[-1]["account"] == "team-acct", recs[-1]
        # 집계 전체 합
        agg = usagelog.aggregate(recs, "day")
        assert agg["total"] == 3900, agg
    finally:
        try:
            os.unlink(srv.tokens_log_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_session_end_commits_surviving_peak():
    """10-D 하드닝: busy(peak>0)→None 직행(idle 프레임 없음)으로 끝나는 응답 —
    스트리밍 중 Claude 종료/Ctrl-C/quit 등 — 의 진행 중 peak 를 예전엔 reset 이
    그냥 버렸다. 이제 reset 직전 1회 영속 확정해 마지막 부분 응답 토큰을 보존한다.
    정상 busy→idle→None 경로는 idle 에서 이미 확정되므로 reset 이 재계수하지
    않는다(이중 로깅 금지)."""
    from pytmuxlib import usagedb
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # busy 스트리밍 peak=3000, idle 프레임 없이 곧장 None(셸 프롬프트).
        p.feed("\x1b[2J\x1b[H✽ Crunching… (5s · ↑ 3k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude == "busy" and p._tok_state["peak"] == 3000
        p.feed(b"\x1b[2J\x1b[Huser@host ~ % ls\r\n")   # claude None — 직행 종료
        srv._scan_claude(sess, win)
        # 유실되지 않고 DB 에 확정 + 라이브 누계는 세션 종료라 0.
        recs = usagedb.query_records(srv._tokens_db_conn())
        assert len(recs) == 1 and recs[0]["tokens"] == 3000, recs
        assert p._session_tokens == 0 and p._tok_state["peak"] == 0

        # 정상 경로(busy→idle→None)는 idle 에서 1줄 확정, 종료 reset 은 재계수 안 함.
        p.feed("\x1b[2J\x1b[H✽ Baking… (4s · ↑ 2k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")     # idle → peak 확정
        srv._scan_claude(sess, win)
        srv._scan_claude(sess, win)                      # 히스테리시스 확정
        p.feed(b"\x1b[2J\x1b[Huser@host ~ % ls\r\n")     # None → reset(추가 로깅 X)
        srv._scan_claude(sess, win)
        recs = usagedb.query_records(srv._tokens_db_conn())
        assert len(recs) == 2 and recs[1]["tokens"] == 2000, recs
    finally:
        try:
            os.unlink(srv.tokens_log_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_token_debug_log_opt_gated():
    """10-D 진단: 서버 옵션 `token_debug`(opts.json plugin_opts 영속, 런타임
    `set token-debug` 토글)가 꺼져 있으면(기본) 토큰 step 마다 아무 파일도 안 남고,
    켜면 `<sock>.tokendbg.jsonl` 에 한 줄=한 step 으로 running/peak/committed + 스캔
    간격(dt)이 적힌다. 세션 종료(busy→None 직행)로 미확정이던 peak 는 10-D 하드닝으로
    reset 직전 확정되어 종료 프레임 committed 에 반영된다."""
    srv, task, sock = await server_only()
    dbg_path = ipc.state_base(srv.sock_path) + ".tokendbg.jsonl"
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane

        # 기본 OFF: 진단 비활성, 파일 미생성. (env 폴백 영향 배제 — 명시 OFF.)
        srv.token_debug = False
        assert srv._token_debug_on() is False
        p.feed("\x1b[2J\x1b[H✽ Crunching… (5s · ↑ 1.9k tokens)\r\n"
               .encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude == "busy"
        assert not os.path.exists(dbg_path), "OFF 인데 진단 파일이 생겼다"

        # 런타임 토글 ON(setter — opts.json 영속 + 즉시 발효, 데몬 재시작 불필요).
        assert srv.set_token_debug(True) is True
        assert srv._token_debug_on() is True
        # opts.json plugin_opts 에 영속됐는지 — 다시 로드해 확인.
        assert srv._load_opts().get("plugin_opts", {}).get("token_debug") is True
        # busy 두 프레임(peak 1900→2500 상승, 미확정)
        p.feed("\x1b[2J\x1b[H✽ Crunching… (6s · ↑ 2.5k tokens)\r\n"
               .encode("utf-8"))
        srv._scan_claude(sess, win)
        # 세션 종료(claude None) — busy→None 직행이라 진행 중 peak(2500)이 idle
        # 확정을 못 거친다. 10-D 하드닝으로 reset 직전 1회 영속 확정된다.
        p.feed(b"\x1b[2J\x1b[Huser@host ~ % ls\r\n")
        srv._scan_claude(sess, win)

        lines = [json.loads(ln) for ln in
                 open(dbg_path, encoding="utf-8").read().splitlines()]
        assert len(lines) == 2, lines     # OFF 프레임은 빠지고 ON 2 프레임만
        first, last = lines
        # 첫 ON 프레임: busy 상승. 직전 로깅 step 이 없어 dt=None(OFF 프레임은
        # 로깅 안 했으므로 간격 기준점이 없다 — 의도된 동작).
        assert first["state"] == "busy" and first["busy"] is True
        assert first["running"] == 2500 and first["peak1"] == 2500
        assert first["committed"] == 0
        assert first["dt"] is None
        # 종료 프레임: busy→None 직행으로 미확정이던 peak 를 reset 전 확정(10-D
        # 하드닝). reset 플래그 + committed=직전 peak + dt 는 직전 로깅 step(첫 ON
        # 프레임)과의 스캔 간격(float).
        assert last.get("reset") is True
        assert last["state"] is None and last["committed"] == 2500
        assert last["peak0"] == 2500, last
        assert isinstance(last["dt"], float)

        # 런타임 토글 OFF(반전) — 즉시 비활성, opts.json 도 False 로 영속.
        assert srv.set_token_debug() is False
        assert srv._token_debug_on() is False
        assert srv._load_opts().get("plugin_opts", {}).get("token_debug") is False
    finally:
        try:
            os.unlink(dbg_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_token_debug_env_migration_and_status():
    """§10-D env→opts 마이그레이션: opts.json 에 token_debug 키가 없을 때 구
    env `PYTMUX_TOKEN_DEBUG` 를 기동 기본값으로 폴백한다(server_opts_init). 또 현재값을
    status full 페이로드에 실어 :설정 화면이 표시할 수 있게 한다."""
    srv, task, sock = await server_only()
    try:
        # env 켜짐 + opts.json 에 키 없음 → 폴백으로 token_debug True(server_opts_init).
        old = os.environ.get("PYTMUX_TOKEN_DEBUG")
        os.environ["PYTMUX_TOKEN_DEBUG"] = "1"
        try:
            srv.plugins.server_opts_init(srv, {})    # plugin_opts·top-level 모두 없음
            assert srv.token_debug is True
            # opts.json 이 권위: 키가 있으면 env 와 무관하게 그 값을 쓴다.
            srv.plugins.server_opts_init(srv, {"plugin_opts": {"token_debug": False}})
            assert srv.token_debug is False
        finally:
            if old is None:
                os.environ.pop("PYTMUX_TOKEN_DEBUG", None)
            else:
                os.environ["PYTMUX_TOKEN_DEBUG"] = old

        # status full 에 현재값 노출(:설정 표시용).
        srv.token_debug = True
        sess = srv.ensure_default_session(80, 24)
        assert srv._status_msg(sess)["token_debug"] is True
    finally:
        await teardown(srv, task, sock)


async def test_account_backfill_from_usage_probe():
    """계정 미식별(패널 화면에 '<email>'s Organization' 라벨이 안 뜸) 시, 그림자
    /usage 프로브가 /status 로 잡은 계정(srv._usage['account'])으로 패널 계정을 채운다
    (요청 2026-06-12: unknown 적재 감소, 한 머신=한 로그인 가정). 'unknown'/없음이면
    종전대로 None 유지(서버가 unknown 으로 묶음)."""
    from pytmuxlib import usagedb
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 프로브 계정 없음 → 패널 화면에 라벨 없는 Claude busy → 계정 미식별(None) 유지.
        # (_scan_claude 는 feed 로 _feed_seq 가 바뀐 패널만 재스캔하므로 매 스캔 전 feed.)
        srv._usage = None
        p.feed("\x1b[2J\x1b[H✽ Crunching… (2s · ↑ 1.5k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude == "busy" and p._claude_account is None, p._claude_account
        # 프로브가 계정을 잡아 self._usage 에 실렸다 → 다음 프레임(feed→scan)에서 백필.
        srv._usage = {"account": "pr…@team.org",
                      "session": {"pct": 12, "reset": "2pm"}}
        p.feed("↑ 1.6k tokens\r\n".encode("utf-8"))     # 프레임 갱신 → 재스캔
        srv._scan_claude(sess, win)
        assert p._claude_account == "pr…@team.org", p._claude_account
        # 종료 후 새 세션: 계정 리셋 → 프로브 계정 'unknown' 이면 백필 안 함(None 유지).
        p.feed(b"\x1b[2J\x1b[Huser@host ~ % ls\r\n")
        srv._scan_claude(sess, win)
        srv._usage = {"account": "unknown"}
        p.feed("\x1b[2J\x1b[H✽ Flowing… (3s · ↑ 2k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude_account is None, p._claude_account
        # 화면 라벨이 직접 뜨면 그게 우선(프로브 폴백 위에서 실측 라벨 채택).
        p.feed("\x1b[2J\x1b[H me@woojinkim.org's Organization\r\n"
               "✽ Flowing… (6s · ↑ 2k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude_account.endswith("@woojinkim.org"), p._claude_account
        _ = usagedb  # noqa
    finally:
        try:
            os.unlink(srv.tokens_log_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_tokens_db_imports_legacy_jsonl_once():
    """기존 *.tokens.jsonl 이력은 새 DB 최초 사용 시 일회 임포트되어 누적 통계가
    보존되고, 재임포트 방지로 JSONL 은 .imported 로 옮겨진다(중복 적재 없음)."""
    from pytmuxlib import usagedb, usagelog
    srv, task, sock = await server_only()
    try:
        # 새 DB 가 열리기 전에 레거시 JSONL 을 미리 깔아 둔다.
        old = srv.tokens_log_path
        usagelog.append(old, usagelog.make_record(
            1_700_000_000.0, 0, 1, 1, "me@woojinkim.org", 1234))
        usagelog.append(old, usagelog.make_record(
            1_700_000_100.0, 0, 1, 1, None, 66))
        conn = srv._tokens_db_conn()          # 최초 사용 → 임포트 발생
        recs = usagedb.query_records(conn)
        assert len(recs) == 2, recs
        assert sum(r["tokens"] for r in recs) == 1300
        assert not os.path.exists(old), "임포트 후 JSONL 은 .imported 로 이동"
        assert os.path.exists(old + ".imported")
        # 두 번째 호출은 재임포트하지 않는다(같은 연결·count>0).
        assert usagedb.count(srv._tokens_db_conn()) == 2
    finally:
        for suffix in ("", ".imported"):
            try:
                os.unlink(srv.tokens_log_path + suffix)
            except OSError:
                pass
        await teardown(srv, task, sock)


async def test_account_token_total_aggregates_across_panes():
    """§10 계정별 합계 + §10-B(2026-06-11) 안정화: 활성 패널의 Claude 계정을 키로,
    같은 계정에 속한 모든 패널(전체 세션 순회)의 _session_tokens 를 합산해 status
    의 claude_tokens 로 내보낸다. **같은 계정이면 어느 패널이 활성이어도 같은
    숫자**: 식별 계정이 하나뿐이면 미식별 패널까지 포함해 합산하고, 둘 이상이면
    활성 패널 계정의 합계(미식별 패널 활성이면 0 — 클라가 마지막값 유지). Claude
    아니면 0. claude_account 식별자도 함께 보낸다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        srv.split_pane(sess, "tb")
        win = sess.active_window
        panes = win.panes()
        assert len(panes) == 3
        # 같은 계정 두 패널 + 다른 계정 한 패널
        panes[0]._claude_account = "alice"
        panes[0]._session_tokens = 1000
        panes[0]._claude = "idle"
        panes[1]._claude_account = "alice"
        panes[1]._session_tokens = 2500
        panes[1]._claude = "busy"
        panes[2]._claude_account = "bob"
        panes[2]._session_tokens = 700
        panes[2]._claude = "idle"
        # 활성=alice 패널 → alice 합계 3500, 식별자 alice
        win.active_pane = panes[0]
        msg = srv._status_msg(sess)
        assert msg["claude_tokens"] == 3500, msg["claude_tokens"]
        assert msg["claude_account"] == "alice", msg["claude_account"]
        # §10-B: 같은 계정의 다른 패널로 전환해도 같은 숫자
        win.active_pane = panes[1]
        assert srv._status_msg(sess)["claude_tokens"] == 3500
        # 활성=bob 패널 → bob 합계 700(다계정 식별 시 활성 계정 기준)
        win.active_pane = panes[2]
        assert srv._status_msg(sess)["claude_tokens"] == 700
        # 다계정(alice·bob) 식별 상태에서 **미식별** Claude 패널이 활성 → 귀속
        # 모호 → 0(클라가 마지막값 유지). 미식별 패널은 식별 계정 합계에도 안 섞임.
        panes[1]._claude_account = None        # known={alice, bob} 유지
        win.active_pane = panes[1]
        assert srv._status_msg(sess)["claude_tokens"] == 0
        win.active_pane = panes[0]
        assert srv._status_msg(sess)["claude_tokens"] == 1000, \
            "다계정에선 미식별 패널을 임의 계정에 귀속하지 않는다"
        # §10-B 단일 계정 귀속: bob 패널이 사라져 식별 계정이 alice 하나뿐이면
        # 미식별 패널까지 포함해 합산 — 어느 Claude 패널이 활성이어도 같은 숫자.
        panes[2]._claude = None
        panes[2]._claude_account = None        # Claude 흔적 제거(known={alice})
        for active in (panes[0], panes[1]):
            win.active_pane = active
            assert srv._status_msg(sess)["claude_tokens"] == 3500, \
                "단일 계정이면 미식별 포함 전체 합 — 전환해도 동일"
        # Claude 흔적 없는 패널 활성 → 0(클라가 마지막값 유지)
        win.active_pane = panes[2]
        assert srv._status_msg(sess)["claude_tokens"] == 0
        # §10-B 탭 전환: 다른 탭의 같은 계정 Claude 패널이 활성이어도 같은 숫자
        # (_all_panes 가 전 세션·탭 순회 — 새 탭 패널 누계도 합계에 합류).
        srv.new_window(sess)
        p_tab1 = sess.active_window.active_pane
        p_tab1._claude = "idle"
        p_tab1._claude_account = "alice"
        p_tab1._session_tokens = 500
        assert srv._status_msg(sess)["claude_tokens"] == 4000, \
            "탭을 전환해도 같은 계정이면 전체 합계(3500+500)가 그대로"
    finally:
        await teardown(srv, task, sock)


async def test_prompt_clear_mode_sequence():
    """#9: 프롬프트 단위 클리어 모드. 사용자 프롬프트 busy→idle 완료마다 ① 문서화
    지시 → ② /clear 를 순차 주입하고, /clear 완료 후 시퀀스가 끝난다. 끄면 리셋,
    지시문은 opts.json 에 영속."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        injected = []
        srv._pc_inject = lambda pane, text: injected.append(text)
        assert srv.set_prompt_clear(sess, True) is True
        assert p.prompt_clear_mode and p._pc_phase is None

        def complete_response():
            p._claude = "busy"                          # 직전 처리중
            p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")  # idle footer
            srv._scan_claude(sess, win)

        complete_response()      # 사용자 프롬프트 완료 → 문서화 지시 주입
        assert p._pc_phase == "doc"
        assert injected == [srv.prompt_clear_message], injected
        complete_response()      # 문서화 응답 완료 → /clear 주입
        assert p._pc_phase == "clear" and injected[-1] == "/clear"
        complete_response()      # /clear 완료 → 시퀀스 종료
        assert p._pc_phase is None
        assert len(injected) == 2, "doc + clear 두 번만 주입"

        # 끄면 진행 중 상태기계도 리셋
        p._pc_phase = "doc"
        assert srv.set_prompt_clear(sess, False) is False
        assert p._pc_phase is None

        # 지시문 변경 + opts.json 영속
        srv.set_prompt_clear_message("새 지시문")
        assert srv.prompt_clear_message == "새 지시문"
        import json as _json
        saved = _json.load(open(srv.opts_path))["prompt_clear_message"]
        assert saved == "새 지시문", saved
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_no_auto_token_saving_intervention_at_high_usage():
    """§3.10 락인: 예산 임계 기반 자동 토큰절감 개입(≥임계 plan 강제·auto-doc-clear·
    auto-compact 자동 발화)은 §7-4(절대예산 deprecate, 2026-06-11)에서 제거됐다. 남은
    토큰절감 자동화는 **전부 사용자 명시 토글**(prompt_clear·auto_mode·perm 팝업)로만
    발화한다. 이 테스트는 기본 opts(모두 off) 상태에서 컨텍스트가 거의 찬(auto-compact
    임박, 잔량 3%) idle Claude 패널의 busy→idle 완료 경계를 반복해도 doc/clear/compact/
    plan 이 **한 건도** 주입·구동·예약되지 않음을 가드해 자동 개입 재도입 회귀를 막는다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane

        injected = []                       # _pc_inject(doc/clear/rename/rc)
        srv._pc_inject = lambda pane, text: injected.append(text)
        drove = []                          # _drive_perm_mode(plan/auto/…)
        srv._drive_perm_mode = lambda pane, txt, target: drove.append(target)
        scheduled = []                      # call_later 로 건 타이머 이름
        class _FakeLoop:
            def call_later(self, delay, fn, *a):
                scheduled.append(getattr(fn, "__name__", str(fn)))
                class _H:
                    def cancel(self_inner):
                        pass
                return _H()
        srv.loop = _FakeLoop()

        # 기본 opts 는 모두 off — 자동 개입 조건 없음
        assert srv.claude_auto_mode is False
        assert p.prompt_clear_mode is False
        assert not p._perm_target

        # 컨텍스트가 거의 찬 idle Claude 완료 화면(과거라면 자동 plan/compact/doc-clear
        # 개입을 유발했을 조건). busy→idle 완료 경계를 여러 번 반복한다.
        def complete_high_usage():
            p._claude = "busy"
            p.feed("\x1b[2J\x1b[H답변 출력 완료\r\n"
                   "Context left until auto-compact: 3%\r\n"
                   "? for shortcuts\r\n".encode("utf-8"))
            srv._scan_claude(sess, win)

        for _ in range(6):
            complete_high_usage()

        assert p._claude == "idle", p._claude
        assert injected == [], f"자동 doc/clear/compact 주입 0 이어야 함: {injected}"
        assert drove == [], f"자동 plan/perm 구동 0 이어야 함: {drove}"
        # 예약 타이머 중 토큰절감 개입(compact/plan/doc/clear)이 없어야 함
        # (usage 프로브 갱신 등 표시성 타이머는 무관 — 이름으로만 판별).
        bad = [n for n in scheduled
               if any(k in n.lower() for k in ("compact", "plan", "doc", "clear"))]
        assert bad == [], f"토큰절감 자동 타이머 0 이어야 함: {bad}"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_screen_prompt_reflects_remote_injected():
    """§10 #19: 데스크탑 앱 원격제어처럼 입력 경로(_track_prompt)를 안 거친 프롬프트
    도 화면 transcript 에서 추출해 헤더(last_prompt)에 반영한다. 같은 화면 재스캔은
    last_prompt 가 이미 같으므로 변화가 없다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # Claude idle + transcript 에 원격 주입 프롬프트가 그려진 상태
        p.feed("\x1b[2J\x1b[H> 원격에서 보낸 프롬프트\r\n"
               "답변 출력...\r\n더 많은 출력\r\n"
               "⏵⏵ auto mode on (shift+tab to cycle)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude == "idle"
        assert p.last_prompt == "원격에서 보낸 프롬프트", p.last_prompt
        # 같은 화면 재스캔 → last_prompt 유지
        srv._scan_claude(sess, win)
        assert p.last_prompt == "원격에서 보낸 프롬프트"
        # 로컬 입력(_track_prompt)으로 들어온 프롬프트가 화면에도 보여도 유지
        srv._track_prompt(p, "로컬 타이핑 프롬프트\r".encode("utf-8"))
        assert p.last_prompt == "로컬 타이핑 프롬프트"
        p.feed("\x1b[2J\x1b[H> 로컬 타이핑 프롬프트\r\n"
               "답변...\r\n출력\r\n"
               "⏵⏵ auto mode on (shift+tab to cycle)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p.last_prompt == "로컬 타이핑 프롬프트"
    finally:
        await teardown(srv, task, sock)


async def test_autoresume_schedules_on_limit_reset():
    """§10 토큰 리밋 자동재개: 리밋 해제 시각 안내가 뜨면 _maybe_schedule_resume 가
    parse_reset_delay 로 지연을 계산해 _fire_resume 타이머를 건다. serverclaude 믹스인
    분리 후 parse_reset_delay import 누락 회귀를 막는 가드(이 경로는 리밋 화면에서만
    실행돼 기존 스위트가 커버하지 않았다)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        scheduled = []

        class _FakeLoop:
            def call_later(self, delay, fn, *a):
                scheduled.append((delay, fn, a))
        srv.loop = _FakeLoop()

        # parse_reset_delay 가 잡는 문구(리밋+해제시각) → 지연 계산·예약
        srv._maybe_schedule_resume(p, "usage limit reached — resets at 3:00pm")
        assert p._resume_pending is True, "리밋 안내 시 예약 플래그"
        assert scheduled, "resume 타이머가 걸려야 함"
        delay, fn, _ = scheduled[0]
        assert delay > 0 and fn == srv._fire_resume, (delay, fn)

        # 이미 예약된 상태면 중복 예약 안 함
        srv._maybe_schedule_resume(p, "usage limit reached — resets at 3:00pm")
        assert len(scheduled) == 1, "중복 예약 방지"

        # 리밋 신호 없는 일반 출력은 예약하지 않음(scanbuf 도 비워 이전 리밋 잔상 제거)
        p._resume_pending = False
        p._scanbuf = ""
        scheduled.clear()
        srv._maybe_schedule_resume(p, "normal shell output, nothing here")
        assert not scheduled and p._resume_pending is False
    finally:
        await teardown(srv, task, sock)


async def test_auto_retry_on_api_error():
    """전송 에러 자동 재시도(요청 2026-06-12): Claude 패널에 API error/rate limit 가 뜨면
    _scan_claude 가 1분 뒤 _fire_retry 를 예약하고, _fire_retry 는 화면이 **여전히** 에러일
    때만 "계속"+Enter 를 주입한다. 에러 해소(busy 복귀) 시 예약 취소, 토글 off 면 미예약."""
    import types
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        scheduled = []

        class _FakeLoop:
            def call_later(self, delay, fn, *a):
                scheduled.append((delay, fn, a))
                return types.SimpleNamespace(cancel=lambda: None)
        srv.loop = _FakeLoop()
        assert srv.claude_auto_retry is True                 # 기본 ON

        # 먼저 Claude 패널임을 확립(_hdr_claude=True) — busy 한 프레임
        p.feed("\x1b[2J\x1b[H✽ Crunching… (esc to interrupt)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._hdr_claude is True
        # API 에러 화면 → 1분 뒤 _fire_retry 예약
        p.feed("\x1b[2J\x1b[H⎿ API Error: Connection error.\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._retry_pending is True, "에러 시 재시도 예약"
        assert scheduled and scheduled[-1][0] == srv._RETRY_DELAYS[0]   # 1차=1분
        assert scheduled[-1][1] == srv._fire_retry
        # 중복 예약 방지
        n = len(scheduled)
        p.feed("\x1b[2J\x1b[H⎿ API Error: Connection error.\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert len(scheduled) == n, "이미 무장이면 재예약 안 함"

        # _fire_retry: 화면이 여전히 에러 → "계속\r" 주입
        writes = []
        p.pty.write = lambda b: writes.append(b)
        srv._fire_retry(p)
        assert writes == ["계속\r".encode("utf-8")], writes

        # 에러 해소(busy 복귀) → 예약 취소
        p.feed("\x1b[2J\x1b[H✽ Crunching… (esc to interrupt)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._retry_handle is None and p._retry_pending is False
        # 에러 아닐 때 _fire_retry 는 주입 안 함
        writes.clear()
        srv._fire_retry(p)
        assert writes == [], "에러 해소 후엔 주입 안 함"

        # 토글 off → 예약 안 함(무장 해제)
        srv.set_claude_auto_retry(False)
        scheduled.clear()
        p.feed("\x1b[2J\x1b[HAPI Error (500 internal)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert not scheduled and p._retry_pending is False
    finally:
        await teardown(srv, task, sock)


async def test_auto_retry_cancelled_on_respawn():
    """#9 H1: respawn(새 셸) 시 무장된 재시도 타이머를 **취소+리셋**한다. 안 하면 살아있는
    타이머가 새 셸로 "계속" 을 발화하고, _retry_pending 잔류로 새 에러의 재무장이 막힌다."""
    import importlib
    import types
    ps = importlib.import_module("pytmuxlib.plugins.claude-code.panestate")
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        cancelled = []

        class _FakeLoop:
            def call_later(self, delay, fn, *a):
                h = types.SimpleNamespace()
                h.cancel = lambda: cancelled.append(h)
                return h
        srv.loop = _FakeLoop()
        # Claude 패널 확립(busy 프레임) 후 API 에러 → 재시도 무장
        p.feed("\x1b[2J\x1b[H✽ Crunching… (esc to interrupt)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        p.feed("\x1b[2J\x1b[H⎿ API Error: Connection error.\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._retry_pending and p._retry_handle is not None
        h = p._retry_handle
        # respawn → 살아있는 타이머 취소 + 상태 리셋
        ps.reset_pane(p)
        assert h in cancelled, "respawn 이 살아있는 재시도 타이머를 취소"
        assert p._retry_handle is None and p._retry_pending is False
        assert p._retry_attempts == 0
    finally:
        await teardown(srv, task, sock)


async def test_auto_retry_cancelled_on_user_input():
    """#9 H2: 사용자가 패널에 입력하면(작업 이어받음) 무장된 재시도 예약을 취소한다. 안
    하면 전사에 남은 잔상 에러 줄 때문에 _fire_retry 의 발화직전 재확인을 통과해 "계속"
    이 사용자 입력에 더해 중복 주입된다(_handle_input → server_input → _cancel_retry)."""
    import base64
    from pytmuxlib.model import ClientConn
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p._retry_handle = srv.loop.call_later(100, lambda: None)
        p._retry_pending = True
        p._retry_attempts = 2
        client = ClientConn(None)
        client.session = sess
        srv._handle_input(client, {"pane": p.id,
                                   "data": base64.b64encode(b"x").decode()})
        assert p._retry_handle is None and p._retry_pending is False
        assert p._retry_attempts == 0
    finally:
        await teardown(srv, task, sock)


async def test_auto_retry_backoff_then_persistent():
    """연속 재시도는 백오프(60→120→이후 300초 고정)하며 **5분 케이던스로 무기한** 반복한다
    (요청 2026-06-15): 진행 중이던 작업이 지속 outage(529 overloaded 등)에도 영영 멈춰
    있지 않게 — 예전 5회 단념 상한이 곧 "작업이 계속 멈춰 있는" 원인이었다. 에러 해소 시
    카운터가 0 으로 리셋돼 다음 새 에러는 다시 1분(1차)부터 빠르게 시작한다."""
    import types
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        scheduled = []

        class _FakeLoop:
            def call_later(self, delay, fn, *a):
                scheduled.append(delay)
                return types.SimpleNamespace(cancel=lambda: None)
        srv.loop = _FakeLoop()
        writes = []
        p.pty.write = lambda b: writes.append(b)
        # 화면을 에러 상태로 고정(스캔 디바운스와 분리해 무장/발화 로직만 검증)
        p.feed("\x1b[2J\x1b[H⎿ API Error: Connection error.\r\n".encode("utf-8"))
        seen = []
        ROUNDS = 8                            # 옛 상한(5)을 한참 넘겨도 계속 무장됨을 확인
        for i in range(ROUNDS):
            srv._maybe_schedule_retry(p)
            assert p._retry_pending, f"{i}차 무장(단념 없음)"
            seen.append(scheduled[-1])
            srv._fire_retry(p)                # 여전히 에러 → 주입 + attempts++
        # 1·2차 빠른 재시도 후 3차부터 5분(300초) 케이던스로 무기한
        assert seen == [60.0, 120.0] + [300.0] * (ROUNDS - 2), seen
        assert len(writes) == ROUNDS, "상한 없이 매 라운드 주입"
        assert p._retry_attempts == ROUNDS
        # 에러 해소(idle 복귀, 새 출력) → dispatch else 분기가 카운터 리셋
        p.feed("\x1b[2J\x1b[H? for shortcuts\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._retry_attempts == 0, "에러 해소 시 카운터 리셋"
        # 리셋 후 새 에러 → 다시 1분(1차)부터
        srv._maybe_schedule_retry(p)
        assert scheduled[-1] == 60.0, "리셋 후 1차=1분"
    finally:
        await teardown(srv, task, sock)


async def test_auto_retry_not_fired_when_busy():
    """#9: _fire_retry 는 Claude 가 **이미 busy**(스스로 재시도 중)면 주입하지 않는다 —
    전사에 잔상 에러 줄이 남아 claude_api_error 가 True 라도 working Claude 를 방해 안 함."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        writes = []
        p.pty.write = lambda b: writes.append(b)
        # busy 스피너 + 위쪽 전사에 잔상 "API Error" 가 같이 보이는 프레임
        p.feed(("\x1b[2J\x1b[H⎿ API Error: Connection error.\r\n"
                "✽ Crunching… (8s · ↑ 1.2k tokens)\r\n").encode("utf-8"))
        srv._fire_retry(p)
        assert writes == [], "busy(재시도 중)면 주입 안 함"
    finally:
        await teardown(srv, task, sock)


async def test_auto_retry_cancelled_on_pane_close():
    """#9 M1: 패널 종료 시 무장된 재시도/재개 타이머를 거둔다 — 닫힌 Pane 참조가 최대
    백오프 간격(최대 5분) 동안 call_later 큐에 살아있지 않게(pane_closing → _cancel_*)."""
    import types
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        cancelled = []
        hr = types.SimpleNamespace(cancel=lambda: cancelled.append("retry"))
        hs = types.SimpleNamespace(cancel=lambda: cancelled.append("resume"))
        p._retry_handle, p._retry_pending = hr, True
        p._resume_handle, p._resume_pending = hs, True
        srv.plugins.pane_closing(srv, p)
        assert "retry" in cancelled and "resume" in cancelled
        assert p._retry_handle is None and p._retry_pending is False
        assert p._resume_handle is None and p._resume_pending is False
    finally:
        await teardown(srv, task, sock)


async def test_scan_claude_gating_skips_settled_pane():
    """B1 성능: 출력이 없으면(settled) _scan_claude 가 비싼 화면 파싱(claude_state)을
    건너뛴다. 새 출력이 오면 다시 스캔. 전이 디바운스(done 알림 등)는 별도 테스트가
    출력 없이도 진행됨을 보장한다(test_inactive_tab_claude_done_flag)."""
    import importlib
    # _scan_claude 은 이제 claude-code 플러그인의 servermixin 에서 돈다 — 그 모듈의
    # claude_state 참조를 패치해야 스캔이 본다(코어 serverclaude 패치는 무효).
    sc = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    srv, task, sock = await server_only()
    orig = sc.claude_state
    calls = []
    sc.claude_state = lambda txt: (calls.append(1), orig(txt))[1]
    try:
        srv.claude_auto_launch = False   # auto /rc 디바운스(_rc_pending 스캔 지속) 제외 — B1 게이팅만 격리
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")   # idle 출력 → _feed_seq 증가
        srv._scan_claude(sess, win)
        n1 = len(calls)
        assert n1 >= 1 and p._claude == "idle"
        # 출력 없이 재스캔 2회 → settled 라 claude_state 재호출 없음(스킵)
        srv._scan_claude(sess, win)
        srv._scan_claude(sess, win)
        assert len(calls) == n1, ("settled 패널 재파싱 안 함", len(calls), n1)
        # 새 출력 → 다시 스캔
        p.feed(b"\x1b[2J\x1b[Hbusy... \xe2\x86\x91 1k tokens (5s)\r\n")
        srv._scan_claude(sess, win)
        assert len(calls) == n1 + 1, "출력 오면 재스캔"
    finally:
        sc.claude_state = orig
        await teardown(srv, task, sock)


async def test_screen_delta_frame_and_equivalence():
    """B2 행 단위 델타: _screen_frame 이 최초엔 full screen, 이후 소수 행 변경엔
    screen-delta 를 낸다. 델타를 직전 rows 에 적용하면 full render 와 동일(골든),
    변경 행이 임계 초과면 full 폴백."""
    import json
    from pytmuxlib.model import ClientConn
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(40, 10)
        p = sess.active_window.active_pane
        c = ClientConn(None)

        def decode(frame):
            return json.loads(frame[4:].decode("utf-8"))

        p.feed(b"\x1b[2J\x1b[Hline0\r\nline1\r\nline2\r\n")
        rows1, cur1 = p.render(True)
        f1 = decode(srv._screen_frame(c, p.id, rows1, cur1))
        assert f1["t"] == "screen", "최초 전송은 full"

        # 한 행만 변경 → 델타
        p.feed(b"\x1b[2;1HLINE1-CHANGED")
        rows2, cur2 = p.render(True)
        f2 = decode(srv._screen_frame(c, p.id, rows2, cur2))
        assert f2["t"] == "screen-delta", f2["t"]
        assert 0 < len(f2["rows"]) < len(rows2), "일부 행만"
        # 델타를 직전 rows 에 적용 → full render 와 셀 단위 동일
        applied = list(rows1)
        for y, segs in f2["rows"]:
            applied[y] = segs
        assert applied == rows2, "델타 적용 == full render(골든)"

        # 대부분 행 변경 → full 폴백
        p.feed(b"\x1b[2J\x1b[H" + b"\r\n".join(b"x" * 30 for _ in range(9)))
        rows3, cur3 = p.render(True)
        f3 = decode(srv._screen_frame(c, p.id, rows3, cur3))
        assert f3["t"] == "screen", "대부분 변경 시 full 폴백"

        # 행 수 변동(리사이즈)도 full
        c._sent_rows[p.id] = rows3[:5]
        f4 = decode(srv._screen_frame(c, p.id, rows3, cur3))
        assert f4["t"] == "screen", "행 수 불일치 시 full"
    finally:
        await teardown(srv, task, sock)


async def test_claude_auto_mode_cycles_to_auto():
    """§10 권한모드 자동전환: 토글 ON 이면 idle 패널의 footer 권한모드가 auto 가
    아닐 때 shift+tab(\\x1b[Z)을 폐루프로 순환 주입해 auto 로 맞춘다. 같은 모드
    반복(화면 미갱신) 시 중복 주입 안 함, auto/bypass 도달 시 정지, idle 이탈 시
    카운터 리셋, 오검출 대비 _CAM_MAX 가드. 토글 opts 영속."""
    import importlib
    _CAM_MAX = importlib.import_module(
        "pytmuxlib.plugins.claude-code.servermixin")._CAM_MAX
    srv, task, sock = await server_only()
    try:
        srv.claude_auto_launch = False   # 상시 auto_mode 만 격리(launch /rc·auto 제외)
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        sent = []
        srv._inject_keys = lambda pane, data: sent.append(data)
        BT = b"\x1b[Z"

        assert srv.claude_auto_mode is False, "기본 off"
        assert srv.set_claude_auto_mode(True) is True
        import json as _json
        assert _json.load(open(srv.opts_path))["claude_auto_mode"] is True

        def scan(s):
            p.feed(b"\x1b[2J\x1b[H" + s.encode("utf-8") + b"\r\n")
            srv._scan_claude(sess, win)

        scan("? for shortcuts")                       # idle, 실제 default footer → 1회
        assert p._claude == "idle" and sent == [BT], sent
        scan("? for shortcuts")                       # 같은 default → 중복 방지
        assert sent == [BT]
        scan("⏸ plan mode on (shift+tab to cycle)")    # plan → 한 번 더
        assert sent == [BT, BT], sent
        scan("⏵⏵ auto mode on (shift+tab to cycle)")   # auto 도달 → 정지+리셋
        assert sent == [BT, BT] and p._cam_tries == 0

        # bypass(명시·위험 모드)는 건드리지 않음
        sent.clear()
        scan("bypass permissions on (shift+tab to cycle)")
        assert sent == []

        # idle 이탈(busy) 시 카운터 리셋(다음 idle 진입에 다시 시도)
        scan("⏸ plan mode on (shift+tab to cycle)")    # plan → 주입(1)
        assert sent == [BT]
        scan("✽ Crunching… (5s · ↑ 1k tokens)")        # busy → 리셋
        assert p._claude == "busy" and p._cam_tries == 0

        # _CAM_MAX 가드: 모드가 계속 바뀌어도 최대 _CAM_MAX 회까지만
        sent.clear()
        modes = ["? for shortcuts", "⏸ plan mode on (shift+tab to cycle)"]
        for i in range(_CAM_MAX + 3):
            scan(modes[i % 2])
        assert len(sent) == _CAM_MAX, (len(sent), _CAM_MAX)

        # 토글 off → 카운터 리셋, 더 안 보냄
        srv.set_claude_auto_mode(False)
        assert p._cam_tries == 0
        sent.clear()
        scan("? for shortcuts")
        assert sent == []
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_claude_auto_launch_rc_and_perm_auto():
    """요청: 새 Claude 세션이 패널에 뜨면(None→Claude) auto-launch(기본 ON)가
    ① 첫 idle 에 `/rc`(원격 제어) 를 1회 주입하고 ② 다음 idle 에 권한모드를 auto 로
    1회 유도(shift+tab 폐루프)한다. 이미 원격제어가 켜진 화면('Remote Control active')
    에선 /rc 를 건너뛴다(도로 끄지 않음). 토글 OFF 면 어느 것도 안 한다. opts 영속."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        rc, bt = [], []
        srv._pc_inject = lambda pane, text: rc.append(text)
        srv._inject_keys = lambda pane, data: bt.append(data)
        BT = b"\x1b[Z"

        assert srv.claude_auto_launch is True, "기본 on"

        import importlib
        _sc = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")

        def scan(s):
            p.feed(b"\x1b[2J\x1b[H" + s.encode("utf-8") + b"\r\n")
            srv._scan_claude(sess, win)

        # 새 세션(None→idle): auto /rc 는 첫 idle 한 프레임에 즉발하지 않는다(버그 수정)
        # — 데스크탑 앱이 원격제어를 이미 켜 둔 경우 'Remote Control active' 오버레이가
        # 한두 프레임 늦게 떠도 잡도록 idle 이 _RC_CONFIRM_FRAMES 안정될 때까지 디바운스.
        scan("? for shortcuts")
        assert p._claude == "idle"
        assert rc == [], "첫 idle 프레임엔 /rc 즉발 안 함(디바운스 시작)"
        assert p._rc_pending is True
        for _ in range(_sc._RC_CONFIRM_FRAMES - 2):
            scan("? for shortcuts")
        assert rc == [], "디바운스 만료 전엔 여전히 /rc 없음"
        # 이 프레임에 _idle_frames 가 임계 도달 → /rc 1회, 권한 유도는 예약만(프레임 분리)
        scan("? for shortcuts")
        assert rc == ["/rc"], rc
        assert p._rc_pending is False and p._perm_auto_pending is True
        assert bt == [], "이 프레임엔 shift+tab 없음(프레임 분리)"

        # 다음 idle(권한=plan): _perm_target=auto 세우고 shift+tab 1회 주입
        scan("⏸ plan mode on (shift+tab to cycle)")
        assert p._perm_auto_pending is False and p._perm_target == "auto"
        assert bt == [BT], bt
        # auto 도달 → 폐루프 정지, /rc 재주입 없음(세션 유지 중)
        scan("⏵⏵ auto mode on (shift+tab to cycle)")
        assert p._perm_target is None and rc == ["/rc"]

        # 같은 세션 유지 중엔 /rc 재주입 없음(busy→idle 왕복해도)
        scan("✽ Crunching… (5s · ↑ 1k tokens)")   # busy → idle 이탈(세션 유지)
        scan("/help for help")                     # 다시 idle — 재주입 없음
        assert rc == ["/rc"]
        # 세션 종료(None) 후 'Remote Control active' 동반 재시작
        scan("$ ")                          # 비-Claude(None) → 세션 끝
        assert p._claude is None
        rc.clear()
        scan("Remote Control active\n? for shortcuts")   # 새 세션 + 이미 원격 ON
        assert p._claude == "idle"
        assert rc == [], "원격제어 ON 화면이면 /rc 건너뜀"
        assert p._perm_auto_pending is True, "그래도 권한 auto 유도는 예약"

        # 토글 OFF → 영속 + 새 세션에 아무 자동 셋업 안 함
        import json as _json
        assert srv.set_claude_auto_launch(False) is False
        assert _json.load(open(srv.opts_path))["claude_auto_launch"] is False
        scan("$ ")                          # 세션 종료
        rc.clear(); bt.clear()
        scan("? for shortcuts")             # 새 세션이지만 OFF
        assert p._rc_pending is False and p._perm_auto_pending is False
        assert rc == [] and bt == []
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_rc_skipped_when_remote_active_appears_during_debounce():
    """버그 수정(요청 2026-06-12): 원격제어가 **이미 활성**인 세션에 auto /rc 를 쏘면
    /remote-control 이 응답 대기 대화로 멈춰 진행이 정지한다. 데스크탑 앱의 'Remote
    Control active' 오버레이는 새 세션 첫 idle 직후 한두 프레임 늦게 그려질 수 있어,
    첫 프레임만 보고 쏘면 가드를 못 세운다. 수정: idle 이 _RC_CONFIRM_FRAMES 안정될
    때까지 디바운스 — 그 사이 오버레이가 뜨면 _rc_done 이 서서 /rc 를 건너뛴다."""
    srv, task, sock = await server_only()
    try:
        import importlib
        _sc = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        rc = []
        srv._pc_inject = lambda pane, text: rc.append(text)
        srv._inject_keys = lambda pane, data: None

        def scan(s):
            p.feed(b"\x1b[2J\x1b[H" + s.encode("utf-8") + b"\r\n")
            srv._scan_claude(sess, win)

        # 새 세션: 첫 idle 몇 프레임은 오버레이 없이 평범한 프롬프트(데스크탑 앱 재연결
        # 직전) — /rc 즉발 금지, 디바운스 진행 중.
        scan("? for shortcuts")
        assert p._claude == "idle" and p._rc_pending is True
        for _ in range(_sc._RC_CONFIRM_FRAMES // 2):
            scan("? for shortcuts")
        assert rc == [], "디바운스 중엔 아직 /rc 없음"
        # 디바운스 임계 전에 'Remote Control active' 오버레이가 뜸 → _rc_done 셋·/rc 스킵.
        scan("⏵⏵ auto mode on (shift+tab to cycle)Remote Control active")
        assert p._rc_done is True
        assert p._rc_pending is False, "원격 ON 관측 → 디바운스 종료"
        assert srv._rc_seen_active is True, "원격 ON 관측 → 서버 전역 sticky 셋"
        assert rc == [], "이미 원격제어 ON — /rc 안 쏨(응답 대기 대화 방지)"
        # 이후 오버레이가 안 보이는 프레임이 와도 재주입 없음(sticky _rc_done).
        for _ in range(_sc._RC_CONFIRM_FRAMES + 2):
            scan("? for shortcuts")
        assert rc == [], "원격 ON 관측 후엔 /rc 재발 없음"
    finally:
        await teardown(srv, task, sock)


async def test_rc_globally_suppressed_after_remote_seen_active():
    """버그 수정 강화(요청 2026-06-12 — "이미 리모트컨트롤 켜져있을 때 /remote-control
    대화 다시 띄우지 마세요"): 원격제어가 **이미 켜진** 게 한 번이라도 관측되면 이 서버
    세션 동안 auto /rc 를 **서버 전역**으로 영구 중단한다(데스크탑 앱이 세션마다 원격제어
    지속 연결). 디바운스(타이밍)만으론 첫 프레임 레이스를 완전히 못 막으므로, 정책 차단과
    같은 sticky(_rc_seen_active)로 새 세션의 /rc 재무장 자체를 막아 확정 보장한다."""
    srv, task, sock = await server_only()
    try:
        import importlib
        _sc = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        rc = []
        srv._pc_inject = lambda pane, text: rc.append(text)
        srv._inject_keys = lambda pane, data: None

        def scan(s):
            p.feed(b"\x1b[2J\x1b[H" + s.encode("utf-8") + b"\r\n")
            srv._scan_claude(sess, win)

        assert srv._rc_seen_active is False
        # 세션 A: 원격제어가 이미 켜진 화면 관측 → 서버 전역 sticky 셋, /rc 안 쏨.
        scan("⏵⏵ auto mode on (shift+tab to cycle)Remote Control active")
        assert srv._rc_seen_active is True and rc == []
        # 세션 종료(디바운스 확정) → 새 세션 시작.
        for _ in range(_sc._HDR_CLAUDE_MISS + 1):
            scan("$ ")
        assert p._claude is None
        rc.clear()
        # 새 세션이 원격제어 표시 **없이** 떠도(오버레이 늦음) — sticky 로 fire 시점에
        # /rc 확정 스킵. 단 권한모드 auto 유도(_perm_auto_pending)는 디커플링되어 정상
        # 인계된다(auto-launch 는 /rc 외에 perm-auto 도 겸함 — 그건 막지 않는다).
        scan("? for shortcuts")
        assert p._claude == "idle"
        assert p._perm_auto_pending is True, "perm-auto 는 sticky 와 무관하게 인계"
        for _ in range(_sc._RC_CONFIRM_FRAMES + 2):
            scan("? for shortcuts")
        assert rc == [], "원격 기관측 후엔 새 세션에도 /rc 안 쏨(대화 재호출 방지)"
    finally:
        await teardown(srv, task, sock)


async def test_rc_suppressed_after_org_policy_block():
    """요청: '원격 제어가 조직 정책으로 비활성화' 메시지를 한 번 보면 이 세션(서버
    프로세스) 동안 자동 /rc 를 영구 중단한다 — 매 새 세션마다 /rc 를 재시도해 같은
    거부를 반복하지 않는다. 서버 전역 sticky 플래그(조직 단위 정책)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        rc = []
        srv._pc_inject = lambda pane, text: rc.append(text)
        srv._inject_keys = lambda pane, data: None

        assert srv.claude_auto_launch is True and srv._rc_policy_blocked is False

        import importlib
        _sc = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")

        def scan(s):
            p.feed(b"\x1b[2J\x1b[H" + s.encode("utf-8") + b"\r\n")
            srv._scan_claude(sess, win)

        def settle_rc():
            # auto /rc 디바운스(_RC_CONFIRM_FRAMES) 통과까지 idle 을 반복 스캔.
            for _ in range(_sc._RC_CONFIRM_FRAMES):
                scan("? for shortcuts")

        # 새 세션 → 디바운스 통과 후 /rc 1회 주입
        settle_rc()
        assert p._claude == "idle" and rc == ["/rc"], rc
        # /rc 결과로 조직 정책 거부 메시지가 뜸 → sticky 차단 + 무장 해제
        scan("/remote-control\n"
             "Remote Control is disabled by your organization's policy.")
        assert srv._rc_policy_blocked is True
        assert p._rc_pending is False
        # 세션 종료 후 새 세션 → /rc 재무장·재주입 없음(차단 유지)
        scan("$ ")
        assert p._claude is None
        rc.clear()
        scan("? for shortcuts")
        assert p._claude == "idle"
        assert p._rc_pending is False, "차단 후 /rc 재무장 안 함"
        assert rc == [], "차단 후 /rc 재주입 없음"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_rc_not_reinjected_after_restart_transient():
    """버그(요청): 작업보존 재시작(re-exec) 직후 _induce_redraw_all 의 강제 repaint 가
    순간 빈 프레임을 만들어 _claude 가 None→Claude 로 깜빡이면 거짓 '새 세션'으로
    오인돼 auto /rc 가 재주입됐다 — 이미 켜진 원격제어 패널이 다시 떴다. fire 시점
    _rc_done(직렬화 sticky) 가드로 재주입을 막고, 진짜 세션 종료(디바운스)에서만 해제해
    다음 claude 기동엔 정상 재무장한다."""
    import importlib
    _sc = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
    from pytmuxlib.model import Pane
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        rc = []
        srv._pc_inject = lambda pane, text: rc.append(text)
        srv._inject_keys = lambda pane, data: None

        def scan(s):
            p.feed(b"\x1b[2J\x1b[H" + s.encode("utf-8") + b"\r\n")
            srv._scan_claude(sess, win)

        def settle_rc():
            # auto /rc 디바운스(_RC_CONFIRM_FRAMES) 통과까지 idle 을 반복 스캔.
            for _ in range(_sc._RC_CONFIRM_FRAMES):
                scan("? for shortcuts")

        # 최초 세션 → 디바운스 통과 후 /rc 1회 주입, _rc_done 셋
        settle_rc()
        assert rc == ["/rc"] and p._rc_done is True

        # _rc_done 은 재시작 직렬화 대상이라 re-exec 후에도 유지된다(이 케이스의 핵심).
        # S4 에서 직렬화 위치가 코어 _RESUME_FIELDS → claude-code 플러그인 pane_serialize
        # 로 이전됐다 — export_state 의 불투명 'plugin_state' dict 에 담긴다(동작 불변).
        assert p.export_state()["plugin_state"]["_rc_done"] is True

        # 재시작 transient 재현: 빈 프레임(None) 몇 개 뒤 다시 Claude(거짓 None→Claude).
        # 미스가 디바운스 임계에 못 미쳐 _rc_done 이 살아남아 /rc 가 재주입되지 않는다.
        rc.clear()
        for _ in range(3):
            scan("")
        assert p._claude is None
        scan("? for shortcuts")
        assert p._claude == "idle"
        assert rc == [], "재시작 transient 후 /rc 재주입 없음"
        assert p._rc_done is True

        # 진짜 세션 종료(디바운스 임계 초과) → sticky 해제 → 다음 기동엔 재무장·주입
        for _ in range(_sc._HDR_CLAUDE_MISS + 1):
            scan("$ ")
        assert p._rc_done is False, "디바운스 확정 종료 후 sticky 해제"
        rc.clear()
        settle_rc()
        assert rc == ["/rc"], "진짜 새 세션엔 /rc 재주입"
    finally:
        await teardown(srv, task, sock)


async def test_manual_usage_panel_captured_for_5h_pct():
    """요청: 사용자가 패널에서 직접 /usage 를 띄우면 그 **실측** 한도를 캡처해 상태줄
    5h% 가 /usage 의 세션 %와 일치한다. S6 T3: 분모 근사 폐기 — 실측 전엔 None
    (지어내지 않음)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p._claude = "idle"
        assert srv._tok5h_pct(p, 1000) is None, "실측 전엔 None(근사 폐기)"
        # 사용자가 /usage 를 띄움(패널 + Claude footer 동반) → 세션 61% 를 캡처.
        panel = ("Current session\n  blocks 61% used\n  Resets 1:40pm (Asia/Seoul)\n"
                 "Current week (all models)\n  blocks 41% used\n"
                 "? for shortcuts\n")
        p.feed(b"\x1b[2J\x1b[H" + panel.encode("utf-8"))
        srv._scan_claude(sess, win)
        assert isinstance(srv._usage, dict), srv._usage
        assert srv._usage["session"]["pct"] == 61, srv._usage
        p._claude = "idle"
        assert srv._tok5h_pct(p, 1000) == 61, "이제 /usage 실측을 따른다"
    finally:
        await teardown(srv, task, sock)


async def test_rename_single_claude_pane_injects_rename():
    """요청: 탭에 패널이 하나뿐이고 그게 Claude Code 패널이면, 탭 이름 변경 시 같은
    이름을 Claude 세션에도 /rename 으로 주입한다. 패널이 둘 이상이거나 Claude 가
    아니면 주입하지 않는다(모호/무관)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        injected = []
        srv._pc_inject = lambda pane, text: injected.append((pane, text))

        # ① 단일 Claude 패널 → 탭 이름 + /rename 주입
        p._claude = "idle"
        srv.rename_window(sess, "myproj")
        assert sess.active_tab.name == "myproj"
        assert injected == [(p, "/rename myproj")], injected

        # ② 단일이지만 Claude 아님 → 주입 안 함(탭 이름만)
        injected.clear()
        p._claude = None
        srv.rename_window(sess, "plain")
        assert sess.active_tab.name == "plain" and injected == [], injected

        # ③ 패널 둘 → 모호 → 주입 안 함
        injected.clear()
        p._claude = "idle"
        srv.split_pane(sess, "lr")
        assert len(win.panes()) == 2
        srv.rename_window(sess, "two")
        assert sess.active_tab.name == "two" and injected == [], injected
    finally:
        await teardown(srv, task, sock)


async def test_rename_busy_claude_defers_until_idle():
    """단일 Claude 패널이라도 리네임 당시 busy 면 즉시 주입하지 않고(슬래시 명령으로
    실행 안 됨), _pending_rename 에 보류했다가 다음 busy→idle 경계(_scan_claude)에서
    /rename 을 발동한다(요청). idle 게이트는 자동 compact/doc-clear 와 동일한 규약."""
    srv, task, sock = await server_only()
    try:
        srv.claude_auto_launch = False   # 첫 idle /rc 자동주입 격리(드레인만 검증)
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        injected = []
        srv._pc_inject = lambda pane, text: injected.append((pane, text))

        # busy 중 리네임 → 즉시 주입 없음, 보류만
        p._claude = "busy"
        srv.rename_window(sess, "myproj")
        assert sess.active_tab.name == "myproj"
        assert injected == [], injected
        assert p._pending_rename == "myproj"

        # 응답 종료(busy→idle, 입력 준비됨) → 보류분 발동 후 비움
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._claude == "idle"
        assert injected == [(p, "/rename myproj")], injected
        assert p._pending_rename is None
    finally:
        await teardown(srv, task, sock)


async def test_inpane_usage_panel_no_autopopup_seq():
    """§3.9(2026-06-17): 인패널 /usage 자동 팝업 신호(_usage_shown_seq)는 제거됐다.
    패널을 봐도 그런 서버 필드/status 키가 생기지 않는다(실측 캡처는 별도로 유지 —
    test_manual_usage_panel_captured_for_5h_pct 참조)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p._claude = "idle"
        assert not hasattr(srv, "_usage_shown_seq"), \
            "자동 팝업 시퀀스 필드가 남아있음(§3.9 제거 대상)"
        panel = ("Current session\n  blocks 10% used\n  Resets 5am (Asia/Seoul)\n"
                 "Current week (all models)\n  blocks 14% used\n? for shortcuts\n")
        p.feed(b"\x1b[2J\x1b[H" + panel.encode("utf-8"))
        srv._scan_claude(sess, win)
        # 패널을 봐도 자동 팝업 신호 필드는 생기지 않는다(스캔이 만들지 않음).
        assert not hasattr(srv, "_usage_shown_seq")
    finally:
        await teardown(srv, task, sock)


async def test_request_redraw_induces_repaint_and_resends_full():
    """§2.12 redraw/refresh 명령(prefix r): 서버가 ① 각 패널에 SIGWINCH 유발
    (_induce_redraw_all)해 alt-screen 앱이 전체 repaint 하게 하고 ② 요청 클라에
    전체 프레임(layout+screen)을 다시 보낸다(stale 스냅샷 교체)."""
    from pytmuxlib.protocol import write_msg, read_msg, PROTO_VERSION
    srv, task, sock = await server_only()
    try:
        srv.ensure_default_session(80, 24)
        induced = []
        _orig = srv._induce_redraw_all
        srv._induce_redraw_all = lambda: (induced.append(1), _orig())[1]
        reader, writer = await ipc.open_connection(sock)
        await write_msg(writer, {"t": "hello", "proto": PROTO_VERSION,
                                 "cols": 80, "rows": 24, "token": srv.auth_token})
        # 초기 attach 프레임(layout/screen) 소비 — 새 프레임과 구분되게 잠시 비운다.
        for _ in range(20):
            m = await asyncio.wait_for(read_msg(reader), 2.0)
            if m and m.get("t") == "screen":
                break
        await write_msg(writer, {"t": "cmd", "action": "request_redraw"})
        got_layout = got_screen = False
        for _ in range(50):
            m = await asyncio.wait_for(read_msg(reader), 2.0)
            if not m:
                break
            if m.get("t") == "layout":
                got_layout = True
            elif m.get("t") == "screen":
                got_screen = True
            if got_layout and got_screen:
                break
        assert induced, "_induce_redraw_all 이 호출됐어야(앱 repaint 유발)"
        assert got_layout and got_screen, \
            f"전체 프레임 재전송(layout+screen)이 와야: layout={got_layout} screen={got_screen}"
        writer.close()
    finally:
        await teardown(srv, task, sock)


async def test_sync_output_defers_flush():
    """DEC 2026 동기화 출력(BSU/ESU): 프레임(?2026h…?2026l) 도중엔 _flush_loop 가 그
    패널 screen 을 클라에 안 보내(반쪽 프레임=무작위 글자 겹침 방지), ?2026l 후에
    완성 프레임을 보낸다. pytmux 가 2026 을 무시해 프레임 중간을 흘리던 게 'Claude
    화면 무작위 깨짐'의 근본원인이었다(tmux 는 2026 구현해 안 깨짐)."""
    import time as _time
    from pytmuxlib.protocol import write_msg, read_msg, PROTO_VERSION
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        reader, writer = await ipc.open_connection(sock)
        await write_msg(writer, {"t": "hello", "proto": PROTO_VERSION,
                                 "cols": 80, "rows": 24, "token": srv.auth_token})

        async def _collect(window):
            got = []
            end = _time.monotonic() + window
            while _time.monotonic() < end:
                try:
                    m = await asyncio.wait_for(
                        read_msg(reader), max(0.01, end - _time.monotonic()))
                except asyncio.TimeoutError:
                    break
                if m is None:
                    break
                got.append(m)
            return got

        # 초기 attach + 셸 프롬프트 프레임이 잦아들 때까지 비운다(정적 상태 확보).
        while await _collect(0.2):
            pass

        def _pane_screen(m):
            return (m.get("t") in ("screen", "screen-delta")
                    and m.get("pane") == p.id)

        # 프레임 시작(아직 ?2026l 없음) — 동기화 중. dirty 지만 송신은 미뤄져야 한다.
        srv._ingest_slice(p, b"\x1b[?2026h\x1b[Hhello-sync")
        assert p.sync_output is True
        assert p.dirty is True
        during = await _collect(0.12)   # 디퍼 창(0.15s) 안
        assert not any(_pane_screen(m) for m in during), \
            f"동기화 프레임 도중엔 screen 송신 금지: {[m.get('t') for m in during]}"

        # 프레임 끝 → 다음 flush 에 완성 프레임이 한 번에 온다.
        srv._ingest_slice(p, b" world\x1b[?2026l")
        assert p.sync_output is False
        after = await _collect(0.4)
        assert any(_pane_screen(m) for m in after), \
            "?2026l 후엔 완성 screen 프레임이 와야 한다"
        writer.close()
    finally:
        await teardown(srv, task, sock)


async def test_sync_output_defer_times_out():
    """안전망: ?2026l 이 안 와도(먹통 앱) SYNC_OUTPUT_MAX_DEFER 후엔 강제로 보낸다 —
    패널이 영구히 묶이지 않는다."""
    import time as _time
    from pytmuxlib.protocol import write_msg, read_msg, PROTO_VERSION, \
        SYNC_OUTPUT_MAX_DEFER
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        reader, writer = await ipc.open_connection(sock)
        await write_msg(writer, {"t": "hello", "proto": PROTO_VERSION,
                                 "cols": 80, "rows": 24, "token": srv.auth_token})

        async def _collect(window):
            got = []
            end = _time.monotonic() + window
            while _time.monotonic() < end:
                try:
                    m = await asyncio.wait_for(
                        read_msg(reader), max(0.01, end - _time.monotonic()))
                except asyncio.TimeoutError:
                    break
                if m is None:
                    break
                got.append(m)
            return got

        while await _collect(0.2):
            pass

        def _pane_screen(m):
            return (m.get("t") in ("screen", "screen-delta")
                    and m.get("pane") == p.id)

        # ?2026l 을 절대 안 보냄(먹통 앱 모사). 타임아웃 뒤엔 강제 송신돼야 한다.
        srv._ingest_slice(p, b"\x1b[?2026h\x1b[Hstuck-frame")
        assert p.sync_output is True
        late = await _collect(SYNC_OUTPUT_MAX_DEFER + 0.3)
        assert any(_pane_screen(m) for m in late), \
            "ESU 가 없어도 타임아웃 후엔 강제 송신(패널 영구 묶임 방지)"
        writer.close()
    finally:
        await teardown(srv, task, sock)


async def test_sync_output_active_feed_does_not_time_out():
    """무거운 스크롤 회귀: 한 동기화 프레임이 FEED_SLICE(8KB) 여러 조각으로
    SYNC_OUTPUT_MAX_DEFER 보다 오래 걸쳐 들어와도, 바이트가 계속 흐르는 한 _flush_loop
    는 반쪽 프레임을 보내면 안 된다. 디퍼 타임아웃은 '프레임 총 소요'가 아니라 '마지막
    바이트 이후 침묵'을 재야 한다(안 그러면 대형 프레임이 타임아웃을 넘겨 글자 겹침 송신
    — 로컬 패널만 깨지고 원격은 완성 프레임이 릴레이돼 멀쩡한 비대칭의 원인이었다).
    times_out 테스트와의 차이: 저긴 BSU 후 바이트가 끊겨(먹통 앱) 강제 송신이 정답."""
    import time as _time
    from pytmuxlib.protocol import write_msg, read_msg, PROTO_VERSION, \
        SYNC_OUTPUT_MAX_DEFER
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        reader, writer = await ipc.open_connection(sock)
        await write_msg(writer, {"t": "hello", "proto": PROTO_VERSION,
                                 "cols": 80, "rows": 24, "token": srv.auth_token})

        async def _collect(window):
            got = []
            end = _time.monotonic() + window
            while _time.monotonic() < end:
                try:
                    m = await asyncio.wait_for(
                        read_msg(reader), max(0.01, end - _time.monotonic()))
                except asyncio.TimeoutError:
                    break
                if m is None:
                    break
                got.append(m)
            return got

        while await _collect(0.2):
            pass

        def _pane_screen(m):
            return (m.get("t") in ("screen", "screen-delta")
                    and m.get("pane") == p.id)

        # 프레임 시작(BSU). 이후 ?2026 토글 없는 '중간' 슬라이스를 SYNC_OUTPUT_MAX_DEFER
        # 를 넘는 시간 동안 계속 먹인다(대형 프레임이 청크로 들어오는 상황 모사).
        srv._ingest_slice(p, b"\x1b[?2026h\x1b[Hheavy-scroll")
        assert p.sync_output is True
        sent_during = []
        step = SYNC_OUTPUT_MAX_DEFER / 3
        elapsed = 0.0
        while elapsed < SYNC_OUTPUT_MAX_DEFER * 2 + 0.1:
            srv._ingest_slice(p, b"row\r\n" * 50)   # 2026 토글 없음 = 프레임 도중
            assert p.sync_output is True
            sent_during += [m for m in await _collect(step) if _pane_screen(m)]
            elapsed += step
        assert not sent_during, \
            ("활성 피드 중엔 반쪽 프레임 송신 금지(디퍼 타임아웃 리셋): "
             f"{[m.get('t') for m in sent_during]}")

        # ESU → 다음 flush 에 완성 프레임이 와야 한다.
        srv._ingest_slice(p, b"done\x1b[?2026l")
        assert p.sync_output is False
        after = await _collect(0.4)
        assert any(_pane_screen(m) for m in after), \
            "?2026l 후엔 완성 screen 프레임이 와야 한다"
        writer.close()
    finally:
        await teardown(srv, task, sock)


async def test_inline_session_limit_reflected_in_5h_pct():
    """요청: /usage 패널을 안 열어도 footer 인라인 한도("used 93% of your session
    limit")를 캡처해 상태줄 5h% 가 실측 93% 를 따른다. S6 T3: 실측 전엔 None."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p._claude = "idle"
        assert srv._tok5h_pct(p, 1000) is None, "실측 전엔 None(근사 폐기)"
        p.feed("\x1b[2J\x1b[H"
               "You've used 93% of your session limit · resets 1:40pm "
               "(Asia/Seoul) · /usage-credits to request more\r\n"
               "? for shortcuts\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert isinstance(srv._usage, dict), srv._usage
        assert srv._usage["session"]["pct"] == 93, srv._usage
        p._claude = "idle"
        assert srv._tok5h_pct(p, 1000) == 93, "인라인 실측을 따른다"
    finally:
        await teardown(srv, task, sock)


async def test_claude_presence_debounce_no_flicker():
    """디바운스된 Claude 존재 신호 `_hdr_claude`(API 에러 게이트 등 소비): 원격
    (ssh/ConPTY) Claude 의 footer 가 한두 프레임 안 잡혀 raw `_claude` 가 None 으로
    깜빡여도 안정 신호는 안 흔들리고, _HDR_CLAUDE_MISS 프레임 연속 non-Claude 일
    때만 내려간다. (옛 헤더 행 예약의 떨림 방지 디바운스 — 헤더는 2026-06-13
    제거됐지만 신호는 다른 소비자가 그대로 쓴다.)"""
    import importlib
    _HDR_CLAUDE_MISS = importlib.import_module(
        "pytmuxlib.plugins.claude-code.servermixin")._HDR_CLAUDE_MISS
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # Claude idle footer → 디바운스 플래그 즉시 True
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._claude == "idle" and p._hdr_claude is True
        # footer 없는 중간 프레임이 한 번 와 raw 가 None 으로 깜빡여도 신호 유지
        p.feed(b"\x1b[2J\x1b[H(redrawing...)\r\n")
        srv._scan_claude(sess, win)
        assert p._claude is None, "raw 상태는 None 으로 깜빡"
        assert p._hdr_claude is True, "한 프레임 깜빡임에 신호가 흔들리면 안 됨"
        # 연속 _HDR_CLAUDE_MISS 프레임 non-Claude → 그제서야 해제
        for _ in range(_HDR_CLAUDE_MISS):
            srv._scan_claude(sess, win)
        assert p._hdr_claude is False
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_prompt_clear_queue_drains():
    """#4: 프롬프트 단위 클리어 큐. 명령을 쌓으면 모드가 자동으로 켜지고, 각 명령은
    doc+/clear 사이클을 마칠 때마다 하나씩 투입된다. 패널이 한가하면(idle, 진행 중
    시퀀스 없음) 곧장 첫 명령을 투입한다. 모드를 끄면 큐도 비운다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        injected = []
        srv._pc_inject = lambda pane, text: injected.append(text)

        def complete_response():
            p._claude = "busy"
            p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")  # idle footer
            srv._scan_claude(sess, win)

        # busy 중 큐 추가 → 모드 자동 on, 즉시 투입 안 하고 쌓아둠
        p._claude = "busy"
        assert srv.pc_queue_add(sess, "task A") == 1
        assert p.prompt_clear_mode is True
        assert injected == [], "busy 면 즉시 투입 안 함"
        srv.pc_queue_add(sess, "task B")
        assert p.prompt_clear_queue == ["task A", "task B"]

        # 현재 사용자 프롬프트 완료 → doc → /clear → 큐의 task A 투입(새 사이클)
        complete_response()
        assert injected == [srv.prompt_clear_message]
        complete_response()
        assert injected[-1] == "/clear"
        complete_response()
        assert injected[-1] == "task A", injected
        assert p.prompt_clear_queue == ["task B"] and p.last_prompt == "task A"

        # task A 도 사이클 → doc → /clear → task B 투입
        complete_response(); complete_response(); complete_response()
        assert injected[-1] == "task B"
        assert p.prompt_clear_queue == []

        # task B 사이클 후 큐가 비었으면 시퀀스 종료(추가 투입 없음)
        n = len(injected)
        complete_response(); complete_response(); complete_response()
        assert p._pc_phase is None
        assert injected[n:] == [srv.prompt_clear_message, "/clear"], injected[n:]

        # idle 패널에 큐 추가 → 즉시 첫 명령 드레인
        srv.set_prompt_clear(sess, False)
        injected.clear()
        p._claude = "idle"
        srv.pc_queue_add(sess, "now")
        assert injected == ["now"], "idle+phase None 이면 즉시 투입"
        # 모드 끄면 남은 큐도 비운다
        p.prompt_clear_queue.append("leftover")
        srv.set_prompt_clear(sess, False)
        assert p.prompt_clear_queue == []
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_queued_prompt_header_defers():
    # #4: busy 중 입력한 프롬프트는 헤더(last_prompt)를 즉시 안 바꾸고 큐에 쌓였다가
    # 응답 경계(busy→idle)에 순서대로 승격된다.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # idle 에서 A 제출 → 즉시 헤더 확정
        srv._track_prompt(p, b"prompt A\r")
        assert p.last_prompt == "prompt A"
        # busy 진입
        p.feed("\x1b[2J\x1b[H✽ Crunching… (5s · ↓ 0.5k tokens)\r\n".encode())
        srv._scan_claude(sess, win)
        assert p._claude == "busy"
        # busy 중 B, C 제출 → 큐잉, 헤더는 A 유지
        srv._track_prompt(p, b"prompt B\r")
        srv._track_prompt(p, b"prompt C\r")
        assert p.last_prompt == "prompt A", "busy 중 새 프롬프트는 헤더 안 바꿈"
        assert p.pending_prompts == ["prompt B", "prompt C"]
        # 응답 종료(busy→idle): §3.4 깜빡임 흡수 — 첫 idle 프레임엔 승격하지 않고
        # 연속 2프레임 idle 에 확정 승격(B). 사이에 busy 로 복귀하면 승격 없음.
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p.last_prompt == "prompt A", "첫 idle 프레임은 미승격(깜빡임 흡수)"
        srv._scan_claude(sess, win)        # 연속 2프레임 idle → 경계 확정
        assert p.last_prompt == "prompt B", p.last_prompt
        assert p.pending_prompts == ["prompt C"]
        # 깜빡임 시나리오: busy 중 한 프레임 idle 로 보였다가 busy 복귀 → 승격 없음
        p.feed("\x1b[2J\x1b[H✽ Baking… (3s · ↓ 0.3k tokens)\r\n".encode())
        srv._scan_claude(sess, win)
        assert p.last_prompt == "prompt B", "B 처리 중엔 B 유지"
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")   # 리페인트 한 프레임 idle
        srv._scan_claude(sess, win)
        p.feed("\x1b[2J\x1b[H✽ Baking… (5s · ↓ 0.4k tokens)\r\n".encode())
        srv._scan_claude(sess, win)                   # busy 복귀 → 라치 해제
        assert p.last_prompt == "prompt B", "깜빡임으로는 승격 안 함"
        assert p.pending_prompts == ["prompt C"]
        # 진짜 종료(연속 2프레임 idle) → C 승격
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        srv._scan_claude(sess, win)
        assert p.last_prompt == "prompt C" and p.pending_prompts == []
    finally:
        await teardown(srv, task, sock)


async def test_session_tokens_accumulate():
    # 토큰 누계(#3): busy footer 의 running 토큰을 응답별 peak 로 합산, 세션 종료 시 리셋.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 응답1: 0.5k → 1.9k 스트리밍(busy) 후 idle
        p.feed("\x1b[2J\x1b[H✽ Crunching… (5s · ↓ 0.5k tokens)\r\n".encode())
        srv._scan_claude(sess, win)
        assert p._claude == "busy", p._claude
        p.feed("\x1b[2J\x1b[H✽ Crunching… (9s · ↓ 1.9k tokens)\r\n".encode())
        srv._scan_claude(sess, win)
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        srv._scan_claude(sess, win)   # §3.4 busy 이탈 히스테리시스(2프레임) 확정
        assert p._session_tokens == 1900, p._session_tokens
        # 응답2: 2.5k 까지 → idle (누계 4400)
        p.feed("\x1b[2J\x1b[H✽ Baking… (4s · ↓ 2.5k tokens)\r\n".encode())
        srv._scan_claude(sess, win)
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        srv._scan_claude(sess, win)   # 히스테리시스 확정
        assert p._session_tokens == 4400, p._session_tokens
        # Claude 세션 종료 → 누계 리셋
        p.feed(b"\x1b[2J\x1b[Huser@host ~ % ls\r\n")
        srv._scan_claude(sess, win)
        assert p._session_tokens == 0, "세션 끝 → 누계 리셋"
    finally:
        await teardown(srv, task, sock)


async def test_break_join_pane():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        n = len(sess.tabs)
        srv.break_pane(sess)
        assert len(sess.tabs) == n + 1, "break → 새 윈도우"
        srv.join_pane(sess)
        for p in sess.active_window.panes():
            assert p.parent is None or p in (p.parent.a, p.parent.b)
    finally:
        await teardown(srv, task, sock)


async def test_move_pane_to_tab():
    # #1 헤더 드래그 pick-up → 다른 탭에 드롭: 활성 윈도우의 패널을 지정한 탭으로
    # 옮긴다(대상 윈도우 활성 패널과 분할로 합침). 소스가 유일 패널이던 탭은 사라진다.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)              # 탭 1개 추가 → 탭 0, 탭 1
        assert len(sess.tabs) == 2
        sess.active_index = 0             # 탭 0 활성
        win0 = sess.tabs[0].window
        srv.split_pane(sess, "lr")        # 탭 0 에 패널 2개
        moved = win0.active_pane
        moved_id = moved.id
        # 탭 0 의 활성 패널을 탭 1 로 이동
        ok = srv.move_pane_to_tab(sess, moved_id, 1)
        assert ok is True
        # 탭 수는 그대로(소스 탭 0 에 아직 패널 1개 남음 — 제거 안 됨)
        assert len(sess.tabs) == 2
        # 이동한 패널은 이제 탭 1 윈도우에 있고, 그 윈도우 활성 패널이다
        win1 = next(t.window for t in sess.tabs if t.index == 1)
        assert moved in win1.panes() and win1.active_pane is moved
        assert moved not in sess.tabs[0].window.panes()
        # 대상 탭이 활성이 됐다
        assert sess.active_window is win1

        # 유일 패널을 옮기면 소스 탭이 사라진다(break 의 반대)
        n = len(sess.tabs)
        lone = sess.active_window.active_pane   # win1 의 (방금 옮겨온) 패널 등
        # win1 이 단일 패널이 되도록: 위에서 win1 엔 원래 패널 + 옮겨온 패널 2개.
        # 단일-패널 탭을 만들기 위해 새 탭을 하나 더 만들고 그 유일 패널을 옮긴다.
        srv.new_window(sess)                    # 새 단일-패널 탭(끝 인덱스)
        src_idx = len(sess.tabs) - 1
        sess.active_index = src_idx
        lone_id = sess.active_window.active_pane.id
        ok2 = srv.move_pane_to_tab(sess, lone_id, 0)   # 탭 0 으로 이동
        assert ok2 is True
        assert len(sess.tabs) == n, "유일 패널 이동 → 소스 탭 제거"

        # 같은 탭으로의 이동은 무동작(False)
        cur = sess.active_index
        assert srv.move_pane_to_tab(
            sess, sess.active_window.active_pane.id, cur) is False
    finally:
        await teardown(srv, task, sock)


async def test_pane_master_fd_cloexec():
    """새 패널의 PTY master 에 FD_CLOEXEC 가 걸려, 이후 만들어지는 패널의 자식 셸이
    형제 패널 fd 를 상속하지 않는다(패널 간 출력 섞임 방지)."""
    if ipc.IS_WINDOWS:
        return  # ConPTY 는 fd 가 아니라 핸들 기반이라 FD_CLOEXEC 개념이 없음(POSIX 전용)
    import fcntl
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")
        srv.new_window(sess)
        all_panes = [p for t in sess.tabs for p in t.window.panes()]
        assert len(all_panes) >= 3
        for p in all_panes:
            flags = fcntl.fcntl(p.master_fd, fcntl.F_GETFD)
            assert flags & fcntl.FD_CLOEXEC, f"pane {p.id} master fd 에 CLOEXEC 없음"
    finally:
        await teardown(srv, task, sock)


async def test_last_pane_and_window():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")
        p2 = win.active_pane.id
        p1 = [p.id for p in win.panes() if p.id != p2][0]
        win.active_pane = win.pane_by_id(p1)
        srv.last_pane(sess)
        assert win.active_pane.id == p2
        srv.new_window(sess)
        srv.new_window(sess)
        srv.select_window(sess, 0)
        srv.last_window(sess)
        assert sess.active_index == 2
    finally:
        await teardown(srv, task, sock)


async def test_window_move_swap_rename():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.rename_window(sess, "w0")
        srv.new_window(sess)
        srv.rename_window(sess, "w1")
        srv.new_window(sess)
        srv.rename_window(sess, "w2")
        srv.move_window(sess, 0)
        assert [t.name for t in sess.tabs] == ["w2", "w0", "w1"]
        assert [t.index for t in sess.tabs] == [0, 1, 2]
        srv.swap_window(sess, 2)
        assert [t.name for t in sess.tabs] == ["w1", "w0", "w2"]
    finally:
        await teardown(srv, task, sock)


async def test_single_session_enforced():
    """단일 세션 모델: 이름을 줘도 항상 같은 하나의 세션에 attach 한다."""
    srv, task, sock = await server_only()
    try:
        s1 = srv.ensure_default_session(80, 24)
        s2 = srv.get_or_create_session("brandnew", 80, 24)
        s3 = srv.get_or_create_session("other", 80, 24)
        assert s1 is s2 is s3, "세션 이름 요청은 무시되고 단일 세션"
        assert len(srv.sessions) == 1
    finally:
        await teardown(srv, task, sock)


async def test_sync_input_broadcast():
    # 2026-06-10 근본원인 규명 — 이 테스트가 Windows 에서 "실패"하던 건 **제품 버그도, 리더
    # 레이스도, 누적 오염도 아니라 이 테스트의 검색 방식 결함**이었다(과거 가설 전부 오진).
    # 80폭 세션을 lr 분할하면 패널 폭이 38/39 가 되는데, 테스트 cwd 프롬프트
    # `D:\p4\office\scripts\pytmux>`(27자) + 입력 `echo SYNCED`(11자) = 정확히 38자다.
    # 폭 38 패널에서는 그 입력 에코가 38칸을 꽉 채우고 마지막 'D' 가 **다음 행으로 하드랩**
    # 되어 화면 텍스트가 `...SYNCE\nD...` 가 된다 → `"SYNCED" in pane_text` 부분문자열 검색이
    # 줄바꿈에 걸려 실패한다(폭 39 는 한 줄에 들어가 통과). 즉 cwd 길이에 따라 갈리는
    # 검색 아티팩트였고("간헐"·"단독은 통과"로 보이던 이유), 제품은 프롬프트·에코를 정상
    # 렌더한다. **수정: 검색 전에 줄바꿈을 제거해 하드랩을 펼친다**(`replace("\n","")`).
    # 이러면 폭과 무관하게 동작하므로 Windows skip 도 제거한다(Unix·Windows 공통 검증).
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.split_pane(sess, "lr")
        srv._layout_msg(sess)            # 패널 크기 반영
        srv.set_sync(sess, True)
        assert win.sync
        # 동기화가 켜진 윈도우의 모든 패널에 동일 입력이 전달되는지 확인.
        # master_fd 직접 os.write 는 Windows(ConPTY, fd 없음)에서 깨지므로
        # 백엔드 추상화(pty.write)로 쓴다.
        data = b"echo SYNCED\n"
        for t in win.panes():
            t.pty.write(data)

        # 하드랩(좁은 패널에서 에코가 줄바꿈으로 쪼개짐)을 펼쳐 검색 — 행 경계가 'SYNCED'
        # 를 가르지 않게 한다. 셸(Windows=cmd.exe)이 에코를 출력하기까지 시간이 들쭉날쭉
        # 하므로 모든 패널에 보일 때까지 최대 ~10s 폴링.
        def _has_synced(p):
            return "SYNCED" in pane_text(p).replace("\n", "")
        for _ in range(100):
            await asyncio.sleep(0.1)
            if all(_has_synced(p) for p in win.panes()):
                break
        assert all(_has_synced(p) for p in win.panes())
    finally:
        await teardown(srv, task, sock)


async def test_mouse_mode_tracking_and_passthrough():
    import base64
    from pytmuxlib.model import ClientConn
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 내부 앱이 DECSET 1002+1006 을 켜면 추적되고 레이아웃에 노출된다
        changed = p.update_mouse_modes(b"\x1b[?1002h\x1b[?1006h")
        assert changed and p.mouse_track == 2 and p.mouse_sgr is True
        lay = srv._layout_msg(sess)
        pm = next(m for m in lay["panes"] if m["id"] == p.id)
        assert pm["mouse"] == 2 and pm["mouse_sgr"] is True
        # 끄면 0 으로 복귀
        p.update_mouse_modes(b"\x1b[?1002l\x1b[?1006l")
        assert p.mouse_track == 0 and p.mouse_sgr is False

        # mouse 플래그 입력은 대상 패널만, 프롬프트 추적/동기화 제외
        srv.split_pane(sess, "lr")
        srv._layout_msg(sess)
        srv.set_sync(sess, True)        # 동기화 ON 이어도 마우스는 브로드캐스트 안 함
        target = win.panes()[0]
        client = ClientConn(None)
        client.session = sess
        seq = b"\x1b[<0;3;4M"
        srv._handle_input(client, {"pane": target.id, "mouse": True,
                                   "data": base64.b64encode(seq).decode()})
        await asyncio.sleep(0.2)
        # 마우스 경로는 _track_prompt 를 거치지 않으므로 입력 누적이 없어야 함
        assert target._inbuf == "" and target.last_prompt == ""
    finally:
        await teardown(srv, task, sock)


async def test_win_mouse_motion_cap_in_layout_and_persist():
    """HANDOFF §10-H — Windows ConPTY 마우스 모션 누출 수정.

    근거(캡처 captures/.../20260622_093650_0_1.win_p3.log): office1 Windows 패널에서
    앱(Claude)이 `ESC[?1003h`(any-motion ON)를 누출 구간 내내 계속 재emit 하는데도,
    우리가 패널 입력으로 **주입**한 SGR 모션 리포트(`ESC[<35;…M`, 168건·전부 ESC
    선행 0)가 프롬프트에 **텍스트로 박혔다** → 'stale 플래그(앱 OFF인데 우리만 ON)'
    가설 H2 반증, ConPTY 가 주입 any-motion 을 소비 못 하는 지속성 버그. 그래서
    Windows 에서는 광고 mouse 를 drag(2)로 캡해 any-motion 을 아예 안 흘린다. 클릭/
    드래그(1000/1002)는 누출 증거 없어 유지. win_mouse_motion 옵션으로 복구 가능."""
    from pytmuxlib import serverio
    srv, task, sock = await server_only()
    saved = serverio.pty_backend.IS_WINDOWS
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane

        def adv():   # 현재 _layout_msg 가 이 패널에 광고하는 mouse 레벨
            lay = srv._layout_msg(sess)
            return next(m for m in lay["panes"] if m["id"] == p.id)

        # 앱이 any-motion(1003) + SGR(1006) 을 켠다
        p.update_mouse_modes(b"\x1b[?1003h\x1b[?1006h")
        assert p.mouse_track == 3 and p.mouse_sgr is True

        # 비-Windows: 캡 없음(커널 PTY 라 주입 모션이 정상 소비됨)
        serverio.pty_backend.IS_WINDOWS = False
        assert adv()["mouse"] == 3, "비-Windows 는 any-motion 그대로 광고"

        # Windows + 기본(off): any-motion → drag(2) 로 캡, SGR 인코딩은 유지
        serverio.pty_backend.IS_WINDOWS = True
        srv.win_mouse_motion = False
        m = adv()
        assert m["mouse"] == 2 and m["mouse_sgr"] is True, \
            "Windows any-motion 은 drag 로 캡(SGR 유지)"

        # 클릭/드래그(1002)는 Windows 에서도 캡 안 됨(누출 증거 없음)
        p.update_mouse_modes(b"\x1b[?1002h\x1b[?1003l")
        assert p.mouse_track == 2
        assert adv()["mouse"] == 2, "drag 는 Windows 도 그대로"

        # 옵션 ON 이면 Windows 도 종전대로 any-motion 광고(복구 경로)
        p.update_mouse_modes(b"\x1b[?1003h")
        assert p.mouse_track == 3
        assert srv.set_win_mouse_motion(True) is True
        assert adv()["mouse"] == 3, "win_mouse_motion ON 이면 캡 해제"

        # opts.json 영속 + 재시작 round-trip(_load_opts↔_save_opts 짝맞춤)
        assert json.load(open(srv.opts_path))["win_mouse_motion"] is True
        assert pytmux.Server(sock).win_mouse_motion is True
        assert srv.set_win_mouse_motion(None) is False   # 토글 → 기본 OFF
        assert json.load(open(srv.opts_path))["win_mouse_motion"] is False
    finally:
        serverio.pty_backend.IS_WINDOWS = saved
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_search_buffer_capture_clear():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        for i in range(50):
            p.feed((f"L{i} NEEDLE\r\n" if i == 10 else f"L{i}\r\n").encode())
        srv.search_pane(sess, "NEEDLE", "up")
        assert p._match_abs == 10, "검색 매치"
        srv.set_buffer("BUF1")
        assert srv.buffers[0] == "BUF1"
        p.feed(b"CAPME\r\n")
        srv.capture_pane(sess)
        assert "CAPME" in srv.buffers[0], "캡처"
        srv.clear_history(sess)
        assert len(p.screen.history.top) == 0, "히스토리 비움"
    finally:
        await teardown(srv, task, sock)


async def test_layout_persistence():
    srv, task, sock = await server_only()
    srv2, task2, sock2 = await server_only()
    try:
        sess = srv.new_session(80, 24, "work")
        srv.rename_window(sess, "editor")
        srv.split_pane(sess, "lr")
        srv.split_pane(sess, "tb")
        srv.new_window(sess)
        srv.rename_window(sess, "logs")
        import tempfile
        lp = tempfile.mktemp(suffix=".json")
        assert srv.save_layout(lp)
        struct = [(s.name, [(t.name, len(t.window.panes())) for t in s.tabs])
                  for s in srv.sessions.values()]
        assert srv2.restore_layout(lp)
        struct2 = [(s.name, [(t.name, len(t.window.panes())) for t in s.tabs])
                   for s in srv2.sessions.values()]
        assert struct2 == struct, (struct, struct2)
    finally:
        await teardown(srv, task, sock)
        await teardown(srv2, task2, sock2)


async def test_handle_control():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        assert srv.handle_control("new-window") == "ok"
        assert len(sess.tabs) == 2
        srv.handle_control("split-window -h")
        assert len(sess.active_window.panes()) == 2
        srv.handle_control("rename-window CTRL")
        assert sess.active_tab.name == "CTRL"
        assert srv.handle_control("bogus").startswith("unknown")
    finally:
        await teardown(srv, task, sock)


async def test_fire_resume_rechecks_limit_state():
    """_fire_resume 는 발화 직전 화면이 여전히 limit 일 때만 'continue' 를 주입한다(#6).
    예약~발화 사이에 사용자가 재개했거나(화면이 limit 아님) parse_reset_delay 오탐이면
    주입을 건너뛰어 작업 중인 Claude 에 끼어들지 않는다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p.resume_msg = "continue"
        real = p.pty
        writes = []

        class _Spy:
            def write(self, b):
                writes.append(b)
        try:
            p.pty = _Spy()
            # ① 화면이 limit 아님(셸 프롬프트) → 주입 안 함
            p.feed(b"\x1b[2J\x1b[H$ ls -la\r\n")
            srv._fire_resume(p)
            assert writes == [], "limit 아니면 주입 안 함"
            # ② 화면이 limit → continue 주입
            p.feed(b"\x1b[2J\x1b[Husage limit reached, resets at 3pm\r\n")
            srv._fire_resume(p)
            assert writes and b"continue" in writes[0], "limit 이면 continue 주입"
        finally:
            p.pty = real
    finally:
        await teardown(srv, task, sock)


async def test_clear_resets_token_session():
    """/clear 자동 주입(_pc_advance doc→clear) 시 토큰 누계가 새 세션으로 끊긴다(#5).
    절감 자동화가 돌수록 doc/clear 토큰이 사용자 누계에 합산되던 구조적 오차를 막는다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p._tok_state = {"peak": 0, "total": 5000}
        p._session_tokens = 5000
        sid0 = p._claude_session_id
        p._pc_phase = "doc"               # doc 응답 완료 → /clear 주입 단계
        srv._pc_advance(p)
        assert p._tok_state["total"] == 0 and p._session_tokens == 0, "누계 리셋"
        assert p._claude_session_id != sid0, "새 세션 id 부여"
    finally:
        await teardown(srv, task, sock)


async def test_proto_version_negotiation():
    """와이어 프로토콜 버전 협상(#7): 비호환 proto 는 명확히 거절, proto 없는(구버전)
    클라는 호환으로 통과. 버전 스큐 시 조용한 오작동 대신 명시적 실패가 되게 한다."""
    from pytmuxlib.protocol import write_msg, read_msg, PROTO_VERSION
    srv, task, sock = await server_only()
    try:
        # ① 비호환 proto → proto_mismatch 에러 + 연결 종료 + 클라 미등록
        reader, writer = await ipc.open_connection(sock)
        await write_msg(writer, {"t": "hello", "proto": PROTO_VERSION + 999,
                                 "cols": 80, "rows": 24})
        resp = await read_msg(reader)
        assert resp and resp.get("error") == "proto_mismatch", resp
        assert resp.get("server_proto") == PROTO_VERSION
        assert (await read_msg(reader)) is None         # 거절 후 연결 닫힘
        assert len(srv.clients) == 0                     # 등록 안 됨
        writer.close()

        # ② proto 없는 구버전 클라 → 호환으로 통과(첫 프레임 _send_full 수신)
        reader2, writer2 = await ipc.open_connection(sock)
        await write_msg(writer2, {"t": "hello", "cols": 80, "rows": 24,
                                  "token": srv.auth_token})
        assert (await read_msg(reader2)) is not None     # 레이아웃/화면 수신 = 수락
        writer2.close()
    finally:
        await teardown(srv, task, sock)


# ── 연결 인증 토큰(F1, docs/internal/SECURITY_REVIEW.md) ────────────────────────────────
async def test_auth_token_required_and_published():
    """서버는 listen 전에 0600 토큰 파일을 게시하고, 올바른 토큰만 수락한다.
    무인가 로컬 주체(특히 Windows TCP 루프백)의 접속을 차단한다."""
    import os
    from pytmuxlib.protocol import write_msg, read_msg
    srv, task, sock = await server_only()
    try:
        # 서버가 토큰을 발급했고, 파일에서 읽은 값과 일치한다.
        assert srv.auth_token and ipc.read_token(sock) == srv.auth_token
        # 토큰 파일은 0600(소유자만 읽기) — TCP 루프백에서도 같은 UID 만 토큰을 얻는다.
        if not ipc.IS_WINDOWS:
            mode = os.stat(ipc.token_path(sock)).st_mode & 0o777
            assert mode == 0o600, oct(mode)

        # ① 토큰 없음 → auth_failed + 연결 종료 + 클라 미등록
        r0, w0 = await ipc.open_connection(sock)
        await write_msg(w0, {"t": "hello", "cols": 80, "rows": 24})
        reply = await read_msg(r0)
        assert reply == {"t": "error", "error": "auth_failed"}, reply
        assert (await read_msg(r0)) is None        # 서버가 연결을 끊음
        w0.close()

        # ② 틀린 토큰 → auth_failed
        r1, w1 = await ipc.open_connection(sock)
        await write_msg(w1, {"t": "hello", "cols": 80, "rows": 24, "token": "nope"})
        assert (await read_msg(r1)) == {"t": "error", "error": "auth_failed"}
        w1.close()

        # ③ 올바른 토큰 → 수락(레이아웃/화면 수신), 클라 등록
        r2, w2 = await ipc.open_connection(sock)
        await write_msg(w2, {"t": "hello", "cols": 80, "rows": 24,
                             "token": srv.auth_token})
        assert (await read_msg(r2)) is not None
        w2.close()
    finally:
        await teardown(srv, task, sock)


async def test_control_requires_auth_token():
    """control 채널(send-keys/kill 등)도 토큰 없이는 거절된다(무인증 kill 방지)."""
    from pytmuxlib.protocol import write_msg, read_msg
    srv, task, sock = await server_only()
    try:
        r, w = await ipc.open_connection(sock)
        await write_msg(w, {"t": "control", "line": "kill-server"})
        assert (await read_msg(r)) == {"t": "error", "error": "auth_failed"}
        w.close()
        assert srv.running, "무인가 control 로 서버가 종료되면 안 됨"
    finally:
        await teardown(srv, task, sock)


async def test_peer_uid_over_unix_socket():
    """ipc.peer_uid 가 AF_UNIX 상대 UID 를 읽는다(F2 심층 방어). None 입력은 None."""
    import os
    import socket as _s
    if ipc.IS_WINDOWS:
        return
    a, b = _s.socketpair(_s.AF_UNIX, _s.SOCK_STREAM)
    try:
        assert ipc.peer_uid(a) == os.getuid()
        assert ipc.peer_uid(b) == os.getuid()
    finally:
        a.close()
        b.close()
    assert ipc.peer_uid(None) is None


async def test_validate_state_dir_rejects_symlink():
    """상태 디렉터리 검증(F3): 심볼릭 링크 거부, 정상 디렉터리는 통과.

    타 UID 소유 거부는 root 없이는 재현 불가하므로 심링크 거부로 대표 검증한다(둘 다
    lstat 기반 같은 메커니즘 — 공격자 선점 심링크·디렉터리 모두 소유자 불일치로 잡힘)."""
    import os
    import tempfile
    if ipc.IS_WINDOWS:
        return
    d = tempfile.mkdtemp(prefix="pytmux-sd-")
    real = os.path.join(d, "real")
    os.mkdir(real)
    ipc._validate_state_dir(real)            # 정상(비링크·자기소유) → 통과
    link = os.path.join(d, "link")
    os.symlink(real, link)
    try:
        ipc._validate_state_dir(link)
        assert False, "심볼릭 링크 상태 디렉터리가 거부되지 않음"
    except RuntimeError:
        pass


async def test_private_files_are_0600():
    """민감 영속·캡처 파일은 0600, 캡처 디렉터리는 0700 으로 생성된다(F4/F5).

    캡처 raw 로그에는 표시·에코된 비밀번호·토큰이 남을 수 있어 같은 머신의 다른 로컬
    사용자가 못 읽게 해야 한다. opts/resume/slots/layout 도 화면 스냅샷 등 민감정보를 담는다."""
    import os
    import tempfile
    if ipc.IS_WINDOWS:
        return
    # open_private: 생성 시점부터 0600
    p = tempfile.mktemp(prefix="pytmux-priv-")
    with ipc.open_private(p) as f:
        f.write("x")
    assert (os.stat(p).st_mode & 0o777) == 0o600, oct(os.stat(p).st_mode)
    os.unlink(p)

    srv, task, sock = await server_only()
    try:
        srv._save_opts()
        assert (os.stat(srv.opts_path).st_mode & 0o777) == 0o600
        # 캡처: 패널 출력 한 조각 기록 후 파일/디렉터리 권한 확인
        srv.capture = True
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        srv._capture_write(pane, b"secret-output\n")
        capfile = srv._cappaths[pane.id]
        assert (os.stat(capfile).st_mode & 0o777) == 0o600
        assert (os.stat(srv.capture_dir).st_mode & 0o777) == 0o700
    finally:
        await teardown(srv, task, sock)


async def test_clamp_dim_bounds():
    """치수 필드 검증(F6): 상·하한 적용, 정수로 못 바꾸면 default."""
    from pytmuxlib.protocol import clamp_dim, MIN_W, MAX_W
    assert clamp_dim(99999, MIN_W, MAX_W, 80) == MAX_W      # 거대값 → 상한
    assert clamp_dim(1, MIN_W, MAX_W, 80) == MIN_W          # 작은값 → 하한
    assert clamp_dim(-5, MIN_W, MAX_W, 80) == MIN_W         # 음수 → 하한
    assert clamp_dim("nope", MIN_W, MAX_W, 80) == 80        # 비정수 → default
    assert clamp_dim(None, MIN_W, MAX_W, 80) == 80
    assert clamp_dim(100, MIN_W, MAX_W, 80) == 100          # 정상 통과


async def test_bad_base64_input_is_ignored():
    """손상된 base64 input 은 그 입력만 무시하고 서버·연결은 살아남는다(F6)."""
    from pytmuxlib.protocol import write_msg
    srv, task, sock = await server_only()
    try:
        srv.ensure_default_session(80, 24)
        r, w = await ipc.open_connection(sock)
        await write_msg(w, {"t": "hello", "cols": 80, "rows": 24,
                            "token": srv.auth_token})
        await harness.drain(r, [], timeout=1)
        await write_msg(w, {"t": "input", "data": "!!!not base64!!!"})
        await write_msg(w, {"t": "ping", "ts": 1})
        got = []
        await harness.drain(r, got, timeout=3,
                            until=lambda s: any(m.get("t") == "pong" for m in s))
        assert any(m.get("t") == "pong" for m in got), "서버가 살아 pong 응답해야"
        assert srv.running
        w.close()
    finally:
        await teardown(srv, task, sock)


async def test_split_window_orientation_matches_tmux():
    """split-window -h = 좌우(lr), -v/기본 = 상하(tb) — tmux 규약 정합(회귀 가드).

    과거엔 -h→상하로 반전돼 prefix %/" · join-pane -h 와 어긋났다(키↔명령 경로 비대칭).
    서버 외부제어 경로(handle_control)와 join-pane 규약이 일치하는지 못박는다."""
    srv, task, sock = await server_only()
    try:
        # -h → 좌우(lr)
        sess = srv.ensure_default_session(80, 24)
        srv.handle_control("split-window -h")
        assert sess.active_window.root.orient == "lr", "split -h 는 좌우(lr)"
        # 새 탭에서 -v → 상하(tb)
        srv.handle_control("new-window")
        srv.handle_control("split-window -v")
        assert sess.active_window.root.orient == "tb", "split -v 는 상하(tb)"
        # 기본(플래그 없음) → 상하(tb)
        srv.handle_control("new-window")
        srv.handle_control("split-window")
        assert sess.active_window.root.orient == "tb", "split 기본은 상하(tb)"
    finally:
        await teardown(srv, task, sock)


async def test_tab_hierarchy_and_commands():
    """최상위 Tab → 단일 Window → 패널 집합 구조 및 탭 명령(new/kill/rename)."""
    from pytmuxlib.model import Tab, Window
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        assert isinstance(sess.tabs[0], Tab), "최상위는 Tab"
        assert isinstance(sess.active_tab.window, Window), "탭에 종속된 단일 윈도우"
        assert sess.active_tab.window is sess.active_window, "compat 프로퍼티"
        # 새 탭 = 새 윈도우(단일 패널)
        assert srv.handle_control("new-tab") == "ok"
        assert len(sess.tabs) == 2
        assert len(sess.active_tab.window.panes()) == 1
        # 탭의 윈도우를 패널로 분할
        srv.split_pane(sess, "lr")
        assert len(sess.active_tab.window.panes()) == 2
        # 탭 이름 변경
        srv.handle_control("rename-tab MYTAB")
        assert sess.active_tab.name == "MYTAB"
        # 탭 삭제
        srv.handle_control("kill-tab")
        assert len(sess.tabs) == 1
    finally:
        await teardown(srv, task, sock)


async def test_resize_rescales_panes():
    """터미널 리사이즈 시 패널이 비율대로 다시 계산된다."""
    import pytmux
    srv, task, sock = await server_only()
    try:
        r, w = await ipc.open_connection(sock)
        await pytmux.write_msg(w, {"t": "hello", "cols": 100, "rows": 40,
                                   "token": srv.auth_token})
        s = []
        await harness.drain(r, s)
        sess = next(iter(srv.sessions.values()))
        srv.split_pane(sess, "lr")
        await pytmux.write_msg(w, {"t": "resize", "cols": 100, "rows": 40})
        s = []
        await harness.drain(r, s)
        big = [m for m in s if m["t"] == "layout"][-1]
        wbig = max(p["w"] for p in big["panes"])
        await pytmux.write_msg(w, {"t": "resize", "cols": 50, "rows": 40})
        s = []
        await harness.drain(r, s)
        small = [m for m in s if m["t"] == "layout"][-1]
        wsmall = max(p["w"] for p in small["panes"])
        assert small["cols"] == 50 and wsmall < wbig, (wsmall, wbig)
        w.close()
    finally:
        await teardown(srv, task, sock)


async def test_claude_prompt_tracking():
    """입력에서 마지막 프롬프트 추적(백스페이스/CSI/붙여넣기) + 탭 상태 집계."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        srv._track_prompt(p, b"abc\x7f\r")            # abc + backspace → ab
        assert p.last_prompt == "ab", p.last_prompt
        srv._track_prompt(p, b"\x1b[200~pasted\x1b[201~\r")  # bracketed paste 본문
        assert p.last_prompt == "pasted", p.last_prompt
        srv._track_prompt(p, b"\x1b[Dmid\r")          # 화살표(CSI)는 건너뜀
        assert p.last_prompt == "mid", p.last_prompt
        # 붙여넣기(모바일 받아쓰기/자동완성 포함)도 추적되어야 함:
        # paste_text 로 본문 입력 후 별도 Enter(\r) 로 확정 → last_prompt 갱신.
        # (이 경로가 빠지면 헤더가 셸 실행 명령에 머문다)
        srv.paste_text(sess, "fix the header")
        assert p.last_prompt == "mid", "Enter 전엔 미확정"
        srv._track_prompt(p, b"\r")
        assert p.last_prompt == "fix the header", p.last_prompt
        # 탭 Claude 집계(limit > busy > idle)
        p._claude = "idle"
        assert srv._tab_claude(sess.active_tab) == "idle"
        p._claude = "limit"
        assert srv._tab_claude(sess.active_tab) == "limit"
    finally:
        await teardown(srv, task, sock)


async def test_tab_reorder():
    """탭 재정렬: move_current_tab(좌/우/맨앞/맨뒤) + move_tab(임의), 활성 추적."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        for nm in ["a", "b", "c"]:
            srv.new_window(sess)
            srv.rename_window(sess, nm)
        assert [t.name for t in sess.tabs] == ["win", "a", "b", "c"]
        assert sess.active_tab.name == "c"
        srv.move_current_tab(sess, "first")
        assert [t.name for t in sess.tabs] == ["c", "win", "a", "b"]
        assert sess.active_tab.name == "c", "활성 탭 추적"
        srv.move_current_tab(sess, "last")
        assert [t.name for t in sess.tabs] == ["win", "a", "b", "c"]
        srv.move_current_tab(sess, "left")
        assert [t.name for t in sess.tabs] == ["win", "a", "c", "b"]
        # 임의 탭 이동(활성 c 는 위치 유지 추적)
        srv.move_tab(sess, 0, 3)
        assert [t.name for t in sess.tabs] == ["a", "c", "b", "win"]
        assert sess.active_tab.name == "c"
    finally:
        await teardown(srv, task, sock)


async def test_per_tab_layout_save_load():
    """활성 탭 레이아웃을 이름 슬롯에 저장 → 새 탭/현재 탭 덮어쓰기로 불러오기."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        srv.split_pane(sess, "tb")
        assert len(sess.active_tab.window.panes()) == 3
        assert srv.save_tab_layout(sess, "three")
        assert "three" in srv.list_tab_layouts()
        # 새 탭으로 불러오기
        assert srv.load_tab_layout(sess, "three", new_tab=True)
        assert len(sess.tabs) == 2
        assert len(sess.active_tab.window.panes()) == 3
        # 단일 패널 탭 만든 뒤 현재 탭 덮어쓰기
        srv.new_window(sess)
        assert len(sess.tabs) == 3
        assert len(sess.active_tab.window.panes()) == 1
        assert srv.load_tab_layout(sess, "three", new_tab=False)
        assert len(sess.tabs) == 3, "덮어쓰기는 탭 수 불변"
        assert len(sess.active_tab.window.panes()) == 3
        # 없는 슬롯
        assert srv.load_tab_layout(sess, "nope") is False
    finally:
        await teardown(srv, task, sock)


async def test_hello_and_multiclient_minsize():
    srv, task, sock = await server_only()
    import pytmux
    try:
        rA, wA = await ipc.open_connection(sock)
        await pytmux.write_msg(wA, {"t": "hello", "cols": 100, "rows": 40,
                                    "session": "main", "token": srv.auth_token})
        sA = []
        await harness.drain(rA, sA, timeout=5,
                            until=lambda s: any(m["t"] == "layout" for m in s)
                            and any(m["t"] == "status" for m in s))
        assert any(m["t"] == "layout" for m in sA)
        assert any(m["t"] == "status" for m in sA)  # 단일 세션(이름 무시)
        # 둘째 클라이언트(더 작음) → 공유 최소 크기 80x24
        rB, wB = await ipc.open_connection(sock)
        await pytmux.write_msg(wB, {"t": "hello", "cols": 80, "rows": 24,
                                    "token": srv.auth_token})
        sB = []
        await harness.drain(rB, sB, timeout=5,
                            until=lambda s: any(m["t"] == "layout" for m in s))
        layB = [m for m in sB if m["t"] == "layout"][-1]
        assert layB["cols"] == 80 and layB["rows"] == 24, "최소 크기 공유"
        wA.close()
        wB.close()
    finally:
        await teardown(srv, task, sock)


async def test_capture_output():
    """패널 출력 캡처(plugins/rec): 기본 OFF(깃헙 배포 F4 — opts 미설정 시 미캡처),
    토글로 ON 시 무손실 기록, opts.json plugin_opts 영속/재시작 유지(끈 선택도 유지)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        assert srv.capture is False, "기본 OFF(opts 미설정 시, F4/깃헙 배포)"
        assert srv.set_capture(True) is True, "토글 ON"

        # 켜진 상태에서 기록 → <날짜>_<시간>_<세션>_<탭>_p<패널>.log 에 raw 바이트 무손실
        srv._capture_write(pane, b"hello\x1b[31m world")
        path = srv._cappaths[pane.id]
        assert os.path.basename(path).endswith(f"_p{pane.id}.log"), path
        with open(path, "rb") as f:
            assert f.read() == b"hello\x1b[31m world", "무손실 캡처"
        # 메타 로그에 파일명·탭/패널 매핑
        meta = open(os.path.join(srv.capture_dir, "sessions.log")).read()
        assert os.path.basename(path) in meta and "tab0:" in meta, meta

        # 끄면 파일 닫힘 + opts.json 영속(capture=False)
        assert srv.set_capture(False) is False
        assert pane.id not in srv._capfiles, "끄면 핸들 닫힘"
        assert json.load(open(srv.opts_path))["plugin_opts"]["capture"] is False
        # 꺼진 동안 _on_pane_readable 경로는 기록하지 않음
        before = os.path.getsize(path)
        if srv.capture:
            srv._capture_write(pane, b"X")
        assert os.path.getsize(path) == before, "OFF 중 기록 없음"

        # 재시작 영속: 같은 sock 로 새 Server 를 만들면 OFF 를 읽음
        assert pytmux.Server(sock).capture is False, "재시작 후 OFF 유지"

        # 토글로 다시 ON → opts 갱신, 재기록 가능. OFF→ON 은 새 시각 파일로 재오픈.
        assert srv.set_capture(None) is True
        assert json.load(open(srv.opts_path))["plugin_opts"]["capture"] is True
        srv._capture_write(pane, b"again")
        path2 = srv._cappaths[pane.id]
        with open(path2, "rb") as f:
            assert f.read().endswith(b"again"), "재개 후 기록"
    finally:
        srv._close_all_capfiles()
        shutil.rmtree(srv.capture_dir, ignore_errors=True)
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_capture_dir_project_and_override():
    """캡처 루트 경로 결정(§10): 기본 소켓→프로젝트 captures/, 임시 소켓→state_base,
    PYTMUX_CAPTURE_DIR→강제 override. Perforce 공유용 경로 + GitHub 차단 대상."""
    from pytmuxlib import ipc, server as srv_mod
    # 임시(비기본) 소켓 → 휘발 영역(state_base 옆 .capture). 프로젝트 미오염.
    tmp_srv = pytmux.Server("/tmp/pytmux-test-xyz.sock")
    assert tmp_srv.capture_dir == "/tmp/pytmux-test-xyz.sock.capture"

    # 기본 엔드포인트 → 프로젝트 captures/<id> 하위(Perforce 공유 대상)
    default_srv = pytmux.Server(ipc.default_endpoint())
    cap = default_srv.capture_dir
    assert cap.startswith(os.path.join(srv_mod.PROJECT_DIR, "captures")), cap
    # 캡처 루트는 .gitignore 의 captures/ 로 GitHub 차단(민감 화면 유출 방지)
    gi = open(os.path.join(srv_mod.PROJECT_DIR, ".gitignore"),
              encoding="utf-8").read()
    assert "captures/" in gi, "captures/ 가 .gitignore 에 있어야 함(GitHub 차단)"

    # PYTMUX_CAPTURE_DIR override 가 최우선
    os.environ["PYTMUX_CAPTURE_DIR"] = "/tmp/pytmux-cap-override"
    try:
        ov = pytmux.Server("/tmp/pytmux-test-xyz.sock")
        assert ov.capture_dir.startswith("/tmp/pytmux-cap-override"), ov.capture_dir
    finally:
        del os.environ["PYTMUX_CAPTURE_DIR"]


async def test_swap_pane_ids():
    # #9b: swap_pane_ids 가 임의의 두 리프 패널 위치를 맞바꾼다(드래그 swap 서버측).
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")            # 좌우 2분할
        win = sess.active_window
        leaves = win.panes()
        assert len(leaves) == 2
        a, b = leaves[0], leaves[1]
        # 분할 전 위치(rect) 기록
        panes0, _ = win.compute_layout(0, 0, 80, 24)
        rect = {p.id: p.rect for p in panes0}
        # a, b 위치 교환
        assert srv.swap_pane_ids(sess, a.id, b.id) is True
        panes1, _ = win.compute_layout(0, 0, 80, 24)
        rect2 = {p.id: p.rect for p in panes1}
        assert rect2[a.id] == rect[b.id], "a 가 b 의 옛 위치로"
        assert rect2[b.id] == rect[a.id], "b 가 a 의 옛 위치로"
        # 트리 일관성 유지
        for p in win.panes():
            assert p.parent is None or p in (p.parent.a, p.parent.b)
        # 같은 id / 없는 id 는 no-op(False)
        assert srv.swap_pane_ids(sess, a.id, a.id) is False
        assert srv.swap_pane_ids(sess, a.id, 99999) is False
    finally:
        await teardown(srv, task, sock)


async def test_single_pane_border_toggle_and_persist():
    # #9: 단일 패널 테두리 표시를 옵션화. 기본 ON(단일 패널도 box), off 면 단일
    # 패널은 box 없이 화면 전체를 내용으로 쓴다. 패널이 둘 이상이면 옵션과
    # 무관하게 항상 테두리. opts.json 영속.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        assert srv.single_border is True, "기본 ON"
        # 단일 패널 + 기본 ON → box 있음
        lay = srv._layout_msg(sess)
        assert len(lay["panes"]) == 1 and lay["panes"][0]["box"], "단일 패널도 테두리"
        # OFF → 단일 패널 box 없음, 내용이 화면 전체
        assert srv.set_single_border(False) is False
        lay = srv._layout_msg(sess)
        assert lay["panes"][0]["box"] is None, "off 면 단일 패널 테두리 없음"
        assert lay["panes"][0]["w"] == 80 and lay["panes"][0]["h"] == 24, \
            "테두리 없으면 내용이 화면 전체"
        # 패널이 둘 이상이면 off 여도 테두리 유지(패널 구분 필요)
        srv.split_pane(sess, "lr")
        lay = srv._layout_msg(sess)
        assert len(lay["panes"]) == 2
        assert all(p["box"] for p in lay["panes"]), "다중 패널은 항상 테두리"
        # opts.json 영속 + 재시작 후 OFF 유지, status 에도 반영
        assert json.load(open(srv.opts_path))["single_border"] is False
        assert pytmux.Server(sock).single_border is False, "재시작 후 OFF 유지"
        assert srv._status_msg(sess)["single_border"] is False, "status 반영"
        assert srv.set_single_border(None) is True   # 토글 → ON
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def _drain_pane_feed(pane, limit=5000):
    """버스트 드레인 태스크가 끝날 때까지(또는 한도) 루프를 돌린다."""
    for _ in range(limit):
        if pane._feed_task is None and not pane._feedbuf:
            return
        await asyncio.sleep(0)
    raise AssertionError("feed 드레인이 끝나지 않음")


async def test_large_output_chunked_equivalent_and_lossless():
    # 대량 출력 비차단 처리(feed 슬라이스 드레인): 서버 ingest 경로로 큰 버스트를
    # 넣으면 reader 를 떼고 FEED_SLICE 단위로 쪼개 먹인 뒤 재개한다. 결과 화면/
    # 스크롤백이 동일 데이터를 한 번에 feed 한 것과 바이트 단위로 같아야 한다
    # (색 escape 가 슬라이스 경계를 가로질러도 손실/깨짐 없음).
    from pytmuxlib.model import Pane
    from pytmuxlib.protocol import FEED_SLICE
    srv, task, sock = await server_only()
    try:
        pane = Pane(0, -1, 80, 24)   # pty=None → pause/resume 가드로 무해
        colors = [b"\x1b[31m", b"\x1b[32m", b"\x1b[33m", b"\x1b[0m"]
        payload = b"".join(
            colors[i % 4] + f"row-{i:05d}-the-quick-brown-fox\x1b[0m\r\n".encode()
            for i in range(800))
        assert len(payload) > FEED_SLICE * 3, "버스트 경로를 타도록 충분히 크게"
        srv._on_pane_data(pane, payload)
        assert pane._feed_task is not None, "버스트는 드레인 태스크로"
        assert pane._feedbuf, "아직 남은 바이트가 큐에 있어야"
        await _drain_pane_feed(pane)

        ref = Pane(0, -1, 80, 24)
        ref.feed(payload)            # 한 번에 먹인 기준값
        assert pane._export_screen() == ref._export_screen(), \
            "슬라이스 드레인 결과가 일괄 feed 와 동일해야(무손실)"
        lines = pane._export_screen()
        assert any("row-00000-" in ln for ln in lines), "첫 줄 스크롤백 보존"
        assert any("row-00799-" in ln for ln in lines), "마지막 줄 보존"
    finally:
        await teardown(srv, task, sock)


async def test_small_output_fed_inline():
    # 소량(대화형 에코 등, <= FEED_SLICE)은 드레인 태스크 없이 인라인 즉시 처리해
    # 기존 동기 경로와 동일하게 동작(추가 지연 0).
    from pytmuxlib.model import Pane
    srv, task, sock = await server_only()
    try:
        pane = Pane(0, -1, 80, 24)
        srv._on_pane_data(pane, b"hello world\r\n")
        assert pane._feed_task is None and pane._feedbuf == b"", "인라인 처리"
        assert any("hello world" in ln for ln in pane._export_screen())
    finally:
        await teardown(srv, task, sock)


async def test_feed_drain_interleaves_with_loop():
    # 드레인이 슬라이스마다 이벤트 루프에 양보하는지: 함께 도는 코루틴이 드레인
    # 진행 중에 여러 번 끼어들어 실행돼야 한다(한 번에 블록하지 않음).
    from pytmuxlib.model import Pane
    from pytmuxlib.protocol import FEED_SLICE
    srv, task, sock = await server_only()
    try:
        pane = Pane(0, -1, 80, 24)
        payload = (b"x" * FEED_SLICE) * 8        # 8 슬라이스
        progressed = 0
        drain_done = {"v": False}

        async def ticker():
            nonlocal progressed
            while not drain_done["v"]:
                progressed += 1
                await asyncio.sleep(0)

        tk = asyncio.create_task(ticker())
        srv._on_pane_data(pane, payload)
        await _drain_pane_feed(pane)
        drain_done["v"] = True
        await tk
        # 8 슬라이스 = 최소 8회 양보 → ticker 가 그 사이사이 여러 번 돌았어야.
        assert progressed >= 8, f"드레인이 루프를 양보하지 않음(progressed={progressed})"
    finally:
        await teardown(srv, task, sock)


async def test_feed_drain_disables_gc_during_burst():
    # §10 GC 튜닝: 버스트 드레인이 도는 동안 순환 GC 를 꺼(슬라이스 중간 GC 일시정지로
    # 인한 입력 끊김 제거) 모든 드레인이 끝나면 다시 켜야 한다. 동시 드레인(여러 패널)
    # 에서도 카운터로 안전 — 마지막 하나가 끝날 때만 복구한다.
    import gc
    from pytmuxlib.model import Pane
    from pytmuxlib.protocol import FEED_SLICE
    srv, task, sock = await server_only()
    gc_was = gc.isenabled()
    try:
        gc.enable()   # 기준 상태 고정
        p1 = Pane(0, -1, 80, 24)
        p2 = Pane(0, -1, 80, 24)
        big = (b"y" * FEED_SLICE) * 6
        srv._on_pane_data(p1, big)
        srv._on_pane_data(p2, big)
        await asyncio.sleep(0)   # 드레인 태스크들이 시작해 _gc_drain_enter 를 거치게
        assert srv._gc_drain_depth == 2, "두 패널 드레인 → 깊이 2"
        assert not gc.isenabled(), "드레인 중엔 GC 꺼짐"
        await _drain_pane_feed(p1)
        await _drain_pane_feed(p2)
        assert srv._gc_drain_depth == 0, "모든 드레인 종료 → 깊이 0"
        assert gc.isenabled(), "원래 켜져 있었으면 드레인 후 GC 복구"
    finally:
        if gc_was:
            gc.enable()
        await teardown(srv, task, sock)


async def test_feed_drain_gc_balanced_on_cancel():
    # 드레인이 취소(패널 teardown/재attach)돼도 GC 가드 카운터가 균형을 유지해야 한다
    # — finally 의 _gc_drain_exit 가 항상 깊이를 되돌린다(GC 영구 꺼짐 방지).
    import gc
    from pytmuxlib.model import Pane
    from pytmuxlib.protocol import FEED_SLICE
    srv, task, sock = await server_only()
    gc_was = gc.isenabled()
    try:
        gc.enable()
        pane = Pane(0, -1, 80, 24)
        srv._on_pane_data(pane, (b"z" * FEED_SLICE) * 6)
        await asyncio.sleep(0)         # 드레인 태스크 시작(_gc_drain_enter)
        assert srv._gc_drain_depth == 1 and not gc.isenabled()
        srv._stop_pane_feed(pane)      # 진행 중 드레인 취소
        await asyncio.sleep(0)         # 취소 콜백 처리(finally 실행)
        assert srv._gc_drain_depth == 0, "취소 후에도 깊이 복구"
        assert gc.isenabled(), "취소돼도 GC 복구"
    finally:
        if gc_was:
            gc.enable()
        await teardown(srv, task, sock)


async def test_coalesce_repaints_collapses_feedbuf_and_persists():
    # §10 대응 ②: 옵션이 켜져 있으면 _on_pane_data 가 쌓인 alt-screen 풀스크린
    # 리페인트 버스트를 합쳐 feedbuf 를 줄이고, 끄면 그대로 둔다. 옵션은 opts.json 영속.
    from pytmuxlib.model import Pane
    from pytmuxlib.protocol import FEED_SLICE
    srv, task, sock = await server_only()
    try:
        assert srv.coalesce_repaints is True, "기본 ON"

        def frame(tag):
            pad = b"x" * 1200   # 슬라이스 경로(>FEED_SLICE)를 타도록 프레임을 키운다
            return (b"\x1b[H\x1b[2J\x1b[1;1H" + f"FRAME-{tag}".encode() +
                    pad + b"\x1b[0m")

        pane = Pane(0, -1, 80, 24)
        pane.feed(b"\x1b[?1049h")          # alt 진입(이후 도착분은 alt 버스트)
        burst = b"".join(frame(c) for c in "ABCDEFGHIJ")
        assert len(burst) > FEED_SLICE, "버스트 경로를 타도록 충분히 크게"
        srv._on_pane_data(pane, burst)
        # 마지막 2J 이전(앞 9 프레임)이 드롭돼 남은 바이트가 한 프레임 수준으로 작아야.
        remaining = len(pane._feedbuf) + 0
        await _drain_pane_feed(pane)
        assert remaining < len(burst) // 2, \
            f"coalesce 로 feedbuf 가 크게 줄어야(remaining={remaining}/{len(burst)})"
        txt = pane_text(pane)   # alt-screen 렌더(_export_screen 은 main 만 봄)
        assert "FRAME-J" in txt, "마지막 프레임 보존"
        assert "FRAME-A" not in txt, "중간 프레임 무효화"

        # 끄면 드롭 안 함
        assert srv.set_coalesce_repaints(False) is False
        assert json.load(open(srv.opts_path))["coalesce_repaints"] is False
        pane2 = Pane(0, -1, 80, 24)
        pane2.feed(b"\x1b[?1049h")
        srv._on_pane_data(pane2, burst)
        # 끄면 합치지 않으므로 버스트 전체가 드레인 큐에 그대로 남는다(드레인 전 시점).
        assert len(pane2._feedbuf) == len(burst), "끄면 합치지 않음"
        await _drain_pane_feed(pane2)

        # 재시작(같은 sock) 후 OFF 유지
        assert pytmux.Server(sock).coalesce_repaints is False
        assert srv.set_coalesce_repaints(None) is True   # 토글 → ON
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_small_chunk_burst_engages_drain_backpressure():
    # 회귀: 짧은 간격으로 연달아 오는 소형 청크(≤FEED_SLICE)도 버스트로 감지해 드레인
    # 경로(pause 백프레셔·슬라이스 양보)로 돌린다. Windows owned-ConPTY 는 read 를
    # FEED_SLICE 로 캡해 모든 청크가 인라인 한계 이하 → 과거엔 대량 출력도 드레인을 못
    # 타고 인라인으로 연속 처리돼 이벤트 루프가 포화·입력이 굶었다(Claude Code 패널).
    from pytmuxlib.model import Pane
    from pytmuxlib.protocol import FEED_SLICE
    from pytmuxlib.serverpty import BURST_RUN
    srv, task, sock = await server_only()
    try:
        pauses = []

        class _Spy:
            def write(self, b): pass
            def pause_reader(self): pauses.append(True)
            def resume_reader(self): pass

        pane = Pane(0, -1, 80, 24)
        pane.pty = _Spy()
        chunk = b"x" * 64          # 인라인 한계보다 훨씬 작음
        assert len(chunk) <= FEED_SLICE

        # 첫 청크는 유휴 후 단발 → 인라인(드레인 태스크 없음).
        srv._on_pane_data(pane, chunk)
        assert pane._feed_task is None, "단발 소형 청크는 인라인 즉시 처리"
        assert pauses == [], "단발은 reader 를 멈추지 않음"

        # 동기 연속 호출 → monotonic 간격이 BURST_GAP 이하 → 버스트 누적 → 드레인 진입.
        total = chunk
        for _ in range(BURST_RUN + 2):
            srv._on_pane_data(pane, chunk)
            total += chunk
        assert pane._feed_task is not None, "버스트 감지 시 드레인 경로로 전환"
        assert pauses, "버스트 드레인은 reader 를 멈춰 백프레셔를 건다"

        await _drain_pane_feed(pane)
        # 모든 바이트가 손실 없이 pyte 에 먹였는지(렌더된 한 줄에 64*N 개의 x).
        assert pane_text(pane).count("x") == len(total), "버스트 드레인도 무손실"
    finally:
        await teardown(srv, task, sock)


async def test_set_vt_parser_validates_and_persists():
    """set_vt_parser 명령 계약: 기본 native(2026-06-16 전환) → pyte 로 명시 변경 시
    검증·opts.json 영속·재시작 round-trip. 잘못된 값은 무시(현행 유지). (패널이
    vt_parser 를 채택하는 동작은 test_model 의 native 등가 테스트가 커버.)
    docs/VT_PARSER_TRADEOFF §8·§9."""
    srv, task, sock = await server_only()
    try:
        assert srv.vt_parser == "native", "기본 native"
        assert srv.set_vt_parser("bogus") == "native", "잘못된 값 무시(현행 유지)"
        assert srv.set_vt_parser("native") == "native"
        assert json.load(open(srv.opts_path))["vt_parser"] == "native", "opts 영속"
        # 재시작(같은 sock) round-trip — _load_opts↔_save_opts 짝맞춤 확인
        assert pytmux.Server(sock).vt_parser == "native"
        # 되돌리면 pyte 로 영속
        assert srv.set_vt_parser("pyte") == "pyte"
        assert json.load(open(srv.opts_path))["vt_parser"] == "pyte"
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


class _OneShotReader:
    """hello 프레임 하나를 준 뒤 EOF(IncompleteReadError)를 내는 가짜 reader.
    handle_client 의 attach 직후 종료 경로(초기 _send_full 만 도는)를 재현한다."""
    def __init__(self, hello: dict):
        body = json.dumps(hello).encode()
        self._buf = len(body).to_bytes(4, "big") + body
        self._done = False

    async def readexactly(self, n: int) -> bytes:
        if self._done or len(self._buf) < n:
            self._done = True
            raise asyncio.IncompleteReadError(b"", n)
        out, self._buf = self._buf[:n], self._buf[n:]
        if not self._buf:
            self._done = True
        return out


class _NullWriter:
    def write(self, b): pass
    async def drain(self): pass
    def close(self): pass
    def is_closing(self): return False
    def get_extra_info(self, name, default=None): return default  # 피어검증(F2)용


async def test_attach_survives_send_full_error():
    # 회귀(§10 안정성): 초기 _send_full 이 예외를 던져도 handle_client 가 ① 예외를
    # 전파하지 않고(클라 즉시 종료/브릭 방지) ② 클라를 self.clients 에 누수하지 않으며
    # ③ 트레이스백을 <sock>.error.log 에 남긴다. 예전엔 _send_full 이 try 밖이라 한 번
    # 터지면 클라가 누수되고 이후 모든 attach 가 같은 상태로 브릭됐다.
    srv, task, sock = await server_only()
    try:
        srv.ensure_default_session(80, 24)
        boom = []

        async def _raise(_c):
            boom.append(1)
            raise RuntimeError("synthetic _send_full failure")

        srv._send_full = _raise
        before = len(srv.clients)
        # 예외가 전파되지 않아야 한다(await 가 깔끔히 반환).
        await srv.handle_client(_OneShotReader({"t": "hello", "cols": 80,
                                                "rows": 24,
                                                "token": srv.auth_token}),
                                _NullWriter())
        assert boom, "초기 _send_full 이 호출됐어야"
        assert len(srv.clients) == before, "실패한 클라가 누수되면 안 됨"
        log = ipc.state_base(sock) + ".error.log"
        assert os.path.exists(log), "에러 로그가 남아야"
        assert "synthetic _send_full failure" in open(log, encoding="utf-8").read()
    finally:
        try:
            os.unlink(ipc.state_base(sock) + ".error.log")
        except OSError:
            pass
        await teardown(srv, task, sock)


def _claude_footer(mode):
    """권한모드 footer 텍스트 한 줄(claude_perm_mode 가 mode 로 판정하도록)."""
    return {
        "auto": "⏵⏵ auto mode on (shift+tab to cycle)",
        "accept": "⏵⏵ accept edits on (shift+tab to cycle)",
        "plan": "plan mode on (shift+tab to cycle)",
        "default": "↑↓ history  (shift+tab to cycle)",
    }[mode]


async def test_claude_perm_mode_set_and_drive():
    """§10 item 2: 권한모드 footer 클릭 팝업 흐름의 서버측 — set_claude_perm_mode 가
    목표를 박고, _drive_perm_mode 가 idle footer 가 목표가 될 때까지 shift+tab(backtab)
    을 폐루프 주입하며, 도달하면 _perm_target 를 해제한다. _status_msg 는 현재
    perm_mode 를 실어 보낸다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(100, 30)
        p = sess.active_window.active_pane
        injected = []
        srv._inject_keys = lambda pane, data: injected.append(data)
        # 목표 설정(클라 팝업 → set_claude_perm_mode)
        srv.set_claude_perm_mode(sess, "auto")
        assert p._perm_target == "auto"
        # 현재 default → 한 번 shift+tab 주입, 카운터 증가
        srv._drive_perm_mode(p, _claude_footer("default"), "auto")
        assert injected == [b"\x1b[Z"], injected
        # 화면 미갱신(같은 모드) → 중복 주입 안 함
        srv._drive_perm_mode(p, _claude_footer("default"), "auto")
        assert len(injected) == 1, "화면 갱신 전 중복 주입 금지"
        # 화면이 auto 로 갱신 → 도달, 목표 해제, 추가 주입 없음
        srv._drive_perm_mode(p, _claude_footer("auto"), "auto")
        assert p._perm_target is None and len(injected) == 1
        # status 에 현재 perm_mode 가 실린다
        p._perm_mode = "auto"
        msg = srv._status_msg(sess)
        pc = [e for e in msg["panes_claude"] if e["id"] == p.id][0]
        assert pc["perm_mode"] == "auto", pc
    finally:
        await teardown(srv, task, sock)


async def test_perm_drive_accept_is_not_auto_and_fallback():
    """사용자 보고: 새 세션이 acceptEdits('accept edits on')에서 멈춰 진짜 auto
    ('auto mode on')까지 못 갔다. acceptEdits 와 auto 는 다른 모드라(둘 다 ⏵⏵),
    auto 목표 구동은 accept 에서 멈추지 않고 계속 순환한다. 단 auto 가 cycle 에 없는
    계정(한 바퀴 돌아 재방문)은 accept 로 폴백해 plan/default 에 안 멈춘다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(100, 30)
        p = sess.active_window.active_pane
        BT = b"\x1b[Z"
        sent = []
        srv._inject_keys = lambda pane, data: sent.append(data)

        # accept 는 auto 가 아니다 → 멈추지 않고 shift+tab(계속 순환).
        srv._perm_reset(p)
        done = srv._perm_step(p, _claude_footer("accept"), "auto")
        assert done is False and sent == [BT], "accept 에서 멈추면 안 됨"
        # 이어서 plan → auto 도달 시 종료(추가 주입은 plan 1회만).
        assert srv._perm_step(p, _claude_footer("plan"), "auto") is False
        assert srv._perm_step(p, _claude_footer("auto"), "auto") is True
        assert p._cam_tries == 0, "auto 도달 → 리셋"

        # 폴백: auto 가 cycle 에 없는 계정 — default→accept→plan 만 순환. 한 바퀴
        # 돌아 재방문하면 accept(편집 자동수락)로 정착한다(plan/default 에 안 멈춤).
        srv._perm_reset(p)
        sent.clear()
        seq = ["default", "accept", "plan", "default", "accept"]
        results = [srv._perm_step(p, _claude_footer(m), "auto") for m in seq]
        # default·accept·plan·default 까진 순환(주입), 재방문 후 accept 에서 정착(종료).
        assert results[-1] is True, results
        # bypass 는 자동 구동이 손대지 않는다(위험 모드).
        srv._perm_reset(p)
        sent.clear()
        assert srv._perm_step(p, "bypass permissions on", "auto") is True
        assert sent == [], "bypass → 무주입"
    finally:
        await teardown(srv, task, sock)


async def test_bypass_availability_tracked_and_in_status():
    """§10 item 2: bypass(권한 우회) 모드는 Claude 를 --dangerously-skip-permissions
    로 띄웠을 때만 shift+tab 순환에 나타난다. 서버가 idle footer 에서 bypass 를 한 번
    관측하면 Pane._bypass_seen 을 sticky True 로 두고 status(bypass_ok)로 실어, 클라
    팝업이 'Bypass Permission Mode' 항목을 노출하게 한다. 세션 종료 시 리셋된다."""
    srv, task, sock = await server_only()
    try:
        srv.claude_auto_launch = False    # auto-launch /rc·auto 격리
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane

        def scan(s):
            p.feed(b"\x1b[2J\x1b[H" + s.encode("utf-8") + b"\r\n")
            srv._scan_claude(sess, win)

        def status_bypass():
            msg = srv._status_msg(sess)
            e = [e for e in msg["panes_claude"] if e["id"] == p.id][0]
            return e.get("bypass_ok")

        # 일반 권한모드만 본 동안엔 bypass 미가용
        scan("? for shortcuts")
        assert p._bypass_seen is False and status_bypass() is False
        scan("⏵⏵ auto mode on (shift+tab to cycle)")
        assert p._bypass_seen is False and status_bypass() is False

        # bypass footer 를 한 번 관측 → sticky True, status 에 반영
        scan("bypass permissions on (shift+tab to cycle)")
        assert p._bypass_seen is True and status_bypass() is True
        assert p._perm_mode == "bypass"

        # 다른 모드로 돌아가도 가용성은 sticky 유지(시작 시 활성이라 cycle 에 잔존)
        scan("⏵⏵ auto mode on (shift+tab to cycle)")
        assert p._bypass_seen is True and status_bypass() is True

        # Claude 세션 종료(평범한 셸 화면) → 가용성 리셋
        scan("user@host:~$ ")
        assert p._claude is None
        assert p._bypass_seen is False and status_bypass() is False
    finally:
        await teardown(srv, task, sock)


async def test_restore_layout_session_has_popup():
    """§10 회귀(치명적 크래시): 부팅 시 layout.json 자동 복원(restore_layout)이 만드는
    Session 도 popup 속성을 가져야 한다. Session.__new__ 는 __init__ 을 건너뛰는데
    예전엔 popup 세팅을 빠뜨려, 복원된 세션에 attach 하면 _layout_msg→_popup_layout 의
    sess.popup 에서 AttributeError → _send_full 실패 → 화면 일부만 그려진 채 끊김/브릭."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.split_pane(sess, "lr")
        assert srv.save_layout(), "레이아웃 저장"
        srv.sessions.clear()
        assert srv.restore_layout(), "레이아웃 복원"
        rsess = next(iter(srv.sessions.values()))
        # 복원된 세션이 popup 을 가져야(없으면 attach 가 통째로 깨졌다)
        assert hasattr(rsess, "popup") and rsess.popup is None
        # 핵심: _layout_msg/_popup_layout 가 예외 없이 동작
        assert srv._layout_msg(rsess) is not None
        assert srv._popup_layout(rsess, 80, 24) is None
    finally:
        try:
            os.unlink(srv.layout_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_no_window_kwargs():
    """§10 Windows: subprocess 콘솔 창 팝업 방지 헬퍼. POSIX 에선 빈 dict(무영향),
    Windows 에선 CREATE_NO_WINDOW creationflags 를 담는다(clip.exe·PowerShell
    Get-Clipboard·cmd /c·tasklist/taskkill 콘솔 창이 번쩍이지 않게)."""
    from pytmuxlib import proc
    kw = proc.no_window_kwargs()
    assert isinstance(kw, dict)
    if proc.IS_WINDOWS:
        assert kw.get("creationflags", 0) & 0x08000000, kw  # CREATE_NO_WINDOW
    else:
        assert kw == {}, kw


async def test_status_usage_display_m18():
    """M18-A: 배지-only 사용문자열에 세션 누계/윈도우 근사 사용%를 ~ 로 곁들이고,
    Claude 점유%는 그대로. M18-B: 5h 근접도 %는 분모(설정/학습)가 있을 때만."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p._claude = "idle"
        # A: 배지-only + 세션 누계 → 근사 사용%(~)
        p._claude_usage = "1M ctx"
        p._session_tokens = 100_000
        assert srv._usage_text(p) == "ctx:~10%/1M"
        # 누계가 윈도우 이상이면 근사 생략(배지만)
        p._session_tokens = 1_500_000
        assert srv._usage_text(p) == "1M ctx"
        # Claude 가 점유%를 그리면 그대로(콤팩트 포맷)
        p._claude_usage = "ctx:23%/1M"
        assert srv._usage_text(p) == "ctx:23%/1M"
        # B(S6 T3): 실측 없으면 항상 None — 분모 근사 폐기(지어내지 않음 일관).
        # §7-4: token_budget_5h 설정 자체도 deprecate 로 제거됨.
        p._session_tokens = 100_000
        assert srv._tok5h_pct(p, 25_000) is None
        # 실측이 오면 그 값(분자/분모 불필요)
        srv._usage = {"session": {"pct": 7, "reset": "2pm"}}
        assert srv._tok5h_pct(p, 25_000) == 7
        # 비-Claude 면 None
        p._claude = None
        assert srv._tok5h_pct(p, 25_000) is None
    finally:
        await teardown(srv, task, sock)


async def test_repeat_loop_warn_m17():
    """M17 S8: 동일 출력으로 busy→idle 완료가 반복되면 루프 의심 경고가 선다(grade0)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        for _ in range(4):
            p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))  # busy
            srv._scan_claude(sess, win)
            assert p._claude == "busy", p._claude
            p.feed(b"\x1b[2J\x1b[HError: same failure\r\n? for shortcuts\r\n")  # idle
            srv._scan_claude(sess, win)
            assert p._claude == "idle", p._claude
        # 4회 동일 완료 → repeat_n>=3 → 경고 문자열 설정
        assert p._repeat_n >= 3, p._repeat_n
        assert p._claude_warn and "반복" in p._claude_warn, p._claude_warn
        # 출력이 달라지면 카운터 리셋 → 경고 해제
        p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        p.feed(b"\x1b[2J\x1b[HDifferent now\r\n? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._repeat_n == 0 and p._claude_warn is None
        # repeat_alert=0 이면 반복이 쌓여도 경고 안 뜸(opt 끔)
        srv.set_claude_turn_warn(repeat=0)
        for _ in range(4):
            p.feed("\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8"))
            srv._scan_claude(sess, win)
            p.feed(b"\x1b[2J\x1b[HSame fail\r\n? for shortcuts\r\n")
            srv._scan_claude(sess, win)
        assert p._repeat_n >= 3 and p._claude_warn is None
    finally:
        await teardown(srv, task, sock)


async def test_claude_model_status_m14c():
    """M14c: 스캔이 실 배지 'Opus 4.8 (1M context)' 에서 모델을 파싱해 저장하고
    status 로 송신한다(활성 Claude 패널 한정)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p.feed("\x1b[2J\x1b[H Opus 4.8 (1M context)\r\n✽ Crunching… (3s · ↑ 1k tokens)\r\n"
               .encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude == "busy"
        assert p._claude_model == "opus-4.8", p._claude_model
        assert srv._status_msg(sess)["claude_model"] == "opus-4.8"
    finally:
        await teardown(srv, task, sock)


async def test_usage_limits_status_m19():
    """M19: self._usage(그림자 /usage 결과)가 있으면 _tok5h_pct 가 분모 추정 대신
    세션 실측 % 를 그대로 쓰고, status 가 usage_limits 를 싣는다. (질의 자체는 숨은
    세션이라 결과를 직접 주입해 배선만 검증.)"""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p._claude = "idle"
        assert srv._tok5h_pct(p, 0) is None        # _usage 없고 분모 0 → None
        srv._usage = {"session": {"pct": 37, "reset": "2pm (Asia/Seoul)"},
                      "week_all": {"pct": 14, "reset": "Jun 13 at 3am"}}
        assert srv._tok5h_pct(p, 0) == 37          # 실측 — 분모 불필요
        m = srv._status_msg(sess)
        assert m["usage_limits"]["session"]["pct"] == 37
        assert m["usage_limits"]["week_all"]["pct"] == 14
    finally:
        await teardown(srv, task, sock)


async def test_tok5h_pct_fail_open_on_account_mismatch():
    """그림자 /usage 세션의 계정과 패널 계정이 둘 다 알려져 있고 다르면 _tok5h_pct 가
    None(상태줄 5h% 숨김) — 다른 계정의 한도가 이 패널 계정 라벨로 그려지는 오표기
    방지(사용자 보고 2026-06-13: 팝업 'Account (/usage)' ≠ 하단 토큰 표시 계정).
    한쪽이라도 미상이면 같은 로그인으로 보고 표시한다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p._claude = "idle"
        srv._usage = {"session": {"pct": 37},
                      "account": "me@woojinkim.org"}
        # ① 패널 계정 미상 → 같은 로그인 가정, 실측 표시
        assert srv._tok5h_pct(p, 0) == 37
        # ② 패널 계정 == 측정 계정 → 표시
        p._claude_account = "me@woojinkim.org"
        assert srv._tok5h_pct(p, 0) == 37
        # ③ 계정 불일치 → fail-open(숨김)
        p._claude_account = "wo@nexongames.co.kr"
        assert srv._tok5h_pct(p, 0) is None
        # ④ 측정 계정 미상 → 같은 로그인 가정, 표시
        srv._usage = {"session": {"pct": 37}}
        assert srv._tok5h_pct(p, 0) == 37
    finally:
        await teardown(srv, task, sock)


async def test_status_static_opts_only_on_full_c4():
    """C4: 토글로만 바뀌는 정적 옵션(claude_rules·컨텍스트/경고 임계)은 full status
    (attach·_broadcast_session)에만 싣고 주기(full=False) status 에선 뺀다. 낙관적
    토글 4개 불린은 주기에도 유지(전용 브로드캐스트 경로 없음)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        STATIC = ["claude_rules",
                  "claude_long_turn_sec", "claude_repeat_alert"]
        full = srv._status_msg(sess, full=True)
        for k in STATIC:
            assert k in full, f"full status 에 정적 옵션 {k} 누락"
        periodic = srv._status_msg(sess, full=False)
        for k in STATIC:
            assert k not in periodic, f"주기 status 가 정적 옵션 {k} 를 실음(C4 위반)"
        for k in ("single_border",
                  "claude_auto_mode", "claude_pending"):
            assert k in periodic, f"주기 status 에 동적/낙관 필드 {k} 누락"
    finally:
        await teardown(srv, task, sock)


async def test_manual_clear_resets_token_session():
    """수동 /clear 감지(2026-06-07): 사용자가 직접 /clear 하면 환영 배너가 뜨고,
    pytmux 가 토큰 누계를 새 세션으로 끊어 상태줄 ctx 근사%(누계/윈도우)가 비워진다.
    (자동화 _pc_advance 경로를 안 타는 수동 /clear 가 옛 % 를 남기던 문제 회귀.)"""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # Claude busy + 토큰 누계 발생
        p.feed("\x1b[2J\x1b[H? for shortcuts\r\n↑ 5k tokens\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude in ("busy", "idle")
        sid = p._claude_session_id
        assert p._session_tokens > 0, p._session_tokens
        # 사용자 직접 /clear → 환영(splash) 배너 + 빈 컨텍스트(idle)
        p.feed("\x1b[2J\x1b[H Claude Code v2.1.168\r\n"
               " Opus 4.8 (1M context)\r\n"
               " ⏵⏵ auto mode on (shift+tab to cycle)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._session_tokens == 0, ("토큰 누계가 안 끊김", p._session_tokens)
        assert p._claude_session_id == sid + 1, "새 세션 id 부여"
        assert p._welcome_seen is True
        # 배너가 머무는 동안 재리셋 안 함(디바운스) — id 가 또 안 오른다
        srv._scan_claude(sess, win)
        assert p._claude_session_id == sid + 1, "배너 지속 중 재리셋 금지"
    finally:
        await teardown(srv, task, sock)


async def test_token_log_reply_includes_daily():
    """request_token_log 회신에 버킷 전체 이력 집계용 일자별 합성 레코드(daily)가
    실린다(cap 무관 일/주/월). 플러그인 handle_server_request 훅 경유(코어 serverio
    는 내용을 모름)."""
    from pytmuxlib import usagedb
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        conn = srv._tokens_db_conn()
        A = "me@woojinkim.org"
        usagedb.insert(conn, {"ts": 200.0, "tab": 0, "pane": 1, "session": 1,
                              "account": A, "tokens": 300})
        resp = srv.plugins.handle_server_request(
            srv, sess, "request_token_log", {"limit": 100})
        assert resp and resp["t"] == "token_log"
        # 버킷 전체 이력 집계용 일자별 합성 레코드가 실린다(cap 무관 일/주/월).
        daily = resp.get("daily")
        assert isinstance(daily, list) and len(daily) == 1, daily
        assert daily[0]["tokens"] == 300 and daily[0]["account"] == A, daily[0]
        assert sum(d["tokens"] for d in daily) == usagedb.total_all(conn)
    finally:
        await teardown(srv, task, sock)


async def test_token_log_reply_includes_xc_totals():
    """§10-D P6: request_token_log 회신에 트랜스크립트 권위 합(xc_totals)이 실린다 —
    팝업이 실측 4항목(cache 포함)을 1차값으로 보이게. footer 스크랩(records)과 별개로
    usage_xc 의 full/footer/cache_read/cache_create/ratio 를 서버가 SQL 로 집계해 보낸다."""
    from pytmuxlib import usagedb
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        conn = srv._tokens_db_conn()
        # in=10,out=5,cc=0,cr=985 → footer=15, full=1000. 2건.
        for i in range(2):
            usagedb.insert_xc(conn, {
                "xkey": f"m{i}:r{i}", "ts": "2026-06-22T10:00:00.000Z",
                "session_uuid": "s1", "model": "claude-opus-4-8",
                "input": 10, "output": 5, "cache_create": 0,
                "cache_read": 985, "is_sidechain": 0})
        resp = srv.plugins.handle_server_request(
            srv, sess, "request_token_log", {"limit": 100})
        assert resp and resp["t"] == "token_log"
        xc = resp.get("xc_totals")
        assert isinstance(xc, dict) and xc.get("full") == 2000, xc
        assert xc["footer"] == 30 and xc["cache_read"] == 1970, xc
        assert round(xc["ratio"], 1) == round(2000 / 30, 1), xc
    finally:
        await teardown(srv, task, sock)


async def test_status_includes_xc_totals_cached_dirty_gated():
    """§10-D P7: status 가 트랜스크립트 권위 누계(xc_totals)를 싣는다 — federation
    다운스트림이 원격 서버의 정확 Σ(cache 포함)를 보도록 usage_limits 와 동형. 매
    status 풀스캔을 피하는 dirty 게이트 캐시: _xc_totals_dirty 가 설 때만 재계산하고
    그 외엔 직전 캐시값을 그대로 싣는다(핫패스 비용 회피)."""
    from pytmuxlib import usagedb
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        conn = srv._tokens_db_conn()
        usagedb.insert_xc(conn, {
            "xkey": "m1:r1", "ts": "2026-06-22T10:00:00.000Z",
            "session_uuid": "s1", "model": "claude-opus-4-8", "input": 10,
            "output": 5, "cache_create": 0, "cache_read": 985, "is_sidechain": 0})
        srv._xc_totals_dirty = True                 # 적재 → 캐시 무효
        m = srv._status_msg(sess)
        assert m["xc_totals"]["full"] == 1000, m["xc_totals"]
        assert m["xc_totals"]["cache_read"] == 985, m["xc_totals"]
        # 추가 적재했지만 dirty 를 안 세우면 status 는 옛 캐시값(풀스캔 회피 확인).
        usagedb.insert_xc(conn, {
            "xkey": "m2:r2", "ts": "2026-06-22T10:01:00.000Z",
            "session_uuid": "s1", "model": "claude-opus-4-8", "input": 10,
            "output": 5, "cache_create": 0, "cache_read": 985, "is_sidechain": 0})
        assert srv._status_msg(sess)["xc_totals"]["full"] == 1000  # 캐시 유지
        srv._xc_totals_dirty = True                 # 무효화 → 재계산
        assert srv._status_msg(sess)["xc_totals"]["full"] == 2000
    finally:
        await teardown(srv, task, sock)


async def test_usage_fixture_end_to_end_chain():
    """S6 T6 골든: 실 /usage 패널 캡처(fixtures/claude/usage.txt)를 패널에 흘리면
    파서(parse_usage)→_usage 캡처→limits 스냅샷(T1)→상태줄 5h% 실측(T3)→게이트
    판단(T4)→status 신선도(usage_age_sec)까지 전 체인이 fixture 값으로 정합한다.
    Claude 가 /usage 레이아웃을 바꾸면 여기서 깨진다(드리프트 회귀망).
    (T4 게이트는 과사용 완화 제거로 폐기 — 추적 체인만 검증.)"""
    from pytmuxlib import usagedb
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        fix = os.path.join(os.path.dirname(__file__), "fixtures", "claude",
                           "usage.txt")
        raw = open(fix, encoding="utf-8").read()
        p.feed(b"\x1b[2J\x1b[H"
               + raw.replace("\n", "\r\n").encode("utf-8"))
        srv._scan_claude(sess, win)
        # 파서 → _usage 캡처(세션 2%·주간 14%·Sonnet 0%)
        assert srv._usage["session"]["pct"] == 2, srv._usage
        assert srv._usage["week_all"]["pct"] == 14
        # T1: limits 스냅샷 이력(source=panel)
        snap = usagedb.last_limits(srv._tokens_db_conn())
        assert snap and snap["session_pct"] == 2 and snap["source"] == "panel"
        assert snap["week_all_pct"] == 14 and snap["week_sonnet_pct"] == 0
        # T3: 상태줄 5h% = 실측 그대로(분모 근사 없음)
        p._claude = "idle"
        assert srv._tok5h_pct(p, 0) == 2
        # status 에 실측 경과(usage_age_sec)가 정수로 실린다(stale 표기용)
        m = srv._status_msg(sess)
        assert isinstance(m["usage_age_sec"], int) and m["usage_age_sec"] >= 0
    finally:
        await teardown(srv, task, sock)


async def test_status_week_sonnet_when_model_sonnet():
    """활성 모델=Sonnet 이면 status 메시지가 5h(통합) 대신 주간 Sonnet% 만 싣는다
    (tok5h_pct=None, week_sonnet_pct=값). 그 외 모델은 종전대로 5h 세션%만(요청
    2026-06-16: Anthropic 이 5h 를 모델 통합으로만 줘 모델별 측정 불가 → sonnet 엔
    측정 가능한 주간 Sonnet only 만 표시, 5h 는 숨김)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p._claude = "idle"
        srv._usage = {"session": {"pct": 40, "reset": None},
                      "week_sonnet": {"pct": 12, "reset": None}}
        # 모델=Sonnet → 5h 숨김(None), 주간 Sonnet% 만
        p._claude_model = "sonnet-4.6"
        m = srv._status_msg(sess)
        assert m["tok5h_pct"] is None, m
        assert m["week_sonnet_pct"] == 12, m
        # 모델=Opus → 종전대로 5h 세션%, 주간 Sonnet 미전송
        p._claude_model = "opus-4.8"
        m = srv._status_msg(sess)
        assert m["tok5h_pct"] == 40, m
        assert m["week_sonnet_pct"] is None, m
    finally:
        await teardown(srv, task, sock)


async def test_week_sonnet_pct_fail_open_on_account_mismatch():
    """_week_sonnet_pct 도 _tok5h_pct 와 같은 계정 불일치 fail-open: /usage 계정과
    패널 계정이 둘 다 알려져 있고 다르면 None(한쪽 미상이면 표시). week_sonnet 부재도 None."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        p._claude = "idle"
        srv._usage = {"week_sonnet": {"pct": 9}, "account": "alice@x"}
        assert srv._week_sonnet_pct(p) == 9            # 패널 계정 미상 → 표시
        p._claude_account = "alice@x"
        assert srv._week_sonnet_pct(p) == 9            # 일치 → 표시
        p._claude_account = "bob@y"
        assert srv._week_sonnet_pct(p) is None         # 불일치 → 숨김
        srv._usage = {"session": {"pct": 5}}           # week_sonnet 부재 → None
        assert srv._week_sonnet_pct(p) is None
    finally:
        await teardown(srv, task, sock)


async def test_token_db_failure_logged_once():
    """#28 진단 로깅: 토큰 DB 연결 실패(토큰 영속 통째 정지)가 error.log 에 남되,
    매 커밋 재시도가 도배하지 않게 **첫 실패만** 기록된다. 성공으로 회복하면
    플래그가 풀려 재발 시 다시 1회 기록된다."""
    srv, task, sock = await server_only()
    old_db = os.environ.get("PYTMUX_TOKENS_DB")
    try:
        # 부모가 '파일'인 경로 → usagedb.connect 의 makedirs 가 실패한다.
        blocker = old_db  # harness 가 만든 임시 DB 경로(파일)를 디렉터리처럼 사용
        open(blocker, "w").close()
        os.environ["PYTMUX_TOKENS_DB"] = os.path.join(blocker, "x.db")
        assert srv._tokens_db_conn() is None
        assert srv._tokens_db_conn() is None      # 반복 실패
        elog = ipc.state_base(sock) + ".error.log"
        body = open(elog, encoding="utf-8").read()
        assert body.count("[tokens_db_connect]") == 1, "첫 실패만 기록(스팸 가드)"
        # 회복(정상 경로) → 연결 성공 + 플래그 해제
        os.environ["PYTMUX_TOKENS_DB"] = blocker + ".ok.db"
        assert srv._tokens_db_conn() is not None
        assert srv._tokens_db_err is False
    finally:
        if old_db is not None:
            os.environ["PYTMUX_TOKENS_DB"] = old_db
        await teardown(srv, task, sock)


async def test_usage_snapshot_failure_logged():
    """#28: 스냅샷 변환/계약 버그(sqlite 외 예외)가 조용히 사라지지 않고
    error.log 에 남는다(본 흐름 비차단 — 예외 전파 없음)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        from pytmuxlib import usagedb
        orig = usagedb.snap_from_usage
        usagedb.snap_from_usage = lambda *a, **k: (_ for _ in ()).throw(
            ValueError("계약 버그 시뮬"))
        try:
            srv._record_usage_snapshot({"session": {"pct": 1}}, "probe")  # 비차단
        finally:
            usagedb.snap_from_usage = orig
        elog = ipc.state_base(sock) + ".error.log"
        body = open(elog, encoding="utf-8").read()
        assert "[usage_snapshot]" in body and "계약 버그 시뮬" in body
    finally:
        await teardown(srv, task, sock)


async def test_panel_env_sets_truecolor_and_term():
    """패널 셸 환경(요청 2026-06-16): TERM=xterm-256color + COLORTERM=truecolor 를
    명시 설정해 로컬/원격 셸에서 같은 프로그램이 일관되게 24비트 색을 내게 한다
    (원격은 ssh 가 COLORTERM 을 안 넘겨 256색 폴백→색 미묘차 발생하던 것 해소)."""
    srv, task, sock = await server_only()
    try:
        env = srv._panel_env()
        assert env["TERM"] == "xterm-256color"
        assert env["COLORTERM"] == "truecolor"
        assert env["PYTMUX"] == srv.resolved_endpoint
        # 외부 LINES/COLUMNS 는 떼어 reflow 오염 방지(기존 거동 유지).
        assert "LINES" not in env and "COLUMNS" not in env
    finally:
        await teardown(srv, task, sock)


async def test_fmt_unrecognized_logs_footer_tail():
    """포맷 미인식 진단 로그 보강(요청 2026-06-25): claude_state 가 busy/limit/idle
    어느 것도 못 잡은 화면의 footer tail 을 _fmt_unrecognized_detail 이 추출하고,
    _log_error(detail) 이 error.log 에 남긴다 — 다음에 Claude footer 형식이 또 바뀌어도
    이 로그만 보고 새 형식을 파악해 claude.py _IDLE_ANCHORS 를 갱신할 수 있다."""
    import pyte
    from pytmuxlib import ipc
    srv, task, sock = await server_only()
    try:
        screen = pyte.Screen(40, 6)
        st = pyte.Stream(screen)
        st.feed("prompt box\r\n@@ brand-new footer 2099 @@\r\n")

        class _P:
            pass
        p = _P()
        p.screen = screen
        detail = srv._fmt_unrecognized_detail(p)
        assert "screen tail" in detail, detail
        assert "brand-new footer 2099" in detail, detail   # 마지막 비어있지 않은 줄
        # _log_error(detail) 가 detail 을 error.log 에 남기는지(기존 호출은 detail="" 라 무영향)
        srv._log_error("claude_format_unrecognized", detail)
        with open(ipc.state_base(sock) + ".error.log", encoding="utf-8") as f:
            log = f.read()
        assert "claude_format_unrecognized" in log
        assert "brand-new footer 2099" in log, "footer tail 이 로그에 남아야"
    finally:
        await teardown(srv, task, sock)


async def test_flush_to_client_drops_slow_consumer():
    """H-2: 한 클라의 송신버퍼가 high-water 를 넘으면(드레인을 못 따라옴) 즉시 떨궈
    flush 루프가 전체를 끌고 막히지 않게 한다. write 가 무한 드레인이어도 타임아웃 후
    떨군다(영구 hang 차단). 클라를 통째 제거하므로 _sent_rows 불일치 없음."""
    from pytmuxlib import serverio as S
    from pytmuxlib.model import ClientConn
    srv, task, sock = await server_only()
    try:
        class _Tr:
            def __init__(self, n):
                self._n = n

            def get_write_buffer_size(self):
                return self._n

        class _W:
            def __init__(self, n, hang=False):
                self.transport = _Tr(n)
                self.closed = False
                self._hang = hang

            def write(self, b):
                pass

            def close(self):
                self.closed = True

            async def drain(self):
                if self._hang:
                    await asyncio.Event().wait()

        # ① 송신버퍼 high-water 초과 → 즉시 드롭(write 안 함)
        c1 = ClientConn(_W(99 * 1024 * 1024))
        srv.clients.append(c1)
        await srv._flush_to_client(c1, [b"frame"])
        assert c1 not in srv.clients and c1.writer.closed, "high-water 즉시 드롭"
        # ② 무한 드레인 → 타임아웃 드롭
        c2 = ClientConn(_W(0, hang=True))
        srv.clients.append(c2)
        orig = S._CLIENT_WRITE_TIMEOUT
        S._CLIENT_WRITE_TIMEOUT = 0.05
        try:
            await srv._flush_to_client(c2, [b"frame"])
        finally:
            S._CLIENT_WRITE_TIMEOUT = orig
        assert c2 not in srv.clients and c2.writer.closed, "타임아웃 드롭"
        # ③ 정상 클라는 안 떨군다
        c3 = ClientConn(_W(0))
        srv.clients.append(c3)
        await srv._flush_to_client(c3, [b"frame"])
        assert c3 in srv.clients and not c3.writer.closed, "정상 클라 유지"
    finally:
        await teardown(srv, task, sock)


async def test_liveness_evicts_dead_client_and_regrows_session():
    """死-클라 회수(_evict_idle_clients): 반응 없는(ping 끊긴) 코-클라가 세션 공유
    크기(_session_size=min)를 작게 핀해 정상 클라 화면 하단/우측에 빈 띠가 남던
    것을 막는다. ever_pinged 인 클라가 CLIENT_IDLE_TIMEOUT 넘게 무응답이면 떨구고
    핀이 풀려 세션이 다시 자란다. ping 을 안 켠(ever_pinged=False) 무응답 클라는
    회수하지 않는다(오탐 방지)."""
    import time
    from pytmuxlib import serverio as S
    from pytmuxlib.model import ClientConn
    srv, task, sock = await server_only()
    try:
        class _W:
            def __init__(self):
                self.closed = False
                self.transport = None

            def write(self, b):
                pass

            def close(self):
                self.closed = True

            async def drain(self):
                pass

        def _mk(rows, cols=120):
            c = ClientConn(_W())
            c.session = sess
            c.cols, c.rows = cols, rows
            return c

        sess = srv.ensure_default_session(120, 50)
        now = time.monotonic()
        # 큰 정상 클라(방금 ping) + 작은 死 코-클라(ping 켜졌었지만 timeout 초과 무응답)
        live = _mk(50); live.ever_pinged = True; live.last_seen = now
        dead = _mk(10); dead.ever_pinged = True
        dead.last_seen = now - (S.CLIENT_IDLE_TIMEOUT + 5)
        srv.clients[:] = [live, dead]
        # 회수 전: min(50,10)=10 으로 핀
        assert srv._session_size(sess)[1] == 10, srv._session_size(sess)
        n = await srv._evict_idle_clients()
        assert n == 1 and dead not in srv.clients and dead.writer.closed
        # 회수 후: 핀 해제 → 살아 있는 클라 크기로 재성장
        assert live in srv.clients
        assert srv._session_size(sess)[1] == 50, srv._session_size(sess)

        # ping 을 안 켠(ever_pinged=False) 무응답 클라는 회수 대상 아님(오탐 방지)
        noping = _mk(8); noping.ever_pinged = False
        noping.last_seen = now - (S.CLIENT_IDLE_TIMEOUT + 99)
        srv.clients[:] = [live, noping]
        assert await srv._evict_idle_clients() == 0
        assert noping in srv.clients and not noping.writer.closed
    finally:
        await teardown(srv, task, sock)


async def test_autorename_apply_skips_stale_tab():
    """M-2: _fg_command executor await 동안 탭이 제거되거나(kill_window) active_pane 이
    바뀌면 stale 자동이름을 쓰지 않는다(정상일 때만 적용)."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        tab = sess.tabs[0]
        ap = tab.window.active_pane
        # 정상 → 적용
        assert srv._autorename_apply(sess, tab, ap, "vim") is True
        assert tab.name == "vim"
        # 탭이 세션에서 제거됨 → stale 미적용
        sess.tabs.remove(tab)
        assert srv._autorename_apply(sess, tab, ap, "bash") is False
        assert tab.name == "vim", "제거된 탭에 stale 이름 안 씀"
        # active_pane 이 바뀜 → 미적용
        sess.tabs.append(tab)
        assert srv._autorename_apply(sess, tab, object(), "bash") is False
        assert tab.name == "vim"
    finally:
        await teardown(srv, task, sock)


async def test_opts_load_save_symmetry():
    """1-3/1-4: server.py 가 _opts.get 으로 로드하는 모든 코어 옵션은 저장돼야 한다 —
    usage_refresh_sec 가 저장 누락돼 매 기동 600 으로 리셋되던 드리프트 재발 방지.

    1-4 로 save 가 단일소스 `_PERSISTED_OPT_KEYS` 튜플에서 자동 미러되므로(_save_opts 가
    literal `"key": self.key` 를 손으로 안 나열), 이 테스트는 **로드하는 모든 키가 그
    튜플(또는 명시 특수처리 키)에 있는지**를 대조한다 — 새 영속 옵션 추가 시 튜플에도
    넣게 강제(안 넣으면 저장 안 됨). 라운드트립 보존은 test_persisted_opt_keys_roundtrip_symmetry."""
    import re
    from pathlib import Path
    from pytmuxlib.serverpersist import ServerPersistMixin
    root = Path(__file__).resolve().parent.parent / "pytmuxlib"
    loaded = set(re.findall(r'_opts\.get\(\s*"([a-z_0-9]+)"',
                            (root / "server.py").read_text(encoding="utf-8")))
    persisted = set(ServerPersistMixin._PERSISTED_OPT_KEYS)
    # _save_opts 가 명시적으로 다루는 특수 키(단일소스 튜플 밖): disabled_plugins(플러그인
    # 매니저 소유)·remote_allowed_hosts(리스트 방어복사).
    specials = {"disabled_plugins", "remote_allowed_hosts"}
    missing = loaded - persisted - specials
    assert not missing, (
        f"_opts.get 로 로드하나 저장 안 되는 옵션(드리프트): {missing} — "
        f"serverpersist._PERSISTED_OPT_KEYS 에 추가할 것")


async def test_running_server_cm_starts_and_cleans_up():
    """1-6: running_server() 컨텍스트 매니저가 서버를 기동하고 블록 종료 시(예외
    경로 포함) teardown 한다 — 214곳 보일러플레이트를 한 줄로 줄이는 헬퍼."""
    from harness import running_server
    async with running_server() as (srv, task, sock):
        sess = srv.ensure_default_session(80, 24)
        assert sess is not None and srv.sessions and sock
    # 예외 경로에서도 teardown 이 돈다(finally) — 예외가 밖으로 전파되는지 확인.
    raised = False
    try:
        async with running_server() as (srv, task, sock):
            raise ValueError("boom")
    except ValueError:
        raised = True
    assert raised, "CM 이 예외를 삼키지 않고 finally 정리 후 전파"
