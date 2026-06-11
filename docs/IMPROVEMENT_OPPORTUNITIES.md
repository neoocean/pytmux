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
- **Windows 계열 #1.1~#1.5**(H/M): 실 Windows 박스 검증 필요(헤드리스 대리 불가).

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

### [M] 1.2 Windows `terminate` 가 graceful 의미 부재 → 고아 ConPTY 셸 누수 — `proc.py:148-164`
`force=False` 에서도 `taskkill /PID /T`(/F 없음)라 콘솔 서브시스템·자식 셸이 응답
안 하면 트리를 못 내린다. **개선**: `CREATE_NEW_PROCESS_GROUP` + `GenerateConsoleCtrlEvent
(CTRL_BREAK_EVENT)` → grace 후 `taskkill /F /T` 에스컬레이트. 셧다운 순서를 PTY
terminate→reap→데몬 순으로. **위험**: 중.

### [M] 1.3 Windows `is_alive` 가 `tasklist` 부분문자열 매칭 오탐 — `proc.py:123-145` (추정)
`str(pid) in out` 라 pid 가 짧으면(`4`) 메모리 수치 "4,096 K" 등에 부분일치해 죽은
서버를 살았다고 오판 가능. **개선**: `/FO CSV` 파싱 또는 ctypes `OpenProcess`+
`WaitForSingleObject(0)`. **위험**: 낮음.

### [M] 1.4 ssh 중첩 거부 전파가 Windows 전면 미구현 — `sshwrap.py:52-92` [검증됨]
`ensure_wrapper_dir` 이 `os.name=="nt"` 면 `None`(래퍼는 POSIX sh). Windows 패널의
ssh 워크플로에서 `LC_PYTMUX` SendEnv 전파가 없어 **원격 중첩 거부 미작동**(로컬
`$PYTMUX` 가드만). **개선**: `.cmd`/PowerShell 래퍼로 `ssh -o SendEnv=LC_PYTMUX` 주입.
최소한 미지원 명시. **위험**: 낮~중.

### [M] 1.5 Windows 백프레셔가 in-flight read 1건을 못 막음 — `pty_backend.py:387-396` (추정)
`pause_reader` 가 *다음* read 만 막아, 진행 중인 64KB read(메인스레드 ~50ms 점유)가
드레인 중 끼어든다. POSIX `remove_reader` 대비 약함. **개선**: 리더가 read 를 FEED_SLICE
(8KB) 청크로 끊어 슬라이스마다 게이트 재확인. **위험**: 낮음.

### [L] 1.6 인터랙티브·ConPTY 회귀 가드 부재 — `docs/WINDOWS_TESTING.md`
CI 는 헤드리스 + `pywinpty import/spawn` 만. 라이브 attach·실 ssh 반응성·키 인코딩은
"사람이 실 박스에서". 실 Windows 검증은 2026-06-04 1머신 1회뿐, ConPTY 회귀 가드 없음.
간헐 레이스 `test_sync_input_broadcast` 미해결. **개선**: ConPTY 통합 스모크(멀티바이트
경계 왕복 assert) + 비결정 테스트 `xfail` 가시화 + arm64 휠 대비.

### 의도된 기능 열화(공백, 추후 채울 것)
자동 탭이름/ssh 감지(`servertree.py:462`), 패널 cwd 상속(`servertree.py:176`, Windows 항상
None), **작업보존 re-exec 재시작**(`serverpersist.py:229`, ConPTY 핸들 비상속이라 Windows
무중단 재시작 불가), `record()` 녹화(`replay.py:94`).

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

### [M] 3.5 로깅 집계 어긋남 — `usagelog.py:32-37,74-77`
로컬타임 버킷(DST/tz 변경 시 이중/누락), session id 가 서버 내 시퀀스라 재시작 후
재등장, account 가 프레임마다 갱신돼 한 응답이 엉뚱한 계정으로. **개선**: UTC 버킷+offset
저장, session id 에 서버 epoch/sock 해시 prefix, account 는 세션 first-seen 고정.

### [M] 3.6 라이브 계정합 vs 영속 집계 불일치 — `serverclaude.py:549-562`
상태줄 "계정별 Σ" 는 살아있는 패널 합(account 미정·종료 세션 제외)인데 영속 로그와
의미가 달라 혼동. **개선**: UI 출처 구분 표기 + account 확정 전 committed 버퍼링/후정정.

### [M] 3.7 토큰 추적이 CLI 단일 포맷에 강결합(silent failure) — `claude.py:26-31,73-124`, `tokens.py:72`
스피너 글리프·"context N%"·org/plan 정규식이 현행 포맷 가정 → 포맷 변경 시 상태머신
전체가 조용히 멈춤. running 급감 휴리스틱(`peak - max(50,peak//2)`)도 짧은 연속 응답을
합치거나 과민하게 끊음. **개선**: 감지 실패 지속 시 "포맷 미인식" 경고로 가시화, 응답
경계는 busy 재시작 엣지 1차 + 급감 보조, 실제 footer 골든 픽스처.

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
