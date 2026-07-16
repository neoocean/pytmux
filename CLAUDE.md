# CLAUDE.md — pytmux 에이전트 온보딩

> LLM/에이전트가 이 저장소에서 작업할 때 먼저 읽는 30초 안내(LLM 친화성 4-3).
> 사람용 상세 문서는 **GitHub 위키**(매뉴얼·갤러리·플러그인·기능비교·기여)와 `docs/internal/`(p4 전용).

## 무엇인가
Python/Textual 기반 tmux 유사 터미널 멀티플렉서. 단일 서버(데몬)–다중 클라이언트
구조(서버는 단일 스레드 asyncio 루프), Windows/macOS/Linux 지원, Claude Code 토큰
추적·리밋 자동 재개 + 원격 페더레이션(ssh) 포함.

## 빌드/실행/테스트
- 의존성: `pip install -r requirements.txt` (Textual·pyte·wcwidth 등).
- 실행: `python3 pytmux.py` (또는 설치 후 `pytmux`).
- **테스트(커밋 전 필수)**: `python3 tests/run.py` — 헤드리스로 전체 스위트를 돌려
  `N passed, 0 failed` 를 확인한다. 특정 모듈만: `python3 tests/run.py test_server`.
  - 주의: `run.py` 는 실패해도 종료코드가 0 일 수 있으니 **요약줄(passed/failed)** 을
    꼭 본다. **서브셋 실행은 플러그인 믹스인 poison 으로 가짜 실패**가 날 수 있어
    권위는 항상 **전체 스위트**다.
  - **명시 SKIP**: 플랫폼 부적합 등으로 건너뛸 땐 조용한 `return` 대신
    `from run import skip` 후 `skip("사유")` — 요약이 `N skipped` + 사유별로 리포트해
    커버리지 갭이 보인다(신규/수정 테스트부터 점진 채택). 타임아웃(행)은 1회 재시도한다
    (`PYTMUX_TEST_TIMEOUT_RETRIES`).
  - **대기는 고정 `pilot.pause(N)` 대신 폴링 헬퍼**(신규/수정 테스트 규약): 조건 대기는
    `harness.wait_until(pilot, cond)` — Unix 즉시·느린 CI 인내. "정착했는데 조건 미충족"
    (수렴-오답 스톨)을 타임아웃과 구분해 빠르게 진단하려면 `wait_until_settled(pilot, cond,
    snapshot)`(스톨 시 `(False, 진단)` 조기 반환). 고정 pause 는 느린 러너에서 플레이크.
  - macOS 헤드리스 러너는 일부 PTY 스위트를 인프라 레벨로 wedge → CI 매트릭스에서
    제외(로컬이 권위). 실 PTY·실 ConPTY(Windows)·실 Claude 패널은 driver 검증 불가.

## 아키텍처 한눈에
- 코어: `pytmuxlib/*.py`. 서버측 = `server.py`(합성 진입)·`serverio.py`(연결/라우팅/플러시/
  브로드캐스트)·`servercmd.py`(명령 핸들러 테이블)·`serverpty.py`·`serverremote.py`
  (페더레이션)·`servertree.py`·
  `serverpersist.py`(세션유지 재시작). 클라측 = `client.py`·`clientscreens.py`·
  `clientwidgets.py`·`clientutil.py`. 공통 = `model.py`·`protocol.py`·`ipc.py`·
  `vtparse.py`(VT 파서)·`pty_backend.py`/`conpty.py`(Windows)·`ptyhost*.py`(아웃오브
  프로세스 pty-host).
- 플러그인: `pytmuxlib/plugins/<name>/`. **delete-to-disable**: 디렉토리를 지우면 그
  기능이 조용히 사라진다(코어는 플러그인을 직접 import 하지 않고 레지스트리 훅으로만
  닿는다). 훅 계약은 `pytmuxlib/plugins/__init__.py` 의 `Registry` 한 곳에 모여 있다.

## LLM 작업 팁(중요)
- **거대 파일은 부분 읽기**(아래 줄수는 **규모 판단용 대략치** — 정확 행수는 `wc -l`,
  드리프트 잦아 자릿수만): `clientscreens.py`·`tests/test_client.py`·`tests/test_server.py`
  (수천 줄대)·`plugins/claude-code/screens.py`·`plugins/claude-code/servermixin.py`(단일
  클래스)·`model.py`·`serverio.py` 등은 한 컨텍스트에 안 들어온다. `grep -n '^class \|^    def '`
  로 위치를 잡고 Read offset/limit 으로 관심 영역만 읽어 부분 수정→회귀를 피한다.
  (`servermixin.py` 는 상단 **메서드 인덱스 주석**에 섹션→앵커 메서드명이 있다.) (`client.py` 는 믹스인 3모듈로
  분할돼 ~1.7천 줄이다 — `clientconn.py`·`clientcmd.py`·`clientio.py` 참조.)
- **거대 문서 Read 주의**: `docs/internal/HANDOFF.md`(수백 KB)·`IMPROVEMENT_OPPORTUNITIES.md`
  를 통째로 Read 하면 컨텍스트 예산을 소진한다. 루트 `MEMORY.md`(주제→파일→p4 CL 색인)로
  먼저 관련 항목을 찾아 해당 파일만 본다.
- **동적 합성 메서드**: `Server` 의 일부 메서드(`set_autoresume`·`_scan_claude` 등)는
  `server.py` 에 없고 런타임에 플러그인 믹스인(`plugins/claude-code/servermixin.py`)으로
  합성된다. jump-to-def 가 안 닿으면 그 파일을 grep 한다(server.py 의 `class Server` 위
  주석 참조).
- 명령은 문자열로 핸들러를 바로 찾을 수 있다. 클라측은 명시적 if/elif —
  `grep '"split-window"'`(clientcmd.py). 서버측은 **action→핸들러 테이블** —
  `grep '@_cmd("split"'`(servercmd.py `_CMD_TABLE`). 서버 핸들러의 응답 방식은
  테이블이 **데이터로 선언**한다(`FULL`=요청 클라 full 재동기 / `HANDLED`=핸들러가
  응답 완결 / `DYNAMIC`=핸들러가 반환값으로 결정 — `kill_pane` 뿐). 계약은 servercmd
  모듈 docstring, 전수 고정은 `test_command_table_disposition_golden`.
  `serverio._handle_cmd` 에는 페더레이션/원격 보기 **라우팅**만 남아 있고, 테이블에 없는
  action 은 `_dispatch_plugin_cmd` 로 플러그인 훅에 넘어간다.

## 게시(이 저장소 관례)
- 코드 변경은 **Perforce submit + git push** 양쪽(번호 CL, 내 파일만 명시 add).
  `docs/internal/` 은 gitignore → **p4 전용**.
- 공유 워크스페이스(병렬 세션)라 게시 전 `p4 diff`/`git diff` 로 **내 hunk 만**인지 확인.
- **CL 을 만드는 것만으로 남의 파일이 딸려온다**: `p4 change -o | p4 change -i` 는 스펙
  Files: 에 **default CL 에 열린 파일이 전부**(= 병렬 세션 것까지) 실려 새 CL 로 끌려
  들어간다(`created with N open file(s)` 가 그 신호). 그러니 **제출 직전** 부정 게이트를
  돌려 0 이 아니면 멈춘다 — 확인 없이 submit 하면 남의 WIP 를 대신 올린다(실제 재발):
  ```sh
  p4 opened -c <CL> | sed 's/#.*//' | grep -v "/pytmux/" | xargs p4 reopen -c default
  p4 opened -c <CL> | grep -vc "/pytmux/"     # 0 이어야 제출
  ```
- 자세한 워크플로·코딩 규약은 **GitHub 위키 `Contributing`**(`docs/CONTRIBUTING.md` 는
  위키로 이전하며 삭제됨 — p4 60012).
