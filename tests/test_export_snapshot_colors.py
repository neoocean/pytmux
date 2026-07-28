"""재시작 스냅샷·표시 계층이 **서버가 낼 수 있는 모든 색 이름**을 잃지 않는가(§10-12 조사).

배경. 서버 화면 모델의 색 이름은 pyte 계보이고, 그 표에는 **원 pyte 오타가 일부러
보존**돼 있다(`vtconst.BG_AIXTERM[105] = "bfightmagenta"` — 렌더 바이트 동일성).
이름을 **해석하는** 소비자는 그 오타를 알아야 하는데, 2026-07-27 에는 표시 계층
(`clientutil._rich_color`)만 고쳤고 **스냅샷 계층(`model._sgr_color`)은 빠져 있었다** —
세션유지 재시작(Windows 아웃오브프로세스 pty-host 경로)을 지나면 밝은 마젠타 배경이
**색만 사라진 채** 남고, 앱이 다시 그리기 전까지 안 고쳐졌다. 글자는 멀쩡해서 조용하다.

오라클을 이름 목록으로 적지 않는다. **vtconst 의 표에서 SGR 코드를 뽑아** 실제 패널에
먹이고, 서버가 내놓은 이름을 그대로 두 소비자에 통과시킨다 — 표가 바뀌면 이 테스트가
따라온다(손으로 적은 목록은 안 따라온다).

되돌리면 실패해야 하는 것:
  · `model._sgr_color` 의 PYTE_COLOR_TYPOS 정규화를 빼면 → test_export_roundtrip… 실패
    (SGR 105 배경이 왕복에서 사라진다)
  · `clientutil._COLOR_ALIASES` 를 비우면 → test_display_layer… 실패(93/103/105)
"""
import harness  # noqa: F401
from pytmuxlib import vtconst
from pytmuxlib.clientutil import _rich_color
from pytmuxlib.model import Pane

_MASCOT = "▀▄█"          # 제보된 표면(반칸 블록)과 같은 모양의 글자


def _sgr_cases():
    """(라벨, SGR 파라미터, 배경인가) — vtconst 의 표에서 뽑는다(손으로 나열 금지).

    표의 `default`(39/49)는 **색이 없는 것이 정답**이라 제외한다."""
    for tbl, is_bg, what in ((vtconst.FG_ANSI, False, "전경"),
                             (vtconst.FG_AIXTERM, False, "전경"),
                             (vtconst.BG_ANSI, True, "배경"),
                             (vtconst.BG_AIXTERM, True, "배경")):
        for code in sorted(tbl):
            if tbl[code] == "default":
                continue
            yield f"SGR {code} {what}", str(code), is_bg
    for i in range(len(vtconst.FG_BG_256)):
        yield f"256 전경 {i}", f"{vtconst.FG_256};5;{i}", False
        yield f"256 배경 {i}", f"{vtconst.BG_256};5;{i}", True
    for r, g, b in [(0, 0, 0), (255, 255, 255), (113, 184, 141), (215, 119, 87)]:
        yield f"truecolor 전경 {r},{g},{b}", f"38;2;{r};{g};{b}", False
        yield f"truecolor 배경 {r},{g},{b}", f"48;2;{r};{g};{b}", True


def _fed(sgr):
    p = Pane(-1, -1, 24, 4)
    p.feed(f"\x1b[2J\x1b[H\x1b[{sgr}m{_MASCOT}\x1b[0m".encode())
    return p


async def test_export_roundtrip_preserves_every_sgr_color():
    """export_state → import_state 왕복이 **모든** SGR 색을 보존한다.

    오라클은 렌더 결과 전체(글자+스타일)다 — 색만 빠지는 결함이 정확히 여기서 보인다.
    """
    lost = []
    for label, sgr, _is_bg in _sgr_cases():
        src = _fed(sgr)
        before = src.render(True)[0]
        dst = Pane(-1, -1, 24, 4)
        dst.import_state(src.export_state())
        after = dst.render(True)[0]
        if before != after:
            lost.append((label, before[0], after[0]))
    assert not lost, (
        "재시작 스냅샷 왕복에서 색/속성을 잃었다(%d건): %r" % (len(lost), lost[:3]))


async def test_display_layer_keeps_every_server_color_name():
    """서버가 내놓는 색 이름을 표시 계층이 하나도 버리지 않는다(두 번째 소비자).

    `_rich_color` 가 None 을 주면 그 색은 화면에서 그냥 사라진다 — 07-27 결함의 모양.
    """
    dropped = []
    for label, sgr, is_bg in _sgr_cases():
        rows = _fed(sgr).render(True)[0]
        name = None
        for text, st in rows[0]:
            if text.startswith(_MASCOT[0]):
                name = st.get("b" if is_bg else "f")
                break
        assert name is not None, f"{label}: 서버가 색을 안 실었다"
        if _rich_color(name) is None:
            dropped.append((label, name))
    assert not dropped, (
        "표시 계층이 색을 버린다(%d건): %r" % (len(dropped), dropped[:5]))


async def test_preserved_typo_names_are_still_in_the_tables():
    """오타 보존 계약의 대조군 — 표에서 오타가 사라지면(누가 '고치면') 정규화 표가
    죽은 코드가 되고, 반대로 새 오타가 늘면 위 두 테스트가 잡는다. 여기서는 **정규화
    표의 키가 실제로 표에 있는 이름**임을 못박는다(오타 목록의 주인 = vtconst)."""
    all_names = set(vtconst.FG_ANSI.values()) | set(vtconst.FG_AIXTERM.values()) \
        | set(vtconst.BG_ANSI.values()) | set(vtconst.BG_AIXTERM.values())
    for typo in vtconst.PYTE_COLOR_TYPOS:
        assert typo in all_names, f"정규화 표에 표에 없는 이름이 있다: {typo}"
    assert "bfightmagenta" in all_names, "aixterm 105 오타 보존 계약이 사라졌다"
