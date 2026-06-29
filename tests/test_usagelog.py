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


async def test_tzoff_recorded_and_bucket_stable_across_tz_change():
    """§3.5①: make_record 는 쓰기 시점 로컬 오프셋(tzoff)을 싣고, bucket_key 는 그
    오프셋의 **벽시계**로 버킷한다 → 이후 DST/여행으로 시스템 tz 가 바뀌어도 과거
    기록의 시/일 버킷이 재분류되지 않는다(머신 tz 와 무관한 결정 검증)."""
    # 2023-11-14 22:13:20 UTC
    ts = 1_700_000_000.0
    rec = usagelog.make_record(ts, 0, 1, 1, "me@x.org", 1000)
    assert "tzoff" in rec and isinstance(rec["tzoff"], int), rec
    # 저장 오프셋별 벽시계 버킷(머신 tz 와 무관 — 명시 offset).
    assert usagelog.bucket_key(ts, "hour", 0) == "2023-11-14 22:00"
    assert usagelog.bucket_key(ts, "hour", 9 * 3600) == "2023-11-15 07:00"
    assert usagelog.bucket_key(ts, "hour", -5 * 3600) == "2023-11-14 17:00"
    assert usagelog.bucket_key(ts, "day", 9 * 3600) == "2023-11-15"  # +9 → 다음날
    assert usagelog.bucket_key(ts, "day", -5 * 3600) == "2023-11-14"
    # tzoff=None(레거시)은 종전대로 시스템 로컬 폴백.
    assert usagelog.bucket_key(ts, "hour", None) == \
        usagelog.bucket_key(ts, "hour")
    # 같은 ts·다른 저장 offset 의 두 레코드는 aggregate 에서 **다른 hour 버킷**.
    r_kr = dict(usagelog.make_record(ts, 0, 1, 1, "me@x.org", 100), tzoff=9 * 3600)
    r_us = dict(usagelog.make_record(ts, 0, 1, 1, "me@x.org", 200), tzoff=-5 * 3600)
    agg = usagelog.aggregate([r_kr, r_us], "hour")
    assert agg["buckets"]["2023-11-15 07:00"]["me@x.org"] == 100
    assert agg["buckets"]["2023-11-14 17:00"]["me@x.org"] == 200
    # 저장 offset 이 그 시점 시스템 로컬과 같으면 시스템-로컬 경로와 동일 결과(불변).
    off = usagelog._tzoff_at(ts)
    if off is not None:
        assert usagelog.bucket_key(ts, "day", off) == usagelog.bucket_key(ts, "day")


async def test_session_view_label_has_tabpane_and_start_time():
    """세션 뷰 행 라벨은 대표 탭:패널을, **시작 시각**은 별도 'gtimes' 열로 낸다
    (2026-06-20: 표시 측이 '세션 | 타임스탬프 | 토큰' 3열로 나눈다). tzoff 를 고정해
    머신 tz 와 무관하게 검증한다."""
    # tzoff=0(UTC). ts=1_700_000_000 → 2023-11-14 22:13:20Z.
    s1a = dict(usagelog.make_record(1_700_000_000.0, 0, 1, 7, "a@x.org", 1000),
               tzoff=0)
    s1b = dict(usagelog.make_record(1_700_003_600.0, 0, 1, 7, "a@x.org", 500),
               tzoff=0)  # 같은 세션의 더 늦은 레코드 → 시작 시각(min ts)은 s1a 것
    s2 = dict(usagelog.make_record(1_700_100_000.0, 1, 2, 9, "b@y.org", 2000),
              tzoff=0)
    v = usagelog.agg_view([s1a, s1b, s2], "hour", dim="session")
    labels = [lbl for lbl, _, _ in v["groups"]]
    # 토큰 많은 순: 세션 9(2000) → 세션 7(1500)
    assert labels[0] == "세션 9 (탭2:p2)", labels
    assert labels[1] == "세션 7 (탭1:p1)", labels
    assert v["gtimes"][0] == "11-16 02:00", v["gtimes"]
    assert v["gtimes"][1] == "11-14 22:13", v["gtimes"]
    # day/week/month 버킷(일자 합성 ts=정오 고정)은 시:분이 무의미 → 날짜만.
    vd = usagelog.agg_view([s1a, s1b, s2], "day", dim="session")
    dlabels = [lbl for lbl, _, _ in vd["groups"]]
    assert dlabels[1] == "세션 7 (탭1:p1)", dlabels
    assert vd["gtimes"][1] == "11-14", vd["gtimes"]


async def test_session_view_time_order_and_no_fold():
    """요청 2026-06-22: 세션 뷰는 group_order="time"·top=None 이면 **시작 시각
    내림차순(최신 위)** 으로 전 세션을 보인다('기타' 접힘 없음). 토큰을 시간과
    반대로(늦은 세션일수록 적게) 주어 토큰순이 아니라 시간순임을 분리 검증한다."""
    base = 1_700_000_000.0
    recs = []
    for i in range(10):                  # 세션 100..109, 늦을수록 ts 큼·토큰 적음
        recs.append(dict(
            usagelog.make_record(base + i * 86400, 0, 1, 100 + i, "a@x.org",
                                 1000 - i * 10), tzoff=0))
    v = usagelog.agg_view(recs, "day", dim="session", top=None,
                          group_order="time")
    labels = [lbl for lbl, _, _ in v["groups"]]
    assert len(labels) == 10, f"전 세션 표시(기타 접힘 없음): {labels}"
    assert not any("기타" in s for s in labels), labels
    # 시작 시각 내림차순: 최신(ts 최대=세션 109, 토큰은 가장 적음)이 맨 위.
    assert labels[0].startswith("세션 109"), labels
    assert labels[-1].startswith("세션 100"), labels
    # 기본(group_order 미지정=tokens)은 종전대로 토큰 많은 순(세션 100 이 맨 위).
    vd = usagelog.agg_view(recs, "day", dim="session", top=None)
    dl = [lbl for lbl, _, _ in vd["groups"]]
    assert dl[0].startswith("세션 100"), dl


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


async def test_agg_index_keyed_map_with_labels_and_models():
    """agg_index: 버킷 키 → {tokens, label, models} 순수 맵(계층 트리 O(1) 조회용)."""
    recs = [
        dict(usagelog.make_record(1_700_000_000.0, 0, 1, 1, "a@x.org", 1000),
             model="opus-4.8"),
        dict(usagelog.make_record(1_700_000_100.0, 0, 1, 1, "a@x.org", 500),
             model="sonnet-4.6"),
        dict(usagelog.make_record(1_700_086_400.0, 1, 2, 2, "b@y.org", 2000),
             model="opus-4.8"),
    ]
    day0 = usagelog.bucket_key(1_700_000_000.0, "day")
    day1 = usagelog.bucket_key(1_700_086_400.0, "day")
    idx = usagelog.agg_index(recs, "day", weekdays="월화수목금토일")
    # 같은 날 두 레코드 합산 + 모델 티어 분해.
    assert idx[day0]["tokens"] == 1500
    assert idx[day0]["models"] == {"opus": 1000, "sonnet": 500}
    assert idx[day1]["tokens"] == 2000 and idx[day1]["models"] == {"opus": 2000}
    # 라벨은 _bucket_short 규칙(MM-DD(요일)).
    assert idx[day0]["label"].startswith(day0[5:])
    # 월 버킷은 한 키로 합산(키=YYYY-MM).
    midx = usagelog.agg_index(recs, "month")
    assert len(midx) == 1 and next(iter(midx.values()))["tokens"] == 3500
    # 빈 입력 → 빈 맵.
    assert usagelog.agg_index([], "day") == {}


async def test_claude_account_heuristics():
    # ① 가장 신뢰: Claude UI 의 "<email>'s Organization" 표시 → 별칭화(원문 미노출)
    assert claude_account(
        "Welcome\nwoojin@woojinkim.org's Organization\n/release-notes"
    ) == "wo…@woojinkim.org"
    # ② 계정 라벨 바로 뒤 이메일(Login:/Logged in as) → 별칭화. 짧은 로컬은 그대로.
    assert claude_account("Logged in as woojin@woojinkim.org\n") == "wo…@woojinkim.org"
    assert claude_account("Login: me@x.org") == "me@x.org"
    # 비이메일 약신호(조직/팀명·플랜명)는 2026-06-12 제거 — 산문/도구 출력 임의
    # 구절을 계정으로 오검출(실측 "Running 1 shell command" 적재). 이젠 None.
    assert claude_account("Organization: Acme Corp") is None
    assert claude_account("You are on the Max plan") is None
    assert claude_account("Account: Running 1 shell command") is None
    # 단서 없음 → None
    assert claude_account("? for shortcuts") is None
    # 예약/플레이스홀더 도메인은 계정으로 잡지 않는다. 다른 단서 없으면 None.
    assert claude_account("Transcript: email user@example.com to confirm") is None
    assert claude_account("contact a@b.invalid or x@y.test") is None
    # 예시 이메일·비이메일 조직 라벨이 섞여 있어도 None(이메일 신호만 신뢰)
    assert claude_account("see admin@example.org\nOrganization: Acme") is None
    # 계정 라벨의 이메일이 본문 이메일보다 우선
    assert claude_account(
        "ref bob@contractor.net in notes\nLogin: woojin@woojinkim.org"
    ) == "wo…@woojinkim.org"


async def test_claude_account_rejects_screen_emails():
    """2026-06-07 오탐 수정: 화면 본문에 흩어진 임의 이메일(git SSH URL·산문·예시)을
    계정으로 잡지 않는다 — 신뢰 신호(<email>'s Organization·계정 라벨)가 없으면 None
    (→ usagelog 가 unknown 으로 묶음). 잘못된 계정 표시보다 Unknown 이 옳다(사용자 지시)."""
    # git SSH URL → 과거 gi…@github.com 으로 튀던 대표 오탐
    assert claude_account(
        "set to the SSH URL of your GitHub repo, e.g. "
        "git@github.com:woojinkim/docker-monitor.git"
    ) is None
    # 라벨 없는 맨 이메일(transcript·코드 본문) → None
    assert claude_account("me@woojinkim.org") is None
    assert claude_account("please email someone at a@x.org about it") is None
    # git URL 이 화면에 있어도 진짜 계정 신호가 있으면 그쪽을 잡는다
    assert claude_account(
        "remote git@github.com:woojinkim/x.git\nme@woojinkim.org's Organization"
    ) == "me@woojinkim.org"


async def test_window_sum_boundaries_and_account():
    """창 구간 (since, until] 토큰 합 — 경계 규약은 (ts > since, ts <= until).
    계정 필터 옵션 포함."""
    recs = [
        {"ts": 100.0, "account": "me@x.org", "tokens": 10},
        {"ts": 200.0, "account": "me@x.org", "tokens": 20},
        {"ts": 300.0, "account": "unknown", "tokens": 40},
    ]
    assert usagelog.window_sum(recs, 100.0) == 60, "since 경계는 배타(>100)"
    assert usagelog.window_sum(recs, 0.0) == 70
    assert usagelog.window_sum(recs, 0.0, until_ts=200.0) == 30, "until 은 포함(<=)"
    assert usagelog.window_sum(recs, 0.0, account="me@x.org") == 30
    assert usagelog.window_sum(recs, 0.0, account="unknown") == 40
    assert usagelog.window_sum([], 0.0) == 0


async def test_fold_unknown_single_identified_account():
    """§5.5 단일 식별 계정 귀속(표시층): 식별(이메일) 계정이 정확히 하나면 미식별
    (unknown/None) 레코드를 그 계정으로 재라벨한다 — 'unknown 86%' 소음과 계정
    필터 시 일자 누락의 해소. 둘 이상이면 모호 → 귀속 안 함(원본 그대로)."""
    # 귀속 대상 판정: 식별 1개 → 그 계정, 0개/2개 → None.
    assert usagelog.fold_target({"me@x.org", "unknown"}) == "me@x.org"
    assert usagelog.fold_target({"wo…@y.org"}) == "wo…@y.org"   # 별칭도 '@' 포함
    assert usagelog.fold_target({"unknown"}) is None
    assert usagelog.fold_target({"me@x.org", "a@b.org"}) is None
    assert usagelog.fold_target(set()) is None
    # 재라벨: 미식별만 target 으로, 식별 레코드는 불변. 원본 리스트도 불변.
    recs = [
        {"ts": 1.0, "account": "unknown", "tokens": 100},
        {"ts": 2.0, "account": None, "tokens": 50},
        {"ts": 3.0, "account": "me@x.org", "tokens": 7},
    ]
    out = usagelog.fold_unknown(recs, "me@x.org")
    assert [r["account"] for r in out] == ["me@x.org"] * 3
    assert recs[0]["account"] == "unknown", "원본 불변(얕은 복사)"
    agg = usagelog.aggregate(out, "day")
    assert agg["groups"] == {"me@x.org": 157}
    # target 없음(None) → 원본 그대로.
    assert usagelog.fold_unknown(recs, None) is recs


async def test_migrate_token_accounts():
    """비신뢰 계정 일괄 unknown 마이그레이션(scripts/migrate_token_accounts) 핵심 로직.

    신뢰(allowlist) 계정·도메인만 남기고 나머지(git URL 오탐 등)는 unknown 으로
    바꾼다. 이미 unknown·빈 값은 unknown 유지, 손상 줄은 보존."""
    import importlib.util
    import json
    import os
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "migrate_token_accounts",
        os.path.join(here, "scripts", "migrate_token_accounts.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    keep_acc = {"me@woojinkim.org"}
    keep_dom = {"woojinkim.org"}
    # 단위: is_trusted / remap_account
    assert mod.is_trusted("me@woojinkim.org", keep_acc, keep_dom)
    assert mod.is_trusted("wo…@woojinkim.org", set(), keep_dom)   # 도메인 매치
    assert not mod.is_trusted("gi…@github.com", keep_acc, keep_dom)
    assert not mod.is_trusted("unknown", keep_acc, keep_dom)
    assert not mod.is_trusted(None, keep_acc, keep_dom)
    assert mod.remap_account("gi…@github.com", keep_acc, keep_dom) == "unknown"
    assert mod.remap_account("me@woojinkim.org", keep_acc, keep_dom) == "me@woojinkim.org"

    # 줄 단위 마이그레이션 + 통계
    lines = [
        json.dumps({"ts": 1, "account": "me@woojinkim.org", "tokens": 10}) + "\n",
        json.dumps({"ts": 2, "account": "gi…@github.com", "tokens": 5}) + "\n",
        json.dumps({"ts": 3, "account": "us…@example.com", "tokens": 4}) + "\n",
        json.dumps({"ts": 4, "account": "unknown", "tokens": 3}) + "\n",
        json.dumps({"ts": 5, "tokens": 2}) + "\n",          # account 키 없음→unknown 유지
        "  \n",                                              # 공백줄 보존
        "{corrupt json\n",                                   # 손상줄 보존
    ]
    out, st = mod.migrate_lines(lines, keep_acc, keep_dom)
    assert st["records"] == 5, st          # 공백·손상 제외
    assert st["changed"] == 2, st          # github·example 2건만 변경
    assert st["bad"] == 1, st
    accts = [json.loads(l)["account"] for l in out
             if l.strip() and not l.strip().startswith("{corrupt")]
    assert accts == ["me@woojinkim.org", "unknown", "unknown", "unknown", "unknown"]
    assert st["after"].get("me@woojinkim.org") == 1
    assert st["after"].get("unknown") == 4
    assert "{corrupt json\n" in out and "  \n" in out   # 비레코드 줄 원형 보존


async def test_model_cell_sequence_largest_remainder():
    """_model_cell_sequence: 칸 수를 점유비로 배분(합 정확히 n, 최대잔여법),
    바닥→위 순. 미정의 티어(unknown 등)는 정의 티어 뒤(위쪽)에."""
    # 3:1 → 4칸 중 3 opus, 1 sonnet (바닥부터: sonnet, opus, opus, opus)
    seq = usagelog._model_cell_sequence({"opus": 3, "sonnet": 1}, 4)
    assert seq == ["sonnet", "opus", "opus", "opus"], seq
    # unknown 은 뒤(위쪽)
    seq2 = usagelog._model_cell_sequence({"opus": 1, "unknown": 1}, 2)
    assert seq2 == ["opus", "unknown"], seq2
    # 토큰 없음 → 미귀속 칸은 'unknown'(종전 None), 0칸은 빈 리스트.
    assert usagelog._model_cell_sequence({}, 3) == ["unknown"] * 3
    assert usagelog._model_cell_sequence({"opus": 5}, 0) == []
