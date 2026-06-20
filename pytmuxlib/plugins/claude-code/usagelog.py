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
                account: str | None, tokens: int) -> dict:
    """로그 레코드 한 건을 만든다(append 직전 서버가 호출).

    §3.5①: 쓰기 시점의 로컬 UTC 오프셋 `tzoff`(초)를 함께 적재한다 — hour 버킷이
    이후 DST/여행 후에도 재분류되지 않게(bucket_key 가 이 offset 으로 벽시계 복원).
    오프셋을 못 구하면 키를 생략(레거시처럼 시스템 로컬 폴백)."""
    rec = {"ts": float(ts), "tab": tab, "pane": int(pane),
           "session": int(session), "account": account or UNKNOWN,
           "tokens": int(tokens)}
    off = _tzoff_at(ts)
    if off is not None:
        rec["tzoff"] = off
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
    account 가 주어지면 그 계정만. 경계 규약은 usagedb.reconcile 과 동일(ts > since,
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
    사실상 하나인 환경에선 미식별=그 계정 활동이다 — reconcile 의 같은-계정 합산,
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


def agg_view(records: list, bucket: str = "day", account: str | None = None,
             dim: str = "account", order: str = "time",
             top: int | None = None, weekdays=None, hour_suffix="시") -> dict:
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

    groups = sorted(agg["groups"].items(), key=lambda kv: -kv[1])
    grows = [(glabel(g), t, pct(t)) for g, t in groups]
    gtimes = [gtime(g) for g, _ in groups]
    if top is not None and len(grows) > top:
        rest = grows[top:]
        rest_tok = sum(t for _, t, _ in rest)
        grows = grows[:top] + [(f"기타 {len(rest)}개", rest_tok, pct(rest_tok))]
        gtimes = gtimes[:top] + [""]   # '기타 N개' 접힘 행엔 시작 시각 없음
    bucket_tot = {bk: sum(per.values()) for bk, per in agg["buckets"].items()}
    if order == "tokens":
        bkeys = sorted(bucket_tot, key=lambda k: -bucket_tot[k])
    else:
        bkeys = sorted(bucket_tot, reverse=True)        # 시간 내림차순(최근 위)
    brows = [(_bucket_short(bk, bucket, weekdays, hour_suffix), bucket_tot[bk],
              pct(bucket_tot[bk]))
             for bk in bkeys]
    return {"total": total, "groups": grows, "buckets": brows,
            "gtimes": gtimes,          # grows 와 같은 순서의 세션 시작 시각(세션 뷰 전용, 그 외 "")
            "bkeys": bkeys,            # brows 와 같은 순서의 원시 버킷 키(일자 5h% 조인용)
            "multi": len(grows) > 1,
            "gmax": max((t for _, t, _ in grows), default=0),
            "bmax": max((t for _, t, _ in brows), default=0)}


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


def recon_view(intervals: list) -> list:
    """대사(reconcile) 구간 → 표시용 행 (구간, 실측, 추정Σ, 비고) 튜플 목록 — S6 T2.

    usagedb.reconcile 의 dict 목록을 받아 TokenLogScreen [대사] 표가 그대로 꽂을
    수 있는 문자열 4튜플로 푼다(순수 함수 — 헤드리스 테스트 대상). 실측과 추정은
    의미가 다른 두 출처라 같은 행에서도 라벨/형식을 구분한다: 실측은 'p0%→p1%
    (Δ±n)', 추정은 '~Σ'. 리셋 구간(pct 감소)은 Δ 대신 '리셋' — 5h 창이 새로
    시작돼 Δpct 비교가 무의미하다. 계정 미상/혼합(account None)은 비고에 표시."""
    import time as _t
    rows = []
    for iv in intervals:
        span = "{}→{}".format(
            _t.strftime("%m-%d %H:%M", _t.localtime(iv["t0"])),
            _t.strftime("%H:%M" if _t.localtime(iv["t0"])[:3]
                        == _t.localtime(iv["t1"])[:3] else "%m-%d %H:%M",
                        _t.localtime(iv["t1"])))
        if iv.get("reset"):
            measured = f"{iv['pct0']}%→{iv['pct1']}% (리셋)"
        else:
            measured = f"{iv['pct0']}%→{iv['pct1']}% (Δ{iv['dpct']:+d})"
        est = "~" + _fmt_tokens(iv.get("tokens", 0))
        note = iv.get("account") or "계정혼합/미상"
        rows.append((span, measured, est, note))
    return rows


# 세로 막대의 부분 칸(1/8 단위) — 빈칸→꽉찬블록(아래에서 위로 채워짐).
_VBLOCKS = " ▁▂▃▄▅▆▇█"


def recon_chart(intervals: list, plot_w: int, plot_h: int,
                x_off: int = 0, step: int = 2) -> dict:
    """대사 구간을 **시간축 세로 막대 그래프**로 그리기 위한 순수 기하 계산(요청
    2026-06-20 — 표 대신 시간에 따른 사용률 증가를 한눈에). 각 구간 = 막대 1개,
    높이 = 그 구간 끝의 실측 세션 5h%(pct1, 0~100 고정 분모) — 5h 창 안에서 단조
    증가하다 리셋 때 0 부근으로 떨어지는 톱니가 그대로 보인다. 좌우 스크롤(x_off)로
    더 이전 구간을 본다.

    좌표/색은 표시층(위젯) 몫이라 여기선 글리프 격자와 열↔구간 매핑만 돌려준다
    (헤드리스 테스트 대상 — Strip/색 없이 모양만 검증).

    intervals: 시간순(옛→새) 대사 dict 목록(usagedb.reconcile).
    plot_w, plot_h: 막대 영역 칸수(좌측 눈금자·하단 축 제외).
    x_off: **새 쪽에서 왼쪽으로 스크롤한 구간 수**(0=최신이 오른쪽 끝). [0,max_off] 로 클램프.
    step: 구간당 칸수(막대 1칸 + 간격 step-1칸). ≥1.

    반환 dict:
      grid:    plot_h 줄(위→아래) 문자열, 각 plot_w 폭 — 막대는 ▁..█, 나머지 공백.
      col_iv:  길이 plot_w 의 [구간index|None] — 각 열이 그리는 구간(위젯이 pct/리셋으로
               색칠). 막대가 아닌 간격 열은 None.
      labels:  [(col, text)] x축 시각 라벨(막대 아래, 겹치지 않게 솎음).
      x_off:   실제 적용된(클램프된) 오프셋.
      max_off: 최대 스크롤 오프셋(n-capacity, ≥0).
      i0, i1:  보이는 첫/끝 구간 index(포함), 비면 (-1,-1).
      n:       전체 구간 수."""
    n = len(intervals)
    step = max(1, int(step))
    grid = [" " * plot_w for _ in range(plot_h)]
    col_iv = [None] * plot_w
    out = {"grid": grid, "col_iv": col_iv, "labels": [], "x_off": 0,
           "max_off": 0, "i0": -1, "i1": -1, "n": n}
    if n == 0 or plot_w <= 0 or plot_h <= 0:
        return out
    capacity = max(1, (plot_w + step - 1) // step)   # 화면에 들어가는 구간 수
    max_off = max(0, n - capacity)
    x_off = max(0, min(int(x_off), max_off))
    last = n - 1 - x_off                 # 보이는 가장 새(오른쪽) 구간
    first = max(0, last - capacity + 1)  # 보이는 가장 옛(왼쪽) 구간
    out.update(x_off=x_off, max_off=max_off, i0=first, i1=last)

    rows = [list(r) for r in grid]       # 가변 격자
    last_label_end = -1
    import time as _t
    prev_day = None
    for i in range(first, last + 1):
        iv = intervals[i]
        col = (i - first) * step         # 이 구간 막대의 x(왼쪽 정렬)
        if col >= plot_w:
            break
        col_iv[col] = i
        pct = iv.get("pct1", 0) or 0
        level = max(0.0, min(1.0, pct / 100.0)) * plot_h   # 채울 칸(부분 포함)
        for r in range(plot_h):
            from_bottom = plot_h - 1 - r                   # 0=맨 아래 줄
            if level >= from_bottom + 1:
                rows[r][col] = "█"
            elif level > from_bottom:
                idx = max(1, min(8, round((level - from_bottom) * 8)))
                rows[r][col] = _VBLOCKS[idx]
        # x축 라벨: 날짜 바뀌면 'MM-DD', 아니면 'HH:MM'. 겹침 방지로 솎음.
        lt = _t.localtime(iv.get("t1", 0))
        day = lt[:3]
        text = (_t.strftime("%m-%d", lt) if day != prev_day
                else _t.strftime("%H:%M", lt))
        prev_day = day
        if col > last_label_end:
            out["labels"].append((col, text))
            last_label_end = col + len(text)   # 다음 라벨은 이 뒤부터
    out["grid"] = ["".join(r) for r in rows]
    return out
