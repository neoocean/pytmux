# CLAUDE.md — `client/`(Rust 클라) 에이전트 온보딩

> **먼저 루트 [`../CLAUDE.md`](../CLAUDE.md) 를 읽는다.** 저장소 전체 안내와 ⛔ 안전
> 규율("프로세스 이름으로 일괄 kill 금지")은 **거기 한 벌**만 있다 — 복제된 안전 규율은
> 한쪽만 갱신되면 갈라지고, 갈라진 안전 규율은 안전 규율이 아니다(트리 통합 계획 §2-5).
> 이 파일은 그 위에 얹는 **Rust 클라 전용**(빌드·게이트·하네스) 안내다.
>
> 설계·진행 기록은 같은 저장소의 `../docs/internal/`(p4 전용)에 있다:
> `PYTMUX_CLIENT_WARP_DESIGN_2026-07-26.md`(설계·결정) · `WINDOWS_BOX_TASKS_SCENARIO.md`
> (Windows 브링업) · `HANDOFF.md` 머리말(진행 상황).
>
> ★ **축이 다 닫혔다**(2026-08-01): **피처 패리티 187/0/0** · **GUI 네이티브 크롬
> N0~N8** · **정본 기준 레이아웃 맞추기**(조정 후보 열둘 — CL 68662~68800) ·
> **판 안 마우스** · **2차 대조의 후보 넷**(③ CL 68921 · ①+② CL 68927 · ④ CL 68928).
> 기준 문서는 **`../docs/internal/client/reports/2026-08-01-ui-parity-side-by-side.md`**(3차 대조 · 20장면)
> 이고, 그 문서의 조정 후보 여섯 중 **다섯이 닫혔다**(ⓐ 68932 · ⓒ 68933 · ⓕ 68934 ·
> ⓓ 68937 · ⓑ 68938). 스플리터도 실제 선이 됐다(68936).
>
> **남은 하나는 ⓔ 「작성창이 패널 프롬프트 줄에」**다 — 정본은 작성창을 활성 패널의
> 프롬프트 줄에 얹는데 우리는 창 바닥에 한 칸을 차지한다. 캔버스 **위에 겹쳐** 그려야
> 해서 두 뷰의 렌더 구조를 건드린다(「판 안 마우스」와 같은 규모). 그 다음은 **4차
> 대조**를 굽는 것이다 — 위 다섯이 화면을 또 바꿨다.
>
> 걸려 있는 것 하나: **탭 드래그 hover** — 실측에서 드롭 대상 강조가 안 떴다. 우리 배선인지
> 합성 마우스가 버튼-다운 중 hover 를 안 깨우는지 아직 못 갈랐다
> (`2026-07-31-live-windows-remaining-three.md` 에 다음 단계). **TUI 공백 손실은 닫혔다**
> — 제품이 아니라 테스트 화면 모델의 결함이었다.
>
> GUI 는 패널 테두리를 **선문자가 아니라 실제 선**으로 그린다(N8 · CL 68805) — 서버
> 호환은 그대로이고 TUI 는 종전대로 문자다.

## 무엇인가
pytmux 서버(파이썬 데몬)에 **같은 소켓 프로토콜**로 붙는 네이티브 GUI 클라이언트(Rust).

> ⚠ **2026-08-01: Rust TUI 를 지웠다**(사용자 결정 · 근거
> `../docs/internal/CLIENT_PRODUCT_SET_2026-08-01.md`). 이 워크스페이스의 산출물은
> **`pytmux-gui` 하나**다. 아래 문서에 "두 뷰"라고 적힌 자리가 남아 있으면 그건 낡은
> 문장이다 — 계층 게이트가 지키는 것은 이제 "뷰가 둘"이 아니라 **"상태·키맵·명령이
> 뷰와 갈라져 있다"**(정본과의 대조가 그 계층을 통해 이뤄진다).
서버는 그대로 두고 클라만 갈아 끼우는 구조라, 파이썬 Textual 클라와 같은 서버에 동시에
붙을 수 있다. 산출물은 **하나** — GUI `pytmux-gui`.
라이선스는 **MIT**(출처 warp 저장소는 두 크레이트를 빼면 AGPL — 경계는 `PROVENANCE.md`).

## ⛔ 가장 먼저: 안전 규율은 루트에 한 벌

**프로세스 이름으로 일괄 kill 금지** — 사고 2026-07-28(같은 날 3회, 이 클라 세션이
사용자의 라이브 pytmux 를 통째로 죽였다)와 그 뒤 굳은 안전한 레시피·검증 규율은
**[루트 `../CLAUDE.md`](../CLAUDE.md) 「빌드/실행/테스트」의 ⛔ 항목**이 정본이다.
여기에 복제하지 않는다 — 복제된 안전 규율은 한쪽만 갱신되면 갈라지고, 갈라진 안전
규율은 안전 규율이 아니다(트리 통합 계획 §2-5).

**한 줄 요약**: `PYTMUX_HOME=<스크래치>` 로 격리해 띄우고, `pytmux.py kill-server --yes`
로 내리고, **검증도 그 홈의 상태 파일로** 한다(전역 프로세스 목록으로 확인하면 사용자의
라이브 데몬이 보여 "정리 실패"로 오판한다). 아래 레시피의 cwd 는 `client/` 다.

### 안전한 레시피(실측 2026-07-28)

```powershell
$env:PYTMUX_HOME = "$sp\pytmuxhome"          # 상태·소켓·캡처가 전부 이 아래로 격리된다
# ⛔ 그런데 **설정 파일은 안 딸려 온다** — 아래 「설정 파일은 따로 격리한다」 참조.
New-Item -ItemType File -Force "$env:PYTMUX_HOME\config" | Out-Null
$py = "..\pytmux.py"                          # 저장소 루트(이 디렉토리의 한 단계 위)

python $py start-server                       # → "서버 기동됨: tcp:127.0.0.1:0"
python $py ls                                 # → "1 tabs, 1 panes" (이 홈 전용 서버)
.\target\release\pytmux-gui.exe              # 같은 PYTMUX_HOME 이라 스크래치 서버에 붙는다

python $py kill-server --yes                  # → "서버 종료됨" (서버 + 그 pty-host)
Test-Path "$env:PYTMUX_HOME\state\default.port"          # False
Test-Path "$env:PYTMUX_HOME\state\default.ptyhost.pid"   # False
```

- **`PYTMUX_HOME` 없이 클라 이진을 띄우면 사용자의 라이브 서버에 붙는다**: `endpoint.rs`
  가 `PYTMUX_HOME/state` → 없으면 `%LOCALAPPDATA%\pytmux`(=라이브) 순으로 소켓을 자동
  발견한다. 실험용으로 띄울 땐 **항상** `PYTMUX_HOME` 을 먼저 세운다.
- ⛔ ★ **설정 파일은 따로 격리한다 — `PYTMUX_HOME` 만으로는 안 된다**(사고 2026-08-04).
  탐색 차례가 `$PYTMUX_CONFIG` → **`$PYTMUX_HOME/config`** → `~/.config/pytmux/config`
  → `~/.pytmux.conf` 인데(`base/src/config.rs` 머리말), 스크래치 홈에 `config` 파일이
  **없으면 그 자리를 건너뛰어 사용자의 진짜 설정 파일을 읽는다**. 읽기만이면 그나마
  낫지만 **쓰기도 같은 파일로 간다** — 설정 판을 마우스로 눌러 값을 바꾸는 순간
  (§10-21ⓣ 실측) 사용자의 `~/.config/pytmux/config` 에 `set status-position top` 이
  박혔다.
  - 증상이 **엉뚱한 데서 터진다**: 그 다음 `cargo test -p gui` 에서 배지 자리 오라클
    둘이 떨어졌다(`monitor_badges_sit_in_the_bottom_status_bar_not_the_tab_bar` 등).
    GUI 테스트는 `Config::load()` 로 **이 상자의 진짜 설정 파일**을 읽으므로, 상태줄이
    위로 간 설정이 테스트 프레임의 그리기 차례를 바꿔 버린 것이다. 제품도 테스트도
    멀쩡한데 **환경이 실패를 만든** 부류라, 원인을 코드에서 찾으면 한참 헤맨다.
  - 처방: 위 레시피처럼 `$PYTMUX_HOME\config` 를 **빈 파일로 먼저 만들거나**
    `$env:PYTMUX_CONFIG` 를 스크래치 파일로 세운다. 빈 파일이면 기본값으로 뜬다.
  - 이미 밟았으면 **되돌린다**: 라이브 확인 전에 `~/.config/pytmux/config` 를 복사해
    두고 끝나면 되돌릴 것(무엇을 눌렀는지 기억으로 복원하려 들지 말 것 — 값이 기본값과
    같으면 파일에 줄이 아예 없어서, 지운 줄인지 원래 없던 줄인지 사후에 못 가른다).
- GUI 이진 자체를 치우는 건 이름으로 해도 안전하다(`pytmux-gui`
  는 우리 것만 있다). 위험한 것은 **`pythonw`·`python`** 이다.

## 사용자 문서

사용법(키·마우스·화면·설정 — 실물 GUI 스크린샷 포함)은 **`../docs/internal/client/USER_GUIDE.md`** 다
(§10-17 로 이사 — 스크린샷이 실 캡처라 미러 제외).
스크린샷은 합성이 아니라 `--frame-dump`/`--frame-keys` 로 실제 앱에서 뜬 것이고,
갱신도 같은 방법으로 한다(연출 레시피는 그 CL 디스크립션 참조).

## 빌드/실행

```sh
cargo build -p gui                      # → target/debug/pytmux-gui(.exe)
cargo build --release -p gui
cargo test                              # 워크스페이스 전체
```

- Windows 에서는 `$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"` 가 필요할 수 있다.
- ⚠ **테스트 이진이 `Access is denied (os error 5)` 로 안 돌면 `CARGO_INCREMENTAL=0`**
  (실측 2026-07-31): 이 상자의 보안 에이전트가 **증분 빌드로 나온 특정 산출물 하나**를
  실행·읽기 차단했다(그 파일만 — 같은 시각의 다른 테스트 이진은 멀쩡했다). 파일을 지우고
  다시 링크해도, 이름을 바꿔도, 다른 target 디렉토리로 옮겨도 그대로였고 `cargo clean` 도
  못 풀었다. `CARGO_INCREMENTAL=0` 이 코드젠을 바꿔 다른 산출물이 나오면 바로 돈다.
  **앱이나 테스트를 의심하기 전에 이걸 먼저 해 볼 것** — 20분을 썼다.
- 배포용 이진 스냅샷 자리는 `build/` 다. **지금은 GUI 선빌드가 둘**이다 —
  `pytmux-gui-macos-arm64`(2026-08-01) 과 `pytmux-gui-windows-x64.exe`(2026-08-01).
  ⚠ **크로스 컴파일은 안 된다**(창·GPU 백엔드를 실제로 링크한다) — 각 이진은 그 OS 의
  상자에서 굽는다(사유·갱신 레시피 = `build/README.md`).

## 게이트(커밋 전)

> **한 번에 다 돌리려면 저장소 루트의 `python3 scripts/check_all.py`**(합본 게이트, M2).
> 아래 게이트 + 파이썬 스위트 + 미러 점검을 순서대로 돌고 요약 한 줄을 낸다. 빠른
> 되먹임만 원하면 `--fast`(크로스컴파일·전체 스위트·미러 제외), 무엇을 도는지만 보려면
> `--list`. ⚠ 파이썬 스위트는 **rc 가 아니라 요약줄**로 판정한다(루트 CLAUDE.md 경고).

- `scripts/check_fixtures.py` — **픽스처가 정본보다 낡지 않았나.** 생성기 열여섯을 전부
  돌려 작업본과 대조한다(작업본은 반드시 되돌린다 — `--write` 는 갱신 모드). 이 게이트가
  없던 동안 다섯 개가 조용히 벌어졌다(트리 통합 계획 §4.8 — 그중 `select-window wid` 는
  정본이 고친 **레이스 결함**이 우리에게만 남아 있었다는 뜻이다). 실패하면 `--write` 로
  갱신하고, **새로 우는 적합성 테스트가 가리키는 표면을 두 뷰에** 이관한다.
  정본 쪽 스위트도 같은 게이트를 소비한다(`tests/test_surface_ledger.py`) — 정본을
  건드리면 저쪽이 먼저 운다.
- `scripts/check_layering.sh` — "뷰만 두 벌이고 상태·키맵·명령은 한 벌"을 기계로 강제
  (`base` 에 UI 의존 금지). GUI/TUI 가 조용히 갈라지는 것을 막는다.
- `scripts/check_licenses.sh` — MIT 경계. 의존 그래프의 **로컬(path) 크레이트**가 허용
  목록과 정확히 일치해야 한다. 새 크레이트를 넣었으면 ALLOWED + `PROVENANCE.md` §2 표를
  같이 갱신한다. **결과가 비면 통과가 아니라 고장**이다(경로 표기 차이로 한 줄도 못 잡고
  rc 0 이던 회귀가 있었다 — 빈 결과는 실패로 떨어뜨리는 가드가 들어가 있다).
- `scripts/check_windows.sh` — 크로스 컴파일로 두 번째 OS 가 조용히 썩는 것을 막는다
  (링크는 안 하지만 cfg 분기·타입은 전부 검사).
- `crates/proto/tests/parity.rs` — **패리티 래칫.** 표면 하나를 덮으면 그
  줄과 `static SCORE` 를 **같은 CL 에서** 옮긴다(안 옮기면 테스트가 떨어진다).
  현재 점수를 보려면 `cargo test -p proto --test parity -- print_the_score
  --nocapture`. ⚠ 이름이 표들 사이에 겹치니(`help` 등) 고칠 때 `static SETTINGS` /
  `static SCREENS` 로 파일을 잘라 **범위를 좁히고** 바꿀 것.
  - ★ **칸이 둘이다**(2026-08-01, L2 — 계획 §6.1): 표면마다 `tui`/`gui` 를 따로 적고
    `SCORE` 도 뷰마다 한 쌍이다. 한 칸만 두면 **TUI 와 GUI 가 갈리는 것을 아무도 안
    잰다** — 실제로 키 바인딩 문법이 갈려 GUI 만 첫 프레임에 패닉했고 몇 달간 안
    드러났다. 둘이 같으면 `i(name, cover, note)` 그대로 쓰고, **갈렸으면
    `iv(name, tui, gui, note)` 로 이유와 함께 선언**하고 `KNOWN_DIVERGENCES` 를 같은
    CL 에서 옮긴다(지금 0). 선언 없는 갈림은 `the_two_views_do_not_drift_silently` 가
    잡는다 = "한쪽 배선이 빠졌다".
- `crates/proto/tests/command_conformance.rs` — 우리가 보내는 명령이 서버 표에
  실재하는지. `full` 재동기 예외 목록은 **정확·정렬**이라야 한다.

## 슬라이스 한 벌의 모양 (패리티 트랙에서 굳은 것)

파이썬 정본에 대고 재기 → 가능하면 **정본을 직접 호출하는 픽스처 생성기**(`scripts/gen_*.py`)
→ core/proto 에 구현하고 **뷰는 얇게** → 단위 + 큐 오라클 → **변이를 심어 오라클이 죽는지**
→ 게이트 → `PYTMUX_HOME` 격리 서버에 **라이브** → `../docs/internal/client/reports/` 에 리포트 한 장 → CL 하나.

지켜서 값을 본 규칙 셋:

- **부정 단언만 있는 오라클은 아무 일도 안 해도 통과한다.** 배선이 통째로 빠진 것을 두 번
  놓쳤다 — "무엇이 실제로 생기는가"를 재는 **양성 오라클**을 세운다.
- **목록에서 무언가를 고르는 테스트는 줄 번호가 아니라 키로 찾는다**(표를 재정렬하면
  자리를 박아 둔 오라클이 낡는다 — 세 번 밟았다).
- **"없다·막혔다"고 적기 전에 우리가 이미 받는 것·이미 쓰는 크레이트를 본다**(세 번
  잘못 적었고 한 번은 필요 없는 서버 변경으로 이어질 뻔했다).
- **GUI 에는 아직 큐 오라클 하네스가 없다**(TUI 의 `outgoing_after_*` 에 해당하는 것).
  그래서 GUI 쪽 배선 누락은 **라이브 스크린샷만이 잡는다** — GUI 를 건드렸으면 찍을 것.

## 아키텍처 한눈에

- `crates/base` — 상태·키맵·명령·프로토콜 소비. **UI 의존 없음**(계층 게이트).
- `crates/proto` — pytmux 소켓 프로토콜(서버와 동형).
- `crates/gui` — 뷰. 키 바인딩 문법의 주인은 warpui `Keystroke`(`"shift-G"`) 하나다.
  (2026-08-01 에 `crates/tui` 를 지웠다 — 제품은 정본 Textual TUI 와 이 GUI 둘이다.)
- `crates/claude` — Claude 트랜스크립트/블록 뷰.
- `crates/warpui`·`warpui_core` — 상류 스냅샷(MIT 경계 안). 나머지 `warp_*`·`command`·
  `markdown_parser`·`sum_tree`·`string-offset` 은 AGPL 원본을 대체한 자체 구현.

## 라이브 확인 하네스 (`scripts/*.ps1`)

창 안의 그림을 사람 눈 없이 확인한다. 전부 **pid 를 받는다**.

⛔ **창 찾기는 `winlib.ps1` 한 벌이다 — 직접 훑지 말 것**(pytmux-32, 2026-08-03).
`pytmux-gui` 는 최상위 창을 여러 개 갖고, 그중 winit 의 숨은 `Winit Thread Event Target`
은 **15×15 인데 보이고 소유자도 없다**. 그래서 "그 pid 의 첫 보이는 최상위 창"이라는
술어를 **그대로 만족한다** — EnumWindows 는 Z 순서로 도니 그 창이 위로 올라오는 순간
(앱 창을 최소화하면 바로 그렇다) 하네스가 **그것을 앱 창으로 집는다**. 증상 셋이 한꺼번에
온다: 키가 성공을 찍고도 안 들어간다 · 전경이 바탕화면으로 간다 · rect 가 15×15 다.
**그 셋이 "최소화했다 복원하면 창이 15×15 로 남고 키를 하나도 안 받는다"로 제품 결함처럼
기록됐다** — 제품은 멀쩡했다(`ShowWindow(SW_RESTORE)` 도 사람이 누르는
`WM_SYSCOMMAND/SC_RESTORE` 도 정상 복원을 실측). 여덟 중 **일곱**이 그 술어를 복붙하고
있었고, `capture_window.ps1` 만 64px 필터가 있어 오집 대신 "창을 못 찾았다"로 떨어졌다 —
그 문구가 오진의 출발점이었다.
- 이제 전부 `. "$PSScriptRoot\winlib.ps1"` 후 **`Get-AppWindow -ProcessId <pid>`** 를 쓴다.
  판정은 크기가 아니라 **상태**로 한다: `IsIconic` 이면 크기 필터를 건너뛰고(최소화된
  우리 창은 rect 가 158×26 이다), 아니면 64px 하한으로 숨은 창을 버린다. 클래스 이름으로
  거르지 않는다(winit 판이 바뀌면 이름도 바뀐다).
- 못 찾으면 **본 창을 전부 적어** 던진다. "창이 없다" 한 줄은 다음 세션이 *앱이 깨졌다*로
  읽는다 — 실제로 그렇게 읽혔다. 후보만 보고 싶으면 `Show-AppWindowCandidates -ProcessId`.
- 복붙이 돌아오는 것은 `tests/test_harness_window_lookup.py` 가 막는다(파일만 읽어 판정하니
  OS 를 안 탄다).

| 스크립트 | 하는 일 |
|---|---|
| `winlib.ps1` | **pid → 앱 창**(`Get-AppWindow`). 나머지 여덟이 dot-source 한다 |
| `capture_window.ps1` | pid → PNG. 화면 DC BitBlt(`PrintWindow` 는 wgpu 창에 **까만 사각형을 성공으로** 돌려준다) |
| `send_keys_to_window.ps1` · `send_chord_to_window.ps1` | SendInput 으로 글자·조합키 |
| `wheel_on_window.ps1` · `drag_mouse_on_window.ps1` | 휠·드래그(`-ClientPixels`) |
| `hover_on_window.ps1` | **누르지 않는 hover** + `-Click` · 그 자리의 **커서 모양**을 이름으로(캡처에는 커서가 안 찍힌다) |
| `launch_console_window.ps1` | 콘솔 명령을 **새 창**에 띄우고 그 창 HWND 를 확정해 돌려준다(정본 클라용) |
| `gen_canon_shots.ps1` · `compose_side_by_side.ps1` | **파이썬 정본을 실제로 띄워** 장면별로 찍고, 우리 컷과 나란히 굽는다(대조 문서) |
| `type_korean_to_window.ps1` · `resize_window.ps1` | IME · 창 크기 |

- ⚠ **휠 아래로 굴리기는 2026-08-03 까지 아예 안 됐다**: `mouse_event` 의 `mouseData` 는
  DWORD 지만 담는 값은 **부호 있는** 델타라 `[uint32](-120)` 이 변환이 아니라 오류로 죽는다
  (`wheel_on_window.ps1` 의 `.EXAMPLE` 이 바로 `-Notches -3` 인데 그 예제가 죽었다). 이제
  2의 보수 바이트를 재해석해 넘긴다 — 위/아래 둘 다 실측했다.

- ⛔ **IME 조합 모드는 `IMC_SETCONVERSIONMODE` 로 켜지 말 것**(2026-08-03 실측, 리포트
  `docs/internal/client/reports/2026-08-03-ime-harness.md`). 그 쓰기는 성공을 돌려주고
  **읽기값까지 바꾸지만 실제 입력기는 안 바뀐다** — 그림자만 켜진다. 그러면 다음 실행이
  "이미 켜져 있다"고 읽고 토글을 건너뛰어 자판이 **로마자 그대로** 들어간다(`gksrmf`).
  세 세션이 "하네스가 IME 를 못 몬다"고 적은 것이 전부 이 자국이다. 진짜 토글은
  **`VK_HANGUL` 뿐**이고, 지금 스크립트가 그렇게 한다(물어보고 켜는 방향으로만 + 확인 +
  끝나면 되돌림). ⚠ `crates/gui/src/ime.rs` 의 배지도 **같은 값을 읽는다**.

- ⚠ **좌표계 — 배율만 나누면 빗나간다. 원점도 옮겨야 한다**(2026-08-04 정정):
  캡처 그림의 원점은 **DWM 확장 프레임의 좌상단**이고 앱이 보는 원점은 **클라이언트
  영역의 좌상단**(=타이틀바 아래)이다. 둘이 겹치지 않으므로 **배율을 나누기 전에 그
  차이를 빼야 한다**:

  ```
  clientX = (ix - dx) / 배율        # 이 상자 실측: dx ≈ 2,  dy ≈ 45.5,  배율 1.5
  clientY = (iy - dy) / 배율        # → -ClientPixels 로 준다
  ```

  `dx`·`dy` 는 창 장식이라 판·테마가 바뀌면 달라진다. **박아 쓰지 말고 그때 재라** —
  `DwmGetWindowAttribute(…, DWMWA_EXTENDED_FRAME_BOUNDS)`(그림 원점, 물리 픽셀)와
  `ClientToScreen(hwnd, {0,0})`(클라 원점)의 차다. ⚠ 에이전트 셸의 PowerShell 은 DPI
  인식이 아니라 `GetWindowRect`·`ClientToScreen` 이 **논리 픽셀**을 주는데 DWM 쪽은
  **물리 픽셀**을 준다 — 빼기 전에 한쪽에 배율을 곱할 것.

  ⛔ 종전에 이 자리는 *"그림 좌표 ÷ 1.5 를 `-ClientPixels` 로"* 라고만 적혀 있었고
  **그건 틀렸다**. 그대로 하면 세로로 30칸쯤 어긋나 탭·×·경계·패널 안 클릭존을 **전부
  빗나가고**, 증상은 "hover 도 클릭도 죽었다"로 보인다 — 2026-07-30 에 "크롬 마우스가
  통째로 죽었다"고 결론 낼 뻔했고, 2026-08-04(pytmux-2·23)에 **이 문장을 믿고 두 번 더
  밟았다**. 두 번째는 새 클릭존이 안 먹는 것으로 읽혀 제품을 의심했다.
  앱을 의심하기 전에 **먼저 좌표를 재고**, 그래도 의심되면 임시 `log::warn!` 로 앱이
  받은 좌표를 찍어 볼 것. 값싼 교정: **`+` 새 탭 단추를 눌러 탭이 느는지** 본다(맞으면
  매핑이 맞은 것이고, 안 늘면 아직 어긋난 것이다).

- ⚠ **이 상자에서 `--frame-dump` 는 안 된다**(2026-07-31 실측): wgpu 가 스왑체인 텍스처에
  `COPY_SRC` 가 없다며 검증 오류로 죽는다(`Frame capture encoder`). 그건 맥 쪽 처방이고
  (아래 macOS 절), Windows 는 `capture_window.ps1`(화면 DC BitBlt)이 답이다.
- **둘 다 대상 창이 전경일 때만 동작한다**(남의 창에 키를 넣거나 남의 화면을 찍지 않으려는
  가드다). 먼저 `SetForegroundWindow` 하고, 중간에 다른 창이 전경을 뺏으면 **거기서
  실패한다** — 단계마다 다시 세우는 편이 안전하다.
- 이 상자는 **150% 배율**이라 스크린샷 픽셀이 논리 좌표의 1.5배다. ⚠ 그런데 배율만으로는
  자리가 안 맞는다 — **원점도 다르다**(위 「좌표계」 참조. 이 줄만 읽고 1.5 로 나누면
  빗나간다).
- **콘솔 앱(파이썬 정본 클라)은 pid 로 창을 못 찾는다** — 이 상자의 콘솔 창은 Windows
  Terminal 이 소유한다. 제목으로 찾는 것도 위험하다(사용자가 쓰는 창을 집는다).
  `launch_console_window.ps1` 이 띄우기 전후의 창 목록을 비교해 HWND 를 확정하고,
  캡처·키 스크립트는 `-Hwnd` 로 그것을 받는다.
- **이 상자에는 `NO_COLOR=1` 이 걸려 있다.** Textual(정본 클라)은 그걸 보면 색을 통째로
  버리고 헤드리스 경로에서는 **모노크롬 필터에서 죽는다** — 그런데 SVG 는 만들어진 채로
  죽어 "찍혔다"로 오독된다. 정본을 찍는 도구는 자기 프로세스에서 이 변수를 지운다.
- `PYTMUX_HOME` 을 세우고 띄울 것 — 안 세우면 **사용자의 라이브 서버에 붙는다**(위 ⛔).

### macOS 에서 GUI 그림 확인 (⚠ Background 세션 함정)

에이전트 셸은 **Background launchd 세션**이라, 여기서 띄운 GUI 창은 사용자의 Aqua 화면에
컴포지트되지 않고 `screencapture` 도 그 별세계를 찍는다 — **창이 멀쩡히 그려져도 "안
그려진다"로 오판하게 된다**(2026-07-30 G9j 리포트가 정확히 그 오진을 정정한 기록이다).
그래서 맥의 그림 확인은 화면이 아니라 **드로어블에서 뜬다**:

```sh
PYTMUX_HOME=$스크래치홈 target/debug/pytmux-gui --frame-dump=/tmp/out.png   # rc 0 + PNG
```

TUI 는 pty 로 몬다(**키는 낱개 write** — ESC 와 다음 글자가 한 write 면 crossterm 이
Alt+글자로 읽는다). `scripts/capture_window_mac.sh`·`send_keys_mac.sh` 도 있지만 앞의 것은
같은 세션 함정에, 뒤의 것은 손쉬운 사용 권한에 걸린다 — frame-dump 가 먼저다.

## 게시(이 저장소 관례)

- **더 이상 별도 CL 이 아니다**(트리 통합 2026-08-01). depot
  `//woojinkim/scripts/pytmux/client/...` — 서버·정본 클라와 **한 트리·한 CL** 이다.
  프로토콜을 건드리면 서버와 세 소비자가 **같은 CL 안에** 들어간다(종전에는 반쪽 CL 이
  정상이었다 — 하나를 되돌리면 반쪽만 되돌아갔다). 게시 관례 정본은 루트
  [`../CLAUDE.md`](../CLAUDE.md) 「게시」다: **Perforce submit + git push 양쪽**,
  게이트 `python3 ../scripts/publish_check.py`(이제 `client/` 도 그 ROOT 안이다).
- 공유 워크스페이스(병렬 세션)라 제출 직전 **내 파일만인지** 확인한다 — 0 이어야 제출:
  ```sh
  p4 opened -c <CL> | grep -vc "/pytmux/"
  ```
- `p4 change -o | p4 change -i` 는 default CL 에 열린 **남의 파일까지** 새 CL 로 끌어간다.
  스펙을 직접 쓰고 `p4 edit -c <CL> <내 파일>` 로 담는 편이 안전하다.
