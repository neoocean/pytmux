"""claude-disable-feedback 플러그인 — Claude Code 의 피드백 권유 문구를 화면에서 가린다.

두 가지 피드백 표면을 폭을 유지한 채 공백으로 덮어 **절대 안 보이게** 한다(사용자 요청
2026-06-17·2026-06-18):
  ① 시작 팁 "Tip: Use /feedback to help us improve!"
  ② 세션 종료 배너 "How is Claude doing this session? (optional)" + "1: Bad … 0: Dismiss"

기능 전체가 이 디렉토리 안에 있다(claude-code 에서 분리, 2026-06-20). 디렉토리를 통째로
지우거나 플러그인 관리 팝업에서 끄면 두 문구가 다시 화면에 보일 뿐 — 코어/claude-code 는
그대로 동작한다(delete-to-disable). 코어는 render 행을 클라 전송 직전에 `server_filter_rows`
레지스트리 훅으로만 이 플러그인에 흘려 보내므로, 이 플러그인이 없으면 행은 변형 없이 지나간다.

게이트는 claude-code 가 패널에 설치한 `_claude`(현재 실행 중) **또는** `_hdr_claude`
(디바운스 Claude 신호)를 getattr 로 부드럽게 참조한다 — claude-code 가 없으면 둘 다 없어
모든 패널이 통과(no-op)된다. 피드백 배너는 세션이 **끝나는 순간** 떠 `_claude` 가 이미
None 으로 떨어질 수 있어 `_hdr_claude` 폴백이 그 전이 창을 덮는다(제보 2026-06-20).

무게: 이 모듈은 textual/rich 를 import 하지 않는다(서버 프로세스도 plugins.load() 로 같은
코드를 읽는다). 행 변형은 순수 문자열 연산뿐이라 무거운 의존이 없다."""
from __future__ import annotations


# ① 시작 팁 "Tip: Use /feedback to help us improve!" 를 숨긴다(사용자 요청 2026-06-17).
# 문구 중 식별성이 높은 부분으로만 매칭해 사용자가 직접 "/feedback" 을 친 줄 등 오탐을 피한다.
_FEEDBACK_TIP_MARK = "/feedback to help us improve"


def _blank_feedback_tip(rows):
    """render 된 행 목록에서 '/feedback 팁' 줄을 **폭을 유지한 채 공백으로** 바꾼다.
    각 행은 [text, style] 런 목록 — 매칭 행만 같은 폭의 공백 런으로 교체하고 나머지는
    원본 그대로 둔다. render 캐시의 행 객체를 공유하므로 in-place 변형 금지 → 매칭이
    있을 때만 새 리스트를 만들어 그 행만 교체한다(없으면 원본 객체 그대로 반환)."""
    out = None
    for i, row in enumerate(rows):
        text = "".join(t for t, _ in row)
        if _FEEDBACK_TIP_MARK in text:
            if out is None:
                out = list(rows)
            out[i] = [[" " * len(t), st] for t, st in row]
    return out if out is not None else rows


# ② 세션 종료 피드백 배너("How is Claude doing this session? (optional)" +
# "1: Bad  2: Fine  3: Good  0: Dismiss")를 **완전히** 숨긴다(사용자 요청 — 절대 안 보이게).
# 이 배너는 컴포저 위 비모달이라 안 닫아도 작업을 막지 않으므로 키 주입 없이 표시만 가린다
# (servermixin: 단일 Esc 가 종종 Dismiss 대신 작동 중인 턴을 interrupt 해 키 주입은 폐기,
# 2026-06-20). 평가옵션 줄은 오탐을 막으려 프롬프트 줄이 화면에 함께 있을 때만 가린다.
_FEEDBACK_PROMPT_MARK = "How is Claude doing this session"
_FEEDBACK_OPT_MARKS = ("Bad", "Fine", "Good", "Dismiss")


def _blank_feedback_banner(rows):
    """피드백 배너(프롬프트 줄 + 평가옵션 줄)를 폭 유지 공백으로 가린다(_blank_feedback_tip
    과 같은 규약: in-place 금지, 매칭 있을 때만 새 리스트). 프롬프트 줄이 화면에 없으면
    원본 그대로(핫패스 무영향 + 옵션 줄 오탐 방지)."""
    texts = ["".join(t for t, _ in row) for row in rows]
    if not any(_FEEDBACK_PROMPT_MARK in tx for tx in texts):
        return rows
    out = None
    for i, tx in enumerate(texts):
        if _FEEDBACK_PROMPT_MARK in tx or all(m in tx for m in _FEEDBACK_OPT_MARKS):
            if out is None:
                out = list(rows)
            out[i] = [[" " * len(t), st] for t, st in rows[i]]
    return out if out is not None else rows


class _ClaudeDisableFeedbackPlugin:
    name = "claude-disable-feedback"
    description = "Claude Code 피드백 문구 숨김 — 시작 팁·세션 종료 평가 배너 가림"
    category = "Claude"

    def server_filter_rows(self, server, pane, rows):
        """Claude 패널의 'Tip: Use /feedback …' 줄과 세션 종료 피드백 배너('How is Claude
        doing this session?' + 평가옵션)를 공백으로 가려 화면에 안 보이게 한다. Claude
        패널이 아니면 원본 그대로(핫패스 무영향).

        게이트는 `_claude`(현재 실행 중) **또는** `_hdr_claude`(디바운스 Claude 신호)로
        본다 — 피드백 배너는 세션이 **끝나는 순간** 떠 `_claude` 가 이미 None 으로 떨어질
        수 있는데(제보 2026-06-20: 그래서 배너가 안 가려지고 보였다), _hdr_claude
        는 그 전이 창에서 한동안 True 로 남아 배너 출현을 덮는다. 두 속성은 claude-code 가
        설치하므로 getattr 로 부드럽게 참조한다(없으면 no-op)."""
        if not (getattr(pane, "_claude", None) or getattr(pane, "_hdr_claude", None)):
            return rows
        rows = _blank_feedback_tip(rows)
        rows = _blank_feedback_banner(rows)
        return rows


PLUGIN = _ClaudeDisableFeedbackPlugin()
