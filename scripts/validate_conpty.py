"""직접 소유 ConPTY 백엔드(`PYTMUX_PTY_BACKEND=owned`) 라이브 검증 — 실 인터랙티브 박스용.

§1.1② 멀티바이트 손상 해결을 **실 콘솔**에서 확인한다. 헤드리스 도구/리다이렉트 콘솔에선
ConPTY 자식 출력이 왕복하지 않으므로(메모리: conpty-io-needs-real-console) **반드시 실
인터랙티브 PowerShell/터미널에서** 직접 실행해야 한다:

    set PYTMUX_PTY_BACKEND=owned
    py scripts\\validate_conpty.py

(헤드리스에서 돌리려면 자기 콘솔을 줘야 한다: PowerShell 에서
 `Start-Process py -ArgumentList 'scripts\\validate_conpty.py' -WindowStyle Hidden` 후
 종료 코드/표준출력 확인.)

검증 항목:
  1) 자식(cmd.exe)이 우리 의사 콘솔에 attach → 배너가 raw 바이트로 read 단 도달.
  2) 입력 왕복(echo 마커).
  3) **멀티바이트 플러드 무손상**: 대량 CJK 출력을 64KB read 경계에 걸쳐 받아도 U+FFFD 0개
     (raw 바이트 → incremental decoder carry; winpty-rs per-chunk 디코드 손상 회피).
"""
import codecs
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PYTMUX_PTY_BACKEND"] = "owned"

from pytmuxlib import conpty, pty_backend  # noqa: E402

# 결과를 stdout + 파일(%TEMP%\validate_conpty.out)에 동시에 남긴다. Start-Process
# -WindowStyle Hidden(자기 콘솔 필요)로 띄우면 stdout 이 안 보이므로 파일로 확인.
# 주의: stdout 을 -RedirectStandardOutput 으로 리다이렉트하면 자식이 부모 콘솔을 붙잡아
# attach 가 깨진다 — 리다이렉트 말고 이 파일을 읽을 것.
_REPORT = os.path.join(os.environ.get("TEMP", "."), "validate_conpty.out")
_logf = open(_REPORT, "w", encoding="utf-8")


def report(msg=""):
    print(msg)
    try:
        _logf.write(str(msg) + "\n"); _logf.flush()
    except OSError:
        pass


def _collect(pty, seconds):
    """리더를 직접 돌려(이벤트 루프 없이) raw 바이트를 모은다."""
    chunks = []
    dec = codecs.getincrementaldecoder("utf-8")()
    inc = []
    cp = pty._cp

    stop = threading.Event()

    def loop():
        while not stop.is_set():
            try:
                data = cp.read(65536)
            except OSError:
                break
            if not data:
                break
            chunks.append(data)
            inc.append(dec.decode(data))

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return chunks, inc, stop


def main():
    if not pty_backend.IS_WINDOWS:
        report("SKIP: Windows 전용"); return 0
    if not conpty.conpty_supported():
        report("FAIL: ConPTY 미지원 OS"); return 1

    pty = pty_backend.spawn(["cmd.exe"], cols=200, rows=50, cwd=None,
                            env=dict(os.environ))
    if type(pty).__name__ != "_OwnedConPty":
        report("FAIL: owned 백엔드 미선택 (got %s) — 폴백했거나 env 미설정"
               % type(pty).__name__)
        pty.close(); return 1
    chunks, inc, stop = _collect(pty, 0)
    try:
        time.sleep(1.2)
        banner = b"".join(chunks)
        ok_attach = b"Microsoft" in banner or len(banner) > 40
        report("[1] attach/banner: %d bytes  -> %s"
               % (len(banner), "OK" if ok_attach else "FAIL"))

        chunks.clear()
        pty.write(b"echo MARKER_PYTMUX_OWNED\r\n")
        time.sleep(0.8)
        echoed = b"".join(chunks)
        ok_echo = b"MARKER_PYTMUX_OWNED" in echoed
        report("[2] input round-trip: %s" % ("OK" if ok_echo else "FAIL"))

        # 멀티바이트 플러드: chcp 65001 후 python 자식이 raw UTF-8 CJK 대량 출력.
        chunks.clear()
        pty.write(b"chcp 65001\r\n")
        time.sleep(0.6)
        chunks.clear()
        code = ("import sys;sys.stdout.buffer.write(('"
                "\\uac00\\ub098\\ub2e4\\ub77c\\ub9c8\\ubc14\\uc0ac\\uc544'"
                "*20000).encode('utf-8'));sys.stdout.buffer.flush()")
        pty.write(('python -c "%s"\r\n' % code).encode("utf-8"))
        time.sleep(4.0)
        data = b"".join(chunks)
        txt = data.decode("utf-8", "replace")
        inc_txt = "".join(inc[-len(chunks):]) if chunks else ""
        fffd = txt.count("�")
        cjk = txt.count("가")
        ok_flood = fffd == 0 and cjk > 0
        report("[3] CJK flood: raw=%d bytes  CJK(가)=%d  U+FFFD=%d  -> %s"
               % (len(data), cjk, fffd, "OK" if ok_flood else "FAIL"))
        report("    (incremental-decoder path U+FFFD=%d)" % inc_txt.count("�"))

        all_ok = ok_attach and ok_echo and ok_flood
        report("\nVERDICT: %s" % ("PASS" if all_ok else "FAIL"))
        return 0 if all_ok else 1
    finally:
        stop.set()
        pty.write(b"exit\r\n")
        time.sleep(0.3)
        pty.stop_reader()
        pty.close()


if __name__ == "__main__":
    sys.exit(main())
