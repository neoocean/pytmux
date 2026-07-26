"""`make_style` 이 **서버가 실제로 보내는 색 이름을 전부** 이해하는가.

# 왜 이 테스트가 생겼나

서버(`model._style_key`)는 색 이름을 pyte 계보로 보내는데, 그중 둘을 Rich 가 모른다:

- `bright_brown` — SGR 93/103(밝은 노랑)
- `bfightmagenta` — SGR 105(밝은 마젠타 배경, 원 pyte 오타를 vtconst 가 의도 보존)

종전 `make_style` 은 `Style(...)` 전체를 try 로 감싸고 실패하면 `Style(reverse, bold)`
로 떨어뜨렸다. 그래서 저 두 이름이 오면 **색뿐 아니라 기울임·밑줄·취소선까지 사라졌다**.
예외도 로그도 없어서 "가끔 색이 안 나온다"로만 드러났다. 밝은 노랑은 CLI 도구가 흔히
쓰는 색이라 실사용에서 보이는 자리였다.

# 이 테스트가 하는 일

색 이름을 손으로 나열하지 않는다 — **실제 Pane 에 SGR 을 먹여** 서버가 내놓는 이름을
받아 그것으로 검증한다. 그래서 서버가 색 이름 체계를 바꾸면 이 테스트가 따라온다.
"""
import harness  # noqa: F401 (경로 설정)
from pytmuxlib.clientutil import make_style
from pytmuxlib.model import Pane


def _emitted_style(params: str) -> dict:
    """SGR 파라미터를 실제로 먹여 서버가 내보내는 스타일 dict 를 얻는다."""
    pane = Pane(-1, -1, 20, 2)
    pane.feed(f"\x1b[{params}mX".encode())
    rows, _ = pane.render(True)
    for text, style in rows[0]:
        if text.startswith("X"):
            return style
    return {}


def _all_color_codes():
    """색을 지정하는 SGR 코드 전부(표준 8 + 밝은 8, 전경/배경)."""
    return [str(c) for c in
            list(range(30, 38)) + list(range(40, 48))
            + list(range(90, 98)) + list(range(100, 108))]


async def test_every_color_the_server_emits_survives_make_style():
    """서버가 색을 지정했으면 표시 스타일에도 색이 남아야 한다."""
    lost = []
    for code in _all_color_codes():
        emitted = _emitted_style(code)
        assert emitted, f"SGR {code} 가 스타일을 안 냈다(테스트 전제 붕괴)"
        st = make_style(emitted)
        if st.color is None and st.bgcolor is None:
            lost.append((code, emitted))
    assert not lost, (
        "색이 사라진 SGR 코드: "
        + ", ".join(f"{c}({e})" for c, e in lost))


async def test_bright_yellow_and_bright_magenta_specifically():
    """회귀의 진원지 세 코드를 이름으로 못박는다.

    서버 쪽 이름이 바뀌면 위 전수 테스트가 잡지만, 이 테스트는 **무엇이 문제였는지**를
    남긴다(전수 테스트만으로는 나중에 사람이 경위를 못 읽는다).
    """
    fg = make_style(_emitted_style("93"))
    assert fg.color is not None, "SGR 93 밝은 노랑 전경이 사라졌다"

    bg_yellow = make_style(_emitted_style("103"))
    assert bg_yellow.bgcolor is not None, "SGR 103 밝은 노랑 배경이 사라졌다"

    bg_magenta = make_style(_emitted_style("105"))
    assert bg_magenta.bgcolor is not None, "SGR 105 밝은 마젠타 배경이 사라졌다"


async def test_unknown_color_does_not_take_the_other_attributes_with_it():
    """알 수 없는 색이 와도 기울임·밑줄·취소선은 살아남아야 한다.

    이게 종전 폴백의 진짜 손해였다 — 색 하나 때문에 그 런의 서식이 통째로 날아갔다.
    """
    st = make_style({"f": "존재하지않는색", "it": 1, "un": 1, "st": 1, "bo": 1})
    assert st.color is None, "모르는 색은 포기한다"
    assert st.italic and st.underline and st.strike and st.bold, \
        "색 하나 때문에 다른 속성을 잃으면 안 된다"


async def test_one_bad_color_does_not_kill_the_other_one():
    """전경이 이상해도 배경은 남아야 한다(색끼리도 서로 독립)."""
    st = make_style({"f": "존재하지않는색", "b": "blue"})
    assert st.color is None
    assert st.bgcolor is not None, "멀쩡한 배경색까지 버리면 안 된다"


async def test_attributes_round_trip_without_colors():
    """색 없는 순수 속성 스타일도 그대로 통과한다."""
    for code, attr in (("1", "bold"), ("3", "italic"), ("4", "underline"),
                       ("7", "reverse"), ("9", "strike")):
        st = make_style(_emitted_style(code))
        assert getattr(st, attr), f"SGR {code} → {attr} 가 안 붙었다"


async def test_hex_colors_from_256_and_truecolor_paths():
    """256색·트루컬러는 #rrggbb 로 온다 — 이 경로도 색이 남아야 한다."""
    for params in ("38;5;196", "48;5;21", "38;2;18;52;86", "48;2;0;0;0"):
        emitted = _emitted_style(params)
        st = make_style(emitted)
        assert st.color is not None or st.bgcolor is not None, \
            f"SGR {params} 의 색이 사라졌다({emitted})"


async def test_empty_style_is_the_shared_default():
    """빈 스타일은 캐시된 기본값을 그대로 준다(핫패스 할당 회피)."""
    from pytmuxlib.clientutil import DEFAULT_STYLE
    assert make_style({}) is DEFAULT_STYLE
