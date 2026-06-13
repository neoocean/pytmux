# Claude Code 토큰 과사용 자동 회피 — 개입 시나리오

> ⚠️ **§7-4 deprecate 메모(2026-06-11)**: 본문의 절대 토큰 예산(M10 일/세션 예산,
> M12 예산 게이트, M15 계정 예산, `token_budget_*`)은 S6 §7-4 에서 **제거됐다** —
> 스크랩 추정 누계 대신 실측(/usage) 게이트(`usage_gate_*`)가 같은 소비처(자동재개
> 보류·plan 유도·우선 정리·⚠ 경고)를 담당한다. 해당 절은 설계 이력으로만 유효.
> 상세: [TOKEN_ACCOUNTING_ACCURACY_SCENARIO.md](TOKEN_ACCOUNTING_ACCURACY_SCENARIO.md) §7-4.
>
> 📦 **플러그인 이전 메모(CL 57812)**: 본문의 `serverclaude.py:NNN` 참조는 현재
> `pytmuxlib/plugins/claude-code/servermixin.py`(`ServerClaudeMixin` 클래스)에 있다 — 함수
> 이름·동작은 동일하고 모듈 위치만 이전됐다(줄번호는 드리프트, 심볼명으로 grep). 토큰 회계
> 모듈(`tokens`/`usagedb`/`usagelog`)·`claude.py` 파서는 아직 코어에 있으나 추출 후속(S5)에
> 플러그인으로 이전 예정. 추출 현황: [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md) §4·[HANDOFF.md](HANDOFF.md) §11.6.

> **상태**: 🟢 **M8~M12 구현 완료**(아래 §6 로드맵 표 참조, `tests/test_token_saver.py`
> 11케이스 + 골든 픽스처). 설정은 전역 opts(opts.json 영속)이고 `token-saver` 명령
> (별칭 `claude-settings`/`token-settings`)으로 **설정 팝업**을 열어 각 개입을 토글
> 한다. 모든 자동 개입은 **기본 OFF**(옵트인). 본 문서는 ① 무엇을 개입할 수 있고
> ② 그 개입을 어떻게 **안전하게** 자동화하는지를 코드 근거 기반으로 정리한다. 각 전략은
> `file:line` 근거·재사용할 기존 1차 함수·안전 게이트·위험·검증 게이트를 갖는다.
> 관련: [IMPROVEMENT_OPPORTUNITIES.md](IMPROVEMENT_OPPORTUNITIES.md) §3(Claude 자동화
> 공백) · [HANDOFF.md](HANDOFF.md) §10(LLM 친화) · [PERFORMANCE_SCENARIO.md](PERFORMANCE_SCENARIO.md)
> (핫패스 — 스캔 추가 시 충돌 면).
>
> **핵심 결론(먼저)**: pytmux 는 이미 토큰 절감에 필요한 **감지(잔량%·토큰누계·상태)와
> 개입(텍스트/키 주입)의 1차 함수를 모두 보유**하고 있다. 빠진 것은 **"감지값을 발화
> 조건으로 잇는 정책 계층"** 하나다. 가장 큰 공백은 `claude_usage` 가 이미 파싱하는
> **컨텍스트 잔량%가 표시(display)로만 쓰이고 트리거에 안 쓰인다**는 점이다
> (`serverclaude.py:425-431` — 문자열 `"ctx 23%"` 를 클라에 보내 헤더에 그릴 뿐).
> "잔량 < X% 면 정리"가 가장 효과 큰 절감 자동화인데 미구현이다(IMPROVEMENT §3 "빠진
> 절감 전략" 과 동일 진단).
>
> **2차 보강(이번 개정)**: pytmux 의 개입을 한 패널 너머로 넓히는 **두 표면**을 §2.4 에
> 추가했다 — ① **전 세션 동시 시야**(계정 합계는 이미 계산하나 개입은 패널 단독을 봄
> `serverclaude.py:817`↔`640`) ② **PTY 밖 알림 채널**(벨 훅 `client.py:558` 은 있으나 절감
> 신호엔 미사용). 여기서 새 시나리오 **S7(멀티세션 누적)·S8(반복 실패 루프)·S9(장기 턴)**
> 와 전략 **T5~T7**, 제안 마일스톤 **M15~M17**(§6)을 도출했다. 단일 Claude 세션이 못 하는
> 개입 — pytmux 의 가장 고유한 절감 자산이다.
>
> **3차(이번 개정) — 다음 구현 기능 설계 확정**: M15~M17 중 **M16(PTY 밖 에스컬레이션
> 훅, T6)** 을 첫 구현으로 확정하고 §8 에 상세 설계를 기록했다. 등급 0·새 서버데이터
> 0(이미 송신되는 `budget_level`·`claude_pending` 의 클라측 전이 에지에 기존 `_fire_hook`
> 을 잇기만 함)이라 가장 안전·고효용한 첫발이다(자리 비움 시 알림이 닿는 유일 경로).
>
> **4차(이번 개정) — 가시성 기능 설계(§9, M18)**: 상태줄에 세션 사용현황을 드러낸다 —
> `ctx:10%/1M`(사용%+윈도우)·`25k (7% / 5h)`(5시간 롤링 한도 근접도), 그리고 토큰
> 영역을 클릭/esc-Enter 하면 **통계 + 시나리오 on/off** 통합 팝업. 전부 등급 0(표시·탐색).
> 단 **5h 분모는 Claude 가 안 알려줘** best-effort(설정 opt·관측 학습·미상 시 무표기)임을
> §9.3 에 정직히 기록했다.
>
> **5차(이번 개정) — 그림자 `/usage` 질의 설계(§10, M19)**: §9.3 의 "5h 분모 미상"을
> Claude 의 `/usage` 실측으로 정직히 푸는 방안. 핵심 요구가 "사용자 눈에 안 띄게"라,
> **현재 세션 주입(A, render-freeze)** vs **숨은 세션(B)/headless `claude -p`(B2)** 두 길을
> 모두 검토했다. **B2(`claude -p`)=기각**(무비용 headless 지만 출력이 첫 줄뿐 — 한도 숫자
> 미출력). **방법 B(숨은 대화형 세션) 확정.** 실 `/usage` TUI 패널을 REC 캡처에서 확보해
> 픽스처화(`usage.txt`)했고, 패널이 **세션(5h) % 를 직접** 줘 §9.3 "5h 분모 미상"이 소멸
> (주간 한도·리셋 시각도 확보). 구현은 숨은 세션 lifecycle 플럼밍이 남아 **승인 후 착수**.

---

## 1. 배경과 모델 — pytmux 가 개입할 수 있는 경계

pytmux 는 Claude Code 의 **API 나 내부 상태에 접근하지 않는다.** 패널 = 서버가 소유한
PTY master fd + 자식 셸이고, Claude Code 는 그 셸 안에서 도는 한 TUI 프로그램일 뿐이다.
따라서 pytmux 의 개입 표면은 정확히 둘이다.

- **감지(read-only)**: 패널의 pyte 스크린을 평문으로 떠서(`screen_text`,
  `serverclaude.py:25`) 정규식 휴리스틱으로 상태/사용량/계정을 **추정**한다. Claude Code
  가 화면에 그리는 것만 본다 — 토큰 카운트도 footer 의 `↑/↓ N tokens` 표시를 읽은
  값이지 API 청구치가 아니다.
- **개입(write)**: 패널 PTY 에 **바이트를 쓴다.** 사용자가 키보드로 칠 수 있는 것이면
  무엇이든(텍스트+Enter, shift+tab, ESC, 슬래시 명령) 주입할 수 있고, 그 이상은 못 한다.

> **이 경계가 곧 안전의 1차 근거다.** pytmux 가 할 수 있는 모든 개입은 "사용자가
> 키보드로 했을 수도 있는 일"이라 Claude/계정 상태를 임의로 깨뜨릴 권한이 없다. 동시에
> **화면 스크래핑은 본질적으로 best-effort**(Claude UI 포맷 의존)라, 오탐 시 엉뚱한 키를
> 주입할 수 있다 — 비가역 동작(`/clear`)일수록 게이트를 두텁게 해야 한다(§5).

**토큰 과사용이란**: 같은 작업을 끝내는 데 필요 이상으로 토큰을 태우는 것. Claude Code
에서 토큰을 태우는 주 동력은 **턴마다 재전송되는 컨텍스트의 크기**다(대화가 길수록 매
턴이 비싸짐 → auto-compact 임계에서 급증). 그밖에 모델 과선택·맹목적 자동재개·게이트
없는 권한모드·잊힌 idle 세션이 있다(§3). pytmux 의 목표는 이 동력들을 **감지해 적시에
값싼 개입(정리/모드 전환/알림)으로 끊는 것**이다.

---

## 2. 개입 가능 표면 — 이미 보유한 1차 함수 (재사용 대상)

새 전략은 모두 아래 기존 자산 위에 **정책만** 얹는다. 새 주입 메커니즘을 만들지 않는다.

### 2.1 감지(sensing) — 화면 → 구조화 신호

| 함수 | 반환 | 근거 | 비고 |
|---|---|---|---|
| `claude_state` | `limit`/`busy`/`idle`/`None` | `claude.py:34` | 발화 게이트의 기본 신호 |
| `claude_usage` | `"ctx 23%"`/`"45.2k tok"`/배지 | `claude.py:77` | **문자열** — §4 T1 의 핵심 |
| `_CTX_PCT_RES` | (잔량%·auto-compact% 정규식) | `claude.py:61-65` | 이미 잔량%를 잡는다 |
| `tokens.parse_running_tokens` | 현재 응답 ↑/↓ 합(int) | `tokens.py:34` | 응답 단위 델타 |
| `tokens.step` / `_session_tokens` | 세션 누계(int) | `tokens.py:59`, `serverclaude.py:476` | 응답 peak 합 |
| `usagelog.aggregate` | (버킷×계정) 누계 | `usagelog.py:80` | 일/주/월 예산 판단 |
| `claude_perm_mode` | `auto`/`bypass`/`plan`/`default` | `claude.py:190` | 모드 절감 판단 |
| `claude_account` | 별칭 이메일/조직/플랜 | `claude.py:287` | 계정별 예산(엄격 — 아래) |
| `parse_reset_delay` | 리밋 해제까지 지연(초) | `claude.py:226` | 자동재개 타이밍 |

> **`claude_account` 엄격 검출(2026-06-07 오탐 수정)**: 화면 본문에는 코드·diff·git
> URL·예시 등 **계정과 무관한 이메일**이 흔하다(예: git SSH URL
> `git@github.com:user/repo` → 과거 상태줄·토큰로그에 `gi…@github.com` 으로 튐;
> `a@x.org`·`us…@example.com` 도 동류). 이제 **Claude UI 가 직접 그린 신뢰 신호에서만**
> 계정을 잡는다 — ① `<email>'s Organization`(계정/조직 표시, 최우선) ② 계정 라벨 **바로
> 뒤** 이메일(`Login:`/`Account:`/`Logged in as <addr>`, git SSH URL 제외). **신뢰 신호가
> 없으면 `None`** → `usagelog` 가 `unknown` 으로 묶는다(잘못된 계정 표시보다 Unknown 이
> 옳다 — 사용자 지시). 맨 이메일(라벨 없는)·예약 도메인은 더 이상 계정으로 잡지 않는다.
> 회귀: `test_claude_account_rejects_screen_emails`.
>
> **2026-06-12 추가 정밀화(p4 58538)**: ③ 라벨 기반 조직/팀명·플랜(비이메일 약신호)도
> 제거 — Claude 산문/도구 출력 구절(실측 사례 "Running 1 shell command")을 계정으로
> 오검출해 존재하지 않는 계정이 토큰을 쓴 것처럼 보였다. 이제 계정 신호는 **이메일 기반
> ①②뿐**이고, 기존 오염 데이터는 usagedb **v4 마이그레이션**('@' 없는 비-unknown 계정
> → unknown 정정, 원값 `usage_acct_fixlog` 보존)이 connect 시 일괄 정정한다. 미식별
> (unknown) 레코드는 식별 계정이 하나뿐이면 표시층이 그 계정에 귀속한다(p4 58540 —
> reconcile §5.5·footer 계정합계와 동일 가정, 스코프 줄에 '미식별 포함' 명시).
>
> **과거 로그 정리**: 검출 수정 전 `*.tokens.jsonl` 에 적힌 옛 오탐 계정은
> `scripts/migrate_token_accounts.py` 로 일괄 `unknown` 처리한다 — 신뢰 allowlist
> (`--keep <account>`/`--keep-domain <domain>`)만 남기고 나머지를 unknown 으로(기본
> 드라이런, `--apply` 로 적용, `<path>.bak` 백업·원자 교체). 회귀
> `test_migrate_token_accounts`. **주의**: 검출 수정은 서버 데몬 재기동(restart-all/
> kill-server) 후에야 로드되므로, 재기동 전까진 옛 코드가 새 오탐을 계속 적을 수 있다.

### 2.2 개입(actuation) — PTY 주입

| 함수 | 동작 | 근거 | 가역성 |
|---|---|---|---|
| `_pc_inject(pane, text)` | 한 줄 + Enter 제출 | `serverclaude.py:146` | 내용에 따라 다름 |
| `_inject_keys(pane, data)` | raw 키 바이트(Enter 없음) | `serverclaude.py:308` | shift+tab=`\x1b[Z` 등 |
| `_pc_advance` | doc→`/clear` 상태기계 1단 전진 | `serverclaude.py:213` | `/clear`=**비가역** |
| `_adc_arm`/`_adc_fire` | idle N초 후 doc→clear 무장/발화 | `serverclaude.py:260,273` | 시간 기반 트리거 |
| `_fire_resume` | 리밋 해제 후 `continue` 주입 | `serverclaude.py:67` | 발화 게이트 보유(#6) |
| `_maybe_auto_mode` | 권한모드 auto 로 순환 | `serverclaude.py:318` | 가역(다시 순환) |
| `_drive_perm_mode` | 임의 목표 모드로 폐루프 구동 | `serverclaude.py:341` | 가역 |
| `_reset_token_session` | `/clear` 시 토큰 세션 경계 끊기 | `serverclaude.py:202` | 회계 정합(#5) |

### 2.3 정책 진입점 — `_scan_claude`

핵심 폐루프는 `_scan_claude`(`serverclaude.py:383`) 하나다. 서버 flush 루프가 매
프레임(≈30Hz) 호출하며, **모든 탭의 모든 패널**을 훑어 상태·사용량·토큰·전이를 갱신하고
busy→idle 경계에서 doc/clear/권한모드 상태기계를 전진시킨다. 새 절감 정책도 **여기에
훅을 추가**하는 형태가 자연스럽다(단, dirty 게이팅 B1 `serverclaude.py:403` 과 충돌하지
않게 — 화면 불변 프레임은 스캔을 건너뛰므로 시간 기반 디바운스는 pending 표시로 살려야
한다, §5.6).

### 2.4 아직 안 쓴 두 표면 — pytmux 고유의 적극 개입 여지

지금까지의 전략(T1~T4)은 모두 **한 패널을 PTY 로 보고 PTY 로 친다.** 그러나 pytmux 에는
**아직 절감에 안 쓰는 두 표면**이 있고, 둘 다 pytmux 만 할 수 있는 개입이다.

**(A) 전 세션 동시 시야 (cross-session vantage).** `_scan_claude` 의 순회는 포커스
패널이 아니라 **모든 탭·모든 패널**이다(`serverclaude.py:407-409` `for t in sess.tabs / for
p in w.panes()`). 더 나아가 서버는 **계정별 토큰 누계를 이미 전역으로 합산**한다 —
`_account_token_total`(`serverclaude.py:640-653`)이 `_all_panes()` 를 돌며 같은
`_claude_account` 의 `_session_tokens` 를 합한다. **단일 Claude 세션은 자기 컨텍스트만
보지만 pytmux 는 켜진 모든 세션을 동시에 본다** — 이게 pytmux 의 가장 고유한 절감 자산이다.
공백은: 이 전역 합계가 **표시(헤더 계정합계)로만 쓰이고**, 정작 개입 결정은 **패널 단독
수치**를 본다 — `_budget_over`/`_budget_level_for`(`serverclaude.py:817·828`)는 세션 예산을
`pane._session_tokens`(그 패널만)로 판정한다. 같은 계정으로 5개 세션을 돌리면 각 패널은
패널-예산 밑이라 무개입인데 **계정 합계는 한참 초과**일 수 있다(→ T5).

**(B) PTY 밖 알림 채널 (out-of-band).** §5.1 의 "등급 0 알림만"은 지금 **화면(헤더/상태줄)
그리기뿐**이다. 그런데 토큰 낭비가 가장 많이 쌓이는 때는 **사용자가 화면을 안 볼 때**(잊힌
idle 세션·자리 비움)라, 화면 알림은 그 순간 무력하다. pytmux 엔 이미 **PTY 밖 구동 채널**이
있다 — 벨(BEL) 수신 시 `_fire_hook("alert-bell")`(`client.py:558-559`)로 사용자가 바인딩한
임의 셸 명령을 실행한다(`self.hooks`, `client.py:480-483`). 즉 데스크톱 알림·푸시·소리로
**자리를 비운 사용자에게 닿는 경로가 이미 존재**하는데 절감 신호엔 안 쓰인다(→ T6).

| 표면 | 이미 있는 것 | 근거 | 빠진 것 |
|---|---|---|---|
| 전 세션 시야 | 전 패널 순회·계정별 누계 합산 | `serverclaude.py:407,640` | 개입 결정이 **패널 단독** 수치를 봄(전역 미연동) |
| PTY 밖 알림 | 벨→사용자 훅(임의 셸) | `client.py:558,480` | 절감 이벤트용 훅 이벤트가 없음(화면 알림만) |

---

## 3. 토큰 과사용 시나리오 분류 (효과 큰 순)

| # | 시나리오 | 토큰 동력 | pytmux 가 보는 신호 | 값싼 개입 |
|---|---|---|---|---|
| **S1** | **컨텍스트 팽창** | 턴마다 거대 컨텍스트 재전송, auto-compact 임계서 급증 | `ctx N%`/auto-compact% (`claude.py:61-65`) | 잔량 낮으면 doc→`/clear` 로 컨텍스트 비우기 |
| **S2** | **맹목 자동재개** | 리밋 해제 후 무조건 `continue` → 또 한도까지 태움 | `claude_state==limit`, 세션/일 누계 | 예산 초과 시 재개 **보류**·알림 |
| **S3** | **게이트 없는 권한모드** | bypass/auto 가 확인 없이 도구 반복 호출 | `claude_perm_mode` | 예산 압박 시 `plan`/`default` 로 유도 |
| **S4** | **모델 과선택** | Opus 로 단순 작업 | 모델 배지(`1M context` 등, `claude.py:73`) | 알림(모델 변경은 사용자 슬래시) |
| **S5** | **잊힌 idle 세션** | 큰 컨텍스트를 든 채 방치 → 다음 입력이 비쌈 | `idle` 지속 + 높은 누계 | idle N분 후 doc→`/clear`(기존 auto-doc-clear) |
| **S6** | **절감 자동화 자체 비용** | doc 작성·`/clear` 자체가 토큰 소모 | (메타) | 빈도 상한·idle-only·중복 억제 |
| **S7** | **멀티세션 누적**(신규) | 같은 계정 N개 세션이 각자 큰 컨텍스트·각자 비싼 턴 | 계정별 전역 누계(`serverclaude.py:640`)·전 패널 idle | **계정 합계** 예산 게이트·가장 비싼 idle 세션부터 정리 |
| **S8** | **반복 실패 루프**(신규) | 같은 실패 명령/도구를 매 턴 재시도 → 턴마다 풀 컨텍스트 재전송 | busy→idle 완료마다 화면 꼬리 동일 반복 | 동일 출력 N회 반복 감지 → **알림**(개입은 사용자) |
| **S9** | **비정상 장기 턴**(신규) | 한 턴이 폭주(에이전트 루프 고착)해 길게 태움 | busy 지속 시간(타임스탬프) | 임계 초과 busy → **알림**(취소는 사용자 ESC) |

> S1 이 압도적으로 크다(매 턴 비용을 좌우). S6 는 **절감 전략의 역설** — 정리 자체가
> 토큰을 쓰므로, 자주 돌수록 손해다. 따라서 모든 정리 트리거는 "**지금 정리하는 게
> 앞으로 아낄 토큰보다 싼가**"를 근사하는 임계(잔량%·idle·빈도 상한)를 가져야 한다.
> **S7~S9 는 §2.4 의 "아직 안 쓴 두 표면"에서 직접 나온다** — S7 은 전 세션 시야(A),
> S8·S9 는 단일 세션이 자기 화면만 봐선 못 잡고 pytmux 의 외부 관측이 필요한 패턴이다.
> S8·S9 의 개입은 **전부 등급 0(알림)** — 오탐 비용이 비싸 자동 비가역으로 올리지 않는다.

---

## 4. 개입 전략별 안전 자동화 설계

### [핵심] T1 — 컨텍스트 잔량% 기반 자동 정리 (S1)

**문제**: `claude_usage` 가 `ctx N%` 를 이미 파싱하지만(`claude.py:98-101`) 결과는 표시
문자열로만 쓰여(`serverclaude.py:425-431`) 헤더에 그려질 뿐, **발화 조건에 안 쓰인다.**
현행 자동 정리(auto-doc-clear)는 **시간 기반(idle 30초, `server.py:86`)** 뿐이라 "컨텍스트가
얼마나 찼는지"와 무관하게 발화한다 — 짧은 작업 뒤에도 정리하거나(S6 낭비), 컨텍스트가
거의 다 찼는데 사용자가 화면을 보고 있어 idle 타이머가 안 도는 채 다음 비싼 턴으로
넘어간다.

**설계**:
1. `claude.py` 에 **숫자 파서** 추가 — `claude_context_pct(text) -> int|None`. 기존
   `_CTX_PCT_RES`(`claude.py:61-65`)를 재사용하되 **"잔량%"와 "사용%"의 의미를 명확히**
   한다(Claude 표기가 "context left 18%" 인지 "82% used" 인지에 따라 반대 — §7 골든
   픽스처로 확정 필요). 순수 함수라 단위 테스트가 쉽다.
2. `_scan_claude` idle 분기(`serverclaude.py:542~`)에서, `claude_auto_compact_below`
   (신규 opt, 예: 15%) 임계를 **잔량이 밑돌면** 기존 `_adc`/`_pc_advance` 기계로 doc→
   `/clear` 시퀀스를 발화한다. **새 주입 경로를 만들지 않고** 시간 트리거(`_adc_arm`)와
   동일한 상태기계·동일한 토큰 세션 리셋(`_reset_token_session`, #5)을 탄다.
3. 시간 트리거와 잔량 트리거는 **OR** — 둘 중 하나만 충족해도 무장. 단 §5 의 게이트를
   공유(idle 안정·최근입력 없음·busy 복귀 시 취소).

**안전 게이트**: 발화 직전 `claude_state==idle` 재확인(`_adc_fire` 패턴, `serverclaude.py:280-288`),
busy 복귀 시 `_adc_disarm`(`serverclaude.py:539`), 최근 사용자 입력 후 X초 미만이면 연기,
헤더에 카운트다운/취소 힌트(§5.3). 잔량%는 best-effort 라 **파싱 실패(None)면 시간
트리거로 폴백**(절대 0% 로 오해해 발화 금지).

**위험**: 중. `/clear` 는 비가역. 잔량%의 의미(잔량 vs 사용) 오독이 치명적 →
**골든 픽스처 필수**(§7). **효과**: S1 직격 — auto-compact 직전 정리로 가장 비싼 턴을
회피. **노력**: 소(파서 1개 + 스캔 분기 1개, 기계는 재사용).

### T2 — 예산/임계 알림 + 자동재개 하드 게이트 (S2)

**문제**: `usagelog.aggregate`(`usagelog.py:80`)가 일/주/월×계정 누계를 내지만 **예산
초과 경고가 없어** 수동 조회(InfoScreen)에 머문다. 더 위험한 건 **자동재개가 예산을
모른다는 것** — `_fire_resume`(`serverclaude.py:67`)는 limit 상태만 재확인하고 `continue`
를 넣어, 한도 해제될 때마다 무한히 또 한도까지 태울 수 있다(S2).

**설계**:
1. opt `token_budget_day`/`token_budget_session`(신규, 0=무제한). 서버가 토큰 확정
   이벤트(`step()` committed>0, `serverclaude.py:470`)마다 라이브 누계와 비교.
2. **알림 단계**(가역, 안전): 임계의 80%/100% 도달 시 상태줄/헤더에 경고 표기만. 동작
   변경 없음 — 단계적 도입(§5.4)의 첫 칸.
3. **자동재개 게이트**(비가역에 준함): 예산 100% 초과 시 `_fire_resume` 가 주입 대신
   **보류 + 알림**("일일 예산 초과 — 재개 보류, 수동 continue 가능"). 자동재개를 끄는
   게 아니라 **예산이 회복(날짜 경계)될 때까지 미루는** 것.

**안전 게이트**: 알림→게이트 순서로 도입(처음엔 알림만). 예산은 사용자가 명시 설정해야
활성(기본 0=무제한)이라 **옵트인**. 누계는 best-effort(화면 토큰 합)라 **하드 차단이
아니라 "자동 개입만 보류"** — 사용자는 언제든 직접 재개 가능.

**위험**: 낮~중. **효과**: S2 의 무한 태움 차단 + 사용자 가시성. **노력**: 중(예산 opt +
조회/알림 배선).

### T3 — 권한모드·모델 절감 유도 (S3·S4)

**문제**: bypass/auto 모드는 확인 없이 도구를 반복 호출해 토큰을 빨리 태운다(S3).
pytmux 는 이미 권한모드를 임의 목표로 구동할 수 있다(`_drive_perm_mode`,
`serverclaude.py:341`) — 지금은 "auto 로 맞추기"에만 쓴다(반대 방향).

**설계**:
1. **예산 압박 연동**(opt-in): T2 예산 80% 초과 + idle 이면 `_drive_perm_mode(p, txt,
   "plan")` 로 **plan 모드 유도**(편집 전 검토 → 맹목 도구 호출 감소). 기존 폐루프
   재사용, 가역(사용자가 shift+tab 으로 되돌림).
2. **모델 과선택(S4)은 알림만.** 모델 전환은 슬래시 명령(`/model`)이지만 pytmux 가
   자동으로 모델을 바꾸는 건 작업 의미를 바꿀 위험이 커 **권장하지 않는다** — 모델
   배지(`claude.py:73`)로 "Opus + 큰 잔량 + 단순 반복" 패턴을 감지하면 **헤더 힌트**만.

**안전 게이트**: 권한모드는 본디 가역이라 위험이 낮지만, **bypass 는 절대 건드리지
않는다**(명시적·사용자 의도 — 기존 원칙 `claude.py:205`, `serverclaude.py:328` 유지).
plan 유도는 opt-in 이고 idle 한정.

**위험**: 낮음. **효과**: 중(S3 churn 감소). **노력**: 소(기존 구동 재사용 + 예산 훅).

### T4 — 자동재개 안전화 (S2 보강, 일부 구현됨)

기존에 **발화 게이트(#6)는 이미 있다** — `_fire_resume` 가 발화 직전 `limit` 재확인
(`serverclaude.py:76`)으로 사용자가 이미 재개한 작업을 안 망친다. 남은 보강:
- **예약 취소 경로**: busy 복귀 시 예약된 `call_later`(`serverclaude.py:65`) 취소(현재는
  발화 시점 재확인만 — 핸들을 패널에 들고 있다가 busy 전이에서 cancel).
- **카운트다운/취소 힌트**: 헤더에 "리밋 해제까지 N분 → 자동재개" 표기 + ESC 취소.
- **T2 예산 게이트** 결합.

**위험**: 낮음(게이트 추가만). **효과**: S2 오작동 제거 + 가시성.

### [신규] T5 — 계정 합계 예산 + 멀티세션 우선순위 정리 (S7)

**문제**: 예산 게이트가 **패널 단독**을 본다(`_budget_over` → `pane._session_tokens`,
`serverclaude.py:817-824`). 그런데 토큰은 **계정 단위로 청구**되고, pytmux 는 이미 계정
합계를 계산해 둔다(`_account_token_total`, `serverclaude.py:640-653`) — 다만 **헤더 표시
에만** 쓴다. 같은 계정으로 5개 세션을 띄우면 각 패널은 패널-예산 밑이라 아무 개입이 안
걸리는데 **계정 합계는 이미 한도 초과**일 수 있다. pytmux 만이 이 전역 그림을 본다.

**설계**:
1. **예산 판정을 전역으로 승격**: `_budget_level_for`/`_budget_over` 가 세션 예산을 볼 때
   `pane._session_tokens` 대신(또는 그와 더불어) `_account_token_total(pane)` 을 쓰는
   **opt `token_budget_account`**(신규, 0=무제한). 기존 함수에 인자 한 줄 — 새 수집 경로
   없음(합계 함수 재사용).
2. **우선순위 정리**: 계정 합계가 임계 초과 + 여러 세션이 idle 이면, **가장 컨텍스트가 찬
   (잔량% 최저) idle 세션부터** T1 정리를 적용. 전 패널 순회는 이미 `_scan_claude` 안에
   있으니, idle 후보를 잔량% 오름차순 정렬해 1개만 무장(한 프레임 1발, §5.6 빈도 상한 공유).

**안전 게이트**: T1·T2 게이트 전부 상속(idle 안정·최근입력 없음·발화직전 재확인·빈도 상한).
정리 대상은 **idle 세션만**(busy 세션 불간섭). 합계도 best-effort(화면 토큰 합)라 **하드
차단 아님** — 자동 개입(정리/재개보류) 보류용. **옵트인**(기본 0).

**위험**: 중(T1 과 동일 — `/clear` 비가역). **효과**: S7 직격 — 멀티세션 환경에서 패널-
단독 예산이 못 막는 합계 초과를 차단, 가장 비싼 세션부터 값싸게 정리. **노력**: 소(합계
함수 재사용 + 예산 판정에 인자 1개 + idle 정렬 1개).

### [신규] T6 — PTY 밖 에스컬레이션 (자리 비움 대응, S2·S5·S7 보강)

**문제**: 등급 0 "알림"이 **화면 그리기뿐**이라(§5.1), 정작 토큰이 새는 순간(잊힌 idle·
자리 비움)엔 사용자가 화면을 안 봐서 무력하다. pytmux 엔 이미 **PTY 밖 채널**이 있다 —
`_fire_hook(event)`(`client.py:480-483`)가 `self.hooks` 의 사용자 바인딩 셸 명령을 실행하고,
벨에서 `alert-bell` 로 이미 발화한다(`client.py:558-559`).

**설계**:
1. **새 훅 이벤트** `claude-budget`(예산 80/100% 도달)·`claude-auto-action`(무장된 비가역
   자동액션 발화 직전). 서버가 status 로 보내는 기존 `claude_pending`(M14b)·예산 레벨
   (M10) 전이에 클라가 훅을 거는 형태 — **새 신호 없이 기존 status 전이에 훅만 추가**.
2. 사용자는 `set-hook claude-budget 'terminal-notifier -message "예산 80%"'` 식으로
   데스크톱 알림·푸시·소리에 자유롭게 바인딩(기본 미바인딩 = 무동작).

**안전 게이트**: 등급 0(부작용은 사용자가 건 명령에 한정, pytmux 는 이벤트만 발화).
기본 미바인딩이라 **옵트인**. 화면 알림과 **중복이 아니라 보완**(자리 비움 시 유일한 경로).

**위험**: 낮음(알림만, 사용자 바인딩). **효과**: 중(자리 비움 시 절감 신호가 비로소 닿음).
**노력**: 소(훅 이벤트 2개 + 기존 status 전이에 발화점).

### [신규] T7 — 반복 실패·장기 턴 감지 알림 (S8·S9)

**문제**: 단일 Claude 세션은 자기 출력을 "처음 보는 것"으로 다루지만, pytmux 는 **여러
완료를 가로질러** 같은 실패가 반복되는지 본다. 또 한 턴이 비정상적으로 길게 도는지(폭주)도
본다. 단 — **이 둘을 잡는 1차 함수는 아직 없다**(정직하게: `_feedback_seen`,
`serverclaude.py:427` 은 특정 프롬프트 1종 디바운스일 뿐 일반 반복 감지가 아니고, busy
**지속시간 타임스탬프**도 없다 — `_idle_frames` 는 프레임 카운터지 경과시간이 아니다,
`model.py:395`).

**설계**(둘 다 **신규 1차 함수**가 필요하지만 작다):
1. **S8 반복 감지**: busy→idle 완료 경계마다 화면 **꼬리 N줄의 해시**를 패널에 누적,
   직전 K개 완료의 해시와 동일하면 카운트. `_repeat>=임계`면 헤더 알림 "동일 결과 N회
   반복 — 루프 의심". **알림만**(개입은 사용자) — 오탐해도 비용 0.
2. **S9 장기 턴**: busy 진입 시 `_busy_since = monotonic()` 1줄 추가, `now-_busy_since >
   임계(opt, 예 600초)`면 헤더 알림 "이 턴이 N분째 — 폭주 가능". 역시 알림만.

**안전 게이트**: 둘 다 **등급 0** — pytmux 가 자동으로 ESC/취소를 치지 **않는다**(폭주처럼
보이는 정상 장시간 작업을 죽일 위험). 디바운스(1회 알림, §5.6)로 매 프레임 반복 금지.

**위험**: 낮음(알림만). **효과**: 중(S8·S9 가시화 — 사용자가 손으로 끊게). **노력**: 소~중.
**상태**: ✅ 구현 — `claude.py` `screen_tail_key`/`track_repeat`(순수, 단위테스트), 스캔
완료 경계에서 반복 카운트(S8)·`_busy_since`로 장기 턴(S9), `_claude_warn`을 status 로
실어 상태줄 ⚠배지(grade0). 임계는 모듈 상수(`_LONG_TURN_SEC`=600·`_REPEAT_ALERT`=3) —
opt 화는 후속.

---

## 5. 안전 자동화 원칙 (이 문서의 핵심)

개입을 자동화할 때 위험은 **오탐 시 비가역 동작**이다. 아래 원칙을 모든 전략이 공유한다.

### 5.1 비가역성 등급 — 등급이 높을수록 게이트를 두텁게
| 등급 | 예 | 필요한 게이트 |
|---|---|---|
| **0 알림만** | 헤더 경고, 카운트다운 | 없음(부작용 0) |
| **1 가역 주입** | 권한모드 순환(shift+tab), 모델 힌트 | idle 한정·디바운스 |
| **2 준비된 비가역** | doc 작성(컨텍스트 추가) | idle 안정 + 최근입력 없음 |
| **3 비가역** | **`/clear`**, `continue`(예산 태움) | 발화직전 재확인 + 취소창 + 빈도상한 |

> 새 자동화는 **가능한 한 낮은 등급으로** 설계한다. 같은 효과를 알림(0)으로 낼 수 있으면
> 자동 동작(2/3)을 만들지 않는다 — 단계적 도입(5.4)의 근거.

### 5.2 발화 직전 재확인 (fire-time re-check)
예약과 발화 사이에 상태가 바뀐다(사용자가 끼어듦). **타이머 콜백은 발화 직전 조건을
다시 본다** — `_fire_resume`(limit 재확인, `serverclaude.py:76`)·`_adc_fire`(idle·진행중·
토글 재확인, `serverclaude.py:280-288`)가 이미 이 패턴. 모든 신규 트리거가 따라야 한다.

### 5.3 사용자 우선권 (preemption)
사용자 입력·busy 복귀는 **항상 자동 개입을 취소**한다. busy 이탈 시 `_adc_disarm` +
`_cam_tries` 리셋(`serverclaude.py:538-541`)이 이미 그 골격. 최근 입력 후 X초 미만이면
"사용자가 작업 중"으로 보고 **연기**한다(비가역 `/clear` 가 사용자가 읽는 중에 터지는
사고 방지, IMPROVEMENT §3.3 진단).

### 5.4 단계적 도입 사다리 (observe → notify → suggest → auto)
새 정책은 **관측 → 알림 → 제안 → 자동**의 순서로만 켠다. 각 칸이 다음 칸의 신뢰
근거다. 예산(T2)·잔량 정리(T1) 모두 **첫 릴리스는 알림(등급 0)** 으로 내고, 골든
픽스처로 정확도가 검증된 뒤에야 자동 동작(등급 3)을 기본 OFF opt 로 추가한다.

### 5.5 오탐 비용 비대칭 → 미동작 편향
잘못된 `/clear`(작업 손실)·잘못된 `continue`(예산 태움)는 비싸고, 정리 한 번 거른
비용은 싸다. **불확실하면 동작하지 않는다.** 구체적으로: 잔량% 파싱 실패(None)는
0%로 오해하지 말고 발화 보류(§4 T1), 누계는 하드 차단이 아니라 자동 개입만 보류(§4 T2).

### 5.6 멱등·디바운스·빈도 상한
화면 스크래핑은 같은 화면을 여러 프레임 본다 — **한 트리거는 사라질 때까지 1회만**
(`_feedback_seen` 디바운스 패턴, `serverclaude.py:412-420`). 정리는 **세션당/시간당 빈도
상한**을 둬 S6(자동화 자체 비용)을 막는다. dirty 게이팅(B1)과 충돌하지 않게, 시간 기반
디바운스는 pending 표시로 살린다(`serverclaude.py:400-403`).

### 5.7 휴리스틱 포맷 결합 가시화
모든 감지는 현행 Claude UI 포맷 가정이다(`claude.py` 정규식). 포맷이 바뀌면 **조용히
멈추는 게 가장 위험**(IMPROVEMENT §3.7) → "리밋/잔량 신호를 오래 못 봄" 같은 **감지
실패를 헤더 경고로 가시화**하고, 정규식은 **실제 화면 골든 픽스처로 회귀 고정**(§7).

---

## 6. 단계적 구현 로드맵 (제안 마일스톤)

기존 진행(M1~M7, IMPROVEMENT §진행현황)을 잇는 번호. 각 단계 `tests/run.py`(현재 17개
테스트 파일) 통과 + 골든 픽스처 추가가 머지 조건.

| MS | 내용 | 등급 | 위험 | 핵심 파일 | 상태 |
|---|---|---|---|---|---|
| **M8** | 골든 픽스처(합성, 실캡처 보강 TODO) + `claude.py` 회귀 고정 | — | 낮음 | `tests/fixtures/claude/*`, `tests/test_token_saver.py` | ✅ |
| **M9** | `claude_context_pct` 숫자 파서(잔량=headroom, 작을수록 참) + 단위 테스트 | 0 | 낮음 | `claude.py:114` | ✅ |
| **M10** | 예산 opt(일/세션) + 누계 추적 + **알림만**(상태줄 ⚠예산 경고) | 0 | 낮음 | `server.py`(`_budget_track`), `serverclaude.py`(`_budget_level_for`), `clientwidgets.py` | ✅ |
| **M11** | 잔량%<임계 자동 정리(기본 OFF) — `/compact`(기본) 또는 doc→/clear(`_pc_advance` 재사용) | 3 | 중 | `serverclaude.py`(`_ctx_intervene`, `_scan_claude` 완료경계) | ✅ |
| **M12** | 자동재개 예약 취소 경로(`_cancel_resume`) + 예산 게이트(`_fire_resume`) | 3 | 낮음 | `serverclaude.py` | ✅ |
| **M13** | T3 권한모드 plan 유도(예산≥80%+idle, opt-in 토글, bypass 불간섭) | 1 | 낮음 | `serverclaude.py`(`_scan_claude` 권한구동) | ✅ |
| **M14a** | 정리 **빈도 상한**(time floor `claude_ctx_min_interval`, 기본 120초) — `_ctx_fired` 디바운스에 직교하는 시간 바닥(§5.6) | 3 | 낮음 | `serverclaude.py`(`_ctx_cap_ok`), `server.py`, 설정 팝업 | ✅ |
| **M14b** | 무장 자동액션 카운트다운/취소 힌트 UI(`claude_pending`) + 입력 시 자동재개 취소(§5.3) | 2 | 중 | `serverio.py`, `client.py` | ✅ |
| **M14c** | 모델 배지 파서 `claude_model`(T3) + **힌트 UI 완료**(2026-06-13): `model_overselect_hint` 순수함수(Opus 계열 + `_repeat_n`≥3 반복 + 잔량%≥40 여유면 "가벼운 모델 고려" 헤더 배지). **알림만 — 자동 전환 없음**(S4 설계). opt `claude_model_hint`(기본 OFF·`model-hint` 명령·설정 팝업 행). 서버 idle 완료 경계 평가→`claude_model_tip` 송출, 클라 secondary 톤 배지 | 1 | 낮음 | `claude.py`(`claude_model`/`model_overselect_hint`)·`servermixin.py`·`clientstatus.py`·`__init__.py`·`screens.py`·`tests/fixtures/claude/badge_1m.txt` | ✅ 파서+힌트 UI |
| **M15** | **계정 합계 예산 + 멀티세션 우선순위 정리**(T5) — `_budget_*` 가 `_account_token_total` 도 봄(opt `token_budget_account`). 계정 예산 초과 시 per-pane `_ctx_pct` 로 **가장 꽉 찬 idle 부터** 정리(`_is_fullest_idle`/`_account_over_budget`) | 3 | 낮음 | `serverclaude.py`·`model.py`(`_ctx_pct`)·`server.py`·`clientutil.py`·`client.py` | ✅ |
| **M16** | **PTY 밖 에스컬레이션 훅**(T6) — `claude-budget-warn/over`·`claude-auto-armed`·`claude-limit` 훅(클라 status 전이에 발화) | 0 | 낮음 | `claude.py`(`saver_hook_events`), `client.py`(status 핸들러·`_fire_hook` env); 서버 무변경 | ✅ |
| **M17** | **반복 실패·장기 턴 감지 알림**(T7) — 완료 꼬리 비교(S8 `screen_tail_key`/`track_repeat`)+`_busy_since`(S9). 상태줄 ⚠배지(grade0) | 0 | 낮음 | `claude.py`·`serverclaude.py`·`model.py`·`serverio.py`·`clientwidgets.py` | ✅ |
| **M18** | **상태줄 가시성 + 통합 팝업**(§9) — `ctx:N%/1M`·`Σ25k(7%/5h)` 표기 + 사용량존 클릭→통계, `[s]`→시나리오 토글, 5h 분모 설정행 | 0 | 낮음 | `claude.py`·`serverclaude.py`·`serverio.py`·`clientwidgets.py`·`clientscreens.py`·`client.py` | ✅ (esc-커서 키보드만 보류) |
| **M8보강** | 실 Claude 화면 골든 캡처 — busy/idle/badge_1m/ctx_low 는 REC 캡처로 실교체 완료. **limit/feedback/auto-compact 는 미수집**(녹화 중 미발생) | — | 낮음 | `tests/fixtures/claude/*`(README) | 🟡 부분(limit 등 잔여) |
| **M19** | **그림자 `/usage` 질의**(§10) — 숨은 대화형 claude 스크랩으로 실 세션(5h)/주간 한도·리셋 확보(방법 B; B2 기각). `usageprobe.query_usage`·`refresh_usage`·`parse_usage`·`claude-usage` 명령·`[u]`. 세션 % 실측이 §9.3 분모 대체. 라이브 검증 | 2~3 | 중 | `usageprobe.py`·`claude.py`(`parse_usage`)·`serverclaude.py`·`serverio.py`·`client*.py` | ✅ (수동; 주기 자동·실박스 확인 남음) |

> 순서 원칙: **감지 정확도(M8·M9)를 먼저 고정**한 뒤에야 비가역 자동화(M11)를
> 켠다(§5.4). M10(알림)은 위험 0. 모든 자동 개입은 **기본 OFF**, `token-saver` 팝업
> 으로 옵트인. ~~**남은 후속(M13·M14)**: plan 유도·모델 힌트(T3)~~(완료), 정리 빈도 상한,
> 카운트다운 UI, **실 Claude limit 화면 골든 캡처**(M8 의 가장 중요한 보강 — 현재
> 픽스처는 문서화 포맷 합성이라 실화면 검증은 미완, `tests/fixtures/claude/README.md`).
>
> **신규 제안(M15~M17, §2.4 의 두 표면에서 도출)**: M15 계정 합계 예산·멀티세션
> 우선순위 정리(T5, pytmux 의 전 세션 시야를 비로소 개입에 연결 — 대부분 재사용),
> M16 PTY 밖 에스컬레이션 훅(T6, 자리 비움 시 알림이 닿는 유일 경로 — 등급 0),
> M17 반복 실패·장기 턴 감지(T7, 단일 세션이 못 보는 패턴 — 등급 0, 작은 신규
> primitive). **우선순위 권고**: M16→M15→M17 — M16 은 위험 0·재사용이라 먼저,
> M15 는 멀티세션 사용자에게 효과 최대(다만 §5.4 사다리상 알림→자동 순으로 단계 도입).

### 구현 메모(설정·동작 요약)
- **설정 팝업**: `token-saver`(별칭 `claude-settings`·`token-settings`) → `ClaudeSaverScreen`.
  ●/○ 토글 + 정리방식/잔량임계/일·세션예산 프리셋 순환(Enter), ESC 닫기. 전역 opts,
  status 회신마다 권위값 갱신(`clientscreens.py` `_saver_screen` 훅). 행: 자동재개·
  예산재개보류·**예산압박plan유도(M13)**·잔량자동정리·정리방식·잔량임계·**정리빈도
  상한(M14a)**·auto-doc-clear·권한자동·프롬프트클리어·일예산·세션예산.
- **M11 발화 시점**: busy→idle(응답 완료) 경계 — 사용자가 타이핑 중이 아니고 다음
  비싼 턴 직전이라 가장 값싼 정리 시점. 디바운스(`_ctx_fired`)는 잔량이 임계+5%p 위로
  회복하거나 새 세션 시작 시 해제(compact 무효 시 매 응답 무한 정리 방지).
- **M11 우선순위**: 잔량 정리가 auto-doc-clear(시간 기반)보다 먼저(잔량 부족이 더 시급).
- **M14a 빈도 상한**: `_ctx_fired`(잔량 회복까지 1회)와 **직교**하는 시간 바닥. 정리가
  잔량을 못 늘리는 오검출·병적 진동에서 회복→재하락이 빠르게 반복돼도 `min_interval`
  초 안엔 재발화 금지(`_ctx_cap_ok`). 0=상한 없음. 발화 시 `_ctx_last_fire`(monotonic)
  기록, 새 세션/respawn 시 리셋. 설정 팝업 `정리 빈도 상한` 행(0/60/120/300/600초).
- **M14b 카운트다운/취소**: 서버가 무장된 자동 액션(자동재개 예약·auto-doc-clear
  타이머)의 `{kind, eta초}`를 status `claude_pending` 으로 싣고(`_pending_action`,
  타이머 `when()`−`loop.time()`), flush 루프가 ETA 정수 초 변동 시에만 status 재전송
  (1Hz 틱, `sess._pending_key` 디바운스). 클라 상태줄에 주황 배지 `⏳자동재개 12s
  (입력=취소)`. 비가역 동작 발화 전 발견성+취소권(§5.3·§5.4). **사용자 입력 시
  자동재개도 취소**(`_handle_input`→`_cancel_resume`, auto-doc-clear `_adc_disarm`
  과 대칭). 무장/해제 전이 시 배지가 즉시 뜨고 사라진다.
- **M10 누계**: 확정 토큰(`step` committed>0) append **전에** 추적(이중계산 방지). 기동
  시 로그에서 오늘 누계 시드(재시작 정합), 자정 넘김 0 리셋. best-effort(화면 토큰 합)라
  하드 차단 아님 — 경고 + 자동개입 보류용.

---

## 7. 검증 게이트

- **골든 픽스처**(M8 — 가장 중요): 실제 Claude Code 의 ① 리밋 화면, ② `ctx N%`/
  auto-compact 표시, ③ busy 스피너, ④ idle footer, ⑤ 모델 배지를 텍스트로 떠서
  `tests/fixtures/` 에 두고, `claude_state`/`claude_usage`/`claude_context_pct`/
  `parse_reset_delay` 가 그 위에서 기대값을 내는지 단언. **정규식이 실 화면에 맞는지의
  유일한 객관 근거**(현재 일부는 IMPROVEMENT §3.2 "검증 흔적 없음=추정").
- **상태기계 단위 테스트**: 잔량 트리거 발화/연기/취소 경로(idle 안정·최근입력·busy
  복귀)를 가짜 시계로 단언(기존 `_adc`/`_fire_resume` 테스트 패턴 확장).
- **회귀 불변**: `tests/run.py` 전부 통과 — 기존 자동화(autoresume·auto-doc-clear·
  perm-mode)는 동작 불변. 새 동작은 전용 테스트 추가.
- **실 박스 종단 검증**: 화면 주입은 실제 Claude 세션에서 한 번은 사람이 확인(헤드리스
  대리 불가 — WINDOWS_TESTING §3 와 동일 한계). 특히 비가역 `/clear` 발화는 실측 필수.

---

## 8. M16 / T6: PTY 밖 에스컬레이션 훅 [✅ 구현완료]

§6 의 제안 M15~M17 중 **M16 을 첫 구현 대상으로 확정**한다. 이유: ① 비가역성 **등급 0**
(부작용은 사용자가 건 명령에만 국한 — pytmux 는 이벤트만 발화) ② **새 메커니즘·새 서버
데이터 0**(전부 기존 자산 재사용) ③ 효과가 분명 — 토큰 낭비가 가장 많이 쌓이는 "사용자
자리 비움" 구간에서 화면 알림(현행 등급 0)이 무력한 공백을 메운다(§2.4 (B)·§5.1).

### 8.1 동기 — 왜 화면 알림으론 부족한가

§5.1 의 "등급 0 알림"은 현재 **헤더·상태줄 그리기뿐**이다. 그런데 절감이 가장 필요한
순간들 — 잊힌 idle limit 세션, 예산 초과, 비가역 자동액션(자동재개·잔량정리) 발화 직전 —
은 대부분 **사용자가 화면을 안 보는 때**다. 화면에만 그리는 알림은 그 순간 닿지 않는다.
pytmux 엔 이미 **PTY 밖 구동 채널**이 있다: `_fire_hook(event)`(`client.py:480-483`)가
`self.hooks` 의 사용자 바인딩 셸 명령을 실행하고, 벨에서 `alert-bell` 로 이미 발화 중이다
(`client.py:558-559`). 절감 신호용 훅 이벤트만 더하면 **데스크톱 알림·푸시·소리**로 자리
비운 사용자에게 닿는다.

### 8.2 설계 — 클라이언트 전용, 기존 전이 패턴 재사용

핵심: **새로 만들 것이 거의 없다.** status 메시지가 이미 필요한 신호를 전부 싣는다 —
`budget_level`(0/80/100, `serverio.py:170`)·`claude_pending`({kind, eta(초)}, `serverio.py:173`).
클라 status 핸들러(`client.py:529-560`)는 이미 **prev→cur 전이로 훅을 쏘는 골격**을 가졌다
(`after-new-window`는 창수 증가 에지, `alert-bell`은 `_prev_bell` 에지). 여기에 절감 전이
4종을 같은 방식으로 추가한다.

**새 훅 이벤트(상승 에지에서만 발화):**

| 이벤트 | 발화 조건(전이) | 비고 |
|---|---|---|
| `claude-budget-warn` | `budget_level` 0/80 미만 → **80** 도달 | 1차 경고 |
| `claude-budget-over` | `budget_level` 100 미만 → **100** 도달 | 예산 초과(자동재개 보류 동반 가능) |
| `claude-auto-armed` | `claude_pending` None → **{kind}** | 비가역 자동액션 무장(카운트다운 시작, §5.3 취소창과 동시) |
| `claude-limit` | 활성 패널 `claude_state` 비-limit → **limit** | 리밋 진입(자리 비움 시 재개 타이밍 통지) |

**전이 디바운스(§5.6) — 구현됨:** 전이 계산은 **순수 모듈 함수 `saver_hook_events(prev,
msg)`(`claude.py`)** 로 뺐다(PytmuxApp 은 함수 내부 정의라 import 불가 → 테스트 가능하게
분리). `prev` 는 `self._saver_prev = {"budget_level":0, "pending_kind":None, "limit":False}`
(클라 `__init__`). status 마다 현재값과 비교해 **상승 에지에서만 1회 발화**(하강·동일은
무시). status 핸들러는 `for ev, env in saver_hook_events(self._saver_prev, msg): self._fire_hook(ev, env=env)`
한 줄 — `alert-bell` 의 `_prev_bell` 에지 패턴과 동형이라 같은 화면을 여러 프레임 봐도
중복 발화하지 않는다.

**컨텍스트 전달 — 구현됨:** `_fire_hook` 는 훅 값을 **pytmux 명령**으로 실행한다
(`_run_command`). 셸 명령은 `run-shell "…"`(`client.py:2061` → `_run_shell` →
`subprocess.run`, `os.environ` 상속)으로 돈다. `_fire_hook(event, env)` 가 발화 직전
**`os.environ` 에 `PYTMUX_*` 를 잠깐 심고 finally 로 복원**하므로 그 안의 `run-shell`
subprocess 가 환경변수를 상속해 컨텍스트를 본다:
`PYTMUX_HOOK_EVENT`·`PYTMUX_BUDGET_LEVEL`·`PYTMUX_PENDING_KIND`·`PYTMUX_PENDING_ETA`·
`PYTMUX_ACCOUNT`(별칭, [[claude-scrape-false-positives]] 의 안전한 계정 표기 그대로).
민감정보(원문 이메일)는 넘기지 않는다 — `claude_account` 별칭만.

**설정(기존 그대로):** `set-hook <event> <cmd>`(`client.py:2386`)·`show-hooks`. 기본
미바인딩 = 무동작(옵트인). `<cmd>` 는 pytmux 명령이라 셸은 `run-shell` 로 감싼다. 예시:
```
set-hook claude-budget-over 'run-shell terminal-notifier -title pytmux -message 예산초과:$PYTMUX_ACCOUNT'
set-hook claude-auto-armed  'run-shell osascript -e display notification \"$PYTMUX_PENDING_KIND ${PYTMUX_PENDING_ETA}s\"'
set-hook claude-limit       'run-shell afplay /System/Library/Sounds/Glass.aiff'
```

### 8.3 안전성 — 등급 0

- **부작용 국한**: pytmux 는 이벤트만 발화하고 동작은 전적으로 사용자가 건 명령이다 —
  Claude/계정 상태를 바꾸지 않는다(§1 경계 안). 화면 알림과 **중복이 아니라 보완**.
- **옵트인**: 미바인딩이면 완전 무동작. 명령 실행은 best-effort(`_run_command`)라 실패해도
  무해.
- **오탐 영향 최소**: 잘못 떠도 "불필요한 알림 1회"일 뿐 비가역 동작 없음(§5.5 비대칭 —
  M16 은 오탐 비용이 가장 싼 칸이라 자동화 사다리 §5.4 의 첫발로 적합).

### 8.4 변경 표면 (구현 시)

| 위치 | 변경 | 신규/재사용 |
|---|---|---|
| `client.py` `__init__`(167 부근) | `_prev_budget_level`/`_prev_pending_kind`/`_prev_limit` 초기화 | 신규 3줄 |
| `client.py` status 핸들러(529-560) | 4개 전이 비교 + `_fire_hook` 호출(기존 패턴) | 재사용 |
| `client.py` `_fire_hook`(480) | 컨텍스트 환경변수 주입 한 줄 | 소폭 확장 |
| **서버** | **무변경**(`budget_level`·`claude_pending` 이미 송신) | — |

### 8.5 테스트 계획

- **전이 단언(단위)**: `budget_level` 시퀀스 `[0,80,80,100,80,100]` → `warn` 1회·`over`
  2회(80→100 두 번) 발화, 동일·하강은 미발화. `claude_pending` `None→armed→None→armed`
  → `auto-armed` 2회. `_prev_*` 디바운스 회귀.
- **환경변수 구성(단위)**: 이벤트별 env dict 가 기대 키·값(별칭 계정·eta)을 담는지.
- **실 박스 1회(사람)**: 실제 `terminal-notifier`/`osascript` 바인딩으로 알림이 뜨는지
  육안 확인(헤드리스 대리 불가, §7 마지막 항목과 동일 한계).

> **요약**: M16 은 "감지→발화" 정책 계층조차 새로 안 만든다 — 이미 송신되는 신호의 클라
> 측 **전이 에지에 기존 훅 메커니즘을 잇기만** 한다. 가장 안전한(등급 0) 고효용(자리
> 비움 대응) 첫 기능이라 §5.4 사다리의 첫 칸으로 확정한다. 후속은 §6 권고대로 M15(전 세션
> 합계 예산)→M17(반복·장기턴 감지).

---

## 9. 가시성 기능 — 상태줄 사용현황 표시 + 통계/설정 팝업 [✅ 구현]

절감 정책(§4·§8)은 "개입"이고, 이 절은 그 전제인 **가시성**이다. 사용자가 지금 얼마나
썼는지·5h 한도에 얼마나 가까운지를 한눈에 보고, 한 진입점에서 통계 확인과 시나리오
on/off 를 하게 한다. 세 기능 모두 **이미 있는 데이터·렌더·팝업·클릭존**에 얹는다.

### 9.1 동기 — 현행 표시의 세 공백

상태줄은 지금 `claude_usage`(claude.py:77)가 만든 `ctx 1M`(배지만)·`Σ 25k`(누계만)를
그린다(clientwidgets.py:990-1008). 공백: ① **세션 사용현황 부재** — `1M` 은 윈도우 크기일
뿐 "얼마나 찼는지"가 없다. ② **5h 한도 근접도 부재** — `25k` 만으론 Claude 의 5시간 롤링
한도에 얼마나 다가갔는지 모른다. ③ **진입점 분산** — 통계(TokenLogScreen, 사용량존 클릭
clientwidgets.py:1098-1104)와 시나리오 설정(ClaudeSaverScreen, `token-saver` 명령
client.py:1693·clientscreens.py:368)이 따로 떨어져 있다.

### 9.2 기능 A — 컨텍스트 사용%/윈도우: `ctx:10%/1M`

**목표**: 배지(윈도우)만이 아니라 **사용률 + 윈도우**를 같이 — `ctx:10%/1M`(공백 없는
콤팩트 포맷, 사용자 요청).

**설계**: `claude_usage` 가 이미 컨텍스트%(`claude_context_pct`, claude.py:114, headroom
의미 — §M9)와 배지(`_CTX_BADGE_RE`, claude.py:73)를 각각 잡는다. 둘이 **동시에** 잡히면
`ctx:{사용%}/{배지}` 로 합쳐 낸다. 사용% = Claude 표기의 의미 규약을 따르되(headroom
이면 `100−headroom`), **표기 의미는 골든 픽스처로 확정**(§7·§M8 — "left 18%" vs "82%
used" 반대 주의). 한쪽만 잡히면 현행대로(`ctx:23%` 또는 `1M ctx`) 폴백.

> Claude 가 화면에 %를 안 그릴 때의 보강: 서버가 `_session_tokens`(serverclaude.py:495)와
> 배지 숫자(1M=1e6)로 **근사 사용%를 계산**할 수 있으나, `_session_tokens` 는 응답 peak
> 누계라 라이브 컨텍스트 점유와 다르다(윈도우 초과 가능) → **근사값임을 명시(`~`)** 하고,
> Claude 가 그린 %가 있으면 항상 그쪽 우선(§5.5 미동작/보수 편향).

### 9.3 기능 B — 5h 한도 근접도: `25k (7% / 5h)`

**목표**: 토큰 누계에 **5시간 롤링 한도 대비 진행률**을 곁들임 — `25k (7% / 5h)`.

**정직한 제약(중요)**: pytmux 는 **Claude 의 절대 5h 토큰 상한(분모)을 모른다.** Claude 가
화면에 안 그리기 때문이다 — pytmux 가 아는 건 한도에 **걸렸을 때** 해제까지 지연
(`parse_reset_delay`, claude.py:290)뿐, 윈도우 총량이 아니다. 따라서 분모를 어디서 얻느냐가
설계의 핵심이고, 셋 다 best-effort 다:

1. **분자(롤링 5h 사용량)**: usagelog 레코드가 `ts`(epoch)를 가지므로(usagelog.py:32),
   `ts ≥ now−5h` 인 레코드 합으로 **5시간 롤링 누계**를 낸다(`aggregate` 의 시간창 변형,
   usagelog.py:80). 이건 확실히 계산 가능.
2. **분모(5h 상한)** — 우선순위:
   (a) 사용자 설정 opt `token_budget_5h`(신규, 기존 예산 opt 와 동일 골격 — set_token_budget
       client.py 패턴). 명시 설정이면 그 값.
   (b) **관측 학습**: limit 진입(`claude_state==limit`) 시점의 롤링 5h 누계를 상한 추정치로
       기록(여러 번 관측해 max/median). 노이즈 있으나 분모 없는 것보단 유의미.
   (c) **미상**: (a)·(b) 둘 다 없으면 **% 를 숨기고 `25k (5h)` 로만** — 윈도우는 표기하되
       분모를 지어내지 않는다(§5.5).
3. **표기**: `25k (7% / 5h)`. 한도 리셋이 예약돼 있으면(파싱된 reset) `5h→Nm`(남은 분)로
   대체 가능. % 색은 M10 예산 경고색 재사용(80%=⚠노랑, 100%=⚠빨강).

### 9.4 기능 C — 통합 사용량 팝업 + 시나리오 토글(상태줄에서 진입)

**목표**: 토큰 영역을 **클릭/터치** 또는 **esc(scroll)모드에서 커서로 가리키고 Enter** →
**사용량 통계 팝업**, 그 안에서 **과사용 완화 시나리오를 켜고 끔**.

**구현(최소안 채택 — 통계 팝업 + `[s]` 점프)**:
1. **진입(마우스/터치)**: 사용량존 클릭(`_usage_zone`, on_mouse_down clientwidgets.py:1090~)
   → TokenLogScreen. on_mouse_down 은 **상태줄 하단행 전용 위젯 핸들러**라 normal/esc/
   scroll **어느 모드에서든** 동작한다(앱 모드와 무관) — "클릭/터치로 가리켜 연다"를 충족.
2. **통계 → 시나리오 토글**: TokenLogScreen 에 **`[s]` 키**를 추가해 ClaudeSaverScreen
   (시나리오 on/off)으로 점프(clientscreens.py on_key, 힌트에 `[s]시나리오 설정`).
   "사용량 보고 → 그 자리에서 절감 on/off" 동선 충족.
3. **설정에 5h 분모 행 추가**: ClaudeSaverScreen 의 SAVER_ROWS 에 `budget_5h`(5시간 한도)
   cycle 행을 더해(clientutil.py) 9.3 의 분모를 GUI 로 설정 — `_saver_action`/`_saver_display`
   (client.py)·`set_token_budget(h5=…)`(serverclaude.py)·status `token_budget_5h` 로 영속.
4. **마우스 서브탭 + /usage 표시(개정)**: TokenLogScreen 상단에 **클릭 가능한 서브탭**
   `시간/일/주/월`(버킷 전환) + 버튼 `계정`·`/usage`·`시나리오` 를 둔다(`#tktabs`, on_click
   이 위젯 id 로 분기 — 키 h/d/w/m·a 와 동등). 활성 버킷은 강조(`tkbtab-active`). **M19
   `/usage` 실측 한도(세션 5h·주간 전모델/Sonnet % + 리셋)를 팝업 맨 위에 표시**하고,
   `/usage` 버튼/`[u]`/`claude-usage` 로 갱신한다(결과는 status `usage_limits` → 열린
   팝업의 `update_usage` 훅으로 즉시 반영). 자동 조회는 안 함(매 열람마다 숨은 claude
   기동은 과함 — 마지막 결과 표시 + 명시 갱신).
5. **키보드 전용 진입(esc-모드 커서+Enter)은 미구현**: 상태줄은 패널 내용이 아니라
   **크롬(chrome)**이라 copy/scroll 모드의 셀 커서가 그 위에 놓이지 않는다. 키보드만으로는
   `:token-log` 명령(또는 `token-saver`)을 쓴다. 마우스/터치는 모든 모드에서 동작하므로
   실사용 동선은 막히지 않는다(상태줄을 진짜 셀 커서 대상으로 만드는 건 별도 과제).

### 9.5 안전·degrade

- 전부 **표시/탐색 기능(등급 0)** — 개입 없음. 잘못 떠도 비용은 표시 오류뿐(§5.1).
- **분모 미상 시 % 를 지어내지 않는다**(9.3 (c)) — 틀린 7% 보다 무표기가 안전(§5.5).
- 모든 파싱은 best-effort(§5.7) — ctx%/배지/5h 누계 신뢰 저하 시 조용히 폴백하되,
  **장기 신호 부재는 §5.7 대로 가시화**(예: "5h 누계 산출 불가").
- 토글은 기존 `_saver_action` 경로라 권위값은 서버 opts(상태 회신마다 라벨 갱신,
  clientscreens.py `_saver_screen` 훅) — 팝업 위치만 바뀌고 동작·정합은 불변.

### 9.6 변경 표면 (구현됨)

| 위치 | 변경 | 비고 |
|---|---|---|
| `claude.py` `claude_usage` | %+배지 동시 시 `ctx N% / 1M` 슬래시 합성(A) + `ctx_window_tokens` 파서 | ✅ |
| `serverclaude.py` `_usage_text` | 배지-only 시 세션누계/윈도우로 `ctx ~N% / 1M` 근사(A) | ✅ |
| `serverclaude.py` `_tok5h_pct` + scan | 5h 근접도 %(분모=`token_budget_5h`/limit 학습 `_learned_5h_cap`)(B) | ✅ |
| `server.py`·`serverpersist.py` | `token_budget_5h` opt + 영속, `_learned_5h_cap` | ✅ |
| `serverio.py` `_status_msg` | `claude_usage`=`_usage_text`, `tok5h_pct`·`token_budget_5h` 필드, `set_token_budget(h5=)` | ✅ |
| `clientwidgets.py` | `tok5h_pct`→`Σ 25k (7% / 5h)` 곁들임, `token_budget_5h` 보관 | ✅ |
| `clientutil.py`·`client.py` | SAVER_ROWS `budget_5h` cycle 행 + `_saver_action/_display` | ✅ |
| `clientscreens.py` TokenLogScreen | `[s]`→ClaudeSaverScreen 점프 + 힌트(C) | ✅ |
| (esc-모드 셀 커서 Enter) | 상태줄=크롬이라 미구현 — 마우스/터치(모든 모드)+`:token-log` 로 대체 | — |

### 9.7 검증 (구현 테스트)

- **단위**: `test_claude` — `claude_usage` 슬래시 합성·`ctx_window_tokens`. `test_server`
  `test_status_usage_display_m18` — `_usage_text`(근사~/점유%/배지) + `_tok5h_pct`(미상
  None·설정·학습 분모). `test_token_saver` — `set_token_budget(h5=)` 영속.
- **골든 픽스처(§7)**: ctx% 표기 의미(headroom vs used) 확정은 §M8보강(실캡처)에 의존.
- **실 박스 1회(사람)**: 표기·팝업 진입(클릭)·`[s]` 점프·5h 분모 설정 반영 육안 확인.

> **요약**: 9.2·9.3 은 표시 합성(데이터는 이미 있음, 단 5h 분모만 정직히 best-effort —
> 설정 또는 limit 관측 학습, 미상이면 무표기), 9.4 는 흩어진 통계·설정을 토큰 영역 한
> 진입점으로 모으는 동선 개선. 전부 등급 0. **M18 구현 완료**(esc-커서 키보드 진입만 크롬
> 한계로 보류).

---

## 10. M19 — 그림자 `/usage` 질의: 실 사용량 한도 확보 [✅ 구현(방법 B, 수동 트리거)]

> **구현됨(2026-06-07)**: `pytmuxlib/usageprobe.py` `query_usage()` 가 `pty.openpty`+
> `subprocess`(close_fds·start_new_session — executor 스레드 fork 안전)로 **숨은 대화형
> `claude`** 를 띄워 `? for shortcuts` 까지 대기 → `/usage`+Enter 주입 → pyte 로 패널 렌더
> → `parse_usage` 스크랩 → kill. 서버 `refresh_usage()`(executor·35s 타임아웃)가 결과를
> `self._usage` 에 저장·broadcast. 트리거: `claude-usage`/`usage` 명령 또는 토큰로그 팝업
> `[u]`. `_tok5h_pct` 가 세션 실측 % 를 그대로 써 §9.3 분모 추정을 대체. 상태줄/팝업에
> 세션·주간 한도·리셋 표시(status `usage_limits`). **실측 검증**: 라이브에서 ~2–8초에
> `{session:2%@2pm, week_all:14%, week_sonnet:0%}` 확보. 남은 것: **주기 자동 질의 opt**
> (현재 수동만), 실 박스 무표시 최종 확인.

§9.3 의 **5h 분모 미상** 문제(분모를 추정·학습에 의존)를 **Claude 의 실측치로 정직히
해결**하는 방안. Claude Code 의 `/usage` 는 5시간/주간 사용량 한도와 **리셋 시각**을
보여준다(로컬 슬래시 — API 호출 아님이라 토큰 비용 ≈ 0). 핵심 요구는 **이 질의가 사용자
눈에 띄지 않아야 한다**는 것. 두 방법을 모두 검토하고 더 나은 쪽을 고른다.

> **상태**: **설계만** 기록. 구현은 등급 2~3(아래 리스크)이라 **사용자 승인 후 착수**.
> 또한 이 녹화엔 **실 `/usage` 패널이 없어**(검출된 "/usage"·"5-hour" 는 전부 이 세션
> meta=오염) **출력 포맷 픽스처가 없다** — 구현 전 실 `/usage` 출력 1회 캡처가 선행 조건.

### 10.1 공통 제약

pytmux 화면 = Claude TUI 출력 그 자체다. `/usage` 를 **사용자가 보는 패널**에 그대로 치면
입력박스·결과 패널이 pyte 에 그려져 사용자에게 렌더된다. "안 보이게"의 방법은 둘뿐이다 —
**(A) 보는 패널에 치되 렌더를 동결**하거나, **(B) 사용자가 안 보는 다른 세션에서 친다.**

### 10.2 방법 A — 현재 세션 그림자 질의(render-freeze in-pane)

pytmux 가 렌더 파이프라인을 소유하는 점을 이용:
1. 활성 Claude 패널의 현재 프레임 **스냅샷** → 그 패널의 화면 갱신 송신을 **동결**(사용자
   뷰 고정).
2. `/usage` 주입(`_pc_inject`) → Claude 가 usage 패널을 **서버측 pyte 에만** 그림.
3. pyte 스크랩 → `parse_usage()`(신규)로 5h%·주간%·리셋 시각 파싱.
4. `Esc` 주입 → 패널 닫고 원화면 복구.
5. 동결 해제(before=after 면 무틈).

- **게이트(필수, §5.3·5.5)**: **idle + 입력박스 비어 있음 + 최근 사용자 입력 없음**일 때만.
  동결 중 사용자 입력이 오면 **즉시 중단·복구**. 빈도 상한(예: 5h마다 1회).
- **리스크**: ⚠️**입력 오염(치명적)** — 입력박스에 작성 중 텍스트가 있으면 `/usage` 가 거기
  붙어 프롬프트가 깨짐(빈 입력 확정 필수). 동결 seam(스피너 등으로 before≠after 면 점프
  보임). 대화/모드 side effect 가능. busy 면 불가.
- **비용**: 가벼움(프로세스 추가 0). 정확히 그 계정·그 세션을 질의.

### 10.3 방법 B — 별도 숨은 세션(off-screen)

사용자에게 안 보이는 **숨은 Claude 인스턴스**에서 `/usage` 를 친다.
1. 서버가 **숨은 PTY**(어느 가시 윈도우에도 안 붙은 off-tree 패널)에 `claude` 기동.
2. ready 대기 → `/usage` 주입 → pyte 스크랩 → `kill`.

- **계정 매칭(핵심 근거)**: `/usage` 는 **계정-전역** 한도(5h/주간)를 보고한다 —
  세션 단위가 아니다. 따라서 **같은 로그인의 별도 세션도 동일 숫자**를 낸다. 숨은 세션이
  사용자 세션의 한도를 정확히 대신 조회할 수 있는 이유다.
- **완전 무표시**: 사용자 뷰에 전혀 안 뜸. 사용자 세션·입력·대화에 **무위험**.
- **변형 B2(가장 깨끗한 후보)**: 캡처에서 **`claude -p`(비대화 print 모드)** 사용이 확인된다
  (`captures/.../pane-5.log`). `claude -p`(또는 usage 서브커맨드)가 usage 를 낼 수 있으면
  **TUI·pane 자체가 불필요** — subprocess stdout 만 파싱하면 된다. 단 **슬래시 명령이
  print 모드에서 동작하는지 검증 필요**(미확인).
- **리스크**: 프로세스 기동 비용(시간·메모리), 숨은 pane **수명 관리**(off-tree 플럼빙 신규),
  세션 기동 자체의 미세 비용. (B2 면 pane 플럼빙 불필요 — 리스크 대폭 축소.)
- **비용**: 무거움(질의마다 프로세스). 단 **저빈도**(5h마다·온디맨드)면 수용 가능.

### 10.4 비교와 선택

| 기준 | A 현재 세션 동결 | B 숨은 세션 | B2 headless(`claude -p`) |
|---|---|---|---|
| **무표시성** | 동결 seam·복구 틈 잔존 | **완전 무표시** | **완전 무표시** |
| **사용자 세션 위험** | 입력 오염·side effect | **없음** | **없음** |
| **자원 비용** | 가벼움 | 무거움(프로세스) | 중간(단발 subprocess) |
| **구현 난이도** | 동결 플럼빙 | off-tree pane 플럼빙 | 가장 단순(있으면) |
| **선행 검증** | /usage 포맷 캡처 | /usage 포맷 캡처 | + print 모드 슬래시 동작 |

**B2 타당성 검증 결과 (2026-06-07, 실측 — `claude` v2.1.167):**
- ✅ **메커니즘 완벽**: `claude -p "/usage" --output-format json` 가 `/usage` 를 **로컬
  슬래시로 처리** — `num_turns:0 · input/output_tokens:0 · total_cost_usd:0 ·
  duration_api_ms:0 · model:"<synthetic>"`. **API 호출·토큰 0, ~30ms, TUI/pane 불필요.**
- ❌ **데이터 불완전(치명적)**: print 모드 출력이 **첫 줄 한 줄**뿐 —
  `"You are currently using your subscription to power your Claude Code usage"`. **5h/주간
  한도 %·리셋 시각은 안 나온다**(대화형 TUI 패널의 박스/바에서만 렌더; json·stream-json
  동일). 디스크 `~/.claude/stats-cache.json` 도 일별 토큰·누적비용·컨텍스트윈도우 등
  **이력 통계만**이고 5h/주간 한도·리셋은 없음.
- `claude` 서브커맨드에도 `usage` 류 없음(agents/auth/auto-mode/doctor/install/mcp/plugin/
  project/setup-token/ultrareview/update 뿐).

**→ B2 기각.** 무비용·headless 지만 한도 숫자를 못 뽑는다(print 모드가 /usage 를 한 줄로
자름). **확정: 방법 B(숨은 대화형 세션).** 실제 TUI 패널을 pyte 로 렌더해 스크랩해야 한도가
나온다 — pytmux 의 pyte 렌더·스크랩 강점을 쓴다. B 가 "완전 무표시·무위험"이고, 비용
(off-tree 숨은 pty + 대화형 기동)은 저빈도로 상쇄. A(현세션 동결)는 폴백.

**실 `/usage` TUI 패널 캡처 확보(2026-06-07, pane-2 REC, replay 렌더):**
```
 Settings  Status  Config  Usage  Stats
 Current session · Resets 5am (Asia/Seoul)
 ████▏   10% used
 Current week (all models) · Resets Jun 13 at 3am (Asia/Seoul)
 █████▉  14% used
 Current week (Sonnet only) · Resets Jun 13 at 3am (Asia/Seoul)
 0% used
```
→ `tests/fixtures/claude/usage.txt`. **핵심 수확**: 패널이 **세션(5시간) % 를 직접** 준다
(`Current session … N% used`) — §9.3 의 "5h **분모** 미상" 문제가 **소멸**한다(추정·학습
불필요, % 가 곧 답). 덤으로 **주간 한도(전모델/Sonnet) % + 리셋 시각**까지 나온다.

### 10.5 파싱·검증·안전

- **`parse_usage(text)`(신규)**: 위 패널에서 **세션(5h) % · 주간(전모델) % · 주간(Sonnet) %
  + 각 리셋 표기**를 뽑는다. 파싱 대상 행: `Current (session|week ...) · Resets <리셋>` 과
  그 아래 `N% used`. 리셋은 `5am`(세션, 당일 시각) / `Jun 13 at 3am`(주간, 날짜+시각),
  tz 괄호(`(Asia/Seoul)`). 실 골든 픽스처: `tests/fixtures/claude/usage.txt`.
- **연동(단순화)**: 세션 % 가 직접 나오므로 §9.3 `_tok5h_pct` 가 **추정·학습 분모를 버리고
  실측 % 를 그대로** 쓴다(`token_budget_5h`/`_learned_5h_cap` 폴백만 남김). 주간 한도·리셋
  시각은 상태줄/팝업에 추가 표시.
- **안전(등급 2~3)**: B 는 사용자 세션 무간섭이라 실질 위험은 "숨은 프로세스 기동"
  뿐(가역). A 를 쓸 경우만 §5.3 의 강한 게이트(빈 입력·idle·즉시 취소)가 필수. 빈도 상한
  공유(§5.6). 모든 파싱 best-effort(§5.7).
- **검증**: `parse_usage` 단위(실 픽스처 usage.txt) · 숨은 세션 lifecycle 통합테스트
  (기동→`/usage` 주입→스크랩→kill 정합) · 실 박스 1회(사람) 무표시 확인.

### 10.6 변경 표면(예상, 구현 시)

| 위치 | 변경 |
|---|---|
| `claude.py` | `parse_usage()` 파서(✅ 픽스처 usage.txt 확보) |
| `serverclaude.py` | 방법 B 오케스트레이션(숨은 대화형 PTY 기동→`/usage` 주입→스크랩→kill), 세션 % 를 `_tok5h_pct` 실측으로 |
| `serverpty.py`/`model.py` | off-tree 숨은 pane(대화형 claude) 생성·수명 관리 |
| `serverio.py` | 주간 한도(전모델/Sonnet)·리셋 시각 status 필드 |
| opts | 질의 주기·on/off(`token-saver` 행) |

> **요약**: "안 보이게"라는 핵심 요구에는 **현재 세션 주입(A)보다 숨은 세션(B), 그중에서도
> headless `claude -p`(B2)가 가능하면 B2 가 가장 안전·깨끗**하다. 구현은 ① 실 `/usage`
> 포맷 캡처와 ② B2 타당성 검증을 선행 조건으로 두고 **승인 후 착수**한다.

---

## 부록 — 무엇이 이미 있고 무엇이 빠졌나

**이미 보유**(재사용): 화면 감지 전부(§2.1)·주입 전부(§2.2)·발화직전 재확인(#6)·토큰
세션 경계 정합(#5)·시간 기반 auto-doc-clear·권한모드 폐루프 구동·영속 토큰 로깅/집계.

**빠진 것**(이 문서가 채우려는 것): ① **잔량%를 트리거로 잇는 정책**(T1, 최대 공백) ②
**예산 개념과 알림/게이트**(T2) ③ **자동재개 예약 취소 경로**(T4) ④ **감지 정확도의
객관 근거(골든 픽스처)**(M8) — 이상 M8~M14 로 구현됨. 추가로 **§2.4 가 드러낸 두 표면**:
⑤ **전 세션 시야를 개입에 연결**(T5/M15 — 합계는 이미 계산하나 개입은 패널 단독을 봄) ⑥
**PTY 밖 알림 채널**(T6/M16 — 벨 훅은 있으나 절감 신호엔 미사용) ⑦ **단일 세션이 못 보는
패턴 감지**(T7/M17 — 반복 실패·장기 턴). 핵심은 여전히 **새 메커니즘이 아니라 "감지→발화"
정책 계층**이며(T5·T6 은 거의 재사용, T7 만 작은 신규 primitive), 그 계층의 위험은
전적으로 **비가역 동작의 오탐**이라 §5 의 게이트와 §7 의 골든 픽스처가 자동화의 전제
조건이다. **새로 추가된 통찰**: pytmux 의 가장 고유한 절감 자산은 "한 패널을 잘 보는 것"이
아니라 **켜진 모든 Claude 세션을 동시에 보고, 자리를 비운 사용자에게 화면 밖으로 닿는
것**이다(§2.4 (A)·(B)) — 어떤 단일 Claude 세션도 못 하는 개입이다.

> 비채택/보류: **자동 모델 전환**(작업 의미 변경 위험 — 힌트만, §4 T3). **하드 토큰
> 차단**(화면 누계가 best-effort 라 오차로 정상 작업을 막을 위험 — "자동 개입만 보류"로
> 한정, §5.5). **API 직접 계측**(pytmux 의 경계 밖 — §1).
