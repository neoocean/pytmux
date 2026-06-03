"""런처 단위 테스트 — 중첩 실행 거부 등(서버/클라 기동 불필요)."""
import os

import harness  # noqa: F401  (경로 설정)
from pytmuxlib.launcher import main, nesting_blocked


async def test_nesting_blocked_helper():
    old = os.environ.get("PYTMUX")
    try:
        os.environ["PYTMUX"] = "/tmp/some.sock"   # 패널 안인 상황
        assert nesting_blocked(False) is True, "패널 안 → 중첩 거부"
        assert nesting_blocked(True) is False, "--force 면 통과"
        os.environ.pop("PYTMUX", None)
        assert nesting_blocked(False) is False, "패널 밖 → 정상"
    finally:
        if old is None:
            os.environ.pop("PYTMUX", None)
        else:
            os.environ["PYTMUX"] = old


async def test_main_refuses_nested_attach():
    # PYTMUX 가 설정된 상태에서 attach → SystemExit(1)(ensure_server 도달 전 차단).
    old = os.environ.get("PYTMUX")
    try:
        os.environ["PYTMUX"] = "/tmp/some.sock"
        code = None
        try:
            main(["attach"])
        except SystemExit as e:
            code = e.code
        assert code == 1, f"중첩 attach 는 거부(exit 1), got {code}"
    finally:
        if old is None:
            os.environ.pop("PYTMUX", None)
        else:
            os.environ["PYTMUX"] = old
