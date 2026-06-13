# 원격 중첩 자동 승격 — 원격 `pytmux` 실행을 거부 대신 바깥 remote-attach 로 (시나리오)

> **상태**: ✅ **Stage N1~N3 구현 완료(CL 58670, 2026-06-13)** — 시나리오 도입 CL 58665.
> 열린 결정 확정(사용자, 2026-06-13): ㉠ argv 전체 b64→서버 `sshwrap.parse_dest` 파싱,
> ㉡ Windows .cmd 래퍼 DEST 발신 1차 제외, ㉢ `nest_auto_attach` 기본 ON+자동 탭 전환
> ON+확인 팝업 없음, ㉣ 호스트 대조=소문자 정규화+접두 일치(불일치=무 ack), ㉤ ack
> 1.0초. 잔여 = **Stage N4(실 ssh office1 라이브 검증)** 후속 — 코드는 서버+launcher
> 양쪽이라 라이브 반영엔 데몬 재시작 필요. 구현 중 발견: pyte 는 DCS 본문을 화면으로
> 흘린다 → `model.Pane.feed` 가 NEST DCS 를 제거/이월한다(§4 의 "화면 오염 금지"가
> 서버 스캔이 아니라 feed 전처리 소관이 됐다). 테스트 +5(launcher 2·server 1·remote 2).
>
> 백로그 "원격 중첩 ssh attach — 거부만 구현, 중첩 지원은 후속"(HANDOFF §9 CL 56394
> '원격 중첩은 후속')의 시나리오. §1.7 페더레이션([REMOTE_ATTACH_SCENARIO.md](REMOTE_ATTACH_SCENARIO.md),
> 완결)의 후속이며 그 인프라(remote_attach·stdio-proxy·in-band DCS 스캔)를 그대로 재사용한다.
>
> **한 줄 요약**: pytmux 패널에서 ssh 로 들어간 원격 셸에서 `pytmux` 를 치면, 지금은
> "거부 + ':remote-attach <이 호스트>' 힌트"로 끝난다(사용자가 로컬로 돌아와 호스트명을
> 손으로 쳐야 함). 이를 **그 한 번의 입력만으로 바깥 pytmux 가 자동으로 그 호스트에
> remote-attach 하고 원격 탭(분홍)으로 전환**되게 승격한다. 중첩 TUI 는 여전히 뜨지
> 않는다(§1.7 요청의 명시 비목표 유지).

## 목차
- [1. 배경 — 현황과 빈틈](#1-배경--현황과-빈틈)
- [2. 목표 / 비목표](#2-목표--비목표)
- [3. 사용자 흐름(UX)](#3-사용자-흐름ux)
- [4. 설계 — in-band 핸드셰이크](#4-설계--in-band-핸드셰이크)
- [5. 단계(구현 권고)](#5-단계구현-권고)
- [6. 테스트 계획](#6-테스트-계획)
- [7. 위험·완화·열린 결정](#7-위험완화열린-결정)

## 1. 배경 — 현황과 빈틈

중첩 거부는 2중 가드로 동작한다(둘 다 구현 완료):

1. **env 마커**: 패널 셸에 `$PYTMUX`(로컬)·`$LC_PYTMUX`(원격 — sshwrap 래퍼가
   `ssh -o SendEnv=LC_PYTMUX` 로 전파) → `launcher.nesting_blocked()` 가 거부.
2. **in-band 프로브**(§1.7 Stage 0): env 가 전파 안 되는 경로 대비.
   원격 attach 직전 `launcher.host_terminal_is_pytmux()` 가 단말에 XTVERSION
   (`ESC[>0q`)을 질의하고, 바깥 서버 `serverpty._on_pane_data` 가 패널 출력에서
   질의를 스캔(`NEST_QUERY`, read 경계는 `pane._nestq_carry` 보전)해
   `DCS >|pytmux ST`(`NEST_REPLY`)를 패널 stdin 으로 응답한다.

즉 **패널 스트림을 통한 안(원격 프로세스)↔밖(로컬 서버) 양방향 in-band 채널은 이미
검증된 패턴**이다. 그런데 거부 후 동선은 수동이다:

- 거부 메시지가 `:remote-attach <이 호스트>` 힌트를 줄 뿐, 사용자가 ① 로컬 pytmux 로
  돌아와 ② 호스트명을 알아내/기억해 ③ 명령을 직접 쳐야 한다.
- 특히 호스트명: 사용자가 실제로 친 것은 `ssh office1`(config 별칭, ControlMaster,
  도메인 계정 `NATGAMES\user@host`)인데 원격 머신은 자기 별칭을 모른다 — 거부
  메시지가 정확한 attach 인자를 제시할 수 없다.

## 2. 목표 / 비목표

**목표**
- 원격 셸에서 `pytmux` 한 번 입력 = 바깥 pytmux 의 `remote-attach <그 패널의 ssh
  목적지>` 자동 실행 + (옵션) 원격 탭 자동 전환. 원격 명령은 "위임됨" 안내 후 exit 0.
- 바깥이 구버전/기능 OFF/판단 불가면 **현행 거부 메시지로 정확히 폴백**(우아한 열화 —
  새 경로가 실패해도 지금보다 나빠지지 않는다).
- attach 대상은 **사용자가 그 패널에서 실제로 ssh 한 목적지 문자열 그대로**(별칭·
  ControlMaster·도메인 계정 보존 — §1.7 이 라이브 검증한 레시피를 그대로 탄다).

**비목표**
- 중첩 TUI(textual-in-pyte) 실행 지원 — §1.7 요청에서 명시적으로 원치 않음.
- tmux 식 이중 prefix(중첩 pytmux 키 패스스루) — 위와 같은 이유로 범위 밖.
- mosh 경로 보장 — mosh 단말 에뮬레이션이 미지 DCS 를 떨어뜨릴 수 있어 승격 요청이
  못 다닐 수 있다(ack 타임아웃 → 거부 폴백, §7).
- 원격 서버 측 코드 변경 요구 없음 원칙(§1.7)은 **이번엔 해당 없음** — 승격 요청을
  보내는 쪽이 바로 원격 launcher 라 원격도 신버전이어야 한다(구버전 원격은 현행
  거부 그대로 = 열화 없음).

## 3. 사용자 흐름(UX)

1. pytmux 패널에서 `ssh office1` → 원격 셸.
2. 원격에서 `pytmux` 입력.
3. 원격 launcher 가 중첩을 감지(env 마커 **또는** XTVERSION 프로브 — 기존 두 거부
   지점)하면, 거부 대신 **승격 요청 DCS** 를 stdout 으로 보내고 ack 를 기다린다
   (상한 ~1초, cbreak — 기존 프로브와 같은 단말 처리).
4. 바깥 서버가 패널 스트림에서 요청을 발견 → ① 그 패널에 기록된 **마지막 ssh
   목적지**(§4 의 래퍼 마커)를 확인 ② ack DCS 를 패널 stdin 으로 회신 ③
   `remote_attach(sess, host=목적지)` 태스크 시작.
5. 원격 명령: `pytmux: 바깥 pytmux 가 이 호스트를 원격 탭(⇄)으로 어태치합니다` 출력
   후 **exit 0**. ack 미수신이면 기존 거부 메시지+힌트로 폴백(exit 1).
6. attach 결과는 기존 패턴대로 바깥 상태줄 **notice**(성공: `remote-attach …: 원격
   탭 병합됨` / 실패: 원인). 성공 시 (기본 ON 제안) 그 링크의 첫 원격 탭으로 자동
   전환 — 사용자 시점에선 "원격에서 pytmux 쳤더니 분홍 탭이 떴다".
7. 같은 호스트 링크가 이미 있으면 재연결하지 않고 **기존 ⇄ 탭으로 전환만**(멱등).

## 4. 설계 — in-band 핸드셰이크

기존 `NEST_QUERY`/`NEST_REPLY` 와 동형의 DCS 3종을 신설한다. 스캔은 전 청크가
지나는 `serverpty._on_pane_data` 의 기존 지점 하나를 확장(carry 패턴 동일).

| 이름 | 방향 | 와이어(안) | 의미 |
|---|---|---|---|
| `NEST_DEST` | 래퍼→바깥 서버 (패널 출력) | `DCS >\|pytmux-ssh;<b64(argv-json)> ST` | "이 패널이 지금 이 목적지로 ssh 한다" |
| `NEST_ATTACH_REQ` | 원격 launcher→바깥 서버 (ssh 경유 패널 출력) | `DCS >\|pytmux-nest;<b64(user@hostname)> ST` | "중첩 감지 — 승격해 달라" |
| `NEST_ATTACH_ACK` | 바깥 서버→패널 stdin | `DCS >\|pytmux-nest-ack ST` | "접수"(성공 보장 아님 — 결과는 notice) |

- **`NEST_DEST` — 패널 ssh 목적지 기록**: sshwrap 래퍼(`_WRAPPER_SH`)가 진짜 ssh
  를 exec 하기 **직전** 자기 stdout(=패널 PTY)으로 마커를 찍는다. 페이로드는
  **argv 전체의 b64(JSON)** 권장 — 래퍼 sh 에서 ssh 옵션 문법(-o/-p/-J/-l 값 스킵)을
  파싱하는 대신 서버측 파이썬(`sshdest.parse_dest(argv)`)이 목적지를 추출한다(열린
  결정 ㉠). b64 는 도메인 백슬래시·세미콜론·공백을 보존한다. 서버는
  `pane._ssh_dest=(dest문자열, monotonic ts)` 로 최신 1건만 기록.
- **`NEST_ATTACH_REQ`**: launcher 의 두 거부 지점(env 마커 / in-band 프로브)을 공통
  `request_nest_promotion(rfd, wfd)` 로 모은다 — cbreak 로 REQ 를 쓰고 ACK 를
  select 대기(기존 `host_terminal_is_pytmux` 의 단말 처리·타임아웃 패턴 재사용).
  self-report(`user@hostname`)는 **표시·대조용일 뿐 attach 인자로 쓰지 않는다**(§7
  보안). POSIX 전제(프로브와 동일) — Windows 가 "원격"인 경우는 sshd 가 띄우는
  셸에 tty 가 있으면 동작하나 1차 범위 밖.
- **`NEST_ATTACH_ACK` 후 바깥 서버 동작**(`serverpty` 스캔 → `serverremote` 위임):
  1. 옵션 `nest_auto_attach`(서버 opt) OFF 면 무시 → 원격은 타임아웃 폴백(현행 거부).
  2. `pane._ssh_dest` 가 **없으면 ack 하지 않는다** — self-report 로 ssh 를 시도하지
     않는다(§7 보안 ㉣). 원격은 폴백 거부(힌트에 self-report 호스트를 표기해 수동
     attach 를 돕는 것은 가능).
  3. 패널당 디바운스(예 5초 1회) — 출력 재생/루프로 인한 중복 트리거 차단.
  4. ack 발신 + 이미 같은 host 링크가 있으면 전환만, 없으면
     `remote_attach(sess, host=dest)` 태스크(+성공 시 자동 전환, 열린 결정 ㉢).

**2단 ssh 대조(중요)**: 래퍼는 로컬 패널 PATH 에만 있으므로 `NEST_DEST` 는 **1단
목적지만** 기록된다. 사용자가 host1 에서 다시 `ssh host2` 후 host2 에서 pytmux 를
치면(2단 중첩 — XTVERSION 프로브는 단말 체인 끝의 pytmux 를 여전히 감지한다) REQ 가
오지만 기록된 목적지는 host1 이다 — 그대로 attach 하면 **엉뚱한 호스트에 붙는다**.
완화: REQ 의 self-report hostname 과 `pane._ssh_dest` 를 대조해 **불일치면 ack 하지
않고** 폴백 거부(힌트에 self-report 호스트 표기). 단 별칭(office1) vs 실호스트명
(OFFICE1.local 등) 불일치 오탐이 가능하므로 대조 강도는 열린 결정 ㉣(소문자
정규화·접두 일치부터 시작 권장 — 오탐이면 attach 자동화만 포기되고 안전).

## 5. 단계(구현 권고)

- **Stage N1 — 래퍼 목적지 마커 + 서버 기록**: `_WRAPPER_SH` 에 NEST_DEST 발신
  추가(printf 한 줄 — base64 는 POSIX coreutils/맥 기본), `serverpty` 스캔 확장 +
  `pane._ssh_dest`, 목적지 추출 `parse_dest`(서버측). Windows `.cmd` 래퍼의 raw
  ESC 출력은 까다로워(배치 escape) 1차 제외(열린 결정 ㉡ — POSIX 클라이언트만).
- **Stage N2 — launcher 승격 요청/ACK 대기 + 폴백**: `request_nest_promotion` 신설,
  두 거부 지점 통합. ack 시 안내+exit 0, 타임아웃/비tty 시 현행 메시지 그대로.
- **Stage N3 — 바깥 서버 자동 remote_attach**: 옵션 `nest_auto_attach`(기본값 열린
  결정 ㉢), 디바운스, ssh_dest 부재/대조 불일치 시 무 ack, 멱등(기존 링크 전환),
  성공 시 자동 탭 전환, notice.
- **Stage N4 — 문서·라이브 검증**: README zshrc 가드 절·REMOTE_ATTACH_SCENARIO §5
  사용 절 갱신, macOS→office1(ControlMaster) 실 ssh 전 구간 검증(§1.7 검증 프로브
  레시피 재사용 — 2000×2000 보조 클라).

각 Stage 는 독립 제출 가능(N1 은 단독으로도 무해 — 기록만 하고 소비자 없음).

## 6. 테스트 계획

- **N1**: 래퍼 본문에 NEST_DEST 발신 포함(파일 내용 단언 — 기존 sshwrap 테스트 패턴),
  서버 스캔이 `pane._ssh_dest` 를 기록(read 경계 분할 carry — `test_pane_xtversion_
  query_gets_pytmux_reply` 와 동형), `parse_dest` 단위(별칭/`user@host`/`-o`·`-p`·
  `-J` 값 스킵/도메인 백슬래시 b64 왕복/`-T` 등 플래그만), DCS 가 pyte 화면을
  오염하지 않음(렌더 단언).
- **N2**: `request_nest_promotion` 3분기(ack 수신/타임아웃 폴백/비tty 즉시 폴백) —
  pty 쌍 주입(`test_host_terminal_probe_inband_detection` 패턴).
- **N3**(`test_remote` 인프라 — in-process 서버 2대 실 소켓 직결): REQ 수신 →
  ack 회신+`remote_attach(endpoint)` 로 ⇄ 탭 병합, ssh_dest 부재 시 무 ack,
  self-report 불일치 시 무 ack, `nest_auto_attach` OFF 무시, 디바운스(연속 REQ
  1회 처리), 기존 링크 멱등 전환, 성공 시 보던 클라 자동 전환.
- **N4**: 실 ssh 수동 체크리스트(시나리오 §3 흐름 + 폴백 2종) — 자동화 범위 밖.

## 7. 위험·완화·열린 결정

**보안 — 패널 출력은 신뢰 경계 밖(가장 중요)**: REQ/DEST 는 패널 스트림에서 오므로
악성 파일 `cat` 으로 위조 가능하다. 설계 원칙:
- attach 인자는 **절대 REQ 의 self-report 를 쓰지 않는다**. 항상 래퍼가 기록한
  `pane._ssh_dest`(= 사용자가 그 패널에서 실제로 친 ssh 목적지) — 위조자가 유발할
  수 있는 최대치는 "사용자가 이미 신뢰·접속한 호스트로의 attach 를 원치 않는 시점에
  트리거"로 제한된다(새 호스트로의 아웃바운드 ssh 를 만들 수 없음).
- `NEST_DEST` 위조로 가짜 목적지를 심는 공격은 남는다 → ssh_dest 는 **래퍼 발신
  직후의 것만 신뢰할 수 없으므로** 디바운스+notice 가시화+`nest_auto_attach` OFF
  스위치로 완화. 더 강한 안은 확인 팝업(㉢) 또는 ssh_dest TTL.
- 모든 자동 attach 는 notice 로 가시화(조용히 일어나지 않는다).

**기타 위험**
- **mosh**: DCS 미통과 가능 → ack 타임아웃 폴백(거부) — 현행 동작과 동일, 열화 없음.
- **ACK 지연 레이스**: 원격이 타임아웃 후 종료한 뒤 ACK 가 도착하면 원격 셸 입력에
  DCS 바이트가 들어간다(프롬프트 노이즈 가능, 기능 무해 — 낮은 위험으로 기록).
- **XTVERSION 프로브와의 간섭**: REQ 대기와 프로브가 둘 다 cbreak 로 단말을 만진다 —
  프로브 직후 같은 cbreak 세션에서 REQ 까지 처리하도록 한 함수로 묶어 모드 전환을
  1회로(N2 에서 통합).

**열린 결정**
- ㉠ 목적지 추출 위치: 래퍼 sh 파싱 vs **argv 전체 b64 → 서버 파이썬 파싱(권장)**.
- ㉡ Windows(.cmd 래퍼) NEST_DEST 발신: 1차 제외 권장(배치 ESC 출력 곤란 —
  PowerShell 경유 비용 대비 가치 낮음). Windows 로컬 클라이언트는 거부 폴백 유지.
- ㉢ `nest_auto_attach` 기본값(ON 제안 — notice 가시화 전제) · 성공 시 자동 탭 전환
  (ON 제안) · 확인 팝업 도입 여부(OFF 제안 — "한 번의 입력" 목표 훼손).
- ㉣ self-report 호스트 대조 강도: 소문자 정규화+짧은쪽 접두 일치(제안) vs 미대조 vs
  완전 일치. 오탐 비용 = 자동화 포기(안전), 미탐 비용 = 2단 중첩 오어태치(위험) —
  보수적으로 시작.
- ㉤ ack 대기 상한: 1.0초 제안(`NEST_PROBE_TIMEOUT`(0.4s)보다 길게 — 바깥 서버의
  스캔→ack 는 로컬 RTT 지만 원격 왕복이 2회 끼는 경로 고려).
