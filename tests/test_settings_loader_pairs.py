"""설정 표면의 «짝» — 쓰는 자리와 읽는 자리가 같은 말을 하나(pytmux-534·535·536·537).

pytmux-533(웹사이트 감사)이 코드 판독으로 찾은 갈림 넷은 전부 같은 모양이었다 — 기능을
한 자리에 더하고 **짝 표**(로더·`set` 갈래·명령 목록·카탈로그)를 안 고쳤다. 그래서 한 건씩
고치는 대신 짝을 **전수로** 맞대는 시험을 둔다.

되돌리면 실패해야 하는 오라클:
  · `keymap.load_config` 의 `alt-scroll` 갈래를 지우면
      → test_every_config_backed_setting_is_read_back_by_the_loader (534 의 부류 전체)
      → test_alt_scroll_in_the_file_is_read
  · `net-rtt-threshold` 갈래를 지우면 → test_net_rtt_threshold_is_read_and_clamped
  · 클라가 그 두 값을 config 에서 안 읽으면(**호출부**) → test_the_client_takes_both_from_its_config
  · `apply_option` 의 strip-box-drawing 갈래를 지우면
      → test_set_strip_box_drawing_reaches_the_client (537 ①)
  · `parse_set_hook` 이 다시 토큰을 공백으로 이으면
      → test_a_quoted_hook_survives_the_round_trip (536)
  · 디스패치가 그 함수를 안 쓰면(**호출부**) → test_set_hook_from_the_prompt_stores_the_quoted_command
"""
import os
import shlex
import tempfile

import harness  # noqa: F401  (러너 위생 · 경로 설정)

from pytmuxlib import keymap
from pytmuxlib.clientcmd import parse_set_hook
from pytmuxlib.clientutil import (COMMANDS, COMPLETIONS, SET_OPTION_CHOICES,
                                  SETTINGS, _SET_OPTION_NAMES)


def _cfg_from(text):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "config")
    with open(p, "w", encoding="utf-8") as f:
        f.write(text)
    return keymap.load_config(p)


def _candidates(desc):
    """그 줄이 받을 수 있는 값 몇 개 — 기본값과 다른 것이 하나는 끼게 고른다."""
    t = desc["type"]
    if t == "bool":
        return ["on", "off"]
    if t == "enum":
        return list(desc["choices"])
    if t == "int":
        return [str(desc["lo"]), str(desc["hi"])]
    if t == "ratio":
        return ["0.5"]
    if desc["key"] == "prefix":
        return ["C-a"]
    return ["zz-pytmux-534"]


def test_every_config_backed_setting_is_read_back_by_the_loader():
    """`:settings` 가 설정 파일에 쓰는 줄은 전부 다음 기동에 다시 읽혀야 한다.

    ⛔ 종전에는 `alt-scroll` 이 그 짝이 없었다 — 화면에서 바꾸면 파일에는 남는데 다음
    기동에 기본값으로 돌아갔다(pytmux-534). 같은 부류가 또 생기면 여기서 이름으로 운다."""
    base = _cfg_from("")
    missing = []
    for desc in SETTINGS:
        if desc.get("backend") != "config":
            continue
        changed = False
        for v in _candidates(desc):
            d = tempfile.mkdtemp()
            p = os.path.join(d, "config")
            keymap.set_config_option(desc["key"], v, p)
            if keymap.load_config(p) != base:
                changed = True
                break
        if not changed:
            missing.append(desc["key"])
    assert not missing, f"설정 화면이 쓰는데 로더가 안 읽는 줄: {missing}"


def test_alt_scroll_in_the_file_is_read():
    assert _cfg_from("set alt-scroll off\n")["disable_alt_scroll"] is False
    assert _cfg_from("set alt-scroll on\n")["disable_alt_scroll"] is True
    # 밑줄 표기도 같은 옵션이다(쓰기-백 별칭표와 같은 규칙).
    assert _cfg_from("set alt_scroll off\n")["disable_alt_scroll"] is False
    # 줄이 없으면 키도 없다 — 클라의 기본(True)이 그대로 선다.
    assert "disable_alt_scroll" not in _cfg_from("")


def test_alt_scroll_write_back_replaces_the_underscore_line():
    """쓰기-백이 밑줄 철자 줄을 같은 옵션으로 알아봐야 줄이 둘로 안 는다."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "config")
    with open(p, "w", encoding="utf-8") as f:
        f.write("set alt_scroll off\n")
    keymap.set_config_option("alt-scroll", "on", p)
    txt = open(p, encoding="utf-8").read()
    assert txt.count("alt") == 1, txt
    assert keymap.load_config(p)["disable_alt_scroll"] is True


def test_net_rtt_threshold_is_read_and_clamped():
    assert _cfg_from("set net-rtt-threshold 1.5\n")["net_rtt_threshold"] == 1.5
    assert _cfg_from("set net_rtt_threshold 0.8\n")["net_rtt_threshold"] == 0.8
    assert _cfg_from("set net-rtt-threshold 999\n")["net_rtt_threshold"] == 10.0
    assert _cfg_from("set net-rtt-threshold 0\n")["net_rtt_threshold"] == 0.05
    # 못 읽는 값은 무시한다 — 클라의 기본(0.4)이 그대로 선다.
    assert "net_rtt_threshold" not in _cfg_from("set net-rtt-threshold abc\n")


async def test_the_client_takes_both_from_its_config():
    """★ 호출부 — 로더가 값을 채워도 클라가 그 키를 안 읽으면 공허하다."""
    from test_client import _with_app

    cfg = _cfg_from("set alt-scroll off\nset net-rtt-threshold 1.5\n")

    async def body(app, pilot, srv):
        assert app.disable_alt_scroll is False
        assert app.net_rtt_threshold == 1.5

    await _with_app(body, cfg=cfg)


async def test_set_strip_box_drawing_reaches_the_client():
    """`:set strip-box-drawing off` 가 조용히 무시되던 것(pytmux-537 ①)."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        app.strip_box_drawing = True
        app._run_command("set strip-box-drawing off")
        assert app.strip_box_drawing is False
        app._run_command("set strip-box-drawing on")
        assert app.strip_box_drawing is True

    await _with_app(body)


def test_strip_box_drawing_is_offered_as_a_set_option():
    assert "strip-box-drawing" in _SET_OPTION_NAMES
    assert SET_OPTION_CHOICES["strip-box-drawing"] == ("on", "off")
    assert "set strip-box-drawing" in COMPLETIONS


def test_layout_list_is_in_the_command_list():
    """디스패치는 되는데 `?` 목록·자동완성에 안 뜨던 것(pytmux-537 ②)."""
    names = [c[0] for c in COMMANDS]
    assert "layout-list" in names
    assert "layout-list" in COMPLETIONS


def test_no_catalog_names_an_alias_that_nothing_dispatches():
    """영어 카탈로그가 `token-settings` 를 별칭으로 적었지만 받는 곳이 없었다(537 ③)."""
    import importlib

    from pytmuxlib import i18n
    importlib.import_module("pytmuxlib.plugins.claude-code")   # 카탈로그 등록
    prev = i18n.get_locale()
    try:
        i18n.set_locale("en")
        en = i18n.t("cmd.claude-settings")
    finally:
        i18n.set_locale(prev)
    assert en and en != "cmd.claude-settings", en
    assert "token-settings" not in en, en


def test_a_quoted_hook_survives_the_round_trip():
    """`:` 프롬프트로 건 훅과 설정 파일 `hook` 줄이 **같은 명령**으로 발화한다(536)."""
    line = 'set-hook claude-limit run-shell "notify.sh $PYTMUX_ACCOUNT"'
    args = shlex.split(line)[1:]
    name, cmd = parse_set_hook(args)
    assert name == "claude-limit"
    # 발화 때 _run_command 가 다시 쪼갠다 — run-shell 이 받는 인자가 하나여야 한다.
    assert shlex.split(cmd) == ["run-shell", "notify.sh $PYTMUX_ACCOUNT"]
    # 설정 파일 경로와 같은 토큰이 된다.
    cfg = _cfg_from('hook claude-limit run-shell "notify.sh $PYTMUX_ACCOUNT"\n')
    assert shlex.split(cfg["hooks"]["claude-limit"]) == shlex.split(cmd)


def test_only_leading_dashes_are_flags():
    assert parse_set_hook(["-g", "alert-bell", "run-shell", "-b", "x"]) == (
        "alert-bell", "run-shell -b x")
    assert parse_set_hook(["-u", "alert-bell"]) == ("-u", "alert-bell")
    assert parse_set_hook(["alert-bell"]) is None
    assert parse_set_hook(["-u"]) is None
    assert parse_set_hook([]) is None


async def test_set_hook_from_the_prompt_stores_the_quoted_command():
    """★ 호출부 — 함수만 고치고 디스패치가 옛 이어 붙이기를 쓰면 여기서 운다."""
    from test_client import _with_app

    async def body(app, pilot, srv):
        app._run_command(
            'set-hook claude-limit run-shell "notify.sh $PYTMUX_ACCOUNT"')
        assert shlex.split(app.hooks["claude-limit"]) == [
            "run-shell", "notify.sh $PYTMUX_ACCOUNT"]
        app._run_command("set-hook -u claude-limit")
        assert "claude-limit" not in app.hooks

    await _with_app(body)
