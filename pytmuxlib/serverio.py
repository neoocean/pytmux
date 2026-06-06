"""클라이언트 IPC·렌더 flush 루프·명령 디스패치 서버 로직 믹스인. `server.Server`
가 상속한다(§10 LLM 친화 리팩토링). 레이아웃/상태/트리 메시지 빌드(_layout_msg·
_status_msg·_tree_msg), 전체 동기화(_send_full)·브로드캐스트, flush 루프, 명령 처리
(_handle_cmd)·클라이언트 연결(handle_client)·입력/스크롤·서버 serve/shutdown 을 모은다.
동작 불변 — self.* 상태와 Server 의 다른 메서드를 그대로 참조한다."""
from __future__ import annotations

import asyncio
import base64
import os
import signal
import time
import traceback

from . import ipc, tokens, usagelog
from .claude import claude_account, claude_usage
from .model import ClientConn, Session
from .protocol import (FLUSH_HZ, MIN_H, MIN_W, PROTO_VERSION, frame_msg,
                       read_msg, write_frames, write_msg)


class ServerIOMixin:
    def _layout_msg(self, sess: Session, cols: int = None, rows: int = None):
        win = sess.active_window
        if not win:
            return None
        if cols is None or rows is None:
            cols, rows = self._session_size(sess)
        # 모든 패널 PTY 크기를 레이아웃에 맞춰 갱신
        panes, divs = win.compute_layout(0, 0, cols, rows)
        # 패널이 둘 이상이면 각 패널을 테두리 박스로 감싼다(활성=파랑, 비활성=회색).
        # 패널이 하나뿐이면 single_border 옵션이 켜져 있을 때만 아웃라인을 그린다
        # (off 면 단일 패널이 화면 전체를 내용으로 사용 — 사용자 요청).
        bordered = len(panes) >= 2 or self.single_border
        pane_msgs, titlebars = [], []
        for p in panes:
            x, y, w, h = p.rect
            box = None
            if bordered and w >= 3 and h >= 3:
                # 박스 테두리(상/하/좌/우) 안쪽이 내용 영역
                cx, cy, cw, ch = x + 1, y + 1, w - 2, h - 2
                box = [x, y, w, h]
            elif win.border_status and h > 1:
                cx, cy, cw, ch = x, y + 1, w, h - 1
                titlebars.append({"x": x, "y": y, "w": w, "title": p.title,
                                  "active": p is win.active_pane})
            else:
                cx, cy, cw, ch = x, y, w, h
            # Claude 헤더가 그려질 패널이면 내용 영역 맨 윗 한 행을 헤더에 양보한다
            # (#1). 내용은 cy+1 부터, 높이 -1. 헤더는 클라가 예약된 행(cy)에 그린다.
            hdr = self._should_reserve_header(p) and ch > 1
            if hdr:
                cy += 1
                ch -= 1
            p._hdr_reserved = hdr
            p.resize(cw, ch)
            p._mouse_sent = (p.mouse_track, p.mouse_sgr)
            pane_msgs.append({"id": p.id, "x": cx, "y": cy, "w": cw, "h": ch,
                              "title": p.title, "box": box,
                              "active": p is win.active_pane,
                              "claude_hdr": hdr,
                              "mouse": p.mouse_track, "mouse_sgr": p.mouse_sgr})
        return {
            "t": "layout",
            "cols": cols, "rows": rows,
            "panes": pane_msgs,
            "dividers": divs,
            "titlebars": titlebars,
            "bordered": bordered,
            "border_status": bool(win.border_status),
            "active": win.active_pane.id,
            "popup": self._popup_layout(sess, cols, rows),
        }

    # 패널이 원격 세션을 돌리는지 판정하는 fg 명령 이름들(소문자).
    _REMOTE_CMDS = {"ssh", "mosh", "mosh-client", "autossh", "sshpass",
                    "telnet", "et", "eternal-terminal", "kitten"}

    def _pane_overview(self, pane):
        """트리/개요용 패널 1건 정보: id·제목·fg 앱·로컬/원격·Claude 상태/사용량."""
        cmd = self._fg_command(pane.master_fd) or ""
        return {"id": pane.id, "title": (pane.title or "").strip(),
                "cmd": cmd, "remote": cmd.lower() in self._REMOTE_CMDS,
                "claude": pane._claude, "usage": pane._claude_usage,
                # 세션 누계 토큰(#18) — 트리 팝업이 ctx 와 함께 실제 토큰량을 보이게.
                "tokens": pane._session_tokens}

    def _tree_msg(self):
        return {"t": "tree", "current": None, "sessions": [
            {"name": s.name, "active": (s is None),
             "windows": [{"index": t.index, "name": t.name,
                          "active": (t is s.active_tab),
                          "panes": [self._pane_overview(p)
                                    for p in t.window.panes()]}
                         for t in s.tabs]}
            for s in self.sessions.values()]}

    def _pane_claude_entry(self, p, full):
        """status 의 패널별 Claude 항목. history 는 드물게 바뀌는데 매 status(토큰
        변동으로 자주 발화)마다 30개 프롬프트를 재직렬화·전송하면 ssh 트래픽이
        커진다(§4.5). 그래서 **변할 때만** 싣는다: `full`(신규 attach·구조 변경
        resync)이면 항상 실어 새 클라가 비어 보이지 않게 하고, 주기 flush(full=False)
        는 직전 전송분(_hist_sent)과 다를 때만 싣고 갱신한다. full 은 _hist_sent 를
        건드리지 않는다 — 주기 스트림(모든 클라 공유)의 추적을 오염시키면 다른 클라가
        델타를 놓친다."""
        e = {"id": p.id, "claude": p._claude, "prompt": p.last_prompt,
             "perm_mode": p._perm_mode}
        h = p.prompt_history[-30:]
        if full or h != p._hist_sent:
            e["history"] = h
            if not full:
                p._hist_sent = h
        return e

    def _status_msg(self, sess: Session, full=True):
        win = sess.active_window
        cap_path, cap_size = self._capture_info(win.active_pane if win else None)
        return {
            "t": "status",
            "session": sess.name,
            "windows": [{"index": t.index, "name": t.name,
                         "active": (i == sess.active_index),
                         "bell": t.has_bell, "activity": t.has_activity,
                         "claude_done": t.has_claude_done,
                         "claude": self._tab_claude(t)}
                        for i, t in enumerate(sess.tabs)],
            # 활성 윈도우 패널별 Claude 상태/마지막 프롬프트(헤더용). history 는
            # 변할 때만 싣는다(§4.5 — _pane_claude_entry).
            "panes_claude": [self._pane_claude_entry(p, full)
                             for p in (win.panes() if win else [])],
            "active_pane": win.active_pane.id if win else None,
            # 활성 패널이 Claude 면 토큰/컨텍스트 사용량(best-effort)
            "claude_usage": (win.active_pane._claude_usage
                             if win and win.active_pane
                             and win.active_pane._claude else None),
            # 활성 패널 계정 기준 — 그 계정에 속한 모든 세션/패널의 세션 누적 토큰을
            # 합산(§10 토큰 지속표시·계정별 합계). 계정 식별자도 함께 보내 표시에 곁들임.
            "claude_tokens": self._account_token_total(
                win.active_pane if win else None),
            "claude_account": (win.active_pane._claude_account
                               if win and win.active_pane else None),
            "zoomed": bool(win.zoomed) if win else False,
            "sync": bool(win.sync) if win else False,
            "pane_title": win.active_pane.title if win and win.active_pane else "",
            "autoresume": bool(win.active_pane.autoresume)
            if win and win.active_pane else False,
            "prompt_clear": bool(win.active_pane.prompt_clear_mode)
            if win and win.active_pane else False,
            # 프롬프트 단위 클리어 큐(#4): 활성 패널에 쌓인 명령들(표시·목록용)
            "prompt_clear_queue": (list(win.active_pane.prompt_clear_queue)
                                   if win and win.active_pane else []),
            "capture": self.capture,
            "capture_path": cap_path,
            "capture_size": cap_size,
            "claude_header": self.claude_header,
            "single_border": self.single_border,
            "claude_rules": self.claude_rules,   # #27 시작 규칙(에디터 초기값용)
            # 토큰 절감 설정(설정 팝업 토글 현재값 + 예산 경고). 전역 opts 그대로,
            # budget_level 은 일/세션 예산 대비 경고 레벨(0/80/100, M10).
            "auto_doc_clear": self.auto_doc_clear,
            "claude_auto_mode": self.claude_auto_mode,
            "claude_ctx_autoclear": self.claude_ctx_autoclear,
            "claude_ctx_threshold": self.claude_ctx_threshold,
            "claude_ctx_min_interval": self.claude_ctx_min_interval,
            "claude_ctx_action": self.claude_ctx_action,
            "token_budget_day": self.token_budget_day,
            "token_budget_session": self.token_budget_session,
            "token_budget_resume_gate": self.token_budget_resume_gate,
            "claude_budget_plan": self.claude_budget_plan,
            "budget_level": self._budget_level_for(
                win.active_pane if win else None),
            # M14 무장된 자동 액션 카운트다운(없으면 None): {kind, eta(초)}.
            "claude_pending": self._pending_action(
                win.active_pane if win else None),
        }

    async def _send_full(self, client: ClientConn):
        sess = client.session
        if not sess:
            return
        lay = self._layout_msg(sess)  # 세션 공유 크기(최소)로 계산
        if not lay:
            return
        await write_msg(client.writer, lay)
        win = sess.active_window
        # B2: full 재동기 — 이 클라의 델타 기준(_sent_rows)을 비우고 아래에서 보낸
        # full screen 으로 다시 채운다(이후 flush 가 이 기준 대비 델타를 보냄). 죽은
        # 패널의 stale 스냅샷도 함께 정리된다.
        client._sent_rows.clear()
        # A3: 활성 패널을 가장 먼저 render·전송해 사용자가 보는 화면의 first-paint 를
        # 앞당긴다(분할이 많아도 포커스 패널이 비활성 패널 직렬화 뒤로 안 밀림). 총량
        # 동일, 순서만 활성 우선.
        ap = win.active_pane
        panes = sorted(win.panes(), key=lambda p: p is not ap)
        for p in panes:
            rows, cursor = p.render(p is ap)
            p.dirty = False
            client._sent_rows[p.id] = rows
            await write_msg(client.writer, {"t": "screen", "pane": p.id,
                                            "rows": rows, "cursor": cursor})
        # 팝업 패널 화면도 함께(트리에 없으므로 별도로 보냄). 팝업은 항상 포커스라
        # 커서를 그린다(render(True)).
        if sess.popup and sess.popup.get("pane") is not None:
            pp = sess.popup["pane"]
            rows, cursor = pp.render(True)
            pp.dirty = False
            client._sent_rows[pp.id] = rows
            await write_msg(client.writer, {"t": "screen", "pane": pp.id,
                                            "rows": rows, "cursor": cursor})
        await write_msg(client.writer, self._status_msg(sess))

    _DELTA_MAX_RATIO = 0.7   # 바뀐 행이 이 비율 초과면 full screen 으로 폴백

    def _screen_frame(self, client, pane_id, rows, cursor):
        """이 클라에 보낼 screen 프레임 bytes(B2). 직전 전송(_sent_rows) 대비 바뀐 행이
        적으면 screen-delta(바뀐 [y, segs] 목록), 아니면(행 수 변동·최초·임계 초과)
        full screen. client._sent_rows[pane_id] 를 새 rows 로 갱신한다."""
        prev = client._sent_rows.get(pane_id)
        client._sent_rows[pane_id] = rows
        if prev is not None and len(prev) == len(rows):
            changed = [[y, rows[y]] for y in range(len(rows))
                       if rows[y] != prev[y]]
            if len(changed) <= len(rows) * self._DELTA_MAX_RATIO:
                return frame_msg({"t": "screen-delta", "pane": pane_id,
                                  "rows": changed, "cursor": cursor})
        return frame_msg({"t": "screen", "pane": pane_id,
                          "rows": rows, "cursor": cursor})

    def _broadcast_session(self, sess: Session):
        """구조 변경 후 해당 세션의 모든 클라이언트에 전체 상태를 다시 보낸다."""
        for c in self.clients:
            if c.session is sess:
                asyncio.create_task(self._send_full(c))

    def _notify_no_sessions(self):
        for c in self.clients:
            asyncio.create_task(write_msg(c.writer, {"t": "bye"}))
        self.running = False
        if self.loop:
            self.loop.call_later(0.2, self.shutdown)

    # ---- flush 루프 ----
    async def _flush_loop(self):
        interval = 1.0 / FLUSH_HZ
        while self.running:
            await asyncio.sleep(interval)
            for sess in list(self.sessions.values()):
                win = sess.active_window
                if not win:
                    continue
                clients = [c for c in self.clients if c.session is sess]
                if not clients:
                    continue
                status_changed = False
                # B2+B4: 패널은 1회 render 하고, 클라마다 직전 전송 대비 바뀐 행만
                # screen-delta(아니면 full screen)로 만들어, 그 클라의 프레임 bytes 를
                # 모아 한 번에 write+drain 한다(B4 배치). 클라별 _sent_rows 기준이라
                # 다중 클라·신규 attach 도 정합.
                frames_by_client = {c: [] for c in clients}
                for p in win.panes():
                    if not p.dirty:
                        continue
                    rows, cursor = p.render(p is win.active_pane)
                    p.dirty = False
                    for c in clients:
                        frames_by_client[c].append(
                            self._screen_frame(c, p.id, rows, cursor))
                # 라이브 PTY 팝업 패널(트리 밖)도 dirty 면 스트리밍한다.
                pu = sess.popup
                if pu and pu.get("pane") is not None and pu["pane"].dirty:
                    pp = pu["pane"]
                    rows, cursor = pp.render(True)
                    pp.dirty = False
                    for c in clients:
                        frames_by_client[c].append(
                            self._screen_frame(c, pp.id, rows, cursor))
                # Claude Code 상태/사용량 갱신(+ 비활성 탭 완료 감지, #22).
                # 새 휴리스틱(프롬프트/토큰/권한모드)이 특정 화면에서 터져도 flush
                # 루프 전체(=모든 클라 렌더)가 죽지 않게 가드한다(§10 안정성).
                try:
                    if self._scan_claude(sess, win):
                        status_changed = True
                except Exception:
                    self._log_error("scan_claude")
                # M14 카운트다운 틱: 무장된 자동 액션의 ETA(정수 초)나 종류가 바뀌면
                # status 를 재전송한다(출력 변화가 없어도 1초마다 카운트다운 갱신).
                # 무장/해제 전이도 여기서 잡혀 배지가 즉시 뜨고 사라진다.
                pend = self._pending_action(win.active_pane if win else None)
                pkey = (pend["kind"], pend["eta"]) if pend else None
                if pkey != sess._pending_key:
                    sess._pending_key = pkey
                    status_changed = True
                # 활동/벨 모니터링: 비활성 윈도우의 출력/BEL 을 플래그로
                for t in sess.tabs:
                    w = t.window
                    for p in w.panes():
                        if w is win:
                            p._activity = p._bell = False  # 보고 있는 탭
                            continue
                        if p._bell:
                            p._bell = False
                            if t.monitor_bell and not t.has_bell:
                                t.has_bell = True
                                status_changed = True
                        if p._activity:
                            p._activity = False
                            if t.monitor_activity and not t.has_activity:
                                t.has_activity = True
                                status_changed = True
                # Claude 헤더 행 예약(#1) 변동: 프롬프트가 처음 떠 헤더가 생기거나
                # Claude 가 끝나 헤더가 사라지면 내용 영역을 ±1 행 해야 한다. 레이아웃을
                # 다시 보내 PTY 리사이즈 + 새 geometry 를 반영(_send_full 이 status 포함).
                if any(self._should_reserve_header(p) != p._hdr_reserved
                       for p in win.panes()):
                    for c in clients:
                        await self._send_full(c)
                    continue
                if status_changed:
                    # 주기 status: history 는 변할 때만(§4.5, full=False).
                    sframe = frame_msg(self._status_msg(sess, full=False))
                    for c in clients:
                        frames_by_client[c].append(sframe)
                # 클라마다 이 프레임의 모든 메시지를 한 번에 write+drain(B4).
                for c in clients:
                    await write_frames(c.writer, frames_by_client[c])

    # ---- 명령 처리 ----
    async def _handle_cmd(self, client: ClientConn, msg: dict):
        sess = client.session
        if not sess:
            return
        action = msg.get("action")
        if action == "split":
            self.split_pane(sess, msg.get("orient", "lr"), path=msg.get("path"))
        elif action == "kill_pane":
            pane = sess.active_window.active_pane if sess.active_window else None
            if pane:
                self.kill_pane(sess, pane)
                return  # kill 은 트리 콜백에서 broadcast
        elif action == "select_pane":
            self.select_pane_dir(sess, msg.get("dir"))
        elif action == "select_pane_id":
            win = sess.active_window
            p = win.pane_by_id(msg.get("id")) if win else None
            if p:
                win.active_pane = p
        elif action == "cycle_pane":
            self.select_pane_cycle(sess)
        elif action == "last_pane":
            self.last_pane(sess)
        elif action == "set_sync":
            self.set_sync(sess, msg.get("value"))
        elif action == "set_pane_title":
            self.set_pane_title(sess, str(msg.get("title", "")))
        elif action == "set_border_status":
            self.set_border_status(sess, msg.get("value"))
        elif action == "respawn_pane":
            self.respawn_pane(sess)
        elif action == "search":
            self.search_pane(sess, msg.get("query"), msg.get("direction", "up"))
        elif action == "set_buffer":
            self.set_buffer(str(msg.get("text", "")))
            return
        elif action == "paste_buffer":
            self.paste_buffer(sess, int(msg.get("index", 0)))
            return
        elif action == "paste":
            self.paste_text(sess, str(msg.get("text", "")))
            return
        elif action == "request_buffers":
            await write_msg(client.writer, self._buffers_msg())
            return
        elif action == "clear_history":
            self.clear_history(sess)
            await self._send_full(client)
            return
        elif action == "capture_pane":
            n = self.capture_pane(sess, bool(msg.get("full")))
            await write_msg(client.writer, {"t": "captured", "chars": n})
            return
        elif action == "pipe_pane":
            self.pipe_pane(sess, str(msg.get("cmd", "")))
            return
        elif action == "popup_open":
            self.popup_open(sess, str(msg.get("cmd", "")),
                            want_w=msg.get("w"), want_h=msg.get("h"),
                            title=msg.get("title"))
            return  # popup_open 이 broadcast
        elif action == "popup_close":
            self.popup_close(sess)
            return  # popup_close 가 broadcast
        elif action == "save_layout":
            ok = self.save_layout()
            await write_msg(client.writer, {"t": "captured",
                                            "chars": 1 if ok else 0})
            return
        elif action == "restore_layout":
            self.restore_layout()
            await self._send_full(client)
            return
        elif action == "request_tree":
            await write_msg(client.writer, self._tree_msg())
            return
        elif action == "request_token_log":
            # 영속 토큰 로그(최근 N 줄)를 클라이언트로. 클라가 usagelog 로 시간/일/월×
            # 계정 집계해 팝업에 표시(라운드트립 없이 버킷/계정 전환).
            recs = usagelog.read(self.tokens_log_path,
                                 limit=int(msg.get("limit", 5000)))
            await write_msg(client.writer, {"t": "token_log", "records": recs})
            return
        elif action == "request_version":
            # version 명령 팝업: 이 서버가 로드한 코드 버전(p4 CL)·업타임·pid 회신.
            # 클라가 자기 버전/업타임과 합쳐 팝업을 띄운다.
            await write_msg(client.writer, {
                "t": "version", "version": self._code_version,
                "uptime": time.time() - self._boot_time, "pid": os.getpid()})
            return
        elif action == "request_restart_check":
            # restart-check 드라이런: 작업 보존 재시작 안전성 점검 결과 회신(부작용 없음).
            rep = self.restart_check()
            rep["t"] = "restart_check"
            await write_msg(client.writer, rep)
            return
        elif action == "set_claude_account":
            self.set_claude_account(sess, str(msg.get("name", "")))
            return
        elif action == "set_claude_perm_mode":
            # footer 클릭 팝업(§10 item 2): 활성/지정 패널 권한모드 목표 설정.
            self.set_claude_perm_mode(sess, str(msg.get("target", "")),
                                      pane_id=msg.get("id"))
            return
        elif action == "list_layouts":
            await write_msg(client.writer, {"t": "layouts",
                                            "names": self.list_tab_layouts()})
            return
        elif action == "save_tab_layout":
            ok = self.save_tab_layout(sess, str(msg.get("name", "")).strip())
            await write_msg(client.writer, {"t": "captured",
                                            "chars": 1 if ok else 0})
            return
        elif action == "load_tab_layout":
            if self.load_tab_layout(sess, str(msg.get("name", "")).strip(),
                                    new_tab=bool(msg.get("new"))):
                for c in [x for x in self.clients if x.session is sess]:
                    await self._send_full(c)
            return
        elif action == "set_autoresume":
            self.set_autoresume(sess, value=msg.get("value"),
                                msg=msg.get("msg"))
        elif action == "resize":
            self.resize_split(sess, msg.get("split_id"), msg.get("ratio", 0.5))
        elif action == "resize_dir":
            self.resize_dir(sess, msg.get("dir"), msg.get("cells", 3))
        elif action == "new_window":
            self.new_window(sess, path=msg.get("path"))
        elif action == "next_window":
            self.select_window(sess, (sess.active_index + 1) % len(sess.tabs))
        elif action == "prev_window":
            self.select_window(sess, (sess.active_index - 1) % len(sess.tabs))
        elif action == "select_window":
            self.select_window(sess, msg.get("index", 0))
        elif action == "last_window":
            self.last_window(sess)
        elif action == "move_window":
            self.move_window(sess, int(msg.get("index", 0)))
        elif action == "swap_window":
            self.swap_window(sess, int(msg.get("index", 0)))
        elif action == "move_tab":
            self.move_tab(sess, int(msg.get("index", 0)),
                          int(msg.get("to", 0)))
        elif action == "move_current_tab":
            self.move_current_tab(sess, str(msg.get("where", "")))
        elif action == "zoom":
            self.toggle_zoom(sess)
        elif action == "select_layout":
            self.select_layout(sess, msg.get("preset", "tiled"))
        elif action == "cycle_layout":
            self.cycle_layout(sess)
        elif action == "rotate":
            self.rotate_panes(sess, bool(msg.get("forward", True)))
        elif action == "swap_pane":
            self.swap_pane(sess, bool(msg.get("forward", True)))
        elif action == "swap_pane_to":
            self.swap_pane_ids(sess, int(msg.get("id", -1)),
                               int(msg.get("to_id", -1)))
        elif action == "break_pane":
            self.break_pane(sess)
        elif action == "join_pane":
            # src(끌어온 탭 인덱스) 지정 가능(#19 탭→패널 드래그). 미지정이면 직전 탭.
            self.join_pane(sess, src_index=msg.get("src"),
                           orient=msg.get("orient", "tb"))
        elif action == "rename_window":
            self.rename_window(sess, str(msg.get("name", "")).strip())
        elif action == "set_auto_rename":
            self.set_auto_rename(sess, msg.get("value"))
        elif action == "set_monitor":
            self.set_monitor(sess, msg.get("which", "activity"), msg.get("value"))
        elif action == "set_capture":
            self.set_capture(msg.get("value"))
        elif action == "set_claude_header":
            self.set_claude_header(msg.get("value"))
        elif action == "set_single_border":
            self.set_single_border(msg.get("value"))
        elif action == "set_coalesce":
            self.set_coalesce_repaints(msg.get("value"))
        elif action == "set_auto_doc_clear":
            self.set_auto_doc_clear(msg.get("value"))
        elif action == "set_claude_auto_mode":
            self.set_claude_auto_mode(msg.get("value"))
        elif action == "set_claude_ctx_autoclear":   # M11 잔량 자동 정리 토글
            self.set_claude_ctx_autoclear(msg.get("value"))
            self._broadcast_session(sess)
        elif action == "set_claude_ctx_action":      # M11 정리 방식(compact/doc-clear)
            self.set_claude_ctx_action(str(msg.get("value", "")))
            self._broadcast_session(sess)
        elif action == "set_claude_ctx_threshold":   # M11 잔량 임계(%)
            self.set_claude_ctx_threshold(msg.get("value"))
            self._broadcast_session(sess)
        elif action == "set_claude_ctx_min_interval":  # M14 정리 빈도 상한(초)
            self.set_claude_ctx_min_interval(msg.get("value"))
            self._broadcast_session(sess)
        elif action == "set_token_budget":           # M10 일/세션 예산
            self.set_token_budget(day=msg.get("day"), session=msg.get("session"))
            self._broadcast_session(sess)
        elif action == "set_token_budget_resume_gate":   # M12 예산 게이트 토글
            self.set_token_budget_resume_gate(msg.get("value"))
            self._broadcast_session(sess)
        elif action == "set_claude_budget_plan":         # M13 예산 압박 plan 유도
            self.set_claude_budget_plan(msg.get("value"))
            self._broadcast_session(sess)
        elif action == "set_claude_rules":      # #27 시작 규칙 저장(영속)
            self.set_claude_rules(msg.get("text", ""))
            self._broadcast_session(sess)       # status 로 새 규칙 회신
        elif action == "set_prompt_clear":
            self.set_prompt_clear(sess, msg.get("value"))
        elif action == "set_prompt_clear_message":
            self.set_prompt_clear_message(str(msg.get("msg", "")))
            return
        elif action == "pc_queue_add":
            self.pc_queue_add(sess, str(msg.get("cmd", "")))
        elif action == "pc_queue_clear":
            self.pc_queue_clear(sess)
        elif action == "kill_window":
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
            return
        elif action == "rename_session":
            self.rename_session(sess, str(msg.get("name", "")).strip())
        elif action == "new_session":
            new = self.new_session(client.cols, client.rows,
                                   str(msg.get("name", "")).strip() or None)
            client.session = new
            await self._send_full(client)
            return
        elif action == "switch_session":
            self.switch_session(client, str(msg.get("name", "")).strip())
            await self._send_full(client)
            return
        elif action == "detach_others":
            for c in list(self.clients):
                if c is not client and c.session is sess:
                    await write_msg(c.writer, {"t": "bye"})
            return
        elif action == "kill_session":
            name = str(msg.get("name") or sess.name)
            self.kill_session(name)
            if not self.sessions:
                self._notify_no_sessions()
                return
            for c in self.clients:
                await self._send_full(c)
            return
        elif action == "kill_server":
            self._notify_no_sessions()
            return
        elif action == "restart_server":
            # 작업 보존 재시작(re-exec). 셸/PTY 보존(docs/RESTART_SCENARIO.md).
            self.restart_server()
            return
        else:
            return
        await self._send_full(client)

    def _log_error(self, where: str):
        """방금 처리 중인 예외의 트레이스백을 `<sock>.error.log` 에 append 한다.

        데몬은 stderr 가 /dev/null 이라, 클라이언트 처리(attach/_send_full/dispatch)
        나 flush 루프에서 난 예외가 **조용히 삼켜지면** 진단 단서가 없다. 한 클라
        attach 가 _send_full 에서 터지면 화면이 일부만 그려진 채 연결이 끊겨(클라가
        '일부 나타났다 바로 종료') 이후 모든 attach 가 같은 상태로 브릭되는데,
        호출부가 이걸 잡아 로그를 남기고 계속 진행하게 해 자가복구한다. 로깅 자체는
        절대 실패를 전파하지 않는다(best-effort)."""
        try:
            path = ipc.state_base(self.sock_path) + ".error.log"
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n==== {stamp} [{where}] ====\n")
                f.write(traceback.format_exc())
        except Exception:
            pass

    async def handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter):
        first = await read_msg(reader)
        if first is None:
            writer.close()
            return
        # 와이어 프로토콜 버전 협상: 클라가 보낸 proto 가 서버와 다르면 명확히 거절한다
        # (구·신 버전 혼용 시 조용한 오작동 대신 명시적 실패). 필드가 없으면(구버전 클라)
        # 호환으로 간주해 통과시킨다 — 점진 롤아웃.
        cproto = first.get("proto")
        if cproto is not None and cproto != PROTO_VERSION:
            try:
                await write_msg(writer, {"t": "error", "error": "proto_mismatch",
                                         "server_proto": PROTO_VERSION})
            except (OSError, ConnectionError):
                pass
            writer.close()
            return
        t = first.get("t")
        if t == "list":
            await write_msg(writer, {"t": "list", "sessions": [
                {"name": s.name, "windows": len(s.tabs),
                 "panes": sum(len(t.window.panes()) for t in s.tabs)}
                for s in self.sessions.values()]})
            writer.close()
            return
        if t == "kill-server":
            await write_msg(writer, {"t": "ok"})
            writer.close()
            self._notify_no_sessions()
            return
        if t == "control":
            result = self.handle_control(first.get("line", ""))
            await write_msg(writer, {"t": "ok", "result": result})
            writer.close()
            return
        if t != "hello":
            writer.close()
            return

        client = ClientConn(writer)
        client.cols = max(MIN_W, int(first.get("cols", 80)))
        client.rows = max(MIN_H, int(first.get("rows", 24)))
        # 주의: append + 초기 _send_full 을 try 안에 둔다. 예전엔 try 밖이라
        # _send_full 이 한 번 터지면 ① 클라가 self.clients 에 남아 누수되고
        # ② 화면이 일부만 그려진 채 연결이 끊겨 클라가 즉시 종료, ③ 트레이스백도
        # 없이 이후 모든 attach 가 같은 상태로 브릭됐다(사용자 보고: "화면이 일부
        # 나타났다 바로 종료"). 이제 finally 가 항상 정리하고, _send_full 은 클라별로
        # 가드해 한 클라의 실패가 다른 클라 attach 를 막지 않게 한다.
        try:
            client.session = self.get_or_create_session(
                first.get("session"), client.cols, client.rows)
            self.clients.append(client)
            # 새 클라이언트가 붙으면 공유 크기가 바뀔 수 있어 같은 세션 전체를 갱신.
            # 한 클라의 _send_full 실패가 새 attach 를 죽이지 않게 개별 가드한다.
            for c in [x for x in self.clients if x.session is client.session]:
                try:
                    await self._send_full(c)
                except Exception:
                    self._log_error("send_full(initial)")

            while self.running:
                msg = await read_msg(reader)
                if msg is None:
                    break
                try:
                    mt = msg.get("t")
                    if mt == "ping":
                        # 네트워크 응답성 측정(§10): 클라가 RTT 를 재도록 즉시 echo.
                        await write_msg(client.writer, {"t": "pong",
                                                        "ts": msg.get("ts")})
                    elif mt == "input":
                        self._handle_input(client, msg)
                    elif mt == "resize":
                        client.cols = max(MIN_W, int(msg.get("cols", 80)))
                        client.rows = max(MIN_H, int(msg.get("rows", 24)))
                        # 미러링: 세션 공유 크기가 바뀌므로 모든 클라이언트 갱신
                        for c in [x for x in self.clients
                                  if x.session is client.session]:
                            await self._send_full(c)
                    elif mt == "scroll":
                        self._handle_scroll(client, msg)
                    elif mt == "cmd":
                        await self._handle_cmd(client, msg)
                except Exception:
                    # 한 메시지 처리 실패가 세션을 끊지 않게 잡아 로그만 남기고 계속.
                    self._log_error(f"dispatch({msg.get('t')})")
        except Exception:
            self._log_error("handle_client")
        finally:
            sess = client.session
            if client in self.clients:
                self.clients.remove(client)
            try:
                writer.close()
            except (OSError, ConnectionError):
                pass
            # 미러링: 남은 클라이언트는 공유 크기가 커질 수 있으니 갱신(개별 가드)
            if sess and sess in self.sessions.values():
                for c in [x for x in self.clients if x.session is sess]:
                    try:
                        await self._send_full(c)
                    except Exception:
                        self._log_error("send_full(teardown)")

    def _handle_input(self, client: ClientConn, msg: dict):
        sess = client.session
        win = sess.active_window if sess else None
        if not win:
            return
        # 팝업이 열려 있고 입력 대상이 팝업 패널이면 그 PTY 로만 직접 보낸다
        # (트리 밖이라 pane_by_id 로는 못 찾음; 동기화/프롬프트추적도 제외).
        pid = msg.get("pane")
        if sess.popup and sess.popup.get("pane") is not None \
                and pid == sess.popup["pane"].id:
            pp = sess.popup["pane"]
            try:
                if pp.pty is not None:
                    pp.pty.write(base64.b64decode(msg.get("data", "")))
            except OSError:
                pass
            return
        p = win.pane_by_id(pid) or win.active_pane
        data = base64.b64decode(msg.get("data", ""))
        # 마우스 패스스루: 커서 아래 패널 PTY 로만 raw 전달. 입력 동기화 대상이
        # 아니고(위치 기반), 프롬프트 추적/scroll 복귀도 건드리지 않는다.
        if msg.get("mouse"):
            try:
                if p.pty is not None:
                    p.pty.write(data)
            except OSError:
                pass
            return
        self._track_prompt(p, data)   # 마지막 입력 프롬프트 추적(Claude 헤더용)
        self._adc_disarm(p)   # 사용자 입력 = 활동 중 → 자동 doc→/clear 발화 취소(§10)
        self._cancel_resume(p)  # M14: 사용자가 입력했으면 자동재개 예약도 취소(§5.3
        #   선점 — 사용자가 직접 키를 쳤다 = 작업을 이어받음 → continue 중복 주입 방지)
        # 입력 동기화 시 윈도우 내 모든 패널에 동일 입력 전달
        targets = win.panes() if win.sync else [p]
        for t in targets:
            if t.scroll != 0 or t._match_abs is not None:
                t.scroll = 0  # 입력 시작 시 live 로 복귀(R6)
                t._match_abs = None
                t.dirty = True
            try:
                if t.pty is not None:
                    t.pty.write(data)
            except OSError:
                pass

    def _handle_scroll(self, client: ClientConn, msg: dict):
        sess = client.session
        win = sess.active_window if sess else None
        if not win:
            return
        p = win.pane_by_id(msg.get("pane")) or win.active_pane
        if msg.get("bottom"):
            p.scroll_to("bottom")
        elif msg.get("top"):
            p.scroll_to("top")
        else:
            p.scroll_by(int(msg.get("delta", 0)))

    def shutdown(self):
        self.running = False
        self._close_all_capfiles()
        for sess in self.sessions.values():
            for tab in sess.tabs:
                for p in tab.window.panes():
                    if p.pty is not None:
                        p.pty.terminate()       # SIGHUP
        try:
            # TCP 엔드포인트는 지울 파일이 없다(포트파일은 다음 기동이 덮어씀).
            if not ipc.is_tcp(self.sock_path) and os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except OSError:
            pass
        if self.loop:
            self.loop.stop()

    def _on_term_signal(self, signame: str):
        """외부 종료 시그널(SIGTERM/SIGHUP) 수신 시: 사실을 error.log 에 남기고 질서
        있게 종료한다. 핸들러가 없으면 기본동작 = 정리 없는 즉사라, master fd 가
        그대로 닫혀 pane 의 claude 들이 SIGHUP 으로 함께 죽는다(docs/INVESTIGATION
        §3.2 '서버 사망' 변종). 여기서 잡아 **흔적을 남기고**(평소 silent — 다음
        사후분석에서 외부 kill 여부 판별) shutdown() 으로 내려간다."""
        try:
            path = ipc.state_base(self.sock_path) + ".error.log"
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n==== {stamp} [signal {signame}] "
                        f"서버 종료 시그널 수신 → shutdown ====\n")
        except Exception:
            pass
        self.shutdown()

    def _install_signal_handlers(self) -> list:
        """외부 종료 시그널 핸들러(SIGTERM/SIGHUP)를 현재 루프에 설치하고, 설치한
        시그널 목록을 돌려준다(정리용). 핸들러가 없으면 SIGTERM/SIGHUP 은 '정리
        없는 즉사'라 pane claude 들이 SIGHUP 연쇄로 함께 죽는다(docs/INVESTIGATION
        §3.2/§8). 잡아서 로그를 남기고 깨끗이 정리 종료하게 한다.

        **프로덕션 엔트리(run_server)에서만** 켠다(self._handle_signals): 테스트는
        harness 가 한 프로세스에서 serve() 를 여러 번 띄우는데, 시그널 핸들러는
        프로세스 전역(self-pipe) 자원이라 루프 간 누수로 teardown 레이스를 만든다.
        Windows/미지원 환경은 조용히 패스."""
        installed = []
        if not getattr(self, "_handle_signals", False):
            return installed
        for signame in ("SIGTERM", "SIGHUP"):
            sig = getattr(signal, signame, None)
            if sig is None:
                continue
            try:
                self.loop.add_signal_handler(
                    sig, self._on_term_signal, signame)
                installed.append(sig)
            except (NotImplementedError, RuntimeError, ValueError):
                pass
        return installed

    def _remove_signal_handlers(self, installed: list):
        """설치된 시그널 핸들러를 제거한다(serve 종료/취소 시 — 누수 방지)."""
        for sig in installed:
            try:
                self.loop.remove_signal_handler(sig)
            except (RuntimeError, ValueError):
                pass

    async def serve(self):
        self.loop = asyncio.get_running_loop()
        signals = self._install_signal_handlers()
        try:
            # OS 별 listen 분기(Unix=AF_UNIX, Windows=TCP 루프백+포트파일)는 ipc 가 담당.
            # 확정 엔드포인트(TCP 면 실제 포트)를 패널 셸 $PYTMUX 에 게시한다.
            server, self.resolved_endpoint = await ipc.start_server(
                self.sock_path, self.handle_client)
            # 작업 보존 재시작(re-exec) 후: 상속된 PTY 를 채택해 셸을 살린 채 복원.
            # 성공 시 상태 파일을 지워(다음 평범한 재시작이 stale 채택을 안 하게).
            resumed = False
            rp = self._resume_path
            if rp and os.path.exists(rp) and not self.sessions:
                resumed = self.restore_resume_state(rp)
                try:
                    os.unlink(rp)
                except OSError:
                    pass
            # 재부팅/재시작 후: 저장된 레이아웃이 있으면 구조 복원(셸은 새로 시작)
            if not resumed and os.path.exists(self.layout_path) \
                    and not self.sessions:
                self.restore_layout()
            flush = asyncio.create_task(self._flush_loop())
            autoname = asyncio.create_task(self._autorename_loop())
            async with server:
                try:
                    await server.serve_forever()
                except asyncio.CancelledError:
                    pass
            flush.cancel()
            autoname.cancel()
        finally:
            self._remove_signal_handlers(signals)
