"""§10-E #1: PYTMUX_HOME 단일 디렉토리 통합(opt-in) — 서버 상태(소켓/state)·클라
config·토큰 DB·captures 가 한 디렉토리 아래로 모이고, 미설정 시 종전 거동(무변경)·
기존 config 1회 복사(원본 보존) 마이그레이션을 검증한다."""
import os
import tempfile

import harness  # noqa: F401  (경로 설정)
from harness import server_only, teardown
from pytmuxlib import ipc, keymap


class _Env:
    """os.environ 키들을 임시로 세팅하고 컨텍스트 종료 시 원복(테스트 격리)."""
    def __init__(self, **kw):
        self._kw = kw
        self._saved = {}

    def __enter__(self):
        for k, v in self._kw.items():
            self._saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, old in self._saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


async def test_pytmux_home_resolves_and_unset_default():
    """pytmux_home(): 설정되면 abspath(expanduser), 미설정이면 None(종전 거동)."""
    with _Env(PYTMUX_HOME=None):
        assert ipc.pytmux_home() is None
    with tempfile.TemporaryDirectory() as d:
        with _Env(PYTMUX_HOME=d):
            assert ipc.pytmux_home() == os.path.abspath(d)
        # 상대경로도 abspath 로 고정(cwd 무관 일관)
        with _Env(PYTMUX_HOME="./relhome"):
            assert os.path.isabs(ipc.pytmux_home())
            assert ipc.pytmux_home().endswith("relhome")


async def test_state_dir_and_endpoint_under_home():
    """PYTMUX_HOME 설정 시 런타임은 <home>/state, 소켓·후보가 그 아래 하나로 통일."""
    if os.name == "nt":
        return
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "pthome")
        with _Env(PYTMUX_HOME=home):
            state = os.path.join(os.path.abspath(home), "state")
            assert ipc.default_state_dir() == state
            ep = ipc.default_endpoint()
            assert ep == os.path.join(state, "default.sock")
            # 통합 시 XDG/tmp 이중 후보 없이 그 소켓 하나가 canonical
            assert ipc.default_endpoint_candidates() == [ep]
            assert os.path.isdir(state), "state/ 디렉토리 생성됨"


async def test_config_migrates_to_home_once_preserving_original():
    """PYTMUX_HOME 설정 시 기존(흩어진) config 를 <home>/config 로 1회 복사하고 원본은
    보존한다. load_config 가 그 내용을 읽고, config_path_for_write 가 <home>/config 를
    반환하며, 재호출해도 다시 복사하지 않는다(멱등)."""
    with tempfile.TemporaryDirectory() as d:
        home = os.path.join(d, "home")
        xdg = os.path.join(d, "xdg")
        legacy = os.path.join(xdg, "pytmux", "config")
        os.makedirs(os.path.dirname(legacy), exist_ok=True)
        with open(legacy, "w", encoding="utf-8") as f:
            f.write("set mouse off\nset prefix C-a\n")
        with _Env(PYTMUX_HOME=home, XDG_CONFIG_HOME=xdg, PYTMUX_CONFIG=None):
            target = os.path.join(home, "config")
            assert not os.path.exists(target)
            cfg = keymap.load_config()
            # 마이그레이션: home/config 생성 + 원본 보존
            assert os.path.isfile(target), "home/config 로 복사됐어야"
            assert os.path.isfile(legacy), "원본 config 는 보존돼야"
            # 내용이 읽혔다(set mouse off → mouse False, prefix C-a → ctrl+a)
            assert cfg["mouse"] is False
            assert cfg["prefix"] == "ctrl+a"
            # 쓰기 대상도 home/config
            assert keymap.config_path_for_write() == target
            # 멱등: home/config 를 사용자가 바꾼 뒤 재호출해도 덮어쓰지(재복사) 않음
            with open(target, "w", encoding="utf-8") as f:
                f.write("set mouse on\n")
            cfg2 = keymap.load_config()
            assert cfg2["mouse"] is True, "재복사로 원본이 home/config 를 덮으면 안 됨"


async def test_tokens_db_and_captures_under_home():
    """PYTMUX_HOME 설정 시 토큰 DB 는 <home>/db, captures 는 <home>/captures 아래로.
    (server_only 이 격리용으로 심는 PYTMUX_TOKENS_DB/PYTMUX_CAPTURE_DIR override 를 잠시
    걷어내고 PYTMUX_HOME 만 둔 채 property 를 재평가 — override 가 우선이므로 그대로면
    home 분기를 못 탄다.)"""
    if os.name == "nt":
        return
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as d:
            home = os.path.join(d, "home")
            with _Env(PYTMUX_HOME=home, PYTMUX_TOKENS_DB=None,
                      PYTMUX_CAPTURE_DIR=None):
                hp = os.path.abspath(home)
                assert srv.tokens_db_path.startswith(os.path.join(hp, "db")), \
                    srv.tokens_db_path
                assert srv.capture_dir.startswith(os.path.join(hp, "captures")), \
                    srv.capture_dir
    finally:
        await teardown(srv, task, sock)
