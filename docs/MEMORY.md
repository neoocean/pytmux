# pytmux 학습 메모 (MEMORY)

> 세션을 거치며 알게 된 비자명한 사실·함정·진단 기법을 한 줄씩 쌓는다.
> 코드/커밋에 이미 적힌 것은 적지 않는다(여기엔 "왜·어떻게 알아냈나"를 남긴다).
> 관련: [HANDOFF.md](HANDOFF.md) · [DESIGN.md](DESIGN.md) · [CONTRIBUTING.md](CONTRIBUTING.md)

## 아키텍처·런타임

- **데몬 stale 판별법**: `ps -eo pid,lstart …`로 데몬 시작 시각을 보고, 만지려는
  서버 파일의 마지막 변경 시각(`git log -1 --format=%ci -- <file>`)과 비교한다.
  데몬 시작 < 파일 변경이면 stale → `kill-server` 재기동 필요. "왜 안 바뀌지"의 90%.
- **데몬 fd 들여다보기**: `lsof -p <daemon_pid>`로 `/dev/ptmx`(패널별 PTY master)와
  캡처 로그 fd를 직접 볼 수 있다. PTY master 개수 = 살아있는 패널 수여야 한다.
- 소켓 경로는 `/tmp/pytmux-<uid>/default.sock`(macOS는 `/private/tmp/...` 로 보임).
  캡처는 `<sock>.capture/pane-<id>.log` + `sessions.log`(패널→탭 매핑·생성시각).

## "새 탭이 기존 탭을 복사" 버그 (CL 56309) — 진단 전말

- **증상**: 활성 패널이 Claude Code(대체화면 TUI)일 때 새 탭을 열면 새 패널에
  활성 패널의 명령·출력이 섞여 "복사된 듯" 보임.
- **헛다리**: 코드상 `new_window`는 항상 fresh 단일 패널을 만든다(헤드리스 수십 회,
  alt-screen·fd churn 재현 모두 새 탭은 깨끗). 그래서 "stale 데몬/셸 SHARE_HISTORY/
  탭 이름 중복"을 먼저 의심했으나 전부 아님.
- **결정타**: 라이브 데몬에 `cmd new-tab` 후 캡처 로그를 보니 **새 패널
  `pane-8.log`(title='shell'로 시작=fresh)에 활성 Claude 화면이 byte 0부터 찍힘**.
  → 패널 간 출력이 실제로 섞이는 fd 문제임을 확정.
- **원인**: `pty.fork()`의 master는 close-on-exec가 아니다 → 새 패널의 자식 셸이
  형제 패널 master fd들을 상속 → master가 여러 프로세스에 살아남아 종료·재사용 시 꼬임.
- **교훈**: PTY 멀티플렉서는 master에 **반드시 `FD_CLOEXEC`**. 환경 특이적(데몬화+churn)
  버그는 헤드리스로 안 잡힐 수 있으니, **캡처 로그를 1차 진단 도구로** 쓰고, 막히면
  라이브 데몬에 프로토콜로 직접 붙어(`asyncio.open_unix_connection` + hello) 서버가
  보내는 `screen` 메시지를 받아 본다.
- macOS PTY는 슬레이브 종료 시 master 읽기가 빈 바이트가 아니라 **EIO**를 던진다
  → EOF 판정은 EIO/빈 읽기만. 그 외 OSError로 살아있는 패널을 닫으면 fd가 재사용되며
  같은 fd 꼬임을 부른다.

## Git·Perforce 워크플로 (이 저장소 특수)

- Perforce가 정본, GitHub(`neoocean/pytmux`)는 미러. **서브밋마다 git도 같은 단위로
  커밋·푸시**, 커밋 메시지에 `Perforce: change NNNN` 푸터.
- **무관한 변경은 별도 번호 CL**로. 분리법: `p4 change -o` → Description 채우고
  `Files:`에서 해당 파일만 남겨 `p4 change -i` → `p4 submit -c`. git도 파일 단위
  `git add`로 같은 수의 커밋 분리. (이 세션 56308 클라/56309 서버 분리가 그 예.)
- **주의: 동시에 도는 다른 작업/세션의 `git add -A`가 내 미커밋 변경을 휩쓸어
  엉뚱한 커밋에 섞을 수 있다.** 이 세션에 실제로 자동완성 변경이 "설치 스크립트"
  커밋에 묶여 푸시된 일이 있어, `git reset --soft`+파일별 재커밋으로 갈라
  force-push로 정리했다(backup 브랜치 먼저 떠두고). 작업 중엔 git 상태를 자주 확인.

## Textual UI 패턴(이 코드베이스)

- 모달은 `ModalScreen` 서브클래스. 선택 UI는 `ListView`(↑↓·Enter)로, 위험 확인은
  `ConfirmScreen`(중앙 팝업, 기본 선택=취소)으로 통일. 바닥 한 줄 입력은 `PromptScreen`.
- 명령 프롬프트 자동완성은 **부분일치**(첫 토큰이 명령 이름에 포함되면 후보) +
  입력 줄 위로 펼치는 후보 영역(`#pcand`, dock:bottom을 입력 줄보다 먼저 yield해서
  입력 줄이 맨 아래·후보가 그 위). 설명의 `[on|off]` 대괄호는 마크업 이스케이프 필수.
