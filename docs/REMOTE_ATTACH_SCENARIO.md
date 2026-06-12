# 원격 pytmux 어태치 페더레이션 (IMPROVEMENT §1.7)

> 요청(2026-06-12): "pytmux 안에서 원격 ssh 서버에 접속했을 때, 그 서버에 pytmux 탭이
> 열려 있으면 원격 pytmux 가 재접속을 반복하며 정상 동작하지 않는다. pytmux 안에
> pytmux 가 다시 열리는 동작을 원하지 않으므로, 원격에서 pytmux 를 실행하면 이중으로
> 실행되지 않고 **원격 서버의 탭·패널을 현재 실행 중인 pytmux 에 어태치**해 원격 탭
> 모양으로 나타나야 한다."

## 1. 원인 격리 — "재접속 반복"은 어디서 오나

기존 중첩 방지는 **env 마커 전파**에 100% 의존한다:
- 로컬: 패널 셸에 서버가 `$PYTMUX` 를 심음 → `launcher.nesting_blocked()` 거부.
- 원격: 패널 셸의 ssh 래퍼(sshwrap, PATH 앞단)가 `ssh -o SendEnv=LC_PYTMUX` 로 표식을
  전파 → 원격 pytmux 가 `$LC_PYTMUX` 를 보고 거부.

**전파가 깨지는 경로**(이때 중첩 TUI 가 실제로 떠 버린다):
1. ssh 래퍼 우회 — `/usr/bin/ssh` 절대경로, 셸 alias, PATH 캐시, 래퍼 설치 전의 셸.
2. 원격 sshd 의 `AcceptEnv` 에 `LC_*` 부재(표식 이름이 LC_ 인 이유는 배포판 기본
   `AcceptEnv LANG LC_*` 에 올라타기 위함이지만, 강화된 서버는 이를 지운다).
3. SendEnv 를 안 끼우는 클라이언트/점프 호스트 경유.

중첩 TUI(textual-in-pyte)가 뜨면 두 메커니즘이 "재접속 반복"으로 나타난다:
- **crash-relaunch**: 클라 크래시 시 자가 재기동(`run_client`, 30초 창에서 최대 5회) —
  중첩 환경 크래시가 반복되면 5회까지 재attach 가 깜빡인다.
- **net 워치독 자동 재접속**: `net_auto_reconnect`(기본 ON)의 `_force_reconnect` 는
  의도적으로 **무상한**(불안정 네트워크용) — 중첩 환경에서 degraded 판정이 반복되면
  지속적인 재접속 루프가 된다.
- 추가 악화: 원격 zshrc 가드가 `exec pytmux` 면 거부 종료가 로그인 셸을 죽여 autossh
  류 자동 재접속과 **ssh 레벨 루프**를 만든다(README 에 경고 추가).

## 2. Stage 0 — 즉시 완화 (구현 완료, 2026-06-12)

env 전파와 무관한 **in-band 능동 감지**로 "중첩 TUI 가 뜨는 것 자체"를 차단한다.

- **내부(원격) 측** `launcher.host_terminal_is_pytmux()`: attach 직전, 원격 로그인
  (`SSH_CONNECTION`/`SSH_TTY`)이고 env 마커가 없으면 단말에 **XTVERSION 질의**
  (`ESC[>0q`)를 쓰고 cbreak 로 응답을 기다린다(상한 0.4초).
  - 응답에 `pytmux` → 중첩 확정, 거부(이중 실행 차단).
  - 타 단말의 완결 DCS 응답(iTerm2/kitty/xterm 등은 XTVERSION 에 자기 이름으로 응답)
    → **조기 통과**(지연 = ssh RTT 수준).
  - 무응답 단말 → 0.4초 후 통과(원격 attach 한정 비용).
- **외부(로컬) 측** `serverpty._on_pane_data`: 패널 출력 스트림에서 질의를 스캔
  (read 경계 분할은 `pane._nestq_carry` 로 보전)해 `DCS >| pytmux ST`(`NEST_REPLY`)를
  그 패널 stdin 으로 응답한다. 실제 터미널과 동일 의미론(cat 된 파일 속 질의에도
  응답) — 부수효과로 패널 안 neovim 등 XTVERSION 사용 프로그램도 올바른 답을 받는다.
- **README**: zshrc 가드에 `$LC_PYTMUX` 검사 추가 + `exec pytmux` 금지 경고.

테스트: `test_server::test_pane_xtversion_query_gets_pytmux_reply`(응답·무관출력 무응답·
경계분할), `test_launcher::test_host_terminal_probe_inband_detection`(pytmux/타단말/무응답
3분기, pty 쌍).

한계: mosh 는 자체 터미널 에뮬레이션이 미지 DCS 를 떨어뜨려 질의가 통과하지 않을 수
있다 — mosh 경로는 env 마커(LC_* 전파)가 1차 방어로 남는다.

## 3. Stage 1 — 전송 프리미티브 `pytmux stdio-proxy` (구현 완료, 2026-06-12)

페더레이션의 전송로. **`ssh -T <host> pytmux stdio-proxy`** 로 원격에서 실행되면:
1. 원격 서버 인증 토큰을 `TOKEN <hex>\n` 한 줄로 알리고(F1 인증 — 로컬이 hello 에 실음),
2. 이후 stdin↔원격 서버소켓↔stdout 을 그대로 스플라이스한다.

ssh exec 채널(`-T`, TTY 없음)은 8-bit clean 파이프라 와이어 프로토콜의 길이-프레임이
무손상 통과한다 — tmux -CC 식 in-pane DCS 인코딩(TTY 변형·이스케이프 충돌 위험) 대신
**별도 ssh exec 채널**을 전송으로 쓴다. 서버 없으면 exit 1. 구현은 Stage 3 에서
**스레드 스플라이스로 재작성돼 POSIX·Windows 공통**(§5 전제 3 — 초기 v1 은 stdin
add_reader 의 POSIX 전용이었다).

테스트: `test_launcher::test_stdio_proxy_token_and_frame_roundtrip`(TOKEN 줄 + list
프레임 왕복, 실제 서브프로세스).

## 4. Stage 2 — 원격 탭 흡수 (구현 완료, 2026-06-12)

로컬 **서버**가 원격 서버의 클라이언트(업스트림)가 되어 원격 세션을 흡수한다(와이어
프로토콜 재사용 — **원격 서버는 변경 0**). 구현: `pytmuxlib/serverremote.py`
(`ServerRemoteMixin`/`RemoteLink`) + serverio 접점 5곳.

핵심 단순화(설계 대비): 원격 패널을 로컬 모델(pyte)로 미러링하지 않는다 —
**클라별 보기 플래그**(`ClientConn.remote_view`)로 통째 릴레이한다. id 재작성 0.

- **업스트림 연결**: `remote_attach(sess, host|endpoint)` — host 면 `ssh -T host
  pytmux stdio-proxy`(TOKEN 줄 파싱), endpoint 면 직결(같은 머신/테스트). 로컬 세션
  크기로 hello(proto+token) → 일반 클라처럼 layout/screen/delta/status 수신.
- **탭바 병합**: 업스트림 status 의 windows 를 `RemoteLink.windows` 로 흡수,
  `_status_msg` 가 로컬 탭 뒤에 `⇄host:이름` 엔트리를 전역 연속 index 로 병합 —
  탭바에 양쪽이 항상 보인다(클릭/alt+N = select_window 전역 index 그대로).
- **원격 탭 진입**: `select_window(전역 index ≥ 로컬 수)` → `client.remote_view =
  host` + 업스트림에 원격 index 로 릴레이 → 업스트림 _send_full 전체 화면이
  `_remote_reader` 를 거쳐 그 클라에 **그대로 전달**(layout/screen/delta + prompt_
  segment 등 비-status 전부). 로컬 flush/_send_full 은 보는 클라에 화면을 안 보냄
  (status 만 — 가드 1곳 `_send_full` 을 모든 방송 경로가 공유).
- **입력 라우팅**: 보는 중 input/scroll/resize 메시지 + cmd 화이트리스트(select_
  pane_id·zoom·next/prev_window·resize_dir·프롬프트 점프/펼치기 등)를 업스트림으로
  릴레이 — 원격 패널 분할선 드래그·헤더 팝업까지 동작. resize 는 로컬에도 반영(복귀
  대비).
- **복귀/해제/사망**: 로컬 index 선택 → 보기 해제+로컬 _send_full. `remote-detach
  [host]`(생략=전부). 링크 EOF(ssh 끊김/원격 종료) → 보던 클라 자동 로컬 복귀 + ⇄
  탭 제거 — **"재접속 루프" 대신 명시적 끊김 처리**.
- **진입 UX**: `remote-attach <host>` 명령 + Stage 0 거부 메시지에 힌트.

테스트(`tests/test_remote.py`, ssh 불요 — **in-process 서버 2대 실 소켓 직결** 전
구간 2홉): `test_remote_attach_merge_select_input_detach`(attach→⇄병합→진입→원격
화면 마커 수신→입력 B 패널 도달→로컬 복귀→해제), `test_remote_link_death_recovers_
viewer_to_local`(EOF→자동 복귀+탭 제거).

### Stage 3 (구현 완료, 2026-06-12)

- **per-client status — 원격 탭 active 하이라이트 + 업스트림 부가필드 전달**:
  업스트림 status 를 `link.last_status` 에 **누적**(`update` — full 이 채운 옵션
  키를 light 가 안 지움)하고, status 조립을 클라별로 바꿨다(`_status_msg(...,
  client=)`). 보는 클라는 업스트림 status 기반 머지본을 받는다 — active_pane/
  zoomed/pane_title/**Claude 헤더·토큰 등 부가필드가 원격 것 그대로**(클라는 원격
  패널 헤더를 로컬과 동일하게 그림), windows 만 병합 탭바(로컬=비활성, 원격=업스트림
  active 보존 → **⇄ 탭 하이라이트**)로 교체(`_remote_status_override` — session
  명·single_border 는 로컬 유지). 안 보는 클라는 종전 로컬 status(⇄ 비활성).
  전송 3지점(_send_full·flush status_changed·_remote_status_broadcast)이 클라별
  프레임을 만든다(클라 1~2 — 비용 미미). 한계: 보는 동안 로컬 탭의 플러그인 탭
  집계(claude 배지)는 기본 필드(bell/activity/claude_done)만 반영.
- **끊김 백오프 자동 재연결**: 링크가 비명시적으로 죽으면(EOF/오류) `_RECONNECT_
  DELAYS`(1,2,4,8,16,30×3초 — **유한**, §1 의 무상한 '재접속 루프'를 페더레이션에
  재현하지 않음) 백오프로 재시도, 성공/포기를 notice 로 알린다. 명시 `remote-
  detach`/재-attach 가 보류 재연결을 취소한다(사용자 의사 우선).
- **재시작(re-exec) 후 링크 복원**: `_resume_payload` 에 링크 spec(`remotes`)을
  실어 새 이미지가 부트 후 `remote_restore_links` 로 재연결(ssh 파이프는 CLOEXEC
  라 execv 를 살아남지 못하므로 항상 새로 연다). 복원 실패는 notice.
- **다중 원격 정리**: 링크별 탭이 전역 index 연속 병합·개별 detach. **자기 자신
  endpoint attach 거부**(자기 ⇄ 탭 재흡수로 탭 목록이 status 왕복마다 무한 증식하는
  루프 차단). 서버 shutdown 시 링크/ssh/보류 재연결 동기 정리(`remote_shutdown`).
- ~~Windows(stdio-proxy POSIX)~~ → **완료(2026-06-12)**: 스레드 스플라이스로
  재작성, §5 전제 3.

테스트(`tests/test_remote.py`, +6): active 하이라이트/부가필드 passthrough(2클라
대조), 백오프 재연결 re-merge(notice+⇄ 복귀), detach 의 보류 재연결 취소, resume
payload+restore_links 복원(서버 3대), 자기 attach 거부 notice, 다중 원격 병합/개별
detach.

### 실 ssh 라이브 검증 (완료, 2026-06-12 — §1.7 완결)
macOS(로컬 라이브 데몬 p4:58579) → **office1(Windows, 패스워드 호스트 →
ControlMaster 경유)** 실 ssh 로 전 구간 확인: ① `ssh -T -o BatchMode=yes office1
pytmux stdio-proxy` TOKEN 핸드셰이크 ② `remote-attach office1` notice 성공 ③
`⇄office1:cmd` 탭 병합 ④ 원격 탭 진입 → 업스트림 layout/screen(21행) ssh 릴레이
수신 ⑤ Stage 3 **⇄ 탭 active 하이라이트** 확인 ⑥ 로컬 복귀·detach 정리. 즉
ssh exec 채널·Windows stdio-proxy(스레드 스플라이스)·ControlMaster 레시피·Stage 3
per-client status 가 실전 조합으로 동작. 검증 프로브: 라이브 서버에 2000×2000
보조 클라로 attach(세션 min 크기 비훼손)해 와이어로 구동 — 실 클라 화면 무영향.

## 5. 사용

- 로컬 pytmux 에서 `:remote-attach <host>` → 탭바에 `⇄host:이름` 탭 등장 → 클릭/
  alt+N 으로 진입(원격 화면·입력 그대로), 로컬 탭 클릭으로 복귀, `:remote-detach`.
  host 는 **원시 문자열 그대로**(도메인 계정 `NATGAMES\\user@host` 의 백슬래시 보존 —
  첫 보고에서 shlex 가 삼켜 엉뚱한 host 로 가던 버그 수정). 결과는 상태줄
  **notice**(성공/실패+원인)로 표시된다.
- **전제조건**:
  1. **무인증 접속 수단** — 서버가 띄우는 ssh 는 TTY 가 없어 비밀번호를 못 묻는다
     (`BatchMode=yes`). 미설정이면 notice 에 `Permission denied` 가 그대로 보인다.
     둘 중 하나:
     - **키 인증**: `ssh-copy-id <host>`(또는 동등 설정).
     - **패스워드 전용 호스트 → ControlMaster(연결 공유)**: `~/.ssh/config` 에
       ```
       Host office1
         HostName office1
         User NATGAMES\woojinkim
         ControlMaster auto
         ControlPath ~/.ssh/cm-%C
         ControlPersist 10m
       ```
       후 **아무 패널에서 `ssh office1` 로 한 번 로그인**(비밀번호 입력) — 이후
       remote-attach 의 비대화 ssh 가 그 인증된 연결을 **재인증 없이** 다중화해
       탄다(mux 는 클라(macOS) 기능이라 Windows 서버와 무관, 2FA 도 동일하게
       해결). 로그인 셸을 닫아도 `ControlPersist` 동안 마스터가 살아 있다.
       부수효과: config 별칭 덕에 `:remote-attach office1` 로 짧게 쓴다.
  2. 원격 pytmux 가 **stdio-proxy 보유 버전**(POSIX 58551+/Windows 58565+)이고
     비대화 ssh 의 PATH 에서 `pytmux` 가 실행 가능해야 한다(없으면 `command not
     found`). Windows 는 `install.ps1` 이 까는 래퍼가 **사용자 PATH(레지스트리)**에
     있어야 sshd 가 띄우는 cmd.exe 에서도 보인다.
  3. **Windows 원격 지원**(Stage 3, 2026-06-12): stdio-proxy 를 스레드 스플라이스
     (블로킹 소켓 2 스레드, `ipc.control_socket` — Windows=TCP 루프백+포트파일)로
     재작성해 POSIX·Windows 공통. 새 프로세스라 **원격 서버 재시작 불필요**(코드
     동기화만). ⚠️ Windows OpenSSH 함정: 계정이 Administrators 그룹이면 sshd 가
     `%USERPROFILE%\.ssh\authorized_keys` 대신
     `C:\ProgramData\ssh\administrators_authorized_keys` 를 읽는다 — ssh-copy-id
     가 성공해 보여도 인증이 계속 실패하면 이 파일에 공개키를 넣어야 한다.
- 원격 pytmux 가 "호스트 단말이 pytmux 입니다(원격 중첩 감지…)" 로 거부하면 정상 —
  거부 메시지의 힌트대로 remote-attach 를 사용.
- 수동 점검: `ssh -T -o BatchMode=yes <host> pytmux stdio-proxy < /dev/null`
  → `TOKEN …` 한 줄이면 전 구간 준비 완료.
