"""레이아웃/재개상태/슬롯/옵션 영속(직렬화·저장·복원)과 서버 재시작(execv) 서버
로직 믹스인. `server.Server` 가 상속한다(§10 LLM 친화 리팩토링). 동작 불변 — self.*
상태와 Server 의 다른 메서드(spawn_pane·_unique_name·_all_panes 등)를 그대로 참조."""
from __future__ import annotations

import asyncio
import json
import os

from . import ipc, proc, pty_backend, version
from .model import Pane, Session, Split, Tab, Window
from .protocol import MIN_H, MIN_W, write_msg


class ServerPersistMixin:
    # ---- 레이아웃 영속(저장/복원) ----
    @property
    def layout_path(self):
        return os.path.join(os.path.dirname(ipc.state_base(self.sock_path)) or "/tmp",
                            "layout.json")

    def _serialize_node(self, node):
        if isinstance(node, Split):
            return {"type": "split", "orient": node.orient, "ratio": node.ratio,
                    "a": self._serialize_node(node.a),
                    "b": self._serialize_node(node.b)}
        return {"type": "pane", "title": node.title}

    def _build_node(self, spec, cols, rows):
        if spec.get("type") == "split":
            a = self._build_node(spec["a"], cols, rows)
            b = self._build_node(spec["b"], cols, rows)
            return Split(spec.get("orient", "lr"), a, b, spec.get("ratio", 0.5))
        p = self.spawn_pane(cols, rows)
        p.title = spec.get("title", "shell")
        return p

    def save_layout(self, path=None):
        path = path or self.layout_path
        data = {"sessions": [
            {"name": s.name, "windows": [
                {"name": t.name, "root": self._serialize_node(t.window.root)}
                for t in s.tabs]}
            for s in self.sessions.values()]}
        try:
            with ipc.open_private(path) as f:   # 0600(F5): 화면 스냅샷 등 민감정보 보호
                json.dump(data, f)
            return True
        except OSError:
            return False

    def restore_layout(self, path=None):
        path = path or self.layout_path
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        for ss in data.get("sessions", []):
            tabs = []
            for i, wspec in enumerate(ss.get("windows", [])):
                root = self._build_node(wspec["root"], 80, 24)
                w = Window(root)
                w._active = root if isinstance(root, Pane) else root.first_pane()
                w._fix_parents(root, None)
                tabs.append(Tab(i, wspec.get("name", "win"), w))
            if not tabs:
                continue
            # Session.restored 가 popup 등 휘발성 속성을 빠짐없이 채운다(§10 — 과거
            # popup 누락으로 복원 세션 attach 가 전부 깨졌다).
            sess = Session.restored(self._unique_name(ss.get("name")), tabs)
            self.sessions[sess.name] = sess
        return True

    # ---- 작업 보존 재시작(re-exec) 상태 직렬화/복원 — docs/RESTART_SCENARIO.md ----
    @property
    def resume_state_path(self) -> str:
        """재시작 보존 상태 파일. save_layout(구조만)과 달리 살아 있는 셸의 PTY
        식별자(child_pid·master_fd 번호)·패널 상태·화면 스냅샷까지 담는다."""
        return ipc.state_base(self.sock_path) + ".resume.json"

    def _all_panes(self):
        for sess in self.sessions.values():
            for tab in sess.tabs:
                for p in tab.window.panes():
                    yield p

    def _serialize_resume_node(self, node):
        if isinstance(node, Split):
            return {"type": "split", "orient": node.orient, "ratio": node.ratio,
                    "a": self._serialize_resume_node(node.a),
                    "b": self._serialize_resume_node(node.b)}
        return {"type": "pane", "pane": node.export_state()}

    def _serialize_resume_window(self, w: Window) -> dict:
        return {"root": self._serialize_resume_node(w.root),
                "active_pid": w.active_pane.child_pid if w.active_pane else None,
                "zoomed": w.zoomed, "border_status": w.border_status,
                "sync": w.sync, "auto_rename": w.auto_rename,
                "layout_idx": w.layout_idx}

    def _resume_payload(self) -> dict:
        """re-exec 복원용 상태 페이로드(트리·패널·창 메타)를 만든다. save_resume_state
        와 restart_check(드라이런)이 공유한다."""
        return {"version": 1, "sessions": [
            {"name": s.name, "active_index": s.active_index,
             "last_index": s.last_index,
             "tabs": [{"index": t.index, "name": t.name,
                       "window": self._serialize_resume_window(t.window),
                       "monitor_activity": t.monitor_activity,
                       "monitor_bell": t.monitor_bell,
                       "monitor_claude": t.monitor_claude}
                      for t in s.tabs]}
            for s in self.sessions.values()]}

    def save_resume_state(self, path: str | None = None) -> bool:
        """현재 트리·패널 상태(살아 있는 셸 PTY 포함)를 상태 파일에 직렬화한다.
        re-exec 직전에 호출되며, 새 이미지가 restore_resume_state 로 복원한다."""
        path = path or self.resume_state_path
        data = self._resume_payload()
        try:
            with ipc.open_private(path) as f:   # 0600(F5): 화면 스냅샷 등 민감정보 보호
                json.dump(data, f)
            return True
        except OSError:
            return False

    def _release_master_cloexec(self):
        """re-exec 직전(ⓐ): 넘길 모든 패널 master fd 의 CLOEXEC 를 해제해 execv 가
        fd 를 닫아 셸을 죽이지 않게 한다. 새 이미지가 채택 직후 다시 건다(ⓓ/adopt).
        평상시엔 절대 풀지 않는다(§6 불변식)."""
        try:
            import fcntl
        except ImportError:
            return
        for p in self._all_panes():
            if p.master_fd is not None and p.master_fd >= 0:
                try:
                    flags = fcntl.fcntl(p.master_fd, fcntl.F_GETFD)
                    fcntl.fcntl(p.master_fd, fcntl.F_SETFD,
                                flags & ~fcntl.FD_CLOEXEC)
                except OSError:
                    pass

    def _build_resume_node(self, spec):
        if spec.get("type") == "split":
            a = self._build_resume_node(spec["a"])
            b = self._build_resume_node(spec["b"])
            return Split(spec.get("orient", "lr"), a, b, spec.get("ratio", 0.5))
        ps = spec["pane"]
        cols, rows = max(MIN_W, ps["cols"]), max(MIN_H, ps["rows"])
        # 상속된 master fd 를 fork 없이 다시 채택한다(PID 그대로 → reap/killpg 유효).
        proc = pty_backend.adopt(ps["master_fd"], ps["child_pid"],
                                 cols=cols, rows=rows)
        fd = proc.fileno() if hasattr(proc, "fileno") else ps["master_fd"]
        pane = Pane(ps["child_pid"], fd, cols, rows)
        pane.pty = proc
        pane.import_state(ps)
        self._attach_reader(pane)
        return pane

    def restore_resume_state(self, path: str | None = None) -> bool:
        """save_resume_state 가 만든 상태 파일에서 세션·탭·트리를 복원하고, 상속된
        PTY master fd 를 채택해 살아 있는 셸에 다시 연결한다. re-exec 후 새 서버
        이미지의 부트 경로에서 호출. docs/RESTART_SCENARIO.md ⓓ."""
        path = path or self.resume_state_path
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if data.get("version") != 1:
            return False
        for ss in data.get("sessions", []):
            tabs = []
            for wt in ss.get("tabs", []):
                wspec = wt["window"]
                try:
                    root = self._build_resume_node(wspec["root"])
                except (KeyError, OSError):
                    continue
                w = Window(root)
                w._fix_parents(root, None)
                apid = wspec.get("active_pid")
                ap = next((p for p in w.panes() if p.child_pid == apid), None)
                w._active = ap or (root if isinstance(root, Pane)
                                   else root.first_pane())
                w.zoomed = wspec.get("zoomed", False)
                w.border_status = wspec.get("border_status", False)
                w.sync = wspec.get("sync", False)
                w.auto_rename = wspec.get("auto_rename", True)
                w.layout_idx = wspec.get("layout_idx", 0)
                t = Tab(wt.get("index", len(tabs)), wt.get("name", "win"), w)
                t.monitor_activity = wt.get("monitor_activity", False)
                t.monitor_bell = wt.get("monitor_bell", True)
                t.monitor_claude = wt.get("monitor_claude", True)
                tabs.append(t)
            if not tabs:
                continue
            sess = Session.restored(
                self._unique_name(ss.get("name")), tabs,
                active_index=max(0, min(ss.get("active_index", 0), len(tabs) - 1)),
                last_index=ss.get("last_index", 0))
            self.sessions[sess.name] = sess
        if self.sessions:
            self._induce_redraw_all()
        return bool(self.sessions)

    def _induce_redraw_all(self):
        """재시작 복원 후 alt-screen TUI(vim/claude/htop 등)가 다시 그리도록 각 패널
        PTY 에 SIGWINCH 를 한 번 유발한다(docs/RESTART_SCENARIO.md 주의 ① 대안 B).

        새 이미지의 pyte 는 메인 화면(직렬화한 스냅샷)에서 시작하지만, 살아 있는
        앱은 alt 화면 상태 그대로다. 재접속 크기가 이전과 같으면 resize 가 SIGWINCH
        를 안 보내 앱이 영영 다시 안 그린다. 그래서 크기를 한 칸 줄였다 되돌려
        커널이 SIGWINCH 를 보내게 강제한다(앱이 현재 화면을 전체 repaint → 스냅샷을
        덮어쓴다). winsize 만 건드리고 pyte/Pane 치수는 그대로 둔다."""
        for p in self._all_panes():
            if p.pty is None:
                continue
            try:
                p.pty.set_winsize(max(1, p.rows - 1), p.cols)
                p.pty.set_winsize(p.rows, p.cols)
            except OSError:
                pass

    def restart_server(self) -> bool:
        """작업 보존 재시작(방식① 제자리 re-exec) — 셸/PTY 를 살린 채 서버 코드만
        새 이미지로 교체한다. docs/RESTART_SCENARIO.md §3. POSIX 전용.

        ⓑ 상태 직렬화 → ⓔ 클라이언트 재접속 통지 → (call_later) ⓐ master fd
        CLOEXEC 해제 → ⓒ os.execv. PID 가 그대로라 자식 셸이 계속 자식으로 남고,
        상속된 master fd 가 PTY 를 살린다. 새 이미지는 --resume 로 채택 복원한다(ⓓ)."""
        if pty_backend.IS_WINDOWS or self.loop is None:
            return False
        if not self.sessions:
            return False
        # 폭주 드레인 중이면 아직 pyte 에 안 먹인 _feedbuf 가 남아 있을 수 있다.
        # execv 로 프로세스 이미지가 바뀌면 그 바이트(파이썬 메모리)는 사라지므로,
        # 직렬화 전에 진행 중 드레인을 멈추고 남은 바이트를 동기로 마저 먹여 화면
        # 스냅샷(_export_screen)에 반영한다.
        for p in self._all_panes():
            buf = p._feedbuf
            self._stop_pane_feed(p)   # 태스크 취소 + _feedbuf 비움
            if buf:
                self._ingest_slice(p, buf)
        if not self.save_resume_state():
            return False
        self._save_opts()
        # ⓔ 재시작 통지: 클라이언트가 끊김을 종료가 아닌 재접속으로 다루게 한다.
        for c in list(self.clients):
            asyncio.create_task(write_msg(c.writer, {"t": "restarting"}))
        # 비-PTY fd(캡처 파일 등) 정리 — 새 이미지가 다시 연다. master fd 는 보존.
        self._close_all_capfiles()
        argv = proc.server_argv(self.sock_path) + ["--resume",
                                                   self.resume_state_path]
        # write_msg 가 flush 될 짧은 틈을 준 뒤 execv(같은 이벤트 루프 틱이 아님).
        self.loop.call_later(0.1, self._do_execv, argv)
        return True

    def restart_check(self) -> dict:
        """restart-all **드라이런**: 실제 재시작 없이 작업 보존 재시작이 안전한지
        점검한 결과를 dict 로 돌려준다(restart-check 명령이 팝업으로 표시). 부작용
        없음 — 상태를 임시로 직렬화/역파싱만 하고 파일/프로세스는 안 건드린다.

        점검: ① re-exec 지원(POSIX·이벤트루프) ② 복원할 세션 존재 ③ 상태 직렬화
        round-trip(json dump→load→구조 검증) ④ 모든 패널이 살아있는 master fd 보유
        (상속해야 셸이 산다) ⑤ 실행 코드 버전(running) vs 디스크 버전(재시작이
        로드할 코드 — 다르면 '갱신됨'이지 위험은 아님)."""
        panes = list(self._all_panes())
        n = len(panes)
        with_fd = sum(1 for p in panes
                      if p.master_fd is not None and p.master_fd >= 0)
        serialize_ok, serialize_err = False, ""
        try:
            back = json.loads(json.dumps(self._resume_payload()))
            serialize_ok = (back.get("version") == 1
                            and isinstance(back.get("sessions"), list)
                            and len(back["sessions"]) == len(self.sessions))
        except (TypeError, ValueError) as e:
            serialize_err = str(e)
        return {
            "reexec_supported": (not pty_backend.IS_WINDOWS
                                 and self.loop is not None),
            "has_sessions": bool(self.sessions),
            "panes": n, "panes_with_fd": with_fd,
            "serialize_ok": serialize_ok, "serialize_err": serialize_err,
            "running_version": self._code_version,
            "disk_version": version.code_version(),
        }

    def _do_execv(self, argv):
        # ⓐ 넘길 master fd 의 CLOEXEC 를 execv 직전에만 해제(평상시 불변식 유지).
        self._release_master_cloexec()
        self.running = False
        try:
            os.execv(argv[0], argv)
        except OSError:
            # execv 실패(드묾): 깨끗한 종료로 폴백. 셸은 SIGHUP 으로 정리된다.
            self._notify_no_sessions()

    # ---- 탭(윈도우+패널) 레이아웃 슬롯: 이름으로 저장/불러오기 ----
    @property
    def slots_path(self):
        # 상태파일 접두 기준(unix=소켓 경로 그대로, tcp=상태 디렉터리/default)
        return ipc.state_base(self.sock_path) + ".slots.json"

    def _load_slots(self) -> dict:
        try:
            with open(self.slots_path, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_slots(self, slots: dict):
        try:
            with ipc.open_private(self.slots_path) as f:   # 0600(F5)
                json.dump(slots, f)
        except OSError:
            pass

    # ---- 서버 옵션 영속(opts.json) ----
    @property
    def opts_path(self) -> str:
        return ipc.state_base(self.sock_path) + ".opts.json"

    def _load_opts(self) -> dict:
        try:
            with open(self.opts_path, encoding="utf-8") as f:
                d = json.load(f)
                return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_opts(self):
        try:
            with ipc.open_private(self.opts_path) as f:   # 0600(F5)
                json.dump({"capture": self.capture,
                           "claude_header": self.claude_header,
                           "single_border": self.single_border,
                           "coalesce_repaints": self.coalesce_repaints,
                           "prompt_clear_message": self.prompt_clear_message,
                           "auto_doc_clear": self.auto_doc_clear,
                           "auto_doc_clear_delay": self.auto_doc_clear_delay,
                           "auto_compact": self.auto_compact,
                           "auto_compact_delay": self.auto_compact_delay,
                           "auto_hardstop": self.auto_hardstop,
                           "auto_cc_cooldown_sec": self.auto_cc_cooldown_sec,
                           "claude_auto_mode": self.claude_auto_mode,
                           "claude_auto_launch": self.claude_auto_launch,
                           "claude_rules": self.claude_rules,
                           "claude_ctx_autoclear": self.claude_ctx_autoclear,
                           "claude_ctx_threshold": self.claude_ctx_threshold,
                           "claude_ctx_action": self.claude_ctx_action,
                           "claude_ctx_min_interval":
                               self.claude_ctx_min_interval,
                           "token_budget_day": self.token_budget_day,
                           "token_budget_session": self.token_budget_session,
                           "token_budget_5h": self.token_budget_5h,
                           "token_budget_account": self.token_budget_account,
                           "claude_long_turn_sec": self.claude_long_turn_sec,
                           "claude_repeat_alert": self.claude_repeat_alert,
                           "token_budget_resume_gate":
                               self.token_budget_resume_gate,
                           "claude_budget_plan": self.claude_budget_plan}, f)
        except OSError:
            pass
