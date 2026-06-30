#!/usr/bin/env bash
# pytmux 랜딩 사이트 번들 — R2(또는 임의 정적 호스팅) 배포용 자급 디렉토리를 만든다.
#
#   ./build.sh            # docs/landing/_dist/ 에 배포 가능한 사이트 생성
#   ./build.sh /tmp/site  # 다른 출력 경로
#
# docs/landing/ 은 이미 자기완결적(이미지가 image/ 하위에 있음)이라 디렉토리 그대로
# 올려도 된다. build.sh 는 배포에 불필요한 파일(README·build.sh 자신)을 뺀 깨끗한
# 번들을 _dist/ 로 추려 줄 뿐이다(배포는 하지 않음).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$here/_dist}"

rm -rf "$out"
mkdir -p "$out/image"

cp "$here/index.html" "$here/guide.html" "$here/styles.css" "$out/"
cp "$here"/image/*.svg "$out/image/"

echo "built → $out"
echo "  files: $(find "$out" -type f | wc -l | tr -d ' ')"
echo "  preview: python3 -m http.server -d \"$out\" 8000"
