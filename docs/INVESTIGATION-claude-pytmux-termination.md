# 조사: Claude Code 중단 + pytmux 클라이언트 종료 현상

> 📦 **플러그인 이전 메모(CL 57812, 조사 이후)**: 본문의 `serverclaude.py`(주입 경로 등) 참조는
> 현재 `pytmuxlib/plugins/claude-code/servermixin.py`(`ServerClaudeMixin`)다 — 동작·결론 불변,
> 위치만 이전. 시점 기록이라 본문은 보존. 참고: [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md) §4.

> 시작: 2026-06-06 · 상태: **진행 중(LIVE)** · 작성자: 조사 에이전트
> 목적: pytmux pane 안에서 Claude Code 를 실행하던 중 **Claude Code 가
> 멈추고(halt) 동시에 pytmux 클라이언트(Textual 앱)가 종료되는** 재현성 있는
> 현상의 근본 원인 규명.
> 진행 중 중단 가능성이 있어 **증거가 쌓이는 즉시 이 문서를 갱신·커밋**한다
> (GitHub push 는 이번 회차에는 하지 않음 — 로컬 git commit 만).

---

## 0. 한 줄 요약 (계속 갱신)

- (잠정) 서버·셸은 멀쩡히 살아남고 **클라이언트 + 일부 pane 의 claude 만**
  죽는 패턴. macOS 크래시 리포트/ jetsam 없음 → **segfault 아님, 시그널 혹은
  정상-ish 종료**. 원인은 클라이언트(Textual) 측 또는 pytmux 의 Claude 전용
  처리(감지/auto-action/sticky 헤더 렌더)로 좁혀짐. **검증 진행 중.**

---

## 1. 환경 / 증거 수집 시각

- 수집 시각: 2026-06-06 18:46 KST, 호스트 uptime 17d 21h, macOS 15.7.3
  (24G419), Apple Silicon (Mac16,11).
- 시스템 메모리 여유 78%, load avg 6.0 (claude 다중 실행 중). **메모리 압박
  아님.**

## 2. 프로세스 토폴로지 (사실, `ps` 실측)

```
launchd(1)
└─ pytmux SERVER  45767  (Fri 6/5 20:08 기동, 22h+ 무중단)   ← 데몬, PPID=1
   ├─ ttys004  zsh 2630 (6/6 15:44) ─ claude 2692 (15:45, 15min CPU)  ★생존
   ├─ ttys005  zsh 30101(6/6 01:28) ─ claude 82153(18:42)            re-launched
   ├─ ttys006  zsh 49321(6/6 14:00) ─ claude 82804(18:45)            re-launched
   ├─ ttys001  zsh 35508(6/6 13:22) ─ ssh office1
   └─ ttys009  zsh 24718(6/6 01:23) ─ ssh office1

Terminal(ttys002) ─ -zsh 78530 (18:34) ─ pytmux CLIENT 81908 (18:41) ← 재attach본
```

### 2.1 확정 사실
1. **서버(45767)는 죽지 않았다** — 22시간 무중단, PPID=1 데몬.
2. **pane 의 셸(zsh)들도 죽지 않았다** — 01:28~15:44 기동분이 그대로 생존.
3. 죽은 뒤 재기동된 것: **pytmux 클라이언트**(18:41 재attach) + ttys005·
   ttys006 안의 **claude**(18:42 / 18:45). ttys004 의 claude(2692)는 생존.
4. **macOS 크래시 리포트 없음** (`~/Library/Logs/DiagnosticReports` 에
   python/node/claude 항목 0건; 유일한 .ips 는 무관한 6/4 앱).
5. **jetsam / memorystatus / lowswap 로그 0건** (최근 3h `log show`).

### 2.2 위 사실에서 바로 따라오는 추론
- 크래시 리포트도 jetsam 도 없다 → **segfault/OOM-kill 이 아니다.** 남는 종료
  경로는 (a) 시그널(SIGHUP/SIGTERM/SIGINT/SIGKILL) (b) 프로세스의 자발적
  `sys.exit`/정상 return.
- 서버가 PTY/셸을 보유하는 클라이언트–서버 분리 구조이므로, **클라이언트가
  죽어도 pane 안 claude 는 살아야 정상**이다. 그런데 claude 두 개가 같이 죽었다
  → 단순 "클라 크래시"로는 설명 안 됨. 다음 중 하나:
  - claude 가 먼저(독립적으로) 죽고, 클라는 별개로 죽었다.
  - pytmux 가 **claude 에게 직접 시그널/키입력**을 보내 멈추게 했다(예:
    auto-action/감지 로직). ← pytmux 가 claude 를 죽일 수 있는 유일한 경로라
    **최우선 의심**.
  - 셸·서버가 살아있는데 claude 만 죽은 건, claude 프로세스에 한정된 시그널
    (그 pane 의 foreground 프로세스 그룹에 SIGINT/SIGHUP 등)일 가능성.

### 2.3 서버측 예외 로그: **없음**
- `serverio.py:_log_error` 가 dispatch/_send_full/flush 예외를 `<sock>.error.log`
  로 append 하도록 되어 있으나 `/tmp/pytmux-501/default.sock.error.log` **부재**.
  → 서버 핸들러 경로에서 잡힌 예외는 없었다. 크래시는 **클라이언트 측**이거나
  서버의 try/except 밖(예: PTY로 키 송신 같은 정상경로)이다.

## 3. 의심 후보 (검증 대기)

| # | 가설 | 근거 | 검증 방법 | 상태 |
|---|------|------|-----------|------|
| H1 | pytmux **Claude auto-action** 이 claude 에 키/시그널을 보내 중단시킴 | "claude 만 멈춤"을 설명. M14 auto-action 최근 추가 | server.py 의 auto-action 송신부 + 트리거 조건 정독 | 조사중 |
| H2 | 클라이언트 **Claude 렌더 경로**(_draw_claude_headers/claude_state) 가 특정 출력에서 예외 → 클라 종료 | "클라만 죽음"의 전형. claude 출력 파싱 휴리스틱 | client.py 렌더 + 예외 처리 정독 | 대기 |
| H3 | 최근 **perf 패스트패스**(dirty행 재직렬화/플레인텍스트 경로) 회귀 | 최근 다수 커밋 | 해당 커밋 diff 검토 | 대기 |
| H4 | **REC 기본 ON**(출력 캡처) 가 디스크/메모리 누수 → 동시 사망 | f3c042f 로 기본 ON, tokens.jsonl 147KB | 캡처 누적량/주기 확인 | 대기 |
| H5 | 외부 시그널(터미널 닫힘/세션 SIGHUP) 동시 전파 | — | unified log 시그널/세션 이벤트 | 대기 |

## 4. 다음 단계
1. server.py 의 Claude 감지 + auto-action 송신 경로 정독 (H1).
2. client.py 의 Claude 렌더/예외 경로 정독 (H2).
3. 최근 perf 커밋 diff 검토 (H3).
4. unified log 에서 18:30~18:45 claude/python 종료·시그널 추적 (H5).

---

## 5. ★ 2차 재발 (2026-06-06 19:01) — 결정적 반증 증거

사용자가 "다시 중단" 보고. 19:03 시점 라이브 실측 결과, **이전 가설(서버 사망)을
정면으로 반증**하는 새 사실이 나왔다.

### 5.1 19:03 실측 프로세스 토폴로지
```
launchd(1)
└─ pytmux SERVER 45767  (Fri 6/5 20:08 기동)        ★★ 여전히 생존 (23h+)
   └─ zsh 30101 (6/6 01:28 기동)                     ★★ 생존 (셸 안 죽음)
      └─ claude 88758 (6/6 19:01:45 재기동) = 「이 세션, 나」  ← 이전 claude 가 19:01 사망 후 재기동
   └─ ... (다른 pane 의 zsh 들도 생존)
   └─ claude 2692 (6/6 15:45 기동)                   ★ 생존 (다른 pane)

Terminal ─ -zsh 78530 ─ pytmux CLIENT 88728 (6/6 19:01:38 재attach)  ← 이전 클라(81908) 19:01 사망 후 재기동
```

### 5.2 결정적 사실 (이전 문서 CRASH_INVESTIGATION 의 "서버 사망" 이론을 반증)
1. **서버 45767 가 죽지 않았다.** 19:01 사건 내내 PPID=1 데몬으로 생존. 따라서
   "서버 프로세스 사망 → master fd 닫힘 → 전체 SIGHUP" **경로가 아니다.**
2. **claude 의 부모 zsh(30101)도 죽지 않았다.** 셸은 01:28 기동분 그대로.
   → claude 한 프로세스에만 한정된 종료(셸 foreground 프로세스에 국한된 시그널
   /자발 종료)이지, pane PTY 가 닫힌 게 아니다(닫혔으면 zsh 도 SIGHUP 받아야).
3. **`.error.log` 여전히 없음** → 서버 try/except 가드에 걸린 예외 아님.
4. **macOS 크래시 리포트 없음**(재확인 예정) → segfault 아님.
5. 동시에 **클라이언트(81908)도 19:01 에 사망 → 88728 로 재attach.**

### 5.3 새 핵심 질문 (수정된 문제 정의)
서버·셸이 멀쩡한데 **(a) pane 안 claude 와 (b) 별도 프로세스인 클라이언트가
거의 동시(19:01:38 클라 재기동, 19:01:45 claude 재기동)에 죽었다.** 둘을 동시에
죽이는 공통 원인은?
- 서버 사망이 아님(생존 확인).
- PTY 닫힘이 아님(zsh 생존).
- 남는 후보:
  - (i) **외부에서 claude·client 프로세스에 직접 시그널** (사용자/런처/스크립트/
    OS) — 둘 다 같은 사용자 소유라 일괄 kill 가능.
  - (ii) **claude 자체가 자발 종료**(예: 입력 EOF/특정 키/세그폴트 아닌 exit) +
    클라이언트가 **별개 이유로** 동시 종료(우연 동시 or 공통 트리거).
  - (iii) pytmux **클라이언트가 죽으면서 서버에 명령**을 보내 그 pane 의 claude
    에 키/시그널 전달 (예: detach 시 cleanup, auto-action). ← 코드 정독 필요.
  - (iv) **터미널 앱(Terminal.app) 자체**가 재시작/탭교체 → 클라(터미널 자식)는
    SIGHUP 으로 죽고, claude 는 무관하게 동시 사망(상관 아닌 우연)?

### 5.4 보조 사실
- 캡처 누적: `pane-5.log` 42MB, `pane-1.log` 16MB (계속 증가 중). REC 기본 ON.
- `default.sock.tokens.jsonl` 147KB — Claude 토큰 추적이 **활발히 기록 중**
  (마지막 이벤트 ts=1780740108 ≈ 19:01:48, pane5/session34). → pytmux 가
  claude 출력을 **실시간 파싱**하고 있다(토큰 카운트 추출). 이 파싱 경로가
  claude 전용 처리의 핵심 — H1/H2 와 직결.

### 5.5 다음 검증 (우선순위 재조정)
1. **unified log (`log show`) 19:00~19:02** 에서 claude(이전 PID)·client(81908)
   에 전달된 **시그널/exit** 추적 → (i) vs (ii) 판별. ★최우선
2. 클라이언트 종료 경로 정독: detach/disconnect 시 서버에 보내는 메시지 +
   서버가 그걸로 pane 에 하는 행동 (iii).
3. Claude 토큰 파서/감지 경로(servercapture? tokens.jsonl 생산자) 정독 — claude
   출력 파싱 중 예외나 부작용(키 송신) 여부.

## 6. 가설 소거 결과 (19:03~19:08 실측)

| 가설 | 검증 | 결과 |
|------|------|------|
| 서버 프로세스 사망 → 전체 SIGHUP | 서버 45767 PPID=1 로 23h+ 생존 | **반증(사망 아님)** |
| jetsam/메모리 압박 SIGKILL | JetsamEvent 리포트 0건(`~/Library/Logs/DiagnosticReports` + `/Library`), 메모리 79% 여유, 18:55~19:05 memorystatus/lowswap 로그 0건 | **반증(메모리 킬 아님)** |
| segfault/SIGABRT 하드크래시 | python/node/claude `.ips` 크래시 리포트 0건 | **반증** |
| 서버 try/except 가드 예외 | `.error.log` 부재 | **반증(가드 경로 아님)** |
| pytmux auto-action 이 claude 를 **죽임** | `serverclaude.py` 전 주입경로 정독: 보내는 키는 `continue\r`·`/clear`·`/compact`·shift+tab(`\x1b[Z`)·피드백 `0`·시작규칙뿐. **Ctrl-C(\x03)/Ctrl-D(\x04)/kill 없음** | **claude 를 직접 죽이는 경로 없음** |

→ 남은 정합 시나리오: **claude·client 가 각각 (자발적/외부신호로) 종료**됐고
pytmux 의 치명 경로는 아니다. 19:01:28 에 **Claude 데스크톱 앱이 ShipIt(Squirrel
자동업데이트)로 재시작**한 정황(audio/network/video 헬퍼 + ShipIt 프로세스 19:01:28
일제 기동, coreaudiod 오디오 컨텍스트 8개 동시 해제)이 claude **CLI 자동업데이트**와
같은 배포 타이밍일 가능성 → claude CLI 가 새 버전으로 self-exit/relaunch 했을 의심.
client 재기동(19:01:38)은 그와 별개(사용자 재실행 or Textual 예외)일 수 있다. **검증 중.**

## 7. ★ 결론 (2026-06-06 19:1x, 증거기반) — 이전 "서버 사망" 프레이밍 정정

### 7.1 가장 중요한 정정
**관측된 모든 스냅샷에서 서버(45767)는 살아 있었다.** §2 1차 스냅샷도, §5 2차
(19:01)도 서버는 PPID=1 데몬으로 생존. → `CRASH_INVESTIGATION.md` 의 중심 주장
("근본 원인 = 무엇이 서버를 죽이는가")는 **실측 데이터로 뒷받침되지 않는다.** 그건
"서버가 죽으면 이렇게 된다"는 **메커니즘 가설**이었을 뿐, 서버는 실제로 한 번도 죽지
않았다. 동시종료의 진짜 그림은 아래처럼 **두 개의 독립 사건**이다.

### 7.2 19:01 사건 분해
**(A) pytmux 클라이언트 재기동(81908→88728, 19:01:38)**
- 배후에 서버 restart 가 **없었다**: `restart_server` 는 `_save_opts()`+소켓 재바인드를
  수반하는데 `opts.json` mtime=**17:49**, `default.sock` mtime=**14:38** — 둘 다 19:01
  아님. 마지막 작업보존 재시작은 14:38 이 마지막. → 클라 relaunch 는 restart-all
  트리거가 **아니다.**
- 따라서 클라는 **자체 종료**(Textual 미처리 예외 크래시 or 사용자 수동 재실행).
  `run_client`(client.py:2886)은 `_relaunch`(restart-all) 일 때만 execv 로 자가 재기동
  하고, 그 외 미처리 예외로 `app.run()` 이 반환하면 **그냥 프로세스 종료** — 자동복구
  없음. 사용자가 직접 `pytmux` 재실행해야 한다.
- **클라이언트는 크래시 트레이스백을 디스크에 전혀 남기지 않는다**(client.py 에
  excepthook/크래시 로그 부재) → Terminal 스크롤백(휘발)으로만 가 사후분석 불가.
  ⇒ **이것이 이번 조사범위 안에서 가장 실질적인 pytmux 약점.**

**(B) 한 pane 의 claude 종료(이전 PID→88758, 19:01:45)**
- pytmux 자동주입 탓 **아님**: 현재 `opts.json` 에서 `auto_doc_clear`=false,
  `claude_ctx_autoclear`=false, `claude_auto_mode`=false, `claude_rules`="",
  `token_budget`=0 — claude 를 종료시킬 만한 주입(continue·/clear·/compact·rules)을
  **자동으로 보내지 않는 설정.** 상시 주입은 피드백 dismiss `0` 뿐(무해).
- CLI 자동업데이트 **아님**: claude 바이너리(`~/.local/bin/claude`→2.1.167) mtime=
  **14:01**, 19:01 에 안 바뀜.
- segfault **아님**(오늘 `.ips` 없음), jetsam **아님**(리포트 0·79% 여유).
- zsh 가 시그널 종료 메시지(`zsh: killed/terminated/segfault`)를 **안 찍었다**(전 pane
  캡처 ANSI-strip 검색 결과 실 메시지 0건) → **클린 exit** 쪽.
- **전례**: `~/Library/Logs/DiagnosticReports/2.1.162-2026-06-04-081043.ips` = claude
  **2.1.162 크래시 리포트(6/4)**. claude CLI 가 자체 크래시한 사실이 실재한다.
  ⇒ claude 종료는 **claude 내부/외부신호** 영역으로, pytmux 통제 밖. (오늘 19:01 은
  .ips 없는 클린 종료라 claude 자체 정상 exit 이거나 덤프 안 남는 종료.)

### 7.3 시간 상관 (해석 주의)
클라(19:01:38)·claude(19:01:45)가 ~7초 간격, 그리고 **Claude 데스크톱 앱 ShipIt
자동업데이트(19:01:28)**와 같은 분대. Claude 계열(데스크톱+CLI+pytmux의 claude 처리)
프로세스에 작용한 **공통 외부 트리거** 가능성을 시사하나, pytmux 자동화는 설정상
면책된다. **상관이지 인과 확정 아님.**

### 7.4 결론 한 줄
> 이번 "동시종료"는 **서버 사망의 귀결이 아니라**, ① 자동복구·크래시로깅이 없는
> pytmux **클라이언트의 독립 종료**와 ② pytmux 통제 밖 **claude 프로세스의 독립
> 종료**가 거의 동시에 겹친 것이다. 서버·셸은 멀쩡했다.

### 7.5 권고 (pytmux 개선 — 우선순위)
1. **클라이언트 크래시 영속화**: `run_client` 에 excepthook/try 로 트레이스백을
   상태디렉터리(`<sock>.client.crash.log`)에 기록 → 사후분석 가능하게. *(가장 시급)*
2. **클라이언트 미처리 예외 자동복구**: `app.run()` 이 예외로 반환하면(=restart-all
   아닌 비정상 종료) 한정 횟수 자동 재attach(degraded reconnect 재사용). 사용자가
   화면을 잃지 않게.
3. (다른 변종 대비, 메커니즘 방어) 서버 SIGTERM/SIGHUP 핸들러 + `run_server` 예외
   광역 가드 — §8 의 제안 유지.
4. claude 측은 pytmux 밖이라 직접 수정 불가 — 단, pytmux 가 claude 종료/재시작을
   **이벤트로 기록**(이미 tokens.jsonl 세션경계 있음)해 빈도·시각을 추적하면 외부
   트리거 상관분석이 쉬워진다.

---

## 8. 부록: 서버 종료 경로 지도 (구 CRASH_INVESTIGATION.md 통합)

> 두 조사 문서를 하나로 합치며, 옛 `CRASH_INVESTIGATION.md` 중 **여전히 유효한**
> 종료경로 지도·방어책만 여기로 옮겼다. (그 문서의 중심 주장 "근본 원인=서버 사망"
> 은 §7.1 에서 실측으로 정정됨 — 서버는 관측상 한 번도 죽지 않았다.) 아래는 "만약
> 서버가 죽는다면"의 메커니즘 참고 + 예방적 방어책이다.

### 8.1 동시종료의 메커니즘 (서버가 죽는 변종일 때)
pane 셸은 **서버의 자식**이고 서버가 각 pane 의 **PTY master fd 를 소유**한다.
따라서 서버 프로세스가 죽으면: ① master fd 가 닫혀 커널이 slave 의 foreground
프로세스그룹(=claude)에 **SIGHUP** → claude 사망, ② 리스닝 소켓이 닫혀 클라이언트
연결이 끊김 → 클라 종료. 이게 tmux 와 다른 약점(진짜 tmux 서버는 견고한 데몬).
**단, 이번 실측 두 사건 모두 서버는 생존**했으므로 이 변종은 관측되지 않았다.

### 8.2 서버 종료 경로
- **자가 종료**: `_notify_no_sessions()`→`shutdown()`(serverio.py). 마지막 pane/탭이
  닫혀 세션 0개가 되면(servertree.py `_remove_pane_from_tree`) 정상 종료. **pane EOF
  오검출 시 위험**(멀쩡한데 종료).
- **외부 시그널**: (구 약점) 서버에 시그널 핸들러가 없어 SIGTERM/SIGHUP = 정리 없는
  즉사였다. → **본 회차에서 핸들러 추가(§9)로 보강**: 잡아서 error.log 에 남기고
  깨끗이 shutdown.
- **re-exec 실패 폴백**: `_do_execv`(serverpersist.py) execv 실패 시
  `_notify_no_sessions()` 폴백 = 전체 SIGHUP(드묾).

### 8.3 예방적 방어책 (구 §4-5 → 본 회차 구현 §9 로 반영)
1. 서버 SIGTERM/SIGHUP 핸들러(정리+로깅) — **구현됨**.
2. `run_server` 미처리 예외 광역 가드+로깅 — **구현됨**.
3. 클라이언트 크래시 로깅+자동복구 — **구현됨**.
4. (미구현·설계 큰 변경) feed 경로 백프레셔, pane PTY 를 서버 죽어도 살리는 진짜
   데몬화 — 필요성 낮음(서버는 실제로 안 죽는 것으로 관측). 보류.

---

## 9. 본 회차 구현 (방어·관측 보강)

§7.5 권고를 코드로 반영. 원인이 pytmux 통제 밖(claude 독립 종료)이라 "재발 시 즉시
원인을 잡을 수 있게" 하는 **관측성 + 복원력** 위주.

1. **클라이언트 크래시 영속화 + 자동복구** (`client.py` `run_client`):
   - `app.run()` 을 try 로 감싸 미처리 예외를 `<sock>.client.crash.log` 에 기록
     (`_log_client_crash`). 지금까지 클라 크래시 트레이스백은 Terminal 스크롤백
     (휘발)으로만 가 사후분석 불가였다(§7.2-A).
   - 크래시 시 새 클라로 자가 재기동(`_relaunch_self`, execv) — 서버 생존이라 즉시
     재attach 해 화면 회복. **연쇄 크래시 가드**: 30초 내 재크래시만 누적해 5회
     초과 시 자동복구 중단·사용자 통지(무한 execv 루프 방지). 정상 종료 시 카운터
     리셋.
2. **서버 종료 시그널 핸들러** (`serverio.py` `serve`/`_on_term_signal`):
   - SIGTERM/SIGHUP 에 핸들러 설치(POSIX). 수신 시 `<sock>.error.log` 에 기록 후
     `shutdown()`. 핸들러 부재 시의 '정리 없는 즉사 → pane claude SIGHUP 연쇄'(§8.2)
     를 막고, 외부 kill 여부를 다음 조사에서 판별 가능하게 한다.
3. **`run_server` 미처리 예외 광역 가드** (`server.py`):
   - `serve()` 밖으로 샌 예외를 `_log_error("run_server(fatal)")` 로 `<sock>.error.log`
     에 남긴다. 데몬 stderr=/dev/null 이라 평소 흔적 없이 사라지던 '서버 사망' 변종
     의 트레이스백을 확보.

검증: AST/import OK, 타깃 테스트(server/client/persist/restart) 통과.

---
_(이 문서는 진행 중 계속 append/갱신된다.)_
