#!/usr/bin/env python3
"""qa/run.py — QA 런 진입점.

    python3 qa/run.py                          # 전 시나리오 · 산출물만 굽는다
    python3 qa/run.py --scenario T0-core-loop  # 하나만
    python3 qa/run.py --tier T0
    python3 qa/run.py --ingest                 # 굽고 나서 트래커에 담는다
    python3 qa/run.py --ingest --dry-run       # 담는 시늉만 (무엇이 들어갈지 본다)
    python3 qa/run.py --ingest --run-dir qa/out/qa-…   # 이미 구운 런을 담는다
    python3 qa/run.py --keep                   # 격리 슬롯을 안 지운다(사후 조사용)

## 종료 코드 — ⛔ **초록은 비싸다**(원칙 ⓑ)

| rc | 뜻 |
| --- | --- |
| 0 | 초록 — 결함 0 **이고** 건너뜀 0 |
| 1 | 결함이 있다 |
| 2 | 환경 구성 실패 · 시나리오 0건(빈 결과는 통과가 아니라 고장이다) |
| 3 | 결함은 0 인데 **미검증**이 있다(건너뛴 검사) |

⛔ 미커버·SKIP·미판정이 하나라도 있으면 초록이 아니다. "서버 없으면 `exit 0`" 관행을
   계승하지 않기로 한 것이 이 표다.

## 결함은 어디로 가나

`findings.json` 하나를 굽고 끝난다. 흡수는 **나중에·언제든·여러 번** 트래커가 한다
(멱등) — 그래서 트래커가 꺼져 있어도 야간 QA 는 완주한다. pytmux 는 M2 라 이슈의 정본이
트래커이고, 저장소의 `docs/internal/qa/issues/*.md` 는 그 미러다.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time

if __package__ in (None, ""):                    # `python3 qa/run.py` 로 부를 때
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa import oracles, scenarios                                   # noqa: E402
from qa.env import ROOT, HomeSlot, Refused, new_run_id, stamp       # noqa: E402
from qa.findings import Finding, Run, Skipped, render_report, write_findings  # noqa: E402
from qa.session import EnvBroken, Session                           # noqa: E402

OUT_ROOT = os.path.join(ROOT, "qa", "out")
#: 트래커 저장소는 같은 모노리포의 형제다. 다른 데 뒀으면 `ISSUE_REPO` 로 가리킨다.
DEFAULT_ISSUE_REPO = os.path.join(os.path.dirname(ROOT), "issue")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


class _Step:
    """스텝 하나. `skip(사유)` 로 명시 SKIP 을 낸다(조용한 `return` 금지)."""

    def __init__(self, ctx, name):
        self.ctx = ctx
        self.name = name
        self._skipped = None

    def skip(self, reason: str):
        self._skipped = reason

    def __enter__(self):
        self.ctx.current = self.name
        self._before = len(self.ctx.findings)
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            return False                          # 예외는 위에서 잡는다(환경 실패)
        # ★ 상시 오라클은 **스텝마다** 돈다 — 어느 스텝이 깼는지가 곧 귀속이다.
        for oracle in oracles.STANDING:
            for f in oracle(self.ctx):
                self.ctx.findings.append(f)
        if self._skipped:
            self.ctx.skipped.append(Skipped(self.ctx.scenario, self.name, self._skipped))
            verdict = f"건너뜀 — {self._skipped}"
        else:
            grew = len(self.ctx.findings) - self._before
            verdict = "OK" if not grew else f"결함 {grew}건"
        self.ctx.steps.append((self.ctx.scenario, self.name, verdict))
        return False


class Ctx:
    """시나리오가 보는 세상. 여기 없는 것은 시나리오가 못 만진다(블랙박스 유지)."""

    def __init__(self, slot: HomeSlot, session: Session, scenario: str, seed: int):
        self.slot = slot
        self.session = session
        self.scenario = scenario
        self.seed = seed
        self.rng = random.Random(seed)            # 결정론 우선 — 시드는 리포트에 남는다
        self.current = "env-up"                   # 지금 열려 있는 스텝(결함 귀속용)
        self.findings: list[Finding] = []
        self.skipped: list[Skipped] = []
        self.steps: list[tuple[str, str, str]] = []

    def step(self, name: str) -> _Step:
        """`with ctx.step("split"):` — 블록을 나갈 때 상시 오라클이 돈다."""
        return _Step(self, name)

    def fail(self, *, oracle, key, severity, title, expected, actual, **kw):
        self.findings.append(Finding(
            scenario=self.scenario, oracle=oracle, key=key, severity=severity,
            title=title, expected=expected, actual=actual, step=self.current, **kw))


def run_scenario(mod, slot: HomeSlot, seed: int) -> Ctx:
    """시나리오 하나를 끝까지 돌린다. 스택은 시나리오마다 새로 세운다(교차 오염 금지)."""
    session = Session(slot)
    ctx = Ctx(slot, session, mod.NAME, seed)
    session.start()
    try:
        mod.run(ctx)
    finally:
        session.stop()
    return ctx


def bake(args) -> tuple[int, str]:
    """런 한 벌을 돌고 산출물을 굽는다. 반환 `(rc, run_dir)`."""
    mods = scenarios.by_name(args.scenario) if args.scenario else scenarios.by_tier(args.tier)
    if args.scenario and not mods:
        print(f"그런 시나리오가 없다: {args.scenario} — "
              f"있는 것: {', '.join(s.NAME for s in scenarios.REGISTRY)}", file=sys.stderr)
        return 2, ""
    if not mods:
        # ⛔ 빈 결과는 통과가 아니라 고장이다(scripts/check_all.py 와 같은 규칙).
        print("돌 시나리오가 없다 — 빈 런은 통과가 아니다", file=sys.stderr)
        return 2, ""

    run_id = new_run_id()
    seed = args.seed if args.seed is not None else int(time.time()) & 0xFFFF
    st = stamp()
    run = Run(runId=run_id, seed=seed, scenarios=[m.NAME for m in mods], **st)
    run_dir = os.path.join(OUT_ROOT, run_id)

    findings: list[Finding] = []
    skipped: list[Skipped] = []
    steps: list[tuple[str, str, str]] = []

    for i, mod in enumerate(mods, 1):
        # ⚠ 슬롯 이름에 시나리오 이름을 붙이지 않는다 — 소켓 경로가 AF_UNIX 한계를 넘는다
        #   (`env.SUN_PATH_MAX`. 이 층의 첫 런이 정확히 거기서 죽었다). 번호로 잇고
        #   무엇이 몇 번인지는 바로 아래 줄이 말한다.
        slot = HomeSlot(f"{run_id}-{i}")
        print(f"[qa] {mod.NAME} — {mod.TITLE}  (슬롯 {i})")
        try:
            with slot:
                try:
                    ctx = run_scenario(mod, slot, seed)
                except (EnvBroken, Refused) as e:
                    # ⛔ 환경 구성 실패도 결함이다 — 조용히 빠지지 않는다.
                    findings.append(Finding(
                        scenario=mod.NAME, oracle="env/stack_up", key="stack-up",
                        severity="S1", step="env-up",
                        title=f"QA 스택을 세우지 못한다 — {mod.NAME}",
                        expected="격리 슬롯 위에 데몬이 뜨고 조작을 받는다",
                        actual=str(e)))
                    steps.append((mod.NAME, "env-up", f"환경 실패 — {e}"))
                    continue
                except Exception as e:                     # noqa: BLE001
                    # 시나리오가 도중에 터졌다. ⛔ **여기서 죽지 않는다** — 죽으면 앞서
                    # 돈 시나리오의 결함까지 산출물 없이 사라진다. 대신 그 사실을 결함으로
                    # 남긴다. 원인이 제품인지 이 층인지는 사람이 가른다(제목이 그렇게 말한다).
                    findings.append(Finding(
                        scenario=mod.NAME, oracle="qa/scenario_crashed", key=type(e).__name__,
                        severity="S2", step="—",
                        title=f"QA 시나리오가 예외로 멈춘다 — {type(e).__name__}",
                        expected="시나리오가 끝까지 돌고 판정만 남긴다",
                        actual=f"{type(e).__name__}: {e}"))
                    steps.append((mod.NAME, "—", f"예외로 중단 — {type(e).__name__}: {e}"))
                    continue
                findings += ctx.findings
                skipped += ctx.skipped
                steps += ctx.steps
        finally:
            if args.keep:
                print(f"[qa] 슬롯 보존: {slot.home}")
            else:
                slot.wipe()

    os.makedirs(run_dir, exist_ok=True)
    fpath = write_findings(run_dir, run, findings)
    rpath = os.path.join(run_dir, "REPORT.md")
    with open(rpath, "w", encoding="utf-8") as fh:
        fh.write(render_report(run, findings, skipped, steps))

    print(f"\n[qa] {run_id} — 결함 {len(findings)} · 건너뜀 {len(skipped)} · "
          f"빌드 {run.build}{' (미제출 편집 있음)' if run.dirty else ''}")
    for f in findings:
        print(f"  [{f.severity}] {f.title}  ({f.oracle} · {f.fp})")
    for s in skipped:
        print(f"  [skip] {s.scenario}/{s.step} — {s.reason}")
    print(f"[qa] 산출물: {fpath}\n           {rpath}")

    if findings:
        return 1, run_dir
    if skipped:
        return 3, run_dir
    return 0, run_dir


def ingest(run_dir: str, dry_run: bool) -> int:
    """트래커에 담는다 — **M2 의 유일한 유입 경로**다.

    ⛔ 이 호출이 없으면 여기서 찾은 결함은 **어디에도 안 들어간다.** M2 에서는 러너가
       저장소에 쓰지 않고 트래커의 `sync` 도 저장소를 안 읽는다(pytmux/pytmux-132).
    """
    # ⚠ 절대경로로 넘긴다 — 트래커 CLI 는 **자기 저장소**를 cwd 로 돌므로 상대경로는
    #   거기서 다시 풀린다(우연히 맞는 날이 있어서 더 나쁘다).
    findings = os.path.abspath(os.path.join(run_dir, "findings.json"))
    if not os.path.exists(findings):
        print(f"findings.json 이 없다: {findings}", file=sys.stderr)
        return 2
    repo = os.environ.get("ISSUE_REPO") or DEFAULT_ISSUE_REPO
    cli = os.path.join(repo, "bin", "issue.mjs")
    if not os.path.exists(cli):
        print(f"트래커 CLI 를 못 찾았다: {cli} (ISSUE_REPO 로 가리킬 것)", file=sys.stderr)
        return 2
    argv = ["node", cli, "ingest-findings", "--project", "pytmux", "--run", findings]
    if dry_run:
        argv.append("--dry-run")
    print(f"[qa] $ {' '.join(argv)}")
    return subprocess.run(argv, cwd=repo).returncode


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="pytmux QA 런")
    ap.add_argument("--scenario", help="시나리오 이름 하나만")
    ap.add_argument("--tier", help="티어(T0 …)")
    ap.add_argument("--seed", type=int, help="결정론 시드(리포트에 남는다)")
    ap.add_argument("--ingest", action="store_true", help="트래커에 담는다")
    ap.add_argument("--dry-run", action="store_true", help="--ingest 를 시늉만")
    ap.add_argument("--keep", action="store_true", help="격리 슬롯을 안 지운다")
    ap.add_argument("--run-dir", help="이미 구운 런을 담는다(굽지 않는다)")
    ap.add_argument("--list", action="store_true", help="시나리오 목록만 찍는다")
    args = ap.parse_args(argv)

    if args.list:
        for s in scenarios.REGISTRY:
            print(f"{s.TIER}\t{s.NAME}\t{s.TITLE}")
        return 0

    if args.run_dir:
        if not args.ingest:
            print("--run-dir 은 --ingest 와 같이 쓴다", file=sys.stderr)
            return 2
        return ingest(args.run_dir, args.dry_run)

    rc, run_dir = bake(args)
    if args.ingest and run_dir:
        # ⚠ 결함이 있어도 담는다 — 담는 것이 이 도구의 목적이다. 판정(rc)은 그대로 돌려준다.
        irc = ingest(run_dir, args.dry_run)
        if irc != 0:
            print(f"[qa] 흡수 실패 rc={irc} — 결함이 트래커에 안 들어갔다", file=sys.stderr)
            return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
