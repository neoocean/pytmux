#!/usr/bin/env python3
"""벤치마크 추세 SVG 그래프 생성기.

docs/benchmark/<os-slug>/*.json 를 읽어 **일별 중앙값(median)** 시계열을 뽑고,
의존성 없이(matplotlib 불요) 손수 그린 멀티라인 SVG 차트를 docs/internal/image/ 에
쓴다. 리포트(BENCHMARK_TRENDS_*.md)가 이 이미지를 참조한다.

사용:  python scripts/bench_trends_svg.py [--outdir docs/internal/image]
"""
from __future__ import annotations
import argparse, glob, json, os, statistics
from collections import defaultdict

OS_SLUGS = ["linux-x86_64", "darwin-arm64", "windows-amd64"]
# 색: linux 파랑, darwin 주황, windows 초록 (색약 고려 대비)
OS_COLOR = {"linux-x86_64": "#1f77b4", "darwin-arm64": "#ff7f0e", "windows-amd64": "#2ca02c"}
OS_LABEL = {"linux-x86_64": "linux", "darwin-arm64": "darwin", "windows-amd64": "windows"}


def _case(js, name_sub):
    for c in js.get("output_flood", {}).get("cases", []):
        if name_sub in c.get("name", ""):
            return c
    return None


def extract(js, metric):
    """metric 키 → 단일 수치 (없으면 None)."""
    if metric == "cold_import_p50":
        return js["startup"]["cold_import_ms"]["p50"]
    if metric == "framework_init_p50":
        return js["startup"]["framework_init_ms"]["p50"]
    if metric == "render_all_p50":
        return js["tabs_panes"]["render_all_ms"]["p50"]
    if metric == "tab_switch_p50":
        return js["tabs_panes"]["tab_switch_ms"]["p50"]
    if metric == "busy_frame_ms":
        c = _case(js, "claude_busy 200")
        return c["render_ms_frame"] if c else None
    if metric == "feed_mb_s":
        c = _case(js, "claude_busy 200")
        return c["feed_mb_s"] if c else None
    if metric == "plaincat_slice_p99":
        c = _case(js, "plain_cat 200")
        return c["slice_p99_ms"] if c else None
    raise KeyError(metric)


def daily_median(bench_root, metric):
    """{os_slug: [(day_str, median_val), ...]} (day 오름차순)."""
    out = {}
    for slug in OS_SLUGS:
        per_day = defaultdict(list)
        for f in sorted(glob.glob(os.path.join(bench_root, slug, "*.json"))):
            day = os.path.basename(f)[:8]  # YYYYMMDD
            try:
                with open(f) as fh:
                    v = extract(json.load(fh), metric)
            except (KeyError, json.JSONDecodeError, TypeError):
                v = None
            if v is not None:
                per_day[day].append(v)
        series = [(d, statistics.median(per_day[d])) for d in sorted(per_day)]
        out[slug] = series
    return out


# ---------- SVG ----------
import math

W, H = 760, 440
ML, MR, MT, MB = 64, 150, 48, 56  # margins (오른쪽 넓게: 범례)
PW, PH = W - ML - MR, H - MT - MB


def _x(i, n):
    return ML + (PW * (i / (n - 1)) if n > 1 else PW / 2)


def _nice_ticks(lo, hi, n=5):
    if lo == hi:
        return [lo]
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            step = m * mag
            break
    start = math.floor(lo / step) * step
    ticks = []
    v = start
    while v <= hi + step * 0.5:
        if v >= lo - step * 0.5:
            ticks.append(round(v, 10))
        v += step
    return ticks


def make_chart(series_by_os, title, ylabel, outpath, logy=False, unit=""):
    # 공통 x 축: day 라벨 합집합
    all_days = sorted({d for s in series_by_os.values() for d, _ in s})
    n = len(all_days)
    day_idx = {d: i for i, d in enumerate(all_days)}

    vals = [v for s in series_by_os.values() for _, v in s if v is not None]
    if not vals:
        return
    vmin, vmax = min(vals), max(vals)

    if logy:
        vmin = max(vmin, 1e-3)
        lo, hi = math.log10(vmin), math.log10(vmax)
        pad = (hi - lo) * 0.08 or 0.2
        lo -= pad; hi += pad
        def y(v): return MT + PH * (1 - (math.log10(max(v, 1e-3)) - lo) / (hi - lo))
        # 10의 거듭제곱 + 중간 눈금
        ticks = []
        e = math.floor(lo)
        while e <= math.ceil(hi):
            for m in (1, 3):
                tv = m * 10 ** e
                if 10 ** lo <= tv <= 10 ** hi:
                    ticks.append(tv)
            e += 1
    else:
        span = vmax - vmin
        lo = max(0, vmin - span * 0.1)
        hi = vmax + span * 0.12
        if hi == lo:
            hi = lo + 1
        def y(v): return MT + PH * (1 - (v - lo) / (hi - lo))
        ticks = _nice_ticks(lo, hi)

    P = []
    P.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
             f'viewBox="0 0 {W} {H}" font-family="-apple-system,Segoe UI,Helvetica,Arial,sans-serif">')
    P.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
    P.append(f'<text x="{ML}" y="26" font-size="16" font-weight="700" fill="#1a1a1a">{title}</text>')
    P.append(f'<text x="{ML}" y="42" font-size="11" fill="#666">{ylabel}'
             + (' · 로그 스케일' if logy else '') + ' · 일별 중앙값</text>')

    # y 그리드 + 라벨 (플롯 영역 밖 눈금은 버림)
    for t in ticks:
        yy = y(t)
        if yy < MT - 0.5 or yy > MT + PH + 0.5:
            continue
        P.append(f'<line x1="{ML}" y1="{yy:.1f}" x2="{ML+PW}" y2="{yy:.1f}" stroke="#e8e8e8" stroke-width="1"/>')
        lbl = (f'{t:g}')
        P.append(f'<text x="{ML-8}" y="{yy+4:.1f}" font-size="10" fill="#888" text-anchor="end">{lbl}</text>')

    # x 축 라벨 (MM-DD, 격자 줄이려 짝수 인덱스만 텍스트)
    for d, i in day_idx.items():
        xx = _x(i, n)
        if i % 2 == 0 or i == n - 1:
            mmdd = f'{d[4:6]}-{d[6:8]}'
            P.append(f'<text x="{xx:.1f}" y="{MT+PH+18}" font-size="9.5" fill="#888" '
                     f'text-anchor="middle">{mmdd}</text>')
    # 축선
    P.append(f'<line x1="{ML}" y1="{MT+PH}" x2="{ML+PW}" y2="{MT+PH}" stroke="#bbb" stroke-width="1.2"/>')
    P.append(f'<line x1="{ML}" y1="{MT}" x2="{ML}" y2="{MT+PH}" stroke="#bbb" stroke-width="1.2"/>')

    # 라인 + 점
    for slug in OS_SLUGS:
        s = series_by_os.get(slug, [])
        if not s:
            continue
        col = OS_COLOR[slug]
        pts = [(_x(day_idx[d], n), y(v)) for d, v in s]
        path = " ".join((("M" if k == 0 else "L") + f"{px:.1f} {py:.1f}") for k, (px, py) in enumerate(pts))
        P.append(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="2"/>')
        for px, py in pts:
            P.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="2.4" fill="{col}"/>')

    # 범례 (오른쪽) — 마지막 값 표시
    ly = MT + 6
    for slug in OS_SLUGS:
        s = series_by_os.get(slug, [])
        if not s:
            continue
        col = OS_COLOR[slug]
        last = s[-1][1]
        lx = ML + PW + 16
        P.append(f'<rect x="{lx}" y="{ly-9}" width="14" height="3.2" rx="1.5" fill="{col}"/>')
        P.append(f'<text x="{lx+20}" y="{ly-4}" font-size="11" font-weight="600" fill="#333">{OS_LABEL[slug]}</text>')
        P.append(f'<text x="{lx+20}" y="{ly+9}" font-size="9.5" fill="#999">최신 {last:g}{unit}</text>')
        ly += 30

    P.append('</svg>')
    with open(outpath, "w") as fh:
        fh.write("\n".join(P))
    return outpath


CHARTS = [
    ("cold_import_p50", "콜드 import 시작시간 (p50)", "ms", "bench-cold-import.svg", False, " ms"),
    ("render_all_p50", "정상상태 전체 재렌더 (render_all p50)", "ms", "bench-render-all.svg", True, " ms"),
    ("busy_frame_ms", "claude_busy 200×50 풀리페인트 프레임 비용", "ms/frame", "bench-busy-frame.svg", False, " ms"),
    ("feed_mb_s", "출력 폭증 처리량 (claude_busy 200 feed)", "MB/s", "bench-feed-mbs.svg", False, " MB/s"),
    ("plaincat_slice_p99", "plain_cat 200×50 슬라이스 지연 p99 (회귀 추적)", "ms", "bench-plaincat-p99.svg", False, " ms"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench-root", default="docs/benchmark")
    ap.add_argument("--outdir", default="docs/internal/image")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    for metric, title, ylabel, fname, logy, unit in CHARTS:
        s = daily_median(args.bench_root, metric)
        out = os.path.join(args.outdir, fname)
        make_chart(s, title, ylabel, out, logy=logy, unit=unit)
        print("wrote", out)


if __name__ == "__main__":
    main()
