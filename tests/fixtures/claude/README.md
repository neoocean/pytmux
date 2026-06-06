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

나머지는 아직 **합성**(synthesized)이다 — `claude.py` 주석의 문서화 포맷으로 만든 것이라
실화면 1:1 일치의 객관 근거는 아직 아니다.

## TODO — 남은 실 캡처 보강
이 녹화엔 없어 못 떴다(다음 발생 시 캡처 필요):
- [ ] `limit.txt` — **실제 사용량 리밋 화면**(가장 중요·가장 드묾). 이 세션 녹화 중
      한도에 안 걸려 미수집. M16 `claude-limit` 훅으로 발생 순간을 통지받아 캡처.
      실제 문구·줄 위치가 다르면 `_RESET_RE*`·`claude_state` 리밋 분기를 보정.
- [ ] `feedback.txt` — "How is Claude doing this session" 프롬프트(녹화에 없음).
- [ ] `ctx_compact.txt` — "until auto-compact" 표기(녹화엔 low/remaining 만 있었음).
- [ ] 좁은 폭(모바일)·ssh/ConPTY 조각 도착 화면 변형.

실 캡처는 `pytmux` 안에서 Claude 를 돌리고 REC(`captures/`) 로 떠서
`python3 pytmux.py replay <log> --cols N --rows M` 로 렌더해 이 디렉터리에 저장한다.
**주의**: 캡처가 이 세션 자신(meta)이면 내 명령·대화가 Claude UI 문자열을 흉내내
오염되므로(예: pane-2), 실제 Claude 패널(pane-1/4/5/7)에서만 떠야 한다.
