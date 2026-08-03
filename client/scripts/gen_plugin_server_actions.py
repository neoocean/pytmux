#!/usr/bin/env python3
"""서버가 **실제로 받는** 플러그인 액션 이름을 정본에게 물어 뽑는다.

# 왜 필요한가

우리는 플러그인 명령을 오래 `plugin_open`("화면을 다오")으로만 보냈고, 상태를 바꾸는
명령에는 그 길이 틀려서 서버가 *"이 플러그인은 화면 스펙을 제공하지 않습니다"* 로
거절했다 — 팔레트에 보이는데 눌러도 안 먹는 줄 스물셋(pytmux-35).

고치는 길은 **서버가 이미 받고 있는 액션 이름을 그대로 치는 것**이다(정본 훅이 치는
그 이름). 그런데 그 이름은 파이썬 소스에만 있고 우리 표는 손으로 적는다 — 한 글자만
달라도 명령은 **조용히 아무 일도 안 한다.** 종전(거절 알림)보다 오히려 **더 조용해진다.**

그래서 이름을 눈으로 옮기지 않고 **정본에게 물어** 고정한다.

# 어떻게 묻나

레지스트리의 `server_command(server, client, sess, action, msg)` 를 **후보 이름마다 실제로
불러 본다.** 아는 이름이면 지시("handled"/"send_full"/"broadcast")를 돌려주고, 모르면
`None` 이다. `server` 자리에는 아무 속성이나 받아 삼키는 인형을 넣는다 — 우리가 재는 것은
"그 이름이 디스패치되는가"이지 그 다음에 무슨 일이 나는가가 아니다.

⚠ 인형을 쓰므로 **부작용이 있는 액션은 예외를 낼 수 있다**(`refresh_usage` 는
`asyncio.create_task` 를 부른다). 예외가 났다는 것은 **그 이름이 매칭됐다는 뜻**이므로
아는 이름으로 센다 — 모르는 이름은 그 전에 `None` 으로 조용히 빠진다.

    python3 scripts/gen_plugin_server_actions.py [--pytmux ..]
"""

import argparse
import json
import os
import sys


def _utf8_stdout():
    """Windows 콘솔 코드페이지에서 한글 요약 print 가 죽지 않게(다른 생성기와 같은 처방)."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")


class _Doll:
    """무엇을 물어도 받아 주는 인형. 부르면 자기를 돌려준다(체이닝 대비)."""

    def __getattr__(self, _name):
        return self

    def __call__(self, *_a, **_kw):
        return self

    def __bool__(self):
        return True

    # 서버가 세션 목록·패널을 훑는 경우를 위한 최소 시퀀스 흉내.
    def __iter__(self):
        return iter(())

    def get(self, *_a, **_kw):
        return None


def probe(registry, names):
    """이름마다 `server_command` 를 실제로 불러 아는 것만 남긴다."""
    known = []
    for name in sorted(names):
        try:
            directive = registry.server_command(_Doll(), _Doll(), _Doll(), name, {})
        except Exception:
            # 매칭된 뒤 인형 위에서 넘어진 것 — 그 이름은 **안다**.
            known.append(name)
            continue
        if directive is not None:
            known.append(name)
    return known


def candidates(root):
    """후보 이름. 플러그인 소스에서 `action == "..."` 꼴을 긁어 모은다 —
    후보를 넓게 잡는 것은 안전하다(진짜 판정은 위 `probe` 가 한다)."""
    import re
    out = set()
    base = os.path.join(root, "pytmuxlib", "plugins")
    for dirpath, _dirs, files in os.walk(base):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            with open(os.path.join(dirpath, fn), encoding="utf-8") as fp:
                src = fp.read()
            out.update(re.findall(r'action\s*==\s*"([a-z][a-z0-9_]*)"', src))
            out.update(re.findall(r'send_cmd\(\s*"([a-z][a-z0-9_]*)"', src))
    return out


def main():
    _utf8_stdout()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pytmux", default=os.path.join(".."))
    ap.add_argument("--out", default=os.path.join(
        "crates", "proto", "tests", "fixtures", "plugin_server_actions.json"))
    args = ap.parse_args()

    sys.path.insert(0, os.path.abspath(args.pytmux))
    from pytmuxlib import plugins  # noqa: E402

    registry = plugins.load()
    names = probe(registry, candidates(os.path.abspath(args.pytmux)))
    if not names:
        raise SystemExit("아무 액션도 못 찾았다 — 빈 픽스처는 오라클을 공허하게 만든다")

    out = {"actions": names}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, sort_keys=True)
        fp.write("\n")
    print(f"서버가 받는 플러그인 액션 {len(names)}종 → {args.out}")
    print(f"  {names}")


if __name__ == "__main__":
    main()
