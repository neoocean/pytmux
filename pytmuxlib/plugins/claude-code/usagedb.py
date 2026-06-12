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
# v2(S6 T1): limits 테이블(실측 /usage 스냅샷 이력) 추가 — CREATE IF NOT EXISTS 라
# v1 DB 도 connect() 시 자동 업그레이드된다(기존 usage 테이블 무접촉).
# v3(§5.5 2026-06-11): 데이터 정리 — tokens.step 잔상 가드(58236) **이전**에 쌓인
# 중복 커밋(같은 pane·session·tokens 가 60초 안에 연속 반복 — 하루치의 83% 사례)을
# 첫 건만 남기고 usage_dup_archive 로 격리(하드 삭제 아님). 스키마 자체는 v2 동일.
SCHEMA_VERSION = 3

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
    cur_v = int(conn.execute("PRAGMA user_version").fetchone()[0])
    new_v = SCHEMA_VERSION
    if cur_v < 3:
        try:
            _migrate_v3_dedup_residue(conn)
        except sqlite3.Error:
            new_v = min(cur_v, 2) or 2   # 실패 시 버전 유지 → 다음 connect 재시도
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
    cur = conn.execute(
        "SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS day, "
        "       account, session, tab, pane, "
        "       COALESCE(SUM(tokens), 0) AS tokens "
        "FROM usage GROUP BY day, account, session, tab, pane "
        "HAVING SUM(tokens) <> 0")
    return [{"day": r["day"], "account": r["account"], "session": r["session"],
             "tab": r["tab"], "pane": r["pane"], "tokens": int(r["tokens"])}
            for r in cur.fetchall()]


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
# 최신값(self._usage) 하나뿐이라 재시작 시 유실·추이 조회 불가·스크랩 누계와의 대사
# (reconcile) 검증이 불가능했다(docs/TOKEN_ACCOUNTING_ACCURACY_SCENARIO.md §0-5).
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


def reconcile(conn, limit: int | None = 20) -> list:
    """대사(reconcile) 구간 목록 — S6 T2(docs/TOKEN_ACCOUNTING_ACCURACY_SCENARIO.md §4).

    연속한 실측 스냅샷 쌍(세션 pct 가 있는 것만) 사이 구간마다, **실측 Δpct(세션
    5h)** 와 그 구간에 적힌 **스크랩 committed Σ** 를 나란히 돌려준다. 두 값은 의미가
    달라(점유 % vs streaming 추정) 절대 일치를 기대하지 않는다 — 목적은 스크랩 추정이
    상대 지표(활동량)로 쓸 만한지(상관)를 데이터로 판단하는 것(§0-3 강등의 근거).

    계정: 양 끝 스냅샷 계정이 같고 비어있지 않으면 그 계정의 스크랩만 합산(같은 계정
    한정 — 다른 계정 패널의 토큰이 섞여 비교가 무의미해지는 것 방지). 다르거나 미상
    이면 전체 합 + account=None(혼합 표시는 표시층 몫).
    미식별('unknown'/NULL) 레코드는 같은-계정 합산에 **포함**한다(2026-06-11 §5.5):
    패널 화면엔 계정 라벨이 거의 안 떠(라벨은 /status 에만) 레코드 대부분이
    미식별인데, 이를 빼면 같은 계정 활동이 Σ=0 으로 보인다 — 식별 계정이 사실상
    하나인 환경(§10-B 단일 계정 귀속과 같은 가정)에서 미식별=그 계정 활동으로 본다.

    reset: 실측 pct 가 감소한 구간(5h 창 리셋이 낀 것) — Δpct 비교가 무의미하므로
    표시층이 구분하도록 플래그만 단다. limit=N 이면 최근 N 구간."""
    snaps = [s for s in query_limits(conn) if s["session_pct"] is not None]
    out = []
    for a, b in zip(snaps, snaps[1:]):
        acct = (b["account"]
                if b["account"] and a["account"] == b["account"] else None)
        if acct:
            cur = conn.execute(
                "SELECT COALESCE(SUM(tokens),0) AS s FROM usage "
                "WHERE ts > ? AND ts <= ? AND (account = ? "
                "OR account IS NULL OR account = 'unknown')",
                (a["ts"], b["ts"], acct))
        else:
            cur = conn.execute(
                "SELECT COALESCE(SUM(tokens),0) AS s FROM usage "
                "WHERE ts > ? AND ts <= ?", (a["ts"], b["ts"]))
        out.append({"t0": a["ts"], "t1": b["ts"], "account": acct,
                    "pct0": int(a["session_pct"]), "pct1": int(b["session_pct"]),
                    "dpct": int(b["session_pct"]) - int(a["session_pct"]),
                    "tokens": int(cur.fetchone()["s"]),
                    "reset": b["session_pct"] < a["session_pct"]})
    if limit is not None and limit >= 0:
        out = out[-limit:]
    return out
