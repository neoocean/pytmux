"""pytmux-415 ⓒ — 「재는 자」가 실제로 재는가(`scripts/fullscreen_watch.py`).

이 이슈가 여섯 회차째 추론에 머문 이유가 한 줄로 적혀 있다: *"재는 자를 안 세웠다.
이 값들은 전부 사후에 읽은 것이다."* 사후에 읽으면 가장 중요한 증거가 이미 없다 —
`fullscreenBootPending` 은 **트립하는 순간 비워지는** pid 표라, 그때를 놓치면
「세 boot 가 각각 무엇이었나」를 영영 못 가른다.

되돌리면 실패해야 하는 오라클:
  · 세 칸만 뜨는 대신 파일을 통째로 싣게 하면 → test_only_the_canary_fields_are_kept
  · 트립을 「안 바뀐 것」으로 접으면 → test_a_trip_is_significant
  · `pending_who` 를 판정에 넣어 매 표본을 적게 하면 → test_a_quiet_sample_is_not_written
  · pid 귀속(부모 사슬)을 지우면 → test_pending_pids_get_their_parents
  · 죽은 pid 를 산 것으로 세면 → test_a_dead_pending_pid_is_marked_dead
"""
import importlib.util
import json
import os
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SPEC = importlib.util.spec_from_file_location(
    "fullscreen_watch", os.path.join(_HERE, "..", "scripts",
                                     "fullscreen_watch.py"))
fw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fw)


def _cfg(doc):
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    return p


_ROWS = [
    {"pid": 100, "ppid": 50, "started": "Wed Sep  3 07:46:09 2026",
     "cmd": "claude"},
    {"pid": 50, "ppid": 9, "started": "Wed Sep  3 07:46:01 2026",
     "cmd": "/bin/zsh"},
    {"pid": 9, "ppid": 1, "started": "Wed Sep  3 07:01:19 2026",
     "cmd": "python3 pytmux.py server"},
]


# ---- 읽기: 세 칸만 · 남의 글은 안 싣는다 ----

def test_only_the_canary_fields_are_kept():
    """⛔ 이 파일에는 **사용자가 친 프롬프트 이력**이 들어 있다(실측 수백 KB).
    로그에 통째로 실으면 그 로그를 남에게 못 준다."""
    p = _cfg({"fullscreenAutoDisabled": {"version": "2.1.259", "at": 1,
                                         "strikes": 3},
              "history": ["회사 비밀번호는 …"],
              "projects": {"/x": {"y": 1}}})
    try:
        got = fw.read_fields(p)
    finally:
        os.unlink(p)
    assert set(got) == set(fw.FIELDS), got
    assert "회사" not in json.dumps(got, ensure_ascii=False)


def test_an_unreadable_config_is_said_not_guessed():
    got = fw.read_fields(os.path.join(tempfile.gettempdir(), "no-such-x.json"))
    assert got == {"_unreadable": True}


def test_a_broken_config_does_not_raise():
    fd, p = tempfile.mkstemp(suffix=".json")
    os.write(fd, b"{not json")
    os.close(fd)
    try:
        assert fw.read_fields(p) == {"_unreadable": True}
    finally:
        os.unlink(p)


# ---- 무엇을 적나 ----

def test_a_trip_is_significant():
    prev = {"fullscreenAutoDisabled": None, "fullscreenBootPending": None,
            "fullscreenBootStrikes": None}
    cur = dict(prev, fullscreenAutoDisabled={"version": "2.1.259", "at": 2,
                                             "strikes": 3})
    assert fw.significant(prev, cur) is True


def test_a_strike_short_of_a_trip_is_significant_too():
    """임계(이진 상수 `yh=2`)에 못 미친 스트라이크가 **가장 값진 표본**이다 —
    트립 전에만 볼 수 있는 상태이고, 트립하면 이 칸도 비워진다."""
    prev = {"fullscreenAutoDisabled": None, "fullscreenBootPending": None,
            "fullscreenBootStrikes": None}
    cur = dict(prev, fullscreenBootStrikes={"count": 1, "version": "2.1.259"})
    assert fw.significant(prev, cur) is True


def test_a_quiet_sample_is_not_written():
    """⛔ 안 바뀌면 안 적는다. 이걸 놓치면 로그가 하루 4320줄이 되고 트립 줄이
    그 안에 묻힌다 — 기록기가 있으나 마나가 된다."""
    s = {"fullscreenAutoDisabled": None, "fullscreenBootPending": None,
         "fullscreenBootStrikes": None}
    assert fw.significant(s, dict(s)) is False


def test_process_churn_alone_does_not_count_as_a_change():
    """`pending_who` 는 판정 밖이다 — 프로세스가 뜨고 지는 것만으로 매 표본이
    달라져 이 함수가 하는 일이 없어진다."""
    s = {"fullscreenAutoDisabled": None, "fullscreenBootPending": None,
         "fullscreenBootStrikes": None, "pending_who": [{"pid": 1}]}
    assert fw.significant(s, dict(s, pending_who=[{"pid": 2}])) is False


def test_the_first_sample_is_always_written():
    assert fw.significant(None, {"fullscreenAutoDisabled": None}) is True


# ---- 귀속: 「그 boot 가 누구였나」 ----

def test_pending_pids_get_their_parents():
    """★ 이 기록기의 값이 여기 있다. 트립하면 이 표는 비워지므로, 살아 있는 동안
    부모 사슬을 적어 두지 않으면 「우리 그림자 프로브가 낳은 것인가, 사용자가
    패널에서 띄운 것인가」를 나중에 못 가른다."""
    who = fw.attribute({"100": {"startedAt": 5, "version": "2.1.259"}}, _ROWS)
    assert len(who) == 1
    it = who[0]
    assert it["pid"] == 100 and it["alive"] is True
    assert it["version"] == "2.1.259" and it["startedAt"] == 5
    chain = [c["cmd"] for c in it["parents"]]
    assert "/bin/zsh" in chain[0]
    assert "pytmux.py server" in chain[1], chain


def test_a_dead_pending_pid_is_marked_dead():
    """⛔ pid 는 재사용된다 — 「살았나」를 짐작하지 않고 `ps` 에 있나로 적는다.
    죽은 pending 이야말로 claude 가 스트라이크로 세는 그것이다(정본 `jc` 의 `oe`)."""
    who = fw.attribute({"777": {"startedAt": 5}}, _ROWS)
    assert who[0]["alive"] is False and "parents" not in who[0]


def test_junk_pending_keys_are_skipped_not_fatal():
    """상대는 남의 파일이다 — 모양이 아니면 그 항목만 버리고 계속한다."""
    who = fw.attribute({"x": {}, "100": {}}, _ROWS)
    assert [w["pid"] for w in who] == [100]
    assert fw.attribute(None, _ROWS) == []
    assert fw.attribute(["nope"], _ROWS) == []


def test_a_parent_cycle_does_not_hang():
    """부모 사슬은 남이 준 값으로 걷는다 — 고리가 있어도 멎지 않는다."""
    rows = [{"pid": 1, "ppid": 2, "started": "", "cmd": "a"},
            {"pid": 2, "ppid": 1, "started": "", "cmd": "b"}]
    who = fw.attribute({"1": {}}, rows)
    assert len(who[0]["parents"]) <= 6


# ---- 전체 한 장 ----

def test_a_sample_carries_the_installed_version():
    """판이 함께 있어야 「그 기록이 지금 도는 claude 의 것인가」를 나중에 가른다
    (pytmux-415 ⓑ 와 같은 규칙 — 판이 다른 기록은 claude 가 무효로 본다)."""
    p = _cfg({"fullscreenBootPending": {"100": {"startedAt": 1}}})
    try:
        s = fw.sample(p, rows=_ROWS, now=123.0)
    finally:
        os.unlink(p)
    assert s["ts"] == 123.0
    assert "installed" in s
    assert s["pending_who"][0]["pid"] == 100
