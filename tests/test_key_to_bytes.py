"""수식 키 → 패널 바이트(pytmux-608).

정본 `clientutil.key_to_bytes` 는 표(SPECIAL) 밖의 수식 조합을 **버렸고**(`b""`) Alt+글자의
ESC 접두를 떨궜다. 이제 xterm ctlseqs 의 PC 식 수식(N = 1 + shift + 2·alt + 4·ctrl)이다.
여기 시험은 **호스트 단말이 보낼 바이트**를 정본이 실제로 쓰는 Textual `XTermParser` 에 먹여
나온 키 이벤트로 잰다 — Textual 이 키 이름을 어떻게 접는지까지 함께 잡힌다. GUI 와의 대조는
`client/crates/proto/tests/key_bytes_conformance.rs`(격자 픽스처 `key_grid.json`).
"""
import harness  # noqa: F401 (경로 설정)
from textual import events
from textual.app import App
from textual.widgets import Input
from textual._xterm_parser import XTermParser

from pytmuxlib.clientutil import install_alt_word_keys, key_to_bytes


def _feed(seq):
    p = XTermParser(debug=False)
    toks = list(p.feed(seq)) + list(p.feed(""))
    return [t for t in toks if isinstance(t, events.Key)]


def _bytes(seq):
    return b"".join(key_to_bytes(k) for k in _feed(seq))


async def test_modified_cursor_and_function_keys_use_the_xterm_parameter():
    install_alt_word_keys()
    for seq, want in [
        ("\x1b[1;5A", b"\x1b[1;5A"),      # Ctrl+↑ — 종전 b""
        ("\x1b[1;3D", b"\x1b[1;3D"),      # Alt+←
        ("\x1b[1;6B", b"\x1b[1;6B"),      # Ctrl+Shift+↓
        ("\x1b[1;3P", b"\x1b[1;3P"),      # Alt+F1
        ("\x1b[15;2~", b"\x1b[15;2~"),    # Shift+F5
        ("\x1b[5;5~", b"\x1b[5;5~"),      # Ctrl+PgUp
        ("\x1b[3;5~", b"\x1b[3;5~"),      # Ctrl+Delete
        ("\x1b[2;2~", b"\x1b[2;2~"),      # Shift+Insert
        ("\x1b[1;2H", b"\x1b[1;2H"),      # 종전 표 줄은 그대로
        ("\x1bOA", b"\x1b[A"),            # SS3 화살표는 CSI 로 다시 짓는다(종전 그대로)
    ]:
        assert _bytes(seq) == want, (seq, _bytes(seq), want)


async def test_alt_letters_keep_the_esc_prefix():
    install_alt_word_keys()
    for seq, want in [("\x1bx", b"\x1bx"), ("\x1bX", b"\x1bX"),
                      ("\x1b한", "\x1b한".encode()), ("\x1b[97;4u", b"\x1bA"),
                      ("\x1b[46;3u", b"\x1b.")]:
        assert _bytes(seq) == want, (seq, _bytes(seq), want)


async def test_alt_backspace_is_esc_del_and_ctrl_h_is_bs():
    """Textual 이 두 키를 한 이름으로 접는 자리 — 글자 칸으로 되가른다."""
    assert _bytes("\x1b\x7f") == b"\x1b\x7f", "Alt+Backspace(ESC DEL) — 종전 ^W(0x17)"
    assert _bytes("\x17") == b"\x17", "진짜 Ctrl+W 는 그대로"
    assert _bytes("\x08") == b"\x08", "Ctrl+H — 종전 DEL(0x7f)"
    assert _bytes("\x7f") == b"\x7f", "Backspace 는 그대로 DEL"
    assert _bytes("\x1b[127;5u") == b"\x08" and _bytes("\x1b[127;3u") == b"\x1b\x7f"


async def test_ctrl_slash_enter_space_and_friends():
    assert _bytes("\x1f") == b"\x1f" and _bytes("\x1b[47;5u") == b"\x1f"
    assert _bytes("\x1b[13;5u") == b"\r", "Ctrl+Enter = CR — 종전 b\"\""
    assert _bytes("\x1b[13;3u") == b"\x1b\r", "Alt+Enter(kitty) = ESC CR"
    assert _bytes("\x1b[13;2u") == b"\n", "Shift+Enter 는 종전 그대로 LF"
    assert _bytes("\x00") == b"\x00" and _bytes("\x1b[32;5u") == b"\x00"
    assert _bytes("\x1b[97;7u") == b"\x1b\x01", "Alt+Ctrl+A(kitty) = ESC ^A"


async def test_esc_b_and_esc_f_arrive_as_alt_and_reach_the_pane_as_is():
    """Q1(사람 결정 2026-11-13): Textual 이 `ESC b/f` 를 Ctrl+←/→ 로 접던 표 줄을 뺀다 —
    맥 Option+←/→ 와 readline 의 단어 이동이 패널 안 셸에서 산다(종전: 버려짐)."""
    install_alt_word_keys()
    for seq in ("\x1bb", "\x1bf"):
        keys = _feed(seq)
        assert [k.key for k in keys] == ["alt+" + seq[1]], [k.key for k in keys]
        assert _bytes(seq) == seq.encode()
    assert _bytes("\x1b[1;5D") == b"\x1b[1;5D", "진짜 Ctrl+← 는 CSI 1;5D"
    assert install_alt_word_keys() is False, "두 번 불러도 같아야 한다"


async def test_our_input_still_moves_by_word_with_alt_b_and_alt_f():
    """그 대가로 잃을 뻔한 것 — pytmux 자신의 입력칸(명령 프롬프트 등)의 단어 이동. 종전에는
    `ESC b` 가 ctrl+left 로 와 단어 이동이었다. 이제 alt+b/f 에 같은 동작을 건다."""
    install_alt_word_keys()

    class _A(App):
        def compose(self):
            yield Input(value="foo bar baz")

    app = _A()
    async with app.run_test() as pilot:
        inp = app.query_one(Input)
        inp.focus()
        inp.cursor_position = len(inp.value)
        await pilot.press("alt+b")
        assert inp.cursor_position == 8, inp.cursor_position
        await pilot.press("alt+b")
        assert inp.cursor_position == 4, inp.cursor_position
        await pilot.press("alt+f")
        assert inp.cursor_position > 4, inp.cursor_position
        await pilot.press("ctrl+left")                     # 종전 키도 그대로
        assert inp.cursor_position == 4, inp.cursor_position
    from textual.widgets import TextArea
    assert "alt+b" in TextArea._merged_bindings.key_to_bindings


async def test_on_key_sends_the_new_bytes_and_chrome_keys_are_unchanged():
    """**호출부**: 평소 모드 `on_key` 가 패널로 실제로 싣는 바이트(헬퍼만 재면 호출을 지워도
    통과한다). 크롬 키(Alt+숫자 탭 전환 · F12 · prefix)는 종전처럼 패널로 안 간다."""
    from test_client import _with_app
    from textual.events import Key

    async def body(app, pilot, srv):
        sent, cmds = [], []
        app.send_input = lambda data: sent.append(data)
        app.send_cmd = lambda a, **k: cmds.append(a)
        app.open_prompt = lambda *a, **k: cmds.append("prompt")
        assert app.mode == "normal"
        for key, ch, want in [("ctrl+up", None, b"\x1b[1;5A"), ("alt+x", "x", b"\x1bx"),
                              ("ctrl+w", None, b"\x1b\x7f"), ("backspace", "\x08", b"\x08"),
                              ("shift+f5", None, b"\x1b[15;2~")]:
            sent.clear()
            app.on_key(Key(key=key, character=ch))
            assert sent == [want], (key, sent)
        sent.clear()
        app.tabbar.tabs = [{"index": 0}, {"index": 1}]
        app.on_key(Key(key="alt+2", character=None))
        app.on_key(Key(key="f12", character=None))
        assert sent == [], f"크롬 키가 패널로 샜다: {sent}"
        assert "select_window" in cmds and "prompt" in cmds, cmds
    await _with_app(body)


async def test_the_app_installs_the_alt_word_keys_at_construction():
    """**호출부**(소스로 단언): 정본 앱 `__init__` 이 `install_alt_word_keys()` 를 부른다.

    상태로 재면 공허하다 — 같은 프로세스의 다른 시험이 먼저 불러 Textual 표가 이미 바뀌어 있어,
    앱의 호출을 지워도 «두 줄이 빠졌다»가 참으로 남는다. 그래서 호출 자체를 AST 로 찾는다
    (`harden_no_color_filters` 를 부르는 그 `__init__` = 앱 생성자 안이어야 한다)."""
    import ast
    import pathlib
    src = (pathlib.Path(harness.__file__).resolve().parent.parent
           / "pytmuxlib" / "client.py").read_text(encoding="utf-8")
    inits = [f for f in ast.walk(ast.parse(src))
             if isinstance(f, ast.FunctionDef) and f.name == "__init__"]

    def calls(fn):
        return {c.func.id for c in ast.walk(fn)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}

    app_inits = [f for f in inits if "harden_no_color_filters" in calls(f)]
    assert app_inits, "앱 생성자를 못 찾았다(harden_no_color_filters 를 부르는 __init__)"
    assert any("install_alt_word_keys" in calls(f) for f in app_inits), \
        "앱 생성자가 install_alt_word_keys() 를 안 부른다 — ESC b/f 가 Ctrl+←/→ 로 접힌다"
