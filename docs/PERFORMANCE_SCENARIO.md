# pytmux 성능/반응성 가속 — 동작 시나리오

> **상태**: 🟢 핵심 구현 + 추가 리서치. ReDoS·B1·B2·B3·B4·A2·A3 구현 완료(§3~§6 ✅),
> 실행속도·반응성의 남은 레버는 §8(추가 리서치, 2026-06-06 실측)에 A4/A5/B8/B9 로 정리.
> 본 문서는 "더 빨리 실행되고 더 빨리 응답하게" 만들기 위한 **근거 기반 최적화 시나리오**
> 다. 각 항목은 코드 근거(`file:line`)·개선안·예상 효과·위험·검증 게이트를 갖는다.
> 후속 리뷰(2026-06-07, 신규 레버 C1~C5 미구현)는 [PERFORMANCE_REVIEW_2026-06-07.md](PERFORMANCE_REVIEW_2026-06-07.md).
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

### B1. `_scan_claude` dirty 게이팅 + 저주기화 ★최우선·저위험 — ✅ 구현(CL 56802)

> **구현 완료(dirty 게이팅, CL 56802)**: 패널별 `_feed_seq`(feed 마다 증가)/`_scan_seq`
> (마지막 스캔 seq)로 **출력이 없으면 join+정규식 스캔을 통째로 건너뛴다**. 단 프레임
> 카운터 디바운스(완료 알림 #22의 `_DONE_IDLE_FRAMES`, 헤더 예약 해제 `_HDR_CLAUDE_MISS`)
> 는 출력 없는 프레임에도 진행돼야 하므로 **전이 중(pending)인 패널은 계속 스캔**하고
> settled 패널만 건너뛴다. auto-mode/perm 토글 시엔 `_scan_seq=-1` 로 1회 재스캔 강제.
> 효과: idle 다중 패널(12탭×… )에서 `_scan_claude` 가 사실상 무비용(<0.01ms). **저주기화
> 는 디바운스 상수 재환산 위험 대비 이득이 작아 보류**(게이팅으로 충분).
>
> **부수 발견·해결(CL 56801)**: 성능 측정 중 `claude_usage` 가 와이드·대부분 공백
> 화면서 **~418ms ReDoS**(`_CTX_BADGE_RE` 선행 `\s*` 백트래킹)인 것을 발견·수정
> (420ms→0.08ms). 게이팅과 별개로 **변경된 패널 스캔마다** 도는 핫패스라 영향이 컸다.

원안(설계):

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

### B2. 화면 **행 단위 델타** 전송 ★고효과 — ✅ 구현(CL 56852)

> **구현(per-client 델타)**: 클라마다 `ClientConn._sent_rows[pane]`(직전 전송 rows)를
> 두고, flush 가 패널을 1회 render 한 뒤 클라별로 직전 대비 **바뀐 행만** `screen-delta`
> `{pane, rows:[[y,segs]...], cursor}` 로 보낸다(변경 행이 70% 초과·행수 변동·최초면
> full `screen` 폴백). 클라는 캐시된 rows 에 행 단위로 적용. **클라별 자기 상태 기준**
> 이라 base seq/resync 없이 다중 클라·신규 attach 정합(_send_full 이 _sent_rows 를
> 비우고 full 로 재시드). 효과: Claude busy 스피너 1줄 변경 시 프레임 4659B→164B(28×).
> 회귀 test_screen_delta_frame_and_equivalence(델타 적용==full 골든·임계 폴백).

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

### B3. `render()` 셀 직렬화 비용 절감 (스타일 키 캐시 ✅ / dirty 줄 재직렬화 ✅ #8)

> **구현(스타일 키 캐시, CL 56805)**: 셀마다 dict 생성 + `tuple(sorted(...))` 하던 것을
> 모듈 `@lru_cache _style_key(fg,bg,bold,…)` 로 메모이즈. 셀 1만개 스타일키 2.12ms→1.08ms
> (≈render 비용의 1/3 구간을 절반으로 → render 약 25–33% 단축) + 셀당 dict/tuple 할당
> 제거(GC 부담↓, 출력 폭증 시 유리).
>
> **구현(dirty 줄 재직렬화, #8)**: `render()` 가 라이브 뷰(scroll 0)·검색 비활성·캐시
> 유효(같은 화면 객체·크기)면 pyte `screen.dirty` 가 표시한 행만 다시 직렬화하고 나머지
> 행은 직전 캐시(`Pane._row_cache`)를 재사용한다. 스크롤/검색/리사이즈/alt전환/콜드캐시는
> 전체 경로로 폴백. **측정: 80×24에서 1행만 바뀔 때 render 376µs→21µs(18.3×).** render 가
> 패널당 flush당 1회뿐이라 클라별 델타(B2 `_sent_rows`)와 충돌하지 않는다(패널 단위 캐시).
> 정확성: pyte 0.8.2 의 draw/erase/scroll/insert·delete(lines·chars)가 모두 dirty 를
> 표기함을 확인하고, **빠른 경로==전체 경로 등가성**을 11개 연산×2프레임+무효화 경로로
> 못박았다(`tests/test_model.py::test_render_dirty_path_matches_full` 외). 회귀 게이트:
> ptyshot 골든(`test_real_client_delta_render` end-to-end 마커 렌더).


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

### B4. flush 메시지 배치 + `drain()` 1회 — ✅ 구현(CL 56806)

> **구현**: `protocol.frame_msg(obj)`(프레이밍만)·`write_frames(writer, frames)`(일괄
> write+drain 1회) 추가. `_flush_loop` 이 한 프레임의 screen(+status) 들을 frame bytes
> 로 모아 클라마다 한 번에 write+drain → 패널×클라 곱만큼의 await/drain 왕복 제거.
> read_msg 는 길이프리픽스라 연결된 프레임을 그대로 순차 파싱(클라 변경 불필요).
> (`_send_full` 은 first-paint 위해 배치 대신 A3 의 active-first 증분 송신과 함께 다룸.)


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

### A2. `wait_server` 초기 백오프 단축 — ✅ 구현(CL 56808)

**현상/근거**: `wait_server` 는 20ms 고정 간격 폴(`launcher.py:27-33`). 첫 probe 는
즉시 하지만, 서버가 막 떠 첫 probe 가 실패하면 다음 시도까지 20ms 고정 대기.

**개선안**: 초기 몇 회는 더 촘촘히(예 2→5→10→20ms 지수 백오프)로 서버가 빨리
뜬 경우의 체감 지연을 수십 ms 단축. 상한(총 4s)은 유지.

**검증**: 효과가 작을 수 있음 — `bench` 보다 실 attach 반복 측정으로 판단.

### A3. 첫 프레임: 활성 패널 우선 전송 — ✅ 구현(CL 56808)

**현상/근거**: `_send_full` 은 layout → **전 패널 순차 render/전송** → status 순
(`server.py:2368-2390`). 분할이 많으면 활성 패널 첫 그림이 비활성 패널 직렬화 뒤로
밀린다.

**개선안**: 활성 패널을 **가장 먼저** render/전송해 사용자가 보는 화면이 먼저 뜨게
하고 나머지는 이어서. (체감 first-paint 단축, 총량 동일.)

**검증**: 다중 패널 attach 시 활성 패널 first-paint 시각(ptyshot).

## 6. 우선순위 로드맵

효과×안전(낮은 위험·높은 효과 우선):

| 순위 | 시나리오 | 효과 | 위험 | 상태 |
|---|---|---|---|---|
| — | **ReDoS** claude_usage `_CTX_BADGE_RE` | 높음(핫패스) | 낮음 | ✅ CL 56801 (420ms→0.08ms) |
| 1 | **B1** scan_claude dirty 게이팅 | 높음(idle/다중패널) | 낮음 | ✅ CL 56802 (idle scan 무비용) |
| 3 | **B3** render 스타일 키 캐시 | 중(render ~25-33%↓) | 낮음 | ✅ CL 56805 |
| 5 | **B4** 메시지 배치+drain 1회 | 중(다중 패널/클라) | 낮음 | ✅ CL 56806 |
| 7 | **A3/A2** first-paint·백오프 | 작음(체감) | 낮음 | ✅ CL 56808 |
| 2 | **B2** 행 단위 델타 전송 | 매우 높음(alt 풀리페인트·ssh) | 중~높 | ✅ CL 56852 (스피너 28×↓) |
| 4 | **B5** 적응형 flush(이벤트 구동) | 중(입력지연) | 중 | ⏳ 보류(아래) |
| 6 | **A1** textual import 다이어트 | 중(cold start) | 낮음 | ⏳ 미착수 |
| 8 | **B6** 직렬화 포맷(orjson) | B2 후 잔여 | 중(의존성) | ⏳ B2 후 측정 |
| 9 | **B7** 비가시 패널 feed 스로틀 | 낮음(동시 폭증만) | 중 | ⏳ 저효과 보류 |

**완료(2026-06-06)**: ReDoS·B1·B3·B4·A2·A3 + 측정 중 발견한 claude_usage ReDoS. idle/
다중패널 CPU·flush 왕복·render 직렬화·first-paint 를 개선했다(235 passed, 매 단계 동작 불변).

**남은 항목 — 권고**:
- ~~B2(행 단위 델타)~~ → ✅ **CL 56852 완료**(per-client 델타, 스피너 28×↓).
- **B5(이벤트 구동 flush)** — 입력→화면 지연(≤33ms) 단축이 목표지만 **코어 flush 루프**
  에 `asyncio.Event` 웨이크를 넣는 변경이라 기아/중복웨이크/누락 회귀 위험이 있다.
  B1(스캔 게이팅)+B4(배치)로 idle CPU 는 이미 낮아져 잔여 이득이 작아 보류.
- **B7** — 단일 패널 폭증엔 무효(동시 다중 폭증만 도움) → 실효 낮아 보류.
- **A1** — textual import 지연/트리셰이킹은 무위험·소이득, 여력 시 착수(§8 실측 참고).
- **신규 레버 A4/A5/B8/B9 는 §8(추가 리서치) 참조** — 실측 import 분해로 도출.

## 7. 비목표 / 주의

- **동작·렌더 결과는 불변**이어야 한다(델타·캐시는 full 재전송과 셀 단위 동일).
- 공유 워크스페이스 충돌 주의(HANDOFF §9/§11) — Windows 포팅 등 동시 세션과 같은
  파일 동시 편집 회피, 작은 CL 로 점진 이전.
- 서버/클라 구조 변경은 재시작 필요(HANDOFF §2). 작업 보존 재시작은
  [RESTART_SCENARIO.md](RESTART_SCENARIO.md) 의 `restart-server` 사용.
- 인터랙티브 ssh 반응성의 최종 확인은 실 Windows 박스(WINDOWS_TESTING.md §3-c) —
  헤드리스 `bench.py` 는 대리 지표다.

## 8. 추가 리서치 (2026-06-06) — 실측 import 분해 + 신규 레버

> B1·B2·B3·B4·A2·A3·ReDoS 완료 후 **실행속도·반응성의 남은 레버**를 `python -X
> importtime` 과 마이크로 측정으로 다시 도출했다. 아래는 근거(실측)·개선안·효과·위험.

### 8.1 실측 — cold import 분해 (`python -X importtime`)

| 진입 | 총 import | 지배 요인 |
|------|----:|------|
| `import pytmux`(서버/제어 경로, textual 미로드) | **~52ms** | asyncio ~22(ipc 경유) · model→pyte ~12 · wcwidth ~9.5 |
| `import pytmuxlib.client`(attach 경로, textual) | **~136ms** | textual ~60(clientscreens→textual.screen→css 31) · asyncio ~20 · rich ~8 |

- 마이크로 측정: `ipc+protocol` 만 = 26.6ms, 여기에 `server(→model→pyte)` = +9.9ms.
- 결론: **attach(common) 의 startup 은 textual(~60ms) 이 지배**(회피 불가, 정리 여지
  제한적). 경량 제어 명령(ls/cmd/kill)은 server/model/pyte(~10ms) 를 불필요 지불.

### 8.2 A4. 경량 제어 명령(ls/cmd/kill) 지연 import ★저위험·소이득

**현상/근거**: `launcher.py:16 from .server import run_server`·`pytmux.py:47,53` 이
**server/model/pyte/wcwidth 를 모듈 로드 시 즉시** 끌어온다(~10ms). `ls`/`cmd`/`kill`
은 IPC 만 쓰고 model/pyte 가 필요 없다(서버 데몬만 필요). **개선**: textual 처럼
server/model/pyte/replay 도 `pytmux.py` `__getattr__`(PEP 562)·`run_server` 지연
import 로 돌려, 경량 명령 경로가 pyte/wcwidth 를 안 사게 한다. **효과**: ls/cmd/kill
cold ~52ms→~42ms. **위험**: 낮음(심볼 재노출 동일, 서버/attach 경로는 그대로 로드).
**검증**: `python -X importtime pytmux.py ls` 전후, 기존 테스트 import 호환.

### 8.3 A5. 배포 시 `.pyc` 사전컴파일 ★무위험

**현상/근거**: `install.sh` 에 `compileall` 이 없어 설치 후 **첫 실행이 .pyc 를 컴파일**
하며 cold import 가 더 느리다(importtime 첫 실행에 컴파일 포함). **개선**: `install.sh`
가 `python -m compileall pytmuxlib` 를 돌려 배포 시 바이트코드를 미리 만든다(또는
`PYTHONPYCACHEPREFIX` 안내). **효과**: 설치 직후 첫 attach cold import 단축(환경따라
수~수십 ms). **위험**: 없음(런타임 동작 불변, 패키징만). **검증**: 설치 후 첫 실행
cold_import_ms.

### 8.4 A1(보강). textual import — 현실적 기대치

**근거**: 클라 import 의 ~60ms 는 textual 코어(css 엔진 31ms·app 19ms·rich)다 —
**TUI 인 이상 회피 불가**. 트리셰이킹은 안 쓰는 textual 위젯/심볼 import 제거로 한정적
(수~십 ms). clientscreens(모달군) 지연 import 는 textual.screen 을 client 가 어차피
로드하므로 marginal. **결론**: A1 은 "큰 한 방"이 아니라 A4/A5 와 함께 수십 ms 를
긁는 수준 — 기대치를 낮게 잡고 측정 후 착수.

### 8.5 B8. 클라이언트 **증분 합성**(dirty-region `_composite`) ★반응성

**현상/근거**: 클라 `_composite`(`client.py:1054`)는 매 갱신마다 **전 화면 W×H 셀을
재합성**한다(패널 blit·테두리·오버레이·dim 다중 순회). B2 로 서버→클라 전송은 줄었지만
**1줄 델타에도 클라가 화면 전체를 재합성**한다 — 출력 폭증·다중 패널서 클라측 핫패스.
**개선**: 변경된 패널/행만 재합성하는 dirty-region 합성(서버 B2/B3 의 클라측 대응).
B2 가 보낸 변경 행 집합을 합성 단위로 재사용. **효과**: 클라 CPU·입력→화면 지연 감소
(특히 저사양 클라·원격). **위험**: 중(합성은 테두리·오버레이·헤더가 셀을 공유해 부분
갱신 정합이 까다로움 — 골든 비교 필수). **검증**: `tests/ptyshot` 시각 회귀 + 합성
호출당 처리 셀 수 측정.

### 8.6 B9. 클라 **합성 코얼레싱**(read-burst 당 1회) ★반응성·저위험·고효과

**현상/근거**: 클라 `_reader_task`(`client.py:352`)가 메시지 1개마다 핸들러에서
`self._composite()` 를 부른다(`client.py` screen/screen-delta/status 분기). B4(배치
송신)·B2(델타) 이후 **한 flush 가 여러 메시지를 한 번에** 보내므로, 클라가 한 번에
받은 N 메시지에 **_composite 를 N 회** 돌린다(같은 프레임을 여러 번 재합성). **개선**:
메시지 처리에서 즉시 합성하지 말고 `self._needs_composite=True` 만 세운 뒤, **이벤트
루프 틱마다 1회**(`call_soon`/한 read 버스트 후) 합성해 코얼레싱. **효과**: 출력 폭증·
다중 패널 flush 에서 클라 합성 횟수를 프레임당 1회로 — 클라 CPU·지터 대폭 감소(B8
없이도 즉효). **위험**: 낮음(합성을 한 틱 미루는 것뿐, 시각 결과 동일). **검증**: 한
배치 수신 시 _composite 호출이 1회인지 + ptyshot 시각 회귀.

### 8.6b B10·B11. 후속 마이크로 레버 — ✅ 구현(2026-06-06, IMPROVEMENT §4 잔여)

- **B10. feed ESC-없는 빠른 경로**(IMPROVEMENT §4.4, CL 57052) — `Pane.feed` 에서
  `buf` 에 ESC(0x1b)가 전혀 없으면 전처리 정규식 4개(`_CSI_PARTIAL_RE`/`_PRIVATE_
  SGR_RE`/`_sanitize_sgr`/`_ALT_RE`)를 건너뛰고 현재 화면에 직행. 빌드 로그·cat 등
  플레인 텍스트 버스트의 무의미한 정규식 스캔 제거(동작 불변). **주의**: §4.4 원
  리뷰의 "8KB 슬라이스마다 8× 반복"은 과대평가 — 각 슬라이스는 자기 8KB 만
  전처리하므로 총량은 1패스에 가깝다(B7 의 "전처리 ~0.2s 무시 수준"과 정합). 실효
  이득은 ESC-없는 경우의 단축이다. 회귀: `test_feed_plain_text_fast_path`(alt 라우팅·
  `_altcarry` 경계 포함).
- **B11. status prompt-history 변경 시에만 전송**(IMPROVEMENT §4.5, CL 57051) —
  prompt_history 는 드물게 바뀌는데 매 status(토큰 변동으로 자주 발화)마다 패널별
  30개 프롬프트를 재직렬화·전송했다. `_pane_claude_entry(p, full)` 가 full(신규
  attach·구조 resync)이면 항상, 주기 flush 면 직전 전송분(`pane._hist_sent`)과 다를
  때만 싣는다. ssh 트래픽·직렬화 비용 절감. 클라 `_update_claude` 가 빠진 history 를
  유지. 회귀: `test_status_history_debounce`(서버) + `test_claude_history_retained_
  when_omitted`(클라).

### 8.7 갱신된 우선순위(신규 레버)

| 순위 | 레버 | 효과 | 위험 | 상태 |
|---|---|---|---|---|
| 1 | **B9** 클라 합성 코얼레싱 | 높음(폭증/다중패널 클라 CPU) | 낮음 | ✅ 완료 |
| 2 | **A4** 경량명령 지연 import | 낮음(ls/cmd/kill ~10ms) | 낮음 | ✅ 완료 |
| 3 | **A5** .pyc 사전컴파일 | 낮음(첫 실행) | 없음 | ✅ 완료 |
| 4 | **B8** 클라 증분 합성 | 중~높(클라 핫패스) | 중 | ✅ 완료(행 단위) |
| 5 | **A1** textual import 다이어트 | 낮음(현실적) | 낮음 | ☑ 측정·종결 |

> **권장 다음 착수**: ~~B9 → A4/A5 → B8~~ — **전부 완료(2026-06-06)**. 각각
> `tests/run.py` 통과 + (B8) ptyshot 델타 회귀를 게이트로 했다.

**구현 요약(2026-06-06)**:
- **B9**(`a44e16e`): `_request_composite()` — read-burst 당 `_composite` 1회.
- **A4**(`681af9c`): launcher 의 `run_server`·pytmux 의 model/replay/server 재노출을
  지연 import → ls/cmd/kill 이 pyte/wcwidth/model/server 를 안 사게(검증: importtime
  0줄). 회귀 `test_control_path_skips_heavy_imports`.
- **A5**(`a31646e`): install.sh/install.ps1 에 `compileall` 추가 — 설치 시 .pyc 선생성.
- **B8**(`998423c`): `set_frame` 이 직전 프레임과 **행 단위 정확 비교 → 변경 행만
  `refresh(Region)`**. `_composite` 는 전 화면 재구성 유지(오버레이 정합) — 증분 합성이
  아니라 증분 **렌더**다(textual 이 깨끗한 행 render_line 을 건너뜀). 회귀:
  `test_set_frame_dirty_row_refresh` + `test_real_client_delta_render`(PTY end-to-end).
- **A1**(측정·종결): 클라 import ~99ms 중 **textual 코어 ~57ms(css.model 44ms 지배)는
  불가피**. clientscreens(고유 ~14ms) 지연은 build_client_app 이 attach 시 즉시 호출돼
  net-zero(팝업 시점까지 미루려면 27곳 변경·중위험에 spawn 경로선 서버부팅과 겹쳐
  대부분 숨겨짐 → 보류). 무거운 미사용 모듈 import 없음. **유의미한 import-time 레버
  없음** — client.py 의 죽은 import 9개만 정리(`998423c` 후속). §8.4 예측대로.

## 9. 측정 결과 — 개선 전/후 (2026-06-06, darwin-arm64)

> CPython 3.12.10 · textual 8.2.x. ①은 같은 머신·같은 시점에 **before 커밋을 git
> worktree 로 직접 띄워** 측정한 통제 A/B(변인 최소) — 가장 단단한 값. ②는 커밋된
> 헤드리스 벤치(`scripts/bench.py`) 06-05 기준 → 현재 HEAD 로, 여러 커밋·warm 상태가
> 섞인 추세값. ③은 직전 스프린트 문서값(이번에 재측정 아님).

### 9.1 통제 A/B (worktree before vs HEAD)

| 지표 | BEFORE | AFTER | 차이 | 레버 |
|---|---|---|---|---|
| `import pytmux` cold (프로세스 내) | 33.3 ms | **23.2 ms** | −30% | A4 |
| `_composite` 호출 /6메시지 버스트 | 6회 | **1회** | 6×↓ | B9 |
| `render_line` 실행 행 /1행 델타 | 28행 | **2행** | 14×↓ | B8 |

- A4: BEFORE 는 `import pytmux` 가 pyte 를 로드, AFTER 는 미로드(검증). model/replay/
  server 지연화로 ls/cmd/kill·attach 공통 −10ms.
- B9(before=3d498d9 pre-B9): 한 read 버스트의 N 메시지를 합성 1회로 코얼레싱(전 화면
  W×H 재구성 6→1회). B8(before=a44e16e, B9 이미 포함): 1행 델타에 변경 행만 재렌더.

### 9.2 헤드리스 벤치 (06-05 → HEAD)

| 축 | 06-05 | HEAD | 차이 |
|---|---|---|---|
| cold import p50 | 63.3 ms | **23.3 ms** | −63% (A4 + A5 .pyc + warm) |
| 전 패널 render+직렬화 p50 | 2.78 ms | **1.89 ms** | −32% |
| feed 처리량 claude_busy | 1.16 MB/s | **2.11 MB/s** | **1.8×** |
| feed slice max claude_busy | 24.3 ms | **9.98 ms** | −59% (60fps 안정권) |
| feed 처리량 plain_cat | 0.99 MB/s | **1.83 MB/s** | 1.8× |

> 벤치 cold import 63→23ms 는 A4(−10ms)에 A5(.pyc 사전컴파일)·warm-up 이 더해진 값 —
> 순수 A4 기여는 §9.1 의 −10ms. feed/render 개선은 B2(행 델타)·B4(배치) 등 누적분 반영.

### 9.3 직전 스프린트 문서값

- **B2**: per-client 행 델타 전송 → Claude busy/ssh 트래픽 **28×↓**.
- **A3**: 입력 스파이크 **82ms → 4.5ms**.

**요약**: 기동 `import pytmux` 33→23ms(−30%); 출력 폭증 반응성 feed **1.8×**·slice 최대
24→10ms(60fps 안정권); 클라 핫패스 1행 델타당 합성 6→1·행 렌더 28→2 — 저사양·원격
클라 CPU/지터 대폭 감소.
