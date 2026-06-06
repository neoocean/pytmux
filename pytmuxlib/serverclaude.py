"""Claude 연동 서버 로직(토큰 리밋 자동재개·프롬프트 단위 클리어·auto-doc-clear·
권한모드 자동전환/구동)을 모은 믹스인. `server.Server` 가 상속한다.

§10 LLM 친화 리팩토링 / §11 Claude 특화 분리: Claude 화면 휴리스틱(`claude.py`)·코어
멀티플렉서(`server.py`)와 충돌 면을 줄이려 Claude 주입 로직을 별도 파일로 모았다.
`ServerClaudeMixin` 의 메서드는 Server 인스턴스(self)에서만 동작하며 self.* 상태와
다른 Server 메서드(`_all_panes`/`_save_opts` 등)를 그대로 참조한다(동작 불변)."""
from __future__ import annotations

import asyncio
import json
import time

from . import tokens
from .claude import (claude_account, claude_context_pct, claude_feedback_prompt,
                     claude_prompt, claude_perm_mode, claude_state, claude_usage,
                     ctx_window_tokens, parse_reset_delay)
from .model import Pane, Session

# 권한모드 자동 오토모드 전환(§10): 한 번 idle 진입 후 auto 에 도달하지 못해도 이
# 횟수까지만 shift+tab 을 보낸다(footer 순환 순서가 고정이 아닐 수 있어 폐루프지만,
# 오검출 시 무한 순환을 막는 가드). default↔auto↔plan(↔bypass) 순환을 덮을 만큼.
_CAM_MAX = 4


def screen_text(screen) -> str:
    """pyte 스크린을 줄바꿈 결합한 평문으로 — `"\\n".join(screen.display)` 의 경량
    대체(perf #11). `screen.display` 는 셀마다 `wcwidth()` 를 호출(폭 보정)하지만,
    이 텍스트는 claude.py 의 정규식 8~10개 입력으로만 쓰여 폭 보정이 무의미하다.
    pyte 는 와이드문자 연속셀을 `data==""` 로 저장하므로 셀 data 를 그대로 join 하면
    display 와 **셀 단위 동일**한 결과가 나오며(연속셀은 빈 문자열이라 자연 스킵),
    wcwidth 호출이 사라져 ~2.6× 빠르다(80×24 측정 267µs→101µs). busy Claude 패널은
    매 프레임 변경돼 30Hz 로 도는 핫패스다."""
    buf = screen.buffer
    cols = range(screen.columns)
    return "\n".join(
        "".join(buf[y][x].data for x in cols) for y in range(screen.lines))


# Claude 헤더 행 예약(#1)을 풀기 전, 패널이 연속으로 non-Claude 로 보여야 하는
# 스캔(=flush) 횟수. `_claude` 는 화면 스크래핑이라 footer 가 한두 프레임 안 잡혀
# None 으로 깜빡이는데(특히 ssh/ConPTY 처럼 화면이 조각나 도착할 때), 그때마다
# 예약을 풀면 PTY 가 ±1 행으로 리사이즈를 반복해 원격 Claude 화면이 한 줄씩 위아래로
# 스크롤되는 떨림이 난다. 그래서 예약 해제(=PTY 한 행 키우기)만 디바운스한다(설정
# 은 즉시). 30Hz flush 기준 ~1초 — 진짜 Claude 종료 시 행을 되찾는 지연은 미미하다.
_HDR_CLAUDE_MISS = 30


# 비활성 탭 Claude 완료 알림(#22) 플리커 방지(§10 #18): busy→idle 후 idle 이 연속
# 이만큼의 스캔 프레임 동안 안정돼야 "완료"로 친다. raw busy↔idle 깜빡임(footer 가
# 한 프레임 안 잡혀 idle 로 보였다 다시 busy)에 done 이 잘못 서서 탭이 잠깐 녹색이
# 되는 것을 막는다. 30Hz flush 기준 ~0.1초 — 진짜 완료 알림 지연은 미미하다.
_DONE_IDLE_FRAMES = 3


class ServerClaudeMixin:
    # ---- 토큰 리밋 자동 재개 ----
    def _maybe_schedule_resume(self, pane: Pane, newtext: str):
        pane._scanbuf = (pane._scanbuf + newtext)[-4000:]
        if pane._resume_pending:
            return
        delay = parse_reset_delay(pane._scanbuf)
        if delay is not None:
            pane._resume_pending = True
            # 해제 시각 +5초 버퍼 후 재개 메시지 입력. 핸들을 들고 있다가 busy 복귀
            # 시 취소한다(M12 _cancel_resume — 사용자가 먼저 재개한 작업 보호).
            pane._resume_handle = self.loop.call_later(
                delay + 5, self._fire_resume, pane)

    def _cancel_resume(self, pane: Pane):
        """무장된 자동재개 예약을 취소한다(busy 복귀·세션 종료 시, M12). 발화직전
        재확인(#6)이 limit 이탈을 이미 막지만, 예약 자체를 일찍 거둬 헤더
        카운트다운도 즉시 사라지게 한다. 핸들이 없으면 무동작."""
        h = getattr(pane, "_resume_handle", None)
        if h is not None:
            h.cancel()
            pane._resume_handle = None
        pane._resume_pending = False

    def _fire_resume(self, pane: Pane):
        pane._resume_pending = False
        pane._resume_handle = None
        pane._scanbuf = ""
        # 발화 직전 재확인(#6): 화면이 **여전히 limit 상태**일 때만 주입한다. 예약과
        # 발화 사이(수 분~수 시간)에 사용자가 직접 재개했거나 화면이 busy/idle 로
        # 돌아갔다면, 'continue' 주입이 작업 중인 Claude 에 끼어들어 작업을 망친다.
        # parse_reset_delay 가 transcript 의 우연한 시각 숫자로 오탐했을 때도 막아 준다.
        if pane.pty is None:
            return
        if claude_state(screen_text(pane.screen)) != "limit":
            return
        # M12 예산 게이트: 게이트가 켜져 있고 예산을 초과했으면 자동재개를 보류한다
        # (또 한도까지 태우는 것을 막음). 사용자는 직접 continue 로 재개 가능.
        if self.token_budget_resume_gate and self._budget_over(pane):
            return
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

    def _reset_token_session(self, pane: Pane):
        """패널 토큰 누계를 **새 세션으로 끊는다**(/clear 컨텍스트 경계, #5). tokens
        상태를 0 으로 리셋하고 세션 id 를 새로 부여해, /clear 이후의 토큰이 비워진 새
        컨텍스트에 귀속되게 한다(usagelog 의 세션 단위 집계도 컨텍스트와 정합). None→
        Claude 첫 진입과 동일한 경계 처리지만, /clear 는 화면이 계속 Claude 라 그 경계
        검출(new_cl and not old_cl)에 안 걸려 여기서 명시적으로 끊는다."""
        tokens.reset(pane._tok_state)
        pane._session_tokens = 0
        self._claude_session_seq += 1
        pane._claude_session_id = self._claude_session_seq

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
            # /clear 로 Claude 컨텍스트가 비워지므로 토큰 누계도 **새 세션으로 끊는다**(#5).
            # 안 그러면 절감 자동화(doc→clear)가 돌수록 doc 작성·/clear 자체 토큰이 사용자
            # 프롬프트 누계에 계속 합산되고, 세션 id 가 실제 컨텍스트 경계와 어긋난다.
            self._reset_token_session(pane)
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
        else:
            # 켤 때: 이미 idle-settled(출력 없음)라 스캔 게이팅(B1)으로 스킵될 패널도
            # 다음 프레임에 한 번 재스캔해 auto 로 즉시 맞추도록 강제한다.
            for p in self._all_panes():
                p._scan_seq = -1
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
        p._scan_seq = -1   # 스캔 게이팅(B1) 무시하고 다음 프레임에 구동 시작

    def _scan_claude(self, sess, win) -> bool:
        """모든 탭 패널의 Claude 상태/사용량을 화면 텍스트(screen.display)로 갱신
        하고, **비활성 탭**의 busy→idle(작업 완료) 전이를 감지해 `has_claude_done`
        를 세운다(#22). 활성 윈도우만이 아니라 전체를 훑는 이유는 백그라운드 탭의
        완료를 알리기 위해서다. 상태가 바뀌면 True 반환."""
        changed = False
        for t in sess.tabs:
            w = t.window
            for p in w.panes():
                # dirty 게이팅(B1): 마지막 스캔 이후 출력이 없었으면(_feed_seq 불변)
                # 화면 텍스트가 그대로라 상태/사용량/전이가 바뀔 수 없다 — join+정규식
                # 스캔을 통째로 건너뛴다(idle·다중 패널에서 flush CPU 대폭 절감).
                # 단, **프레임 카운터로 도는 디바운스**는 출력 없는 프레임에도 진행돼야
                # 하므로(완료 알림 #22: idle 이 _DONE_IDLE_FRAMES 프레임 안정 / 헤더
                # 예약 해제: 비-Claude 가 _HDR_CLAUDE_MISS 프레임 지속), 그 전이가
                # 진행 중인 패널(pending)은 화면 불변이어도 계속 스캔한다. settled
                # (안정 idle·안정 비Claude) 패널만 건너뛴다.
                pending = ((p._was_busy and p._claude == "idle"
                            and p._idle_frames < _DONE_IDLE_FRAMES)
                           or (p._hdr_claude and not p._claude))
                if p._feed_seq == p._scan_seq and not pending:
                    continue
                p._scan_seq = p._feed_seq
                txt = screen_text(p.screen)
                old_cl = p._claude
                new_cl = claude_state(txt)
                # Claude 세션 피드백 프롬프트 자동 Dismiss(#26): "How is Claude doing
                # this session?" 가 뜨면 '0'(Dismiss) 키를 한 번 주입해 치운다. 같은
                # 화면에 반복 주입하지 않도록 사라질 때까지 디바운스한다.
                if claude_feedback_prompt(txt):
                    if not p._feedback_seen and p.pty is not None:
                        p._feedback_seen = True
                        try:
                            p.pty.write(b"0")
                        except OSError:
                            pass
                else:
                    p._feedback_seen = False
                # 사용량 표시는 Claude 세션이 살아 있는 동안 유지한다(#5): 화면에서
                # 토큰 문구가 잠시 사라져도(스크롤 등) 마지막 값을 보존하고, 세션이
                # 끝나면(claude None) 비운다.
                if new_cl:
                    u = claude_usage(txt)
                    new_use = u if u is not None else p._claude_usage
                else:
                    new_use = None
                if new_cl != p._claude or new_use != p._claude_usage:
                    p._claude = new_cl
                    p._claude_usage = new_use
                    changed = True
                # 헤더 예약(#1)용 디바운스: Claude 로 보이면 즉시 True, 아니면 연속
                # _HDR_CLAUDE_MISS 프레임 뒤에야 False. raw `_claude` 깜빡임이 헤더
                # 예약을 토글해 PTY 가 ±1 행 리사이즈를 반복(원격 화면 한 줄 떨림)
                # 하는 것을 막는다(_should_reserve_header 가 _hdr_claude 를 읽음).
                if new_cl:
                    p._hdr_claude = True
                    p._hdr_claude_miss = 0
                elif p._hdr_claude:
                    p._hdr_claude_miss += 1
                    if p._hdr_claude_miss >= _HDR_CLAUDE_MISS:
                        p._hdr_claude = False
                # 토큰 누계(#3): 새 Claude 세션 시작(None→Claude) 시 리셋, 매 프레임
                # 현재 응답 running 토큰을 step 으로 접어 응답별 peak 를 누계에 확정.
                # (확정 시점 committed>0 은 #7 의 영속 로깅 이벤트로도 쓰인다.)
                committed = 0
                if new_cl and not old_cl:
                    tokens.reset(p._tok_state)
                    # 새 Claude 세션 경계: 세션 id 부여, 계정 재감지(수동 지정은 유지).
                    self._claude_session_seq += 1
                    p._claude_session_id = self._claude_session_seq
                    if not p._claude_account_manual:
                        p._claude_account = None
                    # 시작 규칙 주입 예약(#27): 새 Claude 세션이 뜨면 다음 idle(입력
                    # 준비됨) 때 저장된 규칙을 프롬프트에 넣는다. 빈 규칙이면 안 함.
                    if self.claude_rules.strip():
                        p._rules_pending = True
                    p._ctx_fired = False   # 새 세션 — 잔량 자동정리 디바운스 해제(M11)
                if not new_cl:
                    p._rules_pending = False   # 세션 끝나면 예약 해제
                if new_cl:
                    # 계정 단서를 매 프레임 갱신(마지막 본 값 유지; 수동 지정 우선).
                    if not p._claude_account_manual:
                        acct = claude_account(txt)
                        if acct and acct != p._claude_account:
                            p._claude_account = acct
                    running = tokens.parse_running_tokens(txt)
                    committed = tokens.step(p._tok_state, running,
                                            new_cl == "busy")
                    if committed > 0:
                        self._log_tokens(sess, t, p, committed)
                    # 표시용 누계 = 확정 total + **진행 중 응답의 peak**(아직 미확정).
                    # 예전엔 total 만 써서, 스트리밍 중인 현재 응답 토큰이 빠져 Claude
                    # 표시보다 항상 적게 나왔다(#20). peak 는 확정 시 total 로 접히므로
                    # total+peak 는 경계에서 연속적이고 이중계산이 없다.
                    live = p._tok_state["total"] + p._tok_state["peak"]
                    if live != p._session_tokens:
                        p._session_tokens = live
                        changed = True
                elif p._session_tokens:
                    p._session_tokens = 0
                    p._tok_state["peak"] = 0
                    p._tok_state["total"] = 0
                    changed = True
                # M18-B: limit 진입 시점의 계정 누계로 5h 상한을 학습(설정 미지정 시
                # 5h 근접도 표시의 분모로 쓴다, §9.3 (b) 관측 학습).
                if new_cl == "limit":
                    tot = self._account_token_total(p)
                    if tot > self._learned_5h_cap:
                        self._learned_5h_cap = tot
                # 큐된 프롬프트 승격(#4): 헤더는 "지금 처리 중인 프롬프트"를 보여야
                # 한다. busy 중 입력한 프롬프트는 _track_prompt 가 pending_prompts 에
                # 쌓아 뒀다(last_prompt 즉시 안 바꿈). 응답 경계 — ① busy→non-busy(응답
                # 종료) 또는 ② 연속 busy 중 running 토큰 급감(committed>0 = 다음 응답
                # 시작) — 에서 큐의 다음 프롬프트를 last_prompt 로 승격한다.
                if p._claude is None:
                    if p.pending_prompts:
                        p.pending_prompts.clear()
                    # Claude 세션 종료 → 권한모드 관측/목표 비움(§10 item 2)
                    if p._perm_mode is not None or p._perm_target is not None:
                        p._perm_mode = None
                        p._perm_target = None
                else:
                    boundary = (old_cl == "busy" and new_cl != "busy")
                    if not boundary and committed > 0 and new_cl == "busy":
                        boundary = True
                    if boundary and p.pending_prompts:
                        p.last_prompt = p.pending_prompts.pop(0)
                        changed = True
                # 데스크탑 앱 원격제어 등 입력 경로(_track_prompt)를 안 거친 프롬프트
                # 반영(§10 #19): 화면 transcript 에서 최신 사용자 프롬프트를 best-effort
                # 추출해, 입력으로 안 잡힌(last_prompt 와 다르고 최근 히스토리에도 없는)
                # 경우에만 헤더/히스토리를 갱신한다. 로컬 입력은 _track_prompt 가 제출
                # 즉시 히스토리에 남기므로 여기 가드(히스토리 멤버십)에 걸려 중복되지
                # 않는다. 화면 파싱은 best-effort 라 보수적으로 매칭한다.
                if new_cl:
                    sp = claude_prompt(txt)
                    if (sp and sp != p.last_prompt
                            and sp not in p.prompt_history[-5:]):
                        p.last_prompt = sp
                        p.prompt_history.append(sp)
                        if len(p.prompt_history) > 200:
                            p.prompt_history = p.prompt_history[-200:]
                        changed = True
                # 비활성 탭에서 처리(busy)→대기(idle) 전이 = 작업 완료. limit 은
                # "대기"가 아니므로 대상 아님. 플리커 방지(§10 #18): raw busy→idle 즉시
                # 대신, busy 를 본 적이 있고(idle 진입) idle 이 _DONE_IDLE_FRAMES 프레임
                # 연속 안정될 때만 완료로 친다(한 프레임 깜빡임에 녹색 오검출 방지).
                if new_cl == "idle":
                    p._idle_frames += 1
                else:
                    p._idle_frames = 0
                    if new_cl == "busy":
                        p._was_busy = True   # 작업 중이었음 → 다음 안정 idle 이 '완료'
                if (w is not win and t.monitor_claude and not t.has_claude_done
                        and p._was_busy and new_cl == "idle"
                        and p._idle_frames >= _DONE_IDLE_FRAMES):
                    t.has_claude_done = True
                    p._was_busy = False
                    changed = True
                # M12: limit 이탈 시 무장된 자동재개 예약을 취소(사용자가 먼저 재개
                # 했거나 화면이 전환됨). _fire_resume 도 발화직전 limit 재확인으로
                # 막지만(#6), 예약을 일찍 거둬 헤더 카운트다운도 즉시 사라지게 한다.
                if new_cl != "limit" and p._resume_handle is not None:
                    self._cancel_resume(p)
                # 자동 doc→/clear(§10): idle 이탈(busy/limit/종료) 시 무장된 타이머를
                # 즉시 해제한다 — idle 이 끊기면 "N초 지속" 전제가 깨진다. 권한모드
                # 자동전환 시도 카운터도 idle 이탈 시 리셋(다음 idle 진입에 다시 시도).
                if new_cl != "idle":
                    self._adc_disarm(p)
                    p._cam_tries = 0
                    p._cam_last = None
                else:
                    # 시작 규칙 주입(#27): 새 세션/clear 후 첫 idle(입력 준비됨)에 한 번.
                    if p._rules_pending:
                        p._rules_pending = False
                        self._inject_rules(p)
                    # idle: 현재 권한모드를 관측해 저장(팝업 '현재 모드' 표시용 — status
                    # 로 클라에 전달, §10 item 2). footer 가 안 보이면(None) 마지막 값 유지.
                    pm = claude_perm_mode(txt)
                    if pm is not None and pm != p._perm_mode:
                        p._perm_mode = pm
                        changed = True
                    # 권한모드 구동: 사용자가 footer 클릭 팝업으로 고른 수동 목표
                    # (_perm_target)가 우선, 없고 claude_auto_mode 면 auto 로 순환
                    # (§10 item 2 + 권한모드 자동 오토모드 전환). 둘 다 shift+tab 폐루프.
                    if p._perm_target:
                        self._drive_perm_mode(p, txt, p._perm_target)
                    elif (self.claude_budget_plan
                          and self._budget_level_for(p) >= 80
                          and claude_perm_mode(txt) not in ("plan", "bypass")):
                        # M13: 예산 압박(≥80%) → plan 유도. bypass(명시적 위험)는
                        # 불간섭, 이미 plan 이면 무동작. claude_auto_mode 보다 우선.
                        self._drive_perm_mode(p, txt, "plan")
                    elif self.claude_auto_mode:
                        self._maybe_auto_mode(p, txt)
                    # M11 디바운스 해제: 정리 후 잔량이 임계+여유(5%p) 위로 회복하면
                    # 다음 저잔량 구간에 재발화할 수 있게 한다. 회복 전엔 재발화 금지
                    # (compact 가 효과 없어도 매 응답 무한 정리하지 않게 — §5.5).
                    if p._ctx_fired:
                        rec = claude_context_pct(txt)
                        if rec is not None and rec >= self.claude_ctx_threshold + 5:
                            p._ctx_fired = False
                # 프롬프트 단위 클리어 모드(#9) + 자동 doc→/clear(§10): busy→idle(응답
                # 완료) 경계에서 상태기계를 전진한다. 수동 모드(prompt_clear_mode)와
                # 자동 시퀀스(_adc_active)가 같은 _pc_phase 기계를 공유한다.
                # 진행 중이 아니면서 자동 모드가 켜져 있으면 idle 진입 시점에 무장만
                # 한다(실제 발화는 N초 뒤 _adc_fire).
                if old_cl == "busy" and new_cl == "idle":
                    if p.prompt_clear_mode or p._adc_active:
                        self._pc_advance(p)
                        if p._adc_active and p._pc_phase is None:
                            p._adc_active = False   # 자동 doc→clear 시퀀스 완료
                    else:
                        # M11 컨텍스트 잔량 자동 정리: 응답 완료 경계에서 잔량이 임계
                        # 밑이면 1회 정리(우선). 잔량 부족이 "idle N초 경과"(auto-doc-
                        # clear)보다 시급하므로 둘 다 켜져 있으면 잔량 정리를 먼저 한다.
                        # 완료 경계라 사용자가 타이핑 중이 아니고(응답이 막 끝남) 다음
                        # 비싼 턴 직전이라 정리에 가장 값싼 시점이다(§3 S1).
                        pct = (claude_context_pct(txt)
                               if self.claude_ctx_autoclear and not p._ctx_fired
                               else None)
                        if pct is not None and pct < self.claude_ctx_threshold:
                            # M14 빈도 상한: 직전 정리로부터 min_interval 초가 안
                            # 지났으면 이번 경계는 건너뛴다(_ctx_fired 를 안 세워 다음
                            # 완료 경계에 재평가 — 시간이 차면 발화). 잔량이 낮은 동안엔
                            # auto-doc-clear 무장도 하지 않는다(정리가 더 시급·우선).
                            if self._ctx_cap_ok(p):
                                p._ctx_fired = True
                                p._ctx_last_fire = time.monotonic()
                                self._ctx_intervene(p)
                        elif self.auto_doc_clear:
                            self._adc_arm(p)
        return changed

    @staticmethod
    def _tab_claude(tab) -> str | None:
        """탭 내 패널들의 Claude 상태를 합쳐 대표 상태 반환(limit>busy>idle)."""
        pri = {"limit": 3, "busy": 2, "idle": 1}
        best, score = None, 0
        for p in tab.window.panes():
            s = p._claude
            if s and pri[s] > score:
                best, score = s, pri[s]
        return best

    def _account_token_total(self, ap) -> int:
        """활성 패널의 Claude 계정을 키로, 그 계정에 속한 모든 패널(전체 세션 순회)
        의 세션 누적 토큰을 합산한다(§10 계정별 합계). 계정 추정 전이면 활성 패널
        단독 누계로 폴백하고, 활성 패널이 Claude 가 아니면 0 을 보낸다(이 경우
        클라이언트가 마지막 비어있지 않은 값을 유지해 표시가 사라지지 않게 한다)."""
        if not ap:
            return 0
        acct = ap._claude_account
        if acct:
            return sum(p._session_tokens for p in self._all_panes()
                       if p._claude_account == acct)
        if ap._claude:
            return ap._session_tokens
        return 0

    def _usage_text(self, p):
        """M18-A: 상태줄 컨텍스트 표시. Claude 가 점유%를 그리면 그대로(`ctx N% / 1M`),
        배지만 있으면(점유% 미상) 세션 누계/윈도우로 **근사 사용%**를 `~` 로 곁들인다.
        근사는 누계 기반이라 라이브 점유와 다르므로 윈도우 미만일 때만 보인다(§9.2)."""
        if p is None:
            return None
        u = p._claude_usage
        if not u:
            return None
        if u.endswith(" ctx") and "%" not in u:   # 배지-only("1M ctx")
            wt = ctx_window_tokens(u)
            st = p._session_tokens
            if wt and 0 < st < wt:
                return f"ctx ~{round(st / wt * 100)}% / {u[:-4]}"
        return u

    def _tok5h_pct(self, p, total):
        """M18-B: 5시간 한도 근접도 %(int)|None. 분모 = token_budget_5h(설정) 또는
        limit 관측 학습치(_learned_5h_cap). 분모 미상이면 None — % 를 지어내지 않는다
        (§5.5 미동작 편향). 분자는 표시 토큰(계정 누계)으로 §9.3 의 5h 근사."""
        if p is None or not p._claude or total <= 0:
            return None
        cap = self.token_budget_5h or self._learned_5h_cap
        if cap <= 0:
            return None
        return min(999, round(total / cap * 100))

    @staticmethod
    def _track_prompt(pane: Pane, data: bytes):
        """입력 바이트에서 현재 줄을 누적하고 Enter 시 last_prompt 로 확정한다.
        CSI/ESC 시퀀스는 건너뛰고(화살표 등), bracketed paste 본문은 포함한다."""
        text = data.decode("utf-8", "ignore")
        buf = pane._inbuf
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":                 # ESC: 제어 시퀀스 건너뜀
                i += 1
                if i < n and text[i] == "[":
                    i += 1
                    while i < n and not (0x40 <= ord(text[i]) <= 0x7e):
                        i += 1
                    i += 1
                else:
                    i += 1
                continue
            if ch in ("\r", "\n"):
                line = buf.strip()
                if line:
                    # 히스토리는 제출 즉시 기록(큐잉돼도 제출된 것은 맞다).
                    if not pane.prompt_history or pane.prompt_history[-1] != line:
                        pane.prompt_history.append(line)
                        if len(pane.prompt_history) > 200:
                            pane.prompt_history = pane.prompt_history[-200:]
                    # 헤더용 last_prompt(#4): 이전 프롬프트가 아직 처리중(busy)이면
                    # 즉시 덮지 말고 pending 큐에 쌓는다 — _scan_claude 가 응답 경계에
                    # 다음 프롬프트를 승격한다(헤더 = "지금 처리 중인 프롬프트").
                    # busy 가 아니면(idle/None/limit) 곧장 확정.
                    if pane._claude == "busy":
                        pane.pending_prompts.append(line)
                    else:
                        pane.last_prompt = line
                buf = ""
            elif ord(ch) in (8, 127):        # backspace
                buf = buf[:-1]
            elif ord(ch) >= 32:
                buf += ch
            i += 1
        pane._inbuf = buf[-500:]

    def set_claude_rules(self, text: str):
        self.claude_rules = text or ""
        self._save_opts()

    # ---- 토큰 절감 설정(docs/TOKEN_SAVING_SCENARIO.md) ----
    def set_claude_ctx_autoclear(self, value=None):
        """컨텍스트 잔량 기반 자동 정리(M11) 토글. value 미지정 시 반전. 끄면 무장된
        디바운스도 리셋한다(다음 켤 때 깨끗이 시작). opts.json 영속."""
        self.claude_ctx_autoclear = (not self.claude_ctx_autoclear) \
            if value is None else bool(value)
        if not self.claude_ctx_autoclear:
            for p in self._all_panes():
                p._ctx_fired = False
        else:
            # 켤 때: idle-settled(B1 스킵) 패널도 다음 프레임에 한 번 재스캔.
            for p in self._all_panes():
                p._scan_seq = -1
        self._save_opts()
        return self.claude_ctx_autoclear

    def set_claude_ctx_action(self, action: str):
        """정리 방식 설정: "compact"(/compact 요약) 또는 "doc-clear"(문서화→/clear).
        알 수 없는 값은 무시(현 값 유지). opts.json 영속."""
        a = (action or "").strip().lower()
        if a in ("compact", "doc-clear"):
            self.claude_ctx_action = a
            self._save_opts()
        return self.claude_ctx_action

    def set_claude_ctx_threshold(self, pct):
        """잔량 임계(%) 설정. 1~99 범위로 클램프. opts.json 영속."""
        try:
            v = int(pct)
        except (TypeError, ValueError):
            return self.claude_ctx_threshold
        self.claude_ctx_threshold = max(1, min(99, v))
        self._save_opts()
        return self.claude_ctx_threshold

    def set_claude_ctx_min_interval(self, secs):
        """정리 빈도 상한(초) 설정(M14). 0=상한 없음, 그 외 0~3600 클램프.
        opts.json 영속. 잘못된 값은 현 값 유지."""
        try:
            v = float(secs)
        except (TypeError, ValueError):
            return self.claude_ctx_min_interval
        self.claude_ctx_min_interval = max(0.0, min(3600.0, v))
        self._save_opts()
        return self.claude_ctx_min_interval

    def _pending_action(self, pane):
        """M14 카운트다운: 무장된 자동 액션(자동재개 예약 / auto-doc-clear 타이머)의
        종류와 남은 초(ETA)를 반환한다(없으면 None). 클라 상태줄 배지가 "곧 자동
        개입이 일어난다(입력 시 취소)"를 사용자에게 보이게 한다 — 비가역 동작의
        발견성·취소권 보장(§5.3·§5.4). 자동재개를 우선해 본다(둘 다 무장은 정상
        경로상 안 생기지만 안전하게 한쪽만). 발화 시각은 asyncio 타이머 핸들의
        when()(loop.time 기준)에서 얻는다 — 클램프해 음수/만료는 0 으로."""
        if pane is None or self.loop is None:
            return None
        h = getattr(pane, "_resume_handle", None)
        kind = "resume"
        if h is None:
            h = getattr(pane, "_adc_timer", None)
            kind = "doc-clear"
        if h is None:
            return None
        try:
            eta = max(0, int(round(h.when() - self.loop.time())))
        except (AttributeError, RuntimeError, TypeError):
            return None
        return {"kind": kind, "eta": eta}

    def _ctx_cap_ok(self, pane: Pane) -> bool:
        """M14 빈도 상한: 직전 자동 정리(_ctx_last_fire)로부터
        claude_ctx_min_interval 초가 지났으면 True(0=상한 없음 → 항상 True).
        _ctx_fired 디바운스(잔량 회복까지 1회)와 직교하는 **시간 바닥**으로, 정리가
        컨텍스트를 못 줄이는 오검출/병적 진동에서 매 완료경계 무한 정리를 막는다(§5.6)."""
        iv = self.claude_ctx_min_interval
        if iv <= 0:
            return True
        last = pane._ctx_last_fire
        return last is None or (time.monotonic() - last) >= iv

    def set_token_budget(self, day=None, session=None, h5=None):
        """일/세션/5시간 토큰 예산 설정(0=무제한). None 인 인자는 변경하지 않는다.
        예산을 바꾸면 경고 레벨을 즉시 재계산한다. opts.json 영속."""
        if day is not None:
            try:
                self.token_budget_day = max(0, int(day))
            except (TypeError, ValueError):
                pass
        if session is not None:
            try:
                self.token_budget_session = max(0, int(session))
            except (TypeError, ValueError):
                pass
        if h5 is not None:
            try:
                self.token_budget_5h = max(0, int(h5))
            except (TypeError, ValueError):
                pass
        self._refresh_budget_level()
        self._save_opts()
        return (self.token_budget_day, self.token_budget_session,
                self.token_budget_5h)

    def set_token_budget_resume_gate(self, value=None):
        """자동재개 예산 게이트(M12) 토글. value 미지정 시 반전. opts.json 영속."""
        self.token_budget_resume_gate = (not self.token_budget_resume_gate) \
            if value is None else bool(value)
        self._save_opts()
        return self.token_budget_resume_gate

    def set_claude_budget_plan(self, value=None):
        """예산 압박 시 plan 유도(M13) 토글. value 미지정 시 반전. 끄면 다음 프레임에
        한 번 재스캔(켤 때와 대칭)해 cam 카운터를 리셋. opts.json 영속."""
        self.claude_budget_plan = (not self.claude_budget_plan) \
            if value is None else bool(value)
        for p in self._all_panes():
            p._cam_tries = 0
            p._cam_last = None
            p._scan_seq = -1
        self._save_opts()
        return self.claude_budget_plan

    def _budget_over(self, pane: Pane) -> bool:
        """일 또는 세션 예산을 초과했는지(M10/M12). 예산이 0(무제한)이면 그 축은
        무시. 누계는 best-effort 라 하드 차단이 아니라 자동개입 보류 판단에만 쓴다."""
        if (self.token_budget_day > 0 and self._today_tokens is not None
                and self._today_tokens >= self.token_budget_day):
            return True
        if (self.token_budget_session > 0 and pane is not None
                and pane._session_tokens >= self.token_budget_session):
            return True
        return False

    def _budget_level_for(self, pane: Pane) -> int:
        """status 표시용 경고 레벨(0/80/100). 일 예산(_budget_level)과 활성 패널
        세션 예산 중 높은 쪽. 예산 미설정이면 0."""
        lvl = self._budget_level
        b = self.token_budget_session
        if b > 0 and pane is not None and pane._claude:
            used = pane._session_tokens
            if used >= b:
                lvl = max(lvl, 100)
            elif used >= b * 0.8:
                lvl = max(lvl, 80)
        return lvl

    def _ctx_intervene(self, pane: Pane):
        """컨텍스트 잔량 부족(M11): 설정된 방식으로 1회 정리한다. "compact" 는
        /compact 한 줄 주입(연속성 유지), "doc-clear" 는 기존 doc→/clear 상태기계를
        무장·시작(_adc 와 동일 경로 — 토큰 세션 리셋·시작 규칙 재주입까지 재사용).
        호출부(_scan_claude)가 idle·발화직전·디바운스·예산 게이트를 이미 확인했다."""
        if self.claude_ctx_action == "doc-clear":
            # 자동 doc→clear 시퀀스를 시작(idle 경계마다 _pc_advance 가 이어 감).
            if not pane._adc_active and not pane.prompt_clear_mode:
                pane._adc_active = True
                pane._pc_phase = None
                self._pc_advance(pane)
        else:   # "compact"
            self._pc_inject(pane, "/compact")
