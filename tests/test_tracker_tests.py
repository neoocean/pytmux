"""scripts/tracker_tests.py 의 scope_of() — 전량/부분 판정(pytmux-233).

`ingest-tests` 를 실제로 부르는 나머지(issue_cli·subprocess)는 트래커 저장소가
있어야 도니 여기서는 순수 판정 함수만 잰다."""
import importlib
import os
import sys

import harness  # noqa: F401  (경로 설정)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))
tracker_tests = importlib.import_module("tracker_tests")


async def test_no_argv_is_full():
    assert tracker_tests.scope_of({"kind": "start", "argv": []}) == "full"


async def test_any_argv_is_partial():
    assert tracker_tests.scope_of({"kind": "start", "argv": ["test_i18n"]}) == "partial"


async def test_missing_start_line_does_not_guess():
    # ⛔ 짐작하지 않는다 — 트래커가 scope=NULL(모른다)로 눕혀야 한다.
    assert tracker_tests.scope_of(None) is None


async def test_start_line_without_argv_key_is_full():
    # 옛 리포트라도 start 줄 자체가 있으면 그 값을 믿는다 — argv 가 없다는 것은
    # 비어 있다는 것과 같은 뜻이다(`discover()` 의 `if not names`).
    assert tracker_tests.scope_of({"kind": "start"}) == "full"
