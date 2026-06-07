"""토큰 사용량 SQLite 저장소(usagedb) + 세션 차원 집계(usagelog.dim) 단위 테스트."""
import os
import tempfile

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import usagedb, usagelog


def _rec(ts, tab, pane, session, account, tokens):
    return usagelog.make_record(ts, tab, pane, session, account, tokens)


async def test_connect_insert_query_roundtrip():
    conn = usagedb.connect(":memory:")
    assert usagedb.query_records(conn) == [], "빈 DB 는 빈 목록"
    assert usagedb.insert(conn, _rec(1_700_000_000.0, 0, 1, 1, "me@x.org", 1900))
    assert usagedb.insert(conn, _rec(1_700_000_001.0, 1, 2, 2, None, 300))
    recs = usagedb.query_records(conn)
    assert len(recs) == 2
    assert recs[0]["tokens"] == 1900 and recs[0]["account"] == "me@x.org"
    assert recs[0]["pane"] == 1 and recs[0]["session"] == 1
    assert recs[1]["account"] == usagelog.UNKNOWN, "account None → unknown"
    # ts 오름차순
    assert recs[0]["ts"] < recs[1]["ts"]
    conn.close()


async def test_query_limit_returns_recent_in_order():
    conn = usagedb.connect(":memory:")
    for i in range(5):
        usagedb.insert(conn, _rec(1_700_000_000.0 + i, 0, 1, 1, "a@x.org", i + 1))
    # 최근 2건(토큰 4,5)을 ts 오름차순으로
    recs = usagedb.query_records(conn, limit=2)
    assert [r["tokens"] for r in recs] == [4, 5]
    conn.close()


async def test_insert_many_and_count():
    conn = usagedb.connect(":memory:")
    n = usagedb.insert_many(conn, [
        _rec(1_700_000_000.0, 0, 1, 1, "a@x.org", 100),
        _rec(1_700_000_100.0, 0, 1, 1, "a@x.org", 200),
        _rec(1_700_086_400.0, 1, 2, 2, "b@y.org", 50),
    ])
    assert n == 3 and usagedb.count(conn) == 3
    assert usagedb.insert_many(conn, []) == 0
    conn.close()


async def test_total_for_day_matches_bucket_key():
    conn = usagedb.connect(":memory:")
    t0 = 1_700_000_000.0
    usagedb.insert(conn, _rec(t0, 0, 1, 1, "a@x.org", 1000))
    usagedb.insert(conn, _rec(t0 + 100, 0, 1, 1, "a@x.org", 500))
    usagedb.insert(conn, _rec(t0 + 86_400, 1, 2, 2, "a@x.org", 9999))  # 다음 날
    day0 = usagelog.bucket_key(t0, "day")
    assert usagedb.total_for_day(conn, day0) == 1500, "그날만 합산"
    assert usagedb.total_for_day(conn, "1900-01-01") == 0, "없는 날 0"
    conn.close()


async def test_update_accounts_remaps_untrusted():
    conn = usagedb.connect(":memory:")
    usagedb.insert(conn, _rec(1.0, 0, 1, 1, "me@woojinkim.org", 10))
    usagedb.insert(conn, _rec(2.0, 0, 1, 1, "gi…@github.com", 5))  # 오검출
    usagedb.insert(conn, _rec(3.0, 0, 1, 1, None, 7))              # 이미 unknown
    before = usagedb.account_counts(conn)
    assert before.get("gi…@github.com") == 1
    changed = usagedb.update_accounts(conn, keep_accounts=set(),
                                      keep_domains={"woojinkim.org"})
    assert changed == 1, "비신뢰 1건만 정정"
    after = usagedb.account_counts(conn)
    assert "gi…@github.com" not in after
    assert after["me@woojinkim.org"] == 1
    assert after[usagelog.UNKNOWN] == 2, "오검출 1 + 기존 unknown 1"
    conn.close()


async def test_prune_deletes_old():
    conn = usagedb.connect(":memory:")
    usagedb.insert(conn, _rec(100.0, 0, 1, 1, "a@x.org", 1))
    usagedb.insert(conn, _rec(200.0, 0, 1, 1, "a@x.org", 2))
    usagedb.insert(conn, _rec(300.0, 0, 1, 1, "a@x.org", 3))
    deleted = usagedb.prune(conn, before_ts=250.0)
    assert deleted == 2 and usagedb.count(conn) == 1
    assert usagedb.query_records(conn)[0]["tokens"] == 3
    conn.close()


async def test_import_jsonl_preserves_history():
    path = tempfile.mktemp(suffix=".tokens.jsonl")
    try:
        usagelog.append(path, _rec(1_700_000_000.0, 0, 1, 1, "me@x.org", 1900))
        usagelog.append(path, _rec(1_700_000_001.0, 1, 2, 2, None, 300))
        conn = usagedb.connect(":memory:")
        n = usagedb.import_jsonl(conn, path)
        assert n == 2 and usagedb.count(conn) == 2
        # 합계 보존(round-trip 동치)
        jsonl_total = sum(r["tokens"] for r in usagelog.read(path))
        db_total = sum(r["tokens"] for r in usagedb.query_records(conn))
        assert jsonl_total == db_total == 2200
        conn.close()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def test_file_db_persists_and_sets_schema_version():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "db", "claude-tokens.db")
    try:
        conn = usagedb.connect(path)
        usagedb.insert(conn, _rec(1.0, 0, 1, 1, "a@x.org", 42))
        conn.close()
        assert os.path.exists(path), "파일 생성됨"
        # 재연결해도 데이터 유지 + 스키마 버전
        conn2 = usagedb.connect(path)
        assert usagedb.count(conn2) == 1
        ver = conn2.execute("PRAGMA user_version").fetchone()[0]
        assert ver == usagedb.SCHEMA_VERSION
        conn2.close()
    finally:
        import shutil
        shutil.rmtree(d, ignore_errors=True)


async def test_aggregate_dim_session_groups_by_session():
    """[패널] 탭: dim='session' 이면 (재사용되는)패널 id 가 아니라 세션 id 로 묶는다."""
    recs = [
        # 같은 세션 1 이 다른 패널(1,3)에 걸쳐도 한 그룹으로
        _rec(1_700_000_000.0, 0, 1, 1, "a@x.org", 1000),
        _rec(1_700_000_100.0, 0, 3, 1, "a@x.org", 500),
        _rec(1_700_000_200.0, 1, 2, 2, "a@x.org", 200),
    ]
    agg = usagelog.aggregate(recs, "day", dim="session")
    assert agg["groups"]["세션 1"] == 1500
    assert agg["groups"]["세션 2"] == 200
    assert agg["total"] == 1700
    # 하위호환 별칭
    assert agg["accounts"] == agg["groups"]
    # summary_lines 도 세션 라벨
    lines = usagelog.summary_lines(recs, "day", dim="session")
    joined = "\n".join(lines)
    assert "세션별" in joined and "[세션 1]" in joined


async def test_aggregate_default_dim_unchanged():
    """dim 기본값(account)은 기존 동작 그대로(하위호환)."""
    recs = [_rec(1_700_000_000.0, 0, 1, 1, "a@x.org", 1000),
            _rec(1_700_000_100.0, 0, 1, 1, "b@y.org", 500)]
    agg = usagelog.aggregate(recs, "day")
    assert agg["accounts"]["a@x.org"] == 1000 and agg["accounts"]["b@y.org"] == 500
    assert "계정별" in "\n".join(usagelog.summary_lines(recs, "day"))


def _load_script(name):
    import importlib.util
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(here, "scripts", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


async def test_import_token_jsonl_script():
    """scripts/import_token_jsonl: JSONL → DB 임포트(미리보기/적용/중복 거부)."""
    import shutil
    d = tempfile.mkdtemp()
    try:
        jsonl = os.path.join(d, "default.sock.tokens.jsonl")
        usagelog.append(jsonl, _rec(1_700_000_000.0, 0, 1, 1, "me@x.org", 100))
        usagelog.append(jsonl, _rec(1_700_000_100.0, 0, 1, 1, "me@x.org", 200))
        db = os.path.join(d, "db", "claude-tokens.db")
        mod = _load_script("import_token_jsonl")
        # 미리보기: DB 미생성
        assert mod.main(["--db", db, jsonl]) == 0
        assert not os.path.exists(db), "미리보기는 쓰지 않음"
        # 적용: 2건 임포트
        assert mod.main(["--apply", "--db", db, jsonl]) == 0
        conn = usagedb.connect(db)
        assert usagedb.count(conn) == 2
        conn.close()
        # 중복 거부(--force 없이)
        assert mod.main(["--apply", "--db", db, jsonl]) == 2
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_migrate_db_mode_remaps_untrusted():
    """scripts/migrate_token_accounts --db: DB 의 비신뢰 계정을 unknown 으로 정정."""
    import shutil
    d = tempfile.mkdtemp()
    try:
        db = os.path.join(d, "claude-tokens.db")
        conn = usagedb.connect(db)
        usagedb.insert(conn, _rec(1.0, 0, 1, 1, "me@woojinkim.org", 10))
        usagedb.insert(conn, _rec(2.0, 0, 1, 1, "gi…@github.com", 5))
        conn.close()
        mod = _load_script("migrate_token_accounts")
        # 드라이런: 변경 없음
        assert mod.main(["--db", db, "--keep-domain", "woojinkim.org"]) == 0
        conn = usagedb.connect(db)
        assert usagedb.account_counts(conn).get("gi…@github.com") == 1
        conn.close()
        # 적용: github 오탐 → unknown
        assert mod.main(["--db", db, "--keep-domain", "woojinkim.org",
                         "--apply"]) == 0
        conn = usagedb.connect(db)
        counts = usagedb.account_counts(conn)
        assert "gi…@github.com" not in counts
        assert counts["me@woojinkim.org"] == 1 and counts[usagelog.UNKNOWN] == 1
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)
