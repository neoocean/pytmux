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
PY="${PYTHON:-python3}"
if ! "$PY" "$REPO/scripts/check_mirror.py" --scan "$CLIENT/$BIN"; then
    echo "build_release: 갓 구운 이진에 이 상자의 경로가 남았다 — build/ 에 넣지 않는다." >&2
    echo "  remap 이 안 먹었다는 뜻이다(cargo 홈이 다른 데 있나? CARGO_HOME 확인)." >&2
    exit 1
fi

cp "$BIN" "build/$OUT"
# ⚠ `cp` 는 **대상이 이미 있으면 대상의 모드를 유지한다** — 갱신할 때마다 실행 권한이
# 조용히 사라진다(실제로 그랬다: depot filetype 이 `binary` 라 받은 사람이 chmod 를 손으로
# 해야 했다. 이제 `binary+x` 이고 git 도 100755 로 싣는다). 굽는 자리에서 못박는다.
chmod +x "build/$OUT"
echo "build_release: build/$OUT ($(wc -c < "build/$OUT" | tr -d ' ') bytes) — p4 edit/add 후 제출할 것"
