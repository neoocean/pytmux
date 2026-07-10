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
  - macOS 헤드리스 러너는 일부 PTY 스위트를 인프라 레벨로 wedge → CI 매트릭스에서
    제외(로컬이 권위). 실 PTY·실 ConPTY(Windows)·실 Claude 패널은 driver 검증 불가.

## 아키텍처 한눈에
- 코어: `pytmuxlib/*.py`. 서버측 = `server.py`(합성 진입)·`serverio.py`(명령/플러시/
  브로드캐스트)·`serverpty.py`·`serverremote.py`(페더레이션)·`servertree.py`·
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
- 명령 라우팅은 명시적 if/elif(동적 디스패치 거의 없음)라 문자열로 핸들러를 바로 찾을 수
  있다. 예: 클라측은 `grep '"split-window"'`(clientcmd.py), 서버측은 `grep 'action == "split"'`
  (serverio.py `_handle_cmd`).

## 게시(이 저장소 관례)
- 코드 변경은 **Perforce submit + git push** 양쪽(번호 CL, 내 파일만 명시 add).
  `docs/internal/` 은 gitignore → **p4 전용**.
- 공유 워크스페이스(병렬 세션)라 게시 전 `p4 diff`/`git diff` 로 **내 hunk 만**인지 확인.
- 자세한 워크플로·코딩 규약은 `docs/CONTRIBUTING.md`.
