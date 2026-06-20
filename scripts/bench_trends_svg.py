#!/usr/bin/env python3
"""벤치마크 추세 SVG 그래프 생성기 (matplotlib).

docs/benchmark/<os-slug>/*.json 를 읽어 **일별 중앙값(median)** 시계열을 뽑고,
matplotlib 으로 멀티라인 SVG 차트를 docs/internal/image/ 에 쓴다. 리포트
(BENCHMARK_TRENDS_*.md)가 이 이미지를 참조한다.

한글 깨짐 방지: 한글 폰트(Apple SD Gothic Neo 등)를 등록하고, matplotlib 기본
`svg.fonttype='path'` 로 **텍스트를 벡터 path 로 구워** 뷰어 폰트 의존을 없앤다.

의존성(개발 전용, 앱 런타임 아님): matplotlib.
  pip install --break-system-packages --user matplotlib   # PEP668 외부관리 환경

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
        out[slug] = [(d, statistics.median(per_day[d])) for d in sorted(per_day)]
    return out


# ---------- matplotlib 설정 (한글 폰트 등록 + 경고 억제) ----------
import matplotlib
matplotlib.use("Agg")
from matplotlib import font_manager as fm
import matplotlib.pyplot as plt

_KFONT_CANDIDATES = [
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",                 # macOS
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",     # Linux
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/Library/Fonts/NanumGothic.ttf",
]


def _setup_font():
    for p in _KFONT_CANDIDATES:
        if os.path.exists(p):
            fm.fontManager.addfont(p)
            name = fm.FontProperties(fname=p).get_name()
            plt.rcParams["font.family"] = name
            break
    plt.rcParams["axes.unicode_minus"] = False   # 한글 폰트 minus 글리프 회피
    plt.rcParams["svg.fonttype"] = "path"        # 텍스트→path (뷰어 폰트 무의존)


def make_chart(series_by_os, title, ylabel, outpath, logy=False, unit=""):
    all_days = sorted({d for s in series_by_os.values() for d, _ in s})
    if not all_days:
        return
    day_idx = {d: i for i, d in enumerate(all_days)}

    fig, ax = plt.subplots(figsize=(7.8, 4.3), dpi=100)
    for slug in OS_SLUGS:
        s = series_by_os.get(slug, [])
        if not s:
            continue
        xs = [day_idx[d] for d, _ in s]
        ys = [v for _, v in s]
        last = s[-1][1]
        ax.plot(xs, ys, marker="o", markersize=3.2, linewidth=1.8,
                color=OS_COLOR[slug], label=f"{OS_LABEL[slug]} · 최신 {last:g}{unit}")

    if logy:
        # 로그축 기본 라벨(10⁻¹)은 mathtext 의 U+2212 를 써 한글 폰트서 깨진다 →
        # 평범한 ASCII 십진수(0.1·1·10)로 포맷.
        from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter
        ax.set_yscale("log")
        ax.yaxis.set_major_locator(LogLocator(base=10.0, subs=(1.0,), numticks=15))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        ax.yaxis.set_minor_formatter(NullFormatter())

    # x 축: 짝수 인덱스 + 마지막만 라벨
    ticks = [i for i in range(len(all_days)) if i % 2 == 0 or i == len(all_days) - 1]
    ax.set_xticks(ticks)
    ax.set_xticklabels([f"{all_days[i][4:6]}-{all_days[i][6:8]}" for i in ticks], fontsize=8.5)
    ax.tick_params(axis="y", labelsize=8.5)
    ax.set_xlim(-0.4, len(all_days) - 0.6)

    ax.grid(True, which="major", color="#e8e8e8", linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color("#bbb")

    sub = ylabel + (" · 로그 스케일" if logy else "") + " · 일별 중앙값"
    ax.set_title(title, fontsize=13, fontweight="bold", loc="left", color="#1a1a1a", pad=20)
    ax.text(0.0, 1.015, sub, transform=ax.transAxes, fontsize=8.5, color="#666", va="bottom")
    ax.set_ylabel(ylabel, fontsize=9, color="#444")

    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False,
              fontsize=9, handlelength=1.4)

    fig.tight_layout()
    fig.savefig(outpath, format="svg", bbox_inches="tight")
    plt.close(fig)
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
    _setup_font()
    for metric, title, ylabel, fname, logy, unit in CHARTS:
        s = daily_median(args.bench_root, metric)
        out = os.path.join(args.outdir, fname)
        make_chart(s, title, ylabel, out, logy=logy, unit=unit)
        print("wrote", out)


if __name__ == "__main__":
    main()
