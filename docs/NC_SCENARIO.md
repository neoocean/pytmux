# 코드네임 "nc" = NCD(Norton Change Directory) — 동작 시나리오

> **상태**: 🚧 구현 중. 본 문서는 구현 기준선이자 명세다.
> 시나리오 도입 CL: **57704**(초안) · 정정 CL: **57715**(NCD 로 정정).
> 구현 CL: **57709**(서버 디렉토리 목록 API 1차) · (이하 진행).
>
> **정정 메모(중요)**: 코드네임 "nc" 는 처음에 Norton Commander(듀얼 패널 파일
> 매니저)로 적었으나, 실제 요구사항은 거기 들어 있던 **`ncd.exe`(Norton Change
> Directory)** 다. NCD 는 파일 매니저가 아니라 **디렉토리 전용 트리로 빠르게 디렉토리를
> 바꾸는(cd) 도구**다. 본 문서·구현은 NCD 기준으로 한다.
>
> **한 줄 요약**: `:ncd` 로 **드라이브(루트 `/`) 전체의 디렉토리 트리**를 화면 가득
> 띄우되 **현재 디렉토리까지 펼쳐 그 위에 커서**를 둔다. ↑↓ 이동 · → 펼치기 · ← 접기 ·
> **디렉토리명 타이핑 = 즉시 점프(speed search)** · **Enter = 현재 패널에서 그 디렉토리로
> `cd` 후 닫기**. (pytmux 확장) **Shift+Enter / Ctrl+O = 그 디렉토리를 연 새 패널 분할.**
>
> **명령**: 명령 프롬프트/팔레트의 `ncd`(별칭 `nc`). 무인자·비파괴적 → 선택 즉시 실행.

## 목차
- [1. NCD 란 / 목표](#1-ncd-란--목표)
- [2. 사용자 흐름(UX)](#2-사용자-흐름ux)
- [3. 화면 레이아웃](#3-화면-레이아웃)
- [4. 아키텍처 — 어디에 무엇을 붙이나](#4-아키텍처--어디에-무엇을-붙이나)
- [5. 프로토콜 — 디렉토리 목록 요청/응답](#5-프로토콜--디렉토리-목록-요청응답)
- [6. 키 처리 · speed search · 열기 동작](#6-키-처리--speed-search--열기-동작)
- [7. 단계(구현 권고)](#7-단계구현-권고)
- [8. 테스트 계획](#8-테스트-계획)
- [9. 위험·완화·미해결](#9-위험완화미해결)

## 1. NCD 란 / 목표

옛 Norton Utilities 의 **NCD(Norton Change Directory)** 는 DOS 시절 디렉토리 이동
도구다. 핵심 동작:

1. 드라이브 전체의 **디렉토리만**(파일 없음) 트리를 화면 가득 그린다.
2. 방향키로 트리를 돌아다니고, **이름 일부를 타이핑하면 그 디렉토리로 즉시 점프**
   (speed search)한다.
3. **Enter 를 누르면 그 디렉토리로 바꾸고(cd) 종료**한다. Esc 는 취소.

즉 "지금 어디에 있든, 트리에서 아무 디렉토리나 골라 거기로 `cd`" 하는 도구다(파일
조작·미리보기·듀얼 패널은 NCD 의 범위가 **아니다** — 그건 Norton Commander).

**pytmux 목표**: `:ncd` 로 NCD 를 재현한다 — 루트(`/`)부터의 전체 디렉토리 트리를
**현재 패널 cwd 까지 펼쳐** 띄우고, 방향키+타이핑으로 골라 **Enter 로 현재 패널을 그
디렉토리로 cd**. pytmux 다움을 위해 **Shift+Enter/Ctrl+O 로 "그 디렉토리를 연 새 패널"**
도 제공한다(NCD 에 없는 확장).

## 2. 사용자 흐름(UX)

1. ESC 모드에서 `:` → `ncd`(또는 팔레트). 무인자라 즉시 실행.
2. 클라가 서버에 초기 목록을 요청(`request_nc_list`, path 생략). 서버는 **루트(`/`)부터
   현재 패널 cwd 까지의 조상 사슬(chain)**과 각 조상의 직계 하위 디렉토리를 함께 회신
   (`cwd` 포함).
3. 화면 전체를 덮는 `NcdScreen` 을 push. 트리는 **루트부터 cwd 까지 이미 펼쳐져** 있고
   **커서는 cwd 에** 놓인다 — "지금 여기"에서 시작해 어디로든 갈 수 있다.
4. 조작:
   - ↑/↓: 이동.
   - →: 접힌 디렉토리의 하위를 **지연 로드**(같은 요청에 그 path)해 펼친다.
   - ←: 펼침이면 접기, 접힘이면 부모로 이동(루트 방향으로 거슬러 올라감).
   - **글자/숫자 타이핑**: speed search — 현재 위치부터 순환하며 basename 이 입력
     접두로 시작하는 첫 디렉토리로 점프(없으면 부분일치). 방향키를 누르면 검색어 리셋.
     Backspace 로 한 글자 지움.
5. 목표 디렉토리에서:
   - **Enter** → 닫고 현재 패널 셸에 `cd <선택경로>\n` 전송(= NCD 의 핵심).
   - **Shift+Enter / Ctrl+O** → 닫고 `split` 을 `path=<선택경로>` 로 보내 그 디렉토리에서
     시작하는 새 패널 생성(pytmux 확장; Ctrl+O 는 Shift+Enter 미지원 터미널 폴백이자
     speed search 글자와 충돌하지 않는 키).
6. Esc → 취소(아무 동작 없이 닫기).

## 3. 화면 레이아웃

전체 화면 모달. 디렉토리 전용 트리(파일 없음). 루트→cwd 펼침, cwd 에 커서.

```
┌─ ncd → /Users/woojin/p4/playground/scripts/pytmux ───────────────────┐
│ ▾ Users                                                              │
│   ▾ woojin                                                           │
│     ▸ Documents                                                      │
│     ▾ p4                                                             │
│       ▾ playground                                                   │
│         ▾ scripts                                                    │
│           ▾ pytmux           ← 커서(현재 cwd, 하이라이트)            │
│             ▸ docs                                                   │
│             ▸ pytmuxlib                                              │
│             ▸ tests                                                  │
│   ▸ opt                                                              │
│   ▸ var                                                              │
├──────────────────────────────────────────────────────────────────────┤
│ ↑↓ 이동  →펼치기 ←접기  타이핑=찾기  Enter cd  ⇧Enter/^O 새 패널  Esc │
└──────────────────────────────────────────────────────────────────────┘
```

- border_title: `ncd → <현재 cwd>`. border_subtitle: 키 힌트(+ speed search 중이면 `찾기:<문자>`).
- 디렉토리만 표시. 접힘 `▸`, 펼침 `▾`, 잎(자식 없음) 공백.
- 루트(`/`)는 제목이 아니라 트리 최상단 노드들(그 직계 하위)부터 그린다.
- 구현은 `ListView` 로 트리를 평탄화해 그린다(clientscreens 의 `ChooseTreeScreen`/
  `CommandListScreen` 패턴; 별 파일 `clientnc.py` — 진행 중 WIP 와 CL 격리).

## 4. 아키텍처 — 어디에 무엇을 붙이나

서버가 PTY·파일시스템(원격 포함)의 권위 소유자이므로 디렉토리 목록은 서버가 제공하고
클라는 표시·탐색만 한다(기존 `request_tree`/`request_version` 와 동일 패턴).

**클라이언트**
- 명령: `clientutil.COMMANDS` 에 `("ncd", …, "탐색")`, 별칭 `nc`. `COMMAND_NOARG` 에
  `ncd`·`nc` 추가(즉시 실행). `COMPLETIONS` 자동 포함.
- 디스패치: `client.py:_run_command` 에 `if c in ("ncd","nc"): self.request_nc_list()`.
- 요청: `request_nc_list(path=None)` → `send_cmd("request_nc_list", path=path)`.
- 응답: 수신 루프 `elif t == "nc_list": self._on_nc_list(msg)`. 초기(path None) →
  `NcdScreen(root, chain, cwd)` push. 펼치기(path 有) → 화면의 `fill_children`.
- 모달: `clientnc.py` 의 `NcdScreen(ModalScreen)`. chain 으로 루트→cwd 펼친 트리를
  만들고 cwd 선택. 키·speed search 처리. 결과 `dismiss(("cd"|"newpane", path))`.
- 콜백 `_nc_done`: cd → `send_input("cd <quoted>\n")`; newpane → `send_cmd("split", path=…)`.

**서버**
- `serverio._handle_cmd`: `action == "request_nc_list"` → `nc_list_msg(sess, path)`.
- `servertree.nc_list_msg(sess, path)`:
  - `path` 有 → 그 노드의 직계 하위(`dirs`)만 회신(지연 펼치기, 기존과 동일).
  - `path` 없음 → **루트→cwd 조상 사슬**을 만들어 각 조상의 직계 하위와 함께 `chain`
    으로 회신(+ `cwd`). cwd 추정 불가 시 루트만.
- `servertree._list_dirs(path)`: 직계 하위 디렉토리만 이름순 절대경로(파일·숨김 제외,
  graceful) — 1차 구현(57709)에서 추가됨, 재사용.
- `servertree._ancestor_chain(cwd)`: 루트부터 cwd 까지 경로 리스트.

**기존 자산 재사용(서버 신규 거의 불필요)**
- 새 패널 cwd 지정은 `split_pane(path=…)` + `_resolve_start_cwd` 가 이미 처리 → 확장의
  서버 코드 불필요. 현재 패널 cd 는 `send_input` 으로 충분.

## 5. 프로토콜 — 디렉토리 목록 요청/응답

요청(클라→서버):
```json
{"t":"cmd","action":"request_nc_list","path":null}        // 초기: 루트→cwd 펼친 트리
{"t":"cmd","action":"request_nc_list","path":"/abs/dir"}  // 펼치기: 그 노드 자식
```
초기 응답(서버→클라) — 사슬과 각 단계 자식을 함께:
```json
{"t":"nc_list","root":"/","path":null,
 "cwd":"/Users/woojin/p4/playground/scripts/pytmux",
 "chain":[["/",            ["/Users","/opt","/var", …]],
          ["/Users",       ["/Users/woojin", …]],
          ["/Users/woojin",["/Users/woojin/Documents","/Users/woojin/p4", …]],
          ["…",            ["…"]],
          ["…/pytmux",     ["…/pytmux/docs","…/pytmux/pytmuxlib","…/pytmux/tests"]]]}
```
- `chain` 은 루트→cwd 순서. 각 항목 `[디렉토리, [그 직계 하위 디렉토리…]]`.
- 클라는 chain 의 각 디렉토리를 펼친 상태로 트리를 만들고 `cwd` 행을 선택한다.
- 숨김 디렉토리가 cwd 경로에 끼어도 사슬은 보이도록, 각 부모의 자식 목록에 다음 사슬
   원소를 보장 포함한다(없으면 추가·정렬).

펼치기 응답:
```json
{"t":"nc_list","root":"/abs/dir","path":"/abs/dir","dirs":["/abs/dir/x", …]}
```
- `path` 에 요청 노드 절대경로를 echo 해 클라가 노드를 매칭. `dirs` 는 절대경로.

## 6. 키 처리 · speed search · 열기 동작

`NcdScreen.on_key`(요약):
- `up`/`down`: ListView 기본 이동(+ speed search 버퍼 리셋).
- `right`: 접힌 노드면 `request_nc_list(path)` 로 펼치기(미로드)·즉시 펼치기(로드됨).
- `left`: 펼침이면 접기, 접힘이면 부모 행으로 이동.
- `enter`: `dismiss(("cd", cur))`.
- `shift+enter`, `ctrl+o`: `dismiss(("newpane", cur))`.
- `escape`: `dismiss(None)`.
- `backspace`: speed search 버퍼 한 글자 삭제 후 재점프.
- 그 외 **출력 가능한 한 글자**: speed search 버퍼에 추가 후 점프.

speed search(점프) 규칙:
- 현재 선택 위치부터 순환하며 basename 이 버퍼로 **시작**하는 첫 디렉토리로 이동;
  없으면 **부분일치**로 한 번 더. 대소문자 무시.
- 방향키 이동 시 버퍼 리셋(증분 검색 관례). 버퍼는 border_subtitle 에 `찾기:<문자>` 로
  노출.

콜백 `_nc_done`:
```python
if action == "cd":
    self.send_input(f"cd {shlex.quote(path)}\n".encode())
elif action == "newpane":
    self.send_cmd("split", orient="lr", path=path)
```

### 6-A. 키 충돌·터미널 주의
- **speed search 가 글자 키를 차지**하므로, 새 패널 폴백을 글자(`o`)로 둘 수 없다 →
  **`Ctrl+O`**(출력 불가 제어문자라 검색과 충돌 안 함)로 둔다. Shift+Enter 는 지원
  터미널에서 동작(미지원 시 Ctrl+O 사용).
- cd 전송은 **대상 패널이 셸 프롬프트 상태일 때만** 의미가 있다(claude/vim 실행 중이면
  그 앱 입력으로 들어감 — 실제 셸 cd 와 같은 한계). 설계 한계로 수용.

## 7. 단계(구현 권고)

1. **서버 초기 사슬** — `nc_list_msg` 를 path None 일 때 루트→cwd `chain` + `cwd` 회신
   으로 확장(`_ancestor_chain` 추가, 숨김 경유 보강). 펼치기(path 有)는 그대로.
2. **클라 모달 NCD** — `NcdScreen(root, chain, cwd)`: 사슬로 펼친 트리·cwd 선택,
   지연 펼치기, ←/→, Enter=cd, Shift+Enter/Ctrl+O=새 패널, Esc.
3. **speed search** — 글자/Backspace 처리, 점프, 방향키 리셋, 힌트 노출.
4. **명령 등록** — `ncd`(별칭 `nc`), NOARG, 디스패치.
5. **회귀 테스트** — `tests/test_nc.py`(§8).
6. **문서** — MANUAL/FEATURES/HANDOFF 갱신, 본 시나리오 상태 갱신.

## 8. 테스트 계획 (`tests/test_nc.py`)

서버(IPC 없이 직접 호출):
- `_list_dirs`: 디렉토리만·정렬·숨김/파일 제외·하위경로·빈/없는/파일경로 graceful.
- `nc_list_msg(path 有)`: 그 노드 자식·path echo.
- `nc_list_msg(path None)`: `chain` 이 루트→cwd 순, 각 항목 [dir, 자식], 마지막이 cwd,
  `cwd` 필드 일치; 숨김 경유 시에도 사슬 보존(부모 자식에 다음 원소 포함).
- `_ancestor_chain`: 임의 경로 → 루트 시작·cwd 끝·순서.

클라(Textual headless):
- 명령 `ncd`/별칭 `nc` → `request_nc_list(path=None)`.
- 초기 nc_list(chain) → NcdScreen 열림, **cwd 행이 선택**, 사슬이 펼쳐져 보임(행 수).
- Enter → `cd <quoted>\n`(공백 인용 포함) send_input, 화면 닫힘.
- Shift+Enter·Ctrl+O → `split(path=…)`(동치).
- → 지연 펼치기: request 발생 → fill 후 행 증가. ← 접기: 행 감소.
- speed search: 글자 입력 → 매칭 디렉토리로 선택 이동; 방향키 후 리셋.
- Esc → 닫힘.

## 9. 위험·완화·미해결

- **대형 트리/네트워크 마운트**: 초기엔 루트→cwd 사슬 + 각 단계 직계만 읽고, 나머지는
  지연 로드 → 가볍다. 느린 마운트는 펼칠 때만 비용.
- **speed search vs 새 패널 키 충돌** → Ctrl+O 로 회피(§6-A).
- **cd 가 셸 아닌 앱에 들어감** → 한계 명시(§6-A). 후속: 대상이 셸 아니면 자동 새 패널.
- **원격(ssh) 패널 cwd** — `_pane_cwd` 는 서버 로컬 기준이라 ssh 실제 cwd 와 다를 수
  있음 → 1차는 로컬 패널, OSC 7 기반 정확화는 후속(미구현).
- **숨김 디렉토리** — 기본 비표시지만 cwd 경로에 끼면 사슬엔 보이게 보강. 토글은 후속.
- **미해결/후속**: ① 숨김 표시 토글 ② 새 "탭"으로 열기 ③ 즐겨찾기/최근 ④ 디렉토리
  생성/삭제(NCD 의 MD/RD) ⑤ ssh 원격 cwd 정확화 ⑥ 셸 아닌 패널에서 Enter 자동 새 패널.
