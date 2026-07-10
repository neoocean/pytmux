"""claude-name-sync 플러그인 — 디렉토리별 이름 동기화.

미리 지정한 (머신·OS·경로)에서 Claude Code 를 실행하면, 그 패널이 든 pytmux **탭**과
**패널 제목**을 지정 키워드로 자동 변경하고, 같은 키워드로 **Claude 세션**도
`/rename <키워드>` 로 통일한다. 서로 다른 머신·OS 마다 다른 경로/키워드를 `:namesync`
TUI 편집기로 설정한다.

기능 전체가 이 디렉토리 안에 있다(delete-to-disable):
  - `__init__.py` : 코어와의 계약(명령 메타·훅·규칙 매칭·설정 영속). textual 무관·가벼움.
  - `screen.py`   : `:namesync` 모달 편집기(textual). 클라에서 실제로 열 때 지연 import.

이 디렉토리를 지우면 `namesync`/`nsync` 명령·자동 이름 동기화가 에러 없이 사라진다 —
코어는 이 플러그인을 직접 import 하지 않고 plugins 레지스트리 훅으로만 닿는다.

**claude-code 와의 관계**: 이 플러그인은 claude-code 를 import 하지 않는다. Claude 실행
감지는 코어 model.Pane 의 안전기본값 필드 `_claude`(claude-code 가 화면 스크랩으로
채움, 부재 시 None)를 **읽기만** 하고, Claude 세션 리네임은 코어 model.Pane 의 안전기본값
필드 `_pending_rename`(claude-code 스캔이 idle 경계에 발동)을 **쓰기만** 한다. claude-code
가 없으면 `_claude` 가 항상 None 이라 자동 감지가 조용히 비활성된다(pytmux 탭/패널 이름
변경도 함께 사라짐) — 두 필드 모두 코어 소유라 결합 없는 소프트 의존이다.

무게: 이 모듈은 textual 을 최상단에서 import 하지 않는다(서버 프로세스도 plugins.load()
로 이걸 읽는다). socket/os/sys 만 필요한 곳에서 쓴다."""
from __future__ import annotations

import os
import re
import socket
import sys

# 명령 메타데이터 — 코어가 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐 쓴다.
COMMANDS = [
    ("namesync", "디렉토리별 이름 동기화 규칙 — 지정 경로에서 Claude 실행 시 pytmux "
                 "탭/패널·Claude 세션 이름을 키워드로 자동 통일(별칭 nsync)", "Claude"),
]
NOARG = {"namesync", "nsync"}

# opts.json plugin_opts 네임스페이스에 규칙 목록을 저장하는 키.
_OPT_KEY = "namesync_rules"

# 코어 `_claude` 는 busy 출력 중 한두 프레임 None 으로 깜빡이므로(claude-code 의
# _hdr_claude 디바운스 유래), Claude 부재를 이만큼 **연속** 관측해야 세션 종료로 보고
# per-appearance 가드(_ns_synced)를 푼다. 즉발 재무장은 같은 세션에 cwd 재조회(lsof)·
# `/rename` 재시도를 유발한다(claude-code _HDR_CLAUDE_MISS 와 동일 임계, 30Hz ~1초).
_NS_ABSENT_FRAMES = 30


# ---- 머신/OS 신원 + 규칙 매칭(서버 측, textual 무관) ----
def _this_host() -> str:
    """이 머신의 짧은 호스트명(도메인 제거). 규칙의 host 필드와 대조한다."""
    try:
        return socket.gethostname().split(".")[0]
    except OSError:
        return ""


def _this_os() -> str:
    """이 머신의 OS 코드(darwin|linux|windows|기타). 규칙의 os 필드와 대조한다."""
    p = sys.platform
    if p.startswith("linux"):
        return "linux"
    if p == "darwin":
        return "darwin"
    if p.startswith("win"):
        return "windows"
    return p


def _norm_path(p: str) -> str:
    """경로 비교 정규화: ~ 확장 + normpath + OS 대소문자 규칙(macOS/Windows 무시)."""
    if not p:
        return ""
    return os.path.normcase(os.path.normpath(os.path.expanduser(p)))


def _real_path(p: str) -> str:
    """심볼릭 링크까지 해소한 정규화 경로. macOS 의 /tmp→/private/tmp 처럼 설정 경로와
    lsof 보고 cwd 가 링크로 갈릴 때도 일치시키려는 폴백. 경로가 없어도 realpath 는
    예외 없이 정규화만 하므로(존재 불필요) 안전하다."""
    if not p:
        return ""
    try:
        return os.path.normcase(os.path.realpath(os.path.expanduser(p)))
    except OSError:
        return _norm_path(p)


def _match_keyword(rules, cwd, host: str, osname: str):
    """cwd(패널 현재 디렉토리)에 정확히 일치하는 규칙의 키워드를 반환(없으면 None).

    host/os 가 빈 규칙은 '아무 머신/OS'(와일드카드)로 매칭한다. 경로는 **정확히 그
    디렉토리만** 일치(하위 디렉토리는 제외 — 사용자 결정). normpath 직접 비교가 어긋나면
    realpath(심링크 해소)로 한 번 더 비교한다(macOS /tmp 등). 여러 규칙이 일치하면
    먼저 선언된 것을 채택한다."""
    if not cwd:
        return None
    t_norm = _norm_path(cwd)
    t_real = _real_path(cwd)
    for r in (rules or ()):
        rh = (r.get("host") or "").strip()
        ro = (r.get("os") or "").strip()
        if rh and rh != host:
            continue
        if ro and ro != osname:
            continue
        rp = r.get("path")
        if _norm_path(rp) == t_norm or _real_path(rp) == t_real:
            kw = (r.get("keyword") or "").strip()
            if kw:
                return kw
    return None


# 규칙 keyword 는 최종적으로 `/rename <keyword>` 로 Claude 패널 입력에 주입될 수 있다
# (claude-code servermixin `_pc_inject` 가 text+"\r" 를 PTY 에 쓴다). keyword 에 내장된
# CR/LF·제어문자는 그 주입을 다중 줄 제출(임의 프롬프트 주입)로 만든다 — 소스가
# 자기-작성 opts.json 이라 신뢰경계를 넘지는 않지만(같은 UID), 심층방어로 세정한다
# (코드검수 2026-07-10 Low). 표시 이름에 제어문자가 낄 이유도 없다.
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _clean_name(s: str) -> str:
    """이름/키워드에서 제어문자(CR/LF/ESC/NUL 등)를 제거하고 양끝 공백을 다듬는다."""
    return _CTRL_RE.sub("", str(s or "")).strip()


def _sanitize_rules(rules) -> list:
    """외부(클라 편집기/opts.json)에서 온 규칙 목록을 신뢰 가능한 형태로 정제한다.
    path·keyword 가 빈 항목은 버리고, 각 필드를 문자열로 고정한다. keyword 는 주입
    경로에 실리므로 제어문자를 제거한다(_clean_name)."""
    out = []
    if not isinstance(rules, list):
        return out
    for r in rules:
        if not isinstance(r, dict):
            continue
        path = str(r.get("path") or "").strip()
        kw = _clean_name(r.get("keyword"))
        if not path or not kw:
            continue
        out.append({
            "host": str(r.get("host") or "").strip(),
            "os": str(r.get("os") or "").strip(),
            "path": path,
            "keyword": kw,
        })
    return out


class _NameSyncPlugin:
    name = "claude-name-sync"
    description = "디렉토리별 이름 동기화(지정 경로 Claude 실행 시 탭/패널·세션 이름 통일)"
    category = "Claude"
    commands = COMMANDS
    noarg = NOARG
    completions = []
    command_options = {}
    pane_scoped = set()

    # ---- 설정 영속(opts.json plugin_opts) ----
    def server_opts_init(self, server, opts):
        """opts.json → server._namesync_rules 설치. plugin_opts 네임스페이스 우선,
        없으면 구 top-level 키로 폴백(업그레이드 무중단). 플러그인 부재 시 이 훅이 안
        불려 server 에 규칙이 안 생기고, 읽는 코드(server_scan)도 함께 사라진다."""
        po = opts.get("plugin_opts")
        po = po if isinstance(po, dict) else {}
        raw = po[_OPT_KEY] if _OPT_KEY in po else opts.get(_OPT_KEY, [])
        server._namesync_rules = _sanitize_rules(raw)

    def server_opts_serialize(self, server):
        """server._namesync_rules → opts.json plugin_opts(코어가 불투명 저장)."""
        return {_OPT_KEY: [dict(r) for r in getattr(server, "_namesync_rules", [])]}

    # ---- 서버 런타임 훅 ----
    def server_scan(self, server, sess, win) -> bool:
        """30Hz flush 스캔(활성 윈도우). 규칙에 걸린 디렉토리에서 Claude 가 새로
        떠오르면(패널 `_claude` None→비None 전이) 이름 동기화를 1회 발동한다. 변화가
        즉시 반영됐으면 True(코어가 status 재전송).

        cwd 조회(_pane_cwd)는 macOS 에서 lsof 서브프로세스라 느리므로, 전이 감지 시엔
        executor 로 오프로드하는 지연 태스크(_schedule_sync)에 넘겨 flush 루프를 막지
        않는다 — 그래서 여기서는 대개 False 를 돌려주고 실제 적용/방송은 태스크가 한다.
        Linux(/proc)·Windows(PEB)는 빠르지만 일관성을 위해 동일 경로."""
        if win is None:
            return False
        rules = getattr(server, "_namesync_rules", None)
        if not rules:
            return False
        tab = sess.active_tab if sess else None
        for pane in win.panes():
            cl = getattr(pane, "_claude", None)
            if cl is None:
                # Claude 부재(또는 비-Claude 패널). raw `_claude` 는 busy 출력 중 한두
                # 프레임 None 으로 깜빡이므로, 즉발 재무장하면 같은 세션에 cwd 재조회·
                # `/rename` 이 반복된다 — _NS_ABSENT_FRAMES 연속 부재일 때만 세션 종료로
                # 보고 per-appearance 가드를 푼다(다음 실행에 다시 동기화). 세션 리네임
                # 이력(_ns_last_kw)은 여기서 지우지 않는다 — 거짓 종료 후 재등장에
                # `/rename` 을 재주입하지 않기 위해. 진짜 새 셸(respawn)에서만 pane_reset
                # 이 지워, 그때 새 세션이 다시 리네임된다.
                miss = getattr(pane, "_ns_absent", 0) + 1
                pane._ns_absent = miss
                if miss >= _NS_ABSENT_FRAMES and getattr(pane, "_ns_synced", False):
                    pane._ns_synced = False
                continue
            pane._ns_absent = 0
            if getattr(pane, "_ns_synced", False):
                continue
            # Claude 가 이 패널에 (재)등장 — 1회만 처리(매칭 실패해도 재-probe 방지).
            pane._ns_synced = True
            self._schedule_sync(server, sess, win, tab, pane)
        return False

    def pane_reset(self, pane):
        """respawn(새 셸) 시 이 플러그인의 per-pane 상태를 리셋한다 — 새 셸에서 Claude
        가 다시 뜨면 처음부터 탭/패널 이름 동기화·세션 리네임을 하도록. (claude-code 의
        panestate.reset_pane 과 독립 — Registry.pane_reset 이 두 훅을 모두 부른다.
        훅 부재 시 no-op, delete-to-disable.)"""
        pane._ns_synced = False
        pane._ns_last_kw = None
        pane._ns_absent = 0

    def _schedule_sync(self, server, sess, win, tab, pane):
        """전이 감지 패널의 cwd 를 executor 로 조회해(블로킹 없이) 규칙에 걸리면 탭/패널
        이름을 바꾸고 Claude 세션 리네임(_pending_rename)을 무장한다. flush 루프를 막지
        않도록 지연 태스크로 실행한다(태스크 안에서만 블로킹 lsof 를 돈다)."""
        import asyncio

        host, osname = _this_host(), _this_os()

        async def _run():
            try:
                loop = asyncio.get_event_loop()
                cwd = await loop.run_in_executor(None, server._pane_cwd, pane)
            except Exception:
                return
            # 이 조회는 executor(비블로킹)라 결과를 패널에 캐시해 둔다 — 동기 요청
            # 핸들러 namesync_get 이 블로킹 _pane_cwd(macOS lsof, ≤2s)를 이벤트 루프
            # 에서 다시 부르지 않고 이 캐시를 읽어 `:namesync` 열기가 전 클라를 얼리지
            # 않게 한다(코드검수 2026-07-10 S-3).
            if cwd is not None:
                pane._ns_cwd = cwd
            kw = _match_keyword(getattr(server, "_namesync_rules", None) or [],
                                cwd, host, osname)
            if not kw:
                return
            changed = False
            # pytmux 탭 이름(전이 당시 활성 탭이 이 패널의 윈도우일 때만 — await 중
            # kill/switch 로 stale 이 되지 않게 재확인). auto_rename 은 끈다(수동 이름).
            if (tab is not None and sess is not None and tab in sess.tabs
                    and tab.window is win and tab.name != kw):
                tab.name = kw
                tab.window.auto_rename = False
                changed = True
            # 패널 제목.
            if getattr(pane, "title", None) != kw:
                pane.title = kw
                changed = True
            # Claude 세션 리네임: 코어 Pane 필드 `_pending_rename` 을 세우면 claude-code
            # 스캔이 입력 준비된 첫 idle 에 `/rename <kw>` 를 주입한다(busy 면 대기).
            # claude-code 부재 시 이 필드는 안 읽혀 무해(delete-to-disable).
            #
            # 단, 이 패널의 세션을 **이미 이 키워드로 리네임한 이력**이 있으면
            # (_ns_last_kw == kw) 재주입하지 않는다 — 탭·패널·세션 이름이 모두 kw 로
            # 일치한 상태다(사용자 요청: 이미 일치하면 더는 /rename 시도 안 함). 세션
            # 이름은 화면에서 관측 불가하므로, 우리가 마지막으로 건 리네임 키워드를
            # 기록(_ns_last_kw)해 판단한다. 이 기록은 새 셸(respawn=pane_reset)에서만
            # 지워져, 그때 새 세션이 다시 리네임된다.
            if getattr(pane, "_ns_last_kw", None) != kw:
                pane._pending_rename = kw
                pane._ns_last_kw = kw
            if changed:
                try:
                    server._broadcast_status(sess)
                except Exception:
                    pass

        try:
            asyncio.get_event_loop().create_task(_run())
        except RuntimeError:
            # 이벤트 루프가 없으면(비정상 경로) 조용히 건너뛴다 — 다음 전이에 재시도.
            pane._ns_synced = False

    def handle_server_request(self, server, sess, action, msg):
        """`:namesync` 편집기 열기(namesync_get)·저장(namesync_set) 요청 처리."""
        if action == "namesync_get":
            rules = getattr(server, "_namesync_rules", None) or []
            # cwd 는 **캐시된 best-effort** 값만 쓴다(_schedule_sync 가 executor 로
            # 채운다). 여기서 server._pane_cwd 를 직접 부르면 macOS lsof(≤2s)가 동기
            # 요청 핸들러에서 이벤트 루프를 막아 전 클라가 프리즈한다(S-3). 스캔 이력이
            # 없는 패널이면 빈 문자열(편집기에서 사용자가 경로 입력) — 안전한 저하.
            win = sess.active_window if sess else None
            ap = win.active_pane if win else None
            cwd = str(getattr(ap, "_ns_cwd", "") or "") if ap is not None else ""
            return {"t": "namesync_config",
                    "rules": [dict(r) for r in rules],
                    "host": _this_host(), "os": _this_os(), "cwd": cwd}
        if action == "namesync_set":
            server._namesync_rules = _sanitize_rules(msg.get("rules"))
            try:
                server._save_opts()
            except Exception:
                pass
            return {"t": "namesync_saved", "count": len(server._namesync_rules)}
        return None

    # ---- 클라이언트 측 ----
    def attach_client(self, app):
        """편집기 진입점을 인스턴스에 설치한다(handle_command 가 호출). 서버에 현재
        규칙+호스트/OS+활성 패널 cwd 를 요청하고, 회신(namesync_config)이 오면
        handle_message 가 편집기를 연다."""
        def open_namesync():
            app._want_namesync = True
            app.send_cmd("namesync_get")
        app.open_namesync = open_namesync

    def handle_command(self, app, c, args):
        if c in ("namesync", "nsync"):
            app.open_namesync()
            return True
        return False

    def handle_message(self, app, msg) -> bool:
        t = msg.get("t")
        if t == "namesync_config":
            if not getattr(app, "_want_namesync", False):
                return True            # 요청 안 했는데 온 회신은 무시(방어)
            app._want_namesync = False
            from .screen import NameSyncScreen

            def _saved(res):
                if res is not None:    # None = 취소(Esc 전 변경 없음도 저장이 정상)
                    app.send_cmd("namesync_set", rules=res)
            app.push_screen(
                NameSyncScreen(msg.get("rules") or [], msg.get("host", ""),
                               msg.get("os", ""), msg.get("cwd", "")),
                _saved)
            return True
        if t == "namesync_saved":
            from pytmuxlib import i18n
            n = msg.get("count", 0)
            try:
                app.display_message(i18n.t("nsmsg.saved").format(n=n))
            except Exception:
                pass
            return True
        return False


PLUGIN = _NameSyncPlugin()
