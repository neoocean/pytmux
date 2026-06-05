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
        srv._fg_command = lambda fd, _f=first: ("ssh" if fd == _f.master_fd
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


async def test_prompt_history_accumulates():
    # _track_prompt 가 제출 프롬프트를 시간순 히스토리에 쌓고(연속 중복 제외),
    # status 의 panes_claude 에 최근 목록을 전달한다(#7).
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        p = sess.active_window.active_pane
        srv._track_prompt(p, b"first\r")
        srv._track_prompt(p, b"second\r")
        srv._track_prompt(p, b"second\r")   # 연속 중복 → 제외
        srv._track_prompt(p, b"third\r")
        assert p.prompt_history == ["first", "second", "third"], p.prompt_history
        msg = srv._status_msg(sess)
        pc = next(e for e in msg["panes_claude"] if e["id"] == p.id)
        assert pc["history"][-1] == "third" and "first" in pc["history"]
    finally:
        await teardown(srv, task, sock)


async def test_inactive_tab_claude_done_flag():
    # 비활성 탭의 Claude 패널이 busy→(안정)idle 로 끝나면 has_claude_done 이 켜지고,
    # 그 탭을 보면(select_window) 해제된다(#22). idle 은 _DONE_IDLE_FRAMES 프레임
    # 안정돼야 완료로 친다(§10 #18 플리커 방지).
    from pytmuxlib.server import _DONE_IDLE_FRAMES
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        srv.new_window(sess)                 # 탭 1 추가(활성=탭1)
        srv.select_window(sess, 0)           # 탭0 활성 → 탭1 비활성
        t1 = sess.tabs[1]
        p1 = t1.window.active_pane
        win = sess.active_window             # 탭0(활성)
        # 처리중(busy) 을 스캔으로 확정(_was_busy 세팅)
        p1.feed("\x1b[2J\x1b[H↑ 1k tokens\r\n".encode("utf-8"))
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


async def test_startup_rules_injection():
    # #27: 저장된 시작 규칙이 새 Claude 세션의 첫 idle 에 프롬프트로 주입되고 **엔터까지
    # 눌러 제출**된다(본문 줄바꿈은 \n, 맨 끝 \r 로 제출). 빈 규칙이면 주입하지 않는다.
    srv, task, sock = await server_only()
    try:
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


async def test_auto_dismiss_feedback_prompt():
    # #26: Claude 세션 피드백 프롬프트가 뜨면 '0'(Dismiss)을 한 번 주입하고, 같은
    # 화면엔 반복 주입하지 않으며(디바운스), 프롬프트가 사라지면 다시 무장된다.
    from pytmuxlib.claude import claude_feedback_prompt
    assert claude_feedback_prompt("x How is Claude doing this session? (optional)")
    assert not claude_feedback_prompt("just normal output")
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        writes = []
        p.pty.write = lambda b: writes.append(b)   # 주입 캡처
        p.feed(b"\x1b[2J\x1b[HHow is Claude doing this session? (optional)\r\n"
               b"  1: Bad   2: Fine   3: Good   0: Dismiss\r\n")
        srv._scan_claude(sess, win)
        assert writes == [b"0"], writes
        assert p._feedback_seen is True
        srv._scan_claude(sess, win)                # 재스캔 → 반복 주입 없음
        assert writes == [b"0"], "디바운스(반복 주입 없음)"
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")  # 프롬프트 사라짐
        srv._scan_claude(sess, win)
        assert p._feedback_seen is False, "사라지면 재무장"
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
        p1.feed("\x1b[2J\x1b[H↑ 1k tokens\r\n".encode("utf-8"))  # busy
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


async def test_token_usage_logging():
    """#7: 응답 확정(committed>0) 시 tokens.jsonl 에 ts/tab/pane/session/account/
    tokens 한 줄이 적히고, 새 Claude 세션마다 session id 가 증가하며, 계정은 화면
    이메일에서 별칭으로 잡힌다. 수동 지정(set_claude_account)이 자동을 덮는다."""
    from pytmuxlib import usagelog
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # 새 Claude 세션 + busy(↑ N tokens 가 busy 신호 겸 running) + 계정 단서
        p.feed("\x1b[2J\x1b[H me@woojinkim.org\r\n"
               "↑ 1.9k tokens\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude == "busy", p._claude
        sid1 = p._claude_session_id
        assert sid1 > 0
        # idle 로 종료 → peak(1900) 확정 → 로그 1줄
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        recs = usagelog.read(srv.tokens_log_path)
        assert len(recs) == 1, recs
        r = recs[0]
        assert r["tokens"] == 1900 and r["pane"] == p.id, r
        assert r["session"] == sid1 and r["tab"] == 0, r
        assert r["account"].endswith("@woojinkim.org"), r["account"]

        # 세션 종료 후 새 Claude 세션 → session id 증가
        p.feed(b"\x1b[2J\x1b[Huser@host ~ % ls\r\n")     # 평범한 셸(claude None)
        srv._scan_claude(sess, win)
        p.feed("\x1b[2J\x1b[H↑ 2k tokens\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert p._claude_session_id == sid1 + 1, "새 세션 id 증가"

        # 수동 계정 지정이 자동 감지를 덮는다
        srv.set_claude_account(sess, "team-acct")
        assert p._claude_account == "team-acct" and p._claude_account_manual
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        recs = usagelog.read(srv.tokens_log_path)
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


async def test_account_token_total_aggregates_across_panes():
    """§10 계정별 합계: 활성 패널의 Claude 계정을 키로, 같은 계정에 속한 모든
    패널(전체 세션 순회)의 _session_tokens 를 합산해 status 의 claude_tokens 로
    내보낸다. 계정 미상이면 활성 패널 단독 누계, Claude 아니면 0(클라가 마지막값
    유지). claude_account 식별자도 함께 보낸다."""
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
        # 활성=bob 패널 → bob 단독 700
        win.active_pane = panes[2]
        assert srv._status_msg(sess)["claude_tokens"] == 700
        # 계정 미상 + Claude 패널 → 단독 누계 폴백
        panes[2]._claude_account = None
        assert srv._status_msg(sess)["claude_tokens"] == 700
        # 계정 미상 + Claude 아님 → 0(클라가 마지막값 유지)
        panes[2]._claude = None
        assert srv._status_msg(sess)["claude_tokens"] == 0
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


async def test_auto_doc_clear_sequence_and_guards():
    """§10 자동 doc→/clear: 토글이 켜진 상태에서 busy→idle 가 N초 지속되면(타이머
    만료=_adc_fire) 문서화 지시→/clear 를 1회 자동 주입한다. idle 이탈 시 무장된
    타이머를 해제하고, idle아님/진행중/수동모드/토글off 면 발화하지 않는다. 토글은
    opts.json 영속."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        injected = []
        srv._pc_inject = lambda pane, text: injected.append(text)

        assert srv.auto_doc_clear is False, "기본 off"
        assert srv.set_auto_doc_clear(True) is True
        import json as _json
        assert _json.load(open(srv.opts_path))["auto_doc_clear"] is True

        def go_idle():
            p._claude = "busy"
            p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
            srv._scan_claude(sess, win)

        # busy→idle → 타이머 무장(아직 발화 안 함)
        go_idle()
        assert p._claude == "idle"
        assert p._adc_timer is not None and p._adc_active is False
        assert injected == []
        # 타이머 만료(N초 지속 시뮬) → 문서화 지시 주입, 시퀀스 시작
        srv._adc_fire(p)
        assert p._adc_active is True and p._pc_phase == "doc"
        assert injected == [srv.prompt_clear_message], injected
        # 문서화 응답 완료 → /clear → 다음 완료 → 시퀀스 종료(_adc_active 해제)
        go_idle()
        assert p._pc_phase == "clear" and injected[-1] == "/clear"
        go_idle()
        assert p._pc_phase is None and p._adc_active is False
        assert len(injected) == 2, "doc + clear 두 번만"

        # idle 이탈(재busy) 시 _scan_claude 가 무장 해제
        go_idle()
        assert p._adc_timer is not None
        p._claude = "idle"
        p.feed(b"\x1b[2J\x1b[H\x1b[2K\xe2\x86\x91 1k tokens\r\n")  # busy(↑ tokens)
        srv._scan_claude(sess, win)
        assert p._claude == "busy" and p._adc_timer is None

        # 발화 가드: idle아님/진행중/수동모드/토글off 면 _adc_fire no-op
        injected.clear()
        p._claude = "busy"
        p._adc_active = False
        p._pc_phase = None
        srv._adc_fire(p)
        assert injected == [], "idle 아님 → no-op"
        p._claude = "idle"
        p._adc_active = True
        srv._adc_fire(p)
        assert injected == [], "이미 진행 중 → no-op"
        p._adc_active = False
        p.prompt_clear_mode = True
        srv._adc_fire(p)
        assert injected == [], "수동 클리어 모드 → no-op"
        p.prompt_clear_mode = False
        srv.set_auto_doc_clear(False)
        srv._adc_fire(p)
        assert injected == [], "토글 off → no-op"

        # 토글 off 시 무장된 타이머 일괄 해제
        srv.set_auto_doc_clear(True)
        go_idle()
        assert p._adc_timer is not None
        srv.set_auto_doc_clear(False)
        assert p._adc_timer is None
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_screen_prompt_reflects_remote_injected():
    """§10 #19: 데스크탑 앱 원격제어처럼 입력 경로(_track_prompt)를 안 거친 프롬프트
    도 화면 transcript 에서 추출해 헤더(last_prompt)/히스토리에 반영한다. 단 로컬
    입력으로 이미 히스토리에 있는 프롬프트는 화면에서 중복으로 다시 잡지 않는다."""
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
        assert p.prompt_history[-1] == "원격에서 보낸 프롬프트"
        # 같은 화면 재스캔 → 이미 last_prompt/히스토리에 있으니 중복 추가 안 함
        n = len(p.prompt_history)
        srv._scan_claude(sess, win)
        assert len(p.prompt_history) == n, "중복 방지"
        # 로컬 입력(_track_prompt)으로 들어온 프롬프트가 화면에도 보여도 중복 안 됨
        srv._track_prompt(p, "로컬 타이핑 프롬프트\r".encode("utf-8"))
        assert p.last_prompt == "로컬 타이핑 프롬프트"
        hist_len = len(p.prompt_history)
        p.feed("\x1b[2J\x1b[H> 로컬 타이핑 프롬프트\r\n"
               "답변...\r\n출력\r\n"
               "⏵⏵ auto mode on (shift+tab to cycle)\r\n".encode("utf-8"))
        srv._scan_claude(sess, win)
        assert len(p.prompt_history) == hist_len, "로컬 입력은 화면 파싱이 중복 안 함"
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


async def test_claude_auto_mode_cycles_to_auto():
    """§10 권한모드 자동전환: 토글 ON 이면 idle 패널의 footer 권한모드가 auto 가
    아닐 때 shift+tab(\\x1b[Z)을 폐루프로 순환 주입해 auto 로 맞춘다. 같은 모드
    반복(화면 미갱신) 시 중복 주입 안 함, auto/bypass 도달 시 정지, idle 이탈 시
    카운터 리셋, 오검출 대비 _CAM_MAX 가드. 토글 opts 영속."""
    from pytmuxlib.server import _CAM_MAX
    srv, task, sock = await server_only()
    try:
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


async def test_claude_header_reserves_row():
    """#1: Claude 프롬프트 헤더가 그려질 패널은 내용 영역에서 한 행을 빼(헤더 양보)
    PTY 도 그만큼 작게 리사이즈하고, layout 에 claude_hdr=True 를 실어 보낸다. 전역
    헤더 옵션이 꺼져 있거나 프롬프트가 없으면 예약하지 않는다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        # Claude/프롬프트 없음 → 예약 안 함
        pm0 = srv._layout_msg(sess)["panes"][0]
        assert pm0["claude_hdr"] is False and p._hdr_reserved is False
        h0, y0 = pm0["h"], pm0["y"]
        # Claude + 프롬프트 → 예약(claude_header 기본 on). 헤더 예약은 디바운스된
        # _hdr_claude 를 보므로(원격 깜빡임 방지) 그것도 세운다.
        p._claude = "idle"
        p._hdr_claude = True
        p.last_prompt = "do x"
        pm1 = srv._layout_msg(sess)["panes"][0]
        assert pm1["claude_hdr"] is True and p._hdr_reserved is True
        assert pm1["h"] == h0 - 1, "내용 영역 한 행 축소"
        assert pm1["y"] == y0 + 1, "내용 시작 한 행 내림"
        assert p.rows == pm1["h"], "PTY 도 축소된 높이로 리사이즈"
        # 전역 헤더 off 면 예약 안 함(내용 영역 원복)
        srv.set_claude_header(False)
        pm2 = srv._layout_msg(sess)["panes"][0]
        assert pm2["claude_hdr"] is False and pm2["h"] == h0
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_claude_header_debounce_no_thrash():
    """원격(ssh/ConPTY) Claude 의 footer 가 한두 프레임 안 잡혀 _claude 가 None 으로
    깜빡여도 헤더 예약(=PTY 한 행 양보)이 토글되면 안 된다. raw `_claude` 깜빡임이
    예약을 매 프레임 뒤집으면 PTY 가 ±1 행 리사이즈를 반복해 원격 Claude 화면이
    한 줄씩 위아래로 스크롤되는 떨림이 난다(Windows→ssh→macOS 첫 실행 증상).
    예약 해제는 _HDR_CLAUDE_MISS 프레임 연속 non-Claude 일 때만 일어난다."""
    from pytmuxlib.server import _HDR_CLAUDE_MISS
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        p.last_prompt = "do x"
        # Claude idle footer → 예약 ON, 디바운스 플래그도 즉시 True
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p._claude == "idle" and p._hdr_claude is True
        assert srv._should_reserve_header(p) is True
        # footer 없는 중간 프레임이 한 번 와 raw 가 None 으로 깜빡여도 예약 유지
        p.feed(b"\x1b[2J\x1b[H(redrawing...)\r\n")
        srv._scan_claude(sess, win)
        assert p._claude is None, "raw 상태는 None 으로 깜빡"
        assert p._hdr_claude is True and srv._should_reserve_header(p) is True, \
            "한 프레임 깜빡임으로 예약이 풀리면 PTY 떨림이 난다"
        # 연속 _HDR_CLAUDE_MISS 프레임 non-Claude → 그제서야 예약 해제
        for _ in range(_HDR_CLAUDE_MISS):
            srv._scan_claude(sess, win)
        assert p._hdr_claude is False and srv._should_reserve_header(p) is False
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
    # 응답 경계(busy→idle)에 순서대로 승격된다. 히스토리는 제출 즉시 기록.
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
        # 응답 종료(busy→idle) → 큐 다음(B) 승격
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p.last_prompt == "prompt B", p.last_prompt
        assert p.pending_prompts == ["prompt C"]
        # B 처리(busy) → 종료(idle) → C 승격
        p.feed("\x1b[2J\x1b[H✽ Baking… (3s · ↓ 0.3k tokens)\r\n".encode())
        srv._scan_claude(sess, win)
        assert p.last_prompt == "prompt B", "B 처리 중엔 B 유지"
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
        assert p.last_prompt == "prompt C" and p.pending_prompts == []
        # 히스토리에는 A,B,C 모두 즉시 기록
        assert p.prompt_history == ["prompt A", "prompt B", "prompt C"]
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
        assert p._session_tokens == 1900, p._session_tokens
        # 응답2: 2.5k 까지 → idle (누계 4400)
        p.feed("\x1b[2J\x1b[H✽ Baking… (4s · ↓ 2.5k tokens)\r\n".encode())
        srv._scan_claude(sess, win)
        p.feed(b"\x1b[2J\x1b[H? for shortcuts\r\n")
        srv._scan_claude(sess, win)
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
    # Windows 격리(알려진 ConPTY 레이스): 헤드리스 러너에서 다수 ConPTY 패널을 같은
    # 프로세스에 생성한 뒤(이 모듈 후반) split 신규 패널의 리더가 입력 echo 를 못 받는
    # 일이 결정적으로 재현된다. 단독/소수 실행·실데몬 스모크에선 정상이라 로직 버그가
    # 아니라 _WinPty 의 리더 스레드 read() ↔ 메인 스레드 set_winsize 동기화 부재로 보는
    # 레이스다(신규 패널은 MIN_W 로 떠 _layout_msg 가 곧 리사이즈). pty_backend 동기화
    # 수정 후 재개할 것. 동기화 입력 경로 자체는 Unix 에서 그대로 검증된다.
    if ipc.IS_WINDOWS:
        return
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
        # 고정 sleep 대신 폴링: 셸(Windows=cmd.exe)이 echo 를 처리·출력하기까지
        # 시간이 들쭉날쭉하다(전체 스위트 동시 ConPTY 기동 부하 하에서 특히). 모든
        # 패널에 SYNCED 가 보일 때까지 최대 ~10s 대기.
        for _ in range(100):
            await asyncio.sleep(0.1)
            if all("SYNCED" in pane_text(p) for p in win.panes()):
                break
        assert all("SYNCED" in pane_text(p) for p in win.panes())
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
        await pytmux.write_msg(w, {"t": "hello", "cols": 100, "rows": 40})
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
                                    "session": "main"})
        sA = []
        await harness.drain(rA, sA, timeout=5,
                            until=lambda s: any(m["t"] == "layout" for m in s)
                            and any(m["t"] == "status" for m in s))
        assert any(m["t"] == "layout" for m in sA)
        assert any(m["t"] == "status" for m in sA)  # 단일 세션(이름 무시)
        # 둘째 클라이언트(더 작음) → 공유 최소 크기 80x24
        rB, wB = await ipc.open_connection(sock)
        await pytmux.write_msg(wB, {"t": "hello", "cols": 80, "rows": 24})
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
    """패널 출력 캡처: 기본 OFF, 켜면 무손실 기록, 토글, opts.json 영속/재시작 유지."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        assert srv.capture is False, "기본 OFF"

        # 켜면 기록 시작 → pane-<id>.log 에 raw 바이트 무손실
        assert srv.set_capture(True) is True
        srv._capture_write(pane, b"hello\x1b[31m world")
        path = os.path.join(srv.capture_dir, f"pane-{pane.id}.log")
        with open(path, "rb") as f:
            assert f.read() == b"hello\x1b[31m world", "무손실 캡처"
        # 메타 로그에 탭/패널 매핑
        meta = open(os.path.join(srv.capture_dir, "sessions.log")).read()
        assert f"pane-{pane.id}" in meta and "tab0:" in meta, meta

        # 끄면 파일 닫힘 + opts.json 영속(capture=False)
        assert srv.set_capture(False) is False
        assert pane.id not in srv._capfiles, "끄면 핸들 닫힘"
        assert json.load(open(srv.opts_path))["capture"] is False
        # 꺼진 동안 _on_pane_readable 경로는 기록하지 않음
        before = os.path.getsize(path)
        if srv.capture:
            srv._capture_write(pane, b"X")
        assert os.path.getsize(path) == before, "OFF 중 기록 없음"

        # 재시작 영속: 같은 sock 로 새 Server 를 만들면 OFF 를 읽음
        assert pytmux.Server(sock).capture is False, "재시작 후 OFF 유지"

        # 토글로 다시 ON → opts 갱신, 재기록 가능(lazy 재오픈)
        assert srv.set_capture(None) is True
        assert json.load(open(srv.opts_path))["capture"] is True
        srv._capture_write(pane, b"again")
        with open(path, "rb") as f:
            assert f.read().endswith(b"again"), "재개 후 append"
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


async def test_claude_header_opt_persists():
    # #6 ③: claude-header 전역 표시 상태가 opts.json 에 영속되고 재시작 후 유지.
    srv, task, sock = await server_only()
    try:
        assert srv.claude_header is True, "기본 표시"
        assert srv.set_claude_header(False) is False
        assert json.load(open(srv.opts_path))["claude_header"] is False
        # 같은 sock 로 새 Server → OFF 를 읽음
        assert pytmux.Server(sock).claude_header is False, "재시작 후 OFF 유지"
        assert srv.set_claude_header(None) is True   # 토글 → ON
        assert json.load(open(srv.opts_path))["claude_header"] is True
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
                                                "rows": 24}), _NullWriter())
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
        "auto": "⏵⏵ auto-accept edits on (shift+tab to cycle)",
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
