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
