"""스위트 위생(hermetic) — «거두면 오히려 새는» env 를 **세우는** 자리.

`PYTMUX_HOME`·`NO_COLOR` 는 거두는 것이 맞다(`run.py` §위생 · `harness.server_only`).
그런데 설정 파일만은 **거두면 안 된다**. 탐색 차례가

    인자 경로 → `$PYTMUX_CONFIG` → `$PYTMUX_HOME/config`
              → `$XDG_CONFIG_HOME/pytmux/config` → `~/.pytmux.conf`

라서(`pytmuxlib/keymap.py::load_config`), `PYTMUX_HOME` 을 거두면 **두 번째 자리가
사라져 곧장 세 번째 = 사용자의 진짜 설정 파일**로 떨어진다. 위생을 지키려던 한 줄이
오히려 진짜 파일로 가는 길을 연다(pytmux/pytmux-135).

값은 두 방향 모두다.

  · **읽기** — 이 상자의 `~/.config/pytmux/config` 에 `set inactive-dim off` 가 있으면
    스위트는 **다른 상자와 다른 값으로** 돈다. CI 는 그 파일이 없어 초록이라, 로컬에만
    나는 거짓 실패/거짓 통과가 되고 원인이 코드에 없다.
  · **쓰기** — `keymap.config_path_for_write()` 는 같은 후보 순서를 쓴다. 클라의
    `:settings` 경로를 지나는 테스트가 `path` 를 명시하지 않으면 `set_config_option`
    이 **사용자 파일에 줄을 박는다.**

같은 함정이 Rust 쪽에는 이미 사고로 기록돼 있다(`client/CLAUDE.md` 사고 2026-08-04):
스크래치 홈에 `config` 를 안 만들었더니 GUI 가 사용자의 진짜 설정을 읽고 **썼고**,
그 다음 `cargo test -p gui` 에서 배지 자리 오라클 둘이 떨어졌다. 제품도 테스트도
멀쩡한데 **환경이 실패를 만든** 부류라 원인을 코드에서 찾으면 한참 헤맨다.

처방은 그 문서가 적어 둔 것과 같다 — **빈 파일을 하나 만들어 `PYTMUX_CONFIG` 로
가리킨다.** 빈 파일이면 `load_config` 가 기본값 그대로 돌아오고, 쓰기도 그 파일로 간다.

⚠ 이 모듈은 **무거운 것을 import 하지 않는다**(os·tempfile 뿐). `run.py` 의 위생
블록은 `pytmuxlib` 을 물기 **전에** 돌아야 하고, 그 자리에서 하는 import 가 매달리면
스위트가 첫 출력도 없이 멈추던 자리이기 때문이다(`run.py` §_STARTUP_TIMEOUT).
"""
import os
import tempfile

# 이 프로세스(와 그 자식들)에서 이미 격리를 세웠다는 표식. `run.py` 와 `harness.py`
# 둘 다 부르지만 파일은 **한 장**이면 되고, `test_ptyshot` 처럼 자식 프로세스를 띄우는
# 테스트에서는 그 자식이 부모의 파일을 그대로 물려받아야 한다(env 상속).
_MARK = "PYTMUX_TEST_CONFIG_ISOLATED"

_HEADER = (
    "# pytmux 테스트 스위트가 만든 빈 설정 파일(tests/hermetic.py).\n"
    "# 스위트가 사용자의 ~/.config/pytmux/config 를 읽지도 쓰지도 않게 하는 자리다.\n"
)


def isolate_config() -> str:
    """`PYTMUX_CONFIG` 를 **빈 임시 파일로 세운다**(거두는 것이 아니라 세우는 것 —
    거두면 또 사용자의 진짜 파일로 떨어진다). 세운 경로를 돌려준다.

    멱등: 이미 이 프로세스(또는 부모)가 세웠으면 그 경로를 그대로 돌려준다.
    ⛔ 개발자 셸이 `PYTMUX_CONFIG` 를 export 해 뒀어도 **덮어쓴다** — `PYTMUX_HOME`
    을 무조건 거두는 것과 같은 규율이다. 그 값을 존중하면 위생이 셸 상태에 달린다.
    """
    cur = os.environ.get("PYTMUX_CONFIG")
    if os.environ.get(_MARK) == "1" and cur and os.path.isfile(cur):
        return cur
    path = os.path.join(tempfile.mkdtemp(prefix="pytmux-test-config-"), "config")
    with open(path, "w", encoding="utf-8") as f:
        f.write(_HEADER)
    os.environ["PYTMUX_CONFIG"] = path
    os.environ[_MARK] = "1"
    return path
