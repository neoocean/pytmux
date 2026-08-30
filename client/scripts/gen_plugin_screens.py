#!/usr/bin/env python3
"""정본 플러그인이 낼 수 있는 **화면 모양(`kind`)** 을 전수로 뽑아 픽스처로 남긴다.

# 왜 이 자가 필요한가 (pytmux-33 ⓖ3 · 축 ⑶ "플러그인 화면")

ⓖ3(전면 1:1 대조)이 "못 재는 축" 넷을 셌고, 그중 **플러그인 화면**은 재는 자가
아예 없었다. 이미 있던 둘(`gen_plugin_client_cmds.py`·`gen_plugin_server_actions.py`)은
**명령 이름**만 센다 — *"이 이름을 치면 화면이 나오나"* 까지다. 그 다음 질문,
*"나온 화면을 이 클라가 그릴 줄 아나"* 는 아무도 안 물었다.

그 자리가 비면 어떻게 되는지는 이 저장소가 이미 두 번 겪었다. 선언형 화면 스펙
(`Registry.plugin_screen`)의 계약은 `kind` 한 낱말이고, GUI 는 자기가 아는 모양만
그린다. 정본이 **일곱째 모양**을 내기 시작하면 GUI 는 *"이 화면 모양은 아직 못
그립니다"* 한 줄을 띄우고 — 조용히 버리지는 않지만(설계 §8-5) — **아무 게이트도
울지 않는다.** 사용자에게는 죽은 명령으로 보이고, 그것이 ⓡ(`close-clock`)·pytmux-20
이 남긴 부류 그대로다.

⛔ **정본 훅의 독스트링은 이 질문에 답을 못 준다.** 거기 적힌 계약은 아직도
`"kind": "list"|"text"` 둘인데(`pytmuxlib/plugins/__init__.py`), 실측하면 정본은
**여섯**을 낸다. 글로 적힌 계약과 코드가 내는 값이 갈린 지 오래고, 그 갈림을 재는
것이 이 파일이다.

# 어떻게 재나 — 왜 드라이브가 아니라 소스 훑기인가

옆 생성기 둘은 정본을 **불러서**(`handle_command`·`plugin_screen`) 판정한다. 여기서는
그 길이 통째로 막힌다:

 · 스펙을 내는 자리의 절반이 **비동기**다(`mdir`·`ncd`·`p4changes`·`claude-resume` 는
   코루틴/Future 를 돌려준다 — 파일시스템·p4 를 executor 로 넘긴다). 인형 앱에
   대고 부르면 `kind` 가 든 dict 는 **영영 안 나온다**.
 · 나머지도 **열기(`do=open`) 한 갈래만** 답한다. `prompt`·`confirm` 은 화면 **안**에서
   답을 물을 때만 나오는 모양이라(`mdir` 의 이름 묻기·지움 확인), 열기만 눌러서는
   한 번도 안 보인다.

그래서 부르는 대신 **읽는다**. `"t": "plugin_screen"` 이 든 dict 리터럴을 AST 로 찾아
그 `kind` 자리를 본다. 값이 글자면 그대로 받고, 값이 **매개변수**면(스펙을 한 곳에서
짓는 공장 — `screenspec._spec` · `mdir._ask` · `namesync._ask`) 그 공장의 **호출부**를
다시 훑어 실제로 실리는 글자를 모은다.

⛔ **못 푼 자리를 초록으로 위장하지 않는다**(저장소 규율). 글자가 아닌 것이 실리면
`unresolved` 에 자리와 함께 남기고, 적합성 테스트가 그 목록이 비었는지 단언한다 —
비면 "전수로 셌다"가 참이고, 안 비면 **무엇을 못 셌는지가 보인다.**

출력: `crates/proto/tests/fixtures/plugin_screens.json`
"""
from __future__ import annotations

import argparse
import ast
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PYTMUX = HERE.parent.parent
DEFAULT_OUT = HERE.parent / "crates" / "proto" / "tests" / "fixtures" / "plugin_screens.json"

# 화면 스펙임을 알아보는 표식 — 정본 훅의 계약이 정한 값이다(`Registry.plugin_screen`).
SPEC_MARK = "plugin_screen"


def _dict_get(node: ast.Dict, key: str):
    """dict 리터럴에서 글자 열쇠 하나를 꺼낸다(없으면 `None`)."""
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and k.value == key:
            return v
    return None


def _is_spec_dict(node: ast.Dict) -> bool:
    t = _dict_get(node, "t")
    return isinstance(t, ast.Constant) and t.value == SPEC_MARK


def _enclosing(tree: ast.AST):
    """`{자식 노드: 그 노드를 품은 함수}` — `kind` 가 매개변수인지 가리려면 필요하다."""
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for child in ast.walk(fn):
            # 안쪽 함수가 이기게 둔다 — 나중에 덮어쓰는 것이 곧 더 좁은 자리다.
            out[child] = fn
    return out


def _param_slot(fn, name: str):
    """그 이름이 이 함수의 매개변수면 `(자리, 이름)`. `self` 는 자리에서 뺀다 —
    호출부는 `self.f(x)` 로 부르므로 위치 인자의 번호가 하나씩 밀린다."""
    args = list(fn.args.posonlyargs) + list(fn.args.args)
    names = [a.arg for a in args]
    if name not in names:
        for a in list(fn.args.kwonlyargs):
            if a.arg == name:
                return (None, name)      # 키워드로만 받는다
        return None
    idx = names.index(name)
    if names and names[0] in ("self", "cls"):
        idx -= 1
        if idx < 0:
            return None
    return (idx, name)


def _call_name(call: ast.Call) -> str | None:
    """`f(...)` · `self.f(...)` · `mod.f(...)` 를 전부 `f` 로 읽는다.

    ⚠ 이름만으로 이으면 **남의 동명 함수**를 끌어온다 — 실측으로 물렸다:
    `mdir._spec(mine, row, note)` 의 둘째 자리는 줄 번호인데, `screenspec._spec(sid,
    kind, …)` 의 둘째 자리가 `kind` 라서 mdir 의 호출 열둘이 통째로 *"kind 가 글자가
    아니다"* 로 셌다. 그래서 호출부는 **공장을 정의한 파일 안에서** 찾는다(아래
    `collect` ②) — 이 공장들은 전부 `_` 로 시작하는 모듈 안쪽 함수다."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _literal(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def collect(pytmux_root: pathlib.Path) -> dict:
    root = pytmux_root / "pytmuxlib" / "plugins"
    files = sorted(root.rglob("*.py"))
    trees: list[tuple[pathlib.Path, ast.AST]] = []
    for path in files:
        try:
            trees.append((path, ast.parse(path.read_text(encoding="utf-8"), str(path))))
        except SyntaxError as exc:                      # 못 읽은 것은 삼키지 않는다
            raise SystemExit(f"파싱 실패 {path}: {exc}")

    kinds: set[str] = set()
    sites: dict[str, list[str]] = {}
    # (공장을 정의한 파일, 공장 이름, 위치 자리, 키워드 이름)
    holes: list[tuple[str, str, int | None, str]] = []
    unresolved: list[str] = []

    def note(kind: str, where: str) -> None:
        """이 모양을 내는 자리를 적는다. ⛔ **`where` 는 파일까지다 — 줄 번호를 담지 않는다.**

        판정 축은 `kinds` 와 `unresolved` 둘뿐이다(픽스처를 읽는
        `crates/proto/tests/plugin_screen_conformance.rs` 가 그 둘만 역직렬화한다).
        `sites` 는 *"어느 파일이 이 모양을 내나"* 를 사람에게 알려 주는 참고인데,
        여기에 줄 번호를 담았더니 **플러그인 파일을 한 줄만 고쳐도** 픽스처가 낡아
        게이트 둘(`check_fixtures` · `test_surface_ledger`)이 울었다 — 화면 모양의
        집합은 하나도 안 바뀐 채로다(pytmux-394 · 실측 2026-08-24).

        그 상시 적색이 가르치는 것은 「이 게이트는 원래 붉다」이고, 그러면 **진짜
        갈림(새 화면 모양)이 왔을 때 아무도 안 본다.** 그래서 참고를 판정에서 뺐다.
        줄 번호가 필요하면 그 파일에서 `grep -n '"t": "plugin_screen"'` 로 그때 찾는다
        — 픽스처에 굳혀 두는 것보다 싸고, 언제나 지금 값이다.

        ⚠ `unresolved` 는 줄 번호를 **그대로** 든다. 그것은 참고가 아니라 *"여기를
        못 풀었다"* 는 진단이고, 비어 있는 것이 정상이라(적합성 테스트가 단언한다)
        무관한 편집으로 흔들리지 않는다."""
        kinds.add(kind)
        sites.setdefault(kind, []).append(where)

    # ① 스펙 dict 리터럴 — `kind` 가 글자면 그대로, 매개변수면 공장으로 적어 둔다.
    for path, tree in trees:
        rel = path.relative_to(pytmux_root).as_posix()
        owner = _enclosing(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict) or not _is_spec_dict(node):
                continue
            where = f"{rel}:{node.lineno}"
            kv = _dict_get(node, "kind")
            if kv is None:
                unresolved.append(f"{where} — 스펙에 kind 칸이 없다")
                continue
            lit = _literal(kv)
            if lit is not None:
                note(lit, rel)          # 자리는 파일까지만 — `note` 머리말 참조
                continue
            fn = owner.get(node)
            if isinstance(kv, ast.Name) and fn is not None:
                slot = _param_slot(fn, kv.id)
                if slot is not None:
                    holes.append((rel, fn.name, slot[0], slot[1]))
                    continue
            unresolved.append(f"{where} — kind 를 소스에서 못 푼다({ast.dump(kv)[:60]})")

    # ② 공장의 호출부 — 실제로 실리는 글자를 모은다.
    #
    # ⛔ **정의한 파일 안에서만** 찾는다. 이 공장들은 `_` 로 시작하는 모듈 안쪽
    #    함수라 밖에서 부를 자리가 없고, 이름만으로 넓히면 동명 함수를 끌어와
    #    거짓 실패를 만든다(`_call_name` 의 실측 참조).
    for home, factory, slot, kwname in sorted(set(holes)):
        found = False
        for path, tree in trees:
            rel = path.relative_to(pytmux_root).as_posix()
            if rel != home:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _call_name(node) != factory:
                    continue
                arg = None
                for kw in node.keywords:
                    if kw.arg == kwname:
                        arg = kw.value
                if arg is None and slot is not None and slot < len(node.args):
                    arg = node.args[slot]
                if arg is None:
                    continue                # 그 자리를 안 준다 = 기본값 갈래(스펙 아님)
                found = True
                lit = _literal(arg)
                if lit is not None:
                    note(lit, rel)      # 자리는 파일까지만 — `note` 머리말 참조
                else:
                    unresolved.append(
                        f"{rel}:{node.lineno} — {factory}() 의 kind 가 글자가 아니다"
                    )
        if not found:
            unresolved.append(f"{home} 의 {factory}() 를 부르는 자리가 없다 — 공장이 죽었나")

    return {
        "kinds": sorted(kinds),
        "sites": {k: sorted(set(v)) for k, v in sorted(sites.items())},
        "unresolved": sorted(set(unresolved)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", type=pathlib.Path, default=DEFAULT_PYTMUX)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true", help="파일 대신 표준출력으로")
    args = ap.parse_args()

    data = collect(args.pytmux)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.print:
        sys.stdout.write(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(
        f"{args.out}: 화면 모양 {len(data['kinds'])}"
        f" · 못 푼 자리 {len(data['unresolved'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
