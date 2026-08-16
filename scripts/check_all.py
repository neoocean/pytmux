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
            "MIT 경계 — 의존 그래프의 로컬 크레이트가 허용 목록과 정확히 같은가",
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


def child_env():
    """모든 스텝이 물려받을 env. **여기가 게이트의 유일한 자식 환경**이다.

    세 가지를 한다. 셋 다 "게이트가 재는 대상을 게이트가 바꾸지 않는다"의 반대편 —
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
