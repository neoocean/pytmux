"""토큰 동기화 P2 — 클라 워커(limits push/pull) E2E + 검증.

설계 §5.6. **소켓 없이** 클라이언트의 transport 를 서버 앱(`SyncApp.handle`)에 직접
물려 두 머신을 한 프로세스에서 돌린다 — 실제 코드 경로(서명·인가·멱등·복호)는 전부
지나가면서 네트워크 플레이크가 없다.

되돌리면 실패해야 하는 오라클:
  · 화이트리스트 직렬화를 dict 통째 dump 로 바꾸면 → test_payload_is_whitelisted 실패
  · 커서를 실패 시에도 전진시키면 → test_cursor_not_advanced_on_failure 실패
  · import_limits 를 insert_limits(로컬 dedup 가드)로 바꾸면 → test_pull_merges 실패
  · seq 후퇴 방어를 빼면 → test_rejects_server_cursor_rollback 실패(무한 루프 대신)
"""
import json
import os
import sys
import tempfile

import harness  # noqa: F401
from pytmuxlib import syncrypto, usagedb
from pytmuxlib import tokensync

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from synserver import app as sapp        # noqa: E402
from synserver import db as sdb          # noqa: E402
from synserver import webauthnlib as wa  # noqa: E402

from test_synserver_app import Clock, _enroll, _j   # noqa: E402
from test_synserver_webauthn import RP_ID, ORIGIN   # noqa: E402


def _server():
    conn = sdb.connect(":memory:")
    clock = Clock()
    return sapp.SyncApp(conn, RP_ID, ORIGIN, now=clock), clock


def _transport(app):
    """클라 transport → 서버 handle 직결(HTTP 계층만 생략)."""
    def send(method, path, query="", body=b"", headers=None):
        return app.handle(method, path, query, headers or {}, body or b"")
    return send


class Machine:
    """머신 하나 = 토큰 DB + 동기화 클라이언트."""

    def __init__(self, app, clock, code, master=None):
        self.dir = tempfile.mkdtemp(prefix="pytmux-sync-m-")
        self.conn = usagedb.connect(os.path.join(self.dir, "tokens.db"))
        if master is not None:
            syncrypto.save_master(os.path.join(self.dir, "sync_vault.key"), master)
        self.cli = tokensync.SyncClient(self.conn, self.dir, _transport(app),
                                        now=clock)
        self.cli.enroll(code)

    def add_limits(self, ts, pct, account="me@example.com", source="probe"):
        usagedb.insert_limits(self.conn, {
            "ts": ts, "account": account, "session_pct": pct,
            "session_reset": "3am", "week_all_pct": pct // 2,
            "source": source}, host=self.cli.host_id)


def _pair_code(app, cookie):
    return _j(app.handle("POST", "/v1/pairing", headers=cookie))[1]["code"]


def _two_machines():
    """같은 vault·같은 마스터 키를 쓰는 머신 2대(= 같은 사람의 노트북/데스크톱)."""
    app, clock = _server()
    cookie, _vault, _auth = _enroll(app)
    m1 = Machine(app, clock, _pair_code(app, cookie))
    master = syncrypto.load_or_create_master(
        os.path.join(m1.dir, "sync_vault.key"))
    m2 = Machine(app, clock, _pair_code(app, cookie), master=master)
    return app, clock, m1, m2


# ── 순수 함수(직렬화·검증) ─────────────────────────────────────────────────

async def test_payload_is_whitelisted():
    """전송 레코드는 **정해진 필드만** 싣는다 — 나중에 컬럼이 늘어도 조용히 새 필드가
    새 나가지 않게(§8.1)."""
    row = {"ts": 100.0, "account": "a@b.c", "session_pct": 10,
           "session_reset": "3am", "week_all_pct": 5, "week_all_reset": None,
           "week_sonnet_pct": None, "week_sonnet_reset": None,
           "source": "probe", "host": None,
           "secret_new_column": "이건 나가면 안 된다"}
    out = tokensync._limits_payload(row, "hostA")
    assert set(out) == set(tokensync._LIM_FIELDS) | {"v"}
    assert "secret_new_column" not in out
    assert out["host"] == "hostA"          # host 없으면 자기 것으로 채움
    assert json.dumps(out)                 # 직렬화 가능


async def test_validate_rejects_adversarial():
    now = 1_000_000.0
    ok = {"v": 1, "ts": now, "source": "probe", "account": "a@b.c",
          "session_pct": 42, "host": "h1"}
    assert tokensync._validate_limits(dict(ok), now)["session_pct"] == 42
    bad = [
        dict(ok, v=2),                                  # 버전 불일치
        dict(ok, ts=now + 400 * 24 * 3600),             # 미래 창 밖
        dict(ok, ts=now - 400 * 24 * 3600),             # 과거 창 밖
        dict(ok, ts="어제"),                             # 타입
        dict(ok, session_pct=-1),                       # 음수
        dict(ok, session_pct=10 ** 9),                  # 거대값
        dict(ok, session_pct=True),                     # bool 은 숫자가 아니다
        dict(ok, source=""),                            # 빈 source
        dict(ok, source="x" * 100),                     # 과장 길이
        dict(ok, account="a" * 500),
        dict(ok, host="h" * 100),
        dict(ok, session_reset=123),                    # 타입
        "문자열",
    ]
    for b in bad:
        assert tokensync._validate_limits(b, now) is None, b


# ── E2E: 머신 2대 ──────────────────────────────────────────────────────────

async def test_push_pull_merges_between_machines():
    app, clock, m1, m2 = _two_machines()
    m1.add_limits(1_000_000.0, 40)
    m1.add_limits(1_000_100.0, 55)
    up = m1.cli.push_limits()
    assert up["sent"] == 2 and up["accepted"] == 2
    down = m2.cli.pull()
    assert down["merged"] == 2 and down["rejected"] == 0
    rows = usagedb.query_limits(m2.conn)
    assert sorted(r["session_pct"] for r in rows) == [40, 55]
    # 원산지 host 가 보존된다(누가 관측했는지 잃지 않는다).
    hosts = {r["host"] for r in m2.conn.execute("SELECT host FROM limits")}
    assert hosts == {m1.cli.host_id}


async def test_pull_is_idempotent_and_resumes():
    app, clock, m1, m2 = _two_machines()
    m1.add_limits(1_000_000.0, 40)
    m1.cli.push_limits()
    assert m2.cli.pull()["merged"] == 1
    assert m2.cli.pull()["merged"] == 0            # 커서 재개 — 중복 0
    assert usagedb.limits_count(m2.conn) == 1
    # 커서를 0 으로 되돌려 전량 재수신해도 행이 늘지 않는다(lkey 멱등).
    usagedb.set_sync_remote(m2.conn, tokensync.SyncClient.REMOTE, cursor="0")
    assert m2.cli.pull()["merged"] == 0
    assert usagedb.limits_count(m2.conn) == 1


async def test_push_does_not_echo_foreign_rows():
    """받은 행을 되돌려 보내지 않는다(무한 왕복 방지)."""
    app, clock, m1, m2 = _two_machines()
    m1.add_limits(1_000_000.0, 40)
    m1.cli.push_limits()
    m2.cli.pull()
    up = m2.cli.push_limits()
    assert up["sent"] == 0


async def test_own_rows_are_skipped_on_pull():
    app, clock, m1, m2 = _two_machines()
    m1.add_limits(1_000_000.0, 40)
    m1.cli.push_limits()
    before = usagedb.limits_count(m1.conn)
    out = m1.cli.pull()                 # 자기가 올린 것을 도로 받음
    assert out["merged"] == 0 and usagedb.limits_count(m1.conn) == before


async def test_account_whitelist_limits_what_leaves():
    app, clock, m1, m2 = _two_machines()
    m1.cli.accounts = ("work@corp.com",)
    m1.add_limits(1_000_000.0, 40, account="personal@example.com")
    m1.add_limits(1_000_100.0, 41, account="work@corp.com")
    up = m1.cli.push_limits()
    assert up["sent"] == 1
    m2.cli.pull()
    accts = {r["account"] for r in m2.conn.execute("SELECT account FROM limits")}
    assert accts == {"work@corp.com"}


# ── 실패 경로 ──────────────────────────────────────────────────────────────

async def test_cursor_not_advanced_on_failure():
    """업로드가 거부되면 커서를 전진시키지 않는다 — 그게 곧 재시도 큐다."""
    app, clock, m1, m2 = _two_machines()
    m1.add_limits(1_000_000.0, 40)
    m1.cli.transport = lambda *a, **kw: (503, {}, b'{"error":"down"}')
    try:
        m1.cli.push_limits()
    except tokensync.SyncError:
        pass
    else:
        raise AssertionError("실패가 조용히 성공으로 처리됐다")
    assert usagedb.get_export_cursor(m1.conn, "limits") == 0
    m1.cli.transport = _transport(app)
    assert m1.cli.push_limits()["accepted"] == 1     # 다음 주기에 그대로 올라간다


async def test_rejects_server_cursor_rollback():
    """서버가 seq 를 되돌리면 무한 pull 대신 **중단**한다(§9.11)."""
    app, clock, m1, m2 = _two_machines()
    m1.add_limits(1_000_000.0, 40)
    m1.cli.push_limits()
    m2.cli.pull()
    def bad(method, path, query="", body=b"", headers=None):
        if method == "GET":
            line = json.dumps({"seq": 1, "kind": "lim", "rkey": "aa",
                               "acct_id": None, "ct": "AAAA", "nonce": "AAAA"})
            return 200, {}, (line + "\n").encode()
        return app.handle(method, path, query, headers or {}, body or b"")
    m2.cli.transport = bad
    try:
        m2.cli.pull()
    except tokensync.SyncError as e:
        assert "커서" in str(e)
    else:
        raise AssertionError("커서 후퇴를 통과시켰다")


async def test_forged_records_are_rejected_without_polluting_db():
    """악성 서버가 조작 레코드를 돌려줘도 AEAD 가 전부 거른다(DB 무오염)."""
    app, clock, m1, m2 = _two_machines()
    other = syncrypto.gen_master()
    k_id, k_enc = syncrypto.derive_keys(other)      # 남의 키로 만든 레코드
    rk = syncrypto.rkey(k_id, "lim", "x")
    nonce, ct = syncrypto.seal(k_enc, syncrypto.aad("", "lim", rk, None),
                               json.dumps({"v": 1, "ts": 1_000_000.0,
                                           "source": "evil",
                                           "session_pct": 99}).encode())
    def evil(method, path, query="", body=b"", headers=None):
        if method == "GET":
            line = json.dumps({"seq": 7, "kind": "lim", "rkey": rk,
                               "acct_id": None,
                               "ct": tokensync._b64u(ct),
                               "nonce": tokensync._b64u(nonce)})
            return 200, {}, (line + "\n").encode()
        return app.handle(method, path, query, headers or {}, body or b"")
    m2.cli.transport = evil
    out = m2.cli.pull()
    assert out["merged"] == 0 and out["rejected"] == 1
    assert usagedb.limits_count(m2.conn) == 0
    st = usagedb.get_sync_remote(m2.conn, tokensync.SyncClient.REMOTE)
    assert st["last_err"]                    # 사유가 남는다(조용한 실패 금지)


async def test_not_enrolled_is_its_own_error():
    app, clock = _server()
    d = tempfile.mkdtemp(prefix="pytmux-sync-n-")
    conn = usagedb.connect(os.path.join(d, "t.db"))
    cli = tokensync.SyncClient(conn, d, _transport(app), now=clock)
    usagedb.insert_limits(conn, {"ts": 1_000_000.0, "account": "a@b.c",
                                 "session_pct": 1, "source": "probe"},
                          host=cli.host_id)
    try:
        cli.push_limits()
    except tokensync.NotEnrolled:
        pass
    else:
        raise AssertionError("미등록인데 통과했다")


async def test_plaintext_mode_is_refused():
    """`token_sync_encrypt=off` 는 아직 구현하지 않았다 — **조용히 평문을 올리지
    않고** 거부한다(설계 권고는 on 고정)."""
    app, clock, m1, _m2 = _two_machines()
    m1.cli.encrypt = False
    m1.add_limits(1_000_000.0, 40)
    try:
        m1.cli.push_limits()
    except tokensync.SyncError as e:
        assert "평문" in str(e)
    else:
        raise AssertionError("평문 업로드가 통과했다")


# ── 워커(비동기) ───────────────────────────────────────────────────────────

async def test_worker_skips_when_off_and_never_blocks_loop():
    """설정이 off 면 클라이언트를 만들지도 않는다. 그리고 켜졌을 때 블로킹 작업은
    **executor** 로 나간다(이벤트 루프에서 직접 부르면 서버가 멈춘다)."""
    import asyncio
    import threading

    def stop_after(n, log):
        """N 번 자면 워커를 취소한다 — running 플래그로 멈추면 상속·인스턴스 함정에
        걸려 테스트가 무한 루프가 된다(실제로 한 번 물렸다)."""
        async def _sleep(secs):
            log.append(secs)
            if len(log) >= n:
                raise asyncio.CancelledError
        return _sleep

    class OffServer:
        running = True
        token_sync = "off"
        token_sync_url = ""
        token_sync_sec = 30

    made, slept = [], []
    try:
        await tokensync.run_worker(OffServer(),
                                   make_client=lambda s: made.append(s),
                                   sleep=stop_after(2, slept))
    except asyncio.CancelledError:
        pass
    assert made == [] and slept == [30, 30]

    class OnServer(OffServer):
        token_sync = "server"
        token_sync_url = "https://x"

    main_thread = threading.get_ident()
    seen, slept2 = [], []

    class FakeClient:
        def push_limits(self):
            seen.append(threading.get_ident())
            return {"sent": 0}

        def pull(self):
            return {"merged": 0}

    try:
        await tokensync.run_worker(OnServer(), make_client=lambda s: FakeClient(),
                                   sleep=stop_after(1, slept2))
    except asyncio.CancelledError:
        pass
    assert seen and all(t != main_thread for t in seen), "루프 스레드에서 블로킹했다"


async def test_worker_records_error_and_backs_off():
    import asyncio

    class FakeServer:
        running = True
        token_sync = "server"
        token_sync_url = "https://x"
        token_sync_sec = 60

        def __init__(self):
            self.logged = []

        def _log_error(self, m):
            self.logged.append(m)

    srv = FakeServer()
    slept = []

    async def fake_sleep(n):
        slept.append(n)
        if len(slept) >= 3:
            raise asyncio.CancelledError

    def boom(_s):
        raise tokensync.SyncError("서버에 닿지 못했습니다")

    try:
        await tokensync.run_worker(srv, make_client=boom, sleep=fake_sleep)
    except asyncio.CancelledError:
        pass
    assert srv.logged and "token_sync" in srv.logged[0]
    assert slept[1] > slept[0]           # 지수 백오프
    assert max(slept) <= 3600
