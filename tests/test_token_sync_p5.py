"""토큰 동기화 P5 — 표시층: 원산지 tz 버킷 + 호스트 분해(설계 §7.1·§7.3).

P4 로 머신 간 행이 섞이기 시작하면 집계가 **조회 머신 tz** 로 버킷을 만들던 것이
그대로 버그가 된다: 같은 DB 를 서울에서 보면 22일, 로스앤젤레스에서 보면 21일 합계가
된다. 행에 실린 원산지 오프셋(tzoff)으로 버킷을 만들어 어디서 봐도 같게 만든다.

되돌리면 실패해야 하는 오라클:
  · 일자 버킷을 'localtime' 으로 되돌리면 → test_daily_buckets_are_tz_invariant 실패
  · 레거시(tzoff NULL) 폴백을 없애면 → test_legacy_rows_keep_local_fallback 실패
  · 레코드에 tzoff 를 안 실으면 → test_records_carry_tzoff_for_hour_buckets 실패
  · 호스트 분해에서 로컬(NULL)을 빠뜨리면 → test_totals_by_host_splits_origin 실패
"""
import os
import time

import harness  # noqa: F401
from pytmuxlib import usagedb, usagelog


def _rec(xkey, ts, **kw):
    r = {"xkey": xkey, "ts": ts, "session_uuid": "s1", "model": "opus-4.8",
         "input": 10, "output": 0, "cache_create": 0, "cache_read": 0,
         "is_sidechain": 0}
    r.update(kw)
    return r


class _TZ:
    """프로세스 tz 를 잠시 바꾼다(조회 머신을 옮겨 보는 것과 같은 효과)."""

    def __init__(self, tz):
        self.tz = tz

    def __enter__(self):
        self.old = os.environ.get("TZ")
        os.environ["TZ"] = self.tz
        time.tzset()

    def __exit__(self, *a):
        if self.old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = self.old
        time.tzset()


# 2026-07-22T14:30Z — 서울(+9) 벽시계로 22일 23:30, LA(-7)로 22일 07:30(같은 날).
# 원산지가 서울이면 **어디서 보든 22일**이어야 한다.
_TS = 1_784_730_600.0        # calendar.timegm((2026,7,22,14,30,0)) 로 검산
_KST = 9 * 3600
# 경계를 넘는 시각: 2026-07-22T16:00Z = 서울 23일 01:00 / LA 22일 09:00.
_TS_EDGE = 1_784_736_000.0


async def test_daily_buckets_are_tz_invariant():
    """같은 DB 를 다른 tz 에서 집계해도 **일자 합계가 같다** — 이 작업의 요점.

    **경계를 넘는 시각**을 쓴다(2026-07-22T16:00Z = 서울 23일 01:00 / LA 22일 09:00):
    안 그러면 'localtime' 으로 되돌려도 두 tz 가 우연히 같은 날이라 오라클이 공허하게
    통과한다(초안이 그랬다)."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1", _TS_EDGE, tzoff=_KST, input=100)])
    with _TZ("Asia/Seoul"):
        seoul = usagedb.xc_daily_full(conn)
        seoul_bd = usagedb.xc_daily_breakdown(conn)
    with _TZ("America/Los_Angeles"):
        la = usagedb.xc_daily_full(conn)
        la_bd = usagedb.xc_daily_breakdown(conn)
    assert seoul == la == {"2026-07-23": 100}     # 원산지(서울) 벽시계 = 23일 01:00
    assert [r["day"] for r in seoul_bd] == [r["day"] for r in la_bd] == \
        ["2026-07-23"]
    conn.close()


async def test_daily_bucket_uses_origin_not_viewer():
    """원산지가 LA 인 행은 **LA 벽시계**로 버킷된다(조회 머신이 서울이어도)."""
    conn = usagedb.connect(":memory:")
    # 같은 순간(_TS)이지만 원산지가 LA(-7) → 그 벽시계로는 22일 07:30 → 22일.
    # 원산지가 서울이면 22일 23:30 → 역시 22일이라 구분이 안 되므로, 경계를 넘는
    # 순간을 쓴다: 2026-07-22T16:00Z = 서울 23일 01:00 / LA 22일 09:00.
    ts = _TS_EDGE
    usagedb.insert_xc_many(conn, [_rec("a:1", ts, tzoff=-7 * 3600, input=5)])
    usagedb.insert_xc_many(conn, [_rec("a:2", ts, tzoff=_KST, input=7)])
    with _TZ("Asia/Seoul"):
        got = usagedb.xc_daily_full(conn)
    assert got == {"2026-07-22": 5, "2026-07-23": 7}
    conn.close()


async def test_legacy_rows_keep_local_fallback():
    """tzoff 가 없는 레거시 행은 종전대로 **조회 머신 로컬**로 버킷된다(거동 유지).

    v9 이전 129k 행이 여기 해당한다 — 이 폴백을 없애면 그 이력이 통째로 UTC 로
    밀려 과거 일자 합계가 하루 어긋난다."""
    conn = usagedb.connect(":memory:")
    conn.execute("INSERT INTO usage_xc (xkey, ts, input) VALUES ('old:1', ?, 3)",
                 (_TS,))
    conn.commit()
    with _TZ("Asia/Seoul"):
        seoul = usagedb.xc_daily_full(conn)
    with _TZ("America/Los_Angeles"):
        la = usagedb.xc_daily_full(conn)
    assert seoul == {"2026-07-22": 3}          # 서울 벽시계 23:30
    assert la == {"2026-07-22": 3}             # LA 벽시계 07:30 — 같은 날
    # 경계를 넘는 순간이면 레거시 행은 **조회 머신마다 달라진다**(그게 폴백의 정의).
    conn.execute("DELETE FROM usage_xc")
    conn.execute("INSERT INTO usage_xc (xkey, ts, input) VALUES ('old:2', ?, 3)",
                 (_TS_EDGE,))
    conn.commit()
    with _TZ("Asia/Seoul"):
        s2 = usagedb.xc_daily_full(conn)
    with _TZ("America/Los_Angeles"):
        l2 = usagedb.xc_daily_full(conn)
    assert s2 == {"2026-07-23": 3} and l2 == {"2026-07-22": 3}
    conn.close()


async def test_records_carry_tzoff_for_hour_buckets():
    """시각 버킷은 클라(usagelog.aggregate)가 만든다 — 레코드가 tzoff 를 들고
    가야 그 계산이 원산지 벽시계로 떨어진다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1", _TS, tzoff=_KST)])
    conn.execute("INSERT INTO usage_xc (xkey, ts, input) VALUES ('old:1', ?, 1)",
                 (_TS,))
    conn.commit()
    recs = usagedb.xc_query_records(conn)
    live = [r for r in recs if r.get("tzoff") is not None]
    assert len(recs) == 2
    assert live and live[0]["tzoff"] == _KST
    # 레거시 행은 키 자체가 없어야 한다(있으면 bucket_key 가 0 offset=UTC 로 오인).
    assert any("tzoff" not in r for r in recs)
    assert usagelog.bucket_key(_TS, "hour", _KST) == "2026-07-22 23:00"
    conn.close()


# ── 호스트 분해(§7.3) ──────────────────────────────────────────────────────

async def test_totals_by_host_splits_origin():
    """머신별 분해 — 로컬 적재분(host NULL)은 `<local>` 로 묶인다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1", _TS, input=60)])            # 로컬
    usagedb.insert_xc_many(conn, [_rec("b:1", _TS, input=40, host="h2")])
    got = usagedb.xc_totals_by_host(conn)
    assert got == {usagedb.LOCAL_HOST: 60, "h2": 40}
    assert sum(got.values()) == usagedb.xc_totals(conn)["full"]
    conn.close()


async def test_records_carry_host_for_origin_display():
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("b:1", _TS, host="h2")])
    usagedb.insert_xc_many(conn, [_rec("a:1", _TS)])
    hosts = {r.get("host") for r in usagedb.xc_query_records(conn)}
    assert hosts == {"h2", None}       # 로컬은 키 없음(= 이 머신)
    conn.close()


# ── 팝업 표시(§7.3) ────────────────────────────────────────────────────────

def _screens():
    import importlib
    return importlib.import_module("pytmuxlib.plugins.claude-code.screens")


async def test_sigma_line_shows_host_split_only_when_multi_machine():
    """Σ 줄의 호스트 분해는 **머신이 둘 이상일 때만** 붙는다 — 동기화를 안 쓰는
    사람에게는 표시가 종전과 한 글자도 다르지 않아야 한다."""
    S = _screens()
    scr = S.TokenLogScreen.__new__(S.TokenLogScreen)
    scr._xc_hosts = {}
    assert scr._host_text() == ""
    scr._xc_hosts = {usagedb.LOCAL_HOST: 100}          # 이 머신뿐
    assert scr._host_text() == ""
    scr._xc_hosts = {usagedb.LOCAL_HOST: 60, "a1b2c3d4e5": 40}
    txt = scr._host_text()
    assert txt.startswith("  ⇅ ")
    assert "60%" in txt and "40%" in txt
    assert "a1b2c3d4" in txt and "a1b2c3d4e5" not in txt   # host_id 는 8자로 줄임
    # 비중 큰 순으로 정렬(작은 것이 앞에 오면 읽는 순서가 뒤집힌다).
    assert txt.index("60%") < txt.index("40%")


async def test_screen_local_host_key_matches_storage():
    """표시층 상수와 저장소 정본이 어긋나면 로컬 덩어리가 호스트 id 처럼 보인다."""
    assert _screens()._LOCAL_HOST == usagedb.LOCAL_HOST
