#!/usr/bin/env python3
"""정본의 **분류(카테고리·그룹) 데이터**를 픽스처로 뽑는다 — 화면을 나누는 기준의 자.

# 왜 따로 뽑나

「정본 기준 레이아웃 맞추기」의 남은 셋(설정 사이드바 · 팔레트 카테고리 탭 · 메뉴 계층화)은
낱개가 아니라 **한 덩어리**다. 셋 다 화면을 나누는 일인데, 나누는 **기준이 정본에만** 있다.
87+34+31 줄을 손으로 옮기면 정본과 조용히 어긋난다 — 그 부류의 손번역은 G9y 에서 이미 한 번
정본 추출로 갈아엎었다.

`gen_client_surface_fixture.py` 와 갈라 두는 이유: 그 픽스처는 **패리티의 세는 단위**라
머리말이 "세는 단위를 바꾸지 말 것"을 못박는다. 분류는 세는 것이 아니라 **배치**라, 늘고 줄어도
진행률이 흔들려선 안 된다.

# 무엇을 뽑나

| 키 | 출처 | 뜻 |
|---|---|---|
| `command_cats` · `command_cat_order` | `clientutil.COMMANDS` + 플러그인 `commands` | 팔레트 탭 그룹 |
| `setting_cats` · `settings_cat_order` | `clientutil.SETTINGS`/`SETTINGS_CATS` + 플러그인 `settings()` | 설정 사이드바 |
| `menu_labels` · `menu_order` · `menu_toggles` | `clientutil.MENU_ITEMS`/`MENU_TOGGLES` | 메뉴 낱줄 |
| `menu_groups` · `menu_toplevel` · `menu_group_labels` | `clientutil.MENU_*` | 메뉴 계층 |
| `cat_en` · `setcat_en` · `menu_group_en` | `i18n._CATALOG["en"]` | 같은 분류의 영어 표기 |

**플러그인 기여를 함께 싣는다.** 정본의 팔레트는 `COMMANDS + plugins.commands` 를 보고,
설정 화면은 코어 `SETTINGS` 에 `plugins.settings()` 를 병합한다 — 코어만 뽑으면 `Claude`
카테고리가 통째로 빠져, "우리에겐 그 탭이 없다"가 아니라 "정본에도 없다"로 잘못 적힌다.

# 왜 import 로 뽑나

`clientutil` 과 `plugins/` 는 Textual 을 안 끌어온다(순수 데이터 표 + 지연 import 규약).
설정 병합 순서만은 `clientscreens.SettingsScreen` 의 코드를 그대로 흉내 낸다 — 그 모듈은
import 하는 순간 Textual 을 요구하고, 그건 이 저장소의 의존이 아니다.

    python3 scripts/gen_categories.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "categories.json")


def _utf8_stdout():
    """cp949 콘솔에서 요약 print 가 죽지 않게(생성기 공통)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _first_seen(values):
    """등장 순서를 지키며 중복을 없앤다(정본이 카테고리 탭을 만드는 방식)."""
    out = []
    for v in values:
        if v not in out:
            out.append(v)
    return out


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

    registry = py_plugins.load()

    # ── 명령 카테고리 ────────────────────────────────────────────────────────
    # 정본 `clientcmd.py`: all_commands = COMMANDS + self.plugins.commands.
    # 탭 순서는 그 목록의 **카테고리 등장 순서**다(CommandListScreen.__init__).
    all_commands = list(cu.COMMANDS) + list(registry.commands)
    command_cats = {}
    for name, _desc, cat in all_commands:
        command_cats.setdefault(name, cat)      # 코어가 먼저 — 플러그인이 덮지 않는다
    command_cat_order = _first_seen(cat for _n, _d, cat in all_commands)

    # ── 설정 카테고리 ────────────────────────────────────────────────────────
    # 정본 `SettingsScreen._build`: 코어 SETTINGS 뒤에 플러그인 항목을, SETTINGS_CATS 의
    # '키' **앞**에 플러그인 카테고리를 끼운다. '키'는 항목 표가 아니라 런타임에 만드는
    # 읽기 전용 레퍼런스라 setting_cats 에는 안 실린다(탭 순서에만 있다).
    plugin_settings, extra_cats = registry.settings()
    setting_cats = {s["key"]: s["cat"] for s in cu.SETTINGS}
    for s in plugin_settings:
        setting_cats.setdefault(s["key"], s["cat"])
    core_cats = list(cu.SETTINGS_CATS)
    at = core_cats.index("키") if "키" in core_cats else len(core_cats)
    settings_cat_order = core_cats[:at] + [c for c in extra_cats
                                           if c not in core_cats] + core_cats[at:]
    # ★ 설정 줄의 **차례**. `setting_cats` 는 키로 정렬한 사전이라 차례를 안 담는다 —
    # 3차 대조(2026-08-01)에서 `표시` 안 차례가 조용히 갈라져 있던 것이 그래서다.
    # 화면은 이 차례대로 그리므로 눈이 외운 자리가 여기 달려 있다.
    settings_order = [s["key"] for s in cu.SETTINGS] + [s["key"] for s in plugin_settings]

    # ── 메뉴 ─────────────────────────────────────────────────────────────────
    menu_labels = {key: label for key, label in cu.MENU_ITEMS}
    menu_order = [key for key, _label in cu.MENU_ITEMS]

    # ★ 플러그인 메뉴 기여. 정본은 `MENU_GROUPS` 에 `plugin` 을 **안 두고**
    # `MenuScreen._toplevel_entries` 가 런타임에 `group:tab` 뒤로 끼운다(플러그인
    # 디렉토리를 지우면 그 그룹이 통째로 사라지는 delete-to-disable 규약). 그래서
    # 그룹 멤버가 정적 표에 없고, 여기서 따로 떠야 한다.
    plugin_menu = list(registry.menu_items)
    plugin_menu_order = [key for key, _label in plugin_menu]
    plugin_menu_labels = {key: label for key, label in plugin_menu}
    plugin_menu_en = {key: py_i18n._CATALOG["en"].get(f"menu.{key}")
                      for key in plugin_menu_order
                      if f"menu.{key}" in py_i18n._CATALOG["en"]}

    payload = {
        "_comment": "python3 scripts/gen_categories.py 로 생성. 출처 = "
                    "pytmuxlib/clientutil.py 의 분류 표 + plugins 레지스트리 기여. "
                    "화면을 나누는 기준(카테고리·그룹)의 정본이다 — 세는 단위는 "
                    "client_surface.json 쪽이고 여기는 배치다.",
        "command_cats": {k: command_cats[k] for k in sorted(command_cats)},
        "command_cat_order": command_cat_order,
        "setting_cats": {k: setting_cats[k] for k in sorted(setting_cats)},
        "settings_cat_order": settings_cat_order,
        "menu_labels": {k: menu_labels[k] for k in sorted(menu_labels)},
        "menu_order": menu_order,
        "menu_toggles": sorted(cu.MENU_TOGGLES),
        "menu_groups": {g: list(keys) for g, keys in cu.MENU_GROUPS.items()},
        "menu_group_order": list(cu.MENU_GROUPS),
        "menu_toplevel": list(cu.MENU_TOPLEVEL),
        "menu_group_labels": dict(cu.MENU_GROUP_LABELS),
        "plugin_menu_order": plugin_menu_order,
        "plugin_menu_labels": plugin_menu_labels,
        "plugin_menu_en": plugin_menu_en,
        # ★ 플러그인이 기여한 **명령·설정 줄 자체**(설계 Tier A · P2).
        #
        # 위 `command_cats`/`setting_cats` 는 코어와 플러그인을 한 사전에 섞어 담아
        # **어느 줄이 플러그인 것인지** 구분이 없다. 그런데 그 구분이 곧 게이트다:
        # 서버가 런타임에 부는 목록이 바로 이것이고, 우리 화면이 그것을 빠짐없이(그리고
        # 두 번 세지 않고) 세우는가를 재려면 목록 원본이 있어야 한다.
        "plugin_commands": [{"name": n, "desc": d, "cat": c}
                            for n, d, c in registry.commands],
        "plugin_noarg": sorted(registry.noarg),
        "plugin_settings": [{"key": s["key"], "cat": s["cat"],
                             "type": s.get("type", ""),
                             "values": list(s.get("values", []))}
                            for s in plugin_settings],
        "plugin_setting_cats": list(extra_cats),
        # 정본이 `group:plugin` 을 끼우는 자리(`_toplevel_entries`). 코드에 박힌 규칙이라
        # 표에서 못 뜬다 — 여기 적어 두면 정본이 자리를 옮길 때 diff 에 보인다.
        "plugin_menu_after": "group:tab",
        # 같은 분류의 영어 표기. 우리 쪽은 ko 원문이 msgid 라 (ko→en) 표로 접어 쓴다.
        "cat_en": {cat: py_i18n._CATALOG["en"][f"cat.{cat}"]
                   for cat in ["전체"] + command_cat_order
                   if f"cat.{cat}" in py_i18n._CATALOG["en"]},
        "settings_order": settings_order,
        # ★ ko 라벨도 뜬다. 정본은 `setcat.입력` → `입력/키` 처럼 **분류 이름과 다른**
        # 화면 이름을 쓰는 자리가 있는데, en 만 뜨면 그 차이가 한국어에서만 조용히
        # 어긋난다(3차 대조에서 실제로 그렇게 걸렸다 — 게이트 셋을 다 통과했다).
        "setcat_ko": {cat: py_i18n._CATALOG["ko"].get(f"setcat.{cat}", cat)
                      for cat in settings_cat_order},
        "setcat_en": {cat: py_i18n._CATALOG["en"][f"setcat.{cat}"]
                      for cat in settings_cat_order
                      if f"setcat.{cat}" in py_i18n._CATALOG["en"]},
        "menu_group_en": {g: py_i18n._CATALOG["en"][f"menu.group.{g}"]
                          for g in cu.MENU_GROUP_LABELS
                          if f"menu.group.{g}" in py_i18n._CATALOG["en"]},
    }

    # 빈 결과는 통과가 아니라 고장이다 — `check_licenses.sh` 가 한 번 밟은 "안 우는
    # 게이트"와 같은 함정(카탈로그 이름이 바뀌면 조용히 빈 표가 나온다).
    for key, value in payload.items():
        if key != "_comment" and not value:
            sys.exit(f"'{key}' 가 비었다 — 정본의 표 이름이 바뀌었을 것이다")
    # 메뉴 그룹의 키는 MENU_ITEMS 의 부분집합이라야 한다(정본 주석의 계약).
    for group, keys in payload["menu_groups"].items():
        unknown = [k for k in keys if k not in menu_labels]
        if unknown:
            sys.exit(f"menu_groups[{group}] 에 MENU_ITEMS 밖의 키: {unknown}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=False)
        fp.write("\n")

    sizes = " · ".join(f"{k} {len(payload[k])}" for k in
                       ("command_cats", "command_cat_order", "setting_cats",
                        "settings_cat_order", "menu_labels", "menu_groups",
                        "menu_toplevel", "plugin_menu_order", "settings_order"))
    print(f"{args.out} — {sizes}")


if __name__ == "__main__":
    main()
