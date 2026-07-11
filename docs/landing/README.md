# pytmux 랜딩 사이트

`pytmux` 소개용 정적 웹사이트입니다. 의존성·빌드 도구 없이 순수 HTML/CSS 로만 되어 있습니다.

## 구성

| 파일 | 내용 |
|------|------|
| `index.html` | 랜딩(소개·핵심가치·기능·Claude 연동·운영·플러그인·갤러리·설치·**다운로드·연락**) |
| `guide.html` | 상세 가이드 **개요**(14개 챕터로 가는 목차·카드) |
| `guide/*.html` | 챕터별 상세 가이드 14장(`start`·`install`·`panes`·`tabs`·`scrollback`·`mouse`·`command`·`claude`·`tokens`·`remote`·`restart`·`tools`·`config`·`plugins`). 각 페이지 하단에 이전·다음 페이저 |
| `guide-nav.js` | 챕터 페이지 공용 크롬(상단 내비·목차 사이드바·이전/다음 페이저·푸터)을 `data-topic` 으로 주입. space 프로젝트 `guide-nav.js` 패턴 |
| `styles.css` | 공용 다크 테마 스타일 |
| `lightbox.js` | 스크린샷 클릭 확대(라이트박스) |
| `image/` | HTML 이 참조하는 스크린샷 SVG (자기완결용 동봉본) |
| `build.sh` | 배포용 깨끗한 번들(`_dist/`) 추출 스크립트 |

> **가이드 구조:** `guide.html` 은 개요(목차)이고, 실제 내용은 `guide/<topic>.html` 챕터
> 페이지에 있습니다. 챕터 순서·이전/다음·목차는 `guide-nav.js` 상단의 `CH` 배열 한 곳에서
> 관리합니다. 챕터를 추가하려면 (1) `guide/<topic>.html` 을 다른 챕터 복사로 만들고
> `data-topic` 을 바꾼 뒤, (2) `guide-nav.js` 의 `CH` 와 `guide.html` 의 목차·카드에 한 줄씩
> 추가합니다. 챕터 페이지는 `../styles.css`·`../guide-nav.js`·`../lightbox.js`·`../image/` 를 참조합니다.

> **자기완결:** 이미지를 `image/` 하위에 동봉했고 HTML 도 `image/…` 상대 경로만
> 씁니다. 따라서 **`docs/landing/` 디렉토리 그대로** 정적 호스팅 루트에 올리면 됩니다
> (상위 `../image/` 참조 없음). `image/*.svg` 는 저장소의 `docs/image/` 원본을 동봉한
> 사본이며, 원본 스크린샷이 갱신되면 `build.sh` 위쪽 안내대로 다시 복사해 맞춥니다.

## 로컬 미리보기

```bash
# 이 디렉토리에서 바로
cd docs/landing
python3 -m http.server 8000
# → http://localhost:8000/index.html
```

## 배포용 번들 만들기 (선택)

디렉토리가 이미 자급이라 통째로 올려도 되지만, README·build.sh 를 뺀 깨끗한 번들이
필요하면 `build.sh` 가 `_dist/` 로 추려 줍니다.

```bash
cd docs/landing
./build.sh                              # → docs/landing/_dist/ (index.html 이 루트)
python3 -m http.server -d _dist 8000    # 번들 미리보기
```

## 스크린샷 동기화

`image/*.svg` 는 `docs/image/` 의 사본입니다. 원본 스크린샷이 바뀌면 참조 중인 것만
다시 복사합니다.

```bash
cd docs/landing
for f in image/*.svg; do cp "../image/$(basename "$f")" "$f"; done
```

## Cloudflare R2 배포 (참고 — 실행하지 않음)

R2 버킷에 정적 사이트로 올리는 예시입니다. 실제 배포는 별도로 진행하세요.

```bash
# S3 호환 API(aws-cli)로 디렉토리째 동기화
#   aws s3 sync docs/landing/ s3://pytmux-site/ \
#     --exclude 'README.md' --exclude 'build.sh' --exclude '_dist/*' \
#     --endpoint-url https://<ACCOUNT_ID>.r2.cloudflarestorage.com \
#     --content-type-by-extension
```

배포 후 R2 버킷에 커스텀 도메인을 연결하거나 `r2.dev` 공개 URL 을 켜고,
기본 문서를 `index.html` 로 설정하면 됩니다.

## 연락

문의: [me@woojinkim.org](mailto:me@woojinkim.org) · [GitHub Issues](https://github.com/neoocean/pytmux/issues)
