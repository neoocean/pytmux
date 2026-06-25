"""East Asian Ambiguous 폭(cellwidth) 단위 테스트 — 자동감지·폭 모델 일관성·회귀.

배경: CJK 로케일 단말이 모호폭 문자(→ · — 등)를 2칸으로 그릴 때, pytmux 가 1칸으로
보면 패널 폭에 꽉 찬 줄이 1칸 넘쳐 줄바꿈→다음 줄과 겹침(이중 출력)이 연쇄했다.
fix=단말 자동감지(CPR) 후 폭 모델을 클라 합성·Rich/Textual·서버 pyte 세 곳에서
'모호폭=2' 로 일관 전환. 좁은(기본) 단말은 패치 미설치라 거동 0 변화.
"""
import os
import threading
import time

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import cellwidth as cw
from pytmuxlib import launcher
from pytmuxlib.clientutil import _char_cells

AMB = "·"     # U+00B7 EAW='A' (Ambiguous), wcwidth=1
CJK = "가"    # 항상 2
ASCII = "A"   # 항상 1


def _with_wide(fn):
    """wide 모드로 fn 을 돌리고 끝나면 반드시 narrow 로 복원(전역 패치 격리)."""
    cw.set_ambiguous_wide(True)
    try:
        fn()
    finally:
        cw.set_ambiguous_wide(False)


async def test_char_cells_narrow_vs_wide():
    # 기본(narrow): 모호폭=1, 종전 동작과 동일.
    cw.set_ambiguous_wide(False)
    assert _char_cells(AMB) == 1
    assert _char_cells(CJK) == 2
    assert _char_cells(ASCII) == 1
    # wide: 모호폭만 2 로, 나머지는 불변.
    def check():
        assert _char_cells(AMB) == 2, "모호폭은 wide 에서 2"
        assert _char_cells(CJK) == 2
        assert _char_cells(ASCII) == 1
    _with_wide(check)
    # 복원 확인.
    assert _char_cells(AMB) == 1, "wide 해제 후 모호폭 1 로 복원"


async def test_box_drawing_stays_one_cell_in_wide():
    """박스 드로잉(─│┌┼)·블록 요소(▀)는 EAW='A' 지만 wide 모드에서도 **1칸**으로
    둔다 — pytmux 가 테두리/탭연결을 1칸 격자 셀에 배치하므로 2칸으로 측정하면
    가로 테두리 줄(─ 가득)이 위젯 폭의 2배가 돼 넘쳐 첫/마지막 줄이 겹친다(ssh+CJK
    단말 스크롤, p4 60827 후속). char_cells·Rich/Textual·pyte 세 경로가 모두 1칸으로
    일치해야 격자가 안 어긋난다. 반면 일반 모호폭 기호(→·—)는 2칸 유지(원 버그 대상)."""
    import rich.cells as rc
    import pyte.screens as ps
    from rich.segment import Segment
    from textual.strip import Strip
    BOX = ["─", "│", "┌", "┐", "└", "┘", "├", "┤", "┬", "┴", "┼", "▀"]
    WIDE_SYM = ["→", "·", "—", "↔", "…", "×"]

    def check():
        for ch in BOX:
            assert _char_cells(ch) == 1, f"박스 {ch!r} 는 wide 에서도 1칸"
            assert rc.cell_len(ch) == 1, f"Rich {ch!r} 1칸"
            assert ps.wcwidth(ch) == 1, f"pyte {ch!r} 1칸"
            assert Strip([Segment(ch)]).cell_length == 1, f"Strip {ch!r} 1칸"
        for ch in WIDE_SYM:
            assert _char_cells(ch) == 2, f"일반 모호폭 {ch!r} 는 wide 에서 2칸"
            assert rc.cell_len(ch) == 2, f"Rich {ch!r} 2칸"
        # 핵심 불변식: 테두리 줄(─*W)의 클라 합성 폭 == Strip 폭(둘이 어긋나면 넘침).
        line = "─" * 40
        assert (sum(_char_cells(c) for c in line)
                == Strip([Segment(line)]).cell_length == 40)
    _with_wide(check)
    # narrow(기본)에서도 박스는 1칸(영향 없음).
    cw.set_ambiguous_wide(False)
    assert _char_cells("─") == 1 and _char_cells("→") == 1


async def test_rich_textual_measure_consistency():
    """Rich/Textual 의 Segment·Strip 폭이 wide 에서 모호폭=2 로, 클라 합성과 일치."""
    import rich.cells as rc
    import textual._cells as tc
    import textual.strip as ts
    from rich.segment import Segment
    from textual.strip import Strip

    # narrow: Rich/Textual 측정이 원래대로(이모지·CJK 표 값 보존).
    cw.set_ambiguous_wide(False)
    assert rc.cell_len(AMB) == 1
    assert rc.cell_len(CJK) == 2 and rc.cell_len("🚀") == 2 and rc.cell_len("AB") == 2

    def check():
        # 모호폭만 2 로 올라가고, 비모호 문자는 Rich 표 값 그대로.
        assert rc.cell_len(AMB) == 2
        assert tc.cell_len(AMB) == 2 and ts.cell_len(AMB) == 2
        assert Segment(AMB).cell_length == 2
        assert Strip([Segment(AMB)]).cell_length == 2
        assert rc.cell_len(CJK) == 2 and rc.cell_len("🚀") == 2 and rc.cell_len("AB") == 2
        # 핵심 불변식: 클라 합성 폭(char_cells 합) == Textual Strip 폭. 둘이 어긋나면
        # 셀이 밀려(이 버그) 깨진다.
        for text in ("A" + AMB + "B", AMB * 3, "한" + AMB + "x", "→·—↔…"):
            assert sum(_char_cells(c) for c in text) == Strip([Segment(text)]).cell_length
    _with_wide(check)

    # 복원: Rich/Textual 이 원본 측정으로 돌아옴.
    assert rc.cell_len(AMB) == 1 and ts.cell_len(AMB) == 1


async def test_pyte_grid_width_wide():
    """서버 pyte 격자: wide 에서 모호폭을 2 칸으로 앉혀 줄이 폭을 넘지 않는다."""
    from pytmuxlib.replay import replay

    # 폭 10 패널에 모호폭 문자를 폭까지 채운 줄을 그린다(앱이 narrow 가정으로 꽉 채운 꼴).
    line = ("A" * 8 + AMB + "B").encode()   # narrow 로는 10칸, wide 로는 11칸
    cw.set_ambiguous_wide(False)
    # wide 모드: pyte 가 모호폭을 2로 세어 마지막 B 가 다음 줄로 흘러 첫 줄은 ≤10 wide칸.
    def check():
        ls = replay(line, 10, 3)
        first = ls[0]
        w = sum(_char_cells(c) for c in first)
        assert w <= 10, f"wide pyte 첫 줄 폭 {w} ≤ 10(오버플로 없음)"
    _with_wide(check)


# ── 자동감지(CPR 프로브) ───────────────────────────────────────────────────────

async def test_detect_forced_and_nontty():
    assert launcher.detect_ambiguous_width("narrow") == "narrow"
    assert launcher.detect_ambiguous_width("wide") == "wide"
    # auto + 비-tty fd → narrow(안전 폴백).
    r, w = os.pipe()
    try:
        assert launcher.detect_ambiguous_width("auto", rfd=r, wfd=w) == "narrow"
    finally:
        os.close(r)
        os.close(w)


def _fake_terminal(master_fd, advance, stop):
    """master 끝에서 프로브 출력을 읽다가 `ESC[6n` 마다 CPR 로 응답.

    1차 응답 col=1, 2차 응답 col=1+advance(테스트 문자 출력 후 전진 칸수 모사).
    advance=2 → wide, =1 → narrow 로 감지되어야 한다."""
    seen = 0
    buf = b""
    while not stop.is_set():
        try:
            chunk = os.read(master_fd, 1024)
        except OSError:
            return
        if not chunk:
            return
        buf += chunk
        while b"\x1b[6n" in buf:
            buf = buf.split(b"\x1b[6n", 1)[1]
            seen += 1
            col = 1 if seen == 1 else 1 + advance
            try:
                os.write(master_fd, f"\x1b[1;{col}R".encode())
            except OSError:
                return
        if seen >= 2:
            # 정리 시퀀스까지 흘려보내고 종료.
            try:
                os.read(master_fd, 1024)
            except OSError:
                pass
            return


async def _detect_over_pty(advance):
    import pty
    master, slave = pty.openpty()
    stop = threading.Event()
    t = threading.Thread(target=_fake_terminal, args=(master, advance, stop), daemon=True)
    t.start()
    try:
        # slave 는 진짜 tty 라 termios/CPR 경로가 실제로 돈다.
        return launcher.detect_ambiguous_width("auto", rfd=slave, wfd=slave)
    finally:
        stop.set()
        t.join(timeout=1.0)
        os.close(master)
        os.close(slave)


async def test_detect_wide_terminal_via_pty():
    """모호폭을 2칸 전진으로 그리는 가짜 단말 → 'wide' 로 감지."""
    if os.name == "nt":      # pty/termios 는 Unix 전용 — Windows 는 실 PTY 검증 불가
        return
    assert await _detect_over_pty(advance=2) == "wide"


async def test_detect_narrow_terminal_via_pty():
    """모호폭을 1칸 전진으로 그리는 가짜 단말 → 'narrow' 로 감지."""
    if os.name == "nt":      # pty/termios 는 Unix 전용 — Windows 는 실 PTY 검증 불가
        return
    assert await _detect_over_pty(advance=1) == "narrow"


async def test_captured_reference_safe_after_restore():
    """wide 창에서 패치된 측정 함수를 by-ref 로 잡은 모듈이, 복원 후 좁게 측정.

    회귀: Textual 위젯 모듈(DataTable 등)이 wide 렌더 도중 lazy-import 되면 `cell_len`
    별칭이 패치본을 가리킨 채 남는다. 복원으로 모듈 attr 은 되돌려도 그 by-ref 사본은
    남으므로, 패치본 자체가 `_AMBIG_WIDE` 가 False 면 원본값(좁게)을 돌려줘야 한다
    (예전엔 비워진 _saved 에 KeyError 가 나 46개 렌더 테스트가 깨졌다)."""
    import rich.cells as rc
    cw.set_ambiguous_wide(True)
    captured_cell_len = rc.cell_len          # wide 창에서 잡은 참조(패치본)
    captured_char = rc.get_character_cell_size
    assert captured_cell_len(AMB) == 2
    cw.set_ambiguous_wide(False)
    # 복원 후: 잡아 둔 참조도 좁게(KeyError·잘못된 wide 측정 금지).
    assert captured_cell_len(AMB) == 1
    assert captured_cell_len("A" + AMB + "B") == 3
    assert captured_char(AMB) == 1


async def test_config_directive_parsed():
    """`set ambiguous-width <v>` 설정 지시어가 cfg 에 반영된다(기본 auto)."""
    import tempfile
    from pytmuxlib.keymap import load_config
    assert load_config("/nonexistent-xyz").get("ambiguous_width") == "auto"
    for v in ("narrow", "wide", "auto"):
        with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
            f.write(f"set ambiguous-width {v}\n")
            path = f.name
        try:
            assert load_config(path).get("ambiguous_width") == v
        finally:
            os.unlink(path)
