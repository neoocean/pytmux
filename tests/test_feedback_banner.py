"""Claude 세션 종료 피드백 배너 숨김 회귀(사용자 요청 2026-06-18 — 절대 안 보이게).

claude-code 는 하이픈 패키지라 importlib 로 가져온다(test_claude 의 stale
`import pytmuxlib.claude` 경로와 무관하게 이 파일은 정상 collect 된다)."""
import importlib

import harness  # noqa: F401  (sys.path 주입)

cc = importlib.import_module("pytmuxlib.plugins.claude-code")


def _row(text, st=None):
    return [[text, st or {}]]


def test_blank_feedback_banner_hides_prompt_and_options():
    """프롬프트 줄 + 평가옵션 줄을 폭 유지 공백으로 가린다. 둘 다 보이지 않게."""
    rows = [
        _row("some output above"),
        _row("How is Claude doing this session? (optional)"),
        _row(" 1: Bad   2: Fine   3: Good   0: Dismiss"),
        _row("composer line"),
    ]
    out = cc._blank_feedback_banner(rows)
    assert out is not rows, "매칭 있으면 새 리스트"
    assert "".join(t for t, _ in out[0]) == "some output above"   # 무관 줄 보존
    assert "".join(t for t, _ in out[1]).strip() == "", "프롬프트 줄 공백화"
    assert "".join(t for t, _ in out[2]).strip() == "", "평가옵션 줄 공백화"
    assert "".join(t for t, _ in out[3]) == "composer line"
    # 폭(셀 수) 보존 — 레이아웃이 흔들리지 않게.
    assert len("".join(t for t, _ in out[1])) == len("How is Claude doing this session? (optional)")
    assert len("".join(t for t, _ in out[2])) == len(" 1: Bad   2: Fine   3: Good   0: Dismiss")


def test_blank_feedback_banner_noop_without_prompt():
    """프롬프트 줄이 없으면(옵션 비슷한 텍스트만 있어도) 가리지 않는다 — 오탐 방지·
    원본 객체 그대로(핫패스 무복사)."""
    rows = [_row("1: Bad 2: Fine 3: Good 0: Dismiss (just text)"),
            _row("normal")]
    assert cc._blank_feedback_banner(rows) is rows


def test_server_filter_rows_hides_banner_on_claude_pane_only():
    """server_filter_rows 가 Claude 패널에서 배너를 가리고, 비-Claude 패널은 그대로."""
    rows = [_row("How is Claude doing this session? (optional)"),
            _row(" 1: Bad   2: Fine   3: Good   0: Dismiss")]

    class _P:
        def __init__(self, claude):
            self._claude = claude

    # 비-Claude → 동일 객체(무영향)
    assert cc.PLUGIN.server_filter_rows(None, _P(None), rows) is rows
    # Claude → 배너 두 줄 공백
    out = cc.PLUGIN.server_filter_rows(None, _P("idle"), rows)
    assert "".join(t for t, _ in out[0]).strip() == ""
    assert "".join(t for t, _ in out[1]).strip() == ""
