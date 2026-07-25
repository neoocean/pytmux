"""동기화 서버 P6 — 보존정책 스윕 + purge 의 쿼터 회계(설계 §9.4).

**발견(2026-07-25)**: `purge_events` 가 행을 지우면서 `rows_used`/`bytes_used` 를
되돌리지 않았다. 보존정책을 성실히 돌릴수록 vault 가 "비었는데 가득 참" 으로 굳어
새 업로드가 507 로 막힌다(S-4 가 잡은 것과 같은 결의 회계 드리프트). 보존정책을
쓸 수 있게 만들려면 이게 선결이라 함께 고친다.

정책 자체(며칠 보관)는 **운영자 결정**이라 코드가 대신 고르지 않는다 — 기본
`retain_days=0`(무기한)이면 스윕이 아예 돌지 않아 종전과 동작이 같다.

되돌리면 실패해야 하는 오라클:
  · purge 의 쿼터 차감을 빼면 → test_purge_returns_quota 실패
  · 보존 스윕을 기본 on 으로 바꾸면 → test_retention_off_by_default 실패
  · 나이 기준을 무시하고 전량 지우면 → test_retention_keeps_recent 실패
"""
import os
import sys

import harness  # noqa: F401

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from synserver import app as sapp        # noqa: E402
from synserver import db as sdb          # noqa: E402


def _vault(conn, vid="v1"):
    sdb.create_vault(conn, vid, 0.0) if hasattr(sdb, "create_vault") else None
    return vid


def _rec(rkey, size=100):
    return {"kind": "lim", "rkey": rkey, "acct_id": "a",
            "ct": b"x" * size, "nonce": b"n" * 12}


def _put(conn, vid, n, at=1000.0, base=0):
    return sdb.put_events(conn, vid,
                          [_rec("k%d" % (base + i)) for i in range(n)], at)


def _quota(conn, vid):
    r = conn.execute("SELECT rows_used, bytes_used FROM vault WHERE vault_id=?",
                     (vid,)).fetchone()
    return (int(r["rows_used"]), int(r["bytes_used"]))


def _new_db():
    conn = sdb.connect(":memory:")
    vid = "v1"
    conn.execute("INSERT INTO vault (vault_id, created) VALUES (?, ?)",
                 (vid, 0.0))
    conn.commit()
    return conn, vid


# ── purge 의 쿼터 회계 ─────────────────────────────────────────────────────

async def test_purge_returns_quota():
    """지운 만큼 쿼터가 돌아와야 한다 — 안 그러면 '비었는데 가득 참' 이 된다."""
    conn, vid = _new_db()
    _put(conn, vid, 5)
    used_rows, used_bytes = _quota(conn, vid)
    assert used_rows == 5 and used_bytes > 0
    seq = sdb.max_seq(conn, vid)
    assert sdb.purge_events(conn, vid, seq) == 5
    assert _quota(conn, vid) == (0, 0)
    conn.close()


async def test_partial_purge_returns_partial_quota():
    conn, vid = _new_db()
    _put(conn, vid, 4)
    rows = conn.execute("SELECT seq FROM event ORDER BY seq").fetchall()
    half = int(rows[1]["seq"])
    assert sdb.purge_events(conn, vid, half) == 2
    used_rows, used_bytes = _quota(conn, vid)
    assert used_rows == 2 and used_bytes > 0
    conn.close()


async def test_purge_quota_never_goes_negative():
    """레거시 vault 는 계상이 어긋나 있을 수 있다 — 음수로 내려가면 쿼터가 무한이 된다."""
    conn, vid = _new_db()
    _put(conn, vid, 3)
    conn.execute("UPDATE vault SET rows_used=1, bytes_used=1 WHERE vault_id=?",
                 (vid,))
    conn.commit()
    sdb.purge_events(conn, vid, sdb.max_seq(conn, vid))
    assert _quota(conn, vid) == (0, 0)
    conn.close()


async def test_purge_of_nothing_is_noop():
    conn, vid = _new_db()
    _put(conn, vid, 2)
    before = _quota(conn, vid)
    assert sdb.purge_events(conn, vid, 0) == 0
    assert _quota(conn, vid) == before
    conn.close()


# ── 나이 기준 보존 스윕 ────────────────────────────────────────────────────

async def test_retention_keeps_recent():
    """오래된 것만 지운다(경계: before_ts 미만)."""
    conn, vid = _new_db()
    _put(conn, vid, 3, at=1000.0)                 # 옛 이벤트
    _put(conn, vid, 2, at=9000.0, base=100)       # 최근 이벤트
    assert sdb.purge_old_events(conn, 5000.0) == 3
    left = [r["rkey"] for r in conn.execute("SELECT rkey FROM event")]
    assert len(left) == 2
    assert _quota(conn, vid)[0] == 2
    conn.close()


async def test_retention_spans_vaults_independently():
    """vault 별로 카운터가 따로라 스윕도 vault 별로 정확해야 한다."""
    conn, vid = _new_db()
    conn.execute("INSERT INTO vault (vault_id, created) VALUES ('v2', 0.0)")
    conn.commit()
    _put(conn, vid, 2, at=1000.0)
    _put(conn, "v2", 3, at=1000.0, base=50)
    _put(conn, "v2", 1, at=9000.0, base=80)
    assert sdb.purge_old_events(conn, 5000.0) == 5
    assert _quota(conn, vid)[0] == 0
    assert _quota(conn, "v2")[0] == 1
    conn.close()


# ── 기본 off(정책은 운영자 결정) ───────────────────────────────────────────

async def test_retention_off_by_default():
    """기본값은 무기한 — 코드가 사용자의 보존정책을 대신 고르지 않는다."""
    conn, _vid = _new_db()
    app = sapp.SyncApp(conn, "example.org", "https://example.org")
    assert app.retain_days == 0.0
    assert sapp.RETAIN_DAYS == 0.0


async def test_retention_reads_env_and_arg():
    conn, _vid = _new_db()
    old = os.environ.get("PYTMUX_SYNC_RETAIN_DAYS")
    os.environ["PYTMUX_SYNC_RETAIN_DAYS"] = "30"
    try:
        app = sapp.SyncApp(conn, "example.org", "https://example.org")
        assert app.retain_days == 30.0
        # 인자가 환경변수를 이긴다.
        app2 = sapp.SyncApp(conn, "example.org", "https://example.org",
                            retain_days=7)
        assert app2.retain_days == 7.0
        # 이상한 값은 기본으로 떨어진다(서버가 안 뜨면 안 된다).
        os.environ["PYTMUX_SYNC_RETAIN_DAYS"] = "며칠"
        assert sapp.SyncApp(conn, "example.org",
                            "https://example.org").retain_days == 0.0
    finally:
        if old is None:
            os.environ.pop("PYTMUX_SYNC_RETAIN_DAYS", None)
        else:
            os.environ["PYTMUX_SYNC_RETAIN_DAYS"] = old


async def test_sweep_runs_only_when_enabled():
    """스윕은 켰을 때만 돈다 — 꺼져 있으면 오래된 이벤트도 그대로."""
    conn, vid = _new_db()
    _put(conn, vid, 3, at=1000.0)
    clock = [10_000_000.0]
    off = sapp.SyncApp(conn, "example.org", "https://example.org",
                       now=lambda: clock[0])
    off._purge_due()
    assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 3
    on = sapp.SyncApp(conn, "example.org", "https://example.org",
                      now=lambda: clock[0], retain_days=1)
    on._purge_due()
    assert conn.execute("SELECT COUNT(*) FROM event").fetchone()[0] == 0
    assert on.stats.get("retention_purged") == 3
