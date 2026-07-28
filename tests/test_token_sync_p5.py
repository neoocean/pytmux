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
    """프로세스 tz 를 잠시 바꾼다(조회 머신을 옮겨 보는 것과 같은 효과).

    `time.tzset` 은 POSIX 전용이라 Windows 에선 **조회 머신을 옮길 수단이 없다** —
    거기서는 명시적으로 건너뛴다(`TZ` 만 바꾸고 지나가면 두 '다른 tz' 가 실은 같은
    tz 라 tz 불변 오라클이 공허하게 통과한다 — 이 모듈이 경계 시각을 고른 이유와
    같은 함정이다)."""

    def __init__(self, tz):
        self.tz = tz

    def __enter__(self):
        if not hasattr(time, "tzset"):
            from run import skip
            skip("Windows: time.tzset 부재 — 프로세스 tz 를 바꿀 수 없다")
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


async def test_hourly_buckets_are_tz_invariant():
    """**시각** 집계도 원산지 벽시계로 버킷된다 — 일자와 같은 규칙(_XC_HOUR).

    시각은 하루보다 눈금이 촘촘해 tz 실수가 더 잘 드러난다: 'localtime' 으로
    되돌리면 조회 머신마다 시각 행이 통째로 밀려, 같은 DB 를 두 머신에서 볼 때
    5h%/1w% 열 조인키(hourly_pct)와도 어긋난다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1", _TS_EDGE, tzoff=_KST, input=100)])
    with _TZ("Asia/Seoul"):
        seoul = usagedb.xc_hourly_breakdown(conn)
    with _TZ("America/Los_Angeles"):
        la = usagedb.xc_hourly_breakdown(conn)
    # 원산지(서울) 벽시계 = 23일 01:00 — 어디서 보든 같다.
    assert [h["hour"] for h in seoul] == [h["hour"] for h in la] == \
        ["2026-07-23 01:00"]
    # 클라 bucket_key(hour) 와 **같은 문자열**이어야 hourly_pct 조인이 성립한다.
    assert seoul[0]["hour"] == usagelog.bucket_key(_TS_EDGE, "hour", _KST)
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


# ── 머신 뷰 탭(§7.3, 2026-07-25) ──────────────────────────────────────────

def _tk_records():
    base = 1_700_000_000.0
    return [{"ts": base + d * 3600, "account": "me@x.org", "tokens": 1000}
            for d in range(4)]


async def test_host_tab_hidden_when_single_machine():
    """머신이 하나뿐이면 탭 자체가 없다 — 동기화를 안 쓰는 사람에게 빈 뷰는 잡음."""
    from harness import make_app, server_only, teardown
    S = _screens()
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(S.TokenLogScreen(_tk_records()))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            assert not scr.query("#tab_host")
            # 'o' 를 눌러도 뷰가 안 바뀌고 팝업도 안 닫힌다(예약키 무동작).
            await pilot.press("o")
            await pilot.pause(0.2)
            assert app.screen_stack[-1] is scr and scr._view == "time"
    finally:
        await teardown(srv, task, sock)


async def test_host_tab_shows_machine_breakdown():
    """머신이 둘 이상이면 탭이 뜨고, [o]/클릭으로 머신별 분해 표가 나온다."""
    from harness import make_app, server_only, teardown
    S = _screens()
    srv, task, sock = await server_only()
    try:
        app = make_app(sock, None, None)
        async with app.run_test(size=(100, 36)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(S.TokenLogScreen(
                _tk_records(),
                xc_totals={"full": 100, "footer": 50,
                           "cache_read": 30, "cache_create": 20},
                xc_hosts={usagedb.LOCAL_HOST: 60, "a1b2c3d4e5f6": 40}))
            await pilot.pause(0.3)
            scr = app.screen_stack[-1]
            assert scr.query("#tab_host"), "머신 탭이 있어야 한다"
            await pilot.press("o")
            await pilot.pause(0.3)
            assert scr._view == "host"
            rows = scr._host_rows()
            assert [r[0] for r in rows][0] == i18n_t_local(S)
            assert rows[0][1] == 60 and abs(rows[0][2] - 60.0) < 1e-9
            assert rows[1][0] == "a1b2c3d4e5f6"[:12]
            # 다시 누르면 기간 뷰로 복귀(다른 뷰 탭과 같은 토글 규약).
            await pilot.press("o")
            await pilot.pause(0.3)
            assert scr._view == "time"
    finally:
        await teardown(srv, task, sock)


def i18n_t_local(S):
    from pytmuxlib import i18n
    return i18n.t("pscreen.tklog_host_local")


async def test_host_rows_sum_matches_totals():
    """머신 분해 합 = 전체 Σ (표시가 회계와 어긋나면 신뢰를 잃는다)."""
    S = _screens()
    scr = S.TokenLogScreen.__new__(S.TokenLogScreen)
    scr._xc_hosts = {usagedb.LOCAL_HOST: 7, "h2": 3, "h3": 0}
    rows = scr._host_rows()
    assert sum(v for _, v, _ in rows) == 10
    assert abs(sum(pct for _, _, pct in rows) - 100.0) < 1e-9
    assert len(rows) == 2          # 0 토큰 머신은 행을 만들지 않는다


# ── 적재 속도 관측(설계 §12 롤업 판정, 2026-07-25) ─────────────────────────

async def test_xc_growth_measures_recent_rate():
    """`xc_growth` = 롤업 트리거를 사람이 볼 수 있게 하는 유일한 수단.

    서버는 결정 3(보존 무기한)으로 계속 자라는데 결정 2(암호화 on) 때문에 **제 데이터를
    집계할 수 없다** → "언제 롤업이 필요한가"는 클라만 답한다. 창 밖 행을 최근 속도에
    섞으면(경계 무시) 이 단언이 깨진다."""
    conn = usagedb.connect(":memory:")
    now = 1_800_000_000.0
    recs = [_rec("r%d" % i, now - i * 3600.0) for i in range(48)]   # 2일치, 시간당 1건
    recs += [_rec("old%d" % i, now - (40 + i) * 86400.0) for i in range(10)]  # 창 밖
    usagedb.insert_xc_many(conn, recs)
    g = usagedb.xc_growth(conn, days=30.0, now=now)
    assert g["rows"] == 58, "전체 행수"
    assert g["recent"] == 48, "최근 30일 = 창 밖 10건 제외"
    assert abs(g["per_day"] - 48 / 30.0) < 1e-6
    assert abs(g["per_year"] - g["per_day"] * 365.0) < 1e-6
    # 빈 DB 는 0(표시층이 '-' 로 접는다).
    empty = usagedb.connect(":memory:")
    assert usagedb.xc_growth(empty, now=now)["rows"] == 0
    empty.close()
    conn.close()
