# 기여/작업 규칙 (pytmux)

이 디렉토리(`scripts/pytmux`)에서 작업할 때 따르는 규칙이다.

## Perforce 서브밋 규칙 (필수)

1. **의미 있는 변경마다 서브밋한다.** 하나의 논리적 변경(기능 추가, 버그 수정,
   문서 변경, 리네임 등)이 완성되면 그 단위로 즉시 제출한다. 여러 무관한 변경을
   한 체인지리스트에 섞지 않는다.

2. **디폴트 체인지리스트를 사용하지 않는다.** 항상 번호가 매겨진(named/numbered)
   pending 체인지리스트를 만들어 그 안에서 제출한다.
   - `p4 add`/`p4 edit`/`p4 move` 로 연 파일은 디폴트 체인지리스트에 들어가므로,
     `p4 change` 로 새 번호 체인지리스트를 만들어 파일을 옮긴 뒤 `p4 submit -c <num>` 한다.

3. **디스크립션에 무엇을 했는지 상세히 기입한다.** 다음을 포함한다:
   - 한 줄 요약(맨 윗줄). 가능하면 `[scripts/pytmux] <영역>: <요약>` 형식.
   - 변경의 배경/이유, 구체적으로 추가·수정·삭제한 내용, 영향받는 파일 목록.

### 권장 절차 예시

```sh
# 1) 파일 열기 (add/edit/move) — 디폴트 체인지리스트로 들어감
p4 add docs/FOO.md            # 또는 p4 edit / p4 move

# 2) 상세 설명을 담은 새 번호 체인지리스트 생성 (디폴트의 파일들이 옮겨짐)
p4 change                     # 에디터에서 Description 작성 후 저장 → "Change NNNN created"

# 3) 해당 번호 체인지리스트로 제출
p4 submit -c NNNN
```

> 스크립트로 처리할 때는 `p4 change -o` 출력의 `Description:` 블록을 상세 설명으로
> 치환해 `p4 change -i` 에 넘기면 번호 체인지리스트가 생성된다.

## 코드/문서 규칙

- 설계 변경은 먼저 `docs/DESIGN.md` 에 반영한 뒤 구현한다.
- 진입점은 `pytmux.py`(얇은 진입점), 구현은 `pytmuxlib/` 패키지.
- 의존성은 `requirements.txt` 에 명시한다.

## 보안 규칙

전송 계층 보안 모델은 [DESIGN.md §5.5.1](DESIGN.md) · 위협 모델/검토는
[SECURITY_REVIEW.md](SECURITY_REVIEW.md) 참고. 새 코드는 다음을 지킨다.

- **민감 파일은 `ipc.open_private`(0600) 로 연다** — 화면 내용·토큰·캡처 등이 담기는
  영속/로그 파일을 `open(...,"w")` 로 만들면 umask(0644)로 다른 로컬 사용자가 읽을 수 있다.
- **서버 첫 메시지 핸들러는 인증을 전제한다** — `handle_client` 의 토큰(F1)·피어 UID(F2)
  검증을 우회하는 새 진입점을 만들지 않는다. 새 `control`/`cmd` 액션도 같은 신뢰 경계 안.
- **클라이언트는 서버 데이터를 실행하지 않는다** — 서버에서 받은 메시지를 `_run_command`/
  셸/`eval` 로 흘리지 않는다(표시 전용). 명령은 로컬 설정·사용자 입력에서만.
- **외부 명령은 argv 리스트로** 실행한다(`shell=True`/`os.system` 금지). 셸 실행이 의도된
  기능(popup/pipe/run-shell)이면 접근 통제로 보호하고 그 사실을 주석에 남긴다.
- **클라 입력 필드는 검증한다** — 치수는 `protocol.clamp_dim`, base64/JSON 디코드는 예외
  가드로 감싼다.

## 스크린샷 (매뉴얼 이미지)

`docs/MANUAL.md` 의 이미지는 **수작업 캡처가 아니라** `scripts/gen_screenshots.py` 가
실 클라이언트를 헤드리스로 운전해 SVG 로 생성한다(`docs/image/*.svg`). 설계·방법은
[SCREENSHOT_SCENARIO.md](SCREENSHOT_SCENARIO.md), 사용법은 그 스크립트의 docstring 참고.

- UI(테두리색·상태줄·팝업·탭바 등)나 매뉴얼 장면을 바꿨으면 **서브밋 전에 재생성**한다:
  ```bash
  python3 scripts/gen_screenshots.py            # 결정적 장면 전체 재생성
  python3 scripts/gen_screenshots.py 14-info    # 이름 매칭 장면만
  python3 scripts/gen_screenshots.py claude-suite  # 라이브 Claude 컷(실 API 호출)
  ```
- 저장 직후 `_redact_svg` 가 (1) 계정 PII(이메일·환영 배너 이름) 마스킹, (2) 한글 등
  와이드 문자 자간 보정(Rich `textLength` 버그 교정)을 자동 적용한다 — 별도 작업 불필요.
- 시계·호스트명 등 환경값은 실제값이 박혀 그 부분만 diff 가 날 수 있다(무해).

## 테스트

- 변경 후 **서브밋 전에** 헤드리스 테스트를 돌린다: `python3 tests/run.py`
  (화면/터미널 없이 전체 동작 검증, 종료 코드 0 = 통과).
- 동작을 바꾸거나 버그를 고치면 `tests/` 에 회귀 테스트를 추가한다. 화면 검증은
  `harness.pane_text()` 나 `app.view._cells`/`render_line` 의 텍스트 비교로 한다.
