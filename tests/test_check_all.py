"""합본 게이트(`scripts/check_all.py`)의 **판정 규칙**을 잰다.

# 왜 게이트를 또 재나

이 게이트가 틀리면 증상이 **초록불**이다 — 그리고 초록불은 아무도 안 들여다본다. 특히
두 규칙은 이 저장소가 실제로 물렸던 자리라 못박을 값이 있다:

1. **파이썬 스위트는 rc 를 믿지 않는다.** `tests/run.py` 는 실패해도 종료코드가 0 일 수
   있다(CLAUDE.md 경고). rc 로 판정하면 "N failed" 를 찍고도 통과가 된다.
2. **절단은 통과가 아니다.** 부하로 러너가 죽으면 요약줄 자체가 안 나오는데, 그때
   "실패 0건"으로 읽으면 아무것도 안 재고 초록을 준 것이다.

`check_all` 은 서브프로세스를 띄우는 도구라 여기서는 **순수 판정 함수만** 부른다
(스위트 안에서 전체 스위트를 다시 돌릴 수는 없다).
"""

import os
import sys

import harness  # noqa: F401  (경로 설정)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
import check_all  # noqa: E402


async def test_python_verdict_reads_the_summary_not_the_return_code():
    # rc 0 인데 실패가 있다 — run.py 가 실제로 이렇게 끝날 수 있다.
    out = "…\n1663 passed, 4 failed, 8 skipped\n"
    assert check_all.python_suite_verdict(out, 0) == "4건 실패(통과 1663)"
    # rc 1 인데 요약은 깨끗하다(러너 뒷정리에서 난 rc) — 실패로 안 본다.
    assert check_all.python_suite_verdict("1663 passed, 0 failed\n", 1) is None


async def test_python_verdict_treats_a_truncated_run_as_failure():
    """요약줄이 없으면 **아무것도 안 잰 것**이다 — 통과로 접으면 안 된다."""
    verdict = check_all.python_suite_verdict("모듈 test_server 시작…\n", 0)
    assert verdict and "요약줄" in verdict
    # 통과 0건도 같은 부류(모듈이 하나도 안 돌았다).
    assert check_all.python_suite_verdict("0 passed, 0 failed\n", 0)


async def test_cargo_verdict_fails_when_nothing_ran():
    """rc 0 + 테스트 0건은 통과가 아니라 고장이다(빈 결과 = 규칙이 안 걸렸다)."""
    ok = "test result: ok. 12 passed; 0 failed; 0 ignored; 0 measured\n"
    assert check_all.cargo_verdict(ok, 0) is None
    assert check_all.cargo_verdict("", 0)
    # 실패는 이름을 남긴다 — 요약만 주면 어느 테스트인지 다시 돌려야 안다.
    bad = "---- session_view::tests::foo stdout ----\npanicked at x\n"
    assert "foo" in check_all.cargo_verdict(bad, 101)


async def test_every_step_says_why_it_exists():
    """스텝마다 **왜 도는지**가 붙어 있어야 한다.

    이유 없는 스텝은 느려질 때 가장 먼저 지워지고, 그때 무엇을 잃는지 아무도 모른다.
    `--list` 가 사람에게 보여 주는 것이 이 문장이다.
    """
    steps = check_all.steps()
    assert steps, "스텝이 하나도 없다 — 통과가 아니라 고장이다"
    for step in steps:
        assert step.why.strip(), f"{step.name} 에 이유가 없다"
        assert step.argv, f"{step.name} 에 명령이 없다"


async def test_the_gate_covers_both_trees():
    """서버(파이썬)와 클라(Rust) **양쪽**을 도는가 — 그것이 이 파일의 존재 이유다."""
    cwds = {os.path.basename(s.cwd) for s in check_all.steps()}
    assert "client" in cwds, "Rust 클라를 안 돈다"
    assert "pytmux" in cwds, "정본(서버·파이썬)을 안 돈다"


async def test_every_step_has_a_deadline():
    """스텝마다 **시한**이 있어야 한다(pytmux-194).

    시한 없는 스텝의 침묵은 "아직 도는 중"과 구분이 안 된다 — 실제로 첫 스텝이 47분을
    매달렸고 화면은 0바이트였다. 사람은 기다리다 Ctrl-C 를 치고, 그 CL 은 관문을 안
    지나고 나간다.
    """
    for step in check_all.steps():
        secs = check_all.step_timeout(step)
        assert secs and secs > 0, f"{step.name} 에 시한이 없다"


async def test_a_hanging_step_is_killed_with_its_grandchildren():
    """매달린 스텝을 **손자까지** 걷고 FAIL 로 적는가.

    이 시험이 손자를 보는 이유가 사고의 모양 그대로다: 그날 매달린 것은 자식
    (`check_fixtures.py`)이 아니라 **손자**(`gen_plugin_client_cmds.py`)였다. 자식만
    죽이면 손자가 파이프의 쓰기 끝을 쥐고 있어 `communicate()` 가 여전히 안 돌아온다 —
    시한을 걸어 놓고도 매달리는, 가장 나쁜 모양이다.
    """
    import tempfile
    import time

    if os.name == "nt":
        return          # `start_new_session` 이 POSIX 전용이라 이 상자에선 못 잰다

    with tempfile.TemporaryDirectory() as tmp:
        pidfile = os.path.join(tmp, "grandchild.pid")
        child = (
            "import os, subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, '-c',\n"
            "  \"import os,sys,time; open(sys.argv[1],'w').write(str(os.getpid()));\"\n"
            "  \"time.sleep(300)\", %r])\n"
            "sys.stdout.write('시작했다\\n'); sys.stdout.flush()\n"
            "time.sleep(300)\n" % pidfile
        )
        step = check_all.Step("매달림", [sys.executable, "-c", child],
                              os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "매달린 스텝을 걷는지 잰다")
        started = time.monotonic()
        out, verdict, _secs = check_all.run(step, dict(os.environ), timeout=2.0)
        elapsed = time.monotonic() - started

        assert elapsed < 30, f"시한이 안 먹었다 — {elapsed:.1f}초 매달렸다"
        assert verdict and "시한" in verdict, f"매달림을 FAIL 로 안 적었다: {verdict!r}"
        # 어디까지 갔나가 남아야 한다 — 그날 사람이 `ps` 로 트리를 떠야 알아낸 그것.
        assert "시작했다" in out, "죽이기 전 출력을 안 건졌다"

        for _ in range(100):                     # 손자가 죽었나 (최대 10초)
            if not os.path.exists(pidfile):
                time.sleep(0.1)
                continue
            pid = int(open(pidfile).read().strip() or 0)
            try:
                os.kill(pid, 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.1)
        else:
            raise AssertionError("손자가 살아남았다 — 프로세스 그룹째 안 걷었다")
