"""claude-prompt-history 서버 측 — 프롬프트 추적·status 직렬화·스크롤백 점프.

textual 무관. 입력 바이트 스트림에서 (멀티라인) 프롬프트를 누적하고 Enter(\\r)에 제출로
확정해 패널별 `_ph_history` 에 쌓는다. 점프는 코어 model.py 의 앵커/hist_total 에 의존하지
않고, 제출된 프롬프트 텍스트를 **스크롤백에서 검색**해 그 줄을 뷰포트 맨 위로 올리는
`pane.scroll` 오프셋을 계산한다(회전으로 사라졌으면 graceful 실패).

Claude 패널 판정은 claude-code 가 세우는 `pane._claude` 를 getattr 로 약하게 읽는다 —
claude-code 가 없으면 아무 프롬프트도 기록하지 않는다(하드 참조 금지)."""
from __future__ import annotations

_HIST_CAP = 200           # 패널당 보관 상한
_TAIL = 30                # status 에 싣고 팝업이 다루는 최근 슬라이스(번호↔인덱스 정렬)
_INBUF_CAP = 4000         # 멀티라인 프롬프트 누적 버퍼 상한


def _is_claude(pane) -> bool:
    return bool(getattr(pane, "_claude", None))


def track_input(pane, data: bytes):
    r"""입력 바이트에서 현재 (멀티라인) 프롬프트를 누적하고 제출(Enter=\r)에 한 항목으로
    확정한다. CSI/ESC 시퀀스(화살표 등)는 건너뛰고, \n(Shift+Enter)은 줄바꿈으로 누적만
    한다(멀티라인=한 항목). Claude 패널만 기록한다."""
    if not _is_claude(pane):
        return
    text = data.decode("utf-8", "ignore")
    buf = getattr(pane, "_ph_inbuf", "")
    hist = getattr(pane, "_ph_history", None)
    if hist is None:
        hist = pane._ph_history = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\x1b":                 # ESC: 제어 시퀀스 건너뜀
            i += 1
            if i < n and text[i] == "[":
                i += 1
                while i < n and not (0x40 <= ord(text[i]) <= 0x7e):
                    i += 1
                i += 1
            else:
                i += 1
            continue
        if ch == "\n":                   # Shift+Enter/Ctrl+J: 프롬프트 안 줄바꿈(누적)
            buf += "\n"
        elif ch == "\r":                 # Enter: 제출 경계 → 누적분을 한 항목으로 확정
            line = buf.strip()
            if line and (not hist or hist[-1] != line):   # 연속 중복 제외
                hist.append(line)
                if len(hist) > _HIST_CAP:
                    del hist[:-_HIST_CAP]
            buf = ""
        elif ord(ch) in (8, 127):        # backspace
            buf = buf[:-1]
        elif ord(ch) >= 32:
            buf += ch
        i += 1
    pane._ph_inbuf = buf[-_INBUF_CAP:]


def status_fields(server, win, msg, full):
    """status 메시지에 미리보기 행수와 패널별 프롬프트 히스토리(tail)를 싣는다. 히스토리는
    드물게 바뀌므로 **변할 때만**(또는 full resync) 실어 ssh 트래픽을 아낀다(_ph_sent
    디바운스). full 은 _ph_sent 를 건드리지 않는다(주기 flush 의 공유 디바운스 비오염)."""
    msg["ph_max_lines"] = getattr(server, "_ph_max_lines", 3)
    entries = []
    for p in (win.panes() if win else ()):
        if not _is_claude(p):
            continue
        tail = list(getattr(p, "_ph_history", []))[-_TAIL:]
        e = {"id": p.id}
        if full or tail != getattr(p, "_ph_sent", None):
            e["h"] = tail
            if not full:
                p._ph_sent = tail
        entries.append(e)
    msg["ph_panes"] = entries


def _plain_line(line, cols) -> str:
    """pyte 줄을 평문 문자열로(와이드 문자 연속 빈 셀 건너뜀, 뒤 공백 제거)."""
    out = []
    for x in range(cols):
        d = line[x].data
        if d == "":          # 와이드 문자(CJK/이모지) 연속 셀
            continue
        out.append(d if d else " ")
    return "".join(out).rstrip()


def scroll_to_prompt(server, sess, index: int) -> bool:
    """활성 패널을 팝업의 `index`(0 기반 = tail 슬라이스 인덱스) 프롬프트가 입력된 스크롤백
    위치로 점프한다. 앵커 없이 **스크롤백 텍스트 검색**으로: 프롬프트 첫 줄을 포함하는 줄을
    아래(최근)에서부터 찾아, 그 줄이 뷰포트 맨 위에 오도록 pane.scroll 을 맞춘다.

    뷰포트 수학(model.render): full = hist+buffer, top 줄 = full[total - scroll - lines].
    따라서 full[idx] 를 맨 위로 → scroll = total - lines - idx, [0, len(hist)] 로 클램프.
    못 찾으면(회전/재시작) False."""
    win = sess.active_window
    if not win or not win.active_pane:
        return False
    p = win.active_pane
    tail = list(getattr(p, "_ph_history", []))[-_TAIL:]
    if not (0 <= index < len(tail)):
        return False
    prompt = tail[index]
    first = next((ln.strip() for ln in prompt.splitlines() if ln.strip()), "")
    if not first:
        return False
    screen = getattr(p, "screen", None)
    if screen is None:
        return False
    h = getattr(screen, "history", None)
    hist = list(h.top) if h is not None else []
    lines_n = screen.lines
    cols = screen.columns
    full = hist + [screen.buffer[y] for y in range(lines_n)]
    total = len(full)
    target = None
    for j in range(total - 1, -1, -1):       # 아래(최근)에서 위로 — 가장 최근 등장
        if first in _plain_line(full[j], cols):
            target = j
            break
    if target is None:
        return False
    scroll = total - lines_n - target
    p.scroll = max(0, min(scroll, len(hist)))
    p.dirty = True
    return True
