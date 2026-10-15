"""pytmux-34 플러그인·인프라 검수 — claude-code 쪽 회귀 시험.

되돌리면 실패해야 하는 오라클:
  · `_migrate_legacy_db` 에서 `_harden_db_dir` 호출을 지우면 → test_db_dir_is_hardened_on_every_db_entry
  · `PYTMUX_NO_DB_MIGRATE` 갈래를 지우면 → test_scratch_home_does_not_copy_the_real_db
  · `ensure_host_id` 가 다시 «아무 OSError 든 새로 만든다» 로 돌아가면 → test_host_id_*
  · `save_master` 가 다시 O_TRUNC 직접 쓰기로 돌아가면 → test_save_master_is_atomic_and_hardened
  · 그림자 프로브 캡을 손으로 적은 합으로 되돌리면 → test_usage_probe_cap_covers_the_inner_worst_case
  · 시간 초과 때 `_usage_busy` 를 바로 풀면 → test_usage_probe_timeout_keeps_busy_until_the_thread_ends
"""
import asyncio
import importlib
import os
import subprocess
import tempfile
import time

import harness  # noqa: F401  (경로 설정 + 플러그인 별칭 등록)
from pytmuxlib import ipc

sm = importlib.import_module("pytmuxlib.plugins.claude-code.servermixin")
syncrypto = importlib.import_module("pytmuxlib.plugins.claude-code.syncrypto")
usageprobe = importlib.import_module("pytmuxlib.plugins.claude-code.usageprobe")


class _Env:
    """환경변수를 구간에 가둔다(None = 지움)."""
    def __init__(self, **kv):
        self.kv = kv
        self.saved = {}

    def __enter__(self):
        for k, v in self.kv.items():
            self.saved[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


class _Fake(sm.ServerClaudeMixin):
    def __init__(self, plugin_db, legacy_db):
        self._p, self._l = plugin_db, legacy_db

    def _plugin_tokens_db_path(self):
        return self._p

    def _legacy_tokens_db_path(self):
        return self._l


# ── #1 (S2) DB 디렉터리 하드닝 ──────────────────────────────────────────────

async def test_db_dir_is_hardened_on_every_db_entry():
    """pytmux-34 #1: 토큰 DB·sync_vault.key·sync_device.key 가 사는 디렉터리가 Windows
    에서 넓은 상속 ACL(`Authenticated Users:(M)`)을 물려받았다. 모든 DB 진입이 지나는
    `_migrate_legacy_db` 가 그 디렉터리를 `ipc._harden_win_acl(is_dir=True)` 로 조이는지,
    그리고 `PYTMUX_TOKENS_DB` 로 받은 남의 디렉터리(예: %TEMP%)는 **안** 건드리는지 잰다."""
    d = tempfile.mkdtemp()
    new = os.path.join(d, "db", "claude-tokens.db")
    calls = []
    old_h, old_w = ipc._harden_win_acl, ipc.IS_WINDOWS
    ipc._harden_win_acl = lambda p, is_dir=False: calls.append((p, is_dir))
    ipc.IS_WINDOWS = True
    try:
        with _Env(PYTMUX_TOKENS_DB=None, PYTMUX_NO_DB_MIGRATE="1", PYTMUX_HOME=None):
            _Fake(os.path.join(d, "p.db"), os.path.join(d, "l.db"))._migrate_legacy_db(new)
        assert calls == [(os.path.dirname(os.path.abspath(new)), True)], calls
        calls.clear()
        with _Env(PYTMUX_TOKENS_DB=os.path.join(d, "x.db")):
            _Fake(os.path.join(d, "p.db"), os.path.join(d, "l.db"))._migrate_legacy_db(new)
        assert calls == [], "PYTMUX_TOKENS_DB 의 (남의) 디렉터리 ACL 을 바꿨다"
    finally:
        ipc._harden_win_acl, ipc.IS_WINDOWS = old_h, old_w


async def test_db_dir_hardening_really_drops_broad_aces_on_windows():
    """실제 icacls 로 잰다(Windows 전용). 조인 뒤 그 안에 **새로 만든** 키 파일까지 상속으로
    `Authenticated Users`·`BUILTIN\\Users` ACE 가 없어야 한다."""
    if os.name != "nt":
        from run import skip
        skip("Windows ACL 전용")
    scratch = tempfile.mkdtemp(prefix="pytmux-acl-")
    # 부모에 넓은 **상속** ACE 를 심어 «D:\ 루트에서 내려온 상속» 을 흉내 낸다(대조군) —
    # 실측 ACL 은 `Authenticated Users:(I)(OI)(CI)(M)` 였다(I = 상속).
    subprocess.run(["icacls", scratch, "/grant", "*S-1-5-11:(OI)(CI)M"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    db_dir = os.path.join(scratch, "db")
    os.makedirs(db_dir)
    before = subprocess.run(["icacls", db_dir], capture_output=True).stdout.decode(
        "utf-8", "replace")
    if "S-1-5-11" not in before and "Authenticated Users" not in before:
        from run import skip
        skip("대조군 ACE 를 심지 못했다(로캘/권한)")
    with _Env(LOCALAPPDATA=scratch):          # ACL 표식 저장소도 스크래치로
        sm.ServerClaudeMixin._harden_db_dir(os.path.join(db_dir, "claude-tokens.db"))
    key = os.path.join(db_dir, "sync_device.key")
    with open(key, "wb") as f:
        f.write(b"k" * 32)
    for p in (db_dir, key):
        out = subprocess.run(["icacls", p], capture_output=True).stdout.decode(
            "utf-8", "replace")
        assert "Authenticated Users" not in out and "S-1-5-11" not in out, out
        assert "BUILTIN\\Users" not in out, out


# ── #9 스크래치 홈이 실 DB 를 끌어오지 않는다 ────────────────────────────────

async def test_scratch_home_does_not_copy_the_real_db():
    """pytmux-34 #9: QA 슬롯·드라이버(PYTMUX_HOME=스크래치)가 사용자의 실 플러그인 DB
    (72MB · 이메일 원문)를 루프 스레드에서 복사해 왔다. 스위치가 서면 복사하지 않는다.
    대조군: 스위치가 없으면 종전대로 복사한다."""
    d = tempfile.mkdtemp()
    plugin_db = os.path.join(d, "plugin", "claude-tokens.db")
    os.makedirs(os.path.dirname(plugin_db))
    with open(plugin_db, "wb") as f:
        f.write(b"REAL_USER_DB")
    home = os.path.join(d, "home")
    new = os.path.join(home, "db", "claude-tokens.db")
    old_h = ipc._harden_win_acl
    ipc._harden_win_acl = lambda *a, **k: None
    try:
        with _Env(PYTMUX_TOKENS_DB=None, PYTMUX_HOME=home, PYTMUX_NO_DB_MIGRATE="1"):
            _Fake(plugin_db, os.path.join(d, "legacy.db"))._migrate_legacy_db(new)
        assert not os.path.exists(new), "스크래치 홈이 실 DB 를 복사해 왔다"
        with _Env(PYTMUX_TOKENS_DB=None, PYTMUX_HOME=home, PYTMUX_NO_DB_MIGRATE=None):
            _Fake(plugin_db, os.path.join(d, "legacy.db"))._migrate_legacy_db(new)
        assert open(new, "rb").read() == b"REAL_USER_DB", "대조군 — 평소 이전이 죽었다"
    finally:
        ipc._harden_win_acl = old_h


async def test_run_pytmux_driver_sets_the_same_switches():
    """드라이버도 QA 슬롯과 같은 스위치를 세운다(호출부 단언)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, ".claude", "skills", "run-pytmux", "driver.py"),
               encoding="utf-8").read()
    assert 'os.environ["PYTMUX_NO_DB_MIGRATE"] = "1"' in src
    assert 'os.environ["PYTMUX_TOKEN_SYNC_URL"]' in src


# ── #4 신원 파일을 조용히 재생성하지 않는다 ──────────────────────────────────

async def test_host_id_read_failure_is_hard_not_regenerated():
    """pytmux-34 #4: 읽기에서 난 **어떤** OSError 든(백신의 일시 공유 잠금 등) 새 uuid 로
    덮어썼다 → 미전송 행이 `host=?` 에서 영영 빠지고 자기 옛 레코드가 남의 것이 됐다."""
    import builtins
    d = tempfile.mkdtemp()
    path = os.path.join(d, "host_id")
    with open(path, "w", encoding="ascii") as f:
        f.write("original-host")
    real_open = builtins.open

    def flaky(p, *a, **k):
        if os.path.abspath(str(p)) == os.path.abspath(path):
            raise PermissionError(13, "sharing violation", p)
        return real_open(p, *a, **k)
    builtins.open = flaky
    try:
        try:
            syncrypto.ensure_host_id(d)
        except syncrypto.SyncCryptoError as e:
            assert path in str(e), e
        else:
            raise AssertionError("읽기 실패를 삼키고 새 host_id 를 만들었다")
    finally:
        builtins.open = real_open
    assert open(path, encoding="ascii").read() == "original-host", "파일을 덮어썼다"
    assert syncrypto.ensure_host_id(d) == "original-host"


async def test_host_id_created_only_when_missing_and_atomically():
    d = tempfile.mkdtemp()
    hid = syncrypto.ensure_host_id(d)
    assert hid and syncrypto.ensure_host_id(d) == hid
    assert [n for n in os.listdir(d) if n.startswith(".tmp-")] == []   # 임시 파일 잔해 없음
    # 옛 비원자 쓰기가 남긴 빈 파일은 잔해라 다시 만든다.
    d2 = tempfile.mkdtemp()
    open(os.path.join(d2, "host_id"), "w").close()
    assert syncrypto.ensure_host_id(d2)
    # 이미 있으면 진다(replace=False) — 동시 생성에서 서로의 값을 덮지 않는다.
    p = os.path.join(d, "host_id")
    assert syncrypto._write_private(p, b"other", replace=False) is False
    assert open(p, encoding="ascii").read() == hid


async def test_save_master_is_atomic_and_hardened():
    """pytmux-34 #1·#4: vault 마스터 키는 임시 파일에 쓰고 **하드닝한 뒤** 제자리에
    놓는다(O_TRUNC 직후 죽으면 0바이트 키가 남았고, Windows 에서는 0600 이 무의미했다)."""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "sync_vault.key")
    seen = []
    old = syncrypto.harden_private_file

    def rec(p):
        seen.append((p, os.path.exists(path)))
        old(p)
    syncrypto.harden_private_file = rec
    try:
        syncrypto.save_master(path, b"\x01" * syncrypto.MASTER_LEN)
    finally:
        syncrypto.harden_private_file = old
    assert seen and seen[0][0] != path and seen[0][1] is False, \
        "제자리에 놓인 뒤에야 조였다(읽힐 창)"
    import base64
    assert base64.b64decode(open(path, "rb").read()) == b"\x01" * syncrypto.MASTER_LEN
    assert [n for n in os.listdir(d) if n.startswith(".tmp-")] == []


# ── #5 _fg_is_claude 의 ps 가 루프 스레드에서 돌지 않는다 ────────────────────

async def test_fg_is_claude_ps_runs_off_the_loop_thread():
    """pytmux-34 #5: 인식 실패 패널마다 5초에 한 번 `ps`(타임아웃 1초)를 루프에서 동기로
    불렀다. 이제 executor 로 묻고 (pgid, 판정) 캐시로 받는다 — 첫 물음은 «모른다 = False»
    (보수적), 답이 오면 같은 포그라운드 그룹에 그 답을 쓴다. 그룹이 바뀌면 다시 모른다."""
    import threading
    loop_thread = threading.current_thread()
    seen = []

    class _Srv(sm.ServerClaudeMixin):
        @staticmethod
        def _ps_says_claude(pgid):
            seen.append((threading.current_thread(), pgid))
            time.sleep(0.2)
            return True

    class _Pane:
        master_fd = 7

    had = hasattr(os, "tcgetpgrp")
    old_tc = getattr(os, "tcgetpgrp", None)
    old_win = sm.pty_backend.IS_WINDOWS
    pg = {"v": 4242}
    os.tcgetpgrp = lambda fd: pg["v"]
    sm.pty_backend.IS_WINDOWS = False
    try:
        s, p = _Srv(), _Pane()
        t0 = time.monotonic()
        assert s._fg_is_claude(p) is False            # 아직 모른다
        assert time.monotonic() - t0 < 0.1, "ps 를 기다렸다 — 루프 블로킹"
        await harness.wait_for(lambda: not getattr(p, "_fg_claude_pending", False),
                               timeout=5.0)
        assert s._fg_is_claude(p) is True              # 같은 그룹 → 받은 답
        assert seen and all(th is not loop_thread for th, _ in seen), seen
        pg["v"] = 5151                                 # 포그라운드가 바뀌면 다시 모른다
        assert s._fg_is_claude(p) is False
    finally:
        sm.pty_backend.IS_WINDOWS = old_win
        if had:
            os.tcgetpgrp = old_tc
        else:
            del os.tcgetpgrp
        await harness.wait_for(lambda: not getattr(p, "_fg_claude_pending", False),
                               timeout=5.0)


# ── #10 그림자 /usage 프로브 캡 ──────────────────────────────────────────────

async def test_usage_probe_cap_covers_the_inner_worst_case():
    """pytmux-34 #10: 바깥 캡(25+12+25=62초)이 안쪽 최악(승인 연장 부팅 2회 + /usage·
    /status 패널 2회 ≈ 74초)보다 작았다."""
    import inspect
    worst = usageprobe.worst_case_seconds()
    assert worst >= 2 * usageprobe.BOOT_TIMEOUT + 2 * usageprobe.PANEL_TIMEOUT
    src = inspect.getsource(sm.ServerClaudeMixin.refresh_usage)
    assert "worst_case_seconds()" in src, "캡이 안쪽 최악을 근거로 하지 않는다"


async def test_usage_probe_timeout_keeps_busy_until_the_thread_ends():
    """pytmux-34 #10: 바깥이 잘라도 executor 의 숨은 claude 는 계속 돈다 — 그때 바쁨을
    풀면 다음 예약이 두 번째 숨은 claude 를 겹쳐 띄웠다. 시간 초과는 트레이스백 없이
    (exc=False) 한 줄만 남는다."""
    logged = []

    class _Srv(sm.ServerClaudeMixin):
        _usage = None
        _usage_busy = False
        _usage_probe_err = False

        def _probe_cwd(self):
            return None

        def _note_usage_probe(self, timings):
            pass

        def _log_error(self, where, detail="", exc=True):
            logged.append((where, detail, exc))

    old_q, old_w = usageprobe.query_usage, usageprobe.worst_case_seconds
    usageprobe.query_usage = lambda *a, **k: time.sleep(0.6)
    usageprobe.worst_case_seconds = lambda *a, **k: -9.8     # 캡 = 0.2초
    try:
        s = _Srv()
        u = await s.refresh_usage()
        assert u is None
        assert s._usage_busy is True, "스레드가 아직 도는데 바쁨을 풀었다"
        assert await s.refresh_usage() is None                 # 겹쳐 띄우지 않는다
        await harness.wait_for(lambda: not s._usage_busy, timeout=5.0)
        assert s._usage_busy is False, "스레드가 끝났는데 바쁨이 안 풀렸다"
        assert logged == [("usage_probe", "timeout", False)], logged
    finally:
        usageprobe.query_usage, usageprobe.worst_case_seconds = old_q, old_w
