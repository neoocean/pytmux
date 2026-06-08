# 토큰 사용량 저장소 설계 검토 — JSONL → SQLite (패널별·계정별 통계)

> **상태**: 🟢 **구현 완료(SQLite 전면 도입, 2026-06-07)**. 사용자 결정으로 §7 의
> 단계적 권고 대신 **SQLite 전면 도입**을 채택해 구현했다. 본 문서는 그 설계 근거·
> 트레이드오프·경로를 남긴다. 구현 요약:
> - 저장: `pytmuxlib/usagedb.py`(SQLite, WAL). 경로 `db/claude-tokens.db`
>   (db/ 는 .gitignore·p4ignore 제외). 서버 최초 사용 시 옛 JSONL 일회 임포트.
> - 집계: 버킷×차원(시간/일/주/월 × 계정/세션) 전환은 `usagelog.aggregate` 로 **클라
>   측**(받은 최근 N 건을 라운드트립 없이 즉시 전환). **Phase B(2026-06-09 착수)**:
>   전체 이력 합은 서버가 SQL 로 집계해 함께 보낸다 — `usagedb.total_all`(SUM)·
>   `totals_by_account`(GROUP BY account). 받은 레코드는 cap(기본 5000) 이라 그 'Σ
>   합계'가 이력 초과 시 과소표시되던 문제를, 서버측 GROUP BY 로 정확한 lifetime Σ
>   를 돌려줘 해결(`request_token_log` 응답에 `total_all`/`accounts_total` 포함).
>   버킷별 GROUP BY 는 week 키 `%G-W%V` 가 SQLite 3.46+ 에서만 지원돼 클라 strftime
>   과 바이트 동일 보장이 어려워 클라측 유지(정확·즉시).
> - UI: 토큰 팝업 **[패널] 서브탭**(세션 기준 묶기 — 패널 재사용 대비, §8).
> - 도구: `scripts/import_token_jsonl.py`(수동 임포트), `migrate_token_accounts.py
>   --db`(DB 계정 정정), `usagedb.prune`(보존).
>
> 관련: [TOKEN_SAVING_SCENARIO.md](TOKEN_SAVING_SCENARIO.md) (토큰 감지/개입 전반) ·
> [HANDOFF.md](HANDOFF.md) §10 #7 (영속 로깅 도입 배경) ·
> `pytmuxlib/usagedb.py`(SQLite 저장) · `pytmuxlib/usagelog.py`(순수 집계·신뢰 규칙) ·
> `scripts/migrate_token_accounts.py`·`scripts/import_token_jsonl.py`(도구).

---

## 0. 핵심 결론(먼저)

- **데이터는 이미 패널별 통계를 낼 수 있다** — 레코드에 `tab`·`pane`·`session` 이
  들어 있으나(`usagelog.make_record` `usagelog.py:32-37`), 집계 함수
  (`aggregate` `usagelog.py:80-102`)가 **버킷×계정만** 묶고 `pane` 을 버린다.
  즉 "패널별 통계"의 **1차 병목은 저장 포맷이 아니라 집계 차원의 누락**이다.
- **SQLite 의 본질적 이득은 "패널별"이 아니라 "규모·동시성·정정"** 에 있다 —
  ① `GROUP BY` 로 임의 차원(패널/탭/세션/계정×버킷) 집계를 **서버에서** 끝내
  IPC 페이로드를 줄이고, ② 전체 파일을 메모리로 읽지 않아 로그가 커져도 쿼리비용이
  유계이며, ③ 계정 정정이 파일 전체 재작성이 아니라 `UPDATE` 한 방이다.
- **현 규모(187KB·1,752줄)에선 JSONL 도 충분하다.** 그래서 권고는 **2단계**(§7):
  먼저 JSONL 에 **패널 차원 집계만 추가**(포맷 불변·저비용)해 요구를 즉시 충족하고,
  로그 성장·보존정책·이력질의가 상시화되면 그때 SQLite 로 승격한다.

---

## 1. 현황(코드 근거)

### 1.1 저장
- 경로: `state_base(sock_path) + ".tokens.jsonl"` — 휘발 영역(state_base), Perforce
  비공유. 캡처(raw 화면)와 달리 민감 잔재 없는 집계 데이터라 공유 대상 아님
  (`server.py:256-260`).
- 쓰기: 응답 1건 확정(`committed>0`) 시 1레코드 append. **append 는 매번
  open(a)→write→close** 로 fd 를 들고 있지 않다(`usagelog.py:40-48`). → 외부에서
  `os.replace` 로 파일을 갈아끼워도 다음 쓰기가 경로를 새로 열어 안전(이 성질 덕에
  계정 정정 도구가 원자적 교체를 쓴다).
- 레코드 스키마(JSON 1줄):
  ```json
  {"ts": 1717500000.0, "tab": 0, "pane": 3, "session": 7,
   "account": "wo…@woojinkim.org", "tokens": 4200}
  ```
  (`usagelog.py:32-37`. `account` 없으면 `"unknown"` 으로 고정.)

### 1.2 읽기·집계
- 읽기: `read()` 가 **전체 파일 readlines → 줄마다 json.loads** 후 리스트 반환.
  `limit=N` 이면 마지막 N 줄만(`usagelog.py:51-71`).
- 집계: `aggregate()` 가 버킷(hour/day/week/month)×**계정**으로 합산. **`pane`/`tab`
  은 무시**(`usagelog.py:80-102`). 사람용 줄은 `summary_lines()`(`usagelog.py:114-138`).

### 1.3 데이터 흐름(IPC)
- 클라가 `request_token_log` 전송 → 서버가 `read(limit=5000)` 으로 **레코드 원본을
  최대 5000건 그대로 클라에 전송**(`serverio.py:462-467`, 요청 측 `client.py:1490`).
- **집계는 클라 측**에서 — `TokenLogScreen` 이 받은 레코드로 `summary_lines` 호출
  (`clientscreens.py:1056`). 팝업 서브탭은 **시간/일/주/월 + 계정**뿐, **패널 탭 없음**
  (`clientscreens.py:990-999`).
- 부수 읽기: 서버 기동 시 일 예산 시드 `_seed_today_from_log` 가 **로그 전체를 읽어**
  오늘분을 합산(`server.py:280-287`).

### 1.4 정정(이미 존재)
- `scripts/migrate_token_accounts.py` 가 비신뢰 계정을 `unknown` 으로 일괄 정정 —
  **파일 전체를 읽어 다시 쓴다**(`.bak` 백업 + `os.replace`). 2026-06-07 git SSH URL
  오검출(`gi…@github.com`) 잔재 정리에 사용.

### 1.5 현 한계(요약)
| # | 한계 | 근거 |
|---|---|---|
| L1 | 패널/탭/세션별 통계가 **데이터엔 있으나 집계·UI 에 없음** | `usagelog.py:80-102`, `clientscreens.py:990-999` |
| L2 | 조회마다 **전체 파일 → 메모리 → 줄마다 파싱**(O(N) 메모리/CPU) | `usagelog.py:51-71` |
| L3 | IPC 가 **레코드 원본 최대 5000건**을 통째로 전송 → 그 이상은 **잘려** 통계 누락 | `serverio.py:465`, `client.py:1490` |
| L4 | 예산 시드가 매 기동 **전체 로그 스캔** | `server.py:280-287` |
| L5 | 계정 정정/보존(prune)이 **파일 전체 재작성** | `migrate_token_accounts.py` |

---

## 2. SQLite 방안

### 2.1 스키마
```sql
CREATE TABLE IF NOT EXISTS usage (
  ts      REAL    NOT NULL,   -- epoch 초(현 ts 그대로)
  tab     INTEGER,            -- 탭 인덱스
  pane    INTEGER NOT NULL,   -- 패널 id
  session INTEGER,            -- claude 세션 id
  account TEXT    NOT NULL,   -- 'unknown' 기본
  tokens  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_usage_ts      ON usage(ts);
CREATE INDEX IF NOT EXISTS ix_usage_account ON usage(account);
CREATE INDEX IF NOT EXISTS ix_usage_pane    ON usage(pane);

PRAGMA user_version = 1;     -- 스키마 버전(이후 마이그레이션 분기)
```
- 파일: `db/claude-tokens.db`(프로젝트 디렉터리 하위 `db/`. .gitignore·중앙
  p4ignore 에 `db/` 전체 제외 등록 — 런타임·호스트 로컬, 버전관리 비대상).
- `sqlite3` 은 **CPython 표준 라이브러리** — 새 외부 의존 없음.

### 2.2 동시성
- **쓰기는 서버 단일 프로세스만**(클라는 IPC 로 질의만) → writer 경합 없음.
  단 예산 시드/통계가 같은 프로세스 내 다른 커서로 읽을 수 있으니 **WAL 모드**
  (`PRAGMA journal_mode=WAL`)로 reader/writer 비차단. `busy_timeout` 수백 ms 로
  드문 락 흡수.
- append 빈도는 "응답 1건당 1행"으로 낮다(핫패스 아님) → 커밋 비용 무시 가능.

### 2.3 집계(서버 측 GROUP BY)
패널별·계정별·버킷별을 SQL 한 방으로:
```sql
-- 예: 일별 × 계정 × 패널
SELECT strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime') AS bucket,
       account, tab, pane, SUM(tokens) AS tok
FROM usage
GROUP BY bucket, account, tab, pane
ORDER BY bucket DESC, tok DESC;
```
- 버킷 포맷은 현 `_BUCKET_FMT`(`usagelog.py:24-29`)와 동치로 strftime 매핑
  (week 는 `%G-W%V`).
- **이득**: 클라로 보내는 건 **집계 결과 수십 줄**이지 레코드 5000건이 아니다(L3 해소).
  예산 시드도 `SELECT SUM(tokens) WHERE ts>=오늘0시`(L4 해소).

### 2.4 정정·보존
- 계정 정정: `UPDATE usage SET account='unknown' WHERE account NOT IN (...)` — 파일
  재작성 불필요(L5 해소). 도구는 `--apply` 시 같은 안전장치(트랜잭션) 유지.
- 보존(prune): `DELETE FROM usage WHERE ts < ?` + 주기적 `VACUUM`(옵션). JSONL 은
  보존정책 자체가 곤란했다.

---

## 3. 패널별 통계 UI(저장소와 독립)

> 이 절은 **JSONL/SQLite 어느 쪽이든** 적용되는 표시 설계다(차원 누락 L1 해소).

- `aggregate`/`summary_lines` 에 **그룹 차원 인자**(`by={"account","pane","tab","session"}`)
  추가. 기본은 현 동작(계정) 유지 — 하위호환.
- `TokenLogScreen` 서브탭에 **[패널]** 추가(`clientscreens.py:990-999`). 누르면
  버킷×패널(또는 탭) 표로 전환. 패널 라벨은 `tab:pane`(예 `2:3`)로 표기.
- 한 응답의 토큰은 **그 응답을 낸 패널**에 귀속(레코드의 `tab`/`pane` 이 곧 그 값)
  이라 추가 추적 로직 불필요 — 집계 차원만 켜면 된다.

---

## 4. 장단점 비교

| 관점 | JSONL(현행) | SQLite(제안) |
|---|---|---|
| 의존성 | 없음 | 없음(stdlib `sqlite3`) |
| 패널별 집계 | 함수만 고치면 가능(L1) | `GROUP BY` 로 자명 |
| 조회 비용 | 전체 읽기 O(N) 메모리 | 인덱스 질의, 유계 |
| IPC 페이로드 | 레코드 ≤5000 전송 | 집계 결과만 전송 |
| 대용량/이력 | 5000줄 절단·메모리 부담 | 강함 |
| 계정 정정 | 파일 전체 재작성 | `UPDATE` 한 방 |
| 보존(prune) | 곤란 | `DELETE`+`VACUUM` |
| **사람 검사** | **`grep`/`jq`/눈으로 즉시** | sqlite3 CLI 필요(불투명) |
| **디버그/이식** | 텍스트라 자명·복구 쉬움 | 바이너리·손상 시 복구 난이도↑ |
| 테스트 | 순수 함수·문자열 픽스처 단순 | in-memory(`:memory:`) DB 픽스처 |
| Windows | 텍스트 동일 | 파일 락/경로 주의(WAL 사이드카 `-wal`/`-shm`) |
| 코드량 | 최소(append 1줄) | 스키마·연결·쿼리·마이그레이션 |

**핵심 트레이드오프**: JSONL 의 가장 큰 실전 가치는 **사람이 grep/jq 로 바로 보고
고칠 수 있다**는 점이다(실제로 `gi…@github.com` 진단·정정을 grep+마이그레이션으로
처리했다). SQLite 는 규모·질의력을 얻는 대신 이 투명성을 잃는다.

---

## 5. 마이그레이션 경로(SQLite 채택 시)

1. **스키마/연결 계층** 신설: `pytmuxlib/usagedb.py` — `connect(path)`, `insert(rec)`,
   `query_agg(by, bucket, account, since)`, `update_accounts(...)`, `prune(before)`.
   `usagelog.py` 의 **순수 집계 규칙(버킷 포맷·UNKNOWN)** 은 재사용/공유.
2. **일회성 임포트**: 기존 `*.tokens.jsonl` → `db/claude-tokens.db` 적재 스크립트
   (`scripts/import_token_jsonl.py`). 멱등(이미 적재분 스킵 위해 `ts,pane` 등으로
   판정하거나 임포트 전 빈 DB 가정).
3. **이중 쓰기 과도기(옵션)**: 한동안 JSONL+DB 동시 append 로 안전망 → 검증 후 JSONL
   중단. 또는 단칼 전환 + JSONL 백업 보존.
4. **서버 전환**: `_log_tokens`(`server.py:268-278`)·시드(`server.py:280-287`)·
   `request_token_log`(`serverio.py:462-467`)를 DB 질의로 교체. 서버 측 집계 후
   **집계 결과**를 새 메시지 `t:"token_agg"` 로 전송(또는 기존 `token_log` 를 집계형
   페이로드로 변경).
5. **클라 전환**: `TokenLogScreen` 이 원본 레코드 대신 집계 결과를 렌더. [패널] 탭 추가.
6. **정정 도구**: `migrate_token_accounts.py` 에 DB 백엔드 분기(또는 신규
   `usagedb.update_accounts`). 동작/안전장치(미리보기·백업) 동일 유지.

각 단계는 독립 커밋·테스트 가능. 핵심 게이트: **임포트 round-trip 동치**(JSONL 합계 ==
DB 합계), **집계 동치**(기존 `summary_lines` 결과 == DB GROUP BY 결과).

---

## 6. 위험·완화
- **DB 손상**: 휘발 영역이라 최악엔 통계만 소실(본 흐름 무영향, append 실패는 현재도
  조용히 무시 `usagelog.py:47`). 완화: WAL+주기 백업(옵션), 손상 감지 시 새 DB 재생성.
- **투명성 상실**: 디버그/사후분석에서 grep 불가. 완화: `tokens dump` 류 명령으로
  DB→JSONL 덤프 제공(검사·이식 경로 유지).
- **Windows 락/사이드카**: `-wal`·`-shm` 파일 동반, 경로/권한 주의. 완화: 단일 writer
  전제 유지·`busy_timeout`·테스트 매트릭스에 Windows 포함(`test_windows_port` 패턴).
- **스키마 진화**: `PRAGMA user_version` 분기로 향후 컬럼 추가 흡수.

---

## 7. 권고(단계적)

- **1단계(즉시·저위험)** — 저장은 JSONL 유지, **집계에 패널/탭 차원 추가**.
  `aggregate`/`summary_lines` 에 `by=` 인자, `TokenLogScreen` 에 [패널] 서브탭.
  요구("패널별·계정별 통계")를 **포맷 변경 없이** 충족. 데이터는 이미 충분(§0).
- **2단계(승격 트리거 충족 시)** — 아래 중 하나라도 상시화되면 SQLite 로 이행(§5):
  - 로그가 커져 L2/L3(전체읽기·5000 절단)가 실제 통계 왜곡을 낳을 때,
  - 보존정책(오래된 사용량 prune)·장기 이력 질의가 필요할 때,
  - 계정 정정이 잦아 파일 재작성 비용/경합이 부담될 때.

현 규모(187KB·1,752줄)에선 **1단계로 충분**하고, SQLite 는 트리거가 분명해질 때
도입하는 것이 비용 대비 합리적이다.

---

## 8. 미해결/후속 질문
- 패널 귀속의 의미: 패널은 재사용·종료된다. **과거 패널 id 의 통계 표시**를 그대로 둘지,
  세션 종료 패널을 묶어 보일지(현재 레코드는 당시 `tab:pane` 스냅샷).
- 집계를 서버로 옮기면 클라의 `summary_lines`(표시 로직)와 **단일 진실원**을 어디 둘지
  (순수 규칙은 `usagelog`/`usagedb` 공유 권장).
- `/usage`(M19 실측 한도)는 본 저장소와 **독립**(별도 dict) — 본 설계 범위 밖.
