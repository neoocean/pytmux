"""서버측 명령 핸들러 — `action` → 핸들러 디스패치 테이블(`_CMD_TABLE`).

`serverio._handle_cmd` 는 페더레이션/원격 보기 **라우팅**만 맡고, 개별 명령 수행은 이
모듈의 핸들러가 맡는다(클라측 `clientcmd.py` 와 대칭). 종전엔 한 함수 안 67 분기
if/elif 체인이었다(§10-4⑨ God-함수, 검수마다 유보).

## disposition 계약 (★이 모듈의 핵심)

체인 시절 각 분기의 응답 방식은 **암묵적 제어흐름**이었다 — 본문이 `return` 하면
"핸들러가 응답 완결", 그냥 끝나면 함수 끝의 `await self._send_full(client)` 로
**폴스루**. 한 verb 만 오분류해도 명령이 **조용히** 깨진다(트리는 바뀌는데 화면 미갱신,
또는 이중 방송). 그래서 여기서는 disposition 을 제어흐름이 아니라 **테이블의 데이터**로
선언한다 — 등록 시점에 눈에 보이고, 드라이브 없이 테스트가 전수 대조할 수 있다
(`test_command_table_disposition_golden`).

- `FULL`    — 핸들러 수행 후 **요청 클라에 full 프레임 재동기**(`_send_full`). 체인의
              폴스루와 동일. 트리/레이아웃을 바꾸는 대다수 구조 명령.
- `HANDLED` — 핸들러가 응답을 **완결**(직접 회신했거나, 트리 콜백 broadcast 에 의존해
              일부러 안 보냄). 체인의 `return` 과 동일.
- `DYNAMIC` — 실행 시점에 갈리는 소수 — 핸들러가 `FULL`/`HANDLED` 를 **반환**해 결정한다
              (`kill_pane`: 죽일 패널이 있으면 트리 콜백 broadcast 에 맡기고 HANDLED,
              없으면 no-op 이라 폴스루 FULL — 체인의 조건부 `return` 등가).

핸들러 시그니처는 `async def (self, client, sess, msg)` 로 통일한다(대다수가 client 를
안 쓰지만 균일해야 테이블 디스패치가 단순하다). `self` 는 합성된 `Server` 다.
"""

from __future__ import annotations

import os
import time

FULL = "full"
HANDLED = "handled"
DYNAMIC = "dynamic"

# action -> (핸들러 함수, disposition). 클래스 본문의 @_cmd 데코레이터가 채운다.
_CMD_TABLE: dict[str, tuple] = {}


def _cmd(action: str, disp: str):
    """핸들러를 `action` 으로 등록한다(disposition 은 위 계약 참조).

    같은 action 을 두 번 등록하면 즉시 예외 — 체인 시절 뒤쪽 `elif` 가 조용히 죽던
    중복(첫 분기가 항상 이김)을 import 시점 오류로 드러낸다.
    """
    def deco(fn):
        if action in _CMD_TABLE:
            raise RuntimeError(f"명령 중복 등록: {action}")
        if disp not in (FULL, HANDLED, DYNAMIC):
            raise RuntimeError(f"알 수 없는 disposition: {action}={disp}")
        _CMD_TABLE[action] = (fn, disp)
        return fn
    return deco


class ServerCmdMixin:
    """`_CMD_TABLE` 에 등록된 명령 핸들러 모음(`Server` 합성 믹스인)."""

    # ── 패널 ──────────────────────────────────────────────────────────────
    @_cmd("split", FULL)
    async def _cmd_split(self, client, sess, msg):
        self.split_pane(sess, msg.get("orient", "lr"), path=msg.get("path"))

    @_cmd("kill_pane", DYNAMIC)
    async def _cmd_kill_pane(self, client, sess, msg):
        pane = sess.active_window.active_pane if sess.active_window else None
        if pane:
            self.kill_pane(sess, pane)
            return HANDLED   # kill 은 트리 콜백에서 broadcast
        return FULL          # 죽일 패널 없음 → no-op 이지만 재동기

    @_cmd("select_pane", FULL)
    async def _cmd_select_pane(self, client, sess, msg):
        self.select_pane_dir(sess, msg.get("dir"))

    @_cmd("select_pane_id", FULL)
    async def _cmd_select_pane_id(self, client, sess, msg):
        win = sess.active_window
        p = win.pane_by_id(msg.get("id")) if win else None
        if p:
            win.active_pane = p

    @_cmd("cycle_pane", FULL)
    async def _cmd_cycle_pane(self, client, sess, msg):
        self.select_pane_cycle(sess)

    @_cmd("last_pane", FULL)
    async def _cmd_last_pane(self, client, sess, msg):
        self.last_pane(sess)

    @_cmd("set_sync", FULL)
    async def _cmd_set_sync(self, client, sess, msg):
        self.set_sync(sess, msg.get("value"))

    @_cmd("set_pane_title", FULL)
    async def _cmd_set_pane_title(self, client, sess, msg):
        self.set_pane_title(sess, str(msg.get("title", "")))

    @_cmd("set_border_status", FULL)
    async def _cmd_set_border_status(self, client, sess, msg):
        self.set_border_status(sess, msg.get("value"))

    @_cmd("respawn_pane", FULL)
    async def _cmd_respawn_pane(self, client, sess, msg):
        self.respawn_pane(sess)

    @_cmd("search", FULL)
    async def _cmd_search(self, client, sess, msg):
        self.search_pane(sess, msg.get("query"), msg.get("direction", "up"))

    # ── 버퍼 / 붙여넣기 / 캡처 ────────────────────────────────────────────
    @_cmd("set_buffer", HANDLED)
    async def _cmd_set_buffer(self, client, sess, msg):
        self.set_buffer(str(msg.get("text", "")))

    @_cmd("paste_buffer", HANDLED)
    async def _cmd_paste_buffer(self, client, sess, msg):
        self.paste_buffer(sess, int(msg.get("index", 0)))

    @_cmd("paste", HANDLED)
    async def _cmd_paste(self, client, sess, msg):
        self.paste_text(sess, str(msg.get("text", "")))

    @_cmd("request_buffers", HANDLED)
    async def _cmd_request_buffers(self, client, sess, msg):
        await self._send_to(client, self._buffers_msg())

    @_cmd("clear_history", HANDLED)
    async def _cmd_clear_history(self, client, sess, msg):
        self.clear_history(sess)
        await self._send_full(client)

    @_cmd("capture_pane", HANDLED)
    async def _cmd_capture_pane(self, client, sess, msg):
        n = self.capture_pane(sess, bool(msg.get("full")))
        await self._send_to(client, {"t": "captured", "chars": n})

    @_cmd("pipe_pane", HANDLED)
    async def _cmd_pipe_pane(self, client, sess, msg):
        self.pipe_pane(sess, str(msg.get("cmd", "")))

    # ── 팝업 / 레이아웃 영속 ─────────────────────────────────────────────
    @_cmd("popup_open", HANDLED)     # popup_open 이 broadcast
    async def _cmd_popup_open(self, client, sess, msg):
        self.popup_open(sess, str(msg.get("cmd", "")),
                        want_w=msg.get("w"), want_h=msg.get("h"),
                        title=msg.get("title"))

    @_cmd("popup_close", HANDLED)    # popup_close 가 broadcast
    async def _cmd_popup_close(self, client, sess, msg):
        self.popup_close(sess)

    @_cmd("save_layout", HANDLED)
    async def _cmd_save_layout(self, client, sess, msg):
        ok = self.save_layout()
        await self._send_to(client, {"t": "captured",
                                     "chars": 1 if ok else 0})

    @_cmd("restore_layout", HANDLED)
    async def _cmd_restore_layout(self, client, sess, msg):
        self.restore_layout()
        await self._send_full(client)

    @_cmd("list_layouts", HANDLED)
    async def _cmd_list_layouts(self, client, sess, msg):
        await self._send_to(client, {"t": "layouts",
                                     "names": self.list_tab_layouts()})

    @_cmd("save_tab_layout", HANDLED)
    async def _cmd_save_tab_layout(self, client, sess, msg):
        ok = self.save_tab_layout(sess, str(msg.get("name", "")).strip())
        await self._send_to(client, {"t": "captured",
                                     "chars": 1 if ok else 0})

    @_cmd("load_tab_layout", HANDLED)
    async def _cmd_load_tab_layout(self, client, sess, msg):
        if self.load_tab_layout(sess, str(msg.get("name", "")).strip(),
                                new_tab=bool(msg.get("new"))):
            for c in [x for x in self.clients if x.session is sess]:
                await self._send_full(c)

    # ── 조회 요청(회신 타입 고정) ────────────────────────────────────────
    @_cmd("request_tree", HANDLED)
    async def _cmd_request_tree(self, client, sess, msg):
        await self._send_to(client, self._tree_msg())

    @_cmd("request_redraw", HANDLED)
    async def _cmd_request_redraw(self, client, sess, msg):
        # 화면 전체 강제 재그리기(§2.12, redraw/refresh 명령·prefix r). ① 각 패널
        # PTY 에 SIGWINCH 를 유발해 alt-screen 앱이 현재 화면을 전체 repaint 하게
        # 하고(스냅샷 갱신) ② 요청 클라에 layout+screen 전체 프레임을 다시 보낸다
        # (stale 스냅샷 교체). 원격 보기 중엔 _handle_cmd 의 릴레이 라우팅이 먼저
        # 잡아 업스트림으로 릴레이하므로 여기 로컬 경로엔 오지 않는다.
        self._induce_redraw_all()
        await self._send_full(client)

    @_cmd("request_version", HANDLED)
    async def _cmd_request_version(self, client, sess, msg):
        # version 명령 팝업: 이 서버가 로드한 코드 버전(p4 CL)·업타임·pid 회신.
        # 클라가 자기 버전/업타임과 합쳐 팝업을 띄운다.
        await self._send_to(client, {
            "t": "version", "version": self._code_version,
            "uptime": time.time() - self._boot_time, "pid": os.getpid()})

    @_cmd("request_restart_check", HANDLED)
    async def _cmd_request_restart_check(self, client, sess, msg):
        # restart-check 드라이런: 작업 보존 재시작 안전성 점검 결과 회신(부작용 없음).
        rep = self.restart_check()
        rep["t"] = "restart_check"
        await self._send_to(client, rep)

    @_cmd("set_claude_account", HANDLED)
    async def _cmd_set_claude_account(self, client, sess, msg):
        self.set_claude_account(sess, str(msg.get("name", "")))

    # ── 크기 / 탭 ────────────────────────────────────────────────────────
    @_cmd("resize", FULL)
    async def _cmd_resize(self, client, sess, msg):
        self.resize_split(sess, msg.get("split_id"), msg.get("ratio", 0.5))

    @_cmd("resize_dir", FULL)
    async def _cmd_resize_dir(self, client, sess, msg):
        self.resize_dir(sess, msg.get("dir"), msg.get("cells", 3))

    @_cmd("new_window", FULL)
    async def _cmd_new_window(self, client, sess, msg):
        self.new_window(sess, path=msg.get("path"))

    @_cmd("next_window", FULL)
    async def _cmd_next_window(self, client, sess, msg):
        self.select_window(sess, (sess.active_index + 1) % len(sess.tabs))

    @_cmd("prev_window", FULL)
    async def _cmd_prev_window(self, client, sess, msg):
        self.select_window(sess, (sess.active_index - 1) % len(sess.tabs))

    @_cmd("select_window", FULL)
    async def _cmd_select_window(self, client, sess, msg):
        # 원격(병합 전역) index 진입·로컬 복귀 라우팅은 _handle_cmd 프롤로그가 이미
        # 처리했다 — 여기 오는 건 로컬 탭 선택뿐.
        self.select_window(sess, msg.get("index", 0))

    @_cmd("last_window", FULL)
    async def _cmd_last_window(self, client, sess, msg):
        self.last_window(sess)

    @_cmd("move_window", FULL)
    async def _cmd_move_window(self, client, sess, msg):
        self.move_window(sess, int(msg.get("index", 0)))

    @_cmd("swap_window", FULL)
    async def _cmd_swap_window(self, client, sess, msg):
        self.swap_window(sess, int(msg.get("index", 0)))

    @_cmd("move_tab", FULL)
    async def _cmd_move_tab(self, client, sess, msg):
        self.move_tab(sess, int(msg.get("index", 0)),
                      int(msg.get("to", 0)))

    @_cmd("move_current_tab", FULL)
    async def _cmd_move_current_tab(self, client, sess, msg):
        self.move_current_tab(sess, str(msg.get("where", "")))

    @_cmd("set_pinned", FULL)
    async def _cmd_set_pinned(self, client, sess, msg):
        # 항목7: 탭 고정/해제. index 없으면 활성 탭. value 미지정이면 토글.
        idx = msg.get("index")
        idx = sess.active_index if idx is None else int(idx)
        if idx >= len(sess.tabs):
            # §12 ①: 원격(병합) 탭 핀 — per-link 다운스트림 로컬 집합(업스트림
            # 비전파). 핀은 보는 쪽 탭바 레이아웃 문제라 로컬에서만 토글한다.
            self.set_remote_pinned(sess, idx, msg.get("value"))
        elif "value" in msg:
            self.set_pinned(sess, idx, bool(msg.get("value")))
        else:
            self.toggle_pin(sess, idx)

    # ── 배치(arrange) ────────────────────────────────────────────────────
    @_cmd("zoom", FULL)
    async def _cmd_zoom(self, client, sess, msg):
        self.toggle_zoom(sess)

    @_cmd("select_layout", FULL)
    async def _cmd_select_layout(self, client, sess, msg):
        self.select_layout(sess, msg.get("preset", "tiled"))

    @_cmd("cycle_layout", FULL)
    async def _cmd_cycle_layout(self, client, sess, msg):
        self.cycle_layout(sess)

    @_cmd("rotate", FULL)
    async def _cmd_rotate(self, client, sess, msg):
        self.rotate_panes(sess, bool(msg.get("forward", True)))

    @_cmd("swap_pane", FULL)
    async def _cmd_swap_pane(self, client, sess, msg):
        self.swap_pane(sess, bool(msg.get("forward", True)))

    @_cmd("swap_pane_to", FULL)
    async def _cmd_swap_pane_to(self, client, sess, msg):
        self.swap_pane_ids(sess, int(msg.get("id", -1)),
                           int(msg.get("to_id", -1)))

    @_cmd("break_pane", FULL)
    async def _cmd_break_pane(self, client, sess, msg):
        self.break_pane(sess)

    @_cmd("join_pane", FULL)
    async def _cmd_join_pane(self, client, sess, msg):
        # src(끌어온 탭 인덱스) 지정 가능(#19 탭→패널 드래그). 미지정이면 직전 탭.
        self.join_pane(sess, src_index=msg.get("src"),
                       orient=msg.get("orient", "tb"))

    @_cmd("move_pane_to_tab", FULL)
    async def _cmd_move_pane_to_tab(self, client, sess, msg):
        # 헤더 드래그 pick-up → 다른 탭에 드롭(#1): id 패널을 to 탭으로 옮긴다.
        self.move_pane_to_tab(sess, int(msg.get("id", -1)),
                              int(msg.get("to", -1)))

    # ── 이름 / 모니터 / 옵션 ─────────────────────────────────────────────
    @_cmd("rename_window", FULL)
    async def _cmd_rename_window(self, client, sess, msg):
        self.rename_window(sess, str(msg.get("name", "")).strip())

    @_cmd("set_auto_rename", FULL)
    async def _cmd_set_auto_rename(self, client, sess, msg):
        self.set_auto_rename(sess, msg.get("value"))

    @_cmd("set_monitor", FULL)
    async def _cmd_set_monitor(self, client, sess, msg):
        self.set_monitor(sess, msg.get("which", "activity"), msg.get("value"))

    @_cmd("set_single_border", FULL)
    async def _cmd_set_single_border(self, client, sess, msg):
        self.set_single_border(msg.get("value"))

    @_cmd("set_window_size", FULL)
    async def _cmd_set_window_size(self, client, sess, msg):
        # 세션 공유 격자 크기 규칙(smallest|latest|largest, tmux window-size 동형).
        # value=None 이면 순환 토글. 공유 크기가 바뀌므로 같은 세션 전 클라를 새
        # 규칙으로 다시 미러링해 즉시 발효(작은 코-뷰어는 latest/largest 에서 crop).
        # 요청 클라는 이 루프 + 테이블 FULL 로 두 번 받지만(체인 시절과 동일) 무해.
        self.set_window_size(msg.get("value"))
        for c in [x for x in self.clients if x.session is sess]:
            try:
                await self._send_full(c)
            except Exception:
                self._log_error("send_full(set_window_size)")

    @_cmd("set_win_mouse_motion", FULL)
    async def _cmd_set_win_mouse_motion(self, client, sess, msg):
        # Windows any-motion 패스스루 토글(HANDOFF §10-H). 광고 mouse 레벨이
        # 바뀌므로 레이아웃을 다시 방송해 즉시 발효시킨다.
        self.set_win_mouse_motion(msg.get("value"))
        self._broadcast_session(sess)

    @_cmd("set_coalesce", FULL)
    async def _cmd_set_coalesce(self, client, sess, msg):
        self.set_coalesce_repaints(msg.get("value"))

    @_cmd("set_nest_auto_attach", FULL)
    async def _cmd_set_nest_auto_attach(self, client, sess, msg):
        # 원격 중첩 자동 승격 토글(NESTED_ATTACH ㉢) — 서버 내부 동작이라 클라
        # 렌더 변화 없음. value=None 이면 반전(클라 toggle).
        self.set_nest_auto_attach(msg.get("value"))

    @_cmd("set_vt_parser", FULL)
    async def _cmd_set_vt_parser(self, client, sess, msg):
        # VT 파서 백엔드("pyte"|"native") 선택. 재시작 시 발효(라이브 패널 즉시
        # 변화 없음) — 서버가 opts.json 영속. 클라가 발효 시점을 안내한다.
        self.set_vt_parser(msg.get("value"))

    @_cmd("set_plugin_enabled", HANDLED)
    async def _cmd_set_plugin_enabled(self, client, sess, msg):
        # 플러그인 관리 팝업 토글(PLUGIN_MANAGER_SCENARIO). disabled 갱신·영속 후
        # 전 클라에 새 status(disabled_plugins) 방송 — 각 클라가 자기 레지스트리에
        # 반영해 명령/훅이 즉시 빠지거나 돌아온다.
        self.set_plugin_enabled(str(msg.get("name", "")), msg.get("on"))
        self._broadcast_session(sess)
        await self._send_full(client)

    # ── 윈도우 / 세션 종료 ───────────────────────────────────────────────
    @_cmd("kill_window", HANDLED)
    async def _cmd_kill_window(self, client, sess, msg):
        self.kill_window(sess)
        if sess.name not in self.sessions:
            # 세션의 마지막 윈도우였음 → 다른 세션으로 옮기거나 종료
            if self.sessions:
                client.session = next(iter(self.sessions.values()))
                await self._send_full(client)
            else:
                self._notify_no_sessions()
            return
        await self._send_full(client)

    @_cmd("rename_session", FULL)
    async def _cmd_rename_session(self, client, sess, msg):
        self.rename_session(sess, str(msg.get("name", "")).strip())

    @_cmd("new_session", HANDLED)
    async def _cmd_new_session(self, client, sess, msg):
        new = self.new_session(client.cols, client.rows,
                               str(msg.get("name", "")).strip() or None)
        client.session = new
        await self._send_full(client)

    @_cmd("switch_session", HANDLED)
    async def _cmd_switch_session(self, client, sess, msg):
        self.switch_session(client, str(msg.get("name", "")).strip())
        await self._send_full(client)

    @_cmd("detach_others", HANDLED)
    async def _cmd_detach_others(self, client, sess, msg):
        for c in list(self.clients):
            if c is not client and c.session is sess:
                await self._send_to(c, {"t": "bye"})

    @_cmd("kill_session", HANDLED)
    async def _cmd_kill_session(self, client, sess, msg):
        name = str(msg.get("name") or sess.name)
        self.kill_session(name)
        if not self.sessions:
            self._notify_no_sessions()
            return
        for c in self.clients:
            await self._send_full(c)

    @_cmd("kill_server", HANDLED)
    async def _cmd_kill_server(self, client, sess, msg):
        self._notify_no_sessions()

    @_cmd("restart_server", HANDLED)
    async def _cmd_restart_server(self, client, sess, msg):
        # 작업 보존 재시작(re-exec). 셸/PTY 보존(docs/internal/RESTART_SCENARIO.md).
        self.restart_server()
