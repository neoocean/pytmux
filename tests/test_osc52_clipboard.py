"""pytmux-420 ① — 패널 안 앱의 OSC 52 를 클라의 OS 클립보드까지 나른다.

배경: claude 의 `/tui fullscreen` 이 광고하는 셋 중 «auto-copy on select» 는 ssh 아래에서
**OSC 52 한 길**로만 나간다(로컬이면 pbcopy 같은 네이티브 도구를 직접 부른다). pytmux 는
타이틀 밖 OSC 를 플러그인 훅에 넘길 뿐이고 그것을 받는 `blocks` 는 133/7/633 만 보므로
**52 가 어디에도 안 닿았다** — 사용자에겐 「복사가 그냥 안 된다」로 보인다.

되돌리면 실패해야 하는 오라클:
  · 코어가 52 를 안 세워 두면          → test_pane_collects_a_write 실패
  · 두 번째 걷기가 같은 값을 또 주면    → test_taking_it_twice_gives_nothing 실패
  · 읽기 요청(`?`)에 답하면            → test_a_read_request_is_never_answered 실패
  · base64 상한을 안 올리면            → test_a_long_selection_survives 실패
  · 잘린 본문을 그대로 흘리면          → test_a_truncated_write_is_dropped 실패
  · 서버가 광고 안 한 클라에 보내면     → test_only_advertised_clients_get_it 실패
  · **flush 에서 부르는 줄을 지우면**   → test_flush_calls_the_appender 실패
    (값 만드는 함수만 재는 시험은 «호출 제거» 뮤테이션에 공허 통과한다)
  · 클라가 base64 를 안 풀면           → test_client_decodes_and_copies 실패
  · `set-clipboard off` 를 안 보면      → test_off_means_off 실패
"""
import base64

import harness  # noqa: F401  (스위트 공통 부트스트랩)

from pytmuxlib import protocol
from pytmuxlib.model import Pane
from pytmuxlib.vtparse import VTTokenizer


# ---- 코어: 패널이 OSC 52 를 세워 둔다 ----

def _pane(cols=80, rows=24):
    """PTY 없이 화면만 가진 패널. `feed` 로 바이트를 먹인다."""
    return Pane(pid=0, fd=-1, cols=cols, rows=rows)


def _b64(text):
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_pane_collects_a_write():
    p = _pane()
    p.feed(("\x1b]52;c;%s\x07" % _b64("복사된 글")).encode("utf-8"))
    assert p.take_clipboard() == _b64("복사된 글")


def test_taking_it_twice_gives_nothing():
    """서버는 클라 수만큼이 아니라 **한 번** 걷는다 — 두 번째는 비어야 한다."""
    p = _pane()
    p.feed(("\x1b]52;c;%s\x07" % _b64("x")).encode("utf-8"))
    assert p.take_clipboard() == _b64("x")
    assert p.take_clipboard() is None


def test_the_last_write_wins():
    """클립보드는 하나뿐이라 중간 값은 어차피 덮인다 — 마지막 것만 남는다."""
    p = _pane()
    p.feed(("\x1b]52;c;%s\x07" % _b64("첫째")).encode("utf-8"))
    p.feed(("\x1b]52;c;%s\x07" % _b64("둘째")).encode("utf-8"))
    assert base64.b64decode(p.take_clipboard()).decode() == "둘째"


def test_a_read_request_is_never_answered():
    """`ESC]52;c;?` 는 「클립보드를 돌려달라」다. 답하면 패널 안 아무 프로그램이나
    (`cat` 한 파일 포함) 사용자의 클립보드를 훔쳐 간다 — 우리는 **쓰기만** 받는다."""
    p = _pane()
    p.feed(b"\x1b]52;c;?\x07")
    assert p.take_clipboard() is None


def test_an_empty_write_is_not_a_write():
    p = _pane()
    p.feed(b"\x1b]52;c;\x07")
    assert p.take_clipboard() is None


def test_other_osc_codes_are_not_clipboard():
    """셸 통합(133/7)이 클립보드로 새면 안 된다."""
    p = _pane()
    p.feed(b"\x1b]133;A\x07\x1b]7;file:///tmp\x07")
    assert p.take_clipboard() is None


def test_the_plugin_hook_still_sees_52():
    """코어가 «모아만» 둔다 — 보던 플러그인이 계속 볼 수 있어야 한다."""
    seen = []
    p = _pane()
    p.osc_handler = lambda pane, code, param: seen.append((code, param))
    p.feed(("\x1b]52;c;%s\x07" % _b64("y")).encode("utf-8"))
    assert seen == [("52", "c;" + _b64("y"))]


# ---- 토크나이저: 상한 ----

def _feed_osc(code, payload, chunks=1):
    """`ESC]<code>;<payload>BEL` 을 `chunks` 조각으로 나눠 패널에 먹인다.

    조각내기가 핵심이다 — `52;` 판정이 «첫 조각 안에서만» 되면 실제 PTY 처럼 본문이
    갈려 올 때 상한이 조용히 4096 으로 되돌아간다.
    """
    p = _pane()
    seen = []
    p.osc_handler = lambda pane, c, prm: seen.append((c, prm))
    data = ("\x1b]%s;%s\x07" % (code, payload)).encode("utf-8")
    step = max(1, len(data) // chunks)
    for i in range(0, len(data), step):
        p.feed(data[i:i + step])
    return p, seen


def test_a_long_selection_survives():
    """종전 상한(_OSC_MAX=4096)은 «사람이 고른 글»을 자른다 — 16KB 를 온전히 넘긴다."""
    payload = _b64("가" * 4000)
    assert len(payload) > VTTokenizer._OSC_MAX
    p, _ = _feed_osc("52", "c;" + payload)
    assert p.take_clipboard() == payload


def test_a_long_selection_survives_when_split_across_feeds():
    payload = _b64("나" * 4000)
    p, _ = _feed_osc("52", "c;" + payload, chunks=37)
    assert p.take_clipboard() == payload


def test_a_truncated_write_is_dropped():
    """상한을 넘긴 52 는 **버린다.** 잘린 base64 는 반쪽 글이 아니라 쓰레기이고,
    길이가 우연히 4의 배수면 디코드까지 성공해 «다른 글»이 클립보드에 앉는다."""
    payload = _b64("다" * 200000)
    assert len(payload) > VTTokenizer._OSC52_MAX
    p, seen = _feed_osc("52", "c;" + payload)
    assert p.take_clipboard() is None
    assert seen == [], "잘린 52 는 플러그인 훅에도 안 간다"


def test_a_long_other_osc_is_still_capped():
    """52 의 큰 상한이 **다른 OSC 로 새면** N1(멀티 MB OSC 자원 폭주)이 돌아온다."""
    _, seen = _feed_osc("777", "A" * 100000)
    assert seen and len(seen[0][1]) <= VTTokenizer._OSC_MAX


# ---- 서버: 광고한 클라에게만, flush 가 실제로 부른다 ----

class _Client:
    def __init__(self, caps, remote_view=False):
        self.caps = caps
        self.remote_view = remote_view


class _Io:
    """`_append_clipboard_frames` 만 빌려 쓰는 최소 서버 — 상속으로 진짜 메서드를 쓴다."""


def _appender():
    from pytmuxlib.serverio import ServerIOMixin
    return ServerIOMixin._append_clipboard_frames


def test_only_advertised_clients_get_it():
    p = _pane()
    p.feed(("\x1b]52;c;%s\x07" % _b64("z")).encode("utf-8"))
    yes, no = _Client(("clipboard",)), _Client(("cwd",))
    frames = {yes: [], no: []}
    _appender()(_Io(), frames, [yes, no], p)
    assert len(frames[yes]) == 1 and frames[no] == []


def test_a_remote_view_client_still_gets_it():
    """원격 보기 클라가 보는 것은 상류 패널이고, 거기서 한 복사는 **그 사람 기계의**
    클립보드에 앉는 것이 맞다(`cwd` 가 원격을 빼는 이유는 뜻이 어긋나서다)."""
    p = _pane()
    p.feed(("\x1b]52;c;%s\x07" % _b64("z")).encode("utf-8"))
    c = _Client(("clipboard",), remote_view=True)
    frames = {c: []}
    _appender()(_Io(), frames, [c], p)
    assert len(frames[c]) == 1


def test_nothing_pending_sends_nothing():
    p = _pane()
    c = _Client(("clipboard",))
    frames = {c: []}
    _appender()(_Io(), frames, [c], p)
    assert frames[c] == []


def test_the_value_is_dropped_when_nobody_can_use_it():
    """아무도 안 광고했으면 걷어서 **버린다** — 나중에 붙은 클라에 옛 복사가 튀어나오면
    사용자가 안 한 복사가 뒤늦게 클립보드를 덮는다."""
    p = _pane()
    p.feed(("\x1b]52;c;%s\x07" % _b64("z")).encode("utf-8"))
    _appender()(_Io(), {}, [_Client(("cwd",))], p)
    assert p.take_clipboard() is None


def test_flush_calls_the_appender():
    """**호출 제거** 뮤테이션: flush 루프에서 이 줄을 지워도 위 시험은 전부 통과한다.
    그래서 «부르는 자리»를 소스에서 직접 센다(AST — 이름이 바뀌면 같이 운다)."""
    import ast
    import inspect

    from pytmuxlib import serverio
    tree = ast.parse(inspect.getsource(serverio))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_append_clipboard_frames"]
    assert len(calls) == 1, "flush 가 부르는 자리가 정확히 하나여야 한다"


def test_the_cap_is_advertised():
    """서버는 광고한 것만 보낸다 — 정본 클라가 광고를 빼면 프레임이 영영 안 온다."""
    assert "clipboard" in protocol.CLIENT_CAPS


# ---- 클라: base64 를 풀어 OS 클립보드에 넣는다 ----

class _App:
    """`_ClipboardMixin._apply_remote_clipboard` 만 빌려 쓰는 최소 앱.

    OS 클립보드로 나가는 마지막 한 걸음만 가로채 **무엇을 풀었나**를 본다."""

    def __init__(self):
        self.copied = []

    def _copy_to_os_clipboard(self, text):
        self.copied.append(text)


def _apply(app, data):
    from pytmuxlib.client import _ClipboardMixin
    return _ClipboardMixin._apply_remote_clipboard(app, data)


def test_client_decodes_and_copies():
    """base64 를 **제대로** 풀어야 한다 — 「불렸다」가 아니라 「무엇이 나갔나」를 본다."""
    app = _App()
    _apply(app, _b64("붙일 글 · 여러 줄\n둘째 줄"))
    assert app.copied == ["붙일 글 · 여러 줄\n둘째 줄"]


def test_client_drops_garbage():
    """앱이 보낸 쓰레기로 클라가 죽거나 클립보드가 깨지면 안 된다."""
    app = _App()
    _apply(app, "!!!not base64!!!")
    _apply(app, base64.b64encode(b"\xff\xfe\xfd").decode("ascii"))
    _apply(app, None)
    _apply(app, "")
    assert app.copied == []


def test_the_setting_is_on_every_surface():
    """사용자가 끌 수 있으려면 이름이 **세 표에 다** 서 있어야 한다 — 자동완성 이름 ·
    값 후보 · 설정 화면. 한 곳만 빠지면 「있는데 안 보이는 옵션」이 된다."""
    from pytmuxlib import clientutil
    assert "set-clipboard" in clientutil._SET_OPTION_NAMES
    assert clientutil.SET_OPTION_CHOICES["set-clipboard"] == ("on", "off")
    assert any(s["key"] == "set-clipboard" for s in clientutil.SETTINGS)


def test_off_means_off(tmp=None):
    """설정 파일의 `set set-clipboard off` 가 실제로 꺼야 한다(기본은 켜짐)."""
    import os
    import tempfile

    from pytmuxlib import keymap
    d = tempfile.mkdtemp(prefix="pytmux-osc52-")
    path = os.path.join(d, "config")
    with open(path, "w") as f:
        f.write("set set-clipboard off\n")
    assert keymap.load_config(path).get("set_clipboard") is False
    with open(path, "w") as f:
        f.write("set set-clipboard on\n")
    assert keymap.load_config(path).get("set_clipboard") is True
    # 안 적으면 키 자체가 없다 — 기본값은 클라가 `config.get(..., True)` 로 준다.
    with open(path, "w") as f:
        f.write("")
    assert "set_clipboard" not in keymap.load_config(path)
