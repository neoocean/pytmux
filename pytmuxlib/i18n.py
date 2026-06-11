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


def t(key: str, **kw) -> str:
    """키를 현재 로케일 문자열로. 없으면 ko 폴백, 그래도 없으면 키 자체(개발 중 가시).

    kw 가 있으면 `str.format(**kw)` 로 치환한다(예: t("foo", n=3) → "...{n}..." 포맷).
    포맷 실패(키 불일치 등)는 원문을 그대로 돌려 렌더가 죽지 않게 한다."""
    s = _CATALOG.get(_locale, {}).get(key)
    if s is None:
        s = _CATALOG.get(_FALLBACK, {}).get(key, key)
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
    },
    "en": {
        "lang.changed": "Language: {name}",
        "lang.usage": "usage: lang ko|en",
        "lang.name.ko": "한국어",
        "lang.name.en": "English",
        "capture.status_on": "Status: ON (capturing)",
        "capture.status_off": "Status: OFF",
    },
})
