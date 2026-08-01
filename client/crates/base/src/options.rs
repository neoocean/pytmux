//! **인자 폼** — 선택지가 정해진 인자를 방향키로 고른다(파이썬 `CommandOptionsScreen`).
//!
//! # 왜 물음이 아니라 화면인가
//!
//! 우리는 지금까지 인자를 **한 줄 물음**으로 받았다. 그건 자유 텍스트에는 맞지만
//! `split-window` 의 `-h`/`-v` 같이 **답이 둘뿐인** 자리에는 두 가지가 나쁘다:
//!
//! 1. 무엇을 칠 수 있는지 화면이 안 알려 준다(`-h` 인지 `h` 인지 `horizontal` 인지).
//! 2. 오타가 조용히 아무 일도 안 하는 명령이 된다.
//!
//! 파이썬 정본의 `COMMAND_OPTIONS` 표와 **같은 문구·같은 값**을 쓴다. 문구가 다르면
//! 사용자가 눈으로 못 찾고, 값이 다르면 서버가 조용히 무시한다.
//!
//! # 켜기·끄기가 토글과 갈리는 자리 (그리고 여섯 번째 오판을 면한 자리)
//!
//! 우리 클라는 `synchronize-panes` 류를 **토글로만** 보낼 수 있었다. "값을 정해 보내려면
//! 서버가 값을 받아야 한다"고 적을 뻔했는데, 서버를 열어 보니 **처음부터 받고 있었다** —
//! `servertree.set_sync(sess, value=None)` 은 값이 없으면 뒤집고 있으면 그 값으로 정한다.
//! 우리가 `value` 를 안 실어 보냈을 뿐이다. (규칙: 없다고 적기 전에 서버를 연다.)
//!
//! 그래서 켜기/끄기 선택지는 정본의 `_ONOFF` 와 같이 **셋**이다 — `토글`·`켜기`·`끄기`.

use crate::keymap::{Action, Dir, EnumOpt, ServerOpt};

/// 선택지 하나. `label` 은 사람이 읽는 것, `value` 는 명령줄에 실리는 것.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Choice {
    pub label: &'static str,
    pub value: &'static str,
}

const fn c(label: &'static str, value: &'static str) -> Choice {
    Choice { label, value }
}

/// 한 명령의 인자 하나(=화면의 한 줄).
#[derive(Debug, Clone, Copy)]
pub struct OptionSpec {
    pub label: &'static str,
    pub choices: &'static [Choice],
}

/// 인자 폼이 있는 명령 하나.
#[derive(Debug, Clone, Copy)]
pub struct CommandOptions {
    pub command: &'static str,
    pub specs: &'static [OptionSpec],
}

/// 정본 `_ONOFF` 와 같은 셋. **첫째가 토글**이라 값이 비어 있다.
static ONOFF: &[Choice] = &[c("토글", ""), c("켜기", "on"), c("끄기", "off")];

/// 인자가 하나뿐인 명령이 대부분이라 짧게 쓴다. 여럿이 필요해지면 배열을 직접 적는다.
///
/// 함수가 아니라 매크로인 이유: `&[…]` 를 함수에서 돌려주면 그 배열은 **함수의 지역
/// 임시값**이라 `'static` 이 아니다. 매크로는 부르는 자리(정적 초기화)에서 펴지므로
/// 같은 배열이 그대로 승격된다.
macro_rules! one {
    ($label:expr, $choices:expr) => {
        &[OptionSpec {
            label: $label,
            choices: $choices,
        }]
    };
}

/// 인자 폼 표 — **우리가 실제로 그 값을 보낼 수 있는 것만** 싣는다.
///
/// 못 하는 이름을 목록에 두면 고르는 순간 아무 일도 안 일어나고, 그건 "명령이 있는데 안
/// 먹는다"로 읽힌다(팔레트와 같은 규칙).
pub static COMMAND_OPTIONS: &[CommandOptions] = &[
    CommandOptions {
        command: "split-window",
        specs: one!(
            "방향",
            &[c("좌우 분할 │ (-h)", "-h"), c("상하 분할 ─ (-v)", "-v")]
        ),
    },
    CommandOptions {
        command: "select-pane",
        specs: one!(
            "이동",
            &[
                c("◀ 왼쪽", "-L"),
                c("▶ 오른쪽", "-R"),
                c("▲ 위", "-U"),
                c("▼ 아래", "-D"),
            ]
        ),
    },
    CommandOptions {
        command: "resize-pane",
        specs: one!(
            "동작",
            &[
                c("줌 토글 ⛶", "-Z"),
                c("◀ 왼쪽", "-L"),
                c("▶ 오른쪽", "-R"),
                c("▲ 위", "-U"),
                c("▼ 아래", "-D"),
            ]
        ),
    },
    CommandOptions {
        command: "capture-pane",
        specs: one!("범위", &[c("보이는 영역", ""), c("스크롤백 전체 -S", "-S")]),
    },
    CommandOptions {
        command: "synchronize-panes",
        specs: one!("동기화", ONOFF),
    },
    CommandOptions {
        command: "monitor-activity",
        specs: one!("활동", ONOFF),
    },
    CommandOptions {
        command: "monitor-bell",
        specs: one!("벨", ONOFF),
    },
    CommandOptions {
        command: "automatic-rename",
        specs: one!("자동이름", ONOFF),
    },
    CommandOptions {
        command: "inactive-dim",
        specs: one!("비활성흐리게", ONOFF),
    },
    CommandOptions {
        command: "strip-box-drawing",
        specs: one!("테두리제거", ONOFF),
    },
    CommandOptions {
        command: "inactive-dim-ratio",
        specs: one!(
            "흐리게세기",
            &[
                c("아주 옅게 0.10", "0.10"),
                c("옅게 0.18", "0.18"),
                c("보통 0.30", "0.30"),
                c("진하게 0.45", "0.45"),
            ]
        ),
    },
    CommandOptions {
        command: "single-border",
        specs: one!("단일테두리", ONOFF),
    },
    CommandOptions {
        command: "coalesce-repaints",
        specs: one!("리페인트합치기", ONOFF),
    },
    CommandOptions {
        command: "nest-auto-attach",
        specs: one!("중첩자동승격", ONOFF),
    },
    CommandOptions {
        command: "exit-empty",
        specs: one!("세션0개시종료", ONOFF),
    },
    CommandOptions {
        command: "win-mouse-motion",
        specs: one!("윈도우모션", ONOFF),
    },
    CommandOptions {
        command: "vt-parser",
        specs: one!("VT파서", &[c("pyte", "pyte"), c("native", "native")]),
    },
    // 파이썬 `COMMAND_OPTIONS["lang"]` 와 같은 두 선택지다. 라벨은 파이썬처럼
    // **각 언어의 자기 이름**이라 어느 로케일에서도 번역하지 않는다(en 표에 없다).
    CommandOptions {
        command: "lang",
        specs: one!("언어", &[c("한국어", "ko"), c("English", "en")]),
    },
    CommandOptions {
        command: "window-size",
        specs: one!(
            "공유크기",
            &[
                c("smallest", "smallest"),
                c("latest", "latest"),
                c("largest", "largest"),
            ]
        ),
    },
];

pub fn options_for(command: &str) -> Option<&'static CommandOptions> {
    COMMAND_OPTIONS.iter().find(|o| o.command == command)
}

/// 화면 한 줄의 글자 — 정본 `format_option_row` 와 같은 모양이다.
///
/// 선택지가 하나뿐이면 화살표를 그리지 않는다(누를 데가 없는 화살표는 거짓말이다).
pub fn row_text(spec: &OptionSpec, selected: usize) -> String {
    let arrows = if spec.choices.len() > 1 { "◀ ▶" } else { "    " };
    // 표(`COMMAND_OPTIONS`)는 const 문맥이라 여기 **소비 지점**에서 번역한다.
    let current = crate::i18n::t(spec.choices.get(selected).map(|c| c.label).unwrap_or(""));
    format!("{}:  {arrows}  {current}", crate::i18n::t(spec.label))
}

/// 지금 고른 값들로 만든 **명령줄**. 화면 아래에 그대로 보인다.
///
/// 빈 값(`토글`·`보이는 영역`)은 낱말을 안 붙인다 — 정본과 같다.
pub fn line(options: &CommandOptions, selected: &[usize]) -> String {
    let mut out = String::from(options.command);
    for (spec, sel) in options.specs.iter().zip(selected) {
        if let Some(choice) = spec.choices.get(*sel)
            && !choice.value.is_empty()
        {
            out.push(' ');
            out.push_str(choice.value);
        }
    }
    out
}

/// 고른 것이 뜻하는 일.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OptionPick {
    /// 액션으로 끝난다 — 키로 누른 것과 **같은 길**이다.
    Act(Action),
    /// 우리 설정 파일에 `set <키> <값>`. 서버가 모르는 값들이다.
    Set(&'static str, &'static str),
    /// 우리 설정 파일의 켜고끄기를 **뒤집는다**(지금 값은 설정이 안다).
    Flip(&'static str),
}

/// 고른 값들을 할 일로 옮긴다. 모르는 조합이면 `None`(아무 일도 안 한다).
pub fn pick(options: &CommandOptions, selected: &[usize]) -> Option<OptionPick> {
    let value = options
        .specs
        .first()
        .and_then(|spec| spec.choices.get(*selected.first()?))
        .map(|c| c.value)?;
    // 켜기/끄기 셋 중 무엇을 골랐나. `None` 이면 토글(값이 빈 것).
    let on = match value {
        "on" => Some(true),
        "off" => Some(false),
        _ => None,
    };
    let act = |a: Action| Some(OptionPick::Act(a));
    match options.command {
        "split-window" => act(if value == "-h" {
            Action::SplitLeftRight
        } else {
            Action::SplitTopBottom
        }),
        "select-pane" => act(Action::SelectPane(dir(value)?)),
        "resize-pane" => act(if value == "-Z" {
            Action::Zoom
        } else {
            Action::ResizePane(dir(value)?)
        }),
        "capture-pane" => act(Action::CapturePane(value == "-S")),
        // ── 서버가 값을 받는 것들(모듈 문서 참조). 토글이면 종전 액션 그대로다.
        "synchronize-panes" => act(match on {
            Some(on) => Action::SetSync(on),
            None => Action::ToggleSync,
        }),
        "monitor-activity" => act(match on {
            Some(on) => Action::SetMonitor { bell: false, on },
            None => Action::ToggleMonitorActivity,
        }),
        "monitor-bell" => act(match on {
            Some(on) => Action::SetMonitor { bell: true, on },
            None => Action::ToggleMonitorBell,
        }),
        "automatic-rename" => act(match on {
            Some(on) => Action::SetAutoRename(on),
            None => Action::ToggleAutoRename,
        }),
        "single-border" => server(ServerOpt::SingleBorder, on),
        "coalesce-repaints" => server(ServerOpt::CoalesceRepaints, on),
        "nest-auto-attach" => server(ServerOpt::NestAutoAttach, on),
        "exit-empty" => server(ServerOpt::ExitEmpty, on),
        "win-mouse-motion" => server(ServerOpt::WinMouseMotion, on),
        // ── 우리 설정 파일이 주인인 것들 ────────────────────────────────────
        "inactive-dim" | "strip-box-drawing" => Some(match on {
            Some(on) => OptionPick::Set(options.command, if on { "on" } else { "off" }),
            // ⚠ 토글의 두 갈래가 비대칭인 것은 **남아 있는 이름 때문**이다:
            // `inactive-dim` 에는 옛 액션이 하나 있고 `strip-box-drawing` 에는 없다.
            // 끝에서 하는 일은 둘 다 `config::flip_config` 로 같다 — 액션을 지우는 편이
            // 깔끔하지만 그건 이 슬라이스가 아니라 정리 슬라이스의 일이다.
            None if options.command == "inactive-dim" => {
                OptionPick::Act(Action::ToggleInactiveDim)
            }
            None => OptionPick::Flip(options.command),
        }),
        "inactive-dim-ratio" => Some(OptionPick::Set("inactive-dim-ratio", value)),
        // 로케일 값은 표의 `&'static` 그대로다 — `leak` 이 필요 없다.
        "lang" => act(Action::SetLang(match value {
            "en" => "en",
            _ => "ko",
        })),
        "vt-parser" => act(Action::SetEnum(EnumOpt::VtParser, leak(value)?)),
        "window-size" => act(Action::SetEnum(EnumOpt::WindowSize, leak(value)?)),
        _ => None,
    }
}

fn server(opt: ServerOpt, on: Option<bool>) -> Option<OptionPick> {
    Some(OptionPick::Act(match on {
        Some(on) => Action::SetServerOption(opt, on),
        None => Action::ToggleServerOption(opt),
    }))
}

fn dir(value: &str) -> Option<Dir> {
    Some(match value {
        "-L" => Dir::Left,
        "-R" => Dir::Right,
        "-U" => Dir::Up,
        "-D" => Dir::Down,
        _ => return None,
    })
}

/// 표의 값은 `'static` 인데 여기서는 빌린 문자열이라 다시 표에서 찾아 준다.
///
/// `Box::leak` 같은 것을 쓰지 않는 이유: 그러면 화면을 열 때마다 새는 메모리가 생기고,
/// 무엇보다 **표에 없는 값도 통과한다** — 서버가 모르는 철자가 조용히 나간다.
fn leak(value: &str) -> Option<&'static str> {
    [EnumOpt::VtParser, EnumOpt::WindowSize]
        .iter()
        .flat_map(|opt| opt.choices())
        .find(|known| **known == value)
        .copied()
}

#[cfg(test)]
#[path = "options_tests.rs"]
mod tests;
