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
- **매달림도 실패다.** 스텝마다 시한이 있고(`STEP_TIMEOUT`) 넘으면 손자까지 걷어 FAIL
  로 적는다. 그리고 스텝 이름은 **시작할 때** 찍는다 — 시한도 진행 표시도 없던 시절에
  이 게이트가 첫 스텝에서 47분 동안 0바이트였고, 그때 침묵은 "아직 도는 중"과 구분이
  안 됐다(pytmux-194).

# 순서

빠르고 좁은 것부터(고장이 있으면 일찍 보인다), 느리고 넓은 것을 뒤로.
"""

import argparse
import locale
import os
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(ROOT, "client")

# 스위트 위생 헬퍼를 **한 곳에서** 빌린다(`tests/hermetic.py` · pytmux/pytmux-202).
# 레시피를 여기에 두 번째로 적으면 「빈 설정 파일을 어디에 세우나」를 두 술어가 각자
# 답하게 되고, 갈리는 날 조용한 쪽이 믿긴다 — 이 저장소의 상습 결함이다.
# ⚠ 그 모듈은 `os`·`tempfile` 밖의 것을 안 문다(그 파일 머리말) — 게이트가 재는 대상을
#   import 로 물어 버리는 일이 없다.
# ⛔ **맨 앞이 아니라 맨 뒤에 붙인다** — `tests/` 에는 `run.py`·`harness.py` 처럼 흔한
#   이름이 있어서, 앞에 세우면 이 파일이 나중에 무엇을 import 하든 그 이름부터 집는다.
sys.path.append(os.path.join(ROOT, "tests"))
import hermetic  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


# 한 스텝이 이보다 오래 끌면 **매달린 것**으로 본다(pytmux-194).
#
# 왜 이 자리에 시한이 있어야 하나: 2026-08-09 에 첫 스텝(픽스처 신선도)이 손자
# 프로세스의 비-데몬 스레드 때문에 **47분 동안 0바이트**로 매달렸다. 게이트에 시한이
# 없으면 그 침묵은 "아직 도는 중"과 구분이 안 되고, 사람은 기다리다 Ctrl-C 를 친다 —
# 그 CL 은 관문을 안 지나고 나간다. **침묵이 초록으로 읽히면 안 된다**는 이 저장소의
# 규율이 여기서는 "침묵이 무한대기면 안 된다"다.
#
# 값은 이 상자의 실측(2026-08-17 · `--fast` 한 회차)에 **여섯 배**를 뒀다: 픽스처
# 신선도 95.1s · 계층 게이트 96.4s · 라이선스 경계 18.2s · 패리티 래칫 53.4s · 미러
# 위생 75.9s.
# ⚠ **라이선스 경계는 그날 늦게 늘었다**(pytmux-193): 외부 의존과 배포 고지를 함께 재면서
# 26.7s 가 됐고, 다른 cargo 가 패키지 캐시 잠금을 쥐고 있으면 **146s** 까지 갔다(실측).
# 그래도 시한을 안 줄인 이유는 아래와 같다 — 잴 것이 늘어난 스텝에 촘촘한 시한을 물리면
# 그 스텝이 **제 손으로 거짓 빨강**을 만든다.
# ⛔ 빡빡하게 잡지 않는 이유가 있다 — 이 상자는 러너와 세션이 **동시에**
# 전량 스위트를 돌리는 자리라(부하로 벽시계 시험이 붉는 것이 기록돼 있다) 시한이
# 촘촘하면 게이트가 **제 손으로 거짓 빨강**을 만든다. 47분을 10분으로 줄이는 것이 이
# 시한의 값이고, 100초를 120초로 줄이는 것은 아니다.
#
# ⚠ 넘으면 그 스텝만 FAIL 로 적고 **나머지를 계속 돈다** — 이미 "한 스텝이 넘어져도
# 나머지를 돈다"가 이 파일의 계약이다. `PYTMUX_STEP_TIMEOUT` 로 덮을 수 있다(0 이면
# 시한 없음 — 아주 느린 상자에서 디버깅할 때).
STEP_TIMEOUT = 600.0
SLOW_STEP_TIMEOUT = 3600.0


def step_timeout(step):
    """이 스텝에 줄 시한(초). `None` 이면 안 잰다."""
    override = os.environ.get("PYTMUX_STEP_TIMEOUT")
    if override:
        try:
            secs = float(override)
        except ValueError:
            secs = 0.0
        return secs if secs > 0 else None
    return SLOW_STEP_TIMEOUT if step.slow else STEP_TIMEOUT


class Step:
    """한 스텝. `check` 가 있으면 rc 대신 그것이 판정한다."""

    def __init__(self, name, argv, cwd, why, check=None, slow=False, needs=None,
                 skip_ok=False):
        self.name = name
        self.argv = argv
        self.cwd = cwd
        self.why = why
        self.check = check
        self.slow = slow
        self.needs = needs or (lambda: None)
        #: 이 스텝의 SKIP 은 **정당한가**(잴 것이 애초에 없는가). 참이면 종료코드를
        #: 안 바꾼다. 거짓인 SKIP 은 「도구가 없어서 못 쟀다」이고, 그것은 통과가
        #: 아니다(검수 2026-09-05 C-3 · `qa/run.py` 의 rc 3 과 같은 규율).
        self.skip_ok = skip_ok


def unmeasured_skips(skipped):
    """건너뛴 것 중 **정당하지 않은** 것들 — 「도구가 없어서 못 쟀다」쪽.

    `skipped` 는 `(이름, 사유, 정당한가)` 목록이다. 정당한 SKIP 은 잴 것이 애초에 없는
    자리 하나뿐이다(p4 전용 워크스페이스의 미러 드리프트). 나머지는 bash·cargo·client
    부재라 **못 잰 것**이고, 그것을 rc 0 으로 접으면 「✓ 전부 통과」가 거짓이 된다
    (검수 2026-09-05 C-3 · `qa/run.py` 의 rc 3 과 같은 규율).

    판정을 순수 함수로 떼어 둔 이유: `main()` 은 서브프로세스를 띄우는 도구라
    스위트 안에서 통째로 돌릴 수 없고, 그러면 이 규칙이 안 재진다.
    """
    return [(n, r) for n, r, ok in skipped if not ok]


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


def find_cargo():
    """cargo 가 있는 **디렉터리**. 못 찾으면 None.

    `find_bash()` 와 같은 부류의 자리다 — `shutil.which` 하나로는 **깔려 있는데 없다고**
    말하는 상자가 있다. rustup 은 이진을 `~/.cargo/bin` 에 두고 PATH 는 셸 프로필
    (`~/.cargo/env`)에서 세우는데, **비대화형 셸은 그 프로필을 안 읽는다** — 에이전트
    툴 환경·launchd·CI 러너가 전부 그 갈래다.

    그 한 줄이 무엇을 태웠는지가 이 함수의 존재 이유다: 이 상자(playground · macOS)에서
    `cargo 1.92.0` 이 `~/.cargo/bin` 에 멀쩡히 있는데도 세 스텝(패리티 래칫 · Rust
    스위트 · 크로스OS 컴파일)이 *"cargo 가 없다"* 로 SKIP 됐고, 그 SKIP 한 줄이
    pytmux-33(ⓖ3 전면 대조)의 착수 관문에 **"이 상자에서는 Rust 자를 못 세운다"** 로
    적혀 축 둘의 자 세우기가 통째로 미뤄졌다(2026-08-05 기록 → 2026-08-09 실측으로
    뒤집힘). 셸 게이트 셋은 같은 자리에서 *"PATH 에 ~/.cargo/bin 이 있는지 확인"* 이라고
    맞게 안내하고 있었다 — **한 질문에 두 술어가 서로 다른 답을 하고 있었고, 조용한
    쪽이 믿겼다.**

    ⛔ 찾은 자리를 **PATH 앞에 세우는 것**까지가 이 함수의 값이다(`main`). 셸 게이트는
    자기가 `command -v cargo` 로 다시 찾으므로, 이름만 풀고 PATH 를 안 고치면 그
    스텝만 또 죽는다.
    """
    found = shutil.which("cargo")
    if found:
        return os.path.dirname(found)
    exe = "cargo.exe" if os.name == "nt" else "cargo"
    home = os.path.join(os.path.expanduser("~"), ".cargo", "bin")
    if os.path.isfile(os.path.join(home, exe)):
        return home
    return None


def steps():
    have_cargo = find_cargo() is not None
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
            "MIT 경계 — 로컬 크레이트가 허용 목록과 같은가 + 외부 의존의 라이선스와"
            " 배포 이진의 저작권 고지(pytmux-193)",
            check=rc_verdict, needs=lambda: no_bash,
        ),
        Step(
            "패리티 래칫",
            ["cargo", "test", "-p", "proto", "--test", "parity"], CLIENT,
            "정본 표면을 덮은 수가 **의도 없이** 움직이지 않았나",
            check=cargo_verdict,
            needs=lambda: None if have_cargo else "cargo 를 못 찾았다(PATH·~/.cargo/bin 둘 다)",
        ),
        Step(
            "상호작용 계약",
            ["cargo", "test", "-p", "proto", "--test", "interaction"], CLIENT,
            "새 판이 «정본과 같게 구나»를 재고 들어왔나(pytmux-185 — 있는 것과 같게 구는"
            " 것은 다른 질문이다)",
            check=cargo_verdict,
            needs=lambda: None if have_cargo else "cargo 를 못 찾았다(PATH·~/.cargo/bin 둘 다)",
        ),
        Step(
            "화면 키 적합성",
            ["cargo", "test", "-p", "proto", "--test", "screen_key_conformance"], CLIENT,
            "정본 화면의 on_key 를 AST 로 읽어 그 키를 실제로 눌러 대조한다(pytmux-454 —"
            " 「같아 보인다」가 아니라 「눌러 봤다」)",
            check=cargo_verdict,
            needs=lambda: None if have_cargo else "cargo 를 못 찾았다(PATH·~/.cargo/bin 둘 다)",
        ),
        Step(
            "네이티브 등록표",
            ["cargo", "test", "-p", "proto", "--test", "native_escape_ledger"], CLIENT,
            "「이 오버레이는 내가 그린다」가 등록표·광고에서 같은 말을 하나(pytmux-458 —"
            " 그 갈림이 어디에도 안 적히면 표가 조용히 는다)",
            check=cargo_verdict,
            needs=lambda: None if have_cargo else "cargo 를 못 찾았다(PATH·~/.cargo/bin 둘 다)",
        ),
        Step(
            "Rust 스위트", ["cargo", "test", "--workspace", "--no-fail-fast"], CLIENT,
            "클라 둘 + core/proto 전부",
            check=cargo_verdict, slow=True,
            needs=lambda: None if have_cargo else "cargo 를 못 찾았다(PATH·~/.cargo/bin 둘 다)",
        ),
        Step(
            "크로스OS 컴파일", [bash or "bash", os.path.join("scripts", "check_windows.sh")], CLIENT,
            "두 번째 OS 가 조용히 썩지 않았나(링크는 안 하고 cfg·타입만)",
            check=rc_verdict, slow=True,
            needs=lambda: no_bash or (None if have_cargo else "cargo 를 못 찾았다(PATH·~/.cargo/bin 둘 다)"),
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
            # ★ **이 SKIP 만 정당하다**(검수 2026-09-05 C-3): p4 전용 워크스페이스에는
            # 잴 것이 아예 없다. 나머지 여덟(bash·cargo·client 부재)은 「도구가 없어서
            # 못 쟀다」이고, 그것을 rc 0 으로 접으면 «전부 통과» 한 줄이 거짓이 된다.
            skip_ok=True,
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


def _kill_tree(proc):
    """자식**과 그 손자**를 죽인다 — pytmux-194 가 가르친 자리.

    `subprocess` 의 `timeout` 은 **직접 자식**만 죽인다. 그런데 매달렸던 것은 손자였다
    (`check_fixtures.py` → `gen_plugin_client_cmds.py`). 자식만 죽이면 손자가 그대로
    살아 파이프의 쓰기 끝을 쥐고, `communicate()` 는 **여전히 안 돌아온다** — 시한을
    걸어 놓고도 매달리는, 가장 나쁜 모양이다.

    그래서 POSIX 에서는 자식을 **새 세션**으로 띄우고(위 `run`) 프로세스 그룹째 죽인다.
    ⚠ Windows 에는 이 수단이 없어 직접 자식만 죽인다(`start_new_session` 이 POSIX
    전용이다). 그 상자에서는 시한이 손자까지는 못 미친다 — 안 되는 것을 되는 척하지
    않는다.
    """
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def run(step, env, timeout=None):
    started = time.monotonic()
    try:
        # **바이트로 받아 우리가 디코딩한다**(`text=True` 를 안 쓴다) — 아래 `decode` 주석.
        # POSIX 에서는 **새 세션**으로 띄운다 — 시한이 넘었을 때 손자까지 걷으려면
        # 프로세스 그룹이 필요하다(`_kill_tree`).
        proc = subprocess.Popen(
            step.argv, cwd=step.cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **({"start_new_session": True} if os.name != "nt" else {}),
        )
    except FileNotFoundError as exc:
        return None, f"실행할 수 없다: {exc}", time.monotonic() - started
    try:
        raw_out, raw_err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        # 죽인 뒤 남은 출력을 마저 받는다 — 시한에 걸린 스텝일수록 **어디까지 갔나**가
        # 유일한 단서다(그날 사람이 `ps` 로 트리를 떠야 알아낸 그것).
        try:
            raw_out, raw_err = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            raw_out = raw_err = b""
        out = decode(raw_out) + decode(raw_err)
        secs = time.monotonic() - started
        return out, f"시한 {timeout:.0f}초를 넘겨 죽였다 — 매달렸다(출력 {len(out)}자)", secs
    out = decode(raw_out) + decode(raw_err)
    verdict = (step.check or rc_verdict)(out, proc.returncode)
    return out, verdict, time.monotonic() - started


def child_env():
    """모든 스텝이 물려받을 env. **여기가 게이트의 유일한 자식 환경**이다.

    네 가지를 한다. 넷 다 "게이트가 재는 대상을 게이트가 바꾸지 않는다"의 반대편 —
    **상자가 재는 대상을 바꾸고 있는 것**을 걷어내는 자리다.

    ⑴ `NO_COLOR` 를 지운다. Claude Code 툴 환경이 `NO_COLOR=1` 을 심는데, 그러면
       Textual 이 모노크롬 필터를 물려 **내 변경과 무관하게** test_client 가 통째로
       떨어진다(CLAUDE.md 실측). 게이트가 그 환경 실패를 제 실패로 보고하면 사람이
       헛수고를 한다.
    ⑵ 찾은 cargo 를 **PATH 앞에** 세운다(`find_cargo` 의 마지막 ⛔). 셸 게이트
       (`check_windows.sh`)는 자기가 `command -v cargo` 로 다시 찾으므로, 여기서
       PATH 를 안 고치면 그 스텝만 또 "cargo 를 찾을 수 없다"로 죽는다.
    ⑶ ★ `PYTMUX_CONFIG` 를 **빈 임시 파일로 세운다**(pytmux/pytmux-202).
       설정 파일 탐색 차례가 `$PYTMUX_CONFIG` → `$PYTMUX_HOME/config` →
       `$XDG_CONFIG_HOME/pytmux/config` → `~/.config/pytmux/config` → `~/.pytmux.conf`
       라(`client/crates/base/src/config.rs` · `pytmuxlib/keymap.py` 가 같은 차례),
       아무것도 안 세우면 **이 상자의 진짜 설정 파일**로 떨어진다. 파이썬 쪽은
       `tests/run.py` 가 스스로 막지만 **`cargo test` 는 그 프로세스를 안 지난다** —
       Rust 스위트·패리티 래칫·크로스OS 스텝이 통째로 그 보호 밖이었다.
       - 값은 실측이다(2026-08-16 · playground): 스크래치 홈에
         `set status-position top` 한 줄을 두고 `cargo test -p gui` 를 돌리자
         `monitor_badges_sit_in_the_bottom_status_bar_not_the_tab_bar` 가 떨어졌다.
         **제품도 테스트도 멀쩡한데 상자가 실패를 만든** 부류라(사고 2026-08-04 ·
         `client/CLAUDE.md`) 원인을 코드에서 찾으면 한참 헤맨다.
       - ★ **프로세스 밖에서 세우는 것**이라 Rust 저장소의 규약과 안 부딪친다.
         `config_tests.rs`·`session_view_tests.rs` 가 금지하는 것은 테스트가
         `set_var` 로 **프로세스 전역**을 건드리는 것이고(형제 테스트와 경합한다),
         부모가 자식에게 물려주는 값은 그 경합을 안 만든다.
       - ⚠ `cargo test` 를 **직접** 치는 사람은 여전히 보호 밖이다. 읽기 사물함을
         `Config::path()` 에 두는 것은 「테스트가 기대하는 기본값」을 프로덕션 경로에
         심는 모양이 되기 쉬워, 그 판단은 따로 남겼다(pytmux-202 본문).
    ⑷ ★ `PYTHON` 에 **이 게이트가 도는 인터프리터**(`sys.executable`)를 싣는다
       (pytmux/pytmux-383). ⑵ 가 cargo 에 한 것과 **정확히 같은 결**이다 — 셸 게이트
       (`check_licenses.sh`·`build_release.sh`)는 자기가 파이썬을 다시 찾으므로, 여기서
       안 넘기면 그 스텝만 이 상자의 `python3` 로 떨어진다.
       - 값은 실측이다(2026-08-23 · 이 Windows 상자): 그 이름이 Store 앱 별칭
         (`…\\WindowsApps\\python3`)이라 「라이선스 경계」가 **1.0초 만에 FAIL** 로
         떨어졌다(스크립트는 멀쩡했다 — 잴 사람을 못 찾은 것이다).
       - ☠ 더 나쁜 방향이 남아 있다: 같은 별칭이 **rc 0 으로** 끝나는 판이 있고, 그때
         그 스텝은 **아무것도 안 재고 초록**이 된다. 그래서 처방이 둘이다 — 여기서
         넘겨 주는 것과, 받는 쪽이 "있나"가 아니라 **"도나"**로 고르는 것
         (`scripts/pick_python.sh`). 서로를 대신하지 않는다.
       - `sys.executable` 이 빈 값인 자리(임베딩)가 있어 **있을 때만** 싣는다. 없는
         값을 빈 문자열로 심으면 받는 쪽의 `${PYTHON:-…}` 갈래가 조용히 달라진다.

    ⛔ **`main()` 안에 인라인으로 두지 않는다** — 그러면 이 셋이 실제로 자식에게 가는지를
    재려면 전체 게이트를 돌려야 하고, 그건 스위트 안에서 못 한다(`test_check_all.py`
    머리말). 함수 하나면 `test_config_hygiene` 이 **대조군까지 두고** 잰다.
    """
    # ⚠ `isolate_config()` 는 이 프로세스의 `os.environ` 도 함께 세운다(멱등). 그래야
    #   자식이 물려받고, `tests/run.py` 는 표식을 보고 **같은 파일을 재사용**한다 —
    #   스텝마다 다른 빈 파일이 서면 "게이트가 무엇을 읽고 돌았나"가 스텝마다 달라진다.
    hermetic.isolate_config()

    env = dict(os.environ)
    env.pop("NO_COLOR", None)

    cargo_dir = find_cargo()
    if cargo_dir and cargo_dir not in env.get("PATH", "").split(os.pathsep):
        env["PATH"] = cargo_dir + os.pathsep + env.get("PATH", "")

    if sys.executable:
        env["PYTHON"] = sys.executable
    return env


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

    env = child_env()   # 자식이 물려받을 것은 전부 저기 한 곳에서 온다(위 함수 머리말).

    print(f"합본 게이트 — {len(todo)}단계" + (" (--fast)" if args.fast else ""), flush=True)
    failures, skipped = [], []
    for step in todo:
        reason = step.needs()
        if reason:
            skipped.append((step.name, reason, step.skip_ok))
            print(f"  … {step.name:<14} SKIP  ({reason})", flush=True)
            continue
        # ★ **시작할 때 찍는다**(pytmux-194). 종전에는 끝나야 한 줄이 났고, 파이프로
        #   받으면 버퍼링까지 겹쳐 **파일 크기가 0바이트**였다 — 어느 스텝에서 막혔는지
        #   알아내려고 `ps` 로 프로세스 트리를 떠야 했다.
        # ⛔ 터미널일 때만 제자리 갱신(`\r`)하는 재주는 안 부린다 — 그러면 사람이 보는
        #   것과 파일에 남는 것이 달라지고, 이 게이트의 로그는 대개 파일로 읽힌다.
        secs_cap = step_timeout(step)
        print(f"  ..  {step.name:<14} 도는 중"
              + ("" if secs_cap is None else f" (시한 {secs_cap:.0f}초)"), flush=True)
        out, verdict, secs = run(step, env, timeout=secs_cap)
        mark = "OK  " if verdict is None else "FAIL"
        print(f"  {mark} {step.name:<14} {secs:6.1f}s"
              + ("" if verdict is None else f"  — {verdict}"), flush=True)
        if verdict is not None:
            failures.append((step.name, verdict, out))

    print()
    # ☠ **SKIP 은 rc 0 이 아니다**(검수 2026-09-05 C-3). 종전에는 Git Bash 를 못 찾으면
    # 셋이, cargo 를 못 찾으면 여섯이 조용히 빠지고도 「✓ N단계 전부 통과」 + rc 0 이었다
    # — 스크립트로 이 게이트를 무는 자리(CI·훅)는 그것을 초록으로 읽는다. `qa/run.py` 는
    # 같은 상황을 이미 rc 3(미검증이 남았다)으로 가르고 있었고, 갈라져 있던 것이 결함이다.
    unmeasured = unmeasured_skips(skipped)
    if skipped:
        # 조용한 SKIP 은 "다 돌았다"로 읽힌다 — 무엇을 못 쟀는지 반드시 남긴다.
        print(f"건너뜀 {len(skipped)}: "
              + " · ".join(f"{n}({r})" for n, r, _ in skipped))
    if not failures:
        if unmeasured:
            print(f"⚠ {len(todo) - len(skipped)}단계 통과 · "
                  f"{len(unmeasured)}단계는 **못 쟀다** — 통과가 아니다(rc 3): "
                  + " · ".join(n for n, _ in unmeasured))
            return 3
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
