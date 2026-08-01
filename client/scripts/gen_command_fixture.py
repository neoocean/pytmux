#!/usr/bin/env python3
"""명령 테이블 픽스처 — 클라가 부르는 이름이 서버에 실제로 있는가.

# 왜 이 표가 필요한가

이름이 어긋나도 **아무 소리가 안 난다.** `serverio._handle_cmd` 는 `_CMD_TABLE` 에 없는
action 을 플러그인 훅으로 넘기고(`_dispatch_plugin_cmd`), 아무 플러그인도 안 집으면 조용히
끝난다. 예외도 로그도 없다 — 사용자에게는 "키가 안 먹는다"로만 보인다.

종전에는 이름 11개를 Rust 테스트 안에 **손으로 적어 두고** 그 목록과 구현을 서로 대조했다.
자기 구현을 자기가 확인하는 모양이라, `servercmd.py` 가 이름을 바꾸면 테스트는 초록인 채로
어긋난다. 같은 함정을 엔드포인트 규칙에서 이미 한 번 밟았다(P6 에서 잡은 `PYTMUX_HOME`
버그 — 옛 테스트가 틀린 값을 그대로 단언하고 있었다).

# disposition 도 같이 뽑는 이유

클라는 "명령을 보내면 서버가 full 프레임으로 재동기해 준다"를 전제로 로컬 상태를 낙관적으로
고치지 않는다(`command.rs` 모듈 주석 — 서버가 권위). 그 전제는 disposition 이 `full` 일
때만 성립한다. `handled`/`dynamic` 인 명령은 트리 콜백 broadcast 에 기대므로 **어느 명령이
거기 해당하는지가 클라 쪽 계약**이다. 서버가 조용히 `full` → `handled` 로 바꾸면 증상은
"명령은 먹었는데 화면이 안 바뀐다"이고, 이름 대조만으로는 안 잡힌다.

# 어떻게 뽑는가

`pytmuxlib.servercmd` 를 import 하면 클래스 본문의 `@_cmd` 데코레이터가 `_CMD_TABLE` 을
채운다. 서버를 띄우거나 드라이브할 필요가 없다 — 표는 import 시점에 완성된다.

표 전체(71개)를 적는다. 클라가 쓰는 것은 그중 일부지만, 전부 적어 두면 서버가 이름을 바꿀
때 **픽스처 diff 에 보인다**. 클라가 안 쓰는 이름이 사라지는 것은 오류가 아니므로 Rust 쪽
단언은 "클라가 부르는 이름이 표에 있는가" 한 방향만 본다.

    python3 scripts/gen_command_fixture.py [--pytmux ..]
"""

import argparse
import inspect
import json
import os
import re
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "commands.json")


def _utf8_stdout():
    """Windows 콘솔의 기본 코드페이지(한국어=cp949)에서 한글 출력이 죽지 않게.

    이 스크립트들은 마지막에 결과 요약을 한글로 찍는다. cp949 콘솔에서는 그 print 가
    UnicodeEncodeError 로 죽는데, **파일은 이미 다 쓴 뒤**라 종료코드 1 만 보고
    "생성 실패"로 오인하게 된다(2026-07-28 실측: 생성기 6개 전부 그랬다).
    출력 스트림만 UTF-8 로 돌린다 — 생성 결과에는 영향이 없다.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _dispatched_in_io(root):
    """`serverio.py` 가 표를 거치지 않고 직접 처리하는 action 이름들.

    소스를 정규식으로 긁는다 — 데코레이터가 없어 import 로는 알 수 없다. 그래서 **못
    찾으면 실패**로 떨어뜨린다(빈 결과가 통과로 보이던 라이선스 게이트의 회귀와 같은
    부류). disposition 은 `handled` 다: 이 갈래들은 자기가 알아서 회신하고 돌아가므로
    끝의 full 재동기를 안 탄다."""
    path = os.path.join(root, "pytmuxlib", "serverio.py")
    with open(path, encoding="utf-8") as fp:
        text = fp.read()
    names = sorted(set(re.findall(r'if action == "(\w+)":', text)))
    if not names:
        sys.exit(f"{path} 에서 직접 처리 action 을 하나도 못 찾았다 — 형태가 바뀌었으면 "
                 f"이 정규식을 고칠 것(빈 결과는 통과가 아니라 고장이다)")
    return {n: "handled" for n in names}


# `msg.get("x")` · `msg["x"]` 둘 다 잡는다. 서버는 두 표기를 섞어 쓴다.
_MSG_KEY = re.compile(
    r"""msg\.get\(\s*["'](\w+)["']|msg\[\s*["'](\w+)["']\s*\]"""
)


def _keys_in(text):
    return {a or b for a, b in _MSG_KEY.findall(text)}


def _action_branches(text):
    """`if/elif action == "x":` 갈래의 이름 → 몸통 소스들.

    몸통은 **들여쓰기로** 끊는다. 다음 `if action ==` 까지로 자르면 마지막 갈래가 파일
    끝까지 먹어, 그 명령의 허용 칸이 파일 전체의 칸 이름이 되어 게이트가 통째로
    헐거워진다(처음 그렇게 짰다가 `select_window` 에 `data`·`delta`·`cols` 가 딸려 왔다).
    """
    out = {}
    offset = 0
    spots = {}
    for line in text.splitlines(keepends=True):
        m = re.match(r'(\s*)(?:if|elif) action == "(\w+)":', line)
        if m:
            spots.setdefault(m.group(2), []).append((offset, len(m.group(1))))
        offset += len(line)
    for name, places in spots.items():
        for start, indent in places:
            body = []
            for line in text[start:].splitlines(keepends=True)[1:]:
                stripped = line.strip()
                # 빈 줄·주석은 들여쓰기를 안 믿는다(빈 줄의 indent 는 0이다).
                if stripped and (len(line) - len(line.lstrip())) <= indent:
                    break
                body.append(line)
            out.setdefault(name, []).append("".join(body))
    return out


def _method_source(text, name):
    """`def <name>(` 부터 들여쓰기가 되돌아올 때까지. 못 찾으면 `None`."""
    offset = 0
    for line in text.splitlines(keepends=True):
        m = re.match(rf"(\s*)def {re.escape(name)}\(", line)
        if m:
            indent = len(m.group(1))
            body = []
            for follow in text[offset:].splitlines(keepends=True)[1:]:
                stripped = follow.strip()
                if stripped and (len(follow) - len(follow.lstrip())) <= indent:
                    break
                body.append(follow)
            return "".join(body)
        offset += len(line)
    return None


# 플러그인 훅이 돌려주는 지시 → 클라 쪽 계약(`full`/`handled`).
#
# `send_full` 은 요청한 클라에게 전체 재동기를 보내고, `broadcast` 는 거기에 세션 방송을
# 더한다(`serverio._dispatch_plugin_cmd`) — **둘 다 full 이 온다.** 클라가 낙관적 갱신을
# 안 해도 되는가라는 물음에서는 표의 FULL 과 같은 자리다.
_PLUGIN_DIRECTIVE = {
    "handled": "handled",
    "send_full": "full",
    "broadcast": "full",
}


def _plugin_commands(root):
    """플러그인이 소유한 action — 표에도 `serverio` 직접 갈래에도 없다.

    # 왜 필요한가 (2026-07-29)

    `_CMD_TABLE` 에 없는 action 은 `serverio._dispatch_plugin_cmd` 가 플러그인의
    `server_command` 훅으로 넘긴다. 그래서 `jump_prompt`(claude-code 플러그인 —
    esc Ctrl+↑/↓ 프롬프트 점프) 같은 이름은 **서버에 실재하는데도** 표만 뽑는 픽스처에는
    없었고, 클라가 보내면 적합성 게이트가 "서버에 없는 명령"으로 잡았다.

    반대 방향의 위험도 같다: 플러그인이 이름을 바꾸면 클라는 조용히 무동작이 된다
    (훅 체인이 아무도 안 집으면 예외도 로그도 없다 — 이 픽스처의 존재 이유 그대로).

    `server_command` 훅의 몸통만 본다. 같은 파일의 `handle_server_request` 는 회신
    dict 를 돌려주는 **다른 계약**이라(disposition 이 없다) 여기 섞지 않는다.
    """
    root_dir = os.path.join(root, "pytmuxlib", "plugins")
    if not os.path.isdir(root_dir):
        sys.exit(f"플러그인 디렉토리를 못 찾았다: {root_dir}")
    dispositions, keys = {}, {}
    for name in sorted(os.listdir(root_dir)):
        path = os.path.join(root_dir, name, "__init__.py")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fp:
            text = fp.read()
        hook = _method_source(text, "server_command")
        if hook is None:
            continue
        for action, bodies in _action_branches(hook).items():
            joined = "".join(bodies)
            found = re.findall(r'return "(\w+)"', joined)
            unknown = sorted(set(found) - set(_PLUGIN_DIRECTIVE))
            if unknown:
                sys.exit(f"{path} 의 {action} 이 모르는 지시 {unknown} 를 돌려준다 — "
                         f"_dispatch_plugin_cmd 의 계약이 늘었으면 여기도 고칠 것")
            if not found:
                sys.exit(f"{path} 의 {action} 갈래에서 지시를 못 찾았다 — 몸통을 "
                         f"자르는 방법이 틀렸다(빈 결과는 통과가 아니다)")
            # 한 갈래가 조건에 따라 여러 지시를 돌려주면 **약한 쪽**(handled)으로 적는다
            # — 클라가 full 을 기대하다 못 받는 것이 반대보다 나쁘다.
            mapped = {_PLUGIN_DIRECTIVE[f] for f in found}
            dispositions[action] = "handled" if "handled" in mapped else "full"
            keys[action] = _keys_in(joined)
    if not dispositions:
        sys.exit(f"{root_dir} 에서 플러그인 소유 action 을 하나도 못 찾았다 — "
                 f"훅 이름이 바뀌었으면 이 스크래퍼를 고칠 것(빈 결과는 고장이다)")
    return dispositions, keys


def _payload_keys(table, root):
    """명령마다 서버 핸들러가 **실제로 읽는 칸 이름**.

    # 왜 필요한가 (2026-07-29 에 값을 증명한 자리)

    이름 대조만으로는 **칸이 틀린 것을 못 잡는다.** 우리는 `split` 에 `horizontal` 을
    실어 보내고 있었는데 서버는 `msg.get("orient", "lr")` 를 읽는다 — 못 찾으니 늘
    기본값으로 떨어졌고, 그래서 **G1 이래 모든 분할이 좌우였다.** 명령 이름은 맞아서
    적합성 게이트도, 1400개 테스트도 전부 초록이었다(클라 p4 68374).

    표의 핸들러는 `inspect.getsource` 로 읽는다(데코레이터가 함수를 그대로 등록한다).
    `serverio.py` 가 표를 거치지 않고 직접 처리하는 갈래는 함수가 아니라 `if` 블록이라
    **소스를 잘라** 긁는다 — 그쪽도 `host`·`index` 같은 칸을 읽는다.
    """
    keys = {}
    for action, (fn, _disp) in table.items():
        try:
            keys[action] = _keys_in(inspect.getsource(fn))
        except (OSError, TypeError):
            sys.exit(f"{action} 핸들러의 소스를 못 읽었다 — 뽑는 방법이 틀렸다")

    path = os.path.join(root, "pytmuxlib", "serverio.py")
    with open(path, encoding="utf-8") as fp:
        text = fp.read()
    branches = _action_branches(text)
    if not branches:
        sys.exit(f"{path} 에서 직접 처리 갈래를 하나도 못 찾았다 — 빈 결과는 고장이다")
    for name, bodies in branches.items():
        keys.setdefault(name, set()).update(_keys_in("".join(bodies)))
    # ★ 플러그인 소유 action 의 칸도 같이 적는다(`_plugin_commands` 문서 참조).
    for name, plugin_keys in _plugin_commands(root)[1].items():
        keys.setdefault(name, set()).update(plugin_keys)
    return {k: sorted(v) for k, v in sorted(keys.items())}


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
    from pytmuxlib import servercmd

    table = servercmd._CMD_TABLE
    if not table:
        sys.exit("_CMD_TABLE 이 비었다 — import 만으로 채워지지 않는다면 뽑는 방법이 틀렸다")

    # 등록 순서가 아니라 이름순으로 적는다. 소스에서 핸들러를 옮기는 것만으로 픽스처가
    # 바뀌면 diff 가 잡음이 되고, 진짜 변경(이름·disposition)이 묻힌다.
    dispositions = {action: disp for action, (_fn, disp) in sorted(table.items())}

    # ★ 표가 전부가 아니다 — `serverio._handle_cmd` 는 `_CMD_TABLE` 을 보기 **전에**
    # 몇 개를 `if action == "…"` 로 직접 처리하고 돌아간다(페더레이션 진입/해제·릴레이).
    # 그것들은 데코레이터를 안 거치므로 표에 없고, 표만 뽑으면 클라가 정상적으로 보내는
    # 명령이 "서버에 없는 이름"으로 잡힌다. 그 자리를 소스에서 긁어 함께 적는다.
    for name, disp in _dispatched_in_io(root).items():
        # ★ 표에 이미 있는 이름은 **덮지 않는다**. `select_window` 가 그렇다 — 원격
        # index 일 때만 저 갈래로 새고, 로컬 index 는 그대로 표의 FULL 로 내려간다.
        # 덮으면 "이 명령은 full 재동기를 안 받는다"가 되어 클라 쪽 계약이 뒤집힌다.
        dispositions.setdefault(name, disp)
    # ★ 그리고 표에도 직접 갈래에도 없는 셋째 자리 — **플러그인이 소유한 action**.
    # `jump_prompt`(claude-code) 가 그렇다. 여기 없으면 클라가 정상적으로 보내는
    # 명령이 "서버에 없는 이름"으로 잡힌다(`_plugin_commands` 문서 참조).
    plugin_dispositions = _plugin_commands(root)[0]
    for name, disp in plugin_dispositions.items():
        dispositions.setdefault(name, disp)
    dispositions = dict(sorted(dispositions.items()))

    unknown = sorted(set(dispositions.values())
                     - {servercmd.FULL, servercmd.HANDLED, servercmd.DYNAMIC})
    if unknown:
        sys.exit(f"모르는 disposition 이 생겼다: {unknown} — 계약이 늘었으면 "
                 f"command_conformance.rs 도 같이 고칠 것")

    payload = {
        "_comment": "python3 scripts/gen_command_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/servercmd.py 의 _CMD_TABLE(@_cmd 데코레이터가 채운다) + "
                    "serverio.py 가 표를 거치지 않고 직접 처리하는 action 들 + "
                    "플러그인 server_command 훅이 소유한 action 들. "
                    "disposition 계약(full/handled/dynamic)은 그 모듈 docstring 참조.",
        "dispositions": dispositions,
        "payload_keys": _payload_keys(table, root),
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")

    counts = {}
    for disp in dispositions.values():
        counts[disp] = counts.get(disp, 0) + 1
    shown = " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    fields = sum(len(v) for v in payload["payload_keys"].values())
    print(f"{args.out} — 명령 {len(dispositions)}개 ({shown}) · 페이로드 칸 {fields}개")


if __name__ == "__main__":
    main()
