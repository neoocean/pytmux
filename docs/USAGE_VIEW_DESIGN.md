# usage-view 플러그인 설계 — Claude 사용량 화면 + 리셋 카운트다운

이 문서는 새 pytmux 플러그인 **`usage-view`** 의 설계를 정의한다. 명령 한 번으로
`scripts/claude-token-viewer`(참조 스크립트)와 **유사한 Claude usage 화면**(한도 막대
그래프)을 띄우고, **다음 토큰 리셋까지 남은 시간을 큰 카운트다운**으로 보여 준다.
화면은 **팝업(모달, 기본) · 새 탭 · 현재 패널 오버레이** 세 방식으로 표시할 수 있다.

- 플러그인 작성 규칙·계약은 [PLUGIN_MANUAL.md](PLUGIN_MANUAL.md), 시스템 배경은
  [PLUGIN_SYSTEM.md](PLUGIN_SYSTEM.md).
- 참조 스크립트(독립 TUI): `//office/scripts/claude-token-viewer/run.py`
  (claude.ai 웹 API + `sessionKey` 쿠키로 정밀한 `resets_at` 을 얻어 막대+5행 블록
  카운트다운을 그리는 텍스추얼 앱).

---

## 1. 목표와 요구사항

사용자 요청:

> 커맨드를 입력하면 참조 스크립트처럼 Claude usage 화면과 유사한 화면을 보여주고,
> 다음 토큰 리셋까지 남은 시간을 팝업에 보여 준다. 표시 방법은 **새 탭 / 현재 탭(패널)
> / 팝업** 중 하나로 띄울 수 있다.

확정된 설계 결정(2026-06-11):

| 항목 | 결정 | 비고 |
|------|------|------|
| **데이터 소스** | **기존 스크랩 재사용** | claude-code 플러그인이 이미 숨은 `/usage` 스크랩으로 얻는 `usage_limits` 를 그대로 읽는다. 추가 의존성·자격증명 없음. |
| **배치** | **새 독립 플러그인** `pytmuxlib/plugins/claude-token-usage-view/` | delete-to-disable 모범 사례. usage_limits 는 `getattr` 로 부드럽게 참조. |
| **기본 표시** | **팝업(모달)** | 세 모드 모두 지원하되 무인자 기본은 팝업. |

---

## 2. 데이터 소스: 기존 스크랩 재사용 (`usage_limits`)

claude-code 플러그인의 서버 측은 숨은 `claude` 세션을 띄워 `/usage` 패널을 pyte 로
렌더·파싱해(`usageprobe.query_usage` → `claude.parse_usage`) 다음 형태의 dict 를
status 메시지 `usage_limits` 로 모든 클라이언트에 보낸다(서버 `_usage`):

```python
{
  "session":     {"pct": 41, "reset": "2pm (Asia/Seoul)"},
  "week_all":    {"pct": 14, "reset": "Jun 13 at 3am (Asia/Seoul)"},
  "week_sonnet": {"pct": 0,  "reset": "..."},
  "account":     "<email>'s Organization",   # 있을 때만
}
```

클라이언트는 이 값을 `app.status.usage_limits` 로 보관하고, 신선도(초)는
`app.status.usage_age_sec` 로 함께 받는다(2분 이상 묵으면 'N분 전 실측' 표기).

**usage-view 는 이 두 값만 읽는다.** 새로운 네트워크 호출·자격증명·의존성을 도입하지
않는다. 화면을 열 때 최신성을 위해 `app.send_cmd("refresh_usage")` 로 **재스크랩을
요청**할 수 있다(이 명령은 claude-code 가 처리 — 없으면 무응답·무에러).

### 2.1 정밀도 한계 (중요)

참조 스크립트는 웹 API 의 ISO `resets_at`(초 단위 정확)을 쓰지만, **스크랩 데이터의
`reset` 은 사람이 읽는 문자열**("2pm", "Jun 13 at 3am")이다. 따라서 카운트다운은
**분 단위 근사**다. usage-view 는 이 문자열을 가장 가까운 미래 시각으로 파싱해
`Dd HHh MMm`(또는 1시간 미만이면 `MMm SSs`) 형태로 카운트한다(§5.2). 초 단위 정밀
카운트다운이 필요하면 향후 웹 API 옵션(§9)을 켤 수 있게 경계를 열어 둔다.

---

## 3. 플러그인 구조

```
pytmuxlib/plugins/claude-token-usage-view/
├── __init__.py    # PLUGIN 객체 + 계약(명령 메타·디스패치·메시지 핸들러). 가벼움.
│                  #   textual/rich 를 최상단에서 import 하지 않는다(서버도 읽음).
├── screen.py      # 팝업/탭 화면(textual ModalScreen·Screen). 열 때 지연 import.
├── overlay.py     # 현재-패널 오버레이 그리기(순수 함수). client_overlay 에서 지연 import.
└── reset.py       # reset 문자열 → datetime 근사 파서 + 카운트다운 포맷(순수, 테스트 용이).
```

`__init__.py` 는 **모듈 레벨 `PLUGIN`** 만 노출하고 무거운 import 를 피한다. 화면·오버
레이·시각 파싱은 실제로 그릴 때 메서드 안에서 지연 import 한다(무게 규칙 §7).

**delete-to-disable**: 이 디렉토리를 통째로 지우면 `usage-view` 명령은 검색·자동완성·
디스패치 어디에도 안 잡히고, 현재-패널 오버레이도 `client_overlay` 훅이 빈 루프라 안
그려진다. 코어는 무에러로 그대로 동작한다.

---

## 4. 명령과 계약

```python
COMMANDS = [
    ("usage-view", "Claude 사용 한도 + 다음 리셋 카운트다운 화면 "
                   "(usage-view [popup|tab|pane], 기본 popup; 별칭 token-viewer)", "Claude"),
]
NOARG = {"usage-view", "token-viewer", "usage-clock"}
PANE_SCOPED = {"usage-view"}   # pane 모드일 때 대상(활성) 패널을 밝게 표시
```

- **무인자** `usage-view` → 기본 **팝업**.
- `usage-view popup|tab|pane` → 지정 모드.
- 별칭: `token-viewer`, `usage-clock`.

> 명령 표면을 새로 만들므로 PLUGIN_MANUAL §4.4 의 **모든 소비 지점**(CommandListScreen,
> PromptScreen `?`, 자동완성, 팔레트)이 레지스트리 병합으로 자동 노출됨을 확인한다 —
> 코어를 건드리지 않는다.

`handle_command` 골격:

```python
def handle_command(self, app, c, args):
    if c not in ("usage-view", "token-viewer", "usage-clock"):
        return False
    mode = (args[0].lower() if args else "popup")
    if mode not in ("popup", "tab", "pane"):
        mode = "popup"
    app.send_cmd("refresh_usage")            # 최신화 시도(claude-code 없으면 no-op)
    app.open_usage_view(mode)                # attach_client 가 설치한 인스턴스 글루
    return True
```

`attach_client` 에서 `app.open_usage_view` / `app.usage_view_panes`(오버레이 토글
집합)를 인스턴스에 설치한다(clock 플러그인의 `toggle_clock`/`clock_panes` 패턴과 동일).

---

## 5. 화면 설계

세 모드 모두 **동일한 렌더 콘텐츠**(한도 막대 + 카운트다운 시계)를 쓰고, **담는 그릇과
크기만** 다르다. 콘텐츠 생성은 한 곳(`screen.py` 의 헬퍼 / `overlay.py` 의 순수 함수)에
모은다.

![usage-view 팝업(기본) — 한도 막대(%는 줄 오른쪽 끝에 우측정렬, 리셋은 막대 뒤) + "다음 리셋: 세션 5h" 블록 카운트다운 시계](image/30-usage-view.svg)

> 위 스크린샷은 `scripts/gen_screenshots.py` 의 `usage_view` 장면(`30-usage-view`)이
> 실제 클라이언트를 헤드리스로 운전해 생성한다(수작업 캡처 아님 — PLUGIN_MANUAL §11).

### 5.1 한도 막대 — 코어 `usage_bar_lines` 재사용

`pytmuxlib.clientscreens.usage_bar_lines(usage, width, age_sec)` 가 이미 `usage_limits`
dict 를 라벨+막대+%+리셋 줄 목록으로 만든다(세션 5h·주 전체·주 Sonnet, stale 표기 포함).
팝업·탭 모드는 이 함수를 그대로 호출해 막대 영역을 채운다(참조 스크립트의 BucketPanel
대응). 데이터가 없으면 "/usage 한도 데이터 없음 — Claude 패널에서 /usage 먼저 실행"
안내만 표시한다(기존 `_open_usage_panel` 와 같은 폴백).

### 5.2 카운트다운 시계 — `_CLOCK_FONT` 재사용 + reset 파서

- **블록 폰트**: 코어 공유 자산 `clientrender._CLOCK_FONT`(3×5 블록, 시계·달력 공유)를
  재사용해 `HH:MM:SS`/`Dd HH:MM` 를 큰 글자로 그린다 — 별도 폰트 자산을 안 만든다.
- **대상 버킷**: 기본은 `session`(5시간 세션) 리셋 — 가장 자주 차는 한도. 화면엔 모든
  버킷의 리셋도 작은 줄로 함께 표시.
- **`reset.py` (순수 함수, 테스트 핵심)**:
  - `parse_reset_to_dt(s, now) -> datetime|None`: "2pm (Asia/Seoul)", "Jun 13 at 3am"
    등을 **가장 가까운 미래 시각**으로 파싱(시각만 있으면 오늘/내일 중 미래, 날짜가
    있으면 그 날짜). 타임존 괄호는 표시 일관성을 위해 로컬로 간주(스크랩이 이미 사용자
    로캘 기준). 못 파싱하면 None → 카운트다운 숨김(막대는 그대로).
  - `fmt_countdown(td) -> str`: `Dd HHh MMm` / `HHh MMm SSs` / `MMm SSs` 단계 포맷
    (참조 스크립트 `fmt_td` 미러).
  - `urgency_color(td)`: 30분 미만 빨강, 1시간 미만 노랑, 그 외 시안(참조 `_clock_style`).
- **1초 틱**: 팝업/탭 화면은 `set_interval(1.0, ...)` 로 매 초 카운트다운만 다시 그린다
  (API 폴링과 독립 — 참조 스크립트와 동일). 오버레이 모드는 `client_tick` 훅이 1초마다
  True 를 돌려줘 코어가 재합성(clock 플러그인과 동일).

### 5.3 모드별 그릇

| 모드 | 그릇 | 비고 |
|------|------|------|
| **popup**(기본) | `ModalScreen`(중앙, 90%×자동), Esc/바깥클릭 닫기 | ncd `NcdScreen` 패턴. 빠른 글랜스. 카운트다운이 상단에 크게. |
| **tab** | 풀스크린 비모달 `Screen`(전체 차지) | 참조 스크립트의 넉넉한 멀티버킷+큰 시계 레이아웃. 작업하다 돌아올 수 있게 화면 스택에 유지. |
| **pane** | `client_overlay` 훅으로 활성 패널 위에 그림 | clock/calendar 패턴. 뒤 패널은 dim. Shift+ESC/패널 클릭으로 닫기(`client_close_overlay`). 토글 상태는 `app.usage_view_panes` 집합. |

> **"새 탭" 에 관한 설계 메모(중요)**: pytmux 의 서버 탭(`new_window`)은 **항상 fresh
> 셸 패널**을 만들고, 스크랩 데이터는 **클라이언트**에 있으므로, usage-view 의 "탭"은
> 서버 탭이 아니라 **클라이언트 풀스크린 화면**으로 구현한다(데이터 일관성·delete-to-
> disable 유지). 따라서 서버 탭 바엔 나타나지 않는다 — 이 점은 알려진 한계로 문서화한다.
> 진짜 PTY 탭에서 독립 모니터를 원하면, 별도로 참조 스크립트(`run.py`)를 새 윈도우의
> 패널 명령으로 띄우는 방식을 §9 향후 옵션으로 둔다(단 그 경로는 웹 API·sessionKey 사용).

### 5.4 레이아웃(참조 스크립트 미러, 텍스트 스케치)

```
┌─ Claude 사용 한도 ───────────────────────────────────────────┐
│  세션 5h    ██████████████░░░░░░░░░░  41%   ↻ 2pm            │
│  주 전체    ███░░░░░░░░░░░░░░░░░░░░░░  14%   ↻ Jun 13 3am     │
│  주 Sonnet  ░░░░░░░░░░░░░░░░░░░░░░░░    0%                     │
│  계정(/usage): <email>'s Organization                        │
│                                                               │
│  다음 세션 리셋까지                                            │
│     ██  ██   ███ ███      █  ███     (HH:MM:SS, _CLOCK_FONT)  │
│     █ █ █ █  █ █  █                                            │
│  (3분 전 실측 — 갱신 [u])                                      │
└───── Esc 닫기 · [u] 갱신 · [p]opup/[t]ab/[a]pane 전환 ────────┘
```

짧은 터미널 폴백: 참조 스크립트처럼 공간이 모자라면 블록 시계를 1줄 숫자 시계로 접고
(좁으면) 막대 폭을 24→16→8 로 줄인다(`usage_bar_lines` 가 이미 width 기반 분기).

---

## 6. 코어·타 플러그인과의 경계

- **읽기 전용 소프트 참조**: `getattr(app.status, "usage_limits", None)`,
  `getattr(app.status, "usage_age_sec", None)`. claude-code 가 없으면 None → "데이터
  없음" 안내. 플러그인끼리 하드 import 금지(PLUGIN_MANUAL §6.2 규율).
- **갱신 요청**: `app.send_cmd("refresh_usage")` — claude-code 의 `server_command` 가
  처리(없으면 무응답). usage-view 는 회신을 직접 기다리지 않고, 다음 status 의
  `usage_limits` 갱신을 화면이 자동 반영(팝업/탭은 status 흡수 시 re-render, 오버레이는
  `client_overlay` 가 매 합성마다 최신값을 읽음).
- **코어 재사용 자산**: `usage_bar_lines`(clientscreens), `_CLOCK_FONT`·`put_cell`
  (clientrender). 이들은 코어 공용이라 usage-view 를 지워도 코어에 죽은 코드가 안 남는다.

---

## 7. 무게 규칙 준수

`__init__.py` 는 textual/rich/datetime 무거운 의존을 최상단에서 import 하지 않는다
(서버 프로세스도 `plugins.load()` 로 읽는다). `screen.py`(textual)·`overlay.py`(rich
Style)·`reset.py`(datetime) 는 각 훅 메서드 본문에서 지연 import 한다. 서버는 usage-view
의 명령 메타데이터만 읽고 끝난다(이 플러그인은 서버 훅을 하나도 구현하지 않는다 —
데이터는 claude-code 가 이미 status 로 싣는다).

---

## 8. 테스트 계획

PLUGIN_MANUAL §10 두 갈래를 따른다.

1. **순수 함수 단언** (`tests/test_plugin_usage_view_reset.py`):
   - `parse_reset_to_dt("2pm (Asia/Seoul)", now=...)` → 오늘/내일 14:00 중 미래.
   - `parse_reset_to_dt("Jun 13 at 3am", now=...)` → 해당 날짜 03:00.
   - 못 파싱 문자열 → None.
   - `fmt_countdown` 단계 포맷·`urgency_color` 임계(30m/60m) 경계값.
   - 오버레이 순수 함수: usage_limits dict + 셀 그리드 → 막대/시계 셀이 채워지는지,
     데이터 없으면 안내만, 좁은 폭 폴백.
2. **delete-to-disable 계약** (`tests/test_plugin_contract.py` 에 케이스 추가):
   - usage-view 만 뺀 Registry 로 `reg.commands`/`noarg`/`completions` 에 usage-view
     명령이 **하나도 없음**, `client_tick(None) is False`, `client_overlay(...)` no-op,
     `handle_command(None, "usage-view", []) is False` 확인.
   - usage-view 없이 클라 앱 구성·합성·ESC·입력해도 프레임이 안 깨짐.

---

## 9. 향후 확장 (경계만 열어 둠)

- **웹 API 정밀 모드(옵션)**: 참조 스크립트의 `ClaudeApiClient`(curl_cffi+sessionKey)를
  선택적 백엔드로 끼워 초 단위 정확한 `resets_at`·Opus/Sonnet 세부 버킷·100% 소진 ETA
  예측을 제공. 자격증명 파일과 `curl_cffi` 의존이 추가되므로 기본 OFF, opts 설정으로 ON.
- **진짜 PTY 탭 모니터**: 새 윈도우를 만들고 그 패널에 `python .../claude-token-viewer/run.py`
  주입 → 서버 탭 바에 보이는 상시 모니터(웹 API 경로). usage-view 의 클라 화면과 별개 옵션.
- **소진 예측**: 스크랩 % 시계열을 영속 로그(usagedb)로 회귀해 참조 스크립트의 'to 100%'
  ETA 를 분 단위로 근사.

---

## 10. 체크리스트 (구현 시)

- [ ] `__init__.py` 가 모듈 레벨 `PLUGIN` 노출, 최상단 textual/rich/datetime import 없음.
- [ ] 상태/메서드(`open_usage_view`·`usage_view_panes`)를 `attach_client` 에서 인스턴스 설치.
- [ ] 타 플러그인 데이터(`usage_limits`)는 `getattr` 로만 참조(하드 import 금지).
- [ ] 디렉토리 삭제 시 코어 무에러 — `test_plugin_contract.py` 케이스 추가.
- [ ] 세 모드(popup/tab/pane) 모두 동일 렌더 콘텐츠 공유, 그릇만 다름.
- [ ] 카운트다운은 `_CLOCK_FONT`·`reset.py` 재사용, 1초 틱으로 갱신.
- [ ] 데이터 없음/파싱 실패 폴백 경로 확인(빈 화면 금지, 안내 표시).
- [ ] 스크린샷 필요 시 `scripts/gen_screenshots.py` 에 장면 추가 후 재생성.

---

> **요약 한 줄**: `usage-view` 는 claude-code 가 이미 스크랩해 둔 `usage_limits` 를
> `getattr` 로 읽어, 코어 `usage_bar_lines`+`_CLOCK_FONT` 를 재사용해 한도 막대와 다음
> 리셋 카운트다운을 **팝업(기본)·탭·패널 오버레이**로 그리는 독립 플러그인이다. 새
> 네트워크·자격증명·의존성 없이, 디렉토리를 지우면 조용히 사라진다.
