# pytmux 핸드오프 문서

> 작성: 2026-06-03 · 대상: 이 프로젝트를 이어받는 사람/에이전트
> 관련: [DESIGN.md](DESIGN.md) · [FEATURES.md](FEATURES.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [MEMORY.md](MEMORY.md)

## 1. 한눈에 보기

- **무엇**: Python + [Textual](https://textual.textualize.io/) 로 만든 tmux 유사 터미널
  멀티플렉서. 마우스 1급 지원 + TUI 메뉴/탭 인터페이스가 차별점.
- **어디**: Perforce `//woojinkim/scripts/pytmux/...`, 로컬
  `/Users/neoocean/p4/playground/scripts/pytmux`. GitHub 미러
  `https://github.com/neoocean/pytmux` (origin, main).
- **진입점**: `python3 pytmux.py` (서버 없으면 자동 기동 후 attach). 어디서든
  `pytmux` 로 띄우려면 `./install.sh` (PATH 에 래퍼 설치, `./uninstall.sh` 로 제거).
- **상태**: `docs/FEATURES.md` 의 모든 항목 구현. 헤드리스 테스트 **65 passed**
  (`python3 tests/run.py`).
- **플랫폼**: macOS/Linux(POSIX PTY), Python 3.11+.

## 2. 실행 / 개발

```sh
python3 pytmux.py                 # attach(단일 세션)
python3 pytmux.py kill-server     # 서버와 모든 탭/셸 종료
python3 pytmux.py ls              # 탭/패널 요약
python3 pytmux.py cmd <명령>      # 외부에서 서버 제어(new-tab, split-window -h ...)
python3 pytmux.py record/replay   # 렌더 진단(화면 없이 출력 녹화→재생)

python3 tests/run.py              # 전체 헤드리스 테스트
python3 tests/run.py test_client  # 특정 모듈만
python3 -m py_compile pytmuxlib/*.py   # 빠른 문법 점검

./install.sh [DIR]                # PATH 에 `pytmux` 래퍼 설치(기본 ~/.local/bin)
BIN=pt ./install.sh               # 다른 이름으로 설치
./uninstall.sh [DIR]              # 래퍼 제거(설치 시 쓴 DIR/BIN 동일 인자)
```

> 원격 로그인 시 자동 attach 를 원하면 `~/.zshrc` 에 가드 블록(인터랙티브 + tty +
> 미중첩 + ssh/mosh)을 넣어 `pytmux` 한 줄을 실행 — 자세한 예시는 README 의
> "SSH/mosh 접속 시 자동 실행" 절. 중첩 방지는 `$PYTMUX` 환경변수로 한다.

> ⚠️ **데몬 재시작 주의**: 서버(데몬)는 셸을 보유한 장수 프로세스라 **클라이언트를
> 다시 띄워도 서버 코드는 갱신되지 않는다.** 서버 측(`server.py`/`model.py`/`protocol.py`)
> 을 바꿨으면 `kill-server` 후 재기동해야 반영된다(실행 중 셸은 종료됨). 클라이언트
> (`client.py`)만 바꿨으면 attach 다시 하면 된다. "왜 안 바뀌지?" 의 90%가 이것이다.

## 3. 아키텍처

클라이언트–서버 분리(앱/터미널을 닫아도 셸 유지):

- **서버(데몬)** `pytmuxlib/server.py`: PTY·자식 셸·pyte 화면 버퍼·트리 소유. 유닉스
  도메인 소켓에서 **길이 프리픽스 JSON** 메시지로 통신. 이중 포크로 데몬화.
- **클라이언트** `pytmuxlib/client.py`: Textual 앱. 서버가 보낸 레이아웃/화면/상태를
  렌더하고 키·마우스를 명령/입력으로 보냄.

### 계층 모델 (중요 — tmux 와 다름)

```
Server → sessions(항상 1개) → Session.tabs[] → Tab.window(단일) → Window.root(패널 분할 트리)
```

- **단일 세션 모델**: 멀티 세션 개념은 사용자 표면에서 제거됨. `get_or_create_session`
  은 이름 요청을 무시하고 항상 같은 세션을 반환.
- **Tab** = 최상위 전환 단위(= tmux 의 윈도우 역할). 이름/인덱스/활동·벨/Claude
  상태 보유. **Window** = 탭에 종속된 단일 렌더 영역(패널 트리·줌·동기화 보유).
  **Pane** = 셸 1개(PTY + pyte).
- 호환용 `Session.active_window` 프로퍼티 = `active_tab.window` (패널/레이아웃 코드
  대부분이 이걸로 동작 — 리팩토링 시 깨지지 않게 유지).

## 4. 모듈 지도 (`pytmuxlib/`)

| 파일 | 줄수 | 역할 |
|------|----:|------|
| `protocol.py` | ~170 | 상수·소켓 경로·프레이밍(read/write_msg)·색/시각 헬퍼, `parse_reset_delay`(리밋 해제 시각), `claude_state`/`claude_usage`(화면 휴리스틱) |
| `keymap.py` | ~110 | 설정 파일 로드(`load_config`), tmux 키 표기 변환 |
| `model.py` | ~460 | `Pane`/`Split`/`Window`/`Tab`/`Session`. 레이아웃 계산(`compute_layout`/`_layout` — **테두리 박스용 겹침 분할**), 프리셋 |
| `server.py` | ~1720 | `Server`: PTY·flush 루프·명령 처리·세션/탭/패널 조작·검색·버퍼·캡처·레이아웃 슬롯·자동재개·Claude 감지·출력 캡처(`opts.json` 영속). 붙여넣기(`_write_paste`)도 프롬프트 추적 경유 |
| `client.py` | ~2530 | `build_client_app()` 클로저: 위젯(MultiplexerView/TabBar/StatusBar)·모달(Prompt/Menu/CommandList/ChooseTree/ChooseLayout/Info/ChooseBuffer)·`_composite`(합성, `_draw_tab_close` 포함)·키/마우스·명령 |
| `launcher.py` | ~160 | `main()`·서브커맨드(attach/ls/kill-server/cmd/server/record/replay)·데몬화 |
| `replay.py` | ~200 | 렌더 진단: `record`(PTY 녹화)·`replay`(텍스트 프레임 재생) |

`pytmux.py` 는 얇은 진입점(위 심볼 재노출 + `main()`).

### 렌더 합성(`client.py::_composite`) 순서
1. 각 패널 내용 blit(와이드 문자 폭 인지, 연속 셀 `""`).
2. 활성 패널 커서.
3. 패널 **테두리 박스**: 비활성(회색) → 활성(primary 파랑) 순, 인접 경계 비트 병합
   (┬┴├┤┼). 패널이 하나여도 항상 그림.
4. 패널 이름(리네임/border-status 시) 위 테두리 중앙.
5. copy-mode 선택 하이라이트, display-panes 번호.
6. **Claude 마지막 프롬프트 스티키 헤더**(`_draw_claude_headers`).
7. **clock-mode 오버레이**(`_draw_clock_overlay`, 패널 전체 덮고 뒤 dim).

## 5. 구현된 주요 기능 (요지)

- 패널: 분할(`-h`=가로/상하, `-v`=세로/좌우 — **tmux 와 반대, 한국어 직관 기준**),
  이동·줌·swap/rotate·break/join·동기화·제목·테두리 아웃라인.
- 탭: 새 탭(=새 윈도우)·삭제·이름변경·선택·재정렬(드래그/Shift+←→ Enter 확정/
  `move-tab-left/right/first/last`). **상단 탭바**(기본 항상 표시, `tab-bar auto`
  면 2개↑일 때만), 마우스 클릭·ESC 위 방향키 포커스→←→ 선택→Enter, 폭 초과 시 ◀▶ 스크롤.
  `[+]` 새 탭은 **마지막 탭 바로 오른쪽**, 탭 닫기 `[x]` 는 **콘텐츠 영역 오른쪽 위
  모서리**(`_draw_tab_close`, 상단 테두리 행에 그려 Claude 헤더·시계와 비겹침).
  닫기 `[x]`·키바인딩·`&` 메뉴는 모두 **중앙 확인 팝업**(`ConfirmScreen`, `닫기`/`취소`
  를 좌우 배치·선택된 쪽만 유채색/미선택 무채색·←→·Enter·y/n·Esc·버튼 터치, 기본=취소).
- 탭별 **레이아웃 슬롯** 저장/불러오기(`layout-save`/`layout-load`/`layout-load-new`,
  메뉴·선택기). 디스크 영속(`<sock>.slots.json`).
- 명령 프롬프트: 고정 `:` 프리픽스, `?`/`help` 목록(**카테고리 탭** — ←→ 전환,
  ↑↓ 명령 이동), 자동완성(옵션 포함). **부분일치 후보 영역**: 명령 이름을 치면
  접두사뿐 아니라 **중간 일치**(예: `tab`→`new-tab`/`kill-tab`…)까지 입력 줄 위로
  펼쳐 보여줌(↑↓ 선택, Tab/Enter 채우기 → 다시 Enter 로 실행).
  **F12 로 바로 진입**(ESC 모드 아닐 때). `prefix F12` = 중첩 패스스루 토글.
- 색: p4v-tui 와 동일한 Textual `textual-dark` 팔레트(`theme_color()` 로 해석).
- clock-mode: 현재 패널 전체를 큰 시계로 덮음(뒤 dim, [x]/명령/**하단 바 시계
  클릭**으로 토글).
- copy-mode 스크롤백/검색/선택복사/클립보드, 붙여넣기 패스스루.
- **Claude Code 연동(고유)**: 토큰 리밋 자동재개(prefix R), 탭 상태 아이콘
  (대기 ○/처리중 ◐/리밋 ⊘), 마지막 프롬프트 스티키 헤더, 토큰/컨텍스트 표시.
- **패널 출력 캡처(진단)**: 각 패널 raw 출력을 `<sock>.capture/pane-<id>.log` 로
  무손실 기록(탭 매핑은 `sessions.log`). Claude 화면 문구 분석용. 기본 ON,
  `capture-output [on|off]` 토글(상태줄 `REC`), 상태는 `<sock>.opts.json` 영속.

## 6. ⚠️ 깨지기 쉬운/휴리스틱 부분 (주의)

- **Claude 감지(`protocol.claude_state`/`claude_usage`)**: 패널 화면 텍스트의 특정
  문자열에 의존한다. **Claude Code 버전이 표시 문구를 바꾸면 오작동/미표시**.
  실제 화면 문구를 확인해 정규식을 보강해야 한다. 가장 손볼 가능성이 높은 곳.
  - 현행(2026, CL 56315 기준)은 busy 시 **작업 스피너 줄** `✽ Crunching… (38s · ↓ 1.9k
    tokens)`(글리프·동명사·시간 매 프레임 변동, **"esc to interrupt" 없음**)을, idle
    시 **권한 모드 footer** `⏵⏵ auto mode on (shift+tab to cycle)`(accept edits/plan
    mode 로 순환)를 그린다. 그래서 `_BUSY_SPINNER_RE`(말줄임표+괄호 경과시간)와
    `"shift+tab to"` 로 잡는다. 모드 footer 는 busy 중에도 같이 보이므로 **busy 를 먼저**
    판정한다. 레거시 "esc to interrupt"/"? for shortcuts"/"bypass permissions" 신호도
    하위호환으로 유지.
  - 실제 화면 재현법: `<sock>.capture/pane-*.log`(raw, ANSI 섞임)를 **pyte 로 다시
    렌더**해야 `claude_state` 가 보는 텍스트와 같아진다(raw grep 은 글자 사이 이스케이프
    때문에 빗나감). `pyte.ByteStream` 에 로그 tail 을 feed → `screen.display` 하단 줄 확인.
- **마지막 프롬프트 추적(`server._track_prompt`)**: 입력 바이트를 누적하고 Enter 시
  확정. 백스페이스/CSI(화살표) 건너뜀·bracketed paste 본문 포함은 처리하나, 복잡한
  줄 편집은 근사치. **붙여넣기 경로(`_write_paste`)도 같은 추적을 거친다** — 모바일
  받아쓰기/자동완성은 키 입력이 아니라 paste 로 들어오므로, 이게 없으면 Claude 헤더가
  셸 실행 명령("claude")에 머문다(붙여넣기로 본문 누적 → 이후 Enter 로 확정).
- **레이아웃 겹침 분할(`model._layout`)**: 패널 테두리를 위해 자식이 경계 셀을
  공유(겹침)한다. 한 변당 최소 `MIN_W=MIN_H=3`. 분할 좌표를 만질 땐 합성(`_composite`)
  의 박스/내용 inset 과 함께 봐야 한다.
- **상태줄 텍스트 매칭 테스트**: 시계("10:03")가 "0:" 같은 부분문자열과 충돌할 수
  있음 — 테스트 단언은 구체적으로(`:win`/`:zsh` 등) 쓸 것.
- **PTY master fd 격리(`server._fork_shell` 의 CLOEXEC)**: `pty.fork()` 의 master 는
  기본적으로 close-on-exec 가 아니라, 새 패널을 만들 때마다 자식 셸이 형제 패널들의
  master fd 를 상속한다. 이러면 master 가 여러 프로세스에 살아남아 패널 종료·fd 재사용
  시 **출력이 섞여 새 탭이 다른 패널을 "복사"한 듯** 보인다(특히 활성 패널이 Claude
  같은 대체화면 앱일 때). 그래서 master 생성 직후 **반드시 `FD_CLOEXEC`** 를 건다
  (CL 56309). 헤드리스로는 잘 안 드러나고 **데몬화+다수 패널 churn** 에서만 재현되니,
  PTY 생성/복제 코드를 만질 땐 이 불변식을 깨지 말 것. 진단은 캡처 로그
  (`<sock>.capture/pane-<id>.log`)가 결정적 — 새 패널 로그에 다른 패널 화면이 찍히면
  fd 누수다.
- **macOS PTY EOF 는 EIO**: 슬레이브(자식)가 끝나면 master 읽기에서 빈 바이트가 아니라
  `OSError(EIO)` 가 난다. `_on_pane_readable` 은 EIO/빈 읽기만 EOF 로 처리하고 그 외
  일시적 오류는 무시한다(살아있는 패널을 잘못 닫으면 fd 가 재사용되며 위 fd 꼬임 유발).

## 7. 테스트

- `tests/run.py`: `test_*.py` 의 `async def test_*` 를 각각 새 루프에서 실행, PASS/FAIL.
- 모두 **헤드리스**(디스플레이 불필요). 서버는 `harness.server_only()`, 정리는
  `harness.teardown()`(serve 태스크는 await 하지 않음 — Textual run_test 루프 충돌 회피).
- 화면 검증은 `app.view._cells` / `render_line` 텍스트, 또는 `pytmux.replay()` 골든
  스냅샷.
- 파일: `test_protocol`(프레이밍·색·리밋·claude 휴리스틱), `test_model`(스크롤백·
  대체화면·와이드), `test_server`(패널/탭/세션/재정렬/레이아웃 슬롯/리사이즈/Claude),
  `test_client`(프롬프트·탭바·clock·Claude 헤더/아이콘·합성), `test_replay`.

## 8. 작업 워크플로 (필수)

`docs/CONTRIBUTING.md` 참조. 요지:

1. **의미 있는 변경 단위마다 서브밋.** 무관한 변경을 한 CL 에 섞지 않는다.
2. **디폴트 체인지리스트 금지** — 항상 번호 CL.
3. **상세 디스크립션**(한 줄 요약 `[scripts/pytmux] <영역>: <요약>` + 배경/변경/파일).
4. **서브밋마다 GitHub 에 동일하게 커밋·푸시**(origin main).

세션 중에는 임시 헬퍼(열린 파일을 번호 CL 로 옮겨 submit + git commit/push)를 썼다:

- `/tmp/ship.py <desc파일>` — 디폴트 체인지리스트의 **열린 파일 전부**를 한 CL 로.
- `/tmp/ship2.py <desc파일> <file...>` — **지정한 파일만** 빈 번호 CL 로 옮겨 submit.
  무관한 변경을 두 CL 로 나눠 보낼 때 사용(예: 56297/56298 분리 서브밋).

**`/tmp` 라 영구적이지 않다** — 없으면 아래처럼 수동 수행:

```sh
p4 edit <수정파일> ; p4 add <새파일>          # 대상 열기
p4 change                                     # 설명 작성 → "Change NNNN created"
p4 submit -c NNNN
git add -A && git commit -m "<설명>" && git push   # GitHub 미러
```

**무관한 변경을 두 CL 로 쪼개는 법**(이 세션의 56308/56309 처럼): `p4 change -o` 로
스펙을 받아 `Description` 을 채우고 `Files:` 섹션에서 **그 CL 에 넣을 파일만 남긴** 뒤
`p4 change -i` 로 번호 CL 을 만들고(나머지는 디폴트에 남음) 각각 `p4 submit -c`. git 도
파일 단위로 `git add` 해서 같은 수의 커밋으로 나눈다(메시지에 `Perforce: change NNNN`
푸터를 달아 둠).

## 9. 최근 변경(CL 56279~56351 + git, 신→구)

- 56351 원격 SSH 휠 스크롤백 미동작(§10) **진단 계측** 추가 — 환경 의존이라
  코드만으론 단정 불가. `set mouse-debug on`(별칭 mouse-log) 으로 켜면
  `MultiplexerView` 의 down/scroll_up/scroll_down 핸들러가 받은 이벤트를
  `<sock>.mouse.log` 에 남긴다. 원격에서 휠이 Textual 까지 도달하는지(조사 방향
  ①)를 切り分け 하는 용도. 회귀 테스트. 클라이언트 전용(attach 재실행 반영).
- 56347 **내부 마우스 TUI 앱 마우스 패스스루** 구현(§10 해결) — 서버가 내부 앱의
  DECSET 1000/1002/1003/1006 을 추적(`Pane.update_mouse_modes`)해 패널별
  `mouse`/`mouse_sgr` 로 레이아웃에 실어 보내고, 클라이언트가 마우스 모드 ON
  패널의 content 영역에서 normal 모드일 때 SGR(1006)/X10 으로 인코딩해 PTY 로
  전달(`send_mouse`→`_handle_input` mouse 플래그, 동기화/프롬프트 추적 제외).
  prefix/copy-mode 면 pytmux 우선. 휠도 마우스 모드 앱엔 전달. 회귀 테스트 2종.
  **서버+클라이언트 양쪽 변경이라 `kill-server` 재기동 후 반영.**
- (신규) XTMODKEYS 제거로 **로컬 Claude Code 전체 밑줄 버그 재발** 수정 — 56333
  의 콜론식 SGR 정규화로 한 번 잡았으나, 현행 Claude Code 는 capable 터미널을 감지
  하면 modifyOtherKeys 토글로 `CSI > 4 ; Ps m`(XTMODKEYS)을 내보낸다. pyte 0.8.2
  의 CSI 파서가 `>` private 마커를 무시하고 이를 `CSI 4 ; Ps m`(=SGR 밑줄 ON)으로
  잘못 읽어, 콜론 경로와 무관하게 다시 화면 전체에 밑줄이 번졌다. `model.feed` 에
  `_PRIVATE_SGR_RE`(`CSI [<>=]..m`) 제거 단계를 추가해 pyte 에 닿기 전에 통째로 버린다
  (pytmux 가 자체 키보드 프로토콜을 다루므로 이 시퀀스는 불필요). feed 경계 캐리
  (`_CSI_PARTIAL_RE`)는 이미 `<>=` 를 포함해 쪼개진 시퀀스도 안전. 회귀 테스트 추가.
  **서버 변경이라 `kill-server` 재기동 후 반영.**
- 56333 콜론식 SGR 정규화로 **로컬 Claude Code 전체 밑줄 버그** 수정(§10 해결) —
  pyte 0.8.2 가 콜론(:) 서브파라미터를 미지 문자로 보고 SGR 시퀀스를 끊는 탓에,
  capable 터미널을 감지한 Claude Code 가 내보내는 `CSI 4:0 m`(밑줄 끄기 콜론형)을
  못 읽어 밑줄이 영영 안 꺼지고 이후 모든 셀에 번졌다(곱슬밑줄 `4:3`·24bit `38:2::`·
  밑줄색 `58:` 도 동일). `model.feed` 경로에 `_sanitize_sgr` 추가로 콜론형 SGR 을
  세미콜론 형태로 정규화(4:0→24, 4:n→4, 38:2::r:g:b→38;2;r;g;b, 58:·형식불명→제거).
  feed 경계 캐리를 `_CSI_PARTIAL_RE` 로 확장해 쪼개진 콜론 SGR 도 완전 시퀀스로 처리.
  회귀 테스트 추가. 클라이언트 합성이 아니라 서버 화면 버퍼(pyte) 단계의 문제였다.
  **서버 변경이라 `kill-server` 재기동 후 반영.**
- 56324 README 키 바인딩에 Shift 조합 패스스루 안내 추가(문서만).
- 56321/56323 Shift+Enter/Shift+Escape 를 활성 패널로 전달 — Textual 이 Shift 조합을
  `event.key="shift+enter"/"shift+escape"`(character=None)로 주는데 `SPECIAL` 에
  매핑이 없어 `key_to_bytes` 가 빈 바이트를 돌려줘 키가 삼켜졌다. `"shift+enter": b"\n"`
  (LF — Claude 멀티라인 입력, 그냥 Enter=CR 제출과 구분), `"shift+escape": b"\x1b"`
  (앱으로 ESC 전달 — ESC 단독은 esc 모드 진입이라 별도 조합) 추가 + 회귀 테스트 3종.
  **교훈**: 56321 최초 제출 때 git 미러가 depot 보다 뒤처져 있어 stale 한 git HEAD 로
  파일을 재구성하는 바람에 다른 기계(@surface, 56319)의 UI 변경을 의도치 않게 되돌렸고
  56323 으로 복구. depot 이 source of truth — 제출 전 `p4 diff`/`p4 print #have` 로
  비교하고 `git checkout HEAD -- file` 로 격리하지 말 것.
- 56320 Claude busy(처리중) 감지 보강 — busy footer 가 `<글리프> <동명사>… (12s · ↑ 1.9k
  tokens · still thinking)` 형식인데 글리프가 폰트/터미널에 따라 `*`/`·` 로 렌더되거나
  idle footer(shift+tab to cycle)와 겹쳐 보일 때 놓쳤다. `_BUSY_SPINNER_RE` 에 글리프
  `*`/`·` 변형·토큰 화살표(↑/↓ … tokens)·`still thinking` 시그널 추가, 화살표 없는 토큰
  언급("1.2k tokens used")은 오탐 안 하도록 유지. 회귀 테스트 보강.
- 56319 Shift+Tab(backtab) 을 활성 패널로 전달 — Textual 이 Shift+Tab 을
  `event.key="shift+tab"`(character=None)로 주는데 `client.py::SPECIAL` 에
  매핑이 없어 `key_to_bytes` 가 빈 바이트를 돌려줘 키가 삼켜졌다(특히 Claude
  권한 모드 순환이 안 됨). `SPECIAL` 에 `"shift+tab": b"\x1b[Z"`(CSI Z) 추가 +
  회귀 테스트. 클라이언트 전용이라 attach 재실행으로 반영(서버 재기동 불필요).
- (git dcf0740) 모바일 UI 개선 묶음 — ① Claude 헤더가 붙여넣기 프롬프트도 추적
  (`_write_paste`→`_track_prompt`): 모바일 받아쓰기/자동완성은 키 입력이 아니라 paste 라
  이전엔 헤더가 셸 실행 명령("claude")에 머물렀음. ② ConfirmScreen 닫기/취소 좌우 배치·
  선택된 쪽만 유채색/미선택 무채색·터치 확정. ③ CommandListScreen 화면 레벨 on_key 에서
  Enter 직접 처리(ListView 포커스 의존 제거). ④ 상태줄 날짜 `%d-%b-%y`→`%Y-%m-%d`.
  ⑤ 탭바 `[+]` 를 마지막 탭 오른쪽으로, 탭 닫기 `[x]` 를 콘텐츠 오른쪽 위 모서리로
  분리(`_draw_tab_close`). 테스트 62 passed.
- 56315 Claude 감지 정규식을 현행 Claude Code busy/idle footer 에 맞춰 보강 —
  busy 는 작업 스피너 줄(`✽ Crunching… (38s · ↓ 1.9k tokens)`, "esc to interrupt"
  없음)을 `_BUSY_SPINNER_RE`(말줄임표+괄호 경과시간/글리프+동명사)로, idle 은 권한
  모드 footer(`⏵⏵ auto mode on (shift+tab to cycle)`)를 "shift+tab to" 로 판정.
  레거시 신호 하위호환 유지·busy 우선. 실제 문구는 capture 로그 pyte 재렌더로 확인.
  테스트 62 passed.
- 56313 상단 탭바 기본 항상표시 + 하단 시계 클릭으로 clock-mode 토글 — tab-bar
  기본값 always(`set tab-bar auto` 로 2개↑만 표시). `: set`/source-file 런타임
  반영(`set_tab_bar_always`). StatusBar 오른쪽 시계 영역(`_clock_zone`) 클릭 시
  활성 패널 clock-mode 토글. 테스트 61 passed.
- 56311 README 에 SSH/mosh 자동 attach 셸 설정 안내 추가 — 원격 로그인 시 가드
  (인터랙티브 + tty + 미중첩 + ssh/mosh)로 `pytmux` 한 줄 실행. 중첩 방지는
  `$TMUX` 대신 `server.py` 가 패널 셸에 심는 `$PYTMUX`(server.py:48) 검사. 문서 전용.
- 56310 문서 최신화 + 세션 학습 메모(`docs/MEMORY.md` 신규) — 데몬 stale 판별·
  PTY fd 진단·CLOEXEC 교훈 등 비자명한 함정 기록. 문서 전용.
- 56309 새 패널 PTY master fd 격리(CLOEXEC) + EOF 처리 강화 — **새 탭이 기존 탭을
  "복사"하던 버그 수정**(§6 참조). 서버 변경이라 `kill-server` 재기동 후 반영.
- 56308 탭 닫기 확인을 중앙 팝업(`ConfirmScreen`)으로 통일
- 56305 명령 프롬프트 부분일치 자동완성 + 후보 영역(↑↓ 선택·Tab/Enter 채우기)
- 56303 설치/제거 스크립트 추가(`install.sh`/`uninstall.sh` — 어디서든 `pytmux` 실행)
- 56298 패널 출력 캡처(Claude 화면 분석용, 기본 ON·`opts.json` 영속, 상태줄 REC)
- 56297 ?/help 명령 목록을 카테고리 탭으로 분할(←→ 카테고리·↑↓ 명령)
- 56293 F12 로 명령 프롬프트 바로 진입(중첩 토글은 prefix F12)
- 56292 활성 Claude 패널 토큰/컨텍스트 상태줄 표시(best-effort)
- 56291 Claude 상태 탭 아이콘 + 마지막 프롬프트 스티키 헤더
- 56290 탭 재정렬(드래그·Shift+←→·move-tab-left/right/first/last)
- 56289 clock-mode 를 패널 전체 덮는 큰 시계로
- 56288 문서를 구현에 맞게 갱신
- 56287 리사이즈 비율 재계산 회귀 테스트
- 56286 탭별 레이아웃 저장/불러오기
- 56285 컬러 스키마를 p4v-tui(textual-dark)로 통일
- 56284 상단 탭바 하단 탭목록 생략 + 가로 스크롤
- 56283 split -h/-v 방향 수정 + 단일 패널도 활성 아웃라인
- 56282 명령 프롬프트 고정 ':' 프리픽스
- 56281 상단 탭바 + ESC 탭 내비게이션
- 56279 help 팝업 스크롤바

(그 이전: 단일 세션 전환, Session→Tab→Window 계층 도입, 패널 테두리 박스, 패널 이름,
리플레이 진단툴 등 — `p4 changes` 또는 git log 참조.)

## 10. 가능한 후속 작업 (열린 항목)

- ~~**[버그] 내부 마우스 TUI 앱(p4v-tui 등)에 마우스 입력이 전달 안 됨**~~ →
  **CL 56347 에서 해결.** 위 "구현 방향" 그대로 구현했다: ① 서버(`model.Pane.
  update_mouse_modes`)가 내부 앱의 DECSET 1000/1002/1003/1006 을 추적해
  `mouse_track`(0~3)/`mouse_sgr` 로 두고, 바뀌면 레이아웃을 다시 보내 패널별
  `mouse`/`mouse_sgr` 플래그로 클라이언트에 전달. ② 클라이언트(`MultiplexerView.
  _mouse_target`/`_encode_mouse`)가 마우스 모드 ON 패널의 **content 영역**(테두리
  제외)에서 normal 모드일 때 마우스를 SGR(1006)/X10 으로 인코딩해 `send_mouse`
  →해당 패널 PTY 로 전달(`_handle_input` 의 `mouse` 플래그: 동기화/프롬프트 추적
  제외). ③ prefix/copy-mode 면 pytmux 가 가로챔. 휠도 마우스 모드 앱엔 전달.
  좌표는 패널 content 오프셋(`p["x"]/p["y"]`)을 빼서 1-based 로 변환. **주의:
  `set mouse off` 면 pytmux 가 마우스를 아예 안 써 패스스루도 비활성(기본 on).**
- **[버그·환경 의존, 진단 추가됨] 원격 SSH 환경에서 마우스 휠 위쪽 스크롤백이
  동작 안 함** — 로컬은 정상, 원격 SSH 만 위쪽 휠 스크롤 안 됨. 코드 경로
  (`on_mouse_scroll_up`→`send_scroll`)는 로컬에서 정상이라 **상위 터미널/SSH 가
  휠을 Textual 까지 전달하느냐**의 환경 문제로 의심(헤드리스/로컬 재현 불가).
  **CL 56351 에서 조사 방향 ①을 위한 진단 계측 추가**: `set mouse-debug on` 으로
  켜면 받은 마우스/휠 이벤트가 `<sock>.mouse.log` 에 찍힌다. **원격에서 켜고 휠을
  올린 뒤 mouse.log 확인** — `scroll_up` 이 찍히면 이벤트는 도달한 것(→서버
  scroll 처리/터미널 재그리기 ③을 조사), 안 찍히면 상위 터미널이 휠을 안
  넘기는 것(①: 터미널 마우스 트래킹·`$TERM`·SGR 1006·mosh vs ssh 차이, 또는
  ② 터미널이 자체 스크롤백으로 가로챔 → 터미널 설정). 이게 환경(①/②)이면
  pytmux 코드 수정으로는 못 고치고 터미널 쪽 설정이 필요하다.
- ~~**[버그] 로컬 실행 시 Claude Code 인터페이스 글자에 원치 않는 밑줄**~~ →
  **CL 56333 에서 해결.** 원인은 합성 단계가 아니라 **서버 화면 버퍼(pyte) 단계**
  였다: pyte 0.8.2 의 CSI 파서가 콜론(:) 서브파라미터를 미지 문자로 처리해 SGR
  시퀀스를 끊는데, capable 터미널을 감지한 Claude Code 가 밑줄 끄기를 콜론형
  `CSI 4:0 m` 으로 보내 pyte 가 `4`(밑줄 ON)만 읽고 끊어 밑줄이 영영 안 꺼지고
  이후 모든 셀에 번졌다(원격은 단순 세미콜론형/밑줄 미사용이라 정상). `model.feed`
  에 `_sanitize_sgr`(콜론형 SGR → 세미콜론 정규화) 추가로 수정. **교훈: pyte
  0.8.2 는 콜론식 SGR(곱슬밑줄 `4:3`·24bit `38:2::r:g:b`·밑줄색 `58:`)을 전혀
  파싱 못하므로, 비슷한 "장식이 번지거나 잔해가 찍히는" 증상은 먼저 이 정규화
  경로를 의심할 것.**
- ~~Claude 감지/사용량 정규식을 실제 Claude Code 화면 문구에 맞춰 보강(§6).~~
  → **busy/idle 은 CL 56315 에서 현행 문구(작업 스피너·권한 모드 footer)에 맞춰 보강
  완료**(§6). 남은 것: `claude_usage` 의 토큰 수("↓ N tokens")는 스트리밍 델타라
  누적 컨텍스트와 다름 — 컨텍스트 잔량%·`(1M context)` 모델 배지 등 더 의미있는 신호로
  교체 여지. 리밋(limit) 문구는 실제 리밋 캡처 샘플이 없어 미검증.
- **[요청·미구현] ESC 모드 탭 전환 Enter 한 번으로 확정+복귀** — 현재 ESC 모드에서
  위 방향키로 상단 탭바에 포커스를 준 뒤 ←→ 로 탭을 고르고 **Enter 를 누르면 그 탭으로
  전환되지만 ESC 모드는 유지**(`tb.bar_focus` 만 해제)되고, **Enter 를 한 번 더** 눌러야
  ESC 모드에서 빠져나오며 전환이 확정된다. 요청: ESC 모드에서 탭을 골라 **Enter 를 처음
  누를 때 바로 ESC 모드에서 빠져나오며 탭이 확정**되게 한다. 손볼 곳은
  `client.py::_handle_esc_mode` 의 `elif k == "enter":` 분기(탭바 포커스 블록) — 현재
  `select_window` 후 `tb.bar_focus=False; tb.refresh()` 만 하는데, 대신 `_exit_esc()` 를
  호출하면 bar_focus 해제·refresh 까지 포함해 한 번에 종료된다. 클라이언트 전용 변경이라
  attach 재실행으로 반영(서버 재기동 불필요). 회귀 테스트(`test_tab_bar_and_esc_nav`)에
  Enter 한 번 뒤 `app.mode == "normal"` 단언 추가 권장.
- **[요청·미구현] 상태줄 REC 클릭 시 캡처 정보 팝업** — 왼쪽 아래 `REC` 표시를
  클릭하면 **현재 탭/패널이 어느 경로의 어느 파일에 기록되고 있는지와 그 파일 크기**를
  보여주는 팝업을 띄운다. 캡처 경로는 `<sock>.capture/pane-<id>.log`(서버
  `server.py::_capture_write`, `capture_dir`), 탭 매핑은 같은 디렉터리 `sessions.log`.
  구현 방향: ① `client.py` StatusBar 의 REC 세그먼트에 클릭 영역을 등록(현재 오른쪽
  시계 `_clock_zone`(`client.py` ~1128/1135)과 동일 패턴으로 `_rec_zone` 좌표 추가,
  REC 는 줄 맨 왼쪽 `"REC "` 4칸). ② 클릭 시 활성 패널의 캡처 파일 경로·크기를 표시할
  팝업(`InfoScreen` 류 모달) 노출 — 경로는 클라이언트가 알기 어려우니 서버에서 활성
  패널의 캡처 파일 절대경로와 `os.path.getsize` 결과를 내려주는 경로 추가(예: 상태/
  레이아웃 메시지에 `capture_path`/`capture_size` 동봉, 또는 전용 질의 명령). 캡처가
  off(`REC` 미표시)일 때는 클릭 영역도 없음. **주의**: 이 REC/캡처 기능은 개발 중
  디버깅용이며 어느 정도 궤도에 오르면 제거 예정 — 깊게 결합시키지 말고 분리 가능하게.
- **[요청·미구현] 상태줄 토큰 사용량을 세션 누적·세션 종료 전까지 유지** — REC 옆의
  토큰 사용량 표시는 활성 Claude 패널 화면을 읽어 만든다(`protocol.claude_usage` →
  서버 `server.py` 의 `_claude_usage`(~1337-1341) 설정 → 레이아웃 메시지 `claude_usage`
  (~1273-1275) → 클라이언트 StatusBar(`client.py` 1070, 1101-1102) 표시). 현재는
  **화면에 지금 보이는 토큰 수치만** 반영하므로, ① 한 세션에서 프롬프트를 여러 번 입력해도
  직전 값으로 덮어써질 뿐 **누적 합계가 아니고**, ② 화면에서 토큰 문구가 사라지면
  `new_use` 가 None 이 되어 **표시도 사라진다**. 요청: (1) **한 세션의 전체 토큰 사용량을
  이전 사용량에 이어 누적**해서 보여주고, (2) **화면에서 표시가 사라져도 Claude Code 세션이
  끝나기 전까지는 계속 표시**한다. 구현 방향: 패널마다 세션 누적값(예: `_claude_usage_total`)을
  두고, `_claude` 상태가 None→비None 으로 바뀌는 시점을 **세션 시작**(누적 리셋), 화면에서
  사용량이 갱신될 때마다 합산, `_claude` 가 종료로 빠지는 시점을 **세션 끝**으로 보고 클리어.
  화면에서 수치가 사라져도(`new_use is None`) `_claude` 가 아직 살아 있으면 **마지막 누적값을
  유지**(None 으로 덮지 말 것). **주의/난점**: §10 아래에 이미 적힌 대로 화면의
  "↑/↓ N tokens" 는 **스트리밍 델타**라 누적 컨텍스트와 의미가 다르다 — 단순 합산하면 중복
  계상될 수 있으니, 무엇을 "세션 전체 토큰"으로 정의할지(델타 누적 vs Claude 가 주는 누계
  표시 파싱) 먼저 정해야 한다. 세션 경계 판정도 `claude_state` 휴리스틱에 의존하므로
  오판 시 누적이 어긋날 수 있음.
- **[요청·미구현] 대기(큐) 중인 새 프롬프트는 첫 줄 헤더를 아직 바꾸지 말 것** — Claude
  패널 첫 줄의 스티키 헤더는 마지막으로 입력한 프롬프트를 보여준다(서버
  `server.py::_track_prompt`(~1632)가 Enter 즉시 `pane.last_prompt` 확정 → 레이아웃 메시지
  `prompt`(~1268-1269) → 클라이언트 `_draw_claude_headers`). 현재는 **이전 프롬프트가 아직
  실행 중인데 새 프롬프트를 입력하면 Enter 즉시** 헤더가 새 프롬프트로 바뀐다. 요청: 새로
  입력한 프롬프트가 **아직 처리되지 않고 대기(큐)중일 때는 첫 줄 프롬프트를 바꾸지 말고**,
  그 프롬프트가 **전달되어 처리되기 시작하는 시점에** 헤더를 새 프롬프트로 갱신한다. 즉
  헤더는 "지금 처리 중인 프롬프트"를 반영해야 함(Claude Code 가 busy 중 입력을 큐잉했다가
  순차 처리하는 동작과 맞춤). 구현 방향: `_track_prompt` 에서 Enter 확정 시 패널이 이미
  busy(`p._claude` 가 처리중)면 **`last_prompt` 를 즉시 덮지 말고 `pending_prompt` 로 보관**,
  현재 처리 끝나고 다음 프롬프트 처리가 시작되는 전이(busy 재진입/스피너 갱신 등 `claude_state`
  신호)를 잡아 그때 `pending_prompt`→`last_prompt` 로 승격. **난점**: "큐된 프롬프트가 처리
  시작됐다"는 전이를 화면 휴리스틱으로만 판정해야 한다(연속 busy 라 idle 갭이 안 보일 수
  있음) — Claude 화면의 큐 표시/프롬프트 에코를 단서로 삼거나 보수적으로 처리. 입력·붙여넣기
  둘 다 `_track_prompt` 를 거치므로(§6) 두 경로 모두 동일하게 적용.
- **[요청·미구현] Claude 프롬프트 헤더를 ESC 모드로 선택 → 프롬프트 히스토리 팝업** —
  Claude 패널 첫 행 스티키 헤더는 직전 프롬프트 **하나만** 보여준다(`_draw_claude_headers`,
  서버는 `pane.last_prompt` 단일만 보관). 요청: ① **ESC 모드에서 방향키로 이 헤더
  인터페이스를 선택**할 수 있게 하고, ② 선택 후 **Enter 또는 클릭**하면 **이전에 입력한
  프롬프트들을 시간 순으로 조회하는 팝업**을 띄운다. 구현 방향:
  - **서버**: 프롬프트 히스토리 보관 추가 — `_track_prompt`(Enter 확정 시점)에서
    `last_prompt` 외에 패널별 `prompt_history`(시간순 리스트)에 append. 레이아웃/상태
    메시지나 전용 질의 명령으로 클라이언트에 히스토리 전달(길면 최근 N개·요청 시 풀 조회).
    Claude 세션 경계(`claude_state` 신호)로 묶을지 여부 결정.
  - **클라이언트(ESC 모드 선택)**: `_handle_esc_mode` 의 포커스 대상에 "Claude 헤더"를
    추가 — 현재 포커스 이동은 패널(`select_pane`)과 상단 탭바(`bar_focus`) 두 종류뿐이니,
    활성 패널이 Claude 헤더를 가진 경우 방향키로 헤더에 포커스가 가도록 새 상태 추가
    (탭바 포커스 패턴 참고). 포커스 시 시각 표시.
  - **팝업**: 클릭 영역은 기존 `_claude_close_zones`([x] 닫기)와 별개로 **헤더 본문
    클릭 zone** 추가, Enter/클릭 시 프롬프트 히스토리 모달(`ChooseBuffer`/`InfoScreen` 류
    스크롤 가능한 리스트)을 시간 순으로 노출. 좌표 변환 시 패널 오프셋·테두리 inset 주의.
  - **주의**: §6 의 last_prompt 추적은 근사치(백스페이스/CSI/붙여넣기 처리)라 히스토리도
    같은 한계 — 복잡한 줄 편집은 부정확할 수 있음.
- **[요청·미구현] Claude 헤더 첫 행 닫기 버튼 제거 → 팝업에서 숨김 설정·명령으로 복원** —
  현재 Claude 패널 첫 행 프롬프트 헤더 **좌측에 닫기 `[x]` 버튼**이 있다
  (`_draw_claude_headers` 가 `[x]` 를 그리고 `_claude_close_zones` 등록 → 클릭 시
  `close_claude_header` 가 `_claude_hidden[pid]=prompt` 로 그 프롬프트만 숨김; 새 프롬프트가
  오면 `_update_claude` 가 다시 보이게 함). 요청: ① 이 **`[x]` 닫기 버튼을 제거**한다. ②
  대신 위 항목의 **첫 행 클릭 팝업**(프롬프트 히스토리 팝업) 안에서 **이 첫 행을 없애도록
  설정**할 수 있게 한다(끄면 헤더가 안 보임). ③ 이후 **명령으로 이 행을 다시 켤 수 있어야**
  한다. 구현 방향:
  - `_draw_claude_headers` 에서 `[x]` 그리기·`_claude_close_zones` 제거. 프롬프트별 일시
    숨김(`_claude_hidden`) 모델 대신 **헤더 표시 여부를 영속 옵션**으로(예: `opts.json` 의
    `claude_header` on/off — 캡처 옵션처럼 서버 영속, §5/§의 `set_capture` 패턴 참고).
  - 위 히스토리 팝업에 **"이 헤더 숨기기" 토글/버튼** 추가 → 끄면 옵션 off 로 저장,
    `_draw_claude_headers` 가 off 면 그리지 않음.
  - 다시 켜는 **명령** 추가(예: `claude-header on|off`, 명령 목록·`set` 에 노출). 끈 뒤에도
    명령으로 복원 가능해야 하므로 프롬프트 단위가 아니라 패널/전역 옵션으로 둘 것.
  - **주의**: 닫기 [x] 를 없애면 우측 탭 닫기 [x](`_draw_tab_close`)와의 시각적 겹침 회피
    명분(§90 합성 순서 주석)도 사라지니, 헤더 클릭 zone 을 행 전체로 둘 수 있음.
- **[요청·미구현] 커맨드 팔레트에서 옵션 설정 후 프롬프트 없이 바로 실행** — 현재
  명령 실행은 tmux 식: 명령 프롬프트(`:`)에서 자동완성하거나 `?`/`help` 목록
  (`CommandListScreen`)에서 명령을 고르면 **프롬프트 입력 줄에 채워주고**
  (`open_prompt("command", initial=name+" ")`), 거기서 **Enter 를 눌러 실행**한다
  (`_run_command`). 요청: **명령 프롬프트는 그대로 유지**하되, **커맨드 팔레트에서 커맨드를
  선택하고 그 인터페이스 안에서 옵션을 설정해 프롬프트를 거치지 않고 바로 실행**하는 경로를
  추가한다. 구현 방향:
  - `CommandListScreen`(또는 새 팔레트 모달)에서 명령 선택 시 **프롬프트로 채우는 대신**,
    그 명령의 **옵션 입력 UI**(인자/플래그 토글·값 입력)를 모달 안에 펼치고, "실행" 액션이
    완성된 명령 줄을 만들어 `_run_command` 를 **직접 호출**(프롬프트 우회).
  - **옵션 메타데이터 필요**: 현재 `COMMANDS`(client.py ~165) 항목은 `(이름, 설명, 카테고리)`
    뿐이라 옵션 정의가 없다. 명령별 옵션 스키마(이름/타입/기본값/플래그 여부)를 추가하거나,
    옵션이 있는 명령에 한해 점진 적용. 옵션 없는 명령은 선택 즉시 실행.
  - 기존 "선택 → 프롬프트 채움 → Enter" 경로(line ~532, ~2026-2027)는 유지(둘 다 가능).
  - ESC 모드/`F12` 진입과의 동선, 카테고리 탭 UI 와의 일관성 고려.
- **[요청·미구현] 하단 상태줄(REC 줄) 배경을 터미널 배경색으로** — 화면 하단 REC 표시가
  나오는 줄(`StatusBar`)의 배경이 **고정 검정/어두운색**이라, 터미널 앱에서 배경색을 바꿔도
  이 줄에는 반영되지 않는다. 원인: `StatusBar.render_line`(client.py ~1077-1078)의 `base`
  스타일이 `bgcolor=self.bg or tc("surface")` 로 **고정 테마색**(`surface`=`#1E1E1E`,
  팔레트 ~82행)을 칠한다. 요청: 이 줄 배경을 **터미널 색상(터미널 기본 배경)이 적용되도록**
  수정. 구현 방향: 명시적 `bg` 설정이 없을 때 base 의 `bgcolor` 를 고정 `surface` 대신
  **터미널 기본 배경(`bgcolor=None`)** 으로 두어 터미널이 칠하게 한다(REC/SYNC/AR 등 개별
  배지는 자체 bgcolor 유지 — 이건 의도된 강조라 그대로). **주의**: ① 합성/렌더 경로가
  `bgcolor=None` 을 터미널 기본으로 제대로 흘려보내는지 확인(`Segment`/`Strip` 의 None bg
  처리, `adjust_cell_length` 의 패딩 채움 스타일도 base 라 함께 영향). ② 상단 탭바
  (`TabBar`)·패널 배경 등 **다른 영역도 같은 고정색을 쓰는지** 점검 — 일관성을 위해
  함께 갈지 결정. ③ 설정으로 명시 배경을 준 경우(`self.bg`)는 그대로 우선.
- **[요청·미구현] 원격(SSH) 접속이면 머신 이름에 `ssh:` 접두사 + 붉은색 표시** — 화면
  맨 아랫줄 오른쪽에 머신 이름·시간·날짜를 표시하고(`StatusBar` 의 `right_fmt` =
  `#{pane_title}#h %H:%M %Y-%m-%d`, `#h` 가 `_expand` 에서 `socket.gethostname()` 단축명으로
  치환), 이 영역을 누르면 활성 패널 clock-mode 토글(`_clock_zone`/`on_mouse_down`). 요청:
  머신 이름 표시를 ① **로컬이면 지금과 동일한 색**(현재 `base` 스타일)으로, ② **SSH 를 통한
  원격 서버면 앞에 `ssh:` 접두사를 붙이고 그 접두사+머신 이름을 붉은색**으로 표시한다.
  구현 방향:
  - **원격 판정(클라이언트 측)**: 클라이언트 프로세스 env 의 `SSH_CONNECTION`/`SSH_TTY`
    유무로 판단(클라이언트는 사용자가 attach 한 머신에서 돌고, 원격 ssh 세션이면 그 서버
    env 에 잡힘). 시작 시 1회 캐시.
  - **렌더 분리**: 현재 오른쪽은 통째로 한 `base` 세그먼트(render_line ~1120-1125)라
    host 부분만 색을 못 준다. host(`#h`) 구간을 **별도 세그먼트로 분리**해 원격이면
    `ssh:<host>` 를 붉은색(`tc("error")` 전경)으로, 로컬이면 기존 `base` 로 그린다.
    `_expand` 가 `#h` 를 문자열로 치환하므로 토큰 위치 추적이 필요 — host 만 따로 만들거나
    오른쪽을 조각내어 합성.
  - **주의**: `_clock_zone` 폭 계산(rw)·클릭 동작은 그대로 유지(접두사로 폭이 늘어나면
    zone 좌표도 같이 갱신). 시간/날짜 부분 색은 변경 없음.
- **[요청·미구현] 시계 클릭 존을 "시간" 부분으로만 한정** — 화면 오른쪽 아래에서
  **시계(시간) 부분을 클릭할 때만 clock-mode 가 켜지고**, **머신 이름·날짜를 클릭하면
  시계로 연결되지 않아야** 한다. 현재는 `_clock_zone` 이 오른쪽 전체(host + 시간 + 날짜)를
  덮어(render_line ~1126-1128, `rw = right` 전체 폭) 어디를 눌러도 토글된다. 구현 방향:
  오른쪽을 조각내어 **시간(`%H:%M`) 구간의 x 범위만** `_clock_zone` 으로 잡고
  (`on_mouse_down` ~1135-1137 은 그대로), host·날짜 구간은 zone 에서 제외. 위 "원격 SSH
  머신 이름 색" 항목과 **같은 오른쪽 영역 세그먼트 분리 작업**이라 함께 구현하면 좋다
  (host/시간/날짜를 별도 세그먼트로 쪼개면 각 구간 x 범위를 정확히 알 수 있음).
- **[요청·미구현] 날짜 클릭 시 현재 패널에 이번 달 달력 오버레이(clock-mode 식)** — 화면
  오른쪽 아래 **날짜를 클릭하면 현재(활성) 패널에 오늘을 포함한 이번 달 달력**을 표시한다.
  clock-mode 시계와 동일하게 ① **뒤 패널 내용이 흐리게(dim)** 보이고, ② **계속 업데이트**되며
  (자정 넘어가면 '오늘' 갱신·시계 tick 과 같은 주기), ③ **시계와 같은 방법으로 닫기**
  (우상단 `[x]` / 같은 영역 재클릭 / 명령). 구현 방향(기존 clock-mode 미러링):
  - clock-mode 구조를 그대로 본떠 `calendar_panes`(set)·`toggle_calendar`·
    `_draw_calendar_overlay`·`_calendar_close_zones` 추가. `_draw_clock_overlay`
    (client.py ~1550) 패턴 재사용 — 뒤 화면 dim, 가운데 정렬, 우상단 `[x]`, `_put_cell`
    로 와이드 문자 정렬 보존. 합성 순서(`_composite` ~1748)에서 clock 오버레이와 같은
    단계에 그림. 갱신은 `_clock_tick`(~1526)에 `calendar_panes` 도 포함시켜 다시 합성.
  - 달력 본문은 파이썬 `calendar` 모듈(`Calendar`/`monthcalendar`)로 이번 달 그리드를
    만들고 **오늘 날짜를 강조**(반전/색). 패널이 좁으면 축약 표시.
  - **트리거(날짜 클릭 존)**: 바로 위 "시계 클릭 존을 시간 부분으로만 한정" 항목과 한 묶음 —
    오른쪽 영역을 host/시간/날짜로 세그먼트 분리한 뒤 **날짜 구간 x 범위를 `_date_zone`**
    으로 잡고, `StatusBar.on_mouse_down` 에서 시간 구간이면 clock, 날짜 구간이면 calendar
    토글. (시계=시간, 달력=날짜로 분리.)
- **[요청·미구현] 전체 탭/패널/실행앱 한눈에 보기 + 전환·종료 팝업(로컬/원격 구분)** —
  현재 열려 있는 **모든 탭 → 탭별 패널 → 각 패널에서 실행 중인 앱**을 한 화면(팝업)에 모두
  표시하고, 그 안에서 **항목 간 전환**하거나 **선택한 탭/패널을 종료**할 수 있는 기능. **명령어로
  팝업을 열 수** 있어야 하고, 각 패널이 **로컬에서 실행 중인지 원격(SSH)에서 실행 중인지
  구분** 표시한다. 기존 인프라(확장 대상):
  - **트리 선택기**: `ChooseTreeScreen`(client.py ~353)이 이미 윈도우(탭) 목록을 보여주나
    `index:name (N panes)` 수준뿐 — **패널 단위로 들여쓰기**하고 각 패널의 실행 앱·로컬/원격
    배지를 붙이도록 확장(또는 새 "overview" 모달). 트리에서 탭/패널 선택 시
    `select_window`/`select_pane_id` 로 전환, 선택 항목 종료는 `kill_window`/`kill_pane`
    (탭/패널 닫기 확인 팝업 `ConfirmScreen` 경유).
  - **실행 앱 정보(서버)**: `_fg_command(fd)`(server.py ~567, `tcgetpgrp`+`ps -o comm=`)가
    **활성 패널만** 조회한다. 트리 스냅샷에 **모든 패널의 fg 명령**을 담도록 확장(패널별
    `master_fd` 로 조회). 트리 메시지(현재 `ChooseTreeScreen` 에 넘기는 tree)에 패널 id·
    제목·fg 앱·로컬/원격 필드 추가.
  - **로컬/원격 판정(패널별)**: 그 패널의 **프로세스 체인에 ssh/mosh/telnet 등 원격 세션이
    있으면 원격**으로 표시. fg 명령이 ssh/mosh 면 단순 판정, 더 정확히는 자식 프로세스 트리
    검사. (이건 §"원격 SSH 머신 이름 색" 의 *클라이언트 호스트* 판정과는 다른, **패널 내부에서
    원격 접속 중인지**의 판정임 — 혼동 주의.)
  - **명령**: `choose-tree`/`tree`/`overview` 류 명령을 `COMMANDS` 에 노출(이미 트리 선택기를
    여는 경로가 있으면 그 명령을 확장).
- **[요청·미구현] pytmux 중첩 실행 거부(로컬·원격 공통)** — pytmux 패널 안에서 **다시
  pytmux 를 실행하면 거부**해야 한다. 특히 pytmux 로 **원격 서버에 접속한 뒤 그쪽에서도
  pytmux 를 실행하려 하면 중단**시킨다. 현재 패널 셸에는 `$PYTMUX`(소켓 경로)가 심어지지만
  (`server.py` ~48), `launcher.main`(launcher.py ~154-156)의 기본 동작(attach/기동)이 **이를
  검사하지 않아** 중첩 실행이 막히지 않는다(README 의 SSH 자동 attach 가드만 `$PYTMUX` 를
  본다). 구현 방향:
  - **로컬 중첩**: `main`(및 `attach` 서브커맨드) 시작에서 `$PYTMUX` 가 설정돼 있으면
    **에러 메시지 출력 후 비정상 종료**(tmux 의 "sessions should be nested with care; unset
    $TMUX to force" 식). 강제 플래그(예: `--force`)나 환경 해제로만 우회 허용할지 결정.
  - **원격 중첩**: ssh 로 원격에 들어가면 `$PYTMUX` 가 기본적으로 **전파되지 않는다**
    (SendEnv/AcceptEnv 미설정). 따라서 원격 셸은 자신이 pytmux 패널 안에서 떠 있는지 모른다.
    pytmux 가 패널에서 ssh 를 띄울 때 **중첩 표식을 원격으로 전달**해야 함 — 예: ssh 명령에
    `SetEnv PYTMUX=...`(OpenSSH 7.8+) 주입, 또는 원격에서도 인식 가능한 마커 사용. 이게 있어야
    원격 pytmux 가 `$PYTMUX` 검사로 중첩을 거부할 수 있다.
  - **주의**: 정상 attach(서버↔클라이언트)는 막으면 안 되고, **새 서버를 패널 안에서 새로
    기동/attach 하려는 경우만** 거부 대상. `$PYTMUX` 는 패널 셸에만 있으므로 클라이언트
    프로세스 자체 env 와 혼동하지 말 것.
- **[요청·미구현] 닫기 확인 팝업을 "pytmux 종료 여부"로 구분(메시지+하이라이트색)** — 탭을
  닫을 때 확인 팝업(`ConfirmScreen`)이 뜨는데, **① 남은 탭이 있어 pytmux 가 안 끝나는 경우**와
  **② 마지막 탭/패널이라 닫으면 pytmux 가 종료되는 경우**를 **메시지와 강조(하이라이트) 색상으로
  구분**한다. 현재 `confirm_kill_tab`(client.py ~1381)은 두 경우 모두 같은 문구
  ("이 탭을 닫을까요? 탭의 셸이 종료됩니다.")를 쓰고, `ConfirmScreen`(~555)의 선택 버튼 강조는
  `$accent` 한 색뿐이라 구분이 없다. 구현 방향:
  - **종료 여부 판정(클라이언트)**: 이 닫기로 pytmux 가 끝나는가 = 탭이 1개뿐이고(`self.windows`
    길이) 그 탭을 닫는 경우(또는 마지막 패널까지 닫히는 경우). `confirm_kill_tab` 에서
    `len(self.windows) <= 1` 이면 "종료" 케이스로 분기.
  - **메시지 분기**: 종료 케이스는 "이 탭을 닫으면 pytmux 가 종료됩니다" 식 경고 문구로, 일반
    케이스는 기존 문구 유지.
  - **하이라이트 색 분기**: `ConfirmScreen` 에 위험도/강조색 파라미터 추가(예: `danger=True`
    → `.sel` 강조를 `$accent` 대신 `$error`(붉은색)로). 현재 `.sel` CSS 는 `$accent` 하드코딩
    이라 색을 주입 가능하게 바꿔야 함.
  - **패널 닫기 경로도**: kill-pane 확인(현재 별도 prompt, ~2359)도 마지막 패널이면 같은
    종료-경고로 통일 고려.
- **[요청·미구현] 컨텍스트 메뉴가 뜰 때 대상 패널을 배경에서 구분 표시** — Ctrl+클릭(우클릭)
  하면 현재 패널의 컨텍스트 메뉴(`MenuScreen`)가 **화면 중앙**에 뜨는데, 한 탭에 패널이 여럿이면
  **어느 패널을 대상으로 한 메뉴인지 알기 어렵다**. 요청: 메뉴가 떠 있는 동안 **대상 패널을
  나머지 패널과 구분**되게(배경에서) 표시한다. 구현 방향:
  - **대상 패널 추적**: `open_menu`(client.py ~1890)는 활성 패널을 대상으로 동작한다(메뉴
    액션이 활성 패널에 send_cmd). 우클릭 진입(`on_mouse_down` button==3, ~836)은 현재 커서
    아래 패널을 **선택하지 않으므로**, 우클릭 시 그 패널을 먼저 `select_pane_id` 하거나
    메뉴 대상 패널 id 를 명시적으로 보관(`self._menu_pane`).
  - **배경 강조**: 메뉴가 열린 동안 `_composite` 에서 **대상 패널만 평소대로, 나머지 패널은
    흐리게(dim)** 그리거나 대상 패널 테두리를 강조색으로. clock-mode 의 dim 패턴
    (`_draw_clock_overlay` ~1568-1572, 뒤 패널에 `Style(dim=True)` 합성)을 그대로 재사용.
    `MenuScreen` 은 별도 ModalScreen 이라 그 아래 MultiplexerView 배경이 계속 보이므로,
    메뉴 열림 상태 플래그를 두고 합성에 반영 → 닫힐 때 해제·재합성.
  - **주의**: MenuScreen 은 중앙 고정(`align: center middle`)이라 위치로는 패널을 가리킬 수
    없으니 배경 강조가 핵심. 좌표를 패널 근처로 옮기는 대안도 있으나(팝업을 대상 패널 위에
    배치) 요청은 "배경에서 구분"이므로 dim/하이라이트 우선.
- **[요청·미구현] 토큰 사용량 표시 클릭 → Claude 실행 중 탭/패널 트리 + 세션별 토큰 팝업** —
  화면 오른쪽 아래(REC 옆) **토큰 사용량 표시를 클릭하면**, 현재 **Claude Code 를 실행 중인 모든
  탭/패널을 트리 형태**로 보여주고 **각 세션이 토큰을 얼마나 쓰는지 한 화면에서** 확인하는
  팝업을 띄운다. 구현 방향:
  - **클릭 존**: StatusBar 의 `claude_usage` 세그먼트(client.py ~1101-1104)에 클릭 영역
    (`_usage_zone`)을 등록(시계 `_clock_zone` 패턴). `on_mouse_down` 에서 이 영역이면 팝업.
  - **데이터(서버)**: 패널마다 `_claude`(상태)·`_claude_usage`(사용량)가 이미 있고
    `panes_claude` 로 내려간다. **Claude 가 떠 있는 패널만 추려** 탭→패널 트리로 묶고 각 패널의
    토큰 사용량을 함께 전달. (현재 `claude_usage` 는 활성 패널 1개분만 상태줄에 노출 →
    모든 Claude 패널분을 모아야 함.) **세션별 누적 토큰**은 위 "상태줄 토큰 사용량을 세션
    누적" 항목의 누적값(`_claude_usage_total`)을 그대로 쓰면 의미가 맞다(스트리밍 델타 주의).
  - **팝업**: 탭/패널 트리 + 패널별 토큰을 한 화면에 보이는 모달(위 "전체 탭/패널/실행앱 개요"
    트리 팝업과 같은 위젯을 재사용하되 **Claude 패널만 필터**하고 토큰 열 추가). 전환·종료까지
    얹을지는 그 항목과 통합 시 결정.
  - **연관**: [전체 탭/패널/실행앱 개요 팝업], [상태줄 토큰 사용량 세션 누적] 두 항목과 묶어
    구현하면 트리·사용량 인프라를 공유한다.
- **[요청·미구현] 토큰 사용량 로깅(탭/패널/세션별) + 시간·일·월 단위 조회 화면** — Claude Code
  화면에서 읽은 토큰 사용량을 **탭별·패널별·세션별 로그로 영속 기록**하고, **시간 단위/날짜
  단위/월 단위로 집계 조회**하는 화면을 만든다. **명령어로 팝업을 열어** 조회할 수 있어야 하고,
  Claude Code 가 실행될 때 화면을 읽어 **사용량을 모니터링·세션 단위로 기록해 나중에 합산**할
  수 있어야 한다. 구현 방향:
  - **모니터링·기록(서버)**: 이미 flush 루프가 패널 화면에서 `claude_usage`(protocol)를 매
    프레임 추출한다(server.py ~1337). 이를 **타임스탬프 + tab id + pane id + 세션 id** 와 함께
    **영속 로그**(예: `<sock>.tokens.jsonl` — 캡처 로그 `<sock>.capture/` 와 같은 디스크
    영속 패턴, `opts.json` 영속처럼)로 append. 세션 id 는 `claude_state` 의 None→비None
    전이로 새 세션을 끊어 부여(§"토큰 세션 누적" 의 경계 판정 공유).
  - **합산 단위 정의(난점)**: 화면의 "↑/↓ N tokens" 는 **스트리밍 델타**라 단순 합산 시
    중복(§"토큰 세션 누적" 의 주의와 동일). "세션 단위로 기록해 나중에 합산"하려면 **세션당
    최종 누계 1건**을 확정 기록하거나, 델타를 정확히 더하는 규칙을 먼저 정해야 한다.
  - **조회 화면(클라이언트)**: 명령(`token-usage`/`tokens` 류, `COMMANDS` 노출)으로 팝업을 열어
    로그를 읽고 **시간/일/월 버킷으로 집계**해 탭·패널·세션별로 표시(기간 전환 UI). 기존 모달
    (`InfoScreen`/트리 위젯) 확장 또는 신규. 큰 로그는 증분 읽기/롤오버 고려.
  - **연관**: [상태줄 토큰 사용량 세션 누적](누적값 정의), [토큰 사용량 클릭 → Claude 트리
    팝업](실시간 현재값 트리) 과 데이터 소스를 공유 — 이 항목은 그 **영속 이력/집계** 버전.
- **[요청·미구현] Claude 패널 컨텍스트 메뉴에 '프롬프트 단위 클리어' 모드 토글** —
  Claude Code 패널의 컨텍스트 메뉴(우클릭/`&` 메뉴)에서 **'프롬프트 단위 클리어' 모드**를
  켜고 끈다(패널별 토글, 기본 off). **끄면 평소와 똑같이** 동작한다. **켜면** 그 패널에서
  **프롬프트 하나의 진행이 완료(busy→idle 전이)될 때마다**, 다음 사용자 명령을 그대로
  보내기 전에 자동으로 ① "현재 세션에서 얻은 정보를 문서에 기록" 지시, ② `/clear` 를
  순서대로 수행한 뒤 ③ 큐에 있던 다음 명령을 보낸다. 목적: 긴 작업을 프롬프트 단위로
  잘라 매번 학습을 문서화하고 컨텍스트를 비워(토큰 절약·드리프트 방지) 진행.
  구현 방향:
  - **상태(서버, 패널별)**: `Pane` 에 `prompt_clear_mode`(bool) 추가(autoresume/bracketed
    와 같은 패널 플래그 자리). 영속이 필요하면 `<sock>.opts.json` 패턴 사용(capture 처럼).
  - **완료 감지**: 토큰 리밋 자동재개(`auto-resume`)·`claude_state` 와 **같은 busy→idle
    전이**를 트리거로 쓴다. flush 루프(server.py ~1337)가 매 프레임 `claude_state` 를
    계산하므로, 패널별 **직전 상태**를 들고 있다가 `busy→idle` 가 되는 순간을 잡는다
    (`_maybe_schedule_resume` 가 리밋 문구로 입력을 주입하는 것과 동일한 주입 경로 재사용).
  - **시퀀스 실행(난점)**: idle 전이 후 ①문서화 지시문 입력→Enter, 그 프롬프트가 다시
    busy→idle 로 끝나길 기다렸다가 ②`/clear` 입력→Enter, 또 idle 을 기다렸다가 ③다음
    명령. 즉 **단발 주입이 아니라 idle 을 기다리며 단계 전진하는 소형 상태기계**가 필요하다
    (각 단계 사이에 idle 확인. 무한대기 방지 타임아웃·취소 고려). ①의 "문서에 기록"
    지시문 문구는 설정 가능하게(autoresume 의 `resume_msg`/`auto-resume-message` 처럼
    `prompt-clear-message` 옵션).
  - **큐**: 모드가 켜진 동안 사용자가 보낸 다음 명령들을 **바로 패널에 흘리지 말고 큐에
    쌓아**, ①②가 끝난 뒤 하나씩 투입. 사용자가 직접 친 입력과 자동 주입을 구분해야 한다
    (프롬프트 추적 `_track_prompt`·`_write_paste` 경로와 충돌 안 나게).
  - **UI**: 컨텍스트 메뉴 항목 추가(`client.py::MENU_ITEMS`, autoresume 토글 항목과 나란히)
    + 명령(`prompt-clear [on|off]`, `COMMANDS` 노출). 상태줄/탭 아이콘에 모드 표시 여지.
  - **주의**: "/clear" 와 "문서 기록"은 **Claude Code 슬래시 명령/지시문**이라 pytmux 가
    아니라 패널 안 Claude 에게 보내는 입력이다(헷갈리지 말 것). 문서화 지시가 실제로 무엇을
    어디에 기록할지는 Claude 쪽 프로젝트 관례(CLAUDE.md/메모리)에 맡긴다.
- **[요청·미구현] 비활성 탭의 Claude 작업 완료를 상단 탭 배경색(옅게)으로 알림(보면 사라짐)** —
  다른 탭을 보고 있을 때, **비활성(현재 안 보는) 탭의 Claude Code 패널** 중 하나 이상이 **작업을
  마치고 대기 상태(busy→idle)**가 되면 **상단 탭바에서 그 탭의 배경색을 옅게** 바꿔 "완료된 작업이
  있는 Claude 패널이 있음"을 알린다. 그 **탭으로 전환하면 읽은 것으로 처리**되어 표시가 사라진다.
  이는 기존 **활동(`#`)·벨(`!`) 모니터링과 같은 "비활성 탭에 표시 → 보면 해제"** 패턴의 Claude 완료
  버전이다(활성 탭에서 끝나는 건 사용자가 보고 있으니 표시 안 함). 기존 인프라(확장 대상):
  - **탭 플래그(모델)**: `Tab` 에 `has_activity`/`has_bell` 처럼 **`has_claude_done`(bool)** 추가
    (model.py ~595-598, `monitor_*` 옆). 모니터 토글이 필요하면 `monitor_bell` 패턴으로
    `monitor_claude`(기본 on) 도 둘 수 있음.
  - **완료 전이 감지(서버 flush 루프)**: flush 루프가 매 프레임 패널 화면에서 `claude_state` 를
    계산하고 `new_cl != p._claude` 비교 후 `p._claude=new_cl` 로 갱신한다(server.py ~1353-1358).
    그 **직전 값(`p._claude`)이 busy 이고 새 값(`new_cl`)이 idle** 이면 **busy→idle 전이**다.
    바로 아래 활동/벨 처리 블록(~1359-1375)과 같은 자리에서, **그 패널이 비활성 탭(`w is not win`)
    소속이면** `t.has_claude_done = True; status_changed = True`. (활성 탭 패널은 activity/bell
    처럼 매 프레임 클리어 — ~1363-1365 와 동일 정책.)
  - **읽음 처리(보면 해제)**: `select_window`(server.py ~360-368)에서 `t.has_activity =
    t.has_bell = False` 와 **같은 줄에 `t.has_claude_done = False`** 추가. flush 루프에서 활성
    탭은 매 프레임 클리어되므로 전환 즉시 사라진다.
  - **상태 메시지**: `_status_msg`(server.py ~1278-1282)의 윈도우 dict 에 **`claude_done` 필드**
    추가(`bell`/`activity` 와 나란히). 클라이언트 `TabBar.set_tabs` 로 흘러간다(client.py ~1007).
  - **렌더 강조(클라이언트)**: `TabBar.render_line` 의 스타일 선택(client.py ~1078-1083)에서
    **비활성·비선택 탭이고 `claude_done` 이면 `base` 대신 옅은 배경 스타일**로 그린다 — 활성
    탭(`active_st`=primary, 진한 파랑)보다 **옅게**(예: `success`/`primary` 를 dim 또는 낮은
    채도 배경으로 블렌드). 선택(`sel_st`)·활성(`active_st`) 우선순위는 유지. `_labels` 의 idle
    아이콘(`○`)·`#`/`!` flag 와는 **독립**(배경색은 추가 신호).
  - **주의/난점**: ① 완료 판정이 `claude_state` 휴리스틱(§6)에 의존하므로, **스피너 깜빡임으로
    busy↔idle 가 떨리면 거짓 완료**가 켜질 수 있다 — 잠깐 idle 후 곧 busy 면 무시하도록 **연속
    N프레임 idle 확인/짧은 디바운스**를 고려. ② **limit(리밋 멈춤, ⊘)** 은 "작업을 마치고 대기"가
    아니므로 이 표시 대상이 아님(busy→idle 만; limit 은 기존 아이콘 유지). ③ 한 탭에 Claude 패널이
    여럿이면 **하나라도** 완료되면 켜고, 전환 시 일괄 해제(패널 단위가 아니라 탭 단위 플래그).
  - **회귀 테스트**: `test_server` 에 비활성 탭 패널을 busy→idle 로 만들고 status 메시지의
    `claude_done==True`, 그 탭 `select_window` 후 `False` 단언. `test_client` 에 `claude_done` 탭이
    옅은 배경(활성과 다른 스타일)으로 그려지는지(`render_line`/셀 스타일) 단언.
- **[요청·미구현] 활성 탭을 아래 콘텐츠와 "연결되는" 노트북 탭 모양으로** — 현재 화면 맨 위
  탭바와 그 아래 패널 영역 사이에 **줄(콘텐츠 상단 테두리)이 가로로 쭉 그어져** 있어 활성 탭이
  콘텐츠와 분리돼 보인다. 요청: **활성 탭과 그 아래 탭 패널 영역을 연결하는 모양**(노트북/폴더
  탭처럼 활성 탭 밑은 줄이 끊기고 옆으로 이어지는 형태)으로 바꿔 **활성 탭이 더 잘 보이게** 한다.
  구현 방향(탭바 ↔ 콘텐츠 상단 테두리 맞물림):
  - 현재 `TabBar.render_line`(client.py ~1025)은 활성 탭을 **배경색(`active_st`=primary)만**
    칠한다(박스 문자 없음). 콘텐츠 상단 테두리 줄은 `_composite` 의 박스 그리기(테두리 비트
    병합 ~1644-1647)가 그린다 — 이 둘이 따로라 활성 탭 밑에도 `─` 가 지나간다.
  - 연결 모양: **활성 탭의 x 범위 구간만** 콘텐츠 상단 테두리에서 `─` 를 빼고(끊고), 양 끝을
    `┘`/`└`(또는 `┐`/`┌`) 코너로 마감해 탭이 아래 영역과 이어진 것처럼 보이게 한다. 비활성 탭
    아래에는 줄을 유지. 탭 자체에 좌/우 `│` 변을 줄지(진짜 탭 모양) 여부도 함께 결정.
  - **좌표 공유 필요**: 활성 탭의 화면 x 범위를 `_composite` 가 알아야 한다 — TabBar 가 활성 탭
    zone(이미 `_zones` 에 tab 종류로 보관, ~1059)을 노출하거나, 합성 단계에서 활성 탭 위치를
    받아 상단 테두리 줄의 해당 칸만 연결 문자로 바꾼다. 스크롤(◀▶)·`[+]` 와 겹치지 않게 주의.
  - **주의**: 탭바는 높이 1(dock top)이고 콘텐츠 테두리는 별도 행이라, 두 행의 경계 문자가
    자연스럽게 이어지는지 골든 스냅샷(`replay`)으로 확인. 색도 활성=primary 로 맞춰야 연결감.
- **[요청·미구현] 탭 선택기(트리)에 각 탭/패널의 로컬/원격 구분 표시** — 탭 선택기 트리
  (`ChooseTreeScreen`, client.py ~353)에서 각 탭·패널이 **로컬에서 실행 중인지 원격(SSH) 서버
  인지** 구분되게 표시한다. 현재 트리는 `index:name (N panes)` 윈도우 수준만 보여준다. 구현
  방향: 위 "전체 탭/패널/실행앱 개요 팝업" 항목의 **로컬/원격 판정**과 동일 — 패널의 프로세스
  체인에 ssh/mosh/telnet 등 원격 세션이 있으면 원격으로 보고(서버에서 패널별 판정해 트리
  스냅샷에 `remote` 플래그 추가), 트리 항목에 로컬/원격 배지(예: `[ssh]`/색상)를 붙인다. 그
  항목과 **같은 데이터·위젯을 공유**하므로 함께 구현하면 된다(이 항목은 트리에 구분 표시를
  넣는 것에 한정한 부분 요청).
- **[요청·미구현/확인필요] Ctrl+Q 를 활성 패널로 전달(앱 종료는 detach 명령으로만)** — pytmux
  는 Ctrl+Q 로 종료되지 않는다(의도된 동작 유지). 요청: **Ctrl+Q 를 누르면 그 키 입력을 활성
  패널로 전달**하고, 앱을 끝내려면 **detach 명령**을 쓰게 한다. 현재 상태:
  - **detach 는 이미 있음** — `detach`/`detach-client` 명령(client.py ~161/224/2311), 메뉴
    항목(~161), `self.exit()`. "종료는 명령으로 detach" 요구는 이미 충족.
  - **전달 경로도 코드상 존재** — `key_to_bytes`(~128)가 `ctrl+q` 를 `\x11`(DC1/XON)로 만들고,
    normal 모드 `on_key`(~2471)가 `send_input` 으로 활성 패널에 보낸다. 그런데 **실제로
    패널에 안 가면** 원인은 두 가지가 유력: ① **Textual 프레임워크가 ctrl+q 를 먼저 가로챔**
    (기본 quit/priority 바인딩 — `BINDINGS=[]` 는 앱 자체 바인딩만 비우고 시스템 바인딩은
    남을 수 있음). ② **터미널 흐름제어(IXON)** 가 Ctrl+Q(XON)/Ctrl+S(XOFF)를 먹어 앱까지
    도달 안 함. 조사·수정 방향: on_key 진입 전에 ctrl+q 가 잡히는지 로깅 → Textual 바인딩이면
    명시적으로 해제/무력화(ESC 모드·prefix 와의 우선순위도 정의), 흐름제어면 PTY 입력단
    `termios` 의 `IXON` 해제(또는 raw 모드) 검토. ESC/F12/prefix 처럼 **pytmux 가 가로채는
    키가 아님**을 분명히 해 그냥 패널로 흘려보내는 게 목표.
  - **주의**: prefix 모드/esc 모드/스크롤 모드 중에는 기존 동선 유지(그 모드의 키로 해석),
    normal 모드에서만 패스스루.
- **[요청·미구현] ESC 모드 탭 네비게이션에 맨 오른쪽 `[+]` 새 탭 버튼도 포함** — ESC 모드에서
  방향키로 탭 사이를 오갈 때 **맨 오른쪽 `[+]`(새 탭) 버튼도 커서로 선택**할 수 있게 하고, 그
  버튼에서 **Enter 를 누르면 새 탭**이 열리게 한다. 현재 `_handle_esc_mode` 의 탭바 포커스 블록
  (client.py ~2605-2641)은 `tb.sel`(탭 **인덱스 정수**)만 다루고 ←→ 가 탭 인덱스 사이를
  순환(`(cur±1) % len(idxs)`)할 뿐 `[+]` 는 네비 대상이 아니다(현재는 `+`/`a` 문자 입력으로만
  새 탭). 구현 방향:
  - **선택 상태에 `[+]` 추가**: `tb.sel` 이 정수 인덱스만 담으므로 `[+]` 용 **센티넬**(예
    `tb.sel = "+"` 또는 별도 `tb.sel_add` 플래그)을 도입. **마지막 탭에서 → 를 누르면 첫 탭으로
    감싸지 말고 `[+]` 로 이동**, `[+]` 에서 → 는 첫 탭으로(또는 정지), ← 는 마지막 탭으로 복귀.
  - **Enter 분기**: 선택이 `[+]` 면 `select_window` 대신 `new_window` 실행(기존 `+`/`a` 경로와
    동일 명령).
  - **렌더 강조**: TabBar render(`add` zone, ~1084 의 `add_st`)에서 **bar_focus 이고 sel==`[+]`**
    이면 선택 강조 스타일(다른 선택 탭처럼 `sel_st` 계열)로 그려 커서 위치가 보이게.
  - **주의**: 스크롤(◀▶)로 `[+]` 가 화면 밖일 때도 선택되면 보이도록 스크롤 보정과
    맞물릴 것. `[+]` 는 항상 마지막 탭 오른쪽(현 위치) 유지.
- 탭 **드래그 재정렬 시 시각적 피드백**(현재는 놓을 때 확정만).
- 패널 **드래그 swap**, 단일 패널 테두리 on/off 옵션화.
- 다중 줄 상태표시줄, unbind-key, 라이브 PTY display-popup.
- `unbind`/추가 옵션 등 FEATURES 의 "미구현" 표기 항목.
