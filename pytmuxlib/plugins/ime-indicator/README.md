# ime-indicator — 한/영 입력 상태 배지

현재 키보드 입력소스(IME) 상태를 **`[한]` / `[EN]` 배지**로 표시하는 플러그인. 배지는 커서가 있는 줄의 오른쪽 끝(preedit 과 같은 높이)에 그려져 시선 이동을 최소화한다.

**2계층 감지:**
- **macOS 로컬** — HIToolbox TIS 로 OS 입력소스를 직접 폴링(~1µs/호출)해 입력이 없어도 모드 전환을 즉시 반영(권위값).
- **ssh 원격·Linux 등** — OS 질의가 안 되면 확정 입력 문자의 스크립트로 추정(한글→`한`, ASCII→`EN`).

![IME 배지](screenshot.svg)

## 사용법

| 명령 | 별칭 | 동작 |
|---|---|---|
| `ime-indicator` | `ime` | 배지 표시 ON/OFF 토글(기본 ON) |

- 배지 색: `[한]` = 초록(success), `[EN]` = 파랑(primary) 배경.
- `y=0`(첫 줄) 커서일 땐 탭 닫기 `[x]` 와 겹치지 않게 우측 4칸을 비운다.

옵션(plugin_opts) 없음 — 표시 토글만.

## 동작 방식

화면 자체는 없고 `client_render`(프레임 합성) 훅에서 `render.py: draw_ime_indicator` 가 `app.ime_state` 를 읽어 배지를 그린다. macOS 실측은 `oskbd.current_source_id()`/`is_korean()` 이 담당한다. preedit(조합 중) 글자는 OS 오버레이라 확정 글자만 관찰된다.

## delete-to-disable

이 디렉토리를 지우면 `ime`/`ime-indicator` 명령과 `client_render` 배지 렌더가 사라진다. 상태(`app.ime_show`/`ime_state`)는 `attach_client` 가 설치하므로 플러그인이 없으면 코어가 직접 읽지 않는다 — 무에러로 계속 동작한다.

지우지 않고 끄기: `:plugins`(별칭 `plugin-manager`) 로 여는 **플러그인 관리 팝업**에서도 이 플러그인을 토글로 끌 수 있다. 가역적이며 `opts.json` 의 `disabled_plugins` 에 영속되고, 같은 팝업에서 다시 켜면 돌아온다(서버가 새 비활성 집합을 전 클라에 방송해 명령·훅이 즉시 빠짐). 파일을 지우는 delete-to-disable 과 달리 되돌릴 수 있다.
