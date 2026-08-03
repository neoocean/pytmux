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

출력: `crates/proto/tests/fixtures/plugin_client_cmds.json`
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from unittest.mock import MagicMock

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_PYTMUX = HERE.parent.parent
DEFAULT_OUT = HERE.parent / "crates" / "proto" / "tests" / "fixtures" / "plugin_client_cmds.json"


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
            resp.close()          # 안 기다릴 코루틴은 닫는다(경고를 남기지 않게)

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
            disp = "handled"      # 잡혔다(그 다음이 인형에서 넘어졌을 뿐)
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


if __name__ == "__main__":
    raise SystemExit(main())
