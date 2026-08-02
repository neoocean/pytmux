"""REC(패널 출력 캡처) 플러그인 — 동작 + delete-to-disable 계약(docs/internal/REC_SCENARIO.md).

REC 서버 본체는 `plugins/rec/` 로 추출됐다. 디렉토리를 지우면(=registry 에서 제외):
캡처 명령·서버 믹스인·PTY 출력 훅·status 필드가 전부 사라지고 코어는 바이트를 그냥
흘려보낸다(기록 안 함). 코어 잔류는 토큰 DB 가 쓰는 `_capture_id`/`PROJECT_DIR` 뿐.
"""
import json
import os
import tempfile

from harness import server_only, teardown
from pytmuxlib import plugins


def _registry_without_rec():
    """rec 플러그인을 뺀 Registry — 디렉토리 삭제(delete-to-disable)와 동치."""
    found = plugins._discover()
    return plugins.Registry([p for p in found
                             if getattr(p, "name", "") != "rec"])


def _registry_only_rec():
    found = plugins._discover()
    return plugins.Registry([p for p in found
                             if getattr(p, "name", "") == "rec"])


# ── 동작(플러그인 존재) ──────────────────────────────────────────────────────
async def test_rec_capture_default_off_and_toggle(tmp_path=None):
    """기본 OFF(깃헙 배포 F4) + 토글 ON 시 무손실 기록 + plugin_opts 영속."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        # rec 플러그인이 server_init/server_opts_init 로 상태·플래그를 설치한다.
        assert hasattr(srv, "_capfiles") and hasattr(srv, "capture")
        assert srv.capture is False, "기본 OFF(opts 미설정 시, F4)"
        # PTY 출력 훅: OFF 면 기록 안 함.
        srv.plugins.server_pty_output(srv, pane, b"while-off")
        assert pane.id not in srv._capfiles, "OFF 중엔 파일 미생성"
        # 토글 ON → 훅이 기록.
        assert srv.set_capture(True) is True
        srv.plugins.server_pty_output(srv, pane, b"hello-rec")
        path = srv._cappaths[pane.id]
        with open(path, "rb") as f:
            assert f.read() == b"hello-rec", "무손실 캡처"
        assert json.load(open(srv.opts_path))["plugin_opts"]["capture"] is True
    finally:
        if hasattr(srv, "_close_all_capfiles"):
            srv._close_all_capfiles()
        import shutil
        shutil.rmtree(getattr(srv, "capture_dir", "/nonexistent"),
                      ignore_errors=True)
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_rec_capture_rotates_at_size_cap():
    """단일 캡처 파일 50MB 상한(요청 2026-06-16): 누적이 상한을 넘으면 새 파일로
    로테이션해 한 로그가 폭주(실측 318MB)하지 않는다. 상한 이내엔 같은 파일 유지."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        srv.set_capture(True)
        srv._CAPTURE_CAP = 10                       # 인스턴스 한정 작은 상한
        srv.plugins.server_pty_output(srv, pane, b"AAAAA")   # 5
        first = srv._cappaths[pane.id]
        srv.plugins.server_pty_output(srv, pane, b"BBBBB")   # 누적 10(상한 이내)
        assert srv._cappaths[pane.id] == first, "상한 이내엔 같은 파일"
        srv.plugins.server_pty_output(srv, pane, b"CCCCCC")  # 10+6=16>10 → 로테이션
        second = srv._cappaths[pane.id]
        assert second != first, "상한 초과 → 새 파일로 로테이션"
        with open(first, "rb") as f:
            assert f.read() == b"AAAAABBBBB"         # 첫 세그먼트 = 상한까지
        with open(second, "rb") as f:
            assert f.read() == b"CCCCCC"             # 새 세그먼트 = 이후 출력
        import glob
        logs = glob.glob(os.path.join(srv.capture_dir, f"*_p{pane.id}.log"))
        assert len(logs) == 2, logs                  # 정확히 두 세그먼트
    finally:
        if hasattr(srv, "_close_all_capfiles"):
            srv._close_all_capfiles()
        import shutil
        shutil.rmtree(getattr(srv, "capture_dir", "/nonexistent"),
                      ignore_errors=True)
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


async def test_rec_status_fields_present_when_on():
    """server_status 훅이 capture/capture_path/capture_size 를 채운다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        win = sess.active_window
        srv.set_capture(True)
        msg = {"windows": [{}]}
        srv.plugins.server_status(srv, sess, win, msg, True)
        assert msg.get("capture") is True
        assert "capture_path" in msg and "capture_size" in msg
    finally:
        if hasattr(srv, "_close_all_capfiles"):
            srv._close_all_capfiles()
        import shutil
        shutil.rmtree(getattr(srv, "capture_dir", "/nonexistent"),
                      ignore_errors=True)
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)


# ── delete-to-disable 계약(rec 제외) ────────────────────────────────────────
async def test_rec_present_sanity():
    """전제: 정상 상태에선 rec 가 캡처 명령·서버 믹스인을 실제로 기여한다."""
    reg = _registry_only_rec()
    names = {n for (n, *_rest) in reg.commands}
    assert "capture-output" in names, names
    assert "ServerRecMixin" in {c.__name__ for c in reg.server_mixins()}


async def test_rec_disabled_removes_all_touchpoints():
    """rec 디렉토리 삭제 동치 — 캡처 명령·믹스인·PTY 훅·status 필드가 전부 사라지고
    코어가 깨지지 않는다(예외 없음)."""
    reg = _registry_without_rec()
    # ① 캡처 명령이 검색·자동완성·옵션 어디에도 없다.
    names = {n for (n, *_rest) in reg.commands}
    assert not ({"capture-output", "capture-toggle"} & names), names
    assert not ({"capture-output", "capture-toggle"} & reg.noarg)
    assert "capture-output" not in reg.command_options
    # ② 서버측 믹스인에 ServerRecMixin 없음(캡처 메서드가 Server 에서 빠진다).
    assert "ServerRecMixin" not in {c.__name__ for c in reg.server_mixins()}
    # ③ rec 가 plugins 목록에 없으니 server_status/server_command 가 capture 를
    #    기여할 길이 없다(이 두 훅은 claude-code 도 구현하므로 None 서버로 직접 호출하면
    #    claude 가 실서버를 기대해 깨진다 — 부재는 목록으로 확인한다).
    assert not any(getattr(p, "name", "") == "rec" for p in reg.plugins)
    # ④ server_pty_output / server_shutdown 은 **rec 만** 구현 → rec 부재 시 빈 루프라
    #    server=None 으로 호출해도 진짜 no-op(코어가 바이트를 그냥 흘려보냄).
    reg.server_pty_output(None, None, b"bytes")   # 예외 없음
    reg.server_shutdown(None)                      # 예외 없음
    # ⑤ server_opts_serialize 에 capture 키가 빠진다(rec 소유 opt).
    class _S:
        pass
    assert "capture" not in reg.server_opts_serialize(_S())


# ── 클라 표시(배지·팝업탭·흡수) ─────────────────────────────────────────────
async def test_rec_client_badge_init_and_tab_present():
    """rec present: client_statusbar_init 가 capture 필드를 설치하고, client_statusbar
    가 ` REC ` 배지+클릭존을 그리며, client_status_tabs 가 REC 탭(+동작)을 기여한다."""
    reg = _registry_only_rec()   # rec 만 → claude 훅이 fake status 로 안 깨짐

    class _St:
        pass
    st = _St()
    reg.client_statusbar_init(None, st)
    assert st.capture is False and st._rec_zone is None
    assert st.capture_path is None and st.capture_size == 0
    # 흡수: capture* 필드 반영.
    reg.client_statusbar_update(None, st, {"capture": True,
                                           "capture_path": "/t/p.log",
                                           "capture_size": 42})
    assert st.capture is True and st.capture_path == "/t/p.log"
    # 배지: capture ON → ` REC ` 세그먼트 + zone, 누적폭 +5(시스템 배지 영역 훅).
    segs = []
    w = reg.client_statusbar_badges(None, st, segs, 80, 10)
    assert w == 15 and st._rec_zone == (10, 15)
    assert any(getattr(s, "text", "") == " REC " for s in segs)
    # capture OFF → 배지·zone 없음, 폭 불변.
    st.capture = False
    segs2 = []
    w2 = reg.client_statusbar_badges(None, st, segs2, 80, 10)
    assert w2 == 10 and not segs2 and st._rec_zone is None
    # 팝업 탭: (제목, 줄, 동작) 3-튜플.
    class _App:
        def __init__(self):
            self.status = _St()
            self.status.capture = True
            self.status.capture_path = "/t/p.log"
            self.status.capture_size = 42
    t = reg.client_status_tabs(_App(), {"sessions": []})[0]
    assert t[0] == "출력 캡처(REC)" and len(t) == 3 and len(t[2]) == 2


async def test_rec_disabled_client_noop():
    """rec absent: 클라 표시 훅이 capture 를 안 만든다(코어 무크래시). client_status_tabs
    는 rec 만 구현하므로 REC 탭이 통째로 빠진다."""
    reg = _registry_without_rec()

    class _St:
        pass
    st = _St()
    reg.client_statusbar_init(None, st)        # 예외 없음(claude 가 자기 필드만 설치)
    assert not hasattr(st, "capture") and not hasattr(st, "_rec_zone"), \
        "rec 부재인데 capture 필드 설치됨"
    # client_status_tabs 는 이제 rec 만 구현 → rec 부재 시 REC 탭 없음(None 도 무탈).
    tabs = reg.client_status_tabs(None, {"sessions": []})
    assert not any(t[0] == "출력 캡처(REC)" for t in tabs)
    # rec 가 plugins 목록에 없으니 client_statusbar 배지를 그릴 길이 없다.
    assert not any(getattr(p, "name", "") == "rec" for p in reg.plugins)


# ── 상태줄 기여를 **자료로** (Tier B ③ · P6) ────────────────────────────────

async def test_the_rec_badge_also_goes_out_as_data():
    """정본이 그리는 배지와 **같은 배지**가 네이티브 클라에게는 자료로 간다.

    종전에는 이 배지가 파이썬 훅(`client_statusbar_badges`)뿐이라 GUI 에는 REC 가
    아예 없었다(`base::chrome` 이 "저쪽의 rec 은 플러그인이 채우는 칸이라 우리에게는
    없다"고 적어 둔 그 자리). 이제 서버가 `status.plugin_badges` 로 준다.

    값의 출처는 **`server_status` 가 방금 채운 그 필드**다 — 같은 것을 두 번 계산하면
    두 클라가 다른 것을 보는 자리가 하나 생긴다."""
    reg = _registry_only_rec()
    # 캡처 중이면 배지 하나, 이름이 찍혀 있고, 색은 **이름**이다(hex 금지).
    got = reg.plugin_badges(None, None, {"capture": True})
    assert len(got) == 1, got
    b = got[0]
    assert b["name"] == "rec", b
    assert b["text"] == " REC ", b
    assert b["theme"] == {"b": "error"}, b
    for v in b["theme"].values():
        assert not v.startswith("#"), f"hex 가 실렸다: {b}"
    # 정본이 그리는 글자와 **같은 문자열**이라야 한다(두 클라가 다른 낱말을 보면 안 된다).
    class _St:
        pass
    st = _St()
    reg.client_statusbar_init(None, st)
    reg.client_statusbar_update(None, st, {"capture": True})
    segs = []
    reg.client_statusbar_badges(None, st, segs, 80, 0)
    assert [s.text for s in segs] == [b["text"]], (segs, b)
    # 캡처가 아니면 아무것도 안 낸다 — 서버가 키를 빼야 배지가 사라진다.
    assert reg.plugin_badges(None, None, {"capture": False}) == []
    assert reg.plugin_badges(None, None, {}) == []


async def test_the_status_carries_the_badge_only_while_capturing():
    """서버 status 에 **실제로** 실리는가, 그리고 **끄면 키가 빠지는가**.

    ★ 여기가 조용히 틀리기 쉬운 자리다: 네이티브 클라는 "안 오면 없는 것"으로 읽으므로
    (`SessionState::plugin_badges` 파싱부), 서버가 끈 뒤에도 키를 계속 실으면 REC 가
    영영 남는다. 그래서 **켠 판과 끈 판을 둘 다** 잰다."""
    srv, task, sock = await server_only()
    try:
        sess = srv.ensure_default_session(80, 24)
        msg = srv._status_msg(sess)
        assert "plugin_badges" not in msg, "캡처 전인데 배지가 실렸다"
        # 캡처 중으로 만든 뒤 다시 만든다. ★ 필드 이름은 `server.capture` 다
        # (`rec.server_status` 가 그것을 읽는다) — 틀린 이름을 세우면 이 테스트는
        # **조용히 공허해진다**(캡처가 안 켜지니 아래 단언이 전부 건너뛰어진다).
        # 그래서 켜졌다는 것을 먼저 단언한다(실제로 한 번 그렇게 썼다).
        srv.capture = True
        msg2 = srv._status_msg(sess)
        assert msg2.get("capture") is True, \
            "캡처를 못 켰다 — 이 테스트가 아무것도 안 재고 있다"
        assert msg2.get("plugin_badges"), "캡처 중인데 배지가 안 실렸다"
        assert msg2["plugin_badges"][0]["text"] == " REC "
        assert msg2["plugin_badges"][0]["name"] == "rec"
        srv.capture = False
        assert "plugin_badges" not in srv._status_msg(sess), \
            "캡처를 껐는데 배지 키가 남았다 — 네이티브 클라에 REC 가 영영 남는다"
    finally:
        await teardown(srv, task, sock)
