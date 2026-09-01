"""qa/repo.py — 저장소 위생: **미러 빚을 조용하지 않게 만든다**(`pytmux/pytmux-153`).

## 왜 QA 런 안에 있나

`scripts/publish_check.py` 는 미러 빚을 **이미 정확히 안다**. 그런데 `pytmux-38` 은 **두 번**
열렸다 — 처음은 「클론이 없다」, 두 번째는 「클론은 있는데 세션들이 p4 만 제출한다」. 원인이
옮겨 다녔지 사라지지 않았고, 그 이슈가 자기 본문에 진단을 적어 두었다:

> 미러 빚은 **조용하다** — 한쪽만 게시해도 그 워크스페이스에서는 아무 신호가 없다.
> 세션 보고서에만 적으면 다음 세션은 그 보고서를 안 읽는다.

⛔ **그러니 여기서 할 일은 「검사를 만든다」가 아니다** — 검사는 있다. 할 일은 그 검사를
**아무도 안 돌려도 매일 도는 자리**로 옮기는 것이고, 이 저장소에서 그 자리는 야간 QA 런
(`tools/launchd/org.woojinkim.pytmux.qa.plist` · 04:00 · `--ingest`)이다. 그 길로 나가면
빚이 **트래커의 이슈**가 된다 — 지문으로 병합되고, 큐에 서고, 안 고치면 매일 다시 관측된다.
「제출 뒤에 밀기를 잊지 말자」는 사람의 규율이고 세 번째가 온다.

## ⛔ 낡은 기준선으로는 **한 건도 만들지 않는다**

이 층이 만들 수 있는 최악은 「매일 남의 게시를 내 빚이라고 이슈로 세우는 것」이다. 실측
(2026-08-10)으로 이 워크스페이스의 클론은 `origin/main` 보다 30커밋 뒤였고, 그 상태로 재면
**143 + 61건**이 빚으로 보였는데 표본 3개 중 2개가 이미 origin 에 있었다. 그래서 순서가 계약이다:

    ① measure_freshness() → 낡았으면 **드리프트를 재지 않는다**
       · 「낡았다」 자체는 Finding 으로 낸다 — 사람이 고칠 수 있는 것이고(당기면 된다),
         Skipped 로만 두면 런 회계에만 남아 아무도 안 본다.
       · 못 잰 드리프트는 Skipped 로 남긴다 — ⛔ **초록이 아니다**(원칙 ⓑ · rc 3).
    ② measure_drift() → 갈래마다 Finding 하나

★ **다만 「낡음」에 갈래가 둘이다**(pytmux-388). 로컬 HEAD 가 뒤처지기만 했으면(로컬 커밋
없음) `measure_freshness` 가 `baseline` 을 돌려주고, 그때는 **멈추지 않고 그 기준선 위에서**
잰다. 그 갈래를 멈추던 시절에 이 감시는 같은 「기준선이 낡았다」를 **엿새 · 7회** 냈고, 그
붉은 줄 뒤에서 진짜 빚(내용이 갈린 114 파일 · 미러가 depot 보다 39 CL 뒤)이 아무에게도 안
보였다. 미룬 판정이 빚을 가린 것이다.

## 지문

`fingerprint(scenario, oracle, key)` 셋으로만 짓는다(`qa/findings.py`). 그래서 **key 는
드리프트 갈래**(`publish_check.SEVERITY` 의 키)이고 파일 목록·건수는 안 들어간다 — 넣으면
파일 하나가 바뀔 때마다 새 이슈가 태어난다. 제목도 같은 이유로 건수를 안 담는다(휘발성 값은
`actual` 로 간다).
"""
from __future__ import annotations

import importlib.util
import os

from qa.env import ROOT
from qa.findings import Finding, Skipped

#: 시나리오 이름 자리. 제품 시나리오가 아니라 **게시 상태**를 재므로 티어(`T\\d`)를 안 쓴다.
NAME = "repo-mirror"
STEP = "mirror"

_EXPECTED = ("p4 submit 과 git push 가 **같이** 나간다(CLAUDE.md §게시) — "
             "자동 미러가 없어 한쪽만 게시하면 다른 쪽이 영영 뒤처진다")


def _load_publish_check():
    """`scripts/publish_check.py` 를 모듈로 연다.

    ⚠ `scripts/` 는 패키지가 아니라(`__init__.py` 없음) 일반 import 가 안 된다. 파일 경로로
    여는 이 방식은 `tests/test_publish_check.py` 가 이미 쓰는 것과 같다 — 사본을 만들지
    않는다는 것이 요점이다. **판정은 게이트 하나가 한다**(두 벌이면 곧 두 개의 SSOT).
    """
    path = os.path.join(ROOT, "scripts", "publish_check.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("publish_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _workspace(pc) -> str:
    """어느 워크스페이스의 빚인가. ⛔ **라벨(env)이 아니라 본문에 넣는다** — 머신마다 달라서
    라벨로 만들면 라벨이 계속 늘고, 지문에 넣으면 같은 빚이 머신 수만큼 이슈가 된다.
    pytmux-38 이 「alienware 에는 클론이 없다」였던 것처럼 귀속은 여전히 필요하다."""
    try:
        # ⚠ `-F` 는 **`-ztag` 와 함께**여야 먹는다 — 빼면 조용히 빈 출력이 나오고(rc 0!)
        #   귀속이 `?` 로 남는다(실측 2026-08-10).
        rc, txt = pc.run(["p4", "-ztag", "-F", "%clientName%", "info"])
        client = txt.strip().splitlines()[0] if (not rc and txt.strip()) else "?"
    except Exception:                                          # noqa: BLE001
        client = "?"
    return f"{client}@{os.uname().nodename if hasattr(os, 'uname') else 'unknown'}"


def _snippet(items, limit=10) -> str:
    head = ", ".join(str(i) for i in items[:limit])
    return head + (f" … (+{len(items) - limit})" if len(items) > limit else "")


def audit(remote: bool = True):
    """미러 빚을 잰다 → `(findings, skipped, steps)`.

    `steps` 는 `qa/run.py` 의 리포트 표에 그대로 들어가는 `(시나리오, 스텝, 판정)` 이다.
    """
    findings: list[Finding] = []
    skipped: list[Skipped] = []

    pc = _load_publish_check()
    if pc is None:
        skipped.append(Skipped(NAME, STEP, "scripts/publish_check.py 가 없다"))
        return findings, skipped, [(NAME, STEP, "건너뜀 — 게이트 파일이 없다")]

    if not os.path.isdir(os.path.join(ROOT, ".git")):
        # p4 전용 워크스페이스. ⛔ 결함이 아니다 — 여기서는 **잴 것이 아예 없다**.
        skipped.append(Skipped(NAME, STEP,
                               "git 클론이 아니다 — 미러는 다른 워크스페이스에서 본다"))
        return findings, skipped, [(NAME, STEP, "건너뜀 — git 클론이 아니다")]

    try:
        where = _workspace(pc)
        stale, unmeasured, baseline = pc.measure_freshness(remote=remote)
    except Exception as e:                                     # noqa: BLE001
        # 이 층이 터진 것도 조용히 넘기지 않는다(원칙 ⓑ) — 다만 미러의 빚은 아니다.
        findings.append(Finding(
            scenario=NAME, oracle="repo/audit", key="audit-crashed", severity="S3",
            step=STEP, title="미러 감시가 예외로 멈춘다",
            expected="야간 런이 미러 빚을 재고 결함으로 남긴다",
            actual=f"{type(e).__name__}: {e}"))
        return findings, skipped, [(NAME, STEP, f"예외로 중단 — {type(e).__name__}: {e}")]

    for u in unmeasured:
        skipped.append(Skipped(NAME, STEP, f"{u['what']} — {u['detail']}"))

    if stale:
        # ★ 「낡았다」는 **결함이다**. 잴 수 없는 게이트는 없는 게이트와 같고, 고치는 길이
        #   한 줄(fix)로 있으니 사람이 고칠 대상이다. ⛔ 그러면서 드리프트는 **안 잰다**.
        detail = " · ".join(f"{s['what']}: {s['detail']} → {s['fix']}" for s in stale)
        findings.append(Finding(
            scenario=NAME, oracle="repo/baseline", key="baseline-stale", severity="S3",
            step=STEP,
            title="미러 게이트가 낡은 기준선 위에서 돈다 — 판정을 못 낸다",
            expected="게이트가 재는 기준선(git HEAD · p4 have)이 head 와 같다",
            actual=f"워크스페이스 {where} · {detail}"))
        skipped.append(Skipped(NAME, STEP,
                               "기준선이 낡아 미러 드리프트를 안 쟀다 — 낡은 기준선의 "
                               "목록은 남의 게시다(pytmux-153)"))
        return findings, skipped, [(NAME, STEP, "결함 1건 — 기준선이 낡아 미판정")]

    drifts, wip, dunmeasured = pc.measure_drift(baseline=baseline)
    for u in dunmeasured:
        skipped.append(Skipped(NAME, STEP, f"{u['what']} — {u['detail']}"))
    for d in drifts:
        findings.append(Finding(
            scenario=NAME, oracle="repo/mirror", key=d["kind"], severity=d["severity"],
            step=STEP,
            title=f"미러 빚 — {d['head']}({d['why']})",
            expected=_EXPECTED,
            actual=(f"워크스페이스 {where} · {d['count']}개: {_snippet(d['items'])}"
                    f" · 고치는 길: {d['fix']}"),
            count=d["count"]))

    if findings:
        verdict = f"결함 {len(findings)}건"
    elif skipped:
        verdict = "미검증 있음 — 초록 아님"
    else:
        verdict = f"OK — 미러 일치{f' (작업 중 {len(wip)}개)' if wip else ''}"
    if baseline:
        # ⛔ 결함이 아니다(잴 자가 있었다) — 그러나 **안 보이면 안 된다**. 이 클론이
        #   뒤처졌다는 사실은 사람이 고칠 것이라 회차 줄에 실어 보낸다(pytmux-388).
        verdict += f" · 기준선 {baseline['ref']}(로컬 클론이 뒤처졌다 → {baseline['fix']})"
    return findings, skipped, [(NAME, STEP, verdict)]
