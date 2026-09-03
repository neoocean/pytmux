"""명령 한 줄 → **서버 액션 + 인자**의 규칙 한 벌 — UI 무의존(rich/textual 을 안 읽는다).

# 왜 여기인가 (pytmux-35 의 본줄기)

팔레트에 보이는데 눌러도 안 먹는 줄이 열여덟 있었다. 뿌리는 네이티브 클라가 플러그인
명령을 **전부** `plugin_open`("화면을 다오")으로 보낸다는 것이고, 리포트가 나눈 처방 셋
중 *"가장 옳은 길"* 로 적은 것이 **서버가 플러그인 명령을 직접 받는 것**이었다.

그런데 그 길에는 걸림돌이 하나 있었다: 명령을 액션으로 옮기는 규칙이 **정본 클라 안에**
있었다(`handle_command` 의 `elif` 사슬). 그 안에는 액션 이름뿐 아니라

- **인자 칸 이름**이 액션마다 다르다는 사실(`value` · `msg` · `name` · `sub`+`arg`)과
- **인자 파싱**(3-state `corruption|idle|off`, `strict|weak|off`, on/off)

이 함께 들어 있다. 이것을 클라(네이티브)가 알게 하면 그 표가 서버와 갈리고, 갈린 순간
명령은 **조용히 아무 일도 안 한다** — 죽은 명령이 생긴 원인 그대로다.

그래서 규칙을 여기 한 벌로 빼고 **두 소비자**가 같은 것을 쓴다:

- 정본 — `handle_command` 가 이 표로 액션을 얻어 `send_cmd` 한다.
- 서버 — 네이티브 클라가 보낸 `plugin_cmd {name, arg}` 를 받아 같은 표로 풀고
  자기 `server_command` 로 넘긴다.

`cells.py`(자리 규칙)·`statusbadges.py`(표식 규칙)가 먼저 낸 길이고, 이것은 **명령 규칙**
판이다.

# 여기에 무엇을 두지 않나

**화면을 여는 명령은 여기 없다**(`claude-settings`·`model`·`claude-token-log` …).
그것들은 액션이 아니라 팝업이고, 그 길은 Tier C 화면 스펙이다(`usage-panel` 이 먼저
갔다 — pytmux-20). 여기 두면 "서버로 보냈는데 아무 일도 안 나는" 칸이 생긴다.

**알림이 딸린 것도 없다**(`claude-token-debug` 는 보내고 나서 알림을 띄운다). 알림은
클라의 일이라 이 표가 나를 수 없다.

# 인자에 따라 갈리는 것 (`prompt-clear-queue`)

한 이름이 **화면이기도 하고 액션이기도** 할 수 있다. `prompt-clear-queue` 는 무인자면
큐를 보여 주고(화면), 인자가 있으면 쌓거나 비운다(액션). 그 갈래는 여기서 정한다 —
무인자에 `None` 을 돌려주면 서버가 그대로 **화면 경로**로 넘어간다(`servercmd`
`_cmd_plugin_cmd` 의 3단계). 클라는 어느 쪽인지 몰라도 된다.
"""
from __future__ import annotations

from pytmuxlib.plugins import onoff


def redraw_arg(args):
    """`claude-auto-redraw` 3-state. corruption/idle/off 명시면 그 모드,
    on→idle, off→off, 무인자/toggle→None(서버가 순환). 빈 선택지("")도 None."""
    s = " ".join(a for a in args if a).lower()
    if any(k in s for k in ("corrupt", "감지", "깨짐")):
        return "corruption"
    if "idle" in s or "완료" in s:
        return "idle"
    v = onoff(args)
    return "idle" if v is True else "off" if v is False else None


def verify_arg(args):
    """`claude-resume-verify` 3-state. strict/weak/off 명시면 그 모드,
    on→weak, off→off, 무인자/toggle→None(서버가 순환). `redraw_arg` 와 동형."""
    s = " ".join(a for a in args if a).lower()
    if any(k in s for k in ("strict", "엄격")):
        return "strict"
    if any(k in s for k in ("weak", "약")):
        return "weak"
    v = onoff(args)
    return "weak" if v is True else "off" if v is False else None


def pc_queue_arg(args):
    """`prompt-clear-queue` 의 갈래 — `(액션, 인자)` 이거나 `None`(무인자 = 화면).

    `-c`/`clear`/`--clear` 는 비우기, 그 밖의 말은 그대로 큐에 쌓는다(정본
    `_pc_queue` 와 같은 규칙 — 두 벌이 되면 같은 글자가 클라마다 다른 일을 한다)."""
    if not args:
        return None
    if str(args[0]).lower() in ("-c", "clear", "--clear"):
        return "pc_queue_clear", {}
    return "pc_queue_add", {"cmd": " ".join(args).strip()}


#: 명령 이름(별칭 포함) → `(액션, 인자 만드는 함수)`.
#:
#: 인자 함수는 `args`(낱말 목록)를 받아 **그 액션이 실제로 읽는 칸**의 dict 를 돌려준다 —
#: 칸 이름이 액션마다 다르다는 사실이 이 표의 존재 이유의 절반이다.
_TABLE = {
    ("auto-resume", "autoresume"):
        ("set_autoresume", lambda a: {"value": onoff(a)}),
    ("auto-resume-message", "autoresume-message"):
        ("set_autoresume", lambda a: {"msg": " ".join(a)}),
    ("claude-usage", "usage", "refresh-usage"):
        ("refresh_usage", lambda a: {}),
    ("claude-token-sync",):
        ("token_sync", lambda a: {"sub": (a[0] if a else "status").strip().lower(),
                                  "arg": " ".join(a[1:]).strip()}),
    # ⚠ 이 액션은 **코어 명령표**(`servercmd._CMD_TABLE`)가 받는다 — 플러그인
    #    `server_command` 가 아니다. 그래서 "서버가 받나"를 플러그인 훅에만 물으면
    #    없는 것으로 보인다(2026-08-03 에 실제로 그렇게 오판했다). 액션을 어디가 받든
    #    이 표가 하는 일은 같다: 이름을 액션과 인자로 옮긴다.
    ("claude-token-account",):
        ("set_claude_account", lambda a: {"name": " ".join(a).strip()}),
    ("prompt-clear", "prompt-clear-mode"):
        ("set_prompt_clear", lambda a: {"value": onoff(a)}),
    ("auto-token-on-exit", "auto-token", "token-on-exit"):
        ("set_auto_token_on_exit", lambda a: {"value": onoff(a)}),
    ("claude-auto-redraw", "auto-redraw"):
        ("set_claude_auto_redraw", lambda a: {"value": redraw_arg(a)}),
    ("claude-resume-verify", "resume-verify"):
        ("set_claude_resume_verify", lambda a: {"value": verify_arg(a)}),
    ("auto-retry", "retry"):
        ("set_claude_auto_retry", lambda a: {"value": onoff(a)}),
    ("claude-auto-mode", "auto-mode"):
        ("set_claude_auto_mode", lambda a: {"value": onoff(a)}),
    ("claude-auto-yes", "auto-yes"):
        ("set_claude_auto_yes", lambda a: {"value": onoff(a)}),
    # ★ 종전에는 이 이름이 **정본에서도 죽어 있었다**(pytmux-35): 팔레트·선택지 팝업·
    #   외부 CLI 토글표에는 있는데 `handle_command` 의 사슬에 분기가 없어, `:auto-launch
    #   on` 은 "알 수 없는 명령"으로 끝났다. 전수로 재는 자가 없으면 이런 구멍은 안
    #   보인다 — 이 표가 그 자가 재는 자리다.
    ("auto-launch", "claude-auto-launch"):
        ("set_claude_auto_launch", lambda a: {"value": onoff(a)}),
    ("prompt-clear-message",):
        ("set_prompt_clear_message", lambda a: {"msg": " ".join(a).strip()}),
}

#: 인자에 따라 **액션 자체가 갈리는** 이름. 값은 `args → (액션, 인자) | None` 이고
#: `None` 은 *"이번엔 액션이 아니다"* = 서버가 화면 경로로 넘어간다.
_BRANCH = {
    ("prompt-clear-queue", "pc-queue"): pc_queue_arg,
}

_BY_NAME = {name: spec for names, spec in _TABLE.items() for name in names}
_BY_NAME_BRANCH = {name: fn for names, fn in _BRANCH.items() for name in names}


def to_action(name, args):
    """`(액션, 인자 dict)` — 이 표에 없는 이름이면 `None`.

    ⚠ `None` 은 **"내 것이 아니다"** 이지 실패가 아니다. 부르는 쪽은 다른 길(화면 스펙 ·
    클라 전용 팝업)로 넘어간다.
    """
    args = list(args or [])
    branch = _BY_NAME_BRANCH.get(name)
    if branch is not None:
        return branch(args)
    spec = _BY_NAME.get(name)
    if spec is None:
        return None
    action, mk = spec
    return action, mk(args)


def names():
    """이 표가 다루는 이름 전부(오라클이 전수를 재는 데 쓴다).

    갈리는 이름(`_BRANCH`)도 이 표의 것이다 — 무인자일 때 액션이 안 나올 뿐이다."""
    return sorted(set(_BY_NAME) | set(_BY_NAME_BRANCH))
