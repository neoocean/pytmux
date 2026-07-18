# Claude Code 화면 골든 픽스처 (M8)

`pytmuxlib/claude.py` 의 휴리스틱(상태/사용량/잔량%/리밋 파서)이 실제 Claude Code
화면 텍스트에서 기대값을 내는지 회귀 고정하는 픽스처다. `test_token_saver.py` 가
각 `.txt` 를 읽어 `claude_state`/`claude_usage`/`claude_context_pct`/`parse_reset_delay`
의 결과를 단언한다.

## 출처 — 일부 실캡처 보강(2026-06-07)
pytmux REC 캡처(`captures/default/pane-*.log`, 오염된 이 세션 pane-2 제외)를
`pytmuxlib.replay.replay()` 로 렌더해 **실제 현행 Claude Code 화면 문자열**을 확인하고,
아래 4종을 그 실문자열로 교체했다(docs/TOKEN_SAVING_SCENARIO.md §M8보강·M14c):

- ✅ `busy.txt` — 실 스피너 `✻ Whirring… (1m 15s · thought for 1s)`
  (이전 합성 `Crunching… (38s · ↓ 1.9k tokens)` 과 **포맷 다름**).
- ✅ `idle.txt` — 실 footer `⏵⏵ auto mode on · 2 shells` + `? for shortcuts`.
- ✅ `badge_1m.txt` — 실 모델 배지 `Opus 4.8 (1M context)`(M14c `claude_model` 근거).
- ✅ `ctx_low.txt` — 실 잔량 경고 `Context low (8% remaining)`.
- ✅ `usage.txt` — 실 `/usage` TUI 패널(M19): `Current session/week · Resets … · N% used`.
  pane-2 REC 를 replay 로 렌더해 재구성(세션=5h % 직접·주간 한도·리셋 시각).

- ✅ `ctx_used.txt` — 신형 idle footer `N% context used`(스크린샷 실측, 2026-07-18).
  이건 **사용량%**(작을수록 여유)라 잔량 계열과 방향이 반대다: 원격 탭 Claude 가 98%
  인데 pytmux 상태줄엔 `ctx:2%` 로 뒤집혀 뜨던 버그를 `claude_usage`/`claude_context_pct`
  회귀로 고정(사용%는 뒤집지 않고 그대로 표시).

나머지는 아직 **합성**(synthesized)이다 — `claude.py` 주석의 문서화 포맷으로 만든 것이라
실화면 1:1 일치의 객관 근거는 아직 아니다.

## 코퍼스 감사 보강(2026-06-16, captures/playground.local + woojinkim ~708 로그)
다른 머신 캡처까지 동기화해 `scripts/audit_scrape.py`(프레임 재생+휴리스틱)·
`scripts/extract_frame.py`(ESC[H 홈 경계로 깨끗한 단일 프레임 추출)로 실화면을 점검:
- ✅ `feedback.txt` — **실 캡처로 교체**. 실문구 `● How is Claude doing this session?
  (optional)` + `1: Bad  2: Fine  3: Good  0: Dismiss`(합성본엔 `●`·`(optional)` 없었음).
- ✅ `inline_limit.txt` — **신규 실 캡처**. footer 인라인 한도
  `You've used 93% of your session limit · resets 1:40pm (Asia/Seoul) · /usage-credits…`.
  `parse_inline_limit` 회귀 + 사용률경고≠차단(claude_limit/api_error 모두 False) 고정.

## TODO — 남은 실 캡처 보강
- [ ] `limit.txt` — **실제 사용량 리밋(차단) 화면**(가장 중요·가장 드묾). 2026-06-16
      코퍼스 감사에서도 **클린 캡처 확보 실패**: 코퍼스의 "limit reached"·"usage limit"
      매칭은 전부 Claude 가 **소스/문서**(HANDOFF.md·test 리터럴)나 `/usage-credits`
      슬래시 도움말을 표시한 것이라 진짜 차단 배너가 아니었다(이건 `claude_limit` 가
      소스줄 필터+하드스톱 가드로 올바로 무시 — F2 정밀성 방증). 실제 한도 발생 순간을
      M16 `claude-limit` 훅 통지로 캡처해야 한다. 그때까지 합성 유지.
- [ ] `ctx_compact.txt` — "until auto-compact" 표기. 코퍼스의 매칭은 Claude 가 이
      감사 스크립트 소스를 표시한 것뿐이라 미확보. 합성 유지(test 12% 단언).
- [ ] 좁은 폭(모바일)·ssh/ConPTY 조각 도착 화면 변형.

실 캡처는 `pytmux` 안에서 Claude 를 돌리고 REC(`captures/`) 로 떠서
`python3 pytmux.py replay <log> --cols N --rows M`(또는 `scripts/extract_frame.py
<log> "<target>"` 로 레거시 비-2026 로그의 깨끗한 프레임 추출) 로 렌더해 저장한다.
**주의**: 캡처가 이 세션 자신(meta)이면 내 명령·대화가 Claude UI 문자열을 흉내내
오염되므로(예: pane-2), 실제 Claude 패널(pane-1/4/5/7)에서만 떠야 한다.
