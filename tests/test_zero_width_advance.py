"""폭 0 글자(변이 선택자·ZWJ·결합 표시)가 **칸을 안 먹는다** — pytmux-389 상시 오라클.

# 무엇을 재나

제보(2026-08-24 · 맥 GUI `--frame-dump` 실측): `U+FE0F` 가 든 줄만 **한 칸씩 오른쪽으로
밀렸다**. 뿌리는 「칸을 나눌 때 **폭**을 묻는다」였다 — `cellwidth.char_cells` 는 러스트
`proto::compose::char_cells` 와 글자 하나까지 같아야 하는 계약이 있어 폭 0 도 `1` 로
떨어뜨린다(그 값을 고치면 다른 자리가 조용히 어긋난다). 자리를 나눌 때 물어야 하는 것은
다른 질문이다 — *"다음 글자를 몇 칸 밀어내나"* = `cellwidth.char_advance`.

⛔ **이 결함은 두 클라에 다 있었다.** 러스트 쪽은 `client/crates/` 의 오라클이 지고,
여기는 **정본(파이썬) 합성**(`clientio._composite`)을 진다. 한쪽만 고치면 같은 상자에서
두 클라가 다르게 그린다.

# 왜 손으로 지은 런을 넣나 (이 저장소의 평소 규약과 다르다)

옆 오라클(`test_wide_char_no_duplication`)은 **서버가 실제로 내보내는 런**을 태운다 —
그게 낫다. 여기서는 못 한다: depot HEAD 의 서버 격자는 변이 선택자를 만나면 **그 줄의
나머지를 통째로 버린다**(실측: `|⚠️|` → `|⚠`). 그 절단을 고치는 것이 pytmux-270 이고
그 CL 은 아직 미제출이라, 서버를 태우면 이 시험은 «밀림»이 아니라 «절단»을 재게 된다.

와이어가 실을 모양은 이슈 본문이 못박았다 — `model._serialize_row` 가 셀의 `data` 를
이어 붙이므로 런 글은 `"⚠️"` 처럼 **폭 0 글자를 품은 한 덩어리**로 온다. 그것을
그대로 넣는다. ★ 절단이 고쳐지면 이 머리말을 지우고 서버를 태우는 편으로 옮길 것.
"""
import harness  # noqa: F401  (경로 설정)
from pytmuxlib.clientutil import _char_advance, _char_cells

SEL = "\ufe0f"          # 변이 선택자(이모지 표현)
ZWJ = "\u200d"          # 제로폭 접합자


async def test_the_width_table_and_the_advance_table_answer_different_questions():
    """두 자가 **다른 질문**에 답한다 — 같아지면 한쪽이 죽은 것이다."""
    assert _char_cells(SEL) == 1, "폭 표의 계약(폭 0 도 1)이 깨졌다 — 러스트와 갈린다"
    assert _char_advance(SEL) == 0, "선택자가 칸을 밀어낸다"
    assert _char_advance(ZWJ) == 0, "ZWJ 가 칸을 밀어낸다"
    assert _char_advance("A") == 1 and _char_advance("한") == 2, "평범한 글자가 틀렸다"
    # 비출력 문자는 «폭 0» 이 아니다 — 폭을 모르는 것과 0 인 것은 다르다.
    assert _char_advance("\t") == 1, "폭을 모르는 글자를 0 으로 접었다"


async def test_a_selector_does_not_shift_the_rest_of_the_row():
    """합성 격자에서 **닫는 글자가 대조군과 같은 칸**에 서는가."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        pid = 7
        cols = 12
        app.layout = {"panes": [{"id": pid, "x": 0, "y": 1, "w": cols, "h": 3,
                                 "mouse": 0, "active": True}],
                      "dividers": [], "active": pid,
                      "cols": cols, "rows": 5}
        plain = "|A|"
        marked = "|\u26a0" + SEL + "|"
        app._dispatch({"t": "screen", "pane": pid,
                       "rows": [[[plain, {}]], [[marked, {}]]], "cursor": None})
        app._composite()

        pane = next(p for p in app.layout["panes"] if p["id"] == pid)

        def closing(y):
            row = app.view._cells[pane["y"] + y][pane["x"]:pane["x"] + cols]
            at = [i for i, (ch, _st) in enumerate(row) if ch == "|"]
            return at

        assert closing(0) == [0, 2], f"대조군이 틀렸다 — 오라클이 공허하다: {closing(0)}"
        assert closing(1) == closing(0), (
            "폭 0 글자가 칸을 먹어 그 줄이 밀렸다 — "
            f"대조군 {closing(0)} vs 선택자 줄 {closing(1)}")

    await _with_app(body, size=(20, 8))
