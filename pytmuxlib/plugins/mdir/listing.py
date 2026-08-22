"""목록을 **어떻게 늘어놓나** — 정렬과 마스크. UI 무의존(rich/textual 을 안 읽는다).

# 왜 여기인가 (pytmux-12 C)

제보: *"정본 동작과 완전히 같게."* 정본은 `Alt+N/E/S/T/O` 로 정렬을 바꾸고(같은 키를 다시
누르면 내림차순) `Alt+F` 로 파일 마스크를 건다. 그 규칙이 **Textual 화면 안**(`screen.py`)에
있어서 서버가 못 불렀고, 그래서 네이티브 클라의 mdir 은 늘 이름순이었다.

`rowtag.py`(줄의 뜻)·`cmdmap.py`(명령)가 먼저 낸 자리이고, 이것은 **차례** 판이다.

# 왜 정렬이 다섯인가

`n` 이름 · `e` 확장자 · `s` 크기 · `t` 시각 · `o` **무정렬**(서버가 준 순서 — 원조 기본).
무정렬을 빼면 "파일시스템이 준 차례"를 볼 길이 사라진다 — 그 차례가 뜻을 갖는 자리가 있다
(내려받은 순서로 쌓인 디렉터리 따위).
"""
from __future__ import annotations

import fnmatch

#: 정렬 갈래. 이 다섯이 정본 `Alt+` 키의 마지막 글자와 같다.
SORTS = ("n", "e", "s", "t", "o")


def sort_key(mode: str):
    """그 갈래의 정렬 키. 두 번째 칸이 늘 이름인 이유: 크기·시각이 같은 것들이
    프레임마다 자리를 바꾸면 커서가 엉뚱한 줄로 미끄러진다(안정 정렬)."""
    if mode == "e":
        return lambda e: ((e["n"].rsplit(".", 1)[-1].lower()
                           if "." in e["n"][1:] else ""), e["n"].lower())
    if mode == "s":
        return lambda e: (e.get("s", 0), e["n"].lower())
    if mode == "t":
        return lambda e: (e.get("m", 0), e["n"].lower())
    return lambda e: e["n"].lower()


def next_sort(cur: str, rev: bool, pressed: str):
    """같은 갈래를 **다시 누르면 내림차순**(정본 손버릇), 다른 갈래면 오름차순부터.

    돌려주는 것은 `(갈래, 내림차순인가)`."""
    if pressed not in SORTS:
        return cur, rev
    if cur == pressed:
        return cur, not rev
    return pressed, False


def arrange(entries, sort="n", rev=False, masks=None):
    """`(디렉터리, 파일)` — 마스크를 걸고 정렬한 결과.

    ⚠ **마스크는 파일에만** 건다(정본과 같다). 디렉터리까지 거르면 `*.txt` 를 걸었을 때
    들어갈 곳이 사라져 그 화면에서 나올 수 없다.
    """
    dirs = [e for e in entries if e.get("d")]
    files = [e for e in entries if not e.get("d")]
    if masks:
        files = [e for e in files
                 if any(fnmatch.fnmatch(e["n"], m) for m in masks)]
    if sort != "o":                 # o = 무정렬(서버가 준 차례 그대로)
        key = sort_key(sort)
        dirs.sort(key=key, reverse=rev)
        files.sort(key=key, reverse=rev)
    return dirs, files


def parse_masks(text: str):
    """`*.txt *.md` 같은 한 줄을 마스크 목록으로. 빈 줄이면 `[]`(= 거르지 않는다)."""
    return [m for m in str(text or "").split() if m]


def counts(dirs, files, tags=(), free=0, total=0,
           sort="n", rev=False, hidden=False, tail="", key=None):
    """하단 **집계줄** 한 줄 — `N File  M Dir  T Byte  F(P%)byte free`
    (+ `Sel n (bytes)` · 정렬 표시 · 숨김 `H` · 꼬리말).

    # 왜 여기인가 (pytmux-126)

    이 줄이 나르는 것은 **표현이 아니라 자료**다 — 몇 개가 보이고, 그것이 몇 바이트이고,
    볼륨에 얼마가 남았고, **지금 무슨 정렬인지**. 그런데 그 계산이 `screen.py`(Textual)
    안에만 있어서 서버가 못 불렀고, 그래서 네이티브 클라의 mdir 에는 그 값이 **하나도
    없었다**. 특히 pytmux-12 가 넣은 정렬·마스크는 *건 다음 무엇이 걸렸는지 볼 자리*가
    없었다 — 손은 있고 눈이 없는 상태다.

    `rowtag.py`(줄의 뜻)·`arrange`(차례)가 먼저 낸 자리와 **같은 이유로** 한 벌이다:
    두 벌이면 같은 디렉터리가 두 클라에서 다른 수를 말한다.

    ⚠ **글자를 번역하지 않는다.** `File`·`Dir`·`Byte`·`free` 는 Mdir III 원조의 서식이고
    색과 같은 부류다(제품의 정체성 — 정본이 로케일과 무관하게 이 글자를 쓴다). 로케일을
    타는 것은 꼬리말뿐이라, 그것만 **부르는 쪽이 지어서** `tail` 로 넣는다 — 여기서
    `i18n.t` 를 부르면 서버 로케일이 스펙에 실려 영어 클라로 샌다.

    `key` — 태그가 무엇을 열쇠로 쓰나. 정본 화면은 **이름**, 화면 스펙은 **절대경로**다
    (같은 이름이 두 디렉터리에 있어도 안 섞이게). 기본은 이름이다.
    """
    key = key or (lambda e: e["n"])
    tagged = set(tags or ())
    total_bytes = sum(int(e.get("s") or 0) for e in files)
    # ⚠ `Sel` 의 **수는 태그 전부**(디렉터리 포함)이고 **바이트는 파일만**이다 —
    #    정본이 그렇게 센다(디렉터리 크기는 재지 않는다).
    sel_bytes = sum(int(e.get("s") or 0) for e in files if key(e) in tagged)
    pct = round(free * 100 / total) if total else 0
    text = (f"{len(files)} File  {len(dirs)} Dir  {total_bytes:,} Byte  "
            f"{free:,}({pct}%)byte free")
    if tagged:
        text += f"  Sel {len(tagged)} ({sel_bytes:,})"
    # 정렬 표시(원조 상태줄의 N/E/S/T 문자 — 내림차순은 ↓, O=무정렬).
    text += f"  {(sort or 'n').upper()}{'↓' if rev else ''}"
    if hidden:
        text += " H"
    if tail:
        text += f"  {tail}"
    return text
