"""Claude Code 시작 배너 **마스코트가 한 색인가** — 서버 쪽 정본 고정(pytmux-205).

# 왜 이 테스트가 생겼나

제보(pytmux-205): pytmux 안에서 Claude Code 를 띄우면 마스코트가 코랄이 아니라
**자홍/분홍**으로 나온다. 모양은 멀쩡하고 색만 틀렸다.

제보 스크린샷을 픽셀로 재 보면 틀어진 것은 *색 하나*가 아니라 **줄**이다:

| 줄 | 마스코트 | 같은 줄의 회색 글자 |
| --- | --- | --- |
| 1 (`Claude Code v…`) | `#d77757` (정확) | `#999999` (정확) |
| 2 (`Opus 5 · …`)     | `#f4005f` (틀림) | `#625e4c` (틀림) |
| 3 (경로)             | `#f4005f` (틀림) | `#625e4c` (틀림) |

즉 그 줄에 있는 **모든 색**이 함께 틀어졌다. 그리고 틀어진 값(`#f4005f`·`#625e4c`)은
이 저장소에도 Claude Code 번들에도 **없는 색**이고, 원본 색의 어떤 채널별 함수로도
안 나온다(같은 입력 `153` 이 `98`·`94`·`76` 세 값으로 갔다). 즉 값이 망가진 것이
아니라 **다른 색이 들어온** 것이다 — 표시 계층(클라 → 호스트 터미널) 쪽이다.

여기서 고정하는 것은 그 **앞쪽 절반**이다: 정본이 내는 바이트를 그대로 먹였을 때
서버가 세 줄 전부를 `#d77757` 한 색으로 담는가. 실측(2026-08-16)으로 담는다 —
그래서 SGR 파싱과 와이어 직렬화는 이 결함의 자리가 **아니다**. 그 사실이 조용히
뒤집히면(예: `select_graphic_rendition` 의 truecolor 3연 pop 순서, 콜론식 SGR 처리)
같은 증상이 서버 쪽에서도 날 수 있으므로 오라클로 남긴다.

# 픽스처 출처

`tests/fixtures/claude/banner_mascot.ansi` — 이 맥에서 Claude Code 2.1.233 을 pty 로
띄워 뜬 **실제 시작 배너 3줄**(`FORCE_COLOR=3` · `COLORTERM=truecolor`). 2.1.229 로도
같은 SGR 이 나오는 것을 확인했다. 홈 경로만 `~/src/example` 로 바꿨다(공개 미러).

⚠ 세 줄의 SGR 구조가 서로 다르고, **그 차이가 제보의 갈림과 정확히 겹친다**:
1번 줄만 마스코트와 회색 글자 사이에 `ESC[39m`(기본색 복귀)가 있고, 2·3번 줄은
truecolor → truecolor 로 곧장 넘어간다. 그래서 픽스처를 손으로 다듬지 않는다 —
그 `39` 하나가 이 픽스처의 값어치다.
"""
import os

import harness  # noqa: F401 (경로 설정)
from pytmuxlib.model import Pane

MASCOT_FG = "#d77757"          # 정본이 내는 코랄 = SGR 38;2;215;119;87
SUBTLE_FG = "#999999"          # 같은 배너의 회색 = SGR 38;2;153;153;153
MASCOT_COLS = 9                # 마스코트가 차지하는 왼쪽 칸 수(가장 넓은 2번 줄 기준)

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "claude", "banner_mascot.ansi")


def _banner_rows():
    """픽스처를 실제 Pane 에 먹여 와이어 런(=클라가 받는 것)으로 돌려준다.

    정본은 원점 복귀 뒤 **한 줄 내려서** 배너를 그리므로 첫 줄은 비어 있다 —
    픽스처를 손대지 않고 여기서 건너뛴다(`\\x1b[H\\r\\x1b[1B` 도 정본의 일부다)."""
    # ⛔ 반드시 바이너리로 읽는다 — 텍스트 모드는 정본의 `\\r` 을 `\\n` 으로 바꿔서
    #    줄이 한 칸씩 더 내려가고, 그러면 픽스처가 다른 화면이 된다.
    with open(_FIXTURE, "rb") as fh:
        data = fh.read()
    pane = Pane(-1, -1, 60, 5)
    pane.feed(data)
    rows, _ = pane.render(True)
    return rows[1:4]


def _cell_fg(runs, col):
    """런 목록에서 `col` 번째 칸의 전경색(없으면 None)."""
    x = 0
    for text, style in runs:
        if x <= col < x + len(text):
            return style.get("f")
        x += len(text)
    return None


async def test_fixture_is_the_real_banner():
    """전제 붕괴 감지 — 픽스처가 실제로 마스코트 3줄이어야 한다."""
    rows = _banner_rows()
    assert len(rows) == 3, f"3줄이 아니다: {len(rows)}"
    text = "".join(t for runs in rows for t, _ in runs)
    assert "Claude Code" in text, text
    # 마스코트는 블록·사분면 글자로 그려진다(pytmux-177 이 보는 그 표면).
    blocks = set("▀▄█▌▐▘▝▖▗▛▜▙▟")
    assert blocks & set(text), "블록 글자가 하나도 없다 — 픽스처가 배너가 아니다"


async def test_mascot_is_one_colour_on_every_row():
    """마스코트 세 줄이 **같은 한 색**이어야 한다(제보 pytmux-205 의 본론).

    줄마다 SGR 구조가 다른데(1번 줄만 `ESC[39m` 이 낀다) 그것이 색을 갈라서는 안 된다.
    """
    seen = {}
    for y, runs in enumerate(_banner_rows()):
        for col in range(MASCOT_COLS):
            fg = _cell_fg(runs, col)
            if fg is None:
                continue            # 마스코트 밖 여백(줄마다 폭이 다르다)
            seen.setdefault(fg, []).append((y, col))
    assert list(seen) == [MASCOT_FG], f"마스코트가 한 색이 아니다: { {k: v[:3] for k, v in seen.items()} }"
    rows_hit = {y for y, _ in seen[MASCOT_FG]}
    assert rows_hit == {0, 1, 2}, f"세 줄 다 안 잡혔다: {sorted(rows_hit)}"


async def test_subtle_text_survives_a_truecolor_to_truecolor_switch():
    """`39` 없이 truecolor → truecolor 로 넘어가도 뒤 색이 온전해야 한다.

    2·3번 줄이 정확히 그 모양이고, 제보에서 **틀어진 것도 그 두 줄**이다.
    """
    for y, runs in enumerate(_banner_rows()):
        greys = [t for t, st in runs if st.get("f") == SUBTLE_FG]
        assert greys, f"{y + 1}번 줄에 회색 런이 없다: {runs}"
    rows = _banner_rows()
    # 1번 줄은 `39` 를 지나고 2·3번 줄은 안 지난다 — 그래도 같은 값이어야 한다.
    assert [t for t, st in rows[1] if st.get("f") == SUBTLE_FG][0].startswith("Opus")
    assert [t for t, st in rows[2] if st.get("f") == SUBTLE_FG][0].startswith("~/")


async def test_black_background_run_does_not_leak_into_the_next_run():
    """마스코트 눈의 `48;2;0;0;0` 이 `49` 뒤 칸까지 물들이면 안 된다."""
    rows = _banner_rows()
    for y, runs in enumerate(rows):
        for text, style in runs:
            if style.get("f") != MASCOT_FG:
                continue
            if style.get("b") == "#000000":
                assert set(text) <= set("▛▜█▌▐▀▄"), \
                    f"{y + 1}번 줄: 검은 배경이 엉뚱한 칸에 붙었다 {text!r}"
