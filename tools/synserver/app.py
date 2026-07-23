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
# 세션은 **명시적 로그아웃 전까지** 유지된다(요청). 새로고침마다 지문을 다시 대는 것은
# 관리 화면에서 과한 마찰이고, 쿠키는 __Host-·HttpOnly·Secure·SameSite=Strict 라
# 스크립트·타 사이트가 못 읽는다. 대신 활동이 있을 때마다 갱신(슬라이딩)하고,
# 로그아웃은 **서버에서 즉시 삭제**한다(Q-2).
SESSION_TTL = 7 * 24 * 3600.0
RATE_BURST = 60                     # 디바이스당 분당 요청
UNAUTH_BURST = 30                   # 미인증 경로 **전역** 분당 요청(S-1)
MAX_CHALLENGES = 512                # 진행 중 챌린지 상한(S-3)
PULL_BYTE_BUDGET = 4 * 1024 * 1024  # 다운로드 응답 바이트 예산(S-6)
PURGE_INTERVAL = 60.0               # 만료 상태 정리 최소 간격(S-5)
COOKIE = "__Host-sync_sid"          # 접두가 Path=/·Secure·도메인 미지정을 강제(Q-3)
KINDS = ("xc", "lim")


class SyncApp:
    def __init__(self, conn, rp_id: str, origin: str, now=None,
                 open_registration: bool = False, bootstrap_token: str = ""):
        self.conn = conn
        self.rp_id = rp_id
        self.origin = origin
        self._now = now or time.time
        # S-1: 공개 종단이라 "아무나 vault 를 만들 수 있음" 은 그 자체로 자원 고갈이다
        # (실측: 인증 없이 50/50 생성). 기본은 **첫 vault 만** 열고 그 뒤로 닫는다.
        # 여러 사람이 쓸 서버라면 --open-registration, 스스로 여러 vault 를 만들
        # 계획이면 --bootstrap-token 으로 연다.
        self.open_registration = bool(open_registration)
        self.bootstrap_token = str(bootstrap_token or "")
        # 잠김 복구(§5.9 보강): 패스키를 전부 잃으면 로그인도, 새 등록도 막힌다
        # (S-1 잠금이 의도대로 동작한 결과). 서버를 **직접 운영하는 사람**만 쓸 수 있는
        # 일회용 토큰으로 세션을 발급해 그 자리에서 새 패스키를 추가하게 한다.
        # 토큰은 한 번 쓰면 사라지고, 서버 재시작 없이는 다시 만들 수 없다.
        self.recovery_token = ""
        self._unauth = [0.0, 0]         # [윈도 시작, 카운트] — 미인증 전역
        self._last_purge = 0.0
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

    _UNAUTH_ROUTES = {("POST", "/v1/enroll/options"), ("POST", "/v1/enroll/verify"),
                      ("POST", "/v1/auth/options"), ("POST", "/v1/auth/verify"),
                      ("POST", "/v1/devices"), ("POST", "/v1/recover")}

    def _handle_locked(self, method, path, query, headers, body, q):
        try:
            self._purge_due()
            if (method, path) in self._UNAUTH_ROUTES and not self._unauth_ok():
                self._bump("unauth_rate")
                return self._err(429, "slow_down")
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
            if route == ("POST", "/v1/recover"):
                return self._recover_code(body)
            if method == "GET" and path == "/v1/recover":
                return self._recover(q)
            if route == ("GET", "/v1/session"):
                return self._session_state(headers)
            if route == ("POST", "/v1/vault/key"):
                return self._vault_key_put(headers, body)
            if route == ("GET", "/v1/vault/key"):
                return self._vault_key_get(headers)
            if route == ("POST", "/v1/logout"):
                return self._logout(headers)
            if route == ("POST", "/v1/pairing"):
                return self._pairing(headers, body)
            if route == ("POST", "/v1/devices"):
                return self._device_add(headers, body)
            if route == ("GET", "/v1/devices"):
                return self._device_list(headers)
            if route == ("DELETE", "/v1/devices/self"):
                return self._device_self_revoke(method, path, headers, body)
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
        self._remember_challenge(ch, "create", vault_id)
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

    def _may_create_vault(self, headers) -> bool:
        """신규 vault 를 만들어도 되는가(S-1).

        · open_registration=True  → 항상 허용(여러 사람이 쓰는 서버)
        · bootstrap_token 설정됨   → X-Sync-Bootstrap 헤더가 **정확히** 일치할 때만
        · 둘 다 아니면            → vault 가 하나도 없을 때(=최초 1회)만
        기본값이 마지막이라 "내 서버" 는 첫 등록 뒤 자동으로 닫힌다."""
        if self.open_registration:
            return True
        if self.bootstrap_token:
            got = headers.get("x-sync-bootstrap") or ""
            return hmac.compare_digest(got, self.bootstrap_token)
        return sdb.vault_count(self.conn) == 0

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
        if ch[1] is None and not self._may_create_vault(headers):
            return self._bad_auth("registration_closed")
        vault_id = ch[1] or sdb.create_vault(self.conn, now)
        if sdb.get_passkey(self.conn, reg["cred_id"]) is not None:
            return self._bad_auth("credential_exists")
        try:
            sdb.add_passkey(self.conn, vault_id, reg["cred_id"], reg["cose_key"],
                            reg["sign_count"], reg["aaguid"],
                            label=(d.get("label") or None), now=now)
        except sdb.LimitExceeded:
            self._bump("passkey_limit")
            return self._err(409, "limit")
        sid = sdb.new_session(self.conn, vault_id, now, SESSION_TTL)
        out = {"ok": True}
        if ch[1] is None:
            # **새 vault 를 만든 그 순간에만** 복구 코드를 준다. 패스키를 잃으면
            # 이것 말고는 스스로 돌아올 길이 없다(관리자 개입은 해법이 아니다).
            out["recovery_code"] = sdb.new_recovery_code(self.conn, vault_id, now)
        return self._json(200, out, cookie=sid)

    def _auth_options(self):
        """discoverable 로그인 — `allowCredentials` 를 **비워** 아이디 입력 없이,
        인증기가 스스로 어느 vault 인지 알려 준다."""
        ch = wa.new_challenge()
        self._remember_challenge(ch, "get", None)
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

    def _recover_code(self, body):
        """복구 코드로 기존 vault 세션을 연다 — **사용자 스스로** 잠김을 푸는 길.

        얻는 것은 세션뿐이다: vault 키는 패스키(PRF)나 암호구절이 쥐고 있어 이 코드로는
        데이터를 읽지 못한다. 그래서 코드가 새도 **기록은 안전**하고, 대신 새 패스키를
        붙일 수 있다. 1회용이며 성공 즉시 새 코드를 발급해 돌려준다."""
        d = self._body_json(body)
        if d is None:
            return self._err(400, "bad_request")
        vault_id = sdb.use_recovery_code(self.conn, str(d.get("code") or ""),
                                         self._now())
        if not vault_id:
            return self._bad_auth("recovery_code")
        now = self._now()
        sid = sdb.new_session(self.conn, vault_id, now, SESSION_TTL)
        return self._json(200, {"ok": True,
                                "recovery_code": sdb.new_recovery_code(
                                    self.conn, vault_id, now)}, cookie=sid)

    def _recover(self, q):
        """일회용 복구 링크 — 토큰이 맞으면 **기존 vault** 세션을 발급하고 첫 화면으로.

        새 vault 를 만들지 않는다는 점이 핵심이다: 기존 vault 에 붙어야 등록된 기기와
        감싼 키가 그대로 살아 있다. 토큰은 상수시간 비교 후 **즉시 폐기**한다."""
        token = (q.get("t") or [""])[0]
        if not self.recovery_token or not token:
            return self._bad_auth("recover")
        if not hmac.compare_digest(token, self.recovery_token):
            return self._bad_auth("recover")
        row = self.conn.execute(
            "SELECT vault_id FROM vault ORDER BY created LIMIT 1").fetchone()
        if row is None:
            return self._bad_auth("recover")
        self.recovery_token = ""            # 일회용
        sid = sdb.new_session(self.conn, row["vault_id"], self._now(), SESSION_TTL)
        status, headers_out, body = self._json(200, {"ok": True}, cookie=sid)
        headers_out["Location"] = "/"
        return 302, headers_out, body

    def _session_state(self, headers):
        """새로고침 후 페이지가 로그인 상태를 되찾는 경로.

        쿠키는 살아 있는데 화면이 로그아웃처럼 보이던 문제(제보) — 페이지가 세션을
        **묻지 않았기** 때문이다. vault_id 는 돌려주지 않는다(필요 없고, 노출면만 는다).
        키 보관 여부는 알려 줘 화면이 '키 잠김'을 안내할 수 있게 한다."""
        vault_id = self._session_vault(headers)
        # vault 존재 여부는 화면이 **고아 패스키를 만들지 않도록** 필요하다(로그아웃
        # 상태의 '새 패스키 만들기' 를 사전에 막는다). 이미 등록 거부 응답으로 알 수
        # 있는 사실이라 새로 새는 정보는 없다.
        exists = sdb.vault_count(self.conn) > 0
        if not vault_id:
            return self._json(200, {"authenticated": False,
                                    "vault_exists": exists})
        return self._json(200, {"authenticated": True,
                                "vault_exists": exists,
                                "has_key": sdb.get_vault_key(self.conn,
                                                             vault_id) is not None})

    def _vault_key_put(self, headers, body):
        """패스키로 감싼 마스터 키를 보관한다(§5.3a). 서버는 이걸 **풀 수 없다**."""
        vault_id = self._session_vault(headers)
        if not vault_id:
            return self._bad_auth("session")
        d = self._body_json(body)
        if d is None:
            return self._err(400, "bad_request")
        try:
            wrapped = wa.b64u_decode(d.get("wrapped") or "")
        except wa.WebAuthnError:
            return self._err(400, "bad_request")
        if not (0 < len(wrapped) <= 4096):
            return self._err(400, "bad_request")
        meta = str(d.get("meta") or "")[:256]
        ok = sdb.set_vault_key(self.conn, vault_id, wrapped, meta,
                               overwrite=bool(d.get("overwrite")))
        # 이미 있는데 덮어쓰기를 안 했으면 409 — 조용히 무시하면 브라우저는 자기 키가
        # 올라간 줄 알고, 나중에 다른 기기가 **다른 키**로 복호를 시도한다.
        return self._json(200 if ok else 409, {"stored": ok})

    def _vault_key_get(self, headers):
        vault_id = self._session_vault(headers)
        if not vault_id:
            return self._bad_auth("session")
        rec = sdb.get_vault_key(self.conn, vault_id)
        if rec is None:
            return self._json(404, {"error": "no_key"})
        return self._json(200, {"wrapped": wa.b64u_encode(rec["wrapped"]),
                                "meta": rec["meta"]})

    def _logout(self, headers):
        """세션을 **서버에서** 지운다(Q-2). 쿠키 만료만 기다리면 훔친 쿠키가 TTL 동안
        유효하다."""
        sid = _cookie(headers.get("cookie") or "", COOKIE)
        if sid:
            sdb.drop_session(self.conn, sid)
        return self._json(200, {"ok": True}, cookie="")

    def _pairing(self, headers, body=b""):
        vault_id = self._session_vault(headers)
        if not vault_id:
            return self._bad_auth("session")
        d = self._body_json(body) or {}
        ct = nonce = None
        if d.get("key_ct"):
            try:
                ct = wa.b64u_decode(d.get("key_ct") or "")
                nonce = wa.b64u_decode(d.get("key_nonce") or "")
            except wa.WebAuthnError:
                return self._err(400, "bad_request")
            if not (0 < len(ct) <= 4096) or len(nonce) != 12:
                return self._err(400, "bad_request")
        code_h = str(d.get("code_h") or "")
        if code_h:
            # §5.3a: 코드를 **브라우저가** 만들고 해시만 등록한다. 서버가 코드를 만들면
            # 그 순간 서버도 코드를 알아 감싼 키를 풀 수 있다("서버 혼자서는 못 푼다"가
            # 깨진다). 그래서 키를 나르는 경로에서는 서버가 원문을 보지 않는다.
            if len(code_h) != 64 or any(c not in "0123456789abcdef" for c in code_h):
                return self._err(400, "bad_request")
            sdb.put_pairing(self.conn, vault_id, code_h, self._now(),
                            key_ct=ct, key_nonce=nonce)
            return self._json(200, {"expires_in": 600, "carries_key": bool(ct)})
        code = sdb.new_pairing(self.conn, vault_id, self._now())
        return self._json(200, {"code": code, "expires_in": 600,
                                "carries_key": False})

    def _device_add(self, headers, body):
        """세션(같은 브라우저) 또는 **1회용 페어링 코드**(헤드리스 머신)로 등록."""
        d = self._body_json(body)
        if d is None:
            return self._err(400, "bad_request")
        vault_id = self._session_vault(headers)
        paired = None
        if not vault_id and d.get("pairing_code_h"):
            # 머신은 **해시만** 보낸다(원문은 서버가 못 본다 — §5.3a).
            paired = sdb.consume_pairing_h(self.conn, str(d["pairing_code_h"]),
                                           self._now())
            vault_id = paired["vault_id"] if paired else None
        elif not vault_id and d.get("pairing_code"):
            paired = sdb.consume_pairing(self.conn, d["pairing_code"], self._now())
            vault_id = paired["vault_id"] if paired else None
        if not vault_id:
            return self._bad_auth("pairing")
        try:
            pub = wa.b64u_decode(d.get("pubkey") or "")
        except wa.WebAuthnError:
            return self._err(400, "bad_request")
        if len(pub) != 32:                       # Ed25519 raw
            return self._err(400, "bad_request")
        try:
            host_id = str(d.get("host_id") or "")[:64] or None
            if host_id and not all(c in "0123456789abcdefABCDEF-" for c in host_id):
                return self._err(400, "bad_request")
            did = sdb.add_device(self.conn, vault_id, pub,
                                 label=(d.get("label") or None), now=self._now(),
                                 host_id=host_id)
        except sdb.LimitExceeded:
            self._bump("device_limit")
            return self._err(409, "limit")
        out = {"device_id": did}
        # §5.3a: 코드에 실려 온 **감싼 마스터 키**를 그 한 번만 돌려준다. 머신은 자기
        # 코드로 풀어 K 를 얻는다 — invite/adopt 로 손수 옮기던 단계가 사라진다.
        if paired and paired.get("key_ct"):
            out["key_ct"] = wa.b64u_encode(bytes(paired["key_ct"]))
            out["key_nonce"] = wa.b64u_encode(bytes(paired["key_nonce"] or b""))
        return self._json(200, out)

    def _device_list(self, headers):
        vault_id = self._session_vault(headers)
        if not vault_id:
            return self._bad_auth("session")
        return self._json(200, {"devices": sdb.list_devices(self.conn, vault_id)})

    def _device_self_revoke(self, method, path, headers, body):
        """기기가 **자기 자신**을 지운다(서명 인증). 등록 직후 키 설치가 실패했을 때
        클라이언트가 스스로 뒷정리하는 경로 — 실패한 등록이 서버에 남지 않게 한다.
        남의 기기는 건드릴 수 없다(vault·device 를 서명에서 유도한다)."""
        dev = self._device_auth(method, path, headers, body)
        if dev is None:
            return self._bad_auth("signature")
        ok = sdb.revoke_device(self.conn, dev["vault_id"], dev["device_id"],
                               self._now())
        return self._json(200, {"ok": bool(ok)})

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
        # S-6: 행 수만 제한하면 64KB 레코드 5,000건 = 한 응답에 ~328MB 를 메모리에
        # 조립한다(업로드엔 MAX_BODY 가 있는데 다운로드엔 없었다). 바이트 예산에서
        # 잘리면 거기까지만 준다 — 클라는 커서 기반이라 **다음 요청에서 이어받는다**.
        out, used, truncated = [], 0, False
        for r in rows:
            line = json.dumps({
                "seq": r["seq"], "kind": r["kind"], "acct_id": r["acct_id"],
                "rkey": r["rkey"], "ct": wa.b64u_encode(r["ct"]),
                "nonce": wa.b64u_encode(r["nonce"])}, ensure_ascii=False)
            if out and used + len(line) > PULL_BYTE_BUDGET:
                truncated = True
                break
            out.append(line)
            used += len(line) + 1
        if truncated:
            self._bump("pull_truncated")
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

    def _unauth_ok(self):
        """미인증 경로의 **전역** 분당 상한(S-1). 터널 뒤라 클라이언트 IP 를 신뢰할
        수 없어 IP 별이 아니라 전역으로 건다 — 개인용 서버라 정상 사용은 분당 몇 건이다."""
        now = self._now()
        if now - self._unauth[0] >= 60.0:
            self._unauth = [now, 1]
            return True
        self._unauth[1] += 1
        return self._unauth[1] <= UNAUTH_BURST

    def _purge_due(self):
        """만료된 nonce/세션/페어링 정리(S-5). 요청 수가 아니라 **시간 기반**이라
        트래픽이 뜸해도 언젠가는 돈다. 예전엔 purge_expired 가 아무 데서도 안 불려
        nonce_seen 이 요청마다 단조 증가했다."""
        now = self._now()
        if now - self._last_purge < PURGE_INTERVAL:
            return
        self._last_purge = now
        try:
            sdb.purge_expired(self.conn, now)
        except Exception:           # noqa: BLE001 — 정리 실패가 요청을 죽이면 안 된다
            self._bump("purge_failed")

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
        sid = _cookie(headers.get("cookie") or "", COOKIE)
        return (sdb.session_vault(self.conn, sid, self._now(), renew=SESSION_TTL)
                if sid else None)

    def _remember_challenge(self, ch: bytes, kind: str, vault_id):
        """진행 중 챌린지를 기록한다 — **발급 시점에** 만료분을 걷고 상한을 건다(S-3).
        예전엔 소비 때만 걷고 상한이 없어 미인증 호출만으로 무한히 쌓였다(실측 2,000개).
        상한을 넘겨 밀려나는 것은 진행 중이던 등록/로그인뿐이고 다시 누르면 된다."""
        now = self._now()
        for k, v in list(self._chal.items()):
            if v[2] <= now:
                self._chal.pop(k, None)
        while len(self._chal) >= MAX_CHALLENGES:
            self._chal.pop(next(iter(self._chal)), None)   # 가장 오래된 것부터
        self._chal[ch.hex()] = (kind, vault_id, now + CHALLENGE_TTL)

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
        if cookie is not None:
            # Q-3: `__Host-` 접두는 Path=/ · Secure · Domain 미지정을 브라우저가
            # **강제**한다 — 하위도메인이 쿠키를 심어 오염시키는 경로가 원천 차단된다.
            h["Set-Cookie"] = (
                "%s=%s; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=%d"
                % (COOKIE, cookie, int(SESSION_TTL) if cookie else 0))
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
    p.add_argument("--open-registration", action="store_true",
                   help="아무나 새 vault 를 만들 수 있게(기본: 첫 등록 뒤 잠김)")
    p.add_argument("--bootstrap-token", default="",
                   help="새 vault 생성 시 요구할 X-Sync-Bootstrap 값")
    p.add_argument("--recovery-token", default="",
                   help="일회용 복구 링크 토큰(/v1/recover?t=…) — 패스키를 전부 잃었을 때")
    a = p.parse_args(argv)
    conn = sdb.connect(a.db)
    app = SyncApp(conn, a.rp_id, a.origin or ("https://" + a.rp_id),
                  open_registration=a.open_registration,
                  bootstrap_token=a.bootstrap_token)
    app.recovery_token = a.recovery_token
    if a.recovery_token:
        sys.stderr.write("recovery link armed: /v1/recover?t=<token> (일회용)\n")
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
