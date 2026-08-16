"""메타 QA — **QA 를 누가 검증하나**에 대한 답.

형제 프로젝트(STS)의 `qa/selftest/` 와 같은 자리다: 고의로 결함을 심어 **오라클이 무는지**
본다. 이 저장소가 이미 쓰는 변이 규율과 같은 것이라 손에 익다.

⛔ **여기 있는 것은 QA 층 자체의 시험이다** — 실 데몬을 띄우지 않는다(그건 `qa/run.py` 가
   한다). 이 파일이 지키는 것은 넷이다:

   ⑴ 오라클이 **정말로 문다**(뮤테이션 — 안 물면 그 오라클은 공허하다)
   ⑵ 지문이 **런에 따라 안 흔들린다**(흔들리면 매 런 새 이슈가 태어난다)
   ⑶ `findings.json` 이 **트래커 계약**을 지킨다(어긋나면 흡수가 통째로 거부된다)
   ⑷ 안전 규율 — QA 층 어디에도 **이름으로 죽이는 코드가 없다**
   ⑸ **커버리지 원장이 미커버를 정말로 신고한다**(`pytmux/pytmux-145`) — 이것도 뮤테이션
      이다: 명령 하나를 안 지난 척하면 원장이 그 이름을 대야 한다. 안 물면 원장은
      「100% 커버」라고 말하는 장식이 되고, 그건 오라클이 공허한 것보다 나쁘다
      (안 본 것을 봤다고 말하기 때문이다)
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

from qa import inventory, oracles, scenarios                         # noqa: E402
from qa.env import HomeSlot, Refused, SLOT_BASE, new_run_id          # noqa: E402
from qa.findings import Finding, Run, Skipped, fingerprint, render_report, write_findings  # noqa: E402
from qa.ledger import LEDGER_SCENARIO, Ledger, render_ledger         # noqa: E402


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


class _RecCtx:
    """결함·건너뜀만 받아 적는 ctx. 실 데몬 없이 **판정만** 재려는 것이다."""

    def __init__(self, session=None, scenario="T2-multi-client"):
        self.session = session
        self.scenario = scenario
        self.current = "probe"
        self.findings = []
        self.skips = []

    class _Step:
        def __init__(self, ctx, name):
            self.ctx, self.name = ctx, name

        def skip(self, reason):
            self.ctx.skips.append((self.name, reason))

        def __enter__(self):
            self.ctx.current = self.name
            return self

        def __exit__(self, *exc):
            return False

    def step(self, name):
        return _RecCtx._Step(self, name)

    def fail(self, *, oracle, key, severity, title, expected, actual, **kw):
        self.findings.append((oracle, severity, key, actual))


def test_multi_client_mirror_oracle_bites_when_the_broadcast_goes_one_way():
    """★ **이 이슈의 받아들임 기준**(pytmux-146): 브로드캐스트가 한 쪽에만 가면
    오라클이 물어야 한다.

    ⛔ 「하나라도 받았으면 통과」로 접으면 판정이 클라 하나일 때와 같은 모양이 되고,
       그러면 T2 전체가 초록을 파는 장식이 된다. 그래서 **못 받은 클라를 이름 대서**
       신고하는지까지 본다 — 이슈를 받은 사람이 어느 클라인지 알아야 고칠 수 있다.
    """
    from qa.scenarios.t2_multi_client import _judge_mirror

    def judge(labelled):
        ctx = _RecCtx()
        _judge_mirror(ctx, labelled, "MARK", oracle="multi/broadcast", key="k",
                      title="t", expected="e")
        return ctx.findings

    tabbar = " 1:zsh  2:MARK "
    assert judge([("클라 0", tabbar), ("클라 1", tabbar), ("클라 2", tabbar)]) == [], \
        "전원이 받았는데 결함을 냈다 — 위양성(원칙 ⓓ)"

    # 뮤테이션: 첫 클라에만 보냈다.
    one_way = judge([("클라 0", tabbar), ("클라 1", " 1:zsh "), ("클라 2", " 1:zsh ")])
    assert len(one_way) == 1, "브로드캐스트가 한 쪽에만 갔는데 안 물었다 — 공허한 오라클"
    oracle, severity, _key, actual = one_way[0]
    assert (oracle, severity) == ("multi/broadcast", "S1"), one_way[0]
    assert "클라 1" in actual and "클라 2" in actual, \
        f"못 받은 클라를 이름 대지 않았다: {actual}"
    assert "클라 0" in actual, f"받은 클라가 누구인지도 말해야 한다: {actual}"

    # 전원이 못 받은 것도 결함이다(그리고 그 사실을 그렇게 말한다).
    none = judge([("클라 0", " 1:zsh "), ("클라 1", " 1:zsh ")])
    assert len(none) == 1 and "전원 못 받음" in none[0][3], none


def test_multi_client_screen_judgement_bites_per_client():
    """⛔ **첫 클라에서 멈추지 않는다.** 「어느 하나가 깨졌다」로 접으면 지문이 하나라,
    두 번째 클라만 깨진 날 이슈가 엉뚱한 것을 가리킨다.

    ⚠ 판정 재료는 **탭바**다(테두리가 아니다). 실측(2026-08-09 뮤테이션): 서버 프레임을
    하나도 못 받은 클라도 자기 껍데기(테두리)는 그린다 — 「떴다」를 「붙었다」로 읽으면
    이 판정이 통째로 장식이 된다.
    """
    from qa.scenarios.t2_multi_client import _judge_screens

    good = "┌──┐\n│ 1:zsh  2:zsh │\n└──┘"
    shell_only = "┌────────┐\n│        │\n└────────┘"      # 테두리는 있고 탭바가 없다

    def judge(snapshot):
        ctx = _RecCtx()
        _judge_screens(ctx, snapshot, key_prefix="attach")
        return ctx.findings

    assert judge([("클라 0", True, good), ("클라 1", True, good)]) == [], "위양성"

    dead = judge([("클라 0", True, good), ("클라 1", False, good)])
    assert [(o, s) for o, s, _k, _a in dead] == [("client/alive", "S1")], dead
    assert dead[0][2].endswith("-1-died"), f"어느 클라인지가 지문에 없다: {dead[0][2]}"

    crashed = judge([("클라 0", True, "Traceback (most recent call last)\n  …"),
                     ("클라 1", True, good)])
    assert [(o, s) for o, s, _k, _a in crashed] == [("client/no_traceback", "S1")], crashed

    blank = judge([("클라 0", True, good), ("클라 1", True, shell_only)])
    assert [(o, s) for o, s, _k, _a in blank] == [("client/renders_tree", "S2")], blank
    assert "테두리는 있다" in blank[0][3], blank[0][3]

    # 여럿이 동시에 깨지면 **각각** 신고한다(클라마다 지문이 다르다).
    both = judge([("클라 0", False, good), ("클라 1", False, good)])
    assert len(both) == 2 and both[0][2] != both[1][2], both


def test_multi_client_reports_every_step_as_unverified_when_it_cannot_run():
    """⛔ 못 재는 상자(Windows — `ptyshot` 은 POSIX 전용)에서 **조용히 빠지지 않는다**.

    그 구멍은 `pytmux/pytmux-152` 가 이미 이름 대서 적어 둔 것이다. 조용히 빠지면 그
    상자에서 T2 는 「돌았고 결함 0」과 구별되지 않는다(원칙 ⓑ · rc 3 의 존재 이유).
    """
    from qa.scenarios import t2_multi_client as t2
    from qa.session import NotSupported

    class _Refuses:
        def clients(self, n=2):
            raise NotSupported("ptyshot 은 POSIX 전용(stdlib pty)")

    ctx = _RecCtx(session=_Refuses())
    t2.run(ctx)
    assert [name for name, _r in ctx.skips] == list(t2.STEPS), ctx.skips
    assert all(r.strip() for _n, r in ctx.skips), "사유 없는 SKIP 은 통과와 구분되지 않는다"
    assert ctx.findings == [], "못 잰 것을 결함으로 냈다 — 고칠 사람이 없는 이슈가 선다"


def test_multi_client_scenario_actually_asks_for_more_than_one_client():
    """⛔ 클라가 하나면 이 티어는 T0 의 사본이다. 세는 자리를 못으로 박는다."""
    from qa.scenarios import t2_multi_client as t2

    assert t2.CLIENTS >= 2, f"다중 클라가 {t2.CLIENTS} 개다"
    # 단계별 마커가 겹치면 앞 단계의 흔적이 뒤 단계를 통과시킨다(누적 버퍼).
    marks = [t2.MARK_BROADCAST, t2.MARK_SURVIVOR, t2.TYPED[1], t2.TYPED_LAST[1]]
    assert len(set(marks)) == len(marks), f"단계 마커가 겹친다: {marks}"
    for typed, marker in (t2.TYPED, t2.TYPED_LAST):
        assert marker not in typed, \
            f"치는 글자가 마커를 품는다 — tty 에코만으로 통과한다: {typed!r}"


def test_session_refuses_a_single_client_roster():
    """`Session.clients(1)` 은 다중 클라가 아니다 — 조용히 받아들이면 T2 가 T0 이 된다."""
    from qa.session import Session

    try:
        Session(_slot()).clients(1)
    except ValueError:
        return
    raise AssertionError("클라 1 개짜리 「다중 클라」를 받아들였다")


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


def test_findings_json_carries_the_unverified_too():
    """☠ **미검증이 트래커까지 건너가야 rc 3 과 rc 0 이 저쪽에서도 갈린다**(pytmux-148).

    종전에는 `skipped` 가 계약에 없어서 미검증이 `REPORT.md` 에만 남았다 — 그 파일은
    `.p4ignore` 라 **돌린 머신에만** 있고, 트래커에 도착하는 것은 결함 0건뿐이었다.
    그러면 「돌았는데 절반을 건너뛰었다」가 「초록」과 같은 모양이 된다(원칙 ⓑ · §4).
    ⛔ 미검증은 이슈가 되지 않는다 — 지문이 없다. 담기는 자리는 트래커의 **런 한 행**이다.
    """
    run = Run(runId="qa-20260809-000000", build="p4-1", head=1, dirty=False,
              stamped=True, seed=7, scenarios=["T0-core-loop"])
    sk = Skipped("T0-core-loop", "client-render", "이 상자에는 실 PTY 가 없다")
    with tempfile.TemporaryDirectory() as d:
        doc = json.load(open(write_findings(d, run, [], [sk]), encoding="utf-8"))
    assert doc["skipped"] == [{"scenario": "T0-core-loop", "step": "client-render",
                               "reason": "이 상자에는 실 PTY 가 없다"}], doc

    # 건너뜀이 없으면 빈 배열이다 — 키 자체가 사라지면 저쪽이 「옛 러너」로 읽는다.
    with tempfile.TemporaryDirectory() as d:
        doc = json.load(open(write_findings(d, run, []), encoding="utf-8"))
    assert doc["skipped"] == []


def test_the_runner_actually_hands_the_skips_to_write_findings():
    """⛔ 계약을 지켜도 **부르는 쪽이 안 넘기면** 아무 소용이 없다.

    `write_findings` 의 `skipped` 기본값은 옛 호출자를 위한 것이지 생략해도 된다는 뜻이
    아니다. 여기서 소스를 직접 재는 이유가 그것이다 — 실제 런을 돌리지 않고도 「굽는
    자리가 미검증을 넘기는가」를 못으로 박을 수 있다.
    """
    with open(os.path.join(ROOT, "qa", "run.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "write_findings(run_dir, run, findings, skipped)" in src, \
        "qa/run.py 가 미검증을 안 넘긴다 — findings.json 의 skipped 가 늘 빈 배열이 된다"


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


# ── ⑸ 커버리지 원장 (pytmux/pytmux-145) ───────────────────────────────────────

def _ledger(drop=(), extra=(), replies=None):
    """제어 라인 인벤토리를 **전부 지난 척** 한 원장. `drop` 이 뮤테이션이다."""
    led = Ledger()
    for name in inventory.control_spellings():
        if name in drop:
            continue
        led.record("control", name, (replies or {}).get(name, "ok"))
    for name in extra:
        led.record("control", name)
    return led


def _rows_by_key(rows):
    return {r.surface.key: r for r in rows}


def test_inventory_reads_every_surface_from_the_repo_text():
    """⛔ 인벤토리는 **손으로 적지 않는다** — 손목록은 새 명령이 생긴 날 조용히 낡는다."""
    seen = {}
    for surface in inventory.SURFACES:
        names = surface.extract()
        assert names and all(isinstance(n, str) and n for n in names), surface.key
        assert len(set(names)) == len(names), f"{surface.key} 에 중복이 있다"
        seen[surface.key] = set(names)
    # 표면마다 「이건 반드시 있다」를 하나씩 못 박는다 — 추출기가 조용히 좁아지면
    # 커버리지가 저절로 올라가 보이기 때문이다(0건보다 잡기 어려운 부류).
    assert {"split-window", "kill-server", "coalesce"} <= seen["control"]
    assert "kill_pane" in seen["cmd-table"]
    assert "split-window" in seen["client-commands"]
    assert "kill-pane" in seen["palette"]


def test_inventory_refuses_to_return_an_empty_list(monkeypatch=None):
    """⛔ **0건은 「전부 커버」와 같은 모양이다** — 파서가 죽은 날 원장이 100% 로 뜬다.

    뮤테이션: 소스를 읽는 자리를 「그 표가 없는 파일」로 바꿔치기한다.
    """
    saved = inventory._read
    inventory._read = lambda rel: "def handle_control(self, line):\n    return 'ok'\n"
    try:
        for fn in (inventory.control_spellings, inventory.cmd_table_actions,
                   inventory.client_commands, inventory.palette_names):
            try:
                fn()
            except inventory.InventoryBroken:
                continue
            raise AssertionError(f"{fn.__name__} 이 빈 인벤토리를 조용히 돌려줬다")
    finally:
        inventory._read = saved


def test_the_ledger_bites_when_a_command_is_never_run():
    """★ **이 이슈의 받아들임 기준**(pytmux-145): 명령 하나를 안 지나면 원장이 그것을
    **미커버로 신고**해야 한다. 안 물면 원장은 100% 를 파는 장식이다."""
    rows, findings, _ = _ledger().audit({LEDGER_SCENARIO})
    control = _rows_by_key(rows)["control"]
    assert control.uncovered == (), f"전부 지났는데 미커버가 남았다: {control.uncovered}"
    assert [f for f in findings if f.oracle == "coverage/control"] == [], \
        "전부 지났는데 결함을 냈다 — 위양성"

    rows, findings, _ = _ledger(drop=("kill-server",)).audit({LEDGER_SCENARIO})
    control = _rows_by_key(rows)["control"]
    assert control.uncovered == ("kill-server",), control.uncovered
    hit = [f for f in findings if f.oracle == "coverage/control"]
    assert len(hit) == 1, "명령을 안 지났는데 원장이 안 물었다 — 공허한 원장"
    assert "kill-server" in hit[0].actual, hit[0].actual
    assert hit[0].severity == "S3"


def test_a_refused_command_does_not_count_as_covered():
    """⛔ 「보냈다」와 「먹었다」는 다른 사건이다 — `unknown:` 은 그 철자가 서버에서
    사라졌다는 뜻이고, 그걸 지남으로 세면 원장이 결함을 덮는다."""
    led = _ledger(replies={"kill-server": "unknown: kill-server",
                           "new-window": "no session"})
    rows, findings, _ = led.audit({LEDGER_SCENARIO})
    assert set(_rows_by_key(rows)["control"].uncovered) == {"kill-server", "new-window"}
    assert any(f.oracle == "coverage/control" for f in findings)


def test_the_ledger_flags_a_command_it_has_never_heard_of():
    """지난 이름이 인벤토리 밖이면 **추출기가 좁아졌거나 시나리오가 낡은 것**이다.
    둘 다 원장을 조용히 거짓말하게 만든다."""
    _, findings, _ = _ledger(extra=("no-such-command",)).audit({LEDGER_SCENARIO})
    stray = [f for f in findings if f.oracle == "coverage/stray"]
    assert len(stray) == 1 and "no-such-command" in stray[0].actual, findings


def test_unreachable_surfaces_are_unverified_not_defects():
    """⛔ 아무도 못 지나는 표면을 **결함**으로 내면 고칠 사람이 없는 이슈가 서고, 그런
    이슈가 QA 를 끈다(원칙 ⓓ). 그렇다고 통과도 아니다 — **미검증**이 그 자리다(원칙 ⓑ)."""
    rows, findings, skips = _ledger().audit({LEDGER_SCENARIO})
    unreachable = {s.key for s in inventory.SURFACES if s.reached_by is None}
    assert unreachable, "지날 수 없는 표면이 하나도 없다면 이 시험은 뜻이 없다"
    for key in unreachable:
        assert not _rows_by_key(rows)[key].measured
        assert any(s.step == f"coverage/{key}" for s in skips), key
        assert not [f for f in findings if f.oracle == f"coverage/{key}"], key
    for s in skips:
        assert s.reason.strip(), "사유 없는 미검증은 회계에서 통과와 구분되지 않는다"


def test_a_partial_run_does_not_report_the_others_as_uncovered():
    """`--scenario T0-core-loop` 만 돌린 런이 「T1 이 안 지났다」를 결함으로 내면 안 된다 —
    그건 제품의 결함이 아니라 **그 런의 범위**다."""
    _, findings, skips = _ledger(drop=("kill-server",)).audit({"T0-core-loop"})
    assert [f for f in findings if f.oracle == "coverage/control"] == []
    assert [s for s in skips if s.step == "coverage/control"] == []


def test_ledger_fingerprint_does_not_move_with_the_numbers():
    """표면 하나 = 이슈 하나. 커버리지 숫자가 바뀔 때마다 새 이슈가 태어나면
    「자동 QA 는 첫 주에 익사한다」(원칙 ⓔ)."""
    one = _ledger(drop=("kill-server",)).audit({LEDGER_SCENARIO})[1]
    two = _ledger(drop=("kill-server", "new-window")).audit({LEDGER_SCENARIO})[1]
    a = next(f for f in one if f.oracle == "coverage/control")
    b = next(f for f in two if f.oracle == "coverage/control")
    assert a.fp == b.fp, f"미커버 건수가 지문을 흔들었다: {a.fp} != {b.fp}"
    assert a.title == b.title, "제목에 휘발성 숫자가 들어갔다"


def test_the_report_shows_the_ledger_as_numbers():
    """★ 받아들임 기준의 나머지 절반 — **리포트에 숫자로 뜬다**."""
    rows = _ledger().audit({LEDGER_SCENARIO})[0]
    run = Run(runId="r", build="p4-1", head=1, dirty=False, stamped=True, seed=1,
              scenarios=[LEDGER_SCENARIO])
    txt = render_report(run, [], [], [], rows)
    assert "## 커버리지 원장" in txt
    for row in rows:
        assert f"| {row.total} |" in txt, f"{row.surface.key} 의 인벤토리 수가 안 보인다"
    # 원장이 없는 런은 **그렇다고 말한다** — 빈 절은 「0건 미커버」로 읽힌다.
    assert "부분 런" in "\n".join(render_ledger([]))


def test_the_runner_actually_threads_the_ledger_through_the_session():
    """⛔ 계약을 지켜도 **배선이 빠지면** 원장은 늘 0건을 세고 100% 미커버를 신고한다.

    소스를 직접 재는 이유는 `write_findings` 쪽 시험과 같다 — 실 데몬 없이도 「세는
    자리가 보내는 자리에 이어져 있는가」를 못으로 박을 수 있다.
    """
    with open(os.path.join(ROOT, "qa", "run.py"), encoding="utf-8") as fh:
        run_src = fh.read()
    assert "run_scenario(mod, slot, seed, ledger" in run_src, \
        "qa/run.py 가 시나리오에 원장을 안 넘긴다"
    assert "ledger.audit(ran)" in run_src, "qa/run.py 가 원장을 회계하지 않는다"
    # ★ 그림 증거의 자리도 같은 부류의 배선이다(T3) — 안 넘기면 시나리오가 슬롯에 남기고,
    #   슬롯은 런이 끝나면 지워지므로 결함이 가리키는 그림이 판정 직후 사라진다.
    assert "run_scenario(mod, slot, seed, ledger, run_dir)" in run_src, \
        "qa/run.py 가 시나리오에 런 산출물 자리를 안 넘긴다"
    with open(os.path.join(ROOT, "qa", "session.py"), encoding="utf-8") as fh:
        sess_src = fh.read()
    assert 'self.ledger.record("control", head, out)' in sess_src, \
        "Session.control 이 지난 명령을 원장에 안 적는다"


# ── ⑹ 실 GUI 창 (pytmux/pytmux-147) ───────────────────────────────────────────
#
# ⛔ **여기서도 실 창을 띄우지 않는다** — 이 파일의 계약은 「QA 층 자체」다(머리말). 실
#    창은 `qa/run.py --tier T3` 이 띄우고, 여기서는 **그 판정이 정말로 무는가**를 합성
#    프레임으로 잰다. 합성이라서 오히려 셀 수 있다: 배너를 몇 픽셀 그렸는지 우리가 안다.

def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    return a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)


def _encode_png(width, height, rows, filt=0, alpha=False):
    """스캔라인 필터를 **골라서** 굽는 최소 PNG 인코더(시험용).

    ⚠ 필터를 고를 수 있어야 하는 이유: `image` 크레이트는 adaptive 라 한 프레임 안에
    Sub·Up·Average·Paeth 가 섞여 나온다. 디코더가 그중 하나만 틀려도 그림이 조용히
    어긋나고, 그 위의 오라클은 자기가 무엇을 보는지 모른 채 판정한다.
    """
    import struct
    import zlib

    bpp = 4 if alpha else 3
    raw = bytearray()
    prev = bytes(width * bpp)
    for row in rows:
        line = bytes(row)
        assert len(line) == width * bpp
        raw.append(filt)
        if filt == 0:
            enc = line
        elif filt == 1:
            enc = bytes((line[i] - (line[i - bpp] if i >= bpp else 0)) & 0xFF
                        for i in range(len(line)))
        elif filt == 2:
            enc = bytes((line[i] - prev[i]) & 0xFF for i in range(len(line)))
        elif filt == 3:
            enc = bytes((line[i] - (((line[i - bpp] if i >= bpp else 0) + prev[i]) >> 1)) & 0xFF
                        for i in range(len(line)))
        elif filt == 4:
            enc = bytes((line[i] - _paeth(line[i - bpp] if i >= bpp else 0, prev[i],
                                          prev[i - bpp] if i >= bpp else 0)) & 0xFF
                        for i in range(len(line)))
        else:
            raise AssertionError(filt)
        raw += enc
        prev = line

    def chunk(kind, body):
        return (struct.pack(">I", len(body)) + kind + body
                + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6 if alpha else 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw))) + chunk(b"IEND", b""))


#: 합성 프레임 크기. T3 의 띠 상수(`TAB_BAND`·`NOTICE_BAND_H`)가 다 들어가야 뜻이 선다.
_W, _H = 200, 200
_BG = (26, 27, 38)
_INK = (120, 140, 220)
_RED = (240, 90, 120)


def _canvas(fill=_BG):
    return [[fill[0], fill[1], fill[2]] * _W for _ in range(_H)]


def _paint(rows, y0, y1, x0, x1, color):
    for y in range(y0, y1):
        for x in range(x0, x1):
            rows[y][x * 3:x * 3 + 3] = list(color)
    return rows


def _session_frame(tabs=1):
    """세션 화면 흉내 — 탭바 띠에 탭 수만큼 「알약」을 그린다."""
    from qa.scenarios.t3_gui_window import TAB_BAND

    rows = _canvas()
    _paint(rows, TAB_BAND[0] + 6, TAB_BAND[1] - 6, 5, 45, _INK)
    if tabs >= 2:
        _paint(rows, TAB_BAND[0] + 6, TAB_BAND[1] - 6, 55, 95, _INK)
    _paint(rows, 100, 104, 5, 90, _INK)                    # 셸 프롬프트 자리
    return rows


def _banner_frame(tabs=1):
    """아래 알림 띠에 오류 배너를 띄운 화면. 실제로 이 상자에서 본 그림이다."""
    from qa.scenarios.t3_gui_window import NOTICE_BAND_H

    rows = _session_frame(tabs)
    return _paint(rows, _H - NOTICE_BAND_H + 10, _H - NOTICE_BAND_H + 22, 5, 65, _RED)


def _palette_frame():
    """팔레트 오버레이 — 화면 전체를 어둡게 덮고 그 위에 목록을 그린다.

    ⚠ **「통짜 한 색」으로 만들지 않는다** — 그렇게 두면 `gui/draws_something` 이 물어서
    이 픽스처가 재려던 것(키 배선)이 아니라 엉뚱한 오라클을 재게 된다. 실물도 통짜가
    아니다(실측: 팔레트가 열린 화면의 최빈색 비율 0.53).
    """
    rows = _canvas((10, 10, 16))
    _paint(rows, 60, 140, 30, 170, (40, 42, 60))
    return _paint(rows, 66, 72, 36, 150, _INK)


def test_frames_decoder_reads_every_scanline_filter_back_exactly():
    """⛔ 필터 하나를 틀리면 그림이 **조용히** 어긋난다 — 다섯을 전부 왕복시킨다."""
    from qa.frames import read_png

    rows = _session_frame(tabs=2)
    with tempfile.TemporaryDirectory() as d:
        for filt in (0, 1, 2, 3, 4):
            p = os.path.join(d, f"f{filt}.png")
            with open(p, "wb") as fh:
                fh.write(_encode_png(_W, _H, rows, filt=filt))
            f = read_png(p)
            assert (f.width, f.height) == (_W, _H)
            assert f.rgb == b"".join(bytes(r) for r in rows), f"필터 {filt} 왕복이 깨졌다"

        # 알파가 붙은 판(색타입 6)도 같은 픽셀로 읽혀야 한다 — 실제 하네스가 내는 모양이다.
        rgba = [bytes(b for i in range(_W)
                      for b in (r[i * 3], r[i * 3 + 1], r[i * 3 + 2], 255)) for r in rows]
        p = os.path.join(d, "rgba.png")
        with open(p, "wb") as fh:
            fh.write(_encode_png(_W, _H, rgba, filt=4, alpha=True))
        assert read_png(p).rgb == b"".join(bytes(r) for r in rows)


def test_frames_decoder_refuses_shapes_it_cannot_read():
    """⛔ 못 읽은 것을 **빈 프레임으로 접지 않는다** — 접으면 디코더가 죽은 날 제품이
    「아무것도 안 그렸다」로 신고된다(파싱 실패를 초록으로 위장하는 것의 거울상)."""
    from qa.frames import NotAFrame, read_png

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "nope.png")
        with open(p, "wb") as fh:
            fh.write(b"not a png at all")
        for path, blob in (
                (p, None),
                (os.path.join(d, "interlaced.png"),
                 bytearray(_encode_png(_W, _H, _session_frame()))),
        ):
            if blob is not None:
                blob[24] = 1                      # IHDR 의 인터레이스 칸을 켠다
                with open(path, "wb") as fh:
                    fh.write(bytes(blob))
            try:
                read_png(path)
            except NotAFrame:
                continue
            raise AssertionError(f"못 읽는 모양을 조용히 받아들였다: {path}")


def test_gui_frame_oracles_bite_and_stay_quiet_otherwise():
    """오라클 셋을 **합성 프레임**으로 잰다 — 배너 · 통짜 한 색 · 띠 안의 변화."""
    from qa.frames import read_png
    from qa.scenarios.t3_gui_window import (ALARM_PIXELS, BLANK_SHARE, NOTICE_BAND_H,
                                            TAB_BAND, TREE_DIFF_MIN)

    with tempfile.TemporaryDirectory() as d:
        def png(name, rows):
            p = os.path.join(d, name)
            with open(p, "wb") as fh:
                fh.write(_encode_png(_W, _H, rows, filt=4))
            return read_png(p)

        one, two = png("one.png", _session_frame(1)), png("two.png", _session_frame(2))
        bad = png("banner.png", _banner_frame(1))
        flat = png("flat.png", _canvas())

        band = (_H - NOTICE_BAND_H, _H)
        assert one.alarm(*band) == 0, "경보색이 없는데 물었다 — 위양성"
        assert bad.alarm(*band) >= ALARM_PIXELS, "배너를 그렸는데 오라클이 안 물었다"
        assert flat.dominant_share() > BLANK_SHARE, "통짜 한 색을 못 봤다"
        assert one.dominant_share() <= BLANK_SHARE, "그림이 있는데 「통짜」라고 했다"
        assert one.diff_ratio(one) == 0.0
        assert two.diff_ratio(one, *TAB_BAND) >= TREE_DIFF_MIN, \
            "탭이 늘었는데 탭바 띠가 안 바뀌었다고 읽었다"
        # ⛔ 띠 밖의 변화는 탭바 오라클을 통과시키면 안 된다(그러면 무엇을 재는지 모른다).
        elsewhere = png("elsewhere.png", _paint(_session_frame(1), 150, 160, 5, 90, _INK))
        assert elsewhere.diff_ratio(one, *TAB_BAND) == 0.0


class _GuiCtx(_RecCtx):
    """T3 를 돌리는 ctx — 슬롯·런 산출물 자리까지 흉내낸다."""

    def __init__(self, session, run_dir):
        super().__init__(session=session, scenario="T3-gui-window")
        self.run_dir = run_dir
        self.slot = self

    def residue(self):
        return []


class _FakeGui:
    """`gui_frame` 만 있는 가짜 세션. `plan` 이 프레임을 정하는 뮤테이션 손잡이다."""

    def __init__(self, plan, refuse=None):
        self.plan = plan
        self.refuse = refuse
        self.calls = []

    def gui_frame(self, path, keys=None, timeout=None):
        from qa.session import GuiShot, NotSupported

        if self.refuse:
            raise NotSupported(self.refuse)
        self.calls.append((os.path.basename(path), keys))
        rows = self.plan[len(self.calls) - 1]
        with open(path, "wb") as fh:
            fh.write(_encode_png(_W, _H, rows, filt=4))
        return GuiShot(binary="fake-gui", rc=0, stdout=f"frame-dump: {path} ({_W}x{_H})",
                       stderr="", path=path, said=(_W, _H))

    def control(self, line):
        return "ok"

    def tree(self):
        return 2, 3

    def stop(self):
        pass


def _run_t3(plan=None, refuse=None):
    from qa.scenarios import t3_gui_window as t3

    with tempfile.TemporaryDirectory() as d:
        ctx = _GuiCtx(_FakeGui(plan or [], refuse=refuse), d)
        t3.run(ctx)
        return ctx


def test_t3_is_quiet_when_the_window_behaves():
    """⛔ 위양성이 최대의 적이다(원칙 ⓓ) — 멀쩡한 네 장에 결함을 내면 이 티어는 꺼진다."""
    ctx = _run_t3([_session_frame(1), _session_frame(1), _session_frame(2), _palette_frame()])
    assert ctx.findings == [], ctx.findings
    # 마우스만 미검증이다 — 그것은 이 상자에 하네스가 없다는 사실이고 통과가 아니다.
    assert [n for n, _r in ctx.skips] == ["mouse"], ctx.skips


def test_t3_bites_when_the_window_shows_an_error_banner():
    """★ **이 이슈가 첫 런에서 실제로 잡은 것**(pytmux/pytmux-147) — 트리를 바꾼 뒤 붙은
    창이 「Disconnected …」 배너를 띄운 채로 그려졌다. 사람 눈 대신 색이 그것을 본다."""
    ctx = _run_t3([_session_frame(1), _session_frame(1), _banner_frame(2), _palette_frame()])
    hit = [(o, s) for o, s, _k, _a in ctx.findings]
    assert hit == [("gui/no_alarm_banner", "S1")], ctx.findings


def test_t3_bites_when_the_tree_never_reaches_the_window():
    """`client/CLAUDE.md` 가 말한 「GUI 쪽 배선 누락」 — 서버는 탭 둘이라는데 창은 그대로."""
    ctx = _run_t3([_session_frame(1), _session_frame(1), _session_frame(1), _palette_frame()])
    hit = [(o, s) for o, s, _k, _a in ctx.findings]
    assert hit == [("gui/tree_reaches_window", "S1")], ctx.findings


def test_t3_bites_when_keys_never_reach_the_window():
    """키를 넣었는데 화면이 안 움직이면 팔레트가 안 열린 것이다."""
    ctx = _run_t3([_session_frame(1), _session_frame(1), _session_frame(2), _session_frame(2)])
    hit = [(o, s) for o, s, _k, _a in ctx.findings]
    assert hit == [("gui/keys_reach_window", "S1")], ctx.findings


def test_t3_bites_when_two_attaches_disagree():
    """같은 세션에 두 번 붙었는데 그림이 다르면 한쪽이 세션을 잘못 그린 것이다."""
    ctx = _run_t3([_banner_frame(1), _session_frame(1), _session_frame(2), _palette_frame()])
    hit = [(o, s) for o, s, _k, _a in ctx.findings]
    # 첫 장은 배너로 먼저 걸린다(판정은 말해 주는 것이 많은 쪽부터) — 그러면 대조군이
    # 없으므로 안정성은 **미검증**이지 통과가 아니다.
    assert hit == [("gui/no_alarm_banner", "S1")], ctx.findings
    assert "부착 안정성은 미검증" in dict(ctx.skips)["reattach"], ctx.skips


def test_t3_reports_every_step_as_unverified_when_the_box_cannot_run_a_window():
    """★★ **이 이슈의 받아들임 기준 둘째**(pytmux-147): GUI 를 못 띄우는 상자에서는
    **사유 붙은 SKIP** 으로 나오고 리포트가 「미검증」이라고 말한다.

    ⛔ 조용히 빠지면 그 상자에서 T3 은 「돌았고 결함 0」과 구별되지 않는다(원칙 ⓑ · rc 3
       의 존재 이유). 그리고 **결함으로 내서도 안 된다** — 고칠 사람이 없는 이슈가 선다.
    """
    ctx = _run_t3(refuse="실 GUI 이진이 없다 — cargo build -p gui --bin pytmux-gui")
    assert ctx.findings == [], "못 잰 것을 결함으로 냈다"
    steps = [n for n, _r in ctx.skips]
    assert steps == ["first-attach", "reattach", "layout-mirror", "key-wiring", "mouse"], steps
    assert all(r.strip() for _n, r in ctx.skips), "사유 없는 SKIP 은 통과와 구분되지 않는다"


def test_gui_binary_refuses_to_measure_a_binary_that_is_not_there():
    """⛔ 「없으면 통과」로 접지 않는다 — 사유를 들고 `NotSupported` 로 나온다."""
    from qa.session import NotSupported, gui_binary

    saved = os.environ.get("PYTMUX_GUI")
    os.environ["PYTMUX_GUI"] = os.path.join(tempfile.gettempdir(), "no-such-pytmux-gui")
    try:
        gui_binary()
    except NotSupported as e:
        assert "cargo build" in str(e), f"고치는 법을 안 알려 준다: {e}"
    else:
        raise AssertionError("없는 이진으로 재겠다고 답했다")
    finally:
        if saved is None:
            os.environ.pop("PYTMUX_GUI", None)
        else:
            os.environ["PYTMUX_GUI"] = saved


def test_the_gui_harness_attaches_to_the_slot_and_never_starts_its_own_server():
    """⛔ 인자 없이 띄우면 서버를 못 찾았을 때 **직접 띄운다**(`Plan::FindOrStart`) — 그
    순간 격리 밖의 데몬이 생기고, 그것이 이 층에서 가장 비싼 사고의 씨앗이다(§안전 규율).
    """
    with open(os.path.join(ROOT, "qa", "session.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert '"--socket", endpoint' in src, "GUI 를 엔드포인트로 지목해 붙이지 않는다"
    assert "self.slot.spawned.append(p.pid)" in src, \
        "우리가 띄운 GUI 의 pid 를 안 쥔다 — 정리가 이름 매칭으로 번진다"
