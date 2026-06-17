# docs/benchmark/ — OS별 성능/반응성 벤치마크 히스토리

> 관련: [../WINDOWS_TESTING.md](../WINDOWS_TESTING.md) · [../HANDOFF.md](../HANDOFF.md) ·
> 생성기 `scripts/bench.py` · 워크플로 `.github/workflows/benchmark.yml`

이 디렉터리는 `scripts/bench.py` 가 측정한 결과를 **OS별·시각별 파일**로 쌓는다.

```
docs/benchmark/
  <os-slug>/                 # 예: linux-x86_64, darwin-arm64, windows-amd64
    20260605-061700Z.md      # 사람이 읽는 리포트
    20260605-061700Z.json    # 비교/그래프용 원자료
```

## 측정 3축 (헤드리스 — 실 터미널/셸 불필요)

1. **초기 실행시간(startup)** — 별도 프로세스 `import pytmux`(cold) + 프레임워크
   init(Server 생성→기본 세션→첫 layout, 셸 spawn 제외).
2. **다중 탭/패널 반응성** — 탭 N개·활성 윈도우 패널 M개 상태에서 클라로 가는
   layout 메시지 빌드·전 패널 render+직렬화·탭 전환 지연(p50/p99/max ms) + 패널 수별
   스케일링.
3. **터미널 출력 폭증** — 처리량(feed MB/s)과 반응성(슬라이스 지연 p50/p99/max).
   claude busy 풀리페인트 / plain cat 스크롤 합성 워크로드(`scripts/poc/feed_profile.py` 재사용).

## 동기화 모델 (왜 Perforce 가 아니라 git 인가)

벤치마크는 **CI(GitHub Actions)가 ubuntu/macos/windows 러너에서** 생성한다 — 개발
환경(Apple Silicon macOS)에서는 Linux/Windows 수치를 낼 수 없기 때문이다. CI 는
Perforce depot 에 쓸 수 없고 git 미러에만 커밋할 수 있으므로, 이 히스토리는
**git-우선**이다:

- `.github/workflows/benchmark.yml` 의 `publish` 잡이 세 OS 결과를 모아 미러 `main` 에
  커밋(`[skip ci]` 로 자기 재트리거 방지).
- 개발환경에서는 **`git pull`** 로 받아 본다.
- **`.p4ignore` 에 `docs/benchmark/` 가 있어** Perforce 는 이 파일들을 추적하지 않는다
  (워크스페이스에 git 으로 받아도 depot 은 깨끗하게 유지). 생성기 소스(`scripts/bench.py`,
  워크플로, 이 README)는 `docs/benchmark/` 밖이거나 별도라 Perforce 로 정상 관리된다.

> **주의(미러 재생성)**: git 미러가 depot 기준으로 강제 재빌드되면 git-only 인 벤치마크
> 히스토리는 사라질 수 있다(벤치마크는 언제든 재생성 가능하므로 보통 문제 없음).
> 특정 결과를 영구 보존하려면 그 파일만 따로 depot 에 `p4 add` 하면 된다.

## 로컬 실행

```sh
python scripts/bench.py            # docs/benchmark/<os-slug>/<ts>.md|json 작성
python scripts/bench.py --stdout   # 파일 대신 표준출력
python scripts/bench.py --quick    # 짧게(스모크)
```
