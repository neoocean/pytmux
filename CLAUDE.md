# CLAUDE.md — pytmux 에이전트 온보딩

> LLM/에이전트가 이 저장소에서 작업할 때 먼저 읽는 30초 안내(LLM 친화성 4-3).
> 사람용 상세 문서는 **GitHub 위키**(매뉴얼·갤러리·플러그인·기능비교·기여)와 `docs/internal/`(p4 전용).
> **Rust 클라 전용 안내는 [`client/CLAUDE.md`](client/CLAUDE.md)** — 빌드·게이트·라이브
> 하네스가 거기 있다. 아래 ⛔ 안전 규율은 **이 파일 한 벌**이 정본이고 클라 쪽은 참조만 한다.

## 무엇인가
Python/Textual 기반 tmux 유사 터미널 멀티플렉서. 단일 서버(데몬)–다중 클라이언트
구조(서버는 단일 스레드 asyncio 루프), Windows/macOS/Linux 지원, Claude Code 토큰
추적·리밋 자동 재개 + 원격 페더레이션(ssh) 포함.

**두 언어 · 두 클라이언트가 한 트리에 산다**(트리 통합 2026-08-01 — 계획은
`docs/internal/PYTMUX_CLIENT_MERGE_PLAN_2026-07-31.md`):

| | 무엇 | 어디 |
|---|---|---|
| 서버 | 파이썬 데몬(정본) | `pytmux.py` · `pytmuxlib/` |
| 클라 1 | 파이썬 Textual — **의미의 권위** | `pytmuxlib/client*.py` |
| 클라 2 | Rust GUI `pytmux-gui` | `client/crates/gui/` |

둘 다 **같은 소켓 프로토콜**로 같은 서버에 **동시에** 붙는다(회귀시키지 않는다). 파이썬이
정본이라는 뜻은 *무엇이 맞는가*의 권위라는 것이지 유일한 제품이라는 뜻이 아니다 — 새 조작
표면은 **정본에 먼저** 들어가거나 같은 CL 에서 둘 다 들어간다.

> ⚠ **Rust TUI 는 2026-08-01 에 지웠다**(사용자 결정 · 근거
> `docs/internal/CLIENT_PRODUCT_SET_2026-08-01.md`). 같은 매체에 제품이 두 벌일 이유가
> 없었다. 그 대가로 **Rust 쪽 기계 검증이 1714 → 1189 건으로 줄었다**(세션 뷰 행동
> 오라클이 TUI 쪽에 많았다) — GUI 오라클을 늘리는 것이 그만큼 급해졌다.
> `pytmux --native` 플래그도 함께 사라졌다(GUI 는 `pytmux-gui` 를 직접 실행한다).

## 빌드/실행/테스트
- 의존성: `pip install -r requirements.txt` (Textual·pyte·wcwidth 등).
- 실행: `python3 pytmux.py` (또는 설치 후 `pytmux`).
- **git 훅 설정(처음 클론 후 한 번)**: `bash .git-hooks-install.sh` — `git push` 전에
  `publish_check.py` 를 자동으로 실행해서 미러 드리프트를 방지한다(pytmux-153).
- Rust GUI: `cd client && cargo build -p gui`.
  자세한 것(게이트 4종·패리티 래칫·라이브 하네스)은 `client/CLAUDE.md`.
- ⛔ **프로세스 이름으로 일괄 kill 금지**(사고 2026-07-28, 같은 날 3회 재발):
  `Get-Process pythonw | Stop-Process -Force`(또는 `pkill -f python`·`taskkill /IM
  pythonw.exe`)는 **사용자가 지금 쓰고 있는 pytmux 서버와 pty-host 를 죽인다** — 둘 다
  이름이 그냥 `pythonw.exe`/`python3` 라 내 테스트 데몬과 구분되지 않는다. 실제로
  Rust 클라 세션의 "테스트 서버 정리" 한 줄이 라이브 세션을 3번 날렸다(15:38·16:06·16:09 —
  트랜스크립트의 명령 시각과 pytmux 캡처가 멎은 시각이 초 단위로 일치. 그 세션을
  `claude --resume` 로 이어받을 때마다 같은 정리 단계가 다시 돌아 재발). **내가 띄운
  것만** 겨냥한다:
  - 격리해서 띄운다 — `PYTMUX_HOME=<스크래치>`(상태·소켓·캡처가 전부 그 아래로 간다)
    또는 `--socket`/`-L <이름>`. 드라이버(`.claude/skills/run-pytmux/driver.py`)는 이미
    전용 임시 상태 디렉터리로 자기를 격리한다.
  - 내린다 — `PYTMUX_HOME=<스크래치> python3 pytmux.py kill-server --yes`. 서버가 이미
    죽어 host 만 남았어도 이 명령이 그 엔드포인트의 pty-host 까지 회수한다.
  - 그래도 남으면 **pid 로만** 죽인다 — `<스크래치>/state/*.ptyhost.pid` 와
    `spawn_detached` 가 돌려준 pid. 이름 매칭으로 넓히지 않는다.
  - ★ **검증도 스코프 안에서** 한다 — 여기가 진짜 함정이다. 스코프를 지켜 `kill-server`
    를 부른 뒤 전역 `Get-Process pythonw`/`pgrep` 로 "정리됐나"를 확인하면 화면에 남는
    것은 **사용자의 라이브 데몬**이다 → "정리 실패"로 오판 → 일괄 kill 로 확대. 판정은
    **내 홈의 상태 파일**로 한다: `<PYTMUX_HOME>/state/default.port`(Windows) 또는
    `default.sock`(Unix)과 `*.ptyhost.pid` 가 없어야 한다. ⚠ **sock 파일은 "서버 종료됨"
    직후 잠깐 남는다**(kill-server 는 0.2초 지연 shutdown — 실측 1.5초 안에 사라진다) —
    직후 판정은 `ptyhost.pid` 나 스코프 안 `ls`("실행 중인 서버 없음")로 하고, sock
    잔존만 보고 확대하지 말 것(2026-07-30 두 번 오독).
  - **`ls` 같은 읽기 확인도 `PYTMUX_HOME` 을 세우고** 한다. 안 세우면 자동 발견이
    사용자의 라이브 서버를 읽어, 내 스크래치에 한 일이 "안 먹었다"로 오진된다.
  - **`PYTMUX_HOME` 없이 Rust 클라 이진을 띄우면 사용자의 라이브 서버에 붙는다**
    (`client/crates/.../endpoint.rs` 가 `PYTMUX_HOME/state` → 없으면 라이브 순으로 자동
    발견). 실험용으로 띄울 땐 **항상** `PYTMUX_HOME` 을 먼저 세운다. 반대로 클라 이진
    자체를 이름으로 치우는 건 안전하다(`pytmux-gui` 는 우리 것뿐) —
    위험한 것은 **`pythonw`·`python`** 이다.
  - 증상 참고: 서버가 밖에서 강제 종료되면 클라는 재접속 실패 후
    `msg.server_lost`("…재접속에 실패했습니다 — …강제 종료됐는지 확인")를 남기고 끝난다.
    bye 경로(`msg.server_terminated` = 의도된 종료)와 이 문구로 갈린다.
- **커밋 전 한 명령: `python3 scripts/check_all.py`**(합본 게이트 — 트리 통합 M2).
  픽스처 신선도 · 계층/라이선스/크로스OS 게이트 · 패리티 래칫 · Rust 스위트 · 파이썬
  스위트 · 미러 위생/드리프트를 순서대로 돌고 요약 한 줄을 낸다. 빠른 되먹임만 원하면
  `--fast`, 무엇을 도는지만 보려면 `--list`. **한 스텝이 넘어져도 나머지를 돈다**(한 번
  돌려 고칠 것을 전부 본다). 개별 게이트는 아래·`client/CLAUDE.md`.
  ★ **git 훅을 설정했으면 `git push` 직전에 자동으로 실행되므로**(pytmux-153) 수동으로
  또 돌릴 필요는 없다. 다만 `commit` 하기 전에 미리 실패를 보고 싶으면 이 명령을 직접 치면 된다.
  - **Windows**: 셸 게이트 셋은 **Git Bash** 로 돈다. `bash` 를 PATH 에서 그냥 집으면
    `…\WindowsApps\bash.exe`(Store 앱 별칭 = WSL 런처)가 잡혀 `Class not registered` 로
    죽는데, 그건 게이트가 **아무것도 안 재고 빨간 줄만 남기는** 상태다(2026-08-01 실측:
    계층·라이선스 둘이 그렇게 60초씩 태웠다). 이제 git 옆의 것을 스스로 찾는다 —
    다른 데 있으면 `PYTMUX_BASH=<경로>`. `--list` 가 **실제로 쓸 이진 경로**를 찍으니
    의심되면 그것부터 볼 것.
  - **SKIP 은 실패가 아니다**: p4 전용 워크스페이스(git 클론이 없는 곳)에서 `미러 드리프트`
    는 잴 것이 없어 건너뛴다. 건너뛴 것은 요약의 `건너뜀 N:` 줄에 **사유와 함께** 남는다.
  - ★ **게이트는 «이 상자» 를 자식에게서 걷어낸다**(§`child_env` 한 함수 · pytmux/pytmux-202):
    `NO_COLOR` 를 지우고 · 찾은 cargo 를 PATH 앞에 세우고 · **`PYTMUX_CONFIG` 를 빈 임시
    파일로 세운다**. 셋째가 없으면 `cargo test` 가 탐색 차례를 끝까지 걸어가 **이 상자의
    진짜 `~/.config/pytmux/config`** 를 읽는다 — 실측(2026-08-16)으로 `set status-position
    top` 한 줄에 GUI 배지 자리 오라클이 떨어졌다. 파이썬 스위트는 `tests/run.py` 가 제
    손으로 막지만 **카고는 그 프로세스를 안 지나서** 보호 밖이었다.
    ⚠ **`cargo test` 를 직접 치면 여전히 밖이다**(그때는 `PYTMUX_CONFIG` 를 손으로 세운다).
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
  - ★ **완주했으면 트래커로 흘린다 — `python3 scripts/tracker_tests.py --ingest`**
    (2026-08-05 · `pytmux/pytmux-132`). 이 저장소는 **M2** 라 러너는 저장소에 쓰지 않고
    트래커의 `sync` 도 저장소를 안 읽는다 — 그 사이를 잇는 것은 이 명령뿐이고,
    ⛔ **안 부르면 스위트가 무엇을 잡든 리포트 파일에만 남는다**(실측 2026-08-04: 유입
    **0회**. 그동안 트래커의 `doctor` 는 이 프로젝트를 「건강」이라고 답한다).
    - `reports/testrun.jsonl` 을 트래커 모양으로 바꿔 `issue ingest-tests` 에 넘긴다.
      **멱등**이라 같은 리포트를 두 번 흘려도 런 한 줄이다(런 id 는 시작 시각에서 짓는다).
      먼저 볼 때는 `--ingest --dry-run`, 변환만 볼 때는 인자 없이.
    - 트래커에서 일어나는 일: 실행 기록이 `run`(kind=test)으로 남고, **실패는 이슈가 된다** —
      케이스 이름이 곧 지문이라 같은 시험이 다시 깨지면 새 이슈가 아니라 **같은 이슈**가
      다시 열리고(재발) 런과 서로 링크된다.
    - ⛔ **절단된 run 은 안 흘린다**(요약줄 없음 · 회계 불일치 · 빈 결과 = 전부 거절, 종료코드 2).
      그 셋 중 하나면 담기는 것이 「통과했다」라는 거짓말이 된다.
    - 트래커 저장소가 형제 경로가 아니면 `ISSUE_REPO=<경로>`.
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
    ★ **그 사각지대를 겨눈 층이 `qa/` 다**(아래).

## QA 층 — `qa/` (실 PTY·실 클라를 운전한다)

위 스위트는 위젯 상태와 합성 셀을 보지만 **사용자가 실제로 보는 화면**은 안 본다. `qa/` 는
격리 슬롯 위에 진짜 데몬을 띄우고 진짜 Textual 클라를 가짜 터미널 아래 붙여 그 프레임을
잰다. 쓰는 법은 `qa/README.md`, **설계 SSOT 는 트래커**다(이 저장소는 M2):

```sh
node ../issue/bin/issue.mjs doc-get pytmux/qa-system   # 노선·결정·티어·후속
python3 qa/run.py                                      # 한 바퀴 (실측 약 15초)
python3 qa/run.py --ingest                             # ⛔ 이걸 안 부르면 결함이 어디에도 안 들어간다
```

- ⛔ **초록은 비싸다** — 종료코드 0 은 「결함 0 **이고** 건너뜀 0」일 때뿐이다. 환경 구성
  실패도 결함이고(rc 2), 미검증이 남으면 rc 3 이다. 그 표를 0 으로 접지 말 것.
- ⛔ **라이브 데몬에는 안 붙는다.** 런은 `PYTMUX_HOME=/tmp/pytmux-qa-<uid>/qa-…` 슬롯
  안에서만 돌고, 정리는 **pid 로만** 한다 — 위 「프로세스 이름으로 일괄 kill 금지」를
  코드로 못 박은 자리다(`tests/test_qa_layer.py` 가 AST 로 지킨다).
- 결함의 정본은 리포트가 아니라 **트래커의 이슈**다. `qa/out/` 은 p4·git 양쪽 제외.
- 이 층을 고쳤으면 `python3 tests/run.py test_qa_layer`(메타 QA — 오라클이 무는지)와
  실제 한 바퀴를 **둘 다** 돌린다. 메타 QA 는 전체 스위트에 들어 있어 `check_all.py` 가
  같이 돌지만, `qa/run.py` 자체는 커밋 게이트에 **안 넣었다**(설계 SSOT §6).

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
  ⚠ 오늘 클라 훅은 **파이썬/Textual 모양**(파이썬 객체를 주고받는다)이라 소켓을 못 건넌다 —
  Rust 클라에는 손으로 옮긴 것만 있다. 선언형으로 바꿔 한 벌이 세 클라에 뜨게 하는 계획이
  `docs/internal/PYTMUX_CLIENT_MERGE_PLAN_2026-07-31.md` §7(M4)다.
- **`client/` — Rust 클라 워크스페이스**(Cargo). `crates/base`(상태·키맵·
  명령, **UI 의존 없음** — 계층 게이트가 강제)·`_proto`(소켓 프로토콜, 서버와 동형)·
  `_tui`/`_gui`(뷰 두 벌)·`_claude`·`_clip` + `warpui`·`warpui_core`(상류 스냅샷, MIT
  경계 = `client/PROVENANCE.md`). `client/scripts/gen_*.py` 는 **정본을 직접 import 해**
  픽스처를 뽑는다(`--pytmux` 기본값이 저장소 루트) — 정본이 움직이면 다시 뽑아야 한다.

## LLM 작업 팁(중요)
- **거대 파일은 부분 읽기**(아래 줄수는 **규모 판단용 대략치** — 정확 행수는 `wc -l`,
  드리프트 잦아 자릿수만): `clientscreens.py`·`tests/test_client.py`·`tests/test_server.py`
  (수천 줄대)·`plugins/claude-code/screens.py`·`plugins/claude-code/servermixin.py`(단일
  클래스)·`model.py`·`serverio.py` 등은 한 컨텍스트에 안 들어온다. `grep -n '^class \|^    def '`
  로 위치를 잡고 Read offset/limit 으로 관심 영역만 읽어 부분 수정→회귀를 피한다.
  **Rust 쪽도 같다**:
  `client/crates/gui/src/session_view.rs`(4천 줄대)·`gui/src/session_view_tests.rs`·
  `base/src/keymap.rs`·`proto/src/{session,command}.rs`. 앵커는 `grep -n '^pub fn \|^fn \|^impl \|^#\[test\]'`.
  (`servermixin.py` 는 상단 **메서드 인덱스 주석**에 섹션→앵커 메서드명이 있다.) (`client.py` 는 믹스인 3모듈로
  분할돼 ~1.7천 줄이다 — `clientconn.py`·`clientcmd.py`·`clientio.py` 참조.)
- **거대 문서 Read 주의**: `docs/internal/HANDOFF.md`(수백 KB)·`IMPROVEMENT_OPPORTUNITIES.md`
  를 통째로 Read 하면 컨텍스트 예산을 소진한다. 루트 `MEMORY.md`(주제→파일→p4 CL 색인)로
  먼저 관련 항목을 찾아 해당 파일만 본다.
- **항목(제보·결함·할일)의 정본은 이슈트래커다**(이전 2026-08-03 · 단계 **M2**):
  `//woojinkim/scripts/issue` 가 권위이고 `docs/internal/qa/issues/pytmux-<번호>.md` 는
  **자동 생성 미러**다 — ⛔ **그 파일을 손으로 고치지 말 것**(다음 `sync` 가 드리프트로
  신고하고 반영하지 않는다). 고치는 길은 MCP `issue_update`·`issue_create` → `mirror --write`
  → 사람이 P4 제출. 규약은 그 디렉터리 README.
  ★ **기계가 잡은 결함의 유입구는 `python3 scripts/tracker_tests.py --ingest` 다**(위 테스트 절)
  — M2 에서는 그 명령이 **유일한 길**이고, 안 부르면 스위트 실패가 어디에도 안 들어간다.
  HANDOFF §10-21·ARCHIVE §13-4 는 **색인 표**만
  두고 본문을 갖지 않는다 — ⛔ **핸드오프에 항목을 다시 적지 말 것**(사본이 둘이면 SSOT 가
  아니다).
- ⛔ ★ **내부 문서 361편은 이제 저장소에 없다 — 링크 스텁만 있다**(2026-08-03 · 사용자 지시 ·
  실측 2026-08-06 · 2.70M자). `docs/internal/**/*.md` 를 열면 **제목 + 트래커 링크**뿐이다.
  전문을 읽는 곳:
  - 웹 — <http://100.79.188.26:8086/d/pytmux/<slug>>(각 스텁이 자기 링크를 갖고 있다)
  - 셸 — `node ../issue/bin/issue.mjs doc-get pytmux/<slug>`(전문 · **자르지 않는다**) ·
    판 이력·비교는 `doc-history`·`doc-diff`. **웹이 안 떠도 이쪽은 뜬다** — 웹을 거치지 않고
    같은 DB 를 직접 읽는다(읽기는 MCP 로 안 나간다).
  ⛔ **예전에 여기 「오프라인 — 저널 JSONL 을 P4 로 받아 `issue rebuild` 가 복원한다」고 적혀
  있던 것은 틀렸다**(2026-08-05 · 트래커 `issue/issue-75` · p4 70444 · 70536). 그 파일들은
  **depot 에서 지워졌고**(delete change 70446) `.p4ignore` 가 `data/` 를 예외 없이 전부
  제외하므로 그 경로를 `p4 sync` 해도 **아무것도 안 온다**. 저널 쓰기는 은퇴했고
  (`ISSUE_JOURNAL_ON=1` 로만 되살아난다) `rebuild` 는 이제 **옆에 있는 옛 DB 를 이월**한다 —
  ⛔ **저널은 복구원이 아니다.**
  ⚠ `scripts/issue/data/issues.db` 가 **없는 머신**이면(그 파일도 `.p4ignore` 다) 위 셸 명령도
  못 뜬다. 그때 세우는 길은 depot 에 4시간마다 저절로 남는 스냅샷이다 —
  `p4 sync //woojinkim/scripts/issue/snapshots/issues.sql` 뒤
  `node bin/issue.mjs restore snapshots/issues.sql`(옆에 `data/restored-*.db` 가 생기고,
  제자리를 갈아 끼우는 것은 사람이 한다). 파일 타입이 `text+S64` 라 **되돌릴 수 있는 범위는
  약 10.7일**이다.
  **새 글·수정은 트래커에서** 하고 `issue mirror --project pytmux --write` 로 스텁을 갱신한다 —
  스텁 파일을 손으로 고치면 다음 미러가 되돌린다. `benchmark/` 2451편은 데이터라 대상 밖이다.
  ⚠ 루트 `CLAUDE.md`·`client/CLAUDE.md` 는 **스텁이 아니다**(문서 루트 밖) — 여기 ⛔ 안전
  규율은 저장소 안에 그대로 남는다.
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
- **서버와 세 클라가 한 CL 이다**(트리 통합 2026-08-01). `client/` 는 같은 depot 경로
  `//woojinkim/scripts/pytmux/...` 안이고 같은 게이트를 탄다 — 프로토콜을 건드리면 서버와
  소비자 셋이 **같은 CL 안에** 들어간다(종전에는 두 트리라 반쪽 CL 이 정상이었고, 하나를
  되돌리면 반쪽만 되돌아갔다). 슬라이스 리포트는 **`docs/internal/client/reports/`** 에
  쓴다(§10-17 로 이사 — 실 캡처라 미러 제외. 종전 `client/docs/reports/` 는 비었다).
- **표면이 움직이면 세 소비자가 같이 깨진다**(트리 통합 M3 §6.2): `clientutil` 의
  명령·설정·키 표나 `servercmd._CMD_TABLE` 을 건드리면 `tests/test_surface_ledger.py`
  가 먼저 운다 — 클라 픽스처가 낡았다는 뜻이다. 순서는
  `python3 client/scripts/check_fixtures.py --write` → `cd client && cargo test` →
  새로 우는 적합성 테스트가 가리키는 표면을 **두 뷰 다** 이관(같은 CL).
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
