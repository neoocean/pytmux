#!/usr/bin/env python3
"""Windows(및 임의 플랫폼) 호환성·성능 테스트 리포트 생성기 — docs/HANDOFF.md §10.

한 번 실행하면 ① 환경 정보 ② 호환성(헤드리스 테스트·import 가드) ③ 성능 수치
(feed/render 핫패스)를 모아 Markdown 리포트로 출력한다. 결과는 JSON 사이드카로도
남겨, 다음 실행 시 **이전 실행 대비 변화**를 표에 곁들인다(회귀 추적).

설계 메모(§10 열린 결정에 대한 선택):
- **단일 스크립트**로 기존 자산을 래핑한다: 호환성=`tests/run.py`(헤드리스 러너),
  성능=`poc/feed_profile.py`(feed/render 프로파일러)의 순수 함수를 재사용한다.
- **측정만** 한다(성능 회귀 게이트는 두지 않음) — 다만 슬라이스 지연을 60/30fps
  기준선과 비교해 advisory 로 표시한다.
- 호환성은 **헤드리스 범위**(tests/run.py + import 가드)만 본다 — 라이브 attach
  스모크는 환경 의존이라 제외(추후 옵션).
- 출력: `reports/win-report-<UTC타임스탬프>.md` + 사람이 안 읽는 `win-report-latest.json`
  (다음 실행 비교용). `reports/` 는 .p4ignore/.gitignore 로 버전관리 제외.

사용:
  python scripts/win_report.py                 # 기본(헤드리스 테스트 + 5MB 성능)
  python scripts/win_report.py --mb 20         # 더 큰 합성 스트림으로 성능 측정
  python scripts/win_report.py --no-tests      # 호환성 테스트 건너뛰고 환경+성능만
  python scripts/win_report.py --stdout        # 파일 대신 표준출력으로만
"""
from __future__ import annotations

import argparse
import datetime as _dt
import importlib
import json
import os
import platform
import subprocess
import sys

# 리포지토리 루트(= scripts/ 의 부모)를 import 경로에 추가해 pytmuxlib/poc 를 쓴다.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


# ---------------------------------------------------------------- 환경 수집
def collect_env() -> dict:
    """OS/Python/터미널/PTY 백엔드/의존성 등 실행 환경을 모은다."""
    env = {
        "platform": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "python_impl": platform.python_implementation(),
        "python_executable": sys.executable,
    }
    # Windows 전용: pythonw.exe(콘솔 없는 런처) 유무
    if env["system"] == "Windows":
        pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        env["pythonw_exe"] = pyw if os.path.exists(pyw) else None
    # 터미널 식별 단서(환경변수)
    env["terminal"] = {
        k: os.environ.get(k)
        for k in ("TERM", "TERM_PROGRAM", "WT_SESSION", "WT_PROFILE_ID",
                  "ConEmuPID", "SSH_CONNECTION", "KITTY_WINDOW_ID")
        if os.environ.get(k)
    }
    # POSIX 전용 모듈 가용성(Windows 포팅의 핵심 분기점)
    env["posix_modules"] = {
        m: _import_ok(m) for m in ("fcntl", "termios", "pty")}
    # PTY 백엔드: pywinpty(ConPTY) 버전(Windows) / pytmuxlib.pty_backend import
    env["pty_backend"] = _pty_backend_info()
    # 의존성 버전
    env["deps"] = {m: _mod_version(m) for m in ("textual", "pyte", "wcwidth")}
    return env


def _import_ok(name: str) -> bool:
    try:
        importlib.import_module(name)
        return True
    except Exception:
        return False


def _mod_version(name: str):
    try:
        m = importlib.import_module(name)
        return getattr(m, "__version__", "(unknown)")
    except Exception as e:
        return f"(missing: {e.__class__.__name__})"


def _pty_backend_info() -> dict:
    info = {"pytmuxlib_import": None, "pywinpty": None}
    try:
        importlib.import_module("pytmuxlib.pty_backend")
        info["pytmuxlib_import"] = "ok"
    except Exception as e:
        info["pytmuxlib_import"] = f"FAIL: {e.__class__.__name__}: {e}"
    if platform.system() == "Windows":
        info["pywinpty"] = _mod_version("winpty")
    return info


# ---------------------------------------------------------- 호환성(헤드리스)
def run_tests() -> dict:
    """헤드리스 테스트 러너(tests/run.py)를 서브프로세스로 돌려 통과/실패를 집계."""
    try:
        proc = subprocess.run(
            [sys.executable, os.path.join("tests", "run.py")],
            cwd=ROOT, capture_output=True, text=True, timeout=600)
    except Exception as e:
        return {"ran": False, "error": f"{e.__class__.__name__}: {e}"}
    passed = failed = None
    fail_labels = []
    for line in proc.stdout.splitlines():
        s = line.strip()
        if s.endswith("failed") and "passed" in s:        # "N passed, M failed"
            try:
                parts = s.replace(",", "").split()
                passed = int(parts[0])
                failed = int(parts[parts.index("failed") - 1])
            except (ValueError, IndexError):
                pass
        elif s.startswith("FAIL "):
            fail_labels.append(s[5:].split(":")[0].strip())
    return {"ran": True, "exit_code": proc.returncode,
            "passed": passed, "failed": failed, "fail_labels": fail_labels}


def check_imports() -> dict:
    """핵심 모듈 import 가드 — 플랫폼별 import 실패(예: fcntl 부재)를 잡아낸다."""
    mods = ["pytmuxlib.protocol", "pytmuxlib.claude", "pytmuxlib.model",
            "pytmuxlib.proc", "pytmuxlib.ipc", "pytmuxlib.pty_backend",
            "pytmuxlib.server", "pytmuxlib.client", "pytmuxlib.tokens",
            "pytmuxlib.usagelog"]
    out = {}
    for m in mods:
        try:
            importlib.import_module(m)
            out[m] = "ok"
        except Exception as e:
            out[m] = f"FAIL: {e.__class__.__name__}: {e}"
    return out


# ----------------------------------------------------------------- 성능
def run_perf(mb: float) -> dict:
    """poc/feed_profile.py 의 순수 함수를 재사용해 feed/render 핫패스를 측정."""
    try:
        fp = importlib.import_module("poc.feed_profile")
        from pytmuxlib.model import Pane
        from pytmuxlib.protocol import FEED_SLICE
    except Exception as e:
        return {"ran": False, "error": f"{e.__class__.__name__}: {e}"}

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
            "slice_p50_ms": round(fp.pct(slices, 50) * 1e3, 2),
            "slice_p99_ms": round(fp.pct(slices, 99) * 1e3, 2),
            "slice_max_ms": round(max(slices) * 1e3, 2) if slices else None,
            "render_ms_frame": (round(render_s / nrender * 1e3, 2)
                                if nrender else None),
            "json_kib_frame": (round(jbytes / nrender / 1024, 1)
                               if nrender else None),
        }

    busy_one = fp.gen_claude_busy(200, 50, 1)
    frames = max(1, int(mb * 1e6 / len(busy_one)))
    busy = fp.gen_claude_busy(200, 50, frames)
    cat_lines = max(1, int(mb * 1e6 / 202))
    cat = fp.gen_plain_cat(200, 50, cat_lines)
    return {"ran": True, "feed_slice": FEED_SLICE, "cases": [
        _case("claude_busy (alt-screen 풀 리페인트) 200x50", 200, 50, busy),
        _case("plain_cat   (main-screen 스크롤) 200x50", 200, 50, cat),
    ]}


# --------------------------------------------------------------- 리포트
def _delta(cur, prev, lower_better=True, unit=""):
    """이전 값 대비 변화 문자열. lower_better 면 감소가 개선(↓)."""
    if cur is None or prev is None:
        return ""
    d = cur - prev
    if abs(d) < 1e-9:
        return " (=)"
    better = (d < 0) if lower_better else (d > 0)
    arrow = "↓" if d < 0 else "↑"
    mark = "✓" if better else "✗"
    return f" ({arrow}{abs(round(d, 2))}{unit} {mark} vs 이전)"


def render_markdown(data: dict, prev: dict | None) -> str:
    env = data["env"]
    L = []
    L.append(f"# pytmux Windows 호환성·성능 리포트")
    L.append("")
    L.append(f"- 생성: {data['generated_utc']} (UTC)")
    if prev:
        L.append(f"- 이전 실행: {prev.get('generated_utc', '?')} (UTC) 대비 비교")
    L.append("")
    # 환경
    L.append("## 1. 환경")
    L.append("")
    L.append(f"- 플랫폼: `{env['platform']}` ({env['machine']})")
    L.append(f"- Python: {env['python_version']} "
             f"({env['python_impl']}) — `{env['python_executable']}`")
    if env["system"] == "Windows":
        L.append(f"- pythonw.exe: {env.get('pythonw_exe') or '없음'}")
    term = env["terminal"] or {}
    L.append(f"- 터미널 단서: "
             + (", ".join(f"{k}={v}" for k, v in term.items()) or "(없음)"))
    L.append(f"- POSIX 모듈: "
             + ", ".join(f"{k}={'O' if v else 'X'}"
                         for k, v in env["posix_modules"].items()))
    pb = env["pty_backend"]
    L.append(f"- PTY 백엔드 import: {pb['pytmuxlib_import']}"
             + (f" / pywinpty={pb['pywinpty']}" if pb.get("pywinpty") else ""))
    L.append(f"- 의존성: "
             + ", ".join(f"{k} {v}" for k, v in env["deps"].items()))
    L.append("")
    # 호환성
    L.append("## 2. 호환성")
    L.append("")
    t = data.get("tests") or {}
    if not t.get("ran"):
        L.append(f"- 헤드리스 테스트: 실행 실패 — {t.get('error', '건너뜀')}")
    else:
        pv = (prev or {}).get("tests", {})
        dp = _delta(t.get("passed"), pv.get("passed"), lower_better=False)
        df = _delta(t.get("failed"), pv.get("failed"), lower_better=True)
        L.append(f"- 헤드리스 테스트(`tests/run.py`): "
                 f"**{t.get('passed')} passed{dp}**, "
                 f"**{t.get('failed')} failed{df}** "
                 f"(exit={t.get('exit_code')})")
        for lbl in (t.get("fail_labels") or []):
            L.append(f"  - ✗ FAIL: `{lbl}`")
    L.append("")
    L.append("| 모듈 import | 결과 |")
    L.append("|---|---|")
    for m, r in data["imports"].items():
        L.append(f"| `{m}` | {'✓ ok' if r == 'ok' else '✗ ' + r} |")
    L.append("")
    # 성능
    L.append("## 3. 성능 (feed/render 핫패스)")
    L.append("")
    perf = data.get("perf") or {}
    if not perf.get("ran"):
        L.append(f"- 측정 실패: {perf.get('error', '건너뜀')}")
    else:
        L.append(f"FEED_SLICE={perf['feed_slice']} bytes. "
                 f"기준: 입력이 부드러우려면 슬라이스 max < ~16ms(60fps), <33ms(30fps).")
        L.append("")
        L.append("| 케이스 | feed MB/s | slice p50/p99/max ms | render ms/frame | json KiB/frame |")
        L.append("|---|---|---|---|---|")
        prev_cases = {c["name"]: c for c in (prev or {}).get("perf", {}).get("cases", [])}
        for c in perf["cases"]:
            pc = prev_cases.get(c["name"], {})
            fmbs = (f"{c['feed_mb_s']}"
                    + _delta(c["feed_mb_s"], pc.get("feed_mb_s"),
                             lower_better=False))
            smax = (f"{c['slice_p50_ms']}/{c['slice_p99_ms']}/{c['slice_max_ms']}"
                    + _delta(c["slice_max_ms"], pc.get("slice_max_ms"),
                             lower_better=True, unit="ms"))
            rmf = (f"{c['render_ms_frame']}"
                   + _delta(c["render_ms_frame"], pc.get("render_ms_frame"),
                            lower_better=True, unit="ms"))
            flag = ""
            if c["slice_max_ms"] and c["slice_max_ms"] > 33:
                flag = " ⚠️>33ms"
            elif c["slice_max_ms"] and c["slice_max_ms"] > 16:
                flag = " ⚠️>16ms"
            L.append(f"| {c['name']}{flag} | {fmbs} | {smax} | {rmf} "
                     f"| {c['json_kib_frame']} |")
    L.append("")
    L.append("---")
    L.append("_생성: scripts/win_report.py — 재실행하면 이 리포트 대비 변화가 "
             "표에 ↑/↓ 로 표시됩니다._")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------- main
def main(argv=None) -> int:
    # Windows 콘솔 기본 인코딩(cp1252)은 한글·박스글리프(✓/↑/✽ 등)를 못 써
    # stdout 출력이 UnicodeEncodeError 로 죽는다(리포트 md·요약 print 모두 영향 —
    # CI windows-latest 에서 적발). 파일 쓰기는 이미 utf-8 이고, stdout 만 재설정한다.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass
    # 행 백스톱(CI): 과거 macOS 러너에서 이 리포트(feed 프로파일·서브프로세스)가
    # 매달려 잡이 수십 분 돌았다. 전체 예산을 넘기면 전 스레드 트레이스백을 덤프하고
    # 종료해 행 위치를 남기고 CI step 을 끝낸다(자체 스레드 — 메인 블록돼도 동작).
    import faulthandler
    faulthandler.enable()
    budget = float(os.environ.get("PYTMUX_REPORT_TIMEOUT", "180"))
    if budget > 0:
        faulthandler.dump_traceback_later(budget, exit=True)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mb", type=float, default=5.0,
                    help="성능 측정 합성 스트림 크기(MB, 기본 5)")
    ap.add_argument("--no-tests", action="store_true",
                    help="호환성 테스트(tests/run.py) 건너뛰기")
    ap.add_argument("--no-perf", action="store_true", help="성능 측정 건너뛰기")
    ap.add_argument("--stdout", action="store_true",
                    help="파일로 안 쓰고 표준출력으로만")
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "reports"),
                    help="리포트 출력 디렉터리(기본 reports/)")
    args = ap.parse_args(argv)

    now = _dt.datetime.now(_dt.timezone.utc)
    data = {
        "generated_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
        "env": collect_env(),
        "imports": check_imports(),
        "tests": ({} if args.no_tests else run_tests()),
        "perf": ({} if args.no_perf else run_perf(args.mb)),
    }

    # 이전 실행(사이드카 JSON) 로드 → 비교
    sidecar = os.path.join(args.out_dir, "win-report-latest.json")
    prev = None
    try:
        with open(sidecar, encoding="utf-8") as f:
            prev = json.load(f)
    except (OSError, ValueError):
        prev = None

    md = render_markdown(data, prev)

    if args.stdout:
        sys.stdout.write(md)
    else:
        os.makedirs(args.out_dir, exist_ok=True)
        stamp = now.strftime("%Y%m%d-%H%M%S")
        md_path = os.path.join(args.out_dir, f"win-report-{stamp}.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md)
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"리포트 작성: {md_path}")
        print(f"비교용 사이드카: {sidecar}")

    # 요약 한 줄(콘솔)
    t = data.get("tests") or {}
    if t.get("ran"):
        print(f"호환성: {t.get('passed')} passed, {t.get('failed')} failed")
    fails = [m for m, r in data["imports"].items() if r != "ok"]
    if fails:
        print(f"import 실패: {', '.join(fails)}")
    # 테스트가 실패했으면 비-0 종료(CI 연동 가능)
    return 1 if (t.get("ran") and t.get("failed")) else 0


if __name__ == "__main__":
    sys.exit(main())
