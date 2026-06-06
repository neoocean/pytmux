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
_(이 문서는 진행 중 계속 append/갱신된다.)_
