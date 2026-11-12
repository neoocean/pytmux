//! **물음의 대답 → 서버 명령** — 사람이 물음 판에 답한 것과 명령 줄 실행기가 인자를 대답으로
//! 넘긴 것(`base::cmdline::Run::Answer`)이 **같은 표**를 지난다(pytmux-572).
//!
//! 종전에는 이 표가 GUI 세션 뷰 안(`SessionView::answered`)에 있었다. 표가 뷰 안에 있으면
//! «그 명령 줄이 서버로 무엇을 내보내나»를 뷰 없이 잴 수 없다 — 정본 픽스처와 줄마다 대조하는
//! `tests/command_line_conformance.rs` 가 이 함수를 그대로 부른다. 뷰는 이제 여기를 부른다.

use base::Prompt;

use crate::command::Command;

/// 물음에 나온 대답이 뜻하는 명령(패리티 G4).
///
/// **빈 대답은 아무 일도 아니다** — 이름을 지우고 Enter 를 친 사람이 탭 이름을
/// 빈 문자열로 만들려던 것은 아니다(서버가 그걸 받으면 탭바에서 그 탭이 사라진 것처럼
/// 보인다).
pub fn answer_command(prompt: Prompt, answer: &str) -> Option<Command> {
    let answer = answer.trim();
    match prompt {
        Prompt::RenameTab => (!answer.is_empty()).then(|| Command::RenameWindow {
            name: answer.to_owned(),
        }),
        // 숫자가 아니면 아무 일도 안 한다 — 짐작해서 0번으로 옮기면 사용자는 자기가
        // 무엇을 했는지 모른다.
        Prompt::MoveTab => answer
            .parse::<usize>()
            .ok()
            .map(|index| Command::MoveWindow { index }),
        Prompt::KillPane => Some(Command::KillPane),
        // 넷 다 하는 일은 같다 — **문구만** 상황을 안다(무엇을 잃는지가 다르다).
        Prompt::KillTab
        | Prompt::KillTabLast
        | Prompt::KillTabLastRemote
        | Prompt::KillTabPinned => Some(Command::KillWindow),
        // 호출부(`apply_answer`)가 판을 연 순간의 `(host, index)` 로 앞에서 처리한다.
        Prompt::DetachRemoteTab => None,
        // 설정은 **서버에 시킬 일이 아니다** — 우리 설정 파일과 이번 판의 키맵을
        // 고치는 것이라 호출부에서 따로 처리한다(위 `Answered` 갈래).
        // 빈 제목은 **되돌리기**다(서버가 기본 제목으로 돌린다) — 지우고 Enter 를
        // 친 사람의 뜻이 그거다.
        Prompt::RenamePane => Some(Command::SetPaneTitle {
            title: answer.to_owned(),
        }),
        // 숫자가 아니면 아무 일도 안 한다 — 짐작해서 0번과 바꾸면 사용자는 자기가
        // 무엇을 했는지 모른다(탭 이동과 같은 규칙).
        Prompt::SwapTab => answer
            .parse::<usize>()
            .ok()
            .map(|index| Command::SwapTab { index }),
        // 플러그인이 물은 것은 **서버 명령이 아니라** 그 플러그인의 액션이라
        // 여기서 명령으로 옮기지 않는다(`answer_plugin_ask` 가 앞에서 가로챈다).
        Prompt::PluginAsk => None,
        // 빈 이름은 아무 일도 아니다 — 이름 없는 배치는 다시 못 찾는다.
        Prompt::SaveTabLayout => (!answer.is_empty()).then(|| Command::SaveTabLayout {
            name: answer.to_owned(),
        }),
        Prompt::LoadTabLayout => (!answer.is_empty()).then(|| Command::LoadTabLayout {
            name: answer.to_owned(),
            new: false,
        }),
        Prompt::LoadTabLayoutNew => (!answer.is_empty()).then(|| Command::LoadTabLayout {
            name: answer.to_owned(),
            new: true,
        }),
        // 빈 대답이 **뜻을 갖는다** — 파이프를 끈다(원격 떼기와 같은 부류).
        Prompt::PipePane => Some(Command::PipePane {
            cmd: answer.to_owned(),
        }),
        Prompt::JoinPane => answer.parse::<usize>().ok().map(|src| Command::JoinPane {
            src,
            horizontal: false,
        }),
        // 서버에 시킬 일이 아니다 — 우리 상태줄·설정 파일이라 호출부가 처리한다.
        Prompt::DisplayMessage | Prompt::DefaultPath | Prompt::ClaudeCommand | Prompt::SetOption
        | Prompt::SendKeys | Prompt::BindKey | Prompt::UnbindKey
        | Prompt::RunShell | Prompt::IfShell | Prompt::DisplayPopup => None,
        // 상태줄 넷과 커서 색은 **설정 파일**이 주인이라 서버에 시킬 일이 없다.
        Prompt::StatusLeft | Prompt::StatusRight | Prompt::StatusBg | Prompt::StatusFg
        | Prompt::CursorColor | Prompt::FontFamily => None,
        // 훅도 클라 안에서 끝난다(`base::hooks`).
        Prompt::SetHook => None,
        Prompt::SetPrefix => None,
        Prompt::KillServer => Some(Command::KillServer),
        // 재시작 둘은 아래 갈래에서 이미 처리된다(부수효과가 둘이라 명령 하나로
        // 못 옮긴다).
        Prompt::RestartServer | Prompt::RestartAll => None,
        // 빈 host 는 아무 일도 아니다 — 어느 상자에 붙을지 모르는 채로 보내면
        // 서버가 빈 이름으로 ssh 를 띄운다.
        // `C via B`(다중홉) 문법까지 여기서 갈린다 — 파싱은 proto 한 곳이다.
        Prompt::RemoteAttach => Command::remote_attach(answer),
        Prompt::RemoteNewTab => (!answer.is_empty()).then(|| Command::RemoteNewTab {
            host: answer.to_owned(),
        }),
        // 떼기는 **빈 값이 곧 뜻**이다(전부) — 위 둘과 갈리는 자리다.
        Prompt::RemoteDetach => Some(Command::RemoteDetach {
            host: answer.to_owned(),
            // 팔레트 `remote-detach [host]` 는 정본처럼 호스트 전체다(index 없음).
            index: None,
        }),
        // 빈 대답은 검색이 아니다(파이썬도 값이 있어야 보낸다). 새 검색은 위(과거)
        // 방향부터 — `/` 물음의 문구가 그 약속이다.
        Prompt::SearchScrollback => (!answer.is_empty()).then(|| Command::Search {
            query: Some(answer.to_owned()),
            down: false,
        }),
        // 빈 대답은 검색이 아니다(정본도 값이 있어야 보낸다). `awaiting_search_all`
        // 는 이 명령을 실제로 큐에 넣는 호출부(§`Action::SearchAll` 처리)가 켠다 —
        // 여기서는 명령 모양만 짓는다.
        Prompt::SearchAll => (!answer.is_empty())
            .then(|| Command::SearchAll { query: answer.to_owned() }),
    }
}
