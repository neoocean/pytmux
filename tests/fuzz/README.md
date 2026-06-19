# 퍼징(Tier 2) — 커버리지 가이드 보안 퍼저

정적 검토(`docs/internal/SECURITY_REVIEW.md` §0–§8)와 런타임 적대 테스트(§9 Tier 1,
`tests/test_security_runtime.py`)에 이어, **신뢰불가 바이트를 직접 받는 경계 파서**를
대량 무작위/진화 입력으로 두드린다.

## 두 갈래(같은 타깃 공유)

- **결정론적 baseline** — `tests/test_fuzz_parsers.py`. 신규 의존성 0, 매 CI 실행.
  고정 seed 의 시드+의사난수 코퍼스로 "어떤 바이트에도 예외 없음·출력 한계"를 단언.
- **atheris 커버리지 가이드** — `tests/fuzz/fuzzer.py`. libFuzzer 기반, 설치 필요,
  야간/수동(`.github/workflows/security.yml` 의 `atheris-nightly`, non-blocking).

공유 타깃은 `tests/fuzz_targets.py` 의 `TARGETS`:
`protocol`(와이어 길이프리픽스 JSON) · `ptyhost`(pty-host 멀티플렉싱 프레임) ·
`clamp`(치수 가드) · `vtparse`(패널 VT 파서, N1 OSC·R2 CSI arity 포함).

## 로컬 실행

```sh
pip install atheris
python tests/fuzz/gen_corpus.py                       # corpus/<target>/ 시드 생성
FUZZ_TARGET=vtparse python tests/fuzz/fuzzer.py -max_total_time=120 \
    tests/fuzz/corpus/vtparse
```

신규 의존성 없이 baseline 만 돌리려면:

```sh
python tests/run.py test_fuzz_parsers
```

## 크래시 → 회귀

atheris 가 크래시를 찾으면 `crash-<hash>` 파일을 떨군다. 그 입력을
`tests/fuzz/crashes/` 로 옮기고, `tests/test_fuzz_parsers.py::
test_fuzz_known_crashers_regression` 에 바이트를 추가해 **영구 회귀**로 못박은 뒤
근본(파서)을 고친다. 이미 그렇게 흡수한 예: R2 과다 파라미터 CSI(`ESC[38;2;H`).

`corpus/` 와 `crashes/` 는 `.gitkeep` 만 추적하고 내용물은 무시한다(생성물).
