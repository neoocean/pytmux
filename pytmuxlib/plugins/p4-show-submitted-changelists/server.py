"""p4-show-submitted-changelists 서버 측 로직 — `p4` 호출(submitted 목록·describe).

textual 무관(순수 subprocess). version.py 의 p4 호출 패턴을 따른다:
  * `p4 -G ...`(마샬링된 파이썬 dict 스트림)로 **구조화된** 출력을 안정적으로 파싱.
  * `proc.no_window_kwargs()` 로 창 없는(pythonw) 서버가 콘솔 p4.exe 를 띄울 때 콘솔
    창이 번쩍이지 않게 한다.
  * **현재 설정된 퍼포스 서버 설정을 그대로 사용**한다 — P4PORT/P4CLIENT/P4USER 를
    명시 주입하지 않고, 활성 패널 cwd 에서 `p4` 를 실행해 그 디렉토리 트리의 `.p4config`·
    환경·`p4 set` 값을 honor 한다(version.py 와 동일 철학).

오류는 전부 graceful: 회신 dict 의 `err` 필드에 원문 p4 메시지(또는 예외)를 담아 클라가
표시한다(여기선 i18n 비대상 — 클라 화면이 로케일로 감싼다)."""
from __future__ import annotations

import io
import marshal
import os
import subprocess

from pytmuxlib import proc

# p4 호출 타임아웃(초). 목록은 짧게, describe 는 큰 CL 도 견디게 넉넉히.
_LIST_TIMEOUT = 8.0
_DESCRIBE_TIMEOUT = 20.0


def _cwd(server, sess) -> str | None:
    """활성 패널의 cwd(절대경로) 또는 None. 이 디렉토리에서 p4 를 실행해 그 워크스페이스의
    퍼포스 설정(.p4config·P4CONFIG·p4 set)을 적용받는다(version.py 와 같은 best-effort)."""
    try:
        cwd = server._resolve_start_cwd(sess, "current")
    except Exception:
        cwd = None
    return os.path.abspath(cwd) if cwd else None


def _d(v) -> str:
    """p4 -G 의 값(bytes)을 문자열로(나머지 타입은 str() 폴백)."""
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", "ignore")
    return str(v)


def _rec(d: dict) -> dict:
    """p4 -G 가 준 {bytes: bytes} dict 를 {str: str} 로 평탄 디코드."""
    return {_d(k): _d(v) for k, v in d.items()}


def _run_p4_marshal(args: list[str], cwd: str | None,
                    timeout: float) -> tuple[list | None, str | None]:
    """`p4 -G <args>` 를 실행해 마샬링된 dict 들의 리스트를 돌려준다. 실행 자체가 실패하면
    (p4 부재·타임아웃 등) (None, 오류문자열). 각 dict 는 아직 bytes 키/값(_rec 로 디코드)."""
    try:
        r = subprocess.run(["p4", "-G", *args], capture_output=True,
                           timeout=timeout, cwd=cwd, **proc.no_window_kwargs())
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)
    out: list = []
    buf = io.BytesIO(r.stdout)
    while True:
        try:
            out.append(marshal.load(buf))   # 레코드(dict)를 차례로 언마샬
        except EOFError:
            break
        except (ValueError, TypeError, EOFError):
            break                            # 깨진 스트림 — 여기까지만
    return out, None


def _info(cwd: str | None) -> dict:
    """`p4 info` 로 현재 서버 주소·유저·클라이언트를 읽어 화면 제목에 쓴다(best-effort)."""
    recs, err = _run_p4_marshal(["info"], cwd, _LIST_TIMEOUT)
    if err or not recs:
        return {}
    r = _rec(recs[0])
    return {"port": r.get("serverAddress", ""),
            "user": r.get("userName", ""),
            "client": r.get("clientName", "")}


def _fmt_when(epoch: str) -> str:
    """p4 의 time(에폭 초 문자열)을 'YYYY/MM/DD HH:MM' 로. 파싱 실패 시 원문 그대로."""
    try:
        import time as _t
        return _t.strftime("%Y/%m/%d %H:%M", _t.localtime(int(epoch)))
    except (ValueError, OSError, OverflowError):
        return epoch or ""


_UNSET = object()      # "cwd 미지정 → 세션에서 구하라"(None 은 '구했지만 없음'이라 구분)


def list_changes_msg(server, sess, count: int, cwd=_UNSET) -> dict:
    """submitted changelists 최신 `count` 건을 회신한다(현재 워크스페이스의 p4 설정 사용).

    `p4 -G changes -m <count> -s submitted -l` → 각 레코드를 {change,when,user,client,desc}
    로 정규화. p4 오류 레코드(code==error)는 `err` 로 모은다. info(서버주소 등)도 함께.

    (`cwd` 를 넘기면 세션 조회를 건너뛴다 — 서버 훅이 cwd 를 루프에서 미리 구하고
    나머지 p4 서브프로세스를 executor 로 넘기기 위한 분할. LOOP-2.)"""
    if cwd is _UNSET:
        cwd = _cwd(server, sess)
    info = _info(cwd)
    recs, err = _run_p4_marshal(
        ["changes", "-m", str(count), "-s", "submitted", "-l"], cwd, _LIST_TIMEOUT)
    if err is not None:
        return {"t": "p4_changes", "rows": [], "err": err, "info": info}
    rows: list[dict] = []
    perr: str | None = None
    for d in recs or []:
        r = _rec(d)
        if r.get("code") == "error":
            perr = (r.get("data", "") or "p4 error").strip()
            continue
        rows.append({
            "change": r.get("change", ""),
            "when": _fmt_when(r.get("time", "")),
            "user": r.get("user", ""),
            "client": r.get("client", ""),
            "desc": (r.get("desc", "") or "").strip(),
        })
    return {"t": "p4_changes", "rows": rows, "err": perr, "info": info}


def describe_msg(server, sess, change: str, cwd=_UNSET) -> dict:
    """체인지리스트 `change` 의 상세(설명+영향 파일)를 사람이 읽기 좋은 텍스트로 회신한다.

    `p4 describe -s <change>`(-s = diff 생략, 파일 목록만)의 원문 텍스트를 그대로 싣는다 —
    화면은 이걸 줄 단위로 스크롤한다. returncode≠0 이면 stderr 를 `err` 로.

    (`cwd` 인자는 list_changes_msg 와 동일한 LOOP-2 분할용.)"""
    if cwd is _UNSET:
        cwd = _cwd(server, sess)
    try:
        r = subprocess.run(["p4", "describe", "-s", str(change)],
                           capture_output=True, timeout=_DESCRIBE_TIMEOUT,
                           cwd=cwd, **proc.no_window_kwargs())
    except (OSError, subprocess.SubprocessError) as e:
        return {"t": "p4_describe", "change": str(change), "text": "", "err": str(e)}
    text = r.stdout.decode("utf-8", "ignore")
    err = None
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "ignore").strip() or "p4 describe error"
    return {"t": "p4_describe", "change": str(change), "text": text, "err": err}
