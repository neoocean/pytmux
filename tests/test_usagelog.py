"""토큰 사용량 영속 로그(usagelog) + 계정 추정(claude.claude_account) 단위 테스트."""
import os
import tempfile

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import usagelog
from pytmuxlib.claude import claude_account


async def test_append_and_read_roundtrip():
    path = tempfile.mktemp(suffix=".tokens.jsonl")
    try:
        assert usagelog.read(path) == [], "없는 파일은 빈 목록"
        usagelog.append(path, usagelog.make_record(
            ts=1_700_000_000.0, tab=0, pane=1, session=1,
            account="me@x.org", tokens=1900))
        usagelog.append(path, usagelog.make_record(
            ts=1_700_000_001.0, tab=1, pane=2, session=2,
            account=None, tokens=300))
        recs = usagelog.read(path)
        assert len(recs) == 2
        assert recs[0]["tokens"] == 1900 and recs[0]["account"] == "me@x.org"
        assert recs[1]["account"] == usagelog.UNKNOWN, "account None → unknown"
        # limit=1 은 최근 1줄만
        assert len(usagelog.read(path, limit=1)) == 1
        # 깨진 줄은 건너뜀
        with open(path, "a") as f:
            f.write("not json\n")
        assert len(usagelog.read(path)) == 2, "깨진 줄 무시"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


async def test_aggregate_buckets_and_accounts():
    recs = [
        usagelog.make_record(1_700_000_000.0, 0, 1, 1, "a@x.org", 1000),
        usagelog.make_record(1_700_000_100.0, 0, 1, 1, "a@x.org", 500),
        usagelog.make_record(1_700_086_400.0, 1, 2, 2, "b@y.org", 2000),
    ]
    agg = usagelog.aggregate(recs, "day")
    assert agg["total"] == 3500
    assert agg["accounts"]["a@x.org"] == 1500
    assert agg["accounts"]["b@y.org"] == 2000
    # 두 레코드는 같은 날 같은 계정 → 한 버킷에 합산
    day0 = usagelog.bucket_key(1_700_000_000.0, "day")
    assert agg["buckets"][day0]["a@x.org"] == 1500
    # 계정 필터
    only_a = usagelog.aggregate(recs, "day", account="a@x.org")
    assert only_a["total"] == 1500 and "b@y.org" not in only_a["accounts"]
    # 월 버킷은 모두 한 달로
    mo = usagelog.aggregate(recs, "month")
    assert len(mo["buckets"]) == 1
    # 주 버킷: 같은 ISO 주차의 레코드는 한 버킷으로 합산, 키는 "%G-W%V" 형식
    wk = usagelog.aggregate(recs, "week")
    assert len(wk["buckets"]) == 1
    wk0 = usagelog.bucket_key(1_700_000_000.0, "week")
    assert "-W" in wk0 and wk["buckets"][wk0]["a@x.org"] == 1500
    # 요약 줄 생성(헤더 + 계정별 + 버킷별)
    lines = usagelog.summary_lines(recs, "day")
    assert any("전체 Σ3.5k" in ln for ln in lines), lines
    assert usagelog.summary_lines([], "day") == ["(기록된 토큰 사용량이 없습니다)"]


async def test_claude_account_heuristics():
    # 이메일 → 별칭화(원문 미노출), 도메인은 유지
    a = claude_account("Logged in as woojin@woojinkim.org\n")
    assert a == "wo…@woojinkim.org", a
    # 짧은 로컬은 그대로
    assert claude_account("me@x.org") == "me@x.org"
    # 조직/팀명
    assert claude_account("Organization: Acme Corp") == "Acme Corp"
    # 플랜명
    assert claude_account("You are on the Max plan").startswith("max")
    # 단서 없음 → None
    assert claude_account("? for shortcuts") is None
    # 예약/플레이스홀더 도메인은 계정으로 잡지 않는다(본문 예시 이메일 오탐 차단 —
    # 상태줄 @us…@example.com 사용자 보고). 다른 단서 없으면 None.
    assert claude_account("Transcript: email user@example.com to confirm") is None
    assert claude_account("contact a@b.invalid or x@y.test") is None
    # 예시 이메일은 건너뛰되 실제 단서(조직)는 살린다
    assert claude_account("see admin@example.org\nOrganization: Acme") == "Acme"
    # 계정 맥락 줄의 이메일이 앞선 본문 이메일보다 우선(둘 다 비예약이어도)
    assert claude_account(
        "ref bob@contractor.net in notes\nLogin: woojin@woojinkim.org"
    ) == "wo…@woojinkim.org"
