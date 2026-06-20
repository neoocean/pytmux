"""Claude 피드백 문구 숨김 회귀(사용자 요청 2026-06-17·2026-06-18 — 절대 안 보이게).

이 기능은 claude-code 에서 분리된 별도 플러그인 claude-disable-feedback 가 소유한다
(2026-06-20). 하이픈 패키지라 importlib 로 가져온다."""
import importlib

import harness  # noqa: F401  (sys.path 주입)

cc = importlib.import_module("pytmuxlib.plugins.claude-disable-feedback")


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


def test_blank_feedback_tip_hides_line():
    """Claude 패널의 'Tip: Use /feedback …' 줄은 폭을 유지한 채 공백으로 가려진다
    (사용자 요청 2026-06-17). 팁이 없으면 원본 객체를 그대로 돌려준다(render 캐시 보호·
    핫패스 무복사). 사용자가 직접 친 '/feedback' 단독 줄은 안 가린다(오탐 방지)."""
    tip = "Tip: Use /feedback to help us improve!"
    rows = [[["hello", {}]], [[tip, {"dim": 1}]], [["world", {}]]]
    out = cc._blank_feedback_tip(rows)
    assert "".join(t for t, _ in out[1]) == " " * len(tip)   # 같은 폭 공백
    assert out[1][0][1] == {"dim": 1}                         # 스타일 보존
    assert out[0] is rows[0] and out[2] is rows[2]            # 다른 행 그대로
    assert rows[1][0][0] == tip                               # 원본 미변형(캐시 보호)
    plain = [[["nothing here", {}]]]
    assert cc._blank_feedback_tip(plain) is plain             # 팁 없음 → 동일 객체
    typed = [[["please run /feedback", {}]]]
    assert cc._blank_feedback_tip(typed) is typed             # 직접 친 /feedback 은 보존


def test_server_filter_rows_hides_tip_on_claude_pane_only():
    """server_filter_rows 훅은 Claude 패널만 '/feedback 팁'을 가리고, 비-Claude 패널은
    원본 그대로 둔다(핫패스 무영향)."""
    tip = "Tip: Use /feedback to help us improve!"
    rows = [[[tip, {}]]]

    class _P:
        _claude = None

    assert cc.PLUGIN.server_filter_rows(None, _P(), rows) is rows   # 비-Claude → 그대로
    p = _P()
    p._claude = "idle"
    out = cc.PLUGIN.server_filter_rows(None, p, rows)
    assert "".join(t for t, _ in out[0]) == " " * len(tip)
