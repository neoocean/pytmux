"""타이핑 찾기(speed search)의 **뛰기 규칙** 한 벌 — UI 무의존 순수 함수.

# 왜 코어에 있나 (pytmux-611·612)

정본 mdir(`plugins/mdir/screen.py:_MdirView._jump`)과 ncd(`plugins/ncd/screen.py:
_NcdView._jump`)가 같은 규칙을 각자 들고 있었다. 두 플러그인은 서로를 import 못 한다
(delete-to-disable — 한쪽을 지우면 남은 쪽이 죽는다). 그리고 GUI 클라(Rust)가 같은
규칙을 한 벌 더 든다(`base::screens::typeahead_jump`). 세 벌이 조용히 갈리지 않게 규칙을
여기 한 곳에 두고, `client/scripts/gen_typeahead_fixture.py` 가 이 함수로 뽑은 사례를
Rust 쪽 자(`proto/tests/typeahead_conformance.rs`)가 맞댄다.
"""
from __future__ import annotations


def find_jump(names, start: int, query: str):
    """`query` 로 뛸 줄 번호 — 없으면 `None`.

    지금 줄(`start`)부터 감아 돌며 **접두 일치를 먼저**, 없으면 **포함 일치**(대소문자
    무시). 이름이 빈(`""`·`None`) 줄은 건너뛴다 — mdir 의 `..`·드라이브 줄을 정본 찾기는
    안 본다.
    """
    n = len(names)
    if not query or not n:
        return None
    q = query.lower()
    start = max(0, min(int(start), n - 1))
    for match_prefix in (True, False):
        for off in range(n):
            i = (start + off) % n
            name = names[i]
            if not name:
                continue
            name = name.lower()
            if (name.startswith(q) if match_prefix else q in name):
                return i
    return None
