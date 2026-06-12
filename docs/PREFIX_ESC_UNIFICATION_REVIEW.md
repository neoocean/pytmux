# prefix 키 ↔ esc(명령) 모드 통합 검토

> 작성: 2026-06-12 · 요청: "prefix 키와 esc 모드를 통합할 수 있는지 검토해 문서화"
> 대상 코드: `pytmuxlib/client.py` `_handle_prefix`(prefix 모드) · `_handle_esc_mode`(esc 모드)
> 관련: [HANDOFF.md](HANDOFF.md) · [FEATURES.md](FEATURES.md)

## 1. 요약(결론 먼저)

- **완전 통합(두 모드를 하나로)은 권장하지 않는다.** 두 모드는 진입 철학·생애·키
  의미가 달라, 합치면 둘 중 하나의 기존 사용성을 반드시 깬다(특히 `n`/`p` 키 의미 충돌).
- **현실적 권고 = "공유 디스패치 + 두 진입점 유지"(옵션 C).** prefix 와 esc 가 *같은 의도*
  (패널 이동·탭 전환·분할·명령 프롬프트)에 대해 **하나의 액션 디스패처**를 호출하도록
  내부만 합치고, 진입 키(Ctrl-b vs ESC/`)와 생애(one-shot vs sticky)는 그대로 둔다.
  사용자 표면은 안 바뀌고 중복 코드만 준다.
- 사용자가 정말 단일 모드를 원하면 **옵션 B(‘sticky prefix’)** 가 차선 — prefix 진입 후
  esc 처럼 머무르게 하되, 키 충돌은 esc 쪽 의미로 통일하고 prefix 전용 키는 보존한다.
  단 이는 tmux 근육기억(`prefix` 1회=1명령)을 깨므로 옵트인 설정으로 둔다.

## 2. 현황 — 두 모드 비교

| 항목 | prefix 모드 (`_handle_prefix`) | esc 모드 (`_handle_esc_mode`) |
|---|---|---|
| 진입 | `prefix_key`(기본 `ctrl+b`, 설정 가능) 1회 | `ESC` 또는 `` ` ``(2026-06-12 추가) |
| 생애 | **one-shot** — 키 1개 처리 후 즉시 `normal` 복귀 | **sticky** — 방향키 등으로 계속 머무름, 액션·`Esc`·`:` 등에서 빠짐 |
| 성격 | tmux 호환 명령 키맵(풀 보캐) | 마우스리스 **포커스 내비게이션**(탭바·헤더·상태바 포커스 링, blink 피드백) |
| 셸로 진입키 전달 | prefix 1회 더 → 셸로 `prefix_bytes` | `` ` `` 1회 더 → 셸로 리터럴 `` ` ``(double-tap); ESC 는 Shift+ESC 로만 전달 |
| 사용자 정의 바인딩 | `self.bindings`(config `bind`) 우선 | 없음 |

### 2.1 키 의미 — 겹침과 **충돌**

같은 키가 두 모드에서 **다른 동작**을 하는 게 통합의 핵심 장애물이다.

| 키 | prefix | esc | 충돌? |
|---|---|---|---|
| 방향키 | select-pane(이동) | select-pane(이동, 모드 유지) | 같음(생애만 다름) |
| 숫자 | select-window(0-based 입력? 실제 1-based 표시→-1) | select-window(1-based) | 같음(둘 다 표시기준 전환) |
| `:` | 명령 프롬프트 | 명령 프롬프트 | 같음 |
| `n` | **next-window** | **new-window(새 탭)** | ⚠ **충돌** |
| `p` | **prev-window** | **new-pane(상하 분할)** | ⚠ **충돌** |
| `c` | new-window | — | prefix 전용 |
| `%`/`"` | split lr/tb | — | prefix 전용 |
| `x` | kill-pane | — (탭바 포커스 시 `x`=kill-tab) | 의미 다름 |
| `?` | — | help 팝업 | esc 전용 |
| `h` | — | Claude 헤더 포커스 | esc 전용 |
| ↑(최상단)·↓(최하단) | — | 탭바/헤더/상태바 포커스 링 | esc 전용 |

`n`/`p` 의 의미가 정반대 계열이라(탭 순회 vs 생성/분할), 단순 합치기는 둘 중 하나의
근육기억을 깬다.

## 3. 통합 옵션

### 옵션 A — 완전 통합(단일 모드) ❌ 비권장
ESC/`/prefix 모두 같은 단일 모드로 진입, 키맵 1벌.
- **문제**: ① one-shot vs sticky 를 하나로 못 둠(tmux 사용자는 one-shot, 내비 사용자는
  sticky 기대) ② `n`/`p` 충돌을 한쪽으로 강제 → 반대쪽 사용자 깨짐 ③ prefix 의 풀 보캐
  (%/"/x/z/o/space/{}/!/H J K L/&/d/[/]/= 등)를 sticky 로 옮기면 esc 모드가 비대해지고
  방향키 연속 내비 중 오타 1키로 파괴적 액션(kill-pane 등) 위험.
- 결론: 표면 단순화 이득 < 사용성·안전 손실.

### 옵션 B — ‘sticky prefix’(prefix 를 머무르게) ⚠ 옵트인이면 가능
prefix 진입 후 esc 처럼 머무르며 연속 명령. 키 충돌은 esc 의미로 통일.
- **장점**: 모드 1개 멘탈모델. 연속 패널/탭 조작이 prefix 에서도 빨라짐.
- **문제**: tmux `prefix=1회 1명령` 근육기억 파괴. 파괴적 키(x/&) sticky 노출 위험 →
  확인 프롬프트 의존도 ↑. ⇒ **반드시 설정 토글(`sticky-prefix off` 기본)** 로.

### 옵션 C — 공유 디스패치 + 두 진입점 유지 ✅ 권장
표면(진입 키·생애)은 **그대로**, 내부에서 *동일 의도 액션*을 **하나의 함수 테이블**로
모은다. 예:
```
_NAV_ACTIONS = {
  "select_pane_dir": ..., "select_window": ..., "command_prompt": ...,
  "new_window": ..., "split_tb": ..., ...
}
```
`_handle_prefix`/`_handle_esc_mode` 는 각자의 키→의도 매핑만 갖고(여기서 `n`/`p` 의 모드별
의미 차이를 흡수), 공통 의도는 같은 구현을 부른다.
- **장점**: 사용자 표면 0 변화(회귀 위험 최소) · select-pane/select-window/command-prompt
  등 중복 로직 1벌화 · 차후 키맵 일관성 점검 용이.
- **단점**: 사용자 체감 "통합"은 아님(내부 리팩토링). 다만 요청의 "통합 가능성 검토" 답으로는
  *안전하게 통합 가능한 범위*가 바로 이 공유층이다.
- **위험**: 낮음. 순수 리팩토링이라 기존 esc/ prefix 테스트(`tests/test_client.py`)가 그대로
  회귀 가드.

### 옵션 D — esc 모드에 prefix 전용 명령 일부 흡수(부분) ➕ 옵션 C 와 병행 가능
esc 모드에 충돌 없는 prefix 명령(`z`=zoom, `space`=layout, `c`=new-window 등 esc 에 없는 것)
중 **비파괴적인 것만** 추가해 "esc 하나로 대부분 된다"에 근접. 파괴적(x/&)·충돌(n/p) 키는 제외.

## 4. 권고 로드맵

1. **지금**: 옵션 C(공유 디스패치)로 내부 통합 — 표면 무변화, 중복 제거. (별도 작업으로 구현)
2. **선택**: 옵션 D 로 esc 모드에 비파괴 prefix 명령 소수 흡수(요청 시).
3. **옵트인**: 단일 모드를 원하면 옵션 B 를 `sticky-prefix` 설정으로(기본 off).
4. `n`/`p` 충돌은 **해소하지 않는다**(각 모드 관습 유지) — 통합층은 ‘의도’ 레벨에서만 공유.

## 5. 통합으로 건드릴 코드(옵션 C 기준, 구현 시)
- `client.py:_handle_prefix`(약 2537~2633) · `_handle_esc_mode`(약 2774~2893): 키→의도 매핑만
  남기고 의도 구현을 공통 헬퍼로 추출.
- 회귀 가드: `tests/test_client.py` 의 `test_esc_*` · prefix 관련 테스트가 표면 불변을 단언.
