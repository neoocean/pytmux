"""`NO_COLOR` 상자에서 Textual 이 렌더 중 죽는 것을 막는 안전판(§10-14).

상류 결함: `textual.filter.monochrome_style(style)` 이 `style.color` 를 **가드 없이**
읽는데 Rich `Segment.style` 은 `Optional[Style]` 이라 `None` 이 정상값이다. 그 변수를
켠 상자에서 `Monochrome.apply` 가 `AttributeError` 로 터지고, 앱 **초기화** 경로라
사용자 클라가 뜨다가 죽는다(실측 `alienware`: `test_client` 16 passed / 256 failed,
변수만 지우면 272 passed). upstream(8.2.8·main, 확인 2026-07-28) 미수정.

처방 = 사용자 결정 ⒟ — **`NO_COLOR` 는 존중한 채**(색은 계속 빠진다) 그 필터만
None-안전판으로 교체. Textual 내부(`app._filters`)에 기대므로 업그레이드가 계약을
깨면 여기서 잡는다.

되돌리면 실패해야 하는 것:
  · `clientutil.harden_no_color_filters` 의 교체 루프를 빼면 → 아래 3개 중 2개 실패
  · `client.py::PytmuxApp.__init__` 의 **호출 한 줄**을 지우면 →
    test_client_app_installs_the_guard 실패(헬퍼만 단언하면 공허 통과한다)
"""
import os

import harness  # noqa: F401
from run import skip

from pytmuxlib.clientutil import harden_no_color_filters


def _none_style_segments():
    """스타일이 `None` 인 세그먼트 — Rich 가 정상값으로 만들어 내는 모양."""
    from rich.segment import Segment
    return [Segment("mascot", None, None)]


def _black():
    from textual.color import Color
    return Color(0, 0, 0)


async def test_upstream_monochrome_still_crashes_on_none_style():
    """대조군 — 상류가 실제로 터지는가. 안 터지면 안전판은 무해한 no-op 이다.

    이 대조군이 없으면 아래 두 테스트가 '아무 일도 안 하는 코드'를 통과시킬 수 있다.
    """
    from textual.filter import Monochrome

    try:
        Monochrome().apply(_none_style_segments(), _black())
    except AttributeError:
        return                      # 결함이 여전히 있다 — 안전판이 필요하다
    skip("upstream 이 고쳤다 — 안전판은 이제 무해한 no-op(버전 핀 검토 가능)")


async def test_guard_replaces_monochrome_and_survives_none_style():
    """안전판이 필터를 교체하고, 교체된 필터는 `None` 스타일을 통과시킨다."""
    from textual.filter import Monochrome

    class _App:                      # App 전체를 세우지 않고 필터 목록만 흉내
        def __init__(self):
            self._filters = [Monochrome()]

    app = _App()
    assert harden_no_color_filters(app) == 1, "교체가 일어나지 않았다"
    f = app._filters[0]
    assert type(f) is not Monochrome and isinstance(f, Monochrome), \
        "상류 클래스를 그대로 두거나 관계 없는 것으로 바꿨다"
    out = f.apply(_none_style_segments(), _black())          # 터지면 실패
    assert [s.text for s in out] == ["mascot"], out
    # 멱등: 다시 불러도 감싸지 않는다(재진입·재접속 경로에서 중첩 방지)
    assert harden_no_color_filters(app) == 0

    # 색이 있는 세그먼트는 **여전히 단색화**된다 — NO_COLOR 를 존중한다는 계약.
    from rich.segment import Segment
    from rich.style import Style
    colored = f.apply([Segment("x", Style(color="red"), None)], _black())
    assert colored[0].style is not None and colored[0].style.color is not None
    assert colored[0].style.color.name != "red", (
        "단색화가 사라졌다 — 안전판이 NO_COLOR 를 무력화했다")


async def test_client_app_installs_the_guard():
    """호출부 오라클 — 실제 클라 App 이 만들어질 때 안전판이 걸린다.

    헬퍼만 단언하면 `PytmuxApp.__init__` 의 호출 한 줄을 지워도 통과한다(이 저장소가
    반복해서 밟은 공허 통과). 그래서 **진짜 App 을 그 변수와 함께** 만든다.
    """
    from textual.filter import Monochrome

    had = os.environ.get("NO_COLOR")
    os.environ["NO_COLOR"] = "1"
    try:
        app = harness.make_app("/tmp/pytmux-no-color-guard.sock")
    finally:
        if had is None:
            os.environ.pop("NO_COLOR", None)
        else:
            os.environ["NO_COLOR"] = had

    filters = list(getattr(app, "_filters", []))
    mono = [f for f in filters if isinstance(f, Monochrome)]
    assert mono, "NO_COLOR 를 켰는데 Monochrome 필터가 없다 — 전제가 바뀌었다"
    assert not [f for f in mono if type(f) is Monochrome], (
        "상류 Monochrome 이 그대로 남아 있다 — 안전판 호출이 빠졌다")
    for f in mono:
        f.apply(_none_style_segments(), _black())            # 터지면 실패
