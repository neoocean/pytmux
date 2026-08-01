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

**세는 단위를 바꾸지 말 것.** 이 파일이 정하는 것은 "패리티를 무엇으로 세는가"이고, 그
단위가 흔들리면 진행률이 의미를 잃는다.

# 왜 import 로 뽑나

`clientutil` 은 Textual 을 안 끌어온다(순수 데이터 표라서). 화면 목록만은 소스를 정규식으로
읽는다 — `clientscreens` 는 import 하는 순간 Textual 을 요구하고, 그건 이 저장소의
의존이 아니다.

    python3 scripts/gen_client_surface_fixture.py [--pytmux ..]
"""

import argparse
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
    }

    for key in ("commands", "command_help_en", "prefix_keys", "esc_keys",
                "menu_items", "mouse_gestures", "settings", "set_options",
                "screens"):
        if not payload[key]:
            sys.exit(f"'{key}' 가 비었다 — 카탈로그 이름이 바뀌었을 것이다")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=False)
        fp.write("\n")

    sizes = " · ".join(f"{k} {len(payload[k])}" for k in
                       ("commands", "prefix_keys", "esc_keys", "menu_items",
                        "settings", "set_options", "screens"))
    print(f"{args.out} — {sizes}")


if __name__ == "__main__":
    main()
