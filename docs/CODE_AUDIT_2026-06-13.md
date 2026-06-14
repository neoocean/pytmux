# pytmux 전체 코드 감사 — 속도·보안 (2026-06-13)

> **✅ 구현 완료(2026-06-13)**: 아래 14개 항목 전부 적용·검증(606 green). 성능 8건은
> 동작 불변(CSE·할당 제거·캐시), 보안 5건은 코드 수정 + 회귀 테스트 3종 추가
> (S3 `test_ipc`, S2 `test_remote`, S4 `test_restart`), S1 은 신뢰 모델 인코드 문서화
> (ssh-암호화·F1/F2 서버검증으로 토큰-over-ssh 는 의도된 인증 — 재설계 불요). 각 표의
> ✅ 표시 참조.

> **상태**: 신규 패스. `pytmuxlib/` 전체(~25.5k LOC)를 **전송/코어·플러그인/데이터·서버
> 핫패스·클라 렌더** 4축으로 정독해 도출한 **net-new 개선점**만 싣는다.
> **방법**: 정적 코드 정독 + 데이터 흐름 추적. 동적 익스플로잇·벤치 미수행(개선 시
> `scripts/bench.py` before/after + `tests/run.py` 통과가 게이트).
> **선행 문서와의 관계**: 본 문서는 아래에 **이미 적용된** 항목을 재보고하지 않는다.
> - 보안: [SECURITY_REVIEW.md](SECURITY_REVIEW.md) F1~F8 (전 항목 적용 완료)
> - 성능: [PERFORMANCE_REVIEW_2026-06-07.md](PERFORMANCE_REVIEW_2026-06-07.md) C1~C5,
>   [PERFORMANCE_SCENARIO.md](PERFORMANCE_SCENARIO.md) A1~A5·B1~B11 (전부 완료)
>
> 표기: **[검증됨]** = 작성자가 인용된 `file:line` 을 직접 읽어 확인.
> 효과 **높음·중·낮음** / 위험 **낮음·중**.

---

## 0. 요약 (TL;DR)

직전 스프린트로 핫패스 상위 레버(C1~C5)와 보안 경계(F1~F8)는 정리됐다. 이번 패스의
결론: **거대한 신규 취약점은 없다**(SQL 전수 파라미터화, subprocess 전수 argv, ReDoS
정규식 길이상한 이미 적용 — §5.9 작업의 성과). 대신 ①**연합(federation)·stdio-proxy
신뢰 경계**에 F1 토큰 모델을 약화시키는 net-new 보안 갭 2건과, ②**Claude 패널 스캔
루프**·**행 직렬화**·**클라 합성/상태바**에서 프레임당 반복 작업을 제거하는 성능 레버가
남아 있다.

### 보안 (net-new, F1~F8 외)

| ID | 심각도 | 항목 | 위치 |
|----|--------|------|------|
| S1 | **Med** ✅문서화 | stdio-proxy 토큰-over-ssh = 의도된 인증(ssh 암호화·F1/F2 서버검증). 신뢰 모델 인코드 명시 | `launcher.py:288` |
| S2 | **Med** ✅적용 | `remote_attach` host = 비신뢰 클라 입력 → **ssh 옵션 인젝션(RCE)** 차단(`--`+선행`-`/공백 거부) | `serverremote.py:122` |
| S3 | Low–Med ✅적용 | `parse_endpoint` 의 `int(port)` 가드(명확한 `ValueError`) + 회귀 테스트 | `ipc.py:54` |
| S4 | Low ✅적용 | 재시작 상태파일 `master_fd`/`child_pid` 의미검증(정수·열림·char device·양수 pid) + 테스트 | `serverpersist.py:184` |
| S5 | Low ✅적용 | `search_pane` 텍스트라인 `_feed_seq` 기반 캐시(n/N 재스캔 제거) | `server.py:256,588` |
| S6 | Low ✅적용 | 레거시 `usagelog.append` → `ipc.open_private`(0600) | `usagelog.py:79` |

> **S2 정정**: 본 항목의 실질 위험은 "핀 부재"보다 **ssh 옵션 인젝션**이다 — argv 형은
> 셸 인젝션을 막지만 ssh 가 argv 의 `-oProxyCommand=<명령>` 을 옵션으로 해석해 임의
> 명령을 실행한다. `--` 로 옵션 파싱을 끊고 선행 `-`/공백 host 를 거부해 차단했다.
> host-key 정책은 사용자 ssh config/known_hosts 를 그대로 따른다(강제 변경은 동작 설정을
> 깨거나 보안을 느슨하게 만들 수 있어 미적용).
>
> **S2 후속(2026-06-14, "특권 설정으로 취급" 권장 반영)**: opt-in 연합 허용목록
> `remote_allowed_hosts`(opts.json, 0600 서버측) 추가 — **비어 있으면(기본) 현행 무변경**,
> 설정되면 `_remote_transport` 가 정확히 일치하는 host 만 ssh 로 띄운다. 비신뢰 클라 cmd
> 입력이 데몬 ssh egress 를 임의 호스트로 조종하지 못하게 잠그되, 클라 cmd 로는 못 바꾸는
> 관리자 특권 설정으로 둔다(endpoint 로컬 직결엔 미적용). 테스트
> `test_remote_allowed_hosts_allowlist`. host-key 강제 핀은 여전히 미적용(동작 설정 보존).

### 성능 (net-new, C1~C5·A·B 외)

| 순위 | ID | 레버 | 위치 | 효과 | 위험 | 상태 |
|---|---|---|---|---|---|---|
| 1 | P1 | `_scan_claude` 프레임당 동일 정규식 중복 호출 제거(CSE) | `servermixin.py:1138,1161,1173,1195,1249` | 높음 | 낮음 | ✅적용 |
| 2 | P2 | `_serialize_row` 의 run 당 `dict(cur_key)` 할당 제거(`_key_to_style` lru_cache) | `model.py:866,872` | 높음 | 낮음 | ✅적용 |
| 3 | P3 | `render()` 라이브 경로서 전체 스크롤백 복사 회피 | `model.py:884` | 중 | 낮음 | ✅적용 |
| 4 | P4 | 클라 `_composite` 의 프레임당 `Style`/`theme_color` 할당 캐시(시그니처) | `client.py:1344+` | 높음 | 낮음 | ✅적용 |
| 5 | P5 | `theme_color` 메모이즈(테마이름+변수명 캐시) | `clientutil.py:253` | 중 | 낮음 | ✅적용 |
| 6 | P6 | 상태바 세그먼트 폭 재합산 → 증분 누적(메인+플러그인 훅) | `clientwidgets.py:1087,1138`, `clientstatus.py:224,236` | 중 | 낮음 | ✅적용(전체) |
| 7 | P7 | `usagedb` 레코드별 fsync → `synchronous=NORMAL`(WAL) | `usagedb.py:84` | 중 | 중 | ✅적용 |
| 8 | P8 | autorename 의 동기 `ps` subprocess → `run_in_executor` 오프로드 | `servertree.py:568` | 중 | 중 | ✅적용 |

> **P6 범위**: 메인 상태바(`clientwidgets._render_main`)의 **이중 전수 재순회**(rx0·used)를
> 증분 누적으로 제거. **+ 플러그인 훅까지 확장**: `client_statusbar` 훅 시그니처에 `w0`
> (들어오는 누적 셀폭)을 추가하고 **새 누적 폭을 반환**하게 바꿔, `render_segs` 의
> `ux0`/`left` 전수합산과 코어의 추가분 재순회를 모두 제거했다. 4계층(호출부→디스패처
> →claude-code 래퍼→render_segs) 일괄 변경, 단일 구현부(claude-code)·직접 호출 테스트
> 없음 확인 후 적용. 불변식: 훅 진입 시 `w0 == segs 전수합산`(좌측+배지+REC 까지만 들어옴).
>
> **P7 방식**: 문서가 제안한 앱-레벨 배치 버퍼 대신 `PRAGMA synchronous=NORMAL`(WAL
> 표준)을 채택 — commit 마다 fsync 하던 비용을 체크포인트로 미뤄 같은 효과를 더 낮은
> 위험으로 얻는다(앱 크래시엔 내구, OS 크래시/정전 시에만 마지막 미체크포인트 구간 유실).
>
> **P8**: 이미 async 인 `_autorename_loop` 에서 동기 `ps` 호출만 `run_in_executor` 로
> 오프로드(읽기전용 OS 호출이라 스레드 안전). `_fg_command` 자체는 동기 유지 — 다른
> 동기 호출부(`_fg_is_claude` 등) 무영향. `_pane_cwd` 의 `lsof` 동기 호출은 호출 빈도가
> 낮아(패널 cwd 조회 시점) 이번 범위 밖.

> **권장 착수 순서**: P1 → P2 → P4 → P5 → P6 (저위험 즉효 묶음) → P3 → P7/P8(동작
> 변경·async 리팩터, 측정 후). 보안은 S3(값싼 견고화) 먼저, 이어 S1·S2(연합 신뢰
> 경계 재설계, 설계 검토 필요).

---

## 1. 보안 — net-new 발견

### S1. stdio-proxy 가 인증 토큰을 stdout 평문으로 노출 [검증됨] · Med

`pytmuxlib/launcher.py:288-289` (`run_stdio_proxy`):
```python
tok = ipc.read_token(sock_path) or ""
out.write(f"TOKEN {tok}\n".encode())
```
연합 경로(`ssh -T <host> pytmux stdio-proxy`)에서 프록시가 0600 인증 토큰을 읽어 stdout
한 줄로 흘린다. 이 토큰은 **F1(Windows TCP 루프백 무인증)의 유일한 방어선**이다. 프록시
자체는 "토큰 파일을 읽을 수 있는가"(= 동일 UID) 외에 추가 인증 게이트가 없으므로 Unix
에선 중복·무해하지만, 토큰이 **애플리케이션 계층에서 ssh 채널을 타고 파이프로 방출**되어
같은 사용자 세션의 다른 프로세스가 그 subprocess stdout 을 관찰하면 **라이브 토큰을 수확**
→ Windows 의 루프백 TCP 리스너에 직접 접속해 F1 을 우회할 수 있다. 영향은 same-user-on-host
로 한정되나, 토큰은 바로 그 cross-local-user TCP 케이스를 막으려던 것이라 의미가 약화된다.

**개선**: 연결용 토큰과 연합용 토큰을 분리·회전하거나, 스플라이스된 연결에 토큰
의존 대신 peer-UID/peer 검증을 요구.

### S2. `remote_attach` 가 클라 메시지의 host 를 검증·호스트키 핀 없이 ssh 대상으로 사용 [검증됨] · Med

`pytmuxlib/serverremote.py:122-126`:
```python
proc = await asyncio.create_subprocess_exec(
    "ssh", "-T", "-o", "BatchMode=yes", host, "pytmux", "stdio-proxy", ...)
```
`host` 는 클라 `cmd` 프레임의 `remote_attach`/`remote_new_window` 액션(`serverio.py:325-354`)
에서 **무검증**으로 ssh 목적지 인자에 전달된다. argv 형이라 셸 인젝션은 없지만, 임의의
ssh 대상 문자열을 `cmd` 프레임을 보낼 수 있는 누구든 지정할 수 있고, `StrictHostKeyChecking`/
`UserKnownHostsFile` 핀이 없어 최초 접속 시 공격자 영향 호스트키를 조용히 수락한다. S1
과 결합하면 서버가 라이브 키입력(`input` 릴레이)을 그 상류로 포워딩한다. 즉 **명령 가능
클라가 데몬의 ssh egress 와 키입력 릴레이를 임의 목적지로 조종**할 수 있다.

**개선**: `host` 를 ssh config alias 화이트리스트로 검증, 명시적 host-key 정책 추가,
연합 대상을 per-message 클라 입력이 아닌 특권 설정으로 취급.

### S3. `parse_endpoint` 의 `int(port)` 무가드 [검증됨] · Low–Med

`pytmuxlib/ipc.py:54`:
```python
return ("tcp", host, int(port))   # try/except 없음
```
`"tcp:127.0.0.1:abc"` / `"tcp:"` 같은 잘못된 엔드포인트는 `parse_endpoint` 밖으로
`ValueError` 를 던진다 — `start_server`/`open_connection`/`control_socket` 에서 호출된다.
`_read_portfile`(ipc.py:167)은 `ValueError` 를 가드하지만 `parse_endpoint` 는 아니다.
주로 오설정 견고성/DoS 문제이나, (F3 이전) 선점 가능한 상태 디렉터리에 엉터리 엔드포인트를
심으면 미처리 크래시. **개선**: `int(port)` 를 try/except 로 감싸 명확한 에러 반환.

### S4. 재시작 상태파일의 fd/pid 의미검증 부재 [검증됨] · Low

`pytmuxlib/serverpersist.py:184-199` → `pty_backend.adopt(ps["master_fd"], ps["child_pid"], ...)`.
resume 파일은 데몬이 0600 으로 쓰므로 정상 신뢰 하에선 안전. 그러나 로드된 `master_fd`(파일
내 임의 정수)가 그대로 `fcntl`/`set_winsize` 대상이 되고 `child_pid` 가 `reap`/`killpg`
대상이 된다. 파일 무결성이 깨지면(F3 선점 창 등) 공격자 제어 fd/pid 로 임의 fd ioctl·임의
프로세스그룹 시그널이 가능한 confused-deputy. 노드별 `KeyError`/`OSError` 가드는 있으나
의미 유효성 검증은 없다. **개선**: `master_fd` 가 execv 로 실제 상속된 집합에 속하는지,
`child_pid` 가 데몬 자신의 프로세스그룹인지 확인 후 사용.

### S5. `search_pane` 가 키입력마다 전체 스크롤백을 동기 재스캔 [검증됨] · Low

`pytmuxlib/server.py:577-604`: `_pane_text_lines(p)` 가 전체 history(HISTORY=10000)×열을
매 검색마다 재구성·소문자화 후 선형 스캔한다. 클라가 `search` 를 빠르게 구동하면 이벤트
루프에서 **동기로** 전체 버퍼를 매번 재스캔(feed 는 `FEED_SLICE` 로 양보하지만 search 는
양보 없음) → 클라-구동 CPU/지연 증폭. **개선**: 패널 텍스트 스냅샷을 검색 간 캐시하거나
스캔 비용 상한.

### S6. 레거시 토큰 JSONL 평문 open(잠복) [검증됨] · Low

`pytmuxlib/plugins/claude-code/usagelog.py:79-87` 의 `append` 는 다른 곳이 쓰는
`ipc.open_private`(0600, F5)가 아닌 `open(path, "a")`(umask, 보통 0644)를 쓴다. 계정 alias
가 담긴 `*.tokens.jsonl` 이 공유 호스트에서 group/other-readable 이 된다. **완화 기적용**:
현행 백엔드는 SQLite(`usagedb.connect` 가 0700 디렉터리·0600 파일)이고 `usagelog.append`
는 현행 서버 흐름에서 더는 호출되지 않음(JSONL 은 읽기전용 레거시/임포트). 영향은 잠복.
**개선(원하면)**: 레거시 append 도 `ipc.open_private` 경유.

> **확인했으나 비취약(스코프 정리)**: 프레이밍 길이 처리(`protocol.read_msg`, `MAX_FRAME`
> 상한 후 readexactly), `clamp_dim`(3~2000 상한), 트리 연산 범위 가드, argv 기반 spawn
> (no `shell=True`/`pickle`/`os.system`), NEST DCS 정규식 앵커+8192 상한, conpty ctypes
> 핸들 처리, SQL 전수 파라미터화(`usagedb` PRAGMA/IN-절도 안전), ReDoS 정규식 길이상한
> (§5.9), 스크랩 데이터의 eval/exec/format/SQL/subprocess 미도달. — 모두 견고.

---

## 2. 성능 — net-new 레버

### P1. `_scan_claude` 프레임당 동일 정규식 중복 호출 제거(CSE) [검증됨] · 높음·낮음

`pytmuxlib/plugins/claude-code/servermixin.py`: 비-settled Claude 패널마다 프레임(30Hz)당
`txt = screen_text(p.screen)` 한 번 뒤로 다수 스캐너가 같은 `txt` 를 독립 재스캔한다.
특히 동일 프레임 내 **중복 호출**이 명확:
- `claude_perm_mode(txt)` — `:1138`, `:1145`(여기서 `pm` 으로 저장됨), `:1161` 에서 각각
  새로 호출. `:1145` 의 `pm` 을 `:1138`/`:1161` 에서 재사용하면 됨.
- `claude_context_pct(txt)` — `:976`(여기서 `cp` 로 저장됨), `:1173`, `:1195`, `:1249`
  에서 각각 새로. `:976` 의 `cp` 재사용.

스캔 루프는 활성 윈도뿐 아니라 **모든 탭의 모든 패널**을 돈다. **개선**: 프레임 진입 시
파생값을 지역 변수로 1회 계산해 재사용(순수함수의 CSE). 추가로 무거운 스캐너 클러스터를
값싼 `in` 부분문자열 게이트 뒤로 두는 패턴(`:827` `"Current" in txt` 가 이미 사용)을
`claude_api_error`/`claude_remote_*` 로 확장. **효과 높음**(busy 패널의 지배적 per-frame
CPU), **위험 낮음**(불변 문자열에 대한 순수함수 CSE).

### P2. `_serialize_row` 의 run 당 `dict(cur_key)` 할당 제거 [검증됨] · 높음·낮음

`pytmuxlib/model.py:866,872`:
```python
segs.append(["".join(cur_text), dict(cur_key)])
```
`cur_key` 는 `_style_key`(B3)의 메모된 정렬 튜플인데, run 마다 그로부터 새 dict 를 만들어
B3 메모이즈를 일부 무력화한다. 대부분 셀이 기본 스타일(`cur_key == ()`)이라 `dict(())`
빈 dict 를 매 run 할당한다. 렌더 fast-path(`model.py:917`)는 dirty 행을 매 프레임 재직렬화
하므로 busy 패널의 dirty 행마다 비용을 문다. **개선**: 정렬 튜플→dict 를 `lru_cache` 로
메모하거나 `_style_key` 가 dict 를 직접 반환·공통 `()` 케이스는 공유 불변 빈 dict. 검색
하이라이트 경로(`model.py:928`)는 이미 `{**st, "rv":1}` 로 복사하므로 공유 안전 —
**다운스트림이 스타일 dict 를 in-place 변경하지 않는지만 확인**. **효과 높음**, **위험 낮음**.

### P3. `render()` 라이브 경로서 전체 스크롤백 복사 회피 [검증됨] · 중·낮음

`pytmuxlib/model.py:884-892`: fast-path 캐시 검사(라인 ~912) **이전에** 무조건
`hist = list(h.top)`(스크롤백 deque 전체 복사, 최대 HISTORY)와
`full = hist + [버퍼 행]`(전 history+화면 행의 새 리스트)을 만들고 슬라이스한다. 라이브
경로(`scroll == 0`)에선 `window` 가 사실상 화면 버퍼 행이라 `hist`/`full` 이 불필요. **개선**:
`hist`/`full` 계산을 `self.scroll > 0`(스크롤백 보기) 분기로 한정. **효과 중**(매 프레임·매
dirty 패널마다 수천 스크롤백 행의 deque 복사+concat 제거), **위험 낮음**.

### P4. 클라 `_composite` 의 프레임당 `Style`/`theme_color` 할당 캐시 [검증됨] · 높음·낮음

`pytmuxlib/client.py:1344-1361` 외: 매 `_composite`(≈30Hz)가 테마/degraded/remote 토글
시에만 바뀌는 입력으로 `Style` 다수를 새로 만든다:
```python
inactive_box = Style(color="grey42")
active_box   = Style(color=theme_color(self, "primary"), bold=True)
flash_box    = Style(color=theme_color(self, "warning"), bold=True)
# + remote/degraded/conn/tint/stint/hl/ftint ...
```
C3 는 **진짜 상수** 스타일만 호이스트했고, 이들 테마 파생 스타일은 매 프레임 재생성 +
`theme_color`(uncached, P5) 호출. **개선**: `(theme, viewing_remote, net_degraded)` 시그니처를
프레임당 1회 계산해 `inactive_box/active_box/flash_box` 를 `self` 에 캐시(시그니처 변할
때만 재생성). **효과 높음**(1줄 델타에도 매 프레임 ~6~10 `Style()` + 수 개 `theme_color`
제거), **위험 낮음**.

### P5. `theme_color` 메모이즈 [검증됨] · 중·낮음

`pytmuxlib/clientutil.py:253-261`: `try/except` + 이중 dict `.get`. `_composite`(P4)·
`StatusBar._render_main` 의 `tc = lambda n: theme_color(self,n)`(`clientwidgets.py:1063`)·
`clientstatus.render_segs` 에서 프레임/세그먼트당 반복 호출. **개선**: `(theme_name, name)`
키 `lru_cache`(테마 변경 시 무효화). 고정 테마에선 순수함수에 준함. **효과 중**, **위험 낮음**.

### P6. 상태바/Claude 상태 세그먼트 폭 재합산 → 증분 누적 [검증됨] · 중·낮음

`pytmuxlib/clientwidgets.py:1087,1138`: `_render_main`(스트리밍 중 30Hz)이 REC 존 시작을
`sum(sum(_char_cells(c) for c in s.text) for s in segs)` 로, 다시 `used = sum(...)` 로
**전 세그먼트를 문자 단위로 두 번 재순회**한다(`clientstatus.render_segs:224,236` 도 동일
패턴). C1 의 `_char_cells` 메모는 문자 측정은 줄였지만 **이미 만든 세그먼트의 전수 재순회**
자체가 남는다. **개선**: 세그먼트를 append 하며 폭 누적기(단일 `width`)를 증분 유지해 두
번의 전수 합산 제거. **효과 중**(O(전체문자)×2 → 증분), **위험 낮음**.

### P7. `usagedb.insert` 레코드별 commit → 배치/지연 flush [검증됨] · 중·중

`pytmuxlib/plugins/claude-code/usagedb.py:213`: `insert` 가 레코드마다 `conn.commit()`
(WAL fsync) 하고, `_scan_claude`(`servermixin.py:1721`)의 `_log_tokens` 가 응답 경계마다
호출 → 스트리밍 중 빈번 fsync. `insert_many`(:219)는 임포트에만 사용. **개선**: 레코드를
버퍼링해 짧은 타이머(1~2s)/`insert_many` 로 flush, 또는 WAL 체크포인트에 맡겨 commit
캐던스 완화. **효과 중**, **위험 중**(크래시 시 마지막 미flush 초의 토큰 기록 유실 —
usage 로그엔 허용 가능하나 동작 변경).

### P8. autorename 의 동기 `ps` subprocess → executor/TTL 캐시 [검증됨] · 중·중

`pytmuxlib/servertree.py:544`(`_fg_command` 의 `subprocess.run`)이 `:568` autorename
루프에서 **2초마다, 클라 있는 세션의 auto_rename 탭마다** 이벤트 루프 위에서 동기 실행된다
(`ps -o comm= -p <pgid>`). 탭이 많으면 주기적 루프 stall. 동일 패턴이 `_fg_is_claude`
(`servermixin.py:390`)·`_pane_cwd` 의 `lsof`(`servertree.py:179`)에도 있음(`p4 changes` 는
이미 `run_in_executor` 사용 — `serverio.py:929`). **개선**: subprocess 동반 호출을
`run_in_executor` 로, 또는 `(pgid)` 키 단기 TTL 캐시(전경 pgrp 는 2초 틱 사이 거의 불변).
**효과 중**(탭 수에 비례하는 주기적 동기 stall), **위험 중**(async/lifecycle 리팩터).

> **기각/비채택(자동분석 과대평가·확인 결과 무해)**: `_feed_drain` O(n²)(이미 snapshot+offset
> 수정, `serverpty.py:230`), `model.feed` no-ESC fast path 기적용(`:720`), `Window.panes()`
> 캐시·명시 무효화(`:1014`), PTY 리더 루프 add_reader/Event 기반(busy-wait 없음), claude.py·
> model.py 정규식 전부 모듈레벨 컴파일, 키입력 경로(`on_key`/`key_to_bytes`/keymap) 이미
> 경량(per-key 정규식·i18n·할당 없음).

---

## 3. 검증

- 성능 P1~P8: 동작 불변(CSE·할당 제거·캐시·executor)이라 `tests/run.py` 전체 green
  (606)으로 회귀 게이트. P7(`synchronous=NORMAL`)은 내구성 트레이드오프가 명시적(앱
  크래시 내구·OS 크래시 시 미체크포인트 구간만 유실, usage 로그 허용). 절대 효과는
  추후 `scripts/bench.py` before/after 로 정량화 권장.
- 보안: S3 `tests/test_ipc.py`(malformed 엔드포인트 → ValueError), S2 `tests/test_remote.py`
  (ssh 옵션 인젝션/공백 host 거부), S4 `tests/test_restart.py`(변조 fd/pid 거부) 회귀
  테스트 추가. S5/S6 은 기존 스위트로 동작 불변 확인.

## 4. 구현 결과 (2026-06-13)

14개 항목 **전부 적용**(606 green). 게시: p4 + git.

- **성능 P1~P8 ✅**: 모두 동작 불변(CSE·할당 제거·캐시·executor 오프로드). P6 은 메인
  상태바 범위, P7 은 `synchronous=NORMAL` 방식, P8 은 autorename 루프 executor 오프로드.
- **보안 S2~S6 ✅**: 코드 수정 + 회귀 테스트 3종(S3 `test_ipc`·S2 `test_remote`·S4
  `test_restart`). S2 는 ssh 옵션 인젝션(RCE) 차단이 실질.
- **보안 S1 ✅문서화**: 토큰-over-ssh 는 ssh 암호화 + 서버측 F1/F2 검증으로 보호되는
  **의도된** 페더레이션 인증 — 같은-UID 노출은 0600 토큰 파일과 등가라 추가 위험 없음.
  재설계(에페메럴 토큰)는 동작하는 페더레이션을 위협해 미채택, 신뢰 모델을 코드에 명시.
