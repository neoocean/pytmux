"""문자소 군집이 **한 칸에 한 덩어리로** 선다 — pytmux-407 ⓐ 상시 오라클.

# 무엇을 재나

사람이 고른 규약(2026-09-01): **군집의 폭 = 밑글자의 폭**(tmux 3.4·현대 단말과 같다).
종전 격자는 **폭 0 조각만** 앞 칸에 얹었고([[test_zero_width_advance]] = pytmux-389),
제 폭을 가진 조각(둘째 이모지·둘째 지역 지시자·피부톤 수정자)에는 **새 칸을 줬다** —
그래서 `👨‍👩‍👧` 가 여섯 칸(셀 셋)이고 화면에는 **이모지 셋**이 떴다(실측 2026-09-01 ·
맥 GUI `--frame-dump`).

⛔ 이 판정은 **네 자리**가 읽는다: 서버 화면 모델 · 정본 클라 합성 · 재생 합성 ·
러스트 `proto::compose`. 하나만 고치면 그 줄이 클라마다 다르게 밀린다. 그래서 여기서
**세 층을 다 문다**(판정 함수 · 서버 격자 · 클라 합성). 러스트 쪽은
`client/crates/proto/tests/cluster_conformance.rs` 가 같은 표본을 정본에서 뽑아 잰다.
"""
import harness  # noqa: F401  (경로 설정)
from pytmuxlib import cellwidth
from pytmuxlib.nativescreen import NativeScreen

ZWJ = "‍"
FAMILY = "👨" + ZWJ + "👩" + ZWJ + "👧"
FLAG_KR = "🇰🇷"
THUMB = "👍🏿"


def _grid(text, cols=24):
    """서버 화면 모델에 찍고 **칸마다의 글**을 돌려준다(뒤 공백은 뗀다)."""
    screen = NativeScreen(cols, 3)
    screen.draw(text)
    cells = [screen.buffer[0][x].data for x in range(cols)]
    while cells and cells[-1] == " ":
        cells.pop()
    return cells


async def test_the_join_rule_answers_the_three_shapes_and_not_the_others():
    """판정 한 벌 — 세 갈래(ZWJ · 피부톤 · 지역 지시자)와 **대조군**."""
    j = cellwidth.joins_previous
    assert j("👨" + ZWJ, "👩"), "ZWJ 뒤의 그림 글자를 안 잇는다"
    assert j("👍", "🏿"), "피부톤 수정자를 안 잇는다"
    assert j("🇰", "🇷"), "지역 지시자 둘이 한 깃발이 아니다"
    # ⛔ 대조군 — 이것들이 참이면 「무엇이든 잇는」 판이고, 그러면 줄이 어긋난다.
    assert not j("👨", "👩"), "ZWJ 없이 이었다"
    assert not j("🇰🇷", "🇯"), "완성된 깃발 뒤에서 새 깃발이 시작해야 한다"
    assert j("🇰🇷🇯", "🇵"), "그 새 깃발의 짝은 이어야 한다"
    assert not j("क" + ZWJ, "ष"), (
        "ZWJ 를 이음 제어로 쓰는 글자까지 접었다 — 그러면 그 줄이 어긋난다")
    assert not j("", "👩"), "앞이 없는데 이었다"


async def test_the_server_grid_gives_a_cluster_one_cell():
    """서버 격자가 군집에 **밑글자만큼의 칸**을 준다."""
    assert _grid("|" + FAMILY + "|") == ["|", FAMILY, "", "|"], _grid("|" + FAMILY + "|")
    assert _grid("|" + FLAG_KR + "|") == ["|", FLAG_KR, "", "|"], _grid("|" + FLAG_KR + "|")
    assert _grid("|" + THUMB + "|") == ["|", THUMB, "", "|"], _grid("|" + THUMB + "|")
    # ⛔ 대조군 — 이어지지 않는 것은 종전대로 제 칸을 갖는다(안 그러면 글자가 사라진다).
    assert _grid("|👍👎|") == ["|", "👍", "", "👎", "", "|"], _grid("|👍👎|")
    assert _grid("|한글|") == ["|", "한", "", "글", "", "|"], _grid("|한글|")
    assert _grid("|🇰🇷🇯🇵|") == ["|", "🇰🇷", "", "🇯🇵", "", "|"], _grid("|🇰🇷🇯🇵|")


async def test_a_cluster_does_not_shift_the_rest_of_the_row():
    """정본 클라 합성에서 **닫는 글자가 대조군과 같은 칸**에 서는가.

    격자가 군집을 한 칸에 넣어도 클라가 낱개로 다시 앉히면 그 줄이 어긋난다 —
    pytmux-389 가 폭 0 에서 겪은 것과 **같은 부류**라 같은 방식으로 잰다.
    """
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = 7
        cols = 12
        app.layout = {"panes": [{"id": pid, "x": 0, "y": 1, "w": cols, "h": 4,
                                 "mouse": 0, "active": True}],
                      "dividers": [], "active": pid,
                      "cols": cols, "rows": 6}
        # 대조군은 **폭 2 한 글자**다 — 군집도 그만큼만 써야 한다.
        app._dispatch({"t": "screen", "pane": pid,
                       "rows": [[["|한|", {}]], [["|" + FAMILY + "|", {}]],
                                [["|" + FLAG_KR + "|", {}]]],
                       "cursor": None})
        app._composite()

        pane = next(p for p in app.layout["panes"] if p["id"] == pid)

        def closing(y):
            row = app.view._cells[pane["y"] + y][pane["x"]:pane["x"] + cols]
            return [i for i, (ch, _st) in enumerate(row) if ch == "|"]

        assert closing(0) == [0, 3], f"대조군이 틀렸다 — 오라클이 공허하다: {closing(0)}"
        assert closing(1) == closing(0), (
            "가족 이모지가 칸을 더 먹어 그 줄이 밀렸다 — "
            f"대조군 {closing(0)} vs 군집 줄 {closing(1)}")
        assert closing(2) == closing(0), (
            f"국기가 칸을 더 먹었다 — 대조군 {closing(0)} vs 깃발 줄 {closing(2)}")

    await _with_app(body, size=(20, 10))


async def test_a_line_is_measured_in_clusters_not_codepoints():
    """줄의 시각 폭은 **군집 수**다 — 낱개로 세면 계약(`폭 == cols`)이 거짓으로 깨진다."""
    assert cellwidth.line_cells("|" + FAMILY + "|") == 4, "군집을 낱개로 셌다"
    assert cellwidth.line_cells("|한|") == 4, "대조군이 틀렸다"
    assert cellwidth.line_cells("") == 0
    # 얹힌 조각만으로는 폭이 안 는다(폭 0 도 군집의 일부다).
    assert cellwidth.line_cells("⚠️") == 1


async def test_the_cluster_travels_whole_into_copy_and_search():
    """군집이 **한 셀의 글**이라 복사·검색이 그 전부를 집는다.

    ⛔ 낱개로 갈라 두면 복사본에 조각만 남고(그때 붙여넣기가 다른 글자가 된다) 검색도
    못 찾는다 — 와이드 글자의 연속칸을 공백으로 접었을 때 겪은 것과 같은 부류다.
    """
    screen = NativeScreen(24, 3)
    screen.draw("[" + FAMILY + "]")
    text = "".join(c.data for c in (screen.buffer[0][x] for x in range(24)))
    assert FAMILY in text, f"군집이 쪼개져 복사본에 안 남는다: {text!r}"
