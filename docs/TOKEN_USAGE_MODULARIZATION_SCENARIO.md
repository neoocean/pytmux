# 토큰 사용기록 모듈화 + 정확성 검증/수정 시나리오

> **상태**: ✅ **모듈화 완료(2026-06-10, T1~T6)**. §6 단계 전부 동작 보존 이전으로 제출:
> T1 request_token_log→훅(p4 58071) · T2 _log_tokens·예산·DB연결→servermixin+server_init
> 훅(58072) · T3 token_budget_* 완전 플러그인 소유+plugin_opts 네임스페이스+마이그레이션
> shim(58073) · T4 Pane 토큰누계→panestate+pane_closing 훅(58074) · T5 usagedb/usagelog
> 물리이전+DB 파일 플러그인 하위 이동+타 머신 마이그레이션(58078) · T6 종합 계약 테스트.
> 전체 442 green. 코어(server/serverio/model/servertree/serverpersist)는 토큰의 의미를
> 전혀 모른다(delete-to-disable: claude-code 디렉토리 삭제 시 토큰 명령·DB·기록·조회·예산·
> 누계·데이터까지 흔적 없이 사라지고 코어 무에러). **남은 것은 §8 회계 정확성 재설계(별도
> 세션)** — 모듈화로 폭발 반경이 플러그인 안에 갇혔다.
>
> 동기(원안): **현재 기록값이 Claude Code 앱(/usage)에 표시되는 값과 상당히 다르다** —
> 회계 로직을 갈아엎으려면 먼저 코어 결합을 끊어 폭발 반경을 플러그인 안으로 가둬야 한다.
>
> 관련: [TOKEN_USAGE_STORAGE_DESIGN.md](TOKEN_USAGE_STORAGE_DESIGN.md)(JSONL→SQLite
> 저장 설계) · [TOKEN_USAGE_UI_SCENARIO.md](TOKEN_USAGE_UI_SCENARIO.md)(팝업 UI) ·
> [TOKEN_SAVING_SCENARIO.md](TOKEN_SAVING_SCENARIO.md)(감지/개입) ·
> [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md)(플러그인 계약) · [HANDOFF.md](HANDOFF.md) §11.6.

---

## 0. 핵심 결론 (먼저)

1. **현 회계는 화면 스크랩 휴리스틱이다 — 권위 데이터가 아니다.** 토큰 수는 Claude
   busy footer 의 `↑ 0.4k · ↓ 1.9k tokens` 문구를 정규식으로 긁어, 응답 종료 시
   **peak 값을 확정**해 쌓는다(`tokens.parse_running_tokens`·`tokens.step`). 이 숫자는
   **현재 응답의 컨텍스트 창 점유(streaming)** 에 가깝지, Claude 가 **과금/집계하는
   입력·출력·캐시 토큰의 누계가 아니다.** → 앱 `/usage` 와 어긋나는 게 당연하다.

2. **권위값 경로는 이미 있다 — 다만 회계와 분리돼 있다.** `usageprobe.query_usage`
   가 숨은 `claude` 세션을 띄워 `/usage` 패널을 pyte 로 스크랩해 **세션(5h)·주간 실측
   %/used/limit** 을 얻는다(M19). 그러나 이 권위값은 **상태줄의 `tok5h_pct` 표시에만**
   쓰이고, SQLite 에 쌓는 스크랩 누계와 **대사(reconcile)되지 않는다.** 두 출처가
   따로 놀아 사용자가 보는 숫자가 모순된다([MEMORY] `usage-panel-vs-app-mismatch`).

3. **모듈화가 선행 조건이다.** 회계 로직을 권위값 기반으로 갈아엎으려면(또는 footer
   스크랩을 폐기하려면) 저장·집계·예산·조회·Pane 필드가 **코어 곳곳에 박혀 있어서**
   는 안 된다. 현재 코어 결합은 §3 인벤토리대로 **서버 5파일 + 데이터 모듈 2개**에
   퍼져 있다. 이를 플러그인으로 모아 **delete-to-disable 계약**을 토큰 영역까지 확장
   하면, 회계를 통째로 교체해도 코어와 다른 플러그인 기능은 무관해진다.

4. **권장 경로**: (A) **이전 먼저, 수정 나중**. 동작 보존 이전(behavior-preserving
   move)으로 토큰 코드를 플러그인으로 옮겨 그린 유지 → (B) 그 다음 격리된 플러그인
   안에서 회계를 `/usage` 권위값과 대사하도록 재설계. 두 작업을 **섞지 말 것**(이전
   중 수정은 회귀 원인 규명을 불가능하게 만든다).

---

## 1. 현황 — 토큰 사용기록 데이터 흐름

```
Claude 화면(PTY)  "✽ … (12s · ↑ 0.4k · ↓ 1.9k tokens)"   ← streaming footer
        │
        ▼  servermixin._scan_claude()  (30Hz flush 루프)            [플러그인]
        ▼  tokens.parse_running_tokens(txt) → 2300|None             [플러그인]
        ▼  tokens.step(pane._tok_state, running, busy) → committed  [플러그인]
        │     · pane._tok_state = {"peak","total"}  (응답 peak 확정)
        │     · pane._session_tokens = total+peak   (표시 캐시)
        ▼  committed>0 이면:
        ▼  server._log_tokens(sess, tab, pane, amount)              ★[코어 server.py]
        │     · server._budget_track(amount)   → 일 누계·경고레벨    ★[코어]
        │     · usagelog.make_record(...)       → dict               ★[코어 usagelog]
        │     · usagedb.insert(conn, rec)       → INSERT             ★[코어 usagedb]
        ▼
   SQLite usage(ts,tab,pane,session,account,tokens)                  ★[코어]
        │
        ├─▶ status 메시지(server_status 훅): claude_tokens/usage/    [플러그인]
        │      tok5h_pct/usage_limits/budget_level/token_budget_*
        │      → clientstatus.absorb/render_segs (상태줄)            [플러그인]
        │
        └─▶ request_token_log(serverio): usagedb.query_records +     ★[코어 serverio]
               total_all + totals_by_account
               → 클라 usagelog.aggregate/agg_view (버킷×차원)        ★[코어 usagelog]
               → TokenLogScreen 팝업                                 [플러그인]

   별도: usageprobe.query_usage → 숨은 /usage 스크랩 → server._usage  [플러그인]
         → tok5h_pct 표시에만 사용(★ 회계와 대사 안 됨)
```

단계별 위치(파일:라인):

| 단계 | 코드 | 위치 | 소유 |
|---|---|---|---|
| footer 파싱 | `parse_running_tokens` | `plugins/claude-code/tokens.py:34` | 플러그인 |
| peak 확정 상태기계 | `step` | `tokens.py:59` | 플러그인 |
| 스캔 루프 | `_scan_claude`(토큰부) | `servermixin.py:742-753` | 플러그인 |
| **영속 기록** | `_log_tokens` | **`server.py:363`** | **코어★** |
| **일예산 추적** | `_budget_track`/`_refresh_budget_level`/`_seed_today_from_log` | **`server.py:387/403/377`** | **코어★** |
| **DB 연결·마이그레이션** | `_tokens_db_conn`/`tokens_db_path` | **`server.py:334/319`** | **코어★** |
| **SQLite 백엔드** | connect/insert/query/total_all/… | **`usagedb.py`** | **코어★** |
| **레코드·집계 순수함수** | make_record/aggregate/agg_view/bucket_key | **`usagelog.py`** | **코어★** |
| **로그 조회 핸들러** | `request_token_log` | **`serverio.py:396-409`** | **코어★** |
| **Pane 토큰 필드** | `_tok_state`/`_session_tokens` | **`model.py:451-452`** | **코어★** |
| **패널 토큰 이관**(리네임/머지) | `_tok_state` read/write | **`servertree.py:52-59`** | **코어★** |
| **예산 설정 직렬화** | `token_budget_*` | **`serverpersist.py:358-365`** | **코어★** |
| 상태줄 렌더 | absorb/render_segs/init_defaults | `clientstatus.py` | 플러그인 |
| 토큰 로그 팝업 | `TokenLogScreen` | `screens.py:325` | 플러그인 |
| 그림자 /usage | query_usage/refresh_usage | `usageprobe.py`·`servermixin.py:1043` | 플러그인 |

> ⚠️ **재감사 주의**: 초기 탐색이 "`_log_tokens` 등은 이미 servermixin 에 있다"고
> 보고했으나, **틀렸다 — 전부 코어 `server.py` 에 있다**(grep 확인). [MEMORY]
> `claude-plugin-extraction-phases` 의 "탐색 에이전트가 코어 결합 2회 과소보고" 교훈
> 그대로다. **이전 착수 전 위 표를 grep 으로 한 줄씩 재검증할 것.**

---

## 2. 정확성 문제 진단 — 왜 Claude Code 앱과 다른가

회계가 앱과 어긋나는 **구조적 원인**(수정 대상):

1. **잘못된 출처**: footer `↑/↓ tokens` 는 **현재 응답의 컨텍스트 streaming 수치**다.
   Claude 과금/`/usage` 는 **입력+출력+캐시읽기+캐시쓰기**의 누적이며 footer 숫자와
   단위·의미가 다르다. 두 값은 애초에 같아질 수 없다.
2. **peak-확정의 의미 왜곡**: `step` 은 응답 종료 시 **running 의 peak** 을 더한다.
   즉 "컨텍스트가 가장 컸던 순간"을 "소비 토큰"으로 적는다 — 누적 과금과 무관.
3. **캐시 구분 없음**: 캐시 적중(대량·저비용)과 신규 입력을 구분 못 해 절대량이
   왜곡된다.
4. **세션 경계 휴리스틱**: "running 이 절반 이하로 급감 → 새 응답"으로 추정해
   중복 계상/누락 위험(`step` 로직).
5. **계정 귀속 휴리스틱**: `_claude_account` 가 화면 스크랩 추정이라 오귀속 가능
   ([MEMORY] `claude-scrape-false-positives`).
6. **두 출처 미대사**: 권위값(`/usage` 그림자)과 스크랩 누계가 **별도로** 표시돼
   사용자가 모순을 본다([MEMORY] `usage-panel-vs-app-mismatch` — stale 스냅샷 +
   계정 불일치).

→ **검증 기준선(ground truth)은 `/usage` 패널**이다. 수정의 핵심 질문: *스크랩 누계를
버리고 `/usage` 권위값으로 갈 것인가, 아니면 둘을 대사해 보정할 것인가*(§8).

---

## 3. 코어 결합 인벤토리 (이전해야 할 것)

토큰 영역을 delete-to-disable 로 만들려면 아래 코어 결합을 전부 끊어야 한다.
(`plugins/claude-code/` 삭제 시 토큰 기능만 무에러로 사라지고 코어는 동작.)

### 3.1 데이터 모듈 (물리 이전 대상)
- **`pytmuxlib/usagedb.py`**(190줄) → `plugins/claude-code/usagedb.py`
- **`pytmuxlib/usagelog.py`**(263줄) → `plugins/claude-code/usagelog.py`
  - 단, `usagelog.bar` 는 이미 `clientutil.bar`(코어 표시 헬퍼)로 분리됨(S5b) —
    클라 코어가 데이터 모듈을 import 하지 않음. 잔여 코어 import 는 **server.py·
    serverio.py 뿐**(둘 다 서버측).

### 3.2 서버 코어 메서드 (servermixin 으로 이전 + 훅화)
`server.py` 에 박힌 토큰 메서드/상태:
- `_tokens_db`/`_tokens_db_conn`/`tokens_db_path`/`tokens_log_path`(DB 연결·마이그레이션)
- `_log_tokens`(기록), `_seed_today_from_log`(시드), `_budget_track`·`_refresh_budget_level`·`_budget_level`·`_today_tokens`·`_today_key`(예산)
- 설정 필드: `token_budget_day/session/5h/account`·`token_budget_resume_gate`(생성자·setter·serverpersist 직렬화)
- 모듈 import: `from . import … usagedb, usagelog`

**문제**: `_scan_claude`(플러그인)는 `self._log_tokens(...)` 로 코어 메서드를 부른다.
이 호출은 "플러그인→코어 메서드" 라 **방향이 거꾸로**다(코어는 토큰을 몰라야 함).
→ 신규 훅이나 mixin 이전으로 뒤집어야 한다(§5).

### 3.3 serverio 로그 조회
- `serverio.py:396` `request_token_log` 핸들러가 `usagedb` 를 직접 호출.
  → `server_command`/`handle_server_request` 훅으로 이전(이미 19종 액션이 이 패턴).

### 3.4 Pane 필드 / 트리 이관
- `model.py` Pane: `_tok_state`·`_session_tokens`(코어 기본값·`export_state` 슬롯).
  S4 에서 일부 필드는 `panestate` 로 갔으나 **이 둘은 코어 잔류**(코어 read 존재).
- `servertree.py:52-59`: 패널 리네임/머지 시 `_tok_state["total"]` 을 읽어 합산 이관.
  → `pane_merge`/`pane_rename` 류 훅 또는 panestate 위임 필요.

### 3.5 코어 read 지점 (getattr 가드 확인 대상)
- `servertree.py`(토큰 이관), `serverpersist`(예산 직렬화), `client.py:779`
  (`_token_log_screen`), `clientwidgets.py:1141`(`open_token_log` getattr) —
  클라측은 이미 getattr 가드. **서버측 servertree/serverpersist 가 직접 read/write**
  하므로 가드/훅 필요.

---

## 4. 모듈화 목표 경계

| 항목 | 현재 | 목표 |
|---|---|---|
| `usagedb.py` | 코어 | **플러그인** |
| `usagelog.py` | 코어 | **플러그인** |
| `_log_tokens`·예산·DB연결 | 코어 server.py | **플러그인 servermixin** |
| `request_token_log` | 코어 serverio | **플러그인 server_command 훅** |
| `token_budget_*` 설정 | 코어 server.py | **플러그인 소유**(서버 설정 네임스페이스 또는 불투명 opts 위임) |
| Pane `_tok_state`/`_session_tokens` | 코어 model.py | **panestate(플러그인)** |
| 패널 토큰 이관(servertree) | 코어 직접 read/write | **신규 훅(`pane_merge`)으로 위임** |
| `clientutil.bar`(표시 헬퍼) | 코어 | 코어 유지(순수 표시) |

**코어 잔류 허용**: 순수 표시 헬퍼(`clientutil.bar`), 그리고 토큰을 **모르는** 일반
디스패치 골격(예: serverpersist 가 불투명 dict 를 저장하는 구조). 핵심은 *코어가
토큰의 의미를 아는 코드*를 0 으로 만드는 것.

---

## 5. 필요한 신규 훅 / 계약

§11.6 의 기존 훅(server_scan/status/command, pane_init/serialize/restore)에 더해:

1. **`server_token_commit(server, sess, tab, pane, amount)`** — `_scan_claude` 가
   committed>0 일 때 부르는 훅. 플러그인이 DB insert·예산 추적을 **소유**(코어
   `_log_tokens` 제거, 방향 정상화). 부재 시 no-op → 토큰 안 쌓임.
2. **서버 DB 수명주기**를 플러그인이 소유: `server_mixins` 로 들어오는 mixin 이
   `_tokens_db_conn` 등을 정의(코어 server.py 에서 삭제). 코어는 connection 을 모름.
3. **`request_token_log` → `handle_server_request`/`server_command` 훅** 로 이전
   (이미 있는 훅 재사용; serverio 의 elif 분기 제거).
4. **예산 설정 저장**: `server_opts_serialize(server) → dict` / `server_opts_restore`
   훅으로 `token_budget_*` 를 플러그인이 직렬화(serverpersist 는 불투명 병합만).
   — 또는 더 가볍게, serverpersist 가 `plugins.server_persist_fields()` 로 키 목록을
   받아 저장(토큰 의미 모름).
5. **패널 토큰 이관**: `pane_merge(dst, src)` 훅으로 servertree 의 `_tok_state`
   합산을 플러그인에 위임(코어는 슬롯만 직렬화).

> 설계 메모: 4·5 는 "코어가 토큰을 모르되 불투명 데이터는 저장/이관" 패턴 — S4 의
> `plugin_state` 직렬화와 동일 철학. 신규 훅은 **부재 시 전부 no-op/{}** 이어야 한다.

---

## 6. 단계적 이전 순서 (저위험, 동작 보존)

각 단계 후 **437+ 그린 + 라이브 attach 검증**. 중간 CL 은 계약이 깨질 수 있음(최종
CL 에서 delete-to-disable 완성 — §11.6 사용자 결정 ① 재적용).

- ✅ **T1**(p4 58071) `request_token_log` → 플러그인 `handle_server_request` 훅(serverio
  탈토큰). 설계 정정: `server_command` 가 아니라 요청/회신 dict 패턴인 `handle_server_request`
  가 조회 핸들러에 맞았다.
- ✅ **T2**(58072) `_log_tokens`·예산·DB연결을 `server.py`→`servermixin.py` 로 이전.
  설계 정정: `_scan_claude` 가 이미 플러그인이라 `server_token_commit` 훅은 불필요(플러그인
  내부 호출). 대신 런타임 상태 초기화용 **`server_init` 훅** 신설. 코어 server.py 에서
  `usagedb`/`usagelog` import 제거.
- ✅ **T3**(58073) `token_budget_*` 설정·직렬화를 **완전 플러그인 소유**(사용자 결정)로 —
  신설 `server_opts_init`/`server_opts_serialize` 훅 + opts.json `plugin_opts` 네임스페이스
  + **구 top-level 키 마이그레이션 shim**(타 머신 업그레이드 무중단).
- ✅ **T4**(58074) Pane `_tok_state`/`_session_tokens` → panestate(pane_init/reset/
  serialize), servertree 토큰 이관을 신설 **`pane_closing` 훅**으로 위임.
- ✅ **T5**(58078) `usagedb.py`·`usagelog.py` **물리 이전**(`p4 move`) →
  `plugins/claude-code/` + **DB 파일도 플러그인 하위 db/ 로 이동**(사용자 결정) + 루트
  db/→플러그인 db/ **타 머신 자동 마이그레이션**(`_migrate_legacy_db`). run.py 별칭에
  usagedb/usagelog 추가(테스트 무수정).
- ✅ **T6** 계약 테스트 확장: claude-code 격리 시 토큰 명령·DB·기록·조회·예산·누계·이관·
  DB 백엔드 import·서버 믹스인이 전부 사라지고 코어 무에러임을 한 테스트로 종합 단언
  (`test_plugin_contract.test_token_subsystem_fully_disabled_without_plugin`) + T1~T5 분산
  단언.

---

## 7. 독립 검증 전략 (수정의 기준선)

모듈화 후, 플러그인 안에서 회계를 **앱 `/usage` 대비** 검증한다:

1. **기준선 캡처**: 같은 작업 구간에서 ① pytmux 스크랩 누계, ② `/usage` 그림자
   실측, ③ 앱 화면 수기값을 동시에 기록(REC 캡처 활용 — [MEMORY]
   `rec-capture-claude-data`). 세 값의 차이를 표로.
2. **대사 테스트**: `usageprobe` 권위값과 SQLite 누계를 같은 계정·창에서 비교하는
   헤드리스 테스트(고정 캡처 로그를 pyte 재렌더 → 결정적). 허용 오차 정의.
3. **회귀 고정**: 실제 footer/`/usage` 캡처 샘플을 fixture 로 박아 파서 변경 시
   숫자 드리프트를 잡는다(현재 "리밋 문구 미검증" 공백 — [MEMORY]
   `claude-usage-headless-probe` 보강).
4. **계정 귀속 검증**: 오귀속(임의 이메일 스크랩) 방지 규칙을 테스트로 고정
   ([MEMORY] `claude-scrape-false-positives`).

---

## 8. 수정 방향 옵션 (모듈화 후 결정)

격리가 끝나면 회계를 다음 중 하나로 재설계(별도 결정 필요):

- **(A) `/usage` 권위값 1차화**: footer 스크랩 누계를 **표시에서 강등**하고, 주기적
  `/usage` 그림자 질의의 세션/주간 실측을 **주 표시값**으로. 장점: 앱과 일치. 단점:
  `/usage` 는 % 위주라 패널별/세션별 세분 불가, 질의 비용·지연.
- **(B) 스크랩 보정**: footer 누계를 유지하되 `/usage` 권위값으로 **주기 보정**
  (drift 계수). 장점: 세분 유지. 단점: 보정 모델 복잡·취약.
- **(C) 하이브리드 표시**: "추정(스크랩)"과 "실측(/usage)"을 **명시적으로 분리 표기**
  해 사용자가 혼동 안 하게. 가장 정직하나 UI 복잡.

> 권장: 모듈화(§6) 완료 → (A) 를 기본으로 검증, 세분이 필요한 화면만 (C) 로 보강.
> 이는 **별도 CL/세션**에서 다룬다(이 문서는 모듈화 경계까지가 범위).

---

## 9. 리스크 / 테스트

- **서버 데몬 재시작 필요**: server.py/serverio.py/servertree.py/model.py 변경은
  `restart-server` 재기동 후 반영([MEMORY] `restart-dryrun-first-protocol`).
- **DB 경로·마이그레이션 보존**: `tokens_db_path`(`db/claude-tokens.db`)와 JSONL
  일회 임포트 로직을 이전 중 깨면 이력 유실 — 이전 전후 `usagedb.count` 동일 확인.
- **테스트 의존**: `test_usagedb`·`test_client`(토큰 팝업)·`test_server`(예산)·
  `test_plugin_contract`. 물리 이전(T5)은 importlib 별칭으로 import 경로 무수정.
- **병렬 WIP 충돌**: playground 공유 워크스페이스 — server.py/serverio.py 는 다른
  세션도 만짐([MEMORY] `shared-workspace-parallel-wip`). 이전 전 `p4 diff -ds`·
  `p4 opened -a` 로 미서브밋 변경 0 확인.

---

## 10. 미해결 질문 / 결정 필요

1. **예산 설정의 소유권**: `token_budget_*` 를 완전 플러그인 소유(서버 opts 네임스페이스
   신설)로 갈지, serverpersist 가 불투명 키 목록만 저장(`server_persist_fields`)할지.
2. **세션 경계 정의**: 현 휴리스틱(running 급감) 유지 vs `/usage`/claude_session_id
   기반 재정의.
3. **footer 스크랩 폐기 여부**(§8 A vs B/C) — 모듈화 후 검증 데이터로 결정.
4. **물리 이전 시점**: usagedb/usagelog 를 지금(T5) 옮길지, 회계 재설계가 끝난 뒤
   한 번에 옮길지(이전+수정 분리 원칙상 **지금 이전 권장**).

---

> **다음 행동**: ✅ 모듈화(§6 T1~T6) 완료. **§8 회계 정확성 재설계는 본설계 시나리오로
> 이행** → [TOKEN_ACCOUNTING_ACCURACY_SCENARIO.md](TOKEN_ACCOUNTING_ACCURACY_SCENARIO.md)
> (S6, 채택안 (A) 권위값 1차화 + 세분 화면 (C) 분리 표기, T1~T6 단계 계획).
> 이제 토큰 회계가 claude-code 플러그인 안에 완전히 갇혀 있어, 코어·다른 플러그인 기능과
> 무관하게 통째로 교체할 수 있다([MEMORY] `usage-panel-vs-app-mismatch`·
> `claude-usage-headless-probe` 참조).
>
> **결정 기록(2026-06-10 사용자)**: ㉠ 예산 설정=완전 플러그인 소유(plugin_opts
> 네임스페이스), ㉡ 토큰 DB 파일=플러그인 하위 이동(+타 머신 마이그레이션), ㉢ 범위=T6
> 에서 정지(회계 수정은 별도). §10 미해결 질문 1·4 는 이 결정으로 해소.
