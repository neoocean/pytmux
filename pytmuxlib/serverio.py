"""클라이언트 IPC·렌더 flush 루프·명령 디스패치 서버 로직 믹스인. `server.Server`
가 상속한다(§10 LLM 친화 리팩토링). 레이아웃/상태/트리 메시지 빌드(_layout_msg·
_status_msg·_tree_msg), 전체 동기화(_send_full)·브로드캐스트, flush 루프, 명령 처리
(_handle_cmd)·클라이언트 연결(handle_client)·입력/스크롤·서버 serve/shutdown 을 모은다.
동작 불변 — self.* 상태와 Server 의 다른 메서드를 그대로 참조한다."""
from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import os
import secrets
import signal
import time
import traceback

from . import ipc, version
from .model import ClientConn, Pane, Session
from .protocol import (FLUSH_HZ, MAX_H, MAX_W, MIN_H, MIN_W, PROTO_VERSION,
                       clamp_dim, frame_msg, read_msg, write_frames, write_msg)
from .serverremote import _REMOTE_BLOCK_ACTIONS, _REMOTE_RELAY_ACTIONS


class ServerIOMixin:
    @staticmethod
    def _content_rect(rect, bordered, border_status):
        """패널 박스 rect(x,y,w,h) → (cx,cy,cw,ch, box, titled). 테두리/상태줄을
        차감한 내용 영역을 돌려준다. box=테두리 좌표 [x,y,w,h] 또는 None,
        titled=상태줄 타이틀바를 그릴지. 표시 루프와 줌 중 숨은 패널 리사이즈
        (§2.6)가 같은 차감 규칙을 공유하도록 추출했다."""
        x, y, w, h = rect
        if bordered and w >= 3 and h >= 3:
            # 박스 테두리(상/하/좌/우) 안쪽이 내용 영역
            return x + 1, y + 1, w - 2, h - 2, [x, y, w, h], False
        if border_status and h > 1:
            return x, y + 1, w, h - 1, None, True
        return x, y, w, h, None, False

    def _layout_msg(self, sess: Session, cols: int = None, rows: int = None):
        win = sess.active_window
        if not win:
            return None
        if cols is None or rows is None:
            cols, rows = self._session_size(sess)
        # 모든 패널 PTY 크기를 레이아웃에 맞춰 갱신
        panes, divs = win.compute_layout(0, 0, cols, rows)
        if win.zoomed and isinstance(win.active_pane, Pane):
            # 줌 중에는 활성 패널만 표시되지만(compute_layout 이 [active] 만 반환),
            # 숨은 패널도 정상 분할 크기로 **미리** 리사이즈해 둔다 — 안 그러면
            # (줌 중 창 리사이즈 + 숨은 패널 출력) 뒤 줌 해제 시점에야 옛 크기→새
            # 크기 reflow 가 한꺼번에 일어나 이미 출력된 줄이 깨진다(§2.6). win._layout
            # 은 모든 노드 rect 를 분할 좌표로 덮으므로(숨은 패널 select-pane-dir
            # 정확도엔 오히려 이로움) 활성 패널의 줌 표시용 full rect 만 복구한다.
            bg_panes = []
            win._layout(win.root, 0, 0, cols, rows, bg_panes, [])
            win.active_pane.rect = (0, 0, cols, rows)
            bg_bordered = len(bg_panes) >= 2 or self.single_border
            for p in bg_panes:
                if p is win.active_pane:
                    continue
                _, _, cw, ch, _, _ = self._content_rect(
                    p.rect, bg_bordered, win.border_status)
                p.resize(cw, ch)
        # 패널이 둘 이상이면 각 패널을 테두리 박스로 감싼다(활성=파랑, 비활성=회색).
        # 패널이 하나뿐이면 single_border 옵션이 켜져 있을 때만 아웃라인을 그린다
        # (off 면 단일 패널이 화면 전체를 내용으로 사용 — 사용자 요청).
        bordered = len(panes) >= 2 or self.single_border
        pane_msgs, titlebars = [], []
        for p in panes:
            cx, cy, cw, ch, box, titled = self._content_rect(
                p.rect, bordered, win.border_status)
            if titled:
                x, y, w, _h = p.rect
                titlebars.append({"x": x, "y": y, "w": w, "title": p.title,
                                  "active": p is win.active_pane})
            p.resize(cw, ch)
            p._mouse_sent = (p.mouse_track, p.mouse_sgr)
            pane_msgs.append({"id": p.id, "x": cx, "y": cy, "w": cw, "h": ch,
                              "title": p.title, "box": box,
                              "active": p is win.active_pane,
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
        """트리/개요용 패널 1건 정보: id·제목·fg 앱·로컬/원격. Claude 상태/사용량/토큰은
        플러그인이 server_pane_overview 훅으로 덧붙인다(플러그인 없으면 생략)."""
        cmd = self._fg_command(pane) or ""
        info = {"id": pane.id, "title": (pane.title or "").strip(),
                "cmd": cmd, "remote": cmd.lower() in self._REMOTE_CMDS}
        self.plugins.server_pane_overview(self, pane, info)
        return info

    def _tree_msg(self):
        return {"t": "tree", "current": None, "sessions": [
            # 세션-레벨 active 는 이 계층에 개념이 없다(어느 클라가 어느 세션에 attach
            # 했는지는 클라마다 달라 서버 전역의 '활성 세션'이 없음) — 항상 False.
            # 윈도우-레벨 active(아래 t is s.active_tab)가 권위값이고 소비자(ChooseTreeScreen)
            # 도 그것만 읽는다. (과거 `s is None` 은 항상-False 를 우회 표현해, 미래의 트리
            # 소비자가 무심코 물려받을 오인 소지가 있었다 — 명시적 False 로 교체.)
            {"name": s.name, "active": False,
             "windows": [{"index": t.index, "name": t.name,
                          "active": (t is s.active_tab),
                          "panes": [self._pane_overview(p)
                                    for p in t.window.panes()]}
                         for t in s.tabs]}
            for s in self.sessions.values()]}

    def _status_msg(self, sess: Session, full=True, client=None):
        # §1.7 Stage 3: 원격 탭을 보는 클라는 업스트림 status 기반 머지본(원격 탭
        # active 하이라이트 + Claude 헤더 등 부가필드)을 받는다 — per-client.
        if client is not None:
            rs = self._remote_status_override(sess, client)
            if rs is not None:
                return rs
        win = sess.active_window
        msg = {
            "t": "status",
            "session": sess.name,
            "windows": [{"index": t.index, "name": t.name,
                         "active": (i == sess.active_index),
                         "bell": t.has_bell, "activity": t.has_activity,
                         "claude_done": t.has_claude_done}
                        for i, t in enumerate(sess.tabs)],
            "active_pane": win.active_pane.id if win else None,
            "zoomed": bool(win.zoomed) if win else False,
            "sync": bool(win.sync) if win else False,
            "pane_title": win.active_pane.title if win and win.active_pane else "",
            "single_border": self.single_border,
            # 플러그인 관리(PLUGIN_MANAGER_SCENARIO): 비활성 플러그인 이름 목록. 클라가
            # 자기 레지스트리에 set_disabled 로 반영해 명령/훅을 거른다.
            "disabled_plugins": sorted(self.plugins.disabled),
        }
        # REC capture/capture_path/capture_size 는 plugins/rec 의 server_status 훅이
        # 채운다(아래 plugins.server_status). 플러그인 부재 시 키가 빠진다.
        # Claude 필드(패널별 상태·history·토큰·사용량·예산·팝업·full-only 옵션 12개와
        # windows[].claude 탭 집계)는 플러그인이 server_status 훅으로 in-place 로 채운다.
        # 플러그인이 없으면 status 에 Claude 키가 빠지고, 클라(역시 플러그인 부재)는
        # 그 키를 읽지 않는다(delete-to-disable). 채워질 키/값은 플러그인이 있을 때
        # 종전과 동일하다(서버 테스트가 claude_tokens 등 키를 그대로 검증).
        self.plugins.server_status(self, sess, win, msg, full)
        # §1.7: 원격 링크의 탭을 병합(⇄host:이름, 전역 index 는 로컬 뒤 연속) — 탭바에
        # 양쪽이 항상 보이고, select_window(전역 index)로 원격 탭에 진입한다.
        msg["windows"] += self._remote_tabs(len(sess.tabs), client)
        return msg

    async def _send_full(self, client: ClientConn):
        sess = client.session
        if not sess:
            return
        # §1.7: 원격 탭을 보는 클라에겐 로컬 layout/screen 을 보내지 않는다(화면은
        # 업스트림 전달분이 권위) — 병합 탭바용 status 만 갱신한다. 모든 브로드캐스트
        # 경로(_broadcast_session·flush 헤더예약·resize 미러링)가 이 가드를 공유한다.
        if getattr(client, "remote_view", None):
            await write_msg(client.writer, self._status_msg(sess, client=client))
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
                                            "rows": rows, "cursor": cursor,
                                            "wrap": p._last_wrap})
        # 팝업 패널 화면도 함께(트리에 없으므로 별도로 보냄). 팝업은 항상 포커스라
        # 커서를 그린다(render(True)).
        if sess.popup and sess.popup.get("pane") is not None:
            pp = sess.popup["pane"]
            rows, cursor = pp.render(True)
            pp.dirty = False
            client._sent_rows[pp.id] = rows
            await write_msg(client.writer, {"t": "screen", "pane": pp.id,
                                            "rows": rows, "cursor": cursor,
                                            "wrap": pp._last_wrap})
        await write_msg(client.writer, self._status_msg(sess, client=client))

    _DELTA_MAX_RATIO = 0.7   # 바뀐 행이 이 비율 초과면 full screen 으로 폴백

    def _screen_frame(self, client, pane_id, rows, cursor, wrap=None):
        """이 클라에 보낼 screen 프레임 bytes(B2). 직전 전송(_sent_rows) 대비 바뀐 행이
        적으면 screen-delta(바뀐 [y, segs] 목록), 아니면(행 수 변동·최초·임계 초과)
        full screen. client._sent_rows[pane_id] 를 새 rows 로 갱신한다.

        wrap(soft-wrap 연속원 행 인덱스)은 행 단위 델타 대상이 아니라 **매 프레임 전체
        리스트를 그대로** 싣는다(보통 빈 리스트~수개 정수라 작고, 델타 머지 복잡도를
        피한다). 클라는 메시지마다 자기 wrap 셋을 통째로 교체한다."""
        prev = client._sent_rows.get(pane_id)
        client._sent_rows[pane_id] = rows
        if prev is not None and len(prev) == len(rows):
            changed = [[y, rows[y]] for y in range(len(rows))
                       if rows[y] != prev[y]]
            if len(changed) <= len(rows) * self._DELTA_MAX_RATIO:
                return frame_msg({"t": "screen-delta", "pane": pane_id,
                                  "rows": changed, "cursor": cursor,
                                  "wrap": wrap or []})
        return frame_msg({"t": "screen", "pane": pane_id,
                          "rows": rows, "cursor": cursor, "wrap": wrap or []})

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
                        if c.remote_view:   # §1.7 원격 보기 중 — 로컬 화면 미전송
                            continue
                        frames_by_client[c].append(
                            self._screen_frame(c, p.id, rows, cursor,
                                               p._last_wrap))
                # 라이브 PTY 팝업 패널(트리 밖)도 dirty 면 스트리밍한다.
                pu = sess.popup
                if pu and pu.get("pane") is not None and pu["pane"].dirty:
                    pp = pu["pane"]
                    rows, cursor = pp.render(True)
                    pp.dirty = False
                    for c in clients:
                        if c.remote_view:   # §1.7
                            continue
                        frames_by_client[c].append(
                            self._screen_frame(c, pp.id, rows, cursor,
                                               pp._last_wrap))
                # Claude Code 상태/사용량 갱신(+ 비활성 탭 완료 감지, #22).
                # 새 휴리스틱(프롬프트/토큰/권한모드)이 특정 화면에서 터져도 flush
                # 루프 전체(=모든 클라 렌더)가 죽지 않게 가드한다(§10 안정성).
                try:
                    if self.plugins.server_scan(self, sess, win):
                        status_changed = True
                except Exception:
                    self._log_error("scan_claude")
                # M14 카운트다운 틱: 무장된 자동 액션의 ETA(정수 초)나 종류가 바뀌면
                # status 를 재전송한다(출력 변화가 없어도 1초마다 카운트다운 갱신).
                # 무장/해제 전이도 여기서 잡혀 배지가 즉시 뜨고 사라진다.
                pend = self.plugins.server_pending(self, win.active_pane if win else None)
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
                if status_changed:
                    # 주기 status: history 는 변할 때만(§4.5, full=False). §1.7
                    # Stage 3: status 가 클라별(원격 보기 오버라이드)이라 프레임도
                    # 클라별로 만든다(클라 수는 보통 1~2 — 비용 미미).
                    for c in clients:
                        frames_by_client[c].append(frame_msg(
                            self._status_msg(sess, full=False, client=c)))
                # 클라마다 이 프레임의 모든 메시지를 한 번에 write+drain(B4).
                for c in clients:
                    await write_frames(c.writer, frames_by_client[c])

    async def _usage_loop(self):
        """M19+ 그림자 /usage 자동 갱신: usage_refresh_sec 마다 refresh_usage 를 돌려
        세션·주간 한도 표시가 stale(폰 앱과 어긋남) 해지지 않게 한다. 기존엔 토큰
        화면을 열 때만 on-demand 였다(요청). interval=0 이면 비활성. Claude 패널이
        없으면 건너뛰어 불필요한 숨은 세션 생성을 막는다. 부팅 직후 한 번 채운 뒤 주기."""
        interval = self.usage_refresh_sec
        if interval <= 0:
            return
        # 부팅/트러스트 대화상자와 안 겹치게 약간 늦춰 첫 채움(패널이 비지 않게).
        await asyncio.sleep(min(20.0, interval))
        while self.running:
            # 그림자 /usage 갱신 1회(플러그인 없으면 no-op → 루프는 그냥 sleep 만 돈다).
            await self.plugins.server_usage_refresh(self)
            await asyncio.sleep(interval)

    # ---- 명령 처리 ----
    async def _handle_cmd(self, client: ClientConn, msg: dict):
        sess = client.session
        if not sess:
            return
        action = msg.get("action")
        # §1.7 페더레이션 진입/해제·릴레이 — 다른 분기보다 먼저:
        # ① remote_attach/remote_detach 는 어디서든 로컬 처리.
        # ② select_window 는 병합 전역 index 공간 — 원격 index 면 보기 진입(릴레이),
        #    로컬 index 면 보기 해제 후 평소대로(아래로 진행 → 끝의 _send_full 이
        #    로컬 화면 복귀까지 처리).
        # ③ 보는 중엔 화이트리스트 action 을 업스트림으로 릴레이.
        if action == "remote_attach":
            target = msg.get("host") or msg.get("endpoint") or "?"
            ok = await self.remote_attach(sess, host=msg.get("host"),
                                          endpoint=msg.get("endpoint"))
            if ok:
                self._remote_status_broadcast()
            # 결과를 요청 클라에 알린다(notice) — 실패가 서버 로그에만 남아
            # "아무 일도 안 일어남"으로 보이던 갭(사용자 보고 2026-06-12) 해소.
            text = (f"remote-attach {target}: 원격 탭 병합됨" if ok else
                    f"remote-attach {target} 실패 — "
                    f"{getattr(self, '_remote_last_err', '') or '서버 error.log 참조'}")
            await write_msg(client.writer, {"t": "notice", "text": text})
            return
        if action == "remote_detach":
            self.remote_detach(msg.get("host"))
            return
        if action == "remote_new_window":
            # remote-new-tab <host>: 원격에 새 터미널을 만들어 새 탭으로 보여준다
            # (필요하면 먼저 attach). 성공 시 화면·탭바는 업스트림 new_window→_send_full
            # 전달분(reader)이 그린다 — select_window 진입과 동형이라 여기서 따로
            # broadcast/_send_full 하지 않는다(로컬 화면이 끼어들면 깜빡임). 실패만 notice.
            target = msg.get("host") or msg.get("endpoint") or "?"
            ok = await self.remote_new_window(client, sess,
                                              host=msg.get("host"),
                                              endpoint=msg.get("endpoint"))
            if not ok:
                await write_msg(client.writer, {"t": "notice", "text":
                    f"remote-new-tab {target} 실패 — "
                    f"{getattr(self, '_remote_last_err', '') or '서버 error.log 참조'}"})
            return
        if action == "select_window":
            idx = int(msg.get("index", 0))
            if idx >= len(sess.tabs):
                if await self.remote_select_window(client, sess, idx):
                    return     # 화면은 업스트림 _send_full 전달분이 그린다
            elif client.remote_view:
                client.remote_view = None   # 로컬 탭 복귀(아래 평소 경로)
        elif client.remote_view and action in _REMOTE_RELAY_ACTIONS:
            if self.remote_relay(client, msg):
                return
        elif client.remote_view and action in _REMOTE_BLOCK_ACTIONS:
            # §1.7-c 섞임 금지: 원격 보기 중 경계 횡단/로컬 트리 조작은 거부.
            # (조용한 로컬 실행도, index 공간이 안 맞는 릴레이도 모두 위험.)
            await write_msg(client.writer, {"t": "notice", "text":
                            "원격 탭에서는 사용할 수 없는 명령입니다 — "
                            "원격↔로컬 패널/탭은 섞을 수 없습니다(§1.7)"})
            return
        elif client.remote_view and action == "new_window":
            # 원격 보기 중 새 탭 = 로컬 새 탭 의도로 본다 — 보기를 해제하고 아래
            # 평소 경로로 진행해 사용자가 새 로컬 탭을 **보면서** 받게 한다(종전엔
            # 화면은 원격인 채 보이지 않는 로컬 탭이 생겼다).
            client.remote_view = None
        elif client.remote_view is None and action in (
                "join_pane", "move_pane_to_tab", "move_tab", "move_window",
                "swap_window"):
            # §1.7-c 섞임 금지(로컬 쪽): 병합 전역 index 의 원격 탭을 겨냥한 패널/탭
            # 이동을 거부한다(탭 드래그로 원격 탭 위에 드롭, 패널을 원격 탭으로 이동
            # 등). 원격 index 는 len(sess.tabs) 이상의 병합 공간이다.
            n = len(sess.tabs)
            refs = [msg.get(k) for k in ("src", "to", "index")
                    if msg.get(k) is not None]
            if any(isinstance(r, int) and r >= n for r in refs):
                await write_msg(client.writer, {"t": "notice", "text":
                                "원격 탭으로/원격 탭을 이동할 수 없습니다 — "
                                "원격↔로컬 패널/탭은 섞을 수 없습니다(§1.7)"})
                return
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
        elif action == "move_pane_to_tab":
            # 헤더 드래그 pick-up → 다른 탭에 드롭(#1): id 패널을 to 탭으로 옮긴다.
            self.move_pane_to_tab(sess, int(msg.get("id", -1)),
                                  int(msg.get("to", -1)))
        elif action == "rename_window":
            self.rename_window(sess, str(msg.get("name", "")).strip())
        elif action == "set_auto_rename":
            self.set_auto_rename(sess, msg.get("value"))
        elif action == "set_monitor":
            self.set_monitor(sess, msg.get("which", "activity"), msg.get("value"))
        elif action == "set_single_border":
            self.set_single_border(msg.get("value"))
        elif action == "set_coalesce":
            self.set_coalesce_repaints(msg.get("value"))
        elif action == "set_nest_auto_attach":
            # 원격 중첩 자동 승격 토글(NESTED_ATTACH ㉢) — 서버 내부 동작이라 클라
            # 렌더 변화 없음. value=None 이면 반전(클라 toggle).
            self.set_nest_auto_attach(msg.get("value"))
        elif action == "set_plugin_enabled":
            # 플러그인 관리 팝업 토글(PLUGIN_MANAGER_SCENARIO). disabled 갱신·영속 후
            # 전 클라에 새 status(disabled_plugins) 방송 — 각 클라가 자기 레지스트리에
            # 반영해 명령/훅이 즉시 빠지거나 돌아온다.
            self.set_plugin_enabled(str(msg.get("name", "")), msg.get("on"))
            self._broadcast_session(sess)
            await self._send_full(client)
            return
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
            # 먼저 플러그인 명령 훅(Claude set_claude_*/token/pc/refresh_usage 등).
            # 후속 지시를 반환하면 그대로 따른다 — 'broadcast'/'send_full' 은 원래
            # _handle_cmd 가 하던 _broadcast_session+_send_full 동작을 그대로 재현한다.
            directive = self.plugins.server_command(self, client, sess, action, msg)
            if directive == "handled":
                return
            if directive == "send_full":
                await self._send_full(client)
                return
            if directive == "broadcast":
                self._broadcast_session(sess)   # 세션 전 클라에 새 권위값 status
                await self._send_full(client)
                return
            # 그 외 알 수 없는 action → 플러그인 요청 핸들러에 위임. 회신 메시지(dict)를
            # 반환하면 그대로 클라로 보낸다(ncd 의 request_nc_list 등). 없으면 무시.
            resp = self.plugins.handle_server_request(self, sess, action, msg)
            if resp is not None:
                await write_msg(client.writer, resp)
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
        # 피어 UID 검증(F2): Unix 소켓이면 상대 프로세스의 UID 가 서버와 같은지 확인한다
        # (파일권한 0700/0600 위의 심층 방어). 다른 UID 면 거절. 검증 불가(None)면 통과
        # (TCP·미지원 OS — 토큰 F1 이 1차 방어). docs/SECURITY_REVIEW.md F2.
        if not ipc.is_tcp(self.sock_path) and hasattr(os, "getuid"):
            puid = ipc.peer_uid(writer.get_extra_info("socket"))
            if puid is not None and puid != os.getuid():
                try:
                    await write_msg(writer, {"t": "error", "error": "auth_failed"})
                except (OSError, ConnectionError):
                    pass
                writer.close()
                return
        # 연결 인증(F1): 토큰이 설정돼 있으면(=실제 데몬) 첫 메시지의 token 을 상수시간
        # 비교로 검증한다. 토큰을 읽을 수 있는 건 0600 파일을 둔 같은 UID 뿐이므로,
        # Windows TCP 루프백에서 다른 로컬 사용자의 접속을 차단한다. 불일치/누락이면
        # 명확히 거절하고 연결을 끊는다(서버는 살아 있음).
        if self.auth_token is not None:
            tok = first.get("token")
            if not isinstance(tok, str) or not hmac.compare_digest(
                    tok, self.auth_token):
                try:
                    await write_msg(writer, {"t": "error", "error": "auth_failed"})
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
        client.cols = clamp_dim(first.get("cols", 80), MIN_W, MAX_W, 80)
        client.rows = clamp_dim(first.get("rows", 24), MIN_H, MAX_H, 24)
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
            # 재접속/신규 attach 직후 살아 있는 TUI(Claude·vim 등)가 idle 라 출력이
            # 없으면 pyte 스냅샷이 직전 리사이즈로 깨진 채 남아, 새 클라가 깨진 화면을
            # 받는다(사용자 보고: ssh 재접속 시 프롬프트 박스 테두리 소실·빈 입력칸 2줄,
            # 입력해도 안 돌아옴). 특히 **같은 크기 재접속**은 resize 가 SIGWINCH 를 안
            # 보내 idle 앱이 영영 다시 안 그린다(_induce_redraw_all 주석 참조 — 지금까진
            # 재시작 복원에서만 불렀다). attach 직후 한 번 SIGWINCH 를 유발해 앱이 현재
            # 크기로 전체 repaint → 스냅샷을 새로 써 깨끗한 프레임이 흐르게 한다.
            self._induce_redraw_all()

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
                        # §1.7 원격 보기: 입력을 업스트림으로(릴레이 실패=링크 사망
                        # 직후 레이스 → 보기 해제돼 로컬 처리로 폴백).
                        if client.remote_view and self.remote_relay(client, msg):
                            continue
                        self._handle_input(client, msg)
                    elif mt == "resize":
                        # §1.7: 보는 중이면 업스트림에도 리사이즈를 알려 원격이 이
                        # 크기로 다시 렌더하게 한다(로컬 갱신도 그대로 — 돌아올 때
                        # 정확한 로컬 레이아웃 유지. _send_full 의 보기 가드가 보는
                        # 클라에겐 status 만 보낸다).
                        if client.remote_view:
                            self.remote_relay(client, msg)
                        client.cols = clamp_dim(msg.get("cols", 80),
                                                MIN_W, MAX_W, 80)
                        client.rows = clamp_dim(msg.get("rows", 24),
                                                MIN_H, MAX_H, 24)
                        # 미러링: 세션 공유 크기가 바뀌므로 모든 클라이언트 갱신
                        for c in [x for x in self.clients
                                  if x.session is client.session]:
                            await self._send_full(c)
                    elif mt == "scroll":
                        if client.remote_view and self.remote_relay(client, msg):
                            continue
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
        # 입력 데이터 base64 디코드(F6): 손상·악의 base64 가 예외를 던지지 않게 한 곳에서
        # 가드한다(binascii.Error 는 ValueError 하위). 실패하면 그 입력만 무시.
        try:
            data = base64.b64decode(msg.get("data", ""))
        except (binascii.Error, ValueError):
            return
        # 팝업이 열려 있고 입력 대상이 팝업 패널이면 그 PTY 로만 직접 보낸다
        # (트리 밖이라 pane_by_id 로는 못 찾음; 동기화/프롬프트추적도 제외).
        pid = msg.get("pane")
        if sess.popup and sess.popup.get("pane") is not None \
                and pid == sess.popup["pane"].id:
            pp = sess.popup["pane"]
            try:
                if pp.pty is not None:
                    pp.pty.write(data)
            except OSError:
                pass
            return
        p = win.pane_by_id(pid) or win.active_pane
        # 마우스 패스스루: 커서 아래 패널 PTY 로만 raw 전달. 입력 동기화 대상이
        # 아니고(위치 기반), 프롬프트 추적/scroll 복귀도 건드리지 않는다.
        if msg.get("mouse"):
            try:
                if p.pty is not None:
                    p.pty.write(data)
            except OSError:
                pass
            return
        # 사용자 입력 1건의 Claude 부수효과(플러그인 server_input): 마지막 프롬프트
        # 추적(헤더용)·자동 doc→/clear·자동 /compact·자동재개 예약 해제(사용자가 키를
        # 쳤다 = 작업 이어받음 → 자동 개입/중복 주입 방지). 플러그인 없으면 no-op.
        self.plugins.server_input(self, p, data)
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
        self.remote_shutdown()   # §1.7: 링크/ssh/보류 재연결 동기 정리
        self.plugins.server_shutdown(self)   # REC 캡처 파일 닫기 등(plugins/rec)
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

    async def _capture_version(self):
        """실행 코드 버전(p4 CL)을 이벤트 루프 밖에서 캡처해 채운다(자리표시자 "…"
        대체). `p4 changes` 가 수백 ms 걸리므로 executor 로 돌려 listen·입출력을
        막지 않는다. 실패(취소 등)는 무시 — 버전은 best-effort 표기일 뿐."""
        try:
            loop = asyncio.get_running_loop()
            self._code_version = await loop.run_in_executor(
                None, version.code_version)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._log_error("code_version")   # #28: 버전 '미상' 폴백의 진단 단서

    async def serve(self):
        self.loop = asyncio.get_running_loop()
        signals = self._install_signal_handlers()
        try:
            # 연결 인증 토큰(F1)을 listen **전에** 게시한다. 클라이언트는 0600 토큰
            # 파일을 읽어 hello/control 에 실어 보내고, handle_client 가 검증한다. listen
            # 후에 쓰면 재시작 직후 클라가 빈/구토큰을 읽을 창이 생기므로 먼저 쓴다.
            self.auth_token = secrets.token_hex(32)
            try:
                ipc.write_token(self.sock_path, self.auth_token)
            except OSError:
                self._log_error("write_token")
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
            # §1.7 Stage 3: re-exec 복원이 보관한 원격 링크 spec 재연결(비동기).
            if resumed:
                self.remote_restore_links()
            flush = asyncio.create_task(self._flush_loop())
            autoname = asyncio.create_task(self._autorename_loop())
            usage = asyncio.create_task(self._usage_loop())
            # 코드 버전(p4 CL) 캡처를 listen **이후** 백그라운드로 미룬다 — __init__
            # 에서 동기로 부르면 `p4 changes` 왕복(~수백 ms)이 클라 접속 임계경로에
            # 올라 콜드 기동이 느려졌다(server.__init__ 주석 참조). executor 로 돌려
            # 이벤트 루프를 막지 않고, 끝나면 self._code_version 을 갱신한다.
            asyncio.create_task(self._capture_version())
            async with server:
                try:
                    await server.serve_forever()
                except asyncio.CancelledError:
                    pass
            flush.cancel()
            autoname.cancel()
            usage.cancel()
        finally:
            self._remove_signal_handlers(signals)
