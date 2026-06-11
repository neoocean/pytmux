# macOS에서 Windows 테스트 자동화 — 타당성 검토 + 시나리오

> 작성: 2026-06-05 · 대상: Apple Silicon macOS에서 개발하며 Windows 경로를 검증해야 하는 사람·에이전트
> 관련: [WINDOWS_PORT.md](WINDOWS_PORT.md) · [ENV_SETUP_WINDOWS.md](ENV_SETUP_WINDOWS.md) · [HANDOFF.md](HANDOFF.md)
> 질문: "macOS라 Windows 테스트가 어렵다. Docker로 Windows 컨테이너를 만들어 테스트할 수 있나?"

## 0. 결론 먼저 (TL;DR)

| 방법 | macOS(arm64)에서 가능? | Windows 코드 실제 검증? | 권장도 |
|---|---|---|---|
| **Docker Windows 컨테이너** | ❌ **불가** (Windows 커널 필요) | — | ✗ |
| **GitHub Actions (macOS/Linux/Windows 매트릭스)** | ✅ (클라우드 실행) | ✅ pywinpty·ConPTY 실제 + Linux 이식성 | ★★★ **권장** |
| Docker **Linux** 컨테이너 | ✅ | ❌ (Linux일 뿐) | △ 보조(POSIX 재현성) |
| Windows VM (UTM/Parallels) | △ arm64 제약 | ✅ 인터랙티브까지 | ★ 수동 재현용 |
| 실 Windows 박스(@office) ssh | ✅(이미 있음) | ✅ 전부 | ★★ 인터랙티브 버그용 |

**한 줄 답**: macOS에서 Docker Windows 컨테이너는 **원리적으로 불가능**하다. 대신
**GitHub Actions의 OS 매트릭스** 가 이 프로젝트에 가장 잘 맞는다 — 실제 Windows 커널
위에서 `pywinpty`를 설치하고, 헤드리스 스위트 + `scripts/win_report.py`를 그대로 돌려
리포트를 아티팩트로 남긴다.

> **CI 매트릭스에서 macOS 제거(2026-06-07)**: 현재 `os-compat` 워크플로는
> **`ubuntu` × `windows` × Python 3.11/3.12/3.13** 만 돈다. macOS 는 (a) **개발 박스가
> macOS** 라 로컬 `python tests/run.py`(322 passed)가 같은 커버리지를 줘 중복이고,
> (b) GitHub macOS 러너에서 **간헐적 PTY/서브프로세스 데드락**(헤드리스·tty 부재 환경
> 특이, 로컬·Linux·Windows 미재현)으로 잡이 17분씩 매달려 거짓-적색·러너 분 낭비만
> 냈다 — in-process 백스톱(SIGALRM·faulthandler)으로도 그 환경에선 신뢰성 있게 못 끊겼다.
> 워크플로 본래 목적(개발 박스가 못 보는 Windows 네이티브 + 깨끗한 Linux 검증)은
> ubuntu+windows 로 충족된다. macOS 특이 검증이 필요하면 로컬 실행 또는 `windows.yml`
> matrix 에 `macos-latest` 한시 재추가(주석 안내). 러너 견고성(UTF-8 출력·테스트별
> 타임아웃·faulthandler 행 덤프)은 유지된다.
>
> **macOS 재추가 + non-blocking 결론(2026-06-12)**: 사용자 요청으로 `macos-latest` 를
> 매트릭스에 도로 넣고 "실패 안 할 때까지" 수정을 시도했다. 진단 4회로 **인프라 레벨
> wedge** 임이 확정됐다 — 우리 코드/설정으론 못 고친다:
> 1. 스위트 **startup 에서 간헐적**으로 매달린다(3.11 이 한 번 3분에 통과 → 데드락이
>    아니라 경쟁/인프라 타이밍, 특정 테스트가 아님).
> 2. run.py 의 **startup faulthandler**(`dump_traceback_later(exit=True)`, 이번에 추가)·
>    SIGALRM 도 **안 끊긴다** → 파이썬 워치독 스레드조차 못 도는, 프로세스/스레드 **아래**
>    레벨 wedge.
> 3. GitHub 의 **step(8분)·job(12분) 타임아웃도 안 지켜진다**(17분까지 매달림) → GitHub
>    자신의 제어 아래로 wedge.
> 4. **`if: always()` 업로드 post-step 조차 안 돈다**(macOS 만 아티팩트 0개; ubuntu/windows
>    는 같은 `-X importtime` + `tee` 진단으로 정상 회수) → 콘솔·파일 어느 쪽으로도 행
>    지점을 못 건진다.
> `OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES`(macOS fork 데드락 표준 회피)도 효과 없었다.
> **`continue-on-error` 도 안 통한다**: wedge 한 잡을 GitHub 이 17분에 강제 **cancel** 하는데,
> continue-on-error 는 **fail** 한 잡만 구제하고 cancel 된 잡은 그대로 run 을 cancel(빨강)
> 시킨다. VM 자체가 wedge 라 in-VM 워치독(shell kill·python·timeout)으로 fail-fast 전환도
> 불가(러너 아래 레벨).
> → **결론**: PTY/서브프로세스 다수 스위트 + 헤드리스 macOS 러너의 인프라 flakiness 라
> **push CI 에서 green 으로 둘 방법이 없다**. macos-latest 를 매트릭스에서 **제외**해
> os-compat 을 green 으로 유지한다(ubuntu+windows blocking). macOS 검증은 로컬 개발 박스
> (`python tests/run.py`)가 권위. 한시 재확인이 필요하면 matrix 에 `macos-latest` 를 도로
> 넣어 보되 wedge 시 run 이 cancel(빨강) 됨을 감안. run.py startup faulthandler 백스톱은 유지.

---

## 1. 왜 Docker Windows 컨테이너가 macOS에서 안 되는가

컨테이너는 **호스트 커널을 공유**한다(VM과 다른 점). 따라서:

- **Windows 컨테이너**(`mcr.microsoft.com/windows/nanoserver`, `.../servercore`)는
  **Windows 커널**을 요구한다 — Windows 호스트에서만, process 격리 또는 Hyper-V 격리로 뜬다.
- **macOS의 Docker Desktop**은 내부적으로 **경량 Linux VM(LinuxKit)** 을 띄우고 그 안에서
  Linux 컨테이너만 실행한다. Windows 커널이 없으므로 `docker run windows/...` 는
  `no matching manifest for linux/arm64` 또는 OS 불일치로 **반드시 실패**한다.
- Apple Silicon(arm64)이면 한 겹 더 막힌다: Windows 컨테이너 이미지는 amd64/Windows이고,
  설령 Windows 호스트라도 arm64 Windows 컨테이너 생태계는 빈약하다.

> 요컨대 "Docker로 Windows 컨테이너"는 **Windows 호스트가 있어야** 성립하는 말이고,
> macOS에는 Windows 커널이 없어 시작점 자체가 없다.

### 1-b. 설령 Windows 호스트가 있어도 컨테이너는 이 프로젝트에 부적합

pytmux의 진짜 Windows 통증(§HANDOFF §10 ②: **Windows→ssh→원격 macOS 반응성 급락**)은
**인터랙티브 콘솔 + ConPTY + ssh 전송**이 얽힌 **동적 행위**다. 헤드리스 Windows 컨테이너
(nanoserver/servercore)는 ① 대화형 의사콘솔(ConPTY) 지원이 제한적이고 ② `pywinpty`가
컨테이너에서 안정적으로 ConPTY를 잡는다는 보장이 없으며 ③ 실제 ssh 왕복·터미널 키
인코딩을 재현하지 못한다. **즉 컨테이너로는 그 버그를 못 잡는다** — 그건 실 Windows
박스(또는 VM)에서 사람이 인터랙티브로 봐야 한다.

**컨테이너가 검증할 수 있는 건 "헤드리스로 충분한 것"뿐인데, 그건 아래에서 보듯 이미
macOS에서도 mock으로 대부분 통과한다.** 그래서 컨테이너의 추가 가치가 낮다.

---

## 2. 무엇이 "진짜 Windows"를 필요로 하는가 (코드 기준)

조사 결과(코드 확인), Windows 분기는 많지만 **대부분 mock으로 macOS에서 이미 검증**된다.
실제 Windows 커널이 있어야만 새로 확인되는 건 좁다.

### 2-a. macOS에서 이미 검증됨 (mock/가드, 실 Windows 불필요)
- 플랫폼 분기 로직: `pty_backend.IS_WINDOWS`, `proc.shell_argv/spawn_detached/is_alive/terminate`,
  `ipc.default_state_dir/default_endpoint`, `server` shell 선택, `client` 클립보드 분기 —
  `tests/test_windows_port.py`가 `IS_WINDOWS`/`os.name`을 patch해 양쪽 분기를 검증.
- `fcntl`/`termios` 부재 시 import 안전성: `_BlockImport` 컨텍스트로 모듈 차단 후 `protocol`
  import 성공 확인.
- `_WinPty` 백프레셔 게이트(`_resume_evt` pause/resume) **순수 로직**: `__new__`로 winpty
  spawn을 우회해 Event 상태만 검증(`test_winpty_backpressure_gate`).
- 헤드리스 스위트(`tests/run.py`)·성능 측정(`poc/feed_profile.py`): pyte 가짜 화면·합성
  데이터만 써서 **실 PTY/터미널 불필요** → 어느 OS에서나 동일.

### 2-b. 실제 Windows 커널에서만 새로 검증되는 것 (CI의 가치)
- **`pip install pywinpty`가 실제로 되는지** + import 성공 (x86-64 Windows 휠).
- **`_WinPty`가 진짜 ConPTY로 cmd.exe를 spawn**하고 read/write/resize/terminate가 동작하는지
  (`pty_backend.py:305-432`, `from winpty import PtyProcess`는 `__init__` 지연 import).
- `proc.py`의 Windows 실경로: `cmd /c`, `CREATE_NO_WINDOW`, `tasklist`, `taskkill /T`.
- `ipc.py` TCP 엔드포인트(`tcp:127.0.0.1:0`)가 Windows에서 실제 바인드/연결되는지.
- Windows 경로·인코딩(`%LOCALAPPDATA%\pytmux`, UTF-8 변환) 실동작.

> 핵심: **CI(windows-latest)의 실익은 2-b 한 묶음** — 특히 "pywinpty가 깨지지 않고 설치·
> import되고 ConPTY를 잡는다"는 회귀를, macOS 개발 중에는 절대 못 보는 것을 자동으로 본다.

---

## 3. 권장 시나리오 — GitHub Actions CI (macOS/Linux/Windows 3-OS 매트릭스)

이 저장소는 **Perforce 주(主) + GitHub 미러(`neoocean/pytmux`)** 다. Actions는 **미러에
푸시된 커밋**에 대해 돈다(미러가 depot보다 늦을 수 있음 — 그래도 주기적 검증엔 충분).

OS × Python 매트릭스로 **Linux x86-64 / macOS arm64 / Windows Server** 9칸을 한 번에 돈다.
`pywinpty` 는 `requirements.txt` 의 `sys_platform=="win32"` 마커로 Linux/macOS 에선 pip 이
**자동 스킵**하므로 설치 실패가 없고, ConPTY 스모크 스텝만 `if: runner.os == 'Windows'` 로
가둔다(비-Windows 엔 `winpty` 모듈이 없으므로).

### 3-a. 워크플로 (`.github/workflows/windows.yml`, `name: os-compat`)

```yaml
name: os-compat

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:        # 수동 트리거(Actions 탭에서 버튼)

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]   # Linux / macOS arm64 / Win Server
        python: ["3.11", "3.12", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}

      - name: Install deps         # pywinpty 는 win32 마커로 Linux/macOS 자동 스킵
        run: |
          python -m pip install --upgrade pip
          python -m pip install -r requirements.txt

      - name: Smoke — pywinpty imports + ConPTY spawns (Windows only)
        if: runner.os == 'Windows'
        run: python -c "from winpty import PtyProcess; p=PtyProcess.spawn('cmd /c echo ok'); print('pywinpty OK')"

      - name: Headless test suite
        run: python tests/run.py

      - name: Compat + perf report
        run: python scripts/win_report.py --mb 5

      - name: Upload report artifact
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: report-${{ matrix.os }}-py${{ matrix.python }}
          path: reports/win-report-*.md
          if-no-files-found: warn
```

### 3-b. 이 워크플로가 잡는 것
- **Windows**: `pywinpty` 설치·import·ConPTY spawn 회귀(macOS에선 절대 못 보는 것).
- **Linux**: macOS-특유 가정이 POSIX 일반에서 깨지는지(이식성).
- **ubuntu × windows × 3 Python**(macOS 는 매트릭스에서 제외, 위 §0 노트) 에서 헤드리스
  스위트(현재 로컬 322 passed)·`win_report.py` 통과 여부.
- 각 칸의 **호환성·성능 Markdown 리포트**를 `report-<os>-py<ver>` 아티팩트로 남겨 비교.

### 3-c. 한계(CI가 못 잡는 것)
- 인터랙티브 라이브 attach(새 콘솔 창), 실제 ssh 왕복 반응성(§10 ②), 터미널 키 인코딩
  (Shift+ESC·마우스 휠 1007) — 이건 **실 Windows 박스/VM에서 사람이** 봐야 한다(§4).

### 3-d. CI가 처음 적발한 실버그 (검증 사례)
도입 첫 실행에서 **Windows 전용 버그 1건을 즉시 잡았다** — macOS 개발 중엔 보이지 않던 것:
- **증상**: Windows 잡의 "Compat + perf report" 스텝이 `UnicodeEncodeError: 'charmap' codec
  can't encode`(position 0-2 = "리포트")로 exit 1. pywinpty/ConPTY/헤드리스 스위트는 모두 ✓.
- **원인**: `win_report.py` 의 요약 `print()`(한글·박스글리프)가 Windows 기본 stdout
  인코딩(cp1252)에 막힘. 파일 쓰기는 이미 utf-8 이라 무사했고 **stdout 만** 문제.
- **수정**: `main()` 진입부에서 `sys.stdout.reconfigure(encoding="utf-8")`(실패 무해 가드).
- **교훈**: Windows CI 의 가치 그대로 — "macOS에선 안 보이는 인코딩/경로/콘솔 차이"를 자동 적발.
  이후 모든 Windows 출력은 utf-8 stdout 을 전제로 한다.

---

## 4. 보조 수단 (인터랙티브·수동 재현용)

CI로 안 잡히는 인터랙티브 버그를 봐야 할 때만.

### 4-a. 이미 가진 실 Windows 박스 (@office/@surface) — 가장 실용적
[ENV_SETUP_WINDOWS.md](ENV_SETUP_WINDOWS.md) 그 박스. ssh로 들어가
`py scripts/win_report.py` 한 줄로 리포트를 만들고, 필요하면 라이브 attach로 반응성을
직접 본다. **새 인프라 0** — macOS에서 자동화하고 싶은 것의 80%는 사실 여기서 충분.

> **REC(패널 출력 캡처)로 실 Claude Code 출력 수집(2026-06-06~, 기본 ON).** CL 57060
> 이후 capture 기본값이 ON 이라 Windows 박스에서 pytmux 를 새로 띄우면 자동으로 모든
> 패널의 raw PTY 출력을 `captures/<sock-id>/pane-<id>.log` 에 무손실 기록한다(매핑은
> `sessions.log`). 실 Claude limit/busy/idle/ctx 화면을 떠서 `python pytmux.py replay
> <log> --cols N --rows M [--ruler]` 로 복원 → M8 골든 픽스처·M14c 모델힌트·§3.2 감지
> 정확도 작업의 객관 근거로 쓴다(현재 이 항목들은 실 캡처에 차단돼 있다). **유의**:
> ① 그 박스 `opts.json` 에 과거 `capture:false` 가 영속돼 있으면 기본 ON 이 안 먹으니
> `python pytmux.py cmd capture-output on` 1회. ② `captures/` 는 `.gitignore` 로 GitHub
> 미러 차단(민감 화면 유출 방지), `.p4ignore` 비차단(머신 간 Perforce 공유용) — 캡처
> 데이터 submit 은 실 터미널 내용(비밀·경로 포함 가능)이라 검토 후. ③ 무손실이라 무한
> 증가 — 분석 후 불필요 로그 정리.

### 4-b. Windows VM (UTM 또는 Parallels) — arm64 제약 주의
- Apple Silicon이라 **Windows 11 ARM64**만 자연스럽게 뜬다(x86-64 Windows는 에뮬레이션→느림).
- **함정**: `pywinpty`(Rust 확장)의 **ARM64 Windows 사전 빌드 휠이 없을 수 있다** → VM 안에서
  Rust 툴체인으로 빌드해야 할 수도. 이 마찰 때문에 "그냥 테스트 환경"으로는 무겁다.
- 인터랙티브 ConPTY/콘솔 창 행위를 로컬에서 꼭 봐야 할 때만 권장.

### 4-c. Docker **Linux** 컨테이너 — Windows 아님, 보조 용도만
macOS의 Docker로 띄울 수 있는 건 Linux뿐. Windows 검증은 못 하지만,
**깨끗한 POSIX 환경에서 헤드리스 스위트 재현성**(macOS 특유 동작 배제)·`win_report.py`의
Linux 기준선 확보엔 쓸 수 있다. Windows 통증과는 무관하므로 "있으면 좋은" 정도.

### 4-d. 직접 소유 ConPTY 백엔드(§1.1② raw 바이트) 라이브 검증 — 실 인터랙티브 박스
`PYTMUX_PTY_BACKEND=owned` 으로 켜는 직접 소유 ConPTY(`pytmuxlib/conpty.py`)는 **raw 바이트**
경로라 멀티바이트(CJK/이모지) 경계 손상(§1.1②)을 구조적으로 없앤다. 그러나 **ConPTY 자식
출력은 실 콘솔에서만 왕복**한다 — NonInteractive 도구/리다이렉트 콘솔에선 pywinpty 포함 어떤
백엔드도 자식 출력이 안 온다(conhost init 23B 만; 메모리 `conpty-io-needs-real-console`).
따라서 **실 인터랙티브 PowerShell/터미널**에서 검증한다.

**(a) 자동 검증 스크립트** — `scripts/validate_conpty.py`:
```powershell
$env:PYTMUX_PTY_BACKEND = "owned"
py scripts\validate_conpty.py        # [1]attach [2]echo 왕복 [3]CJK 플러드 U+FFFD=0 → VERDICT
```
헤드리스 도구에서 돌릴 때는 자기 콘솔을 줘야 한다(자식이 부모 콘솔을 붙잡는 것 방지).
**`-RedirectStandardOutput` 은 쓰지 말 것** — 리다이렉트하면 새 콘솔이 안 생겨 attach 가
깨진다. 스크립트가 `%TEMP%\validate_conpty.out` 에 결과를 함께 남기므로 그 파일을 읽는다:
```powershell
$p = Start-Process py -ArgumentList 'scripts\validate_conpty.py' -WindowStyle Hidden -PassThru
$p.WaitForExit(20000) | Out-Null
Get-Content "$env:TEMP\validate_conpty.out" -Encoding utf8     # [1][2][3] + VERDICT
```

**(b) 실 제품 라이브** — 가장 확실한 회귀 확인:
```powershell
$env:PYTMUX_PTY_BACKEND = "owned"
py pytmux.py                          # attach 후: 한글 입력·붙여넣기, CJK 대량 출력 명령 실행
```
확인 포인트: ① 한글/이모지가 `�` 없이 정확히, ② Windows→ssh→원격 Claude CJK 핫패스에서
플러드 시 손상 0, ③ 응답성·리사이즈·종료(고아 셸 없음) 정상. 문제 시 env 를 지우면 즉시
검증된 pywinpty 로 폴백한다. 라이브에서 안정 확인되면 기본 백엔드를 owned 로 전환하는 후속 CL.

> **헤드리스 단위 커버리지**(`tests/test_pty_backend.py`): 선택 분기(`test_spawn_selects_backend`)·
> env 블록(`test_build_env_block`)·지원 판정·**Windows 수명**(`test_owned_conpty_lifecycle_windows`
> — spawn→resize→terminate→close→reap, I/O 없이). 바이트 왕복·플러드는 위 라이브 항목으로만
> 검증 가능(실 콘솔 필요).

---

## 5. 추천 액션 순서

1. **(지금)** `.github/workflows/windows.yml` 추가 → GitHub 미러에 푸시 → Actions가 매
   푸시마다 Windows에서 pywinpty/ConPTY/헤드리스/리포트를 자동 검증. **macOS를 안 떠나고
   Windows 회귀를 본다.**
2. **(필요 시)** 인터랙티브 반응성 버그(§10 ②)는 @office 박스에서 `win_report.py` +
   라이브 attach로 수동 확인, 결과 리포트를 depot에 커밋(`reports/`는 .gitignore라 본문만).
3. VM은 정말 로컬 인터랙티브가 필요할 때만(arm64+pywinpty 빌드 각오).

> **Docker Windows 컨테이너는 선택지에서 제외** — macOS에서 불가능하고, 가능한 환경에서도
> 이 프로젝트의 인터랙티브 통증을 재현하지 못한다.
