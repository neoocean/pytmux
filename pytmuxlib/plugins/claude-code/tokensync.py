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
import time

from . import syncrypto, usagedb

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

    opener = urllib.request.build_opener(_NoRedirect)

    def send(method, path, query="", body=b"", headers=None):
        url = base_url.rstrip("/") + path + (("?" + query) if query else "")
        req = urllib.request.Request(url, data=(body or None), method=method)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()
        except OSError as e:
            raise SyncError("서버에 닿지 못했습니다: %s" % e) from e

    return send


# ── 클라이언트 ─────────────────────────────────────────────────────────────

class SyncClient:
    """한 머신의 동기화 상태(키·기기 id·커서)와 push/pull 을 담는다.

    **블로킹 클래스**다 — 호출자가 executor 로 돌린다. 여기서 asyncio 를 몰라야
    테스트가 단순해진다."""

    REMOTE = "server"       # sync_remote 의 키(T5 는 원격이 서버 하나)

    def __init__(self, conn, db_dir: str, transport, *, accounts=(),
                 encrypt: bool = True, now=time.time):
        self.conn = conn
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
        from cryptography.hazmat.primitives import serialization as ser
        from cryptography.hazmat.primitives.asymmetric import ed25519
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
        body = json.dumps({"pairing_code": str(pairing_code),
                           "pubkey": _b64u(pub),
                           "label": label or _hostname()}).encode()
        status, _, resp = self.transport(
            "POST", "/v1/devices", "", body,
            {"Content-Type": "application/json"})
        if status != 200:
            raise SyncError("등록 거부(코드 만료·오타 가능)")
        try:
            did = json.loads(resp)["device_id"]
        except (ValueError, KeyError) as e:
            raise SyncError("서버 응답 형식 오류") from e
        path = os.path.join(self.db_dir, "sync_device.id")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, did.encode("ascii"))
        finally:
            os.close(fd)
        return did

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
            raise SyncError("업로드 거부(HTTP %d)" % status)
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
            raise SyncError("내려받기 거부(HTTP %d)" % status)
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
    conn = getattr(server, "_tokens_db", None)
    if conn is None:
        raise SyncError("토큰 DB 가 열려 있지 않습니다")
    db_dir = os.path.dirname(server.tokens_db_path())
    accounts = [a.strip() for a in
                str(getattr(server, "token_sync_accounts", "") or "").split(",")
                if a.strip()]
    return SyncClient(conn, db_dir,
                      http_transport(str(server.token_sync_url)),
                      accounts=accounts,
                      encrypt=bool(getattr(server, "token_sync_encrypt", True)))


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
