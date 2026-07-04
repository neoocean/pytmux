"""런타임 보안 검사(Tier 1) — 살아있는 서버에 raw 소켓으로 공격을 던져 정적 검토의
불변식을 *실측*한다(docs/internal/SECURITY_REVIEW.md).

정적 검토는 "토큰 없으면 거절·MAX_FRAME 으로 OOM 차단·clamp_dim·비-dict 가드·악성
연발에도 서버 생존"을 코드로 *추론*했다. 여기선 `server_only()` 로 진짜 서버를 띄우고
프로토콜 클라이언트를 흉내 낸 적대 클라(인증 우회·프레임 퍼징·자원 연발)를 붙여,
서버가 ① 무인가/손상 입력을 깨끗이 거절하고 ② 그래도 살아 있으며 ③ 라이브 상태파일
권한이 0600 임을 단언한다. 전 경로 POSIX/TCP 공통(Windows 는 TCP 분기).

씨앗: test_robustness.py(깨진 JSON·미지 액션). 인증 게이트 단위 검증은 F1/F2/M1 의
test_server.py·test_ptyhost_auth.py 에 있고, 여기선 *공격자 시점의 와이어 왕복*을 본다.
"""
import asyncio
import os
import stat

import harness
from harness import server_only, teardown
from pytmuxlib import ipc, protocol
from pytmuxlib.protocol import MAX_FRAME, MAX_W, MAX_H


# ---- raw 적대 클라 헬퍼 ----
async def _open(endpoint):
    kind = ipc.parse_endpoint(endpoint)
    if kind[0] == "unix":
        return await asyncio.open_unix_connection(kind[1])
    return await asyncio.open_connection(kind[1], kind[2])


async def _send(w, obj):
    w.write(protocol.frame_msg(obj))
    await w.drain()


async def _recv(r, timeout=2.0):
    """다음 프레임(dict) 또는 None(서버가 끊음). 타임아웃도 None 으로 환원."""
    try:
        return await asyncio.wait_for(protocol.read_msg(r), timeout)
    except asyncio.TimeoutError:
        return None


async def _server_alive(srv, endpoint) -> bool:
    """올바른 토큰으로 list 왕복이 성립하면 서버가 살아있다(악성 입력 뒤 생존 확인)."""
    if not srv.running:
        return False
    tok = ipc.read_token(endpoint)
    try:
        r, w = await _open(endpoint)
    except OSError:
        return False
    try:
        await _send(w, {"t": "list", "token": tok})
        reply = await _recv(r)
        return isinstance(reply, dict) and reply.get("t") == "list"
    finally:
        w.close()


# ---- A) 인증 우회 배터리 ----
async def test_unauth_hello_rejected_server_alive():
    srv, task, ep = await server_only()
    try:
        r, w = await _open(ep)
        await _send(w, {"t": "hello", "cols": 80, "rows": 24})   # 토큰 없음
        msg = await _recv(r)
        assert msg == {"t": "error", "error": "auth_failed"}, msg
        w.close()
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


async def test_unauth_control_kill_rejected_no_exec():
    # 무인가 control(kill-server)은 인증 게이트에서 막혀 서버가 안 죽는다(F1/F2 핵심).
    srv, task, ep = await server_only()
    try:
        n_sess = len(srv.sessions)
        r, w = await _open(ep)
        await _send(w, {"t": "control", "line": "kill-server"})   # 토큰 없음
        msg = await _recv(r)
        assert msg == {"t": "error", "error": "auth_failed"}, msg
        w.close()
        assert srv.running and not task.done()
        assert len(srv.sessions) == n_sess
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


async def test_wrong_token_rejected():
    srv, task, ep = await server_only()
    try:
        r, w = await _open(ep)
        await _send(w, {"t": "hello", "token": "00" * 32, "cols": 80, "rows": 24})
        msg = await _recv(r)
        assert msg == {"t": "error", "error": "auth_failed"}, msg
        w.close()
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


async def test_valid_token_hello_accepted():
    # 해피패스: 올바른 토큰 hello 는 받아들여진다(위 거절들이 유의미함을 보장).
    srv, task, ep = await server_only()
    try:
        tok = ipc.read_token(ep)
        r, w = await _open(ep)
        await _send(w, {"t": "hello", "token": tok, "cols": 80, "rows": 24})
        msg = await _recv(r)                 # 초기 layout/screen 류 — auth_failed 아님
        assert isinstance(msg, dict) and msg.get("t") != "error", msg
        w.close()
    finally:
        await teardown(srv, task, ep)


async def test_authed_control_nonstr_line_clean_no_traceback():
    # R3(런타임 발견, SECURITY_REVIEW §11): 유효 토큰 control 의 line 이 비-str(int/None/
    # list)이면 handle_control 의 shlex.split 이 AttributeError 로 새 handle_client 밖으로
    # 전파됐다(트레이스백 노이즈 + 연결 비정상 드롭). 가드 후 깨끗한 ok 응답 + 서버 생존.
    srv, task, ep = await server_only()
    try:
        tok = ipc.read_token(ep)
        for bad_line in (1234, None, [1, 2], {"x": 1}):
            r, w = await _open(ep)
            await _send(w, {"t": "control", "line": bad_line, "token": tok})
            reply = await _recv(r)
            # auth_failed 가 아니라 정상 ok(result="bad-line")로 거절돼야 한다.
            assert isinstance(reply, dict) and reply.get("t") == "ok", (bad_line, reply)
            assert reply.get("result") == "bad-line", (bad_line, reply)
            w.close()
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


# ---- B) 프로토콜 프레임 퍼징 ----
async def test_oversized_length_prefix_no_oom_drop():
    # MAX_FRAME 초과 길이프리픽스 → 서버가 수 GiB 할당 시도 없이 연결만 끊는다(F6).
    srv, task, ep = await server_only()
    try:
        r, w = await _open(ep)
        w.write((MAX_FRAME + 1).to_bytes(4, "big") + b"x")
        await w.drain()
        assert await _recv(r) is None          # 서버가 끊음(None)
        w.close()
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


async def test_non_json_frame_dropped():
    srv, task, ep = await server_only()
    try:
        r, w = await _open(ep)
        body = b"\xff\xfe not json {"
        w.write(len(body).to_bytes(4, "big") + body)
        await w.drain()
        assert await _recv(r) is None
        w.close()
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


async def test_non_dict_first_frame_dropped_cleanly():
    # 비-dict JSON 첫 프레임(리스트/정수)은 first.get(...) AttributeError 없이 깨끗이
    # 끊겨야 한다(serverio 비-dict 가드 회귀). 서버 생존.
    srv, task, ep = await server_only()
    try:
        for bad in ([1, 2, 3], 42, "hello", None):
            r, w = await _open(ep)
            await _send(w, bad)
            assert await _recv(r) is None, bad
            w.close()
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


async def test_giant_dims_clamped_runtime():
    # 거대 cols/rows hello → 서버가 clamp_dim 으로 [MIN,MAX] 안에 가둔다(레이아웃 메모리
    # 폭증 차단). 살아있는 ClientConn 의 실제 치수를 관찰해 단언한다.
    srv, task, ep = await server_only()
    try:
        tok = ipc.read_token(ep)
        r, w = await _open(ep)
        await _send(w, {"t": "hello", "token": tok,
                        "cols": 999999, "rows": 999999})
        await _recv(r)                          # 서버가 hello 처리(append)할 시간
        for _ in range(50):
            if srv.clients:
                break
            await asyncio.sleep(0.01)
        assert srv.clients, "hello 가 ClientConn 으로 채택되지 않음"
        c = srv.clients[-1]
        assert c.cols <= MAX_W and c.rows <= MAX_H, (c.cols, c.rows)
        w.close()
    finally:
        await teardown(srv, task, ep)


async def test_malformed_flood_keeps_server_responsive():
    # 손상 프레임 다연발(여러 연결) 후에도 서버가 wedge 되지 않고 응답한다.
    srv, task, ep = await server_only()
    try:
        for i in range(24):
            r, w = await _open(ep)
            if i % 2:
                w.write((MAX_FRAME + 7).to_bytes(4, "big"))      # 거대 길이
            else:
                w.write(b"\x00\x00\x00\x05bad!!")                # 비-JSON
            await w.drain()
            w.close()
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


# ---- C) 라이브 상태파일 권한 ----
async def test_live_token_and_socket_are_0600():
    # 부팅된 서버가 게시한 토큰(과 Unix 소켓)이 0600 인지 런타임 stat 으로 확인(F1/F4/F5).
    if not hasattr(os, "getuid"):
        return                                  # POSIX 권한 모델 한정
    srv, task, ep = await server_only()
    try:
        tpath = ipc.token_path(ep)
        assert os.path.exists(tpath), tpath
        assert stat.S_IMODE(os.lstat(tpath).st_mode) == 0o600, oct(
            os.lstat(tpath).st_mode)
        if not ipc.is_tcp(ep):                  # Unix 소켓도 0600
            assert stat.S_IMODE(os.lstat(ep).st_mode) == 0o600, oct(
                os.lstat(ep).st_mode)
    finally:
        await teardown(srv, task, ep)


async def test_preauth_frame_capped_below_max_frame():
    """M2(보안검수 2026-07-03): 인증 전 프레임은 HANDSHAKE_MAX_FRAME(64KiB)로 상한.
    MAX_FRAME(64MiB)보다 작지만 핸드셰이크 상한을 넘는 길이(예: 1MiB)를 광고하면 서버가
    버퍼링 없이 끊는다 — Windows 루프백 비인가 로컬 사용자의 대용량-할당 pre-auth DoS 차단."""
    from pytmuxlib.protocol import HANDSHAKE_MAX_FRAME
    assert HANDSHAKE_MAX_FRAME < MAX_FRAME
    srv, task, ep = await server_only()
    try:
        r, w = await _open(ep)
        w.write((HANDSHAKE_MAX_FRAME + 1).to_bytes(4, "big") + b"x")
        await w.drain()
        assert await _recv(r) is None          # 서버가 끊음(버퍼링 안 함)
        w.close()
        assert await _server_alive(srv, ep)
    finally:
        await teardown(srv, task, ep)


async def test_preauth_handshake_timeout_drops_stalled_conn():
    """M2: 인증 전 연결이 첫 프레임을 안 보내고 매달려 있으면(slowloris) 핸드셰이크
    타임아웃으로 끊는다 — 무한 대기 코루틴 누적 방지. 서버 생존."""
    import pytmuxlib.serverio as serverio
    orig = serverio.HANDSHAKE_TIMEOUT
    serverio.HANDSHAKE_TIMEOUT = 0.3
    srv, task, ep = await server_only()
    try:
        r, w = await _open(ep)
        w.write(b"\x00\x00")    # 4바이트 길이헤더 미완성 → 서버는 타임아웃 후 끊어야
        await w.drain()
        assert await _recv(r, timeout=2.0) is None
        w.close()
        assert await _server_alive(srv, ep)
    finally:
        serverio.HANDSHAKE_TIMEOUT = orig
        await teardown(srv, task, ep)


async def test_preauth_conn_cap_rejects_over_limit():
    """S1(2026-07-04, 레드팀 M2-c): 인증 전 동시 연결수가 _MAX_PREAUTH_CONNS 를 넘으면
    새 핸드셰이크를 즉시 끊는다(연결 개수 고갈 slowloris 방어). 카운트는 핸드셰이크 읽기
    구간만 감싸 인증된 클라 수명은 세지 않고, 종료 후 원복돼 누수가 없다."""
    import asyncio
    import pytmuxlib.serverio as serverio

    class _W:
        def __init__(self):
            self.closed = False

        def write(self, *_a):
            pass

        async def drain(self):
            pass

        def close(self):
            self.closed = True

    srv, task, sock = await server_only()
    try:
        # ① 캡 도달 → 새 연결 즉시 close(읽기 시도조차 안 함), 카운트 불변
        srv._preauth_conns = serverio._MAX_PREAUTH_CONNS
        r, w = asyncio.StreamReader(), _W()   # r 에 아무것도 안 먹임(읽으면 매달림)
        await srv.handle_client(r, w)
        assert w.closed, "캡 초과 연결은 즉시 끊어야"
        assert srv._preauth_conns == serverio._MAX_PREAUTH_CONNS, "거부는 카운트 증감 없음"

        # ② 캡 미만 + 첫 프레임 EOF → 정상 경로로 끊고 카운트 원복(finally 누수 없음)
        srv._preauth_conns = 0
        r2 = asyncio.StreamReader()
        r2.feed_eof()
        w2 = _W()
        await srv.handle_client(r2, w2)
        assert w2.closed
        assert srv._preauth_conns == 0, "핸드셰이크 종료 후 카운트 원복(누수 없음)"
    finally:
        await teardown(srv, task, sock)


async def test_harden_win_acl_runs_once_per_path():
    """L7 후속(2026-07-03 os-compat fd 회귀): _harden_win_acl 은 경로별 **1회만** icacls 를
    스폰한다. default_state_dir 이 소켓/포트/토큰/state_base·에러로그 경로 해석에서 반복
    호출되므로, 여기에 무조건 서브프로세스를 걸면 연결마다 Windows 핸들 churn 이 나
    red-team 배터리 fd 증가가 임계(16)를 넘었다(Δ17~22). 메모이즈로 방지한다."""
    import subprocess
    calls = []
    orig_run, orig_win = subprocess.run, ipc.IS_WINDOWS
    orig_set = set(ipc._win_acl_hardened)

    def fake_run(*a, **k):
        calls.append(a[0] if a else k.get("args"))
        class _R:
            returncode = 0
        return _R()
    try:
        ipc.IS_WINDOWS = True
        subprocess.run = fake_run
        ipc._win_acl_hardened.clear()
        for _ in range(5):                       # 같은 경로 반복 호출(핫패스 모사)
            ipc._harden_win_acl(r"C:\fake\statedir", is_dir=True)
        assert len(calls) == 1, calls            # 경로당 1회만 스폰
        ipc._harden_win_acl(r"C:\fake\default.token")   # 다른 경로 → +1
        assert len(calls) == 2, calls
    finally:
        subprocess.run, ipc.IS_WINDOWS = orig_run, orig_win
        ipc._win_acl_hardened.clear()
        ipc._win_acl_hardened.update(orig_set)
