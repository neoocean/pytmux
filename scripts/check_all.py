#!/usr/bin/env python3
"""합본 게이트 — **커밋 전에 기억할 명령 하나**(트리 통합 계획 §5.1 · M2).

    python3 scripts/check_all.py            # 전부
    python3 scripts/check_all.py --fast     # Rust 크로스컴파일·미러 드리프트 빼고
    python3 scripts/check_all.py --list     # 무엇을 도는지만 본다

# 왜 필요한가

2026-08-01 트리 통합 전에는 서버(파이썬)와 Rust 클라가 **다른 트리**였고, 게이트도 각자
있었다: 파이썬은 `tests/run.py`, 클라는 `cargo test` + 셸 게이트 셋 + 패리티 래칫,
게시는 `scripts/publish_check.py`. 트리를 합친 뒤에도 사람이 여섯 개를 순서대로 기억해야
한다면 그건 **합친 것이 아니다** — 실제로 프로토콜을 건드리고 한쪽만 돌린 반쪽 CL 이
이 저장소의 상습 결함이었다.

# 판정 규칙(이 저장소 특유)

- **파이썬 스위트는 rc 를 믿지 않는다.** `tests/run.py` 는 실패해도 0 으로 끝날 수 있어
  (CLAUDE.md 경고), 요약줄의 `N failed` 를 읽어 판정한다.
- **빈 결과는 통과가 아니라 고장이다.** 셸 게이트가 규칙을 하나도 안 잡고 rc 0 이던
  회귀가 실제로 있었다(`check_licenses.sh` 주석). 여기서도 스텝이 "잰 것이 0개"면
  실패로 본다.
- **한 스텝이 넘어져도 나머지를 돈다.** 첫 실패에서 멈추면 "고칠 것이 몇 개인가"를 못
  본다 — 한 번 돌려 전부 보는 것이 이 파일의 값이다.

# 순서

빠르고 좁은 것부터(고장이 있으면 일찍 보인다), 느리고 넓은 것을 뒤로.
"""

import argparse
import locale
import os
import re
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


class Step:
    """한 스텝. `check` 가 있으면 rc 대신 그것이 판정한다."""

    def __init__(self, name, argv, cwd, why, check=None, slow=False, needs=None):
        self.name = name
        self.argv = argv
        self.cwd = cwd
        self.why = why
        self.check = check
        self.slow = slow
        self.needs = needs or (lambda: None)


def python_suite_verdict(out, rc):
    """`tests/run.py` 의 **요약줄**로 판정한다 — rc 는 신뢰할 수 없다(CLAUDE.md).

    절단(요약줄 자체가 없다)도 실패다: 부하로 러너가 죽으면 "아무 실패도 없었다"처럼
    보이는데, 그건 아무것도 안 잰 것이다.
    """
    m = re.search(r"(\d+)\s+passed[^\n]*?(\d+)\s+failed", out)
    if not m:
        return "요약줄(N passed, N failed)이 없다 — 절단됐거나 러너가 죽었다"
    passed, failed = int(m.group(1)), int(m.group(2))
    if passed == 0:
        return "통과 0건 — 통과가 아니라 고장이다"
    if failed:
        return f"{failed}건 실패(통과 {passed})"
    return None


def cargo_verdict(out, rc):
    """`cargo test` 는 rc 가 정직하다. 다만 **한 건도 안 돌았으면** 고장이다."""
    if rc != 0:
        fails = [l for l in out.splitlines() if l.startswith("---- ") or "panicked at" in l]
        return "; ".join(fails[:3]) or f"rc={rc}"
    total = sum(int(m) for m in re.findall(r"test result: ok\. (\d+) passed", out))
    if total == 0:
        return "테스트가 한 건도 안 돌았다 — 통과가 아니라 고장이다"
    return None


# 스텝이 실패했을 때 **원인 줄**을 알아보는 표지. 자식들은 `FAIL:`·`✗` 로 결론을 낸다.
_CAUSE = re.compile(r"^\s*(FAIL\b|ERROR\b|✗|error(\[|:))", re.IGNORECASE)


def rc_verdict(out, rc):
    """rc != 0 이면 **원인 줄**을 골라 준다.

    종전에는 그냥 마지막 줄을 집었다. 그런데 자식은 결론(`FAIL: …`) 뒤에 처방을 더 찍고,
    stderr 에 잡음이 섞이면 그게 맨 뒤로 간다 — 그래서 요약에 뜨는 한 줄이 원인이 아닌
    경우가 생긴다. 실제로 `FAIL: 픽스처가 낡았다 — config_write.json` 이 디코딩 경고
    트레이스백에 덮여, **낡은 픽스처를 환경 실패로 오독**했다(2026-08-01). 요약 한 줄이
    거짓말을 하면 합본 게이트의 값이 통째로 없어진다.
    """
    if rc == 0:
        return None
    lines = [l.strip() for l in (out or "").splitlines() if l.strip()]
    for line in lines:
        if _CAUSE.match(line):
            return line
    return lines[-1] if lines else "rc != 0"


def detail(out):
    """실패한 스텝의 본문 — **원인 줄 우선**, 없으면 꼬리.

    종전에는 무조건 꼬리 15줄이었다. 파이썬 스위트가 4건 실패한 날 그 자리에 뜬 것은
    `:: import test_…` 열다섯 줄이었고 **떨어진 테스트 이름은 한 줄도 없었다**(실측
    2026-08-01 — 무엇이 깨졌는지 보려고 러너를 처음부터 다시 돌려야 했다).
    """
    lines = [l.rstrip() for l in (out or "").splitlines() if l.strip()]
    cause = [l for l in lines if _CAUSE.match(l)]
    return (cause or lines)[-15:]


def find_bash():
    """셸 게이트가 쓸 **진짜** bash. 못 찾으면 None.

    Windows 에서 `shutil.which("bash")` 는 `…\\WindowsApps\\bash.exe` — **Store 앱 실행
    별칭**(WSL 런처)을 집는다. WSL 이 없는 상자에서 그것은 뜨자마자
    `Class not registered` 로 죽어, **계층·라이선스 게이트 둘이 통째로 무용**이었다
    (실측 2026-08-01: 각 60초를 태우고 빨간 줄만 남겼다 — 재는 것이 없는데 재는 척을
    했으니 SKIP 보다 나쁘다). 이 저장소의 셸 게이트는 Git Bash 로 돌게 쓰였으니 git
    옆의 것을 집는다. `PYTMUX_BASH` 로 직접 지정할 수도 있다.
    """
    override = os.environ.get("PYTMUX_BASH")
    if override:
        # 있는 그대로 돌려준다 — 경로가 틀렸으면 **FAIL 로 시끄럽게** 죽는 편이 낫다.
        # None 을 돌려주면 세 게이트가 조용히 SKIP 되는데, 지정한 사람은 돌고 있다고
        # 믿는다("안 재고 있는 줄 모르는 것"이 이 저장소가 가장 싫어하는 실패다).
        return override

    candidates = []
    found = shutil.which("bash")
    # System32/WindowsApps 의 것은 WSL 런처(또는 그 별칭)다 — 게이트가 보는 경로 규약이
    # 다르고, 이 상자에서는 아예 안 뜬다. 비Windows 에서는 이 필터에 걸릴 일이 없다.
    if found and not any(p in found.replace("/", "\\").lower()
                         for p in ("\\windowsapps\\", "\\system32\\")):
        candidates.append(found)
    git = shutil.which("git")
    if git:                                   # …\Git\cmd\git.exe → …\Git\bin\bash.exe
        candidates.append(os.path.join(os.path.dirname(os.path.dirname(git)), "bin", "bash.exe"))
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            candidates.append(os.path.join(base, "Git", "bin", "bash.exe"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def steps():
    have_cargo = shutil.which("cargo") is not None
    bash = find_bash()
    no_bash = None if bash else "쓸 만한 bash 가 없다(Git Bash 설치 또는 PYTMUX_BASH 로 지정)"
    return [
        Step(
            "픽스처 신선도", [sys.executable, os.path.join("scripts", "check_fixtures.py")],
            CLIENT,
            "정본이 앞서 나갔는데 픽스처가 안 따라왔나(계획 §4.8 — 안 재서 다섯 개가 벌어졌다)",
            check=rc_verdict,
            needs=lambda: None if os.path.isdir(CLIENT) else "client/ 가 없다",
        ),
        Step(
            "계층 게이트", [bash or "bash", os.path.join("scripts", "check_layering.sh")], CLIENT,
            "뷰만 두 벌이고 상태·키맵·명령은 한 벌인가(core 에 UI 의존 금지)",
            check=rc_verdict, needs=lambda: no_bash,
        ),
        Step(
            "라이선스 경계", [bash or "bash", os.path.join("scripts", "check_licenses.sh")], CLIENT,
            "MIT 경계 — 의존 그래프의 로컬 크레이트가 허용 목록과 정확히 같은가",
            check=rc_verdict, needs=lambda: no_bash,
        ),
        Step(
            "패리티 래칫",
            ["cargo", "test", "-p", "proto", "--test", "parity"], CLIENT,
            "정본 표면을 덮은 수가 **의도 없이** 움직이지 않았나",
            check=cargo_verdict,
            needs=lambda: None if have_cargo else "cargo 가 없다",
        ),
        Step(
            "Rust 스위트", ["cargo", "test", "--workspace", "--no-fail-fast"], CLIENT,
            "클라 둘 + core/proto 전부",
            check=cargo_verdict, slow=True,
            needs=lambda: None if have_cargo else "cargo 가 없다",
        ),
        Step(
            "크로스OS 컴파일", [bash or "bash", os.path.join("scripts", "check_windows.sh")], CLIENT,
            "두 번째 OS 가 조용히 썩지 않았나(링크는 안 하고 cfg·타입만)",
            check=rc_verdict, slow=True,
            needs=lambda: no_bash or (None if have_cargo else "cargo 가 없다"),
        ),
        Step(
            "파이썬 스위트", [sys.executable, os.path.join("tests", "run.py")], ROOT,
            "정본(서버 + Textual 클라) 전체 — **요약줄**로 판정한다",
            check=python_suite_verdict, slow=True,
        ),
        Step(
            "미러 위생", [sys.executable, os.path.join("scripts", "check_mirror.py")], ROOT,
            "올라가야 할 것이 올라가고 안 될 것이 안 올라가나(계획 §5.3 — 앵커 하나로 리포트 400장이 조용히 빠진다)",
            check=rc_verdict,
        ),
        Step(
            "미러 드리프트", [sys.executable, os.path.join("scripts", "publish_check.py")], ROOT,
            "p4 에만 / git 에만 있는 내용이 없나(반쪽 게시)",
            check=rc_verdict, slow=True,
            # p4 전용 워크스페이스(git 클론이 없는 곳)에서는 **잴 것이 아예 없다.**
            # 그것을 FAIL 로 찍으면 매번 빨간 줄이 하나 상주하고, 상주하는 빨간 줄은
            # 곧 아무도 안 본다 — SKIP 은 요약에 사유와 함께 반드시 남는다(아래 '건너뜀').
            # `미러 위생`(check_mirror.py)이 이미 같은 판정을 스스로 한다.
            needs=lambda: (None if os.path.isdir(os.path.join(ROOT, ".git"))
                           else "git 클론이 아니다 — 미러는 다른 워크스페이스에서 본다"),
        ),
    ]


def decode(raw):
    """자식 출력을 글자로. **환경변수로 자식을 길들이지 않는다.**

    Windows 에서 `text=True` 의 기본 디코더는 로케일(cp949/cp1252)인데 이 저장소의
    게이트 출력은 UTF-8 한글이다 — 그대로 두면 리더 스레드가 UnicodeDecodeError 로 죽고
    **그 스텝의 출력이 통째로 사라진다**(같은 사고가 `win_report.py` 에 2026-06-09 로
    적혀 있다). 그렇다고 자식에게 `PYTHONIOENCODING=utf-8` 을 심는 것은 더 나쁘다:
    그 환경은 **파이썬 스위트까지** 물려받아, 자기가 띄운 게이트의 출력을 로케일로 읽는
    `test_surface_ledger` 가 깨졌다(실측 2026-08-01). **게이트가 재는 대상을 게이트가
    바꾸면 그건 게이트가 아니다** — 그래서 읽는 쪽에서 둘 다 받아 준다.

    (자기 스트림을 utf-8 로 고쳐 잡는 스크립트는 UTF-8 로, 안 그런 것은 로케일로 나온다.)
    """
    if not raw:
        return ""
    for enc in ("utf-8", locale.getpreferredencoding(False)):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "backslashreplace")


def run(step, env):
    started = time.monotonic()
    try:
        # **바이트로 받아 우리가 디코딩한다**(`text=True` 를 안 쓴다) — 아래 `decode` 주석.
        proc = subprocess.run(step.argv, cwd=step.cwd, env=env, capture_output=True)
    except FileNotFoundError as exc:
        return None, f"실행할 수 없다: {exc}", time.monotonic() - started
    out = decode(proc.stdout) + decode(proc.stderr)
    verdict = (step.check or rc_verdict)(out, proc.returncode)
    return out, verdict, time.monotonic() - started


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="느린 스텝(크로스컴파일·전체 스위트·드리프트)을 건너뛴다")
    ap.add_argument("--list", action="store_true", help="무엇을 도는지만 본다")
    args = ap.parse_args()

    todo = [s for s in steps() if not (args.fast and s.slow)]
    if args.list:
        for s in todo:
            print(f"  {s.name:<14} {' '.join(s.argv)}   ({s.cwd})")
            print(f"                 └ {s.why}")
        return 0

    # ⚠ Claude Code 툴 환경은 `NO_COLOR=1` 을 심는데, 그러면 Textual 이 모노크롬 필터를
    # 물려 **내 변경과 무관하게** test_client 가 통째로 떨어진다(CLAUDE.md 실측).
    # 게이트가 그 환경 실패를 제 실패로 보고하면 사람이 헛수고를 한다 — 여기서 지운다.
    env = dict(os.environ)
    env.pop("NO_COLOR", None)

    print(f"합본 게이트 — {len(todo)}단계" + (" (--fast)" if args.fast else ""))
    failures, skipped = [], []
    for step in todo:
        reason = step.needs()
        if reason:
            skipped.append((step.name, reason))
            print(f"  … {step.name:<14} SKIP  ({reason})")
            continue
        out, verdict, secs = run(step, env)
        mark = "OK  " if verdict is None else "FAIL"
        print(f"  {mark} {step.name:<14} {secs:6.1f}s" + ("" if verdict is None else f"  — {verdict}"))
        if verdict is not None:
            failures.append((step.name, verdict, out))

    print()
    if skipped:
        # 조용한 SKIP 은 "다 돌았다"로 읽힌다 — 무엇을 못 쟀는지 반드시 남긴다.
        print(f"건너뜀 {len(skipped)}: " + " · ".join(f"{n}({r})" for n, r in skipped))
    if not failures:
        print(f"✓ {len(todo) - len(skipped)}단계 전부 통과")
        return 0
    print(f"✗ {len(failures)}단계 실패")
    for name, verdict, out in failures:
        print(f"\n── {name}: {verdict}")
        for line in detail(out):
            print(f"   {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
