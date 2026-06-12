# Claude 스크롤백 프롬프트-단위 히스토리 (IMPROVEMENT §3.8)

> 요청(2026-06-12): "Claude Code 패널을 위로 스크롤하면 이전 작업 **전체**를 보여주지
> 말고 **프롬프트 단위**로 묶어 목록을 보여주고, 그중 하나를 선택해 펼치면 그 프롬프트로
> 진행된 기록(응답·툴 실행)을 보여주도록." = 위로 스크롤 → 프롬프트 히스토리.

목표: Claude 패널의 긴 raw 스크롤백을 사용자 **프롬프트** 경계로 탐색 가능하게 한다.

## 핵심 메커니즘 — 프롬프트↔스크롤백 anchor (회전 강건)

pyte 스크롤백은 `deque(maxlen=HISTORY=10000)` 라 가득 차면 옛 줄이 빠져 **상대 인덱스가
밀린다**. 그래서 **절대 라인 인덱스**(anchor)를 쓴다:

- `_ScrollbackScreen.hist_total`(model.py): top 히스토리에 들어간 **누적** 줄 수(단조 증가,
  `index()` 에서 +1, `reset()`/RIS 에서 0). deque 가 회전해도 줄지 않는다. 현재 deque 의
  줄 `i` 의 절대 인덱스 = `hist_total - len(top) + i`.
- `Pane.current_anchor()` = `hist_total + cursor.y` — **지금 커서 줄**의 절대 인덱스.
  프롬프트 제출 시점에 호출 → 그 프롬프트 줄의 anchor.
- `Pane.scroll_to_anchor(anchor)`: 그 줄을 뷰포트 맨 위로. render 의 `start = len(hist) -
  scroll` 를 anchor 로 풀면 **`scroll = hist_total - anchor`**, `[0, len(hist)]` 클램프
  (아직 라이브 화면 위면 0=라이브, 회전으로 사라졌으면 맨 위).

검증: `tests/test_model.py::test_scroll_to_anchor_lands_line_at_top` (40줄 피드 후 회전 겪어도
MARKER 줄이 정확히 뷰포트 맨 위).

## Stage 1 — 구현 완료 (CL 미정, 2026-06-12)

서버가 프롬프트마다 anchor 를 잡고, 그 위치로 점프하는 명령을 제공한다.

- **anchor 캡처**(servermixin): `prompt_history` 에 프롬프트를 쌓는 **두 경로**(`_track_prompt`
  로컬 입력 / `_scan_claude` 데스크탑-원격 입력) 모두에서, append 시 `current_anchor()` 를
  `pane._prompt_anchors` 에 **인덱스 정렬**로 함께 쌓고, 200 초과 트림도 함께 한다.
- **직렬화 안 함**(panestate): anchor 는 화면-수명(재시작 시 `hist_total` 리셋)이라 직렬화하지
  않고, 복원 시 복원된 `prompt_history` 길이에 맞춰 `None`(점프 불가)으로 패딩해 정렬 유지.
- **점프**(servermixin `scroll_to_prompt(sess, index)`): 히스토리 팝업 번호(상태로 보낸 마지막
  `_PROMPT_HIST_TAIL=30` 슬라이스 기준)를 전체 prompt_history 절대 인덱스로 환산(base =
  len-30)해 그 anchor 로 `scroll_to_anchor`. anchor `None`·범위 밖이면 무동작.
- **디스패치**(serverio): `scroll_to_prompt` 액션을 getattr 가드로(플러그인 삭제 시 no-op).
- **명령**(claude-code 플러그인): `prompt-jump <n>` → `send_cmd("scroll_to_prompt", index=n-1)`.
  n = 프롬프트 히스토리 팝업의 번호(1 기반).

사용: 헤더 클릭/`:prompt-history` 로 번호 매긴 프롬프트 목록을 보고, `:prompt-jump 3` 으로
3번 프롬프트가 제출된 위치로 스크롤 점프(그 프롬프트로 진행된 기록을 위에서부터 본다).

테스트: `test_server.py::{test_prompt_anchors_aligned_with_history,
test_scroll_to_prompt_maps_tail_index_and_guards}`, `test_model.py::test_scroll_to_anchor_…`.

## Stage 2 — 후속 (UX 결정 필요)

1. **위로 스크롤 → 오버레이 자동 표시**: Claude 패널에서 스크롤백 맨 위(또는 특정 키)에
   닿으면 raw 스크롤 대신 프롬프트 목록 오버레이를 띄운다. **열린 결정**: ① 스크롤-업이
   raw 스크롤을 **대체**하나 vs 별도 키/제스처인가(raw 스크롤도 필요한 사용자 보호), ②
   Claude 패널 한정(비-Claude 는 기존 raw 스크롤 유지). 안전상 Stage 1 은 명령(`prompt-jump`)
   으로만 노출하고, 스크롤 거동 변경은 분리.
2. **팝업에서 직접 선택 점프**: 현재 `_open_prompt_history` 는 InfoScreen(표시 전용). Enter
   선택 콜백을 받아 `scroll_to_prompt` 를 바로 쏘게 확장(InfoScreen 에 on-select 추가 또는
   ChooseList 류로 전환). 그러면 `prompt-jump <n>` 타이핑 없이 목록에서 골라 점프.
3. **선택 '펼치기'(그 프롬프트 구간만 표시)**: 다음 프롬프트 anchor 까지를 한 구간으로 떼어
   접힌 목록↔펼친 구간 뷰. anchor 가 인접 프롬프트 경계를 주므로 구간 = `[anchor_i, anchor_{i+1})`.

## 주의/한계

- anchor 는 **제출 시 커서 줄** 기준 — Claude 컴포저가 화면 하단이라 점프하면 그 프롬프트
  입력 줄이 맨 위에 온다(응답은 그 아래). 충분히 직관적.
- `/clear`·compact·RIS 는 스크롤백과 `hist_total` 을 리셋 → 그 이전 anchor 는 클램프로
  라이브/맨위 폴백(무해).
- `_PROMPT_HIST_TAIL`(=30) 은 상태 슬라이스(`__init__` client_status)와 **동일해야** 한다
  (팝업 번호↔절대 인덱스 환산). 한쪽만 바꾸면 점프 대상이 어긋난다.
