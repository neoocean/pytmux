"""플러그인 표면(Tier A) — 서버가 부는 것이 정본이 아는 것과 **같은가**.

설계 = `docs/internal/PLUGIN_COMPAT_TEXTUAL_GUI_2026-08-01.md` §4.1 · §8-1.

# 왜 이 대조가 있어야 하나

플러그인이 기여하는 명령·메뉴 줄·설정은 **파이썬 자료구조**라, 정본 클라는 자기 프로세스
안에서 훅을 바로 부른다. 네이티브 GUI 는 파이썬을 못 읽으므로 서버가 같은 자료를 실어
줘야 하는데 — **두 경로가 생기는 순간 갈라질 수 있다.** 한쪽만 늘어난 상태는 "GUI 에는
그 명령이 없다"로만 보이고, 그건 사용자가 신고하기 전에는 아무도 모른다.

그래서 여기서 재는 것은 값 하나하나가 아니라 **집합의 동치**다.

# delete-to-disable 도 여기서 잰다

플러그인 디렉토리를 지우면 그 기여가 **두 프레임 모두에서** 사라져야 한다. 정본에서만
사라지면 GUI 는 없는 기능의 입구를 계속 보여 준다 — 코어가 플러그인을 직접 import 하지
않는다는 계약이 GUI 에서만 거짓이 되는 것이다.
"""

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown
from pytmuxlib.model import ClientConn


def _surface(srv):
    return srv._plugin_surface()


async def test_the_surface_matches_what_the_canonical_client_would_call():
    """서버가 부는 표면 == 정본이 훅을 직접 불러 얻는 것."""
    srv, task, sock = await server_only()
    try:
        surface = _surface(srv)
        reg = srv.plugins
        # 명령: 이름 집합이 같아야 한다(설명·범주는 아래에서 값까지 본다).
        assert {c["name"] for c in surface["commands"]} == {n for n, _d, _c in reg.commands}
        # 값까지 — 설명이 갈리면 팔레트에서 다른 글이 보인다.
        assert {(c["name"], c["desc"], c["cat"]) for c in surface["commands"]} == {
            (n, d, c) for n, d, c in reg.commands
        }
        assert set(surface["noarg"]) == set(reg.noarg)
        assert {(m["key"], m["label"]) for m in surface["menu_items"]} == set(reg.menu_items)
        descs, cats = reg.settings()
        assert surface["settings"] == list(descs)
        assert surface["setting_cats"] == list(cats)
    finally:
        await teardown(srv, task, sock)


async def test_the_surface_is_not_empty_on_a_stock_tree():
    """빈 표면은 통과가 아니라 고장이다.

    플러그인이 하나도 안 실렸는데 위 동치 테스트는 `set() == set()` 으로 **통과한다** —
    이 저장소가 여러 번 밟은 '빈 결과를 통과로 읽는' 자리다. 기본 트리에는 플러그인이
    열댓 개 있고 그중 여럿이 명령을 기여하므로, 여기서 0 이면 로더가 죽은 것이다.
    """
    srv, task, sock = await server_only()
    try:
        surface = _surface(srv)
        assert surface["commands"], "플러그인 명령이 하나도 없다 — 로더를 볼 것"
        assert surface["menu_items"], "플러그인 메뉴 줄이 하나도 없다"
    finally:
        await teardown(srv, task, sock)


async def test_only_clients_that_advertise_get_the_surface():
    """광고 안 한 클라의 프레임은 **한 바이트도** 안 달라진다.

    정본 클라는 이 키를 안 읽는다(자기 레지스트리를 쓴다). 안 읽는 클라에게 실어 보내면
    대역폭만 쓰고, 더 나쁘게는 "둘 중 어느 것이 진짜인가"가 생긴다.
    """
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        plain = ClientConn(None)
        plain.caps = set()
        native = ClientConn(None)
        native.caps = {"plugin_surface"}
        assert "plugin_surface" not in srv._status_msg(sess, full=True, client=plain)
        assert "plugin_surface" in srv._status_msg(sess, full=True, client=native)
        # full 이 아니면 안 싣는다 — 목록은 서버가 도는 동안 안 변한다.
        assert "plugin_surface" not in srv._status_msg(sess, full=False, client=native)
    finally:
        await teardown(srv, task, sock)


async def test_deleting_a_plugin_removes_it_from_both_paths():
    """delete-to-disable 왕복 — 끄면 **표면과 정본 양쪽에서** 함께 사라진다.

    실제 디렉토리를 지우는 대신 레지스트리의 `set_disabled` 로 같은 상태를 만든다(그것이
    플러그인 관리 화면이 쓰는 길이고, 로더가 거르는 지점도 같다).
    """
    srv, task, sock = await server_only()
    try:
        reg = srv.plugins
        # 명령을 기여하는 플러그인 하나를 고른다 — 이름을 박아 두지 않는다(표가 바뀌면
        # 자리를 박아 둔 오라클이 낡는다는 이 저장소의 규칙).
        target = None
        for name, _desc, _cat, _enabled in reg.plugin_overview():
            before = {c["name"] for c in _surface(srv)["commands"]}
            reg.set_disabled([name])
            after = {c["name"] for c in _surface(srv)["commands"]}
            reg.set_disabled([])
            if before - after:
                target = (name, before - after)
                break
        assert target, "명령을 기여하는 플러그인을 못 찾았다 — 이 판으로는 못 잰다"
        name, gone = target
        reg.set_disabled([name])
        try:
            surface_names = {c["name"] for c in _surface(srv)["commands"]}
            canon_names = {n for n, _d, _c in reg.commands}
            assert not (gone & surface_names), f"{name} 을 껐는데 표면에 남았다: {gone}"
            assert not (gone & canon_names), f"{name} 을 껐는데 정본 경로에 남았다"
            # ★ 그리고 **둘이 여전히 같다** — 한쪽만 사라지는 것이 진짜 위험이다.
            assert surface_names == canon_names
        finally:
            reg.set_disabled([])
    finally:
        await teardown(srv, task, sock)
