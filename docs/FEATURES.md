# tmux 기능 대비 pytmux 기능 제안

> 상태: Draft v1
> 작성일: 2026-06-02
> 관련 문서: [DESIGN.md](DESIGN.md)

tmux 가 제공하는 기능들을 검토해 pytmux 가 갖추면 좋을 기능을 우선순위별로 제안한다.
각 항목은 tmux 의 대응 개념/명령과, pytmux 에서의 적용 방식, 현재 상태를 함께 적는다.

## 범례 — 현재 상태

- ✅ **구현됨**: `pytmux.py` 에 동작 코드 존재
- 📐 **설계됨**: DESIGN.md 에 설계되어 있으나 미구현 또는 부분 구현
- ⬜ **미구현**: 아직 설계·구현 모두 없음

## 우선순위

- **P0 (필수)**: tmux 사용자가 없으면 불편을 크게 느끼는 핵심 기능
- **P1 (권장)**: 일상 사용 빈도가 높아 곧 필요한 기능
- **P2 (선택)**: 있으면 좋은 편의/고급 기능
- **P3 (향후)**: 큰 작업이거나 틈새 기능

---

## 0. 현재 상태 요약

이미 동작하거나 설계된 것:

| 영역 | 기능 | 상태 |
|------|------|------|
| 세션 | 새 세션, 기본 세션 attach/detach, kill-server, `ls` | ✅ |
| 윈도우 | 새 윈도우, 다음/이전/번호 선택 | ✅ |
| 패널 | 좌우/상하 분할, 방향 이동·순환, 삭제, 리사이즈(키/마우스) | ✅ |
| 화면 | pyte 렌더, 하단 상태표시줄 | ✅ |
| 스크롤백 | 패널별 독립 스크롤백(휠/키), 뷰포트 고정 | ✅ |
| 입력 | prefix 키 + 명령 메뉴(ModalScreen) | ✅ |
| 마우스 | 휠 스크롤, 클릭 포커스, 경계 드래그 리사이즈, 우클릭 메뉴 | ✅ |
| 영속성 | 데몬 분리로 앱/터미널 종료에도 세션 유지 | ✅ |

---

## 1. 패널(Pane) 기능

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **패널 줌(전체화면 토글)** | `prefix z` (resize-pane -Z) | 활성 패널만 윈도우 전체로 확대/복귀. 줌 상태 상태줄 표시 | P0 | ✅ |
| **패널 번호 표시 후 점프** | `prefix q` (display-panes) | 각 패널에 번호 오버레이, 숫자 키로 선택 | P1 | ✅ |
| **레이아웃 프리셋** | `prefix Space`, select-layout (even-h/v, main-h/v, tiled) | 트리를 프리셋대로 재배치하는 명령/메뉴 | P1 | ✅ |
| **패널 swap/rotate** | `prefix {` `}` `Ctrl-o` | 두 패널 위치 교환, 윈도우 내 회전 | P1 | ✅ |
| **패널 → 윈도우 분리(break)** | `prefix !` (break-pane) | 패널을 새 윈도우로 떼어냄 | P2 | ✅ |
| **패널 합치기(join)** | join-pane | 다른 윈도우 패널을 현재로 끌어옴 | P2 | ✅ |
| **마지막 패널 토글** | `prefix ;` | 직전 활성 패널로 복귀 | P2 | ✅ |
| **입력 동기화(broadcast)** | `setw synchronize-panes` | 한 입력을 윈도우 내 모든 패널에 전송 | P2 | ✅ |
| **패널 제목** | `select-pane -T`, pane-border-format | prefix T/명령으로 제목 설정, 상태줄에 활성 패널 제목 표시 | P2 | ✅ |
| **패널 경계선 상태** | pane-border-status | 각 패널 상단에 제목 줄(활성=cyan), `setw pane-border-status on` | P3 | ✅ |
| **respawn-pane** | respawn-pane | 같은 슬롯에서 셸 재시작(새 PTY, 패널 id 유지) | P3 | ✅ |

## 2. 윈도우(Window) 기능

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **윈도우 이름 변경** | `prefix ,` (rename-window) | 입력 프롬프트로 이름 변경 | P0 | ✅ |
| **윈도우 삭제** | `prefix &` (kill-window) | 확인 후 윈도우 닫기 | P0 | ✅ |
| **마지막 윈도우 토글** | `prefix l` (last-window) | 직전 윈도우로 복귀 | P1 | ✅ |
| **윈도우 재정렬/이동** | `prefix .` (move-window), swap-window | 인덱스 변경·교환 | P2 | ✅ |
| **윈도우 선택기(트리)** | `prefix w` (choose-tree) | 세션/윈도우 트리 모달에서 선택→전환 | P1 | ✅ |
| **자동 이름(automatic-rename)** | automatic-rename | 활성 패널 포그라운드 명령으로 2초마다 이름 갱신, 수동 rename 시 해제 | P2 | ✅ |
| **활동/벨 모니터링** | monitor-activity, monitor-bell | 비활성 윈도우 출력(#)·벨(!)을 상태줄 플래그로 표시 | P2 | ✅ |

## 3. 세션(Session) 기능

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **이름 있는 세션 + 이름으로 attach** | `new -s`, `attach -t` | `attach -t name`, 세션 이름 지정 생성 | P0 | ✅ |
| **세션 전환** | `prefix s` (choose-tree), switch-client | `:switch-client -t name` 으로 전환(선택기 트리는 미구현) | P1 | ✅ |
| **세션 이름 변경** | `prefix $` (rename-session) | 프롬프트로 변경 | P1 | ✅ |
| **세션 삭제** | kill-session | 특정 세션만 종료 | P1 | ✅ |
| **다중 클라이언트 미러링** | 같은 세션 동시 attach | 여러 클라이언트가 한 세션 공유, 최소 크기로 레이아웃 동기화 | P2 | ✅ |
| **다른 클라이언트 detach** | detach-client | `detach-client -a` 로 다른 클라이언트 분리 | P3 | ✅ |

## 4. 복사 모드 / 스크롤백 (copy-mode)

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **스크롤백 스크롤** | copy-mode 스크롤 | 패널별 휠/키 스크롤 | — | ✅ |
| **스크롤백 검색** | `/` `?` (search-forward/backward) | 스크롤백 모드에서 `/` 검색·매치 라인 하이라이트·`n`/`N` 다음/이전 | P1 | ✅ |
| **텍스트 선택/복사** | space→enter, vi/emacs 선택 | 스크롤백 모드에서 마우스 드래그 선택→페이스트 버퍼 복사(선택 영역 하이라이트) | P1 | ✅ |
| **클립보드 연동** | OSC52, `pbcopy` 연동 | 복사 시 OS 클립보드(pbcopy/xclip/wl-copy)에도 저장, paste-clipboard 로 붙여넣기 | P1 | ✅ |
| **붙여넣기 버퍼** | `prefix ]`, paste-buffer, choose-buffer | 다중 페이스트 버퍼(prefix ] 붙여넣기) + 선택기(prefix =) | P2 | ✅ |
| **히스토리 지우기** | clear-history | 활성 패널 스크롤백 비우기(clear-history 명령) | P2 | ✅ |
| **vi/emacs 키 테이블** | mode-keys | `set mode-keys vi\|emacs` 로 스크롤백 모드 키맵 선택(j/k·Ctrl-p/n 등) | P2 | ✅ |

## 5. 명령 / 키 / 설정

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **명령 프롬프트** | `prefix :` (command-prompt) | 하단에 명령 입력줄, 명령어 실행 | P0 | ✅ |
| **설정 파일** | `~/.tmux.conf` | `~/.config/pytmux/config` 로드(prefix·mouse·색·bind) | P0 | ✅ |
| **prefix 키 변경** | `set prefix` | 설정으로 prefix 재정의 | P1 | ✅ |
| **사용자 키 바인딩** | bind-key / unbind-key | `bind <key> <command>` (unbind 는 미구현) | P1 | ✅ |
| **설정 리로드** | `source-file` | `source-file [path]` 로 재시작 없이 prefix/색/키맵/바인딩 재적용 | P1 | ✅ |
| **옵션 체계** | set/setw (server/session/window/pane) | 런타임 `set <opt> <val>` 적용 + `show-options` 조회(클라 옵션), 윈도우 옵션은 `setw` | P2 | ✅ |
| **command-alias / if-shell / run-shell** | 동명 | `alias` 설정·`if-shell`/`run-shell`(출력은 버퍼+모달) | P3 | ✅ |
| **hooks** | set-hook | `set-hook <event> <cmd>` (client-attached/after-new-window/alert-bell) | P3 | ✅ |

## 6. 상태표시줄 / UI

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **하단 상태줄** | status line | 세션·윈도우·시계 표시 | — | ✅ |
| **상태줄 포맷 커스터마이즈** | status-left/right, #{} 포맷 | status-left/right 템플릿(#S/#h/#H/#{pane_title}/strftime) + 색 | P1 | ✅ |
| **상태줄 위치/다중 줄** | status-position, status 2~5 | `set status-position top\|bottom` (다중 줄은 미구현) | P2 | ✅ |
| **갱신 주기** | status-interval | `set status-interval N` 초마다 상태줄(시계) 갱신 | P2 | ✅ |
| **메시지/알림 표시** | display-message | 상태줄에 일시적 메시지(복사 알림 등), `display-message` 명령 | P1 | ✅ |
| **시계 모드** | `prefix t` (clock-mode) | 초 단위 갱신 시계 오버레이(아무 키나 닫힘) | P3 | ✅ |
| **팝업 창** | display-popup | `display-popup <cmd>` 실행 결과를 떠있는 모달로 표시(라이브 PTY 팝업은 미구현) | P2 | ✅ |
| **메뉴** | display-menu | 컨텍스트 메뉴 | — | ✅ |
| **터미널 제목 설정** | set-titles | `set set-titles on` + set-titles-string(#S/#I/#W)로 터미널 제목 갱신 | P2 | ✅ |
| **마우스 모드 토글** | `set mouse` | 설정 `set mouse on/off` 로 전환 | P2 | ✅ |

## 7. 통합 / 자동화

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **토큰 리밋 자동 재개(pytmux 고유)** | — | 패널 출력에서 사용량 리밋 해제 시각을 읽어 그 시각에 자동으로 재개 메시지 입력. 패널별 토글(prefix R) | P1 | ✅ |
| **붙여넣기 패스스루(멀티라인/이미지)** | bracketed paste | 외부 터미널 붙여넣기를 bracketed paste 로 감싸 패널에 전달(멀티라인 보존). 이미지는 내부 앱이 공유 OS 클립보드에서 읽음 | P1 | ✅ |
| **send-keys** | send-keys | `send-keys <키...>` 로 활성 패널에 키/텍스트 주입(Enter/C-x/-l 등) | P2 | ✅ |
| **capture-pane** | capture-pane | `capture-pane [-S]` 로 패널(가시/전체) 텍스트를 페이스트 버퍼로 덤프 | P2 | ✅ |
| **pipe-pane** | pipe-pane | `pipe-pane <cmd>` 로 패널 출력을 외부 명령에 복제(빈 인자=토글 off) | P3 | ✅ |
| **CLI 제어** | `tmux <command>` | 외부 셸에서 서버 제어(분할/선택 등) | P2 | 부분(ls/kill) |
| **세션 영속(재부팅 복원)** | tmux-resurrect/continuum | 레이아웃 직렬화 후 복원 | P3 | 📐 |
| **중첩(tmux in tmux)** | prefix 중복 처리 | 내부 클라이언트로 prefix 전달 | P3 | ⬜ |

---

## 8. 권장 도입 순서 (제안)

1. **1차(P0) — ✅ 구현 완료** — tmux 사용자가 즉시 기대하는 핵심:
   패널 줌(`z`), 윈도우 이름변경(`,`)·삭제(`&`), 명령 프롬프트(`:`),
   이름 있는 세션 + `attach -t`, 설정 파일 로드.
   (보너스로 P1 의 prefix 변경·키 바인딩·세션 전환/이름변경도 함께 구현)
2. **2차(P1)** — 일상 편의:
   레이아웃 프리셋, 패널 번호 점프(`q`), choose-tree 선택기,
   스크롤백 검색·선택 복사·클립보드, 키 바인딩 커스터마이즈,
   상태줄 포맷·메시지 표시, 마지막 윈도우/세션 토글.
3. **3차(P2 이상)** — 고급/통합:
   synchronize-panes, break/join-pane, 팝업, 다중 클라이언트,
   send-keys/capture-pane, 옵션·훅 체계, 재부팅 복원.

## 9. pytmux 차별화 관점

tmux 대비 pytmux 가 더 잘할 수 있는(강조할) 지점:

- **마우스 1급 지원**: 드래그 리사이즈·클릭 포커스·우클릭 메뉴는 이미 구현됨.
  여기에 드래그로 패널 swap, 탭(윈도우)을 클릭/드래그로 전환·재정렬까지 확장.
- **항상 켜진 직관적 스크롤백(R6)**: copy-mode 진입 없이 휠만으로 과거 화면 확인.
- **TUI 메뉴/선택기 우선**: 모든 동작을 키뿐 아니라 메뉴·트리 선택기로 노출해
  단축키를 외우지 않아도 전부 조작 가능.
