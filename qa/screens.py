"""qa/screens.py — 실 클라 화면을 읽는 **한 벌**의 술어.

⛔ **같은 질문을 두 술어로 묻지 않는다.** 「탭바가 사는 줄」을 찾는 정규식이 T0 과 T1 에
   한 벌씩 따로 있었고(`_status_line`·`_tabbar` — 글자까지 같았다), T2 를 더하면 셋이
   된다. 그러면 한쪽만 고쳐진 날 두 시나리오가 **같은 화면을 다르게 읽고**, 그때 의심하는
   자리는 제품이지 QA 가 아니다.

여기 있는 것은 전부 **증거를 위한 것이지 판정이 아니다** — 못 찾으면 예외를 내지 않고
빈 문자열을 돌려준다. 여기서 터지면 진짜 결함이 그 예외에 묻힌다.
"""
from __future__ import annotations

import re

#: 탭바의 탭 표식. ⚠ 「`:` 가 든 마지막 줄」로 고르면 **시계**(`03:32`)를 집는다(실측).
#: 탭은 `2:zsh` 처럼 콜론 뒤가 숫자가 아니라는 점으로 갈린다.
_TAB = re.compile(r"\d+:[^\d\s]")

#: 패널 테두리에 쓰이는 글자들. 하나라도 보이면 클라가 그림을 그린 것이다.
BORDER = "┌─│┐└┘"

#: 파이썬 트레이스백의 머리줄. 화면에 이것이 있으면 클라가 토한 것이다.
TRACEBACK = "Traceback (most recent call last)"


def drawn(text: str) -> bool:
    """클라가 **무언가 그렸나**. 테두리든 탭바든 하나면 된다.

    ⚠ 이것은 「제대로 그렸나」가 아니라 「떴나」다 — 기다림을 끝낼 조건으로 쓴다.
    """
    return any(c in text for c in BORDER) or "[+]" in text or bool(_TAB.search(text))


def has_traceback(text: str) -> bool:
    """클라가 화면에 트레이스백을 토했나."""
    return TRACEBACK in text


def missing_tree(text: str) -> list[str]:
    """탭 둘과 패널 테두리 중 **화면에 없는 것**의 이름들(빈 목록 = 다 그려졌다).

    ★ **이것이 판정이면서 동시에 기다림의 조건이다**(pytmux-425·426·427). 종전에는
    시나리오가 이 셋을 제 안에 펴 놓고 캡처는 **고정 6초**를 쉬었다 — 그러면 부하가
    걸린 회차에 클라가 아직 안 그린 화면을 잡아 「아무것도 안 그렸다」로 신고한다
    (실측 2026-08-31 `qa-20260831-040007`: 같은 기계·같은 시각에 폴링하는 T2 는 통과,
    고정 대기인 T0·T1 은 셋 다 붉었다 · 한가할 때 첫 그리기 실측 0.6~1.2초).
    ⛔ **그러니 이 술어를 두 벌로 만들지 않는다** — 기다림이 판정보다 약하면 못 그린
    화면을 통과시키고(거짓 초록), 강하면 다 그린 화면을 더 기다린다.
    """
    return [n for n, ok in (
        ("탭 1", "1:" in text),
        ("탭 2", "2:" in text),
        ("패널 테두리", "┌" in text and "└" in text),
    ) if not ok]


def tabbar(text: str) -> str:
    """탭바가 사는 줄(못 찾으면 빈 문자열). **증거**를 위한 것이다."""
    for line in reversed(text.replace("\r", "").split("\n")):
        s = line.strip()
        if _TAB.search(s):
            return s[:200]
    return ""


def around(text: str, needle: str, span: int = 400) -> str:
    """`needle` 언저리를 잘라 낸다(없으면 꼬리). 결함 본문에 실을 재료다."""
    i = text.find(needle)
    return text[max(0, i - 80):i + span] if i >= 0 else text[-span:]
