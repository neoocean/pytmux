"""레이아웃/재개상태/슬롯/옵션 영속(직렬화·저장·복원)과 서버 재시작(execv) 서버
로직 믹스인. `server.Server` 가 상속한다(§10 LLM 친화 리팩토링). 동작 불변 — self.*
상태와 Server 의 다른 메서드(spawn_pane·_unique_name·_all_panes 등)를 그대로 참조."""
from __future__ import annotations

import asyncio
import contextlib
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
            with ipc.private_atomic(path) as f:  # 0600(F5) + 원자적 교체(M5)
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

    # ---- 작업 보존 재시작(re-exec) 상태 직렬화/복원 — docs/internal/RESTART_SCENARIO.md ----
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
        # 활성 패널 식별: host 모드면 host_pane_id(child_pid 는 -1), 아니면 child_pid.
        ap = w.active_pane
        active_pid = (ap.host_pane_id if (ap and ap.host_pane_id is not None)
                      else (ap.child_pid if ap else None))
        return {"root": self._serialize_resume_node(w.root),
                "active_pid": active_pid,
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
            for s in self.sessions.values()],
            # §1.7 Stage 3: 원격 링크 spec — 새 이미지가 remote_restore_links 로
            # 재연결한다(ssh 파이프는 CLOEXEC 라 execv 를 살아남지 못함).
            "remotes": [dict(link.spec)
                        for link in getattr(self, "_remotes", {}).values()
                        if getattr(link, "spec", None)]}

    def save_resume_state(self, path: str | None = None) -> bool:
        """현재 트리·패널 상태(살아 있는 셸 PTY 포함)를 상태 파일에 직렬화한다.
        re-exec 직전에 호출되며, 새 이미지가 restore_resume_state 로 복원한다."""
        path = path or self.resume_state_path
        data = self._resume_payload()
        try:
            with ipc.private_atomic(path) as f:  # 0600(F5) + 원자적 교체(M5)
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

    def _rearm_master_cloexec(self):
        """execv 실패 폴백(ⓐ 되돌리기): _release_master_cloexec 로 풀었던 master fd
        의 CLOEXEC 를 다시 건다. 안 걸면, 종료 직전(shutdown 의 0.2s 창)에 떠 있는
        subprocess(예: p4 버전 프로브)가 이 fd 를 상속해 PTY master 를 계속 붙들어,
        서버가 죽어도 셸이 SIGHUP 을 못 받고 고아가 되며 fd 가 샌다(§5.6).
        평상시 불변식(master fd 는 CLOEXEC) 복귀."""
        try:
            import fcntl
        except ImportError:
            return
        for p in self._all_panes():
            if p.master_fd is not None and p.master_fd >= 0:
                try:
                    flags = fcntl.fcntl(p.master_fd, fcntl.F_GETFD)
                    fcntl.fcntl(p.master_fd, fcntl.F_SETFD,
                                flags | fcntl.FD_CLOEXEC)
                except OSError:
                    pass

    def _cleanup_endpoint_files(self):
        """execv 실패 폴백: listen 엔드포인트의 영속 파일(unix 소켓·포트파일·토큰)을
        즉시 정리한다. _notify_no_sessions 가 거는 0.2s 지연 shutdown 만 믿으면, 그
        창에 새 서버 기동이 아직 살아 있는(=곧 죽을) 소켓에 probe 성공해 좀비로
        붙는다(§5.6 stale-소켓 차단). best-effort — 새 서버가 어차피 다시 게시한다."""
        paths = [ipc.portfile_for(self.sock_path), ipc.token_path(self.sock_path)]
        if not ipc.is_tcp(self.sock_path):
            paths.append(self.sock_path)   # AF_UNIX 소켓 파일
        for path in paths:
            try:
                if path and os.path.exists(path):
                    os.unlink(path)
            except OSError:
                pass

    def _close_resume_subtree(self, node) -> None:
        """복원 중 부분 생성된 서브트리의 패널 pty/리더를 닫는다(M4: 형제 노드 빌드
        실패로 이 노드가 통째 스킵될 때 이미 채택된 master fd/리더가 누수되는 것 방지)."""
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, Split):
                stack.append(n.a)
                stack.append(n.b)
            elif isinstance(n, Pane) and n.pty is not None:
                with contextlib.suppress(Exception):
                    n.pty.stop_reader()
                with contextlib.suppress(Exception):
                    n.pty.close()

    def _build_resume_node(self, spec):
        if spec.get("type") == "split":
            a = self._build_resume_node(spec["a"])
            try:
                b = self._build_resume_node(spec["b"])
            except BaseException:
                # M4: a 는 이미 master fd 채택/리더 부착 완료 — b 빌드 실패 시 상위
                # except 가 이 노드를 통째 스킵하므로 여기서 a 의 자원을 회수하고 재전파.
                self._close_resume_subtree(a)
                raise
            return Split(spec.get("orient", "lr"), a, b, spec.get("ratio", 0.5))
        ps = spec["pane"]
        cols, rows = max(MIN_W, ps["cols"]), max(MIN_H, ps["rows"])
        # host 모드(옵션 C) reattach: execv/fd 채택 대신, host 가 재시작을 살아남겨 살려 둔
        # 원격 PTY 에 host_pane_id 로 재바인딩한다. serve() 가 미리 조회한 생존 패널 집합
        # (_host_resume_alive)에 있을 때만 — 갭 중 죽은 패널은 건너뛴다(상위 except 가 스킵).
        hpid = ps.get("host_pane_id")
        if self._pty_host is not None and hpid is not None:
            if hpid not in getattr(self, "_host_resume_alive", set()):
                raise ValueError(f"host pane {hpid} 미생존 — 복원 스킵")
            proc = self._pty_host.make_pane(hpid, cols, rows)
            pane = Pane(-1, -1, cols, rows,
                        vt_parser=getattr(self, "vt_parser", "native"))
            pane.host_pane_id = hpid
            pane.pty = proc
            pane.import_state(ps)
            self._attach_reader(pane)              # client.register → host 라이브 재개
            self._pane_seq = max(self._pane_seq, hpid)  # 이후 새 spawn id 충돌 방지
            return pane
        # S4: 상태파일이 변조됐을 때 임의 fd/pid 로 ioctl·killpg 하는 confused-deputy 를
        # 막는 의미검증(파일은 0600 이지만 심층방어). master_fd 는 실제 상속된 **열린
        # PTY master(char device)** 여야 하고 child_pid 는 양의 정수여야 한다. 위배 시
        # ValueError → 상위 except 가 이 노드를 건너뛴다(레거시 -1 sentinel 도 여기서 탈락).
        mfd, cpid = ps.get("master_fd"), ps.get("child_pid")
        if (isinstance(mfd, bool) or not isinstance(mfd, int) or mfd < 0
                or isinstance(cpid, bool) or not isinstance(cpid, int) or cpid <= 0):
            raise ValueError(f"resume 노드 fd/pid 형식 오류: fd={mfd!r} pid={cpid!r}")
        try:
            import stat as _stat
            if not _stat.S_ISCHR(os.fstat(mfd).st_mode):
                raise ValueError(f"resume master_fd={mfd} 가 PTY(char device) 아님")
        except OSError:
            raise ValueError(f"resume master_fd={mfd} 가 열린 fd 아님")
        # 상속된 master fd 를 fork 없이 다시 채택한다(PID 그대로 → reap/killpg 유효).
        proc = pty_backend.adopt(mfd, cpid, cols=cols, rows=rows)
        try:
            fd = proc.fileno() if hasattr(proc, "fileno") else ps["master_fd"]
            # 작업 보존 재시작으로 복원되는 패널도 현재 opts 의 VT 파서 백엔드를 따른다
            # (spawn_pane 과 동일 — 그러지 않으면 재시작 후 기존 패널만 pyte 로 남는다).
            pane = Pane(ps["child_pid"], fd, cols, rows,
                        vt_parser=getattr(self, "vt_parser", "native"))
            pane.pty = proc
            pane.import_state(ps)
            self._attach_reader(pane)
            return pane
        except BaseException:
            # M4: 채택 후 패널 구성/리더 부착이 실패하면 채택한 master fd 를 닫는다.
            with contextlib.suppress(Exception):
                proc.close()
            raise

    def restore_resume_state(self, path: str | None = None) -> bool:
        """save_resume_state 가 만든 상태 파일에서 세션·탭·트리를 복원하고, 상속된
        PTY master fd 를 채택해 살아 있는 셸에 다시 연결한다. re-exec 후 새 서버
        이미지의 부트 경로에서 호출. docs/internal/RESTART_SCENARIO.md ⓓ."""
        path = path or self.resume_state_path
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        if data.get("version") != 1:
            return False
        # §1.7 Stage 3: 원격 링크 spec 보관 — serve() 가 루프 가동 후
        # remote_restore_links 로 재연결한다(여기선 루프가 아직 없을 수 있음).
        self._remote_resume = data.get("remotes", [])
        for ss in data.get("sessions", []):
            tabs = []
            for wt in ss.get("tabs", []):
                wspec = wt["window"]
                try:
                    root = self._build_resume_node(wspec["root"])
                except (KeyError, OSError, ValueError, TypeError):
                    # S4: 변조/구버전 상태 노드 스킵. TypeError 추가(M4) — 타입 오류
                    # (예: cols 가 문자열)가 복원 전체를 크래시→세션 전손시키던 것 차단.
                    continue
                w = Window(root)
                w._fix_parents(root, None)
                apid = wspec.get("active_pid")
                ap = next((p for p in w.panes()
                           if (p.host_pane_id if p.host_pane_id is not None
                               else p.child_pid) == apid), None)
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
            # 재시작 복원 진단(RESTART_SCENARIO.md §주의①): SIGWINCH **직전**(복원된
            # 스냅샷) 프레임을 찍고, 0.6초 뒤(앱의 SIGWINCH repaint 가 가라앉은 뒤) 다시
            # 찍는다. 둘의 차이 = 그 repaint 가 한 일 — idle 메인화면 패널이 "내용
            # 있음→빔"이면 부분 repaint 가 복원 스냅샷을 지운 것(clobber, 시나리오 A),
            # 복원 시점부터 비어 있으면 스냅샷 자체가 빈 것(시나리오 B). 무게 무시(재시작
            # 시에만, 짧은 메타+스니펫 — resume.json 은 이미 전체 화면을 영속).
            self._write_restore_manifest("restored")
            self._induce_redraw_all()
            if self.loop is not None:
                self.loop.call_later(
                    0.6, lambda: self._write_restore_manifest("post_repaint"))
        return bool(self.sessions)

    def _write_restore_manifest(self, phase: str) -> None:
        """재시작 복원 진단 매니페스트 한 줄(phase=restored|post_repaint)을 패널별로
        `<state>.restartdbg.jsonl` 에 append 한다(best-effort, 절대 복원을 깨지 않음).
        각 패널: id·탭·alt·커서·치수·**비공백 행 수**·마지막 비공백 행 인덱스·상/하단
        텍스트 스니펫. 두 phase 의 비공백 행 수가 줄면(예 restored 18→post 1) 그 패널이
        repaint 에 지워진 것(재시작 후 빈 패널 증상). 파일은 마지막 ~80줄로 캡한다."""
        try:
            import json as _json
            import time as _time
            path = ipc.state_base(self.sock_path) + ".restartdbg.jsonl"
            rows = []
            for p in self._all_panes():
                scr = getattr(p, "_main", None)
                disp = list(getattr(scr, "display", []) or [])
                nonblank = [i for i, r in enumerate(disp) if r.strip()]
                cur = getattr(scr, "cursor", None)
                rows.append({
                    "pane": getattr(p, "id", None),
                    "alt": bool(getattr(p, "alt_active", False)),
                    "rows": getattr(p, "rows", None),
                    "cols": getattr(p, "cols", None),
                    "cursor": ({"x": cur.x, "y": cur.y,
                                "hidden": bool(cur.hidden)} if cur else None),
                    "nonblank_rows": len(nonblank),
                    "last_nonblank": (nonblank[-1] if nonblank else -1),
                    # 마우스 추적 모드 진단(restart-all 후 SGR 시퀀스가 프롬프트에
                    # 새어 텍스트로 입력되는 Windows 버그, HANDOFF §10-H). restored
                    # 와 post_repaint 의 mouse 값을 대조하면, 복원된 플래그가 앱의
                    # 실제 모드(재그리기 후 DECSET 재협상 결과)와 어긋나는지 보인다.
                    "mouse": getattr(p, "mouse_track", None),
                    "mouse_sgr": bool(getattr(p, "mouse_sgr", False)),
                    "top": (disp[0][:60] if disp else ""),
                    "bottom": (disp[nonblank[-1]][:60] if nonblank else ""),
                })
            line = _json.dumps({"ts": _time.time(), "phase": phase,
                                "panes": rows}, ensure_ascii=False)
            # 마지막 ~80줄만 보존(여러 번 재시작해도 무한 성장 방지).
            old = []
            try:
                with open(path, encoding="utf-8") as f:
                    old = f.read().splitlines()[-79:]
            except OSError:
                pass
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(old + [line]) + "\n")
        except Exception:
            pass

    def _induce_redraw_all(self):
        """재시작 복원 후 alt-screen TUI(vim/claude/htop 등)가 다시 그리도록 각 패널
        PTY 에 SIGWINCH 를 한 번 유발한다(docs/internal/RESTART_SCENARIO.md 주의 ① 대안 B).

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

    def restart_server(self, relaunch_clients: bool = False) -> bool:
        """작업 보존 재시작(방식① 제자리 re-exec) — 셸/PTY 를 살린 채 서버 코드만
        새 이미지로 교체한다. docs/internal/RESTART_SCENARIO.md §3. POSIX 전용.

        ⓑ 상태 직렬화 → ⓔ 클라이언트 재접속 통지 → (call_later) ⓐ master fd
        CLOEXEC 해제 → ⓒ os.execv. PID 가 그대로라 자식 셸이 계속 자식으로 남고,
        상속된 master fd 가 PTY 를 살린다. 새 이미지는 --resume 로 채택 복원한다(ⓓ).

        relaunch_clients=True 면 재접속 통지에 relaunch 플래그를 실어, 연결된
        클라가 in-place 재접속 대신 **자신을 relaunch**(새 클라 코드로 재attach)
        하게 한다 — 외부 CLI `restart-all` 이 클라-측 :restart-all 과 같은 효과를
        내도록(클라가 로컬로 _relaunch_on_restart 를 세울 수 없는 외부 트리거 경로).

        host 모드(옵션 C·Windows 세션유지)면 execv 대신 _restart_server_host 로 분기한다
        (HPCON 비이관이라 제자리 교체 불가 → 후속 서버 프로세스 + host 재연결)."""
        if self._pty_host is not None and self.loop is not None:
            return self._restart_server_host(relaunch_clients)
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
        # relaunch_clients 면 클라가 in-place 재접속 대신 relaunch 하도록 플래그를 싣는다.
        note = {"t": "restarting"}
        if relaunch_clients:
            note["relaunch"] = True
        for c in list(self.clients):
            asyncio.create_task(write_msg(c.writer, dict(note)))
        # 비-PTY fd(캡처 파일 등) 정리 — 새 이미지가 다시 연다. master fd 는 보존.
        self.plugins.server_shutdown(self)   # REC 캡처 파일 닫기 등(plugins/rec)
        argv = proc.server_argv(self.sock_path) + ["--resume",
                                                   self.resume_state_path]
        # write_msg 가 flush 될 짧은 틈을 준 뒤 execv(같은 이벤트 루프 틱이 아님).
        self.loop.call_later(0.1, self._do_execv, argv)
        return True

    def _restart_server_host(self, relaunch_clients: bool) -> bool:
        """host 모드 작업보존 재시작(옵션 C·Windows). execv 불가하므로:
        ⓑ resume 직렬화(host_pane_id 포함) → ⓔ 클라 재접속 통지 → 후속 서버 프로세스를
        detached 로 띄움(--resume) → 이 서버는 패널을 **terminate 하지 않고**(셸은 host
        소유·생존) 종료. 후속 서버가 같은 host 에 재연결해 host_pane_id 로 reattach 한다(P5a).

        후속 서버는 ephemeral 포트로 새로 listen 하고 portfile 을 덮으며, host(세션)는 별도
        프로세스라 이 서버의 종료에 영향받지 않는다. 끊긴 동안의 자식 출력은 host 가
        버퍼링했다가 후속 서버 재연결 시 flush 한다(갭 무손실)."""
        if not self.sessions:
            return False
        # 진행 중 드레인 마무리 — 직렬화 스냅샷 정합(POSIX 경로와 동일).
        for p in self._all_panes():
            buf = p._feedbuf
            self._stop_pane_feed(p)
            if buf:
                self._ingest_slice(p, buf)
        if not self.save_resume_state():
            return False
        self._save_opts()
        note = {"t": "restarting"}
        if relaunch_clients:
            note["relaunch"] = True
        for c in list(self.clients):
            asyncio.create_task(write_msg(c.writer, dict(note)))
        argv = proc.server_argv(self.sock_path) + ["--resume",
                                                   self.resume_state_path]
        proc.spawn_detached(argv)
        # write_msg flush 틈을 준 뒤 '패널 보존' 종료(terminate 금지!).
        self.loop.call_later(0.2, self._host_restart_exit)
        return True

    def _host_restart_exit(self) -> None:
        """host 모드 재시작 종료: 패널을 terminate 하지 않고(=host 소유 셸 보존) listen 만
        닫고 루프를 멈춘다. 일반 shutdown() 은 p.pty.terminate()로 자식을 죽여 host 세션을
        잃으므로 이 경로에선 절대 쓰면 안 된다. 원격 프록시 reader 만 떼고 host 연결을
        끊는다(host 는 미전송 출력을 버퍼링→후속 서버가 flush)."""
        self.running = False
        self.remote_shutdown()
        self.plugins.server_shutdown(self)        # REC 캡처 파일 닫기 등(패널 보존)
        for p in self._all_panes():
            if p.pty is not None:
                try:
                    p.pty.stop_reader()           # 콜백만 해제(원격 = client.unregister)
                except Exception:
                    pass
        if self._pty_host is not None:
            # 연결만 닫는다(close_pane 아님!) — 패널은 host 가 계속 소유한다.
            asyncio.create_task(self._pty_host.close())
        try:
            if not ipc.is_tcp(self.sock_path) and os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except OSError:
            pass
        if self.loop:
            self.loop.stop()

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
        host_mode = self._pty_host is not None
        # 복원 가능한 패널: POSIX=살아있는 master fd, host 모드=host_pane_id 보유(원격 PTY).
        with_fd = sum(1 for p in panes
                      if (p.master_fd is not None and p.master_fd >= 0)
                      or (host_mode and p.host_pane_id is not None))
        serialize_ok, serialize_err = False, ""
        try:
            back = json.loads(json.dumps(self._resume_payload()))
            serialize_ok = (back.get("version") == 1
                            and isinstance(back.get("sessions"), list)
                            and len(back["sessions"]) == len(self.sessions))
        except (TypeError, ValueError) as e:
            serialize_err = str(e)
        return {
            # host 모드면 execv 없이 후속 서버 + host 재연결로 재시작 가능(옵션 C).
            "reexec_supported": (host_mode or
                                 (not pty_backend.IS_WINDOWS
                                  and self.loop is not None)),
            "host_mode": host_mode,
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
            # execv 실패(드묾): 채택 인수인계가 무산됐다. 깨끗한 종료로 폴백하되,
            # ① ⓐ 에서 푼 CLOEXEC 를 즉시 되걸어(평상시 불변식 복귀) 종료 직전 떠
            #    있는 subprocess 가 master fd 를 상속해 셸을 고아로 만들지 않게 하고,
            # ② listen 엔드포인트 파일(소켓·포트파일·토큰)을 즉시 정리해 새 서버
            #    기동이 stale 소켓에 붙는 좀비를 막는다(§5.6).
            self._log_error("execv")   # #28: 드문 실패의 진단 단서
            self._rearm_master_cloexec()
            self._cleanup_endpoint_files()
            # 셸은 SIGHUP 으로 정리된다(_notify_no_sessions → shutdown).
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
            with ipc.private_atomic(self.slots_path) as f:  # 0600(F5) + 원자교체(M5)
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
            with ipc.private_atomic(self.opts_path) as f:   # 0600(F5) + 원자교체(M5)
                # capture 는 plugins/rec 가 server_opts_serialize 로 plugin_opts 에 넣는다.
                json.dump({# 플러그인 관리(PLUGIN_MANAGER_SCENARIO): 비활성 플러그인 집합.
                           # 키가 한 번 생기면 default_enabled 시드 대신 이 값이 권위.
                           "disabled_plugins": sorted(self.plugins.disabled),
                           "single_border": self.single_border,
                           "win_mouse_motion": self.win_mouse_motion,
                           "coalesce_repaints": self.coalesce_repaints,
                           "nest_auto_attach": self.nest_auto_attach,
                           "remote_allowed_hosts":
                               list(getattr(self, "remote_allowed_hosts", [])),
                           "prompt_clear_message": self.prompt_clear_message,
                           "claude_auto_mode": self.claude_auto_mode,
                           "claude_auto_launch": self.claude_auto_launch,
                           "claude_rules": self.claude_rules,
                           # 1-3: 종전 누락 → 사용자가 바꿔도 저장 시 소실, 다음 기동에
                           # 600 으로 리셋되던 드리프트 버그 수정(load↔save 대칭 테스트로 가드).
                           "claude_long_turn_sec": self.claude_long_turn_sec,
                           "claude_repeat_alert": self.claude_repeat_alert,
                           "usage_refresh_sec": self.usage_refresh_sec,
                           "vt_parser": self.vt_parser,
                           # 플러그인 소유 설정(S5 토큰 모듈화 T3): claude-code 가 돌려준
                           # 설정(claude_auto_retry 등)을 plugin_opts 네임스페이스에 불투명하게
                           # 저장한다(코어는 키 의미 모름). 디렉토리 삭제 시 {} → 설정이 사라진다.
                           "plugin_opts": self.plugins.server_opts_serialize(self)},
                          f)
        except OSError:
            pass
