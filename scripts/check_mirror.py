#!/usr/bin/env python3
"""게이트 — 공개 미러에 **올라가야 할 것이 올라가고, 올라가면 안 될 것이 안 올라가나**.

트리 통합 계획 §5.3(D4 첫 푸시 체크리스트)을 사람이 기억하는 목록에서 **실행 가능한
게이트**로 옮긴 것이다. 목록으로 두면 첫 푸시에만 확인하고 그 뒤로는 아무도 안 본다 —
그런데 이 항목들은 전부 **조용히 썩는다**:

- `client/target/`(7GB대) 이 무시 목록에서 빠지면 첫 커밋이 통째로 부푼다.
- 상류 자산이 다시 들어오면 파일 하나가 GitHub 한도(100MB)를 넘길 수 있다.
- 리댁션 전 `client/docs/` 가 규칙 한 줄이 사라지는 것만으로 공개된다(그림에 실
  사용자·호스트·계정이 찍혀 있다). 되돌릴 수 없는 방향이라 게이트가 붙든다.
- 라이브 캡처를 굳힌 픽스처에 이 상자의 실 경로가 섞여 들어온다 — 사람 눈에는 diff
  로만 보이고 한 번 푸시하면 히스토리에 남는다(첫 푸시 준비에서 실제로 둘 걸렸다).

판정은 **양성·음성 둘 다** 본다: "무시된다"만 재면 규칙이 전부 사라져도 통과한다.
"""

import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# GitHub 의 하드 한도는 100MB 다. 그 앞에서 멈추게 여유를 둔다 — 한도에 닿은 뒤에는
# 히스토리를 다시 쓰는 것 말고 되돌릴 길이 없다.
BIG_FILE_MB = 50

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


def ignored(path):
    """git 이 이 경로를 무시하나. 저장소가 아니면 `None`."""
    proc = subprocess.run(
        ["git", "check-ignore", "-q", path],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode not in (0, 1):
        return None
    return proc.returncode == 0


# 이 상자에서만 나오는 문자열들. 값이 아니라 **모양**으로 잡는다 — 계정 이름 하나를
# 목록에 박으면 다음 상자의 다른 이름은 그냥 지나간다.
#
# 잡는 것은 **홈 아래 개발 경로** 둘이다: 워크스페이스(depot 구조)와 빌드 도구 홈
# (`~/.cargo`·`~/.rustup`). 후자가 실측으로 훨씬 크다 — 릴리스 이진에 박힌 절대경로는
# 워크스페이스 16·21건인 데 비해 cargo 레지스트리가 329·1013건이었다(2026-08-01).
# 자리표시자(`/Users/me`·`C:\Users\me` 류)는 뺀다 — 문서·테스트가 경로 규칙을 설명할 때
# 쓰는 모양이고, 그것까지 물면 아래 「소음이 된 게이트」와 같은 길을 간다.
_NOT_PLACEHOLDER = r"(?!me\b|x\b|test\b|user\b|someone\b|<)"
LEAK_PATTERNS = (
    (re.compile(r"/Users/" + _NOT_PLACEHOLDER + r"[A-Za-z0-9._-]{2,}"
                r"/(?:p4|perforce|\.cargo|\.rustup)\b"),
     "macOS 실 홈의 워크스페이스·빌드도구 경로"),
    # Linux 도 같은 모양으로 잰다 — 2026-08-01 부터 `pytmux-gui-linux-x64` 를 CI 에서
    # 굽는다(`.github/workflows/release-binaries.yml`). 러너 홈은 `/home/runner` 라
    # remap 이 빠지면 그 경로가 이진에 박힌다. 재는 자가 OS 마다 다르면 그 OS 만
    # 사각지대가 된다 — 이번에 `.exe` 로 정확히 그것을 겪었다.
    (re.compile(r"/home/" + _NOT_PLACEHOLDER + r"[A-Za-z0-9._-]{2,}"
                r"/(?:p4|perforce|\.cargo|\.rustup)\b"),
     "Linux 실 홈의 워크스페이스·빌드도구 경로"),
    (re.compile(r"[A-Za-z]:[\\/]{1,2}perforce[\\/]{1,2}", re.I),
     "Windows depot 절대경로"),
    (re.compile(r"[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}" + _NOT_PLACEHOLDER +
                r"[A-Za-z0-9._-]{2,}[\\/]{1,2}\.(?:cargo|rustup)\b", re.I),
     "Windows 실 홈의 빌드도구 경로"),
)
# **머신 이름은 일부러 안 잰다.** 처음엔 넣었는데 정본 14파일을 물었다 — `office1` 은
# 원격 페더레이션의 예시 호스트로 문서·테스트에 이미 공개돼 있다. 선재 적색만 만드는
# 규칙은 게이트가 아니라 소음이고, 소음이 된 게이트는 곧 꺼진다. 이름 하나가 새는 것과
# **계정·홈·depot 구조**가 새는 것은 값이 다르다 — 위 셋이 후자다. 스크린샷 쪽 호스트명
# 노출은 ② 가 디렉토리째 막는다.


def _scan(path):
    """파일 하나에서 유출 문자열을 찾는다 → 사유 또는 `None`.

    **확장자로 거르지 않는다.** 종전에는 이진·그림을 `SKIP_EXT` 로 건너뛰었는데, 그
    목록이 정확히 사각지대를 만들었다: 배포 이진 둘이 나란히 실 경로를 품고 있었지만
    `.exe` 는 목록에 있어 조용히 통과하고 확장자 없는 macOS 것만 걸렸다(2026-08-01).
    확장자 유무가 판정을 가르면 그것은 게이트가 아니다. rustc 가 박는 경로는 UTF-8
    이라 바이트를 `latin-1` 로 펴면 ASCII 부분이 그대로 남는다 — 텍스트·이진 한 길로 잰다.
    (그림·압축물은 규칙이 안 맞아 그냥 지나간다. 값은 못 재는 것이 아니라 헛도는 것뿐이다.)
    """
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("latin-1")
    except OSError:
        return None
    for pat, why in LEAK_PATTERNS:
        if pat.search(text):
            return why
    return None


def _leaks():
    """미러로 갈 파일에서 이 상자 고유 문자열을 찾는다 → `(경로, 사유)` 목록."""
    proc = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=ROOT, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return [("(git ls-files)", "미러 대상 목록을 못 얻었다 — 못 쟀으면 통과가 아니다")]
    found = []
    for rel in proc.stdout.split("\0"):
        if not rel:
            continue
        why = _scan(os.path.join(ROOT, rel))
        if why:
            found.append((rel, why))
    return found


def scan_paths(paths):
    """`--scan` — 지정한 파일만 잰다(git 추적 여부와 무관).

    `client/scripts/build_release.*` 가 갓 구운 이진을 `build/` 에 넣기 **전에** 부른다.
    유출을 미러 문턱에서만 재면 이미 depot 에 들어간 뒤라 되돌리는 값이 커진다 —
    산출 지점에서 같은 자를 대는 편이 싸다(그리고 자가 한 벌이라 갈라지지 않는다).
    """
    bad = [(p, why) for p in paths for why in [_scan(p)] if why]
    for path, why in bad:
        print(f"FAIL: {path} — {why}")
    if bad:
        return 1
    print(f"OK: 유출 문자열 0 ({len(paths)}개 파일)")
    return 0


def docs_verdict(present, is_ignored, canon):
    """② `client/docs/` 판정. `(kind, msg)` — kind 는 `"problem"|"skip"|None`.

    셋을 가른다:

    - **있는데 안 무시된다** → 문제. 리댁션 전 그림에는 실 사용자·호스트·계정이
      찍혀 있다(`.gitignore` 의 `/client/docs/` 주석). 계획 §4.4-2 는 리포트를 공개
      대상으로 적었고 이 자리도 원래 "올라가나"를 쟀는데, 첫 푸시 직전에 실물을 보고
      방향을 **뒤집었다** — 한시 보류를 게이트가 안 붙들면 "리댁션했다고 생각하고"
      아무 때나 새는 쪽으로 되돌아간다. **푸는 것도 여기서** 한다: 그림을 리댁션·
      재생성한 CL 이 이 판정과 `.gitignore` 규칙을 같이 뒤집는다.
    - **없는데 정본 워크스페이스다** → 문제. 잴 것이 없으면 통과가 아니라 고장이다.
    - **없는데 미러 체크아웃이다** → 건너뜀. 거기서는 없는 것이 **정답**이다 —
      없다고 실패시키면 "제외가 성공한 것"을 고장으로 읽는다(2026-08-01 실측: 첫
      푸시 뒤 rust-client `gates` 가 정확히 그렇게 붉었다).

    두 자리를 가르는 표식이 `canon`(= `docs/internal/` 의 존재)이다. 그것도 p4
    전용이라 미러 체크아웃엔 `client/docs/` 와 **같이** 없다.
    """
    docs = "client/docs"
    if present:
        if not is_ignored:
            return ("problem",
                    f"{docs}/ 가 미러에 올라간다 — 리댁션 전 그림에는 실 사용자·호스트·"
                    "계정이 찍혀 있다(`.gitignore` 의 `/client/docs/` 주석). 리댁션"
                    " 슬라이스가 이 판정과 규칙을 **같은 CL 에서** 뒤집을 것")
        return (None, "")
    if canon:
        return ("problem", f"{docs}/ 가 없다 — 잴 것이 없으면 통과가 아니라 고장이다")
    return ("skip",
            f"② {docs}/ — 미러 체크아웃이라 잴 것이 없다(정본 워크스페이스에서만 잰다)")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--scan":
        return scan_paths(sys.argv[2:])
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        print("SKIP: git 저장소가 아니다 — 미러 위생은 잴 것이 없다")
        return 0
    if not os.path.isdir(os.path.join(ROOT, "client")):
        print("SKIP: client/ 가 없다")
        return 0

    problems = []
    # 건너뛴 판정은 **사유와 함께** 남긴다 — 조용히 건너뛰면 "쟀다"와 구분이 안 된다
    # (루트 CLAUDE.md 의 합본 게이트 규약과 같은 결).
    skipped = []

    # ① 무거운 산출물은 안 올라간다.
    #
    # 종전에는 `client/build/pytmux-client-tui.exe` 도 함께 셌는데, 그 이진은 2026-08-01
    # 에 지웠다(Rust TUI 퇴역) — 없는 파일을 이름으로 가리키는 규칙은 **아무것도 안 재면서
    # 재는 척**을 한다. 지운다. `build/` 의 배포 이진은 이제 일부러 미러에 싣고(69022),
    # 부푸는 쪽은 ④(50MB 문턱)와 ⑤(그 안에 무엇이 적혔나)가 잡는다.
    for rel in ("client/target",):
        if os.path.exists(os.path.join(ROOT, rel)) and ignored(rel) is False:
            problems.append(f"{rel} 이 무시되지 않는다 — 첫 커밋이 통째로 부푼다")

    # ② `client/docs/` 는 **안 올라간다** — 판정은 `docs_verdict` 한 곳에 있다.
    kind, msg = docs_verdict(
        present=os.path.isdir(os.path.join(ROOT, "client", "docs")),
        is_ignored=bool(ignored("client/docs")),
        canon=os.path.isdir(os.path.join(ROOT, "docs", "internal")),
    )
    if kind == "problem":
        problems.append(msg)
    elif kind == "skip":
        skipped.append(msg)

    # ③ MIT 경계의 기록이 함께 올라간다.
    for rel in ("client/PROVENANCE.md", "client/LICENSE-MIT"):
        if not os.path.isfile(os.path.join(ROOT, rel)):
            problems.append(f"{rel} 이 없다 — 가져온 코드의 라이선스 근거가 사라졌다")
        elif ignored(rel):
            problems.append(f"{rel} 이 무시된다 — 미러에 라이선스 없이 코드만 올라간다")

    # ④ GitHub 한도에 닿을 파일. 무시되는 것은 어차피 안 올라가므로 뺀다.
    big = []
    for base, dirs, files in os.walk(os.path.join(ROOT, "client")):
        dirs[:] = [d for d in dirs if d not in (".git", "target")]
        for name in files:
            path = os.path.join(base, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            if size > BIG_FILE_MB * 1024 * 1024:
                rel = os.path.relpath(path, ROOT)
                if not ignored(rel):
                    big.append((rel, size / 1024 / 1024))
    for rel, mb in big:
        problems.append(f"{rel} 이 {mb:.1f}MB — GitHub 한도(100MB) 앞이다. 미러에서 뺄지 정할 것")

    # ⑤ 미러로 갈 파일 안에 이 상자의 실 경로·계정이 섞였나.
    #
    # ①~④ 는 "무엇이 올라가나"를 재고 이것은 "무엇이 그 안에 적혀 있나"를 잰다. 첫 푸시
    # 준비에서 실제로 넷이 걸렸다 — 라이브 캡처를 그대로 굳힌 픽스처 두 개에 depot 경로가
    # 통째로 들어 있었고(`prompt_box*.json`), `build/` 의 배포 이진 **둘 다** rustc 가 박은
    # 절대 소스경로를 품고 있었다(`--remap-path-prefix` 없이 구운 것 — 굽는 법은
    # `client/scripts/build_release.*`). 사람 눈으로는 못 잡는다: 픽스처는 리뷰에서 내용이
    # 아니라 diff 로만 보이고 이진은 diff 조차 안 보이며, 한 번 푸시하면 히스토리에 영구히
    # 남는다.
    #
    # 무시되는 파일은 재지 않는다(`--exclude-standard`) — 그것들은 애초에 안 올라간다.
    # 이메일(`me@woojinkim.org`)은 **일부러 뺐다**: 커밋 작성자로 이미 공개돼 있고
    # `tests/test_server.py` 가 계정 스크랩 오라클로 쓴다(막으면 선재 적색만 만든다).
    for rel, why in _leaks():
        problems.append(f"{rel} — {why}. 미러로 가는 텍스트다(리댁션하거나 무시 목록에 넣을 것)")

    for s in skipped:
        print(f"SKIP: {s}")
    if problems:
        print("FAIL: 공개 미러 위생(계획 §5.3)")
        for p in problems:
            print(f"  · {p}")
        return 1
    print("OK: 미러 위생 — target·docs 제외 · 라이선스 동반 · 큰 파일 없음 · 유출 문자열 0")
    return 0


if __name__ == "__main__":
    sys.exit(main())
