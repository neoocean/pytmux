#!/usr/bin/env python3
"""트랜스크립트 폴더 이름 인코딩 픽스처 — 클라와 서버가 같은 폴더를 보게 묶는다.

Claude Code 는 세션 기록을 작업 디렉터리별 폴더에 넣고, 그 폴더 이름은 절대경로를
인코딩한 것이다(`/`·`.` → `-`). pytmux 서버는 이미 그 규칙을 쓰고 있고
(`plugins/claude-code/transcript.py::encode_project_dir`), 네이티브 클라도 같은 규칙으로
폴더를 찾는다(`claude::discover`).

**한 글자만 달라도 폴더를 못 찾는다.** 그리고 그 실패는 조용하다 — 빈 목록은 "이 패널엔
Claude 세션이 없다"와 구분되지 않는다. 그래서 파이썬 구현으로 표를 뽑아 두고 Rust
테스트가 자기 구현과 대조한다.

    python3 scripts/gen_transcript_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "claude", "tests", "fixtures",
                   "project_dirs.json")

# 경계를 겨눈 표본: 점이 든 경로·숨김 폴더·공백·한글·끝 슬래시·중첩 점.
#
# **POSIX 표기이지만 POSIX 머신 전용이 아니다** — 아래 WIN_CASES 와 같은 이유로 순수
# 인코더로 뽑는다(2026-07-28). 종전에는 `encode_project_dir`(= abspath + 인코딩)를
# 태웠고, "이미 절대경로라 abspath 는 값에 영향이 없다"고 적혀 있었다. 그 문장은
# **POSIX 에서만 참**이다: Windows 의 `os.path.abspath("/Users/me/x")` 는 현재 드라이브를
# 붙여 `D:\Users\me\x` 를 만들고, 그래서 Windows 에서 생성기를 돌리면 픽스처가
# `D--Users-me-x` 로 바뀐다(적합성 테스트가 즉시 붉어진다 — 조용히 썩지는 않았다).
# 대조 상대인 러스트 `encode_project_dir` 도 **abspath 를 안 한다**(순수 치환).
CASES = [
    "/Users/me/p4/playground/scripts",
    "/Users/me/.config/nvim",
    "/tmp/a.b.c/d",
    "/Users/me/작업 폴더/프로젝트",
    "/",
    "/Users/me/repo.git/worktree",
]

# Windows 표기 표본(2026-07-27g). POSIX 생성 머신에서 `os.path.abspath` 를 태우면 이
# 값들이 로컬 cwd 에 붙어 버리므로 **순수 인코더**(encode_project_name)로 뽑는다 —
# 검증하려는 것은 "구분자·콜론이 이름에 남지 않는가"이지 abspath 규칙이 아니다.
WIN_CASES = [
    "C:\\Users\\me\\proj",
    "D:\\work\\a.b\\c",
    "\\\\server\\share\\dir",          # UNC
    "C:\\",
]


def _utf8_stdout():
    """Windows 콘솔의 기본 코드페이지(한국어=cp949)에서 한글 출력이 죽지 않게.

    이 스크립트들은 마지막에 결과 요약을 한글로 찍는다. cp949 콘솔에서는 그 print 가
    UnicodeEncodeError 로 죽는데, **파일은 이미 다 쓴 뒤**라 종료코드 1 만 보고
    "생성 실패"로 오인하게 된다(2026-07-28 실측: 생성기 6개 전부 그랬다).
    출력 스트림만 UTF-8 로 돌린다 — 생성 결과에는 영향이 없다.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    _utf8_stdout()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()

    root = os.path.abspath(args.pytmux)
    if not os.path.isdir(root):
        sys.exit(f"pytmux 저장소를 못 찾았다: {root}")
    sys.path.insert(0, root)
    import importlib
    transcript = importlib.import_module("pytmuxlib.plugins.claude-code.transcript")

    # 두 표본군 모두 **순수 인코더**로 뽑는다 — 어느 OS 에서 돌려도 같은 값이 나오고,
    # 대조 상대인 러스트 `encode_project_dir` 이 하는 일과도 같다(위 CASES 주석 참조).
    pairs = {}
    for cwd in CASES + WIN_CASES:
        pairs[cwd] = transcript.encode_project_name(cwd)
    payload = {
        "_comment": "python3 scripts/gen_transcript_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/plugins/claude-code/transcript.py::encode_project_dir",
        "dirs": pairs,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 표본 {len(pairs)}개")


if __name__ == "__main__":
    main()
