#!/usr/bin/env python3
"""pytmux 헤드리스 테스트 러너.

사용법: python3 tests/run.py [test_module ...]
test_*.py 안의 'test_' 로 시작하는 async 함수를 각각 새 asyncio 루프에서 실행하고
PASS/FAIL 을 집계한다. 화면(TUI) 없이 전체 동작을 검증한다.
"""
import asyncio
import faulthandler
import importlib
import os
import signal
import sys
import traceback

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

# 한 테스트가 매달리면(예: CI macOS 러너에서 PTY/서브프로세스 데드락) 스위트 전체가
# 멈추지 않게 테스트별 타임아웃을 건다 → 행(hang)을 그 테스트의 TIMEOUT 실패로 바꿔
# 빠르게·이름과 함께 드러낸다. 로컬은 테스트당 수초 이내라 90초면 오검출 없고, 47분씩
# 매달리던 진짜 행은 잡힌다. PYTMUX_TEST_TIMEOUT 으로 조정(0=무제한).
TEST_TIMEOUT = float(os.environ.get("PYTMUX_TEST_TIMEOUT", "90"))

# 간헐 실패(주로 느린 CI 러너에서 Textual run_test 클라 테스트의 타이밍 — fixed
# pilot.pause 가 짧아 렌더가 아직 안 됨) 재시도. 일시적 플레이크는 재시도로 통과하고,
# 진짜 실패는 모든 시도에서 실패해 그대로 잡힌다. 재시도로 통과한 건 FLAKY 로 표시해
# 가시성 유지. **타임아웃(행)은 재시도 안 함**(행을 또 기다리는 건 낭비). 0=재시도 끔.
TEST_RETRIES = int(os.environ.get("PYTMUX_TEST_RETRIES", "2"))

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


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

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
    for _m in ("claude", "tokens", "usageprobe", "usagelog", "usagedb"):
        _mod = importlib.import_module(f"pytmuxlib.plugins.claude-code.{_m}")
        sys.modules[f"pytmuxlib.{_m}"] = _mod
        setattr(_pt, _m, _mod)
except Exception:
    pass


async def _run_with_timeout(fn):
    if TEST_TIMEOUT > 0:
        await asyncio.wait_for(fn(), TEST_TIMEOUT)
    else:
        await fn()


def discover(names):
    mods = []
    for fn in sorted(os.listdir(HERE)):
        if fn.startswith("test_") and fn.endswith(".py"):
            mod = fn[:-3]
            if not names or mod in names:
                mods.append(mod)
    return mods


def main(argv):
    names = [a[:-3] if a.endswith(".py") else a for a in argv]
    passed = failed = flaky = 0
    failures = []
    for modname in discover(names):
        # import 도 SIGALRM 으로 감싼다 — 모듈 import 가 매달리면(과거 macOS CI 에서
        # 스위트가 첫 출력도 없이 17분 매달리던 정확한 지점) 여기서 TIMEOUT 실패로
        # 전환돼 run.py 가 스스로 끝난다(step 이 완료돼 로그가 남고 주범 모듈이 보임).
        print(f":: import {modname}", file=sys.stderr, flush=True)
        _arm()
        try:
            mod = importlib.import_module(modname)
        except BaseException as e:   # TimeoutError(SIGALRM) 포함
            failed += 1
            failures.append((f"{modname} (import)", e, traceback.format_exc()))
            print(f"  FAIL  {modname} (import): {e}")
            _disarm()
            continue
        _disarm()
        tests = [(n, f) for n, f in vars(mod).items()
                 if n.startswith("test_") and asyncio.iscoroutinefunction(f)]
        for name, fn in sorted(tests):
            label = f"{modname}.{name}"
            ok, hung, last_exc, last_tb = False, False, None, ""
            for attempt in range(max(1, TEST_RETRIES + 1)):
                _arm()
                try:
                    asyncio.run(_run_with_timeout(fn))
                    ok = True
                except asyncio.TimeoutError:
                    hung = True            # 행은 재시도 안 함(아래에서 break)
                    last_exc = TimeoutError(f"{TEST_TIMEOUT}s 초과 — hang(데드락 의심)")
                    last_tb = f"TIMEOUT after {TEST_TIMEOUT}s\n"
                except Exception as e:
                    last_exc, last_tb = e, traceback.format_exc()
                finally:
                    _disarm()
                if ok:
                    if attempt == 0:
                        print(f"  PASS  {label}")
                    else:
                        flaky += 1
                        print(f"  PASS  {label} (FLAKY — {attempt}회 재시도 후 통과)")
                    break
                if hung or attempt == TEST_RETRIES:
                    break                  # 행이거나 마지막 시도 → 실패 확정
                print(f"  retry {label} (시도 {attempt + 1} 실패: {last_exc})")
            if ok:
                passed += 1
            else:
                failed += 1
                failures.append((label, last_exc, last_tb))
                tag = "TIMEOUT" if hung else "FAIL"
                print(f"  {tag}  {label}: {last_exc}")
    flaky_note = f" ({flaky} flaky — 재시도 후 통과)" if flaky else ""
    print(f"\n{'='*50}\n{passed} passed, {failed} failed{flaky_note}")
    for label, e, tb in failures:
        print(f"\n--- {label} ---\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
