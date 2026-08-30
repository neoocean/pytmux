"""블록 폰트 글리프 ⊆ GUI 사각형 표 — **두 언어를 가로지르는 가드**.

# 무엇을 막나

정본의 큰 시계·달력은 블록 원소(`█▀▄` 등)로 그림을 그린다(`pytmuxlib/blockfont.py`).
GUI 는 그 글자를 **글꼴로 그리지 않고 사각형으로 직접 칠한다**
(`client/crates/proto/src/canvas.rs::BLOCK_FILLS` → `splitter::paint_blocks`) — 보조 글꼴의
진폭이 칸 너비의 정수배가 아니라 글꼴에 맡기면 행마다 어긋나기 때문이다(pytmux-55·177).

그러니 **표에 없는 글리프를 폰트 자산에 더하면 그 글자만 조용히 옛 결함을 겪는다.**
실제로 그 일이 있었다: 사분면(U+2596~U+259F)이 표에서 빠져 있던 동안 Claude 마스코트의
스무 칸 중 여덟 칸이 폴백 글꼴로 갔고, 증상은 「행마다 가로로 밀린다」였다(pytmux-177).

⇒ 이 시험은 **자산이 앞서 나가는 것**을 잡는다. 자산은 파이썬이고 표는 Rust 라 어느 쪽
스위트도 혼자서는 못 본다 — 트리를 합친 뒤에 생긴 가드 자리다(루트 CLAUDE.md §표면이
움직이면 세 소비자가 같이 깨진다).
"""
import os
import re

import harness  # noqa: F401  (sys.path 주입)
from run import skip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _canon_glyphs():
    """정본 블록 폰트가 실제로 쓰는 글자들(공백 제외)."""
    from pytmuxlib import blockfont
    out = set()
    for font in (blockfont._CLOCK_FONT, blockfont._CLOCK_FONT_BIG):
        for rows in font.values():
            for row in rows:
                out.update(ch for ch in row if ch != " ")
    return out


def _gui_table():
    """`BLOCK_FILLS` 표가 아는 글자들."""
    path = os.path.join(ROOT, "client", "crates", "proto", "src", "canvas.rs")
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    start = src.find("pub const BLOCK_FILLS")
    assert start >= 0, "BLOCK_FILLS 표를 못 찾았다 — 이름이 바뀌었으면 이 가드도 옮길 것"
    end = src.find("\n];", start)
    body = src[start:end]
    return set(re.findall(r"\('(.)',", body))


async def test_every_block_glyph_the_canon_draws_has_a_rectangle():
    table = _gui_table()
    if table is None:
        skip("client/ 가 없다 — Rust 클라 트리 없이 정본만 있는 판")
    assert len(table) >= 20, f"표가 {len(table)}자뿐이다 — 파싱이 깨졌다(공허 통과 방지)"
    canon = _canon_glyphs()
    assert canon, "정본 폰트에서 글리프를 하나도 못 읽었다 — 이 가드가 공허해졌다"
    missing = sorted(canon - table)
    assert not missing, (
        "정본 블록 폰트가 쓰는 글자가 GUI 사각형 표에 없다 — 그 글자만 폴백 글꼴로 가고,\n"
        "증상은 「큰 시계·달력이 행마다 밀린다」다(pytmux-55·177).\n"
        f"  빠진 글자: {' '.join(missing)} "
        f"({' '.join('U+%04X' % ord(c) for c in missing)})\n"
        "  고치는 자리: client/crates/proto/src/canvas.rs 의 BLOCK_FILLS"
    )


async def test_the_guard_would_notice_a_new_glyph():
    """가드가 실제로 무는지 — **대조군**(루트 CLAUDE.md §부정 단언만 있는 오라클).

    표에 **일부러 없는** 글자로 잰다: 팔분면(U+1FB00)은 지금 표에 없고, 없는 것이 맞다
    (도는 claude 의 화면에서 한 글자도 안 나왔다 — `BLOCK_FILLS` 머리말의 실측).
    자산이 그 글자를 쓰기 시작하면 위 시험이 그것을 이름으로 지목해야 한다.
    """
    table = _gui_table()
    if table is None:
        skip("client/ 가 없다")
    absent = "🬀"
    assert absent not in table, "대조군 글자가 표에 들어왔다 — 다른 글자로 바꿀 것"
    # 위 시험이 쓰는 차집합 셈으로 그 글자가 «빠진 것»으로 잡히는지.
    assert sorted({absent} | set("█") - table) == [absent], "가드가 빠진 글자를 못 짚는다"
