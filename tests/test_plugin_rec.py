"""REC(패널 출력 캡처) 플러그인 — 동작 + delete-to-disable 계약(docs/REC_SCENARIO.md).

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
    # 배지: capture ON → ` REC ` 세그먼트 + zone, 누적폭 +5.
    segs = []
    w = reg.client_statusbar(None, st, segs, 80, 10)
    assert w == 15 and st._rec_zone == (10, 15)
    assert any(getattr(s, "text", "") == " REC " for s in segs)
    # capture OFF → 배지·zone 없음, 폭 불변.
    st.capture = False
    segs2 = []
    w2 = reg.client_statusbar(None, st, segs2, 80, 10)
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
