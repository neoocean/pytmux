"""scripts/win_report.py 스모크 테스트 — 리포트 생성기의 순수 부분이 동작하는지.

run_tests()(서브프로세스로 tests/run.py 전체 재실행)는 재귀/느림이라 제외하고,
환경 수집·import 가드·성능 측정·Markdown 렌더만 검증한다."""
import importlib
import os
import sys

import harness  # noqa: F401  (경로 설정)

# scripts/ 는 패키지가 아니므로 직접 경로를 잡아 모듈로 로드한다.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
win_report = importlib.import_module("win_report")


async def test_collect_env_and_imports():
    env = win_report.collect_env()
    # 필수 키와 POSIX 모듈/의존성 구조
    for k in ("platform", "python_version", "posix_modules", "deps",
              "pty_backend"):
        assert k in env, k
    assert set(env["posix_modules"]) == {"fcntl", "termios", "pty"}
    imp = win_report.check_imports()
    # 코어 모듈은 이 환경에서 import 가능해야 한다
    assert imp["pytmuxlib.protocol"] == "ok", imp["pytmuxlib.protocol"]
    assert imp["pytmuxlib.client"] == "ok", imp["pytmuxlib.client"]


async def test_run_perf_small():
    perf = win_report.run_perf(0.3)   # 작은 합성 스트림
    assert perf.get("ran") is True, perf
    assert perf["cases"], "측정 케이스 존재"
    c = perf["cases"][0]
    for k in ("feed_mb_s", "slice_p50_ms", "slice_max_ms", "render_ms_frame"):
        assert k in c, k
    assert c["feed_mb_s"] and c["feed_mb_s"] > 0


async def test_render_markdown_with_delta():
    data = {
        "generated_utc": "2026-06-04 00:00:00",
        "env": win_report.collect_env(),
        "imports": win_report.check_imports(),
        "tests": {"ran": True, "exit_code": 0, "passed": 180, "failed": 0,
                  "fail_labels": []},
        "perf": win_report.run_perf(0.3),
    }
    # 이전 실행(다른 수치) → 델타 화살표가 렌더돼야 한다
    prev = {"generated_utc": "2026-06-03 00:00:00",
            "tests": {"passed": 179, "failed": 1},
            "perf": {"cases": [dict(data["perf"]["cases"][0],
                                    feed_mb_s=(data["perf"]["cases"][0]
                                               ["feed_mb_s"] or 1) + 1.0)]}}
    md = win_report.render_markdown(data, prev)
    assert "# pytmux Windows 호환성·성능 리포트" in md
    assert "180 passed" in md and "passed" in md
    assert "vs 이전" in md, "이전 대비 델타 표기"
    assert "| `pytmuxlib.client` |" in md
