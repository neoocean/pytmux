"""T0 코어 루프 — 붙기 · 분할 · 탭 · detach/attach.

이 저장소가 스스로 적어 둔 사각지대를 정확히 겨눈다: **실 PTY · 실 데몬 · 실 Textual
클라 프로세스가 그리는 화면.** 헤드리스 단언 스위트(`tests/`)는 위젯 상태와 합성 셀을
보지만 "사용자가 실제로 보는 화면"은 안 본다.

⚠ **깊이가 아니라 끝까지 가는 것이 T0 의 값이다** — 기동 → 조작 → 실 화면 → 재부착 →
종료까지 한 줄기가 끊기지 않고 도는 것을 매 런 확인한다. 넓히는 것은 T1 몫이다.
"""
from __future__ import annotations

import re

from ..session import NotSupported

NAME = "T0-core-loop"
TIER = "T0"
TITLE = "코어 루프 — 붙기·분할·탭·detach/attach"


def run(ctx) -> None:
    s = ctx.session

    with ctx.step("server-up"):
        tabs, panes = s.tree()
        if (tabs, panes) != (1, 1):
            ctx.fail(oracle="tree/initial", key="initial-tree", severity="S2",
                     title="새 서버의 초기 트리가 탭1·패널1 이 아니다",
                     expected="1 tabs, 1 panes", actual=f"{tabs} tabs, {panes} panes")

    with ctx.step("split"):
        s.control("split-window -h")
        tabs, panes = s.tree()
        if (tabs, panes) != (1, 2):
            ctx.fail(oracle="tree/split", key="split-h", severity="S2",
                     title="split-window -h 뒤 패널이 둘이 아니다",
                     expected="1 tabs, 2 panes", actual=f"{tabs} tabs, {panes} panes")

    with ctx.step("new-window"):
        s.control("new-window")
        tabs, panes = s.tree()
        if (tabs, panes) != (2, 3):
            ctx.fail(oracle="tree/new_window", key="new-window", severity="S2",
                     title="new-window 뒤 탭이 둘이 아니다",
                     expected="2 tabs, 3 panes", actual=f"{tabs} tabs, {panes} panes")

    # ── 실 화면 ────────────────────────────────────────────────────────────────
    # 여기부터가 이 층의 존재 이유다. 위 세 스텝은 서버가 자기 트리를 말한 것이고,
    # 아래는 **클라가 실제로 그린 것**이다. 서버는 맞는데 화면이 비는 부류(이 저장소가
    # `client/CLAUDE.md` 에 적어 둔 "GUI 배선 누락")는 여기서만 잡힌다.
    with ctx.step("client-render") as st:
        try:
            _, alive, text = s.capture_client(seconds=6.0)
        except NotSupported as e:
            st.skip(str(e))
        else:
            _judge_screen(ctx, text, alive, key_prefix="first")

    # ── 재부착 ─────────────────────────────────────────────────────────────────
    # 캡처는 끝에 클라를 SIGKILL 로 죽인다. **서버와 셸 세션은 그래도 살아 있어야 한다** —
    # "클라이언트나 상위 터미널을 닫아도 세션은 유지된다"가 이 제품의 첫째 계약이다.
    with ctx.step("reattach") as st:
        tabs, panes = s.tree()
        if (tabs, panes) != (2, 3):
            ctx.fail(oracle="session/survives_client_death", key="tree-after-death",
                     severity="S1",
                     title="클라가 죽으면 서버 세션도 함께 사라진다",
                     expected="클라 종료 뒤에도 2 tabs, 3 panes",
                     actual=f"{tabs} tabs, {panes} panes")
        try:
            _, alive, text = s.capture_client(seconds=6.0)
        except NotSupported as e:
            st.skip(str(e))
        else:
            _judge_screen(ctx, text, alive, key_prefix="reattach")

    with ctx.step("kill-server"):
        s.stop()
        left = ctx.slot.residue()
        if left:
            ctx.fail(oracle="lifecycle/clean_exit", key="residue", severity="S3",
                     title="kill-server 뒤에도 슬롯에 살아 있는 것이 남는다",
                     expected="슬롯에 응답하는 서버도, 살아 있는 pty-host 도 없다",
                     actual="; ".join(left))


def _judge_screen(ctx, text: str, alive: bool, key_prefix: str) -> None:
    """실 클라가 그린 화면 판정.

    ⚠ **판정 재료를 좁게 고른다.** 화면 전체를 골든으로 굳히면 시각·호스트명·셸 프롬프트가
    들어와 매 런 붉어진다(위양성 = 원칙 ⓓ 위반). 여기서 보는 셋은 그 셋 다 아니다 —
    ⑴ 클라가 살아 있었나 ⑵ 트레이스백이 화면에 토해졌나 ⑶ 탭바에 탭 둘과 패널 테두리가
    실제로 그려졌나.
    """
    if not alive:
        ctx.fail(oracle="client/alive", key=f"{key_prefix}-died", severity="S1",
                 title="실 PTY 아래 붙인 클라가 스스로 종료한다",
                 expected="캡처 시간 동안 클라 프로세스가 살아 있다",
                 actual="캡처 중 스스로 종료(즉시 종료/크래시 신호)")
        return
    if "Traceback (most recent call last)" in text:
        ctx.fail(oracle="client/no_traceback", key=f"{key_prefix}-traceback",
                 severity="S1",
                 title="실 클라 화면에 파이썬 트레이스백이 그려진다",
                 expected="화면에 트레이스백이 없다",
                 actual=_around(text, "Traceback (most recent call last)"))
        return
    missing = [n for n, ok in (
        ("탭 1", "1:" in text),
        ("탭 2", "2:" in text),
        ("패널 테두리", "┌" in text and "└" in text),
    ) if not ok]
    if missing:
        # ⚠ **무엇이 없다**만 적으면 이슈를 받은 사람이 아무것도 못 한다(실측: 첫 진짜
        #   결함의 본문이 "안 보이는 것: 탭 1 · 화면 9683자" 뿐이었다). 화면의 마지막
        #   상태줄을 함께 싣는다 — 그 한 줄이 곧 재현 단서다.
        ctx.fail(oracle="client/renders_tree", key=f"{key_prefix}-missing",
                 severity="S2",
                 title="실 클라 화면에 탭바·패널 테두리가 안 그려진다",
                 expected="탭 둘(1:·2:)과 패널 테두리가 화면에 있다",
                 actual=f"안 보이는 것: {', '.join(missing)} · 화면 {len(text)}자\n"
                        f"상태줄: {_status_line(text) or '(못 찾음)'}")


def _status_line(text: str) -> str:
    """화면의 마지막 상태줄(탭바가 사는 줄). 판정이 아니라 **증거**를 위한 것이라
    못 찾으면 조용히 빈 문자열이다 — 여기서 예외를 내면 진짜 결함이 묻힌다.

    ⚠ 「`:` 가 든 마지막 줄」로 고르면 **시계**(`03:32`)를 집는다(실측). 탭은
    `2:zsh` 처럼 콜론 뒤가 숫자가 아니라는 점으로 갈린다.
    """
    tab = re.compile(r"\d+:[^\d\s]")
    for line in reversed(text.replace("\r", "").split("\n")):
        s = line.strip()
        if tab.search(s):
            return s[:200]
    return ""


def _around(text: str, needle: str, span: int = 400) -> str:
    i = text.find(needle)
    return text[max(0, i - 80):i + span] if i >= 0 else text[-span:]
