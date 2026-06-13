"""REC(패널 출력 캡처) 서버 믹스인 — `server.Server` 의 **동적 베이스**로 합성된다
(plugins/rec/__init__.py 의 `server_mixin()`). 각 패널 raw PTY 출력을
`captures/<머신>/<날짜>_<시간>_<세션>_<탭>_p<패널>.log` 로 무손실 기록한다(탭 매핑은
sessions.log). 디렉토리를 지우면 이 믹스인이 Server 에서 통째로 빠진다(delete-to-disable).

무게 규칙: `textual`/`rich` 를 import 하지 않는다(서버도 같은 코드를 읽는다). 표준
라이브러리만 쓴다. 상태(`_capfiles`/`_cappaths`/`capture`)는 `server_init` 훅이 설치한다.

코어 잔류: `_capture_id`(소켓별 격리 id)와 `PROJECT_DIR` 은 토큰 DB(claude-code)가 빌려
쓰므로 `pytmuxlib/servercapture.py` 에 남는다(REC_SCENARIO §10 ①). 이 믹스인은 그
`self._capture_id()`(합성된 코어 메서드)와 core 의 `PROJECT_DIR` 을 참조한다."""
from __future__ import annotations

import os
import re
import socket
import time

from pytmuxlib import ipc
from pytmuxlib.model import Pane
from pytmuxlib.servercapture import PROJECT_DIR

# 파일명에 허용할 문자(영문/숫자/한글/._-). 그 외는 제거하고 공백은 - 로 바꾼다.
_UNSAFE_RE = re.compile(r"[^0-9A-Za-z가-힣._-]+")


def _safe(s) -> str:
    """세션/탭 이름을 파일명 조각으로 정리(공백→-, 비허용 문자 제거)."""
    s = re.sub(r"\s+", "-", str(s).strip())
    s = _UNSAFE_RE.sub("", s)
    return s or "x"


class ServerRecMixin:
    # ---- 패널 출력 캡처(Claude 화면 문구 분석용) ----
    def _capture_subdir(self) -> str:
        """captures/ 하위 폴더명. 기본 소켓(실사용)은 여러 기계가 같은 Perforce
        captures/ 를 공유하므로 **머신 이름**으로 격리한다. 비기본(임시·named) 소켓은
        sock-id(코어 `_capture_id`)를 그대로 쓴다."""
        if self.sock_path == ipc.default_endpoint():
            return _safe(socket.gethostname()) or self._capture_id()
        return self._capture_id()

    @property
    def capture_dir(self) -> str:
        """캡처(REC) 출력 루트. 기본 소켓은 `PROJECT_DIR/captures/<머신이름>/`(Perforce
        공유·기계별 격리), 비기본 소켓은 휘발 영역(state_base 옆 `.capture`).
        `PYTMUX_CAPTURE_DIR` 로 강제 지정 가능(테스트). **GitHub 미러 차단**: 이 경로는
        `.gitignore`/`.p4ignore` 의 `captures/` 로 막는다(민감 화면 유출 방지)."""
        override = os.environ.get("PYTMUX_CAPTURE_DIR")
        if override:
            return os.path.join(override, self._capture_subdir())
        if self.sock_path == ipc.default_endpoint():
            return os.path.join(PROJECT_DIR, "captures", self._capture_subdir())
        return ipc.state_base(self.sock_path) + ".capture"

    def _capture_info(self, pane):
        """활성 패널의 현재 캡처 파일 절대경로·크기(REC 클릭 팝업용).
        아직 기록 시작 전(파일 미생성)이거나 캡처 off 면 (None,0)."""
        if not self.capture or pane is None:
            return None, 0
        path = self._cappaths.get(pane.id)   # 파일명에 시각이 박혀 핸들에서 보관해 둔 경로
        if path is None:
            return None, 0
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return path, size

    def _pane_idents(self, pane: Pane):
        """패널의 (세션, 탭, 패널) 파일명 조각. 못 찾으면 ('x','x', pane.id).
        탭은 '<인덱스>.<이름>'(정렬·식별 겸용), 패널은 전역 유일 pane.id 문자열."""
        for sess in self.sessions.values():
            for i, tab in enumerate(sess.tabs):
                if pane in tab.window.panes():
                    return _safe(sess.name), f"{i}.{_safe(tab.name)}", str(pane.id)
        return "x", "x", str(pane.id)

    def _pane_location(self, pane: Pane) -> str:
        """패널이 속한 탭을 'tab<idx>:<name>' 로 반환(sessions.log 메타용)."""
        for sess in self.sessions.values():
            for i, tab in enumerate(sess.tabs):
                if pane in tab.window.panes():
                    return f"tab{i}:{tab.name}"
        return "tab?:?"

    def _capture_filename(self, pane: Pane) -> str:
        """REC 캡처 파일명: <날짜>_<시간>_<세션>_<탭>_p<패널>.log (기록 시작 시각 기준).
        예: 20260608_184500_0_0.claude_p1.log"""
        sess, tab, pn = self._pane_idents(pane)
        ts = time.strftime("%Y%m%d_%H%M%S")
        return f"{ts}_{sess}_{tab}_p{pn}.log"

    def _capture_write(self, pane: Pane, data: bytes):
        """패널 raw PTY 출력을 <날짜>_<시간>_<세션>_<탭>_p<패널>.log 에 무손실 append."""
        fh = self._capfiles.get(pane.id)
        if fh is None:
            try:
                os.makedirs(self.capture_dir, exist_ok=True)
                # 캡처 디렉터리는 0700(F4): raw 출력엔 표시·에코된 비밀번호·토큰이 남을
                # 수 있어 같은 머신의 다른 로컬 사용자가 못 읽게 좁힌다(best-effort).
                try:
                    os.chmod(self.capture_dir, 0o700)
                except OSError:
                    pass
                path = os.path.join(self.capture_dir, self._capture_filename(pane))
                fh = ipc.open_private(path, "ab", buffering=0)   # 0600(F4)
            except OSError:
                return
            self._capfiles[pane.id] = fh
            self._cappaths[pane.id] = path
            # 탭/패널 매핑을 별도 텍스트 로그에 기록(raw 로그는 오염하지 않음).
            try:
                meta = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                        f"{os.path.basename(path)} {self._pane_location(pane)} "
                        f"title={pane.title!r}\n")
                with ipc.open_private(                          # 0600(F4)
                        os.path.join(self.capture_dir, "sessions.log"), "a") as mf:
                    mf.write(meta)
            except OSError:
                pass
        try:
            fh.write(data)
        except OSError:
            self._close_capfile(pane.id)

    def _close_capfile(self, pane_id: int):
        self._cappaths.pop(pane_id, None)   # 다음에 켤 때 새 시각 파일명으로 재오픈
        fh = self._capfiles.pop(pane_id, None)
        if fh is not None:
            try:
                fh.close()
            except OSError:
                pass

    def _close_all_capfiles(self):
        for pid in list(self._capfiles):
            self._close_capfile(pid)

    def set_capture(self, value=None):
        """출력 캡처 토글. value 미지정 시 반전. 상태를 opts.json 에 영속."""
        self.capture = (not self.capture) if value is None else bool(value)
        self._save_opts()
        if not self.capture:        # 끄면 열린 파일을 닫음(켜면 lazy 재오픈)
            self._close_all_capfiles()
        return self.capture
