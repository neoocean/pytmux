//! 서버가 보내 주는 블록 — 명령 한 번의 실행.
//!
//! # 서버가 권위다
//!
//! 경계를 클라가 추정하지 않는다. 서버의 `plugins/blocks` 가 셸 통합(OSC 133)으로
//! 판정한 것을 그대로 받는다 — 두 클라(파이썬·네이티브)가 각자 추정하면 서로 다른
//! 블록을 보게 된다.
//!
//! # 경계의 출처는 둘, 소비자는 하나 (pytmux-21)
//!
//! Claude 패널에는 OSC 133 이 안 온다(Claude 는 OSC 를 안 보낸다). 대신 서버가 화면
//! 글의 프롬프트 마커로 **턴** 경계를 잡아 같은 메시지로 보낸다
//! (`plugins/claude-code/promptblocks.py`). ★ **클라는 그 차이를 모른다** — 고르기·
//! 강조·복사가 한 벌로 남는 것이 이 설계의 값이다. 여기서 갈리는 것은 `state` 하나뿐이고
//! (`turn`), 그건 "성패를 가질 수 없는 블록"이라는 뜻이다.
//!
//! # 능력 광고가 필요하다
//!
//! 서버는 `hello` 에 `caps: ["blocks"]` 를 실은 클라에게만 이 메시지를 보낸다. 광고하지
//! 않으면 블록이 오지 않는다 — 기능이 조용히 안 되는 것처럼 보이므로, 광고는
//! [`Hello`](crate::message::Hello) 가 기본으로 한다.
//!
//! # 좌표는 절대 행이다
//!
//! `start`/`end` 는 스크롤백 **절대** 행 번호다. 뷰포트가 움직여도 안 변하므로, 스크롤한
//! 뒤에도 블록이 제자리를 가리킨다. `screen` 메시지의 `top` 과 함께 쓰면 지금 화면의
//! 어느 줄에 해당하는지 계산할 수 있다.

use serde::Deserialize;

/// 블록의 진행 상태. 서버가 문자열로 보낸다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BlockState {
    /// 프롬프트가 떴고 아직 명령이 시작되지 않았다.
    Prompt,
    Running,
    Done,
    /// Claude 패널의 **한 턴**(프롬프트 하나 + 그 답). 종료코드가 **없는 부류**다 —
    /// 셸 명령처럼 성패로 끝나지 않는다(모듈 머리말 「경계의 출처는 둘」).
    Turn,
}

impl BlockState {
    fn parse(value: &str) -> Self {
        match value {
            "running" => BlockState::Running,
            "done" => BlockState::Done,
            "turn" => BlockState::Turn,
            // 모르는 값은 "아직 진행 중"으로 본다 — 끝났다고 넘겨짚는 쪽이 더 나쁘다
            // (종료코드 없는 블록이 성공처럼 보인다).
            _ => BlockState::Prompt,
        }
    }
}

/// 명령 한 번의 실행.
#[derive(Debug, Clone, PartialEq)]
pub struct Block {
    /// 사용자가 친 명령. 서버가 아직 못 알아낸 경우 비어 있다.
    pub command: String,
    pub state: BlockState,
    /// 종료코드. `None` 은 **모른다**는 뜻이지 성공이 아니다.
    ///
    /// `i64` 인 이유: Windows 의 종료코드는 DWORD 라 `0xC0000005`(=3221225477) 처럼
    /// `i32` 를 넘는 값이 정상적으로 온다. `i32` 로 받던 동안에는 그런 값 하나가
    /// **프레임 전체의 역직렬화를 실패**시켜 그 패널의 블록 표시가 조용히 멈췄다
    /// (서버는 그 범위를 정상으로 인정한다 — `plugins/blocks/segment.py::_parse_exit`).
    pub exit: Option<i64>,
    pub cwd: Option<String>,
    /// 스크롤백 절대 행. 뷰포트와 무관하다.
    pub start_row: usize,
    pub end_row: Option<usize>,
}

/// 블록이 어떤 **부류**로 보여야 하는가. 구체적인 색은 뷰가 정한다.
///
/// # 왜 부류를 여기서 정하나
///
/// 종전에는 `badge()`(여기)와 `block_color()`(TUI 뷰)가 **각자** `(state, exit)` 를
/// match 했다. 같은 표를 두 번 적은 것이라, 한쪽만 고치면 표식과 색이 어긋난다 —
/// 그리고 그건 조용하다(화면에 `ok` 가 빨갛게 뜨는 식이라 "테마가 이상한가" 로 읽힌다).
/// GUI 가 세 번째 소비자가 되는 자리에서 표를 하나로 모은다.
///
/// **색 자체는 여기 없다.** TUI 는 팔레트 이름을 터미널에 넘기고(사용자 테마가 실제
/// RGB 를 정한다) GUI 는 물려받을 테마가 없어 구체적인 값을 든다 — 그건 뷰의 몫이다.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tone {
    /// 끝났고 종료코드 0.
    Ok,
    /// 끝났고 종료코드가 0 이 아니다.
    Failed,
    /// 끝났는데 **종료코드를 모른다**. 성공도 실패도 아니다.
    Unknown,
    Running,
    /// 프롬프트만 떴다(아직 명령이 없다).
    Idle,
    /// Claude 한 턴. **성패를 묻지 않는다** — `Unknown`(=끝났는데 코드를 모른다)과
    /// 갈라 두는 이유가 그것이다. 뭉치면 요약 판에 `??` 가 줄줄이 떠서, 아무 문제도
    /// 없는 대화가 "뭔가 잘못됐다"로 읽힌다.
    Turn,
}

impl Block {
    /// 성공으로 끝났는가. 아직 안 끝났거나 종료코드를 모르면 `None`.
    pub fn succeeded(&self) -> Option<bool> {
        match self.tone() {
            Tone::Ok => Some(true),
            Tone::Failed => Some(false),
            _ => None,
        }
    }

    /// 표시 부류. **`(state, exit)` 를 읽는 곳은 여기 하나**다.
    pub fn tone(&self) -> Tone {
        match (self.state, self.exit) {
            (BlockState::Done, Some(0)) => Tone::Ok,
            (BlockState::Done, Some(_)) => Tone::Failed,
            // 끝났는데 코드를 모르는 경우 — 성공/실패 어느 쪽으로도 넘겨짚지 않는다.
            (BlockState::Done, None) => Tone::Unknown,
            (BlockState::Running, _) => Tone::Running,
            (BlockState::Prompt, _) => Tone::Idle,
            // 턴은 종료코드를 **가질 수 없다**. 혹시 서버가 실어 보내도 무시한다 —
            // 그 값의 뜻을 우리가 모르므로 성패로 칠하는 것이 곧 거짓말이다.
            (BlockState::Turn, _) => Tone::Turn,
        }
    }

    /// 상태를 나타내는 짧은 표식. 색은 뷰가 정한다.
    pub fn badge(&self) -> &'static str {
        match self.tone() {
            Tone::Ok => "ok",
            Tone::Failed => "err",
            Tone::Unknown => "??",
            Tone::Running => "···",
            Tone::Idle => "…",
            // 화면에서 그 턴이 시작되는 자리에 실제로 찍혀 있는 글자다 — 표식과 패널을
            // 눈으로 잇는 데 이보다 나은 것이 없다.
            Tone::Turn => "❯",
        }
    }

    /// 화면에 적을 명령 텍스트. 서버가 아직 못 알아냈으면 그렇다고 말한다.
    ///
    /// 빈 문자열을 그대로 그리면 그 줄은 표식만 남아 **무엇이 도는지 모르는 채**
    /// 자리를 차지한다. 두 뷰가 각자 이 판단을 하면 한쪽만 빈 줄을 그린다.
    pub fn command_text(&self) -> &str {
        if self.command.is_empty() {
            base::i18n::t("(명령 미상)")
        } else {
            &self.command
        }
    }
}

/// 블록 하나가 차지하는 **절대 행 범위**(양끝 포함). 목록 밖이면 `None`.
///
/// # `end_row` 를 그대로 못 쓰는 이유 (서버 `segment.py` 실측)
///
/// 서버는 `end_row` 를 **다음 프롬프트가 뜰 때** 채운다(`_on_prompt_start`: 직전 블록의
/// `end_row = row` 를 적고 **같은 `row` 로** 새 블록을 시작한다). 즉 그 값은 이 블록의
/// 마지막 줄이 아니라 **다음 블록의 첫 줄**이다 — 그대로 쓰면 복사한 글 끝에 다음
/// 프롬프트 한 줄이 딸려 온다. 그리고 명령이 끝났을 때(`OSC 133;D`)는 아직 안 채워지므로
/// **끝난 블록에도 `None` 이 정상**이다.
///
/// 그래서 끝을 셋에서 고른다: ⑴ `end_row` ⑵ 다음 블록의 시작 ⑶ 그것도 없으면
/// `live_bottom`(= 지금 살아 있는 마지막 줄 — 마지막 블록은 아직 자라는 중이다).
/// ⑴·⑵ 는 **한 줄 앞**이 이 블록의 끝이고, 프롬프트에서 그냥 Enter 를 친 경우처럼
/// 시작과 끝이 같은 줄이면 그 한 줄이 곧 블록이다.
pub fn row_span(blocks: &[Block], index: usize, live_bottom: usize) -> Option<(usize, usize)> {
    let start = blocks.get(index)?.start_row;
    let next = blocks[index]
        .end_row
        .or_else(|| blocks.get(index + 1).map(|b| b.start_row));
    let end = match next {
        Some(row) => row.saturating_sub(1).max(start),
        None => live_bottom.max(start),
    };
    Some((start, end))
}

/// 와이어 형태. 값이 없는 필드는 서버가 보내지 않는다.
///
/// 숫자 필드를 `Value` 로 받는 이유: **한 필드가 이상하면 그 필드만 버려야 하고, 프레임
/// 전체를 잃어선 안 된다.** 강타입으로 받으면 범위·부호가 어긋나는 값 하나가 메시지
/// 역직렬화를 실패시켜 그 패널의 블록 표시가 **조용히** 멈춘다(실측: Windows 종료코드
/// `0xC0000005` 가 `i32` 를 넘겨 프레임이 통째로 버려졌다). 서버가 경계에서 정규화하지만
/// (`segment.py`·`serverremote._sanitize_blocks`) 클라가 붙는 상대가 항상 그 버전은 아니다.
#[derive(Debug, Deserialize)]
pub(crate) struct BlockWire {
    #[serde(default)]
    cmd: String,
    #[serde(default)]
    state: String,
    #[serde(default)]
    exit: Option<serde_json::Value>,
    #[serde(default)]
    cwd: Option<String>,
    #[serde(default)]
    start: Option<serde_json::Value>,
    #[serde(default)]
    end: Option<serde_json::Value>,
}

/// 와이어 정수 → `i64`. 정수가 아니거나 `i64` 를 넘으면 `None`(= 모른다).
fn wire_i64(value: &Option<serde_json::Value>) -> Option<i64> {
    value.as_ref()?.as_i64()
}

/// 와이어 정수 → 행 번호. 음수는 0 으로 접는다(행은 음수가 될 수 없다).
fn wire_row(value: &Option<serde_json::Value>) -> Option<usize> {
    Some(wire_i64(value)?.max(0) as usize)
}

/// 화면에 그려지는 문자열에서 C0/C1/DEL 을 공백으로 접는다.
///
/// 이 값들은 **패널 안의 아무 프로그램**이 만든 것이다(`OSC 633;E` 명령 텍스트·`OSC 7`
/// cwd). 서버가 같은 방어를 하지만, 클라가 붙는 상대가 그 버전이라는 보장은 없고 뷰는
/// 이 문자열을 셀에 그대로 써 넣는다(ratatui 는 제어문자를 걸러 주지 않는다) — 그러면
/// 사용자 단말에 이스케이프가 주입된다. 지우지 않고 공백으로 바꾸는 것은 여러 줄 명령이
/// 한 단어로 붙지 않게 하려는 것이다(서버 `_sanitize_cmd` 와 같은 규율).
fn fold_control(text: &str) -> String {
    text.chars()
        .map(|c| {
            let n = c as u32;
            if n < 0x20 || (0x7f..=0x9f).contains(&n) {
                ' '
            } else {
                c
            }
        })
        .collect()
}

impl From<BlockWire> for Block {
    fn from(w: BlockWire) -> Self {
        Block {
            command: fold_control(&w.cmd),
            state: BlockState::parse(&w.state),
            exit: wire_i64(&w.exit),
            cwd: w.cwd.as_deref().map(fold_control),
            start_row: wire_row(&w.start).unwrap_or(0),
            end_row: wire_row(&w.end),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::message::ServerMessage;

    fn parse(raw: &str) -> (i64, Vec<Block>) {
        let msg: ServerMessage = serde_json::from_str(raw).unwrap();
        match msg {
            ServerMessage::Blocks { pane, blocks } => (pane, blocks),
            other => panic!("blocks 로 안 갈렸다: {other:?}"),
        }
    }

    #[test]
    fn parses_the_shape_the_server_sends() {
        let (pane, blocks) = parse(
            r#"{"t":"blocks","pane":3,"blocks":[
                {"cmd":"ls -la","state":"done","exit":0,"cwd":"/tmp","start":10,"end":12}]}"#,
        );
        assert_eq!(pane, 3);
        assert_eq!(blocks.len(), 1);
        let b = &blocks[0];
        assert_eq!(b.command, "ls -la");
        assert_eq!(b.state, BlockState::Done);
        assert_eq!(b.exit, Some(0));
        assert_eq!(b.cwd.as_deref(), Some("/tmp"));
        assert_eq!((b.start_row, b.end_row), (10, Some(12)));
    }

    #[test]
    fn absent_fields_are_absent_not_defaulted_to_success() {
        // 서버는 값이 없는 필드를 아예 안 보낸다. exit 없음 = 모른다.
        let (_, blocks) = parse(r#"{"t":"blocks","pane":1,"blocks":[{"state":"done"}]}"#);
        let b = &blocks[0];
        assert_eq!(b.exit, None);
        assert_eq!(b.succeeded(), None, "모르는 것을 성공으로 넘겨짚지 않는다");
        assert_eq!(b.badge(), "??");
    }

    #[test]
    fn exit_codes_split_success_from_failure() {
        let (_, blocks) = parse(
            r#"{"t":"blocks","pane":1,"blocks":[
                {"state":"done","exit":0},{"state":"done","exit":127}]}"#,
        );
        assert_eq!(blocks[0].succeeded(), Some(true));
        assert_eq!(blocks[0].badge(), "ok");
        assert_eq!(blocks[1].succeeded(), Some(false));
        assert_eq!(blocks[1].badge(), "err");
    }

    #[test]
    fn running_block_is_not_reported_as_finished() {
        let (_, blocks) = parse(r#"{"t":"blocks","pane":1,"blocks":[{"state":"running"}]}"#);
        assert_eq!(blocks[0].state, BlockState::Running);
        assert_eq!(blocks[0].succeeded(), None);
        assert_eq!(blocks[0].badge(), "···");
    }

    #[test]
    fn a_claude_turn_is_neither_success_nor_failure() {
        // Claude 패널의 블록은 프롬프트 마커로 잘린 **턴**이라 종료코드가 없다.
        // `done`+코드없음(= `??` 노랑)으로 뭉치면 아무 문제 없는 대화가 줄줄이
        // "뭔가 잘못됐다"로 보인다(`promptblocks.py` 의 같은 근거).
        let (_, blocks) = parse(
            r#"{"t":"blocks","pane":1,"blocks":[{"cmd":"테스트 돌려줘","state":"turn","start":7}]}"#,
        );
        assert_eq!(blocks[0].state, BlockState::Turn);
        assert_eq!(blocks[0].tone(), Tone::Turn);
        assert_eq!(blocks[0].succeeded(), None);
        assert_eq!(blocks[0].badge(), "❯");
        assert_eq!(blocks[0].command_text(), "테스트 돌려줘");
    }

    #[test]
    fn a_turn_never_borrows_an_exit_code() {
        // 턴에 종료코드가 실려 와도 성패로 칠하지 않는다 — 그 값의 뜻을 모른다.
        let (_, blocks) =
            parse(r#"{"t":"blocks","pane":1,"blocks":[{"state":"turn","exit":0}]}"#);
        assert_eq!(blocks[0].tone(), Tone::Turn);
        assert_eq!(blocks[0].succeeded(), None);
    }

    #[test]
    fn unknown_state_is_treated_as_in_progress() {
        // 서버가 상태 이름을 늘려도 "끝났다"로 넘겨짚지 않는다.
        let (_, blocks) = parse(r#"{"t":"blocks","pane":1,"blocks":[{"state":"미래상태"}]}"#);
        assert_eq!(blocks[0].state, BlockState::Prompt);
        assert_eq!(blocks[0].succeeded(), None);
    }

    #[test]
    fn empty_block_list_parses() {
        let (_, blocks) = parse(r#"{"t":"blocks","pane":1,"blocks":[]}"#);
        assert!(blocks.is_empty());
    }
}

#[cfg(test)]
mod boundary_tests {
    //! 경계 정독(검수 2026-07-27g §5-1): 이 크레이트는 **서버가 보낸 것을 소비**하는
    //! 쪽이고, 그 값들의 출처는 결국 패널 안에서 도는 아무 프로그램이다.
    use super::*;
    use crate::message::ServerMessage;

    fn blocks(raw: &str) -> Vec<Block> {
        match serde_json::from_str::<ServerMessage>(raw).expect("프레임이 파싱돼야 한다") {
            ServerMessage::Blocks { blocks, .. } => blocks,
            other => panic!("blocks 로 안 갈렸다: {other:?}"),
        }
    }

    #[test]
    fn windows_exit_codes_do_not_kill_the_frame() {
        // 0xC0000005(액세스 위반) 은 Windows 에서 정상적으로 나오는 종료코드이고
        // 서버도 그 범위를 인정한다. i32 로 받던 동안엔 이 한 값이 프레임 전체를
        // 버려 그 패널의 블록 표시가 조용히 멈췄다.
        let b = blocks(r#"{"t":"blocks","pane":1,"blocks":[{"state":"done","exit":3221225477}]}"#);
        assert_eq!(b[0].exit, Some(3_221_225_477));
        assert_eq!(b[0].badge(), "err", "0 이 아니면 실패로 보인다");
    }

    #[test]
    fn absurd_numbers_lose_only_their_own_field() {
        // 서버는 경계에서 정규화하지만 클라가 붙는 상대가 항상 그 버전은 아니다.
        // 이상한 값 하나가 **그 필드만** 잃고, 나머지는 살아야 한다.
        let b = blocks(
            r#"{"t":"blocks","pane":1,"blocks":[
                {"cmd":"ls","state":"done","exit":99999999999999999999,
                 "start":-5,"end":"nope","cwd":"/tmp"}]}"#,
        );
        assert_eq!(b[0].command, "ls");
        assert_eq!(b[0].exit, None, "범위 밖 종료코드는 '모른다'");
        assert_eq!(b[0].badge(), "??", "모르는 것을 성공/실패로 넘겨짚지 않는다");
        assert_eq!(b[0].start_row, 0, "음수 행은 0 으로 접는다");
        assert_eq!(b[0].end_row, None);
        assert_eq!(b[0].cwd.as_deref(), Some("/tmp"));
    }

    #[test]
    fn control_chars_never_reach_the_view() {
        // 뷰는 이 문자열을 셀에 그대로 써 넣는다(ratatui 는 제어문자를 안 거른다) →
        // 살아남으면 사용자 단말에 이스케이프 주입이다. 글자는 지우지 않는다.
        let b = blocks(
            "{\"t\":\"blocks\",\"pane\":1,\"blocks\":[{\"cmd\":\"echo \\u001b]0;pwned\\u0007\",\
             \"cwd\":\"/t\\u009bmp\",\"state\":\"done\"}]}",
        );
        assert!(!b[0].command.contains('\u{1b}'), "{:?}", b[0].command);
        assert!(!b[0].command.contains('\u{7}'), "{:?}", b[0].command);
        assert!(b[0].command.contains("pwned"), "글자까지 지울 필요는 없다");
        let cwd = b[0].cwd.as_deref().unwrap();
        assert!(!cwd.contains('\u{9b}'), "{cwd:?}");
    }

    #[test]
    fn multiline_command_does_not_glue_words() {
        let b = blocks(
            "{\"t\":\"blocks\",\"pane\":1,\"blocks\":[{\"cmd\":\"echo a\\necho b\",\"state\":\"done\"}]}",
        );
        assert_eq!(b[0].command, "echo a echo b");
    }

    // ── 행 범위(pytmux-18) ───────────────────────────────────────────────────
    //
    // 이 산수가 틀리면 **강조된 것과 복사되는 것이 어긋난다** — 화면은 맞고 클립보드만
    // 틀리는 부류라 눈으로는 못 잡는다.

    /// 시작·끝만 든 블록(나머지는 이 계산과 무관하다).
    fn at(start: usize, end: Option<usize>) -> Block {
        Block {
            command: String::new(),
            state: BlockState::Done,
            exit: None,
            cwd: None,
            start_row: start,
            end_row: end,
        }
    }

    #[test]
    fn the_end_row_is_the_next_prompt_so_the_block_stops_one_line_earlier() {
        // 서버는 다음 프롬프트가 뜬 **그 행**을 `end` 로 적는다(`segment.py`
        // `_on_prompt_start`). 그대로 쓰면 복사한 글 끝에 다음 프롬프트가 딸려 온다.
        let blocks = [at(10, Some(15))];
        assert_eq!(row_span(&blocks, 0, 999), Some((10, 14)));
    }

    #[test]
    fn a_finished_block_without_an_end_borrows_the_next_blocks_start() {
        // `OSC 133;D`(끝) 만 온 블록은 `end` 가 비어 있다 — 다음 프롬프트가 떠야 채워진다.
        // 그때도 뒤 블록이 있으면 경계를 안다.
        let blocks = [at(0, None), at(7, None)];
        assert_eq!(row_span(&blocks, 0, 999), Some((0, 6)));
    }

    #[test]
    fn the_last_block_grows_to_the_live_bottom() {
        // 마지막 블록은 아직 자라는 중이라 끝을 물어볼 데가 없다 — 지금까지 찬 데까지다.
        let blocks = [at(0, None), at(7, None)];
        assert_eq!(row_span(&blocks, 1, 20), Some((7, 20)));
    }

    #[test]
    fn a_one_line_block_never_inverts() {
        // 프롬프트에서 그냥 Enter 를 치면 시작과 끝이 같은 행이다. 한 줄 빼기가
        // 시작보다 앞서면 범위가 뒤집혀 **엉뚱한 데가 복사된다**.
        assert_eq!(row_span(&[at(5, Some(5))], 0, 999), Some((5, 5)));
        // 라이브 하단이 시작보다 위인 순간(창이 막 줄었다)도 같은 보호를 받는다.
        assert_eq!(row_span(&[at(5, None)], 0, 2), Some((5, 5)));
    }

    #[test]
    fn asking_past_the_end_of_the_list_is_not_a_panic() {
        assert_eq!(row_span(&[], 0, 9), None);
        assert_eq!(row_span(&[at(0, None)], 3, 9), None);
    }
}
