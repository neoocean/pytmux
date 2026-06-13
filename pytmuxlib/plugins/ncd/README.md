# ncd — 디렉토리 트리 점프 (Norton Change Directory)

Norton Commander 풍의 **디렉토리 트리 모달**(코드네임 nc). 루트(또는 Windows 드라이브 목록)부터 현재 패널 cwd 까지 **폴더만**(파일 제외) 펼쳐 띄우고 커서를 현재 디렉토리에 놓는다. 디렉토리명을 타이핑하면 speed-search 로 점프하고, Enter 로 그 폴더에 `cd`, ⇧Enter/^O 로 새 패널을 연다. 단일 `render_line` 위젯이라 선택 이동 시 변경된 두 줄만 다시 그려 ssh 원격에서도 빠르다.

![ncd 디렉토리 트리](screenshot.svg)

## 사용법

| 명령 | 별칭 |
|---|---|
| `ncd` | `nc` |

**트리 안에서 키:**

| 키 | 동작 |
|---|---|
| `↑` / `↓` · `Home`/`End` · `PgUp`/`PgDn` | 커서 이동 |
| `→` | 폴더 펼치기(지연 로드) |
| `←` | 접기 / 부모로 |
| 글자 입력 | speed-search 점프(`Backspace` 삭제) |
| `Enter` | 선택 폴더로 `cd` |
| `⇧Enter` / `^O` | 선택 폴더에 새 패널 분할 |
| `Esc` | 닫기 |

마우스 행 클릭으로도 커서를 옮긴다. 옵션 없음.

## 동작 방식

`ncd` 명령 → 클라가 `request_nc_list` 를 서버에 보내고, 서버 `nc_list_msg`(`server.py`)가 루트→cwd 사슬과 직계 하위를 회신하면 클라가 `NcdScreen`(`screen.py`)을 띄운다. `→` 펼치기마다 해당 폴더의 하위만 추가로 요청한다.

## delete-to-disable

이 디렉토리를 지우면 `ncd`/`nc` 명령·`app.request_nc_list` 글루·서버 `handle_server_request`·`NcdScreen` 이 모두 사라진다. 코어는 ncd 를 직접 참조하지 않으므로 무에러로 계속 동작한다.
