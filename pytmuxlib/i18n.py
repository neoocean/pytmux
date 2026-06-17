"""경량 다국어화(i18n) 카탈로그 — 한국어(ko)·영어(en) (docs/IMPROVEMENT §6).

설계 의도(왜 gettext 가 아닌가):
  사용자 표면 문자열은 수백 규모라 gettext 의 .po/.mo 빌드 파이프라인은 과하다.
  키→로케일별 번역 dict + `str.format` 으로 충분하고, 의존성 0 에 디버깅이 쉽다.

핵심 규약:
  * **원본 언어는 한국어(ko).** 코드에 박혀 있던 리터럴이 곧 ko 값이다. 따라서 en
    번역이 빠진 키는 ko 로 폴백한다 — 단계적 롤아웃(§6 ①~⑤) 중 일부만 번역돼도
    영어 사용자는 "번역된 곳은 영어 + 나머지는 한국어" 로 우아하게 degrade 한다.
  * **로케일은 클라이언트-로컬**이다. 화면 문자열은 클라가 렌더하고, 로케일은
    per-user(같은 서버에 ko/en 사용자가 동시에 attach 가능)라서 서버 전역 옵션으로
    두지 않는다. 그래서 프로토콜·서버 opts 변경이 전혀 없다(서버 리팩터와 비충돌).
  * 번역 대상은 **사용자가 보는 표면만**. 코드 주석·로그(error.log)·내부 키는 비번역.

로케일 우선순위(resolve): 런타임 `lang` 명령(클라 영속) > `pytmux.conf` `lang` > 환경
`LANG`/`LC_ALL`(`ko*`→ko, 그 외→en). 명령 영속은 load_persisted/save_persisted 가 담당.

플러그인은 자기 카탈로그를 `register()` 로 병합한다(코어가 모르는 문자열도 같은 `t()`
경로로 번역 — delete-to-disable 일관). 키 충돌 시 나중 등록이 이긴다(플러그인 우선).
"""
from __future__ import annotations

import os
from typing import Dict, Optional

from . import ipc

# 지원 로케일. 첫 항목이 원본/폴백 언어(ko).
LOCALES = ("ko", "en")
_FALLBACK = "ko"

# locale -> {key: text}. 코어 시드 + 플러그인 register() 가 채운다.
_CATALOG: Dict[str, Dict[str, str]] = {loc: {} for loc in LOCALES}

# 현재 활성 로케일(클라 프로세스 1개당 1개라 모듈 전역으로 충분).
_locale = _FALLBACK


def register(catalog: Dict[str, Dict[str, str]]) -> None:
    """카탈로그를 병합한다. catalog = {"ko": {key: text, ...}, "en": {...}}.

    코어 시드와 플러그인이 같은 함수를 쓴다. 같은 키를 다시 등록하면 덮어쓴다
    (플러그인이 코어 문자열을 자기 맥락에 맞게 바꿀 수 있게 — 의도된 동작)."""
    for loc, items in catalog.items():
        if loc not in _CATALOG:
            _CATALOG[loc] = {}
        _CATALOG[loc].update(items)


def set_locale(loc: str) -> str:
    """활성 로케일을 바꾼다. 미지원 값이면 폴백(ko)으로. 적용된 로케일을 반환."""
    global _locale
    _locale = loc if loc in _CATALOG and loc in LOCALES else _FALLBACK
    return _locale


def get_locale() -> str:
    return _locale


def available() -> tuple:
    return LOCALES


def t(key: str, default: Optional[str] = None, **kw) -> str:
    """키를 현재 로케일 문자열로. 없으면 ko 폴백, 그래도 없으면 default(또는 키 자체).

    default 는 카탈로그에 키가 전혀 없을 때 쓸 대체 문자열(예: 플러그인이 아직 카탈로그를
    등록 안 한 명령 설명은 원본 한국어를 그대로 보이게). 미지정이면 키 자체(개발 중 가시).
    kw 가 있으면 `str.format(**kw)` 로 치환한다(예: t("foo", n=3) → "...{n}..." 포맷).
    포맷 실패(키 불일치 등)는 원문을 그대로 돌려 렌더가 죽지 않게 한다."""
    s = _CATALOG.get(_locale, {}).get(key)
    if s is None:
        s = _CATALOG.get(_FALLBACK, {}).get(key)
    if s is None:
        s = default if default is not None else key
    if kw:
        try:
            return s.format(**kw)
        except (KeyError, IndexError, ValueError):
            return s
    return s


def resolve(config_lang: Optional[str], env: Optional[Dict[str, str]] = None) -> str:
    """명령 영속을 제외한 초기 로케일을 정한다: config `lang` > 환경 `LANG`/`LC_ALL`.

    config_lang 이 지원 로케일이면 그것을, 아니면 환경 변수가 `ko` 로 시작하면 ko,
    그 외(미설정 포함 — 영어권 기본)는 en. C/POSIX 로케일도 en 으로 떨어진다."""
    if config_lang and config_lang.lower() in LOCALES:
        return config_lang.lower()
    env = env if env is not None else os.environ
    raw = (env.get("LC_ALL") or env.get("LANG") or "").lower()
    return "ko" if raw.startswith("ko") else "en"


# ─────────────────────────────────────────────────────────────────────────────
# 런타임 `lang` 명령 선택의 클라이언트-로컬 영속(opts.json 과 별개의 작은 파일).
# 서버 opts 가 아니라 클라 측에 두는 이유는 로케일이 per-user 이기 때문(위 모듈 docstring).
# 경로는 상태 디렉터리(엔드포인트별)라 같은 서버에 붙는 클라끼리 공유되지만, 값은 마지막
# `lang` 선택이라 단일 사용자 기준으로 충분하다(per-client 분리는 후속 여지).
# ─────────────────────────────────────────────────────────────────────────────
def _lang_file(sock_path: str) -> str:
    return ipc.state_base(sock_path) + ".lang"


def load_persisted(sock_path: str) -> Optional[str]:
    """영속된 로케일 선택을 읽는다. 없거나 미지원이면 None(→ resolve 로 결정)."""
    try:
        with open(_lang_file(sock_path), "r", encoding="ascii") as f:
            v = f.read().strip()
    except OSError:
        return None
    return v if v in LOCALES else None


def save_persisted(sock_path: str, loc: str) -> None:
    """로케일 선택을 영속(best-effort). 실패는 조용히 무시(런타임엔 이미 적용됨)."""
    try:
        with open(_lang_file(sock_path), "w", encoding="ascii") as f:
            f.write(loc)
    except OSError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# 코어 시드 카탈로그(§6 ①). 단계 ②~⑤ 에서 표면 문자열을 이리로 옮겨 키를 늘린다.
# 지금은 프레임워크 검증용 최소 셋 + `lang` 명령 자체의 피드백만 담는다.
# 키 규약: "<도메인>.<이름>" (예: capture.status_on). 포맷 인자는 {name} 형식.
# ─────────────────────────────────────────────────────────────────────────────
register({
    "ko": {
        "lang.changed": "언어: {name}",
        "lang.usage": "사용법: lang ko|en",
        "lang.name.ko": "한국어",
        "lang.name.en": "English",
        "capture.status_on": "상태: ON (캡처 중)",
        "capture.status_off": "상태: OFF",
        "ui.search": "검색…",
    },
    "en": {
        "lang.changed": "Language: {name}",
        "lang.usage": "usage: lang ko|en",
        "lang.name.ko": "한국어",
        "lang.name.en": "English",
        "capture.status_on": "Status: ON (capturing)",
        "capture.status_off": "Status: OFF",
        "ui.search": "Search…",
    },
})

# §6 ⑤ 사용량 트리(core clientscreens.usage_bar_lines) — Claude /usage 한도 표시.
# Claude 도메인이지만 core 코드라 core 시드에 둔다(라벨 폭은 _char_cells 로 로케일 적응).
register({
    "ko": {
        "usage.session_5h": "세션 5h",
        "usage.week_all": "주 전체",
        "usage.week_sonnet": "주 Sonnet",
        "usage.used": "사용",
        "usage.account": "계정(/usage): {acct}",
        "usage.account_unknown": "계정(/usage): 미확인 (폰 앱과 같은 계정인지 확인)",
        "usage.ago_hm": "{h}시간 {m}분",
        "usage.ago_m": "{m}분",
        "usage.measured_ago": "({ago} 전 실측 — 갱신은 [u]/claude-usage)",
    },
    "en": {
        "usage.session_5h": "Session 5h",
        "usage.week_all": "Week all",
        "usage.week_sonnet": "Week Sonnet",
        "usage.used": "used",
        "usage.account": "Account (/usage): {acct}",
        "usage.account_unknown": "Account (/usage): unknown (verify it matches your phone app)",
        "usage.ago_hm": "{h}h {m}m",
        "usage.ago_m": "{m}m",
        "usage.measured_ago": "(measured {ago} ago — refresh: [u]/claude-usage)",
    },
})

# §6 ⑤+ 다이얼로그 본문 — client.py confirm/InfoScreen 이 넘기는 메시지·제목.
register({
    "ko": {
        "dialog.kill_pytmux_msg": "이 탭을 닫으면 pytmux 가 종료됩니다(모든 셸 종료). 닫을까요?",
        "dialog.kill_pytmux_title": "pytmux 종료",
        "dialog.kill_tab_msg": "이 탭을 닫을까요? 탭의 셸이 종료됩니다.",
        "dialog.kill_tab_title": "탭 닫기",
        "dialog.kill_server_msg": "서버와 모든 탭·셸을 종료합니다. 이 pytmux 세션이 끝납니다. 계속할까요?",
        "dialog.kill_server_title": "서버 종료",
        "dialog.kill_server_yes": "종료",
        "dialog.restart_confirm_title": "재시작 확인",
        "dialog.restart_yes": "재시작",
        "dialog.status_title": "상태",
        "plugins.title": "플러그인 관리",
        "plugins.hint": "Space/Enter 토글 · Esc 닫기 · 서버 기여 플러그인은 재시작에 완전 반영",
    },
    "en": {
        "dialog.kill_pytmux_msg": "Closing this tab will quit pytmux (all shells end). Close?",
        "dialog.kill_pytmux_title": "Quit pytmux",
        "dialog.kill_tab_msg": "Close this tab? The tab's shells will end.",
        "dialog.kill_tab_title": "Close tab",
        "dialog.kill_server_msg": "This kills the server and all tabs/shells. This pytmux session will end. Continue?",
        "dialog.kill_server_title": "Kill server",
        "dialog.kill_server_yes": "Kill",
        "dialog.restart_confirm_title": "Restart confirmation",
        "dialog.restart_yes": "Restart",
        "dialog.status_title": "Status",
        "plugins.title": "Plugin manager",
        "plugins.hint": "Space/Enter toggle · Esc close · server-contributed plugins fully apply on restart",
    },
})

# §6 ④ 모달 스크린 — 명령목록·옵션·정보·확인·버퍼/레이아웃 피커의 furniture(제목·
# 서브타이틀·빈 상태·네비 힌트·버튼). 다이얼로그 본문(메시지)은 호출부(client.py)가
# 넘기므로 별도 단계에서 다룬다.
register({
    "ko": {
        "screen.empty": "(없음)",
        "screen.close": "닫기",
        "screen.cancel": "취소",
        "screen.info": "정보",
        "screen.confirm": "확인",
        "screen.no_search_results": "(검색 결과 없음)",
        "screen.command_list": "명령 목록",
        "screen.cmdlist_sub": "타이핑 검색 · ←→/클릭 탭 · ↑↓ 명령 · Home/End 처음·끝 · Enter 선택 · Esc 닫기",
        "screen.options_title": "{cmd} 옵션 · ←→ 값 · Enter 실행 · Esc",
        "screen.infotabs_sub": "←→ 탭·닫기[x] · ↑↓ 항목 · Enter/Esc 닫기",
        "screen.confirm_sub": "←→ 이동 · Enter 확정 · y/n · Esc 취소",
        "screen.no_buffers": "(버퍼 없음)",
        "screen.layout_load": "레이아웃 불러오기",
        "screen.no_layouts": "(저장된 레이아웃 없음)",
        "screen.more_up": "  ↑ 더 …",
        "screen.more_down": "  ↓ 더 …",
        # 통합 설정 화면(:settings)
        "screen.settings_title": "설정",
        "screen.settings_sub": "↑↓ 이동 · ←→ 값 변경 · Tab/클릭 카테고리 · Enter 입력/열기 · Esc 닫기",
        "setting.open": "열기",
        "setting.unset": "미설정",
        "setting.unknown": "미상(서버)",
        "setting.restart": "재시작 시 발효",
        # 설정 값 표시 라벨(저장값→사람친화 라벨; 미등록값은 원값 그대로)
        "setval.on": "켜짐",
        "setval.off": "꺼짐",
        "setval.always": "항상",
        "setval.auto": "자동",
        "setval.top": "위",
        "setval.bottom": "아래",
        "setval.ko": "한국어",
        "setval.en": "English",
        # 설정 카테고리
        "setcat.표시": "표시",
        "setcat.입력": "입력/키",
        "setcat.동작": "동작",
        "setcat.상태줄": "상태줄",
        "setcat.Claude": "Claude",
        "setcat.고급": "고급/플러그인",
        # 설정 라벨(setting.<key>)
        "setting.inactive-dim": "비활성 패널 흐리게",
        "setting.inactive-dim-ratio": "흐리게 세기",
        "setting.tab-bar": "탭 바 표시",
        "setting.status-position": "상태줄 위치",
        "setting.single-border": "단일 패널 테두리",
        "setting.pane-border-status": "패널 헤더 표시",
        "setting.language": "언어",
        "setting.mouse": "마우스",
        "setting.mode-keys": "복사 모드 키",
        "setting.alt-scroll": "휠 스크롤백(1007)",
        "setting.prefix": "prefix 키",
        "setting.default-path": "새 패널 시작 경로",
        "setting.set-titles": "터미널 제목 설정",
        "setting.status-interval": "상태줄 갱신 주기(초)",
        "setting.automatic-rename": "탭 자동 이름",
        "setting.monitor-activity": "활동 모니터",
        "setting.monitor-bell": "벨 모니터",
        "setting.synchronize-panes": "입력 동기화",
        "setting.coalesce-repaints": "리페인트 합치기",
        "setting.nest-auto-attach": "중첩 자동 승격",
        "setting.vt-parser": "VT 파서 백엔드",
        "setting.status-left": "상태줄 왼쪽 포맷",
        "setting.status-right": "상태줄 오른쪽 포맷",
        "setting.status-bg": "상태줄 배경색",
        "setting.status-fg": "상태줄 글자색",
        "setting.token-saver": "Claude 토큰 세이버…",
        "setting.model": "Claude 모델/컨텍스트…",
        "setting.claude-rules": "Claude 시작 규칙…",
        "setting.token-log": "토큰 사용량…",
        "setting.plugins": "플러그인 관리…",
        "setting.list-keys": "키 바인딩 목록…",
    },
    "en": {
        "screen.empty": "(none)",
        "screen.close": "Close",
        "screen.cancel": "Cancel",
        "screen.info": "Info",
        "screen.confirm": "Confirm",
        "screen.no_search_results": "(no results)",
        "screen.command_list": "Commands",
        "screen.cmdlist_sub": "Type to search · ←→/click tabs · ↑↓ commands · Home/End first·last · Enter select · Esc close",
        "screen.options_title": "{cmd} options · ←→ value · Enter run · Esc",
        "screen.infotabs_sub": "←→ tabs·close[x] · ↑↓ items · Enter/Esc close",
        "screen.confirm_sub": "←→ move · Enter confirm · y/n · Esc cancel",
        "screen.no_buffers": "(no buffers)",
        "screen.layout_load": "Load layout",
        "screen.no_layouts": "(no saved layouts)",
        "screen.more_up": "  ↑ more …",
        "screen.more_down": "  ↓ more …",
        # Unified settings screen (:settings)
        "screen.settings_title": "Settings",
        "screen.settings_sub": "↑↓ move · ←→ change · Tab/click category · Enter edit/open · Esc close",
        "setting.open": "open",
        "setting.unset": "unset",
        "setting.unknown": "unknown (server)",
        "setting.restart": "takes effect on restart",
        "setval.on": "on",
        "setval.off": "off",
        "setval.always": "always",
        "setval.auto": "auto",
        "setval.top": "top",
        "setval.bottom": "bottom",
        "setval.ko": "Korean",
        "setval.en": "English",
        "setcat.표시": "Display",
        "setcat.입력": "Input/Keys",
        "setcat.동작": "Behavior",
        "setcat.상태줄": "Status bar",
        "setcat.Claude": "Claude",
        "setcat.고급": "Advanced/Plugins",
        "setting.inactive-dim": "Dim inactive panes",
        "setting.inactive-dim-ratio": "Dim strength",
        "setting.tab-bar": "Tab bar",
        "setting.status-position": "Status bar position",
        "setting.single-border": "Single-pane border",
        "setting.pane-border-status": "Pane header",
        "setting.language": "Language",
        "setting.mouse": "Mouse",
        "setting.mode-keys": "Copy-mode keys",
        "setting.alt-scroll": "Wheel scrollback (1007)",
        "setting.prefix": "Prefix key",
        "setting.default-path": "New-pane start path",
        "setting.set-titles": "Set terminal title",
        "setting.status-interval": "Status update interval (s)",
        "setting.automatic-rename": "Tab auto-rename",
        "setting.monitor-activity": "Activity monitor",
        "setting.monitor-bell": "Bell monitor",
        "setting.synchronize-panes": "Synchronize input",
        "setting.coalesce-repaints": "Coalesce repaints",
        "setting.nest-auto-attach": "Nested auto-attach",
        "setting.vt-parser": "VT parser backend",
        "setting.status-left": "Status-left format",
        "setting.status-right": "Status-right format",
        "setting.status-bg": "Status background",
        "setting.status-fg": "Status foreground",
        "setting.token-saver": "Claude token saver…",
        "setting.model": "Claude model/context…",
        "setting.claude-rules": "Claude start rules…",
        "setting.token-log": "Token usage…",
        "setting.plugins": "Manage plugins…",
        "setting.list-keys": "Key bindings…",
    },
})

# §6 ② 상태줄·경고·헤더 — 클라 transient 알림(display_message)/종료 메시지/캡처 정보.
register({
    "ko": {
        # 연결/재접속/재시작 상태 알림
        "msg.connect_failed": "pytmux: 서버에 연결할 수 없습니다",
        "msg.reconnect_failed": "pytmux: 서버 재접속 실패",
        "msg.restart_done": "pytmux: 서버 재시작 완료 — 재접속됨",
        "msg.reconnect_failed_net": "pytmux: 재접속 실패 — 네트워크 확인",
        "msg.reconnected_resync": "pytmux: 재접속됨 — 화면 재동기",
        "msg.server_restarting": "pytmux: 서버 재시작 중…",
        "msg.server_terminated": "pytmux: 서버가 종료되었습니다",
        "msg.restart_dryrun": "pytmux: 재시작 전 드라이런 점검 중…",
        "msg.server_restart": "pytmux: 서버 재시작…",
        "msg.config_warn_more": " 외 {n}건",
        # 클립보드/복사
        "msg.paste_in_progress": "클립보드 붙여넣기 중… 잠시만요 (ESC 로 빠져나가기)",
        "msg.paste_image_path": "클립보드 이미지 → 경로 붙여넣기: {path}",
        "msg.paste_image_app": "이미지 붙여넣기 → 내부 앱(Alt+V)",
        "msg.clipboard_empty": "클립보드가 비어있거나 읽을 수 없음",
        "msg.copied_chars": "{n} chars 복사됨",
        "msg.clipboard_suffix": " (클립보드)",
        # 캡처(REC)
        "msg.captured_chars": "{n} chars 버퍼에 캡처됨",
        "msg.capture_toggle": "출력 캡처 {state} (상태줄 REC)",
        "msg.inactive_dim": "비활성 패널 흐리게 {state}",
        "msg.inactive_dim_ratio": "비활성 패널 흐리게 세기 {ratio}",
        "msg.inactive_dim_ratio_bad": "세기는 0~0.8 사이 숫자여야 합니다",
        "msg.setting_save_failed": "설정 파일 저장 실패: {err}",
        "msg.open_capture_dir": "기록 폴더 열기",
        "msg.no_capture_dir": "열 기록 폴더가 없습니다(캡처 꺼짐)",
        # 진단/기타
        "msg.mouse_log": "마우스 진단 로그: {path}",
        "msg.bad_tab_index": "탭 번호 범위 초과: {v}",
        "word.toggle": "토글",
    },
    "en": {
        "msg.connect_failed": "pytmux: cannot connect to server",
        "msg.reconnect_failed": "pytmux: server reconnect failed",
        "msg.restart_done": "pytmux: server restarted — reconnected",
        "msg.reconnect_failed_net": "pytmux: reconnect failed — check network",
        "msg.reconnected_resync": "pytmux: reconnected — resyncing screen",
        "msg.server_restarting": "pytmux: server restarting…",
        "msg.server_terminated": "pytmux: server has terminated",
        "msg.restart_dryrun": "pytmux: pre-restart dry-run check…",
        "msg.server_restart": "pytmux: restarting server…",
        "msg.config_warn_more": " +{n} more",
        "msg.paste_in_progress": "Pasting clipboard… please wait (ESC to abort)",
        "msg.paste_image_path": "Clipboard image → pasted path: {path}",
        "msg.paste_image_app": "Image paste → inner app (Alt+V)",
        "msg.clipboard_empty": "Clipboard is empty or unreadable",
        "msg.copied_chars": "{n} chars copied",
        "msg.clipboard_suffix": " (clipboard)",
        "msg.captured_chars": "{n} chars captured to buffer",
        "msg.capture_toggle": "Output capture {state} (status REC)",
        "msg.inactive_dim": "Inactive-pane dim {state}",
        "msg.inactive_dim_ratio": "Inactive-pane dim strength {ratio}",
        "msg.inactive_dim_ratio_bad": "Strength must be a number between 0 and 0.8",
        "msg.setting_save_failed": "Failed to save config file: {err}",
        "msg.open_capture_dir": "Opening capture folder",
        "msg.no_capture_dir": "No capture folder to open (capture off)",
        "msg.mouse_log": "Mouse debug log: {path}",
        "msg.bad_tab_index": "Tab number out of range: {v}",
        "word.toggle": "toggle",
    },
})

# §2.2 마우스 기능 발견성 — list-keys 팝업이 1급 마우스 제스처를 노출(구현됐으나
# ?목록·메뉴 어디에도 안 떠 사장됐던 것). 키 자체는 비번역(예: "Shift").
register({
    "ko": {
        "keys.title": "키 · 마우스",
        "keys.mouse_header": "마우스 제스처",
        "keys.g_click": "휠 — 스크롤백 스크롤 · 클릭 — 패널 포커스",
        "keys.g_rclick": "우클릭 — 패널 메뉴(분할·줌·회전·삭제…)",
        "keys.g_divider": "경계선 드래그 — 패널 크기 조절",
        "keys.g_header": "패널 헤더(위 테두리) 드래그 — 패널을 들어 다른 패널과 "
                         "swap · 탭으로 이동 · [+]에 놓아 새 탭",
        "keys.g_shift": "Shift+드래그 — 텍스트 선택(클립보드 복사)",
        "keys.g_tab": "탭 드래그 — 탭 재정렬 · 패널 위로 끌어 분할",
        "keys.user_header": "사용자 키 바인딩",
        "keys.none": "(없음)",
    },
    "en": {
        "keys.title": "Keys & Mouse",
        "keys.mouse_header": "Mouse gestures",
        "keys.g_click": "Wheel — scroll scrollback · Click — focus pane",
        "keys.g_rclick": "Right-click — pane menu (split·zoom·rotate·kill…)",
        "keys.g_divider": "Drag divider — resize panes",
        "keys.g_header": "Drag pane header (top border) — pick up the pane: swap "
                         "with another · move to a tab · drop on [+] for a new tab",
        "keys.g_shift": "Shift+drag — select text (copy to clipboard)",
        "keys.g_tab": "Drag tab — reorder · drop onto a pane to split",
        "keys.user_header": "User key bindings",
        "keys.none": "(none)",
    },
})
