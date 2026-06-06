# Claude Code + pytmux 동시 종료 크래시 조사

> 진행 중 문서. 중간 종료에 대비해 **발견 즉시 기록·커밋**한다(깃헙 push 안 함, 로컬 커밋만).
> 시작: 2026-06-06. 조사 주체: Claude Code(이 세션은 pytmux pane 안에서 실행 중 — 아래 §1 참조).

## 0. 증상 (사용자 보고)

- "클로드코드 중단 **및** pytmux 클라이언트 종료"가 **동시에** 발생.
- 이전 세션에서 이 현상을 조사하던 중 **다시 동일하게** Claude Code + pytmux 가 종료됨.
  → 조사 행위(대량 터미널 출력 유발) 자체가 재현 트리거일 가능성을 시사(§5 가설 B).

## 1. 핵심 구조 발견 — 이 Claude 세션이 pytmux pane 안에서 돈다

조사 시작 시점 프로세스 트리(실측):

```
pytmux.py server (PID 45767, ppid=1)        ← 서버 데몬. init 으로 reparent(setsid)
  └─ /bin/zsh (PID 30101)                    ← pane 안의 셸 (서버의 직접 자식)
      └─ claude (PID 82153)                  ← 지금 이 Claude Code (셸의 자식)
          └─ zsh -c (Bash 툴 셸)
```

- 별도로 pytmux **클라이언트** 프로세스(PID 81908, `pytmux.py` 인자 없음)가 attach 중.
- 서버 기동 시각: **Fri Jun 5 20:08:17**, 조사 시점까지 22h+ 연속 가동(`ps -o lstart`).
- 소켓 `/tmp/pytmux-501/default.sock` 은 **Jun 6 14:38** 재생성 + cmdline 에 `--resume`
  → 서버가 **14:38 에 작업보존 re-exec(restart_server) 1회 성공**(PID/start time 유지).
  그 재시작은 Claude 생존 = **정상**. 사용자가 본 "Claude 사망" 크래시는 **다른 시점**의
  다른 실패다.

### 1.1 동시 종료의 메커니즘 (확정)

panes 셸은 **서버의 자식**이고, 서버가 각 pane 의 **PTY master fd 를 소유**한다.
따라서 **서버 프로세스가 죽으면**:

1. master fd 가 닫힌다 → 커널이 slave 의 foreground 프로세스그룹(= Claude)에 **SIGHUP**
   → Claude Code 사망.
2. 리스닝 소켓이 닫힌다 → 클라이언트가 연결 끊김 → 클라이언트 종료.

→ "Claude Code 중단 + pytmux 클라이언트 종료 동시 발생"은 **서버 프로세스 사망의 직접
   귀결**이다. **따라서 근본 원인 = "무엇이 서버 프로세스를 죽이는가".**

이건 tmux 와 다른 약점이다: 진짜 tmux 서버는 견고한 데몬이라 거의 안 죽는다. 여기선
서버가 죽는 순간 pane 안의 모든 작업(Claude 포함)이 SIGHUP 으로 함께 날아간다.

## 2. 포렌식 흔적 — 거의 없음(= 가드된 예외가 아님)

- `~/Library/Logs/DiagnosticReports/` 에 **Python 크래시 리포트(.ips) 없음**
  → SIGSEGV/SIGABRT 같은 하드 크래시가 아니다.
- `/tmp/pytmux-501/default.sock.error.log` **파일 자체가 없음**
  → `_log_error`(serverio.py:591)가 한 번도 안 불림 = dispatch/flush/send_full/scan_claude
    의 **try/except 가드에 걸린 예외가 아니다.**
- `log show` jetsam/memorystatus 조회 무수확(단, 로그 롤오버 가능성 있어 확정 아님).

결론: 서버 사망은 **가드되지 않은 치명 경로**다. 후보:
- (A) `asyncio.run` 밖으로 탈출하는 **미처리 예외**(run_server 는 KeyboardInterrupt/
  RuntimeError 만 잡음 — server.py:655).
- (B) 대량 출력 버스트 중 **MemoryError** (가드 밖에서 터지면 프로세스 종료).
- (C) 외부 **SIGTERM/SIGKILL** — 서버에 **시그널 핸들러가 전혀 없음**(아래 §3).
- (D) macOS **jetsam SIGKILL**(메모리 압박). .ips 안 남김 → 흔적 없음과 합치.

## 3. 서버 종료 경로 지도

### 3.1 자가 종료(self-shutdown) — `_notify_no_sessions()` → `shutdown()`
- `shutdown()`(serverio.py:772): `running=False` → 모든 pane `pty.terminate()`(**SIGHUP**)
  → 소켓 unlink → `loop.stop()`. **이것이 "깨끗한 서버 사망 + 전체 SIGHUP" 경로.**
- `_notify_no_sessions()`(serverio.py:234): 클라에 `bye` 전송 후 0.2s 뒤 `shutdown()`.
- 호출처:
  - `servertree.py:93` `_remove_pane_from_tree` — **마지막 pane/탭이 닫혀 세션이 0개**가
    되면 호출. = "마지막 패널 닫으면 pytmux 종료"(정상). **단, pane EOF 오검출 시 위험.**
  - `serverio.py:550/575/581/638`, `server.py:454`(kill-server) — 명시적 종료 경로.

### 3.2 외부 시그널 — 핸들러 없음(취약)
- 서버 코드에 `signal.signal`/`add_signal_handler` **전무**.
  → SIGTERM 받으면 **기본동작 = 즉시 종료**(정리 없이) → master fd 닫힘 → Claude SIGHUP.
  → 누가 SIGTERM 을 보내나? 후보: 다른 `pytmux` 런처/`proc.kill`(SIGTERM→SIGKILL),
    OS, 사용자, 스테일 클라이언트의 정리 로직. (조사 필요)
- `asyncio.run` 은 SIGINT→KeyboardInterrupt 만 설치. run_server 가 잡아 깨끗이 반환하지만,
  프로세스가 끝나며 master fd 가 닫혀 결국 Claude SIGHUP.

### 3.3 재시작 re-exec 실패 폴백 — `_do_execv`
- `_do_execv`(serverpersist.py:292): CLOEXEC 해제 → `os.execv`. **execv 실패 시**
  `_notify_no_sessions()` 폴백 = 전체 SIGHUP. (execv 실패는 드묾)

## 4. 다음 조사 단계 (이어서 할 일)

1. **feed/drain 메모리 경로 분석**(가설 B/D 핵심): pane master fd 의 add_reader 콜백 →
   `_ingest_slice`/`_feedbuf` → pyte feed. 대량 출력 시 `_feedbuf` 무한 증가 / pyte Char
   수백만 할당으로 MemoryError·jetsam 유발 여부. 백프레셔 유무 확인. (serverio.py 피드 경로)
2. **캡처 디스크 쓰기**(servercapture.py): 대량 출력을 캡처 파일에 동기 기록 시 블로킹/메모리.
   현재 캡처 파일 크기: `default.sock.capture/pane-5.log` 44MB, `pane-1.log` 16MB.
3. **SIGTERM 발신처 추적**: 런처/클라이언트/`proc.kill` 가 서버에 신호를 보내는 경로.
4. **재현 시도**: 서버에 대량 출력을 흘려(예: 큰 파일 cat) RSS 추이·생존 관찰.
   주의 — 이 행위가 실제 크래시를 유발할 수 있으니 **이 문서 커밋 이후** 별도 단계로.
5. **방어책 후보**(원인 확정 전 예비):
   - 서버에 SIGTERM/SIGHUP 핸들러 추가(깨끗한 정리 또는 무시).
   - `run_server` 의 except 를 넓혀 미처리 예외를 로깅 후 재시작/유지.
   - feed 경로 백프레셔(드레인 큐 상한) + 캡처 비동기/상한.
   - pane 의 pty 를 서버 죽어도 살리는 detach(진짜 데몬화) — 설계 큰 변경.

## 5. 작업 가설(현재 유력도)

- **가설 B/D (유력)**: 대량 터미널 출력 버스트 → 서버 메모리/CPU 급증 → MemoryError(가드 밖)
  또는 macOS jetsam SIGKILL → 서버 사망 → SIGHUP 연쇄. *조사 중 재현*과 가장 잘 맞는다
  (조사 = 큰 출력 유발). .ips/에러로그 없음과도 합치.
- **가설 C (가능)**: 외부 SIGTERM. 핸들러 부재로 즉사. 발신처 미확인.
- **가설 A (가능)**: serve() 코루틴 내 미처리 예외(restore/start_server 등) 탈출.

## 6. 타임라인(조사 로그)

- 2026-06-06: 조사 시작. §1 구조·§2 흔적·§3 종료경로 지도 확정. 문서 1차 커밋.
