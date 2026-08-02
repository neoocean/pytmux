"""실행 중인 코드 버전(퍼포스 체인지리스트)·업타임 표기 헬퍼.

`version` 명령 팝업이 쓴다(클라/서버 각자 자기 프로세스가 **로드한 코드**의 버전을
보고). 버전은 best-effort: ① p4 `#have`(동기화된 CL) → ② git short hash → ③ unknown.

**중요**: 버전은 프로세스가 코드를 로드한 시점(서버=부팅, 클라=런치)에 캡처해 캐시한다.
이후 디스크가 새 CL 로 바뀌어도(예: p4 submit/sync) 실행 중 코드는 그대로이므로,
"지금 디스크"가 아니라 "이 프로세스가 돌리는 코드"의 버전을 보여주려면 시작 시점
캡처가 옳다(serverpersist re-exec 시엔 새 프로세스라 다시 캡처된다)."""
from __future__ import annotations

import os
import subprocess

from . import proc

# pytmux 프로젝트 루트(= pytmuxlib 패키지의 상위). server.PROJECT_DIR 과 동일 규칙.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 프로세스 1회 캐시(모듈 docstring 의 "로드 시점 캡처" 계약을 함수 자신이 지킨다).
# 키 = project_dir. 호출부(server/client)가 각자 캐시하고 있었지만, **한 프로세스에서
# 서버·클라를 여러 번 띄우는 경우**(테스트 러너는 전 모듈을 한 프로세스에서 돌린다)엔
# 매번 p4+git 서브프로세스가 새로 떴다. 이 박스 실측 4.5~5.2초/회(둘 다 타임아웃 →
# "unknown") — Windows 에서 그 프로세스 생성이 **이벤트 루프를 ~0.6초 정체**시켜,
# 살아 있는 서버로의 루프백 connect 가 `_LOOPBACK_CONNECT_TIMEOUT`(0.5s)에 걸려
# 거짓 TimeoutError 를 냈다(test_server 연결 테스트 10건 TIMEOUT, 2026-07-31 진단).
_CACHE: dict[str, str] = {}


def code_version(project_dir: str | None = None, timeout: float = 1.5) -> str:
    """실행 코드의 버전 문자열을 best-effort 로 만든다(네트워크/도구 없으면 폴백).

    반환 예: "p4:57008" · "git:0deb19e" · "unknown". timeout 으로 p4/git 호출이
    행(hang)에 빠지지 않게 한다(p4 서버 불통 등). 결과는 프로세스 1회만 구한다
    (`_CACHE`). `PYTMUX_CODE_VERSION` 이 설정돼 있으면 그 값을 그대로 쓴다 —
    테스트/CI 위생용 탈출구(서브프로세스 0개)이자, p4·git 이 느린 환경에서 startup
    비용을 없애는 수단이다."""
    env = os.environ.get("PYTMUX_CODE_VERSION")
    if env:
        return env
    d = project_dir or PROJECT_DIR
    hit = _CACHE.get(d)
    if hit is not None:
        return hit
    return _CACHE.setdefault(d, _probe_version(d, timeout))


def _probe_version(d: str, timeout: float) -> str:
    """실제 조회(캐시 미스 경로) — 위 `code_version` 이 유일한 호출자."""
    # ① p4 #have — 이 워크스페이스에 동기화된 CL(=디스크 코드 리비전).
    try:
        # no_window_kwargs: 창 없는 pythonw.exe 로 뜬 서버가 콘솔 앱(p4.exe)을
        # 부팅 시 띄울 때 콘솔 창이 번쩍이지 않게(§10 제보: 딸려 뜨는 창).
        out = subprocess.run(
            ["p4", "changes", "-m1", os.path.join(d, "...") + "#have"],
            capture_output=True, timeout=timeout, cwd=d,
            **proc.no_window_kwargs())
        if out.returncode == 0:
            text = out.stdout.decode("utf-8", "ignore").strip()
            # "Change 57008 on ... by ..." → 57008
            parts = text.split()
            if len(parts) >= 2 and parts[0] == "Change" and parts[1].isdigit():
                return f"p4:{parts[1]}"
    except (OSError, subprocess.SubprocessError):
        pass
    # ② git short hash 폴백.
    try:
        out = subprocess.run(["git", "-C", d, "rev-parse", "--short", "HEAD"],
                             capture_output=True, timeout=timeout,
                             **proc.no_window_kwargs())
        if out.returncode == 0:
            h = out.stdout.decode("utf-8", "ignore").strip()
            if h:
                return f"git:{h}"
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def fmt_uptime(seconds: float) -> str:
    """초 단위 업타임을 "1d 02:03:04" / "02:03:04" 로."""
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    hms = f"{h:02d}:{m:02d}:{s:02d}"
    return f"{d}d {hms}" if d else hms
