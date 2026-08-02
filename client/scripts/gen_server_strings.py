#!/usr/bin/env python3
"""**서버가 지어 보내는 글** 픽스처 — 그 글이 이 클라에서 영어로 뜨는가.

# 무엇이 문제였나

이 저장소의 로케일 규약은 *"화면 문자열은 클라가 렌더하고 로케일은 per-user"* 다
(`pytmuxlib/i18n.py` 모듈 문서). 그런데 Tier B(셀·상태줄 기여)와 Tier C(화면 스펙)가
생기면서 **서버가 지은 글**이 클라에 실려 오기 시작했다 — 화면 제목·힌트·안내 한 줄.
그 글은 **서버 프로세스의 로케일**을 탄다. 서버가 ko 면 영어 사용자도 한국어를 본다.

갈래가 둘이고 비용이 아주 다르다:

- **고정 리터럴**(`{…}` 자리가 없는 것) — 우리 클라는 **한국어 원문을 키로** 번역한다
  (`base::i18n::t`). 즉 그 원문이 우리 표에 있기만 하면 **프로토콜을 안 건드리고**
  영어로 뜬다. 이 픽스처의 `fixed` 가 그것이다.
- **합성된 줄**(`{pct}%/5h 사용` 처럼 자리가 있는 것) — 원문이 키가 못 된다(값이 매번
  다르다). `tf(원문, args)` 로는 되지만 **서버가 args 를 따로 실어 보내야** 한다.
  이 픽스처의 `formatted` 는 그 목록이고, 게이트는 **수만 붙잡는다**(래칫).

# 왜 정본에서 뽑나

두 언어의 값이 이미 정본 카탈로그에 있다(`i18n.register`). 여기서 다시 번역하면
그 순간 두 벌이 되고, 갈리는 순간 증상은 "한 화면만 한국어"다. 그래서 ko→en 짝을
**정본에서 그대로 뽑아** 픽스처로 못박고, Rust 쪽은 자기 표가 그 짝을 재현하는지만 본다.

# 어느 네임스페이스가 "서버가 보내는" 것인가

플러그인이 Tier B/C 로 **자료에 실어 보내는** 문자열의 네임스페이스만 고른다. 목록을
손으로 적는 이유는 자동 판정이 불가능해서다 — 같은 카탈로그에 클라 전용 문자열
(`dialog.*`·`screen.*`)이 섞여 있고, 그것들은 클라가 자기 로케일로 이미 짓는다.
새 Tier B/C 시민이 생기면 **여기에 한 줄 더한다**(그러면 게이트가 즉시 운다).

    python3 scripts/gen_server_strings.py [--pytmux ..] [--out ..]
"""

import argparse
import json
import os
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "server_strings.json")

# 서버가 자료에 실어 보내는 문자열의 네임스페이스 → 어디서 나가는가(사람 읽을 근거).
SHIPPED = {
    "pscreen": "claude-code 의 Tier C 화면 스펙(제목·힌트·열 이름)",
    "uview": "claude-token-usage-view 의 Tier B 셀 런(한도 안내·카운트다운 라벨)",
    "p4cl": "p4changes 의 Tier C 화면 스펙",
    "ph": "prompt-history 의 Tier C 화면 스펙",
    "claude": "claude-code 의 상태줄 배지(P6 후반이 Tier B 로 옮길 것)",
    "ccmsg": "claude-code 가 클라에 보내는 안내 줄",
}


def _utf8_stdout():
    """Windows 콘솔(cp949)에서 한글 요약 print 가 죽지 않게 — 다른 생성기와 같은 이유."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _load_catalog(root):
    """정본 카탈로그를 **플러그인까지 로드한 상태**로 얻는다.

    플러그인은 자기 문자열을 모듈 import 시점에 `i18n.register` 한다(delete-to-disable).
    그래서 `pytmuxlib.i18n` 만 import 하면 코어 시드밖에 없다 — 실제로 그렇게 뽑았다가
    `pscreen.*` 이 하나도 안 나왔다. 플러그인 레지스트리를 세워 전부 물린다."""
    sys.path.insert(0, root)
    from pytmuxlib import i18n, plugins

    plugins.load()
    catalog = i18n._CATALOG
    if not catalog.get("en"):
        sys.exit("en 카탈로그가 비었다 — 통과가 아니라 고장이다(로드 경로를 볼 것)")
    return catalog


def _carried(root, ko):
    """`i18n.phrase(...)` 로 **재료까지 실어 보내는** 키들.

    호출부를 소스에서 긁는다 — import 로는 알 수 없다(어느 키가 어느 훅으로 나가는지는
    호출 위치가 정보다). **하나도 못 찾으면 실패**로 떨어뜨린다: 빈 결과가 통과로 보이면
    "다 옮겼다"와 "한 줄도 안 옮겼다"가 같은 초록이 된다(라이선스 게이트가 그 함정을
    한 번 밟았다)."""
    import glob
    import re

    pat = re.compile(r"""phrase\(\s*["']([a-z0-9_.]+)["']""")
    found = set()
    for path in glob.glob(os.path.join(root, "pytmuxlib", "**", "*.py"),
                          recursive=True):
        with open(path, encoding="utf-8") as fp:
            found |= set(pat.findall(fp.read()))
    if not found:
        sys.exit("i18n.phrase 호출을 하나도 못 찾았다 — 통과가 아니라 고장이다"
                 "(정규식이 낡았으면 고칠 것)")
    return sorted(k for k in found if k in ko)


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--pytmux", default=os.path.join(os.path.dirname(__file__), "..", ".."))
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()

    catalog = _load_catalog(os.path.abspath(args.pytmux))
    ko, en = catalog.get("ko", {}), catalog.get("en", {})

    fixed, formatted = {}, []
    for key, ko_text in sorted(ko.items()):
        ns = key.split(".", 1)[0]
        if ns not in SHIPPED:
            continue
        en_text = en.get(key)
        if en_text is None:
            # 정본에 en 이 없으면 우리가 번역할 근거도 없다. 이것도 사실이라 적어 둔다 —
            # 조용히 빼면 "번역이 다 됐다"로 읽힌다.
            formatted.append(ko_text) if "{" in ko_text else fixed.setdefault(ko_text, ko_text)
            continue
        if "{" in ko_text:
            formatted.append(ko_text)
        else:
            fixed[ko_text] = en_text

    carried = _carried(os.path.abspath(args.pytmux), ko)
    payload = {
        "_comment": "python3 scripts/gen_server_strings.py 로 생성. 출처 = 정본 "
                    "pytmuxlib/i18n.py 카탈로그(플러그인 register 포함). "
                    "fixed = 한국어 원문을 키로 번역할 수 있는 것(우리 표에 있어야 한다). "
                    "formatted = 자리(`{…}`)가 있어 원문이 키가 못 되는 것 — 서버가 "
                    "args 를 따로 실어야 풀린다(로케일 ⓑ).",
        "namespaces": SHIPPED,
        "fixed": fixed,
        "formatted": sorted(set(formatted)),
        # `i18n.phrase` 로 **원문 포맷 + 인자**까지 실어 보내는 것들 — 클라가 자기
        # 로케일로 다시 짓는다(로케일 ⓑ). 이 목록이 자랄수록 아래 래칫이 줄어든다.
        #
        # ⚠ **포맷 원문도 번역이 있어야 한다.** 클라는 `tf(원문, args)` 로 짓는데 그
        #   원문이 우리 표에 없으면 한국어 포맷에 값만 끼워 넣는다 — "실어 보냈다"가
        #   곧 "영어로 뜬다"가 아니다. 그래서 여기도 ko→en 짝으로 싣고 게이트가 잰다.
        "carried": {ko[k]: en.get(k, ko[k]) for k in carried},
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 고정 {len(fixed)}개 · 합성 {len(payload['formatted'])}개 "
          f"(그중 재료로 실리는 것 {len(carried)}개)")


if __name__ == "__main__":
    main()
