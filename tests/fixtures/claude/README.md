# Claude Code 화면 골든 픽스처 (M8)

`pytmuxlib/claude.py` 의 휴리스틱(상태/사용량/잔량%/리밋 파서)이 실제 Claude Code
화면 텍스트에서 기대값을 내는지 회귀 고정하는 픽스처다. `test_token_saver.py` 가
각 `.txt` 를 읽어 `claude_state`/`claude_usage`/`claude_context_pct`/`parse_reset_delay`
의 결과를 단언한다.

## 출처와 한계 — ⚠️ 합성(synthesized)
현재 파일들은 `claude.py` 주석에 문서화된 **실제 표시 포맷 문자열**(예:
`✽ Crunching… (38s · ↓ 1.9k tokens)`, `⏵⏵ auto mode on (shift+tab to cycle)`)로
**합성**한 것이다(docs/TOKEN_SAVING_SCENARIO.md M8, 사용자 결정: "합성 + 추후
실캡처 보강"). 즉 정규식이 **가정한 포맷 자신**에 대해선 회귀를 막지만, Claude Code
의 **실제 현행 화면과 1:1 일치하는지의 객관 근거는 아직 아니다**.

## TODO — 실 캡처로 보강
다음은 실 Claude 세션을 떠서 교체/추가해야 한다(헤드리스 대리 불가):
- [ ] `limit.txt` — **실제 사용량 리밋 화면**(가장 중요·가장 드묾). 현재 합성은
      "resets at 5pm" 가정. 실제 문구·줄 위치가 다르면 `_RESET_RE*`·`claude_state`
      리밋 분기를 보정.
- [ ] `ctx_low.txt`/`ctx_compact.txt` — **잔량/압축 표기의 실제 문자열과 의미**
      (잔량% vs 사용%). M11 트리거 방향이 여기에 달림(claude_context_pct 주석 참조).
- [ ] 좁은 폭(모바일)·ssh/ConPTY 조각 도착 화면 변형.

실 캡처는 `pytmux` 안에서 Claude 를 돌리고 `capture-pane -S`(또는 servercapture)로
떠서 이 디렉터리에 저장하면 된다.
