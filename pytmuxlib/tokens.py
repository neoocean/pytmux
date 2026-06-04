"""Claude Code 토큰 사용량 누적 — 화면의 스트리밍 토큰 수를 세션 누계로 합산.

배경/정의(docs/HANDOFF.md §10 "토큰 사용량 누적"):
  Claude Code 의 busy footer 는 "✽ Crunching… (12s · ↑ 0.4k · ↓ 1.9k tokens)" 처럼
  **현재 응답 한 건의 running 토큰 수**를 보여준다. 이 값은 스트리밍 중 단조 증가하다
  응답이 끝나고 다음 응답이 시작되면 다시 작은 값에서 시작한다(= 프레임 단위 델타도,
  세션 누계도 아니다). 따라서 **세션 누계 = 각 응답의 최종(peak) 토큰 수의 합**으로
  정의한다.

  이 모듈은 순수 함수 + 작은 상태기계로 그 누계를 만든다(claude.py 처럼 코어와 분리,
  서버/클라 어디서나 부담 없이 호출). 서버 flush 루프가 매 프레임 `step()` 을 부르고,
  지속 로깅/집계(#7)는 같은 데이터 소스를 공유한다.
"""
from __future__ import annotations

import re

# busy footer 의 화살표 토큰: "↑ 0.4k tokens" / "↓ 1.9k tokens" / "↑ 419 tokens".
# 화살표가 앞에 붙은 것만(스트리밍 송수신량) 잡는다 — "used 45k tokens" 같은 누계
# 언급(claude_usage 가 다루는 신호)과 구분.
_ARROW_TOK_RE = re.compile(r"[↑↓]\s*([\d][\d.,]*)\s*([kKmM]?)\s*tokens?", re.I)


def _to_int(num: str, suf: str) -> int:
    """"1.9", "k" → 1900. "419", "" → 419. "2", "m" → 2_000_000."""
    try:
        v = float(num.replace(",", ""))
    except ValueError:
        return 0
    mul = {"k": 1_000, "m": 1_000_000}.get(suf.lower(), 1)
    return int(round(v * mul))


def parse_running_tokens(text: str):
    """화면 텍스트에서 현재 응답의 running 토큰 수를 합산해 반환(int).

    busy footer 의 ↑/↓ 토큰을 모두 더한다(송신+수신). 화살표 토큰이 하나도 없으면
    None(= 토큰 표시 없음, 응답 처리 중이 아닐 수 있음).
    """
    total = 0
    found = False
    for m in _ARROW_TOK_RE.finditer(text):
        total += _to_int(m.group(1), m.group(2))
        found = True
    return total if found else None


def new_state() -> dict:
    """패널별 누적 상태. peak=현재 응답 중 본 최댓값, total=세션 누계."""
    return {"peak": 0, "total": 0}


def reset(state: dict) -> None:
    """새 Claude 세션 시작 시 누계를 0 으로(패널 재사용/세션 경계)."""
    state["peak"] = 0
    state["total"] = 0


def step(state: dict, running, busy: bool) -> int:
    """한 프레임 전진. 응답 종료(busy 종료 또는 running 급감=새 응답 시작)에
    현재 peak 를 total 에 확정한다. state 를 in-place 갱신하고, 이번에 확정된
    토큰 양(없으면 0)을 반환한다(#7 의 영속 로깅이 이 확정 시점을 이벤트로 쓴다).

    running: parse_running_tokens 결과(int) 또는 None(토큰 표시 없음).
    busy: 현재 패널이 처리중(claude_state == "busy")인지.
    """
    committed = 0
    peak = state.get("peak", 0)
    if running is not None:
        # running 이 직전 peak 보다 크게 줄면(절반 이하·최소 50 토큰 여유) idle 갭
        # 없이 다음 응답이 시작된 것 — 이전 peak 를 확정하고 새로 시작.
        if peak > 0 and running < peak - max(50, peak // 2):
            state["total"] = state.get("total", 0) + peak
            committed += peak
            peak = 0
        if running > peak:
            peak = running
    if not busy and peak > 0:
        # 응답 종료(busy 끝, idle/None/limit) — peak 확정.
        state["total"] = state.get("total", 0) + peak
        committed += peak
        peak = 0
    state["peak"] = peak
    return committed


def fmt(total: int) -> str:
    """누계를 짧게 표기. 1234567 → "1.2M", 45200 → "45.2k", 800 → "800"."""
    if total >= 1_000_000:
        return f"{total / 1_000_000:.1f}M".replace(".0M", "M")
    if total >= 1_000:
        return f"{total / 1_000:.1f}k".replace(".0k", "k")
    return str(total)
