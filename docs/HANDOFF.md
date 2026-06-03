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

## 9. 최근 변경(CL 56279~56324 + git, 신→구)

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

- **[버그·미해결] 내부 마우스 TUI 앱(p4v-tui 등)에 마우스 입력이 전달 안 됨** —
  상위 디렉토리의 `p4v-tui` 처럼 **마우스를 1급으로 쓰는 TUI 앱을 패널에서 실행하면
  키보드는 패널(PTY)로 전송되지만 마우스 입력은 패널로 전달되지 않는다.** 원인:
  `client.py::MultiplexerView` 의 마우스 핸들러(`on_mouse_down/move/up/scroll`)가 모든
  마우스 이벤트를 **pytmux 자신의 용도로만 소비**(패널 포커스·경계 드래그·스크롤백·
  copy-mode 선택·우클릭 메뉴)하고 `event.stop()` 으로 끝내며, **내부 앱 PTY 로 마우스를
  되돌려 보내는 패스스루 경로가 아예 없다.** 키 입력은 `send_input`(→PTY)으로 가는데
  마우스는 그런 경로가 없는 셈. tmux 는 내부 앱이 마우스 트래킹을 켜면(DECSET
  1000/1002/1003/1006) 마우스 이벤트를 **SGR 시퀀스로 인코딩해 패널에 그대로
  전달**한다. 구현 방향: ① 서버에서 내부 앱의 마우스 모드 DECSET 을 추적(현재
  `server.py` 의 bracketed paste(2004) 추적과 동일 패턴으로 1000/1002/1003/1006/1015
  SGR 추가). ② 해당 패널이 마우스 모드를 켰으면 클라이언트가 자신의 UI 처리 대신
  (또는 경계/탭바 등 pytmux 영역이 아닐 때) 마우스 이벤트를 `CSI < b ; x ; y M/m`
  (SGR 1006) 형태로 인코딩해 `send_input` 으로 PTY 에 전달. ③ `prefix` 가 눌렸거나
  copy-mode 일 때는 pytmux 가 가로채고, 그 외엔 내부 앱 우선 — tmux 의 동작과 맞춤.
  현재는 `set mouse off` 로 꺼도 마우스가 내부 앱으로 가지 않는다(애초에 패스스루가
  없어서). 좌표 변환 시 패널 오프셋·테두리 inset(`_composite` 의 박스/내용 inset)을
  반드시 빼야 내부 앱 좌표와 맞는다.
- **[버그·미해결] 원격 SSH 환경에서 마우스 휠 위쪽 스크롤백이 동작 안 함** —
  **로컬에서는** 활성 패널에서 마우스 휠을 올리면 스크롤백(지난 출력)이 위로 잘
  스크롤되지만, **원격 SSH 환경에서 pytmux 를 쓰면 위쪽으로 스크롤되지 않는다.**
  (아래쪽은 확인 필요.) 휠 처리 경로는 `client.py::MultiplexerView.on_mouse_scroll_up`
  → `app.send_scroll(pane_id, delta=3)` 이므로, **클라이언트가 휠 이벤트 자체를 받는지**가
  관건. 로컬/원격에서만 갈리는 점으로 보아 **상위 터미널/SSH 경로에서 마우스 휠
  이벤트가 Textual 까지 도달하지 않는** 환경 문제로 의심된다. 조사 방향: ① 원격에서
  Textual 이 `MouseScrollUp` 이벤트를 실제로 받는지 로깅(안 받으면 터미널 마우스 트래킹
  미활성/SGR 1006 미협상 — 상위 터미널 설정·`$TERM`·mosh vs ssh 차이). ② 원격 상위
  터미널이 휠을 **앱에 넘기지 않고 자기 스크롤백**으로 가로채는지(Textual 이 마우스
  트래킹 DECSET 을 켰는데도 일부 터미널/세션은 위쪽 휠을 자체 처리). ③ 받긴 받는데
  `send_scroll`/서버 스크롤 적용이 원격에서만 누락되는지 — 서버 `scroll` 처리와
  패널 `scrollback` 상태를 캡처 로그로 확인. 우선 ①(이벤트 도달 여부)부터 切り分け.
- **[버그·미해결] 로컬 실행 시 Claude Code 인터페이스 글자에 원치 않는 밑줄** — 패널에서
  Claude Code 를 띄울 때, **원격(SSH/mosh)에서 실행하면 인터페이스가 정상 출력**되지만
  **로컬에서 실행하면 모든 문자에 밑줄(underline)이 그어진 채** 렌더된다. 원격/로컬에서만
  갈리는 점으로 보아 SGR 언더라인 속성(CSI 4m / 24m)의 상태 누수 또는 합성 단계
  (`client.py::_composite` 의 셀 속성 blit)에서 underline 플래그가 잘못 유지/적용되는
  것으로 의심된다. 조사 시작점: ① `<sock>.capture/pane-*.log` 를 pyte 로 재렌더해
  underline 속성이 **서버 화면 버퍼 단계에서 이미 켜져 있는지**(=내부 앱/터미널 환경
  차이) vs **클라이언트 합성 단계에서 덧붙는지** 切り分け. ② 로컬 vs 원격에서 다른
  `$TERM`·터미널 에뮬레이터·polyfill 여부 확인(밑줄이 환경 의존이면 서버가 받는 raw
  SGR 자체가 다를 수 있음). ③ pyte 셀의 `underscore` 속성이 합성 시 Textual 스타일로
  옮겨지는 경로 점검.
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
- 탭 **드래그 재정렬 시 시각적 피드백**(현재는 놓을 때 확정만).
- 패널 **드래그 swap**, 단일 패널 테두리 on/off 옵션화.
- 다중 줄 상태표시줄, unbind-key, 라이브 PTY display-popup.
- `unbind`/추가 옵션 등 FEATURES 의 "미구현" 표기 항목.
