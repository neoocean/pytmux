# tmux 기능 대비 pytmux 기능 제안

> 상태: 구현 현황 반영(단일 세션 + 탭 모델, 상단 탭바, 패널 테두리/이름,
>       탭별 레이아웃 슬롯, textual-dark 색 스키마)
> 작성일: 2026-06-02 / 갱신: 2026-06-03
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

> **계층 모델(중요):** 멀티 세션 개념은 제거되었고 **항상 단일 세션**으로 시작한다.
> 최상위 전환 단위는 **탭**(= tmux 의 윈도우 역할), 탭마다 **단일 윈도우**가 종속되며,
> 그 윈도우를 **패널**로 분할한다. 즉 **Session → Tab → Window → Pane**.

| 영역 | 기능 | 상태 |
|------|------|------|
| 세션 | 단일 세션 모델(멀티 세션 없음), detach, kill-server, `ls`(탭/패널 요약) | ✅ |
| 탭 | 새 탭(=새 윈도우), 다음/이전/번호 선택, 상단 탭바(마우스/ESC 방향키·스크롤) | ✅ |
| 패널 | 가로(-h 상/하)·세로(-v 좌/우) 분할, 방향 이동·순환, 삭제, 리사이즈(키/마우스) | ✅ |
| 화면 | pyte 렌더, 패널 테두리 박스(활성=primary 파랑/항상 표시), 하단 상태표시줄 | ✅ |
| 스크롤백 | 패널별 독립 스크롤백(휠/키), 뷰포트 고정 | ✅ |
| 입력 | prefix 키 + 명령 프롬프트(고정 `:` 프리픽스) + 메뉴(ModalScreen) | ✅ |
| 마우스 | 휠 스크롤, 클릭 포커스, 경계 드래그 리사이즈, 우클릭 메뉴, 탭바 클릭 | ✅ |
| 색상 | p4v-tui 와 동일한 textual-dark 테마 팔레트 | ✅ |
| 영속성 | 데몬 분리로 앱/터미널 종료에도 셸 유지, 탭별 레이아웃 슬롯 저장/불러오기 | ✅ |

---

## 1. 패널(Pane) 기능

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **패널 줌(전체화면 토글)** | `prefix z` (resize-pane -Z) | 활성 패널만 윈도우 전체로 확대/복귀. 줌 상태 상태줄 표시 | P0 | ✅ |
| **패널 번호 표시 후 점프** | `prefix q` (display-panes) | 각 패널에 번호 오버레이, 숫자 키로 선택 | P1 | ✅ |
| **레이아웃 프리셋** | `prefix Space`, select-layout (even-h/v, main-h/v, tiled) | 트리를 프리셋대로 재배치하는 명령/메뉴 | P1 | ✅ |
| **패널 swap/rotate** | `prefix {` `}` `Ctrl-o` | 두 패널 위치 교환, 윈도우 내 회전 | P1 | ✅ |
| **패널 → 새 탭 분리(break)** | `prefix !` (break-pane) | 패널을 새 탭으로 떼어냄 | P2 | ✅ |
| **패널 합치기(join)** | join-pane | 다른 탭의 패널을 현재로 끌어옴 | P2 | ✅ |
| **마지막 패널 토글** | `prefix ;` | 직전 활성 패널로 복귀 | P2 | ✅ |
| **입력 동기화(broadcast)** | `setw synchronize-panes` | 한 입력을 윈도우 내 모든 패널에 전송 | P2 | ✅ |
| **패널 테두리/아웃라인** | pane border | 패널이 하나여도 항상 테두리 박스, 활성=primary(파랑)·비활성=회색, 경계는 ┬┴├┤ 로 연결 | P1 | ✅ |
| **패널 제목/이름** | `select-pane -T`, pane-border-status | prefix T/명령으로 제목 설정. 리네임되면 위쪽 테두리 **중앙**에 이름 표시(활성색), 상태줄에도 활성 패널 제목 | P2 | ✅ |
| **respawn-pane** | respawn-pane | 같은 슬롯에서 셸 재시작(새 PTY, 패널 id 유지) | P3 | ✅ |

## 2. 탭(Tab) 기능 — tmux 의 윈도우에 대응

각 탭은 단일 윈도우를 가지며 그 윈도우를 패널로 분할한다. 탭 명령은 tmux 의
윈도우 명령(`new-window`/`kill-window`/…)과 동일 동작이며 `*-tab` 별칭도 받는다.

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **새 탭** | `prefix c` (new-window) | 새 탭=새 윈도우(단일 패널) 생성 후 활성화. `new-tab`/`new-window` | P0 | ✅ |
| **탭 이름 변경** | `prefix ,` (rename-window) | 입력 프롬프트로 이름 변경. `rename-tab` | P0 | ✅ |
| **탭 삭제** | `prefix &` (kill-window) | 확인 후 탭 닫기. `kill-tab` | P0 | ✅ |
| **상단 탭바** | (tmux 없음) | 탭 2개↑면 상단 탭바([+]/탭/[x]). 마우스 클릭, ESC 모드 위 방향키로 포커스→←→ 선택→Enter 전환, 폭 초과 시 ◀▶ 스크롤. `set tab-bar always` | P1 | ✅ |
| **마지막 탭 토글** | `prefix l` (last-window) | 직전 탭으로 복귀 | P1 | ✅ |
| **탭 재정렬/이동** | `prefix .` (move-window), swap-window | 인덱스 변경·교환. `move-tab`/`swap-tab` | P2 | ✅ |
| **탭 선택기(트리)** | `prefix w` (choose-tree) | 탭 트리 모달에서 선택→전환 | P1 | ✅ |
| **자동 이름(automatic-rename)** | automatic-rename | 활성 패널 포그라운드 명령으로 2초마다 탭 이름 갱신, 수동 rename 시 해제 | P2 | ✅ |
| **활동/벨 모니터링** | monitor-activity, monitor-bell | 비활성 탭 출력(#)·벨(!)을 탭/상태줄 플래그로 표시 | P2 | ✅ |
| **탭별 레이아웃 저장/불러오기** | (tmux 없음) | 활성 탭의 윈도우+패널 트리를 이름 슬롯으로 저장(`layout-save`), 현재 탭 덮어쓰기/새 탭으로 불러오기(`layout-load`/`layout-load-new`, 선택기·메뉴), 디스크 영속 | P2 | ✅ |

## 3. 세션(Session) 모델 — 단일 세션

멀티 세션 개념은 사용자 표면에서 제거되었다. 항상 하나의 세션으로 시작하며,
여러 작업 공간은 **탭**으로 구분한다(위 2절).

| 기능 | tmux 대응 | pytmux 적용 | 우선순위 | 상태 |
|------|-----------|-------------|:---:|:---:|
| **단일 세션 attach** | `attach` | 이름 없이 단일 세션에 attach(세션 이름 요청 무시) | P0 | ✅ |
| **detach** | `prefix d` (detach-client) | 앱 종료, 셸은 데몬에서 유지 | P0 | ✅ |
| **다중 클라이언트 미러링** | 같은 세션 동시 attach | 여러 클라이언트가 한 세션 공유, 최소 크기로 레이아웃 동기화 | P2 | ✅ |
| ~~다중 세션/세션 전환/이름변경~~ | new-session 등 | **제거됨**(단일 세션 모델) | — | — |

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
| **명령 프롬프트** | `prefix :` (command-prompt) | ESC→`:` 또는 prefix `:`. 하단 입력줄(고정 `:` 프리픽스, 백스페이스로 안 지워짐), `?`/`help` 로 명령 목록(스크롤바·개수 표시), 타이핑 자동완성(옵션 `-h` 등 포함) | P0 | ✅ |
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
| **하단 상태줄** | status line | 탭·줌·시계 표시. **상단 탭바가 보이면 하단 탭 목록은 생략** | — | ✅ |
| **상단 탭바** | (tmux 없음) | 탭 인터페이스([+]/탭/[x]), 마우스·ESC 방향키·가로 스크롤 | P1 | ✅ |
| **색 스키마** | (tmux 색 옵션) | p4v-tui 와 동일한 textual-dark 팔레트(primary/surface/panel/accent 등). `status-bg/fg` 로 덮어쓰기 | P1 | ✅ |
| **상태줄 포맷 커스터마이즈** | status-left/right, #{} 포맷 | status-left/right 템플릿(#S/#h/#H/#{pane_title}/strftime) + 색 | P1 | ✅ |
| **상태줄 위치/다중 줄** | status-position, status 2~5 | `set status-position top\|bottom` (다중 줄은 미구현) | P2 | ✅ |
| **갱신 주기** | status-interval | `set status-interval N` 초마다 상태줄(시계) 갱신 | P2 | ✅ |
| **메시지/알림 표시** | display-message | 상태줄에 일시적 메시지(복사 알림 등), `display-message` 명령 | P1 | ✅ |
| **시계 모드** | `prefix t` (clock-mode) | **현재 패널 전체를 큰 시계로 덮는다**. 뒤의 터미널 출력은 흐리게(dim) 계속 갱신되어 보이고, 우상단 `[x]` 클릭 또는 그 패널에서 `clock-mode` 명령(토글)으로 닫는다 | P3 | ✅ |
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
| **CLI 제어** | `tmux <command>` | `pytmux cmd <명령>` 으로 외부 셸에서 서버 제어(new-window/split/rename/send-keys 등) | P2 | ✅ |
| **레이아웃 영속(재부팅 복원)** | tmux-resurrect/continuum | 전체: `save-layout`/`restore-layout`로 탭·패널 트리 직렬화·복원(셸은 새로 시작), 서버 시작 시 자동 복원. 탭별: 이름 슬롯 `layout-save`/`layout-load`(2절) | P3 | ✅ |
| **중첩(tmux in tmux)** | prefix 중복 처리 | F12 로 outer prefix 가로채기 토글(꺼지면 prefix 가 내부로 전달, 상태줄 NEST) | P3 | ✅ |

---

## 8. 권장 도입 순서 (제안)

1. **1차(P0) — ✅ 구현 완료** — 즉시 기대하는 핵심:
   패널 줌(`z`), 탭 이름변경(`,`)·삭제(`&`)·생성(`c`), 명령 프롬프트(`:`),
   단일 세션 attach, 설정 파일 로드. (보너스로 prefix 변경·키 바인딩도 함께)
2. **2차(P1) — ✅ 구현 완료** — 일상 편의:
   레이아웃 프리셋, 패널 번호 점프(`q`), 상단 탭바·탭 선택기,
   스크롤백 검색·선택 복사·클립보드, 키 바인딩 커스터마이즈,
   상태줄 포맷·메시지 표시, 마지막 탭 토글.
3. **3차(P2 이상) — ✅ 구현 완료** — 고급/통합:
   synchronize-panes, break/join-pane, 팝업, 다중 클라이언트,
   send-keys/capture-pane, 옵션·훅 체계, 재부팅 복원.

> 현재 FEATURES 표의 모든 항목이 구현되어 있다. pytmux 고유 기능으로 **토큰 리밋
> 자동 재개**와 **멀티라인/이미지 붙여넣기 패스스루**가 추가되어 있다.

## 9. pytmux 차별화 관점

tmux 대비 pytmux 가 더 잘할 수 있는(강조할) 지점:

- **마우스 1급 지원**: 드래그 리사이즈·클릭 포커스·우클릭 메뉴·**상단 탭바 클릭(탭
  전환/추가/삭제·스크롤)** 구현됨. 향후 드래그로 패널 swap, 탭 드래그 재정렬로 확장.
- **항상 켜진 직관적 스크롤백(R6)**: copy-mode 진입 없이 휠만으로 과거 화면 확인.
- **TUI 메뉴/선택기 우선**: 모든 동작을 키뿐 아니라 메뉴·트리 선택기로 노출해
  단축키를 외우지 않아도 전부 조작 가능.
