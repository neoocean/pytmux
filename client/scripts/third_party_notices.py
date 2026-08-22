#!/usr/bin/env python3
"""배포 이진의 **서드파티 저작권 고지** — 짓고(`--write`) · 재고(`--check`) · 덮이나 본다(`--covers`).

# 왜 있나 (검수 2026-08-09 B-7 · pytmux/pytmux-193)

`client/build/` 의 `pytmux-gui` 셋은 **공개 미러로 나가는 배포물**인데, 그 안에 링크된
서드파티 크레이트의 저작권 고지가 **어디에도 없었다.** MIT·BSD·ISC·Zlib·Apache-2.0 은
소스 재배포뿐 아니라 **이진 재배포에도** 저작권 고지(와 라이선스 전문) 재현을 요구한다 —
즉 이진만 받아 간 사람의 손에 그 글이 닿아야 한다. 트리에 있던 것은 `client/LICENSE`·
`LICENSE-MIT`(우리 것)와 폰트 하나의 OFL 뿐이었다.

그리고 라이선스 게이트(`check_licenses.sh`)가 보던 것은 **로컬 크레이트뿐**이었다. 그
머리말은 *"외부 crates.io 의존은 이 검사 대상이 아니다(전부 허용적 라이선스로 확인된
것들)"* 라고 적었는데 그 「확인」을 **다시 하는 자리가 없었다** — 사람이 한 번 세어 문단에
적어 둔 값이고, 새 의존이 GPL 단독으로 들어와도 아무도 안 운다. 이 파일이 그 자리다.

# 무엇을 재나 (세 모드)

    --write    고지 파일을 다시 짓는다(사람이 부른다)
    --check    ① 허용 목록에 없는 라이선스가 들어왔나
               ② 고지 파일이 지금 의존 그래프보다 낡았나
               ③ 이진에 박히는 자산 중 신고 안 된 것이 있나
    --covers   방금 구운 이진의 크레이트가 **전부** 고지 안에 있나(굽는 자리가 부른다)

⛔ **`--check` 와 `--covers` 는 같은 질문을 두 번 묻는 것이 아니다.** 앞의 것은
「고지가 지금 소스와 정확히 같은가」(트리플 다섯 · 전문까지)이고, 뒤의 것은 「**이
상자에서 방금 링크된 것**이 전부 고지 안에 있는가」(호스트 트리플 · 이름·버전만)다.
굽는 자리에 앞의 것을 걸면 러너 셋이 글자 한 벌의 동일성에 걸려 릴리스를 통째로 못
내고, 게이트에 뒤의 것만 걸면 다른 OS 에서만 들어오는 의존이 영영 안 잡힌다.

# 왜 트리플 다섯의 **합집합**인가

이진은 세 OS 에서 각각 굽는데 고지 파일은 **한 벌**이다(이진 옆에 놓이는 것이 한 벌이라야
받는 사람이 어느 것을 읽을지 고민하지 않는다). 그런데 의존 그래프는 트리플마다 다르다 —
실측(2026-08-17)으로 macOS 278 · Linux 395 · Windows 351 이다. 호스트 것만 담으면
Windows 이진 옆에 macOS 목록이 놓이고, 굽는 상자마다 다른 파일이 나오면 CI 러너 셋이
서로의 커밋을 덮는다. 그래서 **어느 상자에서 지어도 같은 바이트**가 나오도록 배포 대상
트리플 전부를 합쳐서 담는다.

⚠ 배포 플랫폼을 늘리면 `TARGETS` 에 그 트리플을 더한다. 안 더하면 그 상자에서
`--covers` 가 운다(그 자리가 이 목록의 파수꾼이다 — 목록을 두 곳에 적지 않는 이유다).

# 왜 전문을 dedup 하나

크레이트가 실은 라이선스 파일을 전부 그대로 이으면 3.79MB 인데, 그중 **같은 글이
반복**이다(Apache-2.0 전문은 어느 크레이트에서 가져와도 같다). 같은 바이트는 한 번만
싣고 「이 글을 적용받는 크레이트」를 옆에 적는다. 고지 의무는 「전문을 준다」이지
「크레이트 수만큼 준다」가 아니다. 여기에 §`for_picked`(안 고른 쪽 빼기)를 더해
실측 2026-08-17 에 **428KB** 였다(중복 제거만 하면 864KB).

# 줄끝

이 저장소는 p4(`LineEnd: local`)와 git 이 줄끝을 **각자 번역**한다 — 바이트 동일은 아무도
지켜 주지 않는 성질이라, 짓는 쪽은 플랫폼 기본으로 쓰고 재는 쪽은 줄끝을 지우고 비교한다
(`check_fixtures.py` 머리말이 값을 치르고 얻은 규칙 · §10-18). 크레이트가 CRLF 로 실은
라이선스 파일도 여기서 LF 로 펴서 담는다 — 안 그러면 **글의 출처에 따라** 고지 파일의
바이트가 달라진다.
"""

import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # client/
NOTICES = os.path.join(HERE, "THIRD-PARTY-NOTICES.md")
SHIPPED = os.path.join(HERE, "build", "THIRD-PARTY-NOTICES.md")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


# ── 배포 대상 트리플 ─────────────────────────────────────────────────────────
#
# `build/README.md` 표의 세 이진 + `build_release.sh` 의 `case` 문이 이름을 주는 자리
# 전부. Linux arm64·macOS x64 는 아직 굽는 러너가 없지만 스크립트가 이름을 알고 있으므로
# 함께 담는다 — 나중에 그 상자에서 구웠을 때 고지가 **이미** 그것을 덮고 있어야 한다.
TARGETS = (
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-unknown-linux-gnu",
    "aarch64-unknown-linux-gnu",
    "x86_64-pc-windows-msvc",
)

# ── 허용 라이선스 ────────────────────────────────────────────────────────────
#
# **여기 없는 것이 들어오면 적색이다.** 「전부 허용적인 것들」이라고 적어 두는 대신 목록을
# 두고 기계가 재게 한다 — 그것이 이 파일이 생긴 이유다.
#
# ⛔ 여기에 무엇을 더하는 것은 **되돌리기 어려운 결정**이다(그 의존이 코드에 자리를 잡은
#    뒤에는 빼는 값이 커진다). 더하기 전에 그 라이선스의 의무가 「고지 재현」에서 끝나는지
#    확인하고, 아니면 사람에게 묻는다.
ALLOWED = {
    "0BSD":                          "공개 도메인급 · 고지 의무 없음(그래도 싣는다)",
    "Apache-2.0":                    "고지 + NOTICE 재현 + 변경 고지",
    "Apache-2.0 WITH LLVM-exception": "Apache-2.0 에 링크 예외",
    "BSD-2-Clause":                  "고지 재현",
    "BSD-3-Clause":                  "고지 재현 + 이름 사용 금지",
    "BSL-1.0":                       "이진 배포 시 고지 의무 없음(그래도 싣는다)",
    "CC0-1.0":                       "권리 포기",
    "ISC":                           "고지 재현",
    "MIT":                           "고지 재현",
    "MIT-0":                         "고지 의무 없음(그래도 싣는다)",
    "MPL-2.0":                       "파일 단위 카피레프트 — 고치지 않고 링크만 하면 고지 + 소스 입수처",
    "Unicode-3.0":                   "고지 재현",
    "Unicode-DFS-2016":              "고지 재현",
    "Unlicense":                     "공개 도메인 선언",
    "Zlib":                          "고지 재현(변경 표시)",
}

# 듀얼 라이선스에서 **무엇을 고를지**. 앞에 있는 것부터 고른다.
#
# 의무가 가벼운 순서가 아니라 **이 배포에서 실제로 지키는 것** 순서다: 이 트리는 MIT 이고
# (`client/LICENSE-MIT`), 고지 파일 한 장으로 끝나는 쪽이 지키기 쉽다. Apache-2.0 은
# NOTICE·변경 고지가 따라붙어 뒤로 둔다.
PREFERENCE = (
    "MIT", "MIT-0", "0BSD", "ISC", "BSD-2-Clause", "BSD-3-Clause", "Zlib",
    "Unlicense", "CC0-1.0", "BSL-1.0", "Apache-2.0 WITH LLVM-exception",
    "Apache-2.0", "Unicode-3.0", "Unicode-DFS-2016", "MPL-2.0",
)

# 라이선스 전문으로 볼 파일 이름. 크레이트마다 이름 규칙이 제각각이라 **접두**로 잡는다.
LICENSE_NAME = re.compile(r"^(LICENSE|LICENCE|COPYING|COPYRIGHT|NOTICE|UNLICENSE)", re.I)
# 라이선스 이름을 달고 있지만 전문이 아닌 것들. `*.rs`·`*.toml` 은 코드다.
LICENSE_SKIP_EXT = (".rs", ".toml", ".py", ".sh", ".json", ".lock")

# 파일 이름이 **어느 라이선스의 전문인지** 말하는 경우. 듀얼 크레이트가 여러 벌을 싣기
# 때문에 필요하다 — 우리가 BSD 로 받은 크레이트의 GPL 전문까지 실으면, 그 35KB 는
# 「우리가 GPL 로 배포한다」는 **거짓 신호**가 된다(실측: `bounded-vec-deque` 가 GPL
# 전문을 싣는다). 이름이 말하지 않는 파일(그냥 `LICENSE`)은 **안 거른다** — 짐작으로
# 지우면 진짜 고지가 사라진다.
FILE_HINT = (
    ("AGPL", "AGPL"), ("LGPL", "LGPL"), ("GPL", "GPL"),
    ("APACHE", "Apache-2.0"), ("UNLICENSE", "Unlicense"), ("MIT", "MIT"),
    ("BSD", "BSD"), ("ZLIB", "Zlib"), ("MPL", "MPL-2.0"), ("ISC", "ISC"),
    ("BOOST", "BSL-1.0"), ("BSL", "BSL-1.0"), ("EPL", "EPL"),
)

# ── 이진에 박히는 서드파티 자산 ──────────────────────────────────────────────
#
# 크레이트가 아니어도 `include_bytes!` 로 들어간 것은 **이진 안에 있다** — 고지는 그것도
# 덮어야 한다. 아래는 신고 목록이고, `--check` 가 소스의 `include_bytes!` 를 훑어
# **신고 안 된 것**을 찾는다(자산이 늘었는데 고지가 그대로인 자리를 사람이 기억하는 대신).
BUNDLED = [
    {
        "what": "Roboto (Regular)",
        "why": "이미지 캐시의 SVG 렌더가 쓰는 기본 산세리프 — `warpui_core/src/image_cache.rs`",
        "spdx": "OFL-1.1",
        "license_file": os.path.join("crates", "warpui_core", "assets", "fonts", "LICENSE.txt"),
        "embeds": [os.path.join("crates", "warpui_core", "assets", "fonts", "Roboto-Regular.ttf")],
    },
]

# 우리가 만든 것이라 고지 대상이 아닌 `include_bytes!` 대상. **패턴이 아니라 이유를 적는다.**
OURS_EMBEDDED = {
    "shaders.metallib": "우리 셰이더를 build.rs 가 굽는다(OUT_DIR) — 상류에서 온 바이트가 아니다",
}


def fail(msg, *hints):
    print(f"FAIL: {msg}", file=sys.stderr)
    for h in hints:
        print(f"  → {h}", file=sys.stderr)


def cargo():
    """cargo 실행 경로. 못 찾으면 `None`.

    `check_licenses.sh`·`check_all.py §find_cargo` 와 같은 규칙이다 — rustup 은 이진을
    `~/.cargo/bin` 에 두고 PATH 는 셸 프로필에서 세우는데 **비대화형 셸은 그 프로필을 안
    읽는다**(에이전트 툴 환경·launchd·CI). 깔려 있는 것을 "없다"고 말하면 이 게이트가
    조용히 건너뛰고, 그 침묵이 곧 "이 상자에서는 못 잰다"로 굳는다(pytmux-33 이 그 값을
    나흘 치렀다).
    """
    from shutil import which
    found = which("cargo")
    if found:
        return found
    exe = "cargo.exe" if os.name == "nt" else "cargo"
    home = os.path.join(os.path.expanduser("~"), ".cargo", "bin", exe)
    return home if os.path.isfile(home) else None


def run(argv):
    """cargo 를 부른다 → stdout. 실패하면 stderr 를 그대로 물고 죽는다(조용한 0 금지)."""
    proc = subprocess.run(argv, cwd=HERE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv[1:3])} … rc={proc.returncode}\n"
                           f"{(proc.stderr or '').strip()[-800:]}")
    return proc.stdout


_TREE_LINE = re.compile(r"^(?P<name>[^ ]+) v(?P<ver>[^ |]+)(?P<extra>(?: \([^)]*\))*)\|(?P<lic>.*)$")


def _origin(extra):
    """`cargo tree` 가 이름·버전 뒤에 붙이는 괄호들 → `("registry"|"git"|"path", 값)`.

    ⛔ **이름+버전은 열쇠가 못 된다.** 이 워크스페이스는 `core-foundation-rs` 를 git rev
    로 고정해 쓰는데, 그 저장소의 `core-graphics-types v0.2.0` 과 **crates.io 의 같은
    이름·버전**이 그래프에 **동시에** 있다(실측 2026-08-17). 둘을 한 열쇠로 접으면
    한쪽의 라이선스 전문으로 다른 쪽을 고지하게 된다 — 조용히 틀리는 쪽이다.
    """
    for paren in re.findall(r"\(([^)]*)\)", extra or ""):
        if paren == "proc-macro":
            continue                                   # 출처가 아니라 종류다
        if paren.startswith(("http://", "https://", "git+", "ssh://")):
            return ("git", paren)
        return ("path", paren)                         # 로컬(우리) 크레이트
    return ("registry", "crates.io")


def dep_lines(cargo_bin, target):
    """한 트리플의 의존 그래프 → `{열쇠: (SPDX 선언, 출처)}`.

    `-e no-dev` — dev-dependencies 는 이진에 안 들어간다. `-p gui` — 배포하는 이진이
    그것 하나다(`build/README.md`). 워크스페이스 전체로 넓히면 이진에 없는 것까지 고지에
    실려, 「이 이진에 무엇이 들어 있나」라는 질문에 거짓으로 답한다.
    """
    # ⛔ `--locked` — 이 명령이 **`Cargo.lock` 을 고치게 두지 않는다.** cargo 는 lock 이
    # 뒤처졌으면 조용히 다시 풀어 쓰는데, 이 자리는 릴리스를 굽는 도중에도 불린다
    # (`build_release.*`). 굽는 자리가 lock 을 움직이면 방금 구운 이진과 커밋되는 lock 이
    # 갈린다 — 뒤처졌으면 고쳐 주는 대신 **못 쟀다고 말하는** 편이 옳다.
    argv = [cargo_bin, "tree", "--locked", "-p", "gui", "-e", "no-dev", "--prefix", "none",
            "--format", "{p}|{l}"]
    if target:
        argv += ["--target", target]
    out = {}
    for raw in run(argv).splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.endswith(" (*)"):        # 이미 위에서 펼친 가지
            line = line[:-4]
        m = _TREE_LINE.match(line)
        if not m:
            raise RuntimeError(f"cargo tree 줄을 못 읽었다: {raw!r}")
        kind, where = _origin(m["extra"])
        key = f"{m['name']} v{m['ver']}" + (f" ({where})" if kind == "git" else "")
        out[key] = (m["lic"].strip(), (kind, where))
    if not out:
        raise RuntimeError(f"{target or '호스트'} 의존이 0건이다 — 통과가 아니라 고장이다")
    return out


def metadata(cargo_bin):
    """`{(이름, 버전, 출처종류): 패키지}`. 소스 디렉터리·저장소 주소가 여기 있다."""
    md = json.loads(run([cargo_bin, "metadata", "--locked", "--format-version", "1"]))
    out = {}
    for p in md["packages"]:
        src = p.get("source") or ""
        kind = "git" if src.startswith("git+") else ("registry" if src else "path")
        key = (p["name"], p["version"], kind)
        if key in out:
            raise RuntimeError(f"{key} 가 둘이다 — 이 스크립트의 열쇠가 부족하다")
        out[key] = p
    return out


def pkg_for(md, name, version, kind):
    """`dep_lines` 의 한 줄에 맞는 metadata 패키지. 없으면 시끄럽게 죽는다."""
    pkg = md.get((name, version, kind))
    if pkg is None:
        raise RuntimeError(f"{name} v{version} ({kind}) 가 cargo metadata 에 없다")
    return pkg


# ── SPDX 식 ──────────────────────────────────────────────────────────────────

def _tokens(expr):
    # 옛 표기 `MIT/Apache-2.0` 은 OR 다(cargo 가 오래 허용해 온 모양이고 실측으로 33건).
    expr = expr.replace("/", " OR ")
    return re.findall(r"\(|\)|[^\s()]+", expr)


def _atom(tokens, i):
    if i >= len(tokens):
        raise ValueError("식이 갑자기 끝났다")
    if tokens[i] == "(":
        node, i = _parse(tokens, i + 1)
        if i >= len(tokens) or tokens[i] != ")":
            raise ValueError("괄호가 안 닫혔다")
        return node, i + 1
    name = tokens[i]
    i += 1
    if i + 1 < len(tokens) and tokens[i].upper() == "WITH":
        name = f"{name} WITH {tokens[i + 1]}"
        i += 2
    return ("leaf", name.rstrip("+")), i            # `GPL-3.0+` → `GPL-3.0`


def _parse(tokens, pos=0):
    """`(값, 다음 위치)`. 값은 `("leaf", id)` · `("or", [..])` · `("and", [..])`.

    ⛔ **AND 가 OR 보다 세다**(SPDX 규격). 왼쪽부터 평평하게 접으면 `A AND B OR C` 가
    `(A AND B) OR C` 가 아니라 `A AND (B OR C)` 로 읽혀, **못 지키는 조각을 고를 수
    있는 것처럼** 만든다 — 라이선스 판정에서 그것은 조용히 틀리는 쪽이다.
    """
    def and_level(i):
        parts, node = [], None
        node, i = _atom(tokens, i)
        parts.append(node)
        while i < len(tokens) and tokens[i].upper() == "AND":
            node, i = _atom(tokens, i + 1)
            parts.append(node)
        return (parts[0] if len(parts) == 1 else ("and", parts)), i

    node, i = and_level(pos)
    parts = [node]
    while i < len(tokens) and tokens[i].upper() == "OR":
        node, i = and_level(i + 1)
        parts.append(node)
    return (parts[0] if len(parts) == 1 else ("or", parts)), i


def choose(expr):
    """SPDX 식 → `(고른 라이선스 목록, 못 지키는 조각)`.

    OR 는 **우리가 하나를 고른다**(`PREFERENCE` 순). AND 는 전부 지켜야 한다.
    돌려주는 둘째 값이 비어 있지 않으면 그 크레이트는 **못 쓴다** — 그때 첫 값은
    **빈 목록이다**. 반쪽 선택을 돌려주면 부르는 쪽이 「MIT 로 받으면 되겠네」로 읽는데,
    `GPL-2.0-only AND MIT` 에서 그것은 거짓이다(AND 는 둘 다 지켜야 한다).
    """
    try:
        node, i = _parse(_tokens(expr))
        if i != len(_tokens(expr)):
            raise ValueError("식이 다 안 읽혔다")
    except (ValueError, IndexError) as e:
        return [], [f"{expr} (식을 못 읽었다: {e})"]

    def walk(n):
        kind = n[0]
        if kind == "leaf":
            return ([n[1]], []) if n[1] in ALLOWED else ([], [n[1]])
        if kind == "and":
            picked, bad = [], []
            for child in n[1]:
                p, b = walk(child)
                picked += p
                bad += b
            return picked, bad
        # or — 고를 수 있는 것 중 선호 순으로 하나
        options = []
        for child in n[1]:
            p, b = walk(child)
            if not b:
                options.append(p)
        if not options:
            return [], [expr]
        def rank(p):
            return min((PREFERENCE.index(x) if x in PREFERENCE else len(PREFERENCE)) for x in p)
        return sorted(options, key=rank)[0], []

    picked, bad = walk(node)
    if bad:
        return [], bad                       # 반쪽 선택은 안 돌려준다(독스트링)
    # 중복 제거(순서 보존)
    seen, uniq = set(), []
    for x in picked:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq, bad


# ── 라이선스 전문 ────────────────────────────────────────────────────────────

def text_of(path):
    """파일 하나를 **줄끝을 편** 글로. 못 읽으면 `None`."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        return None
    try:
        body = raw.decode("utf-8")
    except UnicodeDecodeError:
        body = raw.decode("latin-1")
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    return body.strip("\n")


def license_files(pkg_dir, walk_up=0):
    """크레이트가 배포에 실은 라이선스 파일들 → `[(파일명, 글)]` (이름순).

    `walk_up` — git 의존은 **한 저장소 안의 하위 디렉터리**로 오는 일이 흔하고, 그때
    라이선스 파일은 대개 저장소 뿌리에 하나만 있다(실측: `core-foundation-rs` 가 그렇다).
    크레이트 디렉터리에서 못 찾으면 그만큼 위로 올라가 본다. ⛔ 레지스트리 크레이트에는
    안 쓴다 — 거기서 위는 `~/.cargo/registry/src/…` 라 **남의 크레이트 자리**다.
    """
    out = []
    try:
        names = sorted(os.listdir(pkg_dir))
    except OSError:
        return out
    for name in names:
        if not LICENSE_NAME.match(name):
            continue
        if os.path.splitext(name)[1].lower() in LICENSE_SKIP_EXT:
            continue
        path = os.path.join(pkg_dir, name)
        if not os.path.isfile(path):
            continue
        body = text_of(path)
        if body:
            out.append((name, body))
    if not out and walk_up > 0:
        parent = os.path.dirname(pkg_dir.rstrip(os.sep))
        if parent and parent != pkg_dir:
            return license_files(parent, walk_up - 1)
    return out


def hint_of(filename):
    """파일 이름이 말하는 라이선스. 아무 말도 안 하면 `None`."""
    up = filename.upper()
    for needle, lic in FILE_HINT:
        if needle in up:
            return lic
    return None


def for_picked(files, picked):
    """`license_files` 중 **우리가 고른 쪽**만. 이름이 말하지 않는 것은 남긴다.

    ⛔ 전부 걸러지면 원본을 그대로 돌려준다 — 짐작이 고지를 통째로 지우게 두지 않는다.
    """
    keep = [(n, b) for n, b in files
            if hint_of(n) is None or any(hint_of(n) in p for p in picked)]
    return keep or files


# ── 이진에 박히는 자산 ───────────────────────────────────────────────────────

_EMBED = re.compile(r'include_bytes!\s*\(\s*(?:concat!\s*\()?\s*"([^"]+)"')


def embedded_assets():
    """제품 코드의 `include_bytes!` 대상 → `[(소스 상대경로, 인자)]`.

    시험 코드는 뺀다 — 이 저장소는 시험을 `*_tests.rs` 로 옆에 두고 `tests/`·`examples/`
    ·`benches/` 도 쓴다. 그 바이트는 **이진에 안 들어간다**(그래서 고지 대상도 아니다).
    """
    found = []
    crates = os.path.join(HERE, "crates")
    for base, dirs, files in os.walk(crates):
        dirs[:] = [d for d in dirs
                   if d not in ("target", "tests", "examples", "benches", "test_data")]
        for name in sorted(files):
            if not name.endswith(".rs") or name.endswith("_tests.rs"):
                continue
            path = os.path.join(base, name)
            body = text_of(path) or ""
            for arg in _EMBED.findall(body):
                found.append((os.path.relpath(path, HERE).replace(os.sep, "/"), arg))
    return found


def undeclared_assets():
    """신고 안 된 `include_bytes!` 대상 → `[(소스, 인자)]`."""
    declared = {os.path.basename(e).lower()
                for item in BUNDLED for e in item["embeds"]}
    bad = []
    for src, arg in embedded_assets():
        leaf = os.path.basename(arg).lower()
        if leaf in declared or leaf in {k.lower() for k in OURS_EMBEDDED}:
            continue
        bad.append((src, arg))
    return bad


# ── 렌더 ─────────────────────────────────────────────────────────────────────

HEAD = """\
# 서드파티 저작권 고지 (THIRD-PARTY NOTICES)

`client/build/` 의 `pytmux-gui` 이진에 링크된 서드파티 크레이트와, 이진에 박힌 서드파티
자산의 저작권 고지다. MIT·BSD·ISC·Zlib·Apache-2.0 은 **이진 재배포에도** 저작권 고지와
라이선스 전문의 재현을 요구한다 — 그래서 이 파일이 이진 옆에 함께 간다
(`client/scripts/build_release.{{sh,ps1}}` 가 `build/` 로 복사한다).

⛔ **손으로 고치지 않는다 — 생성물이다.** 고치는 자리는 생성기 한 곳이다:

```sh
python3 client/scripts/third_party_notices.py --write    # 다시 짓는다
python3 client/scripts/third_party_notices.py --check     # 낡았나 (라이선스 게이트가 부른다)
```

## 무엇을 담나

- **대상 이진**: `pytmux-gui` — `cargo tree -p gui -e no-dev`. 개발 의존은 이진에 안
  들어가므로 뺐다.
- **트리플 {ntargets}개의 합집합**: {targets}.
  이진은 OS 마다 따로 굽지만 이 파일은 한 벌이라, 어느 상자에서 지어도 같은 바이트가
  나오도록 배포 대상 전부를 합쳤다.
- **우리 크레이트 {nlocal}개는 뺐다** — 전부 MIT 이고 `client/LICENSE-MIT` 가 덮는다.
  상류에서 가져온 것과 자체 구현의 경계는 `client/PROVENANCE.md`.
- **소스 입수처**: 표의 «출처» 칸이 말한다. `crates.io` 인 것은
  `https://crates.io/crates/<이름>/<버전>` 에서 그 버전의 소스를 그대로 받을 수 있고,
  git 인 것은 그 칸의 주소·rev 가 곧 소스다. (MPL-2.0 의 소스 제공 의무는 이것으로
  충족한다 — 우리는 그 크레이트를 **고치지 않고** 링크만 한다.)
- 같은 라이선스 전문은 **한 번만** 싣고 적용받는 크레이트를 옆에 적는다. 크레이트 수만큼
  같은 글을 반복하면 3.79MB 인데 그렇게 해서 늘어나는 정보가 없다.
- 듀얼 라이선스에서 **우리가 안 고른 쪽**의 전문은, 파일 이름이 그것을 말할 때에 한해
  뺐다(`LICENSE-GPL` 처럼). GPL 전문을 함께 싣는 것은 「우리가 GPL 로 배포한다」는 거짓
  신호가 된다. 무엇을 골랐는지는 표의 «고른 것» 칸이 말한다.
"""


def render(crates, assets, nlocal):
    """고지 파일 전문. **입력이 같으면 바이트가 같다** — 시각·경로·상자 이름을 안 쓴다."""
    lines = [HEAD.format(ntargets=len(TARGETS),
                         targets=" · ".join(f"`{t}`" for t in TARGETS),
                         nlocal=nlocal)]

    # 전문 dedup — 같은 글에 번호를 하나 준다. 번호는 **처음 쓰인 순서**(크레이트 이름순)
    # 라 입력이 같으면 늘 같다.
    texts, order = {}, []
    for c in crates:
        for name, body in c["texts"]:
            if body not in texts:
                texts[body] = {"id": None, "names": set(), "users": []}
                order.append(body)
            texts[body]["names"].add(name)
            if c["key"] not in texts[body]["users"]:
                texts[body]["users"].append(c["key"])
    for asset in assets:
        body = asset["text"]
        if body not in texts:
            texts[body] = {"id": None, "names": set(), "users": []}
            order.append(body)
        texts[body]["names"].add(os.path.basename(asset["license_file"]))
        texts[body]["users"].append(asset["what"])
    for i, body in enumerate(order, 1):
        texts[body]["id"] = f"L{i:03d}"

    spdx_count = {}
    for c in crates:
        spdx_count[c["spdx"]] = spdx_count.get(c["spdx"], 0) + 1
    picked_count = {}
    for c in crates:
        for p in c["picked"]:
            picked_count[p] = picked_count.get(p, 0) + 1

    lines.append("\n## 요약\n")
    lines.append("| 항목 | 수 |")
    lines.append("|---|---:|")
    lines.append(f"| 서드파티 크레이트 | {len(crates)} |")
    lines.append(f"| 라이선스 전문(중복 제거) | {len(order)} |")
    lines.append(f"| 이진에 박힌 서드파티 자산 | {len(assets)} |")
    lines.append(f"| 전문을 안 싣는 크레이트 | {sum(1 for c in crates if not c['texts'])} |")

    lines.append("\n**우리가 고른 라이선스**(듀얼은 하나를 고른다):\n")
    lines.append("| 라이선스 | 크레이트 | 의무 |")
    lines.append("|---|---:|---|")
    for lic in sorted(picked_count, key=lambda x: (-picked_count[x], x)):
        lines.append(f"| `{lic}` | {picked_count[lic]} | {ALLOWED.get(lic, '')} |")

    lines.append("\n**선언 그대로의 SPDX**:\n")
    lines.append("| SPDX | 크레이트 |")
    lines.append("|---|---:|")
    for spdx in sorted(spdx_count, key=lambda x: (-spdx_count[x], x)):
        lines.append(f"| `{spdx}` | {spdx_count[spdx]} |")

    if assets:
        lines.append("\n## 1. 크레이트가 아닌 자산\n")
        lines.append("`include_bytes!` 로 이진 안에 들어간 것들이다.\n")
        lines.append("| 자산 | 라이선스 | 왜 이진에 있나 | 전문 |")
        lines.append("|---|---|---|---|")
        for a in assets:
            tid = texts[a["text"]]["id"]
            lines.append(f'| {a["what"]} | `{a["spdx"]}` | {a["why"]} | [{tid}](#{tid.lower()}) |')

    lines.append(f"\n## 2. 서드파티 크레이트 ({len(crates)})\n")
    lines.append("| 크레이트 | 버전 | 출처 | 선언 SPDX | 고른 것 | 전문 |")
    lines.append("|---|---|---|---|---|---|")
    for c in crates:
        if c["texts"]:
            refs = " ".join(f'[{texts[b]["id"]}](#{texts[b]["id"].lower()})'
                            for _n, b in c["texts"])
        else:
            refs = "— (배포에 전문을 안 실었다)"
        lines.append(f'| `{c["name"]}` | {c["version"]} | `{c["origin"]}` | `{c["spdx"]}` | '
                     f'`{" AND ".join(c["picked"])}` | {refs} |')

    lines.append(f"\n## 3. 라이선스 전문 ({len(order)})\n")
    for body in order:
        info = texts[body]
        tid = info["id"]
        names = " · ".join(sorted(info["names"]))
        users = ", ".join(f"`{u}`" for u in info["users"])
        lines.append(f"### {tid}\n")
        lines.append(f"*파일*: {names}\n")
        lines.append(f"*적용*: {users}\n")
        # 울타리는 백틱 넷이다 — 라이선스 전문에 ``` 이 들어 있어도 안 새어 나온다
        # (실제로 마크다운으로 쓰인 LICENSE.md 가 있다).
        lines.append("````text")
        lines.append(body)
        lines.append("````\n")

    return "\n".join(lines) + "\n"


# ── 수집 ─────────────────────────────────────────────────────────────────────

def collect(cargo_bin):
    """`(크레이트 목록, 자산 목록, 로컬 크레이트 수, 못 지키는 것)`."""
    declared = {}
    for target in TARGETS:
        for key, (lic, origin) in dep_lines(cargo_bin, target).items():
            prev = declared.get(key)
            if prev is not None and prev[0] != lic:
                raise RuntimeError(f"{key} 의 라이선스가 트리플마다 다르다: {prev[0]!r} != {lic!r}")
            declared[key] = (lic, origin)

    md = metadata(cargo_bin)
    crates, blocked, nlocal = [], [], 0
    for key in sorted(declared, key=lambda k: (k.split(" v")[0].lower(), k)):
        lic, (kind, where) = declared[key]
        if kind == "path":                  # 로컬 크레이트 = 우리 것
            nlocal += 1
            continue
        name, version = key.split(" v", 1)[0], key.split(" v", 1)[1].split(" (")[0]
        pkg = pkg_for(md, name, version, kind)
        spdx = (lic or "").strip()
        if not spdx:
            blocked.append((key, "(license 칸이 비었다)"))
            continue
        picked, bad = choose(spdx)
        if bad:
            blocked.append((key, spdx))
            continue
        pkg_dir = os.path.dirname(pkg["manifest_path"])
        # git 의존은 저장소 하위 디렉터리로 온다 — 뿌리까지 두 칸 올라가 본다.
        found = license_files(pkg_dir, walk_up=2 if kind == "git" else 0)
        crates.append({
            "key": key,
            "name": pkg["name"],
            "version": pkg["version"],
            "origin": "crates.io" if kind == "registry" else where,
            "spdx": spdx,
            "picked": picked,
            "texts": for_picked(found, picked),
        })

    assets = []
    for item in BUNDLED:
        path = os.path.join(HERE, item["license_file"])
        body = text_of(path)
        if not body:
            raise RuntimeError(f'{item["license_file"]} 을 못 읽었다 — '
                               f'{item["what"]} 의 고지 근거가 사라졌다')
        assets.append({**item, "text": body})

    return crates, assets, nlocal, blocked


# ── 판정 ─────────────────────────────────────────────────────────────────────

def content(text):
    """줄끝을 지운 '내용'. 두 VCS 어느 쪽도 줄끝을 보존해 주지 않는다(머리말)."""
    return text.replace("\r\n", "\n")


_ROW = re.compile(r"^\| `([^`]+)` \| ([^ |]+) \| `([^`]+)` \|")
CRATE_SECTION = "## 2. 서드파티 크레이트"


def listed_crates(text):
    """고지 파일의 크레이트 표 → `{"이름 v버전"}`.

    ⛔ **§2 안에서만 읽는다.** 요약의 라이선스 분포표도 「`MIT` | 190」 모양이라, 문서
    전체에서 긁으면 `MIT v190` 같은 유령 크레이트가 목록에 섞인다 — 그러면 `--covers`
    가 「덮는다」고 답하는 근거가 오염된다.

    표가 곧 기계가 읽는 목록이다(같은 것을 기계용으로 한 벌 더 적으면 갈린다).
    """
    out, inside = set(), False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.startswith(CRATE_SECTION)
            continue
        if not inside:
            continue
        m = _ROW.match(line)
        if m:
            name, version, origin = m.groups()
            out.add(f"{name} v{version}" + ("" if origin == "crates.io" else f" ({origin})"))
    return out


def cmd_write(cargo_bin):
    crates, assets, nlocal, blocked = collect(cargo_bin)
    if blocked:
        fail(f"허용 목록에 없는 라이선스 {len(blocked)}건 — 고지 파일을 안 짓는다",
             "지금 상태를 파일로 굳히면 그 크레이트가 '고지했으니 괜찮다'로 읽힌다")
        for key, spdx in blocked:
            print(f"  - {key}: {spdx}", file=sys.stderr)
        return 1
    body = render(crates, assets, nlocal)
    with open(NOTICES, "w", encoding="utf-8") as fh:      # 줄끝은 플랫폼 기본(머리말)
        fh.write(body)
    print(f"OK: THIRD-PARTY-NOTICES.md — 크레이트 {len(crates)} · 자산 {len(assets)} · "
          f"{len(body.encode('utf-8')) // 1024}KB")
    if os.path.isfile(SHIPPED) and content(text_of(SHIPPED) or "") != content(body).strip("\n"):
        print("  ⚠ build/ 사본이 아직 옛것이다 — 다음 릴리스 빌드가 갱신한다")
    return 0


def cmd_check(cargo_bin):
    rc = 0
    crates, assets, nlocal, blocked = collect(cargo_bin)

    # ① 허용 목록.
    if blocked:
        fail(f"허용 목록에 없는 라이선스가 의존 그래프에 있다 ({len(blocked)}건)",
             "정말 쓸 것이면 third_party_notices.py 의 ALLOWED 에 **의무와 함께** 더한다",
             "카피레프트(GPL·AGPL)면 더하지 말고 그 의존을 뺀다")
        for key, spdx in blocked:
            print(f"  - {key}: {spdx}", file=sys.stderr)
        rc = 1

    # ② 이진에 박힌 자산.
    undeclared = undeclared_assets()
    if undeclared:
        fail(f"고지에 신고 안 된 `include_bytes!` 자산 {len(undeclared)}건",
             "우리 것이면 OURS_EMBEDDED 에 **이유와 함께**, 남의 것이면 BUNDLED 에 더한다")
        for src, arg in undeclared:
            print(f"  - {src}: {arg}", file=sys.stderr)
        rc = 1

    # ③ 고지 파일이 지금 그래프와 같나.
    if blocked:
        return rc                                   # 못 지키는 것이 있으면 신선도는 무의미
    fresh = render(crates, assets, nlocal)
    have = text_of(NOTICES)
    if have is None:
        fail("THIRD-PARTY-NOTICES.md 가 없다 — 배포 이진에 고지가 없다는 뜻이다",
             "python3 client/scripts/third_party_notices.py --write")
        return 1
    if content(have).strip("\n") != content(fresh).strip("\n"):
        old, new = listed_crates(have), {c["key"] for c in crates}
        added, gone = sorted(new - old), sorted(old - new)
        fail("THIRD-PARTY-NOTICES.md 가 지금 의존 그래프보다 낡았다",
             "python3 client/scripts/third_party_notices.py --write 로 다시 짓고 함께 제출한다")
        if added:
            print(f"  · 고지에 없는 새 의존 {len(added)}건: "
                  f"{', '.join(added[:8])}{' …' if len(added) > 8 else ''}", file=sys.stderr)
        if gone:
            print(f"  · 이제 안 쓰는데 남아 있는 것 {len(gone)}건: "
                  f"{', '.join(gone[:8])}{' …' if len(gone) > 8 else ''}", file=sys.stderr)
        if not added and not gone:
            print("  · 크레이트 목록은 같다 — 라이선스 전문이나 버전 표기가 움직였다",
                  file=sys.stderr)
        return 1

    # ④ 배포 사본. **적색이 아니라 경고다** — 이진을 다시 굽기 전까지는 옛 사본이 옛
    #    이진과 짝이 맞고, 그 사이를 빨간 줄로 채우면 상주하는 빨강이 된다(아무도 안 본다).
    shipped = text_of(SHIPPED)
    note = ""
    if shipped is None:
        note = " · ⚠ build/ 사본이 없다(다음 릴리스 빌드가 놓는다)"
    elif content(shipped).strip("\n") != content(fresh).strip("\n"):
        note = " · ⚠ build/ 사본이 정본과 다르다(다음 릴리스 빌드가 갱신한다)"

    if rc == 0:
        print(f"OK: 서드파티 고지 — 크레이트 {len(crates)} · 자산 {len(assets)} · "
              f"라이선스 {len(set(x for c in crates for x in c['picked']))}종, "
              f"전부 허용 목록 안{note}")
    return rc


def cmd_covers(cargo_bin):
    """굽는 자리에서 부른다 — **이 상자에서 방금 링크된 것**이 전부 고지 안에 있나.

    호스트 트리플만 본다(그것이 방금 구운 이진이다). 글자 한 벌의 동일성은 안 잰다 —
    러너 셋이 그것에 걸려 릴리스를 통째로 못 내는 것보다, 「빠진 크레이트가 있나」를
    확실히 재는 편이 이 자리에서 값이 크다.
    """
    # 이 상자의 트리플이 고지가 담은 목록 안인가. **먼저 이것을 잰다** — 아니면 아래
    # 대조는 「겹치는 만큼만」 재고 초록을 줄 수 있다(그 트리플에만 오는 의존이 우연히
    # 없으면 통과한다). 배포 플랫폼을 늘렸을 때 우는 자리가 여기다.
    host = ""
    for line in run([cargo_bin, "-vV"]).splitlines():
        if line.startswith("host: "):
            host = line.split(": ", 1)[1].strip()
    if host and host not in TARGETS:
        fail(f"이 상자의 트리플({host})이 고지의 TARGETS 에 없다",
             "third_party_notices.py 의 TARGETS 에 더하고 --write 로 다시 짓는다",
             "그러지 않으면 이 플랫폼에만 오는 의존이 고지에서 통째로 빠진다")
        return 1

    have = text_of(NOTICES)
    if have is None:
        fail("THIRD-PARTY-NOTICES.md 가 없다 — 고지 없이 이진을 배포할 수 없다",
             "python3 client/scripts/third_party_notices.py --write")
        return 1
    listed = listed_crates(have)
    if not listed:
        fail("고지 파일에서 크레이트 표를 한 줄도 못 읽었다 — 이것은 통과가 아니라 고장이다",
             "표 형식이 바뀌었으면 third_party_notices.py 의 `_ROW` 도 함께 고친다")
        return 1
    missing = []
    for key, (_lic, (kind, _where)) in dep_lines(cargo_bin, None).items():
        if kind == "path":                            # 우리 크레이트
            continue
        if key not in listed:
            missing.append(key)
    if missing:
        fail(f"방금 구운 이진의 크레이트 {len(missing)}건이 고지에 없다",
             "python3 client/scripts/third_party_notices.py --write 로 다시 짓는다",
             "이 상자의 트리플이 그 스크립트의 TARGETS 에 없으면 먼저 그것부터 더한다")
        for key in sorted(missing)[:20]:
            print(f"  - {key}", file=sys.stderr)
        return 1
    print(f"OK: 고지가 이 이진의 서드파티 크레이트 {len(listed)}건을 덮는다")
    return 0


def main():
    ap = argparse.ArgumentParser(description="배포 이진의 서드파티 저작권 고지")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--write", action="store_true", help="고지 파일을 다시 짓는다")
    g.add_argument("--check", action="store_true",
                   help="허용 목록 · 고지 신선도 · 자산 신고(기본값)")
    g.add_argument("--covers", action="store_true",
                   help="이 상자에서 링크된 크레이트가 전부 고지 안에 있나(굽는 자리)")
    args = ap.parse_args()

    cargo_bin = cargo()
    if not cargo_bin:
        print("third_party_notices: cargo 를 찾을 수 없다 (PATH 에도 ~/.cargo/bin 에도 없다)",
              file=sys.stderr)
        return 2

    try:
        if args.write:
            return cmd_write(cargo_bin)
        if args.covers:
            return cmd_covers(cargo_bin)
        return cmd_check(cargo_bin)
    except RuntimeError as e:
        # 못 쟀으면 통과가 아니다 — rc 2 로 **환경 고장**임을 알린다(적색과 구분).
        print(f"third_party_notices: 못 쟀다 — {e}", file=sys.stderr)
        print("  → 오프라인이면 `cargo fetch` 로 레지스트리를 먼저 채운다"
              "(전문을 크레이트 소스에서 읽는다)", file=sys.stderr)
        print("  → `Cargo.lock` 이 뒤처졌다고 하면 여기서 안 고친다(`--locked`) —"
              " `cargo build` 로 먼저 맞추고 그 lock 을 같은 CL 에 담는다", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
