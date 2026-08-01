#!/usr/bin/env python3
"""엔드포인트 발견 규칙 픽스처 — 클라와 서버가 **같은 서버**를 가리키게 묶는다.

# 왜 이게 제일 중요한 표인가

규칙이 어긋나면 증상이 "연결 실패"가 아니다. 클라가 서버를 못 찾아 **새 서버를 띄우고**,
사용자는 자기 탭이 사라진 화면을 본다. `ipc.py` 가 그 위험을 문서에 적어 둔 이유이기도
하다(세션마다 `XDG_RUNTIME_DIR` 유무가 갈린다).

Windows 는 규칙이 통째로 다르다 — AF_UNIX 대신 **루프백 TCP + 포트파일**이고, 상태
디렉터리도 `%LOCALAPPDATA%\\pytmux` 다. macOS 에서 개발하면서 그 규칙을 맞추려면 표를
**서버 구현에서 뽑아** 와야 한다. 추측으로 적으면 Windows 박스에 가서야 틀린 걸 안다.

# 어떻게 뽑는가

`ipc.IS_WINDOWS` 를 켜고 함수를 그대로 부른다. 부수효과(디렉터리 생성·ACL 조이기)는
표에 필요 없으므로 임시 디렉터리와 no-op 로 막는다 — **경로 규칙만** 뽑는다.

경로는 **구성요소 배열**로 적는다. macOS 에서 뽑으면 구분자가 `/` 인데 실제 Windows 는
`\\` 라, 문자열로 적으면 그 자리에서 어긋난다. 붙이는 일은 Rust 쪽 `PathBuf` 에 맡긴다.

    python3 scripts/gen_endpoint_fixture.py [--pytmux ..]
"""

import argparse
import json
import os
import sys
import tempfile

OUT = os.path.join("crates", "proto", "tests", "fixtures",
                   "endpoints.json")

#: 픽스처 안에서 환경값을 가리키는 자리표시자. Rust 테스트가 같은 값을 넣는다.
XDG = "/xdg-run"
LOCALAPPDATA = "/localappdata"
HOME = "/pytmux-home"
UID = 501


def _components(path, subs):
    """절대경로를 구성요소 배열로. 자리표시자 치환도 함께 한다."""
    for real, placeholder in subs:
        if path == real:
            path = placeholder
        elif path.startswith(real + os.sep):
            path = placeholder + path[len(real):]
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    return parts


def _snapshot(ipc, subs):
    """지금 환경에서의 규칙 한 벌."""
    endpoints = ipc.default_endpoint_candidates()
    out = []
    for ep in endpoints:
        if ipc.is_tcp(ep):
            _, host, port = ipc.parse_endpoint(ep)
            out.append({
                "kind": "tcp",
                "host": host,
                "port": port,
                "portfile": _components(ipc.portfile_for(ep), subs),
                "token": _components(ipc.token_path(ep), subs),
            })
        else:
            out.append({
                "kind": "unix",
                "path": _components(ep, subs),
                "token": _components(ipc.token_path(ep), subs),
            })
    return out


def _utf8_stdout():
    """Windows 콘솔의 기본 코드페이지(한국어=cp949)에서 한글 출력이 죽지 않게.

    이 스크립트들은 마지막에 결과 요약을 한글로 찍는다. cp949 콘솔에서는 그 print 가
    UnicodeEncodeError 로 죽는데, **파일은 이미 다 쓴 뒤**라 종료코드 1 만 보고
    "생성 실패"로 오인하게 된다(2026-07-28 실측: 생성기 6개 전부 그랬다).
    출력 스트림만 UTF-8 로 돌린다 — 생성 결과에는 영향이 없다.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    _utf8_stdout()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(here, ".."))
    ap.add_argument("--out", default=os.path.join(here, OUT))
    args = ap.parse_args()

    root = os.path.abspath(args.pytmux)
    if not os.path.isdir(root):
        sys.exit(f"pytmux 저장소를 못 찾았다: {root}")
    sys.path.insert(0, root)
    from pytmuxlib import ipc

    # 부수효과 차단: 표에 필요한 것은 경로 규칙뿐이다.
    ipc._harden_win_acl = lambda *a, **k: None
    ipc._validate_state_dir = lambda *a, **k: None
    real_makedirs, real_chmod = os.makedirs, os.chmod
    os.makedirs = lambda *a, **k: None
    os.chmod = lambda *a, **k: None
    real_getuid = getattr(os, "getuid", None)
    os.getuid = lambda: UID

    scenarios = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            home_real = os.path.join(tmp, "home")

            def run(name, *, windows, env):
                keep = {k: os.environ.get(k)
                        for k in ("XDG_RUNTIME_DIR", "LOCALAPPDATA", "PYTMUX_HOME")}
                for k in keep:
                    os.environ.pop(k, None)
                os.environ.update({k: v for k, v in env.items() if v is not None})
                ipc.IS_WINDOWS = windows
                subs = [(os.path.abspath(home_real), HOME)]
                try:
                    # env 값도 자리표시자로 — 임시 경로가 그대로 들어가면 다시 만들
                    # 때마다 픽스처가 달라져 diff 가 잡음이 된다.
                    shown = {k: (HOME if k == "PYTMUX_HOME" else v)
                             for k, v in env.items() if v is not None}
                    scenarios.append({
                        "name": name,
                        "os": "windows" if windows else "unix",
                        "env": shown,
                        "uid": UID,
                        "candidates": _snapshot(ipc, subs),
                    })
                finally:
                    for k, v in keep.items():
                        if v is None:
                            os.environ.pop(k, None)
                        else:
                            os.environ[k] = v

            run("unix_xdg", windows=False, env={"XDG_RUNTIME_DIR": XDG})
            run("unix_tmp_fallback", windows=False, env={})
            run("unix_pytmux_home", windows=False, env={"PYTMUX_HOME": home_real})
            run("windows_localappdata", windows=True,
                env={"LOCALAPPDATA": LOCALAPPDATA})
            run("windows_pytmux_home", windows=True,
                env={"LOCALAPPDATA": LOCALAPPDATA, "PYTMUX_HOME": home_real})
    finally:
        ipc.IS_WINDOWS = os.name == "nt"
        os.makedirs, os.chmod = real_makedirs, real_chmod
        if real_getuid is not None:
            os.getuid = real_getuid

    payload = {
        "_comment": "python3 scripts/gen_endpoint_fixture.py 로 생성. 출처 = "
                    "pytmuxlib/ipc.py(default_endpoint_candidates·token_path·"
                    "portfile_for). 경로는 구성요소 배열 — 구분자는 각 OS 가 정한다.",
        "placeholders": {"xdg": XDG, "localappdata": LOCALAPPDATA, "home": HOME},
        "scenarios": scenarios,
    }
    os.makedirs = real_makedirs
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 시나리오 {len(scenarios)}개")


if __name__ == "__main__":
    main()
