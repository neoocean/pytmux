"""러스트 시험이 **제 몫의 임시 자리**를 쓰나 — 겹쳐 돌려도 서로를 안 지우나.

`pytmux/pytmux-424`. 시험이 `std::env::temp_dir().join("고정이름")` 을 쓰고 만들기 전에
`remove_dir_all` 을 치면, 같은 기계에서 `cargo test` 가 둘 돌 때 **뒤엣것이 앞엣것의
트리를 런 도중에 지운다.** 그때 터지는 자리는 지운 자리가 아니라 엉뚱한 인덱스라
(실측 2026-08-30: `watcher.items()[1]` 패닉 둘) **혼자 돌리면 통과**하고, 그래서
「부하 플레이크」로 읽혀 오래 산다.

⛔ **사람이 지키는 규약으로 두지 않는다** — 관측된 자리 다섯을 고쳐도 여섯째가 내일
   들어온다. 여기서 센다.

⚠ 재는 것은 **시험 코드**뿐이다. 제품 코드가 `temp_dir()` 을 쓰는 것은 이 이슈가 아니다
   (`clip` 이 클립보드 그림을 거기 떨군다 — 그것이 그 기능의 계약이다).
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

#: 상류 스냅샷(MIT 경계 · `client/PROVENANCE.md`)은 우리 규약을 안 진다.
UPSTREAM = ("warpui", "warpui_core")

#: `temp_dir().join(…)` 한 자리. 인자가 어디서 끝나는지는 괄호를 세어 찾는다.
_JOIN = re.compile(r"temp_dir\(\)\s*\.\s*join\s*\(")

#: 「런마다 다르다」의 증거. 하나라도 있으면 통과다.
UNIQUE = ("process::id()", "nanos", "SEQ", "TempDir", "thread::current")


def _rs_files():
    base = os.path.join(ROOT, "client", "crates")
    for d, dirs, names in os.walk(base):
        dirs[:] = [x for x in dirs if x not in ("target", ".git")]
        rel = os.path.relpath(d, base)
        if rel.split(os.sep)[0] in UPSTREAM:
            continue
        for n in names:
            if n.endswith(".rs"):
                yield os.path.join(d, n)


def _is_test_code(path: str, text: str, at: int) -> bool:
    """그 자리가 **시험 코드**인가.

    `tests/` 아래거나 `*_tests.rs` 면 통째로 시험이고, 그 밖의 파일에서는
    `#[cfg(test)]` **뒤**에 있는 것만 시험이다.
    """
    p = path.replace(os.sep, "/")
    if "/tests/" in p or p.endswith("_tests.rs"):
        return True
    i = text.find("#[cfg(test)]")
    return i >= 0 and at > i


def _arg_of(text: str, open_paren: int) -> str:
    """`join(` 의 여는 괄호부터 짝이 맞는 닫는 괄호까지."""
    depth, i = 0, open_paren
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren:i + 1]
        i += 1
    return text[open_paren:]


def fixed_temp_dirs(path: str, text: str | None = None):
    """`temp_dir().join(고정이름)` 을 쓰는 **시험** 자리들(줄 번호)."""
    text = open(path, encoding="utf-8").read() if text is None else text
    out = []
    for m in _JOIN.finditer(text):
        if not _is_test_code(path, text, m.start()):
            continue
        arg = _arg_of(text, m.end() - 1)
        if any(u in arg for u in UNIQUE):
            continue
        out.append(text[:m.start()].count("\n") + 1)
    return out


def test_no_rust_test_shares_a_fixed_temp_directory():
    """⛔ 겹쳐 돌려도 서로의 트리를 안 지운다(pytmux-424)."""
    bad = []
    for path in _rs_files():
        for line in fixed_temp_dirs(path):
            bad.append(f"{os.path.relpath(path, ROOT)}:{line}")
    assert not bad, ("고정 임시 경로를 쓰는 시험이 생겼다 — pid·일련번호를 섞는다: "
                     + ", ".join(bad))


def test_the_isolation_gate_actually_bites():
    """⛔ 게이트 자체를 변이로 검증한다 — 안 무는 게이트는 초록을 파는 장식이다.

    ⚠ **위양성 쪽도 함께 잰다**: 제품 코드의 `temp_dir()` 과 pid 를 섞은 시험은
    안 물어야 한다. 한쪽만 재면 「전부 신고」하는 게이트가 통과해 버린다.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        planted = os.path.join(d, "mutant_tests.rs")
        src = (
            'fn a() -> PathBuf { std::env::temp_dir().join("pytmux-fixed") }\n'
            'fn b() -> PathBuf {\n'
            '    std::env::temp_dir().join(format!("pytmux-ok-{}", std::process::id()))\n'
            '}\n'
        )
        with open(planted, "w", encoding="utf-8") as fh:
            fh.write(src)
        assert fixed_temp_dirs(planted) == [1], \
            f"고정 자리를 못 봤거나 pid 를 섞은 자리까지 셌다: {fixed_temp_dirs(planted)}"

        # 제품 코드(시험 표식이 없는 파일)는 대상이 아니다
        prod = os.path.join(d, "lib.rs")
        with open(prod, "w", encoding="utf-8") as fh:
            fh.write('fn save() -> PathBuf { std::env::temp_dir().join("shot.png") }\n')
        assert fixed_temp_dirs(prod) == [], "제품 코드까지 신고했다 — 위양성"


def test_the_reported_site_is_actually_fixed():
    """제보가 이름을 댄 자리(`claude/tests/watcher.rs`)가 실제로 고쳐졌나.

    ⚠ 위 전수 게이트는 **자리를 옮기면** 조용해진다(파일이 사라져도 통과한다).
    제보가 짚은 자리를 이름으로 한 번 더 못 박는다.
    """
    p = os.path.join(ROOT, "client", "crates", "claude", "tests", "watcher.rs")
    text = open(p, encoding="utf-8").read()
    assert "std::process::id()" in text, "제보 자리가 아직 런마다 안 갈린다"
    assert fixed_temp_dirs(p, text) == [], "제보 자리에 고정 경로가 남아 있다"
