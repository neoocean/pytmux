"""명령 한 줄 → **서버 액션 + 인자** — REC 몫(UI 무의존).

# 왜 여기인가 (pytmux-35)

`capture-output`·`capture-toggle` 는 팔레트에 보이는데 네이티브 클라에서 눌러도 아무
일이 없었다. 뿌리는 하나다: 클라가 플러그인 명령을 **전부** `plugin_open`("화면을
다오")으로 보내는데 이 둘은 화면이 아니라 **상태를 바꾸는** 명령이라, 서버가 *"이
플러그인은 화면 스펙을 제공하지 않습니다"* 로 거절했다.

서버는 `set_capture` 를 처음부터 받고 있었다(`_RecPlugin.server_command`). 없던 것은
**이름을 액션으로 옮기는 규칙**이고, 그것이 정본 클라 안에만 있었다
(`clientside.handle_command` 의 `if`). 네이티브 클라가 그것을 따로 알면 두 표가 갈리고,
갈린 순간 명령은 조용히 아무 일도 안 한다 — claude-code 가 먼저 낸 길(`cmdmap.py`)을
그대로 따른다.

두 소비자가 같은 표를 쓴다:

- 정본 — `clientside.handle_command` 가 여기서 액션을 얻어 `send_cmd` 한다.
- 서버 — 네이티브 클라가 보낸 `plugin_cmd {name, args}` 를 이 표로 풀어 디스패치한다.

# 여기에 무엇을 두지 않나

**알림은 못 나른다.** 캡처를 켜고 끌 때 정본이 띄우는 토스트는 클라의 일이라 이 표
밖에 남는다(claude-code cmdmap 과 같은 선).
"""
from __future__ import annotations

from pytmuxlib.plugins import onoff

#: 명령 이름(별칭 포함) → `(액션, 인자 만드는 함수)`.
_TABLE = {
    ("capture-output", "capture-toggle"):
        ("set_capture", lambda a: {"value": onoff(a)}),
}

_BY_NAME = {name: spec for names, spec in _TABLE.items() for name in names}


def to_action(name, args):
    """`(액션, 인자 dict)` — 이 표에 없는 이름이면 `None`(*"내 것이 아니다"*)."""
    spec = _BY_NAME.get(name)
    if spec is None:
        return None
    action, mk = spec
    return action, mk(list(args or []))


def names():
    """이 표가 다루는 이름 전부(오라클이 전수를 재는 데 쓴다)."""
    return sorted(_BY_NAME)
