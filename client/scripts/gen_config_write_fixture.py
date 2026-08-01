#!/usr/bin/env python3
"""설정 파일 **쓰기** 형식 픽스처 — 우리가 사용자의 config 를 고치는 첫 자리다.

# 왜 표로 묶는가

읽기는 틀려도 "설정이 안 먹네"로 끝나지만, **쓰기는 사용자의 파일을 고친다**. 규칙이
어긋나면 파이썬 클라가 쓰던 줄 옆에 중복 줄이 생기거나(둘 다 `set prefix` 인데 값이
다르다), 주석·`bind` 줄이 사라진다. 그건 되돌릴 수 없다.

그래서 정본은 `pytmuxlib/keymap.py::set_config_option` 이고, 이 스크립트는 그 함수를
**그대로 불러** 입력/출력 쌍을 뽑는다. 손으로 규칙을 옮겨 적으면 그 순간부터 갈린다.

# 줄바꿈은 **줄 목록**으로 적는다

파이썬은 텍스트 모드로 쓰기 때문에 `\\n` 이 그 OS 의 줄바꿈으로 번역된다 — 같은 함수가
Windows 에서는 CRLF, Linux 에서는 LF 를 쓴다. 그 차이를 픽스처에 굳히면 표가 뽑은 상자에
묶인다. 그래서 **터미네이터 없는 줄 배열**로 적는다(러스트 쪽이 어떤 터미네이터를 쓸지는
그쪽 문서에 적힌 결정이다 — 원본 파일의 것을 보존한다).

    python scripts/gen_config_write_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys
import tempfile

# (이름, 원래 파일 내용 또는 None(=파일 없음), 쓸 옵션, 쓸 값, 왜 이 칸이 있는가)
CASES = [
    ("no file at all", None, "prefix", "C-a",
     "파일이 없으면 만든다 — 설정 화면을 처음 쓰는 사람이 여기다"),
    ("empty file", "", "prefix", "C-a",
     "빈 파일도 같은 길"),
    ("append when absent", "# 내 설정\nset mouse on\n", "prefix", "C-a",
     "모르는 옵션은 끝에 붙인다(앞의 줄은 그대로)"),
    ("replace in place", "set prefix C-b\nset mouse on\n", "prefix", "C-a",
     "★ 있는 줄을 고친다 — 붙이기만 하면 같은 옵션이 두 줄이 되고 나중 줄이 이긴다"),
    ("keep the indent", "  set prefix C-b\n", "prefix", "C-a",
     "선행 공백 보존(사용자가 들여쓴 파일을 평평하게 만들지 않는다)"),
    ("comments are not settings", "# set prefix C-x\n", "prefix", "C-a",
     "주석 안의 같은 옵션은 건드리지 않는다 — 끝에 붙는다"),
    ("only the first one", "set prefix C-b\nset prefix C-c\n", "prefix", "C-a",
     "첫 줄만 고친다(파이썬 로더도 마지막이 이기지만 표는 함수를 따른다)"),
    ("alias form is the same option", "set tabbar always\n", "tab-bar", "auto",
     "★ 별칭으로 적힌 줄을 못 알아보면 중복이 생긴다"),
    ("underscore form too", "set mouse_drag_copy on\n", "mouse-drag-copy", "off",
     "`_` 표기도 같은 옵션"),
    ("bind and hook lines survive",
     "bind r source-file ~/.pytmux.conf\nhook after-new-window run 'x'\n",
     "prefix", "C-a",
     "set 이 아닌 줄은 그대로 둔다"),
    ("no trailing newline", "set mouse on", "prefix", "C-a",
     "끝 개행이 없으면 넣고 붙인다 — 아니면 두 설정이 한 줄로 붙는다"),
    ("a value with spaces", "set status-left old\n", "status-left", "#S #I",
     "값의 공백은 그대로(상태줄 포맷)"),
    ("blank lines stay", "set mouse on\n\n# 끝\n", "prefix", "C-a",
     "빈 줄·꼬리 주석 뒤에 붙는다"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(".."))
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "config_write.json"))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib.keymap import set_config_option  # noqa: E402

    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for name, before, opt, value, why in CASES:
            path = os.path.join(tmp, "config")
            if os.path.exists(path):
                os.remove(path)
            if before is not None:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write(before)
            set_config_option(opt, value, path)
            with open(path, encoding="utf-8", newline="") as f:
                after = f.read()
            rows.append({
                "name": name,
                "why": why,
                "before": None if before is None else before.split("\n")[:-1]
                if before.endswith("\n") else before.split("\n"),
                "before_ends_with_newline": bool(before) and before.endswith("\n"),
                "before_exists": before is not None,
                "option": opt,
                "value": value,
                # 터미네이터를 벗긴 줄 목록(위 독스트링 참조).
                "after": after.replace("\r\n", "\n").rstrip("\n").split("\n"),
            })

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # ⚠ 출력에 `newline="\n"` 를 **명시하지 않는다** — 생성기 열아홉 중 이것만
    # 명시했고, 그 하나 때문에 Windows CI 가 붉었다(2026-08-01 실측). 이유:
    # `check_fixtures.py` 는 픽스처를 **바이트로** 비교하는데, 저장소에
    # `.gitattributes` 가 없어 Windows 러너의 체크아웃은 `core.autocrlf` 로 CRLF 가
    # 된다. 나머지 열여덟은 플랫폼 기본(=Windows 에서 CRLF)으로 써서 체크아웃과
    # 맞아떨어지는데, 여기만 LF 로 써서 "픽스처가 낡았다"로 읽혔다.
    # 위 `before` 쓰기의 `newline="\n"` 는 **그대로 둔다** — 저것은 테스트 입력의
    # 바이트를 정하는 것이라 OS 마다 달라지면 안 된다.
    # (구조적 정답은 `.gitattributes` 로 픽스처를 LF 로 고정하고 열아홉을 모두
    #  명시로 돌리는 것이다 — HANDOFF §10-18.)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "source": "pytmuxlib/keymap.py::set_config_option",
            "note": "줄 터미네이터는 뺐다 — 파이썬은 OS 기본으로 번역해 쓴다",
            "cases": rows,
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"{len(rows)}개 칸 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
