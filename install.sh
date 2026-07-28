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

# 인터프리터는 **절대경로로 고정**한다(PYTHON 으로 지정 가능).
# 종전 래퍼는 `exec python3 ...` 였는데, 그러면 실행 시점의 PATH 가 고른 python3 로
# 돌아간다 — 의존성을 설치한 인터프리터와 다를 수 있다. 실측(2026-07-28): 원격
# 호스트의 homebrew `python3` 가 3.13→3.14 로 옮겨가 requirements 없는 인터프리터를
# 가리켰고, 그 뒤 원격 탭이 "서버 자동 기동 실패" 로 열리지 않았다(데몬이
# ModuleNotFoundError 로 즉사). 비대화식 ssh 처럼 PATH 가 다른 경로에서 특히 잘 터진다.
PY="${PYTHON:-}"
if [ -n "$PY" ]; then
  command -v "$PY" >/dev/null 2>&1 || { echo "오류: PYTHON=$PY 를 실행할 수 없습니다." >&2; exit 1; }
  PY="$(command -v "$PY")"
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
fi

have_py3=0
if [ -n "$PY" ]; then
  have_py3=1
  echo "인터프리터: $PY ($("$PY" -c 'import sys;print(sys.version.split()[0])' 2>/dev/null))"
else
  echo "경고: python3 를 PATH 에서 찾지 못했습니다. 설치는 계속하지만 실행 시 필요합니다." >&2
  PY=python3          # 폴백: 래퍼는 종전대로 PATH 조회에 맡긴다
fi

# 의존성 설치.
if [ "${SKIP_DEPS:-0}" != "1" ] && [ "$have_py3" = "1" ] && [ -f "$REPO/requirements.txt" ]; then
  echo "의존성 설치: $PY -m pip install -r requirements.txt"
  "$PY" -m pip install -r "$REPO/requirements.txt" || \
    echo "경고: 의존성 설치 실패. 수동 실행: \"$PY\" -m pip install -r \"$REPO/requirements.txt\"" >&2
fi

# 바이트코드 사전컴파일(A5): 설치 시 .pyc 를 미리 만들어 첫 실행이 컴파일을 지불하지
# 않게 한다(attach cold import 단축, 런타임 동작 불변·패키징만). 실패해도 설치는 계속
# (런타임이 어차피 lazily 컴파일하므로 치명적이지 않음).
if [ "$have_py3" = "1" ] && [ -d "$REPO/pytmuxlib" ]; then
  echo "바이트코드 사전컴파일: $PY -m compileall pytmuxlib"
  "$PY" -m compileall -q "$REPO/pytmuxlib" "$ENTRY" || \
    echo "경고: 사전컴파일 실패(무시 가능 — 첫 실행 시 자동 컴파일됨)." >&2
fi

mkdir -p "$DIR"
cat > "$TARGET" <<EOF
#!/bin/sh
# pytmux 런처 — install.sh 가 생성. 진입점: $ENTRY
# 인터프리터는 설치 시점에 고정한다(PATH 가 다른 python3 를 고르면 서버 데몬이
# 의존성 없이 떠 조용히 죽는다 — install.sh 주석 참조).
exec "$PY" "$ENTRY" "\$@"
EOF
chmod +x "$TARGET"

echo "설치 완료: $TARGET -> $ENTRY"

# 설치 검증: 래퍼가 쓸 **바로 그 인터프리터**로 서버 의존성이 import 되는지 본다.
# 여기서 잡지 못하면 다음 실패 지점은 "attach 는 되는데 서버가 안 뜬다" 라 진단이
# 훨씬 비싸다(특히 ssh 원격 탭).
if [ "$have_py3" = "1" ]; then
  if ! "$PY" -c 'import pyte, wcwidth, textual' >/dev/null 2>&1; then
    echo "경고: 이 인터프리터에서 의존성 import 에 실패했습니다 — 서버가 뜨지 않습니다." >&2
    echo "      설치: \"$PY\" -m pip install -r \"$REPO/requirements.txt\"" >&2
  fi
fi

# DIR 이 PATH 에 없으면 안내.
case ":$PATH:" in
  *":$DIR:"*) ;;
  *)
    echo
    echo "주의: $DIR 가 PATH 에 없습니다. 셸 설정(예: ~/.zshrc)에 추가하세요:"
    echo "  export PATH=\"$DIR:\$PATH\""
    ;;
esac
