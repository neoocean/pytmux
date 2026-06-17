#!/usr/bin/env python3
"""feed/render 처리량 프로파일러 — §10 "ssh 반응성 급락" 가설 ② 정량화.

실제 Windows→ssh→macOS 박스 없이, 서버의 두 순수-파이썬 핫패스를 격리 측정한다:
  (1) Pane.feed  — 정규식 전처리(_PRIVATE_SGR_RE/_sanitize_sgr/_CSI_PARTIAL_RE/
                   _ALT_RE) + pyte ByteStream.feed
  (2) Pane.render + json.dumps — flush 루프가 dirty 패널마다 매 프레임 수행

핵심 질문:
  - feed 처리량(MB/s)이 §10 이 말한 ~1.2MB/s 천장에 부합하나?
  - FEED_SLICE(8KB) 한 조각이 몇 ms 걸리나? (입력 반응성 = 슬라이스 최대 지연)
  - Claude 풀스크린 리페인트(alt-screen)와 단순 cat(main-screen) 중 무엇이 더 무거운가?
  - 시간은 정규식 전처리 vs pyte 중 어디서 쓰이나? (cProfile)

사용:
  python poc/feed_profile.py                # 기본(claude busy + plain cat, 80x24/200x50)
  python poc/feed_profile.py --mb 20        # 더 큰 합성 스트림
  python poc/feed_profile.py --profile      # cProfile 상위 함수까지
"""
from __future__ import annotations

import argparse
import cProfile
import io
import json
import os
import pstats
import sys
import time

# 저장소 루트(= scripts/poc/ 의 두 단계 위)를 import 경로에 추가해 pytmuxlib 를 쓴다.
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from pytmuxlib.model import Pane                      # noqa: E402
from pytmuxlib.protocol import FEED_SLICE             # noqa: E402

ESC = b"\x1b"
CSI = b"\x1b["


def gen_claude_busy(cols: int, rows: int, frames: int) -> bytes:
    """Claude Code busy 화면 흉내: alt-screen 풀프레임 리페인트 × frames.

    매 프레임마다 alt-screen 을 다 다시 그린다(스피너·토큰 푸터·컬러 본문) —
    Claude 가 작업 스피너/토큰 카운터를 고fps 로 풀스크린 리페인트한다는 §10
    가설의 워크로드. 커서 이동 + SGR(색·굵게) + 텍스트가 빽빽하다.
    """
    out = bytearray()
    out += CSI + b"?1049h"                 # alt-screen 진입(한 번)
    spin = "|/-\\"
    for f in range(frames):
        out += CSI + b"H"                  # 커서 홈
        out += CSI + b"2J"                 # 화면 클리어(풀 리페인트)
        for y in range(rows - 1):
            # 줄마다 색을 바꿔가며(SGR 다발) 본문 그리기
            fg = 31 + (y % 7)
            out += CSI + f"{y + 1};1H".encode()
            out += CSI + f"1;{fg}m".encode()           # bold + color
            body = f"  line {y:3d}  the quick brown fox jumps over {f}"
            body = body[:cols].ljust(min(cols, 60))
            out += body.encode()
            out += CSI + b"0m"                          # reset
        # busy 푸터: 스피너 + 토큰 카운터(자주 바뀜)
        out += CSI + f"{rows};1H".encode()
        out += CSI + b"2;36m"
        foot = f" {spin[f % 4]} Working… (↑ {f * 13 % 9000} tokens · esc to interrupt)"
        out += foot[:cols].encode()
        out += CSI + b"0m"
    out += CSI + b"?1049l"                 # alt-screen 탈출
    return bytes(out)


def gen_plain_cat(cols: int, rows: int, lines: int) -> bytes:
    """단순 cat 대용량: main-screen 평문 스크롤(가설 ③ — Claude 무관 순수 처리량)."""
    out = bytearray()
    for i in range(lines):
        s = f"{i:7d}  " + ("x" * (cols - 12))
        out += s[:cols].encode() + b"\r\n"
    return bytes(out)


def feed_in_slices(pane: Pane, data: bytes, render_every: int = 0):
    """data 를 FEED_SLICE 단위로 먹이며(서버 _feed_drain 모방) 슬라이스 지연을 잰다.

    render_every>0 이면 N 슬라이스마다 render()+json.dumps 를 호출해(=flush 모방)
    그 비용도 합산한다. 반환: (feed_total_s, slice_times, render_total_s, json_bytes)."""
    slice_times = []
    feed_total = 0.0
    render_total = 0.0
    json_bytes = 0
    pos = 0
    n = len(data)
    i = 0
    while pos < n:
        chunk = data[pos:pos + FEED_SLICE]
        pos += FEED_SLICE
        t0 = time.perf_counter()
        pane.feed(chunk)
        dt = time.perf_counter() - t0
        feed_total += dt
        slice_times.append(dt)
        i += 1
        if render_every and i % render_every == 0:
            t0 = time.perf_counter()
            rows, cursor = pane.render(True)
            payload = json.dumps({"t": "screen", "pane": pane.id,
                                  "rows": rows, "cursor": cursor})
            render_total += time.perf_counter() - t0
            json_bytes += len(payload.encode("utf-8"))
    return feed_total, slice_times, render_total, json_bytes


def pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))
    return s[k]


def run_case(name: str, cols: int, rows: int, data: bytes, render_every: int):
    pane = Pane(pid=-1, fd=-1, cols=cols, rows=rows)
    mb = len(data) / 1e6
    feed_s, slices, render_s, jbytes = feed_in_slices(pane, data, render_every)
    print(f"\n=== {name}  ({cols}x{rows}, {mb:.1f} MB, {len(slices)} slices) ===")
    print(f"  feed:    {feed_s * 1e3:8.1f} ms total   "
          f"{mb / feed_s:6.2f} MB/s   "
          f"slice ms p50={pct(slices, 50) * 1e3:.2f} "
          f"p99={pct(slices, 99) * 1e3:.2f} max={max(slices) * 1e3:.2f}")
    if render_every:
        nrender = len(slices) // render_every
        if nrender:
            print(f"  render:  {render_s * 1e3:8.1f} ms total over {nrender} frames  "
                  f"{render_s / nrender * 1e3:6.2f} ms/frame   "
                  f"json {jbytes / max(1, nrender) / 1024:6.1f} KiB/frame")
    return pane, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mb", type=float, default=10.0,
                    help="대략 합성 스트림 크기(MB), 케이스별")
    ap.add_argument("--cols", type=int, default=200)
    ap.add_argument("--rows", type=int, default=50)
    ap.add_argument("--render-every", type=int, default=4,
                    help="N 슬라이스마다 render+json (flush 모방). 0=끔")
    ap.add_argument("--profile", action="store_true",
                    help="claude_busy feed 에 cProfile 적용")
    args = ap.parse_args()

    # 합성 데이터 — 목표 크기에 맞춰 프레임/줄 수 산정
    busy_one = gen_claude_busy(args.cols, args.rows, 1)
    frames = max(1, int(args.mb * 1e6 / len(busy_one)))
    busy = gen_claude_busy(args.cols, args.rows, frames)

    cat_lines = max(1, int(args.mb * 1e6 / (args.cols + 2)))
    cat = gen_plain_cat(args.cols, args.rows, cat_lines)

    print(f"FEED_SLICE={FEED_SLICE}  (서버 _feed_drain 슬라이스 크기)")
    print("기준: 입력이 부드러우려면 슬라이스 max < ~16ms(60fps), <33ms(30fps).")

    run_case("claude_busy (alt-screen 풀 리페인트)", args.cols, args.rows,
             busy, args.render_every)
    run_case("plain_cat   (main-screen 스크롤)", args.cols, args.rows,
             cat, args.render_every)

    # 작은 80x24 도 비교(원격에서 흔한 크기)
    busy24 = gen_claude_busy(80, 24, max(1, int(args.mb * 1e6 /
                             len(gen_claude_busy(80, 24, 1)))))
    run_case("claude_busy 80x24", 80, 24, busy24, args.render_every)

    if args.profile:
        print("\n=== cProfile: claude_busy feed (전처리 vs pyte) ===")
        pane = Pane(pid=-1, fd=-1, cols=args.cols, rows=args.rows)
        pr = cProfile.Profile()
        pr.enable()
        pos = 0
        while pos < len(busy):
            pane.feed(busy[pos:pos + FEED_SLICE])
            pos += FEED_SLICE
        pr.disable()
        st = pstats.Stats(pr, stream=sys.stdout)
        st.sort_stats("cumulative").print_stats(18)


if __name__ == "__main__":
    main()
