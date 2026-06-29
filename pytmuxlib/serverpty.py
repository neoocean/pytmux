"""PTY/패널 생성·자식 셸 fork·출력 feed(폭주 드레인·슬라이스 ingest)·EOF 처리
서버 로직 믹스인. `server.Server` 가 상속한다(§10 LLM 친화 리팩토링). 동작 불변 —
self.* 상태와 Server 의 다른 메서드(_remove_pane_from_tree·_capture_write 등)를 참조."""
from __future__ import annotations

import asyncio
import base64
import gc
import hmac
import os
import time

from . import ipc, proc, pty_backend, sshwrap
from .model import Pane, Session, coalesce_alt_repaints
from .protocol import FEED_SLICE, MIN_H, MIN_W


# §1.7 중첩 능동 감지(in-band): 패널 안 프로그램이 XTVERSION(ESC[>0q)으로 "호스트
# 단말이 누구냐"를 질의하면 실제 터미널처럼 단말명으로 응답한다. env 마커(LC_PYTMUX)가
# 전파되지 않는 경로(ssh 래퍼 우회·sshd AcceptEnv 부재)에서도 원격 pytmux 가 자신이
# pytmux 패널 안임을 알아채 중첩 TUI(→ crash-relaunch/재접속 루프)를 띄우지 않게 한다.
# 부수효과로 패널 안 일반 프로그램(neovim 등)의 XTVERSION 질의에도 올바로 응답.
NEST_QUERY = b"\x1b[>0q"            # XTVERSION 질의
NEST_REPLY = b"\x1bP>|pytmux\x1b\\"  # DCS > | <name> ST

# 원격 중첩 자동 승격(NESTED_ATTACH_SCENARIO §4)의 가변 길이 NEST DCS 스캔. 와이어
# 상수/정규식은 sshwrap(leaf — 래퍼/launcher/model 과 공유): 공통 머리로 빠른 부재
# 판정, 페이로드는 b64 클래스만 허용(임의 출력 오인 충돌 차단 + 선형 매칭 보장).
_NEST_PRE = sshwrap.NEST_PRE
_NEST_DCS_RE = sshwrap.NEST_DCS_RE
# 미완(ST 미도착) NEST DCS 후보 보관 상한 — 우리 페이로드(argv b64)는 수 KB 면 충분,
# 이를 넘는 후보는 위조/우연이므로 버린다(무한 누적 방지).
NEST_CARRY_MAX = 8 * 1024

# 버스트 감지(_on_pane_data) 임계. Windows owned-ConPTY 는 read 를 FEED_SLICE 로 캡해
# 모든 청크가 인라인 한계 이하 → 대량 출력도 버스트 드레인을 못 타고 인라인으로 연속
# 처리돼 이벤트 루프가 포화·입력이 굶었다(Claude Code 등 끊임없는 alt-screen 리페인트).
# 짧은 간격(BURST_GAP)으로 BURST_RUN 회 연달아 오면 버스트로 보고 드레인 경로로 돌린다.
# 분명한 유휴(BURST_IDLE)가 오면 해제 — 그 사이(드레인 직후 resume 등)는 상태를 유지해
# 드레인↔인라인 flap 을 막는다. 단발 대화형 에코는 간격이 커 인라인 즉시 처리로 남는다.
BURST_GAP = 0.02     # 이 간격(초) 이하로 연속 도착하면 버스트 누적
BURST_IDLE = 0.10    # 이 간격 이상 비면 버스트 상태 해제
BURST_RUN = 3        # 연속 누적이 이 횟수 이상이면 버스트로 판정


class ServerPtyMixin:
    # ---- PTY/패널 생성 ----
    def _fork_shell(self, cols: int, rows: int, cwd: str | None = None,
                    cmd: str | None = None):
        """셸을 PTY 백엔드(pty_backend)에 띄우고 PtyProcess 핸들을 반환.

        OS 별 PTY/프로세스 분기는 전부 pty_backend 가 담당한다(Unix=pty.fork,
        Windows=ConPTY). CLOEXEC·winsize·논블로킹 설정도 백엔드 spawn 안에서 처리.

        cmd 가 주어지면 인터랙티브 셸 대신 `셸 -c <cmd>` 로 그 명령만 실행한다
        (display-popup 라이브 PTY 용). 명령이 끝나면 셸이 종료→PTY EOF→팝업 자동 닫힘.
        """
        cols = max(MIN_W, cols)
        rows = max(MIN_H, rows)
        argv, env = self._shell_argv_env(cmd)
        return pty_backend.spawn(argv, cols=cols, rows=rows, cwd=cwd, env=env)

    def _shell_argv_env(self, cmd: str | None = None):
        """셸 실행 argv + 환경을 만든다(인프로세스 spawn 과 host 모드 spawn 공용).

        cmd 가 주어지면 인터랙티브 셸 대신 `셸 -c <cmd>`(display-popup 라이브 PTY).
        OS 별 셸/플래그 분기는 여기 한 곳. host 모드(옵션 C)는 이 argv/env 를 host 로
        넘겨 host 가 같은 셸을 띄운다."""
        env = self._panel_env()
        if pty_backend.IS_WINDOWS:
            shell = env.get("PYTMUX_SHELL") or env.get("COMSPEC") or "cmd.exe"
            argv = [shell, "/c", cmd] if cmd else [shell]
        else:
            shell = env.get("SHELL", "/bin/sh")
            argv = [shell, "-c", cmd] if cmd else [shell]
        return argv, env

    def _next_pane_id(self) -> int:
        """host 모드 패널 id 할당(서버 전역 단조 증가). 재연결 후엔 list_panes 의
        최대 id 위로 올려 충돌을 피한다(P5 reattach 에서 _pane_seq 보정)."""
        self._pane_seq += 1
        return self._pane_seq

    def _spawn_pane_host(self, cols: int, rows: int, cwd, cmd) -> Pane:
        """host 모드: 셸을 host 프로세스에 띄우고 _RemotePtyProcess 로 감싼 Pane 반환.
        패널은 host_pane_id 로 식별한다(child_pid/master_fd 는 -1). 출력 누락이 없도록
        reader 등록(start_reader)을 host spawn **이전**에 한다."""
        cols, rows = max(MIN_W, cols), max(MIN_H, rows)
        argv, env = self._shell_argv_env(cmd)
        pane_id = self._next_pane_id()
        proc = self._pty_host.make_pane(pane_id, cols, rows)
        pane = Pane(-1, -1, cols, rows,
                    vt_parser=getattr(self, "vt_parser", "native"))
        pane.host_pane_id = pane_id
        pane.pty = proc
        self._attach_reader(pane)                  # client.register(pane_id, …) 선행
        self._pty_host.spawn(pane_id, argv, cols, rows, cwd=cwd, env=env)
        return pane

    def _panel_env(self) -> dict:
        """패널 셸에 줄 환경을 만든다(os.environ 기반 + pytmux 표식·색·중첩 거부 래퍼).
        spawn 에서 분리해 테스트가 PTY 없이 검증한다(특히 COLORTERM 일관성)."""
        env = dict(os.environ)
        env["TERM"] = "xterm-256color"
        # COLORTERM=truecolor 를 명시 설정한다(요청 2026-06-16): pytmux 렌더 파이프라인은
        # 끝까지 24비트(256색도 RGB 로 변환해 emit)이므로 셸 프로그램이 항상 truecolor 를
        # 내게 해 색을 일관·풍부하게 한다. **특히 원격(ssh stdio-proxy)** 셸은 COLORTERM 이
        # 상속되지 않아(ssh SendEnv 기본=LANG/LC_*, COLORTERM 미포함) 로컬은 truecolor·원격은
        # 256색 폴백 → pyte 256→RGB 테이블로 변환돼 같은 프로그램 색이 미묘하게 달랐다.
        env["COLORTERM"] = "truecolor"
        env["PYTMUX"] = self.resolved_endpoint
        env.pop("LINES", None)
        env.pop("COLUMNS", None)
        # 원격(ssh) 중첩 거부: 표식(LC_PYTMUX) + ssh 래퍼를 패널 셸에 주입해 ssh 로
        # 들어간 원격에서도 pytmux 중첩이 막히게 한다(docs/internal/HANDOFF.md §10). 로컬
        # 중첩은 위 $PYTMUX 가 담당한다.
        sshwrap.panel_env(env, ipc.default_state_dir())
        return env

    def _attach_reader(self, pane: Pane) -> None:
        """패널 PTY 의 읽기를 시작한다(on_data/on_eof 는 이벤트 루프 스레드에서 호출)."""
        self._stop_pane_feed(pane)   # 재attach 시 이전 드레인 잔여 상태 초기화
        pane.pty.start_reader(
            self.loop,
            lambda d, p=pane: self._on_pane_data(p, d),
            lambda p=pane: self._pane_eof(p))

    def spawn_pane(self, cols: int, rows: int, cwd: str | None = None,
                   cmd: str | None = None) -> Pane:
        if self._pty_host is not None:        # host 모드(옵션 C, Windows 세션유지)
            return self._spawn_pane_host(cols, rows, cwd, cmd)
        proc = self._fork_shell(cols, rows, cwd, cmd)
        fd = proc.fileno() if hasattr(proc, "fileno") else -1
        pane = Pane(proc.pid, fd, max(MIN_W, cols), max(MIN_H, rows),
                    vt_parser=getattr(self, "vt_parser", "native"))
        pane.pty = proc
        self._attach_reader(pane)
        return pane

    def respawn_pane(self, sess: Session):
        """활성 패널의 셸을 종료하고 같은 슬롯에서 새 셸을 띄운다."""
        win = sess.active_window
        if not win or not win.active_pane:
            return
        pane = win.active_pane
        cwd = self._pane_cwd(pane)
        self._stop_pane_feed(pane)          # 진행 중 드레인 취소(새 셸로 교체 전)
        if pane.pty is not None:
            pane.pty.stop_reader()
            pane.pty.kill()                 # SIGKILL 즉시 종료
            pane.pty.close()
            pane.pty.reap(block=True)       # SIGKILL 이므로 블로킹 회수 안전
        if self._pty_host is not None:      # host 모드: 새 원격 PTY 로 교체
            argv, env = self._shell_argv_env(None)
            pane_id = self._next_pane_id()
            proc = self._pty_host.make_pane(pane_id, pane.cols, pane.rows)
            pane.reinit(-1, -1, pane.cols, pane.rows)
            pane.host_pane_id = pane_id
            pane.pty = proc
            self._attach_reader(pane)
            self._pty_host.spawn(pane_id, argv, pane.cols, pane.rows,
                                 cwd=cwd, env=env)
            return
        proc = self._fork_shell(pane.cols, pane.rows, cwd)
        fd = proc.fileno() if hasattr(proc, "fileno") else -1
        pane.reinit(proc.pid, fd, pane.cols, pane.rows)
        pane.pty = proc
        self._attach_reader(pane)

    def _on_pane_data(self, pane: Pane, data: bytes):
        # PTY 읽기/EOF/EIO 처리는 pty_backend 가 담당하고, 여기엔 수신 바이트 처리만
        # 남는다(backend 가 on_data 를 이벤트 루프 스레드에서 부른다).
        #
        # §1.7 XTVERSION 질의 스캔(전 청크가 이 진입점을 지나므로 여기 한 곳에서):
        # 전체 복사 없이 data 본문 + (carry+머리 4바이트)만 검사한다. 질의를 보면
        # 단말명 응답을 그 패널 stdin 으로 써 준다(실제 터미널과 동일 의미론 —
        # cat 된 파일 속 질의에도 응답하는 것까지 같다).
        if NEST_QUERY in data or NEST_QUERY in (
                pane._nestq_carry + data[:len(NEST_QUERY) - 1]):
            if pane.pty is not None:
                try:
                    pane.pty.write(NEST_REPLY)
                except OSError:
                    pass
        tail = len(NEST_QUERY) - 1
        pane._nestq_carry = (data[-tail:] if len(data) >= tail
                             else (pane._nestq_carry + data)[-tail:])
        # NESTED_ATTACH §4: 같은 진입점에서 NEST DCS(ssh 목적지 기록·승격 요청)도
        # 스캔한다 — 평시 비용은 공통 머리 부재 확인(in) 1회.
        self._nest_dcs_scan(pane, data)
        #
        # 대량 출력 비차단 처리: pyte feed 는 순수 파이썬이라 64KB 한 읽기를 통째로
        # 먹이면 ~50ms 동안 이벤트 루프가 막혀 입력·flush 가 지연된다. 그래서 버스트는
        # reader 를 잠깐 떼고(커널 PTY 백프레셔 유지 → 데이터 손실/메모리 폭증 없음)
        # FEED_SLICE 단위로 쪼개 먹이며 슬라이스마다 루프에 양보한다(_feed_drain).
        # 소량(대화형 에코 등)은 인라인 즉시 처리해 기존 경로와 동일하게 둔다.
        # 버스트 감지: 짧은 간격으로 연달아 오는 청크의 연속 횟수를 센다. owned-ConPTY
        # 처럼 read 가 FEED_SLICE 로 캡돼 청크가 항상 인라인 한계 이하인 백엔드에서도,
        # 고빈도 버스트를 드레인 경로(pause 백프레셔·repaint coalesce·슬라이스 양보)로
        # 돌려 이벤트 루프 포화·입력 굶주림을 막기 위함이다(§10 / Windows Claude 패널).
        now = time.monotonic()
        gap = now - pane._burst_ts
        pane._burst_ts = now
        if gap <= BURST_GAP:
            pane._burst_run = min(pane._burst_run + 1, BURST_RUN)
        elif gap >= BURST_IDLE:
            pane._burst_run = 0      # 분명한 유휴 → 해제(단발 에코는 여기서 리셋)
        # 그 사이 간격(BURST_GAP~BURST_IDLE)은 상태 유지 → 드레인 직후 resume 의 flap 방지.
        bursting = pane._burst_run >= BURST_RUN
        if pane._feed_task is not None:
            # 이미 드레인 중 → 큐에 이어 붙이면 실행 중 태스크가 소비한다(POSIX 는
            # reader 가 멈춰 여기 거의 안 옴; pause 가 no-op 인 백엔드의 버스트 대비).
            pane._feedbuf += data
            self._coalesce_feed(pane)
            return
        if len(data) <= FEED_SLICE and not bursting:
            self._ingest_slice(pane, data)
            return
        pane._feedbuf += data
        self._coalesce_feed(pane)
        if pane.pty is not None:
            pane.pty.pause_reader()
        pane._feed_task = self.loop.create_task(self._feed_drain(pane))

    # ---- 원격 중첩 자동 승격 NEST DCS 스캔(NESTED_ATTACH_SCENARIO §4) ----
    def _nest_dcs_scan(self, pane: Pane, data: bytes) -> None:
        """패널 출력에서 NEST_DEST(래퍼의 ssh 목적지 기록)·NEST_ATTACH_REQ(원격
        pytmux 의 승격 요청) DCS 를 찾는다. read 경계 분할은 `pane._nestd_carry` 로
        보전하되, 큰 carry 는 '미완 DCS 후보가 실제로 있을 때만' 유지한다 — 평시엔
        공통 머리(`_NEST_PRE`) 부재 확인과 꼬리 ESC 검사(≤머리 길이)만 돈다."""
        buf = pane._nestd_carry + data if pane._nestd_carry else data
        if _NEST_PRE not in buf:
            self._nest_tail_carry(pane, buf)
            return
        pos = 0
        for m in _NEST_DCS_RE.finditer(buf):
            self._nest_dcs_handle(pane, m.group(1), m.group(2))
            pos = m.end()
        rest = buf[pos:]
        idx = rest.rfind(_NEST_PRE)
        if (idx != -1 and sshwrap.DCS_ST not in rest[idx:]
                and len(rest) - idx <= NEST_CARRY_MAX):
            pane._nestd_carry = rest[idx:]   # ST 미도착 후보 — 다음 청크와 이어 스캔
        else:
            # 완결됐는데 미매치(비 b64 위조)거나 과대 후보 → 버리고 꼬리만 보전.
            self._nest_tail_carry(pane, rest)

    @staticmethod
    def _nest_tail_carry(pane: Pane, rest: bytes) -> None:
        """경계 분할 대비: 꼬리가 `_NEST_PRE` 의 접두(부분 머리)로 끝날 때만 보관."""
        tail = rest[-(len(_NEST_PRE) - 1):]
        i = tail.rfind(b"\x1b")
        pane._nestd_carry = (tail[i:] if i != -1 and _NEST_PRE.startswith(tail[i:])
                             else b"")

    def _nest_dcs_handle(self, pane: Pane, kind: bytes, payload: bytes) -> None:
        """완결 NEST DCS 1건 처리. b64 해독 실패는 조용히 무시(패널 출력은 신뢰
        경계 밖 — 시나리오 §7). ssh=목적지 기록(소비자는 승격 요청), nest=승격
        요청(serverremote._nest_attach_request 가 가드/ack/attach 담당)."""
        try:
            text = base64.b64decode(payload).decode("utf-8", "replace")
        except ValueError:
            return
        if kind == b"ssh":
            # provenance 검증(NEW-1): 머리줄 토큰이 서버 것과 일치할 때만 목적지를
            # 기록한다. 일치하지 않으면(빈 토큰 = 패널 env 를 못 읽은 `cat`/스크롤백/
            # 원격 출력의 위조 DCS) 조용히 무시 — 위조된 _ssh_dest 로 임의 호스트에
            # 자동 attach 하던 경로를 차단한다(NESTED_ATTACH_SCENARIO §7).
            lines = text.split("\n")
            tok = lines[0] if lines else ""
            expected = self._sshwrap_token()
            if not expected or not hmac.compare_digest(tok, expected):
                return
            dest = sshwrap.parse_dest([s for s in lines[1:] if s])
            if dest:
                pane._ssh_dest = dest
                pane._ssh_dest_ts = time.monotonic()
            return
        self._nest_attach_request(pane, text.strip())

    def _sshwrap_token(self) -> str:
        """이 서버의 ssh 래퍼 provenance 토큰(NEST_TOKEN_ENV). 디스크 읽기를 피하려
        한 번 읽고 캐시한다 — panel_env 가 패널 셸에 심는 값과 같다."""
        tok = getattr(self, "_sshwrap_tok", None)
        if tok is None:
            tok = self._sshwrap_tok = sshwrap.load_or_create_token(
                ipc.default_state_dir())
        return tok

    def _coalesce_feed(self, pane: Pane) -> None:
        """대기 중인 feedbuf 에서 무효화된 alt-screen 리페인트 프레임을 합쳐 pyte feed
        부하를 줄인다(§10 대응 ②). 옵션이 꺼져 있거나 안전 조건이 안 맞으면 no-op."""
        if not self.coalesce_repaints or not pane._feedbuf:
            return
        pane._feedbuf = coalesce_alt_repaints(pane._feedbuf, pane.alt_active)

    def _gc_drain_enter(self) -> None:
        """드레인 진입: 첫 드레인이면 순환 GC 를 끈다(중첩은 깊이만 +1)."""
        if self._gc_drain_depth == 0:
            self._gc_was_enabled = gc.isenabled()
            if self._gc_was_enabled:
                gc.disable()
        self._gc_drain_depth += 1

    def _gc_drain_exit(self) -> None:
        """드레인 종료: 마지막 드레인이면 GC 를 (원래 켜져 있었으면) 다시 켜고 1회
        collect 해 드레인 창에서 미룬 회수를 즉시 처리한다."""
        if self._gc_drain_depth > 0:
            self._gc_drain_depth -= 1
        if self._gc_drain_depth == 0 and self._gc_was_enabled:
            gc.enable()
            gc.collect()

    async def _feed_drain(self, pane: Pane):
        """버스트 바이트를 FEED_SLICE 단위로 먹이며 슬라이스마다 이벤트 루프에 양보.
        모두 비우면 reader 를 재개해 다음 배치를 읽는다(취소 시엔 재개 안 함).

        드레인이 도는 동안은 순환 GC 를 꺼 둔다(_gc_drain_enter/exit, §10) — 슬라이스
        중간 GC 일시정지로 인한 입력 끊김을 없앤다."""
        cancelled = False
        self._gc_drain_enter()
        try:
            while pane._feedbuf:
                # 현재 버퍼를 통째로 떼어내(스냅샷) 오프셋으로 슬라이스한다. 과거엔
                # `_feedbuf = _feedbuf[n:]` 로 매 슬라이스 잔여 **전체를 재복사**해
                # backlog 가 수 MB 로 쌓이면 슬라이싱만 O(n²)(4MB≈50ms)로 루프를 막았다.
                # 스냅샷을 비워 두므로 드레인 중 도착분(append)·coalesce 는 새 _feedbuf
                # 에 쌓이고 다음 바깥 루프에서 처리된다(동시성·시각 결과 불변).
                buf = pane._feedbuf
                pane._feedbuf = b""
                off, total = 0, len(buf)
                while off < total:
                    n = min(FEED_SLICE, total - off)
                    self._ingest_slice(pane, buf[off:off + n])
                    off += n
                    await asyncio.sleep(0)   # 양보: 입력/flush/render 가 끼어든다
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            pane._feed_task = None
            self._gc_drain_exit()
            if not cancelled and pane.pty is not None:
                try:
                    pane.pty.resume_reader()
                except OSError:
                    pass

    def _stop_pane_feed(self, pane: Pane):
        """진행 중인 드레인 태스크를 취소하고 대기 버퍼를 비운다(패널 teardown/재attach)."""
        t = pane._feed_task
        if t is not None and not t.done():
            t.cancel()
        pane._feed_task = None
        pane._feedbuf = b""

    def _ingest_slice(self, pane: Pane, data: bytes):
        """수신 바이트 한 조각을 실제로 처리한다(feed + 활동/모드 스캔). _on_pane_data
        (소량 인라인)와 _feed_drain(버스트 슬라이스) 양쪽에서 호출된다."""
        # REC 캡처: 코어가 self.capture/_capture_write 를 이름으로 직접 부르지 않고
        # 훅으로만 닿는다(plugins/rec). 플러그인 부재 시 no-op → 바이트를 그냥 흘려보냄.
        self.plugins.server_pty_output(self, pane, data)
        pane.feed(data)
        pane._activity = True
        if b"\x07" in data:
            pane._bell = True
        # bracketed paste 모드 추적(내부 앱의 DECSET 2004)
        if b"\x1b[?2004h" in data:
            pane.bracketed = True
        if b"\x1b[?2004l" in data:
            pane.bracketed = False
        # 마우스 트래킹 모드 추적(DECSET 1000/1002/1003/1006). 바뀌면 클라이언트가
        # 패스스루 여부를 알도록 레이아웃(패널별 mouse 플래그 포함)을 다시 보낸다.
        if pane.update_mouse_modes(data):
            sess = self._session_of_pane(pane)
            if sess:
                self._broadcast_session(sess)
        # pipe-pane: 패널 출력을 외부 명령으로 복제
        if pane.pipe_proc and pane.pipe_proc.stdin:
            try:
                pane.pipe_proc.stdin.write(data)
                pane.pipe_proc.stdin.flush()
            except (OSError, ValueError):   # broken pipe / 닫힌 stdin
                pane.pipe_proc = None
        if pane.autoresume and not getattr(pane, "_resume_pending", False):
            self._maybe_schedule_resume(pane, data.decode("utf-8", "ignore"))


    def _pane_eof(self, pane: Pane):
        self._stop_pane_feed(pane)   # 진행 중 드레인 취소(정상 EOF 면 이미 빈 상태)
        if pane.pipe_proc:
            try:
                pane.pipe_proc.stdin.close()
            except OSError:
                pass
            pane.pipe_proc = None
        if pane.pty is not None:
            pane.pty.stop_reader()
            pane.pty.close()
            pane.pty.reap(block=False)
        # 라이브 PTY 팝업 패널은 트리에 없으므로(_remove_pane_from_tree 가 못 찾음)
        # 팝업으로 닫는다 — 명령이 끝나면 PTY EOF 로 여기 들어와 자동으로 사라진다.
        for sess in list(self.sessions.values()):
            if sess.popup and sess.popup.get("pane") is pane:
                sess.popup = None
                self._broadcast_session(sess)
                return
        self._remove_pane_from_tree(pane)
