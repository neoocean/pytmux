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

# 왜 바이트가 아니라 '내용'을 재나 (§10-18, 2026-08-02 Windows 실측)

종전엔 `now != data` 로 **바이트**를 쟀다. 그런데 줄끝은 **두 버전관리 어느 쪽도
내용으로 치지 않는다** — 둘 다 플랫폼마다 번역해 둔다:

  · Perforce: 픽스처 filetype 은 `unicode`(텍스트)이고 클라 `LineEnd: local` 이라
    **sync 가 Windows 엔 CRLF, macOS·Linux 엔 LF** 로 푼다.
  · git: `.gitattributes` 가 없어 `core.autocrlf` 가 체크아웃마다 정한다.

그래서 바이트 동일은 **아무도 지켜 주지 않는 성질**이었고, 실제로 셋이 겹쳐 터졌다:

 1. **CI**: 생성기 열여덟은 플랫폼 기본으로 쓰고 하나만 `newline="\n"` 로 LF 를 써서
    Windows 러너에서만 붉었다(2026-08-01, p4 69128 이 그 하나를 열여덟에 맞춰 껐다).
 2. **로컬**: 이 워크스페이스에서 `config_write.json` 이 **LF 로 눌러앉아 있었다.**
    p4 는 그것을 고치지 못한다 — 리비전이 안 바뀌었으니 `sync` 가 안 건드리고,
    `p4 diff` 는 줄끝을 번역해 비교하니 **수정으로도 안 잡힌다**. 즉 워크스페이스가
    두 VCS 어느 쪽에도 안 보이는 채로 어긋나 있고, 바이트 게이트만 그것을 "낡았다"로
    읽는다(실측: `p4 sync -f` 하면 CRLF 로 돌아오고 게이트가 초록이 된다).
 3. **§10-18 이 적어 둔 처방(`.gitattributes` + 생성기 열아홉 전부 LF 고정)은 이
    자리를 못 고친다** — 실측으로 확인했다. git 체크아웃을 LF 로 고정해도 p4 는
    여전히 Windows 에 CRLF 를 풀어 놓으므로, 생성기를 LF 로 고정하는 순간 **이
    워크스페이스가 영구히 붉어진다**(CI 의 빨강을 로컬의 빨강과 맞바꾸는 것뿐이다).

그래서 저장 형식을 통일하는 대신 **재는 자를 고쳤다**: 줄끝을 지운 뒤 비교한다
(`_content`). 게이트의 질문은 원래 "픽스처가 정본보다 낡았나" = **내용**이지 줄끝이
아니고, 줄끝은 어느 VCS 도 보존해 주지 않으니 잴 값이 없다. 이러면 어느 OS·어느
`core.autocrlf`·어느 `LineEnd` 조합에서도 같은 답이 나온다 — **두 VCS 설정이 서로
합의해야만 초록인 상태**를 아예 없앤다.

⚠ 이 완화가 게이트를 눈멀게 하지 않는다: 줄끝 **말고** 한 바이트라도 다르면 종전대로
낡음으로 잡는다. 줄끝만 다른 파일은 세어서 **사유와 함께 찍는다**(조용한 관용은
"쟀다"와 구분이 안 된다).
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


def _content(data):
    """줄끝을 지운 '내용'. 바이트 비교에서 **줄끝만** 걷어낸다(모듈 독스트링 참조).

    CRLF→LF 만 바꾼다. 홑 CR 은 안 건드린다 — 이 픽스처들은 JSON/JSONL 이라 문자열
    안의 제어문자는 `\\r` 로 이스케이프되므로 날 CR 바이트는 **줄끝일 때만** 나온다.
    넓게 접으면 언젠가 내용인 CR 을 삼킨다."""
    return data.replace(b"\r\n", b"\n")


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
        broken, stale, eol_only = [], [], set()
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
                    if now == data:
                        continue
                    if _content(now) == _content(data):
                        # 줄끝만 다르다 = **내용은 같다**. 두 VCS 다 줄끝을 플랫폼마다
                        # 번역하므로 이건 낡음이 아니다(모듈 독스트링 §줄끝).
                        eol_only.add(rel)
                    else:
                        stale.append((rel, os.path.basename(gen)))
                    # 이 생성기 몫은 확인했다 — 다음 생성기가 같은 파일로 또 걸리지
                    # 않게 기준을 옮긴다(한 파일에 두 생성기가 쓰지는 않지만,
                    # 그렇게 되면 여기서 순서 의존이 생기는 것을 막는다).
                    before[rel] = now
        finally:
            # `before` 는 위 루프에서 갱신되므로 원본은 **임시 사본**에서 되살린다.
            if not args.write:
                restore(tmp)

    # 관용한 것은 **사유와 함께** 남긴다 — 조용한 관용은 "쟀다"와 구분이 안 된다.
    if eol_only:
        names = ", ".join(sorted(os.path.basename(r) for r in eol_only))
        print(f"줄끝만 다른 픽스처 {len(eol_only)}개는 내용 같음으로 통과: {names}")
        print("  (두 VCS 다 줄끝을 플랫폼마다 번역한다 — p4 `LineEnd: local` ·"
              " git `core.autocrlf`. 잴 값이 없다.)")
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
