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
**별도 ssh exec 채널**을 전송으로 쓴다. 서버 없으면 exit 1. POSIX 전용(stdin
add_reader). v1 단순화: stdout 쓰기는 블로킹 `os.write` — 프록시 루프에 다른 일이
없어 무해.

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

### Stage 3 잔여(후속)
원격 탭 active 하이라이트(status 가 클라별이 아님 — per-client status 필요), 끊김 시
백오프 자동 재연결, 재시작(re-exec) 후 링크 복원, 원격 status 부가 필드(Claude 헤더
등) 전달, 다중 원격 동시·이름 충돌 정리, Windows(stdio-proxy POSIX), 실 ssh 라이브
검증(헤드리스는 직결 검증 — ssh 경로는 TOKEN 핸드셰이크 테스트로만 커버).

## 5. 사용

- 로컬 pytmux 에서 `:remote-attach <host>` → 탭바에 `⇄host:이름` 탭 등장 → 클릭/
  alt+N 으로 진입(원격 화면·입력 그대로), 로컬 탭 클릭으로 복귀, `:remote-detach`.
- 원격 pytmux 가 "호스트 단말이 pytmux 입니다(원격 중첩 감지…)" 로 거부하면 정상 —
  거부 메시지의 힌트대로 remote-attach 를 사용.
- 수동 점검: 원격에서 `pytmux stdio-proxy < /dev/null` → `TOKEN …` 한 줄 후 종료.
