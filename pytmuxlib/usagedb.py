"""Claude Code 토큰 사용량 SQLite 저장소 — usagelog(JSONL)의 후속 백엔드.

설계: docs/TOKEN_USAGE_STORAGE_DESIGN.md (2026-06-07 SQLite 전면 도입 결정).
레코드 스키마는 usagelog 와 동일(ts, tab, pane, session, account, tokens). 순수
규칙(버킷 포맷·UNKNOWN·신뢰 계정)은 usagelog 와 공유한다. 서버만 쓰고(단일 writer),
조회는 records 로 돌려줘 클라가 usagelog.aggregate 로 집계한다(서버측 GROUP BY 는
설계 Phase B). WAL 모드로 reader/writer 비차단.

여기 함수들은 sqlite3.Connection 을 받는다(테스트는 `:memory:` 또는 임시파일 주입).
서버는 connect() 로 연결을 한 번 열어 보관한다(단일 스레드 asyncio — 스레드 공유 없음).
"""
from __future__ import annotations

import os
import sqlite3

from . import usagelog

# 스키마 버전(PRAGMA user_version). 향후 컬럼 추가 시 분기에 사용.
SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
  ts      REAL    NOT NULL,
  tab     INTEGER,
  pane    INTEGER NOT NULL,
  session INTEGER,
  account TEXT    NOT NULL,
  tokens  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage_ts      ON usage(ts);
CREATE INDEX IF NOT EXISTS ix_usage_account ON usage(account);
CREATE INDEX IF NOT EXISTS ix_usage_pane    ON usage(pane);
CREATE INDEX IF NOT EXISTS ix_usage_session ON usage(session);
"""

_COLS = ("ts", "tab", "pane", "session", "account", "tokens")


def connect(path: str) -> sqlite3.Connection:
    """DB 연결을 열고(없으면 파일·디렉터리 생성) 스키마/WAL/타임아웃을 보장한다.

    path=":memory:" 면 인메모리(테스트). 파일이면 0700 디렉터리·0600 파일을
    지향한다(토큰 데이터는 캡처처럼 호스트 로컬·버전관리 제외)."""
    if path != ":memory:":
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        try:
            os.chmod(d, 0o700)
        except OSError:
            pass
        existed = os.path.exists(path)
    else:
        existed = True
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    # WAL: reader/writer 비차단. busy_timeout: 드문 락 흡수. 인메모리는 WAL 무의미.
    if path != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.executescript(_SCHEMA)
    conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    conn.commit()
    if path != ":memory:" and not existed:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return conn


def _row_to_rec(row) -> dict:
    """sqlite Row → usagelog 호환 dict 레코드."""
    return {"ts": row["ts"], "tab": row["tab"], "pane": row["pane"],
            "session": row["session"], "account": row["account"],
            "tokens": row["tokens"]}


def insert(conn, rec: dict) -> bool:
    """레코드 한 건 삽입. 실패해도 조용히 False(로깅이 본 흐름을 막지 않음)."""
    try:
        conn.execute(
            "INSERT INTO usage (ts,tab,pane,session,account,tokens) "
            "VALUES (?,?,?,?,?,?)",
            (float(rec.get("ts", 0.0)), rec.get("tab"), int(rec.get("pane", 0)),
             rec.get("session"), rec.get("account") or usagelog.UNKNOWN,
             int(rec.get("tokens", 0))))
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def insert_many(conn, recs) -> int:
    """레코드 여러 건 일괄 삽입(임포트용). 삽입 건수 반환."""
    rows = [(float(r.get("ts", 0.0)), r.get("tab"), int(r.get("pane", 0)),
             r.get("session"), r.get("account") or usagelog.UNKNOWN,
             int(r.get("tokens", 0))) for r in recs]
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO usage (ts,tab,pane,session,account,tokens) "
        "VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)


def query_records(conn, limit: int | None = None) -> list:
    """레코드를 ts 오름차순으로 반환(usagelog.read 호환). limit=N 이면 최근 N 건."""
    if limit is not None and limit >= 0:
        # 최근 N 건을 ts 내림차순으로 뽑은 뒤 다시 오름차순으로 돌려준다.
        cur = conn.execute(
            "SELECT * FROM (SELECT * FROM usage ORDER BY ts DESC LIMIT ?) "
            "ORDER BY ts ASC", (limit,))
    else:
        cur = conn.execute("SELECT * FROM usage ORDER BY ts ASC")
    return [_row_to_rec(r) for r in cur.fetchall()]


def total_for_day(conn, day_key: str) -> int:
    """로컬 일자 버킷(YYYY-MM-DD)의 토큰 합(예산 시드용). usagelog.bucket_key('day')
    와 동일한 strftime 규칙으로 SQL 에서 직접 합산한다."""
    cur = conn.execute(
        "SELECT COALESCE(SUM(tokens),0) AS s FROM usage "
        "WHERE strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') = ?",
        (day_key,))
    return int(cur.fetchone()["s"])


def account_counts(conn) -> dict:
    """계정별 레코드 수(정정 도구 미리보기용). {account: n}."""
    cur = conn.execute("SELECT account, COUNT(*) AS n FROM usage GROUP BY account")
    return {r["account"]: r["n"] for r in cur.fetchall()}


def update_accounts(conn, keep_accounts, keep_domains) -> int:
    """비신뢰 계정을 UNKNOWN 으로 일괄 정정(파일 재작성 불필요 — UPDATE 한 방).
    usagelog 의 신뢰 규칙을 공유한다. 변경된 행 수 반환."""
    cur = conn.execute("SELECT DISTINCT account FROM usage")
    untrusted = [r["account"] for r in cur.fetchall()
                 if not usagelog.is_trusted(r["account"], keep_accounts,
                                            keep_domains)
                 and r["account"] != usagelog.UNKNOWN]
    if not untrusted:
        return 0
    qmarks = ",".join("?" * len(untrusted))
    cur = conn.execute(
        f"UPDATE usage SET account=? WHERE account IN ({qmarks})",
        (usagelog.UNKNOWN, *untrusted))
    conn.commit()
    return cur.rowcount


def prune(conn, before_ts: float) -> int:
    """before_ts(epoch 초) 이전 레코드 삭제(보존정책). 삭제 행 수 반환."""
    cur = conn.execute("DELETE FROM usage WHERE ts < ?", (float(before_ts),))
    conn.commit()
    return cur.rowcount


def import_jsonl(conn, jsonl_path: str) -> int:
    """기존 *.tokens.jsonl 을 읽어 DB 로 일괄 적재(이력 보존). 적재 건수 반환.
    멱등이 아니므로 빈 DB(또는 미적재 상태)에 1회만 부르는 것을 전제한다."""
    recs = usagelog.read(jsonl_path)
    return insert_many(conn, recs)


def count(conn) -> int:
    """레코드 총수(임포트 가드·테스트용)."""
    return int(conn.execute("SELECT COUNT(*) AS n FROM usage").fetchone()["n"])
