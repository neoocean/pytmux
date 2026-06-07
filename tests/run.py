#!/usr/bin/env python3
"""pytmux 헤드리스 테스트 러너.

사용법: python3 tests/run.py [test_module ...]
test_*.py 안의 'test_' 로 시작하는 async 함수를 각각 새 asyncio 루프에서 실행하고
PASS/FAIL 을 집계한다. 화면(TUI) 없이 전체 동작을 검증한다.
"""
import asyncio
import importlib
import os
import signal
import sys
import traceback

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

# SIGALRM 하드 백스톱(POSIX). asyncio.wait_for 는 await 지점에서만 취소할 수 있어,
# 테스트가 **동기 블로킹 콜**(PTY os.read·서브프로세스 wait·소켓 recv)에서 매달리면
# 이벤트 루프로 제어가 안 돌아와 못 끊는다 — CI macOS 러너에서 스위트가 첫 출력도
# 없이 47분 매달리던 정확한 증상. SIGALRM 은 블로킹 시스템콜도 인터럽트해 예외를
# 띄우므로, 이 경우에도 그 테스트의 TIMEOUT 실패로 바꿔 빠르게·이름과 함께 드러낸다.
# (Windows 엔 SIGALRM 이 없지만 행은 macOS/Linux 케이스라 무방.)
_HAS_ALARM = hasattr(signal, "SIGALRM")


def _alarm_handler(signum, frame):
    raise TimeoutError(f"{TEST_TIMEOUT}s 초과 — hang(SIGALRM, 동기 블로킹 의심)")


HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))


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
    passed = failed = 0
    failures = []
    for modname in discover(names):
        mod = importlib.import_module(modname)
        tests = [(n, f) for n, f in vars(mod).items()
                 if n.startswith("test_") and asyncio.iscoroutinefunction(f)]
        for name, fn in sorted(tests):
            label = f"{modname}.{name}"
            if _HAS_ALARM and TEST_TIMEOUT > 0:
                signal.signal(signal.SIGALRM, _alarm_handler)
                # 동기 블로킹도 깨도록 wait_for 보다 살짝 늦게 발화(여유 2초).
                signal.setitimer(signal.ITIMER_REAL, TEST_TIMEOUT + 2)
            try:
                asyncio.run(_run_with_timeout(fn))
                passed += 1
                print(f"  PASS  {label}")
            except asyncio.TimeoutError:
                failed += 1
                msg = f"{TEST_TIMEOUT}s 초과 — hang(데드락 의심)"
                failures.append((label, TimeoutError(msg),
                                 f"TIMEOUT after {TEST_TIMEOUT}s\n"))
                print(f"  FAIL  {label}: TIMEOUT {TEST_TIMEOUT}s")
            except Exception as e:
                failed += 1
                failures.append((label, e, traceback.format_exc()))
                print(f"  FAIL  {label}: {e}")
            finally:
                if _HAS_ALARM and TEST_TIMEOUT > 0:
                    signal.setitimer(signal.ITIMER_REAL, 0)   # 타이머 해제
    print(f"\n{'='*50}\n{passed} passed, {failed} failed")
    for label, e, tb in failures:
        print(f"\n--- {label} ---\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
