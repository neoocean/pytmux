#!/usr/bin/env python3
"""줄의 **의미 태그 → 색** 표를 정본에서 뽑는다(pytmux-11·12 A).

# 왜 이 표가 필요한가

제보: *"`:mdir` 는 정본 동작과 완전히 같게. **컬러 스킴 일치가 특히 중요하다.**"*

정본은 줄마다 색을 달리 칠한다 — 디렉터리는 붉고, 숨은 파일은 보라, 드라이브는 주황,
태그된 것은 노랑, 압축은 자홍, 실행 파일은 초록. 네이티브 클라의 그 화면은 **전부 같은
색**이었다: 판정이 Textual 화면 안에 있어 서버가 못 불렀고, 스펙의 줄에는 실을 칸도
없었다.

이제 **판정**은 `plugins/mdir/rowtag.py`(UI 무의존) 한 벌이고 서버가 이름을 스펙에
싣는다. 남은 것은 그 이름을 **같은 색**으로 푸는 일이고, 그 값의 주인은 정본이다.

# 왜 클라 테마로 안 푸나

이 화면의 색은 테마가 아니라 **제품의 정체성**이다(Norton Commander 계열의 그림 —
정본이 hex 를 하드코딩한 이유가 그것이다). 상태줄 표식(`theme::resolve`)은 반대로 의미
색이라 클라 테마가 푼다. 두 규칙이 갈리는 지점이고, 갈리는 이유가 이것이다.

⚠ **서버는 여전히 이름만 싣는다** — hex 는 소켓을 안 건넌다(설계 §10 위험표 그대로).
이 표는 클라 안에서 이름을 값으로 바꾸는 사전일 뿐이다.

# 왜 정규식으로 읽나

`mdir/screen.py`·`ncd/screen.py` 는 Textual 을 요구해 import 할 수 없다
(`gen_screen_anchor_fixture.py` 와 같은 사정). 표는 리터럴이라 정규식으로 정확히 잡히고,
**잡힌 것이 0이면 실패로 떨어뜨린다** — 빈 결과를 통과로 두면 안 우는 게이트가 된다
(`check_licenses.sh` 가 한 번 밟은 자리).

    python3 scripts/gen_row_tags.py [--pytmux ..]
"""

import argparse
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_PYTMUX = HERE.parent
DEFAULT_OUT = HERE / "crates" / "proto" / "tests" / "fixtures" / "row_tags.json"

#: `_TAG_STYLES` 한 줄의 모양 — `"이름": _CONST` 또는 `"이름": Style(color="#hex", …)`.
_ENTRY = re.compile(r'"(?P<tag>[a-z]+)":\s*(?P<val>_[A-Z_]+|Style\([^)]*\))')
#: 스타일 상수 한 줄 — `_NAME = Style(color="#hex", …)`.
_CONST = re.compile(r'^(?P<name>_[A-Z_]+)\s*=\s*Style\(color="(?P<hex>#[0-9a-fA-F]{6})"',
                    re.M)
#: `Style(color=_ARC_COLOR, …)` 처럼 색이 **다른 상수**인 경우.
_COLOR_REF = re.compile(r'color=(?P<ref>_[A-Z_]+\[[a-z]+\]|_[A-Z_]+|"#[0-9a-fA-F]{6}")')
#: `_EXT_COLORS = {...}` 안의 `"ext": "#hex"`.
_HEXCONST = re.compile(r'^(?P<name>_[A-Z_]+)\s*=\s*"(?P<hex>#[0-9a-fA-F]{6})"', re.M)
_EXTMAP = re.compile(r'"(?P<ext>[a-z]+)":\s*"(?P<hex>#[0-9a-fA-F]{6})"')


def _consts(src: str) -> dict:
    """이 파일이 정의한 `_NAME → #hex`(Style 상수 + 색 상수 + 확장자 표)."""
    out = {}
    for m in _CONST.finditer(src):
        out[m.group("name")] = m.group("hex")
    for m in _HEXCONST.finditer(src):
        out[m.group("name")] = m.group("hex")
    ext = re.search(r"_EXT_COLORS = \{(.*?)\}", src, re.S)
    if ext:
        for m in _EXTMAP.finditer(ext.group(1)):
            out[f"_EXT_COLORS[{m.group('ext')}]"] = m.group("hex")
    return out


def _resolve(val: str, consts: dict) -> str | None:
    """표의 값 하나를 hex 로. 못 풀면 None(부르는 쪽이 실패로 센다)."""
    if val.startswith("Style("):
        m = _COLOR_REF.search(val)
        if not m:
            return None
        ref = m.group("ref")
        if ref.startswith('"'):
            return ref.strip('"')
        return consts.get(ref)
    return consts.get(val)


def collect(pytmux_root: pathlib.Path) -> dict:
    mdir = (pytmux_root / "pytmuxlib" / "plugins" / "mdir" / "screen.py").read_text(
        encoding="utf-8")
    ncd = (pytmux_root / "pytmuxlib" / "plugins" / "ncd" / "screen.py").read_text(
        encoding="utf-8")
    consts = _consts(mdir)
    # `Style(color=_EXT_COLORS["exe"], …)` 를 위 사전 키로 정규화한다.
    table = re.search(r"_TAG_STYLES = \{(.*?)\n\}", mdir, re.S)
    if not table:
        raise SystemExit("mdir/screen.py 에서 _TAG_STYLES 를 못 찾았다 — 표가 옮겨졌나?")
    body = re.sub(r'_EXT_COLORS\["([a-z]+)"\]', r"_EXT_COLORS[\1]", table.group(1))

    tags: dict[str, str] = {}
    for m in _ENTRY.finditer(body):
        hexv = _resolve(m.group("val"), consts)
        if hexv is None:
            raise SystemExit(f"{m.group('tag')}: 색을 못 풀었다 ({m.group('val')})")
        tags[m.group("tag")] = hexv

    # ncd 의 현재 디렉터리 강조 — 같은 어휘의 한 칸이다(그 화면에만 있다).
    cwd = re.search(r'^_CWD = Style\(color="(#[0-9a-fA-F]{6})"', ncd, re.M)
    if not cwd:
        raise SystemExit("ncd/screen.py 에서 _CWD 를 못 찾았다")
    tags["cwd"] = cwd.group(1)

    if len(tags) < 10:
        raise SystemExit(f"뽑힌 태그가 너무 적다({len(tags)}) — 정규식이 헛돌았다")
    return {"tags": dict(sorted(tags.items()))}


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
    print(f"{os.fspath(args.out)}: 태그 {len(data['tags'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
