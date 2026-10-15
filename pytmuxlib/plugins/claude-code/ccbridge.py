"""Claude Code 의 **statusLine 입력 JSON · SessionStart 훅**을 pytmux 로 잇는 다리
(pytmux-527 C1 S1 · pytmux-529 C3-1).

# 왜 있나

종전에는 5h%·주간%·모델·ctx% 를 **화면에서 긁었다** — 숨은 `claude` 를 띄워 `/usage` 를
스크랩하는 프로브(부팅 10~25초 · 600초마다)와 footer 배지 스크랩이다. CLI 2.1.80+ 는
statusLine 명령의 stdin 으로 `rate_limits`·`context_window`·`model` 을 **패널(프로세스)마다
정확히** 준다. 그 JSON 을 받아 `<state>/cc/sl/<pane_key>.json` 에 떨궈 두면 서버가 읽는다.

같은 길로 SessionStart 훅의 `sessionTitle` 을 내면 `/rename` 을 **키로 쳐 넣지 않고**
세션 이름을 정할 수 있다(c3 §3). 이름 규칙의 정본은 claude-name-sync 플러그인이고, 이
모듈은 그 판정 함수를 **그대로 불러 쓴다**(두 벌 금지 — 시험이 함수 동일성을 단언한다).

# 어떻게 걸리나 — 사용자 설정 파일을 **안 쓴다**

statusLine 칸은 settings 에 하나뿐이라 사용자 것과 겹친다. 그래서 `~/.claude/settings.json`
을 고치지 않고(fullscreen.py 의 규율 「pytmux 가 남의 설정 파일을 쓰지 않는다」), 패널 셸
PATH 앞에 `claude` **심**을 깔아 `--settings <state>/cc/settings.json` 을 덧붙인다. 그러면
pytmux 패널에서 뜬 claude 에만 걸린다. 사용자 원래 statusLine 은 래퍼가 **실행 시점에** 찾아
같은 stdin 으로 돌리고 그 출력을 그대로 낸다 — 화면은 심이 없을 때와 같아야 한다.

⛔ **Windows 는 심을 안 깐다**(문서화된 무동작). 배치(`.cmd`)로 감싸면 cmd.exe 가 `%*` 를
다시 토큰화해 인자 속 `&`·`|`·`%VAR%` 가 **로컬에서** 실행된다(BatBadBut 부류 —
`sshwrap.ensure_wrapper_dir` 가 같은 이유로 Windows ssh 래퍼를 걷었다 · CL 81761). PATHEXT
에 기대는 `.py`/`.ps1` 실행은 기본 설정이 아니고, 재해석 없이 인자를 넘기는 런처 `.exe` 는
저장소가 갖고 있지 않다. 그래서 Windows 에서는 `pane_env` 가 아무것도 안 얹고, 서버의
읽기 쪽만 남는다(파일이 안 생기므로 종전과 같다).

# 무게

이 파일은 **훅 프로세스에서 단독으로** 돈다(`python ccbridge.py statusline …`). statusLine 은
300ms 디바운스로 자주 불리므로 최상단 import 는 표준 라이브러리만 둔다. 서버 쪽 소비는
`ccsl.py` 다(플러그인 상대 import 가 필요해서 갈라 두었다).
"""
from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import time

IS_WINDOWS = os.name == "nt"

# 패널 셸 env — 서버가 spawn 마다 무작위로 짓는다(tmux 의 TMUX_PANE 자리 · c1 §D2).
ENV_PANE_KEY = "PYTMUX_PANE_KEY"
# 사용자 탈출구: 이 값이 "0" 이면 심이 주입 없이 진짜 claude 로 바로 간다.
ENV_DISABLE = "PYTMUX_CC_BRIDGE"

# 우리 래퍼가 statusLine 명령에 들어 있다는 표식 — 사용자 statusLine 을 찾다가 **자기
# 자신**을 만나면 건너뛴다(재귀 방지). 스크립트 파일 이름이 곧 표식이다.
MARKER = "ccbridge.py"

# 심이 주입하지 않는 첫 인자(claude 하위 명령). `--settings` 를 하위 명령에 붙였을 때의
# 반응은 못 쟀다(c1 ⚠) — 모르는 자리는 손대지 않는다.
SUBCOMMANDS = ("mcp", "config", "update", "upgrade", "doctor", "install",
               "plugin", "plugins", "agents", "migrate-installer", "setup-token",
               "auth", "login", "logout")

# SessionStart 출력이 채택되는 source(이진 판독 · c3 §CLI). clear·compact 는 CLI 가 무시한다.
TITLE_SOURCES = ("startup", "resume", "fork")

# 훅 timeout(초). 이름 동기화가 표식을 기다리는 시간(claude-name-sync `_CC_TITLE_WAIT`)이
# 이 값 + 여유다.
HOOK_TIMEOUT = 5

# OSC 777 표식 머리 — `ESC ] 777 ; pytmux;cc-title;<pane_key>;<b64 kw> BEL`.
OSC_TITLE_PREFIX = "pytmux;cc-title;"

_KEY_RE = re.compile(r"[A-Za-z0-9_-]{1,80}")


def clean_key(k) -> str:
    """파일 이름으로 쓸 키를 거른다(경로 조각·점 파일 금지). 못 쓰면 빈 문자열."""
    k = str(k or "").strip()
    return k if _KEY_RE.fullmatch(k) else ""


# ---- 자리 ----
def sl_dir(cc_dir: str) -> str:
    return os.path.join(cc_dir, "sl")


def bin_dir(cc_dir: str) -> str:
    return os.path.join(cc_dir, "bin")


def settings_path(cc_dir: str) -> str:
    return os.path.join(cc_dir, "settings.json")


def namesync_path(cc_dir: str) -> str:
    return os.path.join(cc_dir, "namesync.json")


def titled_dir(cc_dir: str) -> str:
    return os.path.join(cc_dir, "titled")


def claude_config_dir(env=None) -> str:
    """claude 설정 디렉터리 — `$CLAUDE_CONFIG_DIR` 또는 `~/.claude`(settings·sessions)."""
    env = os.environ if env is None else env
    return env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def claude_json_path(env=None) -> str:
    """claude 전역 설정 `.claude.json` — `$CLAUDE_CONFIG_DIR` 또는 `~` 아래.

    ⚠ 설정 디렉터리와 **다른 규칙**이다(fullscreen.config_path 와 같은 판독)."""
    env = os.environ if env is None else env
    base = env.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~")
    return os.path.join(base, ".claude.json")


# ---- 파일 쓰기(원자 · 0600) ----
def _write_private(path: str, data: bytes) -> None:
    """tmp → rename 으로 원자적으로 쓴다(0600). 읽는 쪽이 반쯤 쓴 파일을 안 본다."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    tmp = os.path.join(d, ".%s.%d.tmp" % (os.path.basename(path), os.getpid()))
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def _same(path: str, data: bytes) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read() == data
    except OSError:
        return False


def write_if_changed(path: str, data: bytes, mode=None) -> None:
    if not _same(path, data):
        _write_private(path, data)
    if mode is not None:
        os.chmod(path, mode)


# ---- 심(POSIX sh) ----
_SHIM_SH = """#!/bin/sh
# pytmux claude 심(pytmux-527): pytmux 패널에서 뜬 claude 에만 --settings 를 얹는다.
# 사용자 설정 파일은 안 건드린다. 자기 디렉터리를 PATH 에서 빼고 진짜 claude 를 exec 한다.
d=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PATH=$(printf '%%s' "$PATH" | awk -v RS=: -v d="$d" \\
  '$0!=d{printf "%%s%%s",(n++?":":""),$0}')
export PATH
real=$(command -v claude 2>/dev/null)
if [ -z "$real" ]; then
  echo "pytmux: claude 을(를) PATH 에서 찾지 못했습니다" >&2
  exit 127
fi
settings=%(settings)s
inject=1
[ "${%(disable)s:-1}" = "0" ] && inject=0
[ -r "$settings" ] || inject=0
if [ "$inject" = 1 ]; then
  for a in "$@"; do
    case "$a" in
      --settings|--settings=*|-p|--print) inject=0; break ;;
    esac
  done
fi
if [ "$inject" = 1 ] && [ $# -gt 0 ]; then
  case "$1" in
    %(subcmds)s) inject=0 ;;
  esac
fi
if [ "$inject" = 1 ]; then
  exec "$real" --settings "$settings" "$@"
fi
exec "$real" "$@"
"""


def shim_body(cc_dir: str) -> bytes:
    return (_SHIM_SH % {
        "settings": shlex.quote(settings_path(cc_dir)),
        "disable": ENV_DISABLE,
        "subcmds": "|".join(SUBCOMMANDS),
    }).encode("utf-8")


# ---- settings(우리가 짓는 --settings 파일) ----
def hook_python() -> str:
    """훅 명령에 쓸 인터프리터. Windows 의 pythonw 는 stdout 이 없어 python 으로 바꾼다."""
    exe = sys.executable or "python3"
    base = os.path.basename(exe).lower()
    if base.startswith("pythonw"):
        alt = os.path.join(os.path.dirname(exe), base.replace("pythonw", "python", 1))
        if os.path.exists(alt):
            return alt
    return exe


def _command(sub: str, cc_dir: str, py=None) -> str:
    py = py or hook_python()
    script = os.path.abspath(__file__)
    return " ".join(shlex.quote(x) for x in (py, script, sub, "--cc", cc_dir))


def user_statusline(env=None):
    """사용자 **user** 설정의 statusLine(dict) — padding·refreshInterval 을 베끼려고 읽는다.

    이 값을 안 베끼면 시계형 사용자 statusline(refreshInterval 로 도는 것)이 멈춘다.
    프로젝트·로컬 설정은 cwd 마다 달라 여기서는 못 읽는다(래퍼가 실행 시점에 찾는다)."""
    p = os.path.join(claude_config_dir(env), "settings.json")
    try:
        with open(p, encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    sl = obj.get("statusLine") if isinstance(obj, dict) else None
    return sl if isinstance(sl, dict) else None


def build_settings(cc_dir: str, user_sl=None, py=None) -> dict:
    sl = {"type": "command", "command": _command("statusline", cc_dir, py)}
    if isinstance(user_sl, dict):
        for k in ("padding", "refreshInterval"):
            v = user_sl.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                sl[k] = v
    return {
        "statusLine": sl,
        # matcher 를 안 건다 — 문서 matcher 목록에 fork 가 없어 스크립트가 source 로 거른다.
        "hooks": {"SessionStart": [{"hooks": [{
            "type": "command",
            "command": _command("session-start", cc_dir, py),
            "timeout": HOOK_TIMEOUT}]}]},
    }


def ensure_bridge(cc_dir: str, env=None) -> "str | None":
    """심·settings 를 (없거나 다르면) 쓰고 심 디렉터리를 돌려준다. 실패·Windows 는 None.

    멱등이다 — 패널을 띄울 때마다 불려도 내용이 같으면 쓰지 않는다."""
    if IS_WINDOWS:
        return None
    try:
        body = json.dumps(build_settings(cc_dir, user_statusline(env)),
                          ensure_ascii=False, indent=1).encode("utf-8")
        write_if_changed(settings_path(cc_dir), body)
        shim = os.path.join(bin_dir(cc_dir), "claude")
        write_if_changed(shim, shim_body(cc_dir), mode=0o755)
        return bin_dir(cc_dir)
    except OSError:
        return None


def disable_bridge(cc_dir: str) -> None:
    """opt off — settings·규칙 파일을 지운다. 이미 떠 있는 셸의 PATH 에 심이 남아 있어도
    심은 settings 가 없으면 **주입 없이** 진짜 claude 로 간다(`[ -r "$settings" ]`)."""
    for p in (settings_path(cc_dir), namesync_path(cc_dir)):
        try:
            os.unlink(p)
        except OSError:
            pass


# ---- statusline 래퍼 ----
def _subset(data: dict, env) -> dict:
    """원문을 통째로 담지 않는다 — 서버가 쓰는 칸만(c1 §D1)."""
    rec = {
        "observed_at": time.time(),
        "session_id": str(data.get("session_id") or ""),
        "pane_key": clean_key(env.get(ENV_PANE_KEY)) or None,
        "config_dir": claude_config_dir(env),
        "claude_json": claude_json_path(env),
        "version": data.get("version"),
        "cwd": data.get("cwd"),
    }
    m = data.get("model")
    if isinstance(m, dict):
        rec["model"] = {"id": m.get("id"), "display_name": m.get("display_name")}
    cw = data.get("context_window")
    if isinstance(cw, dict):
        rec["context_window"] = {k: cw.get(k) for k in (
            "used_percentage", "remaining_percentage", "context_window_size")}
    rl = data.get("rate_limits")
    if isinstance(rl, dict):
        out = {}
        for k in ("five_hour", "seven_day"):
            w = rl.get(k)
            if isinstance(w, dict):
                out[k] = {"used_percentage": w.get("used_percentage"),
                          "resets_at": w.get("resets_at")}
        rec["rate_limits"] = out
    return rec


def write_sl(cc_dir: str, data: dict, env=None) -> "str | None":
    """statusline 입력 하나를 `sl/<key>.json` 에 떨군다. 키 = 패널 키, 없으면 session_id."""
    env = os.environ if env is None else env
    key = clean_key(env.get(ENV_PANE_KEY)) or clean_key(data.get("session_id"))
    if not key:
        return None
    path = os.path.join(sl_dir(cc_dir), key + ".json")
    _write_private(path, json.dumps(_subset(data, env)).encode("utf-8"))
    return path


def find_user_statusline(cwd, env=None) -> "str | None":
    """사용자 **원래** statusLine 명령을 실행 시점에 찾는다.

    순서 = `<cwd>/.claude/settings.local.json` > `<cwd>/.claude/settings.json` >
    `<config_dir>/settings.json`(우리 --settings 가 덮은 자리들의 병합 순서). 찾은 것이
    우리 래퍼 자신이면 건너뛴다."""
    cands = []
    if cwd:
        cands += [os.path.join(cwd, ".claude", "settings.local.json"),
                  os.path.join(cwd, ".claude", "settings.json")]
    cands.append(os.path.join(claude_config_dir(env), "settings.json"))
    for p in cands:
        try:
            with open(p, encoding="utf-8") as f:
                obj = json.load(f)
        except (OSError, ValueError):
            continue
        sl = obj.get("statusLine") if isinstance(obj, dict) else None
        if not isinstance(sl, dict):
            continue
        cmd = sl.get("command")
        if sl.get("type", "command") != "command" or not isinstance(cmd, str) \
                or not cmd.strip():
            continue
        if MARKER in cmd:
            continue                     # 우리 자신 — 재귀 표식
        return cmd
    return None


def run_statusline(cc_dir: str, raw: bytes, env=None, out=None) -> int:
    """statusLine 명령 본체. 파일 쓰기 실패는 삼킨다 — 사용자 statusline 출력이 우선이다."""
    env = os.environ if env is None else env
    out = out if out is not None else sys.stdout.buffer
    data = None
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        data = None
    if isinstance(data, dict):
        try:
            write_sl(cc_dir, data, env)
        except Exception:                 # noqa: BLE001 — 기록 실패가 화면을 막지 않는다
            pass
    cwd = data.get("cwd") if isinstance(data, dict) else None
    cmd = find_user_statusline(cwd, env)
    if not cmd:
        return 0                          # 원래 statusLine 없음 — 아무것도 안 찍는다
    try:
        r = subprocess.run(cmd, shell=True, input=raw, stdout=subprocess.PIPE,
                           env=dict(env), timeout=60)
    except (OSError, subprocess.SubprocessError):
        return 1
    out.write(r.stdout or b"")
    try:
        out.flush()
    except (OSError, ValueError):
        pass
    return r.returncode


def run_record(path: str, raw: bytes) -> int:
    """측정용 statusLine(c1 §측정 계획): stdin 을 `ts\\t<json>` 한 줄로 덧붙인다."""
    line = "%.3f\t%s\n" % (time.time(), raw.decode("utf-8", "replace").replace("\n", " "))
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
    return 0


# ---- SessionStart 훅(c3 §3) ----
def load_namesync():
    """claude-name-sync 플러그인 모듈을 **그대로** 불러온다(규칙 판정 한 벌).

    지워졌거나 못 읽으면 None — 훅은 아무것도 안 낸다(delete-to-disable)."""
    import importlib
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        return importlib.import_module("pytmuxlib.plugins.claude-name-sync")
    except Exception:                     # noqa: BLE001
        return None


def _read_rules(cc_dir: str):
    try:
        with open(namesync_path(cc_dir), encoding="utf-8") as f:
            obj = json.load(f)
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, list) else None


def title_osc(key: str, kw: str) -> str:
    b = base64.b64encode(kw.encode("utf-8")).decode("ascii")
    return "\x1b]777;%s%s;%s\x07" % (OSC_TITLE_PREFIX, key, b)


def session_start_output(cc_dir: str, data, env=None, ns=None) -> "dict | None":
    """SessionStart 입력 → 낼 JSON(dict) 또는 None(아무것도 안 낸다).

    1. source 가 {startup, resume, fork} 밖이면 None.
    2. `session_title` 이 이미 서 있으면 None — `--name`·`/rename`·resume 이 정한 이름을
       지킨다(`_session_named` 의 대체).
    3. 규칙 파일(서버가 내보낸 것)과 name-sync 의 `_match_keyword` 로 kw.
    4. kw 가 있으면 sessionTitle + 띠 안 표식(OSC 777)."""
    env = os.environ if env is None else env
    if not isinstance(data, dict):
        return None
    if str(data.get("source") or "") not in TITLE_SOURCES:
        return None
    if str(data.get("session_title") or "").strip():
        return None
    rules = _read_rules(cc_dir)
    if not rules:
        return None
    ns = ns if ns is not None else load_namesync()
    if ns is None:
        return None
    cwd = data.get("cwd") or os.getcwd()
    kw = ns._match_keyword(ns._sanitize_rules(rules), cwd,
                           ns._this_host(), ns._this_os())
    kw = ns._clean_name(kw) if kw else ""
    if not kw:
        return None
    out = {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                  "sessionTitle": kw}}
    key = clean_key(env.get(ENV_PANE_KEY))
    if key:
        out["terminalSequence"] = title_osc(key, kw)
        # terminalSequence 가 SessionStart 에서 실제로 pty 로 나가는지 못 쟀다(c3 ⚠) —
        # 파일 표식을 함께 남긴다. 서버가 읽고 지운다.
        try:
            _write_private(os.path.join(titled_dir(cc_dir), key + ".json"),
                           json.dumps({"kw": kw, "ts": time.time(),
                                       "session_id": data.get("session_id")}
                                      ).encode("utf-8"))
        except OSError:
            pass
    return out


def run_session_start(cc_dir: str, raw: bytes, env=None, out=None) -> int:
    out = out if out is not None else sys.stdout
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
        res = session_start_output(cc_dir, data, env)
    except Exception:                     # noqa: BLE001 — 훅 실패가 claude 기동을 막지 않는다
        return 0
    if res:
        out.write(json.dumps(res, ensure_ascii=False))
        out.flush()
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    sub = argv[0] if argv else ""
    cc_dir = ""
    if "--cc" in argv:
        i = argv.index("--cc")
        cc_dir = argv[i + 1] if i + 1 < len(argv) else ""
    try:
        raw = sys.stdin.buffer.read()
    except (OSError, ValueError):
        raw = b""
    if sub == "statusline" and cc_dir:
        return run_statusline(cc_dir, raw)
    if sub == "session-start" and cc_dir:
        return run_session_start(cc_dir, raw)
    if sub == "record":
        path = argv[argv.index("--out") + 1] if "--out" in argv[:-1] else \
            os.path.join(cc_dir or ".", "record.log")
        return run_record(path, raw)
    return 0


if __name__ == "__main__":
    # 단독 실행이면 sys.path[0] 이 이 플러그인 디렉터리다 — 그 안의 모듈 이름(tokens·
    # claude …)이 표준 라이브러리·코어를 가리지 않게 뺀다.
    _here = os.path.dirname(os.path.abspath(__file__))
    if sys.path and os.path.abspath(sys.path[0] or ".") == _here:
        sys.path.pop(0)
    sys.exit(main())
