"""Claude Code 트랜스크립트(`~/.claude/projects/<proj>/*.jsonl`) 토큰 회계 — §10-D.

배경(docs/internal/TOKEN_UNDERCOUNT_TRANSCRIPT_SOLUTION.md): pytmux 의 토큰 누계는
busy 푸터 `↑/↓ N tokens` 스크랩(tokens.py)이라 본질적으로 **비캐시 input+output**
근사다. API usage 4항목 중 `cache_read`/`cache_creation`(에이전트 세션 토큰 볼륨의
대다수)을 통째로 빠뜨려 실제의 0.4% 수준(약 230~265배 과소)만 집계한다. 다른 도구
(ccusage·`/usage`)는 트랜스크립트 JSONL 의 4항목을 모두 합산한다.

이 모듈은 그 권위 출처를 읽는 순수 파서 + 증분 테일러 + 패널↔트랜스크립트 매핑을
제공한다(코어/스크랩 경로와 분리, 어디서나 부담 없이 호출). 부수효과(ps·lsof·/proc)
는 전부 주입 가능한 lister 인자로 분리해 헤드리스 단위테스트가 실 프로세스/파일 없이
검증할 수 있게 한다.

집계 단위: assistant 메시지의 `message.usage` 만 회계 대상. 중복 제거 키 =
(message.id, requestId)(ccusage 동일) — resume·스트리밍 재기록이 같은 메시지를 여러
파일/줄에 복사하므로 필수. 둘 다 없으면 이벤트 top-level `uuid` 로 폴백(중복 불가능한
유일 키). `iterations`(폴백/서버툴 분해)는 top-level usage 와 이중계산하지 않는다.
"""
from __future__ import annotations

import glob
import json
import os


def projects_dir() -> str:
    """트랜스크립트 루트(`<config>/projects`). $CLAUDE_CONFIG_DIR 우선, 없으면 ~/.claude."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "projects")


def encode_project_dir(path: str) -> str:
    """절대 경로를 Claude Code 의 프로젝트 디렉터리명으로 인코딩(`/`·`.`→`-`)."""
    ap = os.path.abspath(os.path.expanduser(path))
    return ap.replace("/", "-").replace(".", "-")


def project_dir_for(cwd: str, root: str | None = None) -> str:
    """cwd 에 해당하는 트랜스크립트 디렉터리 절대경로."""
    return os.path.join(root or projects_dir(), encode_project_dir(cwd))


# ---- 파서(순수) ----

def _i(u: dict, key: str) -> int:
    v = u.get(key)
    return int(v) if isinstance(v, (int, float)) else 0


def parse_line(obj: dict):
    """JSON 이벤트 1건(dict) → (xkey, rec) 또는 None.

    assistant + message.usage 만 대상. rec = {ts(ISO 문자열), session_uuid, model,
    input, output, cache_create, cache_read, is_sidechain}. xkey = dedup 키
    'msgid:reqid'(둘 다 있으면), 아니면 이벤트 uuid, 그것도 없으면 None(셀 수 없음)."""
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    msg = obj.get("message")
    if not isinstance(msg, dict):
        return None
    usage = msg.get("usage")
    if not isinstance(usage, dict):
        return None
    msg_id = msg.get("id")
    req_id = obj.get("requestId")
    if msg_id and req_id:
        xkey = f"{msg_id}:{req_id}"
    else:
        xkey = obj.get("uuid") or msg_id          # 폴백: 유일 이벤트 uuid
    if not xkey:
        return None
    rec = {
        "xkey": str(xkey),
        "ts": obj.get("timestamp"),
        "session_uuid": obj.get("sessionId"),
        "model": msg.get("model"),
        "input": _i(usage, "input_tokens"),
        "output": _i(usage, "output_tokens"),
        "cache_create": _i(usage, "cache_creation_input_tokens"),
        "cache_read": _i(usage, "cache_read_input_tokens"),
        "is_sidechain": 1 if obj.get("isSidechain") else 0,
    }
    return rec["xkey"], rec


def iter_records(lines):
    """라인 이터러블 → (xkey, rec) 제너레이터. 빈 줄·JSON 오류·비대상은 건너뛴다."""
    for line in lines:
        line = line.strip() if isinstance(line, str) else line
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (ValueError, TypeError):
            continue
        out = parse_line(obj)
        if out is not None:
            yield out


def sum_records(recs) -> dict:
    """rec 들의 4항목 합 + 파생값. footer=in+out(스크랩 근사), full=4항목 합, ratio."""
    b = {"input": 0, "output": 0, "cache_create": 0, "cache_read": 0, "turns": 0}
    for r in recs:
        b["input"] += r.get("input", 0)
        b["output"] += r.get("output", 0)
        b["cache_create"] += r.get("cache_create", 0)
        b["cache_read"] += r.get("cache_read", 0)
        b["turns"] += 1
    b["footer"] = b["input"] + b["output"]
    b["full"] = b["footer"] + b["cache_create"] + b["cache_read"]
    b["ratio"] = (b["full"] / b["footer"]) if b["footer"] else 0.0
    return b


# ---- 증분 테일러 ----

def tail_file(path: str, offset: int = 0):
    """path 의 offset(바이트) 이후 append 분만 읽어 (recs, new_offset) 반환.

    파일이 offset 보다 짧으면(truncate/rotate) 0 부터 다시 읽는다. 마지막 줄이
    개행으로 끝나지 않으면(쓰기 중) 그 줄은 보류하고 offset 을 그 줄 시작까지만
    전진시킨다(다음 호출에서 완성된 줄을 다시 읽음). 실패 시 ([], offset)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], offset
    if offset > size:                      # 회전/절단 → 처음부터
        offset = 0
    try:
        with open(path, "rb") as fh:
            fh.seek(offset)
            data = fh.read()
    except OSError:
        return [], offset
    if not data:
        return [], offset
    # 마지막 줄이 미완성(개행 없음)이면 보류 — 완성 분만 처리.
    nl = data.rfind(b"\n")
    if nl == -1:
        return [], offset                  # 완성된 줄 없음
    consumed = data[:nl + 1]
    text = consumed.decode("utf-8", "replace")
    recs = [rec for _k, rec in iter_records(text.splitlines())]
    return recs, offset + len(consumed)


# ---- 패널 → 트랜스크립트 매핑(부수효과 주입 가능) ----

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


def claude_descendant_pid(shell_pid: int, ps_list=None):
    """shell_pid 의 후손 중 comm 에 'claude' 가 든 프로세스 pid(없으면 None).

    ps_list: () -> [(pid,ppid,comm)] 주입(테스트). 기본은 실 `ps`. BFS 로 후손을
    훑어 가장 얕은 claude 를 고른다(셸 직속 자식이 claude 인 일반 경우 1-hop)."""
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
                    return ch
                nxt.append(ch)
        frontier = nxt
    return None


def _default_open_jsonl(pid: int):
    """pid 가 연 `.jsonl` 경로들(projects 하위). /proc 우선, 없으면 lsof. 실패 시 []."""
    root = projects_dir()
    found = []
    # Linux: /proc/<pid>/fd/* 심볼릭 링크.
    fd_dir = f"/proc/{pid}/fd"
    if os.path.isdir(fd_dir):
        try:
            for fd in os.listdir(fd_dir):
                try:
                    tgt = os.readlink(os.path.join(fd_dir, fd))
                except OSError:
                    continue
                if tgt.endswith(".jsonl") and root in tgt:
                    found.append(tgt)
        except OSError:
            pass
        return found
    # macOS/BSD: lsof.
    import subprocess
    try:
        out = subprocess.run(["lsof", "-p", str(pid), "-Fn"],
                             capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return found
    for ln in out.stdout.splitlines():
        if ln.startswith("n") and ln.endswith(".jsonl") and root in ln:
            found.append(ln[1:])
    return found


def find_transcript(shell_pid, cwd, ps_list=None, open_jsonl=None,
                    list_dir=None):
    """패널의 트랜스크립트 경로를 best-effort 로 찾는다(없으면 None).

    1차(견고): shell_pid 후손의 claude 프로세스가 연 projects 하위 `.jsonl`.
    2차(폴백): cwd → 프로젝트 디렉터리의 최신 mtime `.jsonl`.
    부수효과(ps_list·open_jsonl·list_dir)는 주입 가능(테스트)."""
    if shell_pid:
        cpid = claude_descendant_pid(shell_pid, ps_list)
        if cpid:
            opener = open_jsonl or _default_open_jsonl
            paths = opener(cpid)
            if paths:
                # 여러 개면 최신 mtime(현재 활성 세션이 append 중인 것).
                return _newest(paths, list_dir)
    if cwd:
        d = project_dir_for(cwd)
        return newest_in_dir(d, list_dir)
    return None


def _mtime(path, stat_fn=None):
    try:
        return (stat_fn or os.path.getmtime)(path)
    except OSError:
        return -1.0


def _newest(paths, stat_fn=None):
    paths = [p for p in paths if p]
    if not paths:
        return None
    return max(paths, key=lambda p: _mtime(p, stat_fn))


def newest_in_dir(d: str, list_dir=None, stat_fn=None):
    """디렉터리 d 의 `.jsonl` 중 최신 mtime(없으면 None). list_dir 주입 가능(테스트)."""
    if list_dir is not None:
        paths = list_dir(d)
    else:
        try:
            paths = glob.glob(os.path.join(d, "*.jsonl"))
        except OSError:
            return None
    return _newest(paths, stat_fn)
