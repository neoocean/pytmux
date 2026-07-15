"""mdir — Mdir III(엠디르) 풍 파일 관리자 모달 화면.

Mdir III 3.10(1998, 최정한) 실물 재현(archive.org 원본 배포판·스크린샷 기준):
  - **검정 바탕 1-패널 다열 파일 리스트**(노턴류 2-pane 아님), 열 사이 `│` 구분.
  - 리스트 맨 앞 `..`(`[ Up-Dir ]`), 디렉토리(`[ SubDir ]`·붉은색), 파일, 맨 끝에
    **드라이브 항목**(`[-C-] …`, Windows) — 드라이브도 커서로 골라 Enter 전환.
  - 확장자별 색: EXE 밝은초록·COM 하늘·BAT 노랑·압축 자홍, 숨김 보라, 실행비트 초록.
  - 상단: 키 안내줄(청색 바) + `Path …` / `Volume(빈 공간)` 줄. 하단: 집계줄
    (`N File M Dir … byte free`) + 청색 정보줄(커서파일 크기│날짜│시간│속성 +
    현재 시각 + 핵심키 안내 — 원조의 `F10=MCD│F11=QCD│F12=Menu` 자리).
  - 커서 = 초록 배경 선택막대. 문자키 = 빨리찾기(speed search), `.` 상위, `\\` 루트.

렌더링은 ncd 와 같은 `render_line` 기반 단일 위젯(_MdirView) — 커서 이동 시 바뀐
행만 refresh 해 ssh 원격에서도 빠르다. 목록 데이터는 서버(request_mdir_list)가
권위(페더레이션이면 원격 머신 fs). 표시 필터(숨김/정렬)는 클라 로컬이라 왕복 없다.

스크롤 모델은 도스 원조대로 **페이지 단위**(부드러운 스크롤 없음): 항목 인덱스가
페이지(행수×열수)를 넘어가면 다음 페이지로 넘긴다. 열 채움은 세로 우선(column-major).
"""
from __future__ import annotations

import fnmatch
import os
import time

from rich.cells import set_cell_size
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual.screen import ModalScreen
from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Input, Static

from pytmuxlib import i18n

# ---- Mdir III 기본 배색 ----
# 색은 원본 배포판 실물 스크린샷(m3v310, archive.org)의 픽셀을 직접 추출해 맞췄다.
# 표준 VGA 16색. 핵심: 크롬(상·하단 바·구분선·테두리)은 파랑이 아니라 **시안
# #00aaaa**, 커서 선택막대는 **밝은 초록 #55ff55**(어두운 초록 아님). 확장자색은
# 매뉴얼·BLACK.COL 과 동일(EXE 밝은초록·COM 밝은하늘·BAT 노랑·압축 밝은자홍).
_CYAN = "#00aaaa"                                            # Mdir 시그니처 크롬색
_TXT = Style(color="#aaaaaa", bgcolor="#000000")            # 일반 파일(회백)
_DIR = Style(color="#ff5555", bgcolor="#000000", bold=True)  # 디렉토리(붉은색)
_UP = Style(color="#ffffff", bgcolor="#000000", bold=True)   # [ Up-Dir ]
_HID = Style(color="#aa00aa", bgcolor="#000000")             # 숨은파일(보라)
_DRIVE = Style(color="#ffaa00", bgcolor="#000000", bold=True)  # [-C-] 드라이브 항목
_CUR = Style(color="#000000", bgcolor="#55ff55", bold=True)  # 선택막대(밝은초록, 실측)
_CUR_BLUR = Style(color="#000000", bgcolor="#00aa00")        # 비포커스는 한 톤 어둡게
_TAG = Style(color="#ffff55", bgcolor="#000000", bold=True)  # 태그(선택)된 항목
_CUR_TAG = Style(color="#000000", bgcolor="#55ff55", bold=True)  # 태그+커서
_BAR = Style(color="#ffffff", bgcolor=_CYAN)                 # 상/하단 시안 바(실측)
_BAR_HI = Style(color="#ffff55", bgcolor=_CYAN, bold=True)   # 바 위 강조(시계·키)
_SEP = Style(color=_CYAN, bgcolor="#000000")                 # 구분선·열 구분 │(시안)
_PATH = Style(color="#ffffff", bgcolor="#000000", bold=True)  # Path 줄(흰색, 실측)
_ERR = Style(color="#ff5555", bgcolor="#000000", bold=True)

# 확장자별 색(원조: EXE=밝은초록 COM=밝은하늘 BAT/BTM=노랑 압축=밝은자홍).
_EXT_COLORS = {
    "exe": "#55ff55", "com": "#55ffff",
    "bat": "#ffff55", "btm": "#ffff55", "cmd": "#ffff55", "sh": "#ffff55",
}
_ARCHIVE_EXTS = {"zip", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz",
                 "zst", "7z", "rar", "lzh", "arj", "jar"}
_ARC_COLOR = "#ff55ff"

# 한글 원문을 i18n 키로 쓰고 en 번역을 등록(코드베이스 관례). 렌더 시점 i18n.t().
_STRIP = ("Space태그 · ⎇C/F5복사 ⎇M/F6이동 ⎇D/F8삭제 ⎇R/F2이름 ⎇K/F7새디렉 "
          "⎇V/F3보기 · ⎇NEST정렬 ⎇Z숨김 ⎇F필터 ⎇0-6열 · F4=cd ⇧↵분할 Esc")
_FIND = "찾기"
_BARKEYS = "F10=트리│F4=cd│⇧↵=분할│Esc"
_TRUNC = "(항목 일부만 표시)"
_COPY_TO = "복사 → 대상 디렉토리"
_MOVE_TO = "이동 → 대상 디렉토리"
_RENAME_TO = "새 이름"
_MKDIR_NAME = "새 디렉토리 이름"
_MASK_SEL = "선택 마스크 (예: *.txt)"
_MASK_UNSEL = "해제 마스크"
_DEL_TITLE = "삭제 확인"
_DEL_WARN = "디렉토리 {n}개 포함 — 하위까지 영구 삭제됩니다"
_ETC = "… 외 {n}개"
_OW_TITLE = "대상에 같은 이름이 있습니다"
_OW_ALL = "모두 덮어쓰기"
_OW_SKIP = "건너뛰기"
_DELETE = "삭제"
_CANCEL = "취소"
_NO_TARGET = "대상 항목 없음"
_ONE_ONLY = "이름변경은 한 항목만"
_FAILN = "실패 {n}"
_READONLY = "압축 보기: 읽기전용"
_BINARY = "(이진 파일 — 미리보기 없음)"
_VIEW_TRUNC = "앞 {kb}KB 만 표시"
_FILTER_MASK = "필터 마스크 (예: *.py;*.md — 빈 값=해제)"
_ARC_UNSUP = "지원하지 않는 압축 형식"
_NO_NCD = "ncd 플러그인이 없어 트리를 열 수 없습니다"
i18n.register({
    "ko": {k: k for k in (
        _STRIP, _FIND, _BARKEYS, _TRUNC, _COPY_TO, _MOVE_TO, _RENAME_TO,
        _MKDIR_NAME, _MASK_SEL, _MASK_UNSEL, _DEL_TITLE, _DEL_WARN, _ETC,
        _OW_TITLE, _OW_ALL, _OW_SKIP, _DELETE, _CANCEL, _NO_TARGET,
        _ONE_ONLY, _FAILN, _READONLY, _BINARY, _VIEW_TRUNC, _FILTER_MASK,
        _ARC_UNSUP, _NO_NCD)},
    "en": {_STRIP: ("Space tag · ⎇C/F5 copy ⎇M/F6 move ⎇D/F8 del ⎇R/F2 ren "
                    "⎇K/F7 mkdir ⎇V/F3 view · ⎇NEST sort ⎇Z hidden ⎇F filter "
                    "⎇0-6 cols · F4=cd ⇧↵ split Esc"),
           _FIND: "Find", _BARKEYS: "F10=Tree│F4=cd│⇧↵=split│Esc",
           _TRUNC: "(list truncated)",
           _COPY_TO: "Copy → destination directory",
           _MOVE_TO: "Move → destination directory",
           _RENAME_TO: "New name", _MKDIR_NAME: "New directory name",
           _MASK_SEL: "Select mask (e.g. *.txt)", _MASK_UNSEL: "Unselect mask",
           _DEL_TITLE: "Confirm delete",
           _DEL_WARN: "{n} director(y/ies) included — deleted permanently",
           _ETC: "… and {n} more",
           _OW_TITLE: "Destination has same name(s)",
           _OW_ALL: "Overwrite all", _OW_SKIP: "Skip", _DELETE: "Delete",
           _CANCEL: "Cancel", _NO_TARGET: "No target item",
           _ONE_ONLY: "Rename takes one item", _FAILN: "{n} failed",
           _READONLY: "Archive view: read-only",
           _BINARY: "(binary file — no preview)",
           _VIEW_TRUNC: "showing first {kb}KB",
           _FILTER_MASK: "Filter mask (e.g. *.py;*.md — empty=clear)",
           _ARC_UNSUP: "Unsupported archive format",
           _NO_NCD: "ncd plugin missing — tree unavailable"},
})

# 조작 이름(결과 표시)·서버발 실패 사유 코드 번역 — 서버는 키(코드)만 운반하고
# 클라가 번역한다(서버발 표면 규율). OSError 원문은 그대로 통과.
_OPNAMES = {"copy": "복사", "move": "이동", "delete": "삭제",
            "rename": "이름변경", "mkdir": "새 디렉토리"}
_REASONS = {
    "no_dst": "대상 없음", "dst_not_dir": "대상이 디렉토리가 아님",
    "no_src": "원본 없음", "root": "루트는 불가", "into_self": "자기 하위로 불가",
    "same": "원본과 대상이 같음", "dir_overwrite": "디렉토리 덮어쓰기 이동 미지원",
    "bad_name": "이름이 올바르지 않음", "exists": "같은 이름이 이미 있음",
    "bad_op": "알 수 없는 조작",
}
i18n.register({
    "ko": {**{v: v for v in _OPNAMES.values()},
           **{v: v for v in _REASONS.values()}},
    "en": {"복사": "copy", "이동": "move", "삭제": "delete",
           "이름변경": "rename", "새 디렉토리": "mkdir",
           "대상 없음": "no destination", "대상이 디렉토리가 아님":
               "destination is not a directory",
           "원본 없음": "source missing", "루트는 불가": "refused on root",
           "자기 하위로 불가": "cannot go into itself",
           "원본과 대상이 같음": "source equals destination",
           "디렉토리 덮어쓰기 이동 미지원":
               "directory overwrite-move unsupported",
           "이름이 올바르지 않음": "invalid name",
           "같은 이름이 이미 있음": "name already exists",
           "알 수 없는 조작": "unknown operation"},
})

# 빨리찾기에 안 쓰는 예약 문자(원조 mdir 의 명령 키) — `.`=상위, `\`=루트,
# `+`/`-`/`*`/`/`=선택 계열(후속 단계), 공백=태그.
_RESERVED_CHARS = set(". \\+-*/")


def _fmt_size(s: int) -> str:
    """크기 표시 — 원조처럼 콤마 구분, 10^9 이상은 칸에 안 들어가 컴팩트(G)."""
    if s < 1_000_000_000:
        return f"{s:,}"
    return f"{s / 2**30:.1f}G"


def _fmt_space(v: int) -> str:
    if v >= 2**30:
        return f"{v / 2**30:.1f}G"
    if v >= 2**20:
        return f"{v / 2**20:.0f}M"
    return f"{v:,}"


class _MdirView(Widget):
    """파일 리스트+상하단 바를 한 줄 단위로 직접 그리는 뷰(커서·페이지 자체 관리)."""
    can_focus = True

    def __init__(self, msg: dict):
        super().__init__(id="mdirview")
        self._path = ""
        self._nt = False
        self._entries: list[dict] = []
        self._drives: list[str] = []
        self._free = self._total = 0
        self._err = None
        self._over = False
        self._items: list[dict] = []
        self._idx = 0
        self._find = ""
        self._show_hidden = False
        self._pending_sel: str | None = None
        self._tags: set[str] = set()       # 태그(선택)된 이름 — 현재 디렉토리 한정
        self._pending_op: dict | None = None   # 충돌 재요청용(overwrite=ask 1차)
        self._notice: tuple[str, bool] | None = None   # (텍스트, 오류여부)
        self._sort = "n"                   # n/e/s/t/o(무정렬) — 원조 Alt-N/E/S/T/O
        self._rev = False                  # 같은 키 재입력 = 내림차순 토글(원조 Alt--)
        self._filter: list[str] = []       # 파일 마스크(;구분, fnmatch) — 원조 Alt-F
        self._cols_override: int | None = None   # 원조 Alt-1~6, Alt-0=자동
        self._arc: dict | None = None      # 압축 내부 보기 {"path","entries"} (읽기전용)
        self._arc_dir = ""                 # 압축 내부 현재 접두("a/b/" 꼴)
        self._apply(msg)

    # ---- 서버 응답 적용 ----
    def _apply(self, msg: dict):
        new_path = msg.get("path") or self._path
        if new_path != self._path:
            self._tags.clear()             # 태그는 디렉토리 한정(원조 동형)
        self._path = new_path
        self._nt = bool(msg.get("nt"))
        self._entries = list(msg.get("entries") or [])
        self._drives = list(msg.get("drives") or [])
        self._free = int(msg.get("free") or 0)
        self._total = int(msg.get("total") or 0)
        self._err = msg.get("err")
        self._over = bool(msg.get("over"))
        self._find = ""
        self._rebuild(keep_name=self._pending_sel)
        self._pending_sel = None

    def apply_list(self, msg: dict):
        self._apply(msg)
        self.refresh()

    # ---- 목록 구성(표시 모델) ----
    def _sort_key(self):
        s = self._sort
        if s == "e":
            return lambda e: ((e["n"].rsplit(".", 1)[-1].lower()
                               if "." in e["n"][1:] else ""), e["n"].lower())
        if s == "s":
            return lambda e: (e.get("s", 0), e["n"].lower())
        if s == "t":
            return lambda e: (e.get("m", 0), e["n"].lower())
        return lambda e: e["n"].lower()

    def _arc_level_items(self) -> list[dict]:
        """압축 내부의 현재 접두(_arc_dir) 한 단계를 디렉토리/파일 항목으로 만든다
        (원조 '디렉토리 재현'). 하위 경로에서 디렉토리를 유도해 tar 처럼 디렉토리
        엔트리가 없는 형식도 계층으로 보인다."""
        pfx = self._arc_dir
        dirs: dict[str, None] = {}
        files: list[dict] = []
        for e in self._arc["entries"]:
            n = e["n"].replace("\\", "/")
            if not n.startswith(pfx):
                continue
            rest = n[len(pfx):].strip("/")
            if not rest:
                continue
            if "/" in rest:
                dirs.setdefault(rest.split("/", 1)[0])
            elif e.get("d"):
                dirs.setdefault(rest)
            else:
                files.append({"n": rest, "d": False, "s": e.get("s", 0),
                              "m": 0, "h": False})
        items: list[dict] = [{"k": "up"}]
        items += [{"k": "dir", "e": {"n": d, "d": True, "s": 0, "m": 0,
                                     "h": False}}
                  for d in sorted(dirs, key=str.lower)]
        items += [{"k": "file", "e": f}
                  for f in sorted(files, key=lambda e: e["n"].lower())]
        return items

    def _rebuild(self, keep_name: str | None = None):
        """entries → 표시 항목 리스트. 순서는 원조대로 `..` → 디렉토리 → 파일 →
        드라이브. 숨김/필터/정렬은 클라 표시 상태(서버는 항상 전부 보냄 — 토글이
        왕복 없이 즉시). 압축 보기 중엔 내부 계층의 현재 단계를 그린다."""
        if self._arc is not None:
            items = self._arc_level_items()
        else:
            ents = [e for e in self._entries if self._show_hidden or not e["h"]]
            dirs = [e for e in ents if e["d"]]
            files = [e for e in ents if not e["d"]]
            if self._filter:
                files = [e for e in files
                         if any(fnmatch.fnmatch(e["n"], m)
                                for m in self._filter)]
            if self._sort != "o":              # o=무정렬(서버 나열 순서, 원조 기본)
                key = self._sort_key()
                dirs.sort(key=key, reverse=self._rev)
                files.sort(key=key, reverse=self._rev)
            items = [{"k": "up"}]
            items += [{"k": "dir", "e": e} for e in dirs]
            items += [{"k": "file", "e": e} for e in files]
            items += [{"k": "drive", "p": p} for p in self._drives]
            self._tags &= {e["n"] for e in ents}   # 사라진 항목의 태그 정리
        self._items = items
        self._idx = 0
        if keep_name:
            for i, it in enumerate(items):
                if it.get("e", {}).get("n") == keep_name:
                    self._idx = i
                    break

    def _item_name(self, it: dict) -> str:
        if it["k"] == "up":
            return ".."
        if it["k"] == "drive":
            return it["p"]
        return it["e"]["n"]

    # ---- 격자 기하(페이지 단위·세로 우선) ----
    def _body_rows(self) -> int:
        return max(1, self.size.height - 6)

    def _cols(self) -> int:
        # 열수: 원조 Alt-1~6 수동, Alt-0=자동(열 하나가 최소 ~34칸은 되게).
        if self._cols_override:
            return max(1, min(self._cols_override,
                              max(1, self.size.width // 16)))
        return max(1, min(6, self.size.width // 34))

    def _colw(self) -> int:
        cols = self._cols()
        return max(10, (self.size.width - (cols - 1)) // cols)

    def _page_geometry(self):
        rows, cols = self._body_rows(), self._cols()
        per = rows * cols
        page = self._idx // per if per else 0
        return rows, cols, per, page

    # ---- 렌더 ----
    def render_line(self, y: int) -> Strip:
        w = self.size.width
        h = self.size.height
        if h < 7:                     # 극단 축소 방어
            return Strip.blank(w, _TXT)
        if y == 0:
            return self._line_bar(f" {i18n.t(_STRIP)}", w)
        if y == 1:
            return self._line_path(w)
        if y == 2 or y == h - 3:
            return Strip([Segment("─" * w, _SEP)])
        if y == h - 2:
            return self._line_counts(w)
        if y == h - 1:
            return self._line_info(w)
        return self._line_body(y - 3, w)

    def _line_bar(self, text: str, w: int) -> Strip:
        return Strip([Segment(set_cell_size(text, w), _BAR)])

    def _line_path(self, w: int) -> Strip:
        if self._arc is not None:
            name = os.path.basename(self._arc["path"])
            ext = name.rsplit(".", 1)[-1].upper() if "." in name else "?"
            left = f" Archive {name} ({ext})  /{self._arc_dir}"
        else:
            left = f" Path {self._path}"
            if self._filter:                # 원조: Path 줄에 파일스펙 표시
                left += f"  [{';'.join(self._filter)}]"
        if self._find:
            right = f"{i18n.t(_FIND)}: {self._find} "
        else:
            right = (f"Free {_fmt_space(self._free)}"
                     f"/{_fmt_space(self._total)} " if self._total else "")
        pad = max(1, w - len(right) - _cells(left))
        return Strip([Segment(set_cell_size(left, _cells(left) + pad), _PATH),
                      Segment(right, _TXT)]).adjust_cell_length(w, _TXT)

    def _line_counts(self, w: int) -> Strip:
        # 우선순위: 조작 결과/오류 공지 → 서버 오류 → 평상시 집계(원조 형식).
        if self._notice:
            text, is_err = self._notice
            return Strip([Segment(set_cell_size(f" {text}", w),
                                  _ERR if is_err else _TAG)])
        if self._err:
            return Strip([Segment(set_cell_size(f" {self._err}", w), _ERR)])
        nf = nd = 0
        total = sel_total = 0
        for it in self._items:
            if it["k"] == "file":
                nf += 1
                total += it["e"]["s"]
                if it["e"]["n"] in self._tags:
                    sel_total += it["e"]["s"]
            elif it["k"] == "dir":
                nd += 1
        pct = round(self._free * 100 / self._total) if self._total else 0
        text = (f" {nf} File  {nd} Dir  {total:,} Byte  "
                f"{self._free:,}({pct}%)byte free")
        if self._tags:
            text += f"  Sel {len(self._tags)} ({sel_total:,})"
        # 정렬 표시(원조 상태줄의 N/E/S/T 문자 — 내림차순은 ↓, O=무정렬).
        text += f"  {self._sort.upper()}{'↓' if self._rev else ''}"
        if self._show_hidden:
            text += " H"
        if self._over:
            text += f"  {i18n.t(_TRUNC)}"
        return Strip([Segment(set_cell_size(text, w), _TXT)])

    def _line_info(self, w: int) -> Strip:
        """최하단 청색 정보줄: 커서 항목 `크기│날짜│시간│속성` + 시계 + 핵심키."""
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        left = " "
        if it and it["k"] in ("file", "dir"):
            e = it["e"]
            m = e.get("m") or 0
            stamp = (time.strftime("%y-%m-%d│%H:%M", time.localtime(m))
                     if m else "--------│--:--")
            size = "[SubDir]" if it["k"] == "dir" else _fmt_size(e.get("s", 0))
            attr = ("r" if e.get("ro") else "-") + ("h" if e.get("h") else "-")
            left = f" {size}│{stamp}│{attr}"
        elif it and it["k"] == "drive":
            left = f" {it['p']}"
        clock = time.strftime("%y-%m-%d %a %H:%M:%S")
        right = f"{i18n.t(_BARKEYS)} "
        mid_w = max(0, w - _cells(left) - _cells(right))
        mid = clock.center(mid_w)[:mid_w] if mid_w else ""
        return Strip([Segment(left, _BAR),
                      Segment(mid, _BAR_HI),
                      Segment(right, _BAR)]).adjust_cell_length(w, _BAR)

    def _line_body(self, row: int, w: int) -> Strip:
        rows, cols, per, page = self._page_geometry()
        if row >= rows:
            return Strip.blank(w, _TXT)
        colw = self._colw()
        segs: list[Segment] = []
        for c in range(cols):
            if c:
                segs.append(Segment("│", _SEP))
            i = page * per + c * rows + row
            if 0 <= i < len(self._items):
                segs.append(self._item_segment(self._items[i], colw,
                                               cursor=(i == self._idx)))
            else:
                segs.append(Segment(" " * colw, _TXT))
        return Strip(segs).adjust_cell_length(w, _TXT)

    def _item_style(self, it: dict) -> Style:
        k = it["k"]
        if k == "up":
            return _UP
        if k == "drive":
            return _DRIVE
        if k == "dir":
            return _DIR
        e = it["e"]
        if e.get("h"):
            return _HID
        ext = e["n"].rsplit(".", 1)[-1].lower() if "." in e["n"][1:] else ""
        if ext in _ARCHIVE_EXTS:
            return Style(color=_ARC_COLOR, bgcolor="#000000")
        if ext in _EXT_COLORS:
            return Style(color=_EXT_COLORS[ext], bgcolor="#000000")
        if e.get("x"):
            return Style(color="#55ff55", bgcolor="#000000")
        return _TXT

    def _item_segment(self, it: dict, colw: int, cursor: bool) -> Segment:
        k = it["k"]
        with_dt = colw >= 56
        size_w = 11
        name_w = colw - size_w - 1 - (15 if with_dt else 0)
        if k == "up":
            text = set_cell_size("..", max(1, name_w)) + " " + \
                f"{'[ Up-Dir ]':>{size_w}}"
        elif k == "drive":
            text = set_cell_size(f"[-{it['p'][:1]}-] {it['p']}", colw)
        else:
            e = it["e"]
            size = "[ SubDir ]" if k == "dir" else _fmt_size(e.get("s", 0))
            text = set_cell_size(e["n"], max(1, name_w)) + " " + \
                f"{size:>{size_w}}"
            if with_dt:
                m = e.get("m") or 0
                text += (time.strftime(" %y-%m-%d %H:%M", time.localtime(m))
                         if m else " " * 15)
        text = set_cell_size(text, colw)
        tagged = k in ("file", "dir") and it["e"]["n"] in self._tags
        if cursor:
            style = (_CUR_TAG if tagged else
                     _CUR if self.has_focus else _CUR_BLUR)
        elif tagged:
            style = _TAG
        else:
            style = self._item_style(it)
        return Segment(text, style)

    # ---- 커서 이동(페이지 단위) ----
    def _move(self, new: int):
        n = len(self._items)
        if not n:
            return
        new = max(0, min(n - 1, new))
        if new == self._idx:
            return
        rows, cols, per, page = self._page_geometry()
        old = self._idx
        self._idx = new
        w = self.size.width
        if per and new // per != page:
            self.refresh()                       # 페이지 넘어감 → 전체
        else:                                    # 같은 페이지 → 바뀐 두 행만(ssh 최소)
            self.refresh(Region(0, 3 + (old % per) % rows, w, 1))
            self.refresh(Region(0, 3 + (new % per) % rows, w, 1))
        # 하단 정보줄(커서 파일 크기/날짜/속성)도 커서 따라 갱신.
        self.refresh(Region(0, self.size.height - 1, w, 1))

    def refresh_clock(self):
        if self.size.height > 0:
            self.refresh(Region(0, self.size.height - 1, self.size.width, 1))

    # ---- 탐색 ----
    @staticmethod
    def _parent_of(p: str) -> str | None:
        """경로 문자열만으로 부모를 구한다(클라 OS 와 무관 — 페더레이션에서 서버
        경로가 클라와 다른 방언일 수 있어 os.path 대신 구분자 직접 처리)."""
        q = p.rstrip("/\\")
        i = max(q.rfind("/"), q.rfind("\\"))
        if i < 0:
            return None                     # 루트('/')·드라이브('C:\\') — 더 위 없음
        parent = q[:i + 1]                  # 구분자 포함('/a'→'/', 'C:\\x'→'C:\\')
        if len(parent) > 1 and not (len(parent) == 3 and parent[1] == ":"):
            parent = parent.rstrip("/\\") or parent
        return parent

    def _join(self, name: str) -> str:
        p = self._path
        if p.endswith(("/", "\\")):
            return p + name
        return p + ("\\" if self._nt else "/") + name

    def _navigate(self, path: str, sel_name: str | None = None):
        self._pending_sel = sel_name
        self.app.request_mdir_list(path)

    def _go_parent(self):
        parent = self._parent_of(self._path)
        if parent:
            child = self._path.rstrip("/\\")
            child = child[max(child.rfind("/"), child.rfind("\\")) + 1:]
            self._navigate(parent, sel_name=child or None)

    def _cur_dir_target(self) -> str:
        """⇧Enter(새 패널)의 대상 — 커서가 디렉토리/드라이브면 그것, 아니면 현재."""
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        if it:
            if it["k"] == "dir":
                return self._join(it["e"]["n"])
            if it["k"] == "drive":
                return it["p"]
            if it["k"] == "up":
                return self._parent_of(self._path) or self._path
        return self._path

    # ---- 빨리찾기(speed search) ----
    def _set_find(self, s: str):
        self._find = s
        self.refresh(Region(0, 1, self.size.width, 1))   # Path/Volume 줄 갱신

    def _jump(self):
        if not self._find or not self._items:
            return
        q = self._find.lower()
        n = len(self._items)
        for match_prefix in (True, False):
            for off in range(n):
                i = (self._idx + off) % n
                if self._items[i]["k"] in ("up", "drive"):
                    continue
                name = self._item_name(self._items[i]).lower()
                if (name.startswith(q) if match_prefix else q in name):
                    self._move(i)
                    return

    # ---- 태그(선택) ----
    def _tag_toggle(self):
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        if it and it["k"] in ("file", "dir"):
            n = it["e"]["n"]
            (self._tags.discard if n in self._tags else self._tags.add)(n)
            self._move(self._idx + 1)      # 원조: 태그 후 커서 한 칸 아래로
            self.refresh()

    def _tag_all_toggle(self):
        names = {it["e"]["n"] for it in self._items if it["k"] in ("file", "dir")}
        self._tags = set() if self._tags else names
        self.refresh()

    def _tag_mask(self, add: bool):
        title = _MASK_SEL if add else _MASK_UNSEL

        def done(mask):
            if not mask:
                return
            hits = {it["e"]["n"] for it in self._items
                    if it["k"] in ("file", "dir")
                    and fnmatch.fnmatch(it["e"]["n"], mask)}
            self._tags = (self._tags | hits) if add else (self._tags - hits)
            self.refresh()
        self.app.push_screen(MdirPrompt(i18n.t(title)), done)

    def _tag_invert(self):
        names = {it["e"]["n"] for it in self._items if it["k"] in ("file", "dir")}
        self._tags = names - self._tags
        self.refresh()

    # ---- 파일 조작 ----
    def _ro(self) -> bool:
        """압축 보기(읽기전용) 중이면 공지하고 True — 태그/조작 키 가드."""
        if self._arc is not None:
            self._flash(i18n.t(_READONLY))
            return True
        return False

    def _flash(self, text: str, err: bool = True):
        self._notice = (text, err)
        self.refresh(Region(0, self.size.height - 2, self.size.width, 1))

    def _clear_notice(self):
        if self._notice:
            self._notice = None
            self.refresh(Region(0, self.size.height - 2, self.size.width, 1))

    def _targets(self) -> list[str]:
        """조작 대상 이름들 — 태그가 있으면 태그 전체, 없으면 커서 항목(파일/
        디렉토리만). `..`/드라이브는 대상이 아니다(원조 동형)."""
        if self._tags:
            return [it["e"]["n"] for it in self._items
                    if it["k"] in ("file", "dir") and it["e"]["n"] in self._tags]
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        if it and it["k"] in ("file", "dir"):
            return [it["e"]["n"]]
        return []

    def _send_op(self, **kw):
        self._pending_op = dict(kw)
        self._clear_notice()
        self.app.request_mdir_op(**kw)

    def _cursor_name(self) -> str | None:
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        return it["e"]["n"] if it and it["k"] in ("file", "dir") else None

    def _op_copy_move(self, op: str):
        names = self._targets()
        if not names:
            self._flash(i18n.t(_NO_TARGET))
            return
        title = i18n.t(_COPY_TO if op == "copy" else _MOVE_TO) + f" ({len(names)})"
        srcs = [self._join(n) for n in names]

        def done(dst):
            if dst:
                self._send_op(op=op, src=srcs, dst=dst, overwrite="ask")
        self.app.push_screen(MdirPrompt(title, value=self._path), done)

    def _op_delete(self):
        names = self._targets()
        if not names:
            self._flash(i18n.t(_NO_TARGET))
            return
        ndirs = sum(1 for it in self._items
                    if it["k"] == "dir" and it["e"]["n"] in names)
        lines = names[:6]
        if len(names) > 6:
            lines.append(i18n.t(_ETC).format(n=len(names) - 6))
        if ndirs:
            lines.append(i18n.t(_DEL_WARN).format(n=ndirs))
        srcs = [self._join(n) for n in names]

        def done(choice):
            if choice == "del":
                self._send_op(op="delete", src=srcs)
        # 기본 선택은 '취소'(파괴적 조작 — Enter 연타로 지워지지 않게).
        self.app.push_screen(MdirConfirm(
            f"{i18n.t(_DEL_TITLE)} ({len(names)})", lines,
            [("del", i18n.t(_DELETE)), ("cancel", i18n.t(_CANCEL))],
            default=1), done)

    def _op_rename(self):
        names = self._targets()
        if not names:
            self._flash(i18n.t(_NO_TARGET))
            return
        if len(names) != 1:
            self._flash(i18n.t(_ONE_ONLY))
            return
        src = self._join(names[0])

        def done(new):
            if new and new != names[0]:
                self._send_op(op="rename", src=[src], dst=new)
        self.app.push_screen(
            MdirPrompt(i18n.t(_RENAME_TO), value=names[0]), done)

    def _op_mkdir(self):
        def done(name):
            if name:
                self._pending_sel = name
                self._send_op(op="mkdir", base=self._path, dst=name)
        self.app.push_screen(MdirPrompt(i18n.t(_MKDIR_NAME)), done)

    # ---- 표시 토글(정렬/숨김/필터/열수) ----
    def _set_sort(self, s: str):
        """원조 Alt-N/E/S/T/O. 같은 키 재입력 = 내림차순 토글(원조 Alt-- 대응)."""
        if self._sort == s:
            self._rev = not self._rev
        else:
            self._sort, self._rev = s, False
        self._rebuild(keep_name=self._cursor_name())

    def _toggle_hidden(self):
        self._show_hidden = not self._show_hidden
        self._rebuild(keep_name=self._cursor_name())

    def _ask_filter(self):
        def done(spec):
            self._filter = [m.strip() for m in (spec or "").split(";")
                            if m.strip()]
            self._rebuild(keep_name=self._cursor_name())
        self.app.push_screen(
            MdirPrompt(i18n.t(_FILTER_MASK), value=";".join(self._filter)), done)

    def _set_cols(self, n: int):
        self._cols_override = n or None
        self._rebuild(keep_name=self._cursor_name())

    # ---- F10: ncd 디렉토리 트리 연동(원조 F10=MCD 대응) ----
    def _open_tree(self):
        """ncd 트리 팝업을 mdir 위에 띄우고, 고른 디렉토리로 **mdir 이 이동**한다
        (ncd 기본 동작인 패널 cd 가 아니라). ncd 플러그인의 일회성 콜백 훅
        (app._nc_open_cb)을 쓴다 — ncd 디렉토리를 지우면 request_nc_list 가 없어
        조용히 공지만 하고 무동작(양방향 delete-to-disable)."""
        fn = getattr(self.app, "request_nc_list", None)
        if fn is None:
            self._flash(i18n.t(_NO_NCD))
            return

        def done(res):
            if not res:
                return                    # Esc — mdir 로 그냥 복귀
            action, path = res
            if action == "cd":
                self._clear_notice()
                self._navigate(path)      # 트리 선택 → mdir 탐색 이동
            elif action == "newpane":
                self.app.send_cmd("split", orient="lr", path=path)
        self.app._nc_open_cb = done
        fn()

    # ---- 내장 뷰어 / 압축 내부 보기 ----
    def _open_viewer(self):
        if self._arc is not None:
            self._flash(i18n.t(_READONLY))
            return
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        if it and it["k"] == "file":
            self.app.request_mdir_view(self._join(it["e"]["n"]))

    def _open_file(self, name: str):
        """실디렉토리에서 파일 Enter — 압축이면 내부 보기, 아니면 뷰어(현대화:
        원조는 실행이지만 멀티플렉서 안에서 임의 실행은 위험해 열람으로 대응)."""
        ext = name.rsplit(".", 1)[-1].lower() if "." in name[1:] else ""
        if ext in _ARCHIVE_EXTS:
            self.app.request_mdir_arc(self._join(name))
        else:
            self.app.request_mdir_view(self._join(name))

    def _exit_arc(self):
        keep = os.path.basename(self._arc["path"]) if self._arc else None
        self._arc = None
        self._arc_dir = ""
        self._rebuild(keep_name=keep)
        self.refresh()

    def _arc_up(self):
        """압축 내부에서 한 단계 위로 — 최상위면 압축 보기 종료(실디렉토리 복귀)."""
        if not self._arc_dir:
            self._exit_arc()
            return
        self._arc_dir = self._arc_dir.rstrip("/")
        cut = self._arc_dir.rfind("/")
        child = self._arc_dir[cut + 1:]
        self._arc_dir = self._arc_dir[:cut + 1] if cut >= 0 else ""
        self._rebuild(keep_name=child or None)
        self.refresh()

    def apply_arc(self, msg: dict):
        err = msg.get("err")
        if err:
            self._flash(i18n.t(_ARC_UNSUP) if err == "arc_unsupported" else err)
            return
        self._arc = {"path": msg.get("path"), "entries": msg.get("entries") or []}
        self._arc_dir = ""
        self._clear_notice()
        self._rebuild()
        self.refresh()

    # ---- 조작 결과 ----
    def _reason(self, code: str) -> str:
        return i18n.t(_REASONS[code]) if code in _REASONS else code

    def apply_result(self, msg: dict):
        """mdir_result 수신. 충돌(overwrite=ask 1차)이면 [모두 덮어쓰기/건너뛰기/
        취소]를 물어 재요청하고, 완료면 공지 + 태그 해제 + 목록 재조회."""
        op = msg.get("op", "?")
        conflicts = msg.get("conflicts") or []
        failed = msg.get("failed") or []
        done = int(msg.get("done") or 0)
        if conflicts and self._pending_op:
            pend = self._pending_op

            def choice(c):
                if c in ("all", "skip"):
                    self._send_op(**{**pend, "overwrite": c})
                else:
                    self._pending_op = None
            lines = conflicts[:6]
            if len(conflicts) > 6:
                lines.append(i18n.t(_ETC).format(n=len(conflicts) - 6))
            self.app.push_screen(MdirConfirm(
                i18n.t(_OW_TITLE), lines,
                [("all", i18n.t(_OW_ALL)), ("skip", i18n.t(_OW_SKIP)),
                 ("cancel", i18n.t(_CANCEL))], default=2), choice)
            return
        self._pending_op = None
        self._tags.clear()
        text = f"{i18n.t(_OPNAMES.get(op, op))} {done}"
        if failed:
            name, why = failed[0][0], self._reason(failed[0][1])
            text += (f" · {i18n.t(_FAILN).format(n=len(failed))}: "
                     f"{name} — {why}")
        self._notice = (text, bool(failed))
        # 목록 재조회(커서 유지 시도). 공지는 apply_list 가 지우지 않는다 — 다음
        # 조작/탐색 키에서 지워진다.
        self._pending_sel = self._pending_sel or self._cursor_name()
        self.app.request_mdir_list(self._path)

    # ---- 키 ----
    async def on_key(self, event: events.Key):
        k = event.key
        ch = event.character
        it = self._items[self._idx] if 0 <= self._idx < len(self._items) else None
        rows, cols, per, _page = self._page_geometry()
        if k == "escape":
            event.stop()
            if self._find:
                self._set_find("")
            elif self._arc is not None:    # 압축 보기 중 Esc = 실디렉토리 복귀
                self._exit_arc()
            else:
                self.screen.dismiss(None)
        elif k == "enter":
            event.stop()
            self._set_find("")
            self._clear_notice()
            if it is None:
                return
            if self._arc is not None:      # 압축 내부(읽기전용 계층 탐색)
                if it["k"] == "up":
                    self._arc_up()
                elif it["k"] == "dir":
                    self._arc_dir += it["e"]["n"] + "/"
                    self._rebuild()
                    self.refresh()
                else:
                    self._flash(i18n.t(_READONLY))
                return
            if it["k"] == "up":
                self._go_parent()
            elif it["k"] == "dir":
                self._navigate(self._join(it["e"]["n"]))
            elif it["k"] == "drive":
                self._navigate(it["p"])
            elif it["k"] == "file":
                self._open_file(it["e"]["n"])
        elif k == "space":                     # 태그 토글(+커서 아래로)
            event.stop()
            self._set_find("")
            if not self._ro():
                self._tag_toggle()
        elif k == "alt+u":                     # 전체 태그/해제
            event.stop()
            if not self._ro():
                self._tag_all_toggle()
        elif ch == "+":                        # 와일드카드 태그(원조 회색 +)
            event.stop()
            if not self._ro():
                self._tag_mask(add=True)
        elif ch == "-":
            event.stop()
            if not self._ro():
                self._tag_mask(add=False)
        elif ch == "*":                        # 태그 반전
            event.stop()
            if not self._ro():
                self._tag_invert()
        elif k in ("alt+c", "f5", "insert"):   # 복사(원조 Alt-C/Ins, NC F5)
            event.stop()
            if not self._ro():
                self._op_copy_move("copy")
        elif k in ("alt+m", "f6"):             # 이동
            event.stop()
            if not self._ro():
                self._op_copy_move("move")
        elif k in ("alt+d", "delete", "f8"):   # 삭제(확인 팝업 필수)
            event.stop()
            if not self._ro():
                self._op_delete()
        elif k in ("alt+r", "f2"):             # 이름변경
            event.stop()
            if not self._ro():
                self._op_rename()
        elif k in ("alt+k", "f7"):             # 새 디렉토리(원조 maKe)
            event.stop()
            if not self._ro():
                self._op_mkdir()
        elif k in ("alt+v", "f3"):             # 내장 뷰어(원조 Alt-V '보라')
            event.stop()
            self._open_viewer()
        elif k in ("alt+n", "alt+e", "alt+s", "alt+t", "alt+o"):
            event.stop()                       # 정렬(재입력=내림차순)
            self._set_sort(k[-1])
        elif k == "alt+z":                     # 숨김 파일 토글
            event.stop()
            self._toggle_hidden()
        elif k == "alt+f":                     # 파일 마스크 필터
            event.stop()
            if not self._ro():
                self._ask_filter()
        elif k in ("alt+0", "alt+1", "alt+2", "alt+3",
                   "alt+4", "alt+5", "alt+6"):
            event.stop()                       # 열수(0=자동)
            self._set_cols(int(k[-1]))
        elif k == "f10":                       # 디렉토리 트리(원조 F10=MCD → ncd)
            event.stop()
            if not self._ro():
                self._open_tree()
        elif k in ("f4", "ctrl+enter"):        # 패널 cd 후 닫기(원조: 종료 시 잔류)
            event.stop()
            self.screen.dismiss(("cd", self._path))
        elif k in ("shift+enter", "ctrl+o"):   # 새 패널 분할(ncd 동형)
            event.stop()
            self.screen.dismiss(("newpane", self._cur_dir_target()))
        elif k == "up":
            event.stop(); self._set_find(""); self._move(self._idx - 1)
        elif k == "down":
            event.stop(); self._set_find(""); self._move(self._idx + 1)
        elif k == "left":
            event.stop(); self._set_find(""); self._move(self._idx - rows)
        elif k == "right":
            event.stop(); self._set_find(""); self._move(self._idx + rows)
        elif k == "pageup":
            event.stop(); self._set_find(""); self._move(self._idx - per)
        elif k == "pagedown":
            event.stop(); self._set_find(""); self._move(self._idx + per)
        elif k == "home":
            event.stop(); self._set_find(""); self._move(0)
        elif k == "end":
            event.stop(); self._set_find(""); self._move(len(self._items) - 1)
        elif k == "backspace":
            event.stop()
            if self._find:
                self._set_find(self._find[:-1])
                self._jump()
            else:
                self._clear_notice()
                # 원조 감각: BS=상위(빨리찾기 없을 때). 압축 안이면 내부 상위.
                self._arc_up() if self._arc is not None else self._go_parent()
        elif ch == ".":
            event.stop(); self._set_find(""); self._clear_notice()
            self._arc_up() if self._arc is not None else self._go_parent()
        elif ch == "\\":
            event.stop()
            self._set_find("")
            self._clear_notice()
            if self._arc is not None:          # 압축 내부 최상위로
                self._arc_dir = ""
                self._rebuild()
                self.refresh()
            else:
                root = (self._path[:3] if self._nt and self._path[1:2] == ":"
                        else "/")
                self._navigate(root)
        elif ch and ch.isprintable() and len(ch) == 1 \
                and ch not in _RESERVED_CHARS:
            event.stop()                       # 빨리찾기 글자 입력
            self._set_find(self._find + ch)
            self._jump()

    # ---- 마우스 ----
    def on_click(self, event: events.Click):
        rows, cols, per, page = self._page_geometry()
        row = event.y - 3
        if not (0 <= row < rows):
            return
        col = min(cols - 1, event.x // (self._colw() + 1))
        i = page * per + col * rows + row
        if 0 <= i < len(self._items):
            event.stop()
            self._move(i)

    def on_mouse_scroll_down(self, event):
        event.stop(); self._move(self._idx + 3)

    def on_mouse_scroll_up(self, event):
        event.stop(); self._move(self._idx - 3)

    def on_mount(self):
        self.focus()

    def on_resize(self, event):
        self._idx = max(0, min(self._idx, len(self._items) - 1))
        self.refresh()


def _cells(s: str) -> int:
    from rich.cells import cell_len
    return cell_len(s)


class MdirScreen(ModalScreen):
    """mdir 팝업 껍데기 — 검정 패널 + 이중 테두리. 실제 그리기는 _MdirView."""
    CSS = """
    MdirScreen { align: center middle; }
    #mdirbox { width: 94%; height: 92%; padding: 0;
               background: #000000; color: #aaaaaa;
               border: double #00aaaa;
               border-title-color: #ffffff; border-title-background: #000000; }
    #mdirview { height: 1fr; width: 1fr; }
    """

    def __init__(self, msg: dict):
        super().__init__()
        self._view = _MdirView(msg)

    def compose(self) -> ComposeResult:
        with Vertical(id="mdirbox"):
            yield self._view

    def on_mount(self):
        self.query_one("#mdirbox", Vertical).border_title = "Mdir"
        self.set_interval(1.0, self._view.refresh_clock)

    def apply_list(self, msg: dict):
        self._view.apply_list(msg)

    def apply_result(self, msg: dict):
        self._view.apply_result(msg)

    def apply_arc(self, msg: dict):
        self._view.apply_arc(msg)


class MdirViewer(ModalScreen):
    """내장 텍스트 뷰어(원조 '보라 VV' 대응, 읽기전용) — 서버가 보낸 앞부분
    (VIEW_LIMIT)을 스크롤 열람. 이진 파일은 본문 없이 안내만. Esc/F3 닫기."""
    CSS = """
    MdirViewer { align: center middle; }
    #mdvbox { width: 94%; height: 92%; padding: 0;
              background: #000000; color: #aaaaaa;
              border: double #00aaaa;
              border-title-color: #ffffff; border-title-background: #000000;
              border-subtitle-color: #ffff55;
              border-subtitle-background: #000000; }
    #mdvscroll { height: 1fr; width: 1fr;
                 scrollbar-background: #000000; scrollbar-color: #00aaaa; }
    #mdvtext { width: 1fr; }
    """

    def __init__(self, msg: dict):
        super().__init__()
        self._msg = msg

    def compose(self) -> ComposeResult:
        from textual.containers import VerticalScroll
        msg = self._msg
        if msg.get("err"):
            body = Text(str(msg["err"]), style="bold #ff5555")
        elif msg.get("binary"):
            body = Text(i18n.t(_BINARY), style="bold #ffff55")
        else:
            # Text() 로 감싼다 — 파일 내용의 대괄호가 마크업으로 오해되지 않게.
            body = Text(msg.get("text") or "")
        with Vertical(id="mdvbox"):
            with VerticalScroll(id="mdvscroll"):
                yield Static(body, id="mdvtext")

    def on_mount(self):
        box = self.query_one("#mdvbox", Vertical)
        box.border_title = self._msg.get("path") or ""
        sub = f"{self._msg.get('size', 0):,} Byte"
        if self._msg.get("truncated"):
            kb = len((self._msg.get("text") or "").encode()) // 1024
            sub += f" · {i18n.t(_VIEW_TRUNC).format(kb=kb)}"
        box.border_subtitle = sub + " · Esc"
        self.query_one("#mdvscroll").focus()

    def on_key(self, event: events.Key):
        if event.key in ("escape", "f3", "q"):
            event.stop()
            self.dismiss(None)


class MdirPrompt(ModalScreen):
    """한 줄 입력 팝업(대상 경로/새 이름/마스크) — Mdir 시안 대화상자 풍(원본
    풀다운 메뉴 배색: 시안 배경·검정 글자·노랑 제목). Enter=값, Esc=None."""
    CSS = """
    MdirPrompt { align: center middle; }
    #mdp { width: 64; max-width: 90%; height: auto; padding: 0 1;
           background: #00aaaa; color: #000000; border: double #55ffff;
           border-title-color: #ffff55; border-title-background: #00aaaa; }
    #mdp Input { background: #000000; color: #ffffff; border: none; }
    #mdp Input:focus { border: none; }
    """

    def __init__(self, title: str, value: str = ""):
        super().__init__()
        self._title = title
        self._value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="mdp"):
            yield Input(value=self._value, id="mdpin")

    def on_mount(self):
        box = self.query_one("#mdp", Vertical)
        box.border_title = self._title
        inp = self.query_one("#mdpin", Input)
        inp.focus()
        inp.cursor_position = len(self._value)

    def on_input_submitted(self, event):
        event.stop()
        self.dismiss((event.value or "").strip() or None)

    def on_key(self, event: events.Key):
        if event.key == "escape":
            event.stop()
            self.dismiss(None)


class MdirConfirm(ModalScreen):
    """확인 팝업(삭제/덮어쓰기) — 본문 여러 줄 + 가로 버튼(←→/Tab 이동, Enter 확정,
    Esc 취소). dismiss(선택 key | None). 파괴적 조작은 default 를 '취소'에 둔다."""
    CSS = """
    MdirConfirm { align: center middle; }
    #mdc { width: 64; max-width: 90%; height: auto; padding: 0 1;
           background: #00aaaa; color: #000000; border: double #55ffff;
           border-title-color: #ffff55; border-title-background: #00aaaa; }
    #mdcbody { width: 100%; }
    #mdcopts { width: 100%; padding: 1 0 0 0; text-align: center; }
    """

    def __init__(self, title: str, lines: list[str],
                 options: list[tuple[str, str]], default: int = 0):
        super().__init__()
        self._title = title
        self._lines = list(lines)
        self._options = list(options)
        self._sel = max(0, min(default, len(options) - 1))

    def compose(self) -> ComposeResult:
        with Vertical(id="mdc"):
            yield Static(Text("\n".join(self._lines)), id="mdcbody")
            yield Static(self._opts_text(), id="mdcopts")

    def on_mount(self):
        self.query_one("#mdc", Vertical).border_title = self._title

    def _opts_text(self) -> Text:
        t = Text(justify="center")
        for i, (_key, label) in enumerate(self._options):
            if i:
                t.append("   ")
            # 선택 버튼은 노랑 강조 막대(원본 메뉴 선택색 감각), 비선택은 어두운
            # 시안 알약으로 시안 배경과 분리해 읽히게 한다.
            style = ("black on #ffff55 bold" if i == self._sel
                     else "white on #007777")
            t.append(f" {label} ", style)
        return t

    def _redraw(self):
        self.query_one("#mdcopts", Static).update(self._opts_text())

    def on_key(self, event: events.Key):
        k = event.key
        n = len(self._options)
        if k == "escape":
            event.stop()
            self.dismiss(None)
        elif k in ("left", "shift+tab"):
            event.stop()
            self._sel = (self._sel - 1) % n
            self._redraw()
        elif k in ("right", "tab"):
            event.stop()
            self._sel = (self._sel + 1) % n
            self._redraw()
        elif k == "enter":
            event.stop()
            self.dismiss(self._options[self._sel][0])
