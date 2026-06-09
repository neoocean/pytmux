# 코드네임 "nc" — Norton Commander 풍 디렉토리 트리 — 동작 시나리오

> **상태**: 📋 설계(미구현). 본 문서는 구현 전 설계 기준선이자 명세다.
> 시나리오 도입 CL: **57704**. 구현 CL: (미정).
>
> **한 줄 요약**: `:nc` 명령으로 **화면 전체를 덮는 디렉토리 트리**(Norton Commander
> 풍)를 띄우고, 방향키로 디렉토리를 탐색한 뒤 **Enter = 현재 패널에서 그 디렉토리로
> `cd`**, **Shift+Enter = 그 디렉토리를 연 새 패널 분할**.
>
> **명령**: 명령 프롬프트/팔레트의 `nc`(별칭 `tree`·`files`). 무인자·비파괴적이라
> 선택 즉시 실행(`COMMAND_NOARG`).
> **키**: 트리 안에서 ↑/↓ = 이동, →/Enter(접힌 노드) = 펼치기, ← = 접기/부모로,
> **Enter = cd(현 패널)**, **Shift+Enter = 새 패널(분할)에서 열기**, Esc = 닫기.
> Shift+Enter 미지원 터미널 폴백: **`o`**(open in new pane) 동일 동작(주의 §6-A).

## 목차
- [1. 배경과 목표](#1-배경과-목표)
- [2. 사용자 흐름(UX)](#2-사용자-흐름ux)
- [3. 화면 레이아웃](#3-화면-레이아웃)
- [4. 아키텍처 — 어디에 무엇을 붙이나](#4-아키텍처--어디에-무엇을-붙이나)
- [5. 프로토콜 — 디렉토리 목록 요청/응답](#5-프로토콜--디렉토리-목록-요청응답)
- [6. 키 처리와 두 가지 열기 동작](#6-키-처리와-두-가지-열기-동작)
- [7. 단계(구현 권고)](#7-단계구현-권고)
- [8. 테스트 계획](#8-테스트-계획)
- [9. 위험·완화·미해결](#9-위험완화미해결)

## 1. 배경과 목표

pytmux 로 작업하다 보면 패널 셸을 깊은 디렉토리로 옮기거나, 어떤 디렉토리를 새 패널로
바로 열고 싶을 때가 잦다. 지금은 셸에서 직접 `cd ...` 를 치거나 `split-window` 후 다시
`cd` 해야 한다. 디렉토리 구조가 한눈에 안 보이고, 오타·경로 기억 부담이 있다.

**목표**: 옛 Norton Commander(또는 mc, ranger)처럼 **트리를 화면에 펼쳐 방향키만으로**
디렉토리를 고르고, 두 가지 동작 중 하나로 즉시 연결한다.

- **Enter** — 현재 패널의 셸에서 선택 디렉토리로 `cd`(패널 그대로, 위치만 이동).
- **Shift+Enter** — 선택 디렉토리를 cwd 로 가지는 **새 패널을 분할로 생성**(원 패널 유지).

비파괴적·보조적 도구이므로 기존 명령/팔레트 체계에 자연스럽게 얹는다.

## 2. 사용자 흐름(UX)

1. 사용자가 ESC 모드에서 `:` → `nc` 입력(또는 팔레트 `?`→`nc` 선택). `nc` 는
   `COMMAND_NOARG` 라 즉시 실행된다(`clientutil.py:525` 집합에 추가).
2. 클라이언트가 서버에 **현재 활성 패널 cwd 기준 목록**을 요청(`request_nc_list`,
   path 생략). 서버는 활성 패널의 cwd(`servertree._pane_cwd`)를 루트로 잡아 그 직계
   하위 디렉토리를 회신한다.
3. 응답이 오면 화면 전체를 덮는 `NortonCommanderScreen`(ModalScreen)을 push.
   루트 경로가 상단 제목줄에, 직계 하위 디렉토리가 트리로 나열되고 첫 항목이 하이라이트.
4. ↑/↓ 로 이동. 접힌 디렉토리에서 →(또는 Enter) 를 누르면 그 디렉토리의 하위를
   **지연 로드**(같은 `request_nc_list` 에 해당 path 전달)해서 펼친다. ← 는 접거나
   이미 접혀 있으면 부모로 이동.
5. 원하는 디렉토리에 커서를 둔 채:
   - **Enter** → 화면을 닫고, 현재 패널 셸에 `cd <선택경로>\n` 전송.
   - **Shift+Enter**(폴백 `o`) → 화면을 닫고, `split` 명령을 `path=<선택경로>` 로
     전송해 그 디렉토리에서 시작하는 새 패널을 만든다.
6. Esc 는 아무 동작 없이 닫는다.

## 3. 화면 레이아웃

전체 화면 모달. mc 풍의 단일 트리 패널(2-pane 듀얼 패널은 §9 후속).

```
┌─ nc · /Users/woojin/p4/playground/scripts/pytmux ────────────────────┐
│ ▾ pytmux                                                              │
│   ▸ captures                                                          │
│   ▾ docs                                                              │
│       ▸ benchmark                                                     │
│       ▸ image                                                         │
│   ▸ db                                                                │
│ ▶ pytmuxlib                            ← 커서(하이라이트)             │
│   ▸ scripts                                                           │
│   ▸ tests                                                             │
│                                                                       │
│                                                                       │
├──────────────────────────────────────────────────────────────────────┤
│ ↑↓ 이동  → 펼치기  ← 접기  Enter cd  ⇧Enter/o 새 패널  Esc 닫기        │
└──────────────────────────────────────────────────────────────────────┘
```

- 제목줄: `nc · <루트 절대경로>`.
- 본문: 디렉토리만(파일 비표시 — 목적이 cd/패널 열기이므로). 접힘 `▸`, 펼침 `▾`.
- 하단: 키 힌트 한 줄(다른 모달과 동일 관례).
- 구현은 Textual `Tree` 위젯 또는 `ListView`(평탄화한 트리 행) 둘 다 가능 —
  본 코드베이스의 `ChooseTreeScreen`(`clientscreens.py:534`)이 `ListView` 로 트리
  들여쓰기를 평탄화해 그리므로 **그 패턴 재사용**(키 처리·dismiss 규약 일관)을 권장.

## 4. 아키텍처 — 어디에 무엇을 붙이나

서버가 PTY·파일시스템(원격 포함)의 권위 소유자이므로 **디렉토리 목록은 서버가 제공**하고
클라는 표시·탐색만 한다(기존 `request_tree`/`tree`, `request_version`/`version` 와 동일한
요청/응답 패턴).

**클라이언트**
- 명령 등록: `clientutil.py:371` `COMMANDS` 에 `("nc", "Norton Commander 풍 디렉토리
  트리 — ↑↓ 탐색·Enter cd·⇧Enter 새 패널", "탐색")`, 별칭 `tree`·`files`. `COMMAND_NOARG`
  (`clientutil.py:525`)에 `nc`(+별칭) 추가. `COMPLETIONS` 에는 `[c[0] for c in COMMANDS]`
  로 자동 포함.
- 디스패치: `client.py:_run_command`(2452~)에 `if c in ("nc","tree","files"): self.request_nc_list()`.
- 요청 헬퍼: `request_tree`(`client.py:1740`) 본을 따 `request_nc_list(self, path=None)`
  → `self.send_cmd("request_nc_list", path=path)`. 펼치기/초기 진입 모두 같은 헬퍼.
- 응답 핸들러: 클라 수신 루프(`{"t":"tree"}`/`{"t":"version"}` 처리하는 곳)에 `nc_list`
  케이스 추가. 첫 응답이면 `NortonCommanderScreen` push, 이미 떠 있으면 해당 노드에
  자식을 채워 펼친다.
- 모달: `clientscreens.py` 에 `NortonCommanderScreen(ModalScreen)` 신설.
  `ChooseTreeScreen`(534~591)을 본으로 — `compose` 에서 평탄화 행을 `ListView` 로,
  `on_mount` 에서 포커스, `on_key` 에서 ↑↓/→←/Enter/Shift+Enter/`o`/Esc 처리,
  선택 결과는 `self.dismiss((action, path))` 로 반환. 콜백 `_nc_done` 에서 §6대로 분기.

**서버**
- 핸들러: `serverio._handle_cmd`(411~)에 `elif action == "request_nc_list":` 추가.
  `path` 가 비면 활성 패널 cwd(`servertree._pane_cwd`, 176~196 — Linux `/proc`,
  macOS `lsof` 폴백)를 루트로, 있으면 그 경로의 직계 하위 디렉토리를 나열해
  `{"t":"nc_list", "root":<루트>, "path":<요청경로>, "dirs":[이름…]}` 회신
  (`token_log`/`version` 회신처럼 `write_msg`).
- 디렉토리 나열은 서버 측 `os.scandir` 로 디렉토리만, 이름순, 숨김(`.`)은 옵션
  (기본 비표시). 권한 오류·심링크 루프는 빈 목록으로 graceful.

**기존 자산 재사용(서버 측 대부분 이미 존재)**
- 새 패널 cwd 지정은 `split_pane(sess, orient, path=...)`(`servertree.py:18`)이
  이미 `_resolve_start_cwd`(198~212)로 `path` 를 해석한다. **Shift+Enter 는 신규
  서버 코드 없이** `send_cmd("split", orient="lr", path=<dir>)` 만으로 동작.
- 현재 패널 `cd` 는 `send_input`(`client.py:1612`)으로 `cd <quoted>\n` 바이트 전송
  → 서버 `_handle_input` → `pane.pty.write`. 신규 서버 코드 불필요.

## 5. 프로토콜 — 디렉토리 목록 요청/응답

요청(클라→서버):
```json
{"t":"cmd","action":"request_nc_list","path":null}   // 초기: 활성 패널 cwd 루트
{"t":"cmd","action":"request_nc_list","path":"/abs/dir"}  // 펼치기: 그 노드 자식
```
응답(서버→클라):
```json
{"t":"nc_list","root":"/Users/woojin/.../pytmux","path":null,
 "dirs":["captures","db","docs","pytmuxlib","scripts","tests"]}
```
- `path:null` 응답의 `root` 가 트리 루트 절대경로. `dirs` 는 그 직계 하위.
- `path:"/abs/dir"` 응답은 해당 노드의 자식 채움용(`root`=그 dir, `dirs`=자식).
- 자식이 없으면 `dirs:[]` → 트리에서 잎(펼칠 것 없음)으로 표시.
- 절대경로 합성은 클라가 `root`+이름으로, 또는 서버가 풀경로 배열로 줘도 됨(택일,
  구현 시 한쪽 고정). 권장: 서버가 **풀경로**까지 줘서 클라의 경로 조합 버그 여지 제거.

## 6. 키 처리와 두 가지 열기 동작

`NortonCommanderScreen.on_key`:
- `up`/`down`: `ListView` 선택 이동.
- `right`: 접힌 디렉토리면 `request_nc_list(path=cur)` 로 펼치기 요청; 이미 펼침이면
  첫 자식으로.
- `left`: 펼침이면 접기; 접힘이면 부모 행으로 커서 이동.
- `enter`: `self.dismiss(("cd", cur_path))`.
- `shift+enter`: `self.dismiss(("newpane", cur_path))`.
- `o`(폴백): `shift+enter` 와 동일.
- `escape`: `event.stop(); self.dismiss(None)`.

콜백:
```python
def _nc_done(self, result):
    if not result:
        return
    action, path = result
    if action == "cd":
        self.send_input(f"cd {shlex.quote(path)}\n".encode())
    elif action == "newpane":
        self.send_cmd("split", orient="lr", path=path)
```

### 6-A. Shift+Enter 검출 — 핵심 주의
대다수 터미널은 **Shift+Enter 를 보통 Enter 와 구분하지 못한다**(Kitty 키보드 프로토콜
등 확장 모드에서만 별도 시퀀스). 본 프로젝트도 같은 한계를 이미 안고 있다 — `send-escape`
명령이 "Shift+ESC 안 먹는 터미널용" 폴백으로 존재한다(`clientutil.py:404`). 따라서:
- **Shift+Enter 를 1차**로 시도하되(지원 터미널에서 자연스러움),
- **모든 터미널에서 동작하는 폴백 키 `o`(open in new pane)를 1급 경로로 문서화·노출**한다.
- 하단 힌트에 `⇧Enter/o 새 패널` 을 함께 표기해 폴백을 항상 보이게 한다.
- Textual 이 `event.key` 로 `"shift+enter"` 를 주는 경우에만 그 분기가 발동하고, 아니면
  `o` 로 받는다 — 두 경로가 같은 `("newpane", path)` 로 수렴.

### 6-B. cd 의 의미적 전제
`cd` 전송은 **대상 패널이 셸 프롬프트 상태일 때만** 의미가 있다(패널에서 claude/vim 등이
실행 중이면 그 앱 입력으로 들어간다 — 실제 셸 cd 와 동일한 한계). 이는 설계 한계로 수용하고,
헤더/문서에 명시. (선택적 개선: 활성 패널이 셸이 아니면 Enter 시 자동으로 새 패널 동작으로
대체 — §9 후속.)

## 7. 단계(구현 권고)

1. **서버 목록 API** — `serverio._handle_cmd` 에 `request_nc_list`,
   `servertree` 에 `_list_dirs(path|None)`(cwd 폴백 + `os.scandir`). 단위 테스트
   먼저(임시 디렉토리 트리 → 응답 검증).
2. **명령 등록** — `COMMANDS`/`COMMAND_NOARG`/별칭. 팔레트·자동완성에 노출 확인.
3. **모달 화면** — `NortonCommanderScreen`(ListView 평탄 트리), 초기 루트 렌더, ↑↓ 이동,
   Esc 닫기까지(열기 동작 전).
4. **지연 펼치기** — →/Enter(접힘)로 `request_nc_list(path)` → 자식 삽입/접기.
5. **두 열기 동작** — Enter=cd(`send_input`), Shift+Enter/`o`=split(`send_cmd path`).
   `_nc_done` 콜백 배선.
6. **키 힌트·문서** — 하단 힌트, `docs/MANUAL.md`·`FEATURES.md` 항목 추가, HANDOFF § 갱신.
7. **회귀 테스트** — `tests/test_nc.py`(§8).

## 8. 테스트 계획 (`tests/test_nc.py`)

서버 로직은 IPC 없이 직접 호출로 검증(다른 server 테스트 관례 따름).
- `test_nc_list_root_uses_active_pane_cwd` — 활성 패널 cwd 를 임시 트리로 두고
  `path=None` 요청 → `root` 가 그 cwd, `dirs` 가 직계 하위와 일치(파일 제외).
- `test_nc_list_lists_only_dirs_sorted` — 파일·디렉토리 혼재 → 디렉토리만, 이름순.
- `test_nc_list_subpath` — `path=<하위>` → 그 자식만.
- `test_nc_list_permission_error_graceful` — 접근 불가 경로 → `dirs:[]`, 예외 없음.
- `test_nc_list_empty_dir` — 빈 디렉토리 → `dirs:[]`.
- `test_nc_enter_sends_cd` — 클라 `_nc_done(("cd", p))` 가 `cd <quoted>\n` 을
  활성 패널로 `send_input`(인용·개행 포함) 호출함을 모킹 검증.
- `test_nc_shift_enter_splits_with_path` — `_nc_done(("newpane", p))` 가
  `send_cmd("split", path=p)` 를 부름.
- `test_nc_fallback_o_equals_shift_enter` — 화면 `on_key` 가 `o`/`shift+enter`
  모두 `("newpane", path)` 로 dismiss.
- `test_nc_esc_dismisses_none` — Esc → `dismiss(None)`, 부작용 없음.
- (통합) split 이 `_resolve_start_cwd(path)` 로 그 디렉토리에서 셸을 띄우는지
  — 기존 `split_pane` 경로 재사용이므로 `test_server` 의 split+path 케이스로 커버.

## 9. 위험·완화·미해결

- **Shift+Enter 미검출**(터미널 한계) → `o` 폴백 1급 노출(§6-A). 가장 큰 UX 리스크.
- **cd 가 셸 아닌 앱에 들어감**(§6-B) → 한계 명시, 후속으로 "셸 아니면 새 패널로 대체"
  옵션 검토.
- **대형/네트워크 디렉토리 스캔 지연** → `os.scandir` 직계만(재귀 없음)으로 가볍게,
  지연 펼치기로 필요한 노드만 로드. 그래도 느린 마운트는 응답 전 로딩 표시 고려.
- **원격(ssh) 패널의 cwd** — 현 `_pane_cwd` 는 서버 로컬 프로세스 기준(원격 셸의
  실제 cwd 아님). ssh 패널에서는 루트가 부정확할 수 있음 → 1차 범위는 로컬 패널,
  ssh 정확 cwd 는 후속(OSC 7 트래킹은 미구현, `servertree` 주석 참조).
- **심링크 루프/권한** → graceful 빈 목록, 트리 깊이 무제한 펼침이지만 지연 로드라 안전.
- **미해결/후속**: ① mc 풍 듀얼 패널(좌우 2트리) ② 파일도 표시 + 미리보기/열기
  ③ 즐겨찾기·최근 경로 ④ 입력 필터(타이핑으로 점프) ⑤ 새 "탭"으로 열기(분할 대신)
  ⑥ Enter 대상이 셸 아닐 때 자동 새 패널 ⑦ ssh 패널 원격 cwd 정확화.
