"""토큰 사용량 동기화 클라이언트 — 서버로 push/pull 하는 머신측 절반.

설계: docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md §5.6.
서버측 절반은 `tools/synserver/`(별도 프로세스, pytmux 런타임 의존 아님).

P2 범위 = **`limits`(5h%/1w% 스냅샷) 동기화만**. 이유는 §1.3 — 한도 퍼센트는 이미
계정 전역 값이라 머신 간 어긋남의 원인이 "회계"가 아니라 **관측 부재**다. 그래서
행 수가 1/20 인 limits 만 먼저 돌려도 체감 문제의 절반이 사라진다. `usage_xc`
본체는 P4.

세 가지 규율:

1. **커서는 성공 뒤에만 전진**한다 — 실패 시 그대로 두는 것이 곧 재시도 큐다
   (별도 오프라인 큐가 없는 이유).
2. **서버 응답은 적대적 입력**이다 — AEAD 복호가 1차 방어, 그 뒤 필드별 검증.
3. **블로킹은 전부 executor** — HTTP·crypto·파일이 이벤트 루프에 올라가면 안 된다
   (이 프로젝트에서 4회 물린 항목).

전송은 `transport(method, path, query, body, headers) -> (status, headers, body)`
콜러블로 주입한다. 프로덕션은 urllib(아래 `http_transport`), 테스트는 서버 앱의
`handle` 을 직접 물려 소켓 없이 전 경로를 돈다.
"""
from __future__ import annotations

import json
import os
import threading
import time

from . import syncrypto, usagedb

# 기본 동기화 서버(요청: 머신마다 설정하지 않아도 되게).
#
# · 이 저장소는 **공개 미러**가 있으므로 여기 박히는 주소는 공개된다. 그래도 안전한
#   이유: ① 등록은 첫 vault 이후 잠겨 있어 남이 붙지 못하고, ② **등록 전에는 네트워크
#   요청이 아예 안 나간다**(워커가 NotEnrolled 에서 HTTP 이전에 멈춘다) — 즉 기본값을
#   들고만 있는 머신은 트래픽 0 이다.
# · 자기 서버를 쓰는 사람은 코드를 고칠 필요 없이 `PYTMUX_TOKEN_SYNC_URL` 이나
#   `claude-token-sync on <URL>` 로 덮어쓴다.
DEFAULT_SYNC_URL = "https://pytmux-sync.woojinkim.org"


def default_sync_url() -> str:
    """기본 서버 주소. 환경변수가 있으면 그쪽이 우선(자기 서버·테스트용)."""
    return (os.environ.get("PYTMUX_TOKEN_SYNC_URL") or DEFAULT_SYNC_URL).rstrip("/")


KIND_LIM = "lim"
PUSH_BATCH = 500
PULL_BATCH = 1000
MAX_SKEW_YEARS = 1.0
_YEAR = 365 * 24 * 3600.0
PCT_MAX = 1000                  # 퍼센트 상한(적대적 거대값 차단 — 100 초과도 실측 존재)


class SyncError(Exception):
    """동기화 실패. 사유는 `sync_remote.last_err` 로 남는다(조용한 실패 금지)."""


class NotEnrolled(SyncError):
    """이 머신이 아직 서버에 등록되지 않았다(`:claude-token-sync enroll <코드>`)."""


# ── 전송 ───────────────────────────────────────────────────────────────────

def http_transport(base_url: str, timeout: float = 15.0):
    """urllib 기반 전송(블로킹 — 반드시 executor 에서 부른다).

    **리다이렉트를 따라가지 않는다** — 302 로 다른 호스트에 데이터를 흘리지 않게
    (설계 §8.3). 사용자가 지정한 URL 그 자체로만 말한다."""
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **kw):
            return None

    opener = urllib.request.build_opener(_NoRedirect,
                                         urllib.request.HTTPSHandler(
                                             context=_ssl_context()))

    def send(method, path, query="", body=b"", headers=None):
        url = base_url.rstrip("/") + path + (("?" + query) if query else "")
        req = urllib.request.Request(url, data=(body or None), method=method)
        # **User-Agent 를 반드시 보낸다**. urllib 기본값(Python-urllib/3.x)은 CDN·WAF 가
        # 봇으로 보고 차단한다 — 실기동에서 Cloudflare 가 error 1010(브라우저 무결성
        # 검사)로 403 을 돌려줘 요청이 오리진에 닿지도 못했다. 브라우저 경로(등록
        # 페이지)만 멀쩡해서 원인이 서버인 줄 알고 한참 헤맸다.
        req.add_header("User-Agent", "pytmux-tokensync/1.0")
        req.add_header("Accept", "application/json, application/x-ndjson")
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()
        except OSError as e:
            raise SyncError(_net_why(e)) from e

    return send


def _ssl_context():
    """TLS 검증 컨텍스트. **검증을 끄지 않는다** — 끄면 이 설계의 전제(전송 무결성)가
    무너진다.

    macOS 의 python.org 빌드는 시스템 루트 저장소를 쓰지 않아 기본 컨텍스트가 비어
    있고, 그러면 정상 서버인데도 CERTIFICATE_VERIFY_FAILED 가 난다(실기동에서 다른
    머신이 그랬다). certifi 가 있으면 그 번들을 쓰고, 없으면 그대로 두되 실패 시
    **무엇을 해야 하는지** 알려 준다(_net_why)."""
    import ssl
    ctx = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        return ctx
    try:
        ctx.load_verify_locations(certifi.where())
    except OSError:
        pass
    return ctx


def _net_why(exc) -> str:
    """네트워크 실패 사유 — 원문만 던지면 사용자가 할 일을 모른다.

    **한 줄 안에 조치가 들어가야 한다**: 상태줄 알림은 한 줄이라 길면 잘린다(실기동에서
    "[SSL: CERTIFICATE_VERIFY_" 에서 끊겨 정작 조치가 안 보였다). 자세한 원문은
    서버 로그(error.log)로 가고, 여기서는 **무엇을 하면 되는지**만 짧게 준다."""
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return "TLS 인증서 검증 실패 — 'Install Certificates.command' 실행 후 재시작"
    if "Name or service not known" in text or "nodename nor servname" in text:
        return "서버 주소를 찾지 못했습니다(DNS) — token_sync_url 확인"
    if "Connection refused" in text:
        return "서버가 연결을 거부했습니다 — 서버가 떠 있는지 확인"
    if "timed out" in text:
        return "서버 응답 시간 초과 — 네트워크·서버 상태 확인"
    return "서버에 닿지 못했습니다: %s" % text[:60]


# ── 클라이언트 ─────────────────────────────────────────────────────────────

class SyncClient:
    """한 머신의 동기화 상태(키·기기 id·커서)와 push/pull 을 담는다.

    **블로킹 클래스**다 — 호출자가 executor 로 돌린다. 여기서 asyncio 를 몰라야
    테스트가 단순해진다."""

    REMOTE = "server"       # sync_remote 의 키(T5 는 원격이 서버 하나)

    def __init__(self, conn, db_dir: str, transport, *, accounts=(),
                 encrypt: bool = True, now=time.time, db_path: str = ""):
        # conn 을 주면 그대로 쓴다(테스트·같은 스레드 호출). db_path 만 주면 **쓰는
        # 스레드마다 자기 연결**을 연다 — 워커는 executor 스레드에서 도는데 sqlite
        # 연결은 만든 스레드에서만 쓸 수 있다(실기동: "SQLite objects created in a
        # thread can only be used in that same thread"). 서버 연결을 공유하지 않는
        # 이유는 한 연결을 두 스레드가 섞어 쓰면 **트랜잭션이 교차**하기 때문이다.
        # WAL + busy_timeout + 멱등 쓰기(INSERT OR IGNORE·커서 갱신)라 별도 연결이 안전.
        self._conn = conn
        self._db_path = db_path
        self._tls = threading.local()
        self.db_dir = db_dir
        self.transport = transport
        self.accounts = tuple(a for a in (accounts or ()) if a)
        self.encrypt = bool(encrypt)
        self._now = now
        self._master = None
        self._k_id = None
        self._k_enc = None
        self._device_sk = None

    # -- 키·신원 ----------------------------------------------------------

    @property
    def conn(self):
        """이 스레드에서 쓸 DB 연결(주입됐으면 그것, 아니면 스레드별 지연 오픈)."""
        if self._conn is not None:
            return self._conn
        c = getattr(self._tls, "conn", None)
        if c is None:
            if not self._db_path:
                raise SyncError("토큰 DB 경로를 모릅니다")
            c = usagedb.connect(self._db_path)
            self._tls.conn = c
        return c

    @property
    def host_id(self) -> str:
        return syncrypto.ensure_host_id(self.db_dir)

    def _keys(self):
        if self._k_id is None:
            self._master = syncrypto.load_or_create_master(
                os.path.join(self.db_dir, "sync_vault.key"))
            self._k_id, self._k_enc = syncrypto.derive_keys(self._master)
        return self._k_id, self._k_enc

    def invite_code(self) -> str:
        """다른 머신에 마스터 키를 옮길 1회용 문자열. **서버를 통과하지 않는다**."""
        self._keys()
        return syncrypto.format_invite(self._master)

    def adopt_invite(self, code: str, force: bool = False) -> None:
        """다른 머신에서 만든 마스터 키를 받아들인다.

        C-2(검수): 예전 주석은 "호출자가 확인을 받는다" 였지만 **실제 호출자는 아무
        확인도 하지 않았다**. 이미 이 머신이 자기 키로 올린 이력이 있으면 키 교체는
        그 데이터를 복호 불능으로 만든다 → 기본은 거부하고 force 로만 강행한다."""
        master = syncrypto.parse_invite(code)
        path = os.path.join(self.db_dir, "sync_vault.key")
        if not force and os.path.exists(path):
            sent = usagedb.get_export_cursor(self.conn, "limits")
            if sent:
                raise SyncError(
                    "이 머신은 이미 자기 키로 %d행까지 올렸습니다 — 키를 바꾸면 그"
                    " 데이터는 복호할 수 없게 됩니다. 정말 바꾸려면"
                    " ':claude-token-sync adopt <코드> force'" % sent)
        syncrypto.save_master(path, master)
        self._master, self._k_id, self._k_enc = None, None, None
        if force:
            # 키가 바뀌었으면 예전 키로 올린 것은 남의 것이나 마찬가지다 — 커서를
            # 되돌려 새 키로 다시 올린다(멱등이라 안전).
            usagedb.set_export_cursor(self.conn, "limits", 0, self._now())

    def _device_key(self):
        """머신 고유 Ed25519 키(파일 0600). 없으면 만든다."""
        if self._device_sk is not None:
            return self._device_sk
        try:
            from cryptography.hazmat.primitives import serialization as ser
            from cryptography.hazmat.primitives.asymmetric import ed25519
        except ImportError as e:
            # 원인만 던지면(=ImportError 원문) 사용자는 무엇을 해야 할지 모른다.
            raise syncrypto.SyncCryptoUnavailable(syncrypto.INSTALL_HINT) from e
        path = os.path.join(self.db_dir, "sync_device.key")
        try:
            with open(path, "rb") as f:
                raw = f.read()
            if len(raw) == 32:
                self._device_sk = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
                return self._device_sk
        except OSError:
            pass
        sk = ed25519.Ed25519PrivateKey.generate()
        raw = sk.private_bytes(ser.Encoding.Raw, ser.PrivateFormat.Raw,
                               ser.NoEncryption())
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, raw)
        finally:
            os.close(fd)
        self._device_sk = sk
        return sk

    def _device_id(self):
        try:
            with open(os.path.join(self.db_dir, "sync_device.id"),
                      encoding="ascii") as f:
                return f.read().strip() or None
        except OSError:
            return None

    def enroll(self, pairing_code: str, label=None) -> str:
        """1회용 페어링 코드로 이 머신을 서버에 등록한다 → device_id."""
        from cryptography.hazmat.primitives import serialization as ser
        pub = self._device_key().public_key().public_bytes(
            ser.Encoding.Raw, ser.PublicFormat.Raw)
        # 코드 **원문 대신 해시**를 보낸다 — 원문을 보내면 서버가 pair_key 를 만들어
        # 함께 보관 중인 감싼 키를 풀 수 있다(§5.3a).
        body = json.dumps({"pairing_code_h": syncrypto.code_hash(pairing_code),
                           "pubkey": _b64u(pub),
                           "label": label or _hostname()}).encode()
        status, _, resp = self.transport(
            "POST", "/v1/devices", "", body,
            {"Content-Type": "application/json"})
        if status != 200:
            # 사유를 뭉뚱그리지 않는다 — 예전엔 어떤 실패든 "코드 만료·오타" 라고만
            # 해서, 실제로는 앞단 CDN 이 막고 있었는데 코드를 계속 새로 발급했다.
            raise SyncError(_http_why(status, resp, "등록"))
        try:
            out = json.loads(resp)
            did = out["device_id"]
        except (ValueError, KeyError) as e:
            raise SyncError("서버 응답 형식 오류") from e
        # §5.3a: 응답에 **감싼 마스터 키**가 실려 오면 페어링 코드로 풀어 심는다 —
        # invite/adopt 로 손수 옮기던 단계가 사라진다. 키가 없으면(구 서버·폴백 vault)
        # 종전대로 사용자가 adopt 로 넣는다.
        if out.get("key_ct"):
            self._install_key_from_pairing(pairing_code, out)
        path = os.path.join(self.db_dir, "sync_device.id")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, did.encode("ascii"))
        finally:
            os.close(fd)
        return did

    def _install_key_from_pairing(self, code: str, out: dict) -> None:
        """등록 응답에 실려 온 감싼 키를 코드로 풀어 저장한다.

        이미 자기 키로 **올린 이력이 있으면** 덮어쓰지 않는다 — 그 데이터가 복호 불능이
        되기 때문이다(검수 C-2 와 같은 규율). 그 경우 사용자가 무엇을 해야 하는지
        사유에 적는다."""
        key_path = os.path.join(self.db_dir, "sync_vault.key")
        try:
            k = syncrypto.aes_gcm_open(
                syncrypto.pair_key(code),
                _b64u_dec(out.get("key_nonce") or ""),
                _b64u_dec(out.get("key_ct") or ""))
        except (ValueError, syncrypto.SyncCryptoError) as e:
            raise SyncError("서버가 보낸 키를 풀지 못했습니다: %s" % e) from e
        if len(k) != syncrypto.MASTER_LEN:
            raise SyncError("서버가 보낸 키 길이가 올바르지 않습니다")
        if os.path.exists(key_path):
            try:
                cur = syncrypto.load_or_create_master(key_path)
            except syncrypto.SyncCryptoError:
                cur = None
            if cur == k:
                return                      # 같은 키 — 재등록은 무해
            if usagedb.get_export_cursor(self.conn, "limits"):
                raise SyncError(
                    "이 머신은 이미 다른 키로 올린 이력이 있습니다 — 계속하려면"
                    " 로컬 키를 지우거나 vault 를 맞추세요(데이터 복호 불능 방지)")
        syncrypto.save_master(key_path, k)
        self._master, self._k_id, self._k_enc = None, None, None

    # -- 서명 요청 --------------------------------------------------------

    def _signed(self, method, path, query="", body=b""):
        did = self._device_id()
        if not did:
            raise NotEnrolled("이 머신은 아직 등록되지 않았습니다")
        ts = "%.0f" % self._now()
        nonce = os.urandom(16).hex()
        full = path + (("?" + query) if query else "")
        import hashlib
        msg = "|".join(["v1", method, full, ts, nonce,
                        hashlib.sha256(body).hexdigest()]).encode()
        sig = _b64u(self._device_key().sign(msg))
        return self.transport(method, path, query, body, {
            "X-Sync-Device": did, "X-Sync-Ts": ts, "X-Sync-Nonce": nonce,
            "X-Sync-Sig": sig, "Content-Type": "application/x-ndjson"})

    # -- push(limits) -----------------------------------------------------

    def push_limits(self, batch: int = PUSH_BATCH) -> dict:
        """아직 안 보낸 자기 limits 행을 봉인해 올린다. {sent, accepted, ignored}.

        대상은 **자기 host 의 lkey 있는 행**뿐이다 — 남에게서 받은 행을 되돌려
        보내면 무한 왕복이 된다(합집합이라 정확성은 깨지지 않지만 낭비다)."""
        if not self.encrypt:
            raise SyncError("평문 업로드는 지원하지 않습니다(token_sync_encrypt=on)")
        k_id, k_enc = self._keys()
        host = self.host_id
        usagedb.backfill_limits_lkey(self.conn, host)
        cursor = usagedb.get_export_cursor(self.conn, "limits")
        rows = self.conn.execute(
            "SELECT rowid AS rid, * FROM limits WHERE rowid>? AND lkey IS NOT NULL"
            " AND (host IS NULL OR host=?) ORDER BY rowid LIMIT ?",
            (cursor, host, int(batch))).fetchall()
        if not rows:
            return {"sent": 0, "accepted": 0, "ignored": 0}
        lines, last = [], cursor
        vault = ""      # AAD 의 vault 자리 — 서버가 vault 를 알려주지 않으므로 빈값
        for r in rows:
            last = r["rid"]
            acct = r["account"]
            if self.accounts and acct not in self.accounts:
                continue                    # 내보내기 계정 화이트리스트(§8.5)
            payload = _limits_payload(r, host)
            rk = syncrypto.rkey(k_id, KIND_LIM, r["lkey"])
            aid = syncrypto.acct_id(k_id, acct)
            nonce, ct = syncrypto.seal(
                k_enc, syncrypto.aad(vault, KIND_LIM, rk, aid),
                json.dumps(payload, ensure_ascii=False).encode())
            lines.append(json.dumps({"kind": KIND_LIM, "rkey": rk,
                                     "acct_id": aid, "ct": _b64u(ct),
                                     "nonce": _b64u(nonce)}))
        if not lines:
            usagedb.set_export_cursor(self.conn, "limits", last, self._now())
            return {"sent": 0, "accepted": 0, "ignored": 0}
        body = ("\n".join(lines) + "\n").encode()
        status, _, resp = self._signed("POST", "/v1/events", "", body)
        if status != 200:
            raise SyncError(_http_why(status, resp, "업로드"))
        try:
            out = json.loads(resp)
        except ValueError as e:
            raise SyncError("서버 응답 형식 오류") from e
        # 성공한 **뒤에만** 커서를 전진시킨다.
        usagedb.set_export_cursor(self.conn, "limits", last, self._now())
        return {"sent": len(lines), "accepted": int(out.get("accepted", 0)),
                "ignored": int(out.get("ignored", 0))}

    # -- pull -------------------------------------------------------------

    def pull(self, batch: int = PULL_BATCH) -> dict:
        """서버에서 새 이벤트를 받아 병합한다. {rows, merged, rejected}."""
        k_id, k_enc = self._keys()
        st = usagedb.get_sync_remote(self.conn, self.REMOTE) or {}
        since = _int(st.get("cursor"), 0)
        query = "since=%d&limit=%d" % (since, int(batch))
        status, _, resp = self._signed("GET", "/v1/events", query, b"")
        if status != 200:
            raise SyncError(_http_why(status, resp, "내려받기"))
        merged = rejected = 0
        last = since
        host = self.host_id
        for line in resp.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                seq = int(ev["seq"])
            except (ValueError, KeyError, TypeError):
                rejected += 1
                continue
            if seq <= last:
                # 서버가 커서를 되돌리거나 재사용하면 무한 루프가 된다 — 방어적 중단.
                raise SyncError("서버 커서가 전진하지 않습니다(seq=%d)" % seq)
            last = seq
            rec = self._open_event(ev, k_id, k_enc)
            if rec is None:
                rejected += 1
                continue
            if rec.get("host") == host:
                continue            # 내가 올린 것 — 로컬에 이미 있다
            if usagedb.import_limits(self.conn, rec, rec.get("host"),
                                     rec.get("_lkey")):
                merged += 1
        # C-3(검수): 성공을 쓴 뒤 오류를 또 쓰면 마지막 값이 성공을 덮어 상태가
        # 뒤집힌다. 한 번만 쓴다.
        usagedb.set_sync_remote(self.conn, self.REMOTE, cursor=str(last),
                                last_ok=self._now(), rows_in_delta=merged,
                                last_err=("거부 %d건" % rejected) if rejected else "")
        return {"rows": last - since, "merged": merged, "rejected": rejected}

    def _open_event(self, ev, k_id, k_enc):
        """이벤트 1건 복호·검증. 조금이라도 어긋나면 None(그 줄만 버린다)."""
        if ev.get("kind") != KIND_LIM:
            return None                    # P2 는 limits 만(usage_xc 는 P4)
        rk, aid = ev.get("rkey"), ev.get("acct_id")
        if not isinstance(rk, str) or not rk:
            return None
        try:
            ct = _b64u_dec(ev.get("ct") or "")
            nonce = _b64u_dec(ev.get("nonce") or "")
            raw = syncrypto.unseal(k_enc, syncrypto.aad("", KIND_LIM, rk, aid),
                                   nonce, ct)
        except (ValueError, syncrypto.SyncCryptoError):
            return None                    # 위조·변조·재조합·키 불일치
        try:
            d = json.loads(raw)
        except ValueError:
            return None
        rec = _validate_limits(d, self._now())
        if rec is None:
            return None
        rec["_lkey"] = usagedb.limits_lkey(rec.get("host"), rec["ts"],
                                           rec["source"], rec.get("account"))
        return rec


# ── 직렬화·검증(순수 함수 — 테스트의 큰 몫) ────────────────────────────────

_LIM_FIELDS = ("ts", "account", "session_pct", "session_reset", "week_all_pct",
               "week_all_reset", "week_sonnet_pct", "week_sonnet_reset",
               "source", "host")


def _limits_payload(row, host) -> dict:
    """limits 행 → 전송 레코드. **화이트리스트 직렬화**(dict 통째 dump 금지) —
    나중에 컬럼이 늘어도 조용히 새 필드가 새 나가지 않게(설계 §8.1)."""
    out = {}
    keys = row.keys() if hasattr(row, "keys") else row
    for f in _LIM_FIELDS:
        out[f] = row[f] if f in keys else None
    out["host"] = row["host"] if ("host" in keys and row["host"]) else host
    out["v"] = 1
    return out


def _validate_limits(d, now: float):
    """신뢰불가 레코드 검증 → 정규화된 dict 또는 None. 복호에 성공했다는 것은
    "우리 키로 만든 것"이라는 뜻이지 "값이 온전하다"는 뜻이 아니다."""
    if not isinstance(d, dict) or d.get("v") != 1:
        return None
    try:
        ts = float(d.get("ts"))
    except (TypeError, ValueError):
        return None
    if not (now - MAX_SKEW_YEARS * _YEAR <= ts <= now + MAX_SKEW_YEARS * _YEAR):
        return None                        # 과거/미래 창 밖
    src = d.get("source")
    if not isinstance(src, str) or not (0 < len(src) <= 32):
        return None
    out = {"ts": ts, "source": src}
    acct = d.get("account")
    if acct is not None and (not isinstance(acct, str) or len(acct) > 256):
        return None
    out["account"] = acct
    host = d.get("host")
    if host is not None and (not isinstance(host, str) or len(host) > 64):
        return None
    out["host"] = host
    for f in ("session_pct", "week_all_pct", "week_sonnet_pct"):
        v = d.get(f)
        if v is None:
            out[f] = None
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if not (0 <= v <= PCT_MAX):
            return None
        out[f] = int(v)
    for f in ("session_reset", "week_all_reset", "week_sonnet_reset"):
        v = d.get(f)
        if v is not None and (not isinstance(v, str) or len(v) > 64):
            return None
        out[f] = v
    return out


# ── 설정(서버 속성 + opts 영속) ────────────────────────────────────────────

def configure(server, *, mode=None, url=None, sec=None, accounts=None,
              encrypt=None) -> dict:
    """동기화 설정을 바꾸고 opts 에 영속한다. 바뀐 값만 넘긴다(None=유지).

    설정을 켤 **경로가 코드 안에 있어야** 한다 — opts.json 직접 편집은 서버가 다음
    저장 때 덮어써 조용히 되돌아간다(실제로 물렸다). url 은 https 만 받는다: 평문
    http 로 보내면 요청 서명은 살아 있어도 가명·암호문·트래픽 메타가 그대로 노출되고,
    패스키 등록 자체도 브라우저가 막는다(localhost 는 개발용 예외)."""
    if mode is not None:
        m = str(mode).lower()
        if m not in ("off", "server"):
            raise SyncError("token_sync 는 off 또는 server 입니다")
        if m == "server" and not syncrypto.available():
            # 켜는 시점에 막는다 — 등록까지 가서야 알게 되면 코드를 몇 번이나 새로
            # 발급하며 헤맨다(라이브에서 그랬다).
            raise SyncError(syncrypto.INSTALL_HINT)
        server.token_sync = m
    if url is not None:
        u = str(url).strip().rstrip("/")
        if u and not (u.startswith("https://")
                      or u.startswith("http://localhost")
                      or u.startswith("http://127.0.0.1")):
            raise SyncError("동기화 URL 은 https 여야 합니다(개발용 localhost 예외)")
        server.token_sync_url = u
    if sec is not None:
        server.token_sync_sec = max(30, int(sec))
    if accounts is not None:
        server.token_sync_accounts = str(accounts).strip()
    if encrypt is not None:
        server.token_sync_encrypt = bool(encrypt)
    save = getattr(server, "_save_opts", None)
    if save is not None:
        save()
    # 설정이 바뀌면 **옛 실패 사유는 더 이상 사실이 아니다**. 남겨 두면 status 가
    # 고쳐진 뒤에도 옛 오류를 계속 보여 준다(실기동에서 그렇게 오해를 샀다).
    conn = getattr(server, "_tokens_db", None)
    if conn is not None and (mode is not None or url is not None):
        try:
            usagedb.set_sync_remote(conn, SyncClient.REMOTE, last_err="")
        except Exception:       # noqa: BLE001 — 정리 실패가 설정을 막으면 안 된다
            pass
    return {"mode": getattr(server, "token_sync", "off"),
            "url": getattr(server, "token_sync_url", ""),
            "sec": getattr(server, "token_sync_sec", 300),
            "accounts": getattr(server, "token_sync_accounts", ""),
            "encrypt": bool(getattr(server, "token_sync_encrypt", True))}


# ── 워커(비동기 — 블로킹은 전부 executor) ──────────────────────────────────

async def run_worker(server, make_client=None, sleep=None):
    """서버 수명 동안 도는 주기 워커. 설정이 off 면 그냥 잠만 잔다(켜면 다음
    주기에 붙는다 — 재시작 없이 토글되게).

    예외는 **삼키되 사유를 남긴다**(`sync_remote.last_err` + error.log 1회) —
    동기화가 죽어도 pytmux 는 계속 돌아야 하고, 조용히 멈추면 안 된다."""
    import asyncio
    sleep = sleep or asyncio.sleep
    fails = 0
    while getattr(server, "running", True):
        interval = max(30, int(getattr(server, "token_sync_sec", 300) or 300))
        mode = str(getattr(server, "token_sync", "off") or "off")
        if mode != "server" or not getattr(server, "token_sync_url", ""):
            await sleep(interval)
            continue
        try:
            client = (make_client or _client_for)(server)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _sync_once, client)
            fails = 0
        except asyncio.CancelledError:
            raise
        except NotEnrolled:
            pass                # 등록 전에는 조용히 대기(사용자 행동 필요)
        except Exception as e:  # noqa: BLE001
            fails += 1
            _note_error(server, e)
        # 지수 백오프(최대 1시간) — 서버가 죽었을 때 재연결 폭주를 막는다.
        await sleep(min(3600, interval * (2 ** min(fails, 5))))


def _sync_once(client) -> dict:
    """push → pull 한 바퀴(블로킹). executor 안에서만 부른다."""
    up = client.push_limits()
    down = client.pull()
    return {"push": up, "pull": down}


def _client_for(server):
    # tokens_db_path 는 **@property(str)** 다 — 호출하면 'str' object is not callable
    # 로 죽는다(실기동에서 등록이 이 줄에서 실패했다. servermixin 은 같은 주의를
    # 주석으로 달아 두고 있었는데 이 경로만 놓쳤다).
    path = server.tokens_db_path
    # 연결은 넘기지 않는다 — 워커는 executor 스레드에서 도는데 sqlite 연결은 만든
    # 스레드에서만 쓸 수 있다(실기동 오류: "SQLite objects created in a thread…").
    # SyncClient.conn 이 쓰는 스레드마다 자기 연결을 연다. 지연 오픈이라 Claude 활동이
    # 아직 없는 새 머신에서도 등록이 막히지 않는다(등록이 활동보다 먼저인 게 정상).
    db_dir = os.path.dirname(path)
    accounts = [a.strip() for a in
                str(getattr(server, "token_sync_accounts", "") or "").split(",")
                if a.strip()]
    return SyncClient(None, db_dir,
                      http_transport(str(server.token_sync_url)),
                      accounts=accounts,
                      encrypt=bool(getattr(server, "token_sync_encrypt", True)),
                      db_path=path)


def _note_error(server, exc):
    try:
        conn = getattr(server, "_tokens_db", None)
        if conn is not None:
            usagedb.set_sync_remote(conn, SyncClient.REMOTE, last_err=str(exc)[:200])
    except Exception:           # noqa: BLE001 — 기록 실패가 워커를 죽이면 안 된다
        pass
    log = getattr(server, "_log_error", None)
    if log is not None:
        log("token_sync: %s" % exc)


# ── 작은 유틸 ──────────────────────────────────────────────────────────────

def _b64u(raw: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64u_dec(s: str) -> bytes:
    import base64
    pad = "=" * ((-len(s)) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _http_why(status: int, body, what: str) -> str:
    """실패 사유를 **구분해서** 돌려준다. 같은 문구로 뭉뚱그리면 엉뚱한 곳을 고치게 된다."""
    snippet = ""
    try:
        snippet = (body or b"").decode("utf-8", "replace").strip()[:80]
    except Exception:       # noqa: BLE001
        pass
    if status == 401:
        return "%s 거부(401) — 코드 만료·오타이거나 기기가 폐기됐습니다" % what
    if status == 403:
        hint = "앞단 CDN/WAF 차단으로 보입니다" if "1010" in snippet or "cloudflare" in snippet.lower() \
            else "서버가 접근을 거부했습니다"
        return "%s 거부(403) — %s: %s" % (what, hint, snippet)
    if status == 429:
        return "%s 거부(429) — 요청이 너무 잦습니다(잠시 후 재시도)" % what
    if status == 507:
        return "%s 거부(507) — 서버 저장 쿼터 초과" % what
    return "%s 거부(HTTP %d) %s" % (what, status, snippet)


def _int(v, default):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _hostname():
    import socket
    try:
        return socket.gethostname()[:64]
    except OSError:
        return "machine"
