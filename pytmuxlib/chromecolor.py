"""클라 **위젯**(판·막대·배지)의 의미색 — ANSI 이름이 아니라 hex 다(pytmux-542).

★ 왜 따로 있나
  Rich 의 이름 색(`"green"`·`"red"` …)은 **표준 ANSI 0–7** 이다. Textual 은 그 색을
  `App.ansi_theme_dark` 표로 옮겨 적는데, pytmux-205 가 그 표를 표준(xterm/VGA) 16색으로
  바꿨다 — **패널 앱이 낸 ANSI 색**을 바로잡으려던 것이다(`clientutil.ANSI_PALETTE_THEME`).
  그 필터는 「누가 낸 색인가」를 안 가린다. 그래서 클라 자신의 위젯이 쓰던 이름 색도
  같이 어두워져, 토큰 판의 경고 빨강(`#800000`)이 판 바탕 위 **1.4:1** 이 됐다.

  ⇒ 근거가 둘인 값을 한 표가 지면 한쪽을 정하는 순간 다른 쪽이 끌려간다. 패널 셀은
  **정본 충실도**(ANSI 표)로, 위젯은 **가독**(이 표)으로 각자 근거를 갖는다. 위젯은
  이름 대신 여기 hex 를 쓴다 — hex 는 그 필터를 안 지난다.

★ 값은 GUI 크롬과 **같다**(`client/crates/gui/src/theme.rs` 의 `OK`·`WARN`·`ERROR`·`TURN`
  — GUI 가 pytmux-187 에서 똑같은 결함을 겪고 되찾은 tokyonight 색). 두 클라가 같은
  판을 같은 색으로 그린다. 대조는 `tests/test_chrome_colors.py` 가 그 파일을 직접 읽어 한다.

⛔ 여기에 ANSI 이름을 다시 넣지 마라 — 위 결함이 그대로 되살아난다.
"""

# 의미 이름 → 색. 판 바탕(#1c1c1c~#272727) 위 글자 4.5:1 이상(`tests/test_chrome_colors.py`).
OK = "#9ece6a"
WARN = "#e0af68"
ERROR = "#f7768e"
TURN = "#bb9af7"

# 이름 색을 쓰던 자리가 **같은 이름**으로 옮겨 오게 하는 표(구분용 색 — 모델 막대 등).
NAMED = {
    "green": OK,
    "yellow": WARN,
    "red": ERROR,
    "magenta": TURN,
    "cyan": "#7dcfff",
    "blue": "#7aa2f7",
}
