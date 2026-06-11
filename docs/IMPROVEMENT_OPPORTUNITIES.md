# pytmux 개선 기회 — 전체 코드 리뷰 (2026-06-06)

> 📦 **플러그인 이전 메모(CL 57812, 리뷰 이후)**: 아래 항목의 `serverclaude.py:NNN` 참조는 현재
> `pytmuxlib/plugins/claude-code/servermixin.py`(`ServerClaudeMixin`)다 — 위치만 이전(줄번호 드리프트,
> 심볼명 grep). 또한 **§4.2(`_scan_claude` 의 `screen.display` per-cell wcwidth)는 해결됨** —
> servermixin 의 경량 `screen_text()`(셀 data join, ~2.6× 빠름)로 대체. 참고: [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md) §4.

> 제품 목표 관점에서 전체 코드(~12k LOC)를 5개 차원으로 병렬 리뷰해 도출한 개선
> 기회 목록. 목표: **Windows/macOS/Linux** 에서 단일 세션·멀티탭·멀티패널을
> **명령어·메뉴·마우스**로 현대적으로 지원 + **Claude Code 토큰 추적/절감 자동화** +
> **tmux 수준 속도**.
>
> 표기: 심각도 **H/M/L**. `파일:라인` 은 리뷰 시점 기준. **[검증됨]** = 작성자가 코드로
> 직접 확인. 그 외 일부는 **추정**(실 OS 박스·런타임 필요)으로 표시. 성능 레버 중복은
> [PERFORMANCE_SCENARIO.md](PERFORMANCE_SCENARIO.md) §8 과 대조해 신규분만 수록.

---

## 진행 현황 (2026-06-06, 영향도 낮은 것부터 자율 구현)

코드 영향도가 낮은 항목부터 7개 마일스톤으로 구현·제출했다(각 단계 `tests/run.py`
통과 후 git push + p4 번호 체인지리스트 submit). **249 passed**.

| 마일스톤 | 항목 | 상태 | CL |
|---|---|---|---|
| M1 | #3 split-window -h/-v tmux 정합, #34 _first_int 음수 | ✅ | 56961 |
| M2 | #1 read_msg MAX_FRAME 상한·비-JSON 방어 | ✅ | 56963 |
| M3 | #11 _scan_claude 경량 추출(2.6×), #25 _feed_drain O(n²) 제거 | ✅ | 56965 |
| M4 | #36 on/off 헬퍼, #30 재접속 통합·상수 일원화 | ✅ | 56966 |
| M5 | #7 와이어 프로토콜 버전 협상 | ✅ | 56969 |
| M6 | #6 자동재개 발화 게이트, #5 /clear 토큰 세션 리셋 | ✅ | 56971 |
| M7 | #17 resize-pane 방향 명령, #10 마우스 발견성 | ✅ | 56973 |
| M8~M13 | Claude 토큰 절감 자동화(골든·잔량파서·예산·잔량정리·예약취소·plan유도) | ✅ | 56986~57024 |
| B | #28 except 좁힘, #5.8 테스트, #8 dirty행 재직렬화, #12 1/N, #5.9 | ✅ | 56995~57024 |
| M14a | 정리 빈도 상한(time floor `claude_ctx_min_interval`) | ✅ | 57032 |
| M14b | 무장 자동액션 카운트다운/취소 힌트 UI(`claude_pending`) | ✅ | 57040 |
| #12 2/N | 시계/달력 오버레이 → `clientrender` 자유함수 추출 | ✅ | 57046 |
| §2.4 | copy-mode 선택 패널 경계 클램프(복사 오염 제거) | ✅ | 57048 |
| §4.5 | prompt history 변경 시에만 전송(ssh 트래픽↓) | ✅ | 57051 |
| §4.4 | feed ESC-없는 플레인 텍스트 빠른 경로 | ✅ | 57052 |

**보류(사유 명시 — 후속 별도 진행)**:
- ~~**#2 IPC 인증 토큰**(H)~~ → **해결**(보안 감사 시리즈 CL 57238~57283, F1):
  서버가 listen 전에 `secrets.token_hex(32)` 를 0600 토큰 파일로 게시, 클라가 읽어
  hello/control 에 실어 보내고 `handle_client` 가 `hmac.compare_digest` 상수시간
  비교로 검증(불일치=auth_failed 거절, Windows TCP 루프백 포함) —
  [SECURITY_REVIEW.md](SECURITY_REVIEW.md) F1 "✅적용". 보류 사유였던 구클라
  락아웃은 토큰 파일 자동 게시/재읽기로 해소됨. (2026-06-11 코드 검증으로 stale 정정
  — 실 Windows 박스 동작 확인만 Windows 계열 #1.x 에 잔존.)
- ~~**#8 서버 render-후-폐기 → dirty 행 재직렬화**(H)~~ → **해결**(B 묶음 CL
  56995~57024 — `model.render()` 가 라이브 뷰·검색 비활성·캐시 유효 시 `screen.dirty`
  행만 재직렬화, 스크롤/검색/리사이즈/alt 전환은 전체 폴백. §4.1 도 같은 건으로 해소).
  한때 이 보류 목록과 진행표 ✅ 가 모순돼 있었음(2026-06-11 코드 검증으로 정정).
- ~~**#12 build_client_app 분할**(H)~~ → **명시된 추출 전부 완료**: **1/N**(`clientrender.
  put_cell`·`clientclip`) ✅ · **2/N**(시계/달력 오버레이) ✅ · **3/N Claude 렌더**
  (`_draw_claude_headers`·footer 존) ✅ — 플러그인 추출 Phase 2c(CL 57899)에서
  `plugins/claude-code/clientrender.py` 로 이전(코어는 `client_render` 훅만).
  client.py 추가 분할(~2.9k 줄)은 필요 시 별도 발의.
- ~~**#28 광역 except 좁히기**(M)~~ → **해결**(2단계): 서버 핵심 경로(dispatch/
  scan/send_full/handle_client 등)는 B 묶음(56995~57024)에서 `_log_error` 와 함께
  좁혀졌고, 잔여분(토큰 영속층 connect/import/snapshot/seed·usage 프로브·
  code_version·servertree ps 프로브 좁힘)은 2026-06-11 마무리 CL 에서 처리 —
  반복 실패 경로는 첫 실패만 기록(스팸 가드). 남은 broad except 는 전부 '의도된
  best-effort'(치명 로깅 자체·시그널 로그·pyte wrap 태그 등 — 주석으로 사유 명시).
- **#9/#21~24/#22**(M, Claude): 실 Claude 화면 캡처·로그 포맷 마이그레이션 필요.
  (~~컨텍스트 잔량% 기반 절감 트리거가 가장 큰 자동화 공백~~ → M11 `claude_ctx_autoclear`
  +`claude_ctx_threshold` 로 구현 완료 — 이 문장은 stale 이었음, 2026-06-11 정정.)
- ~~**Windows 계열 #1.1~#1.5**(H/M): 실 Windows 박스 검증 필요~~ → **전부 구현 완료**
  (2026-06-11, office 박스): #1.1 직접 소유 ConPTY raw-바이트 백엔드(CL 58214, opt-in
  `PYTMUX_PTY_BACKEND=owned`·pywinpty 폴백; 멀티바이트 플러드 왕복만 실 박스 라이브 검증
  대기) · #1.2/#1.3 terminate 에스컬레이트/is_alive CSV 정확대조(CL 58154/58156) · #1.4
  ssh 중첩 거부 .cmd 래퍼(CL 58216) · #1.5 owned 리더 FEED_SLICE 청크(CL 58219) · #1.6
  회귀 가드(헤드리스 수명 + 라이브 validate_conpty.py). 각 항목 §1.x 참조.

> 아래 §1~§5 는 원본 리뷰 결과(변경 없음). 각 항목 옆 상태는 위 표를 참조.

---

## 0. 즉시 착수 권고 (효과 대비 위험·노력)

| # | 항목 | 차원 | 심각도 | 노력 | 근거 |
|---|---|---|---|---|---|
| 1 | `read_msg` 프레임 길이 상한(MAX_FRAME) | 견고성·보안 | **H** | 소 | §5.1 [검증됨] |
| 2 | IPC 인증 토큰(특히 Windows TCP 무방비) | 보안 | **H** | 중 | §5.2 [검증됨] |
| 3 | `split-window -h/-v` 방향 tmux 정합 | UX | **H** | 소 | §2.1 [검증됨] |
| 4 | Windows 출력 bytes→str→bytes 이중변환 제거 | 크로스플랫폼·속도 | **H** | 중 | §1.1 [검증됨] |
| 5 | `/clear` 시 토큰 누계·세션경계 리셋 | Claude | **H** | 소 | §3.1 |
| 6 | 자동재개/auto-doc-clear false-trigger 게이트 | Claude | **H** | 소 | §3.2 §3.3 |
| 7 | 와이어 프로토콜 버전 협상 | 견고성 | **H** | 소 | §5.3 [검증됨] |
| 8 | 서버 render-후-폐기 → dirty 행만 재직렬화 | 속도 | **H** | 중 | §4.1 |

> 1·3·5·7 은 변경이 작고 위험이 낮은 **quick win**. 2·4·8 은 중간 노력. Claude 자동화
> 정확도(5·6)는 절감 전략을 켤수록 오차/오작동이 커지는 구조라 우선순위가 높다.

---

## 1. 크로스플랫폼 (Windows/macOS/Linux)

### [H] 1.1 Windows 출력 스트림 bytes→str→bytes 이중 트랜스코드 + 경계 손상 — `pty_backend.py:354-360,399` [검증됨]
리더 스레드가 `proc.read()`(pywinpty 가 이미 내부 utf-8 디코드한 **str**)를 받아
`encode("utf-8","replace")` 로 **재인코딩**한다. write 도 `data.decode("utf-8","replace")`.
① 전 출력 스트림을 두 번 트랜스코드(throughput 비용), ② 멀티바이트가 read 경계
(64KB)에 걸리면 `replace` 로 **U+FFFD 영구 손상**, ③ write 의 `decode` 가 비-UTF-8
페이스트(CP949·바이너리)를 손상. 문서가 가장 신경 쓰는 Windows→ssh 핫패스.
**개선**: pywinpty 저수준 바이트 API(`winpty.PTY`)로 디코드 우회 + 미완결 멀티바이트
carry 버퍼. 불가 시 read 측 incremental UTF-8 디코더. **효과**: CJK/이모지/바이너리
손상 제거 + 디코드 한 패스 절감. **위험**: 중(spawn/winsize/terminate 재매핑). Windows
박스 필요.

> **조사·실측 정정(2026-06-11, office 박스):** 이 항목의 ①은 저수준 `winpty.PTY` 전환
> (CL 56627 계열)으로 트랜스코드 홉이 줄어 부분 해소됐으나, **②(경계 손상)는 미해결이며
> 위 "개선"의 전제가 틀렸다**. 실태:
> - 손상은 우리 Python 층이 아니라 **pywinpty 가 래핑하는 winpty-rs 의 Rust `read()`**
>   안에서 난다. `ReadFile` 로 **최대 32768 바이트**를 읽어 `MultiByteToWideChar(CP_UTF8)`
>   로 디코드하는데 **read 경계를 넘는 미완결 멀티바이트를 carry 하지 않는다**(청크마다
>   독립 디코드). CJK/이모지가 32768B 경계에 걸리면 우리가 str 을 받기 전에 이미 U+FFFD 로
>   영구 손상 → 우리 층 carry 로 복구 불가.
> - **실측 재현**: cmd.exe 에 CJK 대량 출력. 리더를 멈추고 누적 후 드레인 시 348KB→**24개**
>   U+FFFD, **연속 읽기(미정지)에서도** 696KB→**50개**(max 청크 ~11k자 ≈ 33KB > 32768).
>   즉 producer 가 리더를 앞지르는 플러드(문서가 지목한 Windows→ssh→원격 Claude CJK 핫패스)
>   에서 backpressure 와 무관하게 발생. "연속 읽기" 같은 단순 완화는 무효.
> - **"개선"의 저수준 바이트 API 는 불가**: `winpty.PTY.read()` 는 str 전용이고, conout
>   읽기 핸들도 미노출(`PTY.fd` 는 **프로세스 핸들** — ReadFile 시 ERROR_INVALID_HANDLE).
>   고수준 `PtyProcess` 도 내부에서 `pty.read()`(이미 손상된 str)를 소켓에 넣어 carry 루프가
>   무의미. 서버 feed 경로(`pyte.ByteStream`)는 영속 incremental decoder 로 바이트 경계를
>   carry 하므로 **raw 바이트만 받으면 Unix 와 동일하게 무손상**임은 확인.
> - **진짜 해결책 = ConPTY 직접 소유**: ctypes `CreatePseudoConsole` + 자체 입출력 파이프로
>   raw 바이트를 읽어(서버 `pyte.ByteStream` 가 carry) winpty-rs 디코드를 우회. write 도
>   raw `WriteFile` 로 ③ 동시 해소. ~~익명 `CreatePipe` 로는 conhost 출력이 안 온다~~ 던 이전
>   결론은 **틀렸다**(런처 콘솔 상속 아티팩트) → **아래 ★해결 참조: 익명 파이프 + 표준 MS
>   레시피로 동작**. 안전망으로 신규 경로 실패 시 pywinpty 폴백(구현됨).

> **⚠️ 미해결 재확정(2026-06-11, 라이브 데몬 재검증) — owned ConPTY 는 동작하지 않는다:**
> 직전 "★ 해결(익명 파이프 + 콘솔 상속이 원인)" 결론은 **틀렸다**. 그때 본 "배너 180B
> 도달"은 *자기 콘솔을 가진 진단 프로세스*(`Start-Process -WindowStyle Hidden`)에서의 부분
> 현상이었고, **정작 제품이 도는 콘솔-less 데몬**(서버=DETACHED_PROCESS)에선 owned 백엔드가
> 패널을 백지로 만든다. 라이브 검증(데몬 spawn + run-pytmux 드라이버 텍스트 스크린샷, 그리고
> `_ConPty` 단독을 `spawn_detached` 로 격리 실행):
> - **pywinpty(기본)는 데몬에서 정상**: `echo` 출력·CJK 플러드 렌더 OK. 단 CJK 플러드는
>   여전히 **U+FFFD 발생**(②, 미해결) — pywinpty str 디코드 손상은 그대로.
> - **owned(`PYTMUX_PTY_BACKEND=owned`)는 데몬에서 자식 출력 0바이트** → 패널 백지. 그래서
>   기본 전환 불가.
> **근본 갭(파이프 종류 아님, 실측으로 단계별 규명):**
> 1. 익명 `CreatePipe`(MS 공식 샘플)·overlapped 명명 파이프(`CreateNamedPipe`+`CreateFile`+
>    overlapped Read/Write) **둘 다 conout 0바이트** — 파이프 종류는 원인이 아니다.
> 2. **`ResizePseudoConsole` 킥이 필수**: conhost 는 spawn 직후 출력을 한 글자도 안 내고,
>    첫 resize 신호를 받아야 **초기 화면 페인트 1회**(~118B: clear+blank+title)를 흘린다.
>    `_ConPty.spawn()` 끝에 크기 토글 킥을 넣어 이 1회는 받게 했다.
> 3. **그래도 불충분**: 킥 뒤에도 **이후 앱 출력(배너·echo·ping reply)이 계속 안 흐른다** —
>    초기 페인트 1회뿐. (cmd 는 별개로 stdin EOF 즉시 종료; ping 은 stdin 안 읽어 8초 생존해도
>    매초 reply 0바이트.) 이 **지속 스트리밍 미작동**이 잔여 블로커.
> 4. **결정적 대조**: pywinpty 의 ConPTY 백엔드(backend=0)는 같은 detached 조건에서 정상이며,
>    출력이 `\x1b[c`·`\x1b[?9001h`(win32-input-mode) 등 conhost 초기화 시퀀스로 시작한다(우리
>    시스템 conhost 출력엔 없다). winpty-rs 가 **패키지 번들 `conpty.dll`(MS OpenConsole)** 을
>    쓰는 반면 우리는 **이 빌드(22631)의 시스템 conhost** 를 호출하는 차이로 추정. 시스템
>    conhost 가 detached 에서 스트리밍을 못 하는 것으로 보인다.
> - **pywinpty raw 바이트 우회도 불가**: `PTY.read()` str 전용, conout 핸들 미노출(`PTY.fd`=0).
> **현재 코드 상태(이 CL)**: `conpty._ConPty` 를 overlapped 명명 파이프 + conin 배선 정정 +
> resize 킥까지 끌어올렸고(위 1~3 발견을 코드로 보존), 모듈 docstring 에 비동작 사실·잔여
> 블로커를 명기. owned 는 여전히 **opt-in·비동작**(폴백은 spawn 성공이라 안 걸림) — 켜지 말 것.
> **후보 (b) WinPTY agent 백엔드(backend=1) — 검토 완료·기각(2026-06-11):** pywinpty
> `PTY(cols,rows,backend=1)` 로 winpty-rs 의 WinPTY agent(번들 `winpty-agent.exe`+`winpty.dll`,
> 콘솔 화면버퍼 스크랩)를 켜 ConPTY(backend=0)와 CJK+이모지 플러드를 직접 대조(probe
> `_probe_winpty_backend.py`/`_probe_winpty_cfg.py`, 실 PowerShell 콘솔). 결과:
> | 백엔드 | CJK(소스 28만) | 이모지(소스 4만) | U+FFFD |
> |---|---|---|---|
> | ConPTY (현 기본) | ~28만 보존 | **4만 전부 보존** | ~34 (드문 32KB 경계) |
> | WinPTY agent | 11만~18만만 포착 | **0 — 전부 손실** | 이모지 4만≈전량 |
>
> WinPTY 는 32KB 경계 CJK 손상은 피하지만 (1) **아스트랄/이모지(서로게이트 쌍)를 100%
> U+FFFD 로 파괴**한다 — agent 가 `ReadConsoleOutputW` 로 셀당 WCHAR 1개만 읽어 서로게이트를
> 표현 못 하는 **레거시 콘솔버퍼 구조적 한계**(AgentConfig 3종 모두 동일, 설정 무관). (2)
> 화면버퍼 스크랩이라 대량 플러드의 스크롤백을 합쳐버려 **CJK 자체도 28만 중 11~18만만
> 포착**(바이트 스트림이 아닌 손실성 화면 모델). 즉 드문 CJK 경계 글리치 하나 피하려고
> 이모지 전손 + 대량출력 유실을 떠안는 **순(純) 열화** → 기본·opt-in 모두 부적합, 기각.
> **남은 진짜 후보(미착수, 큰 작업) = (a) 번들 OpenConsole 경로 재현**: 결정적 대조(위 4)대로
> winpty-rs 가 detached 에서 스트리밍에 성공하는 건 **패키지 번들 `conpty.dll`+`OpenConsole.exe`**
> (pywinpty 설치 디렉터리에 동봉 — 확인함) 를 쓰기 때문으로 추정. 우리 ctypes owned 경로가 시스템
> conhost 대신 이 번들 OpenConsole 을 띄우도록(예: `OpenConsole.exe` 를 ConPTY host 로 spawn)
> 재현하면 raw 바이트 스트리밍 + ①②③ 동시 해소 가능성. 검증 하네스는 **데몬 경유 라이브 테스트**
> 여야 함(헤드리스 `validate_conpty.py` 의 배너+echo 체크는 이 스트리밍 갭을 못 잡는다).

### [M] 1.2 Windows `terminate` 가 graceful 의미 부재 → 고아 ConPTY 셸 누수 — `proc.py` ✅ **해결(CL 58154/58156)**
~~`force=False` 에서도 `taskkill /PID /T`(/F 없음)라 콘솔 서브시스템·자식 셸이 응답 안 하면
트리를 못 내린다~~ → **해결**: `terminate(force=False)` 가 짧은 timeout 으로 graceful
`taskkill /T` 시도 후 `_win_wait_dead`(OpenProcess(SYNCHRONIZE)+WaitForSingleObject 로
grace 한 번에 대기)로 종료 확인, 아직 살아 있으면 `taskkill /F /T` 로 에스컬레이트해 트리를
확실히 내린다(창 없는 콘솔/분리 프로세스라 WM_CLOSE 가 안 먹는 고아 케이스 방지).
테스트 `test_win_terminate_escalates`. (CTRL_BREAK 대신 taskkill 에스컬레이트 — 분리/무콘솔
데몬엔 더 신뢰성 높음.)

### [M] 1.3 Windows `is_alive` 가 `tasklist` 부분문자열 매칭 오탐 — `proc.py` ✅ **해결(CL 58154/58156)**
~~`str(pid) in out` 라 pid 가 짧으면 메모리 수치 "4,096 K" 등에 부분일치해 오판~~ →
**해결**: `tasklist /FI "PID eq <pid>" /FO CSV /NH` 를 csv 파싱해 **2번째 컬럼(PID)이 정확히
일치**하는 행이 있을 때만 살아 있다고 본다(메모리 컬럼 부분일치 오탐 제거).
테스트 `test_win_is_alive_csv_exact_match`.

### [M] 1.4 ssh 중첩 거부 전파가 Windows 전면 미구현 — `sshwrap.py` ✅ **해결**
~~`ensure_wrapper_dir` 이 `os.name=="nt"` 면 `None`(래퍼는 POSIX sh)~~ → **해결**: Windows
에서 `ssh.cmd`/`autossh.cmd` 배치 래퍼를 PATH 앞단에 깐다. 래퍼는 `%~$PATH:E`(확장자 .exe
만 검색)로 진짜 `ssh.exe` 를 찾아(우리 dir 엔 .cmd 만 있어 자기 자신을 안 잡음 — PATH 에서
자기 dir 제거 불필요) `ssh.exe -o SendEnv=LC_PYTMUX %*` 로 exec 한다. 명령 해석이 PATH dir
순서를 먼저 따르므로 앞단 .cmd 가 진짜 ssh.exe 를 가린다(cmd.exe·PowerShell 패널 공통).
원격 전파는 POSIX 와 동일하게 sshd AcceptEnv 에 달림(우아한 열화). 실 박스에서 `ssh.cmd -V`
→ `ssh.exe -o SendEnv=LC_PYTMUX -V` 동작 확인. 테스트 `test_sshwrap_windows_cmd_wrapper`.

### [M] 1.5 Windows 백프레셔가 in-flight read 1건을 못 막음 — `pty_backend.py` ✅ **해결(owned 백엔드)**
~~`pause_reader` 가 *다음* read 만 막아, 진행 중인 64KB read 가 드레인 중 끼어든다~~ →
**해결**: 직접 소유 ConPTY 리더(`_OwnedConPty._read_loop`)가 한 read 를 `DEFAULT_READ`(64KB)
대신 **`FEED_SLICE`(8KB) 단위**로 끊어 읽는다(`_OWNED_READ_CHUNK`). pause 게이트를 read
*전에* 확인하므로 in-flight 로 비집고 드는 양이 최대 8KB(64KB 아님)라 pause 가 더 빨리
듣고, 전달 청크가 서버 인라인 처리 한계 이하라 64KB 드레인 태스크 없이 곧장 ingest 된다.
테스트 `test_owned_conpty_reads_in_feed_slice_chunks`. (pywinpty `_WinPty` 는 `pty.read`
반환 크기를 우리가 못 정해 기존 게이트 유지 — owned 백엔드 기본 전환 시 함께 개선됨.)

### [L] 1.6 인터랙티브·ConPTY 회귀 가드 부재 — `docs/WINDOWS_TESTING.md` ✅ **대부분 해결**
~~ConPTY 회귀 가드 없음~~ → 다층 가드 도입:
- **헤드리스 ConPTY 수명 가드**: `test_owned_conpty_lifecycle_windows`(직접 소유 ConPTY
  spawn→resize→terminate→close→reap, windows-latest CI 에서 실행) — 의사콘솔 생성·자식
  attach·정리 회귀를 자동 적발. 선택 분기·env 블록·지원 판정도 단위 커버.
- **멀티바이트 경계 왕복(라이브)**: `scripts/validate_conpty.py`(+ 실 제품) 가 CJK 플러드를
  64KB read 경계에 걸쳐 받아 U+FFFD 0개를 assert — 실 콘솔 필요라 라이브 검증
  (docs/WINDOWS_TESTING.md §4-d). owned 백엔드는 raw 바이트라 구조적으로 무손상.
- **간헐 레이스 `test_sync_input_broadcast`**: **해결(CL 58040)** — TEST-SEARCH 버그(폭 38
  패널 하드랩으로 echo "SYNCED" 검색 깨짐)였고 ConPTY/리더/spawn 문제 아니었음. de-wrap
  검색 + Windows un-skip.
- **남음**: 라이브 attach(새 콘솔 창)·실 ssh 반응성·키 인코딩 인터랙티브 검증은 여전히 실
  박스 수동(§4), **arm64 Windows pywinpty 휠 부재** 대비(소스 빌드 필요 — §4-b).

### 의도된 기능 열화(공백) — #7 대부분 해결
- ~~자동 탭이름/ssh 감지~~ → **해결(#7)**: `_fg_command(pane)` 이 Windows 에서
  `proc.foreground_command(pane.child_pid)` 로 셸 자손 프로세스 트리의 가장 깊은 자손을
  추정한다(ConPTY 엔 fg pgrp 가 없음). idle 이면 셸 이름, `ssh`/`python` 등 실행 시 그 이름
  → 자동 탭이름·`_REMOTE_CMDS` 원격 감지 동작. 실측: cmd→ping 자손 → `PING` 반환.
  테스트 `test_foreground_command`·`test_fg_command_windows_uses_process_tree`.
- ~~패널 cwd 상속(Windows 항상 None)~~ → **이미 해결**: `_pane_cwd` 가 Windows 에서
  `proc.process_cwd`(PEB 읽기)로 cwd 를 구한다(ncd·default-path=current 동작).
- **작업보존 re-exec 재시작**(`serverpersist.py`): ConPTY 핸들 비상속이라 Windows 무중단
  재시작은 여전히 미지원 — **#1.1 직접 소유 ConPTY 의 핸들 상속/adopt 설계에 의존**하므로
  이번 범위 밖(사용자 결정: #1.1 비의존 항목만). 후속 과제.
- `record()` 녹화(`replay.py`): POSIX pty/termios/tty/select 의존 **개발 진단 도구**라
  Windows 는 의도적으로 미지원(명확한 메시지로 폴백, exit 2). 인터랙티브 호스트 I/O
  패스스루가 필요해 헤드리스 검증 불가 + 제품 가치 낮아 보류(replay 재생은 Windows 동작).

---

## 2. 멀티탭/패널 UX (명령어·메뉴·마우스)

### [H] 2.1 `split-window -h/-v` 방향이 tmux 와 정반대 + 코드 내 모순 — `client.py:2123`, `clientutil.py:409` [검증됨]
`orient = "tb" if "-h" in args ...` → `-h`→상하, `-v`→좌우. tmux 는 `-h`=좌우(side-by-side).
prefix `%`→`lr`·`"`→`tb`(2584)는 tmux 와 일치하므로 **키와 명령/팔레트가 정반대로 동작**.
게다가 `join_pane`(2214)은 `-h`→`lr` 로 **같은 코드베이스에서 `-h` 의미가 모순**.
`COMMAND_OPTIONS["split-window"]` 라벨("가로 분할 -h" → 실제 상하)도 굳어 있음.
**개선**: `-h`→`lr`, `-v`→`tb` 로 정정 + 라벨/FEATURES 통일(한 릴리스 병기 가능).
**위험**: 기존 동작 익숙 사용자 변화(문서 안내로 흡수).

### [H] 2.2 강력한 마우스 기능(패널 swap·탭 드래그)이 구현됐으나 발견 불가 — `clientwidgets.py:180-356,636-704`, `FEATURES.md` 9절
Shift+드래그 패널 swap, 탭 드래그 재정렬, 탭→패널 드래그 분할이 **이미 구현**됐는데
FEATURES.md 는 "향후"로 적고 `?`도움말·메뉴·상태줄 어디에도 힌트가 없다. 마우스 1급
지원이라는 차별화 기능이 사장. **개선**: 문서 갱신 + 메뉴/help/ESC 상태줄에 드래그 힌트.
**위험**: 낮음.

### [M] 2.3 리사이즈·임의 swap 이 마우스 전용 (경로 비대칭) — `client.py:2195-2197,2610`
`resize-pane` 명령이 `-Z`(줌)만 처리하고 `-L/-R/-U/-D [N]` 무시 → 키/명령으로 분할선
정밀 이동 불가(마우스 divider 드래그만). 임의 두 패널 swap 도 Shift+드래그만.
**개선**: `resize-pane -L/R/U/D` → 서버 `resize_dir` 매핑, `swap-pane -s/-t` 또는
display-panes 번호 기반. **위험**: 낮음.

### [M] 2.4 copy-mode 드래그 선택이 패널 경계 무시 → 복사 오염 — `clientwidgets.py:39-55,173-179`
선택이 전역 좌표라 분할 패널을 가로질러 테두리·인접 패널까지 복사. **개선**: 시작 패널
rect 로 클램프 + 추출 시 그 열 범위만. **위험**: 낮음.

### [M] 2.5 설정 키 표현력 제한 — `keymap.py:10-23`
`_tmux_key_to_textual` 이 `C-<letter>` 만 변환(`M-`/`S-`/F1–F12 미지원), prefix 폴백이
실패 시 무조건 `\x02`. config `bind` 는 prefix-후-단일키만(`-n` root table 없음).
**개선**: `M-`/`S-`/펑션키 파싱 + 잘못된 키 경고. **위험**: 낮음(조용한 무시 → 경고화).

### [M] 2.6 줌 중 비활성 패널 winsize 정지로 reflow 깨짐 가능 — `model.py:827-831`, `serverio.py:29-55` (추정) — **보류(분석 후)**
`compute_layout` 이 줌 시 활성 패널만 리스트에 넣어(`model.py:827-831`) `_layout_msg`
가 그 한 패널만 `p.resize()` 한다 → 비활성 패널은 줌 진입 직전 크기를 유지한다.
**검증(2026-06-06)**: 메커니즘은 사실이나 영향은 **좁다** — ① 줌 해제 시 전 패널
`_layout` 으로 현 창 크기에 맞게 리사이즈되어 자동 교정되고, ② 줌 중 비활성 패널은
화면에 안 보이며 자기 pyte/자식 크기와 자기정합(SIGWINCH 미발생이라 자식도 옛 크기로
출력)이다. 실제 깨짐은 **"줌 중 창 리사이즈 + 비활성 패널 출력 + 줌 해제"** 조합에서만
배경 reflow 로 나타나는 드문 경계다. **수정 보류 사유**: 제안 수정(줌 중 숨은 패널도
정상 분할 크기로 강제 resize)은 테두리·헤더 content-rect 계산을 가장 많이 테스트되는
레이아웃/리사이즈 핫패스(`_layout_msg`)에 중복·삽입해야 해 미검증 경계 대비 회귀
위험(리사이즈 루프·배경 SIGWINCH 폭주)이 크다. 실 재현 픽스처가 확보되면 content-rect
헬퍼를 먼저 추출해 displayed/hidden 양쪽에 적용하는 방식으로 진행 권장.

### [L] 2.7 컨텍스트 메뉴가 명령 대비 빈약 — `clientutil.py:270-290`
swap/rotate/break/join/layout-preset/clock·calendar/검색이 메뉴 부재("모든 동작 메뉴
노출" 목표와 어긋남). **개선**: 계층 서브메뉴 또는 COMMANDS 테이블 자동생성.

### [L] 2.8 인덱스 명령 음수 인자 침묵 실패 — `client.py:2017-2024`
`_first_int` 가 첫 `-N` 토큰에서 `None` 반환 → `move-tab -2` 무시. **개선**: 음수=끝에서
N번째 또는 명시 거부. (그 외 L: choose-tree 검색·썸네일 부재, Ctrl+Click↔우클릭 구분
불가, ESC 디바운스 트레이드오프 — 모두 발견성/접근성 개선 여지.)

---

## 3. Claude Code 토큰 추적 + 절감 자동화

### [H] 3.1 `/clear` 시 토큰 누계·세션경계가 안 끊겨 이중계산/유실 — `tokens.py:59-84`, `serverclaude.py:412,431`
`tokens.step()` 은 busy→idle 경계로만 peak 확정. 프롬프트 단위 클리어·auto-doc-clear 가
`/clear` 를 주입해도 `_tok_state`·`_claude_session_id` 는 리셋 안 됨 → doc 작성·`/clear`
자체 토큰이 사용자 누계에 합산되고 세션 경계가 컨텍스트 경계와 어긋남. **절감 전략을
켤수록 추적 오차가 커지는 구조적 충돌.** **개선**: `_pc_advance`/clear 주입 지점에서
`tokens.reset` + session seq 증가, 자동 주입 토큰은 `_auto` 태그 분리. **위험**: 낮음.

### [H] 3.2 limit 문구가 미검증 휴리스틱(오탐·미탐 양방향) — `claude.py:46-48,226-232`
`"limit" in low AND any(reset/again/...)` 뿐. 실제 리밋 화면 캡처 검증 흔적 없음(추정).
미탐 시 자동재개 미작동, 오탐 시 사용자가 "rate limit" 을 프롬프트에 치면 limit 으로
오판해 엉뚱한 `continue` 주입. **개선**: 실제 리밋 텍스트 샘플로 정규식 테스트 픽스처화,
신호를 footer 영역/구(phrase) 단위로 한정. **위험**: 좁히면 미탐↑ → 샘플 검증 필수.

### [H] 3.3 자동재개·auto-doc-clear false-trigger(작업 방해/파괴) — `serverclaude.py:43-51,237-252`, `serverpty.py:191`
① `_maybe_schedule_resume` 가 한 번 예약한 `continue` 주입을 **취소하는 경로가 없어**
사용자가 이미 재개한 작업 중간에 끼어든다. ② `parse_reset_delay` 가 화면 아무 곳의
시각 숫자를 잡아 엉뚱한 delay. ③ auto-doc-clear 의 idle 30초 발화가 "사용자가 읽는 중"
을 방해로 오인해 **되돌릴 수 없는 `/clear`** 실행 위험. **개선**: 발화 직전 상태 재확인
(여전히 limit/idle 인지) + busy 복귀 시 `call_later` cancel + 최근 입력 후 X초 미만이면
연기 + 헤더 카운트다운/취소 힌트. **위험**: 낮음(게이트 추가).

### [M] 3.4 busy/idle 상태머신 오탐 — `claude.py:34-58`
`↑/↓ N tokens` 잔재가 idle 인데 busy 로 잡혀 완료알림/doc 지연, 좁은 폭에서 스피너
잘리면 busy 미탐 → 작업 중 doc/clear 주입. **개선**: busy 신호를 "(Ns" 시간표시·"still
thinking" 으로 좁히고 `↑/↓ tokens` 는 토큰 누계 전용, busy 쪽에도 hysteresis.

### [M] 3.5 로깅 집계 어긋남 — `usagelog.py`/`usagedb.py`/`servermixin.py` — **②③ 해결**
세 하위버그였다: ① 로컬타임 버킷(DST/tz 변경 시 이중/누락), ② session id 가 서버 내
시퀀스라 재시작 후 재등장, ③ account 가 프레임마다 갱신돼 한 응답이 엉뚱한 계정으로.
- **② 해결**: `_claude_session_seq` 가 부팅마다 0(코어 `server.py`)이라 재시작 후
  새 세션이 1,2,… 로 재발급돼 영속 DB 의 같은 id 옛 세션과 [패널] 세션 차원 집계에서
  병합됐다. 첫 세션 부여 직전 `usagedb.max_session()` 으로 1회 시드(`_seed_session_seq`/
  `_next_claude_session_id`)해 새 id 가 항상 옛 id 보다 크게 했다(`test_session_seq_*`).
- **③ 해결**: account 래치를 매 프레임 last-seen → **세션 first-seen 고정**(처음 검출된
  신뢰 계정만 래치, 이후 프레임의 다른/오검출 계정으로 안 덮음 — 한 Claude 프로세스
  =한 계정). 응답 종료 후 화면에 우연히 뜬 계정 라벨이 확정 토큰을 재귀속하던 경로
  차단(`test_account_first_seen_latched_not_overwritten`).
- **① 잔존(후속)**: 로컬타임 버킷의 DST/여행 시 과거기록 재분류는 미해결 — 제대로
  고치려면 쓰기 시점의 tz offset 을 레코드에 고정(v4 스키마 마이그레이션)해야 한다.
  영향이 좁아(DST 연 2회·tz 변경) 별도 후속으로 둔다. **개선**: make_record 에 tzoff
  추가, bucket_key 가 저장 offset 적용(레거시 None 은 시스템 로컬 폴백).

### [M] 3.6 라이브 계정합 vs 영속 집계 불일치 — `serverclaude.py:549-562`
상태줄 "계정별 Σ" 는 살아있는 패널 합(account 미정·종료 세션 제외)인데 영속 로그와
의미가 달라 혼동. **개선**: UI 출처 구분 표기 + account 확정 전 committed 버퍼링/후정정.

### [M] 3.7 토큰 추적이 CLI 단일 포맷에 강결합(silent failure) — `claude.py`/`servermixin.py` — **가시화 해결**
스피너 글리프·"context N%"·org/plan 정규식이 현행 포맷 가정 → 포맷 변경 시 상태머신
전체가 조용히 멈춤. running 급감 휴리스틱(`peak - max(50,peak//2)`)도 짧은 연속 응답을
합치거나 과민하게 끊음.
- **가시화 해결**: 파서와 **무관한** ground-truth(포그라운드 프로세스 명령행에
  'claude' 가 있는지 — `_fg_is_claude`, comm 이 'node'여도 명령행으로 식별)로 "Claude
  실행 중인데 `claude_state` 가 None" 인 상태가 `_FMT_UNKNOWN_SEC`(20s) 지속되면 상태줄
  ⚠ "포맷 미인식 — 추적 중단" 경고를 세우고 error.log 1회 기록(`_update_fmt_unknown`,
  `fmt_unknown_update` 순수 전이). fg 검사(ps)는 인식 실패 패널에 한해 `_FMT_CHECK_
  INTERVAL`(5s) throttle. 경고 채널은 기존 `_claude_warn`(상태줄 ⚠ 세그먼트) 재사용.
  **한계**: 처음부터 인식 안 되는 **정적 idle** 패널은 출력이 없어 dirty 게이트로 스캔이
  건너뛰어져 미감지 — 추적이 실제 멈춰 손해 큰 **busy(출력 진행)** 구간이 주 대상.
- **잔존(후속)**: 응답 경계 휴리스틱(busy 재시작 엣지 1차 + 급감 보조)·실제 footer
  골든 픽스처 보강은 §3.4 와 함께 별도.

### 빠진 절감 전략 (가장 큰 자동화 공백)
> 상세 설계: [TOKEN_SAVING_SCENARIO.md](TOKEN_SAVING_SCENARIO.md) — 개입 표면·시나리오
> 분류·전략별 안전 자동화·단계적 로드맵(M8~M14).

- **컨텍스트 잔량 기반 트리거 없음**: `claude_usage` 가 이미 "ctx N%"/auto-compact% 를
  파싱하는데 발화 조건에 안 씀(시간 기반 30초만). "잔량<X% 면 정리"가 가장 효과적인데
  미구현 — **최우선 자동화 기회.** (시나리오 §4 T1)
- **예산/임계 알림 없음**: usagelog 일/주/월 집계는 있으나 예산 초과 경고가 없어 수동
  조회에 머묾. **모델/권한모드 절감**(plan 강제·Haiku 유도)도 perm-mode auto 외 없음.
  (시나리오 §4 T2·T3)

---

## 4. 속도 (tmux 수준 — 기존 §8 레버와 비중복 신규분)

### [H] 4.1 서버가 변경 패널 **전체 뷰포트를 render 한 뒤 델타에서 대부분 폐기** — `serverio.py:230`, `model.py:621,639-672`
플러시가 dirty 패널마다 `render()` 로 모든 행 segment 를 새로 만들고, `_screen_frame` 이
`_sent_rows` 와 비교해 바뀐 행만 보내고 나머지를 버린다. `render()` 는 pyte `screen.dirty`
를 전혀 참조 안 함 → alt 풀리페인트에서 1줄만 바뀌어도 24행 전부 재직렬화. **개선**:
`render()` 가 행 캐시 + `screen.dirty` 행만 재직렬화, 스크롤/검색 중엔 full 폴백.
**효과**: busy render CPU 가 변경행 비율만큼(잠재 ~24×). **위험**: 중(dirty 클리어 시점
정확성, ptyshot 골든). **주의**: §8 의 B3 미구현 잔여분과 부분 중복 — 우선순위 상향분.

### [H] 4.2 `_scan_claude` 가 `screen.display`(셀당 wcwidth)로 텍스트 생성 — `serverclaude.py:370` [실측]
`screen.display` 는 셀마다 `wcwidth()` 호출(80×24 = 267µs vs `buffer[y][x].data` join
101µs, **2.6×**). 이 텍스트는 8~10개 정규식 입력으로만 쓰여 폭 보정이 무의미. busy 패널
에서 30Hz 핫패스. **개선**: 경량 텍스트 추출 헬퍼(`"".join(line[x].data or " ")`)로 대체.
**효과**: 스캔 텍스트 생성 ~60%↓. **위험**: 낮음(정규식 폭 무의존 골든 확인).

### [M] 4.3 `_feed_drain` 슬라이싱이 O(n²) 재복사 — `serverpty.py:141` [실측]
`chunk, buf = buf[:n], buf[n:]` 가 매 슬라이스 잔여 전체 복사(4MB backlog = 50.97ms vs
memoryview 0.062ms, **820×**). 평시 64KB 는 무해하나 backlog 누적 시 루프 점유. **개선**:
`memoryview`+오프셋, `_feedbuf` 를 `bytearray`. **위험**: 낮음.

### [M] 4.4 feed 전처리 정규식이 8KB 슬라이스마다 반복(8×) — `model.py:529`, `serverpty.py:169` (추정)
64KB read 를 8KB×8 로 쪼개 `feed()` 마다 `_altcarry+data` concat·`_PRIVATE_SGR_RE.sub`
(무매치도 전체순회)·sanitize·`_ALT_RE` 를 다시 돈다. **개선**: 전처리를 read 단위 1회로
끌어올리고 드레인은 정제 버퍼를 pyte feed 만 분할. **위험**: 중(alt 전환 경계 테스트).

### [M] 4.5 `_status_msg` 가 패널마다 `prompt_history[-30:]` 매 프레임 직렬화 — `serverio.py:110-114`
히스토리는 드물게 바뀌는데 토큰 변동만으로 전 패널 30개 프롬프트를 매 status 재전송
(ssh 트래픽). **개선**: history 를 변경 시에만 별도 메시지/해시 버전. **위험**: 낮~중.

### [L] 4.6 플러시 1프레임에 `panes()` 트리워크 반복 — `serverio.py:227,255,273` 외
한 프레임에 `~2+3T` 회 트리 DFS(트리는 split/kill 때만 변경). **개선**: 윈도우에 패널
리스트 캐시 + 트리 변경 시 무효화. (그 외 L: `_send_full` 이 B4 배치 미적용 `serverio.py:144`,
`read_msg` 의 `json.loads(payload.decode())` → `json.loads(payload)` 직접.)

---

## 5. 견고성·보안·유지보수

### [H] 5.1 `read_msg` 프레임 길이 무제한 → OOM(DoS) — `protocol.py:36-40`, `launcher.py:_recvn` [검증됨]
`length = int.from_bytes(header,"big")`(최대 4GiB) 후 상한 없이 `readexactly(length)`.
손상/악의 프레임 하나로 즉시 OOM. **개선**: `MAX_FRAME`(예 64MiB) 초과 시 연결 종료.
거대 length 헤더 graceful close 테스트. **위험**: 정상 메시지는 작아 회귀 없음.

### [H] 5.2 IPC 제어 명령 무인증 — Windows TCP 무방비 — `serverio.py:548-557`, `ipc.py:165-191` [검증됨]
첫 프레임이 `kill-server`/`control`(= `send-keys`·`restart-server`·`run-shell`)이면
**hello 없이 즉시 실행**. Unix 는 소켓 0o600 으로만 보호(동일 uid 통과), Windows 는
`127.0.0.1:<ephemeral>` 에 바인드만 하고 **토큰/인증이 없어** 같은 머신 임의 프로세스가
포트파일로 붙어 send-keys 키 주입(사실상 코드 실행)·화면 캡처·kill 가능. **개선**: 서버
기동 시 랜덤 시크릿을 사용자 한정 파일에 저장, hello/control 에서 검증. Unix 는
SO_PEERCRED uid 일치 권장. **위험**: 프로토콜 변경(§5.3 과 함께).

### [H] 5.3 와이어 프로토콜 버전 협상 부재 — `protocol.py`/`ipc.py` 전반 [검증됨]
프레이밍·스키마에 버전/매직 없음. 구·신 버전 클라↔서버 혼용 시 조용히 오작동/`json.loads`
깨짐. **개선**: hello 에 `proto:N`, 불일치 시 명시적 거절 + 메시지 키 셋 모듈 상수화
(HANDOFF §11.2 "명시적 계약"). **위험**: 낮음(hello 확장).

### [H] 5.4 `build_client_app` 단일 클로저 2921줄 — `client.py:30-2921`
`PytmuxApp` + ~120 중첩 메서드가 한 팩토리 안. 단위 테스트가 앱 부팅 필요, 머지 충돌면
거대. 클립보드(1473-1605)·clock/calendar 오버레이(865-1030)·Claude 렌더(558-728) 등
**app 상태 비의존 순수 함수**가 클로저에 갇힘. **개선**(HANDOFF §11.4-4): `clientclip.py`·
오버레이 자유함수·`client_claude.py` 로 단계 추출, 클로저엔 위임만. **위험**: 좌표 회귀
(ptyshot 골든 가드).

### [M] 5.5 광역 `except Exception: pass` 다수 — 조용한 실패 — serverio 7·pty_backend 6·client 11·serverpty 3
PTY/소켓 정리 경로가 무로그 삼킴 → fd 누수·좀비 파이프(§6 CLOEXEC 류)가 흔적 없이 발생.
**개선**: `OSError`/`ConnectionError` 로 좁히고 정리 경로에 디버그 로그(`_log_error` 재사용).

### [M] 5.6 execv 실패 폴백의 소켓/fd 누수·좀비 — `serverpersist.py:256-264`
`_do_execv` 실패 시 CLOEXEC 푼 master fd·listen 소켓·포트파일 정리가 불명확 → stale
소켓에 probe 성공해 새 기동 차단(좀비). 직렬화~execv 창의 입력 유실 레이스. **개선**:
폴백에서 소켓/포트파일 명시 정리, 직렬화를 콜백 안으로 옮겨 창 최소화. test_restart 가드.

### [M] 5.7 재접속 로직 2벌 중복 — `client.py:369-389,391-441`
`_reconnect`/`_force_reconnect` 가 거의 동일(300/150회×0.02s) 복붙 + 매직 상수가
launcher.wait_server 와 별개로 흩어짐. **개선**: `_resync_connect(retries,delay)` 통합 +
재시도 상수 모듈 일원화.

### [M] 5.8 테스트 커버리지 공백(추정)
재접속/degraded 히스테리시스(net_bad/good/recover 경계), execv 실패 폴백·손상 상태파일
복원, 프로토콜 견고성(거대 length·잘린 프레임·비-JSON·미지 타입)에 직접 단언이 얕음.
**개선**: 가짜 reader 로 RTT 주입해 degraded 전이 테스트, save→restore 라운드트립+손상
입력, 위 §5.1 DoS 테스트.

### [L] 5.9 기타
`handle_control` 19-branch elif + on/off 파싱 6중복 → dict 테이블+헬퍼(`server.py:292-376`);
ipc stale 소켓 TOCTOU(`ipc.py:184`); claude 정규식 ReDoS 회귀 테스트 명시; 흩어진 재시도
상수 일원화.

---

## 부록 — 리뷰 방법

5개 차원(크로스플랫폼·UX·Claude·속도·견고성)으로 병렬 리뷰 에이전트를 띄워 근거 기반
findings 를 수집한 뒤, H급 핵심(§1.1·§2.1·§5.1·§5.2·§5.3)은 작성자가 코드로 직접
[검증]했다. 속도 findings 일부는 마이크로벤치 실측치(µs)를 포함한다. 미검증 항목은
**추정**으로 표시했으며 실 OS 박스·런타임 측정이 필요하다. 성능 신규 레버는
[PERFORMANCE_SCENARIO.md](PERFORMANCE_SCENARIO.md) §8 과 대조해 중복분을 명시·제외했다.

> 강점(유지): claude.py 휴리스틱 격리, server.py 믹스인 분할, 연결 세대(`_conn_gen`)
> 기반 옛 reader 종료, per-client 화면 모델. 남은 부채는 주로 **경계 검증(프레임 길이·
> 버전·인증)**, **세 입력 경로 정합**, **Claude 자동화 정확도**, **거대 클로저 분할**이다.
