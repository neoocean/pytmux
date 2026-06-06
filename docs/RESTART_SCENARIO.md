# 작업 보존 서버 재시작 — 동작 시나리오

> **상태**: ✅ 구현됨(방식 ① 제자리 re-exec). 본 문서는 설계 기준선이자 구현 명세다.
> 시나리오 도입 CL: **56541**. 구현 CL: **56543**(상태 직렬화) · **56545**(fd 채택·복원·
> 부트 배선) · **56546**(restart-server 명령=os.execv + 종단간 검증) · **56547**(클라이언트
> 재접속 ⓔ) · 56549(명령 팔레트 노출 + 문서) · 56550(alt-screen 재그리기 유도 = 대안
> B) · 본 CL(스크롤백 연속성 검증). 회귀: `tests/test_restart.py`(9 케이스).
>
> **명령**: 명령 프롬프트/팔레트의 `restart-server`(별칭 `restart`), 또는 외부에서
> `python3 pytmux.py cmd restart-server`. **alt-screen 재그리기**: 실 박스 검증 완료 —
> 복원 후 `_induce_redraw_all` 이 각 패널 PTY 에 SIGWINCH 를 유발해 vim/claude 등이
> repaint 한다(주의 ① 대안 B). 회귀 `test_restore_induces_altscreen_redraw`.
> **스크롤백 연속성**: 실 박스 검증 완료 — 화면 밖으로 밀린 줄이 메인 화면 평문
> 스냅샷으로 복원돼 재시작 후 맨 위로 스크롤하면 다시 보인다(회귀
> `test_restore_preserves_scrollback`; 초기 줄 1~5 가 스크롤백에서 복원됨을 확인).
> 완전한 pyte 내부 상태 직렬화(대안 A)는 비채택.
>
> **전체 재시작 `restart-all`**(별칭 `full-restart`): 서버 work-preserving re-exec
> **+ 클라이언트 자기 relaunch**를 한 명령으로. 서버는 위와 동일하게 셸/세션을 보존한
> 채 코드만 교체하고, 클라는 in-place 재접속(ⓔ) 대신 **자신을 `os.execv` 로 교체**해
> 새 클라 코드로 다시 attach 한다(서버·클라 코드 모두 갱신, 작업 보존). 동작: 클라가
> `_relaunch_on_restart` 를 무장하고 `restart_server` 전송 → `{"t":"restarting"}` →
> 서버 re-exec 로 연결이 끊기면, 끊김 핸들러가 재접속 대신 `_relaunch` 를 세우고 종료
> → textual 이 터미널을 정상복구한 뒤 `run_client` 가 원 인자(`sys.argv`)로 재실행
> → 새 클라가 (상속 listen 소켓 덕에 큐잉되는) 재-exec 서버에 재접속. 클라 코드 변경
> (`client.py`/`clientwidgets.py`/`clientscreens.py`)까지 한 번에 반영하는 "전체 업그레이드".
>
> **드라이런 점검 `restart-check`**(별칭 `restart-dry-run`): restart-all 을 **실행하지
> 않고** 안전성만 점검해 PASS/FAIL 팝업으로 보고한다(부작용 없음 — 상태를 임시
> 직렬화/역파싱만). 점검: ① 서버 re-exec 지원(POSIX·이벤트루프) ② 복원할 세션 존재
> ③ 상태 직렬화 round-trip(`_resume_payload`→json dump/load/구조검증) ④ 모든 패널이
> 살아있는 master fd 보유(상속해야 셸이 산다) ⑤ 클라 relaunch 인자 해석 가능. 더불어
> 실행 버전 vs 디스크 버전을 함께 보여(다르면 "재시작이 새 코드를 로드"한다는 뜻 —
> 위험이 아니라 갱신). 서버 점검은 `restart_check()`(serverpersist), 회신을 클라가
> 자기 측 점검과 합쳐 팝업.

## 1. 배경과 목표

pytmux 는 활발히 개발 중이라 서버 코드(`server.py`/`model.py`/`protocol.py`/`claude.py`)를
바꾸면 `kill-server` 후 재기동해야 반영된다(HANDOFF §2 "데몬 재시작 주의"). 그런데 그 순간
**열려 있던 모든 패널의 셸·실행 중 프로그램(claude/vim/빌드 등)이 함께 죽는다.** 동시에
pytmux 로 실제 작업을 하고 있어 재시작 부담이 크다.

기존 레이아웃 저장(`save_layout`/슬롯, `pytmuxlib/server.py:969-981`)은
`_serialize_node`(`server.py:953-957`)가 **트리 구조와 패널 제목만** 직렬화하고,
복원 시 `restore_layout`→`_build_node`→`spawn_pane`(`server.py:983-1010`, `90`)으로
**새 셸을 띄운다.** 즉 구조는 살지만 **셸 프로세스·실행 중 프로그램·스크롤백은 소실** →
작업 연속성에는 도움이 안 된다.

**목표**: 지금 열려 있는 탭·패널의 **살아 있는 셸/프로그램과 그 PTY 를 보존한 채** 서버
코드만 새 이미지로 교체한다.

## 2. 핵심 난점

**패널 = 서버가 소유한 PTY master fd + 자식 셸 프로세스**다.
- 서버를 죽이면(`kill-server`) 자식 셸도 함께 죽는다.
- 죽이지 않으면 새 서버 코드가 안 올라온다.

따라서 보존 재시작은 "**프로세스/PTY 는 살린 채 서버 코드만 교체**"여야 한다. tmux 가
서버 업그레이드에 쓰는 제자리 re-exec(`os.execv`)가 이 요건을 만족한다.

## 3. 방식 ① — 제자리 re-exec (권장)

새 명령 `restart-server`(가칭, `server-respawn` 도 후보)가 kill+spawn 대신 아래 ⓐ~ⓔ 를
수행한다. **`os.execv` 는 PID 를 유지**하므로 자식 셸들이 계속 서버의 자식으로 남아
`waitpid`/SIGCHLD 가 유효하고, 상속된 master fd 가 PTY 를 살린다.

### ⓐ 넘길 master fd 의 CLOEXEC 해제
각 master fd 는 생성 직후 `FD_CLOEXEC` 가 걸려 있다
(`pytmuxlib/pty_backend.py:132-138` — 새 패널 fork 시 형제 master fd 누수 방지가 목적).
**이게 바로 execv 때 fd 를 닫아 셸을 죽이는 원인**이다. 따라서 execv **직전에**, 넘길
패널 master fd 에 한해서만 `FD_CLOEXEC` 를 푼다(`fcntl.F_SETFD` 로 플래그 클리어).

```
for pane in all_panes:
    flags = fcntl.fcntl(pane.master_fd, fcntl.F_GETFD)
    fcntl.fcntl(pane.master_fd, fcntl.F_SETFD, flags & ~fcntl.FD_CLOEXEC)
```

### ⓑ 상태 직렬화(기존 layout.json 확장)
`save_layout` 의 트리+제목만으로는 부족하다. 패널별로 다음을 상태 파일
(`layout_path`, `server.py:949-951`)에 함께 저장한다:

- **트리 구조**: 기존 `_serialize_node`(split/orient/ratio).
- **패널 메타**: `child_pid`, **`master_fd` 의 정수 번호**, `title`, `cwd`, `size`(cols×rows).
- **Claude 상태**: `_claude`, `_claude_usage`, `autoresume`, `resume_msg`, `last_prompt`,
  `_scanbuf`, `_resume_pending`(`model.py` Pane 필드).
- **마우스 모드** 등 패널별 토글.
- **pyte 화면/스크롤백**: `screen.display` + history(§3 "주의" 참조). 직렬화 안 하면 소실.

> fd **번호**를 적는 이유: execv 후 새 이미지는 같은 PID 라 **fd 가 그대로 상속**된다.
> 번호로 다시 Pane 에 감싸면 PTY 가 연결된 채 살아난다.

### ⓒ 제자리 재실행
`os.execv(sys.executable, [sys.executable, "-m", "pytmuxlib", "--resume", state_path, ...])`
로 서버 프로세스 이미지를 갈아끼운다. PID·자식 셸·상속 fd 는 유지된다.

### ⓓ 새 이미지에서 복원
새 서버가 `--resume` 으로 상태 파일을 읽어:
1. 각 패널 메타의 **fd 번호를 Pane 으로 다시 감싸고** asyncio `add_reader` 재등록.
2. Session/Tab/Window 트리 복원(`_build_node` 를 spawn 없이 "기존 fd 채택" 경로로 분기).
3. **CLOEXEC 재채택**: 채택 직후 각 master fd 에 다시 `FD_CLOEXEC` 를 걸어 §6 불변식
   복구(이후 새 패널 fork 시 형제 fd 누수 방지).
4. 메인 화면 평문 스냅샷 복원(import_state) + **`_induce_redraw_all` 로 SIGWINCH
   유발** → alt-screen TUI repaint(아래 주의 ① 대안 B, 구현됨).

### ⓔ 클라이언트 재접속
서버가 쥔 리슨 유닉스 소켓·연결 클라이언트 소켓은 옛 이벤트 루프의 fd 라 execv 후
무효다. `serve` 가 시작 시 소켓을 unlink+재생성하므로(`server.py:2181`, `2187`)
**리슨 소켓을 다시 만들고**, 클라이언트는 같은 소켓 경로로 재접속(detach→reattach)한다.

### 주의(불변식)
1. **`os.execv` 는 메모리를 갈아끼운다** → 직렬화 안 한 pyte 화면/스크롤백은 소실.
   - 대안 A: 패널별 `screen.display`+history 를 상태 파일에 저장해 복원.
   - 대안 B: 소실 감수 + **재그리기 유도** — SIGWINCH/리사이즈 한 번이면 claude/vim 등
     alt-screen TUI 는 다시 그린다. 순수 셸은 스크롤백만 잃는다.
2. **리슨/클라이언트 소켓**은 execv 후 재생성·재접속(ⓔ).
3. **CLOEXEC 불변식(HANDOFF §6)**: 평상시엔 절대 풀지 말고, **넘길 master fd 에 한해
   execv 직전에만 해제**(ⓐ), 새 이미지에서 채택 직후 다시 건다(ⓓ-3).

## 4. 방식 ② — SCM_RIGHTS fd 핸드오프 (차선)

옛 서버가 새 서버를 띄우고 유닉스 소켓 `sendmsg` 의 ancillary data(`SCM_RIGHTS`)로
**master fd + 메타데이터를 전달**한 뒤, 자식을 죽이지 않고 종료. 새 서버가 fd 채택.

**단점**: 옛 서버가 종료되면 자식 셸이 launchd/init 으로 **reparent** 돼
`waitpid`/SIGCHLD 로 죽음을 못 잡는다 → `os.read` 의 **EOF/EIO**
(macOS 경로, `pty_backend.py:164`)로만 패널 종료를 감지해야 한다(PID 는 살아 있어
`killpg` 신호는 가능). 구현이 ①보다 까다로워 차선.

## 5. 무엇이 보존/소실되나

| 항목 | 방식 ① re-exec | 비고 |
|---|---|---|
| 살아 있는 셸·프로그램(claude/vim/빌드) | ✅ 보존 | PID·자식관계 유지 |
| PTY(master/slave) | ✅ 보존 | 상속 fd 로 연결 유지 |
| 탭/패널 트리·제목·크기 | ✅ 보존 | 상태 파일 직렬화 |
| Claude 상태(autoresume/usage/last_prompt 등) | ✅ 보존 | Pane 필드 직렬화 |
| in-memory pyte 화면/스크롤백 | ⚠️ 직렬화 시 보존, 아니면 소실 | 주의 ① |
| 클라이언트 연결 | 🔁 재접속 필요 | detach→reattach |
| `waitpid`/SIGCHLD 유효성 | ✅ 유지 | PID 불변(①의 핵심 이점) |

## 6. 사용자 시나리오 (예상 흐름)

1. 사용자가 `server.py` 등 서버 코드를 수정.
2. 명령 팔레트/단축키에서 **`restart-server`** 실행(또는 클라이언트가 코드 변경 감지 후 제안).
3. 화면이 잠깐 깜빡이고(클라이언트 재접속), **모든 탭·패널이 그대로** 돌아온다 —
   claude 세션은 대화 맥락 유지, vim 은 열려 있던 버퍼 유지, 빌드는 계속 진행 중.
4. 스크롤백을 직렬화하지 않은 모드라면 순수 셸 패널만 이전 출력이 비어 있을 수 있다(주의 ①).

## 7. 테스트 시나리오 (회귀 기준)

`tests/run.py` 에 추가할 케이스(헤드리스). 셸 프로세스 보존은 PID 동일성으로 검증한다.

1. **`test_restart_preserves_pids`**: 패널 2개 spawn → 각 `child_pid` 기록 →
   `restart-server` → 복원 후 각 패널 `child_pid` 가 **동일**한지 확인.
2. **`test_restart_preserves_tree`**: split 한 트리(orient/ratio/title) → 재시작 →
   트리 구조·제목·크기 일치.
3. **`test_restart_preserves_claude_state`**: `autoresume`/`last_prompt`/`_claude_usage`
   를 세팅 → 재시작 → 값 보존.
4. **`test_restart_cloexec_reasserted`**: 재시작 후 새 패널을 fork → 형제 master fd 가
   **상속되지 않음**(CLOEXEC 재채택, §6 불변식)을 fd 검사로 확인.
5. **`test_restart_socket_recreated`**: 재시작 후 리슨 소켓이 같은 경로로 재생성되고
   클라이언트 재접속이 성공.
6. **`test_restart_pty_alive`**: 재시작 전 패널 셸에 `echo MARKER` 입력 큐 → 재시작 후
   같은 fd 로 출력이 도착(PTY 연결 유지).

> 불변식 유지(HANDOFF §3): `Session.active_window` 프로퍼티, CLOEXEC(§6),
> feed 경계 캐리(§6), 단일 세션 모델은 깨지 말 것. 각 단계마다 `python3 tests/run.py` 통과.

## 8. 구현 순서(제안)

1. `save_layout`/`_serialize_node` 를 **확장**(child_pid·fd 번호·cwd·size·Claude 필드)
   — 기존 슬롯 저장과 별개 "resume 상태 파일" 경로로 분리.
2. `--resume` 부트 경로: fd 채택 분기(`_build_node` 의 spawn 대체) + CLOEXEC 재채택.
3. `restart-server` 명령(ⓐ CLOEXEC 해제 → ⓑ 직렬화 → ⓒ execv) 추가.
4. 클라이언트 재접속(ⓔ) + (선택) 코드변경 감지 후 재시작 제안 UX.
5. ✅ 재그리기 유도(대안 B) 채택: 복원 후 `_induce_redraw_all`(winsize 한 칸
   토글로 SIGWINCH) + 메인 화면 평문 스냅샷. pyte 내부 상태 완전 직렬화(대안 A)는
   비채택(취약·과설계). 실 박스 검증 완료.
6. 테스트 §7 추가.

**데몬 재시작 주의(HANDOFF §2)**: 이 기능 자체가 서버 로직이라, 구현 중에는 여전히
`kill-server` 재기동으로 코드를 반영해야 한다(부트스트랩되기 전까지).
