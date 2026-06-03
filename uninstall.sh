#!/bin/sh
# pytmux 제거 — install.sh 가 만든 래퍼 스크립트를 지운다.
#
# 사용법:
#   ./uninstall.sh         # 기본 위치(~/.local/bin)에서 제거
#   ./uninstall.sh DIR     # DIR 에서 제거
#   BIN=pytmux2 ./uninstall.sh   # 다른 이름으로 설치했을 때
set -eu

BIN="${BIN:-pytmux}"
DIR="${1:-$HOME/.local/bin}"
TARGET="$DIR/$BIN"

if [ -e "$TARGET" ]; then
  rm -f "$TARGET"
  echo "제거 완료: $TARGET"
else
  echo "이미 없습니다: $TARGET"
fi
