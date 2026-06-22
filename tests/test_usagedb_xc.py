"""트랜스크립트 권위 토큰 회계(usagedb usage_xc, v7 §10-D) 단위 테스트.

usage_xc 는 스크랩 usage 테이블과 분리된 멱등(INSERT OR IGNORE) 정확 집계로,
transcript.parse_line 의 rec(4항목+모델+sidechain)을 받아 적재한다. ts 는 ISO-Z
문자열을 epoch 초로 정규화해 기존 usage.ts 와 동일 단위로 저장한다.
"""
import harness  # noqa: F401  (경로 설정 + 플러그인 별칭 등록)
from pytmuxlib import transcript, usagedb, usagelog


def _rec(xkey, inp=10, out=5, cc=0, cr=0, model="claude-opus-4-8",
         ts="2026-06-22T10:00:00.000Z", sid="s1", sidechain=0):
    return {"xkey": xkey, "ts": ts, "session_uuid": sid, "model": model,
            "input": inp, "output": out, "cache_create": cc,
            "cache_read": cr, "is_sidechain": sidechain}


async def test_v7_schema_present_on_fresh_db():
    conn = usagedb.connect(":memory:")
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"usage_xc", "usage_xc_cursor"} <= have
    v = int(conn.execute("PRAGMA user_version").fetchone()[0])
    assert v >= 7
    conn.close()


async def test_insert_xc_is_idempotent_on_xkey():
    conn = usagedb.connect(":memory:")
    assert usagedb.insert_xc(conn, _rec("m1:r1", inp=100, out=50)) is True
    # 같은 xkey 재삽입 → 무시(False), 카운트 불변.
    assert usagedb.insert_xc(conn, _rec("m1:r1", inp=999, out=999)) is False
    assert usagedb.xc_count(conn) == 1
    t = usagedb.xc_totals(conn)
    assert t["input"] == 100 and t["output"] == 50    # 첫 값 유지
    conn.close()


async def test_insert_xc_many_counts_only_new_rows():
    conn = usagedb.connect(":memory:")
    recs = [_rec("m1:r1"), _rec("m2:r2"), _rec("m3:r3")]
    assert usagedb.insert_xc_many(conn, recs) == 3
    # 2 중복 + 1 신규 → 새로 들어간 건 1.
    again = [_rec("m2:r2"), _rec("m3:r3"), _rec("m4:r4")]
    assert usagedb.insert_xc_many(conn, again) == 1
    assert usagedb.xc_count(conn) == 4
    assert usagedb.insert_xc_many(conn, []) == 0
    conn.close()


async def test_xc_totals_footer_full_ratio():
    conn = usagedb.connect(":memory:")
    # in=10,out=5,cc=0,cr=985 → footer=15, full=1000. 3건.
    for i in range(3):
        usagedb.insert_xc(conn, _rec(f"m{i}:r{i}", inp=10, out=5, cr=985))
    t = usagedb.xc_totals(conn)
    assert t["footer"] == 45 and t["full"] == 3000
    assert t["cache_read"] == 2955 and t["cache_create"] == 0
    assert round(t["ratio"], 1) == round(3000 / 45, 1)
    conn.close()


async def test_xc_totals_empty_db_no_div_by_zero():
    conn = usagedb.connect(":memory:")
    t = usagedb.xc_totals(conn)
    assert t["full"] == 0 and t["footer"] == 0 and t["ratio"] == 0.0
    conn.close()


async def test_xc_totals_by_model_groups_null_as_unknown():
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc(conn, _rec("m1:r1", inp=10, out=0, cr=90,
                                 model="claude-opus-4-8"))      # full 100
    usagedb.insert_xc(conn, _rec("m2:r2", inp=20, out=0, cr=80,
                                 model="claude-haiku-4-5"))     # full 100
    usagedb.insert_xc(conn, _rec("m3:r3", inp=1, out=0, cr=0, model=None))
    by = usagedb.xc_totals_by_model(conn)
    assert by["claude-opus-4-8"] == 100
    assert by["claude-haiku-4-5"] == 100
    assert by[usagelog.UNKNOWN] == 1                            # NULL → unknown
    conn.close()


async def test_xc_daily_full_buckets_sum_to_total():
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc(conn, _rec("m1:r1", inp=10, out=5, cr=85,
                                 ts="2026-06-20T12:00:00.000Z"))
    usagedb.insert_xc(conn, _rec("m2:r2", inp=10, out=5, cr=85,
                                 ts="2026-06-22T12:00:00.000Z"))
    daily = usagedb.xc_daily_full(conn)
    # tz 무관: 일자 버킷 합 == 전체 full.
    assert sum(daily.values()) == usagedb.xc_totals(conn)["full"] == 200
    assert len(daily) == 2                                      # 서로 다른 두 날
    conn.close()


async def test_xc_cursor_roundtrip_and_upsert():
    conn = usagedb.connect(":memory:")
    assert usagedb.get_xc_cursor(conn, "/p/s.jsonl") is None
    assert usagedb.set_xc_cursor(conn, "/p/s.jsonl", 1234, 99.5) is True
    off, mt = usagedb.get_xc_cursor(conn, "/p/s.jsonl")
    assert off == 1234 and mt == 99.5
    # 같은 경로 재기록 → upsert(중복 행 없이 갱신).
    usagedb.set_xc_cursor(conn, "/p/s.jsonl", 5678, 100.0)
    off2, mt2 = usagedb.get_xc_cursor(conn, "/p/s.jsonl")
    assert off2 == 5678 and mt2 == 100.0
    conn.close()


async def test_iso_ts_normalized_to_epoch():
    import datetime as dt
    conn = usagedb.connect(":memory:")
    iso = "2026-06-22T10:00:00.000Z"
    usagedb.insert_xc(conn, _rec("m1:r1", ts=iso))
    ts = conn.execute("SELECT ts FROM usage_xc WHERE xkey='m1:r1'").fetchone()[0]
    # ISO-Z → epoch 초(float, UTC).
    expect = dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    assert isinstance(ts, float)
    assert abs(ts - expect) < 1.0
    conn.close()


async def test_parse_line_rec_inserts_directly():
    # 통합: transcript.parse_line 의 rec 형식이 insert_xc 와 그대로 호환.
    evt = {"type": "assistant", "uuid": "u1", "requestId": "r1",
           "timestamp": "2026-06-22T10:00:00.000Z", "sessionId": "s1",
           "isSidechain": True,
           "message": {"id": "m1", "model": "claude-opus-4-8", "usage": {
               "input_tokens": 7694, "output_tokens": 821,
               "cache_creation_input_tokens": 38625,
               "cache_read_input_tokens": 120000}}}
    _xkey, rec = transcript.parse_line(evt)
    conn = usagedb.connect(":memory:")
    assert usagedb.insert_xc(conn, rec, tab=2, pane=3, pytmux_session=1) is True
    row = conn.execute(
        "SELECT tab,pane,pytmux_session,model,input,output,cache_create,"
        "cache_read,is_sidechain,session_uuid FROM usage_xc").fetchone()
    assert (row["tab"], row["pane"], row["pytmux_session"]) == (2, 3, 1)
    assert row["model"] == "claude-opus-4-8"
    assert (row["input"], row["output"], row["cache_create"],
            row["cache_read"]) == (7694, 821, 38625, 120000)
    assert row["is_sidechain"] == 1 and row["session_uuid"] == "s1"
    conn.close()
