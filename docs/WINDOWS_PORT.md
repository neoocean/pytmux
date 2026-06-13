# pytmux 네이티브 Windows 포팅 — 범위 조사

> 작성: 2026-06-04 · 대상: Windows 포팅을 검토/진행하는 사람·에이전트
> 관련: [DESIGN.md](DESIGN.md) · [HANDOFF.md](HANDOFF.md) · [CONTRIBUTING.md](CONTRIBUTING.md)
> 상태: **포팅 완료 + 실 Windows 박스 검증 완료(2026-06-04).** PTY/IPC/프로세스
> 추상층 3종 신설·테스트 완료, `server.py`·`model.py` PTY 생애주기 전환 완료,
> `server.serve()`/`client connect`/`launcher` 데몬·제어를 `ipc`·`proc` 로 전환
> 완료, pipe-pane 셸 분기·record 가드·열화 가드·dead code 제거·의존성/설치 래퍼까지
> 마감(§7-b4). **실 Windows 11 박스에서 의존성 클린 설치 → 헤드리스 150/150 통과 →
> 라이브 attach(새 콘솔)로 세션·ConPTY 셸·split·send-keys 입력까지 검증 완료**(§7-d).

## 0. 배경

`python3 pytmux.py` 를 Windows 네이티브 Python(3.12)에서 실행하면 다음 순서로 막힌다.

1. `ModuleNotFoundError: No module named 'wcwidth'` → `pip install wcwidth` 로 해결됨(실제 설치 완료).
2. `ModuleNotFoundError: No module named 'fcntl'` → **여기서부터는 설치로 해결 불가**. `fcntl`·`termios`·`pty`·`os.fork`·`socket.AF_UNIX`·`os.getuid` 등 POSIX 전용 기능에 의존하기 때문.

WSL/Cygwin/MSYS2 없이 **네이티브 Windows** 에서 돌리려면 "패키지 설치"가 아니라 **포팅(부분 재작성)** 이 필요하다. 이 문서는 그 범위를 파일별로 정리한다.

## 1. 결론 요약

- 절반(`model.py`·`claude.py`·`keymap.py` 와 `client.py` 대부분, `replay.replay()`)은 **이식 가능/거의 그대로**.
- `server.py`(1807줄)는 PTY·프로세스·시그널·이벤트루프 모델을 **대규모 재작성** 해야 함 — 작업의 ~70%.
- 일부 기능(패널 cwd 상속, fg 명령 기반 탭 자동이름·ssh 감지, 시그널 의미)은 Windows에 직접 대응물이 없어 **열화(degradation) 불가피**.
- 현실적 규모: **수일 단위 포팅 프로젝트**. 최대 리스크는 ConPTY × asyncio 통합과 프로세스 트리 종료.

## 2. 모듈별 이식성 등급

| 모듈 | 줄수 | 상태 | 비고 |
|---|---|---|---|
| `model.py` | — | ✅ 그대로 | 순수 트리/pyte 로직. OS 의존 없음 |
| `claude.py` | — | ✅ 그대로 | 화면 텍스트 휴리스틱. 순수 |
| `keymap.py` | — | ✅ 거의 그대로 | `XDG_CONFIG_HOME`/`~/.config`/`~/.pytmux.conf` 만 사용. `expanduser` 는 Windows 동작, XDG 미설정 시 폴백 |
| `client.py` | 3146 | 🟡 소규모 수정 | Textual은 크로스플랫폼. 아래 3곳만 |
| `protocol.py` | — | 🟠 중간 | 모듈 import 자체가 깨짐(`fcntl`,`termios`) + 소켓경로/winsize |
| `launcher.py` | 176 | 🟠 중간 | fork 데몬화 + AF_UNIX |
| `replay.py` | 199 | 🟡 부분 | `replay()` 는 순수(이식 가능), `record()` 만 PTY 사용(진단도구, 후순위) |
| `server.py` | 1807 | 🔴 대규모 재작성 | PTY·fork·fd·프로세스그룹·시그널·이벤트루프 전부 |

## 3. 다시 짜야 할 4개 핵심 서브시스템

### ① PTY 계층 — 가장 큼
- **현재**: `pty.fork()`(`server.py:39`)로 fork+exec+PTY연결을 한 번에. `os.read(fd)` 논블로킹, `os.write(fd)`, `set_winsize` = `fcntl.ioctl(TIOCSWINSZ)`(`protocol.py:55`).
- **Windows**: fork 없음 → **ConPTY**(Win10 1809+). 가장 현실적인 건 **`pywinpty`** 라이브러리(`PtyProcess.spawn`/`.read`/`.write`/`.setwinsize`/`.isalive`, Jupyter·xterm.js 검증). 직접 `CreatePseudoConsole` ctypes 호출도 가능하나 코드량 큼.

### ② 이벤트 루프 / fd 읽기 — 가장 까다로움
- **현재**: `self.loop.add_reader(fd, ...)`(`server.py:76`)로 PTY master fd를 asyncio에 등록. `asyncio.start_unix_server`(`server.py:1785`).
- **Windows 함정**: 기본 루프가 **Proactor** 인데 `add_reader` 를 **임의 핸들/파이프에 못 씀**. SelectorEventLoop는 소켓엔 되지만 ConPTY 파이프엔 안 됨.
- **해결**: 클라이언트 통신은 **TCP 루프백 + Proactor**, PTY 읽기는 **패널마다 리더 스레드** 가 블로킹 read 후 `loop.call_soon_threadsafe` 로 루프에 밀어넣는 구조. → `_on_pane_readable`/`spawn_pane`/`_pane_eof` 등 읽기 경로 전면 수정.

### ③ 프로세스 모델 & 시그널
- **현재**: `os.fork()` 이중 데몬화 + `os.setsid`(`launcher.py:16-29`), `os.killpg(os.getpgid(pid), SIGHUP/SIGKILL)`(`server.py:91,270,610,645,770`), `os.waitpid(...WNOHANG)`, `os.execvpe`.
- **Windows**: 데몬화 → `subprocess` 로 `python pytmux.py server` 를 `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_NO_WINDOW` 분리 기동(+ 가능하면 창 없는 `pythonw.exe`). 종료 → `TerminateProcess`/`proc.kill()` 또는 **Job 오브젝트** 로 자식트리 정리. `waitpid` → `proc.poll/wait`. `FD_CLOEXEC`(`server.py:62`) → `SetHandleInformation` 핸들 상속 제어.
  - **데몬 콘솔 창 억제**: 초기엔 `DETACHED_PROCESS` 만 걸었는데 일부 기동 경로(.cmd 래퍼/py 런처 경유)에서 데몬용 `python.exe` 가 콘솔 창을 띄워, 사용자가 보기엔 pytmux 와 함께 PowerShell 창이 딸려 떴다(그 창을 닫으면 서버가 죽어 클라이언트도 종료). 해결: `CREATE_NO_WINDOW` 추가 + 같은 폴더의 `pythonw.exe`(GUI 서브시스템, 콘솔 미생성) 선호(`proc._windowless_python`). 클라이언트 attach 는 그대로 기존 터미널 전경 실행.

### ④ IPC (소켓)
- **현재**: `AF_UNIX`(`launcher.py:35,69`), `asyncio.open_unix_connection`(`client.py:1592`), 소켓경로 `default_socket_path()` = `XDG_RUNTIME_DIR`/`/tmp/pytmux-{getuid}`(`protocol.py:18-25`).
- **Windows**: **TCP 루프백(127.0.0.1:랜덤포트)** 권장. 포트 번호를 `%LOCALAPPDATA%` 의 작은 파일에 기록해 클라이언트가 읽음. (Win10 AF_UNIX는 OS는 지원하나 Python asyncio Windows 지원이 불완전 → TCP가 안전.) `start_unix_server`→`start_server`, `open_unix_connection`→`open_connection`.

## 4. 기능 열화(degradation) 불가피 항목

Windows에 직접 대응물이 없어 포팅 시 약화/제거됨. **모두 크래시 없이 우아하게
폴백하도록 가드 완료**(아래 ✅) — 기능만 빠지고 서버는 정상 동작한다.

- **패널 cwd 상속** (`_pane_cwd`, `/proc/<pid>/cwd`·`lsof`) — 새 분할이 현재 디렉토리에서 시작하는 기능. Windows엔 간단한 per-process cwd 조회가 없음. ✅ `os.readlink`/`lsof` 모두 OSError 로 잡혀 `None` 반환 → 셸이 서버 cwd 에서 시작(무해 폴백).
- **fg 명령 기반 탭 자동이름 + ssh 감지** (`_fg_command`, `os.tcgetpgrp`+`ps`) — ConPTY는 포그라운드 프로세스 그룹을 노출 안 함. ✅ `os.tcgetpgrp` 는 Windows 에 아예 없어 `AttributeError`(except OSError 로 못 잡음)였으나 `pty_backend.IS_WINDOWS` 가드로 선차단 → 고정 탭이름으로 폴백.
- **시그널 의미**(SIGHUP/SIGKILL) — TerminateProcess(`proc.terminate`/`taskkill /T`)로 대체되나 graceful 종료 동작이 달라짐.
- **렌더 전용 패널 resize** — `set_winsize` 가 fcntl 지연 import → Windows 에서 `ModuleNotFoundError`. ✅ `Pane.resize` 폴백이 `(OSError, ImportError)` 를 함께 삼킴.

## 5. 소소한 수정 (`client.py`)

- 클립보드(`client.py:2234-2254`): `pbcopy/xclip/wl-copy` 목록에 `clip.exe`(복사)·`powershell Get-Clipboard`(붙여넣기) 추가.
- `run-shell`/`if-shell` 의 `/bin/sh -c`(`client.py:2548,2561,2817`): `cmd /c` 또는 `powershell -c` 로 분기.

## 6. 권장 접근 & 단계

1. **추상화 레이어 신설**: `pty_backend.py`(Unix `pty` vs Windows `pywinpty`), `ipc.py`(AF_UNIX vs TCP), `proc.py`(fork/signal vs subprocess/Job). `server.py` 가 이 추상층만 부르도록 리팩터.
2. **`server.py` 리팩터** 가 작업의 70%. fd·프로세스그룹·`add_reader` 가정을 스레드 펌프 모델로 전환하는 게 핵심.
3. **`replay.record()` 는 후순위**(개발 진단용).

### 가장 빠른 검증 슬라이스(PoC)

전면 포팅 전, 리스크 집중부만 먼저 찔러볼 것:

> `pywinpty` 로 `cmd.exe` 하나 띄움 → 스레드로 읽음 → pyte에 먹임 → `replay` 의 순수 렌더로 텍스트 덤프.

이게 되면 ①②가 풀린 것이고, 나머지는 배관 작업이다.

## 7. 다음 액션

- **(a)** ✅ PoC 슬라이스 작성·**Windows 검증 완료** → [`../scripts/poc/winpty_poc.py`](../scripts/poc/winpty_poc.py).
  - ConPTY(pywinpty) → 리더 스레드 → `call_soon_threadsafe` → asyncio → **기존** `Pane`(pyte) → **기존** `render_pane_lines`.
  - pytmuxlib **무수정**: Windows에서 `protocol.py`의 `fcntl`/`termios` import가 깨지는 문제는, 두 모듈이 없을 때만 no-op 스텁을 `sys.modules`에 심어 우회(스텁 `ioctl`은 PoC에서 호출되지 않음).
  - 검증 완료(macOS): `--selftest`로 `Pane.feed`+렌더 동작 확인 + fcntl 차단 시뮬레이션으로 스텁 경로 import 성립 확인.
  - **✅ ConPTY 절반 Windows 검증 완료(2026-06-04)**: Windows 11(10.0.22631) / Python 3.12.4 / pywinpty 3.0.3 / pyte 0.8.2 / wcwidth 0.7.0 환경에서 `python poc\winpty_poc.py` 실행 → **`PYTMUX_POC_OK` 정상 출력**(cmd.exe 의사콘솔 기동 → 리더 스레드 펌프 → pyte → 렌더, 544바이트 프레임). `pip install pywinpty` 한 번이면 됨(`pyte`/`wcwidth`는 기설치 가정). **리스크 ①(ConPTY)·②(asyncio×파이프 읽기) de-risk 완료** — 이제 §6-b 본 포팅 착수 가능.
- **(b)** ✅ **추상화 레이어 3종 신설·테스트 완료**(2026-06-04):
  - `pytmuxlib/pty_backend.py` — PTY 백엔드(Unix `pty.fork` / Windows ConPTY 리더
    스레드 펌프). spawn/start_reader/write/set_winsize/terminate/kill/reap/close.
  - `pytmuxlib/ipc.py` — IPC 전송(Unix AF_UNIX / Windows TCP 루프백 + 포트파일).
    엔드포인트를 문자열로 표현해 기존 `sock_path:str` 스레딩 보존.
  - `pytmuxlib/proc.py` — 데몬/프로세스(Unix setsid 분리·killpg / Windows
    DETACHED_PROCESS·taskkill). 서버 데몬 기동/종료 담당.
  - `pytmuxlib/protocol.py` — 최상단 `fcntl`/`termios` import 를 `set_winsize` 안으로
    지연시켜 Windows 에서 모듈 import 성립(PoC 의 sys.modules 스텁 불필요화).
- **(b2)** ✅ **`server.py`·`model.py` PTY 리팩터 완료**(작업의 ~70%): `pty.fork`·
  `os.read`·`add_reader`·`killpg`·`waitpid`·`fcntl`·`os.write`·`set_winsize` 직접
  호출을 전부 `pty_backend.PtyProcess` 로 치환. `Pane.pty` 주입, `master_fd`/`child_pid`
  와 생성자 시그니처는 호환 유지(테스트·replay·poc 무수정). 헤드리스 테스트 통과.
- **(b3)** ✅ **`client.py` Windows 분기**(§5): 클립보드 `clip`/`Get-Clipboard` 추가,
  `_shell_argv` 로 run-shell/if-shell/popup 의 `/bin/sh -c`→`cmd /c` 분기.
- **(c)** ✅ **IPC/데몬 wiring 완료**(2026-06-04): 추상층을 실제 호출부에 연결.
  - `server.serve()` 의 `asyncio.start_unix_server` → `ipc.start_server`. 확정
    엔드포인트를 `self.resolved_endpoint` 에 받아 패널 셸 `$PYTMUX`(`_fork_shell`)에
    게시. `shutdown()` 의 소켓 unlink 는 unix 엔드포인트일 때만.
  - `client` 의 `asyncio.open_unix_connection` → `ipc.open_connection`.
  - `launcher`: `can_connect` → `ipc.probe`(하위호환 이름 유지), `control_request`
    → `ipc.control_socket`, `ensure_server`(이중 fork+setsid)·`daemonize`(삭제) →
    `proc.spawn_detached(proc.server_argv(...))`, 기본 엔드포인트 → `ipc.default_endpoint`.
  - 보조파일 경로 분리: `ipc.state_base(endpoint)` 신설(unix=소켓 경로 그대로 /
    tcp=`<state_dir>/default`). `server.py` 의 `slots_path`/`opts_path`/`capture_dir`/
    `layout_path` 와 `client` 의 `mouse.log` 가 이를 경유 → TCP 에서 파일명 충돌 없음.
  - 검증: 헤드리스 124 통과(서버 serve 가 새 경로 경유), macOS 에서 `ensure_server`
    →`probe`→`ls`/`cmd`→`kill-server` 실데몬 스모크 통과.
- **(b4)** ✅ **잔여 POSIX 하드코딩/열화 가드 정리**(코드 후속, 2026-06-04):
  - `server.py` pipe-pane 의 `["/bin/sh","-c",cmd]` 하드코딩 → `proc.shell_argv`.
    셸 분기 로직을 `proc.shell_argv` 로 일원화하고 `client._shell_argv` 는 위임.
  - `replay.run_record()` 는 Windows 에서 명확한 메시지+코드 2 로 거부(진단 도구
    후순위, `replay()` 재생은 동작).
  - `_fg_command`(자동 탭이름)·`_pane_cwd`·렌더 전용 `resize` 를 Windows 에서
    크래시 없이 폴백하도록 가드(§4).
  - `protocol.default_socket_path`(dead code, `os.getuid` 의존) 제거 — `ipc`
    로 일원화.
  - 패키징: `requirements.txt` 에 `wcwidth` 명시 + `pywinpty; win32` 추가,
    `install.ps1`/`uninstall.ps1`(Windows 설치 래퍼) 신설.
  - 회귀 가드: `tests/test_windows_port.py` 확장(shell_argv/record/fg_command/
    resize). 헤드리스 전체 통과.
- **(d)** ✅ **실 Windows 박스 검증 완료**(2026-06-04, Windows 11 10.0.22631 /
  Python 3.12.10 / pywinpty 3.0.3 / textual 8.2.7 / pyte 0.8.2 / wcwidth 0.7.0):
  - **의존성 클린 설치**: 디펜던시 전무 상태에서 `py -m pip install -r
    requirements.txt` → textual/pyte/wcwidth/**pywinpty** 무오류 설치(win32 마커로
    pywinpty 자동 선택).
  - **헤드리스 테스트 150/150 통과**(`py tests/run.py`). 과거 간헐 실패하던
    `test_server.test_sync_input_broadcast` 도 이번 풀런에서 통과(레이스는 간헐적,
    재현 안 됨 — §3② 주의 유지).
  - **라이브 attach 검증**: 새 콘솔 창(실 TTY)에서 `py pytmux.py attach` 기동 →
    Textual 클라이언트 접속 → 세션 1탭/1패널 생성(ConPTY `cmd.exe` 실 셸, 캡처
    로그에 실제 프롬프트 확인) → `cmd split-window -h` 로 2패널 → `cmd send-keys`
    입력이 라이브 셸에 도달·실행 → `kill-server` 로 트리 정리. (attach 자체는 TUI라
    헤드리스 불가 — 그 아래 데몬/IPC/ConPTY/입력 경로를 제어 프로토콜·캡처 로그로
    독립 검증.)
  - **멀티바이트**: §3① NOTE 의 read 경계 깨짐은 비이슈로 확인(이전 65KB CJK/이모지
    스트림 U+FFFD 0건). 저수준 `winpty.PTY` 바이트 경로 교체 불필요.

## 부록 — 즉시 해결된 항목

- `wcwidth` 미설치 → `requirements.txt` 에 명시(0.2+). 이후 `fcntl` 에서 막혔던
  문제는 protocol 지연 import 로 해소(위 참조).
