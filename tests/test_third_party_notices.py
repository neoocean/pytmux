"""서드파티 저작권 고지 게이트(`client/scripts/third_party_notices.py`)의 판정 부분.

왜 테스트하나: 이 게이트가 지키는 것은 **되돌릴 수 없는 방향**이다 — 고지 없는 이진이
공개 미러에 한 번 올라가면 히스토리에서 빼는 길이 없고(`build/` 는 개정마다 새 blob),
카피레프트 크레이트가 조용히 들어오면 그것을 알아차리는 자리가 이 판정뿐이다. 그런데
게이트는 **자기가 안 무는 것을 모른다**: 초록만 보고는 「지킨다」와 「아무것도 안 잰다」가
구분되지 않는다(이 저장소가 `check_mirror.py` 확장자 사각지대로 값을 치른 자리다).

⛔ **cargo 를 안 부른다.** 여기서 재는 것은 판정 로직과 소스 오라클이고, 의존 그래프를
실제로 펴는 것은 커밋 게이트(`check_licenses.sh`)가 한다 — 스위트가 네트워크·레지스트리
상태에 매달리면 그 붉음은 결함이 아니라 날씨가 된다.
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CLIENT_SCRIPTS = os.path.join(ROOT, "client", "scripts")

_spec = importlib.util.spec_from_file_location(
    "third_party_notices", os.path.join(CLIENT_SCRIPTS, "third_party_notices.py"))
tpn = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tpn)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ── 라이선스 식 판정 ─────────────────────────────────────────────────────────

async def test_choose_takes_the_permissive_side_of_a_dual():
    """듀얼은 **우리가 고른다**. `GPL-3.0+ OR BSD-3-Clause` 는 BSD 로 받는다(실측 1건)."""
    picked, bad = tpn.choose("GPL-3.0+ OR BSD-3-Clause")
    assert not bad, bad
    assert picked == ["BSD-3-Clause"], picked
    # 옛 표기(`/`)도 OR 다 — cargo 가 오래 허용해 왔고 이 그래프에 실제로 33건 있다.
    assert tpn.choose("MIT/Apache-2.0")[0] == ["MIT"]
    assert tpn.choose("Apache-2.0 / MIT")[0] == ["MIT"]


async def test_choose_rejects_a_copyleft_only_crate():
    """**이 게이트의 존재 이유.** 단독 GPL·AGPL 은 고를 것이 없으니 적색이다.

    종전에는 이 자리에 *"외부 의존은 전부 허용적인 것들"* 이라는 주석 한 줄이 있었고,
    그 문장은 새 의존이 들어와도 스스로 갱신되지 않았다.
    """
    for expr in ("GPL-3.0", "AGPL-3.0-only", "GPL-2.0-only AND MIT"):
        picked, bad = tpn.choose(expr)
        assert bad, f"{expr} 가 통과했다 — 게이트가 안 문다"
        assert not picked, picked


async def test_choose_respects_and_binding_tighter_than_or():
    """`A AND B OR C` 는 `(A AND B) OR C` 다(SPDX 규격).

    왼쪽부터 평평하게 접으면 `MIT AND (GPL OR Apache-2.0)` 로 읽혀 **못 지키는 조각을
    지킬 수 있는 것처럼** 만든다 — 라이선스 판정에서 그건 조용히 틀리는 쪽이다.
    """
    picked, bad = tpn.choose("MIT AND GPL-3.0 OR Apache-2.0")
    assert not bad, bad
    assert picked == ["Apache-2.0"], picked
    # AND 는 양쪽을 다 지킨다 — 실측으로 이 모양이 하나 있다(`icu_*`).
    picked, bad = tpn.choose("(MIT OR Apache-2.0) AND Unicode-DFS-2016")
    assert not bad, bad
    assert picked == ["MIT", "Unicode-DFS-2016"], picked


async def test_allowed_list_carries_the_obligation():
    """허용 목록의 값은 **의무**다 — 빈 문자열이면 왜 허용인지가 사라진다.

    고지 파일의 요약표가 이 문장을 그대로 싣는다(읽는 사람이 보는 유일한 근거다).
    """
    empty = [k for k, v in tpn.ALLOWED.items() if not (v or "").strip()]
    assert not empty, empty


# ── 출처 판정 ────────────────────────────────────────────────────────────────

async def test_origin_separates_git_from_registry():
    """이름+버전은 열쇠가 못 된다 — git rev 로 고정한 크레이트와 crates.io 의 **같은
    이름·버전**이 그래프에 동시에 있다(실측: `core-graphics-types v0.2.0`).

    둘을 한 열쇠로 접으면 한쪽의 전문으로 다른 쪽을 고지하게 된다.
    """
    assert tpn._origin("")[0] == "registry"
    assert tpn._origin(" (proc-macro)")[0] == "registry"     # 출처가 아니라 종류다
    kind, where = tpn._origin(" (https://github.com/servo/core-foundation-rs?rev=abc#abc)")
    assert kind == "git" and where.startswith("https://"), (kind, where)
    assert tpn._origin(" (/somewhere/crates/gui)")[0] == "path"


# ── 고지 파일 읽기 ───────────────────────────────────────────────────────────

_SAMPLE = """\
## 요약

| 라이선스 | 크레이트 | 의무 |
|---|---:|---|
| `MIT` | 394 | 고지 재현 |

## 2. 서드파티 크레이트 (2)

| 크레이트 | 버전 | 출처 | 선언 SPDX | 고른 것 | 전문 |
|---|---|---|---|---|---|
| `serde` | 1.0.228 | `crates.io` | `MIT OR Apache-2.0` | `MIT` | [L001](#l001) |
| `core-graphics-types` | 0.2.0 | `https://example.invalid/x?rev=abc#abc` | `MIT` | `MIT` | [L002](#l002) |

## 3. 라이선스 전문 (2)

| `이건` | 표가 | `아니다` |
"""


async def test_listed_crates_reads_only_the_crate_table():
    """요약의 분포표도 「`MIT` | 394」 모양이다 — 문서 전체에서 긁으면 `MIT v394` 같은
    유령이 목록에 섞이고, 그러면 `--covers` 가 「덮는다」고 답하는 근거가 오염된다."""
    got = tpn.listed_crates(_SAMPLE)
    assert got == {
        "serde v1.0.228",
        "core-graphics-types v0.2.0 (https://example.invalid/x?rev=abc#abc)",
    }, got


async def test_listed_crates_notices_a_dropped_row():
    """한 줄이 빠지면 그 크레이트는 「고지에 없다」로 읽힌다 — `--covers` 가 무는 자리."""
    trimmed = "\n".join(l for l in _SAMPLE.splitlines() if "serde" not in l)
    assert "serde v1.0.228" not in tpn.listed_crates(trimmed)


# ── 전문 고르기 ──────────────────────────────────────────────────────────────

async def test_rejected_side_of_a_dual_is_not_shipped():
    """우리가 BSD 로 받은 크레이트의 **GPL 전문은 안 싣는다**.

    싣는 순간 그 35KB 는 「우리가 GPL 로 배포한다」는 거짓 신호가 된다(실측:
    `bounded-vec-deque` 가 GPL 전문을 함께 싣는다). 이름이 말하지 않는 파일(그냥
    `LICENSE`)은 안 거른다 — 짐작으로 지우면 진짜 고지가 사라진다.
    """
    files = [("LICENSE", "둘 중 하나를 고르라"), ("LICENSE-BSD", "BSD 전문"),
             ("LICENSE-GPL", "GPL 전문")]
    kept = [n for n, _ in tpn.for_picked(files, ["BSD-3-Clause"])]
    assert kept == ["LICENSE", "LICENSE-BSD"], kept


async def test_filter_never_empties_the_notice():
    """전부 걸러지면 원본을 그대로 돌려준다 — 짐작이 고지를 통째로 지우게 두지 않는다."""
    files = [("LICENSE-APACHE", "아파치 전문")]
    assert tpn.for_picked(files, ["MIT"]) == files


# ── 이진에 박히는 자산 ───────────────────────────────────────────────────────

async def test_embedded_asset_scan_actually_finds_the_font():
    """신고 목록을 비우면 **진짜 자산**이 잡혀야 한다.

    스캔이 아무것도 못 보고 있어도 「신고 안 된 것 0건」은 초록이다 — 그 둘을 가르려면
    한 번은 되돌려서 재야 한다(뮤테이션). 지금 이진에 박히는 서드파티 자산은 Roboto 다.
    """
    real = tpn.BUNDLED
    try:
        tpn.BUNDLED = []
        found = {os.path.basename(arg) for _src, arg in tpn.undeclared_assets()}
    finally:
        tpn.BUNDLED = real
    assert "Roboto-Regular.ttf" in found, found


async def test_declared_assets_leave_nothing_unreported():
    """지금 트리에는 신고 안 된 `include_bytes!` 자산이 없다(있으면 고지가 모자란다)."""
    assert tpn.undeclared_assets() == []


async def test_declared_asset_license_files_exist():
    """신고한 자산의 라이선스 파일이 실제로 있어야 한다 — 없으면 고지의 근거가 없다."""
    for item in tpn.BUNDLED:
        path = os.path.join(ROOT, "client", item["license_file"])
        assert os.path.isfile(path), path
        assert tpn.text_of(path), path


# ── 부르는 자리(소스 오라클) ─────────────────────────────────────────────────

async def test_both_build_scripts_ship_the_notice():
    """**두 OS 가 갈리지 않는가.** 한쪽만 고지를 놓으면 그 플랫폼 이진만 고지 없이 나간다 —
    이 저장소는 정확히 그 모양으로 물린 적이 있다(`.exe` 만 유출 스캔을 빠져나갔다).
    """
    for name in ("build_release.sh", "build_release.ps1"):
        body = _read(os.path.join(CLIENT_SCRIPTS, name))
        assert "third_party_notices.py" in body, f"{name}: 굽기 전 고지 검사가 없다"
        assert "--covers" in body, f"{name}: `--covers` 로 재지 않는다"
        assert "build/THIRD-PARTY-NOTICES.md" in body or \
               "build\\THIRD-PARTY-NOTICES.md" in body, f"{name}: 이진 옆에 안 놓는다"


async def test_target_list_keeps_up_with_the_platforms_we_bake():
    """**래칫.** 굽는 스크립트가 이름을 주는 플랫폼 수와 고지가 담는 트리플 수가 같은가.

    ⛔ 여기서 uname 짝을 트리플로 옮기는 표를 만들지 않는다 — 그 표가 곧 두 번째 술어가
    되고, 갈리는 날 조용한 쪽이 믿긴다. 재는 것은 **수**뿐이고, 진짜 판정은 굽는 자리의
    `--covers` 가 그 상자의 실제 트리플로 한다(그것은 짐작이 아니라 `cargo -vV` 다).
    플랫폼을 늘렸으면 `TARGETS` 도 늘리고 이 수를 함께 옮긴다.
    """
    sh = _read(os.path.join(CLIENT_SCRIPTS, "build_release.sh"))
    arms = [l for l in sh.splitlines() if l.strip().startswith(("Darwin/", "Linux/"))]
    windows = 1                      # build_release.ps1 — 크로스 컴파일이 안 돼 따로 산다
    assert len(arms) + windows == len(tpn.TARGETS), (
        f"굽는 플랫폼 {len(arms) + windows} ≠ 고지 트리플 {len(tpn.TARGETS)} — "
        "플랫폼이 늘었는데 고지가 안 늘었거나, 그 반대다")


async def test_commit_gate_calls_the_check():
    """커밋 게이트가 이 판정을 실제로 부르나 — 안 부르면 낡은 고지가 조용히 산다."""
    body = _read(os.path.join(CLIENT_SCRIPTS, "check_licenses.sh"))
    assert "third_party_notices.py --check" in body


async def test_notice_file_is_present_and_generated():
    """고지 파일이 트리에 있고 **손으로 쓴 글이 아니라고** 스스로 말하나."""
    body = _read(os.path.join(ROOT, "client", "THIRD-PARTY-NOTICES.md"))
    assert "손으로 고치지 않는다" in body
    assert tpn.listed_crates(body), "크레이트 표가 비었다 — 고지가 아무것도 안 담고 있다"


# ── 안 풀린 심링크(pytmux-416) ───────────────────────────────────────────────
#
# Windows 에서 심링크 권한이 없으면 git 은 심링크를 **대상 경로 한 줄이 든 보통 파일**로
# 푼다. 그 한 줄이 「MIT 전문」 자리에 실리면 **법적 고지가 고지를 안 한다.** 게다가
# POSIX 에서 구우면 전문이 들어가 상자마다 다른 파일이 구워진다(재발 엔진).


def _crate(tmp, name, body, sibling=None):
    """크레이트 디렉터리 하나를 짓는다 → 그 안의 `name` 절대경로."""
    pkg = os.path.join(tmp, "pkg")
    os.makedirs(pkg, exist_ok=True)
    if sibling is not None:
        with open(os.path.join(tmp, sibling[0]), "w", encoding="utf-8") as fh:
            fh.write(sibling[1])
    path = os.path.join(pkg, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return path


async def test_an_unresolved_symlink_is_followed():
    """대상이 있으면 **전문**을 싣는다 — 그래야 POSIX 와 Windows 가 같은 파일을 굽는다."""
    import tempfile
    real = "Copyright (c) 2012-2013 Mozilla Foundation\n\nPermission is hereby granted"
    with tempfile.TemporaryDirectory() as tmp:
        path = _crate(tmp, "LICENSE-MIT", "../LICENSE-MIT",
                      sibling=("LICENSE-MIT", real))
        assert tpn.text_of(path) == real, "심링크를 안 따라갔다 — 껍데기가 실린다"


async def test_a_broken_symlink_neither_invents_nor_ships_the_shell():
    """대상이 없으면(read-fonts 0.22.7) ⛔ 지어내지도, 껍데기를 싣지도 않는다."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = _crate(tmp, "LICENSE-MIT", "../LICENSE-MIT")
        got = tpn.text_of(path)
        assert got != "../LICENSE-MIT", "껍데기가 전문인 척 실렸다(416 의 원래 증상)"
        assert "Permission is hereby granted" not in got, (
            "표준 문안을 지어 붙였다 — MIT 는 저작권 줄이 본문의 일부라 "
            "그렇게 하면 저작권자를 발명하는 것이 된다")
        assert "../LICENSE-MIT" in got, "무슨 일이 있었는지를 안 적었다"


async def test_a_real_one_line_notice_is_not_mistaken_for_a_link():
    """⛔ 대조군 — 한 줄짜리 **진짜 고지**를 심링크로 오독하면 그 고지가 사라진다.

    「공백이 없다」만으로 가르면 무는 함정이다. 판정은 **마지막 조각이 라이선스 파일
    이름인가**까지 본다.
    """
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        for body in ("This software is released into the public domain.",
                     "Unlicense", "MIT"):
            path = _crate(tmp, "LICENSE", body)
            assert tpn.text_of(path) == body, f"한 줄짜리 고지를 잃었다: {body!r}"


async def test_the_shipped_notice_carries_no_link_shell():
    """게시된 고지에 **경로 한 줄짜리 전문**이 하나도 없나(pytmux-416 의 오라클)."""
    body = _read(os.path.join(ROOT, "client", "THIRD-PARTY-NOTICES.md"))
    shells = [l for l in body.splitlines() if l.strip().startswith("../LICEN")]
    assert not shells, f"전문 자리에 껍데기가 남았다: {shells}"
