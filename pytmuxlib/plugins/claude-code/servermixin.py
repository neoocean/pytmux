"""Claude 연동 서버 로직(토큰 리밋 자동재개·프롬프트 단위 클리어·auto-doc-clear·
권한모드 자동전환/구동)을 모은 믹스인 — **claude-code 플러그인** 소속.

이 디렉토리(plugins/claude-code/)를 통째로 지우면 `plugins.Registry.server_mixins()`
가 이 클래스를 더 못 내놓고, `server.Server` 의 동적 베이스에서 빠져 서버측 Claude
로직이 사라진다(delete-to-disable). 코어 `server.py` 는 이 모듈을 직접 import 하지
않고 오직 플러그인 레지스트리(`plugins.server_mixins()`)를 통해서만 합성한다.

무게: 이 모듈은 `pytmuxlib.model`/`tokens`/`claude` 등 **서버측** 코드를 import 하므로
플러그인 `__init__` 에서 module-top 으로 읽지 않고, `server_mixin()` 이 **지연 import**
한다(클라이언트도 plugins.load() 를 부르지만 서버 코드를 끌어오지 않게).

`ServerClaudeMixin` 의 메서드는 Server 인스턴스(self)에서만 동작하며 self.* 상태와
다른 Server 메서드(`_all_panes`/`_save_opts` 등)를 그대로 참조한다(동작 불변)."""
from __future__ import annotations

import asyncio
import json
import os
import time

import subprocess

from pytmuxlib import ipc, proc, pty_backend
from . import tokens, usagedb, usagelog   # S5 T5: 토큰 DB 백엔드도 플러그인 소속(물리 이전)
from .claude import (claude_account, claude_account_full, claude_api_error,
                     claude_awaiting_answer,
                     claude_context_hardstop, claude_context_pct,
                     claude_feedback_prompt, fmt_long_turn_badge,
                     fmt_unknown_update,
                     claude_model, model_overselect_hint,
                     claude_prompt, claude_perm_mode,
                     claude_remote_active, claude_remote_blocked,
                     claude_state, claude_usage,
                     claude_welcome, ctx_window_tokens, parse_inline_limit,
                     parse_reset_delay, parse_usage, screen_tail_key,
                     track_repeat)
from pytmuxlib.model import Pane, Session, Tab

# 권한모드 자동 오토모드 전환(§10): 한 번 idle 진입 후 auto 에 도달하지 못해도 이
# 횟수까지만 shift+tab 을 보낸다(footer 순환 순서가 고정이 아닐 수 있어 폐루프지만,
# 오검출 시 무한 순환을 막는 가드). cycle default→accept→plan→auto(4모드)를 다 돌아
# auto 유무를 확인하고(미존재면 acceptEdits 폴백) 정착할 때까지 덮을 만큼 넉넉히.
_CAM_MAX = 8


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

# §3.7 포맷 미인식 가시화: Claude 가 실행 중(fg 명령에 'claude')인데 화면 파서가
# 상태를 못 읽는 상태가 _FMT_UNKNOWN_SEC 초 지속되면 "포맷 미인식" 경고를 세운다.
# fg 검사(ps)는 비싸므로 인식 실패 패널에 한해 _FMT_CHECK_INTERVAL 초 간격으로만 한다.
_FMT_CHECK_INTERVAL = 5.0
_FMT_UNKNOWN_SEC = 20.0
# 경고 문구(상태줄 경고 세그먼트로 표시 — _claude_warn 재사용). 아이콘은 각 warn 문자열이
# 직접 갖는다(렌더는 비부가) — 장기턴·반복·미인식 모두 ⚠(노란 세모로 통일).
_FMT_UNKNOWN_MSG = "⚠ Claude 포맷 미인식 — 추적 중단(버전 업데이트?)"


# 비활성 탭 Claude 완료 알림(#22) 플리커 방지(§10 #18): busy→idle 후 idle 이 연속
# 이만큼의 스캔 프레임 동안 안정돼야 "완료"로 친다. raw busy↔idle 깜빡임(footer 가
# 한 프레임 안 잡혀 idle 로 보였다 다시 busy)에 done 이 잘못 서서 탭이 잠깐 녹색이
# 되는 것을 막는다. 30Hz flush 기준 ~0.1초 — 진짜 완료 알림 지연은 미미하다.
_DONE_IDLE_FRAMES = 3
# auto-launch `/rc` 발화 디바운스(버그 수정 요청 2026-06-12): 새 Claude 세션의 **첫
# idle 한 프레임**만 보고 `/rc` 를 쏘면, 데스크탑 앱이 원격제어를 **이미 켜 둔** 세션에
# `/remote-control` 을 한 번 더 보내게 돼 Claude 가 "응답 대기" 대화로 멈춘다(사용자
# 보고: "remote 이미 활성화돼 있는데도 /rc 입력하고 응답 대기로 진행 정지"). 원인은
# 타이밍 — 데스크탑 앱의 'Remote Control active' 오버레이는 새 세션이 붙은 뒤 한두
# 프레임 늦게 footer 에 그려져, 첫 idle 프레임엔 화면에 없어 가드(`claude_remote_active`/
# `_rc_done`)를 못 세운다. idle 이 이만큼 프레임 **연속 안정**될 때까지 기다리면 그 사이
# 도착한 오버레이 출력이 `_rc_done`(원격 ON 관측, 위 `claude_remote_active` 분기)을 세워
# `/rc` 를 건너뛰게 된다. 원격이 정말 꺼진 새 세션이면 이만큼 지나도 안 떠 정상적으로
# `/rc` 를 1회 쏜다. 30Hz flush 기준 ~1초 — 원격제어가 켜지는 지연은 무시할 만하다.
_RC_CONFIRM_FRAMES = 30
# 세션 피드백 프롬프트 자동 Dismiss(#26): 배너를 감지하면 **즉시** Esc 를 1회 쏜다
# (공통 경로는 이 한 번으로 닫혀 곧바로 사라진다 — "최대한 빨리").
# Dismiss 키는 Esc(\x1b). 이 피드백 배너는 입력 컴포저 **위**에 비모달로 떠 있어
# 컴포저가 계속 포커스를 갖는다 — 예전처럼 '0'(0x30)을 쏘면 Dismiss 되지 않고 그대로
# 컴포저에 찍혀, 지워지지 않는 "00"이 프롬프트에 박히고 다음 입력에 딸려 들어간다
# (사용자 보고). Claude Code 의 오버레이는 Space/Enter/Esc 로만 닫히는데, Space 는
# 컴포저에 공백을, Enter 는 컴포저 제출을 유발할 수 있어 인쇄 불가·부작용 최소인 Esc 만
# 쓴다(닫지 못해도 컴포저를 오염시키지 않음).
#
# ★ 이중 Esc=되감기(rewind) 팝업 방지: 첫 키가 레이스(프롬프트 입력 핸들러가 아직
# 안 떴을 때)로 누락될 수 있어 재시도가 필요하지만, **블라인드 프레임 타이머**로
# 재주입하면 안 된다. Esc#1 이 배너를 이미 닫았어도 pyte feed 지연(피드 병목) 때문에
# 화면 텍스트가 한동안 계속 배너 정규식에 매칭돼, 닫힌 뒤 Esc#2 가 나가고 Claude Code
# 에서 **이중 Esc = 되감기 팝업**이 뜬다(사용자 보고 "엉뚱한 팝업"). 그래서 재주입은
# 우리 Esc 이후 단말이 **실제로 다시 그려졌을 때만**(`_feed_seq` 진행) 허용한다 —
# 그 시점의 화면이 post-Esc 상태이므로, 여전히 배너가 보이면 첫 키가 진짜 누락된 것이고
# stale 화면 위 이중 Esc 가 아니다. 추가로 _FEEDBACK_GAP 최소 간격을 둬 같은 리드로
# 버스트 안에서 Esc#1 이 처리되기도 전에 Esc#2 가 나가는 인-버스트 레이스도 막는다.
_FEEDBACK_DISMISS_KEY = b"\x1b"
_FEEDBACK_GAP = 6        # 재주입 최소 프레임 간격(30Hz 기준 ~0.2초) — feed 진행 게이트와 AND
_FEEDBACK_MAX_TRIES = 3
# M17(T7) 경고 임계는 opt(server.py: claude_long_turn_sec 기본 600 / claude_repeat_alert
# 기본 3, 0=끔). 스캔의 warn 블록이 self.* 를 읽는다.


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
        # S6 T4 실측 한도 게이트(기본 ON, usage_gate_session_pct=95): /usage 실측
        # 세션/주간 % 가 임계 이상이면 자동재개 보류 — 한도 직전에 자동재개로 더
        # 태우는 것을 막는다. 실측 부재·stale·계정 불일치면 fail-open(개입 안 함).
        # 스크랩 추정이 아니라 **실측**이 판단 기준(시나리오 §0-4 — 가장 급한 전환).
        if self._usage_gate_over(pane):
            return
        try:
            pane.pty.write((pane.resume_msg + "\r").encode("utf-8"))
        except OSError:
            pass

    # ---- 전송 에러(API error/rate limit/overloaded) 자동 재시도(요청 2026-06-12,
    # 지속화 2026-06-15) ----
    # 사용량 5h 리밋(autoresume, reset 시각 대기)과 별개로, 전송 에러로 멈추면 "계속" 을
    # 주입해 이어가게 한다. 처음 두 번은 빠르게(1·2분) 재시도해 일시적 blip 을 즉시 털고,
    # 이후엔 **5분 케이던스로 무기한** 반복한다 — 진행 중이던 작업이 지속 outage(529
    # overloaded 등)에도 영영 멈춰 있지 않게(사용자 요청 2026-06-15: "5분에 한 번씩
    # 재시작"). 예전엔 5회에서 단념했으나(#9 H3, rate-limited 계정 부담 우려) 그 상한이
    # 곧 "작업이 계속 멈춰 있는" 원인이었다 — 5분 간격이면 주입 빈도가 낮아 부담이 미미
    # 하므로 상한을 없앴다. 토글 claude_auto_retry(기본 ON)로 끌 수 있다.
    _RETRY_DELAYS = (60.0, 120.0, 300.0)  # 1·2차 빠른 재시도 → 3차+ 5분 정상 케이던스(무기한)
    _RETRY_MSG = "계속"      # 재시도 프롬프트(요청)

    def _maybe_schedule_retry(self, pane: Pane):
        """전송 에러가 관측됐을 때(호출부가 보장) "계속" 주입을 1회 예약한다(이미 예약
        중이면 유지 — 디바운스). 토글이 꺼졌으면 무동작. 간격은 시도 횟수에 따라 백오프
        (60→120→이후 300초 고정)하며, 5분 케이던스로 **무기한** 반복해 지속 outage 에도
        작업이 멈춰 있지 않게 한다(요청 2026-06-15). 에러 해소 시 호출부가 _retry_attempts
        를 0 으로 리셋해 다음 새 에러는 다시 1분(1차)부터 빠르게 시작한다."""
        if not getattr(self, "claude_auto_retry", True) or pane._retry_pending:
            return
        n = getattr(pane, "_retry_attempts", 0)
        pane._retry_pending = True
        delay = self._RETRY_DELAYS[min(n, len(self._RETRY_DELAYS) - 1)]
        pane._retry_handle = self.loop.call_later(delay, self._fire_retry, pane)

    def _cancel_retry(self, pane: Pane):
        """무장된 재시도 예약을 취소한다(에러 해소·busy 복귀·세션 종료 시)."""
        h = getattr(pane, "_retry_handle", None)
        if h is not None:
            h.cancel()
            pane._retry_handle = None
        pane._retry_pending = False

    def _fire_retry(self, pane: Pane):
        """백오프 만료: 화면이 **여전히** 전송 에러로 멈춰 있으면 "계속"+Enter 를
        주입한다. 그새 Claude 가 스스로 재시도해 busy/idle 로 돌아갔거나 화면이 바뀌었으면
        (에러 해소) 주입하지 않는다(작업 중 끼어들기 방지 — autoresume _fire_resume 와
        같은 발화직전 재확인). 주입에 성공하면 _retry_attempts 를 올려 백오프를 전진한다
        (상한 없음 — 3차부터 5분 케이던스로 무기한 반복)."""
        pane._retry_pending = False
        pane._retry_handle = None
        if pane.pty is None:
            return
        # 발화직전 재확인: 에러가 사라졌으면(busy/idle 복귀) 주입 안 함. 추가로 Claude 가
        # **이미 busy**(스스로 재시도 중 — 전사에 에러 줄이 남아 있어도)면 작업에 끼어들지
        # 않는다(#9 — 산문/잔상 에러로 working Claude 를 방해하지 않게).
        text = screen_text(pane.screen)
        if not claude_api_error(text) or claude_state(text) == "busy":
            return
        try:
            pane.pty.write((self._RETRY_MSG + "\r").encode("utf-8"))
            pane._retry_attempts = getattr(pane, "_retry_attempts", 0) + 1
        except OSError:
            pass

    def set_claude_auto_retry(self, value=None):
        """전송 에러 자동 재시도 토글(요청). 끄면 무장된 모든 패널 예약을 해제. opts 영속."""
        self.claude_auto_retry = (not self.claude_auto_retry) \
            if value is None else bool(value)
        if not self.claude_auto_retry:
            for p in self._all_panes():
                self._cancel_retry(p)
        self._save_opts()
        return self.claude_auto_retry

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
        self._next_claude_session_id(pane)

    def _seed_session_seq(self):
        """첫 Claude 세션 부여 직전 1회, 세션 일련번호를 영속 DB 의 max(session) 으로
        시드한다(§3.5②). 서버 부팅마다 `_claude_session_seq` 가 0 으로 초기화돼(코어
        server.py) 재시작 후 새 세션이 1,2,… 로 재발급되면 DB 의 같은 id 옛 세션과
        [패널] 세션 차원 집계에서 병합됐다. DB 가 없거나(연결 실패) 비어 있으면 0
        시드(기존 동작). 한 번만 DB 를 읽고(_session_seq_seeded) 이후엔 메모리 카운터만
        증가한다 — 같은 부팅 내 세션 id 는 단조 증가가 보장된다."""
        if getattr(self, "_session_seq_seeded", False):
            return
        self._session_seq_seeded = True
        conn = self._tokens_db_conn()
        if conn is not None:
            try:
                self._claude_session_seq = max(self._claude_session_seq,
                                               usagedb.max_session(conn))
            except Exception:
                pass

    def _next_claude_session_id(self, pane: Pane):
        """새 Claude 세션 일련번호를 패널에 부여(시드 보장 후 +1). 두 경계가 공유한다:
        None→Claude 신규 진입과 수동/auto `/clear`(_reset_token_session)."""
        self._seed_session_seq()
        self._claude_session_seq += 1
        pane._claude_session_id = self._claude_session_seq

    def _fg_is_claude(self, pane) -> bool:
        """패널 포그라운드 프로세스의 **전체 명령행**에 'claude' 가 있으면 True(§3.7
        앵커). comm 만으론 Claude Code(node CLI)를 'node'와 구분 못 하므로 명령행을
        본다(예: 'node …/claude/cli.js' · 'claude'). 화면 파서와 무관한 ground-truth
        라, 포맷이 바뀌어 claude_state 가 None 이어도 Claude 실행 여부를 독립 확인한다.
        실패·미상·다른 node 프로세스는 False(보수적 — 경고는 확실할 때만)."""
        if pty_backend.IS_WINDOWS:
            # Windows(ConPTY): fg pgrp 개념이 없어 자손 트리 comm 만 best-effort.
            cmd = proc.foreground_command(getattr(pane, "child_pid", -1)) or ""
            return "claude" in cmd.lower()
        fd = getattr(pane, "master_fd", -1)
        try:
            pgid = os.tcgetpgrp(fd)
        except OSError:
            return False
        try:
            out = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pgid)], capture_output=True,
                text=True, timeout=1, **proc.no_window_kwargs()).stdout
        except (subprocess.SubprocessError, OSError):
            # 프로세스 소멸·ps 부재·타임아웃 = 기대 가능한 실패 → False 폴백.
            return False
        return "claude" in out.lower()

    def _update_fmt_unknown(self, pane, recognized: bool) -> bool:
        """§3.7: Claude 실행 중인데 화면 파서가 상태를 못 읽는 상태가 지속되면 패널에
        '포맷 미인식' 플래그(_fmt_unknown)를 세운다. 포맷이 바뀌어 토큰 추적·자동화가
        조용히 멈추는 것을 상태줄 ⚠ 로 가시화한다. 변경되면 True 반환(상태 재전송).

        fg 검사(ps)는 비싸므로 **인식 실패 패널에 한해** _FMT_CHECK_INTERVAL 초 간격
        throttle 한다. recognized=True(파서 인식)면 throttle 무시하고 즉시 해제.

        한계: 처음부터 인식 안 되는 **정적 idle** 패널은 출력이 없어 스캔이 건너뛰어져
        (_scan_claude dirty 게이트) 이 경로를 안 탄다 — 추적이 실제로 멈춰 손해가 큰
        쪽은 **출력이 계속 도는 busy** 구간이고 그건 매 프레임 스캔되므로 잡힌다."""
        now = time.monotonic()
        if recognized:
            # 파서가 다시 상태를 인식 → 의심 즉시 해제(throttle 무시).
            pane._fmt_check_mono = 0.0
            if pane._fmt_unknown or pane._fmt_first_mono is not None:
                pane._fmt_first_mono = None
                pane._fmt_logged = False
                was = pane._fmt_unknown
                pane._fmt_unknown = False
                return was
            return False
        if now < pane._fmt_check_mono:
            return False                       # throttle 창 — 상태 유지
        pane._fmt_check_mono = now + _FMT_CHECK_INTERVAL
        first, unknown = fmt_unknown_update(
            pane._fmt_first_mono, False, self._fg_is_claude(pane), now,
            _FMT_UNKNOWN_SEC)
        pane._fmt_first_mono = first
        if unknown and not pane._fmt_logged:
            pane._fmt_logged = True
            self._log_error("claude_format_unrecognized")   # 1회만(스팸 가드)
        elif not unknown:
            pane._fmt_logged = False
        if unknown != pane._fmt_unknown:
            pane._fmt_unknown = unknown
            return True
        return False

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
            self._auto_cc_mark(pane)   # 직전 clear 직후 쿨다운 시작(연속 정리 방지)
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

    def _auto_cc_mark(self, pane: Pane) -> None:
        """시간기반 자동 compact·doc-clear 쿨다운을 새로 시작한다 — 새 세션 시작·직전
        compact·직전 clear 직후 호출. 이후 auto_cc_cooldown_sec 초 동안 _acpt_arm/
        _adc_arm 이 무장을 건너뛴다(요청). 0(비활성)이면 아무것도 하지 않는다."""
        if self.auto_cc_cooldown_sec > 0:
            pane._auto_cc_cooldown_until = time.monotonic() + self.auto_cc_cooldown_sec

    def _auto_cc_blocked(self, pane: Pane) -> bool:
        """아직 쿨다운 중이면(새 세션·직전 정리 직후) True — 시간기반 자동 발화 보류."""
        return (self.auto_cc_cooldown_sec > 0
                and time.monotonic() < pane._auto_cc_cooldown_until)

    def _adc_arm(self, pane: Pane):
        """idle 진입 시점에 무장: auto_doc_clear_delay 초 뒤 _adc_fire 를 예약한다.
        기존 타이머가 있으면 먼저 해제(재무장). 실행 중인 이벤트 루프가 없으면
        (테스트가 _scan_claude 를 동기 호출하는 등) 조용히 패스한다 — 타이머 기반
        자동 발화만 비활성일 뿐 다른 동작에는 영향이 없다.

        새 세션/직전 정리 쿨다운(_auto_cc_blocked) 중이면 무장하지 않는다(요청)."""
        self._adc_disarm(pane)
        if self._auto_cc_blocked(pane):
            return
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

    # ---- 자동 /compact(요청): idle 지속 N초 후 1회 '/compact'+Enter 주입 ----
    # auto-doc-clear(문서화→/clear)의 단순 자매: 문서화 없이 /compact 한 줄만 넣어
    # 컨텍스트를 압축한다. 둘은 같은 idle 경계에서 무장하므로 상호배타(arming elif).
    def set_auto_compact(self, value=None):
        """Claude idle 지속 시 자동 /compact 모드 토글. value 미지정 시 반전.
        끄면 무장된 모든 패널 타이머를 해제한다. opts.json 영속."""
        self.auto_compact = (not self.auto_compact) if value is None \
            else bool(value)
        if not self.auto_compact:
            for p in self._all_panes():
                self._acpt_disarm(p)
        self._save_opts()
        return self.auto_compact

    def set_auto_hardstop(self, value=None):
        """Claude **컨텍스트 하드스톱** 자동복구 토글. value 미지정 시 반전. opts.json
        영속. 켜져 있으면 _scan_claude 가 "Context limit reached · /compact or /clear
        to continue" 화면을 보는 즉시(idle 지연 없이) /compact 를 1회 주입한다 —
        idle-기반 auto_compact 와 다른 트리거. 기본 ON: 하드스톱은 정상 idle 이 아니라
        완전 차단 상태이고 /compact 가 유일한 진행 수단이라 자동복구의 부작용이 없다."""
        self.auto_hardstop = (not self.auto_hardstop) if value is None \
            else bool(value)
        self._save_opts()
        return self.auto_hardstop

    def _acpt_disarm(self, pane: Pane):
        """무장된 자동 /compact 타이머를 해제한다(사용자 입력·재busy·세션 종료·토글
        off 시). 핸들이 없으면 무동작."""
        t = getattr(pane, "_acpt_timer", None)
        if t is not None:
            t.cancel()
            pane._acpt_timer = None

    def _acpt_arm(self, pane: Pane):
        """idle 진입 시점에 무장: auto_compact_delay 초 뒤 _acpt_fire 를 예약한다.
        기존 타이머가 있으면 먼저 해제(재무장). 실행 중 루프가 없으면(테스트가
        _scan_claude 를 동기 호출) 조용히 패스한다(_adc_arm 와 동일).

        이미 1회 발화했으면(_acpt_fired) 무장하지 않는다 — /compact 주입 자체가
        busy→idle 경계를 또 만들어 연속 재발화('Not enough messages to compact'
        반복)하는 것을 막는다(요청). 사용자가 실제 입력하면 플래그가 풀려 재무장된다.
        새 세션/직전 정리 쿨다운(_auto_cc_blocked) 중이어도 무장하지 않는다(요청)."""
        self._acpt_disarm(pane)
        if pane._acpt_fired or self._auto_cc_blocked(pane):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        pane._acpt_timer = loop.call_later(
            self.auto_compact_delay, self._acpt_fire, pane)

    def _acpt_fire(self, pane: Pane):
        """타이머 만료(idle 가 N초 지속됨): 조건 재확인 후 '/compact'+Enter 1회 주입.
        idle 가 아니거나(그새 busy/limit/종료), 자동 doc→clear/수동 prompt-clear 시퀀스가
        진행 중이거나, 토글이 꺼졌으면 발화하지 않는다. 또 화면이 질문/선택으로 끝나
        사용자 답을 기다리는 중이면(claude_awaiting_answer) 건너뛴다 — 답 직전 압축은
        상호작용을 끊고 무의미하다(요청). 이 경우 _acpt_fired 를 세우지 않아 사용자가
        답해 다음 idle 경계가 오면 다시 평가된다."""
        pane._acpt_timer = None
        if not self.auto_compact:
            return
        if pane._claude != "idle":
            return
        if pane._adc_active or pane.prompt_clear_mode:
            return
        if claude_awaiting_answer(screen_text(pane.screen)):
            return   # 질문으로 끝남 — 사용자 답 대기 중이므로 발화하지 않음
        pane._acpt_fired = True   # 디바운스: 다음 사용자 입력 전까지 재발화 금지
        self._auto_cc_mark(pane)  # 직전 compact 직후 쿨다운 시작(연속 정리 방지)
        self._pc_inject(pane, "/compact")   # 한 줄 입력 + Enter

    # ---- 권한모드 자동 오토모드 전환(§10) ----
    def set_claude_auto_mode(self, value=None):
        """Claude idle 시 권한모드를 auto 로 자동 맞추는 모드 토글. value 미지정 시
        반전. 끄면 모든 패널의 시도 카운터를 리셋한다. opts.json 영속."""
        self.claude_auto_mode = (not self.claude_auto_mode) if value is None \
            else bool(value)
        if not self.claude_auto_mode:
            for p in self._all_panes():
                self._perm_reset(p)
        else:
            # 켤 때: 이미 idle-settled(출력 없음)라 스캔 게이팅(B1)으로 스킵될 패널도
            # 다음 프레임에 한 번 재스캔해 auto 로 즉시 맞추도록 강제한다.
            for p in self._all_panes():
                p._scan_seq = -1
        self._save_opts()
        return self.claude_auto_mode

    def set_claude_auto_launch(self, value=None):
        """새 Claude 세션 시작 시 /rc(원격 제어 켜기)+권한모드 auto 를 1회 자동 적용
        하는 모드 토글. value 미지정 시 반전. opts.json 영속. 끌 때 진행 중인 1회
        예약(_rc_pending/_perm_auto_pending)도 거둔다(다음 세션부터 적용/미적용)."""
        self.claude_auto_launch = (not self.claude_auto_launch) if value is None \
            else bool(value)
        if not self.claude_auto_launch:
            for p in self._all_panes():
                p._rc_pending = False
                p._perm_auto_pending = False
        self._save_opts()
        return self.claude_auto_launch

    def _inject_keys(self, pane: Pane, data: bytes):
        """패널 PTY 로 raw 키 바이트를 보낸다(_pc_inject 와 달리 Enter 를 안 붙임).
        권한모드 순환(shift+tab=\\x1b[Z) 등 제어키 주입용."""
        if pane.pty is None:
            return
        try:
            pane.pty.write(data)
        except OSError:
            pass

    def _perm_reset(self, pane: Pane) -> None:
        """권한모드 폐루프 상태(시도 수·직전 모드·이번 드라이브에서 본 모드 집합)를 리셋."""
        pane._cam_tries = 0
        pane._cam_last = None
        pane._cam_seen = set()

    def _perm_step(self, pane: Pane, txt: str, target: str) -> bool:
        """idle 패널의 권한모드를 target 으로 한 단계 폐루프 구동한다(shift+tab=\\x1b[Z
        1회). Claude 의 순환 순서가 버전 의존이라 footer 를 매 프레임 재확인하며 도는
        폐루프다. 반환 True = 드라이브 종료(도달 또는 포기), False = 진행 중.

        핵심(사용자 보고 대응): target=="auto" 는 **진짜 auto 모드**("auto mode on")가
        목표다 — acceptEdits("accept edits on")는 다른 모드라 거기서 멈추지 않고 계속
        순환한다(cycle: default→accept→plan→auto). 단 auto 는 계정이 opt-in 돼야 cycle
        에 나타나므로, **한 바퀴를 다 돌았는데(이미 본 모드를 재방문) auto 를 못 만나면**
        acceptEdits 로 폴백한다 — auto 없는 계정이 plan/default 같은 엉뚱한 모드에 멈추지
        않게(가장 자동적이되 안전한 모드로 정착). bypass 는 명시적 위험 모드라 자동
        구동이 건드리지 않는다(이미 bypass 면 종료)."""
        mode = claude_perm_mode(txt)
        if mode is None:
            return False                 # footer 안 보임 — 대기
        if mode == "bypass" and target != "bypass":
            self._perm_reset(pane)       # 위험 모드 — 자동 구동은 손대지 않음
            return True
        eff = target
        if target == "auto" and mode != "auto" and mode in pane._cam_seen:
            eff = "accept"               # 한 바퀴 돌았는데 auto 없음 → acceptEdits 폴백
        if mode == eff:
            self._perm_reset(pane)       # 도달
            return True
        if mode == pane._cam_last and pane._cam_tries > 0:
            return False                 # 화면 미갱신 — 대기
        if pane._cam_tries >= _CAM_MAX:
            # 못 맞춤 — 포기. _cam_tries 를 **높게 유지**해(리셋 안 함) idle-exit 전까지
            # 다시 안 쏜다(오검출에 shift+tab 무한 스팸 방지). 새 드라이브는 _perm_reset
            # (idle-exit·set_claude_perm_mode·perm_auto)에서만 시작된다.
            return True
        pane._cam_tries += 1
        pane._cam_last = mode
        pane._cam_seen.add(mode)
        self._inject_keys(pane, b"\x1b[Z")
        return False

    def _maybe_auto_mode(self, pane: Pane, txt: str):
        """claude_auto_mode(상시 강제): idle 패널을 진짜 auto 모드로 폐루프 구동한다
        (auto 미존재 계정은 acceptEdits 폴백). _perm_step 가 도달/포기/대기를 판정."""
        self._perm_step(pane, txt, "auto")

    def _drive_perm_mode(self, pane: Pane, txt: str, target: str):
        """사용자가 고른 target(footer 클릭 팝업, §10 item 2) 또는 auto-launch/예산
        plan 유도가 건 _perm_target 까지 폐루프 구동. 도달/포기 시 _perm_target 해제."""
        if self._perm_step(pane, txt, target):
            pane._perm_target = None

    def set_claude_perm_mode(self, sess: Session, target: str, pane_id=None):
        """활성(또는 지정) 패널의 권한모드 목표를 설정한다(footer 클릭 팝업, §10 item 2).
        _scan_claude 가 idle 시 _drive_perm_mode 로 그 모드까지 폐루프 주입한다. accept
        (acceptEdits)·auto(진짜 auto)는 별개 모드다. bypass 는 명시적 위험 모드라 목록엔
        없지만 목표로는 허용한다(완결성)."""
        win = sess.active_window
        if not win:
            return
        p = (win.pane_by_id(pane_id) if pane_id is not None else None) \
            or win.active_pane
        if not p or target not in ("auto", "accept", "plan", "default", "bypass"):
            return
        p._perm_target = target
        self._perm_reset(p)
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
                           or (p._hdr_claude and not p._claude)
                           # 피드백 자동 Dismiss 재시도 중: 화면이 정적이어도
                           # GAP 프레임마다 Esc 를 다시 쏘려면 계속 스캔해야 한다(#26).
                           or p._feedback_active
                           # auto `/rc` 디바운스 중(_RC_CONFIRM_FRAMES): 정적 idle
                           # 화면이어도 _idle_frames 를 임계까지 진행시켜 발화하거나,
                           # 그 사이 도착한 'Remote Control active' 오버레이를 관측해
                           # 스킵해야 하므로 계속 스캔한다(첫 프레임 즉발 → 응답 대기
                           # 대화 멈춤 버그 수정).
                           or (p._rc_pending and p._claude == "idle"
                               and p._idle_frames < _RC_CONFIRM_FRAMES)
                           # §3.4 busy 이탈 확정 대기 중: 화면이 정적이어도 다음
                           # 스캔이 이탈을 확정(또는 busy 복귀)할 수 있게 계속 스캔.
                           or p._busy_exit_miss > 0)
                if p._feed_seq == p._scan_seq and not pending:
                    continue
                p._scan_seq = p._feed_seq
                txt = screen_text(p.screen)
                old_cl = p._claude
                new_cl = claude_state(txt)
                # §3.7: 파서가 상태를 못 읽는데 Claude 가 실제 실행 중이면(throttle 된
                # fg 검사) '포맷 미인식' 경고를 세워 추적 중단을 가시화한다.
                if self._update_fmt_unknown(p, new_cl is not None):
                    changed = True
                # Claude 세션 피드백 프롬프트 자동 Dismiss(#26): "How is Claude doing
                # this session?" 가 뜨면 Esc(_FEEDBACK_DISMISS_KEY)를 한 번 주입해
                # 치운다. 같은 화면에 반복 주입하지 않도록 사라질 때까지 디바운스한다.
                if claude_feedback_prompt(txt):
                    if p._feedback_tries == 0:
                        # 첫 감지 → **즉시** Esc 1회. 공통 경로는 여기서 닫힌다.
                        p._feedback_active = True   # 정적 화면에도 스캔 유지(재시도)
                        p._feedback_tries = 1
                        p._feedback_wait = _FEEDBACK_GAP
                        p._feedback_seq = p._feed_seq
                        if p.pty is not None:
                            try:
                                p.pty.write(_FEEDBACK_DISMISS_KEY)
                            except OSError:
                                pass
                    elif p._feedback_tries >= _FEEDBACK_MAX_TRIES:
                        # 충분히 시도함 → 포기(스캔 강제 해제, 스팸/무한 스캔 방지).
                        p._feedback_active = False
                    else:
                        # 재주입은 **우리 Esc 이후 화면이 다시 그려졌고**(feed 진행)
                        # 최소 간격도 지났는데 아직 배너가 있을 때만. 두 조건이
                        # stale 화면 위 이중 Esc(=되감기 팝업)를 막는다.
                        p._feedback_active = True
                        if p._feedback_wait > 0:
                            p._feedback_wait -= 1
                        if (p._feedback_wait <= 0
                                and p._feed_seq != p._feedback_seq
                                and p.pty is not None):
                            p._feedback_tries += 1
                            p._feedback_wait = _FEEDBACK_GAP
                            p._feedback_seq = p._feed_seq
                            try:
                                p.pty.write(_FEEDBACK_DISMISS_KEY)
                            except OSError:
                                pass
                else:
                    p._feedback_active = False
                    p._feedback_tries = 0
                    p._feedback_wait = 0
                    p._feedback_seq = 0
                # 컨텍스트 하드스톱 자동복구(요청): 화면이 "Context limit reached ·
                # /compact or /clear to continue" 면 컨텍스트가 꽉 차 Claude 가 완전히
                # 멈춘 것 — idle-기반 auto_compact(N초 지속 후 발화)와 **다른 트리거**로,
                # 시간이 지나도 안 풀리므로 **즉시** /compact 를 1회 주입해 진행을 잇는다.
                # 같은 하드스톱 화면에 반복 주입하지 않게 _hardstop_fired 로 디바운스하고
                # (화면이 하드스톱을 벗어나면 해제 → 다음 하드스톱에 재발화), 직전 정리
                # 직후 쿨다운(_auto_cc_blocked)이면 보류한다(연속 압축 방지). 발화 후
                # _auto_cc_mark 로 쿨다운을 새로 시작한다.
                if claude_context_hardstop(txt):
                    if (self.auto_hardstop and not p._hardstop_fired
                            and not self._auto_cc_blocked(p)):
                        p._hardstop_fired = True
                        self._auto_cc_mark(p)
                        self._pc_inject(p, "/compact")  # pty None 은 내부 가드
                else:
                    p._hardstop_fired = False
                # 원격 제어가 조직 정책으로 막혔다는 메시지를 한 번이라도 보면, 이
                # 세션(서버 프로세스) 동안 /rc 자동 주입을 영구 중단한다(요청). 정책은
                # 조직 단위라 서버 전역 플래그로 둬 모든 패널에 적용한다 — 안 그러면
                # 매 새 세션(/clear·재시작)마다 /rc 를 재시도해 같은 거부가 반복된다.
                if not self._rc_policy_blocked and claude_remote_blocked(txt):
                    self._rc_policy_blocked = True
                    for q in self._all_panes():
                        q._rc_pending = False   # 무장된 자동 /rc 예약도 거둔다
                # 원격제어가 켜진 걸(패널/표시) 한 번이라도 보면 sticky 로 기록 — 재시작
                # re-exec 후 거짓 None→Claude 로 auto /rc 가 재발해 이미 켜진 패널을 다시
                # 띄우지 않게 한다(_rc_done 은 _RESUME_FIELDS 로 직렬화돼 유지).
                if claude_remote_active(txt):
                    p._rc_done = True
                    # 추가(요청): 원격제어가 이미 켜진 걸 한 번이라도 관측하면 **서버
                    # 전역** sticky 를 세워, 이후 새 세션의 auto-launch fire 시점에 /rc 를
                    # 확정 스킵한다(아래 fire 블록의 skip 조건에 _rc_seen_active 포함).
                    # 데스크탑 앱이 세션마다 원격제어를 지속 연결하는 환경에선 이미 켜진
                    # 세션에 /rc 를 보내면 Claude 의 `/remote-control` 관리 대화가 다시 떠
                    # 진행이 멈추는데, 디바운스(타이밍)만으론 첫 프레임 레이스를 완전히 못
                    # 막으므로 "한 번 본 적 있으면 더는 안 쏨"으로 보장한다. **무장은
                    # 그대로 둔다** — auto-launch 는 /rc 외에 권한모드 auto 유도도 겸하므로
                    # (fire 블록이 /rc 만 건너뛰고 _perm_auto_pending 은 정상 인계). 수동
                    # 토글(footer 클릭→팝업 [r])은 영향 없음.
                    self._rc_seen_active = True
                # 사용자가 패널에서 직접 /usage 를 띄우면 그 **실측** 한도를 캡처해
                # 권위값(self._usage)으로 둔다 — 상태줄 5h%·토큰 화면 그래프가 /usage 와
                # 어긋나던 문제(요청). 그림자 probe 결과와 같은 형식이라 그대로 저장하고,
                # 패널이 사라져도 마지막 값을 유지한다(덮어쓸 때만 갱신·broadcast).
                # ① /usage 패널(전체, 권위) ② footer 인라인 한도(부분: "used 93% of
                # your session limit"). 둘 중 무엇이든 보이면 캡처해 상태줄 5h% 가 추정치
                # 대신 실측을 따르게 한다(요청). 인라인은 기존 _usage 에 병합(세션/주간만
                # 갱신, 패널서 받은 다른 키는 보존). 패널이 사라져도 마지막 값 유지.
                new_usage = None
                if "Current" in txt and "used" in txt:
                    new_usage = parse_usage(txt)
                # 인패널 /usage 패널이 안 보이다 처음 보이는 순간(상승에지)에만 자동
                # 팝업 신호(seq)를 올린다 — 패널이 떠 있는 동안 매 status 마다 감지되니
                # 패널별 직전 가시성(_usage_panel_seen)과 비교해 중복 팝업을 막는다.
                panel_now = new_usage is not None
                if panel_now and not getattr(p, "_usage_panel_seen", False):
                    self._usage_shown_seq += 1
                    changed = True
                p._usage_panel_seen = panel_now
                if "limit" in txt and "used" in txt:
                    inline = parse_inline_limit(txt)
                    if inline:
                        base = dict(self._usage) \
                            if isinstance(self._usage, dict) else {}
                        if new_usage:
                            base.update(new_usage)
                        base.update(inline)
                        new_usage = base
                if new_usage and new_usage != self._usage:
                    # 그림자 probe 가 붙여 둔 계정(일치 확인용)은 인패널 갱신이
                    # 덮지 않게 보존한다 — 인라인 parse_usage 엔 계정이 없다.
                    if isinstance(self._usage, dict) and self._usage.get("account") \
                            and "account" not in new_usage:
                        new_usage["account"] = self._usage["account"]
                    self._usage = new_usage
                    self._usage_ts = time.time()   # S6 T3: 신선도(표시·게이트)
                    # S6 T1: 실측 한도 스냅샷 이력화 — 값이 바뀐 순간만 적힌다
                    # (insert_limits 가 직전과 동일값이면 skip). 이 분기는
                    # new_usage != self._usage 일 때만 오므로 30Hz 스캔 부담 없음.
                    self._record_usage_snapshot(
                        new_usage, "panel" if panel_now else "inline")
                    changed = True
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
                # 하는 것을 막는다(API 에러 게이트 등 안정 신호 소비자가 읽음).
                if new_cl:
                    p._hdr_claude = True
                    p._hdr_claude_miss = 0
                elif p._hdr_claude:
                    p._hdr_claude_miss += 1
                    if p._hdr_claude_miss >= _HDR_CLAUDE_MISS:
                        p._hdr_claude = False
                        # 진짜 세션 종료(디바운스 확정) → auto /rc sticky 해제. 다음 claude
                        # 기동엔 정상 재무장. 재시작 transient 한 프레임은 miss 임계(30)에
                        # 못 미쳐 여기 안 오므로 _rc_done 이 살아남는다.
                        p._rc_done = False
                # 토큰 누계(#3): 새 Claude 세션 시작(None→Claude) 시 리셋, 매 프레임
                # 현재 응답 running 토큰을 step 으로 접어 응답별 peak 를 누계에 확정.
                # (확정 시점 committed>0 은 #7 의 영속 로깅 이벤트로도 쓰인다.)
                committed = 0
                if new_cl and not old_cl:
                    tokens.reset(p._tok_state)
                    # 새 Claude 세션 경계: 세션 id 부여, 계정 재감지(수동 지정은 유지).
                    self._next_claude_session_id(p)
                    if not p._claude_account_manual:
                        p._claude_account = None
                        p._claude_account_full = None
                        # 계정 자동 캡처(요청 2026-06-12): 새 세션 시작 시 그림자 /usage
                        # 프로브를 곧 1회 돌려 /status 로 계정 라벨을 잡게 한다(자동 갱신이
                        # 켜진 경우만). 위 스캔 백필이 그 계정을 패널에 채워 unknown 적재를
                        # 줄인다. _schedule_usage_refresh 디바운스로 중복 spawn 방지, 약간의
                        # 지연으로 실 세션이 먼저 부팅하게 둔다.
                        if self.usage_refresh_sec > 0:
                            self._schedule_usage_refresh(
                                self._USAGE_NEW_SESSION_DELAY)
                    # 시작 규칙 주입 예약(#27): 새 Claude 세션이 뜨면 다음 idle(입력
                    # 준비됨) 때 저장된 규칙을 프롬프트에 넣는다. 빈 규칙이면 안 함.
                    if self.claude_rules.strip():
                        p._rules_pending = True
                    # 새 세션 자동 셋업(auto-launch): 첫 idle 에 /rc(원격제어)+권한 auto
                    # 1회 적용. _rc_pending 가 idle 에서 /rc 를 쏘고 _perm_auto_pending
                    # 으로 넘겨, 다음 idle 에 _perm_target=auto 를 세운다(프레임 분리로
                    # /rc 제출과 shift+tab 이 한 묶음으로 섞이지 않게).
                    # 조직 정책으로 /rc 가 막힌 세션이면 자동 /rc 를 재무장하지 않는다.
                    # (재시작 후 거짓 새세션 오인으로 /rc 가 재발하는 건 fire 시점의
                    # _rc_done 가드로 막는다 — 무장은 perm-auto 유도도 겸하므로 둔다.)
                    if self.claude_auto_launch and not self._rc_policy_blocked:
                        p._rc_pending = True
                    p._ctx_fired = False   # 새 세션 — 잔량 자동정리 디바운스 해제(M11)
                    p._ctx_pct = None      # 새 세션 — 잔량% 추적 리셋(M15)
                    self._auto_cc_mark(p)  # 새 세션 — 시간기반 자동 compact·clear 쿨다운
                if not new_cl:
                    p._rules_pending = False   # 세션 끝나면 예약 해제
                    p._rc_pending = False      # 세션 끝 — auto-launch 예약 해제
                    p._perm_auto_pending = False
                    p._ctx_pct = None          # 세션 끝 — 잔량% 비교 대상 제외(M15)
                # 수동 /clear 감지: 이미 Claude 세션 중(old_cl)인데 환영 배너가 **새로**
                # 뜨면(빈 컨텍스트) 토큰 누계를 새 세션으로 끊는다. pytmux 자동화(_pc_
                # advance)를 안 타는 사용자 직접 /clear 가, 상태줄 ctx 근사%(누계/윈도우)를
                # 안 비워 /clear 후에도 옛 % 가 남던 문제 수정. 배너가 머무는 동안은
                # _welcome_seen 으로 1회만. 신규 시작(old_cl 없음)은 위 None→Claude 가 처리.
                wel = claude_welcome(txt)
                if wel and not p._welcome_seen and old_cl:
                    self._reset_token_session(p)
                    self._auto_cc_mark(p)   # 수동 /clear — 직후 자동 정리 쿨다운
                    p._ctx_fired = False
                    p._ctx_pct = None
                    if self.claude_rules.strip():
                        p._rules_pending = True
                    changed = True
                p._welcome_seen = wel
                if new_cl:
                    # 계정 단서를 세션 first-seen 으로 고정(§3.5③). 세션 경계에서
                    # None 으로 리셋되므로, 그 세션에서 **처음** 검출된 신뢰 계정만
                    # 래치하고 이후 프레임의 검출로는 덮지 않는다(수동 지정 우선).
                    # 예전엔 매 프레임 last-seen 갱신이라, 한 응답이 끝난 뒤 화면에
                    # 우연히 뜬 다른(또는 오검출) 계정 라벨이 이미 확정된 토큰을
                    # 엉뚱한 계정으로 재귀속할 수 있었다. 한 Claude 프로세스=한 계정
                    # 이므로 first-seen 이 의미상 정확하다.
                    if not p._claude_account_manual and p._claude_account is None:
                        acct = claude_account(txt)
                        # footer 전체 표시용 비별칭 계정(같은 판정). 스크랩에서 못 잡고
                        # 프로브 폴백을 쓰는 경우엔 전체가 없어 None → 클라가 별칭으로 폴백.
                        acct_full = claude_account_full(txt)
                        if not acct:
                            # 폴백(요청 2026-06-12): 패널 자체 화면엔 계정 라벨
                            # ('<email>'s Organization)이 안 떠 미식별이면, 그림자
                            # /usage 프로브가 /status 로 잡은 계정으로 채운다(한
                            # 머신=한 로그인 가정 — usagedb §5.5 단일계정 가정과 동일).
                            # 토큰이 'unknown' 으로 적재되던 걸 줄인다. 프로브 계정도
                            # 없거나 'unknown' 이면 종전대로 None(서버가 unknown 으로 묶음).
                            pa = (self._usage.get("account")
                                  if isinstance(self._usage, dict) else None)
                            if pa and pa != "unknown":
                                acct = pa
                                acct_full = None   # 프로브는 별칭만 → 전체 미상
                        if acct:
                            p._claude_account = acct
                            p._claude_account_full = acct_full
                    # M14c: 모델 배지(Opus 4.8 등) 갱신 — 마지막 본 값 유지.
                    mdl = claude_model(txt)
                    if mdl and mdl != p._claude_model:
                        p._claude_model = mdl
                        changed = True
                    # M15: 컨텍스트 잔량% 추적(우선순위 정리 비교용). 마지막 값 유지.
                    cp = claude_context_pct(txt)
                    if cp is not None:
                        p._ctx_pct = cp
                    running = tokens.parse_running_tokens(txt)
                    busy = new_cl == "busy"
                    peak0 = p._tok_state.get("peak", 0)   # step 전 진행중 peak
                    committed = tokens.step(p._tok_state, running, busy)
                    if committed > 0:
                        self._log_tokens(sess, t, p, committed)
                    # 10-D 판정용 env-gated 진단(기본 OFF): step 의 running/peak/
                    # committed + 스캔 간격을 라이브로 남긴다.
                    if self._token_debug_on():
                        self._log_token_debug(p, t, state=new_cl, busy=busy,
                                              running=running, peak_before=peak0,
                                              committed=committed)
                    # 표시용 누계 = 확정 total + **진행 중 응답의 peak**(아직 미확정).
                    # 예전엔 total 만 써서, 스트리밍 중인 현재 응답 토큰이 빠져 Claude
                    # 표시보다 항상 적게 나왔다(#20). peak 는 확정 시 total 로 접히므로
                    # total+peak 는 경계에서 연속적이고 이중계산이 없다.
                    live = p._tok_state["total"] + p._tok_state["peak"]
                    if live != p._session_tokens:
                        p._session_tokens = live
                        changed = True
                elif p._session_tokens:
                    # 세션 종료(None) — 진행 중 peak 가 미확정 채 버려지던 지점(10-D).
                    # busy footer 가 idle 프레임을 거치지 않고 곧장 사라지면(응답
                    # 스트리밍 중 Claude 종료·Ctrl-C·/quit 등) step 의 not-busy 확정을
                    # 못 거쳐 진행 중 peak 가 유실됐다. reset 전에 그 peak 를 1회
                    # 영속 확정해 마지막(부분) 응답의 토큰을 보존한다.
                    #   trade-off: busy↔None 깜빡임 뒤 같은 응답이 재개되면 재계수(과대)
                    #   여지가 있으나, §10-D 캡처 감사에서 running 중 None 프레임이 0건
                    #   (busy→None 직행 = 사실상 진짜 종료)이라 실질 위험 없음. 그래도
                    #   발생하면 미미한 1회 과대 vs 현행 1회 유실 — 누락 방향을 택한다.
                    surviving = p._tok_state.get("peak", 0)
                    if surviving > 0:
                        self._log_tokens(sess, t, p, surviving)
                    if self._token_debug_on():
                        self._log_token_debug(
                            p, t, state=new_cl, busy=False, running=None,
                            peak_before=surviving,
                            committed=surviving, reset=True)
                    p._session_tokens = 0
                    p._tok_state["peak"] = 0
                    p._tok_state["total"] = 0
                    changed = True
                # (M18-B 의 limit 진입 시 5h 상한 학습(_learned_5h_cap)은 S6 T3 에서
                #  분모 근사 폐기와 함께 제거 — 5h% 는 이제 /usage 실측만 따른다.)
                # 큐된 프롬프트 승격(#4): 헤더는 "지금 처리 중인 프롬프트"를 보여야
                # 한다. busy 중 입력한 프롬프트는 _track_prompt 가 pending_prompts 에
                # 쌓아 뒀다(last_prompt 즉시 안 바꿈). 응답 경계 — ① busy→non-busy(응답
                # 종료) 또는 ② 연속 busy 중 running 토큰 급감(committed>0 = 다음 응답
                # 시작) — 에서 큐의 다음 프롬프트를 last_prompt 로 승격한다.
                if p._claude is None:
                    if p.pending_prompts:
                        p.pending_prompts.clear()
                    p._busy_exit_miss = 0   # §3.4 라치 해제(세션 종료)
                    # Claude 세션 종료 → 권한모드 관측/목표 비움(§10 item 2)
                    if p._perm_mode is not None or p._perm_target is not None:
                        p._perm_mode = None
                        p._perm_target = None
                    # bypass 가용성도 리셋 — 다음 세션은 위험 플래그 없이 떴을 수 있다.
                    p._bypass_seen = False
                else:
                    # §3.4 busy 이탈 깜빡임 흡수: busy→idle 첫 프레임은 리페인트가
                    # 스피너를 한 프레임 놓친 깜빡임일 수 있다 — 즉시 승격하지 않고
                    # **다음 스캔도 idle** 이면(연속 2프레임) 경계로 확정한다. busy 로
                    # 복귀하면 라치 해제(승격 없음 — 조기 승격 방지). busy→limit/None
                    # 은 깜빡임이 아니므로 즉시. (done 플래그·auto-doc 타이머는 각자
                    # _DONE_IDLE_FRAMES·busy 복귀 해제로 이미 깜빡임에 안전하다.)
                    exit_now = (old_cl == "busy" and new_cl != "busy")
                    if exit_now and new_cl == "idle":
                        p._busy_exit_miss = 1     # 확정 대기(다음 스캔에 판정)
                        boundary = False
                    elif p._busy_exit_miss and new_cl == "idle":
                        p._busy_exit_miss = 0
                        boundary = True           # 연속 2프레임 idle → 경계 확정
                    else:
                        p._busy_exit_miss = 0
                        boundary = exit_now       # busy→limit/None 등은 즉시
                    if not boundary and committed > 0 and new_cl == "busy":
                        boundary = True
                    if boundary and p.pending_prompts:
                        p.last_prompt = p.pending_prompts.pop(0)
                        changed = True
                # 데스크탑 앱 원격제어 등 입력 경로(_track_prompt)를 안 거친 프롬프트
                # 반영(§10 #19): 화면 transcript 에서 최신 사용자 프롬프트를 best-effort
                # 추출해, 입력으로 안 잡힌(last_prompt 와 다르고 승격 대기 큐에도 없는)
                # 경우에만 헤더를 갱신한다. 로컬 입력은 _track_prompt 가 제출 즉시
                # last_prompt/pending 큐에 남기므로 여기 가드에 걸려 중복되지 않는다.
                # 화면 파싱은 best-effort 라 보수적으로 매칭한다.
                if new_cl:
                    sp = claude_prompt(txt)
                    if (sp and sp != p.last_prompt
                            and sp not in p.pending_prompts[-5:]):
                        p.last_prompt = sp
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
                        if old_cl != "busy":
                            p._busy_since = time.monotonic()   # M17 S9: 턴 시작 시각
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
                # 전송 에러(API error/rate limit/overloaded) 자동 재시도(요청): 에러로
                # 멈추면 1분 뒤 "계속" 주입을 예약하고, 에러 해소(busy 복귀 등) 시 취소한다.
                # 게이트는 new_cl 이 아니라 **_hdr_claude**(이 패널이 Claude 임 — 디바운스):
                # API 에러 화면은 idle/busy footer 가 없어 claude_state=None 일 수 있고,
                # Claude 아닌 셸이 우연히 "API Error" 를 찍어도 오발화하지 않게 한다.
                # 5h 사용량 배너("usage limit reached")는 claude_api_error 에 안 잡히고,
                # "rate limit exceeded" 처럼 둘 다 걸리는 경우만 autoresume 가 이미 재개를
                # 무장(_resume_pending)했으면 양보해 중복 주입을 막는다(reset 시각으로 다룸).
                if p._hdr_claude and claude_api_error(txt) and not p._resume_pending:
                    self._maybe_schedule_retry(p)
                else:
                    # 에러 아님(해소·busy/idle 복귀·autoresume 양보·non-Claude) → 무장
                    # 예약 취소 + 연속 재시도 카운터 리셋(다음 새 에러는 다시 1분부터, #9 H3).
                    if p._retry_handle is not None:
                        self._cancel_retry(p)
                    p._retry_attempts = 0
                # 자동 doc→/clear(§10): idle 이탈(busy/limit/종료) 시 무장된 타이머를
                # 즉시 해제한다 — idle 이 끊기면 "N초 지속" 전제가 깨진다. 권한모드
                # 자동전환 시도 카운터도 idle 이탈 시 리셋(다음 idle 진입에 다시 시도).
                if new_cl != "idle":
                    self._adc_disarm(p)
                    self._acpt_disarm(p)   # idle 끊김 → 자동 /compact 무장 해제
                    self._perm_reset(p)
                else:
                    # P1 CSE: 권한모드 관측값(claude_perm_mode)은 txt 가 이 프레임 내내
                    # 불변이라 idle 진입 시 1회만 구해 아래 모든 분기가 재사용한다
                    # (예전엔 1138/1145/1161 에서 같은 txt 를 3번 재스캔).
                    pm = claude_perm_mode(txt)
                    # 탭→세션 리네임 보류분(servertree.rename_window): 리네임 당시
                    # busy 라 즉시 주입 못 한 `/rename` 을 입력 준비된 첫 idle 에
                    # 발동한다. 1회성이라 발화 즉시 비운다.
                    if p._pending_rename:
                        nm = p._pending_rename
                        p._pending_rename = None
                        self._pc_inject(p, "/rename " + nm)
                    # 시작 규칙 주입(#27): 새 세션/clear 후 첫 idle(입력 준비됨)에 한 번.
                    if p._rules_pending:
                        p._rules_pending = False
                        self._inject_rules(p)
                    # 새 세션 자동 셋업(auto-launch, 요청): idle 이 _RC_CONFIRM_FRAMES
                    # 안정되면 /rc 로 원격 제어(리모트 커넥션)를 켜고, 다음 idle 에 권한
                    # 모드를 auto 로 유도한다. /rc 와 권한 shift+tab 을 다른 프레임으로
                    # 갈라 한 묶음 입력으로 섞이지 않게 한다. 이미 원격제어가 켜진 화면
                    # (resume 후 오인·데스크탑 앱 재연결 등)에선 /rc 를 건너뛰어 도로
                    # 끄거나 /remote-control 응답 대기 대화로 멈추지 않게 한다.
                    if p._rc_pending:
                        # /rc 생략 조건: ① 조직 정책 차단 ② 원격제어 기관측 sticky
                        # (_rc_seen_active — 서버 전역) ③ 이미 이 세션에 적용함(_rc_done —
                        # 재시작 re-exec 후 거짓 새세션 오인 방지, 직렬화됨. 위
                        # claude_remote_active 분기가 'Remote Control active' 관측 시 셋)
                        # ④ 지금 화면이 이미 원격제어 ON. 어느 하나라도면 즉시 종료
                        # (도로 끄지/응답 대기 대화 띄우지 않음).
                        if (self._rc_policy_blocked or self._rc_seen_active
                                or p._rc_done or claude_remote_active(txt)):
                            p._rc_pending = False
                            p._rc_done = True
                            p._perm_auto_pending = True
                        elif p._idle_frames >= _RC_CONFIRM_FRAMES:
                            # idle 이 _RC_CONFIRM_FRAMES 프레임 연속 안정 — 그 사이 원격제어
                            # 오버레이가 안 떴으니 정말 꺼진 새 세션 → /rc 1회 주입. 첫 idle
                            # 프레임에 바로 안 쏘는 이유: 데스크탑 앱이 이미 켠 원격제어
                            # 오버레이가 한두 프레임 늦게 그려질 수 있어, 그걸 못 보고 쏘면
                            # /remote-control 이 응답 대기 대화로 멈춘다(버그 수정).
                            p._rc_pending = False
                            self._pc_inject(p, "/rc")
                            p._rc_done = True
                            p._perm_auto_pending = True
                        # else: 디바운스 진행 중 — _rc_pending 유지(위 pending 게이트가
                        # 정적 화면에서도 스캔을 이어 가 _idle_frames 를 임계까지 올린다).
                    elif p._perm_auto_pending:
                        p._perm_auto_pending = False
                        if pm not in ("auto", "bypass"):
                            # acceptEdits 도 auto 가 아니므로 여기서 auto 까지 마저 순환한다
                            # (예전엔 accept 를 auto 로 오인해 새 세션이 accept 에서 멈췄다).
                            p._perm_target = "auto"   # 아래 폐루프가 auto 까지 순환
                            self._perm_reset(p)
                    # idle: 현재 권한모드를 관측해 저장(팝업 '현재 모드' 표시용 — status
                    # 로 클라에 전달, §10 item 2). footer 가 안 보이면(None) 마지막 값 유지.
                    # (pm 은 위 else 진입부에서 1회 계산 — P1 CSE.)
                    if pm is not None and pm != p._perm_mode:
                        p._perm_mode = pm
                        changed = True
                    # bypass 를 한 번이라도 관측하면(=시작 시 --dangerously-skip-
                    # permissions 활성) sticky 로 기억해 팝업에 'Bypass' 항목 노출.
                    if pm == "bypass" and not p._bypass_seen:
                        p._bypass_seen = True
                        changed = True
                    # 권한모드 구동: 사용자가 footer 클릭 팝업으로 고른 수동 목표
                    # (_perm_target)가 우선, 없고 claude_auto_mode 면 auto 로 순환
                    # (§10 item 2 + 권한모드 자동 오토모드 전환). 둘 다 shift+tab 폐루프.
                    if p._perm_target:
                        self._drive_perm_mode(p, txt, p._perm_target)
                    elif (self.claude_budget_plan
                          and self._usage_gate_level(p) >= 80
                          and pm not in ("plan", "bypass")):
                        # M13: 실측 한도 압박(게이트 임계의 80% 도달) → plan 유도.
                        # bypass(명시적 위험)는 불간섭, 이미 plan 이면 무동작.
                        # claude_auto_mode 보다 우선. (§7-4: 절대 예산 deprecate 로
                        # 스크랩 추정 축 제거 — 실측 게이트 레벨만 본다.)
                        self._drive_perm_mode(p, txt, "plan")
                    elif self.claude_auto_mode:
                        self._maybe_auto_mode(p, txt)
                    # M11 디바운스 해제: 정리 후 잔량이 임계+여유(5%p) 위로 회복하면
                    # 다음 저잔량 구간에 재발화할 수 있게 한다. 회복 전엔 재발화 금지
                    # (compact 가 효과 없어도 매 응답 무한 정리하지 않게 — §5.5).
                    if p._ctx_fired:
                        rec = cp   # P1 CSE: 위 976 의 claude_context_pct(txt) 재사용
                        if rec is not None and rec >= self.claude_ctx_threshold + 5:
                            p._ctx_fired = False
                # 프롬프트 단위 클리어 모드(#9) + 자동 doc→/clear(§10): busy→idle(응답
                # 완료) 경계에서 상태기계를 전진한다. 수동 모드(prompt_clear_mode)와
                # 자동 시퀀스(_adc_active)가 같은 _pc_phase 기계를 공유한다.
                # 진행 중이 아니면서 자동 모드가 켜져 있으면 idle 진입 시점에 무장만
                # 한다(실제 발화는 N초 뒤 _adc_fire).
                if old_cl == "busy" and new_cl == "idle":
                    # M17 S8: 완료 경계마다 화면 꼬리를 직전과 비교(동일 출력 반복 카운트).
                    p._done_tail, p._repeat_n = track_repeat(
                        p._done_tail, p._repeat_n, screen_tail_key(txt))
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
                        pct = (cp   # P1 CSE: 976 의 claude_context_pct(txt) 재사용
                               if self.claude_ctx_autoclear and not p._ctx_fired
                               else None)
                        # M15 우선순위 정리: 계정 실측 사용량이 게이트 임계 이상이고
                        # (§7-4: 절대 계정 예산 deprecate → 실측 게이트로 전환), 이
                        # 패널이 그 계정 idle 세션 중 가장 꽉 찬(잔량% 최저) 패널이면,
                        # 개별 임계 미만이 아니어도 정리한다 — 멀티세션 누적 시 가장
                        # 비싼 세션부터 비운다. 실측 부재·stale 이면 fail-open(미발화).
                        priority = (self.claude_ctx_autoclear and not p._ctx_fired
                                    and self._usage_gate_over(p)
                                    and self._is_fullest_idle(p))
                        if ((pct is not None and pct < self.claude_ctx_threshold)
                                or priority):
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
                        elif self.auto_compact:
                            self._acpt_arm(p)   # idle N초 후 /compact(요청)
                # M17(T7) 표시 경고 갱신(grade0 — 알림만, 개입 없음). S9 장기 턴 우선,
                # 아니면 S8 반복 루프. 임계는 opt(0=끔). 세션 종료 시 상태 리셋.
                warn = None
                lt = self.claude_long_turn_sec
                ra = self.claude_repeat_alert
                if (lt > 0 and new_cl == "busy" and p._busy_since is not None):
                    el = time.monotonic() - p._busy_since
                    if el >= lt:
                        # 장기 턴 경고: 경고 삼각형(⚠) + 경과 시간. 기본 분:초, 1시간
                        # 이상이면 시:분(사용자 요청 2026-06-17, fmt_long_turn_badge).
                        # 초가 매초 바뀌므로 정수 초 경계에서만 changed.
                        warn = fmt_long_turn_badge(el)
                elif ra > 0 and new_cl == "idle" and p._repeat_n >= ra:
                    warn = f"⚠ 동일 결과 {p._repeat_n + 1}회 반복 — 루프 의심"
                if not new_cl:
                    p._busy_since = None
                    p._repeat_n = 0
                    p._done_tail = None
                # §3.7: 포맷 미인식이면 ⚠ 경고로 가시화(다른 경고가 없을 때만 — 미인식은
                # new_cl=None 이라 위 long-turn/repeat 와 상호배타지만 안전하게 or 사용).
                if p._fmt_unknown:
                    warn = warn or _FMT_UNKNOWN_MSG
                if warn != p._claude_warn:
                    p._claude_warn = warn
                    changed = True
                # M14c 힌트(T3/S4): Opus 로 반복 작업 + 컨텍스트 여유 충분이면 더 가벼운
                # 모델 "고려" 힌트만(알림 전용 — 자동 전환 없음). opt-in(기본 끔)이라
                # 토글이 켜져 있고 idle 완료 경계일 때만 평가한다. 세션 종료(new_cl=None)면
                # 위 _repeat_n 리셋과 함께 자연히 None(repeat_n<min)으로 떨어진다.
                tip = (model_overselect_hint(p._claude_model, p._repeat_n, cp)
                       if self.claude_model_hint and new_cl == "idle" else None)
                if tip != p._model_tip:
                    p._model_tip = tip
                    changed = True
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
        """활성 패널의 Claude 계정을 키로, 그 계정에 속한 모든 패널(전체 세션·탭
        순회)의 세션 누적 토큰을 합산한다(§10 계정별 합계).

        §10-B(2026-06-11): **같은 계정이면 어느 탭/패널이 활성이어도 같은 합계**가
        보이게 한다. 과거엔 계정 추정 전(미식별) 패널에서 활성 패널 단독 누계로
        폴백해, 식별된 패널(계정 합계)과 미식별 패널(단독 누계)을 오갈 때 좌하단
        숫자가 출렁였다. 규칙:
        - 식별된 계정이 **하나뿐**(또는 전부 미식별)이면 단일 계정으로 보고,
          미식별 패널까지 포함해 Claude 흔적이 있는 전 패널을 합산한다 — 어떤
          패널이 활성이어도 같은 숫자.
        - 계정이 **둘 이상** 식별된 상태에선 활성 패널 계정의 합계(기존 동작).
          활성 패널이 미식별이면 귀속이 모호하므로 0 을 보낸다 — 클라이언트가
          마지막 비어있지 않은 값을 유지해 표시 숫자는 그대로 머문다.
        활성 패널이 Claude 가 아니면 0(클라 지속표시·렌더는 claude_active 게이트)."""
        if not ap or not (ap._claude or ap._claude_account):
            return 0
        # Claude 흔적(실행 중이거나 계정이 식별됐던) 패널만 모은다 — 종료된 패널도
        # 계정이 남아 있으면 누계를 유지한다(기존 합산과 동일한 포함 기준 + 미식별).
        panes = [p for p in self._all_panes()
                 if p._claude or p._claude_account]
        known = {p._claude_account for p in panes if p._claude_account}
        if len(known) <= 1:
            return sum(p._session_tokens for p in panes)
        acct = ap._claude_account
        if acct:
            return sum(p._session_tokens for p in panes
                       if p._claude_account == acct)
        return 0

    def _is_fullest_idle(self, pane) -> bool:
        """M15: pane 이 **같은 계정의 idle Claude 패널 중 잔량%가 가장 낮은**(=가장 꽉
        찬) 패널인지. 멀티세션 누적 시 가장 비싼 세션부터 정리하려고 선택한다. 잔량%를
        아는 후보만 비교하고, 동률이면 True(경계마다 1개만 발화되므로 무방)."""
        acct = pane._claude_account
        cand = [q for q in self._all_panes()
                if q._claude == "idle" and q._claude_account == acct
                and q._ctx_pct is not None]
        if pane not in cand:
            return False
        return pane._ctx_pct <= min(q._ctx_pct for q in cand)

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
                # 콤팩트 포맷 'ctx:~N%/1M'. ~ 는 근사(세션누계 기반) 표시 유지.
                return f"ctx:~{round(st / wt * 100)}%/{u[:-4]}"
        return u

    def _tok5h_pct(self, p, total):
        """세션(5h) 한도 근접도 %(int)|None — **실측만**(S6 T3).
        /usage 실측(그림자 probe·인패널 패널·footer 인라인)이 있으면 그 세션 % 를
        그대로, 없으면 None(지어내지 않음). 과거 ② 분모 근사(token_budget_5h 설정/
        limit 관측 학습 _learned_5h_cap 기반 total/cap)는 실측 1차화로 죽은 코드가
        돼 폐기했다(2026-06-10 사용자 결정, 시나리오 §7-3) — 근사가 실측과 섞여
        보이던 모순(999%/5h 류)도 함께 사라진다. total 인자는 호출부(C5 재사용
        합계) 시그니처 유지용으로만 남는다."""
        if p is None or not p._claude:
            return None
        u = self._usage
        if not (u and isinstance(u.get("session"), dict)
                and u["session"].get("pct") is not None):
            return None
        # 계정 불일치 fail-open(_usage_gate_pcts 와 같은 원칙): 그림자 /usage 세션의
        # 계정과 이 패널의 계정이 **둘 다 알려져 있고 다르면** 5h% 를 숨긴다. 안 그러면
        # 다른 계정의 한도가 이 패널 계정 라벨로 그려져("N%/5h used <패널계정>")
        # 팝업의 'Account (/usage): <측정계정>' 과 서로 다른 계정을 가리킨다(사용자
        # 보고 2026-06-13). 한쪽이라도 미상이면 같은 로그인으로 보고 표시한다.
        ua = u.get("account")
        pa = getattr(p, "_claude_account", None)
        if ua and pa and ua != pa:
            return None
        return int(u["session"]["pct"])              # M19 실측 — 분자/분모 불필요

    def _week_sonnet_pct(self, p):
        """주간 **Sonnet only** 한도 % (int)|None — /usage 실측만. 활성 모델이
        Sonnet 일 때 5h 세션%(모델 통합값이라 모델별 측정 불가) 대신 보인다
        (2026-06-16 사용자 결정). Anthropic /usage 는 모델별 분리를 주간 'Sonnet
        only' 에만 제공한다. 계정 불일치 fail-open 은 _tok5h_pct 와 동일."""
        if p is None or not p._claude:
            return None
        u = self._usage
        if not (u and isinstance(u.get("week_sonnet"), dict)
                and u["week_sonnet"].get("pct") is not None):
            return None
        ua = u.get("account")
        pa = getattr(p, "_claude_account", None)
        if ua and pa and ua != pa:
            return None
        return int(u["week_sonnet"]["pct"])

    def _any_claude_pane(self) -> bool:
        """살아 있는 패널 중 Claude 세션이 하나라도 있으면 True(자동 /usage 게이트).
        Claude 를 안 쓰는데 숨은 세션을 띄워 스크랩하는 낭비를 막는다."""
        return any(getattr(p, "_claude", None) for p in self._all_panes())

    def _probe_cwd(self):
        """그림자 /usage 프로브의 cwd — **실행 중인 Claude 패널의 셸 cwd**(사용자가
        이미 신뢰한 폴더). 데몬 cwd(보통 홈 ~)로 띄우면 숨은 claude 가 Claude Code
        신뢰 대화상자("Is this a project you trust?")에 막혀 /usage 패널이 영영 안
        뜨고 프로브가 조용히 None 이었다(2026-06-11 라이브 진단 — 재시작 후 limits
        0건·실측 미표시의 원인. 직접 같은 조건으로 재현해 트러스트 화면 확인).
        Claude 패널이 없으면(프로브 게이트상 드묾) 기존 데몬 cwd 폴백."""
        for p in self._all_panes():
            if getattr(p, "_claude", None):
                d = self._pane_cwd(p)
                if d:
                    return d
        return getattr(self, "cwd", None) or None

    async def refresh_usage(self):
        """M19 그림자 /usage 질의: executor 스레드에서 숨은 claude 를 띄워 /usage 패널을
        스크랩(usageprobe.query_usage)해 self._usage 에 저장하고 broadcast 한다. 사용자
        화면 무간섭. 중복 호출은 무시. 결과 dict|None."""
        if self._usage_busy:
            return self._usage
        self._usage_busy = True
        try:
            from . import usageprobe
            loop = asyncio.get_event_loop()
            cwd = self._probe_cwd()   # 신뢰된 폴더(Claude 패널 cwd) — 위 docstring
            u = await asyncio.wait_for(
                loop.run_in_executor(None, usageprobe.query_usage, "claude", cwd),
                timeout=35)
        except Exception:
            # #28: 프로브 실패(타임아웃·spawn 불가)는 표시상 '미확인' 폴백으로
            # 충분하지만, 진단 단서는 남긴다 — 10분 주기 반복이라 첫 실패만 기록.
            if not self._usage_probe_err:
                self._usage_probe_err = True
                self._log_error("usage_probe")
            u = None
        finally:
            self._usage_busy = False
        if u:
            self._usage_probe_err = False             # 회복 — 재발 시 다시 1회 기록
            self._usage = u
            self._usage_ts = time.time()              # S6 T3: 신선도(표시·게이트)
            self._record_usage_snapshot(u, "probe")   # S6 T1: 실측 이력화
            self._after_usage_probe()                 # S6 T5: 임계 부근 주기 단축
            for s in list(self.sessions.values()):
                self._broadcast_session(s)
        return u

    # ---- S6 T5: 이벤트 트리거 실측 갱신(주기 600초의 보강) ----
    # 숨은 claude 세션 spawn 은 비싸다(부팅 ~12초·타임아웃 35초) — 공격적 단축 금지.
    # 트리거 2종만: ① 응답 종료(committed) 후 디바운스 1회(실측이 묵었을 때만)
    # ② 프로브 성공 직후 임계 부근이면 다음 프로브를 주기/4 로 앞당김(게이트 직전
    # 해상도 — 95% 게이트가 10분 묵은 85% 실측 위에서 졸지 않게).
    _USAGE_COMMIT_DELAY = 20.0    # 커밋 폭주를 1회로 합치는 디바운스(초)
    _USAGE_COMMIT_MIN_AGE = 180.0  # 실측이 이보다 신선하면 커밋 트리거 생략(초)
    _USAGE_NEAR_GATE_MARGIN = 10   # 임계 -N%p 이내면 '부근'(주기 단축 발동)
    _USAGE_NEW_SESSION_DELAY = 25.0  # 새 세션 시작 후 계정 캡처 프로브까지 지연(실 세션 부팅 양보)

    def _schedule_usage_refresh(self, delay: float) -> bool:
        """delay 초 뒤 그림자 /usage 갱신 1회 예약. 이미 예약돼 있으면 유지(중복
        없음 — 디바운스). 예약 성공 True."""
        if getattr(self, "_usage_probe_handle", None) is not None:
            return False
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return False
        self._usage_probe_handle = loop.call_later(
            max(0.0, delay), self._fire_usage_refresh)
        return True

    def _fire_usage_refresh(self):
        """예약된 이벤트 트리거 갱신 발화 — Claude 패널이 남아 있을 때만(게이트는
        refresh_usage 의 _usage_busy 중복 방지에 더해 빈 서버 spawn 낭비 방지)."""
        self._usage_probe_handle = None
        if not self._any_claude_pane() or not self.running:
            return
        asyncio.create_task(self.refresh_usage())

    def _usage_near_gate(self) -> bool:
        """마지막 실측이 게이트 임계 -10%p 이내인가(세션/주간 중 하나라도, 켜진 축만).
        계정 일치는 안 본다 — 주기 단축은 패널 무관한 전역 해상도 결정이다."""
        u = self._usage
        if not isinstance(u, dict):
            return False
        for key, gate in (("session", self.usage_gate_session_pct),
                          ("week_all", self.usage_gate_week_pct)):
            d = u.get(key)
            pct = d.get("pct") if isinstance(d, dict) else None
            if gate > 0 and pct is not None \
                    and pct >= gate - self._USAGE_NEAR_GATE_MARGIN:
                return True
        return False

    def _after_usage_probe(self):
        """프로브 성공 직후: 임계 부근이면 다음 갱신을 주기/4(최소 60초)로 앞당겨
        예약한다. 자동 갱신이 꺼져 있으면(usage_refresh_sec=0) 사용자 의사를 존중해
        앞당기지도 않는다."""
        if self.usage_refresh_sec <= 0 or not self._usage_near_gate():
            return
        self._schedule_usage_refresh(max(60.0, self.usage_refresh_sec / 4))

    def _on_token_commit_refresh(self):
        """응답 종료(committed>0) 이벤트: 실측이 묵었으면(>3분) 디바운스(20초) 갱신
        예약 — 연속 응답 폭주는 1회로 합쳐진다. 신선하면 아무것도 안 한다(프로브
        비용 절약 — 실측 %는 분 단위로만 움직인다)."""
        ts = getattr(self, "_usage_ts", None)
        if ts is not None and (time.time() - ts) < self._USAGE_COMMIT_MIN_AGE:
            return
        self._schedule_usage_refresh(self._USAGE_COMMIT_DELAY)

    @staticmethod
    def _track_prompt(pane: Pane, data: bytes):
        r"""입력 바이트에서 현재 (멀티라인) 프롬프트를 누적하고 **제출(Enter=\r)** 시
        last_prompt(헤더 표시)로 확정한다. CSI/ESC 시퀀스는 건너뛰고(화살표 등),
        bracketed paste 본문은 포함한다.

        멀티라인: Shift+Enter(또는 Ctrl+J)는 LF(`\n`)를 보내 **줄바꿈만** 하고 제출은
        안 한다 — 이때는 버퍼에 개행으로 이어 붙여, 한 번에 입력된 여러 줄이 **한 개의**
        제출 단위가 되게 한다. 제출은 오직 CR(`\r`)만(docs: Enter→`\r`, Shift+Enter→
        `\n`). Enter 가 `\r\n` 으로 와도 `\r` 에서 확정하고 뒤따르는 `\n` 은 빈 버퍼라
        무시된다."""
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
            if ch == "\n":                   # Shift+Enter/Ctrl+J: 프롬프트 안 줄바꿈
                buf += "\n"                  # 제출 아님 — 누적만(멀티라인 한 항목 유지)
            elif ch == "\r":                 # Enter: 제출 경계 → 누적분을 한 항목으로 확정
                line = buf.strip()
                if line:
                    # 헤더용 last_prompt(#4): 이전 프롬프트가 아직 처리중(busy)이면
                    # 즉시 덮지 말고 pending 큐에 쌓는다 — _scan_claude 가 응답 경계에
                    # 다음 프롬프트를 승격한다(헤더 = "지금 처리 중인 프롬프트").
                    # busy 가 아니면(idle/None/limit) 곧장 확정. 헤더는 한 줄이라
                    # 개행을 공백으로 접어 셀 렌더가 깨지지 않게 한다.
                    head_line = line.replace("\n", " ")
                    if pane._claude == "busy":
                        pane.pending_prompts.append(head_line)
                    else:
                        pane.last_prompt = head_line
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

    # ---- 토큰 절감 설정(docs/internal/TOKEN_SAVING_SCENARIO.md) ----
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

    # ---- 토큰 사용량 영속 저장(#7, SQLite) — S5 토큰 모듈화 T2 에서 코어 server.py
    # 에서 이리로 이전. 코어는 더 이상 토큰 DB를 모른다(usagedb/usagelog
    # import 도 코어에서 제거). 런타임 상태(_tokens_db 등)는
    # server_init 훅(_init_token_state)이 설치한다 — 디렉토리 삭제 시 코어 server 에
    # 이 속성들이 아예 안 생기고, 읽는 코드(아래 메서드들)도 함께 사라진다.
    def _init_token_state(self):
        """server_init 훅이 부른다 — 토큰 DB 연결 런타임 상태를 코어
        server.__init__ 에서 빼내 여기서 설치한다(delete-to-disable).
        (§7-4: 절대 예산 deprecate 로 일예산 누계 _today_*/_budget_level 은 제거.)"""
        self._tokens_db = None
        # §3.5②: 세션 일련번호를 DB max(session) 으로 시드했는지(첫 세션 부여 직전 1회).
        # 재시작 후 _claude_session_seq=0(코어) 가 옛 세션 id 와 충돌하는 것 방지.
        self._session_seq_seeded = False
        # #28 진단 로깅 스팸 가드: 반복 실패(매 커밋 DB 재연결 시도·10분 주기
        # 프로브)가 error.log 를 도배하지 않게 '첫 실패만' 기록하는 플래그.
        self._tokens_db_err = False
        self._usage_probe_err = False
        # S6 T3: 마지막 실측(/usage) 수신 시각 — 표시층 stale 표기·T4 게이트 신선도
        # 판단용. _usage(값)는 코어 잔류 초기화(§1.4)지만 ts 는 플러그인 소유.
        self._usage_ts = None
        # S6 T5: 이벤트 트리거 갱신 예약 핸들(call_later) — 중복 예약 방지 디바운스.
        self._usage_probe_handle = None

    @property
    def tokens_log_path(self) -> str:
        """(레거시) 응답별 확정 토큰의 JSONL 로그 경로. 이제 저장은 SQLite(tokens_db_path)
        로 옮겼고, 이 경로는 **기존 이력을 DB 로 일회 임포트**하는 원본으로만 남는다
        (휘발 영역 state_base, docs/internal/TOKEN_USAGE_STORAGE_DESIGN.md §5)."""
        return ipc.state_base(self.sock_path) + ".tokens.jsonl"

    def _tokens_db_filename(self) -> str:
        """DB 파일명. 기본 소켓은 claude-tokens.db, 그 외(임시 소켓 등)는 소켓 id 로 격리."""
        if self.sock_path == ipc.default_endpoint():
            return "claude-tokens.db"
        return f"claude-tokens-{self._capture_id()}.db"

    @property
    def tokens_db_path(self) -> str:
        """토큰 사용량 SQLite DB 경로. **S5 토큰 모듈화 T5** 에서 DB 파일을 플러그인
        디렉터리 하위(`pytmuxlib/plugins/claude-code/db/`)로 옮겼다 — claude-code 를 통째로
        지우면 토큰 이력 데이터까지 함께 사라진다(delete-to-disable 가 데이터까지). db/ 는
        .gitignore·중앙 p4ignore 로 버전관리에서 제외한다(런타임·호스트 로컬). 이전 위치
        (프로젝트 루트 db/)의 DB 는 _migrate_legacy_db 가 첫 연결 시 1회 이전한다.
        PYTMUX_TOKENS_DB 로 강제 지정 가능(테스트가 임시 파일을 주입해 오염 방지)."""
        override = os.environ.get("PYTMUX_TOKENS_DB")
        if override:
            return override
        return os.path.join(os.path.dirname(__file__), "db",
                            self._tokens_db_filename())

    def _legacy_tokens_db_path(self) -> str:
        """이 CL(S5 T5) 이전 또는 타 머신의 DB 위치: 프로젝트 루트 `db/`. 마이그레이션 원본."""
        from pytmuxlib.servercapture import PROJECT_DIR
        return os.path.join(PROJECT_DIR, "db", self._tokens_db_filename())

    def _migrate_legacy_db(self, new_path: str):
        """S5 T5 업그레이드 마이그레이션(타 머신 포함): 토큰 DB 가 이전 위치(프로젝트 루트
        db/)에 있고 새 위치(플러그인 db/)엔 아직 없으면 1회 이전한다 — 이력 유실 없이 무중단.
        WAL 사이드카(-wal/-shm)도 함께 옮긴다. 크로스 디바이스(EXDEV) 등 실패 시 복사로
        폴백하고, 그래도 실패하면 조용히 넘어가 이후 JSONL 일회 임포트가 누계를 복구한다.
        PYTMUX_TOKENS_DB 강제 지정(테스트) 시엔 건너뛴다."""
        if os.environ.get("PYTMUX_TOKENS_DB"):
            return
        old = self._legacy_tokens_db_path()
        if old == new_path or not os.path.exists(old) or os.path.exists(new_path):
            return
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            os.replace(old, new_path)
        except OSError:
            try:
                import shutil
                shutil.copy2(old, new_path)   # 크로스 디바이스 폴백(원본은 남겨 둠)
            except OSError:
                return
        for suffix in ("-wal", "-shm"):      # SQLite WAL 사이드카도 따라 옮긴다(있으면)
            if os.path.exists(old + suffix) and not os.path.exists(new_path + suffix):
                try:
                    os.replace(old + suffix, new_path + suffix)
                except OSError:
                    pass

    def _tokens_db_conn(self):
        """토큰 DB 연결(최초 1회 열고 보관). 먼저 이전 위치(루트 db/)의 DB 를 새 위치
        (플러그인 db/)로 1회 마이그레이션한다(S5 T5). 새(빈) DB 이고 기존 JSONL 이력이
        있으면 일회 임포트해 누적 통계를 보존하고, 재임포트 방지로 JSONL 을 `.imported` 로
        옮긴다. 실패해도 None 을 돌려 본 흐름(토큰 로깅)을 막지 않는다."""
        if self._tokens_db is not None:
            return self._tokens_db
        path = self.tokens_db_path
        self._migrate_legacy_db(path)
        try:
            conn = usagedb.connect(path)
        except Exception:
            # #28: 토큰 영속이 통째로 멈추는 치명 실패가 조용히 사라지지 않게
            # error.log 에 남긴다. 매 커밋마다 재시도하므로 첫 실패만 기록(스팸 가드)
            # — 디스크가 돌아오면 다음 성공 시 플래그를 풀어 재발도 잡힌다.
            if not self._tokens_db_err:
                self._tokens_db_err = True
                self._log_error("tokens_db_connect")
            return None
        self._tokens_db_err = False
        try:
            if usagedb.count(conn) == 0:
                old = self.tokens_log_path
                if os.path.exists(old) and usagedb.import_jsonl(conn, old) > 0:
                    try:
                        os.replace(old, old + ".imported")
                    except OSError:
                        pass
        except Exception:
            self._log_error("tokens_jsonl_import")   # 이력 임포트 실패(1회성 경로)
        self._tokens_db = conn
        return conn

    def _log_tokens(self, sess: Session, tab: Tab, pane: Pane, amount: int):
        """응답 한 건의 확정 토큰을 SQLite 에 적는다(tokens.step committed>0 이벤트)."""
        rec = usagelog.make_record(
            ts=time.time(), tab=tab.index, pane=pane.id,
            session=getattr(pane, "_claude_session_id", 0),
            account=pane._claude_account, tokens=amount)
        conn = self._tokens_db_conn()
        if conn is not None:
            usagedb.insert(conn, rec)
        # S6 T5: 응답 종료 이벤트 → 실측이 묵었으면 디바운스 갱신 예약.
        self._on_token_commit_refresh()

    def set_token_debug(self, value=None):
        """토큰 회계 진단 로그(10-D) 토글. value 미지정 시 반전. opts.json plugin_opts
        영속(server_opts_serialize) + **런타임 즉시 발효** — 다음 `_scan_claude` step 부터
        `<sock>.tokendbg.jsonl` 기록을 켜거나 멈춘다(데몬 재시작 불필요). 이전엔 env
        `PYTMUX_TOKEN_DEBUG` 1회 캐시라 토글에 재시작이 필요했다(§10-D)."""
        self.token_debug = (not getattr(self, "token_debug", False)) \
            if value is None else bool(value)
        self._save_opts()
        return self.token_debug

    def _token_debug_on(self) -> bool:
        """토큰 회계 진단 로그 on/off. 서버 옵션 `self.token_debug`(opts.json plugin_opts
        영속, 런타임 `token-debug on/off` 명령으로 토글, 기동 시 구 env PYTMUX_TOKEN_DEBUG
        폴백은 server_opts_init 이 처리). 이전엔 env 를 기동 1회 캐시했으나 **런타임 토글**을
        위해 옵션 속성 직접 참조로 바꿨다(속성 조회라 별도 캐시 불필요)."""
        return bool(getattr(self, "token_debug", False))

    def _log_token_debug(self, pane: Pane, tab: Tab, *, state, busy: bool,
                         running, peak_before: int, committed: int,
                         reset: bool = False):
        """env-gated(`PYTMUX_TOKEN_DEBUG`) 토큰 회계 진단을 `<sock>.tokendbg.jsonl`
        에 append(한 줄=한 step). 10-D(토큰 사용량 과소표시) 판정용 — `step()` 의
        running/peak/committed 와 **스캔 간격**(dt, 직전 step 과의 초)을 라이브로
        며칠치 남겨, 과소집계가 footer 스크랩의 설계 한계인지(턴당 스트리밍 peak 만
        보임) record-경로 버그인지(전이성 None 에 진행 중 peak 유실 등) 가린다.
        `reset=True` 는 세션 종료로 peak 가 버려지는 프레임(미확정 유실 후보).
        기본 OFF 라 평시 성능/디스크 무영향. best-effort — 로깅이 본 스캔/표시
        흐름을 절대 막지 않는다(_log_error 와 동일 정책)."""
        try:
            now = time.time()
            last = getattr(pane, "_tok_dbg_t", None)
            pane._tok_dbg_t = now
            rec = {
                "ts": round(now, 3),
                "dt": round(now - last, 3) if last else None,  # 스캔 간격(초)
                "pane": pane.id, "tab": tab.index,
                "sess": getattr(pane, "_claude_session_id", 0),
                "acct": getattr(pane, "_claude_account", None),
                "state": state, "busy": busy, "running": running,
                "peak0": peak_before,                       # step 전 peak
                "peak1": pane._tok_state.get("peak", 0),    # step 후 peak
                "total": pane._tok_state.get("total", 0),
                "committed": committed,
                "live": getattr(pane, "_session_tokens", 0),
            }
            if reset:
                rec["reset"] = True
            path = ipc.state_base(self.sock_path) + ".tokendbg.jsonl"
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _record_usage_snapshot(self, usage, source: str):
        """실측 `/usage` 한도 스냅샷을 SQLite limits 테이블에 적는다(S6 T1,
        docs/internal/TOKEN_ACCOUNTING_ACCURACY_SCENARIO.md). 호출처: 그림자 프로브
        (refresh_usage, source=probe)·인패널 /usage(panel)·footer 인라인 한도
        (inline). 직전 스냅샷과 동일값이면 insert_limits 가 skip 해 '값이 바뀐
        순간'만 이력에 남는다. 실패는 조용히 무시(표시/스캔 본 흐름 비차단)."""
        if not isinstance(usage, dict):
            return
        conn = self._tokens_db_conn()
        if conn is None:
            return
        try:
            usagedb.insert_limits(
                conn, usagedb.snap_from_usage(usage, time.time(), source))
        except Exception:
            # #28: sqlite 오류는 insert_limits 가 자체 흡수(False)하므로 여기 오는
            # 건 변환/계약 버그 — 조용히 잃지 말고 흔적을 남긴다(본 흐름 비차단).
            self._log_error("usage_snapshot")

    def _carry_tokens_on_close(self, pane: Pane):
        """닫히는 Claude 패널의 확정 토큰을 **같은 계정의 살아있는 패널**로 이관한다
        (#20). 그래야 계정 합계가 패널 하나 닫혔다고 줄지 않고, 같은 계정의 Claude
        Code 가 전부 닫힐 때까지 유지된다(살아남는 패널이 없으면 자연히 사라진다).
        진행 중(미확정) peak 는 응답이 끊긴 것이므로 이관하지 않는다. (S5 토큰 모듈화 T4
        에서 코어 servertree 에서 이전 — pane_closing 훅으로 호출된다.)"""
        tok = (pane._tok_state.get("total", 0)
               if getattr(pane, "_tok_state", None) else 0)
        acct = getattr(pane, "_claude_account", None)
        if not tok or not acct:
            return
        for p in self._all_panes():
            if p is not pane and getattr(p, "_claude_account", None) == acct:
                p._tok_state["total"] = p._tok_state.get("total", 0) + tok
                p._session_tokens = (p._tok_state["total"]
                                     + p._tok_state.get("peak", 0))
                return

    def set_claude_turn_warn(self, long_sec=None, repeat=None):
        """M17 표시 경고 임계 설정(0=끔). long_sec=장기 턴 초, repeat=동일 완료 반복
        횟수. None 인자는 변경 안 함. opts.json 영속."""
        if long_sec is not None:
            try:
                self.claude_long_turn_sec = max(0, int(long_sec))
            except (TypeError, ValueError):
                pass
        if repeat is not None:
            try:
                self.claude_repeat_alert = max(0, int(repeat))
            except (TypeError, ValueError):
                pass
        self._save_opts()
        return (self.claude_long_turn_sec, self.claude_repeat_alert)

    def set_claude_budget_plan(self, value=None):
        """예산 압박 시 plan 유도(M13) 토글. value 미지정 시 반전. 끄면 다음 프레임에
        한 번 재스캔(켤 때와 대칭)해 cam 카운터를 리셋. opts.json 영속."""
        self.claude_budget_plan = (not self.claude_budget_plan) \
            if value is None else bool(value)
        for p in self._all_panes():
            self._perm_reset(p)
            p._scan_seq = -1
        self._save_opts()
        return self.claude_budget_plan

    def set_claude_model_hint(self, value=None):
        """M14c 모델 과선택 힌트(알림만) 토글. value 미지정 시 반전. 끄면 잔존 힌트
        배지를 즉시 거두고(다음 status 에 None 송출), 켜면 다음 idle 완료 경계에서
        재평가된다. opts.json 영속(plugin_opts)."""
        self.claude_model_hint = (not self.claude_model_hint) \
            if value is None else bool(value)
        if not self.claude_model_hint:
            for p in self._all_panes():
                p._model_tip = None
        self._save_opts()
        return self.claude_model_hint

    # ---- S6 T4: 실측(/usage) 한도 게이트 — 자동개입 보류의 1차 기준 ----
    def _usage_fresh(self) -> bool:
        """마지막 실측이 신선한가 — 갱신 주기×2(자동 갱신 꺼져 있으면 20분) 이내.
        stale 실측으로 게이트가 오발동(이미 리셋된 창의 옛 % 로 보류)하지 않게 하는
        신선도 한계(시나리오 §6-3). 초과·부재면 False → 게이트 fail-open."""
        ts = getattr(self, "_usage_ts", None)
        if ts is None:
            return False
        limit = (self.usage_refresh_sec * 2
                 if self.usage_refresh_sec > 0 else 1200)
        return (time.time() - ts) <= limit

    def _usage_gate_pcts(self, pane: Pane):
        """게이트 판단에 쓸 (실측 세션 %, 실측 주간 %)|None — fail-open 조건을 한곳에:
        ① 실측 부재/stale ② 그림자 세션 계정과 패널 계정이 **둘 다 알려져 있고
        다름**(다른 계정의 한도로 이 패널을 막지 않는다 — 한쪽이라도 미상이면 같은
        로그인으로 보고 적용). None 이면 게이트는 개입하지 않는다(fail-open —
        지어내지 않음 원칙의 판단층 버전)."""
        u = self._usage
        if not isinstance(u, dict) or not self._usage_fresh():
            return None
        ua = u.get("account")
        pa = getattr(pane, "_claude_account", None) if pane is not None else None
        if ua and pa and ua != pa:
            return None
        def pct(key):
            d = u.get(key)
            return d.get("pct") if isinstance(d, dict) else None
        return (pct("session"), pct("week_all"))

    def _usage_gate_over(self, pane: Pane) -> bool:
        """실측 세션/주간 % 가 게이트 임계(usage_gate_session_pct 기본 95 /
        usage_gate_week_pct 기본 0=끔) 이상인가. 자동개입(자동재개 등) 보류 판단
        전용 — 하드 차단 아님. 임계 0=그 축 끔."""
        gs, gw = self.usage_gate_session_pct, self.usage_gate_week_pct
        if gs <= 0 and gw <= 0:
            return False
        pcts = self._usage_gate_pcts(pane)
        if pcts is None:
            return False
        spct, wpct = pcts
        if gs > 0 and spct is not None and spct >= gs:
            return True
        if gw > 0 and wpct is not None and wpct >= gw:
            return True
        return False

    def _usage_gate_level(self, pane: Pane) -> int:
        """실측 기반 경고 레벨(0/80/100) — 절대 예산 레벨과 같은 눈금: 임계 도달
        =100, 임계의 80% 도달=80(예: 임계 95 → 76%부터 예고). 표시(상태줄 ⚠)와
        M13 plan 유도가 절대 예산과 동일하게 소비한다."""
        lvl = 0
        pcts = self._usage_gate_pcts(pane)
        if pcts is None:
            return lvl
        for pct, gate in zip(pcts, (self.usage_gate_session_pct,
                                    self.usage_gate_week_pct)):
            if gate > 0 and pct is not None:
                if pct >= gate:
                    lvl = max(lvl, 100)
                elif pct >= gate * 0.8:
                    lvl = max(lvl, 80)
        return lvl

    def set_usage_gate(self, session=None, week=None):
        """실측 한도 게이트 임계 설정(%, 0=끔). None 인자는 변경 안 함.
        opts.json(plugin_opts) 영속."""
        if session is not None:
            try:
                self.usage_gate_session_pct = max(0, min(100, int(session)))
            except (TypeError, ValueError):
                pass
        if week is not None:
            try:
                self.usage_gate_week_pct = max(0, min(100, int(week)))
            except (TypeError, ValueError):
                pass
        self._save_opts()
        return (self.usage_gate_session_pct, self.usage_gate_week_pct)

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
            self._auto_cc_mark(pane)   # 직전 compact 직후 쿨다운 시작
            self._pc_inject(pane, "/compact")
