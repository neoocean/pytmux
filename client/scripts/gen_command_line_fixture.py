#!/usr/bin/env python3
"""명령 **줄** 픽스처 — 같은 명령 줄을 두 클라가 같은 일로 돌리나(pytmux-572).

# 왜 이 자가 필요한가

`bind x send-keys C-c` · `if-shell "test -f x" "send-keys foo"` · `set-hook … "display-message hi"`
— 설정 파일과 훅은 **명령 줄 한 줄**을 건넨다. 정본은 그 줄을 `_run_command` 한 함수로
돌리고(`pytmuxlib/clientcmd.py`), GUI 는 `base::cmdline::run_line` 한 함수로 돌린다.
이름 표(`command_alias_conformance`)는 «그 이름을 받나» 만 재므로 **인자**가 무엇을
바꾸는지는 아무도 안 쟀다.

# 무엇을 뽑나

대표 줄마다 정본 `_CommandMixin._run_command` 를 **그대로 불러** 그 줄이 낸 효과를 순서대로
싣는다. 효과의 갈래:

- `wire` — 서버로 나가는 명령 프레임(`send_cmd(action, **kw)` → `{"action": …, …kw}`).
- `input` — 활성 패널로 나가는 바이트(`send_input`, 16진).
- `shell` — `run-shell` 이 띄울 셸 명령(`_run_shell`).
- `if` — `if-shell` 의 조건·then·else(`_if_shell`).
- `reload` — `source-file` 이 다시 읽는 경로(`reload_config`, 없으면 `null`).
- `set` — 런타임 `set` 이 닿는 옵션과 값(`apply_option`).
- `local` — 그 밖에 클라 안에서 끝나는 일(화면·알림 등 — 부른 메서드 이름).

⚠ 손으로 적는 것은 **입력**(`CASES` 의 줄)뿐이다. 기대값은 전부 정본이 낸다. 정본의 명령
갈래(`client_surface.json` 의 `client_cmd_groups` 와 같은 출처 — `_run_command` 의 `c in (…)`)
마다 대표 줄이 하나도 없으면 **실패로 떨어진다** — 조용히 모집단 밖에 남는 길을 막는다.

    python scripts/gen_command_line_fixture.py [--pytmux ..] [--out …]
"""

import argparse
import ast
import json
import os
import sys
import tempfile

# (열쇠, 줄). 열쇠로 찾는다(줄 번호로 찾지 않는다). 인자 있는 갈래는 인자 있는 줄을 반드시 싣는다.
CASES = [
    ("split -h", "split-window -h"),
    ("split bare", "split-window"),
    ("splitw -v", "splitw -v"),
    ("kill-pane", "kill-pane"),
    ("new-window", "new-window"),
    ("new-claude-tab", "new-claude-tab"),
    ("kill-tab", "kill-tab"),
    ("next-tab", "next-tab"),
    ("prev", "prev"),
    ("last", "last"),
    ("automatic-rename on", "automatic-rename on"),
    ("setw automatic-rename off", "setw automatic-rename off"),
    ("monitor-activity on", "monitor-activity on"),
    ("monitor-bell", "monitor-bell"),
    ("monitor-bell off", "monitor-bell off"),
    ("setw monitor-bell on", "setw monitor-bell on"),
    ("setw monitor-activity off", "setw monitor-activity off"),
    ("pin", "pin"),
    ("unpin", "unpin"),
    ("pin-toggle", "pin-toggle"),
    ("move-tab-left", "move-tab-left"),
    ("move-tab-last", "move-tab-last"),
    ("move-tab 2", "move-tab 2"),
    ("swap-tab 3", "swap-tab 3"),
    ("choose-tree", "choose-tree"),
    ("select-pane -L", "select-pane -L"),
    ("select-pane -T", "select-pane -T build log"),
    ("rename-pane", "rename-pane build"),
    ("select-tab 3", "select-tab 3"),
    ("select-tab -1", "select-tab -1"),
    ("rename-tab work", "rename-tab work"),
    ("resize-pane -Z", "resize-pane -Z"),
    ("resize-pane -L", "resize-pane -L"),
    ("resize-pane -D 5", "resize-pane -D 5"),
    ("zoom", "zoom"),
    ("select-layout preset", "select-layout even-horizontal"),
    ("select-layout", "select-layout"),
    ("next-layout", "next-layout"),
    ("rotate-window", "rotate-window"),
    ("rotate-window -D", "rotate-window -D"),
    ("swap-pane -U", "swap-pane -U"),
    ("swap-pane -D", "swap-pane -D"),
    ("swap-pane -t", "swap-pane -t 1"),
    ("break-pane", "break-pane"),
    ("join-pane -h", "join-pane -h"),
    ("respawn-pane", "respawn-pane"),
    ("synchronize-panes on", "synchronize-panes on"),
    ("setw synchronize-panes off", "setw synchronize-panes off"),
    ("syncp", "syncp"),
    ("pane-border-status on", "pane-border-status on"),
    ("setw pane-border-status off", "setw pane-border-status off"),
    ("inactive-dim off", "inactive-dim off"),
    ("strip-box-drawing on", "strip-box-drawing on"),
    ("inactive-dim-ratio", "inactive-dim-ratio 0.25"),
    ("settings", "settings"),
    ("detach -a", "detach -a"),
    ("detach", "detach"),
    ("kill-server", "kill-server"),
    ("remote-attach", "remote-attach box1"),
    ("remote-attach via", "remote-attach C via B"),
    ("remote-new-tab", "remote-new-tab box1"),
    ("remote-detach host", "remote-detach box1"),
    ("remote-detach", "remote-detach"),
    ("merge-remote-tab", "merge-remote-tab"),
    ("restart-server", "restart-server"),
    ("restart-check", "restart-check"),
    ("restart-all", "restart-all"),
    ("reconnect", "reconnect"),
    ("redraw", "redraw"),
    ("paste-clipboard", "paste-clipboard"),
    ("send-keys C-c", "send-keys C-c"),
    ("send-keys words", "send-keys ls Enter"),
    ("send-keys -l", "send-keys -l Enter"),
    ("send-escape", "send-escape"),
    ("paste-buffer 2", "paste-buffer 2"),
    ("paste-buffer", "paste-buffer"),
    ("capture-pane -S", "capture-pane -S"),
    ("capture-pane", "capture-pane"),
    ("pipe-pane", "pipe-pane cat"),
    ("save-layout", "save-layout"),
    ("restore-layout", "restore-layout"),
    ("layout-save dev", "layout-save dev"),
    ("layout-load dev", "layout-load dev"),
    ("layout-load -n dev", "layout-load -n dev"),
    ("layout-load-new dev", "layout-load-new dev"),
    ("layout-list", "layout-list"),
    ("choose-buffer", "choose-buffer"),
    ("clear-history", "clear-history"),
    ("single-border on", "single-border on"),
    ("coalesce-repaints off", "coalesce-repaints off"),
    ("win-mouse-motion on", "win-mouse-motion on"),
    ("nest-auto-attach off", "nest-auto-attach off"),
    ("exit-empty on", "exit-empty on"),
    ("window-size latest", "window-size latest"),
    ("window-size", "window-size"),
    ("vt-parser native", "vt-parser native"),
    ("lang en", "lang en"),
    ("version", "version"),
    ("display-popup", "display-popup -w 80 -h 20 htop"),
    ("display-popup -C", "display-popup -C"),
    ("popup-close", "popup-close"),
    ("source-file path", "source-file /tmp/x.conf"),
    ("source-file", "source-file"),
    ("set", "set status-bg red"),
    ("show-options", "show-options"),
    ("set-hook", "set-hook after-new-window display-message hi"),
    ("display-message", "display-message hello world"),
    ("debug-stats", "debug-stats"),
    ("show-hooks", "show-hooks"),
    ("bind-key", "bind-key x send-keys C-c"),
    ("unbind-key", "unbind-key x"),
    ("list-keys", "list-keys"),
    ("plugins", "plugins"),
    ("help", "help"),
    ("run-shell", "run-shell 'echo hi'"),
    ("run-shell -b", "run-shell -b 'echo hi'"),
    ("run-shell words", "run-shell echo hi there"),
    ("if-shell", "if-shell 'test -f x' 'display-message yes' 'display-message no'"),
    ("if-shell then", "if-shell true 'send-keys foo'"),
    ("version alias", "about"),
    ("killp", "killp"),
    ("killw", "killw"),
    ("detach-client", "detach-client"),
]

# 활성 탭이 **원격**인 판(pytmux-624 · 572 의 pin) — `_active_remote_host()` = "box1",
# 활성 탭 = 병합 전역 index 3. 열쇠는 위 표와 안 겹친다.
REMOTE = {"host": "box1", "active": 3}
REMOTE_CASES = [
    ("kill-tab remote", "kill-tab"),
    ("killw remote", "killw"),
    ("pin remote", "pin"),
    ("unpin remote", "unpin"),
    ("pin-toggle remote", "pin-toggle"),
]

# 메뉴(`prefix Enter` · 우클릭)에서 고른 줄 — 정본 `PytmuxApp._run_menu_action(key)` 을 그대로
# 부른다(pytmux-624: «확인을 거치는 입구 / 바로 나가는 입구»의 메뉴 칸). 확인 판은
# `confirm_kill_tab`·`confirm_kill_server`·`open_prompt("confirm", …)` 으로 남는다.
MENU_KEYS = ["kill_pane", "kill_window", "kill_server", "detach"]

# 효과로 따로 싣는 메서드(나머지는 `local`). 값은 효과를 짓는 함수.
_FIXED = 0


def _client_cmd_groups(root):
    """`_run_command` 의 `c in (…)` · `c == "…"` 갈래 — 이름 집합들(생성기
    `gen_client_surface_fixture.py` 와 같은 출처)."""
    path = os.path.join(root, "pytmuxlib", "clientcmd.py")
    with open(path, encoding="utf-8") as fp:
        tree = ast.parse(fp.read(), filename=path)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_command")
    groups = []
    for sub in ast.walk(fn):
        if not isinstance(sub, ast.Compare):
            continue
        if not (isinstance(sub.left, ast.Name) and sub.left.id == "c"):
            continue
        for op, comp in zip(sub.ops, sub.comparators):
            if isinstance(op, ast.Eq) and isinstance(comp, ast.Constant):
                groups.append({comp.value})
            elif isinstance(op, ast.In) and isinstance(comp, ast.Tuple):
                groups.append({e.value for e in comp.elts
                               if isinstance(e, ast.Constant)})
    return [g for g in groups if g]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(".."))
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "command_lines.json"))
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)

    with tempfile.TemporaryDirectory() as tmp:
        # ⛔ 정본 모듈이 import 때 상태 디렉터리를 만들 수 있다 — 격리한다.
        home = os.path.join(tmp, "home")
        os.makedirs(home)
        os.environ["PYTMUX_HOME"] = home
        os.environ.pop("PYTMUX_CONFIG", None)
        os.environ["XDG_CONFIG_HOME"] = os.path.join(tmp, "xdg")
        sys.path.insert(0, root)
        from pytmuxlib import clientcmd  # noqa: E402
        from pytmuxlib.clientio import _InputMixin as _IOMixin  # noqa: E402
        from pytmuxlib.client import _ChooseScreensMixin, _ClipboardMixin  # noqa: E402
        from pytmuxlib.clientconn import _RestartVersionMixin  # noqa: E402

        # `debug-stats` 는 이 프로세스의 살림을 잰다 — 재는 것은 우리 몫이 아니라 막아 둔다.
        from pytmuxlib import clientdiag  # noqa: E402
        clientdiag.collect_stats = lambda *a, **k: {}
        clientdiag.render = lambda *a, **k: []

        log = []

        class _Plugins:
            commands = []
            command_options = {}
            noarg = set()

            def handle_command(self, app, c, a):
                return False

            def settings(self):
                return []

        class _Status:
            windows = [{"index": i, "name": f"w{i}", "active": i == 0} for i in range(5)]
            bg = fg = ""

        class _RemoteStatus:
            windows = [{"index": i, "name": f"w{i}", "active": i == REMOTE["active"]}
                       for i in range(5)]
            bg = fg = ""

        class _Tabbar:
            tabs = list(range(5))

        class Rec(clientcmd._CommandMixin):
            aliases = {}
            hooks = {}
            bindings = {}
            root_bindings = {}
            plugins = _Plugins()
            status = _Status()
            tabbar = _Tabbar()
            layout = {"active": 11, "panes": [{"id": 11}, {"id": 12}, {"id": 13}]}
            inactive_dim = True
            inactive_dim_ratio = 0.18
            strip_box_drawing = True
            single_border_on = False
            prefix_key = "ctrl+b"
            claude_command = "claude"
            sock_path = os.path.join(tmp, "sock")
            lang = "ko"

            _tab_target_index = _IOMixin._tab_target_index
            _pane_id_by_index = _IOMixin._pane_id_by_index
            # 정본에서 **서버에 묻는** 도우미는 진짜 몸통을 빌린다 — 스텁이 이름만 적으면
            # 「무엇이 나가나」를 못 잰다(첫 회차에 `choose-tree` · `version` 이 그렇게 비었다).
            request_tree = _ChooseScreensMixin.request_tree
            request_layouts = _ChooseScreensMixin.request_layouts
            choose_buffer = _ClipboardMixin.choose_buffer
            open_version = _RestartVersionMixin.open_version
            open_restart_check = _RestartVersionMixin.open_restart_check
            begin_restart = _RestartVersionMixin.begin_restart

            def send_cmd(self, action, **kw):
                frame = {"action": action}
                frame.update({k: v for k, v in kw.items() if v is not None})
                log.append({"wire": frame})

            def send_input(self, data):
                log.append({"input": data.hex()})

            def _run_shell(self, cmd):
                log.append({"shell": cmd})

            def _if_shell(self, cond, then_cmd, else_cmd=None):
                log.append({"if": [cond, then_cmd, else_cmd]})

            def reload_config(self, path=None):
                log.append({"reload": path})

            def apply_option(self, name, value):
                log.append({"set": [name, value]})

            def _active_remote_host(self):
                return None

            def _active_tab_index(self):
                return 0

            def _composite(self):
                pass

            def refresh(self, *a, **k):
                pass

            def push_screen(self, screen, *a, **k):
                log.append({"local": f"screen:{type(screen).__name__}"})

            def display_message(self, text, *a, **k):
                log.append({"local": "message"})

            def __getattr__(self, name):
                if name.startswith("__"):
                    raise AttributeError(name)

                def _rec(*a, **k):
                    log.append({"local": name})
                return _rec

        class RemoteRec(Rec):
            status = _RemoteStatus()

            def _active_remote_host(self):
                return REMOTE["host"]

            def _active_tab_index(self):
                return REMOTE["active"]

        def fresh(cls):
            log.clear()
            app = cls()
            app.aliases = {}
            app.hooks = {}
            app.bindings = {}
            app.root_bindings = {}
            return app

        cases = []
        for ctx, cls, table in ((None, Rec, CASES), ("remote", RemoteRec, REMOTE_CASES)):
            for key, line in table:
                app = fresh(cls)
                try:
                    app._run_command(line)
                except Exception as e:  # noqa: BLE001
                    sys.exit(f"{line!r} 를 정본이 돌리다 넘어졌다 — 스텁을 고칠 것: {e!r}")
                case = {"key": key, "line": line, "effects": list(log)}
                if ctx:
                    case["ctx"] = ctx
                cases.append(case)

        # 메뉴 칸 — 정본 앱 클래스의 `_run_menu_action` 몸통을 스텁 위에서 부른다.
        from pytmuxlib.client import build_client_app  # noqa: E402
        app_cls = type(build_client_app(os.path.join(tmp, "menu.sock")))
        menu = []
        for key in MENU_KEYS:
            app = fresh(Rec)
            app.open_prompt = lambda kind, *a, **k: log.append({"local": f"prompt:{kind}"})
            try:
                app_cls._run_menu_action(app, key)
            except Exception as e:  # noqa: BLE001
                sys.exit(f"메뉴 {key!r} 를 정본이 돌리다 넘어졌다 — 스텁을 고칠 것: {e!r}")
            menu.append({"key": key, "effects": list(log)})

    # 모집단 — 정본 갈래마다 대표 줄이 있어야 한다.
    heads = {line.split()[0] for _, line in CASES}
    groups = _client_cmd_groups(root)
    if not groups:
        sys.exit("정본 갈래를 하나도 못 찾았다 — 뽑는 방법이 틀렸다")
    missing = sorted(sorted(g)[0] for g in groups if not (g & heads))
    if missing:
        sys.exit(f"정본 갈래 {missing} 에 대표 줄이 없다 — CASES 에 더할 것")
    keys = [k for k, _ in CASES + REMOTE_CASES]
    if len(set(keys)) != len(keys):
        sys.exit("CASES 에 같은 열쇠가 두 번 섰다")

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "_comment": "python3 scripts/gen_command_line_fixture.py 로 생성. "
                        "출처 = pytmuxlib/clientcmd.py::_CommandMixin._run_command(호출). "
                        "pytmux-572.",
            "cases": cases,
            "menu": menu,
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"명령 줄 {len(cases)} · 정본 갈래 {len(groups)} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
