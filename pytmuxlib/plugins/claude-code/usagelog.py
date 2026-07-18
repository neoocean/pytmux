"""Claude Code 토큰 사용량 영속 로깅 + 집계(docs/internal/HANDOFF.md §10 #7).

`tokens.py` 가 응답별 peak 를 누계에 **확정**(committed>0)하는 그 이벤트를 한 건의
로그 레코드로 적는다(중복 없이 정확히 1응답=1레코드). 레코드는 JSONL 한 줄:

  {"ts": 1717500000.0, "tab": 0, "pane": 3, "session": 7,
   "account": "wo…@woojinkim.org", "tokens": 4200}

여기 함수들은 **순수**(파일 IO 한 함수 제외)라 서버/클라/테스트 어디서나 부른다.
서버는 append 만, 클라이언트 조회 화면은 read+aggregate 로 시간/일/월 × 계정 집계.

조회 화면은 큰 로그를 다룰 수 있으니 read(limit=) 로 최근 N 줄만 증분 읽기 가능.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time as _t

UNKNOWN = "unknown"

# 집계 버킷 → strftime 포맷. "hour"=시간, "day"=일, "week"=주, "month"=월.
# 주는 ISO-8601 주차(%G=ISO 연도, %V=ISO 주차, 월요일 시작) → "2023-W46".
_BUCKET_FMT = {
    "hour": "%Y-%m-%d %H:00",
    "day": "%Y-%m-%d",
    "week": "%G-W%V",
    "month": "%Y-%m",
}


def is_trusted(account, keep_accounts, keep_domains) -> bool:
    """account 가 신뢰 허용목록에 들면 True. None/빈 값/unknown 은 신뢰 아님.

    keep_accounts: 정확히 일치해야 하는 계정 별칭 집합.
    keep_domains: 별칭이 `@<domain>` 으로 끝나면 신뢰(서브도메인 아님, 정확 도메인).
    계정 정정(오검출 이메일 → unknown)의 단일 신뢰 규칙 — migrate 도구와 DB
    update_accounts 가 공유한다(docs/internal/TOKEN_USAGE_STORAGE_DESIGN.md §2.4)."""
    if not account or account == UNKNOWN:
        return False
    if account in keep_accounts:
        return True
    for d in keep_domains:
        if account.endswith("@" + d):
            return True
    return False


def remap_account(account, keep_accounts, keep_domains) -> str:
    """신뢰 계정이면 그대로, 아니면 UNKNOWN."""
    return account if is_trusted(account, keep_accounts, keep_domains) else UNKNOWN


def _tzoff_at(ts: float):
    """ts 시점의 로컬 UTC 오프셋(초). **그 시점 기준**이라 이후 DST/여행으로 시스템
    tz 가 바뀌어도 불변 → §3.5① 의 과거기록 재분류 방지에 쓴다. 플랫폼이 오프셋을
    못 주면(struct_time.tm_gmtoff None) None(레코드에 안 실어 시스템 로컬 폴백)."""
    off = _t.localtime(ts).tm_gmtoff
    return int(off) if off is not None else None


def make_record(ts: float, tab, pane: int, session: int,
                account: str | None, tokens: int,
                model: str | None = None) -> dict:
    """로그 레코드 한 건을 만든다(append 직전 서버가 호출).

    §3.5①: 쓰기 시점의 로컬 UTC 오프셋 `tzoff`(초)를 함께 적재한다 — hour 버킷이
    이후 DST/여행 후에도 재분류되지 않게(bucket_key 가 이 offset 으로 벽시계 복원).
    오프셋을 못 구하면 키를 생략(레거시처럼 시스템 로컬 폴백).

    v6(과티어): `model`(적재 시점 활성 모델 배지, claude_model 파싱값)을 주면 함께
    싣는다 — 토큰 지출의 모델 귀속. None(미상)이면 키를 생략해 기존 레코드와 동일한
    dict 를 만든다(하위호환: model 없이 부르던 호출·테스트의 결과 불변)."""
    rec = {"ts": float(ts), "tab": tab, "pane": int(pane),
           "session": int(session), "account": account or UNKNOWN,
           "tokens": int(tokens)}
    off = _tzoff_at(ts)
    if off is not None:
        rec["tzoff"] = off
    if model:
        rec["model"] = model
    return rec


def append(path: str, record: dict) -> bool:
    """레코드 한 줄을 JSONL 로 append. 실패해도 조용히 False(로깅이 본 흐름을 막지 않음)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        # S6: 0600 으로 append — 계정 alias 가 담긴 레거시 JSONL 이 공유 호스트에서
        # umask(흔히 0644)로 잠깐/영구 group/other-readable 이 되지 않게(F5 동일 정책).
        from pytmuxlib import ipc
        with ipc.open_private(path, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except OSError:
        return False


def read(path: str, limit: int | None = None) -> list:
    """JSONL 로그를 레코드 리스트로 읽는다(깨진 줄은 건너뜀). limit=N 이면 최근 N 줄만."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    if limit is not None and limit >= 0:
        lines = lines[-limit:]
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except ValueError:
            continue
        if isinstance(obj, dict) and "tokens" in obj:
            out.append(obj)
    return out


def bucket_key(ts: float, bucket: str, tzoff=None) -> str:
    """타임스탬프를 버킷 키 문자열로.

    tzoff(초)가 주어지면 **쓰기 시점의 로컬 벽시계**(UTC epoch + 저장 offset)로 키를
    만든다 → 이후 DST/여행으로 시스템 tz 가 바뀌어도 과거 기록이 다른 시/일 버킷으로
    재분류되지 않는다(§3.5①). None(레거시 레코드·offset 미상)이면 종전대로 **시스템
    로컬**(`fromtimestamp`)을 쓴다. day/week/month 합성 레코드(daily_to_records)는
    tzoff 를 안 실으므로 '현재 tz 관점' 표시가 유지된다(설계 의도 — 그 경로는 정오
    anchor 라 DST 흔들림도 없음)."""
    fmt = _BUCKET_FMT.get(bucket, _BUCKET_FMT["day"])
    if tzoff is not None:
        local = _dt.datetime.utcfromtimestamp(ts) + _dt.timedelta(seconds=tzoff)
        return local.strftime(fmt)
    return _dt.datetime.fromtimestamp(ts).strftime(fmt)


def daily_to_records(daily) -> list:
    """서버 usagedb.daily_breakdown(일자별 합성 레코드)을 aggregate/agg_view 가 먹는
    레코드 형태로 변환한다 — 팝업의 day/week/month 버킷을 **이력 전체**로 집계해 옛
    버킷이 레코드 cap 에 잘리지 않게 하는 입력이다.

    각 행의 ts 는 그 일자의 **로컬 정오 epoch** 로 둔다: bucket_key 가 day(%Y-%m-%d)·
    week(%G-W%V)·month(%Y-%m) 키를 그 달력일에 정확히 떨어뜨리고(정오라 자정 근처
    DST/주경계 흔들림이 없다), 같은 날의 raw 레코드와 동일한 버킷 키를 만든다. 주/월
    키는 여기(파이썬 strftime)서 파생하므로 SQLite 의 %G-W%V(3.46+) 의존을 피한다.
    hour 버킷엔 쓰지 않는다(일자 합성으론 시간을 복원 못 함 — 호출부가 raw 사용).
    일자 파싱이 깨진 행은 건너뛴다."""
    out = []
    for d in daily or []:
        try:
            ts = _dt.datetime.strptime(d["day"], "%Y-%m-%d").replace(
                hour=12).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        out.append({"ts": ts, "tab": d.get("tab"), "pane": d.get("pane") or 0,
                    "session": d.get("session"),
                    "account": d.get("account") or UNKNOWN,
                    "tokens": int(d.get("tokens", 0))})
    return out


def window_sum(records: list, since_ts: float, until_ts: float | None = None,
               account: str | None = None) -> int:
    """창 구간 (since_ts, until_ts] 의 토큰 합(추정 Σ). until_ts=None 이면 끝 무제한.
    account 가 주어지면 그 계정만. 경계 규약은 (ts > since,
    ts <= until) — 토큰 팝업이 실측 리셋 시각으로 역산한 현재 5h/주간 창의 스크랩
    추정 합을 보일 때 쓴다(claude.parse_reset_ts 와 짝)."""
    total = 0
    for r in records:
        ts = r.get("ts", 0.0)
        if ts <= since_ts or (until_ts is not None and ts > until_ts):
            continue
        if account is not None and (r.get("account") or UNKNOWN) != account:
            continue
        total += int(r.get("tokens", 0))
    return total


def fold_target(accounts):
    """계정 키 모음에서 **식별 계정(이메일 — '@' 포함)이 정확히 하나**면 그 계정을
    반환, 둘 이상이거나 없으면 None(귀속 불가).

    §5.5 단일 계정 귀속(2026-06-12 표시층 확장): 패널 화면엔 계정 라벨이 거의 안 떠
    (라벨은 /status 에만) 레코드 대부분이 미식별(unknown)로 적재되는데, 식별 계정이
    사실상 하나인 환경에선 미식별=그 계정 활동이다 —
    서버 _account_token_total 의 단일계정 전합산과 동일한 가정. 식별 계정이 둘
    이상이면 귀속이 모호하므로 접지 않는다(unknown 유지). v4 정정 이후 식별 계정은
    항상 이메일 형태('@' 포함)라 '@' 가 식별/미식별 판별식이다."""
    idd = {a for a in accounts if a and a != UNKNOWN and "@" in a}
    if len(idd) == 1:
        return next(iter(idd))
    return None


def fold_unknown(records: list, target) -> list:
    """미식별(unknown/계정 없음) 레코드를 target 계정으로 재라벨한 **새 목록**을
    반환한다(원본 레코드 불변 — 재라벨되는 행만 얕은 복사). target 이 거짓이면
    원본 그대로. fold_target 과 짝으로 쓴다(표시층 귀속 — DB 는 건드리지 않는다)."""
    if not target:
        return records
    out = []
    for r in records:
        if (r.get("account") or UNKNOWN) == UNKNOWN:
            r = dict(r, account=target)
        out.append(r)
    return out


def group_key(r: dict, dim: str = "account") -> str:
    """레코드의 그룹 차원 키(라벨). dim="account"=계정, dim="session"=세션 기준.

    세션은 닫히고 재사용되는 패널 id 대신 안정적인 claude 세션 id 로 묶는다
    (설계 §8 — 사용자 결정 '세션 기준 묶기')."""
    if dim == "session":
        sid = r.get("session")
        return f"세션 {sid}" if sid is not None else "세션 ?"
    return r.get("account") or UNKNOWN


def model_tier(r: dict) -> str:
    """레코드의 모델 티어(계열) — 'opus-4.8'→'opus'. model 미상(None)은 'unknown'.
    토큰 사용량을 모델별로 색 분해(막대 그래프)할 때의 그룹 키(요청 2026-06-21)."""
    m = r.get("model")
    return m.split("-", 1)[0] if m else "unknown"


def _merge_tiers(dicts) -> dict:
    """{tier: tok} 여러 개를 하나로 합산(상위 N 접힘 '기타' 행의 모델 분해용)."""
    out: dict = {}
    for d in dicts:
        for t, v in d.items():
            out[t] = out.get(t, 0) + v
    return out


def aggregate(records: list, bucket: str = "day",
              account: str | None = None, dim: str = "account") -> dict:
    """레코드를 (버킷 × 그룹차원)으로 합산.

    account 가 주어지면 그 계정만 필터(dim 과 무관하게 항상 계정 필터). dim 은
    그룹 차원("account"=계정, "session"=세션). 반환:
      {"buckets": {bucket_key: {group: tokens}},
       "groups": {group: tokens},         # 그룹별 총합(많이 쓴 순 정렬은 표시 측)
       "accounts": {group: tokens},       # 하위호환 별칭(=groups)
       "total": int}                      # 전체 총합
    """
    buckets: dict = {}
    groups: dict = {}
    total = 0
    for r in records:
        acct = r.get("account") or UNKNOWN
        if account is not None and acct != account:
            continue
        g = group_key(r, dim)
        tok = int(r.get("tokens", 0))
        # §3.5①: 레코드가 쓰기 시점 tzoff 를 들고 있으면 그 벽시계로 버킷(재분류 방지).
        bk = bucket_key(r.get("ts", 0.0), bucket, r.get("tzoff"))
        buckets.setdefault(bk, {})
        buckets[bk][g] = buckets[bk].get(g, 0) + tok
        groups[g] = groups.get(g, 0) + tok
        total += tok
    # "accounts" 는 하위호환 별칭(기존 호출부가 agg["accounts"] 를 읽음).
    return {"buckets": buckets, "groups": groups, "accounts": groups,
            "total": total}


def _fmt_tokens(total: int) -> str:
    """누계를 짧게 표기(tokens.fmt 와 동일 규칙, 의존 줄이려 여기 둠)."""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M".replace(".0M", "M")
    if total >= 1_000:
        return f"{total / 1_000:.1f}k".replace(".0k", "k")
    return str(total)


# 막대 게이지(bar)는 순수 표시 헬퍼라 S5b 에서 clientutil.bar 로 이전했다(코어
# clientscreens 가 데이터 모듈 usagelog 를 import 하지 않게). 표시가 필요한 곳은
# clientutil.bar 를 직접 쓴다.


def _bucket_short(bk: str, bucket: str, weekdays=None, hour_suffix="시") -> str:
    """버킷 키를 짧은 표시 라벨로(연도 등 반복 정보 제거). day=MM-DD(+요일),
    month=YYYY-MM, week=W##(연도는 헤더에 한 번), hour=MM-DD HH<접미사>.

    weekdays: 7개 요일 라벨 시퀀스(월요일부터 — datetime.weekday() 인덱스).
    주어지면 day 라벨에 'MM-DD(목)' 처럼 곁들인다(달력일을 알기 쉽게 — ccusage
    daily 뷰 참고). hour_suffix: 시각 라벨 접미사(ko '시' / en 'h') — i18n 라벨은
    표시층(screens)이 주입한다(이 모듈은 순수 유지 · weekdays 와 동일 원칙)."""
    if bucket == "day" and len(bk) == 10:          # YYYY-MM-DD → MM-DD(요일)
        lab = bk[5:]
        if weekdays:
            try:
                wd = _dt.datetime.strptime(bk, "%Y-%m-%d").weekday()
                lab += f"({weekdays[wd]})"
            except ValueError:
                pass
        return lab
    if bucket == "week" and "-W" in bk:            # YYYY-W## → W##
        return "W" + bk.split("-W", 1)[1]
    if bucket == "hour" and len(bk) >= 13:         # YYYY-MM-DD HH:00 → MM-DD HH<접미사>
        return bk[5:10] + " " + bk[11:13] + hour_suffix
    return bk                                       # month(YYYY-MM) 등은 그대로


def _session_tabpane(records: list) -> dict:
    """세션 id → 대표 '탭T:pP' 라벨(그 세션 레코드의 최빈 tab:pane). 표시 1-based 탭."""
    by: dict = {}
    for r in records:
        sid = r.get("session")
        if sid is None:
            continue
        key = (r.get("tab"), r.get("pane"))
        by.setdefault(sid, {})
        by[sid][key] = by[sid].get(key, 0) + 1
    out = {}
    for sid, counts in by.items():
        (tab, pane), _ = max(counts.items(), key=lambda kv: kv[1])
        tlabel = (tab + 1) if isinstance(tab, int) else "?"
        out[sid] = f"탭{tlabel}:p{pane}"
    return out


def _session_start_ts(records: list) -> dict:
    """group_key('session') 라벨('세션 N') → 그 세션 **첫(최소 ts)** 레코드의 원시
    epoch 시각. 세션 뷰 시간 내림차순 정렬용 — _session_time 의 포맷 라벨('06-20')은
    월·연 경계에서 문자열 비교가 어긋나므로 정렬엔 원시 ts 를 쓴다(요청 2026-06-22)."""
    out: dict = {}
    for r in records:
        sid = r.get("session")
        ts = r.get("ts")
        if sid is None or ts is None:
            continue
        g = group_key(r, "session")
        if g not in out or ts < out[g]:
            out[g] = ts
    return out


def _session_time(records: list, bucket: str = "hour") -> dict:
    """세션 id → 대표 시각 라벨. 세션의 **첫(최소 ts)** 레코드 시각으로, 이름 없는 세션을
    시간으로 식별하게 한다(사용자 요청 2026-06-20 — 세션 번호만으로는 어느 세션이
    언제·얼마나 썼는지 알기 어렵다). 저장된 tzoff(쓰기 시점 로컬 오프셋)가 있으면 그
    벽시계로, 없으면 시스템 로컬로 포맷한다(bucket_key 와 동일 규약 → 과거기록 재분류
    방지). hour 버킷은 실 ts 라 '월-일 시:분'까지, day/week/month 는 일자 합성 레코드
    (ts=로컬 정오 고정)라 시:분이 무의미하므로 '월-일'만 보인다."""
    fmt = "%m-%d %H:%M" if bucket == "hour" else "%m-%d"
    first: dict = {}
    for r in records:
        sid = r.get("session")
        if sid is None:
            continue
        ts = r.get("ts")
        if ts is None:
            continue
        prev = first.get(sid)
        if prev is None or ts < prev[0]:
            first[sid] = (ts, r.get("tzoff"))
    out = {}
    for sid, (ts, tzoff) in first.items():
        if tzoff is not None:
            local = _dt.datetime.utcfromtimestamp(ts) + _dt.timedelta(seconds=tzoff)
            out[sid] = local.strftime(fmt)
        else:
            out[sid] = _dt.datetime.fromtimestamp(ts).strftime(fmt)
    return out


def session_time_labels(records: list) -> dict:
    """세션 id → '월-일 시:분' 라벨(원시 레코드 실 ts 기준). 세션 뷰 타임스탬프 열이
    일/주/월 버킷에서도 실제 세션 시작 '시:분'을 보이게 한다(사용자 요청 2026-07-01):
    그 버킷들의 집계 src 는 일자 합성 레코드(ts=로컬 정오 고정)라 시각을 잃으므로,
    타임스탬프 열만은 항상 raw _records 의 실 ts 에서 '월-일 시:분'까지 뽑는다.
    raw 창(최근 N)에 없는 옛 세션은 여기에 안 담기고, 표시 측이 집계 gtimes(날짜만)로
    폴백한다. hour 버킷 규약(_session_time)과 동일 포맷."""
    return _session_time(records, "hour")


def agg_view(records: list, bucket: str = "day", account: str | None = None,
             dim: str = "account", order: str = "time",
             top: int | None = None, weekdays=None, hour_suffix="시",
             group_order: str = "tokens", time_records=None) -> dict:
    """표시(DataTable) 전용 집계 — 정렬·라벨·비율까지 계산해 렌더가 바로 쓰게 한다.

    반환:
      {"total": int,
       "groups": [(label, tokens, share_pct)],   # 묶음(계정/세션) 총합, 토큰 많은 순
       "buckets": [(label, tokens, share_pct)],   # 시간축, order("time"|"tokens")
       "multi": bool,                              # 그룹 2개 이상(=중복 분해 가치 있음)
       "gmax": int, "bmax": int}                   # 막대 기준(각 목록의 최대 토큰)
    세션 차원은 라벨에 대표 탭:패널과 시작 시각을 곁들인다('세션 4 (탭2:p3 · 06-20
    16:03)') — 이름 없는 세션을 시간으로 식별하게(2026-06-20). top 이 주어지고
    그룹이 그보다 많으면 상위 top 만 남기고 나머지는 '기타 N개' 한 줄로 접는다
    (침묵 절단이 아니라 접힘을 명시 — 설계 §4)."""
    agg = aggregate(records, bucket, account, dim)
    total = agg["total"]
    tp = _session_tabpane(records) if dim == "session" else {}
    # 세션엔 이름이 없어 번호만으론 식별이 어렵다 → 대표 탭:패널에 더해 시작 시각을
    # 곁들인다(사용자 요청 2026-06-20). 시작 시각은 라벨에 섞지 않고 별도 'gtimes'
    # 열로 내보내, 표시 측이 '세션 | 타임스탬프 | 토큰' 3열로 나눌 수 있게 한다
    # (사용자 요청 2026-06-20). 라벨엔 대표 탭:패널만('세션 12 (탭1:p1)').
    ts_lbl = _session_time(records, bucket) if dim == "session" else {}

    def _sid_of(g):
        try:
            return int(g.split(" ", 1)[1])
        except (ValueError, IndexError):
            return None

    def glabel(g):
        if dim == "session" and g.startswith("세션 "):
            tplab = tp.get(_sid_of(g))
            return f"{g} ({tplab})" if tplab else g
        return g

    def gtime(g):
        if dim == "session" and g.startswith("세션 "):
            return ts_lbl.get(_sid_of(g), "") or ""
        return ""

    def pct(tok):
        return round(tok / total * 100) if total else 0

    # 모델 티어 분해(요청 2026-06-21): 막대를 모델 구성비로 색 분할하려 그룹/버킷마다
    # {tier: tok} 를 곁들인다. aggregate 가 안 들고 오므로 여기서 한 번 더 훑는다
    # (레코드 수는 최근 N 으로 제한적). 계정 필터는 aggregate 와 동일.
    gmodels_map: dict = {}
    bmodels_map: dict = {}
    for r in records:
        acct = r.get("account") or UNKNOWN
        if account is not None and acct != account:
            continue
        tok = int(r.get("tokens", 0))
        if tok <= 0:
            continue
        tier = model_tier(r)
        g = group_key(r, dim)
        gmodels_map.setdefault(g, {})
        gmodels_map[g][tier] = gmodels_map[g].get(tier, 0) + tok
        bk = bucket_key(r.get("ts", 0.0), bucket, r.get("tzoff"))
        bmodels_map.setdefault(bk, {})
        bmodels_map[bk][tier] = bmodels_map[bk].get(tier, 0) + tok

    # 그룹 정렬: 기본은 토큰 많은 순. 세션 뷰에서 group_order=="time" 이면 **시작 시각
    # 내림차순**(최신 세션이 맨 위, 요청 2026-06-22) — 정렬엔 원시 ts(_session_start_ts)를
    # 쓰고, 없는 그룹은 맨 뒤(0)로 둔다.
    # day/week/month 버킷의 집계 src(`records`)는 일자 합성 레코드(ts=로컬 정오 고정)라
    # 시각을 잃어 하루 안의 세션이 전부 같은 ts→'날짜 단위'로만 정렬됐다(버그, 2026-07-01).
    # time_records(raw _records, 실 ts)가 주어지면 그걸로 시작 시각을 덮어 하루 안에서도
    # 실제 '시:분' 내림차순 정렬한다. raw 창에 없는 옛 세션은 합성 ts(정오)로 폴백(날짜순 유지).
    if dim == "session" and group_order == "time":
        gstart = _session_start_ts(records)
        if time_records is not None:
            gstart = {**gstart, **_session_start_ts(time_records)}
        groups = sorted(agg["groups"].items(),
                        key=lambda kv: -gstart.get(kv[0], 0.0))
    else:
        groups = sorted(agg["groups"].items(), key=lambda kv: -kv[1])
    grows = [(glabel(g), t, pct(t)) for g, t in groups]
    gtimes = [gtime(g) for g, _ in groups]
    gmodels = [gmodels_map.get(g, {}) for g, _ in groups]
    if top is not None and len(grows) > top:
        rest = grows[top:]
        rest_tok = sum(t for _, t, _ in rest)
        grows = grows[:top] + [(f"기타 {len(rest)}개", rest_tok, pct(rest_tok))]
        gtimes = gtimes[:top] + [""]   # '기타 N개' 접힘 행엔 시작 시각 없음
        gmodels = gmodels[:top] + [_merge_tiers(gmodels[top:])]
    bucket_tot = {bk: sum(per.values()) for bk, per in agg["buckets"].items()}
    if order == "tokens":
        bkeys = sorted(bucket_tot, key=lambda k: -bucket_tot[k])
    else:
        bkeys = sorted(bucket_tot, reverse=True)        # 시간 내림차순(최근 위)
    brows = [(_bucket_short(bk, bucket, weekdays, hour_suffix), bucket_tot[bk],
              pct(bucket_tot[bk]))
             for bk in bkeys]
    bmodels = [bmodels_map.get(bk, {}) for bk in bkeys]
    return {"total": total, "groups": grows, "buckets": brows,
            "gtimes": gtimes,          # grows 와 같은 순서의 세션 시작 시각(세션 뷰 전용, 그 외 "")
            "gmodels": gmodels,        # grows 와 같은 순서의 모델 티어 분해 {tier: tok}
            "bmodels": bmodels,        # brows 와 같은 순서의 모델 티어 분해 {tier: tok}
            "bkeys": bkeys,            # brows 와 같은 순서의 원시 버킷 키(일자 5h% 조인용)
            "multi": len(grows) > 1,
            "gmax": max((t for _, t, _ in grows), default=0),
            "bmax": max((t for _, t, _ in brows), default=0)}


def agg_index(records: list, bucket: str = "day", account: str | None = None,
              weekdays=None, hour_suffix="시") -> dict:
    """버킷 키 → {"tokens", "label", "models"} 인덱스(정렬·접힘 없는 순수 맵).

    계층 타임라인 뷰(요청 2026-06-21)가 월/주/일/시각 입도를 각각 인덱싱해 트리 행을
    O(1) 로 조립할 때 쓴다 — agg_view 는 정렬된 *목록* 을 주지만 트리는 키로 직접
    조회해야 한다(월→주→일→시각 멤버십). label 은 _bucket_short 와 동일 규칙,
    models 는 막대 색 분할용 모델 티어 분해({tier: tok})."""
    agg = aggregate(records, bucket, account, "account")
    bmodels: dict = {}
    for r in records:
        acct = r.get("account") or UNKNOWN
        if account is not None and acct != account:
            continue
        tok = int(r.get("tokens", 0))
        if tok <= 0:
            continue
        bk = bucket_key(r.get("ts", 0.0), bucket, r.get("tzoff"))
        bmodels.setdefault(bk, {})
        tier = model_tier(r)
        bmodels[bk][tier] = bmodels[bk].get(tier, 0) + tok
    out: dict = {}
    for bk, per in agg["buckets"].items():
        out[bk] = {"tokens": sum(per.values()),
                   "label": _bucket_short(bk, bucket, weekdays, hour_suffix),
                   "models": bmodels.get(bk, {})}
    return out


def summary_lines(records: list, bucket: str = "day",
                  account: str | None = None, dim: str = "account") -> list:
    """조회 화면(InfoScreen)용 사람이 읽는 집계 줄 목록을 만든다.

    버킷별로 그룹(계정 또는 세션) 합계를 보이고, 맨 위에 그룹 총합·전체 총합
    헤더를 둔다. dim="session" 이면 세션 기준으로 묶는다([패널] 탭). 레코드가
    없으면 안내 한 줄."""
    agg = aggregate(records, bucket, account, dim)
    if not records or agg["total"] == 0:
        return ["(기록된 토큰 사용량이 없습니다)"]
    lines = []
    scope = f" · 계정={account}" if account else ""
    label = "세션" if dim == "session" else "계정"
    lines.append(f"토큰 사용량 — 단위:{bucket} · {label}별{scope}"
                 f"  전체 Σ{_fmt_tokens(agg['total'])}")
    # 그룹별 총합(많이 쓴 순)
    for g, tok in sorted(agg["groups"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  [{g}] Σ{_fmt_tokens(tok)}")
    lines.append("")
    # 버킷별(최근이 위로) × 그룹
    for bk in sorted(agg["buckets"], reverse=True):
        per = agg["buckets"][bk]
        parts = "  ".join(f"{a}:{_fmt_tokens(t)}"
                          for a, t in sorted(per.items(), key=lambda kv: -kv[1]))
        bucket_total = sum(per.values())
        lines.append(f"{bk}  Σ{_fmt_tokens(bucket_total)}   {parts}")
    return lines


# 모델 티어 쌓는 순서(바닥→위). _model_cell_sequence 가 막대 칸을 모델 토큰 점유로
# 배분할 때 이 순서로 채운다 — 어느 막대든 같은 모델이 같은 자리에 와서 색 띠가
# 가지런해진다(요청 2026-06-21). 표시 색은 위젯(screens) 몫.
_MODEL_TIER_ORDER = ("haiku", "sonnet", "opus", "fable")


def _model_cell_sequence(models: dict, n: int) -> list:
    """막대의 채워진 칸 수 n 을 모델 토큰 점유비로 배분 — 바닥→위 순 모델명 리스트
    (길이 n). _MODEL_TIER_ORDER 먼저, 그 밖의 티어(unknown 등)는 뒤(위쪽)에. 칸 배정은
    최대잔여법(소수부 큰 티어부터 +1)으로 합이 정확히 n 이 되게 한다. 토큰이 없으면
    전부 None(위젯이 폴백 색으로 칠함). 순수 함수 — 헤드리스 테스트 대상."""
    if n <= 0:
        return []
    total = sum(v for v in models.values() if v > 0)
    if total <= 0:
        # 모델 토큰이 전혀 없는 구간(스크랩 미귀속) — '?'(unknown) 티어로 채운다.
        # 종전엔 None 을 돌려 위젯이 임계색(초록/노랑/빨강) 폴백으로 칠해, 그 초록이
        # 마치 다른 모델(haiku 색)인 듯 보이는데 범례엔 안 떠 혼란스러웠다(제보
        # 2026-06-22). 이제 unknown 회색으로 칠하고 범례에 '?'로 함께 표시한다.
        return ["unknown"] * n
    tiers = [t for t in _MODEL_TIER_ORDER if models.get(t, 0) > 0]
    tiers += [t for t in models
              if t not in _MODEL_TIER_ORDER and models[t] > 0]
    raw = [(t, models[t] / total * n) for t in tiers]
    counts = {t: int(x) for t, x in raw}
    rem = n - sum(counts.values())
    for t, _x in sorted(raw, key=lambda p: -(p[1] - int(p[1])))[:max(0, rem)]:
        counts[t] += 1
    seq: list = []
    for t in tiers:
        seq += [t] * counts[t]
    if len(seq) < n:                       # 라운딩 여파 보정
        seq += ["unknown"] * (n - len(seq))   # 라운딩 여파도 '?'로(임계색 폴백 제거)
    return seq[:n]
