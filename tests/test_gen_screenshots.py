"""scripts/gen_screenshots.py 의 색 검증 — 거짓 초록 방지(pytmux-266).

`shoot()`/`_worker()`(진짜 서버·Textual 헤드리스 앱을 띄운다)는 여기서 재현하지
않는다 — 순수 판정 함수(`_is_colorless`)와 import 시점의 `NO_COLOR` 처리만 잰다."""
import importlib
import os
import sys
import tempfile

import harness  # noqa: F401  (경로 설정)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "scripts"))


def _write_svg(body):
    fd, path = tempfile.mkstemp(suffix=".svg")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    return path


async def test_importing_pops_no_color_so_capture_stays_colored():
    # ☠ `NO_COLOR` 를 내보내는 것은 사람 셸이 아니라 에이전트 셸이다 — 이 저장소를
    # 고치는 손이 대개 그것이라, import 시점에 걷지 않으면 "그림 다시 떠라" 회차는
    # 기본적으로 흑백을 낳는다.
    os.environ["NO_COLOR"] = "1"
    try:
        sys.modules.pop("gen_screenshots", None)
        importlib.import_module("gen_screenshots")
        assert "NO_COLOR" not in os.environ, "NO_COLOR 를 걷지 않았다"
    finally:
        os.environ.pop("NO_COLOR", None)


async def test_a_real_color_survives_the_chrome_exclusion():
    gen_screenshots = importlib.import_module("gen_screenshots")
    path = _write_svg('<svg><rect fill="#0178d4"/><text fill="#ff5f57">x</text></svg>')
    try:
        assert not gen_screenshots._is_colorless(path)
    finally:
        os.unlink(path)


async def test_traffic_lights_alone_do_not_count_as_color():
    # Rich 가 **모든** 터미널 SVG 껍데기에 이 셋을 늘 박는다(창 장식) — 이것만 있고
    # 본문 색이 전부 회색조면 진짜 NO_COLOR 사고와 구별이 안 된다.
    gen_screenshots = importlib.import_module("gen_screenshots")
    path = _write_svg(
        '<svg><circle fill="#ff5f57"/><circle fill="#febc2e"/>'
        '<circle fill="#28c840"/><rect fill="#656565"/><text fill="#1a1a1a">x</text></svg>'
    )
    try:
        assert gen_screenshots._is_colorless(path), "신호등 셋에 속아 컬러로 오판했다"
    finally:
        os.unlink(path)


async def test_no_colors_at_all_counts_as_colorless():
    gen_screenshots = importlib.import_module("gen_screenshots")
    path = _write_svg("<svg></svg>")
    try:
        assert gen_screenshots._is_colorless(path)
    finally:
        os.unlink(path)
