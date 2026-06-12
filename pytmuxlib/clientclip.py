"""OS 클립보드 입출력(텍스트·이미지) — 앱 상태 비의존 순수 함수(#12 추출).

client.py 의 거대 클로저(build_client_app)에 갇혀 있던 클립보드 static 헬퍼를
모듈 자유함수로 빼냈다(docs/HANDOFF §11.4-4 / IMPROVEMENT #12). 플랫폼 도구
(pbcopy/xclip/wl-*/clip.exe/PowerShell/pngpaste)를 best-effort 로 호출하고, 도구가
없거나 실패하면 조용히 폴백값(False/""/None)을 돌려 호출부(클라)가 기존대로 동작한다.
원격(ssh) 환경에서 이미지 파일 경로는 클라이언트 머신 기준이라는 주의는 호출부 참조."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from . import proc


def copy(text: str) -> bool:
    """OS 클립보드로 복사(pbcopy/xclip/wl-copy/clip.exe). 성공 True."""
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
    """OS 클립보드 텍스트를 읽어 반환(없으면 "")."""
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
