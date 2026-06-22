"""§10-D P3/P4 servermixin 배선: 패널→트랜스크립트 매핑 캐시 + _scan_claude 증분
적재(usage_xc) 테스트.

스크랩 footer 누계(usage 테이블)와 **병행**으로, 응답 종료 시 패널의 Claude
트랜스크립트(~/.claude/projects/*.jsonl)에서 4항목 토큰을 usage_xc 로 멱등 적재한다.
실 ps/lsof·실 파일은 transcript.find_transcript 와 _pane_cwd 를 몽키패치해 헤드리스로
검증한다(부수효과 주입은 transcript 모듈 설계의 일부).
"""
import json
import os
import tempfile

import harness  # noqa: F401  (경로 설정 + 플러그인 별칭 등록)
from harness import server_only, teardown
from pytmuxlib import transcript, usagedb

_BUSY = "\x1b[2J\x1b[H✽ Crunching… (3s · ↑ 1k tokens)\r\n".encode("utf-8")
_IDLE = b"\x1b[2J\x1b[H? for shortcuts\r\n"
_ORIG_FIND = transcript.find_transcript   # 몽키패치 후 복원(다른 스위트 누출 방지)


def _evt(msg_id, req, inp=100, out=50, cc=0, cr=0, model="claude-opus-4-8"):
    return json.dumps({
        "type": "assistant", "uuid": f"u-{msg_id}", "requestId": req,
        "timestamp": "2026-06-22T10:00:00.000Z", "sessionId": "sess-1",
        "message": {"id": msg_id, "model": model, "usage": {
            "input_tokens": inp, "output_tokens": out,
            "cache_creation_input_tokens": cc,
            "cache_read_input_tokens": cr}}}) + "\n"


def _append(path, *lines):
    with open(path, "a", encoding="utf-8") as fh:
        for ln in lines:
            fh.write(ln)


async def _turn(srv, sess, win, pane):
    """busy → idle 한 턴 구동(committed>0 → 강제 트랜스크립트 테일)."""
    pane.feed(_BUSY)
    srv._scan_claude(sess, win)
    pane.feed(_IDLE)
    srv._scan_claude(sess, win)


async def test_commit_force_tails_transcript_into_usage_xc():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, "sess-1.jsonl")
            open(jp, "w").close()
            transcript.find_transcript = lambda pid, cwd: jp   # 매핑 주입
            srv._pane_cwd = lambda pane: d                      # 느린 subprocess 회피
            # 턴이 끝나기 전 Claude 가 usage 레코드를 jsonl 에 기록한 상태를 모사.
            _append(jp, _evt("m1", "r1", inp=10, out=5, cr=985))   # full 1000
            await _turn(srv, sess, win, p)
            conn = srv._tokens_db_conn()
            assert usagedb.xc_count(conn) == 1
            t = usagedb.xc_totals(conn)
            assert t["full"] == 1000 and t["cache_read"] == 985
            assert t["footer"] == 15                            # in+out 근사
    finally:
        transcript.find_transcript = _ORIG_FIND
        await teardown(srv, task, sock)


async def test_incremental_tail_no_double_count():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, "sess-1.jsonl")
            open(jp, "w").close()
            transcript.find_transcript = lambda pid, cwd: jp
            srv._pane_cwd = lambda pane: d
            _append(jp, _evt("m1", "r1", cr=1000))
            await _turn(srv, sess, win, p)
            conn = srv._tokens_db_conn()
            assert usagedb.xc_count(conn) == 1
            # 새 줄 없이 한 턴 더 → offset 으로 재읽기 없음(중복 없음).
            await _turn(srv, sess, win, p)
            assert usagedb.xc_count(conn) == 1
            # 새 레코드 1건 추가 → 증분으로 그것만 적재.
            _append(jp, _evt("m2", "r2", cr=2000))
            await _turn(srv, sess, win, p)
            assert usagedb.xc_count(conn) == 2
            # 같은 xkey 중복 줄이 들어와도 멱등(INSERT OR IGNORE).
            _append(jp, _evt("m2", "r2", cr=2000))
            await _turn(srv, sess, win, p)
            assert usagedb.xc_count(conn) == 2
    finally:
        transcript.find_transcript = _ORIG_FIND
        await teardown(srv, task, sock)


async def test_no_transcript_path_is_noop_and_safe():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        transcript.find_transcript = lambda pid, cwd: None   # 매핑 실패
        srv._pane_cwd = lambda pane: None
        await _turn(srv, sess, win, p)                        # 예외 없이 무동작
        conn = srv._tokens_db_conn()
        assert usagedb.xc_count(conn) == 0
    finally:
        transcript.find_transcript = _ORIG_FIND
        await teardown(srv, task, sock)


async def test_path_resolution_is_cached_not_per_scan():
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, "sess-1.jsonl")
            open(jp, "w").close()
            calls = {"n": 0}

            def _find(pid, cwd):
                calls["n"] += 1
                return jp
            transcript.find_transcript = _find
            srv._pane_cwd = lambda pane: d
            _append(jp, _evt("m1", "r1", cr=1000))
            # 여러 턴(강제 테일 다회) — lsof/ps 해석은 캐시로 1회만(첫 해석).
            for _ in range(4):
                await _turn(srv, sess, win, p)
            assert calls["n"] == 1, f"경로 해석은 캐시돼야(호출 {calls['n']}회)"
    finally:
        transcript.find_transcript = _ORIG_FIND
        await teardown(srv, task, sock)


async def test_cursor_persisted_resumes_across_reconnect():
    # set_xc_cursor 가 offset 을 영속 → 새 패널이 같은 경로를 0 부터 재적재하지 않는다.
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        p = win.active_pane
        with tempfile.TemporaryDirectory() as d:
            jp = os.path.join(d, "sess-1.jsonl")
            open(jp, "w").close()
            transcript.find_transcript = lambda pid, cwd: jp
            srv._pane_cwd = lambda pane: d
            _append(jp, _evt("m1", "r1", cr=1000))
            await _turn(srv, sess, win, p)
            conn = srv._tokens_db_conn()
            cur = usagedb.get_xc_cursor(conn, jp)
            assert cur is not None and cur[0] == os.path.getsize(jp)
            # 패널 offset 캐시를 잃은(재접속) 패널을 모사 — DB 커서로 이어 받아
            # 같은 줄을 재적재하지 않는다.
            p._xc_offset = None
            await _turn(srv, sess, win, p)
            assert usagedb.xc_count(conn) == 1
    finally:
        transcript.find_transcript = _ORIG_FIND
        await teardown(srv, task, sock)
