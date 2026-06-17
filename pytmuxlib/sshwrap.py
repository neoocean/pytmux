"""원격(ssh) pytmux 중첩 거부를 위한 표식 전파 셸 래퍼(docs/internal/HANDOFF.md §10).

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
import re
import stat

# 원격에 전파할 중첩 표식 환경변수. AcceptEnv LC_* 흔한 기본값을 타도록 LC_ 접두사.
# launcher.NEST_MARKER 와 반드시 일치시킬 것.
NEST_MARKER = "LC_PYTMUX"

# ---- 원격 중첩 자동 승격 in-band DCS 와이어(docs/internal/NESTED_ATTACH_SCENARIO.md §4) ----
# launcher(REQ 발신/ACK 대기)·serverpty(스캔)·래퍼(DEST 발신)가 공유한다 — 이 모듈은
# leaf(표준 lib 만 import)라 어느 쪽에서 import 해도 순환이 없다. 페이로드는 b64
# (도메인 계정 백슬래시·공백·세미콜론 보존). 형식: DCS >|pytmux-<kind>;<b64> ST
NEST_DEST_PRE = b"\x1bP>|pytmux-ssh;"     # 래퍼→서버: 패널 ssh 목적지(argv 줄단위 b64)
NEST_REQ_PRE = b"\x1bP>|pytmux-nest;"     # 원격 launcher→서버: 승격 요청(user@host b64)
NEST_ACK = b"\x1bP>|pytmux-nest-ack\x1b\\"  # 서버→원격 launcher: 접수 회신(성공보장 아님)
DCS_ST = b"\x1b\\"
# 공통 머리(빠른 부재 판정용)와 완결 NEST DCS 정규식(serverpty 스캔·model feed 제거가
# 공유). 페이로드는 b64 클래스만 허용 — 임의 출력 오인 충돌 차단 + 선형 매칭 보장.
# pyte 는 DCS 본문을 소비하지 않고 화면으로 흘리므로, model.Pane.feed 가 이 정규식으로
# 완결분을 제거하고 미완 꼬리는 다음 feed 로 미룬다(NESTED_ATTACH §4 — 화면 오염 금지).
NEST_PRE = b"\x1bP>|pytmux-"
NEST_DCS_RE = re.compile(rb"\x1bP>\|pytmux-(ssh|nest);([A-Za-z0-9+/=]{0,8192})\x1b\\")

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
# 패널 ssh 목적지 in-band 기록(NESTED_ATTACH §4 NEST_DEST): argv 전체를 줄단위 b64
# 로 실어 DCS 로 바깥 서버에 알린다(목적지 파싱은 서버 파이썬 parse_dest — 래퍼는
# ssh 옵션 문법을 모른다). stdout 파이프/리다이렉트를 오염하지 않게 /dev/tty 로만
# 쓴다 — 실패/부재 시 조용히 생략(기록이 없으면 중첩 자동 승격만 비활성, 거부
# 폴백은 그대로). 줄단위 인코딩이라 개행 포함 인자는 못 싣는다(병적 — 무시).
if [ -w /dev/tty ]; then
  _b64=$(printf '%%s\n' "$@" | base64 2>/dev/null | tr -d '\r\n')
  if [ -n "$_b64" ]; then
    printf '\033P>|pytmux-ssh;%%s\033\\' "$_b64" > /dev/tty 2>/dev/null || :
  fi
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


# ssh(1) 에서 "다음 토큰(또는 결합)을 값으로 갖는" 단일 글자 옵션들. autossh 는
# -M <port> 가 추가로 값을 갖는다(ssh 의 -M 은 무인자 master 모드라 충돌 — basename
# 으로 구분). 목적지 추출에만 쓰므로 미지 옵션이 늘어도 최악은 "추출 실패 → 자동
# 승격 비활성"(안전한 열화)이다.
_SSH_VALUE_OPTS = frozenset("BbcDEeFIiJLlmOopQRSWw")


def parse_dest(argv: list) -> str:
    """ssh/autossh argv 에서 목적지(destination) 토큰을 추출한다(NESTED_ATTACH §4).

    규칙: 첫 **비옵션** 인자 = 목적지 — user@host·config 별칭·URI 를 **사용자가 친
    문자열 그대로**(remote_attach 가 같은 별칭/ControlMaster 레시피를 타도록). 값을
    갖는 옵션은 분리형(`-p 2222`)·결합형(`-p2222`, `-oKey=v`) 모두 건너뛰고, `--`
    뒤 첫 토큰은 무조건 목적지. 목적지 뒤(원격 명령)는 보지 않는다. 없으면 ""."""
    if not argv:
        return ""
    value_opts = set(_SSH_VALUE_OPTS)
    if os.path.basename(str(argv[0] or "")).startswith("autossh"):
        value_opts.add("M")
    i = 1
    while i < len(argv):
        a = str(argv[i])
        if a == "--":
            return str(argv[i + 1]) if i + 1 < len(argv) else ""
        if a.startswith("-") and len(a) > 1:
            # 묶음 플래그(-4A 등) 안에서 값 옵션을 만나면: 뒤에 글자가 남아 있으면
            # 결합값(-p2222 — 같은 토큰에서 소비), 마지막 글자면 다음 토큰이 값.
            for j, ch in enumerate(a[1:]):
                if ch in value_opts:
                    if j == len(a) - 2:
                        i += 1
                    break
            i += 1
            continue
        return a
    return ""


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
