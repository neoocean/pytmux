"""픽스처 게이트(`client/scripts/check_fixtures.py`)의 **시한**을 잰다.

# 왜 여기에 시한이 있어야 하나 (pytmux-194)

이 게이트는 생성기 열아홉을 차례로 돌린다. 시한이 없으면 **하나가 매달릴 때 전부가
선다** — 2026-08-09 에 실제로 그랬고(첫 생성기에서 47분), 그 위의 합본 게이트도
시한이 없어 커밋 전 관문이 통째로 못 쓰게 됐다.

★ 시한이 **바깥(합본 게이트)보다 작아야** 값이 있다: 작아야 여기가 먼저 울어 **어느
생성기가** 매달렸는지 이름을 남긴다. 밖에서 죽이면 그 이름이 없다 — 그날 사람이 `ps`
로 프로세스 트리를 떠야 했던 이유가 그것이다.
"""

import os
import sys
import tempfile
import time

import harness  # noqa: F401  (경로 설정)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GEN_DIR = os.path.join(ROOT, "client", "scripts")
if GEN_DIR not in sys.path:
    sys.path.insert(0, GEN_DIR)
import check_fixtures  # noqa: E402


async def test_a_hanging_generator_is_killed_and_named():
    """매달린 생성기를 시한 안에 걷고, **어느 것인지** 적는가."""
    with tempfile.TemporaryDirectory() as tmp:
        gen = os.path.join(tmp, "gen_hang_probe.py")
        with open(gen, "w", encoding="utf-8") as fh:
            fh.write("import time\ntime.sleep(3000)\n")

        real = check_fixtures.GEN_TIMEOUT
        check_fixtures.GEN_TIMEOUT = 2.0
        started = time.monotonic()
        try:
            rc, err = check_fixtures.run_generator(gen, ROOT)
        finally:
            check_fixtures.GEN_TIMEOUT = real
        elapsed = time.monotonic() - started

    assert elapsed < 30, f"시한이 안 먹었다 — {elapsed:.1f}초 매달렸다"
    assert rc != 0, "매달린 생성기를 통과로 셌다"
    assert "매달렸다" in err, f"사유를 안 적었다: {err!r}"


async def test_the_inner_deadline_is_shorter_than_the_outer_one():
    """이 시한이 합본 게이트의 스텝 시한보다 **작아야** 이름이 남는다."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import check_all

    fixture_step = next(s for s in check_all.steps() if s.name == "픽스처 신선도")
    outer = check_all.step_timeout(fixture_step)
    assert outer and check_fixtures.GEN_TIMEOUT < outer, (
        f"안쪽 시한({check_fixtures.GEN_TIMEOUT:.0f}초)이 바깥({outer:.0f}초)보다 크다"
        " — 밖에서 먼저 죽이면 어느 생성기가 매달렸는지 안 남는다")
