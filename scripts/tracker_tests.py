#!/usr/bin/env python3
"""스위트 리포트 → 이슈트래커 유입구(pytmux/pytmux-132).

    python3 scripts/tracker_tests.py                 # 변환만 (무엇이 흘러갈지 본다)
    python3 scripts/tracker_tests.py --ingest        # 변환 + 트래커에 담는다
    python3 scripts/tracker_tests.py --ingest --dry-run   # 담는 시늉만 (트래커가 세어만 본다)

# 왜 필요한가

항목의 정본은 `//woojinkim/scripts/issue` 트래커다(CLAUDE.md) — **러너가 저장소에 쓰지 않고
트래커의 `sync` 도 저장소를 읽지 않는다.** 그 사이를 잇는 것은 `issue ingest-*` 하나뿐인데,
실측(2026-08-04) 그것을 **한 번도 안 불렀다** — 즉 스위트가 무엇을 잡든 그 사실이 **어디에도
안 들어가고** 리포트 파일에만 남았다. 그동안 트래커의 `doctor` 는 이 프로젝트를 「건강」이라고
답한다. 그것이 이 파일이 메우는 구멍이다.

⛔ 종전에 여기 적혀 있던 「이 저장소는 **M2** 다」는 이제 틀렸다 — 트래커가 단계(M0~M4)를
   통째로 걷었다(`issue/issue-122`). 바뀐 것은 **이름뿐이고 규칙은 더 세졌다**: 붙은 프로젝트는
   언제나 트래커가 권위이므로, 이 유입구는 「M2 라서」가 아니라 **언제나** 유일한 길이다.

# 무엇을 흘리나

`tests/run.py` 가 이미 결과를 **한 줄씩 즉시** JSONL 로 적재한다(`reports/testrun.jsonl` ·
절단 내성). 여기서는 그것을 트래커가 먹는 모양(`{"cases":[...]}`)으로 바꿔 `ingest-tests` 에
넘긴다. 트래커 쪽에서 일어나는 일:

- 실행 기록이 `run`(kind=test)으로 남는다 — 「어제 몇 건이었나」에 답한다.
- **실패는 결함(이슈)으로 흘러** 케이스 이름이 곧 지문이 된다. 같은 케이스가 다시 깨지면
  새 이슈가 아니라 **같은 이슈**가 다시 열리고(재발), 런과 이슈가 서로 링크된다.

⛔ **새 산출물 형식을 만들지 않았다.** `findings.json` 을 따로 굽는 길도 있었지만, 스위트에는
   트래커가 이미 전용 유입구(`ingest-tests`)를 갖고 있고 케이스 단위 지문·재발 판정을 그쪽이
   한다 — 여기서 다시 만들면 같은 판정이 두 벌이 된다.

# 판정 규칙 (이 저장소의 규율 그대로)

- ⛔ **절단된 run 은 안 흘린다.** `summary` 줄이 없으면 러너가 중간에 죽은 것이고(부하·CI
  타임아웃·faulthandler exit), 그때까지의 결과를 담으면 **"통과했다"가 거짓말이 된다.**
  `tests/run.py --report` 가 같은 자리에서 같은 판정을 한다.
- ⛔ **복원한 회계가 요약줄과 다르면 안 흘린다.** 둘이 어긋난다는 것은 리포트를 잘못 읽고
  있다는 뜻이고, 잘못 읽은 것을 담는 것이 안 담는 것보다 나쁘다.
- ⛔ **빈 결과는 통과가 아니라 고장이다**(`scripts/check_all.py` 와 같은 규칙).

# 런의 정체

`--run-id` 는 `python-YYYYmmdd-HHMMSS` 꼴이다. 그래서 같은 리포트를 두 번 흘려도 런 한 줄이고
(멱등 — 트래커가 그 id 로 upsert 한다), 하루 두 번 돈 것은 두 줄이다.
`--started-at` 은 **돈 날**을 준다 — 흘린 날이 아니다(트래커 p4 70604).

⛔ **그 시각은 리포트가 말하는 것이 아니라 파일 mtime 이다**(실측 2026-08-16 · pytmux-203).
   종전에 여기 적혀 있던 「리포트가 말하는 **시작 시각**에서 짓고, 옛 리포트만 mtime 으로
   떨어진다」는 틀렸다 — `tests/run.py` 의 `start` 줄은 `{kind, modules, argv, pid}` 뿐이라
   `ts` 가 **없다**. 즉 `run_identity` 의 mtime 갈래가 예외가 아니라 **언제나 도는 길**이다.
   그래서 두 가지가 따라온다:
   ⑴ 그 시각은 런의 **시작이 아니라 끝**이다(`Reporter` 가 `"w"` 로 새로 열고 마지막 줄까지
      flush 하므로 mtime = 마지막 결과가 적힌 때). 자정을 넘긴 런은 `--started-at` 이 끝난 날이다.
   ⑵ **멱등은 그 mtime 이 보존되는 동안만 참이다** — 리포트를 복사하거나 `touch` 하면 같은
      결과가 다른 런 id 로 한 줄 더 선다. 옮길 때는 `cp -p` 처럼 mtime 을 지키는 길로 옮긴다.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REPORT = os.path.join(ROOT, "reports", "testrun.jsonl")
DEFAULT_OUT = os.path.join(ROOT, "reports", "tracker-tests.json")
# 트래커 저장소는 같은 모노리포의 형제다. 다른 데 뒀으면 ISSUE_REPO 로 가리킨다.
DEFAULT_ISSUE_REPO = os.path.join(os.path.dirname(ROOT), "issue")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


class Refused(Exception):
    """담을 수 없는 리포트. **조용히 성공하지 않는다** — 왜 못 담는지 말하고 2로 끝난다."""


def report_path(arg=None):
    """`tests/run.py` 의 경로 규칙과 같다(`PYTMUX_TEST_REPORT` · 기본 reports/testrun.jsonl)."""
    if arg:
        return arg
    raw = os.environ.get("PYTMUX_TEST_REPORT")
    if raw is None:
        return DEFAULT_REPORT
    raw = raw.strip()
    return "" if raw.lower() in ("", "0", "off", "no") else raw


def read_report(path):
    """JSONL 을 읽어 `(cases, summary, start)` 로. 마지막 줄이 잘려 있어도 나머지는 읽는다."""
    if not path or not os.path.exists(path):
        raise Refused(f"리포트가 없다: {path or '(비활성 — PYTMUX_TEST_REPORT 가 꺼져 있다)'}")
    cases, summary, start = [], None, None
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue          # 부분 write 로 잘린 마지막 줄 — 아래 회계 대조가 잡는다
            kind = rec.get("kind")
            if kind == "start":
                start = rec
            elif kind == "summary":
                summary = rec
            elif kind == "result":
                cases.append(rec)
    return cases, summary, start


def to_tracker(cases, summary):
    """트래커가 먹는 모양으로. 케이스 **이름이 곧 지문**이라 label 을 그대로 쓴다.

    status → (ok, skip):
      pass/flaky = 통과(flaky 는 재시도 후 통과라 통과다 — 그 사실은 detail 에 남긴다)
      skip       = 통과도 실패도 아니다(트래커도 셋을 가른다)
      fail       = 실패 → 결함으로 흘러 이슈가 된다
      timeout    = 실패(행). 같은 케이스가 매달린 것도 깨진 것이다
    """
    out, counts = [], {}
    for rec in cases:
        st = rec.get("status", "?")
        counts[st] = counts.get(st, 0) + 1
        name = str(rec.get("label") or "(이름 없음)")
        detail = []
        if st in ("fail", "timeout"):
            reason = str(rec.get("reason") or "").strip()
            detail = [f"{'TIMEOUT — ' if st == 'timeout' else ''}{reason or '(사유 없음)'}"]
            attempts = rec.get("attempts")
            if attempts and attempts > 1:
                detail.append(f"시도 {attempts}회 전부 실패")
        elif st == "skip":
            detail = [str(rec.get("reason") or "(사유 없음)")]
        elif st == "flaky":
            detail = [f"재시도 {rec.get('attempts', '?')}회 만에 통과(flaky)"]
        out.append({
            "name": name,
            "ok": st in ("pass", "flaky"),
            "skip": st == "skip",
            "detail": detail,
        })

    # ── 관문 셋 ──────────────────────────────────────────────────────────────
    if not out:
        raise Refused("리포트에 결과가 한 건도 없다 — 빈 결과는 통과가 아니라 고장이다")
    if summary is None:
        raise Refused("절단된 run 이다 — 요약 줄이 없다(러너가 중간에 죽었다). "
                      "그때까지의 결과를 담으면 '통과했다'가 거짓말이 된다. "
                      "`python3 tests/run.py --report` 가 죽은 지점을 말해 준다")
    got = (counts.get("pass", 0) + counts.get("flaky", 0), counts.get("fail", 0) + counts.get("timeout", 0),
           counts.get("skip", 0))
    want = (summary.get("passed"), summary.get("failed"), summary.get("skipped"))
    if got != want:
        raise Refused(f"복원한 회계가 요약줄과 다르다 — 복원 {got} ≠ 요약 {want} "
                      "(passed, failed, skipped). 리포트를 잘못 읽고 있다")
    return out, counts


def run_identity(path, start):
    """`(run_id, started_at)`. 리포트가 시각을 말하면 그것, 아니면 파일 mtime."""
    ts = (start or {}).get("ts")
    when = None
    if ts:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                when = time.strptime(str(ts)[:19], fmt)
                break
            except ValueError:
                continue
    if when is None:      # 시각 기록이 없던 옛 리포트 — 파일이 마지막으로 쓰인 때가 그 런의 끝이다
        when = time.localtime(os.path.getmtime(path))
    return time.strftime("python-%Y%m%d-%H%M%S", when), time.strftime("%Y-%m-%d", when)


def issue_cli():
    """트래커 CLI 경로. 없으면 왜 없는지 말한다(조용히 건너뛰지 않는다)."""
    repo = os.environ.get("ISSUE_REPO") or DEFAULT_ISSUE_REPO
    cli = os.path.join(repo, "bin", "issue.mjs")
    if not os.path.exists(cli):
        raise Refused(f"트래커 저장소가 없다: {cli} (ISSUE_REPO 로 가리킨다)")
    if not shutil.which("node"):
        raise Refused("node 가 PATH 에 없다 — 트래커 CLI 는 Node 로 돈다")
    return cli


def main(argv=None):
    ap = argparse.ArgumentParser(description="스위트 리포트를 이슈트래커로 흘린다")
    ap.add_argument("--report", help=f"스위트 리포트 JSONL (기본 {os.path.relpath(DEFAULT_REPORT, ROOT)})")
    ap.add_argument("--out", default=DEFAULT_OUT, help="변환 결과를 쓸 자리")
    ap.add_argument("--suite", default="python", help="스위트 이름(기본 python)")
    ap.add_argument("--ingest", action="store_true", help="변환에 그치지 않고 트래커에 담는다")
    ap.add_argument("--dry-run", action="store_true", help="트래커가 세어만 보고 안 담는다")
    ap.add_argument("--build", help="빌드 표식(선택)")
    ap.add_argument("--cl", help="이 런이 잰 CL(선택)")
    args = ap.parse_args(argv)

    try:
        path = report_path(args.report)
        cases, summary, start = read_report(path)
        tracker_cases, counts = to_tracker(cases, summary)
        run_id, started_at = run_identity(path, start)
    except Refused as e:
        print(f"✗ 안 담는다: {e}")
        return 2

    doc = {"suite": args.suite, "run_id": run_id, "started_at": started_at,
           "source": os.path.relpath(path, ROOT), "cases": tracker_cases}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(doc, fp, ensure_ascii=False)

    order = ("pass", "flaky", "fail", "timeout", "skip")
    parts = [f"{st}={counts[st]}" for st in order if st in counts]
    print(f"리포트 {os.path.relpath(path, ROOT)} → {os.path.relpath(args.out, ROOT)}")
    print(f"  런 {run_id} ({started_at}) · 스위트 {args.suite} · " + ", ".join(parts))
    if not args.ingest:
        print("  (담지 않았다 — `--ingest` 를 준다)")
        return 0

    try:
        cli = issue_cli()
    except Refused as e:
        print(f"✗ 못 담는다: {e}")
        return 2
    cmd = [shutil.which("node"), cli, "ingest-tests", "--project", "pytmux",
           "--run", os.path.abspath(args.out), "--suite", args.suite,
           "--run-id", run_id, "--started-at", started_at]
    if args.build:
        cmd += ["--build", args.build]
    if args.cl:
        cmd += ["--cl", str(args.cl)]
    if args.dry_run:
        cmd += ["--dry-run"]
    print("  " + " ".join(cmd[1:]))
    return subprocess.run(cmd, cwd=os.path.dirname(os.path.dirname(cli))).returncode


if __name__ == "__main__":
    sys.exit(main())
