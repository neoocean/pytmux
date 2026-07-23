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
DEFAULT_QUOTA_BYTES = 2 * 1024 ** 3      # vault 당 2GB(S-8)
MAX_DEVICES = 32                        # vault 당 기기 상한(S-7)
MAX_PASSKEYS = 16                       # vault 당 패스키 상한(S-7)
PAIRING_TRIES = 5

_SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS vault (
  vault_id    TEXT PRIMARY KEY,
  created     REAL,
  quota_rows  INTEGER NOT NULL DEFAULT %d,
  rows_used   INTEGER NOT NULL DEFAULT 0,
  -- S-8: 행 수만 세면 64KB 레코드로 이론상 0.33TB 까지 쌓인다. 바이트도 함께 센다.
  quota_bytes INTEGER NOT NULL DEFAULT %d,
  bytes_used  INTEGER NOT NULL DEFAULT 0,
  -- §5.3a: 패스키(PRF)로 감싼 vault 마스터 키. 서버는 **풀 수 없다**(열쇠는 인증기
  -- 안). 이게 있어야 다른 브라우저·다른 머신이 invite 없이 같은 키를 얻는다.
  wrapped_key BLOB,
  wrap_meta   TEXT
);

CREATE TABLE IF NOT EXISTS recovery (
  -- 패스키를 전부 잃었을 때 **스스로** 다시 들어오는 길(§5.9 보강).
  -- 평문은 저장하지 않는다(해시만) — 서버 DB 가 유출돼도 그것만으로는 못 쓴다.
  -- 세션만 얻을 뿐 vault 키는 못 푼다(그건 패스키 PRF/암호구절이 쥔다).
  vault_id TEXT PRIMARY KEY,
  code_h   TEXT NOT NULL,
  created  REAL,
  used     REAL
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
  revoked   REAL,
  -- 머신의 안정 식별자(무작위 uuid). 같은 머신을 다시 등록하면 옛 등록을 대체해
  -- **유령 기기**가 쌓이지 않게 한다(재시도가 잦은 등록 절차에서 실제로 쌓였다).
  host_id   TEXT
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
  tries    INTEGER NOT NULL DEFAULT 0,
  -- §5.3a: 코드에서 파생한 키로 감싼 마스터 키(1회용). 머신이 등록하면서 받아
  -- 자기 코드로 푼다. 코드가 소모되면 이 행도 함께 사라진다.
  key_ct    BLOB,
  key_nonce BLOB
);

CREATE TABLE IF NOT EXISTS session (
  sid      TEXT PRIMARY KEY,
  vault_id TEXT NOT NULL,
  exp      REAL NOT NULL
);
""" % (DEFAULT_QUOTA_ROWS, DEFAULT_QUOTA_BYTES)


# 이미 돌고 있는 서버의 DB 에 컬럼을 더할 때 쓰는 표. `CREATE TABLE IF NOT EXISTS`
# 는 **기존 테이블을 바꾸지 않는다** — 실기동에서 이걸로 물렸다(바이트 쿼터 컬럼을
# 추가한 뒤 업로드가 "no such column: quota_bytes" 로 500). 새 컬럼은 반드시 여기에
# 함께 등록한다. (클라 usagedb 가 v5~v9 에서 쓰는 것과 같은 규율.)
_ADD_COLUMNS = (
    ("vault", "quota_bytes", "INTEGER NOT NULL DEFAULT %d" % DEFAULT_QUOTA_BYTES),
    ("vault", "bytes_used", "INTEGER NOT NULL DEFAULT 0"),
    ("vault", "wrapped_key", "BLOB"),
    ("vault", "wrap_meta", "TEXT"),
    ("pairing", "key_ct", "BLOB"),
    ("pairing", "key_nonce", "BLOB"),
    ("device", "host_id", "TEXT"),
)


def _migrate(conn) -> int:
    """기존 DB 에 빠진 컬럼을 더한다(멱등). 더한 컬럼 수 반환.

    ALTER ADD COLUMN 은 메타데이터만 바꾸므로 행 재기록이 없고, 기존 값은 기본값으로
    채워진다(bytes_used=0 은 과소계상이지만 단조 증가라 곧 정확해진다 — 데이터를
    다시 스캔해 정확히 채우는 것보다 재시작을 가볍게 두는 쪽을 택했다)."""
    n = 0
    for table, col, decl in _ADD_COLUMNS:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        if col not in cols:
            conn.execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl))
            n += 1
    return n


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
    _migrate(conn)
    conn.commit()
    if path != ":memory:":
        for suf in ("", "-wal", "-shm"):
            try:
                os.chmod(path + suf, 0o600)
            except OSError:
                pass
    return conn


# ── vault / passkey ────────────────────────────────────────────────────────

def vault_count(conn) -> int:
    """등록된 vault 수(S-1 부트스트랩 잠금 판정용)."""
    return int(conn.execute("SELECT COUNT(*) AS n FROM vault").fetchone()["n"])


def create_vault(conn, now: float) -> str:
    """새 vault. id 는 랜덤 — **사람 이름이 될 만한 것은 아무것도 받지 않는다**
    (설계 §5.2: 서버에 이름 필드 자체를 두지 않는다)."""
    vid = secrets.token_hex(VAULT_ID_LEN)
    conn.execute("INSERT INTO vault (vault_id, created) VALUES (?,?)", (vid, now))
    conn.commit()
    return vid


def new_recovery_code(conn, vault_id: str, now: float) -> str:
    """복구 코드를 발급(재발급)한다. 평문은 **이때 한 번만** 반환된다."""
    raw = secrets.token_bytes(16)                    # 128비트
    code = "-".join(raw.hex().upper()[i:i + 4] for i in range(0, 32, 4))
    conn.execute("INSERT OR REPLACE INTO recovery (vault_id, code_h, created, used)"
                 " VALUES (?,?,?,NULL)", (vault_id, _code_hash(code), now))
    conn.commit()
    return code


def use_recovery_code(conn, code: str, now: float):
    """맞으면 vault_id 를 돌려주고 **그 코드를 소모**한다(1회용). 아니면 None."""
    r = conn.execute("SELECT vault_id FROM recovery WHERE code_h=? AND used IS NULL",
                     (_code_hash(code),)).fetchone()
    if r is None:
        return None
    conn.execute("UPDATE recovery SET used=? WHERE vault_id=?", (now, r["vault_id"]))
    conn.commit()
    return r["vault_id"]


def set_vault_key(conn, vault_id: str, wrapped: bytes, meta: str,
                 overwrite: bool = False) -> bool:
    """패스키로 감싼 마스터 키를 보관한다(§5.3a). 이미 있으면 **덮어쓰지 않는다** —
    키를 갈아치우면 서버에 쌓인 기존 레코드가 전부 복호 불능이 되기 때문이다.
    일부러 바꾸려면 overwrite=True(회전 절차, §5.9)."""
    cur = conn.execute("SELECT wrapped_key FROM vault WHERE vault_id=?",
                       (vault_id,)).fetchone()
    if cur is None:
        raise KeyError(vault_id)
    if cur["wrapped_key"] is not None and not overwrite:
        return False
    conn.execute("UPDATE vault SET wrapped_key=?, wrap_meta=? WHERE vault_id=?",
                 (bytes(wrapped), str(meta or ""), vault_id))
    conn.commit()
    return True


def get_vault_key(conn, vault_id: str):
    r = conn.execute("SELECT wrapped_key, wrap_meta FROM vault WHERE vault_id=?",
                     (vault_id,)).fetchone()
    if r is None or r["wrapped_key"] is None:
        return None
    return {"wrapped": bytes(r["wrapped_key"]), "meta": r["wrap_meta"] or ""}


def add_passkey(conn, vault_id: str, cred_id: bytes, pubkey: bytes,
                sign_count: int, aaguid=None, label=None, now: float = 0.0):
    n = conn.execute("SELECT COUNT(*) AS n FROM passkey WHERE vault_id=?",
                     (vault_id,)).fetchone()["n"]
    if n >= MAX_PASSKEYS:
        raise LimitExceeded("패스키 상한(%d) 초과" % MAX_PASSKEYS)
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

class LimitExceeded(Exception):
    """vault 당 기기/패스키 개수 상한 초과(S-7)."""


def add_device(conn, vault_id: str, pubkey: bytes, label=None,
               now: float = 0.0, host_id=None) -> str:
    # S-7: 상한이 없으면 세션 하나로 기기를 무한히 등록할 수 있다(실측 30개 성공).
    # 폐기된 기기는 세지 않는다 — 정리하면 다시 등록할 수 있어야 하므로.
    if host_id:
        # 같은 머신의 옛 등록은 폐기한다 — 재등록이 기기 목록을 늘리면 사용자가
        # 어느 것이 살아 있는지 알 수 없고, 상한(MAX_DEVICES)만 잡아먹는다.
        conn.execute("DELETE FROM device WHERE vault_id=? AND host_id=?",
                     (vault_id, str(host_id)))
        conn.commit()
    n = conn.execute("SELECT COUNT(*) AS n FROM device WHERE vault_id=?"
                     " AND revoked IS NULL", (vault_id,)).fetchone()["n"]
    if n >= MAX_DEVICES:
        raise LimitExceeded("기기 상한(%d) 초과 — 쓰지 않는 기기를 폐기하세요"
                            % MAX_DEVICES)
    did = secrets.token_hex(DEVICE_ID_LEN)
    conn.execute(
        "INSERT INTO device (device_id, vault_id, pubkey, label, created, host_id)"
        " VALUES (?,?,?,?,?,?)",
        (did, vault_id, pubkey, label, now, str(host_id) if host_id else None))
    conn.commit()
    return did


def get_device(conn, device_id: str):
    """**폐기된 기기는 없는 것과 같다** — 호출자가 revoked 를 확인하는 것을 잊어도
    인증이 통과하지 않도록 여기서 걸러 낸다(빠뜨리기 쉬운 검사는 저장소가 갖는다)."""
    r = conn.execute("SELECT * FROM device WHERE device_id=? AND revoked IS NULL",
                     (device_id,)).fetchone()
    return dict(r) if r else None


def revoke_device(conn, vault_id: str, device_id: str, now: float) -> bool:
    """**자기 vault 의 기기만** 폐기할 수 있다(vault_id 를 조건에 넣는 이유).

    폐기 = **행 삭제**다. 표시로만 남기면 목록에 '폐기됨' 이 쌓여 무엇이 살아 있는지
    읽기 어려워진다(제보). 인증은 device_id 조회로 하므로 지운 기기의 요청은 그대로
    401 이고, 남겨 둘 실익이 없다."""
    cur = conn.execute("DELETE FROM device WHERE device_id=? AND vault_id=?",
                       (device_id, vault_id))
    conn.commit()
    return cur.rowcount > 0


def list_devices(conn, vault_id: str) -> list:
    """살아 있는 기기만. 폐기는 삭제이므로 목록에 잔해가 남지 않는다."""
    return [dict(r) for r in conn.execute(
        "SELECT device_id, label, created, last_seen FROM device"
        " WHERE vault_id=? AND revoked IS NULL ORDER BY created", (vault_id,))]


def touch_device(conn, device_id: str, now: float) -> None:
    conn.execute("UPDATE device SET last_seen=? WHERE device_id=?",
                 (now, device_id))
    conn.commit()


# ── 페어링 코드(사람이 옮기는 1회용 값) ─────────────────────────────────────

def new_pairing(conn, vault_id: str, now: float, ttl: float = 600.0,
                key_ct=None, key_nonce=None) -> str:
    """1회용 코드를 발급한다. **평문은 저장하지 않는다**(해시만) — 서버 DB 가 유출돼도
    미사용 코드로 기기를 붙일 수 없게.

    §5.3a: 코드는 **키 전달 통로**이기도 하다. 브라우저가 코드에서 파생한 키로 감싼
    마스터 키(key_ct/key_nonce)를 함께 올리면 등록하는 머신이 그것을 받아 자기 코드로
    푼다. 그래서 길이가 **128비트**여야 한다 — 서버에 감싼 블롭이 있으니 짧은 코드는
    오프라인 대입이 가능하다(40비트였던 초판은 이 용도에 부적합)."""
    raw = secrets.token_bytes(16)                 # 128비트
    code = "-".join(raw.hex().upper()[i:i + 4] for i in range(0, 32, 4))
    conn.execute("INSERT INTO pairing (code_h, vault_id, exp, key_ct, key_nonce)"
                 " VALUES (?,?,?,?,?)",
                 (_code_hash(code), vault_id, now + ttl, key_ct, key_nonce))
    conn.commit()
    return code


def consume_pairing_h(conn, code_h: str, now: float):
    """**해시로** 코드를 소비한다(§5.3a). 머신이 원문을 안 보내므로 서버는 코드를
    모르고, 따라서 함께 보관한 감싼 키도 풀 수 없다."""
    r = conn.execute("SELECT * FROM pairing WHERE code_h=?",
                     (str(code_h or ""),)).fetchone()
    if r is None:
        return None
    conn.execute("DELETE FROM pairing WHERE code_h=?", (r["code_h"],))
    conn.commit()
    if r["exp"] <= now or r["tries"] >= PAIRING_TRIES:
        return None
    return {"vault_id": r["vault_id"], "key_ct": r["key_ct"],
            "key_nonce": r["key_nonce"]}


def put_pairing(conn, vault_id: str, code_h: str, now: float, ttl: float = 600.0,
                key_ct=None, key_nonce=None) -> None:
    """브라우저가 **직접 만든** 코드의 해시를 등록한다. 서버는 원문을 본 적이 없다."""
    conn.execute("INSERT OR REPLACE INTO pairing (code_h, vault_id, exp, tries,"
                 " key_ct, key_nonce) VALUES (?,?,?,0,?,?)",
                 (str(code_h), vault_id, now + ttl, key_ct, key_nonce))
    conn.commit()


def consume_pairing(conn, code: str, now: float):
    """성공하면 vault_id 를 돌려주고 **코드를 즉시 소모**한다. 만료·시도초과·불일치는
    None.

    S-2(검수): 예전에는 없는 코드를 받으면 **살아 있는 모든 코드**의 tries 를 올리고
    5회에서 삭제했다 — 공격자가 오답 5번으로 사용자의 유효 코드를 전부 날릴 수 있었다
    (실측 3개→0개). 이제 카운터는 **그 코드에만** 적용하고, 무차별 대입은 앱 계층의
    미인증 rate limit 으로 막는다(S-1)."""
    h = _code_hash(code)
    r = conn.execute("SELECT * FROM pairing WHERE code_h=?", (h,)).fetchone()
    if r is None:
        return None                 # 없는 코드는 아무 상태도 건드리지 않는다
    if r["exp"] <= now or r["tries"] >= PAIRING_TRIES:
        conn.execute("DELETE FROM pairing WHERE code_h=?", (h,))
        conn.commit()
        return None
    conn.execute("DELETE FROM pairing WHERE code_h=?", (h,))
    conn.commit()
    # 코드는 소모되고, 감싼 키는 **그 한 번만** 흘러간다.
    return {"vault_id": r["vault_id"], "key_ct": r["key_ct"],
            "key_nonce": r["key_nonce"]}


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


def session_vault(conn, sid: str, now: float, renew: float = 0.0):
    """세션 소유 vault. renew>0 이면 **활동이 있을 때마다 만료를 미룬다**(슬라이딩) —
    쓰는 동안에는 로그인이 유지되고, 명시적 로그아웃·장기 미사용에만 끊긴다."""
    r = conn.execute("SELECT vault_id, exp FROM session WHERE sid=? AND exp>?",
                     (sid or "", now)).fetchone()
    if r is None:
        return None
    if renew and r["exp"] - now < renew * 0.5:
        # 매 요청마다 쓰지 않는다(쓰기 폭주 방지) — 남은 수명이 절반 아래일 때만.
        conn.execute("UPDATE session SET exp=? WHERE sid=?", (now + renew, sid))
        conn.commit()
    return r["vault_id"]


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
    """레코드 배치를 **원자적으로** 멱등 업서트한다. {accepted, ignored, seq_max}.

    records = [{"kind","rkey","acct_id","ct"(bytes),"nonce"(bytes)}, …].
    같은 (vault, kind, rkey)는 **먼저 온 것이 이긴다**(INSERT OR IGNORE) — 뒤에 온
    같은 키로 내용을 덮어쓸 수 있으면 서버에 쓰기 권한이 있는 자가 과거를 고쳐 쓸 수
    있게 된다.

    S-4(검수): 예전에는 루프 중간에 QuotaExceeded 를 올려 **이미 넣은 행은 남고
    rows_used 는 안 오르고 트랜잭션은 열린 채로** 다음 요청에 넘어갔다(실측:
    3행 저장·rows_used=0·in_transaction=True → 쿼터가 영영 안 걸림). 이제
    ① 배치 크기로 **삽입 전에** 판정하고 ② 전부-또는-전무로 커밋하며 ③ 어떤 실패든
    rollback 한다."""
    v = conn.execute(
        "SELECT quota_rows, rows_used, quota_bytes, bytes_used FROM vault"
        " WHERE vault_id=?", (vault_id,)).fetchone()
    if v is None:
        raise KeyError(vault_id)
    recs = list(records)
    size = sum(len(bytes(r["ct"])) + len(bytes(r["nonce"])) for r in recs)
    # 최악(전부 신규)을 가정해 **미리** 판정한다 — 부분 반영을 원천 차단.
    if v["rows_used"] + len(recs) > v["quota_rows"]:
        raise QuotaExceeded("vault 행 쿼터 초과")
    if v["bytes_used"] + size > v["quota_bytes"]:
        raise QuotaExceeded("vault 바이트 쿼터 초과")
    accepted = ignored = 0
    added_bytes = 0
    try:
        for rec in recs:
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO event (vault_id, kind, acct_id, rkey, ct,"
                " nonce, recv) VALUES (?,?,?,?,?,?,?)",
                (vault_id, str(rec["kind"]), rec.get("acct_id"), str(rec["rkey"]),
                 bytes(rec["ct"]), bytes(rec["nonce"]), now))
            if conn.total_changes > before:
                accepted += 1
                added_bytes += len(bytes(rec["ct"])) + len(bytes(rec["nonce"]))
            else:
                ignored += 1
        if accepted:
            conn.execute(
                "UPDATE vault SET rows_used=rows_used+?, bytes_used=bytes_used+?"
                " WHERE vault_id=?", (accepted, added_bytes, vault_id))
        conn.commit()
    except Exception:
        conn.rollback()             # 열린 트랜잭션을 다음 요청에 넘기지 않는다
        raise
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
