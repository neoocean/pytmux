"""claude-prompt-history 플러그인 — Claude 패널에 입력한 프롬프트 히스토리.

기존(코어+claude-code 에 통합돼 있던) 프롬프트 히스토리 기능을 **독립 플러그인**으로
재구성한 것. 동작:
  * Claude CLI 실행 후 입력한 프롬프트를 **패널마다** 시간순 저장(서버 server_input 훅).
  * `:` 명령 프롬프트에서 `prompt-history`(pane_scoped) 를 작성하는 동안, 대상(활성)
    패널 외곽선 안쪽 위에서부터 **직전 프롬프트를 미리보기 패널**로 표시(client_render).
    프롬프트가 멀티라인이면 1행부터 시작해 여러 줄로 늘리되 **최대 N행**(설정 가능).
  * 미리보기 패널이 뜬 상태에서 Enter(=명령 실행) → **프롬프트 팝업**으로 전환. 팝업에서
    ↑↓ 로 이전 프롬프트를 살펴보고, Enter 로 그 프롬프트가 입력된 스크롤백 위치로
    화면을 올린 뒤 팝업을 닫는다(서버가 스크롤백 텍스트 검색으로 점프). Esc 로도 닫는다.
  * 미리보기 행수 N 은 명령(`prompt-history-lines N`)과 팝업(+/−)에서 설정·영속(서버 opts).
  * Claude 패널 첫 줄에 **가장 최근 프롬프트를 고정 바**로 늘 띄운다(최대 2줄 · pytmux-560).
    claude 자신의 스티키 바가 떠 있거나 그 프롬프트가 화면에 보이면 비킨다. 누르면 팝업.
    (아래 2026-06-12 결정 「평소엔 안 보인다」를 사용자 요구 2026-10-15 가 뒤집었다.)

설계 결정(사용자 확정 2026-06-12):
  - 기존 claude-code 의 always-on 단일행 헤더를 **대체**한다(Phase 2 에서 그 헤더 제거).
  - 미리보기 패널은 **명령 프롬프트 작성 중에만** transient 하게 뜬다(서버 행 예약 없이
    client_render 오버레이). 평소엔 안 보인다.
  - 점프는 **플러그인 내부 스크롤백 텍스트 검색**으로 — 코어 model.py(앵커/hist_total)
    무수정. deque 회전으로 사라진 프롬프트는 점프 불가(graceful).

delete-to-disable: 이 디렉토리를 지우면 prompt-history 명령·미리보기·팝업이 조용히
사라지고, 히스토리 추적도 멈춘다(코어/claude-code 무에러). Claude 존재 판정은
claude-code 가 세우는 `pane._claude` 를 getattr 로 **약하게** 읽어, claude-code 가 없으면
아무 프롬프트도 기록하지 않는다(하드 참조 금지).

무게: 이 __init__ 은 textual 을 최상단에서 import 하지 않는다(서버도 plugins.load 로
읽는다). 화면(screen)·렌더(render)·서버 로직(server)은 실제로 쓸 때 지연 import 한다."""
from __future__ import annotations

from pytmuxlib import i18n

COMMANDS = [
    ("prompt-history", "Claude 프롬프트 히스토리 팝업(`:`서 작성 중엔 직전 프롬프트 "
                       "미리보기; 별칭 prompts·ph)", "Claude"),
    ("prompt-history-lines", "프롬프트 미리보기 패널 최대 행수 설정 "
                             "(prompt-history-lines <1-3>; 별칭 ph-lines)", "Claude"),
]
NOARG = {"prompt-history", "prompts", "ph"}
PANE_SCOPED = {"prompt-history", "prompts", "ph"}   # 작성 중 대상 패널에 미리보기

_OPEN_ALIASES = ("prompt-history", "prompts", "ph")
_LINES_ALIASES = ("prompt-history-lines", "ph-lines")
_DEFAULT_LINES = 3
_MIN_LINES, _MAX_LINES = 1, 3

# 미리보기 행수 picker(옵션 모달) — ←→ 로 1~3 선택.
COMMAND_OPTIONS = {
    "prompt-history-lines": [{"key": "n", "label": "행수",
                              "choices": ["1", "2", "3"]}],
    "ph-lines": [{"key": "n", "label": "행수", "choices": ["1", "2", "3"]}],
}

i18n.register({
    "ko": {
        "cmd.prompt-history": "Claude 프롬프트 히스토리 팝업(작성 중 미리보기; 별칭 prompts·ph)",
        "cmd.prompt-history-lines": "프롬프트 미리보기 최대 행수 (prompt-history-lines <1-3>)",
        "ph.popup_title": "프롬프트 히스토리",
        "ph.popup_sub": "↑↓ 이동 · Enter 그 위치로 점프 · +/− 미리보기 {n}행 · Esc 닫기",
        "ph.empty": "(저장된 프롬프트가 없습니다 — Claude 에 프롬프트를 입력해 보세요)",
        "ph.multiline_mark": " ⏎",
        "ph.truncated_mark": " …",
        "ph.jump_fail": "그 프롬프트가 스크롤백에 없습니다(회전/재시작으로 사라짐)",
        "ph.jump_alt": "fullscreen(대체 화면) 패널에서는 그 위치로 못 뜁니다 — Claude 의 스티키 바나 transcript 모드의 이전·다음 프롬프트 키를 쓰세요",
        "ph.lines_set": "프롬프트 미리보기: {n}행",
        # Tier C 스펙 화면의 안내줄 — 정본 팝업(`popup_sub`)과 **조작이 다르다**
        # (스펙엔 +/− 미리보기 행수 조절이 없다)라 문구를 따로 둔다.
        "ph.spec_hint": "↑↓ 이동 · Enter 그 위치로 점프 · Esc 닫기",
    },
    "en": {
        "cmd.prompt-history": "Claude prompt-history popup (preview while typing; alias prompts·ph)",
        "cmd.prompt-history-lines": "Max prompt-preview rows (prompt-history-lines <1-3>)",
        "ph.popup_title": "Prompt history",
        "ph.popup_sub": "↑↓ move · Enter jump to position · +/− preview {n} rows · Esc close",
        "ph.empty": "(no saved prompts — type a prompt into Claude first)",
        "ph.multiline_mark": " ⏎",
        "ph.truncated_mark": " …",
        "ph.jump_fail": "That prompt is no longer in scrollback (rotated out / restarted)",
        "ph.jump_alt": "Can't jump to it in a fullscreen (alternate screen) pane — use Claude's sticky bar or the transcript mode prompt keys",
        "ph.lines_set": "Prompt preview: {n} rows",
        "ph.spec_hint": "↑↓ move · Enter jump to position · Esc close",
    },
})


def _cmdmap():
    """명령→액션 표(`cmdmap`). 하이픈 디렉터리라 일반 import 가 안 돼 지연 로드한다."""
    from . import cmdmap
    return cmdmap


def _clamp_lines(n) -> int:
    try:
        return max(_MIN_LINES, min(_MAX_LINES, int(n)))
    except (TypeError, ValueError):
        return _DEFAULT_LINES


class _PromptHistoryPlugin:
    name = "claude-prompt-history"
    description = "Claude 프롬프트 히스토리 — 미리보기·팝업·스크롤백 점프"
    category = "Claude"
    commands = COMMANDS
    noarg = NOARG
    completions = []
    command_options = COMMAND_OPTIONS
    pane_scoped = PANE_SCOPED

    # ---- Pane 상태(서버·재시작 직렬화) ----
    def pane_init(self, pane):
        pane._ph_history = []       # 시간순 제출 프롬프트(멀티라인 보존, 한 항목=한 제출)
        pane._ph_inbuf = ""         # 입력 누적(claude-code _inbuf 와 별개 — 충돌 방지)
        pane._ph_sent = None        # status 디바운스(직전 전송 tail 슬라이스)

    def pane_reset(self, pane):
        pane._ph_inbuf = ""         # respawn: 입력 버퍼만 비움(히스토리는 보존 가치 적어 유지)

    def pane_serialize(self, pane) -> dict:
        return {"_ph_history": list(getattr(pane, "_ph_history", []))[-100:]}

    def pane_restore(self, pane, data):
        if "_ph_history" in (data or {}):
            pane._ph_history = list(data["_ph_history"])

    # ---- 서버 옵션(미리보기 행수 영속) ----
    def server_opts_init(self, server, opts):
        server._ph_max_lines = _clamp_lines(opts.get("ph_max_lines", _DEFAULT_LINES))

    def server_opts_serialize(self, server) -> dict:
        return {"ph_max_lines": getattr(server, "_ph_max_lines", _DEFAULT_LINES)}

    # ---- 서버 런타임 ----
    def server_input(self, server, pane, data):
        from .server import track_input
        track_input(pane, data)

    def server_paste(self, server, pane, data):
        from .server import track_input
        track_input(pane, data)

    def server_status(self, server, sess, win, msg, full):
        from .server import status_fields
        status_fields(server, win, msg, full)

    def plugin_command_action(self, name, args):
        """`prompt-history-lines` → `(set_ph_max_lines, {n})` (pytmux-35).

        **옮기기만 한다** — 액션을 어느 표가 받는지는 서버가 안다. 규칙 한 벌은
        `cmdmap.py` 에 있고 정본 클라도 그것을 쓴다."""
        from .cmdmap import to_action
        return to_action(name, args)

    def server_command(self, server, client, sess, action, msg):  # noqa: C901
        if action == "set_ph_max_lines":
            # ★ `n` 이 없으면(무인자 명령) **다음 값으로 순환**한다 — 같은 표의 3-state
            #   토글과 같은 규약(cmdmap 머리말). 종전에는 그 경우 아무 일도 안 났다.
            n = msg.get("n")
            cur = _clamp_lines(getattr(server, "_ph_max_lines", _DEFAULT_LINES))
            server._ph_max_lines = _clamp_lines(
                n if n is not None else (cur % _MAX_LINES) + 1)
            server._save_opts()
            return "broadcast"          # 모든 클라에 새 행수 status 반영
        if action == "ph_scroll_to":
            from .server import scroll_to_prompt
            ok = scroll_to_prompt(server, sess, int(msg.get("index", 0)),
                                  pane_id=msg.get("pane"))
            # 점프 성공 시 스크롤 변경을 모든 클라에 반영(pane.scroll 은 공유 서버 상태).
            # 실패는 **요청한 클라에게 말한다**(pytmux-544 — 종전에는 무동작이었고
            # `ph.jump_fail` 문구는 등록만 되고 쓰이지 않았다).
            if not ok:
                server._spawn(server._send_to(client, _jump_failed_notice(server, sess)),
                              "ph_jump_fail")
            return "broadcast" if ok else "handled"
        return None

    # ---- 서버 측: 화면 스펙(Tier C) ----
    #
    # 정본은 아래 `open_prompt_history` 로 자기 Textual 팝업을 띄운다(자료는 status 로
    # 이미 받아 둔 것). 네이티브 클라도 같은 자료를 받지만 그릴 화면이 없었다 — 그래서
    # **무엇을 그릴지**를 스펙으로 준다. 점프는 종전 `ph_scroll_to` 와 같은 손이다.
    def plugin_screen(self, server, sess, req):
        if req.get("do") == "open":
            if req.get("name") not in _OPEN_ALIASES:
                return None                 # 내 이름이 아니다
            # 고정 바 클릭(GUI)은 `pane=<id>` 를 싣는다 — 정본 `client_click` 이 **눌린 패널**의
            # 이력을 여는 것과 같다(pytmux-587). 친 명령의 다른 인자는 정본처럼 무시한다.
            return self._open_spec(server, sess, pane_id=_pane_arg(req.get("args")))
        if req.get("id") != "prompt-history":
            return None
        if req.get("do") == "jump":
            from .server import scroll_to_prompt
            pane_id, index = _split_key(req.get("input"))
            if index >= 0 and scroll_to_prompt(server, sess, index, pane_id=pane_id):
                # 스크롤은 **공유 서버 상태**라 모든 클라가 같이 따라와야 한다
                # (정본의 `ph_scroll_to` 가 broadcast 를 돌려주는 것과 같은 이유).
                server._broadcast_session(sess)
            else:
                # 못 뛰었으면 닫으면서 그 까닭을 함께 보낸다(목록이면 요청 클라에게 다 간다).
                return [{"t": "plugin_screen_close", "id": "prompt-history"},
                        _jump_failed_notice(server, sess)]
            return {"t": "plugin_screen_close", "id": "prompt-history"}
        if req.get("do") in ("lines+", "lines-"):
            # 정본 `+`/`=`/`-` — 미리보기 줄 수(1~3)를 바꾸고 **영속**한다(정본 `bump_lines`
            # → `set_ph_max_lines`). 값을 정하는 것은 같은 서버 옵션 한 벌이고, 바뀐 값은
            # 모든 클라의 status 로도 나간다(정본 클라가 쥔 `ph_max_lines` 가 같이 따라온다).
            cur = _clamp_lines(getattr(server, "_ph_max_lines", _DEFAULT_LINES))
            new = _clamp_lines(cur + (1 if req.get("do") == "lines+" else -1))
            if new != cur:
                server._ph_max_lines = new
                try:
                    server._save_opts()
                except Exception:
                    pass
                server._broadcast_session(sess)
            try:
                row = int(req.get("row") or 0)
            except (TypeError, ValueError):
                row = 0
            return self._open_spec(server, sess, selected=row,
                                   pane_id=_split_key(req.get("input"))[0])
        if req.get("do") == "close":
            return {"t": "plugin_screen_close", "id": "prompt-history"}
        return None

    def _open_spec(self, server, sess, selected=None, pane_id=None):
        """활성 패널의 프롬프트 목록 — **오래된 것이 위, 최신이 아래**이고 처음 커서는
        최신이다(정본 `_HistView`: `_sel = len - 1` · pytmux-616). 종전 판은 최신을 위로
        뒤집었고 이 자리 주석이 「정본 팝업과 같은 차례」라고 잘못 적고 있었다.

        ⚠ 목록은 **tail 슬라이스**여야 한다 — 점프(`scroll_to_prompt`)의 index 가 그
        슬라이스 기준이고, 정본 클라도 status 로 받는 것이 tail 이다. 전체를 실으면
        오래된 세션에서 index 가 통째로 어긋나 **엉뚱한 자리로 점프**한다."""
        from .server import _TAIL, _target_pane
        pane = _target_pane(sess, pane_id)
        hist = list(getattr(pane, "_ph_history", []) or [])[-_TAIL:]
        max_lines = _clamp_lines(getattr(server, "_ph_max_lines", _DEFAULT_LINES))
        mark = i18n.t("ph.truncated_mark")
        rows = []
        for i, text in enumerate(hist):
            # ★ 여러 줄 프롬프트는 **최대 N 줄**로 펼친다(정본 `_rebuild_rows`) — 넘치면
            #   마지막 표시 줄 끝에 `…`. 고르는 단위는 프롬프트 하나 그대로다(`more`).
            plines = text.splitlines() or [""]
            show = plines[:max_lines]
            if len(plines) > max_lines:
                show[-1] = show[-1] + mark
            rows.append({
                # `key` 는 그 줄의 **뜻**(히스토리 자리)이다 — 점프가 이 번호를 쓴다.
                # 패널을 함께 싣는다(`<패널>:<자리>`) — 점프·줄 수 다시 열기가 **이 판의 패널**로
                # 간다(pytmux-587 · 비활성 패널의 바로 연 판에서 활성 패널이 굴러가던 결함).
                "key": f"{pane.id}:{i}" if pane is not None else str(i),
                "label": show[0],
                "more": show[1:],
                "cols": [str(i + 1)],
            })
        hint, hint_spec = i18n.phrase("ph.popup_sub", n=max_lines)
        sel = len(rows) - 1 if selected is None else int(selected)
        return {
            "t": "plugin_screen", "id": "prompt-history", "kind": "list",
            # ⚠ 손으로 적으면 게이트가 못 본다(2026-08-02o) — 카탈로그가 곧 영어 표다.
            "title": i18n.t("ph.popup_title"),
            # 꼬리줄도 정본과 같다(`ph.popup_sub` — `+/− 미리보기 {n}행` 이 들어 있다).
            "hint": hint,
            "rows": rows,
            "selected": max(0, min(sel, len(rows) - 1)),
            # 정본 `_HistView.on_key` — `+`·`=` 는 늘이기, `-` 는 줄이기.
            "keys": {"enter": "jump", "+": "lines+", "=": "lines+", "-": "lines-"},
            "note": "" if rows else i18n.t("ph.empty"),
            "i18n": {"hint": hint_spec},
        }

    # ---- 클라이언트 ----
    def attach_client(self, app):
        app.ph_panes = {}                       # id -> {"id","h":[프롬프트…]}
        app.ph_max_lines = _DEFAULT_LINES

        def open_prompt_history(pane_id=None):
            from .screen import PromptHistoryScreen
            pid = pane_id if pane_id is not None else app.layout.get("active")
            entry = app.ph_panes.get(pid) or {}
            hist = list(entry.get("h") or [])
            app.push_screen(PromptHistoryScreen(pid, hist, app.ph_max_lines))
        app.open_prompt_history = open_prompt_history

    def handle_command(self, app, c, args):
        if c in _OPEN_ALIASES:
            app.open_prompt_history(app.layout.get("active"))
            return True
        # ★ 이름→액션·인자는 `cmdmap` 한 벌이 정한다(pytmux-35). 종전에는 그 규칙이
        #   여기에만 있어 네이티브 클라가 같은 명령을 보낼 길이 없었다.
        got = _cmdmap().to_action(c, args)
        if got is not None:
            action, kw = got
            app.send_cmd(action, **kw)
            return True
        return False

    def client_status(self, app, msg):
        if "ph_max_lines" in msg:
            app.ph_max_lines = _clamp_lines(msg["ph_max_lines"])
        # ★ 보는 쪽이 로컬 ↔ 원격(remote-attach 병합 탭)으로 바뀌면 표를 **버린다**
        #   (pytmux-587). 두 서버는 패널 id 를 같은 번호로 준다 — 안 버리면 ⓐ 상류 status 에
        #   `ph_panes` 가 없을 때 로컬 표가 그대로 남아 원격 패널 위에 로컬 프롬프트를 그리고,
        #   ⓑ 상류 델타의 `h` 없는 항목이 같은 id 의 **로컬** `h` 를 물려받는다.
        #   판정은 이 메시지의 `windows`(활성 탭이 remote 인가)로 한다 — 정본 `_viewing_remote`
        #   와 같은 술어이고, 키가 없는 메시지면 직전 판정을 지킨다.
        wins = msg.get("windows")
        if isinstance(wins, list):
            remote = any(isinstance(t, dict) and t.get("active") and t.get("remote")
                         for t in wins)
            if remote != getattr(app, "_ph_remote_view", False):
                app._ph_remote_view = remote
                app.ph_panes = {}
        if "ph_panes" not in msg:
            return
        prev = getattr(app, "ph_panes", {})
        new = {}
        for e in msg["ph_panes"]:
            pid = e["id"]
            if "h" not in e:                    # 디바운스로 빠진 항목은 직전 유지
                p = prev.get(pid)
                if p is not None and "h" in p:
                    e = {**e, "h": p["h"]}
            new[pid] = e
        app.ph_panes = new

    def client_render(self, app, cells, W, H):
        from .render import draw_pinned, draw_preview
        draw_preview(app, cells, W, H)
        draw_pinned(app, cells, W, H)

    def client_click(self, app, x, y, button=1):
        """고정 바(pytmux-560)를 누르면 그 패널의 프롬프트 히스토리 팝업을 연다.

        바는 앱 글 위에 덮어 그린 것이라, 받지 않으면 클릭이 **가려진 앱 글자**로 간다
        (fullscreen claude 는 그 자리를 다른 뜻으로 읽는다). 그래서 바 위의 왼쪽 클릭은
        여기서 끝낸다."""
        from .render import pinned_at
        if button != 1:
            return False
        pid = pinned_at(app, x, y)
        if pid is None:
            return False
        opener = getattr(app, "open_prompt_history", None)
        if opener is not None:
            opener(pid)
        return True

    def client_hit(self, app, x, y):
        from .render import pinned_at
        return pinned_at(app, x, y) is not None


PLUGIN = _PromptHistoryPlugin()


def _pane_arg(args):
    """`plugin_open` 인자에서 `pane=<id>` 를 찾는다(없으면 None → 활성 패널)."""
    for a in args or ():
        if isinstance(a, str) and a.startswith("pane="):
            try:
                return int(a[5:])
            except ValueError:
                return None
    return None


def _split_key(key):
    """줄 key → `(패널 id | None, 자리)`. `"<패널>:<자리>"` 또는 옛 모양 `"<자리>"`.
    못 읽으면 자리 -1."""
    text = str(key) if key is not None else ""
    pane, _, idx = text.rpartition(":")
    try:
        index = int(idx)
    except ValueError:
        return None, -1
    try:
        return (int(pane) if pane else None), index
    except ValueError:
        return None, index


def _jump_failed_notice(server, sess):
    """프롬프트 점프가 못 뛰었다는 알림(요청한 클라에게만 간다 · pytmux-544).

    활성 패널이 대체 화면이면(Claude fullscreen) 스크롤백에 그 프롬프트가 있을 수 없으니
    그 까닭을, 아니면 종전 문구(회전/재시작으로 사라짐)를 쓴다 — 종전에는 실패가 무동작이었고
    `ph.jump_fail` 은 등록만 되고 쓰이지 않았다."""
    win = getattr(sess, "active_window", None)
    pane = getattr(win, "active_pane", None) if win else None
    alt = bool(getattr(pane, "alt_active", False))
    # 글은 **카탈로그 키로만** 짓는다 — 서버발 글 픽스처(gen_server_strings)가 그 키로
    # 「무엇이 소켓으로 나가나」를 세고, GUI 는 그 짝으로 번역한다(리터럴 한국어 금지).
    key = "ph.jump_alt" if alt else "ph.jump_fail"
    return server._notice_msg(key, i18n.t("ph.jump_alt") if alt else i18n.t("ph.jump_fail"),
                              severity="warn")
