"""usage-view 시각 파서 — 스크랩 /usage 의 reset 문자열을 datetime 으로 근사 + 카운트
다운 포맷(순수 함수, 테스트 핵심).

스크랩 데이터의 `reset` 은 사람이 읽는 문자열("2pm (Asia/Seoul)", "Jun 13 at 3am
(Asia/Seoul)", "1:40pm")이라 정확한 datetime 이 아니다. 가장 가까운 미래 시각으로
근사 파싱해 **분 단위** 카운트다운을 만든다(초 단위 정밀 카운트다운은 웹 API 옵션이
필요 — docs/internal/USAGE_VIEW_DESIGN.md §2.1·§9).

datetime/re 만 쓰고 textual/rich 를 import 하지 않는다. 이 모듈은 화면/오버레이
경로에서만 지연 import 되므로 서버 프로세스는 읽지 않는다."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# "3am", "2pm", "1:40pm", "11:05 am"
_TIME_RE = re.compile(r"(\d{1,2})(?::(\d{2}))?\s*([ap])m", re.I)
# "Jun 13" / "June 13" / "13 Jun"
_MD_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2})|(\d{1,2})\s+([A-Za-z]{3,9})")


def _parse_time(s: str):
    """12시간제 시각 문자열에서 (hour24, minute) 추출. 없으면 None."""
    m = _TIME_RE.search(s)
    if not m:
        return None
    h = int(m.group(1)) % 12
    if m.group(3).lower() == "p":
        h += 12
    mn = int(m.group(2)) if m.group(2) else 0
    if h > 23 or mn > 59:
        return None
    return h, mn


def _parse_month_day(s: str):
    """"Jun 13"/"13 Jun" 에서 (month, day). 없으면 None."""
    m = _MD_RE.search(s)
    if not m:
        return None
    if m.group(1):
        mon = _MONTHS.get(m.group(1)[:3].lower())
        day = int(m.group(2))
    else:
        mon = _MONTHS.get(m.group(4)[:3].lower())
        day = int(m.group(3))
    if not mon or not (1 <= day <= 31):
        return None
    return mon, day


def parse_reset_to_dt(s, now=None):
    """reset 문자열을 가장 가까운 미래 로컬 datetime 으로 근사. 못 파싱하면 None.

    - 시각만("2pm"): 오늘 그 시각, 이미 지났으면 내일.
    - 날짜+시각("Jun 13 at 3am"): 올해 그 날짜·시각, 이미 지났으면 내년.
    - 날짜만("Jun 13"): 그 날짜 00:00.

    타임존 괄호("(Asia/Seoul)")는 무시한다 — 스크랩 값이 이미 사용자 로캘 기준이라
    로컬 시각으로 간주(USAGE_VIEW_DESIGN §2.1). `now` 는 테스트 결정성용."""
    if not s or not isinstance(s, str):
        return None
    now = now or datetime.now()
    head = s.split("(")[0]              # 타임존 괄호 제거
    tm = _parse_time(head)
    md = _parse_month_day(head)
    if md is None and tm is None:
        return None
    h, mn = tm if tm else (0, 0)
    if md is not None:
        mon, day = md
        try:
            dt = now.replace(month=mon, day=day, hour=h, minute=mn,
                             second=0, microsecond=0)
        except ValueError:
            return None                 # 2/30 같은 무효 날짜
        if dt <= now:
            try:
                dt = dt.replace(year=dt.year + 1)
            except ValueError:
                return None             # 2/29 → 비윤년
        return dt
    # 시각만 — 오늘/내일 중 가장 가까운 미래.
    dt = now.replace(hour=h, minute=mn, second=0, microsecond=0)
    if dt <= now:
        dt += timedelta(days=1)
    return dt


def fmt_countdown(td) -> str:
    """timedelta → "Dd HHh MMm" / "HHh MMm SSs" / "MMm SSs" / "SSs"(참조
    claude-token-viewer fmt_td 미러). 0 이하는 'now'."""
    total = int(td.total_seconds())
    if total <= 0:
        return "now"
    d, rem = divmod(total, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h:02d}h {m:02d}m"
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def urgency(td) -> str:
    """카운트다운 긴급도 토큰: 'red'(<30m)·'yellow'(<1h)·'cyan'(그 외). 참조
    스크립트 _clock_style 미러 — 호출부가 테마 색/스타일로 매핑한다."""
    secs = int(td.total_seconds())
    if secs < 30 * 60:
        return "red"
    if secs < 60 * 60:
        return "yellow"
    return "cyan"
