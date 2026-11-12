#!/usr/bin/env python3
"""정본 설정 판의 **기본값**과 서버 줄이 **실제로 내보내는 것**을 픽스처로 뽑는다 (pytmux-568).

# 왜 필요한가

패리티 래칫은 «그 이름의 설정 줄이 있나»만 센다. 값이 행동에 닿는지는 사람이 한 번 훑은 것뿐이었고
(`mouse-drag-threshold` 가 그렇게 Done 으로 세지면서 GUI 에서는 아무 데도 안 읽혔다 · 기본값도 3 대 1),
다음 회귀를 아무도 안 잰다. 이 픽스처는 그 자의 정본 절반이다:

- `defaults` — 빈 설정 파일로 지은 정본 클라의 `setting_current(key)`(설정 판이 처음 보이는 값).
  서버가 주인인 줄은 `null`(정본도 서버 회신 전에는 «미상» 이다).
- `server_frames` — 서버 줄마다 값(bool = on/off · enum = choices)으로 `apply_setting(desc, v)` 를
  부르고 `send_cmd` 를 기록 스텁으로 갈아 끼워 **나간 dict** 를 적는다. GUI 설정 판이 같은 값을 골랐을 때
  같은 dict 를 내는지 Rust 시험이 JSON 그대로 맞댄다.
- `rows` — SETTINGS 38줄의 키·종류·backend(모집단).

재구현이 아니라 호출이라 정본이 기본값이나 명령을 바꾸면 이 픽스처가 따라 바뀌고 GUI 시험이 운다.
앱은 **띄우지 않는다** — `build_client_app(...)` 로 짓기만 해도 `setting_current`·`apply_setting` 이 답한다
(스크래치 `PYTMUX_HOME` · 빈 `PYTMUX_CONFIG` · `LANG=C` 를 이 프로세스가 스스로 세운다).

    python3 scripts/gen_setting_effects.py [--pytmux ..]
"""

import argparse
import json
import os
import sys
import tempfile

OUT = os.path.join("crates", "proto", "tests", "fixtures", "setting_effects.json")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()
    root = os.path.abspath(args.pytmux)

    scratch = tempfile.mkdtemp(prefix="pytmux-setting-effects-")
    cfg_path = os.path.join(scratch, "config")
    open(cfg_path, "w").close()
    # ⛔ 이 상자의 진짜 상태·설정을 읽지도 쓰지도 않는다 — import 전에 세운다.
    os.environ["PYTMUX_HOME"] = scratch
    os.environ["PYTMUX_CONFIG"] = cfg_path
    os.environ.pop("NO_COLOR", None)
    for var in ("LANG", "LC_ALL", "LC_MESSAGES"):
        os.environ[var] = "C"
    sys.path.insert(0, root)
    from pytmuxlib import clientutil as cu, keymap
    from pytmuxlib.client import build_client_app

    app = build_client_app(os.path.join(scratch, "x.sock"), keymap.load_config(cfg_path))
    defaults = {d["key"]: app.setting_current(d["key"]) for d in cu.SETTINGS}

    sent = []
    app.send_cmd = lambda action, **kw: sent.append({"action": action, **kw})
    app.display_message = lambda *a, **k: None
    app._config_path = os.path.join(scratch, "written")
    frames = {}
    for desc in cu.SETTINGS:
        if desc.get("backend") != "server":
            continue
        values = ["on", "off"] if desc["type"] == "bool" else list(desc.get("choices") or [])
        per = {}
        for value in values:
            sent.clear()
            app.apply_setting(desc, value)
            if len(sent) != 1:
                sys.exit(f"{desc['key']}={value}: 정본이 프레임을 {len(sent)}개 냈다 — 모양이 바뀌었다")
            per[value] = sent[0]
        frames[desc["key"]] = per
    if len(frames) < 10:
        sys.exit("서버 설정 줄이 거의 안 잡혔다 — 정본 SETTINGS 의 backend 이름이 바뀌었을 것이다")

    payload = {
        "_comment": "python3 scripts/gen_setting_effects.py 로 생성. 출처 = 정본 "
                    "pytmuxlib/client.py PytmuxApp.setting_current · apply_setting 을 빈 설정으로 지은 "
                    "앱에 대고 직접 부른 값(서버 줄은 send_cmd 를 기록).",
        "rows": [{"key": d["key"], "type": d["type"], "backend": d.get("backend", "")}
                 for d in cu.SETTINGS],
        "defaults": defaults,
        "server_frames": frames,
    }
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2, sort_keys=True)
        fp.write("\n")
    print(f"{len(payload['rows'])} 줄 · 서버 {len(frames)} 줄 → {args.out}")


if __name__ == "__main__":
    main()
