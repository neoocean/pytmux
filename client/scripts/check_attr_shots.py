#!/usr/bin/env python3
"""`gen_attr_shots.sh` 가 뜬 그림을 재서 «속성이 실제로 그려졌나»를 판정한다.

pytmux-33 축 ⑷ 의 라이브 절반. 헤드리스 겹(`gui/src/attr_render_conformance.rs`)은
"그리기 판정이 달라지나"까지 재고, 여기서 재는 것은 그 아래 — **그래서 픽셀이 그렇게
나왔나** 다. 그 층에서만 잡힌 것이 실제로 있다: 기울임을 걸었더니 한글이 두부(▯)가 됐고
(2026-08-04 실측) 단위 오라클 열 개가 전부 초록이었다.

# 무엇을 묻나

⑴ 속성마다 **ASCII 칸의 그림이 바뀌나** — 안 바뀌면 그 속성은 화면에 없다(pytmux-123 의
   밑줄이 그랬다).
⑵ **기울임에서 한글 칸이 하나도 안 바뀌나** — `fallback_safe` 가 보조 글꼴 조각에서
   기울임을 **일부러 뺀다**(그 얼굴이 없어 두부가 되므로 · 사용자 결정 2026-08-04).
   그 결정이 지켜지면 한글 칸은 민 화면과 **바이트가 같아야** 한다. 0 이 아니면 둘 중
   하나다 — 그 규칙이 풀렸거나, 한글이 다른 글리프로 떨어졌거나.

   ☠ **이 줄은 맥에서 반증되지 않는다 — 그것을 실측으로 확인했다**(2026-09-01).
   `fallback_safe` 의 보호를 통째로 풀고 다시 떠도 한글 칸이 **0** 이다: 이 상자의 보조
   글꼴에 이탤릭 얼굴이 아예 없어 `select_font` 가 보통 얼굴을 그대로 돌려준다. 두부가
   된 것은 그 얼굴이 **있는** 상자였다(2026-08-04 · 캡처 `2026-08-04-text-attrs`).
   ⇒ 이 줄이 실제로 무언가를 잡는 자리는 그런 상자이고, 여기서는 **잠자코 초록**이다.
   ⛔ 그렇다고 지우지 마라 — 대신 그 상자에서 도는 것이 이 자의 값이고, 같은 뮤테이션을
      **헤드리스 겹**은 여기서도 잡는다(`italic_is_dropped_where_the_fallback_font_would_have_no_face`).
      두 겹이 서로 다른 것을 잡는다는 것이 이 축의 요점이다.

⛔ **굵게의 한글은 판정하지 않고 «세기만» 한다.** 보조 글꼴에 굵은 얼굴이 있으면 바뀌고
   없으면 안 바뀌는데, 그것은 이 상자의 글꼴 사정이지 제품의 계약이 아니다.

# 못 쟀을 때는 못 쟀다고 말한다 (rc 2)

`--frame-dump` 은 뜬 지 몇 초 만에 한 장을 뜨는데, **보조 글꼴이 앉기 전 프레임**이 잡히는
회차가 있다(2026-09-01 실측: 같은 표본을 두 번 떠서 fg·bg 의 한글만 값이 갈렸고, 그 회차의
한글은 칸 격자가 아니라 자연폭으로 뭉쳐 있었다 — 폭 45px vs 58px). 그 그림으로 위 둘을
판정하면 **없는 결함**이 보인다. 그래서 한글 칸의 오른쪽 끝이 장마다 어긋나면 붉게 하지 않고
**「못 쟀다」로 떨어진다** — 처방은 다시 뜨는 것이다.

사용법: check_attr_shots.py [디렉터리]     (기본 /tmp/pytmux-attr-shots)
종료:   0 다 옳다 · 1 결함 · 2 못 쟀다(다시 떠라) · 3 그림이 모자란다
"""
import sys
import pathlib

try:
    from PIL import Image
    import numpy as np
except ImportError:  # 이 하네스만의 의존이다 — 게이트가 아니므로 없으면 그렇게 말하고 끝낸다.
    print("PIL·numpy 가 필요하다(pip install pillow numpy)", file=sys.stderr)
    sys.exit(3)

# `gen_attr_shots.sh` 의 이름과 같은 순서. `plain` 이 기준이다.
ATTRS = ["bold", "italic", "underline", "reverse", "strike", "fg", "bg"]
# 칸을 통째로 칠하는 것들 — 한글 칸이 잉크로 꽉 차므로 «격자에 앉았나» 판정에서 뺀다.
FILLS_CELL = {"reverse", "bg"}


def load(d, name):
    p = d / f"{name}.png"
    if not p.exists():
        print(f"그림이 없다: {p}", file=sys.stderr)
        sys.exit(3)
    return np.asarray(Image.open(p).convert("RGB")).astype(int)


def ink_mask(img):
    """판 안쪽 배경색과 다른 픽셀."""
    bg = img[img.shape[0] // 2, img.shape[1] // 2]
    return np.abs(img - bg).sum(2) > 30


def geometry(plain, blank):
    """**민 화면 ↔ 빈 줄 화면의 차이**로 첫 글줄의 자리와 ASCII/한글 경계를 잡는다.

    ⛔ 좌표를 못박지도, "잉크가 있는 첫 띠"로 찾지도 않는다. 창 크기·글꼴이 상자마다
    다르고, 후자는 **탭 줄을 글줄로 집는다** — 그러면 모든 값이 0 이 되어 "속성이 하나도
    안 그려진다"는 거짓 붉음이 된다(실측 2026-09-01, 이 판정기의 첫 판이 그랬다).

    두 장의 다른 곳은 정의상 **그 한 줄뿐**이다(크롬도 프롬프트도 같은 자리다). 아래쪽
    상태줄의 시계가 그 사이에 넘어갈 수 있으므로 **맨 위 띠**만 취한다 — 글줄은 판의
    첫 줄이고 시계는 창 바닥이라 섞이지 않는다.
    """
    diff = (np.abs(plain - blank).sum(2) > 12)
    rows = [y for y in range(diff.shape[0]) if diff[y].sum() > 0]
    if not rows:
        print("민 화면과 빈 줄 화면이 같다 — 표본이 안 찍혔다(서버·send-keys 를 볼 것)", file=sys.stderr)
        sys.exit(2)
    band = [rows[0]]
    for y in rows[1:]:
        if y - band[-1] > 2:
            break
        band.append(y)
    # 밑줄·취소선은 글자 띠 밖으로 삐져나온다 — 위아래로 넉넉히 넓힌다.
    y0, y1 = max(0, band[0] - 5), band[-1] + 7

    cols = [x for x in range(diff.shape[1]) if diff[band[0]:band[-1] + 1, x].sum() > 0]
    # ASCII 여덟 자와 한글 넉 자 사이의 **빈 칸**이 첫 큰 틈이다.
    split = None
    for i in range(1, len(cols)):
        if cols[i] - cols[i - 1] >= 6:
            split = (cols[i - 1], cols[i])
            break
    if split is None:
        print("ASCII 와 한글 사이의 빈 칸을 못 찾았다 — 표본 글이 바뀌었나", file=sys.stderr)
        sys.exit(2)
    return y0, y1, slice(cols[0], split[0] + 1), slice(split[1], cols[-1] + 1)


def write_strip(d, y0, y1, ascii_x, han_x):
    """여덟 장의 **그 한 줄만** 잘라 세로로 쌓은 대조 그림 한 장.

    pytmux-33 이 "스크린샷 대조가 자의 일부여야 한다"고 못박은 자리다 — 수는 위 표가
    말하고, 사람이 눈으로 보는 것은 이 한 장이다(밑줄이 그어졌나 · 기울임이 기울었나 ·
    한글이 두부가 아닌가). 3배로 키워 굽는다: 칸이 8px 라 원본 크기로는 안 보인다.
    """
    from PIL import ImageDraw
    names = ["plain"] + ATTRS
    x0, x1 = ascii_x.start - 4, han_x.stop + 8
    crops = [Image.open(d / f"{n}.png").convert("RGB").crop((x0, y0, x1, y1)) for n in names]
    w, h = crops[0].size
    scale, pad = 3, 130
    out = Image.new("RGB", (w * scale + pad, h * scale * len(names) + 12), (18, 20, 28))
    draw = ImageDraw.Draw(out)
    for i, (name, crop) in enumerate(zip(names, crops)):
        out.paste(crop.resize((w * scale, h * scale), Image.NEAREST), (pad, 6 + i * h * scale))
        draw.text((10, 6 + i * h * scale + h * scale // 2 - 6), name, fill=(205, 210, 225))
    path = d / "strip.png"
    out.save(path)
    return path


def main():
    d = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pytmux-attr-shots")
    plain = load(d, "plain")
    y0, y1, ascii_x, han_x = geometry(plain, load(d, "blank"))

    # ── 못 쟀나 먼저 본다 — 한글이 칸 격자에 앉았나(장마다 같은 자리에 끝나나)
    edges = {}
    for name in ["plain"] + [a for a in ATTRS if a not in FILLS_CELL]:
        img = load(d, name)
        ink = ink_mask(img)[y0:y1, han_x]
        xs = [x for x in range(ink.shape[1]) if ink[:, x].sum() > 0]
        edges[name] = xs[-1] if xs else -1
    spread = max(edges.values()) - min(edges.values())
    if spread > 4:
        print(f"⚠ 못 쟀다 — 한글 칸의 오른쪽 끝이 장마다 {spread}px 어긋난다: {edges}")
        print("  보조 글꼴이 앉기 전 프레임이 섞였다. 다시 떠라(gen_attr_shots.sh).")
        return 2

    bad = []
    print(f"{'속성':10} {'ASCII 바뀐 픽셀':>14} {'한글 바뀐 픽셀':>14}")
    for name in ATTRS:
        img = load(d, name)
        da = int((np.abs(img[y0:y1, ascii_x] - plain[y0:y1, ascii_x]).sum(2) > 12).sum())
        dh = int((np.abs(img[y0:y1, han_x] - plain[y0:y1, han_x]).sum(2) > 12).sum())
        print(f"{name:10} {da:14d} {dh:14d}")
        if da == 0:
            bad.append(f"{name}: 켰는데 ASCII 칸의 그림이 그대로다 — 그 속성이 화면에 없다")
        if name == "italic" and dh != 0:
            bad.append(
                f"italic: 한글 칸이 {dh}픽셀 바뀌었다 — 보조 글꼴 조각에서 기울임을 빼는 "
                "규칙(fallback_safe)이 풀렸다. 그 얼굴이 없으면 글자가 두부(▯)가 된다"
            )

    strip = write_strip(d, y0, y1, ascii_x, han_x)
    print(f"\n대조 그림: {strip}")

    if bad:
        print()
        for line in bad:
            print("✗ " + line)
        return 1
    print("\nOK: 일곱 속성이 전부 그림을 바꾼다 · 기울임은 한글 칸을 안 건드린다")
    return 0


if __name__ == "__main__":
    sys.exit(main())
