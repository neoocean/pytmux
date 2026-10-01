"""run-pytmux 드라이버가 **라이브 상태 디렉터리 밖에서** 도나(pytmux/pytmux-508).

# 무엇이 잘못돼 있었나

`.claude/skills/run-pytmux/driver.py` 는 Windows 에서 `LOCALAPPDATA` 만 임시 디렉터리로
돌려 놓고 「격리했다」고 적었다. 그런데 `ipc.default_state_dir()` 는 **`PYTMUX_HOME` 을
먼저** 보고, 그 값이 서 있으면 `LOCALAPPDATA` 를 **아예 안 읽는다**. 그 변수를 상시
설정해 쓰는 박스(§10-E #1 통합을 켠 환경)에서는 드라이버가 띄운 서버가 **라이브 상태
디렉터리**에 `default.port`·`default.token` 을 게시했고, 그 순간 새 주인이 되어 사용자의
라이브 서버를 **퇴거**시켰다 — 패널과 그 안에서 돌던 작업이 전부 사라졌다(실측
2026-09-15 09:49:48 · 이 파일을 베낀 프로브 스크립트가 방아쇠였다).

같은 함정을 `tests/harness.py` 는 2026-07-31 에 (`PYTMUX_HOME` 을 지워서),
`qa/env.py` 는 슬롯으로 (그 변수를 자기 스크래치로 세워서) 이미 막아 놓았다.
**드라이버 한 곳만 남아 있었다.**

# 여기서 재는 것

⑴ `PYTMUX_HOME` 이 선 환경에서 드라이버를 만들면, 해석된 상태 디렉터리가 그 홈
   **밖**이다. ⛔ 대조군을 먼저 둔다 — 드라이버를 만들기 **전에** 같은 환경에서
   `default_state_dir()` 이 라이브 홈 **안**을 가리키는 것을 보인다. 그게 없으면 이
   시험은 「원래부터 밖이었다」를 초록으로 읽는다(공허 통과).
⑵ 격리가 깨진 채로는 **서버를 안 띄운다** — `start()` 가 spawn 전에 거절한다.
   조용히 통과시키면 그것이 곧 사고다.
⑶ `stop()` 이 환경을 원래대로 되돌린다(드라이버를 쓴 뒤 같은 프로세스가 라이브를
   보게 된다).

되돌리면 실패해야 하는 것:
  · `os.environ["PYTMUX_HOME"] = …` 한 줄을 지우면 → ⑴ 실패
  · `_assert_isolated()` 호출을 지우면 → ⑵ 실패(가짜 spawn 이 불린다)
  · `stop()` 의 환경 복원을 지우면 → ⑶ 실패
"""
import importlib.util
import os
import sys

import harness  # noqa: F401  (경로 설정)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DRIVER = os.path.join(ROOT, ".claude", "skills", "run-pytmux", "driver.py")

_ENV_KEYS = ("PYTMUX_HOME", "LOCALAPPDATA", "XDG_RUNTIME_DIR")


def _load_driver():
    """스킬의 `driver.py` 를 파일 경로로 들인다(패키지가 아니라 스크립트다)."""
    spec = importlib.util.spec_from_file_location("_rp_driver", DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Env:
    """`with` 를 벗어나면 위 키들을 원래대로. 시험이 라이브 환경을 물들이지 않게."""

    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


def _inside(path, root):
    path, root = os.path.realpath(path), os.path.realpath(root)
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:      # 드라이브가 다르면 같은 자리일 수 없다(Windows)
        return False


async def test_driver_state_dir_escapes_a_live_pytmux_home(tmp_path=None):
    """⑴ `PYTMUX_HOME` 이 서 있어도 드라이버의 상태 자리는 그 홈 밖이다."""
    from pytmuxlib import ipc
    live = os.path.join(str(tmp_path), "live-home")
    os.makedirs(live, exist_ok=True)
    drv = _load_driver()
    with _Env():
        os.environ["PYTMUX_HOME"] = live
        # 대조군 — 격리 «전» 에는 라이브 홈 안을 가리킨다. 이 줄이 이 시험의 전제다.
        assert _inside(ipc.default_state_dir(), live), (
            "대조군이 안 선다: PYTMUX_HOME 을 세웠는데도 상태 자리가 그 밖이다 — "
            "아래 단언은 아무것도 안 재게 된다")
        d = drv.PytmuxDriver(cols=40, rows=10)
        try:
            state = ipc.default_state_dir()
            assert not _inside(state, live), (
                "드라이버가 라이브 홈 안에 상태를 쓴다 — 여기서 서버를 띄우면 "
                f"라이브 default.port/token 을 덮어쓰고 앞 서버를 퇴거시킨다: {state}")
            assert _inside(state, d._scratch), (
                f"상태 자리가 드라이버 스크래치 밖이다: {state} !⊂ {d._scratch}")
        finally:
            import shutil
            shutil.rmtree(d._scratch, ignore_errors=True)


async def test_driver_refuses_to_spawn_when_isolation_is_broken(tmp_path=None):
    """⑵ 격리가 깨지면 `start()` 가 **spawn 전에** 거절한다."""
    from pytmuxlib import ipc, proc
    live = os.path.join(str(tmp_path), "live-home")
    os.makedirs(live, exist_ok=True)
    drv = _load_driver()
    spawned = []

    with _Env():
        os.environ["PYTMUX_HOME"] = live
        d = drv.PytmuxDriver(cols=40, rows=10)
        try:
            # 격리를 되돌린다 — 「환경이 뒤에서 다시 바뀐」 판을 흉내낸다.
            os.environ["PYTMUX_HOME"] = live
            with harness.patched(proc, spawn_detached=lambda *a, **k: spawned.append(a) or 1):
                try:
                    await d.start(ready_timeout=0.1)
                except RuntimeError as e:
                    assert "격리" in str(e), f"다른 이유로 죽었다: {e}"
                else:
                    assert False, "격리가 깨졌는데 그대로 띄웠다"
            assert not spawned, "거절한다면서 서버를 이미 띄웠다"
            assert _inside(ipc.default_state_dir(), live), "전제가 안 선다"
        finally:
            import shutil
            shutil.rmtree(d._scratch, ignore_errors=True)


async def test_driver_stop_restores_the_environment(tmp_path=None):
    """⑶ `stop()` 뒤에는 라이브 환경이 그대로 돌아온다."""
    live = os.path.join(str(tmp_path), "live-home")
    os.makedirs(live, exist_ok=True)
    drv = _load_driver()
    with _Env():
        os.environ["PYTMUX_HOME"] = live
        d = drv.PytmuxDriver(cols=40, rows=10)
        assert os.environ["PYTMUX_HOME"] != live, "생성 시 격리가 안 걸렸다"
        d.server_pid = None          # 띄운 적이 없으니 내릴 것도 없다
        await d.stop()
        assert os.environ["PYTMUX_HOME"] == live, (
            "stop() 뒤에도 PYTMUX_HOME 이 스크래치를 가리킨다 — 같은 프로세스의 "
            "다음 pytmux 호출이 라이브를 못 본다")
        assert not os.path.exists(d._scratch), "스크래치를 안 치웠다"
