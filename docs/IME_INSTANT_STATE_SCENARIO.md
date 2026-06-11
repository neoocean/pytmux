# IME 한/영 배지 — 상태 변경 "직후" 반영 시나리오 (§10-B)

> **상태**: ✅ 조사·구현 완료(2026-06-11). macOS 는 OS 실측(TIS 폴링)으로 한/영
> 키만 눌러도 입력 없이 ≤0.25초 안에 배지가 따라온다. 그 외 환경(ssh 원격
> 클라·리눅스·Windows)은 기존 확정 입력 휴리스틱 폴백.
> 관련: [IME_PREEDIT_CURSOR_SCENARIO.md](IME_PREEDIT_CURSOR_SCENARIO.md)(preedit
> 관측 불가 제약), `pytmuxlib/plugins/ime-indicator/`.

## 1. 문제

ime-indicator(57983)는 패널로 보내는 **확정 입력 문자의 스크립트**로 한/영을
추정한다(preedit 은 OS 가 하드웨어 커서에 오버레이라 앱은 확정 글자만 받음).
그래서 한/영 키로 **모드만 바꾸고 아직 글자를 입력하지 않은** 동안은 배지가
직전 상태로 남는다 — 사용자가 배지를 보고 모드를 확인하려는 바로 그 순간에
못 미덥다(2026-06-11 사용자 요청: 상태 변경 직후 반영될 방법 찾기).

## 2. 조사 (2026-06-11, macOS 15 playground.local 실측)

### 채택 — ① macOS TIS(Text Input Source) 직접 질의

HIToolbox 의 `TISCopyCurrentKeyboardInputSource()` 는 **GUI 앱이 아닌 CLI
프로세스에서도** 같은 로그인 세션의 현재 입력소스를 돌려준다. ctypes 만으로
바인딩 가능(pyobjc 불필요):

- 실측: 최초 호출 ~33ms(프레임워크 로드), 이후 **호출당 ~1µs** — 0.25초 폴링은
  사실상 무비용.
- 반환 예: `com.apple.keylayout.ABC`(영), `com.apple.inputmethod.Korean.
  2SetKorean`(한). 서드파티는 `…Gureum.han2` 등 — `korean/hangul/han2/han3`
  소문자 부분 대조로 판별(`oskbd.is_korean`).
- 주의: 사용자별 "문서/앱별 입력소스 자동 전환" 옵션이 켜져 있으면 세션 전역
  값과 포커스 앱의 값이 다를 수 있다(드묾 — 터미널 포커스 중 전환은 세션 값도
  바뀌므로 실용상 문제 없음).

### 기각 — ② 입력소스 변경 알림 구독

`kTISNotifySelectedKeyboardInputSourceChanged` 분산 알림은 CFRunLoop 가 도는
전용 스레드 + asyncio 브리지가 필요하다. 폴링 비용이 ~1µs 라 즉시성(0.25초)
대비 복잡도(스레드 수명·종료 정리·Textual 스레드 안전)가 수지에 안 맞아 기각.

### 기각 — ③ 터미널 이스케이프 시퀀스

호스트 터미널이 IME 상태를 앱에 노출하는 **표준 시퀀스는 없다**(xterm/iTerm2/
Terminal.app/WezTerm/kitty 조사 — kitty 의 IME 프로토콜도 preedit 전달이지 모드
질의가 아님). 비표준 의존은 기각.

### 제약 — ④ ssh 원격 클라이언트

pytmux 클라가 **원격 호스트에서** 돌면(로컬 Mac 의 ssh 너머) 그 프로세스는
로컬 OS 입력소스를 질의할 수 없다(원격엔 Aqua 세션 없음 → TIS 로드/질의 실패
→ `oskbd` 가 None). 이 경우 기존 휴리스틱 폴백이 그대로 동작한다. 로컬 IME
상태를 원격 클라로 중계하려면 별도 사이드채널이 필요해 범위 밖(후속 검토 가치
낮음 — 로컬 사용이 주).

### 후속 — ⑤ Windows

`GetKeyboardLayout`/`ImmGetConversionStatus`(IMM32) 로 콘솔 포그라운드 윈도의
한/영 변환 상태를 읽을 수 있을 것으로 보이나 **실 Windows 박스 검증 필요**
(office 머신, WINDOWS_TESTING.md 트랙). 현재 Windows 는 휴리스틱 폴백.

## 3. 설계 (구현됨 — 전부 플러그인 내부, 코어 변경 0)

상태 원천 2계층:

| 계층 | 환경 | 원천 | 지연 |
|---|---|---|---|
| ① OS 실측(권위값) | macOS 로컬 클라 | `oskbd.current_source_id()` 0.25초 폴링 | ≤0.25초 |
| ② 휴리스틱(폴백) | ssh 원격·리눅스·Windows·TIS 실패 | 확정 입력 스크립트(`client_key`) | 다음 확정 입력 시 |

- `oskbd.py`(신규): ctypes TIS 바인딩. 모든 실패는 None 수렴 + 불가 확정 캐시
  (`_libs=False`) — 폴링 경로 스팸 없음.
- `attach_client`: 1회 프로브로 `_ime_os` 결정 + 가능하면 실측으로 초기 상태
  (이전엔 무조건 'EN' 시작).
- `client_tick`(코어 1초 훅): 첫 틱에서 `app.set_interval(0.25, _poll)` **지연
  설치**(attach 시점엔 앱이 안 돌아 set_interval 불가). 타이머 불가 환경은 1초
  틱 폴링으로 강등.
- `_poll`: 변화 시에만 재합성. 일시 실패(None)는 직전 상태 유지(깜빡임 방지).
  `_ime_os` 가드로 실측 비활성 시 잔존 타이머가 상태를 덮지 않음.
- `client_key`: `_ime_os` 면 **침묵** — 한글 모드에서 영문을 치면 'EN' 으로
  오판하던 휴리스틱 한계가 실측에 역류하지 않는다(이 한계는 폴백 경로에만 잔존).

delete-to-disable 유지: 디렉토리 삭제 시 oskbd/타이머/배지 전부 소멸(코어는
client_tick/client_key/client_render 레지스트리 훅으로만 닿음).

## 4. 검증

`tests/test_plugin_ime_indicator.py` 6) 절 — 전부 oskbd 스텁(환경 비의존):
실측 초기화·휴리스틱 침묵·폴링 전환·일시 실패 유지·타이머 1회 지연 설치·
불가 시 폴백. 코어 배선 테스트(5)는 폴백 강제(`_ime_os=False`)로 결정화.
