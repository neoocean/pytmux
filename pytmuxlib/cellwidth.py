"""East Asian Ambiguous 폭 일원화 — 단말이 모호폭 문자를 2칸으로 그릴 때의 정합성.

배경: `→ · — ↔ … ×` 같은 **East Asian Ambiguous(EAW='A')** 문자는 Unicode 표준상
폭이 1 이지만, CJK 로케일·폰트로 설정된 단말(특히 한국/중국/일본 사용자)은 이를
**2칸**으로 그린다. pytmux 의 폭 계산(wcwidth)·pyte 격자·Rich/Textual 측정은 모두
1칸으로 보므로, 앱(Claude Code 등)이 한 줄을 정확히 패널 폭까지 채우면 그 줄이 실
단말에서 1칸 넘쳐 **줄바꿈→다음 줄과 겹침(이중 출력)** 이 연쇄한다(제보:
텍스트가 한 줄에 겹치거나 좌우 폭을 넘어 패널 아웃라인에 겹침).

해법: 단말이 모호폭을 2칸으로 그리는지 클라 기동 시 CPR(커서 위치 질의)로 **자동
감지**(launcher.detect_ambiguous_width)하고, 감지되면 폭 모델을 세 곳에서 일관되게
'모호폭=2' 로 전환한다:
  ① 클라 합성/탭바/상태줄의 `char_cells`(= clientutil._char_cells),
  ② 서버 native 격자(`nativescreen._wcwidth` 오버라이드 — 앱 레이아웃이 격자에
     정확히 앉도록),
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
_orig_native_w = None


@lru_cache(maxsize=4096)
def is_ambiguous(ch: str) -> bool:
    """East Asian Ambiguous(EAW='A') 문자인가. 단, **박스 드로잉(U+2500–257F)·블록
    요소(U+2580–259F)는 제외**해 1칸으로 둔다 — 표준상 EAW='A' 지만 CJK 로케일
    단말도 격자 정렬을 위해 이들을 1칸으로 그린다(테이블·테두리가 2칸이면 끊긴다).
    pytmux 는 테두리/탭연결(─│┌┐└┘├┤┬┴┼ ▀)을 **1칸 격자 셀**에 배치하므로,
    모호폭=2 로 올리면 Textual 셀 측정이 격자와 어긋나 가로 테두리 줄(─ 가득)이
    위젯 폭의 2배가 돼 넘치고 좌우 │가 콘텐츠를 민다(제보: ssh+CJK 단말에서
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


@lru_cache(maxsize=8192)
def cluster_cells(text: str) -> int:
    """**문자소 군집**이 차지하는 칸 수(pytmux-407).

    # 왜 `char_cells` 를 못 쓰나

    저 함수는 글자 **하나**를 받는다(`ord()` 를 부른다). 그런데 셀이 군집을 들기
    시작하면서(`⚠`+U+FE0F) 그 값이 두 글자짜리 문자열로 오고, 그때 저 함수는
    `ord() expected a character, but string of length 2` 로 **죽는다**(실측 2026-08-26).

    ⛔ **저 함수의 값을 바꿔 고칠 수 없다** — 러스트 `proto::compose::char_cells` 와
    글자 하나까지 같아야 한다는 계약이 있고 `conformance.rs` 가 표본으로 잰다.
    그래서 군집을 받는 **새 입구**를 낸다(pytmux-389 가 `char_advance` 를 따로 낸 것과
    같은 판단).

    칸 수는 **첫 글자**가 정한다 — 뒤따르는 것은 폭 0 이라 얹히기만 한다. 빈 글은 1 이다
    (빈 셀도 한 칸이고, 화면 대부분이 그것이다).
    """
    return char_cells(text[0]) if text else 1


#: ZWJ — 이 글자로 끝나는 셀은 **군집이 아직 안 끝났다**는 뜻이다.
ZWJ = "\u200d"
#: 피부톤 수정자(U+1F3FB~U+1F3FF) — 앞 이모지에 얹힌다.
_MODIFIERS = (0x1F3FB, 0x1F3FF)
#: 지역 지시자(U+1F1E6~U+1F1FF) — **둘이 모여** 한 깃발이다.
_REGIONAL = (0x1F1E6, 0x1F1FF)
#: 그림 글자(Extended_Pictographic)의 **실용 근사**. 아래 `joins_previous` 머리말 참조.
_PICTO_RANGES = (
    (0x1F000, 0x1FAFF),   # 이모지 본진(패·이모티콘·기호·깃발·확장A)
    (0x2600, 0x27BF),     # 기타 기호 + 딩뱃(☀ ✋ ⚕ ♀ ✈ …)
    (0x2B00, 0x2BFF),     # 기타 기호와 화살표(⭐ ⬛ …)
)
#: 위 범위 밖에 홀로 있는 그림 글자들(레거시 자리).
_PICTO_SINGLES = frozenset((0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D))


def _in(o: int, span: tuple[int, int]) -> bool:
    return span[0] <= o <= span[1]


def is_pictographic(ch: str) -> bool:
    """그림 글자인가 — **Extended_Pictographic 의 실용 근사**.

    ⚠ 표준 속성이 아니다. `unicodedata` 는 그 속성을 안 주고 러스트 쪽 의존
    (`unicode-width`)도 안 준다 — 표를 통째로 들이는 대신 **범위 넷**으로 근사한다.
    한 벌이 두 언어에 같은 글로 적혀 있어야 하므로 판정은 **코드포인트 범위**뿐이고
    (유니코드 범주 조회를 쓰면 러스트가 못 따라온다), 갈리는지는 픽스처가 잰다
    (`client/crates/proto/tests/fixtures/clusters.json`).
    """
    o = ord(ch)
    return o in _PICTO_SINGLES or any(_in(o, r) for r in _PICTO_RANGES)


def joins_previous(prev: str, ch: str) -> bool:
    """`ch` 가 앞 셀의 **문자소 군집에 이어지나**(pytmux-407 ⓐ).

    # 무엇을 정하나

    종전 격자는 **폭 0 글자만** 앞 칸에 얹었다(`char_advance` 가 0 인 것들 — 변이
    선택자·ZWJ·결합 표시). 그래서 뒤따르는 조각이 **제 폭을 가진** 군집은 칸이 여럿으로
    갈렸다: `👨‍👩‍👧` 가 여섯 칸(셀 셋)이고 화면에는 **이모지 셋**이 뜬다(실측 2026-09-01).

    사람이 고른 규약(2026-09-01)은 **군집의 폭 = 밑글자의 폭**이다(tmux 3.4·현대 단말과
    같다). 그 규약을 지키는 판정이 이 함수다 — 참이면 부르는 쪽이 이 글자를 **앞 셀에
    붙이고 커서를 안 움직인다.**

    # 세 갈래 (유니코드 UAX #29 의 GB11·GB9b·GB12/13 의 실용판)

    ⑴ **ZWJ 뒤** — 앞 셀이 ZWJ 로 끝나고 이 글자가 그림 글자면 잇는다(`🧑‍💻`·`👨‍👩‍👧`).
       ⛔ 그림 글자일 것을 **묻는다**: ZWJ 는 데바나가리 등에서 이음/끊음 제어로도 쓰이는데
          거기서 두 글자를 한 칸에 접으면 그 줄이 어긋난다.
    ⑵ **피부톤 수정자** — 밑글자 뒤에 얹힌다(`👍🏿`).
    ⑶ **지역 지시자** — 앞 셀 끝의 지역 지시자가 **홀수 개**일 때만 잇는다(`🇰🇷`).
       짝수면 그 깃발은 이미 완성이라 새 깃발이 시작한다(`🇰🇷🇯🇵` = 깃발 둘).

    ⚠ 판정은 **앞 셀의 글 전체**를 본다(마지막 글자만이 아니다) — ⑶ 이 홀짝을 세야 하고,
    ⑴ 은 앞 셀이 이미 군집일 수 있다(`👨‍👩` 뒤에 `‍👧`).
    """
    if not prev or not ch:
        return False
    if prev.endswith(ZWJ):
        return is_pictographic(ch)
    o = ord(ch)
    if _in(o, _MODIFIERS):
        return True
    if _in(o, _REGIONAL):
        tail = 0
        for c in reversed(prev):
            if _in(ord(c), _REGIONAL):
                tail += 1
            else:
                break
        return tail % 2 == 1
    return False


@lru_cache(maxsize=8192)
def char_advance(ch: str) -> int:
    """이 글자가 격자에서 **밀어내는 칸 수**. 폭 0 글자는 0 이다.

    # 왜 char_cells 와 따로 있나 (pytmux-389)

    `char_cells` 는 *"몇 칸으로 그리나"* 이고 **러스트 `proto::compose::char_cells`
    와 글자 하나까지 같아야 한다**는 계약이 있다(`client/crates/proto/tests/
    conformance.rs` 가 표본 60개로 잰다). 그래서 거기서는 폭 0 도 1 로 떨어진다.

    자리를 나눌 때 묻는 것은 다른 질문이다 — *"다음 글자를 몇 칸 밀어내나"*. 변이
    선택자(U+FE0E·U+FE0F)·ZWJ(U+200D)·결합 표시는 **앞 글자에 얹히는** 것이라 아무도
    밀지 않는다. 한 칸을 주면 그 글자가 든 줄이 **한 칸씩 오른쪽으로 밀린다**
    (실측 2026-08-24 · GUI 프레임 덤프에서 `|⚠ |` 의 닫는 `|` 가 4번째 칸).

    ⚠ 비출력 문자(wcwidth < 0)는 0 이 아니다 — 종전대로 한 칸으로 센다(폭을 알 수
    없는 것과 폭이 0 인 것은 다르다)."""
    return 0 if wcwidth(ch) == 0 else char_cells(ch)


def attaches(prev: str, ch: str) -> bool:
    """이 글자가 앞 셀에 **얹히나** — 폭 0 이거나 군집이 이어질 때(pytmux-407 ⓐ).

    격자에 글자를 앉히는 자리가 여럿이고(서버 화면 모델 · 클라 합성 · 재생 합성 ·
    렌더), 각자 두 갈래를 적으면 한 곳만 고쳐지는 날이 온다 — 그날 그 줄이 어긋난다.
    러스트 짝은 `proto::compose::attaches` 다.
    """
    return char_advance(ch) == 0 or joins_previous(prev, ch)


def line_cells(text: str) -> int:
    """합성된 **한 줄의 시각 폭**(pytmux-407 ⓐ).

    ⚠ 글자 수가 아니라 **군집 수**를 센다. 얹힌 조각(변이 선택자·ZWJ·둘째 이모지 …)은
    칸을 안 쓰므로 여기서도 안 센다 — 낱개로 세면 이모지가 든 줄이 「폭 == cols」 계약을
    깬 것처럼 보인다(실측: 조합 문자 둘이 든 80칸 줄이 82 로 읽혔다).

    러스트 짝은 `proto::compose::display_width` 다.
    """
    width = 0
    cluster = ""
    for ch in text:
        if cluster and attaches(cluster, ch):
            cluster += ch
            continue
        width += char_cells(ch)
        cluster = ch
    return width


def ambiguous_wide() -> bool:
    return _AMBIG_WIDE


def set_ambiguous_wide(on: bool) -> None:
    """모호폭 wide 모드 전환. 켜질 때 native·Rich/Textual 패치를 설치하고, 꺼지면 복원.

    기동 시 한 번 호출되는 게 정상이나, 멱등·가역이라 재호출도 안전하다. 모듈
    부재(클라엔 nativescreen 미로드일 수 있음)는 패치별 try 로 건너뛴다."""
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
    _install_native()
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


def _install_native() -> None:
    """native 격자 폭: `nativescreen._wcwidth` 를 모호폭 인지로 교체(서버 레이아웃).

    nativescreen 의 `draw`/`display` 는 매 문자 폭을 **모듈 전역 `_wcwidth`** 로
    호출하므로(호출 시점 조회) 그 심볼만 갈면 충분하다. 폭0(결합)·음수는 원값 보존,
    EAW='A' 의 1 만 2 로. (구 pyte.screens.wcwidth 패치의 native 대응 — M4b.)"""
    global _orig_native_w
    try:
        from . import nativescreen as ns
    except Exception:
        return
    if _orig_native_w is None:    # 원본 1회 포착(재설치 시 패치본을 잡지 않도록)
        _orig_native_w = ns._wcwidth
    _patch(ns, "_wcwidth", _native_w)


def _native_w(ch, *a, **k):
    """native 격자용 모호폭 인지 wcwidth. `_AMBIG_WIDE` False 면 원본값(no-op)이라
    혹시 참조가 남아도 안전하다."""
    w = _orig_native_w(ch, *a, **k)
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
