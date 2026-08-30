"""pytmux-415 ⑶ — claude 가 **스스로 끈** fullscreen 을 pytmux 가 말한다.

배경: 스크롤한 프롬프트의 스티키 바(`› <프롬프트>`)와 「클릭해서 점프」는 Claude Code
의 **fullscreen 렌더러의 것**이다. claude 는 부팅 canary 가 두 번 트립하면 그 머신의
fullscreen 을 끄고 `~/.claude.json` 에 `fullscreenAutoDisabled` 를 적는데, 그때
stderr 알림은 **딱 한 번** 뜨고 사라진다 — 사용자는 「기능이 없어졌다」로만 겪는다
(제보 2026-08-28). ⛔ 고치지 않는다(남의 설정을 안 쓴다) — **세고 말한다.**

되돌리면 실패해야 하는 오라클:
  · 파서가 최상위 칸을 안 보면 → test_parses_a_standing_record 실패
  · 빠른 거르기가 **오탐**하면(본문 어딘가의 같은 단어) → test_mentions_are_not_records 실패
  · `CLAUDE_CONFIG_DIR` 규칙을 transcript 쪽과 같게 적으면 → test_config_path_* 실패
  · 알림 호출을 지우면 → test_notice_is_emitted 실패
  · **세션 경계에서 부르는 줄을 지우면** → test_new_session_boundary_asks 실패
    (값 만드는 함수만 재는 시험은 «호출 제거» 뮤테이션에 공허 통과한다)
  · 래치를 빼서 새 세션마다 다시 말하면 → test_says_it_once 실패
  · 일시적으로 못 읽은 것을 «걷혔다»로 읽어 래치를 풀면
    → test_a_blink_does_not_repeat_the_same_trip 실패
"""
import importlib
import os

import harness

fs = importlib.import_module("pytmuxlib.plugins.claude-code.fullscreen")
sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")

_STANDING = ('{"numStartups": 3, "fullscreenAutoDisabled": '
             '{"version": "2.1.250", "at": 1787896926320, "strikes": 2}}')


# ---- 파서(순수) ----

def test_parses_a_standing_record():
    rec = fs.parse_auto_disabled(_STANDING)
    assert rec == {"version": "2.1.250", "at_ms": 1787896926320, "strikes": 2}


def test_a_healthy_config_has_nothing_to_say():
    assert fs.parse_auto_disabled('{"numStartups": 3}') is None


def test_mentions_are_not_records():
    """빠른 거르기(substring)는 **통과만** 시킨다 — 판정은 최상위 칸이 한다.

    이 파일은 프로젝트 이력(사용자가 친 프롬프트!)까지 담으므로 그 단어가 본문
    어딘가에 뜨는 일은 실제로 있다. 거르기를 판정으로 쓰면 그때마다 거짓 경보다."""
    assert fs.parse_auto_disabled(
        '{"history": ["fullscreenAutoDisabled 가 뭐야?"]}') is None


def test_a_record_without_a_version_is_never_in_effect():
    """claude 는 `F.version !== w.version` 이면 무효로 본다 — 버전 없는 기록은
    **어느 버전에도 안 맞아** 영원히 효력이 없다. 그것을 「꺼져 있다」로 말하면 거짓."""
    assert fs.parse_auto_disabled(
        '{"fullscreenAutoDisabled": {"at": 1, "strikes": 2}}') is None


def test_broken_shapes_do_not_raise():
    """알림 하나 때문에 세션 경계 회계가 죽으면 안 된다 — 전부 None 으로 흘린다."""
    for bad in ('', '{', 'null', '[]', '"fullscreenAutoDisabled"',
                '{"fullscreenAutoDisabled": 7}',
                '{"fullscreenAutoDisabled": null}',
                '{"fullscreenAutoDisabled": {"version": ""}}'):
        assert fs.parse_auto_disabled(bad) is None, bad


def test_non_int_fields_fall_back_to_zero():
    rec = fs.parse_auto_disabled(
        '{"fullscreenAutoDisabled": {"version": "9", "at": "x", "strikes": true}}')
    assert rec == {"version": "9", "at_ms": 0, "strikes": 0}


# ---- 파일 자리 ----

def test_config_path_defaults_to_home():
    saved = os.environ.pop("CLAUDE_CONFIG_DIR", None)
    try:
        assert fs.config_path() == os.path.join(
            os.path.expanduser("~"), ".claude.json")
    finally:
        if saved is not None:
            os.environ["CLAUDE_CONFIG_DIR"] = saved


def test_config_path_honours_claude_config_dir():
    """⚠ transcript.projects_dir() 와 **규칙이 다르다**: 전역 설정은
    `$CLAUDE_CONFIG_DIR/.claude.json` 이지 `$CLAUDE_CONFIG_DIR/.claude/…` 가 아니다."""
    saved = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = os.path.join("/tmp", "cfgdir")
    try:
        assert fs.config_path() == os.path.join("/tmp", "cfgdir", ".claude.json")
    finally:
        if saved is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = saved


def test_a_missing_file_is_silence_not_an_error():
    assert fs.read_auto_disabled(os.path.join("/nonexistent-dir", "x.json")) is None


def test_reads_the_file_it_is_given(tmp=None):
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, ".claude.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write(_STANDING)
        assert fs.read_auto_disabled(p)["version"] == "2.1.250"


# ---- 알림(호출부) ----

class _Client:
    session = object()


class _Srv:
    _notice_fullscreen_off = sm.ServerClaudeMixin._notice_fullscreen_off
    # ⚠ 정본에서 이것은 `@staticmethod` 다. 클래스에서 꺼내면 **맨 함수**라, 여기서
    # 그냥 대입하면 인스턴스 메서드가 돼 self 가 첫 인자로 끼어든다 — 그러면 실물과
    # 다른 것을 재게 된다(실측으로 물렸다). 같은 모양으로 다시 감싼다.
    _fmt_fullscreen_at = staticmethod(sm.ServerClaudeMixin._fmt_fullscreen_at)

    def __init__(self):
        self.clients = []          # 전송 0 — 관측은 _notice_msg 스파이로 한다
        self._fullscreen_off_said = None
        self.sent = []

    def _notice_msg(self, key, ko, *, severity=None, **kw):
        msg = {"t": "notice", "key": key, "sev": severity, "kw": kw,
               "text": ko.format(**kw)}
        self.sent.append(msg)
        return msg


def _srv_seeing(*records):
    """`read_auto_disabled` 가 회차마다 이 값들을 차례로 돌려주게 한다."""
    seq = list(records)

    def read(path=None):
        return seq.pop(0) if seq else None
    return read


def test_notice_is_emitted():
    srv = _Srv()
    rec = {"version": "2.1.250", "at_ms": 1787896926320, "strikes": 2}
    with harness.patched(sm.fullscreen, read_auto_disabled=_srv_seeing(rec)):
        srv._notice_fullscreen_off()
    assert [m["key"] for m in srv.sent] == ["ccmsg.fullscreen_off"]
    kw = srv.sent[0]["kw"]
    assert kw["ver"] == "2.1.250" and kw["strikes"] == 2
    assert kw["when"]                      # 시각이 사람이 읽는 글로 실린다
    assert srv.sent[0]["sev"] == "warn"
    # 되살리는 길이 문구 안에 있어야 한다 — 그것이 이 알림의 존재 이유다.
    assert "/tui fullscreen" in srv.sent[0]["text"]


def test_a_healthy_machine_says_nothing():
    srv = _Srv()
    with harness.patched(sm.fullscreen, read_auto_disabled=_srv_seeing()):
        srv._notice_fullscreen_off()
    assert srv.sent == []


def test_says_it_once():
    """claude 를 여러 번 띄우는 것이 정상 작업이다 — 매번 말하면 그게 소음이다."""
    srv = _Srv()
    rec = {"version": "2.1.250", "at_ms": 111, "strikes": 2}
    with harness.patched(sm.fullscreen,
                         read_auto_disabled=_srv_seeing(rec, rec, rec)):
        for _ in range(3):
            srv._notice_fullscreen_off()
    assert len(srv.sent) == 1


def test_a_new_trip_speaks_again():
    srv = _Srv()
    a = {"version": "2.1.250", "at_ms": 111, "strikes": 2}
    b = {"version": "2.1.251", "at_ms": 222, "strikes": 2}
    with harness.patched(sm.fullscreen, read_auto_disabled=_srv_seeing(a, b)):
        srv._notice_fullscreen_off()
        srv._notice_fullscreen_off()
    assert [m["kw"]["ver"] for m in srv.sent] == ["2.1.250", "2.1.251"]


def test_a_blink_does_not_repeat_the_same_trip():
    """기록이 잠깐 안 읽혀도 **이미 말한 트립을 또 말하지 않는다.**

    `read_auto_disabled` 는 파일을 못 읽으면 None 을 준다 — claude 가 그 순간
    설정을 다시 쓰는 중이면 실제로 그렇다. 처음엔 「기록이 걷히면 래치를 푼다」로
    적었다가 뮤테이션 반증에서 그 줄이 **아무 오라클도 안 물리는 것**을 보고 다시
    쟀더니, 이득은 없고(진짜 새 트립은 새 `at` 이라 래치 없이도 말해진다) 이 반복만
    남는 교환이었다. 그래서 그 줄을 지웠고, 이 시험이 그 결정을 못박는다."""
    srv = _Srv()
    rec = {"version": "2.1.250", "at_ms": 111, "strikes": 2}
    with harness.patched(sm.fullscreen,
                         read_auto_disabled=_srv_seeing(rec, None, dict(rec))):
        srv._notice_fullscreen_off()
        srv._notice_fullscreen_off()      # 못 읽었다 — 조용
        srv._notice_fullscreen_off()      # 같은 트립이 도로 보인다 — 다시 말하지 않는다
    assert len(srv.sent) == 1


def test_a_read_that_explodes_is_swallowed():
    """설정 파일 읽기가 어떤 이유로든 던져도 세션 경계가 멎으면 안 된다."""
    srv = _Srv()

    def boom(path=None):
        raise RuntimeError("디스크가 사라졌다")
    with harness.patched(sm.fullscreen, read_auto_disabled=boom):
        srv._notice_fullscreen_off()      # 안 던져야 한다
    assert srv.sent == []


def test_at_zero_renders_as_empty_not_1970():
    assert _Srv._fmt_fullscreen_at(0) == ""


# ---- 호출부 오라클(«호출 제거» 뮤테이션 방어) ----

class _Frame:
    def __init__(self, new_cl, old_cl, old_hdr=True):
        self.new_cl, self.old_cl, self.old_hdr = new_cl, old_cl, old_hdr


class _Pane:
    id = 1
    _claude_session_id = 7
    _claude_account_manual = True      # 계정 재감지 가지를 안 타게(프로브 예약 무관)
    _rules_pending = False
    _rc_pending = False
    _perm_auto_pending = False


class _BoundarySrv:
    _scan_session_boundary = sm.ServerClaudeMixin._scan_session_boundary
    claude_rules = ""
    claude_auto_launch = False
    _rc_policy_blocked = False
    usage_refresh_sec = 0

    def __init__(self):
        self.asked = 0

    def _xc_session_looks_new(self, p):
        return False

    def _notice_fullscreen_off(self):
        self.asked += 1


def test_new_session_boundary_asks():
    """None→Claude 경계에서 **실제로** 묻는다.

    이 오라클이 없으면 `_notice_fullscreen_off` 를 아무도 안 불러도 위 시험들이
    전부 초록이다(공허 통과 — 실측 상습 실패 모드)."""
    srv, pane = _BoundarySrv(), _Pane()
    srv._scan_session_boundary(_Frame(new_cl="claude", old_cl=None), pane)
    assert srv.asked == 1


def test_a_continuing_session_does_not_ask():
    """이미 돌던 세션은 경계가 아니다 — 프레임마다 설정 파일을 읽으면 안 된다."""
    srv, pane = _BoundarySrv(), _Pane()
    srv._scan_session_boundary(_Frame(new_cl="claude", old_cl="claude"), pane)
    srv._scan_session_boundary(_Frame(new_cl=None, old_cl="claude"), pane)
    assert srv.asked == 0
