"""폭 2(동아시아 와이드) 글자가 **격자에 두 번 들어가지 않는다** — pytmux/pytmux-208 상시 오라클.

제보(2026-08-10 · Windows 11): pytmux 패널의 Claude Code 시작 배너가
`이이  Claude는는  조조직직 …` 처럼 **한글 음절마다 두 번** 나왔다. ASCII·공백·마침표는
한 번이고 폭 2 글자만 두 배였다. 증거 사진을 칸 격자로 되재 본 결과 두 사본이 둘 다
«짝수 칸 경계»에 정확히 앉아 있었다 = 한 음절이 **온전한 2칸 슬롯을 두 개** 차지한다 =
페인트가 두 번 그린 것이 아니라 **`draw()` 가 그 글자를 두 번 받았다**(코멘트 #8571 §1).

그 코멘트가 「pytmux 의 POSIX 파이프라인은 이 줄을 안 겹친다」를 실측했는데 그 두 회차는
**저장소에 안 남겼다**(#8571 §6). 이 파일이 그 자리를 대신한다 — 다음 사람이 이 결함을
다시 만났을 때 「pytmux 격자 안인가 밖인가」를 **다시 재지 않고** 이 시험 하나로 가른다.

이 파일이 못박는 것은 둘이고, 둘째가 첫째만큼 중요하다:

  ① **안 늘린다** — 폭 2 글자 하나는 언제나 «본체 + 빈 연속칸("")» 두 칸이다. 바이트가
     어떤 경계로 쪼개져 들어와도 같다(영속 incremental decoder 의 carry).
  ② ⛔ **안 줄인다** — `쓸쓸`·`감감무소식`처럼 **원래 두 번인 글자**는 그대로 둔다.
     이 결함의 유혹적인 오답이 「연속 중복 접기」인데, 그것을 넣으면 표시 결함이
     **데이터 결함**으로 바뀐다(멀쩡한 낱말에서 글자가 말없이 사라진다). ①만 있으면
     그 오답이 초록으로 들어올 수 있어서, ②를 같은 파일에 둔다.

⛔ **이 시험이 초록인 것은 「제보가 틀렸다」가 아니다.** 유력한 자리는 pytmux 에 닿기 **전**
— Windows ConPTY 호스트(번들 `OpenConsole.exe`)가 자기 텍스트 버퍼를 VT 로 재방출할 때
와이드 글자의 **뒤 칸(trailing cell)** 을 안 건너뛰는 것이다(#8571 §4). 이 러너는 macOS 라
그 자리를 못 잰다. 가르는 절차는 이슈 코멘트 §5 에 있다(REC 캡처의 raw 바이트에서 `조조`
= UTF-8 `EC A1 B0` 두 번을 찾는다 → 있으면 격자 밖, 없으면 격자 안 = 이 시험이 붉어야 한다).

★ **되돌려서 재 봤다**(2026-08-15 · 초록만으로는 「안 묻는 시험」과 구별이 안 된다).
결함 셋을 실제로 주입해 이 넷이 각각 무엇을 잡는지 확인했다 — 넷이 **서로 다른 것**을 묻는다:

  | 주입한 결함 | 붉어진 시험 |
  | --- | --- |
  | A. 폭 2 글자를 `draw` 에 두 번 먹인다(= 제보 모양) | slot · no_duplicate · legit(늘어남) |
  | B. 연속 중복 접기(= 유혹적인 오답) | legit **만** |
  | C. 청크마다 carry 없이 디코드(= pywinpty 의 그 손상) | byte_chunk **만** |

A 를 넣으면 격자에 `🔒🔒 이이 Claude는는 조조직직 보보안안 …` 이 나온다 — 제보 사진과
글자 하나까지 같다. 즉 이 오라클은 **그 결함을 정확히 겨눈다.**

⚠ **재는 자리는 서버 격자와 그 직렬화까지다.** 클라 합성(`clientio._composite`)·
`clientwidgets.render_line`·Rust GUI 는 전부 연속칸("")을 **건너뛰는** 쪽이라 글자를 늘릴
자리가 없고(#8571 §3 이 읽어서 확인), 그 세 곳은 앱·소켓·GPU 를 띄워야 해서 여기서 안 잰다.
"""
import harness  # noqa: F401  (경로 설정)
from pytmuxlib.clientutil import _char_cells
from pytmuxlib.model import Pane


def _wide(ch):
    """이 시험이 «폭 2» 로 보는 글자 — pytmux 격자가 쓰는 폭 함수를 그대로 빌린다
    (여기서 따로 표를 들면 한 질문을 두 술어로 묻는 자리가 돼 언젠가 갈린다)."""
    return _char_cells(ch) == 2


# 제보된 그 줄. 앞 이모지는 제보 화면에서 U+FFFD 로 떨어졌지만 그것은 **별개 손상**
# (멀티바이트 청크 경계 no-carry 디코드 — `pytmuxlib/conpty.py` 머리말)이라 이 결함의
# 판정 근거에서 뺐다. 여기서는 폭 2 글자로서 그대로 넣어 ①의 대상에 포함한다.
BANNER = "🔒 이 Claude는 조직 보안 정책에 의해 관리됩니다."

# ⛔ ②의 대상 — **원래** 같은 폭 2 글자가 잇달아 오는 멀쩡한 우리말.
LEGIT_REPEATS = ["쓸쓸하다", "감감무소식", "곰곰이", "각각", "번번이"]


def _cells(pane, y=0):
    """행 y 의 셀 글자를 왼쪽부터 그대로 뽑는다(빈 연속칸은 "" 로 남긴다)."""
    row = pane.screen.buffer[y]
    return [row[x].data for x in range(pane.screen.columns)]


def _pairs(cells):
    """셀 목록을 «글자 → 차지한 칸 수» 로 접는다. 빈 연속칸("")은 앞 글자에 붙인다.
    폭 2 글자가 두 번 들어갔으면 여기서 같은 글자가 두 항목으로 나온다."""
    out = []
    for ch in cells:
        if ch == "" and out:
            out[-1][1] += 1
        elif ch != "":
            out.append([ch, 1])
    return [(ch, n) for ch, n in out]


def _feed(chunks, cols=80, rows=6):
    """바이트 청크들을 새 패널에 순서대로 먹이고 패널을 돌려준다."""
    p = Pane(-1, -1, cols, rows)
    for c in chunks:
        p.feed(c)
    return p


def _rendered(pane, y=0):
    """render 가 클라에 실어 보내는 그 행의 글자(직렬화 홉까지 함께 잰다)."""
    rows, _ = pane.render(False)
    return "".join(t for t, _ in rows[y])


# ---- ① 안 늘린다 ----

async def test_wide_chars_occupy_exactly_one_slot_each():
    """제보된 그 줄을 통째로 먹인다 — 폭 2 글자는 «본체 + 빈 칸» **두 칸 하나**뿐이다.

    붉으면 곧 「격자에 두 번 들어갔다」이고, 그때 이 결함은 pytmux 안이다."""
    p = _feed([BANNER.encode("utf-8")])
    got = _pairs(_cells(p))
    # 글자 순서와 **차지한 칸 수**를 함께 못박는다 — 칸 수까지 봐야 「글자는 한 번인데
    # 칸만 넷」 같은 중간 상태도 잡힌다.
    want = [(ch, 2 if _wide(ch) else 1) for ch in BANNER]
    assert got[:len(want)] == want, (
        f"격자가 원문과 다르다\n원문: {want[:12]}\n격자: {got[:12]}")
    # 뒤는 줄 끝 패딩(공백)뿐이라야 한다 — 원문 뒤에 사본이 붙지 않았음을 함께 잰다.
    assert {ch for ch, _ in got[len(want):]} <= {" "}, repr(got[len(want):][:8])

    # 직렬화 홉도 같은 답이라야 한다(서버가 클라에 실어 보내는 그 문자열).
    assert _rendered(p).rstrip() == BANNER, repr(_rendered(p))


async def test_no_duplicate_syllable_appears_anywhere():
    """제보 그대로의 모양(`조조`·`직직`·`는는`)이 화면에 없다.

    위 시험이 «있어야 할 것»을 재는 반면 이것은 «없어야 할 것»을 재, 원문 자체가
    바뀌어도(배너 문구가 개정돼도) 이 결함의 지문만은 계속 잡히게 한다."""
    p = _feed([BANNER.encode("utf-8")])
    line = _rendered(p)
    for ch in "이는조직보안정책에의해관리됩니다":
        assert ch * 2 not in line, f"{ch!r} 가 잇달아 두 번 나온다 — {line!r}"


async def test_byte_chunk_boundaries_do_not_duplicate_or_corrupt():
    """바이트가 **어떤 경계로 쪼개져** 들어와도 격자는 같다.

    ConPTY 경로는 read 청크마다 오고, 한글 한 자는 UTF-8 로 3바이트라 청크 경계가
    글자 한가운데를 지난다. 토크나이저의 영속 incremental decoder 가 carry 하지 않으면
    U+FFFD 가 나거나(제보 줄 맨 앞의 `��` 가 그 부류) 조각이 두 번 셀 수 있다."""
    raw = BANNER.encode("utf-8")
    whole = _pairs(_cells(_feed([raw])))

    for size in (1, 2, 3, 5, 7, 13):
        chunks = [raw[i:i + size] for i in range(0, len(raw), size)]
        got = _pairs(_cells(_feed(chunks)))
        assert got == whole, f"{size}바이트씩 먹였더니 격자가 달라졌다\n{got[:24]}"
        assert "�" not in "".join(ch for ch, _ in got), f"{size}바이트 경계에서 U+FFFD"


# ---- ② 안 줄인다 (⛔ 이 결함의 오답을 막는 자리) ----

async def test_legitimately_repeated_wide_chars_are_kept():
    """⛔ **원래 두 번인 글자를 접지 않는다.**

    이 결함의 유혹적인 오답이 「연속 중복 접기」다. 그것을 넣으면 `쓸쓸하다` 가
    `쓸하다` 가 되고 — 화면 결함 하나를 지우는 대신 **멀쩡한 글이 말없이 상한다.**
    표시 결함을 데이터 결함으로 바꾸는 거래라 안 한다(#8571 §6). 그 결정을 여기서 못박는다.

    ⚠ 그래서 pytmux 는 «받은 대로» 그린다 — 제보의 `조조직직` 도 그것이 정말 들어왔다면
    그대로 그리는 것이 옳다. 고칠 자리는 그 바이트를 낸 쪽이다."""
    for word in LEGIT_REPEATS:
        p = _feed([word.encode("utf-8")])
        got = _pairs(_cells(p))
        text = "".join(ch for ch, _ in got).rstrip()
        assert text == word, (
            f"{word!r} 가 {text!r} 로 바뀌었다 — 연속 중복을 접지도 늘리지도 마라")
        assert _rendered(p).rstrip() == word, repr(_rendered(p))

    # 「같은 글자가 잇달아 두 번」이 정확히 두 배 칸을 먹는지도 함께 — 접기가
    # 렌더 홉에만 들어와도 잡는다.
    p = _feed(["쓸쓸".encode("utf-8")])
    assert _pairs(_cells(p))[:2] == [("쓸", 2), ("쓸", 2)], _pairs(_cells(p))[:4]
