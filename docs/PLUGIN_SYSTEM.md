# pytmux 플러그인 시스템 — 설계·작성법·추출 진행 기록

> 작성: 2026-06-09 · 관련: [DESIGN.md](DESIGN.md) · [HANDOFF.md](HANDOFF.md) ·
> [NC_SCENARIO.md](NC_SCENARIO.md) · [IME_PREEDIT_CURSOR_SCENARIO.md](IME_PREEDIT_CURSOR_SCENARIO.md)
>
> **한 줄 요약**: `pytmuxlib/plugins/<name>/` 하위 서브패키지를 **선택적으로** 불러와
> 명령·자동완성·디스패치·메시지·서버 요청을 코어에 합친다. **디렉토리를 통째로 지우면
> 그 기능은 명령 검색·자동완성·디스패치 어디에도 나타나지 않고 조용히 비활성화**된다
> (코어는 플러그인을 직접 import 하지 않고 오직 레지스트리를 통해서만 호출).

---

## 1. 왜 플러그인인가
- 거대·선택적 기능(예: ncd 디렉토리 트리, Claude Code 통합)을 코어에서 떼어내 **한
  디렉토리에 응집**시키고, 그 디렉토리를 지우는 것만으로 기능을 끌 수 있게 한다.
- 코어(`client`/`clientutil`/`serverio` 등)는 기능 모듈을 **하드 참조하지 않는다** —
  명령 이름·메시지 타입·서버 액션을 레지스트리에 위임한다. 그래서 플러그인이 없으면
  해당 경로는 **에러 없이 no-op** 이 된다.

## 2. 구조(`pytmuxlib/plugins/__init__.py`)
- `load()` → `Registry`. `pkgutil.iter_modules` 로 하위 **서브패키지**(디렉토리+
  `__init__.py`)를 찾아 `importlib.import_module` 로 불러오고 각 모듈의 `PLUGIN`
  객체를 모은다. import 가 깨진 플러그인은 **조용히 건너뛴다**(하나가 망가져도 앱 전체를
  막지 않음).
- 클라이언트(`PytmuxApp.__init__`)와 서버(`Server.__init__`) **양쪽**이 `plugins.load()`
  를 호출해 `self.plugins` 로 보관한다.

### 2.1 플러그인 계약(모두 선택적, 덕 타이핑)
`PLUGIN` 객체가 노출할 수 있는 멤버:
- `commands: list[(name, desc, category)]` — 코어 `COMMANDS` 에 합쳐져 `?`/팔레트·
  자동완성에 나타남.
- `noarg: set[str]` — 인자 없이 즉시 실행해도 되는 명령(팔레트 선택 즉시 실행).
- `completions: list[str]` — 자동완성 추가 후보(명령 이름은 레지스트리가 자동 추가).
- `command_options: dict` — 팔레트 옵션(선택지) 스키마(코어 `COMMAND_OPTIONS` 에 병합).
- `attach_client(app)` — 앱 인스턴스마다 1회. 인스턴스 글루 설치(예: `app.request_nc_list`,
  `app._saver_display`).
- `handle_command(app, c, args) -> bool` — 명령 `c` 를 처리했으면 True.
- `handle_message(app, msg) -> bool` — 서버 메시지(`t`)를 처리했으면 True(코어
  `_dispatch` 의 else 분기에서 호출).
- `handle_server_request(server, sess, action, msg) -> dict|None` — 서버의 **알 수 없는
  action** 을 받아 회신 메시지(dict)를 돌려주면 그대로 클라로 전송(serverio `_handle_cmd`
  의 else 분기).

### 2.2 코어 통합 지점(여기를 통해서만 호출)
- `client._run_command`: 코어 분기 후 `self.plugins.handle_command(self, c, args)` 폴백.
- `client._dispatch`: 마지막 else 에서 `self.plugins.handle_message(self, msg)`.
- 명령 목록/자동완성: `CommandListScreen(COMMANDS + self.plugins.commands)`,
  `SepInsensitiveSuggester(COMPLETIONS + self.plugins.completions)`, 팔레트
  `_picked` 의 `all_options`/`all_noarg` 병합. **명령 프롬프트 `?` 목록(PromptScreen)도
  `COMMANDS + app.plugins.commands` 로 병합**(CL 57795 에서 누락 수정 — 그전엔 코어 명령만
  보였다).
- `serverio._handle_cmd`: else 에서 `self.plugins.handle_server_request(...)`.

### 2.3 무게 규칙(중요)
플러그인의 `__init__.py` 는 **textual 을 모듈 최상단에서 import 하지 않는다** — 서버
프로세스도 `plugins.load()` 로 같은 코드를 읽기 때문이다. 화면(textual) 등 무거운 의존은
실제 필요할 때 **메서드 안에서 지연 import** 한다(예: `from .screens import NcdScreen`).

### 2.4 하이픈 디렉토리명
`claude-code` 처럼 하이픈이 든 디렉토리도 된다. `importlib.import_module(
"pytmuxlib.plugins.claude-code")` 와 **패키지 내부 상대 import**(`from .screens import X`)는
하이픈과 무관하게 동작한다. 단 **외부 절대 import 문**(`from pytmuxlib.plugins.claude-code...`)
은 문법 오류이므로, 테스트 등에서는 `importlib.import_module(...)` 로 가져온다.

## 3. 레퍼런스 플러그인
- **`ncd`**(완료, CL 57709/57721/**57774**, cwd 표시 **57803**): Norton Change Directory
  풍 디렉토리 트리. `__init__.py`(계약·명령·서버 요청), `screen.py`(모달·트리 위젯,
  textual), `server.py`(디렉토리 나열·조상 사슬, 파일시스템). [NC_SCENARIO.md](NC_SCENARIO.md)
  참고. 서버 측 `request_nc_list` 는 `handle_server_request` 로, 클라 `nc_list` 메시지는
  `handle_message` 로 처리한다. 실행 시 현재 디렉토리(cwd) 행을 노랑+`◀` 로 가리킨다.
  회귀: `tests/test_nc.py`.
- **`claude-code`**(진행 중 — 아래 §4).

## 4. claude-code 단계적 추출(진행 기록)
Claude Code 통합은 ncd 의 ~10배 규모이고 **30Hz 스캔 루프(`_scan_claude`)·상태줄/헤더
렌더·토큰 회계·opts 영속·~20 명령·5 팝업**에 깊게 얽혀 있다. 공유 Perforce depot 이라
**단계별로 나눠 단계마다 제출**한다(한 번에 4000줄+ 를 검증하는 위험을 피함).

- **Phase 1**(CL **57789**): 명령 전용 팝업 2개 → 플러그인.
  `RulesEditScreen`(`claude-rules`) + `ClaudeSaverScreen`(`token-saver`) +
  `_saver_display`/`_saver_action`·`SAVER_ROWS`/`SAVER_CYCLES`. clientscreens/client/
  clientutil 에서 제거.
- **Phase 1b**(CL **57795**): Claude **명령 표면 전체**(~17개) → 플러그인.
  메타데이터(COMMANDS/NOARG/OPTIONS)와 `_run_command` 디스패치를 옮김. 토글/주입 명령은
  플러그인이 `send_cmd` 로 직접 처리, 팝업 명령(model·token-log·prompt-history·
  usage-panel·token-usage)은 **아직 코어에 있는 `open_*`**(클릭/렌더 경로와 함께 Phase 2
  이전 예정)를 호출. `auto-launch` 는 원래 클라 디스패치가 없었어서(잠재 버그) 동작을
  보존(메타데이터만, 핸들러 없음).

### Phase 3 — S1 완료(CL 57812)
- **서버 로직 이전**: `ServerClaudeMixin`(~1280줄, 옛 `serverclaude.py`)을
  `pytmuxlib/plugins/claude-code/servermixin.py` 로 **본문 동작 불변** 이전. 코어 `server.py`
  가 `ServerClaudeMixin` 을 직접 import 하지 않고 `plugins.Registry.server_mixins()`(신설
  훅)로 **Server 의 동적 베이스**로 합성한다(`_PLUGIN_SERVER_MIXINS = tuple(
  plugins.load().server_mixins())` → `class Server(*_SERVER_BASES)`, 원 MRO 보존).
  `pytmuxlib/serverclaude.py` 삭제. 플러그인 `__init__` 에 지연 import 콜러블 `server_mixin()`.
  - **중간 상태(S1 시점)**: 코어 `serverio.py` 가 아직 `self._scan_claude` 등을 직접
    호출하므로 이 단계만으로는 디렉토리 삭제 시 서버가 깨졌다(delete-to-disable 미완 —
    사용자 합의된 중간 CL). → **아래 Phase 3a/3b 에서 호출부를 훅으로 라우팅해 계약 완성.**

### Phase 3a/3b — 서버 delete-to-disable 완성(CL 57828·57829)
S1 이 남긴 "코어가 믹스인 메서드를 이름으로 직접 호출" 문제를 **레지스트리 런타임 훅**으로
끊었다. S2~S5 의 *물리 이전*(토큰 회계·Pane 필드·파서 모듈을 플러그인으로 옮김)은 하지
않고, 더 낮은 위험의 **호출부 라우팅** 경로로 같은 계약(디렉토리 삭제 시 무력화)을 달성했다.
핵심 통찰: Claude Pane/Tab 속성은 `model.py` 코어에 안전한 기본값이 있어, 코어가 그 속성을
*읽는* 경로(예: `_should_reserve_header`)는 플러그인이 없어도 안 깨진다 → **메서드 호출만**
훅으로 옮기면 충분하다.
- **Phase 3a**(CL **57828**): Registry 에 런타임 훅 7종 신설 — `server_scan`·
  `server_status`·`server_pane_overview`·`server_input`·`server_paste`·`server_pending`·
  `server_usage_refresh`(플러그인 없으면 각각 False/None/no-op). serverio `_status_msg`
  (Claude 필드 빌드 + `_pane_claude_entry`)·`_pane_overview`·`_flush_loop`(scan/pending)·
  `_usage_loop`·`_handle_input`(입력 부수효과 5종)을 훅으로 라우팅. server.py `_write_paste`
  를 인스턴스 메서드화(→`server_paste`). 죽은 `from .claude import …` 제거. Claude 필드
  빌드는 plugin `server_status` 로 통째 이전(키/값 불변 → 서버 테스트 그대로 통과).
- **Phase 3b**(CL **57829**): Registry 에 `server_command` 훅 신설. `_handle_cmd` 의 Claude
  액션 19종(set_claude_*/token/pc/refresh_usage)을 plugin 으로 이전, 코어 else 분기는
  지시어(`handled`/`send_full`/`broadcast`)로 후속 회신만 수행. **결과: 코어
  (serverio.py·server.py)에 Claude 믹스인 메서드를 이름으로 부르는 코드 0건**(grep 확인).
- 검증: 412 passed, 빈 Registry 에서 모든 서버 훅 no-op 확인, driver smoke PASS.
- **서버 delete-to-disable = 완료**: `plugins/claude-code/` 삭제 시 서버는 에러 없이 동작
  (Claude 필드 없는 status·no-op 스캔/입력/명령). S4(Pane 필드 네임스페이스화)·S5(claude.py·
  tokens·usagedb·usageprobe 물리 이전)은 **선택적 정리**로 격하 — 계약 달성에는 불필요
  (코어에 남은 토큰 모듈/Pane 필드는 플러그인 부재 시 무해한 사장 데이터).

### Phase 2c 완료 — 클라 렌더/상태/ESC-nav delete-to-disable 완성
- **Phase 2c 완료**: 클라이언트의 모든 Claude 렌더/상태/클릭존을 플러그인으로 이전,
  코어는 레지스트리 훅 4종 + `getattr` 가드로만 닿는다. 디렉토리를 지우면 **헤더·상태줄
  세그먼트·footer 클릭존·토큰 팝업·ESC 헤더 동선이 전부 사라지고** 코어는 그대로 구동.
  - **신설 클라 훅 4종**(Registry): `client_render`(콘텐츠-레이어 — 프롬프트 스티키 헤더
    그리기 + footer 클릭존 스캔), `client_status`(status 메시지의 Claude 필드 흡수 —
    claude_header/rules 동기화·pane_claude 갱신·/usage 자동 팝업), `client_statusbar_update`
    (하단 상태줄 위젯에 Claude 필드 흡수), `client_statusbar`(상태줄 좌측 Claude 세그먼트
    렌더 + 클릭존). 모두 플러그인 부재 시 no-op.
  - **이전 대상**: `_draw_claude_headers`·`_scan_footer_zones`·`_footer_zone_at` →
    `plugins/claude-code/clientrender.py`; StatusBar `update_status`/`_render_main` 의 Claude
    블록 → `clientstatus.py`; `_update_claude`·`set_claude_header`·`toggle_header_hidden`·
    `_toggle_remote_control`·`open_remote_control`·`_claude_header_panes`·`open_claude_usage_
    tree`·`_open_usage_tree`·`_usage_tree_lines` → `__init__.py` 클로저(attach_client 설치).
  - **상태 이전**: `pane_claude`·`claude_header_on`·`_claude_hidden_panes`·`_claude_header_
    zones`·`_perm_zone`·`_remote_zone`·`_last_usage_shown_seq` 를 코어 `__init__` 에서
    제거하고 plugin `attach_client` 가 설치 → 코어/clientwidgets 는 `getattr(app,...,기본값)`
    로만 읽는다. StatusBar 의 `claude_*` 속성은 위젯 `__init__` 의 **안전한 기본값**으로
    남겨(model.py 의 Pane Claude 속성과 같은 관례) 플러그인이 흡수 훅으로 채운다.
  - **`_hdr_focus` 오버로드 분리**: Claude 헤더 포커스와 코어 탭-닫기[x] 포커스가 한
    `_handle_hdr_focus`(코어)에 얽혀 있던 것을 — 헤더 패널 목록 계산만 plugin `_claude_
    header_panes` 클로저로 빼고 코어는 `_hdr_panes()` 게이트(없으면 빈 목록)로 부른다.
    플러그인 부재 시 헤더 동선이 자연히 비고 닫기[x] 만 남는다.
  - **계약 테스트**: `tests/test_plugin_contract.py` — Registry 에서 claude-code 만 필터해
    부재를 시뮬레이션, 명령/훅/믹스인 no-op + 실제 클라 앱이 헤더/상태줄/ESC/클릭 경로에서
    안 깨지고 Claude 클로저·상태·세그먼트가 전혀 안 생김을 검증(2a/2b/2c 회귀망).
  - **검증 한계 보강**: driver 로는 client.py 가 안 도므로 `tests/test_client.py`(+contract)
    417→ 스위트가 회귀망. 이전된 심볼의 테스트 import 경로도 갱신(예: `_toggle_remote_
    control` 의 write_msg 패치를 `pytmuxlib.protocol` 로).

### 남은 단계(미완)
- **(선택) Phase 3 S4/S5 물리 이전**: Pane 의 Claude 필드를 `pane.plugin_state` 로,
  `claude.py`/`tokens`/`usagedb`/`usagelog`/`usageprobe` 를 플러그인으로 물리 이전(코어
  import 제거). delete-to-disable 계약엔 불필요하나 코어 표면을 더 줄이려면 정리 가치 있음.
- **Phase 2a 완료(CL 57844)**: 자족적 팝업 3종 → 플러그인. ModelCtxScreen·PermModeScreen
  → plugins/claude-code/screens.py; open_model_config/_apply_model_config·open_perm_mode·
  open_usage_panel → attach_client 클로저. 코어 콜러 getattr 가드. usage_bar_lines·
  InfoScreen 은 코어 공유 유지(plugin→core 절대 import).
- **Phase 2b 완료(CL 57856)**: 프롬프트 히스토리·토큰로그 팝업 → 플러그인. TokenLogScreen
  → screens.py; open_prompt_history·open_token_log·token_log 메시지(handle_message) → 플러그인.
  clientscreens 에서 TokenLogScreen·잉여 import(DataTable·Static·Text) 제거. 코어 콜러
  getattr 가드. **잠재 버그 수정**: TokenLogScreen [s] 의 미정의 app.open_claude_saver() →
  push_screen(ClaudeSaverScreen()) (크래시→정상).
- **Phase 2c 완료(위 절 참조)**: `_draw_claude_headers`·상태줄 세그먼트·`_scan_footer_zones`·
  ESC Claude 항목·통합 상태탭 토큰 탭을 전부 플러그인으로 이전(클라 훅 4종 신설). 통합
  상태탭(REC/토큰/서버 혼합)의 토큰 탭은 분리 난해 우려가 있었으나, 코어 `_open_status_tabs`
  가 `_usage_tree_lines` 를 `getattr` 폴백(없으면 안내문)으로 부르게 해 해결.
  - **구조 제약(이전 세션 발견, 그대로 적용됨)**: `PytmuxApp` 은 `build_client_app()` **팩토리 안에 지역
    정의**된 중첩 클래스라(메서드 8칸 들여쓰기), 서버처럼 동적 베이스 믹스인 합성
    (`class Server(*bases)`)을 못 쓴다 → Phase 2 는 ncd/`_saver_*` 의 **인스턴스 클로저 설치
    패턴**(`attach_client` 에서 `app.open_* = closure`)으로 옮기거나, 먼저 `PytmuxApp` 을
    모듈 최상위로 끌어올리는 선행 리팩토링이 필요(후자가 깔끔하나 팩토리 해체 위험 큼).
  - **검증 한계**: driver(소켓 클라)는 client.py 앱 코드를 실행하지 않아 Phase 2 는 smoke 로
    검증 불가. 단 `tests/test_client.py` 가 PytmuxApp 을 인스턴스화해 `open_prompt_history`/
    `open_token_log`/`ModelCtxScreen`/`_status_focus`/`usage_bar_lines` 를 직접 검증 → 412
    스위트가 회귀망. 이동 시 그 테스트들의 import 경로도 함께 갱신할 것.

## 5. 이번 세션에서 배운 점(lessons learned)
- **단계적 추출 + 단계별 제출**이 핫패스(30Hz)·렌더·공유 depot 가 얽힌 대형 리팩토링의
  위험을 가장 잘 통제한다. 각 단계는 독립적으로 동작하고 회귀(409 테스트)로 게이트한다.
- **통합 지점은 빠짐없이 한곳으로**: `CommandListScreen` 은 `_run_command` help 경로에서만
  플러그인 명령을 병합하고 **PromptScreen `?` 경로는 누락**돼 있었다(Claude 명령을 빼자
  드러남). 새 플러그인 표면을 만들 땐 *모든* 소비 지점을 점검할 것(CL 57795).
- **하이픈 패키지**는 `importlib.import_module` 로만 외부에서 불러온다(상대 import·내부는 무관).
- **프롬프트 히스토리 오검출**: `claude_prompt` 스크레이퍼 정규식 `^\s*(?:[│|]\s*)?>\s+...`
  은 **줄이 `> ` 로 시작하면** 잡는다. 셸 리다이렉트(`cmd > file`)가 줄바꿈으로 줄 첫머리에
  오면 사용자 프롬프트로 오인해 히스토리에 들어간다(이번 세션에 실제 발생). 입력 추적
  경로를 우선하고 스크레이프는 셸 명령 패턴(`> file`, `2>&1`, `; echo`, 파이프)을 배제하는
  하드닝 여지 있음 — Phase 3 의 프롬프트 추적 이전 시 함께 검토.
- **멀티라인 프롬프트**(Shift+Enter=LF, Enter=CR): 제출은 CR 로만 확정하고 LF 는 버퍼에
  누적해 한 번에 입력한 여러 줄을 **한 히스토리 항목**으로 기록(CL 57774).
- **IME 단축키/조합**: ESC 모드 키는 자모를 QWERTY 로 정규화해 IME 무관 동작(CL 57774).
  한글 조합 글자가 패널 테두리에 박히는 문제는 **하드웨어 커서 미이동**이 원인 —
  [IME_PREEDIT_CURSOR_SCENARIO.md](IME_PREEDIT_CURSOR_SCENARIO.md)(CL 57786). preedit 은
  OS 오버레이라 헤드리스로 검증 불가(실기 수동 확인 필요).
- **공유 `office` 클라이언트**: 항상 **번호 있는 changelist** 로 add/edit/submit(기본
  changelist 금지 — 병렬 세션이 파일을 가로챔). 병렬 `playground` 세션이 같은 파일
  (clientscreens/serverclaude/test_server)을 열고 있어도 번호 CL 이면 깨끗이 제출된다
  (동시 제출 시 서버가 CL 번호를 재부여하기도 함: 57793→57795).
- **무인자 명령은 레거시 프롬프트로 폴백하지 말 것**: `:rename-tab` 을 인자 없이 Enter
  하면 옛 rename 프롬프트 모달이 떴다 → **아무 동작 없이 취소**로 변경(이름 입력은
  `prefix+,` ghost 프롬프트로만), CL **57801**. 명령에 폴백 UI 를 달 땐 그 UI 가 여전히
  의도된 경로인지 점검.
- **"지금 위치"는 커서만으로 부족**: ncd 가 cwd 에서 시작해도 커서를 옮기면 현재
  디렉토리를 알 수 없었다 → cwd 행을 노랑+`◀` 로 **상시 강조**(CL **57803**). 탐색 UI 는
  시작 기준점을 영속 표시하면 길찾기가 쉬워진다.

## 6. 이번 세션 제출 CL 요약
- **57774** ncd 플러그인화 + ESC IME 정규화 + 멀티라인 프롬프트 1항목 + 명령목록 '전체' 탭
- **57786** docs/IME_PREEDIT_CURSOR_SCENARIO.md (한글 preedit 커서 분석·수정 시나리오)
- **57789** claude-code 플러그인 Phase 1 (claude-rules·token-saver 팝업)
- **57795** claude-code 플러그인 Phase 1b (Claude 명령 표면 전체) + PromptScreen `?` 병합 수정
- **57798** docs/PLUGIN_SYSTEM.md 신설 + HANDOFF 링크
- **57801** rename-tab 무인자 취소
- **57803** ncd 현재 디렉토리 표시
- (이후 세션 CL **57812**) claude-code Phase 3-S1: `ServerClaudeMixin` → `plugins/claude-code/
  servermixin.py`, Server 동적 베이스 합성(`server_mixins()` 훅) — §4
- **57828** claude-code Phase 3a: 서버 런타임 훅 7종(scan/status/pane_overview/input/paste/
  pending/usage_refresh) — 코어 serverio/server 호출부 라우팅
- **57829** claude-code Phase 3b: 서버 명령 훅 `server_command`(Claude 액션 19종 이전) —
  **코어→믹스인 직접 호출 0건, 서버 delete-to-disable 완성**
- **57842** docs: PLUGIN_SYSTEM Phase 3 완료 기록 + Phase 2 접근 메모
- **57844** claude-code Phase 2a: 팝업 3종(model/perm/usage) + ModelCtxScreen·PermModeScreen → 플러그인
- **57856** claude-code Phase 2b: 프롬프트 히스토리·토큰로그 팝업 + TokenLogScreen → 플러그인(+open_claude_saver 크래시 수정)
- **(이번 세션) claude-code Phase 2c**: 클라 렌더/상태/ESC-nav delete-to-disable 완성 —
  클라 훅 4종 신설(`client_render`/`client_status`/`client_statusbar_update`/`client_statusbar`),
  헤더·footer 스캔→`clientrender.py`, 상태줄 Claude 블록→`clientstatus.py`, 헤더/원격제어/
  토큰트리 메서드+상태→attach_client 클로저, `_hdr_focus` 오버로드 분리, **계약 테스트
  `tests/test_plugin_contract.py` 신설**. **클라 delete-to-disable 완성** — §4
- **(남은, 선택)** Phase 3 S4/S5 물리 이전(Pane 필드·claude.py/tokens 모듈) — §4·[HANDOFF.md](HANDOFF.md) §11.6
