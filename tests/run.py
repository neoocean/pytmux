#!/usr/bin/env python3
"""pytmux 헤드리스 테스트 러너.

사용법: python3 tests/run.py [test_module ...]
test_*.py 안의 'test_' 로 시작하는 async 함수를 각각 새 asyncio 루프에서 실행하고
PASS/FAIL 을 집계한다. 화면(TUI) 없이 전체 동작을 검증한다.
"""
import asyncio
import faulthandler
import importlib
import inspect
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback

# 스위트 위생 헬퍼(같은 디렉터리). os·tempfile 밖의 것을 안 물어서 아래 startup
# 백스톱이 무장하기 전에 import 해도 매달릴 자리가 없다.
import hermetic
import workspace_guard

faulthandler.enable()   # 세그폴트/치명 신호 시 전 스레드 트레이스백 덤프

# CI 견고성(2026-06-07). ① Windows 콘솔 기본 인코딩(cp1252)이 한글 실패 메시지를 못
# 찍어 러너가 UnicodeEncodeError 로 죽던 것을 막는다 → UTF-8 강제(+backslashreplace).
# ② 줄 버퍼링으로 진행이 CI 로그에 즉시 보이게 한다(파이프 출력은 기본 블록 버퍼라,
# 한 테스트가 매달리면 그때까지의 PASS 도 안 보여 "통째로 멈춘" 것처럼 보였다).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace",
                            line_buffering=True)
    except (AttributeError, ValueError):
        pass

# Windows 패널 spawn 의 콘솔 CP UTF-8 강제(chcp helper — conpty.force_utf8_codepage)를
# 스위트에서는 끈다. 테스트는 콘솔 코드페이지와 무관하고(한글 왕복 검증은
# scripts/validate_conpty.py 라이브 하네스가 자체 env 로 수행), helper 는 실제 셸
# spawn 마다 cmd.exe 생성+대기(수백 ms, 콜드 1.5s)를 얹어 ① Windows CI 스위트가
# 8분 스텝 타임아웃을 넘기고 ② 실제 패널 출력(cmd 배너)의 도착 시점이 테스트 본문
# 안으로 밀려 프레임 push 가 주입 콘텐츠를 덮는 타이밍 실패 4건을 만들었다
# (2026-07-10, 07-09 chcp 도입 직후부터 windows 3잡 전멸 — 상세는 p4 CL 참조).
# setdefault 라 외부에서 명시 설정(예: 코드페이지 자체를 검증하는 수동 실행)이 우선.
os.environ.setdefault("PYTMUX_KEEP_CODEPAGE", "1")

# 한 테스트가 매달리면(예: CI macOS 러너에서 PTY/서브프로세스 데드락) 스위트 전체가
# 멈추지 않게 테스트별 타임아웃을 건다 → 행(hang)을 그 테스트의 TIMEOUT 실패로 바꿔
# 빠르게·이름과 함께 드러낸다. 로컬은 테스트당 수초 이내라 90초면 오검출 없고, 47분씩
# 매달리던 진짜 행은 잡힌다. PYTMUX_TEST_TIMEOUT 으로 조정(0=무제한).
TEST_TIMEOUT = float(os.environ.get("PYTMUX_TEST_TIMEOUT", "90"))

# 간헐 실패(주로 느린 CI 러너에서 Textual run_test 클라 테스트의 타이밍 — fixed
# pilot.pause 가 짧아 렌더가 아직 안 됨) 재시도. 재시도로 통과한 건 FLAKY 로 표시해
# 가시성 유지. 0=재시도 끔.
# ⛔ 여기엔 오래 *"진짜 실패는 모든 시도에서 실패해 그대로 잡힌다"* 고 적혀 있었는데
#    **그것이 참이 아니다**(pytmux-430) — 시도들이 한 프로세스를 공유해 서로 독립이
#    아니다. 그래서 아래 «재시도 재판정» 이 붙었다.
TEST_RETRIES = int(os.environ.get("PYTMUX_TEST_RETRIES", "2"))

# 타임아웃(행)도 **1회는** 재시도한다(2026-07-10, 로드맵 test-infra). 종전엔 "행을 또
# 기다리는 건 낭비"로 아예 안 했으나, 무거운 2-서버 E2E(federation)가 전체 스위트 부하
# 에서 간헐 90s 스톨(격리 실행·CI 는 green)하던 것이 run.py 를 항상 빨갛게 만들었다 —
# 부하 스톨은 대개 transient 라 1회 재시도로 복구되고, 진짜 데드락은 재시도에서도 다시
# hang 해 +1회(유한 90s) 비용 뒤 실패로 확정된다(무한 재시도 아님). 0=타임아웃 재시도 끔.
TEST_TIMEOUT_RETRIES = int(os.environ.get("PYTMUX_TEST_TIMEOUT_RETRIES", "1"))

# ── 재시도 재판정(pytmux-430) ────────────────────────────────────────────────
# 위 재시도의 주석은 오래 "진짜 실패는 모든 시도에서 실패해 그대로 잡힌다"고 적었는데
# **그것이 참이 아니다.** 이 스위트는 전 모듈이 한 프로세스에서 돌므로(§전체=적색인데
# 격리=녹색이 같은 뿌리다) 시도들이 서로 **독립이 아니다** — 앞 시도가 프로세스 전역
# (캐시·레지스트리·모듈 전역)을 데우면 뒤 시도의 조건이 달라져 **결정론적 실패가 초록으로
# 덮인다**. 실측(2026-09-01): 상한의 26배로 두 번 넘어진 시험이 `0 failed` 로 회계됐다 —
# textual `Strip.blank` 의 클래스 수준 `lru_cache` 가 시도 1·2 에서 포화해, 시도 3 은
# 「늘 것」을 못 찾았다(같은 코드·같은 시험·다른 답).
# ⇒ 그래서 재시도로 통과한 건(FLAKY)은 **깨끗한 새 프로세스에서 한 번 더** 재서 판정한다.
#   거기서도 실패하면 그것은 플레이크가 아니라 실패다. 비용은 flaky 경로에만 든다.
# ⚠ 재판정은 그 시험 **하나만** 돌린다. 형제 시험이 깔아 준 상태에 기대는 시험이라면
#   여기서 실패할 수 있는데 — 그것도 이 저장소가 「모듈 간 오염」이라 부르는 결함이다.
#   그 판정을 못 믿을 자리에서만 PYTMUX_TEST_ADJUDICATE=off.
_ADJUDICATE = os.environ.get("PYTMUX_TEST_ADJUDICATE", "on") != "off"
_ADJUDICATING = os.environ.get("PYTMUX_TEST_ADJUDICATING") == "1"


def _adjudicate(label):
    """FLAKY 한 건을 깨끗한 프로세스에서 다시 판정한다.

    True=거기서도 통과(진짜 일시적 플레이크) · False=거기서는 실패(재시도가 덮은
    결정론적 실패) · None=판정 못 함. **None 을 통과로 접지 않는다** — 화면에 남긴다.
    """
    env = dict(os.environ)
    env["PYTMUX_TEST_ADJUDICATING"] = "1"    # 재귀 금지
    env["PYTMUX_TEST_RETRIES"] = "0"         # 또 데우면 재판정의 뜻이 없다
    env["PYTMUX_TEST_REPORT"] = "off"        # 부모 리포트를 덮지 않는다
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__), label],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env,
                           timeout=(TEST_TIMEOUT or 90) * 2 + 60)
    except Exception as e:                   # spawn 실패·타임아웃 = 판정 못 함
        return None, f"재판정을 못 돌렸다: {e}"
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+) passed, (\d+) failed", out)
    if not m:
        return None, "재판정 출력에 요약줄이 없다(절단)"
    n_pass, n_fail = int(m.group(1)), int(m.group(2))
    if n_fail:
        return False, out
    if n_pass:
        return True, out
    return None, "재판정이 그 이름으로 아무것도 안 돌렸다(0 passed, 0 failed)"


# SIGALRM 하드 백스톱(POSIX). asyncio.wait_for 는 await 지점에서만 취소할 수 있어,
# 테스트가 **동기 블로킹 콜**(PTY os.read·서브프로세스 wait·소켓 recv)에서 매달리면
# 이벤트 루프로 제어가 안 돌아와 못 끊는다 — CI macOS 러너에서 스위트가 첫 출력도
# 없이 47분 매달리던 정확한 증상. SIGALRM 은 블로킹 시스템콜도 인터럽트해 예외를
# 띄우므로, 이 경우에도 그 테스트의 TIMEOUT 실패로 바꿔 빠르게·이름과 함께 드러낸다.
# (Windows 엔 SIGALRM 이 없지만 행은 macOS/Linux 케이스라 무방.)
_HAS_ALARM = hasattr(signal, "SIGALRM")


def _alarm_handler(signum, frame):
    raise TimeoutError(f"{TEST_TIMEOUT}s 초과 — hang(SIGALRM, 동기 블로킹 의심)")


def _arm():
    """타임아웃 백스톱 2단을 건다(import·테스트 양쪽을 감싼다). 어디서 매달려도 run.py
    가 스스로 끝나 CI step 이 완료(로그 보존)되고 행 지점이 보인다.

    ① SIGALRM(POSIX, +2초): await/인터럽트 가능한 블로킹을 그 테스트의 TIMEOUT 실패로
       바꿔 스위트를 **계속** 진행. ② faulthandler(+15초, exit=True): SIGALRM 으로도 안
       끊기는 행(과거 macOS CI 미스터리)에서 **전 스레드 트레이스백을 stderr 에 덤프하고
       프로세스를 종료** — 행의 정확한 코드 위치가 로그에 남는다(자체 스레드라 메인이
       블록돼도 동작; 크로스플랫폼)."""
    if _HAS_ALARM and TEST_TIMEOUT > 0:
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, TEST_TIMEOUT + 2)
    if TEST_TIMEOUT > 0:
        faulthandler.dump_traceback_later(TEST_TIMEOUT + 15, exit=True)


def _disarm():
    if _HAS_ALARM and TEST_TIMEOUT > 0:
        signal.setitimer(signal.ITIMER_REAL, 0)
    if TEST_TIMEOUT > 0:
        faulthandler.cancel_dump_traceback_later()


# ── 치명 시그널 = 조용한 죽음 금지 ──────────────────────────────────────────
# 러너가 **출력도 트레이스백도 없이** 사라지는 일이 있다(2026-07-26 추적: 재현
# 조건은 못 잡았고, 통제 반복 10회는 전부 완주했다). 원인을 못 박는 것과 별개로,
# 죽을 때 **무엇이 돌고 있었는지**만은 반드시 남겨야 다음 번에 진단이 된다.
# SIGTERM/SIGHUP 을 가로채 현재 테스트를 리포트·stderr 에 적고 곧바로 기본 동작으로
# 되돌려 재전달한다(종료 의미론 보존 — 삼키지 않는다).
# SIGPIPE 는 **절대 건드리지 않는다**: 파이썬 기본이 SIG_IGN 이라 write 가
# BrokenPipeError 로 올라오는데, 여기에 핸들러를 달면 그게 치명 신호로 바뀌어
# 없던 죽음을 만든다(추적 중 실제로 자초했다).
_CURRENT = {"label": None}
_REPORTER = None


def _fatal_signal(signum, frame):
    label = _CURRENT.get("label")
    name = signal.Signals(signum).name
    msg = f"러너가 {name} 로 종료됨 (진행중 테스트: {label})"
    try:
        print(f"\n  ☠ {msg}", file=sys.stderr, flush=True)
        if _REPORTER is not None:
            _REPORTER.emit("fatal_signal", signal=name, label=label)
    finally:
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)       # 기본 동작으로 재전달


def _install_fatal_signal_logger():
    for nm in ("SIGTERM", "SIGHUP", "SIGQUIT"):
        sig = getattr(signal, nm, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _fatal_signal)
        except (ValueError, OSError):
            pass                            # 메인 스레드가 아니거나 미지원 — 무해


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

# run.py 는 `__main__` 으로 실행되지만 테스트는 `from run import skip` 로 SkipTest 를
# 참조한다 — 자기 자신을 'run' 으로도 등록해 **같은 클래스 객체**가 되게 한다. 안 하면
# __main__.SkipTest ≠ run.SkipTest(이중 import 정체성)라 except 가 스킵을 못 잡아
# 스킵이 실패로 집계된다(로드맵 SKIP 회계).
sys.modules.setdefault("run", sys.modules[__name__])

# 모듈 로드(아래 플러그인 별칭 import)도 백스톱으로 감싼다. 과거 macOS CI 에서 스위트가
# **첫 출력(`:: import …`)도 없이** 17분 매달리던 지점이 바로 이 top-level import 단계였다
# — _arm 은 main() 의 테스트 루프 안에서만 걸려 여기는 무방비였다(faulthandler 타이머
# 미무장). 여기서 일찍 무장하면 import 가 매달려도 전 스레드 트레이스백을 stderr 에 덤프
# 하고 프로세스를 종료해 **행 지점이 CI 로그에 남는다**(17분 침묵 → 빠른-실패+진단).
# main() 진입 시 cancel 하고 per-test _arm 으로 넘긴다(단일 타이머라 교체도 가능).
_STARTUP_TIMEOUT = max(60.0, TEST_TIMEOUT) if TEST_TIMEOUT > 0 else 0
if _STARTUP_TIMEOUT > 0:
    faulthandler.dump_traceback_later(_STARTUP_TIMEOUT, exit=True)

# 스위트 위생(hermetic). 개발자 셸에 PYTMUX_HOME 이 export 돼 있으면(§10-E #1 opt-in 단일
# 디렉토리) 경로/상태 기본값을 가정하는 테스트들이 임시 디렉토리 대신 그 실제 ~/.pytmux 의
# 영속 opts.json·tokens DB·state 를 읽어 **머신마다 다른 거짓 실패**가 난다(예: 구 스키마
# opts 의 auto_hardstop=true, 낡은 plugin_opts → 기본값/속성 단언 깨짐). CI 는 이 env 가
# 없어 초록이라 더 헷갈린다. 여기서 미리 거둬 셸 상태와 무관하게 만든다 — PYTMUX_HOME 이
# 필요한 test_pytmux_home 은 자체 _Env 로 매 테스트 설정/해제하므로 영향 없다.
os.environ.pop("PYTMUX_HOME", None)

# ⛔ **그 pop 이 열어 놓은 길을 여기서 막는다**(2026-08-10 · pytmux/pytmux-135). 설정
# 파일 탐색 차례는 `$PYTMUX_CONFIG` → `$PYTMUX_HOME/config` →
# `$XDG_CONFIG_HOME/pytmux/config` → `~/.pytmux.conf` 라(`pytmuxlib/keymap.py`),
# PYTMUX_HOME 을 **거둔 결과** 두 번째 자리가 사라져 곧장 **세 번째 = 사용자의 진짜
# 설정 파일**로 떨어졌다 — 위생을 지키려던 위의 한 줄이 오히려 진짜 파일로 가는 길을
# 열었다. 그래서 설정만은 **거두지 말고 세운다**(거두면 또 떨어진다). 왜 이것이
# 읽기·쓰기 양쪽 문제인지와 Rust 쪽 선례는 `tests/hermetic.py` 머리말.
HERMETIC_CONFIG = hermetic.isolate_config()

# 같은 위생 축(2026-08-05): 셸의 `NO_COLOR` 도 여기서 거둔다. Claude Code 툴 환경은
# `NO_COLOR=1` 을 심는데, 그러면 Textual 이 Monochrome 필터를 물어 **색을 단언하는
# 테스트가 내 변경과 무관하게 떨어진다**(실측: test_notice_history_cursor_leaves_no_color_trail
# — "커서 줄은 등급색으로 칠해져야"). `scripts/check_all.py` 는 이미 자식 env 에서 이것을
# 지우고 있었지만(그 파일 §main), `tests/run.py` 를 **직접** 부르면 그 보호 밖이었다 —
# CLAUDE.md 가 "먼저 unset 하라"고 사람에게 시키던 자리다. 사람이 기억해서 치는 위생은
# 언젠가 빠지고, 이제는 그 거짓 실패가 `scripts/tracker_tests.py` 를 타고 **이슈트래커에
# 가짜 결함으로 등록**되므로 값이 더 커졌다(pytmux/pytmux-132).
# ⚠ 안전판 자체를 재는 test_no_color_guard 는 **제 손으로** NO_COLOR 를 세우고 되돌리므로
#   영향 없다(이 pop 은 셸에서 물려받은 것만 거둔다).
os.environ.pop("NO_COLOR", None)

# 같은 위생 축(2026-07-31): 코드 버전 조회를 **서브프로세스 없이** 고정한다. 서버·클라는
# 기동 때마다 `version.code_version()`(p4 `#have` → git → unknown)을 부르는데, p4/git 이
# 느리거나 불통인 워크스테이션에선 호출당 4.5~5.2초(둘 다 1.5s 타임아웃 + 프로세스 생성)를
# 태운다. Windows 에선 그 프로세스 생성이 **이벤트 루프를 ~0.6초 정체**시켜, 살아 있는 서버
# 로의 루프백 connect 가 `ipc._LOOPBACK_CONNECT_TIMEOUT`(0.5s)에 걸려 **거짓 TIMEOUT** 이
# 난다(실측: test_server 연결 테스트 10건이 이유 없이 hang 으로 보였다). 값 자체를 보는
# 테스트는 version 모듈을 직접 부르므로 이 고정값에 의존하지 않는다.
os.environ.setdefault("PYTMUX_CODE_VERSION", "test")

# 같은 축(2026-07-31): 루프백 connect 캡을 러너에서만 넉넉히 준다. 스위트는 서버와 클라를
# **한 이벤트 루프**에 넣으므로, 서버가 startup 중에 하는 동기 작업(세션 생성=ConPTY 스폰,
# 상태디렉터리 ACL 조이기=icacls 스폰)이 0.4~0.9초 루프를 멎게 하고, 그 창에 걸린
# `ipc.open_connection` 이 커널 handshake(<1ms)와 무관하게 기본 캡 0.5s 로 **거짓
# TimeoutError** 를 낸다(실측: 살아 있는 서버로의 첫 connect 0.608s, 두 번째부터 0.001s →
# test_server 연결 테스트가 원인 없는 hang 으로 보였다). 프로덕션 기본값(0.5)은 무변경 —
# 죽은 포트 빠른 실패를 검증하는 test_ipc 는 자기 블록에서 이 override 를 걷어낸다.
os.environ.setdefault("PYTMUX_LOOPBACK_CONNECT_TIMEOUT", "5")

# S5c/T5: claude/tokens/usageprobe/usagedb/usagelog 는 plugins/claude-code/ 로 물리
# 이전됐다(코어는 더는 이들을 import 하지 않는다). 기존 테스트가 `from pytmuxlib.claude
# import …`·`from pytmuxlib import tokens, usagedb, usagelog` 로 계속 import 할 수 있게,
# 플러그인 서브모듈을 pytmuxlib.<name> 별칭으로 sys.modules·패키지 속성에 등록한다(테스트
# 편의 — 코어 코드는 이 경로를 쓰지 않는다). 하이픈 디렉토리라 import 문법으론 못 부르므로
# importlib 로 로드. 플러그인이 없으면(delete-to-disable) 조용히 건너뛴다 — 해당 모듈
# 테스트는 어차피 대상 부재다. (usagedb 는 `from . import usagelog` 라 자동 동반 로드되나,
# 명시 등록으로 import 순서 무관하게 둘 다 별칭이 잡히게 한다.)
try:
    import pytmuxlib as _pt
    for _m in ("claude", "tokens", "usageprobe", "usagelog", "usagedb",
               "transcript", "syncrypto", "tokensync"):
        # per-module 격리: usageprobe 는 POSIX 전용(pty/termios)이라 Windows 에서
        # import 가 실패한다. 한 try 로 묶으면 그 실패가 뒤따르는 usagelog/usagedb
        # (Windows 호환) alias 까지 막아 `from pytmuxlib import usagedb` 가 깨진다.
        try:
            _mod = importlib.import_module(f"pytmuxlib.plugins.claude-code.{_m}")
        except Exception:
            continue
        sys.modules[f"pytmuxlib.{_m}"] = _mod
        setattr(_pt, _m, _mod)
except Exception:
    pass


def _call_args(fn):
    """그 테스트가 받을 인자. 지금 채워 주는 것은 `tmp_path` 하나다(pytest 관례) —
    그 이름을 선언한 테스트에는 **비어 있는 임시 디렉터리**를 준다. 안 채워 주면
    동기 테스트가 `missing 1 required positional argument` 로 죽고, 그것이 이 파일들이
    여태 안 돌던(=코루틴만 거두던) 자리와 겹쳐 있었다."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return {}
    if "tmp_path" not in params:
        return {}
    import pathlib as _pl
    import tempfile as _tf
    return {"tmp_path": _pl.Path(_tf.mkdtemp(prefix="pytmux-test-"))}


class SuiteTimeout(Exception):
    """러너가 **자기 상한**에서 끊었다 — 시험이 스스로 낸 `TimeoutError` 와 다르다.

    둘을 같은 것으로 세면 회계가 거짓말을 한다(pytmux-452): 지속시간이 틀리고,
    실패가 **타임아웃 재시도** 경로로 새고, 진짜 원인의 트레이스백이 덮인다.
    """


async def _run_with_timeout(fn):
    """테스트 하나를 (타임아웃과 함께) 돌린다. **동기 함수도 받는다** — 그 경우
    코루틴이 아니라 그냥 부른다(스레드로 넘기지 않는 이유: 이 스위트의 동기 테스트는
    순수 계산이고, 스레드로 옮기면 `harness.patched` 같은 프로세스 전역 조작이 다른
    테스트와 겹친다)."""
    kw = _call_args(fn)
    if not asyncio.iscoroutinefunction(fn):
        fn(**kw)
        return
    if TEST_TIMEOUT > 0:
        # ⛔ `asyncio.wait_for` 만으로는 **누가 시간을 잰 것인지** 못 가른다 — 시험이
        #    스스로 낸 `TimeoutError`(이 스위트의 `_read_until` 이 그런다)도 그대로
        #    올라와 러너가 그것을 「자기 상한에 걸린 행」으로 읽었다. 그래서 4초짜리
        #    단언 실패가 「90.0s 초과 — hang(데드락 의심)」으로 적히고 **트레이스백이
        #    통째로 덮였다**(pytmux-452). 마감이 실제로 지났는지는 `expired()` 가 안다.
        cm = asyncio.timeout(TEST_TIMEOUT)
        try:
            async with cm:
                await fn(**kw)
        except TimeoutError:
            if cm.expired():
                raise SuiteTimeout(f"{TEST_TIMEOUT}s 초과 — hang(데드락 의심)") from None
            raise          # 시험이 스스로 낸 것 — 평범한 실패로 올린다(트레이스백 보존)
    else:
        await fn(**kw)


# ── 모듈 경계 전역 누출 가드 ────────────────────────────────────────────────
# run.py 는 **전 모듈을 한 프로세스**에서 돌린다. 테스트가 `mod.func = lambda …` 로
# 프로덕션 모듈 전역을 덮고 안 되돌리면 그 치환이 뒤따르는 모든 테스트 모듈에 남는다.
# 실측(2026-07-26): test_claude_resume_transparency/-verify 가 servermixin 의
# screen_text/claude_state 를 영구 치환해 test_server(56)·test_token_saver(5)·
# test_transcript_wiring(5) **66건**이 한꺼번에 깨졌다. 모듈별 격리 실행은 초록이라
# "기존 결함"으로 오해되기 쉬웠다 — 전체=적색/격리=녹색이면 결함이 아니라 **오염**이다.
# 그래서 모듈 경계에서 pytmuxlib.* 전역을 스냅샷 대비 되돌리고 누출을 이름으로 보고한다
# (테스트가 스스로 되돌리는 게 원칙이고 이건 2차 방어 + 가시화). 끄기 = 아래 env.
_LEAK_GUARD = os.environ.get("PYTMUX_TEST_LEAK_GUARD", "on") != "off"
_MISSING = object()


def _snapshot_globals(base):
    """아직 안 잡힌 `pytmuxlib.*` 모듈의 전역을 얕게 스냅샷한다(베이스라인 확장).

    모듈이 처음 등장한 시점의 값을 그 모듈의 pristine 으로 삼는다. 한 테스트 모듈이
    어떤 프로덕션 모듈을 **처음 import 하면서 동시에 치환**하면 그 1회는 못 잡지만
    (그 값이 베이스라인이 된다), 그 다음 모듈부터는 잡힌다."""
    for name, mod in list(sys.modules.items()):
        if name in base or not name.startswith("pytmuxlib"):
            continue
        d = getattr(mod, "__dict__", None)
        if d is not None:
            base[name] = dict(d)


def _restore_globals(base):
    """스냅샷과 **정체성이 달라진 함수/클래스 전역**만 되돌리고 이름 목록을 반환한다.

    두 가지를 일부러 건드리지 않는다 — 둘 다 실측으로 배운 오탐이다:
      · **제자리 변경**(캐시 dict 에 키 추가 등): 정체성이 그대로라 애초에 안 걸린다.
      · **지연 초기화 싱글턴**(`_REGISTRY = None` → 첫 사용 때 객체 대입,
        `cellwidth._orig_cell_len = None` → 최초 패치 때 원본 보관): 스냅샷 값이
        None 이라 되돌리면 정상 초기화를 무효로 만든다. 실제로 이걸 되돌렸다가
        test_client·test_compose_prompt 19건이 `'NoneType' object is not callable`
        로 깨졌다(2026-07-26). 그래서 **원래 값이 호출 가능(함수/클래스)일 때만**
        되돌린다 — 잡으려는 건 `mod.f = 가짜함수` 형태의 몽키패치이고, 그 경우는
        원래 값이 언제나 함수다."""
    leaked = []
    for name, snap in base.items():
        d = getattr(sys.modules.get(name), "__dict__", None)
        if d is None:
            continue
        for k, v in snap.items():
            if k.startswith("__") or not callable(v):
                continue
            if d.get(k, _MISSING) is not v:
                leaked.append(f"{name}.{k}")
                d[k] = v
    return leaked


def discover(names):
    mods = []
    for fn in sorted(os.listdir(HERE)):
        if fn.startswith("test_") and fn.endswith(".py"):
            mod = fn[:-3]
            if not names or mod in names:
                mods.append(mod)
    return mods


# ── 명시 SKIP 회계(로드맵 test-infra) ────────────────────────────────────────
# 종전엔 플랫폼 부적합 테스트가 `if os.name=="nt": return` 로 **조용히** 빠져나가
# PASS 로 집계돼, 이 박스에서 실제로 몇 개가 안 도는지 안 보였다(커버리지 갭 은폐).
# `skip("사유")` 를 raise 하면 run.py 가 passed/failed 와 **별개**로 세고 사유를
# 요약에 리포트한다. 점진 채택(wait_until 규약과 동일 — 신규/수정 테스트부터):
#   from run import skip
#   if os.name == "nt": skip("POSIX 전용(pty/termios)")
class SkipTest(Exception):
    """테스트를 건너뛴다는 신호 — passed/failed 아닌 skipped 로 회계·리포트."""


def skip(reason: str = ""):
    raise SkipTest(reason)


# ── durable per-run 리포트(로드맵 test-infra ⑦ 잔여) ──────────────────────────
# 회계 요약(`N passed, M failed, K skipped`)은 **맨 끝에 한 번** 찍힌다. 그래서 러너가
# 중간에 죽으면(머신 부하로 절단 — load 12~17 에서 실측 · CI step 타임아웃 ·
# faulthandler exit) 그때까지의 결과가 통째로 사라져 "무엇이 돌았고 무엇이 안 돌았나"
# 를 재실행 없이는 알 수 없었다(2026-07-25·-25b 세션에서 반복해 물림).
# 처방: 결과가 나오는 **즉시 한 줄씩 append + flush** 한다(kill -9 에도 이미 쓴 줄은
# 남는다 — 버퍼링을 켜면 이 기능의 의미가 사라진다). 그리고 `run.py --report [경로]` 가
# 그 파일만으로 회계를 복원하고, summary 줄이 없으면 **절단된 run** 임을 명시한다
# (마지막으로 import 한 모듈 = 죽을 때 물려 있던 모듈).
# 경로 = `PYTMUX_TEST_REPORT`(`off`/`0`/빈값 = 끔), 기본 `<repo>/reports/testrun.jsonl`
# — `reports/` 는 git·p4 양쪽 ignore 라 산출물이 게시에 딸려가지 않는다.
_DEFAULT_REPORT = os.path.join(os.path.dirname(HERE), "reports", "testrun.jsonl")


def _report_path():
    raw = os.environ.get("PYTMUX_TEST_REPORT")
    if raw is None:
        return _DEFAULT_REPORT
    return "" if raw.strip().lower() in ("", "0", "off", "no") else raw


class Reporter:
    """테스트 결과를 JSONL 로 즉시 적재한다(비활성이면 전부 no-op)."""

    def __init__(self, path):
        self.path, self.fp = path, None
        if not path:
            return
        try:
            parent = os.path.dirname(os.path.abspath(path))
            os.makedirs(parent, exist_ok=True)
            self.fp = open(path, "w", encoding="utf-8")   # 새 run = 새 파일
        except OSError as e:      # 읽기전용 CI 워크스페이스 등 — 리포트는 부가기능이라
            print(f"  (리포트 비활성: {e})")               # 스위트를 죽이지 않는다
            self.fp = None

    def emit(self, kind, **kw):
        if not self.fp:
            return
        try:
            self.fp.write(json.dumps(dict(kind=kind, **kw), ensure_ascii=False) + "\n")
            self.fp.flush()       # 절단 내성의 전부 — 버퍼에 남은 줄은 kill 에서 유실
        except (OSError, TypeError, ValueError):
            pass

    def close(self):
        if self.fp:
            try:
                self.fp.close()
            except OSError:
                pass
            self.fp = None


def report_summary(path, out=print):
    """리포트 파일만으로 회계를 복원한다(러너가 죽어 요약을 못 찍은 run 용).

    summary 줄이 있으면 그 run 은 **완주**했다는 뜻이고, 없으면 절단된 run 이므로
    그 사실과 **죽을 때 물려 있던 모듈**을 보고한다 — 이 기능의 핵심 가치다.
    반환 = 0(완주) / 1(실패 있음 또는 절단)."""
    if not path or not os.path.exists(path):
        out(f"리포트 없음: {path or '(비활성)'}")
        return 1
    counts, skips, fails, last_import, summary = {}, [], [], None, None
    last_begin = done = fatal = None
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue          # 절단된 마지막 줄(부분 write) — 무시하고 나머지 회계
            kind = rec.get("kind")
            if kind == "import":
                last_import = rec.get("module")
            elif kind == "begin":
                last_begin = rec.get("label")
            elif kind == "fatal_signal":
                fatal = rec
            elif kind == "result":
                done = rec.get("label")
                st = rec.get("status", "?")
                counts[st] = counts.get(st, 0) + 1
                if st == "skip":
                    skips.append(rec.get("reason") or "(사유 없음)")
                elif st in ("fail", "timeout"):
                    fails.append(f"{rec.get('label')}: {rec.get('reason', '')}")
            elif kind == "summary":
                summary = rec
    order = ("pass", "flaky", "fail", "timeout", "skip")
    parts = [f"{st}={counts[st]}" for st in order if st in counts]
    parts += [f"{st}={n}" for st, n in sorted(counts.items()) if st not in order]
    out(f"리포트: {path}")
    out("  결과: " + (", ".join(parts) or "(기록 없음)"))
    if skips:
        from collections import Counter
        for reason, cnt in Counter(skips).most_common():
            out(f"  skip {cnt:3d}  {reason}")
    for f in fails:
        out(f"  FAIL {f}")
    if summary is None:
        # 사망 지점 = **끝나지 않은 begin**(begin 은 있는데 같은 label 의 result 가
        # 없는 것). 종전엔 모듈 이름까지만 알려줘 60개를 되짚어 세야 했다.
        inflight = last_begin if last_begin and last_begin != done else None
        out(f"  ⚠ 절단된 run — 요약 줄이 없다(마지막 import: {last_import or '?'}). "
            "러너가 죽었다: 머신 부하·CI 타임아웃·faulthandler exit 을 의심하고 "
            "그 모듈만 재실행할 것.")
        if inflight:
            out(f"  ☠ 죽을 때 물려 있던 테스트: {inflight}")
        elif done:
            out(f"  · 마지막으로 끝난 테스트: {done} (그 다음 것에서 죽었다)")
        if fatal:
            out(f"  ☠ 치명 시그널 {fatal.get('signal')} 수신 "
                f"(진행중: {fatal.get('label')})")
        return 1
    out(f"  완주: {summary.get('passed')} passed, {summary.get('failed')} failed, "
        f"{summary.get('skipped')} skipped, {summary.get('flaky')} flaky")
    return 1 if summary.get("failed") else 0


def main(argv):
    if argv and argv[0] in ("--report", "-r"):
        return report_summary(argv[1] if len(argv) > 1 else _report_path())
    # ☠ **시험을 돌리기 전에** 워크스페이스가 depot 과 몰래 어긋나 있는지 먼저 잰다
    # (pytmux/pytmux-227) — 안 그러면 닷새를 depot 이 아닌 코드로 초록불을 켤 수 있다.
    # rc=2 로 구분한다(1=시험 실패, 2=애초에 잴 수 없었다) — 요약줄을 안 찍으므로
    # `check_all.py` 의 `python_suite_verdict` 가 "절단됐다"로 옳게 잡는다.
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _suspects = workspace_guard.find_suspects(_root)
    if workspace_guard.LAST_SKIP:
        # ⛔ **못 잰 것을 조용히 지나지 않는다**(검수 2026-09-05 C-7) — 한 줄이 없으면
        # 「가드가 봤고 괜찮더라」와 「가드가 아예 안 돌았다」가 화면에서 같아 보인다.
        print(f"… depot 드리프트 가드 건너뜀: {workspace_guard.LAST_SKIP}",
              file=sys.stderr)
    if _suspects:
        print("=" * 50, file=sys.stderr)
        print("✗ 워크스페이스 바이트가 depot 과 어긋나 있다 — 시험을 안 돌린다"
              " (pytmux/pytmux-227 패턴: git HEAD 와는 같은데 depot 과 다르다)",
              file=sys.stderr)
        for f in _suspects[:20]:
            print(f"    {f}", file=sys.stderr)
        if len(_suspects) > 20:
            print(f"    … 외 {len(_suspects) - 20}개", file=sys.stderr)
        print("  이 상태로 돈 시험은 depot 이 아닌 코드를 잰다. 먼저 방향을 확인할 것:", file=sys.stderr)
        print("  `p4 diff <파일>` 로 실제 차이를 본 뒤, 워크스페이스가 낡았으면 "
              "`p4 sync -f`, 진짜 미제출 새 내용이면 `p4 edit`+submit.", file=sys.stderr)
        print("  급하면 PYTMUX_SKIP_WORKSPACE_GUARD=1 로 우회할 수 있다(비권장).",
              file=sys.stderr)
        return 2
    # 모듈 로드 단계의 startup 백스톱(위)을 거둔다 — 이제부터 per-test _arm 이 관리한다
    # (modname 별 import·테스트마다 재무장). discover 가 빈 경우에도 stray 타이머가
    # 성공 실행을 90초 뒤 종료시키지 않게 명시적으로 끈다.
    if TEST_TIMEOUT > 0:
        faulthandler.cancel_dump_traceback_later()
    # ★ **이전 런들이 남긴 임시물에 수명을 준다**(pytmux-435 ④). teardown 이 자기 것을
    #   거두게 된 뒤에도 이미 쌓인 것은 그대로 남는다 — 이 상자 실측 2026-09-02 에
    #   `pytmux-db-*` 2393개 · `pytmux-cap-*` 2847개였다. 나이(24시간)로 갈라서 병렬로
    #   도는 런의 것을 안 건드린다. 실패는 조용히 넘긴다(스위트를 막을 일이 아니다).
    try:
        import harness as _h
        _swept = _h.sweep_stale_temp()
        if _swept:
            print(f"묵은 임시물 {_swept}건을 거뒀다(24시간 지난 pytmux-db-*·pytmux-cap-*)")
    except Exception:
        pass
    # ★ `모듈.시험` 한 건만 고르는 길(pytmux-430). 재시도 재판정이 그 한 건만 깨끗한
    #   프로세스에서 다시 돌리려면 이 선택자가 있어야 한다. 사람이 쓰기에도 좋다:
    #   `python3 tests/run.py test_vtparse.test_csi_raw_param_buffer_bounded`
    names, only = [], {}
    for a in argv:
        a = a[:-3] if a.endswith(".py") else a
        mod, _dot, test = a.partition(".")
        names.append(mod)
        if not test:
            only[mod] = None                       # 모듈 전체 — 부분 선택을 덮는다
        elif only.get(mod, ()) is not None:
            only.setdefault(mod, set()).add(test)
    passed = failed = flaky = skipped = 0
    failures = []
    skips = []
    leaks = []
    leak_base = {}
    mods = discover(names)
    rep = Reporter(_report_path())
    global _REPORTER
    _REPORTER = rep
    _install_fatal_signal_logger()
    # ★ 시작 시각을 남긴다(pytmux/pytmux-132). 리포트는 나중에·다른 머신에서 읽히고
    #   (`--report` · `scripts/tracker_tests.py`), 그때 "언제 돈 run 인가"에 답할 수 있는 것은
    #   이 줄뿐이다 — 없으면 파일 mtime 으로 떨어지는데 그건 복사 한 번에 바뀐다.
    rep.emit("start", ts=time.strftime("%Y-%m-%dT%H:%M:%S"),
             modules=mods, argv=list(argv), pid=os.getpid())
    for modname in mods:
        # import 도 SIGALRM 으로 감싼다 — 모듈 import 가 매달리면(과거 macOS CI 에서
        # 스위트가 첫 출력도 없이 17분 매달리던 정확한 지점) 여기서 TIMEOUT 실패로
        # 전환돼 run.py 가 스스로 끝난다(step 이 완료돼 로그가 남고 주범 모듈이 보임).
        print(f":: import {modname}", file=sys.stderr, flush=True)
        rep.emit("import", module=modname)
        if _LEAK_GUARD:
            _snapshot_globals(leak_base)   # import 전 = 이 모듈의 pristine 기준선
        _arm()
        try:
            mod = importlib.import_module(modname)
        except BaseException as e:   # TimeoutError(SIGALRM) 포함
            failed += 1
            failures.append((f"{modname} (import)", e, traceback.format_exc()))
            print(f"  FAIL  {modname} (import): {e}")
            rep.emit("result", label=f"{modname} (import)", status="fail",
                     reason=str(e))
            _disarm()
            continue
        _disarm()
        # ⚠ **동기 `def test_` 도 거둔다**(2026-08-02). 종전에는 코루틴만 거뒀는데,
        # 그러면 평범한 `def test_…` 로 적힌 테스트가 **한 줄의 경고도 없이** 안 돈다 —
        # 실측으로 여덟 파일에 27개가 그렇게 죽어 있었고(셋은 파일 통째로 0개),
        # 스위트는 그동안 초록이었다. 이 저장소가 '공허 통과'라 부르는 그것이다.
        tests = [(n, f) for n, f in vars(mod).items()
                 if n.startswith("test_") and (asyncio.iscoroutinefunction(f)
                                               or inspect.isfunction(f))]
        sel = only.get(modname)
        if sel:
            tests = [(n, f) for n, f in tests if n in sel]
        for name, fn in sorted(tests):
            label = f"{modname}.{name}"
            # 진행중 표식: 리포트에 **시작**도 남긴다. 종전엔 완료된 result 만 남아,
            # 러너가 통째로 죽으면 --report 가 "마지막 import: <모듈>" 까지만 알려
            # 줬다 — 정작 **죽을 때 물려 있던 테스트**를 못 짚어 진단이 막혔다
            # (2026-07-26 즉사 추적에서 실제로 이것 때문에 모듈을 60개씩 되돌려
            # 세어야 했다). begin 이 있으면 마지막 begin 이 곧 사망 지점이다.
            rep.emit("begin", label=label)
            _CURRENT["label"] = label
            ok, hung, last_exc, last_tb = False, False, None, ""
            was_skipped = False
            n_hung = 0
            t0 = time.monotonic()
            attempts = 0
            for attempt in range(max(1, TEST_RETRIES + 1)):
                _arm()
                hung = False               # 이번 시도의 성격(마지막 시도가 tag 결정)
                attempts = attempt + 1
                try:
                    asyncio.run(_run_with_timeout(fn))
                    ok = True
                except SkipTest as e:
                    was_skipped = str(e) or True     # 사유를 리포트로 그대로 운반
                    skips.append((label, str(e)))
                    print(f"  SKIP  {label}: {e}" if str(e)
                          else f"  SKIP  {label}")
                    break                  # 스킵은 재시도 안 함(finally 가 _disarm)
                except SuiteTimeout as e:
                    # ⛔ 여기 걸리는 것은 **러너의 상한**뿐이다 — 시험이 스스로 낸
                    #    TimeoutError 는 아래 BaseException 으로 가서 트레이스백을
                    #    지킨다(pytmux-452).
                    hung = True
                    n_hung += 1
                    last_exc = e
                    last_tb = f"TIMEOUT after {TEST_TIMEOUT}s\n"
                except BaseException as e:
                    # **BaseException** 이다(종전 Exception). SystemExit·KeyboardInterrupt
                    # 는 Exception 이 아니라, 테스트가 지나는 코드 어딘가에서
                    # `sys.exit(1)` 이 뜨면 여기 안 걸리고 main() 밖으로 빠져나가
                    # **요약도 트레이스백도 없이 종료코드 1** 로 스위트가 끝났다
                    # (프로덕션 경로에 sys.exit 가 여럿 있다 — launcher·client).
                    # 그 한 건을 그 테스트의 실패로 기록하고 스위트는 계속 간다.
                    last_exc, last_tb = e, traceback.format_exc()
                    if isinstance(e, KeyboardInterrupt):
                        raise          # Ctrl-C 는 사용자 의도 — 그대로 전파
                finally:
                    _disarm()
                if ok:
                    if attempt == 0:
                        print(f"  PASS  {label}")
                    elif _ADJUDICATE and not _ADJUDICATING:
                        # 아직 «통과»라 부르지 않는다 — 아래에서 깨끗한 프로세스가
                        # 판정한다(pytmux-430). 여기서 PASS 를 찍어 버리면 화면에
                        # 남는 마지막 말이 초록이라, 뒤집힌 판정을 사람이 놓친다.
                        print(f"  ....  {label} "
                              f"({attempt}회 재시도 후 통과 — 재판정한다)")
                    else:
                        print(f"  PASS  {label} (FLAKY — {attempt}회 재시도 후 통과)")
                    break
                # 재시도 판정: 일반 실패는 TEST_RETRIES 까지, 타임아웃(행)은 부하 스톨
                # 복구용으로 TEST_TIMEOUT_RETRIES 까지만(진짜 데드락은 +유한 비용 후 확정).
                if attempt == TEST_RETRIES or (hung and n_hung > TEST_TIMEOUT_RETRIES):
                    break
                print(f"  retry {label} (시도 {attempt + 1} 실패: {last_exc})")
            secs = round(time.monotonic() - t0, 3)
            if was_skipped:
                skipped += 1
                rep.emit("result", label=label, status="skip", secs=secs,
                         reason="" if was_skipped is True else was_skipped)
                continue                   # passed/failed 어디에도 안 셈
            # ★ 재시도로 통과한 건은 «깨끗한 프로세스»에서 다시 판정한다(pytmux-430).
            #   시도들은 한 프로세스를 공유해 서로 독립이 아니므로, 여기서만 통과한
            #   것은 「통과」의 증거가 못 된다.
            if ok and attempts > 1 and _ADJUDICATE and not _ADJUDICATING:
                verdict, detail = _adjudicate(label)
                rep.emit("adjudicate", label=label,
                         result={True: "pass", False: "fail"}.get(verdict, "unknown"))
                if verdict is False:
                    ok = False
                    last_exc = RuntimeError(
                        "재시도가 덮은 결정론적 실패 — 깨끗한 프로세스에서 다시 재니 "
                        "실패했다(시도들은 한 프로세스를 공유해 서로 독립이 아니다). "
                        "재판정을 끄려면 PYTMUX_TEST_ADJUDICATE=off")
                    last_tb = ""
                    hung = False
                    print(f"        ↳ 재판정: 새 프로세스에서 **실패** — "
                          f"FLAKY 가 아니라 실패다")
                elif verdict is True:
                    print(f"  PASS  {label} "
                          f"(FLAKY — 재판정: 새 프로세스에서도 통과)")
                else:
                    # 판정 못 함. 통과로 접되 **화면에 남긴다** — 조용히 초록이
                    # 되는 것이 이 이슈가 잡으려는 그것이다.
                    print(f"  PASS  {label} (FLAKY — ⚠ 재판정 못 함: {detail!s:.120})")
            if ok:
                passed += 1
                if attempts > 1:
                    flaky += 1
                rep.emit("result", label=label,
                         status="flaky" if attempts > 1 else "pass",
                         secs=secs, attempts=attempts)
            else:
                failed += 1
                failures.append((label, last_exc, last_tb))
                tag = "TIMEOUT" if hung else "FAIL"
                print(f"  {tag}  {label}: {last_exc}")
                rep.emit("result", label=label,
                         status="timeout" if hung else "fail",
                         secs=secs, attempts=attempts, reason=str(last_exc))
                # 트레이스백을 실패 **즉시**도 찍는다 — 종전엔 말미 일괄 덤프뿐이라,
                # CI step 타임아웃이 스위트를 중간에 끊으면(Windows 8분) 실패의
                # 원인 트레이스백이 통째로 유실돼 진단 불능이었다(2026-07-10).
                if last_tb:
                    for ln in str(last_tb).rstrip().splitlines():
                        print(f"        {ln}")
        # 모듈이 남긴 프로덕션 전역 치환을 되돌리고 이름으로 보고한다(2차 방어).
        if _LEAK_GUARD:
            leaked = _restore_globals(leak_base)
            if leaked:
                leaks.append((modname, leaked))
                print(f"  LEAK  {modname}: 프로덕션 전역 {len(leaked)}개를 되돌리지 "
                      f"않고 끝냈다 → 되돌림({', '.join(sorted(leaked)[:6])}"
                      f"{' …' if len(leaked) > 6 else ''})")
                rep.emit("leak", module=modname, attrs=sorted(leaked))
    flaky_note = f" ({flaky} flaky — 재시도 후 통과)" if flaky else ""
    skip_note = f", {skipped} skipped" if skipped else ""
    print(f"\n{'='*50}\n{passed} passed, {failed} failed{skip_note}{flaky_note}")
    # 요약은 stdout 과 **같은 수치**로 리포트에도 남긴다(이 줄의 유무가 완주/절단 판정).
    rep.emit("summary", passed=passed, failed=failed, skipped=skipped, flaky=flaky)
    rep.close()
    if rep.path:
        print(f"리포트: {rep.path} (절단된 run 회계 복원 = "
              f"python3 tests/run.py --report)")
    if leaks:
        # 누출은 실패로 세지 않는다(가드가 이미 되돌려 뒤 모듈은 안전하다). 다만
        # **원인 모듈을 이름으로** 남겨 테스트가 스스로 되돌리게 고칠 수 있게 한다.
        print("leaked globals (가드가 되돌림 — 해당 테스트가 스스로 복원해야 한다):")
        for modname, attrs in leaks:
            print(f"  {modname}: {', '.join(sorted(attrs))}")
    if skips:
        # 커버리지 갭 가시화: 무엇이 왜 안 돌았는지 사유별로 묶어 리포트.
        from collections import Counter
        by_reason = Counter(reason or "(사유 없음)" for _, reason in skips)
        print("skipped:")
        for reason, cnt in by_reason.most_common():
            print(f"  {cnt:3d}  {reason}")
    for label, e, tb in failures:
        print(f"\n--- {label} ---\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
