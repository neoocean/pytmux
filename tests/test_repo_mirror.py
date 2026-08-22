"""저장소 위생 감시(`qa/repo.py`) — **미러 빚을 조용하지 않게 만드는 자리**(pytmux-153).

왜 시험하나: 이 층의 값은 「검사를 한다」가 아니라 **「사람이 안 돌려도 매일 돈다」** 이고,
그런 자리의 고장은 전부 조용하다. 두 가지를 못으로 박는다.

  ⑴ **낡은 기준선으로는 한 건도 만들지 않는다.** 이 층이 만들 수 있는 최악은 매일 남의
     게시를 내 빚이라고 이슈로 세우는 것이다(2026-08-10 실측으로 143 + 61건이 그 모양이었다).
  ⑵ **지문이 런마다 안 흔들린다.** 파일 목록·건수가 지문에 새면 파일 하나가 바뀔 때마다
     새 이슈가 태어나고, 그러면 자동 QA 는 첫 주에 익사한다(`qa/findings.py` §지문).

p4/git 은 안 부른다 — `_load_publish_check` 를 갈아끼워 **게이트가 준 데이터**만 먹인다.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import harness                                                     # noqa: E402

from qa import repo                                                # noqa: E402


class _Gate:
    """`scripts/publish_check.py` 대역. 부른 자리를 기록해 **안 부른 것**도 잰다."""

    def __init__(self, *, stale=(), unmeasured=(), drifts=(), wip=(),
                 dunmeasured=()):
        self._stale = list(stale)
        self._unmeasured = list(unmeasured)
        self._drifts = list(drifts)
        self._wip = list(wip)
        self._dunmeasured = list(dunmeasured)
        self.calls = []

    def run(self, cmd, cwd=None):
        return 0, "playground\n"

    def measure_freshness(self, remote=True):
        self.calls.append("freshness")
        return list(self._stale), list(self._unmeasured)

    def measure_drift(self):
        self.calls.append("drift")
        return list(self._drifts), list(self._wip), list(self._dunmeasured)


def _drift(kind, severity, head, why, items, fix="고치는 길"):
    return {"kind": kind, "severity": severity, "head": head, "why": why,
            "items": list(items), "count": len(items), "fix": fix}


def _audit(gate):
    """`repo.ROOT` 를 **git 클론이 있는 가짜 트리**로 갈아끼운 채로 잰다.

    ⛔ 실제 `repo.ROOT`(이 저장소의 진짜 체크아웃)로 재면 그 워크스페이스가 p4 전용인지
    (git 클론 없음)에 따라 결과가 갈린다 — pytmux-289. `test_p4_only_workspace_skips_
    instead_of_failing` 가 그 반대쪽(클론 없음)을 이미 따로 재므로, 여기서는 「클론 있음」
    가지를 고정해 나머지 시험이 실제 워크스페이스 상태와 무관하게 돈다.
    """
    with tempfile.TemporaryDirectory() as git_root:
        os.mkdir(os.path.join(git_root, ".git"))
        with harness.patched(repo, _load_publish_check=lambda: gate, ROOT=git_root):
            return repo.audit()


_STALE = [{"what": "git 기준선", "detail": "로컬 HEAD 가 origin/main 보다 30커밋 뒤",
           "fix": "git fetch origin && git reset --mixed origin/main"}]


async def test_stale_baseline_makes_one_finding_and_measures_no_drift():
    """⛔ 낡았으면 **드리프트를 아예 안 잰다** — 그 목록은 남의 게시다."""
    gate = _Gate(stale=_STALE,
                 drifts=[_drift("git-unpushed-content", "S3", "git 미푸시", "…",
                                ["a.py", "b.py"])])
    findings, skipped, steps = _audit(gate)
    assert "drift" not in gate.calls, (
        "낡은 기준선인데 드리프트를 쟀다 — 남의 게시를 내 빚으로 세우게 된다")
    assert [f.key for f in findings] == ["baseline-stale"], [f.key for f in findings]
    assert "30커밋 뒤" in findings[0].actual, findings[0].actual
    assert "reset --mixed" in findings[0].actual, "고치는 길이 이슈에 안 실린다"
    assert skipped, "못 잰 것을 미검증으로 안 남기면 rc 3 과 rc 0 이 같아진다"
    assert steps and steps[0][0] == repo.NAME, steps


async def test_drift_becomes_findings_with_the_severity_split_of_issue_38():
    """존재 드리프트는 **미러를 깨진 상태로** 만든다(공개 GitHub 만 보는 사람은 빌드 불가)
    → S2. 내용·커밋은 늦게 갚아도 되는 빚 → S3. pytmux-38 이 가른 그대로."""
    gate = _Gate(drifts=[
        _drift("depot-only-files", "S2", "git 에 없는 파일", "p4 에만 제출됐다",
               ["client/crates/gui/src/titlebar.rs"]),
        _drift("git-unpushed-commits", "S3", "git 미푸시 커밋", "p4 만 게시된 상태",
               ["deadbee 수정"]),
    ])
    findings, skipped, _ = _audit(gate)
    got = {f.key: f.severity for f in findings}
    assert got == {"depot-only-files": "S2", "git-unpushed-commits": "S3"}, got
    assert not skipped, "잰 것이 전부인데 미검증이 생겼다"
    assert all("titlebar" in f.actual or "deadbee" in f.actual for f in findings)


async def test_fingerprint_does_not_move_when_the_file_list_moves():
    """⛔ 지문에 목록·건수가 새면 **매 런 새 이슈**가 태어난다(중복 병합이 무력화된다)."""
    a, _, _ = _audit(_Gate(drifts=[_drift("depot-only-files", "S2", "git 에 없는 파일",
                                          "…", ["one.rs"])]))
    b, _, _ = _audit(_Gate(drifts=[_drift("depot-only-files", "S2", "git 에 없는 파일",
                                          "…", ["one.rs", "two.rs", "three.rs"])]))
    assert a[0].fp == b[0].fp, "같은 빚인데 지문이 움직였다"
    assert a[0].title == b[0].title, (
        "제목에 건수·목록이 샜다 — 파일 하나가 바뀔 때마다 이슈 제목이 흔들린다\n"
        f"  {a[0].title!r}\n  {b[0].title!r}")
    assert a[0].count != b[0].count, "건수는 제목이 아니라 관측치로 실려야 한다"
    assert a[0].actual != b[0].actual, "무엇이 밀렸는지는 본문에 남아야 한다"


async def test_clean_mirror_is_silent():
    findings, skipped, steps = _audit(_Gate())
    assert not findings and not skipped, (findings, skipped)
    assert "OK" in steps[0][2], steps


async def test_unmeasured_is_not_green():
    """못 잰 것은 통과가 아니다(원칙 ⓑ) — 미검증으로 남아 런이 rc 3 이 된다."""
    findings, skipped, steps = _audit(
        _Gate(dunmeasured=[{"what": "존재 대조", "detail": "p4 files 조회 실패"}]))
    assert not findings, findings
    assert len(skipped) == 1 and "p4 files" in skipped[0].reason, skipped
    assert "초록 아님" in steps[0][2], steps


async def test_p4_only_workspace_skips_instead_of_failing():
    """git 클론이 없는 워크스페이스에서는 **잴 것이 아예 없다**(pytmux-38 의 첫 얼굴).
    거기서 결함을 내면 그 상자의 야간 런이 매일 붉고, 상주하는 붉음은 곧 안 보인다.

    ⛔ 여기서는 `_audit()` 을 안 쓴다 — 그것은 이제 「클론 있음」쪽을 고정하므로 이 시험이
    겨눈 「클론 없음」이 가려진다.
    """
    with harness.patched(repo, _load_publish_check=lambda: _Gate(),
                          ROOT=os.path.join(HERE, "no-such-tree")):
        findings, skipped, steps = repo.audit()
    assert not findings, findings
    assert skipped and "git 클론이 아니다" in skipped[0].reason, skipped


async def test_gate_crash_is_reported_not_swallowed():
    class _Boom(_Gate):
        def measure_freshness(self, remote=True):
            raise RuntimeError("p4 가 없다")

    findings, _, steps = _audit(_Boom())
    assert [f.key for f in findings] == ["audit-crashed"], findings
    assert "p4 가 없다" in findings[0].actual, findings[0].actual


async def test_the_nightly_run_actually_calls_the_audit():
    """**호출부를 겨눈 오라클.** 위 시험들은 `repo.audit()` 을 직접 부르므로, `qa/run.py`
    에서 그 **호출 한 줄을 지워도 전부 통과한다** — 이 저장소가 반복해 물린 '공허 통과'
    (`tests/test_qa_layer.py` 의 원장 배선 시험과 같은 부류다). 야간 런이 이 층을 안 부르면
    미러 빚은 다시 조용해진다."""
    with open(os.path.join(ROOT, "qa", "run.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "repo.audit()" in src, "qa/run.py 가 저장소 위생을 아예 안 부른다"
    assert "findings += rfindings" in src, "잰 결함을 findings.json 으로 안 보낸다"
    assert "skipped += rskips" in src, "못 잰 것을 미검증으로 안 보낸다"
