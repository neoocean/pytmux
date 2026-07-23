"""여러 머신 간 토큰 동기화 P0 — 스키마 v9 + 암호 원시함수.

설계: docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md (§5.3 비식별화,
§6 스키마 v9). P0 은 저장소와 순수 함수만 다룬다 — 서버·워커는 P1/P2.

여기 단언들은 "되돌리면 실패해야 하는" 오라클을 의도한다:
  · tzoff 를 안 싣게 되돌리면 → test_xc_row_carries_origin_tzoff 실패
  · 원산지 tzoff 를 로컬 값으로 덮게 되돌리면 → …_preserves_remote_tzoff 실패
  · limits 멱등키를 빼면 → test_limits_lkey_blocks_duplicate 실패
  · normalize 에 신뢰계정 규칙을 끼우면 → test_acct_id_stable_across_machines 실패
"""
import time

import harness  # noqa: F401  (경로 설정 + 플러그인 별칭 등록)
from pytmuxlib import usagedb, usagelog
from pytmuxlib import syncrypto


def _rec(xkey, ts="2026-07-22T10:00:00.000Z", **kw):
    r = {"xkey": xkey, "ts": ts, "session_uuid": "s1", "model": "opus-4.8",
         "input": 10, "output": 5, "cache_create": 0, "cache_read": 0,
         "is_sidechain": 0}
    r.update(kw)
    return r


# ── 스키마 v9 ──────────────────────────────────────────────────────────────

async def test_v9_schema_on_fresh_db():
    conn = usagedb.connect(":memory:")
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) >= 9
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"sync_remote", "sync_export", "account_alias"} <= have
    xc = {r["name"] for r in conn.execute("PRAGMA table_info(usage_xc)")}
    assert {"tzoff", "host"} <= xc
    lim = {r["name"] for r in conn.execute("PRAGMA table_info(limits)")}
    assert {"host", "lkey"} <= lim
    conn.close()


async def test_v8_db_upgrades_in_place_without_touching_rows():
    """v8 DB(기존 사용자)가 첫 connect 에서 v9 로 승급하고, 기존 행은 그대로다."""
    import os
    import sqlite3
    import tempfile
    d = tempfile.mkdtemp(prefix="pytmux-sync-")
    path = os.path.join(d, "t.db")
    conn = usagedb.connect(path)
    usagedb.insert_xc_many(conn, [_rec("m1:r1"), _rec("m2:r2")])
    usagedb.insert_limits(conn, {"ts": 1000.0, "account": "a@b.c",
                                 "session_pct": 10, "source": "probe"})
    before = usagedb.xc_totals(conn)
    # v8 로 되돌린 뒤(컬럼은 남지만 버전만 낮춤) 다시 열어 마이그레이션을 태운다.
    conn.execute("PRAGMA user_version=8")
    conn.commit()
    conn.close()
    conn = usagedb.connect(path)
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 9
    assert usagedb.xc_count(conn) == 2
    assert usagedb.xc_totals(conn) == before
    conn.close()
    # 실제 v8 스키마(새 컬럼 없음)에서 올라오는 경로도 확인한다.
    path2 = os.path.join(d, "old.db")
    old = sqlite3.connect(path2)
    old.executescript(
        "CREATE TABLE usage_xc (xkey TEXT PRIMARY KEY, ts REAL NOT NULL,"
        " session_uuid TEXT, tab INTEGER, pane INTEGER, pytmux_session INTEGER,"
        " model TEXT, account TEXT, input INTEGER NOT NULL DEFAULT 0,"
        " output INTEGER NOT NULL DEFAULT 0, cache_create INTEGER NOT NULL"
        " DEFAULT 0, cache_read INTEGER NOT NULL DEFAULT 0,"
        " is_sidechain INTEGER NOT NULL DEFAULT 0);"
        "CREATE TABLE limits (ts REAL NOT NULL, account TEXT, session_pct"
        " INTEGER, session_reset TEXT, week_all_pct INTEGER, week_all_reset"
        " TEXT, week_sonnet_pct INTEGER, week_sonnet_reset TEXT,"
        " source TEXT NOT NULL);"
        "INSERT INTO usage_xc (xkey, ts, input) VALUES ('old:1', 1.0, 7);"
        "INSERT INTO limits (ts, source) VALUES (1.0, 'probe');"
        "PRAGMA user_version=8;")
    old.commit()
    old.close()
    conn = usagedb.connect(path2)
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 9
    assert usagedb.xc_count(conn) == 1
    assert usagedb.xc_totals(conn)["input"] == 7      # 기존 행 무접촉
    xc = {r["name"] for r in conn.execute("PRAGMA table_info(usage_xc)")}
    assert {"tzoff", "host"} <= xc
    row = conn.execute("SELECT tzoff, host FROM usage_xc").fetchone()
    assert row["tzoff"] is None and row["host"] is None   # 레거시=미상 폴백
    conn.close()


async def test_migration_is_idempotent():
    conn = usagedb.connect(":memory:")
    assert usagedb._migrate_v9_sync(conn) == 0        # 이미 v9 스키마
    conn.close()


# ── 원산지 tzoff/host ──────────────────────────────────────────────────────

async def test_xc_row_carries_origin_tzoff():
    """새 행은 **그 레코드 ts 시점**의 로컬 오프셋을 싣는다(적재 시각 아님)."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc(conn, _rec("m1:r1"))
    row = conn.execute("SELECT tzoff, host FROM usage_xc").fetchone()
    expect = usagelog._tzoff_at(usagedb._iso_to_epoch("2026-07-22T10:00:00.000Z"))
    assert row["tzoff"] == expect
    assert row["host"] is None            # 로컬 적재분 = 자기 것
    conn.close()


async def test_insert_xc_preserves_remote_tzoff_and_host():
    """동기화로 들어온 행은 **원산지 값**을 보존해야 한다 — 여기서 로컬 값으로
    덮으면 머신마다 일자 합계가 갈리는 §1.2 문제가 되살아난다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc(conn, _rec("r:1", tzoff=-25200, host="hostB"))
    row = conn.execute("SELECT tzoff, host FROM usage_xc").fetchone()
    assert row["tzoff"] == -25200 and row["host"] == "hostB"
    conn.close()


# ── limits 멱등 ────────────────────────────────────────────────────────────

async def test_limits_lkey_blocks_duplicate():
    """같은 관측이 동기화로 되돌아와도 한 행이다(부분 UNIQUE 인덱스)."""
    conn = usagedb.connect(":memory:")
    snap = {"ts": 1000.0, "account": "a@b.c", "session_pct": 11,
            "source": "probe"}
    assert usagedb.insert_limits(conn, snap, host="hostA") is True
    # 값이 달라 dedup 가드는 통과하지만, 같은 (host, ts, source, account) 라
    # lkey 충돌로 skip 된다.
    dup = dict(snap, session_pct=99)
    assert usagedb.insert_limits(conn, dup, host="hostA") is False
    assert usagedb.limits_count(conn) == 1
    conn.close()


async def test_backfill_limits_lkey_is_idempotent_and_skips_residue():
    conn = usagedb.connect(":memory:")
    # 레거시 경로(host 없이) — 값이 달라야 dedup 가드를 통과해 2행이 쌓인다.
    usagedb.insert_limits(conn, {"ts": 1.0, "account": "a@b.c",
                                 "session_pct": 1, "source": "probe"})
    usagedb.insert_limits(conn, {"ts": 1.0, "account": "a@b.c",
                                 "session_pct": 2, "source": "probe"})
    assert usagedb.limits_count(conn) == 2
    n = usagedb.backfill_limits_lkey(conn, "hostA")
    assert n == 1                      # 잔상 중복은 첫 건만 키를 갖는다
    assert usagedb.backfill_limits_lkey(conn, "hostA") == 0    # 멱등
    keyed = conn.execute(
        "SELECT COUNT(*) AS n FROM limits WHERE lkey IS NOT NULL").fetchone()
    assert keyed["n"] == 1
    assert usagedb.limits_count(conn) == 2      # 기존 행을 지우지 않는다
    conn.close()


# ── 커서·별칭 저장소 ────────────────────────────────────────────────────────

async def test_export_cursor_roundtrip():
    conn = usagedb.connect(":memory:")
    assert usagedb.get_export_cursor(conn, "xc") == 0
    usagedb.set_export_cursor(conn, "xc", 42, last_ts=time.time())
    assert usagedb.get_export_cursor(conn, "xc") == 42
    usagedb.set_export_cursor(conn, "xc", 99)
    assert usagedb.get_export_cursor(conn, "xc") == 99
    assert usagedb.get_export_cursor(conn, "limits") == 0    # 종류별 독립
    conn.close()


async def test_sync_remote_partial_update_and_error_clear():
    conn = usagedb.connect(":memory:")
    usagedb.set_sync_remote(conn, "server", cursor="10", label="sync.example",
                            last_err="boom")
    st = usagedb.get_sync_remote(conn, "server")
    assert st["cursor"] == "10" and st["label"] == "sync.example"
    assert st["last_err"] == "boom"
    # 성공 시 사유를 **지운다** — 옛 오류가 남아 조용한 오해를 부르지 않게.
    usagedb.set_sync_remote(conn, "server", cursor="20", last_ok=1.0,
                            last_err="", rows_in_delta=5)
    st = usagedb.get_sync_remote(conn, "server")
    assert st["cursor"] == "20" and st["last_err"] is None
    assert st["rows_in"] == 5 and st["label"] == "sync.example"   # 부분 갱신
    conn.close()


async def test_account_alias_roundtrip():
    conn = usagedb.connect(":memory:")
    usagedb.put_account_alias(conn, "ab12", label="회사")
    usagedb.put_account_alias(conn, "ab12", account="me@corp.com")
    al = usagedb.account_aliases(conn)
    assert al["ab12"] == {"account": "me@corp.com", "label": "회사"}
    conn.close()


# ── 가명·키 계층(표준 라이브러리만) ─────────────────────────────────────────

async def test_acct_id_stable_across_machines():
    """같은 이메일 → 같은 가명. 대소문자·공백·NFC 차이를 흡수하되 **그 이상은 아니다**
    (사용자 설정에 따라 달라지는 규칙을 끼우면 머신마다 계정이 쪼개진다)."""
    k_id, _ = syncrypto.derive_keys(b"\x01" * 32)
    a = syncrypto.acct_id(k_id, "Me@Corp.com")
    b = syncrypto.acct_id(k_id, "  me@corp.com  ")
    assert a == b and len(a) == syncrypto.ACCT_ID_LEN * 2
    assert syncrypto.acct_id(k_id, "other@corp.com") != a
    # 미상 계정은 가명 없이(NULL) 나간다 — 임의 계정에 접붙이지 않는다.
    for miss in (None, "", "unknown", "  "):
        assert syncrypto.acct_id(k_id, miss) is None


async def test_acct_id_does_not_leak_email():
    k_id, _ = syncrypto.derive_keys(b"\x02" * 32)
    a = syncrypto.acct_id(k_id, "secret.person@example.com")
    assert "secret" not in a and "example" not in a and "@" not in a
    # 키가 다르면 가명도 다르다(서버가 사전 대입으로 되돌리지 못하게).
    k2, _ = syncrypto.derive_keys(b"\x03" * 32)
    assert syncrypto.acct_id(k2, "secret.person@example.com") != a


async def test_rkey_separates_kinds_and_hides_xkey():
    k_id, _ = syncrypto.derive_keys(b"\x04" * 32)
    x = "msg_01ABCDEF:req_99"
    r1 = syncrypto.rkey(k_id, "xc", x)
    r2 = syncrypto.rkey(k_id, "lim", x)
    assert r1 != r2                      # 종류별 키 공간 분리
    assert r1 == syncrypto.rkey(k_id, "xc", x)      # 결정적(멱등 병합의 근거)
    assert "msg_01" not in r1


async def test_derive_keys_separates_purposes():
    k_id, k_enc = syncrypto.derive_keys(b"\x05" * 32)
    assert k_id != k_enc and len(k_id) == 32 and len(k_enc) == 32
    try:
        syncrypto.derive_keys(b"short")
    except syncrypto.SyncCryptoError:
        pass
    else:
        raise AssertionError("짧은 마스터 키를 받아들이면 안 된다")


async def test_invite_roundtrip_and_typo_detection():
    m = syncrypto.gen_master()
    code = syncrypto.format_invite(m)
    assert syncrypto.parse_invite(code) == m
    assert syncrypto.parse_invite(code.lower().replace("-", " ")) == m
    # 한 글자 오타는 **조용히 통과하면 안 된다**(체크섬).
    bad = list(code.replace("-", ""))
    bad[0] = "A" if bad[0] != "A" else "B"
    try:
        syncrypto.parse_invite("".join(bad))
    except syncrypto.SyncCryptoError:
        pass
    else:
        raise AssertionError("오타 난 초대 코드가 통과했다")


async def test_master_and_host_id_files_are_0600():
    import os
    import stat
    import tempfile
    d = tempfile.mkdtemp(prefix="pytmux-sync-key-")
    p = os.path.join(d, "sync_vault.key")
    m1 = syncrypto.load_or_create_master(p)
    assert syncrypto.load_or_create_master(p) == m1        # 재사용
    hid = syncrypto.ensure_host_id(d)
    assert syncrypto.ensure_host_id(d) == hid              # 안정
    if os.name != "nt":                                    # POSIX 권한만 의미
        for f in (p, os.path.join(d, "host_id")):
            assert stat.S_IMODE(os.stat(f).st_mode) == 0o600, f


# ── 레코드 봉인(소프트 의존) ────────────────────────────────────────────────

async def test_seal_unseal_roundtrip_and_tamper():
    if not syncrypto.available():
        from run import skip
        skip("cryptography 미설치 — 봉인/개봉 미검증")
    _, k_enc = syncrypto.derive_keys(b"\x06" * 32)
    ad = syncrypto.aad("v1", "xc", "rk1", "acct1")
    pt = b'{"input":10,"output":5}'
    nonce, ct = syncrypto.seal(k_enc, ad, pt)
    assert pt not in ct                                   # 평문이 안 남는다
    assert syncrypto.unseal(k_enc, ad, nonce, ct) == pt
    # 변조·재조합(다른 행의 AAD)·키 불일치는 전부 거부된다 = 1차 방어.
    bad_ct = bytearray(ct)
    bad_ct[0] ^= 0x01
    for args in ((k_enc, ad, nonce, bytes(bad_ct)),
                 (k_enc, syncrypto.aad("v1", "xc", "OTHER", "acct1"), nonce, ct),
                 (syncrypto.derive_keys(b"\x07" * 32)[1], ad, nonce, ct)):
        try:
            syncrypto.unseal(*args)
        except syncrypto.SyncCryptoError:
            continue
        raise AssertionError("조작된 레코드가 복호됐다")


async def test_seal_nonce_is_fresh_per_record():
    if not syncrypto.available():
        from run import skip
        skip("cryptography 미설치 — 봉인/개봉 미검증")
    _, k_enc = syncrypto.derive_keys(b"\x08" * 32)
    ad = syncrypto.aad("v1", "xc", "rk", None)
    n1, c1 = syncrypto.seal(k_enc, ad, b"same")
    n2, c2 = syncrypto.seal(k_enc, ad, b"same")
    assert n1 != n2 and c1 != c2      # 같은 평문이어도 서버에서 구분 불가
