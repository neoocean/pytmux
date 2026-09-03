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


async def test_token_db_copy_never_shows_a_half_written_file():
    """토큰 DB 복사는 **최종 이름으로 곧장 쓰지 않는다**(pytmux-474 의 근본).

    실측(2026-09-03 · macOS): 72MB DB 를 복사하는 동안 다른 스레드가 같은 경로를 열면
    SQLite 가 반쯤 쓰인 파일을 읽어 `sqlite3.DatabaseError: database disk image is
    malformed` 로 죽는다. 토큰 동기화 워커의 executor 연결이 실제로 그 창에 끼어
    서버 error.log 에 트레이스백을 남겼고, QA T3 의 여섯 스텝이 그 한 건을 S1 로
    신고했다.

    ⛔ **"복사가 끝난 뒤 내용이 맞다"로는 이걸 못 잡는다** — 그건 경쟁이 없을 때도
       참이다. 그래서 복사가 **어디에 쓰는지**를 잰다: 최종 경로에 직접 쓰면 실패다.
    """
    if os.name == "nt":
        from run import skip
        skip("os.replace 원자성 규약은 POSIX 에서 잰다(Windows 는 별도 경로)")
    import importlib
    import shutil
    sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")

    with tempfile.TemporaryDirectory() as d:
        src_db = os.path.join(d, "src", "claude-tokens.db")
        os.makedirs(os.path.dirname(src_db))
        with open(src_db, "wb") as f:
            f.write(b"REAL_DB")
        with open(src_db + "-wal", "wb") as f:
            f.write(b"WAL")
        new_path = os.path.join(d, "home", "db", "claude-tokens.db")

        wrote_to, real_copy2 = [], shutil.copy2

        def spy(s, dst, *a, **kw):
            wrote_to.append(dst)
            # 복사가 도는 **그 순간** 최종 이름이 보이면 안 된다(경쟁자의 시점).
            assert not os.path.exists(new_path), \
                "복사 도중에 최종 파일이 보인다 — 남이 반쯤 쓰인 DB 를 연다"
            return real_copy2(s, dst, *a, **kw)

        shutil.copy2 = spy
        try:
            assert sm.ServerClaudeMixin._copy_db_tree(src_db, new_path) is True
        finally:
            shutil.copy2 = real_copy2

        assert new_path not in wrote_to, \
            "최종 이름으로 곧장 썼다 — 원자성이 없다: %r" % wrote_to
        assert new_path + "-wal" not in wrote_to, \
            "사이드카도 최종 이름으로 곧장 썼다: %r" % wrote_to
        # 그러고도 결과는 온전해야 한다(원자성이 내용을 갉아먹으면 안 된다).
        assert open(new_path, "rb").read() == b"REAL_DB"
        assert open(new_path + "-wal", "rb").read() == b"WAL"
        # 임시 부스러기를 남기지 않는다.
        leftovers = [n for n in os.listdir(os.path.dirname(new_path)) if ".part-" in n]
        assert not leftovers, "임시 파일이 남았다: %r" % leftovers


async def test_token_db_migrates_plugin_to_home_preserving_original():
    """PYTMUX_HOME 통합 시 토큰 DB 가 평소 위치(플러그인 db/)에서 <home>/db 로 1회 **복사**
    되고 원본은 보존된다(WAL 사이드카 동반). 다른 머신에서 PYTMUX_HOME 을 켜면 기존 토큰
    이력이 따라온다(사용자 요청 2026-06-17)."""
    if os.name == "nt":
        return
    srv, task, sock = await server_only()
    try:
        with tempfile.TemporaryDirectory() as d:
            home = os.path.join(d, "home")
            # 평소(플러그인) DB 원본을 임시 파일로 대체(실제 플러그인 dir 오염 방지).
            src = os.path.join(d, "plugindb", "claude-tokens.db")
            os.makedirs(os.path.dirname(src), exist_ok=True)
            with open(src, "wb") as f:
                f.write(b"FAKE_SQLITE_DB_CONTENT")
            with open(src + "-wal", "wb") as f:   # WAL 사이드카도 따라와야
                f.write(b"WAL")
            srv._plugin_tokens_db_path = lambda: src
            with _Env(PYTMUX_HOME=home, PYTMUX_TOKENS_DB=None):
                new_path = srv.tokens_db_path           # <home>/db/<파일명>
                assert new_path.startswith(
                    os.path.join(os.path.abspath(home), "db")), new_path
                assert not os.path.exists(new_path)
                srv._migrate_legacy_db(new_path)
                assert os.path.exists(new_path), "<home>/db 로 복사됐어야"
                assert os.path.exists(src), "플러그인 db 원본은 보존(복사)"
                assert open(new_path, "rb").read() == b"FAKE_SQLITE_DB_CONTENT"
                assert os.path.exists(new_path + "-wal"), "WAL 사이드카도 복사"
                # 멱등: 이미 있으면 재복사·덮어쓰기 안 함
                with open(new_path, "wb") as f:
                    f.write(b"USER_LOCAL")
                srv._migrate_legacy_db(new_path)
                assert open(new_path, "rb").read() == b"USER_LOCAL"
    finally:
        await teardown(srv, task, sock)
