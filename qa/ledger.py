"""qa/ledger.py — 커버리지 원장. **무엇을 실제로 지나 봤나**의 회계.

⛔ **미커버는 통과가 아니다**(원칙 ⓑ). 명령 하나가 통째로 죽어도 그 명령을 아무도 안
지나면 QA 는 초록이다 — T0 이 만지는 것은 `split-window -h` 와 `new-window` 둘뿐이었고,
그 상태가 정확히 「초록불 + 명령 표 절반이 고장」이 성립하는 자리다.

원장은 셋을 가른다:

| | 무엇 | 어디에 적히나 |
| --- | --- | --- |
| **지남** | 이번 런에서 실제로 그 명령을 보냈고 서버가 거절하지 않았다 | 리포트의 원장 표 |
| **미커버** | 이번 런에 그 표면을 지나는 시나리오가 **있었는데** 안 지난 명령 | **결함**(S3) — 고칠 사람이 있다(그 시나리오를 넓혀라) |
| **미검증** | 그 표면을 지날 수 있는 시나리오가 **아직 없다** | **건너뜀**(rc 3) — 고칠 사람이 아직 없다(그 티어가 없다) |

★ 가운데와 아래를 가르는 것이 이 파일의 값이다. 둘을 합치면 둘 중 하나가 망가진다 —
전부 결함으로 내면 아무도 못 고치는 이슈가 셋 서고(늑대소년 · 원칙 ⓓ), 전부 건너뜀으로
내면 **새 명령이 생긴 날 구멍이 조용히 늘어난다**(이 원장의 존재 이유가 그것이다).

## 지문

원장 결함의 지문은 `(시나리오, coverage/<표면>, <표면>)` 셋으로만 짓는다 — 커버리지
숫자가 바뀔 때마다 새 이슈가 태어나면 안 되고, **표면 하나 = 이슈 하나**가 옳다.
숫자는 제목이 아니라 본문(`actual`)에 싣는다(제목에 넣으면 한 건 늘 때마다 제목이
흔들린다).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .findings import Finding, Skipped
from .inventory import SURFACES, InventoryBroken, Surface

#: 원장을 소유하는 시나리오. 결함의 `scenario` 칸이라 **티어 라벨의 재료**이기도 하다
#: (트래커가 이름 앞머리에서 `tier:T1` 을 뽑는다 · intake.mjs labelsFor).
LEDGER_SCENARIO = "T1-commands"

#: 서버가 「안 했다」고 답한 말들. 이 답을 받은 명령은 **지난 것이 아니다** —
#: `handle_control` 이 직접 돌려주는 값들이라 제품의 계약 안에 있다.
#: ⚠ 마지막 하나만 CLI 가 낸다(서버가 아예 없을 때) — 그때는 그 런의 회계가 통째로
#: 뜻을 잃으므로 지남으로 세면 안 된다.
REFUSALS = ("no session", "bad-line", "empty", "unsupported", "실행 중인 서버 없음")
REFUSAL_PREFIX = "unknown:"

#: 결함 본문에 이름을 몇 개까지 싣나. 전부 실으면 이슈 본문이 수백 줄이 된다.
NAMES_IN_BODY = 40


def refused(reply: str | None) -> bool:
    """서버가 그 명령을 실제로 수행했나. ⛔ 「보냈다」와 「먹었다」는 다른 사건이다."""
    r = (reply or "").strip()
    return (not r) or r.startswith(REFUSAL_PREFIX) or r in REFUSALS


@dataclass
class Row:
    """원장 한 줄 — 표면 하나의 회계."""
    surface: Surface
    total: int = 0
    covered: tuple[str, ...] = ()
    uncovered: tuple[str, ...] = ()
    stray: tuple[str, ...] = ()
    measured: bool = False           # 이번 런에 그 표면을 지나는 시나리오가 있었나
    error: str = ""                  # 인벤토리를 못 읽었으면 그 사유

    @property
    def pct(self) -> int:
        return round(100 * len(self.covered) / self.total) if self.total else 0


@dataclass
class Ledger:
    """런 하나가 실제로 지난 명령을 모은다. **런 전체가 한 벌**이다 — 시나리오마다
    따로 세면 T0 이 지난 것을 T1 의 원장이 못 본다(이슈 본문의 「T0/T1 이 실제로 지난」)."""

    surfaces: tuple[Surface, ...] = SURFACES
    #: 표면 → 이름 → 마지막 응답. 같은 명령을 여러 번 보내도 한 번으로 센다.
    seen: dict[str, dict[str, str]] = field(default_factory=dict)

    def record(self, surface: str, name: str, reply: str = "ok") -> None:
        """명령 하나를 지났다. 판정(거절이었나)은 `audit` 이 한다 — 여기서는 **관측만**."""
        if not name:
            return
        self.seen.setdefault(surface, {})[name] = reply or ""

    # ── 회계 ─────────────────────────────────────────────────────────────────
    def audit(self, scenarios_run) -> tuple[list[Row], list[Finding], list[Skipped]]:
        """`(원장 줄, 결함, 미검증)`.

        `scenarios_run` 은 **이번 런에서 실제로 돈** 시나리오 이름들이다. 이 값이 없으면
        부분 런(`--scenario T0-core-loop`)이 「T1 이 안 지났다」를 결함으로 신고하게 된다 —
        그건 런의 범위이지 제품의 결함이 아니다.
        """
        ran = set(scenarios_run)
        rows: list[Row] = []
        findings: list[Finding] = []
        skips: list[Skipped] = []

        for surface in self.surfaces:
            row = Row(surface=surface)
            try:
                names = surface.extract()
            except InventoryBroken as e:
                # ⛔ 못 읽은 것을 0건으로 접지 않는다 — 그 순간 커버리지가 100% 로 뜬다.
                row.error = str(e)
                rows.append(row)
                findings.append(Finding(
                    scenario=LEDGER_SCENARIO, oracle="coverage/inventory",
                    key=surface.key, severity="S2", step="ledger",
                    title=f"커버리지 원장이 인벤토리를 못 읽는다 — {surface.title}",
                    expected=f"{surface.where} 에서 명령 목록을 뽑는다",
                    actual=str(e)))
                continue

            hit = {n for n, reply in self.seen.get(surface.key, {}).items()
                   if not refused(reply)}
            row.total = len(names)
            row.covered = tuple(sorted(n for n in names if n in hit))
            row.uncovered = tuple(sorted(n for n in names if n not in hit))
            row.stray = tuple(sorted(hit - set(names)))
            row.measured = bool(surface.reached_by) and surface.reached_by in ran
            rows.append(row)

            if row.stray:
                # 지나 본 이름이 인벤토리에 없다 = 추출기가 좁아졌거나 시나리오가 낡았다.
                # 둘 다 원장을 조용히 거짓말하게 만든다.
                findings.append(Finding(
                    scenario=LEDGER_SCENARIO, oracle="coverage/stray", key=surface.key,
                    severity="S3", step="ledger",
                    title=f"원장이 모르는 명령을 지났다 — {surface.title}",
                    expected=f"지난 명령이 전부 {surface.where} 의 인벤토리 안에 있다",
                    actual=_names("인벤토리 밖", row.stray)))

            if row.measured:
                if row.uncovered:
                    findings.append(Finding(
                        scenario=LEDGER_SCENARIO, oracle=f"coverage/{surface.key}",
                        key=surface.key, severity="S3", step="ledger",
                        title=f"안 지나 본 명령이 있다 — {surface.title}",
                        expected=f"{surface.where} 의 명령을 런이 전부 지난다",
                        actual=f"{len(row.covered)}/{row.total} 지남 ({row.pct}%)\n"
                               + _names("미커버", row.uncovered)))
            elif surface.reached_by is None:
                # 지날 수 있는 시나리오가 아직 없다 → **미검증**이다(결함이 아니다).
                skips.append(Skipped(
                    LEDGER_SCENARIO, f"coverage/{surface.key}",
                    f"{surface.title} {row.total}건을 지나는 시나리오가 아직 없다 — "
                    f"{surface.note}"))
        return rows, findings, skips


def _names(label: str, names) -> str:
    """이름 목록을 본문에 싣는다. ⚠ 전부 싣지 않는다 — 수백 줄짜리 이슈는 안 읽힌다."""
    head = list(names)[:NAMES_IN_BODY]
    more = len(names) - len(head)
    return f"{label} {len(names)}건: " + ", ".join(head) + (f" … 외 {more}건" if more else "")


def render_ledger(rows: list[Row]) -> list[str]:
    """리포트의 「커버리지 원장」 절. ★ **숫자로 뜨는 것**이 이 절의 계약이다."""
    if not rows:
        return ["## 커버리지 원장", "",
                "이번 런에는 원장이 없다 — 원장을 소유한 "
                f"`{LEDGER_SCENARIO}` 가 안 돌았다(부분 런).", ""]
    out = ["## 커버리지 원장", "",
           "| 표면 | 인벤토리 | 지남 | 미커버 | 회계 |",
           "| --- | ---: | ---: | ---: | --- |"]
    for r in rows:
        if r.error:
            out.append(f"| {r.surface.title} | ? | ? | ? | ⛔ 인벤토리를 못 읽었다 |")
            continue
        how = ("결함" if r.measured else
               ("미검증 — 지나는 시나리오가 없다" if r.surface.reached_by is None
                else f"이번 런에 `{r.surface.reached_by}` 가 없었다"))
        out.append(f"| {r.surface.title} | {r.total} | {len(r.covered)} ({r.pct}%) | "
                   f"{len(r.uncovered)} | {how} |")
    out.append("")
    for r in rows:
        if r.error:
            out += [f"- ⛔ `{r.surface.key}` — {r.error}", ""]
            continue
        out.append(f"- `{r.surface.key}` — {r.surface.where}")
        if r.uncovered:
            out.append(f"  - {_names('미커버', r.uncovered)}")
        if r.stray:
            out.append(f"  - {_names('인벤토리 밖', r.stray)}")
    out.append("")
    return out
