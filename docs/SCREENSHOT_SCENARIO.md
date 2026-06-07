# 매뉴얼 스크린샷 — 실제 화면 캡처 시나리오

> **상태**: ✅ 1차 구현됨(방식 ① Textual SVG). `scripts/gen_screenshots.py` 가 실제
> 클라이언트를 헤드리스로 운전해 SVG 로 떠 `docs/image/` 에 저장하고, `docs/MANUAL.md`
> 가 이를 싣는다. 본 문서는 그 설계 기준선이자 방법 조사 기록이다. 장면은 두 갈래다:
> ① **결정적 장면 17개**(API·네트워크 불필요) — 무인자 `gen_screenshots.py` 가 장면별
> 격리 서브프로세스로 전부 생성. ② **라이브 Claude 컷 5개**(11/12/13/20/22) — **진짜
> `claude` CLI 를 패널에서 한 세션 실행**해 처리중(◐)·응답완료·자동재개(AR)·권한모드
> 팝업·프롬프트 히스토리를 캡처한다(`claude_suite`). 실제 API 호출(인증·토큰)이라
> 결정적 기본 생성에선 제외하고 **`python3 scripts/gen_screenshots.py claude-suite`** 로만
> 돈다. 저장 시 `_redact_svg` 후처리가 (1) 계정 이메일·환영 배너 이름 등 PII 마스킹(공개
> 저장소 보호)과 (2) 한글 등 와이드 문자 **자간 보정**(`_fix_cjk_textlength`, §5.1)을
> 적용한다. 남은 일: 애니메이션 데모는 **VHS**, 회귀 `tests/test_shotgen.py`.

---

## 1. 배경과 목표

`docs/MANUAL.md` 는 UI 를 코드블록 안 ASCII 로 재현한다. 예:

```
├──────────────────────────────────────┐
│ ~/projects $ █          (활성: 파란 테두리)
└──────────────────────────────────────┘
```

ASCII 재현은 **버전관리 친화적·diff 가능·폰트 무관**이라는 장점이 있지만, 실제 색
(활성 패널 파란 테두리, 상태줄 textual-dark 팔레트, Claude 상태 아이콘 색 등)과 비례
폭·박스 글리프의 실제 렌더를 보여주지 못한다. 사용자가 "실제로 보는 그 화면" 을 그대로
싣는 것이 목표다.

**요구사항**

1. **재현 가능** — 누구나 같은 명령으로 같은 이미지를 다시 생성할 수 있어야 한다(수작업
   캡처 금지). 매뉴얼이 바뀌면 스크린샷도 스크립트로 재생성.
2. **헤드리스/CI 가능** — 실 디스플레이·실 TTY 없이도 생성 가능해야 GitHub Actions
   등에서 돌릴 수 있다(이 프로젝트의 테스트 철학과 동일 — `tests/run.py` 는 화면 없이 통과).
3. **실제 렌더 경로 통과** — 위젯 상태 단언이 아니라 **클라이언트가 실제로 그리는
   픽셀/벡터**를 잡아야 한다.
4. **GitHub 마크다운에서 보임** — 저장소 뷰어에서 바로 렌더되는 포맷(SVG/PNG).
5. **저장 비용 관리** — 바이너리(PNG) 비대화 주의. 가능하면 벡터(SVG, 텍스트 기반).

---

## 2. 지금 가진 캡처 자산

pytmux 에는 이미 세 가지 "화면 캡처" 경로가 있으나 **모두 텍스트**이고 픽셀/색을 담지
않는다. 실제 스크린샷으로 가려면 이들 위에 이미지화 단계를 얹거나, 네이티브 SVG 경로를
쓴다.

| 자산 | 무엇 | 출력 | 색/픽셀 | 헤드리스 |
|------|------|------|---------|----------|
| `.claude/skills/run-pytmux/driver.py` | 서버에 헤드리스로 붙어 서버가 합성한 패널을 텍스트로 합성 | 평문(박스 글리프) | ✗ | ✓ |
| `tests/ptyshot.py` | **진짜 클라(`client.py`)** 를 가짜 PTY 아래 띄워 실제 출력 캡처 | **ANSI 바이트**(색 이스케이프 포함) | △(ANSI 색 코드 보유) | ✓ |
| `tests/harness.py`(`run_test`/`server_only`) | 위젯/합성 셀 단언 | 셀 텍스트 | ✗ | ✓ |
| **Textual 네이티브** `App.save_screenshot()` | 앱 위젯 트리를 SVG 로 내보냄 | **SVG(벡터)** | **✓** | ✓ |

핵심 통찰:

- `ptyshot.py` 는 이미 **실제 색 이스케이프를 포함한 ANSI 프레임**을 손에 쥐어 준다. 여기에
  ANSI→이미지 변환기만 붙이면 진짜 스크린샷이 된다.
- pytmux 클라이언트는 서버가 보낸 pyte 화면을 자체 합성해 Textual 위젯으로 그린다. 따라서
  **클라이언트 위젯 트리의 SVG = 사용자가 보는 화면**이다. Textual 의 `save_screenshot`
  은 바로 그 위젯 트리를 SVG 로 떠 준다.

---

## 3. 방법 조사 (후보 A~E)

### A. Textual 네이티브 SVG 내보내기 ★ 1차 채택

Textual 은 `App.save_screenshot(path)` / `export_screenshot()` 로 현재 화면을 **SVG**
(터미널 창 크롬까지 포함한 벡터 이미지)로 떠 준다. pytmux 클라(`run_client`)를 Textual
**헤드리스 드라이버**로 띄워 원하는 상태까지 운전한 뒤 한 장씩 저장한다.

- **장점**: 진짜 클라 렌더 경로(텍스트 합성·CSS·테두리색·textual-dark 팔레트)를 그대로
  통과. 벡터라 가볍고 선명하며 **GitHub 마크다운에서 바로 렌더**. 폰트/박스 글리프가
  Textual 임베드 폰트로 일관. 외부 바이너리 의존 없음(텍스트만으로 SVG 생성).
- **단점**: 클라가 Textual 앱이어야 하므로 "서버 합성 텍스트" 인 driver.py 와 달리 **실
  클라 프로세스 + 서버**를 함께 띄워야 한다. 마우스 드래그 중간 상태(경계 드래그·패널
  swap 프리뷰)는 정적 캡처가 까다롭다(§6).
- **의존성**: 추가 없음(Textual 내장). SVG→PNG 가 필요하면 `cairosvg`/`rsvg-convert`.

배선 스케치:

```python
# scripts/gen_screenshots.py (제안) — 실 서버 + 헤드리스 실 클라 + SVG 저장
import asyncio, sys
sys.path.insert(0, ".claude/skills/run-pytmux")
from driver import PytmuxDriver          # 서버를 임시 소켓에 띄움
from pytmuxlib.client import build_client_app   # 순수 함수 모듈(최근 추출됨, CL #12)

async def shoot(state_fn, out_svg):
    drv = PytmuxDriver(cols=100, rows=30)
    await drv.start()                    # 격리 서버 기동
    app = build_client_app(endpoint=drv.endpoint, cfg=...)
    async with app.run_test(size=(100, 30)) as pilot:   # Textual 헤드리스
        await state_fn(pilot, drv)       # 분할·새 탭 등 원하는 상태로 운전
        await pilot.pause()
        app.save_screenshot(out_svg)     # ← 실제 화면 SVG
    await drv.stop()
```

> 주의: 클라가 서버 소켓에 attach 하는 부분과 Textual `run_test` 의 이벤트 루프를 한
> 프로세스에서 엮어야 한다. `pytmux.py` 의 `run_client` 가 인자로 endpoint/cfg 를 받게
> 정리돼 있고(`build_client_app` 순수함수 추출, CL #12 1/N), 이를 재사용하면 된다.

### B. ptyshot → ANSI → 이미지 (폴백)

`ptyshot.capture()` 가 주는 **실제 ANSI 프레임**을 이미지로 굽는다. 변환 후보:

- **`ansitoimg`**(pip) — ANSI 텍스트 → PNG/SVG 직접 변환. 파이프라인 단순.
- **charmbracelet `freeze`** — ANSI/텍스트를 받아 창 크롬 포함 PNG/SVG 렌더(고품질,
  둥근 모서리·그림자 등).
- **`aha`/`ansi2html` → 헤드리스 브라우저(Playwright)** — ANSI→HTML→PNG. 무겁지만 색
  재현 정확.

- **장점**: **진짜 클라 프로세스의 진짜 터미널 출력**(트레이스백·테두리·프롬프트 유무까지)
  을 캡처. driver 보다 더 "끝단" 픽셀에 가깝다.
- **단점**: 변환기가 박스 글리프·앰비규어스 폭·256색을 얼마나 충실히 재현하느냐에 좌우.
  외부 바이너리/패키지 의존. ANSI 파싱 변환기마다 미묘한 차이.
- **쓰임**: 패널 안에서 돌아가는 프로그램 출력(예: claude/top/vim) 처럼 Textual 위젯
  트리로는 잘 안 잡히는 "패널 내용 그 자체" 를 보일 때.

### C. VHS (charmbracelet/vhs) — 데모/애니메이션

`.tape` 스크립트로 **실제 터미널 에뮬레이터(내부적으로 ttyd+헤드리스 크로미움)** 에서
pytmux **TTY 클라(휴먼 패스)** 를 띄우고 키 입력을 흘리며 PNG 스크린샷이나 GIF 를 굽는다.

```tape
# manual-split.tape (제안)
Output docs/image/split.gif
Set FontSize 16
Set Width 1000
Set Height 600
Type "python3 pytmux.py"   Enter   Sleep 1s
Ctrl+b   Type "%"          Sleep 500ms      # 좌우 분할
Screenshot docs/image/02-split.png            # 정지 컷
Type "echo hello"          Enter   Sleep 500ms
```

- **장점**: **사용자가 실제로 보는 그대로**(실 폰트·실 색·커서). 정지 PNG 와 GIF 둘 다.
  README 히어로/기능 데모에 최적.
- **단점**: Go 바이너리(vhs)+ttyd+ffmpeg 설치 필요. TTY 휴먼 패스라 헤드리스 CI 에서
  추가 설정 필요(vhs 가 내부 처리). 결정성은 `Sleep` 타이밍에 의존.
- **주의**: pytmux 는 `$PYTMUX` 중첩을 거부 — VHS 의 깨끗한 셸에서 띄우면 무관. 단,
  데모 셸에 `$PYTMUX` 가 새지 않게.

### D. asciinema + agg — 녹화 GIF

세션을 `asciinema rec` 로 캐스트 녹화 후 `agg` 로 GIF 변환. 흐름 데모엔 좋으나 정적
문서 컷엔 과하다. C(VHS)와 용도 중복 — VHS 가 스크립트 결정성이 높아 우선.

### E. 실 터미널 + 수동 캡처 — 비채택

iTerm2/Terminal 에서 띄우고 macOS `screencapture -l<win>`. **재현 불가·CI 불가**라
원칙(§1-1,2)에 어긋나 비채택. 단, 1회성 홍보 이미지엔 가능.

---

## 4. 채택안 (정리)

| 용도 | 수단 | 산출물 | 위치 |
|------|------|--------|------|
| 매뉴얼 본문 정적 컷(패널/탭/분할/팝업/상태줄) | **A. Textual SVG** | `.svg` | `docs/image/` |
| 패널 내부 프로그램 출력(claude/top/vim 등) | **B. ptyshot→ansitoimg/freeze** | `.png`/`.svg` | `docs/image/` |
| README 히어로·기능 데모(움직임) | **C. VHS** | `.gif`/`.png` | `docs/image/` |

원칙: **벡터(SVG) 우선**, 정적 컷은 가능한 A 로. A 로 표현이 어려운 상태만 B/C.

---

## 5. 구현 시나리오 (단계)

1. **생성기 스크립트** `scripts/gen_screenshots.py` 추가(§3-A 스케치).
   - 입력: "장면(scene)" 목록 — 각 장면은 (이름, 운전 함수, 크기). 운전 함수는 driver
     control + 클라 pilot 키입력으로 목표 상태를 만든다.
   - 출력: `docs/image/<NN>-<name>.svg` 한 장씩. 결정적(고정 cols/rows, 고정 시계 텍스트
     주입 — 상태줄 시계는 `--clock 14:03` 같은 고정값으로 모킹).
2. **장면 카탈로그** — 매뉴얼의 ASCII 블록과 1:1 대응시킨다:
   - `01-first-run` 첫 실행(단일 패널+탭바+상태줄)
   - `02-split-h` 좌우 분할(활성 파란 테두리)
   - `03-split-nested` 중첩 분할(┬┴├┤ 경계)
   - `04-zoom` 줌 상태(상태줄 `Z`)
   - `05-confirm` 패널 닫기 확인 팝업
   - `06-tabbar-multi` 탭 여러 개(Claude `◐` 아이콘)
   - `07-scrollback` 스크롤백 모드(스크롤바)
   - `08-menu` prefix Enter 메뉴
   - `09-cmd-prompt` 명령 프롬프트(고스트 자동완성)
   - `10-claude-header` Claude 스티키 헤더
   - `11-perm-mode` 권한모드 팝업
   - `12-calendar` 달력 오버레이
   - `13-info-popup` 통합 정보 팝업
   - `14-degraded` 네트워크 degraded 빨간 외곽선
3. **매뉴얼 연동** — `docs/MANUAL.md` 의 각 ASCII 블록 **아래에** 이미지 참조를 추가하되,
   당분간 ASCII 도 **함께 유지**(폴백·diff·접근성). 예:
   ```markdown
   ![좌우 분할 — 활성 패널 파란 테두리](image/02-split-h.svg)
   ```
   완전 검증 후 ASCII 를 접거나 `<details>` 로 내려도 된다.
4. **재생성 절차 문서화** — `make screenshots` 또는 `python3 scripts/gen_screenshots.py`
   한 줄로 전체 재생성. 매뉴얼/UI 변경 시 재실행을 CONTRIBUTING 규칙에 추가.
5. **회귀** — `tests/test_shotgen.py` 로 생성기가 (a) 크래시 없이 N 장을 만들고 (b) 각
   SVG 가 비어있지 않으며 (c) 기대 마커(예: 활성색 hex, "분할" 후 패널 2개)를 포함하는지
   단언. 이미지 픽셀 diff 대신 **SVG 텍스트 내 마커 단언**으로 결정성 확보.

### 5.1 저장 후처리 파이프라인 (`_redact_svg`)

`app.save_screenshot(path)` 직후 모든 SVG 는 `_redact_svg(path)` 를 한 번 거친다(생성기의
두 경로 — 결정적 `_one_shot`, 라이브 `claude_suite` — 모두 동일). 한 번 읽어 두 가지 변환을
순서대로 적용하고 다시 쓴다:

1. **PII 마스킹** — 실 `claude` 화면의 로그인 이메일은 `user@example.com` 으로,
   "Welcome back &lt;이름&gt;!" 환영 배너는 "Welcome back!" 으로 치환. 공개 저장소에
   커밋되는 이미지 보호. textLength 가 그대로라 폭은 유지된다.
2. **한글 자간 보정** (`_fix_cjk_textlength`) — **Rich 의 `export_svg` 버그 교정**.
   Rich(`rich/console.py`)는 `<text>` 의 `textLength` 를 `cell_len` 이 아닌
   `len(글자수)` 로 계산한다(`textLength = char_width * len(text)`). 한글·한자·가나 등
   **와이드(2칸) 문자는 `len` 이 `cell_len` 의 절반**이라, 글자가 절반 폭으로 압축되어
   자간이 좁아지고 **글자가 겹쳐 보인다**. 셀당 px 폭(`char_width = textLength / len`)을
   역산해 `textLength` 를 `char_width * cell_len(text)` 로 다시 늘리면 모노스페이스
   그리드에 맞게 펼쳐진다. `x` 좌표는 Rich 가 이미 `cell_len` 기준으로 깔아 두므로
   건드리지 않는다. 와이드 문자가 없는 조각(`cell_len == len`)과 줄바꿈 등
   `cell_len == 0` 조각은 그대로 둔다.

> 이 후처리는 site-packages 의 Rich 를 직접 패치하지 않으려는 선택이다(재설치 시 소실·
> 버전관리 불가). 생성 파이프라인 끝단에서 SVG 텍스트만 보정하므로 Rich 업그레이드와
> 무관하게 동작하고, 이미 커밋된 SVG 도 `_fix_cjk_textlength` 만 따로 돌려 일괄 교정할 수
> 있다(텍스트 내용이 SVG 안에 그대로 들어 있어 재생성 불필요).

---

## 6. 주의점 / 미해결

- **마우스 중간 상태** — 경계 드래그·패널 swap 프리뷰(원본 흐림·대상 강조)는 "드래그
  중" 한 순간이라 정적 캡처가 까다롭다. Textual pilot 의 `pilot.hover`/`mouse_down`
  중간에 `save_screenshot` 을 끼우거나, 이 상태만 ASCII 로 남긴다.
- **시계/날짜 결정성** — 상태줄 시계·달력의 "오늘" 이 매번 달라지면 이미지가 흔들린다.
  생성기에서 **고정 시각을 주입**(테스트 훅 `cfg["__now"]` 등)해 결정적 출력.
- **클라+서버 동시 기동** — A 안은 한 프로세스에서 Textual `run_test` 이벤트 루프와 서버
  소켓 attach 를 함께 돌려야 한다. `build_client_app`(CL #12 추출)로 endpoint 주입이
  쉬워졌으나, 실제 배선·종료(서버 cleanup)는 검증 필요.
- **색/팔레트 일치** — SVG 의 색이 실제 터미널 테마와 다를 수 있다(SVG 는 textual-dark
  고정). 매뉴얼이 "기본 테마 기준" 임을 명시.
- **앰비규어스 폭·박스 글리프** — B(ANSI→이미지) 경로는 변환기 폰트에 따라 `┬┴├┤` 가
  깨질 수 있다. A(SVG) 가 이 점에서 안전. 단 A 도 **와이드 문자 자간**은 Rich 의
  `textLength` 버그로 좁아져 한글이 겹쳐 보였고, `_fix_cjk_textlength`(§5.1)로 교정됨.
- **저장소 비대화** — PNG/GIF 누적 주의. 정적 컷은 SVG(텍스트)로, GIF 는 꼭 필요한
  데모에만. `docs/image/` 용량을 주기적으로 점검.
- **Windows** — `ptyshot` 는 POSIX 전용(stdlib `pty`). Windows 화면은 A(SVG, OS 무관)
  로 생성하거나 별도 ConPTY 경로 필요.

---

## 7. 결론

- **지금 당장 가능한 최소 단계**: Textual `save_screenshot` 으로 정적 컷 몇 장(첫 실행·
  분할·메뉴·명령 프롬프트)을 SVG 로 떠 `docs/image/` 에 넣고 매뉴얼에 ASCII 와 병기.
- **이상적 완성형**: `scripts/gen_screenshots.py` + `tests/test_shotgen.py` 로 매뉴얼
  전 장면을 한 명령으로 재생성·회귀 검증하고, README 히어로/데모는 VHS GIF.
- ASCII 재현은 **삭제하지 않는다** — diff·접근성·폰트 무관·CI 텍스트 단언의 가치가 있어
  실제 스크린샷과 **상보적**으로 유지한다.

---

*관련: [MANUAL.md](MANUAL.md)(대상 문서) · `.claude/skills/run-pytmux/`(driver.py) ·
`tests/ptyshot.py`(ANSI 캡처) · [HANDOFF.md](HANDOFF.md) · [DESIGN.md](DESIGN.md).*
