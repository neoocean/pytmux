"""클라에게 보낼 Claude 트랜스크립트 **꼬리**(원문 JSONL).

# 왜 원문을 보내나 — 파서가 둘이 되지 않게

원격 탭의 Claude 뷰를 채우는 방법은 둘이었다(설계문서 §7 P5):

- ⓐ 상류가 **항목까지 만들어** 보낸다. 서버가 권위라는 결을 따르지만, 파이썬에는
  표시용 항목 파서가 **아예 없다** — 이 디렉토리의 `transcript.py` 는 토큰 회계용
  레코드 합산기다(usage 합·모델명). 툴 이름·상태 배지·거부 판정·플랜 항목·권한 모드
  같은 표시 의미는 러스트 `pytmux_client_claude`(약 500줄)에만 있다. 그러니 ⓐ 의
  비용은 "픽스처 한 벌"이 아니라 **파이썬에 항목 파서를 새로 쓰는 것**이고, 그 파서가
  러스트와 어긋나면 같은 대화가 탭에 따라 달라 보인다.
- ⓑ 상류가 **원문을 보내고** 클라가 파싱한다. 파서는 하나로 남는다.

ⓑ 의 반대 근거는 '사적'이 아니라 **양**이었다(대화 본문이 MB 단위로 30Hz 루프를 탄다).
그래서 여기서 하는 일은 하나다 — **상한을 걸어 꼬리만 자른다**. 사용자 결정
2026-07-28: ⓑ' 확정.

# 얼마나 보내나

클라 화면이 쓰는 것은 마지막 몇 항목이다(요약 구역 5줄 + 전문 화면 하나). 그래서
`MAX_TAIL_BYTES`/`MAX_TAIL_LINES` 중 **먼저 걸리는 쪽**으로 자른다. 대화 전체를 볼
필요가 있으면 그건 원격이 아니라 그 기계에서 할 일이다.

# 반 토막 줄을 보내지 않는다

파일 끝에서 바이트로 자르면 첫 줄은 거의 항상 반 토막이다. 그대로 보내면 클라는 그
줄을 조용히 버리거나(운이 좋으면) **틀린 항목으로 파싱한다**(운이 나쁘면). 그래서
중간부터 읽었으면 첫 개행까지를 버린다.
"""
from __future__ import annotations

import os

#: 한 번에 보낼 원문 상한. Claude 트랜스크립트 한 줄은 수 KB 도 흔하다(도구 결과
#: 본문이 통째로 들어간다) — 64KB 면 최근 항목 수십 개가 들어온다.
MAX_TAIL_BYTES = 64 * 1024

#: 줄 수 상한. 바이트 상한만 두면 짧은 줄이 이어질 때 수백 항목이 실린다.
MAX_TAIL_LINES = 80

#: 파일을 다시 들여다보기까지 건너뛸 flush 프레임 수(30Hz 기준 약 0.5초).
#: 매 프레임 stat 하면 패널마다 초당 30번 파일시스템을 두드린다. 사람이 읽는 목록이라
#: 이 지연은 안 보인다 — 네이티브 클라도 같은 값으로 폴링한다(`CLAUDE_POLL`).
TAIL_FRAMES = 15

_SIG = "_claude_tail_sig"      # 마지막으로 보낸 파일의 (크기, mtime)
_COUNT = "_claude_tail_n"      # 남은 건너뛰기 프레임 수


def read_tail(path, max_bytes=MAX_TAIL_BYTES, max_lines=MAX_TAIL_LINES,
              open_fn=open):
    """파일 끝에서 **온전한 줄만** 잘라 온다. 못 읽으면 None.

    온전한 줄이 하나도 없으면(한 줄이 상한보다 크다) 빈 문자열이다 — None(못 읽었다)과
    구분해야 호출부가 "파일이 없다"와 "보낼 줄이 없다"를 섞지 않는다.
    """
    try:
        with open_fn(path, "rb") as fp:
            fp.seek(0, os.SEEK_END)
            size = fp.tell()
            start = max(0, size - max_bytes)
            fp.seek(start)
            raw = fp.read()
    except OSError:
        return None
    if start > 0:
        cut = raw.find(b"\n")
        if cut < 0:
            return ""          # 상한 안에 개행이 없다 = 온전한 줄 0개
        raw = raw[cut + 1:]
    # errors="replace": 상한 경계가 여러 바이트 글자를 가를 수 있다. 위에서 첫 줄을
    # 버리면 대개 해소되지만(start>0), 남은 자리에서 예외가 새면 이 패널의 Claude 뷰가
    # 통째로 죽는다 — best-effort 경로에 어울리지 않는다.
    lines = raw.decode("utf-8", "replace").splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


def due(pane, force=False):
    """지금 파일을 들여다볼 차례인가. 차례면 카운터를 다시 채운다.

    `force` 는 새로 붙은 클라에게 현재 상태를 보낼 때다(`_send_full`) — 그 클라는
    "바뀐 적이 없다"는 이유로 빈 화면을 봐서는 안 된다.
    """
    if force:
        return True
    left = getattr(pane, _COUNT, 0)
    if left > 0:
        setattr(pane, _COUNT, left - 1)
        return False
    setattr(pane, _COUNT, TAIL_FRAMES)
    return True


def pane_tail(pane, path, force=False, stat_fn=os.stat, **kw):
    """이 패널의 꼬리 원문. **바뀐 게 없으면 None**(= 보낼 것 없음).

    바뀜 판정은 `(크기, mtime)` 이다. 내용 해시를 쓰지 않는 이유: 파일 전체를 읽어야
    하고, 트랜스크립트는 **덧붙이기만** 하므로 크기가 곧 신호다. mtime 을 함께 보는
    것은 잘렸다가 같은 크기로 다시 찬 경우(세션 재시작)를 잡기 위해서다.
    """
    try:
        st = stat_fn(path)
    except OSError:
        return None
    sig = (st.st_size, st.st_mtime_ns)
    if not force and getattr(pane, _SIG, None) == sig:
        return None
    text = read_tail(path, **kw)
    if text is None:
        return None
    # 실제로 읽어낸 뒤에 표식을 남긴다 — 읽기에 실패했는데 표식만 갱신하면 다음 변경
    # 때까지 이 패널이 조용히 멈춘다.
    setattr(pane, _SIG, sig)
    return text


def forget(pane):
    """캐시를 버린다(패널 재사용·세션 재시작). 다음 호출이 다시 보낸다."""
    for attr in (_SIG, _COUNT):
        if hasattr(pane, attr):
            delattr(pane, attr)
