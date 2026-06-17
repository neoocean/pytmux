#!/usr/bin/env python3
"""특정 로그에서 target 문자열을 **깨끗하게** 포함하는 단일 프레임을 뽑는다.

레거시(비-2026) 로그는 프레임 경계가 없어 임의 청크 샘플이 repaint 중간을 잘라
겹쳐 보인다. 여기선 Claude 의 per-frame 재도색 마커(ESC[H 홈)로 분할해 **완성된
repaint 단위**만 누적 피드 → 각 경계에서 렌더하면 겹침 없이 깨끗하다. target 을
담은 프레임 중 '비어있지 않은 줄이 많고(=잘 찬) 제어 잔여가 적은' 것을 고른다."""
from __future__ import annotations
import re
import sys

sys.path.insert(0, ".")
from pytmuxlib.model import Pane                                 # noqa: E402
from pytmuxlib.replay import render_pane_lines, read_capture     # noqa: E402

HOME = re.compile(rb"\x1b\[(?:\?25[lh])?H")   # ESC[H / ESC[?25l H / ESC[?25h H


def clean_frames(data: bytes, cols: int, rows: int, target: str):
    # 홈 마커 앞에서 분할 → 각 조각은 한 repaint. 누적 피드.
    bounds = [m.start() for m in HOME.finditer(data)]
    segs = []
    prev = 0
    for b in bounds:
        if b > prev:
            segs.append(data[prev:b])
        prev = b
    segs.append(data[prev:])
    pane = Pane(-1, -1, cols, rows)
    best = None
    for seg in segs:
        if not seg:
            continue
        try:
            pane.feed(seg)
        except Exception:   # noqa: BLE001
            continue
        lines = [ln.rstrip() for ln in render_pane_lines(pane)]
        txt = "\n".join(lines)
        if target.lower() in txt.lower():
            nonempty = sum(1 for ln in lines if ln.strip())
            # 깨짐 점수: 한국어/영문 사이 잡제어 잔여가 적을수록↑ — 근사로 '한 줄에
            # target 이 온전히(자르지 않고) 들어간' 프레임 우선.
            whole = any(target.lower() in ln.lower() for ln in lines)
            score = (1 if whole else 0, nonempty)
            if best is None or score > best[0]:
                best = (score, txt)
    return best[1] if best else None


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("target")
    ap.add_argument("--cols", type=int, default=100)
    ap.add_argument("--rows", type=int, default=30)
    ap.add_argument("--max-bytes", type=int, default=16 * 1024 * 1024)
    a = ap.parse_args()
    data = read_capture(a.file)[:a.max_bytes]    # .gz 투명
    out = clean_frames(data, a.cols, a.rows, a.target)
    print(out if out else "NONE")
