# pytmux 패널 안 Claude CLI — Shift+방향키 텍스트 선택·편집 검증

> 작성: 2026-06-05 · 갱신: 2026-06-08(복사·붙여넣기 가능성 §5 추가) · 대상: §10-A #5
> 관련: [HANDOFF.md](HANDOFF.md)
> `pytmuxlib/clientutil.py`(`SPECIAL`/`key_to_bytes`) · `pytmuxlib/client.py`(`on_key`)
> · `pytmuxlib/clientclip.py`(OS 클립보드) · `pytmuxlib/clientwidgets.py`(copy-mode 선택)

## 1. 결론(요약)

질문: **Claude 프롬프트에서 Shift+Home/End/방향키로 텍스트를 블록으로 잡아
삭제·복사·붙여넣기 할 수 있나?** 동작을 세 층으로 나눠 본다.

| 동작 | 가능성 | 누가 보장하나 |
|------|--------|---------------|
| **선택**(Shift+나브) | 조건부 | pytmux 전달 ✅ · 단말의 Shift 인코딩 + 앱의 선택 지원에 의존 |
| **삭제**(선택 후 Del/타이핑) | 조건부 | 위 선택이 되면 앱이 처리(앱 의존) |
| **붙여넣기**(Ctrl/Cmd+V) | ✅ 가능 | pytmux 가 OS 클립보드를 읽어 bracketed paste(§5) |
| **복사**(선택→시스템 클립보드) | ❌ Shift 선택으로는 불가 → ✅ **copy-mode/마우스 선택**으로 (§5) |

- **pytmux 책임(키 전달)**: ✅ **검증 완료.** pytmux 는 `Shift+Home`/`Shift+End`/
  `Shift+←→↑↓` 를 **표준 xterm 수정자 시퀀스(`CSI 1;2 X`)** 로 활성 패널(앱)에 그대로
  전달한다. 정상 모드에서 이 키들을 pytmux 단축키로 가로채지 않는다.
- **앱 책임(시퀀스 해석)**: 실제로 "선택 영역이 생기고 Del/타이핑으로 지워지는지"는
  **패널 안에서 도는 앱(Claude Code CLI 등)이 그 시퀀스를 어떻게 해석하느냐**에 달려
  있다. pytmux 는 시퀀스를 손실 없이 전달할 뿐, 선택/편집 동작 자체를 구현하지 않는다.
- **복사의 핵심 한계**: 앱 안의 "내부 선택"은 화면에 보일 뿐 **시스템 클립보드와 무관**
  하다. 앱이 선택분을 클립보드로 보내려면 `OSC 52` 시퀀스를 써야 하는데, **pytmux 는
  OSC 52 를 중계하지 않는다**(서버 pyte 가 소비). 따라서 Shift 선택으로 "복사"는
  pytmux 경유로는 성립하지 않고, **pytmux 자체 copy-mode(스크롤 모드 드래그 선택)** 나
  호스트 터미널의 마우스 선택으로 복사해야 한다(§5).
- 즉 **전달 경로는 pytmux 가 보장**하고(헤드리스 회귀로 고정), **선택·삭제의 실효는
  앱 버전에 의존**하며, **붙여넣기는 pytmux 가, 복사는 copy-mode 가 보장**한다.

## 2. pytmux 가 보내는 바이트 시퀀스

`pytmuxlib/clientutil.py` 의 `SPECIAL` 매핑(수정자 포함 커서 키, xterm 표준):

| 키 | 전송 바이트 | 의미 |
|----|-------------|------|
| `Shift+Left`  | `ESC [ 1 ; 2 D` (`\x1b[1;2D`) | 왼쪽으로 선택 확장 |
| `Shift+Right` | `ESC [ 1 ; 2 C` (`\x1b[1;2C`) | 오른쪽으로 선택 확장 |
| `Shift+Up`    | `ESC [ 1 ; 2 A` (`\x1b[1;2A`) | 위로 선택 확장 |
| `Shift+Down`  | `ESC [ 1 ; 2 B` (`\x1b[1;2B`) | 아래로 선택 확장 |
| `Shift+Home`  | `ESC [ 1 ; 2 H` (`\x1b[1;2H`) | 줄 시작까지 선택 |
| `Shift+End`   | `ESC [ 1 ; 2 F` (`\x1b[1;2F`) | 줄 끝까지 선택 |
| `Delete`      | `ESC [ 3 ~`     (`\x1b[3~`)   | 선택/문자 삭제 |
| `Ctrl+Home`/`Ctrl+End` | `\x1b[1;5H`/`\x1b[1;5F` | 문서 처음/끝 |

- `1;2` 의 `2` 가 **Shift 수정자**(xterm modifier 코드: 1=none, 2=Shift, 5=Ctrl).
  많은 라인 에디터·TUI(예: readline, Ink 기반 입력)가 이 표준 시퀀스를 인식한다.
- 보조: `Shift+Enter`=`LF`(`\n`, 줄 제출과 구분되는 줄바꿈), `Shift+Tab`=`CSI Z`
  (backtab — Claude 권한모드 순환), `Shift+Esc`=`ESC`(앱으로 ESC 전달).

## 3. 전달 경로(코드) — 무엇이 검증됐나

1. `client.py::on_key` — 정상 모드에서 위 키들은 pytmux 단축키가 아니므로 분기에
   걸리지 않고 `data = key_to_bytes(event); self.send_input(data)` 로 패널에 전달된다
   (`event.prevent_default()` 로 Textual 기본 동작도 막아 손실 없음).
2. `clientutil.py::key_to_bytes` — `SPECIAL` 에서 정확한 `CSI 1;2 X` 바이트를 돌려준다.
3. 모달(프롬프트/팝업)이 떠 있으면(`len(screen_stack) > 1`) 그 스크린이 키를 처리하므로
   패널로 새지 않는다(의도된 격리).

**헤드리스 회귀**: `tests/test_client.py::test_shift_nav_keys_forwarded_to_panel`
— 6개 키 각각이 정확한 시퀀스로 `send_input` 되는지 단언(정상 모드, 미가로채기 확인).

## 4. 수동 검증 체크리스트(실제 Claude CLI)

> 헤드리스로는 **전달**까지만 자동 검증된다. 선택/편집의 실효는 라이브 세션에서
> 사람이 확인한다(아래). Claude Code CLI 의 입력 위젯 버전에 따라 결과가 다를 수 있다.

준비: `python pytmux.py` 로 attach → 패널에서 `claude` 실행 → 프롬프트에 한 줄 입력.

1. **줄 끝까지 선택 후 삭제**: 커서를 줄 중간에 두고 `Shift+End` → (앱이 지원하면)
   커서~줄끝이 선택 표시 → `Delete` 또는 타이핑 → 선택 구간이 지워지고 대체.
2. **줄 시작까지 선택**: `Shift+Home` → 커서~줄시작 선택 → `Backspace`/타이핑으로 대체.
3. **한 글자씩 선택 확장**: `Shift+Left`/`Shift+Right` 를 여러 번 → 선택이 한 칸씩 확장.
4. **여러 줄 선택**(멀티라인 입력일 때): `Shift+Up`/`Shift+Down` → 행 단위 선택 확장.
5. **수정**: 선택 상태에서 일반 문자 입력 → 선택 구간이 그 문자로 치환되는지.

판정:
- **동작함** → pytmux 전달 + 앱 해석 모두 정상.
- **커서만 움직이고 선택 안 됨** → 앱(Claude CLI)이 `CSI 1;2 X` 의 Shift 수정자를
  선택으로 해석하지 않는 것(앱 한계). pytmux 전달은 정상(§3 회귀로 보장).
- **아무 반응 없음/글자 깨짐** → 터미널이 Shift 수정자를 인코딩 못 하는 환경일 수 있다
  (일부 conhost/WT/ssh 조합). 이 경우 호스트 터미널 설정을 확인.

## 5. 복사·붙여넣기 가능성(검토)

"블록으로 잡아 **복사**·**붙여넣기**"는 **삭제와 메커니즘이 근본적으로 다르다.**
삭제는 앱이 자기 버퍼 안에서 끝내지만, 복사·붙여넣기는 **시스템 클립보드**를 건너야
한다. 핵심 사실: **앱(Claude) 안의 텍스트 선택은 화면에 보일 뿐, 터미널·클립보드는
그 선택을 모른다.**

### 5.1 붙여넣기 — ✅ pytmux 가 보장

- `Ctrl+V`(윈도우/리눅스)·`Cmd+V`(맥)·명령 `paste-clipboard` →
  `client.py::paste_os_clipboard` → `clientclip.paste()`(pbpaste/xclip/wl-paste/
  PowerShell)로 **OS 클립보드 텍스트를 읽어** 서버에 `paste` 로 넘기고, 서버가
  패널 앱의 **bracketed paste(2004)** 모드를 존중해 마커로 감싸 주입한다.
- 즉 "다른 곳에서 복사한 텍스트를 Claude 프롬프트에 붙여넣기"는 앱 버전과 무관하게
  동작한다. (맥 터미널이 `Cmd+V` 를 가로채 `on_paste` 로 주는 경로도 함께 지원.)
- 이미지 붙여넣기는 Claude Code 가 공유 OS 클립보드에서 직접 읽는다(별도 경로).

### 5.2 복사 — Shift 선택으로는 ❌, copy-mode/마우스로 ✅

- **왜 Shift 선택→복사가 안 되나**: 앱이 자기 선택분을 시스템 클립보드로 보내는
  표준 통로는 `OSC 52`(`ESC ] 52 ; c ; <base64> BEL`)뿐이다. 그런데 **pytmux 는
  OSC 52 를 처리·중계하지 않는다** — 앱의 출력은 서버측 pyte 화면 모델로 들어가
  렌더될 뿐, OSC 52 가 호스트 클립보드로 전달되지 않는다. 게다가 흔한 CLI 입력
  위젯은 애초에 OSC 52 복사를 구현하지 않는다(터미널의 일이라 본다).
- **올바른 복사 경로 = pytmux copy-mode**: pytmux 는 tmux 식 copy-mode 를 갖췄다.
  스크롤(copy) 모드로 들어가 **마우스 드래그로 화면 텍스트를 선택**하면
  `clientwidgets.py::_extract_selection` 이 (자동 줄바꿈 줄을 한 줄로 이어) 텍스트를
  뽑고, `client.py::copy_text → clientclip.copy()`(pbcopy/xclip/wl-copy/clip.exe)로
  **시스템 클립보드에 넣는다.** 이건 앱의 내부 선택이 아니라 **화면에 렌더된 글자**를
  복사하므로, Claude 프롬프트에 보이는 텍스트도 그대로 복사된다(앱 버전 무관).
- **대안**: 호스트 터미널(Warp/iTerm 등) 자체의 마우스 선택 복사도 화면 글자를
  복사한다(pytmux 와 독립). 단 분할/오버레이가 겹친 영역은 호스트가 화면 그대로를
  복사하므로 copy-mode 가 더 정확하다.

### 5.3 종합 시나리오(권장 동선)

1. **붙여넣기**: 외부에서 복사 → Claude 프롬프트에서 `Ctrl/Cmd+V`. ✅ 항상 동작.
2. **프롬프트/출력 텍스트 복사**: copy-mode 진입 → 드래그 선택 → 복사(클립보드).
   그 텍스트를 다른 곳/다른 패널에 붙여넣기. ✅ 앱 무관.
3. **프롬프트 안에서 블록 잡아 삭제·치환**: `Shift+Home/End/방향키`로 선택 →
   `Delete`/`Backspace`/타이핑. ⚠ 단말이 Shift 수정자를 보내고 앱이 선택을
   지원할 때만(§4·§6). 안 되면 `Ctrl+W`(단어 삭제)·`Ctrl+U`(줄 앞 삭제) 등
   readline 식 단축키가 대안.

## 6. 알려진 제약

- **터미널 수정자 인코딩 의존(중요)**: pytmux 는 Textual 이 `shift+left` 등으로 키를
  주면 표준 시퀀스로 변환한다. 그러나 호스트 터미널이 `Shift+커서`(나아가 `Shift+Esc`)
  를 **수정자 없는 평범한 키와 같은 바이트로** 보내면 Textual 도 `left`/`escape` 로만
  받아 **Shift 정보가 유실**된다. 실측: **Warp(macOS)** 는 향상된 키보드 프로토콜이
  비활성이면 `Shift+Esc` 를 일반 `Esc` 와 같은 `\x1b` 로 보내 구별하지 못한다 —
  같은 한계가 `Shift+방향키` 선택에도 적용될 수 있다(단말 설정/버전 의존). 이때
  §5.1(붙여넣기)·§5.2(copy-mode 복사)는 **영향받지 않는다**(Shift 인코딩과 무관).
- **앱 버전 의존**: Claude Code CLI 의 텍스트 입력 위젯이 선택 모델을 갖추고 표준
  시퀀스를 받아들일 때만 §4(선택·삭제)가 동작한다. 미지원 버전이면 커서 이동만 된다.
- **OSC 52 미지원**: 앱→시스템 클립보드 복사(OSC 52)는 pytmux 가 중계하지 않는다.
  복사는 copy-mode/마우스 선택으로 한다(§5.2). (OSC 52 중계는 향후 개선 후보.)
