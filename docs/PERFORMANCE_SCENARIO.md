# pytmux 성능/반응성 가속 — 동작 시나리오

> **상태**: 🟡 설계/제안(미구현). 본 문서는 "더 빨리 실행되고, 모든 인터페이스가 더
> 빨리 응답하게" 만들기 위한 **근거 기반 최적화 시나리오**다. 각 항목은 코드 근거
> (`file:line`)·개선안·예상 효과·위험·검증 게이트를 갖는다.
> 관련: [HANDOFF.md](HANDOFF.md) §9(throughput 작업 이력)·§10 · [WINDOWS_TESTING.md](WINDOWS_TESTING.md)
> 측정 도구: `scripts/bench.py`(startup·탭/패널 반응성·출력폭증 3축), `poc/feed_profile.py`(feed/render 핫패스).

## 0. 측정 우선 원칙 (필수)

성능 변경은 **추측이 아니라 측정으로** 한다. 모든 시나리오는 아래 순서를 따른다.

```sh
python scripts/bench.py            # 기준선(baseline) — docs/benchmark/<os>/<ts>.md 생성
# ... 변경 구현 ...
python scripts/bench.py            # 변경 후 — 같은 머신/같은 파라미터로 재측정해 비교
python tests/run.py                # 동작 불변 게이트(현 231 passed 유지)
```

- `bench.py` 는 **완전 헤드리스**(실 셸/ssh 불필요)로 결정적이다. 인터랙티브 ssh
  반응성은 실 Windows 박스가 필요(WINDOWS_TESTING.md §3-c).
- 회귀 게이트: 모든 시나리오는 **동작 불변**이 원칙 — `tests/run.py` 전부 통과가
  머지 조건이다. 새 동작(델타·적응형 주기 등)은 전용 회귀 테스트를 추가한다.

## 1. 배경과 목표

pytmux 는 **클라이언트–서버(데몬)** 구조다(HANDOFF §3). 체감 속도는 두 축으로 갈린다.

- **(A) 실행 속도** — `python pytmux.py` 입력부터 첫 화면이 그려질 때까지.
- **(B) 인터페이스 반응성** — 띄운 뒤 키 입력·패널 전환·출력 갱신이 즉각 반영되는 정도.

이미 상당한 최적화가 끝나 있다(§2). 본 문서는 **남은 레버**를 (A)·(B)로 나눠
우선순위와 함께 제시한다. 핵심 결론을 먼저 적으면:

> **(B)의 가장 큰 낭비 2건이 코드로 확정됨** — ① `_scan_claude` 가 dirty 와 무관하게
> **매 프레임(30Hz) 모든 탭의 모든 패널** 화면 텍스트를 join+정규식 스캔하고
> (`server.py:2124-2127`), ② dirty 패널은 **행 단위 diff 없이 전체 뷰포트를 매번
> 재직렬화·재전송**한다(`server.py:2418-2426`, `model.py:609-640`). 둘 다 "바뀐 것만
> 처리"로 바꾸면 idle·다중 패널·alt-screen 풀리페인트에서 CPU/소켓 비용이 크게 준다.

## 2. 이미 최적화된 부분 (재작업 금지)

새 제안이 이것들과 충돌하지 않게, 현 자산을 먼저 적는다.

| 영역 | 내용 | 근거 |
|------|------|------|
| 시작 | textual 지연 import(PEP 562 `__getattr__`) — 서버 하위프로세스·`ls`/`kill`/`cmd` 는 textual 미로드 | `pytmux.py:38-67` |
| 시작 | 서버 spawn 과 textual import **병렬화**(서버 부팅 중 클라 import) | `launcher.py:164-170` |
| 시작 | 서버는 textual 을 import 하지 않음(경량 의존만) | `server.py:1-19` |
| 시작 | 레이아웃 복원은 동기 JSON 역직렬화(셸 fork 없음, 빠름) | `server.py:1326-1347` |
| feed | pyte `HistoryScreen` → 경량 `_ScrollbackScreen`(`__getattribute__` 훅 제거, 2.75배) | `model.py:88-131`, HANDOFF §9 56558 |
| feed | 대량 출력 슬라이스 드레인(8KB) + reader pause/resume(루프 차단 56→13.6ms) | `server.py:176-248`, `protocol.py:27` 56560 |
| feed | 드레인 중 GC 비활성(버스트 GC 정지 30~85ms 제거) | `server.py:207-223` |
| feed | alt-screen 풀클리어 코얼레싱(중복 `2J/3J` 프레임 폐기) | `model.py:167-196`, `server.py:200-205` |
| flush | 패널 **dirty 플래그** — 안 바뀐 패널은 render/전송 생략 | `server.py:2419-2420` |
| flush | Claude 헤더 예약 디바운스(±1행 리사이즈 떨림 방지, 30프레임) | `server.py:2154-2164` |
| 입력 | 키 입력은 즉시 PTY write(비차단 async fire-and-forget) — 추가 버퍼 지연 없음 | `client.py:1348-1352`, `server.py:2817-2858` |
| 팝업 | 배경 디밍 즉시 합성 + `_darken_style` lru_cache | HANDOFF §10-A 56731 |

## 3. 시나리오 (B) — 인터페이스 반응성 (우선순위 높음)

### B1. `_scan_claude` dirty 게이팅 + 저주기화 ★최우선·저위험

**현상/근거**: `_flush_loop` 이 매 프레임(30Hz) `_scan_claude(sess, win)` 을 호출하고
(`server.py:2441`), 그 안에서 **모든 탭의 모든 패널**을 순회하며 패널마다
`txt = "\n".join(p.screen.display)` 로 전체 화면을 문자열로 만든 뒤
`claude_state`/`claude_usage`/`claude_account`/`claude_prompt`/`claude_feedback_prompt`
정규식을 돌린다(`server.py:2124-2129`). **dirty 검사가 전혀 없다** — 출력이 멈춘
패널도 초당 30회 join+정규식 스캔된다. 탭 12 × 패널 6 = 72 패널이면 idle 에도
초당 2160회 화면 join.

**개선안**:
1. **dirty 게이팅** — 패널이 마지막 스캔 이후 안 바뀌었으면(`p.dirty` 또는 별도
   `_scan_seq`) 스캔을 건너뛴다. Claude 상태는 화면이 바뀔 때만 바뀌므로 안전.
   비활성 탭 완료 감지(#22)도 dirty 일 때만 전이 검사하면 충분.
2. **저주기화** — Claude 휴리스틱은 30Hz 가 필요 없다. `_flush_loop` 카운터로
   N프레임마다(예 6프레임 ≈ 5Hz) 또는 별도 `asyncio` 태스크(예 200ms)로 분리.
   busy 스피너 텍스트가 매 프레임 바뀌어도 상태(busy/idle)는 5Hz 로 충분히 빠름.
   (디바운스 상수 `_HDR_CLAUDE_MISS=30`/`_DONE_IDLE_FRAMES=3` 은 새 주기에 맞춰 환산.)

**예상 효과**: idle·다중 패널에서 flush 루프 CPU 대폭 절감 → 입력 왕복 지터 감소.
출력 폭증 중에도 스캔이 join 을 덜 하므로 슬라이스 지연 여유.

**위험/검증**: Claude 상태 반영이 최대 ~200ms 늦어질 수 있음(허용 범위). 회귀:
busy→idle 전이·헤더 예약·피드백 자동 dismiss·토큰 누적이 새 주기에서도 동작하는지
`test_server` 에 주기/게이팅 테스트 추가. `bench.py` tabs_panes 의 비용은 직접
안 잡히므로(스캔은 flush 내부), 다중 패널 idle CPU 를 별도 마이크로벤치로 측정.

### B2. 화면 **행 단위 델타** 전송 ★고효과

**현상/근거**: dirty 패널은 `p.render(...)` 로 **전체 뷰포트**를 직렬화해
`{"t":"screen","pane","rows":<전 행>,"cursor"}` 를 통째로 보낸다
(`server.py:2421-2426`). `render()` 는 `window` 의 **모든 행·모든 셀**을 매번 다시
런(run)으로 만든다(`model.py:608-640`). Claude busy 는 alt-screen 을 매 프레임 풀
리페인트하지만 실제로는 **스피너 1글자·토큰 카운터 몇 글자만** 바뀌는 경우가 많다 —
그래도 80×24 전체(≈1920셀)를 재직렬화·재전송하고 클라가 `_composite` 로 다시 그린다.

**개선안**:
- 패널별 **직전 행 스냅샷**(또는 행 해시)을 보관하고, `render` 결과에서 **바뀐 행만**
  골라 `{"t":"screen-delta","pane","base":<frame_seq>,"rows":[[y, segs], ...],"cursor"}`
  로 보낸다. 변경 행이 임계(예 전체의 70%)를 넘으면 기존 full `screen` 으로 폴백.
- 클라이언트는 델타를 자기 `pane_content` 캐시에 행 단위로 적용한 뒤 `_composite`.
  (full/delta 혼용 시 base 시퀀스로 동기화 — 어긋나면 서버에 `resync` 요청.)

**예상 효과**: feed_profile 의 claude_busy 케이스에서 프레임당 JSON 바이트·write
·클라 합성 비용이 변경 행 비율만큼 감소(스피너만 바뀌면 수십 분의 1). 소켓 트래픽
감소는 ssh 반응성(§10 "수 분 내 급락")에도 직접 도움.

**위험/검증**: 상태 동기화 복잡도↑(full/delta 일관성, 재접속·resync 경로
`client.py` 재동기와 정합). 회귀: 델타 적용 후 화면이 full 재전송과 **셀 단위
동일**한지 골든 비교 + 임계 폴백·base 불일치 시 full 복구 테스트. `bench.py`
output_flood 의 `render_ms_frame`·`json KiB/frame` 으로 효과 정량화.

### B3. `render()` 셀 직렬화 비용 절감 (pyte dirty 줄 활용 + 스타일 키 캐시)

**현상/근거**: `render()` 는 셀마다 `_char_style(ch)` 로 dict 를 만들고
`tuple(sorted(style.items()))` 로 런 키를 만든다(`model.py:622-623`) — 셀당 dict 생성
+ 정렬. pyte 의 `screen.dirty`(바뀐 줄 번호 집합)는 **무시**되고 매번 전 행을 처리한다.

**개선안**:
1. **dirty 줄만 재직렬화** — `screen.dirty` 에 든 줄만 새로 런으로 만들고, 나머지
   행은 직전 결과를 재사용(B2 의 행 스냅샷과 결합하면 자연스럽다). 라이브(scroll==0)
   뷰포트는 버퍼 dirty 와 정합; 스크롤 중에는 full 폴백.
2. **스타일 키 캐시** — `(ch.fg, ch.bg, ch.bold, ...)` 동일 Char 는 같은 런 키/스타일
   dict 를 재사용(`functools.lru_cache` 또는 fg/bg 변환 메모이즈). 대부분 셀이 같은
   스타일이므로 적중률 높음(클라 `_darken_style` lru_cache 선례, 56731).

**예상 효과**: `render_all_ms`(`bench.py` tabs_panes)·`render_ms_frame`
(output_flood) 직접 감소. B2 와 함께 적용 시 시너지.

**위험/검증**: pyte `dirty` 의 클리어 시점(다음 feed) 정확히 따라가야 함 — 잘못
캐시하면 화면이 갱신 안 됨. 회귀: dirty 줄 외 변경이 새 나가지 않는지 + 스크롤·검색
하이라이트(`_match_abs`, `model.py:634`) 경로 골든 비교.

### B4. flush 메시지 배치 + `drain()` 1회

**현상/근거**: `write_msg` 는 메시지마다 `await writer.drain()` 한다
(`protocol.py` write_msg). flush 는 **패널마다·클라마다** `await write_msg(...)` 를
따로 호출한다(`server.py:2425-2426`). 패널 6 × 클라 1 이면 프레임당 6회 await/drain.

**개선안**: 한 프레임에서 한 클라로 갈 메시지(여러 screen + status)를 **하나의
bytes 버퍼에 프레이밍해 모아** `writer.write` 후 **drain 1회**. 또는 클라당 송신
큐를 두고 프레임 경계에서 일괄 flush.

**예상 효과**: await/drain 횟수·이벤트 루프 왕복 감소 → 다중 패널/다중 클라에서
flush 지연·지터 감소. 느린 클라가 다른 클라를 막는 시간도 축소(§10 slow-client).

**위험/검증**: 프레이밍 경계(길이 프리픽스) 보존, read_msg 가 연속 메시지를 그대로
파싱하는지. 회귀: `test_ipc`/`test_protocol` 에 배치 프레임 왕복 테스트.

### B5. 적응형 flush 주기(idle 저주기 / 활동 시 즉시)

**현상/근거**: `_flush_loop` 은 고정 30Hz 폴링이다(`server.py:2407-2409`) — **출력도
입력도 없는 idle** 에도 초당 30회 깨어나 전 세션을 훑는다. 반대로 키 입력 직후
화면 반영은 다음 폴 경계(최대 33ms)까지 기다린다.

**개선안**(택1/조합):
- **이벤트 구동 보강** — 패널이 dirty 가 되거나 입력이 들어오면 flush 를 **즉시
  한 번** 깨운다(`asyncio.Event`), 그 외엔 저주기(예 idle 5~10Hz)로 폴. 체감 입력
  지연을 33ms→~0 로 줄이면서 idle CPU 도 절감.
- **상한 60Hz 옵션** — 인터랙티브 구간에서만 일시 상향(전력/CPU 트레이드오프).

**예상 효과**: idle CPU↓(노트북 배터리·원격 부하), 입력→화면 지연↓.

**위험/검증**: flush 폭주 방지(coalesce — 한 프레임 내 다중 dirty 를 1회로), 기아
없는지. 회귀: 입력 직후 즉시 반영 + idle 시 저주기 + 폭주 출력에서 상한 유지.

### B6. screen 메시지 직렬화 포맷 (옵션: 더 빠른 인코딩)

**현상/근거**: 프레임마다 `json.dumps(screen)`(`protocol.py` write_msg). screen 이
가장 큰·가장 빈번한 메시지다. `bench.py` output_flood 의 `render_ms_frame` 에
json 직렬화가 포함돼 측정된다.

**개선안**: ① `json` → `orjson`(있으면) **옵션 가속**(없으면 표준 json 폴백,
의존성 강제 안 함). ② 더 공격적으로는 screen 행을 **바이너리 셀 인코딩**(런 길이
+ 스타일 인덱스 테이블)으로. B2(델타) 이후엔 페이로드가 작아져 효과는 작아지므로
**B2 이후 측정해 필요할 때만** 착수.

**위험/검증**: 의존성/이식성(Windows 휠), 클라 파서 동기 변경. 폴백 경로 필수.

## 4. 시나리오 (B′) — feed 처리량 천장 (환경 의존·보류 포함)

### B7. pyte feed 천장(~1.2–2.2 MB/s) — 남은 레버

**현상/근거**: feed 비용은 거의 100% pyte 내부다 — cProfile 상 `Screen.draw` 안의
`Char` namedtuple 할당(`_replace`/`_make`/`__new__`)이 천장의 정체(HANDOFF §9 ★,
`poc/feed_profile.py`). 전처리 정규식은 ~0.2s 로 무시 수준.

**개선안(난이도순)**:
- **(쉬움) 비가시 패널 feed 스로틀** — 비활성 탭/가려진 패널은 슬라이스 주기를
  늘려(렌더가 어차피 안 보임) 활성 패널 우선. dirty 정확성은 유지.
- **(중간) Char 재사용/슬롯화** — pyte `Char` 할당을 줄이는 경량 셀 표현 검토
  (pyte 포크/몽키패치 위험 — 회귀 비용 큼).
- **(보류) feed 별도 스레드** — 단일 asyncio 핫패스를 갈아엎는 큰 공사. 증상이
  Windows→ssh 환경 의존이라 헤드리스/로컬 재현·검증 불가 → **의도적 보류**
  (HANDOFF §10). 실 Windows 박스에서 B1~B5 로도 부족하다는 측정이 나오면 착수.

**검증**: `poc/feed_profile.py --profile` 로 천장 원인 재확인 후 착수.

## 5. 시나리오 (A) — 실행(startup) 가속

> startup 은 이미 (§2) 병렬화/지연 import 가 끝나 큰 낭비는 적다. 아래는 **측정
> 후 효과가 확인될 때만** 손대는 잔여 레버다. `bench.py` startup 의 `cold_import_ms`
> ·`framework_init_ms` 로 기준선을 먼저 잡는다.

### A1. textual import 비용 다이어트

**현상/근거**: 클라 cold start 는 **textual/rich import** 가 지배한다(서버는 미로드).
import 는 `build_client_app` 안으로 모았지만(`client.py:30-44`) 모듈 로드 자체가 큼.

**개선안**: ① 실제 안 쓰는 위젯/심볼 import 제거(트리셰이킹). ② 드물게 쓰는
모달 화면(`clientscreens`)·위젯의 import 를 **최초 사용 시점으로 지연**(첫 화면엔
불필요한 것). ③ `.pyc` 캐시 보장(`PYTHONPYCACHEPREFIX`/배포 시 사전 컴파일),
Python 3.11+ `frozen_modules` 효과 확인.

**검증**: `bench.py` `cold_import_ms` 비교 + 실 attach 첫 프레임까지 시각 측정
(`tests/ptyshot.py` 활용).

### A2. `wait_server` 초기 백오프 단축

**현상/근거**: `wait_server` 는 20ms 고정 간격 폴(`launcher.py:27-33`). 첫 probe 는
즉시 하지만, 서버가 막 떠 첫 probe 가 실패하면 다음 시도까지 20ms 고정 대기.

**개선안**: 초기 몇 회는 더 촘촘히(예 2→5→10→20ms 지수 백오프)로 서버가 빨리
뜬 경우의 체감 지연을 수십 ms 단축. 상한(총 4s)은 유지.

**검증**: 효과가 작을 수 있음 — `bench` 보다 실 attach 반복 측정으로 판단.

### A3. 첫 프레임: 활성 패널 우선 전송

**현상/근거**: `_send_full` 은 layout → **전 패널 순차 render/전송** → status 순
(`server.py:2368-2390`). 분할이 많으면 활성 패널 첫 그림이 비활성 패널 직렬화 뒤로
밀린다.

**개선안**: 활성 패널을 **가장 먼저** render/전송해 사용자가 보는 화면이 먼저 뜨게
하고 나머지는 이어서. (체감 first-paint 단축, 총량 동일.)

**검증**: 다중 패널 attach 시 활성 패널 first-paint 시각(ptyshot).

## 6. 우선순위 로드맵

효과×안전(낮은 위험·높은 효과 우선):

| 순위 | 시나리오 | 효과 | 위험 | 측정 지표 |
|---|---|---|---|---|
| 1 | **B1** scan_claude dirty 게이팅+저주기 | 높음(idle/다중패널) | 낮음 | idle CPU, flush 지터 |
| 2 | **B2** 행 단위 델타 전송 | 매우 높음(alt 풀리페인트·ssh) | 중 | json KiB/frame, slice ms |
| 3 | **B3** render dirty줄+스타일 캐시 | 높음 | 중 | render_all_ms |
| 4 | **B5** 적응형 flush(이벤트 구동) | 중(입력지연·idle CPU) | 중 | 입력→화면 지연 |
| 5 | **B4** 메시지 배치+drain 1회 | 중(다중 패널/클라) | 낮음 | flush 지연 |
| 6 | **A1** textual import 다이어트 | 중(cold start) | 낮음 | cold_import_ms |
| 7 | **A3/A2** first-paint·백오프 | 작음(체감) | 낮음 | first-paint |
| 8 | **B6** 직렬화 포맷 | B2 후 잔여 | 중(의존성) | json/frame |
| 9 | **B7** feed 천장(스레드) | 환경의존 | 높음 | feed MB/s |

**권장 1차 묶음**: B1 → B3 → B2 순으로(스캔 절감 → render 절감 → 델타 전송). 각
단계마다 `bench.py` 전후 비교 + `tests/run.py` 통과를 게이트로 작은 CL 로 제출한다
(서버 측 변경이라 적용엔 `python pytmux.py cmd restart-server` 필요 — HANDOFF §2).

## 7. 비목표 / 주의

- **동작·렌더 결과는 불변**이어야 한다(델타·캐시는 full 재전송과 셀 단위 동일).
- 공유 워크스페이스 충돌 주의(HANDOFF §9/§11) — Windows 포팅 등 동시 세션과 같은
  파일 동시 편집 회피, 작은 CL 로 점진 이전.
- 서버/클라 구조 변경은 재시작 필요(HANDOFF §2). 작업 보존 재시작은
  [RESTART_SCENARIO.md](RESTART_SCENARIO.md) 의 `restart-server` 사용.
- 인터랙티브 ssh 반응성의 최종 확인은 실 Windows 박스(WINDOWS_TESTING.md §3-c) —
  헤드리스 `bench.py` 는 대리 지표다.
