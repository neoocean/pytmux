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
- **상태**: `docs/FEATURES.md` 의 모든 항목 구현. 헤드리스 테스트 **95 passed**
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

## 9. 최근 변경(CL 56279~56439 + git, 신→구)

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
- **[요청·미구현/대형] 작업(열린 탭·패널)을 보존한 채 서버 재시작** — pytmux 는 활발히
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
- **[부분해결] Claude 프롬프트 헤더 → 프롬프트 히스토리 팝업** — **CL 56409 에서 팝업
  구현**: 서버가 패널별 prompt_history 누적(_track_prompt)·status 로 전달, 클라가 헤더
  본문 클릭존·명령(prompt-history)으로 InfoScreen 시간순 목록 표시. **남은 부분**: ①
  ESC 모드 방향키로 헤더에 포커스 주는 선택 동선(별도 포커스 상태 필요) 후속.
- **[부분해결] Claude 헤더 첫 행 닫기 버튼 제거 → 옵션·명령 제어** — **CL 56405 에서
  [x] 제거 + 전역 옵션/명령 구현**: 헤더 [x]·_claude_hidden(프롬프트 단위 숨김) 제거,
  App.claude_header_on(기본 on)·명령 `claude-header on|off|toggle` 로 헤더 표시 제어.
  **남은 부분**: ② 프롬프트 히스토리 팝업(#7) 안의 "이 헤더 숨기기" 토글, ③ opts.json
  영속(현재 클라 세션 한정) — #7 구현 시 함께.
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
- **[부분해결] pytmux 중첩 실행 거부** — **로컬은 CL 56394 에서 해결**: `launcher.
  nesting_blocked` 가 `$PYTMUX` 설정 + not --force 면 main/attach 를 sys.exit(1) 로
  거부(우회: --force 또는 `unset PYTMUX`). **원격(ssh) 중첩은 미구현** — ssh 로 들어가면
  `$PYTMUX` 가 기본 전파 안 되고, pytmux 가 ssh 를 직접 띄우지 않아(사용자가 패널에서 ssh
  입력) SetEnv 주입 지점이 없다. ssh 래퍼/`SetEnv PYTMUX` 주입이 필요해 후속 과제.
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
- **[요청·미구현] 토큰 사용량 로깅(탭/패널/세션별) + 시간·일·월 단위 조회 화면** — Claude Code
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
- 탭 **드래그 재정렬 시 시각적 피드백**(현재는 놓을 때 확정만).
- 패널 **드래그 swap**, 단일 패널 테두리 on/off 옵션화.
- 다중 줄 상태표시줄, unbind-key, 라이브 PTY display-popup.
- `unbind`/추가 옵션 등 FEATURES 의 "미구현" 표기 항목.
- **[조사완료·구현미착수] 네이티브 Windows 포팅** — `fcntl`/`termios`/`pty`/`os.fork`/
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
  다음 단계(WINDOWS_PORT §6-b): 추상화 레이어(`pty_backend`/`ipc`/`proc`) 신설 + `server.py`
  리팩터(작업의 ~70%)의 단계별 구현 계획 수립 후 착수.

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
