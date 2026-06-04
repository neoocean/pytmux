#!/usr/bin/env python3
"""pytmux 드라이버 — 실행 중인 pytmux 를 프로그램으로 운전한다.

pytmux 는 셸 PTY 를 소유하는 백그라운드 서버(데몬)와 화면을 그리는 Textual
클라이언트가 유닉스 도메인 소켓으로 붙는 구조다. 이 드라이버는 **클라이언트 자리에
헤드리스로 붙어**(TTY/tmux 불필요) 실 서버에 입력을 보내고, 서버가 그려 보내는
패널 화면을 받아 합성해 텍스트 "스크린샷" 으로 떨군다. tmux 가 없어도, GUI 없이도
'실제로 돌아가는 앱'을 운전하고 관찰할 수 있다.

PR 이 주로 건드리는 계층(server.py/model.py/client.py/pty_backend.py)을 그대로
운동시킨다: 진짜 PTY·진짜 셸·진짜 서버측 pyte 렌더.

CLI:
    python3 driver.py smoke [--out DIR]   # 분할·명령·새 탭까지 end-to-end + 스크린샷
    python3 driver.py shot  [--cmd CMD] [--out FILE]  # 한 명령 실행 후 스크린샷 1장

라이브러리:
    d = PytmuxDriver(cols=100, rows=30)
    await d.start()
    await d.control("split-window -h")
    await d.send("echo hello\n")
    await d.refresh()
    print(d.screen_text())
    d.screenshot("/tmp/pytmux-shots/x.txt")
    await d.stop()
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
import tempfile

# pytmux 패키지(이 스킬은 <unit>/.claude/skills/run-pytmux/ 에 있으므로 상위 3단계가 unit).
_UNIT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, _UNIT)
from pytmuxlib import ipc, proc                         # noqa: E402
from pytmuxlib.protocol import read_msg, write_msg      # noqa: E402

SHOT_DIR = "/tmp/pytmux-shots"


class PytmuxDriver:
    def __init__(self, cols: int = 100, rows: int = 30, endpoint: str | None = None):
        self.cols = cols
        self.rows = rows
        self.endpoint = endpoint or tempfile.mktemp(suffix=".sock")
        self.server_pid = None
        self.reader = None
        self.writer = None
        self.panes: dict[int, list] = {}     # pane id -> 최신 rows
        self.layout: dict | None = None      # 최신 layout 메시지

    async def start(self, ready_timeout: float = 6.0):
        """서버를 데몬으로 띄우고(부모와 무관하게 생존), 붙어서 hello 한 뒤 초기
        레이아웃/화면을 받는다."""
        self.server_pid = proc.spawn_detached(proc.server_argv(self.endpoint))
        loop = asyncio.get_event_loop()
        end = loop.time() + ready_timeout
        while loop.time() < end and not ipc.probe(self.endpoint):
            await asyncio.sleep(0.02)
        if not ipc.probe(self.endpoint):
            raise RuntimeError("pytmux 서버 기동 실패")
        self.reader, self.writer = await ipc.open_connection(self.endpoint)
        await write_msg(self.writer, {"t": "hello", "cols": self.cols,
                                      "rows": self.rows})
        await self.refresh(1.0)

    async def send(self, text: str):
        """활성 패널에 키 입력을 보낸다(개행 포함 그대로)."""
        await write_msg(self.writer, {
            "t": "input", "data": base64.b64encode(text.encode()).decode()})

    async def control(self, line: str) -> str:
        """tmux 스타일 제어 명령을 별도 연결로 보낸다(예: 'split-window -h',
        'new-window', 'rename-window foo'). 결과 문자열 반환."""
        r, w = await ipc.open_connection(self.endpoint)
        await write_msg(w, {"t": "control", "line": line})
        reply = await asyncio.wait_for(read_msg(r), timeout=3.0)
        w.close()
        return (reply or {}).get("result", "")

    async def scroll_top(self, pane: int | None = None):
        pid = pane if pane is not None else (self.layout or {}).get("active")
        await write_msg(self.writer, {"t": "scroll", "pane": pid, "top": True})

    async def refresh(self, secs: float = 1.2):
        """secs 동안 서버가 보내는 screen/layout 메시지를 모아 상태를 최신화."""
        loop = asyncio.get_event_loop()
        end = loop.time() + secs
        while loop.time() < end:
            try:
                msg = await asyncio.wait_for(
                    read_msg(self.reader), timeout=max(0.05, end - loop.time()))
            except asyncio.TimeoutError:
                break
            if msg is None:
                break
            t = msg.get("t")
            if t == "layout":
                self.layout = msg
            elif t == "screen":
                self.panes[msg["pane"]] = msg.get("rows", [])

    @staticmethod
    def _row_text(row, w):
        s = "".join(seg[0] for seg in row)
        return (s + " " * w)[:w]

    def screen_text(self) -> str:
        """layout 의 패널 rect 에 각 패널 화면을 합성해 전체 화면을 텍스트로 만든다
        (테두리 박스 포함). 실제 pytmux 클라이언트가 그리는 화면의 텍스트 근사."""
        if not self.layout:
            return "(no layout)"
        cols, rows = self.layout["cols"], self.layout["rows"]
        grid = [[" "] * cols for _ in range(rows)]

        def put(x, y, ch):
            if 0 <= y < rows and 0 <= x < cols:
                grid[y][x] = ch

        # 테두리 박스
        for p in self.layout.get("panes", []):
            box = p.get("box")
            if not box:
                continue
            bx, by, bw, bh = box
            for i in range(bw):
                put(bx + i, by, "─"); put(bx + i, by + bh - 1, "─")
            for j in range(bh):
                put(bx, by + j, "│"); put(bx + bw - 1, by + j, "│")
            put(bx, by, "┌"); put(bx + bw - 1, by, "┐")
            put(bx, by + bh - 1, "└"); put(bx + bw - 1, by + bh - 1, "┘")
            title = (p.get("title") or "").strip()
            if title:
                for i, c in enumerate(f" {title} "[:max(0, bw - 2)]):
                    put(bx + 1 + i, by, c)
        # 패널 내용
        for p in self.layout.get("panes", []):
            pid, cx, cy, cw, ch = p["id"], p["x"], p["y"], p["w"], p["h"]
            for ry, row in enumerate(self.panes.get(pid, [])[:ch]):
                line = self._row_text(row, cw)
                for rx, c in enumerate(line):
                    put(cx + rx, cy + ry, c)
        return "\n".join("".join(r).rstrip() for r in grid)

    def screenshot(self, path: str) -> str:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.screen_text() + "\n")
        return path

    async def stop(self):
        try:
            r, w = await ipc.open_connection(self.endpoint)
            await write_msg(w, {"t": "kill-server"})
            await asyncio.sleep(0.2)
            w.close()
        except Exception:
            pass
        if self.server_pid:
            proc.terminate(self.server_pid, force=True)
        if not ipc.is_tcp(self.endpoint):
            try:
                os.unlink(self.endpoint)
            except OSError:
                pass


async def smoke(out_dir: str) -> int:
    os.makedirs(out_dir, exist_ok=True)
    d = PytmuxDriver(cols=100, rows=30)
    await d.start()
    print(f"[smoke] 서버 기동 pid={d.server_pid} endpoint={d.endpoint}")
    # 1) 단일 패널에 명령
    await d.send("echo 'hello from pytmux' && uname -a\n")
    await d.refresh(1.5)
    s1 = d.screenshot(os.path.join(out_dir, "01-single.txt"))
    # 2) 좌우 분할 후 새 패널에 명령
    print("[smoke] split-window -h →", await d.control("split-window -h"))
    await d.refresh(1.0)
    await d.send("ls -1 | head\n")
    await d.refresh(1.5)
    s2 = d.screenshot(os.path.join(out_dir, "02-split.txt"))
    # 3) 새 탭(윈도우)
    print("[smoke] new-window →", await d.control("new-window"))
    await d.refresh(1.0)
    await d.send("echo 'second tab'\n")
    await d.refresh(1.2)
    s3 = d.screenshot(os.path.join(out_dir, "03-newtab.txt"))
    await d.stop()
    print(f"[smoke] 스크린샷: {s1}\n             {s2}\n             {s3}")
    print("\n===== 02-split.txt =====")
    print(open(s2).read())
    ok = "hello from pytmux" in open(s1).read()
    print("✅ PASS" if ok else "❌ FAIL: 패널 출력 미검출")
    return 0 if ok else 1


async def shot(cmd: str, out: str) -> int:
    d = PytmuxDriver(cols=100, rows=30)
    await d.start()
    await d.send(cmd if cmd.endswith("\n") else cmd + "\n")
    await d.refresh(1.5)
    path = d.screenshot(out)
    await d.stop()
    print(open(path).read())
    print(f"[shot] → {path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="pytmux headless driver")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_s = sub.add_parser("smoke", help="분할·명령·새 탭 end-to-end + 스크린샷")
    p_s.add_argument("--out", default=SHOT_DIR)
    p_h = sub.add_parser("shot", help="한 명령 실행 후 스크린샷 1장")
    p_h.add_argument("--cmd", default="echo 'hello from pytmux'")
    p_h.add_argument("--out", default=os.path.join(SHOT_DIR, "shot.txt"))
    args = ap.parse_args(argv)
    if args.cmd == "smoke":
        return asyncio.run(smoke(args.out))
    return asyncio.run(shot(args.cmd, args.out))


if __name__ == "__main__":
    sys.exit(main())
