# pytmux 성능 리뷰 — 전체 코드 재검토 (2026-06-07)

> **상태**: 🟢 C1~C5 구현 진행 중(§4 구현 현황). 이번 패스는 `pytmuxlib/` 전체(~14k LOC)를 서버 핫패스·
> 클라 렌더·Claude/영속 3개 축으로 다시 훑어 **신규 최적화 레버**를 도출한 결과다. 직전
> 스프린트([PERFORMANCE_SCENARIO.md](PERFORMANCE_SCENARIO.md))에서 A1–A5·B1–B11 이
> 이미 끝났으므로, 본 문서는 **그 목록에 없는 net-new 항목**만 싣는다.
>
> 모든 항목은 코드로 직접 확인한 근거(`file:line`)·개선안·효과·위험·검증 게이트를 갖는다.
> 표기: **[검증됨]** = 작성자가 실제 코드를 읽어 확인 / 효과 **높음·중·낮음** ·
> 위험 **낮음·중**. 측정 우선 원칙([PERFORMANCE_SCENARIO.md §0](PERFORMANCE_SCENARIO.md))
> 은 그대로 — 구현 시 `scripts/bench.py` before/after + `tests/run.py` 통과가 게이트다.

---

## 0. 요약 — 채택 후보(우선순위)

| 순위 | 레버 | 위치 | 효과 | 위험 | 비고 |
|---|---|---|---|---|---|
| 1 | **C1** `_char_cells` 메모이즈 | `clientutil.py:24` | 중~높 | 낮음 | 순수함수, 핫패스 다수에서 문자당 호출 |
| 2 | **C2** TabBar `_entries()` 프레임당 1회 | `clientwidgets.py:634,684` | 중 | 낮음 | 동일 기하를 프레임당 2회 재계산 |
| 3 | **C3** 합성 루프 Style/dict 상수 호이스트 | `client.py:1063,1078,1162,1190` | 낮음 | 낮음 | 셀/프레임당 불변 Style·dict 재할당 |
| 4 | **C4** status 정적 필드 분리(델타) | `serverio.py:118` | 중 | 중 | 스트리밍 중 30Hz로 ~55필드 전송 |
| 5 | **C5** `_account_token_total` 프레임당 1회 | `serverio.py:121,190` | 낮음 | 낮음 | status 빌드서 전 패널 2회 순회 |

> **권장 착수 순서**: C1 → C2 → C3(저위험 즉효 묶음) → C5 → C4(프로토콜 변경, 측정 후).
> §2 의 "기각" 항목(자동분석이 과대평가한 것)은 **착수하지 않는다**.

---

## 1. 채택 후보 (검증된 신규 레버)

### C1. `_char_cells` 메모이즈 ★저위험·핫패스 다수 [검증됨]

**근거**: `clientutil.py:24` 의
```python
def _char_cells(ch: str) -> int:
    return 2 if wcwidth(ch) == 2 else 1
```
는 메모이즈가 없다(모듈은 `from functools import lru_cache` 를 이미 import 하면서도
이 함수엔 미적용). 이 함수는 **렌더 핫패스 곳곳에서 문자 1개당** 호출된다:
- 클라 합성 셀 루프(`client.py` 의 패널 본문 blit — 행×세그먼트×문자),
- TabBar 폭 계산 `widths = [sum(_char_cells(c) for c in s) ...]`(`clientwidgets.py:566`)
  + 세그먼트 폭 `sum(_char_cells(c) for c in text)`(`:658`) + `active_tab_xrange`(`:685`)
  → C2 의 2회 호출과 곱해진다,
- 상태줄 폭 합산(`clientwidgets.py` `_render_main` 의 누적 폭 계산).

`wcwidth(ch)` 는 코드포인트 테이블 이분탐색이라 문자당 비용이 0이 아닌데, 실 화면은
ASCII(공백·영숫자)가 절대다수라 캐시 적중률이 거의 100% 다.

**개선**: 순수함수이므로 `@lru_cache(maxsize=256)` 한 줄. 입력 도메인이 사실상 유한(자주
쓰는 문자 수백 개)이라 캐시 폭주 없음.
```python
@lru_cache(maxsize=256)
def _char_cells(ch: str) -> int:
    return 2 if wcwidth(ch) == 2 else 1
```
**효과**: 중~높(텍스트가 빽빽한 패널·다중 탭에서 프레임당 수천 호출의 `wcwidth` 왕복 제거).
**위험**: 낮음(부작용 없는 순수함수, 해시 안정). **검증**: `tests/run.py` 동작 불변 +
`poc/feed_profile.py`/`scripts/bench.py` 의 전 패널 render+직렬화 p50 비교, 적중률 측정.

### C2. TabBar `_entries()` — 프레임당 1회로 [검증됨]

**근거**: `TabBar.render_line`(`clientwidgets.py:634`)과 `active_tab_xrange`(`:684`)가
**같은 합성 프레임에서 `self._entries()` 를 각각 1회씩** 부른다(둘 다 호출됨은
`_composite` 가 탭바 렌더 후 연결부 좌표를 얻는 구조). `_entries()`(`:557`)는 매번
`_labels()`·`widths`(문자당 `_char_cells`)·스크롤 보정·항목 루프를 다시 돈다 — 탭이
많을수록(20+) 동일 기하를 프레임당 두 번 계산한다.

**개선**: `(tuple(탭 index/active/이름), self.sel, self._scroll, self.size.width)` 를 키로
한 프레임 캐시. **주의**: `_entries()` 는 `self._scroll` 을 갱신하는 **부작용**이 있어
(`:575-580`), render_line 이 먼저 계산해 스크롤을 확정한 뒤 캐시하고 `active_tab_xrange`
가 그 캐시를 읽게 순서를 보장해야 한다(또는 스크롤 보정을 별도 메서드로 분리). 탭·선택·
폭이 바뀌면 캐시 무효화.
**효과**: 중(탭 많을 때 폭 계산·스크롤 로직 중복 제거; C1 과 곱해 더 큼).
**위험**: 낮음(단 스크롤 부작용 순서 보존 필수). **검증**: `tests/ptyshot` 탭바 시각
회귀(스크롤·드래그·활성 연결부) + `_entries` 호출 1회 확인.

### C3. 합성 루프 안의 Style/dict 상수 호이스트 ★저위험 [검증됨]

**근거**: `client.py` `_composite` 핫패스가 프레임/셀마다 불변 객체를 재할당한다.
- `:1063` 커서 셀 `st + Style(reverse=True)` — `Style(reverse=True)` 를 프레임마다 새로.
- `:1078-1081` `bbits`(11항목)·`brev`(역인덱스 dict comp)를 **매 `_composite` 호출마다**
  새로 만든다(완전 상수).
- 타이틀바 루프의 `Style(color="black", bgcolor=...)`·`Style(color="grey50")`,
  선택 하이라이트 `sstl + Style(reverse=True)`(선택 셀 수만큼, 대형 선택서 수백~수천 셀).

**개선**: 불변값은 모듈 상수로 — `_REVERSE = Style(reverse=True)`, `_BBITS`/`_BREV`,
타이틀바 active/inactive/border Style. 선택 하이라이트는 `@lru_cache` 로 `st → st+reverse`
를 메모(Style 은 hashable·immutable). dict 역인덱스는 한 번만 만들어 재사용.
**효과**: 낮음(개별 할당은 ns 급이나 대형 선택·풀리페인트서 셀당 누적). **위험**: 낮음
(상수화/메모, 시각 결과 동일). **검증**: `tests/ptyshot` 시각 회귀 + 합성당 Style 생성 수.

### C4. status 페이로드 — 정적 옵션 분리(델타) [검증됨]

**근거**: `_status_msg`(`serverio.py:118`)는 **~55필드 dict 를 매 flush(status_changed
시) 통째로 재구성·JSON 인코딩**한다. 그중 약 절반(`claude_header`, `single_border`,
`token_budget_day/session/5h/account`, `claude_ctx_threshold/action/min_interval`,
`auto_doc_clear`, `claude_auto_mode`, `claude_rules`, `claude_long_turn_sec`,
`claude_repeat_alert`, `token_budget_resume_gate`, `claude_budget_plan` …)은 **사용자가
설정 팝업에서 토글할 때만** 바뀌는 전역 옵션이다. Claude 가 스트리밍 중이면 토큰이 매
프레임 변해 `status_changed` 가 30Hz로 서므로, 정적 옵션 ~25개가 초당 30번 재직렬화·재전송된다.

이미 같은 부류의 최적화 선례가 있다 — **B11**(prompt-history 는 바뀔 때만 전송,
`_pane_claude_entry`)·**B2**(행 델타). status 도 같은 패턴 적용 대상이다.

**개선**: status 를 (a) attach·구조 resync 시 1회 보내는 **정적 opts 블록**과 (b) 매
flush 의 **동적 블록**(claude_usage/tokens/tok5h_pct/warn/pending/windows…)으로 분리.
정적 블록은 `set_*` 토글 시에만 재전송(변경 비트). 클라는 마지막 opts 를 유지.
**효과**: 중(스트리밍 중 직렬화·ssh 트래픽 — status 페이로드의 절반 절감). **위험**: 중
(프로토콜 분기·구클라 호환; PROTO_VERSION 협상 기반이 이미 있음). **검증**: `tests/`에
"정적 필드는 토글 시에만 전송" 회귀 + 한 스트리밍 구간의 status 바이트 before/after.

### C5. `_account_token_total` — status 빌드당 1회로 [검증됨]

**근거**: `_status_msg` 가 한 번 만들어질 때 `_account_token_total` 이 **두 번** 전 패널을
순회한다 — 직접 `:121`(`claude_tokens`/`tok5h_pct` 용)과 `:190` `_budget_level_for` 가
내부에서(`serverclaude.py:1001`) 한 번 더. `_account_token_total`(`serverclaude.py:699`)은
`_all_panes()`(세션→탭→패널 중첩 순회) 전체를 돌며 계정 일치분을 합산한다. status 가
status_changed 시(스트리밍 중 30Hz) 빌드되므로 그 빈도로 2× 전 패널 합산.

**개선**: `_status_msg` 진입에서 `tot = self._account_token_total(active_pane)` 를 **한 번**
계산해 `claude_tokens`·`tok5h_pct`·`budget_level` 에 공유(예: `_budget_level_for(pane,
total=tot)` 인자 추가). 또는 status 빌드 1프레임 TTL 메모.
**효과**: 낮음(패널 적으면 작지만, 10+ 패널·다세션서 합산 절반). **위험**: 낮음
(같은 프레임 내 값 재사용, 동작 불변). **검증**: `tests/run.py` + status 값 동일 확인.

---

## 2. 기각 — 자동분석이 과대평가한 항목 (착수 금지)

> 전체 코드 병렬 리뷰가 올린 후보 중, **코드를 직접 읽어 효과/빈도가 과장됐거나 이미
> 방어된** 것들. 보안 리뷰 때와 같은 원칙(추정 ≠ 검증)으로 기록해 재제안을 막는다.

- **`screen_text()` 가 settled 프레임에도 실행된다** — **틀림**. `serverclaude.py:424`
  의 `if p._feed_seq == p._scan_seq and not pending: continue` 가 **screen_text 호출
  전에** settled 패널을 건너뛴다. 텍스트 추출은 dirty/pending 패널에서만 일어난다(이미 최적).
- **`restart_check` 의 `json.loads(json.dumps(...))` 왕복**(`serverpersist.py:276`) —
  **의도된 동작**. restart-check 는 **사용자가 부르는 드라이런**(핫패스 아님)이고, 그
  왕복 자체가 "직렬화→역파싱 round-trip 안전"을 검증하는 점검 항목(`:266`)이다. 제거하면
  점검 의미가 사라진다. 빈도·목적 모두 최적화 대상 아님.
- **`_save_opts` 가 토글마다 20필드 전체 dump**(`serverpersist.py:336`) — 사용자
  설정 토글(드묾)에서만 호출. 핫패스 아님 + 작은 파일. dirty-flag 추가는 복잡도만 늘림.
- **`_account_token_total`/`_all_panes` 가 `_scan_claude` 에서 프레임당 호출** —
  **과장**. scan 안의 호출(`serverclaude.py:521,646`)은 `new_cl=="limit"` 또는
  `claude_ctx_autoclear and not p._ctx_fired and …` 로 **단락 평가**돼 응답 완료 경계·
  특정 기능 ON 에서만 드물게 돈다. 프레임당 상시 비용 아님. (상시 경로는 C5 의 status 빌드.)
- **usagelog `read(limit)` 가 전체 파일 로드**(`usagelog.py:51`) — 토큰로그 팝업(모달,
  사용자 질의)에서만. 핫패스 아님. 로그가 수만 줄로 커지면 그때 tail seek 고려(현재 보류).
- **`claude.py` 컨텍스트% 정규식 3종 순차 검색** — 결합 정규식은 가독성·테스트만
  해치고 이득 미미(검색당 µs 급, scan 자체가 dirty 게이트). 보류.

---

## 3. 측정 게이트 (구현 시)

구현은 **레버 1개 = 체인지리스트 1개** 원칙으로, 각 단계마다:
```sh
python scripts/bench.py      # before (baseline)
# ... C1/C2/… 구현 ...
python scripts/bench.py      # after — 같은 머신/파라미터
python tests/run.py          # 동작 불변(전부 통과)
```
- C1/C2/C3: 클라 렌더 축 — 전 패널 render+직렬화 p50, ptyshot 시각 회귀.
- C4/C5: status 직렬화 바이트·전 패널 합산 횟수, status 값 동일성 회귀.

관련 문서: [PERFORMANCE_SCENARIO.md](PERFORMANCE_SCENARIO.md)(A/B 레버 이력·측정 원칙) ·
[IMPROVEMENT_OPPORTUNITIES.md](IMPROVEMENT_OPPORTUNITIES.md)(제품 차원 리뷰) ·
[HANDOFF.md](HANDOFF.md) §9(throughput 작업 이력).

---

## 4. 구현 현황 (2026-06-07)

> 레버 1개 = 체인지리스트 1개. 각 단계 `tests/run.py` 통과 후 git push + p4 번호 CL submit.

| 레버 | 상태 | 비고 |
|---|---|---|
| C1 `_char_cells` lru_cache | ✅ 구현 | `clientutil.py:24` `@lru_cache(256)`. 회귀 `test_char_cells_memoized_correct`. |
| C2 TabBar `_entries()` 캐시 | ✅ 구현 | `clientwidgets.py` `_entries` (폭·sel·스크롤·탭 기하) 시그니처 프레임 캐시. 스타일은 키 제외(render_line 이 매 프레임 재적용). 회귀 `test_tabbar_entries_cached_and_consistent`. |
| C3 Style/dict 상수 호이스트 | ✅ 구현 | `clientutil.py` 에 `_REVERSE_STYLE`·`_TB_*`·`_BOX_BITS/REV` 상수 + `_with_reverse` lru_cache. `client.py` `_composite` 가 셀/프레임마다 새로 만들던 Style·dict 제거. 회귀 `test_with_reverse_and_box_constants`. |
| C4 status 정적 옵션 분리 | ⏳ | |
| C5 `_account_token_total` 1회 | ✅ 구현 | `_budget_level_for(pane, total=None)` 에 선택 인자 추가, `serverio._status_msg` 가 `claude_tokens` 용으로 계산한 합계를 넘겨 status 빌드당 전 패널 순회 1회. 회귀 `test_budget_level_for_accepts_precomputed_total_c5`. |
