"""claude-code 상태줄 표식을 **한 벌로 합친 것**이 종전 출력을 재현하는가 (M4 P6 후반).

# 왜 골든이 먼저인가

이 자리는 "없는 것을 만든다"가 아니라 **"두 벌을 합친다"** 였다. 정본(`clientstatus.
render_segs`)과 네이티브(GUI 가 날 필드로 자기가 조립하던 `claude_badge`)가 같은 것을
서로 다르게 그리고 있었고, 합치면 **둘 중 하나가 조용히 바뀐다**. 시계·달력에서 배운
규율이 그래서 "합치기 전 대조 + 골든"이다(08-02b: *"합치면 오라클을 하나 잃는다 —
두 벌이 서로를 검증하던 것"*).

여기 적힌 기대값은 **합치기 전 정본이 실제로 그리던 글**이다. 규칙을 옮긴 뒤에도
글자 하나까지 같아야 한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pytmuxlib import i18n  # noqa: E402
from pytmuxlib import plugins  # noqa: E402

plugins.load()


def _mod():
    """플러그인 디렉토리 이름에 `-` 가 있어 평범한 `import` 문이 안 된다 — 파일 경로로
    직접 물린다(플러그인 모듈을 재는 다른 테스트와 같은 방식)."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pytmuxlib", "plugins", "claude-code", "statusbadges.py")
    spec = importlib.util.spec_from_file_location("_cc_statusbadges", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _texts(fields, lang="ko"):
    i18n.set_locale(lang)
    try:
        return [(b["kind"], b["text"]) for b in _mod().badges(fields)]
    finally:
        i18n.set_locale("ko")


def test_the_merged_rule_reproduces_what_the_canonical_used_to_draw():
    # 합치기 전 정본이 그리던 것 — 모델 배지 + 5시간 사용률.
    got = _texts({"claude_active": True, "claude_model": "opus-5", "tok5h_pct": 12})
    assert got == [("model", "opus-5"), ("usage", "12%/5h 사용")], got


def test_a_sonnet_pane_shows_the_weekly_number_instead():
    # 5h ↔ 주간 Sonnet 은 상호배타다(서버가 한쪽만 채운다).
    got = _texts({"claude_active": True, "week_sonnet_pct": 40})
    assert got == [("usage", "40%/주(Sonnet)")], got


def test_an_unmeasured_pane_still_takes_the_spot():
    # ★ 비워 두면 사용자는 "사용량 표시가 없는 클라"를 본다(제보 2026-06-18).
    got = _texts({"claude_active": True})
    assert got == [("usage", "?%/5h 사용")], got


def test_nothing_is_drawn_for_a_pane_that_is_not_claude():
    assert _texts({"claude_active": False, "claude_model": "opus-5"}) == []


def test_the_countdown_and_the_warning_come_after_the_usage():
    # 순서는 뜻의 일부다 — 정본의 종전 순서 그대로.
    got = _texts({
        "claude_active": True, "tok5h_pct": 5,
        "claude_pending": {"eta": 30},
        "claude_warn": "⚠ 동일 결과 3회 반복 — 루프 의심",
        "claude_warn_kind": "repeat", "claude_warn_n": 3,
    })
    assert [k for k, _ in got] == ["usage", "pending", "warn"], got
    assert got[1][1] == " ⏳ 자동재개 30s(입력=취소) ", got[1]
    # ⚠ 뒤 공백 하나가 더 붙는다(컬러 이모지가 다음 칸을 먹는다 — 제보 2026-06-17).
    assert got[2][1] == " ⚠  동일 결과 3회 반복 — 루프 의심 ", got[2]


def test_a_countdown_does_not_mix_two_languages():
    """★ 이 오라클이 이 슬라이스의 함정을 붙잡는다.

    라벨을 **인자로** 넘기면 클라가 자기 로케일 포맷에 서버 로케일 조각을 끼워
    `⏳ 자동재개 30s (input=cancel)` 같은 것을 만든다. 라벨은 포맷 안에 있어야 한다."""
    spec = _mod().badges({"claude_pending": {"eta": 30}})[0]["i18n"]["text"]
    assert spec["args"] == {"eta": "30"}, spec
    assert "자동재개" in spec["fmt"], spec
    assert "label" not in spec["args"], "번역 대상이 인자로 샜다"


def test_every_composed_badge_carries_the_ingredients_to_be_retranslated():
    """자리가 있는 글은 **전부** 재료를 싣는다 — 안 실으면 영어 클라에 한국어가 뜬다.

    GUI 는 종전에 이 줄을 자기가 조립해 그 문제가 없었으므로, 재료 없이 옮기는 것은
    회귀다(로케일 ⓑ)."""
    for fields in (
        {"claude_active": True, "tok5h_pct": 12},
        {"claude_active": True, "week_sonnet_pct": 40},
        {"claude_pending": {"eta": 9}},
        {"claude_warn": "⚠ x", "claude_warn_kind": "repeat", "claude_warn_n": 2},
    ):
        for b in _mod().badges(fields):
            if "{" in b["text"] or any(c.isdigit() for c in b["text"]):
                assert "i18n" in b, f"재료 없이 나간다: {b}"
                assert b["i18n"]["text"]["fmt"], b


def test_the_english_locale_is_actually_reachable():
    """정본 카탈로그에 en 이 있으니 서버가 en 이면 영어가 나온다 — 재료의 전제다."""
    assert _texts({"claude_active": True, "tok5h_pct": 12}, lang="en") == [
        ("usage", "12%/5h used")
    ]
