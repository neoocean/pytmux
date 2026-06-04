# Windows 환경에서 `python pytmux.py` 실행 구성

> 작성: 2026-06-04 · 대상: 이 Windows 박스(또는 동일 구성)에서 pytmux 를 띄우는 사람·에이전트
> 관련: [WINDOWS_PORT.md](WINDOWS_PORT.md) · [HANDOFF.md](HANDOFF.md)
> 상태: **해결 완료(2026-06-04).** `python pytmux.py` 가 PowerShell·cmd·git-bash 모두에서 실행됨.

이 문서는 **코드 포팅이 아니라 셸 환경(`python` 명령 자체가 안 잡히는 문제)** 을 다룬다.
코드 이식성은 [WINDOWS_PORT.md](WINDOWS_PORT.md) 참고.

## 0. 증상

```
PS> python pytmux.py
python : The term 'python' is not recognized as the name of a cmdlet, function, script file, ...
```

`python` 명령이 아예 PATH 에서 잡히지 않는다. 반면 `py pytmux.py` 는 정상 동작했고
의존성(`textual`/`pyte`/`wcwidth`/`pywinpty`)도 이미 설치돼 있었다 — 즉 **제품·의존성 문제가 아니라
오직 `python` 런처가 PATH 에 없는 환경 문제**.

## 1. 근본 원인

이 박스의 Python 은 **Microsoft Store 배포판(3.12.10)** 이다. 두 가지가 겹쳐 `python` 이 안 잡힌다.

1. **이 셸들의 PATH 가 minimal 로 리셋된다.**
   - SSH/패널 로그인 셸은 PATH 를 `C:\msys64\usr\bin`, `System32`, `Perforce`, `~/.local/bin` 정도의
     최소 집합으로 재설정한다. 사용자 레지스트리 PATH(`HKCU\Environment`)에는
     `...\AppData\Local\Microsoft\WindowsApps` 가 들어 있지만, 이 minimal PATH 에는 빠져 있다.
   - 그래서 Store Python 의 App Execution Alias(`WindowsApps\python.exe`)가 PATH 에서 사라진다.
2. **`py` 런처만 살아남는다.** `py.exe` 는 `C:\Windows` 에 있어 minimal PATH 에서도 잡히므로
   `py pytmux.py` 는 됐던 것.

실제 동작하는 인터프리터(둘 다 `py` 와 동일한 Store Python 3.12 별칭):

- `C:\Users\woojinkim\AppData\Local\Microsoft\WindowsApps\python.exe` — 0바이트 reparse(App Execution
  Alias)지만 직접 호출하면 정상 실행됨(`Python 3.12.10`).
- `...\WindowsApps\PythonSoftwareFoundation.Python.3.12_qbz5n2kfra8p0\python.exe` — 버전 고정 별칭.

## 2. 해결 — `~/.local/bin` 에 `python` shim 한 쌍

`~/.local/bin` 은 **모든 셸 변종의 PATH 에 들어있다**(현 세션 PATH·레지스트리 PATH·로그인 bash
모두 확인). 이미 같은 패턴의 `tmux`/`tmux.cmd` shim 이 여기 있으므로 **동일 패턴**으로
`python`/`python.cmd` 를 추가했다.

> 이 shim 들은 **사용자 프로필(`%USERPROFILE%\.local\bin`)** 에 있고 p4 클라이언트 루트
> (`D:\p4\office`) **바깥**이라 버전관리 대상이 아니다. 새 박스에서는 아래 내용을 그대로 만들면 된다.

### `~/.local/bin/python` (git-bash / msys2 용, 확장자 없음, LF 개행)

```bash
#!/usr/bin/env bash
# `python` 명령을 노출하는 래퍼 (2026-06-04).
# 이 셸들의 PATH 에는 WindowsApps 가 빠져 있어 `python` 이 안 잡힌다.
# 실제 동작하는 Store Python 3.12 (App Execution Alias) 로 포워딩한다 — `py` 와 동일 인터프리터.
exec "/c/Users/woojinkim/AppData/Local/Microsoft/WindowsApps/python.exe" "$@"
```

### `~/.local/bin/python.cmd` (cmd / PowerShell 용)

```bat
@echo off
rem Shim so cmd/PowerShell can run `python` (WindowsApps is missing from this minimal PATH).
rem Forwards to the working Store Python 3.12 alias - same interpreter as `py`.
"C:\Users\woojinkim\AppData\Local\Microsoft\WindowsApps\python.exe" %*
```

확장자 없는 `python` 은 bash 가, `python.cmd` 는 PowerShell/cmd(PATHEXT)가 각각 집어 쓴다.
경로는 박스마다 사용자명이 다르면 맞춰 바꿀 것.

## 3. 검증

```
# PowerShell
PS> (Get-Command python).Source      # → C:\Users\woojinkim\.local\bin\python.cmd
PS> python --version                 # → Python 3.12.10
PS> python pytmux.py ls              # → "현재 실행 중인 서버 없음" (정상 동작)

# 로그인 bash
$ bash -lc 'command -v python && python --version'
/c/Users/woojinkim/.local/bin/python
Python 3.12.10
```

> 콘솔 코드페이지가 cp949 라 한글 도움말/메시지가 깨져 보일 수 있는데, 동작과는 무관하다.
> 필요하면 `chcp 65001` 또는 `$env:PYTHONUTF8=1` 로 UTF-8 출력을 맞춘다.

## 4. 대안(채택 안 함)과 이유

- **레지스트리 PATH 수정**: 새 셸에만 반영되고, minimal PATH 로 리셋하는 로그인 셸에는 무력.
  shim 이 셸 종류와 무관하게 확실.
- **Store 별칭 토글(설정 > 앱 실행 별칭)**: GUI 수동 조작 필요 + 역시 minimal PATH 문제를 못 풂.
- **`py` 만 쓰기**: 사용자가 `python pytmux.py` 로 띄우길 원했으므로 명령 자체를 살리는 게 목표.
