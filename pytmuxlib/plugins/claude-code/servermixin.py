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
from pytmuxlib.protocol import write_msg   # 세션 종료 시 클라 auto_token_log 푸시
from . import tokens, transcript, usagedb, usagelog   # S5 T5: 토큰 DB 백엔드도 플러그인 소속(물리 이전)
from .claude import (claude_account, claude_account_full, claude_api_error,
                     claude_feedback_prompt, fmt_long_turn_badge,
                     fmt_unknown_update,
                     claude_input_box,
                     claude_model_badge,
                     claude_prompt, claude_prompt_marks, claude_perm_mode,
                     claude_remote_active, claude_remote_blocked,
                     claude_remote_menu,
                     claude_state, claude_usage,
                     claude_welcome, ctx_window_tokens, parse_inline_limit,
                     parse_reset_delay, parse_usage, screen_tail_key,
                     track_repeat)
from pytmuxlib.model import Pane, Session, Tab

# 종료 토큰요약 배치의 OS 분기(_emit_auto_token_log): Windows(ConPTY)는 conhost
# 화면버퍼가 권위라 Unix 식 스트림 주입이 프롬프트 재그리기에 덮인다 — 전용 경로
# (_emit_auto_token_log_windows)로 분기. 테스트가 monkeypatch 할 수 있게 모듈 상수.
_IS_WINDOWS = os.name == "nt"

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

# §10-F 세션 종료 토큰 요약 주입 재시도 창(프레임 수, 요청 2026-07-05). 종료 확정
# 프레임에 fg 가 아직 셸로 안 잡히면(tcgetpgrp/ps 일시 실패·한두 프레임 지연) 일회성
# 발화가 영영 유실됐다. 이 창 동안 매 스캔 fg 를 재확인해 셸 복귀가 잡히는 즉시 1회
# 주입한다. 거짓 종료(fg=claude/node)면 창이 소진되도록 확정 안 됨 → 화면 보호 유지.
_EXIT_TOKEN_RETRY = 20

# 모델 배지 디바운스(2026-06-22): claude_model(txt) 는 화면 **아무 위치**의 모델명
# 토큰(opus/sonnet/haiku…)을 잡으므로, opus 세션 중 화면에 haiku 가 일시 등장하면
# (Haiku 서브에이전트/Task 실행 표시·출력 텍스트의 'haiku' 언급·/model 메뉴 잔상)
# 배지가 잠깐 haiku 로 튀었다가 탭 전환 시 정정되던 오탐이 있었다. 첫 확정은 즉시,
# 그 뒤 모델 *변경*은 같은 새 값이 이만큼 연속 관측될 때만 반영해 한두 프레임
# 깜빡임(서브에이전트 전환)을 흡수한다(_HDR_CLAUDE_MISS 디바운스와 동형).
_MODEL_DEBOUNCE = 3

# §3.7 포맷 미인식 가시화: Claude 가 실행 중(fg 명령에 'claude')인데 화면 파서가
# 상태를 못 읽는 상태가 _FMT_UNKNOWN_SEC 초 지속되면 "포맷 미인식" 경고를 세운다.
# fg 검사(ps)는 비싸므로 인식 실패 패널에 한해 _FMT_CHECK_INTERVAL 초 간격으로만 한다.
_FMT_CHECK_INTERVAL = 5.0
# 일시적 추적 불가(스크롤·ssh 단말 조각화·짧은 미인식 깜빡임)에 경고가 바로 뜨지
# 않게 지속 요건을 넉넉히 둔다 — 진짜 포맷 변경(추적 영구 중단)만 가시화(요청 2026-06-18).
_FMT_UNKNOWN_SEC = 60.0
# 경고 문구(상태줄 경고 세그먼트로 표시 — _claude_warn 재사용). 아이콘은 각 warn 문자열이
# 직접 갖는다(렌더는 비부가) — 장기턴·반복·미인식 모두 ⚠(노란 세모로 통일).
_FMT_UNKNOWN_MSG = "⚠ Claude 포맷 미인식 — 추적 중단(버전 업데이트?)"
# 포맷 미인식 1회 진단 로그에 남길 화면 footer tail(최하단 비어있지 않은 줄) 범위.
# 다음에 Claude footer 형식이 또 바뀌면 error.log 만으로 새 형식을 보고 claude.py
# _IDLE_ANCHORS 를 갱신하기 위함. 화면 전체가 아닌 tail 만(노출/스팸 가드).
_FMT_LOG_TAIL_LINES = 6
_FMT_LOG_TAIL_COLS = 160


# 비활성 탭 Claude 완료 알림(#22) 플리커 방지(§10 #18): busy→idle 후 idle 이 연속
# 이만큼의 스캔 프레임 동안 안정돼야 "완료"로 친다. raw busy↔idle 깜빡임(footer 가
# 한 프레임 안 잡혀 idle 로 보였다 다시 busy)에 done 이 잘못 서서 탭이 잠깐 녹색이
# 되는 것을 막는다. 30Hz flush 기준 ~0.1초 — 진짜 완료 알림 지연은 미미하다.
_DONE_IDLE_FRAMES = 3
# §10-I Claude 화면 깨짐 자동 완화의 디바운스 간격(초). busy→idle 완료 경계마다 무조건
# redraw(SIGWINCH→전체 repaint) 하면 멀쩡한 정상 턴까지 한 프레임 번쩍여 깨짐보다 더
# 거슬리므로, 마지막 자동 redraw 후 최소 이만큼 지나야 다시 유발한다(flicker·_send_full
# 비용 억제). 근본원인(pyte SU/SD 미구현)은 p4 61614 로 별도 해결됐고, 이 완화는 그 외
# 미구현 시퀀스로 인한 발산의 바닥 안전망이라 보수적으로(드물게) 둔다.
_AUTO_REDRAW_DEBOUNCE_SEC = 10.0
# §10-D P3/P4 트랜스크립트 증분 적재 디바운스. _XC_TAIL_FRAMES = 주기적 테일 간격
# (30Hz 기준 ~1초) — 응답 종료(committed>0)엔 강제 테일하므로 정확 캡처는 이 주기와
# 무관하고, 이 값은 장기 세션·서브에이전트/compaction usage 의 stragglers 만 위한
# 보조 주기다. _XC_RESOLVE_FRAMES = 패널→jsonl 경로 재해석 간격(테일 단위) — lsof/ps 가
# 비싸 한 번 잡은 경로를 이만큼 테일 동안 캐시한다(재기동 시 경로 변경만 재해석).
_XC_TAIL_FRAMES = 30
_XC_RESOLVE_FRAMES = 60
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
# 진행을 막는 Claude 오버레이 자동 Dismiss 키 = Esc(\x1b). 현재 유일한 대상은 `/rc`
# 원격 제어 관리 메뉴(Continue/Disconnect/QR, "Esc to continue") — 응답 대기로 진행을
# 막아 Esc=Continue 를 첫 감지에 **딱 한 번** 주입한다(_scan_claude). 절대 재주입하지
# 않는다 — 이중 Esc=되감기(Rewind) 모달의 직접 원인이었다(첫 Esc 가 메뉴를 닫아도 feed
# 지연으로 화면이 아직 메뉴에 매칭되는 동안 Esc#2 가 나가면 Claude Code 가 이중 Esc 를
# Rewind 단축키로 해석해 모달로 진행을 막는다, 사용자 보고 2026-06-20). _rc_menu_active
# 디바운스로 메뉴 인스턴스당 1회만 쏜다.
#
# ★ 세션 피드백 프롬프트("How is Claude doing this session?")는 이 Esc 경로를 더 이상
# 타지 않는다(사용자 보고 2026-06-20): 단일 Esc 가 종종 Dismiss 대신 작동 중인 턴을
# interrupt 했다. 이 배너는 컴포저 **위**에 비모달로 떠 있어 안 닫아도 작업을 막지 않고,
# server_filter_rows(_blank_feedback_banner)가 화면에서 완전히 가려 키 주입이 불필요하다.
_FEEDBACK_DISMISS_KEY = b"\x1b"
# M17(T7) 경고 임계는 opt(server.py: claude_long_turn_sec 기본 600 / claude_repeat_alert
# 기본 3, 0=끔). 스캔의 warn 블록이 self.* 를 읽는다.


class ServerClaudeMixin:
    # ── 메서드 인덱스(LLM 부분 Read 용 — 이 단일 클래스는 큰 단일 클래스라 `grep '^    def '`
    #    의 class 축이 무력하다. 섹션→**앵커 메서드명**(정확한 위치는 그 이름을 `grep -n`
    #    으로 확정 — 행수/행범위는 드리프트가 잦아 일부러 안 적는다, 코드검수 2026-07-10):
    #    · 토큰 리밋 자동 재개/재시도      _maybe_schedule_resume … _fire_retry
    #    · 프롬프트 클리어/규칙 주입(#9/#27) set_prompt_clear … _pc_advance
    #    · 세션 상태·모델·auto 모드         _reset_token_session … set_claude_perm_mode
    #    · ★ 화면 스캔 상태기계             _scan_claude(단일 최대 메서드)
    #    · 종료 토큰요약 배치               _usage_exit_lines … _emit_auto_token_log_windows
    #    · 경고 이력(M17)                   _warnhist_path … _read_warn_history
    #    · 토큰 회계 표시(§10-D)            _tab_claude … _week_sonnet_pct
    #    · /usage 그림자 프로브·갱신        _probe_cwd … _after_usage_probe
    #    · 프롬프트 추적·토큰 DB(usagedb)   _track_prompt … _log_tokens
    #    · 트랜스크립트 회계(usage_xc)      _xc_resolve_path … _xc_totals_for_status
    #    · 토큰 디버그·스냅샷·종료 이월      set_token_debug … set_claude_turn_warn
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

    # ---- 프롬프트 점프(esc ctrl+↑/↓) ----
    def claude_jump_prompt(self, sess: Session, direction="up") -> bool:
        """활성 Claude 패널의 스크롤백에서 **이전/다음에 입력한 프롬프트** 위치로
        점프한다(사용자 요청 2026-07-15). 찾아서 스크롤했으면 True.

        찾은 프롬프트 줄은 뷰 **상단**에 놓는다 — 프롬프트는 한 턴의 시작이라 거기서
        아래로 읽어 내려가는 게 자연스럽다(검색이 히트를 가운데 두는 것과 다른 이유).

        앵커는 뷰에서 매번 다시 구하고 상태를 들지 않는다 — 검색(search_pane 의
        `_match_abs`)처럼 마지막 히트를 기억하면 사용자가 중간에 손으로 스크롤했을 때
        어긋난다. 규칙: 뷰 상단에 프롬프트가 **놓여 있으면**(=점프로 거기 서 있음)
        그 줄이 앵커고, 아니면(라이브 하단·손스크롤 중) 화면 **안**의 프롬프트도
        후보가 되도록 맨 아래를 앵커로 본다 — 안 그러면 하단에서 첫 ctrl+↑ 가 화면에
        보이는 **최신** 프롬프트를 건너뛰고 그 앞으로 가버린다.

        셸 패널은 대상이 아니다(`> ` 로 시작하는 인용·diff 를 프롬프트로 오인) —
        Claude 패널일 때만 동작한다."""
        win = sess.active_window
        if not win or not win.active_pane:
            return False
        p = win.active_pane
        if not getattr(p, "_claude", None):
            return False          # Claude 패널 아님 → 오점프 방지(셸 인용/diff)
        texts = self._pane_text_lines(p)
        marks = claude_prompt_marks(texts)
        if not marks:
            return False
        hist = p._history_len()
        view_top = max(0, hist - p.scroll)
        if direction == "up":
            # 뷰 상단에 서 있는 프롬프트가 있으면 그 앞, 아니면 화면 안 것부터.
            anchor = view_top if view_top in set(marks) else len(texts)
            cands = [i for i in reversed(marks) if i < anchor]
        else:
            cands = [i for i in marks if i > view_top]
        # 뷰가 실제로 움직이는 첫 후보로 간다. 맨 끝(라이브 화면) 근처 프롬프트는 그
        # 아래 줄이 한 화면보다 적어 상단까지 끌어올릴 수 없다(스크롤 클램프) — 그걸
        # '점프함' 으로 치면 ctrl+↑ 가 먹통처럼 보이므로 건너뛰고 다음 프롬프트로
        # 간다(어차피 그 프롬프트는 화면에 이미 보인다).
        for found in cands:
            scroll = max(0, min(hist, hist - found))
            if scroll != p.scroll:
                p.scroll = scroll
                p.dirty = True
                return True
        return False              # 그 방향에 갈 곳 없음(뷰 유지)

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
            # host 모드(Windows 기본)에선 child_pid=-1 이라 pty 프록시의 실제 셸 pid 로
            # 폴백한다(_pane_shell_pid) — 안 그러면 항상 None 으로 §3.7 미인식 판정이
            # 어긋난다.
            cmd = proc.foreground_command(self._pane_shell_pid(pane)) or ""
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
            # 미인식 화면의 footer tail 도 함께 남긴다 — 다음에 Claude footer 형식이
            # 또 바뀌었을 때 error.log 만으로 새 형식을 파악해 claude.py _IDLE_ANCHORS
            # 를 갱신하기 위함(요청 2026-06-25). 1회만(_fmt_logged 스팸 가드).
            self._log_error("claude_format_unrecognized",
                            self._fmt_unrecognized_detail(pane))
        elif not unknown:
            pane._fmt_logged = False
        if unknown != pane._fmt_unknown:
            pane._fmt_unknown = unknown
            return True
        return False

    def _fmt_unrecognized_detail(self, pane) -> str:
        """포맷 미인식(claude_state 가 busy/limit/idle 어느 것도 못 잡음) 화면의 footer
        영역을 진단 문자열로 — 최하단 비어있지 않은 줄을 repr 로(특수·비출력 글자 보존)
        남긴다. 다음에 Claude footer 형식이 또 바뀌면 이 로그만 보고 새 형식을 파악해
        claude.py `_IDLE_ANCHORS` 에 한 줄 추가로 대응할 수 있다(요청 2026-06-25).
        화면 전체가 아닌 tail 만(노출/스팸 가드), best-effort(로그가 본동작을 막지 않음)."""
        try:
            lines = [ln for ln in screen_text(pane.screen).splitlines()
                     if ln.strip()]
            tail = lines[-_FMT_LOG_TAIL_LINES:]
            body = "\n".join("  " + repr(ln[:_FMT_LOG_TAIL_COLS]) for ln in tail)
            return ("screen tail (footer 형식 진단 — claude.py _IDLE_ANCHORS 갱신용):\n"
                    + body)
        except Exception:
            return ""

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

    def set_auto_token_on_exit(self, value=None):
        """Claude **세션 종료** 시 토큰 사용량(/usage 한도)을 자동으로 보여 주는 토글.
        value 미지정 시 반전. opts.json(plugin_opts) 영속. 켜져 있으면 _scan_claude 가
        패널의 Claude 가 _HDR_CLAUDE_MISS 프레임 디바운스 뒤 진짜로 사라졌다고 확정하는
        순간(=진짜 세션 종료) 그 패널 **출력 스트림에 한도 요약을 주입**해 스크롤백에
        자연스럽게 흘려보낸다(팝업/모달 아님 — 요청 2026-06-18, 기본 ON)."""
        self.auto_token_on_exit = (not self.auto_token_on_exit) if value is None \
            else bool(value)
        self._save_opts()
        return self.auto_token_on_exit

    def set_claude_auto_redraw(self, value=None):
        """§10-I Claude 화면 깨짐(부분갱신 발산) 자동 완화 3-state. opts.json(plugin_opts)
        영속, 기본 "off". 모드:
          - off        : 비활성.
          - idle       : claude 패널 busy→idle **완료 경계**(idle 이 _DONE_IDLE_FRAMES
                         안정)마다 무조건 1회 SIGWINCH(winsize 토글)로 전체 repaint 유발
                         (_auto_redraw_pane, 디바운스).
          - corruption : 같은 완료 경계에서 **깨짐 신호가 보일 때만** repaint
                         (_claude_corruption_signal — flicker/비용을 더 줄인 보수 모드).
        busy 중엔 절대 유발하지 않고(진행 출력과 SIGWINCH 레이스 방지) claude 패널에만
        적용한다. 깨진 부분갱신 스냅샷을 깨끗한 전체 출력으로 덮는 **완화**다 — 근본은
        외부(앱≠단말)·이미 SU/SD 로 해결(p4 61614), 이건 바닥 안전망. value 미지정/""=
        다음 모드로 순환(off→idle→corruption→off), 명시 시 정규화(구 bool 호환)."""
        from . import REDRAW_MODES, norm_redraw_mode
        cur = norm_redraw_mode(getattr(self, "claude_auto_redraw", "off"))
        if value is None or value == "":
            self.claude_auto_redraw = REDRAW_MODES[
                (REDRAW_MODES.index(cur) + 1) % len(REDRAW_MODES)]
        else:
            self.claude_auto_redraw = norm_redraw_mode(value)
        self._save_opts()
        return self.claude_auto_redraw

    # 박스(프롬프트 테두리) 모서리 글리프 — 깨짐 감지(_claude_corruption_signal)용.
    _BOX_TOP = ("╭", "┌", "┏")      # 둥근/각/굵은 좌상단(우상단도 함께 옴)
    _BOX_BOTTOM = ("╰", "└", "┗")   # 좌하단

    def _claude_corruption_signal(self, txt: str) -> bool:
        """corruption 모드 깨짐 감지(보수적, **완료 idle 경계에서만** 호출). 두 신호:
        ① U+FFFD(�) — 디코드/렌더 손상의 명백한 마커.
        ② 박스 테두리 찢김 — 위(╭┌┏)·아래(╰└┗) 모서리 글리프 중 한쪽만 존재. 정상
           프레임은 프롬프트 박스가 온전(둘 다)하거나 아예 없어(둘 다 없음) 둘이 같이
           존재/부재한다. idle 로 안정된 화면에서 한쪽만 남은 건 부분갱신 발산(테두리
           소실)의 징후다 — 스트리밍 중간이 아닌 완료 경계에서만 보므로 오탐이 낮다.
        근본 해결(SU/SD) 이후엔 거의 발화하지 않는 게 정상(바닥 안전망)."""
        if "�" in txt:   # U+FFFD replacement char(디코드/렌더 손상)
            return True
        has_top = any(c in txt for c in self._BOX_TOP)
        has_bottom = any(c in txt for c in self._BOX_BOTTOM)
        return has_top != has_bottom

    def _auto_redraw_pane(self, p):
        """§10-I: 한 claude 패널 PTY 에만 SIGWINCH(winsize 1줄 토글)를 유발해 alt-screen
        앱이 현재 화면을 전체 repaint 하게 한다. serverpersist._induce_redraw_all 의
        단일-패널판 — 비-claude 패널의 불필요한 flicker 를 피한다. 치수(rows/cols)는
        불변이라 레이아웃(탭바·분할·커서)은 안 흔들리고, 앱의 repaint 출력은 평소
        feed→flush 경로로 클라에 전달된다(별도 _send_full 불필요). pty 부재/OSError 는
        조용히 무시(종료 중 패널)."""
        pty = getattr(p, "pty", None)
        if pty is None:
            return
        try:
            pty.set_winsize(max(1, p.rows - 1), p.cols)
            pty.set_winsize(p.rows, p.cols)
        except OSError:
            pass

    def _usage_exit_lines(self, width: int = 80, pane: Pane = None) -> list:
        """세션 종료 시 패널에 주입할 토큰 사용량 요약 **줄 목록**(ANSI 최소)을 만든다.

        **항상 안정적으로 표시**되게 두 정보를 조합한다(요청 2026-07-05 — 종전엔 그림자
        /usage 스냅샷이 없으면 통째로 b"" 라 '표시될 때도 안 될 때도' 있었다):
        - **세션 토큰 총량**(pane._session_tokens): 로컬 회계라 그림자 /usage 프로브 없이
          **항상 가용** → 종료 요약이 절대 비지 않게 하는 바닥값.
        - **/usage 한도 막대**(self._usage: session·week_all·week_sonnet 각 {pct,reset}):
          스냅샷이 있을 때만 곁들인다(없으면 토큰 총량만).
        둘 다 없으면(claude 무활동·pane=None) []. 팝업(usage_bar_lines)과 같은 정보지만
        모달 크롬 없이 스크롤 출력용 블록 막대로 만든다. 라벨은 코어 i18n(usage.*) 공유.
        `width`(패널 폭)에 맞춰 막대 길이를 늘린다. 줄바꿈/배치 래핑은 호출부가 OS 별로
        입힌다(_usage_exit_text=Unix 스트림, _emit_auto_token_log_windows=CUP 배치)."""
        from pytmuxlib import i18n

        def cells(s: str) -> int:   # 동아시아 와이드(한글/CJK)만 2폭으로 세는 최소 폭 계산
            return sum(2 if "가" <= c <= "힣" or "一" <= c <= "鿿"
                       or "　" <= c <= "〿" else 1 for c in s)

        # /usage 한도 막대 항목(그림자 스냅샷이 있을 때만)
        entries = []
        u = self._usage
        if isinstance(u, dict):
            for key, label in (("session", i18n.t("usage.session_5h")),
                               ("week_all", i18n.t("usage.week_all")),
                               ("week_sonnet", i18n.t("usage.week_sonnet"))):
                d = u.get(key)
                if isinstance(d, dict) and d.get("pct") is not None:
                    reset = d.get("reset")
                    reset_txt = ("  ↻" + reset.split(" (")[0].strip()) if reset else ""
                    entries.append((key, label, max(0, min(100, int(d["pct"]))),
                                    reset_txt))
        # 세션 토큰 총량(항상 가용한 바닥값 — 프로브 무관). _exit_tokens 는 claude None
        # 전이에서 _session_tokens 가 0 으로 리셋되기 **직전** 보존한 값이라, 종료 확정
        # (리셋 30프레임 뒤) 시점에도 총량이 살아 있다. 미보존 경로(직접 호출 등)는 라이브
        # _session_tokens 로 폴백.
        tok = 0
        if pane is not None:
            try:
                tok = int(getattr(pane, "_exit_tokens", 0)
                          or getattr(pane, "_session_tokens", 0) or 0)
            except (TypeError, ValueError):
                tok = 0
        if not entries and tok <= 0:
            return []                        # 표시할 게 전혀 없음(무활동) → 무동작

        # 한도 막대 렌더(있을 때). 막대 폭은 패널 폭에 맞춰 늘린다(고정폭 제외분, 10~60).
        rows = []
        if entries:
            label_w = max(cells(lbl) for _, lbl, _, _ in entries)
            reset_w = max(cells(rt) for _, _, _, rt in entries)
            fixed = 2 + label_w + 2 + 5 + reset_w + 2
            gauge_w = max(10, min(60, int(width) - fixed))
            for key, label, pct, reset_txt in entries:
                filled = (pct * gauge_w + 50) // 100         # 패널폭 막대(반올림)
                gauge = "█" * filled + "░" * (gauge_w - filled)
                # 세션 5h 행은 이번 세션이 가장 즉시 보는 값 → 노란색 강조.
                if key == "session":
                    gauge = f"\x1b[33m{gauge}\x1b[0m"
                pad = " " * (label_w - cells(label) + 2)
                rows.append(f"  {label}{pad}{gauge} {pct:>3}%{reset_txt}")
            sep_w = max(10, min(2 + label_w + 2 + gauge_w + 5 + reset_w, int(width)))
        else:
            sep_w = max(10, min(48, int(width)))
        sep = "─" * sep_w
        lines = ["", f"\x1b[2m{sep}\x1b[0m",
                 f"\x1b[1m{i18n.t('usage.exit_title')}\x1b[0m"]
        if tok > 0:              # 세션 토큰 라인(항상 곁들임 — 한도 스냅샷 유무 무관)
            lines.append(f"  {i18n.t('usage.exit_session_tokens')} "
                         f"\x1b[1m{tok:,}\x1b[0m")
        lines.extend(rows)
        lines.append(f"\x1b[2m{sep}\x1b[0m")
        return lines

    def _usage_exit_text(self, width: int = 80, pane: Pane = None) -> bytes:
        """_usage_exit_lines 의 **Unix 스트림 판** 래핑(표시할 게 없으면 b"").
        셸이 이미 찍어 둔 프롬프트 줄을 **덮어쓰고** 그 자리에 블록을 흘린다(요청
        2026-06-20): \\r 로 줄 처음으로 간 뒤 \\x1b[J(커서~화면끝 지우기)로 빈 프롬프트
        줄을 치우고 블록을 그린다. 끝에 개행을 두지 않아, _emit_auto_token_log 가 뒤이어
        셸에 보내는 Enter 의 \\r\\n 한 번이 블록 **바로 아래** 새 프롬프트를 그린다 →
        토큰 표시가 먼저, 프롬프트·커서가 그다음 순서로 정렬된다."""
        lines = self._usage_exit_lines(width, pane)
        if not lines:
            return b""
        return ("\r\x1b[J" + "\r\n".join(lines)).encode("utf-8")

    def _inject_pane_output(self, pane: Pane, data: bytes) -> None:
        """합성 바이트를 패널 **출력** 스트림에 주입한다(에뮬레이터 feed → 렌더·스크롤).
        PTY 입력(_pc_inject/_inject_keys)과 달리 자식 stdin 이 아니라 화면 모델로
        들어가 정상 출력처럼 보인다. 진행 중 드레인이 있으면 그 큐에 이어 붙여 순서를
        보존하고, 없으면 즉시 ingest 한다(_on_pane_data 의 소량 인라인 경로와 동일)."""
        if pane._feed_task is not None:
            pane._feedbuf += data
        else:
            self._ingest_slice(pane, data)

    # 종료 확정 시 토큰 로그를 주입해도 안전한 셸 포그라운드 이름들. _claude_really_exited
    # 가 거짓 종료(긴 출력이 Claude footer 를 샘플 밖으로 밀어 claude_state→None 30프레임)와
    # 진짜 종료를 교차검증하는 데 쓴다.
    # Windows 셸(cmd·powershell·pwsh)도 포함한다 — Windows 기본 셸은 COMSPEC(대개
    # cmd.exe)이고 foreground_command 가 `.exe` 를 떼 "cmd"/"powershell" 를 준다.
    # "cmd" 누락 시 Claude 종료 후 fg=cmd 를 셸로 못 알아봐 세션종료 토큰요약이 영영
    # 안 떴다(Windows 실박스 보고 2026-07-08).
    _SHELL_FG = frozenset({"zsh", "bash", "sh", "fish", "dash", "ksh", "tcsh",
                           "csh", "ash", "pwsh", "nu", "nushell", "xonsh",
                           "elvish", "oil", "osh", "powershell", "cmd"})

    def _claude_really_exited(self, pane: Pane) -> bool:
        """패널이 정말로 Claude→셸 로 복귀했는지 포그라운드 프로세스로 교차검증한다.
        화면 스크레이프(_hdr_claude 디바운스)만으론 거짓 종료가 난다 — 긴 출력이 Claude
        footer 를 샘플 화면 밖으로 밀면 살아있는 Claude 도 claude_state→None 이 30프레임
        이어져 '종료'로 확정되고, 그 순간 토큰 그래프가 살아있는 TUI 한가운데 주입돼 화면이
        깨진다(사용자 보고 2026-06-18). fg 가 알려진 셸이면 Claude 가 빠져 셸로 돌아온 것
        → 주입 안전(그래프가 새 프롬프트 위 스크롤백에 흐른다). fg 가 여전히 Claude
        (node/claude)거나 확인 불가(None)면 False → 주입 건너뜀(화면 보호 우선)."""
        fg = self._fg_command(pane)
        return bool(fg) and os.path.basename(fg).lower() in self._SHELL_FG

    def _emit_auto_token_log(self, sess: Session, pane: Pane = None) -> None:
        """Claude **세션 종료** 확정 시 그 패널에 토큰 사용량 요약을 **출력으로 주입**한다
        — 팝업(모달)이 아니라 패널 스크롤백에 자연스럽게 흘러가도록(요청 2026-06-18).
        Claude 가 막 빠져 패널은 셸 프롬프트 상태이므로 합성 텍스트를 _ingest_slice 로
        먹이면 정상 출력처럼 렌더·스크롤된다. 요약은 세션 토큰 총량(항상 가용) + 한도 막대
        (스냅샷 있을 때)로, 그림자 /usage 가 없어도 비지 않는다(_usage_exit_text, 요청
        2026-07-05). 세션 토큰도 0·pane 도 없으면 무동작. _scan_claude 종료 예약
        (_exit_token_pending)이 fg=셸 확정 시 종료당 1회 호출한다(중복 주입 없음)."""
        if pane is None:
            return
        if _IS_WINDOWS:
            # Windows(ConPTY)는 conhost 화면버퍼가 권위라 아래 Unix 방식(주입 후
            # Enter 1회)이 성립하지 않는다 — 전용 배치 경로로 분기.
            self._emit_auto_token_log_windows(pane)
            return
        text = self._usage_exit_text(getattr(pane, "cols", 80), pane)
        if text:
            self._inject_pane_output(pane, text)
            # 블록은 셸 프롬프트 줄을 덮어쓰며 그려졌다(_usage_exit_text). 이제 셸이
            # 블록 **아래**에 프롬프트를 새로 그리도록 Enter 1회를 PTY 로 보낸다. 출력만
            # 주입하면 셸 라인에디터(ZLE)의 커서 모델이 화면과 블록 높이만큼 어긋나
            # 다음 입력이 엉뚱한 위치에 찍힌다 — 빈 줄 Enter 는 셸이 현재 커서 자리에
            # 프롬프트를 새로 그려 동기화를 회복한다(claude 종료 직후라 입력 버퍼는
            # 비어 무해; pty=None 인 테스트 패널에선 _inject_keys 가 조용히 무동작).
            self._inject_keys(pane, b"\r")

    # Windows 종료요약 배치: Enter 펌프 후 셸 프롬프트 echo 가 정착하길 기다리는 지연(초).
    # 로컬 셸의 빈-Enter 프롬프트 재출력은 수십 ms — 0.4s 면 여유(테스트에서 단축 가능).
    _EXIT_LOG_WIN_SETTLE = 0.4

    def _emit_auto_token_log_windows(self, pane: Pane) -> None:
        """Windows(ConPTY) 판 종료 토큰요약 배치(실박스 보고 2026-07-09).

        Unix 처럼 '블록을 출력 주입 후 Enter 1회'를 쓰면 안 된다 — ConPTY 의 conhost 는
        자기 화면버퍼가 권위라 우리가 에뮬레이터에만 주입한 블록을 모르고, 셸 프롬프트를
        **자기 커서 기준 절대좌표**로 다시 그려 블록 윗줄부터 잠식한다(Enter 칠 때마다
        한 줄씩 덮임). 대신:
          ① 블록 높이+1 만큼 Enter 를 펌프해 conhost 커서(=셸 프롬프트)를 블록 높이만큼
             아래로 내린다. 빈 Enter 라 셸은 프롬프트만 다시 그린다(claude 종료 직후라
             입력 버퍼는 비어 무해; pty=None 테스트 패널은 _inject_keys 가 무동작).
          ② 프롬프트 echo 정착 뒤(_EXIT_LOG_WIN_SETTLE) 에뮬레이터 커서 **위** 의
             비워진 행들(펌프가 남긴 빈 프롬프트 줄)에 CUP+\\x1b[2K 로 블록을 그린다
             (DECSC/DECRC 로 커서 보존) — 화면 행 이동이 없어 conhost 좌표계와 안
             어긋나고, 새 프롬프트·커서가 블록 **아래**라 이후 타이핑/Enter 가 블록을
             덮지 않는다. 이후 출력으로 스크롤되면 블록도 함께 스크롤백으로 흘러간다.
        한계(용인): conhost 가 그 영역을 자기 버퍼로 전체 repaint 하면(리사이즈 등)
        블록 자리엔 펌프가 남긴 빈 프롬프트 줄들이 되살아난다 — ConPTY 화면버퍼 권위
        구조상 합성 출력은 최종적으로 transient 다."""
        lines = self._usage_exit_lines(getattr(pane, "cols", 80), pane)
        while lines and not lines[0]:      # 선두 공백줄은 Enter 펌프가 이미 여백을 준다
            lines.pop(0)
        if not lines:
            return
        self._inject_keys(pane, b"\r" * (len(lines) + 1))

        def _place():
            try:
                cy = pane.screen.cursor.y          # 0-based 현재(새) 프롬프트 행
            except AttributeError:                 # 패널 정리 중(스크린 해제)
                return
            if cy <= 0:                            # 최상단 — 위에 그릴 공간 없음
                return
            use = lines[-cy:] if cy < len(lines) else lines
            top = cy - len(use)
            seq = "\x1b7" + "".join(
                f"\x1b[{top + i + 1};1H\x1b[2K{ln}" for i, ln in enumerate(use)
            ) + "\x1b8"
            self._inject_pane_output(pane, seq.encode("utf-8"))

        self.loop.call_later(self._EXIT_LOG_WIN_SETTLE, _place)

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

    def _claude_composer_text(self, p):
        """Claude 라이브 입력박스(컴포저)에 지금 들어 있는 텍스트를 서버 화면에서
        긁는다(best-effort). 빈 박스면 "", 입력박스를 못 찾으면 None. 자동 주입
        (`/rename` 등) 전에 사용자가 타이핑 중인 입력을 덮지 않으려는 게이트로 쓴다
        — claude-code 의 클라측 client_prompt_text 와 같은 claude_input_box 파서를
        서버 스크린(screen_text 행 + 하드웨어 커서 행)으로 돌린다."""
        try:
            lines = screen_text(p.screen).split("\n")
            cy = p.screen.cursor.y
        except Exception:
            return None
        return claude_input_box(lines, (), cy)

    def _process_exit_token_pending(self, sess, p) -> None:
        """§10-F 종료 토큰 주입 예약 처리(_scan_claude 에서 추출 — 로드맵 God-함수
        분할 1차, 동작 불변). 예약(_exit_token_pending>0)이 살아 있고 헤더신호가 내려간
        (not _hdr_claude) 상태에서, fg 가 셸로 확정되면 그 즉시 1회 주입 후 해제한다.
        확정 안 되면(fg=claude/node·미상) 창을 줄여 거짓 종료는 소진돼 사라지고 진짜
        종료는 셸 복귀가 잡히는 순간 발화한다(claude 재등장은 호출부에서 예약 취소).
        단, 셸 복귀는 확정됐으나 사용자가 이미 다음 명령을 치는 중(_inbuf 비지 않음)이면
        주입을 **보류**한다 — 지금 주입하면 종료요약의 Enter 펌프(Unix 1회·Windows
        블록높이+1)가 사용자가 치던 부분 명령을 그대로 제출한다(코드검수 2026-07-10 S-2).
        재시도 카운터를 소진시켜, 창 안에 제출/클리어(빈 _inbuf)하면 발동하고 계속 치면
        만료돼 조용히 유실한다(요약은 부가정보라 사용자 입력 보호 우선)."""
        if not (p._exit_token_pending > 0 and not p._hdr_claude):
            return
        if not self._claude_really_exited(p):
            p._exit_token_pending -= 1
        elif getattr(p, "_inbuf", ""):
            p._exit_token_pending -= 1
        else:
            self._emit_auto_token_log(sess, p)
            p._exit_token_pending = 0

    def _update_claude_model(self, p, txt) -> bool:
        """모델 배지/프로브 감지 + 디바운스(_scan_claude 에서 추출 — 로드맵
        God-함수 분할, 동작 불변). 화면 배지(claude_model_badge)를 우선하고 없으면
        그림자 /usage 프로브 모델로 폴백, 강한(배지)/약한(프로브) 값 구분과
        _MODEL_DEBOUNCE 로 서브에이전트 깜빡임을 흡수한다. 모델이 바뀌면 True."""
        ch = False
        mdl = claude_model_badge(txt)
        mdl_from_probe = False
        if not mdl:
            # 폴백/정정(2026-06-22·2026-07-04): 현행 Claude 푸터는 모델
            # 배지를 상시 표시하지 않아(idle 푸터엔 'auto mode on …'뿐)
            # 라이브 배지가 화면에 없는 게 정상이다. 그럴 땐 그림자 /usage
            # 프로브가 /status 로 잡은 실 모델(팝업 모델 행과 동일 권위 출처)
            # 로 채운다. **배지가 화면에 있을 때만** 위 mdl(strong)이 이를
            # 즉시 덮으므로 /model 변경은 배지로 곧장 반영되고, 배지가 없는
            # 동안은 프로브가 권위다. 종전엔 `_claude_model is None`(첫 확정)
            # 에서만 폴백해, 본문 언급 오검출로 한번 굳은 값(예 'fable-5')이
            # 프로브가 opus 를 알아도 세션 경계까지 안 풀렸다 — 조건을 풀어
            # 배지 부재 시 프로브가 강한 값도 디바운스 경유로 정정하게 한다
            # (사용자 보고 2026-07-04). 프로브 미상이면 마지막 값 유지.
            pm = (self._usage.get("model")
                  if isinstance(self._usage, dict) else None)
            if pm:
                mdl = pm
                mdl_from_probe = True
        if mdl and mdl != p._claude_model:
            # 디바운스(2026-06-22, 모델 배지 haiku 튐 오탐 수정): opus 중
            # 화면에 haiku 가 한두 프레임 떠도(Haiku 서브에이전트/Task 출력·
            # 모델명 언급·/model 메뉴 잔상) 즉시 안 바꾼다.
            #  · 첫 확정(None) 또는 **약한**(프로브 폴백) 값 위 → 즉시 반영
            #    (라이브 배지가 /model 변경을 곧장 보이게 — 약한 프로브값을
            #    라이브 배지가 디바운스 없이 덮는다).
            #  · **강한**(라이브 배지) 값 위의 *변경*만 _MODEL_DEBOUNCE 회
            #    연속 관측될 때 반영(서브에이전트 깜빡임 흡수).
            if p._claude_model is None or p._claude_model_weak:
                p._claude_model = mdl
                # 라이브 스크랩이면 strong, 프로브 폴백이면 weak.
                p._claude_model_weak = mdl_from_probe
                ch = True
                p._claude_model_cand = None
                p._claude_model_cand_n = 0
            else:
                if mdl == p._claude_model_cand:
                    p._claude_model_cand_n += 1
                else:
                    p._claude_model_cand = mdl
                    p._claude_model_cand_n = 1
                if p._claude_model_cand_n >= _MODEL_DEBOUNCE:
                    p._claude_model = mdl
                    p._claude_model_weak = False  # 디바운스 통과=라이브
                    ch = True
                    p._claude_model_cand = None
                    p._claude_model_cand_n = 0
        elif mdl and mdl == p._claude_model:
            # 현재 확정값 재확인 → 후보 리셋(깜빡임이 끊기면 다시 처음부터
            # 세도록). opus→haiku(1프레임)→opus 면 haiku 후보가 1에서 리셋.
            # 라이브 스크랩으로 재확인되면 strong 승격(이후 변경은 디바운스).
            if not mdl_from_probe:
                p._claude_model_weak = False
            p._claude_model_cand = None
            p._claude_model_cand_n = 0
        return ch

    def _scan_warnings(self, p, new_cl) -> bool:
        """M17 표시 경고(grade0 — 알림만, 개입 없음) 갱신 phase.

        `_scan_claude` 에서 추출(로드맵 #1 God-분할, 동작 불변). 우선순위는 S9 장기 턴
        > S8 반복 루프 > §3.7 포맷 미인식이며, 임계는 opt(0=끔). 새 경고 **종류**의
        onset 만 이력에 1건 남긴다(같은 kind 가 이어지는 동안 dedup — long_turn 배지의
        초는 매초 바뀌어도 kind 는 그대로). 상태가 바뀌면 True.
        """
        # S9 장기 턴 우선, 아니면 S8 반복 루프. 세션 종료 시 상태 리셋.
        changed = False
        warn = None
        warn_kind = None        # 구조적 종류(클라가 로케일별 렌더에 사용)
        warn_n = None           # 반복 종류일 때 반복 횟수(그 외 None)
        lt = self.claude_long_turn_sec
        ra = self.claude_repeat_alert
        if (lt > 0 and new_cl == "busy" and p._busy_since is not None):
            el = time.monotonic() - p._busy_since
            if el >= lt:
                # 장기 턴 경고: 경고 삼각형(⚠) + 경과 시간. 기본 분:초, 1시간
                # 이상이면 시:분(사용자 요청 2026-06-17, fmt_long_turn_badge).
                # 초가 매초 바뀌므로 정수 초 경계에서만 changed. 배지 문자열은
                # 언어중립(숫자만)이라 클라가 그대로 쓴다.
                warn = fmt_long_turn_badge(el)
                warn_kind = "long_turn"
        elif ra > 0 and new_cl == "idle" and p._repeat_n >= ra:
            warn_n = p._repeat_n + 1
            warn = f"⚠ 동일 결과 {warn_n}회 반복 — 루프 의심"
            warn_kind = "repeat"
        if not new_cl:
            p._busy_since = None
            p._repeat_n = 0
            p._done_tail = None
        # §3.7: 포맷 미인식이면 ⚠ 경고로 가시화(다른 경고가 없을 때만 — 미인식은
        # new_cl=None 이라 위 long-turn/repeat 와 상호배타지만 안전하게 or 사용).
        if p._fmt_unknown and warn is None:
            warn = _FMT_UNKNOWN_MSG
            warn_kind = "fmt_unknown"
        if (warn != p._claude_warn
                or warn_kind != p._claude_warn_kind):
            prev_kind = p._claude_warn_kind
            p._claude_warn = warn
            p._claude_warn_kind = warn_kind
            p._claude_warn_n = warn_n
            changed = True
            # 항목2(2026-06-22): 새 경고 종류의 **onset**(이전과 다른 non-None
            # kind)을 이력에 1건 기록한다 — [경고] 탭이 과거 경고를 트리로
            # 펼쳐 보이게. 같은 kind 가 이어지는 동안(long_turn 배지 초가
            # 매초 바뀌어도 kind 는 그대로)엔 재기록하지 않아 dedup 된다.
            if warn_kind is not None and warn_kind != prev_kind:
                self._record_warn_history(p, warn, warn_kind, warn_n,
                                          time.time())
        return changed

    def _scan_rc_signals(self, p, txt) -> None:
        """`/rc`(원격 제어) 관련 화면 신호 phase — 관리 메뉴 자동 Dismiss·조직 정책
        차단 감지·'이미 켜짐' sticky 관측.

        `_scan_claude` 에서 추출(로드맵 #1 God-분할, 동작 불변). 상태(changed)를 바꾸지
        않는다 — 여기서 세우는 건 전부 **서버/패널 내부 플래그**(클라 표시 무관)라
        원 체인도 changed 를 안 건드렸다.
        """
        # `/rc` 원격 제어 관리 메뉴(Continue/Disconnect/QR) 자동 Dismiss: 이
        # 메뉴는 "Esc to continue" 응답 대기로 **진행을 막으므로**(auto-launch 가
        # 새 세션마다 /rc 를 1회 주입, 사용자 보고 2026-06-18) Esc=Continue 를
        # 첫 감지에 **딱 한 번** 주입해 치운다(원격은 켜진 채 메뉴만 닫혀 자동화가
        # 이어진다). 절대 재주입하지 않는다(이중 Esc=Rewind 모달 차단, 위 상수
        # 주석) — _rc_menu_active 로 메뉴가 사라질 때까지 디바운스한다. 메뉴 출현은
        # claude_remote_active 분기가 _rc_done 도 세워 같은 세션에 /rc 가 재발하지
        # 않는다.
        #
        # ★ 세션 피드백 프롬프트("How is Claude doing this session?")는 더 이상
        # Esc 를 주입하지 않는다(사용자 보고 2026-06-20): 단일 Esc 가 종종 Dismiss
        # 대신 작동 중인 턴을 interrupt 했다 — busy 중 배너 텍스트가 화면에 매칭
        # 되거나 feed 지연으로 stale 매칭이 남으면 Esc 가 interrupt 키로 해석된다.
        # 이 배너는 비모달이라 안 닫아도 컴포저를 막지 않고(사용자의 다음 Enter/
        # Space 가 자연히 닫는다), server_filter_rows(_blank_feedback_banner)가
        # 배너를 화면에서 완전히 가린다 — 키 주입 없는 표시 필터만으로 충분하다.
        if claude_remote_menu(txt):
            if not p._rc_menu_active:
                p._rc_menu_active = True
                if p.pty is not None:
                    try:
                        p.pty.write(_FEEDBACK_DISMISS_KEY)
                    except OSError:
                        pass
        else:
            p._rc_menu_active = False
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

    def _scan_usage_capture(self, txt) -> bool:
        """패널에 뜬 실측 /usage 한도를 권위값(self._usage)으로 캡처하는 phase.

        `_scan_claude` 에서 추출(로드맵 #1 God-분할, 동작 불변). ① /usage 패널(전체)
        ② footer 인라인 한도(부분) 중 무엇이든 보이면 캡처해 상태줄 5h% 가 추정치 대신
        실측을 따르게 한다. 그림자 프로브가 붙여 둔 account/model 은 인패널 갱신이
        덮지 않게 보존한다(인라인 parse 엔 그 두 키가 없다). 값이 바뀌면 True.
        """
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
        # panel_now: /usage 패널(전체)을 봤는가 — 아래 한도 스냅샷 출처 라벨
        # ("panel" vs "inline")에 쓴다. (인패널 /usage 자동 팝업 신호 seq 는
        # 2026-06-17 제거 — 사용자가 이미 Claude /usage 패널을 보고 있는데 같은
        # 내용을 전용 모달로 덮는 게 불필요·방해라서. 수동 usage-panel 명령과
        # 그림자 /usage 질의·실측 캡처는 그대로 유지. §3.9)
        panel_now = new_usage is not None
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
            # 모델도 동일하게 보존(2026-06-22) — parse_usage/인라인엔 model 이
            # 없어, 보존 안 하면 인패널 갱신이 프로브 model 폴백을 지워
            # _scan_claude 의 model 폴백(self._usage['model'])이 끊긴다.
            if isinstance(self._usage, dict) and self._usage.get("model") \
                    and "model" not in new_usage:
                new_usage["model"] = self._usage["model"]
            self._usage = new_usage
            self._usage_ts = time.time()   # S6 T3: 신선도(표시·게이트)
            # S6 T1: 실측 한도 스냅샷 이력화 — 값이 바뀐 순간만 적힌다
            # (insert_limits 가 직전과 동일값이면 skip). 이 분기는
            # new_usage != self._usage 일 때만 오므로 30Hz 스캔 부담 없음.
            self._record_usage_snapshot(
                new_usage, "panel" if panel_now else "inline")
            changed = True
            return True
        return False

    def _scan_done_and_redraw(self, p, t, w, win, old_cl, new_cl, txt) -> bool:
        """busy→안정 idle 완료 처리 phase — idle 프레임 누적·§10-I 자동 리드로우·
        비활성 탭 완료 알림(#22).

        `_scan_claude` 에서 추출(로드맵 #1 God-분할, 동작 불변). 플리커 방지로 raw
        busy→idle 즉시가 아니라 idle 이 _DONE_IDLE_FRAMES 프레임 연속 안정될 때만 완료로
        친다. 자동 리드로우는 `== _DONE_IDLE_FRAMES` 로 완료 경계 **한 프레임**에만 걸려
        idle 이 정적으로 머무는 동안 반복 유발하지 않는다. 상태가 바뀌면 True.
        """
        changed = False
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
        # §10-I Claude 화면 깨짐 자동 완화(opt-in, 기본 off). busy→idle 완료가
        # 막 _DONE_IDLE_FRAMES 로 안정된 경계에서, claude 패널에 1회 SIGWINCH
        # (winsize 토글)를 유발해 부분갱신 발산을 전체 repaint 로 덮는다. 가드:
        # busy 중 금지(idle 확정에서만 — 진행 출력과 레이스 방지)·디바운스
        # (flicker/_send_full 비용 억제)·claude 패널 한정(new_cl=='idle')·
        # 활성/비활성 무관(둘 다 깨질 수 있어 아래 done 블록보다 먼저, _was_busy
        # 가 살아 있을 때 본다). `== _DONE_IDLE_FRAMES` 로 완료 경계 한 프레임에만
        # 걸려 idle 이 정적으로 머무는 동안 반복 유발하지 않는다(다음 busy→idle
        # 마다 다시 1회). 근본원인(pyte SU/SD)은 p4 61614 로 해결됐고 바닥 안전망.
        # 모드: idle=완료마다 무조건 / corruption=깨짐 신호 보일 때만(flicker 억제).
        # 디바운스 타임스탬프는 **실제 유발할 때만** 갱신 — corruption 모드에서
        # 안 깨진 경계가 디바운스 창을 소진해 다음 깨짐을 지연시키지 않게.
        if (self.claude_auto_redraw != "off"
                and p._was_busy and new_cl == "idle"
                and p._idle_frames == _DONE_IDLE_FRAMES):
            nowm = time.monotonic()
            if (nowm - p._auto_redraw_ts >= _AUTO_REDRAW_DEBOUNCE_SEC
                    and (self.claude_auto_redraw == "idle"
                         or self._claude_corruption_signal(txt))):
                p._auto_redraw_ts = nowm
                self._auto_redraw_pane(p)
        # 비활성 탭 완료 알림(#22, 위 주석): busy 를 본 적 있고 idle 이
        # _DONE_IDLE_FRAMES 안정되면 done 으로 확정(탭 녹색).
        if (w is not win and t.monitor_claude and not t.has_claude_done
                and p._was_busy and new_cl == "idle"
                and p._idle_frames >= _DONE_IDLE_FRAMES):
            t.has_claude_done = True
            p._was_busy = False
            changed = True
        return changed

    def _scan_retry_gates(self, p, txt, new_cl) -> None:
        """자동 재개(M12)·전송 에러 자동 재시도 예약의 게이트 phase.

        `_scan_claude` 에서 추출(로드맵 #1 God-분할, 동작 불변). 재시도 게이트는 new_cl 이
        아니라 **_hdr_claude**(디바운스된 '이 패널은 Claude') 로 판정한다 — API 에러 화면은
        idle/busy footer 가 없어 claude_state=None 일 수 있고, 셸이 우연히 'API Error' 를
        찍어도 오발화하면 안 되기 때문이다. 예약만 다루므로 changed 를 바꾸지 않는다.
        """
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

    def _scan_idle_actions(self, p, txt, new_cl) -> bool:
        """idle 프레임의 자동개입 phase — 보류 리네임 주입·시작 규칙·auto-launch(/rc→
        권한 auto)·권한모드 관측/구동.

        `_scan_claude` 에서 추출(로드맵 #1 God-분할, 동작 불변). idle 이 아니면 권한모드
        시도 카운터만 리셋한다(다음 idle 진입에 재시도). 권한모드 관측값(pm)은 이 프레임
        내내 txt 가 불변이라 진입부에서 1회만 구해 모든 분기가 재사용한다(P1 CSE).
        상태가 바뀌면 True.
        """
        changed = False
        # 권한모드 자동전환 시도 카운터는 idle 이탈 시 리셋(다음 idle 진입에
        # 다시 시도).
        if new_cl != "idle":
            self._perm_reset(p)
        else:
            # P1 CSE: 권한모드 관측값(claude_perm_mode)은 txt 가 이 프레임 내내
            # 불변이라 idle 진입 시 1회만 구해 아래 모든 분기가 재사용한다
            # (예전엔 1138/1145/1161 에서 같은 txt 를 3번 재스캔).
            pm = claude_perm_mode(txt)
            # 탭→세션 리네임 보류분(servertree.rename_window / namesync): 리네임
            # 당시 busy 라 즉시 주입 못 한 `/rename` 을 입력 준비된 첫 idle 에
            # 발동한다. 1회성이라 발화 즉시 비운다. 단, 입력박스(컴포저)에
            # 사용자가 타이핑한 텍스트가 이미 있으면 주입을 **미룬다** — 사용자의
            # 입력 줄에 `/rename` 이 끼어들어 덮지 않도록 _pending_rename 을 유지해,
            # 컴포저가 빈 다음 idle(제출/클리어 후)에 재시도한다. 빈 박스("")·
            # 파싱 불가(None)면 기존대로 즉시 주입한다(추적 불가 시 보수적 진행).
            #
            # 화면 스크랩(_claude_composer_text)만으로는 레이스가 있다: 사용자가
            # 키를 쳐도 Claude 가 그걸 받아 **화면에 되그려야** 컴포저가 non-empty
            # 로 보인다 — PTY 왕복(서버→자식→재렌더) 지연 동안은 막 친 첫 글자가
            # 아직 반영 안 돼 "빈 박스"로 오판된다. Windows ConPTY 는 이 왕복이
            # 유독 느려(conhost 리페인트) 창이 넓어져, 그 틈에 주입되면 `/rename`
            # 이 사용자가 치던 글자와 섞여 그대로 제출된다(버그 리포트: 첫 글자를
            # 치는 순간 끼어들어 프롬프트가 섞임). `pane._inbuf`(_track_prompt 가
            # server_input 에서 키 입력을 화면 재렌더 없이 즉시 동기 누적)를 함께
            # 봐 이 레이스를 없앤다 — 키 입력 이벤트 자체가 이미 스캔 이전에
            # _inbuf 를 채워, 화면이 아직 못 따라온 프레임에도 정확히 걸린다.
            if (p._pending_rename and not self._claude_composer_text(p)
                    and not p._inbuf):
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
            elif self.claude_auto_mode:
                self._maybe_auto_mode(p, txt)
            # M11 디바운스 해제: 정리 후 잔량이 임계+여유(5%p) 위로 회복하면
            # 다음 저잔량 구간에 재발화할 수 있게 한다. 회복 전엔 재발화 금지
            # (compact 가 효과 없어도 매 응답 무한 정리하지 않게 — §5.5).
        return changed

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
                           # `/rc` 메뉴 배너가 떠 Esc 디바운스 중: 화면이 정적이어도
                           # 메뉴가 사라지는 프레임을 관측해 _rc_menu_active 를 풀고
                           # 다음 인스턴스에 재무장하려면 계속 스캔해야 한다.
                           or p._rc_menu_active
                           # auto `/rc` 디바운스 중(_RC_CONFIRM_FRAMES): 정적 idle
                           # 화면이어도 _idle_frames 를 임계까지 진행시켜 발화하거나,
                           # 그 사이 도착한 'Remote Control active' 오버레이를 관측해
                           # 스킵해야 하므로 계속 스캔한다(첫 프레임 즉발 → 응답 대기
                           # 대화 멈춤 버그 수정).
                           or (p._rc_pending and p._claude == "idle"
                               and p._idle_frames < _RC_CONFIRM_FRAMES)
                           # §3.4 busy 이탈 확정 대기 중: 화면이 정적이어도 다음
                           # 스캔이 이탈을 확정(또는 busy 복귀)할 수 있게 계속 스캔.
                           or p._busy_exit_miss > 0
                           # §10-F 종료 토큰 주입 예약 중: 셸 프롬프트가 정적이어도 fg 가
                           # 셸로 잡히는 프레임을 관측해 주입하려면 창 동안 계속 스캔한다.
                           or getattr(p, "_exit_token_pending", 0) > 0)
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
                # `/rc` 화면 신호 phase(메뉴 dismiss·정책 차단·active sticky) —
                # 로드맵 #1 God-분할로 _scan_rc_signals 추출(동작 불변).
                self._scan_rc_signals(p, txt)
                # 실측 /usage 한도 캡처 phase(로드맵 #1 God-분할 — _scan_usage_capture
                # 로 추출, 동작 불변).
                if self._scan_usage_capture(txt):
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
                    # claude 재등장 = 거짓 종료 → 종료 토큰 주입 예약 취소(화면 보호).
                    p._exit_token_pending = 0
                elif p._hdr_claude:
                    p._hdr_claude_miss += 1
                    if p._hdr_claude_miss >= _HDR_CLAUDE_MISS:
                        p._hdr_claude = False
                        # 진짜 세션 종료(디바운스 확정) → auto /rc sticky 해제. 다음 claude
                        # 기동엔 정상 재무장. 재시작 transient 한 프레임은 miss 임계(30)에
                        # 못 미쳐 여기 안 오므로 _rc_done 이 살아남는다.
                        p._rc_done = False
                        # §10-F: 세션 종료 확정 → 토큰 사용량(/usage 한도) 자동 표시(요청
                        # 2026-06-18, 기본 ON). 이 _hdr_claude True→False 전이는 30프레임
                        # 디바운스로 깜빡임(ssh/ConPTY 조각 도착)을 흡수한 **진짜** 종료
                        # 신호이고, _hdr_claude 가 다시 True 가 될 때까지 재진입하지 않아
                        # 한 종료당 1회만 발화한다. 팝업 대신 그 패널 출력 스트림에 한도
                        # 요약을 주입해 스크롤백에 자연스럽게 흘려보낸다(요청 2026-06-18).
                        # 단, _hdr_claude 거짓 종료(긴 출력이 footer 를 샘플 밖으로 밀어
                        # claude_state→None 30프레임)면 살아있는 TUI 에 그래프가 주입돼
                        # 화면이 깨진다 — 포그라운드 프로세스로 진짜 셸 복귀를 교차검증해
                        # 그때만 주입한다(_claude_really_exited, 사용자 보고 2026-06-18).
                        # 한 프레임짜리 일회성 발화가 fg 미확정으로 유실되지 않게 주입을
                        # **예약**하고, 아래에서 셸이 잡히는 순간 재시도한다(안정 표시,
                        # 요청 2026-07-05). 종료 프레임에 fg 가 이미 셸이면 같은 패스에서
                        # 즉시 발화(기존 동작 동치).
                        if self.auto_token_on_exit:
                            p._exit_token_pending = _EXIT_TOKEN_RETRY
                # §10-F 종료 토큰 주입 예약 처리(로드맵 God-함수 분할 1차 —
                # _process_exit_token_pending 로 추출, 동작 불변).
                self._process_exit_token_pending(sess, p)
                # 토큰 누계(#3): 새 Claude 세션 시작(None→Claude) 시 리셋, 매 프레임
                # 현재 응답 running 토큰을 step 으로 접어 응답별 peak 를 누계에 확정.
                # (확정 시점 committed>0 은 #7 의 영속 로깅 이벤트로도 쓰인다.)
                committed = 0
                if new_cl and not old_cl:
                    tokens.reset(p._tok_state)
                    p._exit_tokens = 0       # 새 세션 → 이전 종료 총량 보존값 폐기
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
                if not new_cl:
                    p._rules_pending = False   # 세션 끝나면 예약 해제
                    p._rc_pending = False      # 세션 끝 — auto-launch 예약 해제
                    p._perm_auto_pending = False
                # 수동 /clear 감지: 이미 Claude 세션 중(old_cl)인데 환영 배너가 **새로**
                # 뜨면(빈 컨텍스트) 토큰 누계를 새 세션으로 끊는다. pytmux 자동화(_pc_
                # advance)를 안 타는 사용자 직접 /clear 가, 상태줄 ctx 근사%(누계/윈도우)를
                # 안 비워 /clear 후에도 옛 % 가 남던 문제 수정. 배너가 머무는 동안은
                # _welcome_seen 으로 1회만. 신규 시작(old_cl 없음)은 위 None→Claude 가 처리.
                wel = claude_welcome(txt)
                if wel and not p._welcome_seen and old_cl:
                    self._reset_token_session(p)
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
                    # 배지 서명(‘(… context)’/‘/model’)이 붙은 매치만 인정한다
                    # (claude_model_badge). 전체 화면 스크랩이라, 대화/온보딩 본문의
                    # 모델명 언급(예 "claude-fable-5")을 활성 모델로 오인해 상태줄이
                    # 엉뚱한 모델로 튀던 것 방지(2026-07-04). 배지 없으면 None → 아래
                    # 프로브 폴백(/usage 실 모델, 팝업과 동일 출처)이 채운다.
                    # M14c: 모델 배지/프로브 감지+디바운스(로드맵 God-분할 —
                    # _update_claude_model 로 추출, 동작 불변).
                    if self._update_claude_model(p, txt):
                        changed = True
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
                    # §10-D P4: 트랜스크립트 권위 토큰 증분 적재(usage_xc). 위 footer
                    # 스크랩(live)은 cache_read/creation 을 못 봐 실제의 ~0.4%만 잡는
                    # 라이브 활동신호로 남기고, 4항목 정확 누계는 ~/.claude 트랜스크립트
                    # 에서 적재한다. 응답 종료(committed>0)엔 그 턴의 usage 가 막 기록됐
                    # 으므로 강제 테일, 그 외엔 _XC_TAIL_FRAMES 주기로만 — best-effort.
                    self._xc_tail_pane(sess, t, p, force=committed > 0)
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
                    # §10-F: 리셋으로 사라질 세션 총량을 종료 요약용으로 1회 보존한다
                    # (종료 확정은 여기서 _HDR_CLAUDE_MISS 프레임 뒤라, 그때 _session_tokens
                    # 는 이미 0). _usage_exit_text 가 이 값을 우선 읽어 요약이 비지 않는다.
                    p._exit_tokens = p._session_tokens
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
                # 완료 감지(idle 안정)·자동 리드로우·완료 알림 phase — 로드맵 #1
                # God-분할로 _scan_done_and_redraw 추출(동작 불변).
                if self._scan_done_and_redraw(p, t, w, win, old_cl, new_cl, txt):
                    changed = True
                # 자동재개/재시도 예약 게이트 phase(로드맵 #1 God-분할 —
                # _scan_retry_gates 로 추출, 동작 불변).
                self._scan_retry_gates(p, txt, new_cl)
                # idle 자동개입 phase(리네임/규칙/auto-launch/권한모드) — 로드맵 #1
                # God-분할로 _scan_idle_actions 추출(동작 불변).
                if self._scan_idle_actions(p, txt, new_cl):
                    changed = True
                # 프롬프트 단위 클리어 모드(#9): busy→idle(응답 완료) 경계에서
                # doc→/clear 상태기계를 한 단계 전진한다(사용자가 명시 활성화한 수동 모드).
                if old_cl == "busy" and new_cl == "idle":
                    # M17 S8: 완료 경계마다 화면 꼬리를 직전과 비교(동일 출력 반복 카운트).
                    p._done_tail, p._repeat_n = track_repeat(
                        p._done_tail, p._repeat_n, screen_tail_key(txt))
                    if p.prompt_clear_mode:
                        self._pc_advance(p)
                # M17(T7) 표시 경고 갱신 phase(로드맵 #1 God-분할 — _scan_warnings
                # 로 추출, 동작 불변).
                if self._scan_warnings(p, new_cl):
                    changed = True
        return changed

    # 경고 이력 보관 상한(파일 한 줄=경고 1건). onset 만 기록해 증가는 느리지만,
    # 무한 성장을 막으려 기록 때 최근 이만큼만 남기고 잘라 다시 쓴다.
    _WARN_HIST_CAP = 200

    def _warnhist_path(self) -> str:
        """경고 이력 JSONL 경로 — 토큰 DB 와 같은 디렉터리(claude-tokens*.db 옆)에 두어
        claude-code 를 통째로 지우면 함께 사라진다(delete-to-disable). 소켓별로 격리
        (DB 파일명 stem 을 따름)."""
        db = self.tokens_db_path          # @property (str), 호출하지 않는다
        return (db[:-3] if db.endswith(".db") else db) + ".warnhist.jsonl"

    def _record_warn_history(self, p, warn, warn_kind, warn_n, ts) -> None:
        """경고 onset 1건을 이력 JSONL 에 추가한다(append + 최근 _WARN_HIST_CAP 건으로
        트림). 기록 실패(디스크/권한)는 표시 기능이라 조용히 무시한다(경고 표시 자체는
        라이브 status 로 계속 동작)."""
        import json
        rec = {"ts": float(ts), "kind": warn_kind, "n": warn_n,
               "badge": warn,
               "session": getattr(p, "_claude_session_id", 0),
               "pane": getattr(p, "id", None)}
        path = self._warnhist_path()
        try:
            lines = []
            try:
                with open(path, encoding="utf-8") as f:
                    lines = [ln for ln in f.read().splitlines() if ln.strip()]
            except OSError:
                pass
            lines.append(json.dumps(rec, ensure_ascii=False))
            if len(lines) > self._WARN_HIST_CAP:
                lines = lines[-self._WARN_HIST_CAP:]
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with ipc.open_private(path, "w") as f:  # 0600 — usagelog 와 동일 규약(L6)
                f.write("\n".join(lines) + "\n")
        except OSError:
            pass

    def _read_warn_history(self, limit: int = 50) -> list:
        """경고 이력을 시간 **내림차순**(최신 먼저)으로 최근 limit 건 반환. 파일이 없거나
        깨진 줄은 건너뛴다(없으면 빈 리스트). [경고] 탭이 token_log 응답으로 받아 트리로
        그린다."""
        import json
        out = []
        try:
            with open(self._warnhist_path(), encoding="utf-8") as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        continue
        except OSError:
            return []
        out.sort(key=lambda r: r.get("ts", 0) or 0, reverse=True)
        return out[:limit]

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

    def _account_token_total_xc(self, ap) -> int:
        """상태줄 계정 Σ 의 **cache-포함 권위값**(usage_xc full). `_account_token_total`
        과 같은 단일/다중 계정 의미론을 따르되 스크랩 라이브 누계 대신 트랜스크립트
        회계를 쓴다. usage_xc 미보유(v8 전)·빈 테이블·예외는 스크랩 누계로 폴백한다.

        주의: v8 전 백필된 레거시 xc 행은 account=NULL(미상)이라 계정 분해가 불완전
        하다. 그래서 **단일 계정**(known≤1, _account_token_total 과 동형)일 때만 전체
        full(unknown 포함)을 합쳐 안전하게 권위값으로 쓰고, 다중 계정에서만 계정별
        분해를 신뢰한다(분해 불완전분은 그 계정에 덜 잡힐 수 있으나 다중계정은 드묾).

        핫패스(매 status) 비용: 단일 계정이면 이미 dirty-게이트로 캐시된
        `_xc_totals_for_status()` 의 full 을 재사용해 **추가 쿼리가 없다**(같은 status 가
        xc_totals 필드용으로 이미 1회 호출). 다중 계정일 때만 계정별 GROUP BY 를 돈다."""
        if not ap or not (ap._claude or ap._claude_account):
            return 0
        try:
            panes = [p for p in self._all_panes()
                     if p._claude or p._claude_account]
            known = {p._claude_account for p in panes if p._claude_account}
            if len(known) <= 1:
                # 단일 계정 → 캐시된 전체 full(unknown 포함). 캐시 비면(구버전/빈
                # usage_xc) 스크랩 누계로 폴백.
                full = self._xc_totals_for_status().get("full")
                return int(full) if full else self._account_token_total(ap)
            if not hasattr(usagedb, "xc_totals_by_account"):
                return self._account_token_total(ap)
            conn = self._tokens_db_conn()
            if conn is None:
                return self._account_token_total(ap)
            by = usagedb.xc_totals_by_account(conn)
            if not by:
                return self._account_token_total(ap)
            acct = ap._claude_account
            if acct:
                return int(by.get(acct, 0))
            return 0
        except Exception:
            return self._account_token_total(ap)

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
        # 계정 불일치 fail-open: 그림자 /usage 세션의
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

    def _usage_probe_allowed(self) -> bool:
        """그림자 /usage 프로브를 띄워도 되는가. 살아 있는 로컬 Claude 패널이 있거나,
        최근(usage_refresh_sec 창) 트랜스크립트 활동이 있으면 True. 후자는 **패널 밖
        (별도 터미널)에서 Claude 를 쓰는 경우**를 커버해 그때도 시각별 한도%(5h/주간)가
        기록되게 한다 — 패널이 없어도 _probe_cwd 가 트랜스크립트 cwd(신뢰 폴더)로
        폴백하므로 트러스트 화면에 안 막힌다. cwd 를 못 읽으면 recent_activity_cwd 가
        None → 무의미한 숨은세션 spawn 을 막는다(계정은 머신-로그인 하나라 섞이지 않음)."""
        if self._any_claude_pane():
            return True
        window = max(60.0, float(getattr(self, "usage_refresh_sec", 600) or 600))
        try:
            return transcript.recent_activity_cwd(window, time.time()) is not None
        except Exception:
            return False

    def _probe_cwd(self):
        """그림자 /usage 프로브의 cwd — **실행 중인 Claude 패널의 셸 cwd**(사용자가
        이미 신뢰한 폴더). 데몬 cwd(보통 홈 ~)로 띄우면 숨은 claude 가 Claude Code
        신뢰 대화상자("Is this a project you trust?")에 막혀 /usage 패널이 영영 안
        뜨고 프로브가 조용히 None 이었다(2026-06-11 라이브 진단 — 재시작 후 limits
        0건·실측 미표시의 원인. 직접 같은 조건으로 재현해 트러스트 화면 확인).
        살아 있는 Claude 패널이 없으면(패널 밖 별도 터미널 사용) **최근 활동한
        트랜스크립트가 기록한 cwd**(Claude 가 실제로 돈 신뢰 폴더)로 폴백 — 이것도
        없을 때만 마지막으로 데몬 cwd."""
        for p in self._all_panes():
            if getattr(p, "_claude", None):
                d = self._pane_cwd(p)
                if d:
                    return d
        np = transcript.newest_transcript()
        if np:
            d = transcript.read_cwd(np)
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
    # ② 프로브 성공 직후 사용률이 한도 부근이면 다음 프로브를 주기/4 로 앞당김
    # (한도 직전 표시 해상도 — 10분 묵은 실측 위에서 졸지 않게).
    _USAGE_COMMIT_DELAY = 20.0    # 커밋 폭주를 1회로 합치는 디바운스(초)
    _USAGE_COMMIT_MIN_AGE = 180.0  # 실측이 이보다 신선하면 커밋 트리거 생략(초)
    _USAGE_NEAR_LIMIT_PCT = 90    # 실측 사용률이 이 % 이상이면 '한도 부근'(주기 단축)
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
        """예약된 이벤트 트리거 갱신 발화 — Claude 패널이 남아 있거나 최근 트랜스크립트
        활동(패널 밖 사용)이 있을 때만(_usage_probe_allowed). 게이트는 refresh_usage 의
        _usage_busy 중복 방지에 더해 빈 서버 spawn 낭비 방지."""
        self._usage_probe_handle = None
        if not self._usage_probe_allowed() or not self.running:
            return
        asyncio.create_task(self.refresh_usage())

    def _usage_near_limit(self) -> bool:
        """마지막 실측 사용률이 한도 부근(세션/주간 중 하나라도 _USAGE_NEAR_LIMIT_PCT
        이상)인가 — 그러면 다음 프로브를 앞당겨 한도 직전 표시 해상도를 높인다. 계정
        일치는 안 본다 — 주기 단축은 패널 무관한 전역 해상도 결정이다."""
        u = self._usage
        if not isinstance(u, dict):
            return False
        for key in ("session", "week_all"):
            d = u.get(key)
            pct = d.get("pct") if isinstance(d, dict) else None
            if pct is not None and pct >= self._USAGE_NEAR_LIMIT_PCT:
                return True
        return False

    def _after_usage_probe(self):
        """프로브 성공 직후: 사용률이 한도 부근이면 다음 갱신을 주기/4(최소 60초)로
        앞당겨 예약한다. 자동 갱신이 꺼져 있으면(usage_refresh_sec=0) 사용자 의사를
        존중해 앞당기지도 않는다."""
        if self.usage_refresh_sec <= 0 or not self._usage_near_limit():
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

    def _pending_action(self, pane):
        """카운트다운: 무장된 자동재개 예약의 남은 초(ETA)를 반환한다(없으면 None).
        클라 상태줄 배지가 "곧 자동재개가 일어난다(입력 시 취소)"를 사용자에게 보이게
        한다 — 비가역 동작의 발견성·취소권 보장. 발화 시각은 asyncio 타이머 핸들의
        when()(loop.time 기준)에서 얻는다 — 클램프해 음수/만료는 0 으로."""
        if pane is None or self.loop is None:
            return None
        h = getattr(pane, "_resume_handle", None)
        if h is None:
            return None
        try:
            eta = max(0, int(round(h.when() - self.loop.time())))
        except (AttributeError, RuntimeError, TypeError):
            return None
        return {"kind": "resume", "eta": eta}

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
        # §10-E #1: PYTMUX_HOME 통합 시 토큰 DB 도 <home>/db/ 아래로(설정·상태 한 곳).
        home = ipc.pytmux_home()
        if home:
            return os.path.join(home, "db", self._tokens_db_filename())
        return os.path.join(os.path.dirname(__file__), "db",
                            self._tokens_db_filename())

    def _legacy_tokens_db_path(self) -> str:
        """이 CL(S5 T5) 이전 또는 타 머신의 DB 위치: 프로젝트 루트 `db/`. 마이그레이션 원본."""
        from pytmuxlib.servercapture import PROJECT_DIR
        return os.path.join(PROJECT_DIR, "db", self._tokens_db_filename())

    def _plugin_tokens_db_path(self) -> str:
        """플러그인 기본 위치(pytmuxlib/plugins/claude-code/db/) DB 경로 — 평소(PYTMUX_HOME
        미설정) 기본 저장 위치이자, PYTMUX_HOME 통합 시 <home>/db 로의 마이그레이션 원본."""
        return os.path.join(os.path.dirname(__file__), "db",
                            self._tokens_db_filename())

    @staticmethod
    def _copy_db_tree(old: str, new_path: str) -> bool:
        """old DB(+WAL 사이드카 -wal/-shm)를 new_path 로 **복사**(원본 보존). 성공 True."""
        import shutil
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            shutil.copy2(old, new_path)
        except OSError:
            return False
        for suffix in ("-wal", "-shm"):
            if os.path.exists(old + suffix) and not os.path.exists(new_path + suffix):
                try:
                    shutil.copy2(old + suffix, new_path + suffix)
                except OSError:
                    pass
        return True

    def _migrate_legacy_db(self, new_path: str):
        """토큰 DB 를 새 위치(new_path)로 1회 마이그레이션한다 — 이력 유실 없이 무중단,
        WAL 사이드카(-wal/-shm) 동반. PYTMUX_TOKENS_DB 강제 지정(테스트) 시엔 건너뛴다.
        new_path 에 이미 DB 가 있으면 아무것도 안 한다(멱등).

        원본 후보(우선순위):
          ① **PYTMUX_HOME 통합**(new_path=<home>/db): 평소 위치인 **플러그인 db/** 를 원본
             으로 **복사**(원본 보존 — PYTMUX_HOME 을 껐다 켜도 플러그인 db 가 그대로 유효).
             다른 머신에서 코드 업데이트 후 PYTMUX_HOME 을 켜면 그 머신의 기존 토큰 이력이
             <home>/db 로 따라온다(사용자 요청 2026-06-17).
          ② **레거시 PROJECT_DIR/db**(S5 T5 이전 위치): **이동**(move)으로 정리 — 진짜 옛
             위치라 남겨둘 이유가 없다. PYTMUX_HOME 미설정(new_path=플러그인 db/)일 때의
             기존 거동도 이 경로다(루트 db/ → 플러그인 db/)."""
        if os.environ.get("PYTMUX_TOKENS_DB"):
            return
        if os.path.exists(new_path):
            return
        # ① PYTMUX_HOME 통합 시에만: 플러그인 db → <home>/db 복사(원본 보존). home 미설정
        # 이면 new_path 가 곧 플러그인 db 라 자기복사가 무의미 — ② 로 직행(기존 거동).
        if ipc.pytmux_home():
            plugin_db = self._plugin_tokens_db_path()
            if plugin_db != new_path and os.path.exists(plugin_db):
                if self._copy_db_tree(plugin_db, new_path):
                    return
        # ② 레거시 PROJECT_DIR/db → new_path 이동(기존 S5 T5 거동). EXDEV 면 복사 폴백.
        old = self._legacy_tokens_db_path()
        if old == new_path or not os.path.exists(old):
            return
        try:
            os.makedirs(os.path.dirname(new_path), exist_ok=True)
            os.replace(old, new_path)
        except OSError:
            if not self._copy_db_tree(old, new_path):   # 크로스 디바이스 폴백(원본 남음)
                return
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
            account=pane._claude_account, tokens=amount,
            model=getattr(pane, "_claude_model", None))
        conn = self._tokens_db_conn()
        if conn is not None:
            usagedb.insert(conn, rec)
        # S6 T5: 응답 종료 이벤트 → 실측이 묵었으면 디바운스 갱신 예약.
        self._on_token_commit_refresh()

    def _xc_resolve_path(self, pane):
        """패널의 트랜스크립트(`~/.claude/projects/<proj>/*.jsonl`) 경로를 캐시·주기
        재해석한다(§10-D P3, 없으면 None). lsof/ps 후손 탐색이 비싸 한 번 잡은 경로를
        _XC_RESOLVE_FRAMES 테일 동안 패널에 캐시하고, 카운트가 0일 때만(첫 호출 포함)
        다시 해석한다 — claude 재기동으로 경로가 바뀌면 offset 캐시를 무효화해 DB
        커서로 재이음한다. 1차=pid→열린 jsonl(견고), 2차=cwd→최신 mtime 폴백."""
        n = getattr(pane, "_xc_resolve_n", 0)
        if n > 0:
            pane._xc_resolve_n = n - 1
            return getattr(pane, "_xc_path", None)
        old = getattr(pane, "_xc_path", None)
        try:
            cwd = self._pane_cwd(pane)
        except Exception:
            cwd = None
        new_path = transcript.find_transcript(
            getattr(pane, "child_pid", None), cwd)
        if new_path and new_path != old:
            pane._xc_offset = None          # 경로 변경 → DB 커서로 재이음
        pane._xc_path = new_path
        pane._xc_resolve_n = _XC_RESOLVE_FRAMES
        return new_path

    def _xc_tail_pane(self, sess: Session, tab: Tab, pane: Pane,
                      force: bool = False):
        """패널의 Claude 트랜스크립트에서 새 usage 레코드를 usage_xc 로 증분 적재
        (§10-D P4). best-effort — 어떤 실패도 본 스캔/표시 흐름을 막지 않는다.

        force=False 면 _XC_TAIL_FRAMES 프레임마다 한 번만(주기 보조), force=True(응답
        종료 직후)면 디바운스 무시하고 즉시 테일한다 — 그 턴의 usage 레코드가 막
        기록됐으므로. offset 은 패널 캐시 우선·없으면 usage_xc_cursor(재시작 이음),
        tail_file 로 append 분만 읽어 insert_xc_many(멱등). usagedb 가 v7(usage_xc)
        미보유면 조용히 무동작 — P2 미게시 상태에서도 안전(graceful degrade)."""
        if not force:
            n = getattr(pane, "_xc_tail_n", 0) + 1
            if n < _XC_TAIL_FRAMES:
                pane._xc_tail_n = n
                return
        pane._xc_tail_n = 0
        try:
            if not hasattr(usagedb, "insert_xc_many"):
                return                       # v7 미배포(P2 미게시) → 무동작
            conn = self._tokens_db_conn()
            if conn is None:
                return
            path = self._xc_resolve_path(pane)
            if not path:
                return
            off = getattr(pane, "_xc_offset", None)
            if off is None:                  # 첫 테일/경로변경 → DB 커서로 이음
                cur = usagedb.get_xc_cursor(conn, path)
                off = cur[0] if cur else 0
            recs, new_off = transcript.tail_file(path, off)
            if recs:
                # v8: 적재 시점 패널 Claude 계정을 함께 실어 계정별 cache-포함 집계를
                # 가능하게 한다(미식별이면 None → 표시층 unknown). insert_xc_many 가
                # account 인자를 모르는 구버전(v7)이면 TypeError → 계정 없이 폴백.
                acct = getattr(pane, "_claude_account", None) or None
                try:
                    n = usagedb.insert_xc_many(
                        conn, recs, tab=tab.index, pane=pane.id,
                        pytmux_session=getattr(pane, "_claude_session_id", 0),
                        account=acct)
                except TypeError:
                    n = usagedb.insert_xc_many(
                        conn, recs, tab=tab.index, pane=pane.id,
                        pytmux_session=getattr(pane, "_claude_session_id", 0))
                if n:
                    # §10-D P7: 새 권위 레코드 적재 → status 용 누계 캐시 무효화
                    # (federation 다운스트림이 다음 status 에 최신 Σ 를 받는다).
                    self._xc_totals_dirty = True
            if new_off != off:
                mt = None
                try:
                    mt = os.path.getmtime(path)
                except OSError:
                    pass
                usagedb.set_xc_cursor(conn, path, new_off, mt)
            pane._xc_offset = new_off
        except Exception:
            pass

    def _xc_totals_for_status(self) -> dict:
        """§10-D P7: status 에 실을 트랜스크립트 권위 누계(usage_xc 전체 Σ — full/
        footer/cache_read/cache_create/ratio). federation 다운스트림이 **원격 서버의**
        정확 Σ(cache 포함)를 보도록 usage_limits 와 동형으로 status 에 싣는다(serverremote
        가 last_status 로 누적·패스스루). 매 status 마다 풀테이블 SUM 을 돌리지 않게
        dirty 게이트로 캐시한다 — 새 레코드 적재(_xc_tail_pane insert>0)에서만
        _xc_totals_dirty 를 세우고 그때만 1회 재계산(SUM 은 ms 미만이나 핫패스라 가산).
        v7(usage_xc) 미보유/실패는 빈 dict(graceful degrade)."""
        if not getattr(self, "_xc_totals_dirty", True):
            return getattr(self, "_xc_totals_cache", {})
        out: dict = {}
        try:
            if hasattr(usagedb, "xc_totals"):
                conn = self._tokens_db_conn()
                if conn is not None:
                    out = usagedb.xc_totals(conn)
        except Exception:
            out = getattr(self, "_xc_totals_cache", {})
        self._xc_totals_cache = out
        self._xc_totals_dirty = False
        return out

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
            with ipc.open_private(path, "a") as f:  # 0600 — 토큰 회계 진단(L6 동류)
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

