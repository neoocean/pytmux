"""keymap config 쓰기-백 회귀(:settings 영속 인프라).

set_config_option 은 사용자 config 파일의 주석·bind·alias 를 보존하며 `set` 줄만
갱신(없으면 추가)하고, load_config 가 다시 읽을 수 있는 정규형으로 쓴다. prefix
역변환(textual→tmux)은 _tmux_key_to_textual 의 라운드트립이어야 한다."""
import os
import tempfile

import harness  # noqa: F401  (경로 설정)

from pytmuxlib import keymap


async def test_set_config_option_preserves_comments_binds_and_roundtrips():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "config")
    with open(p, "w", encoding="utf-8") as f:
        f.write("# my header\nset mouse on\nbind | split-window -h\n"
                "# keep me\nset inactive-dim on\n")
    keymap.set_config_option("mouse", "off", p)            # 기존 줄 치환
    keymap.set_config_option("inactive-dim", "off", p)     # 기존 줄 치환
    keymap.set_config_option("inactive-dim-ratio", "0.30", p)  # 없던 옵션 추가
    txt = open(p, encoding="utf-8").read()
    # 주석·바인딩 보존
    assert "# my header" in txt and "# keep me" in txt, txt
    assert "bind | split-window -h" in txt, txt
    # 중복 set 줄 안 생김(치환)
    assert txt.count("set mouse") == 1 and txt.count("set inactive-dim ") == 1
    # 파서가 다시 읽어 값 일치
    cfg = keymap.load_config(p)
    assert cfg["mouse"] is False
    assert cfg["inactive_dim"] is False
    assert cfg.get("inactive_dim_ratio") == 0.30
    assert cfg["bindings"] == {"|": "split-window -h"}


async def test_set_config_option_matches_underscore_alias():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "config")
    with open(p, "w", encoding="utf-8") as f:
        f.write("set inactive_dim_ratio 0.18\n")   # 언더바 표기 기존 줄
    keymap.set_config_option("inactive-dim-ratio", "0.40", p)
    txt = open(p, encoding="utf-8").read()
    # 새 줄 추가 없이 기존 언더바 줄을 정규형으로 치환
    assert txt.count("inactive") == 1, txt
    assert keymap.load_config(p).get("inactive_dim_ratio") == 0.40


async def test_set_config_option_creates_missing_file_and_dirs():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "nested", "config")
    keymap.set_config_option("mode-keys", "emacs", p)
    assert os.path.isfile(p)
    assert keymap.load_config(p)["mode_keys"] == "emacs"


async def test_set_config_option_copy_unwrap_roundtrips():
    """copy-unwrap(마우스 복사 시 앱-접힘 펴기, 기본 on)이 `:settings`→config 로
    영속되고 load_config 가 copy_unwrap(언더바)으로 되읽는다. 별칭(언더바) 줄도
    정규형으로 치환해 중복 줄이 생기지 않아야 한다."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "config")
    assert keymap.load_config(p).get("copy_unwrap") is None    # 미지정 = 코드 기본
    keymap.set_config_option("copy-unwrap", "off", p)
    assert keymap.load_config(p).get("copy_unwrap") is False
    keymap.set_config_option("copy-unwrap", "on", p)
    txt = open(p, encoding="utf-8").read()
    assert txt.count("set copy-unwrap ") == 1, txt
    assert keymap.load_config(p).get("copy_unwrap") is True
    with open(p, "w", encoding="utf-8") as f:
        f.write("set copy_unwrap on\n")
    keymap.set_config_option("copy-unwrap", "off", p)
    txt = open(p, encoding="utf-8").read()
    assert txt.count("copy") == 1, txt
    assert keymap.load_config(p).get("copy_unwrap") is False


async def test_set_config_option_strip_box_drawing_roundtrips():
    """§2.13: strip-box-drawing 옵션이 config 에 set 줄로 영속되고 load_config 가
    strip_box_drawing(언더바)으로 되읽는다. 기본값은 코드에서 on 이므로 off 영속을 검증."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "config")
    keymap.set_config_option("strip-box-drawing", "off", p)   # 없던 옵션 추가
    assert keymap.load_config(p).get("strip_box_drawing") is False
    keymap.set_config_option("strip-box-drawing", "on", p)    # 기존 줄 치환
    txt = open(p, encoding="utf-8").read()
    assert txt.count("set strip-box-drawing ") == 1, txt      # 중복 추가 안 함
    assert keymap.load_config(p).get("strip_box_drawing") is True
    # 언더바 표기 기존 줄도 정규형으로 치환(별칭 매칭)
    with open(p, "w", encoding="utf-8") as f:
        f.write("set strip_box_drawing on\n")
    keymap.set_config_option("strip-box-drawing", "off", p)
    txt = open(p, encoding="utf-8").read()
    assert txt.count("strip") == 1, txt
    assert keymap.load_config(p).get("strip_box_drawing") is False


async def test_textual_key_to_tmux_roundtrips_with_parser():
    for tmux in ("C-a", "M-x", "S-Tab", "F5", "C-Left"):
        textual = keymap._tmux_key_to_textual(tmux)
        assert keymap.textual_key_to_tmux(textual) == tmux, (tmux, textual)
    # 단일 글자는 대소문자 보존
    assert keymap.textual_key_to_tmux("b") == "b"


async def test_drag_copy_policy_has_three_values_and_falls_back_to_off():
    """`mouse-drag-copy` 는 값이 셋이다(pytmux-422 · 사람의 결정 2026-08-31).

    ⛔ 이 판정은 저장소에 **한 곳**(keymap.drag_copy_policy)이어야 한다 — 네 자리가
    각자 `val.lower() in (...)` 를 적고 있던 것을 하나로 모았고, 늘어난 값 하나가 한
    자리만 빠지면 그 자리는 조용히 `off` 로 떨어진다.

    ⚠ **모르는 값이 `off` 인 것도 계약이다** — 종전 파서가 그랬다(오타 하나로 «다른»
    새 동작이 켜지는 쪽이 더 나쁘다). 이 단언을 지우면 그 함정이 되살아난다.
    """
    pol = keymap.drag_copy_policy
    assert keymap.DRAG_COPY_VALUES == ("on", "off", "shift")
    for v in ("on", "ON", " true ", "1", "yes", True, None):
        assert pol(v) == "on", v
    for v in ("off", "no", "0", "", "shfit", False):
        assert pol(v) == "off", v
    for v in ("shift", "Shift", " SHIFT "):
        assert pol(v) == "shift", v


async def test_config_file_reads_mouse_drag_copy_shift():
    """설정 파일의 세 번째 값이 로더를 지나 그대로 남는다(옛 파서는 여기서 bool 로
    뭉개 `off` 를 만들었다)."""
    d = tempfile.mkdtemp()
    p = os.path.join(d, "config")
    with open(p, "w", encoding="utf-8") as f:
        f.write("set mouse-drag-copy shift\n")
    assert keymap.load_config(p)["mouse_drag_copy"] == "shift"
    # 쓰기-백도 같은 낱말로 돈다(설정 화면이 고른 값이 다음 기동에 살아남나).
    keymap.set_config_option("mouse-drag-copy", "off", p)
    assert keymap.load_config(p)["mouse_drag_copy"] == "off"
    keymap.set_config_option("mouse_drag_copy", "shift", p)   # 언더바 별칭
    txt = open(p, encoding="utf-8").read()
    # 별칭으로 불러도 **줄은 하나**다(중복 set 줄이 생기면 로더는 나중 것만 본다).
    assert sum(l.startswith("set mouse") for l in txt.splitlines()) == 1, txt
    assert keymap.load_config(p)["mouse_drag_copy"] == "shift"
