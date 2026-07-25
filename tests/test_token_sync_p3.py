"""여러 머신 간 토큰 동기화 P3 — usage_xc.account 미상 백필(설계 §7.2).

설계: docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md §7.2.
동기화를 켜도 계정 미상(NULL) 41% 가 그대로면 계정별 통계가 무의미해진다. 같은
트랜스크립트 세션(session_uuid) 안에서 알려진 계정을 전파해 회수하되, **한 세션에서
계정이 갈리면 다수결이 아니라 포기**한다(잘못된 귀속 > unknown).

되돌리면 실패해야 하는 오라클:
  · 전파를 빼면 → test_backfill_fills_null_rows_within_session 실패
  · 충돌을 다수결로 바꾸면 → test_conflicting_session_is_left_alone 실패
  · 세션 경계를 무시하면 → test_backfill_does_not_cross_sessions 실패
  · 비이메일 라벨을 출처로 인정하면 → test_untrusted_account_is_not_propagated 실패
  · connect 마이그레이션(v10)을 빼면 → test_v10_migration_backfills_on_connect 실패
  · 적재 후 회수 훅을 빼면 → test_tail_hook_backfills_earlier_unknown_rows 실패
"""
import harness  # noqa: F401  (경로 설정 + 플러그인 별칭 등록)
from pytmuxlib import usagedb, usagelog


def _rec(xkey, uuid="s1", ts="2026-07-22T10:00:00.000Z", **kw):
    r = {"xkey": xkey, "ts": ts, "session_uuid": uuid, "model": "opus-4.8",
         "input": 10, "output": 5, "cache_create": 0, "cache_read": 0,
         "is_sidechain": 0}
    r.update(kw)
    return r


def _accounts(conn):
    return [r["account"] for r in conn.execute(
        "SELECT account FROM usage_xc ORDER BY xkey")]


# ── 세션 내 전파 ───────────────────────────────────────────────────────────

async def test_backfill_fills_null_rows_within_session():
    """계정을 아직 못 읽던 동안 들어온 행이 같은 세션의 알려진 계정으로 회수된다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1"), _rec("a:2")])          # 미상
    usagedb.insert_xc_many(conn, [_rec("a:3")], account="me@x.io")    # 알려짐
    assert _accounts(conn) == [None, None, "me@x.io"]
    st = usagedb.backfill_xc_accounts(conn)
    assert st["filled"] == 2 and st["sessions"] == 1
    assert st["conflicts"] == 0 and st["unresolved"] == 0
    assert _accounts(conn) == ["me@x.io"] * 3
    conn.close()


async def test_backfill_is_idempotent():
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1")])
    usagedb.insert_xc_many(conn, [_rec("a:2")], account="me@x.io")
    assert usagedb.backfill_xc_accounts(conn)["filled"] == 1
    assert usagedb.backfill_xc_accounts(conn)["filled"] == 0   # 두 번째는 무동작
    conn.close()


async def test_backfill_does_not_cross_sessions():
    """다른 세션의 계정은 절대 넘어오지 않는다 — 넘어오면 다계정 통계가 오염된다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("b:1", uuid="s2")])          # 미상 세션
    usagedb.insert_xc_many(conn, [_rec("a:1", uuid="s1")], account="me@x.io")
    st = usagedb.backfill_xc_accounts(conn)
    assert st["filled"] == 0 and st["unresolved"] == 1
    assert _accounts(conn) == ["me@x.io", None]
    conn.close()


async def test_conflicting_session_is_left_alone():
    """한 세션에 계정이 둘이면 **포기**한다(다수결 금지 — 잘못된 귀속이 더 나쁘다).

    뮤테이션: 최빈값으로 채우게 바꾸면 미상 행이 'a@x.io' 가 되어 이 단언이 깨진다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1"), _rec("a:2")], account="a@x.io")
    usagedb.insert_xc_many(conn, [_rec("a:3")], account="b@x.io")
    usagedb.insert_xc_many(conn, [_rec("a:4")])                      # 미상
    st = usagedb.backfill_xc_accounts(conn)
    assert st["filled"] == 0 and st["conflicts"] == 1
    assert _accounts(conn)[3] is None
    conn.close()


async def test_case_variants_are_one_account_not_a_conflict():
    """표기 차이(대소문자·공백)는 충돌이 아니다 — 정규화 후 같으면 한 계정으로 보고,
    저장은 그 세션에서 가장 많이 쓰인 **원문 표기**로 한다(멱등·머신 독립)."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1"), _rec("a:2")], account="me@x.io")
    usagedb.insert_xc_many(conn, [_rec("a:3")], account=" Me@X.io ")
    usagedb.insert_xc_many(conn, [_rec("a:4")])
    st = usagedb.backfill_xc_accounts(conn)
    assert st["conflicts"] == 0 and st["filled"] == 1
    assert _accounts(conn)[3] == "me@x.io"        # 최빈 원문
    conn.close()


async def test_untrusted_account_is_not_propagated():
    """비이메일 라벨(스크랩 오탐 잔재·UNKNOWN 리터럴)은 출처가 아니다 — v4 정정과
    같은 규칙. 이걸 출처로 인정하면 가짜 계정이 세션 전체로 번진다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1")],
                           account="Running 1 shell command")
    usagedb.insert_xc_many(conn, [_rec("a:2")], account=usagelog.UNKNOWN)
    usagedb.insert_xc_many(conn, [_rec("a:3")])
    st = usagedb.backfill_xc_accounts(conn)
    assert st["filled"] == 0
    assert _accounts(conn) == ["Running 1 shell command", usagelog.UNKNOWN, None]
    conn.close()


async def test_unknown_literal_rows_are_backfill_targets():
    """UNKNOWN 리터럴로 적재된 행도 NULL 과 같은 의미라 회수 대상이다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1")], account=usagelog.UNKNOWN)
    usagedb.insert_xc_many(conn, [_rec("a:2")], account="me@x.io")
    assert usagedb.backfill_xc_accounts(conn)["filled"] == 1
    assert _accounts(conn) == ["me@x.io", "me@x.io"]
    conn.close()


async def test_rows_without_session_uuid_stay_unknown():
    """session_uuid 가 없으면 이을 근거가 없다 — 그대로 두고 unresolved 로 센다."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1", uuid=None)])
    usagedb.insert_xc_many(conn, [_rec("a:2")], account="me@x.io")
    st = usagedb.backfill_xc_accounts(conn)
    assert st["filled"] == 0 and st["unresolved"] == 1
    conn.close()


async def test_backfill_scoped_to_given_sessions():
    """sessions= 를 주면 그 세션만 본다(적재 직후 증분 회수 경로)."""
    conn = usagedb.connect(":memory:")
    for u in ("s1", "s2"):
        usagedb.insert_xc_many(conn, [_rec(f"{u}:1", uuid=u)])
        usagedb.insert_xc_many(conn, [_rec(f"{u}:2", uuid=u)],
                               account="me@x.io")
    assert usagedb.backfill_xc_accounts(conn, sessions=["s1"])["filled"] == 1
    assert usagedb.backfill_xc_accounts(conn, sessions=[])["filled"] == 0
    assert usagedb.backfill_xc_accounts(conn)["filled"] == 1     # 남은 s2
    conn.close()


# ── 집계 반영 / 커버리지 ───────────────────────────────────────────────────

async def test_account_totals_move_from_unknown_after_backfill():
    """회수는 계정별 Σ 에 실제로 반영돼야 한다(unknown 덩어리 → 그 계정)."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1", input=100, output=0)])
    usagedb.insert_xc_many(conn, [_rec("a:2", input=1, output=0)],
                           account="me@x.io")
    before = usagedb.xc_totals_by_account(conn)
    assert before[usagelog.UNKNOWN] == 100
    usagedb.backfill_xc_accounts(conn)
    after = usagedb.xc_totals_by_account(conn)
    assert usagelog.UNKNOWN not in after and after["me@x.io"] == 101
    conn.close()


async def test_account_coverage_reports_share():
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1"), _rec("a:2")])
    usagedb.insert_xc_many(conn, [_rec("a:3"), _rec("a:4")], account="me@x.io")
    cov = usagedb.xc_account_coverage(conn)
    assert (cov["total"], cov["known"], cov["unknown"]) == (4, 2, 2)
    assert abs(cov["pct"] - 50.0) < 1e-9
    assert usagedb.xc_account_coverage(usagedb.connect(":memory:"))["pct"] == 0.0
    conn.close()


# ── 마이그레이션(v10) ──────────────────────────────────────────────────────

async def test_v10_migration_backfills_on_connect():
    """기존 사용자의 DB 는 첫 connect 에서 회수된다(수동 명령 없이)."""
    import os
    import tempfile
    d = tempfile.mkdtemp(prefix="pytmux-p3-")
    path = os.path.join(d, "t.db")
    conn = usagedb.connect(path)
    usagedb.insert_xc_many(conn, [_rec("a:1"), _rec("a:2")])
    usagedb.insert_xc_many(conn, [_rec("a:3")], account="me@x.io")
    conn.execute("PRAGMA user_version=9")        # v9 로 되돌려 마이그레이션 유도
    conn.commit()
    conn.close()
    conn = usagedb.connect(path)
    assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 10
    assert _accounts(conn) == ["me@x.io"] * 3
    assert usagedb._migrate_v10_xc_accounts(conn) == 0     # 멱등
    conn.close()


async def test_v10_migration_does_not_touch_token_sums():
    """데이터 정정이지 회계 변경이 아니다 — 토큰 합계는 그대로."""
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1", input=7, output=3)])
    usagedb.insert_xc_many(conn, [_rec("a:2")], account="me@x.io")
    before = usagedb.xc_totals(conn)
    usagedb.backfill_xc_accounts(conn)
    assert usagedb.xc_totals(conn) == before
    conn.close()


# ── 적재 경로 훅(서버) ─────────────────────────────────────────────────────

async def test_tail_hook_backfills_earlier_unknown_rows():
    """패널 기동 직후 계정 미확정 구간에 쌓인 행이, 계정을 알게 된 다음 테일에서
    회수된다 — 마이그레이션(한 번뿐)만으로는 새 미상 행이 계속 쌓인다."""
    from pytmuxlib import server as _server
    srv = _server.Server.__new__(_server.Server)
    srv._xc_totals_dirty = False
    conn = usagedb.connect(":memory:")
    usagedb.insert_xc_many(conn, [_rec("a:1"), _rec("a:2")])   # 계정 미확정 구간
    usagedb.insert_xc_many(conn, [_rec("a:3")], account="me@x.io")
    filled = srv._xc_backfill_accounts(conn, [_rec("a:3")])
    assert filled == 2 and srv._xc_totals_dirty is True
    assert _accounts(conn) == ["me@x.io"] * 3
    # 세션 정보가 없는 레코드만 오면 아무 것도 하지 않는다(조용한 무동작).
    srv._xc_totals_dirty = False
    assert srv._xc_backfill_accounts(conn, [_rec("a:9", uuid=None)]) == 0
    assert srv._xc_totals_dirty is False
    conn.close()
