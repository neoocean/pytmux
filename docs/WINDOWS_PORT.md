# pytmux 네이티브 Windows 포팅 — 범위 조사

> 작성: 2026-06-04 · 대상: Windows 포팅을 검토/진행하는 사람·에이전트
> 관련: [DESIGN.md](DESIGN.md) · [HANDOFF.md](HANDOFF.md) · [CONTRIBUTING.md](CONTRIBUTING.md)
> 상태: **조사만 완료(코드 변경 없음).** 구현 미착수.

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
- **Windows**: 데몬화 → `subprocess` 로 `python pytmux.py server` 를 `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP` 분리 기동. 종료 → `TerminateProcess`/`proc.kill()` 또는 **Job 오브젝트** 로 자식트리 정리. `waitpid` → `proc.poll/wait`. `FD_CLOEXEC`(`server.py:62`) → `SetHandleInformation` 핸들 상속 제어.

### ④ IPC (소켓)
- **현재**: `AF_UNIX`(`launcher.py:35,69`), `asyncio.open_unix_connection`(`client.py:1592`), 소켓경로 `default_socket_path()` = `XDG_RUNTIME_DIR`/`/tmp/pytmux-{getuid}`(`protocol.py:18-25`).
- **Windows**: **TCP 루프백(127.0.0.1:랜덤포트)** 권장. 포트 번호를 `%LOCALAPPDATA%` 의 작은 파일에 기록해 클라이언트가 읽음. (Win10 AF_UNIX는 OS는 지원하나 Python asyncio Windows 지원이 불완전 → TCP가 안전.) `start_unix_server`→`start_server`, `open_unix_connection`→`open_connection`.

## 4. 기능 열화(degradation) 불가피 항목

Windows에 직접 대응물이 없어 포팅 시 약화/제거됨:

- **패널 cwd 상속** (`_pane_cwd`, `/proc/<pid>/cwd`·`lsof`, `server.py:310`) — 새 분할이 현재 디렉토리에서 시작하는 기능. Windows엔 간단한 per-process cwd 조회가 없음.
- **fg 명령 기반 탭 자동이름 + ssh 감지** (`_fg_command`, `os.tcgetpgrp`+`ps`, `server.py:568`) — ConPTY는 포그라운드 프로세스 그룹을 노출 안 함.
- **시그널 의미**(SIGHUP/SIGKILL) — TerminateProcess로 대체되나 graceful 종료 동작이 달라짐.

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

## 7. 다음 액션 (미결정)

- **(a)** 위 PoC 슬라이스를 실제 작성해 ConPTY+pyte가 이 코드와 붙는지 검증
- **(b)** 추상화 레이어 + `server.py` 리팩터 구체 단계별 구현 계획 수립
- **(c)** 보류

## 부록 — 즉시 해결된 항목

- `wcwidth` 미설치 → `pip install wcwidth`(0.7.0) 설치 완료. 이후 `fcntl` 에서 막힘(위 참조).
