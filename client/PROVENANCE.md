# PROVENANCE — 이 트리의 코드가 어디서 왔는가

> 이 파일은 **라이선스 경계의 기록**이다. 코드를 추가·수정할 때 여기 적힌 규칙을 어기면
> 산출물의 라이선스가 깨진다. 설계 배경은 pytmux 저장소의
> `docs/internal/PYTMUX_CLIENT_WARP_DESIGN_2026-07-26.md` 참조.

## 0. 한 줄 요약

이 워크스페이스는 **MIT** 다. warp(warpdotdev/warp) 저장소에서 **MIT 로 배포되는 두
크레이트만** 가져왔고, 그 크레이트들이 의존하던 **AGPL 크레이트 6개는 전부 자체 구현으로
갈아끼웠다**. AGPL 코드는 이 트리에 한 줄도 없다.

## 1. 가져온 것 (MIT, warp 저작권 유지)

| 경로 | 출처 | 라이선스 |
|---|---|---|
| `crates/warpui_core/` (87,821줄) | warpdotdev/warp `crates/warpui_core` | MIT (`LICENSE-MIT`) |
| `crates/warpui/` (33,493줄) | warpdotdev/warp `crates/warpui` | MIT (`LICENSE-MIT`) |
| `crates/warpui_core/assets/fonts/Roboto-{Regular,Bold,Italic}.ttf` | 같은 저장소 `app/assets/bundled/fonts/roboto/` | SIL Open Font License 1.1 (`assets/fonts/LICENSE.txt`) |

**스냅샷 기준점**: Perforce `p4 67415` = upstream commit `c16fd426` (2026-07-25).
저작권 표시는 `LICENSE-MIT` 에 원문 그대로 보존한다.

### 가져온 코드에 가한 수정

라이선스 경계와 무관한 **기능 추가**도 여기 적는다. 재임포트(§5-4)할 때 되살려야 하는
것이 Cargo.toml·경로만이라고 읽히면, 다시 가져오는 순간 조용히 사라진다.

| 위치 | 수정 | 이유 |
|---|---|---|
| `crates/warpui_core/src/elements/tui/event_handler.rs` | `on_any_key` 추가(p4 67653) | 터미널 클라는 **모든 키**를 자식에게 줘야 하는데, 정확 일치 바인딩만 있으면 `a` 같은 키가 미처리로 남는다. 그 집합은 바인딩으로 열거할 수 없다 |
| `crates/warpui_core/src/elements/tui/event_handler.rs` | `on_paste` 추가 | 붙여넣기는 키가 아니라 payload 하나다(bracketed paste). 키 콜백으로는 표현도 복원도 못 하고, 무시하면 사용자의 붙여넣기가 통째로 사라진다 |
| `crates/warpui_core/src/elements/tui/event_handler.rs` | `on_scroll`·`on_mouse` 추가 | 휠과 경계선 드래그는 **좌표로 해석하는** 제스처라 위젯 단위인 `TuiHoverable` 로는 안 된다 — 커서 아래 엘리먼트는 우연히 거기 있는 텍스트 줄이다 |
| `crates/warpui_core/src/elements/tui/event_handler.rs` | 키 바인딩을 **등록 시 `Keystroke::parse` 로 파싱**해 두고 수정키까지 비교(종전: 원시 문자열 == `keystroke.key`) | 같은 바인딩 표를 GUI 는 `Keystroke::parse` 로, TUI 는 원시 문자열로 읽고 있었다 — **수정키가 붙은 키에서 둘이 갈린다**(`shift-G` 가 TUI 엔 `G` 로 온다). 실제로 표의 `"G"` 때문에 GUI 가 첫 프레임에 패닉했고, GUI 가 P1 에 멈춰 있어 몇 달간 안 드러났다(2026-07-28). 덤으로 `ctrl-q` 가 `q` 바인딩을 발화시키던 것도 막힌다 — 터미널 클라에서 그건 자식에게 갈 제어문자를 UI 가 삼키는 것이다 |
| `crates/warpui_core/src/elements/gui/event_handler.rs` | `on_scroll_wheel` 콜백이 **커서 위치**를 받도록(종전: 델타 + 수정키만) | 휠은 "무엇 위에서 굴렸나"가 뜻의 일부인 제스처다 — 이 클라는 커서 아래 패널을 굴린다(분할 화면에서 활성 패널만 굴리면 옆 패널을 보며 휠을 돌리는 사람은 자기 눈앞이 아닌 곳이 흘러가는 것을 본다). `Event::ScrollWheel` 은 처음부터 위치를 싣고 있었고 **콜백만 그것을 버리고 있었다** |
| `crates/warpui_core/src/elements/gui/event_handler.rs` | `on_left_mouse_down`·`on_left_mouse_up`·`on_mouse_dragged` 의 **수정키 판 추가**(`*_with_modifiers`) | Shift+드래그는 이 클라에서 "패널 안 앱에게 넘김"이다(평드래그는 복사라 앱에게 줄 자리가 그것뿐). 이벤트는 처음부터 수정키를 싣고 있었고 콜백만 버렸다. **기존 3인자 빌더는 그대로 뒀다** — 시그니처를 바꿨더니 상류 자체 테스트 25곳이 깨졌고, 재임포트할 때마다 그 자리를 다시 고쳐야 한다 |
| `crates/warpui_core/src/elements/gui/event_handler.rs` | `on_typed_characters` 추가 | 한글·CJK 는 자판 한 번이 글자 하나가 아니다 — 조합 결과는 키가 아니라 문자열(`Event::TypedCharacters`)로 온다. 상류는 IME 이벤트를 이미 그 형태로 정규화하고 있었고 **엘리먼트가 받을 창구만 없었다**. 키 콜백만 있으면 조합 결과가 통째로 사라진다 |
| `crates/warpui_core/src/scene.rs` + `elements/gui/{text,shimmering_text}.rs` | Scene 에 그려진 글자의 **원문 기록** 추가(`Layer.texts` · `Scene::record_text`/`painted_texts`) — 글자를 그리는 엘리먼트가 페인트할 때 원문·bounds 를 같이 남긴다 | Scene 의 글리프는 glyph_id 라 글자로 못 되돌린다 — 그래서 "화면에 무엇이 보이나"를 헤드리스로 잴 길이 없었고, GUI 그리기 배선 누락(G8p 류)은 라이브 스크린샷만 잡았다. 이 기록이 그리기 오라클(클라 g9aa)을 가능하게 한다. 래스터라이저는 이 필드를 안 읽는다(부작용 0) |
| `crates/warpui_core/src/core/mod.rs` + `core/app.rs` | `AddWindowOptions::title_bar`(새 열거 `TitleBar{Hidden,Native}`) 추가 — `insert_window_internal` 이 박아 두던 `hide_title_bar: true` 를 그 값에서 읽는다 | 그 하드코딩 때문에 우리 창에 **최소화·최대화·닫기 버튼이 아예 없었다**(사용자 제보 2026-08-02 §10-20ⓐ). 자기 크롬을 다 그리는 앱(warp)의 기본값인데, `WindowOptions::hide_title_bar` 는 **모든 백엔드에 이미 있었고 밖에서 닿을 칸만 없었다**. 우리가 버튼을 그리는 길도 있었지만 **관습이 OS 마다 다르다**(맥은 왼쪽 신호등, Windows 는 오른쪽 셋) — 자리를 한 벌로 박으면 한쪽이 늘 어색하다. 기본값은 `Hidden` 이라 **기존 호출부는 무변경**이다(derive `Default` 가 그것을 고른다) |
| `crates/warpui_core/src/elements/gui/event_handler.rs` | `on_marked_text` 콜백 추가 — `Event::SetMarkedText`/`ClearMarkedText` 를 받는 자리 | 상류는 이 둘을 **이미 보내고 있었는데**(winit `Ime::Preedit`/`Commit` → `handle_ime_event`) `EventHandler` 에 받는 칸이 없어 소비자가 닿을 수 없었다 — `ModifierKeyChanged` 와 **같은 모양의 구멍**이다. 그래서 한글을 치는 동안 `ㅎ`→`하`→`한` 이 **화면 어디에도 없었다**(pytmux-15 ⑵). 확정만 오는 `on_typed_characters` 의 짝이고, 조합이 끝나면 **빈 문자열로 한 번 더** 부른다 — 두 콜백으로 나누면 한쪽만 배선해 조합 잔상이 남는 날이 온다. 기본값 `None` 이라 **기존 호출부는 무변경** |
| `crates/warpui_core/src/elements/gui/hoverable.rs` | `LeftMouseDragged` 에서도 `is_mouse_over_element` 를 갱신 | 종전에는 그 칸을 `MouseMoved` 에서만 고쳐, **버튼을 누른 순간부터 모든 `Hoverable` 의 hover 가 얼어붙었다.** 드롭 대상을 hover 로 알아내는 쪽(탭 드래그)은 그래서 대상을 영영 못 찾는다 — 강조도 재정렬도 안 났고, 뗌은 안 삼켜져 "드롭은 되는데 늘 빈 자리"로 보였다(2026-07-31 실측). hover **핸들러·타이머는 일부러 안 돌린다** — 저것은 "머무름"의 뜻이고 드래그 중 통과는 머무름이 아니다 |
| `crates/warpui/Cargo.toml` | `asset_cache` dev-의존 제거 | AGPL. 쓰던 곳은 아래 삭제한 예제 하나뿐 |
| `crates/warpui/Cargo.toml` | `virtual-fs` dev-의존 제거 | AGPL. Linux 전용 테스트 파일 하나에서만 사용 |
| `crates/warpui/src/windowing/winit/linux/cursor_theme_tests.rs` | `virtual-fs` harness 를 **표준 라이브러리 샌드박스로 재작성**(2026-08-01) | 위 줄이 의존만 지우고 **이 파일을 남겨 뒀다.** Linux 전용이라 macOS·Windows 에서는 컴파일조차 안 돼 몇 달간 안 드러났고, CI 가 Linux 를 처음 돌린 날 `E0432`+`E0282` 15건으로 터졌다. `Cargo.toml` 주석이 "Linux 를 대상 OS 로 고르면 tempfile 로 다시 쓴다"고 적어 둔 바로 그 시점이다(이제 `pytmux-gui-linux-x64` 를 CI 가 굽는다). **의존은 안 늘렸다** — 필요한 것이 디렉토리 몇 개와 파일 둘뿐이라 `std::fs` 로 충분하다. 대체한 harness 자체를 재는 오라클을 하나 더 붙였다(`sandbox_isolates_and_cleans_up`) — 샌드박스가 아무것도 안 만들면 `determine_cursor_theme()` 이 `None` 을 돌려 **일곱이 전부 초록**일 수 있다 |
| `crates/warpui/Cargo.toml`, `crates/warpui_core/Cargo.toml` | `settings_value` feature + 선택적 의존 제거 | AGPL. 쓰는 코드가 전부 `#[cfg(feature)]` 뒤라 기능이 없으면 컴파일되지 않음 |
| `crates/warpui_core/src/image_cache.rs` | 폰트 `include_bytes!` 경로를 `../assets/fonts/` 로 | 원래 `app/assets/` 를 가리켰는데 `app/` 은 AGPL 이라 삭제 |
| `crates/warpui/src/fonts/text_layout_tests.rs` | 폰트 읽기 경로를 `../warpui_core/assets/fonts/` 로 | 같은 이유. 이쪽은 **런타임 파일 읽기**라 `cargo check` 에 안 걸리고 테스트를 돌려야 드러났다(P1 에서 발견) |
| `crates/warpui/examples/image/` | 삭제 | `asset_cache::url_source`(원격 이미지 HTTP 캐시) 필요 |
| `crates/warpui/examples/formatted-text/` | 삭제 | `markdown_parser::parse_markdown`(실제 파서) 필요 |
| `crates/warpui/examples/animated_images/` + `examples/assets/rustyrain.gif`(17.3MB)·`numbers-750ms.gif` | 삭제 | 트리 통합(2026-08-01) 때 **저장소 크기**로 뺐다. 라이선스 문제가 아니라 무게다: 상류 데모 자산인데 출처·라이선스가 §1 표에 안 적혀 있고(폰트 OFL 만 있다) 공개 미러에 올릴 근거가 없었다. 게다가 **예제 전부가 `#[folder = "examples/assets"]` 로 assets 를 통째로 embed** 하므로 이 gif 하나가 모든 예제 이진을 17MB 씩 불렸다. 유일한 소비자가 `animated_images` 예제라 예제도 같이 지웠다 — 자산만 지우면 예제가 런타임에 조용히 깨진다 |

⚠ `examples/assets/numbers-1000ms.gif`(120KB)는 **남긴다** —
`crates/warpui_core/src/image_cache_tests.rs` 가 `include_bytes!` 로 물고 있어 지우면
컴파일이 깨진다(애니메이션 프레임 디코딩 테스트의 입력).

삭제한 예제들은 **P5(Claude 블록 뷰)에서 되살릴 후보**다(`animated_images` 는 제외 —
자산이 없다). 그때 `parse_markdown` 을 `pulldown-cmark` 로 구현하면 `formatted-text`
예제가 그대로 돌아온다. 원본은
`p4 print //woojinkim/scripts/pytmux-client/crates/warpui/examples/formatted-text/...@67415`
로 언제든 다시 읽을 수 있다 — ⚠ **2026-08-01 트리 통합(p4 move) 이전 리비전은 옛 depot
경로 `//woojinkim/scripts/pytmux-client/...` 에 있다.** 그 뒤는 `//woojinkim/scripts/pytmux/client/...`
다(`p4 filelog` 가 move 를 따라간다).

## 2. 자체 구현으로 갈아끼운 것 (AGPL 대체, 전부 MIT)

warp 의 동명 크레이트들은 **AGPL** 이라 쓸 수 없다. 아래는 `warpui`/`warpui_core` 가
**실제로 호출하는 API 만** 만족하도록 새로 쓴 것이다. 크레이트 이름과 공개 API 시그니처는
호출부를 고치지 않으려고 원본과 맞췄지만(이름·시그니처는 인터페이스이지 구현이 아니다),
**구현 본문은 호출부 요구사항에서 새로 작성했다.**

| 크레이트 | 원본 줄수 | 우리 줄수 | 실제 필요했던 API |
|---|---:|---:|---|
| `warp_errors` | 606 | 115 | `report_error!` 매크로 (평문형 29곳 + `extra: {..}` 형 17곳). 원본의 Sentry 연동·오류 등록·보고빈도 제어는 **의도적으로 안 만들었다** — pytmux 는 텔레메트리를 쓰지 않는다 |
| `string-offset` | 266 | 199 | `ByteOffset`·`CharOffset`·`CharCounter` |
| `warp_util` | 4,138 | 129 | `path::ShellFamily` 하나 |
| `command` | 1,763 | 12 | `blocking::Command` — 필요한 게 표준 라이브러리 그대로라 재노출 |
| `markdown_parser` | 6,807 | 426 | 서식 텍스트 **표현 타입만**. 호출부가 파서를 한 번도 부르지 않는다 |
| `sum_tree` | 1,997 | 838 | `SumTree` + `Cursor` 전체 (§3 참조) |
| **합계** | **15,577** | **1,719** | |

## 2-a. 우리가 새로 쓴 것 (P1, 가져온 코드 아님)

| 크레이트 | 역할 |
|---|---|
| `base` | 상태·액션·키맵. **UI 의존이 하나도 없다** — 이게 이 크레이트의 계약이다 |
| `claude` | Claude Code 트랜스크립트(JSONL) → 블록 뷰 모델(§10-11 P5). 우리가 새로 쓴 코드이며 의존은 `serde_json` 뿐이다 |
| `clip` | OS 클립보드. 파이썬 클라(`clientclip.py`)와 **같은 외부 도구를 같은 순서로** 부른다. **의존이 0개인 것이 요점**이다 — `arboard` 계열은 Linux 에서 X11/Wayland 를 끌어와 "아무 터미널에서나"(= 디스플레이 없는 ssh)와 충돌한다 |
| `proto` | pytmux 서버 프로토콜(프레이밍·메시지·행 합성). 역시 UI 무의존 |
| `gui` | GUI 이진 `pytmux-gui`. `warpui` + `elements::gui` |
| ~~`tui`~~ | **2026-08-01 삭제.** TUI 이진 `pytmux-client-tui` 는 퇴역했다(사용자 결정 — 클라는 정본 Textual TUI 와 이 GUI 둘). 상류 `warpui_core` 의 `tui` 엘리먼트·feature 는 스냅샷이라 그대로 두되, 우리 크레이트 중 그것을 쓰는 것은 이제 없다 |

두 뷰는 엘리먼트 타입만 다르고 **상태·키·전이는 core 하나를 공유**한다. 이 규칙은 문서가
아니라 `scripts/check_layering.sh` 가 강제한다(core·proto 에 UI 의존이 생기거나, 뷰가 키
이름을 직접 적으면 실패). 게이트가 실제로 잡는지는 위반을 심어 확인했다.

`proto` 의 화면 합성은 **파이썬 클라이언트와 글자 하나까지 같아야** 한다.
그 확인에 새 오라클을 만들지 않고 pytmux 가 이미 쓰는 골든을 재사용한다 —
`tests/conformance.rs` 와 `scripts/gen_wire_fixture.py` 의 설명 참조. 이 오라클이 고정하는
것은 **텍스트 배치**이고, 색·속성은 다루지 않는다(코퍼스에 SGR 이 없다). 스타일 적합성은
실제로 칠하기 시작하는 P3 에서 별도 코퍼스가 필요하다.

## 3. `sum_tree` — 유일하게 성능 특성이 다른 대체품

원본은 **증강 B-트리**(arrayvec 노드, TREE_BASE 6)라 `seek`/`slice` 가 O(log n) 이다.
우리 것은 **같은 API·같은 의미론을 평평한 `Vec` 위에** 올린 것으로 O(n) 이다.

- **왜**: 이걸 쓰는 곳은 GUI 의 `viewported_list`·`table` 둘뿐이고, TUI 경로는 아예 쓰지
  않는다. P0/P1 에 필요한 것은 컴파일과 의미론적 정확성이지 대규모 리스트 성능이 아니다.
- **언제 갈아치우나**: 블록 리스트가 실제로 가상화를 요구하는 **P3**. 항목 수가 수천을
  넘고 매 프레임 무작위 위치로 `seek` 하는 패턴이 생기면 그때다. 한 방향 순회 패턴에서는
  전체가 O(n) 이라 지금 구조로도 문제없다.
- **정확성 근거**: `crates/sum_tree/src/lib_tests.rs` 15개(특히 `SeekBias` 경계 판정 —
  어긋나면 컴파일은 통과하고 리스트만 한 칸씩 밀린다) + `warpui_core` 자체 테스트 중
  `elements::gui::{table,viewported_list}` 계열 33개가 실제 소비자 쪽에서 통과한다.

## 4. 규칙 (이걸 어기면 라이선스가 깨진다)

1. **AGPL 코드를 이 트리로 복사하지 말 것.** 스냅샷(`@67415`)의 `app/`, `crates/warp_tui/`,
   `crates/warp_terminal/` 등은 **읽고 설계를 배우는 대상**이지 가져오는 대상이 아니다.
2. 새로 만드는 심은 **파일 상단에 출처와 대체 사실을 적는다**(기존 6개가 본보기).
3. 의존을 추가할 때 **path 의존이 늘면 이 문서를 갱신**한다. 검사는
   `scripts/check_licenses.sh` — 허용 목록 밖의 로컬 크레이트가 의존 그래프에 나타나면
   실패한다.
4. upstream 을 다시 가져올 일이 생기면 **두 크레이트만** 가져오고, §1 의 수정 목록을
   다시 적용한다.

## 5. 참고 자료 되찾기

AGPL 스냅샷은 지우지 않았다 — Perforce 이력에 그대로 있다.

```sh
# 파일 하나 읽기
p4 print //woojinkim/scripts/pytmux-client/crates/warp_tui/src/terminal_block.rs@67415

# 그 시점의 파일 목록 훑기
p4 files //woojinkim/scripts/pytmux-client/crates/warp_tui/...@67415
```

무엇을 어디서 볼지는 설계문서 §10 색인에 정리돼 있다.

## 6. 빌드 전제

- Rust **1.92.0** (`rust-toolchain.toml` 이 고정 — rustup 이 자동으로 맞춘다).
- macOS 에서 `warpui` 를 빌드하려면 **전체 Xcode 가 필요하다**. `build.rs` 가
  `xcrun -sdk macosx metal` 로 Metal 셰이더를 컴파일하는데, Command Line Tools 에는
  `metal` 유틸리티가 없다. `warpui_core`(엘리먼트·런타임·TUI 백엔드 전부)는 CLT 만으로
  빌드·테스트된다.
