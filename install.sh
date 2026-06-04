#!/bin/sh
# pytmux 설치 — 의존성을 설치하고, 어디서든 `pytmux` 로 실행되도록 PATH 상의
# 디렉터리에 얇은 래퍼 스크립트를 만든다. 래퍼는 이 저장소의 pytmux.py
# 절대경로를 가리키므로 저장소를 옮기지 않는 한 그대로 동작한다.
# (POSIX 에서는 python3 이 표준이라 Windows 같은 `python` shim 은 불필요.)
#
# 사용법:
#   ./install.sh            # 의존성 설치 + 기본 위치(~/.local/bin)에 래퍼 설치
#   ./install.sh DIR        # DIR 에 설치(예: /usr/local/bin)
#   BIN=pytmux2 ./install.sh   # 다른 이름으로 설치
#   SKIP_DEPS=1 ./install.sh   # 의존성 설치 건너뜀
#
# 제거: ./uninstall.sh (같은 인자 규칙)
set -eu

# 이 스크립트(=저장소 루트)의 절대경로.
REPO="$(cd "$(dirname "$0")" && pwd)"
ENTRY="$REPO/pytmux.py"
BIN="${BIN:-pytmux}"
DIR="${1:-$HOME/.local/bin}"
TARGET="$DIR/$BIN"

if [ ! -f "$ENTRY" ]; then
  echo "오류: 진입점을 찾을 수 없습니다: $ENTRY" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "경고: python3 를 PATH 에서 찾지 못했습니다. 설치는 계속하지만 실행 시 필요합니다." >&2
fi

# 의존성 설치.
if [ "${SKIP_DEPS:-0}" != "1" ] && [ -f "$REPO/requirements.txt" ] && command -v python3 >/dev/null 2>&1; then
  echo "의존성 설치: python3 -m pip install -r requirements.txt"
  python3 -m pip install -r "$REPO/requirements.txt" || \
    echo "경고: 의존성 설치 실패. 수동 실행: python3 -m pip install -r \"$REPO/requirements.txt\"" >&2
fi

mkdir -p "$DIR"
cat > "$TARGET" <<EOF
#!/bin/sh
# pytmux 런처 — install.sh 가 생성. 진입점: $ENTRY
exec python3 "$ENTRY" "\$@"
EOF
chmod +x "$TARGET"

echo "설치 완료: $TARGET -> $ENTRY"

# DIR 이 PATH 에 없으면 안내.
case ":$PATH:" in
  *":$DIR:"*) ;;
  *)
    echo
    echo "주의: $DIR 가 PATH 에 없습니다. 셸 설정(예: ~/.zshrc)에 추가하세요:"
    echo "  export PATH=\"$DIR:\$PATH\""
    ;;
esac
