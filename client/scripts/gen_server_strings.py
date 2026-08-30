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
import re
import sys

OUT = os.path.join("crates", "proto", "tests", "fixtures", "server_strings.json")

# 서버가 자료에 실어 보내는 문자열의 네임스페이스 → 어디서 나가는가(사람 읽을 근거).
SHIPPED = {
    "pscreen": "claude-code 의 Tier C 화면 스펙(제목·힌트·열 이름)",
    "uview": "claude-token-usage-view 의 Tier B 셀 런(한도 안내·카운트다운 라벨)",
    "p4cl": "p4changes 의 Tier C 화면 스펙",
    "ph": "prompt-history 의 Tier C 화면 스펙",
    "mdir": "mdir 의 Tier C 화면 스펙(제목·안내·물음·결과·실패 사유)",
    "nsync": "claude-name-sync 의 Tier C 화면 스펙(규칙 판의 제목·안내·물음)",
    "claude": "claude-code 의 상태줄 배지(P6 후반이 Tier B 로 옮길 것)",
    "ccmsg": "claude-code 가 클라에 보내는 안내 줄",
    # ★ 2026-08-25 에 더했다(pytmux-371). 한 줄뿐이지만 그 한 줄이 **한도 판의 꼬리줄**
    #   이고, 꼬리줄은 「이 판이 무는 키」를 광고하는 자리라 영어로 안 뜨면 그 판의 조작을
    #   영어 사용자가 못 읽는다. 네임스페이스가 빠져 있는 동안은 게이트가 **눈을 감고**
    #   있었다 — 고정 리터럴이라 옮기는 값은 en 표 한 줄뿐이다.
    "cusage": "claude-code 한도(/usage) 판의 꼬리줄",
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


def _wire_producers(root):
    """소켓 너머로 나가는 글을 **짓는 자리**를 소스에서 찾는다.

    돌려주는 것 = `(참조된_i18n_키, 알림으로_나가는_키, 카탈로그에_없는_한국어_리터럴)`.

    # 왜 네임스페이스만으로는 부족했나 (2026-08-02o)

    종전에는 `SHIPPED` 네임스페이스에 속한 카탈로그 항목을 **전부** "서버가 보내는 글"로
    셌다. 실측해 보니 그 12개 중 **7개는 소켓을 안 건넜다** — `app.display_message`·
    Textual 화면(`screen.py`)에서만 쓰는 클라 로컬 문자열이라 애초에 번역 문제가 없다.
    그리고 진짜 문제는 반대쪽에 있었다: 화면 스펙에 **직접 적은 한국어 22개**는
    카탈로그에 없으니 이 생성기의 눈에 아예 안 보였고, 그래서 `en_server.rs` 에도 못
    들어가 **영어 사용자에게 그대로 한국어로 떴다**(게이트는 초록이었다).

    그래서 세는 자리를 카탈로그가 아니라 **짓는 코드**로 옮긴다:
      * `plugin_screen`·`plugin_cells`·`plugin_badges` dict 를 만드는 함수
      * 알림 발신(`note(...)`/`_notice_msg(...)`) — 서버가 클라에 미는 한 줄

    # 그 스캔이 놓치던 것 (2026-08-02p)

    종전에는 **그 dict 를 직접 짓는 함수 안의 리터럴만** 셌다. `mdir` 에 자를 대 보니
    그 한 겹 밖에 같은 성질의 글이 그만큼 더 있었다 — 전부 소켓을 건너는데 수에는
    안 잡혔다:

      * **모듈 레벨 표**(`_REASONS` 10 · `_VERBS` 5 · `_SCREEN_HINT`) — 함수가 이름으로
        참조하지만 정의는 함수 밖이라 `ast.walk(fn)` 이 못 본다.
      * **한 겹 위**(`_begin`·`_apply`) — 자기는 dict 를 안 짓고 `self._ask` 로 짓는다.
      * **한 겹 아래**(`_result_note`) — 돌려준 문자열이 `note` 칸으로 실려 나간다.

    "안 세지는 자리로 옮기면 게이트가 조용해진다"가 성립하면 래칫이 아니다. 그래서 같은
    파일 안에서 **호출 그래프를 양방향으로 닫고**(producer 를 부르는 함수도 producer ·
    producer 가 부르는 함수도 훑는다) 그 함수들이 **이름으로 참조하는 모듈 레벨 상수**
    까지 따라간다.

    한계는 여전히 정직하게 적는다: **파일 경계는 안 넘는다**(다른 모듈의 헬퍼가 지은
    한국어는 그 모듈에 wire 빌더가 있을 때만 잡힌다). 넘기려면 프로젝트 전역 호출
    그래프가 필요한데, 그러면 서버 내부 로그 문자열까지 딸려 와 이 축의 뜻("소켓을
    건너는 글")이 흐려진다.
    """
    import ast
    import glob

    hangul = re.compile(r"[가-힣]")
    wire = ("plugin_screen", "plugin_cells", "plugin_badges")
    # 리터럴은 **집합**이다 — 한 표를 두 함수가 참조하면 같은 줄이 두 번 잡힌다
    # (`_REASONS` 는 `_result_note` 만 읽지만 `_SCREEN_HINT` 는 여럿이 읽는다).
    keys, notices, literals = set(), set(), set()
    # 훑은 자리(파일:함수) — **0 이 통과로 보이지 않게** 하는 증거다. 스캐너가 조용히
    # 아무것도 안 보게 되면 `wire_literals` 가 0 이 되는데, 그 0 은 "다 옮겼다"와
    # 구별되지 않는다. 소비자(Rust 게이트)가 이 수로 그 둘을 가른다.
    seen = set()

    def builds_wire(fn):
        for n in ast.walk(fn):
            if isinstance(n, ast.Dict):
                for k, v in zip(n.keys, n.values):
                    if (isinstance(k, ast.Constant) and k.value == "t"
                            and isinstance(v, ast.Constant) and v.value in wire):
                        return True
        return False

    def calls_in(fn):
        """이 함수가 부르는 이름들 — `f(...)` 와 `self.f(...)` 를 같게 본다."""
        out = set()
        for n in ast.walk(fn):
            if not isinstance(n, ast.Call):
                continue
            if isinstance(n.func, ast.Name):
                out.add(n.func.id)
            elif isinstance(n.func, ast.Attribute):
                out.add(n.func.attr)
        return out

    # 소켓으로 **안** 나가는 자리 — 진단 로그다. 위 닫힘이 `serverio` 의 프레임 함수들을
    # 정당하게 끌어오는데, 그 안의 `_log_error("어디", "한국어")` 까지 세면 게이트가
    # "카탈로그로 옮겨라"라고 틀린 처방을 낸다(그 글은 사람이 읽는 로그 파일로 간다).
    sinks = ("_log_error", "print")

    def sink_nodes(body):
        out = set()
        for n in ast.walk(body):
            if isinstance(n, ast.Call) and (
                    (isinstance(n.func, ast.Name) and n.func.id in sinks)
                    or (isinstance(n.func, ast.Attribute) and n.func.attr in sinks)):
                out |= {id(sub) for sub in ast.walk(n)}
        return out

    def names_in(fn):
        """이 함수가 **읽는** 이름들 — 모듈 레벨 표를 따라가는 데 쓴다."""
        return {n.id for n in ast.walk(fn)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}

    def first_str(call):
        if call.args and isinstance(call.args[0], ast.Constant) \
                and isinstance(call.args[0].value, str):
            return call.args[0].value
        return None

    for path in sorted(glob.glob(os.path.join(root, "pytmuxlib", "**", "*.py"),
                                 recursive=True)):
        with open(path, encoding="utf-8") as fp:
            tree = ast.parse(fp.read())
        # 경로는 **`/` 로 못박는다** — 이 픽스처는 OS 를 건너 비교된다(§10-18 이 줄끝에서
        # 겪은 것과 같은 자리다). `os.sep` 을 그대로 실으면 Windows 에서 뽑은 것과
        # macOS 에서 뽑은 것이 글자 단위로 갈려 `check_fixtures` 가 헛되이 운다.
        rel = os.path.relpath(path, root).replace(os.sep, "/")
        # 알림 발신은 어느 함수에 있든 소켓으로 나간다 — 파일 전체에서 찾는다.
        for n in ast.walk(tree):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                    and n.func.id in ("note", "_notice_msg"):
                k = first_str(n)
                if k:
                    keys.add(k)
                    notices.add(k)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr in ("_notice_msg", "_remote_notice"):
                k = first_str(n)
                if k:
                    keys.add(k)
                    notices.add(k)
        # 독스트링은 소켓으로 안 나간다 — 줄 번호로 걸러 낸다(`ast.get_docstring` 은
        # 정리된 문자열만 주므로 자리를 모른다).
        docline = set()
        for n in ast.walk(tree):
            body = getattr(n, "body", None)
            if not isinstance(body, list) or not body:
                continue
            head = body[0]
            if isinstance(head, ast.Expr) and isinstance(head.value, ast.Constant) \
                    and isinstance(head.value.value, str):
                docline.add(head.value.lineno)
        # 이 파일의 함수들(메서드 포함)을 이름으로. 같은 이름이 여럿이면 전부 묶는다 —
        # 호출부는 `self._spec` 처럼 이름만 남기므로 어느 클래스의 것인지 못 가른다.
        fns = {}
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fns.setdefault(n.name, []).append(n)
        # 모듈 레벨 상수(표·문구) — 함수가 이름으로 참조하면 그 안의 한국어도 나간다.
        consts = {}
        for n in tree.body:
            if isinstance(n, ast.Assign):
                for tgt in n.targets:
                    if isinstance(tgt, ast.Name):
                        consts[tgt.id] = n.value

        # 씨앗 = wire dict 를 **직접** 짓는 함수.
        producers = {name for name, nodes in fns.items()
                     if any(builds_wire(nd) for nd in nodes)}
        # ① 위로 — producer 를 부르는 함수도 producer 다(`_begin` 은 `_ask` 로 짓는다).
        changed = True
        while changed:
            changed = False
            for name, nodes in fns.items():
                if name in producers:
                    continue
                if any(producers & calls_in(nd) for nd in nodes):
                    producers.add(name)
                    changed = True
        # ② 아래로 — producer 가 부르는 같은 파일 함수도 훑는다(`_result_note` 가 지은
        #    줄은 `note` 칸으로 그대로 실린다). producer 로 승격하지는 않는다: 그러면
        #    `_offload` 같은 배관을 타고 파일 전체가 딸려 온다.
        scanned, frontier = set(producers), set(producers)
        while frontier:
            nxt = set()
            for name in frontier:
                for nd in fns.get(name, []):
                    nxt |= calls_in(nd) & set(fns)
            frontier = nxt - scanned
            scanned |= frontier

        for name in sorted(scanned):
            seen.add(f"{rel}:{name}")
            for fn in fns.get(name, []):
                bodies = [fn]
                # 이 함수가 읽는 모듈 레벨 표까지 같은 눈으로 본다.
                bodies += [consts[nm] for nm in sorted(names_in(fn) & set(consts))]
                for body in bodies:
                    logged = sink_nodes(body)
                    for n in ast.walk(body):
                        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                                and n.func.attr in ("t", "phrase", "tc"):
                            k = first_str(n)
                            if k:
                                keys.add(k)
                        elif isinstance(n, ast.Constant) and isinstance(n.value, str) \
                                and hangul.search(n.value) and n.lineno not in docline \
                                and id(n) not in logged:
                            literals.add(
                                (rel, n.lineno, n.value))
    if not keys:
        sys.exit("소켓으로 나가는 글을 짓는 자리를 하나도 못 찾았다 — 통과가 아니라 "
                 "고장이다(AST 스캐너가 낡았으면 고칠 것)")
    return keys, notices, sorted(literals), seen


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

    shipped_keys, notice_keys, wire_literals, wire_scanned = _wire_producers(
        os.path.abspath(args.pytmux))
    # 재료가 실리는 길이 둘이다: 스펙·셀·배지는 `i18n.phrase` 로, 알림은 `_notice_msg`
    # 가 **자기가 받은 ko 포맷과 kw 를 그대로** 싣는다(자리가 있을 때만 — 자리가 없으면
    # 원문이 곧 키라 로케일 ⓐ 로 풀린다).
    carried = set(_carried(os.path.abspath(args.pytmux), ko))
    carried |= {k for k in notice_keys if k in ko and "{" in ko[k]}
    carried = sorted(carried)
    # 합성된 글은 **실제로 소켓을 건너는 것만** 센다(위 `_wire_producers` 머리말).
    # 카탈로그에만 있고 아무 데서도 안 나가는 것 · 클라 로컬 화면에서만 쓰는 것은
    # 여기서 빠진다 — 그것들을 세면 "영어 사용자에게 한국어로 뜬다"가 거짓이 된다.
    shipped_fmt = {ko[k] for k in shipped_keys if k in ko and "{" in ko[k]}
    formatted = [t for t in set(formatted) if t in shipped_fmt]
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
        # 화면 스펙에 **직접 적힌** 한국어 — 카탈로그를 안 거치므로 영어 표에 못 들어가고
        # 영어 사용자에게 그대로 뜬다. 지금은 못 고치는 것이 아니라 **아직 안 옮긴 것**
        # 이라, 고정 리터럴처럼 전수로 못 박는 대신 수를 래칫으로 잡는다. 0 이 되는 날이
        # "서버가 보내는 글은 전부 카탈로그를 거친다"가 참이 되는 날이다.
        "wire_literals": [f"{path}:{line} {text}" for path, line, text in wire_literals],
        # 위 목록을 **어디서** 찾았나(파일:함수). 목록이 0 일 때 "다 옮겼다"와 "스캐너가
        # 눈을 감았다"를 가르는 유일한 증거라 함께 싣는다.
        "wire_scanned": sorted(wire_scanned),
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fp:
        json.dump(payload, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    print(f"{args.out} — 고정 {len(fixed)}개 · 합성 {len(payload['formatted'])}개 "
          f"(그중 재료로 실리는 것 {len(carried)}개) · "
          f"스펙에 직접 적힌 한국어 {len(wire_literals)}개")


if __name__ == "__main__":
    main()
