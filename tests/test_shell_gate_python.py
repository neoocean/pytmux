"""셸 게이트가 파이썬을 «있나»가 아니라 «도나»로 고르나(pytmux/pytmux-383).

# 무엇이 잘못돼 있었나

셸 게이트 둘(`client/scripts/check_licenses.sh`·`build_release.sh`)은 파이썬을 **자기가
다시 찾는다** — 합본 게이트가 `PYTHON` 을 안 넘겼기 때문이다(그쪽은 `child_env` ⑷ 가
고쳤고 `test_config_hygiene` 이 잰다). 그리고 찾는 방법이 `command -v "$PY"` 하나였다.

Windows 에서 `python3` 은 Store 앱 실행 별칭(`…\\WindowsApps\\python3`)일 수 있는데
**`command -v` 는 그것을 찾아낸다.** 즉 스크립트에 적힌 *"못 쟀으면 통과가 아니다"* 는
그 자리에서 무력화됐다 — 이 상자에서는 별칭이 rc 49 로 죽어 **거짓 빨강**이었지만,
같은 별칭이 **출력 없이 rc 0** 으로 끝나는 판에서는 **조용한 초록**이 된다. 그 스텝이
재는 것이 재배포 고지(pytmux-193)라 그 초록은 「고지를 안 재고 이진을 내보냈다」다.

# 여기서 재는 것

⑴ 정본 술어(`scripts/pick_python.sh::pick_python`)가 **별칭을 실제로 가른다** —
   대조군으로 «옛 관문»(`command -v`)이 그 별칭을 통과시키는 것을 먼저 보인다.
   그 대조군이 없으면 이 시험은 아무 일도 안 하는 코드를 통과시킨다.
⑵ **부르는 자리 셋이 그 술어를 쓴다**(`check_licenses.sh`·`build_release.sh`·
   `.githooks/pre-push`). 술어만 재고 호출부를 안 재면, 한 자리가 옛 줄로 돌아가도
   이 파일은 초록이다 — 이 저장소가 말하는 «공허 통과»다.
⑶ **PowerShell 짝도 같은 차례를 본다.** `build_release.ps1` 은 sh 를 source 할 수
   없어 모양만 옮겨 적는데, 종전에는 기본값까지 갈려 있었다(`.sh` 는 `python3`,
   `.ps1` 은 `python`) — 같은 일을 하는 두 짝이 서로 다른 자를 집고 있었다.

되돌리면 실패해야 하는 것:
  · `pick_python` 의 `-c` 물음을 `command -v` 하나로 되돌리면 → ⑴ 실패
  · 셋 중 아무 자리나 `PY="${PYTHON:-python3}"` 로 되돌리면 → ⑵ 실패
  · `build_release.ps1` 을 `if ($env:PYTHON) {…} else { "python" }` 로 되돌리면 → ⑶ 실패
"""
import io
import os
import re
import subprocess
import sys
import tempfile

import harness  # noqa: F401  (경로 설정)
from run import skip

# 합본 게이트는 `scripts/` 에 있다(하니스는 저장소 뿌리와 `tests/` 만 경로에 넣는다).
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import check_all  # noqa: E402

PICKER = os.path.join(ROOT, "scripts", "pick_python.sh")


def _read(rel):
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _shell():
    """셸 게이트가 실제로 쓰는 bash. 없으면 이 파일은 잴 것이 없다.

    ⛔ `shutil.which("bash")` 를 쓰지 않는다 — Windows 에서 그것이 집는 것이 바로 이
    시험이 다루는 부류의 별칭(WSL 런처)이다. 게이트와 **같은 술어**로 찾는다.
    """
    return check_all.find_bash()


def _fake_bin(root, *, stub="python3", real="python"):
    """«Store 앱 별칭»과 «진짜 파이썬»만 든 PATH 디렉터리를 짓는다 → 경로.

    별칭 흉내는 실측 그대로다 — `Python` 한 줄을 찍고 rc 49 로 죽는다(2026-08-23).
    ⚠ 이 상자의 진짜 `python3` 를 쓰면 대조군이 성립하지 않는다(그것은 잘 돈다).
    """
    os.makedirs(root, exist_ok=True)
    if stub:
        path = os.path.join(root, stub)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("#!/bin/sh\necho Python\nexit 49\n")
        os.chmod(path, 0o755)
    if real:
        path = os.path.join(root, real)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('#!/bin/sh\nexec "%s" "$@"\n' % sys.executable)
        os.chmod(path, 0o755)
    return root


def _run(argv, **kw):
    """자식을 돌린다 — **UTF-8 로 읽으면서**.

    ⛔ `text=True` 만 주면 파이썬은 **로캘 인코딩**으로 푼다. 이 저장소의 셸 스크립트는
    stderr 에 한국어를 UTF-8 로 쓰므로, 한국어 Windows(ACP=cp949)에서는 읽기 스레드가
    `UnicodeDecodeError` 로 죽고 **`proc.stderr` 가 `None` 이 된다**(예외는 그 스레드
    안에서 끝나 부르는 쪽엔 안 온다). 그러면 여기 단언은 「없다」가 아니라
    `argument of type 'NoneType' is not iterable` 로 넘어진다 — 무엇이 틀렸는지
    가리키지 않는 실패다(pytmux-398).
    """
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def _pick(bash, bindir, **env_over):
    """그 PATH 에서 `pick_python` 을 돌린다 → (rc, 고른 이름, stderr)."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHON", "PYTMUX_PYTHON")}
    env["PATH"] = bindir
    env.update({k: v for k, v in env_over.items() if v is not None})
    proc = _run([bash, "-c", '. "$1"; pick_python', "sh", PICKER],
                env=env, cwd=ROOT)
    return proc.returncode, proc.stdout.strip(), proc.stderr


async def test_the_picker_tells_a_store_alias_from_a_real_python():
    """⑴ 별칭을 가른다 — 그리고 옛 관문이 그것을 통과시키던 것을 대조군으로 보인다."""
    bash = _shell()
    if not bash:
        skip("쓸 만한 bash 가 없다 — 셸 게이트 자체가 안 도는 상자다")

    with tempfile.TemporaryDirectory() as root:
        bindir = _fake_bin(os.path.join(root, "bin"))

        # 대조군 ⓐ — 옛 관문(`command -v python3`)은 이 별칭을 **통과시킨다**.
        rc, out, _ = _pick(bash, bindir)          # env 만 같게, 술어는 아래에서 따로
        old_gate = _run([bash, "-c", 'command -v python3'], cwd=ROOT,
                        env=dict(os.environ, PATH=bindir))
        assert old_gate.returncode == 0, (
            "별칭 흉내가 PATH 에 안 섰다 — 이 시험이 재려던 구멍이 이제 없다")

        # 대조군 ⓑ — 그런데 그것은 **안 돈다**(스크립트를 안 돌리고 낱말 하나를 찍는다).
        # ⛔ 스텁을 **직접** 부르지 않는다 — 그것은 `#!/bin/sh` 스크립트라 Windows 가
        #    `CreateProcess` 로 못 띄운다(`WinError 193`, pytmux-399). 그리고 직접 부르는
        #    것은 애초에 재려는 모양도 아니다: 정본 술어는 **bash 안에서** `"$1" -c …` 로
        #    부르므로, 대조군도 그 호출 모양 그대로 물어야 같은 것을 잰 것이 된다.
        alias = _run([bash, "-c", '"$1" -c "print(1)"', "sh",
                      os.path.join(bindir, "python3")])
        assert alias.returncode != 0 and alias.stdout.strip() == "Python", (
            alias.returncode, alias.stdout)

        # 본시험 — 정본 술어는 그것을 건너뛰고 진짜를 고른다.
        assert rc == 0, (rc, out)
        assert out == "python", out

    # 그리고 «아무것도 안 도는» 상자에서는 못 찾았다고 말한다 — 조용히 첫 후보를
    # 돌려주면 부르는 쪽의 "못 쟀으면 통과가 아니다" 가 다시 무력해진다.
    with tempfile.TemporaryDirectory() as root:
        only_stub = _fake_bin(os.path.join(root, "bin"), real=None)
        rc, out, _ = _pick(bash, only_stub)
        assert rc != 0, (rc, out)
        assert out == "", out


async def test_the_picker_does_not_silently_swallow_an_explicit_choice():
    """지목한 것이 안 돌면 **다음 후보로 가되 말은 한다**(조용한 대체가 가장 나쁘다)."""
    bash = _shell()
    if not bash:
        skip("쓸 만한 bash 가 없다 — 셸 게이트 자체가 안 도는 상자다")

    with tempfile.TemporaryDirectory() as root:
        bindir = _fake_bin(os.path.join(root, "bin"))
        stub = os.path.join(bindir, "python3")
        real = os.path.join(bindir, "python")

        # 지목한 것이 도는 경우 — 그것을 그대로 쓴다(게이트가 넘기는 절대경로가 이 갈래다).
        rc, out, _ = _pick(bash, bindir, PYTHON=real)
        assert (rc, out) == (0, real), (rc, out)
        rc, out, _ = _pick(bash, bindir, PYTMUX_PYTHON=real)
        assert (rc, out) == (0, real), (rc, out)

        # 지목한 것이 별칭인 경우 — 대체하되 **stderr 에 그 사실을 남긴다**.
        rc, out, err = _pick(bash, bindir, PYTHON=stub)
        assert (rc, out) == (0, "python"), (rc, out)
        assert stub in err, err


async def test_every_call_site_asks_the_one_predicate():
    """⑵ 부르는 자리 셋이 정본 술어를 쓴다 — 같은 줄이 세 자리에 있던 것이 이 결함이다."""
    assert os.path.isfile(PICKER), PICKER
    for rel in ("client/scripts/check_licenses.sh",
                "client/scripts/build_release.sh",
                ".githooks/pre-push"):
        body = _read(rel)
        assert "pick_python.sh" in body, (rel, "정본을 안 연다")
        assert "PY=$(pick_python)" in body, (rel, "정본 술어를 안 쓴다")
        assert "${PYTHON:-python3}" not in body, (
            rel, '옛 줄이 돌아왔다 — `command -v` 는 Store 앱 별칭을 못 가른다')
        # 제 손으로 다시 적지 않는다(두 벌이 되면 갈리고, 갈리면 조용한 쪽이 믿긴다).
        assert body.count("version_info[0]") == 0, (rel, "고르는 법을 여기 또 적었다")


async def test_the_powershell_twin_probes_too():
    """⑶ `.ps1` 도 «도나»로 고르고, `.sh` 와 **같은 차례**를 본다."""
    body = _read("client/scripts/build_release.ps1")
    assert 'else { "python" }' not in body, (
        "기본값 하나로 돌아갔다 — 별칭을 못 가르고, .sh 와도 갈린다")
    assert "version_info[0]" in body, "도는지 안 물어본다"
    # 차례는 «후보 목록 한 줄»에서 읽는다 — 파일 전체에서 이름을 세면 `python3` 이
    # `python` 을 품고 있어 무엇을 적어도 통과한다(공허 통과).
    line = [ln for ln in body.splitlines() if "foreach ($cand in" in ln]
    assert len(line) == 1, line
    order = re.findall(r'"(python3|python|py)"', line[0])
    assert order == ["python3", "python", "py"], (order, "sh 짝과 차례가 갈렸다")
