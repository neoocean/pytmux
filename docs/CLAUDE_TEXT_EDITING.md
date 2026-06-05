# pytmux 패널 안 Claude CLI — Shift+방향키 텍스트 선택·편집 검증

> 작성: 2026-06-05 · 대상: §10-A #5 (검증+문서) · 관련: [HANDOFF.md](HANDOFF.md)
> `pytmuxlib/clientutil.py`(`SPECIAL`/`key_to_bytes`) · `pytmuxlib/client.py`(`on_key`)

## 1. 결론(요약)

- **pytmux 책임(키 전달)**: ✅ **검증 완료.** pytmux 는 `Shift+Home`/`Shift+End`/
  `Shift+←→↑↓` 를 **표준 xterm 수정자 시퀀스(`CSI 1;2 X`)** 로 활성 패널(앱)에 그대로
  전달한다. 정상 모드에서 이 키들을 pytmux 단축키로 가로채지 않는다.
- **앱 책임(시퀀스 해석)**: 실제로 "선택 영역이 생기고 Del/타이핑으로 지워지는지"는
  **패널 안에서 도는 앱(Claude Code CLI 등)이 그 시퀀스를 어떻게 해석하느냐**에 달려
  있다. pytmux 는 시퀀스를 손실 없이 전달할 뿐, 선택/편집 동작 자체를 구현하지 않는다.
- 즉 **전달 경로는 pytmux 가 보장**하고(헤드리스 회귀로 고정), **선택·삭제·수정의
  실효는 앱 버전에 의존**한다(아래 §4 수동 체크리스트로 확인).

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

## 5. 알려진 제약

- **터미널 수정자 인코딩 의존**: pytmux 는 Textual 이 `shift+left` 등으로 키를 주면
  표준 시퀀스로 변환한다. 그러나 호스트 터미널이 Shift+커서를 수정자 없는 평범한
  커서 키로 보내면 Textual 도 `left` 로만 받아 Shift 정보가 유실된다(드물지만 일부
  환경). 이때는 앱에서 선택이 안 된다 — pytmux 가 아니라 호스트 터미널 한계.
- **앱 버전 의존**: Claude Code CLI 의 텍스트 입력 위젯이 선택 모델을 갖추고 표준
  시퀀스를 받아들일 때만 §4 가 동작한다. 미지원 버전이면 커서 이동만 된다.
