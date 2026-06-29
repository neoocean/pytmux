"""claude-reconstruct 플러그인 — pytmux 안의 Claude Code 출력을 **투명하게 가로채
재구성**해 깨짐 없이 보여준다(오버레이 아님·모달 아님·완전 투명).

목표(사용자, 2026-06-29 야간): pytmux 안에서 Claude Code 가 그 자체의 CJK 처리·alt-screen
출력 버그·버전 변경과 **무관하게** 단독 실행한 것처럼 깨끗이 출력되고, 스크롤해도 안
깨지며, 입력도 평소대로 동작한다. 즉 **중간에서 완전히 투명한 재구성 레이어**.

어떻게(설계 §):
  * Claude 패널의 raw PTY 출력 바이트를 `server_pty_output` 훅으로 가로채, 패널마다 둔
    **shadow 에뮬레이터**(`shadow.Shadow`, 모호폭=narrow 고정 `model.Pane`)에 **병렬로**
    먹인다. shadow 는 Claude 의 "모호폭=1칸" 가정과 일치하므로 격자가 절대 발산하지 않는다.
  * 클라 전송 직전 `server_filter_rows` 훅에서 그 패널의 행을 shadow 의 **깨끗한 행으로
    통째 교체**한다(claude-disable-feedback 의 행-교체 계약과 동일). 클라 합성은 행 텍스트를
    좌→우로 재배치하므로 wide 클라에서도 겹치지 않는다.
  * **입력은 안 건드린다** — 키는 지금처럼 Claude 실 PTY 로 간다. 우리는 표시만 가로챈다
    → 완전 투명.

이전 두 시도와의 관계(역사): ① 라이브 패널 오버레이(server_filter_rows 로 transcript 재구성
행 덮어쓰기, p4 61566) — Claude alt-screen 자기재그리기와 충돌해 깨짐. ② 별도 모달 페이저
(claude-rc 명령, p4 61587) — 안 깨지지만 직접 쓰기 불편(투명 아님). **현행(③)** 은 둘 다
버리고 **같은 바이트 스트림을 narrow 에뮬레이터로 병렬 재생**한다 — transcript↔VT 불일치가
구조적으로 없다(우리가 Claude 출력의 단말 에뮬레이터 그 자체이므로).

delete-to-disable: 이 디렉토리를 지우면 `server_pty_output`/`server_filter_rows` 미구현 →
행은 변형 없이 지나가고 코어는 그대로 동작한다(코어 무수정 — 전 접점이 레지스트리 훅).
무게: 이 __init__·shadow·detect 는 textual/rich 를 import 하지 않는다(서버도 plugins.load).
"""
from __future__ import annotations

import time

from pytmuxlib import cellwidth, i18n
from . import detect, shadow  # noqa: F401

COMMANDS = [
    ("claude-reconstruct",
     "Claude 패널 출력을 깨끗이 재구성하는 투명 레이어를 켜고 끈다(별칭 claude-rc)",
     "Claude"),
]
NOARG = {"claude-reconstruct", "claude-rc"}
_ALIASES = ("claude-reconstruct", "claude-rc")

i18n.register({
    "ko": {
        "cmd.claude-reconstruct": "Claude 출력 투명 재구성 레이어 켜기/끄기(별칭 claude-rc)",
        "rc.on": "Claude 재구성: 켜짐 — 출력을 깨끗이 재구성합니다",
        "rc.off": "Claude 재구성: 꺼짐 — 원본 출력을 그대로 표시합니다",
    },
    "en": {
        "cmd.claude-reconstruct": "Toggle transparent clean reconstruction of Claude output (alias claude-rc)",
        "rc.on": "Claude reconstruct: ON — output is cleanly reconstructed",
        "rc.off": "Claude reconstruct: OFF — showing raw output",
    },
})

# 자기완결 detection(claude-code 부재 시) 디바운스 — ps 왕복을 이 주기로만.
_SELF_DETECT_SEC = 2.0

# server_filter_cursor 패스스루 센티넬: 이 프레임에 커서를 안 건드린다(원본 유지).
# None 은 "커서 숨김"이라는 유효값이라 별도 객체를 쓴다.
_PASS = object()


def _is_claude_pane(pane) -> bool:
    """이 패널이 Claude Code 패널인가. 평상은 claude-code 가 심은 `_hdr_claude`(디바운스)·
    `_claude` 로 무비용 판정. claude-code 가 없으면(`_hdr_claude` 속성 부재) 자체
    프로세스트리 판정 결과(`_rc_self`, server_scan 이 디바운스 갱신)를 본다."""
    if hasattr(pane, "_hdr_claude"):
        return bool(pane._hdr_claude or getattr(pane, "_claude", None))
    return bool(getattr(pane, "_rc_self", False))


def _nonblank_rows(rows) -> int:
    """비어 있지 않은 행 수(워밍업/싱크전 shadow 판별용 — 싼 근사)."""
    n = 0
    for row in rows:
        for t, _ in row:
            if t and not t.isspace():
                n += 1
                break
    return n


class _ClaudeReconstructPlugin:
    name = "claude-reconstruct"
    description = "Claude 출력을 narrow 에뮬레이터로 병렬 재생해 깨짐 없이 투명 재구성"
    category = "Claude"
    commands = COMMANDS
    noarg = NOARG
    completions = []

    def __init__(self):
        # 전역 on/off(런타임 토글, 기본 켜짐). 서버 프로세스 단일 인스턴스(PLUGIN)라
        # 서버측 훅이 직접 읽고, claude-rc 명령(→ server_command)이 뒤집는다.
        self.enabled = True

    def _active(self) -> bool:
        """재구성이 실제로 동작할 조건. **wide 모드(모호폭=2 단말)** 에서만 켠다 — 깨짐은
        Claude(1칸 가정)와 wide 격자(2칸)의 발산에서만 생기고, narrow 모드에서는 shadow
        격자가 실 패널과 동일해 이득 없이 이중 feed 만 늘기 때문이다(절대다수 단말=narrow
        는 비용 0). narrow 단말에서 깨진다면 그건 폭 문제가 아니라 shadow 도 동일 바이트로
        똑같이 재현하므로 어차피 못 고친다. 런타임에 wide↔narrow 가 바뀌면 다음 출력부터
        자동 반영(shadow 는 lazy 생성)."""
        return self.enabled and cellwidth.ambiguous_wide()

    # ---- 패널 상태(서버 소유) ----
    def pane_init(self, pane):
        # shadow 패널 자신에는 우리 필드를 안 심는다(재진입·중첩 shadow 방지). pane=None
        # 은 무탈(플러그인 계약 — test_plugin_contract 가 None 인자로 no-op 확인).
        if pane is None or shadow.creating():
            return
        pane._rc_shadow = None
        pane._rc_self = False        # 자기완결 detection 결과(claude-code 부재 시만)
        pane._rc_detect_ts = 0.0
        pane._rc_cursor = _PASS      # server_filter_cursor 이번 프레임 커서 오버라이드

    def pane_reset(self, pane):
        # respawn(새 셸) → shadow 폐기(새 세션일 수 있음).
        if pane is None or shadow.creating():
            return
        pane._rc_shadow = None
        pane._rc_self = False
        pane._rc_detect_ts = 0.0

    def pane_closing(self, server, pane):
        if pane is not None:
            pane._rc_shadow = None

    # ---- 서버: 출력 가로채기 → shadow 에 병렬 feed ----
    def _ensure_shadow(self, pane):
        sh = getattr(pane, "_rc_shadow", None)
        if sh is None:
            sh = shadow.Shadow(pane.cols, pane.rows)
            pane._rc_shadow = sh
        elif sh.cols != pane.cols or sh.rows != pane.rows:
            sh.resize(pane.cols, pane.rows)
        return sh

    def server_pty_output(self, server, pane, data):
        """패널 PTY 출력 1조각(raw 바이트). Claude 패널이면 shadow 에뮬레이터에 narrow 로
        병렬 feed. **30Hz 드레인의 모든 바이트마다 불리는 핫패스** — Claude 아님이면 즉시
        반환(게이트는 무비용 속성 읽기)."""
        if not self._active() or not _is_claude_pane(pane):
            return
        if not hasattr(pane, "_rc_shadow"):   # shadow 패널 자신 등
            return
        try:
            self._ensure_shadow(pane).feed(data)
        except Exception:
            # 재구성은 표시 보조라 어떤 실패도 코어 출력 경로를 막지 않는다.
            pane._rc_shadow = None

    def server_scan(self, server, sess, win):
        """30Hz flush 스캔. claude-code 가 없을 때만(=`_hdr_claude` 속성 부재) 자기완결
        프로세스트리 판정을 디바운스(~2s)로 돌려 `_rc_self` 를 갱신한다. claude-code 가
        있으면 무비용(즉시 반환)이라 핫패스에 부담 없음. 상태 메시지 변화는 없음."""
        if not self._active() or not win:
            return False
        now = time.monotonic()
        for p in win.panes():
            if hasattr(p, "_hdr_claude"):
                continue   # claude-code 가 게이트 제공 — ps 불필요
            if not hasattr(p, "_rc_detect_ts"):
                continue
            if now - p._rc_detect_ts >= _SELF_DETECT_SEC:
                p._rc_detect_ts = now
                try:
                    p._rc_self = detect.has_claude_descendant(
                        getattr(p, "child_pid", None))
                except Exception:
                    p._rc_self = False
        return False

    # ---- 서버: 전송 직전 행 교체 ----
    def server_filter_rows(self, server, pane, rows):
        """Claude 패널의 render 행을 shadow 의 깨끗한 narrow 행으로 통째 교체한다.
        교체 조건: 켜짐 + shadow 존재 + Claude 패널 + shadow 가 alt-screen(=Claude
        풀스크린 TUI 가동 중). 셸 프롬프트(비-alt)나 워밍업(싱크 전) 등에서는 원본 그대로
        둬 회귀를 막는다. 새 리스트를 돌린다(render 캐시 공유 — in-place 금지 계약).
        커서는 별 채널(server_filter_cursor)이라 여기서 shadow 커서를 pane._rc_cursor 에
        스태시해 둔다(같은 flush 에서 곧바로 server_filter_cursor 가 읽음). 스왑 안 하면
        _PASS(원본 커서 유지)로 둔다."""
        if pane is not None and hasattr(pane, "_rc_cursor"):
            pane._rc_cursor = _PASS          # 이 프레임 기본: 커서 안 건드림
        if not self._active():
            return rows
        sh = getattr(pane, "_rc_shadow", None)
        if sh is None or not _is_claude_pane(pane) or not sh.alt_active:
            return rows
        try:
            if sh.cols != pane.cols or sh.rows != pane.rows:
                sh.resize(pane.cols, pane.rows)
            srows, scur = sh.render()
        except Exception:
            return rows
        # 워밍업 폴백: shadow 가 라이브 중간부터 시작하면 Claude 의 다음 풀리페인트
        # 전까지 alt 버퍼가 **비어 있다**(아직 한 글자도 안 그림). 그동안 원본이 실내용을
        # 가졌으면 원본을 유지한다(빈 화면으로 깜빡임 방지). Claude 는 sub-second 로 풀
        # 리페인트하므로 이 창은 짧고, 일단 그리면 shadow 가 권위를 갖는다. 희소하지만
        # 유효한 화면(프롬프트만 있는 등)은 비어 있지 않으므로 오발동하지 않는다.
        if _nonblank_rows(srows) == 0 and _nonblank_rows(rows) > 0:
            return rows
        # 프레임 일관성: soft-wrap 연속원 인덱스도 shadow 격자 기준으로 맞춘다(코어 flush
        # 는 pane._last_wrap 을 쓰는데 그건 실 패널 render 가 셋한 값이라 격자가 다르다).
        try:
            pane._last_wrap = list(sh.pane._last_wrap)
        except Exception:
            pass
        # 커서 스태시: shadow 의 narrow 커서를 wide 합성 col 로 매핑해 둔다(숨김이면 None).
        if hasattr(pane, "_rc_cursor"):
            if scur is None:
                pane._rc_cursor = None
            else:
                cx, cy = scur
                if 0 <= cy < len(srows):
                    cx = shadow.wide_cursor_x(srows[cy], cx)
                pane._rc_cursor = [cx, cy]
        return srows

    def server_filter_cursor(self, server, pane, cursor):
        """전송 직전 커서 오버라이드. server_filter_rows 가 행을 shadow 로 교체했으면 같은
        프레임에 스태시한 매핑 커서(pane._rc_cursor)를 돌려, 실 패널(wide·발산)의 커서 대신
        shadow narrow→wide 매핑 커서를 쓴다. 스왑 안 했으면 _PASS → 원본 커서 그대로."""
        ov = getattr(pane, "_rc_cursor", _PASS) if pane is not None else _PASS
        return cursor if ov is _PASS else ov

    # ---- 클라이언트: 명령 → 서버 토글 ----
    def handle_command(self, app, c, args):
        if c in _ALIASES:
            app.send_cmd("rc_toggle")
            return True
        return False

    def server_command(self, server, client, sess, action, msg):
        if action != "rc_toggle":
            return None
        self.enabled = not self.enabled
        # 모든 패널을 dirty 로 표시해 다음 flush 가 새 행(원본↔재구성)을 다시 보낸다.
        for s in server.sessions.values():
            for t in getattr(s, "tabs", []):
                for p in t.window.panes():
                    p.dirty = True
        return "broadcast"


PLUGIN = _ClaudeReconstructPlugin()
