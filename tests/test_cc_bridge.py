"""pytmux-527(C1 S1 · statusline 다리) + pytmux-529(C3-1 · SessionStart 제목) 오라클.

설계 = 트래커 이슈 pytmux-527 / pytmux-529 의 설계 문서(c1 §D1–D5 · c3 §1–4). 여기서
재는 것은 c1 §관문 시험 · c3 §관문 시험의 목록 그대로다:

- 래퍼 단위 — stdin JSON → `sl/<key>.json` + 사용자 원래 statusLine 의 stdout·rc 통과.
- 서버 — sl 파일을 먹여 status 필드를 본다. **호출부 단언**: 배지가 그 값을 실제로 그린다.
- 리셋이 지나면 0% · `rate_limits` 없는 입력이면 전역 값 유지 · 옛 opts.json 이 죽지 않는다.
- opt off 면 PATH·env 에 흔적 0 · Windows 는 문서화된 무동작.
- session-start 입력 표 · 규칙 함수 한 벌 · OSC 표식이 `_pending_rename` 을 막는다 ·
  표식이 없으면 대기 뒤 폴백(실제로 `_pc_inject` 까지) · 남의 키 OSC 무시.

⛔ 실 claude 는 띄우지 않는다. 서버를 쓰는 시험은 `server_only` 격리 위에서 `_cc_dir_path`
를 임시 디렉터리로 돌린다 — 라이브 상태 디렉터리의 `cc/` 를 안 밟는다.
"""
import asyncio
import base64
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import harness
from harness import patched, server_only, teardown, wait_for
from run import skip

ccbridge = importlib.import_module("pytmuxlib.plugins.claude-code.ccbridge")
ccsl = importlib.import_module("pytmuxlib.plugins.claude-code.ccsl")
cc_plugin = importlib.import_module("pytmuxlib.plugins.claude-code")
ns = importlib.import_module("pytmuxlib.plugins.claude-name-sync")
CC = cc_plugin.PLUGIN
NS = ns.PLUGIN


def _tmp():
    return tempfile.mkdtemp(prefix="pytmux-ccb-")


def _sl_input(**over):
    d = {
        "session_id": "sess-1", "transcript_path": "/x.jsonl", "cwd": "/tmp/w",
        "model": {"id": "claude-opus-4-8", "display_name": "Opus 4.8"},
        "version": "2.1.287",
        "cost": {"total_cost_usd": 1.0},            # 부분집합에서 빠져야 한다
        "context_window": {"total_input_tokens": 9, "used_percentage": 42,
                           "remaining_percentage": 58,
                           "context_window_size": 1000000},
        "rate_limits": {"five_hour": {"used_percentage": 37.4,
                                      "resets_at": time.time() + 3600},
                        "seven_day": {"used_percentage": 12,
                                      "resets_at": time.time() + 86400}},
    }
    d.update(over)
    return d


def _env(**kv):
    e = dict(os.environ)
    e.pop(ccbridge.ENV_PANE_KEY, None)
    e.update(kv)
    return e


def _write_user_statusline(cfg, script_body, rc=0):
    """사용자 statusLine 픽스처 — 셸 문법 차이를 피하려고 파이썬 스크립트 파일로 둔다."""
    os.makedirs(cfg, exist_ok=True)
    sp = os.path.join(cfg, "usr_sl.py")
    with open(sp, "w", encoding="utf-8") as f:
        f.write(script_body)
    cmd = '"%s" "%s"' % (sys.executable, sp)
    with open(os.path.join(cfg, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"statusLine": {"type": "command", "command": cmd,
                                  "padding": 2, "refreshInterval": 5}}, f)
    return cmd


# ── 래퍼 단위 ──────────────────────────────────────────────────────────────────

async def test_wrapper_writes_subset_and_passes_user_statusline_through():
    cc, cfg = _tmp(), _tmp()
    _write_user_statusline(cfg, (
        "import sys\n"
        "d = sys.stdin.buffer.read()\n"
        "sys.stdout.write('USR:%d' % len(d))\n"
        "sys.exit(3)\n"))
    raw = json.dumps(_sl_input()).encode()
    out = io.BytesIO()
    rc = ccbridge.run_statusline(cc, raw, _env(CLAUDE_CONFIG_DIR=cfg,
                                               PYTMUX_PANE_KEY="k1"), out=out)
    # 사용자 statusline 의 stdout·종료코드가 **그대로** 나간다(같은 stdin 바이트를 받았다).
    assert rc == 3
    assert out.getvalue() == b"USR:%d" % len(raw)
    rec = json.load(open(os.path.join(cc, "sl", "k1.json"), encoding="utf-8"))
    assert rec["pane_key"] == "k1" and rec["session_id"] == "sess-1"
    assert rec["model"] == {"id": "claude-opus-4-8", "display_name": "Opus 4.8"}
    assert rec["context_window"]["used_percentage"] == 42
    assert rec["rate_limits"]["five_hour"]["used_percentage"] == 37.4
    assert rec["config_dir"] == cfg
    assert rec["claude_json"] == os.path.join(cfg, ".claude.json")
    # 원문을 통째로 담지 않는다.
    assert "cost" not in rec and "transcript_path" not in rec
    assert "total_input_tokens" not in rec["context_window"]


async def test_wrapper_skips_itself_and_prints_nothing_without_user_statusline():
    cc, cfg = _tmp(), _tmp()
    # 우리 래퍼 자신이 user 설정에 있어도(재귀 표식) 그것을 다시 부르지 않는다.
    with open(os.path.join(cfg, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"statusLine": {"type": "command",
                                  "command": "python3 /x/ccbridge.py statusline"}}, f)
    out = io.BytesIO()
    rc = ccbridge.run_statusline(cc, json.dumps(_sl_input()).encode(),
                                 _env(CLAUDE_CONFIG_DIR=cfg), out=out)
    assert rc == 0 and out.getvalue() == b""
    # 패널 키가 없으면 session_id 로 떨군다.
    assert os.path.exists(os.path.join(cc, "sl", "sess-1.json"))
    # 경로 조각이 든 키는 거른다(→ session_id 로 폴백).
    ccbridge.run_statusline(cc, json.dumps(_sl_input(session_id="s2")).encode(),
                            _env(CLAUDE_CONFIG_DIR=cfg, PYTMUX_PANE_KEY="../evil"),
                            out=io.BytesIO())
    assert os.path.exists(os.path.join(cc, "sl", "s2.json"))
    assert not os.path.exists(os.path.join(cc, "evil.json"))
    # 깨진 stdin 이어도 죽지 않는다(사용자 statusline 이 없으면 빈 출력).
    assert ccbridge.run_statusline(cc, b"{not json", _env(CLAUDE_CONFIG_DIR=cfg),
                                   out=io.BytesIO()) == 0


async def test_wrapper_finds_project_statusline_before_user():
    cc, cfg, cwd = _tmp(), _tmp(), _tmp()
    _write_user_statusline(cfg, "print('USER')\n")
    proj = os.path.join(cwd, ".claude")
    _write_user_statusline(proj, "import sys; sys.stdout.write('PROJ')\n")
    out = io.BytesIO()
    ccbridge.run_statusline(cc, json.dumps(_sl_input(cwd=cwd)).encode(),
                            _env(CLAUDE_CONFIG_DIR=cfg), out=out)
    assert out.getvalue() == b"PROJ"


async def test_wrapper_runs_as_a_standalone_script():
    """settings 에 적히는 명령 그대로 — 별도 프로세스의 `ccbridge.py statusline`."""
    cc, cfg = _tmp(), _tmp()
    raw = json.dumps(_sl_input()).encode()
    r = subprocess.run([sys.executable, ccbridge.__file__, "statusline", "--cc", cc],
                       input=raw, capture_output=True,
                       env=_env(CLAUDE_CONFIG_DIR=cfg, PYTMUX_PANE_KEY="kk"),
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout == b""
    assert json.load(open(os.path.join(cc, "sl", "kk.json"),
                          encoding="utf-8"))["pane_key"] == "kk"


async def test_record_appends_one_line_per_call():
    d = _tmp()
    p = os.path.join(d, "rec.log")
    ccbridge.run_record(p, b'{"a":\n1}')
    ccbridge.run_record(p, b'{"b":2}')
    lines = open(p, encoding="utf-8").read().splitlines()
    assert len(lines) == 2 and lines[0].split("\t", 1)[1] == '{"a": 1}'


async def test_settings_copy_user_padding_and_carry_session_start_hook():
    s = ccbridge.build_settings("/st/cc", {"padding": 2, "refreshInterval": 5,
                                           "command": "x"}, py="/py")
    sl = s["statusLine"]
    assert sl["type"] == "command" and sl["padding"] == 2 and sl["refreshInterval"] == 5
    assert ccbridge.MARKER in sl["command"] and " statusline " in sl["command"]
    hook = s["hooks"]["SessionStart"][0]["hooks"][0]
    assert hook["timeout"] == ccbridge.HOOK_TIMEOUT
    assert " session-start " in hook["command"]
    # C3-2(UserPromptSubmit)는 고르지 않았다(사용자 결정 2026-10-15).
    assert "UserPromptSubmit" not in s["hooks"]
    # 사용자 statusLine 이 없으면 padding·refreshInterval 도 없다.
    assert "padding" not in ccbridge.build_settings("/st/cc", None, py="/py")["statusLine"]


async def test_shim_injects_settings_and_passes_through_on_posix():
    if ccbridge.IS_WINDOWS:
        skip("Windows 는 심을 안 깐다(BatBadBut) — test_windows_pane_env_is_a_noop 가 잰다")
    cc, fake = _tmp(), _tmp()
    real = os.path.join(fake, "claude")
    with open(real, "w") as f:
        f.write('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    os.chmod(real, 0o755)
    bindir = ccbridge.ensure_bridge(cc, env={"CLAUDE_CONFIG_DIR": _tmp()})
    assert bindir and os.access(os.path.join(bindir, "claude"), os.X_OK)
    sp = ccbridge.settings_path(cc)

    def run(*args, **extra):
        env = dict(os.environ)
        env["PATH"] = bindir + os.pathsep + fake + os.pathsep + env.get("PATH", "")
        env.update(extra)
        r = subprocess.run([os.path.join(bindir, "claude"), *args],
                           capture_output=True, text=True, env=env, timeout=30)
        assert r.returncode == 0, r.stderr
        return r.stdout.splitlines()

    assert run("hello world") == ["--settings", sp, "hello world"]
    assert run("--settings", "mine.json") == ["--settings", "mine.json"]
    assert run("-p", "q") == ["-p", "q"]
    assert run("mcp", "list") == ["mcp", "list"]
    assert run("x", **{ccbridge.ENV_DISABLE: "0"}) == ["x"]
    # opt off(settings 삭제) 뒤에는 PATH 에 심이 남은 셸에서도 주입이 없다.
    ccbridge.disable_bridge(cc)
    assert run("y") == ["y"]


# ── opt off = 흔적 0 · Windows = 문서화된 무동작 · 패널 키 ─────────────────────────

async def test_opt_off_leaves_no_trace_and_pane_key_survives_restart():
    srv, task, sock = await server_only()
    cc = _tmp()
    try:
        srv._cc_dir_path = cc
        assert srv.claude_statusline_bridge is False          # 기본 off(사용자 결정)
        env = srv._panel_env("abcd1234abcd1234")
        assert ccbridge.ENV_PANE_KEY not in env
        assert cc not in env.get("PATH", "")
        assert os.listdir(cc) == [], "opt off 인데 파일을 썼다"
        # 패널마다 키가 서고 재시작 직렬화로 넘어간다.
        sess = srv.ensure_default_session(80, 24)
        pane = sess.active_window.active_pane
        assert isinstance(pane.pane_key, str) and len(pane.pane_key) == 16
        snap = pane.export_state()
        assert snap["pane_key"] == pane.pane_key
        from pytmuxlib.model import Pane
        q = Pane(-1, -1, 80, 24)
        q.import_state(snap)
        assert q.pane_key == pane.pane_key
        # 켜면: POSIX 는 심 PATH + 키, Windows 는 여전히 무동작.
        srv.claude_statusline_bridge = True
        env = srv._panel_env("abcd1234abcd1234")
        if ccbridge.IS_WINDOWS:
            assert ccbridge.ENV_PANE_KEY not in env
            assert not os.path.exists(ccbridge.settings_path(cc))
        else:
            assert env[ccbridge.ENV_PANE_KEY] == "abcd1234abcd1234"
            assert env["PATH"].split(os.pathsep)[0] == ccbridge.bin_dir(cc)
            assert os.path.exists(ccbridge.settings_path(cc))
            assert srv._cc_pane_bridged(type("P", (), {"pane_key": "abcd1234abcd1234"})())
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_windows_pane_env_is_a_noop():
    """Windows 는 심을 안 깐다 — `.cmd` 는 cmd.exe 가 `%*` 를 다시 파싱한다(BatBadBut ·
    sshwrap 이 같은 이유로 ssh 래퍼를 걷었다). 플랫폼과 무관하게 그 갈래를 잰다."""
    cc = _tmp()

    class S:
        claude_statusline_bridge = True
        _cc_dir_path = cc

    env = {"PATH": "/usr/bin"}
    with patched(ccbridge, IS_WINDOWS=True):
        ccsl.pane_env(S(), env, "k" * 16)
        assert ccbridge.ensure_bridge(cc) is None
    assert env == {"PATH": "/usr/bin"}
    assert os.listdir(cc) == []


async def test_setter_persists_and_old_opts_json_still_loads():
    import pytmux
    srv, task, sock = await server_only()
    cc = _tmp()
    try:
        srv._cc_dir_path = cc
        assert srv.set_claude_statusline_bridge(True) is True
        po = json.load(open(srv.opts_path, encoding="utf-8"))["plugin_opts"]
        assert po["claude_statusline_bridge"] is True
        assert pytmux.Server(sock).claude_statusline_bridge is True
        assert srv.set_claude_statusline_bridge(False) is False
        assert not os.path.exists(ccbridge.settings_path(cc))
        assert pytmux.Server(sock).claude_statusline_bridge is False
        # CLI 토글(헤드리스)도 같은 setter 로 간다.
        assert CC.server_control(srv, None, "claude-statusline-bridge", ["on"]) == "on"
        assert CC.server_control(srv, None, "claude-statusline-bridge", ["off"]) == "off"
        # 옛 opts.json(usage_refresh_sec 가 있는) — 읽고 죽지 않는다(S1 은 폴백 주기로 쓴다).
        with open(srv.opts_path, "w", encoding="utf-8") as f:
            json.dump({"usage_refresh_sec": 900, "plugin_opts": {}}, f)
        s2 = pytmux.Server(sock)
        assert s2.usage_refresh_sec == 900 and s2.claude_statusline_bridge is False
    finally:
        try:
            os.unlink(srv.opts_path)
        except OSError:
            pass
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


# ── 서버 소비(D3) ──────────────────────────────────────────────────────────────

_DROP_SEQ = [0]


def _drop(cc, key, data):
    os.makedirs(ccbridge.sl_dir(cc), exist_ok=True)
    path = os.path.join(ccbridge.sl_dir(cc), key + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    # 읽는 쪽은 mtime 이 바뀐 파일만 다시 읽는다 — 파일시스템 시각 해상도에 기대지 않게
    # 쓸 때마다 단조 증가하는 mtime 을 준다.
    _DROP_SEQ[0] += 1
    t = time.time() + _DROP_SEQ[0]
    os.utime(path, (t, t))
    return path


def _rec(pane_key, **over):
    acct = over.pop("claude_json", None)
    r = ccbridge._subset(_sl_input(), {ccbridge.ENV_PANE_KEY: pane_key,
                                       "CLAUDE_CONFIG_DIR": "/nope"})
    r["claude_json"] = acct or "/nope/.claude.json"
    r.update(over)
    return r


def _status(srv, sess):
    win = sess.active_window
    msg = {"windows": []}
    CC.server_status(srv, sess, win, msg, True)
    return msg


async def _bridged_server():
    srv, task, sock = await server_only()
    cc = _tmp()
    srv._cc_dir_path = cc
    srv.claude_statusline_bridge = True
    sess = srv.ensure_default_session(80, 24)
    return srv, task, sock, cc, sess, sess.active_window.active_pane


async def test_server_maps_sl_file_to_status_fields_and_badges_draw_them():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    try:
        acct = os.path.join(cc, "claude.json")
        with open(acct, "w", encoding="utf-8") as f:
            json.dump({"oauthAccount": {"emailAddress": "alice@example.com"}}, f)
        _drop(cc, pane.pane_key, _rec(pane.pane_key, claude_json=acct))
        # ⚠ await 없이 이어 간다 — 서버 flush 스캔이 셸 화면을 보고 `_claude` 를 지우기 전에.
        pane._claude = "idle"
        assert ccsl.tick(srv) is True
        msg = _status(srv, sess)
        assert msg["claude_model"] == "opus-4.8"
        assert msg["claude_usage"] == "ctx:42%/1M"
        assert msg["tok5h_pct"] == 37
        u = msg["usage_limits"]
        assert u["source"] == "statusline"
        assert u["session"]["pct"] == 37 and u["week_all"]["pct"] == 12
        assert isinstance(u["session"]["reset"], str) and u["session"]["resets_at"]
        assert u["account"] == "al…@example.com"
        assert msg["usage_age_sec"] is not None and msg["usage_age_sec"] < 60
        # 호출부 단언: 상태줄 배지가 그 값을 실제로 그린다(정본·GUI 같은 규칙).
        badges = CC.plugin_badges(srv, sess, msg)
        texts = {b["kind"]: b["text"] for b in badges}
        assert texts.get("model") == "opus-4.8"
        assert "37%/5h" in texts.get("usage", "")
        # 스크랩 경로도 statusline 값이 이긴다(근사 `~` 없음).
        assert srv._usage_text(pane) == "ctx:42%/1M"
        # 계정 폴백 = .claude.json(프로브 아님).
        assert ccsl.pane_account(srv, pane) == ("al…@example.com", "alice@example.com")
        # 패널 **자기** 5h% 가 전역 값보다 앞선다(다른 계정 한도를 이 패널 라벨로 안 그린다).
        srv._usage["session"]["pct"] = 80
        srv._usage["account"] = "someone@else"
        pane._claude_account = "al…@example.com"
        assert srv._tok5h_pct(pane, 0) == 37
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_reset_passed_goes_to_zero():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    try:
        now = time.time()
        r = _rec(pane.pane_key)
        r["rate_limits"]["five_hour"]["resets_at"] = now + 100
        _drop(cc, pane.pane_key, r)
        pane._claude = "idle"
        ccsl.tick(srv, now=now)
        assert srv._usage["session"]["pct"] == 37
        assert ccsl.pane_5h(pane, now) == 37
        # 새 관측 없이 리셋 시각을 넘긴다 → 0%(CLI 도 지난 창을 뺀다).
        assert ccsl.tick(srv, now=now + 200) is True
        assert srv._usage["session"]["pct"] == 0
        assert ccsl.pane_5h(pane, now + 200) == 0
        # 리셋이 지난 뒤 관측된 입력(five_hour 가 빠진)도 0% 로 읽는다.
        r2 = _rec(pane.pane_key, observed_at=now + 300)
        del r2["rate_limits"]["five_hour"]
        srv._usage["session"] = {"pct": 50, "reset": None, "resets_at": now + 250}
        srv._usage_ts = now
        _drop(cc, pane.pane_key, r2)
        ccsl.tick(srv, now=now + 300)
        assert srv._usage["session"]["pct"] == 0
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_input_without_rate_limits_keeps_global_value():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    try:
        prev = {"session": {"pct": 55, "reset": "2pm"}, "account": "a@x",
                "week_sonnet": {"pct": 9, "reset": "Jun 13"}}
        srv._usage = dict(prev)
        r = _rec(pane.pane_key)
        del r["rate_limits"]                      # 첫 응답 전 · API 키 사용자 · 옛 CLI
        _drop(cc, pane.pane_key, r)
        pane._claude = "idle"
        ccsl.tick(srv)
        assert srv._usage == prev
        assert srv._tok5h_pct(pane, 0) == 55      # 패널 자기 값이 없으면 전역 값
        # 주간 Sonnet only 는 statusline 에 없다 — 들어와도 직전(인패널 /usage) 값을 둔다.
        _drop(cc, pane.pane_key, _rec(pane.pane_key))
        ccsl.tick(srv)
        assert srv._usage["week_sonnet"] == {"pct": 9, "reset": "Jun 13"}
        assert srv._usage["session"]["pct"] == 37
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_fresh_statusline_suppresses_the_probe_spawn():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    try:
        _drop(cc, pane.pane_key, _rec(pane.pane_key))
        before = srv._usage_probe_spawns
        # 억제가 깨져도 진짜 숨은 claude 는 안 뜨게 막아 둔다(그때는 아래 단언이 운다).
        usageprobe = importlib.import_module("pytmuxlib.plugins.claude-code.usageprobe")
        with patched(usageprobe, query_usage=lambda *a, **k: None):
            u = await srv.refresh_usage()      # 즉시 다시 읽기 — 숨은 claude 를 안 띄운다
        assert u is srv._usage and u["source"] == "statusline"
        assert srv._usage_probe_spawns == before
        assert srv._usage_probe_skipped >= 1
        assert ccsl.probe_suppressed(srv) is True
        srv.claude_statusline_bridge = False
        assert ccsl.probe_suppressed(srv) is False   # off 면 종전(프로브)대로
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_old_session_observation_is_dropped_at_a_new_claude():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    try:
        _drop(cc, pane.pane_key, _rec(pane.pane_key, observed_at=time.time() - 3600))
        pane._claude = "idle"
        ccsl.tick(srv)
        assert ccsl.pane_model(pane) == "opus-4.8"
        # 확정 세션 종료를 지나온 새 claude → 지난 관측은 버려지고 다시 안 붙는다.
        pane._cc_sl_floor = time.time() - ccsl.SESSION_FLOOR_SLACK
        pane._cc_sl = None
        srv._cc_sl_mtimes.clear()
        ccsl.tick(srv)
        assert pane._cc_sl is None
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_namesync_rules_export_follows_the_plugin():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    try:
        srv._namesync_rules = [{"host": "", "os": "", "path": "/p", "keyword": "kw"}]
        ccsl.tick(srv)
        p = ccbridge.namesync_path(cc)
        assert json.load(open(p, encoding="utf-8")) == srv._namesync_rules
        # 플러그인이 꺼지면(목록에서 빠지면) 파일을 지운다 — 훅은 아무것도 안 낸다.
        with patched(srv.plugins, plugins=[x for x in srv.plugins.plugins
                                           if x.name != "claude-name-sync"]):
            ccsl.tick(srv)
            assert not os.path.exists(p)
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


# ── C3-1: session-start 훅 ─────────────────────────────────────────────────────

def _ns_cc(path):
    cc = _tmp()
    with open(ccbridge.namesync_path(cc), "w", encoding="utf-8") as f:
        json.dump([{"host": ns._this_host(), "os": ns._this_os(),
                    "path": path, "keyword": "projX"}], f)
    return cc


async def test_session_start_input_table():
    proj = _tmp()
    cc = _ns_cc(proj)
    env = {ccbridge.ENV_PANE_KEY: "pk01"}

    def out(source, title="", cwd=proj, e=env, c=cc):
        return ccbridge.session_start_output(
            c, {"source": source, "session_title": title, "cwd": cwd,
                "session_id": "s"}, e)

    for src in ("startup", "resume", "fork"):
        o = out(src)
        assert o["hookSpecificOutput"] == {"hookEventName": "SessionStart",
                                           "sessionTitle": "projX"}, src
        assert o["terminalSequence"] == ccbridge.title_osc("pk01", "projX")
    for src in ("clear", "compact", "", "weird"):
        assert out(src) is None, src
    assert out("startup", title="그로기") is None          # 정해진 이름을 지킨다
    assert out("resume", title="  ") is not None           # 공백 제목 = 아직 없음
    assert out("startup", cwd=_tmp()) is None              # 규칙 불일치
    assert out("startup", c=_tmp()) is None                # 규칙 파일 없음(서버 미기동·플러그인 없음)
    o = out("startup", e={})                               # 패널 키 없음 → 표식 없이 제목만
    assert "terminalSequence" not in o and o["hookSpecificOutput"]["sessionTitle"] == "projX"
    # 파일 표식(OSC 대역)도 남는다.
    t = json.load(open(os.path.join(ccbridge.titled_dir(cc), "pk01.json"), encoding="utf-8"))
    assert t["kw"] == "projX"
    assert ccbridge.session_start_output(cc, "not a dict", env) is None


async def test_session_start_uses_the_one_rule_function():
    """규칙 판정은 한 벌 — 훅이 부르는 함수 **is** name-sync 플러그인의 함수."""
    loaded = ccbridge.load_namesync()
    assert loaded is ns
    assert loaded._match_keyword is ns._match_keyword
    calls = []

    def spy(rules, cwd, host, osname):
        calls.append((cwd, host, osname))
        return "spied"

    proj = _tmp()
    with patched(ns, _match_keyword=spy):
        o = ccbridge.session_start_output(_ns_cc(proj),
                                          {"source": "startup", "cwd": proj}, {})
    assert calls == [(proj, ns._this_host(), ns._this_os())]
    assert o["hookSpecificOutput"]["sessionTitle"] == "spied"


async def test_session_start_runs_as_a_standalone_script():
    proj = _tmp()
    cc = _ns_cc(proj)
    raw = json.dumps({"source": "startup", "session_title": "", "cwd": proj}).encode()
    r = subprocess.run([sys.executable, ccbridge.__file__, "session-start", "--cc", cc],
                       input=raw, capture_output=True,
                       env=_env(PYTMUX_PANE_KEY="pk02"), timeout=60)
    assert r.returncode == 0, r.stderr
    o = json.loads(r.stdout.decode("utf-8"))
    assert o["hookSpecificOutput"]["sessionTitle"] == "projX"
    assert o["terminalSequence"].startswith("\x1b]777;pytmux;cc-title;pk02;")
    # 실패는 조용히 0 — 깨진 입력이 claude 기동을 막지 않는다.
    r = subprocess.run([sys.executable, ccbridge.__file__, "session-start", "--cc", cc],
                       input=b"{bad", capture_output=True, timeout=60)
    assert r.returncode == 0 and r.stdout == b""


# ── C3-1: 서버 — OSC 표식 · 폴백 ───────────────────────────────────────────────

def _osc(key, kw):
    return ccbridge.title_osc(key, kw).encode("utf-8")


async def test_foreign_or_malformed_osc_is_ignored():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    try:
        pane.feed(_osc("0" * 16, "projX"))                  # 남의 키
        assert pane._cc_titled is None
        pane.feed(b"\x1b]777;pytmux;cc-title;" + pane.pane_key.encode() + b";@@@\x07")
        assert pane._cc_titled is None                      # 깨진 b64
        pane.feed(_osc(pane.pane_key, "projX"))             # 내 키 — 실제 VT 경로로
        assert pane._cc_titled == "projX"
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_titled_file_marker_is_the_osc_fallback():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    try:
        d = ccbridge.titled_dir(cc)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "%s.json" % pane.pane_key), "w") as f:
            json.dump({"kw": "old", "ts": time.time() - 3600}, f)   # 지난 기동의 잔재
        ccsl.tick(srv)
        assert pane._cc_titled is None
        with open(os.path.join(d, "%s.json" % pane.pane_key), "w") as f:
            json.dump({"kw": "projX", "ts": time.time()}, f)
        ccsl.tick(srv)
        assert pane._cc_titled == "projX"
        assert os.listdir(d) == []                                  # 읽고 지운다
    finally:
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def _scan_and_settle(srv, sess, win):
    before = asyncio.all_tasks()
    NS.server_scan(srv, sess, win)
    spawned = [t for t in asyncio.all_tasks() if t not in before]
    assert spawned, "server_scan 이 동기화 태스크를 안 만들었다"
    return spawned


def _ns_setup(srv, pane, cfg):
    srv._namesync_rules = ns._sanitize_rules([{"host": ns._this_host(),
                                               "os": ns._this_os(),
                                               "path": "/tmp/projX",
                                               "keyword": "projX"}])
    srv._pane_cwd = lambda p: "/tmp/projX"
    srv._cc_bridge_keys.add(pane.pane_key)
    os.environ["CLAUDE_CONFIG_DIR"] = cfg            # _session_named 가 빈 곳을 보게


async def test_osc_marker_suppresses_rename_injection():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        _ns_setup(srv, pane, _tmp())
        pane.feed(_osc(pane.pane_key, "projX"))
        pane._claude = "idle"
        spawned = await _scan_and_settle(srv, sess, sess.active_window)
        assert await wait_for(lambda: all(t.done() for t in spawned))
        assert pane._pending_rename is None, "훅이 정한 이름 위에 /rename 을 무장했다"
        assert pane._ns_last_kw == "projX"
        assert pane.title == "projX"                 # pytmux 쪽 이름은 그대로 맞춘다
    finally:
        if old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_cfg
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_marker_arriving_during_the_wait_also_suppresses():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        _ns_setup(srv, pane, _tmp())
        with patched(ns, _CC_TITLE_WAIT=3.0, _CC_TITLE_POLL=0.05):
            pane._claude = "idle"
            spawned = await _scan_and_settle(srv, sess, sess.active_window)
            assert await wait_for(lambda: pane.title == "projX")   # 대기에 들어갔다
            pane.feed(_osc(pane.pane_key, "projX"))
            assert await wait_for(lambda: all(t.done() for t in spawned))
        assert pane._pending_rename is None
    finally:
        if old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_cfg
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_no_marker_falls_back_to_injection_after_the_wait():
    assert ns._CC_TITLE_WAIT == 10.0                 # 훅 timeout 5초 + 여유(설계 c3 §4)
    srv, task, sock, cc, sess, pane = await _bridged_server()
    old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        _ns_setup(srv, pane, _tmp())
        loop = asyncio.get_event_loop()
        with patched(ns, _CC_TITLE_WAIT=0.4, _CC_TITLE_POLL=0.05):
            t0 = loop.time()
            pane._claude = "idle"
            spawned = await _scan_and_settle(srv, sess, sess.active_window)
            assert await wait_for(lambda: all(t.done() for t in spawned))
            waited = loop.time() - t0
        assert pane._pending_rename == "projX", "폴백 주입을 무장하지 않았다"
        assert waited >= 0.4, "심을 받은 패널인데 표식을 기다리지 않았다"
        # 호출부 단언 — 무장된 이름이 실제로 `_pc_inject` 로 나간다.
        sent = []
        srv._pc_inject = lambda p, text: sent.append((p, text))
        srv._claude_composer_text = lambda p: ""
        pane._inbuf = ""
        srv._scan_idle_actions(pane, "", "idle")
        assert sent == [(pane, "/rename projX")]
        assert pane._pending_rename is None
    finally:
        if old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_cfg
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_unbridged_pane_does_not_wait():
    srv, task, sock, cc, sess, pane = await _bridged_server()
    old_cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    try:
        _ns_setup(srv, pane, _tmp())
        srv._cc_bridge_keys.clear()                  # 심을 안 받은 셸(옵션 off 때 뜬 셸 등)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        pane._claude = "idle"
        spawned = await _scan_and_settle(srv, sess, sess.active_window)
        assert await wait_for(lambda: all(t.done() for t in spawned))
        assert pane._pending_rename == "projX"
        assert loop.time() - t0 < ns._CC_TITLE_WAIT
    finally:
        if old_cfg is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = old_cfg
        await teardown(srv, task, sock)
        shutil.rmtree(cc, ignore_errors=True)


async def test_osc_marker_base64_roundtrip():
    s = ccbridge.title_osc("abc", "프로젝트 X")
    assert s.startswith("\x1b]777;pytmux;cc-title;abc;") and s.endswith("\x07")
    b = s[len("\x1b]777;pytmux;cc-title;abc;"):-1]
    assert base64.b64decode(b).decode("utf-8") == "프로젝트 X"
