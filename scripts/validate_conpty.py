"""직접 소유 ConPTY 백엔드(`PYTMUX_PTY_BACKEND=owned`) 라이브 검증 — §1.1② 돌파 레시피.

§1.1② 돌파 레시피(숨은 콘솔 + 동기 128KB 명명 파이프 + 블로킹 read)가 `conpty.py` 에
배선된 뒤(2026-06-12), 이 하네스가 그 경로를 라이브로 검증한다. owned 백엔드는 콘솔-less
프로세스에서도 `_ensure_hidden_console()`(AllocConsole/SetStdHandle)로 자식 attach 를
성립시키므로, 진단 프로세스가 자기 콘솔을 갖든(인터랙티브) 안 갖든(헤드리스) 동작한다.
이 스크립트로 [1][2][3] 가 PASS 함을 office 박스에서 확인(2026-06-12):
대화형 cmd 배너 157B 스트리밍 + 한글 echo 왕복 + 500KB CJK 플러드 U+FFFD 0.

실행:

    set PYTMUX_PTY_BACKEND=owned
    py scripts\\validate_conpty.py

⚠️ stdout 을 `-RedirectStandardOutput` 으로 리다이렉트하면 자식이 부모 콘솔을 붙잡아 attach
가 깨질 수 있다 — 리다이렉트 말고 `%TEMP%\\validate_conpty.out` 파일을 읽을 것. 비대화형
batch-writer 자식 잔여 갭(conpty.py docstring 참조)과 별개로, 제품 패널이 실제 돌리는
대화형 셸/Claude 는 여기 [1][2] 처럼 스트리밍된다.

검증 항목:
  1) 자식(cmd.exe)이 우리 의사 콘솔에 attach → 배너가 raw 바이트로 read 단 도달.
  2) 입력 왕복(echo 마커 + 한글).
  3) **멀티바이트 플러드 무손상**: 대량 CJK 출력을 read 경계에 걸쳐 받아도 U+FFFD 0개
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
        # 배너/프롬프트: 고정 sleep 대신 최대 5초 폴링(콜드스타트 cmd 는 1초 넘게 걸려,
        # 짧은 고정 sleep 은 핸드셰이크 23B 만 보고 거짓 FAIL 한다 — 2026-06-12).
        deadline = time.time() + 5
        while time.time() < deadline:
            if b">" in b"".join(chunks):
                break
            time.sleep(0.2)
        banner = b"".join(chunks)
        ok_attach = b"Microsoft" in banner or len(banner) > 40
        report("[1] attach/banner: %d bytes  -> %s"
               % (len(banner), "OK" if ok_attach else "FAIL"))

        # echo 왕복(한글 포함 — 입력단 raw write + 출력단 raw read 무손상). 최대 4초 폴링.
        chunks.clear()
        pty.write("echo MARKER_PYTMUX_OWNED_가나다\r\n".encode("utf-8"))
        deadline = time.time() + 4
        while time.time() < deadline:
            if "MARKER_PYTMUX_OWNED_가나다" in b"".join(chunks).decode("utf-8", "replace"):
                break
            time.sleep(0.2)
        echoed = b"".join(chunks).decode("utf-8", "replace")
        ok_echo = ("MARKER_PYTMUX_OWNED" in echoed and "가나다" in echoed
                   and echoed.count("�") == 0)
        report("[2] input round-trip(+한글): 가나다=%s fffd=%d -> %s"
               % ("가나다" in echoed, echoed.count("�"),
                  "OK" if ok_echo else "FAIL"))

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
