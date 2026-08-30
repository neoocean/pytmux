"""ConPTY **호스트 선택**의 상시 오라클 — pytmux/pytmux-208.

2026-08-22 에 기본을 **번들 OpenConsole → 시스템 conhost** 로 뒤집었다(사람의 결정
2026-08-16). 그 뒤집기는 «실험»이라 다음 사람이 결과를 읽고 되돌릴 수 있어야 하는데,
읽을 수 있으려면 **무엇이 기본인지가 흔들리지 않아야** 한다. 이 파일이 그것을 못박는다.

⛔ **이 시험은 Windows 를 안 탄다** — 판정을 `conpty.conpty_dll_pref()` **순수 함수**로
빼 뒀기 때문이다(라우팅 자체는 `if IS_WINDOWS:` 안에서 import 시점에 굳어 macOS 러너가
못 잰다). 그래서 이 저장소를 도는 **모든** OS 에서 상시로 돈다 — 종전처럼 「Windows 박스
에서 별도 검증」으로 미루면 기본이 조용히 되돌아가도 아무도 모른다.

여기서 못박는 것은 셋이다:

  ① **기본은 system 이다** — 미설정·빈값·모르는 값 전부. ⛔ 모르는 값을 「번들」로 읽으면
     오타 하나가 옛 기본으로 되돌리고, 사람은 뒤집힌 줄 알고 실험 회차를 거꾸로 읽는다.
  ② **되돌리는 스위치는 산다** — `PYTMUX_CONPTY_DLL=bundled`(별칭 `openconsole`).
     시스템 conhost 가 detached 스트리밍을 못 하는 회차가 있었으므로(2026-06-11 실측 ·
     `conpty.py` §호스트 선택 ⑵) 이 탈출구가 없으면 되돌릴 길이 재빌드뿐이다.
  ③ **판정 자리는 하나다** — 환경변수를 읽는 자리가 소스에 한 곳뿐이어야 두 술어로
     갈리지 않는다.
  ④ ☠ **그 실험을 «재는 자리»가 살아 있다** — 기본을 못박아도 계측이 안 돌면 답이 안
     난다. GHA 의 묵시적 `success()` 때문에 계측 스텝이 **조용히 skipped** 되던 것을
     2026-08-22 에 실측으로 잡았다(아래 §계측 주석). ⛔ ①②③ 이 전부 초록이어도 ④가
     무너지면 이 이슈는 영영 안 닫힌다.

덤으로 진단 함수 `doubled_wide_chars` 도 여기서 잰다(라이브 하네스 [4]가 쓴다) —
**「연속 중복 접기」라는 유혹적인 오답**을 안 저지르는지가 핵심이다
(`tests/test_wide_char_no_duplication.py` 머리말 ② 와 같은 함정이다).
"""
import os
import re

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import conpty


async def test_default_is_system_conhost():
    """① 미설정·빈값·모르는 값 → 전부 system(= 지금 기본)."""
    for raw in (None, "", "   ", "systematic", "번들", "1", "true", "bundle"):
        assert conpty.conpty_dll_pref(raw) == "system", raw
    assert conpty.CONPTY_DLL_DEFAULT == "system"


async def test_bundled_rollback_switch():
    """② `bundled`(별칭 `openconsole`)만 번들로 되돌린다 — 대소문자·공백 무관."""
    for raw in ("bundled", "BUNDLED", "  Bundled  ", "openconsole", "OpenConsole"):
        assert conpty.conpty_dll_pref(raw) == "bundled", raw
    # `system` 을 명시로 준 옛 스크립트·CI 도 그대로 돈다(이제 no-op).
    assert conpty.conpty_dll_pref("system") == "system"


async def test_env_path_matches_pure_path():
    """인자 없이 부르면 실제 환경변수를 읽는다 — 시험이 재는 것과 프로덕션이 쓰는 것이
    같은 술어인지 확인한다(순수 함수만 재고 환경 경로가 딴짓하면 이 오라클이 헛돈다)."""
    orig = os.environ.get("PYTMUX_CONPTY_DLL")
    try:
        os.environ.pop("PYTMUX_CONPTY_DLL", None)
        assert conpty.conpty_dll_pref() == "system"
        os.environ["PYTMUX_CONPTY_DLL"] = "bundled"
        assert conpty.conpty_dll_pref() == "bundled"
        os.environ["PYTMUX_CONPTY_DLL"] = "system"
        assert conpty.conpty_dll_pref() == "system"
    finally:
        if orig is None:
            os.environ.pop("PYTMUX_CONPTY_DLL", None)
        else:
            os.environ["PYTMUX_CONPTY_DLL"] = orig


async def test_env_read_in_exactly_one_place():
    """③ `PYTMUX_CONPTY_DLL` 을 읽는 자리는 소스에 **한 곳**이다.

    종전 코드는 로드 지점에서 직접 읽었다. 그 자리를 함수로 뺀 뒤에도 누군가 「여기서도
    한 번 보면 편하다」로 두 번째 독자를 심으면, 기본을 다시 뒤집는 날 한쪽만 바뀌어
    조용히 갈린다(이 저장소가 여러 번 치른 값이다)."""
    src = open(conpty.__file__, encoding="utf-8").read()
    hits = src.count('environ.get("PYTMUX_CONPTY_DLL")')
    assert hits == 1, "환경변수를 읽는 자리가 %d 곳 — 판정은 conpty_dll_pref 하나다" % hits


async def test_doubled_wide_chars_catches_report_shape():
    """제보 모양(`조직` → `조조직직`)을 잡는다 — 겹친 «폭 2» 글자만 돌려준다."""
    sent = "이 Claude는 조직 보안 정책에 의해 관리됩니다."
    seen = "이이 Claude는는 조조직직 보보안안 정정책책에 의의해해 관관리리됩됩니니다다."
    dup = conpty.doubled_wide_chars(sent, seen)
    for ch in ("조", "직", "보", "안", "는"):
        assert ch in dup, (ch, dup)
    assert "C" not in dup and " " not in dup, dup   # ASCII·공백은 폭 1 이라 대상이 아니다


async def test_doubled_wide_chars_does_not_flag_legit_doubles():
    """⛔ **원래 두 번인 우리말을 겹침으로 세지 않는다** — 이 결함의 유혹적인 오답이
    「연속 중복 접기」인데, 그 오답을 여기서 초록으로 통과시키면 표시 결함이 데이터
    결함으로 바뀐다(멀쩡한 낱말에서 글자가 말없이 사라진다)."""
    assert conpty.doubled_wide_chars("쓸쓸한 감감무소식", "쓸쓸한 감감무소식") == []
    # 보낸 글에 원래 겹침이 있으면, 받은 글에 그대로 있어도 증거가 아니다.
    assert conpty.doubled_wide_chars("하하 웃었다", "하하 웃웃었었다다") == ["웃", "었", "다"]


async def test_doubled_wide_chars_quiet_when_clean():
    """멀쩡한 왕복은 빈 목록이다(= 하네스 [4] 가 「겹침 없음」으로 찍는 자리)."""
    sent = "이 Claude는 조직 보안 정책에 의해 관리됩니다."
    assert conpty.doubled_wide_chars(sent, "C:\\> echo %s\r\n%s" % (sent, sent)) == []


# ── 「겹침 0」과 「못 쟀다」를 가르는 자리 (pytmux/pytmux-208 · 2026-08-23) ─────────
# ☠ **A/B 는 돌았고, 두 호스트를 못 갈랐다.** GHA 32578439033 의 리포트 여섯 장
# (번들·시스템 × py3.11·3.12·3.13)이 전부 `[4] 겹친 글자 0개` 였다. 그 0 은 「고쳤다」가
# 아니라 **「이 자극에서는 안 났다」**이고, `[4]` 자신의 주석이 그럴 수 있다고 미리 적어
# 뒀다 — 제보 경로는 호스트가 **자기 버퍼를 훑어 다시 뱉는** 자리인데 echo 왕복은 그
# 자리를 안 지난다.
#
# 그래서 하네스에 `[5] 리페인트 재방출`(리사이즈로 그 훑기를 강제한다)을 더했다. 그런데
# 그 자극은 **아무것도 안 돌아올 수 있다** — 그때도 겹침은 0 이다. 두 0 을 같은 말로
# 적으면 두 번째 회차도 답을 안 준다. `reemit_verdict` 가 그 둘을 가르고, 아래 셋이
# 그 가름을 잰다.
#
# ⛔ 여기서 재는 것은 **판정 함수**다 — 리사이즈 자체는 Windows 에서만 나므로 이
#    러너(macOS)가 못 잰다. 그 사실은 위 §계측 주석이 이미 지고 있다.

async def test_reemit_verdict_separates_no_reemission_from_no_overlap():
    """☠ **「재방출이 없었다」를 「겹침 없음」으로 적지 않는다.**"""
    sent = "이 Claude는 조직 보안 정책에 의해 관리됩니다."
    # 호스트가 아무것도 다시 안 뱉었다 — 겹침도 0 이지만 그것은 **못 쟀다**다.
    assert conpty.reemit_verdict(sent, "") == ("unmeasured", [])
    # 프롬프트만 돌아왔다(폭 2 글자가 한 자도 없다) — 역시 못 쟀다.
    assert conpty.reemit_verdict(sent, "C:\\Users\\me>") == ("unmeasured", [])


async def test_reemit_verdict_says_clean_only_when_the_line_actually_came_back():
    """그 줄이 **실제로 돌아왔고** 안 겹쳤을 때만 「clean」이다."""
    sent = "이 Claude는 조직 보안 정책에 의해 관리됩니다."
    assert conpty.reemit_verdict(sent, sent) == ("clean", [])


async def test_reemit_verdict_reports_the_report_shape():
    """제보 모양이 재방출에서 나오면 그 글자들과 함께 「doubled」다."""
    sent = "이 Claude는 조직 보안 정책에 의해 관리됩니다."
    seen = "이이 Claude는는 조조직직 보보안안 정정책책에 의의해해 관관리리됩됩니니다다."
    state, dup = conpty.reemit_verdict(sent, seen)
    assert state == "doubled", (state, dup)
    for ch in ("조", "직", "보", "안"):
        assert ch in dup, (ch, dup)


async def test_the_harness_uses_the_three_way_verdict_not_a_bare_count():
    """⛔ 하네스 `[5]` 가 **셋으로 가르는 그 함수**를 실제로 쓴다.

    이 줄이 없으면 다음 사람이 `[5]` 를 `doubled_wide_chars` 로 «되돌려» 놓아도
    아무도 안 운다 — 그 순간 「못 쟀다」가 다시 「겹침 없음」이 된다."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "scripts", "validate_conpty.py"),
               encoding="utf-8").read()
    mark = "# [5] **리페인트 재방출**"
    assert mark in src, "리페인트 자극 스텝이 사라졌다"
    # ⛔ **그 스텝의 «몸통»만 본다.** 파일 전체에서 이름을 세면 «주석에 적힌 이름»이
    #    걸려 되돌린 것을 초록으로 통과시킨다 — 이 시험을 지을 때 실제로 그렇게
    #    빠져나갔다(변이 B 가 13/0 으로 통과했다). 자리를 좁히는 것이 그 고침이다.
    # ⚠ 끝을 «VERDICT 라는 낱말»로 자르지 마라 — 그 스텝의 주석이 스스로 그 낱말을
    #    쓴다("이 스텝은 VERDICT 에 안 든다"). 그렇게 자르면 몸통이 통째로 빠져 이
    #    시험이 **언제나** 붉다(지을 때 실제로 그랬다). 자르는 자리는 그 줄을 찍는
    #    «호출»이다.
    tail_mark = 'report("\\nVERDICT'
    assert tail_mark in src, "VERDICT 를 찍는 자리가 사라졌다"
    body = src[src.index(mark):src.index(tail_mark)]
    assert "reemit_verdict(" in body, "[5] 가 셋으로 가르는 판정을 안 부른다"
    assert "doubled_wide_chars(" not in body, (
        "[5] 가 맨 세기로 되돌아갔다 — 「못 쟀다」가 다시 「겹침 없음」이 된다")
    # 그 스텝이 죽으면 VERDICT 줄이 통째로 안 찍힌다 — 반드시 감싸져 있어야 한다.
    assert "except Exception" in body, (
        "[5] 가 안 감싸져 있다 — 여기서 죽으면 회차가 판정 불능이 된다")


async def test_the_harness_only_calls_methods_the_owned_backend_actually_has():
    """☠ 하네스가 `pty` 에 부르는 이름이 **그 객체에 실제로 있나.**

    ⛔ 이것이 없어서 `[5]` 는 **배선된 날부터 한 번도 안 돌았다.** 실측
    (2026-08-23 · GHA 32637285906·32640590840 의 여섯 장 전부):

        [5] 리페인트 재방출(pytmux-208): 못 쟀다
            — AttributeError("'_OwnedConPty' object has no attribute 'resize'")

    `_OwnedConPty` 에 있는 것은 `set_winsize(rows, cols)` 이고 `resize` 는 없다.
    ☠ **그 자리의 오라클이 전부 「소스에 이 낱말이 있나」였다** — 그래서 있지도 않은
    메서드를 부르는 줄이 열셋 전부 초록을 지나갔다. 낱말이 아니라 **객체**에 묻는다.

    ⚠ 이 시험은 Windows 가 필요 없다 — `pty_backend` 는 어디서나 import 되고
    (클래스 «정의»는 플랫폼과 무관하다) 우리가 묻는 것은 이름의 존재뿐이다.
    ⛔ 인자 순서는 여기서 못 잰다(그 값은 Windows 실기가 진다) — 그래서 `set_winsize`
    의 서명이 `(rows, cols)` 라는 사실만 못박아, 다음 사람이 `resize(cols, rows)` 를
    그대로 옮겨 적으면 **여기서** 걸리게 한다."""
    from pytmuxlib import pty_backend

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(here, "scripts", "validate_conpty.py"),
               encoding="utf-8").read()
    # 주석·독스트링은 빼고 «부르는 줄»만 본다 — 위 시험이 값을 치르고 배운 그 규율이다.
    called = {m.group(1) for m in re.finditer(r"^\s*pty\.([A-Za-z_]\w*)\(", src, re.M)}
    assert called, "하네스가 pty 에 아무것도 안 부른다 — 자리가 바뀌었다"
    missing = sorted(n for n in called if not hasattr(pty_backend._OwnedConPty, n))
    assert not missing, (
        "하네스가 _OwnedConPty 에 없는 메서드를 부른다: %s — 그 스텝은 AttributeError 로 "
        "삼켜져 «못 쟀다» 로만 나온다(있는 것: set_winsize/write/close/…)" % missing)

    # ★ 뒤집힌 인자 순서가 이 결함의 나머지 반쪽이다 — 서명을 못박아 둔다.
    import inspect
    params = list(inspect.signature(pty_backend._OwnedConPty.set_winsize).parameters)
    assert params == ["self", "rows", "cols"], (
        "set_winsize 의 인자 순서가 바뀌었다: %s — 하네스 [5] 의 호출을 함께 고쳐라"
        % params)


# ── 이 실험을 «실제로 재는 자리» 가 살아 있나 (pytmux/pytmux-208) ────────────────
# 위 시험들은 「기본이 무엇인가」를 못박는다. 그런데 이 뒤집기는 **실험**이라 기본을
# 못박는 것만으로는 반쪽이다 — 답을 내는 것은 Windows 에서 도는 계측(`.github/workflows/
# windows.yml` 의 owned-ConPTY 스텝 둘)이고, macOS 러너는 그 자리를 못 잰다.
# ☠ **그 계측은 조용히 사라질 수 있다.** GHA 는 상태 함수가 없는 `if:` 에 `success()` 를
# 묵시적으로 AND 하므로, 앞 스텝(「Headless test suite」)이 붉으면 계측 스텝이 통째로
# **skipped** 된다 — 그리고 skipped 는 초록도 붉음도 아니라 **「안 쟀다」**다.
# 실측(2026-08-22): 최근 os-compat 회차 셋의 windows 잡 9개 전부 그렇게 건너뛰었다.
# 같은 함정에 이 저장소가 걸린 것이 두 번째다(그 스텝 주석의 「직전 12 run 전부」).
# ⇒ 사람이 리포트를 못 받는 것을 「값이 안 났다」로 읽지 않도록, 여기서 상시로 잰다.
_WORKFLOW = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(conpty.__file__))),
    ".github", "workflows", "windows.yml")


def _workflow_steps(path=_WORKFLOW):
    """windows.yml 을 «스텝 이름 → 그 블록의 줄들» 로 가른다.

    ⛔ YAML 파서를 안 쓴다 — PyYAML 은 이 저장소의 의존성이 아니고(requirements.txt),
    계측 한 줄을 지키자고 시험에 런타임 의존을 늘리면 그 시험이 먼저 못 돈다.
    스텝은 들여쓰기 6칸의 `- name:` 하나뿐이라 이 정도로 갈린다."""
    steps, cur = [], None
    for line in open(path, encoding="utf-8").read().splitlines():
        m = re.match(r"^ {6}- name: (.*)$", line)
        if m:
            cur = (m.group(1).strip(), [])
            steps.append(cur)
        elif cur is not None and line.strip() and not line.startswith(" " * 8):
            cur = None          # 스텝 블록이 끝났다(잡 레벨로 돌아왔다)
        elif cur is not None:
            cur[1].append(line)
    return steps


async def test_pytmux208_measurement_steps_run_even_when_the_suite_is_red():
    """계측 스텝 둘의 `if:` 에 **상태 함수**가 있다 — 없으면 묵시적 `success()` 다.

    ⛔ 「앞이 초록일 때만 잰다」는 이 실험에서 정확히 거꾸로다: 뒤집기의 가장 큰 위험이
    「시스템 conhost 가 콘솔-less 에서 스트리밍을 못 한다」인데, 그 위험이 터진 회차가
    바로 앞 스텝까지 붉은 회차다. 그때 계측이 안 돌면 **실험이 실패한 회차에서만 값이
    사라진다.**"""
    steps = _workflow_steps()
    assert steps, "windows.yml 에서 스텝을 한 개도 못 갈랐다 — %s" % _WORKFLOW
    names = [n for n, _ in steps]
    measured = [(n, b) for n, b in steps if n.startswith("owned-ConPTY")]
    assert len(measured) == 2, "계측 스텝이 %d 개 — 둘(기본 · A/B)이어야 한다: %s" % (
        len(measured), [n for n, _ in measured])

    # 앞에 붉을 수 있는 스텝이 실제로 «있다» — 이 시험이 가상의 위험을 재는 것이 아니다.
    suite = [i for i, n in enumerate(names) if n == "Headless test suite"]
    assert suite, names
    assert suite[0] < names.index(measured[0][0]), "계측이 스위트보다 앞이면 이 시험은 무의미하다"

    for name, block in measured:
        ifs = [ln.strip() for ln in block if ln.strip().startswith("if:")]
        assert len(ifs) == 1, (name, ifs)
        assert "cancelled()" in ifs[0] or "always()" in ifs[0], (
            "%r 의 if 에 상태 함수가 없다(= 묵시적 success · 앞이 붉으면 skipped): %s"
            % (name, ifs[0]))


async def test_pytmux208_ab_reports_do_not_overwrite_each_other():
    """A/B 두 회차가 **서로 다른 TEMP** 에 리포트를 쓴다.

    `validate_conpty.py` 의 리포트 경로가 `%TEMP%\\validate_conpty.out` 고정이라, TEMP 를
    안 가르면 뒤 회차가 앞 회차를 덮어 A/B 가 **한쪽만** 남는다 — 그러면 남는 것은
    「어느 호스트에서 잰 것인지 모르는 리포트 한 장」이고, 이 실험이 가르려는 「호스트
    안/밖」이 바로 그 정보다. 그리고 그 덮어쓰기는 **아무 오류도 안 낸다.**"""
    temps = []
    for name, block in _workflow_steps():
        if not name.startswith("owned-ConPTY"):
            continue
        got = [ln.strip() for ln in block if ln.strip().startswith("TEMP:")]
        assert len(got) == 1, (name, got)
        temps.append(got[0].split("#")[0].strip())
    assert len(temps) == 2 and temps[0] != temps[1], temps

    # A/B 쪽은 advisory 여야 한다 — 「지금 기본이 아닌 경로」가 빨갛다고 제품이 깨진 것이
    # 아니다. 이것이 무너지면 되돌리는 스위치의 회귀가 제품 게이트로 새어 들어온다.
    ab = [b for n, b in _workflow_steps() if n.startswith("owned-ConPTY") and "A/B" in n]
    assert len(ab) == 1, "A/B 스텝을 못 찾았다"
    assert any(ln.strip() == "continue-on-error: true" for ln in ab[0]), \
        "A/B 스텝이 advisory 가 아니다"
