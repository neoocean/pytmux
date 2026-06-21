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


async def test_tzoff_roundtrip_and_v4_to_v5_migration():
    """§3.5①: tzoff 컬럼이 insert/query 를 왕복하고, tzoff 없는 v4 DB 가 connect 시
    v5 로 마이그레이트(컬럼 추가)되며 기존 행은 NULL(→레거시 폴백)로 보존된다."""
    import sqlite3
    # ① 신규 DB 왕복: make_record 의 tzoff 가 보존된다.
    conn = usagedb.connect(":memory:")
    assert usagedb.insert(conn, _rec(1_700_000_000.0, 0, 1, 1, "me@x.org", 100))
    rec = usagedb.query_records(conn)[0]
    assert "tzoff" in rec and isinstance(rec["tzoff"], int), rec
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == \
        usagedb.SCHEMA_VERSION
    conn.close()
    # ② v4 형태(tzoff 없는 usage + user_version=4) DB 를 만들고 connect 로 업그레이드.
    path = tempfile.mktemp(suffix=".tokens.db")
    try:
        raw = sqlite3.connect(path)
        raw.execute("CREATE TABLE usage (ts REAL NOT NULL, tab INTEGER, "
                    "pane INTEGER NOT NULL, session INTEGER, account TEXT "
                    "NOT NULL, tokens INTEGER NOT NULL)")
        raw.execute("INSERT INTO usage (ts,tab,pane,session,account,tokens) "
                    "VALUES (?,?,?,?,?,?)",
                    (1_700_000_000.0, 0, 1, 1, "old@x.org", 555))
        raw.execute("PRAGMA user_version=4")
        raw.commit()
        raw.close()
        conn = usagedb.connect(path)
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) >= 5
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage)")}
        assert "tzoff" in cols, "v5 마이그레이션이 tzoff 컬럼 추가"
        recs = usagedb.query_records(conn)
        assert len(recs) == 1 and recs[0]["tokens"] == 555
        assert "tzoff" not in recs[0], "레거시 행은 tzoff NULL → 키 생략(시스템 로컬 폴백)"
        # 새 insert 는 tzoff 를 실어 hour 버킷이 안정.
        assert usagedb.insert(conn, _rec(1_700_000_100.0, 0, 1, 1, "new@x.org", 7))
        new = [r for r in usagedb.query_records(conn) if r["account"] == "new@x.org"][0]
        assert "tzoff" in new
        conn.close()
    finally:
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(path + ext)
            except OSError:
                pass


async def test_model_roundtrip_and_v5_to_v6_migration():
    """v6(과티어): model 컬럼이 insert/query 를 왕복하고 totals_by_model 로 집계되며,
    model 없는 v5 DB 가 connect 시 v6 로 마이그레이트(컬럼 추가)되고 기존 행은 NULL
    (→모델 미귀속, 키 생략)로 보존된다. make_record(model=None)은 기존과 동일 dict."""
    import sqlite3
    # ① 신규 DB 왕복: make_record 의 model 이 보존된다. model=None 이면 키 생략.
    conn = usagedb.connect(":memory:")
    assert usagedb.insert(conn, usagelog.make_record(
        1_700_000_000.0, 0, 1, 1, "me@x.org", 900, model="opus-4.8"))
    assert usagedb.insert(conn, usagelog.make_record(
        1_700_000_001.0, 0, 1, 1, "me@x.org", 50, model="haiku-4.5"))
    assert usagedb.insert(conn, _rec(1_700_000_002.0, 0, 1, 1, "me@x.org", 7))
    recs = usagedb.query_records(conn)
    assert recs[0]["model"] == "opus-4.8" and recs[1]["model"] == "haiku-4.5"
    assert "model" not in recs[2], "model 미지정 → 키 생략(미귀속)"
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == \
        usagedb.SCHEMA_VERSION
    # totals_by_model: 미귀속은 unknown 키로 묶인다.
    tm = usagedb.totals_by_model(conn)
    assert tm == {"opus-4.8": 900, "haiku-4.5": 50, usagelog.UNKNOWN: 7}, tm
    conn.close()
    # ② v5 형태(tzoff 있고 model 없는 usage + user_version=5) → connect 로 업그레이드.
    path = tempfile.mktemp(suffix=".tokens.db")
    try:
        raw = sqlite3.connect(path)
        raw.execute("CREATE TABLE usage (ts REAL NOT NULL, tab INTEGER, "
                    "pane INTEGER NOT NULL, session INTEGER, account TEXT "
                    "NOT NULL, tokens INTEGER NOT NULL, tzoff INTEGER)")
        raw.execute("INSERT INTO usage (ts,tab,pane,session,account,tokens,tzoff) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (1_700_000_000.0, 0, 1, 1, "old@x.org", 555, 32400))
        raw.execute("PRAGMA user_version=5")
        raw.commit()
        raw.close()
        conn = usagedb.connect(path)
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) >= 6
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage)")}
        assert "model" in cols, "v6 마이그레이션이 model 컬럼 추가"
        recs = usagedb.query_records(conn)
        assert len(recs) == 1 and recs[0]["tokens"] == 555
        assert "model" not in recs[0], "레거시 행은 model NULL → 키 생략(미귀속)"
        # 새 insert 는 model 을 실어 이때부터 티어별 지출이 정량화된다.
        assert usagedb.insert(conn, usagelog.make_record(
            1_700_000_100.0, 0, 1, 1, "new@x.org", 12, model="sonnet-4.6"))
        new = [r for r in usagedb.query_records(conn)
               if r["account"] == "new@x.org"][0]
        assert new["model"] == "sonnet-4.6"
        # 레거시(미귀속)는 unknown, 신규는 모델별로 집계된다.
        assert usagedb.totals_by_model(conn) == \
            {usagelog.UNKNOWN: 555, "sonnet-4.6": 12}
        conn.close()
    finally:
        for ext in ("", "-wal", "-shm"):
            try:
                os.unlink(path + ext)
            except OSError:
                pass


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


async def test_agg_view_session_label_has_tabpane_and_start_time():
    # 이름 없는 세션 식별을 위해 대표 탭:패널 라벨 + 시작 시각(별도 gtimes 열)을 낸다
    # (2026-06-20: 라벨엔 탭:패널만, 시각은 'gtimes' 로 분리해 표시 측이 3열로 나눈다).
    # tzoff 를 고정해 머신 tz 와 무관하게 검증(ts=1_700_000_000 → 2023-11-14Z).
    recs = [
        dict(_rec(1_700_000_000.0, 1, 3, 4, "a@x.org", 100), tzoff=0),  # tab1→탭2
        dict(_rec(1_700_000_100.0, 1, 3, 4, "a@x.org", 200), tzoff=0),
    ]
    v = usagelog.agg_view(recs, "day", dim="session")
    assert v["groups"][0][0] == "세션 4 (탭2:p3)", v["groups"][0][0]
    assert v["gtimes"][0] == "11-14", v["gtimes"][0]
    # hour 버킷은 실 ts 라 시각이 시:분까지 보인다(라벨은 그대로 탭:패널만).
    vh = usagelog.agg_view(recs, "hour", dim="session")
    assert vh["groups"][0][0] == "세션 4 (탭2:p3)", vh["groups"][0][0]
    assert vh["gtimes"][0] == "11-14 22:13", vh["gtimes"][0]


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


async def test_agg_view_breaks_down_models_per_bucket_and_group():
    """요청 2026-06-21: agg_view 가 버킷(bmodels)·그룹(gmodels)마다 모델 티어 분해
    {tier: tok} 를 곁들인다 — 막대 색 분할용. 'opus-4.8'→'opus' 계열로 묶고 model
    미상은 'unknown'. top 접힘 시 '기타' 행 분해는 나머지 합."""
    mk = usagelog.make_record
    recs = [
        mk(1_700_000_000.0, 0, 1, 1, "a@x.org", 300, model="opus-4.8"),
        mk(1_700_000_100.0, 0, 1, 1, "a@x.org", 100, model="sonnet-4.6"),
        mk(1_700_000_200.0, 0, 1, 1, "a@x.org", 50),               # 미상
    ]
    v = usagelog.agg_view(recs, "day", dim="account")
    # 한 날·한 계정 → 버킷/그룹 모두 같은 분해
    assert v["bmodels"][0] == {"opus": 300, "sonnet": 100, "unknown": 50}
    assert v["gmodels"][0] == {"opus": 300, "sonnet": 100, "unknown": 50}
    # top 접힘: '기타' 그룹의 분해 = 나머지 합
    recs2 = [mk(1_700_000_000.0 + i, 0, 1, i, f"a{i}@x.org", 10,
                model=("opus-4.8" if i % 2 else "haiku-4.5"))
             for i in range(4)]
    v2 = usagelog.agg_view(recs2, "day", top=1)
    assert v2["groups"][-1][0].startswith("기타")
    # 나머지 3개(a1 opus, a2 haiku, a3 opus) → opus 20, haiku 10
    assert v2["gmodels"][-1] == {"opus": 20, "haiku": 10}, v2["gmodels"]


async def test_daily_breakdown_carries_model():
    """daily_breakdown 합성 레코드가 model 을 들고 와 day/week/month 막대도 모델
    색 분할이 되게(요청 2026-06-21). model 없는 행은 키 자체가 빠진다(미상)."""
    conn = usagedb.connect(":memory:")
    usagedb.insert(conn, usagelog.make_record(
        1_700_000_000.0, 0, 1, 1, "a@x.org", 300, model="opus-4.8"))
    usagedb.insert(conn, usagelog.make_record(
        1_700_000_100.0, 0, 1, 1, "a@x.org", 100))            # 미상
    rows = usagedb.daily_breakdown(conn)
    bym = {r.get("model"): r["tokens"] for r in rows}
    assert bym.get("opus-4.8") == 300
    assert bym.get(None) == 100, rows
    conn.close()


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


async def test_hourly_limit_pct_last_in_hour_shows_reset():
    """hourly_limit_pct 는 시각별 **마지막(ts 최신)** session_pct 를 돌려준다(MAX 아님).
    5h 창이 누적 상승하다 리셋된 시각은 직전 창 최고값이 아니라 **리셋 직후의 낮은
    값**을 보여, 계단식 막대(_hourly_spans)가 그 시각에 바로 0 으로 되돌 수 있다."""
    import time
    conn = usagedb.connect(":memory:")
    # ts 들을 로컬 정시 H, H+1 에 정렬(분/초 단위로 띄워 같은 시각에 묶이게).
    now = 1_700_000_000.0
    lt = time.localtime(now)
    base = now - lt.tm_min * 60 - lt.tm_sec          # 로컬 시각 H 시작
    nxt = base + 3600                                 # 시각 H+1

    def hk(ts):
        return time.strftime("%Y-%m-%d %H:00", time.localtime(ts))

    # 시각 H: 누적 5 → 12 상승 → 끝값 12.
    usagedb.insert_limits(conn, usagedb.snap_from_usage(_usage_dict(spct=5), base, "p"))
    usagedb.insert_limits(conn, usagedb.snap_from_usage(_usage_dict(spct=12), base + 120, "p"))
    # 시각 H+1: 18(창 최고) → 0(리셋) → 3(새 창 상승). 끝값 3(=MAX 18 아님).
    usagedb.insert_limits(conn, usagedb.snap_from_usage(_usage_dict(spct=18), nxt, "p"))
    usagedb.insert_limits(conn, usagedb.snap_from_usage(_usage_dict(spct=0), nxt + 120, "p"))
    usagedb.insert_limits(conn, usagedb.snap_from_usage(_usage_dict(spct=3), nxt + 240, "p"))

    hp = usagedb.hourly_limit_pct(conn)
    assert hp[hk(base)] == 12, hp           # 비-리셋 시각: 끝값=최고값
    assert hp[hk(nxt)] == 3, hp             # 리셋 시각: 끝값(3), 직전 최고(18) 아님
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
        conn = usagedb.connect(p)                    # v2→v3(이후 체인 v4) 마이그레이션 실행
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) \
            == usagedb.SCHEMA_VERSION
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


async def test_v4_nonemail_account_fix_migration():
    """v4(2026-06-12): 비이메일 가짜 계정(스크랩 약신호 오탐 — 실측 "Running 1 shell
    command")을 unknown 으로 정정하고 원값을 usage_acct_fixlog 에 보존. 이메일
    (전체·별칭)·unknown 은 무접촉. v3 DB 가 connect 로 자동 업그레이드, 재연결 멱등."""
    import shutil
    import tempfile as _tf
    d = _tf.mkdtemp()
    try:
        p = os.path.join(d, "t.db")
        conn = usagedb.connect(p)
        usagedb.insert(conn, _rec(100.0, 0, 1, 1, "Running 1 shell command", 146600))
        usagedb.insert(conn, _rec(200.0, 0, 1, 1, "max", 500))            # 플랜명 오탐
        usagedb.insert(conn, _rec(300.0, 0, 2, 2, "me@woojinkim.org", 1900))
        usagedb.insert(conn, _rec(400.0, 0, 2, 2, "wo…@woojinkim.org", 700))  # 별칭
        usagedb.insert(conn, _rec(500.0, 0, 3, 3, "unknown", 300))
        conn.execute("PRAGMA user_version=3")        # 구버전으로 되돌려 업그레이드 유도
        conn.commit()
        conn.close()
        conn = usagedb.connect(p)                    # v3→v4 마이그레이션 실행
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) \
            == usagedb.SCHEMA_VERSION
        accts = usagedb.totals_by_account(conn)
        # 비이메일 2건(146600+500)이 기존 unknown(300)에 합산, 이메일·별칭 보존.
        assert accts == {"unknown": 147400, "me@woojinkim.org": 1900,
                         "wo…@woojinkim.org": 700}, accts
        fixlog = [(r["rid"], r["account"]) for r in conn.execute(
            "SELECT rid, account FROM usage_acct_fixlog ORDER BY rid")]
        assert [a for _, a in fixlog] == ["Running 1 shell command", "max"]
        # 재연결(이미 v4) — 멱등: 정정·fixlog 불변.
        conn.close()
        conn = usagedb.connect(p)
        assert usagedb.totals_by_account(conn)["unknown"] == 147400
        assert int(conn.execute(
            "SELECT COUNT(*) FROM usage_acct_fixlog").fetchone()[0]) == 2
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


async def test_reconcile_breaks_down_models_per_interval():
    """v6(요청 2026-06-21): 각 대사 구간의 토큰을 모델 티어별로 분해한다(막대 색
    분할 데이터). 'opus-4.8'/'sonnet-4.6' 같은 계열-버전은 '-' 앞(계열)으로 묶고,
    model 미상(NULL)은 'unknown' 티어로. total(tokens)은 분해 합과 같다."""
    conn = usagedb.connect(":memory:")
    A = "me@woojinkim.org"
    for ts, pct in [(100.0, 5), (500.0, 9)]:
        usagedb.insert_limits(conn, usagedb.snap_from_usage(
            {"session": {"pct": pct, "reset": "2pm"}, "account": A},
            ts, "probe"))
    # 구간(100,500]: opus 두 버전(300+100=400) + sonnet 150 + 모델 미상 50.
    usagedb.insert(conn, usagelog.make_record(
        200.0, 0, 1, 1, A, 300, model="opus-4.8"))
    usagedb.insert(conn, usagelog.make_record(
        250.0, 0, 1, 1, A, 100, model="opus-4.7"))
    usagedb.insert(conn, usagelog.make_record(
        300.0, 0, 1, 1, A, 150, model="sonnet-4.6"))
    usagedb.insert(conn, usagelog.make_record(
        350.0, 0, 1, 1, A, 50))                       # model 미상 → unknown
    ivs = usagedb.reconcile(conn)
    assert len(ivs) == 1
    mdl = ivs[0]["models"]
    assert mdl == {"opus": 400, "sonnet": 150, "unknown": 50}, mdl
    assert ivs[0]["tokens"] == 600 == sum(mdl.values())
    conn.close()
