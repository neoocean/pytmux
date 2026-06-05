#!/usr/bin/env python3
"""pytmux — Python/Textual 기반 tmux 유사 터미널 멀티플렉서.

설계 문서: docs/DESIGN.md

아키텍처: 셸 PTY 를 소유하는 백그라운드 데몬(서버) 과 화면을 그리는 Textual
클라이언트를 유닉스 도메인 소켓으로 연결한다. 클라이언트나 상위 터미널을 닫아도
서버와 셸 세션은 유지된다.

사용법:
    python3 pytmux.py                 # 서버가 없으면 데몬 기동 후 attach, 있으면 attach
    python3 pytmux.py attach -t NAME  # 이름 있는 세션에 attach(없으면 생성)
    python3 pytmux.py new -s NAME     # 이름 있는 세션 생성 후 attach
    python3 pytmux.py ls              # 세션 목록
    python3 pytmux.py kill-server     # 서버와 모든 세션 종료
    python3 pytmux.py --socket PATH   # 사용할 소켓 경로 지정

기본 키 (prefix = Ctrl-b, 설정으로 변경 가능):
    prefix %      좌우 분할        prefix "      상하 분할
    prefix x      패널 삭제(확인)  prefix z      패널 줌 토글
    prefix o      다음 패널        prefix ←↑↓→   패널 이동
    prefix H/J/K/L 패널 경계 이동  prefix c      새 윈도우
    prefix ,      윈도우 이름변경  prefix &      윈도우 삭제(확인)
    prefix $      세션 이름변경    prefix :      명령 입력
    prefix n / p  다음/이전 윈도우 prefix 0-9    윈도우 선택
    prefix d      detach           prefix [      스크롤백 모드
    prefix Enter  메뉴 열기
    ESC           명령 모드(←↑↓→ 패널 이동, : 명령 프롬프트, 그 외 키로 종료)
    명령 프롬프트: ?=명령 목록(방향키 선택)  →=자동완성 수락
마우스:
    휠 위/아래    해당 패널 스크롤백        패널 클릭   포커스 이동
    경계선 드래그 패널 리사이즈            우클릭      메뉴 열기

설정 파일: ~/.config/pytmux/config (set prefix / set mouse / set status-bg /
    set status-fg / bind <key> <command>). 자세한 내용은 load_config 참고.
"""

# client(=textual) 은 무거우므로 여기서 즉시 import 하지 않는다 — 서버 하위프로세스
# (`pytmux.py server`)나 가벼운 제어 명령(ls/cmd/kill)이 import 만으로 textual 전체를
# 끌어와 기동이 느려지던 문제(Windows 사용자 보고). 가벼운 하위모듈만 즉시 재노출하고,
# client 의 심볼은 모듈 __getattr__(PEP 562)로 **처음 접근할 때** 지연 로드한다
# (`import pytmux; pytmux.build_client_app` 같은 테스트/외부 호환 유지).
from pytmuxlib.keymap import (  # noqa: F401
    _key_to_ctrl_bytes, _tmux_key_to_textual, load_config)
from pytmuxlib.launcher import (  # noqa: F401
    can_connect, control_request, ensure_server, main)
from pytmuxlib.model import (  # noqa: F401
    ClientConn, Pane, Session, Split, Tab, Window, pid_counter, split_counter)
from pytmuxlib.protocol import (  # noqa: F401
    FLUSH_HZ, HISTORY, MIN_H, MIN_W, conv_color,
    parse_reset_delay, read_msg, set_winsize, write_msg)
from pytmuxlib.replay import render_pane_lines, replay  # noqa: F401
from pytmuxlib.server import Server, run_server  # noqa: F401

# 지연 재노출: 접근 전엔 textual 을 안 끌어온다(client 모듈에서만 옴).
_LAZY = {"build_client_app": "pytmuxlib.client",
         "run_client": "pytmuxlib.client"}


def __getattr__(name):   # PEP 562
    mod = _LAZY.get(name)
    if mod is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib
    val = getattr(importlib.import_module(mod), name)
    globals()[name] = val   # 다음 접근부터는 캐시된 전역으로
    return val


if __name__ == "__main__":
    main()
