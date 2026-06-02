"""설정 파일 로드 및 키 표기 변환."""
from __future__ import annotations

import os


def _tmux_key_to_textual(tok: str) -> str:
    """tmux 키 표기(C-a)를 Textual 키 이름(ctrl+a)으로 변환."""
    tok = tok.strip()
    if tok.lower().startswith("c-") and len(tok) == 3:
        return "ctrl+" + tok[2].lower()
    return tok


def _key_to_ctrl_bytes(prefix_key: str) -> bytes:
    """prefix 키를 셸로 그대로 보낼 때의 바이트 시퀀스."""
    if prefix_key.startswith("ctrl+") and len(prefix_key) == 6:
        c = prefix_key[5]
        if c.isalpha():
            return bytes([ord(c.lower()) - 96])
    if len(prefix_key) == 1:
        return prefix_key.encode("utf-8", "replace")
    return b"\x02"


def load_config(path: str | None = None) -> dict:
    """설정 파일을 읽어 클라이언트 설정 딕셔너리를 만든다.

    탐색 순서: 인자 경로 → $PYTMUX_CONFIG → $XDG_CONFIG_HOME/pytmux/config
    → ~/.config/pytmux/config → ~/.pytmux.conf

    지원 지시어:
        set prefix C-a            # prefix 키 변경
        set mouse on|off          # 마우스 사용 여부
        set status-bg <color>     # 상태줄 배경색
        set status-fg <color>     # 상태줄 글자색
        bind <key> <command...>   # prefix 후 <key> 에 명령 바인딩
    """
    cfg = {"prefix": "ctrl+b", "mouse": True, "bindings": {}, "aliases": {},
           "hooks": {}, "status_bg": "green", "status_fg": "black",
           "mode_keys": "vi", "tab_bar_always": False}
    candidates = []
    if path:
        candidates.append(path)
    if os.environ.get("PYTMUX_CONFIG"):
        candidates.append(os.environ["PYTMUX_CONFIG"])
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    candidates.append(os.path.join(xdg, "pytmux", "config"))
    candidates.append(os.path.expanduser("~/.pytmux.conf"))
    cfgfile = next((c for c in candidates if c and os.path.isfile(c)), None)
    if not cfgfile:
        return cfg
    try:
        with open(cfgfile, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if parts[0] == "set" and len(parts) >= 3:
                    opt, val = parts[1], " ".join(parts[2:])
                    if opt == "prefix":
                        cfg["prefix"] = _tmux_key_to_textual(val)
                    elif opt == "mouse":
                        cfg["mouse"] = val.lower() in ("on", "true", "1", "yes")
                    elif opt == "status-bg":
                        cfg["status_bg"] = val
                    elif opt == "status-fg":
                        cfg["status_fg"] = val
                    elif opt == "mode-keys":
                        cfg["mode_keys"] = "emacs" if val == "emacs" else "vi"
                    elif opt == "status-left":
                        cfg["status_left"] = val
                    elif opt == "status-right":
                        cfg["status_right"] = val
                    elif opt == "status-position":
                        cfg["status_position"] = "top" if val == "top" else "bottom"
                    elif opt == "status-interval":
                        try:
                            cfg["status_interval"] = max(1, int(val))
                        except ValueError:
                            pass
                    elif opt in ("tab-bar", "tabbar"):
                        # always = 탭이 하나여도 상단 탭바 표시(auto/off = 자동: 2개↑)
                        cfg["tab_bar_always"] = val.lower() in ("always", "on",
                                                                "true", "1", "yes")
                    elif opt == "set-titles":
                        cfg["set_titles"] = val.lower() in ("on", "true", "1", "yes")
                    elif opt == "set-titles-string":
                        cfg["title_fmt"] = val
                elif parts[0] == "bind" and len(parts) >= 3:
                    cfg["bindings"][parts[1]] = " ".join(parts[2:])
                elif parts[0] == "alias" and len(parts) >= 3:
                    cfg["aliases"][parts[1]] = " ".join(parts[2:])
                elif parts[0] in ("hook", "set-hook") and len(parts) >= 3:
                    cfg["hooks"][parts[1]] = " ".join(parts[2:])
    except OSError:
        pass
    return cfg
