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

## Stage 2 ②③ — 구현 완료 (CL 미정, 2026-06-12): 선택 점프 → 선택 '펼치기'

`_open_prompt_history` 의 InfoScreen 을 **표시 전용 → 선택 동작**으로 확장했다. ② 에서 선택
점프를 넣고, ③ 에서 **Enter/클릭 = '펼치기'**(원 요청의 헤드라인)로 승격했다 — 그 프롬프트로
진행된 스크롤백 기록(응답·툴 실행) 구간 `[anchor_i, anchor_{i+1})` 만 떼어 별도 팝업으로 본다.
점프(②)는 펼친 팝업의 **[j]** 와 `prompt-jump <n>` 명령으로 유지(잃지 않음).

- **InfoScreen `select_cb`**(clientscreens): 선택형 콜백 파라미터. **Enter**(또는 **행 클릭** —
  마우스 1급) 시 현재 ListView 인덱스를 `select_cb(idx)` 로 넘기고 닫는다. `None` 이면 기존
  읽기전용(아무 키나 닫기)이라 REC/토큰/사용량 등 공유 InfoScreen 무영향. 구분선/빈 줄(skip)은
  제외.
- **구간 추출**(model `Pane.prompt_segment_lines(a0, a1)`): 절대 라인 인덱스 구간 `[a0, a1)` 의
  스크롤백 평문을 떼어 낸다. 절대→deque/버퍼 매핑은 render 와 동일(`full = hist + buffer`,
  `full[j]` 절대 = `j + (hist_total − len(hist))`). `a1=None` 이면 현재 맨 아래까지(마지막
  프롬프트). deque 회전으로 앞이 소실됐거나 안전 캡(기본 800줄) 초과면 `truncated=True`.
- **서버 회신**(servermixin `prompt_segment(sess, index)` + `handle_server_request` 의
  `request_prompt_segment`): index→`[anchor_i, anchor_{i+1})` 환산은 `scroll_to_prompt` 와 동일
  (`base = len − _PROMPT_HIST_TAIL`). 다음 anchor 가 없거나 None(복원분)이면 `a1=None`(맨
  아래까지). anchor None/범위 밖이면 `{"ok": False}`. 회신 `{"t":"prompt_segment", ok, index,
  prompt, lines, truncated}` 은 **코어 변경 0** — 미지 action/메시지를 코어가 플러그인에 위임
  (`handle_server_request`/`handle_message`)하는 기존 경로(ncd `nc_list` 식)를 그대로 탄다.
- **펼친 팝업**(client `_on_prompt_segment_msg`): 구간 줄을 InfoScreen 으로(제목=프롬프트 첫
  줄). 끝에 구분선+`[j] 이 위치로 라이브 점프 · Esc 닫기` footer(`hide_key="j"` →
  `scroll_to_prompt` 점프 후 닫힘). `truncated` 면 앞에 잘림 안내, 빈 구간이면 "(기록 없음)".
  `ok=False` 면 팝업 대신 안내 메시지.
- **명령**: `prompt-expand <n>`(= `prompt-jump <n>` 대칭) 도 추가 — n번 프롬프트 구간 펼치기.

테스트(7건): `test_model::test_prompt_segment_lines_extracts_absolute_range`(구간 추출·a1=None·
회전 truncated), `test_server::test_prompt_segment_maps_index_and_guards`(index 환산·a1 경계·
가드), `test_client::{test_prompt_history_enter_expands_selected_prompt, ..._row_click_expands,
..._enter_on_footer_does_not_expand, test_prompt_segment_msg_opens_expanded_popup_with_jump([j]→
점프), test_prompt_segment_msg_not_ok_shows_message}`.

사용: 헤더 클릭/`:prompt-history` → **↑↓ 로 고르고 Enter(또는 클릭)** → 그 프롬프트의 기록이
펼쳐진다. 그 팝업에서 **[j]** 면 실제 터미널이 그 위치로 스크롤(라이브 점프).

## Stage 2 ① — 구현 완료 (CL 미정, 2026-06-12): 위로 스크롤 → 오버레이 자동 표시

Claude 패널에서 **스크롤백 맨 위에 닿은 뒤 한 번 더 위로** 스크롤하면 프롬프트 히스토리
오버레이가 뜬다. **제품 결정 확정**: raw 스크롤을 **대체하지 않는다** — 맨 위 도달까지는
그대로 raw 스크롤되고(사용자 보호), 맨 위에서 **추가** 스크롤업일 때만 오버레이가 뜬다.
Claude 패널 한정(비-Claude 는 기존 raw 스크롤 유지).

- **서버 신호**(serverio `_handle_scroll`): 위로 스크롤(delta>0)인데 `_history_len()>0` 이고
  이미 `scroll >= _history_len()`(맨 위)이면 그 클라에 `{"t":"scroll_at_top","pane":id}` 를
  보낸다(`asyncio.create_task(write_msg)`). 코어는 **신호만** — 오버레이 거동은 플러그인 결정.
- **클라 게이트**(claude-code `handle_message`→`_on_scroll_at_top`): 그 패널이 **Claude**이고
  (`pane_claude[id]["claude"]`) **히스토리/프롬프트가 있고** **모달이 안 떠 있으면**
  (`len(screen_stack)<=1`, 중복 방지) `open_prompt_history(pid)` 로 오버레이를 연다. 비-Claude·
  히스토리 없음·모달 열림이면 무동작. 코어 미지 메시지→플러그인 위임(delete-to-disable).

테스트(2건): `test_server::test_scroll_at_top_signals_only_when_already_at_top`(맨 위 전·아래
스크롤·스크롤백 없음은 무신호), `test_client::test_scroll_at_top_opens_prompt_history_for_claude_only`
(Claude·히스토리·모달 게이트).

사용: Claude 패널을 마우스 휠로 위로 굴려 스크롤백 맨 위까지 본 뒤, **맨 위에서 한 번 더**
굴리면 프롬프트 히스토리가 자동으로 뜬다(→ Enter 로 펼치기·[j] 로 점프).

## 주의/한계

- anchor 는 **제출 시 커서 줄** 기준 — Claude 컴포저가 화면 하단이라 점프하면 그 프롬프트
  입력 줄이 맨 위에 온다(응답은 그 아래). 충분히 직관적.
- `/clear`·compact·RIS 는 스크롤백과 `hist_total` 을 리셋 → 그 이전 anchor 는 클램프로
  라이브/맨위 폴백(무해).
- `_PROMPT_HIST_TAIL`(=30) 은 상태 슬라이스(`__init__` client_status)와 **동일해야** 한다
  (팝업 번호↔절대 인덱스 환산). 한쪽만 바꾸면 점프 대상이 어긋난다.
