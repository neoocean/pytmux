# pytmux 랜딩 사이트

`pytmux` 소개용 정적 웹사이트입니다. 의존성·빌드 도구 없이 순수 HTML/CSS 로만 되어 있습니다.

## 구성

| 파일 | 내용 |
|------|------|
| `index.html` | 랜딩(소개·핵심가치·기능·Claude 연동·운영·플러그인·갤러리·설치·**다운로드·연락**) |
| `guide.html` | 스크린샷 포함 상세 사용 가이드(설치~플러그인 12장) |
| `styles.css` | 공용 다크 테마 스타일 |
| `build.sh` | R2 배포용 자급 번들(`_dist/`) 생성 스크립트 |

스크린샷은 저장소의 `docs/image/*.svg` 를 재사용합니다(로컬 미리보기는 `../image/`).

## 로컬 미리보기

```bash
# 저장소 루트에서
python3 -m http.server 8000
# → http://localhost:8000/docs/landing/index.html
```

## 배포용 번들 만들기

`../image/` 상대 경로 때문에 `docs/landing/` 디렉토리만으로는 R2 루트에 올릴 수 없습니다.
`build.sh` 가 참조된 스크린샷을 안에 품고 경로를 고쳐 **자급 디렉토리** `_dist/` 를 만듭니다.

```bash
cd docs/landing
./build.sh                # → docs/landing/_dist/ (index.html 이 루트)
python3 -m http.server -d _dist 8000   # 번들 미리보기
```

## Cloudflare R2 배포 (참고 — 실행하지 않음)

R2 버킷에 정적 사이트로 올리는 예시입니다. 실제 배포는 별도로 진행하세요.

```bash
# 1) 번들 생성
./build.sh

# 2-A) wrangler 로 업로드 (버킷명/계정은 환경에 맞게)
#   wrangler r2 object put pytmux-site/index.html  --file _dist/index.html  --content-type text/html
#   wrangler r2 object put pytmux-site/guide.html  --file _dist/guide.html  --content-type text/html
#   wrangler r2 object put pytmux-site/styles.css  --file _dist/styles.css  --content-type text/css
#   for f in _dist/image/*.svg; do
#     wrangler r2 object put "pytmux-site/image/$(basename "$f")" --file "$f" --content-type image/svg+xml
#   done

# 2-B) 또는 S3 호환 API(aws-cli)로 동기화
#   aws s3 sync _dist/ s3://pytmux-site/ \
#     --endpoint-url https://<ACCOUNT_ID>.r2.cloudflarestorage.com \
#     --content-type-by-extension
```

배포 후 R2 버킷에 커스텀 도메인을 연결하거나 `r2.dev` 공개 URL 을 켜고,
기본 문서를 `index.html` 로 설정하면 됩니다.

## 연락

문의: [me@woojinkim.org](mailto:me@woojinkim.org) · [GitHub Issues](https://github.com/neoocean/pytmux/issues)
