"""OS 클립보드 입출력(텍스트·이미지) — 앱 상태 비의존 순수 함수(#12 추출).

client.py 의 거대 클로저(build_client_app)에 갇혀 있던 클립보드 static 헬퍼를
모듈 자유함수로 빼냈다(docs/internal/HANDOFF §11.4-4 / IMPROVEMENT #12). 플랫폼 도구
(pbcopy/xclip/wl-*/clip.exe/PowerShell/pngpaste)를 best-effort 로 호출하고, 도구가
없거나 실패하면 조용히 폴백값(False/""/None)을 돌려 호출부(클라)가 기존대로 동작한다.
원격(ssh) 환경에서 이미지 파일 경로는 클라이언트 머신 기준이라는 주의는 호출부 참조."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile

from . import proc


# Windows 유니코드-안전 클립보드 왕복(코드페이지 무관):
# clip.exe 는 stdin 을, Get-Clipboard 는 stdout 을 **콘솔 코드페이지**(한국어
# Windows=cp949)로 해석한다. 그래서 UTF-8 바이트를 넘기면 한글이 mojibake 가 됐다
# (사용자 보고 2026-07-13: 마우스 드래그 복사한 '그림자 샤미' → '洹몃┝???ㅻ?').
# 텍스트를 UTF-16LE→base64(ASCII)로 감싸 PowerShell 과 주고받는다 — base64 는 순수
# ASCII 라 cp949·cp437·cp1252 등 어떤 콘솔 코드페이지로 (역)해석돼도 <128 바이트가
# 동형이라 무손실이다. PowerShell 이 되돌려 Set-Clipboard/Get-Clipboard 한다.
def _win_copy_stdin(text: str) -> bytes:
    """_win_copy 가 PowerShell stdin 으로 보낼 payload(UTF-16LE→base64, ASCII).
    errors='replace': 짝 없는 서로게이트(vt 파서 잔재 등)에 UnicodeEncodeError 로
    UI 경로에 예외가 새지 않게(검수 L-1) — 유효 텍스트엔 무손실."""
    return base64.b64encode(text.encode("utf-16le", errors="replace"))


def _win_paste_from_stdout(out: bytes) -> str:
    """_win_paste 가 PowerShell stdout(base64 ASCII)을 원문으로 복원. 비면 ""."""
    s = out.decode("ascii", "ignore").strip()
    if not s:
        return ""
    try:
        return base64.b64decode(s).decode("utf-16le", "ignore")
    except (ValueError, UnicodeError):
        return ""


def _win_copy(text: str) -> bool:
    """Windows: PowerShell Set-Clipboard 로 유니코드-안전 복사. 성공 True.
    PowerShell 이 없거나 실패하면 False → 호출부가 clip.exe 폴백(ASCII 만 정확)."""
    if not shutil.which("powershell"):
        return False
    # $b(base64) 를 UTF-16LE 원문으로 되돌려 Set-Clipboard. Set-Clipboard 는 자체적으로
    # STA 로 마샬링하므로 -STA 불요. [Console]::In 은 ASCII base64 만 읽어 코드페이지 무관.
    #
    # 검수 M-2: ⓐ Set-Clipboard 실패(다른 앱이 클립보드를 쥐고 있는 흔한 상황)는
    # **비종결 오류**라 그냥 두면 powershell 이 exit 0 → 여기서 True 를 돌려 '복사됨'
    # 이라 거짓보고하고 clip.exe 폴백도 건너뛴다. -ErrorAction Stop+try/catch{exit 1}
    # 로 실패를 종결코드로 드러내 폴백이 돌게 한다. ⓑ capture_output 없으면 PS 오류
    # 텍스트가 클라 단말(Textual 화면)에 그대로 찍혀 UI 를 깨뜨린다 → 캡처해 삼킨다.
    ps = ("$b=[Console]::In.ReadToEnd();"
          "try{Set-Clipboard -Value ([Text.Encoding]::Unicode.GetString("
          "[Convert]::FromBase64String($b))) -ErrorAction Stop}catch{exit 1}")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            input=_win_copy_stdin(text), timeout=5, capture_output=True,
            **proc.no_window_kwargs())
        return r.returncode == 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def _win_paste():
    """Windows: PowerShell Get-Clipboard 로 유니코드-안전 붙여넣기(_win_copy 의 역).
    문자열|"" 반환, PowerShell 이 없으면 None(호출부가 폴백 루프로)."""
    if not shutil.which("powershell"):
        return None
    # -Raw: 여러 줄을 배열로 쪼개지 않고 원문 그대로. base64(ASCII)로 stdout 해 코드페이지
    # 무관. 클립보드가 비었거나 텍스트가 아니면 $t=$null → 빈 출력 → "".
    ps = ("$t=Get-Clipboard -Raw;"
          "if($null -ne $t){[Convert]::ToBase64String("
          "[Text.Encoding]::Unicode.GetBytes($t))}")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=5, **proc.no_window_kwargs())
    except (OSError, subprocess.SubprocessError):
        return None
    # 검수 M-3: PowerShell 자체가 실패(returncode≠0)하면 stdout 은 비지만 그건 '클립보드
    # 가 비었다'는 권위가 아니다. ""(빈 클립보드)로 돌리면 paste() 가 그걸 확정으로 보고
    # 폴백 루프(pbpaste/clip 등)를 건너뛰어 Ctrl+V 가 조용히 무동작한다. None 을 돌려
    # 폴백으로 라우팅한다(빈 클립보드는 returncode 0+빈 stdout → "" 로 정상 구분).
    if r.returncode != 0:
        return None
    return _win_paste_from_stdout(r.stdout)


def copy(text: str) -> bool:
    """OS 클립보드로 복사(pbcopy/xclip/wl-copy/clip.exe). 성공 True.
    Windows 는 코드페이지 무관 유니코드 복사(_win_copy)를 먼저 시도한다."""
    if proc.IS_WINDOWS and _win_copy(text):
        return True
    for cmd in (["pbcopy"], ["xclip", "-selection", "clipboard"],
                ["wl-copy"], ["clip"]):   # clip = Windows clip.exe(표준입력 복사)
        if shutil.which(cmd[0]):
            try:
                # no_window_kwargs: Windows 에서 clip.exe 콘솔 창 안 뜨게(§10)
                subprocess.run(cmd, input=text.encode("utf-8"), timeout=2,
                               **proc.no_window_kwargs())
                return True
            except (OSError, subprocess.SubprocessError):
                pass
    return False


def paste() -> str:
    """OS 클립보드 텍스트를 읽어 반환(없으면 "").
    Windows 는 코드페이지 무관 유니코드 붙여넣기(_win_paste)를 먼저 시도한다."""
    if proc.IS_WINDOWS:
        w = _win_paste()
        if w is not None:
            return w
    for cmd in (["pbpaste"], ["xclip", "-selection", "clipboard", "-o"],
                ["wl-paste", "-n"],
                # Windows: PowerShell Get-Clipboard(끝에 CRLF 가 붙을 수 있음)
                ["powershell", "-NoProfile", "-Command", "Get-Clipboard"]):
        if shutil.which(cmd[0]):
            try:
                # no_window_kwargs: Windows 에서 PowerShell Get-Clipboard 창이
                # 번쩍이지 않게 한다(§10 사용자 보고: 딸려 뜨는 PowerShell 창).
                return subprocess.run(
                    cmd, capture_output=True, timeout=2,
                    **proc.no_window_kwargs()
                ).stdout.decode("utf-8", "ignore")
            except (OSError, subprocess.SubprocessError):
                pass
    return ""


def has_image() -> bool:
    """OS 클립보드에 (텍스트가 아닌) 이미지가 들어 있으면 True.

    윈도우는 PowerShell ``Get-Clipboard -Format Image`` 로 확인하고, mac/linux 는
    가능한 도구로 best-effort 확인한다(도구가 없으면 False → 기존 동작 유지). 어떤
    예외든 조용히 False 로 떨어진다."""
    try:
        if proc.IS_WINDOWS:
            # -Sta: 클립보드 접근은 STA 스레드를 요구할 수 있다(STA 강제).
            # no_window_kwargs: 딸려 뜨는 PowerShell 창 방지(§10).
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Sta", "-Command",
                 "if (Get-Clipboard -Format Image) { 'IMG' }"],
                capture_output=True, timeout=3,
                **proc.no_window_kwargs()
            ).stdout.decode("utf-8", "ignore")
            return "IMG" in out
        if shutil.which("osascript"):   # macOS
            out = subprocess.run(
                ["osascript", "-e", "clipboard info"],
                capture_output=True, timeout=3
            ).stdout.decode("utf-8", "ignore")
            return any(t in out for t in ("PNGf", "TIFF", "GIFf"))
        if shutil.which("xclip"):       # Linux/X11
            out = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
                capture_output=True, timeout=3
            ).stdout.decode("utf-8", "ignore")
            return "image/" in out
        if shutil.which("wl-paste"):    # Linux/Wayland
            out = subprocess.run(
                ["wl-paste", "--list-types"],
                capture_output=True, timeout=3
            ).stdout.decode("utf-8", "ignore")
            return "image/" in out
    except (OSError, subprocess.SubprocessError):
        pass
    return False


def save_image() -> str | None:
    """OS 클립보드의 이미지를 임시 PNG 파일로 저장하고 그 경로를 반환한다
    (실패/이미지 없음 → None). §10-A #11 결정 ①(경로 문자열 주입)용.

    - Windows: .NET `System.Windows.Forms.Clipboard.GetImage()` → `.Save(png)`.
    - macOS: `pngpaste <path>`.
    - Linux/X11: `xclip -selection clipboard -t image/png -o`.
    - Linux/Wayland: `wl-paste --type image/png`.

    주의(로컬 가정): 저장 파일은 **클라이언트 머신**에 생긴다. 클라이언트와
    서버(PTY 자식 앱)가 같은 머신일 때만 경로가 유효하다 — 원격(ssh) 환경은
    호출부가 Alt+V(공유 클립보드 직접 읽기)로 폴백한다."""
    try:
        fd, path = tempfile.mkstemp(prefix="pytmux-clip-", suffix=".png")
        os.close(fd)
    except OSError:
        return None
    ok = False
    try:
        if proc.IS_WINDOWS:
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "Add-Type -AssemblyName System.Drawing;"
                "$img=[System.Windows.Forms.Clipboard]::GetImage();"
                "if ($img) { $img.Save('" + path.replace("'", "''") +
                "', [System.Drawing.Imaging.ImageFormat]::Png); 'OK' }"
            )
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Sta", "-Command", ps],
                capture_output=True, timeout=8, **proc.no_window_kwargs()
            ).stdout.decode("utf-8", "ignore")
            ok = "OK" in out
        elif shutil.which("pngpaste"):           # macOS (설치 시 우선 — 빠름)
            ok = subprocess.run(["pngpaste", path], capture_output=True,
                                timeout=8).returncode == 0
        elif shutil.which("osascript"):          # macOS (pngpaste 없이 기본 도구로)
            # 서드파티 pngpaste 가 없을 때(맥 기본 설치 아님 — 사용자 보고: 이미지
            # 붙여넣기 무동작) AppleScript 로 클립보드 PNG(«class PNGf»)를 직접 파일로
            # 쓴다. 스크린샷 클립보드는 PNGf 표현을 포함하므로 의존성 없이 동작한다.
            # PNGf 표현이 없으면(드묾) osascript 가 에러 → ok=False 로 폴백(호출부 Alt+V).
            ap = path.replace("\\", "\\\\").replace('"', '\\"')
            scr = ['set p to POSIX file "%s"' % ap,
                   "set d to (the clipboard as «class PNGf»)",
                   "set f to open for access p with write permission",
                   "set eof f to 0", "write d to f", "close access f"]
            args = ["osascript"]
            for line in scr:
                args += ["-e", line]
            ok = subprocess.run(args, capture_output=True,
                                timeout=8).returncode == 0
        elif shutil.which("xclip"):              # Linux/X11
            with open(path, "wb") as f:
                ok = subprocess.run(
                    ["xclip", "-selection", "clipboard",
                     "-t", "image/png", "-o"],
                    stdout=f, timeout=8).returncode == 0
        elif shutil.which("wl-paste"):           # Linux/Wayland
            with open(path, "wb") as f:
                ok = subprocess.run(
                    ["wl-paste", "--type", "image/png"],
                    stdout=f, timeout=8).returncode == 0
        if ok and os.path.exists(path) and os.path.getsize(path) > 0:
            return path
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        os.remove(path)
    except OSError:
        pass
    return None


def scp_to_remote(host: str, local_path: str, remote_path: str) -> bool:
    """로컬 파일을 scp 로 원격 호스트에 복사한다. 성공 True.

    remote-attach 가 이미 ssh 키 인증을 통과한 호스트이므로 BatchMode 로 동작한다.
    host 가 SSH config alias 를 포함한 임의 문자열일 수 있어 셸 인젝션을 피하기 위해
    argv 형으로 전달한다."""
    if not host or host.startswith("-") or any(c.isspace() for c in host):
        return False
    try:
        dest = f"{host}:{remote_path}"
        r = subprocess.run(
            ["scp", "-B", "-q", "-o", "BatchMode=yes",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
             "--", local_path, dest],
            capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
