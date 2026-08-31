#!/usr/bin/env python3
"""비율의 **의미 등급** 어휘를 정본에서 뽑는다(pytmux-419 ⑥).

# 왜 이 표가 필요한가

정본 토큰 팝업의 `[기간]` 탭은 `5h%`·`1w%` 칸을 비율에 따라 초록·노랑·빨강으로
칠한다(≥50 주의 · ≥80 위험 — 상태줄 한도 배지와 같은 임계). 그 **눈금**은
`usagehead.pct_level` 한 벌이 쥐고, 소켓을 건너는 것은 색이 아니라 **이름**이다
(색을 실으면 서버가 UI 를 알게 된다 — 설계 §10 위험표).

남은 위험은 **어휘가 갈리는 것**이다: 정본이 등급을 하나 더 내면(예: `warn2`) 클라의
표에 없는 이름이 오고, 그 칸은 조용히 **기본색**으로 뜬다 — 예외도 로그도 없다.
`pytmux-16`(ime 배지가 `primary` 를 몰라 안 보였다)이 정확히 그 부류였다. 그래서
어휘를 여기서 뽑아 `crates/proto/src/celltag_tests.rs` 가 전수를 잰다.

# 왜 import 하지 않고 읽나

플러그인 디렉터리 이름이 `claude-code` 라 파이썬 식별자가 아니다(형제 생성기들이
소스를 읽는 것과 같은 사정). AST 로 읽되 **선언(`PCT_LEVELS`)만 믿지 않는다** —
`pct_level` 이 실제로 돌려주는 이름을 함께 모아 둘이 어긋나면 떨어뜨린다. 선언만
보면 함수에 갈래가 늘어도 조용히 지나간다.

⛔ **0건이면 실패**다. 빈 표를 내면 소비자 쪽 오라클이 "빈 것 == 빈 것"으로 통과한다.

    python3 scripts/gen_pct_levels.py [--pytmux ..] [--print]
"""

import argparse
import ast
import json
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_PYTMUX = HERE.parent
DEFAULT_OUT = HERE / "crates" / "proto" / "tests" / "fixtures" / "pct_levels.json"

SRC = ("pytmuxlib", "plugins", "claude-code", "usagehead.py")


def collect(pytmux_root: pathlib.Path) -> dict:
    path = pytmux_root.joinpath(*SRC)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    consts: dict[str, object] = {}
    fn = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            t = node.targets[0]
            if isinstance(t, ast.Name):
                try:
                    consts[t.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
        elif isinstance(node, ast.FunctionDef) and node.name == "pct_level":
            fn = node
    if fn is None:
        raise SystemExit(f"{path.name}: pct_level 을 못 찾았다 — 판정이 옮겨졌나?")

    declared = list(consts.get("PCT_LEVELS") or ())
    # 함수가 **실제로** 돌려주는 이름(빈 문자열 = 「값이 없다」라 등급이 아니다).
    returned = [
        n.value.value
        for n in ast.walk(fn)
        if isinstance(n, ast.Return)
        and isinstance(n.value, ast.Constant)
        and isinstance(n.value.value, str)
        and n.value.value
    ]
    if not declared:
        raise SystemExit(f"{path.name}: PCT_LEVELS 가 비었다")
    if sorted(declared) != sorted(set(returned)):
        raise SystemExit(
            f"{path.name}: 선언({declared})과 pct_level 이 내는 이름({sorted(set(returned))})이"
            " 어긋난다 — 한쪽만 고쳤나?"
        )
    warn, crit = consts.get("PCT_WARN"), consts.get("PCT_CRIT")
    if not isinstance(warn, int) or not isinstance(crit, int) or not 0 < warn < crit:
        raise SystemExit(f"{path.name}: 눈금을 못 읽었다(PCT_WARN={warn} PCT_CRIT={crit})")
    # 차례는 **낮은 것부터**다 — 소비자가 등급의 대소를 이 차례로 잰다.
    return {"levels": declared, "warn_at": warn, "crit_at": crit}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", type=pathlib.Path, default=DEFAULT_PYTMUX)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true", help="파일 대신 표준출력으로")
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = collect(args.pytmux)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.print:
        sys.stdout.write(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"{os.fspath(args.out)}: 등급 {len(data['levels'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
