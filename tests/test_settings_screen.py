"""설정 화면(SettingsScreen) 레이아웃 회귀 — 카테고리 헤더 앞 빈 줄(사용자 요청
2026-06-18). 화면 mount 없이 _flat/_row_text 만 검사(생성자에서 평탄화 완료)."""
import harness  # noqa: F401  (sys.path 주입)

from pytmuxlib import i18n
from pytmuxlib.clientscreens import SettingsScreen


def test_blank_line_before_each_category_except_first():
    """카테고리 첫 행의 _row_text 는 헤더(── cat ──)를 포함하고, **첫 카테고리(idx 0)를
    제외한** 모든 카테고리는 그 앞에 빈 줄('\\n' 선두)을 둔다 — 묶음 시각 구분."""
    i18n.set_locale("en")
    s = SettingsScreen()
    firsts = [idx for idx in range(len(s._flat)) if s._flat[idx][1]]
    assert len(firsts) >= 3, "카테고리가 여럿이어야 의미 있는 회귀"
    assert firsts[0] == 0, "첫 행은 첫 카테고리"
    # 첫 카테고리: 빈 줄 없이 헤더로 시작
    t0 = s._row_text(0)
    assert not t0.startswith("\n"), "첫 카테고리 앞엔 빈 줄 없음"
    assert "──" in t0.split("\n", 1)[0], "첫 줄이 카테고리 헤더"
    # 나머지 카테고리: 선두가 빈 줄, 그다음 줄이 헤더
    for idx in firsts[1:]:
        t = s._row_text(idx)
        assert t.startswith("\n"), f"카테고리 행 {idx} 앞에 빈 줄이 있어야"
        lines = t.split("\n")
        assert lines[0] == "" and "──" in lines[1], \
            f"행 {idx}: 빈 줄 다음에 헤더"


def test_non_first_rows_have_no_header_or_blank():
    """카테고리 비-첫 행(설정 항목)은 헤더/빈 줄을 달지 않는다(중복 방지)."""
    i18n.set_locale("en")
    s = SettingsScreen()
    for idx in range(len(s._flat)):
        if not s._flat[idx][1]:           # first=False
            t = s._row_text(idx)
            assert not t.startswith("\n"), f"비-첫 행 {idx} 앞 빈 줄 금지"
            assert "──" not in t.split("\n")[0], f"비-첫 행 {idx} 헤더 금지"
