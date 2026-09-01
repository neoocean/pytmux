#!/usr/bin/env python3
"""게시 게이트 — p4↔git 미러 드리프트 + CL 오염(남의 파일) 점검.

이 저장소는 **p4 submit 과 git push 양쪽**에 게시한다(CLAUDE.md '게시'). 자동 미러가
없어 한쪽만 하면 다른 쪽이 영영 뒤처진다 — 실사고 3건(p4 63471·63714 캐치업, 그리고
이 스크립트를 만들며 발견한 `.gitignore` 의 `.env` 규칙이 git 에만 있던 것). 로드맵
§5.1 의 처방 (a)"체크리스트+`git ls-remote` 대조"를 사람이 기억하지 않아도 되게
**실행 가능한 게이트**로 만든 것이 이 파일이다.

    python3 scripts/publish_check.py            # 미러 드리프트 점검
    python3 scripts/publish_check.py --cl 66940 # 그 CL 이 내 파일만 담았는지(부정 게이트)

드리프트 판정 원리(값싼 명령 두 개로 양방향):
  · `p4 diff -se` = 열지 않았는데 depot 과 **내용이 다른** 파일.
  · `git status --porcelain` = git HEAD 와 다른 파일(ignore 된 것은 애초에 안 나온다).
  두 집합의 조합이 방향을 말해준다:
    depot≠workspace 인데 git 은 clean  → git 에만 있는 내용 = **p4 미제출**
    depot==workspace 인데 git 은 modified → p4 에만 있는 내용 = **git 미푸시**
    양쪽 다 다름 → 그냥 작업 중(WIP, 게시 전)이라 드리프트 아님
종료코드 = 0(깨끗)/1(드리프트 또는 오염)/**2(기준선이 낡아 못 쟀다)**. 게시 직전 훅으로 쓸 수 있다.

## ⛔ 재기 전에 **기준선이 신선한지** 먼저 잰다 (pytmux/pytmux-153)

이 게이트는 두 개의 기준선 위에서 판정한다 — git 쪽은 **로컬 HEAD**, p4 쪽은 **워크스페이스의
have 리비전**. 둘 중 하나라도 낡으면 나오는 숫자는 드리프트가 아니라 **낡음의 그림자**다.

- ☠ **거짓 초록**(2026-08-08): `run()` 이 로케일로 디코드해 한국어 커밋 메시지에서 죽었고,
  미푸시가 0 일 때만 초록이 나왔다 — 그 초록은 「드리프트 없음」이 아니라 「잰 것이 없음」이었다.
- ☠ **거짓 붉음**(2026-08-10 실측): 이 워크스페이스의 클론은 `origin/main` 보다 **30커밋 뒤**에
  있었고 한 번도 `git fetch` 하지 않았다. 그 낡은 HEAD 를 기준으로 `git status` 를 읽으니
  **남이 이미 밀어 놓은 143개**가 「git 미푸시」로, **61개**가 「git 에 없는 파일」로 올라왔다
  (표본 3개 중 2개가 `origin/main` 에 이미 있었다). 상주하는 빨간 줄은 곧 아무도 안 본다 —
  **거짓 붉음은 거짓 초록의 다른 얼굴**이고, 실제로 pytmux-134 가 그 값을 치렀다.

그래서 `measure_freshness()` 가 먼저 돌고, 낡았으면 **드리프트 목록을 아예 내지 않고 rc 2** 로
멈춘다. ⛔ 「그래도 참고는 되지 않나」로 되돌리지 마라 — 143줄 중 참인 것이 1줄이면 그 목록은
정보가 아니라 소음이고, 사람은 소음을 끄지 판별하지 않는다.

## ★ 그러나 「낡음」에는 갈래가 둘이고, 한쪽은 **멈출 이유가 없다** (pytmux/pytmux-388)

멈추는 것이 옳다는 말은 **잴 자가 없을 때**의 말이다. 로컬 HEAD 가 `origin/main` 보다 뒤인데
**로컬 커밋이 없으면** 상황은 다르다 — 그때 `origin/main` 은 이 브랜치의 상위집합이고, 그것이
곧 신선한 기준선이다. 잴 자가 손에 있는데 멈추는 것은 판정을 미루는 것일 뿐이다.

☠ 실제로 그 대가를 치렀다(pytmux-388): 야간 감시가 「기준선이 낡았다」를 **엿새 · 7회** 냈고,
아무도 그 한 줄짜리 처방을 안 돌렸다. 그 붉은 줄 **뒤에** 진짜 빚이 서 있었다 — p4 에만 있던
13 파일 · git 에만 있던 4 파일 · 내용이 갈린 114 파일, 미러가 depot 보다 **39 CL** 뒤. 즉
「판정을 미룬 것」이 「빚을 가린 것」이었다.

그래서 지금은 이렇게 가른다:

| 낡은 곳 | 로컬 커밋 | 무엇을 하나 |
| --- | --- | --- |
| git HEAD | 없음 | **`origin/main` 을 기준선으로 삼아 판정한다.** 낡음은 `·` 한 줄로 알린다(막지 않는다) |
| git HEAD | 있음(갈라졌다) | 멈춘다 — 어느 쪽이 기준선인지 애매하다(처방은 `rebase`) |
| p4 워크스페이스 | — | 멈춘다 — 안 받은 파일의 **내용을 볼 수가 없다**(과소평가한다) |

⛔ 이때도 **낡은 기준선의 목록은 안 낸다** — 위 문단의 규율 그대로다. 다른 것은 기준선을
`origin/main` 으로 **바꿔서** 재는 것뿐이고, 그 재기는 `git status`·`git ls-files`(둘 다 로컬
HEAD·인덱스를 본다) 대신 `_at_ref()` 를 지난다. ⚠ 게이트는 **아무것도 안 고친다** — 임시
인덱스(`GIT_INDEX_FILE`)에만 그 트리를 읽고, 작업 트리도 진짜 인덱스도 안 만진다(이 트리의
파일은 p4 가 소유한다).

## 재는 것과 말하는 것을 가른다

⛔ **소비자가 둘이면 판정도 둘이 된다.** 야간 감시(`qa/repo.py`)가 이 파일의 **화면 문구를
파싱**하게 두면 문구를 다듬는 순간 감시가 조용히 아무것도 못 세게 된다 — 한 질문을 두 술어로
묻는 자리다. 그래서 `measure_*()` 가 **데이터**를 내고, 이 파일의 `check_*()` 와 `qa/repo.py` 가
그 같은 데이터를 각자 그린다.
"""
import argparse
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 출력은 UTF-8 로 고정한다(tests/run.py 와 같은 처방). 이 게이트의 메시지는 ✓/✗ 와
# 한글을 쓰는데, 콘솔 기본 인코딩이 cp949 인 박스(한국어 Windows)에서는 `print` 가
# **UnicodeEncodeError 로 죽어** 게이트가 판정을 내리기 전에 traceback 으로 끝났다
# (2026-07-31 실측: `✗ git 저장소가 아니다` 를 찍는 순간 crash — 게시 직전 훅으로
# 쓰라는 스크립트가 정작 이 박스에서 무용이었다). errors 도 관대하게 둬, 인코딩이
# 어떻든 **판정과 종료코드**는 반드시 나오게 한다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass


def run(cmd, cwd=ROOT):
    """(rc, stdout) — 실패해도 예외 대신 rc 로 돌려준다(도구 부재 진단용).

    ⛔ **`encoding` 을 반드시 못박는다.** `text=True` 만 주면 파이썬이
    `locale.getpreferredencoding()` 으로 디코드하는데, 이 상자(Windows-KR)에서 그것은
    **cp1252** 다 — 그러면 이 저장소의 한국어 커밋 메시지·p4 디스크립션이
    `UnicodeDecodeError` 로 죽는다. 죽는 자리가 이 함수 **밖**이라(여기 except 는
    OSError/Timeout 만 받는다) 증상은 엉뚱하게 나온다: 호출부가 `None` 을 받아
    `AttributeError: 'NoneType' object has no attribute 'splitlines'`.

    ⚠ 이 결함은 **미푸시 커밋이 하나라도 있어야** 드러난다 — 없으면 `git log` 출력이
    비어 디코드할 것이 없다. 그래서 "게이트가 초록이었다"가 «드리프트가 없다»가 아니라
    «잴 것이 없었다» 였다. 이 저장소의 커밋 메시지는 전부 한국어라, 사실상
    **Windows 에서 미푸시를 한 번도 보고할 수 없었다**(실측 2026-08-08).
    """
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, f"{e}"
    return p.returncode, p.stdout


def _at_ref(ref, argv):
    """`ref` 의 트리를 **임시 인덱스**에 읽어 놓고 git 명령을 돌린다 → `(rc, stdout)`.

    # 왜 필요한가 (pytmux-388)

    `git status` 도 `git ls-files` 도 **로컬 HEAD·인덱스**를 기준선으로 쓴다. HEAD 가
    `origin/main` 보다 뒤면 그 둘은 남이 이미 민 것을 「내 빚」이라고 말한다(모듈 머리말의
    거짓 붉음). 그래서 기준선을 바꿔야 하는데, 이 트리의 파일은 **p4 가 소유**하므로
    `reset`·`checkout` 으로 진짜 인덱스를 옮기는 길은 게이트가 쓸 수 없다.

    `GIT_INDEX_FILE` 로 임시 인덱스를 세우면 그 자리에서만 다른 기준선이 선다 —
    **작업 트리도 진짜 인덱스도 안 만진다.** 읽은 직후의 인덱스는 stat 정보가 비어 있어
    `diff-index` 가 내용을 실제로 대조한다(실측 1168 파일 · 0.5초).
    """
    fd, idx = tempfile.mkstemp(prefix="publish-check-", suffix=".index")
    os.close(fd)
    os.unlink(idx)                      # read-tree 가 새로 만든다(빈 파일은 못 읽는다)
    saved = os.environ.get("GIT_INDEX_FILE")
    os.environ["GIT_INDEX_FILE"] = idx
    try:
        rc, txt = run(["git", "read-tree", ref])
        if rc:
            return rc, txt
        # 갓 읽은 인덱스는 stat 이 비어 있다 — refresh 가 그것을 채우고, 못 맞춘 파일이
        # 있으면 0 이 아닌 값을 낸다. 그것은 「다르다」는 뜻이라 실패가 아니다.
        run(["git", "update-index", "-q", "--refresh"])
        return run(["git"] + list(argv))
    finally:
        if saved is None:
            os.environ.pop("GIT_INDEX_FILE", None)
        else:
            os.environ["GIT_INDEX_FILE"] = saved
        try:
            os.unlink(idx)
        except OSError:
            pass


def rel(path):
    """p4 의 절대 로컬 경로를 저장소 상대 경로로(비교 키를 git 쪽과 일치시킨다)."""
    path = path.strip()
    if not path:
        return ""
    try:
        r = os.path.relpath(os.path.realpath(path), os.path.realpath(ROOT))
    except ValueError:
        return ""
    return "" if r.startswith("..") else r.replace(os.sep, "/")


def git_ignored(paths):
    """gitignore 된 경로 집합. p4 전용 파일(docs/internal·captures·db)이 '미푸시'로
    오검출되는 것을 막는 필수 필터다.

    ⚠ **NUL 로 주고받는다**(`-z`). 종전에는 `text=True` + 개행 join 이었는데, 그 조합이
    **Windows 에서 이 필터를 통째로 무력화**했다(2026-08-03 실측: 오탐 6813건). 두 가지가
    겹친다 — ⑴ `text=True` 는 stdin 에 유니버설 개행을 적용해 `\\n` 을 `\\r\\n` 으로 바꿔
    쓴다. git 은 그 `\\r` 을 **경로의 일부**로 읽는다. ⑵ 경로에 그런 특수문자나 비ASCII 가
    있으면 git 은 결과를 **큰따옴표로 감싸고 이스케이프해서** 돌려준다. 그래서 반환값이
    `"docs/internal/…json\\r"` 같은 모양이 되고, 호출부의 `set(raw) - git_ignored(raw)` 가
    **하나도 못 뺀다** — gitignore 된 p4 전용 파일 전부가 '게시 누락'으로 올라와 진짜
    드리프트 두 건이 그 안에 묻혔다. `-z` 는 개행 번역(바이트 입출력)과 인용 둘 다를
    비켜간다."""
    if not paths:
        return set()
    p = subprocess.run(["git", "check-ignore", "-z", "--stdin"], cwd=ROOT,
                       input="\0".join(paths).encode("utf-8"), capture_output=True)
    return {s.decode("utf-8", "replace") for s in p.stdout.split(b"\0") if s}


def check_cl(cl, out=print):
    """부정 게이트: CL 에 이 프로젝트 밖 파일(= 병렬 세션의 WIP)이 실렸는지.

    `p4 change -o | p4 change -i` 는 default CL 에 열린 **모든** 파일을 새 CL 스펙에
    싣기 때문에, 확인 없이 submit 하면 남의 작업을 대신 올린다(실제 재발 — CLAUDE.md).
    """
    rc, txt = run(["p4", "opened", "-c", str(cl)])
    if rc:
        out(f"✗ p4 opened -c {cl} 실패: {txt.strip()[:200]}")
        return 1
    files = [ln.split("#")[0] for ln in txt.splitlines() if ln.strip()]
    foreign = [f for f in files if "/pytmux/" not in f]
    out(f"CL {cl}: 파일 {len(files)}개")
    for f in foreign:
        out(f"  ✗ 남의 파일: {f}")
    if foreign:
        out(f"  → 되돌리기: p4 opened -c {cl} | sed 's/#.*//' | "
            f"grep -v \"/pytmux/\" | xargs p4 reopen -c default")
        return 1
    out("  ✓ 내 파일만" if files else "  (빈 CL)")
    return 0


# **git 에만 있어도 되는 경로는 이제 없다.** 종전 `GIT_ONLY_PREFIXES`
# (`docs/benchmark/`·`docs/image/`·`docs/PENDING_UI_IMPROVEMENTS.md`) 는 §10-17 이관의
# 한시 예외였다 — 셋이 `docs/internal/` 로 이사한 뒤에도 gitignore 는 **이미 추적 중인
# 파일을 안 내려서** 미러 HEAD 에 남아 있었고, 그것을 "p4 에 없다"로 세면 존재 대조가
# 5천 개로 상시 빨개졌다. `git rm -r --cached` 로 내린 지금 예외는 조건을 잃었다.
# 예외는 조건과 함께만 산다 — 남겨 뒀다면 그 순간부터 **진짜 드리프트를 가리는 구멍**이다.


def depot_files():
    """depot 에 **살아 있는** 파일들(저장소 상대 경로). p4 조회 실패면 None.

    삭제 리비전을 빼는 게 핵심이다 — `p4 files` 는 지워진 파일도 마지막 리비전으로
    보여주므로(`- delete change`·`- move/delete change`) 그대로 쓰면 옛날에 옮긴 파일이
    영원히 'git 에 없다'로 보고된다(실측: `docs/*.md` 40여 개가 이 모양이었다)."""
    rc, txt = run(["p4", "files", "./..."])
    if rc:
        return None
    out = set()
    for ln in txt.splitlines():
        head, _, action = ln.partition(" - ")
        if "delete change" in action:
            continue
        r = rel_depot(head.split("#")[0])
        if r:
            out.add(r)
    return out


def rel_depot(depot_path):
    """`//…/pytmux/<경로>` → `<경로>`. 이 프로젝트 밖이면 빈 문자열."""
    marker = "/pytmux/"
    i = depot_path.find(marker)
    return depot_path[i + len(marker):].strip() if i >= 0 else ""


def rel_any(path):
    """p4 가 준 경로를 저장소 상대로 — **명령마다 형식이 다르다**.

    `p4 diff -se` 는 로컬 경로(`/Users/…/pytmux/x`)를, `p4 opened` 는 **depot 경로**
    (`//woojinkim/scripts/pytmux/x`)를 준다. depot 경로에 `rel()`(realpath+relpath)을
    쓰면 ROOT 밖이라 늘 빈 문자열이 나와, 열린 파일 집합이 **통째로 비었다** — 그래서
    `p4 edit` 해 두고 고치는 중인 파일이 전부 '한쪽에만 있는 내용'으로 오분류됐다."""
    p = path.strip()
    return rel_depot(p) if p.startswith("//") else rel(p)


#: 신선도 미달 종료코드. ⛔ **1(빚이 있다)과 갈라야 한다** — 「못 쟀다」와 「빚이 있다」가
#: 같은 색이면 사람은 둘을 같은 것으로 배우고, 그러면 못 잰 날이 갚은 날처럼 지나간다.
RC_STALE = 2

#: 드리프트 갈래. **`kind` 는 트래커 지문의 재료다**(`qa/repo.py`) — 바꾸면 같은 빚이
#: 새 이슈로 다시 태어난다. 심각도는 pytmux-38 이 가른 그대로다: **존재** 드리프트(한쪽에
#: 파일이 아예 없다)는 공개 GitHub 만 보는 사람의 빌드를 깨뜨리므로 S2, **내용·커밋**은
#: 늦게 갚아도 되는 빚이라 S3.
SEVERITY = {
    "git-unpushed-commits": "S3",
    "p4-unsubmitted": "S3",
    "git-unpushed-content": "S3",
    "git-only-files": "S2",
    "depot-only-files": "S2",
}


def _drift(kind, head, why, items, fix):
    """드리프트 한 갈래를 **데이터로**. `head`·`why` 가 곧 화면 한 줄이 된다."""
    return {"kind": kind, "severity": SEVERITY[kind], "head": head, "why": why,
            "items": list(items), "count": len(items), "fix": fix}


def _int(txt, default=None):
    try:
        return int(txt.strip())
    except (AttributeError, ValueError):
        return default


def measure_freshness(remote=True):
    """**기준선이 신선한가** → `(stale, unmeasured, baseline)`.

    앞의 둘은 dict 목록이고, `baseline` 은 **판정을 어느 기준선 위에서 낼 것인가**다 —
    `None` 이면 종전대로 로컬 HEAD·인덱스, dict 면 그 `ref`(지금은 `origin/main` 뿐).

    ⛔ 이것을 안 재고 드리프트를 세면 나오는 숫자가 남의 게시다(모듈 docstring §거짓 붉음).
    ★ 그러나 **로컬 커밋이 없이 뒤처지기만 했으면 멈추지 않는다** — 그때 `origin/main` 이
      곧 신선한 기준선이라 잴 수 있고, 멈추면 그 뒤의 진짜 빚이 함께 가려진다(pytmux-388).
    ⚠ `git fetch` 는 `refs/remotes/` 만 건드린다 — **작업 트리는 안 만진다.** 이 트리는 p4
      워크스페이스이고 남의 열린 파일이 그 안에 있으므로, 게이트가 스스로 `pull`·`sync` 하는
      길은 만들지 않는다(처방만 낸다 · 당길지는 사람이 정한다).
    """
    stale, unmeasured, baseline = [], [], None

    # ── git 기준선 ────────────────────────────────────────────────────────
    if remote:
        rc, txt = run(["git", "fetch", "--no-tags", "--quiet", "origin"])
        if rc:
            unmeasured.append({
                "what": "원격 대조",
                "detail": f"git fetch 실패(오프라인?): {txt.strip()[:120]} — "
                          "origin/main 이 낡았을 수 있어 미푸시 판정은 하한이다"})
    else:
        unmeasured.append({"what": "원격 대조",
                           "detail": "--no-remote 로 껐다 — origin/main 의 신선도를 안 쟀다"})
    rc, txt = run(["git", "rev-list", "--count", "HEAD..origin/main"])
    behind = _int(txt, 0) if not rc else None
    rc2, txt2 = run(["git", "rev-list", "--count", "origin/main..HEAD"])
    ahead = _int(txt2, 0) if not rc2 else None
    if behind is None:
        unmeasured.append({"what": "git 기준선",
                           "detail": "origin/main 을 못 읽었다 — 로컬 HEAD 의 신선도 미확인"})
    elif behind and ahead:
        # 갈라졌다 — 어느 쪽이 기준선인지 애매하다. ★ 처방이 `pull` 이 아니다: 이 트리의
        # 파일은 **p4 가 소유**하고 대개 origin 보다 앞서 있어서, `pull`(=merge)은 남의
        # 열린 파일을 덮으려다 거절당하거나 덮는다. 로컬 커밋을 잃지 않게 rebase 로 보낸다.
        stale.append({"what": "git 기준선",
                      "detail": f"로컬 HEAD 가 origin/main 보다 {behind}커밋 뒤이고 "
                                f"로컬 커밋 {ahead}개가 있다 — 어느 쪽도 기준선이 못 된다",
                      "fix": "git fetch origin && git rebase origin/main   "
                             "# 로컬 커밋이 있다 — 잃지 않게"})
    elif behind:
        # ★ 뒤처지기만 했다 = `origin/main` 이 이 브랜치의 **상위집합**이다. 그러면 잴 자가
        #   손에 있으므로 멈추지 않고 **그것을 기준선으로** 잰다(pytmux-388). 낡음 자체는
        #   사람이 고칠 것이라 처방과 함께 알리되, 판정을 막지는 않는다 — 엿새를 붉은 채로
        #   서 있던 것이 이 이슈가 치른 값이다.
        baseline = {
            "ref": "origin/main",
            "what": "git 기준선",
            "detail": f"로컬 HEAD 가 origin/main 보다 {behind}커밋 뒤 — 로컬 커밋이 없어 "
                      "origin/main 이 상위집합이다. 그래서 «그것»을 기준으로 잰다",
            "fix": "git fetch origin && git reset --mixed origin/main   "
                   "# 작업 트리는 안 건드린다(p4 소유)"}

    # ── p4 기준선 ─────────────────────────────────────────────────────────
    rc, txt = run(["p4", "sync", "-n", "./..."])
    if rc:
        unmeasured.append({"what": "p4 기준선",
                           "detail": f"p4 sync -n 실패: {txt.strip()[:120]}"})
    else:
        # ⚠ 깨끗하면 stdout 이 **비고**("file(s) up-to-date." 는 stderr 라 `run` 이 안 받는다).
        #   그래도 그 문구를 걸러 두는 것은 p4 판마다 스트림이 다를 수 있어서다.
        pending = [ln for ln in txt.splitlines()
                   if ln.strip() and "up-to-date" not in ln]
        if pending:
            stale.append({
                "what": "p4 기준선",
                "detail": f"워크스페이스가 depot head 보다 뒤 — 안 받은 파일 {len(pending)}개. "
                          "내용 드리프트를 **과소평가**한다(2026-08-08 실측: 4개 → sync 후 19개)",
                "fix": "p4 sync ./...   ⚠ 공유 워크스페이스다 — 남이 연 파일은 안 받아진다"})
    return stale, unmeasured, baseline


def measure_existence(baseline=None):
    """**파일이 한쪽에만 존재**하는 드리프트 → `(drifts, unmeasured)`.

    `baseline` 이 있으면 git 쪽 목록을 **인덱스가 아니라 그 ref 의 트리**에서 뽑는다 —
    인덱스는 로컬 HEAD 를 따르므로, HEAD 가 뒤처져 있으면 남이 민 새 파일이 통째로
    「git 에 없는 파일」로 올라온다(pytmux-388).

    내용 대조(`p4 diff -se` × `git status`)가 원리적으로 못 보는 구멍이다: depot 에 아예
    없는 파일은 `p4 diff -se` 에 안 나오고, git 에 커밋까지 끝난 파일은 `git status` 에
    안 나온다. 즉 **새 파일을 git 에만 올리고 `p4 add` 를 잊으면** 종전 게이트는 계속
    '✓ 미러 일치' 라고 말한다 — 미러 사고 3건과 정확히 같은 부류의 남은 절반이다."""
    depot = depot_files()
    if depot is None:
        return [], [{"what": "존재 대조", "detail": "p4 files 조회 실패"}]
    if baseline:
        ref = baseline["ref"]
        rc, txt = run(["git", "ls-tree", "-r", "--name-only", ref])
        if rc:
            return [], [{"what": "존재 대조", "detail": f"git ls-tree {ref} 실패"}]
    else:
        rc, txt = run(["git", "ls-files"])
        if rc:
            return [], [{"what": "존재 대조", "detail": "git ls-files 실패"}]
    tracked = {ln.strip() for ln in txt.splitlines() if ln.strip()}
    git_only = sorted(tracked - depot)
    # depot 에만 있는 것은 gitignore 된 p4 전용 파일(docs/internal·captures·db)이 대부분이다.
    depot_only_raw = sorted(depot - tracked)
    depot_only = sorted(set(depot_only_raw) - git_ignored(depot_only_raw))
    drifts = []
    if git_only:
        drifts.append(_drift(
            "git-only-files", "depot 에 없는 파일", "git 에만 커밋됐다(p4 add 누락)",
            git_only, "p4 add <파일> 후 번호 CL 로 submit"))
    if depot_only:
        drifts.append(_drift(
            "depot-only-files", "git 에 없는 파일", "p4 에만 제출됐다(gitignore 도 아님)",
            depot_only,
            "git add/commit + push, 또는 p4 전용이면 .gitignore 에 규칙 추가"))
    return drifts, []


def measure_drift(pre_push=False, baseline=None):
    """미러 빚 전량 → `(drifts, wip, unmeasured)`.

    ⛔ **기준선 신선도는 여기서 안 본다** — 부르는 쪽이 `measure_freshness()` 로 먼저 재고,
       낡았으면 이 함수를 **아예 부르지 않는다**(낡은 기준선의 목록은 정보가 아니라 소음이다).

    `pre_push=True` 면 `git-unpushed-commits` 갈래를 안 잰다(issue/pytmux-267) — `git push`
    직전에는 **지금 밀려는 그 커밋**이 언제나 `origin/main..HEAD` 에 잡혀 이 갈래가 항상
    참이 된다(`origin/main` 은 push 가 끝나야 움직인다). 그러니 이 갈래는 훅의 판정에
    못 쓴다 — 훅이 실제로 재야 하는 것은 **밀고 나면 내용·존재가 맞는가**(아래 나머지
    갈래들)이지 **지금 안 밀렸나**(언제나 참인 동어반복)가 아니다. 훅이 아닌 자리
    (예: 사람이 직접 상태를 보는 `check_all.py`)에서는 여전히 잰다 — "제출만 하고 밀기를
    잊었다" 를 잡는 유일한 신호라 완전히 없애면 안 된다."""
    drifts, unmeasured = [], []

    if not pre_push:
        rc, unpushed = run(["git", "log", "--oneline", "origin/main..HEAD"])
        unpushed = [ln for ln in unpushed.splitlines() if ln.strip()] if not rc else []
    else:
        unpushed = []
    if unpushed:
        drifts.append(_drift(
            "git-unpushed-commits", "git 미푸시 커밋", "p4 만 게시된 상태일 수 있다",
            unpushed, "git push origin main"))

    rc, opened_txt = run(["p4", "opened", "./..."])
    if rc and "not opened" not in opened_txt:
        unmeasured.append({"what": "내용·존재 대조",
                           "detail": f"p4 조회 실패: {opened_txt.strip()[:120]}"})
        return drifts, [], unmeasured
    # 프로젝트 판별은 **세퍼레이터 무관**으로 — Windows 워크스페이스의 로컬 경로는
    # `D:\...\pytmux\pytmux\tests\run.py` 라 `"/pytmux/"` 리터럴이 절대 안 맞고, 그러면
    # opened 가 통째로 비어 열린 파일이 전부 "한쪽에만 있는 내용"으로 오분류된다.
    opened = {r for r in (rel_any(ln.split("#")[0])
                          for ln in opened_txt.splitlines()
                          if ln.strip() and "/pytmux/" in ln.replace("\\", "/"))
              if r}
    _, se_txt = run(["p4", "diff", "-se", "./..."])
    depot_diff = {r for r in (rel(ln) for ln in se_txt.splitlines()) if r}
    _, st_txt = run(["git", "status", "--porcelain"])
    git_dirty, git_untracked = set(), set()
    for ln in st_txt.splitlines():
        if not ln.strip():
            continue
        code, path = ln[:2], ln[3:].strip().strip('"')
        (git_untracked if code.strip() == "??" else git_dirty).add(path)
    if baseline:
        # ★ 기준선을 갈아끼운다(pytmux-388). `git status` 가 방금 낸 두 집합은 **로컬
        #   HEAD·인덱스** 기준이라, HEAD 가 뒤처져 있으면 남이 이미 민 것을 전부 담고 있다.
        ref = baseline["ref"]
        _, dtxt = _at_ref(ref, ["diff-index", "--name-only", ref])
        git_dirty = {ln.strip() for ln in dtxt.splitlines() if ln.strip()}
        # 「추적 안 됨」도 기준선을 탄다 — 그 ref 가 이미 든 파일은 새 파일이 아니다.
        _, ttxt = run(["git", "ls-tree", "-r", "--name-only", ref])
        git_untracked -= {ln.strip() for ln in ttxt.splitlines() if ln.strip()}

    ignored = git_ignored(sorted(depot_diff))
    # ① depot 과 다른데 git 은 clean → git 에만 있는 내용(p4 미제출)
    p4_missing = sorted(depot_diff - ignored - opened - git_dirty - git_untracked)
    # ② depot 과 같은데 git 은 modified → p4 에만 있는 내용(git 미푸시)
    git_missing = sorted(git_dirty - depot_diff - opened)
    if p4_missing:
        drifts.append(_drift(
            "p4-unsubmitted", "p4 미제출", "git 에는 있고 depot 에는 없는 내용",
            p4_missing, "p4 edit <파일> 후 번호 CL 로 submit(캐치업)"))
    if git_missing:
        drifts.append(_drift(
            "git-unpushed-content", "git 미푸시", "depot 과 같은데 git HEAD 와 다른 내용",
            git_missing, "git add/commit + push(p4 만 게시된 상태)"))
    # 양쪽 다 미게시(수정 중이거나 아직 아무 쪽에도 없는 신규 파일) = 드리프트 아님.
    wip = sorted(((git_dirty | git_untracked) & (depot_diff | opened))
                 | (git_untracked - depot_diff - opened))

    edrifts, eunmeasured = measure_existence(baseline=baseline)
    return drifts + edrifts, wip, unmeasured + eunmeasured


def render_drift(drift, out=print, limit=20):
    """드리프트 한 갈래를 사람이 읽는 세 줄로. 문구의 SSOT 는 여기 하나다."""
    out(f"✗ {drift['head']} {drift['count']}개 — {drift['why']}:")
    for item in drift["items"][:limit]:
        out(f"    {item}")
    out(f"  → {drift['fix']}")


def check_existence(out=print, baseline=None):
    """존재 드리프트 — `measure_existence()` 의 화면 판(반환값은 **갈래 수**)."""
    drifts, unmeasured = measure_existence(baseline=baseline)
    for u in unmeasured:
        out(f"· {u['detail']} — {u['what']} 생략")
    for d in drifts:
        render_drift(d, out=out)
    return len(drifts)


def check_mirror(out=print, remote=True, pre_push=False):
    # ── git 클론인가 (없으면 잴 것 자체가 없다 — check_all 은 이 스텝을 SKIP 한다) ──
    rc, head = run(["git", "rev-parse", "HEAD"])
    if rc:
        out(f"✗ git 저장소가 아니다: {head.strip()[:120]}")
        return 1

    # ── ⛔ 기준선 신선도부터. 낡았으면 **판정을 내지 않는다** ────────────────
    stale, unmeasured, baseline = measure_freshness(remote=remote)
    for u in unmeasured:
        out(f"· 못 쟀다 — {u['what']}: {u['detail']}")
    if baseline:
        # ⚠ 막지 않는다 — 잴 자가 손에 있다(모듈 머리말 §낡음의 두 갈래 · pytmux-388).
        out(f"· {baseline['what']}이 낡았지만 판정한다 — {baseline['detail']}")
        out(f"  → {baseline['fix']}")
    if stale:
        out("✗ 기준선이 낡아 미러 판정을 내지 않는다 "
            "(지금 재면 남의 게시가 내 빚으로 보인다):")
        for s in stale:
            out(f"    {s['what']} — {s['detail']}")
            out(f"      → {s['fix']}")
        out("  → 위 처방을 돌린 뒤 이 게이트를 다시 돌린다")
        return RC_STALE

    # ── 양방향 드리프트 + 존재 드리프트 ──────────────────────────────────────
    drifts, wip, dunmeasured = measure_drift(pre_push=pre_push,
                                             baseline=baseline)
    for d in drifts:
        render_drift(d, out=out, limit=10 if d["kind"] == "git-unpushed-commits" else 20)
    if wip:
        out(f"· 작업 중 {len(wip)}개(양쪽 미게시 — 드리프트 아님): "
            f"{', '.join(wip[:6])}{' …' if len(wip) > 6 else ''}")
    for u in dunmeasured:
        out(f"· 못 쟀다 — {u['what']}: {u['detail']}")

    if drifts:
        return 1
    # ⛔ 못 잰 것이 있으면 초록이 아니다(원칙 ⓑ — "안 재고 있는 줄 모르는 것"이 최악이다).
    if dunmeasured or unmeasured:
        out("· 미러 드리프트는 못 찾았지만 **못 잰 항목이 있다** — 초록으로 읽지 말 것")
        return RC_STALE
    where = f" · 기준선 {baseline['ref']}" if baseline else ""
    if pre_push:
        out("✓ p4↔git 미러 일치(내용·존재 드리프트 없음 — push 직전이라 미푸시 커밋은 "
            f"안 쟀다{where})")
    else:
        out(f"✓ p4↔git 미러 일치(미푸시 커밋 없음, 내용·존재 드리프트 없음{where})")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="p4↔git 게시 게이트 — rc 0(깨끗)/1(빚)/2(기준선이 낡아 못 쟀다)")
    ap.add_argument("--cl", help="이 CL 이 내 파일만 담았는지 검사(부정 게이트)")
    ap.add_argument("--no-remote", action="store_true",
                    help="git fetch 생략(오프라인) — 기준선 신선도는 '못 쟀다'로 남는다")
    ap.add_argument("--pre-push", action="store_true",
                    help="git-unpushed-commits 갈래를 안 잰다(issue/pytmux-267) — "
                         "pre-push 훅 전용. push 직전엔 그 갈래가 언제나 참인 동어반복이다")
    a = ap.parse_args(argv)
    if a.cl:
        return check_cl(a.cl)
    return check_mirror(remote=not a.no_remote, pre_push=a.pre_push)


if __name__ == "__main__":
    sys.exit(main())
