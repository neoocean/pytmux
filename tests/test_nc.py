"""nc(Norton Commander 풍 디렉토리 트리) 기능 테스트.

서버 측: `_list_dirs`(직계 디렉토리만·정렬·graceful)와 `nc_list_msg`(활성 패널 cwd
루트 폴백·경로 echo). 클라 측 모달/배선 테스트는 test_client.py(Textual run_test)에
둔다 — 여기서는 IPC 없이 서버 로직을 직접 호출해 검증한다(test_server 관례)."""
import os
import tempfile

import harness
from harness import server_only, teardown


def _make_tree(root):
    """root 아래에 디렉토리/파일/숨김을 만들어 _list_dirs 검증용 픽스처를 세운다."""
    os.makedirs(os.path.join(root, "alpha"))
    os.makedirs(os.path.join(root, "Beta"))      # 대소문자 정렬 확인용
    os.makedirs(os.path.join(root, "gamma"))
    os.makedirs(os.path.join(root, ".hidden"))   # 숨김 → 제외
    os.makedirs(os.path.join(root, "alpha", "child"))
    with open(os.path.join(root, "afile.txt"), "w") as f:
        f.write("x")                              # 파일 → 제외


async def test_nc_list_dirs_only_sorted_no_hidden():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            dirs = srv._list_dirs(root)
            names = [os.path.basename(p) for p in dirs]
            # 디렉토리만, 이름순(대소문자 무시), 숨김·파일 제외
            assert names == ["alpha", "Beta", "gamma"], names
            assert all(os.path.isabs(p) for p in dirs), "절대경로 반환"
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_subpath():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            sub = os.path.join(root, "alpha")
            dirs = srv._list_dirs(sub)
            assert [os.path.basename(p) for p in dirs] == ["child"]
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_empty_and_missing_graceful():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            assert srv._list_dirs(root) == []                       # 빈 디렉토리
        assert srv._list_dirs("/no/such/path/xyzzy") == []          # 없는 경로
        # 파일을 경로로 줘도(디렉토리 아님) 예외 없이 빈 리스트
        with tempfile.NamedTemporaryFile() as f:
            assert srv._list_dirs(f.name) == []
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_msg_root_uses_active_pane_cwd():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            sess = srv.ensure_default_session(80, 24)
            # 활성 패널 cwd 를 픽스처 루트로 고정(실제 프로세스 cwd 추정 대신 주입).
            srv._pane_cwd = lambda pane, _r=root: _r
            msg = srv.nc_list_msg(sess, None)
            assert msg["t"] == "nc_list"
            assert msg["path"] is None, "초기 루트 목록은 path=None(화면 열기 신호)"
            assert os.path.abspath(msg["root"]) == os.path.abspath(root)
            assert [os.path.basename(p) for p in msg["dirs"]] == \
                ["alpha", "Beta", "gamma"]
    finally:
        await teardown(srv, task, sock)


async def test_nc_list_msg_subpath_echoes_path():
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as root:
            _make_tree(root)
            sess = srv.ensure_default_session(80, 24)
            sub = os.path.join(root, "alpha")
            msg = srv.nc_list_msg(sess, sub)
            # 펼치기 응답은 요청 노드 절대경로를 echo 해 클라가 노드를 매칭한다.
            assert os.path.abspath(msg["path"]) == os.path.abspath(sub)
            assert os.path.abspath(msg["root"]) == os.path.abspath(sub)
            assert [os.path.basename(p) for p in msg["dirs"]] == ["child"]
    finally:
        await teardown(srv, task, sock)
