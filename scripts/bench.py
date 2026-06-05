#!/usr/bin/env python3
"""pytmux 벤치마크 — OS별 성능/반응성 측정 (docs/WINDOWS_TESTING.md / HANDOFF §10).

macOS(arm64) 한 곳에서만 개발하면 Linux/Windows 의 성능 특성을 못 본다. 이 스크립트는
**완전 헤드리스**(실 터미널/셸 spawn 불필요)로 3축을 측정해 OS·시각별 Markdown 리포트를
남긴다. CI(.github/workflows/benchmark.yml)가 OS 매트릭스로 돌려 docs/benchmark/<os>/ 에
커밋하면 개발환경에서 git pull 로 비교해 볼 수 있다.

측정 3축:
  1. **startup**  — cold import(별도 프로세스로 `import pytmux`) + 프레임워크 init
     (Server 생성→기본 세션→첫 layout 메시지; 셸 spawn 은 가짜로 대체해 결정적).
  2. **tabs_panes** — 다중 탭/패널이 열렸을 때 반응성: 클라로 가는 layout 메시지 빌드·
     전체 패널 render+직렬화·탭 전환 지연(p50/p99/max ms). 패널 수별 스케일링 표.
  3. **output_flood** — 터미널 출력 폭증 시 처리량(feed MB/s)과 반응성(슬라이스 지연
     p50/p99/max). poc/feed_profile.py 의 합성 워크로드(claude busy 풀리페인트 / plain
     cat 스크롤)를 재사용한다.

사용:
  python scripts/bench.py                  # 측정 후 docs/benchmark/<os>/<ts>.md 작성
  python scripts/bench.py --stdout         # 파일 대신 표준출력
  python scripts/bench.py --quick          # 짧게(스모크/CI 빠른 점검)
  python scripts/bench.py --mb 20 --reps 8 # 더 큰 스트림·더 많은 반복
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import statistics
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


# ------------------------------------------------------------- 통계 유틸
def _pct(xs, p):
    if not xs:
        return 0.0
    s = sorted(xs)
    k = min(len(s) - 1, int(round(p / 100.0 * (len(s) - 1))))
    return s[k]


def _stats(ms):
    """ms 리스트 → p50/p99/max/mean(ms, 2자리) 요약 dict."""
    if not ms:
        return {"n": 0}
    return {
        "n": len(ms),
        "p50": round(_pct(ms, 50), 3),
        "p99": round(_pct(ms, 99), 3),
        "max": round(max(ms), 3),
        "mean": round(statistics.fmean(ms), 3),
    }


class _NoPty:
    """가짜 패널용 pty 스텁: resize 의 set_winsize 를 no-op 으로(실 fd 불필요).

    실 패널은 master_fd 로 TIOCSWINSZ 를 보내지만, 벤치는 셸을 안 띄우므로 fd 가 -1
    이라 ioctl 이 ValueError 가 난다. pane.pty 가 있으면 resize 가 이 set_winsize 를
    부르므로(fd 미사용) 안전하고 fd 누수도 없다."""
    def set_winsize(self, rows, cols):
        pass


def _fake_pane(cols, rows, fill=True):
    """실 셸 없이 화면버퍼만 가진 Pane(렌더/레이아웃 비용 측정용). 한 프레임 채워둠."""
    import poc.feed_profile as fp
    from pytmuxlib.model import Pane
    p = Pane(pid=-1, fd=-1, cols=cols, rows=rows)
    p.pty = _NoPty()    # resize 의 set_winsize 가 fd 없이 no-op 되도록
    if fill:
        p.feed(fp.gen_claude_busy(cols, rows, 1))   # 색·SGR 다발 한 화면
    return p


def _new_server():
    """serve() 없이 Server 인스턴스만 생성하고 spawn_pane 을 가짜로 대체한다.

    Server.__init__ 은 속성 세팅 + opts.json 읽기뿐이라 이벤트 루프가 필요 없다.
    spawn_pane 을 가짜 Pane 팩토리로 바꿔 실 셸 프로세스를 띄우지 않고도 new_window/
    split_pane/ensure_default_session 의 **트리 구성 로직**(실제 코드)을 그대로 쓴다."""
    import pytmux
    srv = pytmux.Server("bench-noserve.sock")
    srv.spawn_pane = lambda cols, rows, cwd=None, **kw: _fake_pane(cols, rows)
    return srv


# ----------------------------------------------------- 1) startup
def bench_startup(reps: int) -> dict:
    """cold import(별도 프로세스) + 프레임워크 init(in-process, 셸 spawn 제외)."""
    code = ("import time;_t=time.perf_counter();import pytmux;"
            "print(time.perf_counter()-_t)")
    cold = []
    # cold import 는 프로세스 spawn 비용이 커 분산이 작다 — 반복을 최대 5회로 캡한다
    # (200회면 서브프로세스 200개라 CI 가 느려진다).
    for _ in range(min(reps, 5)):
        r = subprocess.run([sys.executable, "-c", code], cwd=ROOT,
                           capture_output=True, text=True)
        try:
            cold.append(float(r.stdout.strip()) * 1e3)
        except ValueError:
            pass   # import 실패 시 건너뜀(요약에 n 으로 드러남)

    init = []
    for _ in range(reps):
        t0 = time.perf_counter()
        srv = _new_server()
        sess = srv.ensure_default_session(80, 24)
        srv._layout_msg(sess, 80, 24)
        init.append((time.perf_counter() - t0) * 1e3)

    return {
        "cold_import_ms": _stats(cold),
        "framework_init_ms": _stats(init),
        "note": "cold_import=별도 프로세스 `import pytmux`; "
                "framework_init=Server+세션+첫 layout(셸 spawn 제외)",
    }


# ---------------------------------------------- 2) tabs/panes 반응성
def _build_session(srv, tabs: int, panes_in_active: int):
    """tabs 개의 탭을 만들고, 활성 탭을 panes_in_active 개로 분할한 세션 반환."""
    sess = srv.ensure_default_session(200, 50)
    for _ in range(tabs - 1):
        srv.new_window(sess)
    srv.select_window(sess, len(sess.tabs) - 1)   # 마지막 탭을 활성으로
    orient = "h"
    while len(sess.active_window.panes()) < panes_in_active:
        srv.split_pane(sess, orient)
        orient = "v" if orient == "h" else "h"     # 번갈아 분할 → 균형 트리
    return sess


def bench_tabs_panes(reps: int, tabs: int, panes: int) -> dict:
    """다중 탭/패널이 열린 상태에서 클라 업데이트 3종의 지연을 잰다.

    - layout_build: 활성 윈도우의 layout 메시지 빌드 + json 직렬화(매 변경/리사이즈).
    - render_all  : 활성 윈도우 전 패널 render(True)+screen 메시지 직렬화(매 프레임).
    - tab_switch  : select_window(다음 탭) + layout 빌드(사용자 탭 전환 체감).
    """
    C, R = 200, 50
    srv = _new_server()
    sess = _build_session(srv, tabs, panes)
    win = sess.active_window

    layout_ms, render_ms, switch_ms = [], [], []
    for i in range(reps):
        t0 = time.perf_counter()
        msg = srv._layout_msg(sess, C, R)
        json.dumps(msg)
        layout_ms.append((time.perf_counter() - t0) * 1e3)

        t0 = time.perf_counter()
        for p in win.panes():
            rows, cur = p.render(True)
            json.dumps({"t": "screen", "pane": p.id, "rows": rows, "cursor": cur})
        render_ms.append((time.perf_counter() - t0) * 1e3)

        t0 = time.perf_counter()
        srv.select_window(sess, i % len(sess.tabs))
        json.dumps(srv._layout_msg(sess, C, R))
        switch_ms.append((time.perf_counter() - t0) * 1e3)
        srv.select_window(sess, len(sess.tabs) - 1)   # 활성 탭 원복

    # 패널 수별 스케일링: 단일 윈도우를 1/2/4/8 패널로 만들어 layout+render 1회 비용
    scaling = []
    for k in (1, 2, 4, 8):
        s2 = _new_server()
        sess2 = _build_session(s2, 1, k)
        w2 = sess2.active_window
        t0 = time.perf_counter()
        json.dumps(s2._layout_msg(sess2, C, R))
        lm = (time.perf_counter() - t0) * 1e3
        t0 = time.perf_counter()
        for p in w2.panes():
            rows, cur = p.render(True)
            json.dumps({"t": "screen", "pane": p.id, "rows": rows, "cursor": cur})
        rm = (time.perf_counter() - t0) * 1e3
        scaling.append({"panes": k, "layout_ms": round(lm, 3),
                        "render_all_ms": round(rm, 3)})

    return {
        "tabs": tabs, "panes_in_active": panes, "cols": C, "rows": R,
        "layout_build_ms": _stats(layout_ms),
        "render_all_ms": _stats(render_ms),
        "tab_switch_ms": _stats(switch_ms),
        "scaling_by_panecount": scaling,
    }


# ------------------------------------------- 3) output flood 처리량/반응성
def bench_output_flood(mb: float) -> dict:
    """출력 폭증 시 feed 처리량(MB/s)과 슬라이스 지연(반응성). feed_profile 재사용."""
    import poc.feed_profile as fp
    from pytmuxlib.model import Pane
    from pytmuxlib.protocol import FEED_SLICE

    def _case(name, cols, rows, data, render_every=8):
        pane = Pane(pid=-1, fd=-1, cols=cols, rows=rows)
        feed_s, slices, render_s, jbytes = fp.feed_in_slices(
            pane, data, render_every)
        mb_ = len(data) / 1e6
        nrender = (len(slices) // render_every) if render_every else 0
        return {
            "name": name, "cols": cols, "rows": rows, "mb": round(mb_, 2),
            "slices": len(slices),
            "feed_mb_s": round(mb_ / feed_s, 2) if feed_s else None,
            "slice_p50_ms": round(fp.pct(slices, 50) * 1e3, 3),
            "slice_p99_ms": round(fp.pct(slices, 99) * 1e3, 3),
            "slice_max_ms": round(max(slices) * 1e3, 3) if slices else None,
            "render_ms_frame": (round(render_s / nrender * 1e3, 3)
                                if nrender else None),
        }

    def _busy(cols, rows):
        one = fp.gen_claude_busy(cols, rows, 1)
        return fp.gen_claude_busy(cols, rows, max(1, int(mb * 1e6 / len(one))))

    cat = fp.gen_plain_cat(200, 50, max(1, int(mb * 1e6 / 202)))
    return {
        "feed_slice": FEED_SLICE,
        "target_ms": {"60fps": 16.7, "30fps": 33.3},
        "cases": [
            _case("claude_busy 200x50 (alt 풀리페인트)", 200, 50, _busy(200, 50)),
            _case("plain_cat 200x50 (main 스크롤)", 200, 50, cat),
            _case("claude_busy 80x24 (원격 흔한 크기)", 80, 24, _busy(80, 24)),
        ],
    }


# --------------------------------------------------------- 환경/렌더
def collect_env() -> dict:
    import importlib
    vers = {}
    for m in ("textual", "pyte", "wcwidth"):
        try:
            vers[m] = getattr(importlib.import_module(m), "__version__", "?")
        except Exception:
            vers[m] = "MISSING"
    return {
        "os": platform.system(),
        "os_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "impl": platform.python_implementation(),
        "deps": vers,
    }


def os_slug() -> str:
    return f"{platform.system()}-{platform.machine()}".lower().replace(" ", "_")


def run(reps: int, mb: float, tabs: int, panes: int) -> dict:
    now = _dt.datetime.now(_dt.timezone.utc)
    return {
        "generated_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
        "os_slug": os_slug(),
        "env": collect_env(),
        "params": {"reps": reps, "mb": mb, "tabs": tabs, "panes": panes},
        "startup": bench_startup(reps),
        "tabs_panes": bench_tabs_panes(reps, tabs, panes),
        "output_flood": bench_output_flood(mb),
    }


def _row(s):
    return (f"p50 {s.get('p50','-')} · p99 {s.get('p99','-')} · "
            f"max {s.get('max','-')} · mean {s.get('mean','-')} (n={s.get('n',0)})")


def render_markdown(d: dict) -> str:
    e = d["env"]
    L = []
    L.append(f"# pytmux 벤치마크 — {e['os']} {e['machine']}")
    L.append("")
    L.append(f"> 생성(UTC): {d['generated_utc']} · os_slug: `{d['os_slug']}`")
    L.append(f"> {e['impl']} {e['python']} · {e['os']} {e['os_release']} · "
             f"deps: textual {e['deps']['textual']}, pyte {e['deps']['pyte']}, "
             f"wcwidth {e['deps']['wcwidth']}")
    p = d["params"]
    L.append(f"> params: reps={p['reps']}, mb={p['mb']}, tabs={p['tabs']}, "
             f"panes={p['panes']}")
    L.append("")

    su = d["startup"]
    L.append("## 1. 초기 실행시간 (startup)")
    L.append("")
    L.append("| 항목 | ms |")
    L.append("|---|---|")
    L.append(f"| cold import (`import pytmux`, 별도 프로세스) | {_row(su['cold_import_ms'])} |")
    L.append(f"| framework init (Server+세션+첫 layout, 셸 제외) | {_row(su['framework_init_ms'])} |")
    L.append("")

    tp = d["tabs_panes"]
    L.append("## 2. 다중 탭/패널 반응성")
    L.append("")
    L.append(f"탭 {tp['tabs']}개 · 활성 윈도우 패널 {tp['panes_in_active']}개 "
             f"({tp['cols']}x{tp['rows']}). 값이 작을수록 반응성 좋음.")
    L.append("")
    L.append("| 작업(매 프레임/이벤트) | ms |")
    L.append("|---|---|")
    L.append(f"| layout 메시지 빌드+직렬화 | {_row(tp['layout_build_ms'])} |")
    L.append(f"| 전 패널 render+직렬화 | {_row(tp['render_all_ms'])} |")
    L.append(f"| 탭 전환(select+layout) | {_row(tp['tab_switch_ms'])} |")
    L.append("")
    L.append("패널 수별 스케일링(단일 윈도우, 1회):")
    L.append("")
    L.append("| 패널 수 | layout ms | render-all ms |")
    L.append("|---|---|---|")
    for s in tp["scaling_by_panecount"]:
        L.append(f"| {s['panes']} | {s['layout_ms']} | {s['render_all_ms']} |")
    L.append("")

    of = d["output_flood"]
    L.append("## 3. 터미널 출력 폭증 — 처리량 & 반응성")
    L.append("")
    L.append(f"FEED_SLICE={of['feed_slice']}B. 슬라이스 max < "
             f"{of['target_ms']['60fps']}ms(60fps)/{of['target_ms']['30fps']}ms(30fps)"
             f" 이어야 입력이 부드럽다. feed MB/s 높을수록·슬라이스 ms 낮을수록 좋음.")
    L.append("")
    L.append("| 워크로드 | MB | feed MB/s | slice p50 | p99 | max | render ms/frame |")
    L.append("|---|---|---|---|---|---|---|")
    for c in of["cases"]:
        L.append(f"| {c['name']} | {c['mb']} | {c['feed_mb_s']} | "
                 f"{c['slice_p50_ms']} | {c['slice_p99_ms']} | {c['slice_max_ms']} | "
                 f"{c['render_ms_frame']} |")
    L.append("")
    L.append("---")
    L.append("> 헤드리스 측정(실 셸/ssh 미포함). 인터랙티브 ssh 반응성은 실 박스 필요 — "
             "docs/WINDOWS_TESTING.md §3-c 참고.")
    L.append("")
    return "\n".join(L)


# ----------------------------------------------------------------- main
def main(argv=None) -> int:
    # Windows 콘솔 cp1252 가 한글·글리프를 못 써 stdout 출력이 죽는 것 방지(win_report 와 동일).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mb", type=float, default=8.0, help="출력 폭증 합성 스트림 크기(MB)")
    ap.add_argument("--reps", type=int, default=200, help="반응성/startup 반복 횟수")
    ap.add_argument("--tabs", type=int, default=12, help="다중 탭 수")
    ap.add_argument("--panes", type=int, default=6, help="활성 윈도우 패널 수")
    ap.add_argument("--quick", action="store_true",
                    help="짧게(reps=10, mb=1) — 스모크/CI 빠른 점검")
    ap.add_argument("--stdout", action="store_true", help="파일 대신 표준출력으로만")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "docs", "benchmark"),
                    help="리포트 출력 루트(기본 docs/benchmark)")
    args = ap.parse_args(argv)

    reps, mb = (10, 1.0) if args.quick else (args.reps, args.mb)
    data = run(reps, mb, args.tabs, args.panes)
    md = render_markdown(data)

    if args.stdout:
        sys.stdout.write(md)
        return 0

    slug = data["os_slug"]
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    out_dir = os.path.join(args.out_dir, slug)
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"{stamp}.md")
    json_path = os.path.join(out_dir, f"{stamp}.json")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"벤치마크 작성: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
