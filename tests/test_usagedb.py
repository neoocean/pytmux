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


async def test_max_session_seeds_restart_counter():
    """§3.5②: max_session 이 기록된 최대 세션 id 를 돌려준다(재시작 시드용).
    빈 DB 는 0, dup_archive(격리)는 세지 않는다."""
    conn = usagedb.connect(":memory:")
    assert usagedb.max_session(conn) == 0, "빈 DB 는 0"
    usagedb.insert(conn, _rec(1_700_000_000.0, 0, 1, 3, "a@x.org", 100))
    usagedb.insert(conn, _rec(1_700_000_100.0, 0, 1, 7, "a@x.org", 200))
    usagedb.insert(conn, _rec(1_700_000_200.0, 0, 1, 5, "a@x.org", 50))
    assert usagedb.max_session(conn) == 7, "최대 세션 id"
    # 격리 보관소(dup_archive)에 더 큰 세션 id 가 있어도 활성 집계는 usage 만 본다.
    conn.execute("CREATE TABLE IF NOT EXISTS usage_dup_archive AS "
                 "SELECT * FROM usage WHERE 0")
    conn.execute("INSERT INTO usage_dup_archive "
                 "SELECT * FROM usage WHERE session=7")
    assert usagedb.max_session(conn) == 7, "archive 는 무시"
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


async def test_total_all_and_totals_by_account_full_history():
    """Phase B 서버측 GROUP BY: 전체 이력 합(total_all)·계정별 합(totals_by_account)이
    레코드 수 cap 과 무관하게 SQL SUM/GROUP BY 로 정확히 집계되고, usagelog.aggregate
    의 'groups'/'total'(전체 레코드 기준)과 일치한다."""
    conn = usagedb.connect(":memory:")
    recs = [
        _rec(1_700_000_000.0, 0, 1, 1, "me@x.org", 1000),
        _rec(1_700_000_100.0, 0, 1, 1, "me@x.org", 500),
        _rec(1_700_000_200.0, 1, 2, 2, "you@y.org", 300),
        _rec(1_700_000_300.0, 1, 3, 3, None, 7),       # unknown 계정
    ]
    usagedb.insert_many(conn, recs)
    assert usagedb.total_all(conn) == 1807
    by_acct = usagedb.totals_by_account(conn)
    assert by_acct == {"me@x.org": 1500, "you@y.org": 300, usagelog.UNKNOWN: 7}
    # usagelog 의 계정별 합(전체 레코드)과 동치 — 두 집계 경로의 parity.
    agg = usagelog.aggregate(recs, bucket="day", dim="account")
    assert by_acct == agg["groups"]
    assert usagedb.total_all(conn) == agg["total"]
    conn.close()


async def test_total_all_empty_db_is_zero():
    conn = usagedb.connect(":memory:")
    assert usagedb.total_all(conn) == 0
    assert usagedb.totals_by_account(conn) == {}
    conn.close()


async def test_daily_breakdown_matches_raw_bucket_aggregation():
    """버킷 전체 이력 집계(팝업 일/주/월): 서버 daily_breakdown(일자별 GROUP BY)을
    usagelog.daily_to_records 로 합성 레코드화해 agg_view 에 먹이면, **전체 raw
    레코드**를 직접 agg_view 한 day/week/month 버킷·계정 합과 정확히 일치한다 — 즉
    레코드 cap 으로 옛 버킷이 잘리던 문제를 cap 무관하게 해소한다(설계 Phase B 완성).

    hour 는 일자 합성으로 복원 불가라 검증 대상이 아니다(호출부가 raw 사용)."""
    conn = usagedb.connect(":memory:")
    # 여러 날·여러 계정·여러 세션·하루에 여러 건(SUM 접힘 검증)에 걸친 레코드.
    base = 1_700_000_000.0       # 2023-11-14 (로컬) 부근
    day = 86_400.0
    recs = [
        _rec(base + 0,            0, 1, 1, "me@x.org", 1000),
        _rec(base + 100,          0, 1, 1, "me@x.org", 500),     # 같은 날 합쳐짐
        _rec(base + 200,          1, 2, 2, "you@y.org", 300),
        _rec(base + day + 50,     0, 1, 1, "me@x.org", 700),     # 다음 날
        _rec(base + day + 60,     1, 3, 3, None, 9),             # unknown
        _rec(base + 9 * day,      2, 4, 4, "me@x.org", 42),      # 다른 주/월 가능
    ]
    usagedb.insert_many(conn, recs)
    daily = usagedb.daily_breakdown(conn)
    syn = usagelog.daily_to_records(daily)
    # 합성 레코드 총합 == 전체 이력 합(접힘 손실 없음).
    assert sum(r["tokens"] for r in syn) == usagedb.total_all(conn) == 2551
    for bucket in ("day", "week", "month"):
        got = usagelog.aggregate(syn, bucket=bucket, dim="account")
        want = usagelog.aggregate(recs, bucket=bucket, dim="account")
        # 버킷×그룹 분해, 그룹 합, 총합이 raw 직접 집계와 동일.
        assert got["buckets"] == want["buckets"], (bucket, got, want)
        assert got["groups"] == want["groups"], (bucket, got, want)
        assert got["total"] == want["total"]
    # 계정 필터(account=) 도 동일.
    assert (usagelog.aggregate(syn, "day", account="me@x.org")["total"]
            == usagelog.aggregate(recs, "day", account="me@x.org")["total"]
            == 2242)
    conn.close()


async def test_daily_breakdown_empty_and_malformed():
    """빈 DB 는 빈 목록. daily_to_records 는 깨진 일자 행을 조용히 건너뛴다."""
    conn = usagedb.connect(":memory:")
    assert usagedb.daily_breakdown(conn) == []
    conn.close()
    assert usagelog.daily_to_records(None) == []
    assert usagelog.daily_to_records([{"day": "not-a-date", "tokens": 5}]) == []
    ok = usagelog.daily_to_records([{"day": "2023-11-14", "account": "a",
                                     "session": 1, "tab": 0, "pane": 2,
                                     "tokens": 5}])
    assert len(ok) == 1 and ok[0]["tokens"] == 5 and ok[0]["account"] == "a"


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


async def test_agg_view_buckets_groups_order_and_pct():
    recs = [
        _rec(1_700_000_000.0, 0, 1, 1, "a@x.org", 1000),   # day0
        _rec(1_700_000_100.0, 0, 1, 1, "b@y.org", 9000),   # day0
        _rec(1_700_500_000.0, 1, 2, 2, "a@x.org", 100),    # 다른 날
    ]
    v = usagelog.agg_view(recs, "day", dim="account", order="time")
    assert v["total"] == 10100
    assert v["multi"] is True
    # 그룹: 토큰 많은 순(b 9000, a 1100)
    assert [g[0] for g in v["groups"]] == ["b@y.org", "a@x.org"]
    assert v["groups"][0][1] == 9000 and v["groups"][0][2] == 89  # share %
    # 버킷: 시간 내림차순(최근 먼저) — 둘째 날(100)이 먼저
    assert v["buckets"][0][1] == 100 and v["buckets"][1][1] == 10000
    # order=tokens 면 큰 버킷 먼저
    v2 = usagelog.agg_view(recs, "day", order="tokens")
    assert v2["buckets"][0][1] == 10000
    assert v2["bmax"] == 10000


async def test_agg_view_session_label_has_tabpane():
    recs = [
        _rec(1_700_000_000.0, 1, 3, 4, "a@x.org", 100),   # tab=1→탭2, pane=3
        _rec(1_700_000_100.0, 1, 3, 4, "a@x.org", 200),
    ]
    v = usagelog.agg_view(recs, "day", dim="session")
    assert v["groups"][0][0] == "세션 4 (탭2:p3)", v["groups"][0][0]


async def test_agg_view_top_folds_rest_into_others():
    """top 이 주어지면 상위 N 그룹만 남기고 나머지를 '기타 M개' 한 줄로 접는다(§4)."""
    recs = [_rec(1_700_000_000.0 + i, 0, 1, i, f"a{i}@x.org", 100 - i)
            for i in range(5)]   # 계정 5개(토큰 100,99,...,96)
    v = usagelog.agg_view(recs, "day", top=2)
    labels = [g[0] for g in v["groups"]]
    assert labels[:2] == ["a0@x.org", "a1@x.org"]
    assert labels[-1] == "기타 3개", labels
    # 기타 합 = 98+97+96 = 291
    assert v["groups"][-1][1] == 291
    # 접어도 전체 합 보존
    assert sum(g[1] for g in v["groups"]) == v["total"]


async def test_agg_view_single_group_not_multi():
    recs = [_rec(1_700_000_000.0, 0, 1, 1, "a@x.org", 100),
            _rec(1_700_000_100.0, 0, 1, 1, "a@x.org", 200)]
    v = usagelog.agg_view(recs, "day")
    assert v["multi"] is False and len(v["groups"]) == 1


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


# ---- 실측 한도 스냅샷(limits, S6 T1) ----

def _usage_dict(spct=5, wpct=14, account="me@woojinkim.org"):
    return {"session": {"pct": spct, "reset": "2pm (Asia/Seoul)"},
            "week_all": {"pct": wpct, "reset": "Jun 13 at 3am (Asia/Seoul)"},
            "week_sonnet": {"pct": 0, "reset": None},
            "account": account}


async def test_limits_snap_insert_query_roundtrip():
    """parse_usage 형식 → snap_from_usage → insert/last/query 왕복. 없는 블록은
    None 으로 평탄화되고, last_limits 가 최신 스냅샷을 돌려준다."""
    conn = usagedb.connect(":memory:")
    assert usagedb.last_limits(conn) is None, "빈 DB"
    snap = usagedb.snap_from_usage(_usage_dict(), 1_700_000_000.0, "probe")
    assert snap["session_pct"] == 5 and snap["week_all_pct"] == 14
    assert snap["session_reset"] == "2pm (Asia/Seoul)"
    assert snap["week_sonnet_pct"] == 0 and snap["week_sonnet_reset"] is None
    assert usagedb.insert_limits(conn, snap)
    # 부분 dict(인라인 한도: 세션만)도 누락 키가 None 으로 들어간다
    partial = usagedb.snap_from_usage(
        {"session": {"pct": 9, "reset": "3pm"}}, 1_700_000_100.0, "inline")
    assert partial["week_all_pct"] is None and partial["account"] is None
    assert usagedb.insert_limits(conn, partial)
    rows = usagedb.query_limits(conn)
    assert [r["session_pct"] for r in rows] == [5, 9], rows
    assert rows[0]["source"] == "probe" and rows[1]["source"] == "inline"
    last = usagedb.last_limits(conn)
    assert last["session_pct"] == 9 and last["ts"] == 1_700_000_100.0
    conn.close()


async def test_limits_dedup_consecutive_identical():
    """직전 스냅샷과 값이 전부 같으면(ts·source 무관) skip — 주기 프로브가 같은
    값을 반복해도 '값이 바뀐 순간'만 쌓인다."""
    conn = usagedb.connect(":memory:")
    s1 = usagedb.snap_from_usage(_usage_dict(spct=5), 100.0, "probe")
    assert usagedb.insert_limits(conn, s1)
    # 같은 값, 다른 ts/source → skip
    s2 = usagedb.snap_from_usage(_usage_dict(spct=5), 200.0, "panel")
    assert not usagedb.insert_limits(conn, s2)
    assert usagedb.limits_count(conn) == 1
    # 값 변화(pct 5→7) → insert
    s3 = usagedb.snap_from_usage(_usage_dict(spct=7), 300.0, "probe")
    assert usagedb.insert_limits(conn, s3)
    # 되돌아감(7→5) — '직전'과 다르므로 insert(연속 중복만 거른다)
    s4 = usagedb.snap_from_usage(_usage_dict(spct=5), 400.0, "probe")
    assert usagedb.insert_limits(conn, s4)
    assert usagedb.limits_count(conn) == 3
    conn.close()


async def test_limits_query_since_and_prune():
    """query_limits(since_ts/limit) 경계와 prune_limits(수동 보존정책) 동작."""
    conn = usagedb.connect(":memory:")
    for i, pct in enumerate([1, 2, 3, 4]):
        usagedb.insert_limits(conn, usagedb.snap_from_usage(
            _usage_dict(spct=pct), 100.0 * (i + 1), "probe"))
    assert [r["session_pct"] for r in usagedb.query_limits(conn, since_ts=200.0)] \
        == [2, 3, 4]
    assert [r["session_pct"] for r in usagedb.query_limits(conn, limit=2)] \
        == [3, 4], "최근 2건을 ts 오름차순으로"
    assert usagedb.prune_limits(conn, 250.0) == 2
    assert [r["session_pct"] for r in usagedb.query_limits(conn)] == [3, 4]
    conn.close()


async def test_limits_v1_db_upgrades_on_connect():
    """v1(usage 테이블만) DB 파일도 connect() 가 limits 테이블을 자동 추가한다
    (CREATE IF NOT EXISTS — 기존 usage 데이터 무접촉, 타 머신 업그레이드 무중단)."""
    import shutil
    import sqlite3
    d = tempfile.mkdtemp()
    try:
        db = os.path.join(d, "claude-tokens.db")
        old = sqlite3.connect(db)
        old.execute("CREATE TABLE usage (ts REAL NOT NULL, tab INTEGER, "
                    "pane INTEGER NOT NULL, session INTEGER, "
                    "account TEXT NOT NULL, tokens INTEGER NOT NULL)")
        old.execute("INSERT INTO usage VALUES (1.0, 0, 1, 1, 'a@x.org', 42)")
        old.execute("PRAGMA user_version=1")
        old.commit()
        old.close()
        conn = usagedb.connect(db)
        assert usagedb.count(conn) == 1, "기존 usage 데이터 보존"
        assert usagedb.limits_count(conn) == 0, "limits 테이블 생성됨(빈 상태)"
        assert usagedb.insert_limits(conn, usagedb.snap_from_usage(
            _usage_dict(), 2.0, "probe"))
        assert (int(conn.execute("PRAGMA user_version").fetchone()[0])
                == usagedb.SCHEMA_VERSION)
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_v3_dedup_residue_migration():
    """v3(§5.5 ③): 잔상 중복 런(같은 pane·session·tokens, 60초내 연쇄)을 첫 건만
    남기고 usage_dup_archive 로 격리. 60초 밖 동일값(정상 재발)·다른 패널/값은
    보존. v2 DB 가 connect 로 자동 업그레이드되고, 재연결(이미 v3)은 멱등."""
    import shutil
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "t.db")
        conn = usagedb.connect(p)
        # 잔상 런: (pane1, sess1, 19300) 1초 간격 5건 → 첫 건만 남아야 한다.
        for i in range(5):
            usagedb.insert(conn, _rec(100.0 + i, 0, 1, 1, "unknown", 19300))
        usagedb.insert(conn, _rec(300.0, 0, 1, 1, "unknown", 19300))  # 60초 밖 → 보존
        usagedb.insert(conn, _rec(101.0, 0, 2, 1, "me@x.org", 500))   # 다른 패널 → 보존
        conn.execute("PRAGMA user_version=2")        # 구버전으로 되돌려 업그레이드 유도
        conn.commit()
        conn.close()
        conn = usagedb.connect(p)                    # v2→v3 마이그레이션 실행
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 3
        rows = [(r["ts"], r["pane"], r["tokens"]) for r in conn.execute(
            "SELECT ts, pane, tokens FROM usage ORDER BY ts")]
        assert rows == [(100.0, 1, 19300), (101.0, 2, 500),
                        (300.0, 1, 19300)], rows
        arch = [r["ts"] for r in conn.execute(
            "SELECT ts FROM usage_dup_archive ORDER BY ts")]
        assert arch == [101.0, 102.0, 103.0, 104.0], "런의 2번째부터 아카이브"
        # 재연결(이미 v3) — 멱등: usage/archive 불변.
        conn.close()
        conn = usagedb.connect(p)
        assert int(conn.execute(
            "SELECT COUNT(*) FROM usage").fetchone()[0]) == 3
        assert int(conn.execute(
            "SELECT COUNT(*) FROM usage_dup_archive").fetchone()[0]) == 4
        conn.close()
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def test_reconcile_intervals_sum_and_flags():
    """S6 T2: 연속 실측 스냅샷 쌍 사이 구간의 스크랩 Σ·Δpct·계정 한정·리셋 플래그.
    절대 일치 검증이 아니라 '구간 묶기'가 맞는지를 본다(두 출처는 의미가 다름)."""
    conn = usagedb.connect(":memory:")
    A = "me@woojinkim.org"
    # 실측 스냅샷 3개: pct 5 →(A) 9 →(리셋) 2
    for ts, pct, acct in [(100.0, 5, A), (500.0, 9, A), (900.0, 2, A)]:
        usagedb.insert_limits(conn, usagedb.snap_from_usage(
            {"session": {"pct": pct, "reset": "2pm"}, "account": acct},
            ts, "probe"))
    # 스크랩 레코드: 구간1(100,500]에 A 300+200 + 미식별 80(포함 — §5.5 패널
    # 화면엔 계정 라벨이 거의 안 떠 미식별이 대부분이라, 빼면 Σ=0 왜곡),
    # 타계정 999(제외돼야 함).
    # 경계: ts=100(=t0)은 이전 구간 몫(미포함), ts=500(=t1)은 포함.
    usagedb.insert(conn, _rec(100.0, 0, 1, 1, A, 7777))      # t0 정확히 → 미포함
    usagedb.insert(conn, _rec(200.0, 0, 1, 1, A, 300))
    usagedb.insert(conn, _rec(500.0, 0, 1, 1, A, 200))       # t1 정확히 → 포함
    usagedb.insert(conn, _rec(250.0, 0, 3, 3, "unknown", 80))  # 미식별 → 포함
    usagedb.insert(conn, _rec(300.0, 0, 2, 2, "b@y.org", 999))
    # 구간2(500,900]: A 50
    usagedb.insert(conn, _rec(700.0, 0, 1, 1, A, 50))
    ivs = usagedb.reconcile(conn)
    assert len(ivs) == 2, ivs
    iv1, iv2 = ivs
    assert (iv1["pct0"], iv1["pct1"], iv1["dpct"]) == (5, 9, 4)
    assert iv1["tokens"] == 580, "같은 계정+미식별 + (t0,t1] 경계 (타계정 제외)"
    assert iv1["account"] == A and not iv1["reset"]
    assert iv2["reset"] and iv2["dpct"] == -7, "pct 감소 → 5h 리셋 플래그"
    assert iv2["tokens"] == 50
    # limit: 최근 1구간만
    assert [i["t1"] for i in usagedb.reconcile(conn, limit=1)] == [900.0]
    conn.close()


async def test_reconcile_mixed_account_sums_all():
    """양 끝 스냅샷 계정이 다르거나 미상이면 계정 필터 없이 전체 합 + account=None
    (혼합 표시는 표시층 몫) — 잘못된 한쪽 계정으로 좁혀 과소집계하지 않는다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_limits(conn, usagedb.snap_from_usage(
        {"session": {"pct": 1, "reset": None}, "account": "a@x.org"}, 100.0,
        "probe"))
    usagedb.insert_limits(conn, usagedb.snap_from_usage(
        {"session": {"pct": 3, "reset": None}}, 500.0, "inline"))  # 계정 미상
    usagedb.insert(conn, _rec(200.0, 0, 1, 1, "a@x.org", 100))
    usagedb.insert(conn, _rec(300.0, 0, 2, 2, "b@y.org", 40))
    ivs = usagedb.reconcile(conn)
    assert len(ivs) == 1
    assert ivs[0]["account"] is None and ivs[0]["tokens"] == 140
    conn.close()


async def test_reconcile_skips_snapshot_without_session_pct():
    """세션 pct 없는 스냅샷(주간만 잡힌 인라인 등)은 대사 축에서 제외 — 남은
    스냅샷끼리 이어 구간을 만든다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_limits(conn, usagedb.snap_from_usage(
        {"session": {"pct": 1, "reset": None}}, 100.0, "probe"))
    usagedb.insert_limits(conn, usagedb.snap_from_usage(
        {"week_all": {"pct": 50, "reset": None}}, 200.0, "inline"))  # 세션 없음
    usagedb.insert_limits(conn, usagedb.snap_from_usage(
        {"session": {"pct": 4, "reset": None}}, 300.0, "probe"))
    ivs = usagedb.reconcile(conn)
    assert len(ivs) == 1 and (ivs[0]["t0"], ivs[0]["t1"]) == (100.0, 300.0)
    conn.close()
