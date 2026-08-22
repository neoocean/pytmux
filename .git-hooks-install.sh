#!/bin/bash
# git 훅을 설정한다 — 이 스크립트는 저장소를 처음 클론 한 후 한 번만 실행하면 된다.
# 실행: bash .git-hooks-install.sh

set -e

if [ -d .git ]; then
    git config core.hooksPath .githooks
    echo "✓ git 훅이 설정되었습니다 (git push 전에 publish_check.py 가 자동으로 실행됩니다)"
else
    echo "✗ 이 스크립트는 git 저장소 루트에서 실행해야 합니다"
    exit 1
fi
