"""qa/inventory.py — 명령 인벤토리. ⚠ **블랙박스 원칙(ⓐ)의 유일한 예외다.**

여기서 저장소 텍스트를 읽는 이유는 하나다: **로직이 아니라 인벤토리**를 뽑기 때문이다.
목록만 가져오면 새 명령이 생긴 날 커버리지 구멍이 **저절로** 드러난다 — 손으로 적은
목록은 그날 조용히 낡는다. 판정(무엇이 옳은 동작인가)은 여전히 밖에서만 한다.

⛔ **여기서 제품을 import 하지 않는다.** import 하면 그 표를 만든 코드가 같이 돌아
「표가 있다」와 「표가 도는 서버에 실제로 등록됐다」가 뒤섞인다. AST·정규식으로 **소스
텍스트만** 읽는다(러스트는 파서가 없어 정규식이고, 그래서 표 범위를 먼저 좁힌다).

⛔ **빈 목록을 조용히 돌려주지 않는다**(원칙 ⓑ). 0건은 「전부 커버했다」로 읽히고 그것이
파싱 실패를 초록으로 위장하는 정확한 방법이다 — 못 읽으면 `InventoryBroken` 을 던지고,
런은 그것을 **결함**으로 적는다(`qa/ledger.py`).

## 네 표면과 그 자리

| 표면 | 저장소의 자리 | 무엇인가 |
| --- | --- | --- |
| `control` | `pytmuxlib/server.py` `handle_control` + `_ONOFF_CONTROLS` | 외부 CLI(`pytmux cmd …`)가 지나는 제어 라인 |
| `cmd-table` | `pytmuxlib/servercmd.py` `_CMD_TABLE` | 실 클라가 `cmd` 프레임으로 부르는 서버 액션 |
| `client-commands` | `pytmuxlib/clientutil.py` `COMMANDS` | 파이썬 클라 명령 프롬프트(`:`)의 이름 |
| `palette` | `client/crates/base/src/keymap.rs` `PALETTE` | 러스트 GUI 명령 팔레트의 이름 |

⛔ **`tests/test_control_command_golden.py` 와 겹치지 않는다.** 그 골든이 고정하는 것은
**철자와 disposition**(조용한 증감 금지)이고, 여기서 쓰는 것은 그 목록 대비 **실제로
돌려 봤는가**다. 둘을 헷갈리면 같은 판정을 두 벌 쓰게 된다(`pytmux/qa-system` §0-1).
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass
from typing import Callable

from .env import ROOT

CONTROL_SRC = os.path.join("pytmuxlib", "server.py")
CMD_TABLE_SRC = os.path.join("pytmuxlib", "servercmd.py")
CLIENT_CMD_SRC = os.path.join("pytmuxlib", "clientutil.py")
PALETTE_SRC = os.path.join("client", "crates", "base", "src", "keymap.rs")


class InventoryBroken(Exception):
    """인벤토리를 못 읽었다. ⛔ **빈 목록으로 떨어지지 않는다** — 0건은 원장에서
    「전부 커버」와 같은 모양이라, 파서가 죽은 날 커버리지가 100% 로 뜬다."""


# ── 읽기 ──────────────────────────────────────────────────────────────────────

def _read(rel: str) -> str:
    path = os.path.join(ROOT, rel)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as e:
        raise InventoryBroken(f"{rel} 을 못 읽었다: {e}") from e


def _tree(rel: str) -> ast.Module:
    try:
        return ast.parse(_read(rel))
    except SyntaxError as e:
        raise InventoryBroken(f"{rel} 을 못 파싱했다: {e}") from e


def _strs(node: ast.AST) -> set[str]:
    """`"a"` · `("a", "b")` · `["a"]` 어느 모양이든 문자열 리터럴만 뽑는다."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        out: set[str] = set()
        for e in node.elts:
            out |= _strs(e)
        return out
    return set()


# ── 표면별 추출 ───────────────────────────────────────────────────────────────

def control_spellings() -> tuple[str, ...]:
    """제어 라인 명령 철자 전수(별칭 포함) + on/off 표 키.

    `handle_control` 은 if/elif 체인이라 **런타임에 열거할 표가 없다** — 그래서 소스에서
    `c` 와 비교되는 문자열을 뽑는다(골든 테스트가 쓰는 것과 같은 방법이고, 같은 이유다).
    """
    tree = _tree(CONTROL_SRC)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "handle_control"), None)
    if fn is None:
        raise InventoryBroken(f"{CONTROL_SRC} 에 handle_control 이 없다")
    names: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Compare) and isinstance(node.left, ast.Name) \
                and node.left.id == "c":
            for cmp_ in node.comparators:
                names |= _strs(cmp_)
    names |= _onoff_keys(tree)
    if not names:
        raise InventoryBroken("handle_control 에서 명령 철자를 하나도 못 뽑았다")
    return tuple(sorted(names))


def _onoff_keys(tree: ast.Module) -> set[str]:
    """`Server._ONOFF_CONTROLS` 의 키 — 체인이 아니라 표라 `c` 비교에 안 잡힌다."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "_ONOFF_CONTROLS"
                   for t in node.targets):
            continue
        if isinstance(node.value, ast.Dict):
            keys = {k.value for k in node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if keys:
                return keys
    raise InventoryBroken(f"{CONTROL_SRC} 에서 _ONOFF_CONTROLS 표를 못 읽었다")


def cmd_table_actions() -> tuple[str, ...]:
    """`_CMD_TABLE` 에 등록되는 action 전수 — `@_cmd("…", DISP)` 데코레이터에서 뽑는다."""
    tree = _tree(CMD_TABLE_SRC)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_cmd" and node.args:
            out |= _strs(node.args[0])
    if not out:
        raise InventoryBroken(f"{CMD_TABLE_SRC} 에서 _cmd 등록을 하나도 못 뽑았다")
    return tuple(sorted(out))


def client_commands() -> tuple[str, ...]:
    """`clientutil.COMMANDS` 의 이름 칸(첫 칸) 전수."""
    tree = _tree(CLIENT_CMD_SRC)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "COMMANDS" for t in node.targets):
            continue
        out: set[str] = set()
        for e in getattr(node.value, "elts", []):
            elts = getattr(e, "elts", None)
            if elts:
                out |= _strs(elts[0])
        if out:
            return tuple(sorted(out))
    raise InventoryBroken(f"{CLIENT_CMD_SRC} 에서 COMMANDS 목록을 못 읽었다")


#: `pe("이름", "카테고리", Action::…)` 한 줄. 러스트라 AST 가 없으니 **표 범위를 먼저
#: 좁힌 뒤** 정규식을 건다 — 파일 전체에 걸면 다른 표의 항목까지 섞인다.
_PE = re.compile(r'\bpe\(\s*"([^"]*)"')
_PALETTE_HEAD = "pub static PALETTE"


def palette_names() -> tuple[str, ...]:
    """러스트 GUI 명령 팔레트(`base::PALETTE`)에 실린 이름 전수."""
    src = _read(PALETTE_SRC)
    i = src.find(_PALETTE_HEAD)
    if i < 0:
        raise InventoryBroken(f"{PALETTE_SRC} 에 {_PALETTE_HEAD} 가 없다")
    j = src.find("\n];", i)
    if j < 0:
        raise InventoryBroken(f"{PALETTE_SRC} 의 PALETTE 표 끝(`];`)을 못 찾았다")
    out = set(_PE.findall(src[i:j]))
    if not out:
        raise InventoryBroken("PALETTE 표에서 이름을 하나도 못 뽑았다")
    return tuple(sorted(out))


# ── 표면 ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Surface:
    """인벤토리 하나와 **그것을 지날 수 있는 시나리오**.

    ★ `reached_by` 가 이 표의 핵심이다. 이 값이 없는 표면은 지금 어느 티어도 못 지나므로
    **미커버가 아니라 미검증**이다 — 결함으로 내면 아무도 고칠 수 없는 이슈가 서고,
    그런 이슈가 QA 를 끈다(원칙 ⓓ). 대신 회계에 남긴다(원칙 ⓑ: 통과가 아니다).
    """
    key: str
    title: str
    where: str
    extract: Callable[[], "tuple[str, ...]"]
    reached_by: str | None
    note: str


SURFACES: tuple[Surface, ...] = (
    Surface(
        key="control",
        title="제어 라인 명령",
        where=f"{CONTROL_SRC} · handle_control + _ONOFF_CONTROLS",
        extract=control_spellings,
        reached_by="T1-commands",
        note="",
    ),
    Surface(
        key="cmd-table",
        title="서버 cmd 표",
        where=f"{CMD_TABLE_SRC} · _CMD_TABLE",
        extract=cmd_table_actions,
        reached_by=None,
        note="실 클라가 붙어 `cmd` 프레임을 보내야 닿는다 — 제어 라인(CLI)에는 이 표면이 "
             "없다(handshake 뒤에만 열린다). 액션마다 인자 규약이 달라 무작정 쏘면 "
             "그건 커버리지가 아니라 퍼즈(T4)다",
    ),
    Surface(
        key="client-commands",
        title="파이썬 클라 명령 프롬프트",
        where=f"{CLIENT_CMD_SRC} · COMMANDS",
        extract=client_commands,
        reached_by=None,
        note="사람이 `:` 프롬프트에 치는 자리다 — 실 클라에 키를 넣는 층(T4 키·마우스)이 "
             "있어야 지난다",
    ),
    Surface(
        key="palette",
        title="러스트 GUI 명령 팔레트",
        where=f"{PALETTE_SRC} · PALETTE",
        extract=palette_names,
        reached_by=None,
        # ⛔ **T3(pytmux-147)이 생겼어도 `reached_by` 는 비운다.** T3 은 팔레트를 «열어
        #    보고»(키 배선) 거기서 멈추지 이름으로 하나씩 실행하지 않는다 — 한 번 실행할
        #    때마다 창을 새로 띄워야 하고(실측 프레임 한 장 약 10.6초) 그러면 이 표면
        #    하나에 20분이 넘는다. 여기를 T3 으로 적는 순간 120건이 전부 «미커버»(결함)로
        #    서고, 그건 회계가 아니라 늑대소년이다(원칙 ⓓ).
        note="실 GUI 창(Rust pytmux-gui)의 팔레트다 — T3 은 팔레트를 열어 보는 데까지고, "
             "이름으로 하나씩 실행하는 층은 아직 없다",
    ),
)


def by_key(key: str) -> Surface:
    for s in SURFACES:
        if s.key == key:
            return s
    raise KeyError(key)
