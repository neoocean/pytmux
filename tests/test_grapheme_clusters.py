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
VS16 = chr(0xFE0F)
WARN = chr(0x26A0) + VS16      # Claude Code 의 경고 아이콘 `⚠️`


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
    # 얹힌 조각만으로는 폭이 안 는다(폭 0 도 군집의 일부다) — 단 VS16 이 그림 글자를
    # 넓히는 것은 예외다(pytmux-556).
    assert cellwidth.line_cells(WARN) == 2, "VS16 이 붙은 ⚠ 를 1칸으로 셌다"
    assert cellwidth.line_cells("A" + VS16) == 1, "그림 글자가 아닌 밑글자까지 넓혔다"


async def test_the_cluster_travels_whole_into_copy_and_search():
    """군집이 **한 셀의 글**이라 복사·검색이 그 전부를 집는다.

    ⛔ 낱개로 갈라 두면 복사본에 조각만 남고(그때 붙여넣기가 다른 글자가 된다) 검색도
    못 찾는다 — 와이드 글자의 연속칸을 공백으로 접었을 때 겪은 것과 같은 부류다.
    """
    screen = NativeScreen(24, 3)
    screen.draw("[" + FAMILY + "]")
    text = "".join(c.data for c in (screen.buffer[0][x] for x in range(24)))
    assert FAMILY in text, f"군집이 쪼개져 복사본에 안 남는다: {text!r}"


async def test_vs16_widens_a_pictograph_to_two_cells_in_the_server_grid():
    """서버 격자가 `⚠`+U+FE0F 에 **2칸**을 준다(pytmux-556 · 사용자 결정 2026-10-07).

    Claude Code(string-width)·Windows Terminal·tmux 3.5 가 모두 2칸으로 센다 — 격자만
    1칸이면 바깥 단말이 그 줄을 한 칸 넘쳐 그려 패널 테두리를 덮었고(pytmux-555), 앱이
    그 뒤에 맞춰 그린 표 끝이 한 칸 어긋났다.
    """
    assert _grid("|" + WARN + "|") == ["|", WARN, "", "|"], _grid("|" + WARN + "|")
    # 대조군 — `|한|` 과 닫는 글자 자리가 같다.
    assert _grid("|" + WARN + "|").index("|", 1) == _grid("|한|").index("|", 1)
    # ⛔ 대조군 — 그림 글자가 아닌 밑글자 · VS15 · 이미 2칸인 밑글자는 넓히지 않는다.
    assert _grid("|A" + VS16 + "|") == ["|", "A" + VS16, "|"], _grid("|A" + VS16 + "|")
    assert _grid("|⚠\ufe0e|") == ["|", "⚠\ufe0e", "|"], \
        _grid("|⚠\ufe0e|")
    assert _grid("|\U0001F600" + VS16 + "|") == ["|", "\U0001F600" + VS16, "", "|"]


async def test_vs16_at_the_right_edge_is_dropped_not_overflowed():
    """줄 끝 칸의 그림 글자에 VS16 이 오면 **넓힐 자리가 없어 버린다**.

    ⛔ 얹어 두면 그 셀이 「2칸 글」인데 뒤 칸이 없다 — 직렬화가 그 줄을 한 칸 넘쳐 싣고,
    바로 pytmux-555 의 모양(테두리가 덮인다)이 된다.
    """
    screen = NativeScreen(4, 3)
    screen.draw("abc⚠" + VS16)
    row = [screen.buffer[0][x].data for x in range(4)]
    assert row == ["a", "b", "c", "⚠"], row
    # 대조군 — 한 칸 여유가 있으면 넓힌다(위 버림이 「언제나 버린다」가 아니다).
    s2 = NativeScreen(5, 3)
    s2.draw("abc⚠" + VS16)
    assert [s2.buffer[0][x].data for x in range(5)] == ["a", "b", "c", WARN, ""]


async def test_the_client_lays_vs16_out_in_two_cells_and_sends_it_whole():
    """정본 클라 합성도 2칸으로 앉히고, **VS16 째로** 단말에 낸다(pytmux-555·556).

    ① 닫는 `|` 가 `|한|` 대조군과 같은 칸 ② render_line 이 `⚠️` 를 VS16 과 함께 낸다
    (2칸 셀이므로 단말이 2칸으로 그려도 격자와 맞다) ③ 그림 글자가 아닌 `A`+VS16 은
    1칸이라 VS16 을 떼고 낸다(pytmux-555 의 가드는 그대로).
    """
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = 7
        cols = 12
        app.layout = {"panes": [{"id": pid, "x": 0, "y": 1, "w": cols, "h": 4,
                                 "mouse": 0, "active": True}],
                      "dividers": [], "active": pid,
                      "cols": cols, "rows": 6}
        app._dispatch({"t": "screen", "pane": pid,
                       "rows": [[["|한|", {}]], [["|" + WARN + "|", {}]],
                                [["|A" + VS16 + "|", {}]]],
                       "cursor": None})
        app._composite()

        def closing(y):
            row = app.view._cells[1 + y][:cols]
            return [i for i, (ch, _st) in enumerate(row) if ch == "|"]

        assert closing(0) == [0, 3], f"대조군이 틀렸다 — 오라클이 공허하다: {closing(0)}"
        assert closing(1) == closing(0), (
            f"⚠️ 가 2칸을 안 썼다 — 대조군 {closing(0)} vs {closing(1)}")
        assert closing(2) == [0, 2], f"A+VS16 이 넓어졌다: {closing(2)}"
        out = "".join(seg.text for seg in app.view.render_line(2))
        assert out.startswith("|" + WARN + "|"), f"⚠️ 를 VS16 째로 안 냈다: {out!r}"
        out3 = "".join(seg.text for seg in app.view.render_line(3))
        assert VS16 not in out3 and out3.startswith("|A|"), (
            f"1칸 셀의 VS16 을 단말로 냈다 — 단말이 넓게 그리면 줄이 밀린다: {out3!r}")
        grid3 = "".join(ch for ch, _st in app.view._cells[3][:cols])
        assert "A" + VS16 in grid3, f"격자(복사본)에서 VS16 이 사라졌다: {grid3!r}"

    await _with_app(body, size=(20, 10))
