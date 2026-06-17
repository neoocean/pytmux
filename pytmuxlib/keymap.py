"""설정 파일 로드 및 키 표기 변환."""
from __future__ import annotations

import os


# tmux 수정자 접두사 → Textual 수정자 이름. C-=ctrl, M-=meta(=alt), A-=alt,
# S-=shift. 풀네임(ctrl-/alt-/meta-/shift-)도 허용. 임의 순서로 쌓을 수 있다.
_MOD_PREFIXES = {
    "c-": "ctrl", "ctrl-": "ctrl",
    "m-": "alt", "meta-": "alt", "a-": "alt", "alt-": "alt",
    "s-": "shift", "shift-": "shift",
}
# tmux/일반 키 이름(소문자) → Textual 키 이름.
_NAMED_KEYS = {
    "up": "up", "down": "down", "left": "left", "right": "right",
    "home": "home", "end": "end",
    "pageup": "pageup", "ppage": "pageup", "pgup": "pageup",
    "pagedown": "pagedown", "npage": "pagedown", "pgdn": "pagedown",
    "tab": "tab", "space": "space", "enter": "enter", "return": "enter",
    "escape": "escape", "esc": "escape",
    "bspace": "backspace", "backspace": "backspace", "bs": "backspace",
    "delete": "delete", "dc": "delete", "del": "delete",
    "insert": "insert", "ic": "insert",
}


def _normalize_base(base: str) -> str | None:
    """수정자를 떼어낸 기본 키 토큰을 Textual 키 이름으로 정규화. 모르면 None.

    함수키 F1..F12 → f1..f12, 이름 키(Up/Home/Tab/Esc/BSpace…) → Textual 이름,
    한 글자 → 소문자. 인식 못 하는 다중 글자(오타 등)는 None.
    """
    if not base:
        return None
    low = base.lower()
    if len(low) >= 2 and low[0] == "f" and low[1:].isdigit():
        return "f" + str(int(low[1:]))   # F5 → f5
    if low in _NAMED_KEYS:
        return _NAMED_KEYS[low]
    if len(base) == 1:
        return low
    return None


def _tmux_key_to_textual(tok: str) -> str:
    """tmux/일반 키 표기를 Textual 키 이름으로 변환.

    예: C-a→ctrl+a, M-x→alt+x, S-Tab→shift+tab, F5→f5, C-S-Left→ctrl+shift+left.
    수정자(C-/M-/S-/A-, 풀네임 포함)는 임의 순서로 쌓을 수 있고, **알파벳순 정렬**
    후 '+' 로 이어 붙인다 — Textual 의 xterm 파서가 modifier 토큰을 sort 해서
    event.key 를 만들기 때문에(예: "ctrl+shift+left") 같은 순서여야 바인딩이
    매칭된다. 인식 못 하는 토큰은 **원문 그대로** 돌려준다(하위호환). 수정자 없는
    한 글자는 대소문자를 보존한다 — prefix 핸들러가 인쇄 가능한 글자는 event.key
    가 아니라 character(예: 'X')로 매칭하므로 소문자화하면 안 된다.

    주의: 글자에 shift 만 붙은 조합(S-a)은 터미널이 보통 대문자 글자(event.key
    'A')로 보고하므로 shift+letter 바인딩은 안 먹을 수 있다. S- 는 특수키
    (S-Tab/S-Left/S-F5 등)에 쓰는 게 안전하다.
    """
    raw = tok.strip()
    if not raw:
        return tok
    mods: set[str] = set()
    rest = raw
    while True:
        low = rest.lower()
        for pre, mod in _MOD_PREFIXES.items():
            if low.startswith(pre) and len(rest) > len(pre):
                mods.add(mod)
                rest = rest[len(pre):]
                break
        else:
            break
    if not mods:
        # 수정자 없음 — 함수키/이름키만 정규화, 한 글자(대소문자 보존)·이미-Textual
        # 이름 등은 원문 유지.
        low = raw.lower()
        if len(low) >= 2 and low[0] == "f" and low[1:].isdigit():
            return "f" + str(int(low[1:]))
        if low in _NAMED_KEYS:
            return _NAMED_KEYS[low]
        return raw
    base = _normalize_base(rest)
    if base is None:
        return raw   # 파싱 실패 — 원문 유지(normalize_binding_key 가 경고)
    return "+".join(sorted(mods)) + "+" + base


def normalize_binding_key(tok: str) -> tuple[str, str | None]:
    """bind 용 키 정규화 + 경고 메시지(정상이면 None).

    인식 못 한 다중 글자 토큰(오타 'Ennter' 등)이나 빈 토큰이면 그대로 바인딩하되
    경고 문자열을 돌려주어 호출부(bind-key 명령·설정 로드)가 사용자에게 알릴 수
    있게 한다 — 과거엔 잘못된 키가 조용히 (매칭 안 되는) 바인딩으로 묻혔다.
    """
    key = _tmux_key_to_textual(tok)
    raw = tok.strip()
    if not raw:
        return key, "빈 키 — 바인딩할 키가 없음"
    suspicious = (len(raw) > 1 and "+" not in raw and key == raw
                  and raw.lower() not in _NAMED_KEYS
                  and not (raw[0] in "fF" and raw[1:].isdigit()))
    if suspicious:
        return key, f"알 수 없는 키 표기 '{tok}' (오타 확인 — 그대로 바인딩)"
    return key, None


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
        set default-path <val>    # 새 탭/패널 시작 디렉토리
                                  #   current(기본)=현재 패널, home=$HOME, 또는 경로
        set inactive-dim on|off   # 비활성 패널 흐리게(§2.9, 기본 on)
        set inactive-dim-ratio <0~0.8>   # 흐리게 세기(기본 0.18)
        bind <key> <command...>   # prefix 후 <key> 에 명령 바인딩
        bind -n <key> <command...>  # prefix 없이 바로(root table, §2.5) — 내장
                                  #   크롬 키(ESC/`/F12/prefix/Ctrl+V)가 우선이고
                                  #   나머지 키를 패널 전달 전에 가로챈다
    """
    cfg = {"prefix": "ctrl+b", "mouse": True, "bindings": {},
           "root_bindings": {}, "aliases": {},
           "hooks": {}, "status_bg": None, "status_fg": None,
           "mode_keys": "vi", "tab_bar_always": True, "default_path": "current"}
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
                        # always(기본) = 탭이 하나여도 상단 탭바 표시
                        # auto/off = 자동(탭 2개 이상일 때만 표시)
                        cfg["tab_bar_always"] = val.lower() in ("always", "on",
                                                                "true", "1", "yes")
                    elif opt == "set-titles":
                        cfg["set_titles"] = val.lower() in ("on", "true", "1", "yes")
                    elif opt == "set-titles-string":
                        cfg["title_fmt"] = val
                    elif opt in ("default-path", "default_path"):
                        # 새 탭/패널이 시작할 디렉토리.
                        #   current = 현재 활성 패널의 cwd(기본)
                        #   home    = $HOME
                        #   <경로>  = 해당 절대/~ 경로
                        cfg["default_path"] = val.strip()
                    elif opt in ("lang", "language"):
                        # UI 로케일(§6 i18n): ko|en. 미지원 값은 무시(런타임 resolve 가
                        # 환경 LANG 으로 폴백). 런타임 `lang` 명령 선택이 이보다 우선.
                        v = val.strip().lower()
                        if v in ("ko", "en"):
                            cfg["lang"] = v
                    elif opt in ("inactive-dim", "inactive_dim"):
                        # §2.9 비활성 패널 흐리게(on/off). 런타임 `inactive-dim` 명령이
                        # 세션 우선(이 값은 기본/영속).
                        cfg["inactive_dim"] = val.lower() in (
                            "on", "true", "1", "yes")
                    elif opt in ("inactive-dim-ratio", "inactive_dim_ratio"):
                        # 흐리게 세기(0~0.8). 클라가 범위 클램프. 파싱 실패는 무시.
                        try:
                            cfg["inactive_dim_ratio"] = float(val)
                        except ValueError:
                            pass
                elif parts[0] == "bind" and len(parts) >= 3:
                    # 키를 Textual 표기로 정규화해 저장한다 — 런타임 매칭 토큰이
                    # event.key(ctrl+x 등)이므로 raw "C-x" 로 두면 절대 안 먹는다.
                    # `bind -n` 은 root table(§2.5) — prefix 없이 바로 발동.
                    if parts[1] == "-n":
                        if len(parts) >= 4:
                            bkey, warn = normalize_binding_key(parts[2])
                            cfg["root_bindings"][bkey] = " ".join(parts[3:])
                            if warn:
                                cfg.setdefault("warnings", []).append(
                                    f"config bind -n: {warn}")
                    else:
                        bkey, warn = normalize_binding_key(parts[1])
                        cfg["bindings"][bkey] = " ".join(parts[2:])
                        if warn:
                            cfg.setdefault("warnings", []).append(
                                f"config bind: {warn}")
                elif parts[0] == "alias" and len(parts) >= 3:
                    cfg["aliases"][parts[1]] = " ".join(parts[2:])
                elif parts[0] in ("hook", "set-hook") and len(parts) >= 3:
                    cfg["hooks"][parts[1]] = " ".join(parts[2:])
    except OSError:
        pass
    return cfg


# ── 설정 파일 쓰기-백(통합 설정 UI 영속) ────────────────────────────────────
# load_config 는 시작 시 읽기 전용이라, 런타임으로 바꾼 config-scoped 설정은
# 세션이 끝나면 사라졌다. `:settings` 화면이 바꾼 값을 사용자 config 파일의
# `set <opt> <val>` 줄로 직접 기록해 영속한다 — 주석·bind·alias·hook 줄은
# 일절 건드리지 않고(있으면 값만 치환, 없으면 끝에 한 줄 추가), 파서가 다시
# 읽을 수 있는 정규형(하이픈)으로 쓴다.

# 같은 옵션을 가리키는 표기 변형(load_config 의 별칭과 일치시켜야 기존 줄을
# 찾아 덮어쓴다 — 새 줄을 중복 추가하지 않게). 키는 정규(하이픈) 이름.
_OPT_ALIASES = {
    "tab-bar": ("tabbar",),
    "default-path": ("default_path",),
    "inactive-dim": ("inactive_dim",),
    "inactive-dim-ratio": ("inactive_dim_ratio",),
    "lang": ("language",),
}


def _opt_match_set(opt: str) -> set[str]:
    """opt 와 같은 설정을 의미하는 모든 토큰 집합(정규화 비교용, 하이픈/언더바 무시)."""
    names = {opt, *_OPT_ALIASES.get(opt, ())}
    # 역방향: opt 가 어떤 정규 이름의 별칭이면 그 그룹 전체도 포함.
    for canon, al in _OPT_ALIASES.items():
        if opt == canon or opt in al:
            names.add(canon)
            names.update(al)
    return {n.replace("_", "-").lower() for n in names}


# textual 키 이름(ctrl+a 등) → tmux 표기(C-a). load_config 의 _tmux_key_to_textual
# 역방향(prefix 를 config 에 되쓸 때). _NAMED_KEYS 의 대표 표기로 되돌린다.
_TEXTUAL_TO_TMUX_MOD = {"ctrl": "C", "alt": "M", "shift": "S"}
_TEXTUAL_TO_TMUX_BASE = {
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End", "pageup": "PageUp", "pagedown": "PageDown",
    "tab": "Tab", "space": "Space", "enter": "Enter", "escape": "Escape",
    "backspace": "BSpace", "delete": "Delete", "insert": "Insert",
}


def textual_key_to_tmux(key: str) -> str | None:
    """'ctrl+a'→'C-a', 'shift+tab'→'S-Tab', 'f5'→'F5'. 안전 변환 불가 시 None.

    수정자는 C-/M-/S- 로 되돌리고, _tmux_key_to_textual 가 임의 순서를 재정규화
    하므로 순서는 무관. 베이스가 한 글자면 대소문자 보존, 이름 키는 대표 표기,
    f-키는 대문자. 인식 못 하는 다중 글자 베이스는 None(드묾 — UI 가 편집 비활성)."""
    parts = key.split("+")
    base = parts[-1]
    mods = parts[:-1]
    out_mods = []
    for m in mods:
        if m not in _TEXTUAL_TO_TMUX_MOD:
            return None
        out_mods.append(_TEXTUAL_TO_TMUX_MOD[m])
    low = base.lower()
    if len(base) == 1:
        tbase = base                       # 대소문자 보존
    elif low in _TEXTUAL_TO_TMUX_BASE:
        tbase = _TEXTUAL_TO_TMUX_BASE[low]
    elif low and low[0] == "f" and low[1:].isdigit():
        tbase = "F" + low[1:]
    else:
        return None
    return "-".join(out_mods + [tbase]) if out_mods else tbase


def config_path_for_write(path: str | None = None) -> str:
    """쓰기 대상 config 파일 경로를 정한다. load_config 와 같은 후보 순서로 기존
    파일을 찾고, 없으면 $XDG_CONFIG_HOME/pytmux/config(또는 ~/.config/pytmux/config)
    를 기본 생성 경로로 한다(부모 디렉토리는 호출부가 set_config_option 에서 생성)."""
    candidates = []
    if path:
        candidates.append(path)
    if os.environ.get("PYTMUX_CONFIG"):
        candidates.append(os.environ["PYTMUX_CONFIG"])
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    default_path = os.path.join(xdg, "pytmux", "config")
    candidates.append(default_path)
    candidates.append(os.path.expanduser("~/.pytmux.conf"))
    existing = next((c for c in candidates if c and os.path.isfile(c)), None)
    return existing or (path or default_path)


def set_config_option(opt: str, value: str, path: str | None = None) -> str:
    """config 파일의 `set <opt> <value>` 를 갱신(없으면 추가)하고 쓴 경로를 돌려준다.

    - 첫 비주석 `set <opt(별칭 포함)>` 줄을 찾아 **선행 공백 보존하며 그 줄 전체를
      `set <opt> <value>` 로 치환**(인라인 주석은 옵션 줄에선 드물어 단순 치환).
    - 없으면 파일 끝에 `set <opt> <value>` 한 줄 추가(끝 개행 보장).
    - 주석/bind/alias/hook/빈 줄은 그대로 둔다. temp+os.replace 로 원자적 쓰기.
    value 는 호출부가 이미 정규형 문자열로 직렬화해 넘긴다(bool→on/off, prefix→C-a 등)."""
    target = config_path_for_write(path)
    try:
        with open(target, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        lines = []

    match = _opt_match_set(opt)
    newline = f"set {opt} {value}\n"
    replaced = False
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        toks = s.split()
        if len(toks) >= 2 and toks[0] == "set" \
                and toks[1].replace("_", "-").lower() in match:
            indent = raw[:len(raw) - len(raw.lstrip())]
            lines[i] = f"{indent}set {opt} {value}\n"
            replaced = True
            break
    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(newline)

    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(tmp, target)
    return target
