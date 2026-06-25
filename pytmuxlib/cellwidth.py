"""East Asian Ambiguous 폭 일원화 — 단말이 모호폭 문자를 2칸으로 그릴 때의 정합성.

배경: `→ · — ↔ … ×` 같은 **East Asian Ambiguous(EAW='A')** 문자는 Unicode 표준상
폭이 1 이지만, CJK 로케일·폰트로 설정된 단말(특히 한국/중국/일본 사용자)은 이를
**2칸**으로 그린다. pytmux 의 폭 계산(wcwidth)·pyte 격자·Rich/Textual 측정은 모두
1칸으로 보므로, 앱(Claude Code 등)이 한 줄을 정확히 패널 폭까지 채우면 그 줄이 실
단말에서 1칸 넘쳐 **줄바꿈→다음 줄과 겹침(이중 출력)** 이 연쇄한다(사용자 보고:
텍스트가 한 줄에 겹치거나 좌우 폭을 넘어 패널 아웃라인에 겹침).

해법: 단말이 모호폭을 2칸으로 그리는지 클라 기동 시 CPR(커서 위치 질의)로 **자동
감지**(launcher.detect_ambiguous_width)하고, 감지되면 폭 모델을 세 곳에서 일관되게
'모호폭=2' 로 전환한다:
  ① 클라 합성/탭바/상태줄의 `char_cells`(= clientutil._char_cells),
  ② 서버 pyte 격자(`pyte.screens.wcwidth` 오버라이드 — 앱 레이아웃이 격자에 정확히
     앉도록),
  ③ Rich/Textual 셀 측정(Strip/Segment 폭 — Textual 이 같은 폭으로 크롭·기록하도록).

기본값은 'narrow'(현행 동작)라, 모호폭을 1칸으로 그리는 절대다수 단말은 **패치가
설치조차 되지 않아** 거동·성능 변화가 0 이다. wide 는 감지(또는 opt)로만 켜진다.
"""
from __future__ import annotations

import unicodedata
from functools import lru_cache

from wcwidth import wcwidth

_AMBIG_WIDE = False
_patched = False
# Rich 원본 측정 함수 — **첫 설치 때 한 번** 잡아 두고 절대 비우지 않는다(안정 참조).
# 패치 함수가 이 전역을 읽으므로, 설치/복원 desync 로 패치 참조가 남더라도
# `_AMBIG_WIDE` 가 False 면 원본값을 그대로 돌려줘 거동·정합성이 깨지지 않는다.
_orig_cell_len = None
_orig_char_size = None
_orig_pyte_w = None


@lru_cache(maxsize=4096)
def is_ambiguous(ch: str) -> bool:
    """East Asian Ambiguous(EAW='A') 문자인가. 단, **박스 드로잉(U+2500–257F)·블록
    요소(U+2580–259F)는 제외**해 1칸으로 둔다 — 표준상 EAW='A' 지만 CJK 로케일
    단말도 격자 정렬을 위해 이들을 1칸으로 그린다(테이블·테두리가 2칸이면 끊긴다).
    pytmux 는 테두리/탭연결(─│┌┐└┘├┤┬┴┼ ▀)을 **1칸 격자 셀**에 배치하므로,
    모호폭=2 로 올리면 Textual 셀 측정이 격자와 어긋나 가로 테두리 줄(─ 가득)이
    위젯 폭의 2배가 돼 넘치고 좌우 │가 콘텐츠를 민다(사용자 보고: ssh+CJK 단말에서
    스크롤 시 패널 첫/마지막 줄 텍스트 겹침). →·—↔…× 같은 일반 모호폭 기호는
    단말이 실제 2칸으로 그리므로 그대로 wide 로 둔다(p4 60827 원 버그의 대상).
    표 이분탐색+범위 비교라 메모이즈가 싸고 안전."""
    if unicodedata.east_asian_width(ch) != "A":
        return False
    # Box Drawing(2500–257F) + Block Elements(2580–259F): 격자 1칸 유지.
    return not (0x2500 <= ord(ch) <= 0x259F)


@lru_cache(maxsize=8192)
def char_cells(ch: str) -> int:
    """문자가 차지하는 칸 수(와이드=2, 그 외=1). wide 모드면 모호폭도 2.

    좁은(기본) 모드의 반환값은 종전 `_char_cells`(``2 if wcwidth==2 else 1``)와
    동일하다 — 폭0(결합)·폭-1(비출력)도 1 로 떨어진다. wide 모드에서만 EAW='A' 를
    1→2 로 올린다(다른 문자는 불변)."""
    w = 2 if wcwidth(ch) == 2 else 1
    if w == 1 and _AMBIG_WIDE and is_ambiguous(ch):
        return 2
    return w


def ambiguous_wide() -> bool:
    return _AMBIG_WIDE


def set_ambiguous_wide(on: bool) -> None:
    """모호폭 wide 모드 전환. 켜질 때 pyte·Rich/Textual 패치를 설치하고, 꺼지면 복원.

    기동 시 한 번 호출되는 게 정상이나, 멱등·가역이라 재호출도 안전하다. 모듈
    부재(서버엔 textual 없음·클라엔 pyte 없음)는 패치별 try 로 건너뛴다."""
    global _AMBIG_WIDE
    on = bool(on)
    if on == _AMBIG_WIDE:
        return
    _AMBIG_WIDE = on
    char_cells.cache_clear()
    if on:
        _install_patches()
    else:
        _restore_patches()


def _amwide_char_size(character: str, unicode_version: str = "auto") -> int:
    """Rich `get_character_cell_size` 의 모호폭 인지 버전: 원 너비가 1 인 EAW='A' 만
    2 로 올리고 나머지(이모지·CJK·결합 등)는 Rich 표 값을 그대로 보존한다.

    `_AMBIG_WIDE` 가 False 면(복원됨/desync) 원본값 그대로 — no-op 라 안전하다."""
    w = _orig_char_size(character, unicode_version)
    if _AMBIG_WIDE and w == 1 and is_ambiguous(character):
        return 2
    return w


def _amwide_cell_len(text: str, unicode_version: str = "auto") -> int:
    """문자열 셀 길이의 모호폭 인지 버전. 모호폭 문자가 없거나 wide 가 아니면 원
    함수로 위임해 이모지/CJK 측정을 Rich 와 100% 일치시키고, 있을 때만 합산한다."""
    if not _AMBIG_WIDE or not any(is_ambiguous(c) for c in text):
        return _orig_cell_len(text, unicode_version)
    return sum(_amwide_char_size(c, unicode_version) for c in text)


def _install_patches() -> None:
    global _patched
    if _patched:
        return
    _patched = True
    _install_pyte()
    _install_rich_textual()


def _restore_patches() -> None:
    global _patched
    if not _patched:
        return
    _patched = False
    for target, name, _orig in _restore_list:
        try:
            setattr(target, name, _orig)
        except Exception:
            pass
    _restore_list.clear()


_restore_list: list = []


def _patch(target, name, new) -> None:
    """`target.name` 을 new 로 바꾸고 복원 목록에 원본을 기록(멱등 가역)."""
    _restore_list.append((target, name, getattr(target, name)))
    setattr(target, name, new)


def _install_pyte() -> None:
    """pyte 격자 폭: `pyte.screens.wcwidth` 를 모호폭 인지로 교체(서버 레이아웃).

    pyte 의 `Screen.draw` 는 매 문자 `wcwidth(ch)` 를 모듈 전역으로 호출하므로
    전역 치환이면 충분하다. 폭0(결합)·음수는 원값 보존, EAW='A' 의 1 만 2 로."""
    global _orig_pyte_w
    try:
        import pyte.screens as ps
    except Exception:
        return
    if _orig_pyte_w is None:      # 원본 1회 포착(재설치 시 패치본을 잡지 않도록)
        _orig_pyte_w = ps.wcwidth
    _patch(ps, "wcwidth", _pyte_w)


def _pyte_w(ch, *a, **k):
    """pyte 격자용 모호폭 인지 wcwidth. `_AMBIG_WIDE` False 면 원본값(no-op)이라
    혹시 참조가 남아도 안전하다."""
    w = _orig_pyte_w(ch, *a, **k)
    if _AMBIG_WIDE and w == 1 and is_ambiguous(ch):
        return 2
    return w


def _install_rich_textual() -> None:
    """Rich/Textual 셀 측정: Strip/Segment 폭이 모호폭을 2 로 세도록.

    `rich.cells.cell_len` 본문은 `cached_cell_len`/`_cell_len` 을 **호출 시점에**
    rich.cells 전역에서 찾으므로 그 둘만 갈면 여러 모듈에 import 된 `cell_len`
    사본까지 한꺼번에 새 경로를 탄다. 반면 `get_character_cell_size` 는 by-ref 로
    import 돼(rich.segment·textual._wrap) 별도 재바인딩이 필요하고, textual 은 자체
    `_cells.cell_len`(= rich `cached_cell_len` 별칭)을 가져 그것도 갈아야 한다."""
    global _orig_cell_len, _orig_char_size
    try:
        import rich.cells as rcells
    except Exception:
        return
    # 원본을 **한 번만** 잡는다(이후 영구 보존). 위임 대상은 **원시 워커**
    # `_cell_len`(get_character_cell_size 만 쓰는 캐시미스 경로) — `cell_len`/
    # `cached_cell_len` 은 본문에서 우리가 갈아끼운 프리미티브를 다시 불러 무한재귀가
    # 된다. (재설치 시 이미 패치된 함수를 원본으로 잡지 않도록 1회만.)
    if _orig_cell_len is None:
        _orig_cell_len = rcells._cell_len
        _orig_char_size = rcells.get_character_cell_size

    # ① rich.cells 1차 프리미티브 — cell_len 본문이 호출 시점에 참조한다.
    _patch(rcells, "cached_cell_len", _amwide_cell_len)
    _patch(rcells, "_cell_len", _amwide_cell_len)
    _patch(rcells, "get_character_cell_size", _amwide_char_size)

    # ② by-ref import 된 get_character_cell_size 사본들.
    try:
        import rich.segment as rseg
        _patch(rseg, "get_character_cell_size", _amwide_char_size)
    except Exception:
        pass

    # ③ Textual 자체 셀 측정 — _cells.cell_len 은 rich.cached_cell_len 별칭이라
    #    위 ①로는 안 바뀐다(import 시점 객체 고정). strip 도 그 사본을 가져간다.
    for modname, attrs in (
        ("textual._cells", ("cell_len",)),
        ("textual.strip", ("cell_len",)),
        ("textual._wrap", ("cell_len", "get_character_cell_size")),
    ):
        try:
            import importlib
            mod = importlib.import_module(modname)
        except Exception:
            continue
        for a in attrs:
            if not hasattr(mod, a):
                continue
            new = _amwide_char_size if a == "get_character_cell_size" else _amwide_cell_len
            _patch(mod, a, new)
