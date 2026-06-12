# pytmux 보안 검토 (서버–클라이언트 공격 표면)

> 📦 **플러그인 이전 메모(CL 57812, 검토 이후)**: 본문의 `serverclaude` 모듈 참조는 현재
> `pytmuxlib/plugins/claude-code/servermixin.py`(`ServerClaudeMixin`)다 — 위치만 이전, 보안
> 결론 불변. 시점 스냅샷이라 본문은 보존. 참고: [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md) §4.

> **작성**: 2026-06-07 · **대상 커밋**: `main` (와이드문자 복원 수정 이후) ·
> **방법**: 정적 코드 검토(소스 정독 + 데이터 흐름 추적). 동적 익스플로잇 미수행.
> **범위**: 클라이언트↔서버 IPC, 직렬화/프로토콜, PTY·셸 스폰, 영속화/재시작,
> 캡처(REC)/replay, 클립보드, Claude 연동. 사용자가 요청한 위협(가짜 서버, 조작된
> 클라이언트, 의도치 않은 코드 실행, 중간자 공격, 실행 결과 가로채기) 중심.

---

## 0. 요약 (TL;DR)

pytmux 의 보안 경계는 **전적으로 전송 계층(소켓 파일 권한 / 루프백 도달성)에 의존**한다.
와이어 프로토콜·직렬화·subprocess 구성 자체는 견고하다(JSON·길이상한·argv 기반, `pickle`/
`shell=True`/`os.system` 없음). **애플리케이션 레벨 인증·피어 검증은 전무**하다.

핵심 결론:

- **인가된 클라이언트 = 사용자 본인**이다. `display-popup`/`pipe-pane`/`run-shell` 의 셸
  실행은 tmux 처럼 **의도된 기능**이며, 그 자체는 취약점이 아니다. 위험은 "누가 클라이언트가
  될 수 있는가"(=전송 계층 접근 통제)에 달려 있다.
- **Unix**: AF_UNIX 소켓이 `0700` 디렉터리 + `0600` 소켓으로 보호된다. 같은 UID 만 접근
  가능 — 합리적. 단 `XDG_RUNTIME_DIR` 없는 ssh 로그인의 `/tmp` 폴백에 **디렉터리 선점**
  여지가 있다(F3).
- **Windows**: `127.0.0.1` **TCP 루프백 + 무인증**. **같은 머신의 모든 로컬 사용자/
  프로세스가 서버에 붙어** 입력 주입·셸 실행(popup/pipe)·서버 종료·화면 열람을 할 수
  있다(F1, 최고 심각도).
- 클라이언트는 서버 데이터를 **표시 전용**으로 다룬다. 서버 메시지가 `_run_command`/셸로
  흐르는 경로가 **없어** 가짜 서버→클라 **코드 실행은 불가**. 가짜 서버의 실제 영향은
  **키입력 가로채기(MITM)** 와 화면/상태 **위조(피싱)** 로 한정된다.
- 캡처(REC)가 **기본 ON** 이라 raw PTY 출력(표시·에코된 비밀번호·토큰 포함)이 디스크에
  무손실 기록된다 — 로컬 권한·depot 공유 측면의 정보 노출(F4).

| ID | 심각도 | 항목 | 전제 |
|----|--------|------|------|
| F1 | **High**(Windows) ✅적용 | TCP 루프백 무인증 → 무인가 로컬 접근으로 입력주입·RCE·종료 | Windows |
| F2 | Medium ✅적용 | 애플리케이션 인증/피어 UID 검증 부재(전 플랫폼) | — |
| F3 | Medium ✅적용 | `/tmp` 폴백 상태 디렉터리 선점 → 가짜 서버/MITM | Unix, XDG 미설정(ssh) |
| F4 | Medium ✅적용(권한) | 캡처(REC) 기본 ON·raw 민감출력·world-readable·depot 공유 | 로컬/팀 |
| F5 | Low–Med ✅적용 | 영속 파일(resume/slots/opts) 권한 미설정(심층방어 부족) | — |
| F6 | Low ✅적용 | 메시지 입력 검증 빈약 → 자원 고갈(DoS) | 무인가 접근 시 |
| F7 | Info | popup/pipe/run-shell/claude-rules = 의도된 셸 실행(인가 권한 내) | — |
| F8 | Info | 가짜 서버의 화면/상태 위조(피싱)·키입력 가로채기 | 엔드포인트 통제 시 |

---

## 1. 아키텍처와 신뢰 경계

```
 사용자 ─ 키입력 ─▶ [클라이언트(Textual 앱)] ─JSON/소켓─▶ [서버] ─▶ [PTY 자식 셸/프로그램]
                         ▲   화면=셀그리드          │
                         └──── screen/status ───────┘
```

- 전송: `pytmuxlib/ipc.py`. Unix=AF_UNIX(파일 경로), Windows=TCP 루프백(에페메럴 포트 +
  포트파일). 프레이밍: `protocol.py` 길이프리픽스(4B)+JSON, `MAX_FRAME=64MiB`.
- 서버 디스패치: `serverio.py:handle_client` — 첫 메시지로 `list`/`kill-server`/`control`/
  `hello` 분기, 이후 `input`/`resize`/`scroll`/`cmd`(70여 액션).
- **신뢰 경계는 소켓 하나뿐**이다. 소켓 너머의 클라이언트는 전부 신뢰된다(= 사용자).
  따라서 "조작된 클라이언트"는 위협이 아니라 **무인가 주체가 클라이언트가 되는 것**이
  위협이다.

**중요한 재구성**: "클라가 popup 으로 임의 셸 명령을 실행시킬 수 있다"는 사실 자체는
취약점이 아니다(사용자는 이미 셸을 가진다). 진짜 질문은 *그 클라이언트 소켓에 누가
붙을 수 있는가* 이고, 그 답은 F1–F3 가 결정한다.

---

## 2. 위협 모델 (공격자 유형)

1. **같은 머신의 다른 로컬 사용자** — 멀티유저 호스트/공용 서버. 목표: 피해자 세션 탈취,
   입력 주입, 화면 열람, 셸 실행.
2. **같은 사용자의 악성 로컬 프로세스** — 멀웨어/공급망. 같은 UID 라 어차피 사용자 권한을
   가지므로 IPC 통제로 막을 수 없음(범위 밖이나 F4 캡처 노출은 가중).
3. **가짜 서버 / MITM** — 피해자 클라이언트가 공격자가 심은 엔드포인트에 attach 하게 유도
   (F3). 목표: 키입력(비밀번호) 가로채기, 화면 위조.
4. **패널 안 신뢰불가 프로그램 출력** — `curl`, 악성 로그, 원격 셸 출력 등이 ANSI/OSC 를
   뱉음. 목표: 호스트 터미널 조작/클립보드 주입.
5. **원격(ssh)** — pytmux 는 네트워크 서비스가 아니다. 원격 노출은 사용자가 직접 소켓을
   포워딩할 때만(범위 밖). ssh 중첩은 거부됨(`sshwrap`).

---

## 3. 상세 발견

### F1 — [High, Windows] TCP 루프백 무인증

- **위치**: `ipc.py:80-82`(Windows 기본 `tcp:127.0.0.1:0`), `ipc.py:176`(`asyncio.
  start_server`), `serverio.py:643-726`(인증 없는 디스패치).
- **내용**: Windows 에서 서버는 `127.0.0.1` 에페메럴 포트로 listen 한다. 루프백 TCP 는
  **같은 머신의 모든 사용자·프로세스가 접속 가능**하다(Unix 소켓 파일권한 같은 per-user
  통제가 없다). 포트는 `%LOCALAPPDATA%\pytmux\default.port` 에 있고, 없어도 에페메럴
  포트 범위는 스캔 가능하다.
- **영향**: 무인가 로컬 주체가 `hello` 로 세션에 붙어 **임의 키입력 주입**(`input`),
  **셸 명령 실행**(`cmd popup_open`/`pipe_pane` → F7), **서버 종료**(`kill-server`),
  **전 패널 화면 열람**(`screen`)을 할 수 있다. 사실상 로컬 권한 상승/세션 탈취.
- **권고**: ① 서버가 기동 시 랜덤 토큰을 `0600` 상태 파일(또는 포트파일)에 적고, 클라
  `hello`/`control` 에 토큰을 실어 서버가 검증(없거나 불일치면 즉시 종료). ② 가능하면
  Windows AF_UNIX(최신 빌드 지원) 또는 Named Pipe(per-user ACL)로 전환. 토큰은 최소한의
  심층 방어로 즉시 도입 권장.
- **적용됨(①)**: `ipc.token_path/write_token/read_token` 으로 서버가 listen **전에**
  `secrets.token_hex(32)` 토큰을 `0600` 파일에 게시(`serverio.serve`), 클라/launcher 가
  읽어 `hello`/`control` 첫 메시지에 실어 보내고, `handle_client` 가 `hmac.compare_digest`
  로 검증해 불일치/누락이면 `auth_failed` 로 거절한다. 토큰을 읽을 수 있는 건 같은 UID
  뿐이라 Windows TCP 루프백의 무인가 접속이 차단된다. 회귀: `test_server.py::
  test_auth_token_required_and_published` · `test_control_requires_auth_token`.

### F2 — [Medium] 애플리케이션 인증/피어 검증 부재

- **위치**: `serverio.py:674-678`(`control` 즉시 실행), `:679-695`(`hello` 즉시 attach).
  `SO_PEERCRED`/`getpeereid` 사용처 **전무**(grep 확인).
- **내용**: 서버는 연결 피어의 신원/UID 를 일절 확인하지 않는다. `control` 채널은 인증 없이
  `send-keys`·`new-window`·`kill-session`·`kill-server` 등을 실행한다. 접근 통제가 **전적으로
  전송 계층의 도달성에 위임**돼 있다.
- **영향(플랫폼별)**: Unix 는 `0700` 디렉터리 덕에 같은 UID 외엔 도달 불가라 실질 위험이
  낮다(심층 방어 부족일 뿐). Windows 는 F1 과 결합해 즉시 악용된다.
- **권고**: Unix 에서 `SO_PEERCRED`(Linux)/`getpeereid`(BSD/mac)로 **피어 UID == 서버
  UID** 를 검증해 파일권한 위에 한 겹 더 둔다. control 채널도 동일 검증.
- **적용됨**: `ipc.peer_uid()`(Linux=SO_PEERCRED, macOS/BSD=LOCAL_PEERCRED)로 Unix 소켓
  상대 UID 를 읽어 `handle_client` 가 서버 UID 와 다르면 거절(검증 불가면 통과 — 토큰
  F1 이 1차 방어). control 포함 모든 첫 메시지에 토큰 검증(F1)이 걸려 무인증 실행도
  차단됨. 회귀: `test_server.py::test_peer_uid_over_unix_socket`.

### F3 — [Medium] `/tmp` 폴백 상태 디렉터리 선점 (가짜 서버/MITM)

- **위치**: `ipc.py:68`(`/tmp/pytmux-<uid>` 폴백), `:69-74`(`makedirs(exist_ok=True)`
  후 `chmod 0700` 을 **best-effort** 로만 — 실패 시 `pass`), `:100`(attach 후보).
- **내용**: `XDG_RUNTIME_DIR` 가 없으면(예: 순수 ssh 로그인 — 메모리에도 기록된 흔한 상황)
  상태 디렉터리가 `/tmp/pytmux-<uid>` 가 된다. 공격자가 이 경로를 **먼저 자기 소유로
  생성**해 두면, 피해자의 `makedirs(exist_ok=True)` 는 성공하고 `chmod 0700` 은 소유자가
  아니라 실패(무시)한다. 이후 피해자는 **공격자 소유 디렉터리 안에** 소켓/포트파일을 만들거나
  거기 있는 가짜 소켓에 attach 한다.
- **영향**: 공격자가 가짜 서버를 세워 피해자 클라의 **모든 키입력(비밀번호 포함)을 가로채고**
  화면을 위조(F8)할 수 있다. 중간자 공격 성립.
- **권고**: 상태 디렉터리 사용 전에 `os.lstat` 로 **소유자(`st_uid==os.getuid()`)·권한
  (`0700`)·심볼릭링크 아님**을 검증하고, 어긋나면 거부(새 무작위 디렉터리/실패 처리).
  `XDG_RUNTIME_DIR`(systemd 가 `0700` 보장) 우선 사용.
- **적용됨**: `ipc._validate_state_dir()` 가 `default_state_dir()` 에서 makedirs 직후
  `os.lstat` 로 **심볼릭 링크가 아니고 현재 UID 소유**인지 확인해 어긋나면 `RuntimeError`
  로 거부(fail-closed)하고, 그 다음에 0700 으로 좁힌다. lstat 은 링크를 안 따라가므로
  공격자가 선점한 심링크·디렉터리 모두 소유자 불일치로 잡힌다. 회귀:
  `test_server.py::test_validate_state_dir_rejects_symlink`.

### F4 — [Medium] 캡처(REC) 기본 ON · raw 민감 출력 · world-readable

- **위치**: `server.py:72`(`self.capture = ... "capture", True` — **기본 ON**),
  `servercapture.py:40`(기본 경로 `PROJECT_DIR/captures/`), `:69`(`open(path,"ab")` —
  **권한 설정 없음**, umask 의존 → 보통 `0644`).
- **내용**: REC 가 켜져 있으면 패널 PTY 의 **raw 바이트를 무손실 기록**한다. 화면에 표시되거나
  에코된 **비밀번호·API 키·토큰·개인정보**가 그대로 파일에 남는다. 기본 저장 위치가 프로젝트
  디렉터리(보통 `0755`)라 **같은 머신의 다른 로컬 사용자가 읽을 수 있다**. 또한 이 디렉터리는
  **Perforce depot 으로 공유**된다(의도된 정책) — depot 읽기 권한자 전원에게 raw 캡처가
  노출된다.
- **완화 확인됨**: `captures/` 는 `.gitignore`(line 4)에 있고 `origin/main` 에 0건 —
  **GitHub 미러로는 새지 않는다**(정상). 위험은 **로컬 디스크 권한**과 **depot 공유**에 한정.
- **권고**: ① 캡처 파일/디렉터리를 `0600`/`0700` 으로 명시 생성. ② REC 기본값을 **OFF**
  로 바꾸거나, 최소한 민감 패턴 마스킹·짧은 보존·명시적 동의 흐름 도입. ③ depot 공유가
  필요하면 raw 대신 마스킹본을 올리는 정책을 문서화. (관련 메모리: REC 기본 ON, 실 Claude
  데이터 수집.)
- **적용됨(①)**: `_capture_write` 가 캡처 디렉터리를 `0700`, `pane-*.log`/`sessions.log`
  를 `ipc.open_private`(생성 시점부터 `0600`)로 만든다 — 로컬 다른 사용자의 raw 캡처
  열람을 차단(F4 의 핵심 노출 경로). 회귀: `test_server.py::test_private_files_are_0600`.
- **②는 의도적 미적용**: REC 기본 ON 은 실 Claude 데이터 수집을 위한 **의도된 설계**라
  (개발 워크플로 의존) 기본값을 바꾸지 않는다. 로컬 노출은 ①(0600/0700)로 해소되며,
  GitHub 유출은 `.gitignore` 로 이미 차단됨(검증). depot 공유 시 raw 노출은 팀 내부로
  한정되는 알려진 트레이드오프다.

### F5 — [Low–Medium] 영속 파일 권한 미설정 (심층 방어 부족)

- **위치**: `serverpersist.py:122`(`resume.json`), `:318`(`slots.json`), `:338`
  (`opts.json`), `:46`(`layout.json`) — 전부 `open(path,"w")` 로 **권한 설정 없이** 기록.
- **내용**: `resume.json` 은 **화면 스냅샷(표시된 내용)·프롬프트(last_prompt/대기큐)** 등 민감정보를
  담는다. 파일 자체는 umask(`0644`)로 생성된다. 다만 이들은 `state_base`(= `0700` 상태
  디렉터리) 안에 있어 **정상 경로에선 디렉터리 권한으로 보호**된다. 그러나 파일 단위
  `0600` 이 아니라 심층 방어가 부족하고, F3 의 디렉터리 선점이 성립하면 그대로 노출된다.
- **권고**: 민감 영속 파일은 `os.open(..., 0o600)` 또는 기록 후 `os.chmod(path, 0o600)`.
- **적용됨**: `ipc.open_private()`(O_CREAT 시점부터 `0600`)로 `resume.json`/`slots.json`/
  `opts.json`/`layout.json` 저장. umask 로 잠깐 넓게 열리는 창이 없다. 회귀:
  `test_server.py::test_private_files_are_0600`.

### F6 — [Low] 메시지 입력 검증 빈약 → 자원 고갈

- **위치**: `serverio.py:684-685,717-718`(cols/rows `max()` 하한만, **상한 없음**),
  `:766`(`base64.b64decode` 가 핸들러 본체에 있어 잘못된 base64 가 예외 — 다만
  `:727-729` dispatch try/except 로 잡혀 **세션은 안 끊김**).
- **내용**: 무인가 접근(F1)이 성립하면, 거대 cols/rows 로 레이아웃 계산 메모리를 부풀리거나
  잘못된 필드로 예외를 반복 유발해 로그를 채울 수 있다. 단 프로토콜의 `MAX_FRAME` 과
  per-message try/except 덕에 **원격 크래시/OOM 으로 서버를 죽이긴 어렵다**.
- **권고**: cols/rows 상한(예: 1000×1000), base64 디코드 `try/except`, 정수/실수 필드 타입
  가드. (저위험 — F1 선결이 우선.)
- **적용됨**: `protocol.MAX_W/MAX_H=2000` 상한 + `protocol.clamp_dim()`(정수 변환 실패 시
  default)으로 hello·resize 의 cols/rows 를 `[MIN, MAX]` 로 자른다. `_handle_input` 의
  base64 디코드를 `binascii.Error`/`ValueError` 가드로 감싸 손상 입력만 무시한다(팝업·
  일반 경로 공용). 회귀: `test_clamp_dim_bounds`, `test_bad_base64_input_is_ignored`.

### F7 — [Info] popup/pipe/run-shell/claude-rules = 의도된 셸 실행

- **위치**: `serverpty.py:43`(`[shell,"-c",cmd]`), `server.py:528`(`pipe_pane` →
  `proc.shell_argv`), `client.py:2043,2056`(`run-shell`/`if-shell`),
  `serverclaude`(claude auto-mode rules).
- **내용**: 이들은 클라가 지정한 명령을 셸로 실행한다. **인가된 사용자 권한 내의 의도된
  기능**(tmux `display-popup`/`pipe-pane`/`run-shell` 동급)이며 그 자체는 취약점이 아니다.
  단 **F1/F2 로 무인가 접근이 열리면 이들이 곧 RCE 수단**이 된다. `shell=True` 가 아니라
  `[sh,-c,cmd]` argv 라 *추가적* 메타문자 주입은 없지만 cmd 전체가 셸로 가는 건 설계상 당연.
- **권고**: 별도 조치 불필요(설계 의도). F1/F2 의 접근 통제로 보호하는 것이 정답.

### F8 — [Info] 가짜 서버의 화면/상태 위조 · 키입력 가로채기

- **위치**: `client.py:524-629`(서버 메시지 핸들러 — 전부 표시/상태 갱신).
- **내용**: 클라는 서버의 `screen`/`status`/`layout` 을 검증 없이 표시한다. 가짜 서버는
  **토큰 사용량·프롬프트·패널 타이틀·세션명을 위조**해 사용자를 기만(피싱)하거나, attach 된
  클라의 **모든 키입력을 수집**할 수 있다. **단 코드 실행은 불가** — 서버 메시지가
  `_run_command`/셸로 흐르는 경로가 없음을 확인했다(아래 §4). 가짜 서버 성립 전제는 F3.
- **권고**: F3 차단이 근본 대응. (선택) 표시 데이터 길이·타입 가드로 DoS 표면 축소.

---

## 4. 검증 결과 "안전"으로 판단한 항목 (오탐 방지 기록)

정밀 검토 중 **취약하지 않음**을 코드로 확인한 항목들. (초안 자동분석에서 위험으로 거론됐으나
근거 검토 후 기각/완화한 것 포함.)

- **직렬화 RCE 없음**: 와이어는 **JSON 전용**(`protocol.py:59` `json.loads`). `pickle`/
  `eval`/`exec` 없음. `MAX_FRAME=64MiB`(`:33,51`)로 길이폭탄 OOM 차단.
- **subprocess 전부 argv 리스트**: `shell=True`·`os.system` **전무**(전수 grep). 클립보드
  (`clientclip.py`), git/version, usageprobe 모두 argv. PowerShell 이미지 저장 경로는
  작은따옴표 이스케이프 + `mkstemp` 생성 경로라 주입 불가.
- **가짜 서버 → 클라 코드 실행 없음**: 클라 명령 해석기(`_run_command`/`_run_shell`)는
  **로컬 설정파일**(`keymap.load_config` → `bindings`/`hooks`)과 **사용자 명령 프롬프트
  입력**(`_prompt_done` purpose=="command")으로만 구동된다. **어떤 서버 메시지도 이 경로로
  흐르지 않는다**(`client.py:524-629` 확인).
- **ANSI/OSC 호스트 통과 없음**: 패널 프로그램 출력은 **서버의 pyte 가 셀 그리드로 파싱**
  (`serverpty.py:178 pane.feed`)하고, 클라는 **셀(문자+스타일)만** 받아 Textual 로 재렌더한다.
  raw 이스케이프가 호스트 터미널로 새는 passthrough 구간이 없다. **OSC 52(클립보드) 자체를
  pytmux 가 처리하지 않으므로** "OSC52 클립보드 주입" 은 성립하지 않는다(초안 우려 기각).
- **bracketed paste 모드 위조 불가**: `pane.bracketed` 는 패널 앱의 DECSET 2004 를 서버가
  추적하는 **내부 상태**(`serverpty.py:183-186`)다. 클라/가짜서버가 직접 못 바꾼다. (붙여넣기
  자체의 개행 주입은 bracketed 미지원 셸의 일반적 한계이지 pytmux 결함은 아님.)
- **replay/캡처 경로**: `replay` 경로는 **사용자 CLI 인자**(자기 파일을 자기가 읽음 — 취약
  아님). `PYTMUX_CAPTURE_DIR` 도 사용자 자신의 env(자기 파일에 자기가 씀). 신뢰불가 입력원
  아님.
- **ssh 중첩 거부**: `sshwrap.panel_env` + `$PYTMUX` 로 로컬/원격 중첩 기동을 차단.
- **재시작 PTY 채택**: `serverpersist` 의 `master_fd`/`child_pid` 채택은 `resume.json` 에서
  읽지만, 이 파일은 `0700` 상태 디렉터리 안이라 같은 UID 만 조작 가능(F3/F5 가 선결 조건).

---

## 5. 권고 우선순위 (적용 현황)

> **현황(2026-06-07)**: F1–F6 의 권고를 모두 적용 완료(F4 ②REC 기본 OFF 는 의도적
> 미적용 — §F4 참조). 각 발견의 "**적용됨**" 항목과 회귀 테스트 참조. 전체 308 passed.

1. **(High) F1 ✅** — Windows TCP 무인증 해소: hello/control 에 **공유 비밀 토큰** 검증 추가
   (서버가 `0600` 파일에 게시). 가장 시급.
2. **(Medium) F2 ✅** — Unix `SO_PEERCRED`/`getpeereid` 피어 UID 검증(심층 방어).
3. **(Medium) F3 ✅** — 상태 디렉터리 소유권·권한·심링크 검증 후 사용, `XDG_RUNTIME_DIR` 우선.
4. **(Medium) F4 ✅(권한)** — 캡처 파일/디렉터리 `0600`/`0700`. REC 기본값은 설계상 ON 유지.
5. **(Low) F5/F6 ✅** — 민감 영속 파일 `0600`, cols/rows 상한·base64 가드.

각 항목은 독립적이며, F1·F3 가 "무인가 주체가 클라이언트가 되는" 근본 통로이므로 가장
효과가 크다. F7 류 셸 실행 기능은 **그대로 두고**, 접근 통제(F1–F3)로 보호하는 것이 설계에
부합한다.

---

*관련 문서: [DESIGN.md](DESIGN.md) · [WINDOWS_PORT.md](WINDOWS_PORT.md)(전송 계층) ·
[RESTART_SCENARIO.md](RESTART_SCENARIO.md)(영속화/재시작) · [CONTRIBUTING.md](CONTRIBUTING.md).*
