"""Claude Code 의 **fullscreen boot canary** 상태를 읽는 순수 모듈 — pytmux-415 ⑶.

## 왜 있나

Claude Code 는 스크롤로 지나간 프롬프트를 창 맨 위에 `› <프롬프트>` 한 줄로 고정
표시하고, **그 줄을 클릭하면 그 자리로 점프**한다. 그 바도 그 클릭도 **fullscreen
(alt-screen) 렌더러의 것**이다 — 앱이 뷰포트를 쥐어야 성립한다. classic 렌더러에서는
스크롤을 터미널(=pytmux)이 쥐고 스크롤백은 앱이 이미 흘려보낸 글이라, 바를 그릴 자리도
없고 클릭도 앱에 안 간다. pytmux 가 하는 일은 그 바의 전경색을 올리는 것뿐이다
(`pytmuxlib/model.py` §`_boost_claude_prompt_bar`).

그런데 claude 는 **fullscreen 을 스스로 끈다**. fullscreen 으로 부팅하면 설정 파일에
`fullscreenBootPending[pid]` 를 적고 첫 프레임 + 10초를 살아남아야 그 기록을 지운다.
기록이 남은 채 pid 가 죽어 있으면 다음 실행이 스트라이크 +1 로 세고, **2회**면
`fullscreenAutoDisabled` 를 써서 그 머신의 fullscreen 을 끈다. 그때 stderr 알림이
**딱 한 번** 뜨고 사라지므로, 사용자는 「기능이 없어졌다」로만 겪는다(제보 2026-08-28).

⛔ **이 모듈은 고치지 않는다 — 세고 말한다.** 끈 것을 도로 켜는 것은 사용자의 몫이고
(`/tui fullscreen`), pytmux 가 남의 설정 파일을 쓰지 않는다.

## 정본 (claude 2.1.250 이진에서 읽은 규칙 · 2026-08-28 · 2.1.259 로 재확인 2026-09-03)

설정 파일 자리 — `join(CLAUDE_CONFIG_DIR ?? homedir(), ".claude.json")`:

    function Es(e){return{globalConfig:dt(e||bs(),".claude.json"), …}}

판정 — `fullscreenAutoDisabled` 는 **버전이 같을 때만 효력이 있다**:

    let F=d.fullscreenAutoDisabled;
    if(F&&F.version!==w.version)F=void 0;      // ← 버전이 다르면 무효
    …
    if(F)ee={kind:"disabled"};

상수: 임계 `vp=2`(스트라이크 2회면 끈다) · 건강 창 `wp=1e4`(10초) ·
pending TTL `Pp=600000`(10분) · 남의 host/platform TTL `Ep=2592000000`(30일).

★ **2.1.259 에서 이름이 바뀐 채 값이 그대로임을 직접 읽었다**(pytmux-415 · 앞 회차가
「200MB 이진의 정규식 탐색이 2분 상한에 걸려 못 읽었다」로 남긴 자리다 — 전수 정규식이
아니라 `stickyStrikes` 의 **바이트 오프셋**을 먼저 잡고 그 둘레만 떴다):

    function jc(w,T){let I=T.stickyStrikes??yh, …
      if(K&&K.version!==T.version)K=void 0;            // 버전이 다르면 무효
      …
      if(W>=I)K={version:T.version,at:T.now,strikes:W} // 트립: strikes 는 «누적 W»
    var yh=2      // 임계  (옛 vp)
    Sh=600000     // pending TTL 10분 (옛 Pp)
    _h=2592000000 // 남의 host/platform TTL 30일 (옛 Ep)

☠ **그래서 `strikes` 가 임계보다 클 수 있다 — 그것은 「임계가 3」이 아니다.** 트립할 때
싣는 값은 그 회차의 누적 `W` 이고, `W` 는 **한 번에 여러 개**가 더해진다(`W+=oe` —
`oe` 는 이번 스캔에서 죽어 있던 pending 항목 수). 실측(이 상자 · 2026-09-03)
`{"version":"2.1.259","strikes":3}` 은 **임계 3** 이 아니라 «한 회차에 죽은 fullscreen
boot 가 셋 세어졌다»는 뜻이다. 앞 회차가 「3 이 임계인가」로 남긴 물음의 답이 이것이다.

★ **그래서 「디스크에 서 있으면 곧 효력이 있다」로 읽어도 된다.** 버전이 다른 기록은
claude 가 **다음 기동에 지운다** — 위 `F=void 0` 가 `next` 에 그대로 실려 나가고
`Dl` 이 「`fullscreenAutoDisabled?.version` 이 달라졌다」로 쓰기를 트립시킨다. 즉 낡은
기록이 살아남는 창은 **업데이트 직후 ~ 다음 claude 기동 사이**뿐이고, 그 사이엔 애초에
claude 가 안 돈다. 그래도 사람이 그 드문 창을 알아볼 수 있게 **버전을 알림에 싣는다.**

⚠ `fullscreenAutoDisabled` 자체에는 host·platform 이 없다({version, at, strikes} 셋뿐).
host/platform 은 `fullscreenBootPending` 쪽 항목에만 붙는다 — 위 코드가 그렇다.
"""
from __future__ import annotations

import json
import os
import re
import shutil

# 설정 파일의 그 칸. **리터럴을 한 곳에** 둔다 — 아래 빠른 거르기(substring)와
# 파싱이 같은 이름을 봐야 「거르기는 통과했는데 파싱이 다른 칸을 본다」가 안 생긴다.
KEY = "fullscreenAutoDisabled"

# claude 가 스트라이크를 세는 임계(이진 상수 `vp`). 알림 문구가 「2회 중 N」처럼
# 말하지는 않지만, 기록의 strikes 가 이 값 미만이면 그 파일은 **끈 기록이 아니라**
# 세는 중인 기록이라 우리가 읽는 칸(fullscreenAutoDisabled)에는 애초에 안 선다.
STRIKE_THRESHOLD = 2


def config_path() -> str:
    """claude 의 전역 설정 파일 경로 — `$CLAUDE_CONFIG_DIR/.claude.json`, 없으면
    `~/.claude.json`.

    ⚠ `transcript.projects_dir()` 와 **다른 규칙**이다. 그쪽은
    `CLAUDE_CONFIG_DIR or ~/.claude` 아래 `projects/` 지만, 전역 설정은
    `CLAUDE_CONFIG_DIR or ~` 아래 `.claude.json` 이다(위 §정본의 `Es`). 둘을 같은
    것으로 적으면 `CLAUDE_CONFIG_DIR` 를 세운 사용자에게서 조용히 빗나간다."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~")
    return os.path.join(base, ".claude.json")


# 네이티브 설치기의 자리 규약 — `<…>/claude` 가 `<…>/versions/<판>` 을 가리키는
# 심링크다. 판 이름은 순수 semver 라 그 모양으로만 받아들인다(다른 설치 방식이면
# 안 맞고, 그때는 **모른다**로 떨어진다 — 아래 `installed_version` 참조).
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def installed_version(which=None, realpath=None) -> str | None:
    """지금 PATH 에 선 `claude` 의 판을 **best-effort** 로 읽는다. 모르면 None.

    ⛔ **추측하지 않는다.** 네이티브 설치기는 `~/.local/bin/claude` 를
    `~/.local/share/claude/versions/<판>` 으로 건다 — 그 모양일 때만 판을 말하고,
    아니면(npm·개발 트리·래퍼 스크립트) **None** 이다. 부르는 쪽은 None 을
    「버전이 다르다」로 읽으면 안 된다(아래 `auto_disabled_is_effective`).

    인자 둘은 시험용 주입구다(실제 파일 시스템을 안 밟고 규칙만 재려고 둔다)."""
    which = which or shutil.which
    realpath = realpath or os.path.realpath
    exe = which("claude")
    if not exe:
        return None
    target = os.path.basename(realpath(exe))
    return target if _VERSION_RE.match(target) else None


_AUTO = object()          # `version` 미지정 = 스스로 알아본다(None = 「모른다」와 구분)


def auto_disabled_is_effective(rec, version=_AUTO) -> bool:
    """그 기록이 **지금 도는 claude 에 효력이 있나**(pytmux-415 ⓑ).

    정본은 위 §정본의 한 줄이다 — `if(K && K.version !== T.version) K = void 0;`.
    곧 **판이 다른 기록은 claude 가 없는 셈 친다.** 그것을 「꺼져 있다」로 읽고 경고를
    띄우면 **없는 경보**다(claude 는 fullscreen 으로 멀쩡히 돈다).

    ⛔ **모르면 «효력 있다»로 둔다.** 설치 방식을 못 알아본 상자에서 조용해지면
    이 알림이 있는 이유(제보자는 「기능이 없어졌다」로만 겪었다)가 통째로 사라진다 —
    거짓 침묵이 거짓 경보보다 나쁘다. 판을 **적극적으로 다르게 읽었을 때만** 접는다.
    """
    if not rec:
        return False
    ver = installed_version() if version is _AUTO else version
    if not ver:
        return True                      # 모른다 → 종전대로 말한다
    return rec.get("version") == ver


def parse_auto_disabled(text: str):
    """설정 파일 **본문**에서 `fullscreenAutoDisabled` 기록을 꺼낸다(순수 함수).

    돌려주는 것은 `{"version": str, "at_ms": int, "strikes": int}` 또는 None.
    칸이 없거나 모양이 아니면 None — 이 함수는 **아무것도 안 던진다**(호출부가
    알림 하나 때문에 죽으면 안 된다).

    빠른 거르기: 키 문자열이 본문에 아예 없으면 JSON 파싱을 **건너뛴다**. 이 파일은
    프로젝트 이력까지 담아 수백 KB~수 MB 로 자라는데(실측 194KB), 건강한 기계에서는
    그 칸이 없는 것이 정상이라 대부분의 호출이 substring 한 번으로 끝난다.
    ⚠ 거르기는 **통과만** 시킨다 — 사용자가 프롬프트에 그 단어를 적었으면 본문에도
    뜨지만, 그때는 아래 파싱이 최상위 칸을 안 찾아 None 이 된다(오탐 없음)."""
    if not text or KEY not in text:
        return None
    try:
        doc = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(doc, dict):
        return None
    rec = doc.get(KEY)
    if not isinstance(rec, dict):
        return None
    ver = rec.get("version")
    if not isinstance(ver, str) or not ver:
        # 버전 없는 기록은 claude 가 어느 버전에도 안 맞춰 보므로(`F.version!==w.version`)
        # **영원히 무효**다. 그것을 「꺼져 있다」로 말하면 거짓 경보다.
        return None
    at = rec.get("at")
    strikes = rec.get("strikes")
    return {
        "version": ver,
        "at_ms": at if isinstance(at, int) and not isinstance(at, bool) else 0,
        "strikes": (strikes if isinstance(strikes, int)
                    and not isinstance(strikes, bool) else 0),
    }


def read_auto_disabled(path: str | None = None):
    """설정 파일을 읽어 `parse_auto_disabled` 에 넘긴다(I/O · best-effort).

    파일이 없거나 못 읽으면 None. ⛔ 예외를 밖으로 내지 않는다 — 부르는 자리는
    Claude 세션 경계 스캔이라, 여기서 던지면 **세션 회계가 통째로 멈춘다**."""
    p = path or config_path()
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None
    return parse_auto_disabled(text)
