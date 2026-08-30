"""스위트 위생: 사용자의 진짜 설정 파일을 **읽지도 쓰지도 않는다**(pytmux/pytmux-135).

`run.py` 는 `PYTMUX_HOME` 을 거두는데, 설정 파일 탐색 차례가
`$PYTMUX_CONFIG` → `$PYTMUX_HOME/config` → `$XDG_CONFIG_HOME/pytmux/config`
→ `~/.pytmux.conf` 라 **거둔 결과 두 번째 자리가 사라져 세 번째 = 사용자의 진짜
설정 파일**로 떨어졌다. 처방은 `tests/hermetic.py` — `PYTMUX_CONFIG` 를 빈 임시
파일로 **세운다**.

★ **같은 구멍의 Rust 판은 `cargo test` 다**(pytmux/pytmux-202). 카고는 `run.py` 프로세스를
안 지나므로 위 처방 밖이었고, `client/crates/base/src/config.rs` 의 탐색 차례가 파이썬과
같아서 GUI 테스트가 `Config::load()` 로 **이 상자의 진짜 설정 파일**을 읽었다. 그 자리를
막는 곳은 합본 게이트가 자식 env 를 만드는 한 함수(`scripts/check_all.py::child_env`)다 —
아래 마지막 시험이 그것을 «맨 셸» 모양으로 잰다.

되돌리면 실패해야 하는 것:
  · `tests/hermetic.py::isolate_config` 의 `os.environ["PYTMUX_CONFIG"] = …` 를 빼면
    → 아래 읽기·쓰기 시험이 둘 다 실패(대조군이 그 경로가 살아 있음을 먼저 보인다)
  · `run.py` 의 호출 한 줄을 지우면 → test_runner_and_harness_install_the_guard 실패
  · `harness.py` 의 호출 한 줄을 지우면 → 같은 시험 실패
    (헬퍼만 단언하면 호출부를 지워도 통과한다 — 이 저장소가 말하는 «공허 통과»)
  · `check_all.py::child_env` 의 `hermetic.isolate_config()` 한 줄을 지우면
    → test_the_combined_gate_hands_the_guard_to_its_children 실패
  · `check_all.py::child_env` 의 `env["PYTHON"] = sys.executable` 한 줄을 지우면
    → test_the_combined_gate_hands_its_own_python_to_shell_gates 실패
    (같은 함수가 쥔 넷째 — 갈리는 것이 «설정»이 아니라 «파이썬»일 뿐이다. pytmux-383)
"""
import contextlib
import os
import subprocess
import sys
import tempfile

import harness  # noqa: F401  (경로 설정 + 위생 설치)
import hermetic
import run

from pytmuxlib import keymap

# 합본 게이트는 `scripts/` 에 있다(하니스는 저장소 뿌리와 `tests/` 만 경로에 넣는다).
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))
import check_all  # noqa: E402


@contextlib.contextmanager
def _env(**kw):
    """os.environ 키들을 임시로 세우고(None=제거) 블록을 벗어나면 원복한다."""
    saved = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, old in saved.items():
            if old is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = old


def _fake_user_home(root):
    """«사용자의 진짜 설정»을 흉내 낸 스크래치 홈을 짓고 (env, config 경로) 를 준다.

    ⛔ 이 상자의 실제 `~/.config/pytmux/config` 를 쓰지 않는다 — 그 파일이 없는
    상자(CI)에서 시험이 **아무것도 안 재고 초록**이 되고, 있는 상자에서는 시험이
    사용자 파일을 건드리게 된다. 홈을 통째로 스크래치로 돌려 양쪽을 다 피한다.
    """
    xdg = os.path.join(root, ".config")
    cfg = os.path.join(xdg, "pytmux", "config")
    os.makedirs(os.path.dirname(cfg), exist_ok=True)
    with open(cfg, "w", encoding="utf-8") as f:
        f.write("set mouse off\nset inactive-dim off\n")
    # HOME·USERPROFILE = `~` 해석(POSIX·Windows), XDG_CONFIG_HOME = `.config` 자리.
    env = {"HOME": root, "USERPROFILE": root, "XDG_CONFIG_HOME": xdg,
           "PYTMUX_HOME": None}
    return env, cfg


async def test_runner_and_harness_install_the_guard():
    """호출부 오라클 — 러너와 하니스가 **각자** 위생을 세운다.

    둘 다 필요하다: `run.py` 것은 **어느 테스트 모듈 import 보다 먼저** 돌아야 하고
    (하니스를 import 하지 않는 모듈이 있다), `harness.py` 것은 하니스를 **직접
    import 하는 경로**(임시 진단 스크립트·에디터 러너)를 덮는다.
    """
    path = os.environ.get("PYTMUX_CONFIG")
    assert path, "PYTMUX_CONFIG 가 안 세워졌다 — 위생이 거두기만 하고 있다"
    assert os.path.isfile(path), path
    assert run.HERMETIC_CONFIG == path, (run.HERMETIC_CONFIG, path)
    assert harness.HERMETIC_CONFIG == path, (harness.HERMETIC_CONFIG, path)

    # 사용자 자리와 겹치지 않는다(이 상자에 그 파일이 있든 없든 성립하는 단언).
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    for user_path in (os.path.join(xdg, "pytmux", "config"),
                      os.path.expanduser("~/.pytmux.conf")):
        assert os.path.abspath(path) != os.path.abspath(user_path), path

    # 그 파일은 «빈» 설정이다 — 남의 값이 실려 있으면 위생이 아니라 또 하나의 출처다.
    cfg = keymap.load_config()
    assert cfg["mouse"] is True, cfg
    assert "inactive_dim" not in cfg, cfg


async def test_user_config_is_not_read():
    """읽기 — 사용자 설정에 값을 넣어도 스위트가 보는 값이 안 바뀐다(받아들임 기준 ①)."""
    with tempfile.TemporaryDirectory() as root:
        env, user_cfg = _fake_user_home(root)

        # 대조군: 위생을 걷어내면(=고치기 전의 상태) 진짜 그 파일로 떨어진다.
        # 이 대조군이 없으면 아래 본시험이 «아무 일도 안 하는 코드»를 통과시킨다.
        with _env(PYTMUX_CONFIG=None, **env):
            leaked = keymap.load_config()
        assert leaked["mouse"] is False and leaked["inactive_dim"] is False, (
            "탐색 차례가 바뀌었다 — 이 시험이 재려던 구멍이 이제 없다", leaked, user_cfg)

        # 본시험: 위생이 선 채로는 사용자 파일이 안 읽힌다.
        with _env(**env):
            guarded = keymap.load_config()
        assert guarded["mouse"] is True, guarded
        assert "inactive_dim" not in guarded, guarded


async def test_user_config_is_not_written():
    """쓰기 — `set_config_option` 이 사용자 파일에 줄을 박지 않는다(받아들임 기준 ②).

    클라의 `:settings` 는 `config_path_for_write()` 가 정한 파일에 되쓴다. 경로를
    명시하지 않는 경로가 하나라도 있으면 그 줄은 **사용자 파일**로 갔다.
    """
    with tempfile.TemporaryDirectory() as root:
        env, user_cfg = _fake_user_home(root)
        before = open(user_cfg, encoding="utf-8").read()
        before_mtime = os.stat(user_cfg).st_mtime_ns

        # 대조군: 위생이 없으면 쓰기 대상이 곧 사용자 파일이다.
        with _env(PYTMUX_CONFIG=None, **env):
            assert os.path.abspath(keymap.config_path_for_write()) == \
                os.path.abspath(user_cfg), keymap.config_path_for_write()

        # 본시험 ⑴ 쓰기 대상이 스위트의 빈 파일로 온다.
        suite_cfg = os.environ.get("PYTMUX_CONFIG")
        assert suite_cfg, "PYTMUX_CONFIG 가 안 세워졌다 — 위생이 거두기만 하고 있다"
        with _env(**env):
            assert os.path.abspath(keymap.config_path_for_write()) == \
                os.path.abspath(suite_cfg), keymap.config_path_for_write()

        # 본시험 ⑵ 실제로 써 봐도 사용자 파일은 바이트도 mtime 도 안 움직인다.
        #   ⚠ 스위트가 공유하는 `PYTMUX_CONFIG` 파일에 쓰면 뒤따르는 테스트가 그 값을
        #     읽는다(모듈 경계 오염) — 이 시험 동안만 제 임시 파일로 돌린다.
        scratch = os.path.join(root, "suite-config")
        open(scratch, "w", encoding="utf-8").close()
        with _env(PYTMUX_CONFIG=scratch, **env):
            written = keymap.set_config_option("mouse", "off")
        assert os.path.abspath(written) == os.path.abspath(scratch), written
        assert "set mouse off" in open(scratch, encoding="utf-8").read()
        assert open(user_cfg, encoding="utf-8").read() == before, \
            "사용자 설정 파일의 내용이 바뀌었다"
        assert os.stat(user_cfg).st_mtime_ns == before_mtime, \
            "사용자 설정 파일의 mtime 이 움직였다"


async def test_a_busy_config_file_is_retried_not_lost():
    """설정 쓰기가 **한 번의 PermissionError 로 사라지지 않는다**.

    실측(2026-08-24 · 이 Windows 상자): 방금 쓴 임시 파일을 바꿔치기할 때 `os.replace` 가
    `[WinError 5] Access is denied` 로 죽었다(3회 중 1회 · 픽스처 생성기가 그 경로를 지난다).
    우리 코드 잘못이 아니라 **다른 프로세스가 그 순간 그 파일을 쥐고 있는 것**이고(보안
    에이전트·인덱서) 창은 밀리초다. 한 번만 시도하면 사용자에게는 `set` 이 조용히 안 먹는다.

    ⛔ 끝까지 안 되면 **예외를 올려 보내야** 한다 — 삼키면 "저장했다"는 거짓말이 된다.
    """
    import os as _os

    tmpdir = tempfile.mkdtemp()
    target = os.path.join(tmpdir, "config")
    try:
        real = _os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError(5, "Access is denied")
            return real(src, dst)

        with harness.patched(_os, replace=flaky):
            keymap.set_config_option("mouse", "off", target)
        assert calls["n"] == 2, f"되풀이하지 않았다(호출 {calls['n']}회)"
        with open(target, encoding="utf-8") as fh:
            assert "mouse off" in fh.read(), "되풀이했는데 값이 안 들어갔다"

        def always(src, dst):
            raise PermissionError(5, "Access is denied")

        with harness.patched(_os, replace=always):
            try:
                keymap.set_config_option("mouse", "on", target)
            except PermissionError:
                pass
            else:
                raise AssertionError("끝까지 막혔는데 성공한 척했다")
    finally:
        import shutil as _sh
        _sh.rmtree(tmpdir, ignore_errors=True)


async def test_isolate_config_is_idempotent_and_overrides_the_shell():
    """멱등 + 셸 값 무시 — 개발자 셸이 `PYTMUX_CONFIG` 를 export 해 뒀어도 덮는다.

    존중하면 위생이 셸 상태에 달린다(`PYTMUX_HOME` 을 무조건 거두는 것과 같은 규율).
    """
    path = os.environ.get("PYTMUX_CONFIG")
    assert path, "PYTMUX_CONFIG 가 안 세워졌다 — 위생이 거두기만 하고 있다"
    assert hermetic.isolate_config() == path, "다시 부르면 파일이 또 생긴다"

    with tempfile.TemporaryDirectory() as root:
        shell_cfg = os.path.join(root, "shell-config")
        with open(shell_cfg, "w", encoding="utf-8") as f:
            f.write("set mouse off\n")
        # 셸에서 물려받은 모양 = 표식 없이 값만 있는 상태.
        with _env(PYTMUX_CONFIG=shell_cfg, PYTMUX_TEST_CONFIG_ISOLATED=None):
            fresh = hermetic.isolate_config()
            assert os.path.abspath(fresh) != os.path.abspath(shell_cfg), fresh
            assert keymap.load_config()["mouse"] is True, "셸의 설정이 읽혔다"


async def test_the_combined_gate_hands_the_guard_to_its_children():
    """합본 게이트가 **자식 env** 에 위생을 실어 준다 — cargo 스텝까지(pytmux/pytmux-202).

    파이썬 스위트는 `run.py` 가 제 손으로 막지만 **`cargo test` 는 그 프로세스를 안
    지난다.** 그래서 재는 자리는 게이트가 자식 env 를 만드는 한 함수
    (`scripts/check_all.py::child_env`)다 — 거기 한 줄이 카고·셸 게이트·파이썬 스위트를
    한꺼번에 덮는다.

    ⛔ **`os.environ` 을 그대로 둔 채 재면 공허하게 통과한다**: 이 시험 프로세스에는
    `run.py` 가 이미 `PYTMUX_CONFIG` 를 세워 뒀으므로, 그 한 줄을 지워도
    `dict(os.environ)` 이 그 키를 그대로 싣는다. 그래서 «개발자가 맨 셸에서 친 모양»
    (값도 표식도 없다)으로 돌린다.
    """
    with tempfile.TemporaryDirectory() as root:
        home, user_cfg = _fake_user_home(root)
        bare = dict(home, PYTMUX_CONFIG=None, PYTMUX_TEST_CONFIG_ISOLATED=None)

        with _env(**bare):
            # 대조군 — 맨 복사본은 그 키를 안 싣는다. 즉 자식은 탐색 차례를 끝까지
            # 걸어가 **사용자의 진짜 설정 파일**을 읽고(그리고 쓴다). 이 대조군이 없으면
            # 아래 본시험이 «아무 일도 안 하는 코드»를 통과시킨다.
            assert "PYTMUX_CONFIG" not in dict(os.environ)
            assert keymap.load_config()["mouse"] is False, "탐색 차례가 바뀌었다 — 이 시험이 재려던 구멍이 이제 없다"
            assert os.path.abspath(keymap.config_path_for_write()) == \
                os.path.abspath(user_cfg), keymap.config_path_for_write()

            # 본시험 — 게이트의 자식 env 에는 그 키가 실려 있다.
            env = check_all.child_env()

        path = env.get("PYTMUX_CONFIG")
        assert path, "게이트가 자식에게 PYTMUX_CONFIG 를 안 준다 — cargo 스텝이 이 상자의 설정을 읽는다"
        assert os.path.isfile(path), path
        for user_path in (user_cfg,
                          os.path.join(os.path.expanduser("~"), ".config", "pytmux", "config"),
                          os.path.expanduser("~/.pytmux.conf")):
            assert os.path.abspath(path) != os.path.abspath(user_path), path

        # 그리고 그 파일은 «빈» 설정이다 — 값이 실려 있으면 위생이 아니라 또 하나의 출처다.
        # (Rust 쪽 `Config::path()` 는 이 차례를 그대로 따라가므로 파이썬으로 재도 같은 답이다.)
        with _env(PYTMUX_CONFIG=path, **home):
            assert keymap.load_config()["mouse"] is True, "게이트가 준 설정 파일이 비어 있지 않다"

    # 같은 함수가 `NO_COLOR` 도 거둔다 — 자식 env 를 쥔 자리가 하나라는 것이 이 함수의 값이다.
    with _env(NO_COLOR="1", PYTMUX_CONFIG=None, PYTMUX_TEST_CONFIG_ISOLATED=None):
        assert "NO_COLOR" not in check_all.child_env()


async def test_the_combined_gate_hands_its_own_python_to_shell_gates():
    """합본 게이트가 **자기 인터프리터**를 자식에게 넘긴다(pytmux/pytmux-383 · child_env ⑷).

    위 시험이 ⑶ 을 재는 것과 같은 부류다 — 다만 이번에 갈린 것은 «설정»이 아니라
    «파이썬»이다. 셸 게이트(`check_licenses.sh`·`build_release.sh`)는 자기가 파이썬을
    다시 찾으므로, 게이트가 `PYTHON` 을 안 넘기면 그 스텝만 이 상자의 `python3` 로
    떨어진다. 2026-08-23 에 그 이름이 Windows Store 앱 별칭이라 「라이선스 경계」가
    **1.0초 만에 FAIL** 이었다(재배포 고지를 한 줄도 안 재고).

    ⛔ **`os.environ` 을 그대로 둔 채 재면 공허하게 통과한다** — 개발자 셸에 `PYTHON`
    이 export 돼 있으면 `dict(os.environ)` 이 그 키를 그대로 싣는다. 그래서 «맨 셸»
    모양(키가 아예 없다)으로 돌린다.
    """
    with _env(PYTHON=None, PYTMUX_PYTHON=None):
        # 대조군 — 맨 복사본은 그 키를 안 싣는다. 즉 셸 게이트는 이름을 스스로 다시
        # 찾는다. 이 대조군이 없으면 아래 본시험이 «아무 일도 안 하는 코드»를 통과시킨다.
        assert "PYTHON" not in dict(os.environ)
        env = check_all.child_env()

    assert env.get("PYTHON") == sys.executable, env.get("PYTHON")
    assert os.path.isabs(env["PYTHON"]), (
        "이름만 넘겼다 — 자식의 PATH 가 다르면 다른 인터프리터로 떨어진다", env["PYTHON"])

    # 셸이 심어 둔 값은 **덮는다**. 게이트가 도는 자와 셸 게이트가 쓰는 자가 갈리면
    # 그 갈림 자체가 다음 함정이고(스텝마다 다른 파이썬으로 잰다), 셸의 그 값이 바로
    # 이번에 물린 별칭일 수 있다.
    with _env(PYTHON="python3", PYTMUX_PYTHON=None):
        assert check_all.child_env().get("PYTHON") == sys.executable

    # 그리고 그것은 «실제로 도는» 파이썬 3 이다 — 이름이 아니라 답으로 판정한다.
    got = subprocess.run([env["PYTHON"], "-c", "import sys; print(sys.version_info[0])"],
                         capture_output=True, text=True)
    assert got.returncode == 0 and got.stdout.strip() == "3", (got.returncode, got.stdout)
