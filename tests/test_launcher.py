"""런처 단위 테스트 — 중첩 실행 거부 등(서버/클라 기동 불필요)."""
import os
import tempfile

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import sshwrap
from pytmuxlib.launcher import NEST_MARKER, main, nesting_blocked


async def test_nesting_blocked_helper():
    old = os.environ.get("PYTMUX")
    old_m = os.environ.get(NEST_MARKER)
    try:
        os.environ.pop("PYTMUX", None)
        os.environ.pop(NEST_MARKER, None)
        os.environ["PYTMUX"] = "/tmp/some.sock"   # 로컬 패널 안인 상황
        assert nesting_blocked(False) is True, "로컬 패널 안 → 중첩 거부"
        assert nesting_blocked(True) is False, "--force 면 통과"
        os.environ.pop("PYTMUX", None)
        assert nesting_blocked(False) is False, "패널 밖 → 정상"
        # 원격(ssh)에는 PYTMUX 가 없고 표식(LC_PYTMUX)만 전파된다 → 그래도 거부.
        os.environ[NEST_MARKER] = "1"
        assert nesting_blocked(False) is True, "원격 표식만 있어도 거부"
        assert nesting_blocked(True) is False, "--force 면 통과(원격도)"
    finally:
        for k, v in ((("PYTMUX", old)), ((NEST_MARKER, old_m))):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def test_sshwrap_marker_and_path():
    """sshwrap.panel_env: 표식 env + ssh 래퍼 PATH 앞단 주입. 래퍼는 SendEnv 전파."""
    if os.name == "nt":
        return  # POSIX 전용
    state = tempfile.mkdtemp(prefix="pytmux-sshwrap-")
    env = {"PATH": "/usr/bin:/bin"}
    out = sshwrap.panel_env(env, state)
    assert out[sshwrap.NEST_MARKER] == "1", "표식 env 주입"
    assert out is env
    wd = os.path.join(state, "sshwrap")
    assert out["PATH"].startswith(wd + os.pathsep), "래퍼 디렉터리가 PATH 앞단"
    # ssh/autossh 래퍼가 실행 가능하게 생성되고 SendEnv 표식을 끼운다.
    for name in ("ssh", "autossh"):
        p = os.path.join(wd, name)
        assert os.access(p, os.X_OK), f"{name} 래퍼 실행권한"
        body = open(p).read()
        assert f"SendEnv={sshwrap.NEST_MARKER}" in body, "SendEnv 표식 전파"
    # 표식 이름은 launcher 와 일치해야 한다(로컬·원격 공통 판정).
    assert sshwrap.NEST_MARKER == NEST_MARKER


async def test_main_refuses_nested_attach():
    # PYTMUX 가 설정된 상태에서 attach → SystemExit(1)(ensure_server 도달 전 차단).
    old = os.environ.get("PYTMUX")
    try:
        os.environ["PYTMUX"] = "/tmp/some.sock"
        code = None
        try:
            main(["attach"])
        except SystemExit as e:
            code = e.code
        assert code == 1, f"중첩 attach 는 거부(exit 1), got {code}"
    finally:
        if old is None:
            os.environ.pop("PYTMUX", None)
        else:
            os.environ["PYTMUX"] = old
