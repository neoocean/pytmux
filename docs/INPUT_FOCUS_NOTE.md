# 설계 노트: 왜 명령 프롬프트를 Textual `Input` 대신 직접 렌더링하는가

> 작성일: 2026-06-02
> 관련 코드: `pytmuxlib/client.py` (StatusBar.prompt_*, PytmuxApp._handle_prompt_key)

## 요약 (TL;DR)

pytmux 클라이언트는 **화면 전체를 직접 합성·재그리는 전체화면 위젯
(`MultiplexerView`)이 항상 키보드 포커스를 쥐는** 구조다. 이 구조에 스톡 Textual
`Input` 위젯을 같은 스크린에 얹으면 **포커스를 안정적으로 유지하지 못해 실제
터미널에서 입력이 들어가지 않는다.** 그래서 명령 프롬프트(`:`/`ESC`)는
`Input` 위젯을 쓰지 않고, **상태표시줄에 명령줄을 직접 렌더링하고 키 입력은
`App.on_key`에서 직접 처리**한다(= 셸 포워딩과 동일 경로).

이 결정을 되돌리지 말 것. 되돌리면 "테스트는 통과하는데 실기에서 입력 불가"인
함정에 다시 빠진다.

## 근거 (Textual 8.2.5 소스 기준)

### 1) 키 이벤트는 오직 `self.focused`(또는 screen)로만 전달된다
`textual/app.py` (Key 이벤트 처리):
```python
elif isinstance(event, events.Key):
    ...
    if not await self._check_bindings(event.key, priority=True):
        forward_target = self.focused or self.screen
        forward_target._forward_event(event)
```
→ 도킹된 `Input`이 `screen.focused`가 되어야만 그 위젯이 키를 받는다. 아니면
키는 현재 포커스 위젯으로 가고, 처리되지 않으면 버블링되어 `App.on_key`로 온다.

### 2) AUTO_FOCUS 가 포커스를 "첫 focusable 위젯"으로 되돌린다
`textual/app.py`: `AUTO_FOCUS: ClassVar[str | None] = "*"`
`textual/screen.py` (`_update_auto_focus`):
```python
if auto_focus and self.focused is None:
    for widget in self.query(auto_focus):
        if widget.focusable:
            self.set_focus(widget); break   # compose 순서상 '첫' focusable
```
→ 화면 compose / 모달 닫힘(resume) / resize 등으로 포커스가 비는 순간,
`compose()`에서 가장 먼저 yield된 focusable 위젯으로 포커스가 간다. 우리 앱에서
그건 전체화면 `MultiplexerView`다.

### 3) 실측 (헤드리스 run_test)
```
mount 직후      : MultiplexerView
메뉴(모달) 중   : ListView          # 모달은 포커스가 잘 걸림
메뉴 닫은 직후  : MultiplexerView    # AUTO_FOCUS 가 뷰로 되돌림
```

## 왜 `Input` 이 실패했나

- pytmux 구조 = "전체화면 합성 뷰가 항상 포커스를 쥐고, `App.on_key`가 키를 셸로
  포워딩." 셸 입력이 되려면 **뷰가 포커스를 가져야** 한다.
- 도킹 `Input`을 `focus()` 해도, 초기 Resize·모달 닫힘·재compose 등으로 포커스가
  비는 순간 **AUTO_FOCUS 가 즉시 첫 focusable(=뷰)로 포커스를 가져간다.** 그러면
  키는 다시 뷰 → `App.on_key`로 가고 `Input`은 키를 못 받는다.
- `compose` 순서를 `Input` 먼저로 바꿔 AUTO_FOCUS 가 `Input`을 잡게 하면, 이번엔
  평소 셸 입력(뷰 포커스 필요)이 깨진다. **둘은 양립 불가.**
- 추가로 `Input`의 증분 repaint 가 우리 30Hz 풀프레임 재그리기와 경쟁해 값이
  갱신돼도 화면에 안 뜨는 문제도 있었다.

### 함정: 헤드리스에서는 통과한다
`App.run_test()` 헤드리스 드라이버는 초기 Resize/포커스 인·아웃 이벤트와 갱신
타이밍이 실제 터미널과 달라, `focus()`가 그대로 유지되어 버그가 드러나지 않았다.
→ **포커스가 필요한 메인-스크린 위젯은 헤드리스 통과만으로 안전하다고 믿지 말 것.**

## 채택한 방식 (현재)

위 규칙 (a)에 따라, **명령/이름변경/검색 등 한 줄 입력은 `Input` 위젯을 담은
바닥 고정 모달 `PromptScreen`(ModalScreen)으로 받는다.** 모달은 별도 스크린으로
push 되어 포커스가 안정적이므로(메인 뷰/AUTO_FOCUS 와 경쟁하지 않음) 스톡 `Input`
을 그대로, 안전하게 쓸 수 있다. 실측: `app.focused` 가 모달의 `Input` 이 된다.

- **입력/편집**: Textual `Input` 이 담당(커서 이동·중간 편집 등 기본 제공).
- **자동완성**: `Input(suggester=SuggestFromList([명령이름...]))` 로 회색 고스트 자동완성
  (오른쪽 화살표로 수락).
- **`?` 명령 목록**: 명령 프롬프트에서 `?` 입력 시 `Input.Changed` 에서 감지해
  `CommandListScreen`(ListView 모달)을 띄우고, 선택 결과를 `Input` 값으로 채운다.
- **결과 전달**: `PromptScreen` 은 `dismiss(value)` 로 입력값을 돌려주고,
  `App._prompt_done(purpose, action, value)` 가 용도별로 서버 명령에 매핑한다.

> 이전(중간) 구현은 "StatusBar 직접 렌더 + App.on_key 직접 처리(규칙 b)"였다. 동작은
> 했지만, 결국 규칙 (a)의 모달 `Input` 으로 통일해 표준 위젯의 편집 기능을 얻었다.
> 핵심 교훈은 동일하다: **메인 스크린의 항상-포커스 전체화면 뷰 위에 스톡 입력
> 위젯을 도킹하지 말고, 모달에 담아라.**

## 규칙

1. 메인 스크린에서 **항상 포커스를 쥔 전체화면 뷰**가 있는 한, 같은 스크린에
   포커스가 필요한 스톡 위젯(Input/TextArea 등)을 얹지 말 것.
2. 텍스트 입력이 필요하면 (a) 모달로 띄우거나 (b) 직접 렌더 + `App.on_key`로 처리.
3. 포커스가 얽힌 UI는 헤드리스 테스트만으로 OK 판정하지 말 것.
