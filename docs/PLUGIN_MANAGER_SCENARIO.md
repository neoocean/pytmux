# 플러그인 관리 팝업 — 동작·설계 시나리오 (TODO)

> **상태**: 🟡 **계획(미구현)**. 본 문서는 "설치된 플러그인 목록을 표시하고 각각을
> 켜고/끄는" 관리 팝업의 설계 기준선이다. 구현 전 단계이며, 현행 플러그인 로더의
> **delete-to-disable** 계약(디렉토리 삭제=기능 제거)을 **깨지 않고** 그 위에 **소프트
> 토글(설정 기반 비활성)** 을 얹는 경로를 명세한다.
> **관련**: [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md)(플러그인 계약·통합 지점·무게 규칙) ·
> [REC_SCENARIO.md](REC_SCENARIO.md)(REC→`plugins/rec/` 추출 — 본 문서의 §6 기본 OFF
> 요건의 선행) · [NC_SCENARIO.md](NC_SCENARIO.md)(클라 모달+서버 왕복 형식) ·
> [SECURITY_REVIEW.md](SECURITY_REVIEW.md) §F4(REC 기본 ON·raw 민감출력 — §6 이 해소).
>
> **한 줄 요약**: `:plugins` 팝업이 레지스트리가 발견한 플러그인을 이름·설명·상태(켜짐/
> 꺼짐)로 나열하고, 항목 토글로 `opts.json` 의 **비활성 집합**을 갱신한다. 비활성 플러그인은
> 다음 로드(서버/클라)에서 건너뛴다. **요청 핵심**: REC 가 `plugins/rec/` 로 추출된 뒤엔
> 그 플러그인이 **깃헙 배포 기본 OFF**(`default_enabled = False`)여서, 새 클론은 raw PTY
> 캡처를 자동으로 켜지 않는다(F4 해소). 사용자 자기 머신의 `opts.json` 선택은 그대로 존중.

## 목차
- [1. 목표 / 비목표](#1-목표--비목표)
- [2. 사용자 흐름(UX)](#2-사용자-흐름ux)
- [3. 화면 레이아웃 — 팝업](#3-화면-레이아웃--팝업)
- [4. 아키텍처 — 새 인프라](#4-아키텍처--새-인프라)
- [5. 적용 시점 — 끄고 켜기의 발효](#5-적용-시점--끄고-켜기의-발효)
- [6. REC 기본 OFF(깃헙 배포) — `default_enabled`](#6-rec-기본-off깃헙-배포--default_enabled)
- [7. 단계별 구현 계획](#7-단계별-구현-계획)
- [8. 위험·엣지 케이스](#8-위험엣지-케이스)
- [9. 테스트 게이트](#9-테스트-게이트)

---

## 1. 목표 / 비목표

**목표**
- 현재 설치(디렉토리 존재)된 플러그인을 한 화면에 나열: 이름·한 줄 설명·카테고리·현재
  상태(켜짐/꺼짐).
- 각 항목을 그 자리에서 **토글**(Space/Enter)하여 켜고 끈다.
- 토글 결과를 **영속**(`opts.json`)하고, **발효 방식**(즉시 vs 재시작 필요)을 명확히 안내.
- 깃헙 배포본의 **민감 기본값**(REC)을 코드로 OFF 로 보낼 수 있는 메커니즘 제공.

**비목표(이번 범위 아님)**
- 원격 설치/마켓플레이스/다운로드(설치는 여전히 디렉토리 배치=수동).
- 플러그인 **설정값** 편집(plugin_opts 의 개별 키 — 별도 화면). 본 팝업은 **on/off 만**.
- delete-to-disable 폐기(소프트 토글은 그 위 레이어 — 디렉토리 삭제는 여전히 하드 제거).

## 2. 사용자 흐름(UX)

1. `:plugins`(또는 `:plugin-manager`) 명령 / 우클릭 메뉴 / (선택) prefix 키로 팝업을 연다.
2. 목록에서 ↑↓ 이동, **Space/Enter 로 켜짐↔꺼짐 토글**. `[x] claude-code`, `[ ] rec` 형태.
3. 토글 즉시 `opts.json` 의 `disabled_plugins` 가 갱신된다(서버 왕복 1회).
4. 발효 안내: 클라 전용 플러그인은 **다음 클라 재접속**, 서버 기여(믹스인/서버훅) 플러그인은
   **서버 재시작**에 발효 — 팝업 하단에 "변경 N건 · 서버 재시작 시 적용"을 표시하고
   `r` 로 [restart-server](RESTART_SCENARIO.md)(드라이런 우선)로 바로 연결.
5. ESC/`q` 로 닫는다.

## 3. 화면 레이아웃 — 팝업

```
┌─ 플러그인 관리 ───────────────────────────────┐
│ [x] claude-code    Claude 토큰 추적·자동화      │
│ [x] clock          패널 큰 시계 오버레이         │
│ [x] calendar       달력 오버레이                │
│ [x] ncd            디렉토리 트리(NCD)            │
│ [x] ime-indicator  한/영 입력 배지              │
│ [ ] rec            패널 출력 캡처(기본 OFF)      │  ← default_enabled=False
│ [x] p4-show-…      제출 체인지리스트 뷰           │
│ …                                              │
├───────────────────────────────────────────────┤
│ Space 토글 · r 재시작 적용 · ESC 닫기            │
│ 변경 1건 — 서버 재시작 시 적용                   │
└───────────────────────────────────────────────┘
```
- `[x]`=켜짐 `[ ]`=꺼짐. 끈 항목은 흐리게(dim).
- 디렉토리 삭제로 사라진 플러그인은 **목록에 없다**(설치 안 됨과 구분 불필요 — 레지스트리
  발견분만 표시).

## 4. 아키텍처 — 새 인프라

현행은 **런타임 토글 부재**: `_discover()`(`plugins/__init__.py:20`)가 `plugins/` 하위
서브패키지를 전수 import 하여 `PLUGIN` 을 모을 뿐이다. 토글을 위해 아래를 신설한다.

### 4.1 비활성 집합(영속)
- `opts.json` 에 `disabled_plugins: ["rec", …]`(플러그인 `name` 문자열 목록)을 둔다.
- **서버 소유**: opts 는 서버가 `server_opts_init`/`server_opts_serialize`(`plugins/__init__.py:146,157`)
  로 로드·직렬화한다. 비활성 집합도 같은 경로로 영속. 클라가 토글하면 `cmd` 로 서버에
  전달→서버가 opts.json 갱신.

### 4.2 로더가 비활성 집합을 존중
- `_discover()`/`load()` 에 `disabled: set[str]` 인자를 추가. 비활성 이름의 플러그인은
  **import 는 하되 레지스트리에서 제외**(또는 아예 import 생략 — §8 의 무게/부작용 고려).
- 서버·클라 **양쪽 프로세스**가 각자 `load()` 시 같은 비활성 집합(opts)을 읽어야 일관.
  클라는 attach 시 서버가 status/hello 로 내려준 비활성 집합을 받아 자기 `load()` 에 반영.

### 4.3 플러그인 메타데이터 확장(표시용)
- 현재 `PLUGIN` 은 `name` 만 보장(`clock/__init__.py:29`). 팝업 표시를 위해 **선택적**
  `description: str`(한 줄)·`category: str` 속성을 추가(덕 타이핑 — 없으면 빈 문자열).
- `default_enabled: bool = True` 속성 신설(§6).

### 4.4 명령·팝업 배선(클라)
- 신규 플러그인 후보 `plugins/plugin-manager/`(자기 자신도 끌 수 있나? — §8) 또는 코어
  명령. 클라 모달은 ncd 패턴(`app.push_screen(...)`, `ncd/__init__.py:74`)을 따른다.
- 토글 확정 시 `{"t":"cmd","action":"set_plugin_enabled","name":…,"on":bool}` 서버 전송
  → 서버가 비활성 집합·opts.json 갱신 후 ack(+발효 안내).

## 5. 적용 시점 — 끄고 켜기의 발효

토글은 **설정만 바꾸고 즉시 모든 기여를 회수하지 않는다**(안전·단순). 발효 규칙:

| 플러그인 유형 | 예 | 발효 시점 |
|---|---|---|
| 클라 전용(오버레이/모달) | clock·calendar·ncd·ime-indicator | 다음 **클라 재접속**(또는 클라 `load()` 재실행) |
| 서버 기여(믹스인·서버훅·Pane 소유) | claude-code·(추출 후)rec | **서버 재시작** ([RESTART_SCENARIO.md](RESTART_SCENARIO.md), 드라이런 우선) |

이유: claude-code 는 `server_mixin()` 으로 `Server` 생성 시점에 합성되고 Pane 필드를
소유한다(panestate). 가동 중 떼어내려면 합성 해제·상태 정리가 필요해 위험하다 — **재시작
경계**가 깨끗하다. 팝업은 "변경분이 재시작에 적용됨"을 명시하고 restart 로 안내한다.

## 6. REC 기본 OFF(깃헙 배포) — `default_enabled`

**요청 핵심.** [REC_SCENARIO.md](REC_SCENARIO.md) 대로 REC 가 `plugins/rec/` 로 추출되면,
그 플러그인은 `default_enabled = False` 로 선언한다. 의미를 분리한다:

- **설치(installed)** = 디렉토리 존재(레지스트리 발견). REC 디렉토리는 배포에 포함된다.
- **활성(enabled)** = `name not in disabled_plugins`. **사용자 설정이 없을 때**의 초기값을
  `default_enabled` 가 정한다 — REC 는 `False` 라, **`opts.json` 에 명시 선택이 없으면 OFF**.
- 사용자가 팝업/명령으로 REC 를 켜면 `disabled_plugins` 에서 빠지고(또는 활성 목록에 명시),
  그 선택은 자기 머신 `opts.json` 에 남아 **존중**된다.

효과: **깃헙에서 새로 클론한 사용자는 raw PTY 캡처가 자동으로 켜지지 않는다**(F4 의 "기본
ON·민감 출력 무손실 기록" 해소). 반면 현재 본인 머신처럼 이미 REC 를 쓰던 환경은 opts.json
선택이 유지된다.

> **현행과의 차이(중요)**: 지금 REC 는 **코어 기능·기본 ON**(`capture` 토글, SECURITY_REVIEW
> §F4). 이 변경은 ① REC 를 플러그인으로 추출(REC_SCENARIO) ② 그 플러그인 `default_enabled
> = False` 라는 **두 단계의 합**으로만 "배포 기본 OFF"가 성립한다. 추출 전에는 본 팝업이
> REC 를 토글 대상으로 보여줄 수 없다(아직 플러그인이 아님).

구현 노트: 초기 상태 결정은 "opts 에 `disabled_plugins` 키가 **존재하지 않거나** 그 플러그인을
처음 보는 경우 → `default_enabled` 따름". 한 번이라도 사용자가 결정하면 그 결정이 우선.

## 7. 단계별 구현 계획

1. **메타데이터**: `PLUGIN` 에 `description`/`category`/`default_enabled` 선택 속성 + 각
   기존 플러그인에 한 줄 설명 기입(없어도 빈 값 — 무해).
2. **비활성 집합 영속**: opts.json `disabled_plugins` + `server_opts_init/serialize` 연동.
3. **로더 존중**: `load(disabled=…)` 가 비활성 제외. 서버·클라 양쪽이 같은 집합을 읽도록
   hello/status 로 전달.
4. **팝업(클라)**: `:plugins` → 모달 목록·토글. ncd 모달 패턴 재사용.
5. **토글 왕복**: `set_plugin_enabled` cmd → 서버 opts 갱신·ack·발효 안내.
6. **REC 추출 후**(REC_SCENARIO 완료 의존): `plugins/rec` `default_enabled=False` 선언 +
   초기상태 규칙(§6) 적용. SECURITY_REVIEW §F4 갱신(기본 OFF 로 해소 표기).

각 단계 `tests/run.py` green + git push + p4 번호 CL([publish workflow](../MEMORY.md)).

## 8. 위험·엣지 케이스

- **자기 자신 비활성**: 관리 팝업이 플러그인이라면 자신을 끄면 다시 못 켠다 → 관리 UI 는
  **코어** 명령으로 두거나(권장), 플러그인이면 자기 자신은 토글 목록에서 제외.
- **import 부작용**: 일부 플러그인은 import 시점에 부작용이 없도록 설계됐지만(무게 규칙,
  PLUGIN_SYSTEM §무게), 비활성을 "import 생략"으로 할지 "import 후 제외"로 할지 결정 필요.
  안전 기본: **import 후 레지스트리 제외**(발견·메타데이터는 보되 기여만 차단) — 단,
  서버 믹스인은 생성 시 합성되므로 재시작 발효(§5)와 함께 "import 생략"이 더 깨끗.
- **Pane 상태 소유 플러그인**(claude-code panestate) 비활성 시 직렬화/복원 호환: 비활성
  중엔 Pane Claude 필드가 안 생겨도 코어 read 는 getattr 기본값(기존 delete-to-disable
  동치). 재활성 시 새 세션부터 정상.
- **순서/의존**: 현재 플러그인 간 하드 의존은 없음(레지스트리 합산). 의존 생기면 별도 처리.
- **claude-code 끔**: 토큰 추적/자동화 전부 정지 — 발효는 재시작 경계로 한정해 가동 중
  혼란 방지.

## 9. 테스트 게이트

- 로더: `disabled={"clock"}` → registry 에 clock 기여 부재, 나머지 정상(neg/pos).
- 영속: `disabled_plugins` opts 라운드트립(`server_opts_serialize`→재로드).
- `default_enabled=False` 더미 플러그인: opts 무설정 시 비활성, 명시 켬 후 활성 유지.
- 팝업: 토글→cmd→opts 갱신 ack(헤드리스). 자기 자신/코어-관리 UI 제외 불변식.
- REC(추출 후): 새 opts(키 부재) → 캡처 미동작(F4 회귀), 명시 켬 → 캡처 동작.
