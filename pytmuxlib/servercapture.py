"""캡처 식별 유틸(코어 잔류분). REC(패널 출력 캡처) **본체**는 `plugins/rec/` 로 추출
됐으나(docs/internal/REC_SCENARIO.md), 소켓별 격리 id(`_capture_id`)와 프로젝트 루트
(`PROJECT_DIR`)는 **토큰 DB**(claude-code 플러그인)가 빌려 쓰므로 코어에 남는다
(REC_SCENARIO §10 ①). REC 플러그인의 `capture_dir`/`_capture_subdir` 도 이 둘을 참조한다.

`Server` 가 `ServerCaptureIdMixin` 을 상속해 `_capture_id` 를 갖는다. 이 모듈은 표준
라이브러리만 쓴다(서버측)."""
from __future__ import annotations

import os

from . import ipc

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ServerCaptureIdMixin:
    def _capture_id(self) -> str:
        """소켓별 격리 id(토큰 DB 등). state_base 의 basename 에서 .sock 제거."""
        base = os.path.basename(ipc.state_base(self.sock_path))
        if base.endswith(".sock"):
            base = base[:-len(".sock")]
        return base or "default"
