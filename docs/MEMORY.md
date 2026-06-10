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

## Claude 감지 휴리스틱 (CL 56315) — 진단 전말

- **증상**: 탭 상태 아이콘(대기 ○/처리중 ◐)이 현행 Claude Code 에서 안 뜸. 원인은
  `protocol.claude_state` 가 의존하던 화면 문구가 버전업으로 바뀐 것.
- **현행(2026) footer 문구**: busy 는 작업 스피너 줄 `✽ Crunching… (38s · ↓ 1.9k
  tokens)`(글리프·동명사·시간 매 프레임 변동, **"esc to interrupt" 사라짐**), idle 은
  권한 모드 줄 `⏵⏵ auto mode on (shift+tab to cycle)`(accept edits/plan mode 순환,
  **"? for shortcuts"/"bypass permissions" 안 보임**). 모드 줄은 busy 중에도 같이
  보이므로 **busy 를 먼저 판정**해야 함.
- **결정타(진단 기법)**: 캡처 로그(`<sock>.capture/pane-*.log`)는 raw 바이트라 글자
  사이에 커서 이동 ANSI 가 섞여 **grep 이 빗나간다**(예: `esc[2C[1Bto interrupt`).
  `claude_state` 가 실제로 보는 건 **pyte 가 렌더한 화면 텍스트**이므로, 로그 tail 을
  `pyte.ByteStream` 에 feed → `screen.display` 하단 줄을 봐야 진짜 문구가 나온다.
  스피너 동명사/글리프를 시간순으로 모으려면 일정 크기씩 feed 하며 하단 줄 스냅샷.
- **교훈**: 스피너처럼 매 프레임 바뀌는 UI 는 **변하지 않는 토막만** 잡는다 —
  말줄임표+괄호 경과시간(`… (20s`)은 안정적이고, 시간 숫자+s 를 요구하면
  `… +38 lines (ctrl+o)` 같은 도구 출력 오탐도 막힌다. 리밋(limit) 문구는 실제
  캡처 샘플이 없어 여전히 미검증(다음에 리밋 걸리면 로그 떠서 보강할 것).

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

## pyte 0.8.2 콜론식 SGR 미파싱 (CL 56333) — 진단 전말

- **증상**: 로컬에서 패널에 Claude Code 를 띄우면 인터페이스 **모든 문자에 밑줄**.
  원격(SSH/mosh)에선 정상. 환경 의존이라 "합성 단계(`_composite`) underline 누수"를
  먼저 의심했으나 헛다리.
- **결정타(진단 기법)**: 서버는 `ch.underscore` 를, 클라이언트는 `un` 플래그를 **그대로**
  전달한다(`model._char_style`↔`client.make_style`). 즉 밑줄은 합성이 아니라 **pyte 가
  실제로 켠 것**. 그럼 왜 환경별로 갈리나? → Claude Code 가 capable 터미널을 감지하면
  **현대식 콜론(:) 서브파라미터 SGR** 을 내보낸다는 가설. `python3 -c` 로 pyte 에
  `\x1b[4mAB\x1b[4:0mCD`(밑줄 ON → 콜론형 끄기) 를 직접 먹여보니 **밑줄이 안 꺼지고
  "0m" 잔해까지 찍힘**을 즉시 재현. 끝.
- **원인**: pyte 0.8.2 의 CSI 파서(streams.py)는 콜론을 미지 문자로 보고 `csi_dispatch[':']
  =debug`(no-op) 후 시퀀스를 **중단**한다. 그래서 `4:0`(밑줄 끄기)·`4:3`(곱슬)·
  `38:2::r:g:b`(24bit)·`58:`(밑줄색)이 전부 깨지고, 켠 밑줄이 stuck 돼 이후 셀에 번진다.
  풀스크린 재그리기가 "0m" 글자는 덮지만 커서의 underscore 속성은 살아남는다.
- **교훈**: pyte 0.8.2 는 **콜론식 SGR 을 전혀 못 읽는다.** "장식(밑줄/색)이 번지거나
  화면에 `0m`/`2;`/`:255:` 같은 잔해가 찍히면" 먼저 콜론 SGR 을 의심하고 `model.feed`
  의 `_sanitize_sgr`(콜론→세미콜론 정규화) 경로를 보라. 재현·검증은 **pyte 에 직접
  바이트를 먹여 `underscore`/`fg` 를 찍어보는 것**이 가장 빠르다(헤드리스로 결정적).
  같은 이유로 feed 경계 캐리는 `_CSI_PARTIAL_RE`(미완성 CSI 전반)로 넓혀야 쪼개진
  콜론 SGR 도 완전 시퀀스로 본다.

## 마우스 패스스루 (CL 56347) — 내부 앱에 마우스 보내기

- **구조**: 키는 `send_input`→PTY 경로가 있었지만 마우스는 패스스루 경로가 **아예
  없었다**(핸들러가 다 pytmux 용도로 소비·`event.stop()`). 그래서 서버가 내부 앱의
  마우스 모드를 추적(`Pane.update_mouse_modes`: DECSET 1000/1002/1003/1006, bracketed
  paste 2004 추적과 같은 위치)해 **패널별 `mouse`/`mouse_sgr` 플래그를 레이아웃에 실어**
  클라이언트에 알리고, 클라이언트가 인코딩해 `send_mouse`(=`t:input`+`mouse` 플래그)로
  해당 패널 PTY 에만 raw 전달(동기화/프롬프트 추적 제외)하도록 했다.
- **좌표·우선순위 함정**:
  - 좌표는 **패널 content 오프셋**(`layout` 의 `p["x"]/p["y"]`, 이미 테두리 inset 적용됨)
    을 빼서 **1-based** 로 변환. content 바깥(테두리)이면 전달 안 함.
  - **Claude 헤더 [x] 등 pytmux 오버레이가 content 행 0 에 그려진다** → 패스스루보다
    먼저 close-zone 들을 검사해야 [x] 클릭이 앱으로 새지 않는다.
  - prefix/copy-mode 면 pytmux 가 가로채는 게 tmux 동작(=escape hatch). `_mouse_target`
    이 `app.mode == "normal"` 일 때만 대상으로 잡게 해서 한곳에서 처리.
  - 인코딩은 1006(SGR `CSI<b;x;y M/m`, 릴리스만 소문자 m) 우선, 1006 미사용이면
    레거시 X10(`CSI M` + 32 오프셋, 릴리스는 버튼 3). 드래그는 버튼+32, 모션은 35.
- **검증**: `app.view._encode_mouse`/`_mouse_target` 가 순수 함수라 `app.layout` 만
  꾸며 단위 테스트 가능(헤드리스). 라우팅은 `app.send_mouse` 를 monkeypatch 해 확인.

## 환경 의존 버그는 "진단부터" (CL 56351) — 원격 SSH 휠

- 원격 SSH 에서만 휠 위쪽 스크롤백이 안 되는 버그는 코드 경로
  (`on_mouse_scroll_up`→`send_scroll`)가 로컬에서 정상이라 **상위 터미널/SSH 가 휠을
  Textual 까지 전달하느냐**의 환경 문제로 강하게 의심된다. 헤드리스/로컬 재현 불가.
- **교훈**: 재현·검증 불가한 환경 의존 버그는 **추측으로 "수정"을 서브밋하지 말 것.**
  대신 切り分け 할 **검증 가능한 진단 계측**을 넣는다 — 여기선 `set mouse-debug on` 으로
  받은 마우스/휠 이벤트를 `<sock>.mouse.log` 에 기록(어떤 가드보다 **먼저** 로깅해야
  "도달했는가"를 본다). 원격에서 `scroll_up` 이 찍히면 이벤트는 옴(→서버/터미널 재그리기
  조사), 안 찍히면 터미널이 안 넘김(→`$TERM`·SGR 1006·mosh vs ssh·터미널 자체 스크롤백).

## 동시 편집 세션 충돌 처리 (이 세션 실경험)

- 같은 client(@surface)에서 **다른 세션이 docs 등을 계속 서브밋** 중이면, 내가 연 파일이
  stale base 가 돼 `p4 edit` 시 "must resolve #N,#M" 가 뜬다. §9 의 56321 사고(stale
  base 로 남의 변경 되돌림)를 피하려면 **절대 그냥 submit 하지 말고** `p4 sync` →
  `p4 resolve -am`(겹치지 않으면 자동 병합). 겹치는 청크는 `-at`(theirs 수용) 후 내
  변경만 **head 기준으로 재적용**하는 게 conflict 마커 푸는 것보다 안전·확실.
- 서브밋 후 `p4 print -q //…#head | grep` 으로 **남의 변경+내 변경이 둘 다 살아있는지**
  반드시 확인. (이 세션 client.py: 동시 세션의 shift+enter/escape 매핑 + 내 마우스
  코드가 둘 다 보존됨을 grep 으로 확인했다.) p4 는 submit 시 CL 번호를 리네임할 수
  있다(56331→56333, 56345→56347) — 디스크립션/문서의 번호는 submit 후 실제 번호로 정정.

## Claude Code 기능 플러그인화 — delete-to-disable 패턴 (CL 57789~57907)

- **목표·계약**: `pytmuxlib/plugins/claude-code/` 디렉토리를 통째로 지우면 Claude 기능이
  **에러 없이** 전부 사라지고 코어(server/client)는 그대로 돈다. 코어는 Claude 코드를
  이름으로 부르지 않고 **레지스트리 훅 + `getattr(app, ..., 기본값)` 가드로만** 닿는다.
  훅이 없으면(=플러그인 부재) 전부 no-op/None/False/빈목록 → 코어 경로가 안 깨진다.
- **클라는 동적 베이스 믹스인을 못 쓴다 (핵심 함정)**: `PytmuxApp` 은 `build_client_app`
  팩토리 **안의 지역 클래스**라, 서버처럼 동적 베이스로 믹스인을 합성할 수 없다. 그래서
  클라측 플러그인은 `attach_client(app)` 에서 **인스턴스에 클로저를 설치**한다
  (`app.open_token_log = lambda: _open_token_log(app)`; ncd/`_saver_*` 와 같은 패턴).
  서버측은 반대로 `Registry.server_mixins()` 가 돌려주는 `ServerClaudeMixin` 을
  `Server` 의 **동적 베이스로 합성**한다(`serverclaude.py` 는 삭제하고 클래스만 플러그인
  `servermixin.py` 로 이전). "추출 완료"는 `grep ServerClaudeMixin` 이 플러그인 안에서만
  걸리고 `serverclaude.py` 가 사라졌는지로 확인.
- **선택적 탭은 가운데, 항상 있는 코어 탭은 끝에 두면 인덱스가 우아하게 클램프된다
  (CL 57907)**: 통합 상태 팝업을 `REC(0)·토큰(1)·서버(2)` 로 두고 **토큰 탭만** 플러그인
  소유로 뺐다. 열기 버튼은 인덱스로 초기 탭을 지정하는데(host 클릭=2), 토큰 탭이
  사라지면 탭이 2개라 `initial=2` 가 마지막=서버로 **자연 클램프**된다 → 호출부를 하나도
  안 고치고 delete-to-disable 가 성립. 교훈: **옵션 탭은 중간, 코어가 늘 채우는 탭은 끝**.
  (안 그러면 토큰 탭 자리에 "(플러그인 없음)" 안내 탭을 남겨야 하는데, 그건 진짜
  delete-to-disable 가 아니다.)
- **계약 테스트로 회귀 못박기 (test_plugin_contract)**: 실제로 디렉토리를 지우는 대신
  `Registry` 에서 해당 플러그인만 **필터로 제외**해 부재를 시뮬레이션한다. 이때 `load()`
  가 아니라 **`_discover()` 를 직접** 써야 — `load` 를 monkeypatch 한 테스트에서 자기재귀를
  피한다. 검증 내용: 명령/자동완성/옵션 누수 없음 + 모든 서버·클라 훅 no-op + 실제
  Textual 앱이 렌더·ESC·클릭·status 경로에서 안 깨지고 Claude 클로저·상태·세그먼트·
  **팝업 탭**이 전혀 안 생김. ("존재 시 노출"을 같이 단언해 헛검증(원래도 없던 것 검증)을
  막는 sanity 테스트도 한 쌍으로 둔다.)
- **단계적 추출 + 제출 전 `have==head` 필수**: 한 번에 다 옮기지 말고 phase 당 1 서브밋
  (명령→서버→클라 렌더→자투리). 같은 파일을 **playground 병렬 세션이 동시 편집**하므로
  (client.py·serverclaude→servermixin·test_*), 열기 전 `p4 fstat -T "haveRev, headRev"`
  로 두 값이 같은지 보고 시작해야 남의 미제출 리팩터 중간 상태를 덮지 않는다.

## 컨텍스트 하드스톱 자동복구 (CL 57957) — 리밋과 다른 신호

- **"limit" 과 하드스톱은 별개**: `claude_state` 의 `"limit"` 은 *사용량/rate* 리밋
  (reset/resume/retry/upgrade 키워드 — 시간 지나야 풀림)이다. 대화 **컨텍스트 윈도우**가
  꽉 차 `"Context limit reached · /compact or /clear to continue"` 로 멈추는 건 **다른
  상태** — `/compact` 한 방이면 즉시 풀린다. 둘을 한 파서로 합치면 오발화한다. 신규
  `claude_context_hardstop(text)` 로 분리하고, **오탐방지로 화면에 `/compact`·`/clear`
  리터럴이 실제로 떠 있을 때만 True**(`'…auto-compact: N%'` 카운트다운·셸출력은 비발화).
- **기본 ON 결정 근거**: idle `auto_compact` 는 기본 OFF(정상 대기 중 선제 압축은
  부작용 가능)인데, 하드스톱은 **정상 idle 이 아니라 완전 차단**이고 `/compact` 가
  유일 진행수단이라 부작용 없는 복구 → `auto_hardstop` 만 **기본 ON**. "토글 기본값은
  상태의 성격(선택적 최적화 vs 유일 탈출구)으로 가른다"가 교훈.
- **테스트 격리 함정**: 하드스톱 주입을 검증하려고 idle→hardstop 전환 화면을 쓰면,
  `claude_auto_launch`(기본 ON)가 그 idle 화면에 `/rc` 를 끼워넣어 주입 시퀀스가
  `['/compact','/rc','/compact']` 로 오염된다. 단일 기능 단위테스트는 **인접 자동기능
  (`claude_auto_launch`/`claude_auto_mode`/`auto_compact`)을 명시적으로 꺼서** 격리.

## `:` 명령 자동완성 공백 검색 (CL 57940) — 모호성 자가해소

- **공백을 구분자로 승격**: 옛 코드는 `" " not in s` 게이트로 공백이 들어오면 자동완성을
  꺼버렸다(인자 입력으로 간주). 제거하고 공백을 언더바/하이픈과 동일하게 `norm_sep`
  구분자로 취급 → `clock m`→`clock-mode` 매칭. **명령 vs 인자 모호성은 추가 상태 없이
  자가해소**된다: 실제 인자를 치면 정규화 입력이 어떤 명령 이름의 부분문자열도 아니게 돼
  후보가 자연 소멸하고 힌트(인자 안내)로 전환된다. "구분자 정규화 + 부분문자열 매칭"이
  스스로 모드 전환을 만든다 — 별도 파서/플래그 불필요.
- **후보풀에 플러그인 명령 병합 필수**: 인라인 자동완성이 코어 `COMMANDS` 만 보면
  `clock-mode` 같은 **플러그인 명령이 누락**된다. `_commands()`/`_command_options()` 로
  레지스트리(`getattr(app,"plugins",None)`)를 합쳐 매칭. delete-to-disable 유지(가드 통해).

## IME 한/영 배지 (CL 57983, office) — preedit 은 앱이 관찰 불가

- **관찰의 진실**: 이전 큐 메모는 "조합 중(preedit)을 노출한다"를 전제로 설계했으나
  **틀렸다**. preedit(조합 문자열)은 **앱이 아니라 OS/터미널이 하드웨어 커서 위치에
  오버레이**하는 것이라, 터미널 앱에는 **확정(committed)된 글자만** 키 이벤트로 도착한다
  (docs/IME_PREEDIT_CURSOR_SCENARIO.md). 즉 '조합 중'·OS IME *언어* 둘 다 앱이 직접
  질의할 수 없다. "터미널은 IME 상태를 안다"는 직관이 함정.
- **그래서 휴리스틱**: 한/영은 **패널로 보낼 확정 입력 문자의 스크립트**로 추정한다 —
  한글(자모/완성형 `clientutil.has_hangul`)→'한', ASCII 글자→'EN', 숫자·기호·공백·제어키는
  한·영 공통이라 **모드 중립**(직전 상태 유지, 숫자만 쳐도 안 깜빡임). 한글 모드에서
  ASCII 만 칠 때 'EN' 오판은 **불가피한 한계**(문서화로 갈음).
- **배선 패턴**: 키 관찰은 신규 클라 훅 `client_key(app,event)`(서버 `server_input` 의
  클라 대응) — `on_key` 의 `send_input` 직전 1줄 호출, 부재 시 no-op. 배지는 `client_render`
  훅으로 우상단. 다중패널 첫 행은 활성 패널 상단 테두리라, `[x]`(`_tab_close_zone`)처럼
  `app._ime_zone` 을 테두리-파랑 예외로 둔다. delete-to-disable 유지.
  [[claude-plugin-extraction-phases]]

## 병렬 세션 충돌 — `p4 resolve -ay` 차단 우회 (이 세션 실경험)

- **`p4 resolve -ay`(=accept yours, 남의 변경 폐기)는 Claude Code 분류기가 막는다.**
  병렬 office 세션이 같은 파일(test_server.py 등)을 먼저 서브밋해 `must sync/resolve`
  가 뜨면, 차단되는 `-ay` 대신 **`git merge-file -p MINE BASE THEIRS > merged` 3-way
  머지**로 상대 의도(head)와 내 변경을 둘 다 살린다(무충돌이면 exit 0·마커 0). 그 뒤
  `p4 sync -f <file>#head` 로 head 를 받고 merged 내용으로 덮어쓴 다음 reconcile→submit.
  교훈: "남의 변경을 버리는" 도구는 막혀 있으니 **3-way 로 합치는 경로**를 기본으로.
