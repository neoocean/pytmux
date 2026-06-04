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


def ensure_wrapper_dir(state_dir: str) -> str | None:
    """ssh 래퍼들을 담은 디렉터리를 (없으면) 만들고 경로를 반환한다.

    POSIX 전용(Windows 는 None — cmd 셸 PATH 셰임 동작이 달라 미지원). 멱등:
    이미 있으면 그대로 둔다. 생성 실패 시 None(표식 전파 없이 우아하게 동작)."""
    if os.name == "nt":
        return None
    wd = os.path.join(state_dir, "sshwrap")
    try:
        os.makedirs(wd, exist_ok=True)
        body = (_WRAPPER_SH % {"marker": NEST_MARKER}).encode("utf-8")
        for name in _WRAPPED:
            path = os.path.join(wd, name)
            # 내용이 같으면 다시 쓰지 않는다(멱등·불필요한 chmod 방지).
            try:
                with open(path, "rb") as f:
                    if f.read() == body:
                        continue
            except OSError:
                pass
            with open(path, "wb") as f:
                f.write(body)
            os.chmod(path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
                     | stat.S_IROTH | stat.S_IXOTH)  # 0o755
        return wd
    except OSError:
        return None


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
