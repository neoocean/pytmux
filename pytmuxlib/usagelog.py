"""Claude Code 토큰 사용량 영속 로깅 + 집계(docs/HANDOFF.md §10 #7).

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

UNKNOWN = "unknown"

# 집계 버킷 → strftime 포맷. "hour"=시간, "day"=일, "month"=월.
_BUCKET_FMT = {
    "hour": "%Y-%m-%d %H:00",
    "day": "%Y-%m-%d",
    "month": "%Y-%m",
}


def make_record(ts: float, tab, pane: int, session: int,
                account: str | None, tokens: int) -> dict:
    """로그 레코드 한 건을 만든다(append 직전 서버가 호출)."""
    return {"ts": float(ts), "tab": tab, "pane": int(pane),
            "session": int(session), "account": account or UNKNOWN,
            "tokens": int(tokens)}


def append(path: str, record: dict) -> bool:
    """레코드 한 줄을 JSONL 로 append. 실패해도 조용히 False(로깅이 본 흐름을 막지 않음)."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
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


def bucket_key(ts: float, bucket: str) -> str:
    """타임스탬프를 버킷 키 문자열로(로컬 시간 기준)."""
    fmt = _BUCKET_FMT.get(bucket, _BUCKET_FMT["day"])
    return _dt.datetime.fromtimestamp(ts).strftime(fmt)


def aggregate(records: list, bucket: str = "day",
              account: str | None = None) -> dict:
    """레코드를 (버킷 × 계정)으로 합산.

    account 가 주어지면 그 계정만 필터. 반환:
      {"buckets": {bucket_key: {acct: tokens}},
       "accounts": {acct: tokens},        # 계정별 총합
       "total": int}                      # 전체 총합
    """
    buckets: dict = {}
    accounts: dict = {}
    total = 0
    for r in records:
        acct = r.get("account") or UNKNOWN
        if account is not None and acct != account:
            continue
        tok = int(r.get("tokens", 0))
        bk = bucket_key(r.get("ts", 0.0), bucket)
        buckets.setdefault(bk, {})
        buckets[bk][acct] = buckets[bk].get(acct, 0) + tok
        accounts[acct] = accounts.get(acct, 0) + tok
        total += tok
    return {"buckets": buckets, "accounts": accounts, "total": total}


def _fmt_tokens(total: int) -> str:
    """누계를 짧게 표기(tokens.fmt 와 동일 규칙, 의존 줄이려 여기 둠)."""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M".replace(".0M", "M")
    if total >= 1_000:
        return f"{total / 1_000:.1f}k".replace(".0k", "k")
    return str(total)


def summary_lines(records: list, bucket: str = "day",
                  account: str | None = None) -> list:
    """조회 화면(InfoScreen)용 사람이 읽는 집계 줄 목록을 만든다.

    버킷별로 계정 합계를 보이고, 맨 위에 계정 총합·전체 총합 헤더를 둔다.
    레코드가 없으면 안내 한 줄."""
    agg = aggregate(records, bucket, account)
    if not records or agg["total"] == 0:
        return ["(기록된 토큰 사용량이 없습니다)"]
    lines = []
    scope = f" · 계정={account}" if account else ""
    lines.append(f"토큰 사용량 — 단위:{bucket}{scope}  전체 Σ{_fmt_tokens(agg['total'])}")
    # 계정별 총합(많이 쓴 순)
    for acct, tok in sorted(agg["accounts"].items(),
                            key=lambda kv: -kv[1]):
        lines.append(f"  [{acct}] Σ{_fmt_tokens(tok)}")
    lines.append("")
    # 버킷별(최근이 위로) × 계정
    for bk in sorted(agg["buckets"], reverse=True):
        per = agg["buckets"][bk]
        parts = "  ".join(f"{a}:{_fmt_tokens(t)}"
                          for a, t in sorted(per.items(), key=lambda kv: -kv[1]))
        bucket_total = sum(per.values())
        lines.append(f"{bk}  Σ{_fmt_tokens(bucket_total)}   {parts}")
    return lines
