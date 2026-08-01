#!/usr/bin/env python3
"""정본의 **설정 줄 이름·값 낱말**을 픽스처로 뽑는다 — 설정 화면이 쓰는 사람 말.

# 왜 필요한가

우리 설정 화면은 옵션 키를 **그대로** 적었다(`inactive-dim`·`mouse-drag-threshold`).
설정은 **이름을 모르는 사람**이 여는 화면인데, 그 화면이 이름을 요구했다. 정본은
`i18n.t(f"setting.{key}")` 로 사람 말을 찾고(`비활성 패널 흐리게`), 값도
`i18n.t(f"setval.{v}")` 로 옮긴다(`on` → `켜짐`).

# 왜 손으로 안 옮기나

40+8 줄이다. 손으로 옮기면 정본이 낱말을 고칠 때 조용히 갈라진다 — 이 저장소가 세 번
밟은 자리다(G9y 에서 손번역 표를 통째로 정본 추출로 갈아엎었다).

# ★ 플러그인 등록을 함께 뜬다

`setting.claude-settings`·`setting.model`·`setting.claude-rules`·
`setting.claude-token-log` 와 `setcat.Claude` 는 **코어 i18n.py 에 없다** —
claude-code 플러그인이 `i18n.register` 로 넣는다(완전분리 2026-07-07). 코어 카탈로그만
읽으면 그 넷이 통째로 빠지고, "정본에도 이름이 없다"로 잘못 적힌다.
`gen_categories.py` 가 같은 함정에서 `Claude` 카테고리를 잃을 뻔했다.

    python3 scripts/gen_setting_labels.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "setting_labels.json")

# 값 낱말은 이름으로 못 훑는다(`setval.<값>` 의 `<값>` 이 무엇인지는 카탈로그만 안다).
# 접두사로 훑으면 되지만, **어떤 값이 화면에 실제로 오는지**는 설정 표가 정한다 —
# 그래서 카탈로그 전수 + 표에서 쓰는 값을 둘 다 싣고 아래에서 교차 검사한다.
PREFIXES = ("setting.", "setval.")


def _utf8_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _pick(catalog, prefix):
    return {k[len(prefix):]: v for k, v in catalog.items() if k.startswith(prefix)}


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
    from pytmuxlib import plugins as py_plugins

    # ★ 이 한 줄이 플러그인 등록을 카탈로그에 넣는다(import 부작용). 빼면 Claude 넷이
    #   조용히 빠진다 — 아래 교차 검사가 그것을 잡는다.
    registry = py_plugins.load()

    ko = py_i18n._CATALOG["ko"]
    en = py_i18n._CATALOG["en"]

    setting_ko = _pick(ko, "setting.")
    setting_en = _pick(en, "setting.")
    setval_ko = _pick(ko, "setval.")
    setval_en = _pick(en, "setval.")

    # 설정 표가 실제로 화면에 내보내는 값들(bool 은 on/off 고정 — `_val_display`).
    plugin_settings, _extra_cats = registry.settings()
    used_values = set()
    for s in list(cu.SETTINGS) + list(plugin_settings):
        if s.get("type") == "bool":
            used_values.update(("on", "off"))
        for c in s.get("choices", ()):
            used_values.add(c)

    payload = {
        "_comment": "python3 scripts/gen_setting_labels.py 로 생성. 출처 = "
                    "pytmuxlib/i18n.py 의 setting.*/setval.* + 플러그인 i18n.register "
                    "기여. 설정 화면이 옵션 키 대신 적는 사람 말의 정본이다.",
        "setting_ko": {k: setting_ko[k] for k in sorted(setting_ko)},
        "setting_en": {k: setting_en[k] for k in sorted(setting_en)},
        "setval_ko": {k: setval_ko[k] for k in sorted(setval_ko)},
        "setval_en": {k: setval_en[k] for k in sorted(setval_en)},
        # 설정 표가 화면에 내보내는 값 전부. 여기 있는데 `setval_ko` 에 없으면 정본은
        # **원값 그대로** 보인다(`vi`·`pyte` 같은 기술적 값 — `_vlabel` 의 default).
        "used_values": sorted(used_values),
    }

    # 빈 결과는 통과가 아니라 고장이다(카탈로그 이름이 바뀌면 조용히 빈 표가 나온다).
    for key, value in payload.items():
        if key != "_comment" and not value:
            sys.exit(f"'{key}' 가 비었다 — 정본의 카탈로그 이름이 바뀌었을 것이다")
    # ★ 플러그인 기여가 실려 있나. 이것이 이 생성기의 가장 조용한 실패 모드다.
    if "claude-settings" not in setting_ko:
        sys.exit("플러그인 설정 라벨이 안 실렸다 — plugins.load() 가 안 돌았다")
    # ko/en 이 짝이 안 맞으면 en 로케일에서 한글이 샌다(정본 전수조사가 고친 부류).
    missing_en = sorted(set(setting_ko) - set(setting_en))
    if missing_en:
        sys.exit(f"en 번역이 없는 setting.*: {missing_en}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=False)
        fp.write("\n")

    sizes = " · ".join(f"{k} {len(payload[k])}" for k in
                       ("setting_ko", "setting_en", "setval_ko", "used_values"))
    print(f"{args.out} — {sizes}")


if __name__ == "__main__":
    main()
