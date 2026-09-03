#!/usr/bin/env python3
"""파이썬 클라의 **조작 표면**을 픽스처로 뽑는다 — 패리티의 자.

# 왜 필요한가

네이티브 클라를 파이썬 클라 수준까지 끌고 가는 과정에서(패리티 로드맵 G0) 가장 먼저 썩는
것은 **"무엇이 남았나" 목록**이다. 손으로 센 목록은 조용히 낡는다 — pytmux 저장소의
HANDOFF §10 머리말이 경고하는 바로 그 패턴이고, 이 저장소도 같은 함정을 이미 한 번
밟았다(테스트가 이름 11개를 손으로 적어 두고 자기끼리 맞춰 보던 자리).

그래서 목록을 **파이썬 구현에서 직접 뽑는다**. 파이썬 클라가 명령을 하나 늘리면 이 픽스처가
늘고, Rust 쪽 대조 테이블이 그것을 분류할 때까지 게이트가 운다(`parity.rs`).

# 무엇을 뽑나

| 키 | 출처 | 뜻 |
|---|---|---|
| `commands` | `clientutil.COMMANDS` | 명령 팔레트에 뜨는 이름 + 범주 |
| `prefix_keys` | `clientutil.PREFIX_KEYS` | prefix 모드 키 |
| `esc_keys` | `clientutil.ESC_MODE_KEYS` | esc 모드 키 |
| `menu_items` | `clientutil.MENU_ITEMS` | F10 메뉴 항목 |
| `mouse_gestures` | `i18n` 의 `keys.g_*` | `list-keys` 가 보여 주는 마우스 제스처 |
| `settings` | `clientutil.SETTINGS` | 설정 화면 항목 |
| `set_options` | `clientutil._SET_OPTION_NAMES` | `set` 명령이 받는 옵션 |
| `screens` | `clientscreens` 의 `*Screen` 클래스 | 팝업·모달 화면 |
| `client_cmds` | `clientcmd._run_command` 의 분기 | 정본이 **실제로 받는** 명령 이름(별칭 포함) |
| `client_cmd_groups` | 같은 분기를 **묶음째** | 어느 이름들이 한 갈래인가 + 그 갈래가 이름으로 다시 가르나 |
| `scroll_keys` | `clientio._handle_scroll_key` 의 분기 | 정본 스크롤 모드가 **실제로 받는** 키 |
| `esc_key_modes` | `clientio._handle_esc_mode` 의 분기 | esc 모드에서 그 키를 누르면 **모드가 어떻게 되나** |
| `prefix_key_modes` | `clientio._handle_prefix` 의 분기 | prefix 모드의 같은 것 |

**세는 단위를 바꾸지 말 것.** 이 파일이 정하는 것은 "패리티를 무엇으로 세는가"이고, 그
단위가 흔들리면 진행률이 의미를 잃는다.

# 왜 import 로 뽑나

`clientutil` 은 Textual 을 안 끌어온다(순수 데이터 표라서). 화면 목록만은 소스를 정규식으로
읽는다 — `clientscreens` 는 import 하는 순간 Textual 을 요구하고, 그건 이 저장소의
의존이 아니다.

    python3 scripts/gen_client_surface_fixture.py [--pytmux ..]
"""

import argparse
import ast
import json
import os
import re
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "client_surface.json")


def _utf8_stdout():
    """cp949 콘솔에서 요약 print 가 죽지 않게(생성기 공통 — gen_command_fixture 참조)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _screens(root):
    """`clientscreens.py` 의 화면 클래스 이름.

    import 하지 않는 이유: 그 모듈은 Textual 을 요구한다. 클래스 선언은 정규식으로도
    정확히 잡히고, **잡힌 것이 0이면 실패로 떨어뜨린다** — 빈 결과를 통과로 두면
    `check_licenses.sh` 가 한 번 밟은 "안 우는 게이트"가 된다.
    """
    path = os.path.join(root, "pytmuxlib", "clientscreens.py")
    with open(path, encoding="utf-8") as fp:
        names = re.findall(r"^class\s+(\w+Screen)\s*\(", fp.read(), re.M)
    if not names:
        sys.exit(f"{path} 에서 화면 클래스를 하나도 못 찾았다 — 뽑는 방법이 틀렸다")
    return sorted(set(names))



def _branch_names(root, relpath, func, names):
    """핸들러의 **분기 조건**에서 문자열 상수를 긁는다 — 손으로 적은 미러가 아니라.

    # 왜 소스를 읽나 (표가 아니라)

    정본은 이 둘을 **표로 안 들고 있다**: 명령 해석도 스크롤 키도 `if/elif` 체인이다.
    그 옆에 사람이 적어 둔 표(`clientutil.ESC_MODE_KEYS` 머리말이 스스로 *"데이터-주도가
    아니라 수동 미러"* 라고 적는다)는 낡을 수 있고, 실제로 낡은 것을 이 저장소가 이미
    두 번 잡았다(플러그인 훅 독스트링의 `"list"|"text"` · 이 파일이 뽑는 `client_cmds`
    가 드러낸 팔레트 밖 이름들). 그래서 **분기 자체**를 읽는다.

    `func` 안에서 `<names 중 하나> == "..."` 와 `<...> in ("...", ...)` 를 전부 모은다.
    잡힌 것이 0이면 실패로 떨어뜨린다 — 뽑는 방법이 틀렸는데 조용히 빈 집합을 돌려주면
    그 위에 선 게이트가 **아무것도 안 재면서 초록**이 된다.
    """
    path = os.path.join(root, *relpath)
    with open(path, encoding="utf-8") as fp:
        tree = ast.parse(fp.read(), filename=path)
    found = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == func):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Compare):
                continue
            if not (isinstance(sub.left, ast.Name) and sub.left.id in names):
                continue
            for op, comp in zip(sub.ops, sub.comparators):
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                    if isinstance(comp.value, str):
                        found.add(comp.value)
                elif isinstance(op, ast.In) and isinstance(
                    comp, (ast.Tuple, ast.List, ast.Set)
                ):
                    for elt in comp.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            found.add(elt.value)
    if not found:
        sys.exit(f"{path} 의 {func} 에서 분기 이름을 하나도 못 찾았다 — 뽑는 방법이 틀렸다")
    return sorted(found)



def _branch_groups(root, relpath, func, names):
    """같은 분기에 묶인 이름들을 **묶음째** 뽑는다 — 별칭 표의 재료(pytmux-470).

    # ⛔ 묶음은 별칭 관계가 **아니다**

    이슈가 처음 적은 처방은 *"그 묶음을 그대로 뽑으면 된다"* 였는데 실측이 그것을
    뒤집었다. 정본에는 이런 갈래가 있다:

        c in ("pin-tab", "pin", "unpin-tab", "unpin", "pin-toggle")

    그대로 접으면 **`unpin` 이 pin 을 한다.** 몸통이 `c` 를 다시 보고 이름마다 다른 일을
    하기 때문이다. 그래서 묶음마다 **`dispatches_on_name`** 을 함께 적는다:

        그 `if` 의 몸통이 `c` 를 다시 안 보면 → 이름들은 진짜 동의어(접어도 된다)
        다시 보면                            → 이름으로 가르는 갈래(접으면 안 된다)

    실측(2026-09-04 · 90갈래 · 이름 195): 진짜 별칭 69갈래(176이름) · 가르는 갈래 3(12이름).

    ⚠ **누가 어느 이름의 별칭인가는 여기서 안 정한다.** 「팔레트 이름」을 아는 것은
    소비자(Rust `base::PALETTE`)이고, 이 생성기는 정본만 읽는다 — 그 경계를 넘으면
    픽스처가 우리 쪽 표를 알게 된다.
    """
    path = os.path.join(root, *relpath)
    with open(path, encoding="utf-8") as fp:
        tree = ast.parse(fp.read(), filename=path)
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == func]
    if not fns:
        sys.exit(f"{path} 에 {func} 가 없다")
    out = []
    for node in ast.walk(fns[0]):
        if not isinstance(node, ast.If):
            continue
        found = []
        for sub in ast.walk(node.test):
            if not (isinstance(sub, ast.Compare)
                    and isinstance(sub.left, ast.Name) and sub.left.id in names):
                continue
            for op, comp in zip(sub.ops, sub.comparators):
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                    if isinstance(comp.value, str):
                        found.append(comp.value)
                elif isinstance(op, ast.In) and isinstance(
                    comp, (ast.Tuple, ast.List, ast.Set)
                ):
                    for elt in comp.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            found.append(elt.value)
        if not found:
            continue
        dispatches = any(
            isinstance(sub, ast.Name) and sub.id in names
            for st in node.body for sub in ast.walk(st)
        )
        out.append({"names": sorted(set(found)),
                    "dispatches_on_name": bool(dispatches)})
    if not out:
        sys.exit(f"{path} 의 {func} 에서 갈래를 하나도 못 찾았다 — 뽑는 방법이 틀렸다")
    return sorted(out, key=lambda g: g["names"])


def _mode_effects(root, relpath, func, names, exits):
    """키 하나를 누르면 **모드가 어떻게 되나** — 정본 핸들러의 분기에서 직접 뽑는다.

    # 왜 이 축이 필요한가 (pytmux-33 ⓖ3)

    [[pytmux-185]] 이 GUI 의 최소 요건으로 못박은 것은 그림이 아니라 **「키 반응 ·
    취소 조건 · 포커스 이동」**이다. 그런데 「그 키를 누르면 모드가 풀리나」를 재는 자가
    이 저장소에 **하나도 없었다** — 패리티 표는 *"그 키가 있나"* 만 묻고, 상호작용 계약은
    **화면**의 키를 잰다. 그 사이로 실제로 둘이 샜다(실측 2026-09-02):

    - 정본은 esc 모드에서 **방향키로 패널을 옮겨도 모드를 유지**한다(연속 이동).
      GUI 는 한 번 옮기고 모드가 풀렸다.
    - 정본은 esc 모드에서 **모르는 키를 누르면 모드를 푼다**(`else: self._exit_esc()`).
      GUI 는 모드를 유지해, 잘못 누른 뒤의 타이핑이 통째로 표에 부딪혀 사라졌다 —
      GUI 자기 주석이 prefix 에 대해 *"그러면 키가 안 먹는 것으로 보인다"* 고 경고하는
      바로 그 실패다.

    # 어떻게 뽑나

    함수의 **본문 앞머리**(분기 전)에서 `self.mode = "..."` 를 먼저 본다 — prefix 핸들러는
    거기서 한 번에 `normal` 로 돌리고 분기는 예외만 적는다. 그 다음 `if/elif` 사슬을 걸어
    분기마다 `ch`/`k`/`event.key` 와 견주는 문자열을 모으고, 그 몸에서 `_exit_esc()` 호출과
    `self.mode = "..."` 대입을 찾아 셋 중 하나로 접는다: `exit`(평소 모드로) · `stay`(모드
    유지) · 그 밖의 모드 이름(예 `scroll`). 같은
    걸음에 **패널로 나가는 바이트**도 적는다(`sends` — `sends_of` 머리말).

    마지막 `else` 는 **모르는 키**의 답이라 `"*"` 로 적는다 — 그 한 줄이 위 둘째 결함을
    잡은 자리다.
    """
    path = os.path.join(root, *relpath)
    with open(path, encoding="utf-8") as fp:
        tree = ast.parse(fp.read(), filename=path)
    fns = [n for n in ast.walk(tree)
           if isinstance(n, ast.FunctionDef) and n.name == func]
    if not fns:
        sys.exit(f"{path} 에 {func} 가 없다 — 뽑는 방법이 틀렸다")
    fn = fns[0]

    def sends_of(stmts):
        """이 몸이 **패널로 바이트를 보내나** — 보내면 그 상수(16진).

        모드와 함께 뽑는 이유: 정본이 「모드만 빠지고 **ESC 는 안 보낸다**」를 사용자
        요청으로 못박은 자리가 있는데(`_handle_esc_mode` 의 둘째 ESC · 56632 불변),
        모드만 재면 그 절반이 안 잡힌다. 실측으로 GUI 는 거기서 ESC 를 보내고 있었다.
        """
        for node in ast.walk(ast.Module(body=list(stmts), type_ignores=[])):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "send_input" and node.args):
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, bytes):
                    return arg.value.hex()
                return "?"      # 값이 상수가 아니다 — 「보낸다」까지만 말한다
        return None

    def mode_of(stmts, default):
        """이 몸이 모드를 어떻게 두나. 못 찾으면 `default`.

        ⛔ **소스 차례로 본다.** `ast.walk` 의 차례는 소스 차례가 아니라, 그냥 훑으면
        `self._exit_esc()` 뒤에 오는 `self.mode = "scroll"` 을 못 이겨 프롬프트 점프가
        `exit` 로 읽혔다(실측). 마지막에 놓인 것이 이긴다 — 그것이 그 키의 답이다.
        """
        marks = []
        for node in ast.walk(ast.Module(body=list(stmts), type_ignores=[])):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in exits):
                marks.append((node.lineno, node.col_offset, "exit"))
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Attribute) and tgt.attr == "mode":
                        marks.append((
                            node.lineno, node.col_offset,
                            "exit" if node.value.value == "normal" else node.value.value,
                        ))
        marks.sort()
        return marks[-1][2] if marks else default

    # 분기 **앞**에 놓인 문장들이 정하는 기본값(prefix 핸들러의 `self.mode = "normal"`).
    head = [st for st in fn.body if not isinstance(st, ast.If)]
    base = mode_of(head, "stay")

    def literals(test):
        out = []
        for node in ast.walk(test):
            if not isinstance(node, ast.Compare):
                continue
            left = node.left
            name = (left.id if isinstance(left, ast.Name)
                    else getattr(left, "attr", "") if isinstance(left, ast.Attribute)
                    else "")
            if name not in names:
                continue
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                    if isinstance(comp.value, str):
                        out.append(comp.value)
                elif isinstance(op, ast.In) and isinstance(
                    comp, (ast.Tuple, ast.List, ast.Set)
                ):
                    for elt in comp.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            out.append(elt.value)
        return out

    found = {}
    for st in fn.body:
        if not isinstance(st, ast.If):
            continue
        node = st
        while True:
            for key in literals(node.test):
                # 앞선 분기가 이긴다 — 사슬은 위에서부터 걸린다.
                found.setdefault(key, {"mode": mode_of(node.body, base),
                                       "sends": sends_of(node.body)})
            if len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If):
                node = node.orelse[0]
                continue
            if node.orelse:
                found.setdefault("*", {"mode": mode_of(node.orelse, base),
                                       "sends": sends_of(node.orelse)})
            break
    found.setdefault("*", {"mode": base, "sends": None})
    if len(found) < 2:
        sys.exit(f"{path} 의 {func} 에서 분기를 못 읽었다 — 뽑는 방법이 틀렸다")
    return dict(sorted(found.items()))


def main():
    _utf8_stdout()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()

    root = os.path.abspath(args.pytmux)
    if not os.path.isdir(root):
        sys.exit(f"pytmux 저장소를 못 찾았다: {root}")
    sys.path.insert(0, root)
    from pytmuxlib import clientutil as cu
    from pytmuxlib import i18n as py_i18n

    # 이름순으로 적는다 — 소스에서 항목을 옮기는 것만으로 픽스처가 바뀌면 diff 가 잡음이
    # 되고 진짜 변경(추가·삭제·이름)이 묻힌다(gen_command_fixture 와 같은 규칙).
    payload = {
        "_comment": "python3 scripts/gen_client_surface_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/clientutil.py 의 카탈로그 + clientscreens.py 의 화면 "
                    "클래스. 이 픽스처가 패리티(로드맵 G0)의 세는 단위다.",
        "commands": {name: cat for name, _desc, cat in sorted(cu.COMMANDS)},
        # 팔레트가 이름 옆에 보여 줄 **설명**(G9q). 범주와 갈라 둔 이유: commands 의
        # 값(범주)을 바꾸면 세는 단위가 흔들린다 — 위 머리말의 금지 조항.
        "command_help": {name: desc for name, desc, _cat in sorted(cu.COMMANDS)},
        # 설명의 **영어 번역**(정본 clientutil 의 i18n.register en 블록, `cmd.<name>`).
        # 네이티브는 ko 원문이 msgid 라 (ko→en) 표로 접어 쓴다 — 적합성 테스트
        # (`help_i18n.rs`)가 이 값과 en_proto.rs 표의 동치를 강제한다. import 시점에
        # clientutil 이 register 를 이미 마쳤으므로 카탈로그에서 바로 읽는다.
        "command_help_en": {name: py_i18n._CATALOG["en"][f"cmd.{name}"]
                            for name, _desc, _cat in sorted(cu.COMMANDS)
                            if f"cmd.{name}" in py_i18n._CATALOG["en"]},
        "prefix_keys": {ident: key for ident, key, _ko, _en
                        in sorted(cu.PREFIX_KEYS)},
        "esc_keys": {ident: key for ident, key, _ko, _en
                     in sorted(cu.ESC_MODE_KEYS)},
        "menu_items": sorted({item[0] for item in cu.MENU_ITEMS}),
        # `list-keys`(= `mouse-help`) 가 먼저 보여 주는 **마우스 제스처** 절의 항목들
        # (`clientcmd.py` 의 `keys.g_*` 카탈로그 키). 정본 주석이 이 절을 만든 이유를
        # 적어 둔다 — "구현된 제스처가 명령에도 메뉴에도 안 떠 사장돼 있었다".
        # 문구가 아니라 **몇 가지를 보여 주는가**를 센다: 우리는 같은 제스처를 다르게
        # 묶어 적기도 해서(클릭을 휠 줄에) 글자 대조는 거짓 실패를 낳는다.
        "mouse_gestures": sorted(
            key for key in py_i18n._CATALOG["ko"] if key.startswith("keys.g_")
        ),
        # 설정 항목은 dict 다 — 키 이름과 **어디에 저장되나**(config/서버 옵션)를
        # 함께 적는다. G5 에서 "설정의 권위"를 가르는 값이 이것이다.
        "settings": {s["key"]: s.get("backend", "?") for s in
                     sorted(cu.SETTINGS, key=lambda s: s["key"])},
        "set_options": sorted(cu._SET_OPTION_NAMES),
        "screens": _screens(root),
        # ── 「표가 아니라 분기」에서 뽑는 둘 ────────────────────────────────
        # 정본 팔레트(`COMMANDS`)는 **보여 주는 목록**이고, 실제로 받는 이름은 그보다
        # 많다(별칭·tmux 축약·팔레트에 안 실은 것). GUI 팔레트에 정본에 없는 이름이
        # 섰을 때 *"정본에 정말 없나"* 를 묻는 자가 여기다 — 목록만 보면 있는 것을
        # 없다고 읽는다(실측: `popup-close`·`resync`·`plugin-manager` 셋이 그랬다).
        "client_cmds": _branch_names(
            root, ("pytmuxlib", "clientcmd.py"), "_run_command", {"c"}
        ),
        # 위와 **같은 분기**를 묶음째. 별칭 표(`base::command_alias`)가 이것으로 선다 —
        # 그리고 `dispatches_on_name` 갈래는 **접으면 뜻이 바뀐다**(머리말 참조).
        "client_cmd_groups": _branch_groups(
            root, ("pytmuxlib", "clientcmd.py"), "_run_command", {"c"}
        ),
        # 스크롤 모드도 같은 모양이다 — 정본은 키 표를 안 들고 `if/elif` 로 가른다.
        "scroll_keys": _branch_names(
            root, ("pytmuxlib", "clientio.py"), "_handle_scroll_key", {"k", "ch"}
        ),
        # ── 「키를 누르면 모드가 어떻게 되나」 ────────────────────────────────
        # 패리티 표는 *"그 키가 있나"* 만 묻는다. [[pytmux-185]] 가 GUI 의 최소 요건으로
        # 못박은 것은 **키 반응과 취소 조건**이고, 그 축을 재는 자가 여기 서기 전까지
        # 하나도 없었다(`_mode_effects` 머리말이 그 사이로 샌 둘을 적는다).
        "esc_key_modes": _mode_effects(
            root, ("pytmuxlib", "clientio.py"), "_handle_esc_mode",
            {"ch", "k", "key"}, {"_exit_esc"},
        ),
        "prefix_key_modes": _mode_effects(
            root, ("pytmuxlib", "clientio.py"), "_handle_prefix",
            {"ch", "k", "key"}, {"_exit_esc"},
        ),
    }

    for key in ("commands", "command_help_en", "prefix_keys", "esc_keys",
                "menu_items", "mouse_gestures", "settings", "set_options",
                "screens", "client_cmds", "client_cmd_groups", "scroll_keys",
                "esc_key_modes", "prefix_key_modes"):
        if not payload[key]:
            sys.exit(f"'{key}' 가 비었다 — 카탈로그 이름이 바뀌었을 것이다")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=False)
        fp.write("\n")

    sizes = " · ".join(f"{k} {len(payload[k])}" for k in
                       ("commands", "prefix_keys", "esc_keys", "menu_items",
                        "settings", "set_options", "screens", "client_cmds",
                        "scroll_keys", "esc_key_modes", "prefix_key_modes"))
    print(f"{args.out} — {sizes}")


if __name__ == "__main__":
    main()
