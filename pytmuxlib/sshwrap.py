"""원격(ssh) pytmux 중첩 거부를 위한 표식 전파 셸 래퍼(docs/HANDOFF.md §10).

pytmux 패널 안에서 ssh 로 원격에 접속한 뒤 그 원격에서 다시 pytmux 를 띄우면
재귀 렌더·입력 꼬임이 생긴다. **로컬** 중첩은 패널 셸에 서버가 심는 `$PYTMUX`
(소켓 경로)로 잡지만(launcher.nesting_blocked), **원격**은 ssh 가 환경변수를 기본
전파하지 않아 `$PYTMUX` 가 없다.

해법(서버 sshd_config 수정 불필요한 본안):
  1. 패널 셸 env 에 표식 `LC_PYTMUX=1` 을 심는다.
  2. 패널 셸 PATH 앞단에 ssh 래퍼를 깔아 `ssh -o SendEnv=LC_PYTMUX …` 로 실행한다
     (래퍼는 자기 디렉터리를 PATH 에서 빼고 진짜 ssh 를 exec — 무한 재귀 방지).
  3. 대부분의 sshd 기본 설정이 `AcceptEnv LANG LC_*` 이므로 이 표식이 원격 셸로
     전파되고, 원격 pytmux 의 nesting_blocked 가 `LC_PYTMUX` 를 보고 거부한다.

LC_* 를 못 받는(드문) sshd 면 표식이 전파되지 않아 원격 거부도 안 되지만, 깨지는
동작은 없다(우아한 열화). mosh 는 `-o` 옵션을 직접 받지 않으므로(–-ssh 경유) 래핑
대상에서 제외한다 — ssh/autossh 만 감싼다(둘 다 `-o` 를 ssh 로 전달).

**Windows(#1.4)**: 패널 셸이 cmd.exe 라 POSIX sh 래퍼 대신 `.cmd` 배치 래퍼를 깐다.
명령 해석이 PATH 디렉터리 순서를 먼저 따르므로(같은 dir 안에서만 PATHEXT 순서) 래퍼
디렉터리를 PATH 앞단에 두면 우리 `ssh.cmd` 가 진짜 `ssh.exe` 를 가린다. 래퍼는
`%~$PATH:E` 로 **확장자 .exe 만** 골라 진짜 ssh.exe 를 찾으므로(우리 dir 엔 .cmd 만
있어 자기 자신을 안 잡음) PATH 에서 자기 dir 을 빼는 수고가 필요 없다. 그 뒤
`ssh.exe -o SendEnv=LC_PYTMUX %*` 로 exec 한다. Windows OpenSSH 클라이언트는 SendEnv 를
지원하고, 원격 전파 여부는 POSIX 와 동일하게 sshd AcceptEnv 에 달렸다(우아한 열화).
PowerShell 패널에서도 .cmd 는 동일하게 해석된다.

표식 이름은 launcher.NEST_MARKER 와 동일해야 한다(여기 import 하면 순환이라 상수만 공유).
"""
from __future__ import annotations

import os
import stat

# 원격에 전파할 중첩 표식 환경변수. AcceptEnv LC_* 흔한 기본값을 타도록 LC_ 접두사.
# launcher.NEST_MARKER 와 반드시 일치시킬 것.
NEST_MARKER = "LC_PYTMUX"

# 래핑 대상(ssh 류). 모두 `-o SendEnv=…` 를 ssh 로 전달한다.
_WRAPPED = ("ssh", "autossh")

# POSIX sh 래퍼 본문. argv0(basename)로 어떤 명령인지 알아내고, 자기 디렉터리를
# PATH 에서 제거한 뒤 진짜 바이너리에 SendEnv 표식을 끼워 exec 한다.
_WRAPPER_SH = """#!/bin/sh
# pytmux ssh 래퍼: 원격 pytmux 중첩 거부용 표식(%(marker)s)을 SendEnv 로 전파.
cmd=$(basename "$0")
d=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
# 우리 래퍼 디렉터리를 PATH 에서 빼 진짜 바이너리를 찾는다(무한 재귀 방지).
PATH=$(printf '%%s' "$PATH" | awk -v RS=: -v d="$d" \
  '$0!=d{printf "%%s%%s",(n++?":":""),$0}')
export PATH
real=$(command -v "$cmd" 2>/dev/null)
if [ -z "$real" ]; then
  echo "pytmux: $cmd 을(를) PATH 에서 찾지 못했습니다" >&2
  exit 127
fi
exec "$real" -o SendEnv=%(marker)s "$@"
"""

# Windows .cmd 배치 래퍼 본문. 배치 파일이라 for 변수는 %%E, 변수 확장은 %_real%,
# 모든 인자는 %*. `%%~$PATH:E`(E=ssh.exe 등 .exe 만)로 진짜 바이너리를 찾아 우리
# .cmd 자기 자신을 안 잡는다(PATH 에서 자기 dir 제거 불필요). marker 를 SendEnv 로 끼워
# exec(call 후 같은 종료코드로 종료). .format() 으로 채우므로 % 는 그대로 리터럴.
_WRAPPER_CMD = """@echo off
rem pytmux ssh wrapper: propagate nesting marker ({marker}) via SendEnv.
setlocal
set "_real="
for %%E in ({exe}) do set "_real=%%~$PATH:E"
if not defined _real (
  echo pytmux: {exe} not found in PATH 1>&2
  exit /b 127
)
"%_real%" -o SendEnv={marker} %*
"""


def ensure_wrapper_dir(state_dir: str) -> str | None:
    """ssh 래퍼들을 담은 디렉터리를 (없으면) 만들고 경로를 반환한다.

    멱등: 이미 있으면 그대로 둔다. 생성 실패 시 None(표식 전파 없이 우아하게 동작).
    POSIX 는 `ssh`/`autossh` sh 래퍼(0o755), Windows 는 `ssh.cmd`/`autossh.cmd` 배치
    래퍼를 만든다(#1.4 — 패널 cmd.exe/PowerShell 이 .cmd 를 해석)."""
    wd = os.path.join(state_dir, "sshwrap")
    try:
        os.makedirs(wd, exist_ok=True)
        if os.name == "nt":
            for name in _WRAPPED:
                body = _WRAPPER_CMD.format(
                    exe=name + ".exe", marker=NEST_MARKER).encode("utf-8")
                path = os.path.join(wd, name + ".cmd")
                if _same_file(path, body):
                    continue
                with open(path, "wb") as f:
                    f.write(body)
            return wd
        body = (_WRAPPER_SH % {"marker": NEST_MARKER}).encode("utf-8")
        for name in _WRAPPED:
            path = os.path.join(wd, name)
            if _same_file(path, body):
                continue
            with open(path, "wb") as f:
                f.write(body)
            os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                     | stat.S_IROTH | stat.S_IXOTH)  # 0o755
        return wd
    except OSError:
        return None


def _same_file(path: str, body: bytes) -> bool:
    """파일 내용이 body 와 같으면 True(멱등 — 불필요한 재작성/chmod 방지)."""
    try:
        with open(path, "rb") as f:
            return f.read() == body
    except OSError:
        return False


def panel_env(env: dict, state_dir: str) -> dict:
    """패널 셸 env 에 중첩 표식 + ssh 래퍼 PATH 앞단을 주입한다(in-place + 반환).

    표식(LC_PYTMUX)은 원격으로, ssh 래퍼는 그 표식을 SendEnv 로 끼우는 다리 역할.
    래퍼 생성이 안 되는 환경(Windows/IO 실패)에서는 표식만 심는다(로컬 PYTMUX 가드는
    여전히 동작; 원격 전파만 빠짐)."""
    env[NEST_MARKER] = "1"
    wd = ensure_wrapper_dir(state_dir)
    if wd:
        old = env.get("PATH", os.environ.get("PATH", ""))
        env["PATH"] = wd + (os.pathsep + old if old else "")
    return env
