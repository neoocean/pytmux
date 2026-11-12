"""pytmux-637 — `status-position top` 에서 상태줄이 탭바를 덮던 정본 결함의 회귀 오라클.

종전에는 탭바와 상태줄이 둘 다 `dock: top` 이 되어 Textual 이 같은 자리에 겹쳐 놓았고,
뷰 높이(24−N)와 서버에 보고하는 행 수(23−N)가 한 줄 어긋났다. 이제 셋은 도킹 없이 세로
흐름으로 쌓이고(`PytmuxApp._apply_status_position`), 그 배치의 표는 순수 함수
`clientutil.chrome_bands` 한 벌이다 — GUI 는 같은 표를 픽스처로 받아 자기 차례와 맞댄다
(`client/scripts/gen_chrome_bands.py`).

되돌리면 실패해야 하는 오라클:
  · `move_child` 를 빼면(= 종전 도킹)        → test_bands_match_the_real_widget_regions
  · `chrome_bands` 의 차례를 뒤집으면          → test_the_tab_bar_sits_right_above_the_view
"""
import harness  # noqa: F401  (러너 위생 · 경로 설정)
from harness import make_app, server_only, teardown, wait_until

from pytmuxlib.clientutil import chrome_bands

H = 24


def test_the_tab_bar_sits_right_above_the_view():
    # 노트북 연결부(뷰 0행을 탭 색으로 잇는 `▀`)는 탭바가 뷰 바로 위일 때만 성립한다.
    for pos in ("bottom", "top"):
        for n in range(0, 6):
            b = chrome_bands(H, True, pos, n)
            ty, th = b["tabbar"]
            vy, _vh = b["view"]
            assert ty + th == vy, (pos, n, b)
            if pos == "top" and n:
                assert b["order"] == ["status", "tabbar", "view"], b
            # 겹치지 않고 화면을 정확히 채운다.
            spans = [b[name] for name in b["order"]]
            ys = [y for y, _ in spans]
            assert ys == sorted(ys), b
            assert sum(h for _, h in spans) == H, b
            assert b["view"][1] == b["report_rows"], b


async def test_bands_match_the_real_widget_regions():
    """★ 호출부 — 실제 앱의 위젯 자리가 표와 같고, 뷰 높이 == 보고 행 수(2×2×0..5)."""
    srv, task, sock = await server_only()
    app = make_app(sock, {})
    try:
        async with app.run_test(size=(80, H)) as pilot:
            assert await wait_until(pilot, lambda: getattr(app, "writer", None) is not None,
                                    timeout=8)

            def region(w):
                return (w.region.y, w.region.height) if w.display else None

            for tabbar in (False, True):
                for pos in ("bottom", "top"):
                    for n in range(0, 6):
                        app.set_tab_bar_always(tabbar)
                        app.apply_option("status-position", pos)
                        app.apply_option("status", str(n))
                        want = chrome_bands(H, app._tabbar_visible(), pos, n)

                        def got():
                            return {"tabbar": region(app.tabbar),
                                    "status": region(app.status),
                                    "view": region(app.view)}

                        def settled():
                            g = got()
                            return all(g[k] == want[k] for k in ("tabbar", "status", "view"))

                        ok = await wait_until(pilot, settled, timeout=4)
                        assert ok, (tabbar, pos, n, got(), want)
                        assert app.view.region.height == app._content_size()[1], \
                            (tabbar, pos, n, app.view.region, app._content_size())
    finally:
        await teardown(srv, task, sock)
