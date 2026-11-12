#!/usr/bin/env python3
"""설정 파일에 **쓰는 입구**와 **이번 판만 바꾸는 입구**를 옵션마다 뽑는다 (pytmux-649).

정본 클라에는 설정을 바꾸는 입구가 넷이다:

- 명령 줄의 `set <옵션> <값>`(`:` 줄 · 명령 옵션 판이 지은 줄 · 바인딩·훅이 부르는 줄) — `apply_option`.
- 설정 판(`:settings`) — `apply_setting(desc, v)` = 그 줄의 명령을 돌린 뒤 `backend == "config"` 면
  `set_config_option` 으로 **파일에 쓴다**.
- 명령 옵션 판(`CommandOptionsScreen`) — 완성한 명령 줄을 돌려줄 뿐이라 명령 줄과 같은 길이다.
- 플러그인 판 — `set_plugin_enabled` 를 서버에 보내 서버의 `opts.json` 에 남긴다(설정 파일 밖).

손으로 옮기면 「어느 입구가 파일을 쓰나」가 갈린다(실제로 GUI 는 명령 줄 `set` 도 파일에 썼다).
그래서 정본을 **실제로 부른다**: 앱을 짓기만 하고(띄우지 않는다) `set_config_option` 을 기록 스텁으로
갈아 끼운 뒤, 옵션마다 ① 명령 줄 `set` 을 `_run_command` 로 ② 설정 판 줄을 `apply_setting` 으로 돌려
**쓰였나**를 적는다. 설정 판 갈래에서는 `_run_command` 도 기록 스텁이다(효과가 아니라 쓰기만 잰다).

    python3 scripts/gen_config_entrances.py [--pytmux ..]
"""

import argparse
import json
import os
import sys
import tempfile
import warnings

# 띄우지 않은 앱의 `status-interval` 이 타이머 코루틴을 만들고 버린다 — 측정과 무관한 경고다.
warnings.simplefilter("ignore", RuntimeWarning)

OUT = os.path.join("crates", "proto", "tests", "fixtures", "config_entrances.json")


def sample_value(desc):
    kind = desc.get("type")
    if kind == "bool":
        return "off"
    if desc.get("choices"):
        return list(desc["choices"])[-1]
    if kind == "ratio":
        return "0.30"
    if kind == "int":
        return "4"
    return "x"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)

    scratch = tempfile.mkdtemp(prefix="pytmux-config-entrances-")
    cfg_path = os.path.join(scratch, "config")
    open(cfg_path, "w").close()
    os.environ["PYTMUX_HOME"] = scratch
    os.environ["PYTMUX_CONFIG"] = cfg_path
    os.environ.pop("NO_COLOR", None)
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        os.environ[var] = "C"
    sys.path.insert(0, root)
    from pytmuxlib import client as cl, clientutil as cu, keymap

    app = cl.build_client_app(os.path.join(scratch, "x.sock"), keymap.load_config(cfg_path))
    writes = []
    cl.set_config_option = lambda opt, value, path=None: writes.append((opt, value)) or path
    app.send_cmd = lambda *a, **k: None
    app.display_message = lambda *a, **k: None
    app.notify = lambda *a, **k: None
    real_run = app._run_command

    rows = []
    for desc in cu.SETTINGS:
        key = desc["key"]
        value = sample_value(desc)
        # ① 명령 줄 `set` — 정본 `_run_command` 를 그대로(효과가 앱 상태를 건드려도 쓰기만 본다).
        writes.clear()
        line_error = None
        try:
            real_run(f"set {key} {value}")
        except Exception as e:  # 띄우지 않은 앱이라 위젯이 없을 수 있다 — 쓰기 판정만 남긴다
            line_error = type(e).__name__
        line_writes = bool(writes)
        # ② 설정 판 — 명령은 기록만, 쓰기는 정본 그대로.
        writes.clear()
        app._run_command = lambda _line: None
        try:
            app.apply_setting(desc, value)
        finally:
            app._run_command = real_run
        panel = [opt for opt, _ in writes]
        rows.append({
            "key": key,
            "backend": desc.get("backend", ""),
            "cmd": desc.get("cmd", ""),
            "value": value,
            "command_line_writes": line_writes,
            "command_line_error": line_error,
            "settings_panel_writes": panel,
        })
    if not any(r["settings_panel_writes"] for r in rows):
        sys.exit("설정 판이 한 줄도 안 썼다 — 기록 스텁이 안 물렸다(정본 이름이 바뀌었을 것이다)")
    payload = {
        "_comment": "python3 scripts/gen_config_entrances.py 로 생성. 출처 = 정본 client.py 의 "
                    "_run_command(\"set …\")·apply_setting 을 set_config_option 기록 스텁 위에서 부른 결과. "
                    "명령 옵션 판은 명령 줄과 같은 길이고, 플러그인 판은 서버 opts.json 이라 설정 파일 밖이다.",
        "entrances": {
            "command_line": "apply_option — 설정 파일을 안 쓴다",
            "command_options_panel": "완성한 명령 줄을 _run_command 로 — command_line 과 같다",
            "plugin_panel": "set_plugin_enabled → 서버 opts.json — 설정 파일 밖",
            "settings_panel": "apply_setting — backend == config 면 set_config_option",
        },
        "rows": rows,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
        fp.write("\n")
    n = sum(bool(r["settings_panel_writes"]) for r in rows)
    print(f"{len(rows)} 줄 · 설정 판이 쓰는 줄 {n} · 명령 줄이 쓰는 줄 "
          f"{sum(r['command_line_writes'] for r in rows)} → {args.out}")


if __name__ == "__main__":
    main()
