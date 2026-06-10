"""ncd 서버 측 로직 — 디렉토리 나열·조상 사슬·드라이브 루트·사슬 빌드·응답 메시지.

textual 무관(순수 파일시스템). servertree.ServerTreeMixin 의 메서드였던 것을 플러그인
안의 함수로 옮겼다. cwd 추정만 코어의 일반 헬퍼(`server._resolve_start_cwd`)를 빌린다 —
그건 split/new-window 등도 쓰는 범용 기능이라 코어에 남는다."""
from __future__ import annotations

import os


def _list_dirs(path: str) -> list[str]:
    """`path` 의 직계 하위 **디렉토리**만 이름순(대소문자 무시)으로 나열해 전체경로
    리스트로 반환한다. nc 트리 표시·지연 펼치기용.

    - 파일은 제외(목적이 cd/패널 열기라 디렉토리만 의미 있다).
    - 숨김(`.` 시작) 디렉토리는 기본 제외(잡음 감소). 트리는 지연 로드이므로 재귀하지
      않고 한 단계만 읽어 대형/네트워크 디렉토리에서도 가볍다.
    - 권한 오류·경로 아님·심링크 깨짐 등은 빈 리스트로 graceful — nc 가 중간 노드에서
      죽지 않게 한다(개별 항목 오류도 건너뛴다)."""
    out: list[str] = []
    try:
        with os.scandir(path) as it:
            for e in it:
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir(follow_symlinks=True):
                        out.append(e.path)
                except OSError:
                    continue   # stat 실패(권한·깨진 심링크) 항목은 건너뜀
    except OSError:
        return []
    out.sort(key=lambda p: os.path.basename(p).lower())
    return out


def _ancestor_chain(cwd: str) -> list[str]:
    """`cwd` 의 조상 사슬을 루트부터 cwd 까지 순서대로 반환한다(둘 다 포함).
    예: /a/b/c → ['/', '/a', '/a/b', '/a/b/c']. ncd 초기 트리를 현재 디렉토리까지
    펼쳐 열기 위함."""
    cwd = os.path.abspath(cwd)
    parts: list[str] = []
    p = cwd
    while True:
        parts.append(p)
        parent = os.path.dirname(p)
        if parent == p:        # 루트('/'·드라이브) 도달
            break
        p = parent
    return list(reversed(parts))


def _drive_roots() -> list[str]:
    r"""Windows: 사용 가능한 드라이브 루트(["C:\\","D:\\",...]) 정렬 목록.
    그 외 OS: [](단일 루트 '/'). ncd 가 **드라이브 전환**을 위해 드라이브들을 트리
    최상위로 묶는 데 쓴다."""
    if os.name != "nt":
        return []
    try:
        return sorted(os.listdrives())      # Python 3.12+
    except (AttributeError, OSError):
        pass
    out = []
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        d = f"{c}:\\"
        if os.path.exists(d):
            out.append(d)
    return out


def _build_chain(chain_paths: list[str], drives: list[str]) -> list[list]:
    """루트→cwd 사슬을 [dir, [직계 하위]] 리스트로 만든다. `drives` 가 있으면
    (Windows) 맨 앞에 **합성 최상위('')**를 두고 그 자식으로 드라이브 목록을 실어
    드라이브를 트리 최상위 노드로 만든다(현재 드라이브가 빠지면 보강). 각 단계엔 다음
    사슬 원소를 보장 포함(숨김/누락 대비)해 펼친 경로가 끊기지 않게 한다."""
    chain: list[list] = []
    if drives:
        chain.append(["", sorted(set(drives) | {chain_paths[0]})])
    for i, p in enumerate(chain_paths):
        dirs = _list_dirs(p)
        if i + 1 < len(chain_paths):
            nxt = chain_paths[i + 1]
            if nxt not in dirs:
                dirs = sorted(dirs + [nxt],
                              key=lambda d: os.path.basename(
                                  d.rstrip("/\\")).lower())
        chain.append([p, dirs])
    return chain


def nc_list_msg(server, sess, path: str | None = None) -> dict:
    r"""ncd 디렉토리 목록 요청 응답.

    - `path` 가 있으면(노드 펼치기) 그 경로의 직계 하위를 `dirs` 로 회신하고 `path` 에
      절대경로를 echo 해 클라가 해당 노드를 매칭한다(지연 펼치기).
    - `path` 가 비면(초기 진입) **루트부터 현재 패널 cwd 까지의 사슬**을 만들어 각
      단계의 직계 하위와 함께 `chain` 으로 회신한다(+ `cwd`). 클라는 이 사슬을 펼친
      트리로 그리고 cwd 행에 커서를 둔다(NCD: 전체 트리·현재 위치 시작). **Windows**
      에선 드라이브 문자들을 묶는 합성 최상위('')를 맨 위에 둬 드라이브 전환이 되게
      한다(`root`=""). cwd 추정 불가 시 루트만 1단계.
    - 모든 경로는 **절대경로**(클라 경로 조합 버그 여지 제거; 표시명=basename)."""
    if path:
        root = os.path.abspath(os.path.expanduser(str(path)))
        return {"t": "nc_list", "root": root, "path": root,
                "dirs": _list_dirs(root)}
    cwd = server._resolve_start_cwd(sess, "current")
    cwd = os.path.abspath(cwd) if cwd else None
    chain_paths = _ancestor_chain(cwd) if cwd else [os.path.abspath(os.sep)]
    drives = _drive_roots()
    chain = _build_chain(chain_paths, drives)
    root = "" if drives else chain_paths[0]
    return {"t": "nc_list", "root": root, "path": None,
            "cwd": cwd, "chain": chain}
