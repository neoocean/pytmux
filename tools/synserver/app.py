"""동기화 서버 HTTP 앱 — 표준 라이브러리 http.server + sqlite3(+ cryptography).

설계: docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md §5.5.

라우팅 코어(`SyncApp.handle`)는 **소켓과 분리**돼 있다 — 테스트가 HTTP 를 띄우지 않고
메서드/경로/헤더/바디만으로 전 경로를 돌릴 수 있게(느린 러너에서 플레이크가 없다).

인증 두 갈래(§5.2):
  · 사람  = 패스키 → 세션 쿠키(짧은 TTL). 등록·기기관리 화면에서만 쓴다.
  · 기계  = 디바이스 Ed25519 **요청 서명**. `/v1/events` 는 이 경로만 받는다.

**vault_id 는 언제나 인증에서 유도한다** — 요청 파라미터로 받아 대조하는 구조는
IDOR 의 고전적 자리라 아예 만들지 않는다(회귀 테스트로 못박음).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import time
import traceback
import urllib.parse

try:                                    # 패키지로도, 스크립트로도 실행된다
    from . import db as sdb
    from . import webauthnlib as wa
except ImportError:                     # python3 tools/synserver/app.py …
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from synserver import db as sdb
    from synserver import webauthnlib as wa

MAX_BODY = 8 * 1024 * 1024          # 8MB — 배치 상한
MAX_BATCH = 5000                    # 레코드/요청
SIG_WINDOW = 60.0                   # 요청 서명 ts 허용 창(초)
CHALLENGE_TTL = 300.0
SESSION_TTL = 900.0
RATE_BURST = 60                     # 디바이스당 분당 요청
KINDS = ("xc", "lim")


class SyncApp:
    def __init__(self, conn, rp_id: str, origin: str, now=None):
        self.conn = conn
        self.rp_id = rp_id
        self.origin = origin
        self._now = now or time.time
        # 진행 중 챌린지: {challenge_hex: (kind, vault_id|None, exp)}. 단일 프로세스라
        # 메모리로 충분하고, 재시작하면 진행 중인 등록만 다시 하면 된다(무해).
        self._chal = {}
        self._rate = {}                 # device_id → [윈도 시작, 카운트]
        self.stats = {}                 # 거부 사유별 카운터(로그엔 사유만, 값은 금지)
        # HTTP 서버는 요청마다 스레드를 만든다 → sqlite 연결과 위 dict 들을 **직렬화**
        # 한다. 개인 규모 트래픽에서 락 경합은 무시할 수 있고, 대신 경쟁 상태가
        # 원리적으로 사라진다(챌린지 재사용·nonce·rate 창까지 한 번에 보호).
        self._lock = threading.RLock()

    # ── 라우팅 ────────────────────────────────────────────────────────────

    def handle(self, method: str, path: str, query: str = "", headers=None,
               body: bytes = b""):
        """(status, headers(dict), body(bytes)). 예외를 밖으로 내보내지 않는다 —
        스택트레이스가 응답에 실리면 그 자체가 정보 누출이다."""
        headers = {k.lower(): v for k, v in (headers or {}).items()}
        q = urllib.parse.parse_qs(query or "")
        with self._lock:
            return self._handle_locked(method, path, query, headers, body, q)

    def _handle_locked(self, method, path, query, headers, body, q):
        try:
            if len(body) > MAX_BODY:
                return self._err(413, "too_large")
            route = (method, path)
            if method == "GET" and path in ("/", "/enroll"):
                return self._static("enroll.html")
            if method == "GET" and path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if route == ("GET", "/v1/health"):
                # 버전·호스트명 등 **아무것도 노출하지 않는다**(포트 스캐너 오라클 금지).
                return self._json(200, {"ok": True})
            if route == ("POST", "/v1/enroll/options"):
                return self._enroll_options(headers)
            if route == ("POST", "/v1/enroll/verify"):
                return self._enroll_verify(headers, body)
            if route == ("POST", "/v1/auth/options"):
                return self._auth_options()
            if route == ("POST", "/v1/auth/verify"):
                return self._auth_verify(body)
            if route == ("POST", "/v1/pairing"):
                return self._pairing(headers)
            if route == ("POST", "/v1/devices"):
                return self._device_add(headers, body)
            if route == ("GET", "/v1/devices"):
                return self._device_list(headers)
            if method == "DELETE" and path.startswith("/v1/devices/"):
                return self._device_revoke(headers, path.rsplit("/", 1)[-1])
            if route == ("POST", "/v1/events"):
                return self._events_put(method, path, headers, body)
            if route == ("GET", "/v1/events"):
                return self._events_get(method, path, query, headers, body, q)
            if route == ("POST", "/v1/purge"):
                return self._purge(headers, q)
            return self._err(404, "not_found")
        except Exception:               # noqa: BLE001 — 마지막 그물
            self._bump("internal")
            # 응답에는 사유를 싣지 않지만(정보 누출), **서버 로그에는 반드시 남긴다** —
            # 로그 없는 500 은 디버깅이 불가능하다(실측: 스레드 문제를 로그가 없어
            # 브라우저 화면의 "internal" 만 보고 추적해야 했다).
            sys.stderr.write("ERR %s %s\n%s" % (method, path, traceback.format_exc()))
            return self._err(500, "internal")

    # ── 정적 파일(등록 페이지) ────────────────────────────────────────────

    _STATIC_TYPES = {".html": "text/html; charset=utf-8",
                     ".js": "text/javascript; charset=utf-8",
                     ".css": "text/css; charset=utf-8"}
    # 외부 리소스를 **한 톨도** 부르지 않는 페이지라 CSP 를 최대로 조인다.
    _CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; form-action 'none'; base-uri 'none'; "
            "frame-ancestors 'none'")

    def _static(self, name):
        """`static/` 밑의 화이트리스트 파일만. 경로 조작(`../`)은 이름 자체를 막아
        차단한다 — 정규화로 막는 것보다 확실하다."""
        ext = os.path.splitext(name)[1]
        if name not in ("enroll.html", "enroll.js", "enroll.css"):
            return self._err(404, "not_found")
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "static", name)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            return self._err(404, "not_found")
        return 200, {"Content-Type": self._STATIC_TYPES[ext],
                     "Content-Security-Policy": self._CSP,
                     "Referrer-Policy": "no-referrer",
                     "Cache-Control": "no-store"}, data

    # ── 패스키(사람) ──────────────────────────────────────────────────────

    def _enroll_options(self, headers):
        """등록 옵션. 로그인 세션이 있으면 **그 vault 에 패스키 추가**, 없으면 새
        vault 생성 흐름이다. user.id 는 랜덤 vault_id 이고 name/displayName 도 그
        표기뿐 — 서버는 사람 이름을 받지 않는다(§5.2)."""
        vault_id = self._session_vault(headers)
        ch = wa.new_challenge()
        self._chal[ch.hex()] = ("create", vault_id, self._now() + CHALLENGE_TTL)
        uid = vault_id or ("pending-" + ch.hex()[:16])
        return self._json(200, {
            "challenge": wa.b64u_encode(ch),
            "rp": {"id": self.rp_id, "name": "pytmux sync"},
            "user": {"id": wa.b64u_encode(uid.encode()),
                     "name": "vault-" + uid[:8], "displayName": "vault-" + uid[:8]},
            "pubKeyCredParams": [{"type": "public-key", "alg": a}
                                 for a in wa.SUPPORTED_ALGS],
            "authenticatorSelection": {"residentKey": "required",
                                       "requireResidentKey": True,
                                       "userVerification": "required"},
            "attestation": "none",
            "timeout": int(CHALLENGE_TTL * 1000)})

    def _enroll_verify(self, headers, body):
        d = self._body_json(body)
        if d is None:
            return self._err(400, "bad_request")
        ch = self._take_challenge(d.get("challenge"), "create")
        if ch is None:
            return self._bad_auth("challenge")
        try:
            reg = wa.verify_registration(
                wa.b64u_decode(d.get("attestationObject") or ""),
                wa.b64u_decode(d.get("clientDataJSON") or ""),
                ch[0], self.rp_id, self.origin)
        except wa.WebAuthnError:
            return self._bad_auth("registration")
        now = self._now()
        vault_id = ch[1] or sdb.create_vault(self.conn, now)
        if sdb.get_passkey(self.conn, reg["cred_id"]) is not None:
            return self._bad_auth("credential_exists")
        sdb.add_passkey(self.conn, vault_id, reg["cred_id"], reg["cose_key"],
                        reg["sign_count"], reg["aaguid"],
                        label=(d.get("label") or None), now=now)
        sid = sdb.new_session(self.conn, vault_id, now, SESSION_TTL)
        return self._json(200, {"ok": True}, cookie=sid)

    def _auth_options(self):
        """discoverable 로그인 — `allowCredentials` 를 **비워** 아이디 입력 없이,
        인증기가 스스로 어느 vault 인지 알려 준다."""
        ch = wa.new_challenge()
        self._chal[ch.hex()] = ("get", None, self._now() + CHALLENGE_TTL)
        return self._json(200, {"challenge": wa.b64u_encode(ch),
                                "rpId": self.rp_id,
                                "allowCredentials": [],
                                "userVerification": "required",
                                "timeout": int(CHALLENGE_TTL * 1000)})

    def _auth_verify(self, body):
        d = self._body_json(body)
        if d is None:
            return self._err(400, "bad_request")
        ch = self._take_challenge(d.get("challenge"), "get")
        if ch is None:
            return self._bad_auth("challenge")
        try:
            cred_id = wa.b64u_decode(d.get("credentialId") or "")
        except wa.WebAuthnError:
            return self._bad_auth("credential")
        pk = sdb.get_passkey(self.conn, cred_id)
        if pk is None:
            return self._bad_auth("credential")
        try:
            count = wa.verify_assertion(
                wa.b64u_decode(d.get("authenticatorData") or ""),
                wa.b64u_decode(d.get("clientDataJSON") or ""),
                wa.b64u_decode(d.get("signature") or ""),
                pk["pubkey"], ch[0], self.rp_id, self.origin,
                stored_sign_count=int(pk["sign_count"] or 0))
        except wa.WebAuthnError:
            return self._bad_auth("assertion")
        now = self._now()
        sdb.touch_passkey(self.conn, cred_id, count, now)
        sid = sdb.new_session(self.conn, pk["vault_id"], now, SESSION_TTL)
        return self._json(200, {"ok": True}, cookie=sid)

    # ── 기기 등록·관리 ────────────────────────────────────────────────────

    def _pairing(self, headers):
        vault_id = self._session_vault(headers)
        if not vault_id:
            return self._bad_auth("session")
        code = sdb.new_pairing(self.conn, vault_id, self._now())
        return self._json(200, {"code": code, "expires_in": 600})

    def _device_add(self, headers, body):
        """세션(같은 브라우저) 또는 **1회용 페어링 코드**(헤드리스 머신)로 등록."""
        d = self._body_json(body)
        if d is None:
            return self._err(400, "bad_request")
        vault_id = self._session_vault(headers)
        if not vault_id and d.get("pairing_code"):
            vault_id = sdb.consume_pairing(self.conn, d["pairing_code"], self._now())
        if not vault_id:
            return self._bad_auth("pairing")
        try:
            pub = wa.b64u_decode(d.get("pubkey") or "")
        except wa.WebAuthnError:
            return self._err(400, "bad_request")
        if len(pub) != 32:                       # Ed25519 raw
            return self._err(400, "bad_request")
        did = sdb.add_device(self.conn, vault_id, pub,
                             label=(d.get("label") or None), now=self._now())
        return self._json(200, {"device_id": did})

    def _device_list(self, headers):
        vault_id = self._session_vault(headers)
        if not vault_id:
            return self._bad_auth("session")
        return self._json(200, {"devices": sdb.list_devices(self.conn, vault_id)})

    def _device_revoke(self, headers, device_id):
        vault_id = self._session_vault(headers)
        if not vault_id:
            return self._bad_auth("session")
        ok = sdb.revoke_device(self.conn, vault_id, device_id, self._now())
        return self._json(200 if ok else 404, {"ok": bool(ok)})

    # ── 이벤트(기계) ──────────────────────────────────────────────────────

    def _events_put(self, method, path, headers, body):
        dev = self._device_auth(method, path, headers, body)
        if dev is None:
            return self._bad_auth("signature")
        recs, bad = [], 0
        for line in body.split(b"\n"):
            line = line.strip()
            if not line:
                continue
            rec = self._parse_record(line)
            if rec is None:
                bad += 1
                continue
            recs.append(rec)
            if len(recs) > MAX_BATCH:
                return self._err(413, "too_many")
        try:
            out = sdb.put_events(self.conn, dev["vault_id"], recs, self._now())
        except sdb.QuotaExceeded:
            self._bump("quota")
            return self._err(507, "quota")
        except KeyError:
            return self._bad_auth("vault")
        if bad:
            self._bump("record_rejected")
        out["rejected"] = bad
        sdb.touch_device(self.conn, dev["device_id"], self._now())
        return self._json(200, out)

    def _events_get(self, method, path, query, headers, body, q):
        dev = self._device_auth(method, path, headers, body, query=query)
        if dev is None:
            return self._bad_auth("signature")
        since = _int(q.get("since", ["0"])[0], 0)
        limit = _int(q.get("limit", ["1000"])[0], 1000)
        rows = sdb.get_events(self.conn, dev["vault_id"], since, limit)
        out = []
        for r in rows:
            out.append(json.dumps({
                "seq": r["seq"], "kind": r["kind"], "acct_id": r["acct_id"],
                "rkey": r["rkey"], "ct": wa.b64u_encode(r["ct"]),
                "nonce": wa.b64u_encode(r["nonce"])}, ensure_ascii=False))
        sdb.touch_device(self.conn, dev["device_id"], self._now())
        return (200, {"Content-Type": "application/x-ndjson"},
                ("\n".join(out) + ("\n" if out else "")).encode())

    def _purge(self, headers, q):
        vault_id = self._session_vault(headers)
        if not vault_id:
            return self._bad_auth("session")
        n = sdb.purge_events(self.conn, vault_id,
                             _int(q.get("before_seq", ["0"])[0], 0))
        return self._json(200, {"purged": n})

    # ── 요청 서명(기계 인증) ──────────────────────────────────────────────

    def signing_string(self, method, path, ts, nonce, body, query=""):
        """서명 대상. **경로와 쿼리까지** 포함해야 `since=` 를 바꿔치기 못 한다."""
        full = path + (("?" + query) if query else "")
        return "|".join(["v1", method, full, str(ts), str(nonce),
                         hashlib.sha256(body).hexdigest()]).encode()

    def _device_auth(self, method, path, headers, body, query=""):
        did = headers.get("x-sync-device") or ""
        ts_s = headers.get("x-sync-ts") or ""
        nonce = headers.get("x-sync-nonce") or ""
        sig_s = headers.get("x-sync-sig") or ""
        if not (did and ts_s and nonce and sig_s):
            return self._deny("missing_headers")
        try:
            ts = float(ts_s)
        except ValueError:
            return self._deny("ts")
        now = self._now()
        if abs(now - ts) > SIG_WINDOW:
            return self._deny("ts_window")
        dev = sdb.get_device(self.conn, did)     # 폐기 기기는 None
        if dev is None:
            return self._deny("unknown_device")
        if not self._rate_ok(did, now):
            return self._deny("rate")
        try:
            sig = wa.b64u_decode(sig_s)
        except wa.WebAuthnError:
            return self._deny("sig_format")
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric import ed25519
        try:
            pub = ed25519.Ed25519PublicKey.from_public_bytes(bytes(dev["pubkey"]))
            pub.verify(sig, self.signing_string(method, path, ts_s, nonce,
                                                body, query))
        except (InvalidSignature, ValueError):
            return self._deny("signature")
        # 서명이 맞은 **뒤에** nonce 를 소모한다 — 그 전에 쓰면 아무나 nonce 테이블을
        # 채워 정상 요청을 밀어낼 수 있다.
        if not sdb.use_nonce(self.conn, did, nonce, now, SIG_WINDOW):
            return self._deny("nonce_replay")
        return dev

    def _rate_ok(self, did, now):
        w = self._rate.get(did)
        if w is None or now - w[0] >= 60.0:
            self._rate[did] = [now, 1]
            return True
        w[1] += 1
        return w[1] <= RATE_BURST

    # ── 잡동사니 ──────────────────────────────────────────────────────────

    def _parse_record(self, line: bytes):
        """신뢰불가 입력 — 형식이 조금이라도 어긋나면 그 줄만 버린다. 서버는 내용을
        **복호하지 않는다**(못 한다)."""
        try:
            d = json.loads(line)
        except ValueError:
            return None
        if not isinstance(d, dict):
            return None
        kind, rkey = d.get("kind"), d.get("rkey")
        if kind not in KINDS or not isinstance(rkey, str) or not rkey:
            return None
        if len(rkey) > 64 or not all(c in "0123456789abcdef" for c in rkey):
            return None
        acct = d.get("acct_id")
        if acct is not None and (not isinstance(acct, str) or len(acct) > 64
                                 or not all(c in "0123456789abcdef" for c in acct)):
            return None
        try:
            ct = wa.b64u_decode(d.get("ct") or "")
            nonce = wa.b64u_decode(d.get("nonce") or "")
        except wa.WebAuthnError:
            return None
        if not ct or len(ct) > 64 * 1024 or len(nonce) not in (12, 24):
            return None
        return {"kind": kind, "rkey": rkey, "acct_id": acct, "ct": ct,
                "nonce": nonce}

    def _session_vault(self, headers):
        sid = _cookie(headers.get("cookie") or "", "sync_sid")
        return sdb.session_vault(self.conn, sid, self._now()) if sid else None

    def _take_challenge(self, b64: str, kind: str):
        """챌린지는 **1회용**이다 — 꺼내면서 지운다(재사용 = 재생 공격)."""
        now = self._now()
        for k, v in list(self._chal.items()):
            if v[2] <= now:
                self._chal.pop(k, None)
        try:
            raw = wa.b64u_decode(b64 or "")
        except wa.WebAuthnError:
            return None
        ent = self._chal.pop(raw.hex(), None)
        if ent is None or ent[0] != kind:
            return None
        return raw, ent[1]

    def _body_json(self, body):
        try:
            d = json.loads(body or b"{}")
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    def _deny(self, reason):
        self._bump(reason)
        return None

    def _bump(self, reason):
        self.stats[reason] = self.stats.get(reason, 0) + 1

    def _bad_auth(self, reason):
        """4xx 응답은 **형태를 하나로** 유지한다 — 사유별로 다른 메시지를 주면
        공격자에게 오라클이 된다. 사유는 서버 카운터에만 남는다."""
        self._bump(reason)
        return self._err(401, "unauthorized")

    def _json(self, status, obj, cookie=None):
        h = {"Content-Type": "application/json; charset=utf-8"}
        if cookie:
            h["Set-Cookie"] = (
                "sync_sid=%s; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=%d"
                % (cookie, int(SESSION_TTL)))
        return status, h, json.dumps(obj, ensure_ascii=False).encode()

    def _err(self, status, code):
        return self._json(status, {"error": code})


def _int(s, default):
    try:
        return int(s)
    except (TypeError, ValueError):
        return default


def _cookie(header: str, name: str):
    for part in header.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def sign_request(secret_key, method, path, ts, nonce, body, query=""):
    """클라이언트용 서명 헬퍼(pytmux 워커·테스트가 쓴다). secret_key =
    Ed25519PrivateKey. 서버 `signing_string` 과 **한 곳에서** 형식을 공유해야
    미묘한 불일치로 401 이 나지 않는다."""
    full = path + (("?" + query) if query else "")
    msg = "|".join(["v1", method, full, str(ts), str(nonce),
                    hashlib.sha256(body).hexdigest()]).encode()
    return wa.b64u_encode(secret_key.sign(msg))


# ── 소켓 계층(운영용) ──────────────────────────────────────────────────────

def make_handler(app):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        server_version = "synserver"        # 버전 문자열 노출 최소화
        sys_version = ""

        def _run(self, method):
            length = _int(self.headers.get("Content-Length"), 0)
            body = self.rfile.read(min(length, MAX_BODY + 1)) if length else b""
            parsed = urllib.parse.urlsplit(self.path)
            status, headers, out = app.handle(
                method, parsed.path, parsed.query, dict(self.headers), body)
            self._status = status          # 접근 로그에 상태를 남긴다
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(out)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(out)

        def do_GET(self):
            self._run("GET")

        def do_POST(self):
            self._run("POST")

        def do_DELETE(self):
            self._run("DELETE")

        def log_message(self, fmt, *args):
            """접근 로그는 **경로와 상태만**. 헤더·바디·가명은 절대 남기지 않는다."""
            sys.stderr.write("%s %s %s\n" % (self.command,
                                             self.path.split("?")[0],
                                             getattr(self, "_status", "-")))

    return Handler


def main(argv=None):
    import argparse
    from http.server import ThreadingHTTPServer
    p = argparse.ArgumentParser(description="pytmux 토큰 동기화 서버")
    p.add_argument("--db", default="sync.db")
    p.add_argument("--rp-id", required=True, help="패스키 도메인(예: sync.example.org)")
    p.add_argument("--origin", default=None, help="기본 https://<rp-id>")
    p.add_argument("--host", default="127.0.0.1", help="TLS 는 앞단 리버스 프록시가")
    p.add_argument("--port", type=int, default=8787)
    a = p.parse_args(argv)
    conn = sdb.connect(a.db)
    app = SyncApp(conn, a.rp_id, a.origin or ("https://" + a.rp_id))
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(app))
    sys.stderr.write("synserver %s:%d rp=%s\n" % (a.host, a.port, a.rp_id))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        conn.close()


if __name__ == "__main__":
    main()
