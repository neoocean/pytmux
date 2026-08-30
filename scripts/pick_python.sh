# 셸 게이트가 쓸 **진짜** 파이썬 3 을 고른다 — `source` 해서 쓰는 함수 한 벌.
#
# ⛔ **`command -v python3` 하나로 판정하지 않는다.** Windows 에서 그 이름은
#    `…\WindowsApps\python3`(Store 앱 실행 별칭 = 설치 런처)일 수 있는데, `command -v`
#    는 **그것을 찾아낸다** — 즉 "있나"는 통과하고 "도나"는 아무도 안 묻는다. 그 별칭은
#    스크립트를 안 돌리고 `Python` 한 줄만 찍는다(2026-08-23 실측, 이 상자: rc 49).
#
#    ☠ 이번에 물린 방향은 «거짓 빨강»이었지만 **반대 방향이 더 나쁘다** — 같은 별칭이
#    **출력 없이 rc 0** 으로 끝나는 판이 있고, 그때 부른 쪽은 아무것도 안 재고 초록이
#    된다. `check_licenses.sh` 가 재는 것은 재배포 의무(고지 재현 · pytmux-193)이고
#    `build_release.sh` 는 그것을 **굽는 자리**라, 조용한 초록은 고지를 안 재고 이진을
#    내보내는 것이 된다(pytmux-383).
#
#    그래서 후보를 차례로 짚되 **진짜 파이썬 3 인지를 물어서** 고른다. 별칭은 그 물음에
#    답을 못 하므로 여기서 걸러진다.
#
# 왜 파일 하나인가: 같은 줄(`PY="${PYTHON:-python3}"`)이 **세 자리**에 있었다 —
# `client/scripts/check_licenses.sh` · `client/scripts/build_release.sh` ·
# `.githooks/pre-push`. 한 자리만 고치면 나머지 둘이 조용히 옛 답을 낸다(그리고 실제로
# pre-push 만 먼저 고쳐져 갈려 있었다). 한 질문은 한 술어가 답한다.
#
# 부르는 쪽: `. "<저장소뿌리>/scripts/pick_python.sh"` 뒤에
#
#     PY=$(pick_python) || { echo "…를 못 쟀다" >&2; exit 2; }
#
# ⛔ **못 찾으면 «못 찾았다»로 끝난다** — 이 파일은 메시지를 안 찍고 rc 만 준다.
#    무엇을 못 쟀는지는 부르는 쪽이 안다(그쪽이 사람에게 쓸모 있는 문장을 낸다).
#
# ⚠ POSIX sh 로 적는다 — 부르는 쪽 셋 중 둘이 `#!/bin/sh` 다(bash 에서도 그대로 돈다).
#   `set -u` 아래에서 source 되므로 안 세워진 변수는 전부 `${X:-}` 로 편다.

# 이 후보가 진짜 파이썬 3 인가. 별칭·런처·파이썬 2 는 여기서 떨어진다.
_pick_python_is_py3() {
    command -v "$1" >/dev/null 2>&1 || return 1
    [ "$("$1" -c 'import sys; print(sys.version_info[0])' 2>/dev/null)" = "3" ]
}

pick_python() {
    # ⑴ 사람이 지목한 것이 먼저다. `PYTMUX_PYTHON` 은 훅이 쓰던 이름이고 `PYTHON` 은
    #    셸 게이트·`build_release.ps1` 이 쓰던 이름이라 **둘 다** 받는다.
    #    ★ 합본 게이트(`scripts/check_all.py::child_env` ⑷)가 `PYTHON` 에 자기
    #      `sys.executable` 을 실어 주므로, 게이트를 지나 온 자식은 여기서 바로 끝난다 —
    #      「게이트가 도는 인터프리터」와 「셸 게이트가 쓰는 인터프리터」가 같아진다.
    for _pp_cand in "${PYTMUX_PYTHON:-}" "${PYTHON:-}"; do
        [ -n "$_pp_cand" ] || continue
        if _pick_python_is_py3 "$_pp_cand"; then
            printf '%s\n' "$_pp_cand"
            return 0
        fi
        # ⛔ 조용히 다음으로 넘어가지 않는다 — 지목한 사람은 그것이 쓰인 줄 안다.
        echo "pick_python: 지정한 파이썬($_pp_cand)이 파이썬 3 으로 안 돈다 — 다음 후보를 본다" >&2
    done

    # ⑵ 흔한 이름 차례. `python3` 을 먼저 두는 것은 유닉스 관례고, Windows 에서 그것이
    #    별칭이면 위 물음에서 떨어져 `python` 으로 내려간다 — 기본값을 OS 마다 다르게
    #    적을 필요가 없어진다(종전에 `.sh` 는 `python3`, `.ps1` 은 `python` 이었다).
    for _pp_cand in python3 python py; do
        if _pick_python_is_py3 "$_pp_cand"; then
            printf '%s\n' "$_pp_cand"
            return 0
        fi
    done
    return 1
}
