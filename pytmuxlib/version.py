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


def code_version(project_dir: str | None = None, timeout: float = 1.5) -> str:
    """실행 코드의 버전 문자열을 best-effort 로 만든다(네트워크/도구 없으면 폴백).

    반환 예: "p4:57008" · "git:0deb19e" · "unknown". timeout 으로 p4/git 호출이
    행(hang)에 빠지지 않게 한다(p4 서버 불통 등)."""
    d = project_dir or PROJECT_DIR
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
