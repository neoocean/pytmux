#!/usr/bin/env python3
"""의미 색 이름의 **어휘표** 생성기 — 정본이 런에 실을 수 있는 이름 전부.

# 왜 필요한가

서버는 런에 색 값이 아니라 **의미 이름**을 싣는다(`{"theme": {"b": "primary"}}`) — 값을
실으면 서버가 UI 를 알게 되기 때문이다(설계 §10 위험표). 각 클라가 그 이름을 자기 테마에서
푼다: 정본은 `clientutil.theme_color()`, 우리는 `proto::session::theme`.

문제는 **모르는 이름의 처지**다. 우리 쪽은 모르는 이름을 `None` 으로 떨어뜨리는데, 그러면
그 자리는 런에 실린 리터럴만 남는다. ime-indicator 의 영문 배지가 정확히 그 함정을 밟았다
(pytmux-16, 2026-08-03 실측):

    _THEME = {"한": "success", "EN": "primary"}     # 정본 플러그인
    run  = {"style": {"f": "black", "bo": 1}, "theme": {"b": _THEME[label]}}

`success` 는 우리가 알아 `[한]` 이 밝은 초록 바탕에 뜨는데, **`primary` 는 표에 없어**
바탕이 안 칠해지고 `[EN]` 은 **검은 글자만** 남는다 = 어두운 캔버스 위에서 안 보인다.
제보에 "한글 모드에만 뜨고 영문 모드에서는 사라진다"로 적힌 것이 이것이다. 픽셀로도
확인했다 — 한글 컷에는 배지 바탕색 화소가 640개, 영문 컷에는 **0개**다.

즉 **어휘가 갈리면 화면이 조용히 빈다.** 예외도 로그도 없다. 그래서 어휘를 정본에서
뽑아 고정하고, 클라가 그 전부를 아는지 기계로 잰다.

# 어휘의 정본은 무엇인가

`clientutil._THEME_FALLBACK` 이다. 정본은 활성 Textual 테마의 변수를 먼저 보고 없으면
이 표로 떨어지므로, **이 표의 키가 곧 "정본이 뜻을 아는 이름"의 전부**다. 플러그인이
그 밖의 이름을 쓰면 정본에서도 `white` 로 떨어지니 그건 플러그인 쪽 결함이다.

이름만 뽑고 **값(hex)은 안 뽑는다.** 값이 옮겨 가면 두 클라가 같은 배색이 돼야 한다는
뜻이 되는데, 이 설계의 요점은 그 반대다 — GUI 는 tokyonight 로, 정본은 Textual 테마로
각자 푼다. 재는 것은 **"아는가"**이지 "같은 색인가"가 아니다.

    python3 scripts/gen_theme_names.py [--pytmux ..]
"""

import argparse
import json
import os
import sys


def _utf8_stdout():
    """Windows 콘솔 코드페이지에서 한글 요약 print 가 죽지 않게(다른 생성기와 같은 처방).

    파일은 이미 다 쓴 뒤라, 여기서 죽으면 종료코드 1 만 보고 "생성 실패"로 오인한다.
    """
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", default=os.path.join(".."))
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "theme_names.json"))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib.clientutil import _THEME_FALLBACK  # noqa: E402

    names = sorted(_THEME_FALLBACK)

    # 지금 플러그인이 실제로 싣는 이름. 어휘의 부분집합이라야 한다 — 아니면 정본에서도
    # `white` 로 떨어지므로 **정본 쪽 결함**이고, 여기서 먼저 운다.
    emitted = sorted({"success", "primary", "secondary", "warning", "error", "foreground"})
    unknown = [n for n in emitted if n not in _THEME_FALLBACK]
    if unknown:
        raise SystemExit(
            f"플러그인이 정본도 모르는 이름을 싣는다: {unknown}\n"
            "  → 정본 `_THEME_FALLBACK` 에 넣든지 플러그인이 아는 이름을 쓰든지 한다.")

    out = {"names": names, "emitted": emitted}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, sort_keys=True)
        fp.write("\n")
    print(f"의미 색 이름 {len(names)}종 → {args.out}")
    print(f"  어휘: {names}")
    print(f"  지금 실리는 것 {len(emitted)}종: {emitted}")


if __name__ == "__main__":
    main()
