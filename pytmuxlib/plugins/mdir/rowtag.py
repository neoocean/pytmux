"""줄 하나의 **의미 태그** — UI 무의존(rich/textual 을 안 읽는다).

# 왜 여기인가 (pytmux-12 A)

제보: *"컬러 스킴 일치가 특히 중요하다."* 정본은 줄마다 색을 달리 칠한다 — 디렉터리는
붉고, 숨은 파일은 보라, 드라이브는 주황, 태그된 것은 노랑, 압축은 자홍. 그런데 그 판정
(`screen._item_style`)이 **Textual 화면 안**에 있어서 서버가 못 부르고, 스펙의 줄
(`PluginRow`)에는 실을 칸조차 없었다. 그래서 네이티브 클라의 mdir 은 **전부 같은 색**이다.

여기서 하는 일은 색을 정하는 것이 아니라 **무엇인지 이름 붙이는 것**이다:

    dir · updir · drive · hidden · tagged · archive · exe · com · script · exec · text

색은 각 클라가 그 이름으로 푼다 — 서버가 hex 를 실으면 서버가 UI 를 알게 된다
(설계 §10 위험표). `cells.py`(자리)·`statusbadges.py`(표식)·`cmdmap.py`(명령)가 먼저
낸 자리이고, 이것은 **줄의 뜻** 판이다.

# 표현은 어디에 있나

- 정본 — `screen._TAG_STYLES` 가 이 이름을 자기 `Style` 로 푼다(Mdir 시그니처 색).
- 네이티브 — `proto::rowtag` 가 같은 이름을 푼다. 그 표는 **정본에서 뽑은 픽스처**
  (`scripts/gen_row_tags.py`)가 지키므로 두 벌이 갈리지 않는다.

⚠ 이 화면의 색은 **테마가 아니라 제품의 정체성**이다(Norton Commander 계열의 그림).
그래서 클라 테마로 풀지 않고 정본과 같은 값을 쓴다 — 상태줄 표식(`theme::resolve`)과
갈리는 지점이고, 갈리는 이유가 이것이다.
"""
from __future__ import annotations

#: 압축으로 볼 확장자. 정본과 **같은 집합**이라야 같은 줄이 같은 색이 된다.
ARCHIVE_EXTS = {"zip", "tar", "gz", "tgz", "bz2", "tbz2", "xz", "txz",
                "zst", "7z", "rar", "lzh", "arj", "jar"}

#: 확장자별 태그. 정본 `_EXT_COLORS` 의 키를 그대로 옮긴 것이고, 값이 색이 아니라
#: **이름**인 것만 다르다.
EXT_TAGS = {
    "exe": "exe", "com": "com",
    "bat": "script", "btm": "script", "cmd": "script", "sh": "script",
}

#: 이 모듈이 낼 수 있는 이름 전부(오라클이 전수를 잰다).
TAGS = ("updir", "drive", "dir", "tagged", "hidden", "archive",
        "exe", "com", "script", "exec", "text", "cwd")


def row_tag(kind: str, entry: dict | None = None, tagged: bool = False) -> str:
    """줄의 의미 이름. 정본 `screen._item_style` 과 **같은 차례**로 판정한다.

    차례가 규칙이다 — 태그가 먼저이고(고른 것은 무엇이든 노랗다), 그다음이 갈래
    (`updir`·`drive`·`dir`), 그다음이 숨김, 그다음이 확장자, 마지막이 실행 비트다.
    한 칸만 밀리면 숨은 디렉터리가 보라로 뜨는 식으로 조용히 어긋난다.
    """
    if tagged:
        return "tagged"
    if kind == "up":
        return "updir"
    if kind == "drive":
        return "drive"
    if kind == "dir":
        return "dir"
    e = entry or {}
    if e.get("h"):
        return "hidden"
    name = str(e.get("n") or "")
    # 앞머리 점은 확장자가 아니다(`.bashrc` 는 `bashrc` 확장자가 아니다) — 정본이
    # `e["n"][1:]` 로 첫 글자를 뺀 채 점을 찾는 이유다.
    ext = name.rsplit(".", 1)[-1].lower() if "." in name[1:] else ""
    if ext in ARCHIVE_EXTS:
        return "archive"
    if ext in EXT_TAGS:
        return EXT_TAGS[ext]
    if e.get("x"):
        return "exec"
    return "text"
