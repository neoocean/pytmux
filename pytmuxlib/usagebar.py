"""막대와 Claude 한도 표시 줄 — **UI 무의존**(rich/textual 을 안 읽는다).

# 왜 여기인가

이 코드는 원래 `clientutil.bar`(막대 프리미티브)와 `clientscreens.usage_bar_lines`
(한도 줄)로 나뉘어 있었다. 둘 다 **문자열만 만드는 순수 함수**인데, 사는 집이 최상단
에서 `rich`/`textual` 을 읽는 클라 모듈이라 **서버 프로세스가 부를 수 없었다.**

Tier B 셀 기여(설계 §4.2)는 서버가 같은 그림을 뽑아 네이티브 클라에 보내는 것이라,
그리는 재료가 클라에 묶여 있으면 애초에 시작할 수 없다 — P3 가 시계 폰트 표를
`blockfont.py` 로 먼저 뺀 것과 **같은 이유의 같은 리팩터**다(HANDOFF §10 P3 매듭 ②).

옮기는 것은 자리뿐이고 **동작은 한 글자도 안 바뀐다**. 종전 import 경로
(`clientutil.bar` · `clientscreens.usage_bar_lines`)는 재수출로 그대로 산다 —
소비자가 여덟 곳이라 경로를 다 갈면 이 CL 이 커지고, 커진 CL 은 되돌리기 어렵다.

# 여기에 UI 를 들이지 말 것

`rich.style`·`textual` 을 최상단에서 import 하는 순간 서버가 이 모듈을 못 읽고,
그러면 한도 오버레이가 다시 클라 전용이 된다. 색은 이 모듈이 정하지 않는다 —
런에 **의미 이름**만 싣고 각 클라가 자기 테마로 푼다(`plugins/*/cells.py` 규약).
"""
from __future__ import annotations

from . import i18n
from .cellwidth import char_cells

# 부분 블록 여덟 단(1/8 칸씩). 막대가 정수 칸에 안 맞아도 끝이 뭉개지지 않는다.
_BAR_BLOCKS = " ▏▎▍▌▋▊▉█"

# 비-right_align 막대의 **빈 트랙**을 채우는 글자. 색이 없어도 사용/잔여가 갈리게
# 채움 '█' 과 다른 글리프를 쓴다(요청 2026-06-16).
_USAGE_EMPTY_TRACK = "░"

# 한도 버킷과 그 라벨 키 — 표시 순서가 곧 이 표의 순서다.
_BUCKETS = (("session", "usage.session_5h"),
            ("week_all", "usage.week_all"),
            ("week_sonnet", "usage.week_sonnet"))


def bar(value: int, vmax: int, cells: int) -> str:
    """value/vmax 비율을 cells 칸 막대 문자열로(부분블록 포함). vmax<=0/cells<=0/
    value<=0 이면 빈 문자열. 표시 계층(DataTable/InfoScreen/usage_bar_lines) 공용 —
    폭은 호출부가 셀폭으로 계산한다. (S5b 에서 usagelog → clientutil, 2026-08-02f 에
    여기로 — 서버도 같은 막대를 그려야 해서 UI 무의존 모듈이 필요했다.)"""
    if cells <= 0 or vmax <= 0 or value <= 0:
        return ""
    frac = max(0.0, min(1.0, value / vmax))
    eighths = int(round(frac * cells * 8))
    full, rem = divmod(eighths, 8)
    full = min(full, cells)
    return "█" * full + (_BAR_BLOCKS[rem] if rem and full < cells else "")


def usage_bar_lines(usage, width=80, age_sec=None, right_align=False,
                    track_char=" ", row_gap=False):
    """Claude `/usage` 한도 dict(session·week_all·week_sonnet)를 보기 좋은 표시
    줄 목록으로 만든다. 각 줄: 라벨(폭 통일) + 막대 + % + 리셋(요약, 타임존 생략).
    데이터가 없으면 None. TokenLogScreen 의 한도 섹션과 자동 /usage 팝업이 공유한다.

    age_sec: 실측 경과(초, S6 T3). 2분 이상 묵었으면 마지막에 'N분 전 실측'을 붙여
    stale 임을 알린다 — 실측이 주 표시로 승격되면서 묵은 값을 현재값으로 오독하지
    않게 하는 표시측 대응(stale 스냅샷 혼동 방지).

    right_align: 켜면 막대를 트랙 폭(barw)으로 채워 행마다 리셋 시작 열을 맞추고,
    % 숫자를 막대 바로 옆이 아니라 **줄 오른쪽 끝(width)** 에 우측정렬한다(리셋은
    막대 뒤). usage-view 플러그인 팝업/오버레이가 켠다 — 기본 False 라 기존 소비자
    (usage-panel·TokenLogScreen)의 표시는 그대로다(opt-in).

    track_char: 막대의 **빈 부분**(채움 뒤 트랙)을 채우는 글자. 기본 ' '(공백 →
    배경과 동일, 종전 동작). 호출부가 회색 트랙을 그리려고 구분 글자(예 '░')를 주면
    빈 칸을 그 글자로 채워, 표시측이 그 글자만 회색으로 색칠할 수 있게 한다(요청:
    막대=흰색·빈 부분=회색으로 배경과 구분). right_align 일 때만 의미가 있다(빈 트랙이
    그 분기에서만 채워진다).

    row_gap: 켜면 막대 행들 **사이에 빈 줄 1개**를 넣어 시각적으로 분리한다(요청
    2026-06-18, [한도] 뷰). 첫 막대 앞·계정/신선도 줄엔 안 넣는다. 기본 False."""
    if not isinstance(usage, dict):
        return None
    barw = 24 if width >= 80 else (16 if width >= 60 else 8)
    # 표시할 한도(데이터 있는 것)만 먼저 모아 **라벨 폭을 통일**한다 — 라벨 길이가
    # 달라(예: 'Week Sonnet' 11셀 vs 'Week all' 8셀) 막대 시작 열이 행마다 어긋나던
    # 것을, 가장 긴 라벨 + 1칸으로 모두 패딩해 **모든 막대의 왼쪽 시작을 같은 열**에
    # 맞춘다(요청 2026-06-18 — 종전 고정 10셀은 11셀 라벨에서 막대가 한 칸 밀렸다).
    entries = []
    for key, label_key in _BUCKETS:
        d = usage.get(key)
        if isinstance(d, dict) and d.get("pct") is not None:
            entries.append((i18n.t(label_key), d))
    label_w = max((sum(char_cells(c) for c in nm) for nm, _ in entries),
                  default=0)
    rows = []
    for name, d in entries:
        pct = d["pct"]
        gauge = bar(pct, 100, barw)
        # 가장 긴 라벨 + 1칸 → 모든 라벨이 같은 폭(막대 시작 열 통일), 최소 1칸 간격.
        label = name + " " * max(1, label_w + 1 - sum(char_cells(c) for c in name))
        reset = d.get("reset")
        # 타임존 괄호는 자리 절약 위해 생략.
        # 새로고침 화살표와 날짜/시각 사이 한 칸(가독성 — 붙으면 첫 글자가 화살표에
        # 겹쳐 안 보인다, 제보 2026-07-18). 종료 요약(_usage_exit_lines)과 동형.
        reset_txt = ("↻ " + reset.split(" (")[0].strip()) if reset else ""
        if right_align:
            # 막대를 트랙 폭으로 채워(공백) 리셋 시작 열을 행마다 맞추고, % 숫자는
            # 줄 오른쪽 끝(width)에 우측정렬한다 — 막대/리셋과 % 사이를 공백으로 채움.
            gauge = gauge + track_char * max(0, barw - len(gauge))
            tail = f"{pct:>3}%"
            body = f"{label}{gauge}  {reset_txt}".rstrip()
            gap = (width - sum(char_cells(c) for c in body)
                   - sum(char_cells(c) for c in tail))
            line = body + " " * max(1, gap) + tail
        else:
            # 전체 막대를 그려 **사용(채움)·잔여(빈칸)를 한눈에 구분**한다(요청
            # 2026-06-16, Claude /usage 표시처럼). bar() 는 채운 부분만 주므로 남는
            # 트랙을 '░'(연한 음영)로 채워 항상 전체 폭(barw)을 그린다 — 채움 '█' vs
            # 빈칸 '░' 라 색 없이도 어디까지 찼는지/전체 중 얼마 남았는지 보인다
            # (종전엔 채운 블록만 그려 전체·잔여가 안 보였다). pct≥100 이면 트랙이
            # 전부 채워져 가득 찬 막대가 된다.
            full_gauge = gauge + _USAGE_EMPTY_TRACK * max(0, barw - len(gauge))
            # % 뒤에 '사용/used' 를 명시한다(2026-06-12 제보): 방향 라벨이
            # 없으면 잔여 표기와 섞여 다른 값처럼 읽혔다 — Claude /usage 의 "N% used"
            # 와 동일 표기. footer 5h 도 같은 사용률로 통일됐다(clientstatus
            # claude.limit_used — 모든 표면이 같은 방향·같은 숫자).
            line = f"{label}{full_gauge} {pct:>3}% {i18n.t('usage.used')}"
            if reset_txt:
                line += "  " + reset_txt
        # 막대 행 사이 빈 줄 1개(row_gap) — 첫 막대 앞엔 안 넣는다.
        if row_gap and rows:
            rows.append("")
        rows.append(line)
    # 그림자 /usage 세션의 계정(일치 확인용). 키가 있을 때만 — 폰 앱과 다른 계정이면
    # 한도가 실제로 달라지므로 눈으로 대조하라고 표시한다. 신호 못 잡으면 '미확인'.
    if rows and "account" in usage:
        # 전체 이메일(account_full, 프로브가 라이브로 실어 보냄)을 우선 표시하고, 없으면
        # 별칭(account, DB 영속·재시작 직후 폴백)으로. 사용자 본인 화면이라 줄이지 않고
        # 전체를 보인다(요청·footer claude_account_full 과 동일 방침).
        acct = usage.get("account_full") or usage.get("account")
        rows.append(i18n.t("usage.account", acct=acct) if acct
                    else i18n.t("usage.account_unknown"))
    # S6 T3: 실측 신선도 — 2분 미만이면 표기 생략(잡음), 그 이상은 분/시간 단위.
    if rows and isinstance(age_sec, (int, float)) and age_sec >= 120:
        m = int(age_sec // 60)
        ago = (i18n.t("usage.ago_hm", h=m // 60, m=m % 60) if m >= 60
               else i18n.t("usage.ago_m", m=m))
        rows.append(i18n.t("usage.measured_ago", ago=ago))
    return rows or None
