"""공개 미러 위생 게이트(scripts/check_mirror.py)의 **유출 스캐너**.

왜 테스트하나: 이 게이트는 2026-08-01 에 **자기 사각지대에 물렸다.** `build/` 의 배포
이진 둘이 나란히 rustc 가 박은 절대 소스경로를 품고 있었는데, 스캐너가 확장자로 거르는
바람에 확장자 없는 macOS 것만 걸리고 `.exe` 는 조용히 통과했다. 확장자 유무가 판정을
가르면 그것은 게이트가 아니다 — 그런데 그 결함은 **아무 오라클도 재고 있지 않았다**
(`scripts/check_mirror.py` 에는 테스트가 한 줄도 없었다).

여기서 재는 것은 둘이다:
  ⑴ **양성** — 유출 모양은 텍스트든 이진이든 잡힌다(사각지대 재발 방지).
  ⑵ **음성** — 자리표시자는 안 잡힌다. 선재 적색만 만드는 규칙은 게이트가 아니라
     소음이고, 소음이 된 게이트는 곧 꺼진다(모듈 주석의 「소음이 된 게이트」).
"""
import contextlib
import importlib.util
import io
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "check_mirror", os.path.join(os.path.dirname(HERE), "scripts",
                                 "check_mirror.py"))
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


def _leak(*parts):
    """규칙에 걸리는 표본을 **조각으로 짜서** 만든다.

    ⑤ 는 미러로 갈 파일을 전부 재고 그 안에는 **이 파일도 있다.** 표본을 리터럴로 적으면
    스캐너가 자기 오라클을 물어 저장소가 상시 적색이 되고, 그렇다고 이 파일 경로를 예외로
    빼면 게이트에 무엇이든 숨길 수 있는 구멍이 생긴다(예외는 규칙보다 오래 산다).
    그래서 예외 대신 **런타임에 잇는다** — 파일에는 걸릴 리터럴이 없고 스캐너가 실제로
    보는 값은 온전한 모양이다.

    이름(`alice`·`bob`·`devbox`)은 **이 상자의 것이 아니다.** 규칙은 값이 아니라 모양으로
    물기 때문에 가짜 이름으로도 똑같이 재진다. 실측값을 적어 둘 자리는 코드가 아니라
    `client/build/README.md` 의 표다(거기도 자리표시자로 적는다 — 같은 이유).
    """
    return "".join(parts)


def _write(tmp_path, name, data):
    path = os.path.join(tmp_path, name)
    with open(path, "wb") as fh:
        fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
    return path


def _tmpdir():
    return tempfile.mkdtemp(prefix="pytmux-mirror-")


async def test_scan_catches_leak_shapes_in_text():
    """텍스트 픽스처의 실 경로 — 첫 푸시에서 실제로 걸린 모양(`prompt_box*.json`).

    모양을 전부 잰다: 홈 아래 워크스페이스(macOS·Linux) · depot 절대경로(Windows) ·
    cargo 홈(세 OS). cargo 홈이 이진에서 압도적으로 많았다(329·1013건).

    **세 OS 를 다 재는 것이 요점이다.** 2026-08-01 의 결함은 한 OS 산출물만 재고 있던
    것이었고, 지금은 CI 가 Linux 이진까지 굽는다(`release-binaries.yml`) — 재는 자가
    OS 마다 다르면 안 재는 OS 가 곧 사각지대다.
    """
    tmp = _tmpdir()
    cases = {
        "mac_ws.json": _leak('"/Users/', "alice", '/p4/playground/scripts/pytmux"'),
        "win_depot.json": _leak(r'"D:\\', "perforce", r'\\devbox\\alice\\scripts"'),
        "mac_cargo.txt": _leak("/Users/", "alice", "/.cargo/registry/src/foo/lib.rs"),
        "win_cargo.txt": _leak(r"C:\Users\bob", "\\", r".cargo\registry\src\foo\lib.rs"),
        "linux_cargo.txt": _leak("/home/", "runner", "/.cargo/registry/src/foo/lib.rs"),
        "linux_ws.txt": _leak("/home/", "alice", "/p4/playground/scripts/pytmux"),
        # ☠ 검수 2026-09-05 C-1 — **사설망 호스트의 위치**(테일스케일/CGNAT). 실제로
        # `CLAUDE.md`·`qa/README.md` 에 트래커 웹·**무인증** MCP JSON-RPC·p4 서버의
        # 주소가 들어가 있었고 그 둘은 gitignore 밖이다. 100.x 는 인터넷 라우팅이 안
        # 되지만 「어디에 무엇이 떠 있는지」는 그 자체로 값이다.
        "tailscale_web.md": _leak("http://100.", "79.188.26", ":8086/d/pytmux/x"),
        "tailscale_mcp.md": _leak("http://100.", "127.0.1", ":18787/"),
        "tailscale_p4.txt": _leak("ssl:100.", "64.9.9", ":1666"),
    }
    for name, text in cases.items():
        assert cm._scan(_write(tmp, name, text)) is not None, name


async def test_scan_does_not_skip_binaries_by_extension():
    """**사각지대 자체를 겨눈 오라클.**

    2026-08-01 의 결함은 "이진을 못 읽는다"가 아니라 "확장자로 건너뛴다"였다. 그래서
    같은 바이트를 이름만 바꿔 네 번 잰다 — `.exe` 든 확장자가 없든 **같은 판정**이어야
    한다. `SKIP_EXT` 류의 확장자 목록이 다시 들어오면 여기서 죽는다.

    실 이진처럼 UTF-8 로 못 읽는 바이트를 앞뒤에 섞는다 — 스캐너가 `latin-1` 로 펴는
    이유다(`errors="ignore"` 로 읽던 종전 코드도 여기서는 통과한다. 여기서 죽는 것은
    **확장자 스킵**이고, 그것이 실제로 물렸던 결함이다).
    """
    blob = (b"\x00\xff\xfe\x7fMZ\x90\x00"
            + _leak(r"C:\Users\bob", "\\", r".cargo\registry\src\foo\lib.rs").encode()
            + b"\x00\xc3\x28\xff")
    tmp = _tmpdir()
    for name in ("pytmux-gui-windows-x64.exe", "pytmux-gui-macos-arm64",
                 "thing.dylib", "thing.bin"):
        assert cm._scan(_write(tmp, name, blob)) is not None, name


async def test_scan_lets_placeholders_through():
    """자리표시자와 상류 픽스처는 안 문다 — 소음이 되면 게이트가 꺼진다.

    표본은 **저장소에 실재하는 것**으로 골랐다: `C:\\Users\\John Doe`(상류 warpui_core
    클립보드 테스트) · `C:\\Users\\woojin\\Documents`(`tests/test_nc.py` 의 경로 표본) ·
    문서가 쓰는 `/Users/me` 류. 이것들을 물면 게이트가 저장소를 통째로 적색으로 만든다.
    """
    tmp = _tmpdir()
    cases = {
        "doc1.md": "/Users/me/p4/playground · /Users/x/p4 · /Users/<계정>/.cargo",
        "doc2.md": r"C:\Users\me\.cargo · C:\Users\<계정>\AppData\Local",
        "upstream.rs": r'file://C:\Users\John%20Doe\Desktop\vacation-photo.png',
        "nc.py": r'r"C:\Users\woojin\Documents"',
        "plain.py": "그냥 코드 한 줄 — 경로가 없다",
        # 문서용 주소(RFC 5737)와 CGNAT **밖**의 100.x 는 안 문다 — 시험이 「루프백이
        # 아닌 것」을 적을 자리가 남아 있어야 한다(안 남기면 게이트가 소음이 된다).
        "docaddr.py": "198.51.100.7 · 192.0.2.9 · 203.0.113.4 · 100.1.2.3",
        "version.txt": "100.128.0.1 · 100.63.255.255 · 진행률 100.0%",
    }
    for name, text in cases.items():
        assert cm._scan(_write(tmp, name, text)) is None, (name, text)


async def test_docs_verdict_skips_on_mirror_but_not_in_canon():
    """② 는 **어디서 재는가**에 따라 갈린다 — 네 조합을 전부 고정한다.

    첫 미러 푸시(2026-08-01) 직후 이 게이트가 CI 에서만 붉었다: 클라 문서를 일부러
    미러에서 뺐는데, 판정이 "없으면 고장"이라 **제외가 성공한 것을 고장으로 읽었다.**
    없는 것이 정답인 자리(미러 체크아웃)와 없으면 안 되는 자리(정본 워크스페이스)를
    `docs/internal/` 의 존재로 가른다.

    ⚠ 재는 **자리**는 §10-17 로 옮겨갔다(`client/docs/` → `docs/internal/client/`).
    판정 로직은 그대로라 이 네 칸도 그대로다 — 바뀐 것은 무엇을 `present` 로 넘기느냐
    뿐이고 그건 `main()` 의 몫이다. 그래서 여기서는 **메시지가 새 자리를 가리키는지**
    까지 고정한다(옛 경로를 가리키면 다음 사람이 빈 `client/docs/` 를 보러 간다).

    **양성 두 개가 요점이다** — 건너뛰기만 재면 "판정을 통째로 지워도 통과"가
    성립한다(그 변이는 아래 `canon=True` 두 줄에서 죽는다).
    """
    # 정본 워크스페이스: 있고 무시된다 = 정상.
    assert cm.docs_verdict(present=True, is_ignored=True, canon=True)[0] is None
    # 정본 워크스페이스: 있는데 안 무시된다 = 유출 직전.
    kind, msg = cm.docs_verdict(present=True, is_ignored=False, canon=True)
    assert kind == "problem", msg
    assert "올라간다" in msg, msg
    assert "docs/internal/client" in msg, msg
    # 정본 워크스페이스: 없다 = 잴 것이 사라졌다(고장).
    kind, msg = cm.docs_verdict(present=False, is_ignored=True, canon=True)
    assert kind == "problem", msg
    assert "잴 것이 없으면" in msg, msg
    # 미러 체크아웃: 없다 = **정답**이라 건너뛴다(사유와 함께).
    kind, msg = cm.docs_verdict(present=False, is_ignored=True, canon=False)
    assert kind == "skip", msg
    assert "미러 체크아웃" in msg, msg


async def test_scan_paths_reports_and_fails():
    """`--scan` 이 **rc 와 경로 둘 다** 낸다.

    이 모드는 `client/scripts/build_release.*` 가 갓 구운 이진을 `build/` 에 넣기 전에
    부른다. rc 만 맞고 어느 파일인지 안 찍으면 굽는 사람이 다음에 무엇을 할지 모른다.
    """
    tmp = _tmpdir()
    bad = _write(tmp, "bad.bin",
                 b"\x00" + _leak("/Users/", "alice", "/.cargo/registry").encode()
                 + b"\x00")
    good = _write(tmp, "good.txt", "깨끗하다")

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        clean_rc = cm.scan_paths([good])
        dirty_rc = cm.scan_paths([bad, good])
    out = buf.getvalue()
    assert clean_rc == 0, out
    assert dirty_rc == 1, out
    assert "bad.bin" in out, out


async def test_scan_with_nothing_to_scan_is_a_breakage_not_a_pass():
    """인자 0개면 **rc 1**. 「0개 파일을 쟀고 유출이 0이다」는 통과가 아니다.

    부르는 자리가 `build_release.*` 라 값이 크다 — 빌드가 산출물을 못 냈거나 경로
    확장이 비면, 종전에는 그 빌드가 **아무 검사도 없이** 초록으로 `build/` 에 들어갔다.
    같은 저장소의 `check_licenses.sh` 는 같은 상황(한 줄도 못 잡음)을 이미 「고장」으로
    본다 — 한 질문을 두 술어로 묻던 자리다(검수 2026-08-09 B-5).
    """
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = cm.scan_paths([])
    assert rc == 1, err.getvalue()
    assert "고장" in err.getvalue(), err.getvalue()
