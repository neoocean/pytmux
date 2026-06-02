# pytmux

Python + [Textual](https://textual.textualize.io/) 로 만든 **tmux 유사 터미널 멀티플렉서**입니다.
하나의 터미널 안에서 여러 셸을 패널로 나눠 쓰고, 앱이나 터미널 창을 닫아도 셸 세션이
계속 살아있게 해 줍니다.

## 왜 만들었나

- **tmux 를 원격 윈도우 환경에서 쓰기엔 설치·설정 절차가 번거로워서**, 파이썬으로 그냥
  직접 만들기로 했습니다. 파이썬만 있으면 스크립트 하나로 돌아갑니다.
- tmux 와 비슷하게 동작하지만, **명령어를 다 외우지 못하는 사람**과 **마우스를 좀 더
  쓰고 싶은 사람**을 위해 만들어졌습니다. 그래서:
  - 🖱️ **마우스를 1급으로 지원** — 경계선 드래그로 패널 크기 조절, 클릭으로 패널 포커스,
    우클릭으로 메뉴, 휠로 스크롤백.
  - 🧭 **TUI 기반 메뉴** — 단축키를 외우지 않아도 메뉴(`prefix Enter` 또는 우클릭)와
    명령 프롬프트(`prefix :`)로 거의 모든 동작을 할 수 있습니다.

## 특징

- **세션 영속성**: 셸 PTY 를 백그라운드 데몬(서버)이 보유합니다. 앱을 닫거나(`detach`)
  상위 터미널 창을 닫아도 셸은 계속 돌아가고, 다시 실행하면 이어서 붙습니다.
- **tmux 스타일 상태표시줄**: 하단 한 줄에 세션·윈도우 목록·줌 상태·시계 표시.
- **패널별 스크롤백**: tmux 처럼 copy-mode 로 따로 들어가지 않아도, 패널 위에서 휠을
  올리면 바로 지난 출력을 봅니다. 패널마다 독립적입니다.
- **마우스 + 키보드 + 메뉴** 세 가지 방식 모두로 제어.
- **붙여넣기 패스스루**: 멀티라인 텍스트 붙여넣기를 bracketed paste 로 그대로 전달하여
  Claude Code CLI 등에서 줄마다 실행되지 않고 한 번에 붙습니다. 이미지 붙여넣기도
  내부 프로그램이 공유 OS 클립보드에서 읽어 동일하게 동작합니다.
- **토큰 리밋 자동 재개**: 패널에서 돌리던 Claude Code 등이 사용량 리밋에 걸려 멈추면,
  출력에 표시된 해제 시각을 읽어 그 시각이 되면 자동으로 재개 메시지를 입력합니다.
  패널마다 `prefix R` 로 켜고 끌 수 있습니다(켜진 패널은 상태줄에 `AR` 표시).

## 설치

```sh
pip install textual pyte
# 또는
pip install -r requirements.txt
```

> macOS / Linux(POSIX PTY) 에서 동작합니다. Python 3.11 이상 권장.

## 사용법

```sh
python3 pytmux.py                 # 서버가 없으면 자동 기동 후 attach, 있으면 attach
python3 pytmux.py attach -t work  # 'work' 세션에 attach(없으면 생성)
python3 pytmux.py new -s work     # 'work' 라는 이름의 세션 생성 후 attach
python3 pytmux.py ls              # 세션 목록
python3 pytmux.py kill-server     # 서버와 모든 세션 종료
python3 pytmux.py cmd new-window  # 외부에서 서버 제어(split-window -h, rename-window 등)

# 렌더 진단(화면 없이): 프로그램 출력을 녹화→텍스트 프레임으로 재생
python3 pytmux.py record --cols 120 cap.raw -- ls -C   # 옵션은 파일명 앞, 명령은 -- 뒤
python3 pytmux.py replay --cols 120 cap.raw --ruler    # 텍스트로 덤프(+열 자)
python3 pytmux.py --socket PATH … # 사용할 소켓 경로 직접 지정
```

처음 실행하면 평소 쓰던 셸이 전체 화면으로 뜹니다. `Ctrl-b` (prefix) 를 누른 뒤
명령 키를 누르거나, 마우스/메뉴로 조작하면 됩니다.

## 키 바인딩 (prefix = `Ctrl-b`, 설정으로 변경 가능)

| 키 | 동작 | 키 | 동작 |
|----|------|----|------|
| `prefix %` | 좌우 분할 | `prefix "` | 상하 분할 |
| `prefix x` | 패널 삭제(확인) | `prefix z` | 패널 줌 토글 |
| `prefix o` | 다음 패널 | `prefix ←↑↓→` | 패널 이동 |
| `prefix H/J/K/L` | 패널 경계 이동 | `prefix c` | 새 윈도우 |
| `prefix ,` | 윈도우 이름변경 | `prefix &` | 윈도우 삭제(확인) |
| `prefix $` | 세션 이름변경 | `prefix :` | 명령 입력 |
| `prefix n` / `p` | 다음/이전 윈도우 | `prefix 0-9` | 윈도우 선택 |
| `prefix d` | detach(세션 유지) | `prefix [` | 스크롤백 모드 |
| `prefix Enter` | 메뉴 열기 | `prefix R` | 토큰리밋 자동재개 토글 |
| `ESC` | 명령 모드(←↑↓→ 패널 이동, `:` 명령 프롬프트) | `F12` | 중첩 시 prefix 패스스루 토글 |

스크롤백 모드(`prefix [`): `↑/↓`, `PageUp/PageDown`, `g`/`G`(맨 위/아래), `q`로 빠져나감.

> 한글 IME가 켜져 있어도 단축키가 동작합니다(두벌식 자모를 QWERTY 키로 자동 변환).
> 단, 두벌식에서 시프트가 구분되지 않는 `prefix H/J/K/L`(패널 크기 조절)은 IME 중엔
> 마우스 드래그나 영문 입력 상태를 사용하세요.

## 마우스

| 동작 | 결과 |
|------|------|
| 패널 클릭 | 해당 패널로 포커스 이동 |
| 경계선 드래그 | 패널 크기 조절 |
| 휠 위/아래 | 커서가 올라간 패널 스크롤백 |
| 우클릭 | 컨텍스트 메뉴 |

## 명령 프롬프트 (`ESC` → `:`, 또는 `prefix :`)

바닥에 입력창(모달)이 열리고 tmux 와 비슷한 명령을 받습니다. 입력 중:

- **`?`** — 전체 명령 목록을 띄우고 **방향키로 선택**(Enter 로 명령줄에 채움)
- **`help`** (또는 `commands`) — 전체 명령 목록을 설명과 함께 읽기 전용으로 표시
- **자동완성** — 명령을 타이핑하면 회색 고스트로 미리보기, **오른쪽 화살표(→)** 로 수락

예:

```
split-window -h        # 좌우 분할 (-v 는 상하)
kill-pane / kill-window
new-window / rename-window <name>
new-session -s <name> / switch-client -t <name> / rename-session <name>
resize-pane -Z         # 줌 토글
select-window <n>
detach / kill-server
```

## 설정 파일

`~/.config/pytmux/config` (또는 `~/.pytmux.conf`, `PYTMUX_CONFIG` 환경변수) 를 읽습니다.
예시는 [`pytmux.conf.example`](pytmux.conf.example) 참고.

```conf
set prefix C-a            # prefix 키 변경
set mouse on              # 마우스 on/off
set status-bg green       # 상태줄 색
set status-fg black
bind | split-window -h    # prefix 후 키 바인딩
bind r rename-window
```

## 문서

- [docs/DESIGN.md](docs/DESIGN.md) — 아키텍처/설계
- [docs/FEATURES.md](docs/FEATURES.md) — tmux 대비 기능 제안과 구현 현황
- [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) — 기여/서브밋 규칙
- [docs/INPUT_FOCUS_NOTE.md](docs/INPUT_FOCUS_NOTE.md) — 명령 프롬프트를 Textual Input 대신 직접 렌더링하는 이유(포커스 함정)

## 상태

`docs/FEATURES.md` 의 모든 기능(패널/윈도우/세션/복사 모드/명령·설정/상태줄·UI/
통합·자동화)이 구현되어 있습니다. 구현은 `pytmuxlib/` 패키지로 모듈화되어 있고
`pytmux.py` 는 진입점입니다.
