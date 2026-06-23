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
