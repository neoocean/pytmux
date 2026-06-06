# 조사: Claude Code 중단 + pytmux 클라이언트 종료 현상

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

---
_(이 문서는 진행 중 계속 append/갱신된다.)_
