#!/bin/sh
# 배포용 GUI 이진을 굽는다 — `build/` 에 넣을 것을 만드는 **유일한 길**.
#
# 손으로 `cargo build --release -p gui` 를 돌리면 안 되는 이유가 하나 있다. rustc 는
# 패닉 위치·`file!()` 을 위해 **컴파일 시점의 절대경로를 이진에 박는다.** 그 경로에는
# 이 상자의 계정과 워크스페이스 구조가 그대로 들어간다 — 2026-08-01 실측:
#
#   pytmux-gui-macos-arm64      /Users/<계정>/p4/... 16건 · /Users/<계정>/.cargo 329건
#   pytmux-gui-windows-x64.exe  <드라이브>:\<depot 루트>\... 21건 · C:\Users\<계정>\.cargo 1013건
#
# (위 경로를 자리표시자로 적는 것은 멋이 아니다 — 실제 문자열을 적으면 이 파일 자신이
# `scripts/check_mirror.py` ⑤ 에 걸린다. 게이트에 예외를 뚫는 대신 문서가 모양만 적는다.)
#
# `build/` 는 2026-08-01(p4 69022)부터 **공개 git 미러에 함께 올라간다.** 이진은 개정마다
# 통째로 새 blob 이라 한 번 푸시하면 히스토리에서 빼는 길이 history rewrite 뿐이다 —
# 되돌릴 수 없는 방향이므로 굽는 자리에서 막는다. `--remap-path-prefix` 로 두 뿌리
# (워크스페이스 · cargo 홈)를 중립 이름으로 바꾼다. 이것은 **경로 문자열만** 바꾸며
# 파일 접근에는 영향이 없다(백트레이스는 `/pytmux/client/crates/...` 로 읽힌다).
#
# Windows 는 같은 일을 하는 `build_release.ps1` 을 그 상자에서 돌린다(크로스 컴파일
# 불가 — GUI 는 창·GPU 백엔드를 실제로 링크한다. `build/README.md` 참조).

set -eu

cd "$(dirname "$0")/.."          # client/
CLIENT="$(pwd)"
REPO="$(cd .. && pwd)"

# 파이썬을 «어떻게 고르나» 는 저장소에 한 자리다(`scripts/pick_python.sh` 머리말 ·
# pytmux-383). 굽는 자리라 조용한 실패가 특히 나쁘다 — 고지를 안 재고 이진이 나간다.
. "$REPO/scripts/pick_python.sh"

if ! command -v cargo >/dev/null 2>&1; then
    echo "build_release: cargo 를 찾을 수 없다 (PATH 에 ~/.cargo/bin 이 있는지 확인)" >&2
    exit 2
fi

# `.cargo/config.toml` 의 macOS 링크 인자를 여기서 **다시 적는다** — cargo 는 rustflags
# 를 합치지 않고 한 출처만 쓴다(RUSTFLAGS 환경변수 > target.cfg > build). 즉 이 스크립트가
# RUSTFLAGS 를 세우는 순간 config 쪽 값은 통째로 무시된다. 조용히 빠지면 dylib 재배치
# 여지(headerpad)가 사라지므로, 값이 어긋나면 **여기서 멈춘다**.
LINK_ARGS="-Wl,-headerpad_max_install_names"
case "$(uname -s)" in
Darwin)
    if ! grep -q -- "$LINK_ARGS" .cargo/config.toml; then
        echo "build_release: .cargo/config.toml 의 macOS 링크 인자가 바뀌었다." >&2
        echo "  RUSTFLAGS 를 세우면 config 쪽 rustflags 는 통째로 무시된다 —" >&2
        echo "  이 스크립트의 LINK_ARGS 도 같이 고칠 것." >&2
        exit 2
    fi
    EXTRA="-C link-args=$LINK_ARGS"
    ;;
*)
    EXTRA=""
    ;;
esac

case "$(uname -s)/$(uname -m)" in
Darwin/arm64)   OUT=pytmux-gui-macos-arm64 ;;
Darwin/x86_64)  OUT=pytmux-gui-macos-x64 ;;
Linux/x86_64)   OUT=pytmux-gui-linux-x64 ;;
Linux/aarch64)  OUT=pytmux-gui-linux-arm64 ;;
*)
    echo "build_release: 이 플랫폼($(uname -s)/$(uname -m))의 이름 규칙이 없다 —" >&2
    echo "  build/README.md 의 표와 이 case 문에 함께 더할 것." >&2
    exit 2
    ;;
esac

# cargo 홈은 환경변수가 있으면 그것이 정본이다(CI·격리 빌드가 옮겨 쓴다).
CARGO_ROOT="${CARGO_HOME:-$HOME/.cargo}"

RUSTFLAGS="--remap-path-prefix=$REPO=/pytmux --remap-path-prefix=$CARGO_ROOT=/cargo $EXTRA"
export RUSTFLAGS

echo "build_release: $OUT"
echo "  워크스페이스 $REPO -> /pytmux"
echo "  cargo 홈     $CARGO_ROOT -> /cargo"

# 남은 인자는 cargo 로 넘긴다 — CI 는 `--locked` 를 붙여 Cargo.lock 이 조용히 움직이는
# 것을 막는다(사람이 손으로 구울 때는 대개 안 붙인다).
cargo build --release -p gui "$@"

BIN="target/release/pytmux-gui"
[ -f "$BIN" ] || { echo "build_release: $BIN 이 안 나왔다" >&2; exit 1; }

# 굽자마자 **같은 자**로 잰다 — 미러 문턱(`scripts/check_mirror.py` ⑤)이 쓰는 규칙
# 그대로다. 자가 한 벌이라 여기서 통과한 것이 저기서 떨어지는 일이 없다.
# ⛔ **"있나" 가 아니라 "도나" 로 고른다**(pytmux-383) — `command -v` 는 Windows Store
# 별칭을 못 가르고, 그 별칭이 rc 0 으로 끝나는 판에서는 아래 둘이 **아무것도 안 재고**
# 통과한다. 여기서 못 고르면 굽지 않는다.
PY=$(pick_python) || {
    echo "build_release: 쓸 만한 파이썬 3 을 못 찾았다 — 경로 유출·서드파티 고지를 못 쟀다" >&2
    echo "  → 못 쟀으면 통과가 아니다. PYTHON=<경로> 로 지정할 것" >&2
    exit 2
}
if ! "$PY" "$REPO/scripts/check_mirror.py" --scan "$CLIENT/$BIN"; then
    echo "build_release: 갓 구운 이진에 이 상자의 경로가 남았다 — build/ 에 넣지 않는다." >&2
    echo "  remap 이 안 먹었다는 뜻이다(cargo 홈이 다른 데 있나? CARGO_HOME 확인)." >&2
    exit 1
fi

# 방금 링크된 서드파티 크레이트가 **전부** 저작권 고지 안에 있나(pytmux-193).
#
# MIT·BSD·ISC·Zlib·Apache-2.0 은 이진 재배포에도 고지 재현을 요구한다 — 이진만 받아 간
# 사람 손에 그 글이 닿아야 한다. 그래서 고지 파일이 이진 **옆에** 간다.
#
# ⛔ 여기서 재는 것은 「고지가 이 이진을 덮나」이지 「고지가 최신인가」가 아니다. 후자는
# 커밋 게이트(`check_licenses.sh`)가 트리플 다섯을 다 펴서 잰다 — 그것을 굽는 자리에도
# 걸면 러너 셋이 글자 한 벌의 동일성에 걸려 릴리스를 통째로 못 낸다.
if ! "$PY" scripts/third_party_notices.py --covers; then
    echo "build_release: 이 이진의 서드파티 고지가 모자라다 — build/ 에 넣지 않는다." >&2
    exit 1
fi

cp "$BIN" "build/$OUT"
# ⚠ `cp` 는 **대상이 이미 있으면 대상의 모드를 유지한다** — 갱신할 때마다 실행 권한이
# 조용히 사라진다(실제로 그랬다: depot filetype 이 `binary` 라 받은 사람이 chmod 를 손으로
# 해야 했다. 이제 `binary+x` 이고 git 도 100755 로 싣는다). 굽는 자리에서 못박는다.
chmod +x "build/$OUT"

# 고지를 이진 옆에 둔다. 정본은 `client/THIRD-PARTY-NOTICES.md` 한 벌이고 이것은 그
# 사본이다 — 이진과 **같은 순간에** 놓이므로 배포 디렉터리 안에서 둘의 짝이 맞는다.
# ⚠ 러너 셋이 각자 이 줄을 돌지만 같은 바이트를 복사한다(생성기가 트리플 다섯의
# 합집합을 담아 상자와 무관하게 같은 파일을 낸다 — 그래서 서로의 커밋을 안 덮는다).
cp THIRD-PARTY-NOTICES.md build/THIRD-PARTY-NOTICES.md

echo "build_release: build/$OUT ($(wc -c < "build/$OUT" | tr -d ' ') bytes) — p4 edit/add 후 제출할 것"
echo "build_release: build/THIRD-PARTY-NOTICES.md 도 함께 갱신했다 — 같이 제출할 것"
