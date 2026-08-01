#!/usr/bin/env python3
"""정본 오버레이(시계·달력)가 **어느 테마 변수로 칠하는가**를 픽스처로 뽑는다.

# 왜 색이 아니라 변수 이름인가

정본은 `theme_color(app, "success")` 처럼 **Textual 테마 변수**로 칠한다. 우리는 터미널
이름색(`bright_green` 등)을 쓰므로 값을 그대로 옮길 수 없고, 옮겨서도 안 된다 — 정본의
`#4EBF71` 은 그 변수의 **폴백**일 뿐이고, 터미널에서 그 자리를 정하는 것은 사용자
테마이기 때문이다.

옮길 수 있는 것은 **관습**이다:

- 시계 숫자와 달력 제목이 **같은 변수**(`success`)를 쓴다 — 두 오버레이가 한 색이다.
- 달력의 오늘은 그 색을 **배경**으로 깔고 글자는 검정이다(글자색만 바꾸지 않는다).
- 날짜는 `foreground` — 특별한 색이 아니다.

우리가 틀렸던 것이 정확히 그 구조였다(시계·제목이 청록, 오늘이 주황 **글자**). 그래서
이 픽스처는 이름을 뜨고, 적합성 테스트는 우리 스타일의 **구조**를 대조한다.

# 왜 import 가 아니라 정규식인가

이 스타일들은 Textual 앱 인스턴스(`app.theme_variables`)를 요구하는 렌더 훅 안에서
만들어진다 — 그건 이 저장소의 의존이 아니다(`gen_client_surface_fixture.py` 가 화면
클래스 목록에 정규식을 쓰는 것과 같은 이유). 대신 **한 줄도 못 찾으면 실패로 떨어뜨린다.**

    python3 scripts/gen_overlay_styles.py [--pytmux ..]
"""

import argparse
import json
import os
import re
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "overlay_styles.json")

# (픽스처 키, 파일, 그 줄을 찾는 정규식). 정규식은 `theme_color(app, "<변수>")` 의
# 변수 이름을 잡는다.
WANTED = [
    ("clock.digit", ("plugins", "clock", "__init__.py"),
     r'digit_st\s*=\s*Style\(color=theme_color\(app,\s*"(?P<var>\w+)"\)'
     r'(,\s*bold=(?P<bold>True|False))?'),
    ("calendar.day", ("plugins", "calendar", "__init__.py"),
     r'"day":\s*Style\(color=theme_color\(app,\s*"(?P<var>\w+)"\)'
     r'(,\s*bold=(?P<bold>True|False))?'),
    ("calendar.title", ("plugins", "calendar", "__init__.py"),
     r'"title":\s*Style\(color=theme_color\(app,\s*"(?P<var>\w+)"\)'
     r'(,\s*bold=(?P<bold>True|False))?'),
    ("calendar.big_today", ("plugins", "calendar", "__init__.py"),
     r'"big_today":\s*Style\(color=theme_color\(app,\s*"(?P<var>\w+)"\)'
     r'(,\s*bold=(?P<bold>True|False))?'),
    ("calendar.border", ("plugins", "calendar", "__init__.py"),
     r'"border":\s*Style\(color=theme_color\(app,\s*"(?P<var>\w+)"\)'
     r'(,\s*bold=(?P<bold>True|False))?'),
]

# `today` 만은 모양이 다르다(글자색이 리터럴 `black`, 배경이 테마 변수) — 그 **구조**가
# 이 슬라이스의 알맹이라 따로 뜬다.
TODAY = (
    ("plugins", "calendar", "__init__.py"),
    r'"today":\s*Style\(color="(?P<fg>\w+)",\s*\n?\s*'
    r'bgcolor=theme_color\(app,\s*"(?P<var>\w+)"\)'
    r'(,\s*bold=(?P<bold>True|False))?',
)


def _utf8_stdout():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


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

    sources = {}

    def read(parts):
        path = os.path.join(root, "pytmuxlib", *parts)
        if path not in sources:
            with open(path, encoding="utf-8") as fp:
                sources[path] = fp.read()
        return path, sources[path]

    elements = {}
    for key, parts, pattern in WANTED:
        path, text = read(parts)
        found = re.search(pattern, text)
        if not found:
            sys.exit(f"{path} 에서 '{key}' 스타일 줄을 못 찾았다 — 정본이 바뀌었다")
        elements[key] = {
            "fg_var": found.group("var"),
            "bold": found.group("bold") == "True",
        }

    path, text = read(TODAY[0])
    found = re.search(TODAY[1], text)
    if not found:
        sys.exit(f"{path} 에서 'today' 스타일 줄을 못 찾았다 — 정본이 바뀌었다")
    elements["calendar.today"] = {
        "fg_literal": found.group("fg"),
        "bg_var": found.group("var"),
        "bold": found.group("bold") == "True",
    }

    payload = {
        "_comment": "python3 scripts/gen_overlay_styles.py 로 생성. 출처 = pytmuxlib/"
                    "plugins/{clock,calendar}/__init__.py 의 Style(...) 줄. 값이 아니라 "
                    "**어느 테마 변수로 칠하는가**를 뜬다 — 우리는 터미널 이름색을 쓰므로 "
                    "옮기는 것은 색이 아니라 관습이다.",
        "elements": elements,
        # 그 변수들의 정본 폴백 색(참고용 — 우리 값과 직접 대조하지 않는다).
        "theme_fallback": {
            name: cu._THEME_FALLBACK[name]
            for name in sorted({e.get("fg_var") or "" for e in elements.values()}
                               | {e.get("bg_var") or "" for e in elements.values()})
            if name in cu._THEME_FALLBACK
        },
    }
    if not payload["elements"] or not payload["theme_fallback"]:
        sys.exit("뽑힌 것이 없다 — 통과가 아니라 고장이다")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=False)
        fp.write("\n")
    print(f"{args.out} — elements {len(payload['elements'])}")


if __name__ == "__main__":
    main()
