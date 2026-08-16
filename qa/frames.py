"""qa/frames.py — 실 GUI 창이 뜬 **프레임(PNG)** 을 읽는 한 벌의 술어.

`qa/screens.py` 가 실 Textual 클라의 ANSI 화면에 대해 하는 일을, 이 파일이 Rust
`pytmux-gui` 의 프레임에 대해 한다(`pytmux/pytmux-147`). 둘을 갈라 두는 이유는 재료가
다르기 때문이다 — 저쪽은 글자고 여기는 픽셀이다.

⛔ **의존성 0 을 지킨다**(`pytmux/qa-system` §0-4). Pillow 를 쓰면 QA 층이 이 저장소의
   유일한 런타임 의존성이 되고, 그러면 공개 클론에서 T3 이 통째로 못 돈다. PNG 디코딩은
   `zlib`(stdlib) + 언필터 40 줄이면 되므로 그 값이면 산다.

## 무엇을 재는가 — ⛔ **픽셀에서 글자를 읽지 않는다**

OCR 은 여기 없다(넣으면 의존성이 생기고, 위양성이 늘고, 폰트가 바뀌는 날 전건이 붉어진다).
그래서 판정 재료를 **글자를 안 읽고도 뜻이 서는 것**으로만 고른다:

| 술어 | 무엇을 잡나 |
| --- | --- |
| [`Frame.dominant_share`] | **통짜 한 색** — 창은 떴는데 아무것도 안 그린 프레임(Windows `PrintWindow` 가 까만 사각형을 성공으로 돌려주던 그 부류) |
| [`Frame.ink`] | 어느 **띠**에 그려진 것의 양 — 탭이 하나 늘면 탭바의 잉크가 는다 |
| [`Frame.alarm`] | **경보색**(빨강 계열) 픽셀 — GUI 가 화면에 띄우는 오류 배너 |
| [`Frame.diff_ratio`] | 두 프레임의 차이 — 조작이 **화면에 닿았나** |

⚠ 여기 있는 것은 전부 **증거를 위한 것이지 판정이 아니다** — 판정은 시나리오가 한다
(`qa/screens.py` 머리말과 같은 규율).
"""
from __future__ import annotations

import struct
import zlib
from collections import Counter
from dataclasses import dataclass

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class NotAFrame(Exception):
    """PNG 로 못 읽었다. ⛔ **빈 프레임으로 접지 않는다** — 못 읽은 것을 「아무것도 안
    그렸다」로 세면 디코더가 죽은 날 제품이 결함으로 신고된다(위양성 · 원칙 ⓓ)."""


def _unfilter(raw: bytes, width: int, height: int, bpp: int) -> bytearray:
    """PNG 스캔라인 필터를 푼다(필터 0~4 · PNG 명세 §9).

    ⚠ **필터가 줄마다 다르다** — `image` 크레이트의 인코더는 adaptive 라 한 프레임 안에
    Sub·Up·Paeth 가 섞여 나온다. 「전부 0 이겠지」로 짜면 조용히 무늬만 깨진 그림을 얻고,
    그 위의 오라클은 자기가 무엇을 보고 있는지 모른 채 판정한다.
    """
    stride = width * bpp
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for y in range(height):
        ft = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if len(line) != stride:
            raise NotAFrame(f"스캔라인이 짧다: y={y} {len(line)}B (기대 {stride}B)")
        if ft == 0:
            pass
        elif ft == 1:                                   # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ft == 2:                                   # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ft == 3:                                   # Average
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
        elif ft == 4:                                   # Paeth
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        else:
            raise NotAFrame(f"모르는 스캔라인 필터: {ft} (y={y})")
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return out


@dataclass
class Frame:
    """디코딩된 한 장. 픽셀은 `(r, g, b)` 3바이트로 눕힌다(알파는 버린다 — 창 캡처는
    불투명이고, 알파를 남기면 모든 술어가 4바이트 보폭을 다시 적어야 한다)."""

    width: int
    height: int
    rgb: bytes
    path: str = ""

    # ── 낱낱 ─────────────────────────────────────────────────────────────────
    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        i = (y * self.width + x) * 3
        return self.rgb[i], self.rgb[i + 1], self.rgb[i + 2]

    def counts(self, step: int = 7) -> Counter:
        """색 히스토그램. `step` 은 표본 간격이다 — 100만 픽셀을 전수로 세면 술어 하나가
        몇 초를 먹는다. 여기서 쓰는 판정은 전부 **비율**이라 표본으로 충분하다."""
        c = Counter()
        rgb = self.rgb
        for i in range(0, len(rgb) - 2, 3 * step):
            c[rgb[i:i + 3]] += 1
        return c

    # ── 술어 ─────────────────────────────────────────────────────────────────
    def background(self) -> bytes:
        """가장 흔한 색 = 바탕. 테마가 밝든 어둡든 같은 방식으로 잡힌다."""
        return self.counts().most_common(1)[0][0]

    def dominant_share(self) -> float:
        """가장 흔한 색이 차지하는 비율. 1.0 에 가까우면 **통짜 한 색**이다."""
        c = self.counts()
        return c.most_common(1)[0][1] / max(1, sum(c.values()))

    def distinct(self, limit: int = 4096) -> int:
        """서로 다른 색의 수(`limit` 에서 멈춘다 — 넘는지만 알면 되는 판정이라)."""
        seen = set()
        rgb = self.rgb
        for i in range(0, len(rgb) - 2, 3):
            seen.add(rgb[i:i + 3])
            if len(seen) >= limit:
                break
        return len(seen)

    def ink(self, top: int, bottom: int, background: bytes | None = None,
            tol: int = 12) -> int:
        """`[top, bottom)` 줄 띠에서 **바탕이 아닌** 픽셀 수.

        ⚠ 정확히 같은 색만 바탕으로 치면 안티에일리어싱된 바탕 언저리가 전부 잉크로
        세어져, 어느 띠나 잉크가 가득하다(= 판정이 뜻을 잃는다). 그래서 허용오차를 둔다.
        """
        bg = background or self.background()
        br, bg_, bb = bg[0], bg[1], bg[2]
        rgb = self.rgb
        n = 0
        for y in range(max(0, top), min(self.height, bottom)):
            base = y * self.width * 3
            for i in range(base, base + self.width * 3, 3):
                if (abs(rgb[i] - br) > tol or abs(rgb[i + 1] - bg_) > tol
                        or abs(rgb[i + 2] - bb) > tol):
                    n += 1
        return n

    def alarm(self, top: int = 0, bottom: int | None = None) -> int:
        """**경보색** 픽셀 수 — 빨강이 뚜렷이 앞서는 색.

        GUI 가 사용자에게 무언가 잘못됐다고 말하는 유일한 그림 신호다(연결 끊김 배너 등).
        T0 이 실 클라 화면에서 트레이스백을 찾는 것과 같은 자리이고, 여기서는 글자를 못
        읽으므로 **색**으로 찾는다. ⛔ 문턱을 낮추면 커서·선택색이 걸린다(위양성).
        """
        rgb = self.rgb
        lo = max(0, top) * self.width * 3
        hi = (self.height if bottom is None else min(self.height, bottom)) * self.width * 3
        n = 0
        for i in range(lo, hi, 3):
            r, g, b = rgb[i], rgb[i + 1], rgb[i + 2]
            if r >= 170 and r - g >= 60 and r - b >= 40:
                n += 1
        return n

    def diff_ratio(self, other: "Frame", top: int = 0,
                   bottom: int | None = None) -> float:
        """다른 프레임과 **다른 픽셀의 비율**. 크기가 다르면 1.0(통째로 다르다).

        `top`·`bottom` 을 주면 그 줄 띠 안에서만 센다. ★ **띠로 재는 것이 값이 클 때가
        있다** — 탭이 하나 느는 것은 화면 전체로는 0.6% 지만 탭바 띠 안에서는 그보다
        한참 크다(실측은 `qa/scenarios/t3_gui_window.py` 의 문턱 주석에 있다). 전체로만
        재면 그 문턱이 시계 한 칸(약 0.03%)과 너무 가까워진다.
        """
        if (self.width, self.height) != (other.width, other.height):
            return 1.0
        lo = max(0, top)
        hi = self.height if bottom is None else min(self.height, bottom)
        if hi <= lo:
            return 0.0
        a, b = self.rgb, other.rgb
        n = 0
        for i in range(lo * self.width * 3, hi * self.width * 3, 3):
            if a[i] != b[i] or a[i + 1] != b[i + 1] or a[i + 2] != b[i + 2]:
                n += 1
        return n / max(1, (hi - lo) * self.width)


def read_png(path: str) -> Frame:
    """PNG 한 장을 읽는다. 8비트 RGB/RGBA · 인터레이스 없음만 받는다(우리 하네스가 내는 것).

    ⛔ **모르는 모양을 조용히 받아들이지 않는다** — 받아들이면 그 위의 판정이 무엇을 보고
       있는지 아무도 모른다(파싱 실패를 초록으로 위장하는 정확한 방법 · 원칙 ⓑ).
    """
    with open(path, "rb") as fh:
        blob = fh.read()
    if not blob.startswith(PNG_MAGIC):
        raise NotAFrame(f"PNG 매직이 아니다: {path} ({blob[:8]!r})")
    pos = len(PNG_MAGIC)
    header = None
    idat = bytearray()
    while pos + 8 <= len(blob):
        (length,) = struct.unpack(">I", blob[pos:pos + 4])
        kind = blob[pos + 4:pos + 8]
        body = blob[pos + 8:pos + 8 + length]
        pos += 12 + length                                  # 길이4 + 종류4 + 본문 + CRC4
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
    if header is None:
        raise NotAFrame(f"IHDR 이 없다: {path}")
    width, height, depth, color, comp, filt, interlace = header
    if depth != 8 or color not in (2, 6) or interlace != 0 or comp != 0 or filt != 0:
        raise NotAFrame(
            f"못 읽는 PNG 모양이다: {path} — 깊이 {depth} · 색타입 {color} · "
            f"인터레이스 {interlace} (8비트 RGB/RGBA · 비인터레이스만 읽는다)")
    if not idat:
        raise NotAFrame(f"IDAT 이 없다: {path}")
    bpp = 3 if color == 2 else 4
    try:
        raw = zlib.decompress(bytes(idat))
    except zlib.error as e:
        # ⛔ 잘린 PNG 를 예외째 위로 던지면 시나리오가 「예외로 중단」으로 적히고, 그러면
        #    제품이 터진 것인지 이 층이 터진 것인지가 섞인다. 여기서 이름을 붙여 준다.
        raise NotAFrame(f"IDAT 을 못 푼다: {path} — {e}") from e
    flat = _unfilter(raw, width, height, bpp)
    if bpp == 3:
        rgb = bytes(flat)
    else:
        rgb = bytearray(width * height * 3)                 # 알파를 떼어 3바이트로 눕힌다
        for i in range(width * height):
            rgb[i * 3:i * 3 + 3] = flat[i * 4:i * 4 + 3]
        rgb = bytes(rgb)
    return Frame(width=width, height=height, rgb=rgb, path=path)
