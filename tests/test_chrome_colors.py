"""위젯 의미색이 읽히나 · 두 클라가 같은 색을 쓰나 (pytmux-542).

pytmux-205 가 패널용 ANSI 표를 표준 16색으로 바꾸자, 같은 필터를 지나는 **위젯의 이름
색**까지 어두워져 토큰 판의 경고 빨강이 판 바탕 위 1.4:1 이 됐다. 위젯은 이제
`pytmuxlib.chromecolor` 의 hex 를 쓴다.

되돌리면 실패해야 하는 오라클:
  · 판이 다시 ANSI 이름을 쓰면 → test_the_token_panel_paints_hex_not_ansi_names (호출부)
  · 표에 어두운 색을 넣으면 → test_meaning_colors_are_readable_on_the_panel
  · GUI 크롬 색과 갈라지면 → test_the_gui_chrome_uses_the_same_meaning_colors
  · 패널 셀 쪽 표준 ANSI(pytmux-205)는 그대로다 → test_panel_cells_keep_the_standard_ansi_table
"""
import os
import re

import harness  # noqa: F401  (러너 위생 · 경로 설정)

from pytmuxlib import chromecolor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 토큰 판의 줄 바탕(이슈 본문의 실측 두 값).
PANEL_BGS = ("#272727", "#1c1c1c")


def _lum(hexcolor):
    h = hexcolor.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = out
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_meaning_colors_are_readable_on_the_panel():
    # 글자(5h%·1w%·경고)는 4.5:1, 막대 색은 3:1(이슈의 관문).
    for name in ("OK", "WARN", "ERROR", "TURN"):
        c = getattr(chromecolor, name)
        for bg in PANEL_BGS:
            assert _contrast(c, bg) >= 4.5, (name, c, bg, _contrast(c, bg))
    for name, c in chromecolor.NAMED.items():
        for bg in PANEL_BGS:
            assert _contrast(c, bg) >= 3.0, (name, c, bg, _contrast(c, bg))


def test_the_gui_chrome_uses_the_same_meaning_colors():
    """두 클라가 같은 판을 같은 색으로 — GUI `theme.rs` 를 직접 읽어 맞댄다."""
    src = open(os.path.join(ROOT, "client", "crates", "gui", "src", "theme.rs"),
               encoding="utf-8").read()
    for name in ("OK", "WARN", "ERROR", "TURN"):
        m = re.search(rf"pub const {name}: ColorU = c\(0x([0-9a-f]{{2}}), "
                      rf"0x([0-9a-f]{{2}}), 0x([0-9a-f]{{2}})\);", src)
        assert m, f"theme.rs 에서 {name} 를 못 찾았다"
        assert getattr(chromecolor, name) == "#" + "".join(m.groups()), name


def test_the_token_panel_paints_hex_not_ansi_names():
    """★ 호출부 — 표만 고치고 판이 이름 색을 쓰면 여기서 운다."""
    from rich.color import ColorType
    from rich.style import Style
    import importlib
    scr = importlib.import_module("pytmuxlib.plugins.claude-code.screens")
    for pct in (10, 60, 97):
        color, style = scr._pct_style(pct)
        st = Style.parse(style)
        assert st.color.type == ColorType.TRUECOLOR, (pct, style)
    # 막대 — 단일 톤과 모델 구성비 둘 다.
    tok_bar = scr.TokenLogScreen._tok_bar
    for models in (None, {"haiku": 3, "sonnet": 3, "opus": 3, "fable": 3}):
        t = tok_bar(100, 100, 12, models)
        styles = [s.style for s in t.spans] or [t.style]
        for s in styles:
            st = Style.parse(s) if isinstance(s, str) else s
            assert st.color is not None and st.color.type == ColorType.TRUECOLOR, s


def test_the_usage_view_urgency_is_hex():
    import importlib
    from rich.color import ColorType
    from rich.style import Style
    scr = importlib.import_module("pytmuxlib.plugins.claude-token-usage-view.screen")
    for token, style in scr._URGENCY_STYLE.items():
        assert Style.parse(style).color.type == ColorType.TRUECOLOR, (token, style)


def test_panel_cells_keep_the_standard_ansi_table():
    """대조군 — 패널 앱이 낸 `ESC[31m` 은 여전히 표준 `#800000` 이다(pytmux-205 회귀 0)."""
    from pytmuxlib.clientutil import ANSI_PALETTE_THEME
    assert tuple(ANSI_PALETTE_THEME.ansi_colors[1]) == (0x80, 0, 0)
