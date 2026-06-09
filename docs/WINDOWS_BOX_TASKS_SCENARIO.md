# Windows 박스 전용 작업 — 시나리오 / 작업 분담

> **목적**: 이 저장소는 두 머신에서 동시에 작업된다 — `office` 클라이언트(**Windows 11**,
> `D:\p4\office`)와 `playground` 클라이언트(**macOS**, `/Users/neoocean/p4/playground`).
> 둘 다 같은 depot(`//woojinkim/scripts/pytmux/...`)을 공유한다. 이 문서는 **Windows
> 박스에서만 할 수 있는(= Mac 세션이 못 하는) 작업**을 한곳에 모아, 작업이 엉뚱한
> 머신에 배정돼 낭비되지 않게 한다.
>
> 분석·전략 문서가 이미 있다 — 여기서는 **"무엇을 누가 한다"** 만 다루고 근거는 링크한다:
> [WINDOWS_PORT.md](WINDOWS_PORT.md)(포팅 범위·기능 열화 §4), [WINDOWS_TESTING.md](WINDOWS_TESTING.md)
> (무엇이 진짜 Windows를 요구하나 §2-b·CI 한계 §3-c·박스 보조수단 §4-a),
> [ENV_SETUP_WINDOWS.md](ENV_SETUP_WINDOWS.md)(박스 셋업), [HANDOFF.md](HANDOFF.md)(전체 백로그).

## 1. 배경 — 왜 분담이 필요한가

- pytmux의 PTY 계층은 OS별로 **다른 백엔드**다: POSIX `_UnixPty`(fork+fd) vs Windows
  `_WinPty`(ConPTY + 리더 스레드 펌프, `pty_backend.py:305`). ConPTY의 실동작·레이스는
  **Mac에서 재현·검증 불가**다.
- 키 입력은 **바깥 터미널**(Windows Terminal 등)이 인코딩한다. Shift+Enter·kitty 키보드
  프로토콜·win32-input-mode 같은 건 **그 터미널 + 사람의 실제 키 입력**으로만 확인된다.
- 헤드리스 스위트(`python tests/run.py`)·성능 측정·플랫폼 분기 **로직**은 mock으로
  어느 OS에서나 동일하게 검증된다(WINDOWS_TESTING §2-a) — **이건 Mac도 한다.** 아래
  §3 만이 Windows 박스 전용이다.

## 2. 한눈 분담표

| 작업 | Mac(playground) | Windows 박스(office) |
|------|:---:|:---:|
| 헤드리스 스위트·로직·플랫폼 분기 mock 테스트 | ✅ | ✅ |
| 코드 작성·리팩터·문서 | ✅ | ✅ |
| **실 ConPTY spawn/read/write/resize/terminate** | ❌ | ✅ |
| **pywinpty 설치·import 회귀** | ❌ | ✅ |
| **ConPTY 리더↔set_winsize 레이스·shrink-wrap** | ❌ | ✅ |
| **Windows Terminal 키/프로토콜 라이브 검증** | ❌ | ✅(사람) |
| **실 Claude Code 화면 REC 캡처(골든 픽스처)** | ❌ | ✅ |
| **Windows 경로·인코딩(cp949/UTF-8)·TCP IPC 실동작** | ❌ | ✅ |
| **기능 열화 폴백 실확인**(cwd 상속·fg 자동이름·시그널) | ❌ | ✅ |
| OS 픽셀 스크린샷 | ❌ | ❌(박스도 차단 — §4) |

## 3. Windows 박스 전용 작업(열린 항목)

### A. ConPTY 실동작 — test_sync_input_broadcast skip (헤드리스 스위트 한정 아티팩트)
- **현상**: 전체 `tests/test_server.py` 를 끝까지 돌린 뒤 `test_sync_input_broadcast` 가
  신규 split 패널에서 입력 echo 를 못 받아 **결정적으로 FAIL**(행 아님). 그래서 그 테스트
  만 **Windows에서 skip**된다(`if ipc.IS_WINDOWS: return`). 단독·소수 실행·실데몬
  스모크·실사용에선 정상.
- **(과거 가설은 반증됨)** 한때 "리더 `read()` ↔ 메인 `set_winsize` 데드락"으로 봤으나,
  아래 조사에서 격리 재현 실패로 반증됐다. 백엔드 결함이 아니라 헤드리스 스위트 누적
  상태의 부하·타이밍 아티팩트로 본다.
- **연관(이미 완화됨)**: `/compact` 구분선 misalignment는 폭 축소 시 ConPTY가 새 폭을
  늦게 받아 생긴 것 → `model.py`의 `_wrap_guard`(폭 축소 시 DECAWM 리셋) + `set_winsize`
  를 pyte 축소 **이전**에 통지(`Pane.resize`)로 시각적 무해화. **근본 레이스는 미해결.**
- **Windows 전용인 이유**: ConPTY 리더 스레드 모델 자체가 Windows에만 있다.
- **조사·정정(2026-06-09)**: 앞서 이 항목을 "행(hang)"으로 적었으나 **정정한다** — 그
  "행"은 win_report 의 watchdog 설정 탓이었다(§3-C 에서 별도 수정). 본 테스트를 실제로
  un-skip 해 `python tests/run.py test_server` 로 측정하니 **행이 아니라 결정적 FAIL**
  이다(echo 미수신 단언 실패; 러너 per-test 90s 안에 3회 모두 실패).
- **가설 반증**: "리더 `read()` ↔ 메인 `set_winsize` 데드락" 가설은 **격리 재현으로
  반증됐다**: ① raw pywinpty 에서 블로킹 read 중 `setwinsize`+`write` 를 때려도 정상
  echo, ② 서버 맥락에서 split/kill **60회** 누적해도 정상 echo + **리더 스레드 누수 0**
  (핸들 누수 없음). 즉 `_WinPty` 백엔드엔 **재현 가능한 결함이 없다**. 실패는 오직
  전체 `test_server`(80여 테스트)를 누적 실행한 프로세스 상태에서만 나오는 **부하·타이밍
  의존 아티팩트**(갓 spawn 된 cmd.exe 프롬프트 준비 전 입력이 쓰여 유실되는 것으로 추정).
- **결론(2026-06-09)**: 제품은 실사용에서 정상이다(실 attach·라이브 키 검증·나머지 385
  통과). 재현 가능한 근본 원인이 없어 **위험한 백엔드 재작성은 부적절**(작동 중 포트를
  흔들며 phantom 을 쫓는 격). **skip 유지가 올바른 완화.** 재개하려면 백엔드 변경이
  아니라 (a) 어느 선행 테스트가 프로세스 상태를 오염시키는지 bisect, 또는 (b) 본 테스트를
  **셸-준비(프롬프트) 대기 후 write** 하도록 견고화하는 쪽이다.

### B. Windows Terminal 키/프로토콜 라이브 검증(사람 필요)
바깥 터미널이 인코딩하므로 **실 WT에서 사람이 키를 눌러야** 확정된다(CI·헤드리스 불가).
- **Shift+Enter → 줄바꿈**: 기본 WT는 Shift+Enter를 Enter(CR)와 동일하게 보내 Textual이
  구별 못 함. WT `settings.json`에 `shift+enter → sendInput "\n"`(LF=Ctrl+J 경로) 바인딩으로
  해결. **Warp는 불가**(Shift+Enter 가로채고 hex 전송 액션 없음 — Ctrl+J가 정석 우회).
- **kitty 키보드 프로토콜 누수**: Claude Code가 `\x1b[>1u`(push)/`\x1b[<u`(pop)를 내보내고
  pyte 0.8.2가 pop의 끝 `u`를 화면에 흘리던 버그 → `model.py:_KITTY_KBD_RE`로 제거(가드
  테스트 `test_claude_terminal_protocol_sequences_no_leak`). **실 WT에서 `u` 미발생 최종 확인 필요.**
- **남은 라이브 확인**: Shift+ESC 인코딩(conhost/일부 WT는 Shift 수정자 누락 → `send-escape`
  명령 우회, WINDOWS_TESTING §3-c), 마우스 휠 1007 화살표 변환, win32-input-mode(`?9001h`).

### C. 설치·런타임 회귀(박스 또는 windows-latest CI)
- `pip install pywinpty`(x86-64 휠) 설치·import 성공.
- `_WinPty`가 진짜 ConPTY로 `cmd.exe` spawn → read/write/resize/terminate.
- `proc.py` Windows 실경로: `cmd /c`, `CREATE_NO_WINDOW`, `tasklist`, `taskkill /T`.
- `ipc.py` TCP 엔드포인트(`tcp:127.0.0.1:0`) 실 바인드/연결.
- Windows 경로·인코딩: `%LOCALAPPDATA%\pytmux`, 콘솔 cp949↔UTF-8 변환.
- **도구**: `py scripts/win_report.py --mb 5` 한 줄로 호환·성능 리포트. (CI 자동화는
  WINDOWS_TESTING §3의 `os-compat` 워크플로 참조.)
- **조사·수정(2026-06-09)**: 박스에서 `win_report.py` 를 돌리니 **헬스체크 도구 자체의
  버그 3개**가 드러났다(모두 박스에서만 노출 — 전체 ConPTY 스위트가 **실측 ~691s** 로
  느려야 보인다. 헤드리스/CI 의 빠른 스위트에선 안 보임). 제품은 정상(385 passed):
  ① 글로벌 watchdog `180s` < 스위트 시간 → suite 도중 win_report abort("행처럼" 보임).
  ② `run_tests` subprocess `timeout=600s` < 691s → healthy 스위트도 TimeoutExpired.
  ③ `subprocess.run(text=True)` 가 Windows 로케일(cp949)로 러너의 **UTF-8 한글 출력**을
     디코드 → `UnicodeDecodeError` 로 리더 스레드 사망 → `stdout=None` → 크래시.
  **수정**: watchdog 180→1500, run_tests 600→1200(run_tests<watchdog 순서로 우아),
  `encoding="utf-8", errors="replace"` 명시. → win_report 가 박스에서 끝까지 완주.

### D. 실 Claude Code 화면 REC 캡처(골든 픽스처용)
- 캡처 기본 ON(CL 57060~): 박스에서 pytmux를 띄우면 모든 패널 raw PTY 출력을
  `captures/<sock-id>/pane-<id>.log`에 무손실 기록(매핑 `sessions.log`).
- 실 Claude limit/busy/idle/ctx/footer 화면을 떠서 `python pytmux.py replay <log>
  --cols N --rows M [--ruler]`로 복원 → 감지 정확도·모델힌트·골든 픽스처의 객관 근거.
  **Mac엔 실 Claude+ConPTY 조합이 없어 이 캡처를 못 만든다.**
- **유의**: `captures/`는 `.gitignore`로 GitHub 미러 차단(민감 화면)·`.p4ignore` 비차단.
  submit은 실 터미널 내용(비밀·경로 가능)이라 **검토 후**. 무손실이라 무한 증가 → 정리 필요.

### E. 기능 열화 폴백 실확인(WINDOWS_PORT §4)
코드는 크래시 없이 폴백하도록 가드됨 — **실 Windows에서 폴백이 실제로 우아한지** 확인:
- 패널 cwd 상속 없음(`_pane_cwd` None → 서버 cwd에서 셸 시작).
- fg 명령 탭 자동이름·ssh 감지 없음(`_fg_command` None → 고정 탭이름).
- 시그널 의미 차이(SIGHUP/SIGKILL → `taskkill /T`)로 graceful 종료 동작 차이.
- **검증(2026-06-09)**: 박스에서 헤드리스 스위트 **385 passed, 0 failed** — 위 폴백들은
  전용 가드 테스트(`test_fg_command_guarded_on_windows`·`test_render_only_resize_without_fcntl`·
  `test_protocol_imports_without_fcntl_termios`·`test_winpty_backpressure_gate`·
  `test_proc.test_terminate_bogus_pid_noop`)로 커버되고 전 스위트가 박스에서 크래시 없이
  통과 → **우아한 폴백 확인됨**. (cwd 상속 폴백은 무해 동작이라 별도 단언 없음.)

## 4. 박스에서도 **못 하는** 것(사람·실기 필요)

- **OS 픽셀 스크린샷 차단**: 비대화형 세션에선 OS 픽셀 캡처가 막혀 있다. `run-pytmux`
  드라이버의 **텍스트 스크린샷**(TCP 루프백)은 되지만 픽셀 비교는 박스에 **사람이
  앉아** 실 콘솔에서 봐야 한다.
- **실제 마우스 드래그/호버**(탭→패널 DnD, footer 클릭존 호버) — 헤드리스 주입으론
  좌표 흐름·미리보기 룩을 끝까지 검증 못 함. 라이브 attach + 사람 확인 필요.

## 5. 작업 분담·Perforce 규칙

- **위 §3 항목 = office(Windows) 전담.** §3 외(코드·로직·문서·헤드리스 테스트)는 어느
  머신이든 가능 — 단 **같은 파일 동시 편집 시 충돌**한다(이 저장소는 두 세션이 같은
  depot 공유).
- **절대 디폴트 changelist에 두지 말 것** — 항상 전용 numbered CL로 edit/add/submit.
  공유 `office` 클라이언트에서 파일을 디폴트 CL에 두면 다른 세션의 submit에 휩쓸린다
  (실제 사고: CL 57695 혼입 → 57696 되돌림 → 57707 재제출).
- **머신 간 충돌 resolve는 그 파일을 연 클라이언트에서** — 한쪽이 다른 쪽의 pending CL을
  대신 resolve할 수 없다(워크스페이스 root가 다른 머신). submit 시 `p4 resolve -am`
  (안전 자동병합, 양쪽 보존)으로 풀고, `-at`/`-ay`(한쪽 덮어쓰기)는 쓰지 않는다.
