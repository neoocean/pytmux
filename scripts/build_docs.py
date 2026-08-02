#!/usr/bin/env python3
"""저장소의 모든 마크다운 문서를 `docs/internal/html/` 아래 GitBook 스타일 정적 사이트로 변환한다.

설계 원칙
---------
* **의존성 없음(런타임)** — 결과물은 순수 HTML/CSS/JS. CDN·프레임워크·빌드툴을 쓰지 않아
  `docs/internal/html/index.html` 을 `file://` 로 열어도 내비게이션·검색이 전부 동작한다.
  (검색 인덱스를 JSON 이 아니라 `window.__DOCS__` 를 채우는 **JS 파일**로 굽는 이유가
  이것이다 — `file://` 에서 `fetch()` 는 CORS 로 막히지만 `<script>` 는 로드된다.)
* **자기완결** — 문서가 참조하는 이미지는 `_media/` 로 복사하고 링크를 다시 쓴다.
  `docs/internal/html/` 디렉토리만 통째로 옮겨도 깨지지 않는다.
* **문서 간 링크 보존** — 저장소 경로를 그대로 미러링하므로(`docs/internal/X.md` →
  `docs/internal/X.html`) 문서끼리의 상대 링크가 자연히 살아있다. `.md` 확장자만 바꾼다.
  `[[WIKILINK]]` 문법도 파일명으로 해석해 링크로 만든다.

기본 빌드에는 GitHub 미러 차단 대상(`docs/internal/`·`memory/`·`.claude/`·`MEMORY.md`)이
**본문째 인라인**되므로 산출물 자체가 내부 문서다. 그래서 출력 위치를 이미 미러 차단
구역인 `docs/internal/` **안**에 둔다 — GitHub 유출은 `.gitignore` 의 `docs/internal/`
규칙이 구조적으로 막고, depot 적재는 `.p4ignore` 가 막는다(산출물이라 매 빌드 전량 갱신).
공개용 번들이 필요하면 `--public` 으로 그 넷을 통째로 제외한다(`PRIVATE_PREFIXES`).

사용법
------
    python3 scripts/build_docs.py                # docs/internal/html/ 생성(내부 문서 포함)
    python3 scripts/build_docs.py --public       # docs/internal/ 제외
    python3 scripts/build_docs.py --with-benchmarks   # 벤치마크 실행기록 1500여 건까지
    python3 scripts/build_docs.py --serve 8000   # 빌드 후 로컬 미리보기 서버
"""

from __future__ import annotations

import argparse
import html
import io
import os
import re
import shutil
import sys
from dataclasses import dataclass, field

try:
    from markdown_it import MarkdownIt
    from mdit_py_plugins.anchors import anchors_plugin
    from mdit_py_plugins.deflist import deflist_plugin
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.front_matter import front_matter_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin
except ImportError:  # pragma: no cover - 안내용
    sys.exit("markdown-it-py 가 필요합니다:  pip install markdown-it-py mdit-py-plugins pygments")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(REPO, "docs", "internal", "html")

# 저장소 루트 기준 사이트 루트(`docs/internal/html` → 깊이 3). 문서에서 저장소의 실제
# 파일(소스코드 등)로 잇는 상대경로가 이 깊이에 걸려 있어 예전에는 `+ 2` 로 박혀 있었다.
# OUT_DIR 을 옮기면 저 상수가 조용히 어긋나므로 OUT_DIR 에서 유도한다.
OUT_REL = os.path.relpath(OUT_DIR, REPO).replace("\\", "/")
OUT_DEPTH = OUT_REL.count("/") + 1

SITE_TITLE = "pytmux 문서"
SITE_TAGLINE = "Python/Textual 기반 tmux 유사 터미널 멀티플렉서"

# 걷지 않을 디렉토리(어느 깊이에서든).
SKIP_DIRS = {
    ".git", ".svn", "node_modules", "__pycache__", ".pytest_cache",
    ".pytmux", "reports", "db", "_dist", "captures", "html", ".venv", "venv",
}
MEDIA_EXT = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".avif"}

# GitHub 미러에 올리지 않는(= Perforce 전용) 문서. `.gitignore` 의 차단 규칙과 같은
# 목록이며 `--public` 이 통째로 제외한다. 여기가 어긋나면 공개 번들로 내부 문서가
# 새므로, `.gitignore` 에 미러 차단 규칙을 추가하면 이 목록도 같이 고친다.
PRIVATE_PREFIXES = ("docs/internal/", "memory/", ".claude/")
PRIVATE_FILES = ("MEMORY.md",)


def is_private(rel: str) -> bool:
    return rel.startswith(PRIVATE_PREFIXES) or rel in PRIVATE_FILES


# ─────────────────────────────────────────────────────────────────────────────
# 문서 모델 · 분류
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Doc:
    src: str            # 저장소 상대 경로 (POSIX 구분자)
    out: str            # 사이트 루트 기준 출력 경로 (.html)
    title: str = ""
    section: str = ""
    summary: str = ""
    internal: bool = False
    sort_key: tuple = ()
    body: str = ""      # 렌더된 HTML
    toc: list = field(default_factory=list)
    text: str = ""      # 검색용 평문
    headings: list = field(default_factory=list)


# 섹션 표시 순서. 아이콘은 이모지 대신 유니코드 기호(터미널 문서 톤 유지).
SECTIONS: list[tuple[str, str]] = [
    ("시작하기", "프로젝트 소개 · 에이전트 온보딩 · 랜딩 사이트"),
    ("설계 · 아키텍처", "핵심 설계 문서와 개발사 · 진행 인계"),
    ("Claude Code 연동", "Claude 패널 렌더 · 텍스트 편집 · 종료 감지"),
    ("토큰 · 사용량", "토큰 회계 · 저장 · 사용량 UI · 다중 머신 동기화"),
    ("원격 · 페더레이션", "ssh 원격 attach · 릴레이 · 중첩 세션"),
    ("Windows", "Windows 이식 · ConPTY · 재시작 · 환경 구성"),
    ("성능 · 벤치마크", "성능 측정 · 벤치마크 추이 · 파서 트레이드오프"),
    ("검토 · 보안", "코드 감사 · 보안 레드팀 검토 사이클"),
    ("시나리오 · 기능 설계", "기능별 시나리오 · 타당성 검토 · 설계안"),
    ("조사 · 트러블슈팅", "장애 조사 기록과 대처 절차"),
    ("세션 교훈", "세션별 LESSONS 누적 기록"),
    ("플러그인", "pytmuxlib/plugins 하위 플러그인 문서"),
    ("테스트 · 도구", "테스트 스위트 · 픽스처 · 보조 도구 · 스킬"),
    ("에이전트 메모", "에이전트 영속 메모리 색인"),
    ("기타", "위 분류에 들지 않는 문서"),
]
SECTION_ORDER = {name: i for i, (name, _) in enumerate(SECTIONS)}


def classify(rel: str) -> str:
    """저장소 상대 경로 → 섹션 이름. 규칙 순서가 곧 우선순위다."""
    base = os.path.basename(rel)
    stem = base[:-3] if base.endswith(".md") else base
    up = stem.upper()
    d = os.path.dirname(rel)

    # ── 경로 기반(가장 확실한 신호부터) ──
    if rel in ("README.md", "CLAUDE.md", "install.md"):
        return "시작하기"
    if rel == "MEMORY.md" or d == "memory":
        return "에이전트 메모"
    if d.startswith("pytmuxlib/plugins"):
        return "플러그인"
    if d.startswith("tests") or d.startswith("tools") or d.startswith(".claude") or d.startswith("scripts"):
        return "테스트 · 도구"
    if d.startswith("docs/internal/benchmark"):
        return "성능 · 벤치마크"
    if d.startswith("docs/landing"):
        return "시작하기"
    if d == "docs":
        return "시나리오 · 기능 설계"

    # ── docs/internal 이름 기반 ──
    if up.startswith("LESSONS_") or up in ("TESTING_LESSONS_FROM_SPACE", "SESSION_MODEL_FITNESS_LOG"):
        return "세션 교훈"
    if up.startswith("INVESTIGATION"):
        return "조사 · 트러블슈팅"
    if up in ("DESIGN", "PLUGIN_SYSTEM", "HISTORY", "HANDOFF", "HANDOFF_ARCHIVE",
              "IMPROVEMENT_OPPORTUNITIES") or up.startswith("ROADMAP"):
        return "설계 · 아키텍처"
    if "WINDOWS" in up or up == "ENV_SETUP_WINDOWS":
        return "Windows"
    if "REMOTE" in up or "NEST" in up or "RELAY" in up:
        return "원격 · 페더레이션"
    if "TOKEN" in up or "USAGE" in up:
        return "토큰 · 사용량"
    if "CLAUDE" in up:
        return "Claude Code 연동"
    if any(k in up for k in ("PERFORMANCE", "BENCHMARK", "LATENCY", "OVER_TIER",
                             "TRADEOFF", "AMBIGUOUS_WIDTH")):
        return "성능 · 벤치마크"
    if any(k in up for k in ("SECURITY", "REDTEAM", "REVIEW", "AUDIT", "VULN")):
        return "검토 · 보안"
    if any(k in up for k in ("TROUBLESHOOTING", "ORPHAN", "STALE", "_FAIL", "NOTE")) \
            or stem.startswith(("p4-", "haiku-")):
        return "조사 · 트러블슈팅"
    if any(k in up for k in ("SCENARIO", "DESIGN", "PLAN", "FEASIBILITY", "SOLUTION",
                             "MITIGATION", "STRATEGY", "IMPROVEMENT")):
        return "시나리오 · 기능 설계"
    return "기타"


def sort_key_for(doc: Doc) -> tuple:
    """섹션 내 정렬 키. 색인·개요 문서를 위로, LESSONS 는 최신순으로."""
    base = os.path.basename(doc.src)
    stem = base[:-3]
    up = stem.upper()

    pinned = {
        "README": 0, "CLAUDE": 1, "MEMORY": 2,
        "LESSONS_INDEX": 0, "HANDOFF": 0, "DESIGN": 1, "HISTORY": 2,
    }
    rank = pinned.get(up, 5)

    if up.startswith("LESSONS_") and up != "LESSONS_INDEX":
        # 날짜 내림차순(신→구). 접미사는 오름차순.
        m = re.match(r"LESSONS_(\d{4})-(\d{2})-(\d{2})([a-z]*)", stem, re.I)
        if m:
            y, mo, d, suf = m.groups()
            inv = 99999999 - int(f"{y}{mo}{d}")
            return (rank, f"{inv:08d}", suf, stem)
    return (rank, stem.lower(), "", stem)


# ─────────────────────────────────────────────────────────────────────────────
# 수집
# ─────────────────────────────────────────────────────────────────────────────

def is_benchmark_run(rel: str) -> bool:
    return (rel.startswith("docs/internal/benchmark/")
            and os.path.basename(rel) != "README.md"
            and re.match(r"\d{8}-\d{6}Z\.md$", os.path.basename(rel)) is not None)


def discover(public: bool, with_benchmarks: bool) -> list[Doc]:
    docs: list[Doc] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        rel_dir = os.path.relpath(dirpath, REPO).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        if rel_dir.startswith(OUT_REL):
            continue
        for fn in sorted(filenames):
            if not fn.lower().endswith(".md"):
                continue
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            if public and is_private(rel):
                continue
            if is_benchmark_run(rel) and not with_benchmarks:
                continue
            internal = is_private(rel)
            out = out_path_for(rel)
            docs.append(Doc(src=rel, out=out, internal=internal, section=classify(rel)))
    return docs


def out_path_for(rel: str) -> str:
    """저장소 상대 .md 경로 → 사이트 상대 .html 경로(경로 미러링)."""
    p = rel[:-3] + ".html"
    # 선행 점 디렉토리는 웹 서버가 숨기는 경우가 있어 이름을 바꾼다.
    parts = p.split("/")
    parts = [("_" + s[1:]) if s.startswith(".") else s for s in parts]
    return "/".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# 마크다운 변환
# ─────────────────────────────────────────────────────────────────────────────

WIKILINK_RE = re.compile(r"\[\[([^\[\]|]+?)(?:\|([^\[\]]+?))?\]\]")
TAG_RE = re.compile(r"<[^>]+>")


_SLUG_DROP = re.compile(r"[^\w가-힣ㄱ-ㅎㅏ-ㅣ\- ]", re.U)


class Slugger:
    """문서 단위 앵커 슬러그 생성기.

    Python-Markdown 의 기본 slugify 는 비ASCII 를 버려 한글 제목이 전부 빈 id 가 된다.
    여기서는 한글·영숫자를 살리고 문서 안에서만 유일하게 만든다(문서마다 reset).
    """

    def __init__(self) -> None:
        self.seen: dict[str, int] = {}

    def reset(self) -> None:
        self.seen.clear()

    def __call__(self, text: str) -> str:
        s = TAG_RE.sub("", text)
        s = re.sub(r"[`*_~]", "", s).strip().lower()
        s = _SLUG_DROP.sub("", s)
        s = re.sub(r"\s+", "-", s).strip("-") or "section"
        n = self.seen.get(s, 0)
        self.seen[s] = n + 1
        return s if not n else f"{s}-{n}"


def highlight_code(code: str, lang: str, attrs: str) -> str:
    """pygments 하이라이팅. 언어를 모르면 빈 문자열을 돌려 기본 렌더러에 맡긴다."""
    if not lang:
        return ""
    try:
        from pygments import highlight
        from pygments.formatters import HtmlFormatter
        from pygments.lexers import get_lexer_by_name
        lexer = get_lexer_by_name(lang, stripall=False)
    except Exception:
        return ""
    return highlight(code, lexer, HtmlFormatter(cssclass="codehilite", nowrap=False))


def make_md(slugger: Slugger) -> MarkdownIt:
    """CommonMark(GFM) 파서.

    Python-Markdown 이 아니라 markdown-it-py 를 쓰는 이유 — 이 저장소 문서가 전부
    CommonMark 문법으로 쓰여 있는데 Python-Markdown 은 Markdown.pl 규칙이라 세 가지가
    통째로 깨졌다: ①문단 바로 다음 줄의 목록(빈 줄 없음)이 리터럴 `- ` 텍스트로 남고
    (실측 1341곳) ②2칸 들여쓴 중첩 목록이 형제로 평탄화되며 ③인용문 안 `<details>`
    블록이 태그 짝을 잃어 뒤따르는 마크다운이 원문 그대로 새어 나온다.
    """
    md = (
        MarkdownIt("gfm-like", {"highlight": highlight_code, "html": True, "linkify": True})
        .use(front_matter_plugin)      # memory/*.md 의 YAML 머리말 제거
        .use(footnote_plugin)
        .use(deflist_plugin)
        .use(tasklists_plugin, enabled=True)
        .use(anchors_plugin, min_level=1, max_level=4, slug_func=slugger,
             permalink=True, permalinkSymbol="#", permalinkSpace=False)
    )
    md.options["typographer"] = False   # 기술문서의 따옴표·대시를 바꾸지 않는다
    return md


def convert(md: MarkdownIt, slugger: Slugger, src: str) -> tuple[str, list]:
    """(렌더된 HTML, 제목 트리). 토큰을 한 번만 파싱해 목차와 본문을 함께 얻는다."""
    slugger.reset()
    env: dict = {}
    tokens = md.parse(src, env)
    body = md.renderer.render(tokens, md.options, env)
    return body, heading_tree(tokens)


def heading_tree(tokens) -> list:
    """heading_open 토큰 → 중첩 목차 트리([{name,id,level,children}])."""
    flat = []
    for i, t in enumerate(tokens):
        if t.type != "heading_open":
            continue
        inline = tokens[i + 1] if i + 1 < len(tokens) else None
        text = inline.content if inline is not None and inline.type == "inline" else ""
        # 앵커 플러그인이 붙인 permalink 기호는 목차에서 뺀다.
        text = re.sub(r"\s*#\s*$", "", TAG_RE.sub("", text)).strip()
        flat.append({"level": int(t.tag[1]), "id": t.attrGet("id") or "",
                     "name": text, "children": []})

    root: list = []
    stack: list = []
    for h in flat:
        while stack and stack[-1]["level"] >= h["level"]:
            stack.pop()
        (stack[-1]["children"] if stack else root).append(h)
        stack.append(h)
    return root


def read_text(path: str) -> str:
    with io.open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def extract_title(raw: str, rel: str) -> tuple[str, str]:
    """(제목, 한 줄 요약). H1 이 없으면 파일명을 제목으로 쓴다."""
    title = ""
    summary = ""
    for line in raw.splitlines():
        s = line.strip()
        if not title and s.startswith("# "):
            title = s[2:].strip()
            continue
        if title and not summary:
            t = s.lstrip("> ").strip()
            t = re.sub(r"[*_`\[\]]", "", t)
            if t and not t.startswith("#") and not t.startswith("---") and not t.startswith("|"):
                summary = t
                break
    if not title:
        title = os.path.basename(rel)[:-3]
    return title.strip(), summary[:200]


def build_wiki_map(docs: list[Doc]) -> dict[str, str]:
    """[[NAME]] 해석용: 파일 stem(대소문자 무시) → 저장소 상대 .md 경로."""
    m: dict[str, str] = {}
    for d in docs:
        stem = os.path.basename(d.src)[:-3]
        m.setdefault(stem.lower(), d.src)
    return m


def preprocess(raw: str, rel: str, wiki: dict[str, str]) -> str:
    """[[WikiLink]] → 일반 마크다운 링크. 대상이 없으면 표시만 남긴다."""
    src_dir = os.path.dirname(rel)

    def repl(m: re.Match) -> str:
        name, label = m.group(1).strip(), m.group(2)
        target = wiki.get(name.lower()) or wiki.get(name.lower().replace(" ", "_"))
        text = (label or name).strip()
        if not target:
            return f"<span class=\"wikilink-missing\" title=\"대상 문서 없음\">{html.escape(text)}</span>"
        relpath = os.path.relpath(target, src_dir or ".").replace("\\", "/")
        return f"[{text}]({relpath})"

    return WIKILINK_RE.sub(repl, raw)


HREF_RE = re.compile(r'(<(?:a|img)\b[^>]*?\b(?:href|src)=")([^"]+)(")', re.I)


def rewrite_links(body: str, doc: Doc, known: dict[str, str], media: dict[str, str],
                  by_base: dict[str, str] | None = None) -> str:
    """문서 내 상대 링크를 사이트 구조에 맞게 다시 쓴다.

    * `*.md`  → 대응 `.html` (사이트 내 상대 경로)
    * 이미지  → `_media/` 로 복사한 사본
    * 그 밖의 저장소 파일 → 저장소 상대 경로 유지(외부 파일 표시)
    """
    src_dir = os.path.dirname(doc.src)
    out_dir = os.path.dirname(doc.out)

    def repl(m: re.Match) -> str:
        pre, url, post = m.groups()
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|//|#|data:)", url, re.I):
            return m.group(0)
        path, _, frag = url.partition("#")
        if not path:
            return m.group(0)
        try:
            target = os.path.normpath(os.path.join(src_dir, path)).replace("\\", "/")
        except ValueError:
            return m.group(0)
        target = target.lstrip("./")
        ext = os.path.splitext(target)[1].lower()

        if ext == ".md":
            # 색인 문서(MEMORY.md 등)는 경로 없이 파일명만 적는 관례라 경로가 안 맞는다.
            # 계산된 경로가 없으면 파일명으로 한 번 더 찾는다.
            hit = known.get(target)
            if hit is None and by_base:
                alt = by_base.get(os.path.basename(target).lower())
                hit = known.get(alt) if alt else None
            if hit is None:
                # 저장소에 없는 문서(위키로 옮겨간 MANUAL·CONTRIBUTING 등). 링크를 조용히
                # 404 로 두지 않고 표시를 남긴다.
                return (f'{pre}{html.escape(url)}" class="deadlink" '
                        f'title="이 저장소에 없는 문서입니다 (GitHub 위키로 이전했거나 삭제됨){post}')
            newp = os.path.relpath(hit, out_dir or ".").replace("\\", "/")
        elif ext in MEDIA_EXT and os.path.exists(os.path.join(REPO, target)):
            media_out = media.setdefault(target, "_media/" + target.replace("/", "__"))
            newp = os.path.relpath(media_out, out_dir or ".").replace("\\", "/")
        elif os.path.exists(os.path.join(REPO, target)):
            # 저장소의 실제 파일(소스코드 등) — 사이트 밖 상대경로로 이어 준다.
            depth = out_dir.count("/") + 1 if out_dir else 0
            newp = ("../" * (depth + OUT_DEPTH)) + target  # <OUT_REL>/<out_dir> → 저장소 루트
        else:
            return m.group(0)       # 존재하지 않는 대상은 건드리지 않는다
        return f"{pre}{html.escape(newp + ('#' + frag if frag else ''))}{post}"

    return HREF_RE.sub(repl, body)


def plain_text(body: str) -> str:
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", body, flags=re.S | re.I)
    t = TAG_RE.sub(" ", t)
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


# ─────────────────────────────────────────────────────────────────────────────
# 템플릿
# ─────────────────────────────────────────────────────────────────────────────

def rootrel(out: str) -> str:
    depth = out.count("/")
    return "../" * depth if depth else ""


# 사이드바는 페이지마다 인라인된다(깜빡임 없이 활성 항목이 바로 강조되고 JS 없이도
# 목록이 보인다). 그래서 한 섹션이 지나치게 길면 모든 페이지가 함께 무거워진다 —
# `--with-benchmarks` 의 실행기록 1548건이 그 경우다. 넘치는 만큼은 검색으로 넘긴다.
NAV_MAX_PER_SECTION = 150


def render_sidebar(groups, current: str) -> str:
    """섹션 → 문서 트리. 현재 문서가 든 섹션만 펼쳐서 낸다."""
    parts = ['<nav class="tree" aria-label="문서 목록">']
    for name, desc, items in groups:
        active = any(d.out == current for d in items)
        shown = items[:NAV_MAX_PER_SECTION]
        # 잘려 나간 자리에 현재 문서가 있으면 그 항목은 되살린다(활성 표시 유지).
        if len(items) > len(shown) and any(d.out == current for d in items[NAV_MAX_PER_SECTION:]):
            shown = shown[:-1] + [next(d for d in items if d.out == current)]
        parts.append(f'<details class="sec"{" open" if active else ""}>')
        parts.append(
            f'<summary><span class="sec-name">{html.escape(name)}</span>'
            f'<span class="sec-count">{len(items)}</span></summary>'
        )
        parts.append('<ul>')
        for d in shown:
            href = os.path.relpath(d.out, os.path.dirname(current) or ".").replace("\\", "/")
            cls = ' class="active"' if d.out == current else ""
            badge = '<i class="int" title="내부 문서(비공개)">•</i>' if d.internal else ""
            parts.append(
                f'<li><a{cls} href="{html.escape(href)}" '
                f'data-t="{html.escape(d.title.lower())}">{html.escape(d.title)}{badge}</a></li>'
            )
        if len(items) > len(shown):
            parts.append(f'<li class="navmore">…외 {len(items) - len(shown)}건 — '
                         f'검색(Ctrl K)으로 찾으세요</li>')
        parts.append('</ul></details>')
    parts.append("</nav>")
    return "".join(parts)


def render_toc(toc_tokens, level=0) -> str:
    if not toc_tokens or level > 1:
        return ""
    out = ["<ul>"]
    for t in toc_tokens:
        name = TAG_RE.sub("", t["name"]).strip()
        out.append(f'<li><a href="#{html.escape(t["id"])}">{html.escape(name)}</a>')
        out.append(render_toc(t.get("children") or [], level + 1))
        out.append("</li>")
    out.append("</ul>")
    return "".join(out)


def page_shell(*, title, root, sidebar, content, toc_html, crumbs, prev_next,
               source_note, is_home=False) -> str:
    toc_block = (
        f'<aside class="toc" aria-label="이 문서의 목차"><div class="toc-in">'
        f'<div class="toc-h">이 문서에서</div>{toc_html}</div></aside>'
    ) if toc_html else '<aside class="toc"></aside>'

    return f"""<!doctype html>
<html lang="ko" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · {SITE_TITLE}</title>
<link rel="stylesheet" href="{root}assets/style.css">
<script>(function(){{try{{var t=localStorage.getItem('pytmux-docs-theme');if(t)document.documentElement.dataset.theme=t;}}catch(e){{}}}})();</script>
</head>
<body class="{'home' if is_home else 'doc'}">
<header class="top">
  <button class="burger" aria-label="문서 목록 열기">☰</button>
  <a class="brand" href="{root}index.html"><span class="p">$</span> pytmux <span class="sub">docs</span></a>
  <button class="searchbtn" id="searchbtn">
    <span class="mag">⌕</span><span class="ph">문서 검색…</span><kbd>Ctrl K</kbd>
  </button>
  <button class="themebtn" id="themebtn" aria-label="테마 전환" title="테마 전환">◐</button>
</header>
<div class="layout">
  <aside class="side" id="side">
    <div class="side-in">
      <input class="navfilter" id="navfilter" type="search" placeholder="목록 좁히기…" aria-label="문서 목록 필터">
      {sidebar}
    </div>
  </aside>
  <main class="main">
    <article class="md-body" id="content">
      {crumbs}
      {content}
    </article>
    {source_note}
    {prev_next}
  </main>
  {toc_block}
</div>
<div class="scrim" id="scrim"></div>
<div class="searchmodal" id="searchmodal" hidden>
  <div class="searchbox" role="dialog" aria-modal="true" aria-label="문서 검색">
    <div class="searchtop"><span class="mag">⌕</span>
      <input id="q" type="search" placeholder="문서 전체 검색 (제목 · 제목줄 · 본문)" autocomplete="off" spellcheck="false">
      <kbd>Esc</kbd></div>
    <div class="results" id="results"><div class="hint">검색어를 입력하세요. 여러 낱말을 쓰면 모두 포함한 문서를 찾습니다.</div></div>
  </div>
</div>
<script>window.__ROOT__ = "{root}";</script>
<script src="{root}assets/app.js"></script>
</body>
</html>
"""


def crumbs_for(doc: Doc, root: str) -> str:
    bits = [f'<a href="{root}index.html">문서 홈</a>',
            f'<span>{html.escape(doc.section)}</span>']
    return ('<div class="crumbs">' + '<i>/</i>'.join(bits) + '</div>'
            + (f'<div class="notice int">내부 문서 — <code>{html.escape(doc.src)}</code> 는 '
               'Perforce 전용이며 GitHub 미러에 올라가지 않습니다.</div>' if doc.internal else ''))


def pager(prev: Doc | None, nxt: Doc | None, cur_out: str) -> str:
    if not prev and not nxt:
        return ""
    d = os.path.dirname(cur_out) or "."
    out = ['<nav class="pager">']
    if prev:
        h = os.path.relpath(prev.out, d).replace("\\", "/")
        out.append(f'<a class="pv" href="{html.escape(h)}"><small>← 이전</small>'
                   f'<b>{html.escape(prev.title)}</b></a>')
    else:
        out.append('<span></span>')
    if nxt:
        h = os.path.relpath(nxt.out, d).replace("\\", "/")
        out.append(f'<a class="nx" href="{html.escape(h)}"><small>다음 →</small>'
                   f'<b>{html.escape(nxt.title)}</b></a>')
    out.append("</nav>")
    return "".join(out)


def home_page(groups, total, generated_note) -> str:
    cards = []
    for name, desc, items in groups:
        if not items:
            continue
        first = items[0]
        links = "".join(
            f'<a href="{html.escape(d.out)}">{html.escape(d.title)}</a>' for d in items[:6]
        )
        more = (f'<a class="more" href="{html.escape(first.out)}">'
                f'+{len(items) - 6}건 더</a>') if len(items) > 6 else ""
        cards.append(f"""<section class="card">
  <h3>{html.escape(name)}<span class="n">{len(items)}</span></h3>
  <p>{html.escape(desc)}</p>
  <div class="links">{links}{more}</div>
</section>""")
    return f"""<div class="hero">
  <div class="eyebrow">문서 아카이브</div>
  <h1>pytmux <span class="grad">문서</span></h1>
  <p class="lede">{html.escape(SITE_TAGLINE)}. 저장소의 모든 마크다운 문서 <b>{total}건</b>을
  한곳에서 훑고 검색합니다.</p>
  <div class="hero-cta">
    <button class="cta" onclick="document.getElementById('searchbtn').click()">⌕ 문서 검색</button>
    <a class="cta ghost" href="README.html">프로젝트 README</a>
    <a class="cta ghost" href="CLAUDE.html">에이전트 온보딩</a>
  </div>
</div>
<div class="cards">{''.join(cards)}</div>
<p class="genfoot">{generated_note}</p>"""


# ─────────────────────────────────────────────────────────────────────────────
# 정적 자산
# ─────────────────────────────────────────────────────────────────────────────

def pygments_css() -> str:
    try:
        from pygments.formatters import HtmlFormatter
    except ImportError:
        return ""
    dark = HtmlFormatter(style="monokai").get_style_defs('[data-theme="dark"] .codehilite')
    light = HtmlFormatter(style="friendly").get_style_defs('[data-theme="light"] .codehilite')
    return dark + "\n" + light


STYLE_CSS = r"""
/* pytmux 문서 사이트 — 랜딩(docs/landing/styles.css) 팔레트를 따른 GitBook 레이아웃 */
:root {
  --bg:#14141f; --bg-soft:#1a1a28; --bg-card:#21212f; --bg-elev:#282838;
  --border:#34344a; --border-soft:#2a2a3c;
  --text:#e6e6ef; --text-dim:#a6a6bd; --text-faint:#74748c;
  --accent:#4ea3ff; --accent-2:#7c5cff; --accent-soft:rgba(78,163,255,.14);
  --green:#3fd17a; --amber:#ffcc66; --red:#ff6b6b; --pink:#ff5fa2;
  --mono:"SF Mono","JetBrains Mono","Fira Code",ui-monospace,Menlo,Consolas,"D2Coding",monospace;
  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI","Pretendard","Apple SD Gothic Neo","Malgun Gothic",system-ui,sans-serif;
  --side-w:310px; --toc-w:232px; --top-h:56px;
}
[data-theme="light"] {
  --bg:#ffffff; --bg-soft:#f7f8fb; --bg-card:#fbfbfd; --bg-elev:#f0f2f7;
  --border:#dfe3ec; --border-soft:#eaedf3;
  --text:#1c2030; --text-dim:#5b6377; --text-faint:#8b93a7;
  --accent:#1668d6; --accent-2:#6b46e5; --accent-soft:rgba(22,104,214,.10);
  --green:#12854b; --amber:#9a6b00; --red:#c62828;
}
*,*::before,*::after { box-sizing:border-box; }
html { scroll-behavior:smooth; scroll-padding-top:calc(var(--top-h) + 16px); }
body { margin:0; background:var(--bg); color:var(--text); font-family:var(--sans);
       line-height:1.75; -webkit-font-smoothing:antialiased; overflow-wrap:break-word; }
a { color:var(--accent); text-decoration:none; }
a:hover { text-decoration:underline; }

/* ── 상단바 ── */
.top { position:sticky; top:0; z-index:60; height:var(--top-h); display:flex; align-items:center;
       gap:14px; padding:0 18px; background:color-mix(in srgb, var(--bg) 88%, transparent);
       backdrop-filter:blur(12px); border-bottom:1px solid var(--border); }
.brand { font-family:var(--mono); font-weight:700; font-size:16px; color:var(--text); letter-spacing:-.4px; white-space:nowrap; }
.brand:hover { text-decoration:none; }
.brand .p { color:var(--accent); }
.brand .sub { color:var(--text-faint); font-weight:500; }
.burger { display:none; background:none; border:0; color:var(--text-dim); font-size:19px; cursor:pointer; padding:6px; }
.searchbtn { margin-left:auto; display:flex; align-items:center; gap:9px; min-width:min(340px,42vw);
             background:var(--bg-soft); border:1px solid var(--border); color:var(--text-faint);
             border-radius:9px; padding:7px 11px; font:inherit; font-size:13.5px; cursor:pointer; }
.searchbtn:hover { border-color:var(--accent); color:var(--text-dim); }
.searchbtn .ph { flex:1; text-align:left; }
.searchbtn .mag, .searchtop .mag { font-size:16px; color:var(--accent); }
kbd { font-family:var(--mono); font-size:11px; color:var(--text-faint);
      border:1px solid var(--border); border-bottom-width:2px; border-radius:5px; padding:1px 6px; }
.themebtn { background:none; border:1px solid var(--border); color:var(--text-dim);
            border-radius:8px; width:32px; height:32px; cursor:pointer; font-size:15px; }
.themebtn:hover { color:var(--text); border-color:var(--accent); }

/* ── 레이아웃 ── */
.layout { display:grid; grid-template-columns:var(--side-w) minmax(0,1fr) var(--toc-w);
          max-width:1580px; margin:0 auto; align-items:start; }
.side { position:sticky; top:var(--top-h); height:calc(100vh - var(--top-h));
        border-right:1px solid var(--border); background:var(--bg-soft); }
.side-in { height:100%; overflow-y:auto; overscroll-behavior:contain; padding:16px 10px 60px; }
.navfilter { width:100%; background:var(--bg); border:1px solid var(--border); color:var(--text);
             border-radius:8px; padding:7px 10px; font:inherit; font-size:13px; margin-bottom:12px; }
.navfilter:focus { outline:none; border-color:var(--accent); }
.tree details { margin:0 0 2px; }
.tree summary { list-style:none; cursor:pointer; display:flex; align-items:center; gap:8px;
                padding:7px 10px; border-radius:8px; color:var(--text-dim);
                font-size:13.5px; font-weight:600; user-select:none; }
.tree summary::-webkit-details-marker { display:none; }
.tree summary::before { content:"▸"; font-size:10px; color:var(--text-faint); transition:transform .15s; }
.tree details[open] > summary::before { transform:rotate(90deg); }
.tree summary:hover { background:var(--bg-elev); color:var(--text); }
.sec-name { flex:1; }
.sec-count { font-family:var(--mono); font-size:11px; color:var(--text-faint);
             background:var(--bg-elev); border-radius:20px; padding:1px 7px; }
.tree ul { list-style:none; margin:2px 0 8px; padding:0 0 0 10px; border-left:1px solid var(--border-soft); }
.tree li a { display:block; padding:5px 10px; border-radius:7px; color:var(--text-dim);
             font-size:13px; line-height:1.5; }
.tree li a:hover { background:var(--bg-elev); color:var(--text); text-decoration:none; }
.tree li a.active { background:var(--accent-soft); color:var(--accent); font-weight:600; }
.tree li a .int { color:var(--amber); font-style:normal; margin-left:5px; font-size:10px; vertical-align:2px; }
.tree li.navmore { padding:6px 10px; font-size:11.5px; color:var(--text-faint); }

.main { min-width:0; padding:30px 46px 96px; }
.toc { position:sticky; top:var(--top-h); height:calc(100vh - var(--top-h)); padding:26px 18px 40px 4px; }
.toc-in { max-height:100%; overflow-y:auto; border-left:1px solid var(--border); padding-left:16px; }
.toc-h { font-size:11.5px; letter-spacing:.09em; text-transform:uppercase; color:var(--text-faint);
         font-weight:700; margin-bottom:10px; }
.toc ul { list-style:none; margin:0; padding:0; }
.toc ul ul { padding-left:12px; }
.toc a { display:block; padding:3.5px 0; font-size:12.5px; color:var(--text-faint); line-height:1.45;
         border-left:2px solid transparent; margin-left:-16px; padding-left:14px; }
.toc a:hover { color:var(--text); text-decoration:none; }
.toc a.cur { color:var(--accent); border-left-color:var(--accent); }

/* ── 본문 마크다운 ── */
.md-body { max-width:900px; }
.crumbs { display:flex; gap:9px; align-items:center; font-size:12.5px; color:var(--text-faint); margin-bottom:18px; }
.crumbs i { font-style:normal; opacity:.5; }
.notice { border:1px solid var(--border); border-left:3px solid var(--amber); background:var(--bg-soft);
          border-radius:8px; padding:9px 14px; font-size:13px; color:var(--text-dim); margin:0 0 22px; }
.md-body h1 { font-size:clamp(27px,3.4vw,36px); line-height:1.25; letter-spacing:-.7px;
              margin:6px 0 20px; font-weight:800; }
.md-body h2 { font-size:23px; margin:52px 0 14px; padding-bottom:9px; border-bottom:1px solid var(--border);
              letter-spacing:-.3px; font-weight:700; }
.md-body h3 { font-size:18.5px; margin:34px 0 10px; font-weight:700; }
.md-body h4 { font-size:16px; margin:26px 0 8px; color:var(--text-dim); font-weight:700; }
.md-body h5, .md-body h6 { font-size:14.5px; margin:20px 0 6px; color:var(--text-dim); }
.md-body h1,.md-body h2,.md-body h3,.md-body h4 { scroll-margin-top:calc(var(--top-h) + 14px); }
.header-anchor { opacity:0; margin-left:9px; color:var(--text-faint); font-weight:400;
                 text-decoration:none; font-size:.8em; }
h1:hover .header-anchor, h2:hover .header-anchor,
h3:hover .header-anchor, h4:hover .header-anchor { opacity:1; }
.md-body .task-list-item { list-style:none; margin-left:-22px; }
.md-body .task-list-item input { margin-right:8px; }
.md-body s, .md-body del { color:var(--text-faint); }
.md-body .footnotes { font-size:13px; color:var(--text-dim); border-top:1px solid var(--border);
                      margin-top:44px; padding-top:12px; }
.md-body details { border:1px solid var(--border); border-radius:9px; padding:10px 14px;
                   margin:0 0 18px; background:var(--bg-soft); }
.md-body details > summary { cursor:pointer; font-weight:600; color:var(--text-dim); }
.md-body details[open] > summary { margin-bottom:10px; }
.md-body p { margin:0 0 16px; }
.md-body ul, .md-body ol { margin:0 0 16px; padding-left:26px; }
.md-body li { margin:5px 0; }
.md-body li > ul, .md-body li > ol { margin:6px 0; }
.md-body blockquote { margin:0 0 20px; padding:2px 18px; border-left:3px solid var(--accent);
                      background:var(--bg-soft); border-radius:0 8px 8px 0; color:var(--text-dim); }
.md-body blockquote p:last-child { margin-bottom:0; }
.md-body blockquote p:first-child { margin-top:14px; }
.md-body blockquote p { margin-bottom:14px; }
.md-body hr { border:0; border-top:1px solid var(--border); margin:36px 0; }
.md-body code { font-family:var(--mono); font-size:.875em; background:var(--bg-elev);
                border:1px solid var(--border-soft); border-radius:5px; padding:.1em .38em; }
.md-body pre { margin:0 0 20px; background:var(--bg-card); border:1px solid var(--border);
               border-radius:10px; padding:14px 16px; overflow-x:auto; line-height:1.6; }
.md-body pre code { background:none; border:0; padding:0; font-size:12.9px; }
.codehilite { position:relative; margin:0 0 20px; }
.codehilite pre { margin:0; }
.copybtn { position:absolute; top:8px; right:8px; background:var(--bg-elev); border:1px solid var(--border);
           color:var(--text-faint); border-radius:6px; font:inherit; font-size:11px; padding:3px 9px;
           cursor:pointer; opacity:0; transition:opacity .13s; }
.codehilite:hover .copybtn, .prewrap:hover .copybtn { opacity:1; }
.copybtn:hover { color:var(--text); border-color:var(--accent); }
.prewrap { position:relative; margin:0 0 20px; }
.prewrap pre { margin:0; }
.tablewrap { overflow-x:auto; margin:0 0 22px; border:1px solid var(--border); border-radius:10px; }
.md-body table { border-collapse:collapse; width:100%; font-size:13.5px; }
.md-body th, .md-body td { border-bottom:1px solid var(--border-soft); padding:9px 13px;
                           text-align:left; vertical-align:top; }
.md-body thead th { background:var(--bg-soft); color:var(--text); font-weight:700; white-space:nowrap;
                    border-bottom:1px solid var(--border); }
.md-body tbody tr:last-child td { border-bottom:0; }
.md-body tbody tr:hover { background:var(--bg-soft); }
.md-body img { max-width:100%; height:auto; border-radius:10px; border:1px solid var(--border); }
.wikilink-missing { color:var(--text-faint); border-bottom:1px dotted var(--text-faint); }
.md-body a.deadlink { color:var(--text-dim); text-decoration:line-through; cursor:help; }
.md-body a.deadlink::after { content:"↗ 없음"; font-size:10.5px; color:var(--text-faint);
                             margin-left:5px; text-decoration:none; display:inline-block; }
.md-body .admonition { border:1px solid var(--border); border-left:3px solid var(--accent);
                       background:var(--bg-soft); border-radius:0 8px 8px 0; padding:12px 16px; margin:0 0 20px; }
.md-body .admonition-title { font-weight:700; margin:0 0 6px; }
.md-body .footnote { font-size:13px; color:var(--text-dim); border-top:1px solid var(--border); margin-top:40px; }

.srcnote { max-width:900px; margin:40px 0 0; font-size:12.5px; color:var(--text-faint);
           border-top:1px solid var(--border); padding-top:14px; display:flex; gap:10px; flex-wrap:wrap; }
.srcnote code { font-family:var(--mono); background:var(--bg-elev); border-radius:5px; padding:1px 6px; }

.pager { max-width:900px; display:grid; grid-template-columns:1fr 1fr; gap:14px; margin-top:34px; }
.pager a { border:1px solid var(--border); border-radius:11px; padding:13px 16px; background:var(--bg-soft);
           display:flex; flex-direction:column; gap:3px; }
.pager a:hover { border-color:var(--accent); text-decoration:none; background:var(--bg-card); }
.pager small { color:var(--text-faint); font-size:11.5px; }
.pager b { color:var(--text); font-size:14px; font-weight:600; }
.pager .nx { text-align:right; }

/* ── 홈 ── */
body.home .layout { grid-template-columns:var(--side-w) minmax(0,1fr); }
body.home .main { padding:44px 46px 100px; }
body.home .md-body { max-width:1120px; }
.hero { padding:22px 0 42px; }
.eyebrow { display:inline-block; font-family:var(--mono); font-size:12.5px; color:var(--accent);
           background:var(--accent-soft); border:1px solid var(--accent-soft); border-radius:999px;
           padding:4px 13px; margin-bottom:18px; }
.hero h1 { font-size:clamp(34px,5vw,52px); margin:0 0 14px; letter-spacing:-1.4px; font-weight:800; line-height:1.1; }
.hero h1 .grad { background:linear-gradient(100deg,var(--accent),var(--accent-2));
                 -webkit-background-clip:text; background-clip:text; color:transparent; }
.hero .lede { font-size:17px; color:var(--text-dim); max-width:760px; margin:0 0 26px; }
.hero-cta { display:flex; gap:11px; flex-wrap:wrap; }
.cta { background:var(--accent); color:#06121f; border:1px solid var(--accent); border-radius:9px;
       padding:9px 18px; font:inherit; font-size:14px; font-weight:700; cursor:pointer; }
.cta:hover { text-decoration:none; filter:brightness(1.08); }
.cta.ghost { background:transparent; color:var(--text); border-color:var(--border); }
.cta.ghost:hover { border-color:var(--accent); color:var(--accent); }
.cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(310px,1fr)); gap:16px; }
.card { border:1px solid var(--border); border-radius:13px; padding:18px 20px; background:var(--bg-soft); }
.card:hover { border-color:var(--accent); }
.card h3 { margin:0 0 6px; font-size:16px; display:flex; align-items:center; gap:9px; }
.card h3 .n { font-family:var(--mono); font-size:11px; color:var(--text-faint);
              background:var(--bg-elev); border-radius:20px; padding:1px 8px; font-weight:500; }
.card p { margin:0 0 12px; font-size:13px; color:var(--text-faint); line-height:1.55; }
.card .links { display:flex; flex-direction:column; gap:1px; }
.card .links a { font-size:13px; color:var(--text-dim); padding:3px 0;
                 white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.card .links a:hover { color:var(--accent); text-decoration:none; }
.card .links .more { color:var(--text-faint); font-size:12px; margin-top:4px; }
.genfoot { margin-top:36px; font-size:12.5px; color:var(--text-faint); }

/* ── 검색 ── */
.searchmodal { position:fixed; inset:0; z-index:100; background:rgba(6,6,12,.62);
               backdrop-filter:blur(3px); display:flex; justify-content:center; padding:9vh 18px 18px; }
.searchmodal[hidden] { display:none; }
.searchbox { width:min(760px,100%); max-height:78vh; display:flex; flex-direction:column;
             background:var(--bg-card); border:1px solid var(--border); border-radius:14px;
             box-shadow:0 24px 70px rgba(0,0,0,.5); overflow:hidden; }
.searchtop { display:flex; align-items:center; gap:11px; padding:14px 17px; border-bottom:1px solid var(--border); }
.searchtop input { flex:1; background:none; border:0; color:var(--text); font:inherit; font-size:16px; outline:none; }
.results { overflow-y:auto; padding:8px; }
.hint, .nores { padding:26px 16px; color:var(--text-faint); font-size:13.5px; text-align:center; }
.res { display:block; padding:10px 13px; border-radius:9px; border:1px solid transparent; }
.res:hover, .res.sel { background:var(--bg-elev); border-color:var(--border); text-decoration:none; }
.res .rt { color:var(--text); font-size:14.5px; font-weight:600; display:flex; align-items:baseline; gap:9px; }
.res .rs { font-size:11px; color:var(--text-faint); font-weight:500; background:var(--bg-soft);
           border:1px solid var(--border-soft); border-radius:20px; padding:0 8px; white-space:nowrap; }
.res .rh { font-size:12px; color:var(--accent); margin-top:2px; }
.res .rp { color:var(--text-dim); font-size:12.5px; margin-top:3px; line-height:1.5;
           display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.res mark { background:rgba(255,204,102,.24); color:var(--amber); border-radius:3px; padding:0 2px; }
.resfoot { padding:8px 15px; border-top:1px solid var(--border); color:var(--text-faint);
           font-size:11.5px; display:flex; gap:14px; }

.scrim { display:none; }
@media (max-width:1200px) {
  .layout { grid-template-columns:var(--side-w) minmax(0,1fr); }
  .toc { display:none; }
}
@media (max-width:900px) {
  .layout, body.home .layout { grid-template-columns:minmax(0,1fr); }
  .burger { display:block; }
  .side { position:fixed; top:var(--top-h); left:0; width:min(320px,86vw); z-index:70;
          transform:translateX(-102%); transition:transform .2s ease; }
  body.navopen .side { transform:none; }
  body.navopen .scrim { display:block; position:fixed; inset:var(--top-h) 0 0; z-index:65;
                        background:rgba(6,6,12,.55); }
  .main, body.home .main { padding:22px 18px 80px; }
  .searchbtn { min-width:0; }
  .searchbtn .ph, .searchbtn kbd { display:none; }
  .pager { grid-template-columns:1fr; }
}
@media print {
  .top,.side,.toc,.pager,.searchmodal,.crumbs { display:none !important; }
  .layout { display:block; } .main { padding:0; } .md-body { max-width:none; }
}
"""

APP_JS = r"""/* pytmux 문서 사이트 — 내비게이션 · 검색 · 테마.
   검색 인덱스는 file:// 에서도 읽히도록 JSON fetch 가 아니라 <script> 로 늦게 붙인다. */
(function () {
  var ROOT = window.__ROOT__ || "";
  var doc = document;
  var $ = function (s, r) { return (r || doc).querySelector(s); };

  /* ── 테마 ── */
  var themebtn = $("#themebtn");
  if (themebtn) themebtn.addEventListener("click", function () {
    var next = doc.documentElement.dataset.theme === "light" ? "dark" : "light";
    doc.documentElement.dataset.theme = next;
    try { localStorage.setItem("pytmux-docs-theme", next); } catch (e) {}
  });

  /* ── 모바일 사이드바 ── */
  var burger = $(".burger"), scrim = $("#scrim");
  function closeNav() { doc.body.classList.remove("navopen"); }
  if (burger) burger.addEventListener("click", function () { doc.body.classList.toggle("navopen"); });
  if (scrim) scrim.addEventListener("click", closeNav);

  /* ── 사이드바를 활성 항목 위치로 스크롤 ── */
  var act = $(".tree a.active");
  if (act) {
    var box = $(".side-in");
    var top = act.offsetTop - box.clientHeight / 2;
    if (top > 0) box.scrollTop = top;
  }

  /* ── 사이드바 필터 ── */
  var nf = $("#navfilter");
  if (nf) nf.addEventListener("input", function () {
    var q = nf.value.trim().toLowerCase();
    doc.querySelectorAll(".tree details").forEach(function (d) {
      var hit = 0;
      d.querySelectorAll("li").forEach(function (li) {
        var a = li.querySelector("a");
        var ok = !q || (a.getAttribute("data-t") || "").indexOf(q) >= 0;
        li.style.display = ok ? "" : "none";
        if (ok) hit++;
      });
      d.style.display = (!q || hit) ? "" : "none";
      if (q) d.open = true;
    });
  });

  /* ── 코드블록 복사 버튼 + 표 가로스크롤 래퍼 ── */
  doc.querySelectorAll("#content pre").forEach(function (pre) {
    var host = pre.parentElement;
    if (!host.classList.contains("codehilite")) {
      var w = doc.createElement("div"); w.className = "prewrap";
      pre.parentNode.insertBefore(w, pre); w.appendChild(pre); host = w;
    }
    var b = doc.createElement("button");
    b.className = "copybtn"; b.type = "button"; b.textContent = "복사";
    b.addEventListener("click", function () {
      var t = pre.innerText;
      var done = function () { b.textContent = "복사됨"; setTimeout(function () { b.textContent = "복사"; }, 1400); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(t).then(done, done);
      } else {
        var ta = doc.createElement("textarea"); ta.value = t; doc.body.appendChild(ta);
        ta.select(); try { doc.execCommand("copy"); } catch (e) {} doc.body.removeChild(ta); done();
      }
    });
    host.appendChild(b);
  });
  doc.querySelectorAll("#content table").forEach(function (t) {
    if (t.parentElement.classList.contains("tablewrap")) return;
    var w = doc.createElement("div"); w.className = "tablewrap";
    t.parentNode.insertBefore(w, t); w.appendChild(t);
  });

  /* ── 우측 목차 스크롤 추적 ── */
  var tocLinks = [].slice.call(doc.querySelectorAll(".toc a"));
  if (tocLinks.length) {
    var targets = tocLinks.map(function (a) {
      try { return doc.getElementById(decodeURIComponent(a.getAttribute("href").slice(1))); }
      catch (e) { return null; }
    });
    var tick = function () {
      var best = -1, y = window.scrollY + 120;
      targets.forEach(function (el, i) { if (el && el.offsetTop <= y) best = i; });
      tocLinks.forEach(function (a, i) { a.classList.toggle("cur", i === best); });
    };
    var raf = 0;
    window.addEventListener("scroll", function () {
      if (raf) return;
      raf = requestAnimationFrame(function () { raf = 0; tick(); });
    }, { passive: true });
    tick();
  }

  /* ── 검색 ── */
  var modal = $("#searchmodal"), q = $("#q"), results = $("#results"), sel = -1, rows = [];
  var loaded = false, loading = false;

  function loadIndex(cb) {
    if (loaded) return cb();
    if (loading) return;
    loading = true;
    results.innerHTML = '<div class="hint">검색 인덱스를 불러오는 중…</div>';
    var s = doc.createElement("script");
    s.src = ROOT + "assets/search-index.js";
    s.onload = function () { loaded = true; loading = false; cb(); };
    s.onerror = function () {
      loading = false;
      results.innerHTML = '<div class="nores">검색 인덱스를 불러오지 못했습니다 (assets/search-index.js).</div>';
    };
    doc.head.appendChild(s);
  }

  function openSearch() {
    modal.hidden = false; q.value = ""; sel = -1; rows = [];
    results.innerHTML = '<div class="hint">검색어를 입력하세요. 여러 낱말을 쓰면 모두 포함한 문서를 찾습니다.</div>';
    loadIndex(function () { q.focus(); });
    q.focus();
  }
  function closeSearch() { modal.hidden = true; }

  var sb = $("#searchbtn");
  if (sb) sb.addEventListener("click", openSearch);
  modal.addEventListener("click", function (e) { if (e.target === modal) closeSearch(); });

  doc.addEventListener("keydown", function (e) {
    var typing = /^(INPUT|TEXTAREA|SELECT)$/.test((e.target.tagName || ""));
    if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")) { e.preventDefault(); openSearch(); return; }
    if (e.key === "/" && !typing && modal.hidden) { e.preventDefault(); openSearch(); return; }
    if (modal.hidden) return;
    if (e.key === "Escape") { closeSearch(); return; }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!rows.length) return;
      sel = (sel + (e.key === "ArrowDown" ? 1 : rows.length - 1)) % rows.length;
      rows.forEach(function (r, i) { r.classList.toggle("sel", i === sel); });
      rows[sel].scrollIntoView({ block: "nearest" });
    } else if (e.key === "Enter" && sel >= 0 && rows[sel]) {
      window.location.href = rows[sel].getAttribute("href");
    }
  });

  function esc(s) { return s.replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }

  function highlight(text, terms) {
    var out = esc(text), i;
    for (i = 0; i < terms.length; i++) {
      var re = new RegExp("(" + terms[i].replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + ")", "gi");
      out = out.replace(re, "\u0001$1\u0002");
    }
    return out.replace(/\u0001/g, "<mark>").replace(/\u0002/g, "</mark>");
  }

  /* 소문자 사본은 문서당 최초 1회만 만들어 캐시한다(인덱스는 원문만 싣는다). */
  function lowBody(d) { return d._b || (d._b = d.b.toLowerCase()); }
  function lowHeads(d) {
    return d._k || (d._k = d.h.map(function (x) { return x[0]; }).join(" ").toLowerCase());
  }

  function snippet(d, terms) {
    var body = d.b, low = lowBody(d), at = -1, i;
    for (i = 0; i < terms.length; i++) {
      var p = low.indexOf(terms[i]);
      if (p >= 0 && (at < 0 || p < at)) at = p;
    }
    if (at < 0) return body.slice(0, 160);
    var s = Math.max(0, at - 60);
    return (s ? "… " : "") + body.slice(s, s + 220) + (s + 220 < body.length ? " …" : "");
  }

  function run() {
    var raw = q.value.trim();
    if (!raw) {
      results.innerHTML = '<div class="hint">검색어를 입력하세요. 여러 낱말을 쓰면 모두 포함한 문서를 찾습니다.</div>';
      rows = []; sel = -1; return;
    }
    var data = window.__DOCS__ || [];
    var terms = raw.toLowerCase().split(/\s+/).filter(Boolean);
    var hits = [];
    for (var i = 0; i < data.length; i++) {
      var d = data[i];
      var t = d.t.toLowerCase(), h = lowHeads(d), b = lowBody(d);
      var score = 0, ok = true;
      for (var j = 0; j < terms.length; j++) {
        var term = terms[j], s = 0;
        if (t.indexOf(term) >= 0) s += 160;
        if (h.indexOf(term) >= 0) s += 34;
        var c = b.split(term).length - 1;
        if (c) s += Math.min(c, 12) * 3;
        if (!s) { ok = false; break; }
        score += s;
      }
      if (!ok) continue;
      if (t === raw.toLowerCase()) score += 500;
      hits.push([score, d]);
    }
    hits.sort(function (a, b) { return b[0] - a[0]; });
    if (!hits.length) {
      results.innerHTML = '<div class="nores">일치하는 문서가 없습니다.</div>';
      rows = []; sel = -1; return;
    }
    var total = hits.length;
    hits = hits.slice(0, 50);
    var out = hits.map(function (p) {
      var d = p[1], href = ROOT + d.p, hl = "";
      /* 제목줄이 걸리면 그 앵커로 바로 보낸다 */
      for (var m = 0; m < d.h.length; m++) {
        var hd = d.h[m];
        var all = terms.every(function (tm) { return hd[0].toLowerCase().indexOf(tm) >= 0; });
        if (all) { href += "#" + hd[1]; hl = hd[0]; break; }
      }
      return '<a class="res" href="' + esc(href) + '">'
        + '<div class="rt">' + highlight(d.t, terms) + '<span class="rs">' + esc(d.s) + '</span></div>'
        + (hl ? '<div class="rh">§ ' + highlight(hl, terms) + '</div>' : '')
        + '<div class="rp">' + highlight(snippet(d, terms), terms) + '</div></a>';
    }).join("");
    results.innerHTML = out
      + '<div class="resfoot"><span>' + total + '건 중 ' + hits.length + '건 표시</span>'
      + '<span>↑↓ 이동 · Enter 열기 · Esc 닫기</span></div>';
    rows = [].slice.call(results.querySelectorAll(".res"));
    sel = -1;
  }

  var t0 = 0;
  q.addEventListener("input", function () {
    clearTimeout(t0);
    t0 = setTimeout(function () { loadIndex(run); if (loaded) run(); }, 90);
  });
})();
"""


# ─────────────────────────────────────────────────────────────────────────────
# 빌드
# ─────────────────────────────────────────────────────────────────────────────

def js_string(s: str) -> str:
    """JS 리터럴로 안전한 문자열(</script> 조기 종료 방지 포함)."""
    import json
    return json.dumps(s, ensure_ascii=False).replace("</", "<\\/")


def build(public: bool, with_benchmarks: bool) -> int:
    docs = discover(public, with_benchmarks)
    if not docs:
        sys.exit("변환할 마크다운 문서를 찾지 못했습니다.")

    wiki = build_wiki_map(docs)
    known = {d.src: d.out for d in docs}
    by_base: dict[str, str] = {}
    for d in docs:                      # 파일명 → 저장소 경로(중복이면 먼저 온 것)
        by_base.setdefault(os.path.basename(d.src).lower(), d.src)
    media: dict[str, str] = {}

    slugger = Slugger()
    md = make_md(slugger)
    for d in docs:
        raw = read_text(os.path.join(REPO, d.src.replace("/", os.sep)))
        d.title, d.summary = extract_title(raw, d.src)
        body, d.toc = convert(md, slugger, preprocess(raw, d.src, wiki))
        d.body = rewrite_links(body, d, known, media, by_base)
        d.text = plain_text(d.body)
        d.headings = collect_headings(d.toc)
        d.sort_key = sort_key_for(d)

    # 섹션 그룹 구성
    groups = []
    for name, desc in SECTIONS:
        items = sorted((d for d in docs if d.section == name), key=lambda x: x.sort_key)
        if items:
            groups.append((name, desc, items))

    flat = [d for _, _, items in groups for d in items]

    # 출력 디렉토리 초기화
    reset_out_dir()
    os.makedirs(os.path.join(OUT_DIR, "assets"), exist_ok=True)

    # 페이지
    for i, d in enumerate(flat):
        root = rootrel(d.out)
        sidebar = render_sidebar(groups, d.out)
        toc_html = render_toc(d.toc[0]["children"] if len(d.toc) == 1 else d.toc)
        depth_to_repo = "../" * (d.out.count("/") + OUT_DEPTH)
        srcnote = (f'<div class="srcnote"><span>원본 <code>{html.escape(d.src)}</code></span>'
                   f'<a href="{depth_to_repo}{html.escape(d.src)}">마크다운 원문 열기</a></div>')
        page = page_shell(
            title=d.title, root=root, sidebar=sidebar,
            content=d.body, toc_html=toc_html,
            crumbs=crumbs_for(d, root),
            prev_next=pager(flat[i - 1] if i else None,
                            flat[i + 1] if i + 1 < len(flat) else None, d.out),
            source_note=srcnote,
        )
        write(os.path.join(OUT_DIR, d.out.replace("/", os.sep)), page)

    # 홈
    gen_note = (f"문서 {len(flat)}건 · <code>scripts/build_docs.py</code> 로 생성. "
                f"저장소의 마크다운을 고치고 다시 실행하면 갱신됩니다."
                + ("" if with_benchmarks else
                   " 벤치마크 실행기록(<code>docs/internal/benchmark/&lt;os&gt;/*.md</code>)은 "
                   "<code>--with-benchmarks</code> 로 포함할 수 있습니다."))
    home = page_shell(
        title="문서 홈", root="", sidebar=render_sidebar(groups, "index.html"),
        content=home_page(groups, len(flat), gen_note), toc_html="", crumbs="",
        prev_next="", source_note="", is_home=True,
    )
    write(os.path.join(OUT_DIR, "index.html"), home)

    # 자산
    write(os.path.join(OUT_DIR, "assets", "style.css"), STYLE_CSS + "\n" + pygments_css())
    write(os.path.join(OUT_DIR, "assets", "app.js"), APP_JS)
    write(os.path.join(OUT_DIR, "assets", "search-index.js"), search_index_js(flat))

    # 이미지 동봉
    for src, dst in sorted(media.items()):
        s = os.path.join(REPO, src.replace("/", os.sep))
        t = os.path.join(OUT_DIR, dst.replace("/", os.sep))
        os.makedirs(os.path.dirname(t), exist_ok=True)
        shutil.copy2(s, t)

    print(f"  문서 {len(flat)}건 · 섹션 {len(groups)}개 · 이미지 {len(media)}건")
    for name, _, items in groups:
        print(f"    {name:<20} {len(items):>4}")
    return len(flat)


def collect_headings(tokens, out=None) -> list:
    out = out if out is not None else []
    for t in tokens:
        out.append([TAG_RE.sub("", t["name"]).strip(), t["id"]])
        collect_headings(t.get("children") or [], out)
    return out


BODY_CAP = 14000  # 문서당 색인 본문 상한(인덱스 크기 제어)


def search_index_js(docs: list[Doc]) -> str:
    """검색 인덱스. 소문자 사본은 굽지 않는다 — 클라이언트가 최초 1회 만들어 캐시하므로
    (app.js 의 `d._b`/`d._k`) 인덱스 크기가 절반이고 스니펫도 원문 대소문자를 유지한다."""
    rows = []
    for d in docs:
        heads = d.headings[:120]
        rows.append(
            "{p:%s,t:%s,s:%s,h:%s,b:%s}" % (
                js_string(d.out), js_string(d.title), js_string(d.section),
                "[" + ",".join("[%s,%s]" % (js_string(h[0]), js_string(h[1])) for h in heads) + "]",
                js_string(d.text[:BODY_CAP]),
            )
        )
    return ("/* 자동 생성 — scripts/build_docs.py. 검색 인덱스(첫 검색 시 지연 로드). */\n"
            "window.__DOCS__=[\n" + ",\n".join(rows) + "\n];\n")


def reset_out_dir(attempts: int = 5) -> None:
    """`docs/internal/html/` 비우기. Windows 에서는 인덱서·백신·미리보기 서버가 방금 쓴 파일을
    잠깐 물고 있어 `rmtree` 가 WinError 32 로 중간에 실패한다(실측 — 1768쪽 빌드 직후).
    한 번의 실패로 산출물을 반쯤 지운 채 끝내지 말고 잠깐 쉬었다 다시 시도한다."""
    import time
    if not os.path.isdir(OUT_DIR):
        return
    for i in range(attempts):
        try:
            shutil.rmtree(OUT_DIR)
            return
        except PermissionError as exc:
            if i == attempts - 1:
                sys.exit(f"{OUT_DIR} 를 비우지 못했습니다({exc}).\n"
                         f"미리보기 서버나 파일 탐색기가 이 디렉토리를 잡고 있는지 "
                         f"확인한 뒤 다시 실행하세요.")
            time.sleep(0.6 * (i + 1))


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def main() -> None:
    ap = argparse.ArgumentParser(description="저장소 마크다운 → docs/internal/html 정적 사이트")
    ap.add_argument("--public", action="store_true",
                    help="미러 차단 문서(docs/internal/·memory/·.claude/·MEMORY.md)를 제외한다")
    ap.add_argument("--with-benchmarks", action="store_true",
                    help="docs/internal/benchmark/<os>/*.md 실행기록까지 포함한다 "
                         "(약 1770쪽·166MB — 기본 219쪽·23MB)")
    ap.add_argument("--serve", type=int, metavar="PORT",
                    help="빌드 후 해당 포트로 로컬 미리보기 서버를 띄운다")
    args = ap.parse_args()

    print(f"pytmux 문서 사이트 빌드 → {os.path.relpath(OUT_DIR, REPO)}")
    n = build(args.public, args.with_benchmarks)
    print(f"완료: {os.path.join(os.path.relpath(OUT_DIR, REPO), 'index.html')} ({n}건)")

    if args.serve:
        import functools
        import http.server
        import socketserver
        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=OUT_DIR)
        with socketserver.TCPServer(("127.0.0.1", args.serve), handler) as httpd:
            print(f"미리보기: http://127.0.0.1:{args.serve}/  (Ctrl+C 로 중지)")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print()


if __name__ == "__main__":
    main()
