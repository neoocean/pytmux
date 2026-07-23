"""동기화 서버 저장소 — SQLite 한 파일.

설계: docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md §5.4.

서버는 **레코드 내용을 이해하지 못한다**. 하는 일은 인가·쿼터 검사 → `INSERT OR
IGNORE` → `seq` 부여 → 커서 기반 재생뿐이다. 그래서 여기 어디에도 계정 이메일·토큰
수치를 다루는 코드가 없다(있으면 설계가 깨진 것).

시각은 전부 **호출자가 넣는다**(`now` 인자) — 서버 내부에서 wall clock 을 읽으면
테스트가 시간에 흔들리고, 시계 되돌림에 취약해진다. `seq`(AUTOINCREMENT)만이 커서다.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3

VAULT_ID_LEN = 16
DEVICE_ID_LEN = 16
DEFAULT_QUOTA_ROWS = 5_000_000
PAIRING_TRIES = 5

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS vault (
  vault_id   TEXT PRIMARY KEY,
  created    REAL,
  quota_rows INTEGER NOT NULL DEFAULT %d,
  rows_used  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS passkey (
  cred_id    TEXT PRIMARY KEY,
  vault_id   TEXT NOT NULL,
  pubkey     BLOB NOT NULL,
  sign_count INTEGER NOT NULL DEFAULT 0,
  aaguid     BLOB,
  label      TEXT,
  created    REAL,
  last_used  REAL
);
CREATE INDEX IF NOT EXISTS ix_passkey_vault ON passkey(vault_id);

CREATE TABLE IF NOT EXISTS device (
  device_id TEXT PRIMARY KEY,
  vault_id  TEXT NOT NULL,
  pubkey    BLOB NOT NULL,
  label     TEXT,
  created   REAL,
  last_seen REAL,
  revoked   REAL
);
CREATE INDEX IF NOT EXISTS ix_device_vault ON device(vault_id);

CREATE TABLE IF NOT EXISTS event (
  seq      INTEGER PRIMARY KEY AUTOINCREMENT,
  vault_id TEXT NOT NULL,
  kind     TEXT NOT NULL,
  acct_id  TEXT,
  rkey     TEXT NOT NULL,
  ct       BLOB NOT NULL,
  nonce    BLOB NOT NULL,
  recv     REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_event ON event(vault_id, kind, rkey);
CREATE INDEX IF NOT EXISTS ix_event_pull ON event(vault_id, seq);

CREATE TABLE IF NOT EXISTS nonce_seen (
  device_id TEXT NOT NULL,
  nonce     TEXT NOT NULL,
  exp       REAL NOT NULL,
  PRIMARY KEY (device_id, nonce)
);

CREATE TABLE IF NOT EXISTS pairing (
  code_h   TEXT PRIMARY KEY,
  vault_id TEXT NOT NULL,
  exp      REAL NOT NULL,
  tries    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS session (
  sid      TEXT PRIMARY KEY,
  vault_id TEXT NOT NULL,
  exp      REAL NOT NULL
);
""" % DEFAULT_QUOTA_ROWS


class QuotaExceeded(Exception):
    """vault 행 쿼터 초과. 조용히 버리지 않고 호출자가 사유를 돌려준다."""


def connect(path: str) -> sqlite3.Connection:
    """DB 를 열고 스키마·권한을 보장한다. 파일이면 0600(사이드카 포함)."""
    if path != ":memory:":
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
    # check_same_thread=False: HTTP 서버가 요청마다 다른 스레드를 쓴다. 기본값이면
    # DB 를 건드리는 **모든 엔드포인트가** ProgrammingError 로 죽는다(실측 — 브라우저
    # 로그인이 500 "internal"). 대신 호출자(SyncApp)가 락으로 직렬화한다.
    conn = sqlite3.connect(path, timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=3000")
    conn.executescript(_SCHEMA)
    conn.commit()
    if path != ":memory:":
        for suf in ("", "-wal", "-shm"):
            try:
                os.chmod(path + suf, 0o600)
            except OSError:
                pass
    return conn


# ── vault / passkey ────────────────────────────────────────────────────────

def create_vault(conn, now: float) -> str:
    """새 vault. id 는 랜덤 — **사람 이름이 될 만한 것은 아무것도 받지 않는다**
    (설계 §5.2: 서버에 이름 필드 자체를 두지 않는다)."""
    vid = secrets.token_hex(VAULT_ID_LEN)
    conn.execute("INSERT INTO vault (vault_id, created) VALUES (?,?)", (vid, now))
    conn.commit()
    return vid


def add_passkey(conn, vault_id: str, cred_id: bytes, pubkey: bytes,
                sign_count: int, aaguid=None, label=None, now: float = 0.0):
    conn.execute(
        "INSERT INTO passkey (cred_id, vault_id, pubkey, sign_count, aaguid,"
        " label, created) VALUES (?,?,?,?,?,?,?)",
        (_hex(cred_id), vault_id, pubkey, int(sign_count), aaguid, label, now))
    conn.commit()


def get_passkey(conn, cred_id: bytes):
    r = conn.execute("SELECT * FROM passkey WHERE cred_id=?",
                     (_hex(cred_id),)).fetchone()
    return dict(r) if r else None


def touch_passkey(conn, cred_id: bytes, sign_count: int, now: float) -> None:
    conn.execute("UPDATE passkey SET sign_count=?, last_used=? WHERE cred_id=?",
                 (int(sign_count), now, _hex(cred_id)))
    conn.commit()


def list_passkeys(conn, vault_id: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT cred_id, label, created, last_used FROM passkey WHERE vault_id=?"
        " ORDER BY created", (vault_id,))]


# ── device(기계 인증) ──────────────────────────────────────────────────────

def add_device(conn, vault_id: str, pubkey: bytes, label=None,
               now: float = 0.0) -> str:
    did = secrets.token_hex(DEVICE_ID_LEN)
    conn.execute(
        "INSERT INTO device (device_id, vault_id, pubkey, label, created)"
        " VALUES (?,?,?,?,?)", (did, vault_id, pubkey, label, now))
    conn.commit()
    return did


def get_device(conn, device_id: str):
    """**폐기된 기기는 없는 것과 같다** — 호출자가 revoked 를 확인하는 것을 잊어도
    인증이 통과하지 않도록 여기서 걸러 낸다(빠뜨리기 쉬운 검사는 저장소가 갖는다)."""
    r = conn.execute("SELECT * FROM device WHERE device_id=? AND revoked IS NULL",
                     (device_id,)).fetchone()
    return dict(r) if r else None


def revoke_device(conn, vault_id: str, device_id: str, now: float) -> bool:
    """**자기 vault 의 기기만** 폐기할 수 있다(vault_id 를 조건에 넣는 이유)."""
    cur = conn.execute(
        "UPDATE device SET revoked=? WHERE device_id=? AND vault_id=?"
        " AND revoked IS NULL", (now, device_id, vault_id))
    conn.commit()
    return cur.rowcount > 0


def list_devices(conn, vault_id: str) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT device_id, label, created, last_seen, revoked FROM device"
        " WHERE vault_id=? ORDER BY created", (vault_id,))]


def touch_device(conn, device_id: str, now: float) -> None:
    conn.execute("UPDATE device SET last_seen=? WHERE device_id=?",
                 (now, device_id))
    conn.commit()


# ── 페어링 코드(사람이 옮기는 1회용 값) ─────────────────────────────────────

def new_pairing(conn, vault_id: str, now: float, ttl: float = 600.0) -> str:
    """1회용 코드를 발급한다. **평문은 저장하지 않는다**(해시만) — 서버 DB 가 유출돼도
    미사용 코드로 기기를 붙일 수 없게."""
    code = "-".join(secrets.token_hex(2).upper() for _ in range(2))
    conn.execute("INSERT INTO pairing (code_h, vault_id, exp) VALUES (?,?,?)",
                 (_code_hash(code), vault_id, now + ttl))
    conn.commit()
    return code


def consume_pairing(conn, code: str, now: float):
    """성공하면 vault_id 를 돌려주고 **코드를 즉시 소모**한다. 만료·시도초과·불일치는
    None. 시도 횟수를 세는 이유는 짧은 코드를 무차별 대입하지 못하게."""
    h = _code_hash(code)
    r = conn.execute("SELECT * FROM pairing WHERE code_h=?", (h,)).fetchone()
    if r is None:
        # 존재하지 않는 코드도 **시도 횟수만큼은** 비용이 들게 — 살아 있는 코드들의
        # tries 를 올려 무차별 대입이 전체적으로 소진되게 한다.
        conn.execute("UPDATE pairing SET tries=tries+1 WHERE exp>?", (now,))
        conn.execute("DELETE FROM pairing WHERE tries>=?", (PAIRING_TRIES,))
        conn.commit()
        return None
    if r["exp"] <= now or r["tries"] >= PAIRING_TRIES:
        conn.execute("DELETE FROM pairing WHERE code_h=?", (h,))
        conn.commit()
        return None
    conn.execute("DELETE FROM pairing WHERE code_h=?", (h,))
    conn.commit()
    return r["vault_id"]


def purge_expired(conn, now: float) -> int:
    """만료된 페어링·nonce·세션 정리. 주기 호출(없어도 정확성은 유지되나 커진다)."""
    n = 0
    for sql in ("DELETE FROM pairing WHERE exp<=?",
                "DELETE FROM nonce_seen WHERE exp<=?",
                "DELETE FROM session WHERE exp<=?"):
        n += conn.execute(sql, (now,)).rowcount
    conn.commit()
    return n


# ── 브라우저 세션(패스키 로그인 결과) ───────────────────────────────────────

def new_session(conn, vault_id: str, now: float, ttl: float = 900.0) -> str:
    sid = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO session (sid, vault_id, exp) VALUES (?,?,?)",
                 (sid, vault_id, now + ttl))
    conn.commit()
    return sid


def session_vault(conn, sid: str, now: float):
    r = conn.execute("SELECT vault_id FROM session WHERE sid=? AND exp>?",
                     (sid or "", now)).fetchone()
    return r["vault_id"] if r else None


def drop_session(conn, sid: str) -> None:
    conn.execute("DELETE FROM session WHERE sid=?", (sid,))
    conn.commit()


# ── nonce(재생 방지) ───────────────────────────────────────────────────────

def use_nonce(conn, device_id: str, nonce: str, now: float,
              window: float = 60.0) -> bool:
    """처음 보는 nonce 면 기록하고 True. 재사용이면 False — 서명을 그대로 복사한
    재생 공격이 여기서 막힌다."""
    try:
        conn.execute("INSERT INTO nonce_seen (device_id, nonce, exp)"
                     " VALUES (?,?,?)", (device_id, nonce, now + window * 2))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


# ── 이벤트(동기화 본체) ────────────────────────────────────────────────────

def put_events(conn, vault_id: str, records, now: float) -> dict:
    """레코드 배치를 멱등 업서트한다. {accepted, ignored, seq_max}.

    records = [{"kind","rkey","acct_id","ct"(bytes),"nonce"(bytes)}, …].
    같은 (vault, kind, rkey)는 **먼저 온 것이 이긴다**(INSERT OR IGNORE) — 뒤에 온
    같은 키로 내용을 덮어쓸 수 있으면 서버에 쓰기 권한이 있는 자가 과거를 고쳐 쓸 수
    있게 된다. 쿼터 초과는 QuotaExceeded(조용한 절단 금지)."""
    v = conn.execute("SELECT quota_rows, rows_used FROM vault WHERE vault_id=?",
                     (vault_id,)).fetchone()
    if v is None:
        raise KeyError(vault_id)
    accepted = ignored = 0
    for rec in records:
        if v["rows_used"] + accepted >= v["quota_rows"]:
            raise QuotaExceeded("vault 행 쿼터 초과")
        before = conn.total_changes
        conn.execute(
            "INSERT OR IGNORE INTO event (vault_id, kind, acct_id, rkey, ct,"
            " nonce, recv) VALUES (?,?,?,?,?,?,?)",
            (vault_id, str(rec["kind"]), rec.get("acct_id"), str(rec["rkey"]),
             bytes(rec["ct"]), bytes(rec["nonce"]), now))
        if conn.total_changes > before:
            accepted += 1
        else:
            ignored += 1
    if accepted:
        conn.execute("UPDATE vault SET rows_used=rows_used+? WHERE vault_id=?",
                     (accepted, vault_id))
    conn.commit()
    return {"accepted": accepted, "ignored": ignored,
            "seq_max": max_seq(conn, vault_id)}


def get_events(conn, vault_id: str, since_seq: int = 0, limit: int = 1000) -> list:
    """**자기 vault 것만** seq 순서로. vault_id 는 호출자가 요청 파라미터가 아니라
    서명에서 유도해 넘겨야 한다(§5.5 — 파라미터로 받으면 그게 IDOR 자리다)."""
    limit = max(1, min(int(limit), 5000))
    return [dict(r) for r in conn.execute(
        "SELECT seq, kind, acct_id, rkey, ct, nonce FROM event"
        " WHERE vault_id=? AND seq>? ORDER BY seq LIMIT ?",
        (vault_id, int(since_seq), limit))]


def max_seq(conn, vault_id: str) -> int:
    r = conn.execute("SELECT COALESCE(MAX(seq),0) AS m FROM event WHERE vault_id=?",
                     (vault_id,)).fetchone()
    return int(r["m"])


def purge_events(conn, vault_id: str, before_seq: int) -> int:
    """보존정책 집행(§9.4). 로컬 DB 가 원본이므로 서버에서 지워도 회계 손실이 없다."""
    cur = conn.execute("DELETE FROM event WHERE vault_id=? AND seq<=?",
                       (vault_id, int(before_seq)))
    conn.commit()
    return cur.rowcount


def _hex(b) -> str:
    return b.hex() if isinstance(b, (bytes, bytearray)) else str(b)


def _code_hash(code: str) -> str:
    norm = "".join(ch for ch in str(code).upper() if ch.isalnum())
    return hashlib.sha256(("pairing|" + norm).encode()).hexdigest()
