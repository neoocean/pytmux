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
python3 qa/run.py                          # 전 시나리오 (지금은 T0 하나)
python3 qa/run.py --list                   # 무엇이 등록돼 있나
python3 qa/run.py --scenario T0-core-loop
python3 qa/run.py --seed 1234              # 결정론 — 리포트에 남는다
python3 qa/run.py --keep                   # 격리 슬롯을 안 지운다(사후 조사)
```

산출물은 `qa/out/<runId>/` 에 `findings.json`(기계용) 과 `REPORT.md`(사람용) 둘이다.
**p4·git 양쪽에서 제외**된다 — 결함의 정본은 리포트가 아니라 트래커의 이슈다.

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
python3 qa/run.py                      # 그리고 실제로 한 바퀴 돌린다
```

`tests/test_qa_layer.py` 는 전체 스위트에 들어 있으므로 `scripts/check_all.py` 가
같이 돈다. ⛔ **`qa/run.py` 자체는 커밋 게이트에 안 넣었다** — 데몬을 띄우고 실 PTY 를
6초씩 잡는 일이라 게이트에 넣으면 사람이 게이트를 끄게 된다(그 이유는 설계 SSOT §6).
