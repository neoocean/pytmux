# pytmux 헤드리스 테스트

화면(TUI) 없이 동작을 검증하는 테스트 모음. 렌더 결과를 텍스트로 덤프해
비교하므로 실제 터미널/디스플레이가 필요 없다.

## 실행

```sh
python3 tests/run.py            # 전체
python3 tests/run.py test_client  # 특정 모듈만
```

종료 코드 0 = 전부 통과. 실패 시 해당 테스트의 트레이스백을 출력한다.

## 구성

- `harness.py` — 공용 헬퍼(서버 기동/정리, 패널 텍스트 덤프, 메시지 수신,
  headless 앱 생성).
- `test_protocol.py` — 프레이밍/색/리밋시각 파서, 키 변환, 설정 파일 로드.
- `test_model.py` — Pane: 스크롤백, **대체 화면 버퍼**, **와이드 문자**, 리사이즈, respawn.
- `test_server.py` — 패널/윈도우/세션 조작, 동기화, 검색·버퍼·캡처, 레이아웃
  저장/복원, 외부 제어, 다중 클라이언트 최소 크기.
- `test_client.py` — Textual headless: 명령 프롬프트(모달 Input)·`?`·자동완성·
  `help`, ESC 명령 모드, **IME 단축키**(한글 자모→QWERTY)·Ctrl+한글 무crash,
  display-panes, 포커스 패널 경계 강조, 와이드 문자 합성, 상태줄 포맷.

## 작성 규칙

- 각 테스트는 `async def test_*()` 이며 러너가 **새 asyncio 루프**에서 실행한다.
- 서버는 `harness.server_only()` 로 띄우고, 끝에 `harness.teardown(...)` 호출.
  (teardown 은 serve 태스크를 await 하지 않는다 — Textual run_test 와의 루프 충돌 방지.)
- 화면 검증은 `harness.pane_text(pane)` 또는 `app.view._cells` / `render_line` 의
  텍스트를 비교한다.
