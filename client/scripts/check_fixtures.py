#!/usr/bin/env python3
"""게이트 — 픽스처가 **정본보다 낡지 않았나**.

# 왜 있나 (트리 통합 계획 §4.8 · §6.2)

생성기 열여덟은 정본을 직접 import 해 픽스처를 뽑고, Rust 적합성 테스트가 그 픽스처를
읽는다. 그런데 **아무도 "픽스처가 지금 정본과 같은가"를 재지 않았다** — 정본이 앞서
나가도 픽스처는 그대로이고, 픽스처를 읽는 테스트는 **초록인 채로** 낡은 세상을 지킨다.

2026-08-01 트리 통합이 그 부채를 눈으로 드러냈다: 생성기를 전부 돌렸더니 넷이 갈렸고
(`touch-scroll` 설정 · `copy-unwrap` 분류 · `keys.g_drag` 제스처 · `attach-remote via` ·
`select-window wid`), 그중 하나(`wid`)는 **정본이 고친 레이스 결함**이 우리에게만 남아
있다는 뜻이었다. 아무도 안 재서 그만큼 조용히 벌어졌다.

# 어떻게 재나

생성기를 전부 돌려 결과를 **작업본과 대조**한다. 하나라도 갈리면 rc 1 이고, 어느 파일이
어느 생성기 때문에 갈렸는지 적는다. 원래 내용은 반드시 되돌린다(`finally`) — 게이트가
작업본을 바꾸면 그건 게이트가 아니다.

`--write` 를 주면 되돌리지 않는다(= 갱신 모드). 갱신한 뒤에는 **적합성 테스트가 새로
울 것**이고, 그 울음이 곧 "이관할 표면"의 목록이다.

# 왜 셸이 아니라 파이썬인가

생성기 하나(`gen_rtt_fixture.py`)는 `--out` 을 안 받아 제자리에만 쓴다. 그래서 "임시
디렉토리로 뽑아 비교"가 통째로는 불가능하고, **백업 → 제자리 생성 → 대조 → 복원**이
전부에 통하는 유일한 순서다.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HERE, "scripts")

# 출력을 UTF-8 로 고정한다(`check_all.py`·`publish_check.py` 와 같은 처방). 이 게이트의
# 판정문은 한글인데, **stdout 이 파이프면**(= 합본 게이트가 부를 때) 파이썬은 로케일
# 인코딩을 쓴다 — 한국어 Windows 에서 `print("FAIL: 생성기가 깨졌다 …")` 가
# UnicodeEncodeError 로 죽어, 게이트가 **판정을 내리기 전에** 트레이스백으로 끝났다
# (실측 2026-08-01: 그래서 합본 게이트에서 이 스텝은 늘 "환경 실패"처럼 보였다).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

# 생성기가 쓰는 픽스처 디렉토리들. **여기 없는 곳에 쓰는 생성기가 생기면 그 픽스처는
# 대조에서 샌다** — 새 생성기를 만들면 출력 위치가 이 목록 안인지 볼 것.
FIXTURE_DIRS = [
    os.path.join("crates", "proto", "tests", "fixtures"),
    os.path.join("crates", "claude", "tests", "fixtures"),
]


def generators():
    return sorted(
        os.path.join(SCRIPTS, f)
        for f in os.listdir(SCRIPTS)
        if f.startswith("gen_") and f.endswith(".py")
    )


def snapshot(dst):
    """지금 픽스처를 통째로 떠 둔다. 돌려주는 것은 `{상대경로: 내용}`."""
    out = {}
    for rel in FIXTURE_DIRS:
        src = os.path.join(HERE, rel)
        if not os.path.isdir(src):
            continue
        shutil.copytree(src, os.path.join(dst, rel.replace(os.sep, "_")))
        for name in os.listdir(src):
            path = os.path.join(src, name)
            if os.path.isfile(path):
                with open(path, "rb") as fh:
                    out[os.path.join(rel, name)] = fh.read()
    return out


def restore(tmp):
    """임시 사본에서 픽스처를 되돌린다. 게이트가 작업본을 바꾸면 그건 게이트가 아니다."""
    for rel in FIXTURE_DIRS:
        src = os.path.join(tmp, rel.replace(os.sep, "_"))
        if not os.path.isdir(src):
            continue
        for name in os.listdir(src):
            s = os.path.join(src, name)
            if os.path.isfile(s):
                shutil.copyfile(s, os.path.join(HERE, rel, name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="갈린 것을 되돌리지 않는다(픽스처 갱신 모드)")
    ap.add_argument("--pytmux", default=os.path.join(HERE, ".."),
                    help="정본(pytmux) 저장소 루트")
    args = ap.parse_args()

    gens = generators()
    if not gens:
        print("FAIL: 생성기를 하나도 못 찾았다 — 통과가 아니라 고장이다")
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        before = snapshot(tmp)
        count = len(before)
        if not before:
            print("FAIL: 픽스처를 하나도 못 찾았다 — 통과가 아니라 고장이다")
            return 1
        broken, stale = [], []
        try:
            for gen in gens:
                proc = subprocess.run(
                    [sys.executable, gen, "--pytmux", args.pytmux]
                    if "--pytmux" in open(gen, encoding="utf-8").read()
                    else [sys.executable, gen],
                    # 생성기의 글자는 **양쪽 다** UTF-8 로 고정한다(실측 2026-08-01).
                    #  · 읽기(encoding): 안 주면 Windows 기본 디코더가 로케일이라 리더
                    #    스레드가 죽고, **생성기가 깨졌을 때 보여 줄 stderr 를 잃는다**.
                    #  · 쓰기(PYTHONIOENCODING): 생성기 열아홉은 한글을 찍는데 stdout 이
                    #    파이프면 로케일로 인코딩해 UnicodeEncodeError 로 **죽는다** —
                    #    rc != 0 이라 이 게이트는 그것을 "생성기가 깨졌다"로 보고했고,
                    #    합본 게이트에서 픽스처 스텝은 그래서 늘 빨간 줄이었다.
                    # 이 환경변수는 std 스트림에만 닿는다 — 생성기가 **쓰는 픽스처 파일**은
                    # `open()` 이라 영향이 없다(게이트가 재는 것을 바꾸지 않는다).
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    cwd=HERE, capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                )
                if proc.returncode != 0:
                    broken.append((os.path.basename(gen), proc.stderr.strip()[-400:]))
                    continue
                for rel, data in list(before.items()):
                    with open(os.path.join(HERE, rel), "rb") as fh:
                        now = fh.read()
                    if now != data:
                        stale.append((rel, os.path.basename(gen)))
                        # 이 생성기 몫은 확인했다 — 다음 생성기가 같은 파일로 또 걸리지
                        # 않게 기준을 옮긴다(한 파일에 두 생성기가 쓰지는 않지만,
                        # 그렇게 되면 여기서 순서 의존이 생기는 것을 막는다).
                        before[rel] = now
        finally:
            # `before` 는 위 루프에서 갱신되므로 원본은 **임시 사본**에서 되살린다.
            if not args.write:
                restore(tmp)

    if broken:
        print("FAIL: 생성기가 깨졌다 — 정본을 못 읽으면 이 게이트 전체가 무의미하다")
        for name, err in broken:
            print(f"  {name}: {err.splitlines()[-1] if err else '(stderr 없음)'}")
        return 1
    if stale:
        print("FAIL: 픽스처가 정본보다 낡았다 — 정본이 앞서 나갔는데 우리가 안 따라갔다")
        for rel, gen in stale:
            print(f"  {rel}  ← {gen}")
        print("  → `python3 scripts/check_fixtures.py --write` 로 갱신한 뒤,")
        print("    새로 우는 적합성 테스트가 가리키는 표면을 이관할 것.")
        return 1
    print(f"OK: 픽스처 {count}개가 정본과 같다(생성기 {len(gens)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
