"""Pane 의 Claude 전용 상태 필드 소유(S4).

코어 `model.py` 의 `Pane.__init__` 은 더 이상 Claude 거동 필드(자동개입 타이머·권한모드
순환·피드백 디스미스·완료/폭주 감지·프롬프트 추적·세션 셋업 등)를 정의하지 않는다.
대신 코어는 `Pane.__init__` 끝에서 `plugins.pane_init(self)` 훅을 부르고, 이 플러그인이
여기서 그 필드들을 패널에 설치한다. 디렉토리를 지우면 훅이 사라져 패널엔 Claude 필드가
전혀 안 생기고, 코어의 몇 안 되는 읽기 지점은 `getattr(pane, …, 기본값)` 으로 안전하게
동작한다(delete-to-disable).

코어에 **남는** Claude 인접 Pane 필드(코어가 직접 읽거나 쓰는 것들 — 계정·헤더행 예약·
보류 리네임·자동재개 토글): `_claude_account`·`_claude_account_manual`·`_pending_rename`·
`_feed_seq`·`autoresume`·`prompt_clear_queue`. 이는 HANDOFF §11.6 의
3a/3b 설계("코어가 읽는 Pane 속성은 코어에 안전한 기본값으로 남겨도 무해")와 일치한다.
(토큰 누계 `_tok_state`·`_session_tokens` 는 S5 토큰 모듈화 T4 에서 이리로 이전 — 코어
servertree 의 토큰 이관은 pane_closing 훅으로, 코어는 더는 토큰 누계를 모른다.)
"""
from __future__ import annotations


def init_pane(pane) -> None:
    """패널 생성 시(코어 Pane.__init__ → plugins.pane_init) Claude 거동 필드를 설치한다.
    값/주석은 이전 코어 model.py 정의를 그대로 옮긴 것이다."""
    # Claude Code 감지 상태(idle/busy/limit/None)와 표시용 사용량/모델/컨텍스트%.
    pane._claude = None
    pane._claude_usage = None       # "ctx 42%" / "12k tok" 등(best-effort)
    pane._claude_model = None       # M14c: 모델 배지(opus-4.8 등, best-effort)
    pane._ctx_pct = None            # M15: 마지막 컨텍스트 잔량%(우선순위 정리 비교용)
    # 토큰 영속 로깅(#7): 현재 Claude 세션 id(None→Claude 전이마다 새로 부여)와 토큰
    # 누계 상태(S5 토큰 모듈화 T4 에서 코어 model.py 에서 이전). _tok_state=현재 응답
    # peak+세션 누계({"peak","total"}), _session_tokens=표시·전송용 캐시(= total+peak).
    # (계정 _claude_account/_account_manual 은 아직 코어 Pane 에 남는다.)
    pane._claude_session_id = 0
    pane._tok_state = {"peak": 0, "total": 0}
    pane._session_tokens = 0
    pane._inbuf = ""                # 현재 입력 줄 누적(프롬프트 추적용)
    pane.last_prompt = ""           # 마지막으로 제출한 프롬프트(한 줄)
    pane.pending_prompts = []       # busy 중 입력해 큐된 프롬프트(#4)
    # 프롬프트 단위 클리어 모드(#9). prompt_clear_queue(쌓인 명령 큐)는 코어가
    # respawn 시 직접 비우므로 코어 Pane 에 남고, 여기선 모드/상태기계만 둔다.
    pane.prompt_clear_mode = False
    pane._pc_phase = None           # None(대기) | "doc" | "clear"
    # 자동 doc→/clear(§10): _adc_timer=무장 타이머 핸들, _adc_active=진행중.
    pane._adc_timer = None
    pane._adc_active = False
    # 자동 /compact(요청): _acpt_timer=타이머 핸들, _acpt_fired=1회 발화 디바운스.
    pane._acpt_timer = None
    pane._acpt_fired = False
    # 컨텍스트 하드스톱 자동복구: 같은 하드스톱 화면 반복 주입 방지 디바운스.
    pane._hardstop_fired = False
    # 시간기반 자동 compact·doc-clear 쿨다운 만료 시각(time.monotonic; 0=없음).
    pane._auto_cc_cooldown_until = 0.0
    # 권한모드 자동 오토모드 전환(§10): 시도수·직전 모드·이번 드라이브에서 본 모드 집합.
    pane._cam_tries = 0
    pane._cam_last = None
    pane._cam_seen = set()
    # 현재 관측된 권한모드와 사용자가 고른 목표 모드(footer 클릭 팝업).
    pane._perm_mode = None
    pane._perm_target = None
    # bypass(권한 우회) 가용 여부 sticky(--dangerously-skip-permissions 로 떴을 때 관측).
    pane._bypass_seen = False
    # 비활성 탭 완료 알림(#22) 플리커 방지: busy 후 연속 idle 안정 프레임 카운트.
    pane._was_busy = False
    pane._idle_frames = 0
    # §3.4 busy 이탈 히스테리시스: busy→idle 전이를 연속 관측 후에만 확정하기 위한
    # 미스 카운터(리페인트 한 프레임 깜빡임이 응답 경계로 오인되지 않게).
    pane._busy_exit_miss = 0
    # M17(T7): busy 진입 시각·직전 완료 화면 꼬리 해시·연속 동일 횟수·표시용 경고.
    pane._busy_since = None
    pane._done_tail = None
    pane._repeat_n = 0
    pane._claude_warn = None
    # §3.7 포맷 미인식 가시화: _fmt_unknown=경고 활성, _fmt_first_mono=의심 시작 시각
    # (Claude fg + 파서 None), _fmt_logged=error.log 1회 기록 가드, _fmt_check_mono=
    # 다음 fg(ps) 검사 허용 시각(throttle).
    pane._fmt_unknown = False
    pane._fmt_first_mono = None
    pane._fmt_logged = False
    pane._fmt_check_mono = 0.0
    # 세션 피드백 프롬프트 자동 Dismiss(#26): 재시도중·쏜 횟수·다음 재시도 대기 프레임.
    pane._feedback_active = False
    pane._feedback_tries = 0
    pane._feedback_wait = 0
    # 수동 /clear 감지 디바운스(환영 배너가 머무는 동안 토큰세션 재리셋 방지).
    pane._welcome_seen = False
    pane._rules_pending = False     # 시작 규칙 주입 예약(다음 idle 1회, #27)
    # 새 Claude 세션 자동 셋업(auto-launch): /rc 주입(_rc_pending) 후 권한 auto 유도.
    pane._rc_pending = False
    pane._perm_auto_pending = False
    # _rc_done: 이 세션에 auto /rc 를 이미 적용했음 sticky(재시작 직렬화 — 거짓 새세션
    # 오인으로 /rc 재주입되는 버그 방지). 진짜 세션 종료에서만 해제.
    pane._rc_done = False
    # 토큰 절감 자동화: _resume_handle=자동재개 예약 call_later 핸들(busy 복귀 시 cancel),
    # _ctx_fired=컨텍스트 잔량 자동정리(M11) 이번 구간 발화 여부, _ctx_last_fire=마지막
    # 정리 발화 시각(M14 빈도 상한).
    pane._resume_handle = None
    pane._ctx_fired = False
    pane._ctx_last_fire = None
    # 디바운스된 Claude 존재 플래그·연속 non-Claude 스캔 수 — raw _claude 가 한 프레임
    # 깜빡여도 안 흔들리는 안정 신호. API 에러 게이트·스캔 보조 판정이 읽는다(이름의
    # hdr 는 옛 헤더 예약 유래 — 헤더는 2026-06-13 제거, 신호 자체는 그대로 유효).
    pane._hdr_claude = False
    pane._hdr_claude_miss = 0
    # 토큰 리밋 자동 재개 메시지·예약 보류 플래그. (토글 autoresume 은 코어가 쓰므로
    # 코어 Pane 에 남고, 여기선 메시지/보류만 둔다.)
    pane.resume_msg = "continue"
    pane._resume_pending = False
    # 전송 에러(API error/rate limit) 자동 재시도(요청): _retry_handle=백오프 후 "계속"
    # 주입 예약 call_later 핸들(에러 해소·busy 복귀 시 cancel), _retry_pending=예약 보류,
    # _retry_attempts=연속 주입 횟수(상한·백오프; 에러 해소 시 0 으로 리셋, #9 H3).
    pane._retry_handle = None
    pane._retry_pending = False
    pane._retry_attempts = 0
    # Claude 스캔 버퍼·마지막 스캔 시 본 feed seq(dirty 게이팅; 코어 _feed_seq 와 비교).
    pane._scanbuf = ""
    pane._scan_seq = -1


def reset_pane(pane) -> None:
    """respawn(새 셸) 시(코어 Pane.reinit → plugins.pane_reset) 리셋할 Claude 필드.
    이전 코어 reinit 의 Claude 서브셋을 그대로 옮긴 것(코어가 쓰는 _claude_account·
    prompt_clear_queue 리셋은 코어 reinit 에 남는다; 토큰 누계 _tok_state/_session_tokens
    리셋은 S5 T4 에서 이리로 이전 — 새 셸이므로 0 에서 시작)."""
    pane._scanbuf = ""
    # 무장된 자동재개/재시도 타이머는 **취소**한 뒤 리셋한다(respawn=새 셸). 핸들을
    # 드롭만 하면 ① 살아있는 타이머가 새 셸로 발화하고 ② _retry_pending 잔류로 새
    # 에러의 재무장이 막힌다(#9 H1). reset_pane 은 server 핸들이 없어 _cancel_* 대신
    # 핸들을 직접 cancel(_fire_* 의 pty/state 가드만으론 새 셸 발화를 못 막는다).
    for _h in (pane._resume_handle, pane._retry_handle):
        if _h is not None:
            _h.cancel()
    pane._resume_pending = False
    pane._resume_handle = None      # 자동재개 예약 핸들 리셋(M12)
    pane._retry_pending = False
    pane._retry_handle = None       # 전송 에러 재시도 예약 핸들 리셋(#9 H1)
    pane._retry_attempts = 0
    pane._ctx_fired = False          # 컨텍스트 잔량 자동정리 디바운스 리셋(M11)
    pane._ctx_last_fire = None       # 정리 빈도 상한 시각 리셋(M14)
    pane._claude_session_id = 0
    pane._tok_state = {"peak": 0, "total": 0}   # 새 셸 — 토큰 누계 0 에서 시작(S5 T4)
    pane._session_tokens = 0
    pane._pc_phase = None            # 프롬프트 단위 클리어 상태기계 리셋(모드 자체는 유지)
    pane._adc_active = False         # 자동 doc→/clear 진행상태 리셋(§10)
    pane._cam_tries = 0              # 권한모드 자동전환 시도 카운터 리셋(§10)
    pane._cam_last = None
    pane._cam_seen = set()           # 이번 드라이브에서 본 모드 집합 리셋
    pane._perm_mode = None           # 새 셸 — 권한모드 관측/목표 리셋(§10 item 2)
    pane._perm_target = None
    pane._was_busy = False           # done 플리커 디바운스 리셋(§10 #18)
    pane._idle_frames = 0
    pane._busy_exit_miss = 0         # busy 이탈 히스테리시스 리셋(§3.4)
    pane._hdr_claude = False         # 헤더 예약 디바운스 리셋
    pane._hdr_claude_miss = 0


# 재시작(re-exec) 직렬화 대상 — JSON 가능 스칼라/딕트만. (코어 _RESUME_FIELDS 에서
# 이리로 이전. set/타이머/call 핸들 등 휘발성 필드는 제외 — 재관측으로 복원.)
_SER_FIELDS = (
    "_claude", "_claude_usage", "_scanbuf", "_resume_pending", "resume_msg",
    "last_prompt", "_claude_session_id", "prompt_clear_mode", "_rc_done",
    "pending_prompts",
    # S5 토큰 모듈화 T4: 토큰 누계도 재시작에 보존(코어 _RESUME_FIELDS 에서 이전).
    "_tok_state", "_session_tokens",
)


def serialize(pane) -> dict:
    """재시작 보존용 Claude 필드 부분집합을 dict 로(코어 export_state 가 'plugin_state'
    키로 담는다 — 코어는 내용을 해석하지 않는다)."""
    d = {}
    for f in _SER_FIELDS:
        if hasattr(pane, f):
            d[f] = getattr(pane, f)
    if "pending_prompts" in d:
        d["pending_prompts"] = list(d["pending_prompts"])
    return d


def restore(pane, data: dict) -> None:
    """serialize 가 만든 dict 로 Claude 필드를 복원한다(코어 import_state 에서 위임).
    init_pane 가 이미 기본값을 깔아 둔 뒤라 존재하는 속성을 덮어쓴다."""
    if not data:
        return
    for f in _SER_FIELDS:
        if f in data:
            setattr(pane, f, data[f])
    pane.pending_prompts = list(data.get("pending_prompts", pane.pending_prompts))
