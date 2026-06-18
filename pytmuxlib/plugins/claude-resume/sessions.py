"""Claude Code 세션 열거(순수 로직 — textual 의존 없음, 서버에서 import 가능).

Claude Code 는 대화 세션을 `~/.claude/projects/<cwd-슬러그>/<session-uuid>.jsonl` 로
저장한다(슬러그 = 작업 디렉토리 경로의 구분자를 '-' 로 치환). 각 jsonl 은 줄당 1 JSON
이벤트이고, 라벨에 쓸 만한 줄 타입:
  · `ai-title`  : {aiTitle}      — AI 생성 세션 제목(`/resume` 피커가 보여주는 제목)
  · `last-prompt`: {lastPrompt}  — 마지막 사용자 프롬프트
  · `user`      : {message:{content}} — 사용자 메시지(첫 메시지 폴백·빈 세션 판별)
  · 아무 줄    : {cwd}           — 세션의 작업 디렉토리(리줌 시 그리로 cd)

이 모듈은 디렉토리를 훑어 세션 메타(id·cwd·제목·mtime·프로젝트 라벨)를 최신순으로
돌려준다. 빈 세션(사용자 메시지 0)은 제외한다. 리줌 명령 실행·UI 는 __init__/screen 참조."""
from __future__ import annotations

import glob
import json
import os
import re

_TITLE_MAX = 200       # 제목 표시 상한(라벨 폭은 UI 가 다시 줄임)


def projects_dir() -> str:
    """이 머신의 Claude Code 세션 저장 루트(`~/.claude/projects`)."""
    return os.path.join(os.path.expanduser("~"), ".claude", "projects")


def _user_text(o: dict):
    """user 이벤트에서 텍스트 본문을 뽑는다(content = str 또는 [{type:text,text}])."""
    m = o.get("message")
    if not isinstance(m, dict):
        return None
    ct = m.get("content")
    if isinstance(ct, str):
        return ct
    if isinstance(ct, list):
        for p in ct:
            if isinstance(p, dict) and p.get("type") == "text":
                return p.get("text")
    return None


def _clean(s, n=_TITLE_MAX):
    """제목/프롬프트 1줄화 — 개행·과한 공백을 한 칸으로, 상한 길이로 자른다."""
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s[:n]


def _project_label(cwd, slug):
    """프로젝트 표시 라벨 — cwd 의 마지막 두 경로 요소(예: 'office/rx'). cwd 가 없으면
    슬러그(역변환은 손실이라 그대로)."""
    if cwd:
        parts = [p for p in re.split(r"[\\/]+", cwd.strip()) if p and p != "."]
        if len(parts) >= 2:
            return "/".join(parts[-2:])
        if parts:
            return parts[-1]
    return slug


def parse_session(path: str):
    """세션 jsonl 1개 → 메타 dict, 빈/읽기실패면 None. 제목 우선순위:
    ai-title > last-prompt > 첫 user 메시지 > session id."""
    session_id = os.path.splitext(os.path.basename(path))[0]
    ai_title = last_prompt = first_user = cwd = None
    n_user = 0
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    o = json.loads(ln)
                except (ValueError, TypeError):
                    continue
                if not isinstance(o, dict):
                    continue
                if not cwd and o.get("cwd"):
                    cwd = o["cwd"]
                t = o.get("type")
                if t == "ai-title" and o.get("aiTitle"):
                    ai_title = o["aiTitle"]
                elif t == "last-prompt" and o.get("lastPrompt"):
                    last_prompt = o["lastPrompt"]
                elif t == "user":
                    n_user += 1
                    if first_user is None:
                        first_user = _user_text(o)
    except OSError:
        return None
    if n_user == 0:
        return None                      # 빈 세션 — 리줌 대상 아님
    title = _clean(ai_title or last_prompt or first_user or session_id)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return {"id": session_id, "cwd": cwd, "title": title, "mtime": mtime,
            "ai_title": _clean(ai_title) if ai_title else None,
            "last_prompt": _clean(last_prompt) if last_prompt else None}


def list_sessions(root: str | None = None, limit: int | None = None) -> list:
    """이 머신의 모든 프로젝트 세션을 최신(mtime)순으로 돌려준다. 각 항목에 프로젝트
    표시 라벨(`project`)을 더한다. root 미지정 시 `projects_dir()`. 디렉토리/파일 접근
    실패는 조용히 건너뛴다(부분 결과라도 보여 준다)."""
    root = root or projects_dir()
    out = []
    try:
        slugs = os.listdir(root)
    except OSError:
        return out
    for slug in slugs:
        d = os.path.join(root, slug)
        if not os.path.isdir(d):
            continue
        for path in glob.glob(os.path.join(d, "*.jsonl")):
            s = parse_session(path)
            if s is not None:
                s["project"] = _project_label(s.get("cwd"), slug)
                out.append(s)
    out.sort(key=lambda s: s["mtime"], reverse=True)
    if limit:
        out = out[:limit]
    return out
