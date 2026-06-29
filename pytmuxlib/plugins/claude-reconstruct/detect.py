"""claude-reconstruct — 패널이 Claude Code 를 돌리는지 자체 판정(자기완결 폴백).

평상 게이트는 claude-code 가 패널에 심는 `_hdr_claude`(디바운스 Claude 신호)·`_claude`
(idle/busy/limit)를 getattr 로 부드럽게 읽는 것이다(무비용·정확). 그 둘이 **아예 없으면**
(claude-code 디렉토리 삭제 = delete-to-disable) 여기 프로세스트리 BFS 로 직접 판정한다 —
화면 스크랩이 아니라 자식 프로세스에 `claude` 가 있는지라 견고하다. ps 왕복이 있으므로
호출측이 디바운스한다(수 초 1회). 부수효과(ps)는 주입 가능해 헤드리스 단위테스트가
실 프로세스 없이 검증한다(claude-code/transcript.py 선례와 동형)."""
from __future__ import annotations


def _default_ps_list():
    """`ps -axo pid=,ppid=,comm=` → [(pid, ppid, comm)]. POSIX 전용. 실패 시 []."""
    import subprocess
    try:
        out = subprocess.run(["ps", "-axo", "pid=,ppid=,comm="],
                             capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return []
    rows = []
    for ln in out.stdout.splitlines():
        parts = ln.split(None, 2)
        if len(parts) >= 2:
            try:
                rows.append((int(parts[0]), int(parts[1]),
                             parts[2] if len(parts) > 2 else ""))
            except ValueError:
                pass
    return rows


def has_claude_descendant(shell_pid, ps_list=None) -> bool:
    """shell_pid 의 후손 중 comm 에 'claude' 가 든 프로세스가 있나(BFS). 부수효과
    (ps_list)는 주입 가능."""
    if not shell_pid:
        return False
    rows = (ps_list or _default_ps_list)()
    children: dict[int, list] = {}
    comm: dict[int, str] = {}
    for pid, ppid, c in rows:
        children.setdefault(ppid, []).append(pid)
        comm[pid] = c
    seen = set()
    frontier = [shell_pid]
    while frontier:
        nxt = []
        for pid in frontier:
            for ch in children.get(pid, []):
                if ch in seen:
                    continue
                seen.add(ch)
                if "claude" in (comm.get(ch, "") or "").lower():
                    return True
                nxt.append(ch)
        frontier = nxt
    return False
