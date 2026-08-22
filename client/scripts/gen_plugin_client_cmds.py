#!/usr/bin/env python3
"""정본에서 **클라가 스스로 처리하는 플러그인 명령**을 뽑아 픽스처로 남긴다.

# 왜 이 자가 필요한가 (§10-21ⓡ)

제보: `close-clock`·`close-calendar` 를 쳐도 아무 일이 없다. 원인은 GUI 가 **모든**
플러그인 명령을 `plugin_open`(서버야, 화면을 다오)으로 보내기 때문이다 — 이 셋은 화면을
여는 명령이 아니라 **상태를 바꾸는 명령**이라 그 경로가 통째로 틀렸다. 서버는 "이
플러그인은 화면 스펙을 제공하지 않습니다"로 거절하고, 사용자에게는 죽은 명령으로 보인다.

제보에 적힌 다음 문장이 이 파일의 존재 이유다: **"이 부류(보이는데 안 먹는 명령)가 이것만
인지 전수로 재 볼 것."** 눈으로 세면 다음에 또 샌다.

# 어떻게 재나

정본 클라의 판정을 **그대로 부른다**: 플러그인마다 `handle_command(app, name, [])` 를
스텁 앱에 대고 불러 `True` 를 돌려주면 "클라가 자기 손으로 처리하는 명령"이다. 이름
목록을 손으로 적지 않는 이유는 이 저장소의 규율 그대로다 — 정본이 움직이면 픽스처가
움직여야 하고, 그 차이를 게이트가 운다.

⚠ 스텁 앱은 `MagicMock` 이라 **어떤 속성이든 있다**. 그래서 핸들러가 무엇을 만지든
예외 없이 끝나고, 우리가 보는 것은 오직 **반환값**이다(부작용은 목에만 남는다).

⛔ **부작용이 목에만 남는 것은 아니었다** — 플러그인이 executor 로 내보내는 일은 진짜
프로세스에서 돌아 이 상자의 홈을 훑었고, 그것이 게이트를 47분 매달았다(pytmux-194).
그래서 이 파일은 오프로드를 **안 시킨다**: 아래 `no_filesystem_offload` 를 읽을 것.

출력: `crates/proto/tests/fixtures/plugin_client_cmds.json`
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import pathlib
import sys
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PYTMUX = HERE.parent.parent
DEFAULT_OUT = HERE.parent / "crates" / "proto" / "tests" / "fixtures" / "plugin_client_cmds.json"


class _NeverAwaited:
    """`run_in_executor` 가 돌려주던 자리에 놓는 **일 안 하는** awaitable.

    이 생성기가 묻는 것은 오직 "화면 스펙을 **내는가**"이고(위 §어떻게 재나), 그
    awaitable 이 **무엇을 내는지**는 한 번도 안 읽는다. 그러니 안에 든 일은 시킬 이유가
    없다 — 시키면 그 순간 픽스처 생성이 이 상자의 파일시스템 사정에 걸린다.
    """

    def __init__(self, fn):
        self.fn = fn                      # 진단용(무엇을 안 돌렸나)

    def __await__(self):                  # pragma: no cover — 아무도 안 기다린다
        raise AssertionError(
            "픽스처 생성기는 오프로드 결과를 기다리지 않는다 — 무엇을 내는지가 아니라"
            " 내는가를 묻는 자리다")
        yield                             # 이 줄이 있어야 제너레이터(=awaitable)다


class _NoOffloadLoop:
    """`asyncio.get_event_loop()` 자리에 놓는 가짜 루프.

    ⚠ **`run_in_executor` 하나만 안다.** 다른 것을 부르는 플러그인이 생기면
    `AttributeError` 로 시끄럽게 넘어지고(호출부의 `except Exception` 이 그 이름을
    "화면 없음"으로 접는다) 픽스처 골든이 그 차이를 운다 — 조용히 다른 답을 주는 목
    (`MagicMock`)보다 이 편이 낫다.
    """

    def run_in_executor(self, executor, fn, *a):
        return _NeverAwaited(fn)


@contextlib.contextmanager
def no_filesystem_offload():
    """플러그인이 executor 로 내보내는 일을 **안 시킨다**(pytmux-194).

    # 왜 필요한가

    `mdir`·`ncd` 의 화면 스펙은 디렉터리 나열이 딸려 있고, `p4-show-submitted-changelists`
    는 p4 를 부른다. 스텁이 `MagicMock` 이라 경로 해석이 엉뚱한 곳으로 떨어지는데,
    그 자들은 없는 경로를 만나면 **위로 거슬러 올라가며** 훑는다 — 실측(2026-08-09 ·
    이 맥) `os.scandir` 90회가 `/`·`/Users/<계정>`·`/Applications` 로 나갔고, 그중
    하나(Arq 백업 목적지)에서 `open()` 이 매달려 **47분 동안 0바이트**였다.

    빌드 시각의 픽스처 생성기가 그 상자의 홈을 훑을 이유는 없다. 훑는 순간 픽스처의
    운명이 그 상자의 마운트 사정에 걸린다.

    ⛔ **결과를 위조하는 것이 아니다.** 이 생성기는 awaitable 의 값을 한 번도 안 읽는다
    (`hasattr(resp, "__await__")` 하나로 센다) — 그래서 안 돌려도 픽스처는 같다.
    그 사실은 `tests/test_gen_plugin_client_cmds.py` 가 바이트로 잰다.
    """
    real = asyncio.get_event_loop
    asyncio.get_event_loop = lambda: _NoOffloadLoop()   # type: ignore[assignment]
    try:
        yield
    finally:
        asyncio.get_event_loop = real                   # type: ignore[assignment]


def collect(pytmux_root: pathlib.Path) -> dict:
    sys.path.insert(0, str(pytmux_root))
    from pytmuxlib import plugins as py_plugins

    reg = py_plugins.load()

    advertised: list[str] = []
    for plugin in reg.plugins:
        advertised.extend(row[0] for row in (getattr(plugin, "commands", None) or []))
    advertised = sorted(set(advertised))

    # 그 이름으로 **화면 스펙이 나오나** — GUI 의 `plugin_open` 경로가 살아 있는 이름이다.
    #
    # 왜 이것으로 가르나: 네이티브 클라는 플러그인 명령을 전부 "서버야, 화면을 다오"로
    # 보낸다. 화면이 안 나오는 이름은 그 경로가 **틀린** 것이고(상태를 바꾸는 명령이다),
    # 사용자에게는 죽은 명령으로 보인다. 그러니 이 목록의 여집합이 곧 **위험 목록**이다.
    with_screen: list[str] = []
    # ⛔ 오프로드된 일은 **안 시킨다**(`no_filesystem_offload` 머리말 · pytmux-194).
    #    시키면 이 생성기가 상자의 홈을 훑고, 그 훑기가 매달리면 픽스처 게이트가 통째로
    #    멈춘다. 아래 두 루프가 그 자리다(화면 스펙 · 서버 액션 둘 다 executor 로 나간다).
    with no_filesystem_offload():
        for name in advertised:
            req = {"do": "open", "name": name, "args": [], "state": {}}
            try:
                resp = reg.plugin_screen(MagicMock(), MagicMock(), req)
            except Exception:
                resp = None
            # awaitable 도 "낸다"로 센다 — 무엇을 내는지가 아니라 **내는가**를 묻는 자리다
            # (p4·파일시스템 작업은 executor 로 나가므로 코루틴이 돌아온다).
            if isinstance(resp, dict) or hasattr(resp, "__await__"):
                with_screen.append(name)
            elif hasattr(resp, "close"):
                resp.close()      # 안 기다릴 코루틴은 닫는다(경고를 남기지 않게)

    # 그 이름을 **서버가 명령으로 실행할 수 있나**(pytmux-35 · `plugin_cmd` 경로).
    #
    # 상태를 바꾸는 명령의 살길이다: 이름을 액션으로 옮기는 규칙을 플러그인이 한 벌로
    # 들고(`cmdmap`), 서버가 그것으로 푼다. 화면 스펙도 없고 여기도 없는 이름이 곧
    # **여전히 죽은 줄**이다 — 그 여집합이 래칫이 지키는 목록이다.
    #
    # ⚠ **무엇이 일어나는지는 안 잰다.** 인형(MagicMock)에 대고 실행하면 부작용이 있는
    #    액션은 예외를 낼 수 있는데, 예외는 **그 이름이 잡혔다는 뜻**이다. 우리가 묻는
    #    것은 "이 이름이 명령으로 디스패치되는가" 하나다(서버 액션 생성기와 같은 판단).
    from pytmuxlib.servercmd import _CMD_TABLE

    server_runnable: list[str] = []
    with no_filesystem_offload():
        for name in advertised:
            try:
                got = reg.plugin_command_action(name, [])
            except Exception:
                got = None
            if got is None:
                continue
            action, _kw = got
            # ★ **차례가 규칙이다** — 코어 표를 먼저 본다. 플러그인 훅에만 물으면 코어가
            #   받는 액션이 없는 것으로 보인다(`set_claude_account` 가 그렇다: 그 주인은
            #   `_CMD_TABLE` 이다). 2026-08-03 에 그렇게 오판해 산 명령을 죽은 목록에
            #   넣을 뻔했고, disposition 골든이 잡았다.
            if action in _CMD_TABLE:
                server_runnable.append(name)
                continue
            try:
                disp = reg.server_command(MagicMock(), MagicMock(), MagicMock(),
                                          action, {})
            except Exception:
                disp = "handled"  # 잡혔다(그 다음이 인형에서 넘어졌을 뿐)
            if disp is not None:
                server_runnable.append(name)

    return {
        "advertised": advertised,
        "with_screen": sorted(set(with_screen)),
        "server_runnable": sorted(set(server_runnable)),
        "stateful": sorted(set(advertised) - set(with_screen)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", type=pathlib.Path, default=DEFAULT_PYTMUX)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true", help="파일 대신 표준출력으로")
    args = ap.parse_args()

    data = collect(args.pytmux)
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if args.print:
        sys.stdout.write(text)
        return 0
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(
        f"{args.out}: 광고 {len(data['advertised'])}"
        f" · 화면 스펙 {len(data['with_screen'])}"
        f" · 서버 실행 {len(data['server_runnable'])}"
        f" · 상태형 {len(data['stateful'])}"
    )
    return 0


def exit_without_joining(rc: int):
    """일이 끝났으면 **남은 비-데몬 스레드를 안 기다리고** 나간다(pytmux-194).

    # 왜 필요한가

    인터프리터 종료는 비-데몬 스레드를 전부 `join` 한다. 즉 플러그인이 executor 로
    내보낸 일 하나가 매달리면, 출력이 다 나온 뒤에도 프로세스가 **영원히 좀비**가 된다
    (실측: `--print` 의 출력은 40초 전에 끝났는데 47분 뒤에도 살아 있었다). 그 위에
    있는 것이 픽스처 게이트고, 그 위가 합본 게이트다 — 한 스레드가 커밋 전 관문 전체를
    멈춘다.

    위 `no_filesystem_offload` 가 그 스레드를 **아예 안 만들게** 했으니 여기는 그물이다.
    ⛔ **조용한 그물이 아니다** — 살아남은 스레드가 있으면 이름을 찍는다. 조용히
    빠져나가면 "일을 안 시켰는데 시킨 줄 아는" 다음 결함이 여기 숨는다.
    """
    import threading

    stragglers = [t for t in threading.enumerate()
                  if t is not threading.main_thread() and not t.daemon and t.is_alive()]
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (ValueError, OSError):
            pass
    if not stragglers:
        return rc                       # 평범하게 끝난다(atexit·버퍼 전부 정상 경로)
    sys.stderr.write(
        "경고: 비-데몬 스레드 %d개가 남아 join 을 기다리지 않고 끝낸다 — %s\n"
        % (len(stragglers), ", ".join(sorted(t.name for t in stragglers))))
    sys.stderr.flush()
    os._exit(rc)                        # 여기서 돌아오지 않는다


if __name__ == "__main__":
    raise SystemExit(exit_without_joining(main()))
