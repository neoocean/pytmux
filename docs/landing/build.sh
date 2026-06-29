#!/usr/bin/env bash
# pytmux 랜딩 사이트 빌드 — R2(또는 임의 정적 호스팅) 배포용 자급 번들을 만든다.
#
#   ./build.sh            # docs/landing/_dist/ 에 배포 가능한 사이트 생성
#   ./build.sh /tmp/site  # 다른 출력 경로
#
# _dist/ 는 image/ 를 안에 품고 HTML 의 ../image/ 경로를 image/ 로 바꿔
# index.html 을 루트로 하는 자급 디렉토리가 된다(배포는 하지 않음).
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$here/_dist}"

rm -rf "$out"
mkdir -p "$out/image"

cp "$here/index.html" "$here/guide.html" "$here/styles.css" "$out/"

# HTML 이 참조하는 스크린샷만 골라 복사
grep -ohE '\.\./image/[0-9a-z-]+\.svg' "$here"/index.html "$here"/guide.html \
  | sort -u | sed 's#\.\./image/##' \
  | while read -r f; do cp "$here/../image/$f" "$out/image/$f"; done

# ../image/ → image/ 경로 치환 (자급 번들이므로 상위 참조 제거)
sed -i.bak 's#\.\./image/#image/#g' "$out/index.html" "$out/guide.html"
rm -f "$out"/*.bak

echo "built → $out"
echo "  files: $(find "$out" -type f | wc -l | tr -d ' ')"
echo "  preview: python3 -m http.server -d \"$out\" 8000"
