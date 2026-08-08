"""메타 QA — **QA 를 누가 검증하나**에 대한 답.

형제 프로젝트(STS)의 `qa/selftest/` 와 같은 자리다: 고의로 결함을 심어 **오라클이 무는지**
본다. 이 저장소가 이미 쓰는 변이 규율과 같은 것이라 손에 익다.

⛔ **여기 있는 것은 QA 층 자체의 시험이다** — 실 데몬을 띄우지 않는다(그건 `qa/run.py` 가
   한다). 이 파일이 지키는 것은 넷이다:

   ⑴ 오라클이 **정말로 문다**(뮤테이션 — 안 물면 그 오라클은 공허하다)
   ⑵ 지문이 **런에 따라 안 흔들린다**(흔들리면 매 런 새 이슈가 태어난다)
   ⑶ `findings.json` 이 **트래커 계약**을 지킨다(어긋나면 흡수가 통째로 거부된다)
   ⑷ 안전 규율 — QA 층 어디에도 **이름으로 죽이는 코드가 없다**
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from qa import oracles, scenarios                                    # noqa: E402
from qa.env import HomeSlot, Refused, SLOT_BASE, new_run_id          # noqa: E402
from qa.findings import Finding, Run, Skipped, fingerprint, render_report, write_findings  # noqa: E402


class _FakeSession:
    """오라클에 먹일 최소한의 세션. 실 데몬 없이 **판정만** 재려는 것이다."""

    def __init__(self, blocks=()):
        self._blocks = list(blocks)

    def error_blocks(self):
        return list(self._blocks)


class _FakeCtx:
    def __init__(self, slot, session, scenario="T0-core-loop", current="probe"):
        self.slot = slot
        self.session = session
        self.scenario = scenario
        self.current = current


def _slot():
    return HomeSlot(new_run_id() + "-selftest")


# ── ⑴ 오라클이 무는가 (뮤테이션) ──────────────────────────────────────────────

def test_no_traceback_oracle_bites_only_when_there_is_one():
    ctx = _FakeCtx(_slot(), _FakeSession())
    assert oracles.no_traceback(ctx) == [], "깨끗한 로그에 결함을 내면 위양성이다"

    block = ('Traceback (most recent call last)\n'
             '  File "pytmuxlib/server.py", line 1, in x\n'
             'RuntimeError: 심은 결함\n')
    found = oracles.no_traceback(_FakeCtx(_slot(), _FakeSession([block])))
    assert len(found) == 1, "심은 트레이스백을 오라클이 못 물었다 — 공허한 오라클"
    f = found[0]
    assert f.severity == "S1"
    assert "RuntimeError: 심은 결함" in f.title, f.title


def test_no_traceback_fingerprint_ignores_paths_and_line_numbers():
    """같은 예외면 파일 경로·줄 번호가 달라도 **같은 지문**이어야 한다.

    안 그러면 코드가 한 줄만 움직여도 트래커가 새 이슈를 열고, 재발 판정이 통째로 죽는다.
    """
    def block(path, line):
        return (f'Traceback (most recent call last)\n  File "{path}", line {line}, in x\n'
                'RuntimeError: 같은 결함\n')

    a = oracles.no_traceback(_FakeCtx(_slot(), _FakeSession([block("a.py", 10)])))[0]
    b = oracles.no_traceback(_FakeCtx(_slot(), _FakeSession([block("b.py", 999)])))[0]
    assert a.fp == b.fp, f"같은 예외인데 지문이 갈렸다: {a.fp} != {b.fp}"


def test_screen_judgement_bites_on_a_dead_or_empty_client():
    """실 클라 화면 판정이 **정말로 무는지**.

    ⚠ 이 오라클이 공허하면 T0 의 핵심(실 PTY·실 클라)이 통째로 장식이 된다 — 화면이
    비어도 초록이 나오기 때문이다. 그래서 세 부류를 심어 각각 무는지 본다.
    """
    from qa.scenarios.t0_core_loop import _judge_screen

    good = "┌──┐\n│ 1:zsh  2:zsh │\n└──┘"

    class _C(_FakeCtx):
        def __init__(self):
            super().__init__(_slot(), _FakeSession())
            self.findings = []

        def fail(self, *, oracle, key, severity, title, expected, actual, **kw):
            self.findings.append((oracle, severity))

    ok = _C()
    _judge_screen(ok, good, True, "probe")
    assert ok.findings == [], f"멀쩡한 화면에 결함을 냈다 — 위양성: {ok.findings}"

    dead = _C()
    _judge_screen(dead, good, False, "probe")
    assert dead.findings == [("client/alive", "S1")], dead.findings

    crashed = _C()
    _judge_screen(crashed, "Traceback (most recent call last)\n  …", True, "probe")
    assert crashed.findings == [("client/no_traceback", "S1")], crashed.findings

    blank = _C()
    _judge_screen(blank, "", True, "probe")
    assert blank.findings == [("client/renders_tree", "S2")], blank.findings


def test_home_isolation_oracle_bites_when_the_env_leaks():
    """⛔ 이 층에서 가장 비싼 결함 — 격리가 새면 우리는 **사용자의 라이브 데몬**을 운전한다.

    뮤테이션: 슬롯 안에서 `PYTMUX_HOME` 을 지운다(= 환경변수가 새 나간 상황을 흉내낸다).
    """
    slot = _slot()
    with slot:
        assert oracles.home_isolated(_FakeCtx(slot, _FakeSession())) == [], \
            "격리가 서 있는데 결함을 냈다 — 위양성"
        saved = os.environ.pop("PYTMUX_HOME")
        try:
            found = oracles.home_isolated(_FakeCtx(slot, _FakeSession()))
        finally:
            os.environ["PYTMUX_HOME"] = saved
    assert len(found) == 1, "격리가 샜는데 오라클이 안 물었다"
    assert found[0].severity == "S1"
    assert "격리가 새고" in found[0].title
    slot.wipe()


def test_slot_refuses_a_home_outside_the_scratch_base():
    """⛔ 스크래치 밖을 홈으로 삼으면 그다음 `kill-server` 가 남의 서버를 내린다."""
    bad = HomeSlot("qa-bad", base=os.path.expanduser("~"))
    try:
        bad.__enter__()
    except Refused:
        pass
    else:
        bad.__exit__()
        raise AssertionError("스크래치 밖 홈을 받아들였다 — 안전 관문이 죽었다")

    # 슬롯 이름 규약(qa-)도 관문이다 — 실수로 기존 디렉터리를 겨누는 것을 막는다.
    named = HomeSlot("live", base=SLOT_BASE)
    try:
        named.__enter__()
    except Refused:
        return
    named.__exit__()
    raise AssertionError("qa- 로 시작하지 않는 슬롯 이름을 받아들였다")


# ── ⑵ 지문 ────────────────────────────────────────────────────────────────────

def test_fingerprint_is_stable_and_distinct():
    same = fingerprint("T0-core-loop", "tree/split", "split-h")
    assert same == fingerprint("T0-core-loop", "tree/split", "split-h")
    assert same != fingerprint("T0-core-loop", "tree/split", "split-v")
    assert same != fingerprint("T0-core-loop", "tree/new_window", "split-h")
    assert same != fingerprint("T1-commands", "tree/split", "split-h")


# ── ⑶ 트래커 계약 ─────────────────────────────────────────────────────────────

#: `issue/src/intake.mjs` 의 `ingestFindings` 가 읽는 키. 하나라도 빠지면 흡수가 죽거나
#: 이슈가 빈 칸으로 태어난다. ⛔ 이 목록을 줄이려면 **저쪽 코드를 먼저 보고** 줄인다.
TRACKER_KEYS = {"fp", "title", "severity", "scenario", "oracle", "step",
                "expected", "actual", "env", "count", "flaky", "staged_only"}


def test_findings_json_matches_the_tracker_contract():
    f = Finding(scenario="T0-core-loop", oracle="tree/split", key="split-h",
                title="제목", severity="S2", expected="기대", actual="실제", step="split")
    run = Run(runId="qa-20260806-000000", build="p4-1", head=1, dirty=False,
              stamped=True, seed=7, scenarios=["T0-core-loop"])
    with tempfile.TemporaryDirectory() as d:
        path = write_findings(d, run, [f])
        doc = json.load(open(path, encoding="utf-8"))
    assert set(doc["run"]) == {"runId", "profile", "build", "head", "seed"}, doc["run"]
    assert doc["run"]["runId"] == "qa-20260806-000000", "runId 가 멱등 키다"
    got = doc["findings"][0]
    assert TRACKER_KEYS <= set(got), f"빠진 키: {TRACKER_KEYS - set(got)}"
    assert got["fp"] == f.fp and len(got["fp"]) == 16


def test_severity_outside_the_convention_is_refused():
    try:
        Finding(scenario="s", oracle="o", key="k", title="t", severity="S9",
                expected="e", actual="a")
    except ValueError:
        return
    raise AssertionError("규약 밖 심각도를 받아들였다 — 트래커가 그 행을 못 읽는다")


def test_skip_without_a_reason_is_refused():
    """⛔ 조용한 SKIP 금지 — 사유 없는 건너뜀은 회계에서 통과와 구분되지 않는다."""
    try:
        Skipped("T0-core-loop", "client-render", "   ")
    except ValueError:
        return
    raise AssertionError("사유 없는 SKIP 을 받아들였다")


def test_report_never_calls_a_run_with_skips_green():
    """⛔ 초록은 비싸다 — 미검증이 있으면 초록이 아니다(원칙 ⓑ)."""
    run = Run(runId="r", build="p4-1", head=1, dirty=False, stamped=True, seed=1,
              scenarios=["T0-core-loop"])
    txt = render_report(run, [], [Skipped("T0-core-loop", "client-render", "POSIX 전용")], [])
    assert "초록 아님" in txt, txt[:400]
    assert "POSIX 전용" in txt

    clean = render_report(run, [], [], [])
    assert "판정**: 초록" in clean, clean[:400]


def test_unstamped_report_says_it_is_not_a_submitted_build():
    """웹 공개에는 빌드 스탬프가 필수다 — 어느 개정인지 없으면 리포트가 뜻을 잃는다."""
    run = Run(runId="r", build="unstamped", head=None, dirty=False, stamped=False,
              seed=1, scenarios=["T0-core-loop"])
    assert "제출본 QA 아님" in render_report(run, [], [], [])
    dirty = Run(runId="r", build="p4-1", head=1, dirty=True, stamped=True, seed=1,
                scenarios=["T0-core-loop"])
    assert "미제출 편집" in render_report(dirty, [], [], [])


# ── ⑷ 안전 규율 · 등록소 ──────────────────────────────────────────────────────

#: ⛔ 이 문자열이 QA 층에 생기는 순간 사고가 되살아난다(같은 날 3회 재발한 그것).
FORBIDDEN = ("pkill", "killall", "taskkill", "Get-Process", "pgrep",
             "Stop-Process", "-9 $(")


def _qa_sources():
    for base, _dirs, names in os.walk(os.path.join(ROOT, "qa")):
        if "out" in base.split(os.sep) or "__pycache__" in base:
            continue
        for n in names:
            if n.endswith(".py"):
                yield os.path.join(base, n)


def _code_atoms(path):
    """**실행되는 것만** 뽑는다 — 문자열 리터럴·식별자·속성 이름.

    ⚠ 판정을 소스 문자열 검색으로 하면 **금지어를 설명한 주석·독스트링이 자기 게이트를
    깬다**(실측 — 이 시험을 그렇게 처음 썼다). 규율을 적어 둔 자리가 규율 위반으로
    읽히면 다음 사람은 설명을 지우게 된다. AST 로 보면 그 함정이 없다.
    """
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docs:
            yield node.value
        elif isinstance(node, ast.Name):
            yield node.id
        elif isinstance(node, ast.Attribute):
            yield node.attr


def test_qa_layer_never_kills_by_name():
    """정리는 **pid 로만** 한다 — 이름 매칭은 사용자의 라이브 데몬을 함께 죽인다."""
    bad = []
    for path in _qa_sources():
        for atom in _code_atoms(path):
            for word in FORBIDDEN:
                if word in atom:
                    bad.append(f"{os.path.relpath(path, ROOT)}: {word}")
    assert not bad, "QA 층에 이름으로 죽이는 코드가 생겼다: " + ", ".join(bad)


def test_the_kill_by_name_gate_actually_bites():
    """⛔ 게이트 자체를 변이로 검증한다 — 안 무는 게이트는 초록을 파는 장식이다."""
    with tempfile.TemporaryDirectory() as d:
        planted = os.path.join(d, "mutant.py")
        with open(planted, "w", encoding="utf-8") as fh:
            fh.write('"""pkill 을 설명만 하는 독스트링."""\n'
                     'import subprocess\n'
                     'def clean():\n'
                     '    subprocess.run(["pkill", "-f", "python"])\n')
        atoms = list(_code_atoms(planted))
        assert any("pkill" in a for a in atoms), "심은 호출을 게이트가 못 봤다"
        assert sum("pkill" in a for a in atoms) == 1, \
            "독스트링까지 셌다 — 설명이 자기 게이트를 깨는 함정이 되살아났다"


def test_scenario_registry_is_the_ssot():
    """파일만 만들고 등록을 잊으면 그 시나리오는 **영영 안 돈다**(조용한 커버리지 구멍)."""
    d = os.path.join(ROOT, "qa", "scenarios")
    on_disk = {n[:-3] for n in os.listdir(d)
               if n.endswith(".py") and not n.startswith("__")}
    registered = {m.__name__.rsplit(".", 1)[-1] for m in scenarios.REGISTRY}
    assert on_disk == registered, (
        f"등록소와 디렉터리가 갈렸다 — 미등록 {on_disk - registered} · "
        f"유령 {registered - on_disk}")


def test_every_scenario_declares_the_contract():
    for mod in scenarios.REGISTRY:
        for attr in ("NAME", "TIER", "TITLE"):
            assert getattr(mod, attr, None), f"{mod.__name__} 에 {attr} 이 없다"
        assert callable(getattr(mod, "run", None)), f"{mod.__name__}.run 이 없다"
        # 트래커는 시나리오 이름 앞머리에서 `tier:` 라벨을 뽑는다(intake.mjs labelsFor).
        assert mod.NAME.startswith(mod.TIER), \
            f"{mod.NAME} 은 티어({mod.TIER})로 시작해야 트래커가 라벨을 뽑는다"
        assert scenarios.by_name(mod.NAME) == (mod,)
        assert mod in scenarios.by_tier(mod.TIER)
