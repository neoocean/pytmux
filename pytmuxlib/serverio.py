"""클라이언트 IPC·렌더 flush 루프·명령 디스패치 서버 로직 믹스인. `server.Server`
가 상속한다(§10 LLM 친화 리팩토링). 레이아웃/상태/트리 메시지 빌드(_layout_msg·
_status_msg·_tree_msg), 전체 동기화(_send_full)·브로드캐스트, flush 루프, 명령 처리
(_handle_cmd)·클라이언트 연결(handle_client)·입력/스크롤·서버 serve/shutdown 을 모은다.
동작 불변 — self.* 상태와 Server 의 다른 메서드를 그대로 참조한다."""
from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hmac
import inspect
import os
import secrets
import signal
import time
import traceback

from . import ipc, pty_backend, ptyhostmgr, version
from .model import ClientConn, Pane, Session
from .protocol import (FLUSH_HZ, HANDSHAKE_MAX_FRAME, HANDSHAKE_TIMEOUT, MAX_H,
                       MAX_W, MIN_H, MIN_W, PROTO_VERSION, SYNC_OUTPUT_MAX_DEFER,
                       clamp_dim, frame_msg, read_msg, write_frames, write_msg)
from .servercmd import DYNAMIC, FULL, HANDLED, _CMD_TABLE
from .serverremote import _REMOTE_BLOCK_ACTIONS, _REMOTE_RELAY_ACTIONS

# host 재연결 폭주 가드(옵션 C). host 가 '새 연결이 옛 연결을 대체'하므로 두 서버가 같은
# host 를 두고 다투면 백오프 없는 즉시 재연결이 무한 ping-pong → PTY 출력이 안 흐른다(빈
# 패널로 멈춤). 직전 연결이 STABLE 미만으로 끊기면 급속 churn 으로 보고 지수 백오프하며,
# 연속 급속 끊김이 MAX_BURST 를 넘으면 = 경쟁으로 판단한다. 자세한 처리는 _reconnect_host.
HOST_RECONNECT_STABLE_SEC = 5.0      # 이 시간 이상 붙어 있었으면 정상 → burst 리셋
HOST_RECONNECT_BACKOFF_BASE = 0.25   # 첫 급속 끊김 백오프(초); 이후 2배씩
HOST_RECONNECT_BACKOFF_CAP = 4.0     # 백오프 상한(초)
HOST_RECONNECT_MAX_BURST = 6         # 연속 급속 끊김 한계 → 경쟁 판단

# H-2 느린 소비자 가드: 한 클라의 송신버퍼가 이만큼 쌓이면(드레인을 못 따라옴)
# 그 클라를 떨궈 flush 루프가 전체를 끌고 멈추지 않게 한다. write 자체도 타임아웃으로
# 감싸 영구 hang 을 끊는다(백스톱).
_CLIENT_WRITE_HIGH_WATER = 8 * 1024 * 1024   # 8MiB 송신버퍼 백로그
_CLIENT_WRITE_TIMEOUT = 5.0                   # 한 배치 write+drain 상한(초)
# 보안검수 2026-07-04 S1(레드팀 M2-c): 인증 전(핸드셰이크) 동시 연결수 상한. Windows
# 제어채널은 tcp:127.0.0.1 루프백이라 비인가 로컬 사용자가 토큰 없이 connect() 가능 —
# 각 연결이 최대 HANDSHAKE_TIMEOUT(10초) 동안 상주하므로 수천 개를 동시에 열어 단일스레드
# 데몬의 fd/메모리/이벤트루프를 고갈시키는 slowloris 를 이 캡으로 막는다. 인증된(attach된)
# 클라 수는 캡하지 않는다 — 핸드셰이크 읽기 구간만 카운트한다.
_MAX_PREAUTH_CONNS = 128
# 死-클라 회수(_liveness_loop): 클라는 net_ping_interval(기본 0.5초)마다 ping 을
# 보낸다. ping 을 한 번이라도 보낸(=ping 켜진) 클라가 이 시간 넘게 완전 무응답이면
# 반-열린 TCP/콘솔 닫힘/웨지로 死한 고아로 보고 회수한다. 핀(=세션 공유 크기는
# min)을 풀어 남은 클라가 제 크기로 다시 자라게 한다(빈 띠 해소). 기본 ping 주기의
# 60배라 정상 클라는 절대 걸리지 않고, 느리게 설정한 ping(수 초)도 넉넉히 견딘다.
CLIENT_IDLE_TIMEOUT = 30.0          # 이 시간(초) 넘게 무응답이면 死로 보고 회수
CLIENT_LIVENESS_SWEEP_SEC = 5.0     # 死-클라 회수 점검 주기(초)


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

    def _advertised_mouse_track(self, p: Pane) -> int:
        """클라에 광고할 패널 마우스 트래킹 레벨. Windows 에서는 any-motion(3)을
        drag(2)로 캡한다 — ConPTY 가 주입된 any-motion SGR 리포트를 소비 못 하고
        프롬프트에 텍스트로 흘리기 때문(HANDOFF §10-H, win_mouse_motion 으로 복구).
        클릭/드래그(1000/1002)는 누출 증거 없어 그대로 둔다."""
        mt = p.mouse_track
        if (mt >= 3 and pty_backend.IS_WINDOWS
                and not getattr(self, "win_mouse_motion", False)):
            return 2
        return mt

    def _layout_msg(self, sess: Session, cols: int = None, rows: int = None):
        win = sess.active_window
        if not win:
            return None
        if cols is None or rows is None:
            cols, rows = self._session_size(sess)
            # window_size=latest 재-미러 판정용: 지금 세션에 실제로 적용한 공유 크기를
            # 기록한다(_remirror_if_size_changed 가 이 값과 비교해 불필요한 방송을 건너뜀).
            sess._applied_size = (cols, rows)
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
            mt = self._advertised_mouse_track(p)
            p._mouse_sent = (mt, p.mouse_sgr)
            pane_msgs.append({"id": p.id, "x": cx, "y": cy, "w": cw, "h": ch,
                              "title": p.title, "box": box,
                              "active": p is win.active_pane,
                              "mouse": mt, "mouse_sgr": p.mouse_sgr})
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

    def _switcher_panes(self, tab):
        """탭 스위처 하위행용 **경량** 패널 요약. 패널이 2개 이상인 탭만 반환하고(스위처는
        ≥2 패널 탭에만 하위행을 그린다) 그 외엔 None — status 비대화를 최소화한다.

        용도: **원격 탭** 스위처. 원격 탭의 패널은 상류에 있어 다운스트림 로컬 tree
        (_tree_msg, 로컬 세션 전용)에 안 잡히므로, 상류가 status 창마다 이 요약을 실어
        보내면 다운스트림이 _remote_tabs 로 병합해 스위처 하위행을 그린다. 로컬 탭은
        클라가 여전히 lazy tree 로 채우므로 이 필드를 무시한다(id·앱·제목·로컬여부만 —
        격자/토큰 등 무거운 개요 필드는 뺀다)."""
        panes = tab.window.panes()
        if len(panes) < 2:
            return None
        out = []
        for p in panes:
            cmd = self._fg_command(p) or ""
            out.append({"id": p.id, "cmd": cmd,
                        "title": (p.title or "").strip(),
                        "remote": cmd.lower() in self._REMOTE_CMDS})
        return out

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
                          "pinned": getattr(t, "pinned", False),
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
        atab = sess.tabs[sess.active_index] \
            if sess.tabs and 0 <= sess.active_index < len(sess.tabs) else None
        msg = {
            "t": "status",
            "session": sess.name,
            # boot_id: 이 서버 **인스턴스**의 부팅 식별자(F-1). 아래 wid 는 재시작 때
            # 1..N 으로 재발급되므로, 다운스트림이 sticky 키를 (boot_id, wid) 로
            # 네임스페이싱해야 상류 재시작 뒤 옛 키가 새 탭에 오매칭되지 않는다.
            # 추가 필드라 PROTO_VERSION 범프 불요 — 구버전 상류는 안 보내고, 그때
            # 다운스트림은 종전대로(=네임스페이싱 없이) 동작한다.
            "boot_id": getattr(self, "boot_id", None),
            "windows": [{"index": t.index, "name": t.name,
                         # wid: 안정 window id(위치값 index 와 별개). 다운스트림이 원격
                         # 단일-탭 분리를 상류 탭 close/reorder 로도 안 어긋나게 이걸로
                         # 키잉한다(M-1). 구버전 상류는 미전송 → 다운스트림이 index 폴백.
                         "wid": getattr(t, "wid", None),
                         "active": (i == sess.active_index),
                         "bell": t.has_bell, "activity": t.has_activity,
                         "claude_done": t.has_claude_done,
                         "pinned": getattr(t, "pinned", False)}
                        for i, t in enumerate(sess.tabs)],
            "active_pane": win.active_pane.id if win else None,
            "zoomed": bool(win.zoomed) if win else False,
            "sync": bool(win.sync) if win else False,
            "pane_title": win.active_pane.title if win and win.active_pane else "",
            "single_border": self.single_border,
            "win_mouse_motion": self.win_mouse_motion,
            # 통합 설정 화면(:settings)이 서버 옵션 현재값을 표시할 수 있게 권위값을
            # 함께 보낸다(전엔 클라가 못 읽어 '미상' 표시). 전역 옵션 + 활성 윈도우/탭의
            # per-window 옵션(auto-rename·border-status·monitor). 클라가 self.server_opts
            # 에 담아 SettingsScreen 행 현재값으로 쓴다. opts.json/per-window 권위 유지.
            "coalesce_repaints": self.coalesce_repaints,
            "nest_auto_attach": self.nest_auto_attach,
            "vt_parser": self.vt_parser,
            "window_size": self.window_size,
            "auto_rename": bool(win.auto_rename) if win else True,
            "border_status": bool(win.border_status) if win else False,
            "monitor_activity": bool(atab.monitor_activity) if atab else False,
            "monitor_bell": bool(atab.monitor_bell) if atab else True,
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
        # 탭 스위처 하위행용 경량 패널 요약(≥2 패널 탭만) — 원격 탭 스위처가 상류
        # status 로 하위 패널을 그릴 수 있게 한다(_switcher_panes docstring). 로컬
        # 클라는 이 필드를 무시(lazy tree 사용)하므로 다운스트림 원격 병합에만 쓰인다.
        for wd, t in zip(msg["windows"], sess.tabs):
            ps = self._switcher_panes(t)
            if ps is not None:
                wd["panes"] = ps
        # §1.7: 원격 링크의 탭을 병합(⇄host:이름, 전역 index 는 로컬 뒤 연속) — 탭바에
        # 양쪽이 항상 보이고, select_window(전역 index)로 원격 탭에 진입한다.
        msg["windows"] += self._remote_tabs(len(sess.tabs), client)
        return msg

    async def _send_full(self, client: ClientConn):
        sess = client.session
        if not sess:
            return
        # H-1: 다중-프레임 송신이라 write_lock 으로 flush 루프·다른 _send_full 과
        # 직렬화한다(await drain 사이로 프레임이 끼어드는 순서 역전 방지).
        async with client.write_lock:
            # §1.7: 원격 탭을 보는 클라에겐 로컬 layout/screen 을 보내지 않는다(화면은
            # 업스트림 전달분이 권위) — 병합 탭바용 status 만 갱신한다. 모든 브로드캐스트
            # 경로(_broadcast_session·flush 헤더예약·resize 미러링)가 이 가드를 공유.
            if getattr(client, "remote_view", None):
                await write_msg(client.writer,
                                self._status_msg(sess, client=client))
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
            # A3: 활성 패널을 가장 먼저 render·전송해 사용자가 보는 화면의 first-paint
            # 를 앞당긴다(분할이 많아도 포커스 패널이 비활성 패널 직렬화 뒤로 안 밀림).
            # 총량 동일, 순서만 활성 우선.
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
            await write_msg(client.writer,
                            self._status_msg(sess, client=client))

    async def _remirror_if_size_changed(self, sess):
        """window_size=latest 전용 재-미러링: 사용자 조작(입력·스크롤)으로 '마지막
        조작 클라'가 바뀌면 세션 공유 크기가 달라질 수 있다. 새 _session_size 가 직전
        적용값(_applied_size)과 다를 때만 같은 세션 전 클라에 _send_full 을 다시 보내
        새 크기로 레이아웃한다(같으면 no-op — 매 키 입력마다 방송하지 않음). smallest/
        largest 는 입력으로 크기가 안 변하므로 사실상 항상 no-op(안전)."""
        if not sess:
            return
        new = self._session_size(sess)
        if getattr(sess, "_applied_size", None) == new:
            return
        sess._applied_size = new
        for c in [x for x in self.clients if x.session is sess]:
            try:
                await self._send_full(c)
            except Exception:
                self._log_error("send_full(remirror)")

    async def _send_to(self, c: ClientConn, obj) -> bool:
        """단일 클라에 메시지 1건을 c.write_lock 하에 송신한다(H-1 확장). flush 루프의
        drain 과 같은 writer 에서 동시 drain 이 겹치면 CPython StreamWriter 가
        AssertionError(-O 에선 영구 hang)를 내므로, **모든** per-client write 가 이 lock 을
        공유해 직렬화한다. _send_full 처럼 이미 lock 을 잡은 경로에선 쓰지 않는다(비재진입)."""
        async with c.write_lock:
            return await write_msg(c.writer, obj)

    async def _send_frames_to(self, c: ClientConn, frames) -> bool:
        """_send_to 의 pre-framed 배치 버전(원격 전달 프레임용). 동일하게 write_lock 직렬화."""
        async with c.write_lock:
            return await write_frames(c.writer, frames)

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
            asyncio.create_task(self._send_to(c, {"t": "bye"}))
        self.running = False
        if self.loop:
            self.loop.call_later(0.2, self.shutdown)

    def _on_host_lost(self):
        """host 연결이 예기치 않게 끊겼다(host 크래시 등). 재연결을 시도한다(P6).
        로컬 host 라 '끊김 ≈ host 사망'이므로, 재연결은 보통 **새 host**가 떠 이후 새
        패널 spawn 이 동작하게 한다(옛 패널은 옛 host 와 함께 죽어 복구 불가 — 아웃오브
        프로세스 host 의 본질적 트레이드오프). 정상 재시작 종료(_host_restart_exit)는
        self.running=False 라 여기서 재연결하지 않는다."""
        if not self.running or self.loop is None:
            return
        self._pty_host = None
        self.loop.create_task(self._reconnect_host())

    async def _reconnect_host(self):
        # 폭주 가드: 직전 연결이 곧바로(STABLE 미만) 끊겼으면 급속 churn 으로 보고
        # 지수 백오프한다. 연속 급속 끊김이 한계를 넘으면 = 다른 서버가 같은 host 를
        # 소유하며 경쟁 중(host 가 새 연결로 옛 연결을 대체) → 이 서버가 클라 없는
        # stale 중복이면 스스로 내려가(승자=클라를 가진 최신 서버), 클라가 붙어 있으면
        # 포기하지 않되 CAP 속도로 느리게 재시도해(이벤트 루프 기아·무한 ping-pong 방지)
        # 상대가 양보(stale 종료)하면 다음 시도에서 안정적으로 붙는다.
        now = self.loop.time()
        if now - self._host_last_connect_ts >= HOST_RECONNECT_STABLE_SEC:
            self._host_reconnect_burst = 0       # 직전 연결이 충분히 안정적이었다
        else:
            self._host_reconnect_burst += 1
        if self._host_reconnect_burst > HOST_RECONNECT_MAX_BURST:
            if not self.clients:
                # 클라 없는 stale 중복 서버 → 경쟁을 끝내려 스스로 종료. 이 시점
                # _pty_host 는 None(_on_host_lost 가 비웠고 재연결 안 함)이라 shutdown()
                # 이 공유 host 를 죽이지 않는다(승자가 계속 쓴다).
                self._log_error("ptyhost_reconnect_storm_stepdown")
                if self.loop:
                    self.loop.call_soon(self.shutdown)
                return
            # 클라가 붙은 서버: 포기하지 않고 CAP 속도로 느리게 재시도한다.
            self._log_error("ptyhost_reconnect_storm")
            self._host_reconnect_burst = HOST_RECONNECT_MAX_BURST   # delay 를 CAP 고정
        if self._host_reconnect_burst:
            delay = min(HOST_RECONNECT_BACKOFF_CAP,
                        HOST_RECONNECT_BACKOFF_BASE
                        * (2 ** (self._host_reconnect_burst - 1)))
            await asyncio.sleep(delay)
            if not self.running:
                return
        client = await ptyhostmgr.ensure_connected(self.loop, self.sock_path)
        if client is not None and self.running:
            client._on_lost = self._on_host_lost
            self._pty_host = client
            self._host_last_connect_ts = self.loop.time()
        elif client is not None:
            with contextlib.suppress(Exception):
                await client.close()

    # ---- flush 루프 ----
    def _on_flush_done(self, task: "asyncio.Task"):
        """flush(렌더) 루프 태스크가 예외로 죽으면 로깅 후 재기동한다 — 무감시 태스크가
        조용히 멈춰 전 클라 화면이 영구히 얼어붙는 최악을 막는 안전망(§5 [H]). 정상
        경로에선 write_lock 직렬화 + protocol 백스톱으로 예외 자체가 안 나며, 취소/정상
        종료(서버 셧다운)는 재기동하지 않는다."""
        if task.cancelled() or not self.running or self.loop is None:
            return
        exc = task.exception()
        if exc is None:
            return
        self._log_error("flush_loop", repr(exc))
        new = asyncio.create_task(self._flush_loop())
        new.add_done_callback(self._on_flush_done)

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
                now = time.monotonic()
                for p in win.panes():
                    if not p.dirty:
                        continue
                    # 동기화 출력(DEC 2026): 프레임(?2026h…?2026l) 도중이면 송신을
                    # 미뤄(dirty 유지) 반쪽 화면을 클라에 안 보낸다 — 무작위 글자
                    # 겹침의 근본 해결. 단 ESU 안 오는 먹통 앱이 패널을 영구히 묶지
                    # 않게 SYNC_OUTPUT_MAX_DEFER 지나면 강제로 보낸다.
                    if (p.sync_output
                            and now - p._sync_since < SYNC_OUTPUT_MAX_DEFER):
                        continue
                    rows, cursor = p.render(p is win.active_pane)
                    # 전송 전 플러그인 행 필터(claude-code 가 '/feedback 팁' 줄을 가림).
                    # 변형 시 새 리스트를 받으므로 render 캐시는 안 건드린다(plugins 계약).
                    rows = self.plugins.server_filter_rows(self, p, rows)
                    p.dirty = False
                    for c in clients:
                        if c.remote_view:   # §1.7 원격 보기 중 — 로컬 화면 미전송
                            continue
                        frames_by_client[c].append(
                            self._screen_frame(c, p.id, rows, cursor,
                                               p._last_wrap))
                # 라이브 PTY 팝업 패널(트리 밖)도 dirty 면 스트리밍한다(동기화 출력
                # 프레임 도중이면 일반 패널과 동일하게 송신을 미룬다).
                pu = sess.popup
                if (pu and pu.get("pane") is not None and pu["pane"].dirty
                        and not (pu["pane"].sync_output
                                 and now - pu["pane"]._sync_since
                                 < SYNC_OUTPUT_MAX_DEFER)):
                    pp = pu["pane"]
                    rows, cursor = pp.render(True)
                    rows = self.plugins.server_filter_rows(self, pp, rows)
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
                # H-2: 느린/먹통 클라가 flush 루프 전체를 막지 않게 가드한다.
                for c in clients:
                    await self._flush_to_client(c, frames_by_client[c])

    async def _flush_to_client(self, c: ClientConn, frames):
        """한 클라에 프레임 배치를 보낸다(H-2). 느린/먹통 클라(드레인 무한 대기)가
        flush 루프 전체(=모든 클라·세션 렌더)를 막지 않게: ① 송신버퍼가 이미
        high-water 를 넘으면 그 클라를 즉시 떨궈(전체 차단 회피) 재연결 시 full 재동기로
        복구시키고, ② 실제 write 는 write_lock(H-1, _send_full 과 직렬화) + 타임아웃으로
        감싸 영구 hang 을 끊는다. 클라를 통째 떨구므로 _sent_rows 베이스라인 불일치 없음."""
        if not frames:
            return
        tr = getattr(c.writer, "transport", None)
        if tr is not None:
            try:
                if tr.get_write_buffer_size() > _CLIENT_WRITE_HIGH_WATER:
                    self._drop_slow_client(c)
                    return
            except Exception:
                pass
        # lock 획득 자체도 타임아웃으로 감싼다: _send_full 등이 먹통 클라의 drain 에서
        # write_lock 을 오래 쥐면(무제한 drain) 여기 acquire 가 무한 대기해 flush 루프
        # 전체가 프리즈한다. 타임아웃 시 클라를 떨구면 writer.close 로 쥔 쪽 drain 이
        # ConnectionError 로 풀려 lock 이 해제된다.
        try:
            await asyncio.wait_for(c.write_lock.acquire(), _CLIENT_WRITE_TIMEOUT)
        except asyncio.TimeoutError:
            self._drop_slow_client(c)
            return
        try:
            await asyncio.wait_for(
                write_frames(c.writer, frames), _CLIENT_WRITE_TIMEOUT)
        except (asyncio.TimeoutError, OSError, ConnectionError):
            self._drop_slow_client(c)
        finally:
            c.write_lock.release()

    def _drop_slow_client(self, c: ClientConn):
        """느린 소비자를 브로드캐스트 대상에서 즉시 제거하고 연결을 닫는다(H-2).
        handle_client 의 finally 가 곧 reader 정리를 마저 한다(close 는 멱등)."""
        if c in self.clients:
            self.clients.remove(c)
            self._log_error("slow client dropped (write backpressure)")
        with contextlib.suppress(OSError, ConnectionError):
            c.writer.close()

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

    async def _liveness_loop(self):
        """死-클라 회수: 반응 없는 고아 클라가 세션 공유 크기(_session_size=min)를
        영구히 작게 핀해, 정상 클라 화면 하단/우측에 빈 띠가 남아 "공간이 생겨
        안 사라진다"는 증상을 막는다(클라측 _composite 레터박스와 짝 — 핀이 풀리면
        그 띠도 사라진다). 클라는 net_ping_interval(기본 0.5초)마다 ping 을 보내므로,
        ping 을 한 번이라도 보낸(ever_pinged) 클라가 CLIENT_IDLE_TIMEOUT 넘게 완전
        무응답이면 반-열린 TCP/콘솔 닫힘/웨지로 死한 것으로 보고 떨군다. ping 을 끈
        (net_ping_interval=0) 클라는 ever_pinged 가 False 라 회수 대상이 아니다
        (오탐 방지) — 그 경우는 write 백프레셔 드롭(_flush_to_client)에 맡긴다.

        **앱 계층 한계(고칠 수 없는 잔여 경로)**: federation ingress(다운스트림 원격
        링크)가 **구버전/ping-off** 라 ever_pinged 가 안 서면, 그 링크가 반-열림으로 死해도
        이 회수기가 못 잡아 min 을 영구 핀한다. unix-소켓 ingress 는 중간 stdio-proxy 가
        다운스트림의 죽음을 가려(proxy 는 sshd 파이프가 EOF 나야 죽는다) 서버 소켓엔
        dead/alive 신호가 없고, hello 에 federation 표식도 없어(로컬 클라와 동일 프레임)
        ingress 만 특별 회수할 수도 없다 — 게다가 표식을 보낼 신버전은 이미 _remote_ping_loop
        로 ping 하므로(ever_pinged=True) 여기서 이미 잡힌다. 따라서 이 잔여는 **트랜스포트
        계층**에서만 닫힌다: remote-attach 를 받는 **호스트 sshd 의 ClientAliveInterval**
        이 반-열림 다운스트림 ssh 를 끊으면 → proxy EOF → 정상 teardown(ever_pinged 무관)
        으로 회수 + 고아 proxy/sshd 정리. 설정법은 docs/internal/REMOTE_ATTACH_TROUBLESHOOTING
        §8. (신버전 다운스트림은 p4 62568 keepalive 로 이 앱 경로가 이미 커버.)"""
        while self.running:
            await asyncio.sleep(CLIENT_LIVENESS_SWEEP_SEC)
            try:
                await self._evict_idle_clients()
            except Exception:
                self._log_error("liveness_sweep")

    async def _evict_idle_clients(self):
        """死-클라 회수 1회(_liveness_loop 본체 — 테스트가 직접 호출). 반환=떨군 수."""
        now = time.monotonic()
        dead = [c for c in self.clients
                if c.ever_pinged
                and now - c.last_seen > CLIENT_IDLE_TIMEOUT]
        if not dead:
            return 0
        sessions_hit = set()
        for c in dead:
            if c in self.clients:
                self.clients.remove(c)
                sessions_hit.add(c.session)
                self._log_error(
                    "idle client evicted (no msg in "
                    f"{now - c.last_seen:.0f}s)")
            with contextlib.suppress(OSError, ConnectionError):
                if c.writer is not None:
                    c.writer.close()
        # 미러링: 핀이 풀려 남은 클라는 공유 크기가 커질 수 있으니 갱신
        # (handle_client teardown 과 동일 경로). 세션이 아직 살아 있을 때만.
        for sess in sessions_hit:
            if sess and sess in self.sessions.values():
                for c in [x for x in self.clients if x.session is sess]:
                    try:
                        await self._send_full(c)
                    except Exception:
                        self._log_error("send_full(liveness)")
        return len(dead)

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
            # 결과를 요청 클라에 알린다(notice) — 실패가 서버 로그에만 남아
            # "아무 일도 안 일어남"으로 보이던 갭(제보 2026-06-12) 해소.
            # key+ko 폴백으로 보내 클라가 자기 로케일로 번역(_notice_msg, i18n rnotice.*).
            if ok:
                # hello 는 받았지만 업스트림이 첫 status 를 안 보내는 웨지(원격 pty-host
                # 고장 등, 제보 2026-06-20)면 탭이 안 생기는데도 종전엔 즉시
                # "병합됨"으로 단정해 '성공인데 탭 없음'으로 보였다. 성공을 단정하기
                # 전에 첫 status(실제 탭 도착)를 잠깐 기다려 '병합됨'과 '연결됐지만
                # 무응답'을 가른다(링크는 유지 — 뒤늦은 status 면 그때 탭 출현).
                link = self._remotes_dict().get(target)
                got = (link is not None
                       and await self._remote_wait_first_status(link))
                if got:
                    self._remote_status_broadcast()
                    note = self._notice_msg("rnotice.attach_merged",
                        "remote-attach {target}: 원격 탭 병합됨", target=target)
                else:
                    note = self._notice_msg("rnotice.attach_silent",
                        "remote-attach {target}: 연결됐지만 원격이 응답 없음 — "
                        "원격 서버 점검", sticky=True, target=target)
            else:
                # 핸드셰이크 실패는 놓치면 안 되는 알림 — 3초 유지 + 클릭/Enter 로
                # 수동 닫기(제보 2026-06-16: 너무 빨리 사라짐).
                detail = self._err_detail("rerr.see_log", "서버 error.log 참조")
                note = self._notice_msg("rnotice.attach_fail",
                    "remote-attach {target} 실패 — {why}",
                    sticky=True, detail=detail, target=target)
            await self._send_to(client, note)
            return
        if action == "remote_detach":
            # index 가 실렸으면(탭 닫기[x]/esc x) 그 병합 전역 탭 **하나만** 분리한다.
            # 없으면(:remote-detach [host] 명령) 종전대로 호스트 전체를 끊는다.
            idx = msg.get("index")
            if idx is not None:
                self.remote_detach_tab(sess, idx)
            else:
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
                detail = self._err_detail("rerr.see_log", "서버 error.log 참조")
                await self._send_to(client, self._notice_msg(
                    "rnotice.newtab_fail",
                    "remote-new-tab {target} 실패 — {why}",
                    sticky=True, detail=detail, target=target))
            return
        if action == "select_window":
            idx = int(msg.get("index", 0))
            if idx >= len(sess.tabs):
                if await self.remote_select_window(client, sess, idx):
                    return     # 화면은 업스트림 _send_full 전달분이 그린다
            elif client.remote_view:
                client.remote_view = None   # 로컬 탭 복귀(아래 평소 경로)
        elif client.remote_view and (action in _REMOTE_RELAY_ACTIONS
                                     or action in self.plugins.relay_actions()):
            # 코어 릴레이 화이트리스트 ∪ 플러그인 기여(claude-code: set_autoresume·
            # set_prompt_clear·request_token_log). 플러그인 부재 시 후자는 빈 집합.
            if self.remote_relay(client, msg):
                return
        elif client.remote_view and action in _REMOTE_BLOCK_ACTIONS:
            # §1.7-c 예외: **같은 호스트의 두 원격 탭** 합치기(join_pane)는 전역 src
            # index 를 원격 로컬 index 로 변환해 업스트림에 릴레이한다(원격 탭끼리
            # 드래그 머지). 로컬/타 호스트 src 면 False → 아래 거부로 폴백.
            if action == "join_pane" and self.remote_relay_join(client, sess, msg):
                return
            # §1.7-c 섞임 금지: 원격 보기 중 경계 횡단/로컬 트리 조작은 거부.
            # (조용한 로컬 실행도, index 공간이 안 맞는 릴레이도 모두 위험.)
            await self._send_to(client, self._notice_msg(
                "rnotice.mix_block_cmd",
                "원격 탭에서는 사용할 수 없는 명령입니다 — "
                "원격↔로컬 패널/탭은 섞을 수 없습니다(§1.7)"))
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
                await self._send_to(client, self._notice_msg(
                    "rnotice.mix_block_move",
                    "원격 탭으로/원격 탭을 이동할 수 없습니다 — "
                    "원격↔로컬 패널/탭은 섞을 수 없습니다(§1.7)"))
                return
        # ── 명령 디스패치(servercmd._CMD_TABLE) ──
        # 종전 67 분기 if/elif 체인(§10-4⑨ God-함수)을 action→핸들러 테이블로 대체.
        # disposition(FULL/HANDLED/DYNAMIC)은 제어흐름이 아니라 테이블이 **데이터로
        # 선언**한다 — 계약·근거는 servercmd 모듈 docstring 참조.
        entry = _CMD_TABLE.get(action)
        if entry is None:
            await self._dispatch_plugin_cmd(client, sess, action, msg)
            return
        handler, disp = entry
        decided = await handler(self, client, sess, msg)
        if disp == DYNAMIC:
            # 핸들러가 실행 시점에 결정(kill_pane). 유효하지 않은 반환은 "응답을 아예
            # 안 보냄"이라는 **새 침묵 경로**가 되므로(이 분할이 없애려던 바로 그 실패)
            # 조용히 넘기지 않고 던진다 — handle_client 가 잡아 error.log 에 남기고
            # 세션은 유지된다.
            if decided not in (FULL, HANDLED):
                raise RuntimeError(
                    f"DYNAMIC 핸들러 {action} 이 FULL/HANDLED 대신 {decided!r} 반환")
            disp = decided
        if disp == FULL:
            await self._send_full(client)

    async def _dispatch_plugin_cmd(self, client, sess, action: str, msg: dict):
        """테이블에 없는 action → 플러그인 훅(체인 시절 `else:` 블록과 등가)."""
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
        # 훅이 awaitable(코루틴/Future)을 반환하면 여기서 await 한다 — 순수 파일시스템
        # I/O(mdir 복사/이동/삭제·대형 압축 목록·특수파일 뷰)를 executor 로 넘겨 단일
        # asyncio 루프가 다중 GB 트리 조작에 멎지 않게 하는 하위호환 확장. dict 를
        # 곧바로 반환하는 훅(ncd 등)은 그대로 동작한다.
        if inspect.isawaitable(resp):
            resp = await resp
        if resp is not None:
            # 원격 페더레이션 §4.1: 릴레이된 요청이 요청 클라 식별자(_req_token)를
            # 실어 왔으면 회신에 그대로 echo 한다 — 다운스트림 _remote_reader 가
            # 요청한 그 클라에만 응답을 전달해 다른 뷰어에 새지 않게 한다. 로컬
            # (비릴레이) 요청엔 _req_token 이 없어 무영향.
            if isinstance(resp, dict) and msg.get("_req_token") is not None:
                resp["_req_token"] = msg["_req_token"]
            await self._send_to(client, resp)

    def _log_error(self, where: str, detail: str = ""):
        """방금 처리 중인 예외의 트레이스백을 `<sock>.error.log` 에 append 한다.

        detail 이 주어지면(예외 아닌 진단 로그 — claude_format_unrecognized 가 미인식
        화면의 footer tail 을 남길 때) 트레이스백 앞에 그 본문을 함께 적는다. 예외
        컨텍스트가 없으면 트레이스백은 "NoneType: None" 으로 무해하게 남는다.

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
                if detail:
                    f.write(detail + "\n")
                f.write(traceback.format_exc())
        except Exception:
            pass

    async def handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter):
        # 인증 전(핸드셰이크) 읽기는 작은 상한(HANDSHAKE_MAX_FRAME) + 타임아웃으로
        # 감싼다: Windows 루프백 TCP 는 비인가 로컬 사용자가 connect() 가능하므로,
        # 큰 길이만 광고하거나(slowloris) 무한 대기시켜 단일스레드 데몬을 고갈시키는
        # 걸 막는다(보안검수 2026-07-03 M2). 인증 후 본 루프는 MAX_FRAME 을 쓴다.
        # S1(2026-07-04): 동시 핸드셰이크 수를 _MAX_PREAUTH_CONNS 로 캡한다(연결 개수
        # 고갈 방어) — 카운트는 핸드셰이크 읽기 구간만 감싸고(try/finally) 인증된 클라
        # 수명은 세지 않는다.
        if self._preauth_conns >= _MAX_PREAUTH_CONNS:
            writer.close()
            return
        self._preauth_conns += 1
        try:
            first = await asyncio.wait_for(
                read_msg(reader, max_frame=HANDSHAKE_MAX_FRAME),
                HANDSHAKE_TIMEOUT)
        except (asyncio.TimeoutError, OSError, ConnectionError):
            writer.close()
            return
        finally:
            self._preauth_conns -= 1
        # 첫 프레임은 dict 여야 한다. 비-dict JSON(리스트/정수/문자열 등 악의·비정상
        # 클라)이면 이어지는 `first.get(...)` 가 AttributeError 로 핸들러 밖으로 새
        # (트레이스백 노이즈·비정상 드롭). None 과 동일하게 조용히 끊는다(SECURITY_REVIEW
        # F6 류 입력 가드 — 서버는 살아 있음). 회귀: test_security_runtime.py.
        if not isinstance(first, dict):
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
        # (TCP·미지원 OS — 토큰 F1 이 1차 방어). docs/internal/SECURITY_REVIEW.md F2.
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
        client.last_seen = time.monotonic()   # 死-클라 회수 기준 시각 초기화
        client.cols = clamp_dim(first.get("cols", 80), MIN_W, MAX_W, 80)
        client.rows = clamp_dim(first.get("rows", 24), MIN_H, MAX_H, 24)
        # 모호폭 모드(cellwidth): 클라가 단말 자동감지로 wide 를 통지하면 서버 pyte
        # 격자 폭 계산도 모호폭=2 로 맞춘다(앱 레이아웃이 실 단말과 동일하게 격자에
        # 앉도록). 전역이라 마지막 통지가 이긴다(다중 클라·다중 단말은 한계 — 보통
        # 로컬 단일 클라). 통지 부재(narrow 단말)면 호출 자체가 안 와 현행 유지.
        from . import cellwidth
        cellwidth.set_ambiguous_wide(first.get("ambig") == "wide")
        # 주의: append + 초기 _send_full 을 try 안에 둔다. 예전엔 try 밖이라
        # _send_full 이 한 번 터지면 ① 클라가 self.clients 에 남아 누수되고
        # ② 화면이 일부만 그려진 채 연결이 끊겨 클라가 즉시 종료, ③ 트레이스백도
        # 없이 이후 모든 attach 가 같은 상태로 브릭됐다(제보: "화면이 일부
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
            # 받는다(제보: ssh 재접속 시 프롬프트 박스 테두리 소실·빈 입력칸 2줄,
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
                    # 死-클라 회수용 생존 신호: 어떤 메시지든 받았으면 살아 있다.
                    client.last_seen = time.monotonic()
                    if mt == "ping":
                        # 네트워크 응답성 측정(§10): 클라가 RTT 를 재도록 즉시 echo.
                        client.ever_pinged = True   # ping 켜진 클라 — 회수 후보 자격
                        await self._send_to(client, {"t": "pong",
                                                     "ts": msg.get("ts")})
                    elif mt == "input":
                        # §1.7 원격 보기: 입력을 업스트림으로(릴레이 실패=링크 사망
                        # 직후 레이스 → 보기 해제돼 로컬 처리로 폴백).
                        if client.remote_view and self.remote_relay(client, msg):
                            continue
                        # window_size=latest: 키·붙여넣기·마우스(모두 input 프레임)는
                        # '마지막 조작'이므로 이 클라를 최신으로 표시하고, 그 결과 세션
                        # 공유 크기가 바뀌면 재-미러링한다(smallest/largest 는 no-op).
                        client.last_active = time.monotonic()
                        self._handle_input(client, msg)
                        await self._remirror_if_size_changed(client.session)
                    elif mt == "resize":
                        # §1.7: 보는 중이면 업스트림에도 리사이즈를 알려 원격이 이
                        # 크기로 다시 렌더하게 한다(로컬 갱신도 그대로 — 돌아올 때
                        # 정확한 로컬 레이아웃 유지. _send_full 의 보기 가드가 보는
                        # 클라에겐 status 만 보낸다).
                        if client.remote_view:
                            self.remote_relay(client, msg)
                        client.last_active = time.monotonic()  # 리사이즈=조작(latest)
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
                        client.last_active = time.monotonic()  # 스크롤=조작(latest)
                        self._handle_scroll(client, msg)
                        await self._remirror_if_size_changed(client.session)
                    elif mt == "set_ambig":
                        # 클라가 런타임 모호폭 모드를 바꿨다(:set ambiguous-width).
                        # 서버 pyte 격자 폭도 맞추고(hello 의 ambig 와 동형) 앱들을
                        # SIGWINCH repaint 시켜 새 폭으로 다시 그리게 한 뒤 전체 프레임을
                        # 다시 보낸다(같은 세션 클라 모두). 전역이라 마지막 통지가 이긴다.
                        from . import cellwidth
                        cellwidth.set_ambiguous_wide(bool(msg.get("wide")))
                        self._induce_redraw_all()
                        for c in [x for x in self.clients
                                  if x.session is client.session]:
                            try:
                                await self._send_full(c)
                            except Exception:
                                self._log_error("send_full(set_ambig)")
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
        # host 모드(옵션 C)의 '진짜' 종료: host 프로세스도 내려 고아(OpenConsole/셸)를
        # 막는다. **재시작은 이 경로가 아니라 _host_restart_exit 를 거치므로** host 가
        # 보존된다 — 여기서만 host 를 죽인다.
        if self._pty_host is not None:
            with contextlib.suppress(Exception):
                self._pty_host.shutdown_host()
        # 엔드포인트 영속 파일 정리(unix 소켓 + TCP 포트파일/토큰). 종전엔 unix
        # 소켓만 지우고 TCP 는 "다음 기동이 덮어쓴다"고 남겨뒀는데, Windows 는 죽은
        # 루프백 포트 connect 가 즉답 거절이 아니라 타임아웃이라 stale 포트파일이
        # 다음 기동의 probe/인증 폴을 폴마다 타임아웃시켜 첫 attach 가 "서버 기동
        # 실패"로 오판됐다(완전 재시작 후 한 번 실패, 2026-07-10). owned_only:
        # 내 포트/토큰일 때만 지워 좀비의 지연 shutdown 이 새 서버의 파일을 지우지
        # 않게 한다(_cleanup_endpoint_files 주석).
        self._cleanup_endpoint_files(owned_only=True)
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
            # Windows 세션유지 재시작 host 모드(옵션 C): 장수명 pty-host 에 연결한다.
            # 이미 떠 있으면(서버 재시작) 재연결, 없으면 detached 로 띄워 연결. 실패하면
            # None → spawn_pane 이 인프로세스 백엔드로 폴백(host 버그가 기동을 막지 않게).
            # restore/prewarm(아래)이 spawn_pane 을 부르므로 그 **이전**에 연결한다.
            if ptyhostmgr.host_enabled():
                self._pty_host = await ptyhostmgr.ensure_connected(
                    self.loop, self.sock_path)
                if self._pty_host is None:
                    self._log_error("ptyhost_connect")
                else:
                    # host 연결 끊김(크래시 등) 감지 → 재연결 시도(P6).
                    self._pty_host._on_lost = self._on_host_lost
                    self._host_last_connect_ts = self.loop.time()
                    # 재시작 reattach 준비: host 가 살려 둔 패널 집합을 미리 조회한다
                    # (restore_resume_state 가 이 집합으로 생존 패널만 재바인딩).
                    try:
                        panes = await self._pty_host.list_panes()
                        self._host_resume_alive = {
                            p["pane"] for p in panes if p.get("alive")}
                    except Exception:
                        self._host_resume_alive = set()
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
            # 콜드 스타트 최적화: 첫 패널 ConPTY spawn(~190ms, Windows: OpenConsole
            # 호스트+셸 프로세스 2개 생성)을 클라이언트의 textual import 대기(~550ms)와
            # 겹친다. listen 직후·클라 접속 전에 기본 세션을 선제 생성해 첫 패널 셸을
            # 미리 띄워 두면, 클라가 import 를 마치고 attach 할 때 패널이 이미 준비돼
            # 있어 spawn 비용이 import 뒤로 숨는다(종전엔 handle_client 안
            # get_or_create_session 에서 attach 핸드셰이크 후 직렬 spawn). 크기는 기본
            # (80x24)으로 만들고, attach 시 _send_full→_layout_msg 가 클라 실제 크기로
            # 리사이즈한다(_session_size). 재시작/레이아웃 복원으로 이미 세션이 있으면
            # 건너뛴다. 프로덕션 데몬(run_server)에서만 — 테스트는 빈 상태를 기대.
            if getattr(self, "_prewarm_session", False) and not self.sessions:
                try:
                    self.ensure_default_session(80, 24)
                except Exception:
                    self._log_error("prewarm_session")
            flush = asyncio.create_task(self._flush_loop())
            flush.add_done_callback(self._on_flush_done)
            autoname = asyncio.create_task(self._autorename_loop())
            usage = asyncio.create_task(self._usage_loop())
            liveness = asyncio.create_task(self._liveness_loop())
            # 플러그인 소유 장기 작업(주기·의미는 플러그인이 안다 — 토큰 동기화 워커
            # 등). 코어는 태스크 하나를 띄우고 종료 시 취소하는 것만 한다.
            background = asyncio.create_task(self.plugins.server_background(self))
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
            liveness.cancel()
            background.cancel()
        finally:
            self._remove_signal_handlers(signals)
