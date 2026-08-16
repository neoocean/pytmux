# `qa/` — pytmux 자율 QA 체계 (쓰는 법)

**설계 SSOT 는 이 파일이 아니다.** 이 저장소는 이슈 트래커의 **M2** 프로젝트라 산문의
원본이 트래커에 있다 — 노선·결정·티어·후속 계획 전문은 거기서 읽는다.

```sh
node ../issue/bin/issue.mjs doc-get pytmux/qa-system      # 설계 SSOT (전문)
node ../issue/bin/issue.mjs doc-history pytmux/qa-system  # 판 이력
```

여기 있는 것은 **손으로 치는 명령**뿐이다.

## 돌린다

```sh
python3 qa/run.py                          # 전 시나리오 (T0 · T1 · T2 · T3 · 실측 약 190초)
python3 qa/run.py --list                   # 무엇이 등록돼 있나
python3 qa/run.py --scenario T0-core-loop
python3 qa/run.py --tier T1                # 명령 표 전수 + 커버리지 원장
python3 qa/run.py --tier T2                # 다중 클라 동시 접속 (실측 약 6초)
python3 qa/run.py --tier T3                # 실 GUI 창 프레임 (실측 약 40초 · 아래 §T3)
python3 qa/run.py --seed 1234              # 결정론 — 리포트에 남는다
python3 qa/run.py --keep                   # 격리 슬롯을 안 지운다(사후 조사)
```

산출물은 `qa/out/<runId>/` 에 `findings.json`(기계용) 과 `REPORT.md`(사람용) 둘이다.
**p4·git 양쪽에서 제외**된다 — 결함의 정본은 리포트가 아니라 트래커의 이슈다.

## T2 — 다중 클라 동시 접속 (⛔ 클라가 하나면 이 제품이 아니다)

pytmux 는 **단일 서버 · 다중 클라**다. T0·T1 은 클라를 한 번에 하나만 띄우므로
(`Session.capture_client` 는 한 프로세스를 상한까지 붙들고 있다 돌려준다) 그 전까지는
**브로드캐스트가 통째로 한 클라에만 가도 QA 는 초록**이었다. T2 는 실 Textual 클라
**세 개를 동시에** 같은 서버에 붙여(`ptyshot.Multi`) 넷을 잰다(스텝은 여섯이다):

| 스텝 | 무엇을 | 오라클 |
| --- | --- | --- |
| `attach` | 셋이 같은 시각에 붙어 **탭바를** 그리나 | `client/alive`·`client/no_traceback`·`client/renders_tree`·`multi/attach_together` |
| `broadcast` | 제3자(제어 라인)의 트리 변경이 **전원**에게 가나 | `multi/broadcast` |
| `delta-mirror` | 한 클라에 친 키의 **출력**이 다른 클라 화면에 그려지나(첫 클라·마지막 클라 양쪽에서 친다) | `multi/delta_mirror` |
| `one-dies` | 하나를 SIGKILL 해도 서버·나머지 클라가 살고 **계속 받나** | `multi/survives_one_death`·`multi/survivor_keeps_updating` |
| `detach-all` | 전원이 사라져도 세션은 남나 | `multi/session_outlives_all_clients` |
| `kill-server` | 슬롯에 남는 것이 없나 | `lifecycle/clean_exit` |

⚠ **`tests/test_client_capacity.py` 와 층이 다르다** — 그쪽은 가짜 writer 로 `clients`
리스트를 채워 `_flush_to_client` 를 직접 부르는 헤드리스 단언이고(용량·느린 소비자·선형
비용), 이쪽은 **진짜 클라 프로세스가 실제로 그린 화면**이다. 저쪽이 초록인 채로 이쪽이
붉을 수 있다.

⛔ **판정 재료는 탭바이지 테두리가 아니다.** 실측(2026-08-09 뮤테이션): 서버 프레임을
하나도 못 받은 클라도 자기 껍데기(테두리)는 그린다 — 「떴다」를 「붙었다」로 읽으면 이
티어가 통째로 장식이 된다.

⛔ **Windows 에서는 통째로 미검증이다**(`ptyshot` 이 POSIX 전용). 그때 조용히 빠지지 않고
**스텝마다 사유를 단 SKIP** 을 낸다 — 그 구멍은 `pytmux/pytmux-152` 가 이름 대서 적어 뒀다.

## T3 — 실 GUI 창 (⛔ 두 번째 사각지대 · `pytmux/pytmux-147`)

"실브라우저"에 해당하는 것이 우리에겐 둘인데(실 PTY · **실 GUI 창**) T0~T2 가 잡는 것은
첫째뿐이다. `client/CLAUDE.md` 가 스스로 적어 둔 *"GUI 쪽 배선 누락은 **라이브
스크린샷만이** 잡는다"* 를 사람 손에서 떼어 내는 것이 T3 이다.

**먼저 이진을 굽는다** — 없거나 소스보다 낡았으면 T3 은 **사유를 달고 통째로 SKIP** 한다
(⛔ 낡은 이진으로 재면 이미 고친 결함을 다시 신고하고 안 고친 것에 초록을 판다):

```sh
(cd client && cargo build -p gui --bin pytmux-gui)   # ⚠ 패키지 이름은 gui, 이진 이름이 pytmux-gui
PYTMUX_GUI=/어디/pytmux-gui python3 qa/run.py --tier T3   # 다른 이진을 쓰려면
```

| 스텝 | 무엇을 | 오라클 |
| --- | --- | --- |
| `first-attach` | 갓 뜬 세션에 **처음** 붙은 창 — ⛔ 이 한 장을 「예열」이라며 버리지 않는다(사용자는 예열을 안 한다) | `gui/frame_dumped`·`gui/no_alarm_banner`·`gui/draws_something` |
| `reattach` | 같은 세션에 두 번 붙으면 **같은 그림**이 나오나 | + `gui/attach_is_stable` |
| `layout-mirror` | 제3자가 탭·패널을 늘리면 **탭바가 따라오나** | + `gui/tree_reaches_window` |
| `key-wiring` | 창에 넣은 키가 화면에 닿나(팔레트가 열리나) | + `gui/keys_reach_window` |
| `mouse` | — | **명시 SKIP**(아래) |
| `kill-server` | 슬롯에 남는 것이 없나 | `lifecycle/clean_exit` |

⛔ **`client/scripts/*.ps1` 를 부르는 층이 아니다.** 그 여덟 개는 ⑴ 화면을 찍고
(`PrintWindow` — 까만 사각형을 성공으로 돌려주는 함정을 제품이 이미 알고 있다) ⑵
PowerShell 이라 Windows 밖에서 통째로 못 돈다. 대신 제품이 자기 드로어블에서 뜨는
`pytmux-gui --frame-dump`(+`--frame-keys`)를 쓴다 — `cfg` 갈림이 없어 **세 OS 에서 같은
코드**라, 「Windows 상자에서 판정한다」를 같은 경로로 만족한다.

⛔ **픽셀에서 글자를 읽지 않는다**(OCR 없음 — 의존성 0). 판정 재료는 글자를 안 읽고도
뜻이 서는 넷이다(`qa/frames.py`): 경보색 픽셀 · 최빈색 비율 · 띠 안의 잉크 · 두 프레임의
차이. 문턱은 전부 실측에서 왔고 상수 옆에 그 값이 적혀 있다.

⚠ **남는 구멍은 마우스다** — 키는 `--frame-keys` 로 넣지만 클릭·휠·드래그를 넣는 길이
맥·리눅스에 없다. 조용히 덮지 않고 **사유 붙은 SKIP**(rc 3 = 미검증)으로 회계한다.

★ 프레임은 `qa/out/<runId>/frames/*.png` 에 남는다 — 결함 본문이 그 경로를 가리킨다.

## 커버리지 원장 — ⛔ 미커버는 통과가 아니다

T0 이 만지는 명령은 둘(`split-window -h`·`new-window`)뿐이라, 그 전까지는 **명령 하나가
통째로 죽어도 QA 는 초록**이었다. `T1-commands` 가 도는 런은 저장소 텍스트에서 명령
**목록만** 뽑아(`qa/inventory.py`) 그 대비 실제로 지난 것을 세고, 리포트에 숫자로 낸다.

| 표면 | 어디서 뽑나 | 누가 지나나 |
| --- | --- | --- |
| 제어 라인 명령 | `pytmuxlib/server.py` `handle_control` + `_ONOFF_CONTROLS` | **T1** (지금 100%) |
| 서버 cmd 표 | `pytmuxlib/servercmd.py` `_CMD_TABLE` | 아직 없다 — 실 클라 `cmd` 프레임이 필요하다 |
| 파이썬 클라 명령 프롬프트 | `pytmuxlib/clientutil.py` `COMMANDS` | 아직 없다 — 실 클라 키 입력(T4) |
| 러스트 GUI 팔레트 | `client/crates/base/src/keymap.rs` `PALETTE` | 아직 없다 — ⛔ **T3 이 생겼어도 그대로다**: T3 은 팔레트를 «열어 보고» 멈추지 이름으로 하나씩 실행하지 않는다(한 번에 창 하나 · 프레임 한 장 약 10.6초) |

회계는 **셋**으로 갈린다(`qa/ledger.py`) — 이 구분이 원장의 전부다.

- **지남** — 보냈고 서버가 거절하지 않았다. ⛔ `unknown: <명령>` 은 지남이 아니다.
- **미커버** — 그 표면을 지나는 시나리오가 **있었는데** 안 지난 명령 → **결함**(S3).
  새 명령이 생긴 날 여기가 저절로 늘어난다. 고치는 길은 그 시나리오를 넓히는 것이다.
- **미검증** — 지나는 시나리오가 **아직 없다** → **건너뜀**(rc 3). ⛔ 결함으로 내지
  않는다(고칠 사람이 없는 이슈가 서고, 그런 이슈가 QA 를 끈다) · ⛔ 통과로도 치지
  않는다(원칙 ⓑ). **그래서 지금 전 런의 rc 는 3 이다** — 위 표의 아랫줄 셋이 비어 있는
  동안은 그것이 정직한 값이다.

⛔ **인벤토리는 손으로 적지 않는다.** 손목록은 새 명령이 생긴 날 조용히 낡고, 그러면
원장이 「전부 지났다」고 거짓말한다. ⛔ 추출이 실패하면 **0건이 아니라 결함**이다 —
0건은 원장에서 「전부 커버」와 같은 모양이다.

## 트래커에 담는다 (⛔ 이걸 안 하면 결함은 어디에도 안 들어간다)

```sh
python3 qa/run.py --ingest                       # 돌고 나서 담는다
python3 qa/run.py --ingest --dry-run             # 무엇이 들어갈지만 본다
python3 qa/run.py --ingest --run-dir qa/out/<runId>   # 이미 구운 런을 담는다
```

M2 에서는 러너가 저장소에 쓰지 않고 트래커의 `sync` 도 저장소를 읽지 않는다. 그 사이를
잇는 것은 `ingest-findings` 하나뿐이다(`pytmux/pytmux-132`). 흡수는 **멱등**이라 같은
런을 두 번 흘려도 이슈가 겹치지 않는다. 트래커 저장소가 형제 자리에 없으면 `ISSUE_REPO`
로 가리킨다.

★ **흡수는 이슈만 남기지 않는다 — 런 한 행도 남는다**(`pytmux/pytmux-148`). 결함이
0건이어도 그 행이 서므로 「어젯밤 QA 가 돌았나」와 「돌았는데 조용했나」가 갈린다.
**미검증**(건너뛴 검사)도 그 행에 결함 수 옆에 앉는다 — `findings.json` 의 `skipped`
배열이 그것을 실어 나른다. ⛔ 미검증은 이슈가 되지 않는다(지문이 없다).

```sh
node ../issue/bin/issue.mjs run-list --project pytmux    # 회차 목록
node ../issue/bin/issue.mjs run-get pytmux/<런 id>       # 결함 · 미검증 전문
```

## 저절로 돈다 (⛔ 구축과 실행은 다른 사건이다)

**매일 04:00** 에 `tools/launchd/org.woojinkim.pytmux.qa.plist` 가 위 명령을 돌린다.
설치·제거·지금 한 회차는 그 파일 머리말에 있다. ⛔ **저장소 파일만 고치면 안 먹는다** —
launchd 가 읽는 것은 `~/Library/LaunchAgents/` 다.

| 무엇이 잘못됐나 | 어디에 보이나 |
| --- | --- |
| 결함이 있다(rc 1) | 트래커의 이슈 — 멱등 병합·재발 판정까지 저쪽이 한다 |
| 미검증이 있다(rc 3) | 그 런의 행 — `run-get` 이 「미검증 N건 — 통과가 아니다」로 낸다. ⚠ 지금은 커버리지 원장의 미도달 표면 셋이 **상시** 여기 앉는다 |
| **아예 안 돌았다**(rc 2 · 러너가 죽었다 · 설치가 안 됐다) | 런이 **안 생긴다** → 트래커 `doctor` 가 「주기 QA 가 밀렸다」를 48시간 뒤부터 한 줄로 말한다 |

⛔ 셋째 줄이 이 배선의 값이다 — 앞의 둘은 「돌았을 때」의 이야기고, **안 돌 때가 가장
조용하다**. 로그(`qa/out/qa.launchd.log`)만 보는 것에 기대지 않는다: 로그를 여는 사람이
없는 것이 `pytmux/pytmux-148` 의 원인이었다.

⚠ **인터프리터는 `python3.13` 이다.** 실측(2026-08-09) 이 머신의 `python3`(3.14)에는
`wcwidth`·`textual` 이 없어서 그 인터프리터로 돌리면 매 런 S1 「QA 스택을 세우지 못한다」
가 난다 — 상주 위양성이고, 위양성이 QA 를 끈다(원칙 ⓓ).

스위트 결과(`tests/run.py`)를 담는 길은 따로 있다 — `python3 scripts/tracker_tests.py --ingest`.
두 유입구는 겹치지 않는다(저쪽은 케이스 단위 테스트 회계, 이쪽은 시나리오·오라클 결함).

## 종료 코드 — ⛔ 초록은 비싸다

| rc | 뜻 |
| --- | --- |
| 0 | 초록 — 결함 0 **이고** 건너뜀 0 |
| 1 | 결함이 있다 |
| 2 | 환경 구성 실패 · 시나리오 0건(**빈 결과는 통과가 아니라 고장이다**) |
| 3 | 결함은 0 인데 **미검증**이 있다(건너뛴 검사) |

## 안전 (읽고 시작할 것)

- 런은 **격리 홈 슬롯**에서만 돈다 — `PYTMUX_HOME=/tmp/pytmux-qa-<uid>/qa-<runId>-<n>`.
  사용자의 라이브 데몬에는 **부착하지 않는다**(opt-in 플래그조차 두지 않았다).
- 정리는 **pid 로만** 한다. `pkill`·`Get-Process` 류는 이 층에 한 줄도 없고
  `tests/test_qa_layer.py` 가 AST 로 그걸 지킨다.
- 정리 판정도 **내 슬롯 안에서만** 한다. 전역 프로세스 목록으로 확인하면 화면에 남는
  것이 사용자의 라이브 데몬이라 "정리 실패"로 오판하고, 그 오판이 일괄 kill 로 확대된다
  (루트 `CLAUDE.md` — 같은 날 3회 재발한 사고).

## 이 층을 고쳤으면

```sh
python3 tests/run.py test_qa_layer     # 메타 QA — 오라클이 무는지 · 안전 규율 · 트래커 계약
python3 tests/run.py test_ptyshot      # 하네스 자체 — `capture` 의 먹임 규약 · `Multi` 의 동시성
python3 qa/run.py                      # 그리고 실제로 한 바퀴 돌린다
```

⛔ **T3 을 고쳤으면 이진부터 굽는다**(`cd client && cargo build -p gui --bin pytmux-gui`).
안 구우면 그 회차는 판정이 아니라 **사유 붙은 SKIP** 이고, 리포트만 보면 초록과 헷갈린다.

`tests/test_qa_layer.py` 는 전체 스위트에 들어 있으므로 `scripts/check_all.py` 가
같이 돈다. ⛔ **`qa/run.py` 자체는 커밋 게이트에 안 넣었다** — 데몬을 띄우고 실 PTY 를
6초씩 잡는 일이라 게이트에 넣으면 사람이 게이트를 끄게 된다(그 이유는 설계 SSOT §6).
