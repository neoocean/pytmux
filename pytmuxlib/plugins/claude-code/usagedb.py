"""Claude Code 토큰 사용량 SQLite 저장소 — usagelog(JSONL)의 후속 백엔드.

설계: docs/internal/TOKEN_USAGE_STORAGE_DESIGN.md (2026-06-07 SQLite 전면 도입 결정).
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
# v2(S6 T1): limits 테이블(실측 /usage 스냅샷 이력) 추가 — CREATE IF NOT EXISTS 라
# v1 DB 도 connect() 시 자동 업그레이드된다(기존 usage 테이블 무접촉).
# v3(§5.5 2026-06-11): 데이터 정리 — tokens.step 잔상 가드(58236) **이전**에 쌓인
# 중복 커밋(같은 pane·session·tokens 가 60초 안에 연속 반복 — 하루치의 83% 사례)을
# 첫 건만 남기고 usage_dup_archive 로 격리(하드 삭제 아님). 스키마 자체는 v2 동일.
# v4(2026-06-12): 데이터 정정 — 화면 스크랩 약신호(_ORG_RE/_PLAN_RE, 같은 날 제거)가
# 적재한 **이메일 형태가 아닌** 가짜 계정(실측 사례 "Running 1 shell command")을
# unknown 으로 일괄 정정. 원값은 usage_acct_fixlog(rowid·원 계정)에 보존(포렌식·복구).
# v5(2026-06-13, §3.5①): usage.tzoff(쓰기 시점 로컬 UTC 오프셋 초) 컬럼 추가 — hour
# 버킷이 이후 DST/여행으로 시스템 tz 가 바뀌어도 재분류되지 않게(레거시 NULL 은
# bucket_key 가 시스템 로컬로 폴백 → 기존 거동 유지). ALTER ADD COLUMN(메타데이터만).
# v6(2026-06-21, OVER_TIER_MEASUREMENT §6 권고): usage.model(적재 시점 활성 모델,
# claude_model 파싱값 'opus-4.8'/'sonnet-4.6'… best-effort) 컬럼 추가 — 토큰 지출의
# **모델 귀속**을 기록해 티어별 지출·과티어를 *정량* 측정 가능하게 한다(과티어 보고서가
# "캡처도 DB도 모델 귀속 없음"으로 정량 불가였던 것을 이 시점부터 해소). 레거시 행은
# NULL(미상). ALTER ADD COLUMN(메타데이터만) — 기존 집계 쿼리는 model 을 안 보므로 무영향.
# v7(2026-06-23, §10-D 트랜스크립트 회계): usage_xc(트랜스크립트 권위 토큰)·
# usage_xc_cursor(증분 테일 offset) 테이블 추가. 기존 usage(스크랩 ↑/↓ 근사)는 라이브
# 활동신호로 보존하고, cache_read/creation 까지 담은 정확 집계는 usage_xc 가 담당한다
# (docs/internal/TOKEN_UNDERCOUNT_TRANSCRIPT_SOLUTION.md). PK=xkey(message.id:requestId
# 또는 이벤트 uuid)로 멱등(INSERT OR IGNORE) — 재적재·중복 라인이 무해. CREATE IF NOT
# EXISTS 라 v6 DB 도 connect 시 자동 생성(기존 usage/limits 무접촉).
# v8(2026-06-23, §10-D 표시층 캐시포함): usage_xc.account(적재 시점 패널 Claude 계정)
# 컬럼 추가 — 트랜스크립트 권위 회계(cache 포함)를 **계정별**로도 집계 가능하게 한다
# (팝업 계정 뷰·상태줄 계정 Σ 를 스크랩 usage 대신 usage_xc 로 전환). 트랜스크립트
# jsonl 엔 계정 라벨이 없어 ingest 시 pane._claude_account 로 채운다 — 백필된 레거시
# 행은 NULL(미상→집계서 unknown). ALTER ADD COLUMN(메타데이터만) — 기존 xc 쿼리 무영향.
SCHEMA_VERSION = 8

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
  ts      REAL    NOT NULL,
  tab     INTEGER,
  pane    INTEGER NOT NULL,
  session INTEGER,
  account TEXT    NOT NULL,
  tokens  INTEGER NOT NULL,
  tzoff   INTEGER,
  model   TEXT
);
CREATE INDEX IF NOT EXISTS ix_usage_ts      ON usage(ts);
CREATE INDEX IF NOT EXISTS ix_usage_account ON usage(account);
CREATE INDEX IF NOT EXISTS ix_usage_pane    ON usage(pane);
CREATE INDEX IF NOT EXISTS ix_usage_session ON usage(session);

CREATE TABLE IF NOT EXISTS limits (
  ts               REAL NOT NULL,
  account          TEXT,
  session_pct      INTEGER,
  session_reset    TEXT,
  week_all_pct     INTEGER,
  week_all_reset   TEXT,
  week_sonnet_pct  INTEGER,
  week_sonnet_reset TEXT,
  source           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_limits_ts ON limits(ts);

CREATE TABLE IF NOT EXISTS usage_xc (
  xkey         TEXT    PRIMARY KEY,
  ts           REAL    NOT NULL,
  session_uuid TEXT,
  tab          INTEGER,
  pane         INTEGER,
  pytmux_session INTEGER,
  model        TEXT,
  account      TEXT,
  input        INTEGER NOT NULL DEFAULT 0,
  output       INTEGER NOT NULL DEFAULT 0,
  cache_create INTEGER NOT NULL DEFAULT 0,
  cache_read   INTEGER NOT NULL DEFAULT 0,
  is_sidechain INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_xc_ts      ON usage_xc(ts);
CREATE INDEX IF NOT EXISTS ix_xc_session ON usage_xc(session_uuid);
CREATE INDEX IF NOT EXISTS ix_xc_model   ON usage_xc(model);

CREATE TABLE IF NOT EXISTS usage_xc_cursor (
  path   TEXT PRIMARY KEY,
  offset INTEGER NOT NULL,
  mtime  REAL
);
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
        # P7: synchronous=NORMAL — WAL 에선 commit 마다 fsync 하지 않고 체크포인트
        # 에서만 동기화한다. _log_tokens 가 응답 경계마다 insert+commit 하므로
        # 레코드별 fsync 비용을 없앤다. 내구성: 애플리케이션 크래시엔 안전(WAL 잔존),
        # OS 크래시/정전 시에만 마지막 미체크포인트 구간 유실 — usage 로그엔 허용.
        conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=3000")
    conn.executescript(_SCHEMA)
    cur_v = int(conn.execute("PRAGMA user_version").fetchone()[0])
    new_v = SCHEMA_VERSION
    if cur_v < 3:
        try:
            _migrate_v3_dedup_residue(conn)
        except sqlite3.Error:
            new_v = min(new_v, 2)   # 실패 시 버전 유지 → 다음 connect 재시도
    if cur_v < 4 and new_v >= 3:    # v3 실패 시 v4 도 보류(다음 connect 재시도)
        try:
            _migrate_v4_nonemail_accounts(conn)
        except sqlite3.Error:
            new_v = min(new_v, 3)
    if cur_v < 5 and new_v >= 4:    # v5: tzoff 컬럼 추가(§3.5①)
        try:
            _migrate_v5_add_tzoff(conn)
        except sqlite3.Error:
            new_v = min(new_v, 4)
    if cur_v < 6 and new_v >= 5:    # v6: model 컬럼 추가(과티어 모델 귀속)
        try:
            _migrate_v6_add_model(conn)
        except sqlite3.Error:
            new_v = min(new_v, 5)
    if cur_v < 7 and new_v >= 6:    # v7: usage_xc 트랜스크립트 회계 테이블
        try:
            _migrate_v7_xc_tables(conn)
        except sqlite3.Error:
            new_v = min(new_v, 6)
    if cur_v < 8 and new_v >= 7:    # v8: usage_xc.account 컬럼(계정별 cache 포함 집계)
        try:
            _migrate_v8_xc_account(conn)
        except sqlite3.Error:
            new_v = min(new_v, 7)
    conn.execute(f"PRAGMA user_version={new_v}")
    conn.commit()
    if path != ":memory:" and not existed:
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return conn


def _migrate_v3_dedup_residue(conn) -> int:
    """v3 데이터 마이그레이션(§5.5 ③): 잔상 중복 커밋 정리. 옮긴 행 수를 반환.

    tokens.step 의 idle_mark 잔상 가드(p4 58236) **이전** 기록엔 '응답 종료 후
    화면에 남은 ↑/↓ 토큰 잔상'이 비-busy 프레임(≈1초)마다 같은 값으로 재확정돼
    같은 (pane, session, tokens) 가 60초 안에 연쇄 반복된 런이 섞여 있다(2026-06-11
    하루치의 83%, 한 응답 최대 117회 — 토큰로그 시간/일/주/월 집계와 전체 합계를
    부풀림). 각 런의 **첫 건만** 남기고 나머지를 `usage_dup_archive` 로 옮긴다
    (하드 삭제 아님 — 포렌식·복구 보존, 집계 쿼리는 usage 만 본다).

    런 판정 = 분석 스크립트와 동일: (pane, session, tokens) 파티션에서 직전 동일
    레코드와의 간격 ≤60초(LAG 윈도 함수, SQLite ≥3.25). 60초 밖에서 우연히 같은
    토큰 수가 다시 확정된 정상 레코드는 보존된다."""
    conn.execute("CREATE TABLE IF NOT EXISTS usage_dup_archive AS "
                 "SELECT * FROM usage WHERE 0")
    conn.execute("DROP TABLE IF EXISTS _v3_dups")
    conn.execute(
        "CREATE TEMP TABLE _v3_dups AS "
        "SELECT rowid AS rid FROM ("
        "  SELECT rowid, ts - LAG(ts) OVER ("
        "    PARTITION BY pane, session, tokens ORDER BY ts) AS gap"
        "  FROM usage)"
        " WHERE gap IS NOT NULL AND gap <= 60")
    n = int(conn.execute("SELECT COUNT(*) FROM _v3_dups").fetchone()[0])
    if n:
        conn.execute("INSERT INTO usage_dup_archive "
                     "SELECT u.* FROM usage u "
                     "JOIN _v3_dups d ON u.rowid = d.rid")
        conn.execute("DELETE FROM usage WHERE rowid IN "
                     "(SELECT rid FROM _v3_dups)")
    conn.execute("DROP TABLE _v3_dups")
    return n


def _migrate_v4_nonemail_accounts(conn) -> int:
    """v4 데이터 정정(2026-06-12): 비이메일 가짜 계정 → unknown. 정정 행 수 반환.

    화면 스크랩의 약신호(_ORG_RE 조직/팀명 라벨·_PLAN_RE 플랜명 — 같은 CL 에서
    제거)는 Claude 가 산문/도구 출력에 띄운 임의 구절을 계정으로 오검출했다(실측
    사례: 'Account: Running 1 shell command' 류 → "Running 1 shell command" 계정으로
    158k 토큰 적재 — 존재하지 않는 계정이 토큰을 쓴 것처럼 보임). 신뢰 신호(①②)는
    모두 이메일이라 정상 계정은 항상 '@' 를 포함하므로, '@' 없는 비-unknown 계정을
    전부 unknown 으로 정정한다. 원값은 usage_acct_fixlog(rowid·원 계정)에 남겨
    포렌식·수동 복구가 가능하게 한다(v3 의 usage_dup_archive 와 같은 보존 원칙).

    한계: `token-account` 수동 지정으로 비이메일 라벨을 쓴 이력도 함께 접힌다 —
    수동 라벨과 스크랩 오탐을 DB 만으로는 구분할 수 없고, 잘못된 계정 표시보다
    unknown 이 옳다는 동일 원칙을 따른다(필요 시 fixlog 로 복구)."""
    conn.execute("CREATE TABLE IF NOT EXISTS usage_acct_fixlog ("
                 "rid INTEGER, account TEXT)")
    cur = conn.execute(
        "SELECT rowid, account FROM usage "
        "WHERE account <> ? AND instr(account, '@') = 0", (usagelog.UNKNOWN,))
    rows = cur.fetchall()
    if rows:
        conn.executemany("INSERT INTO usage_acct_fixlog (rid, account) "
                         "VALUES (?, ?)",
                         [(r["rowid"], r["account"]) for r in rows])
        conn.execute(
            "UPDATE usage SET account = ? "
            "WHERE account <> ? AND instr(account, '@') = 0",
            (usagelog.UNKNOWN, usagelog.UNKNOWN))
    return len(rows)


def _migrate_v5_add_tzoff(conn) -> int:
    """v5(§3.5①): usage.tzoff(쓰기 시점 로컬 UTC 오프셋 초) 컬럼을 추가한다(없을 때만).

    기존 행은 NULL → usagelog.bucket_key 가 시스템 로컬로 폴백(기존 거동 유지). 새
    레코드부터 make_record 가 tzoff 를 실어, hour 버킷이 이후 tz 변경에도 안정된다.
    ALTER ADD COLUMN 은 메타데이터만 바꿔 데이터 재기록이 없다. 추가했으면 1, 이미
    있으면 0(멱등)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage)")}
    if "tzoff" in cols:
        return 0
    conn.execute("ALTER TABLE usage ADD COLUMN tzoff INTEGER")
    return 1


def _migrate_v6_add_model(conn) -> int:
    """v6(OVER_TIER_MEASUREMENT §6): usage.model(적재 시점 활성 모델) 컬럼 추가(없을 때만).

    기존 행은 NULL → 모델 미상(과티어 보고서 시점까지의 이력은 모델 귀속 불가, 그대로).
    새 레코드부터 make_record 가 pane 의 활성 모델 배지(claude_model 파싱값)를 실어,
    이때부터 티어별 지출·과티어가 정량 측정된다. ALTER ADD COLUMN 은 메타데이터만 바꿔
    데이터 재기록이 없다. 추가했으면 1, 이미 있으면 0(멱등 — v5 tzoff 와 동일 패턴)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage)")}
    if "model" in cols:
        return 0
    conn.execute("ALTER TABLE usage ADD COLUMN model TEXT")
    return 1


def _migrate_v7_xc_tables(conn) -> int:
    """v7(§10-D): usage_xc·usage_xc_cursor 테이블 보장(없을 때만). _SCHEMA 의
    CREATE IF NOT EXISTS 가 connect 마다 이미 만들므로 여기선 멱등 확인만 — 기존
    usage/limits 는 무접촉. 만들었으면 1, 이미 있으면 0."""
    have = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    if "usage_xc" in have and "usage_xc_cursor" in have:
        return 0
    conn.executescript(_SCHEMA)
    return 1


def _migrate_v8_xc_account(conn) -> int:
    """v8(§10-D 표시층 캐시포함): usage_xc.account 컬럼 추가(없을 때만). 기존 행은
    NULL(미상 — 백필 시점엔 패널 계정 맥락이 없었다 → 계정 뷰서 unknown 으로 묶임).
    새 레코드부터 _xc_tail_pane 이 pane._claude_account 를 실어 계정별 cache-포함 집계가
    된다. ALTER ADD COLUMN(메타데이터만) — 기존 xc 쿼리는 account 를 안 보므로 무영향.
    추가했으면 1, 이미 있으면 0(멱등 — v5/v6 ALTER 와 동일 패턴)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(usage_xc)")}
    if "account" in cols:
        return 0
    conn.execute("ALTER TABLE usage_xc ADD COLUMN account TEXT")
    return 1


def _row_to_rec(row) -> dict:
    """sqlite Row → usagelog 호환 dict 레코드. tzoff(v5)·model(v6)은 있을 때만 싣는다
    (레거시 행/구 SELECT 는 None → tzoff 미상=시스템 로컬 폴백, model 미상=모델 미귀속)."""
    rec = {"ts": row["ts"], "tab": row["tab"], "pane": row["pane"],
           "session": row["session"], "account": row["account"],
           "tokens": row["tokens"]}
    if "tzoff" in row.keys() and row["tzoff"] is not None:
        rec["tzoff"] = row["tzoff"]
    if "model" in row.keys() and row["model"] is not None:
        rec["model"] = row["model"]
    return rec


def insert(conn, rec: dict) -> bool:
    """레코드 한 건 삽입. 실패해도 조용히 False(로깅이 본 흐름을 막지 않음)."""
    try:
        conn.execute(
            "INSERT INTO usage (ts,tab,pane,session,account,tokens,tzoff,model) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (float(rec.get("ts", 0.0)), rec.get("tab"), int(rec.get("pane", 0)),
             rec.get("session"), rec.get("account") or usagelog.UNKNOWN,
             int(rec.get("tokens", 0)), rec.get("tzoff"), rec.get("model")))
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def insert_many(conn, recs) -> int:
    """레코드 여러 건 일괄 삽입(임포트용). 삽입 건수 반환. tzoff(v5)는 있으면 싣고
    없으면 NULL(레거시 임포트 → bucket_key 시스템 로컬 폴백)."""
    rows = [(float(r.get("ts", 0.0)), r.get("tab"), int(r.get("pane", 0)),
             r.get("session"), r.get("account") or usagelog.UNKNOWN,
             int(r.get("tokens", 0)), r.get("tzoff"), r.get("model"))
            for r in recs]
    if not rows:
        return 0
    conn.executemany(
        "INSERT INTO usage (ts,tab,pane,session,account,tokens,tzoff,model) "
        "VALUES (?,?,?,?,?,?,?,?)", rows)
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


# ---- 서버측 GROUP BY 집계(설계 Phase B) ----
# 전체 이력(레코드 cap 무관) 토큰 합을 SQL 에서 직접 집계한다. 클라가 보는 팝업은
# 최근 N 건(query_records)만 받아 usagelog.aggregate 로 버킷×차원 전환을 라운드트립
# 없이 하지만, 그 'Σ 합계'는 받은 N 건 한정이라 이력이 N 을 넘으면 과소표시된다.
# 아래 함수들은 그 정확한 **전체 이력 합**을 서버가 SUM/GROUP BY 로 돌려준다.
# (week 키 %G-W%V 의 SQLite 3.46+ 의존 때문에 버킷별 GROUP BY 를 SQL 로 직접 하진
#  않는다 — 대신 daily_breakdown 이 일자별(%Y-%m-%d, 전버전 호환)로만 GROUP BY 하고
#  클라가 그 일자에서 주/월 키를 파이썬 strftime 으로 파생해 버킷 전체 합도 cap 무관
#  하게 만든다. 즉 버킷 전환은 여전히 클라측 집계지만 입력이 '전체 이력 일자 합'이라
#  정확·즉시이면서 옛 버킷도 안 잘린다. 설계 §Phase B.)
def total_all(conn) -> int:
    """전체 이력 토큰 총합(레코드 수 무관). 정확한 lifetime Σ."""
    return int(conn.execute(
        "SELECT COALESCE(SUM(tokens),0) AS s FROM usage").fetchone()["s"])


def totals_by_account(conn) -> dict:
    """계정별 전체 이력 토큰 합. {account: tokens} (많이 쓴 순 정렬은 표시 측)."""
    cur = conn.execute(
        "SELECT account, COALESCE(SUM(tokens),0) AS s FROM usage GROUP BY account")
    return {r["account"]: int(r["s"]) for r in cur.fetchall()}


def totals_by_model(conn) -> dict:
    """모델별 전체 이력 토큰 합. {model: tokens} (model NULL=미귀속은 'unknown' 키로).

    v6 model 컬럼의 1차 소비처 — 티어별 지출 분해(과티어 측정)의 권위 집계. v6 이전
    레코드는 model NULL 이라 'unknown' 으로 묶인다(그 시점까지 이력은 모델 미상)."""
    cur = conn.execute(
        "SELECT COALESCE(model, ?) AS m, COALESCE(SUM(tokens),0) AS s "
        "FROM usage GROUP BY COALESCE(model, ?)",
        (usagelog.UNKNOWN, usagelog.UNKNOWN))
    return {r["m"]: int(r["s"]) for r in cur.fetchall()}


def daily_breakdown(conn) -> list:
    """전체 이력을 (일자, 계정, 세션, 탭, 패널)별 토큰 합으로 GROUP BY 한 '합성 레코드'
    목록(레코드 cap 무관). 클라가 이걸 usagelog.agg_view 에 그대로 먹여 day/week/month
    × 계정/세션 집계를 **이력 전체**로 재구성한다 — 팝업이 최근 N 건(query_records)만
    받아 옛 일/주/월 버킷이 잘리던 것을 해소한다(설계 Phase B 의 미진 부분 완성).

    일자 키는 어느 SQLite 버전에서나 되는 %Y-%m-%d 만 SQL 로 뽑고, 주(%G-W%V)·월
    키는 클라가 그 일자에서 파이썬 strftime(usagelog.bucket_key)으로 파생한다 —
    week 키의 SQLite 3.46+ 의존(아래 Phase B 주석이 경계한 바로 그 문제)을 피하면서도
    버킷별 전체 합을 정확히 돌려준다. tab/pane 은 [패널] 세션 뷰의 대표 '탭:p' 라벨
    산출(usagelog._session_tabpane)용으로 함께 묶는다(시간단위 hour 버킷은 일자 합성
    레코드로 못 만들어 클라가 raw 레코드를 쓴다 — 전체 이력 시간단위는 무의미)."""
    # model(v6)도 GROUP BY 에 넣어 합성 레코드가 모델 티어를 들고 가게 한다(요청
    # 2026-06-21 — day/week/month 막대 색 분할). model 미상(NULL)은 그대로 None →
    # daily_to_records 가 미상으로 싣고 표시층이 'unknown' 티어로 묶는다. 컬럼이 없는
    # 구 DB(마이그레이션 전)에선 OperationalError → model 없이 폴백.
    has_model = any(c[1] == "model"
                    for c in conn.execute("PRAGMA table_info(usage)"))
    msel = ", model" if has_model else ""
    mgrp = ", model" if has_model else ""
    cur = conn.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS day, "
        "       account, session, tab, pane" + msel + ", "
        "       COALESCE(SUM(tokens), 0) AS tokens "
        "FROM usage GROUP BY day, account, session, tab, pane" + mgrp + " "
        "HAVING SUM(tokens) <> 0")
    out = []
    for r in cur.fetchall():
        rec = {"day": r["day"], "account": r["account"],
               "session": r["session"], "tab": r["tab"], "pane": r["pane"],
               "tokens": int(r["tokens"])}
        if has_model and r["model"] is not None:
            rec["model"] = r["model"]
        out.append(rec)
    return out


def daily_limit_pct(conn) -> dict:
    """로컬 일자별 **세션 5h 한도 최대 %**(limits 스냅샷 기준). {day(YYYY-MM-DD): max_pct}.

    토큰 스크랩 Σ(usage 테이블)는 footer 턴당 ↑/↓ 스트리밍 peak 만 담아 5h 한도 소비
    (캐시된 컨텍스트·시스템 토큰 포함)를 구조적으로 과소반영한다(docs/internal/HANDOFF §10-D).
    사용량 뷰의 일자 표시가 '그날 얼마나 썼나'를 **권위값 /usage 세션%**로 보이도록,
    각 로컬 일자에 도달한 세션% 최댓값을 돌려준다. 키는 daily_breakdown·bucket_key('day')
    와 동일한 %Y-%m-%d(localtime)라 뷰가 그대로 조인한다. NULL pct·빈 limits 는 제외."""
    cur = conn.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS day, "
        "       MAX(session_pct) AS mx FROM limits "
        "WHERE session_pct IS NOT NULL GROUP BY day")
    return {r["day"]: int(r["mx"]) for r in cur.fetchall() if r["mx"] is not None}


def hourly_limit_pct(conn) -> dict:
    """로컬 **시각별** 세션 5h 한도 **시각 끝(최신) 누적 %**(limits 스냅샷 기준).
    {hour('%Y-%m-%d %H:00'): last_pct}.

    daily_limit_pct 의 시간 버킷판 — 사용량 뷰의 Time(시간) 뷰가 '그 시각에 5h 창이
    얼마나 찼나'를 권위 /usage 세션%로 보이게 한다(5h 비율은 일 단위가 아니라 시간
    단위 뷰에 두는 게 의미상 맞다는 사용자 결정 2026-06-17).

    값은 **MAX 가 아니라 그 시각의 마지막(ts 최신) 스냅샷**이다(2026-06-17): session_pct
    는 5h 창 안에서 단조 증가하다 리셋 시 0 으로 떨어지는 누적값이라, 한 시각의 끝
    상태(=최신)를 쓰면 비-리셋 시각엔 MAX 와 같고 **리셋이 일어난 시각만** 리셋 직후의
    낮은 값을 보여 준다. 덕분에 계단식 막대(_hourly_spans)가 리셋을 한 시각 늦지 않게
    바로 그 시각에 0 으로 되돌린다(MAX 면 리셋 시각이 직전 창 최고값에 가려 한 칸 밀렸다).

    키 포맷은 usagelog.bucket_key('hour')·_BUCKET_FMT['hour']('%Y-%m-%d %H:00')와 동일해
    뷰가 그대로 조인한다. NULL pct·빈 limits 는 제외."""
    cur = conn.execute(
        "SELECT hr, session_pct FROM ("
        "  SELECT strftime('%Y-%m-%d %H:00', ts, 'unixepoch', 'localtime') AS hr, "
        "         session_pct, "
        "         ROW_NUMBER() OVER (PARTITION BY "
        "             strftime('%Y-%m-%d %H:00', ts, 'unixepoch', 'localtime') "
        "             ORDER BY ts DESC, rowid DESC) AS rn "
        "  FROM limits WHERE session_pct IS NOT NULL"
        ") WHERE rn = 1")
    return {r["hr"]: int(r["session_pct"]) for r in cur.fetchall()
            if r["session_pct"] is not None}


def hourly_week_pct(conn) -> dict:
    """로컬 **시각별** 주간(전체 모델) 한도 **시각 끝(최신) 누적 %**(limits 스냅샷).
    {hour('%Y-%m-%d %H:00'): last_week_all_pct}. hourly_limit_pct(5h 세션%)의 주간판 —
    사용량 Time 뷰의 '1w%' 열이 그 시각에 주간 창이 얼마나 찼나를 보인다(사용자 요청
    2026-06-17). 5h% 와 동일하게 그 시각의 마지막(ts 최신) 스냅샷을 쓴다(주간%도 창
    안에서 단조 증가→리셋 0). NULL pct·빈 limits 는 제외."""
    cur = conn.execute(
        "SELECT hr, week_all_pct FROM ("
        "  SELECT strftime('%Y-%m-%d %H:00', ts, 'unixepoch', 'localtime') AS hr, "
        "         week_all_pct, "
        "         ROW_NUMBER() OVER (PARTITION BY "
        "             strftime('%Y-%m-%d %H:00', ts, 'unixepoch', 'localtime') "
        "             ORDER BY ts DESC, rowid DESC) AS rn "
        "  FROM limits WHERE week_all_pct IS NOT NULL"
        ") WHERE rn = 1")
    return {r["hr"]: int(r["week_all_pct"]) for r in cur.fetchall()
            if r["week_all_pct"] is not None}


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


# ---- 트랜스크립트 권위 회계(usage_xc, v7 §10-D) ----
# 스크랩 usage 테이블과 분리된 정확 집계. transcript.parse_line 의 rec(xkey·ts(ISO)·
# session_uuid·model·input/output/cache_create/cache_read·is_sidechain)을 받아
# INSERT OR IGNORE 로 멱등 적재한다. ts 는 ISO-Z 문자열이라 epoch 초로 정규화해 저장
# (기존 usage.ts 와 동일 단위 → 같은 strftime 일자 버킷 쿼리 재사용 가능).

def _iso_to_epoch(ts) -> float:
    """ISO-8601(…Z) → epoch 초. 이미 수면 float 이면 그대로. 실패 시 0.0."""
    if isinstance(ts, (int, float)):
        return float(ts)
    if not isinstance(ts, str) or not ts:
        return 0.0
    import datetime as _dt
    try:
        s = ts.replace("Z", "+00:00")
        return _dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def _xc_row(rec: dict, tab=None, pane=None, pytmux_session=None, account=None):
    return (str(rec["xkey"]), _iso_to_epoch(rec.get("ts")),
            rec.get("session_uuid"), tab, pane, pytmux_session,
            rec.get("model"), account, int(rec.get("input", 0)),
            int(rec.get("output", 0)), int(rec.get("cache_create", 0)),
            int(rec.get("cache_read", 0)), int(rec.get("is_sidechain", 0)))


_XC_INSERT = (
    "INSERT OR IGNORE INTO usage_xc (xkey,ts,session_uuid,tab,pane,"
    "pytmux_session,model,account,input,output,cache_create,cache_read,"
    "is_sidechain) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)")


def insert_xc(conn, rec: dict, tab=None, pane=None, pytmux_session=None,
              account=None) -> bool:
    """rec 한 건 멱등 삽입. 이미 있는 xkey 면 무시(False). 실패도 조용히 False."""
    try:
        before = conn.total_changes
        conn.execute(_XC_INSERT, _xc_row(rec, tab, pane, pytmux_session, account))
        conn.commit()
        return conn.total_changes > before
    except sqlite3.Error:
        return False


def insert_xc_many(conn, recs, tab=None, pane=None, pytmux_session=None,
                   account=None) -> int:
    """rec 여러 건 멱등 일괄 삽입(백필·증분 테일). **새로 삽입된** 행 수 반환."""
    rows = [_xc_row(r, tab, pane, pytmux_session, account) for r in recs]
    if not rows:
        return 0
    before = conn.total_changes
    conn.executemany(_XC_INSERT, rows)
    conn.commit()
    return conn.total_changes - before


def xc_count(conn) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM usage_xc").fetchone()["n"])


def xc_totals(conn) -> dict:
    """전체 이력 4항목 합 + 파생값(footer=in+out, full=4합). 정확 lifetime Σ."""
    r = conn.execute(
        "SELECT COALESCE(SUM(input),0) AS i, COALESCE(SUM(output),0) AS o, "
        "COALESCE(SUM(cache_create),0) AS cc, COALESCE(SUM(cache_read),0) AS cr "
        "FROM usage_xc").fetchone()
    i, o, cc, cr = int(r["i"]), int(r["o"]), int(r["cc"]), int(r["cr"])
    foot = i + o
    full = foot + cc + cr
    return {"input": i, "output": o, "cache_create": cc, "cache_read": cr,
            "footer": foot, "full": full,
            "ratio": (full / foot) if foot else 0.0}


def xc_totals_by_model(conn) -> dict:
    """모델별 full(4항목 합) 토큰. {model('unknown'=NULL): full_tokens}."""
    cur = conn.execute(
        "SELECT COALESCE(model, ?) AS m, "
        "COALESCE(SUM(input+output+cache_create+cache_read),0) AS s "
        "FROM usage_xc GROUP BY COALESCE(model, ?)",
        (usagelog.UNKNOWN, usagelog.UNKNOWN))
    return {r["m"]: int(r["s"]) for r in cur.fetchall()}


def xc_daily_full(conn) -> dict:
    """로컬 일자(YYYY-MM-DD)별 full 토큰 합. {day: full}. 일자 뷰 권위 시드용."""
    cur = conn.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS day, "
        "COALESCE(SUM(input+output+cache_create+cache_read),0) AS s "
        "FROM usage_xc GROUP BY day")
    return {r["day"]: int(r["s"]) for r in cur.fetchall()}


def xc_daily_breakdown(conn) -> list:
    """usage_xc(트랜스크립트 권위, cache 포함) 일자별 합성 레코드 — 스크랩
    daily_breakdown 과 **같은 행 구조**(day/account/session/tab/pane/model/tokens)라
    클라 usagelog.agg_view/daily_to_records 가 그대로 먹는다(표시 코드 무변경). 차이는
    tokens 가 4항목 full(input+output+cache_create+cache_read)이고 session 은
    pytmux_session(스크랩 usage.session 과 같은 정수 의미)인 점. account/session/model
    이 NULL 인 레거시 백필 행은 그대로 NULL → 표시층이 unknown 으로 묶는다."""
    cur = conn.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS day, "
        "       account, pytmux_session AS session, tab, pane, model, "
        "       COALESCE(SUM(input+output+cache_create+cache_read), 0) AS tokens "
        "FROM usage_xc "
        "GROUP BY day, account, pytmux_session, tab, pane, model "
        "HAVING SUM(input+output+cache_create+cache_read) <> 0")
    out = []
    for r in cur.fetchall():
        rec = {"day": r["day"], "account": r["account"],
               "session": r["session"], "tab": r["tab"], "pane": r["pane"],
               "tokens": int(r["tokens"])}
        if r["model"] is not None:
            rec["model"] = r["model"]
        out.append(rec)
    return out


def xc_query_records(conn, limit: int | None = None) -> list:
    """usage_xc 레코드를 usagelog.read 호환 dict(ts/tab/pane/session/account/tokens/
    model)로, ts 오름차순 반환(limit=N 이면 최근 N 건). hour 버킷이 raw 레코드를
    쓰므로(시각 단위 합성 불가) 스크랩 query_records 의 cache 포함 대응물이다. tokens
    는 full, session 은 pytmux_session."""
    sel = ("SELECT ts, tab, pane, pytmux_session AS session, account, model, "
           "(input+output+cache_create+cache_read) AS tokens FROM usage_xc ")
    if limit is not None and limit >= 0:
        cur = conn.execute(
            "SELECT * FROM (" + sel + "ORDER BY ts DESC LIMIT ?) ORDER BY ts ASC",
            (limit,))
    else:
        cur = conn.execute(sel + "ORDER BY ts ASC")
    out = []
    for r in cur.fetchall():
        rec = {"ts": r["ts"], "tab": r["tab"], "pane": r["pane"],
               "session": r["session"],
               "account": r["account"] or usagelog.UNKNOWN,
               "tokens": int(r["tokens"])}
        if r["model"] is not None:
            rec["model"] = r["model"]
        out.append(rec)
    return out


def xc_totals_by_account(conn) -> dict:
    """계정별 full(4항목 합) 토큰. {account('unknown'=NULL): full}. 상태줄 계정 Σ·
    팝업 계정 뷰의 cache 포함 권위값(스크랩 totals_by_account 대응물)."""
    cur = conn.execute(
        "SELECT COALESCE(account, ?) AS a, "
        "COALESCE(SUM(input+output+cache_create+cache_read),0) AS s "
        "FROM usage_xc GROUP BY COALESCE(account, ?)",
        (usagelog.UNKNOWN, usagelog.UNKNOWN))
    return {r["a"]: int(r["s"]) for r in cur.fetchall()}


def get_xc_cursor(conn, path: str):
    """경로의 (offset, mtime) 또는 None(미기록)."""
    r = conn.execute("SELECT offset, mtime FROM usage_xc_cursor WHERE path=?",
                     (path,)).fetchone()
    return (int(r["offset"]), r["mtime"]) if r is not None else None


def set_xc_cursor(conn, path: str, offset: int, mtime: float | None = None):
    """경로의 테일 offset 을 upsert(증분 테일 영속)."""
    try:
        conn.execute(
            "INSERT INTO usage_xc_cursor (path,offset,mtime) VALUES (?,?,?) "
            "ON CONFLICT(path) DO UPDATE SET offset=excluded.offset, "
            "mtime=excluded.mtime",
            (path, int(offset), mtime))
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def max_session(conn) -> int:
    """기록된 최대 세션 id(없으면 0) — 재시작 후 세션 일련번호 시드용(§3.5②).

    `_claude_session_seq` 는 코어 server.__init__ 에서 부팅마다 0 으로 초기화되는데,
    재시작 후 새 Claude 세션이 다시 1,2,… 로 발급되면 영속 DB 에 남은 같은 id 의
    옛 세션과 [패널] 세션 차원 집계에서 **무관 세션이 병합**된다(설계 §8 세션 기준
    묶기를 깨뜨림). 서버는 첫 세션 부여 직전 이 값으로 카운터를 시드해 새 id 가 항상
    옛 id 보다 크게 한다. dup_archive 는 격리 보관소라 활성 집계가 안 보므로 세지
    않는다(usage 테이블만)."""
    row = conn.execute(
        "SELECT COALESCE(MAX(session), 0) AS m FROM usage").fetchone()
    return int(row["m"])


# ---- 실측 한도 스냅샷(limits, S6 T1) ----
# `/usage` 권위값(세션 5h·주간 한도 %·리셋)을 시계열로 영속한다. 기존엔 서버 메모리
# 최신값(self._usage) 하나뿐이라 재시작 시 유실·추이 조회가 불가능했다
# (docs/internal/TOKEN_ACCOUNTING_ACCURACY_SCENARIO.md §0-5).
# source: probe(그림자 질의)|panel(인패널 /usage)|inline(footer 한도 문구) — 출처 추적.
# 보존: 무제한(2026-06-10 사용자 결정 — 행이 작고 값 변화 시에만 쌓여 부담 적음).
# prune_limits 는 수동/후속 정책용으로만 둔다.

_LIMITS_VAL_COLS = ("account", "session_pct", "session_reset",
                    "week_all_pct", "week_all_reset",
                    "week_sonnet_pct", "week_sonnet_reset")


def snap_from_usage(usage: dict, ts: float, source: str) -> dict:
    """claude.parse_usage 형식 dict({"session": {"pct","reset"}, "week_all": …,
    "week_sonnet": …, "account": …}) → limits 행 dict. 없는 블록은 None."""
    def pct(key):
        b = usage.get(key)
        return b.get("pct") if isinstance(b, dict) else None

    def reset(key):
        b = usage.get(key)
        return b.get("reset") if isinstance(b, dict) else None

    return {"ts": float(ts), "account": usage.get("account"),
            "session_pct": pct("session"), "session_reset": reset("session"),
            "week_all_pct": pct("week_all"), "week_all_reset": reset("week_all"),
            "week_sonnet_pct": pct("week_sonnet"),
            "week_sonnet_reset": reset("week_sonnet"),
            "source": str(source)}


def insert_limits(conn, snap: dict) -> bool:
    """스냅샷 한 건 삽입. **직전 스냅샷과 값(ts·source 제외)이 전부 같으면 skip**
    (False) — 주기 프로브가 같은 값을 반복 측정해도 DB 가 부풀지 않게, '값이 바뀐
    순간'만 이력에 남긴다. 실패도 조용히 False(본 흐름 비차단, insert 와 동일 계약)."""
    try:
        last = last_limits(conn)
        if last is not None and all(
                last.get(c) == snap.get(c) for c in _LIMITS_VAL_COLS):
            return False
        conn.execute(
            "INSERT INTO limits (ts,account,session_pct,session_reset,"
            "week_all_pct,week_all_reset,week_sonnet_pct,week_sonnet_reset,"
            "source) VALUES (?,?,?,?,?,?,?,?,?)",
            (float(snap.get("ts", 0.0)), snap.get("account"),
             snap.get("session_pct"), snap.get("session_reset"),
             snap.get("week_all_pct"), snap.get("week_all_reset"),
             snap.get("week_sonnet_pct"), snap.get("week_sonnet_reset"),
             snap.get("source") or "probe"))
        conn.commit()
        return True
    except sqlite3.Error:
        return False


def _row_to_limits(row) -> dict:
    return {"ts": row["ts"], "account": row["account"],
            "session_pct": row["session_pct"],
            "session_reset": row["session_reset"],
            "week_all_pct": row["week_all_pct"],
            "week_all_reset": row["week_all_reset"],
            "week_sonnet_pct": row["week_sonnet_pct"],
            "week_sonnet_reset": row["week_sonnet_reset"],
            "source": row["source"]}


def last_limits(conn):
    """최신 스냅샷 dict|None (게이트·표시의 신선도 판단은 ts 로)."""
    row = conn.execute(
        "SELECT * FROM limits ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
    return _row_to_limits(row) if row is not None else None


def query_limits(conn, since_ts: float | None = None,
                 limit: int | None = None) -> list:
    """스냅샷을 ts 오름차순으로 반환. since_ts 이후만/최근 limit 건만 옵션."""
    if limit is not None and limit >= 0:
        # 서브쿼리 밖에선 rowid 가 안 보이므로 별칭(rid)으로 끌고 나와 정렬한다.
        cur = conn.execute(
            "SELECT * FROM (SELECT *, rowid AS rid FROM limits WHERE ts >= ? "
            "ORDER BY ts DESC, rowid DESC LIMIT ?) ORDER BY ts ASC, rid ASC",
            (float(since_ts) if since_ts is not None else 0.0, limit))
    else:
        cur = conn.execute(
            "SELECT * FROM limits WHERE ts >= ? ORDER BY ts ASC, rowid ASC",
            (float(since_ts) if since_ts is not None else 0.0,))
    return [_row_to_limits(r) for r in cur.fetchall()]


def prune_limits(conn, before_ts: float) -> int:
    """before_ts 이전 스냅샷 삭제. 자동 호출 없음(보존 무제한 결정) — 수동 정책용."""
    cur = conn.execute("DELETE FROM limits WHERE ts < ?", (float(before_ts),))
    conn.commit()
    return cur.rowcount


def limits_count(conn) -> int:
    """스냅샷 총수(테스트용)."""
    return int(conn.execute("SELECT COUNT(*) AS n FROM limits").fetchone()["n"])
