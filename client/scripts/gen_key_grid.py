#!/usr/bin/env python3
"""수식 키 **격자** — 같은 조합에 두 클라가 패널로 같은 바이트를 보내는가(pytmux-608).

# 왜 필요한가

`gen_key_bytes.py` 는 정본 `clientutil.SPECIAL` 표의 이름 38 개만 뜬다. 그 표 **밖**의 갈래
(수식 붙은 특수키 · Alt+글자 · Ctrl+구두점)는 아무도 안 맞댔고, 그 자리에서 정본은 조합을
버리고(`b""`) GUI 는 수식 없는 바이트를 보내고 있었다. 이 생성기는 수식 × 키 **격자**를 만들어
칸마다 정본이 실제로 보내는 바이트를 뽑는다.

# 정본 바이트를 어떻게 뽑나 (서버·앱 없이)

칸마다 «호스트 단말이 보낼 바이트»를 만들어 정본이 쓰는 **Textual `XTermParser`** 에 먹이고, 나온
키 이벤트를 정본 `key_to_bytes` 에 넣는다 — 정본 클라가 그 단말 안에서 받는 그 길 그대로다.
호스트 바이트는 두 벌이다:

- `legacy` — xterm 기본(PC 식 수식 · Alt = ESC 접두 · Ctrl+글자 = 제어 문자). 거의 모든 단말.
- `csiu` — kitty 키보드 규약(`CSI 코드;N u`). 레거시가 **못 가르는** 조합(Ctrl+Enter · Alt+구두점 ·
  Ctrl+Tab …)에만 있다. 그런 단말에서 정본이 받는 것이 그 조합의 «받을 수 있었다면 보냈을 바이트»다.

칸의 기준 바이트(`bytes`)는 `csiu` 가 있으면 그 결과, 없으면 `legacy` 결과다. `legacy_lost` 는 레거시
단말에서 정본이 다른 것을 보내는 칸(«단말이 못 준다» = 허용 갈림 ⓐ)이다.

`chrome` 은 정본 `clientio.on_key` 가 **패널에 보내기 전에** 먹는 키다(esc·탭 번호·F10/F12·prefix·
붙여넣기) — 그 칸은 바이트를 안 잰다.

    python3 scripts/gen_key_grid.py [--pytmux ..] [--out …]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PYTMUX = HERE.parent.parent
DEFAULT_OUT = HERE.parent / "crates" / "proto" / "tests" / "fixtures" / "key_grid.json"

MODS = [(), ("shift",), ("ctrl",), ("alt",), ("ctrl", "shift"), ("alt", "shift"), ("alt", "ctrl")]
BITS = {"shift": 1, "alt": 2, "ctrl": 4}
CSI_FINAL = {"up": "A", "down": "B", "right": "C", "left": "D", "home": "H", "end": "F"}
SS3_FN = {"f1": "P", "f2": "Q", "f3": "R", "f4": "S"}
TILDE = {"insert": 2, "delete": 3, "pageup": 5, "pagedown": 6, "f5": 15, "f6": 17, "f7": 18,
         "f8": 19, "f9": 20, "f10": 21, "f11": 23, "f12": 24}
NAMED = ["enter", "tab", "backspace", "space"]
CHARS = ["a", "z", "한", "/", "-", "2", "3", "6", "[", "]", "\\", ".", "0", "=", "b", "v"]
# xterm 의 Ctrl+구두점(레거시 바이트).
XT_CTRL = {"/": 0x1f, "-": 0x1f, "2": 0x00, "3": 0x1b, "6": 0x1e, "[": 0x1b, "]": 0x1d,
           "\\": 0x1c}
CODEPOINT = {"enter": 13, "tab": 9, "backspace": 127, "space": 32}


def n_of(mods) -> int:
    return 1 + sum(BITS[m] for m in mods)


def legacy(mods, key):
    """xterm 기본 설정의 호스트 바이트(못 내는 조합이면 None)."""
    n = n_of(mods)
    alt, ctrl, shift = "alt" in mods, "ctrl" in mods, "shift" in mods
    if key in CSI_FINAL:
        return f"\x1b[{CSI_FINAL[key]}" if n == 1 else f"\x1b[1;{n}{CSI_FINAL[key]}"
    if key in SS3_FN:
        return f"\x1bO{SS3_FN[key]}" if n == 1 else f"\x1b[1;{n}{SS3_FN[key]}"
    if key in TILDE:
        return f"\x1b[{TILDE[key]}~" if n == 1 else f"\x1b[{TILDE[key]};{n}~"
    esc = "\x1b" if alt else ""
    if key == "enter":
        return esc + "\r"
    if key == "tab":
        return esc + ("\x1b[Z" if shift else "\t")
    if key == "backspace":
        return esc + ("\x08" if ctrl else "\x7f")
    if key == "space":
        return esc + ("\x00" if ctrl else " ")
    if key == "한":
        return None if ctrl or shift else esc + "한"
    c = key.upper() if shift and key.isalpha() else key
    if shift and not key.isalpha():
        return None             # Shift+구두점은 배열이 정한다 — 격자에 안 넣는다
    if ctrl:
        if key.isalpha():
            return esc + chr(ord(key) - 96)
        if key in XT_CTRL:
            return esc + chr(XT_CTRL[key])
        return esc + key        # xterm 은 뜻 없는 Ctrl+구두점에 글자를 낸다
    return esc + c


def csiu(mods, key):
    """kitty 키보드 규약 바이트 — 레거시가 못 가르는 칸에만."""
    if not mods:
        return None
    if key in CODEPOINT:
        return f"\x1b[{CODEPOINT[key]};{n_of(mods)}u"
    if key in CSI_FINAL or key in SS3_FN or key in TILDE:
        return None
    if not ("ctrl" in mods or "alt" in mods):
        return None             # Shift 만이면 kitty 도 글자를 낸다
    if "shift" in mods and not key.isalpha():
        return None
    return f"\x1b[{ord(key)};{n_of(mods)}u"


def chrome(mods, key) -> bool:
    """정본 `clientio.on_key` 가 패널로 보내기 **전에** 먹는 칸(평소 모드 · 기본 prefix ctrl+b)."""
    m = set(mods)
    if m == {"alt"} and key.isdigit():
        return True             # Alt+숫자 = 그 번호 탭(맥 ⌥숫자열 글자도 같은 길)
    if not m and key in ("f10", "f12"):
        return True             # 메뉴 · 명령 프롬프트
    if m == {"ctrl"} and key in ("b", "v"):
        return True             # prefix · 붙여넣기
    if m == {"ctrl"} and key in ("3", "["):
        return True             # 레거시 단말이 ESC 를 보낸다 → esc 모드(단말이 못 가르는 키)
    return False


def canon_bytes(parser_cls, events, key_to_bytes, seq):
    if seq is None:
        return None
    p = parser_cls(debug=False)
    toks = list(p.feed(seq)) + list(p.feed(""))
    keys = [t for t in toks if isinstance(t, events.Key)]
    out = b"".join(key_to_bytes(k) for k in keys)
    return list(out), [k.key for k in keys]


def collect(root: pathlib.Path):
    sys.path.insert(0, str(root))
    from textual import events
    from textual._xterm_parser import XTermParser
    from pytmuxlib.clientutil import install_alt_word_keys, key_to_bytes

    # 정본 클라가 기동할 때 하는 일 그대로(Textual 의 ESC b/f 접기를 푼다 · pytmux-608).
    install_alt_word_keys()
    rows = []
    keys = list(CSI_FINAL) + list(SS3_FN) + list(TILDE) + NAMED + CHARS
    for key in keys:
        for mods in MODS:
            leg = legacy(mods, key)
            cu = csiu(mods, key)
            if leg is None and cu is None:
                continue
            legacy_out = canon_bytes(XTermParser, events, key_to_bytes, leg)
            csiu_out = canon_bytes(XTermParser, events, key_to_bytes, cu)
            target = csiu_out or legacy_out
            name = "+".join(list(mods) + [key])
            rows.append({
                "chord": name,
                "key": key,
                "shift": "shift" in mods,
                "ctrl": "ctrl" in mods,
                "alt": "alt" in mods,
                "chrome": chrome(mods, key),
                "legacy": None if leg is None else [ord(c) for c in leg],
                "csiu": None if cu is None else [ord(c) for c in cu],
                "canon_key": target[1],
                "bytes": target[0],
                "legacy_lost": legacy_out is not None and legacy_out[0] != target[0],
            })
    return rows


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", type=pathlib.Path, default=DEFAULT_PYTMUX)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    rows = collect(args.pytmux.resolve())
    if not any(r["bytes"] for r in rows):
        sys.exit("어느 칸도 아무것도 안 보냈다 — 뽑는 방법이 틀렸다")
    payload = {
        "_comment": "python3 scripts/gen_key_grid.py 로 생성. 출처 = Textual XTermParser → "
                    "pytmuxlib/clientutil.key_to_bytes(정본 클라가 단말 안에서 받는 길).",
        "rows": rows,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        fp.write('{"_comment": ' + json.dumps(payload["_comment"], ensure_ascii=False)
                 + ',\n "rows": [\n')
        fp.write(",\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True,
                                        separators=(",", ":")) for r in rows))
        fp.write("\n]}\n")
    lost = sum(r["legacy_lost"] for r in rows)
    print(f"{args.out} — 칸 {len(rows)}개 · 레거시 단말이 못 주는 칸 {lost}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
