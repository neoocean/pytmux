"""명령 한 줄 → **서버 액션 + 인자** — IME 배지 몫(UI 무의존).

# 왜 여기인가 (pytmux-35)

`ime-indicator` 는 팔레트에 보이는데 네이티브 클라에서 눌러도 아무 일이 없었다. 다른
죽은 줄과 뿌리는 같지만(클라가 플러그인 명령을 전부 `plugin_open` 으로 보낸다) 여기엔
사정이 하나 더 있었다: **끄고 켜는 값이 정본 클라의 인스턴스 속성**(`app.ime_show`)이라
서버에 그 값 자체가 없었다. 그래서 "서버가 대신 하라"고 할 대상도 없었다.

# 그래서 값을 서버로 옮겼다

배지를 그리는 쪽은 이미 둘이다 — 정본은 `client_render` 로 자기가 그리고, 네이티브
클라의 그림은 **서버가** `plugin_cells` 로 만든다. 값이 클라마다 따로면 같은 서버에
붙은 두 화면이 서로 다른 상태가 되고, 네이티브 클라 쪽은 끌 방법 자체가 없다.

값이 서버 옵션이 되면 셋이 한꺼번에 풀린다: ⑴ 명령이 두 클라에서 똑같이 먹고
⑵ 껐다 켠 것이 재시작 뒤에도 남고(`opts.json`) ⑶ 배지를 안 그리는 판정이 한 곳이 된다.

⚠ 한/영 **상태**(한/EN)는 여전히 클라의 것이다(OS 가 그 창에만 알려 준다 — 설계 Tier D).
서버로 옮긴 것은 *보일까 말까* 하나뿐이다.
"""
from __future__ import annotations

from pytmuxlib.plugins import onoff

#: 명령 이름(별칭 포함) → `(액션, 인자 만드는 함수)`.
_TABLE = {
    ("ime-indicator", "ime"):
        ("set_ime_indicator", lambda a: {"value": onoff(a)}),
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
