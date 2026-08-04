"""명령 한 줄 → **서버 액션 + 인자** — 프롬프트 히스토리 몫(UI 무의존).

# 왜 여기인가 (pytmux-35)

`prompt-history-lines` 는 팔레트에 보이는데 네이티브 클라에서 눌러도 아무 일이 없었다.
서버는 `set_ph_max_lines` 를 처음부터 받는다 — 없던 것은 **이름을 액션으로 옮기는
규칙**이고 그것이 정본 클라 안에만 있었다(`handle_command` 의 `if`). claude-code·rec 가
먼저 낸 길을 그대로 따른다.

# 무인자는 **순환**이다 (행동 하나가 바뀐다)

종전 정본은 숫자가 없으면 `True` 만 돌려주고 **아무 일도 안 했다** — 명령은 잡히는데
결과가 없는, 이 결함이 사용자에게 보이던 모양 그대로다. 이제 무인자는 다음 값으로
순환한다(1→2→3→1). 같은 표의 3-state 토글(`claude-auto-redraw`·`claude-resume-verify`)
이 이미 *"무인자 = 서버가 순환"* 이고, 팔레트의 선택지 팝업은 여전히 1·2·3 을 직접
고르므로 손버릇은 안 바뀐다.
"""
from __future__ import annotations


def lines_arg(args):
    """낱말 목록에서 행수를 읽는다 — 숫자가 없으면 `None`(**서버가 순환**).

    `1-3` 범위 밖의 수도 그대로 넘긴다: 자르는 것은 서버의 일이고(`_clamp_lines`),
    여기서 한 번 더 자르면 같은 규칙이 두 벌이 된다."""
    for a in args:
        s = str(a).strip()
        if s.lstrip("-").isdigit():
            return int(s)
    return None


#: 명령 이름(별칭 포함) → `(액션, 인자 만드는 함수)`.
_TABLE = {
    ("prompt-history-lines", "ph-lines"):
        ("set_ph_max_lines", lambda a: {"n": lines_arg(a)}),
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
