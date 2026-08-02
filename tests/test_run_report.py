"""테스트 러너의 durable per-run 리포트(로드맵 test-infra ⑦ 잔여).

왜 필요한가: 요약(`N passed, M failed`)은 **끝에 한 번**만 찍히므로 러너가 부하·CI
타임아웃으로 절단되면 그때까지의 회계가 통째로 사라진다(2026-07-25·-25b 실측 사고).
그래서 결과를 **즉시 한 줄씩 flush** 하고 `--report` 로 파일만으로 복원한다.

오라클 구성(공허 통과 방지 — [[verify-call-site-not-just-helper]]):
- `report_summary` 헬퍼 단위(절단 판정·skip 사유 집계) **와**
- 러너를 실제로 돌린 e2e(`run.py test_cellwidth`)에서 그 파일이 생기는지 = **호출부**.
  후자가 없으면 `rep.emit(...)` 호출을 전부 지워도 헬퍼 테스트는 초록불이다.
"""
import json
import os
import subprocess
import sys
import tempfile

import run as runner

HERE = os.path.dirname(os.path.abspath(__file__))
RUNPY = os.path.join(HERE, "run.py")


def _lines(*recs):
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)


def _summarize(path):
    """report_summary 를 돌리고 (반환코드, 출력텍스트)."""
    out = []
    rc = runner.report_summary(path, out=out.append)
    return rc, "\n".join(out)


async def test_report_summary_restores_counts_and_skip_reasons():
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "r.jsonl")
        with open(p, "w", encoding="utf-8") as fp:
            fp.write(_lines(
                {"kind": "start", "modules": ["test_a"]},
                {"kind": "import", "module": "test_a"},
                {"kind": "result", "label": "test_a.t1", "status": "pass"},
                {"kind": "result", "label": "test_a.t2", "status": "skip",
                 "reason": "POSIX 전용(pty/termios)"},
                {"kind": "result", "label": "test_a.t3", "status": "skip",
                 "reason": "POSIX 전용(pty/termios)"},
                {"kind": "result", "label": "test_a.t4", "status": "fail",
                 "reason": "boom"},
                {"kind": "summary", "passed": 1, "failed": 1, "skipped": 2,
                 "flaky": 0}))
        rc, text = _summarize(p)
        assert rc == 1, "failed>0 인 run 은 0 이 아니어야 한다"
        assert "pass=1" in text and "skip=2" in text and "fail=1" in text, text
        # 사유별 집계(커버리지 갭 가시화) — 같은 사유 2건이 하나로 접혀야 한다.
        assert "skip   2  POSIX 전용(pty/termios)" in text, text
        assert "FAIL test_a.t4: boom" in text, text
        assert "완주" in text and "절단" not in text, text


async def test_report_summary_flags_truncated_run_with_last_module():
    """summary 줄 부재 = 러너가 죽은 run. 그 사실 + 물려 있던 모듈을 보고해야 한다."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "r.jsonl")
        with open(p, "w", encoding="utf-8") as fp:
            fp.write(_lines(
                {"kind": "start", "modules": ["test_a", "test_server"]},
                {"kind": "import", "module": "test_a"},
                {"kind": "result", "label": "test_a.t1", "status": "pass"},
                {"kind": "import", "module": "test_server"}))
            fp.write('{"kind": "result", "label": "test_ser')   # 부분 write
        rc, text = _summarize(p)
        assert rc == 1, "절단된 run 은 성공으로 보고하면 안 된다"
        assert "절단된 run" in text, text
        assert "test_server" in text, "죽을 때 물려 있던 모듈이 보여야 한다: " + text
        assert "pass=1" in text, "깨진 마지막 줄 때문에 앞선 회계를 잃으면 안 된다"


async def test_report_summary_missing_file_is_not_a_crash():
    rc, text = _summarize(os.path.join(tempfile.gettempdir(), "no-such-report"))
    assert rc == 1 and "리포트 없음" in text, text


async def test_report_path_env_off_disables():
    """리포트는 부가기능 — off 로 완전히 끌 수 있어야 한다(경로 미생성)."""
    old = os.environ.get("PYTMUX_TEST_REPORT")
    try:
        for val in ("off", "0", ""):
            os.environ["PYTMUX_TEST_REPORT"] = val
            assert runner._report_path() == "", val
        os.environ["PYTMUX_TEST_REPORT"] = "/tmp/x.jsonl"
        assert runner._report_path() == "/tmp/x.jsonl"
    finally:
        if old is None:
            os.environ.pop("PYTMUX_TEST_REPORT", None)
        else:
            os.environ["PYTMUX_TEST_REPORT"] = old


async def test_reporter_flushes_each_line_immediately():
    """절단 내성의 전부가 per-line flush 다 — 닫기 전에 읽어도 보여야 한다."""
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "sub", "r.jsonl")      # 없는 부모 디렉토리도 만든다
        rep = runner.Reporter(p)
        try:
            rep.emit("result", label="x.y", status="pass")
            with open(p, encoding="utf-8") as fp:
                assert json.loads(fp.read().strip())["label"] == "x.y"
        finally:
            rep.close()


async def test_runner_e2e_writes_report_matching_stdout():
    """**호출부** 오라클: 실제 러너가 리포트를 적재하고 수치가 stdout 요약과 같다.

    이 테스트가 없으면 `rep.emit(...)` 호출을 전부 지워도 위 헬퍼 테스트는 통과한다
    (실측된 공허 통과 패턴). test_cellwidth 는 0.2초짜리 순수 모듈이라 e2e 비용이 싸다.
    """
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "r.jsonl")
        env = dict(os.environ, PYTMUX_TEST_REPORT=p)
        # encoding 명시 — run.py 는 자기 stdout 을 UTF-8 로 reconfigure 하고 한글
        # (요약·`리포트:` 꼬리말)을 찍는데, `text=True` 만 주면 **부모가 로케일
        # 인코딩으로 디코드**한다. 한국어 Windows(cp949)에선 리더 스레드가
        # UnicodeDecodeError 로 죽어 `r.stdout` 이 None 이 되고, 아래 단언이
        # "argument of type 'NoneType' is not iterable" 로 터졌다 — 러너 결함이
        # 아니라 이 호출의 디코딩 불일치다(2026-07-31 검수).
        r = subprocess.run([sys.executable, RUNPY, "test_cellwidth"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, timeout=120)
        assert "passed" in r.stdout, r.stdout + r.stderr
        n_pass = int(r.stdout.split("=" * 50)[-1].split(" passed")[0].strip())
        recs = [json.loads(ln) for ln in open(p, encoding="utf-8") if ln.strip()]
        kinds = [r_["kind"] for r_ in recs]
        assert kinds[0] == "start" and kinds[-1] == "summary", kinds
        assert "import" in kinds, "모듈 import 기록이 없으면 절단 진단이 불가능하다"
        results = [r_ for r_ in recs if r_["kind"] == "result"]
        assert len(results) == n_pass, (len(results), n_pass)
        assert all(r_["status"] == "pass" for r_ in results), results
        assert all("secs" in r_ for r_ in results), "소요시간이 있어야 느린 모듈이 보인다"
        summary = recs[-1]
        assert (summary["passed"], summary["failed"]) == (n_pass, 0), summary
        # 파일만으로 복원한 회계가 완주로 판정돼야 한다(왕복 검증).
        assert _summarize(p) == (0, _summarize(p)[1]) and "완주" in _summarize(p)[1]
