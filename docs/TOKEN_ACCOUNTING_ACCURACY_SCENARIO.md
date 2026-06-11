# 토큰 회계 정확성 재설계 시나리오 (S6)

> **상태**: ✅ **구현 완료(2026-06-11, T1~T6)** — T1 스냅샷 이력화(p4 58089) ·
> T2 대사 리포트(58093) · T3 표시 1차화+분모 근사 폐기(58101) · T4 실측 게이트
> 기본 ON(58106) · T5 갱신 전략(58107) · T6 골든·계약 확장+본 문서 완료 표시.
> 전체 463 green. **§7-4 해소(2026-06-11)**: 절대 예산 `token_budget_*` deprecate
> 완료 — 일/세션/5h/계정 예산·M12 resume_gate 전부 제거, M13 plan 유도·M15 우선
> 정리·⚠ 경고 레벨은 실측 게이트(`_usage_gate_*`)로 일원화(구 opts.json 키는
> 로드 시 무시→다음 저장에서 자연 소멸하는 shim). 남은 후속:
> REC 실캡처 기반 대사([대사] 뷰) 상관 관찰. 라이브 반영은 restart-server 필요.
>
> 선행 조건: S5 토큰 모듈화
> T1~T6 완료([TOKEN_USAGE_MODULARIZATION_SCENARIO.md](TOKEN_USAGE_MODULARIZATION_SCENARIO.md)
> §6, p4 58071~58083)로 토큰 회계가 `plugins/claude-code/` 안에 완전히 갇혔다.
> 이 문서는 그 §8("수정 방향 옵션 — 모듈화 후 결정")의 본설계다.
>
> **동기**: 현재 기록·표시되는 토큰 누계가 Claude Code 앱(`/usage`)과 상당히 다르다
> ([MEMORY] `usage-panel-vs-app-mismatch`). 원인은 버그가 아니라 **출처의 의미 차이**다
> (§2) — 고치려면 회계의 1차 기준을 권위값으로 바꿔야 한다.
>
> 관련: [TOKEN_USAGE_STORAGE_DESIGN.md](TOKEN_USAGE_STORAGE_DESIGN.md)(SQLite 저장) ·
> [TOKEN_USAGE_UI_SCENARIO.md](TOKEN_USAGE_UI_SCENARIO.md)(팝업 UI) ·
> [TOKEN_SAVING_SCENARIO.md](TOKEN_SAVING_SCENARIO.md)(절감 자동화 M8~M14) ·
> [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md)(플러그인 계약) · [HANDOFF.md](HANDOFF.md) §11.6.

---

## 0. 핵심 결론 (먼저)

1. **두 출처는 의미가 달라 영원히 같아질 수 없다.** footer 스크랩(`↑/↓ N tokens`)은
   *현재 응답의 컨텍스트 streaming 수치*고, `/usage` 는 *과금 창(세션 5h·주간) 점유 %*다.
   "스크랩을 보정해 앱 숫자에 맞춘다"(S5 §8-B)는 모델 자체가 성립하기 어렵다.

2. **실측은 % 와 리셋 시각만 준다 — 절대 토큰·패널별 세분은 못 준다.**
   `parse_usage` 가 주는 것: `{"session": {"pct", "reset"}, "week_all": {...},
   "week_sonnet": {...}}` + 그림자 세션 `account`. used/limit 절대값·패널/탭/세션
   단위 세분은 `/usage` 패널에 없다. → **스크랩 누계를 폐기할 수 없다**(세분의 유일한
   출처). 단, 의미를 "소비 토큰"에서 **"패널별 활동량 추정(상대 비교용)"** 으로 강등한다.

3. **채택안: (A) 권위값 1차화 + 세분 화면만 (C) 추정/실측 분리 표기** (S5 §8 권장
   그대로). 한도·예산·게이트·상태줄의 1차 기준 = `/usage` 실측 %. 스크랩 누계는
   패널별 세분 화면에서만, 항상 **`~`(추정) 라벨**과 함께.

4. **게이트(자동개입 보류)가 제일 급하다.** `_budget_over`(일/세션/계정 절대 토큰
   예산)와 `token_budget_resume_gate` 가 전부 **왜곡된 스크랩 누계** 기준으로
   동작 중 — 한도에 다가가도 안 막히거나, 멀쩡한데 막힐 수 있다. 실측 % 게이트로
   1차 기준을 바꾼다(§4 T4).

5. **실측을 이력화한다.** 지금 `self._usage` 는 메모리 최신값 하나뿐(서버 재시작 시
   유실, 추이 조회 불가). SQLite 에 스냅샷 시계열로 쌓아 ① 추이 표시 ② 스크랩과의
   대사(reconcile) 검증 데이터를 만든다(§4 T1·T2).

---

## 1. 현황 — 두 출처와 소비자 (S5 모듈화 이후)

### 1.1 출처 ① 스크랩 누계 (추정 — 세분 가능, 의미 왜곡)

```
busy footer "↑ 0.4k · ↓ 1.9k tokens"
  → tokens.parse_running_tokens → tokens.step(peak 확정) → committed
  → servermixin._log_tokens → usagedb INSERT(ts,tab,pane,session,account,tokens)
  → _budget_track(일 누계) / _budget_over(일·세션·계정 예산)
```

### 1.2 출처 ② `/usage` 실측 (권위 — % 만, 세분 불가)

- **그림자 질의**: `usageprobe.query_usage`(숨은 claude 세션 + pyte 스크랩,
  `servermixin.refresh_usage`) → `self._usage` 메모리 저장 + broadcast.
  자동 갱신: 코어 `serverio._usage_loop` 가 `usage_refresh_sec`(기본 600초)마다
  `server_usage_refresh` 훅 호출(Claude 패널 없으면 skip).
- **인라인 한도 문구**: footer "You've used N% of your session limit · resets …" →
  `parse_inline_limit` 이 `_usage` 에 병합(패널 본문에서 공짜 실측, M19+).
- **인패널 /usage 상승에지**: `_usage_shown_seq`(사용자가 직접 띄운 패널 감지 →
  클라 자동 팝업).

### 1.3 소비자 × 현재 출처 × 목표 출처

| 소비자 | 위치 | 현재 출처 | 목표(이 시나리오) |
|---|---|---|---|
| 상태줄 `tok Σ` 누계 | `clientstatus.render_segs` | 스크랩 | **실측 세션%** 주 표시, 누계는 `~` 추정 강등 |
| 상태줄 `(N%/5h)` | `_tok5h_pct` | 실측 우선·없으면 분모 추정 | 실측만(추정 분모 경로 폐기 검토 §7-3) |
| 토큰 트리/사용량 팝업 | `usage_bar_lines` 등 | 실측+스크랩 혼재 | 실측 블록 상단 고정 + 추정 라벨 분리 (C) |
| TokenLogScreen 집계(h/d/w/m×계정) | `screens.py`·`usagelog` | 스크랩 | 유지하되 **"추정" 라벨** + 실측 스냅샷 추이 추가 |
| 일 예산 경고 `_budget_track`(0/80/100) | `servermixin` | 스크랩 절대값 | **실측 % 임계** 1차, 절대 예산은 보조 |
| 자동재개 게이트 `_budget_over` | `servermixin:126` | 스크랩 절대값(일·세션·계정) | **실측 % 게이트** 1차 (§4 T4) |
| 절감 자동화 무장 임계(M13 등) | `servermixin:886` (`>=80`) | 스크랩 기반 레벨 | 실측 % 레벨 |
| `_learned_5h_cap`(분모 학습) | `servermixin:766` | 스크랩 | 폐기 후보(실측 % 가 분모 불필요화) §7-3 |

> ⚠️ **재감사 주의**(S5 §1 교훈 승계): 위 표의 위치/줄번호는 작성 시점 grep 기준.
> 착수 전 한 줄씩 재검증할 것 — 탐색 에이전트 과소보고 전례 2회([MEMORY]
> `claude-plugin-extraction-phases`).

### 1.4 코어 잔류 (이 시나리오 범위 밖, 기록만)

`server.py` 생성자의 `_usage`/`_usage_busy`/`usage_refresh_sec`/`_usage_shown_seq`/
`_learned_5h_cap` 초기화와 `serverio._usage_loop` 골격은 코어에 있다(훅
`server_usage_refresh` 부재 시 no-op 라 delete-to-disable 은 성립). 완전 이전은
S5 후속 정리 거리지만 **회계 수정과 섞지 않는다**(이전≠수정 원칙).

---

## 2. 문제 재진단 — 왜 다른가 (S5 §2 요약 + 이행 판단)

구조적 원인 6가지는 S5 §2 그대로: ① 출처 의미 차이(streaming vs 과금 누적) ②
peak-확정의 의미 왜곡 ③ 캐시 미구분 ④ 세션 경계 휴리스틱 ⑤ 계정 귀속 휴리스틱
⑥ 두 출처 미대사.

**이행 판단**: ①~③은 스크랩 쪽을 어떻게 고쳐도 해소 불가(footer 에 과금 정보가
없다) → 1차 기준을 실측으로 바꾸는 (A) 가 유일한 정합 경로. ④⑤는 스크랩을 추정
용도로 강등하면 치명도가 내려가나, 계정 귀속(⑤)은 실측 게이트의 적용 조건(같은
계정인가)에 여전히 쓰여 검증 필요(§5-4). ⑥은 T2 대사 리포트로 해소.

---

## 3. 설계 원칙 / 비목표

**원칙**
1. **실측 1차**: 한도 판단(게이트·경고·상태줄 주 표시)은 실측 % 만 쓴다. 실측이
   없으면(stale·프로브 실패) 게이트는 **개입하지 않는 쪽으로 fail-open** —
   지어내지 않는다(§5.5 정신, `_tok5h_pct` ③ 선례).
2. **추정은 세분 전용 + 라벨 의무**: 스크랩 누계가 보이는 모든 화면에 `~` 또는
   "추정" 라벨. 실측과 추정을 한 줄에 섞을 땐 항상 구분.
3. **이력화**: 실측 스냅샷을 SQLite 시계열로 영속(메모리 최신값 의존 제거).
4. **이전≠수정 분리 승계**: 각 단계는 독립 CL, 표시 변경(T3)과 게이트 변경(T4)을
   섞지 않는다.

**비목표**
- footer 스크랩 파서(`parse_running_tokens`/`step`) 제거 — 세분 추정 출처로 유지.
- 코어(`server.py`/`serverio.py`) 변경 — 전부 플러그인 안에서 끝낸다(§1.4 잔류
  정리는 별도).
- Claude API/계정 백엔드 직접 질의 — 화면 스크랩 경계 유지(인증 정보 비접촉).
- 과금 금액(원/달러) 추정 — % 와 토큰 수까지만.

---

## 4. 단계별 계획 (각 단계 독립 CL, 442+ 그린 유지)

> 전부 ✅ 구현·서브밋 완료: T1=58089, T2=58093, T3=58101, T4=58106, T5=58107,
> T6=골든·계약 확장 CL. 구현 중 원안과 달라진 점은 각 CL 디스크립션 참조 —
> 주요 정정: T4 실측 게이트는 M12 resume_gate 토글(기본 OFF)에 얹지 않고
> **독립 게이트(기본 ON)** 로 두었다(기본 ON 결정과 토글 기본 OFF 의 충돌 해소).
> T5 의 "인라인 즉시 반영"은 기존 동작 그대로(디바운스 불요 확인).

- **T1 — 실측 스냅샷 영속화**: `usagedb` 에 `limits` 테이블 신설
  `(ts REAL, account TEXT, session_pct INT, session_reset TEXT, week_all_pct INT,
  week_sonnet_pct INT, week_reset TEXT, source TEXT)` — `source` 는
  `probe|inline|panel`(출처 추적). `refresh_usage` 성공·인라인 병합 시 INSERT
  (동일값 연속 중복은 skip), `prune` 보존기간 적용(§7-2). 마이그레이션:
  `CREATE TABLE IF NOT EXISTS`(기존 `usage` 테이블 무접촉, [MEMORY]
  `token-storage-sqlite-decision` 의 스키마 진화 패턴).
- **T2 — 대사(reconcile) 리포트**: 연속 두 실측 스냅샷 사이 구간의 스크랩
  committed Σ 와 실측 Δpct 를 나란히 놓는 조회(같은 계정 한정). 표시 변경 없음 —
  `token-usage` 팝업의 진단 서브뷰(또는 로그 명령)로만. **목적: (A) 강등 후에도
  스크랩 추정이 상대 지표로 쓸 만한지(상관) 데이터로 판단** — REC 캡처 기준선
  ([MEMORY] `rec-capture-claude-data`, S5 §7-1)과 함께 §5 의 검증 입력.
- **T3 — 표시 1차화(강등)**: 상태줄 토큰 세그먼트를 실측 세션% 주 표시로 재배치
  (`tok ~Σ` 추정 라벨), 사용량 트리/팝업 상단에 실측 블록(세션·주간·리셋·계정,
  stale 경과시간 표기) 고정, TokenLogScreen 에 "추정" 라벨 + 실측 추이 탭(T1
  시계열). 클라 전용 변경(attach 재실행으로 반영).
- **T4 — 게이트·경고 실측 전환**: 신설 `plugin_opts` 설정
  `usage_gate_session_pct`/`usage_gate_week_pct`(0=끔) — `_budget_over` 가 실측 %
  를 1차로 보고(스냅샷 stale 기준 초과 시 무시·fail-open), 기존 절대 토큰 예산
  (`token_budget_*`)은 보조 축으로 유지(설정 마이그레이션 불필요, 의미 불변).
  `_budget_track` 경고 레벨(0/80/100)도 실측 % 우선. 서버측 — restart 필요.
- **T5 — 갱신 전략 보강**: 주기(600초) 외 이벤트 트리거 — ① 응답 종료
  (committed>0) 후 디바운스 갱신(연속 응답 폭주 시 1회로 합침) ② footer 인라인
  한도/리밋 문구 감지 시 즉시 반영(이미 있음 — 디바운스만 정리) ③ 게이트 임계
  부근(예: 마지막 실측이 임계-10%p 이내)에서는 주기 단축. 숨은 세션 비용이 있어
  공격적 단축은 금물(§6-1).
- **T6 — 회귀·계약 테스트**: `/usage` 패널·인라인 문구 실캡처 fixture 골든
  (파서 드리프트 가드, [MEMORY] `claude-usage-headless-probe` 의 "리밋 문구
  미검증" 공백 해소), limits 테이블 마이그레이션/prune 테스트, 게이트 fail-open
  (실측 부재·stale·계정 불일치) 테스트, `test_plugin_contract` 에 limits 테이블
  포함 delete-to-disable 단언 확장.

> 순서 근거: T1·T2(데이터)가 T3·T4(판단 변경)의 검증 기반. T4 는 동작 변화가
> 가장 크므로 T2 대사 데이터를 본 뒤 임계 기본값을 정한다(§7-1).

---

## 5. 검증 전략 (S5 §7 이행)

1. **기준선 캡처**: 같은 작업 구간에서 ① 스크랩 누계 ② 실측 스냅샷(T1) ③ 앱
   화면 수기값을 동시 기록(REC 활용) → 차이 표. T2 리포트가 이걸 상시화.
2. **대사 테스트(헤드리스)**: 고정 캡처 로그 pyte 재렌더로 결정적 재현 —
   스냅샷 Δpct 와 committed Σ 의 상관/허용 오차 정의(절대 일치는 기대하지 않음,
   §0-1).
3. **회귀 고정**: 실제 `/usage` 패널·인라인 문구 fixture 로 `parse_usage`/
   `parse_inline_limit` 골든(레이아웃 드리프트 감지).
4. **계정 귀속**: 그림자 세션 `account` 와 패널 계정 불일치 시 게이트 미적용
   (fail-open) 테스트 — 오귀속 방지 규칙([MEMORY] `claude-scrape-false-positives`)
   유지.

### 5.5 라이브 대사 관찰 기록 (2026-06-11, limits 20스냅샷 × usage 270레코드)

후속 ①(실측 Δ% vs 스크랩 Σ 상관 관찰)을 라이브 DB(09:23~14:11, probe 스냅샷
20건)로 1차 수행. 결과:

- **버그 발견 → 수정**: 하루 usage 레코드 270건 중 **83%(223건)가 60초내 동일값
  반복 커밋**(한 응답 최대 117회 — 5분 구간 Σ 가 2.4M/4.9M 로 부풀어 이상치).
  원인: 응답 종료 후에도 화면에 `↑/↓ N tokens` 잔상(완료 라인·스크롤 잔재)이
  남으면 `tokens.step` 이 비-busy 프레임마다 "peak 재구축→즉시 확정"을 반복.
  → **idle_mark 잔상 가드**로 수정(같은 CL): 비-busy 확정 값 이하의 running 은
  busy 재진입까지 무시. IMPROVEMENT §3.5(로깅 집계 어긋남)의 실체였다.
- **상관**: 원본 피어슨 r=0.049(중복 이상치 지배) → 중복 접기(dedup) 후
  **r=0.376**(n=18, 비-reset). 여전히 약한 이유는 구조적: 실측 5h% 는 **계정
  전체**(타 머신 — 이날 office 머신이 같은 계정으로 작업)를 반영하는데 스크랩은
  로컬 패널만 본다. Δpct=1~2 인데 Σ=0 인 구간 다수가 이 패턴. → **§0 의 "보정
  (B) 불성립·추정 강등" 판단을 데이터로 재확인**(상대 지표로도 동일 머신 활동량
  에만 유효).
- **잔존 관찰 항목**: ① limits 스냅샷 `account` 가 전부 None(probe 20/20) +
  usage 레코드 계정도 전일 'unknown' — 그림자 /usage 패널·패널 화면에서 계정
  식별이 안 되고 있다(같은 계정 한정 대사·게이트 계정 대조가 비활성 = fail-open
  으로만 동작). 원인 조사 후속(파서 vs 화면 레이아웃). ② 중복 가드 적용 **후**
  데이터로 상관 재관찰(이날 데이터는 dedup 근사로만 평가). ③ 2026-06-11 이전
  누적 DB 이력의 중복은 보존(표시는 ~Σ 추정 라벨이라 치명 아님 — 필요 시 일괄
  dedup 마이그레이션 별도 발의).

---

## 6. 리스크

1. **그림자 질의 비용·지연**: 숨은 claude 세션 부팅 ~12초·전체 타임아웃 35초,
   spawn 자체가 자원·(잠재) 토큰 비용 — T5 의 주기 단축은 보수적으로. Claude 패널
   없으면 skip 가드 유지.
2. **`/usage` 레이아웃 드리프트**: Claude 버전 의존 — T6 fixture 골든이 1차 방어,
   파서 실패 시 게이트 fail-open(원칙 1)이 2차 방어.
3. **stale 실측으로 오게이트**: 스냅샷에 ts 가 있으므로 게이트는 신선도 한계
   (예: 갱신 주기×2) 초과 시 그 축을 무시. 경계 테스트 필수.
4. **week 버킷 클라측 제약**: `%G-W%V` SQLite 3.46+ 제약으로 week 집계는 클라측
   유지([MEMORY] `token-storage-sqlite-decision`) — T1 limits 추이 표시도 동일
   패턴을 따른다.
5. **서버 재시작 필요**: T4·T5 는 servermixin 변경 — 드라이런 우선 절차
   ([MEMORY] `restart-dryrun-first-protocol`) 준수.
6. **병렬 WIP**: playground 공유 워크스페이스 — 착수 전 `p4 opened -a`·sync 확인
   ([MEMORY] `shared-workspace-parallel-wip`). 이번 작업은 플러그인 디렉터리
   한정이라 코어 충돌 면적은 작다.
7. **flaky**: `test_ime_hardware_cursor`(기존) — 회귀 판단 시 오인 주의.

---

## 7. 미해결 질문 / 결정 (2026-06-10 사용자 결정으로 1·2·3·5 해소)

1. ~~**실측 % 게이트 기본값**~~ → **결정: 세션 95 기본 ON · 주간 0(끔)**.
   T4 에서 구현(`usage_gate_session_pct=95`). 설정 팝업 cycle 로 0(끔)~98 조정.
2. ~~**limits 스냅샷 보존 기간**~~ → **결정: 무제한**(자동 prune 없음 — 행이 작고
   값 변화 시에만 쌓임). `prune_limits` 는 수동·후속 정책용으로만 존재.
3. ~~**추정 분모 경로 폐기 여부**~~ → **결정: 폐기**. T3 에서 `_tok5h_pct` ②경로·
   `_learned_5h_cap` 제거(실측 없으면 None — 지어내지 않음). `token_budget_5h`
   설정은 잔존 호환만(표시 분모로 미사용, 설정 팝업 라벨에 레거시 명시).
4. ~~**기존 `token_budget_*` 절대 예산의 장기 운명**~~ → **해소(2026-06-11,
   사용자 결정)**: 실측 게이트 라이브 검증(58133) 후 **deprecate 실행** — 설정
   5종(day/session/5h/account/resume_gate)·setter·`_budget_track`/`_budget_over`/
   `_budget_level_for`·설정 팝업 행·status full 키 전부 제거. 소비처는 실측
   게이트로 일원화(M13 plan 유도·M15 우선 정리 = `_usage_gate_level/_over`,
   와이어 키 `budget_level` 이름은 유지). 마이그레이션 shim = `_OPTS_KEYS` 에서
   빠진 구 키를 로드 시 무시(다음 _save_opts 에서 자연 소멸 — S5 T3 선례).
5. ~~**상태줄 추정 누계 노출 수위**~~ → **결정: `tok ~Σ` 라벨 유지**(기본 숨김
   아님). T3 에서 구현 — 실측 5h% 가 주 표시로 앞에, ~Σ 는 추정 라벨로 뒤에.

---

> **다음 행동(후속)**: ① REC 실캡처 구간에서 TokenLogScreen [대사] 뷰(r)로 실측
> Δ% vs 추정 Σ 상관을 관찰 — 추정 강등의 실증 데이터 축적(§5-1·2).
> ② ~~실사용 검증 후 §7-4(절대 예산 deprecate) 결정~~ → **완료(2026-06-11,
> §7-4 참조)**. ③ 서버 재시작(restart-server)으로
> 라이브 반영 — 드라이런 우선 절차 준수.
