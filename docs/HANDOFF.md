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
- **상태**: `docs/FEATURES.md` 의 모든 항목 구현. 헤드리스 테스트 **135 passed**
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
  탭바는 **왼쪽 1칸 여백(`TabBar.LEAD`)** 으로 첫 탭이 한 칸 오른쪽에서 시작하고,
  `[+]` 새 탭은 **마지막 탭 오른쪽에 한 칸 더 띄워**(앞 공백 2칸) 나타난다.
  탭 닫기 `[x]` 는 **콘텐츠 영역 오른쪽 위
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

## 9. 최근 변경(CL 56279~56500 + git, 신→구)

> ✅ **git 미러 동기화 완료(2026-06-04, macOS 세션).** Windows 박스(`office`)와
> `surface-office` 병행 세션에서 낸 CL **56540~56560** 을 macOS 개발 머신에서
> depot → git 으로 재생해 **GitHub 미러(origin main)에 반영**했다(CL 1개=커밋 1개,
> 각 CL 의 리비전 시점 내용을 `p4 print` 로 복원, 메시지에 `Perforce: change NNNN`
> 푸터). 작업 트리는 depot head 와 바이트 단위 일치(`p4 diff -sa` 비어 있음),
> `origin/main` 과 동기. 미러 CL: 56540, 56541, 56543, 56544, 56545, 56546, 56547,
> 56548, 56549, 56550, 56551, 56552, 56555, 56556, 56558, 56560. (`.claude/
> settings.local.json` 은 전역 gitignore 로 제외 — p4 추적 스킬 파일만 미러.) 본
> 동기화 메모를 반영한 이 CL 자체도 제출 직후 동일 동선으로 미러한다.

- 56742 **테스트 격리: 캡처(REC) 디렉터리를 `PYTMUX_CAPTURE_DIR` 임시로 주입**(테스트
  인프라 버그) — `harness.server_only` 의 테스트 엔드포인트 `tcp:127.0.0.1:0` 이
  `ipc.default_endpoint()` 와 같아 `server.capture_dir` 가 **공유 프로젝트 `captures/default`**
  를 가리켰다. 그 결과 **실사용 pytmux 데몬이 같은 파일을 캡처 중일 때** `test_capture_output`
  이 17MB 짜리 실제 세션 로그를 읽어 깨지고(이번 세션에서 실제 발생), 거대 파일을 읽다
  전체 스위트가 멈추기도 했다. `server_only` 가 매 서버마다 `PYTMUX_CAPTURE_DIR` 를
  유니크 임시 디렉터리로 주입(`capture_dir` 가 이 override 우선)해 캡처를 격리하고,
  `teardown` 이 그 env 를 해제(비-override 동작을 검증하는 `test_capture_dir_project_and_override`
  에 안 새게)한다. 실사용 `captures/` 오염도 방지. 231 passed. 파일: `tests/harness.py`,
  `docs/HANDOFF.md`.
- 56741 **Claude CLI shift+방향키 텍스트 편집 — 전달 검증 + 문서**(§10-A #5) — pytmux
  가 `Shift+Home/End/방향키` 를 표준 xterm `CSI 1;2 X` 로 활성 패널에 손실 없이 전달함을
  코드 경로(`on_key`→`key_to_bytes`)로 확인하고 헤드리스 회귀
  `test_shift_nav_keys_forwarded_to_panel`(6키 시퀀스·미가로채기)로 고정. 선택/편집의
  실효는 앱 해석 의존임을 명시하고, 바이트 시퀀스 표·전달 경로·라이브 수동 체크리스트·
  제약을 신규 `docs/CLAUDE_TEXT_EDITING.md` 로 작성. 231 passed. 클라이언트 전용.
  파일: `docs/CLAUDE_TEXT_EDITING.md`(신규), `tests/test_client.py`, `docs/HANDOFF.md`.
- 56731 **팝업 배경 디밍 즉시 적용(지연 제거) + `_darken_style` 캐시**(§10-A #4) —
  ① `push_screen`/`pop_screen` 이 같은 턴에 `_composite()` 를 즉시 호출해 dim 이 다음
  refresh/clock tick(최악 1초)을 기다리지 않게 했다(call_after_refresh 는 마운트 후
  안정화용으로 유지). ② 전 화면 셀 dim 비용을 줄이려 `_darken_style` 에 `@lru_cache(8192)`
  — 같은 스타일은 블렌드를 한 번만 계산(대부분 셀이 동일 스타일). 회귀
  `test_popup_dim_synchronous_and_cached`(캐시 동일객체 + push 직후 즉시 _composite),
  230 passed. 클라이언트 전용(attach 재실행). 파일: `pytmuxlib/{client,clientutil}.py`,
  `tests/test_client.py`, `docs/HANDOFF.md`.
- 56726 **상태줄 통합 팝업에 '서버' 탭 + 서버이름 클릭존**(§10-A #12) — REC·토큰이
  이미 통합돼 있던 `InfoTabsScreen` 에 세 번째 **서버 탭**(`_server_info_lines`:
  호스트·로컬/원격·소켓·RTT·degraded·재접속 안내)을 추가하고, 상태줄 host 런에
  `_host_zone` 클릭존을 등록해 클릭 시 `show_status_tabs(initial=2)` 로 서버 탭을 연다
  (REC→0·토큰→1·서버→2). 회귀 `test_status_tabs_has_server_tab` +
  `test_status_host_click_opens_server_tab`, 229 passed. 클라이언트 전용(attach 재실행).
  파일: `pytmuxlib/{client,clientwidgets}.py`, `tests/test_client.py`, `docs/HANDOFF.md`.
- 56724 **프롬프트 히스토리 긴 URL 하드 줄바꿈 + 구분선/↓→[h] 점프**(§10-A #7·#8)
  — `InfoScreen` 에 ① `wrap_hang` 모드: 마운트 후 박스 실폭을 재서 `_hangwrap`(모듈
  헬퍼)로 각 줄을 행잉-인덴트 하드 줄바꿈('NN. ' 접두사 폭만큼 이어줄 들여쓰기 →
  번호 정렬 보존, 긴 URL 은 글자 단위 컷)하고 목록 재구성(`_rewrap`), initial_index 도
  매핑. ② nav 가 구분선/빈 줄을 건너뜀(`_skip`/`_skip_over`). `open_prompt_history` 가
  마지막 프롬프트와 `[h]` footer 사이에 구분선(`─`×24)을 넣고 `wrap_hang=True` 로 연다
  → 마지막 항목서 ↓ 한 번에 `[h]` 로 점프. 회귀: `test_hangwrap_preserves_number_alignment`
  + `test_prompt_history_down_jumps_to_h_over_divider`, 227 passed. 클라이언트 전용
  (attach 재실행). 파일: `pytmuxlib/{client,clientscreens}.py`, `tests/test_client.py`,
  `docs/HANDOFF.md`.
- 56722 **클립보드 이미지 붙여넣기 — PNG 저장 후 경로 주입(결정 ①) + Alt+V 폴백**
  (§10-A #11) — `paste-clipboard`(Ctrl+V)가 텍스트 우선, 텍스트 없고 이미지면 신규
  `_clipboard_save_image`(Windows .NET `Clipboard.GetImage().Save` / macOS `pngpaste`
  / Linux `xclip`·`wl-paste` image/png)로 임시 PNG 저장 후 **경로 문자열을 paste**(앱이
  첨부 이미지로 인식), 저장 실패 시 Alt+V(ESC v) 공유-클립보드 폴백. 로컬(클라=서버)
  가정. 회귀 `test_paste_clipboard_text_image_and_fallback`(텍스트/경로/폴백 3분기),
  225 passed. 클라이언트 전용(attach 재실행). 파일: `pytmuxlib/client.py`,
  `tests/test_client.py`, `docs/HANDOFF.md`.
- 56721 **토큰 사용량 팝업 하단 구분선·전 세션 합계 + InfoTabsScreen 하단 닫기 버튼**
  (§10-A #6) — ① `client._usage_tree_lines` 끝에 가로 구분선(`─`×36)과 전체 세션 토큰
  합계(`전체 세션 합계 — Σ N`, 모든 claude 패널 tokens 합) 한 줄 추가(통합 상태 팝업
  토큰 탭·독립 사용량 트리 팝업 공통). ② `InfoTabsScreen` 에 하단 닫기 버튼(`#itclosebtn`,
  목록 아래 한 줄·가로 가득·가운데, 클릭/터치 닫힘) 추가. 회귀:
  `test_status_tabs_popup_merged` 에 합계·구분선·하단버튼 단언 추가 + 신규
  `test_info_tabs_bottom_close_button`, 224 passed. 클라이언트 전용(attach 재실행).
  파일: `pytmuxlib/{client,clientscreens}.py`, `tests/test_client.py`, `docs/HANDOFF.md`.
- 56720 **큰 달력 두 자리 숫자 사이 간격 좁힘(DIG 2→1)**(§10-A #9) — `client.
  _draw_calendar_overlay` 의 큰 달력(시계 3×5 폰트) 경로에서 한 날짜의 두 자리 숫자
  사이 간격 `DIG` 를 2→1 로 줄여 두 자리가 한 덩어리로 읽히게 했다. 날짜칸 사이
  간격(`DGAP`/`DCW=8`)은 유지(날짜끼리는 안 붙음). 회귀 `test_big_calendar_digit_spacing`
  (큰 패널 90×46 에서 10일 글리프 두 블록 사이 빈 칸이 정확히 1칸인지 셀 단위 측정),
  223 passed. 클라이언트 전용(attach 재실행). 파일: `pytmuxlib/client.py`,
  `tests/test_client.py`, `docs/HANDOFF.md`.
- 56719 **open/close-clock·calendar 명령(멱등 켜기/끄기)**(§10-A #10) — 시계/달력
  오버레이를 토글이 아니라 명시적으로 켜고(`open-clock`/`open-calendar`) 끄는
  (`close-clock`/`close-calendar`) 명령. 활성 패널 대상, 멱등(이미 원하는 상태면 그대로),
  한 패널엔 한 오버레이만(open 시 반대쪽 닫음). 신규 `set_clock`/`set_calendar` 헬퍼
  + `_run_command` 디스패치, `clientutil.COMMANDS`/`COMMAND_NOARG` 등록. 회귀
  `test_open_close_clock_calendar_commands`(멱등·상호배타·close 안전), 222 passed.
  클라이언트 전용(attach 재실행). 파일: `pytmuxlib/{client,clientutil}.py`,
  `tests/test_client.py`, `docs/HANDOFF.md`.
- 56718 **권한모드 팝업 좌측 정렬·footer 바로 위 배치 + 바깥 클릭 닫기**(§10-A #2·#3)
  — Claude footer(`auto mode on …`) 클릭으로 여는 `PermModeScreen` 을 ① **좌측 정렬**
  하고(`align: left top`, `open_perm_mode` 가 `_perm_zone[pid]` 의 시작 x 를 `anchor_x`
  로 넘겨 `on_mount` 가 박스 offset.x 를 그 footer 시작 x 에 맞춤 — 화면 오른쪽 넘으면
  클램프, 앵커 없으면 가로 중앙), ② 세로는 기존대로 클릭 줄 **바로 위**(공간 없으면 아래),
  ③ **박스(`#perm`) 바깥(백드롭) 클릭 시 닫힘**(`on_click` 이 조상 체인에 `#perm` 없으면
  `dismiss(None)` — InfoScreen 의 inside-box 판정 패턴). 회귀: 기존
  `test_claude_footer_zones_and_popups` 에 offset.x=anchor 단언 추가 + 신규
  `test_perm_mode_click_outside_closes`(안 클릭 유지·바깥 클릭 닫힘), 221 passed.
  클라이언트 전용(attach 재실행). 파일: `pytmuxlib/{client,clientscreens}.py`,
  `tests/test_client.py`, `docs/HANDOFF.md`.
- 56632 **ESC 모드 종료 시 ESC 가 패널로 새지 않게 — 패널 ESC 전달은 Shift+ESC 만**
  (§10 사용자 요청) — 단독 ESC 로 esc(명령) 모드 진입 후 **ESC 를 다시 눌러 모드를
  빠져나올 때 그 ESC 가 활성 패널로 전달되던** 동작을 없앴다. 패널(앱)에 실제
  ESC(`\x1b`)를 보내는 통로는 **Shift+ESC 일 때만**이어야 한다는 요청. `_handle_esc_mode`
  의 `k=="escape"` 분기에서 `self.send_input(b"\x1b")` 를 **제거**하고 `self._exit_esc()`
  만 남겼다 — 즉 "ESC 더블탭 → 앱에 ESC 1회"(CL 56572 (a)) 통로를 **폐지**. 앱에 ESC 가
  필요하면 **Shift+ESC 패스스루**(`SPECIAL["shift+escape"]=b"\x1b"`) 또는 **`send-escape`
  명령/전용 바인딩**(CL 56572 (b))을 쓴다 — 56572 가 막으려던 환경(conhost/일부 WT·ssh
  의 Shift 수식 미인코딩)에도 대체 통로가 남아 완전 회귀는 아니다. 회귀
  `test_double_escape_sends_esc_to_pane`(전달 동작 고정)를 폐지하고
  `test_double_escape_exits_mode_without_pane_esc`(더블탭=전달 없이 모드 종료)+
  `test_shift_escape_sends_esc_to_pane`(Shift+ESC 만 `\x1b`)로 교체, 191 passed.
  클라이언트 전용(attach 재실행). 파일: `pytmuxlib/client.py`, `tests/test_client.py`,
  `docs/HANDOFF.md`.
- 56621 **탭-콘텐츠 연결부 `▀` 글리프 → 활성색 배경 블록**(§10 사용자 보고: 모바일서
  탭 깨짐) — 활성 탭↔콘텐츠 연결부(노트북 탭, #23)를 위쪽 절반 블록 `▀`(U+2580)로
  그렸는데 일부 모바일 폰트가 칸 사이를 벌려 렌더해 파선처럼 **깨져 보였다**(렌더
  그리드 `┌▀▀▀──┐` 자체는 정상 — 폰트 글리프 문제). 글리프 의존을 없애 **공백+활성
  배경색**(bgcolor=primary)으로 칠해 어떤 폰트에서도 솔리드 바로 잇게 했다. 회귀 2종
  갱신, 193 passed. 클라 전용(attach 재실행). 파일: `pytmuxlib/client.py`,
  `tests/test_client.py`, `docs/HANDOFF.md`.
- 56616 **refactor(§10 LLM 친화, 4/N): 위젯 3종 → `clientwidgets.py`** — 클로저의
  `MultiplexerView`(패널 합성 뷰·마우스)·`TabBar`·`StatusBar` 853줄을 신규
  `pytmuxlib/clientwidgets.py` 로 이전. config/sock_path 미캡처·Screen/App/서로 미참조
  (self.app 런타임). 의존성 textual·clientutil·datetime·os/socket 뿐. PytmuxApp 이
  import 해 compose. **client.py 3188→2335줄(최초 4363 대비 −46%)**, 누적 분리
  clientutil(356)/clientscreens(868)/clientwidgets(882). 193 passed(ptyshot 실제 클라
  시각 회귀 포함). 다음: server.py 분할 검토. 파일: `pytmuxlib/{client,clientwidgets}.py`,
  `docs/HANDOFF.md`.
- 56615 **실제 화면 스크린샷 테스트 하네스(`ptyshot`) + 진짜 클라 시각 회귀**(§10
  사용자 질문) — 헤드리스 외에 실제 화면을 자동 테스트하는 수단. 신규 `tests/ptyshot.py`
  가 진짜 Textual 클라이언트를 가짜 터미널(stdlib `pty`) 아래 띄워 **사용자가 보는
  ANSI 화면**을 캡처한다(`capture`→(raw, alive), `screen_text`, `has_traceback`).
  헤드리스 단언(위젯/셀)과 달리 실제 `_composite`/CSS/드라이버 렌더를 통과한 출력을
  잡아 트레이스백·테두리·프롬프트 유무를 검증 — driver.py(서버 합성)와 상보적, POSIX
  전용(Windows skip). `tests/test_ptyshot.py`: ANSI 제거·트레이스백 감지 + **실제
  클라가 PTY 아래 렌더·생존·무트레이스백·테두리** 시각 회귀(부팅 layout.json 복원
  경로=CL 56607 크래시도 통과). 임시 소켓 데몬 격리·정리. 193 passed. 파일:
  `tests/{ptyshot,test_ptyshot}.py`(신규), `docs/HANDOFF.md`.
- 56614 **하드닝: Session 복원 일반화(크래시 재발 방지) + Windows 콘솔 창 팝업 방지**
  (§10 사용자 요청) — ① **크래시 재발 방지**: `Session.restored(name, tabs, …)`
  클래스메서드 신설 — 직렬화 복원 경로(`restore_layout`·`restore_resume_state`)가
  `__init__` 을 우회(`__new__`)할 때 휘발성 속성을 **한 곳에서 빠짐없이** 채운다.
  CL 56607 의 popup 누락 같은 부류를 구조적으로 차단(앞으로 Session 휘발성 속성
  추가 시 restored 한 곳만 갱신). 두 복원 사이트를 통일. ② **Windows 콘솔 창 팝업
  방지**: `proc.no_window_kwargs()`(CREATE_NO_WINDOW, POSIX no-op)를 모든 콘솔 명령
  subprocess 에 적용 — 클립보드(clip.exe / **PowerShell Get-Clipboard**), run-shell·
  if-shell·display-popup(cmd /c), pipe-pane(Popen), `is_alive`(tasklist)/`terminate`
  (taskkill), 서버 fg/cwd 감지(lsof/ps). 사용자 보고 "딸려 뜨는 PowerShell 창" 해소.
  회귀 `test_no_window_kwargs` + `test_restore_layout_session_has_popup`(restored 경유),
  191 passed. **서버+클라 — kill-server 재기동.** 파일: `pytmuxlib/{model,server,client,
  proc}.py`, `tests/test_server.py`, `docs/HANDOFF.md`.
- 56611 **refactor(§10 LLM 친화, 3/N): 모달 Screen 클래스 11종 → `clientscreens.py`**
  — 클로저의 자족적 `ModalScreen` 11종(CommandList/CommandOptions/Menu/ChooseTree/
  Info/TokenLog/Prompt/Confirm/ChooseBuffer/ChooseLayout/PermMode) 844줄을 신규
  `pytmuxlib/clientscreens.py` 로 이전. config/sock_path 미캡처·다른 위젯 미참조(AST/grep
  확인), 의존성은 textual·clientutil(COMMANDS·MENU_ITEMS·MENU_TOGGLES)·usagelog·datetime
  뿐. client.py 가 11종을 import 해 push_screen. client.py 4032→3188줄, 신규 clientscreens
  868줄. 190 passed + 실제 클라 스모크. 다음: server.py 분할. 파일: `pytmuxlib/{client,
  clientscreens}.py`, `docs/HANDOFF.md`.
- 56610 **refactor(§10 LLM 친화, 2/N): client.py 클로저 프리앰블 헬퍼/상수 →
  `clientutil.py`** — 1단계(56606)에 이어 `build_client_app` 클로저 맨 앞의 순수
  헬퍼/상수 264줄(`DEFAULT_STYLE`·`theme_color`·`_darken_style`·`make_style`·`SPECIAL`
  ·`key_to_bytes`·`MENU_ITEMS`·`MENU_TOGGLES`·`COMMANDS`·`COMPLETIONS`·`_ONOFF`·
  `COMMAND_OPTIONS`·`COMMAND_NOARG`)을 `clientutil.py` 로 이전. 모두 클로저 상태
  미캡처(스타일 해석·키→바이트·명령/메뉴 테이블). client.py 가 `from .clientutil
  import …` 로 되가져와 참조(동작 불변). client.py 4287→4032줄, clientutil 86→356줄.
  190 passed + 실제 클라 스모크. 클라 전용. 다음: 모달 Screen 클래스 분리. 파일:
  `pytmuxlib/{client,clientutil}.py`, `docs/HANDOFF.md`.
- 56608 **명령 자동완성 후보를 입력 박스 위쪽에 고정**(§10 사용자 요청) — 명령
  프롬프트(esc `:`) 자동완성 후보(`#pcand`)가 입력 박스(`#prow`) **위쪽**에 펼쳐지게
  한다(모바일에서 박스 아래면 키보드에 가림). 기존엔 둘 다 `dock:bottom` 적층 순서에
  의존했는데(버전/환경 따라 뒤집힐 수 있음), 바닥 고정 `Vertical`(`#pwrap`)에 후보(위)
  →박스(아래) 순으로 묶어 못박았다. 회귀 `test_command_candidates_above_input_box`.
  클라 전용(attach 재실행). 파일: `pytmuxlib/client.py`, `tests/test_client.py`,
  `docs/HANDOFF.md`.
- 56607 **FIX 치명적 크래시: 복원된 Session 에 `popup` 누락 → 모든 attach 브릭**(§10)
  — 사용자 보고("실행 시 화면 일부 나타났다 바로 종료/빈 패널")의 근본 원인을 CL 56599
  가 남긴 `<sock>.error.log` 로 확정: `AttributeError: 'Session' object has no attribute
  'popup'`(`_layout_msg`→`_popup_layout`→`sess.popup`). 부팅 시 layout.json **자동
  복원**(`run_server` 가 `restore_layout()` 호출)이 Session 을 `Session.__new__` 로
  만들며 `__init__` 의 `popup=None` 을 빠뜨려, **저장된 레이아웃이 있는 데몬은 부팅마다
  popup 없는 Session 생성 → 이후 모든 attach 가 `_send_full` 에서 터져 화면 일부만
  그려진 채 끊김/브릭/빈 패널**. (resume(re-exec)·`ensure_default_session` 경로는 무사
  → fresh 데몬은 정상이라 재현이 까다로웠고, CL 56599 가드+에러로그가 결정적 단서.)
  수정: `restore_layout` 에 `sess.popup = None` 추가 + `_popup_layout` 이 `getattr`
  폴백. 회귀 `test_restore_layout_session_has_popup`, 189 passed. **서버측 — kill-server
  재기동 후 반영.** 파일: `pytmuxlib/server.py`, `tests/test_server.py`, `docs/HANDOFF.md`.
- 56603 **refactor(§10 LLM 친화, 1/N): client.py 순수 유틸리티 → `clientutil.py`**
  — client.py(4363줄)·server.py 거대 단일 파일을 **동작 보존**(헤드리스 테스트 전부
  통과 게이트) 한 채 작은 단일책임 모듈로 점진 분리하는 첫 단계. 모듈 최상단(클로저
  `build_client_app` 밖)의 순수 헬퍼/상수 9종(`_shell_argv`·`_char_cells`·`_fmt_tokens`
  ·`_TIME/_DATE_STRFTIME`·`_CLOCK_FONT`·`_JAMO`·`_normalize_key`·`_KEY_DIAG`)을 신규
  `pytmuxlib/clientutil.py` 로 이전하고 `from .clientutil import …` 로 되가져온다(공개
  이름 동일=하위호환, 클로저가 그 이름으로 참조). client.py 4363→4287줄. 188 passed
  (무변경). 클라 전용(attach 재실행). 다음: 클로저 프리앰블 헬퍼(theme_color·
  make_style·COMMANDS…)·모달 Screen 분리. 파일: `pytmuxlib/{client,clientutil}.py`,
  `docs/HANDOFF.md`.
- 56602 **Claude footer 클릭 → 권한모드 선택/원격제어 정보 팝업**(§10 item 2/3) —
  패널 내 Claude Code 하단 ① 권한모드 footer(`auto mode on (shift+tab to cycle)`)
  클릭 → 권한모드 선택 팝업(`PermModeScreen`, 현재 모드 표시+auto/default/plan 선택,
  bypass 는 위험 모드라 제외), ② `Remote Control active` 클릭 → 원격제어 정보 팝업
  (`InfoScreen`, 토글은 Claude 데스크탑 앱 관리라 터미널서 불가 → 안내 전용). 두 줄은
  Claude 가 PTY 안에 그리므로, 클라 `_composite` 가 패널 content 를 훑어 위치를 찾아
  클릭존(`_perm_zone`/`_remote_zone`, `_scan_footer_zones`)을 등록하고 `on_mouse_down`
  이 패스스루보다 먼저 히트테스트한다. 서버: `Pane._perm_mode`(관측)/`_perm_target`
  (목표), `_scan_claude` 가 idle 시 `claude_perm_mode` 로 현재 모드 관측→status
  (`panes_claude.perm_mode`), 수동 목표가 있으면 `_drive_perm_mode` 로 shift+tab 폐루프
  주입(없고 `claude_auto_mode` 면 기존 `_maybe_auto_mode`), `set_claude_perm_mode`
  액션. 회귀 2종(목표 설정·폐루프·도달 해제·status / 클릭존 등록·팝업·set 전송),
  188 passed. **서버+클라 — kill-server 재기동 후 반영.** 파일: `pytmuxlib/{client,
  server,model}.py`, `tests/test_{server,client}.py`, `docs/HANDOFF.md`.
- 56601 **degraded(빨간 외곽선) 고착 회복 — IPC 강제 재접속(수동 `reconnect` +
  워치독)**(§10) — 네트워크 저하로 외곽선이 빨강(CL 56593)으로 고착되고 ssh→pytmux
  가 멈출 때(클라↔서버 전송 정체로 `read_msg` 무한 블록), **실행 중 Claude 를 종료
  하지 않고** 반응성을 회복한다. **서버 PTY/세션은 보존하고 클라↔서버 소켓만 교체**
  → 서버 `_send_full` 로 전체 재동기(tmux 모델). `_force_reconnect` 가 정체된 소켓을
  강제로 닫아 블록을 깨우고 새 연결로 hello 재전송, 네트 상태를 리셋한다. **연결
  세대**(`_conn_gen`)+`_start_reader` 로 각 reader 태스크가 자기 (reader, gen) 을
  들고 돌아, 소켓 교체 시 옛 태스크는 EOF 에서 세대 불일치를 보고 `self.exit()` 없이
  조용히 종료(앱 안 닫힘). 트리거는 ① 수동 명령 `reconnect`/`resync`, ② 워치독
  (degraded 가 `net_recover_n` 기본 20표본≈10초 연속 지속 시 자동, `net_auto_reconnect`
  on). `_net_last_rtt` 보관(진단). 회귀 2종(소켓 교체·degraded 해제·앱 미종료·재동기
  ·누수 없음·워치독), 186 passed. 클라이언트 전용(attach 재실행). 파일:
  `pytmuxlib/client.py`, `tests/test_client.py`, `docs/HANDOFF.md`.
- 56599 **server attach 안정성 — 초기 _send_full/scan_claude 예외가 클라를 브릭하지
  않게 가드 + 에러 로그**(§10 사용자 보고) — 증상: pytmux 실행 시 **화면이 일부
  나타났다 바로 종료**되고, 이후 모든 attach 가 같은 상태로 **반복 실패(브릭)**.
  원인: `handle_client` 의 클라 등록(`self.clients.append`)과 **초기 `_send_full`
  루프가 try/finally 밖**이라, `_send_full`(레이아웃→화면→상태 순)이 한 번 예외를
  던지면 ① 화면이 일부만 전송된 채 연결이 끊겨 클라가 즉시 종료(=partial render
  then exit), ② 그 클라가 `self.clients` 에 **누수**되고, ③ 데몬 stderr 가
  `/dev/null` 이라 **트레이스백도 없이** 진단 불가 — 한 번 실패하면 누수된 죽은
  클라 + 깨진 상태로 이후 attach 도 반복 실패한다. 수정: `handle_client` 의 세션
  생성·append·초기 `_send_full`·메시지 루프를 **try 안으로** 옮겨 `finally` 가
  항상 클라를 정리(누수 차단)하고, 초기/teardown `_send_full` 은 **클라별 가드**
  (한 클라 실패가 다른 attach 를 막지 않음), 메시지 디스패치도 **per-message 가드**
  (한 메시지 실패가 세션을 끊지 않음). `_flush_loop` 은 `_scan_claude`(새 휴리스틱:
  프롬프트/토큰/권한모드) 예외가 flush 루프 전체(=모든 클라 렌더)를 죽이지 않게
  가드. 신규 `_log_error(where)` 가 예외 트레이스백을 `<sock>.error.log` 에 append
  (best-effort) — **데몬 무stderr 환경의 진단 단서**(다음 발생 시 정확 원인 추적용).
  회귀 `test_attach_survives_send_full_error`(예외 전파 안 함+누수 없음+error.log
  기록), 184 passed. **서버측 변경 — kill-server 재기동 후 반영.** 파일:
  `pytmuxlib/server.py`, `tests/test_server.py`, `docs/HANDOFF.md`.
- 56574 **팝업 닫기 [x] 버튼 글자 소실 수정(markup=False)** — Claude 토큰 사용량
  팝업(`TokenLogScreen`)·정보 팝업(`InfoScreen`) 우상단 닫기 버튼이 **배경색(빨강)만
  보이고 `[x]` 글자가 안 보이던** 버그. 원인: `Label("[x]")` 의 Textual 마크업이
  기본 활성이라 `[x]` 를 **스타일 태그로 해석**해 빈 문자열로 렌더(`render().plain==''`)
  — 닫는 `[/]` 가 없어 글자가 통째로 사라졌다. `#tklogclose`·`#infoclose` Label 에
  **`markup=False`** 를 주어 `"[x]"` 를 리터럴로 표시. 회귀: 두 팝업 테스트에
  `close.render().plain` 에 `[x]` 포함 단언 추가(총 167). 클라이언트 전용(attach 재실행).
  파일: `pytmuxlib/client.py`, `tests/test_client.py`, `docs/HANDOFF.md`.
- 56563 **원격 Claude 헤더 예약 디바운스 — Windows→ssh 첫 실행 한 줄 스크롤 떨림
  수정** — Windows 에서 pytmux 실행 후 ssh 로 원격 macOS Claude Code 를 처음 띄우면
  화면이 위아래로 **한 줄씩 스크롤**되는 떨림이 났다(로컬 정상). 원인은 헤더 행
  예약(#1, CL 56516): `_should_reserve_header = claude_header AND p._claude AND
  last_prompt` 면 내용 영역에서 한 행을 빼고 **PTY 도 ch-1 로 리사이즈**한다. 그런데
  `p._claude` 는 화면 텍스트 스크래핑(`claude_state`)이라 footer("? for shortcuts"·
  busy 스피너)가 한 프레임 안 잡히면 None 으로 깜빡인다. 로컬은 화면이 한 번에 도착해
  그 중간 프레임을 거의 안 잡지만, **ssh/ConPTY 는 화면이 조각나 도착**해 footer 없는
  중간 프레임을 잡을 확률이 커 `p._claude` 가 None↔truthy 로 깜빡 → 헤더 예약이 매
  프레임 토글 → flush 루프가 레이아웃을 다시 보내 PTY 를 ch↔ch-1 로 반복 리사이즈 →
  원격 Claude 가 SIGWINCH 마다 리플로우해 한 줄씩 떨린다. 해결: 헤더 예약 판정에
  **디바운스된 `p._hdr_claude`** 도입 — Claude 로 보이면 즉시 True(헤더 즉시 표시),
  None 이면 연속 `_HDR_CLAUDE_MISS`(=30프레임, 30Hz≈1초) 동안 None 이어야 False 로
  떨군다. 즉 **예약 해제(=PTY 한 행 키우기)만 디바운스**해 깜빡임 떨림을 없애고 설정은
  즉시라 반응성은 그대로(진짜 Claude 종료 시 행 회수 지연은 ~1초로 미미). `model.Pane`
  에 `_hdr_claude`/`_hdr_claude_miss`(init·respawn), `server` 의 `_should_reserve_header`
  가 `_hdr_claude` 를 읽고 `_scan_claude` 가 매 스캔 갱신, 상수 `_HDR_CLAUDE_MISS`.
  회귀 `test_claude_header_debounce_no_thrash` 신설 + `test_claude_header_reserves_row`
  갱신, 164 passed. **서버측 변경 — kill-server 재기동 후 반영.** 파일:
  `pytmuxlib/{model,server}.py`, `tests/test_server.py`.
- 56560 **대량 출력 비차단 처리: feed 슬라이스 드레인(`_feed_drain`)** — feed
  가속(56558) 후에도 pyte feed 는 순수 파이썬이라 ~1.2 MB/s 가 천장. PTY 한 읽기
  (최대 64KB)를 통째로 동기 feed 하면 그동안(~56ms) 이벤트 루프가 막혀 입력·flush·
  render 가 지연된다. **측정상 literal '레이트 제한/코얼레싱'은 효과 없음**(feed 가
  99.8%, `_on_pane_data` 청크당 스캔은 0.2%; 읽기와 feed 가 동기라 커널 PTY 버퍼가
  이미 producer 를 백프레셔 → 메모리 폭증/데이터 손실 없음). 진짜 개선은 feed 가
  루프를 오래 잡지 않게 쪼개 양보하는 것: 버스트(>`FEED_SLICE`)면 `pause_reader`
  로 reader 를 잠깐 떼고 8KB 슬라이스로 먹이며 슬라이스마다 `await asyncio.sleep(0)`
  로 양보, 다 비우면 `resume_reader`(`_feed_drain`). 소량(대화형 에코, ≤8KB)은
  기존처럼 인라인 즉시 처리. `_on_pane_data` 분기 + `_feed_drain`/`_stop_pane_feed`
  신설, 기존 본문은 `_ingest_slice` 로 분리, 모든 teardown(`_pane_eof`/`kill_pane`/
  `kill_window`/`_destroy_pane_proc`/`respawn`/popup-close/restart)에서 드레인 취소,
  restart 직전 남은 `_feedbuf` 동기 flush(execv 손실 방지). `pty_backend` 에
  `pause_reader`/`resume_reader`(`_UnixPty` 구현; 베이스 no-op, Windows 리더 스레드
  best-effort), `model` 에 `Pane._feedbuf/_feed_task`, `protocol` 에 `FEED_SLICE`.
  결과(측정): 폭주 중 최악 이벤트 루프 차단 **56.3ms → 13.6ms(4.1배)**, throughput
  비용 **+3.5%**(1.25→1.21 MB/s), 화면/스크롤백 무손실(슬라이스 드레인 = 일괄 feed
  와 바이트 동일). 회귀 165개 통과(신규 3: large_output_chunked_equivalent_and_
  lossless / small_output_fed_inline / feed_drain_interleaves_with_loop).
  **서버측 변경 — kill-server 재기동 후 반영.** 파일: `pytmuxlib/{protocol,
  pty_backend,model,server}.py`, `tests/test_server.py`.
- 56558 **PTY feed 2.75배 가속: pyte HistoryScreen 제거(`_ScrollbackScreen`)** —
  pytmux 체감 느림의 단일 원인이 PTY 출력 파싱(pyte feed)이 0.4 MB/s 라는 점이었다
  (빌드 로그·`cat` 등 다MB 출력이 들어오면 단일 asyncio 루프가 묶여 UI 정지). 다른
  터미널 비교상 호스트 터미널은 무죄 — Warp 가 Terminal.app 보다 빠름(5151 vs 2246
  fps, /tmp/termbench.py). cProfile 결과 pyte `HistoryScreen` 이 인터랙티브 페이징
  (prev/next_page)용 `__getattribute__` 훅으로 **모든 속성 접근**을 가로채
  before/after_event 를 끼워, `draw()` 의 글자별 속성 접근마다 호출돼(1 MB 피드당
  1,100만+ 회 — 전체 시간의 절반) feed 가 ~3배 느려졌다. pytmux 는 그 페이징을 전혀
  안 쓰고 `history.top` 만 읽으므로 순수 오버헤드. `_BCEHistoryScreen`
  (pyte.HistoryScreen 기반) → `_ScrollbackScreen`(plain `pyte.Screen` +
  `index`/`reverse_index`/`reset` 만 오버라이드)으로 교체해 HistoryScreen 의 줄
  수집을 **동일 조건·동일 대상**으로 재현하고 훅은 제거. `.history.top/.bottom` deque
  인터페이스(경량 `_History` 홀더)를 유지해 호출부(render/capture_pane/clear_history/
  `_export_screen`)는 무변경. 결과: 8 MB 피드 0.44 → 1.21 MB/s(2.75배), 스크롤백/화면
  버퍼가 옛 HistoryScreen 과 **바이트 단위 동일**함을 검증, 162개 테스트 전부 통과
  (test_feed_and_scrollback, test_restore_preserves_scrollback, test_pane_export_
  import_roundtrip, test_search_buffer_capture_clear 포함). 남은 개선: 대량 출력
  레이트 제한/코얼레싱, feed 별도 스레드 분리(feed 는 여전히 pyte 한계로 ~1.2 MB/s).
  **서버측 변경 — kill-server 재기동 후 반영**(이 세션에서 재기동 완료). 파일:
  `pytmuxlib/model.py`.
- 56539 **ESC 모드에서 `?` → 바로 help 팝업** — ESC 명령 모드에서 `:` 는 명령
  프롬프트(`PromptScreen`)를 여는데, 거기서 다시 `?` 를 쳐야 명령 목록이 나왔다. 한
  단계를 줄여 **ESC 모드에서 `?` 를 누르면 프롬프트를 거치지 않고 곧장 help 팝업**
  (`CommandListScreen`)을 띄운다. `_handle_esc_mode` 에 `ch == "?"` 분기 추가(`:` 와
  같이 `_exit_esc()` 후 `_run_command("help")`). 회귀 테스트
  `test_esc_mode_question_opens_help`. 클라이언트 전용(attach 재실행).
- 56538 **pytmux 강제 중첩 옵션(`--force`) 제거** — 중첩 실행 거부(CL 56394 로컬/56510
  원격)를 우회하던 전역 플래그 `--force` 를 폐지했다. 중첩(재귀 렌더·입력 꼬임)을 강제로
  허용할 정당한 이유가 없고, 오용 시 화면이 깨지는 함정이라 안전장치를 우회 불가로 굳혔다.
  `nesting_blocked()` 가 `force` 인자를 잃고 `$PYTMUX`/`$LC_PYTMUX` 표식만으로 판정,
  argparse `--force` 인자·안내 문구 제거(우회는 `unset PYTMUX LC_PYTMUX` 뿐). 회귀 테스트
  `test_nesting_blocked_helper` 에서 force 케이스 단언 삭제. 클라/런처 전용(attach 재실행).
- 56523 **mouse-debug: 휠→화살표 변환 切り分け 위해 내비게이션 키도 로깅** — 원격 SSH 휠
  스크롤백 미동작(§10)의 두 원인((a)휠 이벤트 미도달 vs (b)터미널이 휠을 ↑/↓ 화살표로
  변환=1007)을 가리기 위해, mouse-debug 켜진 동안 `on_key` 최상단 `_log_key` 가 **내비게이션
  키**(↑/↓/←/→/PageUp/PageDown/Home/End = `_KEY_DIAG`)도 `<sock>.mouse.log` 에 기록. `scroll_*`
  없이 `key up/down` 만 쏟아지면 (b) 확정(터미널 1007/alt-scroll 설정 문제), 둘 다 없으면 (a).
  **문자/단축키는 미기록**(화이트리스트 — 패널 입력 유출 방지). `test_mouse_debug_logging`
  확장(키 기록·문자 미기록 단언), 전체 150 passed. 클라 전용(attach 재실행).
- 56519 **시계/달력 오버레이 닫기: [x] 폐지 → 패널 클릭/Shift+ESC** — 우상단 [x] 닫기
  버튼이 좁은(모바일) 화면에서 잘 안 보이고 누르기 어려웠다. 두 오버레이 그리기에서
  [x]·`_clock_close_zones`/`_calendar_close_zones`(속성 포함) 제거. 닫기는 ① 오버레이가
  켜진 패널 클릭(`MultiplexerView.on_mouse_down`→`_pane_at`→`_close_overlay`), ② 활성
  패널 **Shift+ESC**(`on_key` normal: `shift+escape` 가 `_close_active_overlay` 성공 시
  소비, 없으면 기존대로 ESC 를 패널로 전달). 헬퍼 `_close_overlay`/`_close_active_overlay`
  추가. 상태줄 날짜·시계 클릭/`calendar-mode`·`clock-mode` 명령 토글은 그대로. 회귀
  테스트 `test_overlay_closes_by_panel_click_and_shift_esc` + 기존 2종 갱신(총 148).
  클라이언트 전용(attach 재실행).

- 56517 **정보 팝업 닫기 버튼 + 좁은 화면 반응형 폭** — 좁은(모바일) 폭에서 `InfoScreen`/
  `TokenLogScreen` 팝업이 **고정폭(64/84)이라 화면을 넘쳐 닫기 수단이 안 보이던** 문제.
  박스를 **반응형 폭**(`width:96%`·`max-width:66/86`)으로 바꾸고, 제목 줄을 `[제목 …
  [x]]` 헤더(Horizontal: 제목 `1fr` + 닫기 `[x]` 고정 5칸)로 만들어 **좁아도 [x] 가 항상
  오른쪽에 보임**. `[x]` 클릭(`on_click`)·`Esc`(기존 fallthrough) 둘 다로 닫힘. 회귀
  테스트 `test_info_popup_close_button_and_esc`(좁은 폭 58 에서 [x] 화면 안+클릭/Esc
  닫힘). 클라이언트 전용(attach 재실행).

- 56500 **프롬프트 히스토리 팝업 방향키 내비게이션 + 긴 프롬프트 줄바꿈**(§10/#7) —
  `InfoScreen.on_key` 가 **어떤 키에도 `dismiss`** 하던 탓에 방향키를 누르면 팝업이
  즉시 닫히던 버그 수정. 방향키(`up`/`down`/`pageup`/`pagedown`/`home`/`end`)는 닫지
  않고 `ListView` 선택을 옮기고(`action_cursor_up/down`·`index`), **그 외 키만** 닫는다.
  긴 프롬프트는 잘리지 않게 CSS(`#info ListItem Label { width: 1fr }`·`ListItem
  height:auto`)로 **여러 줄 줄바꿈**. InfoScreen 공용이라 다른 목록 팝업(options/
  토큰 사용량/list-keys)도 방향키 내비게이션·줄바꿈 혜택. 회귀 테스트
  `test_prompt_history_arrows_navigate_not_close`(총 135). 클라이언트 전용(attach 재실행).

- 56497 **다중 줄 상태표시줄**(§10/#10) — `StatusBar.lines`(0~5) + `extra` 보조 줄 포맷.
  맨 아래 줄=주 상태, 그 위는 `status-format[i]`. `render_line(y)` 줄별 분기, `set status N`/
  `set status-format <line> <fmt>`, 위젯 높이·뷰 크기·서버 resize 동기화. 회귀 테스트
  `test_multiline_status_bar`(총 134). 클라이언트 전용.

- 56495 **런타임 unbind-key/bind-key/list-keys**(§10/#11) — FEATURES 에서 "unbind 미구현"
  이던 것 해소. `_run_command` 에 `bind-key <key> <command>`·`unbind-key <key>|-a`·
  `list-keys` 추가(tmux `C-x` → ctrl+x 정규화, bind 명령 원문 플래그 보존). COMMANDS 노출.
  회귀 테스트 `test_bind_unbind_keys`(총 133). 클라이언트 전용.

- 56491 **캡처(REC) 출력 위치 이전 요구사항 기록**(§10, 사용자 요청·문서만) — 캡처
  로그가 현재 /tmp(`state_base`)에 남아 휘발·미공유. 프로젝트 디렉터리(`captures/`)로
  옮겨 Perforce 로 머신 간 공유하되 GitHub 엔 절대 미반영(.gitignore/add 제외) 하는
  요구사항·구현 방향(capture_dir 교체, 롤오버, 깃헙 차단) 정리. 다음 작업 후보.

- 56489 **패널 드래그 swap**(§10 마무리 묶음 #9b) — 서버 `swap_pane_ids` + 액션
  `swap_pane_to`(임의 두 패널 위치 교환). 클라 Shift+좌버튼 드래그로 패널 swap
  (`_pane_swap`/`_pane_swap_over`), 드래그 중 소스 dim·대상 강조. 회귀 테스트
  `test_swap_pane_ids`·`test_shift_drag_pane_swap`. 서버+클라 → kill-server 재기동.

- 56471/56473/56475/56479/56487 **Windows 포팅 후속 마감 묶음**(§10, WINDOWS_PORT §7-b4)
  — 추상화 레이어/리팩터 이후 잔여 POSIX 의존·패키징 정리. ① 56471 `proc.shell_argv`
  신설로 pipe-pane `/bin/sh` 하드코딩 제거 + `client._shell_argv` 위임 통합. ② 56473
  `replay.run_record` Windows 가드(메시지+코드 2). ③ 56475 `requirements.txt`
  (`wcwidth` 명시 + `pywinpty; win32`) + `install.ps1`/`uninstall.ps1` Windows 설치 래퍼.
  ④ 56479 dead code `protocol.default_socket_path` 제거 + `pytmux.py` 재export 정리.
  ⑤ 56487 POSIX 열화 우아한 폴백: `model.py` 렌더 resize `ImportError` 흡수,
  `test_windows_port` 가드 테스트(shell_argv/record/fg_command/resize), `WINDOWS_PORT.md`
  동기화. 회귀 132 통과. ⚠️ **attribution**: ⑤의 `server.py` `_fg_command` Windows 가드는
  공유 워크스페이스에서 동시 세션이 `server.py` 를 default CL에 먼저 열어 둔 탓에 분리
  서브밋 불가 → **CL 56489(드래그 swap)에 동반 커밋**됨(depot 반영 정상, 귀속만 어긋남).

- 56480 **단일 패널 테두리 on/off 옵션화**(§10 마무리 묶음 #9a) — 서버 `single_border`
  옵션(기본 ON, opts.json 영속). `_layout_msg` 가 `len(panes) >= 2 or single_border`
  일 때만 box. 단일 패널 OFF 면 화면 전체 사용, 다중 패널은 항상 테두리. 명령
  `single-border|pane-border [on|off|toggle]`, status `single_border` 클라 반영.
  회귀 테스트 `test_single_pane_border_toggle_and_persist`. 서버+클라 → kill-server 재기동.

- 56469 **탭 드래그 재정렬 시각 피드백**(§10 마무리 묶음 #8) — `TabBar._drag_over` +
  `on_mouse_move` 로 드래그 중 드롭 대상 탭을 추적, `render_line` 이 소스 탭은
  dim, 드롭 대상은 warning 배경+밑줄로 강조(놓으면 그 자리로 이동). 회귀 테스트
  `test_tab_drag_reorder_visual_feedback`(총 125). 클라이언트 전용.

- 56464 **pytmux 중첩 거부(로컬+원격) 요구사항 기록**(§10 #10, 사용자 요청·문서만) —
  로컬 거부(CL 56394)는 완료, 원격(ssh) 거부 구현 방향(SetEnv=PYTMUX 전파/원격측 가드/tty
  표식) 정리. 다음 작업 대상.

- 56460(+56462) **Claude 헤더 숨김 토글 + claude-header opts.json 영속**(§10 #6) — ②
  히스토리 팝업 `[h]` 로 패널별 헤더 숨김(`_claude_hidden_panes`, InfoScreen hide_key/
  hide_cb), ③ 서버가 `claude_header` 를 opts.json 에 영속(set_claude_header)·status 전달,
  클라 명령이 서버 경유로 영속. 회귀 테스트 3종(총 124). **서버+클라 → kill-server 재기동.**
  ⚠️ **attribution 주의**: 공유 워크스페이스(Windows 포팅 세션과 동시 작업) 탓에 **server.py·
  tests·docs 는 CL 56460, client.py 변경은 CL 56462(windows-port 세션 서브밋에 동반)에 분리
  귀속**됐다. depot 에는 양쪽 모두 반영되어 기능은 완전(데이터 손실 없음). 교훈: 같은 워크스페이스
  에선 디폴트 CL이 섞이니 CL 생성 시 파일 명시·CL 먼저 생성해 번호 확보.

- 56449 **ESC 모드 Claude 헤더 포커스 → 히스토리**(§10 #5) — ESC 모드에서 `h` 로 Claude
  헤더 포커스(`_hdr_focus`), ←↑/→↓ 헤더 이동, Enter 히스토리 팝업, Esc 해제. 포커스
  헤더는 accent 강조. 회귀 테스트 1종(총 119↑). 클라이언트 전용.

- 56445 **탭바 왼쪽 여백 + [+] 분리**(사용자 요청) — `TabBar.LEAD=1` 로 첫 탭을 한 칸
  오른쪽에서 시작(lead 엔트리로 render_line/active_tab_xrange 공유), `[+]` addtxt 를
  " [+] "→"  [+] "(앞 공백 2칸)로 왼쪽 탭과 한 칸 더 띄움. 회귀 테스트 1종 + 연결부
  테스트 LEAD 반영. 클라이언트 전용(attach 재실행).

- 56443 **토큰 로깅 계정별 구분 요구사항 기록**(§10 #7, 사용자 요청·문서만) — 토큰 사용량
  로깅을 Claude 계정(개인/팀)별로 분리해야 한다는 요구사항과 식별 방향(/status·푸터의
  이메일/조직/플랜 파싱 또는 수동 레이블, 민감정보 해시) 추가. #3(56437)에서 합산 단위가
  이미 정의됐음(committed 이벤트=1응답 1레코드)도 반영.

- 56439 **대기(큐)중 프롬프트는 헤더 즉시 안 바꿈**(§10) — `_track_prompt` 가 busy 중
  입력 프롬프트를 `Pane.pending_prompts` 큐에 보관(last_prompt 즉시 안 덮음, 히스토리는
  즉시), `_scan_claude` 가 응답 경계(busy→non-busy 또는 running 토큰 급감 committed>0)에
  큐 다음 프롬프트를 last_prompt 로 승격. 헤더 = "지금 처리 중인 프롬프트". 회귀 테스트
  1종(총 114). **서버 변경 → kill-server 재기동.**

- 56437 **상태줄 토큰 사용량 누적 합산**(§10) — 신규 `pytmuxlib/tokens.py`(running 토큰
  파서+응답별 peak 합산 상태기계). 정의: busy footer 의 "↑/↓ N tokens" 는 현재 응답
  running 수(응답 사이 리셋)라 세션 누계 = 각 응답 peak 의 합. `server._scan_claude` 가
  매 프레임 `step` 으로 접어 `Pane._session_tokens` 에 확정(세션 시작 reset), status
  `claude_tokens` → 상태줄 `Σ45.2k` 표기. 회귀 테스트 7종(총 109). **서버+클라 →
  kill-server 재기동.**

- 56429 **Claude 스티키 헤더 배경 진하게**(사용자 요청) — Claude Code 패널 맨 윗줄
  마지막 프롬프트 스티키 헤더(`_draw_claude_headers`)의 배경을 `primary` → 한 단계
  어두운 `primary-darken-2`(#0053AA)로. 본문/활성 테두리(primary)보다 어두워 헤더가
  더 또렷이 구분된다. `_THEME_FALLBACK` 에 primary-darken-2 폴백 추가. 테스트의 헤더
  배경색 단언 1종 추가. 클라이언트 전용(attach 재실행 반영).

- 56428 **Claude 사용량 신호 보강**(§10 사용량) — `claude_usage` 가 컨텍스트 잔량% 우선,
  확장 컨텍스트 모델 배지(`(1M context)`→`_CTX_BADGE_RE`)를 `ctx 23% 1M` 처럼 덧붙이고,
  busy footer 의 스트리밍 델타(`↑/↓ N tokens`)는 사용량 보고에서 제외(화살표 없는 누계만
  채택). 회귀 테스트 2종(총 99). 서버 import 경유 — kill-server 재기동 후 반영.

- 56426 **원격 SSH 휠 스크롤백 best-effort 완화**(§10) — App.on_mount 가 대체 스크롤
  모드(DECSET 1007)를 꺼(`\x1b[?1007l`, `_term_write`) iTerm2/일부 SSH 클라가 휠을
  화살표로 바꿔 보내 스크롤백이 안 열리는 문제를 완화. on_unmount 복원, `set alt-scroll
  on|off` 토글(기본 on). 회귀 테스트 1종(총 97). 클라이언트 전용.

- 56421 **탭 전환 시 노트북 연결부가 따라오게**(§10 #23 회귀 해결) — 활성 탭을 바꿔도
  연결부가 옛 탭에 남던 버그. 원인 둘: ① `active_tab_xrange` 가 렌더 부산물 `_zones`
  를 읽어 전환 직후 stale, ② 연결부를 그리는 `_composite` 가 status(탭 변경) 경로에서
  안 돌았다. 탭바 기하를 `_entries()` 로 추출해 `render_line`/`active_tab_xrange` 가
  공유(후자는 현재 `self.tabs`+스크롤에서 직접 계산), `_update_tabbar` 가 활성 탭
  변경 시 즉시 재합성. 회귀 테스트 1종(총 96). 클라이언트 전용.

- 56419 **활성 탭↔콘텐츠 연결부를 위쪽 절반 블록(▀)으로**(§10 노트북 연결 다듬기) —
  CL 56413 의 노트북 탭 연결부가 row 0 활성 탭 구간을 활성색 배경으로 꽉 채우고(두꺼운
  블록) 양끝을 ┘/└ 코너로 마감했는데, 꽉 찬 블록이라 양옆 가로 테두리(─)와 높이가
  안 맞았다. 그 구간을 위쪽 절반 블록 ▀(활성색 전경)로 칠해 ▀ 아래 모서리(셀 중앙선)가
  ─ 와 같은 높이라 한 줄로 매끄럽게 이어지게 함. ┘/└ 코너 마감 제거. 테스트 갱신
  (연결 셀이 ▀+활성 전경색 확인, 비활성 모서리 검사는 연결부 구간 제외). 총 95 passed.
  클라이언트 전용 — attach 재실행으로 반영.

- 56415 **REC 클릭 → 캡처 정보 팝업**(§10 #4 해결) — 서버 capture_path/size,
  StatusBar _rec_zone 클릭 InfoScreen. 회귀 테스트 1종(총 95). 서버+클라 재기동.

- 56413 **활성 탭 노트북 모양 연결**(§10 #23 해결) — 콘텐츠 상단 테두리의 활성 탭
  구간을 끊고 활성색으로(active_tab_xrange). 회귀 테스트 1종(총 94). 클라이언트 전용.

- 56411 **토큰 사용량 클릭 → Claude 트리 팝업**(§10 #19 해결) — _usage_zone 클릭,
  request_tree(usage)→Claude 패널 필터 InfoScreen(앱·상태·사용량). _pane_overview 에
  claude/usage 추가. 회귀 테스트 1종(총 93). 서버+클라 → kill-server 재기동.

- 56409 Claude 헤더 **프롬프트 히스토리 팝업**(§10 #7 부분해결) — 서버 prompt_history
  누적, 클라 헤더 클릭/명령(prompt-history)→InfoScreen 시간순. ESC 포커스 선택은 후속.
  회귀 테스트 2종(총 92). 서버+클라 → kill-server 재기동.

- 56407 상태줄 토큰 사용량 **세션 동안 유지**(§10 #5 부분: 표시 유지) — _scan_claude
  가 Claude 살아있는 동안 마지막 사용량 보존, 종료 시 클리어. 누적 합산(1)은 후속.
  회귀 테스트 1종(총 90). 서버 변경 → kill-server 재기동 후 반영.

- 56405 Claude 헤더 **[x] 제거 → claude-header on|off 명령**(§10 #8 부분해결) —
  프롬프트 단위 숨김 제거, 전역 옵션 claude_header_on·명령으로 제어. 팝업 숨김(②)·
  영속(③)은 #7 과 함께 후속. 클라이언트 전용.

- 56401 **비활성 탭 Claude 완료 알림**(§10 #22 해결) — `_scan_claude` 가 모든 탭
  패널을 훑어 비활성 탭 busy→idle 을 `has_claude_done` 으로, TabBar 가 success 배경.
  회귀 테스트 2종(총 89). 서버+클라 → kill-server 재기동 후 반영.

- 56398 **Ctrl+Q 활성 패널 전달**(§10 #25 해결) — Textual 기본 ctrl+q quit
  priority 바인딩을 ctrl_q 액션으로 덮어 normal 모드면 \x11 전달(종료는 detach).
  회귀 테스트 1종(총 87). 클라이언트 전용.

- 56396 패널 **경계선 마우스오버 배경 강조**(§10 #27 해결) — `_hover_divider` +
  on_mouse_move 추적, _composite 가 그 칸 배경 강조. 회귀 테스트 1종(총 86).
  클라이언트 전용.

- 56394 **pytmux 중첩 실행 거부(로컬)**(§10 #15 로컬 해결) — `nesting_blocked`
  ($PYTMUX 설정+not --force)면 attach 를 sys.exit(1). 원격 중첩은 후속. 테스트
  2종(총 85, test_launcher.py 신설).

- 56392 닫기 확인 팝업 **pytmux 종료 케이스 구분**(§10 #16 해결) — 마지막 탭이면
  제목/문구 경고 + 선택 강조를 붉은색(ConfirmScreen danger). 회귀 테스트 1종(총 83).
  클라이언트 전용.

- 56389 **ESC 모드 탭바 내비게이션**(§10 #3/#26 해결) — Enter 한 번으로 전환+ESC
  종료(`_exit_esc`), ←/↑/→ 가 탭+`[+]` 순환(센티넬 "+"), [+] Enter=새 탭, 선택
  강조. 회귀 테스트 1종(총 82). 클라이언트 전용.

- 56385 **§11 Claude 코드 분리 1단계** — `claude_state`/`claude_usage`/
  `parse_reset_delay`+정규식을 `pytmuxlib/claude.py` 로 이전, protocol re-export
  하위호환, server import `.claude`, 테스트 `test_claude.py`. 무동작(81 passed).
  서버 import 변경 → kill-server 재기동 후 반영.
- 56383 **탭/패널 트리 개요**(§10 #14/#24 해결) — 서버 `_tree_msg` 가 패널 리스트
  ({id,title,cmd,remote})를 전달, 클라 `ChooseTreeScreen` 가 패널을 들여쓰기로
  `[local]`/`[ssh]`+앱 표시, Enter=전환·d/x=종료, `overview`/`tree` 명령. 회귀
  테스트 2종(총 80). **서버+클라 양쪽 → kill-server 재기동 후 반영.**
- 56379 컨텍스트 메뉴 **토글 항목 on/off 표시 + 선택해도 안 닫기**(§10 #17 해결) —
  `MENU_TOGGLES`·라벨에 ●/○·토글 선택 시 dismiss 없이 명령+낙관적 갱신, ESC 로만
  닫기, status 회신 때 `refresh_labels`. 회귀 테스트 1종(총 78). 클라이언트 전용.
- 56377 컨텍스트 메뉴 열림 시 **대상 패널 배경 구분**(§10 #18 해결) — `_menu_open`
  플래그 + `_composite` 에서 `_menu_pane` 외 패널을 `Style(dim=True)` 로 흐리게.
  회귀 테스트 1종(총 77). 클라이언트 전용.
- 56375 컨텍스트 메뉴 **우클릭으로 통일 + Ctrl+Click 무력화**(§10 #29 해결) —
  `on_mouse_down`: ctrl 클릭은 무동작, button 3 은 마우스 모드 앱 위여도 커서 아래
  패널을 `select_pane_id` 후 그 패널 대상 `open_menu`. `_menu_pane` 보관(#18 토대).
  회귀 테스트 1종(총 76). 클라이언트 전용.
- 56373 상단 탭바·하단 상태줄 **배경을 터미널 기본 배경색으로**(§10 #10/#28 해결) —
  `TabBar`/`StatusBar` 의 base bgcolor 를 고정 테마색(panel/surface)에서 None(터미널
  기본)으로. 활성 탭·REC 등 강조 배지는 자체 bgcolor 유지, 명시 bg(self.bg) 우선.
  회귀 테스트 1종(총 75). 클라이언트 전용.
- 56371 상태줄 **날짜 클릭 → 이번 달 달력 오버레이**(§10 #13 해결) — clock-mode
  미러: `calendar_panes`/`toggle_calendar`/`_draw_calendar_overlay`. 상태줄 날짜
  존(`_date_zone`)·우상단 `[x]`·명령(`calendar-mode`/`cal`)으로 토글. 뒤 화면 dim,
  `calendar` 모듈 그리드(월요일 시작)·오늘 강조, 자정 갱신. 시계/달력 상호 배타.
  회귀 테스트 1종(총 74). 클라이언트 전용.
- 56369 상태줄 오른쪽 **세그먼트 분리**(§10 #11/#12 해결, #13 토대) — `StatusBar.
  _expand_parts` 가 right_fmt 를 (kind,text) 런(host/time/date/plain)으로 쪼개
  render_line 이 런별 세그먼트로 그린다. ① 원격(SSH; `_is_remote`=`SSH_CONNECTION`/
  `SSH_TTY`)이면 host 를 `ssh:`+붉은색(error)+bold 로. ② `_clock_zone` 이 시각
  (`%H:%M`) 런만 덮어 host·날짜 클릭은 clock-mode 무관. ③ 날짜 런 x 범위를
  `_date_zone` 으로 등록(달력 #13 트리거 준비). 회귀 테스트 2종. 클라이언트 전용.
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

### 10-A. 2026-06-05 세션 미착수 큐 (사용자 요청, 순차 구현 예정)

> 같은 세션에서 ESC 더블탭(#36)·ESC+n/p·ESC+없는숫자 깜빡임·SSH attach·driver 윈도우
> 포팅(**CL 56691**)과 명령프롬프트 한영 오타 복원·정보팝업 ←→ 닫기[x] 내비
> (**CL 56702**)는 구현·제출 완료. 아래는 이후 들어온 요청으로 **아직 미착수**(기록만).
> #1·#2 패널 DnD 마우스 설계는 확정됨 → [MEMORY] `pytmux-pane-dnd-mouse-design` 참조.

- ~~**[버그 보고] 모바일에서 Claude 권한모드 `auto mode` 자동 전환이 안 됨**~~
  (2026-06-05 보고) → **CL 56761 에서 해결.** **근본 원인**: 자동전환은 전적으로
  서버 측이다(`_scan_claude` 가 화면 텍스트로 `claude_perm_mode()` 감지 → auto 아니면
  `_maybe_auto_mode` 가 backtab `\x1b[Z` 를 PTY 에 주입 — 주입·감지 모두 클라 단말
  무관). 그런데 `claude_perm_mode` 가 **default(일반) 모드를 `"shift+tab to cycle"`
  문구로만** 잡았는데, 실제 Claude default footer 는 권한 글리프 없이 **`"? for
  shortcuts"`** 입력 힌트만 그린다(= `claude_state` 의 idle 신호). 그래서 default 에서
  `claude_perm_mode` 가 `None` 반환 → `_maybe_auto_mode` 가 `mode is None` 으로 조기
  반환 → **default→auto 전환이 시작조차 못 함**. 폭 무관한 근본 버그였으나, 좁은 폭
  모바일에서 (auto/plan 글리프 footer 는 줄 앞쪽이라 좁아도 살아남는 반면) Claude 가
  default 인 상태가 흔해 특히 두드러졌다. 기존 테스트가 default 를 **가짜
  `"shift+tab to cycle"`** 로 넣어 통과해 거짓 확신을 줬다. **수정**: `claude_perm_mode`
  가 글리프(⏵⏵/⏸/auto/plan/bypass)를 먼저 판정하고, 글리프 없이 idle 입력 힌트
  (`"? for shortcuts"`/`"/help for help"`)가 보이면 **default 로 판정**한다 →
  폐루프가 default 에서 시작. 캡처 로그를 pyte 로 재렌더해 실제 footer 4종 확인,
  단위/서버 테스트를 실제 `"? for shortcuts"` 로 교체. 231 passed. **서버측 —
  kill-server 재기동 후 반영.** **연관**: 권한모드 자동전환(CL 56591)·footer 클릭
  팝업/폐루프(CL 56602).
- ~~**[UI 요청] 권한모드 선택 팝업을 좌측 정렬 + 'auto mode on' 바로 위에 배치**~~ →
  **CL 56718 에서 해결.** `PermModeScreen` CSS 를 `align: left top` 으로 바꾸고,
  `open_perm_mode` 가 `_perm_zone[pid]` 의 시작 x(`anchor_x`)를 넘겨 `on_mount` 가
  박스 offset.x 를 그 footer 시작 x 에 맞춘다(화면 오른쪽 넘으면 클램프, 앵커 없으면
  가로 중앙). 세로는 기존대로 클릭 줄 바로 위(공간 없으면 아래).
- ~~**[UI 요청] 권한모드 선택 팝업 — 바깥 클릭으로 닫기**~~ → **CL 56718 에서 해결.**
  `PermModeScreen.on_click` 추가 — 조상 체인에 `#perm` 박스가 없으면(백드롭) `dismiss(None)`
  (InfoScreen 의 inside-box 판정 패턴 재사용).
- ~~**[성능 요청] 팝업 표시 시 배경 디밍이 ~1초 걸려 느림 — 개선**~~ → **CL 56731 에서
  해결(두 원인 모두 대응).** ① **지연**: `push_screen` 이 dim 재합성을 `call_after_refresh`
  로만 예약해, idle 상태에선 다음 refresh(최악엔 1초 clock tick)까지 dim 이 늦게 적용됐다
  → 같은 턴에 `_composite()` 를 **즉시 호출**(set_frame→view.refresh 로 다음 프레임 표시),
  마운트 후 안정화용 `call_after_refresh` 도 유지. `pop_screen` 도 동일. ② **비용**:
  전 화면 셀 dim 이 `_darken_style` 을 셀마다 호출(truecolor 블렌드+Style 생성)했는데
  대부분 셀의 스타일이 같으므로 `@lru_cache(8192)` 로 (style,ratio) 캐시 → 같은 스타일은
  한 번만 계산. 순수 함수·Style 불변/해시가능이라 안전.
- ~~**[검증+문서 요청] pytmux 패널 안 Claude CLI 에서 shift+home/end/shift+방향키 텍스트
  선택·삭제·수정 가능 여부**~~ → **CL 56741 에서 완료** → 문서 [CLAUDE_TEXT_EDITING.md]
  (CLAUDE_TEXT_EDITING.md). 검증 결과: **pytmux 전달은 ✅ 검증 완료**(정상 모드에서
  `Shift+Home/End/방향키` 를 표준 xterm `CSI 1;2 X` 시퀀스로 패널에 손실 없이 전달,
  미가로채기 — 헤드리스 회귀 `test_shift_nav_keys_forwarded_to_panel` 로 6키 고정).
  **선택/삭제/수정의 실효는 앱(Claude CLI) 해석에 의존** → 문서에 바이트 시퀀스 표·전달
  경로·라이브 수동 체크리스트·터미널 수정자 인코딩 의존 제약을 정리해 제출.
- ~~**[UI 요청] 토큰 사용량 팝업 — 하단 가로선 + 전 세션 토큰 합계 + 하단 닫기 버튼**~~ →
  **CL 56721 에서 해결.** ① `client._usage_tree_lines` 가 맨 아래에 **가로 구분선**(`─`×36)
  과 **전체 세션 토큰 합계**(`전체 세션 합계 — Σ N`) 한 줄을 덧붙인다(모든 claude 패널
  tokens 합). 토큰 탭(통합 상태 팝업)과 독립 사용량 트리 팝업 양쪽에 적용. ② `InfoTabsScreen`
  에 **하단 닫기 버튼**(`#itclosebtn`, 목록 아래 한 줄·가로 가득·가운데) 추가 — 클릭/터치로
  닫힘(상단 [x] 와 별개로 좁은 화면·긴 목록에서 손 닿는 곳).
- ~~**[UI 요청/의도확인] 리스트 팝업 — 긴 영문 URL 하드 줄바꿈(번호 정렬 보존)**~~ →
  **CL 56724 에서 해결(결정: 글자단위 hard-wrap + 행잉 인덴트 채택, truncate 아님).**
  `InfoScreen(wrap_hang=True)` 면 마운트 후 박스 실제 폭을 재서(`_rewrap`, `call_after_refresh`)
  각 줄을 `_hangwrap` 로 하드 줄바꿈한다 — 'NN. '/'NN) ' 번호 접두사 폭만큼 이어줄을
  들여써 번호 정렬을 보존(공백 없는 긴 URL 은 글자 단위로 컷). 프롬프트 히스토리가
  `wrap_hang=True` 로 연다. (len 기반 폭이라 영문 URL=핵심 케이스엔 정확, 한글 와이드
  글자는 근사 — 필요시 후속.)
- ~~**[UI 요청] 프롬프트 히스토리 팝업 — 마지막 항목서 ↓ 시 빈칸 건너뛰어 `[h]` 메뉴로
  + 그 사이 구분선**~~ → **CL 56724 에서 해결.** `open_prompt_history` 가 마지막 프롬프트와
  `[h]` footer 사이에 **구분선(`─`×24)** 을 넣고, `InfoScreen` nav 가 **구분선/빈 줄을 건너뛴다**
  (`_skip`/`_skip_over` — 마운트 시 표시줄에서 `─`/공백만인 줄을 skip 집합으로 계산, ↑↓·
  page·home/end 이동 후 같은 방향으로 skip). 그래서 마지막 항목서 ↓ 한 번에 `[h]` 로 점프.
- ~~**[UI 요청] 달력 큰 글꼴 — 한 날짜의 두 자리 숫자 사이 간격 좁히기**~~ →
  **CL 56720 에서 해결.** `client._draw_calendar_overlay` 의 큰 달력(시계 폰트) 경로에서
  자리(숫자) 사이 간격 `DIG` 를 2→1 로 줄여 한 날짜의 두 자리가 한 덩어리로 읽히게 했다.
  날짜칸 사이 간격(`DGAP`/`DCW=8`)은 그대로라 날짜끼리는 안 붙는다(두 자리 폭 3+1+3=7
  이 8칸 안에서 가운데 정렬). (위치는 `clientwidgets` 가 아니라 `client.py` 합성 경로였음.)
- ~~**[기능 요청] `open-clock`/`open-calendar`·`close-clock`/`close-calendar` 명령**~~ →
  **CL 56719 에서 해결.** `open-clock`/`open-calendar` = 현재 하이라이트(활성) 패널에
  시계/달력 **멱등 켜기**(이미 떠 있으면 유지 — 토글과 달리 다시 호출해도 안 꺼짐),
  `close-clock`/`close-calendar` = 활성 패널에서 끄기(멱등). 한 패널엔 한 오버레이만
  (open 시 반대 오버레이 닫음). 신규 `set_clock`/`set_calendar`(멱등) 헬퍼 + `_run_command`
  디스패치, `clientutil.COMMANDS`/`COMMAND_NOARG` 등록(? 목록·즉시 실행).
- ~~**[기능 요청] OS 클립보드의 이미지·텍스트를 읽어 pytmux 안에 붙여넣는 명령**~~ →
  **CL 56722 에서 해결(결정 ① 채택).** `paste-clipboard`(=Ctrl+V) 가 ① 텍스트면 그 텍스트
  paste(기존), ② 텍스트 없고 이미지면 신규 `_clipboard_save_image` 로 **임시 PNG 저장 후
  그 경로 문자열을 paste**(Claude Code CLI 등이 경로를 첨부 이미지로 인식 — 결정 ①),
  ③ 저장 실패(도구 부재/원격)면 Alt+V(ESC v) 공유-클립보드 폴백. 저장 분기: Windows
  `System.Windows.Forms.Clipboard.GetImage().Save(png)`(`-Sta`, `no_window_kwargs`),
  macOS `pngpaste`, Linux `xclip -t image/png -o`/`wl-paste --type image/png`. **로컬 가정**:
  PNG 는 클라이언트 머신에 생기므로 클라이언트=서버일 때 경로가 유효(원격은 ③ 폴백).

- ~~**[UI 요청] 하단 상태줄 정보 팝업 통합 — REC·Claude 토큰·서버이름 클릭을 탭으로
  구분된 단일 팝업으로**~~ → **CL 56726 에서 완결.** REC·토큰은 이미 통합 팝업
  (`InfoTabsScreen`, CL 56??/§10 #10)으로 합쳐져 있었고, 이번에 **세 번째 '서버' 탭 +
  서버이름(host) 클릭존**을 추가해 요청을 완결했다. ① `client._server_info_lines`
  (호스트·로컬/원격·소켓 경로·RTT·degraded 응답성·재접속 안내), `_open_status_tabs` 가
  `[REC, 토큰, 서버]` 3탭 구성. ② 상태줄(`clientwidgets.StatusBar`)의 host 런에
  `_host_zone` 등록(`_render_main` 우측 런 루프), `on_mouse_down` 이 host 클릭 시
  `show_status_tabs(initial=2)`(서버 탭)로 통합 팝업을 연다 — REC→0·토큰→1·서버→2 로
  클릭 요소에 맞는 초기 탭. 탭 전환은 기존 `InfoTabsScreen` ←→/클릭. (아래 "상태줄
  `ssh:` 호스트 클릭→서버 정보 팝업" 항목을 이 통합이 흡수.)
- ~~**[UI 요청] 명령 목록 팝업에서 Claude 관련 기능을 독립된 탭으로 분리**~~ →
  **CL 56763 에서 해결.** 이전엔 `"모니터/Claude"` 혼합 카테고리였던 것을 **`"모니터"`**
  (monitor-activity/monitor-bell/capture-output)와 **`"Claude"`**(auto-resume·
  auto-resume-message·claude-header·prompt-history·token-usage/log/account·prompt-clear·
  prompt-clear-message·prompt-clear-queue·claude-rules·auto-doc-clear·claude-auto-mode
  — 13개)로 분리했다. `clientutil.COMMANDS` 의 카테고리 키만 바꿔(CommandListScreen 의
  카테고리 버킷/탭은 그대로 동작) 탭 순서는 …레이아웃·모니터·Claude·설정/기타. 회귀:
  `test_command_palette_categories` 에 Claude/모니터 분리 단언 추가. 클라 전용(attach
  재실행).
- ~~**[버그·기능 요청] 네트워크 degraded(빨간 외곽선) 고착에서 Claude 종료 없이
  패널 반응성 회복**~~ → **CL 56601 에서 해결**(수동 `reconnect`/`resync` 명령 +
  degraded 워치독 자동 재접속, 서버 PTY/세션 보존·`_send_full` 재동기, 연결 세대로
  옛 reader 조용히 종료). 아래는 원 분석 기록.
- **[원래 보고·분석] 네트워크 degraded(빨간 외곽선) 고착에서 Claude 종료
  없이 패널 반응성 회복** — 보고: 한 번 네트워크가 나빠져 패널 아웃라인이 빨간색
  (CL 56593 응답성 degraded)으로 바뀐 뒤 **영영 원복되지 않는다**. **Claude
  데스크탑 앱으로 보면 여전히 동작 중**이지만 **ssh 를 통해 pytmux 로 보면 반응이
  없다(멈춤)**. 실행 중인 Claude Code 를 **종료하지 않고** 패널 반응성을 회복할
  방법이 필요하다. 원인 분석: ① Claude 프로세스(서버 측 PTY 자식)는 살아 있고
  데스크탑 앱은 **자체 원격제어 채널**로 Claude 와 직접 통신하므로 진행이 보인다 —
  멈춘 곳은 **로컬 pytmux 클라 ↔ 원격 pytmux 서버 사이의 ssh 전송(IPC)** 이다.
  ② ssh 가 정체되면 클라의 `read_msg(self.reader)`(`client.py:2268`)가 무한 블록
  되고 입력 왕복이 끊긴다. ③ degraded 가 안 풀리는 건 ping 의 pong 이 영영 안 와
  (`_net_sample` 양호 표본이 0) 외곽선이 빨강에 고착되는 것 — **즉 빨강은 증상을
  정확히 보고하나 복구 수단이 없다**(CL 56593 은 detection 만). **기존 자산(복구
  토대)**: ㉠ **detach/재attach** — `detach` 명령이 앱만 종료하고 서버 셸/PTY 는
  유지(`client.py:3137` `self.exit("detached")`); 새 ssh 로 다시 attach 하면 서버가
  세션을 그대로 들고 있어 회복(tmux 모델). 단 정체된 같은 ssh 위에서 detach 자체가
  안 먹을 수 있어 **로컬에서 ssh 를 끊고 새로 접속**해야 할 수도. ㉡ **자동 재접속
  기계** — `_reconnect`(`client.py:2278`)가 이미 `ipc.open_connection` 재연결+hello
  재전송→서버 `_send_full` 로 전체 상태 재수신을 한다(작업 보존 재시작 ⓔ 용). 이
  경로를 **degraded 워치독이 재사용**하면 PTY 를 안 건드리고 IPC 계층만 새로 세워
  회복할 수 있다. **구현 방향(택1/조합)**: (A) **degraded 워치독 자동 재접속** —
  degraded 가 임계(예 수 초~10초) 이상 지속되면 클라가 **멈춘 소켓을 버리고
  reconnect**(stuck `read_msg` 를 깨우려면 reader 태스크/소켓을 강제 close 후 재
  open; `asyncio.wait_for` 타임아웃·watchdog 태스크 필요). 성공 시 서버 `_send_full`
  로 화면/레이아웃 재동기 → 반응성 회복. (B) **수동 회복 명령** — `reconnect`/
  `resync` 명령을 추가해 사용자가 빨간 외곽선을 보고 즉시 IPC 재연결을 트리거(가장
  단순·안전). (C) **서버 측 slow-client 가드** — 서버가 한 클라에 `write_msg` 할 때
  블록되면 전체가 굶을 수 있으니, 클라별 쓰기 타임아웃/드롭(정체 클라가 서버를 막지
  않게). **열린 결정**: ㉠ 자동(A) vs 수동(B) vs 둘 다, ㉡ stuck `read_msg` 를 깨우는
  방법(reader 태스크 취소+소켓 close 후 _reconnect 재사용 범위), ㉢ degraded 지속
  임계(빨강 표시 임계와 별개의 더 긴 "회복 시도" 임계), ㉣ 서버가 IPC 만 새로 받고
  PTY/세션은 100% 보존하는지 회귀 검증. **연관**: 네트워크 응답성 외곽선(CL 56593,
  이 항목의 detection), 아래 "Windows→ssh→원격 macOS ssh 반응성 급락"·feed 드레인/
  백프레셔(서버 측 정체 원인 후보), 데스크탑 원격제어 프롬프트 반영(CL 56592, "앱은
  살아있다"의 같은 근거). **현재는 기록만 — 미구현.**
- ~~**[UI 요청] Claude footer 모드/원격제어 영역 호버 시 배경색을 살짝
  바꿔 클릭 가능함을 표시**~~ → **CL 56762 에서 해결.** `MultiplexerView.on_mouse_move`
  가 `_footer_zone_at(x,y)`(신규, perm/remote 클릭존 공용 히트테스트)로 호버 대상을
  판정해 `_set_footer_hover((pid,kind))` 로 기억하고(바뀔 때만 재합성, 떨림 방지),
  `_composite` 가 divider 호버 직후 그 클릭존 줄 배경을 `secondary` 로 한 톤 덮는다
  (글자색 유지). 회귀 `test_claude_footer_hover_highlights_zone`. 아래는 원 분석.
  요청: 마우스로 Claude Code 하단의 **권한모드 footer**·
  **`Remote Control active`** 영역(= CL 56602 에서 등록한 `_perm_zone`/`_remote_zone`
  클릭존)에 **호버하면 배경색을 살짝 바꿔** 클릭 가능한 영역임을 알린다. 현재 자산:
  클릭존은 이미 있다(`_perm_zone`/`_remote_zone`, `client.py:_scan_footer_zones`,
  `on_mouse_down` 히트테스트). 구현 방향: ㉠ `MultiplexerView.on_mouse_move` 에서
  커서가 두 클릭존 안이면 hover 대상(`_footer_hover=(pid,kind)`)을 기억하고
  `_composite` 재합성, ㉡ `_composite` 가 그 클릭존 줄(content 좌표)을 칠할 때 배경을
  한 톤 밝게/어둡게(예 `$panel`/`secondary` 블렌드) 덮어 affordance 표시(탭바 드래그
  `_drag_over` 강조 선례 참고). **주의**: 호버 강조는 클릭존 줄에만, Claude 본문
  글자색은 유지(배경만 살짝). 탭바 호버(`_hover_divider`) 패턴과 동형. **연관**: CL
  56602(클릭존·팝업)·탭 드래그 시각 피드백(CL 56469). **현재는 기록만 — 미구현.**
- ~~**[UI 요청] 패널 하단 Claude `auto mode on` footer 클릭/터치 → 권한모드 선택
  팝업**~~ → **CL 56602 에서 해결**(`PermModeScreen`, 현재 모드 표시+선택→shift+tab
  폐루프 주입). 아래는 원 분석 기록.
- **[원래 분석] 패널 하단 Claude `auto mode on` footer 클릭/터치 → 권한모드
  선택 팝업** — 요청: Claude Code 가 실행 중일 때 **패널 내 하단의 `auto mode on`
  부분을 터치/클릭**하면 팝업으로 **변경 가능한 권한모드들**을 보여주고 그중 하나를
  **선택**할 수 있게. 현재 구현/자산: 이 footer 는 **Claude 자체가 패널 PTY 안에**
  그리는 줄이라 pytmux 위젯이 아니다 — 클릭하려면 ① footer 의 **화면상 위치(행/열
  범위)를 찾는 휴리스틱**과 ② 그 영역을 **패널 위 클릭존**으로 등록해야 한다.
  권한모드 파서는 이미 있다: `claude.py:claude_perm_mode(text)`(CL 56591, "auto"/
  "plan"/"bypass"/"default"/None 구분). 모드 전환 키도 있다: 서버
  `_inject_keys(pane, b"\x1b[Z")`(shift+tab backtab, CL 56591)·클라 `client.py:160`.
  Claude 헤더 클릭존 선례: `_claude_header_zones`(`client.py:2404` 에서 등록,
  `:1281` 에서 히트테스트). 구현 방향: ㉠ 서버 `_scan_claude` 가 footer 줄을 찾을
  때 그 **행 인덱스**도 같이 실어 보내거나, 클라가 패널 렌더 셀에서 "shift+tab to
  cycle"/"mode on" 줄을 스캔해 클릭존(`_perm_zone[pid]=(x0,x1,y)`)을 만든다. ㉡
  클릭 시 모드 목록 팝업(`InfoScreen`/전용 모달)을 띄우고, 선택하면 **현재 모드에서
  목표 모드까지 shift+tab 을 필요 횟수만큼** 주입(`claude_perm_mode` 로 footer
  재확인하는 폐루프 — CL 56591 `_maybe_auto_mode` 패턴 재사용). **열린 결정**:
  모드 순환 순서가 Claude 버전 의존이라 "목표까지 N회" 계산을 폐루프로 할지, 팝업
  UI(라디오/리스트)·자동전환(`claude-auto-mode`)과의 관계. **연관**: 권한모드 자동
  전환(CL 56591). **현재는 기록만 — 미구현.**
- ~~**[UI 요청] 패널 내 `Remote Control active` 클릭/터치 → 원격제어 토글 팝업**~~ →
  **CL 56602 에서 해결**(클릭→`InfoScreen` 정보 팝업). 단 **토글은 미구현** — 원격제어
  on/off 는 Claude 데스크탑 앱이 관리하는 기능이라 터미널서 직접 토글할 수단이 없어
  **상태/안내 전용 팝업으로 축소**(요청의 '켜고 끄는 화면'은 토글 수단 부재로 안내로).
  토글 키/명령이 Claude 측에 생기면 그때 주입 추가. 아래는 원 분석 기록.
- **[원래 분석] 패널 내 `Remote Control active` 클릭/터치 → 원격제어 토글
  팝업** — 요청: 패널 안 **`Remote Control active`** 표시를 클릭/터치하면 팝업으로
  **원격 제어를 켜고 끄는 화면**을 보여주기. 현재 구현/제약: 이 표시도 **Claude
  데스크탑 앱의 원격제어 기능**이 패널 PTY 안에 그리는 것이라 pytmux 밖이다 —
  ① 위 auto-mode 항목과 같은 **footer 위치 휴리스틱 + 패널 클릭존** 인프라가 필요
  하고, ② **원격제어 on/off 자체는 Claude/데스크탑 앱의 동작**이라 pytmux 가 직접
  토글할 수 없을 수 있다(키/슬래시 명령으로 가능한지 조사 필요 — Claude 측 단축키
  확인). 관련: 데스크탑 원격제어로 주입된 프롬프트를 화면에서 추출해 헤더에 반영하는
  처리는 이미 함(CL 56592, `claude.py:claude_prompt`). 구현 방향: 클릭존을 만든 뒤
  팝업에 **현재 원격제어 상태(화면 파싱)** 와 가능한 동작을 보이고, 토글 수단이
  있으면 해당 키/명령을 패널에 주입(`_inject_keys`). **열린 결정**: 토글 수단 존재
  여부(없으면 안내/상태표시 전용 팝업으로 축소), 클릭존 인프라를 위 auto-mode 항목과
  공유. **연관**: 위 auto-mode footer 클릭 항목·CL 56592. **현재는 기록만 — 미구현.**
- **[UI 요청, 미구현 · 상위 항목에 흡수됨] 상태줄 `ssh:` 원격 호스트 세그먼트
  클릭/터치 → 서버 정보 팝업(연결상태·속도)** — ⚠️ 이 독립 팝업 요청은 §10 최상단의
  **"하단 상태줄 정보 팝업 통합(REC·토큰·서버이름 탭)"** 항목으로 대체된다 — 서버
  정보는 통합 팝업의 **'서버' 탭**으로 구현. 아래 분석(클릭존·RTT 노출)은 그 탭
  구현에 그대로 쓰인다. 요청: 화면 하단의 **`ssh:` 로 시작하는 원격 서버 표시**
  자리를 클릭/터치하면 팝업으로 **서버 정보·연결 상태·속도(지연)** 등을 보여주는
  정보 팝업을 연다. 현재 구현/자산: 호스트 세그먼트는 상태줄이 이미 별도 런으로
  그린다 — `_render_main` 에서 `kind=="host"` 이고 `self._is_remote` 면 `"ssh:"`
  접두사+`host_style`(error 색)로 렌더(`client.py:1914`). `_is_remote` 는
  `SSH_CONNECTION` 등 환경으로 판정(`client.py:1719`). **다만 시계/날짜와 달리 host
  세그먼트엔 클릭존이 없다**(`_clock_zone`/`_date_zone` 만 있음 — `client.py` 우측
  런 처리부). 응답성/속도 신호는 이미 측정한다: 네트워크 RTT 히스테리시스
  (`_net_degraded`, ping/pong RTT — CL 56593). 구현 방향: ㉠ 우측 런 루프에서 host
  세그먼트의 x 범위로 `self._host_zone=(x0,x1)` 를 만들고(시계/날짜 존과 동형),
  `on_mouse_down` 히트테스트에 추가. ㉡ 클릭 시 `InfoScreen` 으로 서버 정보 팝업:
  `SSH_CONNECTION`/`SSH_CLIENT` 파싱(원격/로컬 IP·포트), 호스트명, **연결상태/속도**
  는 CL 56593 의 RTT(최근 표본·degraded 여부)를 노출 — 단 현재 RTT 표본은 클라가
  히스테리시스 카운터로만 쓰고 **마지막 RTT 값을 보관하진 않으므로**, `_net_last_rtt`
  류 필드를 더해 팝업에 "최근 RTT NNms / degraded" 로 보이게 한다. **열린 결정**:
  표시 항목 범위(IP 노출 수위·민감정보), RTT 이력(최근 N개 그래프 vs 단일 값).
  **연관**: 네트워크 응답성 외곽선(CL 56593)·REC/토큰 정보팝업(InfoScreen 공유).
  **현재는 기록만 — 미구현.**
- **[리팩토링 요청, 부분 구현] 코드를 LLM 친화적인 형태로 리팩토링** — **진행 중**:
  client.py 가 `clientutil`/`clientscreens`/`clientwidgets` 로 4단계 분리됨(CL 56603·
  56610·56611·56616, client.py 4363→2335줄). **server.py 분할 진행 중**(믹스인 방식 —
  Server 가 각 믹스인을 상속, 동작 불변·테스트 게이트): ① **Claude 주입 클러스터 →
  `serverclaude.py` `ServerClaudeMixin`**(CL 56765 — 자동재개·prompt-clear·auto-doc-clear·
  claude-auto-mode·perm-mode 구동). ② **캡처(REC) 클러스터 → `servercapture.py`
  `ServerCaptureMixin`**(CL 56766 — _capture_*·capture_dir·set_capture). ③ **영속/직렬화·
  재시작(execv) 클러스터 → `serverpersist.py` `ServerPersistMixin`**(CL 56768 — layout/
  resume/slots/opts 직렬화·저장·복원 + restart_server/_do_execv). **server.py 2968→2023줄
  (-32%), 믹스인 3개(serverclaude/servercapture/serverpersist).** ④ Claude 화면 스캐닝
  (`_scan_claude`/`_tab_claude`/`_account_token_total`/`_track_prompt` + 상수
  `_HDR_CLAUDE_MISS`/`_DONE_IDLE_FRAMES`)도 `serverclaude.py` 로 모음(CL 56773) —
  flush/IPC 꼬리에서 Claude 로직 제거 완료.** 분리 패턴: 연속
  응집 블록을 `class XxxMixin:` 으로 옮기고 `Server(…Mixin들)` 가 상속(메서드는 self.*·
  다른 메서드를 그대로 참조 → 동작 불변, 헤드리스 232 테스트가 게이트). **코어 멀티
  플렉서(패널 spawn/트리·window/tab/session ops·flush/IPC)는 의도적으로 Server 본체에
  유지** — 이들이 곧 Server 의 핵심이라 분리 이득이 작고, flush/IPC 꼬리의 `_scan_claude` 류는 ④에서 serverclaude 로 모았다. 남은 코어
  (패널 spawn/트리·window/tab/session·flush 루프·IPC handle_client/_handle_cmd)는 Server
  본체 유지 — 결합도 높은 핵심이라 분리 이득이 작다(선택적 후속). 아래는 원 요청.
  요청: 코드를
  **LLM 친화적인 형태로 리팩토링**. 동기(현 상태): 핵심 두 모듈이 **거대 단일 파일**
  이라 LLM 이 한 번에 통째로 읽어야 편집이 가능하고 컨텍스트·충돌 비용이 크다 —
  `client.py` **3963줄**, `server.py` **2569줄**(나머지는 model 856·pty_backend 432
  등으로 작음). LLM 친화 = **작고 단일책임인 파일 경계 + 명시적 의존 + 일관된
  네이밍/주석**으로, LLM 이 필요한 부분만 로드해 안전히 편집하게 함. **기존 토대**:
  §11 "Claude Code 특화 기능 분리 전략"이 이미 분리 목표·충돌 회피·점진 이전 순서를
  제시(§11.1~11.5) — 그 줄기를 일반 리팩토링으로 확장. **제안 방향(예시)**: ①
  `client.py` 분할 — 모달/팝업 스크린(`*Screen`)군, `TabBar`, `MultiplexerView`/
  `_composite`(렌더 합성), 오버레이(clock/calendar/header/tab_close), 입력·마우스
  핸들링, 명령 디스패치(`COMMANDS`/옵션)로 파일 쪼개기. ② `server.py` 분할 — 세션/
  윈도우/패널 트리, flush·`_scan_claude`(Claude 휴리스틱 훅), 프롬프트 추적·클리어
  모드, IPC/클라이언트 핸들러, 자동재개 등. ③ 순수 로직(휴리스틱·집계)은 이미 분리
  사례 있음(`claude.py`·`usagelog.py`·`tokens.py`) — 그 패턴을 더 적용. **제약(필수)**:
  ㉠ **동작 불변**(순수 구조 변경) — 헤드리스 테스트 `py tests/run.py`(현 150개)
  **전부 통과 유지**가 게이트. ㉡ **하위호환 임포트 유지**(`protocol.py` re-export
  선례처럼) 또는 임포트 일괄 갱신. ㉢
  **공유 워크스페이스 충돌 주의**(§9/§11 의 attribution 경고 — Windows 포팅 등 동시
  세션과 같은 파일 동시 편집 회피, 작은 CL 로 점진 이전). ㉣ 서버/클라 구조 변경은
  `kill-server` 재기동 필요. **열린 결정**: 분할 입도(파일 수)·디렉터리 구조(예
  `pytmuxlib/client/` 패키지화)·우선순위(client 먼저 vs server 먼저)·한 CL 범위는
  사용자 확인 또는 별도 설계 필요. **연관**: §11 전체. **부분 구현 — client.py 분할
  완료(CL 56603·56610·56611·56616), server.py 분할 잔여.**
- **[기능 요청, 구현 완료] ✅ Claude idle(작업 완료·비리밋) 시 30초 후 진행상황 문서화 →
  `/clear` 자동 수행(옵션 토글)** — 요청: Claude Code 가 실행 중이고 **리밋 상태가
  아니며 작업이 완료(idle)** 되면, **30초 대기** 후 **현재 세션 진행상황을 문서에
  기록**하고 이어서 **`/clear`** 한다. 이 동작은 **옵션에서 끌 수 있어야** 한다.
  **기존 자산(거의 그대로 재사용)**: "프롬프트 단위 클리어 모드(#9)" 상태기계가
  이미 **doc→/clear** 사이클을 구현한다 — `_pc_advance`(`server.py:361`): phase
  None→문서화 지시 주입(`prompt_clear_message`)→phase doc→`/clear` 주입→phase
  clear. 주입은 `_pc_inject`(`server.py:340`, Claude PTY 에 한 줄+Enter), 토글은
  `set_prompt_clear`/`prompt_clear_mode`, 문구는 `set_prompt_clear_message`(opts.json
  영속). 트리거 지점은 `_scan_claude` 의 busy→idle 경계(`server.py:1945`). **이번
  요청과의 차이(델타)**: ① **자동 트리거** — 기존은 큐(`prompt_clear_queue`)에 쌓인
  명령 완료 시 도는 방식인데, 요청은 **사용자 개입 없이 idle 되면** 자동으로 doc→
  /clear 를 시작. ② **30초 대기** — busy→idle 즉시가 아니라 **30초 디바운스** 후 발화
  (그 사이 사용자가 입력하거나 다시 busy 가 되면 **취소**, idle 유지 시에만 발화).
  타이머를 패널별로 두고 idle 진입 시 무장·이탈 시 해제(asyncio 콜백/만료 검사).
  ③ **리밋 가드** — `_claude == "limit"` 이면 발화 안 함(idle 일 때만; 기존도 busy→
  idle 만 보지만 명시적으로 limit 제외). ④ **옵션 토글** — 새 설정/명령(예
  `auto-doc-clear on|off`, 영속) + (선택) 대기초·문서화 지시문 설정. **열린 결정**:
  ㉠ 기존 `prompt-clear-mode` 를 이 자동 모드로 확장할지 vs **별도 모드** 신설할지,
  ㉡ "문서에 기록"의 대상 문서/지시문(= `prompt_clear_message` 재사용 여부, 어떤
  파일에 적을지), ㉢ 대기시간 고정 30초 vs 설정값, ㉣ 활성 패널만 vs 모든 Claude
  패널 대상. **연관**: 위 "Claude 오토모드 자동전환" 항목(둘 다 idle 감지·자동 키
  주입 줄기). **✅ 구현 완료(코드 확인) — auto_doc_clear 토글·auto_doc_clear_delay=30·set_auto_doc_clear·_adc_timer/_adc_disarm (기존 prompt-clear 기계 재사용, 기본 off).**
- **[UI 요청, 구현 완료] ✅ 토큰 사용량 표시 — 기호와 숫자 사이 한 칸 띄우기** — 요청:
  토큰 사용량 표시(`Σ44.6k`)에서 **기호(`Σ`)와 숫자 사이를 한 칸** 띄워 `Σ 44.6k`
  로. 현재 구현: `_status_strip` 에서 `uparts.append("Σ" + _fmt_tokens(...))`
  (`client.py:1864`) — `"Σ"` 와 숫자가 붙어 있다. 수정: `"Σ "`(공백 추가). 폭 1칸
  늘어나므로 상태줄 폭 예산(`_usage_zone` 클릭존 계산)은 같은 `utext` 기준이라 자동
  반영. **✅ 구현 완료(코드 확인) — clientwidgets 가 "Σ " + num (기호·숫자 사이 한 칸).**
- **[UI 요청, 구현 완료] ✅ `help` 명령에도 자동완성 후보 박스 표시** — 요청: 명령 프롬프트
  에 `help` 을 입력해도 **자동완성 박스가 뜨게** 해 달라(현재 안 뜸). 현재 구현:
  `PromptScreen._refresh_cands`(`client.py:869`)가 입력 첫 토큰으로 `COMMANDS` 를
  **부분일치**해 후보 박스(`#pcand`)를 채운다 — 그런데 **`help` 은 `COMMANDS` 목록에
  없어서**(`COMMANDS` 정의 `client.py:205~`) 일치가 0개라 박스가 숨는다(`help` 자체는
  `client.py:3257` 에서 `help/commands/?/list-commands` 로 처리돼 명령 목록을 열고,
  `?` 입력은 `client.py:921` 에서 `CommandListScreen` 을 띄움). 구현 방향: ① `help`
  (및 별칭 `commands`/`list-commands`)을 `COMMANDS` 항목으로 넣어 부분일치에 잡히게
  하거나, ② `_refresh_cands` 에서 `help`/`?` 를 **특수 처리**해 후보 박스에 전체 명령
  목록(또는 카테고리)을 보이게 한다. `?` 가 즉시 `CommandListScreen` 을 여는 것과의
  관계 정리 필요(help=인라인 후보 박스 vs ?=전체화면 목록). **✅ 구현 완료(코드 확인) — help 가 COMMANDS 에 등록돼 부분일치 후보 박스에 노출.**
- **[UI 버그, 구현 완료] ✅ 명령 프롬프트 표시 시 배경 디밍이 일부 글자에 충분히 적용 안 됨** —
  보고: 명령 프롬프트가 뜰 때 **화면이 어두운 색으로 전환**되는데, 이때 **충분히
  어두워지지 않는 글자들**이 있다. 원인(유력): 배경을 어둡게 하는 방식이 **ANSI
  `dim` 속성**(`Style(dim=True)`, 예 오버레이 `client.py:2453·2494·2722`)이거나
  Textual 모달 백드롭에 기대는데, **`dim` 은 터미널 의존적**이라 ① **bold 글자**(많은
  터미널이 bold 를 밝게 렌더 → dim 상쇄), ② **명시적 fg/bg 색이나 밝은색 글자**는
  충분히 어두워지지 않는다. 구현 방향: ANSI `dim` 의존 대신 **실제 색을 배경 쪽으로
  블렌드한 어두운 색**을 계산해 fg 에 적용(+`bold` 해제)해 터미널 무관하게 균일하게
  어둡게. 명령 프롬프트(PromptScreen) 백드롭 디밍 경로 확인 필요(Textual 모달 dim
  vs 자체 cells 합성). **✅ 구현 완료(코드 확인) — _darken_style 이 ANSI dim 대신 실색 블렌드(+bold 해제)로 모달 백드롭을 균일 디밍.**
- **[UI 요청, 구현 완료] ✅ 배경색이 팝업 색과 같으면 배경을 조금 더 어둡게 해 팝업과 구분** —
  요청: **터미널 배경색이 팝업 배경색과 동일**하면 팝업과 배경의 경계가 안 보인다 —
  이때 **배경을 조금 더 어둡게** 해 팝업과 구분되게 하라. 현재 구현: 팝업 박스 배경은
  `background: $panel`(예 `CommandListScreen #cmdbox`·`InfoScreen #infobox`,
  `client.py:347·634`), 테두리는 `round $accent`. 터미널 기본 배경이 `$panel` 과
  같은 색이면 박스 면이 배경에 묻힌다(테두리만 겨우 보임). 구현 방향: 팝업이 떠 있는
  동안 **배경(뒤 화면)을 한 단계 어둡게**(`$panel-darken-1/2` 상당으로 블렌드)
  덮거나, 반대로 팝업 배경을 배경과 다른 톤으로. 위 "배경 디밍" 항목과 같은 합성
  경로에서 처리 가능(팝업 떠 있을 때 백드롭 어둡게 = 디밍 강화). **연관**: 바로 위
  디밍 항목·팝업 높이 항목들. **✅ 구현 완료(코드 확인) — _darken_style 백드롭 디밍으로 뒤 배경이 팝업($panel)보다 어두워져 구분됨.**
- **[UI 요청, 구현 완료] ✅ `esc :` 로 여는 명령어 프롬프트를 외곽선(테두리)으로 둘러싸기** —
  요청: 화면 바닥에 `esc` 후 `:` 로 여는 **명령어 입력 프롬프트**(스크린샷 하단 `:`)
  를 **외곽선으로 둘러달라**(현재는 테두리 없는 한 줄). 현재 구현: `PromptScreen`
  (`client.py:811`)이 바닥 고정 모달로 `#prow { dock: bottom; height: 1 }` 안에 `:`
  프리픽스(`#pprefix`)+입력(`#pinput { border: none }`)을 한 줄로 그린다(자동완성
  후보 영역 `#pcand` 는 그 위로 펼쳐짐). 구현 방향: 입력 줄을 **`border: round
  $accent`(또는 패널 외곽선과 통일)** 로 감싼다 — 테두리가 위·아래 2행을 더 쓰므로
  `#prow`/입력 컨테이너 `height: 3` 으로 키우고, `align: center bottom`·`dock:
  bottom` 유지(테두리 포함 박스가 바닥에 붙도록). 주의: ① `:` 프리픽스를 테두리
  안쪽에 두기(현재 `#pprefix` 별도 위젯), ② 자동완성 후보(`#pcand`)와의 적층 순서·
  테두리 겹침 조정(후보는 박스 위에), ③ command 외 용도(이름변경/검색 등 같은
  `PromptScreen`)에도 테두리를 줄지 결정 — command 만 vs 전체. **✅ 구현 완료(코드
  확인) — PromptScreen #prow 가 border: round $accent (height 3, 프리픽스·입력 안쪽).**
- **[UI 요청, 구현 완료] ✅ 명령 목록 등 팝업 — 세로 길이를 "최대 내용" 상태에 고정하고
  아래쪽 비우기(리사이즈 떨림 방지)** — 요청: 팝업이 **내부 내용 양에 따라 상하
  길이가 달라진다**(스크린샷=명령 목록, ←→ 로 카테고리 전환 시 항목 수가 달라 박스
  높이가 출렁임). **내용이 가장 많은 상태에 높이를 맞추고**, 항목이 적은 상태에선
  **아래쪽을 빈 채로** 둬 높이를 고정하라. 현재 구현: `CommandListScreen` CSS
  (`client.py:347`)가 `#cmdbox { height: auto; max-height: 80% }`,
  `#cmds { height: auto; max-height: 1fr }` 라 **현재 카테고리 항목 수에 따라 box 가
  auto 로 늘었다 줄었다** 한다(`_render_cat` 에서 카테고리 교체 시 `lv.clear/extend`).
  구현 방향: ① 모든 카테고리 중 **최대 항목 수**(또는 최대 콘텐츠 높이, +탭바·여백)를
  미리 계산해 `#cmds`/`#cmdbox` 에 **고정 높이**로 박는다(`height: <max>` 또는
  min-height=max-height 동일값). ② 작은 카테고리는 ListView 아래 **빈 공간**이
  남아(스크롤 없이) 높이 유지 — 단 `max-height: 80%`(또는 위 InfoScreen 항목의 탭
  패널 한도)는 넘지 않게 클램프(가장 큰 카테고리가 한도 초과면 그때만 스크롤). **주의**:
  Textual 행 높이는 가변(설명 줄바꿈)일 수 있으니 "항목 수"보다 **렌더 높이**기준이
  안전. 같은 떨림 방지를 옵션 팝업 등 다른 가변 팝업에도 적용할지 검토. **연관**: 아래
  InfoScreen 팝업 높이 항목. **✅ 구현 완료(코드 확인) — #cmds 높이를 min(최대 카테고리 항목수, _CMDS_MAX_ROWS)로 고정 + max-height 85% (전환 시 박스 높이 불변).**
- **[UI 요청, 구현 완료] ✅ 프롬프트 히스토리 등 InfoScreen 팝업 — 배경 탭 패널 안인 한
  상하로 더 길어지게** — 요청: 이 팝업(스크린샷=프롬프트 히스토리)은 **배경의 탭
  패널 영역 안에 있는 한 상하로 더 길어져도** 된다(더 많은 항목을 한 번에 보이게).
  현재 구현: `InfoScreen` CSS(`client.py:634`)에서 박스 `#infobox { height: auto }`,
  내용 `#info { height: auto; max-height: 75% }` 로 **75% 로 캡**돼 있고 화면 중앙
  정렬(`align: center middle`). 그래서 항목이 많아도 75% 이상 안 커진다. 구현 방향:
  `#info` 의 `max-height` 를 **탭 콘텐츠 영역(탭바 아래 ~ 상태줄 위) 한도까지** 키운다
  — 예 `max-height: 90%`(상태줄/탭바 침범 안 하게 여유) 또는 박스 자체를 그 영역에
  맞춰 키움. **주의**: ① `InfoScreen` 은 프롬프트 히스토리뿐 아니라 캡처(REC)·토큰
  사용량 팝업도 공유하므로(같은 클래스) 모두 영향 — 의도와 맞는지 확인. ② "배경 탭
  패널 안" 경계 = 탭바·하단 상태줄을 가리지 않도록(전체 화면 100% 가 아니라 패널
  콘텐츠 높이 기준). ModalScreen 은 앱 전체를 덮으므로 % 기준이 화면 전체라면 탭바/
  상태줄 높이만큼 빼는 보정이 필요. ③ 폭(`max-width: 66`)은 요청 대상 아님(상하만).
  **✅ 구현 완료(코드 확인) — #infobox max-height: 95% 로 상향(이전 75%).**
- ~~**[기능 요청] Windows 환경 호환성·성능 테스트 리포트 생성 스크립트**~~ →
  **해결** — `scripts/win_report.py`(환경·호환성·성능 리포트) + 3-OS 벤치마크 하네스
  `scripts/bench.py`·`tests/test_bench.py`·benchmark CI(CL 56656·56660 계열). 아래는
  원 요청·분석. 요청: **Windows 환경에서 호환성 및 성능 테스트 리포트를 작성하는 스크립트**가
  필요. 한 번 실행하면 환경 정보 + 호환성 통과/실패 + 성능 수치를 모은 리포트를
  출력. 재사용/재실행 시 비교 가능해야. 기존 자산(재사용·통합 대상): ① **헤드리스
  테스트 러너** `tests/run.py`(`py tests/run.py`, 현재 150/150) — 호환성 회귀. ②
  **Windows 포팅 import 가드** `tests/test_windows_port.py`(fcntl/termios 부재
  시뮬레이션). ③ **feed/render 처리량 프로파일러** `poc/feed_profile.py`(feed MB/s,
  FEED_SLICE 슬라이스 지연 ms, alt-screen vs main-screen, cProfile) — 성능 핫패스.
  ④ 문서 `docs/WINDOWS_PORT.md`(§7-d 실 Windows 11 검증 절차)·`docs/
  ENV_SETUP_WINDOWS.md`·`install.ps1`. **제안 스코프**: A) **환경 수집** — OS/빌드,
  Python 버전(`pythonw.exe` 유무), 터미널(conhost/Windows Terminal/WezTerm, Kitty
  키보드 프로토콜 지원 여부), PTY 백엔드(ConPTY/`pywinpty` 버전), 의존성
  (`requirements.txt`, `wcwidth`) 설치 상태. B) **호환성** — `tests/run.py` 전체
  실행 결과 집계, import 가드, PTY spawn/resize/입력 왕복(라이브 attach 스모크),
  키/마우스 인코딩(§10 Shift+ESC·마우스 1006/X10), 시그널/포그라운드 가드. C)
  **성능** — `feed_profile.py` 수치(feed MB/s·슬라이스 지연·event loop 차단 ms,
  §9 의 "56.3ms→13.6ms" 비교 기준), render+`json.dumps`, (가능하면) IPC RTT. D)
  **출력** — Markdown 리포트(환경·요약 표·PASS/FAIL·수치·이전 실행 대비). **열린
  결정(사용자 확인 필요)**: ① 리포트 포맷/위치(예 `reports/win-report-<날짜>.md` vs
  stdout), ② 신규 단일 스크립트(예 `scripts/win_report.py`) vs 기존 run.py/
  feed_profile.py 래핑, ③ 호환성 범위(헤드리스만 vs 라이브 attach 스모크 포함), ④
  성능 임계/판정선 둘지(회귀 게이트) 또는 측정만. **구현 완료 — win_report.py +
  benchmark CI.**
- ~~**[기능 요청] 네트워크 응답성 저하 시 패널 외곽선을 빨간색으로 표시**~~ →
  **CL 56593 에서 해결**(`_net_degraded` ping/pong RTT 히스테리시스 → degraded 시
  외곽선 error 색; 회복은 CL 56601 워치독 재접속). 아래는 원 요청·분석.
  요청: 네트워크 속도가 느려져 **응답성이 낮아지면 패널 외곽선(테두리)을 빨간색**
  으로 그려 사용자에게 알리고, **응답성이 개선되면 원래 색으로 복귀**한다. 현재 구현:
  패널 테두리 색은 `_draw_box`(`client.py:2587`)에서 `active_box`(=primary, 파랑)·
  `inactive_box`(=grey42)로 결정된다. **응답성/지연 측정 수단은 아직 없다**(검색:
  latency/rtt/ping 없음). 구현 방향: ① **응답성 신호 산출** — 클라↔서버 IPC
  (`ipc.py`)에 **주기적 하트비트/핑을 보내 왕복 지연(RTT)** 을 재거나, 입력 송신→
  화면 갱신 왕복 지연, 또는 출력 드레인 백프레셔 상태(§9·아래 ssh 반응성 항목의
  pyte feed 천장/슬라이스 드레인)를 신호로 삼는다. ② **임계/히스테리시스** — RTT 가
  임계 초과로 N회 지속되면 "degraded" 플래그 ON, 임계 미만 M회 지속되면 OFF(깜빡임
  방지). ③ **색 적용** — degraded 면 `active_box`/`inactive_box` 를
  `theme_color(self, "error")`(빨강)로 덮어 모든(또는 활성) 패널 테두리를 빨갛게,
  회복되면 원복. 주의: 시간 측정은 클라이언트 측 시계로(서버 왕복), 단일 패널만이
  아니라 전체가 같은 ssh 채널을 타므로 보통 전 패널 공통 상태. **연관**: 아래
  "**Windows→ssh→원격 macOS … ssh 반응성 급락**" 항목과 같은 줄기(그 지연을
  외곽선 색으로 가시화하는 것). **구현 완료 — CL 56593(detection)/56601(회복).**
- ~~**[버그·조사] 탭이 일시적으로 녹색으로 보일 때가 있음**~~ → **해결**(플리커
  디바운스). busy↔idle 가 한 프레임 깜빡일 때 done(녹색)이 잘못 서던 오검출을
  `_scan_claude` 에 `_was_busy`+`_idle_frames` 디바운스로 막았다 — busy 를 본 적이
  있고(`_was_busy`) idle 이 `_DONE_IDLE_FRAMES`(=3) 프레임 **연속 안정**일 때만
  `has_claude_done` 을 세운다(`server.py:2154-2165`, `model.py:329`). 의도된 완료
  알림(monitor-claude)은 유지하되 한 프레임 깜빡임만 걸러낸다. 회귀
  `test_done_flag_debounced_against_flicker`. **서버 변경 → kill-server 재기동.**
  아래는 원 분석 기록.
- **[원래 보고·분석] 탭이 일시적으로 녹색으로 보일 때가 있음** — 보고: 탭이
  **일시적으로 녹색**으로 보일 때가 있다(스크린샷: 비활성 탭 `0:claude!` 가 녹색).
  유력 원인: **비활성 탭 Claude 작업 완료 알림(#22)** — `_scan_claude`
  (`server.py:1938`)가 **비활성 탭**의 busy→idle 전이를 감지하면
  `t.has_claude_done = True` 로 두고, 클라이언트 탭바가 그 탭을 **녹색**(`done_st =
  bgcolor=success`, `client.py:1565,1592`)으로 칠한다. 이 플래그는 그 탭을 보면
  (활동/전환 시) 해제된다(`server.py:625`). 즉 **설계상 의도된 알림**일 수 있다 —
  먼저 사용자 의도 확인 필요: ① 알림 자체가 불필요/거슬림 → `monitor-claude` 류
  토글로 끄거나 색을 약화, ② "일시적 깜빡임"이 문제 → busy↔idle 가 빠르게 토글하며
  done 이 잠깐 떴다 사라지는 **오검출/플리커**일 수 있음(상태 디바운스 부재). 후자라면
  `_claude` 상태 판정(`claude.py:claude_state`)이 한 프레임 흔들릴 때 done 이 잘못
  서는 것 — `_hdr_claude` 처럼 **busy→idle 전이에 디바운스**를 두거나, idle 이 연속
  N프레임 유지될 때만 done 을 세우는 보정이 필요. **조사 항목: 의도된 알림인지 vs
  플리커 버그인지 사용자 확인 후 분기. 현재는 기록만 — 미구현.**
- ~~**[버그] Claude 데스크탑 앱(원격 제어)에서 입력한 프롬프트가 상단 프롬프트
  헤더에 반영 안 됨**~~ → **해결**(화면 transcript 파싱). 입력 경로(`_track_prompt`)
  를 안 거친 원격 주입 프롬프트를 `_scan_claude` 가 화면에서 추출해 반영한다 —
  신설 파서 `claude.py:claude_prompt(text)`(transcript 의 "> 내용" 줄, 라이브 입력박스
  하단 `_PROMPT_TAIL_SKIP` 줄 제외)가 잡은 줄이 `last_prompt` 와 다르고 최근 히스토리
  (`prompt_history[-5:]`)에도 없을 때만 `last_prompt`/`prompt_history` 갱신
  (`server.py:2141-2149`). 로컬 입력은 `_track_prompt` 가 이미 히스토리에 남겨 가드에
  걸려 **이중 기록 안 됨**. 회귀 `test_screen_prompt_reflects_remote_injected`(server)·
  `test_claude_prompt`(claude). **서버 변경 → kill-server 재기동.** 아래는 원 분석 기록.
- **[원래 보고·분석] Claude 데스크탑 앱(원격 제어)에서 입력한 프롬프트가 상단 프롬프트
  헤더에 반영 안 됨** — 보고: 프롬프트를 **Claude 데스크탑 앱으로부터** 입력하면
  (화면의 "Remote Control active") 상단 프롬프트 표시 바(Claude 헤더)가 **업데이트
  되지 않는다**. 업데이트되어야 함. 원인: 헤더는 `pane.last_prompt`(`model.py:260`)
  를 표시하는데, 이 값은 **입력 바이트에서만** 잡힌다 — `_track_prompt`
  (`server.py:2433`)가 사용자가 친 키/붙여넣기(`server.py:1016`·`2418` 입력 경로)를
  파싱해 Enter 시 확정한다. **데스크탑 앱이 원격 제어로 주입한 프롬프트는 pytmux 의
  PTY 입력 경로를 거치지 않으므로** `_track_prompt` 가 못 보고 `last_prompt`·
  `prompt_history` 가 안 바뀐다(헤더·히스토리 팝업 모두 영향). 구현 방향: **화면
  출력에서 프롬프트를 추출**하는 휴리스틱 추가 — `_scan_claude`(`server.py:1856`)가
  이미 매 프레임 `txt = screen.display` 를 훑어 상태/사용량/계정을 갱신하므로, 같은
  자리에서 **Claude transcript 에 렌더된 최신 사용자 프롬프트 줄**을 파싱해
  (`claude.py` 에 `claude_prompt(text)` 류 파서 신설) 입력 경로로 안 잡힌 경우
  `last_prompt`/`prompt_history` 를 갱신한다. 주의: ① 입력 경로(`_track_prompt`)와
  **이중 기록 방지**(같은 줄 중복 append 가드는 있으나 화면 파싱과 타이밍 경합 주의),
  ② busy 중 승격 로직(`pending_prompts`, `server.py:1934`)과 충돌 없게 — 화면에서
  본 프롬프트는 이미 "처리 중"일 수 있음, ③ 화면 파싱은 best-effort(Claude UI 포맷
  의존), 오검출 시 헤더가 엉뚱한 줄을 잡지 않게 보수적 매칭. **현재는 기록만 — 미구현.**
- **[UI 요청, 구현 완료] ✅ 탭바 [+] 새 탭 버튼 — 탭과 한 칸 떨어뜨리되 그 빈칸은 터미널
  배경색** — 요청: 탭바 오른쪽 녹색 `[+]` 버튼이 마지막 탭과 **한 칸 떨어져** 있어야
  하고, **그 빈칸은 (녹색이 아니라) 터미널 배경과 같은 색**이어야 한다. 현재 구현:
  `TabBar._entries`(`client.py:1518`)가 `addtxt = "  [+] "`(앞 공백 2칸 포함)를
  **하나의 `("add", …)` 엔트리**로 만들고, `render_line`(`client.py:1148`~)에서
  `kind == "add"` 이면 **전체를 녹색 `add_st`** (`bgcolor=success`)로 칠한다 — 그래서
  **앞 공백(간격)까지 녹색**으로 칠해진다(스크린샷의 증상). 구현 방향: 간격 칸을
  `add_st` 에서 떼어내 **터미널 기본 배경(`base`, `bgcolor=None`)** 으로 그린다 —
  ① `_entries` 에서 `("addgap", None, " ")` + `("add", None, "[+] ")`로 **분리**
  하거나(권장), ② `render_line` 의 add 세그먼트 처리에서 선행 공백만 `base` 로
  쪼개 Segment 를 2개로 낸다. 주의: **기하 일관성** — `_entries`/`render_line`/
  `active_tab_xrange` 가 같은 폭을 공유하므로(주석 #23), 폭 예산(`addtxt` 길이로
  `mid_w` 계산)·클릭 존(`_zones`, `("add", …)` 히트테스트)이 분리 후에도 맞아야
  한다(gap 칸은 클릭 무시 = lead 처럼). 비활성 탭/여백이 터미널 배경을 따르는
  메커니즘(`base = Style(bgcolor=None)`)은 이미 있으므로 그걸 재사용. **✅ 구현
  완료(코드 확인) — TabBar 가 ("addgap", None, …) 로 간격칸을 터미널 배경으로 분리.**
- **[기능 요청, 구현 완료] ✅ 하단 토큰 사용량 표시 — 지속 표시 + 계정별 전체 세션 합계** —
  요청 2가지: ① **사라지지 않게(지속)** — 하단 상태줄의 토큰 사용량(`Σ…`/`ctx …`)이
  **사라질 때가 있다**(예: 활성 패널이 Claude 가 아니거나, 한 프레임 파싱 실패 시).
  **한 번 표시되기 시작하면 계속 표시**하라(마지막 값 유지, 깜빡임/소실 방지). ②
  **계정별 · 전체 세션 합계** — 지금은 **활성 패널 한 개의 세션 누적**만 보여 준다
  (서버: `claude_tokens = win.active_pane._session_tokens`, `claude_usage =
  win.active_pane._claude_usage`, **둘 다 활성 패널이 Claude 일 때만**,
  `server.py:1980,1984`). 요구는 **하이라이트된(활성) 패널의 계정**을 기준으로 **그
  계정에 속한 모든 세션/패널의 토큰을 합산**해 표시하는 것 — 활성 패널이 개인 계정이면
  전체 세션의 개인 계정 합계, 팀 계정이면 팀 계정 합계. 현재 구현 자료: 패널별 계정은
  `_claude_account`(`model.py:257`, 휴리스틱 `claude.py:120 claude_account`,
  이메일 별칭/조직/플랜으로 추정), 패널별 누적은 `_session_tokens`. 이미 **계정별
  집계 로직과 영속 로그**가 있다(`usagelog`, `server.py:1364 make_record(account=…)`,
  클라 `TokenLogScreen` 이 계정×시간/일/월 집계). 구현 방향: 서버 layout 송신부
  (`server.py:1980`)에서 **활성 패널의 `_claude_account` 를 키로, 그 계정과 같은 모든
  패널(전체 세션 순회)의 `_session_tokens` 를 합산**해 `claude_tokens` 로 보내고,
  표시 텍스트엔 계정 식별자도 곁들임(예 `Σ12k @개인`). 지속 표시는 클라
  (`client.py:1804-1808`)에서 `claude_usage`/`claude_tokens` 가 None/0 으로 와도
  **마지막 비어있지 않은 값을 보존**(패널/계정 바뀌면 갱신)하거나, 서버가 항상 계정
  합계를 채워 보내는 쪽으로. 표시 위치: `_status_strip` 의 `uparts`(`client.py:1858`).
  **주의**: 계정 추정이 None 인 동안의 폴백(예 "unknown" 묶음), 세션 종료 패널의
  누적을 합계에 계속 포함할지(영속 로그 합계 vs 살아있는 세션 합계) 정책 결정 필요.
  **✅ 구현 완료(코드 확인) — _carry_tokens_on_close(닫혀도 합계 유지) + 활성 패널 계정 기준 _all_panes 토큰 합산.**
- ~~**[기능 요청] Claude Code 패널 감지 시 권한모드를 자동으로 "오토모드"로
  전환**~~ → **CL 56591 에서 해결**(`claude-auto-mode on|off` 토글·영속 +
  `_maybe_auto_mode` 의 backtab `\x1b[Z` 폐루프 + `claude_perm_mode` 파서). ⚠️ **단
  모바일(좁은 폭)에서 자동전환 미동작** 보고는 이 절 최상단 버그 항목(CL 56757)으로
  추적 중. 아래는 원 요청·분석. 요청: 패널에서 Claude Code 가 떠 입력 대기(idle) 상태가 되면, 현재
  권한모드가 오토모드인지 확인하고 **오토모드가 아니면 자동으로 오토모드로 전환**
  한다. **단, 이 자동전환은 pytmux 설정 토글이 켜져 있을 때만 동작**하고, 토글이
  꺼져 있으면 절대 전환하지 않는다(사용자 클릭 한 정보 기준: 트리거=패널서 Claude
  감지 시 / 가드=pytmux 설정 토글). 구현 메모: ① **감지** — `claude.py` 가 이미
  Claude idle 권한모드 footer 를 잡는다(`claude.py:41,54`: `"shift+tab to" in low or
  "mode on (shift" in low`). 오토모드 ON 여부는 footer 문자열 `⏵⏵ auto mode on
  (shift+tab to cycle)` 매칭으로 판별 가능(현재는 idle/busy 만 구분하므로 **권한
  모드 종류(default/auto/plan)를 구분하는 파서 추가** 필요). ② **전환** — 오토모드가
  아니면 해당 패널 PTY 로 `shift+tab`(backtab `\x1b[Z`, `client.py:160`)을 보내
  권한모드를 순환시킨다. Claude 의 순환 순서가 고정이 아닐 수 있으니 **footer 를
  재확인하며 auto 가 될 때까지(최대 N회) 순환**하는 폐루프가 안전. ③ **가드 토글** —
  새 설정/명령 추가(예 `claude-auto-mode on|off`, 영속 opts.json). 기본값·중복 전송
  방지(한 번 auto 로 맞췄으면 재전송 안 함, 패널별 상태 기억) 필요. ④ **주의** —
  자동 키 주입은 사용자가 타이핑 중이면 방해될 수 있으니 idle 확정 시점에만, 패널당
  1회로. 서버/클라 중 어디서 키를 주입할지 결정 필요(상태 권위는 서버, 키 주입 경로는
  클라 `send_input`/서버 PTY write). **구현 완료 — CL 56591(자동전환)/56602(수동
  목표). 모바일/default 미동작 버그는 CL 56761 에서 해결(default footer 인식).**
- ~~**[UI 요청] 패널 출력 캡처(REC) 정보 팝업 — 팝업 바깥을 클릭하면 닫기**~~ →
  **해결** — `InfoScreen.on_click` 이 박스(`#infobox`) 바깥(백드롭) 클릭 시 `dismiss(None)`
  (REC·프롬프트 히스토리·토큰 팝업 공통). 아래는 원 요청·분석.
  요청: 패널 출력 캡처 팝업이 떠 있을 때 **팝업 박스 바깥 영역을 누르면 닫히게** 한다.
  현재 구현: 캡처 정보 팝업은 `InfoScreen`(`client.py:626`)으로, REC 클릭존
  (`_rec_zone`)에서 열린다(`client.py:2321`, title="패널 출력 캡처(REC)"). 현재
  `InfoScreen.on_click`(`client.py:673`)은 **닫기 [x](`#infoclose`) 클릭만** 처리하고
  배경 클릭은 무시한다. 구현 방향: `on_click` 에서 클릭 위젯의 조상 체인에 `#infobox`
  가 없으면(=박스 바깥/백드롭 클릭) `self.dismiss(None)`. ModalScreen 이라 배경
  클릭이 스크린으로 들어온다. **주의**: `InfoScreen` 은 캡처 팝업뿐 아니라 프롬프트
  히스토리·토큰 사용량 팝업도 공유하므로(같은 클래스), 바깥-클릭-닫기를 넣으면 **세
  팝업 모두**에 적용된다 — 의도와 맞는지 확인(보통 바람직). **구현 완료 —
  InfoScreen.on_click 백드롭 닫기.**
- **[UI 요청, 구현 완료] ✅ 패널 중앙 달력 오버레이에 외곽선(테두리) 그리기** — 요청:
  `calendar-mode` 로 패널 중앙에 달력을 띄울 때 **달력 그리드 주변에 외곽선(박스)**
  을 그린다. 현재 구현: `_draw_calendar_overlay`(`client.py:2485`)가 그리드를 패널
  중앙(`ox/oy`, `grid_w`=20, `nlines`=2+주수)에 `_put_cell` 로 찍지만 **테두리가
  없다**. 구현 방향: 그리드 영역(`ox-1 .. ox+grid_w`, `oy-1 .. oy+nlines`)에 1칸
  패딩을 두고 round/box 글리프(`╭─╮│╰╯`)를 `_put_cell` 로 둘러 그린다. 패널이 좁아
  그리드가 안 들어가 단순 날짜(`%Y-%m-%d`)로 폴백하는 경로(`client.py:2531`)도 테두리
  적용할지 결정. 테두리가 패널 경계(W/H)를 넘지 않게 클리핑, 뒤 배경 dim 과의 z-순서
  유지. **✅ 구현 완료(코드 확인) — _draw_calendar_overlay 가 ╭─╮│╰╯ 박스를 그림(pw>=grid_w+2·ph>=nlines+2 일 때).**
- **[UI 요청, 구현 완료] ✅ 패널 우상단 닫기 [x] 버튼을 패널 외곽선 *안쪽*으로 이동** —
  요청: 현재 닫기 [x] 가 패널 **외곽선(테두리) 위**에 얹혀 있는데, 이를 **외곽선
  안쪽(콘텐츠 영역 안)** 으로 옮겨 달라. 현재 구현: `_draw_tab_close`
  (`client.py:2747`)가 현재 탭(윈도우) 닫기 [x] 를 **콘텐츠 영역 맨 윗행(row 0,
  곧 상단 테두리 행)** 의 오른쪽 끝(`bx0 = W-3`, 열 `W-3..W-1`)에 그리고
  히트존을 `self._tab_close_zone = (W-3, W, 0)` 으로 둔다. 즉 지금은 **테두리 줄에
  겹쳐** 그려진다. 구현 방향(택1): ① 버튼을 **한 행 아래(row 1)** 로 내려 테두리
  안쪽 첫 콘텐츠 행 오른쪽 끝에 그리기 — 단, 그 자리는 **Claude 헤더(내용 첫 행)·
  시계/달력 오버레이**와 겹치므로(같은 파일 주석이 row 0 을 쓴 이유) 겹침 회피
  필요(헤더 예약 행과의 z-순서·오프셋 조정, §4 `_composite` 순서 참고). ② 열만
  안쪽으로 한 칸 당겨 테두리 모서리 글자를 피하기. ③ 좌표를 **활성 패널의 실제
  외곽선 기준**으로 잡아(전역 W/H 가 아니라 active pane 의 x/y/w/h) 그 패널
  테두리 안쪽 우상단에 그리기 — 분할(split) 상태에서 더 자연스럽다. 클릭 처리는
  `client.py:1280` 부근(`_tab_close_zone` 히트테스트)을 새 좌표에 맞춰 갱신.
  주의: 좁은 화면에서도 항상 보이게(고정폭 [x]) 유지, `markup=False` 규약 유지
  (1fb8fbe 처럼 `[x]` 가 마크업으로 소실되지 않게). **✅ 구현 완료(코드 확인) — _draw_tab_close 가 활성 패널 콘텐츠 첫 행(테두리 안쪽) 우상단에 그림.**
- ~~**[버그·동작변경 요청] ESC 모드 종료 시 ESC 가 패널로 새지 않게 — 패널
  ESC 전달은 Shift+ESC 일 때만**~~ → **CL 56632 에서 구현**(`_handle_esc_mode` 의
  `k=="escape"` 분기에서 `send_input(b"\x1b")` 제거, `_exit_esc()` 만 — 더블탭 ESC 의
  패널 전달 폐지). 회귀 `test_double_escape_exits_mode_without_pane_esc`(더블탭=전달
  없이 모드 종료)+`test_shift_escape_sends_esc_to_pane`(Shift+ESC 만 `\x1b` 전달)로
  기존 `test_double_escape_sends_esc_to_pane` 를 교체, 191 passed. 아래는 원래 보고/
  분석(참고): — 보고: 단독 ESC 로 esc(명령) 모드에 진입했다가
  **ESC 를 다시 눌러 esc 모드를 빠져나올 때 그 ESC 가 활성 패널로 전달된다**. 전달되면
  안 된다 — 패널에 실제 ESC(`\x1b`)를 보내는 통로는 **항상 Shift+ESC 일 때만**이어야
  한다. 현재 구현: `_handle_esc_mode` 의 `k == "escape"` 분기(`client.py:2341~2349`)가
  **의도적으로** `self._exit_esc()` 뒤에 `self.send_input(b"\x1b")` 로 패널에 ESC 1회를
  보낸다(="ESC 더블탭 → 앱에 ESC 1회"). 이는 바로 아래 **CL 56572 (a)** 가 Shift+ESC
  가 터미널 수식 인코딩 한계로 안 먹는 환경(conhost/일부 WT·ssh, Kitty 프로토콜/
  modifyOtherKeys 미지원)을 위해 댄 **터미널-비의존 ESC 통로**였는데, 이번 요청은 그
  결정을 **뒤집는다** — 더블탭 ESC 는 ESC 를 전달하지 말고 **모드만 빠져야** 한다.
  구현 방향: 그 분기에서 `self.send_input(b"\x1b")` 를 **제거**하고 `self._exit_esc()`
  만 남긴다(= i/enter/그 외 키와 동일하게 "전달 없이 종료"). 그래도 패널 ESC 전달은
  **Shift+ESC**(`SPECIAL["shift+escape"] = b"\x1b"` + on_key normal `shift+escape`
  패스스루, `client.py:162`)와 **`send-escape` 명령(별칭 send-esc, CL 56572 (b))** 이
  남으므로, 더블탭만 없애도 56572 가 막으려던 환경엔 대체 통로가 남아 완전 회귀는
  아니다. **주의**: 회귀 테스트 `test_double_escape_sends_esc_to_pane`(CL 56572)이
  **현재(전달) 동작을 고정**하므로 구현 시 함께 갱신/삭제해야 하고, 이는 바로 아래
  Shift+ESC 항목·CL 56572 와 **직접 충돌하는 결정 변경**임을 명시할 것. 클라이언트
  전용(attach 재실행). **→ CL 56632 에서 구현됨.**
- ~~**[버그·환경 의존] Windows→ssh→원격 macOS Claude Code 에서 Shift+ESC 로 ESC 를
  못 보냄**~~ → **CL 56572 에서 대응(①: 터미널-비의존 통로 2개 추가).** Shift+ESC 가
  안 먹는 근본 원인(터미널이 ESC 에 Shift 수식을 인코딩 못 함)은 코드로 못 고치므로,
  **수식 인코딩에 의존하지 않는 ESC 전달 통로**를 댔다: (a) **ESC 더블탭** — esc 모드
  에서 ESC 를 한 번 더 누르면 활성 패널에 실제 ESC(`\x1b`) 1회를 보내고 모드를 빠진다
  (`_handle_esc_mode` 에 `k=="escape"` 분기; 단독 ESC=모드 진입은 그대로, 모드만 빠질
  땐 i/enter/그 외 키). **(⚠️ 이 더블탭 통로 (a) 는 이후 CL 56632 에서 폐지됨 — 위
  "ESC 모드 종료 시 ESC 가 패널로 새지 않게" 항목 참조. ESC 전달은 (b)/Shift+ESC 로.)**
  (b) **`send-escape` 명령**(별칭 `send-esc`, COMMAND_NOARG·
  COMMANDS 노출) — 한 키에 `bind-key <key> send-escape` 로 전용 ESC 키를 만들 수 있다
  (기존 `send-keys Escape` 의 한 토큰 단축). 회귀 테스트 `test_double_escape_sends_esc_
  to_pane`·`test_send_escape_command`. 클라이언트 전용(attach 재실행). 아래는 원래
  분석(참고):
- **[원래 보고·분석] Windows→ssh→원격 macOS Claude Code 에서 Shift+ESC 로
  ESC 를 못 보냄** — 보고: Windows 에서 pytmux 실행 후 ssh 로 원격 macOS 에 붙어
  Claude Code 를 쓸 때 **Shift+ESC 가 동작하지 않아 ESC(인터럽트/입력 취소)를 앱에
  보낼 수 없다**. 배경: pytmux 는 **단독 ESC 를 명령(esc) 모드 진입**에 쓰므로 셸/앱
  으로 전달하지 않는다(`on_key` normal: `event.key == "escape"` → esc 모드). 앱에
  ESC 를 보내는 통로로 **Shift+ESC**(`SPECIAL["shift+escape"] = b"\x1b"`,
  `client.py:162`)를 둔다 — Shift+ESC 면 esc 모드로 안 빠지고 패널에 `\x1b` 를 그대로
  전달(오버레이가 떠 있으면 그것부터 닫고, 없으면 전달: `client.py:3651`). **원인
  (유력)**: 레거시 터미널(conhost·일부 Windows Terminal 설정)은 **Escape 키에 Shift
  수식을 인코딩하지 못한다** — Kitty 키보드 프로토콜/`modifyOtherKeys` 미지원이면
  Shift+ESC 가 그냥 `ESC` 로 도착한다. 그러면 pytmux 가 `event.key == "escape"` 로
  보고 **esc 모드로 진입**해 버려 ESC 가 앱(Claude)에 영영 안 간다(맥 로컬 터미널은
  수식 인코딩이 돼 정상 — 증상이 환경 의존인 이유). 마우스 휠 1007 건(아래)과 같은
  부류의 **상위 터미널 키 인코딩 한계**로 의심. 검증: `set mouse-debug on` 의
  `_log_key`(내비게이션 키 로깅)처럼 키 진단을 켜 Windows 에서 Shift+ESC 가
  `shift+escape` 로 오는지 `escape` 로 오는지 확인하면 확정된다. 가능한 대응(미구현,
  택1): ① **ESC 전달 전용 바인딩/명령** 제공(예 `prefix` + 키, 또는 esc 모드에서 한 번
  더 ESC → 앱으로 1회 ESC 전달) — 터미널 수식 인코딩에 의존하지 않음(가장 확실). ②
  Windows 에서 Kitty 키보드 프로토콜 활성 터미널(Windows Terminal 최신/WezTerm 등)
  사용 안내. ③ esc 모드 진입에 디바운스/타임아웃을 둬 곧이어 키가 안 오면 앱으로 ESC
  를 흘리는 휴리스틱(오작동 위험으로 비권장). **현재는 미해결 — 기록만.**
- **[버그·성능, 완화됨 · 근본(feed 스레드) 잔여] Windows→ssh→원격 macOS Claude Code
  사용 중 수 분 내 ssh 반응성 급락** — **요약**: 정량 프로파일로 원인 2건 확정(아래
  ★) 후 **대응 ①(드레인 중 GC 비활성)·②(alt-screen 풀스크린 리페인트 코얼레싱)
  구현 완료** → 입력 스파이크(82ms→4.5ms)·feed 부하 대폭 감소. **남은 레버는 대응
  ③ feed 별도 스레드 하나**인데, 이는 단일 asyncio 루프 핫패스를 갈아엎는 큰 공사인
  데다 증상이 **Windows→ssh 환경 의존이라 헤드리스/로컬 macOS 에서 재현·검증 불가** —
  검증 수단 없이 블라인드로 핫패스를 바꾸는 위험이 이득보다 커 **의도적으로 보류**
  (싼 완화 2건이 이미 랜딩). 실 Windows 박스에서 ①②로도 부족하다는 측정이 나오면
  그때 ③ 착수. 보고: Claude Code 를 쓰다 보면 **몇 분 안에 반응성이 극도로 나빠
  진다**. pytmux 인터페이스 자체(패널 전환·명령 등)는 느리지 않은데 **ssh 의 반응성
  (원격으로 가는 키 입력·원격에서 오는 출력)**이 느려져 아무 조작도 못 하게 된다.
  분석(유력): pytmux 의 단일 asyncio 루프는 PTY 출력을 **pyte 로 feed** 하는데 이게
  순수 파이썬이라 **~1.2 MB/s 가 천장**이다(§9 CL 56558/56560). Claude Code 는 작업
  스피너·토큰 카운터를 **매 프레임 풀스크린 리페인트**(고fps)로 그려 ssh 위로 지속적
  대량 출력을 흘린다. feed 가 그 속도를 못 따라가면 슬라이스 드레인(56560)이 reader
  를 멈춰 **커널 PTY 버퍼 백프레셔** → ConPTY/ssh 출력 파이프가 차 ssh 채널이 막히고,
  같은 채널을 타는 **키 입력 왕복도 함께 지연**된다(그래서 pytmux UI 는 멀쩡한데 ssh
  만 느림). "수 분 내 점진 악화"는 출력이 입력 처리보다 빨라 큐가 계속 밀리는 양상과
  일치. 확인 방향: ① Claude busy(스피너) 중에만 악화되는지(=대량 출력 가설), ②
  `top`/프로파일로 feed(pyte)가 루프를 잡는지, ③ 단순 `cat 대용량` 을 ssh 로 흘려도
  재현되는지(=Claude 무관, 순수 throughput). 가능한 대응(미구현): **출력 레이트
  제한/코얼레싱**(같은 프레임 다중 리페인트 합치기), **feed 별도 스레드 분리**, 또는
  busy 중 비활성/비가시 영역 feed 스로틀(§9 의 "남은 개선"과 동일 줄기). **현재는
  미해결 — 기록만.**
  - **★ CL 56xxx 프로파일링 완료 — 원인 2건 정량 확정**(가설 ②/③ 검증, macOS local·
    Python 3.13, `poc/feed_profile.py`). 합성 워크로드(claude busy = alt-screen 풀
    리페인트, plain cat = main-screen 스크롤)를 FEED_SLICE(8KB) 단위로 먹이며 측정.
    - **(원인 1) feed 처리량 천장 ≈ 2.2 MB/s — 전적으로 pyte 내부 비용**. cProfile:
      feed 10.5s 중 `pyte streams.feed` 10.49s(99.6%), 그중 `Screen.draw` 7.89s,
      **그 안의 `collections.namedtuple._replace`+`_make`+`__new__` = ~10s**(7.78M 호출).
      즉 **pyte 가 셀 하나 그릴 때마다 `Char` 네임드튜플을 새로 할당**하는 게 천장의
      정체다. **우리 전처리 정규식(`_sanitize_sgr`/`_PRIVATE_SGR_RE`/`_CSI_PARTIAL_RE`/
      `_ALT_RE`)은 합쳐 ~0.2s 로 무시할 수준** — feed 비용은 거의 100% pyte 다.
      §9 의 "~1.2 MB/s" 와 동치(측정 박스 차이일 뿐 차원 일치).
    - **(원인 2) 슬라이스 단위 30~85ms GC 일시정지 스파이크** — 정상 슬라이스는
      ~3.7ms(p99 ~5ms)인데 **단일 슬라이스가 주기적으로 30·41·55·68·85ms 로 튄다**
      (점점 커짐). **`gc.disable()` 하면 max 81.6ms→5.0ms, 스파이크 전부 소멸**로 확정 —
      feed 가 쏟아내는 수백만 개 `Char`/리스트 객체를 **순환 GC 가 주기적으로 훑으며
      이벤트 루프를 멈추는 것**. 이게 "UI 는 멀쩡한데 입력이 뚝뚝 끊기는" 직접 원인.
      **plain `cat` 으로도 재현**(스크롤백 有 main-screen 에서 더 잦음) → 가설 ③(Claude
      무관 순수 throughput) 성립. Char 는 불변 값이라 순환이 없어 **refcount 만으로
      회수**되므로, 버스트 드레인 동안 GC 를 끄거나 임계치를 올려도 누수 위험은 낮다.
    - **코얼레싱 잠재력 462x**: 한 버스트에 풀스크린 리페인트(`CSI 2J`) 프레임이 N 개
      쌓여도 **마지막 1 프레임만 보인다**. 300 프레임 전체 feed 867ms vs 마지막 프레임만
      1.9ms. busy 스피너처럼 화면을 통째로 다시 그리는 출력에선 **마지막 풀클리어 이전
      버퍼를 버리는 코얼레싱**이 가장 큰 레버(단, pyte 는 상태기계라 임의 드롭 불가 —
      `2J`/`?1049` 같은 "이전을 무효화하는" 경계에서만 안전).
    - **대응 우선순위(권장)**: ① **GC 튜닝**(드레인 중 `gc.disable()`+종료 후 1회
      `gc.collect()`, 또는 `gc.set_threshold` 상향/`gc.freeze()`) — 가장 싸고 즉효, 입력
      스파이크 제거. ② **풀스크린 리페인트 코얼레싱** — `_feedbuf` 에서 마지막 화면-무효화
      경계(`2J`/alt 전환) 이전을 드롭해 throughput 천장을 사실상 우회. ③ feed 별도 스레드
      (가장 큰 공사). 재현·측정은 `python poc/feed_profile.py [--profile]`.
    - **✅ 대응 ① GC 튜닝 구현 완료(CL 56xxx)**: `server._feed_drain` 이 드레인 창 동안만
      순환 GC 를 끈다 — `_gc_drain_enter`(첫 드레인 0→1 에서 `gc.disable()`)/`_gc_drain_exit`
      (마지막 1→0 에서 원래 켜져 있었으면 `gc.enable()`+`gc.collect()` 1회). 동시 드레인은
      깊이 카운터(`_gc_drain_depth`)로 묶어 마지막 하나가 끝날 때만 복구하고, exit 는 try/
      finally 라 취소·예외에도 균형을 유지(GC 영구 꺼짐 방지). `Char` 는 불변값이라 순환이
      없어 refcount 만으로 회수되므로 드레인 창 누수 위험은 낮다. **효과(측정)**: 서버 드레인
      경로 50k 줄 버스트에서 슬라이스 max 82ms→4.5ms, 스파이크>20ms 5→0. 회귀 테스트
      `test_feed_drain_disables_gc_during_burst`·`test_feed_drain_gc_balanced_on_cancel`.
      **서버 변경 → kill-server 재기동.**
    - **✅ 대응 ② 코얼레싱 구현 완료(CL 56xxx)**: alt-screen 풀스크린 리페인트 버스트에서
      무효화된 중간 프레임을 버려 pyte feed 부하(원인 1 throughput 천장)를 직접 줄인다.
      순수 함수 `model.coalesce_alt_repaints(buf, alt_active)` — **무손실 안전 조건**: ①
      `alt_active` 일 때만(main-screen 은 위로 밀린 줄이 스크롤백에 쌓여 드롭 시 손실 →
      절대 금지), ② 버퍼에 alt 전환(`?1049/?1047/?47`)이 없을 때만(화면 경계 가로지르면
      bail), ③ 풀클리어(`CSI 2J/3J`)가 2개 이상일 때만 — 마지막 클리어 이전 전부를 드롭
      (클리어+그 뒤 리페인트는 온전히 남아 "비우고 새로 그림" 결과 보존). 서버
      `_coalesce_feed` 가 `_on_pane_data` 의 feedbuf 누적 직후 적용(옵션 `coalesce_repaints`
      기본 ON, opts.json 영속, 명령 `coalesce-repaints on|off|toggle`). 클라 렌더엔 영향
      없는 서버 내부 동작이라 status 미전달. **효과(측정, 현실 시나리오=alt 진입 후 busy
      리페인트 버스트)**: 8 프레임 버스트 pyte feed 100%→12.5%·drain 13.3ms→2.9ms,
      30 프레임 100%→3.3%·42.6ms→2.9ms → 드레인이 빨리 끝나 reader 가 즉시 재개, 커널
      PTY 백프레셔/ssh 채널 막힘 완화. 렌더 동일성(2/5/12 프레임 무손실) 검증.
      회귀 테스트 `test_coalesce_alt_repaints_lossless_and_guards`(model)·
      `test_coalesce_repaints_collapses_feedbuf_and_persists`(server). **서버+클라 →
      kill-server 재기동.** (원인 1 천장 자체(pyte Char 할당)는 그대로 — 코얼레싱은 "덜
      먹여서" 우회. 대응 ③ feed 별도 스레드는 미구현 — 잔여.)
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
  - **CL 56426 에서 best-effort 완화 추가**: 가장 흔한 원인이 **대체 스크롤 모드
    (DECSET 1007)** 로 판단 — iTerm2·일부 SSH 클라이언트가 기본적으로 alt-screen 에서
    마우스 휠을 ↑/↓ **화살표 키로 변환**해 보낸다. 그러면 pytmux 는 진짜 휠 이벤트
    (`on_mouse_scroll_up`)를 못 받고 화살표만 활성 패널로 새어 스크롤백이 안 열린다
    (로컬 터미널은 1007 off → 정상, 원격 터미널은 1007 on → 실패로 증상 일치).
    `App.on_mount` 가 `\x1b[?1007l`(`_term_write`, Textual 드라이버 경유)로 1007 을
    꺼 터미널이 SGR(1006) 휠 이벤트를 그대로 넘기게 하고, `on_unmount` 가 `\x1b[?1007h`
    로 복원한다. 기본 on(`disable_alt_scroll`), `set alt-scroll off` 로 터미널 기본에
    맡길 수 있다. **터미널이 1007 을 지원하지 않으면(②류) 여전히 mouse-debug 로 확인**
    필요 — 이 완화는 1007 변환이 원인일 때만 듣는다. 회귀 테스트 `test_alt_scroll_toggle`.
    클라이언트 전용(attach 재실행 반영).
  - **CL 56523 에서 진단 보강(切り分け 완성)**: mouse-debug 가 켜지면 휠 이벤트뿐
    아니라 **내비게이션 키(↑/↓/←/→/PageUp/PageDown/Home/End)** 도 `<sock>.mouse.log` 에
    남긴다(`_log_key`, `_KEY_DIAG` 화이트리스트 — **문자/단축키는 입력 유출 방지로 제외**).
    이제 휠을 굴렸을 때 **`scroll_up/down` 이 안 찍히고 `key up`/`key down` 만 쏟아지면 위
    1007 변환(alt-scroll 안 듣는 터미널)으로 확정**, 둘 다 안 찍히면 터미널이 휠을 아예
    안 넘기는 것(②: 터미널 자체 스크롤백 가로채기 등 → 터미널 설정). 환경 의존이라 코드로
    더 못 고치는 케이스의 **원인 확정**까지가 이 작업의 목표(자동 화살표→스크롤 변환은
    실 화살표 입력과 구분 불가라 의도적으로 미구현). 회귀 테스트 `test_mouse_debug_logging`
    확장. 클라 전용(attach 재실행).
- ~~**[요청·미구현/CL 56510 기록] Claude 프롬프트 헤더가 패널 1행을 차지하면 터미널
  스크롤을 2행부터 시작**~~ → **CL 56516 에서 해결(서버 PTY 리사이즈 방식).** 서버
  `_layout_msg` 가 `_should_reserve_header(p)`(전역 `claude_header` + 그 패널이 Claude 이고
  `last_prompt` 있음)면 내용 영역을 한 행 양보한다(`cy+1`, `ch-1`)— 단일 테두리 on/off 가
  content 를 조절하는 그 자리. `p.resize(cw, ch)` 로 **PTY 도 한 행 작게** 리사이즈해 Claude
  Code 가 실제로 작은 화면을 그려 정합성이 가장 좋다(검토 ①). 예약 사실을 layout 패널 msg 에
  `claude_hdr=True` 로 실어 보내고, 클라(`_draw_claude_headers`/`_claude_header_panes`)는
  그 플래그가 있는 패널만 헤더를 **예약된 행(`p["y"]-1`, 내용 위 한 줄)** 에 그린다(이전엔
  `p["y"]` 에 덧그려 겹쳤다). 헤더 유무가 바뀌면(프롬프트가 처음 뜨거나 Claude 종료) flush
  루프가 `_should_reserve_header(p) != p._hdr_reserved` 를 감지해 레이아웃을 다시 보내 PTY 를
  재리사이즈한다. 클라 전용 `_claude_hidden_panes`(팝업 숨김)는 서버가 모르므로 그 경우 예약
  행만 비고, 토글 시 터미널 리플로우가 없는 이점도 있다. 회귀 테스트
  `test_claude_header_reserves_row`(서버)·`test_claude_icon_and_header` 등(클라, 헤더 행 -1
  반영). **서버+클라 → kill-server 재기동.** 아래는 원래 검토한 구현 방향(참고):
  - 헤더는 클라이언트 합성 단계(`_composite`/`_draw_claude_headers`)가 패널 content
    **위에 덧그린다** — 즉 서버가 보낸 패널 content 행 높이(`_layout_msg` 의 `h`/`cy`)는
    헤더를 모른다. 그래서 **헤더가 그려지는 패널은 content 영역을 한 행 줄이고(`cy+1`,
    `ch-1`) 시작 y 를 한 칸 내려** 헤더와 겹치지 않게 해야 한다(현재 단일 패널 테두리
    on/off 가 `single_border` 로 content 영역을 조절하는 것과 같은 자리).
  - 헤더 표시 여부는 패널별로 다르다(`claude_header` 전역 옵션 + `_claude_hidden_panes`
    패널별 숨김 + 그 패널이 Claude 인지). content 영역 축소도 **그 패널이 실제로 헤더를
    그릴 때만** 적용해야 한다(헤더 없는 패널은 그대로 전체 높이 사용).
  - 서버/클라 어디서 행을 빼는지: 헤더는 클라 전용 렌더라 **클라가 그 패널의 PTY 크기를
    한 행 작게 서버에 통지**하거나(resize), 클라 합성에서 content 를 한 행 내려 그리고
    맨 아래 한 행을 비우는(또는 서버에 작은 높이를 알리는) 방식. PTY 크기까지 줄이면
    Claude Code 가 실제로 한 행 작은 화면을 그려 정합성이 가장 좋다.
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
  완료**(§6). **사용량 신호는 CL 56428 에서 보강**: `claude_usage` 가 ① 컨텍스트 잔량%
  를 우선하고, ② 확장 컨텍스트 모델 배지(`(1M context)`/`200K context window` →
  `_CTX_BADGE_RE`)를 감지해 `ctx 23% 1M` 처럼 덧붙이며, ③ busy footer 의 스트리밍 델타
  (`↑/↓ N tokens`, 한 프레임 송수신량이라 누적 아님)는 **사용량으로 보고하지 않도록**
  화살표 앞붙은 토큰 언급을 건너뛴다(화살표 없는 누계 "used 45.2k tokens" 만 채택).
  회귀 테스트 `test_claude_usage_context_badge`·`test_claude_usage_excludes_streaming_delta`.
  남은 것: 리밋(limit) 문구는 실제 리밋 캡처 샘플이 없어 미검증.
- ~~**[요청·미구현/대형] 작업(열린 탭·패널)을 보존한 채 서버 재시작**~~ → **방식 ① 제자리
  re-exec 으로 구현(CL 56543/56545/56546/56547 + 명령 팔레트/문서).** 명령
  `restart-server`(별칭 `restart`). save_resume_state 가 트리+패널 상태+살아 있는 셸의
  PTY 식별자(child_pid·master_fd 번호)+화면 스냅샷을 직렬화하고, execv 직전 master fd
  CLOEXEC 해제 → `os.execv` → `--resume` 부트에서 `pty_backend.adopt` 로 상속 fd 채택
  +CLOEXEC 재채택. 클라이언트는 `{"t":"restarting"}` 후 같은 소켓으로 재접속. 셸 PID
  보존을 서브프로세스 종단간 테스트로 검증(`tests/test_restart.py`, 9 케이스). 전체 명세는
  **docs/RESTART_SCENARIO.md**. alt-screen 재그리기도 실 박스 검증 완료 — 복원 후
  `_induce_redraw_all`(winsize 토글로 SIGWINCH)이 vim/claude 등을 repaint 시킨다(주의
  ① 대안 B). 순수 셸 스크롤백도 실 박스 검증 완료 — 화면 밖으로 밀린 줄이 메인 화면
  평문 스냅샷으로 복원돼 재시작 후 맨 위로 스크롤하면 다시 보인다(pyte 완전 직렬화=대안
  A 비채택). 이하 원 요청 기록 ⤵
- pytmux 는 활발히
  개발 중이라 **서버를 자주 재시작**해야 하는데(§2 의 "데몬 재시작 주의": 서버 코드
  `server.py`/`model.py`/`protocol.py` 변경은 `kill-server` 후 재기동해야 반영), 동시에
  pytmux 로 **실제 작업을 하고 있어 재시작이 부담**스럽다. **레이아웃 저장
  (`save_layout`/슬롯)은 트리 구조와 패널 제목만 직렬화**(`_serialize_node`, server.py
  ~788-793)할 뿐 **셸 프로세스·실행 중인 프로그램·스크롤백을 잃고 새 셸을 띄우므로**
  (`restore_layout`→`spawn_pane`, ~818-842) 작업 연속성에는 큰 도움이 안 된다. 요청:
  **지금 열려 있는 탭·패널의 작업(살아 있는 셸/프로그램)을 보존한 채 서버를 재시작**할 수 있게 한다.

  핵심 난점: **패널 = 서버가 소유한 PTY master fd + 자식 셸 프로세스**다. 서버를 죽이면
  셸도 함께 죽고, 죽이지 않으면 새 코드가 안 올라온다. 보존 재시작은 "**프로세스/PTY 는
  살린 채 서버 코드만 교체**"해야 한다. 두 가지 방식(tmux 가 쓰는 ①을 권장):

  - **방식 ① 제자리 re-exec(권장, tmux 서버 업그레이드 방식)**: 새 명령
    (예: `restart-server`/`server-respawn`)이 kill+spawn 대신 ⓐ **모든 패널 master fd 의
    `FD_CLOEXEC` 를 해제**하고(현재 master 생성 직후 CLOEXEC 를 거는데(server.py ~61-63,
    §6/CL 56309) **이게 바로 execv 때 fd 를 닫아 셸을 죽이는 원인** — 넘길 fd 만 직전에
    해제), ⓑ **모델 트리 + 패널별 (child_pid·master_fd 번호·title·cwd·size·`_claude`·
    `autoresume`·`last_prompt`·마우스 모드 등) + pyte 화면/스크롤백**을 상태 파일
    (`layout_path` 패턴, server.py ~785)에 직렬화하고, ⓒ `os.execv(sys.executable, …)` 로
    **서버를 제자리 재실행** — **PID 가 그대로라 자식 셸들이 계속 자기 자식으로 남아
    `waitpid`/SIGCHLD 가 유효**하고, 상속된 master fd 가 PTY 를 살린다. ⓓ 새 이미지가
    상태 파일을 읽어 **각 fd 를 Pane 으로 다시 감싸고**(`add_reader` 재등록) Session/Tab/
    Window 트리를 복원. 클라이언트는 같은 소켓으로 재접속(detach→reattach).
    - **주의**: ① `os.execv` 는 프로세스 이미지를 갈아끼우므로 **메모리에 있는 pyte 화면/
      스크롤백은 직렬화하지 않으면 소실**된다 — 패널별 `screen.display`+history 를 상태
      파일에 같이 저장해 복원하거나, 소실을 감수하고 **재그리기를 유도**(SIGWINCH/리사이즈
      한 번으로 claude/vim 등 alt-screen TUI 는 다시 그림; 순수 셸은 스크롤백만 잃음).
      ② 서버가 쥔 **리슨 유닉스 소켓·연결된 클라이언트 소켓**은 옛 이벤트 루프의 fd 라
      execv 후 **리슨 소켓을 다시 만들고**(`serve` 가 이미 시작 시 unlink+재생성,
      ~1719-1722) 클라이언트는 재접속해야 한다. ③ **CLOEXEC 불변식(§6)**: 평상시엔
      절대 풀지 말고, **넘길 master fd 에 한해 execv 직전에만 해제**, 새 이미지에서 재채택
      직후 다시 CLOEXEC 를 건다(형제 셸 fd 누수 재발 방지).
  - **방식 ② 새 프로세스로 fd 핸드오프(SCM_RIGHTS)**: 옛 서버가 새 서버를 띄우고
    유닉스 소켓 `sendmsg` 의 ancillary data(`SCM_RIGHTS`)로 **master fd 들 + 메타데이터를
    전달**한 뒤 **자식을 죽이지 않고** 종료, 새 서버가 fd 를 채택. **단점**: 옛 서버가
    종료되면 **자식 셸이 launchd/init 으로 reparent 돼 `waitpid`/SIGCHLD 로 죽음을 못 잡는다**
    — `os.read` 의 **EOF/EIO(§6 의 macOS EIO 경로)**로만 패널 종료를 감지해야 한다(PID 는
    살아 있어 `killpg` 신호는 가능). 구현이 ①보다 까다로워 차선.

  - **무엇이 보존/소실되나**: 살아 있는 셸·프로그램(claude/vim/빌드 등)과 그 PTY 는 **보존**,
    in-memory pyte 스크롤백은 직렬화 안 하면 **소실**, 클라이언트는 **재접속** 필요.
  - **개발 워크플로 연계**: 이 기능의 1순위 동기가 §2 의 "서버 코드 바꿈 → 재기동" 고통이다.
    re-exec(①)는 **디스크의 새 `server.py` 코드를 로드**하므로 "**작업 보존 + 새 코드 반영**"을
    한 번에 달성한다(정확히 원하는 동작). `restart-server` 가 그 진입점.
  - **테스트**: 실제 fork/exec 가 필요해 완전 헤드리스는 어렵지만, **직렬화↔트리 복원** 경로를
    가짜 fd 로 단위 테스트하고, **execv 직전 넘길 fd 의 CLOEXEC 해제·재채택 후 재설정**을 단언.
    re-exec 통합은 별도 수동 검증(실셸 띄워 `restart-server` 후 프로세스 PID·작업 유지 확인).
- ~~**[요청·미구현] ESC 모드 탭 전환 Enter 한 번으로 확정+복귀**~~ → **CL 56389 에서
  해결**(#26 과 함께). 탭바 포커스 Enter 분기가 `select_window` 후 `_exit_esc()` 를
  호출해 한 번에 전환+ESC 종료.
- ~~**[요청·미구현] 상태줄 REC 클릭 시 캡처 정보 팝업**~~ → **CL 56415 에서 해결.** 서버
  `_capture_info` 가 활성 패널 캡처 파일 경로·크기를 status(capture_path/size)로,
  StatusBar `_rec_zone` 클릭 시 InfoScreen 에 경로·크기·sessions.log 표시. (REC 은 디버깅
  용이라 분리 가능하게 얕게 결합.)
- ~~**[부분해결] 상태줄 토큰 사용량 세션 동안 유지**~~ → **(2) 표시 유지는 CL 56407,
  (1) 누적 합산은 CL 56437 에서 해결.** **정의**: 화면 busy footer 의 "↑/↓ N tokens" 는
  **현재 응답 한 건의 running 토큰 수**(스트리밍 중 단조 증가, 응답 사이 리셋)다 — 프레임
  델타도 누계도 아니다. 그래서 **세션 누계 = 각 응답의 최종(peak) 토큰 수의 합**으로 정의
  했다. 신규 `pytmuxlib/tokens.py`(순수 파서 `parse_running_tokens` + 상태기계 `step`/
  `new_state`/`reset`/`fmt`)가 누계를 만들고, `server._scan_claude` 가 매 프레임 `step` 으로
  접어 응답 종료(busy 종료/running 급감)에 peak 를 `Pane._tok_state`/`_session_tokens` 에
  확정한다(새 Claude 세션마다 reset). status 메시지 `claude_tokens` 로 전달 → 상태줄 사용량
  세그먼트에 `Σ45.2k` 표기(`_fmt_tokens`). 회귀 테스트 `test_tokens`(4)+`test_session_tokens_
  accumulate`+`test_status_session_tokens`. **서버+클라 → kill-server 재기동 후 반영.**
  영속 이력/시간·일·월 집계는 #7(토큰 로깅)에서 같은 데이터 소스로 확장.
- ~~**[요청·미구현] 대기(큐) 중인 새 프롬프트는 첫 줄 헤더를 아직 바꾸지 말 것**~~ →
  **CL 56439 에서 해결.** `_track_prompt` 가 Enter 확정 시 패널이 이미 busy(`p._claude
  == "busy"`)면 `last_prompt` 를 즉시 덮지 않고 **`Pane.pending_prompts` 큐에 보관**한다
  (히스토리는 제출 즉시 기록 — 큐잉돼도 제출은 맞으므로). `_scan_claude` 가 **응답 경계**
  에서 큐의 다음 프롬프트를 `last_prompt` 로 승격: ① busy→non-busy(응답 종료) 또는 ②
  연속 busy 중 **running 토큰 급감**(#3 의 `tokens.step` 이 반환하는 `committed>0` = idle
  갭 없이 다음 응답 시작 — 핸드오프가 지적한 "연속 busy 라 idle 갭이 안 보임" 난점을 토큰
  경계로 해결). Claude 세션 종료(None)면 큐를 비운다. 입력·붙여넣기 둘 다 `_track_prompt`
  를 거쳐 동일 적용. 회귀 테스트 `test_queued_prompt_header_defers`(A 즉시→busy 중 B·C 큐잉
  →응답마다 B,C 순차 승격, 히스토리엔 즉시 A,B,C). **서버 변경 → kill-server 재기동.**
- ~~**[부분해결] Claude 프롬프트 헤더 → 프롬프트 히스토리 팝업**~~ → **CL 56409(팝업)
  + CL 56449(ESC 모드 포커스 동선)에서 해결.** 팝업: 서버가 패널별 prompt_history 누적
  (_track_prompt)·status 전달, 클라가 헤더 클릭존·명령(prompt-history)으로 InfoScreen
  시간순 표시. **ESC 모드 포커스 동선(#5)**: `_hdr_focus`(pane id) 상태 추가 — ESC 모드
  에서 `h` 로 Claude 헤더 포커스 진입(활성 패널이 Claude 면 그것, 아니면 첫 Claude 헤더),
  ←↑/→↓ 로 Claude 헤더 간 이동(`_claude_header_panes` 레이아웃 순서), Enter 로 그 헤더의
  히스토리 팝업, Esc 해제. 포커스 헤더는 `_draw_claude_headers` 가 accent 강조색으로 그린다.
  `_exit_esc` 가 포커스 해제. 회귀 테스트 `test_esc_header_focus_opens_history`. 클라이언트
  전용(attach 재실행).
  - **CL 56500 에서 팝업 조작 보강**: `InfoScreen.on_key` 가 **어떤 키에도 닫히던**(방향키
    포함) 탓에 히스토리 내비게이션이 불가했다 — 방향키는 닫지 말고 `ListView` 선택을 옮기게
    하고(그 외 키만 닫음), 긴 프롬프트는 Label 줄바꿈(`width:1fr`+`height:auto`)으로 여러
    줄 표시. 회귀 테스트 `test_prompt_history_arrows_navigate_not_close`. 클라이언트 전용.
- ~~**[부분해결] Claude 헤더 첫 행 닫기 버튼 제거 → 옵션·명령 제어**~~ → **CL 56405
  ([x] 제거+전역 옵션/명령) + CL 56460(② 팝업 숨김 토글 + ③ opts.json 영속)에서 해결.**
  ② **히스토리 팝업서 '이 헤더 숨기기' 토글**: `InfoScreen` 에 `hide_key`/`hide_cb` 추가,
  `open_prompt_history` 가 `[h]` 키로 `toggle_header_hidden(pid)` 호출 + 힌트 줄 표시.
  패널별 `App._claude_hidden_panes` 집합으로 그 패널 헤더만 숨김(전역 claude-header off
  와 별개), `_draw_claude_headers` 가 스킵. 숨겨도 `prompt-history` 명령/ESC `h` 로 팝업을
  열어 되돌릴 수 있다. ③ **claude-header 전역 옵션 opts.json 영속**: 서버가 `claude_header`
  를 `<sock>.opts.json` 에 저장(`set_claude_header`+`_save_opts`)·status 로 전달, 클라가
  `claude-header` 명령 시 `set_claude_header` 를 서버로 보내 영속(낙관적 즉시 반영, status
  로 권위값 회신). 재시작 후 유지. 회귀 테스트 `test_claude_header_opt_persists`(서버)·
  `test_header_hide_toggle_from_history`·`test_claude_header_status_applies`(클라).
  **서버+클라 → kill-server 재기동.**
- ~~**[요청·미구현] 커맨드 팔레트에서 옵션 설정 후 프롬프트 없이 바로 실행**~~ → **CL
  56516 에서 해결.** `?`/`help` 목록(`CommandListScreen`)에서 명령을 고르면(`_picked`):
  ① 옵션 스키마(`COMMAND_OPTIONS`)가 있으면 **`CommandOptionsScreen` 모달**을 띄워 옵션
  (선택지)을 ←→ 로 정하고 Enter 로 **완성된 명령 줄을 `_run_command` 에 직접 넘겨 실행**
  (프롬프트 우회), ② 인자 없는 안전한 명령(`COMMAND_NOARG`: next-tab·select-layout 등 비파괴)
  은 **선택 즉시 실행**, ③ 그 외(자유 텍스트 인자) 명령은 **기존처럼 프롬프트에 채워**
  Enter 로 실행(파괴적 kill-*/detach 등도 확인 위해 이 경로). 옵션 스키마는 choice(선택지)만
  지원해 모달이 키보드만으로 동작(`MenuScreen` 식 ListView 포커스 — Vertical 래퍼/빈 Label
  은 합성 단계 렌더 오류를 내므로 피함). 적용 명령: split-window·select-pane·resize-pane·
  select-layout·capture-pane + 각종 on/off 토글(synchronize-panes·monitor-*·auto-resume·
  prompt-clear·claude-header·single-border 등). 회귀 테스트 `test_help_command`(옵션 모달
  바로 실행)·`test_command_options_change_value`(←→ 값 변경)·`test_command_palette_routing`
  (no-arg 즉시 실행/자유텍스트 프롬프트). 클라이언트 전용(attach 재실행 반영). 아래는 원래
  검토한 구현 방향(참고):
  - `CommandListScreen`(또는 새 팔레트 모달)에서 명령 선택 시 **프롬프트로 채우는 대신**,
    그 명령의 **옵션 입력 UI**(인자/플래그 토글·값 입력)를 모달 안에 펼치고, "실행" 액션이
    완성된 명령 줄을 만들어 `_run_command` 를 **직접 호출**(프롬프트 우회).
  - **옵션 메타데이터 필요**: 현재 `COMMANDS`(client.py ~165) 항목은 `(이름, 설명, 카테고리)`
    뿐이라 옵션 정의가 없다. 명령별 옵션 스키마(이름/타입/기본값/플래그 여부)를 추가하거나,
    옵션이 있는 명령에 한해 점진 적용. 옵션 없는 명령은 선택 즉시 실행.
  - 기존 "선택 → 프롬프트 채움 → Enter" 경로(line ~532, ~2026-2027)는 유지(둘 다 가능).
  - ESC 모드/`F12` 진입과의 동선, 카테고리 탭 UI 와의 일관성 고려.
- ~~**[요청·미구현] 하단 상태줄(REC 줄) 배경을 터미널 배경색으로**~~ → **CL 56373 에서
  해결**(상단 탭바 #28 과 함께). `StatusBar.render_line` 의 base bgcolor 를
  `self.bg or surface` → `self.bg`(미설정이면 None=터미널 기본). REC/SYNC/AR 등 배지·
  명시 bg(self.bg) 우선은 그대로.
- ~~**[요청·미구현] 원격(SSH) 접속이면 머신 이름에 `ssh:` 접두사 + 붉은색 표시**~~ →
  **CL 56369 에서 해결.** `StatusBar._expand_parts` 가 right_fmt 를 (kind,text) 런으로
  쪼개고, `_is_remote`(`SSH_CONNECTION`/`SSH_TTY` 시작 시 1회 캐시)면 host 런을
  `ssh:`+host·`error`(붉은색)+bold 로 그린다. 로컬은 기존 base 색.
- ~~**[요청·미구현] 시계 클릭 존을 "시간" 부분으로만 한정**~~ → **CL 56369 에서 해결.**
  세그먼트 분리(`_expand_parts`)로 `_clock_zone` 이 시각(`%H:%M`) 런만 덮는다 — host·날짜
  클릭은 clock-mode 와 무관. `on_mouse_down` 시계 토글 동선은 그대로.
- ~~**[요청·미구현] 날짜 클릭 시 현재 패널에 이번 달 달력 오버레이(clock-mode 식)**~~ →
  **CL 56371 에서 해결.** `calendar_panes`/`toggle_calendar`/`_draw_calendar_overlay`/
  `_calendar_close_zones` 를 clock-mode 미러로 추가. 상태줄 날짜 존(`_date_zone`) 클릭·
  우상단 `[x]`·명령(`calendar-mode`/`calendar`/`cal`)으로 토글. 뒤 화면 dim, `calendar`
  모듈 monthdayscalendar(월요일 시작) 그리드(제목 YYYY-MM·요일·주별 날짜), 오늘 강조
  (success 배경), `_clock_tick` 이 자정 넘어가면 갱신. **시계/달력은 한 패널에 하나만**
  (toggle 시 상호 배타).
- ~~**[요청·미구현] 전체 탭/패널/실행앱 한눈에 보기 + 전환·종료 팝업(로컬/원격 구분)**~~ →
  **CL 56383 에서 해결**(#24 와 함께). 서버 `_tree_msg` 의 windows[].panes 를 개수 →
  패널 dict 리스트({id,title,cmd,remote})로 확장(`_pane_overview`+`_REMOTE_CMDS`로 fg
  명령이 ssh/mosh/autossh/telnet/et 류면 remote). 클라 `ChooseTreeScreen` 가 윈도우
  아래 패널을 들여쓰기로 `[local]`/`[ssh]` 배지+앱+제목 표시(markup=False), Enter=전환
  (win→select_window, pane→+select_pane_id), d/x=종료(win→confirm_kill_tab, pane→
  kill-pane y/N). 명령 `overview`/`tree` 별칭. **fg 명령만으로 원격 판정**(자식 트리
  심층 검사는 미적용 — 추후 정밀화 여지).
- ~~**[부분해결·요청확인] pytmux 중첩 실행 거부 (로컬 + 원격 둘 다)**~~ → **로컬 CL 56394,
  원격(ssh) CL 56510 에서 해결.** 원격은 아래 ①(SetEnv/SendEnv 전파)의 **서버 무설정
  변형**으로 구현했다: 패널 셸 env 에 표식 `LC_PYTMUX=1` 을 심고, PATH 앞단에 ssh 래퍼
  (`pytmuxlib/sshwrap.py`)를 깔아 `ssh -o SendEnv=LC_PYTMUX` 로 실행한다(래퍼는 자기
  디렉터리를 PATH 에서 빼 진짜 ssh 를 exec — 무한 재귀 방지). 대부분의 sshd 기본값
  `AcceptEnv LANG LC_*` 를 타고 원격 셸로 표식이 전파되고, `launcher.nesting_blocked`
  가 `$PYTMUX`(로컬) **또는** `$LC_PYTMUX`(원격) 를 보고 거부한다 — **sshd_config 수정
  불필요**(흔한 기본값을 탐). LC_* 미수용 sshd 면 전파만 빠질 뿐 깨지지 않는다(우아한
  열화). 래핑 대상은 `-o` 를 ssh 로 전달하는 ssh/autossh(mosh 제외). 회귀 테스트
  `test_sshwrap_marker_and_path`·`test_nesting_blocked_helper`(원격 표식 케이스 추가).
  아래는 원래 검토한 구현 방향(참고):
  - **로컬은 CL 56394 에서 해결**: `launcher.nesting_blocked` 가 `$PYTMUX`
  설정이면 main/attach 를 `sys.exit(1)` 로 거부(우회: `unset PYTMUX LC_PYTMUX`
  뿐 — 강제 옵션 `--force` 는 CL 56538 에서 폐지). **원격(ssh) 중첩(이전 미구현)** — ssh 로 들어가면
  `$PYTMUX` 가 기본 전파 안 되고, pytmux 가 ssh 를 직접 띄우지 않아(사용자가 패널에서 ssh
  입력) SetEnv 주입 지점이 없다. 구현 방향:
  - **① ssh 래퍼/SetEnv 주입**: pytmux 패널 셸에 `ssh` 래퍼(함수/alias 또는 PATH 앞단
    스크립트)를 깔아 `ssh -o SetEnv=PYTMUX=1 …`(또는 `SendEnv PYTMUX`) 로 원격에 표식을
    전파 → 원격 pytmux 가 `nesting_blocked` 로 거부. 단 서버 `AcceptEnv PYTMUX`/`SetEnv`
    허용이 필요(서버 sshd_config 의존).
  - **② 원격측 자체 가드**: 서버 의존을 피하려면 원격 셸의 자동 attach 가드(README
    "SSH/mosh 자동 attach")에서 이미 `$PYTMUX` 외에 추가 표식(예: 부모 프로세스 트리에
    pytmux 데몬 소켓 존재 여부)을 검사. 다만 다른 호스트면 부모 트리로는 못 잡으니 ①의
    환경변수 전파가 본안.
  - **③ 최후 보루**: 원격 pytmux 기동 시 **이미 같은 tty 가 pytmux 클라이언트인지**(예:
    `$PYTMUX`/래핑된 `$TERM` 표식)만 보고 거부 — 표식 전파(①)가 전제.
  로컬·원격 공통으로 `launcher.nesting_blocked` 한 곳에서 표식을 판정하도록 모으는 게 목표.
- ~~**[요청·미구현] 닫기 확인 팝업을 "pytmux 종료 여부"로 구분(메시지+하이라이트색)**~~ →
  **CL 56392 에서 해결.** `confirm_kill_tab` 이 `len(self.tabbar.tabs) <= 1` 이면 종료
  케이스로 분기 — 제목 "pytmux 종료"·"…닫으면 pytmux 가 종료됩니다…" 문구·danger=True.
  `ConfirmScreen` 에 danger 파라미터(선택 강조를 $accent→$error 붉은색). **패널 닫기**
  **경로(kill-pane y/N 프롬프트)는 별도 — 추후 통일 여지.**
- ~~**[요청·미구현] 컨텍스트 메뉴 토글 항목의 현재 on/off 표시 + 토글은 선택해도 메뉴 안 닫고
  ESC 로만 닫기**~~ → **CL 56379 에서 해결.** `MENU_TOGGLES={zoom,sync,autoresume}` 도입.
  `MenuScreen` 이 토글 항목 라벨 끝에 현재 상태(●/○)를 붙이고(_toggle_state 가 app.status
  또는 낙관적 _optim 읽음), 토글 선택 시 dismiss 없이 `_run_menu_action` 으로 명령만 보내고
  라벨을 즉시 뒤집음(메뉴 유지·ESC 로만 닫기). status 회신 때 `refresh_labels` 로 실제 상태
  확정(app._menu_screen 등록). 비토글은 기존대로 dismiss.
- ~~**[요청·미구현] 컨텍스트 메뉴가 뜰 때 대상 패널을 배경에서 구분 표시**~~ → **CL 56377
  에서 해결.** `_menu_open` 플래그(open_menu True/dismiss 핸들러 False) + open_menu 가
  대상을 `_menu_pane`(우클릭 패널/활성)에 잡고 _composite 재합성. `_composite` 끝에서
  `_menu_open` 이면 `_menu_pane` 외 모든 패널 셀에 `Style(dim=True)` 합성(clock-mode dim
  기법 재사용). 닫히면 재합성으로 복원.
- ~~**[요청·미구현] 토큰 사용량 표시 클릭 → Claude 실행 중 탭/패널 트리 + 세션별 토큰
  팝업**~~ → **CL 56411 에서 해결.** StatusBar 사용량 세그먼트에 `_usage_zone` 클릭존,
  클릭 시 request_tree(purpose="usage") → Claude 패널만 필터해 InfoScreen 에 탭/패널·
  앱·상태·사용량 표시. server `_pane_overview` 에 claude/usage 필드 추가. 명령
  token-usage/tokens. (세션별 누적 토큰의 "누적" 정의는 #5 (1)과 함께 후속.)
- ~~**[요청·미구현] 토큰 사용량 로깅(탭/패널/세션별) + 시간·일·월 단위 조회 화면**~~ →
  **CL 56510 에서 해결.** 아래 구현 방향대로: 신규 `pytmuxlib/usagelog.py`(순수 모듈 —
  `make_record`/`append`/`read`/`bucket_key`/`aggregate`/`summary_lines`)가 JSONL 로그를
  적고 시간/일/월 × 계정으로 집계한다. 서버 `_scan_claude` 가 `tokens.step` 의 **확정
  이벤트(committed>0)** 마다 `<sock>.tokens.jsonl` 에 `{ts,tab,pane,session,account,
  tokens}` 한 줄을 append(중복 없이 1응답=1레코드). 세션 id 는 패널 claude None→비None
  전이마다 `_claude_session_seq` 로 부여. **계정별 구분**: `claude.claude_account(text)`
  가 화면 이메일/조직/플랜을 추정하되 **이메일은 `로컬앞2글자…@도메인` 별칭**으로 마스킹
  (민감정보 보호), `set_claude_account`(명령 `token-account <이름>`)로 수동 보정 가능.
  조회 화면: 명령 `token-log`(별칭 `tokens-log`) → 서버 `request_token_log` 가 최근 N 줄
  반환 → 클라 `TokenLogScreen` 이 `usagelog` 로 집계해 팝업([h]시간/[d]일/[m]월 버킷,
  [a] 계정 필터 순환 — 라운드트립 없이 전환). 회귀 테스트 `test_usagelog.*`·
  `test_token_usage_logging`·`test_token_log_screen_aggregates_and_switches`. **서버+클라
  → kill-server 재기동.** 데이터 소스는 #3 의 `tokens.py` 와 공유.
  아래는 원래 검토한 구현 방향(참고):
- **[참고·구현 방향] 토큰 사용량 로깅(탭/패널/세션별) + 시간·일·월 단위 조회 화면** — Claude Code
  화면에서 읽은 토큰 사용량을 **탭별·패널별·세션별 로그로 영속 기록**하고, **시간 단위/날짜
  단위/월 단위로 집계 조회**하는 화면을 만든다. **명령어로 팝업을 열어** 조회할 수 있어야 하고,
  Claude Code 가 실행될 때 화면을 읽어 **사용량을 모니터링·세션 단위로 기록해 나중에 합산**할
  수 있어야 한다. 구현 방향:
  - **합산 단위(해결됨)**: CL 56437(#3)에서 정의·구현 완료 — busy footer 의 "↑/↓ N tokens"
    는 **현재 응답 한 건의 running 수**(응답 사이 리셋)이고, 세션 누계 = **각 응답 peak 의 합**.
    `pytmuxlib/tokens.py` 의 `step` 이 응답 종료에 peak 를 확정하고 **확정량(committed)을 반환**
    한다 — 이 영속 로깅은 그 **확정 이벤트(committed>0)를 한 건의 로그 레코드**로 쓰면 된다
    (중복 없이 정확히 1응답=1레코드).
  - **모니터링·기록(서버)**: `_scan_claude`(server.py ~1318, `tokens.step`)가 committed>0 을
    돌려줄 때 **타임스탬프 + tab id + pane id + 세션 id + 계정(아래) + 토큰 수**를 **영속 로그**
    (예: `<sock>.tokens.jsonl` — 캡처 로그 `<sock>.capture/` 와 같은 디스크 영속 패턴)에 append.
    세션 id 는 `claude_state` 의 None→비None 전이로 새 세션을 끊어 부여.
  - **★ 계정별 구분(요청, CL 56443 추가)**: 토큰 사용량 로깅은 **Claude 계정별로 분리**돼야
    한다 — 한 사용자가 **개인 계정과 팀(조직) 계정을 따로** 쓰면(요금/한도가 별개), 로그·집계가
    이 둘을 **구분**해서 보여줘야 한다(어느 계정에서 얼마를 썼는지). 구현 방향:
    - 각 로그 레코드에 **`account`(또는 org/plan) 필드**를 넣고, 조회 화면은 **계정별로 그룹/
      필터**해 집계(시간·일·월 버킷 × 계정).
    - **계정 식별(난점, 화면 휴리스틱)**: pytmux 는 패널 화면 텍스트만 읽으므로 계정을 직접
      못 안다. 단서 후보 — ① Claude Code 의 `/status`·푸터·로그인 배너에 표시되는 **이메일/조직/
      플랜 문자열**을 정규식으로 잡기(claude.py 에 `claude_account(text)` 헬퍼 신설), ② 화면에서
      못 얻으면 **패널 작업 디렉터리/환경변수**(예: `ANTHROPIC_*`·프로젝트별 설정)나 사용자가
      패널에 부여한 **레이블/명령(`token-account <name>`)** 으로 수동 지정. ①을 우선하되 미검출
      시 `unknown` 으로 적고 ②로 보정 가능하게.
    - 계정 문자열은 **민감정보(이메일)** 일 수 있으니 로그에 원문 대신 **해시/별칭** 저장 옵션 고려.
  - **조회 화면(클라이언트)**: 명령(`token-usage`/`tokens` 류, `COMMANDS` 노출)으로 팝업을 열어
    로그를 읽고 **시간/일/월 버킷 × 계정**으로 집계해 탭·패널·세션·**계정**별로 표시(기간/계정
    전환 UI). 기존 모달(`InfoScreen`/트리 위젯) 확장 또는 신규. 큰 로그는 증분 읽기/롤오버 고려.
  - **연관**: [상태줄 토큰 사용량 세션 누적](누적값 정의·`tokens.py`), [토큰 사용량 클릭 →
    Claude 트리 팝업](실시간 현재값 트리) 과 데이터 소스 공유 — 이 항목은 그 **영속 이력/계정별
    집계** 버전.
- ~~**[요청·미구현] Claude 패널 컨텍스트 메뉴에 '프롬프트 단위 클리어' 모드 토글**~~ →
  **CL 56510 에서 해결(단, 자동 시퀀스 범위).** 패널별 `Pane.prompt_clear_mode`(기본 off)
  + busy→idle 경계에서 전진하는 소형 상태기계 `_pc_advance`(phase None→문서화 지시 주입→
  `/clear` 주입→종료)를 `_scan_claude` 에 훅. 주입은 `_pc_inject`(자동재개 `_fire_resume`
  와 동일 PTY write 경로 — 프롬프트 추적/히스토리 안 거침). ① 문서화 지시문은 옵션
  `prompt_clear_message`(opts.json 영속, 명령 `prompt-clear-message <문구>`). 토글: 메뉴
  항목 `prompt_clear`(autoresume 옆, ●/○ 상태표시) + 명령 `prompt-clear [on|off]` + status
  `prompt_clear` 전달. 회귀 테스트 `test_prompt_clear_mode_sequence`. **서버+클라 →
  kill-server 재기동.** ~~**남은 것(후속)**: 사용자가 미리 쌓아 둔 **명령 큐를 /clear 후
  하나씩 투입**하는 배치~~ → **CL 56516 에서 해결(명시적 큐 API 방식).** 라이브 키 입력
  가로채기(에코 충돌 위험)는 피하고 **명시적 큐 명령**으로 구현했다: `prompt-clear-queue
  <명령>` 이 활성 패널 `Pane.prompt_clear_queue` 에 명령을 쌓고(모드 자동 on), `-c`/`clear`
  로 비우며, 빈값이면 현재 큐를 InfoScreen 으로 보여준다(status `prompt_clear_queue` 전달).
  드레인: `_pc_advance` 의 `clear` 단계(=/clear 완료)에서 큐가 비지 않았으면 `_pc_drain` 이
  다음 명령을 `last_prompt` 로 올리고 `_pc_inject`(추적 우회) 한 뒤 phase=None 으로 둬 그
  명령도 doc→/clear 사이클을 돈다 — 즉 **각 큐 명령이 프롬프트 단위로 잘려 매번 문서화+컨텍스트
  클리어**된다. 패널이 한가하면(`_claude=="idle"`, 진행 중 시퀀스 없음) `pc_queue_add` 가 곧장
  첫 명령을 투입해 사이클을 시작한다. 모드를 끄면 큐도 비운다. 회귀 테스트
  `test_prompt_clear_queue_drains`(서버, 사이클별 드레인 + idle 즉시 투입)·
  `test_prompt_clear_queue_command`(클라). **서버+클라 → kill-server 재기동.** 아래는 원래
  검토한 구현 방향(참고):
- **[참고·구현 방향] '프롬프트 단위 클리어' 모드(큐 배치 포함)** —
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
- ~~**[요청·미구현] 비활성 탭의 Claude 작업 완료를 상단 탭 배경색(옅게)으로 알림(보면
  사라짐)**~~ → **CL 56401 에서 해결.** `Tab.has_claude_done`/`monitor_claude` 추가,
  서버 flush 가 `_scan_claude` 로 모든 탭 패널 Claude 상태를 보고 비활성 탭 busy→idle
  전이면 플래그 설정(limit 제외), select_window 가 해제, status 에 claude_done. 클라
  TabBar 가 비활성·claude_done 탭을 success 배경으로. **디바운스(스피너 깜빡임 오탐
  방지)는 후속 보강 여지.**
- ~~**[요청·미구현] 활성 탭을 아래 콘텐츠와 "연결되는" 노트북 탭 모양으로**~~ → **CL
  56413 에서 해결, CL 56419 에서 글리프 다듬음.** `TabBar.active_tab_xrange` 로 활성 탭
  x 범위를 노출, `_composite` 가 콘텐츠 상단 테두리(row 0)의 그 구간을 칠해 탭과 콘텐츠를
  연결. **56419: 꽉 찬 블록+┘/└ 코너 → 위쪽 절반 블록 ▀(활성색 전경)로 교체** — ▀ 아래
  모서리(셀 중앙선)가 양옆 가로 테두리(─)와 같은 높이라 끊김 없이 한 줄로 이어진다.
- ~~**[버그·미해결] 탭 전환 시 노트북 탭 연결부가 따라오지 않아 어긋나 보임**~~ (#23 회귀,
  CL 56413) → **CL 56421 에서 해결.** 증상: 활성 탭을 바꿔도 콘텐츠 상단 연결부가
  옛 탭 위치(좌단)에 머물렀다. 원인은 진단대로 둘이었다: ① `active_tab_xrange` 가
  렌더 부산물 `_zones` 를 읽어 전환 직후 stale(또는 스크롤로 새 활성 탭이 빠지면 None),
  ② 연결부를 그리는 `_composite` 가 layout/screen 메시지에만 돌아 status 경로의 탭
  변경(`_update_tabbar`)으로는 재합성이 안 됨. **수정(권장 ①+②)**: 탭바 기하를
  `TabBar._entries()`(kind,payload,text 순서)로 추출해 `render_line`(스타일)과
  `active_tab_xrange`(연결부 x)가 공유 — 후자는 `_zones` 대신 현재 `self.tabs`+스크롤
  에서 직접 계산. 그리고 `_update_tabbar` 가 활성 탭 인덱스 변화를 감지하면 즉시
  `_composite()` 호출. 회귀 테스트 `test_active_tab_connector_follows_switch`(폭 초과
  스크롤 상황에서 전환 직후 새 범위 계산 + `_update_tabbar` 가 ▀ 를 새 위치에 그림).
  클라이언트 전용(attach 재실행 반영).
- ~~**[요청·미구현] 탭 선택기(트리)에 각 탭/패널의 로컬/원격 구분 표시**~~ → **CL 56383 에서
  해결**(위 전체 개요 팝업과 한 묶음). 트리 패널 행에 `[local]`/`[ssh]` 배지 표시(서버
  `_tree_msg` 가 패널별 `remote` 플래그 전달).
- ~~**[요청·미구현/확인필요] Ctrl+Q 를 활성 패널로 전달(앱 종료는 detach 명령으로만)**~~ →
  **CL 56398 에서 해결.** 원인은 Textual 기본 App 의 `ctrl+q → quit` **priority** 바인딩
  이 패스스루 전에 가로챈 것. PytmuxApp.BINDINGS 에 `ctrl+q → ctrl_q`(priority)로 덮고,
  action_ctrl_q 가 normal 모드면 활성 패널로 \x11 전달·그 외 모드는 무시. detach 는 기존
  명령/메뉴 그대로. (IXON 이 Ctrl+Q 를 먹는 터미널 흐름제어는 별개의 터미널 설정 영역.)
- ~~**[요청·미구현] ESC 모드 탭 네비게이션에 맨 오른쪽 `[+]` 새 탭 버튼도 포함**~~ → **CL
  56389 에서 해결**(#3 과 함께). ←/↑/→ 가 `idxs + ["+"]` 를 순환(tb.sel="+"), Enter 가
  "+" 면 `new_window`. TabBar.render 가 bar_focus+sel=="+" 면 `[+]` 를 선택 강조.
- ~~**[요청·미구현] 패널 경계선 마우스오버 시 배경색으로 반응(리사이즈 가능 암시)**~~ →
  **CL 56396 에서 해결.** `MultiplexerView._hover_divider` + on_mouse_move(버튼 없는
  모션)에서 `_divider_at` 으로 경계선 호버 추적(변경 시에만 재합성), `_composite` 가
  그 칸 배경만 primary 로 강조. 드래그 시작/`on_leave` 에 해제. divider 호버는 마우스
  모드 패스스루보다 우선. set mouse off/단일 패널이면 비활성.
- ~~**[요청·미구현] 상단 탭바(첫 줄) 배경을 터미널 기본 배경색으로**~~ → **CL 56373 에서
  해결**(하단 상태줄 #10 과 함께). `TabBar.render_line` 의 base bgcolor 를
  `theme_color("panel")` → None(터미널 기본). 활성/선택/`[+]`/화살표 배지는 자체 bgcolor
  유지, 비활성 탭 전경색은 그대로.
- ~~**[요청·미구현] 패널 컨텍스트 메뉴 트리거를 '마우스 오른쪽 버튼'으로 통일(로컬/원격
  공통) + Ctrl+Click 무력화**~~ → **CL 56375 에서 해결.** `on_mouse_down`(normal): ①
  `event.ctrl` 이면 즉시 무동작(Ctrl+Click 으로 메뉴 안 열림), ② `button==3` 이면
  `_mouse_target` 무관하게 커서 아래 패널(`_pane_at`)을 `select_pane_id` 로 활성화 후
  그 패널 대상 `open_menu(pane_id)`. `open_menu` 가 대상을 `self._menu_pane` 에 보관
  (#18 배경 강조용). **한계**: 터미널이 Ctrl+Click 을 ctrl 플래그 없이 button 3 으로
  합쳐 보내면 진짜 우클릭과 구분 불가 → 우클릭으로 취급(터미널 의존).
- ~~탭 **드래그 재정렬 시 시각적 피드백**(현재는 놓을 때 확정만).~~ → **CL 56469 에서
  해결.** `TabBar` 에 `_drag_over`(드래그 중 가리키는 드롭 대상 탭 index) 추가 +
  `on_mouse_move`(capture_mouse 로 드래그 중에만 이동 이벤트 수신)가 커서 아래 탭을
  추적해 변경 시 재합성. `render_line` 이 들고 있는 탭(소스)은 `base+Style(dim=True)`
  로 흐리게, 드롭 대상은 warning 배경+bold+underline 으로 강조(놓으면 그 자리로 이동).
  같은 탭 위면 대상 해제(소스만 흐림). `on_mouse_up` 이 `_drag`/`_drag_over` 초기화 후
  재합성. 회귀 테스트 `test_tab_drag_reorder_visual_feedback`. 클라이언트 전용(attach 재실행).
- ~~패널 **드래그 swap**.~~ → **CL 56489 에서 해결.** 서버 `swap_pane_ids(sess, id_a,
  id_b)`(임의의 두 리프 패널 트리 위치 교환, swap_pane 의 인접 순환과 달리 지정 교환)
  + 액션 `swap_pane_to`. 클라: **Shift+좌버튼 드래그**로 패널을 잡아 다른 패널에 놓으면
  swap(`MultiplexerView._pane_swap`/`_pane_swap_over`, on_mouse_down/move/up). Shift+드래그를
  passthrough/divider 보다 먼저 가로채 마우스 모드 앱 위에서도 동작(패널 ≥2 일 때만).
  드래그 중 `_composite` 가 소스 패널은 dim, 대상 패널은 warning 배경으로 강조. 회귀
  테스트 `test_swap_pane_ids`(서버)·`test_shift_drag_pane_swap`(클라). 서버+클라 → 재기동.
- ~~단일 패널 테두리 on/off 옵션화.~~ → **CL 56480 에서 해결.** 서버에 `single_border`
  옵션(기본 ON=항상 테두리, opts.json 영속) 추가. `_layout_msg` 의 `bordered` 를
  `len(panes) >= 2 or self.single_border` 로 바꿔, **패널이 둘 이상이면 옵션과 무관하게
  항상 테두리**(구분 필요), **하나뿐이면 옵션 OFF 시 box 없이 화면 전체를 내용으로** 쓴다.
  `set_single_border`(토글/명시) + 액션 `set_single_border` + 텍스트 명령
  `single-border|pane-border [on|off|toggle]`(레이아웃 재브로드캐스트). status 에
  `single_border` 실어 클라가 `single_border_on` 으로 권위값 반영(낙관적 즉시 토글).
  회귀 테스트 `test_single_pane_border_toggle_and_persist`. **서버+클라 → kill-server 재기동.**
- ~~다중 줄 상태표시줄.~~ → **CL 56497 에서 해결.** `StatusBar.lines`(0~5, 기본 1) +
  `extra`(보조 줄 포맷 dict). 맨 아래 줄이 주 상태(REC/사용량/시계 등), 그 위는
  `status-format[i]`(index 1=바닥 바로 위)를 `_expand` 로 펼쳐 표시. `render_line(y)` 가
  줄별 분기, 클릭 존은 맨 아래 줄에서만. `set_status_lines` 가 위젯 높이·뷰 크기 동기화
  +서버 resize 통지. 옵션 `set status N`(0=숨김)·`set status-format <line> <fmt>`. 회귀
  테스트 `test_multiline_status_bar`. 클라이언트 전용(attach 재실행).
- 라이브 PTY display-popup(미구현 — #10 잔여).
- ~~`unbind`/추가 옵션 등 FEATURES 의 "미구현" 표기 항목(unbind-key).~~ → **CL 56495 에서
  해결.** 런타임 명령 `bind-key <key> <command>`·`unbind-key <key>|-a`·`list-keys` 추가
  (`_run_command`). 키는 tmux 표기(`C-x`)를 `_tmux_key_to_textual` 로 ctrl+x 정규화, 한
  글자는 그대로. bind 는 첫 인자만 키·나머지는 명령 원문(플래그 보존). FEATURES 표의
  "unbind 는 미구현" 표기 해소. 회귀 테스트 `test_bind_unbind_keys`. 클라이언트 전용.
- ~~**[요청·미구현] 캡처(REC) 출력을 /tmp → 프로젝트 디렉터리로 옮기고 Perforce 로 관리(단,
  GitHub 엔 절대 미반영)**~~ → **CL 56510 에서 해결.** `Server.capture_dir` 를 경로 결정
  로직으로 바꿨다: **기본 엔드포인트(실사용)면 `PROJECT_DIR/captures/<sock-id>/`**(프로젝트
  하위 — Perforce 공유 대상), `PYTMUX_CAPTURE_DIR` env 가 있으면 그쪽(테스트가 임시
  디렉터리 주입해 프로젝트 오염 방지), **임시/비기본 소켓이면 종전 휘발 영역**(`state_base`
  옆 `.capture` — 헤드리스 테스트는 mktemp 소켓이라 자동으로 이쪽). **GitHub 차단**:
  `.gitignore` 에 `captures/` 추가(민감 화면 유출 방지). `.p4ignore` 는 captures 를
  **제외하지 않음**(Perforce 공유 대상이므로). 기록 포맷·소비자(`_capture_write`/
  `_scan_claude`)는 그대로. 회귀 테스트 `test_capture_dir_project_and_override`. **단, 실제
  `p4 add captures/...` 와 롤오버/보존 정책·자동 submit 동선은 운영 시 별도 결정**(아래
  "Perforce 관리" 참고 — raw 대용량이라 디폴트 CL 오염 주의). 아래는 원래 상황/방향(참고):
- **[참고·상황] 캡처(REC) 출력 경로(이전 /tmp)** — **현재**: 패널 출력 캡처(REC, raw PTY 무손실
  로그)는 (이전) `Server.capture_dir = ipc.state_base(sock) + ".capture"` 에 쓰여, `state_base` 가
  `$XDG_RUNTIME_DIR` 또는 `/tmp/pytmux-<uid>/default.capture/` 로 풀려 **/tmp 휘발 영역**에
  남는다(`pane-<id>.log` + `sessions.log`, server.py `_capture_write`). 재부팅·tmp 청소로
  사라지고 머신 간 공유가 안 된다. **요청**: 캡처 출력을 **프로젝트 디렉터리 하위**(예
  `scripts/pytmux/captures/` 또는 `.captures/`)에 기록하도록 옮겨 **여러 기계에서 개발 시
  Perforce 로 올려 공유·관리**한다. **단, GitHub 미러에는 절대 올라가지 않게** 한다.
  구현 방향:
  - **경로 분리**: 캡처 루트를 sock 기반 state_base 가 아니라 **프로젝트 고정 경로**로
    바꾼다(예 모듈 디렉터리 기준 `captures/<sock-id>/`). `capture_dir`(server.py ~863)만
    교체하면 `_capture_write`/`_capture_info`/REC 팝업 경로가 따라온다. 다중 세션·소켓
    충돌을 피하려 sock 식별자를 하위 폴더로.
  - **Perforce 관리**: 캡처 디렉터리를 depot 에 추가(`p4 add captures/...`). raw PTY 로그라
    **바이너리/대용량**이 될 수 있으니 롤오버·최대 크기·보존 기간 정책과, 자동 `p4 add`/
    submit 동선(또는 수동)을 정한다(자동 submit 은 디폴트 CL 오염 주의 — 공유 워크스페이스).
  - **GitHub 차단(필수)**: 워크스페이스에 git 미러가 생기면 캡처 경로를 `.gitignore` 에
    넣어(`captures/`) 절대 푸시되지 않게 한다. 현재 이 워크스페이스엔 git 저장소가 없지만
    (`§8` 의 GitHub 미러 동선이 부활하면) **add 단계에서 캡처 경로를 제외**하는 게 본안.
    민감 화면(토큰·키 입력 잔재)이 깃헙 공개로 새지 않게 하는 게 목적이라 **누락 시 사고**다.
  - **주의**: capture 는 Claude 화면 문구 분석(busy/usage/프롬프트) 소스이기도 하다 — 경로만
    옮기고 기록 포맷·소비자(`_scan_claude` 등)는 그대로 둔다.
- **[구현 완료·실 Windows 박스 검증만 잔여] 네이티브 Windows 포팅** — `fcntl`/`termios`/`pty`/`os.fork`/
  `AF_UNIX` 등 POSIX 전용 의존 때문에 Windows 네이티브 Python 에서 import 단계부터 막힘.
  범위 조사는 [`docs/WINDOWS_PORT.md`](WINDOWS_PORT.md) 에 파일별로 정리됨(작업의 ~70%가
  `server.py` 의 PTY·이벤트루프·프로세스·시그널 재작성). 리스크 집중부(① ConPTY,
  ② asyncio×파이프 읽기)를 찌르는 **PoC 슬라이스 작성 완료** → [`poc/winpty_poc.py`](../poc/winpty_poc.py):
  `pywinpty`(ConPTY)→리더 스레드→`call_soon_threadsafe`→**기존** `Pane`(pyte)→**기존**
  `render_pane_lines`. pytmuxlib **무수정**(Windows 에서 `protocol.py` 의 fcntl import 깨짐을
  no-op 스텁으로 우회). macOS 에서 렌더 파이프라인 절반(`--selftest` + fcntl 차단 시뮬)은
  검증됨. **✅ ConPTY 절반도 Windows 에서 검증 완료(2026-06-04)** — Windows 11(10.0.22631)/
  Python 3.12.4/pywinpty 3.0.3/pyte 0.8.2/wcwidth 0.7.0 에서 `pip install pywinpty` 후
  `python poc\winpty_poc.py` 실행 → **`PYTMUX_POC_OK` 정상 출력**(cmd.exe 의사콘솔→리더 스레드
  펌프→pyte→렌더, 544바이트). 리스크 ①(ConPTY)·②(asyncio×파이프 읽기) de-risk 완료.
  **✅ 본 포팅 구현 완료(2026-06-04)**: 추상화 레이어 3종(`pty_backend`/`ipc`/`proc`)
  신설·테스트, `server.py`·`model.py` PTY 생애주기 전환, `server.serve()`/`client`/
  `launcher` 데몬·제어 `ipc`·`proc` 전환 완료. 후속 마감(CL 56471/56473/56475/56479/
  56487, 일부 server.py 가드는 56489 동반): pipe-pane 셸 분기(`proc.shell_argv`),
  `replay.record` Windows 가드, POSIX 열화 우아한 폴백(`_fg_command`/렌더 resize),
  dead code `default_socket_path` 제거, `requirements.txt`(`wcwidth`+`pywinpty;win32`)
  ·`install.ps1`/`uninstall.ps1` 패키징. 헤드리스 132 통과. **코드상 남은 일 없음 —
  유일 잔여는 실 Windows 박스 검증(WINDOWS_PORT §7-d)**: `pip install -r requirements.txt`
  → `.\install.ps1` → attach/split/`kill-server` 스모크 + ConPTY 멀티바이트(CJK/이모지)
  경계 확인. 깨지면 `_WinPty` 를 저수준 `winpty.PTY`(바이트) 경로로 교체(§3① NOTE).

## 11. Claude Code 특화 기능 분리 전략 (병렬 세션 충돌 최소화)

> **동기**: 이 프로젝트를 **여러 Claude Code 세션이 각기 다른 부분을 동시에** 수정한다.
> 특히 **Claude Code 연동 기능**(상태 감지·토큰/컨텍스트 표시·자동재개·프롬프트 헤더 등)은
> 화면 문구 휴리스틱이라 자주 손대는데(§6), 이게 **코어 멀티플렉서 코드(패널/탭/레이아웃/
> 입력)와 같은 파일·같은 함수에 뒤섞여** 있어 두 갈래 작업이 **머지 충돌**을 일으킨다.
> 목표: Claude 특화 코드를 **별도 경계로 모아** "Claude 담당 세션"과 "코어 담당 세션"이
> 서로 다른 파일을 만지도록 한다. **이 절은 전략 문서이며 아직 코드는 옮기지 않았다**(실제
> 추출은 후속 CL). 아래 좌표는 현행(depot 56362 기준)이라 추출 전 재확인할 것.

### 11.1 현재 Claude 특화 코드 지도 (흩어져 있음)

| 계층 | 위치 | Claude 특화 내용 |
|------|------|------|
| `protocol.py` | ~73-173 | `claude_state`/`claude_usage`/`parse_reset_delay` + 정규식(`_BUSY_SPINNER_RE`·`_CTX_PCT_RES`·`_TOK_RE`·`_RESET_RE12/24`). **순수 함수**(화면 텍스트→상태/사용량/지연). 가장 분리하기 쉬움. |
| `model.py` | Pane.__init__ ~180-191 (+reinit ~218) | 패널별 상태 필드: `autoresume`·`resume_msg`·`_scanbuf`·`_resume_pending`·`_claude`·`_claude_usage`·`_inbuf`·`last_prompt`. (`_inbuf`/`last_prompt` 은 프롬프트 추적용 — Claude 헤더 외엔 안 씀.) |
| `server.py` | import ~17-18; 자동재개 `_maybe_resume`/scan ~145-163, `set_autoresume` ~167-176; 프롬프트 추적 `_track_prompt` ~1656-1677(+호출 ~752/1642); 레이아웃 메시지 `_tab_claude` ~1263-1268·`panes_claude`/`claude_usage` ~1284-1295; flush 루프 상태 갱신 ~1353-1357; `set_autoresume` 액션 ~1465 | 서버 측 Claude 로직 전부. flush 루프 훅(매 프레임 `claude_state`/`claude_usage`)과 자동재개·프롬프트 추적. |
| `client.py` | `CLAUDE_ICON`/아이콘 라벨 ~1019-1027; StatusBar `claude_usage` ~1156/1188/1219-1221; App 상태 `pane_claude`/`_claude_hidden`/`_claude_close_zones` ~1284-1286; `_update_claude` ~1480-1486; `close_claude_header` ~1488-1491; `_draw_claude_headers` ~1493-1520(+합성 호출 ~1764); 헤더 [x] 클릭 ~847-852 | 클라이언트 렌더/입력. **`build_client_app()` 클로저 안**이라 추출이 가장 까다로움. |
| 프로토콜 메시지 | 레이아웃/상태 메시지의 `claude`(탭)·`panes_claude`(패널별 `{id,claude,prompt}`)·`claude_usage`·`autoresume` 필드 | 서버↔클라이언트 **계약(contract)**. 분리 후에도 이 스키마가 두 계층의 경계. |

### 11.2 목표 경계 (제안)

핵심 아이디어: **Claude 로직을 전용 모듈로 모으고, 코어는 얇은 훅 한 줄로만 부른다.**

1. **`pytmuxlib/claude.py` (신규, 서버측+순수)** — 한곳에 모은다:
   - protocol.py 의 `claude_state`/`claude_usage`/`parse_reset_delay` + 정규식을 **이리로 이전**
     (protocol.py 는 프레이밍/색 등 진짜 공통만 남김). 하위호환 위해 protocol 에서 re-export 가능.
   - 패널별 Claude 상태를 **`ClaudePaneState` 같은 헬퍼 객체**(또는 함수군)로 캡슐화:
     `_claude`·`_claude_usage`·`autoresume`·`resume_msg`·`_scanbuf`·`_resume_pending`·
     `last_prompt`·`_inbuf` 를 `Pane.claude` 한 속성 아래로 모은다(코어 `Pane` 표면 축소).
   - 서버 훅 함수: `update_from_screen(pane, text)`(flush 루프가 매 프레임 호출 →
     상태/사용량 갱신 + busy→idle 등 전이 반환), `maybe_resume(pane, newtext)`(자동재개),
     `track_prompt(pane, data)`(프롬프트 누적/확정). server.py 는 **이 함수들만** 호출.
2. **`server.py` 는 훅만**: flush 루프 ~1353 블록을 `claude.update_from_screen(p, txt)` 한 줄로,
   자동재개 ~145 를 `claude.maybe_resume(...)`, 프롬프트 추적을 `claude.track_prompt(...)` 로.
   레이아웃 메시지의 Claude 필드 조립(`_tab_claude`/`panes_claude`/`claude_usage`)도 claude 모듈
   헬퍼가 dict 조각을 만들어 주면 server 는 끼워 넣기만. **코어가 만지는 줄 수를 최소화**.
3. **클라이언트 렌더 분리** — `client.py` 가 `build_client_app()` 클로저라 통째 이전은 어렵다.
   현실적 절충: Claude 렌더/상태 함수(`_draw_claude_headers`·`_update_claude`·`close_claude_header`·
   `CLAUDE_ICON`·StatusBar 의 usage 세그먼트)를 **모듈 수준 자유함수**로 빼고 `app`(또는 필요한
   `cells/W/H/pane_claude`)을 인자로 받게 한다 — 예 `pytmuxlib/client_claude.py` 의
   `draw_claude_headers(app, cells, W, H)`. 클로저 메서드는 **한 줄 위임**만 남긴다. 이러면
   Claude 렌더 변경이 client.py 본문 대신 client_claude.py 에서 일어나 충돌 면이 분리된다.
   (탭 아이콘/스티키 헤더 클릭 zone 좌표 변환 주의 — §"합성 순서" §5/§90.)
4. **프로토콜 스키마를 명시적 계약으로**: `claude`/`panes_claude`/`claude_usage`/`autoresume`
   필드를 한곳(예 claude 모듈 상수/타입)에 적어 두 계층이 같은 키를 쓰게 한다. 필드 추가는
   계약 변경이니 양측이 의식하게.

### 11.3 분리 후 "누가 어디를 만지나" (충돌 회피 규칙)

- **Claude 담당 세션** → `pytmuxlib/claude.py`(+`client_claude.py`)와 프로토콜 Claude 필드만.
  코어 파일은 **훅 호출 한 줄**만 건드림(거의 안 바뀜).
- **코어(패널/탭/레이아웃/입력/copy-mode) 담당 세션** → `model.py`/`server.py`/`client.py`
  본문. Claude 모듈은 **블랙박스**로 두고 안 만짐.
- 둘 다 만지는 유일한 접점 = **훅 호출 지점**과 **프로토콜 필드**. 여길 안정화하면 충돌 끝.

### 11.4 점진적 이전 순서(저위험)

1. ~~**순수 함수부터**: `claude.py` 신설 + 감지/사용량/지연 파서 이전(+protocol re-export)~~ →
   **CL 56385 에서 완료.** `claude_state`/`claude_usage`/`parse_reset_delay`+정규식을
   `pytmuxlib/claude.py` 로 이전, protocol 은 re-export 로 하위호환, server import 를 `.claude`
   로 갱신, 테스트를 `test_claude.py` 로 이전. **무동작·81 passed.**
2. **(미완·후속)** **서버 훅화**: `_maybe_resume`/`_track_prompt`/flush 갱신/메시지 조립을 claude
   모듈 함수로 추출(동작 동일). 서버 테스트(`test_server` 의 Claude/재개/프롬프트)로 회귀 확인.
3. **(미완·후속)** **Pane 상태 묶기**: 흩어진 Claude 필드를 `Pane.claude` 하위로(접근부 일괄 치환).
   코어 `Pane` 표면이 줄어 model.py 충돌 면 감소.
4. **(미완·후속)** **클라이언트 렌더 추출**: `client_claude.py` 로 렌더/상태 함수 이전, 클로저는
   위임만(`build_client_app` 클로저라 가장 까다로움).
> **현 상태**: 1단계(모듈 경계 생성)는 완료. 2~4단계는 **기존 동작 코드의 무동작 이전**이라
> 기능 추가 작업보다 후순위로 두었다 — claude.py 가 이미 존재하므로 새 Claude 기능의 서버측
> 휴리스틱은 거기에 추가하면 된다(점진적으로 2~3단계가 자연 진행).
- **불변식 유지**(§3): `Session.active_window` 프로퍼티, CLOEXEC(§6), feed 경계 캐리(§6),
  단일 세션 모델은 깨지 말 것. 각 단계마다 `python3 tests/run.py`(현재 81) 유지.
- **데몬 재시작 주의**(§2): 서버측(claude.py 가 서버 로직 포함) 변경은 `kill-server` 재기동 후 반영.

### 11.5 대안(더 가벼움)

전면 모듈 추출이 부담이면 **최소안**: 각 파일에서 Claude 코드를 **인접 블록으로 모으고**
`# ---- Claude Code 연동 (분리 대상) ----` 식 **명확한 구획 주석**으로 감싼다(현재도 일부 있음).
충돌은 줄지만 같은 파일이라 완전 분리는 아니다 — 11.2 의 전용 모듈이 본안.
