"""claude-resume 세션 열거(데이터 레이어) 회귀 — 마일스톤 1.

하이픈 패키지라 importlib 로 가져온다. 실제 ~/.claude 를 건드리지 않고 임시 디렉토리에
가짜 프로젝트 슬러그 + jsonl 픽스처를 만들어 검증한다."""
import importlib
import json
import os
import time

import harness  # noqa: F401  (sys.path 주입)

sessions = importlib.import_module("pytmuxlib.plugins.claude-resume.sessions")


def _write(d, name, lines):
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        for o in lines:
            f.write(json.dumps(o) + "\n")
    return p


def test_parse_session_title_precedence_and_meta(tmp_path):
    d = str(tmp_path / "D--p4-office-scripts-pytmux")
    # ai-title 가 있으면 그게 제목
    p = _write(d, "11111111-1111-1111-1111-111111111111.jsonl", [
        {"type": "user", "cwd": "D:\\p4\\office\\scripts\\pytmux",
         "message": {"content": "첫 질문"}},
        {"type": "last-prompt", "lastPrompt": "마지막 프롬프트"},
        {"type": "ai-title", "aiTitle": "노트북 탭 색상 변경"},
    ])
    s = sessions.parse_session(p)
    assert s["id"] == "11111111-1111-1111-1111-111111111111"
    assert s["title"] == "노트북 탭 색상 변경"        # ai-title 우선
    assert s["cwd"] == "D:\\p4\\office\\scripts\\pytmux"
    assert s["ai_title"] == "노트북 탭 색상 변경"
    assert s["last_prompt"] == "마지막 프롬프트"


def test_parse_session_falls_back_to_prompt_then_user(tmp_path):
    d = str(tmp_path / "proj")
    # ai-title 없음 → last-prompt
    p1 = _write(d, "a.jsonl", [
        {"type": "user", "message": {"content": "유저 텍스트"}},
        {"type": "last-prompt", "lastPrompt": "프롬프트 폴백"},
    ])
    assert sessions.parse_session(p1)["title"] == "프롬프트 폴백"
    # ai-title·last-prompt 없음 → 첫 user 메시지(리스트 content 도 처리)
    p2 = _write(d, "b.jsonl", [
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "리스트형 유저 텍스트"}]}},
    ])
    assert sessions.parse_session(p2)["title"] == "리스트형 유저 텍스트"


def test_parse_session_skips_empty(tmp_path):
    d = str(tmp_path / "proj")
    # user 메시지 0 → 빈 세션 → None
    p = _write(d, "empty.jsonl", [
        {"type": "ai-title", "aiTitle": "제목만 있고 대화 없음"},
        {"type": "mode", "mode": "x"},
    ])
    assert sessions.parse_session(p) is None


def test_clean_collapses_whitespace_and_truncates():
    assert sessions._clean("  여러   줄\n그리고\t탭  ") == "여러 줄 그리고 탭"
    long = "x" * 500
    assert len(sessions._clean(long)) == sessions._TITLE_MAX


def test_project_label_from_cwd_last_two_parts():
    assert sessions._project_label("D:\\p4\\office\\rx", "slug") == "office/rx"
    assert sessions._project_label("/home/me/proj", "slug") == "me/proj"
    assert sessions._project_label("", "fallback-slug") == "fallback-slug"


def test_list_sessions_sorted_newest_first_with_project(tmp_path):
    root = str(tmp_path)
    d1 = os.path.join(root, "D--p4-office-rx")
    d2 = os.path.join(root, "D--p4-office-scripts-pytmux")
    older = _write(d1, "old.jsonl", [
        {"type": "user", "cwd": "D:\\p4\\office\\rx",
         "message": {"content": "오래된 세션"}},
        {"type": "ai-title", "aiTitle": "오래된"},
    ])
    newer = _write(d2, "new.jsonl", [
        {"type": "user", "cwd": "D:\\p4\\office\\scripts\\pytmux",
         "message": {"content": "새 세션"}},
        {"type": "ai-title", "aiTitle": "새것"},
    ])
    # mtime 을 명시적으로 벌려 정렬을 결정적이게
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_700_000_900, 1_700_000_900))
    out = sessions.list_sessions(root)
    assert [s["title"] for s in out] == ["새것", "오래된"]      # 최신 먼저
    assert out[0]["project"] == "scripts/pytmux"
    assert out[1]["project"] == "office/rx"
    # limit 적용
    assert len(sessions.list_sessions(root, limit=1)) == 1


def test_list_sessions_missing_root_returns_empty(tmp_path):
    assert sessions.list_sessions(str(tmp_path / "nope")) == []
