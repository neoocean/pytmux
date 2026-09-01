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


async def test_en_catalog_has_no_hangul_leak():
    """en 로케일 카탈로그 값에 한글이 새지 않는다(i18n 전수조사 회귀 2026-06-19).

    UI 표면 문자열은 전부 i18n 을 거치고 en 값은 영문이어야 한다. 예외는 **언어 자체의
    이름**(autonym) — 'Deutsch' 가 영어 UI 에서도 'Deutsch' 이듯 한국어는 '한국어'로
    표기한다. 그 외 en 값에 한글이 있으면 누출이므로 실패시킨다. 카탈로그를 채우려면
    주요 UI 모듈(코어 클라 + 플러그인)을 import 한다(register 는 import 시점 1회)."""
    import importlib
    import re
    # 코어 클라 UI + 플러그인 모듈 import → register 누적.
    for mod in ("pytmuxlib.client", "pytmuxlib.clientscreens",
                "pytmuxlib.clientwidgets"):
        try:
            importlib.import_module(mod)
        except Exception:
            pass
    for p in ("claude-code", "ncd", "calendar", "clock", "rec",
              "ime-indicator", "claude-resume", "claude-prompt-history",
              "claude-token-usage-view", "p4-show-submitted-changelists"):
        for sub in ("", "clientstatus", "screens", "render", "__init__",
                    "clientside", "overlay", "screen"):
            name = f"pytmuxlib.plugins.{p}" + (f".{sub}" if sub else "")
            try:
                importlib.import_module(name)
            except Exception:
                pass
    hangul = re.compile(r"[가-힣]")
    # 언어 autonym(네이티브 표기 유지)만 허용.
    allow = {"lang.name.ko", "한국어"}
    leaks = {k: v for k, v in i18n._CATALOG.get("en", {}).items()
             if k not in allow and isinstance(v, str) and hangul.search(v)}
    assert not leaks, f"en 카탈로그 한글 누출: {leaks}"
    _reset()


async def test_every_ko_key_has_en_entry():
    """모든 ko 카탈로그 키는 en 엔트리를 가져야 한다(LLM 10a 게이트). en 로케일에서 en
    엔트리가 없으면 t() 가 _FALLBACK(ko) 로 떨어져 **영어 UI 에 한국어가 새기 때문**
    (i18n.t 폴백 참조). test_en_catalog_has_no_hangul_leak 은 en 에 **있는** 값만 검사하므로
    ko-only 키(en 미등록)를 놓친다 — 이 테스트가 그 구멍을 막는다."""
    import importlib
    for mod in ("pytmuxlib.client", "pytmuxlib.clientscreens",
                "pytmuxlib.clientwidgets"):
        try:
            importlib.import_module(mod)
        except Exception:
            pass
    for p in ("claude-code", "ncd", "calendar", "clock", "rec",
              "ime-indicator", "claude-resume", "claude-prompt-history",
              "claude-token-usage-view", "p4-show-submitted-changelists"):
        for sub in ("", "clientstatus", "screens", "render", "__init__",
                    "clientside", "overlay", "screen"):
            name = f"pytmuxlib.plugins.{p}" + (f".{sub}" if sub else "")
            try:
                importlib.import_module(name)
            except Exception:
                pass
    ko_keys = set(i18n._CATALOG.get("ko", {}))
    en_keys = set(i18n._CATALOG.get("en", {}))
    missing = sorted(ko_keys - en_keys)
    assert not missing, f"en 카탈로그 미등록 키(ko-only → 영어 UI 한글 폴백): {missing}"
    _reset()


async def test_token_viewer_compose_labels_use_i18n():
    """1-4: 토큰 뷰어 탭바 compose 라벨이 raw 한글 Label 이 아니라 i18n.t 를 거쳐야
    한다(en 사용자 첫 페인트 한글 노출 방지 — _sync_tabs resize 갱신은 이미 i18n.t).
    소스 정적 검사로 회귀 가드."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent /
           "pytmuxlib" / "plugins" / "claude-code" / "screens.py"
           ).read_text(encoding="utf-8")
    bad = re.findall(r'Label\("([가-힣][^"]*)"', src)
    assert not bad, f"i18n.t 미경유 한글 탭 라벨(compose): {bad}"


async def test_mouse_gesture_catalog_matches_deployed_behavior():
    """마우스 제스처 안내가 **지금 동작**과 같아야 한다 — 평드래그=선택→복사,
    Shift+드래그=내부 앱에 전달(p4 65423 에서 뒤바뀜).

    회귀 이력: 그때 clientcmd 의 `t(key, default=…)` **default 만** 고치고 카탈로그를
    안 고쳐, t() 가 등록된 번역을 우선하는 바람에 ko·en 사용자 모두에게 옛 안내
    ("Shift+드래그 — 텍스트 선택")가 계속 보였다(2026-07-28 사이트 감사에서 발견).
    default 는 카탈로그에 키가 없을 때만 쓰이므로 **default 를 오라클로 삼으면 안 된다**
    — 카탈로그 값 자체를 단언한다.

    또한 clientcmd 가 나열하는 제스처 키가 전부 두 로케일에 등록돼 있는지 본다(빠지면
    en UI 에 한글이 새거나 목록 한 줄이 통째로 키 문자열로 뜬다)."""
    import re
    from pathlib import Path
    _reset()
    src = (Path(__file__).resolve().parent.parent /
           "pytmuxlib" / "clientcmd.py").read_text(encoding="utf-8")
    listed = re.findall(r'\("(keys\.g_[a-z]+)",', src)
    assert "keys.g_shift" in listed and "keys.g_drag" in listed, listed
    for loc in ("ko", "en"):
        cat = i18n._CATALOG.get(loc, {})
        for k in listed:
            assert k in cat, f"{loc} 카탈로그에 제스처 키 미등록: {k}"
        shift, drag = cat["keys.g_shift"], cat["keys.g_drag"]
        # Shift = 앱 전달(옛 '텍스트 선택' 이면 실패), 평드래그 = 복사
        assert ("전달" in shift or "forward" in shift.lower()), (loc, shift)
        assert "선택" not in shift and "select" not in shift.lower(), (loc, shift)
        assert ("복사" in drag or "copy" in drag.lower()), (loc, drag)
    _reset()


async def test_a_notice_carries_the_ingredients_for_a_client_that_keys_on_korean():
    """서버 알림도 **재료**(`fmt`+`args`)를 싣는다 — 로케일 ⓑ 의 알림 갈래.

    # 왜 `key`+`kw` 로는 부족했나

    이 경로는 예전부터 `key`(`ccmsg.resume_injected` 같은 도메인 키)와 `kw` 를 실어
    보내 왔고, **정본 클라**는 그것으로 자기 로케일을 짓는다. 그런데 네이티브 클라의
    카탈로그는 **한국어 원문이 곧 키**라 그 도메인 키로는 아무것도 못 찾는다 — 그래서
    자리가 있는 알림(`자동재개: '{msg}' 주입(패널 {pane})`)이 영어 사용자에게 통째로
    한국어로 떴다. 같은 재료를 그 클라가 읽는 모양으로도 싣는다.

    칸을 **더하기만** 하므로 이것을 모르는 클라는 종전과 한 글자도 다르지 않다.
    """
    from pytmuxlib.serverremote import ServerRemoteMixin

    msg = ServerRemoteMixin._notice_msg(
        "ccmsg.resume_injected", "자동재개: '{msg}' 주입(패널 {pane})",
        severity="info", pane=3, msg="continue")
    # 종전 칸은 그대로다(구 클라 호환).
    assert msg["text"] == "자동재개: 'continue' 주입(패널 3)", msg
    assert msg["key"] == "ccmsg.resume_injected" and msg["kw"]["pane"] == 3
    # 새 칸: 원문 포맷과 값. 값은 문자열로 — 클라의 `tf` 가 소박한 치환이다.
    assert msg["i18n"] == {"text": {"fmt": "자동재개: '{msg}' 주입(패널 {pane})",
                                    "args": {"pane": "3", "msg": "continue"}}}, msg


async def test_a_notice_without_places_needs_no_ingredients():
    """자리가 없는 알림은 **원문이 곧 키**라 재료가 필요 없다(로케일 ⓐ 로 풀린다).

    빈 `i18n` 칸을 굳이 실으면 프레임만 커지고, "재료가 왔다"는 신호가 무의미해진다.
    """
    from pytmuxlib.serverremote import ServerRemoteMixin

    msg = ServerRemoteMixin._notice_msg("rnotice.attach_silent", "조용히 붙었습니다")
    assert "i18n" not in msg, msg


async def test_a_notice_with_a_reason_fragment_does_not_mix_languages():
    """실패 사유(`detail`)가 붙은 알림은 재료를 **안** 싣는다 — 안 그러면 언어가 섞인다.

    그 경로의 `kw["why"]` 는 사유의 **한국어 조각**이다(정본 클라는 `detail` 로 덮어
    자기 로케일로 짓는다). 그것을 인자로 넘기면 영어 포맷 안에 한국어가 박혀
    `remote-attach host failed — 연결 거부됨` 같은 것이 된다. 섞느니 통째로 한국어가
    낫다 — `i18n.phrase` 머리말이 경고하는 그 함정이다.
    """
    from pytmuxlib.serverremote import ServerRemoteMixin

    msg = ServerRemoteMixin._notice_msg(
        "rnotice.attach_fail", "remote-attach {target} 실패 — {why}",
        detail={"text": "연결 거부됨"}, target="office1")
    assert "i18n" not in msg, msg
    # 종전 칸은 그대로다 — 정본 클라는 `detail` 로 자기 로케일 조각을 끼운다.
    assert msg["detail"] == {"text": "연결 거부됨"} and msg["kw"]["why"] == "연결 거부됨"


async def test_every_word_the_server_speaks_is_registered_where_the_server_reads():
    """★ **전수 게이트** — 서버가 짓는 글은 서버가 읽는 카탈로그에 있어야 한다(pytmux-34).

    이 저장소의 카탈로그는 여러 모듈이 `i18n.register` 로 나눠 든다. 그 중 몇은
    **Textual 을 무는 파일**(정본 클라 화면)이라 **서버 프로세스가 안 읽는다** — 화면은
    실제로 열 때 지연 import 하는 것이 규약이기 때문이다. 그런데 화면 **스펙**(GUI 로
    내려가는 선언형 판)을 짓는 것은 서버다. 그래서 서버가 쓰는 키를 클라 전용 파일에만
    적으면 `t()` 가 **키를 그대로** 돌려주고, 그 값이 어디로 흘러가느냐로 둘이 갈린다:

    - 값을 **쪼개 쓰면 터진다** — 실측(pytmux-419): `"pscreen.weekdays".split(",")` 가
      원소 하나라 `weekdays[wd]` 가 월요일 말고는 전부 `IndexError` 였고, GUI 의 기간
      탭이 자료가 있는 홈에서 **아예 안 떴다**. 정본 팝업은 그 파일을 이미 물고 있어
      멀쩡했다 — 그래서 GUI 에서만 나는 갈림이 오래 안 잡혔다.
    - 값을 **그냥 그리면 조용하다** — 키 문자열이 화면에 그대로 뜨거나(`pscreen.*`),
      서버가 실은 한국어 `text` 로 떨어져 **영어 사용자에게 한국어로** 뜬다(`msg.*`).

    ⛔ 사람이 지키는 규칙이 아니라 **여기서 센다**. 규칙 자체는 이미 코드 주석에 있었다
    (`plugins/claude-code/screens.py` 가 `pscreen.perm_title` 에 대해 적어 둔 그것) —
    적혀 있는데도 다섯이 새로 새어 나갔다.

    ⚠ **자식 프로세스에서** 잰다: 이 스위트는 전 모듈이 한 프로세스라, 앞서 도는 시험이
    클라 화면 모듈을 한 번이라도 import 하면 카탈로그가 채워져 **가짜 초록**이 된다.
    """
    import subprocess
    import sys
    import textwrap

    probe = textwrap.dedent('''
        import io, importlib, os, re, sys
        sys.path.insert(0, os.getcwd())
        # 서버가 무는 파일 = Textual 을 import 안 하는 파일.
        server_files = []
        for dp, _dn, fns in os.walk("pytmuxlib"):
            for fn in fns:
                if not fn.endswith(".py"):
                    continue
                fp = os.path.join(dp, fn)
                src = io.open(fp, encoding="utf-8", errors="replace").read()
                if not re.search(r"^\\s*(from|import)\\s+textual", src, re.M):
                    server_files.append((fp, src))
        # 네임스페이스 키만 본다(한국어 원문을 키로 쓰는 관례가 따로 있다 —
        # 그쪽은 못 찾아도 원문이 그대로 뜨므로 이 게이트의 대상이 아니다).
        NS = re.compile(
            r"[\\"\\']((?:pscreen|msg|ui|cmd|opt|nc|mdir|claude|tok|status|word|setting)"
            r"\\.[a-z0-9_.]+)[\\"\\']")
        used = {}
        for fp, src in server_files:
            for k in NS.findall(src):
                used.setdefault(k, set()).add(fp)
        i18n = importlib.import_module("pytmuxlib.i18n")
        # 서버가 무는 만큼만 싣는다 — 플러그인 레지스트리가 하는 그대로.
        for name in sorted(os.listdir(os.path.join("pytmuxlib", "plugins"))):
            d = os.path.join("pytmuxlib", "plugins", name)
            if os.path.isdir(d) and os.path.exists(os.path.join(d, "__init__.py")):
                importlib.import_module("pytmuxlib.plugins." + name)
        leaked = [m for m in sys.modules if m.startswith("textual")]
        assert not leaked, "탐침이 Textual 을 물어 버렸다: %r" % leaked[:3]
        missing = sorted(k for k in used if i18n.t(k) == k)
        print(repr((len(used), missing,
                    {k: sorted(used[k]) for k in missing})))
    ''')
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                         text=True, cwd=os.getcwd(), timeout=180)
    assert out.returncode == 0, f"탐침이 죽었다:\n{out.stderr}"
    total, missing, where = eval(out.stdout.strip())
    assert total >= 100, f"키를 {total}개밖에 못 찾았다 — 정규식이 낡았다"
    # `cmd.exe` 는 Windows 셸 이름이라 키가 아니다(pty 계열 넷이 문자열로 든다).
    missing = [k for k in missing if k != "cmd.exe"]
    assert not missing, (
        f"서버가 쓰는 키 {len(missing)}개가 서버 카탈로그에 없다: "
        f"{ {k: where[k] for k in missing} }. Textual 을 무는 파일의 "
        f"`i18n.register` 에만 적으면 서버는 못 읽는다 — 서버도 읽는 모듈"
        f"(플러그인 `__init__.py` · `pytmuxlib/i18n.py`)로 옮길 것(pytmux-34)")


async def test_a_missing_weekday_catalog_does_not_kill_the_token_popup():
    """☠ **번역 하나가 화면 하나를 죽이면 안 된다**(pytmux-429).

    이 카탈로그에서 `pscreen.weekdays` 만 특별하다 — 유일하게 **쪼개 쓰는 값**이다.
    없으면 `i18n.t` 가 키를 그대로 돌려주고, `"pscreen.weekdays".split(",")` 는 원소가
    **하나**라 `weekdays[wd]` 가 월요일 말고는 전부 `IndexError` 다. 그리고
    `usagelog._bucket_short` 의 `try` 는 `ValueError` 만 잡아 그것을 통과시킨다.

    실측(office 맥 · 2026-09-01): 정본 토큰 팝업이 `on_mount` 에서 통째로 넘어졌다
    — 트레이스백의 지역 변수가 `weekdays = ['pscreen.weekdays']` 였다. 카탈로그가
    비는 길은 여럿이지만(한 프로세스가 `p4 sync` 를 사이에 두고 두 시대의 파일을 드는
    것이 그 중 하나) **길이 무엇이든 이 결말은 결함이다.**

    그래서 쪼개는 자리를 하나로 모으고 그 하나가 일곱을 보장한다. 여기서는 카탈로그를
    실제로 비우고 **트리가 서는지**를 잰다(값을 만드는 함수만 재면 부르는 자리를 지워도
    통과한다 — 그 공허 통과를 피하려 `usagetree.build` 까지 부른다).
    """
    import importlib

    _reset()
    from pytmuxlib import plugins
    plugins.load()
    pkg = importlib.import_module("pytmuxlib.plugins.claude-code")
    usagetree = importlib.import_module("pytmuxlib.plugins.claude-code.usagetree")

    saved = {loc: {k: i18n._CATALOG[loc].get(k)
                   for k in ("pscreen.weekdays", "pscreen.hour_suffix")}
             for loc in ("ko", "en")}
    try:
        # ⑴ 카탈로그가 온전할 때는 카탈로그가 이긴다(폴백이 번역을 덮으면 안 된다).
        assert pkg.weekday_names()[2] == "수"
        i18n.set_locale("en")
        assert pkg.weekday_names()[2] == "We" and pkg.hour_suffix() == "h"
        i18n.set_locale("ko")

        # ⑵ 그 다섯이 없는 프로세스를 흉내낸다.
        for loc in ("ko", "en"):
            for k in ("pscreen.weekdays", "pscreen.hour_suffix"):
                i18n._CATALOG[loc].pop(k, None)
        names = pkg.weekday_names()
        assert len(names) == 7 and names[0] and "pscreen" not in names[0], names
        assert "pscreen" not in pkg.hour_suffix()

        # ⑶ 그리고 팝업이 부르는 그 경로가 **실제로 선다** — 수요일(2026-06-03)이
        #    들어 있어야 옛 결함의 `wd=2` 를 그대로 밟는다.
        recs = [{"ts": 1780455600.0, "tab": None, "pane": 0, "session": None,
                 "account": "unknown", "tokens": 100}]
        nodes, total = usagetree.build(recs, recs, None, ())
        assert total == 100 and nodes
        day = usagelog_day_label(recs)
        assert "(수)" in day, day
    finally:
        for loc, items in saved.items():
            for k, v in items.items():
                if v is not None:
                    i18n._CATALOG[loc][k] = v


def usagelog_day_label(recs):
    """일 버킷 라벨 하나 — 요일이 실제로 붙는지 보려고 집계를 직접 부른다."""
    import importlib
    pkg = importlib.import_module("pytmuxlib.plugins.claude-code")
    usagelog = importlib.import_module("pytmuxlib.plugins.claude-code.usagelog")
    idx = usagelog.agg_index(recs, "day", weekdays=pkg.weekday_names(),
                             hour_suffix=pkg.hour_suffix())
    return next(iter(idx.values()))["label"]


async def test_the_weekday_names_are_split_in_exactly_one_place():
    """⛔ **사본이 다시 생기면 여기서 운다**(pytmux-429).

    이 결함은 「부르는 자리가 넷」이라 살아남았다 — 넷 중 하나
    (`screens._day_header`)만 혼자 `IndexError` 를 막고 있었고, 나머지 셋은 안 막았다.
    한 자리만 고치면 다음 사람이 넷째를 다시 적는다. 그래서 **쪼개는 것은 저장소에
    한 번뿐**이고, 그 한 번은 일곱을 보장하는 `weekday_names()` 안에 있다.
    """
    import io
    import os
    import re

    hits = []
    for dp, _dn, fns in os.walk("pytmuxlib"):
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            fp = os.path.join(dp, fn)
            src = io.open(fp, encoding="utf-8", errors="replace").read()
            for m in re.finditer(
                    r'i18n\.t\(\s*["\']pscreen\.weekdays["\'][^)]*\)\s*\.split',
                    src):
                hits.append((fp, src[:m.start()].count("\n") + 1))
    assert len(hits) == 1, (
        f"`pscreen.weekdays` 를 쪼개는 자리가 {len(hits)}곳이다: {hits}. "
        f"쪼개는 것은 `plugins/claude-code/__init__.py` 의 `weekday_names()` "
        f"하나뿐이어야 한다 — 그 함수만 일곱을 보장한다(pytmux-429)")
    assert hits[0][0].replace("\\", "/").endswith(
        "pytmuxlib/plugins/claude-code/__init__.py"), hits
