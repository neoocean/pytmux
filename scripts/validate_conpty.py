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

**호스트 A/B**(pytmux/pytmux-208 · 2026-08-22): `--dll system` / `--dll bundled` 로 어느
ConPTY 호스트를 띄울지 고른다(안 주면 지금 기본 = 시스템 conhost). 두 회차를 나란히
돌려 [1][2][3] 이 둘 다 PASS 인지, 그리고 [4] «폭 2 글자 겹침»이 어느 쪽에서만 나오는지
본다 — 후자가 그 이슈의 자리를 «호스트 안/밖»으로 가른다.

    py scripts\\validate_conpty.py --dll bundled   > %TEMP%\\ab-bundled.txt
    py scripts\\validate_conpty.py --dll system    > %TEMP%\\ab-system.txt

⚠ 라우팅은 `pytmuxlib.conpty` **import 시점**에 굳는다(HPCON 은 그 DLL 내부 상태라 뒤에
못 바꾼다) — 그래서 이 스크립트는 env 를 import 보다 먼저 세운다. 한 프로세스에서 두
호스트를 번갈아 재는 길은 없다.

⚠️ stdout 을 `-RedirectStandardOutput` 으로 리다이렉트하면 자식이 부모 콘솔을 붙잡아 attach
가 깨질 수 있다 — 리다이렉트 말고 `%TEMP%\\validate_conpty.out` 파일을 읽을 것. 비대화형
batch-writer 자식 잔여 갭(conpty.py docstring 참조)과 별개로, 제품 패널이 실제 돌리는
대화형 셸/Claude 는 여기 [1][2] 처럼 스트리밍된다.

검증 항목:
  1) 자식(cmd.exe)이 우리 의사 콘솔에 attach → 배너가 raw 바이트로 read 단 도달.
  2) 입력 왕복(echo 마커 + 한글).
  3) **멀티바이트 플러드 무손상**: 대량 CJK 출력을 read 경계에 걸쳐 받아도 U+FFFD 0개
     (raw 바이트 → incremental decoder carry; winpty-rs per-chunk 디코드 손상 회피).
  4) **폭 2 글자 이중 방출**(pytmux/pytmux-208 · advisory): 제보된 한글 줄을 echo 해
     돌아온 바이트에 `조조` 같은 겹침이 있나. ⛔ **VERDICT 에 안 넣는다** — 겹침 없음이
     「고쳤다」가 아니기 때문이다(제보 경로는 Claude Code 의 콘솔 API 재페인트다).
     값은 «났을 때» 있다: bundled 에서만 나면 자리는 그 호스트다.
"""
import codecs
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PYTMUX_PTY_BACKEND"] = "owned"

# `--dll <값>` 은 **import 전에** 세운다(위 머리말 ⚠ — 라우팅이 import 시점에 굳는다).
# 값을 그대로 넘기고 «무엇을 뜻하나»는 conpty.conpty_dll_pref() 한 곳이 정한다.
if "--dll" in sys.argv:
    _i = sys.argv.index("--dll")
    if _i + 1 < len(sys.argv):
        os.environ["PYTMUX_CONPTY_DLL"] = sys.argv[_i + 1]

from pytmuxlib import conpty, pty_backend  # noqa: E402

# 제보된 그 줄(pytmux/pytmux-208) — 폭 2 글자가 **원래 잇달아 두 번 오는 자리가 없다**.
# 그래서 여기서 나온 연속 중복은 전부 「호스트가 두 번 냈다」의 증거다.
_HANGUL_LINE = "이 Claude는 조직 보안 정책에 의해 관리됩니다."

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

    # ★ 어느 호스트로 쟀나 — 이 줄이 없으면 A/B 두 회차를 나중에 구별할 수 없다.
    # «고른 것»(pref)과 «실제로 쓴 것»(SOURCE)을 따로 찍는다: bundled 를 골라도 DLL 이
    # 없으면 system 으로 떨어지는데, 그때 둘이 갈린다.
    report("[0] ConPTY host: %s (고른 것=%s · PYTMUX_CONPTY_DLL=%r)"
           % (getattr(conpty, "CONPTY_DLL_SOURCE", "?"), conpty.conpty_dll_pref(),
              os.environ.get("PYTMUX_CONPTY_DLL")))

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

        # [4] 폭 2 글자 «이중 방출»(pytmux/pytmux-208) — 제보된 그 줄을 그대로 echo 해서
        # 호스트가 돌려준 raw 바이트에 `조조` 같은 겹침이 있는지 센다. chcp 65001 은 [3]
        # 에서 이미 걸었다.
        # ⛔ **초록을 「고쳤다」로 읽지 마라** — 제보 경로는 Claude Code 가 콘솔 API 로
        # 그린 화면을 호스트가 VT 로 재방출하는 자리라, 이 echo 왕복에서는 안 날 수 있다.
        # 이 줄의 값은 «났을 때»다: 겹침이 bundled 에서만 나오면 자리는 그 호스트다.
        chunks.clear()
        pty.write(("echo %s\r\n" % _HANGUL_LINE).encode("utf-8"))
        deadline = time.time() + 4
        while time.time() < deadline:
            if "관리됩니다" in b"".join(chunks).decode("utf-8", "replace"):
                break
            time.sleep(0.2)
        seen = b"".join(chunks).decode("utf-8", "replace")
        dup = conpty.doubled_wide_chars(_HANGUL_LINE, seen)
        report("[4] 폭2 이중 방출(pytmux-208): 겹친 글자 %d개%s  -> %s (advisory)"
               % (len(dup), (" [%s]" % "".join(dup)) if dup else "",
                  "겹침 없음" if not dup else "겹침 재현"))

        # [5] **리페인트 재방출**(pytmux/pytmux-208) — [4] 가 답을 «안» 준 이유를 겨눈다.
        #
        # ☠ 2026-08-23 실측(GHA 32578439033): [4] 는 번들·시스템 두 호스트 × 파이썬 셋 =
        #   **여섯 회차 전부** 「겹친 글자 0개」였다. 그러니 [4] 만으로는 A/B 가 두 호스트를
        #   **가르지 못한다.** 그 결과는 [4] 자신의 주석이 미리 적어 둔 그대로다 — 제보 경로는
        #   호스트가 **자기 텍스트 버퍼를 훑어 VT 로 다시 뱉는** 자리인데, echo 왕복은 글자가
        #   들어온 «그대로» 지나가므로 그 자리를 한 번도 안 지난다.
        #
        # 그 자리를 지나게 하는 가장 싼 자극이 **리사이즈**다: 폭이 바뀌면 호스트는 버퍼를
        # 칸 단위로 다시 훑어 화면을 재구성해 내보낸다. 폭 2 글자의 **뒤 칸**을 건너뛰지
        # 않으면 정확히 제보 모양(`조조직직`)이 그 순간 나온다. 제보의 *"공백도 한 번 늘었다 ·
        # 줄바꿈/재그리기 경계"* 도 같은 곳을 가리킨다.
        #
        # ⛔ **0 을 「겹침 없음」으로만 적지 않는다** — 호스트가 아무것도 다시 안 뱉었을 때도
        #   0 이다. 그 둘을 `conpty.reemit_verdict` 가 셋으로 가른다(「못 쟀다」가 따로 있다).
        # ⛔ 이 스텝은 **VERDICT 에 안 든다**(advisory) — 그리고 무슨 일이 나도 여기서 죽지
        #   않는다. 죽으면 아래 VERDICT 줄이 통째로 안 찍혀 회차 전체가 판정 불능이 된다.
        try:
            chunks.clear()
            pty.write(("echo %s\r\n" % _HANGUL_LINE).encode("utf-8"))
            deadline = time.time() + 4
            while time.time() < deadline:
                if "관리됩니다" in b"".join(chunks).decode("utf-8", "replace"):
                    break
                time.sleep(0.2)
            # 여기서부터가 «재방출»이다 — 앞의 echo 응답은 세지 않는다.
            chunks.clear()
            # ☠ **이름도 인자 순서도 여기서 한 번 틀렸다**(2026-08-23 실측 · GHA
            #   32640590840·32637285906 여섯 장 전부):
            #     [5] … 못 쟀다 — AttributeError("'_OwnedConPty' object has no
            #                                     attribute 'resize'")
            #   `_OwnedConPty` 에 `resize` 는 **없다** — 있는 것은 `set_winsize` 이고
            #   그 인자는 **(rows, cols)** 라 `resize(cols, rows)` 와 **뒤집혀 있다**
            #   (`pty_backend.set_winsize` 가 안에서 다시 `cp.resize(cols, rows)` 로
            #   되뒤집으며 「주의: (cols, rows) 순서」라고 적어 둔 그 자리다).
            #   ⛔ 그래서 `except Exception` 이 이것을 삼켜 두 호스트 × 파이썬 셋이
            #      **한 번도 재방출을 안 자극한 채** 「못 쟀다」로만 나왔다. 그 갈래가
            #      「겹침 없음」과 구별되게 지어 둔 덕에 거짓 초록은 아니었지만,
            #      **[5] 는 배선된 날부터 지금까지 한 번도 안 돌았다.**
            #   ⇒ 폭만 200 → 120 으로 줄인다(행은 그대로 50 — 자극은 «폭»이다).
            pty.set_winsize(50, 120)
            deadline = time.time() + 4
            while time.time() < deadline:
                if "관리됩니다" in b"".join(chunks).decode("utf-8", "replace"):
                    break
                time.sleep(0.2)
            seen2 = b"".join(chunks).decode("utf-8", "replace")
            state, dup2 = conpty.reemit_verdict(_HANGUL_LINE, seen2)
            word = {"doubled": "겹침 재현", "clean": "겹침 없음",
                    "unmeasured": "재방출을 못 받았다 — 못 쟀다(초록이 아니다)"}[state]
            report("[5] 리페인트 재방출(pytmux-208): %d bytes · 겹친 글자 %d개%s  -> %s"
                   " (advisory)"
                   % (len(seen2), len(dup2),
                      (" [%s]" % "".join(dup2)) if dup2 else "", word))
            pty.set_winsize(50, 200)   # 되돌린다 (rows, cols)
        except Exception as exc:                      # noqa: BLE001 (진단 스텝)
            report("[5] 리페인트 재방출(pytmux-208): 못 쟀다 — %r (advisory)" % (exc,))

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
