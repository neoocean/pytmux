"""scripts/bench.py 스모크 테스트 — 벤치마크 3축 측정 함수와 렌더가 동작하는지.

실제 수치(머신 의존)는 보지 않고, 각 축이 기대 구조의 결과를 내고 Markdown 이
렌더되는지만 빠르게(작은 reps/mb) 검증한다."""
import importlib
import os
import sys

import harness  # noqa: F401  (경로 설정)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
bench = importlib.import_module("bench")


async def test_env_and_slug():
    env = bench.collect_env()
    for k in ("os", "os_release", "machine", "python", "impl", "deps"):
        assert k in env, k
    assert set(env["deps"]) == {"textual", "pyte", "wcwidth"}
    slug = bench.os_slug()
    assert slug and " " not in slug and slug == slug.lower(), slug


async def test_startup_measures():
    su = bench.bench_startup(reps=2)
    for k in ("cold_import_ms", "framework_init_ms"):
        assert k in su, k
        assert su[k]["n"] >= 1, su[k]
    # framework init 은 in-process 라 항상 측정됨
    assert su["framework_init_ms"]["p50"] >= 0


async def test_tabs_panes_responsiveness():
    tp = bench.bench_tabs_panes(reps=3, tabs=4, panes=4)
    for k in ("layout_build_ms", "render_all_ms", "tab_switch_ms"):
        assert k in tp and tp[k]["n"] == 3, (k, tp.get(k))
    sc = tp["scaling_by_panecount"]
    assert [s["panes"] for s in sc] == [1, 2, 4, 8]
    for s in sc:
        assert s["layout_ms"] >= 0 and s["render_all_ms"] >= 0


async def test_output_flood_small():
    of = bench.bench_output_flood(0.3)
    assert of["feed_slice"] > 0
    assert of["cases"], "측정 케이스 존재"
    c = of["cases"][0]
    for k in ("feed_mb_s", "slice_p50_ms", "slice_max_ms"):
        assert k in c, k
    assert c["feed_mb_s"] and c["feed_mb_s"] > 0


async def test_run_and_render_markdown():
    data = bench.run(reps=2, mb=0.3, tabs=3, panes=3)
    for k in ("startup", "tabs_panes", "output_flood", "env", "os_slug"):
        assert k in data, k
    md = bench.render_markdown(data)
    assert "# pytmux 벤치마크" in md
    assert "## 1. 초기 실행시간" in md
    assert "## 2. 다중 탭/패널 반응성" in md
    assert "## 3. 터미널 출력 폭증" in md
