# REC(패널 출력 캡처) 플러그인 추출 — 동작·설계 시나리오

> **상태**: 🟡 **계획(미구현)**. 본 문서는 REC(패널 출력 무손실 캡처) 기능 전체를
> `pytmuxlib/plugins/rec/` 하위로 옮기는 추출 설계 기준선이다. 구현 전 단계이며,
> 코어 현행 접점(아래 §4 표)을 빠짐없이 나열해 delete-to-disable 계약을 만족하는
> 이전 경로를 명세한다.
> **관련**: [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md)(플러그인 계약·통합 지점·무게 규칙) ·
> [NC_SCENARIO.md](NC_SCENARIO.md)(선행 추출 시나리오의 형식) ·
> [HANDOFF.md](HANDOFF.md) §11.6(claude-code 추출 진행 기록).
>
> **한 줄 요약**: REC 는 각 패널의 raw PTY 출력을 `captures/<머신>/<…>.log` 로 무손실
> 기록하는 **서버 본체 기능**이다(기본 ON, `capture-output` 토글, opts.json 영속,
> 상태줄 ` REC ` 배지 + 정보 팝업). 이를 통째로 `plugins/rec/` 로 옮겨 **디렉토리를
> 지우면** 캡처·배지·팝업·명령이 에러 없이 조용히 사라지고 코어는 PTY 를 그냥 흘려
> 보내게(기록 안 함) 만든다.
>
> **선행 추출과 무엇이 다른가(핵심)**: clock/calendar 는 순수 **클라 오버레이**,
> ncd 는 **클라 모달 + 서버 왕복**, claude-code 는 **서버 믹스인 + 다수 훅**이었다.
> REC 는 **서버 믹스인이 본체**(claude-code 처럼 `server_mixin()` 으로 합성)인데, 단
> **PTY 바이트 스트림 훅**(`serverpty.py:176` 의 `if self.capture: self._capture_write`)
> 만은 **기존 레지스트리에 대응 훅이 없다** → 이 추출은 코어에 **새 서버 훅 1종**
> (`server_pty_output`)을 추가해야 비로소 완전한 delete-to-disable 이 된다(§4.3).
>
> **혼동 주의(중요)**: 코어의 `capture_pane()`(server.py:640)·`{"t":"captured"}` 응답·
> `_capture_version`(serverio.py:857)·`capture_mouse`(textual)는 **REC 가 아니다**.
> `capture_pane` 은 "보이는 패널을 페이스트 버퍼로 복사"하는 별개 기능이고,
> `_capture_version` 은 p4 버전 스냅샷, `capture_mouse` 는 Textual 마우스 캡처다.
> 이름만 겹칠 뿐 추출 대상이 아니며 **코어에 그대로 남는다**(§10 위험표).

## 목차
- [1. REC 란 / 목표](#1-rec-란--목표)
- [2. 사용자 흐름(UX)](#2-사용자-흐름ux)
- [3. 화면 레이아웃 — 배지·팝업](#3-화면-레이아웃--배지팝업)
- [4. 아키텍처 — 어디에 무엇을 붙이나](#4-아키텍처--어디에-무엇을-붙이나)
- [5. 데이터 포맷 — 캡처 파일·sessions.log](#5-데이터-포맷--캡처-파일sessionslog)
- [6. 프로토콜 — 상태 필드·토글 명령](#6-프로토콜--상태-필드토글-명령)
- [7. delete-to-disable 검증 — 무엇이 사라지나](#7-delete-to-disable-검증--무엇이-사라지나)
- [8. 단계(구현 권고)](#8-단계구현-권고)
- [9. 테스트 계획](#9-테스트-계획)
- [10. 위험·완화·미해결](#10-위험완화미해결)

## 1. REC 란 / 목표

REC(코드네임 — 상태줄의 ` REC ` 배지에서 유래)는 각 패널의 **raw PTY 출력을 그대로
파일에 무손실 append** 하는 서버 기능이다. 목적은 실제 Claude Code 화면 출력
(limit/busy/idle/ctx 등)을 객관 근거로 모아 골든 픽스처·휴리스틱 보강에 쓰는 것이다
(IMPROVEMENT §3.2, TOKEN_SAVING M8). 핵심 동작:

1. 켜져 있으면(기본 ON) 모든 패널 PTY 읽기마다 그 바이트를
   `captures/<머신이름>/<날짜>_<시간>_<세션>_<탭>_p<패널>.log` 에 append 한다.
2. 탭/패널 매핑·제목은 같은 폴더의 `sessions.log` 에 한 줄씩 기록한다(raw 로그 비오염).
3. `capture-output [on|off]`(별칭 `capture-toggle`)로 끄고 켜며, 상태는 opts.json 에
   영속한다. 끄면 열린 파일을 닫고, 켜면 다음 출력 때 새 시각 파일명으로 재오픈한다.
4. 상태줄에 ` REC ` 배지를 띄우고, 클릭하면 통합 상태 팝업의 '출력 캡처' 탭(현재 ON/OFF·
   파일 경로·크기·sessions.log 위치)을 연다.

**추출 목표**: 위 전부를 `pytmuxlib/plugins/rec/` 한 디렉토리로 모은다. 디렉토리를
지우면 — 캡처가 멈추고(코어는 PTY 를 그냥 흘려보냄), ` REC ` 배지·정보 탭·`capture-output`
명령이 검색·자동완성·디스패치 어디에도 안 나타나며, status 메시지에서 `capture*` 필드가
빠지고, 코어는 **에러 없이** 그대로 동작한다. 캡처 디렉토리 정책(`captures/<머신>/` ·
0700/0600 · `.p4ignore`/`.gitignore` 차단)은 불변.

## 2. 사용자 흐름(UX)

REC 는 대부분 **백그라운드**다(켜 두면 알아서 기록). 사용자 접점은 세 가지:

1. **토글**: ESC 모드 `:` → `capture-output off`(또는 `on`/인자 생략 시 반전). 서버가
   `set_capture` 로 상태를 바꾸고 opts.json 에 영속, 끄면 열린 파일을 닫는다. 상태줄
   배지가 즉시 사라지거나 나타난다.
2. **배지 확인·클릭**: 상태줄 우측 ` REC ` 배지(캡처 ON 일 때만). 클릭하면 통합 상태
   팝업의 '출력 캡처' 탭이 열려 현재 패널의 캡처 파일 경로·크기·`sessions.log` 위치를
   보여 준다. 팝업의 `[c]` 키로 거기서 바로 캡처를 켜고 끌 수 있다.
3. **ESC-nav**: ESC 모드에서 ←/→ 로 상태줄 버튼을 돌 때 `rec` 버튼이 포커스되면 Enter 로
   같은 팝업을 연다(배지가 떠 있을 때만 버튼 목록에 들어감).

추출 후에도 이 흐름은 **외형·동작 불변**이어야 한다(REC 플러그인이 설치된 평소 상태).
플러그인을 지운 경우에만: 배지가 영영 안 뜨고, `capture-output` 은 미인식 명령이 되며,
팝업을 열어도 '출력 캡처' 탭이 빠진다(서버/토큰 탭만 남음).

## 3. 화면 레이아웃 — 배지·팝업

상태줄 우측 배지(SYNC/AR 배지 오른쪽, 캡처 ON 일 때만):

```
… │ main* │  SYNC   AR   REC   12:34 │ ⏣ 3/5 │ …
                              └ 클릭 → '출력 캡처' 탭 팝업
```

통합 상태 팝업(REC 클릭 시 '출력 캡처' 탭으로 열림. claude-code 가 있으면 '토큰
사용량' 탭이 가운데 끼어 REC(0)·토큰(1)·서버(2), 없으면 REC(0)·서버(1)):

```
┌─ 상태 ── [출력 캡처] 토큰 사용량  서버 ──────────────────────┐
│ [c] 캡처 끄기                                               │
│                                                            │
│ 캡처: ON                                                   │
│ 파일: captures/macbook/20260610_184500_0_0.claude_p1.log   │
│ 크기: 12.4 KiB                                             │
│ 매핑: captures/macbook/sessions.log                        │
└────────────────────────────────────────────────────────────┘
```

- 배지·클릭존: `clientwidgets.py` 의 `StatusBar`(`self.capture`/`_rec_zone`/
  `capture_path`/`capture_size`). 팝업 본문: `client.py` 의 `_capture_info_lines`/
  `show_capture_info`. 팝업 틀: `clientscreens.py` 의 `InfoScreen`(범용, **코어 유지**).
- 캡처 OFF 면 배지 미표시·`_rec_zone=None`(클릭 no-op). 팝업을 다른 경로로 열면 머리줄에
  `(캡처 꺼짐 — REC 미표시)`.

## 4. 아키텍처 — 어디에 무엇을 붙이나

> **현행 코어 접점(추출 전, 2026-06-10 기준).** 아래를 모두 `plugins/rec/` 로 옮기거나
> 레지스트리 훅 호출로 바꾼다. `keep` 은 코어 잔류(범용/타 기능 공유).

| 측 | 파일:라인 | 심볼 | 처리 |
|----|-----------|------|------|
| 서버 | `servercapture.py` 전체 | `ServerCaptureMixin`(`_capture_*`/`set_capture`/`_close_*`) | → `plugins/rec/servermixin.py` |
| 서버 | `servercapture.py:32-46` | `_capture_id`/`_capture_subdir`/`_safe`/`PROJECT_DIR` | **keep**(토큰 DB 가 빌려 씀 — §10) |
| 서버 | `server.py:18` | `from .servercapture import ServerCaptureMixin` | 삭제 |
| 서버 | `server.py:36-38` | `_SERVER_BASES` 에 `ServerCaptureMixin` | 삭제(`server_mixins()` 가 주입) |
| 서버 | `server.py:91-95` | `_capfiles`/`_cappaths`/`capture` 상태 init | → 플러그인 `attach_server`(§4.3) |
| 서버 | `serverpty.py:176-177` | `if self.capture: self._capture_write(pane, data)` | → **새 훅** `server_pty_output`(§4.3) |
| 서버 | `serverio.py:117,130-132` | status 의 `capture`/`capture_path`/`capture_size` | → `server_status` 훅 기여 |
| 서버 | `serverio.py:494-495` | `action=="set_capture"` 디스패치 | → `server_command` 또는 `handle_server_request` |
| 서버 | `serverio.py:793` | 종료 시 `_close_all_capfiles()` | → 새 훅 `server_shutdown` 또는 mixin `__del__`/atexit(§4.3) |
| 서버 | `server.py:503` | `_ONOFF_CONTROLS["capture-output"/"capture-toggle"]="set_capture"` | → 플러그인 명령 메타+디스패치 |
| 서버 | `serverpersist.py:339` | `_save_opts` 의 `"capture": self.capture` | → opts 기여 훅 또는 getattr 가드(§4.3) |
| 서버 | `server.py:95`·`serverpersist.py` | `_load_opts().get("capture", True)` | → 플러그인이 `attach_server` 에서 로드 |
| 클라 | `clientwidgets.py:862,907,911-912` | `capture`/`_rec_zone`/`capture_path`/`capture_size` 필드 | → `client_statusbar_update` 가 흡수·소유 |
| 클라 | `clientwidgets.py:1006,1013-1014` | status 의 `capture*` 흡수 | → `client_statusbar_update` 훅 |
| 클라 | `clientwidgets.py:1056-1063` | ` REC ` 배지 렌더 + `_rec_zone` 설정 | → `client_statusbar` 훅 |
| 클라 | `clientwidgets.py:1132-1134` | REC 존 클릭 → `show_capture_info` | → 배지 소유 플러그인 측에서(존 dict) |
| 클라 | `client.py:478,488-489,548-550` | ESC-nav `rec` 버튼 | → 플러그인 버튼 기여(또는 getattr 가드) |
| 클라 | `client.py:824` | `t=="captured"`(페이스트 복사 응답) | **keep**(REC 아님 — `capture_pane`) |
| 클라 | `client.py:839-861` | `_capture_info_lines`/`show_capture_info` | → 플러그인(팝업 줄 생성) |
| 클라 | `client.py:1605-1615` | 통합 상태 팝업의 '출력 캡처' 탭·`[c]` 토글 | → `client_status_tabs` 훅 기여 |
| 클라 | `clientscreens.py` | `InfoScreen`(탭 팝업 틀) | **keep**(범용) |
| 양측 | `replay.py` | `replay()`/`run_replay()`(오프라인 재생 도구) | **keep**(진단 CLI, REC 파일 소비자) |

### 4.1 서버 측 (본체)

- **믹스인 이전**: `ServerCaptureMixin` 을 `plugins/rec/servermixin.py` 로 옮기고,
  `plugins/rec/__init__.py` 의 `server_mixin()` 이 **지연 import** 로 그 클래스를 돌려
  준다(claude-code 의 `ServerClaudeMixin` 패턴). `server.py:35` 의
  `plugins.load().server_mixins()` 가 이를 `Server` 동적 베이스에 합성한다. 디렉토리를
  지우면 목록이 비어 캡처 로직이 `Server` 에서 통째로 빠진다.
- **무게 규칙**: `__init__.py` 는 `textual`/`rich` 를 최상단에서 import 하지 않는다
  (서버도 같은 코드를 읽는다). `servermixin.py` 는 `os`/`socket`/`time`/`re`/`json`
  표준 라이브러리만 쓰므로(현행 `servercapture.py` 그대로) 서버측 모듈이라 무게 문제
  없음 — 다만 `__init__.py` 의 명령 메타·클라 훅은 가볍게 유지.
- **명령**: `COMMANDS=[("capture-output", "패널 출력 캡처 토글…", "설정/기타"),
  ("capture-toggle", …)]`, `NOARG={"capture-output","capture-toggle"}`. 서버측
  `server_command`(또는 `handle_server_request`)가 `set_capture` 액션을 처리.

### 4.2 클라이언트 측 (표시 전용)

- **상태 흡수**: `client_statusbar_update(app, status, msg)` 훅에서 status 의
  `capture`/`capture_path`/`capture_size` 를 status 위젯에 흡수(현행
  `clientwidgets.py:1006,1013-1014` 이전). 필드 자체도 플러그인이 소유(없으면 코어는
  그 키를 안 봄).
- **배지**: `client_statusbar(app, status, segs, w)` 훅에서 ` REC ` 세그먼트 append +
  클릭존 설정(현행 `clientwidgets.py:1056-1063`). claude-code 가 같은 훅으로 모델/토큰
  세그먼트를 그리는 것과 동일 구조 — 여러 플러그인이 좌측 세그먼트를 함께 채운다.
- **팝업 탭**: `client_status_tabs(app, tree)` 훅에서 `("출력 캡처", 줄들)` 튜플 기여
  (현행 `client.py:1605-1615`). 디렉토리를 지우면 팝업에 이 탭이 빠지고 서버/토큰만 남음
  (레지스트리 docstring 의 `client_status_tabs` 설명과 정합).
- **팝업 줄 생성**: `_capture_info_lines`(ON/OFF·경로·크기·sessions.log)은 플러그인으로
  이전. 무거운 import 없음(문자열 포맷만).

### 4.3 새로 필요한 코어 훅 (이 추출의 핵심 작업)

REC 의 다섯 접점 중 넷은 **기존 훅**(`server_mixin`/`server_status`/`server_command`/
`client_statusbar*`/`client_status_tabs`)에 정확히 매핑된다. 그러나 **PTY 바이트 훅**과
**서버 인스턴스 상태 init/종료**는 기존 레지스트리에 대응이 없어 코어에 추가해야 한다.

1. **`Registry.server_pty_output(server, pane, data)`** — **신규(필수)**.
   현행 `serverpty.py:176`:
   ```python
   if self.capture:
       self._capture_write(pane, data)
   ```
   이는 `self.capture`·`self._capture_write` 를 코어가 **이름으로 직접** 부른다 →
   플러그인 부재 시 `AttributeError`. 이를 무조건 호출하는 훅으로 바꾼다:
   ```python
   self.plugins.server_pty_output(self, pane, data)   # 플러그인 없으면 no-op
   ```
   플러그인 측 `server_pty_output(server, pane, data)` 가 **자기** capture 플래그를
   검사해 기록한다. 플러그인이 없으면 레지스트리 메서드가 빈 루프라 코어는 PTY 를 그냥
   흘려보낸다(delete-to-disable). *주의: 이 훅은 30Hz 드레인 루프의 모든 바이트마다
   불리는 **핫패스**다 — 레지스트리 순회는 `self.plugins` 가 보통 0~1개라 무시할 만하나,
   호출 오버헤드를 재 둔다(§10).*

2. **`Registry.attach_server(server)` 또는 mixin 지연 init** — 서버 인스턴스 상태
   (`_capfiles`/`_cappaths`/`capture`)를 플러그인이 설치할 자리. 두 안:
   - (권장) `attach_client` 의 서버 대칭형 **`attach_server(server)`** 훅을 추가해
     `Server.__init__` 끝에서 1회 호출 → 플러그인이 `server._capfiles={}` 등 + opts 에서
     `capture` 로드를 설치. claude-code 는 상태를 model.py 의 Pane/Tab 기본값에 두어
     필요 없었지만, REC 는 서버 인스턴스 상태가 있어 이 훅이 자연스럽다.
   - (대안) 새 훅 없이 mixin 메서드에서 `getattr(self,"_capfiles",None)` 지연 init.
     훅은 안 늘지만 매 호출 가드가 흩어진다 — clock 의 `getattr(app,"clock_panes",...)`
     방어 패턴과 같은 트레이드오프.

3. **종료 시 파일 닫기** — 현행 `serverio.py:793` `_close_all_capfiles()`(서버 종료 경로).
   `Registry.server_shutdown(server)` 훅(신규)으로 위임하거나, 캡처 파일은 `buffering=0`
   (즉시 flush)이라 닫기 누락이 데이터 손실은 아니므로 **best-effort**로 두고 OS 회수에
   맡기는 안도 가능(파일 핸들 누수만 — 단명 프로세스라 수용 가능). 권장은 얇은
   `server_shutdown` 훅.

4. **opts 영속** — 현행 `serverpersist.py:339` `"capture": self.capture`. 두 안:
   - `_save_opts`/`_load_opts` 가 `Registry.server_opts_save(server, opts)` /
     `server_opts_load(server, opts)` 훅을 호출해 플러그인이 자기 키를 채우게(클린).
   - 또는 최소 변경: 그 줄을 `if hasattr(self,"capture"): opts["capture"]=self.capture`
     getattr 가드로(키가 빠지면 다음 로드 때 기본 ON). 첫 추출 CL 은 후자로 빠르게,
     후속에서 전자로 정리 가능.

> **설계 메모**: 1(`server_pty_output`)은 **반드시** 추가해야 추출이 성립한다(코어가
> raw 바이트를 직접 가로채던 유일한 비훅 접점). 2~4 는 "새 훅 추가(클린)" vs "getattr
> 가드(최소)"의 선택지이며, NC/clock 추출이 택한 getattr 방어 패턴으로도 계약은 충족된다.
> 권장: **1 + 2(attach_server)** 만 신규 훅으로 추가하고, 3·4 는 1차에서 getattr/best-effort,
> 후속 CL 에서 훅으로 승격.

## 5. 데이터 포맷 — 캡처 파일·sessions.log

추출해도 **불변**(파일 포맷·경로·권한 정책 그대로). 참고용 요약:

- **캡처 파일**: `captures/<머신이름>/<YYYYMMDD>_<HHMMSS>_<세션>_<탭idx.이름>_p<패널id>.log`
  — raw PTY 바이트 무손실 append. 예: `20260610_184500_0_0.claude_p1.log`. 파일명에 기록
  시작 시각이 박혀 한 폴더에서 바로 식별·정렬된다.
- **매핑 로그**: 같은 폴더 `sessions.log` — `YYYY-MM-DD HH:MM:SS <파일명> tab<idx>:<이름>
  title='<패널제목>'` 한 줄(파일 최초 오픈 시 1회). raw 로그를 오염하지 않는 별도 텍스트.
- **디렉토리 정책**: 기본 소켓은 `PROJECT_DIR/captures/<머신이름>/`(Perforce 공유·기계별
  격리), 비기본/임시 소켓은 `state_base + ".capture"`(휘발). `PYTMUX_CAPTURE_DIR` 로 강제
  지정(테스트). 권한 0700(디렉토리)/0600(파일) best-effort — raw 출력에 에코된 비밀번호·
  토큰 유출 차단. **GitHub/Perforce 미러에 절대 안 올라가게** `captures/` 는
  `.gitignore`/`.p4ignore` 차단(추출과 무관하게 유지).
- **소비자**: `replay.py`(`replay(data, cols, rows)`/`run_replay()`)가 캡처 파일을 pyte 로
  재생해 화면을 복원하는 오프라인 진단 CLI. REC 파일의 읽는 쪽이라 코어 유지(플러그인이
  쓰고 이 도구가 읽는 느슨한 결합 — 포맷만 안정되면 됨).

## 6. 프로토콜 — 상태 필드·토글 명령

**status(서버→클라)** — 캡처 ON 인 활성 패널이 있을 때 추가 필드(현행 serverio.py:130-132):
```json
{"t":"status", …,
 "capture": true,
 "capture_path": "/…/captures/macbook/20260610_184500_0_0.claude_p1.log",
 "capture_size": 12678}
```
추출 후 이 세 키는 `server_status` 훅이 채운다. 플러그인 부재 시 키가 **빠지고**, 클라
(역시 플러그인 부재)는 그 키를 안 본다 → 배지·팝업 모두 비활성(대칭 delete-to-disable).

**토글 요청(클라→서버)** — 현행 serverio.py:494:
```json
{"t":"cmd","action":"set_capture","value":false}   // value 생략 시 반전
```
추출 후 `server_command`(처리 시 `'broadcast'` 반환 → 코어가 세션 재방송 + 요청 클라에
full)로 처리, 또는 알 수 없는 action 으로 떨어뜨려 `handle_server_request` 가 받는다.
서버는 `set_capture(value)` → opts 영속 + (off 시) 열린 파일 닫기 후, 갱신된 `capture`
필드를 다음 status 로 전파.

**페이스트 복사와 구분**: `{"t":"captured","chars":N}`(serverio.py:371/386/433)와
`capture_pane` 액션은 "보이는 패널 → 페이스트 버퍼 복사"로 **REC 와 무관**. 이름이 겹쳐도
추출 대상 아님(코어 유지).

## 7. delete-to-disable 검증 — 무엇이 사라지나

`rm -rf pytmuxlib/plugins/rec/` 후 기대 동작(계약 테스트로 고정 — §9):

| 접점 | 플러그인 있음 | 디렉토리 삭제 후 |
|------|---------------|-------------------|
| PTY 출력 | `server_pty_output` 가 파일에 기록 | 훅 no-op → 기록 안 함, PTY 정상 흐름 |
| 명령 검색/자동완성 | `capture-output`/`capture-toggle` 노출 | 미인식(검색·디스패치 어디에도 없음) |
| `set_capture` 액션 | `server_command`/`handle_server_request` 처리 | 미처리 → 코어가 조용히 무시 |
| status 필드 | `capture`/`capture_path`/`capture_size` 채움 | 키 빠짐 → 클라가 안 봄 |
| 상태줄 배지 | `client_statusbar` 가 ` REC ` 그림 | 미표시, `_rec_zone=None`(클릭 no-op) |
| ESC-nav `rec` 버튼 | 배지 떠 있으면 목록에 포함 | 버튼 없음 |
| 상태 팝업 탭 | `client_status_tabs` 가 '출력 캡처' 기여 | 탭 빠짐(서버/토큰만) |
| opts.json | 플러그인이 `capture` 키 영속 | 키 미기록(무해 — 로더가 기본 ON 으로 간주) |
| 토큰 DB 경로 | `_capture_id()` 코어 잔류분 사용 | **영향 없음**(§4 표 keep — §10 위험①) |

**코어가 깨지면 안 되는 지점**: ① `serverpty.py` PTY 루프가 `self.capture` 를 더는 직접
안 봄(훅 경유). ② `serverio.py` status 빌더가 `self._capture_info`/`self.capture` 를 직접
안 부름(훅 경유). ③ 토큰 DB 경로가 `_capture_id` 를 부르므로 그 헬퍼는 코어 잔류(§10).

## 8. 단계(구현 권고)

CL 단위를 작게(논리 1~2개) 쪼개 회귀망을 항상 녹색으로 유지한다(claude-code 추출의
Phase 분할 교훈, PLUGIN_SYSTEM.md §4).

1. **코어 훅 선설치(서버)** — `Registry.server_pty_output` + `attach_server` 추가,
   `serverpty.py:176` 을 훅 호출로, `Server.__init__` 끝에 `attach_server` 호출. 이
   시점엔 아직 `ServerCaptureMixin` 이 코어 베이스라 동작 불변(훅이 비어도 기존 경로
   유지되게 임시 양립) — 또는 한 CL 에서 2와 합쳐 원자적 이전.
2. **서버 본체 이전** — `servercapture.py` 의 캡처 로직을 `plugins/rec/servermixin.py`
   로, `_capture_id`/`_capture_subdir`/`_safe`/`PROJECT_DIR` 만 코어 잔류(토큰 DB 용,
   §10). `plugins/rec/__init__.py` 에 `server_mixin()`·`attach_server`·`server_pty_output`·
   `server_status`(status 필드)·`server_command`(set_capture)·명령 메타. `server.py`
   에서 import·`_SERVER_BASES`·상태 init·`_ONOFF_CONTROLS` 항목 제거. `serverio.py`
   status 필드·`set_capture` 디스패치 제거.
3. **클라 표시 이전** — `client_statusbar_update`(흡수)·`client_statusbar`(배지·존)·
   `client_status_tabs`('출력 캡처' 탭)·`_capture_info_lines` 를 플러그인으로. `clientwidgets`/
   `client.py` 의 직접 참조 제거(또는 getattr 가드). ESC-nav `rec` 버튼 처리.
4. **opts·종료 정리(선택)** — getattr 가드를 `server_opts_save/load`·`server_shutdown`
   훅으로 승격(클린업, delete-to-disable 계약엔 불필요).
5. **계약 테스트** — `tests/test_plugin_contract.py` 에 rec 케이스 추가(§9). 기존
   `tests/test_server.py` 의 캡처 테스트가 플러그인 경유로도 녹색인지 확인·이전.
6. **문서** — 본 시나리오 상태 🟡→🟢, PLUGIN_SYSTEM.md 레퍼런스 플러그인에 rec 추가,
   HANDOFF.md §11.6 / FEATURES 갱신. PLUGIN_MANUAL.md 에 "사례 4 — rec(서버 본체+PTY 훅)"
   추가(서버 믹스인+신규 훅을 설명하는 네 번째 사례로 좋음).

## 9. 테스트 계획

**서버(IPC 없이 직접)** — `tests/test_server.py`/신규 `tests/test_plugin_rec.py`:
- `set_capture`: 토글·명시 on/off·opts 영속(`_save_opts` 호출)·off 시 `_close_all_capfiles`.
- `server_pty_output` 훅: capture ON → `capture_dir` 에 시각 파일 생성·append·sessions.log
  한 줄; OFF → 파일 미생성. `PYTMUX_CAPTURE_DIR` 주입으로 프로젝트 오염 방지.
- `_capture_filename`/`_capture_subdir`/`_safe`: 포맷·머신 격리·비허용 문자 정리(현행
  테스트 이전).
- `server_status` 기여: capture ON 인 패널 → status dict 에 `capture`/`capture_path`/
  `capture_size`; OFF → 키 부재.

**클라(Textual headless)** — `tests/test_client.py`/`test_clientrender.py`:
- status 에 `capture=true` → 상태줄 ` REC ` 세그먼트 그려지고 `_rec_zone` 설정.
- REC 존 클릭 → '출력 캡처' 탭 팝업, 줄에 경로·크기.
- `capture=false`/키 부재 → 배지 없음·`_rec_zone=None`.

**계약(delete-to-disable)** — `tests/test_plugin_contract.py`:
- rec 플러그인을 레지스트리에서 제외한 가짜 Registry 로:
  - `server_pty_output` no-op(파일 미생성)·status 에 `capture*` 키 부재.
  - `commands`/`noarg` 에 `capture-output` 부재.
  - `client_statusbar` 가 ` REC ` 미append·`client_status_tabs` 에 '출력 캡처' 부재.
  - 코어 PTY 루프·status 빌더가 `AttributeError` 없이 통과(핵심 회귀).

## 10. 위험·완화·미해결

- **① 토큰 DB ↔ `_capture_id` 커플링(중요)**: `server.py:328-332` 의 토큰 DB 경로가
  `self._capture_id()`(현재 `ServerCaptureMixin` 제공)를 부른다. 믹스인을 통째로 옮기면
  이 헬퍼가 사라져 **토큰 DB 가 깨진다**. 완화: `_capture_id`/`_capture_subdir`/`_safe`/
  `PROJECT_DIR` 를 **코어에 잔류**(servercapture.py 를 얇은 코어 유틸로 남기거나
  `serverpersist`/`server` 로 이동)시키고, 플러그인은 그 코어 헬퍼를 빌려 쓴다. 계약
  테스트에 "rec 삭제해도 토큰 DB 경로 정상" 케이스 추가.
- **② PTY 핫패스 오버헤드**: `server_pty_output` 은 모든 출력 바이트마다 호출되는
  30Hz 드레인 핫패스다. 레지스트리 순회(`for p in self.plugins`)는 보통 0~1개라 무시할
  만하나, 측정 권고(대량 출력 벤치 — `docs/benchmark/`). 필요 시 코어가 "PTY 훅을 가진
  플러그인"을 부팅 시 1회 캐시해 빈 순회를 건너뛰는 최적화.
- **③ 서버 인스턴스 상태 init**: `Server.__init__` 은 `super().__init__()` 을 안 부르므로
  믹스인 베이스만으론 `_capfiles` 등이 자동 init 안 됨 → `attach_server` 훅 또는 getattr
  지연 init 필요(§4.3). 빠뜨리면 첫 캡처에서 `AttributeError`.
- **④ 명령 이름 충돌·혼동**: `capture-output`(REC) vs `capture_pane`/`captured`(페이스트
  복사). 추출 시 후자를 건드리지 않도록 grep 가드. 사용자에게도 둘은 다른 기능.
- **⑤ opts 키 마이그레이션**: 기존 사용자의 opts.json 에 `capture` 키가 이미 있음.
  플러그인이 로드/세이브를 가져가더라도 기존 값(특히 사용자가 off 로 끈 선택)을 존중해야
  함 → `attach_server` 에서 기존 키 그대로 읽기.
- **⑥ 단계 중간 WIP 크래시 주의**: 반쯤 이전된 상태(코어에서 뺐는데 플러그인 훅 미연결)
  로 서버가 뜨면 PTY 루프나 status 빌더가 즉사할 수 있다(2026-06-10 교훈 — 반쯤 통합된
  플러그인 WIP 크래시). 각 CL 은 자체로 부팅·418+ 테스트 녹색이어야 제출.
- **⑦ 병렬 세션 격리**: playground 는 공유 워크스페이스 — 추출 CL 은 번호 changelist 에
  관련 파일만 담고, 병렬 세션의 작업 트리 변경은 건드리지 않는다(shared-workspace 교훈).
- **미해결/후속**: ① `server_shutdown`/`server_opts_*` 훅 정식화(클린업) ② replay CLI 를
  rec 플러그인 산하로 옮길지(현재 코어 진단 도구로 유지) ③ 캡처 회전/상한(파일 크기·보존
  기간) — 현재 무제한 append.
