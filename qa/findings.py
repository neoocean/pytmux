"""qa/findings.py — 결함은 문서가 아니라 **데이터**다(원칙 ⓔ).

★ **상태 기계를 여기서 다시 만들지 않는다.** 형제 프로젝트가 저 자리에 둔
`open → fixed → watching → regressed` 전이·지문 병합·재발 시 심각도 +1·영구 회귀 승격은
**이슈 트래커가 이미 갖고 있고**(`issue/src/intake.mjs`), pytmux 는 그 트래커의 **M2**
프로젝트다 — 이슈의 정본이 트래커다. 여기서 같은 판정을 또 쓰면 **판정이 두 벌**이 되고,
두 벌은 곧 두 개의 SSOT 다.

그래서 이 파일이 하는 일은 하나뿐이다: **트래커가 먹는 모양**(`findings.json`)으로 굽는다.
계약은 `issue/src/intake.mjs` 의 `ingestFindings` 가 정한다:

    {"run": {"runId", "profile", "build", "head", "seed"},
     "findings": [{"fp", "title", "severity", "scenario", "oracle", "step",
                   "expected", "actual", "env", "count", "flaky", "staged_only"}]}

⛔ **`fp`(지문)가 없으면 트래커가 흡수를 거부한다** — 조용한 병합 금지가 그쪽 계약이다.
   지문은 **런마다 안 흔들려야** 한다(시각·runId·경로가 들어가면 매 런 새 이슈가 태어나
   "자동 QA 는 첫 주에 익사한다"). 그래서 `(시나리오, 오라클, 키)` 셋으로만 짓는다.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

SEVERITIES = ("S1", "S2", "S3", "S4")


def fingerprint(scenario: str, oracle: str, key: str) -> str:
    """지문. ⛔ **런에 따라 달라지는 값을 넣지 마라** — 시각·runId·pid·임시 경로가 들어간
    지문은 매 런 새 이슈를 만든다(중복 병합이 통째로 무력화된다)."""
    raw = f"pytmux\x00{scenario}\x00{oracle}\x00{key}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class Finding:
    """한 건의 결함. 필드 이름은 트래커 계약 그대로다(중간 번역을 두지 않는다)."""
    scenario: str
    oracle: str
    key: str                 # 지문의 재료 — 같은 결함이면 런이 달라도 같은 값이어야 한다
    title: str               # 이슈 제목이 된다 — 시각·경로 같은 휘발성 값을 넣지 않는다
    severity: str
    expected: str
    actual: str
    step: str = ""
    env: str = "desktop"
    count: int = 1
    flaky: bool = False
    staged_only: bool = False

    def __post_init__(self):
        if self.severity not in SEVERITIES:
            raise ValueError(f"심각도가 규약 밖이다: {self.severity}")

    @property
    def fp(self) -> str:
        return fingerprint(self.scenario, self.oracle, self.key)

    def as_dict(self) -> dict:
        return {
            "fp": self.fp, "title": self.title, "severity": self.severity,
            "scenario": self.scenario, "oracle": self.oracle, "step": self.step,
            "expected": self.expected, "actual": self.actual, "env": self.env,
            "count": self.count, "flaky": self.flaky, "staged_only": self.staged_only,
        }


@dataclass
class Skipped:
    """건너뛴 검사. ⛔ **통과가 아니다** — 안 돌린 것은 「미검증」으로 보고한다(원칙 ⓑ).
    사유가 비면 회계가 뜻을 잃으므로 사유를 요구한다."""
    scenario: str
    step: str
    reason: str

    def __post_init__(self):
        if not self.reason.strip():
            raise ValueError("건너뛴 사유가 비었다 — 조용한 SKIP 금지")


@dataclass
class Run:
    """런 한 벌의 정체. `runId` 가 트래커의 멱등 키다."""
    runId: str
    build: str
    head: int | None
    dirty: bool
    stamped: bool
    seed: int
    profile: str = "local-isolated"
    scenarios: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"runId": self.runId, "profile": self.profile, "build": self.build,
                "head": self.head, "seed": self.seed}


def write_findings(out_dir: str, run: Run, findings: list[Finding]) -> str:
    """`findings.json` 을 굽는다. 트래커는 이 파일만 있으면 며칠 밀렸다가 흡수해도 된다."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "findings.json")
    doc = {"run": run.as_dict(), "findings": [f.as_dict() for f in findings]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


def render_report(run: Run, findings: list[Finding], skipped: list[Skipped],
                  steps: list[tuple[str, str, str]]) -> str:
    """사람이 읽는 리포트.

    ★ **첫 줄이 빌드 스탬프다.** 어느 개정을 잰 런인지 없으면 리포트가 뜻을 잃는다 —
    미제출 편집이 섞였으면 그 사실도 첫 줄에 나온다("그 런은 「제출본 QA」가 아니다").
    ⛔ 결함의 SSOT 는 이 리포트가 아니라 **트래커의 이슈**다. 여기 있는 것은 그 런의
    관측 요약이고, 사라져도 이슈는 남는다.
    """
    stamp = run.build if run.stamped else "unstamped(p4 없음 — 제출본 QA 아님)"
    if run.dirty:
        stamp += " + 미제출 편집 있음(제출본 QA 아님)"
    green = not findings and not skipped
    lines = [
        f"# pytmux QA — {run.runId}",
        "",
        f"- **빌드**: {stamp}",
        f"- **프로필**: {run.profile} (격리 홈 슬롯 · 라이브 부착 없음)",
        f"- **시드**: {run.seed}",
        f"- **시나리오**: {', '.join(run.scenarios) or '(없음)'}",
        f"- **판정**: {'초록' if green else '초록 아님'} — "
        f"결함 {len(findings)} · 건너뜀 {len(skipped)}",
        "",
        "## 스텝",
        "",
    ]
    for scenario, step, verdict in steps:
        lines.append(f"- `{scenario}` / `{step}` — {verdict}")
    lines += ["", "## 결함", ""]
    if not findings:
        lines.append("없음.")
    for f in findings:
        lines += [
            f"### [{f.severity}] {f.title}",
            "",
            f"- 지문 `{f.fp}` · 오라클 `{f.oracle}` · 스텝 `{f.step or '—'}`",
            f"- 기대: {f.expected}",
            f"- 실제: {f.actual}",
            "",
        ]
    lines += ["## 건너뜀 (미검증 — 통과가 아니다)", ""]
    if not skipped:
        lines.append("없음.")
    for s in skipped:
        lines.append(f"- `{s.scenario}` / `{s.step}` — {s.reason}")
    lines += [
        "",
        "---",
        "",
        "결함의 정본은 이 파일이 아니라 이슈 트래커다(pytmux 는 M2). 담는 길:",
        "",
        "```sh",
        f"python3 qa/run.py --ingest --run-dir qa/out/{run.runId}",
        "```",
        "",
    ]
    return "\n".join(lines)
