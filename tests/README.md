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
  display-panes, **활성 패널 전체 테두리(파랑)/비활성(회색)**, 와이드 문자 합성, 상태줄 포맷.
- `test_replay.py` — 리플레이 골든 스냅샷: 커서 이동·CR 덮어쓰기·열 정렬·와이드
  문자·대체 화면, record→replay 라운드트립.

## 렌더 진단/리플레이 (화면 없이 출력 확인)

실제 프로그램 출력을 녹화하고 텍스트 프레임으로 재생해, 화면 없이 렌더 결과를
확인하거나 깨짐(열 밀림 등)을 오프라인 재현한다.

```sh
# 녹화: 옵션(--cols/--rows)은 파일명 앞, 실행 명령은 -- 뒤. 미지정 시 현재 터미널 크기.
python3 pytmux.py record --cols 120 cap.raw -- ls -C
python3 pytmux.py record --cols 160 cap.raw -- claude   # 상호작용도 통과(녹화)

# 재생: 녹화 폭과 동일하게. --ruler 로 열 번호 자 표시.
python3 pytmux.py replay --cols 120 cap.raw --ruler
```

프로그래밍 방식: `pytmux.replay(raw_bytes, cols, rows) -> list[str]`.

## 스위트 위생(hermetic)

스위트는 **이 상자의 사용자 상태와 무관하게** 같은 값을 내야 한다. 그래서 러너
(`run.py` 머리말)와 하니스가 시작할 때 셸에서 물려받은 것을 정리한다 —
`PYTMUX_HOME`·`NO_COLOR` 는 **거두고**, 설정 파일은 **세운다**.

- ⛔ **설정만은 거두면 오히려 샌다**(pytmux/pytmux-135). 탐색 차례가
  `$PYTMUX_CONFIG` → `$PYTMUX_HOME/config` → `$XDG_CONFIG_HOME/pytmux/config`
  → `~/.pytmux.conf` 라, `PYTMUX_HOME` 을 거두면 두 번째 자리가 사라져 곧장
  **사용자의 진짜 `~/.config/pytmux/config`** 로 떨어진다. 그래서
  `tests/hermetic.py::isolate_config` 가 `PYTMUX_CONFIG` 를 **빈 임시 파일**로
  가리킨다. 읽기(그 상자에만 있는 `set inactive-dim off` 로 결과가 갈린다)와
  쓰기(`keymap.config_path_for_write` 가 같은 차례를 쓴다 — `:settings` 경로가
  사용자 파일에 줄을 박는다) 양쪽이 걸린 자리다.
- 재는 것은 `test_config_hygiene.py`(대조군 포함). 같은 함정의 Rust 쪽 사고 기록은
  `client/CLAUDE.md` §「설정 파일은 따로 격리한다」.
- ⚠ 실험용으로 **손으로** 클라/서버를 띄울 때는 이 보호 밖이다 — 그때는
  `PYTMUX_HOME=<스크래치>` 와 함께 `<스크래치>/config` 를 빈 파일로 먼저 만든다.

### ⛔ 상자 상태는 env 만이 아니다 — **패널에 붙은 셸**도 그렇다

`server_only()` 이 만든 패널에는 **진짜 PTY 와 진짜 셸**이 붙어 있고, 그 셸이 화면에
무엇을 찍는지는 상자마다 다르다: POSIX 는 `$SHELL`→`/bin/sh`(대개 조용하다), Windows 는
`PYTMUX_SHELL`→`COMSPEC`→`cmd.exe`(**배너를 찍고 ConPTY 가 화면을 정리한다**).

⇒ **`pane.feed(...)` 로 심어 두고 재는 시험은 심기 전에 `harness.hush_pane(pane)` 을
부른다.** 안 부르면 심은 줄이 「내가 안 한 일」에 덮이고, 그 덮임은 루프가 양보하는
**아무 `await`** 에서나 일어난다 — 리눅스에서는 늘 초록이라 **안 보인다.**
실측(pytmux/pytmux-384): 전역 검색 시험 다섯이 그렇게 Windows 에서만 늘 빨갰고
2주 넘게 아무도 그것을 「환경」이라 못 갈랐다.

- 처방과 근거는 `harness.hush_pane` 머리말 **한 곳**이 쥔다(⛔ 리더를 끊는 것이지
  셸을 죽이는 것이 아니다 · ⛔ 제품의 `await asyncio.sleep(0)` 을 걷어서 고치지 마라).
- 재는 것은 `test_search_all.py::test_fill_deafens_the_pane_to_its_own_shell`
  (대조군 포함 — 셸이 pty 로 아무것도 안 돌려주는 상자에서는 세지 않고 skip 한다).

## 작성 규칙

- 각 테스트는 `async def test_*()` 이며 러너가 **새 asyncio 루프**에서 실행한다.
- 서버는 `harness.server_only()` 로 띄우고, 끝에 `harness.teardown(...)` 호출.
  (teardown 은 serve 태스크를 await 하지 않는다 — Textual run_test 와의 루프 충돌 방지.)
- 화면 검증은 `harness.pane_text(pane)` 또는 `app.view._cells` / `render_line` 의
  텍스트를 비교한다.
