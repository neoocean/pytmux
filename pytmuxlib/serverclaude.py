"""Claude 연동 서버 로직(토큰 리밋 자동재개·프롬프트 단위 클리어·auto-doc-clear·
권한모드 자동전환/구동)을 모은 믹스인. `server.Server` 가 상속한다.

§10 LLM 친화 리팩토링 / §11 Claude 특화 분리: Claude 화면 휴리스틱(`claude.py`)·코어
멀티플렉서(`server.py`)와 충돌 면을 줄이려 Claude 주입 로직을 별도 파일로 모았다.
`ServerClaudeMixin` 의 메서드는 Server 인스턴스(self)에서만 동작하며 self.* 상태와
다른 Server 메서드(`_all_panes`/`_save_opts` 등)를 그대로 참조한다(동작 불변)."""
from __future__ import annotations

import asyncio
import json

from .claude import claude_perm_mode
from .model import Pane, Session

# 권한모드 자동 오토모드 전환(§10): 한 번 idle 진입 후 auto 에 도달하지 못해도 이
# 횟수까지만 shift+tab 을 보낸다(footer 순환 순서가 고정이 아닐 수 있어 폐루프지만,
# 오검출 시 무한 순환을 막는 가드). default↔auto↔plan(↔bypass) 순환을 덮을 만큼.
_CAM_MAX = 4


class ServerClaudeMixin:
    # ---- 토큰 리밋 자동 재개 ----
    def _maybe_schedule_resume(self, pane: Pane, newtext: str):
        pane._scanbuf = (pane._scanbuf + newtext)[-4000:]
        if pane._resume_pending:
            return
        delay = parse_reset_delay(pane._scanbuf)
        if delay is not None:
            pane._resume_pending = True
            # 해제 시각 +5초 버퍼 후 재개 메시지 입력
            self.loop.call_later(delay + 5, self._fire_resume, pane)

    def _fire_resume(self, pane: Pane):
        pane._resume_pending = False
        pane._scanbuf = ""
        if pane.pty is not None:
            try:
                pane.pty.write((pane.resume_msg + "\r").encode("utf-8"))
            except OSError:
                pass

    def set_autoresume(self, sess: Session, value=None, msg: str | None = None):
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        if msg is not None:
            p.resume_msg = msg
            return
        p.autoresume = (not p.autoresume) if value is None else bool(value)
        if p.autoresume and not p._resume_pending:
            # 켜는 순간 이미 화면에 떠 있는 리밋 안내도 즉시 검사
            rows, _ = p.render(False)
            text = "\n".join("".join(s[0] for s in r) for r in rows)
            self._maybe_schedule_resume(p, text)

    # ---- 프롬프트 단위 클리어 모드(#9) ----
    def set_prompt_clear(self, sess: Session, value=None):
        """활성 패널의 프롬프트 단위 클리어 모드 토글. value 미지정 시 반전.
        끄면 진행 중인 상태기계도 리셋한다(다음 프롬프트는 평소대로)."""
        win = sess.active_window
        if not win or not win.active_pane:
            return None
        p = win.active_pane
        p.prompt_clear_mode = (not p.prompt_clear_mode) if value is None \
            else bool(value)
        if not p.prompt_clear_mode:
            p._pc_phase = None
            p.prompt_clear_queue.clear()   # 모드 끄면 쌓인 큐도 비운다(#4)
        return p.prompt_clear_mode

    def pc_queue_add(self, sess: Session, cmd: str):
        """활성 패널의 프롬프트 단위 클리어 큐에 명령을 쌓는다(#4). 각 명령은
        doc+/clear 사이클을 마칠 때마다 하나씩 Claude 에 투입된다. 큐잉은 이
        워크플로를 함의하므로 모드가 꺼져 있으면 켠다. 패널이 한가하고 진행 중인
        시퀀스가 없으면 곧장 첫 명령을 투입해 사이클을 시작한다."""
        win = sess.active_window
        if not win or not win.active_pane:
            return None
        p = win.active_pane
        cmd = (cmd or "").strip()
        if not cmd:
            return None
        p.prompt_clear_queue.append(cmd)
        if not p.prompt_clear_mode:
            p.prompt_clear_mode = True
        if p._pc_phase is None and p._claude == "idle":
            self._pc_drain(p)
        return len(p.prompt_clear_queue)

    def pc_queue_clear(self, sess: Session):
        """활성 패널의 프롬프트 단위 클리어 큐를 비운다(#4)."""
        win = sess.active_window
        if win and win.active_pane:
            win.active_pane.prompt_clear_queue.clear()

    def set_prompt_clear_message(self, msg: str):
        """① 문서화 지시문 문구를 바꾸고 opts.json 에 영속."""
        msg = (msg or "").strip()
        if msg:
            self.prompt_clear_message = msg
            self._save_opts()
        return self.prompt_clear_message

    def _pc_inject(self, pane: Pane, text: str):
        """패널 안 Claude 에게 한 줄 입력+Enter 주입(자동재개 _fire_resume 와 동일 경로).
        프롬프트 추적/히스토리를 거치지 않아 사용자 프롬프트와 섞이지 않는다."""
        if pane.pty is None:
            return
        try:
            pane.pty.write((text + "\r").encode("utf-8"))
        except OSError:
            pass

    # 본문 붙여넣기 처리가 끝난 뒤 Enter 를 보낼 지연(초). Claude Code 가 빠르게
    # 도착한 본문+\r 을 하나의 '붙여넣기'로 보고 마지막 \r 을 줄바꿈으로 흡수하던
    # 문제(타이핑만 되고 전송 안 됨)를 피하려고 Enter 를 한 박자 뒤 별도로 보낸다.
    _RULES_ENTER_DELAY = 0.25

    def _inject_rules(self, pane: Pane):
        """저장된 시작 규칙(#27)을 Claude 시작/clear 시 패널 프롬프트에 넣고 **엔터까지
        눌러 제출**한다. 본문(여러 줄 가능)은 \\n(=Claude 입력 줄바꿈)으로 한 번에 넣고,
        Enter(\\r)는 본문 처리가 끝난 뒤 **별도 쓰기**로 보낸다 — 본문과 \\r 을 한 번에
        보내면 Claude Code 가 통째로 붙여넣기로 보고 \\r 을 줄바꿈으로 흡수해 제출이 안
        되기 때문이다(타이핑만 되고 전송 안 됨). 규칙이 비었으면 무동작."""
        text = (self.claude_rules or "").strip()
        if not text or pane.pty is None:
            return
        # 본문 내부 개행은 \n(미제출)으로. Enter 는 아래에서 별도로.
        payload = text.replace("\r\n", "\n").replace("\r", "\n")
        try:
            pane.pty.write(payload.encode("utf-8"))
        except OSError:
            return

        def _send_enter():
            # 패널이 그새 닫혔을 수 있어 매번 확인.
            try:
                if pane.pty is not None:
                    pane.pty.write(b"\r")
            except OSError:
                pass

        # loop 가 있으면 한 박자 뒤 별도 Enter, 없으면(드묾) 즉시.
        if self.loop is not None:
            self.loop.call_later(self._RULES_ENTER_DELAY, _send_enter)
        else:
            _send_enter()

    def _pc_drain(self, pane: Pane):
        """큐(#4)의 다음 명령을 Claude 에 투입하고 새 사이클을 시작한다. last_prompt
        를 그 명령으로 갱신해 헤더가 '지금 처리 중인 명령'을 보이게 하고, phase 는
        None 으로 둬 그 명령 완료 시 다시 doc→/clear 사이클이 돌게 한다."""
        if not pane.prompt_clear_queue:
            return
        nxt = pane.prompt_clear_queue.pop(0)
        pane.last_prompt = nxt
        pane._pc_phase = None
        self._pc_inject(pane, nxt)

    def _pc_advance(self, pane: Pane):
        """프롬프트 단위 클리어 상태기계를 busy→idle 경계에서 한 단계 전진한다.

        phase None(사용자 프롬프트 완료) → 문서화 지시 주입(phase=doc)
        phase doc(문서화 응답 완료)      → /clear 주입(phase=clear)
        phase clear(/clear 완료)         → 큐(#4)에 다음 명령이 있으면 투입하고 새
                                           사이클로, 없으면 시퀀스 종료(phase=None)
        """
        ph = pane._pc_phase
        if ph is None:
            self._pc_inject(pane, self.prompt_clear_message)
            pane._pc_phase = "doc"
        elif ph == "doc":
            self._pc_inject(pane, "/clear")
            pane._pc_phase = "clear"
            # /clear 직후엔 시작 규칙을 다시 넣는다(#27): 다음 idle 에 1회 주입 예약.
            if self.claude_rules.strip():
                pane._rules_pending = True
        else:  # "clear"
            pane._pc_phase = None
            if pane.prompt_clear_queue:
                self._pc_drain(pane)

    # ---- 자동 doc→/clear(§10): idle 지속 N초 후 1회 문서화→/clear ----
    def set_auto_doc_clear(self, value=None):
        """Claude idle 지속 시 자동 문서화→/clear 모드 토글. value 미지정 시 반전.
        끄면 무장된 모든 패널 타이머를 해제한다. opts.json 영속."""
        self.auto_doc_clear = (not self.auto_doc_clear) if value is None \
            else bool(value)
        if not self.auto_doc_clear:
            for p in self._all_panes():
                self._adc_disarm(p)
        self._save_opts()
        return self.auto_doc_clear

    def _adc_disarm(self, pane: Pane):
        """무장된 자동 doc→/clear 타이머를 해제한다(사용자 입력·재busy·세션 종료·
        토글 off 시). 핸들이 없으면 무동작."""
        t = getattr(pane, "_adc_timer", None)
        if t is not None:
            t.cancel()
            pane._adc_timer = None

    def _adc_arm(self, pane: Pane):
        """idle 진입 시점에 무장: auto_doc_clear_delay 초 뒤 _adc_fire 를 예약한다.
        기존 타이머가 있으면 먼저 해제(재무장). 실행 중인 이벤트 루프가 없으면
        (테스트가 _scan_claude 를 동기 호출하는 등) 조용히 패스한다 — 타이머 기반
        자동 발화만 비활성일 뿐 다른 동작에는 영향이 없다."""
        self._adc_disarm(pane)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pane._adc_timer = loop.call_later(
            self.auto_doc_clear_delay, self._adc_fire, pane)

    def _adc_fire(self, pane: Pane):
        """타이머 만료(idle 가 N초 지속됨): 발화 조건을 재확인한 뒤 문서화→/clear
        시퀀스를 시작한다. idle 이 아니거나(그새 busy/limit/종료), 이미 진행 중이거나,
        수동 prompt_clear_mode 거나, 토글이 꺼졌으면 발화하지 않는다.
        시작 = _pc_phase 를 None 으로 두고 _pc_advance 로 문서화 지시문을 주입(→doc).
        이후 busy→idle 경계마다 _scan_claude 가 doc→clear→종료로 시퀀스를 잇는다."""
        pane._adc_timer = None
        if not self.auto_doc_clear:
            return
        if pane._claude != "idle":
            return
        if pane._adc_active or pane.prompt_clear_mode:
            return
        pane._adc_active = True
        pane._pc_phase = None
        self._pc_advance(pane)

    # ---- 권한모드 자동 오토모드 전환(§10) ----
    def set_claude_auto_mode(self, value=None):
        """Claude idle 시 권한모드를 auto 로 자동 맞추는 모드 토글. value 미지정 시
        반전. 끄면 모든 패널의 시도 카운터를 리셋한다. opts.json 영속."""
        self.claude_auto_mode = (not self.claude_auto_mode) if value is None \
            else bool(value)
        if not self.claude_auto_mode:
            for p in self._all_panes():
                p._cam_tries = 0
                p._cam_last = None
        self._save_opts()
        return self.claude_auto_mode

    def _inject_keys(self, pane: Pane, data: bytes):
        """패널 PTY 로 raw 키 바이트를 보낸다(_pc_inject 와 달리 Enter 를 안 붙임).
        권한모드 순환(shift+tab=\\x1b[Z) 등 제어키 주입용."""
        if pane.pty is None:
            return
        try:
            pane.pty.write(data)
        except OSError:
            pass

    def _maybe_auto_mode(self, pane: Pane, txt: str):
        """idle 패널의 권한모드 footer 를 확인해 auto 가 아니면 shift+tab(backtab,
        \\x1b[Z)을 한 번 보내 권한모드를 순환시킨다(폐루프 — auto 가 될 때까지 다음
        프레임에 다시 시도, 최대 _CAM_MAX). footer 순서가 고정이 아닐 수 있어 폐루프로
        간다. 화면 갱신 전(직전과 같은 모드)에는 중복 주입하지 않는다.

        대상 제외: footer 미관측(None)·이미 auto·bypass(명시적 위험 모드는 안 건드림)."""
        mode = claude_perm_mode(txt)
        if mode is None:
            return                       # footer 안 보임 — 판정 불가, 상태 유지
        if mode in ("auto", "bypass"):
            pane._cam_tries = 0          # 목표 도달/대상 아님 → 시도 리셋
            pane._cam_last = mode
            return
        # default/plan → auto. 직전에 작용한 모드와 같으면(아직 화면 미갱신) 대기.
        if mode == pane._cam_last and pane._cam_tries > 0:
            return
        if pane._cam_tries >= _CAM_MAX:
            return                       # 무한 순환 가드(오검출 대비)
        pane._cam_tries += 1
        pane._cam_last = mode
        self._inject_keys(pane, b"\x1b[Z")

    def _drive_perm_mode(self, pane: Pane, txt: str, target: str):
        """idle 패널의 권한모드를 사용자가 고른 target 으로 폐루프 구동한다(§10 item 2,
        footer 클릭 팝업). shift+tab(backtab \\x1b[Z)을 한 번 보내고 다음 프레임에
        footer 를 재확인해 target 에 도달할 때까지 반복(순환 순서가 Claude 버전 의존
        이라 폐루프). 도달하거나 _CAM_MAX 초과(오검출/순서 상이)면 _perm_target 해제.
        화면 갱신 전(직전과 같은 모드)에는 중복 주입하지 않는다(_maybe_auto_mode 와
        같은 cam 카운터 공유)."""
        mode = claude_perm_mode(txt)
        if mode is None:
            return                       # footer 안 보임 — 대기
        if mode == target:
            pane._perm_target = None     # 도달
            pane._cam_tries = 0
            pane._cam_last = None
            return
        if mode == pane._cam_last and pane._cam_tries > 0:
            return                       # 화면 미갱신 — 대기
        if pane._cam_tries >= _CAM_MAX:
            pane._perm_target = None      # 못 맞춤 — 포기
            pane._cam_tries = 0
            pane._cam_last = None
            return
        pane._cam_tries += 1
        pane._cam_last = mode
        self._inject_keys(pane, b"\x1b[Z")

    def set_claude_perm_mode(self, sess: Session, target: str, pane_id=None):
        """활성(또는 지정) 패널의 권한모드 목표를 설정한다(footer 클릭 팝업, §10 item 2).
        _scan_claude 가 idle 시 _drive_perm_mode 로 그 모드까지 폐루프 주입한다.
        bypass 는 명시적 위험 모드라 목록엔 없지만 목표로는 허용한다(완결성)."""
        win = sess.active_window
        if not win:
            return
        p = (win.pane_by_id(pane_id) if pane_id is not None else None) \
            or win.active_pane
        if not p or target not in ("auto", "plan", "default", "bypass"):
            return
        p._perm_target = target
        p._cam_tries = 0
        p._cam_last = None
