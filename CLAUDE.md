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
- ⛔ **프로세스 이름으로 일괄 kill 금지**(사고 2026-07-28, 같은 날 3회 재발):
  `Get-Process pythonw | Stop-Process -Force`(또는 `pkill -f python`·`taskkill /IM
  pythonw.exe`)는 **사용자가 지금 쓰고 있는 pytmux 서버와 pty-host 를 죽인다** — 둘 다
  이름이 그냥 `pythonw.exe`/`python3` 라 내 테스트 데몬과 구분되지 않는다. 실제로
  pytmux-client 세션의 "테스트 서버 정리" 한 줄이 라이브 세션을 3번 날렸다(그 세션을
  `claude --resume` 로 이어받을 때마다 같은 정리 단계가 다시 돌아 재발). **내가 띄운
  것만** 겨냥한다:
  - 격리해서 띄운다 — `PYTMUX_HOME=<스크래치>`(상태·소켓·캡처가 전부 그 아래로 간다)
    또는 `--socket`/`-L <이름>`. 드라이버(`.claude/skills/run-pytmux/driver.py`)는 이미
    전용 임시 상태 디렉터리로 자기를 격리한다.
  - 내린다 — `PYTMUX_HOME=<스크래치> python3 pytmux.py kill-server --yes`. 서버가 이미
    죽어 host 만 남았어도 이 명령이 그 엔드포인트의 pty-host 까지 회수한다.
  - 그래도 남으면 **pid 로만** 죽인다 — `<스크래치>/state/*.ptyhost.pid` 와
    `spawn_detached` 가 돌려준 pid. 이름 매칭으로 넓히지 않는다.
  - 증상 참고: 서버가 밖에서 강제 종료되면 클라는 재접속 실패 후
    `msg.server_lost`("…재접속에 실패했습니다 — …강제 종료됐는지 확인")를 남기고 끝난다.
    bye 경로(`msg.server_terminated` = 의도된 종료)와 이 문구로 갈린다.
- **테스트(커밋 전 필수)**: `python3 tests/run.py` — 헤드리스로 전체 스위트를 돌려
  `N passed, 0 failed` 를 확인한다. 특정 모듈만: `python3 tests/run.py test_server`.
  - 주의: `run.py` 는 실패해도 종료코드가 0 일 수 있으니 **요약줄(passed/failed)** 을
    꼭 본다. **서브셋 실행은 플러그인 믹스인 poison 으로 가짜 실패**가 날 수 있어
    권위는 항상 **전체 스위트**다.
  - **전체=적색인데 격리=녹색이면 "기존 결함"이 아니라 모듈 간 오염이다**(2026-07-26
    실측: 그 조합을 무해로 읽어 66건을 오래 방치했다 — `test_server` 56·
    `test_token_saver` 5·`test_transcript_wiring` 5). run.py 는 **전 모듈을 한
    프로세스**에서 돌리므로, 앞서 도는 모듈이 프로덕션 전역을 몽키패치하고 안 되돌리면
    뒤 모듈이 통째로 무너진다. 빠른 단서 = **실패 메시지의 낯선 리터럴**(프로덕션이
    만들 수 없는 문자열이면 테스트가 심은 값이다). 테스트가 프로덕션 전역을 갈아끼울
    땐 `harness.patched(mod, **attrs)` 로 **구간을 가둔다**. 안 되돌린 재바인딩은
    모듈 경계에서 가드가 되돌리고 `LEAK <모듈>: <속성>` 으로 보고한다
    (끄기 `PYTMUX_TEST_LEAK_GUARD=off`).
  - **에이전트 셸에서 돌릴 땐 `NO_COLOR` 를 먼저 지운다**(2026-07-28 실측): Claude Code
    툴 환경은 `NO_COLOR=1` 을 심는데, 그러면 Textual 이 `Monochrome` 필터를 물려
    `'NoneType' object has no attribute 'color'` 로 **test_client 110건이 한꺼번에**
    떨어진다 — 내 변경과 무관한 **환경 실패**다(이유가 전부 같은 한 줄이면 의심할 것).
    `Remove-Item Env:\NO_COLOR`(pwsh) / `unset NO_COLOR`(sh) 후 다시 돌린다.
    Windows 는 그 밖에 심링크 권한(`WinError 1314`)으로 감사 배터리 2건이 상시 실패한다.
  - **명시 SKIP**: 플랫폼 부적합 등으로 건너뛸 땐 조용한 `return` 대신
    `from run import skip` 후 `skip("사유")` — 요약이 `N skipped` + 사유별로 리포트해
    커버리지 갭이 보인다(신규/수정 테스트부터 점진 채택). 타임아웃(행)은 1회 재시도한다
    (`PYTMUX_TEST_TIMEOUT_RETRIES`).
  - **대기는 고정 `pilot.pause(N)` 대신 폴링 헬퍼**(신규/수정 테스트 규약): 조건 대기는
    `harness.wait_until(pilot, cond)` — Unix 즉시·느린 CI 인내. "정착했는데 조건 미충족"
    (수렴-오답 스톨)을 타임아웃과 구분해 빠르게 진단하려면 `wait_until_settled(pilot, cond,
    snapshot)`(스톨 시 `(False, 진단)` 조기 반환). 고정 pause 는 느린 러너에서 플레이크.
  - **서버 예외 만능가드**(2026-07-25 신설): `harness.teardown`/`running_server` 가 매
    테스트 끝에 `<state_base>.error.log`·`.client.crash.log` 의 **트레이스백**을 단언한다
    — 서버는 데몬(stderr=/dev/null)이라 예외를 로그에만 남겨, 종전엔 "테스트 초록불 +
    서버가 매 프레임 터짐"이 성립했다. `서버가 예외를 로그로만 삼켰다` 로 실패하면
    **먼저 진짜 결함인지 본다**. 의도적으로 예외를 내는 테스트만
    `teardown(..., allow_errors=("<where 라벨 접두>",))` 로 **좁게** 허용한다(전면
    `True` 금지 — 라벨 접두여야 `expected_thing` 허용이 `unexpected_thing` 을 안 삼킨다).
    예외 없는 진단 로그(`_log_error(where, detail)`)는 세지 않는다.
  - **표시 기능은 호출부까지 단언**: 값을 만드는 헬퍼만 테스트하면 그 값을 붙이는 호출을
    지워도 통과한다(실측 2회 — 공허 통과). 뮤테이션에 **'호출 제거'** 를 포함할 것.
  - **머신 부하가 높으면**(load ≳10) 러너가 요약 없이 절단된다 — 전체 스위트를 고집하지
    말고 **모듈 배치 + 백그라운드 실행 + 알림 대기**로 돌리고 죽은 모듈만 재실행한다.
    절단돼 요약을 못 봤을 때 회계는 `python3 tests/run.py --report` 로 복원한다(결과가
    나오는 즉시 `reports/testrun.jsonl` 에 flush — 절단 여부와 **죽을 때 물려 있던
    테스트**를 이름으로 알려준다). 끄기 = `PYTMUX_TEST_REPORT=off`.
    배치가 도는 중에는 **그 배치가 import 할 파일을 편집하지 말 것**(다음 모듈 프로세스가
    반쯤 고친 코드를 읽는다). 상세 = `docs/internal/LESSONS_2026-07-25.md`·`-25b.md`.
  - **"출력·트레이스백 없이 러너가 사라진다" 는 부하가 아니었다**(2026-07-26, p4 67413):
    `pty_backend._UnixPty._signal_group` 이 **자기 프로세스 그룹**에 SIGHUP/SIGKILL 을
    쏴 러너와 **부모 셸**까지 죽였다(`pty.fork()` 자식이 setsid 를 끝내기 전 창에는
    pgid 가 아직 부모 것 + 종전 가드 `pid < 0` 이 **pid 0** 을 통과 — POSIX 에서
    `getpgid(0)`·`kill(0)`·`waitpid(0)` 의 0 은 **호출자/자기 그룹**이다). 수정됐으니
    같은 침묵이 재발하면 **부하로 접지 말고** 리포트의 진행중 테스트 + `☠ 러너가
    <시그널> 로 종료됨` 줄부터 본다. 시그널을 계측할 땐 **SIGPIPE 를 건드리지 말 것**
    (파이썬 기본이 SIG_IGN 인데 핸들러를 달면 없던 죽음을 만든다 — 실제로 5/6 사망을
    자초했다). 상세 = `docs/internal/LESSONS_2026-07-26.md`.
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
- **게시 게이트 `python3 scripts/publish_check.py`**(rc 0 이어야 한다): 한쪽만 게시해
  생기는 **미러 드리프트를 양방향**으로 잡는다 — 미푸시 커밋 · depot 에만 있는 내용
  (git 미푸시) · git 에만 있는 내용(p4 미제출). 자동 미러가 없어 실제로 3번 물렸다
  (63471·63714 캐치업, `.gitignore` `.env` 규칙). CL 오염 검사는
  `--cl <번호>`(아래 부정 게이트와 동일 판정).
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
