#!/usr/bin/env python3
"""라이브 입력박스 긁기 픽스처 — **정본을 직접 불러** 기대값을 뽑는다.

# 무엇을 고정하나

파이썬 클라는 작성창(`esc Insert`)을 열 때 활성 패널 프롬프트에 **지금 들어 있는 글**을
인계한다. 그 값을 만드는 것이
`pytmuxlib/plugins/claude-code/claude.py::claude_input_box(lines, wrap, cursor_y)` 이고,
이 스크립트는 그 함수를 **그대로 호출해** `(입력, 기대 출력)` 표를 만든다.

# 왜 손으로 안 적나

이 파서는 **주석마다 실제 결함에서 온 예외**가 붙어 있다 — 모서리 없는 현행 UI ·
busy 중 구획선 없음 · 멀티라인 중간의 빈 줄(`"" in _BOX_TOP` 이 파이썬에서 True 라
거짓 top 이 잡혔다) · 마커 뒤 비분리공백 · 연속 줄 정렬 폭 학습 · 큐 대기 플레이스홀더.
기대값을 손으로 적으면 그 예외를 우리가 다시 해석하는 셈이고, 한 줄이라도 어긋나면
증상은 **"작성창에 이상한 게 딸려 온다"** 다(제보가 여섯 번 있었던 자리다).

정본이 UI 변화를 따라 고쳐지면 **픽스처 diff 에 보인다.**

# 표본은 어디서 오나

⑴ `pytmux/tests/fixtures/claude/*.txt` — **실제 Claude 화면**을 떠 둔 것. 정본 파서의
   회귀도 이 파일들로 돈다.
⑵ 아래 `SYNTHETIC` — 정본 주석이 이름을 든 **경계 사례**들. 실 화면 표본에는 빈 입력칸만
   있어서(그 자체도 사례다) 멀티라인·구 UI·플레이스홀더가 안 들어온다.

    python3 scripts/gen_prompt_box_fixture.py [--pytmux ..]
"""

import argparse
import importlib.util
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "prompt_box.json")

# 프롬프트 마커 — 커서 행을 못 알 때 앵커로 쓴다(정본 `_PROMPT_MARK` 와 같은 짝).
MARKS = (">", "❯")

# ── 경계 사례(정본 주석이 이름을 든 것들) ─────────────────────────────────────
#
# `cursor` 가 None 이면 "커서 행 미상" — 정본은 그때 아래에서부터 앵커를 찾는다.
SYNTHETIC = [
    {
        "name": "현행 UI · 구획선만 · 한 줄",
        "lines": ["대화 본문", "────────────", "❯ hello", "────────────",
                  "  ? for shortcuts"],
        "wrap": [],
        "cursor": 2,
    },
    {
        # ★ 2026-07-16 제보: 멀티라인 프롬프트 **중간의 빈 줄**이 거짓 top 으로 잡혀
        # 커서 줄만 인계됐다(`"" in _BOX_TOP` 가 파이썬에서 True 다).
        "name": "현행 UI · 멀티라인 · 중간 빈 줄",
        "lines": ["────────────", "❯ 첫 줄", "", "  셋째 줄", "────────────"],
        "wrap": [],
        "cursor": 3,
    },
    {
        "name": "현행 UI · 멀티라인 · 연속 줄 정렬 폭 학습",
        "lines": ["────────────", "❯ 첫 줄", "  둘째 줄", "      들여쓴 줄",
                  "────────────"],
        "wrap": [],
        "cursor": 2,
    },
    {
        # ★ 정렬 폭이 **2가 아닌** 경우. 이 사례가 없으면 폭을 2로 못박아도 표가
        # 초록이다 — 실제로 그렇게 짜다가 **변이로 잡았다**(2026-07-30).
        #
        # 현행 UI 는 세로 테두리가 없어 **밖의 패딩 한 칸이 안쪽에 그대로 남고**,
        # 마커 뒤는 마커 전용 공백(U+00A0)이다. 그래서 첫 줄이 소모하는 폭은 3칸이고
        # 연속 줄도 3칸 들여쓰여진다. 2로 못박으면 둘째 줄부터 공백 한 칸이 붙어
        # 나오고, 그것이 2026-07-16 제보다.
        "name": "현행 UI · 정렬 폭이 3인 경우(밖의 패딩 + 마커 공백)",
        "lines": ["────────────", " ❯ 첫 줄", "   둘째 줄", "────────────"],
        "wrap": [],
        "cursor": 1,
    },
    {
        # soft-wrap 은 **개행 없이** 이어 붙는다 — 하드 개행과 갈리는 유일한 자리다.
        "name": "현행 UI · soft-wrap 은 개행 없이 잇는다",
        "lines": ["────────────", "❯ 앞부분", "  뒤부분", "────────────"],
        "wrap": [2],
        "cursor": 1,
    },
    {
        "name": "구 UI · 모서리 박스 · 세로 테두리",
        "lines": ["╭──────────╮", "│ > hello  │", "╰──────────╯"],
        "wrap": [],
        "cursor": 1,
    },
    {
        "name": "구 UI · 모서리 박스 · 멀티라인",
        "lines": ["╭──────────╮", "│ > 첫 줄  │", "│   둘째   │", "╰──────────╯"],
        "wrap": [],
        "cursor": 1,
    },
    {
        # busy·큐 대기 중에는 구획선을 **안 그린다** → 마커로 논리 블록을 찾는다.
        "name": "구획선 없음 · 프롬프트 블록",
        "lines": ["⏺ 뭔가 하는 중", "", "❯ 첫 줄", "  둘째 줄", "",
                  "  esc to interrupt"],
        "wrap": [],
        "cursor": 2,
    },
    {
        # 커서가 마커 없는 줄(스피너 등)에 얹히면 **긁지 않는다**(None).
        "name": "구획선 없음 · 마커 없는 줄에 커서",
        "lines": ["⏺ 뭔가 하는 중", "", "  ✳ Thinking…", "", "  esc to interrupt"],
        "wrap": [],
        "cursor": 2,
    },
    {
        # ★ 2026-07-18 제보: 이 안내문이 작성창 시드로 딸려 왔다.
        "name": "큐 대기 플레이스홀더는 빈 입력이다",
        "lines": ["────────────", "❯ Press up to edit queued messages",
                  "────────────"],
        "wrap": [],
        "cursor": 1,
    },
    {
        "name": "구 마커(>) + 공백",
        "lines": ["────────────", "> hello", "────────────"],
        "wrap": [],
        "cursor": 1,
    },
    {
        "name": "빈 입력칸",
        "lines": ["────────────", "❯", "────────────"],
        "wrap": [],
        "cursor": 1,
    },
    {
        "name": "커서 행 미상 — 아래에서 앵커를 찾는다",
        "lines": ["────────────", "❯ hello", "────────────",
                  "  ? for shortcuts"],
        "wrap": [],
        "cursor": None,
    },
    {
        "name": "빈 화면",
        "lines": [],
        "wrap": [],
        "cursor": None,
    },
]


def _utf8_stdout():
    """cp949 콘솔에서 한글 요약 print 가 죽지 않게(다른 생성기와 같은 처방)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_canonical(root):
    """플러그인 디렉터리 이름에 `-` 가 있어 보통 import 로는 못 불러온다 — 파일 경로로
    직접 로드한다. 못 읽으면 **실패**로 떨어뜨린다(빈 픽스처는 통과가 아니라 고장이다)."""
    path = os.path.join(root, "pytmuxlib", "plugins", "claude-code", "claude.py")
    if not os.path.isfile(path):
        sys.exit(f"정본을 못 찾았다: {path}")
    spec = importlib.util.spec_from_file_location("cc_claude", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "claude_input_box"):
        sys.exit(f"{path} 에 claude_input_box 가 없다 — 이름이 바뀌었으면 여기도 고칠 것")
    return module


def _live_sample(here):
    """라이본 추가 표본 — **살아 있는 서버가 보낸 프레임**을 그대로 뜨 것.

    합성 표본은 우리가 지은 줄이다 — 오른쪽 패딩도 없고 `wrap` 집합도 보통 비어 있다.
    이 한 건은 겪어 보지 않은 것을 단는다: 서버가 실제로 부이는 폭만큼 채운 행과
    **진짜 `wrap` 값**. 파생을 때료 뜼기지 않게 파일로 굳혀 둔다(라이본 없이도 돌아야 한다).
    """
    path = os.path.join(here, "crates", "proto", "tests", "fixtures",
                        "prompt_box_live.json")
    if not os.path.isfile(path):
        sys.exit(f"라이본 추출본을 못 찾았다: {path}")
    with open(path, encoding="utf-8") as fp:
        cap = json.load(fp)
    return [{"name": "실 서버 프레임(라이본 추출 2026-07-30)",
             "lines": cap["lines"], "wrap": cap["wrap"], "cursor": cap["cursor"]}]


def _screen_samples(root):
    """실제 Claude 화면 표본. 커서 행은 **프롬프트 마커 줄**로 잡는다(라이브에서 커서가
    거기 있다). 마커가 없으면 `None` 으로 두고 정본의 앵커 탐색을 그대로 태운다."""
    dirname = os.path.join(root, "tests", "fixtures", "claude")
    if not os.path.isdir(dirname):
        sys.exit(f"화면 표본 디렉터리를 못 찾았다: {dirname}")
    out = []
    for name in sorted(os.listdir(dirname)):
        if not name.endswith(".txt"):
            continue
        with open(os.path.join(dirname, name), encoding="utf-8") as fp:
            lines = [line.rstrip("\r\n") for line in fp]
        cursor = next((i for i, line in enumerate(lines)
                       if line.lstrip("  ")[:1] in MARKS), None)
        out.append({"name": f"실화면 {name}", "lines": lines, "wrap": [],
                    "cursor": cursor})
    if not out:
        sys.exit(f"{dirname} 에서 표본을 하나도 못 읽었다 — 빈 결과는 고장이다")
    return out


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
    canonical = _load_canonical(root)

    cases = []
    for sample in _screen_samples(root) + _live_sample(here) + SYNTHETIC:
        expected = canonical.claude_input_box(
            sample["lines"], tuple(sample["wrap"]), sample["cursor"])
        cases.append({
            "name": sample["name"],
            "lines": sample["lines"],
            "wrap": sample["wrap"],
            "cursor": sample["cursor"],
            # `None` = 긁을 수 없다(호출부가 초안으로 떨어진다) · `""` = 박스가 실제로 빔.
            # **둘은 다르다** — 하나로 뭉치면 빈 입력칸이 초안을 되살린다.
            "expected": expected,
        })

    payload = {
        "_comment": "python3 scripts/gen_prompt_box_fixture.py 로 생성. 기대값은 "
                    "pytmuxlib/plugins/claude-code/claude.py 의 claude_input_box 를 "
                    "그대로 호출한 결과다(손으로 적은 것이 아니다). 표본 = "
                    "pytmux/tests/fixtures/claude/*.txt(실화면) + 이 스크립트의 "
                    "SYNTHETIC(정본 주석이 이름을 든 경계 사례).",
        "cases": cases,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")

    none_n = sum(1 for c in cases if c["expected"] is None)
    empty_n = sum(1 for c in cases if c["expected"] == "")
    text_n = len(cases) - none_n - empty_n
    print(f"{args.out} — 사례 {len(cases)}개 "
          f"(긁기 불가 {none_n} · 빈 입력 {empty_n} · 글 있음 {text_n})")


if __name__ == "__main__":
    main()
