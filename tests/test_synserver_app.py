"""동기화 서버 P1 — HTTP 앱(라우팅·인가·요청 서명·멱등) 테스트.

설계 §5.5. 라우팅 코어(`SyncApp.handle`)를 소켓 없이 직접 호출하므로 느린 러너에서도
플레이크가 없다. 시각은 전부 주입한다(`app._now`) — 실제 시계에 의존하지 않는다.

되돌리면 실패해야 하는 오라클:
  · vault_id 를 요청 파라미터로 받게 하면 → test_cannot_read_other_vault 실패
  · 서명 검증/ts 창/nonce 소모를 빼면 → test_signature_* 계열 실패
  · put_events 를 INSERT OR REPLACE 로 바꾸면 → test_events_idempotent_first_wins 실패
  · 레코드 형식 검사를 빼면 → test_rejects_malformed_records 실패
"""
import json
import os
import sys

import harness  # noqa: F401

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from synserver import app as sapp        # noqa: E402
from synserver import db as sdb          # noqa: E402
from synserver import webauthnlib as wa  # noqa: E402

from test_synserver_webauthn import FakeAuthenticator, RP_ID, ORIGIN  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _app(**kw):
    """테스트용 앱. 기본은 **등록 개방**(대부분의 테스트가 vault 2개를 만든다) —
    닫힌 기본값 자체는 S-1 테스트가 따로 검증한다."""
    conn = sdb.connect(":memory:")
    clock = Clock()
    kw.setdefault("open_registration", True)
    app = sapp.SyncApp(conn, RP_ID, ORIGIN, now=clock, **kw)
    return app, clock


def _j(resp):
    status, _, body = resp
    return status, (json.loads(body) if body else {})


def _enroll(app, auth=None):
    """패스키 등록 한 바퀴 → (세션 쿠키 헤더, vault_id)."""
    auth = auth or FakeAuthenticator()
    st, opts = _j(app.handle("POST", "/v1/enroll/options"))
    assert st == 200
    ch = wa.b64u_decode(opts["challenge"])
    att, cd = auth.register(ch)
    status, headers, body = app.handle("POST", "/v1/enroll/verify", body=json.dumps({
        "challenge": opts["challenge"],
        "attestationObject": wa.b64u_encode(att),
        "clientDataJSON": wa.b64u_encode(cd)}).encode())
    assert status == 200, body
    sid = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
    vault = sdb.session_vault(app.conn, sid, app._now())
    return {"Cookie": sapp.COOKIE + "=" + sid}, vault, auth


def _device(app, cookie):
    """페어링 코드로 기기 등록 → (device_id, Ed25519 개인키)."""
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519
    st, pr = _j(app.handle("POST", "/v1/pairing", headers=cookie))
    assert st == 200
    sk = ed25519.Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(ser.Encoding.Raw, ser.PublicFormat.Raw)
    st, out = _j(app.handle("POST", "/v1/devices", body=json.dumps({
        "pairing_code": pr["code"], "pubkey": wa.b64u_encode(pub),
        "label": "mac"}).encode()))
    assert st == 200, out
    return out["device_id"], sk


def _signed(app, clock, did, sk, method, path, body=b"", query="", *,
            ts=None, nonce=None):
    ts = str(clock.t if ts is None else ts)
    nonce = nonce or os.urandom(16).hex()
    sig = sapp.sign_request(sk, method, path, ts, nonce, body, query)
    return {"X-Sync-Device": did, "X-Sync-Ts": ts, "X-Sync-Nonce": nonce,
            "X-Sync-Sig": sig}


def _rec(rkey, acct="a1" * 8, ct=b"opaque-bytes", kind="xc"):
    return json.dumps({"kind": kind, "rkey": rkey, "acct_id": acct,
                       "ct": wa.b64u_encode(ct),
                       "nonce": wa.b64u_encode(b"\x00" * 12)})


# ── 기본 ───────────────────────────────────────────────────────────────────

async def test_health_leaks_nothing():
    app, _ = _app()
    st, out = _j(app.handle("GET", "/v1/health"))
    assert st == 200 and out == {"ok": True}       # 버전·호스트명 없음


async def test_unknown_route_404():
    app, _ = _app()
    assert _j(app.handle("GET", "/v1/secret"))[0] == 404


# ── 등록 → 기기 → 이벤트 왕복 ──────────────────────────────────────────────

async def test_enroll_pair_device_and_roundtrip():
    app, clock = _app()
    cookie, vault, _ = _enroll(app)
    did, sk = _device(app, cookie)
    body = (_rec("aa" * 8) + "\n" + _rec("bb" * 8)).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    st, out = _j(app.handle("POST", "/v1/events", headers=h, body=body))
    assert st == 200 and out["accepted"] == 2 and out["ignored"] == 0
    assert out["seq_max"] == 2
    h = _signed(app, clock, did, sk, "GET", "/v1/events", b"", "since=0")
    status, hdrs, raw = app.handle("GET", "/v1/events", "since=0", h, b"")
    assert status == 200 and hdrs["Content-Type"] == "application/x-ndjson"
    rows = [json.loads(x) for x in raw.decode().splitlines()]
    assert [r["seq"] for r in rows] == [1, 2]
    assert wa.b64u_decode(rows[0]["ct"]) == b"opaque-bytes"
    # 커서 재개: 이미 받은 것은 다시 오지 않는다(중복 0·누락 0).
    h = _signed(app, clock, did, sk, "GET", "/v1/events", b"", "since=2")
    _, _, raw = app.handle("GET", "/v1/events", "since=2", h, b"")
    assert raw.decode().strip() == ""


async def test_events_idempotent_first_wins():
    app, clock = _app()
    cookie, _, _ = _enroll(app)
    did, sk = _device(app, cookie)
    b1 = _rec("cc" * 8, ct=b"first").encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", b1)
    assert _j(app.handle("POST", "/v1/events", headers=h, body=b1))[1]["accepted"] == 1
    b2 = _rec("cc" * 8, ct=b"second-overwrite").encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", b2)
    st, out = _j(app.handle("POST", "/v1/events", headers=h, body=b2))
    assert st == 200 and out["accepted"] == 0 and out["ignored"] == 1
    # 먼저 온 것이 이긴다 — 나중 요청이 과거를 고쳐 쓸 수 없다.
    h = _signed(app, clock, did, sk, "GET", "/v1/events", b"", "since=0")
    _, _, raw = app.handle("GET", "/v1/events", "since=0", h, b"")
    rows = [json.loads(x) for x in raw.decode().splitlines()]
    assert wa.b64u_decode(rows[0]["ct"]) == b"first"


# ── 인가(IDOR) ─────────────────────────────────────────────────────────────

async def test_cannot_read_other_vault():
    """vault A 의 기기로는 vault B 의 것을 **한 줄도** 못 본다. vault 는 서명에서
    유도되므로 요청에 끼워 넣을 자리조차 없다."""
    app, clock = _app()
    ca, va, _ = _enroll(app)
    da, ka = _device(app, ca)
    cb, vb, _ = _enroll(app, FakeAuthenticator())
    db_, kb = _device(app, cb)
    assert va != vb
    body = _rec("dd" * 8, ct=b"vault-B-secret").encode()
    h = _signed(app, clock, db_, kb, "POST", "/v1/events", body)
    assert _j(app.handle("POST", "/v1/events", headers=h, body=body))[0] == 200
    h = _signed(app, clock, da, ka, "GET", "/v1/events", b"", "since=0")
    _, _, raw = app.handle("GET", "/v1/events", "since=0", h, b"")
    assert raw.decode().strip() == ""
    # 쿼리로 남의 vault 를 지목해도(그런 파라미터는 없다) 결과가 달라지지 않는다.
    q = "since=0&vault_id=" + vb
    h = _signed(app, clock, da, ka, "GET", "/v1/events", b"", q)
    _, _, raw = app.handle("GET", "/v1/events", q, h, b"")
    assert raw.decode().strip() == ""


async def test_session_endpoints_need_session():
    app, _ = _app()
    for method, path in (("POST", "/v1/pairing"), ("GET", "/v1/devices"),
                         ("POST", "/v1/purge")):
        st, out = _j(app.handle(method, path))
        assert st == 401 and out == {"error": "unauthorized"}   # 형태 동일


# ── 요청 서명 ──────────────────────────────────────────────────────────────

async def test_signature_required_and_tamper_detected():
    app, clock = _app()
    cookie, _, _ = _enroll(app)
    did, sk = _device(app, cookie)
    body = _rec("ee" * 8).encode()
    assert _j(app.handle("POST", "/v1/events", body=body))[0] == 401  # 헤더 없음
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    tampered = _rec("ff" * 8).encode()                # 서명은 옛 바디 것
    assert _j(app.handle("POST", "/v1/events", headers=h, body=tampered))[0] == 401
    # 쿼리 변조(since 바꿔치기)도 서명이 잡는다.
    h = _signed(app, clock, did, sk, "GET", "/v1/events", b"", "since=0")
    assert _j(app.handle("GET", "/v1/events", "since=99", h, b""))[0] == 401


async def test_signature_ts_window_and_nonce_replay():
    app, clock = _app()
    cookie, _, _ = _enroll(app)
    did, sk = _device(app, cookie)
    body = _rec("ab" * 8).encode()
    old = _signed(app, clock, did, sk, "POST", "/v1/events", body,
                  ts=clock.t - 3600)
    assert _j(app.handle("POST", "/v1/events", headers=old, body=body))[0] == 401
    future = _signed(app, clock, did, sk, "POST", "/v1/events", body,
                     ts=clock.t + 3600)
    assert _j(app.handle("POST", "/v1/events", headers=future, body=body))[0] == 401
    good = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    assert _j(app.handle("POST", "/v1/events", headers=good, body=body))[0] == 200
    # 같은 서명을 그대로 재생 → nonce 재사용으로 거부.
    assert _j(app.handle("POST", "/v1/events", headers=good, body=body))[0] == 401
    assert app.stats.get("nonce_replay") == 1


async def test_revoked_device_is_denied():
    app, clock = _app()
    cookie, _, _ = _enroll(app)
    did, sk = _device(app, cookie)
    body = _rec("ba" * 8).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    assert _j(app.handle("POST", "/v1/events", headers=h, body=body))[0] == 200
    assert _j(app.handle("DELETE", "/v1/devices/" + did, headers=cookie))[0] == 200
    body2 = _rec("bc" * 8).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body2)
    assert _j(app.handle("POST", "/v1/events", headers=h, body=body2))[0] == 401


async def test_cannot_revoke_other_vault_device():
    app, _ = _app()
    ca, _, _ = _enroll(app)
    da, _ = _device(app, ca)
    cb, _, _ = _enroll(app, FakeAuthenticator())
    st, out = _j(app.handle("DELETE", "/v1/devices/" + da, headers=cb))
    assert st == 404 and out["ok"] is False
    assert sdb.get_device(app.conn, da) is not None      # 살아 있다


# ── 페어링·챌린지 1회성 ────────────────────────────────────────────────────

async def test_pairing_code_is_single_use_and_expires():
    from cryptography.hazmat.primitives import serialization as ser
    from cryptography.hazmat.primitives.asymmetric import ed25519
    app, clock = _app()
    cookie, _, _ = _enroll(app)
    st, pr = _j(app.handle("POST", "/v1/pairing", headers=cookie))
    sk = ed25519.Ed25519PrivateKey.generate()
    pub = wa.b64u_encode(sk.public_key().public_bytes(
        ser.Encoding.Raw, ser.PublicFormat.Raw))
    payload = json.dumps({"pairing_code": pr["code"], "pubkey": pub}).encode()
    assert _j(app.handle("POST", "/v1/devices", body=payload))[0] == 200
    assert _j(app.handle("POST", "/v1/devices", body=payload))[0] == 401   # 1회용
    # 만료
    st, pr2 = _j(app.handle("POST", "/v1/pairing", headers=cookie))
    clock.t += 601
    payload2 = json.dumps({"pairing_code": pr2["code"], "pubkey": pub}).encode()
    assert _j(app.handle("POST", "/v1/devices", body=payload2))[0] == 401


async def test_challenge_is_single_use():
    app, _ = _app()
    auth = FakeAuthenticator()
    st, opts = _j(app.handle("POST", "/v1/enroll/options"))
    att, cd = auth.register(wa.b64u_decode(opts["challenge"]))
    payload = json.dumps({"challenge": opts["challenge"],
                          "attestationObject": wa.b64u_encode(att),
                          "clientDataJSON": wa.b64u_encode(cd)}).encode()
    assert _j(app.handle("POST", "/v1/enroll/verify", body=payload))[0] == 200
    assert _j(app.handle("POST", "/v1/enroll/verify", body=payload))[0] == 401


async def test_login_with_passkey_gives_same_vault():
    app, clock = _app()
    cookie, vault, auth = _enroll(app)
    st, opts = _j(app.handle("POST", "/v1/auth/options"))
    ch = wa.b64u_decode(opts["challenge"])
    ad, cd, sig = auth.sign(ch)
    status, headers, body = app.handle("POST", "/v1/auth/verify", body=json.dumps({
        "challenge": opts["challenge"],
        "credentialId": wa.b64u_encode(auth.cred_id),
        "authenticatorData": wa.b64u_encode(ad),
        "clientDataJSON": wa.b64u_encode(cd),
        "signature": wa.b64u_encode(sig)}).encode())
    assert status == 200, body
    sid = headers["Set-Cookie"].split(";")[0].split("=", 1)[1]
    assert sdb.session_vault(app.conn, sid, clock.t) == vault


# ── 신뢰불가 입력 ──────────────────────────────────────────────────────────

async def test_rejects_malformed_records():
    app, clock = _app()
    cookie, _, _ = _enroll(app)
    did, sk = _device(app, cookie)
    bad = "\n".join([
        json.dumps({"kind": "evil", "rkey": "aa", "ct": "x", "nonce": "y"}),
        json.dumps({"kind": "xc", "rkey": "not-hex!!", "ct": "x", "nonce": "y"}),
        json.dumps({"kind": "xc", "rkey": "aa" * 8}),              # ct 없음
        json.dumps({"kind": "xc", "rkey": "aa" * 8, "ct": "AAAA",
                    "nonce": wa.b64u_encode(b"short")}),           # nonce 길이
        "not json at all",
        _rec("11" * 8),                                            # 유일한 정상
    ]).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", bad)
    st, out = _j(app.handle("POST", "/v1/events", headers=h, body=bad))
    assert st == 200 and out["accepted"] == 1 and out["rejected"] == 5
    assert sdb.max_seq(app.conn, sdb.session_vault(
        app.conn, cookie["Cookie"].split("=", 1)[1], clock.t)) == 1


async def test_quota_exceeded_is_reported():
    app, clock = _app()
    cookie, vault, _ = _enroll(app)
    did, sk = _device(app, cookie)
    app.conn.execute("UPDATE vault SET quota_rows=1 WHERE vault_id=?", (vault,))
    app.conn.commit()
    body = (_rec("21" * 8) + "\n" + _rec("22" * 8)).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    st, out = _j(app.handle("POST", "/v1/events", headers=h, body=body))
    assert st == 507 and out["error"] == "quota"
    assert app.stats.get("quota") == 1


async def test_oversized_body_rejected():
    app, _ = _app()
    st, out = _j(app.handle("POST", "/v1/events", body=b"x" * (sapp.MAX_BODY + 1)))
    assert st == 413


# ── 서버는 평문을 갖지 않는다(§5.7) ────────────────────────────────────────

async def test_server_db_holds_no_plaintext():
    """클라이언트가 syncrypto 로 봉인해 올리면, **서버 DB 어디에도** 계정 이메일·
    모델명·토큰 수치의 평문이 남지 않는다. 암호화를 끄는 쪽으로 되돌리면 실패한다."""
    from pytmuxlib import syncrypto
    if not syncrypto.available():
        from run import skip
        skip("cryptography 미설치 — 봉인 경로 미검증")
    app, clock = _app()
    cookie, vault, _ = _enroll(app)
    did, sk = _device(app, cookie)
    k_id, k_enc = syncrypto.derive_keys(b"\x11" * 32)
    acct = syncrypto.acct_id(k_id, "someone@example.com")
    rk = syncrypto.rkey(k_id, "xc", "msg_secret_id:req_1")
    payload = json.dumps({"model": "opus-4.8", "input": 12345,
                          "account_hint": "someone@example.com"}).encode()
    nonce, ct = syncrypto.seal(k_enc, syncrypto.aad(vault, "xc", rk, acct), payload)
    line = json.dumps({"kind": "xc", "rkey": rk, "acct_id": acct,
                       "ct": wa.b64u_encode(ct),
                       "nonce": wa.b64u_encode(nonce)}).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", line)
    assert _j(app.handle("POST", "/v1/events", headers=h, body=line))[1]["accepted"] == 1
    blob = b"".join(bytes(r[0]) if isinstance(r[0], (bytes, bytearray))
                    else str(r[0]).encode()
                    for r in app.conn.execute(
                        "SELECT ct FROM event UNION ALL SELECT acct_id FROM event"
                        " UNION ALL SELECT rkey FROM event"))
    for marker in (b"someone@example.com", b"opus-4.8", b"12345",
                   b"msg_secret_id"):
        assert marker not in blob, marker


# ── 등록 페이지(정적) ──────────────────────────────────────────────────────

async def test_static_page_served_with_strict_csp():
    app, _ = _app()
    status, hdrs, body = app.handle("GET", "/")
    assert status == 200 and hdrs["Content-Type"].startswith("text/html")
    assert "default-src 'none'" in hdrs["Content-Security-Policy"]
    assert b"pytmux" in body
    for name in ("enroll.js", "enroll.css"):
        st, h, b = app.handle("GET", "/static/" + name)
        assert st == 200 and b
    # 화이트리스트 밖·경로 조작은 404(파일이 실제로 있어도 나가지 않는다).
    for bad in ("../db.py", "../../CLAUDE.md", "app.py", "secret.txt"):
        assert app.handle("GET", "/static/" + bad)[0] == 404


# ── 스레드(실제 HTTP 서버는 요청마다 스레드다) ─────────────────────────────

async def test_handles_requests_from_other_threads():
    """`ThreadingHTTPServer` 는 요청마다 새 스레드를 만든다. 메인 스레드에서 연
    sqlite 연결을 그대로 쓰면 **DB 를 건드리는 모든 엔드포인트가 500** 이 된다
    (실측: 브라우저 로그인이 'internal'). handle() 을 다른 스레드에서 불러 못박는다.

    되돌리면(check_same_thread 기본값) 이 테스트가 실패한다."""
    import queue
    import threading

    app, clock = _app()
    out = queue.Queue()

    def worker(fn):
        try:
            out.put(("ok", fn()))
        except Exception as e:            # noqa: BLE001
            out.put(("err", repr(e)))

    # ① 등록 한 바퀴를 통째로 다른 스레드에서
    t = threading.Thread(target=worker, args=(lambda: _enroll(app),))
    t.start()
    t.join(20)
    kind, val = out.get_nowait()
    assert kind == "ok", val
    cookie, vault, auth = val

    # ② 기기 등록·이벤트 업로드도 또 다른 스레드에서(각각 새 스레드)
    t = threading.Thread(target=worker, args=(lambda: _device(app, cookie),))
    t.start(); t.join(20)
    kind, val = out.get_nowait()
    assert kind == "ok", val
    did, sk = val

    body = _rec("f1" * 8).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    t = threading.Thread(target=worker, args=(
        lambda: _j(app.handle("POST", "/v1/events", headers=h, body=body)),))
    t.start(); t.join(20)
    kind, val = out.get_nowait()
    assert kind == "ok", val
    assert val[0] == 200 and val[1]["accepted"] == 1


async def test_concurrent_requests_do_not_corrupt_state():
    """여러 스레드가 동시에 두드려도 멱등·카운트가 어긋나지 않는다(락 직렬화)."""
    import threading

    app, clock = _app()
    cookie, _vault, _ = _enroll(app)
    did, sk = _device(app, cookie)
    results = []
    lock = threading.Lock()

    def push(i):
        body = _rec("%02x" % i * 8).encode()
        h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
        st, out = _j(app.handle("POST", "/v1/events", headers=h, body=body))
        with lock:
            results.append((st, out.get("accepted")))

    ts = [threading.Thread(target=push, args=(i,)) for i in range(1, 9)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(20)
    assert len(results) == 8 and all(r == (200, 1) for r in results), results
    assert sdb.max_seq(app.conn, sdb.session_vault(
        app.conn, cookie["Cookie"].split("=", 1)[1], clock.t)) == 8


# ── 검수 처방 회귀(SYNSERVER_REVIEW_2026-07-23) ─────────────────────────────

async def test_second_vault_blocked_by_default():
    """S-1: 공개 종단에서 아무나 vault 를 만들 수 있으면 그 자체가 자원 고갈이다
    (검수 실측: 인증 없이 50/50 생성). 기본은 **첫 등록만** 열린다."""
    app, _ = _app(open_registration=False)
    _enroll(app)                                  # 최초 1회는 허용
    auth = FakeAuthenticator()
    st, opts = _j(app.handle("POST", "/v1/enroll/options"))
    att, cd = auth.register(wa.b64u_decode(opts["challenge"]))
    st, out = _j(app.handle("POST", "/v1/enroll/verify", body=json.dumps({
        "challenge": opts["challenge"],
        "attestationObject": wa.b64u_encode(att),
        "clientDataJSON": wa.b64u_encode(cd)}).encode()))
    assert st == 401 and out == {"error": "unauthorized"}
    assert sdb.vault_count(app.conn) == 1
    assert app.stats.get("registration_closed") == 1


async def test_bootstrap_token_gate():
    """S-1: 여러 vault 가 필요하면 부트스트랩 토큰으로만 연다(정확히 일치해야)."""
    conn = sdb.connect(":memory:")
    app = sapp.SyncApp(conn, RP_ID, ORIGIN, now=Clock(), bootstrap_token="s3cr3t")

    def try_enroll(hdrs):
        auth = FakeAuthenticator()
        _st, opts = _j(app.handle("POST", "/v1/enroll/options", headers=hdrs))
        att, cd = auth.register(wa.b64u_decode(opts["challenge"]))
        return _j(app.handle("POST", "/v1/enroll/verify", headers=hdrs,
                             body=json.dumps({
                                 "challenge": opts["challenge"],
                                 "attestationObject": wa.b64u_encode(att),
                                 "clientDataJSON": wa.b64u_encode(cd)}).encode()))[0]

    assert try_enroll({}) == 401                       # 토큰 없음
    assert try_enroll({"X-Sync-Bootstrap": "wrong"}) == 401
    assert try_enroll({"X-Sync-Bootstrap": "s3cr3t"}) == 200
    assert try_enroll({"X-Sync-Bootstrap": "s3cr3t"}) == 200   # 여러 개 허용


async def test_unauth_rate_limit():
    """S-1: 미인증 경로는 전역 상한이 있다(터널 뒤라 IP 를 못 믿는다)."""
    app, _ = _app()
    codes = [app.handle("POST", "/v1/auth/options")[0]
             for _ in range(sapp.UNAUTH_BURST + 5)]
    assert codes[0] == 200 and codes[-1] == 429
    assert app.stats.get("unauth_rate")
    # 인증된 경로는 이 상한과 무관하다(같은 창 안에서도 동작).
    assert app.handle("GET", "/v1/health")[0] == 200


async def test_wrong_pairing_guess_does_not_kill_others():
    """S-2: 오답 5회가 **남의 유효 코드**를 지우던 것(실측 3개→0개)."""
    app, _ = _app()
    cookie, _v, _a = _enroll(app)
    codes = [_j(app.handle("POST", "/v1/pairing", headers=cookie))[1]["code"]
             for _ in range(3)]
    for i in range(5):
        app.handle("POST", "/v1/devices", body=json.dumps({
            "pairing_code": "ZZZZ-ZZZZ-%02X" % i,
            "pubkey": wa.b64u_encode(b"\x00" * 32)}).encode())
    assert app.conn.execute("SELECT COUNT(*) FROM pairing").fetchone()[0] == 3
    # 살아남은 코드는 여전히 쓸 수 있다.
    st, out = _j(app.handle("POST", "/v1/devices", body=json.dumps({
        "pairing_code": codes[0], "pubkey": wa.b64u_encode(b"\x01" * 32)}).encode()))
    assert st == 200 and out["device_id"]


async def test_challenge_store_is_bounded():
    """S-3: 미인증 호출만으로 챌린지가 무한히 쌓이던 것(실측 2,000개 상주)."""
    app, _ = _app()
    for _ in range(sapp.MAX_CHALLENGES + 200):
        app._unauth = [0.0, 0]                    # rate limit 은 이 테스트 대상 아님
        app.handle("POST", "/v1/auth/options")
    assert len(app._chal) <= sapp.MAX_CHALLENGES


async def test_quota_rejects_atomically():
    """S-4: 쿼터 초과가 **부분 삽입 + 회계 드리프트 + 열린 트랜잭션**을 남기던 것
    (실측 3행 저장·rows_used=0·in_transaction=True → 쿼터가 영영 안 걸림)."""
    app, clock = _app()
    cookie, vault, _ = _enroll(app)
    did, sk = _device(app, cookie)
    app.conn.execute("UPDATE vault SET quota_rows=3 WHERE vault_id=?", (vault,))
    app.conn.commit()
    body = ("\n".join(_rec("%02x" % i * 8) for i in range(1, 8))).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    st, out = _j(app.handle("POST", "/v1/events", headers=h, body=body))
    assert st == 507
    assert app.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 0
    assert app.conn.in_transaction is False
    # 쿼터 안쪽 배치는 정상 처리되고 회계도 맞는다.
    body = ("\n".join(_rec("a%01x" % i * 8) for i in range(1, 3))).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    assert _j(app.handle("POST", "/v1/events", headers=h, body=body))[1]["accepted"] == 2
    row = app.conn.execute("SELECT rows_used, bytes_used FROM vault").fetchone()
    assert row["rows_used"] == 2 and row["bytes_used"] > 0


async def test_byte_quota_enforced():
    """S-8: 행 수만 세면 64KB 레코드로 이론상 0.33TB 까지 쌓인다."""
    app, clock = _app()
    cookie, vault, _ = _enroll(app)
    did, sk = _device(app, cookie)
    app.conn.execute("UPDATE vault SET quota_bytes=100 WHERE vault_id=?", (vault,))
    app.conn.commit()
    body = _rec("b1" * 8, ct=b"x" * 200).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    st, out = _j(app.handle("POST", "/v1/events", headers=h, body=body))
    assert st == 507 and out["error"] == "quota"
    assert app.conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 0


async def test_expired_state_is_purged():
    """S-5: purge_expired 가 아무 데서도 안 불려 nonce/세션/코드가 영구 누적되던 것."""
    app, clock = _app()
    cookie, _v, _a = _enroll(app)
    did, sk = _device(app, cookie)
    body = _rec("c1" * 8).encode()
    h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
    app.handle("POST", "/v1/events", headers=h, body=body)
    app.handle("POST", "/v1/pairing", headers=cookie)
    assert app.conn.execute("SELECT COUNT(*) FROM nonce_seen").fetchone()[0] == 1
    clock.t += 3600                                   # 전부 만료될 만큼 전진
    app.handle("GET", "/v1/health")                   # 아무 요청이나 하나
    assert app.conn.execute("SELECT COUNT(*) FROM nonce_seen").fetchone()[0] == 0
    assert app.conn.execute("SELECT COUNT(*) FROM pairing").fetchone()[0] == 0
    assert app.conn.execute("SELECT COUNT(*) FROM session").fetchone()[0] == 0


async def test_pull_respects_byte_budget():
    """S-6: 다운로드 응답을 통째로 메모리에 조립하던 것(최대 ~328MB).
    예산에서 잘리면 커서로 이어받는다 — 누락이 아니다."""
    app, clock = _app()
    cookie, _v, _a = _enroll(app)
    did, sk = _device(app, cookie)
    big = b"y" * 60000
    for i in range(1, 6):
        body = _rec("d%01x" % i * 8, ct=big).encode()
        h = _signed(app, clock, did, sk, "POST", "/v1/events", body)
        assert _j(app.handle("POST", "/v1/events", headers=h, body=body))[0] == 200
    old_budget = sapp.PULL_BYTE_BUDGET
    sapp.PULL_BYTE_BUDGET = 200000                    # 2건 남짓만 들어가게
    try:
        h = _signed(app, clock, did, sk, "GET", "/v1/events", b"", "since=0")
        _st, _hd, raw = app.handle("GET", "/v1/events", "since=0", h, b"")
        lines = raw.decode().splitlines()
        assert 0 < len(lines) < 5 and len(raw) < 400000
        last = json.loads(lines[-1])["seq"]
        q = "since=%d" % last
        h = _signed(app, clock, did, sk, "GET", "/v1/events", b"", q)
        _st, _hd, raw2 = app.handle("GET", "/v1/events", q, h, b"")
        assert raw2.decode().strip(), "이어받기가 비었다(잘림이 곧 누락이 됐다)"
    finally:
        sapp.PULL_BYTE_BUDGET = old_budget


async def test_device_limit_per_vault():
    """S-7: 세션 하나로 기기를 무한 등록할 수 있던 것(실측 30개)."""
    app, _ = _app()
    cookie, _v, _a = _enroll(app)
    for _ in range(sdb.MAX_DEVICES):
        app._unauth = [0.0, 0]      # 미인증 상한은 이 테스트의 대상이 아니다(S-1 별도)
        _device(app, cookie)
    app._unauth = [0.0, 0]
    st, pr = _j(app.handle("POST", "/v1/pairing", headers=cookie))
    st, out = _j(app.handle("POST", "/v1/devices", body=json.dumps({
        "pairing_code": pr["code"], "pubkey": wa.b64u_encode(b"\x02" * 32)}).encode()))
    assert st == 409 and out["error"] == "limit"


async def test_logout_drops_session():
    """Q-2: 로그아웃이 없으면 훔친 쿠키가 TTL 동안 유효하다."""
    app, _ = _app()
    cookie, _v, _a = _enroll(app)
    assert _j(app.handle("POST", "/v1/pairing", headers=cookie))[0] == 200
    st, hdrs, _b = app.handle("POST", "/v1/logout", headers=cookie)
    assert st == 200 and "Max-Age=0" in hdrs["Set-Cookie"]
    assert _j(app.handle("POST", "/v1/pairing", headers=cookie))[0] == 401


async def test_session_cookie_uses_host_prefix():
    """Q-3: `__Host-` 접두는 Path=/·Secure·도메인 미지정을 브라우저가 강제한다."""
    app, _ = _app()
    _c, _v, _a = _enroll(app)
    st, hdrs, _b = app.handle("POST", "/v1/auth/options")
    st, opts = _j((st, hdrs, _b))
    assert sapp.COOKIE.startswith("__Host-")
