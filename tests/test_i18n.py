"""i18n 카탈로그 단위 테스트 (§6 ① 프레임워크 — 서버/클라 기동 불필요).

t() 조회·ko 폴백·포맷 치환, set_locale, resolve 우선순위, 플러그인 register 병합,
런타임 lang 선택의 클라이언트-로컬 영속 왕복을 검증한다."""
import os
import tempfile

import harness  # noqa: F401  (경로 설정)
from pytmuxlib import i18n


def _reset():
    """모듈 전역 카탈로그/로케일을 시드 직후 상태로 되돌린다(테스트 격리)."""
    i18n.set_locale("ko")


async def test_t_lookup_fallback_and_format():
    """t() 가 ① 현재 로케일 값 ② en 미번역 키는 ko 폴백 ③ 둘 다 없으면 키 자체
    ④ kw 포맷 치환 을 보장한다(점진 롤아웃 중 graceful degrade)."""
    _reset()
    i18n.register({
        "ko": {"x.only_ko": "한국어만", "x.greet": "안녕 {who}"},
        "en": {"x.greet": "hi {who}"},
    })
    # ① 현재 로케일(ko)
    i18n.set_locale("ko")
    assert i18n.t("x.greet", who="A") == "안녕 A"
    # ② en 으로 바꿔도 en 에 없는 키는 ko 폴백
    i18n.set_locale("en")
    assert i18n.t("x.only_ko") == "한국어만"
    assert i18n.t("x.greet", who="B") == "hi B"
    # ③ 아무 카탈로그에도 없으면 키 자체(개발 중 가시성)
    assert i18n.t("x.missing.key") == "x.missing.key"
    _reset()


async def test_t_format_failure_returns_raw():
    """포맷 인자 불일치(KeyError 등)는 예외 대신 원문을 돌려 렌더가 죽지 않게 한다."""
    _reset()
    i18n.register({"ko": {"x.fmt": "값 {n}"}, "en": {}})
    i18n.set_locale("ko")
    # n 을 안 줘도(KeyError) 예외 없이 원문
    assert i18n.t("x.fmt") == "값 {n}"
    _reset()


async def test_set_locale_rejects_unknown():
    """미지원 로케일은 폴백(ko)으로 떨어진다."""
    assert i18n.set_locale("ko") == "ko"
    assert i18n.set_locale("en") == "en"
    assert i18n.set_locale("fr") == "ko"     # 미지원 → 폴백
    assert i18n.set_locale("") == "ko"
    _reset()


async def test_resolve_priority():
    """resolve: config lang > 환경 LANG(ko*→ko, 그 외→en, 미설정→en)."""
    # config 가 지원 로케일이면 환경 무시
    assert i18n.resolve("en", {"LANG": "ko_KR.UTF-8"}) == "en"
    assert i18n.resolve("ko", {"LANG": "en_US.UTF-8"}) == "ko"
    # config 미지정/미지원 → 환경
    assert i18n.resolve(None, {"LANG": "ko_KR.UTF-8"}) == "ko"
    assert i18n.resolve("zz", {"LANG": "ko_KR.UTF-8"}) == "ko"
    assert i18n.resolve(None, {"LANG": "en_US.UTF-8"}) == "en"
    assert i18n.resolve(None, {"LC_ALL": "ko_KR.UTF-8", "LANG": "en_US"}) == "ko"
    # 미설정/C 로케일 → en(영어권 기본)
    assert i18n.resolve(None, {}) == "en"
    assert i18n.resolve(None, {"LANG": "C"}) == "en"


async def test_register_merge_overwrites():
    """register 는 누적 병합하고, 같은 키 재등록은 덮어쓴다(플러그인 우선)."""
    i18n.register({"ko": {"x.a": "A1"}, "en": {}})
    i18n.register({"ko": {"x.a": "A2", "x.b": "B"}, "en": {}})
    i18n.set_locale("ko")
    assert i18n.t("x.a") == "A2"
    assert i18n.t("x.b") == "B"
    _reset()


async def test_persist_roundtrip():
    """load/save_persisted 가 클라이언트-로컬 파일로 왕복하고, 미지원·부재는 None."""
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "default.sock")
        # 부재 시 None(→ resolve 로 결정)
        assert i18n.load_persisted(sock) is None
        i18n.save_persisted(sock, "en")
        assert i18n.load_persisted(sock) == "en"
        i18n.save_persisted(sock, "ko")
        assert i18n.load_persisted(sock) == "ko"
        # 손상/미지원 값은 None
        with open(i18n._lang_file(sock), "w", encoding="ascii") as f:
            f.write("xx")
        assert i18n.load_persisted(sock) is None


async def test_catalog_locales_symmetric():
    """코어 시드 카탈로그의 ko·en 키 집합이 일치해야 한다(단계 ②~⑤ 누락 가드).

    한쪽에만 있는 키는 폴백으로 동작하긴 하지만, 시드(코어 문자열)는 항상 양 로케일을
    완비해 영어 사용자가 한국어로 새는 문자열을 빌드 시점에 잡는다."""
    # 다른 테스트가 주입하는 비대칭 테스트 전용 키("x.*")는 제외 — 모듈 전역 카탈로그라
    # 실행 순서에 따라 섞일 수 있다. 시드(실 도메인 키)만 대칭이면 된다.
    ko_keys = {k for k in i18n._CATALOG["ko"] if not k.startswith("x.")}
    en_keys = {k for k in i18n._CATALOG["en"] if not k.startswith("x.")}
    assert ko_keys == en_keys, {
        "ko_only": sorted(ko_keys - en_keys),
        "en_only": sorted(en_keys - ko_keys),
    }


async def test_command_catalog_symmetric_and_translated():
    """clientutil 을 import 하면 §6 ③ 명령/카테고리/메뉴 카탈로그가 ko(데이터 자동시드)
    +en 으로 등록되고, cmd.*/cat.*/menu.* 키가 양 로케일 대칭이며 실제로 번역돼야 한다."""
    from pytmuxlib import clientutil  # noqa: F401  (import 시 카탈로그 시드)
    for pfx in ("cmd.", "cat.", "menu."):
        ko = {k for k in i18n._CATALOG["ko"] if k.startswith(pfx)}
        en = {k for k in i18n._CATALOG["en"] if k.startswith(pfx)}
        assert ko and en, pfx
        assert ko == en, {"prefix": pfx, "ko_only": sorted(ko - en),
                          "en_only": sorted(en - ko)}
    # 자동 시드된 ko = COMMANDS 원본, en = 번역
    i18n.set_locale("ko")
    assert i18n.t("cmd.kill-pane") == "현재 패널 삭제"
    assert i18n.t("cat.패널") == "패널"
    i18n.set_locale("en")
    assert i18n.t("cmd.kill-pane") == "Delete current pane"
    assert i18n.t("cat.패널") == "Pane"
    assert i18n.t("menu.zoom") == "Toggle pane zoom ⛶"
    # 미등록(플러그인 가정) 명령은 default 로 원본 유지
    assert i18n.t("cmd.__nonexistent__", default="원본") == "원본"
    _reset()


async def test_keylist_key_column_no_hangul_in_en():
    """키 바인딩 레퍼런스(설정 '키' 탭)의 **키표기 열**은 EN 로케일에서 한글이 새면
    안 된다 — 보통 기호(↑ ↓ % 등)라 번역 안 하지만, e_up/e_tb 처럼 그 자리에 한글
    설명문이 든 항목은 kkey.<id> 로 번역돼야 한다(clientscreens 가 그렇게 렌더). 회귀:
    en 에서 모든 항목의 키표기에 한글 음절이 없어야."""
    from pytmuxlib import clientutil
    i18n.set_locale("en")
    bad = []
    for kid, k, _ko, _en in clientutil.ESC_MODE_KEYS + clientutil.PREFIX_KEYS:
        shown = i18n.t(f"kkey.{kid}", default=k)
        if any("가" <= ch <= "힣" for ch in shown):
            bad.append((kid, shown))
    assert not bad, f"EN 키표기에 한글이 남음: {bad}"
    # ko 에선 원래 한글 라벨이 보존된다(영문 전환만 고친 것이지 ko 를 깨지 않음).
    i18n.set_locale("ko")
    assert i18n.t("kkey.e_tb", default="X") == "탭바 포커스 후"
    _reset()


async def test_plugin_catalog_registered_and_translated():
    """플러그인(claude-code) 로드 시 claude.*/플러그인 cmd.* 카탈로그가 등록되고,
    core usage.* 와 함께 ko/en 대칭·번역돼야 한다(§6 ⑤)."""
    from pytmuxlib import plugins
    plugins.load()        # 플러그인 import → 카탈로그 등록(claude.*·cmd.<plugin>)
    for pfx in ("claude.", "usage."):
        ko = {k for k in i18n._CATALOG["ko"] if k.startswith(pfx)}
        en = {k for k in i18n._CATALOG["en"] if k.startswith(pfx)}
        assert ko and ko == en, {"prefix": pfx, "ko_only": sorted(ko - en),
                                "en_only": sorted(en - ko)}
    # 플러그인 명령 설명이 코어 cmd.* 키로 등록돼 번역된다.
    i18n.set_locale("en")
    assert i18n.t("cmd.auto-resume") == "Auto-resume on token limit [on|off]"
    assert i18n.t("claude.auto_resume") == "auto-resume"
    assert i18n.t("usage.session_5h") == "Session 5h"
    i18n.set_locale("ko")
    assert i18n.t("claude.auto_resume") == "자동재개"
    assert i18n.t("usage.session_5h") == "세션 5h"
    # claude-token-usage-view 플러그인(§6.1 후속): uview.* 화면/오버레이 + 명령 설명.
    uv_ko = {k for k in i18n._CATALOG["ko"] if k.startswith("uview.")}
    uv_en = {k for k in i18n._CATALOG["en"] if k.startswith("uview.")}
    assert uv_ko and uv_ko == uv_en, {"ko_only": sorted(uv_ko - uv_en),
                                      "en_only": sorted(uv_en - uv_ko)}
    i18n.set_locale("en")
    assert i18n.t("uview.title") == "Claude usage limit (/usage)"
    assert i18n.t("cmd.usage-view").startswith("Claude usage limit")
    i18n.set_locale("ko")
    assert i18n.t("uview.title") == "Claude 사용 한도 (/usage)"
    _reset()


async def test_client_screen_keys_translated():
    """§6 추가(2026-06-17): 그동안 한국어로 새던 클라 팝업/안내(AR·restart·version·
    host status·remote/vt-parser·notice 닫기)가 en 로 실제 번역된다(완전 ko/완전 en)."""
    keys = ("ar.title", "ar.line1", "restart.confirm_q", "restartcheck.title",
            "version.header", "hoststatus.host", "msg.remote_attach_usage",
            "msg.vt_parser_usage", "msg.display_no_output", "ui.notice_close")
    for k in keys:
        assert k in i18n._CATALOG["ko"] and k in i18n._CATALOG["en"], k
    i18n.set_locale("en")
    assert i18n.t("ar.title") == "Autoresume (AR)"
    assert i18n.t("restart.confirm_q") == "Restart anyway?"
    assert i18n.t("msg.display_no_output") == "(no output)"
    # 포맷 키도 en 으로 치환
    assert i18n.t("hoststatus.host", host="h1") == "Host: h1"
    i18n.set_locale("ko")
    assert i18n.t("ar.title") == "자동 재개 (AR · Autoresume)"
    assert i18n.t("restart.confirm_q") == "그래도 재시작할까요?"
    _reset()


async def test_seed_catalog_has_both_locales():
    """코어 시드 키는 ko·en 둘 다 존재해야 한다(누락 시 폴백이지만 시드는 완전성 유지)."""
    for key in ("lang.usage", "capture.status_on", "capture.status_off"):
        assert key in i18n._CATALOG["ko"], key
        assert key in i18n._CATALOG["en"], key
    # en/ko 가 실제로 다른 문자열인지(번역됨) 하나 확인
    i18n.set_locale("en")
    assert i18n.t("capture.status_on") == "Status: ON (capturing)"
    i18n.set_locale("ko")
    assert i18n.t("capture.status_on") == "상태: ON (캡처 중)"
    _reset()
