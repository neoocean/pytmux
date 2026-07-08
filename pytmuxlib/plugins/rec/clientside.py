"""REC 플러그인 클라이언트 표시(배지·흡수·팝업 탭·정보 줄). 코어 clientwidgets/
client.py 에서 이전(REC_SCENARIO §4.2). 무게 규칙상 rec/__init__.py 는 이 모듈을
attach_client/훅에서 **지연 import** 한다(서버 프로세스가 textual 을 안 읽게).

코어는 capture/_rec_zone/capture_path/capture_size 를 직접 두지 않고, 이 플러그인이
client_statusbar_init 으로 status 위젯에 설치한다. 흡수(update)·배지(statusbar)·팝업
탭(status_tabs)이 그 값을 읽고 쓴다. 디렉토리 삭제 시 이 훅들이 사라져 배지·탭·흡수가
전부 비활성되고 코어는 getattr/.get 가드로 무탈하다(delete-to-disable)."""
from __future__ import annotations

import os

from pytmuxlib import i18n


# ---- 상태 위젯 필드 소유(client_statusbar_init) ----
def init_status_defaults(status):
    """StatusBar 생성 직후 — REC 표시 상태를 안전한 기본값으로 설치한다(코어
    clientwidgets.__init__ 에서 이전). 흡수/배지가 이 속성을 읽고 쓴다."""
    status.capture = False        # 패널 출력 캡처 중(서버 옵션, 기본 OFF)
    status._rec_zone = None       # (x0, x1) REC 클릭 영역(캡처 정보 팝업)
    status.capture_path = None    # 활성 패널 캡처 파일 경로
    status.capture_size = 0       # 그 파일 크기(bytes)


# ---- status 메시지 흡수(client_statusbar_update) ----
def absorb(status, msg):
    """status 메시지의 capture/capture_path/capture_size 를 위젯에 흡수(코어
    clientwidgets.update_status 에서 이전). 키 부재 시 안전 기본값."""
    status.capture = msg.get("capture", False)
    status.capture_path = msg.get("capture_path")
    status.capture_size = msg.get("capture_size", 0)


# ---- 상태줄 REC 배지(client_statusbar) ----
def render_badge(status, segs, w0):
    """캡처 ON 이면 ` REC ` 세그먼트를 append 하고 _rec_zone(클릭존)을 채운다(코어
    clientwidgets._render_main 에서 이전). w0=들어오는 누적 셀폭, 반환=새 누적 폭(P6).
    plugins 부재 시 이 훅이 사라져 배지·클릭존이 안 생긴다."""
    from rich.segment import Segment
    from rich.style import Style
    from pytmuxlib.clientutil import theme_color
    status._rec_zone = None
    if not getattr(status, "capture", False):
        return w0
    tc = lambda n: theme_color(status, n)  # noqa: E731
    rx0 = w0
    status._rec_zone = (rx0, rx0 + 5)   # " REC "
    rec_st = (Style(color="black", bgcolor=tc("warning"), bold=True)
              if getattr(status, "focus_btn", None) == "rec"
              else Style(color="white", bgcolor=tc("error"), bold=True))
    segs.append(Segment(" REC ", rec_st))
    return w0 + 5


# ---- 캡처 정보 줄(팝업 본문) ----
def capture_info_lines(app, path=None, size=None):
    """REC(출력 캡처) 정보 줄(코어 client._capture_info_lines 에서 이전). 인자를 안
    주면 상태줄에 마지막으로 온 값을 쓴다. 맨 앞에 ON/OFF 를 보여 [c] 토글 결과가
    바로 반영된다."""
    on = bool(getattr(app.status, "capture", False))
    head = (i18n.t("capture.status_on") if on else i18n.t("capture.status_off"))
    if path is None:
        path = getattr(app.status, "capture_path", None)
        size = getattr(app.status, "capture_size", 0) or 0
    if not on:
        return [head, "(캡처 꺼짐 — REC 미표시)"]
    if not path:
        return [head, "(캡처 파일 준비 중…)"]
    return [head,
            f"파일: {path}",
            f"크기: {size:,} bytes ({size / 1024:,.1f} KiB)",
            f"탭 매핑: {os.path.join(os.path.dirname(path), 'sessions.log')}"]


def show_capture_info(app, path=None, size=None):
    """REC 배지/버튼 클릭 → 통합 상태 팝업의 '출력 캡처' 탭(index 0)으로 연다(코어
    client.show_capture_info 에서 이전). 캡처 탭 줄은 client_status_tabs 훅이 status 의
    capture_path/size 에서 생성하므로, 명시 path 가 오면 그 값을 status 에 반영한다
    (클릭 핸들러는 status.capture_path 를 그대로 넘겨 실사용에선 무변경)."""
    if path is not None:
        app.status.capture_path = path
        app.status.capture_size = size or 0
    app._status_tab_initial = 0   # 0 = 캡처 탭(REC 가 왼쪽)
    app.request_tree(purpose="status_tabs")


# ---- 클라 명령 디스패치(capture-output/capture-toggle) ----
def handle_command(app, c, args):
    """rec 클라 명령을 처리한다(코어 clientcmd 의 capture-output/capture-toggle elif
    에서 이전 — delete-to-disable: 디렉토리 삭제 시 이 훅이 사라져 명령이 디스패치
    어디에도 안 나타난다). on/off/무인자(토글) 파싱 후 서버에 set_capture 전송.
    처리하면 True."""
    if c in ("capture-output", "capture-toggle"):
        val = True if "on" in args else (False if "off" in args else None)
        app.send_cmd("set_capture", value=val)
        state = (i18n.t("word.toggle") if val is None
                 else ("ON" if val else "OFF"))
        app.display_message(i18n.t("msg.capture_toggle", state=state))
        return True
    return False


# ---- 통합 상태 팝업의 'REC' 탭(client_status_tabs) ----
def status_tab(app, tree):
    """(제목, 줄, 동작) 3-튜플로 REC 탭을 기여한다(코어 client._open_status_tabs 의
    하드코딩 REC 탭 + [c]/[o] 동작에서 이전). 동작 콜백은 갱신된 줄을 반환한다."""
    from pytmuxlib import proc

    def _toggle_capture():
        # capture-output 토글 명령 전송 + 낙관적 로컬 반영 → 갱신된 캡처 줄.
        app._run_command("capture-output")
        app.status.capture = not bool(getattr(app.status, "capture", False))
        if not app.status.capture:
            app.status.capture_path = None
            app.status.capture_size = 0
        app.status.refresh()
        return capture_info_lines(app)

    def _open_capture_dir():
        # 기록 중인 캡처 파일이 있는 디렉터리를 OS 파일 관리자로 연다(요청). 캡처 경로는
        # 클라이언트 머신 기준(서버=로컬일 때 유효). 없으면 안내.
        path = getattr(app.status, "capture_path", None)
        if path and proc.open_in_file_manager(os.path.dirname(path)):
            app.display_message(i18n.t("msg.open_capture_dir"))
        else:
            app.display_message(i18n.t("msg.no_capture_dir"))
        return None   # 줄 갱신 없음(팝업 유지)

    actions = [("c", "[c] 캡처 켜기/끄기", _toggle_capture),
               ("o", "[o] 기록 폴더 열기", _open_capture_dir)]
    return ("출력 캡처(REC)", capture_info_lines(app), actions)
