"""상태줄 알림의 **등급(severity)과 이력** — 순수 데이터 계층(§10-8).

설계 = `docs/internal/NOTICE_SEVERITY_AND_HISTORY_2026-07-23.md`. 동기: 맨 아랫줄
알림이 성공/실패 구분 없이 같은 노란색이고 2초 뒤 사라지면 **어디에도 남지 않아**,
실패 문구가 잘리거나(SSL 오류) 성공과 구분되지 않아 반복해서 놓쳤다.

이 모듈은 Textual 을 import 하지 않는다 — 색 이름(테마 변수명)만 정하고 실제 해석은
`clientwidgets` 의 `theme_color()` 가 한다. 덕분에 등급표·이력 규칙을 앱 없이 단위
테스트할 수 있다(회귀 오라클 §7).
"""
from __future__ import annotations

import time as _time
from collections import deque

# 등급 4종. `ok`=요청한 일이 끝났다 / `info`=상태 알림(성공도 실패도 아님) /
# `warn`=됐지만 주의 필요 / `error`=실패, 사용자가 조치해야 한다.
#
# · theme = theme_color() 로 해석할 **테마 변수명**(하드코딩 색 금지 — 라이트/다크
#   양쪽에서 대비를 테마가 보장한다). `textual-dark` 에서 warning==accent 였던 전례가
#   있어(compose-esc-mode 회귀) **네 등급 색이 서로 다름**을 테스트로 못박는다.
# · sym = 색맹·모노크롬 터미널 대비 기호(G5). CJK 폭 문제를 피해 **폭 1** 글자만.
# · secs/dismissable = 호출부가 매번 정하지 않게 두는 등급 기본값. error 는 길게
#   남기고 수동 닫기 — 놓치면 안 되는 알림이 전부 여기 해당한다.
_SPEC = {
    "ok":    {"sym": "✓", "theme": "success", "fg": "black",
              "secs": 2.0, "dismissable": False, "rank": 1},
    "info":  {"sym": "·", "theme": "primary", "fg": "white",
              "secs": 2.0, "dismissable": False, "rank": 0},
    "warn":  {"sym": "!", "theme": "warning", "fg": "black",
              "secs": 3.0, "dismissable": False, "rank": 2},
    "error": {"sym": "✕", "theme": "error", "fg": "white",
              "secs": 5.0, "dismissable": True, "rank": 3},
}
SEVERITIES = ("ok", "info", "warn", "error")
DEFAULT_SEVERITY = "info"

# 이력 링 버퍼 크기(클라 메모리·비영속). 하루치 디버깅을 덮으면서 수십 KB 수준.
HISTORY_LIMIT = 200


def normalize(sev) -> str:
    """모르는 등급은 `info` 로 낮춘다 — 구/신 버전 혼재(서버가 새 등급을 보냄)에서
    예외 대신 안전한 표시로 떨어지게(§7-8)."""
    return sev if sev in _SPEC else DEFAULT_SEVERITY


def spec(sev) -> dict:
    return _SPEC[normalize(sev)]


def symbol(sev) -> str:
    return _SPEC[normalize(sev)]["sym"]


def theme_name(sev) -> str:
    """등급 배경색으로 쓸 **테마 변수명**(clientwidgets 가 theme_color 로 해석)."""
    return _SPEC[normalize(sev)]["theme"]


def fg(sev) -> str:
    return _SPEC[normalize(sev)]["fg"]


def default_secs(sev) -> float:
    return _SPEC[normalize(sev)]["secs"]


def default_dismissable(sev) -> bool:
    return _SPEC[normalize(sev)]["dismissable"]


def rank(sev) -> int:
    """심각도 순위(error > warn > ok > info). 미확인 배지 색을 고를 때 쓴다."""
    return _SPEC[normalize(sev)]["rank"]


class NoticeEntry:
    """이력 한 줄. `count` 는 직전과 같은 알림이 연속으로 반복된 횟수(중복 접기)."""

    __slots__ = ("ts", "sev", "text", "source", "count", "seen")

    def __init__(self, ts: float, sev: str, text: str, source: str):
        self.ts = ts
        self.sev = normalize(sev)
        self.text = text
        self.source = source
        self.count = 1
        self.seen = False

    def same_as(self, sev: str, text: str, source: str) -> bool:
        return (self.sev == normalize(sev) and self.text == text
                and self.source == source)


class NoticeHistory:
    """지나간 알림의 링 버퍼(클라 메모리·비영속, N1).

    · 중복 접기 — 직전과 같은 (등급, 문구, 출처)가 이어지면 새 항목 대신 `count` 만
      올린다. 주기 워커가 같은 실패를 반복해도 이력이 그것만으로 차지 않게.
    · 미확인(seen=False) 수와 그 중 최고 등급을 상태줄 배지가 읽는다 — 알림이 사라진
      뒤에도 "방금 실패가 있었다"가 **색으로 남는** 것이 이 기능의 핵심이다.
    · 문구는 **번역된 최종 문자열**로 받는다(키+kw 로 저장하면 로케일을 바꿨을 때
      이력이 뒤섞인다 — 호출부가 번역 후 넣는다).
    """

    def __init__(self, limit: int = HISTORY_LIMIT):
        self._items: deque = deque(maxlen=max(1, int(limit)))

    def add(self, text: str, sev: str = DEFAULT_SEVERITY,
            source: str = "local", ts: float | None = None) -> NoticeEntry:
        """알림 1건 기록(신규 또는 직전 항목에 접기). 어느 쪽이든 **미확인**으로 돈다
        — 같은 실패의 반복도 사용자가 알아야 할 새 사건이기 때문이다."""
        now = _time.time() if ts is None else float(ts)
        text = "" if text is None else str(text)
        if self._items:
            last = self._items[-1]
            if last.same_as(sev, text, source):
                last.count += 1
                last.ts = now
                last.seen = False
                return last
        e = NoticeEntry(now, sev, text, source)
        self._items.append(e)
        return e

    def entries(self, newest_first: bool = True) -> list:
        items = list(self._items)
        return list(reversed(items)) if newest_first else items

    def __len__(self) -> int:
        return len(self._items)

    def unseen(self) -> int:
        return sum(1 for e in self._items if not e.seen)

    def unseen_severity(self):
        """미확인 항목 중 **가장 높은 등급**(없으면 None) — 배지 색."""
        top = None
        for e in self._items:
            if e.seen:
                continue
            if top is None or rank(e.sev) > rank(top):
                top = e.sev
        return top

    def mark_seen(self) -> None:
        """이력을 열어봤다 — 미확인 수 0, 배지는 평시 색으로."""
        for e in self._items:
            e.seen = True

    def clear(self) -> None:
        self._items.clear()
