//! GUI 세션 뷰에서 **판정이 있는 부분**만 시험한다.
//!
//! 이 크레이트에는 TUI 의 `TestScreen` 에 해당하는 것이 없다 — `Scene` 이 들고 있는
//! 것은 `glyph_key`(폰트별 id)라 글자로 되돌릴 수 없고, 테스트 델리게이트에서는 그마저
//! 가짜다. 그래서 "화면에 무엇이 보이나"는 사람이 본다(설계문서 §7 GUI 따라붙이기).
//!
//! 대신 **색 판정은 순수 함수로 빼 두었으므로** 여기서 전부 잡는다(사용자 결정
//! 2026-07-28: "로직은 밀고 뷰는 얇게"). 이 자리들은 눈으로 보기 가장 어려운 종류다 —
//! 팔레트 한 줄이 복붙으로 어긋나면 그 색만 조용히 틀리고, 반전이 안 풀리면 커서가
//! 사라진다.

use super::*;

fn style(fg: Option<CellColor>, bg: Option<CellColor>, reverse: bool) -> CellStyle {
    CellStyle {
        fg,
        bg,
        reverse,
        ..CellStyle::default()
    }
}

#[test]
fn reverse_swaps_foreground_and_background() {
    // 터미널에서 반전은 장식이 아니라 **신호**다(커서·선택). 안 풀면 그 신호가 사라진다.
    let red = CellColor::Named(NamedColor::Red);
    let blue = CellColor::Named(NamedColor::Blue);
    let (fg, bg) = colors(&style(Some(red), Some(blue), true));
    assert_eq!(fg, named(NamedColor::Blue));
    assert_eq!(bg, Some(named(NamedColor::Red)));
}

#[test]
fn reverse_without_a_background_still_paints_something() {
    // 배경이 없는 셀을 반전하면 전경이 배경이 되는데, 새 전경이 없으면 **글자가
    // 배경색으로 배경 위에** 그려져 사라진다. 캔버스 배경을 전경으로 쓴다.
    let (fg, bg) = colors(&style(Some(CellColor::Named(NamedColor::Green)), None, true));
    assert_eq!(fg, palette::BG, "반전한 글자가 안 보인다");
    assert_eq!(bg, Some(named(NamedColor::Green)));
}

#[test]
fn a_plain_cell_keeps_its_colors() {
    let (fg, bg) = colors(&style(Some(CellColor::Named(NamedColor::Cyan)), None, false));
    assert_eq!(fg, named(NamedColor::Cyan));
    assert_eq!(bg, None);
}

#[test]
fn a_cell_without_a_foreground_uses_the_default_not_black() {
    // 서버는 기본 전경을 안 싣는다(`fg: None`). 0 으로 떨어뜨리면 배경 위에 검은 글자가
    // 되어 화면 절반이 안 보인다.
    let (fg, _) = colors(&style(None, None, false));
    assert_eq!(fg, palette::FG);
}

#[test]
fn rgb_from_the_server_passes_through_untouched() {
    // 24비트 색을 팔레트로 접으면 사용자가 고른 색이 사라진다.
    let (fg, _) = colors(&style(
        Some(CellColor::Rgb {
            r: 1,
            g: 2,
            b: 3,
        }),
        None,
        false,
    ));
    assert_eq!(fg, ColorU { r: 1, g: 2, b: 3, a: 0xff });
}

#[test]
fn every_palette_entry_is_opaque() {
    // 알파가 0 이면 그 색만 **투명하게** 그려진다 — 화면에서는 "그 글자만 안 보인다"로
    // 나타나고, 색이 틀린 것보다 찾기 어렵다.
    for color in ALL_NAMED {
        assert_eq!(named(color).a, 0xff, "{color:?} 가 투명하다");
    }
}

/// ☠ **열여섯 이름은 열여섯 색이라야 한다** — 이 팔레트가 지켜야 하는 하드 계약.
///
/// # 왜 짝 목록을 없앴나 (pytmux-187)
///
/// 종전 이름은 `bright_variants_differ_from_their_base` 였고, 재는 짝을 **손으로 적은
/// 목록**으로 돌았다. 그 목록에 마젠타 한 줄이 **빠져 있었고**, 하필 `MAGENTA` 와
/// `BR_MAGENTA` 가 같은 값이었다 — 즉 오라클은 계속 초록이었는데 그 색만 안 재고
/// 있었다. 목록이 스스로 "일곱 짝만 잰다"고 말해 주지 않는 것이 이 부류의 전부다
/// (테스트 주석은 "16줄짜리 복붙 표라 한 줄이 어긋나기 쉽다"고 적어 놓고, 정작 그
/// 테스트가 같은 방식으로 한 줄을 흘렸다).
///
/// 그래서 짝짓기를 **없앴다**: 손으로 적을 목록이 없으면 거기서 한 줄이 샐 수 없다.
/// 전수(`ALL_NAMED`)를 훑어 값이 겹치는지만 물으면 짝이 같은 경우를 포함해 **더 많이**
/// 잡는다(예: `RED` 를 `BLUE` 자리에 복붙한 것도 여기서 운다). `ALL_NAMED` 자체가 한
/// 줄을 두 번 적어도 여기서 걸린다 — 같은 색이 두 번 나오기 때문이다.
///
/// # 여기서 재는 것은 "겹치면 안 된다" 하나다
///
/// 「밝은 쪽이 더 밝은가」는 옆의 `the_bright_half_of_every_pair_is_actually_brighter`
/// 가 따로 잰다 — 겹침과 뒤집힘은 **부류가 다른 결함**이고(하나는 두 SGR 이 한 색이 되는
/// 것, 하나는 강조가 뜻과 반대로 그려지는 것), 한 오라클에 얹으면 실패 메시지가 어느
/// 쪽인지 안 말한다.
///
/// ⚠ 이 문단에는 종전에 *"「밝은 쪽이 더 밝은가」는 배색 취향이라 안 잰다 · 지금
/// 팔레트도 `BR_CYAN`(#0db9d7)이 `CYAN`(#7dcfff)보다 어둡다"* 가 적혀 있었다. **둘 다
/// 지금은 거짓이다**: 그 유보는 2026-08-23 에 뒤집혔고(어느 표를 고르든 지켜져야 하는
/// 성질이다) 그 값들도 남아 있지 않다 — 팔레트는 xterm 표준으로 갈렸다(pytmux-187 ·
/// 사람의 결정 2026-08-24).
#[test]
fn the_sixteen_palette_names_are_sixteen_colors() {
    let mut seen: std::collections::BTreeMap<(u8, u8, u8, u8), NamedColor> =
        std::collections::BTreeMap::new();
    for color in ALL_NAMED {
        let c = named(color);
        if let Some(prev) = seen.insert((c.r, c.g, c.b, c.a), color) {
            panic!(
                "{prev:?} 와 {color:?} 가 같은 값(#{:02x}{:02x}{:02x})이다 — \
                 그 두 SGR 이 화면에서 한 색이 된다",
                c.r, c.g, c.b
            );
        }
    }
    assert_eq!(seen.len(), ALL_NAMED.len(), "전수를 다 훑지 않았다");
}

/// ☠ **이 열여섯은 xterm 표준 표다** — 배색 취향이 아니라 **사람이 고른 결정**이다
/// (pytmux-187 · 갈림길은 pytmux-391 · 2026-08-24).
///
/// # 왜 값을 못박나
///
/// 이 표를 고른 근거는 「xterm 의 기본 표와 **같다**」 한 줄뿐이다. 한 칸이라도 눈대중으로
/// 옮기면 그 근거가 통째로 사라지는데, 화면에서는 *"색이 좀 안 맞나?"* 정도로만 보여서
/// 아무 오라클도 안 운다 — 이 이슈가 정확히 그 부류로 넉 달을 갔다. 그래서 값 자체를
/// 여기 한 번 더 적어 **밖의 표와 대조 가능한 자리**를 만든다.
///
/// ⛔ **여기가 붉다고 이 목록을 「지금 코드에 맞춰」 고치지 마라.** 답은 둘 중 하나다:
/// ⑴ 팔레트가 표류했다 → **팔레트를 되돌린다** ⑵ 표를 바꾸기로 **사람이 다시 정했다**
/// → 그때는 이 목록과 `palette` 모듈 문서를 **같은 CL 에서** 옮긴다.
///
/// 밖의 근거: xterm 기본 리소스(`XTerm-col.ad` / `charproc.c`)와 X11 `rgb.txt` 의 색이름.
#[test]
fn the_sixteen_colors_are_the_xterm_standard_table() {
    // (SGR, 이름, xterm 리소스 = X11 색이름, 값)
    let table: [(u16, NamedColor, &str, (u8, u8, u8)); 16] = [
        (30, NamedColor::Black, "color0 = black", (0x00, 0x00, 0x00)),
        // ⚠ SGR 34 한 칸만 **어두운 바탕 보정을 거친 값**이다(pytmux-412 ⓐ3). 원본과
        //    규칙은 `the_dark_background_correction_is_the_rule_not_a_taste` 가 잰다.
        (31, NamedColor::Red, "color1 = red3", (0xcd, 0x00, 0x00)),
        (32, NamedColor::Green, "color2 = green3", (0x00, 0xcd, 0x00)),
        (33, NamedColor::Yellow, "color3 = yellow3", (0xcd, 0xcd, 0x00)),
        (34, NamedColor::Blue, "color4 = blue2 + 보정", (0x52, 0x52, 0xf3)),
        (35, NamedColor::Magenta, "color5 = magenta3", (0xcd, 0x00, 0xcd)),
        (36, NamedColor::Cyan, "color6 = cyan3", (0x00, 0xcd, 0xcd)),
        (37, NamedColor::White, "color7 = gray90", (0xe5, 0xe5, 0xe5)),
        (90, NamedColor::BrightBlack, "color8 = gray50", (0x7f, 0x7f, 0x7f)),
        (91, NamedColor::BrightRed, "color9 = red", (0xff, 0x00, 0x00)),
        (92, NamedColor::BrightGreen, "color10 = green", (0x00, 0xff, 0x00)),
        (93, NamedColor::BrightYellow, "color11 = yellow", (0xff, 0xff, 0x00)),
        (94, NamedColor::BrightBlue, "color12 = rgb:5c/5c/ff", (0x5c, 0x5c, 0xff)),
        (95, NamedColor::BrightMagenta, "color13 = magenta", (0xff, 0x00, 0xff)),
        (96, NamedColor::BrightCyan, "color14 = cyan", (0x00, 0xff, 0xff)),
        (97, NamedColor::BrightWhite, "color15 = white", (0xff, 0xff, 0xff)),
    ];
    for (sgr, color, xterm, (r, g, b)) in table {
        let got = named(color);
        assert_eq!(
            (got.r, got.g, got.b),
            (r, g, b),
            "SGR {sgr}({color:?})가 xterm `{xterm}`(#{r:02x}{g:02x}{b:02x}) 이 아니라 \
             #{:02x}{:02x}{:02x} 이다 — 표가 표류했으면 팔레트를 되돌리고, \
             사람이 표를 다시 정한 것이면 이 목록과 `palette` 문서를 같은 CL 에서 옮긴다",
            got.r,
            got.g,
            got.b
        );
    }
    // ⛔ 목록이 전수인가 — 한 줄을 지우면 위 루프는 조용히 통과한다.
    assert_eq!(table.len(), ALL_NAMED.len(), "표가 열여섯 칸을 다 안 적었다");
}

/// ⓐ3 의 **규칙**을 그대로 다시 계산해 팔레트와 맞춰 본다 (pytmux-412 · 사람의 결정
/// 2026-08-31).
///
/// ⛔ **값을 베껴 적는 시험이 아니다.** 위 표는 「지금 값이 이것이다」를 못박고, 이쪽은
/// 「그 값이 **규칙에서 나온다**」를 못박는다 — 둘 다 없으면 다음 사람은 두 칸이 왜
/// xterm 과 다른지 알 길이 없고, 「표류했나 정한 건가」를 못 가린다.
///
/// 규칙(모듈 문서 §어두운 바탕 보정과 **같은 것**):
///   ⑴ `BG` 대비가 3:1 미만인 칸만 손댄다
///   ⑵ **3.1:1**(= 기준선 3.0 + 여유 0.1)을 넘기는 가장 작은 `t`(0.01 단위)로 흰색 쪽에
///      섞는다 — `c' = c + (255-c)·t`. 여유를 두는 이유는 모듈 문서에 있다(3.0 에 딱
///      맞추면 부동소수 한 톨로 값이 갈린다 — 실측으로 파이썬과 러스트가 갈렸다).
///   ⑶ 밝은 짝이 **1.05배 이상 밝아야** 한다. ⛔ 그 휘도는
///      [`the_bright_half_of_every_pair_is_actually_brighter`] 가 쓰는 것과 **같은 식**
///      (감마 인코딩 값 그대로)이라야 한다 — 한 물음을 두 술어로 물으면 이 규칙이
///      통과시킨 값을 저쪽 오라클이 떨어뜨린다(실제로 그렇게 한 번 붉었다).
///   ⑷ ⑵·⑶ 을 함께 못 지키는 칸은 **xterm 값 그대로** 둔다 — 못 고치는 것도 결과다.
#[test]
fn the_dark_background_correction_is_the_rule_not_a_taste() {
    fn channel(v: u8) -> f64 {
        let c = v as f64 / 255.0;
        if c <= 0.03928 { c / 12.92 } else { ((c + 0.055) / 1.055).powf(2.4) }
    }
    /// 대비를 재는 휘도(WCAG — 선형화한다).
    fn wcag(c: ColorU) -> f64 {
        0.2126 * channel(c.r) + 0.7152 * channel(c.g) + 0.0722 * channel(c.b)
    }
    fn contrast(a: ColorU, b: ColorU) -> f64 {
        let (x, y) = (wcag(a), wcag(b));
        let (hi, lo) = if x > y { (x, y) } else { (y, x) };
        (hi + 0.05) / (lo + 0.05)
    }
    fn lighten(c: ColorU, t: f64) -> ColorU {
        let f = |v: u8| (v as f64 + (255.0 - v as f64) * t).round().min(255.0) as u8;
        ColorU { r: f(c.r), g: f(c.g), b: f(c.b), a: 0xff }
    }
    fn rgb(r: u8, g: u8, b: u8) -> ColorU {
        ColorU { r, g, b, a: 0xff }
    }

    // xterm 원본(밝은 바탕용) — 어두운 절반의 유채색 여섯.
    let source: [(&str, NamedColor, ColorU, ColorU); 6] = [
        ("31/91", NamedColor::Red, rgb(0xcd, 0x00, 0x00), named(NamedColor::BrightRed)),
        ("32/92", NamedColor::Green, rgb(0x00, 0xcd, 0x00), named(NamedColor::BrightGreen)),
        ("33/93", NamedColor::Yellow, rgb(0xcd, 0xcd, 0x00), named(NamedColor::BrightYellow)),
        ("34/94", NamedColor::Blue, rgb(0x00, 0x00, 0xee), named(NamedColor::BrightBlue)),
        ("35/95", NamedColor::Magenta, rgb(0xcd, 0x00, 0xcd), named(NamedColor::BrightMagenta)),
        ("36/96", NamedColor::Cyan, rgb(0x00, 0xcd, 0xcd), named(NamedColor::BrightCyan)),
    ];
    let bg = crate::theme::BG;
    let mut touched = 0usize;
    for (pair, name, xterm, bright) in source {
        let want = if contrast(xterm, bg) >= 3.0 {
            xterm // ⑴ 이미 읽힌다 — 한 톨도 안 옮긴다
        } else {
            // ⑶ 의 휘도는 **짝 오라클과 같은 식**이다 — `luminance` 는 이 파일의 그것을
            //    그대로 쓴다(감마 인코딩 값 · 선형화하지 않는다).
            match (0..=100)
                .map(|i| lighten(xterm, i as f64 / 100.0))
                .find(|&c| {
                    contrast(c, bg) >= 3.1
                        && luminance(bright) as f64 >= luminance(c) as f64 * 1.05
                }) {
                Some(c) => {
                    touched += 1;
                    c
                }
                // ⑷ 못 고치는 칸 — xterm 값 그대로. SGR 31 이 그것이다(짝이 순수
                //    `#ff0000` 이라 조금만 밝혀도 31 이 91 을 앞지른다).
                None => xterm,
            }
        };
        let got = named(name);
        assert_eq!(
            (got.r, got.g, got.b),
            (want.r, want.g, want.b),
            "SGR {pair} 의 어두운 칸이 규칙의 값이 아니다 — 규칙이 내는 값은 \
             #{:02x}{:02x}{:02x}({:.2}:1)인데 팔레트는 #{:02x}{:02x}{:02x}({:.2}:1)이다",
            want.r, want.g, want.b, contrast(want, bg),
            got.r, got.g, got.b, contrast(got, bg),
        );
    }
    // ⛔ **규칙이 아무것도 안 하게 되면 이 시험은 장식이다** — 임계값이나 바탕이 움직여
    //    손댈 칸이 없어졌으면 그것은 조용한 통과가 아니라 **말해야 할 사건**이다.
    assert_eq!(touched, 1, "보정이 닿는 칸 수가 하나가 아니다 — 바탕이나 임계가 움직였다");
}

// ── 블록 구역(P4) ────────────────────────────────────────────────────────────

/// 부류 전수. `block_color` 의 match 가 컴파일로 누락을 막지만, 아래 테스트가 실제로
/// 전부를 훑으려면 목록이 필요하다.
const ALL_TONES: [Tone; 6] = [
    Tone::Ok,
    Tone::Failed,
    Tone::Unknown,
    Tone::Running,
    Tone::Idle,
    Tone::Turn,
];

#[test]
fn a_claude_turn_is_not_painted_like_a_verdict() {
    // 턴은 성패 축 밖이다(pytmux-21). 성공·실패·"코드 모름" 중 어느 것과도 같은 색이면
    // 요약 판에서 대화가 판정처럼 읽힌다 — 특히 노랑(`??`)과 겹치면 "뭔가 잘못됐다"다.
    let turn = SessionView::block_color(Tone::Turn);
    for verdict in [Tone::Ok, Tone::Failed, Tone::Unknown] {
        assert_ne!(turn, SessionView::block_color(verdict), "{verdict:?} 와 같은 색이다");
    }
}

#[test]
fn an_unknown_exit_code_is_not_painted_like_success() {
    // ★ 이 뷰에서 가장 비싼 오해다. 서버가 종료코드를 못 받은 블록을 초록으로 칠하면
    // 사용자는 "끝났고 잘됐다"로 읽는다. 실패로 칠하는 것도 같은 종류의 거짓말이라
    // **둘 다와 달라야** 한다.
    let unknown = SessionView::block_color(Tone::Unknown);
    assert_ne!(unknown, SessionView::block_color(Tone::Ok));
    assert_ne!(unknown, SessionView::block_color(Tone::Failed));
}

#[test]
fn success_and_failure_never_share_a_color() {
    assert_ne!(
        SessionView::block_color(Tone::Ok),
        SessionView::block_color(Tone::Failed),
        "성공과 실패가 같은 색이면 블록 구역이 아무 말도 안 하는 것과 같다"
    );
}

#[test]
fn a_running_block_looks_different_from_a_finished_one() {
    // 진행 중을 끝난 것과 같이 칠하면 "왜 안 끝나지"를 화면에서 알 수 없다.
    for done in [Tone::Ok, Tone::Failed, Tone::Unknown] {
        assert_ne!(
            SessionView::block_color(Tone::Running),
            SessionView::block_color(done),
            "{done:?} 와 진행 중이 같은 색이다"
        );
    }
}

fn block(command: &str, cwd: Option<&str>) -> Block {
    Block {
        command: command.to_owned(),
        state: proto::blocks::BlockState::Done,
        exit: Some(0),
        cwd: cwd.map(str::to_owned),
        start_row: 0,
        end_row: None,
    }
}

#[test]
fn a_block_line_stays_inside_the_width_budget() {
    // ★ 실측 결함(2026-07-28): GUI 의 Text 는 TUI 와 달리 안 잘려서, 긴 명령 줄이
    // **창 밖으로 흘러나갔다**. 표식(4칸) + 명령 + 공백 + cwd 가 예산 안이라야 한다.
    let long = "x".repeat(300);
    let b = block(&long, Some(&"/very/long/path".repeat(20)));
    let (cmd, cwd) = SessionView::block_parts(&b, 80);
    let used = 4 + footer::width(&cmd) + cwd.as_deref().map_or(0, |c| 1 + footer::width(c));
    assert!(used <= 80, "{used} 칸을 썼다(예산 80)");
}

#[test]
fn a_cramped_line_drops_the_cwd_instead_of_halving_both() {
    // 둘을 똑같이 잘라 반쪽씩 남기면 어느 쪽도 못 읽는 줄이 된다. 명령이 주인공이다.
    let b = block("cargo build --release --workspace", Some("/home/u/proj"));
    let (cmd, cwd) = SessionView::block_parts(&b, 30);
    assert!(cwd.is_none(), "자리가 없으면 cwd 를 뺀다: {cwd:?}");
    assert!(!cmd.is_empty(), "명령까지 사라지면 그 줄은 아무 말도 안 한다");
}

#[test]
fn a_roomy_line_keeps_both() {
    let b = block("ls", Some("/tmp"));
    let (cmd, cwd) = SessionView::block_parts(&b, 80);
    assert_eq!(cmd, "ls");
    assert_eq!(cwd.as_deref(), Some("/tmp"), "자리가 넉넉하면 안 자른다");
}

#[test]
fn an_empty_command_still_says_something() {
    // 빈 문자열을 그리면 그 줄은 표식만 남아 자리만 차지한다.
    let (cmd, _) = SessionView::block_parts(&block("", None), 80);
    assert_eq!(cmd, "(명령 미상)");
}

// ── 입력(P7) ─────────────────────────────────────────────────────────────────
//
// 뷰를 통째로 만들 수는 없다(창·글꼴이 필요하다). 그래서 **판정이 있는 부분**만 순수
// 함수로 빼 두고 여기서 잡는다 — 키 이름 해석이 그것이다. 나머지(모드 전이·바이트
// 인코딩)는 core 의 오라클이 이미 덮고, GUI 는 그 core 를 부르기만 한다.

fn ks(key: &str, ctrl: bool, alt: bool, shift: bool) -> warpui::keymap::Keystroke {
    warpui::keymap::Keystroke {
        ctrl,
        alt,
        shift,
        cmd: false,
        meta: false,
        key: key.to_owned(),
    }
}

/// 맥의 `Cmd`(또는 윈도·리눅스의 `Super`)를 쥔 조합. 정본의 `super+v` 짝이다.
///
/// `ks` 에 인자를 둘 더 붙이지 않는 이유: 그 함수를 부르는 자리가 이미 여럿이고, 거기
/// 전부에 `false, false` 를 더하면 **읽는 사람이 무엇이 참인지 못 본다.**
fn ks_cmd(key: &str, cmd: bool, meta: bool, shift: bool) -> warpui::keymap::Keystroke {
    warpui::keymap::Keystroke {
        ctrl: false,
        alt: false,
        shift,
        cmd,
        meta,
        key: key.to_owned(),
    }
}

#[test]
fn the_gui_reads_key_names_with_the_same_table_as_the_tui() {
    // ★ 이름 표가 갈리면 **한쪽 클라에서만 안 먹는 키**가 생기고, 그 증상은 조용하다
    // (누르면 아무 일도 안 난다). 이름은 core 가 정하고 GUI 는 부르기만 한다.
    use base::keys::Key;
    let cases = [
        ("enter", Key::Enter),
        ("escape", Key::Escape),
        ("pageup", Key::PageUp),
        ("f5", Key::Function(5)),
        ("a", Key::Char('a')),
    ];
    for (name, want) in cases {
        let got = SessionView::key_from_keystroke(&ks(name, false, false, false));
        assert_eq!(got.map(|(k, _)| k), Some(want), "이름 '{name}'");
    }
}

#[test]
fn a_bare_tab_and_a_shifted_tab_are_different_keys() {
    // 둘이 같으면 역방향 탭 이동이 통째로 사라진다.
    use base::keys::Key;
    let plain = SessionView::key_from_keystroke(&ks("tab", false, false, false));
    let shifted = SessionView::key_from_keystroke(&ks("tab", false, false, true));
    assert_eq!(plain.map(|(k, _)| k), Some(Key::Tab));
    assert_eq!(shifted.map(|(k, _)| k), Some(Key::BackTab));
}

#[test]
fn meta_and_cmd_are_treated_as_alt() {
    // 단말은 이 셋을 구분해 주지 않는다 — 셋 다 ESC 접두로 나간다(TUI 와 같은 규칙).
    for (ctrl, alt, want_alt) in [(false, true, true), (false, false, false)] {
        let mods = SessionView::key_from_keystroke(&ks("a", ctrl, alt, false)).unwrap().1;
        assert_eq!(mods.alt, want_alt);
    }
    let mut k = ks("a", false, false, false);
    k.cmd = true;
    assert!(SessionView::key_from_keystroke(&k).unwrap().1.alt, "cmd 가 alt 로 안 갔다");
    let mut k = ks("a", false, false, false);
    k.meta = true;
    assert!(SessionView::key_from_keystroke(&k).unwrap().1.alt, "meta 가 alt 로 안 갔다");
}

#[test]
fn ctrl_survives_the_conversion() {
    // Ctrl 이 떨어지면 Ctrl+C 가 그냥 `c` 가 되어 **인터럽트가 안 간다**.
    let mods = SessionView::key_from_keystroke(&ks("c", true, false, false)).unwrap().1;
    assert!(mods.ctrl);
}

// ── 창에 맞춘 격자(슬라이스 12) ──────────────────────────────────────────────
//
// ★ 종전 GUI 는 붙을 때 80×24 를 알리고 그걸로 끝이었다 — 창을 키워도 캔버스가 안 자라고
// (나머지는 빈 배경), 줄이면 화면이 창 밖으로 넘쳤다. 아래가 그 자리를 지킨다.

#[test]
fn the_grid_fills_the_window_minus_the_chrome() {
    // 자리표: 원점 (16, 48) · 칸 8×16. 창 816×848 · 아래 구역 없음.
    // 가로 = (816 - 16 - 8)/8 = 99 · 세로 = (848 - 48 - 8)/16 = 49.5 → 49
    let got = SessionView::grid_for(probe_at(16., 48., 8., 16.), 816., 848., 0.);
    assert_eq!(got, Some((99, 49)));
}

#[test]
fn the_summary_area_takes_its_rows_out_of_the_canvas() {
    // ★ 재는 것은 **아래 구역의 높이**이지 창 바닥까지의 거리가 아니다. 후자로 재면 그
    // 아래 빈 자리까지 크롬으로 세어 캔버스가 프레임마다 줄어든다(실측: 10줄 → 3줄).
    // 세로 = (848 - 48 - 248 - 8)/16 = 34
    let got = SessionView::grid_for(probe_at(16., 48., 8., 16.), 816., 848., 248.);
    assert_eq!(got, Some((99, 34)));
}

#[test]
fn a_window_too_small_for_one_cell_is_not_reported() {
    // 알리면 서버가 최소 크기로 클램프해 되돌려 주고, 그 프레임이 매번 다시 온다.
    assert_eq!(SessionView::grid_for(probe_at(16., 48., 8., 16.), 20., 848., 0.), None);
    assert_eq!(SessionView::grid_for(probe_at(16., 48., 8., 16.), 816., 50., 0.), None);
}

#[test]
fn a_degenerate_probe_reports_nothing_instead_of_dividing_by_zero() {
    assert_eq!(SessionView::grid_for(probe_at(0., 0., 0., 16.), 800., 800., 0.), None);
    assert_eq!(SessionView::grid_for(probe_at(0., 0., 8., f32::NAN), 800., 800., 0.), None);
}

#[test]
fn the_grid_never_rounds_up() {
    // ★ 한 줄 남는 것은 빈 줄 하나지만, 한 줄 넘치면 아래 구역이 창 밖으로 밀려
    // **블록·복사 알림이 통째로 안 보인다**. 0.9칸이 남아도 안 올린다.
    let got = SessionView::grid_for(probe_at(0., 0., 10., 10.), 108., 108., 0.);
    assert_eq!(got, Some((10, 10)), "{got:?} — 남는 자리를 한 칸으로 올렸다");
}

fn probe_at(x: f32, y: f32, w: f32, h: f32) -> RectF {
    RectF::new(vec2f(x, y), vec2f(w, h))
}

// ── 입력기(IME) 확정 글자 — 슬라이스 11 ──────────────────────────────────────

#[test]
fn a_committed_string_reaches_the_pane() {
    // 한글은 자판 한 번이 글자 하나가 아니다. 조합 결과는 키가 아니라 문자열로 온다.
    assert_eq!(SessionView::typed_target(InputMode::Normal, false, false, "한글"), TypedTo::Pane);
}

#[test]
fn nothing_is_typed_while_the_user_is_talking_to_pytmux() {
    // 명령 모드에서 확정된 글자는 명령이 아니다. 패널로 흘리면 사용자가 pytmux 에게
    // 말하는 중에 셸에 글자가 찍힌다. **판이 없을 때** 이야기다(아래 ⓜ2 참조).
    for mode in [InputMode::Command, InputMode::Scroll] {
        assert_eq!(SessionView::typed_target(mode, false, false, "한글"), TypedTo::Drop, "{mode:?}");
    }
}

#[test]
fn an_empty_commit_sends_nothing() {
    // 입력기는 조합을 취소할 때 빈 확정을 보낸다. 그걸 그대로 보내면 빈 입력 프레임이
    // 매번 서버로 나간다.
    assert_eq!(SessionView::typed_target(InputMode::Normal, false, false, ""), TypedTo::Drop);
    assert_eq!(SessionView::typed_target(InputMode::Command, true, false, ""), TypedTo::Drop);
}

#[test]
fn a_committed_string_reaches_the_open_panel() {
    // ★ **제보 그대로**(§10-21ⓜ2): `esc` `:` 에서 한글을 못 쳤다. 판이 열려 있으면
    //   확정된 글자는 **그 판의 것**이다 — 종전에는 모드만 보고 버렸다.
    assert_eq!(SessionView::typed_target(InputMode::Command, true, false, "한글"), TypedTo::Screen);
    // 평소 모드에서 판이 열려 있는 경우(작성창 등)도 판이 먼저다.
    assert_eq!(SessionView::typed_target(InputMode::Normal, true, false, "한글"), TypedTo::Screen);
}

#[test]
fn korean_typed_into_the_palette_lands_in_the_filter_as_qwerty() {
    // 순수 판정만 재면 배선이 빠져도 통과한다 — **실제로 필터에 쌓이는지**를 본다.
    //
    // ⚠ 종전엔 확정 글자가 배선은 타되 **변환 없이 그대로** 쌓였다(`typed() == "한글"`) —
    // IME 를 켠 채 영문 명령을 치면 자모가 그대로 필터에 들어가 검색이 안 됐다
    // (pytmux-176). 이제 `press_palette` 가 정본 `hangul_to_qwerty` 와 동치인 변환을
    // 거친다 — "한글" 은 두벌식으로 h-a-n-g-e-u-l 자리라 "gksrmf" 가 된다.
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_key(Key::Escape, Mods::NONE);
    view.handle_key(Key::Char(':'), Mods::NONE);
    assert_eq!(view.screens.top(), Some(Screen::Commands), "팔레트가 안 열렸다");
    assert!(view.handle_typed("한글"), "확정 글자를 아무도 안 받았다");
    assert_eq!(view.screens.typed(), "gksrmf", "한글 자모가 QWERTY 로 안 돌아왔다");
}

#[test]
fn korean_typed_in_command_mode_without_a_panel_still_goes_nowhere() {
    // 반대쪽도 지킨다 — 판이 없으면 종전 그대로다(셸에 글자가 찍히면 안 된다).
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_key(Key::Escape, Mods::NONE);
    assert_eq!(view.screens.top(), None, "판이 없어야 한다");
    assert!(!view.handle_typed("한글"), "esc 모드에서 글자가 새 나갔다");
}

// ── 마우스 패스스루(슬라이스 10) ─────────────────────────────────────────────
//
// Shift+드래그만 앱에게 넘어간다. 이 판정이 **넓으면** 사용자의 복사 드래그가 앱으로 새고,
// **좁으면** 마우스 1급 앱(p4v-tui 의 스플리터 등) 안에서 드래그를 아예 못 한다.

/// 왼쪽 패널만 마우스 추적을 켠 상태. 오른쪽은 안 켰다.
fn tracking_state() -> SessionState {
    let mut state = SessionState::new();
    let msg = serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 40, "h": 24, "active": true, "mouse": 1},
            {"id": 2, "x": 40, "y": 0, "w": 40, "h": 24},
        ]
    });
    state.apply(serde_json::from_value(msg).unwrap());
    state
}

#[test]
fn shift_drag_over_a_mouse_app_goes_to_the_app() {
    let state = tracking_state();
    assert!(SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), true, base::config::DragCopy::On));
}

#[test]
fn a_plain_drag_is_a_selection_even_over_a_mouse_app() {
    // ★ 평드래그를 앱에게 넘기면 **화면의 글자를 꺼낼 방법이 사라진다** — 이 클라에는
    // 마우스 캡처를 대신 풀어 줄 바깥 터미널이 없다.
    let state = tracking_state();
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), false, base::config::DragCopy::On));
}

#[test]
fn a_plain_click_reaches_the_app_even_though_a_plain_drag_does_not() {
    // ★ pytmux-19. 클릭은 드래그가 아니다 — 복사와 다투지 않으므로 Shift 를 요구할
    //   이유가 없다. 이 자리를 비워 뒀더니 패널 안 앱의 **버튼·링크가 통째로 죽었다**
    //   (제보는 Claude 프롬프트 바였지만 구멍은 그보다 넓었다).
    let state = tracking_state();
    assert!(SessionView::click_goes_to_app(&state, InputMode::Normal, (10, 5)));
    // 드래그 쪽 판정은 안 넓어졌다 — 평드래그는 여전히 복사다.
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), false, base::config::DragCopy::On));
}

#[test]
fn a_click_where_no_app_wants_the_mouse_stays_ours() {
    // 안 켠 앱에 리포트를 보내면 그 바이트가 프롬프트에 **글자로 찍힌다**.
    let state = tracking_state();
    assert!(!SessionView::click_goes_to_app(&state, InputMode::Normal, (60, 5)));
    for mode in [InputMode::Command, InputMode::Scroll] {
        assert!(
            !SessionView::click_goes_to_app(&state, mode, (10, 5)),
            "{mode:?} 에서 넘어갔다 — 사용자가 pytmux 에게 말하는 중이다"
        );
    }
}

#[test]
fn turning_off_drag_copy_hands_the_plain_press_to_the_app() {
    // `mouse-drag-copy off` 는 "복사를 포기하고 앱에게 다 준다"는 뜻이다(정본과 같다).
    let state = tracking_state();
    assert!(SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), false, base::config::DragCopy::Off));
}

#[test]
fn drag_copy_shift_swaps_the_plain_drag_and_the_shift_drag() {
    // `mouse-drag-copy shift`(pytmux-422 · 사람의 결정 2026-08-31) — 평드래그를 pytmux 가
    // 가져가는 규칙은 **claude 에 선택 기능이 없던 때** 세운 것이라, 제 선택과 auto-copy 를
    // 가진 fullscreen 앱을 쓰려면 평드래그가 그 앱에 닿아야 한다. 그 대신 복사는 Shift 다.
    use base::config::DragCopy;
    let state = tracking_state();
    assert!(
        SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), false, DragCopy::Shift),
        "평드래그가 마우스 앱에게 안 갔다"
    );
    assert!(
        !SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), true, DragCopy::Shift),
        "Shift 는 pytmux 선택이어야 한다"
    );
    // ★ **마우스를 안 켠 패널에서는 평드래그가 여전히 우리 것**이다(조건 2가 잡는다) —
    // 넘길 앱이 없는데 드래그를 버리면 그 패널에서 복사할 길이 사라진다.
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (60, 5), false, DragCopy::Shift));
    // 그리고 `shift` 는 **복사를 끄지 않는다** — 그 값이 바꾼 것은 어느 드래그가
    // 선택이 되나이지 복사 여부가 아니다(`handle_mouse_up` 의 관문).
    assert!(DragCopy::Shift.copies() && DragCopy::On.copies() && !DragCopy::Off.copies());
}

#[test]
fn nothing_is_forwarded_while_the_user_is_talking_to_pytmux() {
    // 명령·스크롤 모드에서 마우스만 앱으로 새면 모드가 반쪽이 된다.
    let state = tracking_state();
    for mode in [InputMode::Command, InputMode::Scroll] {
        assert!(
            !SessionView::press_goes_to_app(&state, mode, (10, 5), true, base::config::DragCopy::On),
            "{mode:?} 에서 넘어갔다"
        );
    }
}

#[test]
fn an_app_that_never_asked_for_the_mouse_gets_nothing() {
    // 안 켠 앱에 리포트를 보내면 그 바이트가 프롬프트에 **글자로 찍힌다**.
    let state = tracking_state();
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (60, 5), true, base::config::DragCopy::On));
    // 캔버스 밖도 마찬가지다.
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 99), true, base::config::DragCopy::On));
}

/// 뒤 패널이 추적을 켠 채, 추적을 켠 앱이 든 팝업이 떠 있는 판(popup.mouse).
fn popup_tracking_state() -> SessionState {
    let mut state = SessionState::new();
    let msg = serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "active": true,
                   "mouse": 1, "mouse_sgr": true}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8,
                  "mouse": 2, "mouse_sgr": true}
    });
    state.apply(serde_json::from_value(msg).unwrap());
    state
}

#[test]
fn a_shift_press_inside_the_popup_goes_to_the_popup_app() {
    // 서버가 popup.mouse 를 광고하면 GUI 판정도 팝업 안 앱을 대상으로 잡는다.
    let state = popup_tracking_state();
    assert!(SessionView::press_goes_to_app(&state, InputMode::Normal, (12, 7), true, base::config::DragCopy::On));
    // 테두리와 팝업 밖(뒤 패널이 추적 중이어도)은 아니다 — 모달 규칙.
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (10, 5), true, base::config::DragCopy::On));
    assert!(!SessionView::press_goes_to_app(&state, InputMode::Normal, (60, 20), true, base::config::DragCopy::On));
}

// ── 붙여넣기 조합(슬라이스 9 · pytmux-364) ──────────────────────────────────
//
// 이 판정이 틀리면 **아무 소리 없이** 어긋난다: 좁으면 붙여넣기가 안 되고, 넓으면 패널
// 안 프로그램의 키가 사라진다.
//
// ★ **2026-08-23 에 넓혔다.** 종전 오라클은 「`Ctrl+V` 는 붙여넣기가 **아니다**」를 단언해
//   두었고(`plain_ctrl_v_belongs_to_the_program_in_the_pane`), 그래서 이 갈림은 **기계로는
//   절대 안 잡히는** 상태였다 — 게이트가 결함을 「맞다」고 고정하고 있었다. 뒤집는 이유는
//   그 관찰(0x16 은 패널의 바이트다)이 틀려서가 아니라, **정본이 이미 그 값을 치르기로**
//   정해 두었기 때문이다(`clientio.py` 가 `ctrl+v`/`super+v` 를 크롬 키로 잡는다).

#[test]
fn plain_ctrl_v_asks_for_a_paste_just_like_the_canon() {
    // 제보(pytmux-364)가 짚은 그 손버릇이다 — 정본에서는 이것이 붙는다.
    assert!(SessionView::is_paste_chord(&ks("v", true, false, false)));
    assert!(SessionView::is_paste_chord(&ks("V", true, false, false)), "대문자로 와도 같은 키다");
}

#[test]
fn ctrl_shift_v_still_asks_for_a_paste() {
    // 터미널 에뮬레이터 관례 — 이미 손에 익은 사람이 있어 별칭으로 남긴다.
    assert!(SessionView::is_paste_chord(&ks("v", true, false, true)));
    assert!(SessionView::is_paste_chord(&ks("V", true, false, true)), "대문자로 와도 같은 키다");
}

#[test]
fn cmd_v_asks_for_a_paste_so_the_mac_has_an_entrance_at_all() {
    // ⛔ 맥에서는 `Ctrl+Shift+V` 가 관례가 아니다 — 이것이 없으면 그 상자에는 입구가
    //    **하나도 없다**(정본은 `super+v` 로 받는다).
    assert!(SessionView::is_paste_chord(&ks_cmd("v", true, false, false)));
    // 이 백엔드는 맥의 `Cmd` 와 윈도·리눅스의 `Super` 를 갈라 준다. 정본은 둘을 한
    // 이름(`super+v`)으로 부르므로 여기서도 둘 다 같은 입구다.
    assert!(SessionView::is_paste_chord(&ks_cmd("v", false, true, false)));
    assert!(SessionView::is_paste_chord(&ks_cmd("V", true, false, true)), "Cmd+Shift+V 도 같다");
}

#[test]
fn a_paste_is_one_family_or_the_other_and_never_another_key() {
    assert!(!SessionView::is_paste_chord(&ks("v", false, false, true)), "Shift+V 는 대문자 V 다");
    assert!(!SessionView::is_paste_chord(&ks("c", true, false, true)), "Ctrl+Shift+C 는 다른 키다");
    assert!(!SessionView::is_paste_chord(&ks("v", false, false, false)), "맨 V 는 글자다");
    // Alt 가 섞이면 다른 조합이다 — 넓게 잡으면 그 조합이 통째로 사라진다.
    assert!(!SessionView::is_paste_chord(&ks("v", true, true, true)));
    // `Ctrl` 과 `Cmd` 를 함께 쥔 것은 어느 계열도 아닌 제3의 조합이다.
    let mut both = ks("v", true, false, false);
    both.cmd = true;
    assert!(!SessionView::is_paste_chord(&both));
}

// ── 클립보드 붙여넣기 — 글자와 **그림**(pytmux-159·363) ─────────────────────────
//
// ⛔ **여기 오라클이 없어서 기능이 한 revision 만에 조용히 사라졌다.** CL 71659 가 그림
//    분기를 넣어 pytmux-159 를 닫았는데, 바로 다음 revision(CL 71667 — 설명은 테두리
//    얘기뿐이다)에 그 코드가 통째로 없다. 낡은 트리로 덮어쓴 사고이고, **아무도 몰랐다**
//    (그 CL 은 `cargo check` 로만 확인했다). 패리티 래칫은 그동안 계속 `Done` 이라고
//    말하고 있었다.

/// 클립보드 한 장을 짓는다. 창 계층이 주는 모양 그대로다(`ClipboardContent`).
fn clipboard(text: &str, images: Vec<(&[u8], &str)>) -> warpui_core::clipboard::ClipboardContent {
    warpui_core::clipboard::ClipboardContent {
        plain_text: text.to_owned(),
        images: (!images.is_empty()).then(|| {
            images
                .into_iter()
                .map(|(data, mime)| warpui_core::clipboard::ImageData {
                    data: data.to_vec(),
                    mime_type: mime.to_owned(),
                    filename: None,
                })
                .collect()
        }),
        ..Default::default()
    }
}

/// 붙여넣기 한 장을 먹이고 **서버로 나간 것**과 **화면에 남은 말**을 돌려준다.
fn paste_outcome(content: warpui_core::clipboard::ClipboardContent) -> (Vec<Outgoing>, Vec<String>) {
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.paste_clipboard_for_test(content);
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    let said = view
        .state_for_test()
        .notices()
        .map(|n| n.text.clone())
        .collect();
    (out, said)
}

#[test]
fn clipboard_text_goes_to_the_pane_as_a_paste() {
    let (out, _) = paste_outcome(clipboard("hello", vec![]));
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Command(Command::Paste { text }) if text == "hello")),
        "글자가 `paste` 로 안 나갔다: {out:?}"
    );
}

#[test]
fn a_clipboard_image_is_pasted_as_the_path_of_a_temp_file() {
    // ★ pytmux-159 의 본체. 정본이 정한 계약이다 — PTY 너머로 비트맵이 못 가서 파일로
    //   떨구고 **경로**를 붙인다(Claude Code CLI 등이 그 경로를 첨부 이미지로 읽는다).
    let (out, said) = paste_outcome(clipboard("", vec![(b"fake-png-bytes", "image/png")]));
    let pasted = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::Paste { text }) => Some(text.clone()),
        _ => None,
    });
    let Some(path) = pasted else {
        panic!("그림이 `paste` 로 안 나갔다: {out:?}");
    };
    assert!(path.ends_with(".png"), "붙여넣은 것이 그림 경로가 아니다: {path}");
    // ⛔ **파일이 실제로 있어야 한다.** 경로만 그럴듯하고 파일이 없으면 앱이 빈손을 쥐고,
    //    증상은 "붙여넣기가 안 된다"가 아니라 "그림이 안 열린다"로 어긋난다.
    assert_eq!(
        std::fs::read(&path).expect("붙여넣은 경로에 파일이 없다"),
        b"fake-png-bytes"
    );
    let _ = std::fs::remove_file(&path);
    // 무슨 일이 났는지 **말한다** — 경로 하나가 프롬프트에 튀어나오는 것은 설명이 없으면
    // 사용자에게 결함으로 읽힌다.
    assert!(said.iter().any(|m| m.contains("이미지")), "그림을 붙였다고 말하지 않았다: {said:?}");
}

#[test]
fn text_wins_over_an_image_when_the_clipboard_carries_both() {
    // HTML 을 복사하면 창 계층이 글자와 그림을 **둘 다** 싣는다. 그때 기대되는 것은
    // 글자이고, 정본도 글자를 먼저 본다.
    let (out, _) = paste_outcome(clipboard("hello", vec![(b"fake-png-bytes", "image/png")]));
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Command(Command::Paste { text }) if text == "hello")),
        "글자가 있는데 그림을 붙였다: {out:?}"
    );
}

#[test]
fn an_image_we_cannot_save_falls_back_to_alt_v_like_the_canon() {
    // 모르는 형식은 파일로 못 떨군다(`clip::save_image`). 그때 정본은 **안쪽 앱이 공유
    // 클립보드를 스스로 읽게** `Alt+V`(= ESC v)를 보낸다 — 여기도 같아야 한다.
    let (out, said) = paste_outcome(clipboard("", vec![(b"fake-bmp-bytes", "image/bmp")]));
    assert!(
        out.contains(&Outgoing::Input(vec![0x1b, b'v'])),
        "폴백 Alt+V 가 안 나갔다: {out:?}"
    );
    assert!(!said.is_empty(), "폴백을 말없이 했다");
}

#[test]
fn an_empty_clipboard_says_so_instead_of_doing_nothing() {
    // ⛔ 제보(pytmux-364)의 핵심이 **무동작**이었다 — 클립보드가 빈 건지 클라가 못 받은
    //    건지 서버가 안 넣은 건지 사용자가 가를 수 없었다. 말없이 끝나는 팔은 없다.
    let (out, said) = paste_outcome(clipboard("", vec![]));
    assert!(
        !out.iter().any(|o| matches!(o, Outgoing::Command(Command::Paste { .. }))),
        "빈 클립보드로 무언가를 붙였다: {out:?}"
    );
    assert!(
        said.iter().any(|m| m.contains("클립보드")),
        "빈 클립보드인데 아무 말도 안 했다: {said:?}"
    );
}

#[test]
fn an_image_on_a_remote_tab_never_pastes_a_local_path_straight_away() {
    // ★ 파일은 **이 상자**에 생긴다. 원격 셸에 그 경로는 없다 — 그대로 붙이면 저쪽 앱이
    //   "그런 파일 없음"이라 말하고, 증상은 "붙여넣기가 깨졌다"로 어긋난다. 정본은 `scp`
    //   로 먼저 옮긴다(`_do_paste_clipboard`).
    //
    // 여기서 재는 것은 **즉시 붙이지 않는다**는 것이다: `scp` 는 상한이 30초라 스레드로
    // 나가고, 붙이는 것은 그 결과가 온 뒤다. 진짜 `scp` 를 부르는 자리는 이 오라클 밖이고
    // (`clip::scp_to_remote` 가 호스트 검증을 따로 잰다), 없는 호스트라 실패로 끝난다.
    let (mut view, tx, sent) = harness();
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "⇄box:원격", "remote": true, "active": true}]
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(status))).unwrap();
    view.pump_headless();
    assert_eq!(
        view.state_for_test().active_remote_host(),
        Some("box"),
        "하네스가 원격 탭을 안 세웠다 — 아래 단언이 공허하게 통과한다"
    );

    view.paste_clipboard_for_test(clipboard("", vec![(b"fake-png-bytes", "image/png")]));
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        !out.iter().any(|o| matches!(o, Outgoing::Command(Command::Paste { .. }))),
        "원격 탭인데 옮기기도 전에 경로를 붙였다: {out:?}"
    );
    // 말없이 몇 초를 끌지 않는다 — 그 침묵이 곧 「아무 일도 안 난다」로 읽힌다.
    assert!(
        view.state_for_test().notices().count() > 0,
        "원격 전송을 말없이 시작했다"
    );
}

#[test]
fn an_overlay_we_cannot_open_yet_says_why_instead_of_nothing() {
    // ⛔ pytmux-366 — 시각 클릭·`prefix t`·`:clock-mode` 세 입구가 모두 여기로 모이는데,
    //    레이아웃을 아직 못 받았으면 `toggle_overlay` 가 조용히 `None` 이었다. 그러면
    //    누른 사람에게도 로그에도 「아무 일도 안 일어남」만 남아, 제보가 가른 후보 넷 중
    //    무엇인지 **가릴 수가 없다**. 침묵 자체가 고쳐야 할 것이었다.
    //
    // 레이아웃을 안 먹인 하네스가 곧 그 상태다.
    let (mut view, _tx, sent) = harness();
    view.pump_headless();
    assert!(view.apply_action_for_test(base::Action::ToggleClock));
    view.pump_headless();
    assert!(
        !sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::PluginOverlay { .. })
        )),
        "대상 패널을 모르는데 오버레이를 켰다"
    );
    assert!(
        view.state_for_test().notices().count() > 0,
        "못 켠 이유를 아무 데도 안 남겼다 — 제보가 가른 그 침묵이다"
    );
}

#[test]
fn a_chrome_click_leaves_a_trace_before_any_gate_can_swallow_it() {
    // ★ pytmux-366 — 시각을 눌러도 시계가 안 뜬다는 제보에서, 두 회차가 **판정 없이**
    //   끝났다. 남은 물음은 하나다: 「클릭이 앱까지 왔나」. 그것을 재려면 종전에는
    //   상류의 `dispatching typed action` 을 `RUST_LOG=debug` 로 켜야 했고, 실제로
    //   2026-08-30 회차가 그것을 못 켜서 아무것도 못 갈랐다.
    //
    //   ⛔ 그리고 `set mouse off` 갈래는 **말없이** 돌아갔다 — 같은 증상의 옛 제보
    //      (2026-08-02)의 뿌리가 바로 그것이었는데도.
    //
    // ⚠ 로그는 이 하네스가 못 읽는다(창도 없고 수집기도 없다). 그래서 재는 것은
    //   **원문**이다 — 이 파일의 다른 원문 가드와 같은 사정이다.
    let body = source_after("pub fn chrome_click(&mut self, target:", 1600);
    let logged = body.find("log::info!").expect(&format!("크롬 클릭이 자취를 안 남긴다: {body}"));
    let gate = body.find("if !self.config.mouse").expect("마우스 관문이 사라졌다");
    assert!(
        logged < gate,
        "관문 **뒤에** 적는다 — 그러면 `set mouse off` 인 클릭은 여전히 자취가 없다: {body}"
    );
    // 관문이 삼킬 때도 왜인지 말한다 — 「닿았다」와 「닿았는데 껐다」는 다른 판정이다.
    let swallowed = &body[gate..];
    assert!(
        swallowed.contains("log::info!"),
        "마우스가 꺼져 있을 때 조용히 돌아간다 — 그 침묵이 옛 제보를 못 가르게 했다: {swallowed}"
    );
    // ⛔ 알림(`note_notice`)은 **안 띄운다** — 크롬 클릭은 잦고, 정본 TUI 는 마우스가
    //    꺼졌을 때 아무 말도 안 한다(단말이 이벤트를 아예 안 보낸다).
    //
    // ⚠ 자르는 자리를 **글자 경계로 물린다**. 종전에는 바이트 인덱스를 그대로 썼는데,
    //   이 원문은 한국어라 그 자리가 글자 가운데에 떨어지는 순간 시험이 **패닉으로**
    //   죽는다 — 실측(2026-09-02 · Windows): `byte index 1389 is not a char boundary;
    //   it is inside '데'`. 재려던 것(«말풍선을 띄우나»)과 무관한 죽음이고, 원문이
    //   한 글자만 길어져도 되살아난다.
    let mut cut = (gate + 200.min(swallowed.len())).min(body.len());
    while cut > 0 && !body.is_char_boundary(cut) {
        cut -= 1;
    }
    assert!(
        !body[..cut].contains("note_notice"),
        "마우스가 꺼졌다고 말풍선을 띄운다 — 정본에 없는 갈림이다: {body}"
    );
}

#[test]
fn the_palette_entry_and_the_key_meet_at_the_same_action() {
    // 입구가 둘이어도 경로는 하나다 — 팔레트의 `paste-clipboard` 도 키와 **같은 칸**에
    // 실려 창을 쥔 자리로 간다.
    let entry = base::PALETTE
        .iter()
        .find(|e| e.name == "paste-clipboard")
        .expect("팔레트에 paste-clipboard 가 없다");
    assert_eq!(entry.action, base::Action::PasteClipboard);
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    assert!(view.apply_action_for_test(entry.action));
    assert!(
        view.paste_requested_for_test(),
        "팔레트로 부른 붙여넣기가 창을 쥔 자리로 안 실렸다"
    );
}

#[test]
fn typing_paste_in_the_palette_offers_the_clipboard_first_like_the_canon() {
    // ★ pytmux-363 ⑵. 둘 다 `paste` 전체 접두 일치라 **선언 순서가 곧 기본 선택**이고,
    //   정본은 2026-06-16 요청으로 클립보드를 앞에 뒀다(`clientutil.py:1032` ·
    //   정본 오라클 `tests/test_client.py:7697`). 자리가 바뀌면 같은 타이핑이 정반대
    //   후보를 고른다.
    let first = base::PALETTE
        .iter()
        .find(|e| e.name.starts_with("paste"))
        .expect("`paste` 로 시작하는 항목이 없다");
    assert_eq!(
        first.name, "paste-clipboard",
        "`paste` 의 첫 후보가 정본과 다르다"
    );
}

// ── 하단 한 줄(§10-21ⓝ·ⓦ·ⓗ2) ────────────────────────────────────────────────
//
// 제보 셋이 **같은 줄**을 두고 있다: 복사 결과를 그 자리로 모으고(ⓝ) 닫을 수 있게 하고
// 시한을 주며(ⓦ) 그래서 비워진 머리줄을 가운데로 옮긴다(ⓗ2).

#[test]
fn the_copy_note_lands_on_the_bottom_line_not_the_head() {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.note_flash_for_test("· 20 chars copied".into(), proto::session::Severity::Ok);
    let painted = painted_after_setup(vec![layout_one_pane()], &[], |v| {
        v.note_flash_for_test("· 20 chars copied".into(), proto::session::Severity::Ok)
    });
    assert!(
        painted_contains(&painted, "20 chars copied"),
        "복사 결과가 프레임에 없다: {painted:?}"
    );
    // 머리줄에는 **안 붙는다** — 그 줄에는 이제 이름과 주소만 남는다(ⓗ2 의 전제).
    assert!(
        painted
            .iter()
            .any(|t| t.starts_with("pytmux-gui · ") && !t.contains("copied")),
        "머리줄에 복사 결과가 아직 붙어 있다: {painted:?}"
    );
}

#[test]
fn the_line_is_not_always_red() {
    // ★ 이 줄은 빨강 고정이었다. 복사 결과까지 여기 오면서 **성공이 오류로 읽히던**
    //   자리다 — 색은 심각도가 정하고, 표는 알림 이력과 같은 것을 쓴다.
    use proto::session::Severity;
    // ⚠ 표는 **크롬의 의미색**이다 — SGR 팔레트가 아니다(pytmux-412 ⓑ1). 그 둘이 다시
    //    같아지면 `theme` 의 `the_meaning_colors_are_not_the_sgr_table_any_more` 가 운다.
    assert_eq!(SessionView::severity_color(Severity::Ok), theme::OK);
    assert_eq!(SessionView::severity_color(Severity::Error), theme::ERROR);
    assert_eq!(SessionView::severity_color(Severity::Warn), theme::WARN);
    assert_ne!(
        SessionView::severity_color(Severity::Ok),
        SessionView::severity_color(Severity::Error),
        "성공과 오류가 같은 색이면 이 줄은 종전과 같다"
    );
}

#[test]
fn the_bottom_line_can_be_closed_but_the_history_stays() {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.state.note_error("서버가 성났다");
    view.pump_headless();
    assert!(view.live_flash_for_test().is_some(), "오류가 하단 줄로 안 왔다");
    let before = view.state.notices().len();
    assert!(view.chrome_click(base::chrome::ClickTarget::DismissMessage));
    assert!(view.live_flash_for_test().is_none(), "닫아도 줄이 남는다");
    // ⚠ 이력은 남아야 한다 — 지우면 그 줄을 눌러 이력으로 가는 동선이 무의미해진다.
    assert_eq!(view.state.notices().len(), before, "닫기가 이력까지 지웠다");
}

#[test]
fn a_disconnect_line_does_not_expire() {
    // 끊김은 지나가는 사건이 아니라 **지금 상태**다 — 사라지면 화면은 멀쩡해 보이는데
    // 아무것도 안 오는 창이 된다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.ended = Some("서버가 닫았다".into());
    let flash = view.live_flash_for_test().expect("끊김 줄이 없다");
    assert!(flash.has_no_deadline(), "끊김에 시한이 붙었다");
}

// ── 판 공통 기하(§10-21 ⓗ·ⓢ·ⓥ·ⓐ2·ⓚ2) ───────────────────────────────────────
//
// 제보 다섯의 뿌리가 하나다: **판의 기하를 내용이 정한다**. 여기서 재는 것은 그 반대다 —
// 내용이 달라져도 판이 같은가.

/// 판 하나를 열고 **그려진 줄 수**를 센다.
fn panel_line_count(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> usize {
    painted_after(messages, keys).len()
}

#[test]
fn the_palette_keeps_its_height_when_the_filter_narrows() {
    // ⓗ⑵ — 후보 수가 달라져도 높이는 그대로다. 종전에는 목록이 짧아지면 판이 줄었다.
    //
    // ⚠ **필터는 후보가 "적지만 0 은 아닌" 자리라야 한다**(처음에 여기서 틀렸다):
    //   0 개면 "맞는 명령이 없다" 갈래로 빠져 다른 코드가 채우고, 예산을 넘게 많으면
    //   양쪽 다 예산만큼 그려 차이가 안 난다 — 둘 다 변이가 살아남는다.
    let open = |keys: &[(Key, Mods)]| {
        let mut all = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
        all.extend_from_slice(keys);
        painted_after(three_tabs(), &all)
    };
    let wide = open(&[]);
    assert!(
        wide.iter().any(|t| t.starts_with("> ")),
        "팔레트 입력줄이 없다 — 판이 안 열렸다: {wide:?}"
    );
    // `notice-history` 하나만 남는 필터.
    let typed: Vec<(Key, Mods)> =
        "notice".chars().map(|c| (Key::Char(c), Mods::NONE)).collect();
    let narrow = open(&typed);
    let hits = narrow.iter().filter(|t| t.contains("notice-history")).count();
    assert_eq!(hits, 1, "필터가 한 줄만 남기지 않았다 — 단언이 뜻을 잃는다: {narrow:?}");
    // ⚠ **조각 수가 아니라 줄 수를 센다**(§10-21ⓞ 이후). 한 줄이 세 칸으로 갈리면서
    //   "그려진 글 조각의 수"는 더 이상 높이의 대리값이 아니다 — 그 CL 에서 이 단언이
    //   먼저 울었고, 그것이 옳았다(대리값이 뜻을 잃었다는 신호다).
    let rows = |keys: &[(Key, Mods)]| -> usize {
        let mut all = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
        all.extend_from_slice(keys);
        let boxes = painted_boxes(three_tabs(), &all);
        let mut ys: Vec<i64> = boxes.iter().map(|(_, y)| y.round() as i64).collect();
        ys.sort_unstable();
        ys.dedup();
        ys.len()
    };
    assert_eq!(
        rows(&[]),
        rows(&typed),
        "필터로 좁혔더니 판 높이가 달라졌다(ⓗ)
넓을 때: {wide:?}
좁을 때: {narrow:?}"
    );
}

#[test]
fn the_palette_is_half_and_the_others_are_two_thirds() {
    // 비율의 주인은 core 다 — 제보가 팔레트만 "절반"이라고 못박았다.
    use base::screens::Screen;
    assert_eq!(Screen::Commands.height_ratio(), (1, 2));
    assert_eq!(Screen::Settings.height_ratio(), (2, 3));
    assert_eq!(Screen::Notices.height_ratio(), (2, 3));
}

#[test]
fn a_short_list_still_fills_the_panel() {
    // ⓥ — 굴려 끝에 가까워져도, 목록이 짧아도 판은 그대로다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    let budget = view.panel_budget_for_test();
    assert!(budget >= 5, "예산이 너무 작아 단언이 뜻을 잃는다: {budget}");
    // 빈 목록도 예산만큼 자리를 차지한다(빈 줄로 채운다).
    let padded = view.pad_rows_count_for_test(0, budget);
    assert_eq!(padded, budget, "모자란 줄을 안 채운다");
}

// ── 폭 2 글자를 두 번 그리지 않는다(pytmux-208) ────────────────────────────────
//
// ⛔ **이 자리가 그 이슈의 마지막 빈칸이었다.** 정본 쪽 오라클
//    (`tests/test_wide_char_no_duplication.py`)이 서버 격자·직렬화·파이썬 클라 합성까지
//    재면서 머리말에 이렇게 적어 두었다: *"Rust GUI 는 여전히 안 잰다 — 같은 연속칸
//    규약을 쓰는 **별개 구현**이라 `client/` 쪽 오라클이 따로 져야 한다."*
//
// 제보 모양(`조직` → `조조직직`)의 뿌리 가설은 「폭 2 글자를 **칸마다 한 번씩** 그린다」다.
// 서버는 연속칸(`data == ""`)을 아예 안 보내므로, 이쪽에서 겹치려면 **우리가** 한 글자를
// 두 조각으로 쪼개거나 두 번 실어야 한다 — 그것을 잰다. GPU 는 필요 없다.

#[test]
fn a_wide_char_is_one_piece_that_owns_two_cells() {
    // 한 글자 = 한 조각 · 두 칸. 조각이 둘이 되면 그것이 곧 화면의 `조조` 다.
    let segs = SessionView::grid_segments("조직");
    assert_eq!(
        segs,
        vec![("조".to_owned(), 2), ("직".to_owned(), 2)],
        "폭 2 글자가 한 조각·두 칸이 아니다"
    );
}

#[test]
fn a_variation_selector_rides_along_instead_of_taking_a_cell() {
    // 제보(pytmux-389): 변이 선택자(U+FE0F)가 **자기 칸**을 차지해 그 줄이 한 칸씩
    // 밀렸고, 덤으로 `⚠`+U+FE0F 가 셰이퍼에 **따로** 들어가 색 이모지가 아니라 흑백
    // 글자로 그려졌다. 둘은 같은 뿌리다 — 칸을 나눌 때 **폭**을 물었기 때문이다.
    //
    // 재는 것 둘: ⑴ 칸의 합이 안 늘어난다 ⑵ 선택자가 **앞 조각에 붙어** 한 조각으로
    // 셰이퍼에 간다(갈라 놓으면 색이 죽는다).
    let segs = SessionView::grid_segments("|\u{26a0}\u{fe0f}|");
    let cells: usize = segs.iter().map(|(_, c)| c).sum();
    assert_eq!(
        cells,
        SessionView::grid_segments("|\u{26a0}|").iter().map(|(_, c)| c).sum::<usize>(),
        "선택자가 칸을 먹어 줄이 밀렸다: {segs:?}"
    );
    assert!(
        segs.iter().any(|(piece, _)| piece.chars().count() == 2
            && piece.starts_with('\u{26a0}')
            && piece.ends_with('\u{fe0f}')),
        "선택자가 앞 글자와 갈라졌다 — 색 이모지가 흑백으로 그려진다: {segs:?}"
    );
}

#[test]
fn a_grapheme_cluster_is_one_segment_so_the_shaper_draws_one_glyph() {
    // ★ pytmux-407 ⓐ — 사람이 고른 규약은 **군집의 폭 = 밑글자의 폭**이다(tmux 3.4 와
    //   같다). 서버 격자가 그렇게 셀을 짓고(`nativescreen`), 여기서도 그렇게 조각을
    //   나눠야 셰이퍼가 **한 글리프**를 그린다. 갈라 넘기면 `👨‍👩‍👧` 가 이모지 셋으로
    //   그려진다 — 맥에서 화소로 확인된 그 증상이다(2026-08-31 코멘트).
    //
    // 재는 것 둘: ⑴ 한 조각인가 ⑵ 그 조각이 **밑글자만큼만** 칸을 쓰나.
    let family = "\u{1f468}\u{200d}\u{1f469}\u{200d}\u{1f467}";
    let segs = SessionView::grid_segments(&format!("|{family}|"));
    assert!(
        segs.iter().any(|(piece, cells)| piece == family && *cells == 2),
        "가족 이모지가 한 조각(폭 2)이 아니다 — 낱개로 그려진다: {segs:?}"
    );
    assert_eq!(
        segs.iter().map(|(_, c)| c).sum::<usize>(),
        4,
        "군집이 밑글자보다 많은 칸을 먹었다 — 그 줄이 밀린다: {segs:?}"
    );
    // 깃발(지역 지시자 둘)과 피부톤 수정자도 같은 규칙이다.
    let flag = "\u{1f1f0}\u{1f1f7}";
    assert!(
        SessionView::grid_segments(flag).iter().any(|(p, c)| p == flag && *c == 2),
        "국기가 두 조각으로 갈렸다: {:?}",
        SessionView::grid_segments(flag)
    );
    let thumb = "\u{1f44d}\u{1f3ff}";
    assert!(
        SessionView::grid_segments(thumb).iter().any(|(p, c)| p == thumb && *c == 2),
        "피부톤 수정자가 제 칸을 차지했다: {:?}",
        SessionView::grid_segments(thumb)
    );
}

#[test]
fn two_unrelated_emoji_are_not_folded_into_one_cluster() {
    // ⛔ 대조군 — 「이모지끼리는 붙인다」는 판이면 위 시험은 통과하고 **글자가 사라진다**
    //    (한 칸에 둘을 넣으면 뒤엣것이 안 그려진다). 옆의 `쓸쓸` 오라클과 같은 이유로
    //    둔다: 표시 결함을 고치다 **자료 결함**을 만드는 것이 이 부류의 유혹적인 오답이다.
    let segs = SessionView::grid_segments("\u{1f44d}\u{1f44e}");
    assert_eq!(
        segs.len(),
        2,
        "이어질 이유가 없는 이모지 둘을 한 조각으로 접었다: {segs:?}"
    );
    assert_eq!(segs.iter().map(|(_, c)| c).sum::<usize>(), 4, "{segs:?}");
    // 완성된 깃발 **뒤**에서는 새 깃발이 시작한다(홀짝을 안 세면 넷이 한 덩어리가 된다).
    let two_flags = SessionView::grid_segments("\u{1f1f0}\u{1f1f7}\u{1f1ef}\u{1f1f5}");
    assert_eq!(two_flags.len(), 2, "깃발 둘을 하나로 접었다: {two_flags:?}");
}

#[test]
fn a_zero_width_char_at_the_head_of_a_line_is_dropped_not_given_a_cell() {
    // 얹힐 앞 글자가 없으면 놓을 칸도 없다. **버리되 칸은 안 준다** — 칸을 주면
    // 그 줄 전체가 밀리고, 그건 이 결함의 증상 그대로다.
    let segs = SessionView::grid_segments("\u{fe0f}ab");
    assert_eq!(
        segs.iter().map(|(_, c)| c).sum::<usize>(),
        2,
        "줄 앞머리의 폭 0 글자가 칸을 먹었다: {segs:?}"
    );
}

#[test]
fn legitimately_repeated_wide_chars_are_not_folded_away() {
    // ⛔ 이 결함의 **유혹적인 오답**이 「연속 중복 접기」다(정본 오라클 머리말 ②).
    //    넣는 순간 표시 결함이 **데이터 결함**으로 바뀐다 — `쓸쓸` 에서 글자가 조용히
    //    사라진다. 그래서 ①(안 늘린다)과 ②(안 줄인다)를 같은 파일에 둔다.
    let segs = SessionView::grid_segments("쓸쓸");
    assert_eq!(segs, vec![("쓸".to_owned(), 2), ("쓸".to_owned(), 2)]);
}

#[test]
fn a_hangul_line_from_the_server_is_painted_once() {
    // 서버가 보낸 그대로(연속칸 없음)를 프레임까지 태워 본다 — 조각 단위가 아니라
    // **그려진 줄**에서 제보 모양이 안 나오는지 본다.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["이 Claude는 조직 보안 정책에", {}]]],
        "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen], &[]);
    // ⚠ 폭 2 글자는 **조각마다 따로** 그려지므로(`grid_segments`) 한 줄이 여러 조각으로
    //   온다 — 그래서 이어 붙여서 본다. 이어 붙이면 제보 사진과 같은 글이 된다.
    let line: String = painted.concat();
    assert!(line.contains("Claude"), "그 줄이 프레임에 없다: {painted:?}");
    for ch in ['조', '직', '보', '안'] {
        let n = line.matches(ch).count();
        assert_eq!(
            n, 1,
            "`{ch}` 가 {n}번 그려졌다 — 제보의 `조조직직` 이 바로 이 모양이다: {line:?}"
        );
    }
}

// ── 칸을 넘는 글리프를 자르지 않는다(pytmux-270) ──────────────────────────────

#[test]
fn a_wide_glyph_is_pinned_to_its_cells_without_being_clipped() {
    // ⛔ 종전에는 비 ASCII 조각을 `ConstrainedBox` 로 `칸수 × 칸너비` 에 묶었다. 그
    //    상자는 아이의 `constraint.max` 를 좁히고, `Text` 는 그 값을 그대로
    //    `layout_line(.., max_width, clip_config)` 에 넘긴다 — 그리고
    //    `ClipConfig::default()` 는 `{direction: End, style: Fade}` 다.
    //    ⇒ 넘치는 만큼이 **오른쪽에서 지워졌다**(제보의 `★`·`❋` 가 오른쪽이 잘리고
    //    `⚠️` 가 왼쪽 반쪽만 남은 그 그림).
    //
    // 창 없이 화소는 못 재므로(이 파일 머리말 — `Scene` 은 glyph_key 만 준다) 재는 것은
    // **한도를 내려보내지 않는다**는 성질이다. 그것이 자르기의 유일한 원인이기 때문이다.
    for (what, body) in [
        ("캔버스 줄", source_after("let mut cell = cell.finish();", 500)),
        ("격자 한 줄", source_after("fn mono_row(", 900)),
    ] {
        assert!(
            body.contains("CellBox::new("),
            "{what}: 칸 상자가 `CellBox` 가 아니다 — 다시 글리프가 잘린다: {body}"
        );
        assert!(
            !body.contains("ConstrainedBox::new(cell)"),
            "{what}: 아직 한도를 내려보내는 상자를 쓴다: {body}"
        );
    }
    // 그리고 그 상자가 **실제로 한도를 안 내려보내는지** — 이름만 바꾸면 위는 통과한다.
    let boxed = source_after("impl Element for CellBox {", 900);
    assert!(
        boxed.contains("f32::INFINITY"),
        "`CellBox` 가 가로 한도를 내려보낸다 — 그러면 종전과 같다: {boxed}"
    );
    // 격자는 여전히 우리가 정한 값이라야 한다(안 그러면 이번엔 줄이 밀린다).
    assert!(
        boxed.contains("vec2f(self.width, child.y())"),
        "`CellBox` 가 잰 값을 돌려준다 — 칸이 밀린다: {boxed}"
    );
}

// ── 캔버스 팔레트 — 「밝은 쪽이 더 밝다」(pytmux-187) ─────────────────────────
//
// ⛔ 이 축에는 **팔레트 취향이 안 섞인다.** 어느 표를 고르든(tokyonight·Campbell·
//    사용자 것) 밝은 변형은 제 기준색보다 밝아야 한다 — 안 그러면 `SGR 93`·`96`
//    (밝게 강조)이 `33`·`36` 보다 어둡게 그려져 **강조가 뜻과 반대로** 나온다.
//    그래서 「어느 팔레트인가」를 기다리지 않고 쟀다. ★ 그 물음은 그 뒤 **xterm 표준**
//    으로 답해졌지만(pytmux-187 · 사람의 결정 2026-08-24) 이 오라클은 그대로 산다 —
//    표가 아니라 표가 지켜야 할 성질을 재기 때문이다. 지금 표에서도 여덟 짝 전부 참이다.
//
// 실측(2026-08-23)으로 여덟 짝 중 **둘이 뒤집혀 있었다**(노랑·청록). 밝은 마젠타는
// 같은 부류가 먼저 잡혀 고쳐진 자리다 — 그때는 「값이 겹친다」로 읽혔고, 이번에
// 재 보니 겹침이 아니라 **뒤집힘**이 본체였다.

/// 사람 눈이 느끼는 밝기(Rec. 709). 채도·색상을 안 보는 이유: 밝은 변형이 **다른 색상**
/// 인 것은 팔레트의 자유이고(밝은 노랑이 주황 쪽으로 도는 표가 흔하다), 여기서 재는
/// 것은 그 자유 밖의 성질 하나다.
fn luminance(c: warpui::color::ColorU) -> f32 {
    0.2126 * c.r as f32 + 0.7152 * c.g as f32 + 0.0722 * c.b as f32
}

#[test]
fn the_bright_half_of_every_pair_is_actually_brighter() {
    let pairs: [(&str, proto::style::NamedColor, proto::style::NamedColor); 8] = [
        ("검정", proto::style::NamedColor::Black, proto::style::NamedColor::BrightBlack),
        ("빨강", proto::style::NamedColor::Red, proto::style::NamedColor::BrightRed),
        ("초록", proto::style::NamedColor::Green, proto::style::NamedColor::BrightGreen),
        ("노랑", proto::style::NamedColor::Yellow, proto::style::NamedColor::BrightYellow),
        ("파랑", proto::style::NamedColor::Blue, proto::style::NamedColor::BrightBlue),
        ("마젠타", proto::style::NamedColor::Magenta, proto::style::NamedColor::BrightMagenta),
        ("청록", proto::style::NamedColor::Cyan, proto::style::NamedColor::BrightCyan),
        ("흰색", proto::style::NamedColor::White, proto::style::NamedColor::BrightWhite),
    ];
    for (name, base_c, bright_c) in pairs {
        let b = SessionView::named_for_test(base_c);
        let br = SessionView::named_for_test(bright_c);
        assert_ne!(
            (b.r, b.g, b.b),
            (br.r, br.g, br.b),
            "{name}: 밝은 쪽이 기준색과 **같은 값**이다 — SGR 3x 와 9x 가 한 색으로 붙는다"
        );
        assert!(
            luminance(br) > luminance(b),
            "{name}: 밝은 쪽이 더 어둡다(기준 {:.1} · 밝은 {:.1}) — 강조가 뜻과 반대로 그려진다",
            luminance(b),
            luminance(br)
        );
    }
}

/// 두 패널짜리 판 — 활성 패널이 **창 왼쪽 끝이 아니다**(자리 산수를 재려면 필요하다).
fn layout_two_panes_active_right() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 2,
        "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 40, "h": 24, "title": "sh", "active": false},
            {"id": 2, "x": 41, "y": 0, "w": 39, "h": 24, "title": "sh", "active": true}
        ]
    }))
    .unwrap()
}

/// 어느 패널의 화면 한 장 — 커서를 그 패널 안 `(cx, cy)` 에 둔다.
///
/// 옆의 [`screen_with_cursor`] 는 패널 1 에 못박혀 있어 **활성 패널이 다른** 판을 못
/// 만든다(자리 산수를 재려면 그 판이 필요하다).
fn pane_screen_with_cursor(pane: i64, cx: u16, cy: u16) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": pane,
        "rows": [[["HELLO", {}]]], "cursor": [cx, cy], "wrap": [], "top": 0
    }))
    .unwrap()
}

#[test]
fn the_compose_panel_stands_on_the_pane_cursor_line_not_at_the_window_bottom() {
    // 제보(pytmux-370 · 첨부 5장): *"cli 쪽 의도는 현재 커서가 있는 줄에 팝업이 정확히
    // 나타나는 것입니다. 그런데 gui는 완전히 별도 위치에 팝업이 나타난다"*.
    //
    // 정본 `ComposePromptScreen.on_mount` 의 산수를 그대로 옮겼는지 잰다:
    //  · 가로 — 활성 패널 **안쪽 x** 에 맞추고 폭도 그 패널 폭이다(창 전폭이 아니다).
    //  · 세로 — 입력 줄이 커서 줄 **한 칸 아래**에 오게 바닥에서 띄운다.
    //
    // ⚠ 헤드리스에는 자리표가 없어 칸 크기를 못 잰다 — 그 값을 시험이 대신 넣는다
    //   (`note_*_for_test`). 재는 것은 «자리표를 어떻게 쓰나»이지 «자리표를 잘 재나»가
    //   아니다(뒤엣것은 `report_size` 의 몫이다).
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_two_panes_active_right()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(pane_screen_with_cursor(2, 3, 5)))).unwrap();
    view.pump_headless();
    view.note_cell_size(10., 20.);
    view.note_canvas_box_for_test(5., 7., 800., 600.);

    let (left, bottom, width) =
        view.pane_cursor_box_for_test().expect("자리를 못 쟀다 — 값을 다 넣었는데도");
    // 왼쪽 = 캔버스 여백 + 패널 안쪽 x(41칸).
    assert_eq!(left, 5. + 41. * 10., "판이 활성 패널의 왼쪽에 안 붙는다");
    // 폭 = 그 패널의 폭(39칸). ⛔ 창 전폭(800)이면 종전 그림 그대로다.
    assert_eq!(width, 39. * 10., "판이 아직 창 전폭이다");
    // 아래 여백 = 창 높이 - (캔버스 위 여백 + (패널 y + 커서 행 + 2) * 칸높이).
    // ⚠ **캔버스 위 여백(7)을 빠뜨리면 안 된다** — 캔버스는 창 꼭대기에서 시작하지 않는다
    //   (탭바가 위에 있다). 그것을 빼먹으면 판이 그만큼 아래로 내려간다.
    assert_eq!(bottom, 600. - (7. + (0. + 5. + 2.) * 20.), "입력 줄이 커서 줄에 안 선다");
    assert!(bottom > 0., "바닥에 붙어 버렸다 — 커서 줄을 안 본다");
}

#[test]
fn the_compose_panel_falls_back_instead_of_guessing_a_place() {
    // ⛔ 대조군이자 규율: **못 재면 짐작하지 않는다**(`Anchor::AtPaneCursor` 머리말).
    //    자리표가 없는 첫 프레임에 자리를 지어내면 그 그림이 새 거짓말이 된다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_two_panes_active_right()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(pane_screen_with_cursor(2, 3, 5)))).unwrap();
    view.pump_headless();
    assert!(
        view.pane_cursor_box_for_test().is_none(),
        "칸 크기를 아직 못 쟀는데 자리를 지어냈다"
    );
    // 커서가 패널 **밖**인 프레임도 같다 — 밀린 자리에 상자를 그리지 않는다.
    view.note_cell_size(10., 20.);
    view.note_canvas_box_for_test(5., 7., 800., 600.);
    tx.send(LinkEvent::Message(Box::new(pane_screen_with_cursor(2, 3, 99)))).unwrap();
    view.pump_headless();
    assert!(
        view.pane_cursor_box_for_test().is_none(),
        "커서가 패널 밖인데 그 자리에 판을 세운다"
    );
}

#[test]
fn the_view_actually_routes_the_compose_panel_through_that_math() {
    // ★ 위 둘은 **산수**만 잰다. 그리는 자리가 그 함수를 안 부르면 화면은 종전 그대로인데
    //   시험은 전부 통과한다(이 파일이 여러 번 물린 「호출 제거」 뮤테이션).
    let body = source_after("fn render_screen_panel(", 14000);
    assert!(
        body.contains("place_at_pane_cursor"),
        "판을 그리는 자리가 커서 줄 배치를 안 부른다: {body}"
    );
    // 그리고 **전폭 `Expanded`** 는 사라졌어야 한다 — 커서 줄에 서면 폭은 패널 폭이다.
    assert!(
        !body.contains("Expanded::new(1., panel)"),
        "작성창이 아직 창 전폭으로 늘어난다"
    );
}

#[test]
fn the_compose_panel_does_not_dim_what_you_are_writing_about() {
    // 제보(pytmux-370 · 첨부 5장): GUI 는 작성창 뒤를 딤으로 덮어 스크롤백이 안 읽힌다.
    // 정본은 아무것도 어둡게 하지 않는다 — 작성창은 **위에 보이는 것을 보면서 쓰는
    // 자리**라, 뒤가 안 보이면 그 화면의 값이 절반 사라진다.
    //
    // 판정의 주인은 core 다(자리표와 같은 결) — 뷰가 각자 정하면 클라마다 달라진다.
    assert!(
        !base::screens::Screen::Compose.dims_behind(),
        "작성창이 아직 뒤를 가라앉힌다"
    );
    // ⚠ 나머지는 종전대로다 — 제보는 작성창 하나를 말했다.
    for screen in [
        base::screens::Screen::Commands,
        base::screens::Screen::Confirm,
        base::screens::Screen::Notices,
    ] {
        assert!(screen.dims_behind(), "{screen:?} 의 딤이 사라졌다(범위 밖 변경)");
    }
    // 그리고 뷰가 그 자료를 **실제로 본다** — 안 보면 위 단언은 공허하다.
    let body = source_after("let mut body = Stack::new()", 1400);
    assert!(
        body.contains("screen.dims_behind()"),
        "뷰가 화면별 딤 판정을 안 본다 — 다시 전부 덮는다: {body}"
    );
}

#[test]
fn a_filler_row_is_exactly_as_tall_as_a_real_one() {
    // ⛔ **줄 수만 재던 오라클이 이 결함을 통과시켰다**(pytmux-369). `pad_rows` 는 줄
    //    수를 늘 예산만큼 채웠지만, 채움 줄은 맨 글자이고 항목 줄은 1px 사방 패딩
    //    컨테이너라 **한 줄이 2px 낮았다** — 목록이 짧을수록(채움이 많을수록) 판이
    //    그만큼 짧아져, 분류 탭을 옮길 때마다 판이 들썩였다.
    //
    //    루트 CLAUDE.md 가 경고하는 *"값을 만드는 헬퍼만 테스트하면 공허 통과"* 의 실례라,
    //    여기서는 **한 줄을 짓는 자리가 하나**라는 것을 소스로 못박는다(뮤테이션 '호출
    //    제거'에도 걸리게).
    let pad = source_after("fn pad_rows(", 700);
    assert!(
        pad.contains("self.panel_row_box("),
        "채움 줄이 판의 줄 상자를 안 쓴다 — 다시 2px 씩 어긋난다: {pad}"
    );
    let item = source_after("fn palette_item(", 3000);
    assert!(
        item.contains("self.panel_row_box(line)"),
        "항목 줄이 판의 줄 상자를 안 쓴다: {item}"
    );
    // 그리고 그 상자가 **실제로 감싸는지** — 빈 함수로 만들면 위 둘은 통과한다.
    let helper = source_after("fn panel_row_box(", 400);
    assert!(
        helper.contains("with_uniform_padding"),
        "줄 상자가 아무것도 안 한다: {helper}"
    );
    // ★ **`pad_rows` 를 쓰는 판은 전부** 그 상자를 지나야 한다(pytmux-373 ⑴). 팔레트
    //   하나만 고쳤던 동안 나머지 셋은 채움 줄보다 2px 낮은 내용 줄을 그렸고, 그것이
    //   "탭을 바꾸면 판이 들썩인다"였다. Status 판은 아래 픽셀 오라클이 따로 잰다 —
    //   여기서는 픽셀 오라클이 없는 나머지 둘까지 **한 줄로** 못박는다.
    for (head, what) in [
        ("fn render_info_tabs(", "Status 판"),
        ("fn render_notices(", "알림 판"),
    ] {
        let body = source_after(head, 7000);
        assert!(
            body.contains("self.panel_row_box("),
            "{what} 의 내용 줄이 판의 줄 상자를 안 쓴다 — 채움 줄과 2px 씩 어긋난다: {body}"
        );
    }
    // 플러그인 글 판은 함수가 길어 `pad_rows` 앞 구간만 본다(목록 갈래는 제 상자가 있다).
    let plugin = source_after("let max_scroll = body.lines().count()", 2200);
    assert!(
        plugin.contains("self.panel_row_box("),
        "플러그인 글 판의 내용 줄이 판의 줄 상자를 안 쓴다: {plugin}"
    );
}

#[test]
fn the_palette_panel_is_the_same_height_whatever_the_list_length() {
    // 제보(pytmux-369 · 첨부 2장): 분류 탭만 바꿨는데 판 위 모서리가 약 18px 내려왔다.
    // 바닥은 붙박이라 **위 모서리만** 움직인다 — 곧 판이 짧아진 것이다.
    //
    // 재는 것은 그림이 아니라 **줄 수 × 한 줄 높이**의 불변식이다: 채움이 몇이든 판이
    // 쓰는 줄 수는 예산으로 같고(pytmux-58), 한 줄 높이도 이제 한 곳이 정한다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    let budget = view.panel_budget_for_test();
    assert!(budget >= 5, "예산이 너무 작아 단언이 뜻을 잃는다: {budget}");
    // 목록이 하나든 예산만큼이든 **채움 뒤 총 줄 수는 같다**.
    for drawn in [0usize, 1, budget / 2, budget] {
        // ⚠ 이 헬퍼는 **총 줄 수**를 준다(채움 수가 아니다 — 이름이 그렇게 읽힌다).
        assert_eq!(
            view.pad_rows_count_for_test(drawn, budget),
            budget,
            "목록 길이 {drawn} 에서 판이 쓰는 줄 수가 달라졌다"
        );
    }
}

#[test]
fn a_long_line_is_cut_so_it_cannot_push_the_panel_out() {
    // ⓚ2 — `p4changes` 의 CL 설명 한 줄이 그 부류다. 폭을 못박아도 줄이 안 잘리면
    // 상한을 넘겨 밀고 나간다.
    let long = "x".repeat(400);
    let cut = proto::footer::elide(&long, 110);
    assert!(cut.chars().count() <= 110, "안 잘렸다: {}", cut.chars().count());
    assert!(cut.ends_with('…'), "잘렸으면 그 사실이 보여야 한다: {cut}");
    // 짧은 줄은 안 건드린다(자르는 것이 목적이 아니다).
    assert_eq!(proto::footer::elide("짧다", 110), "짧다");
}

// ── 모드 표식·켜짐 표시(§10-21ⓖ·ⓧ) ─────────────────────────────────────────
//
// 두 제보가 **같은 그림**을 요구한다: 배경을 채운 반전 칩. ⓖ 는 `esc` 모드를 눈에 띄게,
// ⓧ 는 토글이 켜졌다는 것을 배지가 말하게.

#[test]
fn the_mode_badge_sits_in_the_bottom_status_bar_not_the_tab_bar() {
    // 프레임은 위에서 아래로 그려진다 — 탭바에 남아 있으면 캔버스보다 **먼저**,
    // 상태줄로 갔으면 **나중에** 그려진다(감시류 배지와 같은 방법으로 잰다).
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen], &[(Key::Escape, Mods::NONE)]);
    // 표식 문구는 `InputMode::badge()` 가 쥔다(pytmux-380 에서 `[esc]` → 정본 문구로
    // 바뀌었다) — 리터럴을 박아 두면 그 자리를 옮길 때마다 이 오라클이 낡는다.
    let badge = InputMode::Command.badge().expect("표식이 없다");
    let mode_at = painted.iter().position(|t| t == badge);
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    assert!(mode_at.is_some(), "esc 모드 표식이 프레임에 없다: {painted:?}");
    assert!(canvas_at.is_some(), "캔버스가 없다 — 단언이 공허해진다: {painted:?}");
    assert!(
        mode_at > canvas_at,
        "모드 표식이 캔버스보다 먼저 그려졌다(=탭바 자리 그대로다): {painted:?}"
    );
}

/// ⛔ **지금 배지 넷은 전부 «버튼»이다 — 토글이 하나도 없다**(pytmux-377).
///
/// 종전에는 `⇕`(터치 스크롤)가 유일한 토글이었고, 그 옆에 *"켜졌으면 배지가 그렇게
/// 말한다"*(§10-21ⓧ)를 재는 짝 오라클이 있었다. 그 배지를 걷었으므로 여기 남는 것은
/// **반쪽뿐**이다 — 누르면 화면이 열리는 버튼은 켜짐을 말할 것이 없다.
///
/// ⚠ 토글 배지를 새로 들이면 `badge_is_on` 이 **그것만은 `true`** 로 답해야 하고, 그때
/// 이 시험은 그 배지를 목록에서 빼고 짝 오라클을 되살리는 자리다(색 규약은 바로 아래
/// `on_and_picked_are_different_pictures` 가 그대로 지키고 있다).
#[test]
fn a_button_badge_is_never_on() {
    let (view, _tx, _sent) = harness();
    for badge in [base::Badge::Notices, base::Badge::Host, base::Badge::Clock, base::Badge::Calendar] {
        assert!(!view.badge_is_on_for_test(badge), "{badge:?} 가 켜짐으로 나온다");
    }
}

#[test]
fn on_and_picked_are_different_pictures() {
    // ⚠ FOCUS 는 "키보드가 이것을 고르고 있다"이고 반전은 "켜져 있다"다. 색이 같으면
    //   그 둘이 한 그림이 되어 어느 쪽인지 알 수 없다.
    assert_ne!(theme::INVERT_BG, theme::FOCUS);
    // 반전 칩은 배경 위에 글자를 빼낸다 — 둘이 같으면 글자가 안 보인다.
    assert_ne!(theme::INVERT_BG, theme::INVERT_FG);
}

// ── Alt+Tab 동선(§10-21ⓕ·ⓔ2·ⓕ2) ────────────────────────────────────────────
//
// 셋이 한 이야기다: 스위처를 열면 커서가 **다음 탭**에 있고(ⓔ2), `Ctrl` 을 쥔 채 `Tab`
// 으로 돌며(ⓕ2), 짧게 눌렀다 떼면 그것이 곧 "다음 탭으로"다(ⓕ).

#[test]
fn the_switcher_opens_on_the_next_tab_not_the_first() {
    // 정본이 일부러 그렇게 한다 — `esc Tab Enter` 가 곧 "다음 탭으로 전환"이어야 한다.
    let (mut view, tx, _sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.apply_action_for_test(base::Action::ShowTabs);
    assert_eq!(view.screens.top(), Some(base::screens::Screen::Tabs));
    assert_eq!(view.screens.selected(), 1, "첫 선택이 '다음 탭'이 아니다");
}

#[test]
fn a_single_tab_does_not_open_the_switcher() {
    // 고를 것이 없는 목록을 띄우는 것은 "아무 일도 안 일어난다"와 같다(정본과 같은 규칙).
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(serde_json::json!({"t": "status", "windows": [
            {"index": 0, "name": "하나", "active": true},
        ]}))
        .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    view.apply_action_for_test(base::Action::ShowTabs);
    assert_eq!(view.screens.top(), None, "탭이 하나뿐인데 스위처가 열렸다");
}

#[test]
fn ctrl_tab_is_the_switcher_chord() {
    assert_eq!(SessionView::tab_switch_chord(&ks("tab", true, false, false)), Some(true));
    assert_eq!(
        SessionView::tab_switch_chord(&ks("tab", true, false, true)),
        Some(false),
        "Shift 가 섞이면 뒤로다"
    );
    // 평범한 Tab 은 **패널의 것**이다(셸 자동완성) — 가로채면 조용히 사라진다.
    assert_eq!(SessionView::tab_switch_chord(&ks("tab", false, false, false)), None);
    assert_eq!(SessionView::tab_switch_chord(&ks("tab", true, true, false)), None);
    assert_eq!(SessionView::tab_switch_chord(&ks("a", true, false, false)), None);
}

#[test]
fn holding_ctrl_walks_the_list_and_releasing_confirms() {
    // ★ 이것이 ⓕ2 의 전부다: 쥔 채 돌고, 떼면 확정.
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.alt_tab_step_for_test(true); // Ctrl+Tab — 열리고 커서는 '다음 탭'
    assert_eq!(view.screens.top(), Some(base::screens::Screen::Tabs));
    assert_eq!(view.screens.selected(), 1);
    view.alt_tab_step_for_test(true); // 쥔 채 한 번 더 — 한 줄 아래
    assert_eq!(view.screens.selected(), 2, "쥔 채 누른 Tab 이 목록을 안 움직였다");
    view.release_ctrl(); // 뗌 = 확정
    view.pump_headless();
    assert_eq!(view.screens.top(), None, "떼도 판이 안 닫혔다");
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Command(Command::SelectWindow { .. }))),
        "확정했는데 탭 전환이 안 나갔다: {out:?}"
    );
}

#[test]
fn a_quick_ctrl_tab_is_just_next_tab() {
    // ⓕ 의 동작은 ⓕ2 에서 **저절로** 나온다 — 열자마자 커서가 다음 탭이므로.
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.alt_tab_step_for_test(true);
    view.release_ctrl();
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    let picked = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::SelectWindow { index, .. }) => Some(*index),
        _ => None,
    });
    assert_eq!(picked, Some(1), "짧게 눌렀다 뗀 것이 '다음 탭'이 아니다: {out:?}");
}

#[test]
fn releasing_ctrl_without_holding_does_nothing() {
    // 평소에 Ctrl 을 떼는 일은 흔하다 — 그때마다 무언가 일어나면 안 된다.
    let (mut view, tx, _sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(!view.release_ctrl());
    assert_eq!(view.screens.top(), None);
}

// ── 패널 커서(§10-21ⓒ) ───────────────────────────────────────────────────────
//
// 배선이 **통째로 없던** 자리다: 서버는 `screen` 마다 커서를 통째로 주는데 뷰가 그 값을
// 한 번도 안 읽었다. 그래서 여기서 재는 것은 "그 값이 화면 칸으로 옮겨지나"다.
//
// ⚠ **그림 자체는 이 하네스가 못 잰다.** 시험 글꼴은 칸 폭이 0이라 오버레이의 paint 가
// `cw <= 0.5` 가드에서 돌아간다(스플리터·테두리도 같은 이유로 여기서 안 그려진다) —
// 그 층은 `client/CLAUDE.md` 가 적은 대로 **라이브 스크린샷이** 잡는다. 여기서는 그
// 앞 단계(어느 칸인가)를 못 박는다.

fn screen_with_cursor(col: u16, row: u16) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO", {}]]], "cursor": [col, row], "wrap": [], "top": 0
    }))
    .unwrap()
}

#[test]
fn the_cursor_lands_on_the_cell_the_server_named() {
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(3, 2)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let cur = view.cursor_cell().expect("커서를 안 그린다 — 서버가 준 값을 아무도 안 읽는다");
    // 이 패널은 (0,0) 에서 시작하므로 패널 안 좌표가 곧 캔버스 좌표다.
    assert_eq!((cur.x, cur.y), (3, 2));
}

#[test]
fn the_cursor_follows_the_pane_offset() {
    // 패널이 캔버스 안쪽에 있으면 그만큼 밀려야 한다 — 안 밀면 분할했을 때 커서가
    // 왼쪽 패널에 나타난다(그 증상이 곧 ⓨ 의 모양이라 여기서 못 박는다).
    let layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 10, "active": 1,
        "panes": [{"id": 1, "x": 10, "y": 4, "w": 20, "h": 5, "title": "sh", "active": true}]
    }))
    .unwrap();
    let (mut view, tx, _sent) = harness();
    for msg in [layout, screen_with_cursor(2, 1)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let cur = view.cursor_cell().unwrap();
    assert_eq!((cur.x, cur.y), (12, 5));
}

#[test]
fn no_cursor_while_a_panel_is_open() {
    // 판이 떠 있는 동안 키는 그 판의 것이다 — 그때 패널 커서를 그리면 "여기 치면
    // 들어간다"는 거짓말이 된다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(1, 1)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.cursor_cell().is_some(), "먼저 그려지고 있어야 뜻이 있는 단언이다");
    view.screens.open(base::screens::Screen::Keys);
    assert!(view.cursor_cell().is_none());
}

#[test]
fn a_cursor_outside_the_pane_is_not_drawn() {
    // 밀린 자리에 상자를 그리면 그것이 새 거짓말이 된다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(200, 0)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.cursor_cell().is_none());
}

// ── 커서 모양·색·깜빡임(`pytmux/pytmux-161`) ─────────────────────────────────
//
// 제보는 "외곽선 네모 한 가지로 **고정**"이었다. 그래서 재는 것 셋이다:
// ⑴ 설정한 모양·색이 그리는 쪽까지 **실제로 간다** ⑵ 채운 네모는 오버레이가 아니라
// **캔버스가 반전으로** 낸다(덮어 칠하면 그 글자를 못 읽는다) ⑶ 깜빡임이 반주기마다
// 뒤집히고 **껐을 때 켜진 채로 남는다**.
//
// ⛔ ⑵ 를 부정 단언("오버레이가 안 그린다")으로만 두면 아무 데서도 안 그려도 통과한다.
//    `cursor_block_cell` 이 **어느 칸을 반전할지**를 직접 본다.

#[test]
fn the_configured_shape_and_color_reach_the_thing_that_draws_them() {
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(3, 2)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    // 기본은 종전 그대로 — 외곽선 네모 · 테마색(`None`).
    let cur = view.cursor_cell().expect("기본에서도 커서는 그려진다");
    assert_eq!(cur.style, crate::splitter::CursorStyle::Hollow);
    assert_eq!(cur.color, None, "빈 색 이름은 테마 그대로다");

    view.config.cursor_style = "bar".into();
    view.config.cursor_color = "#ff8800".into();
    let cur = view.cursor_cell().expect("모양을 바꿨다고 커서가 사라지면 안 된다");
    assert_eq!(cur.style, crate::splitter::CursorStyle::Bar);
    assert!(cur.color.is_some(), "설정한 색이 그리는 쪽까지 안 간다");
}

#[test]
fn an_unknown_shape_still_draws_something() {
    // 파서가 이미 어휘를 거르지만 설정 파일만이 이 값의 입구가 아니다(`set` 한 줄·
    // 설정 화면). 어느 길로 들어와도 커서가 사라지는 일은 없어야 한다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(1, 1)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.config.cursor_style = "blcok".into();
    let cur = view.cursor_cell().expect("모르는 낱말이 커서를 지웠다");
    assert_eq!(cur.style, crate::splitter::CursorStyle::Hollow);
}

#[test]
fn the_block_shape_is_drawn_by_the_canvas_not_by_the_overlay() {
    // ★ 오버레이는 캔버스를 **다 그린 뒤에** 얹힌다 — 거기서 칠하면 커서가 놓인 글자를
    //   못 읽는다. 그래서 채운 네모만 줄이 반전으로 낸다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(3, 2)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert_eq!(view.cursor_block_cell(), None, "외곽선은 줄이 반전할 일이 없다");

    view.config.cursor_style = "block".into();
    assert_eq!(view.cursor_block_cell(), Some((3, 2)), "반전할 칸을 줄에 안 알린다");
    // 그리고 오버레이 쪽은 그 칸을 **안 칠한다**(모양으로 그 사실이 말해진다).
    assert_eq!(
        view.cursor_cell().map(|c| c.style),
        Some(crate::splitter::CursorStyle::Block)
    );
}

#[test]
fn the_cursor_blinks_only_when_asked_and_comes_back_when_it_is_turned_off() {
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(1, 1)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    // 꺼져 있으면 아무리 재촉해도 안 뒤집힌다(종전 동작).
    assert!(!view.tick_cursor_blink_for_test(true));
    assert!(view.cursor_cell().is_some());

    view.config.cursor_blink = true;
    // 반주기가 아직 안 지났으면 그림이 안 바뀐다 — 30Hz 루프가 매 프레임 다시 그리면
    // 커서 하나 때문에 화면 전체를 초당 서른 번 그린다.
    assert!(!view.tick_cursor_blink_for_test(false), "주기 전에 뒤집혔다");
    assert!(view.tick_cursor_blink_for_test(true), "반주기가 지났는데 안 뒤집힌다");
    assert!(view.cursor_cell().is_none(), "「안 보임」 반주기인데 그린다");
    assert!(view.tick_cursor_blink_for_test(true));
    assert!(view.cursor_cell().is_some(), "다시 보이는 반주기로 안 돌아온다");

    // ⛔ 「안 보임」에서 껐는데 그대로 굳으면 커서가 **영영** 사라진다 — 되돌릴 입구는
    //    있어도, 커서 없는 화면에서 원인이 「깜빡임을 껐다」임을 알 길이 없다.
    assert!(view.tick_cursor_blink_for_test(true));
    assert!(view.cursor_cell().is_none(), "먼저 안 보이는 상태를 만들어야 뜻이 있다");
    view.config.cursor_blink = false;
    assert!(view.tick_cursor_blink_for_test(false), "껐는데 되살리지 않았다");
    assert!(view.cursor_cell().is_some(), "깜빡임을 껐는데 커서가 안 돌아온다");
}

// ── 커서 판(`cursor` · pytmux-375) ───────────────────────────────────────────
//
// 이 판이 새로 만든 것은 **화면이지 값이 아니다**(다섯은 이미 설정 화면에 있었다).
// 그래서 여기서 재는 것도 화면의 성질 넷이다: 입구가 실제로 여나 · 판이 고른 줄이 그
// 설정으로 옮겨지나 · 안 먹는 줄이 **정말로 안 먹나** · 제 것 아닌 키가 판을 안 닫나.

/// 커서 판이 떠 있는 뷰. `harness` 를 지나는 이유는 **설정 쓰기를 사물함으로 돌리려고**
/// 다(이 판의 키가 실제로 설정 파일을 고친다 — 안 돌리면 돌린 사람의 진짜 config 가 바뀐다).
fn cursor_panel() -> SessionView {
    let (mut view, _tx, _sent) = harness();
    view.pump_headless();
    // ★ **팔레트를 지난다** — 이 판에는 키가 없어서 팔레트가 유일한 입구다. 여기서
    //   `screens.open` 을 직접 부르면 「입구가 있나」는 한 번도 안 재진다.
    let entry = base::PALETTE
        .iter()
        .find(|e| e.name == "cursor")
        .expect("팔레트에 `cursor` 가 없다 — 이 판에 들어갈 길이 없다");
    assert!(view.apply_action(entry.action), "팔레트 줄이 아무 일도 안 했다");
    assert_eq!(view.screens.top(), Some(Screen::Cursor), "그 줄이 다른 판을 열었다");
    view
}

/// 커서 판에서 그 설정 줄이 몇 번째인가.
fn cursor_row_of(key: &str) -> usize {
    base::config::CURSOR_SETTINGS
        .iter()
        .position(|k| *k == key)
        .unwrap_or_else(|| panic!("커서 판에 {key} 줄이 없다"))
}

// ── N13 정보의 그림(pytmux-462) — 값은 서버 것, 표현만 ──────────────────────

/// RTT 표본을 몇 개 심고 상태 판을 연 뷰.
fn info_with_rtt(samples: &[f64]) -> SessionView {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    let now = view.pinger.now();
    for (i, ms) in samples.iter().enumerate() {
        view.state.rtt_mut().sample(now - (samples.len() - i) as f64, ms / 1000.);
    }
    view
}

#[test]
fn the_rtt_graph_rows_are_found_by_asking_the_graph_not_by_reading_the_text() {
    // ⛔ 글자를 보고 「이건 그래프 같다」로 고르지 않는다 — 같은 그래프를 낸 함수의
    //    출력과 그대로 견준다. 표본이 없으면 그래프도 없고, 그때는 한 줄도 안 고른다.
    let view = info_with_rtt(&[]);
    let lines = proto::info::tabs(&view.state, "/tmp/x.sock", view.pinger.now());
    let server = lines.iter().find(|(name, _)| name.contains("서버")).expect("서버 탭이 없다");
    let (rows, _) = view.rtt_graph_rows(&server.1);
    assert!(rows.is_empty(), "표본이 없는데 그래프 줄을 골랐다");

    let view = info_with_rtt(&[12., 40., 8., 90., 30.]);
    let lines = proto::info::tabs(&view.state, "/tmp/x.sock", view.pinger.now());
    let server = lines.iter().find(|(name, _)| name.contains("서버")).expect("서버 탭이 없다");
    let (rows, first) = view.rtt_graph_rows(&server.1);
    assert_eq!(
        rows.len(),
        proto::rtt::GRAPH_H,
        "그래프 줄 수가 {} 가 아니다",
        proto::rtt::GRAPH_H
    );
    assert!(rows.contains(&first), "첫 줄이 목록에 없다");
    // 고른 줄이 실제로 **막대 글자**를 든 줄이라야 한다(자리를 한 칸 밀면 여기서 죽는다).
    let picked = &server.1[first];
    assert!(
        picked.chars().any(|c| "▁▂▃▄▅▆▇█┄".contains(c)),
        "고른 줄이 그래프 줄이 아니다: {picked:?}"
    );
}

#[test]
fn one_rule_makes_both_the_bars_and_the_block_characters() {
    // ⛔ 채우는 규칙이 두 벌이면 GUI 의 막대와 정본의 글자가 **다른 높이**를 말한다.
    //    글자 그래프는 이제 `graph_cells` 를 접어 쓰므로 그럴 길이 없다 — 그 사실을
    //    같은 자료로 두 번 물어 잰다.
    let view = info_with_rtt(&[12., 40., 8., 90., 30.]);
    let now = view.pinger.now();
    let data = view
        .state
        .rtt()
        .graph_data(now, proto::rtt::GRAPH_W, proto::rtt::GRAPH_H)
        .expect("표본을 넣었는데 그래프가 없다");
    let grid = proto::rtt::graph_cells(&data, proto::rtt::GRAPH_W, proto::rtt::GRAPH_H);
    let text = view
        .state
        .rtt()
        .graph_lines(now, proto::rtt::GRAPH_W, proto::rtt::GRAPH_H)
        .expect("글자 그래프가 없다");
    const BLOCKS: [char; 9] = [' ', '▁', '▂', '▃', '▄', '▅', '▆', '▇', '█'];
    for (r, row) in grid.iter().enumerate() {
        // 글자 줄은 축 여섯 칸을 앞에 둔다(제목 한 줄 뒤가 첫 막대 줄이다).
        let cells: Vec<char> = text[r + 1].chars().skip(6).collect();
        for (c, cell) in row.iter().enumerate() {
            if cell.gap || cell.on_threshold {
                continue; // 그 자리는 글자 쪽이 축·빈칸 표기를 따로 쓴다
            }
            assert_eq!(
                cells[c],
                BLOCKS[cell.eighths as usize],
                "줄 {r} 칸 {c}: 막대와 글자가 다른 높이를 말한다"
            );
        }
    }
}

#[test]
fn the_graph_row_draws_bars_and_keeps_its_axis_number() {
    // 양성 오라클 — 막대가 실제로 늘고, 축의 **숫자는 남는다**(값을 그림으로 바꾸면
    // 정확한 값을 잃는다 · 사용량 막대와 같은 판단).
    let view = info_with_rtt(&[12., 40., 8., 90., 30.]);
    let lines = proto::info::tabs(&view.state, "/tmp/x.sock", view.pinger.now());
    let server = lines.iter().find(|(name, _)| name.contains("서버")).unwrap();
    let (rows, first) = view.rtt_graph_rows(&server.1);
    assert!(!rows.is_empty(), "그래프가 없다 — 이 오라클이 잴 것이 없다");
    let axis: String = server.1[first].chars().take(6).collect();
    assert!(
        axis.chars().any(|c| c.is_ascii_digit()),
        "축에 숫자가 없다: {axis:?}"
    );
}

#[test]
fn the_graph_rows_are_actually_swapped_for_bars() {
    // ⛔ **「호출 제거」 뮤테이션**(시계·달력의 짝). 위 오라클들은 줄을 **고르는** 함수와
    //    막대를 **만드는** 함수를 각각 재는데, 본문 루프에서 그 둘을 잇는 갈래를 지우면
    //    화면은 종전 글자 그래프로 조용히 되돌아가고 전부 초록이다.
    const VIEW: &str = include_str!("session_view.rs");
    assert!(
        VIEW.contains("None if spark.contains(&(row - actions.len())) =>"),
        "본문 루프가 그래프 줄을 막대로 안 바꾼다 — 만드는 함수가 멀쩡해도 화면은 글자다"
    );
    assert!(
        VIEW.contains("self.rtt_spark_row("),
        "막대를 그리는 함수를 아무도 안 부른다"
    );
}

#[test]
fn the_prompt_history_candidates_are_already_native_rows() {
    // pytmux-462 의 「프롬프트 이력 바 — 표현 확인 필요」에 대한 답이다.
    // 우리 후보 목록은 **글자 바가 아니라 줄 위젯**이다(고른 줄이 배경으로 말한다) —
    // 그 사실을 재서, 다음 사람이 「아직 글자 바다」로 다시 열지 않게 한다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.screens.ask(base::screens::Prompt::RenameTab, "");
    view.screens
        .set_prompt_history(vec!["build".into(), "buildall".into()]);
    view.handle_key(Key::Char('b'), Mods::NONE);
    let matches = view.screens.prompt_matches();
    assert!(!matches.is_empty(), "후보가 안 좁혀졌다 — 이 오라클이 잴 것이 없다");
    // 후보 하나하나가 **제 줄**로 그려진다(한 덩어리 글자 바가 아니다).
    let painted = painted_after_setup(vec![layout_one_pane()], &[], |v| {
        v.screens.ask(base::screens::Prompt::RenameTab, "");
        v.screens
            .set_prompt_history(vec!["build".into(), "buildall".into()]);
        v.handle_key(Key::Char('b'), Mods::NONE);
    });
    for cand in ["build", "buildall"] {
        assert!(
            painted.iter().any(|t| t.contains(cand)),
            "후보 {cand:?} 가 제 줄로 안 떴다: {painted:?}"
        );
    }
}

// ── N12 크롬 마감(pytmux-461) — 글자로 말하던 것을 그림으로 ─────────────────
//
// ⛔ 재는 것은 **그림이 실제로 늘었나**다. 색만 재면 「색은 맞는데 아무것도 안 그렸다」를
//    못 잡는다(이 절 머리말의 그 함정).

/// 탭 하나짜리 status — `claude` 집계를 실어 보낸다.
fn tabs_with_claude(state: Option<&str>, done: bool) -> ServerMessage {
    let mut tab = serde_json::json!({
        "index": 0, "name": "sh", "active": false, "claude_done": done
    });
    if let Some(s) = state {
        tab["claude"] = serde_json::json!(s);
    }
    serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [tab, {"index": 1, "name": "other", "active": true}],
    }))
    .unwrap()
}

/// 이 프레임에 그려진 **면**의 수(획·점·알약).
fn shape_count(messages: Vec<ServerMessage>) -> usize {
    painted_scene(messages, &[], |scene| {
        scene.layers().flat_map(|l| l.rects.iter()).count()
    })
}

#[test]
fn the_claude_state_becomes_an_icon_not_a_glyph() {
    // 정본은 `○`·`◐`·`⊘` 를 **글자**로 찍는다. 우리는 같은 뜻을 그림으로 그린다 —
    // 그래서 상태가 오면 면이 늘고, 셋이 **서로 다른 그림**이라야 한다.
    let none = shape_count(vec![layout_one_pane(), tabs_with_claude(None, false)]);
    let idle = shape_count(vec![layout_one_pane(), tabs_with_claude(Some("idle"), false)]);
    let limit = shape_count(vec![layout_one_pane(), tabs_with_claude(Some("limit"), false)]);
    assert!(idle > none, "상태가 왔는데 아이콘이 안 늘었다({none} → {idle})");
    assert!(
        limit > idle,
        "`limit` 이 `idle` 과 같은 그림이다({idle} vs {limit}) — 「막혔다」의 막대가 없다"
    );
    // ⛔ 글리프를 **글자로** 찍지 않는다(정본의 글자를 흉내내면 그것은 TUI 재현이다).
    let painted = painted_after(
        vec![layout_one_pane(), tabs_with_claude(Some("busy"), false)],
        &[],
    );
    for glyph in ["○", "◐", "⊘"] {
        assert!(
            !painted.iter().any(|t| t.contains(glyph)),
            "정본 글리프를 글자로 찍고 있다: {glyph}"
        );
    }
}

#[test]
fn an_unknown_claude_state_draws_nothing_instead_of_guessing() {
    // 서버가 모르는 값을 보내면 **지어내지 않는다**.
    let none = shape_count(vec![layout_one_pane(), tabs_with_claude(None, false)]);
    let junk = shape_count(vec![layout_one_pane(), tabs_with_claude(Some("???"), false)]);
    assert_eq!(none, junk, "모르는 상태에 그림을 지어냈다");
}

#[test]
fn a_finished_tab_gets_a_dot_not_only_a_colour() {
    // ⛔ 색만으로 말하는 자리는 색각이 다른 사람에게 아무 말도 안 한다 —
    //    모양이 하나 더 있어야 한다(pytmux-461).
    let plain = shape_count(vec![layout_one_pane(), tabs_with_claude(None, false)]);
    let done = shape_count(vec![layout_one_pane(), tabs_with_claude(None, true)]);
    assert!(done > plain, "작업이 끝났는데 점이 안 붙었다({plain} → {done})");
}

#[test]
fn a_percentage_badge_grows_a_bar_and_keeps_its_number() {
    // 「얼마나 찼나」는 눈이 **길이**로 먼저 읽는다. 그래도 글자는 남긴다 —
    // 막대만 두면 정확한 값을 잃고 정본과 문구가 갈린다.
    let badge = |text: &str| -> ServerMessage {
        serde_json::from_value(serde_json::json!({
            "t": "status",
            "plugin_badges": [{"name": "claude-code", "text": text, "style": {}, "theme": {}}],
        }))
        .unwrap()
    };
    let flat = shape_count(vec![layout_one_pane(), badge(" sonnet ")]);
    let meter = shape_count(vec![layout_one_pane(), badge(" 40% ")]);
    assert!(meter > flat, "퍼센트인데 막대가 안 생겼다({flat} → {meter})");
    let painted = painted_after(vec![layout_one_pane(), badge(" 40% ")], &[]);
    assert!(
        painted.iter().any(|t| t == "40%"),
        "막대를 넣으면서 숫자를 잃었다: {painted:?}"
    );
}

#[test]
fn a_number_that_is_not_a_percentage_gets_no_bar() {
    // ⛔ 「숫자가 있으면 퍼센트」로 접지 않는다 — 모델 이름·카운트다운도 숫자를 든다.
    assert_eq!(SessionView::percent_in("40%"), Some(0.4));
    assert_eq!(SessionView::percent_in("ctx 7%"), Some(0.07));
    assert_eq!(SessionView::percent_in("100%"), Some(1.0));
    assert_eq!(SessionView::percent_in("sonnet-4"), None);
    assert_eq!(SessionView::percent_in("3분"), None);
    assert_eq!(SessionView::percent_in("%"), None);
    // 상한을 넘겨 와도 막대가 칩 밖으로 안 자란다.
    assert_eq!(SessionView::percent_in("140%"), Some(1.0));
}

#[test]
fn the_pane_numbers_are_drawn_by_us_not_by_the_compositor() {
    // ⛔ 둘 다 그리면 같은 번호가 **두 벌** 뜬다. 뷰가 그린다고 알렸으므로 합성기는
    //    그 칸을 안 찍어야 하고, 대신 우리 배지가 막대를 낸다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    assert!(view.pane_number_badges().is_empty(), "안 켰는데 번호가 있다");
    assert!(view.state.toggle_pane_numbers(), "토글이 안 켰다");
    let badges = view.pane_number_badges();
    assert_eq!(badges.len(), 1, "패널 하나에 배지 하나가 아니다");
    // 합성기는 그 칸을 **안 찍는다**(캔버스에 번호 글자가 없다).
    let canvas = view.state.composite().expect("캔버스가 없다");
    let text: String = (0..canvas.size().1)
        .map(|y| {
            canvas
                .row_runs(y)
                .into_iter()
                .map(|(text, _)| text)
                .collect::<String>()
        })
        .collect();
    assert!(
        !text.contains('0'),
        "합성기가 번호를 아직 칸으로 찍는다 — 같은 번호가 두 벌 뜬다"
    );
    // 그리고 우리 배지가 실제로 막대를 낸다.
    let drawn = crate::splitter::SplitterOverlay::for_clock_test(Vec::new())
        .with_numbers(badges)
        .number_rects(vec2f(0., 0.), 9., 18.)
        .len();
    assert!(drawn > 1, "배지가 알약도 숫자도 안 그린다({drawn})");
}

// ── 네이티브 시계(pytmux-458 장치 · 459 그림) ────────────────────────────────
//
// ⛔ 여기서 재는 것은 **그림이 실제로 서나**다. 「상태를 받았다」로 초록을 만들면 그리기
// 배선이 통째로 빠져도 통과한다 — 이 저장소가 두 번 밟은 그 자리(§슬라이스 규칙 ①).

/// 서버가 네이티브 시계 상태를 실어 보내는 프레임.
fn native_clock_frame(time: &str) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay",
        "dim": [1], "runs": [], "zones": [], "keys": [],
        "native": {"clock": {"1": {"time": time}}}
    }))
    .unwrap()
}

/// 종전(격자 글자) 시계 프레임 — 네이티브를 안 광고한 클라가 받는 그것.
fn run_clock_frame() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [1],
        "runs": [{"x": 2, "y": 1, "text": "12:34:56", "style": {}}],
        "zones": [], "keys": []
    }))
    .unwrap()
}

/// 그 시각의 시계가 **실제로 그리는 막대 수**.
///
/// ⚠ 씬에서 못 잰다 — 헤드리스 글꼴은 칸 폭이 0 이라 오버레이 그리기가 통째로
/// 건너뛰어진다(`SplitterOverlay::clock_rects` 머리말이 그 사정을 적는다). 그래서 뷰가
/// 만든 판을 **그리는 함수**에 진짜 칸 크기와 함께 넣어 같은 코드를 부른다.
fn clock_strokes(time: &str) -> usize {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(native_clock_frame(time)))).unwrap();
    view.pump_headless();
    crate::splitter::SplitterOverlay::for_clock_test(view.clock_faces())
        .clock_rects(vec2f(0., 0.), 9., 18.)
        .len()
}

#[test]
fn the_native_clock_actually_draws_something() {
    // 양성 오라클 — 「시계를 안 켜면 없다」만 재면 배선이 빠져도 통과한다.
    let none = {
        let (mut view, tx, _sent) = harness();
        tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
        view.pump_headless();
        crate::splitter::SplitterOverlay::for_clock_test(view.clock_faces())
            .clock_rects(vec2f(0., 0.), 9., 18.)
            .len()
    };
    assert_eq!(none, 0, "시계를 안 켰는데 무언가 그려졌다");
    let some = clock_strokes("01:23:45");
    // 숫자 여섯 + 점 넷은 최소 이만큼을 낸다(`1` 이 획 둘로 가장 적다).
    assert!(
        some >= 6 * 2 + 4,
        "그려진 막대가 {some}뿐이다 — 숫자를 다 안 그렸다"
    );
}

#[test]
fn a_different_time_draws_a_different_number_of_strokes() {
    // ⛔ 「무언가 그렸다」로 접지 않는다 — **받은 글자를** 그렸나를 잰다.
    //    `11:11:11`(획 2×6) 과 `88:88:88`(획 7×6) 는 획 수가 정확히 갈린다.
    let few = clock_strokes("11:11:11");
    let many = clock_strokes("88:88:88");
    assert_eq!(
        many - few,
        6 * (7 - 2),
        "일곱 획 표대로 안 그렸다(1 은 획 둘 · 8 은 일곱): {few} vs {many}"
    );
}

#[test]
fn the_run_shaped_clock_still_paints_for_a_client_without_the_cap() {
    // 대조군 — 네이티브를 안 받는 프레임(종전 런)은 종전대로 **글자**로 뜬다.
    // 그래야 이 장치가 정본의 그림을 안 건드렸다고 말할 수 있다.
    let painted = painted_after(vec![layout_one_pane(), run_clock_frame()], &[]);
    assert!(
        painted.iter().any(|t| t.contains("12:34:56")),
        "격자 글자 시계가 안 떴다 — 네이티브 전환이 종전 경로를 깨뜨렸다: {painted:?}"
    );
}

#[test]
fn a_screen_on_top_hides_the_native_clock() {
    // 판이 캔버스를 덮는 자리라, 시계가 그 위로 뚫고 나오면 안 된다(한/영 배지와 같은 판단).
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(native_clock_frame("88:88:88")))).unwrap();
    view.pump_headless();
    assert!(!view.clock_faces().is_empty(), "판이 없을 때도 시계가 없다");
    view.screens.open(Screen::Keys);
    assert!(
        view.clock_faces().is_empty(),
        "판이 떠 있는데 시계가 그 위로 뚫고 나온다"
    );
}

#[test]
fn the_clock_faces_are_actually_handed_to_the_overlay() {
    // ⛔ **「호출 제거」 뮤테이션을 잡는 자리다.** 위 오라클들은 값을 만드는 함수
    //    (`clock_faces`)와 그리는 함수(`clock_rects`)를 각각 재는데, 그 둘을 **잇는 한
    //    줄**을 지우면 화면에서 시계가 사라진 채로 전부 초록이다 — 이 저장소가 두 번
    //    밟은 공허 통과가 정확히 그 모양이다.
    //
    //    ⚠ 씬에서 못 잰다: 헤드리스 글꼴은 칸 폭이 0 이라 `SplitterOverlay::paint` 가
    //    오버레이를 통째로 건너뛴다(그 함수의 `cw <= 0.5` 가드). 그래서 남는 오라클은
    //    **소스가 그 줄을 들고 있나**뿐이고, 그것을 이렇게 적어 둔다.
    const VIEW: &str = include_str!("session_view.rs");
    assert!(
        VIEW.contains(".with_clocks(self.clock_faces())"),
        "렌더가 시계 판을 오버레이에 안 넘긴다 — 그리는 함수가 멀쩡해도 화면에는 안 뜬다"
    );
    const OVERLAY: &str = include_str!("splitter.rs");
    assert!(
        OVERLAY.contains("self.paint_clock(origin, cw, ch, ctx);"),
        "오버레이가 시계를 그리는 함수를 안 부른다"
    );
    assert!(
        OVERLAY.contains("&& self.clocks.is_empty()"),
        "그릴 것이 시계뿐일 때 오버레이가 일찍 빠져나간다 — 그러면 시계만 안 뜬다"
    );
}

#[test]
fn the_clock_reads_the_time_from_the_server_not_from_this_box() {
    // ⛔ 클라가 제 시계를 읽으면 원격 세션에서 두 시각이 갈린다 — 그때 어느 쪽이
    //    맞는지 알 길이 없다. 서버가 보낸 글자가 **그대로** 판에 실려야 한다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(native_clock_frame("03:04:05")))).unwrap();
    view.pump_headless();
    let faces = view.clock_faces();
    assert_eq!(faces.len(), 1, "패널 하나에 시계 하나가 아니다");
    assert_eq!(faces[0].text, "03:04:05", "서버가 준 시각을 안 쓴다");
}

// ── 네이티브 달력(pytmux-460) ────────────────────────────────────────────────
//
// 시계와 갈리는 것 하나 — **per-client 상태가 왕복한다**. 그래서 재는 것도 하나 더 있다:
// 단추를 누르면 **서버로 나가나**(큐 오라클)와, 되받은 `offset` 을 **그대로 그리나**.

/// 달력이 실제로 그려지려면 **픽셀 자리**가 필요하다(패널 위에 얹는 위젯이라서).
/// 제품에서는 자리표가 그 값을 주지만 헤드리스에는 창이 없어 자리표가 없다 — 그래서
/// 오라클이 손으로 세운다. ⛔ 이 값을 안 세우면 위젯이 **정당하게** 안 그려지고,
/// 그때 붉는 것은 제품이 아니라 오라클이다.
fn measured(view: &mut SessionView) {
    view.note_cell_size(9., 18.);
    view.note_canvas_for_test(0., 40., 800., 600.);
}

/// 서버가 달력 상태를 실어 보내는 프레임.
fn native_calendar_frame(offset: i64, title: &str, today: i64) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay",
        "dim": [1], "runs": [], "zones": [], "keys": [],
        "native": {"calendar": {"1": {
            "offset": offset,
            "title": title,
            "heads": ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"],
            "weeks": [[0, 0, 1, 2, 3, 4, 5], [6, 7, 8, 9, 10, 11, 12]],
            "today": today,
        }}}
    }))
    .unwrap()
}

#[test]
fn the_native_calendar_paints_the_month_the_server_sent() {
    // 양성 오라클 — 「없으면 안 뜬다」만 재면 배선이 통째로 빠져도 통과한다.
    let plain = painted_after_setup(vec![layout_one_pane()], &[], measured);
    assert!(
        !plain.iter().any(|t| t == "2026-07"),
        "달력을 안 켰는데 달 제목이 떴다"
    );
    let painted = painted_after_setup(
        vec![layout_one_pane(), native_calendar_frame(-2, "2026-07", 0)],
        &[],
        measured,
    );
    assert!(
        painted.iter().any(|t| t == "2026-07"),
        "서버가 준 달이 안 떴다: {painted:?}"
    );
    for needle in ["Su", "Sa", "‹", "›"] {
        assert!(
            painted.iter().any(|t| t == needle),
            "{needle:?} 가 안 떴다 — 요일 머리와 화살표는 이 위젯의 알맹이다"
        );
    }
    // 이 달이 아닌 칸은 **빈 자리**다(정본과 같은 정보량 · 계획 §7 ②).
    assert!(
        painted.iter().any(|t| t == "12"),
        "달 격자의 날짜가 안 떴다: {painted:?}"
    );
}

#[test]
fn a_different_offset_paints_a_different_month() {
    // ⛔ 「무언가 그렸다」로 접지 않는다 — **받은 상태를** 그렸나를 잰다.
    let july = painted_after_setup(
        vec![layout_one_pane(), native_calendar_frame(-2, "2026-07", 0)],
        &[],
        measured,
    );
    let sept = painted_after_setup(
        vec![layout_one_pane(), native_calendar_frame(0, "2026-09", 3)],
        &[],
        measured,
    );
    assert!(july.iter().any(|t| t == "2026-07"), "{july:?}");
    assert!(sept.iter().any(|t| t == "2026-09"), "{sept:?}");
    assert!(
        !sept.iter().any(|t| t == "2026-07"),
        "달을 바꿨는데 옛 제목이 남았다 — 상태를 안 읽고 그리고 있다"
    );
}

#[test]
fn a_screen_on_top_hides_the_native_calendar() {
    let painted = painted_after_setup(
        vec![layout_one_pane(), native_calendar_frame(0, "2026-09", 3)],
        &[],
        |view| {
            measured(view);
            view.screens.open(Screen::Keys);
        },
    );
    assert!(
        !painted.iter().any(|t| t == "2026-09"),
        "판이 떠 있는데 달력이 그 위로 뚫고 나온다"
    );
}

#[test]
fn the_arrow_sends_the_name_the_server_gave_and_never_counts_months_itself() {
    // ⛔ **이 이슈의 관문이다.** 단추는 「이전/다음」이라는 이름만 올리고, 몇 달인지는
    //    서버가 정한다. 클라가 세면 상태가 두 벌이 되고 원격 보기·재접속에서 한쪽만
    //    되돌아간다(플러그인 설계 §4.2).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(native_calendar_frame(0, "2026-09", 3))))
        .unwrap();
    view.pump_headless();
    for _ in 0..2 {
        view.handle_action_for_test(ViewAction::OverlayAction {
            name: "calendar",
            pane: 1,
            act: "prev",
        });
    }
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    let asks: Vec<&Outgoing> = out
        .iter()
        .filter(|o| {
            matches!(
                o,
                Outgoing::Command(Command::PluginOverlayAction { name, act, .. })
                    if name == "calendar" && act == "prev"
            )
        })
        .collect();
    assert_eq!(asks.len(), 2, "`‹` 를 두 번 눌렀는데 나간 것이 {}: {out:?}", asks.len());
    // ⛔ **클라가 스스로 그린 달이 없다** — 되받기 전에는 서버가 준 그대로다.
    let painted = {
        let faces = view.calendar_month_for_test(1);
        faces.unwrap_or_default()
    };
    assert_eq!(
        painted, "2026-09",
        "단추를 눌렀다고 클라가 달을 혼자 넘겼다 — offset 은 서버 것이다"
    );

    // 서버가 되돌려준 상태로 그린다.
    tx.send(LinkEvent::Message(Box::new(native_calendar_frame(-2, "2026-07", 0))))
        .unwrap();
    view.pump_headless();
    assert_eq!(
        view.calendar_month_for_test(1).unwrap_or_default(),
        "2026-07",
        "되받은 상태를 안 그린다"
    );
}

#[test]
fn the_calendar_widgets_are_actually_handed_to_the_frame() {
    // ⛔ **「호출 제거」 뮤테이션을 잡는 자리다**(시계의 짝). 위 오라클들은 픽셀 자리를
    //    손으로 세우고 재므로, 그 자리를 못 재는 프레임에서 위젯이 정당하게 빠지는 것과
    //    **렌더가 아예 안 부르는 것**을 못 가른다.
    const VIEW: &str = include_str!("session_view.rs");
    assert!(
        VIEW.contains("for overlay in self.calendar_overlays()"),
        "렌더가 달력 위젯을 프레임에 안 넣는다 — 만드는 함수가 멀쩡해도 화면에는 안 뜬다"
    );
}

// ── `debug-stats` — GUI 가 **제 런타임**을 재는 판(pytmux-457) ─────────────────
//
// ⛔ 「판이 뜬다」로 초록을 만들지 않는다. 이 이슈의 관문은 *"판의 값이 실제로 움직인다는
// 오라클 — 값 없는 판을 초록으로 접지 않는다"* 다. 줄의 **모양**은 `base::diag` 의 시험이
// 재고, 여기서 재는 것은 ⑴ 팔레트가 그 판을 열고 ⑵ 그린 값이 **판에 실제로 뜨고**
// ⑶ 프레임을 더 그리면 **그 수가 는다** 셋이다.

/// `debug-stats` 판을 팔레트로 연 뷰.
fn debug_stats_panel() -> SessionView {
    let (mut view, _tx, _sent) = harness();
    view.pump_headless();
    let entry = base::PALETTE
        .iter()
        .find(|e| e.name == "debug-stats")
        .expect("팔레트에 `debug-stats` 가 없다 — 이 판에 들어갈 길이 없다");
    assert!(view.apply_action(entry.action), "팔레트 줄이 아무 일도 안 했다");
    assert_eq!(
        view.screens.top(),
        Some(Screen::DebugStats),
        "그 줄이 다른 판을 열었다"
    );
    view
}

#[test]
fn the_palette_is_the_way_into_the_debug_stats_panel() {
    let mut view = debug_stats_panel();
    // 같은 이름을 다시 부르면 닫힌다(판 토글 — 다른 판과 같은 손이다).
    let entry = base::PALETTE.iter().find(|e| e.name == "debug-stats").unwrap();
    view.apply_action(entry.action);
    assert_eq!(view.screens.top(), None, "같은 입구를 다시 눌렀는데 안 닫힌다");
}

#[test]
fn the_debug_stats_panel_paints_numbers_it_actually_measured() {
    // ⛔ 이 판이 **빈 껍데기가 아니다**를 재는 자리다.
    let painted = painted_after_setup(vec![layout_one_pane()], &[], |view| {
        let entry = base::PALETTE
            .iter()
            .find(|e| e.name == "debug-stats")
            .expect("팔레트에 `debug-stats` 가 없다");
        view.apply_action(entry.action);
    });
    let all = painted.join("\n");
    assert!(
        all.contains(&format!("pid {}", std::process::id())),
        "판에 이 프로세스의 pid 가 없다:\n{all}"
    );
    // 격자는 위 `layout_one_pane()` 이 정한 80×4 다 — 지어낸 값이 아니라 **받은 값**이다.
    assert!(all.contains("80×4"), "판이 지금 격자를 안 보인다:\n{all}");
    for needle in ["그린 프레임", "보낼 큐 깊이", "링크 RTT", "그린 칸"] {
        assert!(all.contains(needle), "{needle:?} 줄이 판에 없다:\n{all}");
    }
}

/// 프레임을 **두 번** 그리고 그때마다 판에 뜬 「그린 프레임」 줄을 돌려준다.
///
/// 위 `painted_scene_setup` 은 한 프레임만 세운다 — 이 이슈의 관문(*"프레임을 두 번
/// 돌리면 프레임 수가 는다"*)은 두 번이 필요해서 여기만 따로 짓는다.
fn frames_line_after_two_paints() -> (String, String) {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    warpui::App::test((), |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
        view.pump_headless();
        let entry = base::PALETTE
            .iter()
            .find(|e| e.name == "debug-stats")
            .expect("팔레트에 `debug-stats` 가 없다");
        view.apply_action(entry.action);
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let root = app.root_view_id(window_id).unwrap();
        app.update(move |ctx| {
            let mut grab = || {
                let mut updated = EntityIdSet::default();
                updated.insert(root);
                presenter.invalidate(
                    WindowInvalidation { updated, ..Default::default() },
                    ctx,
                );
                let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
                scene
                    .painted_texts()
                    .map(|t| t.text.clone())
                    .find(|t| t.starts_with("  ") && t.contains("그린 프레임"))
                    .unwrap_or_default()
            };
            let first = grab();
            let second = grab();
            (first, second)
        })
    })
}

#[test]
fn drawing_one_more_frame_shows_up_in_the_panel() {
    // 이 이슈가 못박은 관문 그 줄 — *"프레임을 두 번 돌리면 프레임 수가 는다"*.
    // ⛔ 값 없는 판을 초록으로 접지 않는다: 두 줄이 **다르다**를 재고, 첫 줄이 비어
    //    있으면(= 판이 그 줄을 아예 안 그렸으면) 그것도 실패다.
    let (first, second) = frames_line_after_two_paints();
    assert!(!first.is_empty(), "판에 「그린 프레임」 줄이 아예 없다");
    assert_ne!(
        first, second,
        "프레임을 한 번 더 그렸는데 판이 세는 수가 그대로다: {first:?}"
    );
    assert!(first.contains('1'), "첫 프레임의 수가 1 이 아니다: {first:?}");
    assert!(second.contains('2'), "둘째 프레임의 수가 2 가 아니다: {second:?}");
}

#[test]
fn what_the_upstream_will_not_tell_us_is_named_not_zeroed() {
    // 글리프 캐시·씬 원소는 상류 스냅샷의 사유 필드라 크기를 못 얻는다. 그 사실을
    // **0 으로 적으면** 다음 사람이 「캐시가 비었다」로 읽는다 — 우리가 모르는 사실이다.
    let view = debug_stats_panel();
    let stats = view.runtime_stats();
    assert_eq!(stats.glyph_cache, None, "못 얻는 값을 얻은 척한다");
    assert_eq!(stats.scene_nodes, None, "못 얻는 값을 얻은 척한다");
    // 대신 우리가 아는 그리기 일의 크기는 **잰다**.
    assert!(stats.painted_cells.is_some(), "그린 칸도 못 잰다면 판이 비었다");
}

#[test]
fn the_palette_is_the_way_into_the_cursor_panel() {
    let mut view = cursor_panel();
    // 같은 이름을 다시 부르면 닫힌다(판 토글 — 다른 판과 같은 손이다).
    let entry = base::PALETTE.iter().find(|e| e.name == "cursor").unwrap();
    view.apply_action(entry.action);
    assert_eq!(view.screens.top(), None, "같은 입구를 다시 눌렀는데 안 닫힌다");
}

#[test]
fn the_configured_thickness_reaches_the_thing_that_draws_it() {
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(3, 2)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    // 기본은 종전 상수(`splitter.rs` 의 옛 `CURSOR_PX`)와 같은 2px 다.
    assert_eq!(view.cursor_cell().expect("커서가 없다").thickness, 2.);
    view.config.cursor_thickness = 4.5;
    assert_eq!(
        view.cursor_cell().expect("두께를 바꿨다고 커서가 사라지면 안 된다").thickness,
        4.5,
        "설정한 두께가 그리는 쪽까지 안 간다"
    );
}

#[test]
fn the_cursor_panel_moves_the_setting_its_row_points_at() {
    let mut view = cursor_panel();
    // ⚠ 판 안 줄 번호는 0~4 이고 값 표(`SETTINGS`)의 색인은 그것과 **다르다**. 옮기는
    //   자리가 어긋나면 커서 두께를 누르는데 엉뚱한 설정이 바뀐다 — 그 자리를 잰다.
    let before = view.config.cursor_thickness;
    view.screens.select_row(cursor_row_of("cursor-thickness"));
    view.handle_key(Key::Right, Mods::NONE);
    assert_eq!(view.screens.top(), Some(Screen::Cursor), "값을 바꿨는데 판이 닫혔다");
    assert_eq!(
        view.config.cursor_thickness,
        before + base::config::CURSOR_THICKNESS_STEP,
        "→ 가 두께를 한 걸음 안 옮겼다"
    );
    // 그리고 **그 줄만** 움직인다 — 옆 줄이 함께 바뀌면 옮기는 자리가 밀린 것이다.
    let fresh = base::config::Config::default();
    assert_eq!(view.config.cursor_style, fresh.cursor_style);
    assert_eq!(view.config.cursor_blink_ms, fresh.cursor_blink_ms);

    // 모양 줄도 같은 길로 움직인다(어휘의 주인은 core 다).
    view.screens.select_row(cursor_row_of("cursor-style"));
    view.handle_key(Key::Right, Mods::NONE);
    assert_eq!(view.config.cursor_style, base::config::CURSOR_STYLES[1]);
}

#[test]
fn on_block_the_thickness_row_changes_nothing() {
    // ☠ 판은 그 줄을 **흐리게** 그린다(지우지 않는다 — 줄 수가 흔들리면 판 높이가
    //    흔들린다). 그런데 흐리게 그려 놓고 값은 바뀌면 그것이 가장 나쁜 조합이다:
    //    화면은 아무 반응이 없는데 설정 파일만 조용히 움직인다.
    let mut view = cursor_panel();
    view.config.cursor_style = "block".into();
    let before = view.config.cursor_thickness;
    view.screens.select_row(cursor_row_of("cursor-thickness"));
    for key in [Key::Right, Key::Left, Key::Enter] {
        view.handle_key(key, Mods::NONE);
        assert_eq!(view.config.cursor_thickness, before, "{key:?} 가 안 먹는 줄을 움직였다");
        assert_eq!(view.screens.top(), Some(Screen::Cursor), "{key:?} 가 판을 닫았다");
    }
    // ⚠ 「안 먹는다」가 「이 판이 멈춘다」는 아니다 — 다른 줄은 그대로 산다.
    view.screens.select_row(cursor_row_of("cursor-blink"));
    view.handle_key(Key::Enter, Mods::NONE);
    assert!(view.config.cursor_blink, "옆 줄까지 함께 죽었다");
}

#[test]
fn a_stray_key_leaves_the_cursor_panel_open() {
    // ⛔ pytmux-374·273 이 걸린 그 함정 — **모르는 키가 판을 닫으면 안 된다**. core 쪽은
    //    `proto/tests/interaction.rs` 가 재고, 여기서는 **뷰 배선까지 포함한** 키 경로를 잰다.
    let mut view = cursor_panel();
    for key in [Key::Function(5), Key::Insert, Key::Tab, Key::BackTab] {
        view.handle_key(key, Mods::NONE);
        assert_eq!(view.screens.top(), Some(Screen::Cursor), "{key:?} 가 판을 닫았다");
    }
    // `Tab` 은 이 판에 분류가 없어 **삼킨다** — 설정 화면처럼 선택이 튀면 안 된다.
    assert_eq!(view.screens.selected(), 0, "Tab 이 선택을 옮겼다");
    view.handle_key(Key::Escape, Mods::NONE);
    assert_eq!(view.screens.top(), None, "Esc 가 안 닫았다");
}

#[test]
fn the_cursor_panel_cursor_stays_inside_its_five_rows() {
    // `End` 는 core 가 `usize::MAX` 를 두고 가고 접는 것은 뷰다(`settle_settings_cursor`).
    // 안 접으면 아래에서 넘긴 만큼 `↑` 가 헛돈다(그림은 그대로인데 키가 안 먹는 것으로 보인다).
    let mut view = cursor_panel();
    let last = base::config::CURSOR_SETTINGS.len() - 1;
    view.handle_key(Key::End, Mods::NONE);
    assert_eq!(view.screens.selected(), last, "End 가 끝으로 안 갔다");
    view.handle_key(Key::PageDown, Mods::NONE);
    assert_eq!(view.screens.selected(), last, "끝에서 더 내려갔다");
    view.handle_key(Key::Up, Mods::NONE);
    assert_eq!(view.screens.selected(), last - 1, "끝에서 ↑ 한 번이 안 먹었다");
}

// ── 반전할 칸 떼어 내기(`isolate_cell`) ──────────────────────────────────────
//
// 순수 함수라 글꼴 없이 잰다. 여기가 틀리면 증상이 둘로 갈리는데 둘 다 조용하다 —
// 런 전체가 뒤집히거나(커서가 줄 하나를 먹는다), 한글이 반으로 쪼개져 안 그려진다.

#[test]
fn the_cursor_cell_becomes_its_own_piece() {
    let segs = SessionView::grid_segments("HELLO");
    assert_eq!(segs, vec![("HELLO".to_string(), 5)]);
    assert_eq!(
        SessionView::isolate_cell(segs, 0, 2),
        vec![("HE".to_string(), 2), ("L".to_string(), 1), ("LO".to_string(), 2)]
    );
}

#[test]
fn a_cell_at_either_end_does_not_make_an_empty_piece() {
    // 빈 조각은 그릴 것이 없는 엘리먼트라 자리만 먹는다(그리고 자리표 셈을 흔든다).
    let segs = SessionView::grid_segments("HELLO");
    assert_eq!(
        SessionView::isolate_cell(segs.clone(), 0, 0),
        vec![("H".to_string(), 1), ("ELLO".to_string(), 4)]
    );
    assert_eq!(
        SessionView::isolate_cell(segs, 0, 4),
        vec![("HELL".to_string(), 4), ("O".to_string(), 1)]
    );
}

#[test]
fn a_two_cell_letter_is_never_cut_in_half() {
    // ★ 한글은 두 칸을 먹는다. 칸 단위로 자르면 반쪽 글자가 나오는데 그건 그릴 수가
    //   없다. 커서는 **글자**에 놓인다 — 두 번째 칸을 가리켜도 그 글자 하나가 뜯긴다.
    //   (`grid_segments` 가 이미 글자마다 쪼개므로 여기서는 그대로 지나야 한다.)
    let segs = SessionView::grid_segments("가나");
    assert_eq!(segs, vec![("가".to_string(), 2), ("나".to_string(), 2)]);
    for at in [2, 3] {
        assert_eq!(SessionView::isolate_cell(segs.clone(), 0, at), segs);
    }
}

#[test]
fn a_run_without_the_cursor_is_left_alone() {
    // 부르는 쪽이 "이 런에 커서가 있나"를 다시 세지 않게 하려는 계약이다.
    let segs = SessionView::grid_segments("HELLO");
    assert_eq!(SessionView::isolate_cell(segs.clone(), 10, 3), segs);
    assert_eq!(SessionView::isolate_cell(segs.clone(), 0, 99), segs);
}

#[test]
fn only_the_first_matching_cell_is_isolated() {
    // 커서는 하나다 — 두 번 뜯으면 그 줄의 조각 수가 늘어 자리표 셈이 흔들린다.
    let segs = vec![("AB".to_string(), 2), ("CD".to_string(), 2)];
    assert_eq!(
        SessionView::isolate_cell(segs, 0, 1),
        vec![("A".to_string(), 1), ("B".to_string(), 1), ("CD".to_string(), 2)]
    );
}

// ── 글자 크기 배율(§10-21ⓐ) ──────────────────────────────────────────────────
//
// 제보가 "패널 캔버스만이 아니라 **앱 전체**"라고 못박았다. 그래서 재는 것 둘이다:
// ⑴ 그 조합이 실제로 잡히는가(넓지도 좁지도 않게) ⑵ **글자 크기가 실제로 바뀌는가**.
//
// ⑵ 를 부정 단언으로 두면(예: "패널로 안 샌다") 배율을 아무 데도 안 곱해도 통과한다 —
// `client/CLAUDE.md` 가 두 번 밟았다고 적은 그 자리다. 그래서 `scaled()` 가 만드는
// **값**을 직접 본다.

#[test]
fn ctrl_plus_and_minus_change_the_text_size() {
    assert_eq!(
        SessionView::font_scale_chord(&ks("=", true, false, false)),
        Some(Action::FontScale { up: true })
    );
    // Shift 를 눌러 `+` 가 오는 사람도 같은 뜻이다 — 하나만 받으면 절반에게 안 먹는다.
    assert_eq!(
        SessionView::font_scale_chord(&ks("+", true, false, true)),
        Some(Action::FontScale { up: true })
    );
    assert_eq!(
        SessionView::font_scale_chord(&ks("-", true, false, false)),
        Some(Action::FontScale { up: false })
    );
    assert_eq!(
        SessionView::font_scale_chord(&ks("0", true, false, false)),
        Some(Action::FontScaleReset)
    );
}

#[test]
fn plain_minus_belongs_to_the_program_in_the_pane() {
    // ★ 넓게 잡으면 패널에서 `-` 를 못 친다. 좁게 잡으면 확대가 안 된다 — 둘 다 조용하다.
    assert_eq!(SessionView::font_scale_chord(&ks("-", false, false, false)), None);
    assert_eq!(SessionView::font_scale_chord(&ks("=", false, false, false)), None);
    // Alt 가 섞이면 다른 조합이다.
    assert_eq!(SessionView::font_scale_chord(&ks("-", true, true, false)), None);
    // 글자 키는 배율과 상관없다(`Ctrl+a` 를 삼키면 셸의 줄 처음 가기가 사라진다).
    assert_eq!(SessionView::font_scale_chord(&ks("a", true, false, false)), None);
}

/// 배율을 **쓰기 경로를 안 타고** 옮긴다.
///
/// ⚠ `apply_action(FontScale)` 은 `set_number` 를 거쳐 **진짜 설정 파일**에 쓴다
/// (`Config::path_for_write` — 이 상자에서 돌리는 사람의 파일이다). 이 저장소는
/// 테스트에서 `PYTMUX_CONFIG` 를 세우는 것을 금한다(프로세스 전역이라 형제 테스트와
/// 경합한다 — `config_tests.rs:865` 의 그 자리). 그래서 여기서는 **값만** 옮기고,
/// "그 값이 설정 파일 형식으로 오간다"는 `base` 의 파일 왕복 오라클이 잰다.
/// 액션이 실제로 이 자리에 닿는지는 `every_action_does_something_in_this_view` 가 센다.
fn step_scale(view: &mut SessionView, up: bool) {
    view.config.font_scale = base::config::font_scale_step(view.config.font_scale, up);
}

#[test]
fn the_whole_app_grows_not_just_the_canvas() {
    let (mut view, _tx, _sent) = harness();
    let before = view.scaled(13.);
    step_scale(&mut view, true);
    let after = view.scaled(13.);
    assert!(
        after > before,
        "글자가 안 커졌다 — 배율이 어디에도 안 곱해진 것이다: {before} → {after}"
    );
    // 크롬 글자도 **같은 배율**을 탄다(제보의 "앱 전체"가 그 뜻이다). 두 자리가 서로
    // 다른 배율을 타면 탭바만 그대로 남는다.
    assert!((view.scaled(11.) / 11. - view.scaled(13.) / 13.).abs() < 1e-6);
}

#[test]
fn the_size_comes_back_to_one() {
    let (mut view, _tx, _sent) = harness();
    let base = view.scaled(13.);
    for _ in 0..3 {
        step_scale(&mut view, true);
    }
    assert!(view.scaled(13.) > base);
    view.config.font_scale = 1.0;
    assert!(
        (view.scaled(13.) - base).abs() < 1e-6,
        "되돌리기가 기본으로 안 온다 — 작게 줄여 놓으면 설정 화면조차 못 읽는다"
    );
}

// ── Claude 구역(P5) ──────────────────────────────────────────────────────────

fn tool(state: ToolState) -> ClaudeItem {
    ClaudeItem {
        kind: claude::ItemKind::Tool { name: "Bash".into(), state },
        title: "ls -la".into(),
        detail: None,
    }
}

fn said(kind: claude::ItemKind) -> ClaudeItem {
    ClaudeItem { kind, title: "안녕".into(), detail: None }
}

#[test]
fn a_denied_tool_is_not_painted_like_a_failed_one() {
    // ★ 사용자가 할 일이 **정반대**다: 막힌 것은 허용하거나 그대로 두고, 깨진 것은
    // 고친다. 빨강으로 뭉치면 "고쳐야 할 것"과 "안 시킨 것"이 한 덩어리가 된다.
    assert_ne!(
        SessionView::claude_color(&tool(ToolState::Denied)),
        SessionView::claude_color(&tool(ToolState::Failed))
    );
}

#[test]
fn a_running_tool_is_not_painted_like_a_finished_one() {
    // 결과가 안 온 툴 호출은 진행 중이지 성공이 아니다(블록의 `??` 와 같은 규칙).
    let running = SessionView::claude_color(&tool(ToolState::Running));
    assert_ne!(running, SessionView::claude_color(&tool(ToolState::Ok)));
    assert_ne!(running, SessionView::claude_color(&tool(ToolState::Failed)));
}

#[test]
fn what_the_user_typed_reads_differently_from_what_claude_said() {
    // 둘이 같은 색이면 목록에서 대화의 방향이 사라진다.
    assert_ne!(
        SessionView::claude_color(&said(claude::ItemKind::Prompt)),
        SessionView::claude_color(&said(claude::ItemKind::Answer))
    );
}

#[test]
fn every_claude_color_is_opaque() {
    // 알파 0 이면 그 부류의 줄만 통째로 안 보인다 — "대화가 안 온다"와 구분되지 않는다.
    for state in [ToolState::Ok, ToolState::Failed, ToolState::Running, ToolState::Denied] {
        assert_eq!(SessionView::claude_color(&tool(state)).a, 0xff, "{state:?}");
    }
    for kind in [
        claude::ItemKind::Prompt,
        claude::ItemKind::Answer,
    ] {
        assert_eq!(SessionView::claude_color(&said(kind)).a, 0xff);
    }
}

#[test]
fn a_plan_is_labelled_in_a_word_the_user_knows() {
    // `ExitPlanMode` 는 내부 이름이다. 정본이 정하지만 GUI 가 그걸 실제로 쓰는지도
    // 물어 둔다 — 뷰가 kind 를 다시 match 하기 시작하면 그때부터 갈린다.
    let plan = ClaudeItem {
        kind: claude::ItemKind::Plan { state: ToolState::Running },
        title: "3단계".into(),
        detail: None,
    };
    assert_eq!(plan.name(), Some("플랜"));
    assert_eq!(plan.badge(), ToolState::Running.badge());
}

#[test]
fn every_tone_is_opaque() {
    // 알파 0 이면 그 부류의 줄만 통째로 안 보인다 — "블록이 안 온다"와 구분되지 않는다.
    for tone in ALL_TONES {
        assert_eq!(SessionView::block_color(tone).a, 0xff, "{tone:?} 가 투명하다");
    }
}

/// 팔레트 전수. 와일드카드 없는 `named` 의 match 가 **컴파일로** 누락을 막지만, 위
/// 테스트들이 실제로 전부를 훑으려면 목록이 필요하다.
const ALL_NAMED: [NamedColor; 16] = [
    NamedColor::Black,
    NamedColor::Red,
    NamedColor::Green,
    NamedColor::Yellow,
    NamedColor::Blue,
    NamedColor::Magenta,
    NamedColor::Cyan,
    NamedColor::White,
    NamedColor::BrightBlack,
    NamedColor::BrightRed,
    NamedColor::BrightGreen,
    NamedColor::BrightYellow,
    NamedColor::BrightBlue,
    NamedColor::BrightMagenta,
    NamedColor::BrightCyan,
    NamedColor::BrightWhite,
];

// ── 좌표 보정(P7 마우스 · §4.2 스플리터) ─────────────────────────────────────
//
// 이 자리가 **GUI 에만 있는 문제**다: TUI 는 터미널 이벤트가 이미 셀 좌표지만 GUI 는
// 픽셀이다. 그리고 보정을 계산하면 렌더와 어긋나므로, 렌더가 남긴 사각형 하나로 푼다.
// 여기서 잡는 것은 그 산수다 — 창 없이 물을 수 있는 유일한 부분이다.

use warpui::geometry::rect::RectF;
use warpui::geometry::vector::vec2f;

/// 원점 (100, 50) · 칸 8×16 인 자리표.
fn probe() -> RectF {
    RectF::new(vec2f(100., 50.), vec2f(8., 16.))
}

#[test]
fn the_probe_origin_is_cell_zero_zero() {
    assert_eq!(SessionView::cell_at(probe(), 100., 50.), Some((0, 0)));
    // 그 칸 안 아무 데나 눌러도 같은 칸이다 — 경계에서만 넘어간다.
    assert_eq!(SessionView::cell_at(probe(), 107.9, 65.9), Some((0, 0)));
}

#[test]
fn one_cell_right_and_down_lands_on_one_one() {
    assert_eq!(SessionView::cell_at(probe(), 108., 66.), Some((1, 1)));
    assert_eq!(SessionView::cell_at(probe(), 100. + 8. * 40., 50. + 16. * 7.), Some((40, 7)));
}

#[test]
fn a_click_above_or_left_of_the_canvas_is_not_a_cell() {
    // ★ 캔버스 위는 탭바다. 음수를 u16 으로 접으면 **엉뚱한 칸**(65535 근처)이 되고,
    // 그러면 탭바를 누를 때마다 화면 끝 패널이 반응한다.
    assert_eq!(SessionView::cell_at(probe(), 99., 60.), None);
    assert_eq!(SessionView::cell_at(probe(), 110., 49.), None);
}

// ── 휠(슬라이스 8) ───────────────────────────────────────────────────────────
//
// 좌표 보정이 생기기 전까지 GUI 의 휠은 대상을 **서버 판단**(활성 패널)에 맡기고
// 있었다. 아래가 그 자리를 지킨다 — 커서 아래 패널이 활성 패널과 **다를 때** 갈린다.

/// 좌우로 나뉜 두 패널(각 40칸)을 가진 상태. `active` 는 왼쪽(1)이다.
fn split_state() -> SessionState {
    let mut state = SessionState::new();
    let msg = serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [
            {"id": 1, "x": 0, "y": 0, "w": 40, "h": 24, "active": true},
            {"id": 2, "x": 40, "y": 0, "w": 40, "h": 24},
        ]
    });
    state.apply(serde_json::from_value(msg).unwrap());
    state
}

#[test]
fn the_wheel_rolls_the_pane_under_the_cursor_not_the_active_one() {
    // ★ 이 테스트가 지키는 것. 활성 패널만 굴리면, 옆 패널을 보며 휠을 돌리는 사람은
    // **자기 눈앞이 아닌 곳**이 흘러가는 것을 본다. 라이브로도 이 장면을 찍었다
    // (리포트 슬라이스 8: 활성은 왼쪽인데 오른쪽에서 굴려 오른쪽만 움직였다).
    let state = split_state();
    assert_eq!(state.active_pane(), Some(1), "전제가 깨졌다");
    assert_eq!(SessionView::wheel_scroll(&state, true, Some((60, 5))).pane, Some(2));
    assert_eq!(SessionView::wheel_scroll(&state, true, Some((10, 5))).pane, Some(1));
}

#[test]
fn the_wheel_moves_the_same_three_lines_as_the_other_clients() {
    // 세 클라의 감각이 갈리면 같은 손짓이 화면마다 다르게 움직인다. 부호도 함께 본다 —
    // 뒤집히면 휠이 **반대로** 굴러간다(과거 방향이 +).
    let state = split_state();
    assert_eq!(SessionView::wheel_scroll(&state, true, None).delta, Some(3));
    assert_eq!(SessionView::wheel_scroll(&state, false, None).delta, Some(-3));
}

#[test]
fn a_wheel_outside_the_canvas_lets_the_server_decide() {
    // 탭바·아래 요약 구역에서 굴린 휠이다. 여기서 억지로 패널을 고르면 화면 끝 패널이
    // 반응한다 — 모르면 모른다고 하고 서버 판단(활성 패널)에 맡긴다.
    let state = split_state();
    assert_eq!(SessionView::wheel_scroll(&state, true, None).pane, None);
    assert_eq!(SessionView::wheel_scroll(&state, true, Some((10, 99))).pane, None);
    assert_eq!(SessionView::wheel_scroll(&state, true, Some((999, 5))).pane, None);
}

#[test]
fn a_wheel_before_the_first_frame_is_not_aimed_at_a_pane() {
    // 배치가 아직 없으면 좌표가 어디를 가리키는지 알 수 없다.
    let empty = SessionState::new();
    assert_eq!(SessionView::wheel_scroll(&empty, true, Some((10, 5))).pane, None);
}

#[test]
fn a_degenerate_probe_is_refused_instead_of_dividing_by_zero() {
    // 첫 프레임이나 글꼴 사고로 사각형이 0 이면, 짐작해서 처리하는 것보다 아무 일도
    // 안 하는 편이 낫다 — 엉뚱한 패널로 포커스가 가면 사용자는 왜 그런지 모른다.
    assert_eq!(SessionView::cell_at(RectF::new(vec2f(0., 0.), vec2f(0., 16.)), 5., 5.), None);
    assert_eq!(SessionView::cell_at(RectF::new(vec2f(0., 0.), vec2f(8., 0.)), 5., 5.), None);
    let nan = RectF::new(vec2f(0., 0.), vec2f(f32::NAN, 16.));
    assert_eq!(SessionView::cell_at(nan, 5., 5.), None);
}

// ── 큐 오라클 하네스 — **뷰를 통째로 세워 키를 먹인다** ────────────────────────
//
// # 왜 이제야 있나
//
// `SessionView::new` 가 `ViewContext` 를 요구하는 바람에 테스트가 이 뷰를 만들 수 없었다.
// 그래서 GUI 에는 TUI 의 `outgoing_after_*` 에 해당하는 것이 없었고, **G8p 에서 `pump()`
// 배선이 통째로 빠진 것을 워크스페이스 1287개 테스트 중 어느 것도 못 잡았다** — 잡은 것은
// 라이브 스크린샷이었다. 이 절이 그 구멍을 막는다.
//
// # 무엇을 안 지나나 (정직하게)
//
// 창이 진짜로 필요한 둘만 뺀다 — **클립보드 쓰기**와 **창 크기 보고**(`report_size`).
// 나머지(키 해석 · 모드 · 화면 스택 · 크롬 · 액션 → 명령 · 퍼올리기 · 큐 비우기)는 실제와
// **같은 순서로** 지난다. 링크도 흉내가 아니라 진짜다(`ServerLink::detached`).

use proto::link::{LinkEvent, Sent};
use proto::message::ServerMessage;

/// 소켓 없는 뷰 · 받을 것을 밀어 넣는 쪽 · 보낸 것이 쌓이는 자리.
fn harness() -> (SessionView, std::sync::mpsc::Sender<LinkEvent>, Sent) {
    // ★ 설정 쓰기를 **사물함으로 돌린다**(맨 처음 한 번만 먹는다). 액션 축 오라클이
    //   액션 전수를 먹이는데 그중 몇은 설정 파일을 고친다 — 안 돌리면 `cargo test` 가
    //   돌린 사람의 진짜 config 를 고친다(글자 배율은 다음 기동에 눈에 보인다).
    // ⛔ **이 자리도 런마다 제 몫이어야 한다**(pytmux-424) — 이름이 고정이면 같은
    //    기계에서 도는 두 `cargo test` 가 **같은 설정 파일 한 장**에 함께 쓴다.
    base::config::redirect_writes(
        std::env::temp_dir().join(format!("pytmux-gui-test-config-{}", std::process::id())),
    );
    let (link, tx, sent) = ServerLink::detached("/tmp/test.sock");
    // 글꼴은 값 하나다 — 그리지 않는 테스트에서는 어느 id 든 상관없다.
    (
        SessionView::with_font(link, warpui::fonts::FamilyId(0)),
        tx,
        sent,
    )
}

/// 서버 메시지를 먹인 뒤 키를 먹이고, **서버로 실제 나간 것**을 돌려준다.
///
/// 키마다 `pump_headless` 를 한 번씩 돌리는 이유: 실제 GUI 도 프레임마다 한 번 돌고,
/// 스레드에서 오는 것(셸 결과)은 그 회전이 있어야 줍힌다.
fn sent_after(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<Outgoing> {
    let (mut view, tx, sent) = harness();
    for msg in messages {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    for (key, mods) in keys {
        view.handle_key(*key, *mods);
        view.pump_headless();
    }
    let out = sent.lock().unwrap().clone();
    out
}

fn layout_one_pane() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 4, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 4, "title": "sh", "active": true}]
    }))
    .unwrap()
}

fn three_tabs() -> Vec<ServerMessage> {
    vec![
        layout_one_pane(),
        serde_json::from_value(serde_json::json!({"t": "status", "windows": [
            {"index": 0, "name": "하나", "active": true},
            {"index": 1, "name": "둘"},
            {"index": 2, "name": "셋"},
        ]}))
        .unwrap(),
    ]
}

#[test]
fn the_harness_itself_carries_a_key_to_the_server() {
    // ★ 이 오라클이 먼저다. 하네스가 아무것도 안 나르면 **아래 전부가 공허하게 통과한다** —
    // 이 저장소가 정확히 그 방식으로 두 번 속았다.
    let out = sent_after(vec![layout_one_pane()], &[(Key::Char('a'), Mods::NONE)]);
    assert_eq!(
        out,
        vec![Outgoing::Input(b"a".to_vec())],
        "하네스가 키를 서버까지 안 날랐다"
    );
}

#[test]
fn the_prefix_table_reaches_the_server_from_this_view() {
    // prefix c → 새 탭. 표는 core 가 갖지만 **부르는 배선은 뷰마다** 있다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[(Key::Char('b'), Mods::CTRL), (Key::Char('c'), Mods::NONE)],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NewWindow { .. }))),
        "{out:?}"
    );
}

#[test]
fn esc_c_opens_a_tab_that_runs_claude_here() {
    // pytmux-137 — `esc c` 는 **지금 디렉토리에서 Claude Code 가 도는 새 탭**이다.
    // 재는 것 셋: ⑴ 명령이 실제로 나간다 ⑵ 자리가 `current` 다 ⑶ 실행할 것이 실린다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char('c'), Mods::NONE)],
    );
    let sent = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::NewWindow { path, cmd }) => Some((path.clone(), cmd.clone())),
        _ => None,
    });
    assert_eq!(
        sent,
        Some(("current".to_owned(), Some("claude".to_owned()))),
        "{out:?}"
    );
}

#[test]
fn esc_n_still_opens_a_plain_shell_tab() {
    // ⛔ 옆자리를 안 건드렸는지 같이 잰다 — `esc n` 은 명령 없이 그대로다(pytmux-137).
    let out = sent_after(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char('n'), Mods::NONE)],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NewWindow { cmd: None, .. }))),
        "{out:?}"
    );
}

#[test]
fn a_destructive_key_asks_before_it_sends() {
    // prefix x 는 **확인 화면**을 세운다. 바로 나가면 파이썬보다 위험하다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[(Key::Char('b'), Mods::CTRL), (Key::Char('x'), Mods::NONE)],
    );
    assert!(out.is_empty(), "묻지 않고 보냈다: {out:?}");
}

// ── 크롬 포커스(G8r) — TUI 와 **같은 것**을 GUI 에서도 본다 ────────────────────

#[test]
fn the_top_edge_takes_the_focus_to_the_tab_bar_and_enter_switches_tab() {
    let out = sent_after(
        three_tabs(),
        &[
            (Key::Escape, Mods::NONE),
            (Key::Up, Mods::NONE),
            (Key::Right, Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::SelectWindow { index: 1, .. }))),
        "{out:?}"
    );
}

#[test]
fn shift_arrows_on_the_tab_bar_move_the_selected_tab() {
    // 활성은 0번인데 **1번을 골라** 옮긴다 — 활성 탭을 옮기는 명령을 쓰면 여기서 죽는다.
    let out = sent_after(
        three_tabs(),
        &[
            (Key::Escape, Mods::NONE),
            (Key::Up, Mods::NONE),
            (Key::Right, Mods::NONE),
            (Key::ShiftRight, Mods::NONE),
        ],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::MoveTab { index: 1, to: 2 }))),
        "{out:?}"
    );
}

#[test]
fn a_key_that_leaves_esc_mode_also_lets_go_of_the_chrome_focus() {
    let out = sent_after(
        three_tabs(),
        &[
            (Key::Escape, Mods::NONE),
            (Key::Up, Mods::NONE),
            (Key::Char('2'), Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Right, Mods::NONE),
        ],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::SelectPaneDir { .. }))),
        "포커스가 탭바에 남아 방향키를 먹었다: {out:?}"
    );
}

#[test]
fn the_bottom_edge_takes_the_focus_to_the_badges_and_enter_runs_one() {
    // ⚠ 종전 이 테스트는 첫 배지가 `서버` 라는 것에 기대어 `RequestVersion` 을 봤다.
    //   §10-21ⓑ 로 `서버`·`시계`·`달력` 이 목록에서 빠지면서 그 전제가 사라졌다 —
    //   재는 것("아래로 내려가면 배지에 포커스가 가고 Enter 가 그것을 실행한다")은
    //   그대로 두고 **남아 있는 배지**로 재도록 고친다. 알림은 있을 때만 실리므로
    //   하나 만들어 두고 본다.
    //
    // ★ **2026-08-23 에 차례가 바뀌었다**(pytmux-367): 알림 배지가 정본과 같이 **우측
    //   무리의 머리**로 옮겨 가면서 `badges()` 의 차례도 눈에 보이는 왼→오를 따르게 됐다
    //   — 이제 `⇕`(왼쪽)가 먼저이고 알림이 그 다음이다. 그래서 여기서도 한 칸 옮겨
    //   누른다. 재는 것은 그대로다(아래로 내려가면 배지에 포커스가 가고 Enter 가 그것을
    //   실행한다) — 바뀐 것은 **어느 배지가 첫 칸인가**뿐이다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.state.note_notice(String::from("무언가 알림"));
    view.handle_key(Key::Escape, Mods::NONE);
    view.handle_key(Key::Down, Mods::NONE);
    // 첫 칸은 `⇕`(터치 스크롤 · 기본 켜짐) — 한 칸 오른쪽이 알림이다.
    view.handle_key(Key::Right, Mods::NONE);
    view.handle_key(Key::Enter, Mods::NONE);
    assert_eq!(
        view.screens.top(),
        Some(base::screens::Screen::Notices),
        "아래 모서리에서 배지 포커스를 돌려 Enter 를 눌렀는데 알림이 안 열렸다"
    );
}

// ── 시스템 표식 자리 + 자동재개 판(pytmux-183) ────────────────────────────────

fn autoresume_on() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "autoresume": true
    }))
    .unwrap()
}

#[test]
fn system_badges_sit_in_the_bottom_status_bar_not_the_tab_bar() {
    // 사용자 요청(pytmux-183): `[자동재개]` 자리를 정본에 맞춘다 — 정본은 **좌하단**
    // 클러스터이고 GUI 는 좌상단(탭바 앞)이었다.
    //
    // ⚠ 감시류가 2026-07-30 에 **같은 이유로 먼저** 내려간 선례가 있다
    // (`monitor_badges_sit_in_the_bottom_status_bar_not_the_tab_bar`) — 재는 방법도 같다:
    // 프레임은 위에서 아래로 그려지므로, 탭바에 남아 있으면 캔버스보다 먼저 그려진다.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen, autoresume_on()], &[]);
    let badge_at = painted.iter().position(|t| t.contains("[자동재개]"));
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    let badge_at = badge_at.unwrap_or_else(|| panic!("[자동재개] 표식이 프레임에 없다: {painted:?}"));
    let canvas_at = canvas_at.unwrap_or_else(|| panic!("캔버스가 없다: {painted:?}"));
    assert!(
        badge_at > canvas_at,
        "[자동재개] 가 아직 캔버스보다 먼저 그려진다(= 탭바에 남았다): {painted:?}"
    );
}

#[test]
fn clicking_the_autoresume_badge_opens_the_panel_instead_of_toggling() {
    // 제보 ②: *"이 배지를 마우스로 클릭하면 auto-resume 을 설정할 수 있는 팝업이 떠야 한다."*
    //
    // ⛔ **누르자마자 뒤집지 않는다**(정본과 같다). 자동재개는 「모르고 켜 두면 자리를
    //    비운 사이 대화가 이어지는」 상태라, 클릭 한 번에 뒤집히면 이번엔 **모르고 꺼
    //    버리는** 자리가 하나 더 생긴다. 정본은 설명을 보이고 `a` 로 뒤집게 한다.
    let (mut view, tx, sent) = harness();
    for msg in [layout_one_pane(), autoresume_on()] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::SysBadge(
        base::chrome::SysBadge::AutoResume,
    ));
    view.pump_headless();
    assert_eq!(
        view.screens.top(),
        Some(base::screens::Screen::Autoresume),
        "표식을 눌렀는데 판이 안 열렸다"
    );
    assert!(
        !sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::SetAutoresume)
        )),
        "판을 열기만 해야 하는데 그 자리에서 뒤집었다"
    );
}

#[test]
fn the_a_key_toggles_autoresume_and_closes_the_panel_like_the_canon() {
    // 정본 `open_autoresume_info` 의 `hide_key="a"` + `hide_cb` 그대로다 —
    // 뒤집는 명령을 보내고 판을 닫는다(다시 열어 새 상태를 확인하는 동선).
    let (mut view, tx, sent) = harness();
    for msg in [layout_one_pane(), autoresume_on()] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.apply_action_for_test(base::Action::ShowAutoresume));
    view.handle_key(Key::Char('a'), Mods::NONE);
    view.pump_headless();
    assert_eq!(view.screens.top(), None, "`a` 를 눌렀는데 판이 안 닫혔다");
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::SetAutoresume)
        )),
        "`a` 가 자동재개를 안 뒤집었다: {out:?}"
    );
}

#[test]
fn the_autoresume_panel_says_which_way_it_is_set() {
    // 판이 **지금 상태**를 말해야 뜻이 있다(정본 `ar.line1`). 켜짐/꺼짐이 같은 글이면
    // 사용자는 무엇을 뒤집는지 모른 채 `a` 를 누른다.
    let on = proto::info::autoresume_lines(&{
        let mut s = proto::SessionState::default();
        s.apply(serde_json::from_value(serde_json::json!({
            "t": "status", "windows": [{"index": 0, "name": "하나", "active": true}],
            "autoresume": true
        }))
        .unwrap());
        s
    });
    let off = proto::info::autoresume_lines(&proto::SessionState::default());
    assert_ne!(on, off, "켜짐과 꺼짐의 글이 같다");
    assert!(on.iter().any(|l| l.contains("[a]")), "뒤집는 손 안내가 없다: {on:?}");
}

// ── 배지 자리 — 알림은 **우측 무리의 머리**다(pytmux-367) ──────────────────────

#[test]
fn the_notices_badge_sits_at_the_head_of_the_right_hand_group_like_the_canon() {
    // 제보(첨부 4장): 같은 서버·같은 순간에 두 클라를 나란히 놓으면 알림 배지가 정본은
    // **오른쪽 아래**(`≡2` 가 `alienware 18:26 …` 바로 앞), GUI 는 **왼쪽 아래**였다.
    // 사용자 요청은 *"tui를 기준으로 맞춰야 합니다"* 였다.
    //
    // ⛔ 「오른쪽 **끝**」이 아니라 「오른쪽 **무리의 머리**」다 — host·시각·날짜가 그
    //    뒤로 온다(정본 `_render_main` §10-8 주석).
    let painted = painted_after_setup(vec![layout_one_pane()], &[], |v| {
        v.state.note_notice(String::from("무언가 알림"));
    });
    let line = painted
        .iter()
        .find(|l| l.contains("알림"))
        .unwrap_or_else(|| panic!("알림 배지가 어느 줄에도 없다: {painted:?}"));
    let notices_at = line.find("알림").expect("방금 찾은 글자가 사라졌다");
    // `⇕`(터치 스크롤)는 정본에서도 **왼쪽**이라 그대로다 — 알림만 오른쪽으로 갔다.
    if let Some(touch_at) = line.find('⇕') {
        assert!(
            touch_at < notices_at,
            "알림이 아직 `⇕` 보다 왼쪽에 있다(= 왼쪽 무리에 남았다): {line:?}"
        );
    }
}

#[test]
fn the_focus_ring_walks_the_badges_in_the_order_they_are_drawn() {
    // ⚠ 제보의 「고칠 때 같이 봐야 할 것」: 크롬 포커스 순환이 `badges()` 차례를 쓴다 —
    //    눈에 보이는 자리와 탭 순서가 어긋나면 사용자는 화살표가 어디로 갈지 못 읽는다.
    //    그래서 그 목록의 차례가 곧 **왼→오**여야 한다.
    let mut state = proto::SessionState::default();
    state.apply(serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 4, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 4, "title": "sh", "active": true}]
    }))
    .unwrap());
    state.note_notice(String::from("무언가"));
    let badges = state.badges();
    assert!(
        badges.contains(&base::Badge::Notices),
        "알림이 목록에 없다 — 단언이 공허해진다: {badges:?}"
    );
    // ⚠ **알림은 언제나 마지막이다.** 종전에는 그 앞에 `⇕`(터치 스크롤)가 있어서 둘의
    //   자리를 견줬는데, 그 배지를 걷어(pytmux-377) 지금 이 목록에 설 수 있는 것은
    //   알림 하나뿐이다. 그래도 재는 뜻은 그대로다 — 배지를 새로 들이면서 **뒤에**
    //   붙이면 그 순간 포커스 차례가 화면 차례(왼→오)와 어긋나고, 여기가 운다.
    assert_eq!(
        badges.last(),
        Some(&base::Badge::Notices),
        "알림이 마지막이 아니다(새 배지는 그 **앞**에 선다): {badges:?}"
    );
}

// ── 퍼올리기 자체 — G8p 가 통째로 빠졌던 자리 ────────────────────────────────

#[test]
fn a_selection_reply_reaches_the_server_buffer() {
    // 이 길은 **오라클이 하나도 없었다** — 클립보드 쓰기가 창을 요구해서 통째로 테스트
    // 밖에 있었다. 서버 버퍼로 가는 절반은 창과 무관하다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(serde_json::json!({"t": "selection", "text": "복사한 것"}))
            .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::SetBuffer { .. }))),
        "{out:?}"
    );
}

#[test]
fn a_delta_without_a_baseline_asks_for_a_full_frame() {
    // 서버가 기준 없는 델타를 보내면 화면이 조용히 멎는다 — 그때 다시 그려 달라고 청하는
    // 것이 뷰의 일이다(상태 누적기는 소켓을 모른다).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(
            serde_json::json!({"t": "screen-delta", "pane": 1, "rows": [], "seq": 2}),
        )
        .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::RequestRedraw))),
        "{out:?}"
    );
}

#[test]
fn the_link_ending_is_noticed() {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Ended("서버가 닫았다".into())).unwrap();
    view.pump_headless();
    assert!(view.is_ended());
}

#[test]
fn a_shell_result_reaches_the_screen_through_pump() {
    // ★ **G8p 에서 통째로 빠졌던 바로 그 배선**이다. 셸은 스레드에서 돌고 결과는
    // 퍼올리기가 줍는다 — 그 한 줄이 없으면 결과 화면이 영원히 빈다. 당시 이 크레이트에는
    // 뷰를 세울 방법이 없어 라이브 스크린샷만이 그것을 잡았다.
    let (mut view, _tx, _sent) = harness();
    view.state.apply(layout_one_pane());
    // 팔레트 → run-shell → 명령. 액션을 직접 부르지 않는 이유는 늘 같다 —
    // **그 키가 실제로 걸려 있는지**까지 봐야 한다.
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("run-shell".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend("echo pytmuxhello".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    // 스레드 결과를 기다린다. **안 기다리면 아무 일도 안 일어난 채로 통과한다**(G8p §4.1).
    for _ in 0..200 {
        view.pump_headless();
        if !view.state.shell_output().is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    let out = view.state.shell_output().join("\n");
    assert!(out.contains("pytmuxhello"), "셸 결과가 화면에 안 담겼다: {out:?}");
}

// ── lang(i18n) — GUI 에서도 같은 배선이 돈다 ──────────────────────────────────

#[test]
fn picking_a_language_runs_the_whole_wiring_in_the_gui_too() {
    // TUI 의 같은 이름 오라클과 한 쌍이다 — 클라 안에서 끝나는 액션(SetLang)은
    // `action_to_command` 가 None 이라 **뷰마다 손 배선**이 있고, 그 배선은 한쪽만
    // 빠질 수 있다(G8p 의 pump 처럼). 전역 로케일을 안 바꾸는 설계(지금 로케일과
    // 같은 ko 를 고른다)와 그 이유는 TUI 쪽 주석 참조.
    let dir = std::env::temp_dir().join(format!("pytmux-gui-lang-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let lang_file = dir.join("default.sock.lang");
    base::i18n::set_persist_path(lang_file.clone());
    let (mut view, _tx, sent) = harness();
    view.state.apply(layout_one_pane());
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("lang".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE)); // 폼이 뜬다
    keys.push((Key::Enter, Mods::NONE)); // 한국어(ko)를 고른다
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    assert!(
        view.state.notices().any(|n| n.text.contains("언어: 한국어")),
        "언어 전환 피드백 알림이 없다"
    );
    assert_eq!(
        std::fs::read_to_string(&lang_file).ok().as_deref(),
        Some("ko"),
        "선택이 .lang 에 영속되지 않았다"
    );
    // 서버로는 아무것도 안 나간다 — 로케일은 per-user 다.
    assert!(
        sent.lock().unwrap().is_empty(),
        "클라 안에서 끝나야 하는데 서버로 나갔다"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

// ── 마우스 크롬(68332 빚) — 탭·`[+]`·`[x]`·배지 클릭이 Enter 와 같은 길로 간다 ──
//
// Hoverable(레이아웃 히트테스트) → `ViewAction::ChromeClick` → `chrome_click` 중
// 앞 구간은 창 없이 못 세운다(엘리먼트 이벤트 디스패치가 레이아웃을 요구한다) —
// 그 구간은 라이브(frame-dump 로 그림 · Windows 하네스로 클릭)가 잡고, 여기서는
// **판정·배선 구간**(core `chrome::click` → `apply_action` → 큐)을 잰다.

#[test]
fn the_summary_area_no_longer_takes_a_row_from_the_canvas() {
    // ★ 종전 이 자리의 오라클은 "머리줄을 누르면 크롬 높이가 바뀐다"였다(접기 계약).
    //   §10-21ⓓ 로 그 구역이 **화면에서 빠지면서** 접기 자체가 사라졌다 — 이제 재는
    //   것은 그 반대다: 블록이 생겨도 크롬 높이가 **안 늘어난다**(그만큼 캔버스가 늘
    //   넓다). 그것이 이 제보가 하려던 일 전체다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    let empty = view.footer_lines();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(serde_json::json!({
            "t": "blocks", "pane": 1,
            "blocks": [{"command": "ls", "state": "done", "exit": 0, "start_row": 0}]
        }))
        .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    assert!(!view.state.active_blocks().is_empty(), "블록이 안 들어왔다 — 단언이 공허해진다");
    assert_eq!(
        view.footer_lines(),
        empty,
        "블록이 생겼다고 크롬이 한 줄 더 먹는다 — 요약 구역이 아직 화면에 있다(ⓓ)"
    );
}

#[test]
fn the_summary_is_reachable_as_a_panel() {
    // 화면에서 뺐으면 **여는 길**이 있어야 한다(제보: "별도 명령어나 메뉴로").
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    assert!(view.apply_action_for_test(base::Action::ShowSummary));
    assert_eq!(view.screens.top(), Some(base::screens::Screen::Summary));
}

#[test]
fn a_chrome_click_travels_the_same_road_as_enter() {
    use base::chrome::ClickTarget;
    use base::TabSpot;
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    // 둘째 탭 클릭 → 전환이 나간다.
    view.chrome_click(ClickTarget::Spot(TabSpot::Tab(1)));
    view.pump_headless();
    assert!(
        sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::SelectWindow { index: 1, .. })
        )),
        "탭 클릭이 전환으로 안 나갔다: {:?}",
        sent.lock().unwrap()
    );
    // `[+]` 클릭 → 새 탭이 나간다.
    view.chrome_click(ClickTarget::Spot(TabSpot::New));
    view.pump_headless();
    assert!(
        sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::NewWindow { .. })
        )),
        "[+] 클릭이 새 탭으로 안 나갔다"
    );
    // `[x]` 클릭 → **확인 없이는 안 나간다**(Enter 와 같은 확인 화면 길).
    view.chrome_click(ClickTarget::Spot(TabSpot::Close));
    view.pump_headless();
    assert!(
        !sent.lock().unwrap().iter().any(|o| matches!(o, Outgoing::Command(Command::KillWindow))),
        "확인 없이 탭 닫기가 나갔다"
    );
    // 서버 배지 클릭 → 정보 팝업이 버전을 청한다.
    view.handle_key(Key::Escape, Mods::NONE); // 확인 화면을 닫고
    view.pump_headless();
    view.chrome_click(ClickTarget::Badge(base::Badge::Host));
    view.pump_headless();
    assert!(
        sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::RequestVersion)
        )),
        "서버 배지 클릭이 정보 요청으로 안 나갔다"
    );
}

// ── 팝업 완성(68295 빚) — w/h 와이어 · 마우스는 팝업이 먼저(GUI 도 같은 한 벌) ──

#[test]
fn the_popup_wants_and_the_modal_wheel_work_in_the_gui_too() {
    let popup_layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "title": "sh"}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8, "title": "top"}
    }))
    .unwrap();
    // ① 물음 대답의 `-w/-h` 가 와이어에 실린다(판정은 proto 한 벌 — TUI 와 같은 문법).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("display-popup".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend("-w 40 -h 10 top".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    let frame = sent
        .lock()
        .unwrap()
        .iter()
        .find_map(|o| match o {
            Outgoing::Command(cmd) => Some(cmd.to_frame()),
            _ => None,
        })
        .expect("팝업 명령이 안 나갔다");
    assert_eq!(frame["action"], "popup_open");
    assert_eq!(frame["w"], 40);
    assert_eq!(frame["h"], 10);
    // ② 팝업이 떠 있으면 휠은 커서 위치와 무관하게 팝업을 굴린다(모달).
    tx.send(LinkEvent::Message(Box::new(popup_layout))).unwrap();
    view.pump_headless();
    view.handle_wheel(true, Some((75, 20)));
    view.pump_headless();
    let scrolled: Vec<_> = sent
        .lock()
        .unwrap()
        .iter()
        .filter_map(|o| match o {
            Outgoing::Scroll(scroll) => Some(scroll.pane),
            _ => None,
        })
        .collect();
    assert!(
        !scrolled.is_empty() && scrolled.iter().all(|pane| *pane == Some(99)),
        "팝업이 아니라 다른 것이 굴렀다"
    );
}

#[test]
fn the_wheel_reaches_the_popup_app_in_the_gui_too() {
    // popup.mouse 광고가 서면 GUI 도 팝업 안 앱에 휠 리포트를 넘긴다(TUI 와 한 벌).
    let popup_layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "title": "sh"}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8, "title": "top",
                  "mouse": 2, "mouse_sgr": true}
    }))
    .unwrap();
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(popup_layout))).unwrap();
    view.pump_headless();
    view.handle_wheel(true, Some((15, 8)));
    view.pump_headless();
    let sent = sent.lock().unwrap();
    let reports: Vec<_> = sent
        .iter()
        .filter_map(|o| match o {
            Outgoing::Mouse { pane, data } => {
                Some((*pane, String::from_utf8_lossy(data).into_owned()))
            }
            _ => None,
        })
        .collect();
    // 팝업 내용은 (11,6) 시작 → 1-based 로 열 5, 행 3. 64 = WheelUp.
    assert_eq!(reports, vec![(99, "\u{1b}[<64;5;3M".to_owned())], "휠 리포트가 안 갔다");
    assert!(
        !sent.iter().any(|o| matches!(o, Outgoing::Scroll(_))),
        "뷰 스크롤로도 샜다"
    );
}

#[test]
fn a_click_on_a_mouse_app_sends_press_and_release_from_where_it_was_pressed() {
    // ★ pytmux-19 의 **호출부** 오라클. 위 순수 판정만 재면 "판정은 맞는데 아무 데서도
    //   안 부른다"가 통과한다 — 그게 정확히 종전 상태였다(뗄 때 포커스만 옮겼다).
    let (mut view, tx, sent) = harness();
    let msg: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "title": "claude",
                   "active": true, "mouse": 1, "mouse_sgr": true}]
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    view.pump_headless();
    // 누르고 — 안 끌고 — 뗀다. 뗀 자리는 한 칸 옆이다(손이 흔들린 클릭).
    view.handle_mouse_down((7, 3), false);
    view.handle_mouse_up(Some((8, 3)));
    view.pump_headless();
    let sent = sent.lock().unwrap();
    let reports: Vec<String> = sent
        .iter()
        .filter_map(|o| match o {
            Outgoing::Mouse { data, .. } => Some(String::from_utf8_lossy(data).into_owned()),
            _ => None,
        })
        .collect();
    // SGR 1-based · 패널 원점 (0,0) → 열 8 행 4. 0=버튼1 누름, m=뗌.
    assert_eq!(
        reports,
        vec!["\u{1b}[<0;8;4M".to_owned(), "\u{1b}[<0;8;4m".to_owned()],
        "클릭이 앱에 안 갔거나 좌표가 **뗀 자리**로 갔다"
    );
}

#[test]
fn a_click_where_no_app_listens_sends_nothing_to_the_pane() {
    // 마우스를 안 켠 앱에 리포트를 보내면 그 바이트가 프롬프트에 **글자로 찍힌다**.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.handle_mouse_down((7, 3), false);
    view.handle_mouse_up(Some((7, 3)));
    view.pump_headless();
    assert!(
        !sent.lock().unwrap().iter().any(|o| matches!(o, Outgoing::Mouse { .. })),
        "안 켠 앱에 마우스 리포트를 보냈다"
    );
}

// ── run-shell 버퍼 · if-shell else (파이썬 `_run_shell`/`_if_shell` 동형) ───────

#[test]
fn shell_output_reaches_the_server_buffer_and_else_runs_in_the_gui_too() {
    // GUI 는 이 표를 **자기 사본**으로 갖는다("두 뷰가 같은 표") — 한쪽만 고치는
    // 실수를 이 오라클이 잡는다.
    let (mut view, _tx, sent) = harness();
    view.state.apply(layout_one_pane());
    // ① run-shell 출력 → set_buffer 가 큐로.
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("run-shell".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend("echo pytmuxbuf".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    for _ in 0..200 {
        view.pump_headless();
        if !view.state.shell_output().is_empty() {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    view.pump_headless();
    assert!(
        sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::SetBuffer { text }) if text.contains("pytmuxbuf")
        )),
        "출력이 서버 버퍼로 안 갔다"
    );
    // ② if-shell 실패 갈래 — `exit 1 | clear-history | redraw` 는 redraw 를 돌린다.
    view.handle_key(Key::Escape, Mods::NONE); // 셸 결과 화면 닫기
    view.pump_headless();
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("if-shell".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend("exit 1 | clear-history | redraw".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    let mut redrew = false;
    for _ in 0..200 {
        view.pump_headless();
        if sent.lock().unwrap().iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::RequestRedraw)
        )) {
            redrew = true;
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(25));
    }
    assert!(redrew, "실패 갈래의 redraw 가 안 나갔다");
    assert!(
        !sent.lock().unwrap().iter().any(|o| matches!(o, Outgoing::Command(Command::ClearHistory))),
        "성공 갈래가 잘못 돌았다"
    );
}

// ── 이벤트 훅(G8u) — 사건이 나면 명령이 서버까지 간다 ─────────────────────────

/// 팔레트에서 `set-hook` 을 골라 한 줄을 걸고, 그 뒤 서버 메시지를 먹인다.
///
/// 액션을 직접 부르지 않는 이유는 늘 같다 — **그 이름이 팔레트에 실제로 걸려 있고,
/// 물음이 실제로 뜨고, 대답이 실제로 훅 표에 닿는지**까지 한 줄로 봐야 한다.
fn sent_after_hook(hook_line: &str, messages: Vec<ServerMessage>) -> Vec<Outgoing> {
    let (mut view, tx, sent) = harness();
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("set-hook".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend(hook_line.chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    for msg in messages {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    out
}

fn status_with(count: usize, bell: bool) -> ServerMessage {
    let windows: Vec<serde_json::Value> = (0..count)
        .map(|i| {
            serde_json::json!({
                "index": i, "name": format!("탭{i}"), "active": i == 0, "bell": bell && i == 0
            })
        })
        .collect();
    serde_json::from_value(serde_json::json!({"t": "status", "windows": windows})).unwrap()
}

#[test]
fn a_new_tab_fires_the_hook_and_the_command_reaches_the_server() {
    // ★ 양성 오라클. "안 나갔다"만 재는 시험은 배선이 통째로 빠져도 통과한다(G8p).
    let out = sent_after_hook(
        "after-new-window next-tab",
        vec![layout_one_pane(), status_with(1, false), status_with(2, false)],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "탭이 늘었는데 훅이 안 돌았다: {out:?}"
    );
}

#[test]
fn the_first_tab_list_does_not_fire_the_hook() {
    // 붙자마자 탭 셋이 보이는 것은 "셋 생긴" 것이 아니다 — 여기서 발화하면 붙을 때마다
    // 훅이 돈다.
    let out = sent_after_hook(
        "after-new-window next-tab",
        vec![layout_one_pane(), status_with(3, false)],
    );
    assert!(
        !out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "첫 목록에서 발화했다: {out:?}"
    );
}

#[test]
fn a_hook_argument_skips_the_question_and_goes_straight_out() {
    // 훅이 도는 자리에는 물음에 답할 사람이 없다 — 인자가 있으면 그 대답이 이미 나온
    // 것처럼 처리한다.
    let out = sent_after_hook(
        "after-new-window rename-tab 빌드",
        vec![layout_one_pane(), status_with(1, false), status_with(2, false)],
    );
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::RenameWindow { name }) if name == "빌드"
        )),
        "인자 있는 훅이 안 돌았다: {out:?}"
    );
}

#[test]
fn a_bell_fires_its_own_hook() {
    let out = sent_after_hook(
        "alert-bell next-tab",
        vec![layout_one_pane(), status_with(1, false), status_with(1, true)],
    );
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "벨이 울렸는데 훅이 안 돌았다: {out:?}"
    );
}

#[test]
fn attaching_fires_its_own_hook() {
    // `client-attached` 는 **첫 배치**가 발화점이다(정본과 같다).
    let out = sent_after_hook("client-attached next-tab", vec![layout_one_pane()]);
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "붙었는데 훅이 안 돌았다: {out:?}"
    );
}

#[test]
fn unsetting_a_hook_stops_it() {
    let (mut view, tx, sent) = harness();
    let mut keys: Vec<(Key, Mods)> = Vec::new();
    for line in ["after-new-window next-tab", "-u after-new-window"] {
        keys.push((Key::Escape, Mods::NONE));
        keys.push((Key::Char(':'), Mods::NONE));
        keys.extend("set-hook".chars().map(|c| (Key::Char(c), Mods::NONE)));
        keys.push((Key::Enter, Mods::NONE));
        keys.extend(line.chars().map(|c| (Key::Char(c), Mods::NONE)));
        keys.push((Key::Enter, Mods::NONE));
    }
    for (key, mods) in keys {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    for msg in [layout_one_pane(), status_with(1, false), status_with(2, false)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        !out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::NextWindow))),
        "푼 훅이 계속 돈다: {out:?}"
    );
}

// ── 인자 폼(G8v) — TUI 와 **같은 것**을 GUI 에서도 본다 ──────────────────────

/// 팔레트에서 이름을 골라 폼을 연 뒤 그 안에서 키를 더 먹인다.
fn sent_from_option_form(name: &str, inside: &[(Key, Mods)]) -> Vec<Outgoing> {
    let mut keys: Vec<(Key, Mods)> = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend(name.chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend(inside.iter().copied());
    sent_after(vec![layout_one_pane()], &keys)
}

#[test]
fn the_form_reaches_the_server_from_this_view_too() {
    // ★ 이 오라클이 먼저다 — 폼이 GUI 에서 안 열리면 아래가 공허하게 통과한다.
    let out = sent_from_option_form("split-window", &[(Key::Enter, Mods::NONE)]);
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::Split {
                horizontal: true,
                ..
            })
        )),
        "폼에서 고른 것이 서버까지 안 갔다: {out:?}"
    );
}

#[test]
fn the_arrow_changes_the_value_in_the_gui_form() {
    let out = sent_from_option_form(
        "split-window",
        &[(Key::Right, Mods::NONE), (Key::Enter, Mods::NONE)],
    );
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::Split {
                horizontal: false,
                ..
            })
        )),
        "→ 가 값을 안 바꿨다: {out:?}"
    );
}

#[test]
fn on_carries_a_value_out_of_the_gui_form() {
    let frames: Vec<serde_json::Value> = sent_from_option_form(
        "synchronize-panes",
        &[(Key::Right, Mods::NONE), (Key::Enter, Mods::NONE)],
    )
    .iter()
    .filter_map(|o| match o {
        Outgoing::Command(cmd) => Some(cmd.to_frame()),
        _ => None,
    })
    .collect();
    let frame = frames
        .iter()
        .find(|f| f["action"] == "set_sync")
        .expect("set_sync 가 안 나갔다");
    assert_eq!(frame["value"], true, "{frame}");
}

#[test]
fn escape_leaves_the_gui_form_without_doing_anything() {
    let out = sent_from_option_form(
        "synchronize-panes",
        &[(Key::Right, Mods::NONE), (Key::Escape, Mods::NONE)],
    );
    assert!(
        !out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::ToggleSync { .. }))),
        "취소했는데 나갔다: {out:?}"
    );
}

// ── 프롬프트 점프(패리티 `e_jump`) — TUI 와 **같은 것**을 GUI 에서도 본다 ─────

#[test]
fn esc_ctrl_arrows_jump_and_keep_jumping_from_this_view() {
    // 배선은 뷰마다 있다. TUI 오라클이 초록이어도 GUI 가 같은 키를 안 나르면 그 사실은
    // **라이브 스크린샷 전까지 아무도 모른다**(G8p 가 정확히 그랬다).
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Up, Mods::CTRL),
            (Key::Down, Mods::CTRL),
        ],
    );
    let jumps: Vec<&str> = out
        .iter()
        .filter_map(|o| match o {
            Outgoing::Command(Command::JumpPrompt { direction }) => Some(*direction),
            _ => None,
        })
        .collect();
    assert_eq!(jumps, vec!["up", "down"], "실제: {out:?}");
    // 그 키가 패널로도 새면 자식이 커서를 같이 움직인다.
    assert!(
        !out.iter().any(|o| matches!(o, Outgoing::Input(_))),
        "점프 키가 패널로 샜다: {out:?}"
    );
}

#[test]
fn a_plain_ctrl_up_still_reaches_the_pane_from_this_view() {
    let out = sent_after(vec![layout_one_pane()], &[(Key::Up, Mods::CTRL)]);
    assert_eq!(out, vec![Outgoing::Input(b"\x1b[A".to_vec())], "{out:?}");
}

// ── 여러 줄 작성창(패리티 `e_ins`) — TUI 와 **같은 것**을 GUI 에서도 본다 ─────

fn pasted(out: &[Outgoing]) -> Vec<String> {
    out.iter()
        .filter_map(|o| match o {
            Outgoing::Command(Command::Paste { text }) => Some(text.clone()),
            _ => None,
        })
        .collect()
}

#[test]
fn esc_insert_composes_and_enter_sends_one_paste_from_this_view() {
    // GUI 배선은 뷰마다 따로다 — TUI 오라클이 초록이어도 여기가 빠지면 **라이브
    // 스크린샷 전까지 아무도 모른다**(G8p 가 그랬다).
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('a'), Mods::NONE),
            (Key::ShiftEnter, Mods::NONE),
            (Key::Char('b'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert_eq!(pasted(&out), vec!["a\nb".to_owned()], "실제: {out:?}");
    // 작성 중 글자가 패널로 새면 셸에 그대로 찍힌다.
    assert!(
        !out.iter().any(|o| matches!(o, Outgoing::Input(_))),
        "작성 중 키가 패널로 샜다: {out:?}"
    );
}

#[test]
fn esc_esc_cancels_the_compose_box_without_sending() {
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('z'), Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Escape, Mods::NONE),
        ],
    );
    assert!(pasted(&out).is_empty(), "취소했는데 나갔다: {out:?}");
}

#[test]
fn the_draft_survives_a_cancel_in_this_view_too() {
    // `Esc` 는 "안 넣겠다"이지 "버리겠다"가 아니다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('z'), Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert_eq!(pasted(&out), vec!["z".to_owned()], "초안이 사라졌다: {out:?}");
}

#[test]
fn ctrl_a_inside_the_compose_box_selects_instead_of_closing_it() {
    // ★ 순서 함정: "수정키 조합은 화면이 알 바 아니다"를 먼저 보면 편집 중 `Ctrl+A` 가
    // **화면을 닫는다**. 고른 뒤 한 글자를 치면 통째로 바뀌는 것으로 확인한다.
    let out = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('o'), Mods::NONE),
            (Key::Char('l'), Mods::NONE),
            (Key::Char('d'), Mods::NONE),
            (Key::Char('a'), Mods::CTRL),
            (Key::Char('N'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert_eq!(pasted(&out), vec!["N".to_owned()], "실제: {out:?}");
}

// ── 정보 팝업(패리티 `InfoTabsScreen`) — GUI 배선 ─────────────────────────────

#[test]
fn the_server_badge_opens_the_info_tabs_and_asks_for_the_version_from_this_view() {
    // 배지 동선은 뷰마다 배선이 따로다. 버전 탭은 서버가 채우므로 **열면서 함께 청해야**
    // 한다 — 안 청하면 그 줄이 영영 "묻는 중"이다.
    //
    // ⚠ 입구가 **상태줄 오른쪽의 `#h` 구간**으로 옮겼다(§10-21ⓑ·ⓑ2) — 왼쪽 `서버`
    //   배지는 없앴다. 뜻(`Badge::Host`)은 그대로이므로 그 클릭 대상으로 잰다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Host));
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert_eq!(view.screens.top(), Some(base::screens::Screen::InfoTabs));
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::RequestVersion))),
        "버전을 안 청했다: {out:?}"
    );
}

#[test]
fn the_info_tab_content_is_the_same_in_both_views() {
    // ★ 줄을 만드는 것은 `proto` 한 곳이다. 뷰가 각자 지으면 **같은 팝업이 GUI 와 TUI
    // 에서 다른 말을 한다** — 이 저장소가 이미 두 번 만든 갈라짐이다. 여기서는 그 함수가
    // 두 뷰에 같은 것을 준다는 사실을 못박는다(그리는 모양은 각자다).
    let state = proto::SessionState::new();
    let tabs = proto::info::tabs(&state, "/tmp/test.sock", 0.0);
    let titles: Vec<&str> = tabs.iter().map(|(t, _)| *t).collect();
    assert_eq!(titles, vec!["서버", "세션"]);
}

// ── 프롬프트 인계·비우기(패리티 G9c) — GUI 배선 ──────────────────────────────

fn claude_pane_messages() -> Vec<ServerMessage> {
    vec![
        layout_one_pane(),
        serde_json::from_value(serde_json::json!({
            "t": "status",
            "windows": [{"index": 0, "name": "claude", "active": true}],
            // 활성 패널 id 는 status 가 든다 — 없으면 긁을 대상을 못 찾는다.
            "active_pane": 1,
            "panes_claude": [{"id": 1, "claude": true}],
        }))
        .unwrap(),
        serde_json::from_value(serde_json::json!({
            "t": "screen", "pane": 1,
            "rows": [[["────────", {}]], [["❯ 한글넷", {}]], [["────────", {}]]],
            "cursor": [0, 1], "wrap": [], "top": 0
        }))
        .unwrap(),
    ]
}

#[test]
fn the_gui_clears_the_prompt_before_pasting_and_counts_characters() {
    // 배선은 뷰마다 따로다. TUI 오라클이 초록이어도 여기가 빠지면 GUI 에서만 글이 **두
    // 번** 들어간다 — 그 사실은 라이브 전까지 아무도 모른다(G8p 가 그랬다).
    let out = sent_after(
        claude_pane_messages(),
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('x'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    // 인계된 글은 "한글넷"(3자) → 백스페이스 3개가 **먼저**, 그다음 paste.
    assert_eq!(
        out,
        vec![
            Outgoing::Input(vec![0x7f, 0x7f, 0x7f]),
            Outgoing::Command(Command::Paste { text: "한글넷x".to_owned() }),
        ],
        "실제: {out:?}"
    );
}

#[test]
fn the_gui_does_not_scrape_a_shell_pane() {
    let out = sent_after(
        vec![
            layout_one_pane(),
            serde_json::from_value(serde_json::json!({
                "t": "status",
                "windows": [{"index": 0, "name": "sh", "active": true}],
                "active_pane": 1,
            }))
            .unwrap(),
            serde_json::from_value(serde_json::json!({
                "t": "screen", "pane": 1,
                "rows": [[["────────", {}]], [["❯ ~/dir", {}]], [["────────", {}]]],
                "cursor": [0, 1], "wrap": [], "top": 0
            }))
            .unwrap(),
        ],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Insert, Mods::NONE),
            (Key::Char('z'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    assert_eq!(
        out,
        vec![Outgoing::Command(Command::Paste { text: "z".to_owned() })],
        "셸 패널을 긁었다: {out:?}"
    );
}

// ── 전체 재시작의 드라이런 게이트(패리티 `restart-all`) ───────────────────────
//
// **이 하네스에서만 잴 수 있다.** GUI 큐 오라클은 서버 메시지를 `LinkEvent` 로 밀어 넣어
// 실제 `pump_messages` 를 태우므로 게이트가 진짜로 돈다. TUI 렌더 하네스는 메시지를 상태에
// 직접 넣어(게이트를 안 지나) 이것을 못 본다 — 그 사실을 알고 여기 둔다.

fn restart_check(safe: bool) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "restart_check",
        "reexec_supported": safe,
        "has_sessions": true,
        "serialize_ok": true,
        "panes": 1,
        "panes_with_fd": 1,
    }))
    .unwrap()
}

/// ★ 게이트 오라클은 **`restart-server` 로** 돈다(`restart-all` 이 아니다).
///
/// `restart-all` 은 통과하면 `restart::relaunch()` 를 부르고, 그것은 **진짜로 프로세스를
/// 띄운다** — 테스트에서 부르면 테스트 이진의 자식이 생겨 스위트가 자기를 다시 돌린다
/// (처음에 그렇게 짜서 실제로 그랬다). 게이트 자체는 두 종류가 **같은 코드**를 지나므로
/// (`begin_restart` → `gate_restart`) 서버 종류로 재도 지키는 것이 같다. `All` 쪽의
/// 다른 점(재기동 판정)은 `base::restart` 단위 테스트가 든다.
///
/// 팔레트로 명령 하나를 실행하는 키 열(`prefix :` → 이름 → Enter).
fn palette(name: &str) -> Vec<(Key, Mods)> {
    let mut keys = vec![(Key::Char('b'), Mods::CTRL), (Key::Char(':'), Mods::NONE)];
    keys.extend(name.chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys
}

/// 키를 먹인 **뒤** 서버 메시지를 밀어 넣고 퍼올린다 — 게이트는 회신이 늦게 오는 자리다.
fn sent_after_then(messages: Vec<ServerMessage>, keys: &[(Key, Mods)], late: Vec<ServerMessage>)
    -> Vec<Outgoing>
{
    let (mut view, tx, sent) = harness();
    for msg in messages {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    for (key, mods) in keys {
        view.handle_key(*key, *mods);
        view.pump_headless();
    }
    for msg in late {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    out
}

fn has_restart(out: &[Outgoing]) -> bool {
    out.iter()
        .any(|o| matches!(o, Outgoing::Command(Command::RestartServer)))
}

#[test]
fn a_green_dry_run_lets_the_restart_through() {
    // 양성 오라클 — 점검이 통과하면 **확인 없이** 진행한다(파이썬과 같다).
    let out = sent_after_then(
        vec![layout_one_pane()],
        &palette("restart-server"),
        vec![restart_check(true)],
    );
    assert!(has_restart(&out), "통과했는데 재시작이 안 나갔다: {out:?}");
}

#[test]
fn a_failing_dry_run_blocks_the_restart_until_it_is_confirmed() {
    // ★ 이 상자가 정확히 그 경우다 — Windows 서버는 re-exec 를 못 한다
    // (`reexec_supported: false`, 2026-07-30 실측). 그때 조용히 진행하면 되돌릴 수 없다.
    let out = sent_after_then(
        vec![layout_one_pane()],
        &palette("restart-server"),
        vec![restart_check(false)],
    );
    assert!(!has_restart(&out), "실패했는데 그냥 재시작했다: {out:?}");
}

#[test]
fn confirming_after_a_failed_dry_run_does_restart() {
    // 막는 것으로 끝이 아니다 — 사용자가 실패 항목을 보고 "그래도" 라고 하면 진행한다.
    let mut keys = palette("restart-server");
    keys.push((Key::Char('y'), Mods::NONE)); // 확인 화면의 예
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    for (key, mods) in &keys[..keys.len() - 1] {
        view.handle_key(*key, *mods);
        view.pump_headless();
    }
    tx.send(LinkEvent::Message(Box::new(restart_check(false)))).unwrap();
    view.pump_headless();
    // 여기서 확인 화면이 떠 있어야 `y` 가 뜻을 갖는다.
    view.handle_key(Key::Char('y'), Mods::NONE);
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(has_restart(&out), "확인했는데 재시작이 안 나갔다: {out:?}");
}

// ── 탭 드래그(G9w) — 판정·배선 구간(hover 히트는 라이브 몫 — 크롬 클릭과 같은 경계) ──

#[test]
fn a_tab_drag_dropped_on_the_canvas_joins_into_that_pane() {
    // ★ 드롭 → core `drag_drop` → 두 명령이 **그 순서로**(select_pane_id 먼저 —
    //   서버가 그 사이의 활성 패널에 붙는 것을 막는다. TUI 와 같은 표).
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    // TabPress(1) 가 하는 일 — 엘리먼트 이벤트는 레이아웃 없이 못 세워 직접 세운다.
    view.tab_drag = Some(1);
    assert!(view.handle_mouse_up(Some((2, 2))), "드롭이 처리 안 됐다");
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    let wanted: Vec<&Outgoing> = out
        .iter()
        .filter(|o| {
            matches!(
                o,
                Outgoing::Command(Command::SelectPaneId { .. } | Command::JoinPane { .. })
            )
        })
        .collect();
    assert_eq!(
        wanted,
        vec![
            &Outgoing::Command(Command::SelectPaneId { id: 1 }),
            &Outgoing::Command(Command::JoinPane { src: 1, horizontal: true }),
        ],
        "합치기 두 명령이 순서대로 안 나갔다: {out:?}"
    );
}

#[test]
fn a_tab_drag_released_nowhere_falls_back_to_select() {
    // 캔버스도 탭도 아닌 자리(상태줄·창 밖)에서 놓으면 클릭과 같은 뜻 — 전환.
    let (mut view, tx, sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.tab_drag = Some(2);
    assert!(view.handle_mouse_up(None));
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter().any(|o| matches!(
            o,
            Outgoing::Command(Command::SelectWindow { index: 2, .. })
        )),
        "전환이 안 나갔다: {out:?}"
    );
    assert!(
        !out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::MoveTab { .. } | Command::JoinPane { .. }))),
        "빈 자리 드롭이 재정렬/합치기로 샜다: {out:?}"
    );
}

// ── 그리기 오라클 — "화면에 무엇이 보이나"를 Scene 에서 기계로 잰다 (G8s 의 남은 빚) ──
//
// # 무엇이 새로 가능해졌나
//
// 큐 오라클은 "서버로 무엇이 나갔나"만 본다. 그리기 배선(상태 → render_* → 엘리먼트)이
// 빠지면 워크스페이스 전부가 초록인 채 화면만 비는데(G8p 류), 그것을 잡는 것은 지금까지
// **라이브 스크린샷뿐**이었다. Scene 의 글리프는 glyph_id 라 글자로 못 되돌린다 — 그래서
// 글자를 그리는 엘리먼트가 **원문을 Scene 에 같이 기록**하게 했고(`Scene::record_text`),
// 이 절이 그 기록을 단언한다.
//
// # 무엇을 안 지나나 (정직하게)
//
// 시험 폰트(`platform::test::FontDB`)는 **빈 Line** 을 돌려준다 — 글자 폭이 전부 0 이라
// 가로 배치·잘림·픽셀 좌표는 여기서 재지 못한다(그건 여전히 frame-dump 라이브 몫).
// 여기서 재는 것은 **존재와 순서**다: 어떤 글자가 그려지는 프레임에 실렸는가.

/// 서버 메시지(와 키)를 먹인 뷰의 한 프레임을 헤드리스로 그려, 그려진 글자들을 돌려준다.
///
/// `App::test` + `Presenter::build_scene` — `clipped_tests` 가 쓰는 그 헤드리스 GUI
/// 파이프라인이라, 레이아웃·페인트·레이어 스택을 실제와 같은 코드로 지난다.
fn painted_after(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<String> {
    painted_after_setup(messages, keys, |_| {})
}

/// 위와 같지만 **글자마다 색을 함께** 돌려준다(`(글자, 색)`).
///
/// # 왜 색이 따로 필요한가
///
/// 이 절의 머리말이 재는 것을 "존재와 순서"로 적어 두었는데, pytmux 의 크롬에는 **색만으로
/// 말하는 자리**가 여럿이다 — 작업이 끝난 탭(pytmux-376)·모드 표식(pytmux-380)·흐린 꼬리줄.
/// 글자만 재는 오라클은 "그려졌지만 엉뚱한 색"과 "제대로 그려졌다"를 못 가른다. 그래서
/// `Scene::record_text` 가 색을 같이 기록하고 여기서 그것을 읽는다.
fn painted_colors(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<(String, ColorU)> {
    painted_scene(messages, keys, |scene| {
        scene
            .painted_texts()
            .map(|t| (t.text.clone(), t.color))
            .collect()
    })
}

/// 위와 같지만 그리기 **직전에** 뷰를 한 번 더 만진다.
///
/// 왜 필요한가: 끊김(`ended`)처럼 **서버 메시지가 아니라 이벤트 루프가** 세우는 상태가
/// 있다. 그 상태의 그림은 메시지만 먹여서는 세울 수 없다.
fn painted_after_setup(
    messages: Vec<ServerMessage>,
    keys: &[(Key, Mods)],
    setup: impl FnOnce(&mut SessionView) + 'static,
) -> Vec<String> {
    painted_scene_setup(messages, keys, setup, |scene| {
        scene.painted_texts().map(|t| t.text.clone()).collect()
    })
}

/// 한 프레임을 헤드리스로 그려 **그 `Scene` 에서 재고 싶은 것만** 뽑는다.
///
/// 위 함수들과 아래 `painted_fills` 가 재는 것은 다르지만(글자 / 글자+세로 자리 / 면),
/// **그리는 절차는 하나**다 — 창을 만들고 뷰를 얹고 한 프레임을 세우는 그 스물몇 줄.
/// 그것을 함수마다 베끼면 파이프라인이 바뀔 때 한쪽만 고쳐지고, 그때 갈리는 것은 코드가
/// 아니라 **오라클끼리의 판정**이다(이 저장소가 「구현 하나」를 못 박는 그 이유다).
fn painted_scene<T: 'static>(
    messages: Vec<ServerMessage>,
    keys: &[(Key, Mods)],
    take: impl FnOnce(&warpui_core::Scene) -> T + 'static,
) -> T {
    painted_scene_setup(messages, keys, |_| {}, take)
}

/// 위와 같지만 그리기 **직전에** 뷰를 한 번 더 만진다(`painted_after_setup` 의 그 자리).
fn painted_scene_setup<T: 'static>(
    messages: Vec<ServerMessage>,
    keys: &[(Key, Mods)],
    setup: impl FnOnce(&mut SessionView) + 'static,
    take: impl FnOnce(&warpui_core::Scene) -> T + 'static,
) -> T {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    let keys = keys.to_vec();
    warpui::App::test((), |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in messages {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        for (key, mods) in keys {
            view.handle_key(key, mods);
            view.pump_headless();
        }
        setup(&mut view);
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            take(&scene)
        })
    })
}

/// 위와 같지만 **세로 자리까지** 돌려준다 — 판이 화면 어디에 섰나를 재는 자리용.
///
/// ⚠ 시험 폰트는 글자 **폭**이 0이라 가로는 못 잰다(위 절 머리말). 세로는 줄 높이가
/// 살아 있어 잴 수 있다 — 그 사실 자체를 `the_bounds_oracle_sees_vertical_positions`
/// 가 먼저 확인한다(빈 오라클로 배치를 단언하면 아무것도 안 재고 통과한다).
fn painted_boxes(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<(String, f32)> {
    painted_scene(messages, keys, |scene| {
        scene
            .painted_texts()
            .map(|t| (t.text.clone(), t.bounds.origin().y()))
            .collect()
    })
}

/// 그 글자가 그려진 세로 자리(여럿이면 첫 것).
fn painted_y(boxes: &[(String, f32)], needle: &str) -> Option<f32> {
    boxes.iter().find(|(t, _)| t.contains(needle)).map(|(_, y)| *y)
}

fn painted_contains(painted: &[String], needle: &str) -> bool {
    painted.iter().any(|t| t.contains(needle))
}

/// **상태줄이 그려진 자리**를 짚는 표식 — 날짜 run(`%Y-%m-%d`)이다.
///
/// ⚠ 배지 하나에 매달리지 않는다. 종전에는 `시계` 배지였다가(§10-21ⓑ 로 사라졌다)
/// `⇕`(터치 스크롤) 배지였는데 그것도 걷었다(pytmux-377) — 두 번 다 표식이 사라지면서
/// 이 오라클들이 함께 무너졌다. 날짜는 상태줄 **기본 형식**(`status-right` =
/// `#h %H:%M %Y-%m-%d`)이 늘 싣고, 머신 이름·시각과 달리 **모양이 고정**이라 어디서
/// 언제 돌려도 같은 방법으로 찾힌다.
fn looks_like_a_date(text: &str) -> bool {
    let t = text.trim().as_bytes();
    t.len() == 10
        && t[4] == b'-'
        && t[7] == b'-'
        && [0, 1, 2, 3, 5, 6, 8, 9].iter().all(|&i| t[i].is_ascii_digit())
}

#[test]
fn the_oracle_itself_sees_a_painted_frame() {
    // ★ 이 오라클이 먼저다 — 기록이 통째로 안 되면 아래 전부가 "없다" 단언만 남아
    // 공허하게 통과한다(부정 단언만 있는 오라클 금지 규칙과 같은 뿌리).
    let painted = painted_after(vec![], &[]);
    assert!(
        painted_contains(&painted, "첫 화면을 기다리는 중"),
        "빈 상태의 대기 문구조차 안 그려졌다 — 기록 장치가 죽었다: {painted:?}"
    );
}

#[test]
fn the_pane_screen_text_is_painted() {
    // G8p 류(상태는 쌓이는데 그리기 배선이 빠짐)를 잡는 양성 오라클.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen], &[]);
    assert!(
        painted_contains(&painted, "HELLO-ORACLE"),
        "패널 화면 글자가 프레임에 없다: {painted:?}"
    );
}

/// 배율을 세운 채 한 프레임 그리고, 그 글자가 차지한 **높이**를 돌려준다.
///
/// 왜 높이인가: 시험 폰트는 글자 **폭**이 0이라 가로는 못 잰다(위 절 머리말) — 줄
/// 높이는 살아 있고 글자 크기를 따라간다. 그래서 "캔버스가 실제로 커졌나"를 잴 수
/// 있는 유일한 축이다.
fn painted_height_at_scale(scale: f32, needle: &str) -> Option<f32> {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    let needle = needle.to_owned();
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    warpui::App::test((), |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in [layout_one_pane(), screen] {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        view.config.font_scale = scale;
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            scene
                .painted_texts()
                .find(|t| t.text.contains(&needle))
                .map(|t| t.bounds.height())
        })
    })
}

#[test]
fn the_canvas_rows_grow_with_the_scale() {
    // ★ `scaled()` 만 재면 **캔버스가 그것을 안 쓰는** 변이가 살아남는다(`render_row`
    //   의 `13.` 을 안 곱해도 크롬은 커지므로 다른 오라클이 다 통과한다). 그래서 실제로
    //   그려진 캔버스 글자의 높이를 잰다 — 제보의 "앱 전체"에서 캔버스가 그 몫이다.
    let one = painted_height_at_scale(1.0, "HELLO-ORACLE").expect("캔버스 글자가 안 그려졌다");
    let two = painted_height_at_scale(2.0, "HELLO-ORACLE").expect("캔버스 글자가 안 그려졌다");
    assert!(
        two > one,
        "배율을 두 배로 했는데 캔버스 줄 높이가 그대로다 — render_row 가 배율을 안 탄다: {one} → {two}"
    );
}

#[test]
fn monitor_badges_sit_in_the_bottom_status_bar_not_the_tab_bar() {
    // 사용자 요청(2026-07-30): 감시류 표식([벨감시]·[활동감시])은 파이썬 정본의 시스템
    // 배지 자리인 **하단 상태줄**이다. 프레임은 위에서 아래로 그려지므로, 탭바에
    // 남아 있으면 캔버스(HELLO-ORACLE)보다 먼저, 상태줄로 갔으면 나중에 그려진다.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    // ⚠ 표본을 **활동 감시**로 바꿨다 — 벨 감시는 화면에서 감췄다(§10-21ⓜ). 그 표식으로
    //   자리를 재면 이제 "없다"가 되어 오라클이 뜻을 잃는다.
    let flags: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status", "windows": [{"index": 0, "name": "하나", "active": true}],
        "monitor_activity": true, "monitor_bell": true
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen, flags], &[]);
    let bell_at = painted.iter().position(|t| t.contains("[활동감시]"));
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    assert!(bell_at.is_some(), "[활동감시] 표식이 프레임에 없다: {painted:?}");
    assert!(canvas_at.is_some(), "캔버스가 없다: {painted:?}");
    assert!(
        bell_at > canvas_at,
        "[활동감시] 가 캔버스보다 먼저(=탭바에) 그려졌다 — 하단 상태줄이 자리다: {painted:?}"
    );
    // ★ 그리고 **벨 감시는 켜져 있어도 안 그려진다**(§10-21ⓜ) — 같은 프레임에서 잰다.
    assert!(
        !painted.iter().any(|t| t.contains("[벨감시]")),
        "감춘 표식이 그려졌다: {painted:?}"
    );
}

#[test]
fn the_disconnect_message_sits_below_the_status_bar_and_opens_the_notice_history() {
    // 사용자 요청(2026-07-30): 종전 자리는 **탭바 바로 밑**이었다 — 줄이 생기는 순간
    // 캔버스를 아래로 밀고 사라질 때 되밀어, 끊겼다 붙는 동안 화면이 출썩였다.
    // 프레임은 위에서 아래로 그려지므로 자리는 **순서로** 잰다(감시 배지 오라클과 같은 식).
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after_setup(vec![layout_one_pane(), screen], &[], |view| {
        view.ended = Some("서버가 닫았다".into())
    });
    let msg_at = painted.iter().position(|t| t.contains("연결 종료"));
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    // 상태줄이 그려진 자리는 **날짜 run** 이 짚는다(`looks_like_a_date` 문서).
    let status_at = painted.iter().position(|t| looks_like_a_date(t));
    assert!(msg_at.is_some(), "끊김 메시지가 프레임에 없다: {painted:?}");
    assert!(
        painted_contains(&painted, "서버가 닫았다"),
        "사유가 프레임에 없다: {painted:?}"
    );
    assert!(canvas_at.is_some() && status_at.is_some(), "캔버스/상태줄이 없다: {painted:?}");
    assert!(
        msg_at > canvas_at,
        "메시지가 캔버스보다 먼저 그려졌다(=위쪽 자리 그대로다): {painted:?}"
    );
    assert!(
        msg_at > status_at,
        "메시지가 상태줄보다 먼저 그려졌다 — 자리는 상태줄 **아래**다: {painted:?}"
    );
}

#[test]
fn clicking_the_disconnect_message_opens_the_notice_history() {
    // 그 줄이 감싸는 클릭 대상이 실제로 알림 이력을 연다(자리는 위 오라클이 잰다).
    // 지나간 메시지는 이 한 줄에 남지 않으니, 눌러 본 사람이 이력에 닿아야 한다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.ended = Some("서버가 닫았다".into());
    view.chrome_click(base::chrome::ClickTarget::Badge(
        base::Badge::Notices,
    ));
    assert_eq!(
        view.screens.top(),
        Some(base::screens::Screen::Notices),
        "알림 이력이 안 열렸다"
    );
}

#[test]
fn the_head_line_names_the_socket_like_the_tui() {
    // 머리줄은 TUI 와 같은 배치(맨 위 한 줄) — 어느 서버에 붙었는지가 화면에 있어야
    // 하고, 복사 결과가 붙는 자리이기도 하다(아래 요약 구역은 비면 안 그려진다).
    // ★ `pytmux-1` 이후 이 줄은 **이 창의 타이틀바**이기도 하다 — 그래도 적히는 글자는
    //   그대로다(제보가 자리를 옮겼지 이름을 바꾸지 않았다).
    let painted = painted_after(vec![], &[]);
    assert!(
        painted_contains(&painted, "pytmux-gui · "),
        "머리줄(소켓)이 프레임에 없다: {painted:?}"
    );
}

#[test]
fn the_head_line_carries_the_window_buttons_now() {
    // ★ `pytmux-1` — OS 타이틀바를 없앴으므로 창 버튼은 **이 줄에** 있어야 한다.
    //   ⛔ 없으면 사용자는 마우스로 창을 닫을 자리를 잃는다(장식이 없는 창이다).
    //   맥만 예외이고 그 이유는 `titlebar` 모듈 머리말의 표에 있다 — 신호등이 OS 것이다.
    let painted = painted_after(vec![], &[]);
    if crate::titlebar::BUTTONS.is_empty() {
        // 맥 — 우리가 그리면 신호등과 합쳐 여섯 개가 된다. 대신 **그 자리를 비웠나**를
        // 양성으로 잰다(부정 단언만 두면 머리줄이 통째로 안 그려져도 통과한다).
        assert!(
            crate::titlebar::reserved_width_for(0) >= crate::titlebar::MAC_LIGHTS_W,
            "신호등 자리를 안 비우면 제목과 겹친다"
        );
        assert!(painted_contains(&painted, "pytmux-gui · "));
    } else {
        for button in crate::titlebar::BUTTONS {
            assert!(
                painted_contains(&painted, button.glyph()),
                "창 버튼 {}이 머리줄에 없다: {painted:?}",
                button.glyph()
            );
        }
    }
}

#[test]
fn the_title_row_sits_above_everything_else() {
    // 타이틀바는 **창 맨 위 한 줄**이다 — 그 자리라야 상류가 "위에서 띠 높이 안"으로
    // 판정하는 창 끌기·더블클릭 최대화가 이 줄에 걸린다(`titlebar` 모듈 머리말).
    // ⚠ 시험 글꼴은 글자 폭이 0이라 가로는 못 잰다 — 세로만 잰다.
    let boxes = painted_boxes(three_tabs(), &[]);
    let head = painted_y(&boxes, "pytmux-gui · ").expect("머리줄이 안 그려졌다");
    let tab = painted_y(&boxes, "하나").expect("탭바가 안 그려졌다");
    assert!(
        head < tab,
        "머리줄이 탭바보다 아래에 있다 — 타이틀바가 아니다: {boxes:?}"
    );
    // 그리고 창 꼭대기에 붙어 있다: 이 줄 위에는 아무 글자도 없다.
    let above = boxes.iter().filter(|(_, y)| *y < head).count();
    assert_eq!(above, 0, "머리줄 위에 그려진 글자가 있다: {boxes:?}");
}

/// 그 자리의 왼쪽 누름을 이 뷰가 **먹었다고 하나**.
///
/// ★ 이 한 값이 곧 "창이 끌리나"다. 상류(winit `event_loop` · 맥 `host_view.m`)는
/// **앱이 안 먹은** 누름만 창 끌기·더블클릭 최대화로 돌리기 때문이다. 그래서 자리를
/// 재는 것이 아니라 **`handled` 를 잰다** — 화면에는 아무 차이가 없는 축이라, 이 오라클이
/// 없으면 `PropagateToParent` 한 줄을 지워도 프레임은 그대로고 아무도 안 운다
/// (이 크레이트가 이미 두 번 밟은 "배선이 통째로 빠짐"의 자리다).
fn mouse_down_handled_at(y: f32, messages: Vec<ServerMessage>) -> bool {
    mouse_down_handled_xy(WIN_W / 2., y, messages)
}

/// 헤드리스 창의 크기 — 아래 오라클들이 **같은 값을 본다**(x 를 오른쪽 끝에서 재려면 필요하다).
const WIN_W: f32 = 800.;
const WIN_H: f32 = 600.;

/// ☠ **종전에는 x 를 한 번도 안 쟀다** — `mouse_down_handled_at` 이 x 를 창 가운데(400)로
/// 박아 두고 y 만 움직였다. 그래서 **머리줄의 오른쪽 끝**(우리가 그린 창 버튼 셋이 앉는 자리)이
/// 이 크레이트에서 한 번도 측정되지 않았다.
///
/// ⚠ 그 구멍은 맥에서는 안 보인다 — `titlebar::BUTTONS` 가 맥에서 **빈 배열**이라 그 상자에서는
/// 잴 것 자체가 없다(그 상수의 주석이 같은 이유로 `reserved_width_for` 를 인자받게 만들었다).
/// ⇒ pytmux/pytmux-155(Windows 에서 창이 안 끌린다)를 맥에서 조사한 회차가 「머리줄 누름을
/// 안 먹는다」를 x=400 하나로 재고 넘어간 자리가 정확히 여기다.
fn mouse_down_handled_xy(x: f32, y: f32, messages: Vec<ServerMessage>) -> bool {
    mouse_down_handled_in(x, y, (WIN_W, WIN_H), 1., messages)
}

/// `mouse_down_handled_xy` 와 같은 것을 **창 크기와 배율까지 인자로** 받아 잰다.
///
/// ☠ **종전에는 그 둘이 박혀 있었다** — 800x600 · 배율 1.0 한 점. 그런데 이 값을 실제로
/// 쓰는 판정(`cell_from_event` → `cell_at`)은 **그 프레임의 캔버스 자리표**를 읽어 셀을
/// 되짚는다. 곧 「머리줄 누름을 안 먹는다」는 성질이 **창 기하에 걸려 있는데** 그 축을
/// 한 번도 안 쟀다. pytmux/pytmux-365 가 후보 ②로 세운 것이 정확히 그 축이다
/// (「왕복 뒤 캔버스 기하가 낡으면 머리줄 좌표가 셀로 되짚힌다」).
///
/// ⚠ 배율(`build_scene` 의 셋째 인자)은 **장치 배율**이고 머리줄 띠를 정하는
/// `config.font_scale` 과 다른 값이다 — 둘 다 움직여야 판이 덮인다.
fn mouse_down_handled_in(
    x: f32,
    y: f32,
    win: (f32, f32),
    scale: f32,
    messages: Vec<ServerMessage>,
) -> bool {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    use warpui_core::event::{Event, ModifiersState};
    warpui::App::test((), move |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in messages {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let _ = presenter.build_scene(vec2f(win.0, win.1), scale, None, ctx);
            presenter
                .dispatch_event(
                    Event::LeftMouseDown {
                        position: vec2f(x, y),
                        modifiers: ModifiersState::default(),
                        click_count: 1,
                        is_first_mouse: false,
                    },
                    ctx,
                )
                .handled
        })
    })
}

#[test]
fn the_titlebar_band_is_re_asserted_from_what_the_window_reports() {
    // 제보(pytmux-365): 전체 화면에 갔다 돌아오면 머리줄을 끌어도 창이 안 움직인다.
    // 그 이슈가 후보 ①로 세운 자리가 여기다 — 종전 `refresh_titlebar_band` 는
    // **우리가 마지막으로 말한 값**을 기억해 그 값이 바뀔 때만 창을 두드렸다:
    //
    //     if self.titlebar_band == Some(band) { return; }   // ← 창이 «잊었을» 때를 못 본다
    //
    // 곧 창이 그 값을 잊는 경로가 하나라도 있으면 그 뒤로 **영영 다시 안 말한다**.
    // 가정을 관측으로 바꿨는지 잰다.
    let body = source_after("fn refresh_titlebar_band(", 1400);
    assert!(
        body.contains("window.titlebar_height()"),
        "창에게 안 물어본다 — 「한 번 말하면 안 잊는다」를 다시 가정하고 있다: {body}"
    );
    assert!(
        !body.contains("if self.titlebar_band == Some(band)"),
        "우리 기억만 보고 일찍 돌아간다 — 창이 잊으면 영영 안 말한다: {body}"
    );
    // ⛔ 그리고 **인형이 그 값을 버리면** 이 축을 영영 못 잰다. 종전 테스트 창은
    //    `fn set_titlebar_height(&self, _height: f64) {}` 였다 — 말한 것이 어디에도
    //    안 남으니 물어볼 자리가 없었고, 그래서 이 결함이 기계 검증 밖에 있었다.
    let doll = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../warpui_core/src/platform/test/delegate.rs"
    ))
    .expect("테스트 창 소스를 못 읽었다");
    assert!(
        doll.contains("self.titlebar_height.set(height)"),
        "테스트 창이 띠를 다시 버린다 — 이 축이 기계 검증 밖으로 나간다"
    );
}

#[test]
fn a_press_on_the_title_row_is_left_for_the_window_to_drag() {
    // ⛔ 이 크레이트의 루트 `EventHandler` 는 **모든** 왼쪽 누름을 삼키고 있었다. 그대로
    //    두면 머리줄을 아무리 그려도 창은 한 픽셀도 안 움직인다 — 상류의 창 끌기 갈래에
    //    영영 안 닿기 때문이다(`titlebar` 모듈 머리말).
    let band = crate::titlebar::band_height(1.);
    assert!(
        !mouse_down_handled_at(band / 2., vec![]),
        "머리줄 누름을 뷰가 먹었다 — 창이 안 끌린다"
    );
    // 그 아래는 종전대로 우리 것이다. 양성 짝이 없으면 "전부 안 먹는다"로 고쳐도 통과한다
    // (그러면 판 클릭·선택이 통째로 죽는다).
    assert!(
        mouse_down_handled_at(band + 40., vec![layout_one_pane()]),
        "캔버스 누름까지 창에게 넘겼다 — 판 클릭·드래그 선택이 죽는다"
    );
}

/// 머리줄을 **x 로 훑는다** — 끌 수 있는 자리와 창 버튼 자리를 갈라 잰다 (pytmux/pytmux-155).
///
/// ☠ 위 시험은 x=400 **한 점**만 잰다. 그래서 「머리줄이 안 끌린다」의 두 갈래
/// ⑴ 띠 전체를 우리가 먹는다 ⑵ 버튼 자리만 먹는다(설계대로) 를 **구별하지 못한다.**
/// 이 시험이 그 둘을 가른다.
///
/// ⚠ **갈래가 OS 마다 다르고, 이 시험은 도는 상자의 갈래만 잰다** — 맥은
/// `titlebar::BUTTONS` 가 비어 있어 버튼 자리가 없고, Windows·Linux 는 오른쪽 끝에 셋이 앉는다.
/// 그래서 단언을 `BUTTONS.is_empty()` 로 갈라 적는다(상수를 읽되 **그 갈래에서만** 단언한다).
#[test]
fn the_titlebar_is_draggable_across_its_width_except_on_our_window_buttons() {
    let band = crate::titlebar::band_height(1.);
    let y = band / 2.;
    let lane = crate::titlebar::reserved_width_for(crate::titlebar::BUTTONS.len());

    // ⑴ **왼쪽 여백과 가운데 제목은 창의 것이다.** 여기가 참이면 창이 한 픽셀도 안 움직인다.
    for x in [2., 20., WIN_W / 4., WIN_W / 2., WIN_W - lane - 4.] {
        assert!(
            !mouse_down_handled_xy(x, y, vec![]),
            "머리줄 x={x} 누름을 뷰가 먹었다 — 그 자리에서 창이 안 끌린다 (버튼 자리 폭 {lane})"
        );
    }

    // ⑵ **창 버튼 자리는 우리 것이다** — 먹어야 그 자리가 드래그 영역에서 빠진다
    //    (`render_titlebar` 의 ★: `on_click` 이라야 하는 이유가 그것이다).
    if !crate::titlebar::BUTTONS.is_empty() {
        let mid_of_last_slot = WIN_W - crate::titlebar::SLOT_W / 2.;
        assert!(
            mouse_down_handled_xy(mid_of_last_slot, y, vec![]),
            "창 버튼(닫기) 자리의 누름을 뷰가 안 먹었다 — 누르면 창이 끌리고 버튼은 안 먹힌다"
        );
    }
}

/// 머리줄이 **창 기하가 움직여도** 계속 창의 것인가 — pytmux/pytmux-365 후보 ②.
///
/// ☠ 위 두 시험은 800x600 · 배율 1.0 **한 판**만 재고, x 훑기는 **패널 없이**(`vec![]`) 돈다.
/// 그런데 머리줄 누름을 삼킬 수 있는 유일한 자는 `cell_from_event` 이고, 그것은 **패널이
/// 있을 때만** 그리는 캔버스 자리표를 읽는다. 곧 「삼킬 수 있는 판」이 그 훑기에 한 번도
/// 안 들어와 있었다.
///
/// 이 시험이 재는 것: **창 크기 · 장치 배율 · 캔버스 유무**를 곱해 머리줄 띠 안을 훑고,
/// 그 전부에서 뷰가 누름을 **안 먹는지** 본다. 하나라도 먹으면 그 판에서 창이 안 끌린다.
///
/// ⚠ 실기 재현(마우스로 실제로 끌기)을 대신하지 않는다 — 이것은 「그럴 수 있는 판」을
/// 좁히는 자다. 실기는 Windows 상자 + 사람 손이 필요하다(그 이슈의 벽).
#[test]
fn the_titlebar_stays_the_windows_across_sizes_scales_and_canvas() {
    let band = crate::titlebar::band_height(1.);
    let lane = crate::titlebar::reserved_width_for(crate::titlebar::BUTTONS.len());

    // 창 크기: 작은 판 · 기본 판 · 전체화면만 한 판(이 상자 2560x1440 · 맥 레티나 판).
    let sizes = [(640., 400.), (WIN_W, WIN_H), (2560., 1392.), (3024., 1890.)];
    // 장치 배율: 1.0 · 1.25 · 1.5(이 Windows 상자) · 2.0(레티나).
    let scales = [1., 1.25, 1.5, 2.];

    for (w, h) in sizes {
        for scale in scales {
            for canvas in [false, true] {
                let msgs = if canvas { vec![layout_one_pane()] } else { vec![] };
                // 띠 안을 위·가운데·아래로 훑는다. 아래 끝(band - 1)이 제일 위험하다 —
                // 캔버스 원점이 한 픽셀이라도 올라오면 그 줄부터 셀로 되짚힌다.
                for y in [1., band / 2., band - 1.] {
                    for x in [2., 20., w / 4., w / 2., w - lane - 4.] {
                        assert!(
                            !mouse_down_handled_in(x, y, (w, h), scale, msgs.clone()),
                            "머리줄 누름을 뷰가 먹었다 — 그 판에서 창이 안 끌린다 (창 {w}x{h} · 배율 {scale} · 캔버스 {canvas} · x {x} · y {y} · 띠 {band})"
                        );
                    }
                }
            }
        }
    }

    // 양성 짝 — 띠 아래는 캔버스가 있을 때 우리 것이어야 한다. 없으면 위 단언은
    // "전부 안 먹는다"로 고쳐도 통과한다(판 클릭이 통째로 죽은 채로).
    for (w, h) in sizes {
        assert!(
            mouse_down_handled_in(w / 2., band + 40., (w, h), 1.5, vec![layout_one_pane()]),
            "캔버스 누름까지 창에게 넘겼다 — 그 판에서 판 클릭·드래그 선택이 죽는다 (창 {w}x{h})"
        );
    }
}

#[test]
fn each_window_button_does_its_own_thing() {
    // ★ 셋이 같은 일을 하거나 하나가 아무 일도 안 하는 회귀는 **화면으로 안 보인다** —
    //   눌러 봐야 알고, 그건 라이브뿐이다. 그래서 뜻을 창 없는 판에서 잰다.
    use crate::titlebar::Button;

    let (mut view, _tx, _sent) = harness();
    assert!(!view.press_window_button(Button::Minimize));
    assert_eq!(view.window_op_for_test(), Some(Button::Minimize));
    assert!(!view.quit_requested(), "최소화가 종료로 샜다");

    let (mut view, _tx, _sent) = harness();
    view.press_window_button(Button::Maximize);
    assert_eq!(view.window_op_for_test(), Some(Button::Maximize));
    assert!(!view.quit_requested(), "최대화가 종료로 샜다");

    // 닫기만 `quit_requested` 로 접힌다 — 상류 `close_window` 가 이 백엔드에서 창을
    // 안 닫는다는 실측(2026-07-30)의 뒤처리가 그 길에 있다.
    let (mut view, _tx, _sent) = harness();
    view.press_window_button(Button::Close);
    assert!(view.quit_requested(), "닫기가 종료 경로를 안 탔다");
    assert_eq!(
        view.window_op_for_test(),
        None,
        "닫기는 창 조작 표식에 남지 않는다(두 길을 다 타면 두 번 처리된다)"
    );
}

#[test]
fn the_tab_chrome_and_status_line_are_painted() {
    let painted = painted_after(three_tabs(), &[]);
    // 네이티브 탭바(N1): `[+]`·`[x]` 가 + 버튼과 활성 탭 안 × 로 바뀌었다.
    for needle in ["하나", "둘", "셋", "+", "×"] {
        assert!(painted_contains(&painted, needle), "{needle} 가 탭바에 없다: {painted:?}");
    }
    // ★ §10-21ⓑ — `서버`·`시계`·`달력` 배지는 **없어야 한다**(오른쪽 글자와 중복이라는
    //   제보). 부정 단언만 두면 상태줄이 통째로 안 그려져도 통과하므로, **그 동작이 옮겨
    //   간 자리**를 함께 양성으로 잰다.
    for gone in ["서버", "시계", "달력"] {
        assert!(
            !painted_contains(&painted, gone),
            "{gone} 배지가 아직 상태줄에 있다(ⓑ): {painted:?}"
        );
    }
    // 오른쪽 구간 셋이 그 동작을 갖는다(ⓑ2 로 라이브 확인) — 시각·날짜는 형식 문자열이
    // 만든다. `%H:%M` 은 시각이라 글자를 못 박으므로 `:` 를 낀 구간이 있는지로 본다.
    assert!(
        painted.iter().any(|t| t.contains("2026-")),
        "오른쪽 날짜 구간(달력 입구)이 없다: {painted:?}"
    );
    assert!(
        painted.iter().any(|t| t.len() == 5 && t.contains(':')),
        "오른쪽 시각 구간(시계 입구)이 없다: {painted:?}"
    );
}

// ── ⓑ2 — 안 먹는 것을 눌리는 것처럼 그리지 않는다 ────────────────────────────
//
// 제보는 "hover 효과만 나고 눌러도 아무 일이 없다"였고, 라이브에서 그 증상을 **정확히**
// 만드는 것은 `set mouse off` 였다(기능 자체는 멀쩡하다 — 제보자가 쓴 릴리스 이진에서도
// 시계·달력이 떴다). 즉 결함은 클릭이 아니라 **강조**다.

#[test]
fn nothing_is_highlighted_when_the_mouse_is_off() {
    // 진리표 넷 — **양성이 먼저**다(마우스가 켜져 있고 그 자리에 있으면 강조가 뜬다).
    // 그것이 없으면 아래 부정 단언은 "늘 false" 로도 통과한다.
    assert!(SessionView::hover_shown(true, true), "켜져 있으면 강조가 떠야 한다");
    assert!(
        !SessionView::hover_shown(false, true),
        "마우스를 껐는데 강조가 뜬다 — 눌러도 아무 일이 없는 것을 눌리는 것처럼 그린다(ⓑ2)"
    );
    assert!(!SessionView::hover_shown(true, false));
    assert!(!SessionView::hover_shown(false, false));
}

#[test]
fn the_click_and_the_highlight_agree_about_the_mouse_setting() {
    // 배선 확인 — 판정을 순수 함수로 빼도 **부르는 자리가 빠지면** 뜻이 없다.
    // 클릭이 거절되는 그 설정에서 강조도 함께 죽는지 본다(둘이 갈리면 그것이 ⓑ2 다).
    let (mut view, _tx, _sent) = harness();
    view.config.mouse = false;
    assert!(!view.chrome_hovered(0));
    assert!(!view.panel_hovered(0));
    assert!(
        !view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Clock)),
        "마우스를 끈 판에서 크롬 클릭이 먹었다"
    );
}

#[test]
fn the_popup_box_is_painted_over_the_canvas() {
    let popup: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 24, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 24, "title": "sh"}],
        "popup": {"id": 99, "x": 10, "y": 5, "w": 40, "h": 10,
                  "cx": 11, "cy": 6, "cw": 38, "ch": 8, "title": "ORACLE-POP"}
    }))
    .unwrap();
    let painted = painted_after(vec![popup], &[]);
    assert!(
        painted_contains(&painted, "ORACLE-POP"),
        "팝업 제목이 프레임에 없다: {painted:?}"
    );
}

#[test]
fn a_screen_floats_over_the_canvas_in_the_frame() {
    // N2: 화면(팔레트)은 캔버스를 **대체하지 않고 위에 뜬다** — 캔버스·제목·힌트·목록이
    // 한 프레임에 같이 있고, 판(제목)이 캔버스보다 **나중에**(=위에) 그려져야 한다.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(
        vec![layout_one_pane(), screen],
        &[(Key::Char('b'), Mods::CTRL), (Key::Char(':'), Mods::NONE)],
    );
    let canvas_at = painted.iter().position(|t| t.contains("HELLO-ORACLE"));
    let title_at = painted.iter().position(|t| t.contains("명령"));
    assert!(canvas_at.is_some(), "캔버스가 팝업 밑에서 사라졌다: {painted:?}");
    assert!(title_at.is_some(), "팔레트 제목이 없다: {painted:?}");
    assert!(
        title_at > canvas_at,
        "판이 캔버스보다 먼저 그려졌다(위아래가 뒤집혔다): {painted:?}"
    );
    // 내용도 판과 함께 떠야 한다 — 틀만 있고 목록 배선이 빠지는 결함을 잡는 양성 단언.
    assert!(
        painted_contains(&painted, "> _"),
        "팔레트 필터 줄이 없다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "split-window"),
        "팔레트 목록이 없다: {painted:?}"
    );
}

// ── 패널 테두리를 **실제 선**으로(2026-07-31 사용자 지시) ──────────────────────

/// 테두리 사각형 하나짜리 배치와, 그것을 합성한 캔버스.
fn framed_canvas() -> (proto::canvas::Canvas, proto::message::Layout) {
    use proto::canvas::Canvas;
    use proto::message::{Layout, PaneLayout};
    let mut canvas = Canvas::new(10, 5);
    canvas.draw_box(0, 0, 10, 5, CellStyle::default());
    // 패널 **안**에 앱이 그린 선문자 하나 — 이건 우리 크롬이 아니다.
    canvas.put_text(3, 2, "┌", CellStyle::default());
    let layout = Layout {
        cols: 10,
        rows: 5,
        panes: vec![PaneLayout {
            id: 1,
            x: 1,
            y: 1,
            w: 8,
            h: 3,
            boxrect: Some([0, 0, 10, 5]),
            ..Default::default()
        }],
        active: 1,
        ..Default::default()
    };
    (canvas, layout)
}

#[test]
fn the_pane_frame_becomes_real_line_segments() {
    // 양성 오라클: 네 모서리가 **뻗는 방향까지** 옳아야 한다. 비트를 잘못 옮기면 모서리가
    // 바깥으로 삐져나가거나 안쪽이 뚫려 보인다 — 눈으로는 한참 봐야 보이는 종류다.
    let (canvas, layout) = framed_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout), base::tint::BorderTint::Local);
    let at = |x: u16, y: u16| segs.iter().find(|s| s.x == x && s.y == y).map(|s| s.bits);
    assert_eq!(at(0, 0), Some(0b0101), "┌ 는 아래·오른쪽으로만 뻗는다");
    assert_eq!(at(9, 0), Some(0b0110), "┐ 는 아래·왼쪽");
    assert_eq!(at(0, 4), Some(0b1001), "└ 는 위·오른쪽");
    assert_eq!(at(9, 4), Some(0b1010), "┘ 는 위·왼쪽");
    assert_eq!(at(5, 0), Some(0b0011), "위 변은 좌우로");
    assert_eq!(at(0, 2), Some(0b1100), "왼 변은 상하로");
}

#[test]
fn a_box_character_inside_a_pane_is_left_alone() {
    // ★ 테두리를 네이티브로 그리는 것이지 **남의 화면을 고쳐 그리는 것이 아니다.**
    //   캔버스를 통째로 훑으면 패널 안 앱(`htop` 등)이 그린 선문자까지 선으로 바뀐다.
    let (canvas, layout) = framed_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout), base::tint::BorderTint::Local);
    assert!(
        !segs.iter().any(|s| s.x == 3 && s.y == 2),
        "패널 안의 선문자를 크롬으로 잡았다"
    );
}

#[test]
fn the_cells_we_draw_as_lines_are_exactly_the_cells_we_blank() {
    // 한쪽만 바뀌면 선이 두 겹으로 보이거나(글자가 남음) 테두리가 통째로 사라진다.
    let (canvas, layout) = framed_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout), base::tint::BorderTint::Local);
    let cells = SessionView::frame_cells(&canvas, Some(&layout));
    assert!(!cells.is_empty(), "빈 목록은 통과가 아니라 고장이다");
    assert_eq!(cells.len(), segs.len());
    for seg in &segs {
        assert!(cells.contains(&(seg.x, seg.y)), "({},{}) 가 비우는 목록에 없다", seg.x, seg.y);
    }
}

#[test]
fn a_divider_cell_is_left_to_the_splitter_bar() {
    // 경계 칸에 선까지 그리면 잡는 자리가 두 겹으로 보인다 — 바가 자기 그림을 그린다.
    use proto::message::Divider;
    let (canvas, mut layout) = framed_canvas();
    layout.dividers = vec![Divider {
        split_id: 7,
        orient: "lr".into(),
        x: 0,
        y: 2,
        w: 1,
        h: 1,
        ..Default::default()
    }];
    let segs = SessionView::frame_segments(&canvas, Some(&layout), base::tint::BorderTint::Local);
    assert!(
        !segs.iter().any(|s| s.x == 0 && s.y == 2),
        "스플리터 바가 있는 칸에 테두리 선까지 그렸다"
    );
}

/// 좌우로 쪼갠 배치 — 경계 열이 위·아래 테두리와 만나 `┬`·`┴` 를 만든다.
///
/// 서버가 주는 경계 사각형은 노드 rect **전체 높이**를 덮으므로 그 양 끝이 곧 이음새다.
fn split_canvas() -> (proto::canvas::Canvas, proto::message::Layout) {
    use proto::canvas::Canvas;
    use proto::message::{Divider, Layout, PaneLayout};
    let mut canvas = Canvas::new(10, 5);
    canvas.draw_box(0, 0, 5, 5, CellStyle::default());
    canvas.draw_box(4, 0, 6, 5, CellStyle::default());
    // 첫 패널이 **활성**이다 — 테두리 색이 두 단인 것을 재려면 그 갈림이 있어야 한다.
    let pane = |id, x| PaneLayout {
        id,
        x: x + 1,
        y: 1,
        w: 3,
        h: 3,
        boxrect: Some([x, 0, if x == 0 { 5 } else { 6 }, 5]),
        active: id == 1,
        ..Default::default()
    };
    let layout = Layout {
        cols: 10,
        rows: 5,
        panes: vec![pane(1, 0), pane(2, 4)],
        dividers: vec![Divider {
            split_id: 7,
            orient: "lr".into(),
            x: 4,
            y: 0,
            w: 1,
            h: 5,
            ..Default::default()
        }],
        active: 1,
        ..Default::default()
    };
    (canvas, layout)
}

/// ★ §10-21ⓟ — **이음새는 테두리가 그린다.**
///
/// 이 칸까지 바에게 주면 가로 테두리가 거기서 끊긴 채 세로선만 지나가, 세로 스플리터가
/// 패널 위 테두리를 **넘어 위로 뻗은 것**처럼 보인다(제보의 스크린샷). 곧은 칸은 종전대로
/// 바의 것이다 — 그 자리에 선을 겹쳐 그리면 잡는 자리가 두 겹으로 보인다.
#[test]
fn the_junction_cells_of_a_divider_stay_with_the_frame() {
    let (canvas, layout) = split_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout), base::tint::BorderTint::Local);
    let at = |x: u16, y: u16| segs.iter().find(|s| s.x == x && s.y == y).map(|s| s.bits);
    assert_eq!(at(4, 0), Some(0b0111), "┬ 는 좌·우·아래 — 가로 테두리가 이어져야 한다");
    assert_eq!(at(4, 4), Some(0b1011), "┴ 는 좌·우·위");
    // 그 사이의 곧은 칸은 종전대로 바의 것이다.
    assert_eq!(at(4, 2), None, "곧은 경계 칸까지 테두리가 그리면 선이 두 겹이다");
}

/// 그리고 바는 **그 칸을 건너뛴다** — 안 건너뛰면 방금 그린 이음새를 바탕색으로 덮는다.
///
/// 판정이 한 곳(`frame_cells`)이라 둘이 갈릴 수 없다는 것까지 여기서 잰다.
#[test]
fn the_bar_skips_the_cells_the_frame_owns() {
    let (canvas, layout) = split_canvas();
    let frame = SessionView::frame_cells(&canvas, Some(&layout));
    let divider = &layout.dividers[0];
    let bar = crate::splitter::Bar {
        vertical: true,
        x: divider.x,
        y: divider.y,
        w: divider.w,
        h: divider.h,
        active: false,
        tint: None,
        skip: frame
            .iter()
            .copied()
            .filter(|(fx, fy)| {
                *fx >= divider.x
                    && *fx < divider.x + divider.w
                    && *fy >= divider.y
                    && *fy < divider.y + divider.h
            })
            .collect(),
    };
    assert_eq!(bar.skip.len(), 2, "이음새 둘(┬·┴)을 못 찾았다: {:?}", bar.skip);
    // 위·아래 이음새를 뺀 **가운데 세 칸**만 한 구간으로 칠한다.
    assert_eq!(bar.runs(), vec![(1, 3)], "바가 이음새를 덮는다");
}

/// 이음새가 없으면(경계가 테두리에 안 닿는 배치) 종전처럼 **통짜 한 구간**이다 —
/// 구간으로 자른 것이 평상시 그림을 바꾸지 않았음을 못박는다.
#[test]
fn a_divider_without_junctions_is_still_painted_in_one_piece() {
    let bar = crate::splitter::Bar {
        vertical: true,
        x: 4,
        y: 0,
        w: 1,
        h: 5,
        active: false,
        tint: None,
        skip: Default::default(),
    };
    assert_eq!(bar.runs(), vec![(0, 5)]);
    // 가로 바는 x 축으로 센다(축을 헷갈리면 세로 바만 맞고 가로가 조용히 틀린다).
    let across = crate::splitter::Bar {
        vertical: false,
        x: 2,
        y: 3,
        w: 4,
        h: 1,
        active: false,
        tint: None,
        skip: [(3, 3)].into_iter().collect(),
    };
    assert_eq!(across.runs(), vec![(2, 1), (4, 2)]);
}

// ── 테두리가 **상태를 말한다**(§10-21ⓩ) ───────────────────────────────────────

/// ★ 원격 탭이면 테두리도 분홍이다 — 탭 라벨만 분홍이고 테두리는 파랗던 자리다.
///
/// 그리고 **활성/비활성이 두 단**이라야 원격 탭 안에서 어느 패널이 키를 받는지가 남는다.
#[test]
fn a_remote_tab_paints_the_frame_pink_in_two_shades() {
    use base::tint::BorderTint;
    let (canvas, layout) = split_canvas();
    // `split_canvas` 의 첫 패널이 활성이다(id 1 · layout.active = 1).
    let segs = SessionView::frame_segments(&canvas, Some(&layout), BorderTint::Remote);
    let at = |x: u16, y: u16| segs.iter().find(|s| s.x == x && s.y == y).map(|s| s.color);
    assert_eq!(at(0, 2), Some(REMOTE_PINK), "활성 패널 왼 변이 밝은 분홍이 아니다");
    assert_eq!(at(9, 2), Some(REMOTE_PINK_DIM), "비활성 패널 오른 변이 어두운 분홍이 아니다");
    // 두 색이 실제로 달라야 두 단인 뜻이 산다(같은 상수를 두 번 쓰면 오라클이 공허하다).
    assert_ne!(REMOTE_PINK, REMOTE_PINK_DIM);
}

/// degraded 는 **원격보다 먼저**이고 활성·비활성이 같은 빨강이다.
///
/// 그 순간 중요한 것은 "어느 패널이 활성인가"가 아니라 "끊기고 있다"는 사실이다.
#[test]
fn a_degraded_link_paints_every_frame_red() {
    use base::tint::BorderTint;
    let (canvas, layout) = split_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout), BorderTint::Degraded);
    assert!(!segs.is_empty(), "빈 목록은 통과가 아니라 고장이다");
    assert!(
        segs.iter().all(|s| s.color == theme::ERROR),
        "빨갛지 않은 칸이 있다: {:?}",
        segs.iter().find(|s| s.color != theme::ERROR).map(|s| (s.x, s.y))
    );
}

/// 평소에는 **캔버스 색을 그대로** 둔다 — 서버의 활성/비활성 판정이 그 색에 들어 있다.
#[test]
fn a_local_tab_leaves_the_canvas_colours_alone() {
    use base::tint::BorderTint;
    let (canvas, layout) = split_canvas();
    let segs = SessionView::frame_segments(&canvas, Some(&layout), BorderTint::Local);
    assert!(!segs.is_empty());
    assert!(
        segs.iter().all(|s| s.color != REMOTE_PINK && s.color != theme::ERROR),
        "평소인데 상태색을 칠했다"
    );
}

#[test]
fn without_a_layout_nothing_is_converted() {
    // 첫 프레임(배치 없음)에 크롬을 지어내면 없는 테두리가 잠깐 번쩍인다.
    let (canvas, _) = framed_canvas();
    assert!(SessionView::frame_segments(&canvas, None, base::tint::BorderTint::Local).is_empty());
}

// ── 탭 라벨이 «바뀌었다»를 색으로 말하나 (pytmux-376) ────────────────────────
//
// 제보: 같은 탭이 정본에서는 주황 글자인데 GUI 는 다른 탭과 같은 색이었다(첨부 2장).
// 글리프(`!`)는 이미 둘 다 그리고 있었으므로 **빠진 것은 색 하나**였다.

/// 시험용 탭 하나 — 필요한 칸만 세운다.
fn tab_for_colour(f: impl FnOnce(&mut proto::Tab)) -> proto::Tab {
    let mut t = proto::Tab {
        index: 1,
        name: "구현".into(),
        ..Default::default()
    };
    f(&mut t);
    t
}

/// WCAG 상대휘도 대비(pytmux-372 가 쓴 그 방법) — 1.0 ~ 21.0.
fn contrast_ratio(a: ColorU, b: ColorU) -> f32 {
    fn lum(c: ColorU) -> f32 {
        let ch = |v: u8| {
            let v = v as f32 / 255.;
            if v <= 0.03928 { v / 12.92 } else { ((v + 0.055) / 1.055).powf(2.4) }
        };
        0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b)
    }
    let (x, y) = (lum(a), lum(b));
    let (hi, lo) = if x > y { (x, y) } else { (y, x) };
    (hi + 0.05) / (lo + 0.05)
}

/// ★ 비활성 탭에서 Claude 가 끝났으면 **글자가 주황**이다 — 정본 `done_st` 그대로.
#[test]
fn a_finished_claude_tab_says_so_in_amber() {
    let done = tab_for_colour(|t| t.claude_done = true);
    let plain = tab_for_colour(|_| {});
    assert_eq!(SessionView::tab_label_color(&done), CLAUDE_DONE_AMBER);
    // 대조군이 없으면 "전부 주황"도 통과한다 — 그러면 오라클이 공허하다.
    assert_eq!(SessionView::tab_label_color(&plain), palette::FG);
    assert_ne!(CLAUDE_DONE_AMBER, palette::FG, "알림색이 기본색과 같으면 알림이 아니다");
}

/// 활성 탭은 안 물들인다 — 정본도 `active` 갈래가 `claude_done` 보다 앞이다.
///
/// 지금 보고 있는 탭에 "바뀌었다"를 칠하면, 그 색이 뜻하는 «봐야 할 다른 탭»이 흐려진다.
#[test]
fn the_tab_you_are_looking_at_keeps_its_label_colour() {
    let t = tab_for_colour(|t| {
        t.active = true;
        t.claude_done = true;
    });
    assert_eq!(SessionView::tab_label_color(&t), palette::FG);
}

/// 원격이면서 Claude 가 끝난 탭은 **주황**이다(정본 갈래 순서: `claude_done` → `remote`).
///
/// 뒤집으면 원격 탭에서만 완료 알림이 조용해지고, 그 침묵은 눈으로 못 찾는다.
#[test]
fn a_finished_claude_beats_the_remote_pink() {
    let t = tab_for_colour(|t| {
        t.remote = true;
        t.claude_done = true;
    });
    assert_eq!(SessionView::tab_label_color(&t), CLAUDE_DONE_AMBER);
    // 원격만이면 그대로 분홍이라야 §1.7-a 가 산다.
    let remote = tab_for_colour(|t| t.remote = true);
    assert_eq!(SessionView::tab_label_color(&remote), REMOTE_PINK);
}

/// ⛔ 벨·활동은 **탭 라벨 색을 안 바꾼다 — 정본이 안 바꾼다**(pytmux-376 의 남은 절반).
///
/// 제보 화면의 주황은 벨이 아니라 `claude_done` 이었다(정본 `TabBar.render_line` 에는
/// `bell`·`activity` 갈래가 아예 없다 — 그 둘을 색으로 가르는 것은 **하단 상태줄의 창
/// 목록**이다). 여기에만 색을 더하면 갈림을 **새로 만드는** 것이라, 이 오라클이 그
/// 판정을 못박는다. 탭바에서도 벨/활동을 색으로 말하게 하려면 **두 클라를 함께** 고친다.
#[test]
fn a_bell_or_activity_does_not_repaint_the_tab_label() {
    let bell = tab_for_colour(|t| t.bell = true);
    let activity = tab_for_colour(|t| t.activity = true);
    assert_eq!(SessionView::tab_label_color(&bell), palette::FG);
    assert_eq!(SessionView::tab_label_color(&activity), palette::FG);
    // 다만 **글자로는** 이미 말한다 — 그 표식까지 사라지면 이건 침묵이 맞다.
    assert!(bell.label(2, proto::tabs::FULL_TITLE).ends_with('!'));
    assert!(activity.label(2, proto::tabs::FULL_TITLE).ends_with('#'));
}

/// 고른 색이 **그 바탕에서** 읽히나 — 탭바는 캔버스가 아니라 `SURFACE` 위다.
///
/// pytmux-372 가 이 앱의 대비가 빠듯하다는 것을 재 놓았다. 비활성 탭은 배경을 안 칠하므로
/// 바탕이 곧 띠 색이다.
#[test]
fn an_amber_tab_label_is_readable_on_the_tab_strip() {
    let ratio = contrast_ratio(CLAUDE_DONE_AMBER, theme::SURFACE);
    assert!(ratio >= 4.5, "탭 띠 위에서 안 읽힌다: {ratio:.2}:1");
    // 이미 뜻이 박힌 색과 겹치면 두 뜻이 한 그림이 된다(theme.rs 가 갈라 둔 이유).
    assert_ne!(CLAUDE_DONE_AMBER, theme::FOCUS, "'고르는 중'과 '바뀌었다'가 같은 색이다");
    assert_ne!(CLAUDE_DONE_AMBER, theme::INVERT_BG, "'켜짐'과 '바뀌었다'가 같은 색이다");
    assert_ne!(CLAUDE_DONE_AMBER, REMOTE_PINK, "'원격'과 '바뀌었다'가 같은 색이다");
}

// ★ 위 다섯은 **순수 함수**를 직접 부른다(그 함수 문서가 적은 이유 — 종전에는 헤드리스
// 프레임에 색이 안 남아 그리는 자리에 두면 아무도 못 쟀다). 그 전제는 2026-08-24 에
// 사라졌다: `Scene::record_text` 가 색을 함께 기록하므로(`PROVENANCE.md` §47) **부르는
// 줄까지** 잴 수 있다 — 순수 함수만 재면 `render_tabs` 에서 그 함수를 부르는 줄을 지워도
// 통과한다(루트 CLAUDE.md §표시 기능은 호출부까지 단언 — 이 저장소가 두 번 물린 부류).
/// ★ **호출부까지 잰다.** 위 넷은 순수 함수를 재므로, `render_tabs` 에서 그 함수를 부르는
/// 줄을 지워도 통과한다(이 저장소가 「공허 통과」로 두 번 물린 부류 — 루트 CLAUDE.md
/// §표시 기능은 호출부까지 단언). 그래서 프레임에서 그 색을 실제로 찾는다.
#[test]
fn the_frame_really_paints_a_finished_tab_amber() {
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status", "windows": [
            {"index": 0, "name": "하나", "active": true},
            {"index": 1, "name": "구현", "claude_done": true},
        ]
    }))
    .unwrap();
    let painted = painted_colors(vec![layout_one_pane(), status], &[]);
    let done = painted
        .iter()
        .find(|(text, _)| text.contains("구현"))
        .unwrap_or_else(|| panic!("완료 탭이 프레임에 없다: {painted:?}"));
    assert_eq!(done.1, CLAUDE_DONE_AMBER, "완료 탭이 호박색이 아니다: {:?}", done);
    // 조용한 탭은 그대로 기본색이다 — 안 그러면 "전부 호박색"으로도 이 시험이 통과한다.
    let quiet = painted
        .iter()
        .find(|(text, _)| text.contains("하나"))
        .expect("조용한 탭이 없다");
    assert_eq!(quiet.1, palette::FG, "조용한 탭까지 물들였다");
}

/// 같은 이유의 호출부 오라클 — 모드 표식 칩의 **바탕색**이 정본의 호박색인가(pytmux-380).
#[test]
fn the_command_mode_chip_is_painted_in_the_canon_accent() {
    let fills = painted_fills(vec![layout_one_pane()], &[(Key::Escape, Mods::NONE)]);
    assert!(
        fills.iter().any(|(c, _, _)| *c == CLAUDE_DONE_AMBER),
        "esc 모드 칩이 정본 색으로 안 칠해졌다: {fills:?}"
    );
    // 대조군: 평소 모드에는 그 색이 없어야 한다(있으면 위 단언이 공허하다).
    let quiet = painted_fills(vec![layout_one_pane()], &[]);
    assert!(
        !quiet.iter().any(|(c, _, _)| *c == CLAUDE_DONE_AMBER),
        "모드가 아닌데 그 색이 이미 있다 — 위 단언이 공허해진다"
    );
}

// ── 검색이 «다 못 봤다» 를 말한다 (pytmux-404) ────────────────────────────────────

#[test]
fn a_host_that_answered_but_held_some_back_still_says_so() {
    // ⛔ 종전에는 `state != "ok"` 인 상류만 훑어서, **답했는데 일부를 못 실은** 경우가
    //    통째로 사라졌다. 그건 문구가 아니라 판정이다 — 「없다」와 「안 봤다」를 가른다.
    let sr: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "search_results", "query": "x", "items": [],
        "hosts": [
            {"host": "boxA", "state": "ok", "n": 3, "hidden": 2},
            {"host": "boxB", "state": "ok", "n": 1, "dropped": 7},
            {"host": "boxC", "state": "ok", "n": 5},
        ]
    }))
    .unwrap();
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(sr))).unwrap();
    view.pump_headless();
    let notes = view.state.search_results().expect("회신이 안 담겼다").notes();
    assert!(notes.contains("boxA"), "숨긴 탭을 안 적었다: {notes}");
    assert!(notes.contains("2"), "숨긴 수를 안 적었다: {notes}");
    assert!(notes.contains("boxB"), "못 실은 것을 안 적었다: {notes}");
    assert!(notes.contains('7'), "못 실은 수를 안 적었다: {notes}");
    // ⛔ 대조군: 전수로 답한 곳은 «빠진 곳» 에 들지 않는다(그러면 경고가 잡음이 된다).
    assert!(!notes.contains("boxC"), "멀쩡한 상류를 빠진 곳으로 적었다: {notes}");
}

// ── 스펙의 막대 (pytmux-371 ③) ────────────────────────────────────────────────────

fn machines_spec() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-machines", "kind": "table",
        "title": "토큰 사용량 · 머신별", "hint": "↑↓ 이동 · Esc 닫기",
        "rows": [
            {"key": "이 머신", "label": "이 머신", "cols": ["1,200"], "bar": 1000},
            {"key": "91ddca94", "label": "91ddca94", "cols": ["300"], "bar": 250},
        ],
        "text": "", "note": "", "selected": 0
    }))
    .unwrap()
}

/// 정본 토큰 팝업의 **글자 키**를 실은 스펙(pytmux-371 · 서버 `screenspec._hub_keys`).
///
/// 가르는 것: `keys` 에 탭으로 가는 글자 다섯이 있다. 정본이 «소비만 하고 무동작»으로
/// 둔 글자들(`h`·`d`·`w`·`m`·`r`)은 **여기 안 싣는다** — 이 클라는 표에 없는 글자에
/// 이미 아무 일도 안 하므로(`a_letter_the_spec_does_not_declare_is_ignored_not_a_close`),
/// 실으면 아무 일도 안 하는 왕복만 서버에 한 번 더 가게 된다.
fn machines_spec_with_letter_keys() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-machines", "kind": "table",
        "title": "토큰 사용량 · 머신별",
        "hint": "↑↓ 이동 · Esc 닫기 · p세션 · l한도 · o머신 · s시나리오 · u/usage",
        "rows": [
            {"key": "이 머신", "label": "이 머신", "cols": ["1,200"], "bar": 1000},
            {"key": "goto:limits", "label": "한도(/usage) 보기 →", "cols": []},
        ],
        "text": "", "note": "", "selected": 0,
        "keys": {"p": "goto:sessions", "l": "goto:limits", "o": "goto:period",
                 "s": "goto:settings", "u": "refresh-usage"}
    }))
    .unwrap()
}

#[test]
fn a_letter_key_on_a_token_panel_sends_the_action_the_canonical_popup_would() {
    // ⛔ **여기가 «있다» 와 «같게 군다» 가 갈리는 자리다**(루트 CLAUDE.md ★★). 잇는 줄은
    //    이미 있었지만, 정본을 손에 익힌 사람은 줄을 고르지 않고 `l` 을 친다
    //    (`plugins/claude-code/screens.py::TokenLogScreen.on_key`).
    //
    // 재는 것은 **나가는 것**이다 — 그리는 것이 아니라. 스펙이 키를 실어도 그 키가
    // 액션이 안 되면 판만 예쁘고 아무 일도 안 난다(부정 단언만 있는 오라클이 놓치는 것).
    let sent = sent_after(
        vec![layout_one_pane(), machines_spec_with_letter_keys()],
        &[(Key::Char('l'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("`l` 이 액션이 안 됐다: {frames:?}"));
    assert_eq!(action["id"], "claude-token-machines");
    assert_eq!(action["do"], "goto:limits", "`l` 이 한도 판으로 안 간다: {action:?}");
}

/// 한도 판 — 줄 하나가 **리셋 시각**(`until`)을 싣는다(pytmux-371 ④).
fn limits_spec_with_deadline(until: i64) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-usage-panel", "kind": "table",
        "title": "Claude 사용 한도 (/usage)", "hint": "↑↓ 스크롤 · Esc 닫기",
        "rows": [
            {"key": "세션", "label": "세션", "cols": ["42% 사용"], "bar": 420,
             "until": until},
            {"key": "acct", "label": "계정 me@example.com", "cols": []}
        ],
        "text": "", "note": "", "selected": 0, "keys": {}
    }))
    .unwrap()
}

/// 고르개 줄 — 정본 `[한도]` 탭의 행0·행1(모델·컨텍스트).
fn limits_spec_with_choosers() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-usage-panel", "kind": "table",
        "title": "Claude 사용 한도 (/usage)", "hint": "↑↓ · ↔ 값 · Enter 적용",
        "rows": [
            {"key": "mc:model", "label": "모델", "cols": ["opus-5"], "w": "choose"},
            {"key": "mc:ctx", "label": "컨텍스트", "cols": ["기본"], "w": "choose"},
            {"key": "세션", "label": "세션", "cols": ["42% 사용"], "bar": 420}
        ],
        "text": "", "note": "", "selected": 0,
        "keys": {"enter": "apply", "left": "prev", "right": "next"}
    }))
    .unwrap()
}

#[test]
fn a_chooser_row_draws_the_arrows_that_say_it_can_be_turned() {
    // ☠ pytmux-130 — 정본 `[한도]` 탭의 맨 위 두 줄은 **모델·컨텍스트 고르개**다
    //    (`←→` 로 값을 돌리고 `Enter` 로 적용). 그 줄을 여느 표 줄과 같이 그리면
    //    「돌릴 수 있다」가 화면 어디에도 안 적히고, 키만 물리는 것은 pytmux-185 가
    //    세는 갈림이다(있는 것과 같게 구는 것은 다른 질문이다).
    //
    //    ⛔ 화살표는 **우리가 그린다** — 서버가 `◀ 값 ▶` 글자를 실으면 다시 텍스트
    //       기반 인터페이스가 된다(막대를 글자로 안 싣는 것과 같은 경계).
    let painted = painted_after(vec![layout_one_pane(), limits_spec_with_choosers()], &[]);
    assert!(
        painted.iter().any(|t| t == "◀") && painted.iter().any(|t| t == "▶"),
        "고르개 줄에 화살표가 안 그려진다: {painted:?}"
    );
    // 값 자체는 서버가 준 그대로 — 우리가 지어내지 않는다.
    assert!(painted.iter().any(|t| t.contains("opus-5")), "{painted:?}");
}

#[test]
fn a_plain_row_gets_no_chooser_arrows() {
    // 대조군 — 힌트가 없는 줄에 화살표를 붙이면 그 화살표가 거짓말이다(눌러도 안 돈다).
    let painted = painted_after(
        vec![layout_one_pane(), limits_spec_with_deadline(0)],
        &[],
    );
    assert!(
        !painted.iter().any(|t| t == "◀" || t == "▶"),
        "고르개가 아닌 줄에 화살표가 붙었다: {painted:?}"
    );
}

#[test]
fn turning_a_chooser_sends_the_key_of_that_row() {
    // 정본은 `←→` 로 값을 돌린다. 그 뜻은 **서버가 정하고**(`prev`/`next`) 우리는 어느
    // 줄에서 눌렀는지를 그 줄의 열쇠로 말한다 — 표를 두 벌로 안 적는 그 규약이다.
    let sent = sent_after(
        vec![layout_one_pane(), limits_spec_with_choosers()],
        &[(Key::Right, Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("`→` 가 액션이 안 됐다: {frames:?}"));
    assert_eq!(action["do"], "next", "{action:?}");
    assert_eq!(action["input"], "mc:model", "{action:?}");
}

#[test]
fn the_limit_panel_counts_down_to_the_reset_the_server_named() {
    // 정본 `[한도]` 탭은 다음 리셋까지를 **큰 글자 카운트다운**으로 센다. 서버가 그 글자를
    // 지어 보내면 초마다 프레임이 와야 하므로(판 하나 때문에 초당 전 세션 재그리기),
    // 서버는 **언제인지**만 싣고 남은 시간은 이쪽이 굴린다.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let painted = painted_after(
        vec![layout_one_pane(), limits_spec_with_deadline(now + 3661)],
        &[],
    );
    assert!(
        painted.iter().any(|t| t.starts_with("1:0")),
        "리셋까지 남은 시간이 안 그려진다: {painted:?}"
    );
}

#[test]
fn a_reset_that_already_passed_is_not_drawn_as_zero() {
    // ⛔ 대조군이자 규율: `0:00:00` 이 굳어 있으면 그것이 **「지금 리셋된다」**로 읽힌다.
    //    실제 뜻은 「실측이 낡았다」이고, 그 사실은 판의 신선도 줄이 따로 말한다.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs() as i64;
    let painted = painted_after(
        vec![layout_one_pane(), limits_spec_with_deadline(now - 60)],
        &[],
    );
    assert!(
        !painted.iter().any(|t| t.contains("0:00:00")),
        "지난 시각을 0 으로 그린다: {painted:?}"
    );
    // 시각이 아예 없는 줄도 같다(대조군 둘째).
    let painted = painted_after(vec![layout_one_pane(), limits_spec_with_deadline(0)], &[]);
    assert!(
        !painted.iter().any(|t| t.contains(":00:")),
        "시각이 없는데 카운트다운을 그린다: {painted:?}"
    );
}

#[test]
fn the_clock_only_repaints_for_a_countdown_that_is_actually_on_screen() {
    // `true` 를 돌려주면 화면 **전체**를 다시 그린다 — 카운트다운이 없는 평상시에도 초당
    // 한 번 전 세션을 다시 그리면 그 비용을 아무도 안 낸다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    assert!(!view.tick_clock(), "판이 안 떴는데 초마다 다시 그린다");
}

#[test]
fn a_row_with_a_ratio_is_drawn_as_a_bar_not_as_block_glyphs() {
    // 정본은 같은 뜻을 `█` 글자로 그린다(격자라 그 길뿐이다). GUI 는 면으로 그린다 —
    // 사용자 지시(«인터페이스는 gui 기반»)이고, 서버는 **비율만** 싣는다.
    let fills = painted_fills(vec![layout_one_pane(), machines_spec()], &[]);
    let bars = fills
        .iter()
        .filter(|(c, _, _)| *c == theme::INVERT_BG)
        .count();
    assert!(bars >= 2, "막대가 줄마다 안 그려졌다(면 {}): {fills:?}", bars);
    // 그리고 글자로는 `█` 를 쓰지 않는다 — 그것을 쓰면 정본 그림을 흉내내는 것이다.
    let painted = painted_after(vec![layout_one_pane(), machines_spec()], &[]);
    assert!(
        !painted.iter().any(|t| t.contains('█')),
        "막대를 글자로 그렸다: {painted:?}"
    );
}

#[test]
fn a_row_without_a_ratio_gets_no_bar() {
    // ⛔ 대조군. 막대가 늘 그려지면 위 시험은 아무 일도 안 해도 통과한다 — 그리고 종전
    //    화면(mdir·ncd 등)에 없던 막대가 생기는 것은 회귀다.
    let plain: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "x", "kind": "table", "title": "t", "hint": "h",
        "rows": [{"key": "a", "label": "a", "cols": ["1"]}], "text": "", "note": "",
        "selected": 0
    }))
    .unwrap();
    let fills = painted_fills(vec![layout_one_pane(), plain], &[]);
    assert!(
        !fills.iter().any(|(c, _, _)| *c == theme::INVERT_BG),
        "비율이 없는 줄에 막대를 그렸다: {fills:?}"
    );
}

/// 정본 토큰 팝업의 `[기간]` 탭이 내려오는 모양 — **계층 트리를 실은 `table`**.
///
/// 서버(`screenspec._tree_rows`)는 줄마다 `depth`(월→주→일→시각)와 `expand`
/// (`open`/`shut`/빈 것 = 잎)를 **싣는다**. 잎에 화살표를 안 붙이는 것도 서버의 뜻이다.
fn period_tree_spec() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-period", "kind": "table",
        "title": "토큰 사용량(추정) · 기간별", "hint": "↑↓ 이동 · Enter/←→ 펼침·접힘 · Esc 닫기",
        "rows": [
            {"key": "month:2026-08", "label": "2026-08", "cols": ["4,167"],
             "depth": 0, "expand": "open"},
            {"key": "day:2026-08-31", "label": "08-31(월)", "cols": ["608"],
             "depth": 1, "expand": "open", "bar": 1000},
            {"key": "", "label": "23시", "cols": ["39"], "depth": 2, "expand": "",
             "bar": 400},
            {"key": "day:2026-08-30", "label": "08-30(일)", "cols": ["376"],
             "depth": 1, "expand": "shut", "bar": 600},
        ],
        "text": "", "note": "", "selected": 0
    }))
    .unwrap()
}

#[test]
fn a_table_that_carries_a_tree_is_drawn_as_a_tree_not_flat() {
    // ★ pytmux-419 ③ — 정본 `[기간]` 탭은 월→주→일→시각을 **한 트리**로 보인다.
    //   서버는 `depth`·`expand` 를 싣고 있는데 `"table"` 갈래가 그 둘을 **안 읽어서**
    //   판이 통째로 평면이었다. 같은 자료를 받는 `"list"` 갈래는 오래전부터 읽는다
    //   (pytmux-11 B) — 갈래 하나가 뒤처진 것이지 계약이 없던 것이 아니다.
    //
    //   ⛔ 들여쓰기를 서버가 글자로 넣지 않는 이유는 `"list"` 와 같다: 그러면 `label` 이
    //      더는 자료가 아니게 되고 찾기·복사가 그 공백을 물고 간다.
    let painted = painted_after(vec![layout_one_pane(), period_tree_spec()], &[]);
    let joined = painted.join("\n");
    assert!(
        joined.contains('▾'),
        "펼친 줄에 여는 화살표가 없다 — 표가 트리를 평면으로 그렸다:\n{joined}"
    );
    assert!(
        joined.contains('▸'),
        "접힌 줄에 닫힌 화살표가 없다:\n{joined}"
    );
    // 깊이가 다른 두 줄은 **들여쓰기가 다르다**(그래야 계층이 읽힌다).
    // 월(0) = `"▾ "` · 일(1) = `"  ▾ "` — 앞의 공백 수가 곧 깊이다.
    let prefixes: Vec<&String> = painted
        .iter()
        .filter(|t| t.ends_with("▾ ") || t.ends_with("▸ "))
        .collect();
    assert_eq!(prefixes.len(), 3, "펼침 표시가 셋이 아니다: {painted:?}");
    let indents: Vec<usize> = prefixes
        .iter()
        .map(|t| t.len() - t.trim_start_matches(' ').len())
        .collect();
    assert!(
        indents.contains(&0) && indents.contains(&2),
        "깊이가 들여쓰기로 안 나온다(전부 같은 자리에 붙었다): {prefixes:?}"
    );
    // 시각 행(깊이 2 · 잎)은 화살표가 없어도 **그만큼 들여써져** 있다 — 안 그러면
    // 잎이 부모와 같은 열에 서서 계층이 끊긴다.
    assert!(
        painted.iter().any(|t| t == "    "),
        "잎이 깊이만큼 안 들여써졌다: {painted:?}"
    );
}

#[test]
fn a_leaf_row_in_a_table_gets_no_arrow_that_would_lie() {
    // ⛔ 대조군 — 잎(시각 행)은 눌러도 안 열린다. 거기에 화살표를 붙이면 그 화살표가
    //    거짓말이고, 화살표를 **늘** 붙이면 위 시험은 아무 일도 안 해도 통과한다.
    let plain: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "x", "kind": "table", "title": "t", "hint": "h",
        "rows": [{"key": "a", "label": "leafrow", "cols": ["1"], "depth": 0,
                  "expand": ""}],
        "text": "", "note": "", "selected": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), plain], &[]);
    let joined = painted.join("\n");
    assert!(
        !joined.contains('▾') && !joined.contains('▸'),
        "잎에 화살표를 붙였다 — 눌러도 안 열리는 줄이 열리는 것처럼 보인다:\n{joined}"
    );
}

/// 정본 `[기간]` 탭의 시각 행 — 토큰 칸 + 비율 칸 둘, 그리고 **칸마다의 뜻**.
///
/// 서버(`screenspec._limit_cols`)는 `cols` 와 같은 차례로 `coltags` 를 싣는다. 첫 칸
/// (토큰)은 뜻이 없어 빈 이름이다 — 정본도 그 칸은 한 색이다.
fn period_pct_spec() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-period", "kind": "table",
        "title": "토큰 사용량(추정) · 기간별", "hint": "↑↓ 이동 · Esc 닫기",
        "rows": [
            {"key": "", "label": "09시", "cols": ["39", "9%", "22%"], "depth": 2,
             "expand": "", "coltags": ["", "ok", "ok"]},
            {"key": "", "label": "10시", "cols": ["40", "63%", "22%"], "depth": 2,
             "expand": "", "coltags": ["", "warn", "ok"]},
            {"key": "", "label": "11시", "cols": ["41", "94%", "100%"], "depth": 2,
             "expand": "", "coltags": ["", "crit", "crit"]},
        ],
        "text": "", "note": "", "selected": 0
    }))
    .unwrap()
}

#[test]
fn a_columns_meaning_paints_that_column_and_only_that_column() {
    // ★ pytmux-419 ⑥ — 정본은 `5h%`·`1w%` 를 비율에 따라 초록·노랑·빨강으로 칠한다.
    //   ☠ **줄 태그(`tag`)로는 못 말하는 갈림이다**: 같은 줄 안에서 `Tokens` 칸은 한
    //     색이고 뒤 두 칸만 갈린다. 그래서 서버가 칸마다 이름을 싣고(`coltags`) 여기서
    //     등급으로 푼다(`proto::celltag`).
    //   ⛔ 눈금(≥50·≥80)은 여기 없다 — 정본 `usagehead.pct_level` 한 벌이다.
    let painted = painted_colors(vec![layout_one_pane(), period_pct_spec()], &[]);
    let of = |needle: &str| -> Vec<ColorU> {
        painted.iter().filter(|(t, _)| t == needle).map(|(_, c)| *c).collect()
    };
    assert_eq!(of("9%"), vec![theme::OK], "여유로운 비율이 초록이 아니다: {painted:?}");
    assert_eq!(of("63%"), vec![theme::WARN], "주의 비율이 노랑이 아니다: {painted:?}");
    assert_eq!(of("94%"), vec![theme::ERROR], "위험 비율이 빨강이 아니다: {painted:?}");
    // ★ **그 칸만** 이다 — 토큰 칸까지 물들면 「이 줄이 위험하다」로 읽혀 뜻이 넓어진다.
    assert_eq!(of("41"), vec![palette::DIM], "뜻이 없는 칸까지 칠했다: {painted:?}");
}

#[test]
fn a_column_without_a_meaning_stays_the_plain_colour() {
    // ⛔ 대조군 — 뜻을 안 실은 표(mdir·세션 목록 …)는 종전 그대로다. 이것이 없으면
    //    위 시험은 «칸을 늘 초록으로 칠하는» 판에도 통과한다(그때 `9%` 는 맞고 나머지
    //    둘만 틀리는데, 셋을 다 재는 지금도 그 함정은 값 하나로 좁혀지지 않는다).
    let plain: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "x", "kind": "table", "title": "t", "hint": "h",
        "rows": [{"key": "a", "label": "row", "cols": ["9%", "63%", "94%"],
                  "depth": 0, "expand": ""}],
        "text": "", "note": "", "selected": 0
    }))
    .unwrap();
    let painted = painted_colors(vec![layout_one_pane(), plain], &[]);
    for needle in ["9%", "63%", "94%"] {
        let got: Vec<ColorU> =
            painted.iter().filter(|(t, _)| t == needle).map(|(_, c)| *c).collect();
        assert_eq!(
            got,
            vec![palette::DIM],
            "뜻을 안 실은 칸에 색을 짐작해 칠했다({needle}): {painted:?}"
        );
    }
}

#[test]
fn a_panel_head_is_drawn_above_the_rows_and_costs_a_row_of_budget() {
    // ★ pytmux-419 ② — 정본 토큰 팝업은 다섯 탭이 **머리줄 두 줄을 공유**한다
    //   (`5h 29% · wk 22% · Σ… · 91ddca94 85% · this machine 15% · unattributed 45%`).
    //   GUI 는 판이 여럿이라 판마다 같은 줄이 `spec.head` 로 실려 온다. 종전에는
    //   `"table"` 갈래가 그 칸을 아예 안 읽어서 **그 값이 화면에 없었다**.
    let with_head: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-period", "kind": "table",
        "title": "토큰 사용량", "hint": "h",
        "head": "5h 29% · 주 22% · Σ22641.9M 실측 · 이 머신 15%",
        "rows": [{"key": "a", "label": "행하나", "cols": ["1"]}],
        "text": "", "note": "", "selected": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), with_head], &[]).join("\n");
    assert!(
        painted.contains("5h 29%") && painted.contains("이 머신 15%"),
        "머리줄이 안 그려졌다:\n{painted}"
    );

    // ⛔ 대조군 — `head` 가 비면 **아무 줄도 안 는다**. 늘 그리면 위 단언은 아무 일도 안
    //    해도 통과하고, 종전 화면(mdir·ncd)에 없던 줄이 생기는 것은 회귀다.
    let no_head: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "x", "kind": "table", "title": "t", "hint": "h",
        "rows": [{"key": "a", "label": "행하나", "cols": ["1"]}],
        "text": "", "note": "", "selected": 0
    }))
    .unwrap();
    let bare = painted_after(vec![layout_one_pane(), no_head], &[]);
    let headed = painted_after(vec![layout_one_pane(), {
        let m: ServerMessage = serde_json::from_value(serde_json::json!({
            "t": "plugin_screen", "id": "x", "kind": "table", "title": "t", "hint": "h",
            "head": "머리줄",
            "rows": [{"key": "a", "label": "행하나", "cols": ["1"]}],
            "text": "", "note": "", "selected": 0
        }))
        .unwrap();
        m
    }], &[]);
    assert!(
        !bare.iter().any(|t| t.contains("머리줄")),
        "머리줄이 없는 판에 무언가를 그렸다: {bare:?}"
    );
    assert_eq!(
        headed.len(),
        bare.len() + 1,
        "머리줄이 정확히 한 줄이 아니다 — bare={bare:?} headed={headed:?}"
    );

    // ⛔ 그리고 그 한 줄은 **예산을 먹는다**. 안 떼면 목록이 그만큼 아래로 넘친다 —
    //    줄이 예산보다 적으면 이 차이가 안 드러나므로(실측: 한 줄짜리 판으로는 뮤테이션이
    //    안 물렸다) 예산을 확실히 넘기는 판으로 잰다.
    let many = |head: &str| -> ServerMessage {
        let rows: Vec<_> = (0..200)
            .map(|i| serde_json::json!({"key": format!("k{i}"),
                                        "label": format!("행{i:03}"), "cols": []}))
            .collect();
        serde_json::from_value(serde_json::json!({
            "t": "plugin_screen", "id": "x", "kind": "table", "title": "t", "hint": "h",
            "head": head, "rows": rows, "text": "", "note": "", "selected": 0
        }))
        .unwrap()
    };
    let long_bare = painted_after(vec![layout_one_pane(), many("")], &[]).len();
    let long_head = painted_after(vec![layout_one_pane(), many("머리줄")], &[]).len();
    assert_eq!(
        long_head, long_bare,
        "머리줄이 예산을 안 먹었다 — 목록이 그만큼 아래로 넘친다 \
         (머리줄 없음 {long_bare}줄 · 있음 {long_head}줄)"
    );
}

// ── 빈 목록도 말을 한다 (pytmux-405) ──────────────────────────────────────────────
//
// 정본은 목록이 비면 그 사실을 적는다(`(버퍼 없음)`·`(검색 결과 없음)`·`(지나간 알림 없음)`).
// 아무 말도 없는 빈 상자는 사용자에게 **「고장」과 구별되지 않는다.**
//
// ⛔ 문구를 정본에서 옮기는 것이 목적이 아니다(문구는 GUI 것으로 짓는다) — 재는 것은
//    **「빈 판이 침묵하지 않는다」** 하나다. 그래서 판마다 문구를 박지 않고 전수로 훑는다.

#[test]
fn no_panel_stays_silent_when_it_has_nothing_to_show() {
    // 재료를 하나도 안 준 상태로 판을 전부 열어 본다. 머리줄·꼬리줄은 늘 있으므로
    // 그 둘을 빼고 **본문에 글자가 있나**를 본다.
    let mut silent: Vec<String> = Vec::new();
    for screen in base::screens::Screen::all().iter().copied() {
        // 목록형이 아닌 판(읽는 판·입력 판)은 대상이 아니다 — 비어 있을 수가 없다.
        if !base::screens::Screens::is_list(screen) && screen != base::screens::Screen::Notices {
            continue;
        }
        let (mut view, tx, _sent) = harness();
        tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
        view.pump_headless();
        view.screens.open(screen);
        let Some(panel) = view.render_screen_panel(screen).debug_text_content() else {
            silent.push(format!("{screen:?}(글자 0)"));
            continue;
        };
        let title = screen.title();
        let hint = screen.hint();
        let body: Vec<&str> = panel
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && *l != title && *l != hint && !hint.contains(*l))
            .collect();
        if body.is_empty() {
            silent.push(format!("{screen:?}"));
        }
    }
    assert!(
        silent.is_empty(),
        "재료가 없을 때 **아무 말도 안 하는** 판이 있다 — 빈 상자는 고장과 구별되지 않는다:
  {}",
        silent.join("
  ")
    );
}

// ── 레터박스 (pytmux-381) ─────────────────────────────────────────────────────────

#[test]
fn a_window_bigger_than_the_shared_grid_gets_a_matte_band() {
    // 제보의 그림 — 두 클라가 같은 탭을 볼 때 GUI 오른쪽·아래에 창 바탕이 드러났다.
    // 정본은 그 자리를 무광으로 칠한다(`clientio.py::_composite` 의 matte 띠).
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    // 공유 격자는 80x4(`layout_one_pane`)인데 내 창은 그보다 크다고 알린다.
    view.size.update(100, 10);
    let canvas = view.composite_for_paint().expect("캔버스가 없다");
    let matte = view.letterbox(&canvas).expect("레터박스가 안 섰다");
    assert_eq!((matte.live_cols, matte.live_rows), (80, 4), "라이브 경계가 격자와 다르다");
    assert_eq!((matte.cols, matte.rows), (100, 10), "띠가 내 창을 안 덮는다");
    assert_eq!(matte.color, theme::MATTE);
}

#[test]
fn the_usual_single_client_gets_no_band() {
    // ⛔ 대조군. 단일 클라(정상 경로)에서 발동하면 평소 그림이 바뀐다 — 정본도 그때는
    //    두 값이 같아 안 칠한다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.size.update(80, 4);
    let canvas = view.composite_for_paint().expect("캔버스가 없다");
    assert!(view.letterbox(&canvas).is_none(), "격자와 같은 크기인데 띠를 세웠다");
    // 더 작은 창(공유 격자가 나보다 크다)에서도 안 칠한다 — 그때는 잘리는 쪽이다.
    view.size.update(40, 2);
    assert!(view.letterbox(&canvas).is_none(), "내가 더 작은데 띠를 세웠다");
}

// ── 고정(핀) 구역 (pytmux-62) ─────────────────────────────────────────────────────

fn tabs_with_a_pinned_one() -> ServerMessage {
    serde_json::from_value(serde_json::json!({"t": "status", "windows": [
        {"index": 0, "name": "고정", "pinned": true},
        {"index": 1, "name": "하나", "active": true},
        {"index": 2, "name": "둘"},
    ]}))
    .unwrap()
}

#[test]
fn pinned_tabs_gather_behind_a_separator() {
    // 제보(2026-08-24): 정본은 고정 탭을 뒤 구역으로 모으는데 GUI 는 제자리에 두어
    // 「핀이 반영되지 않는다」로 보였다. `*` 글리프는 오고 있었다 — 없던 것은 **자리**다.
    let painted = painted_after(vec![layout_one_pane(), tabs_with_a_pinned_one()], &[]);
    let at = |needle: &str| {
        painted
            .iter()
            .position(|t| t.contains(needle))
            .unwrap_or_else(|| panic!("프레임에 {needle:?} 가 없다: {painted:?}"))
    };
    assert!(at("하나") < at("‖"), "비고정 탭이 구분자 뒤로 갔다: {painted:?}");
    assert!(at("둘") < at("‖"), "비고정 탭이 구분자 뒤로 갔다: {painted:?}");
    assert!(at("‖") < at("고정"), "고정 탭이 구분자 앞에 남았다: {painted:?}");
    // `+` 는 정본 차례대로 **고정 구역 앞**이다.
    assert!(at("+") < at("‖"), "새 탭 단추가 고정 구역 뒤로 갔다: {painted:?}");
}

#[test]
fn without_a_pinned_tab_there_is_no_separator() {
    // 대조군 — 구분자가 늘 있으면 위 시험은 아무 일도 안 해도 통과한다.
    let painted = painted_after(three_tabs(), &[]);
    assert!(
        !painted.iter().any(|t| t.contains('‖')),
        "고정 탭이 없는데 구분자를 그렸다: {painted:?}"
    );
    // 그리고 `+` 는 그때도 그려진다(정본 탭바도 늘 달고 있다).
    assert!(painted.iter().any(|t| t.contains('+')), "새 탭 단추가 사라졌다: {painted:?}");
}

// ── esc 모드 표식 (pytmux-380) ────────────────────────────────────────────────────

#[test]
fn the_command_mode_badge_says_what_it_can_do() {
    // `[esc]` 네 글자는 "무언가 모드에 들어와 있다"까지만 말한다. 정본은 같은 자리에서
    // 나가는 길·쓰는 길을 광고한다.
    let badge = InputMode::Command.badge().expect("표식이 없다");
    assert!(badge.contains('←') && badge.contains(':'), "할 일을 안 적었다: {badge}");
    assert_ne!(badge, "[esc]");
}

#[test]
fn the_command_mode_badge_is_translated() {
    // 종전 `[esc]` 는 기호라 카탈로그에 줄이 없었고 en 로케일에서도 그대로 나왔다.
    let badge = InputMode::Command.badge().unwrap();
    let en = base::i18n::with_locale("en", || base::i18n::t(badge));
    assert_ne!(en, badge, "en 카탈로그에 줄이 없다");
    assert!(en.contains("cmd"), "정본 en 문구와 다르다: {en}");
}

// ── 판이 서는 자리 — 정본 앵커(`Screen::anchor`)를 뷰가 실제로 따르나 ────────────
//
// core 가 앵커를 들고 있어도 뷰가 그것을 **안 보면** 아무 일도 안 일어난다. 적합성
// 테스트(`screen_anchor_conformance.rs`)는 core 의 표만 재므로, 그리는 자리는 여기서
// 잰다 — 이 저장소가 두 번 밟은 "값은 맞는데 붙이는 호출이 없다" 부류다.

/// ★ 이 오라클이 먼저다 — 세로 좌표가 안 살아 있으면 아래 배치 단언이 전부 공허하다.
#[test]
fn the_bounds_oracle_sees_vertical_positions() {
    let boxes = painted_boxes(vec![], &[]);
    assert!(!boxes.is_empty(), "그려진 글자가 없다 — 기록 장치가 죽었다");
    let ys: Vec<f32> = boxes.iter().map(|(_, y)| *y).collect();
    assert!(
        ys.iter().any(|y| *y > 0.0),
        "세로 좌표가 전부 0이다 — 이 오라클로는 배치를 못 잰다: {boxes:?}"
    );
}

/// `esc :` 프롬프트는 **바닥**이다(정본 `PromptScreen { align: center bottom }`).
///
/// 손과 눈이 방금 `:` 를 친 화면 아래에 있는데 판이 가운데 뜨면 시선이 한 번 튄다 —
/// 사용자가 두 달을 쓰며 굳힌 자리다(지시 2026-08-01).
#[test]
fn the_command_prompt_sits_at_the_bottom() {
    let boxes = painted_boxes(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)],
    );
    let y = painted_y(&boxes, ">").unwrap_or_else(|| panic!("입력 줄이 안 그려졌다: {boxes:?}"));
    assert!(
        y > 300.0,
        "프롬프트가 화면 위쪽(y={y})에 떴다 — 정본은 바닥이다: {boxes:?}"
    );
}

/// 읽는 판(버전)은 **위**다(정본 `InfoScreen { align: center top }`).
///
/// 긴 글이라 첫 줄이 늘 같은 자리라야 훑을 수 있다. 프롬프트(바닥)와 **같은 프레임 크기
/// 에서** 재야 뜻이 있다 — 그래서 두 y 를 서로 비교한다(절대 좌표가 아니라 **차이**가
/// 배치를 말한다).
#[test]
fn a_reading_screen_starts_above_a_typing_screen() {
    let version = painted_boxes(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)],
    );
    let prompt_y = painted_y(&version, ">").unwrap_or_else(|| panic!("입력 줄이 없다: {version:?}"));
    // 읽는 판은 `esc ?`(키 도움말 — 정본이 InfoScreen 으로 띄우는 그 부류)로 연다.
    let keys = painted_boxes(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char('?'), Mods::NONE)],
    );
    let keys_y = painted_y(&keys, t("키 도움말"))
        .unwrap_or_else(|| panic!("키 도움말 제목이 안 그려졌다: {keys:?}"));
    assert!(
        keys_y < prompt_y,
        "읽는 판(y={keys_y})이 치는 판(y={prompt_y})보다 아래에 떴다"
    );
}

/// `esc :` 판의 **입력 줄이 목록 아래**다 — 곧 화면 맨 밑이다.
///
/// 사용자 지시(2026-08-01): "터미널에서 프롬프트가 보통 화면 하단에 있어 시선이 하단에
/// 가 있기 때문"에 정본은 입력 박스를 바닥에 뒀다. 우리 팔레트는 정본의 `:` 입력과
/// 명령 목록 **둘의 역할을 겸하므로** 판 안에서 같은 기하를 만들어야 한다.
#[test]
fn the_command_input_sits_below_the_list() {
    let boxes = painted_boxes(
        vec![layout_one_pane()],
        &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)],
    );
    let input_y = painted_y(&boxes, ">").unwrap_or_else(|| panic!("입력 줄이 없다: {boxes:?}"));
    let list_y = painted_y(&boxes, "split-window")
        .unwrap_or_else(|| panic!("명령 목록이 없다: {boxes:?}"));
    assert!(
        input_y > list_y,
        "입력(y={input_y})이 목록(y={list_y})보다 위에 있다 — 프롬프트는 아래다: {boxes:?}"
    );
}

// ── 플러그인 표면(Tier A) — 서버가 준 기여가 실제로 화면에 뜨나 ──────────────────
//
// 설계 = docs/internal/PLUGIN_COMPAT_TEXTUAL_GUI_2026-08-01.md §4.1.
// 상태에 값이 들어오는 것만 재면 **그리는 호출을 지워도 통과한다**(이 저장소가 두 번
// 밟은 공허 통과) — 그래서 그려진 글자로 잰다.

fn status_with_plugin_surface() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [{"name": "mdir", "desc": "파일 관리자", "cat": "설정/기타"}],
            "noarg": ["mdir"],
            "menu_items": [{"key": "mdir", "label": "파일 관리자 ▤"}],
            "settings": [],
            "setting_cats": []
        }
    }))
    .unwrap()
}

#[test]
fn plugin_commands_show_up_in_the_palette() {
    // 이 명령은 코어 표(`base::PALETTE`)에 **없다** — 서버가 준 것이라야 뜬다.
    assert!(
        !base::PALETTE.iter().any(|e| e.name == "mdir"),
        "코어 표에 mdir 이 있으면 이 오라클은 아무것도 안 잰다"
    );
    // 이름을 쳐서 좁힌다 — 코어 명령이 87개라 안 좁히면 판 높이 예산에 밀려 안 보인다
    // (그건 이 오라클이 재려는 것이 아니다).
    let painted = painted_after(
        vec![layout_one_pane(), status_with_plugin_surface()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Char(':'), Mods::NONE),
            (Key::Char('m'), Mods::NONE),
            (Key::Char('d'), Mods::NONE),
            (Key::Char('i'), Mods::NONE),
            (Key::Char('r'), Mods::NONE),
        ],
    );
    assert!(
        painted_contains(&painted, "mdir"),
        "플러그인 명령이 팔레트에 안 떴다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "파일 관리자"),
        "설명이 서버가 준 글과 다르다: {painted:?}"
    );
}

/// 서버가 준 메뉴 줄이 **메뉴 계층 안에** 뜨나(설계 P2).
///
/// 종전에는 이 두 줄이 정적 표에 손으로 적혀 있었다 — 그러면 서버가 그 플러그인을 안
/// 실어도 화면에 남고(delete-to-disable 이 우리 쪽에서만 거짓), 새 플러그인이 낸 줄은
/// 영영 안 뜬다.
#[test]
fn plugin_menu_rows_come_from_the_server() {
    let painted = painted_after(
        vec![layout_one_pane(), status_with_plugin_surface()],
        // 메뉴는 `prefix Enter` 다(esc 모드의 Enter 는 다른 표다 — `BINDINGS`).
        &[(Key::Char('b'), Mods::CTRL), (Key::Enter, Mods::NONE)],
    );
    assert!(
        painted_contains(&painted, "플러그인  ›"),
        "최상위에 플러그인 그룹이 없다: {painted:?}"
    );

    // 들어가면 **서버가 준 문구**가 보인다(정적 표에는 없는 글이다).
    let inside = painted_after(
        vec![layout_one_pane(), status_with_plugin_surface()],
        &[
            (Key::Char('b'), Mods::CTRL),
            (Key::Enter, Mods::NONE),
            // 패널▸ 레이아웃▸ 탭▸ **플러그인▸** — 정본이 끼우는 자리가 `탭` 다음이다.
            (Key::Down, Mods::NONE),
            (Key::Down, Mods::NONE),
            (Key::Down, Mods::NONE),
            (Key::Right, Mods::NONE),
        ],
    );
    assert!(
        painted_contains(&inside, "파일 관리자 ▤"),
        "플러그인 서브메뉴에 서버가 준 줄이 없다: {inside:?}"
    );
}

/// 기여가 없으면 **그룹 자체가 없다**(delete-to-disable 이 화면에서도 먹는다).
#[test]
fn no_plugin_contributions_means_no_plugin_group() {
    let painted = painted_after(
        vec![layout_one_pane()],
        &[(Key::Char('b'), Mods::CTRL), (Key::Enter, Mods::NONE)],
    );
    assert!(
        !painted_contains(&painted, "플러그인  ›"),
        "기여가 하나도 없는데 플러그인 그룹이 떴다: {painted:?}"
    );
    // 그리고 다른 그룹은 그대로다 — 이 오라클이 "메뉴가 안 열렸다"로 헛통과하지 않게.
    assert!(painted_contains(&painted, "탭  ›"), "메뉴가 안 열렸다: {painted:?}");
}

/// 서버가 준 설정 줄과 **그 분류 탭**이 설정 화면에 서나(설계 P2).
#[test]
fn plugin_settings_show_up_with_their_own_sidebar_tab() {
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [],
            "noarg": [],
            "menu_items": [],
            "settings": [{"key": "claude-rules", "cat": "Claude", "type": "link"}],
            "setting_cats": ["Claude"]
        }
    }))
    .unwrap();
    // 설정 화면은 팔레트로 연다(전용 키가 없다). 그 뒤 **마지막 분류**로 간다 —
    // 코어 다섯 뒤가 플러그인 분류다.
    let mut keys = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("settings".chars().map(|c| (Key::Char(c), Mods::NONE)));
    keys.push((Key::Enter, Mods::NONE));
    keys.extend(std::iter::repeat_n((Key::Tab, Mods::NONE), 5));
    let painted = painted_after(vec![layout_one_pane(), status], &keys);
    assert!(
        painted_contains(&painted, "Claude"),
        "플러그인 분류 탭이 사이드바에 없다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "Claude 시작 규칙…"),
        "플러그인 설정 줄이 화면에 없다: {painted:?}"
    );
}

/// 우리가 **네이티브로 든 이름**은 팔레트에 두 번 서지 않는다(P1 이 두 줄로 만들었다).
#[test]
fn a_natively_handled_plugin_command_is_not_listed_twice() {
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [{"name": "clock-mode", "desc": "서버가 준 설명", "cat": "설정/기타"}],
            "noarg": ["clock-mode"], "menu_items": [], "settings": [], "setting_cats": []
        }
    }))
    .unwrap();
    let painted = painted_after(
        vec![layout_one_pane(), status],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Char(':'), Mods::NONE),
            (Key::Char('c'), Mods::NONE),
            (Key::Char('l'), Mods::NONE),
            (Key::Char('o'), Mods::NONE),
            (Key::Char('c'), Mods::NONE),
            (Key::Char('k'), Mods::NONE),
        ],
    );
    // ⚠ 한 줄이 **세 칸**으로 갈려 그려진다(§10-21ⓞ) — 이름 칸은 그 이름 하나뿐이다.
    //   종전처럼 `"clock-mode "` 로 시작하는 **한 덩이**를 찾으면 이제 아무것도 안 걸려
    //   단언이 뜻을 잃는다(칸을 나눈 CL 에서 여기가 먼저 울었다).
    let rows = painted.iter().filter(|piece| piece.as_str() == "clock-mode").count();
    assert_eq!(rows, 1, "같은 이름이 팔레트에 두 줄 섰다: {painted:?}");
}

#[test]
fn a_frame_without_the_surface_keeps_the_previous_one() {
    // 델타에는 이 키가 없다. 그때 목록을 지우면 플러그인 기여가 **매 틱 깜빡인다**.
    let mut state = SessionState::new();
    state.apply(status_with_plugin_surface());
    assert_eq!(state.plugin_surface().commands.len(), 1);
    let delta: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status", "windows": [{"index": 0, "name": "하나", "active": true}]
    }))
    .unwrap();
    state.apply(delta);
    assert_eq!(
        state.plugin_surface().commands.len(),
        1,
        "델타 한 장에 플러그인 기여가 사라졌다"
    );
}

// ── G1 측정 — 이 **뷰**가 액션마다 실제로 무엇을 하나 ────────────────────────────
//
// 왜 이 오라클이 필요한가(`CLIENT_PRODUCT_SET_2026-08-01.md` §4-G1):
//
// 패리티 래칫의 칸은 2026-08-01 에 **GUI 하나**가 됐는데, 값은 *정본 대 Rust 쪽 아무 뷰*
// 시절 것을 물려받았다 — 즉 지워진 TUI 에만 있던 `Done` 이 섞여 있을 수 있고, 그 상태로는
// 표의 숫자가 "GUI 가 다 된다"는 **거짓말**이 된다. 표를 눈으로 다시 훑는 것은 189줄짜리
// 손번역이라 같은 부류의 부채를 하나 더 만든다.
//
// 그래서 기계로 잰다: **액션 전수**(`base::keymap::all_actions`)를 이 뷰에 먹이고, 뷰가
// 아무 일도 안 하는 것을 센다. `apply_action` 은 화면을 열거나 명령을 밀면 `true` 이고,
// 둘 다 아니면 `false` 다 — 그 `false` 가 곧 "이 클라에서는 없는 기능"이다.
//
// 예외 목록이 이 측정의 **결과**다. 목록이 이유와 함께 정확·정렬이라야 하므로, 새로 죽는
// 액션이 생기면 같은 CL 에서 여기 적히거나 고쳐진다.

/// 뷰가 **아무 일도 안 하는** 액션과 그 이유.
///
/// 셋 다 "GUI 가 못 한다"가 아니라 **다른 입구로만 뜻이 생긴다**는 뜻이다. 그래도 목록에
/// 두는 이유는 하나다: 여기 없으면 조용히 죽은 액션이 늘어난다.
static NO_OP_ACTIONS: &[(&str, &str)] = &[
    ("EnterScroll", "액션이 아니라 **모드 전이**로 처리된다(`KeyOutcome::ModeChanged`) — \
     서버 명령으로 옮기면 뷰마다 다르게 해석할 여지가 생긴다(`keys.rs` 주석)"),
    ("ToggleExpand", "블록 목록 데모 뷰의 것 — 세션 뷰에는 펼칠 목록이 없다"),
];

#[test]
fn every_action_does_something_in_this_view() {
    let mut dead: Vec<String> = Vec::new();
    for action in base::keymap::all_actions() {
        // 탭 셋짜리 세션으로 세운다 — `SelectTab(3)` 처럼 **대상이 있어야** 명령이 나는
        // 액션을 하네스 빈곤 때문에 "죽었다"로 세지 않으려는 것이다.
        let (mut view, tx, _sent) = harness();
        for msg in three_tabs() {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        if !view.apply_action_for_test(action) {
            // 이름만 견준다 — `SelectTab(3)` 처럼 값을 든 액션은 값이 뜻이 아니다.
            dead.push(format!("{action:?}").split('(').next().unwrap_or("?").to_owned());
        }
    }
    dead.sort();
    dead.dedup();
    let known: Vec<String> =
        NO_OP_ACTIONS.iter().map(|(name, _)| (*name).to_owned()).collect();
    let mut sorted = known.clone();
    sorted.sort();
    assert_eq!(known, sorted, "NO_OP_ACTIONS 는 이름순이라야 한다");
    assert_eq!(
        dead, sorted,
        "이 뷰가 아무 일도 안 하는 액션이 달라졌다.\n\
         늘었다면 그 액션은 **이 클라에서 없는 기능**이다 — 배선하거나, 왜 다른 입구로만\n\
         뜻이 생기는지를 NO_OP_ACTIONS 에 적을 것(패리티 표의 Done 도 함께 볼 것)."
    );
}

// ── 화면 축 측정(2026-08-02p) — 액션 축(G1)을 **화면**으로 넓힌다 ────────────────
//
// 패리티 표의 화면 칸은 17줄인데 그 값도 손번역이었다(`parity.rs` Item 주석의 *"설정
// 36·화면 17 축은 아직 같은 강도로 안 쟀다"*). 여기서 재는 것은 액션 축과 같은 질문의
// 화면판이다: **이 화면을 열 길이 있나.**
//
// 그리기는 안 잰다 — `render_screen_panel` 이 와일드카드 없는 `match` 라 변형이 늘면
// 컴파일러가 먼저 운다. 반면 "여는 길"은 아무도 안 지킨다: 화면 하나를 enum 에 남긴 채
// 그것을 여는 액션만 지우면 표의 수는 그대로이고 사용자는 그 화면을 영영 못 본다.

/// 액션으로는 못 여는 화면과 **무엇이 여는가**.
///
/// 전부 "GUI 에 없다"가 아니라 **서버 회신이나 다른 화면이 연다**는 뜻이다. 목록에
/// 두는 이유는 액션 축과 같다: 여기 없으면 조용히 못 여는 화면이 늘어난다.
/// ⚠ 이 셋은 **잰 값이다.** 처음에는 여덟을 적었는데(물음·확인·인자 화면 따위) 재
/// 보니 다섯은 액션이 이미 연다 — 손으로 적은 목록이 그만큼 틀렸다는 뜻이고, 이 축을
/// 기계로 재는 이유가 그것이다.
static NOT_OPENED_BY_AN_ACTION: &[(&str, &str)] = &[
    ("MergeRemote", "원격 탭 **목록이 와야** 고를 것이 있다(서버 회신이 연다)"),
    ("PluginView", "플러그인이 준 스펙(`plugin_screen`)이 연다"),
    (
        "SearchResults",
        "`Action::SearchAll` 은 **물음**(`Prompt::SearchAll`)만 연다 — 결과 판은 \
         `search_results` 회신이 오고 그 회신을 기다리고 있을 때만 연다(pytmux-27, \
         정본 `_want_search_all` 과 같은 게이트)",
    ),
    ("ShellOutput", "`run-shell` 의 **결과가 온 뒤에** 열린다"),
];

#[test]
fn every_screen_has_a_way_to_open_it() {
    // 액션 전수를 먹여 **열린 화면의 집합**을 모은다(G1 과 같은 하네스·같은 세션).
    let mut opened: Vec<String> = Vec::new();
    for action in base::keymap::all_actions() {
        let (mut view, tx, _sent) = harness();
        for msg in three_tabs() {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        view.apply_action_for_test(action);
        if let Some(screen) = view.screens.top() {
            opened.push(format!("{screen:?}"));
        }
    }
    let mut unreachable: Vec<String> = base::screens::Screen::all()
        .iter()
        .map(|s| format!("{s:?}"))
        .filter(|name| !opened.contains(name))
        .collect();
    unreachable.sort();
    let mut known: Vec<String> =
        NOT_OPENED_BY_AN_ACTION.iter().map(|(n, _)| (*n).to_owned()).collect();
    known.sort();
    assert_eq!(
        known,
        NOT_OPENED_BY_AN_ACTION.iter().map(|(n, _)| (*n).to_owned()).collect::<Vec<_>>(),
        "NOT_OPENED_BY_AN_ACTION 은 이름순이라야 한다"
    );
    assert_eq!(
        unreachable, known,
        "액션으로 열리는 화면이 달라졌다.\n\
         늘었다면 그 화면은 **이 클라에서 열 길이 없다** — 액션을 배선하거나, 무엇이 여는지를\n\
         NOT_OPENED_BY_AN_ACTION 에 적을 것(패리티 표의 화면 칸도 함께 볼 것)."
    );
    // 빈 결과가 통과로 보이지 않게 — 아무 화면도 안 열렸다면 위 비교는 공허하다.
    assert!(opened.len() > 5, "액션이 연 화면이 너무 적다: {opened:?}");
}

// ── 플러그인 화면(Tier C · P4) — 스펙이 판이 되고, 고른 줄이 서버로 돌아간다 ────────

fn plugin_list_screen() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "p4changes", "kind": "list",
        "title": "Perforce submitted changelists",
        "hint": "(↑↓ 이동 · Enter 상세 · Esc 닫기)",
        "rows": [
            {"key": "68995", "label": "68995  플러그인 호환 P2", "cols": ["woojinkim", "08/01"]},
            {"key": "68997", "label": "68997  열린 항목 둘", "cols": ["woojinkim", "08/01"]}
        ],
        "selected": 0, "note": "", "keys": {"enter": "describe"}
    }))
    .unwrap()
}

#[test]
fn a_plugin_screen_spec_becomes_a_panel() {
    let painted = painted_after(vec![layout_one_pane(), plugin_list_screen()], &[]);
    assert!(
        painted_contains(&painted, "68995  플러그인 호환 P2"),
        "서버가 준 목록이 안 그려졌다: {painted:?}"
    );
    // 부가 칸도 그린다(정본 목록 화면과 같은 짜임).
    assert!(painted_contains(&painted, "woojinkim"), "부가 칸이 없다: {painted:?}");
}

/// 판의 **제목과 안내는 스펙의 것**이다 — 어느 판을 열었는지 화면이 말해야 한다.
///
/// P4~P6 동안 여기가 빠져 있어 `mdir`·`ncd`·`p4changes` 가 전부 `플러그인 화면` 이라는
/// 한 제목으로 떴다(`base::screens` 주석은 "뷰가 스펙으로 덮어 그린다"고 적어 뒀는데
/// 그 덮는 자리가 없었다 — 2026-08-02 라이브에서 드러났다).
#[test]
fn the_spec_names_the_panel_not_us() {
    let painted = painted_after(vec![layout_one_pane(), plugin_list_screen()], &[]);
    assert!(
        painted_contains(&painted, "Perforce submitted changelists"),
        "스펙 제목이 안 보인다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "Enter 상세"),
        "스펙 안내가 안 보인다: {painted:?}"
    );
    assert!(
        !painted_contains(&painted, "플러그인 화면"),
        "폴백 제목이 스펙을 덮었다: {painted:?}"
    );
}

#[test]
fn choosing_a_row_sends_back_its_key_not_its_position() {
    // 자리(번호)만 보내면 목록이 바뀔 때 엉뚱한 줄이 열린다 — 그 줄의 **뜻**을 보낸다.
    let sent = sent_after(
        vec![layout_one_pane(), plugin_list_screen()],
        &[(Key::Down, Mods::NONE), (Key::Enter, Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("plugin_action 이 안 나갔다: {frames:?}"));
    assert_eq!(action["id"], "p4changes");
    // 액션 이름의 칸은 `do` 다 — `action` 은 명령 디스패처의 것이다.
    assert_eq!(action["do"], "describe");
    assert_eq!(action["input"], "68997", "고른 줄의 key 가 아니다: {action:?}");
}

#[test]
fn a_plugin_command_goes_to_the_server_by_name_not_by_our_guess() {
    // ★ 계약이 바뀌었다(pytmux-35). P1~P2 에서는 알림만 남았고, 그 뒤로는 **전부**
    //   `plugin_open`("화면을 다오")이었다 — 그래서 상태를 바꾸는 명령이 통째로 죽어
    //   있었다(팔레트에 보이는데 안 먹는 줄 열여덟).
    //
    //   이제 **이름만 보낸다**(`plugin_cmd`). 어느 갈래인지는 플러그인이 알고, 그 표를
    //   우리가 들면 서버와 갈린다 — 갈린 순간 명령은 조용히 죽는다.
    //   화면인 이름(`mdir`)도 같은 길로 간다: 서버가 화면 경로로 넘어간다.
    let sent = sent_after(
        vec![layout_one_pane(), status_with_plugin_surface()],
        &[
            (Key::Escape, Mods::NONE),
            (Key::Char(':'), Mods::NONE),
            (Key::Char('m'), Mods::NONE),
            (Key::Char('d'), Mods::NONE),
            (Key::Char('i'), Mods::NONE),
            (Key::Char('r'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let open = frames
        .iter()
        .find(|f| f["action"] == "plugin_cmd")
        .unwrap_or_else(|| panic!("plugin_cmd 가 안 나갔다: {frames:?}"));
    assert_eq!(open["name"], "mdir");
    // ⚠ **우리가 갈래를 정하지 않는다**는 것이 이 오라클의 요점이다 — 화면인 이름에도
    //    `plugin_open` 을 직접 치지 않는다(치면 상태형 이름에서 다시 죽는다).
    assert!(
        !frames.iter().any(|f| f["action"] == "plugin_open"),
        "갈래를 클라가 정했다: {frames:?}"
    );
}

#[test]
fn esc_from_the_detail_goes_back_to_the_list_without_asking_the_server() {
    // 상세에서 한 판 물러나면 **방금 보던 목록**이 그대로 있어야 한다(서버에 다시 물으면
    // p4 를 또 부르고 그 사이 목록이 달라진다).
    let detail: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "p4changes", "kind": "text",
        "title": "CL 68995", "hint": "(↑↓ 스크롤 · Esc 목록으로)",
        "text": "Change 68995 by woojinkim\n\t플러그인 호환 P2", "note": "", "keys": {}
    }))
    .unwrap();
    let painted = painted_after(
        vec![layout_one_pane(), plugin_list_screen(), detail],
        &[(Key::Escape, Mods::NONE)],
    );
    assert!(
        painted_contains(&painted, "68995  플러그인 호환 P2"),
        "Esc 뒤 목록으로 안 돌아왔다: {painted:?}"
    );
}

/// 끊김 사유를 받아 적는 로거 — 아래 한 시험만 쓴다(pytmux-390).
///
/// `log` 의 전역 로거는 **프로세스에 하나뿐**이라 걸기는 한 번뿐이다. 그래서 받아 적기만
/// 하고 판정은 시험이 한다 — 다른 시험이 뱉은 줄이 섞여도 사유 글로 골라내면 된다.
static ENDED_RECORDS: std::sync::Mutex<Vec<(log::Level, String)>> =
    std::sync::Mutex::new(Vec::new());

struct CaptureLog;

impl log::Log for CaptureLog {
    fn enabled(&self, _meta: &log::Metadata) -> bool {
        true
    }
    fn log(&self, record: &log::Record) {
        if let Ok(mut out) = ENDED_RECORDS.lock() {
            out.push((record.level(), record.args().to_string()));
        }
    }
    fn flush(&self) {}
}

#[test]
fn the_reason_a_link_ended_reaches_the_log_not_just_the_screen() {
    // ⛔ 이 자가 없으면 리팩터가 `log::error!` 를 지우거나 `debug!` 로 내려도 **아무 시험이
    //    안 운다**(pytmux-390). 그러면 증상은 오류가 아니라 **침묵으로 되돌아가는 것**이라,
    //    다음 사람은 「로그가 없네」를 「안 끊겼네」로 읽는다 — 이 저장소가 pytmux-171 에서
    //    한 번 치른 값이다(제보자는 `--frame-dump` 그림에서 눈으로 보고서야 알았다).
    //
    // 재는 것 셋: ⑴ 레코드가 났나 ⑵ 수준이 `Error` 인가(기본 로그 수준에서도 보여야
    // 한다) ⑶ **사유 문자열이 실렸나**(사유 없는 "끊겼다"는 진단이 아니다).
    static CAPTURE: CaptureLog = CaptureLog;
    log::set_logger(&CAPTURE)
        .expect("전역 로거를 못 걸었다 — 이 자는 «못 쟀다»이지 통과가 아니다");
    log::set_max_level(log::LevelFilter::Trace);

    let (mut view, tx, _sent) = harness();
    // 실제로 났던 사유 그대로(pytmux-171 의 길이 프레임 오독).
    let reason = "Frame too large: 1684217948 bytes (limit 67108864)";
    tx.send(LinkEvent::Ended(reason.into())).unwrap();
    view.pump_headless();

    let records = ENDED_RECORDS.lock().unwrap().clone();
    let hit = records
        .iter()
        .find(|(_, msg)| msg.contains("1684217948"))
        .unwrap_or_else(|| {
            panic!("끊김 사유가 로그에 안 남았다 — 화면 밖에는 자취가 없다: {records:?}")
        });
    assert_eq!(
        hit.0,
        log::Level::Error,
        "끊김이 기본 로그 수준 아래로 내려갔다 — `RUST_LOG` 를 미리 켠 사람만 본다: {hit:?}"
    );
}

#[test]
fn the_text_panel_stops_scrolling_at_the_end_instead_of_running_away() {
    // 제보(pytmux-184 ⑵ · 스크린샷 3장): `/usage` 팝업에서 아래 방향키를 계속 누르면
    // 내용이 사라지고 빈 칸만 남았다. 그리는 쪽이 `min(max_scroll)` 을 매기면서
    // **판이 비는 것**은 멎었지만, core 의 `scroll` 은 그대로 자란다 — 그러면 끝에서
    // 더 누른 만큼 `↑` 가 헛돈다(그림은 그대로인데 키가 안 먹는 것으로 보인다).
    //
    // ⛔ 재는 것은 「끝에서 ↑ 한 번이 곧바로 한 줄을 되돌리나」다. 값을 안 보고 **그림**을
    //    보는 이유: 자르는 자리가 어디든(뷰든 core 든) 사용자가 겪는 것은 이 한 가지다.
    let body: String = (1..=40).map(|n| format!("L{n:02}
")).collect();
    let long: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-usage-view", "kind": "text",
        "title": "Claude usage limit (/usage)", "hint": "(↑↓ 스크롤 · Esc 닫기)",
        "text": body, "note": "", "keys": {}
    }))
    .unwrap();
    // 끝을 한참 넘겨 내려간다(PgDn 한 번이 10줄 · 판 예산은 그보다 작다).
    let deep: Vec<(Key, Mods)> = std::iter::repeat_n((Key::PageDown, Mods::NONE), 10).collect();
    let mut deep_then_up = deep.clone();
    deep_then_up.push((Key::Up, Mods::NONE));

    let at_end = painted_after(vec![layout_one_pane(), long.clone()], &deep);
    let after_up = painted_after(vec![layout_one_pane(), long], &deep_then_up);

    assert!(
        at_end.iter().any(|t| t.contains("L40")),
        "끝까지 내려갔는데 마지막 줄이 안 보인다 — 판이 비었거나 상한이 틀렸다: {at_end:?}"
    );
    assert_ne!(
        at_end, after_up,
        "끝에서 `↑` 가 헛돌았다 — 넘긴 만큼 눌러야 움직인다(pytmux-184 ⑵)"
    );
}

#[test]
fn a_text_panel_that_fits_does_not_move_when_you_press_down() {
    // ⛔ **제보의 화면은 이것이었다**(pytmux-184 ⑵ · 스크린샷 3장): *"내용이 화면 안에 다
    //    들어가 스크롤이 필요 없는데도 아래 방향키를 누르면 스크롤되어 내용이 사라지고
    //    빈 칸만 남는다."* 형제 오라클
    //    (`the_text_panel_stops_scrolling_at_the_end_instead_of_running_away`)은 **긴 글**로
    //    「끝에서 ↑ 가 헛도나」를 재는데, 그것은 제보의 화면이 아니다 — 그 판은 애초에
    //    끝이 없다(다 보인다). 상한이 `lines - budget` 이라 그 값이 0 으로 포화되는지는
    //    **짧은 글로만** 재진다.
    //
    // ⛔ 값이 아니라 **그림**을 본다(형제와 같은 이유): 자르는 자리가 뷰든 core 든
    //    사용자가 겪는 것은 「눌렀는데 화면이 달아나나」 하나다.
    let body: String = (1..=4).map(|n| format!("줄{n}\n")).collect();
    let short: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-usage-view", "kind": "text",
        "title": "Claude usage limit (/usage)", "hint": "(↑↓ 스크롤 · Esc 닫기)",
        "text": body, "note": "", "keys": {}
    }))
    .unwrap();

    let rest = painted_after(vec![layout_one_pane(), short.clone()], &[]);
    let downs: Vec<(Key, Mods)> = std::iter::repeat_n((Key::Down, Mods::NONE), 12)
        .chain(std::iter::repeat_n((Key::PageDown, Mods::NONE), 3))
        .collect();
    let after = painted_after(vec![layout_one_pane(), short], &downs);

    assert!(
        rest.iter().any(|t| t.contains("줄4")),
        "이 오라클의 전제가 깨졌다 — 짧은 글이 판 안에 다 안 들어간다: {rest:?}"
    );
    assert_eq!(
        rest, after,
        "다 보이는 판이 ↓·PgDn 에 움직였다 — 제보의 그 화면이다(pytmux-184 ⑵)"
    );
}

#[test]
fn opening_debug_stats_asks_the_server_for_its_half() {
    // pytmux-382 — 서버 절반은 **물어야 온다**. 판을 여는 것이 곧 `debug_stats` 명령이다.
    // 이 오라클이 없으면 「판은 뜨는데 서버 줄이 영영 안 온다」가 조용히 통과한다
    // (호출부 오라클 — 값 만드는 함수만 재면 '호출 제거' 변이에 공허 통과한다).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    assert!(view.apply_action_for_test(Action::ShowDebugStats));
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Command(Command::DebugStats))),
        "판을 열었는데 서버에 안 물었다: {out:?}"
    );
    assert_eq!(view.screens.top(), Some(Screen::DebugStats));
}

#[test]
fn the_debug_stats_reply_lands_in_the_view_and_the_panel_shows_it() {
    // 회신이 오기 전에는 「아직 답하지 않았다」 한 줄이고(0 이나 빈 표가 아니다), 오면
    // 서버 줄이 클라 줄 **아래**에 붙는다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.apply_action_for_test(Action::ShowDebugStats);
    let before = view.debug_stats_lines();
    assert!(
        before.iter().any(|l| l.contains("서버가 아직 답하지 않았다")),
        "회신 전인데 기다린다는 말이 없다: {before:?}"
    );
    assert!(!before.iter().any(|l| l.contains("pid 4242")));
    let reply: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "debug_stats",
        "stats": {"pid": 4242, "python": "3.13.0", "uptime_s": 90000.0, "clients": 2,
                  "sessions": 1, "windows": 3, "panes": 4, "fds": 77,
                  "usage_probe": {"boot": 14.0, "panel": 10.1, "total": 37.0, "ok": true}}
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(reply))).unwrap();
    view.pump_headless();
    let after = view.debug_stats_lines();
    let joined = after.join("\n");
    assert!(joined.contains("pid 4242"), "{joined}");
    assert!(joined.contains("1d 1h"), "기동 뒤 시간이 안 적혔다: {joined}");
    assert!(joined.contains("boot 14.0s"), "프로브 회차가 안 적혔다: {joined}");
    assert!(!joined.contains("서버가 아직 답하지 않았다"), "{joined}");
    // 클라 절반이 먼저다 — 서버 줄은 그 아래.
    let mine = after.iter().position(|l| l.starts_with("pid ")).unwrap();
    let theirs = after.iter().position(|l| l.contains("pid 4242")).unwrap();
    assert!(mine < theirs, "{after:?}");
}

#[test]
fn reopening_debug_stats_forgets_the_old_reply() {
    // 지난 회신을 두면 「지금」이 아니라 「그때」를 본다 — 열 때마다 비우고 다시 묻는다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.apply_action_for_test(Action::ShowDebugStats);
    let reply: ServerMessage =
        serde_json::from_value(serde_json::json!({"t": "debug_stats", "stats": {"pid": 9}})).unwrap();
    tx.send(LinkEvent::Message(Box::new(reply))).unwrap();
    view.pump_headless();
    assert!(view.server_stats.is_some());
    view.handle_key(Key::Escape, Mods::NONE);
    view.apply_action_for_test(Action::ShowDebugStats);
    assert!(view.server_stats.is_none(), "다시 열었는데 옛 회신이 남아 있다");
}

/// pytmux-130 ⑴ — 탭 띠가 실린 목록 판. 자료 둘 + 꼬리의 잇는 줄 둘(서버 `_hub_rows`).
fn tabbed_spec() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-period", "kind": "list", "title": "기간별",
        "hint": "Esc 닫기", "keys": {"enter": "apply"},
        "rows": [
            {"key": "2026-09", "label": "9월 자료", "cols": [], "depth": 0, "expand": ""},
            {"key": "2026-08", "label": "8월 자료", "cols": [], "depth": 0, "expand": ""},
            {"key": "goto:sessions", "label": "세션별 →", "cols": [], "depth": 0, "expand": ""},
            {"key": "goto:settings", "label": "시나리오 설정 →", "cols": [], "depth": 0, "expand": ""}
        ],
        "tabs": [
            {"key": "goto:period", "label": "기간", "active": true, "action": false},
            {"key": "goto:sessions", "label": "세션", "active": false, "action": false},
            {"key": "goto:settings", "label": "시나리오", "active": false, "action": true}
        ],
        "text": "", "note": ""
    }))
    .unwrap()
}

#[test]
fn a_tabbed_panel_draws_the_strip_and_hides_the_trailing_link_rows() {
    // 정본 토큰 팝업의 탭 띠(`#tktabs`)가 우리 판 위에 선다. 같은 뜻인 꼬리의 잇는 줄은
    // 안 그린다 — 둘 다 보이면 어느 쪽을 눌러야 하나가 된다.
    let painted = painted_after(vec![layout_one_pane(), tabbed_spec()], &[]);
    for label in ["기간", "세션", "시나리오"] {
        assert!(painted.iter().any(|t| t == label), "띠에 {label} 이 없다: {painted:?}");
    }
    assert!(painted.iter().any(|t| t == "9월 자료"), "자료 줄이 사라졌다: {painted:?}");
    assert!(
        !painted.iter().any(|t| t.contains("세션별 →") || t.contains("시나리오 설정 →")),
        "띠가 있는데 잇는 줄도 그렸다: {painted:?}"
    );
}

#[test]
fn a_panel_without_tabs_still_draws_its_link_rows() {
    // 점진 채택 — 띠를 안 싣는 판(구서버 · 다른 플러그인)은 종전처럼 잇는 줄이 길이다.
    let mut spec = tabbed_spec();
    if let ServerMessage::PluginScreen(ref mut s) = spec {
        s.tabs.clear();
    }
    let painted = painted_after(vec![layout_one_pane(), spec], &[]);
    assert!(painted.iter().any(|t| t == "세션별 →"), "잇는 줄이 사라졌다: {painted:?}");
}

#[test]
fn clicking_a_tab_chooses_its_link_row() {
    // 탭은 잇는 줄의 다른 얼굴이다 — 누르면 그 줄을 고른 것과 **같은 명령**이 나간다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(tabbed_spec()))).unwrap();
    view.pump_headless();
    assert!(view.plugin_tab_clicked(1), "탭을 눌렀는데 아무 일도 안 났다");
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Command(Command::PluginAction { act, input, .. })
            if act == "apply" && input.as_deref() == Some("goto:sessions"))),
        "세션 탭이 잇는 줄을 안 골랐다: {out:?}"
    );
    // 활성 탭(자기 자신)은 잇는 줄이 없다 — 아무 일도 안 난다(정본도 그렇다).
    assert!(!view.plugin_tab_clicked(0));
}

#[test]
fn end_then_enter_on_a_tabbed_panel_picks_the_last_visible_row_not_a_hidden_link() {
    // 안 그리는 줄에 커서가 서면 `End`·`Enter` 가 보이지 않는 줄을 고른다 — 그 자리를 막는다.
    let out = sent_after(
        vec![layout_one_pane(), tabbed_spec()],
        &[(Key::End, Mods::NONE), (Key::Enter, Mods::NONE)],
    );
    let picked: Vec<_> = out
        .iter()
        .filter_map(|o| match o {
            Outgoing::Command(Command::PluginAction { input, .. }) => input.clone(),
            _ => None,
        })
        .collect();
    assert_eq!(picked, vec!["2026-08".to_owned()], "{out:?}");
}

#[test]
fn a_tab_drag_lights_the_tab_under_the_pointer_as_the_drop_target() {
    // pytmux-471 — 「배선이었나 합성 마우스였나」의 **배선 절반**을 기계로 가른다.
    // 진짜 이벤트(누름 → 버튼 든 채 이동)를 요소 트리에 흘려 드롭 대상이 서는지 본다.
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    use warpui_core::event::{Event, ModifiersState};
    let (over, lit_before, lit_after) = warpui::App::test((), move |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in three_tabs() {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        let (window_id, handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation.clone(), ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            let at = |needle: &str| {
                let t = scene
                    .painted_texts()
                    .find(|t| t.text.contains(needle))
                    .unwrap_or_else(|| panic!("탭 {needle} 이 안 그려졌다"));
                // ⚠ 시험 글꼴은 글자 폭이 0 — 가로는 여백만큼만 갈린다(하네스 머리말).
                vec2f(t.bounds.origin().x() + 1., t.bounds.origin().y() + t.bounds.height() / 2.)
            };
            let (a, b) = (at("하나"), at("둘"));
            let lit = |scene: &warpui_core::Scene| {
                scene
                    .layers()
                    .flat_map(|l| l.rects.iter())
                    .filter(|r| matches!(r.border.color, warpui::elements::Fill::Solid(c) if c == theme::FOCUS))
                    .count()
            };
            let lit_before = lit(&scene);
            presenter.dispatch_event(
                Event::LeftMouseDown { position: a, modifiers: ModifiersState::default(), click_count: 1, is_first_mouse: false },
                ctx,
            );
            // ⚠ 헤드리스 프레젠터는 요소가 쏜 **액션**(`dispatch_typed_action`)을 뷰까지
            //   안 나른다(상류 비공개 — 메모리 「클릭 주입 하네스는 거짓 오라클」). 그래서
            //   누름의 뜻(`TabPress` → `tab_drag`)은 뷰에 직접 세우고, 여기서 재는 것은
            //   **버튼 든 채 이동이 hover 를 갱신하나**(상류 07-31 수정) + 그것을 읽는
            //   `handle_mouse_drag` 의 배선이다.
            let pressed = handle.read(&*ctx, |v, _| v.tab_drag);
            if pressed.is_none() {
                handle.update(ctx, |v, _| v.tab_drag = Some(0));
            }
            presenter.dispatch_event(
                Event::LeftMouseDragged { position: b, modifiers: ModifiersState::default() },
                ctx,
            );
            let over = handle.update(ctx, |v, _| {
                v.handle_mouse_drag(None);
                v.tab_drag_over
            });
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            (over, lit_before, lit(&scene))
        })
    });
    assert_eq!(over, Some(1), "버튼 든 채 둘째 탭 위로 갔는데 드롭 대상이 안 섰다 — 배선이다");
    assert!(
        lit_after > lit_before,
        "드롭 대상은 섰는데 강조 테두리가 안 늘었다(전 {lit_before} · 후 {lit_after})"
    );
}

/// 8×8 진짜 PNG 한 장(pytmux-472 오라클용).
fn tiny_png() -> String {
    let path = std::env::temp_dir().join(format!("pytmux-gui-test-thumb-{}.png", std::process::id()));
    image::RgbaImage::from_pixel(8, 8, image::Rgba([200, 40, 40, 255]))
        .save(&path)
        .expect("시험용 PNG 를 못 썼다");
    path.to_string_lossy().into_owned()
}

#[test]
fn a_pasted_image_shows_a_thumbnail_in_the_corner_for_a_while() {
    // pytmux-472 — 자리 결정 ⓑ: 캔버스 우하단의 뜬 그림. 양성 오라클 — **면이 는다**
    // (그림이 아직 안 읽혔으면 그 자리의 글이 선다 — 빈 상자는 「안 붙었다」로 읽힌다).
    let path = tiny_png();
    let (images, texts, before) = painted_scene_setup(
        vec![layout_one_pane()],
        &[],
        move |v| v.note_pasted_thumb(&path),
        |scene| {
            (
                scene.layers().flat_map(|l| l.images.iter()).count(),
                scene.painted_texts().map(|t| t.text.clone()).collect::<Vec<_>>(),
                0usize,
            )
        },
    );
    let _ = before;
    assert!(
        images >= 1 || texts.iter().any(|t| t.contains("그림 읽는 중")),
        "붙여넣은 그림이 어디에도 안 보인다(그림 {images} · 글 {texts:?})"
    );
}

#[test]
fn the_thumbnail_goes_away_after_its_ttl_and_never_pushes_the_notice() {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.state.note_error("이미지 원격 전송 실패(scp 오류)".to_owned());
    view.pasted_thumb = Some(PastedThumb {
        path: "/nonexistent.png".to_owned(),
        since: Instant::now() - THUMB_TTL - Duration::from_secs(1),
    });
    assert!(view.pasted_thumb_element().is_none(), "수명이 지난 썸네일이 아직 그려진다");
    assert!(view.tick_thumb(), "지난 썸네일을 걷었으면 다시 그려야 한다");
    assert!(view.pasted_thumb.is_none());
    assert!(!view.tick_thumb(), "걷은 뒤에는 조용해야 한다");
    // 경고는 그대로다 — 썸네일은 알림 자리를 밀어내지 않는다.
    assert!(
        view.state.notices().any(|n| n.text.contains("scp 오류")),
        "썸네일이 알림을 밀어냈다"
    );
}

#[test]
fn a_text_panel_that_fits_does_not_advertise_scrolling() {
    // pytmux-478 ⑵ — 다 들어가는 판이 꼬리줄에서 「↑↓ 스크롤」이라고 말하면, 사용자는
    // 눌러 보고 **아무 일도 안 일어나는 것**을 본다. 할 수 없는 조작을 광고하는 것이다.
    //
    // ⛔ 그 판정은 서버가 못 한다 — 스크롤이 필요한지는 뷰포트가 정하고, 뷰포트를 아는
    //    것은 이 클라뿐이다. 그래서 서버는 두 토막을 따로 싣고 붙일지는 우리가 정한다.
    //
    // ⚠ **양쪽을 다 잰다.** 안 붙이기만 재면 「넘치는 판에서도 안 뜨는」 반쪽 수정이
    //    통과한다(부정 단언만 있는 오라클의 함정 — 이 저장소가 두 번 겪었다).
    let spec = |lines: usize| -> ServerMessage {
        serde_json::from_value(serde_json::json!({
            "t": "plugin_screen", "id": "mdir", "kind": "text", "title": "파일",
            "hint": "Esc 닫기", "scroll_hint": "↑↓ 스크롤",
            "text": (1..=lines).map(|n| format!("줄{n}\n")).collect::<String>(),
            "note": "", "keys": {}
        }))
        .unwrap()
    };
    let fits = painted_after(vec![layout_one_pane(), spec(3)], &[]);
    assert!(
        fits.iter().any(|t| t.contains("Esc 닫기")),
        "늘 붙는 토막이 아예 안 그려졌다: {fits:?}"
    );
    assert!(
        !fits.iter().any(|t| t.contains("↑↓ 스크롤")),
        "다 들어가는 판이 스크롤을 광고한다 — 제보의 그 꼬리줄이다: {fits:?}"
    );

    let overflows = painted_after(vec![layout_one_pane(), spec(400)], &[]);
    let hint = overflows
        .iter()
        .find(|t| t.contains("Esc 닫기"))
        .unwrap_or_else(|| panic!("넘치는 판에 꼬리줄이 없다: {overflows:?}"));
    assert!(
        hint.contains("↑↓ 스크롤"),
        "넘치는 판이 스크롤을 안 알린다: {hint:?}"
    );
    // ⚠ **뒤에 붙는다.** 가운데에 끼우면 토막이 나타나고 사라질 때마다 `Esc 닫기` 가
    //    좌우로 움직인다 — 같은 판을 두 번 열었을 뿐인데 꼬리줄이 딴 데 있어 보인다.
    assert!(
        hint.starts_with("Esc 닫기"),
        "늘 붙는 토막이 앞자리를 안 지켰다: {hint:?}"
    );
}

#[test]
fn a_spec_without_the_scroll_piece_keeps_its_whole_hint() {
    // 점진 채택 — 칸을 모르는 판은 종전대로 힌트를 통째로 늘 붙인다. 스펙을 내는
    // 플러그인 전부를 한 CL 에 고치지 않아도 되게 하는 자리다(pytmux-478 ⑵ §관문).
    let spec: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "x", "kind": "text", "title": "판",
        "hint": "↑↓ 스크롤 · Esc 닫기",
        "text": "한 줄\n", "note": "", "keys": {}
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), spec], &[]);
    assert!(
        painted.iter().any(|t| t == "↑↓ 스크롤 · Esc 닫기"),
        "칸이 없는 스펙의 꼬리줄이 달라졌다: {painted:?}"
    );
}

#[test]
fn the_list_panel_cursor_comes_back_from_the_end_in_one_press() {
    // 제보(pytmux-432 · 실 GUI 로 잰 것): `:usage-panel` 에서 `End` 를 누른 뒤 `↑` 를
    // **세 번** 눌러도 커서가 마지막 줄에 그대로 있었다.
    //
    // 까닭은 `press_list` 의 `End` 가 `usize::MAX` 를 두기 때문이다 — *"끝이 몇 번째인지는
    // 뷰가 안다"* 는 규약 위에 선 값인데(pytmux-417 ①) **그 뷰가 없었다**: 형제 셋
    // (`settle_info_tabs`·`settle_settings_cursor`·`settle_plugin_scroll`) 중 어느 것도
    // `Screen::PluginView` 의 «목록»을 안 봤다. 되돌리려면 10^19 번을 눌러야 한다.
    //
    // ⛔ 값이 아니라 **그림**을 본다(형제 오라클과 같은 이유) — 자르는 자리가 어디든
    //    사용자가 겪는 것은 「한 번 눌러 한 줄 올라가나」 하나다.
    let rows: Vec<serde_json::Value> = (1..=8)
        .map(|n| serde_json::json!({"key": format!("k{n}"), "cols": [format!("행{n:02}")]}))
        .collect();
    let list: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-token-usage-view", "kind": "list",
        "title": "Claude usage limit (/usage)", "hint": "", "rows": rows,
        "text": "", "note": "", "keys": {}
    }))
    .unwrap();

    let end = vec![(Key::End, Mods::NONE)];
    let mut end_up = end.clone();
    end_up.push((Key::Up, Mods::NONE));

    let at_end = painted_after(vec![layout_one_pane(), list.clone()], &end);
    let after_up = painted_after(vec![layout_one_pane(), list], &end_up);

    assert!(
        at_end.iter().any(|t| t.contains("행08")),
        "End 를 눌렀는데 마지막 줄이 안 보인다 — 이 오라클의 전제가 깨졌다: {at_end:?}"
    );
    assert_ne!(
        at_end, after_up,
        "`End` 뒤 `↑` 한 번이 아무것도 안 바꿨다 — 커서가 목록 밖으로 달아났다(pytmux-432)"
    );
}

#[test]
fn a_kind_we_cannot_draw_says_so_instead_of_showing_an_empty_panel() {
    // 조용한 누락이 이 저장소의 상습 결함이다(설계 §8-5).
    //
    // ⚠ 2026-08-01 P5 에서 이 오라클의 표본을 바꿨다: 종전에는 `form` 을 썼는데 P5 가
    //   그 모양을 그리게 되면서 **오라클이 스스로 낡았다**(적색으로 그 사실을 알렸다).
    //   지금은 스펙에 없는 모양(`tree`)으로 잰다 — 재는 것은 특정 모양이 아니라
    //   "모르는 모양을 말하는가"다.
    let unknown: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "tree",
        "title": "트리", "hint": "", "rows": [], "text": "", "note": "", "keys": {}
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), unknown], &[]);
    assert!(
        painted.iter().any(|line| line.contains("아직 못 그립니다")),
        "못 그리는 모양인데 아무 말도 없다: {painted:?}"
    );
}

// ── P5 — 나머지 모양 넷과 **스펙이 정하는 글자 키** ────────────────────────────────

fn ncd_list_screen() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "ncd", "kind": "list",
        "title": "디렉터리 — /home/me", "hint": "(↑↓ · Enter 들어가기 · c 여기로 cd)",
        "rows": [
            {"key": "/home", "label": "..", "cols": []},
            {"key": "/home/me/src", "label": "src", "cols": []}
        ],
        "selected": 0, "note": "", "keys": {"enter": "into", "c": "cd"}
    }))
    .unwrap()
}

#[test]
fn a_letter_key_the_spec_declares_becomes_that_plugin_action() {
    // 스펙이 자기 키를 정한다 — 목록 화면에서 글자는 원래 "닫기"라, 이 배선이 없으면
    // 우리 키가 그 판을 먼저 닫는다.
    let sent = sent_after(
        vec![layout_one_pane(), ncd_list_screen()],
        &[(Key::Down, Mods::NONE), (Key::Char('c'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("글자 키가 액션이 안 됐다: {frames:?}"));
    assert_eq!(action["do"], "cd");
    assert_eq!(action["input"], "/home/me/src", "고른 줄의 뜻이 안 실렸다: {action:?}");
}

/// 진짜 `ncd` 가 내려 주는 모양의 **트리** 스펙(`plugins/ncd/__init__.py::_tree_spec`).
///
/// 위 `ncd_list_screen` 과 가르는 것: `depth`·`expand` 가 실리고 키 표에 `right`/`left`
/// (펼치기·접기)가 있다. 제보(pytmux-173)가 가리키는 화면이 이쪽이다.
fn ncd_tree_screen() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "ncd", "kind": "list",
        "title": "디렉터리 — /home/me", "hint": "(↑↓ · Enter 여기로 cd · ←→ 접고 펴기)",
        "rows": [
            {"key": "/home", "label": "home", "cols": [], "depth": 0, "expand": "open"},
            {"key": "/home/me", "label": "me", "cols": [], "depth": 1, "expand": "open",
             "tag": "cwd"},
            {"key": "/home/me/src", "label": "src", "cols": [], "depth": 2, "expand": "shut"}
        ],
        "selected": 1, "note": "",
        "keys": {"enter": "into", "c": "cd", "right": "expand", "left": "collapse"}
    }))
    .unwrap()
}

#[test]
fn enter_on_an_ncd_row_sends_the_into_action_with_that_path() {
    // ⛔ **이 자리에 양성 오라클이 없었다**(pytmux-173 「참고」). 글자 키(`c`)는 위에서
    //    잠겨 있었지만 `Enter` 는 아니었고, 제보가 가리킨 것이 바로 `Enter` 다.
    //
    // 재는 것: 커서를 한 칸 내리고 `Enter` → **그 줄의 경로**를 실은 `into` 가 나간다.
    // 서버(`plugins/ncd/__init__.py`)의 `do == "into"` 는 `input` 이 비면 아무것도 안
    // 보내고 화면만 닫는다 — 곧 `input` 이 빠지는 것이 제보의 증상과 똑같이 생겼다.
    let sent = sent_after(
        vec![layout_one_pane(), ncd_tree_screen()],
        &[(Key::Down, Mods::NONE), (Key::Enter, Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("Enter 가 액션이 안 됐다: {frames:?}"));
    assert_eq!(action["id"], "ncd");
    assert_eq!(action["do"], "into", "Enter 가 `into` 로 안 갔다: {action:?}");
    assert_eq!(
        action["input"], "/home/me/src",
        "고른 줄의 **경로**가 안 실렸다 — 서버는 input 이 비면 조용히 아무것도 안 한다: {action:?}"
    );
}

#[test]
fn enter_without_moving_uses_the_row_the_spec_preselected() {
    // ncd 는 커서를 **셸이 서 있는 줄**에 두고 연다(`_tree_spec` 의 `selected`) — 그래야
    // 「열자마자 Enter 한 번」이 뜻을 갖는다. 그 자리를 안 쓰면 늘 첫 줄로 cd 한다.
    let sent = sent_after(
        vec![layout_one_pane(), ncd_tree_screen()],
        &[(Key::Enter, Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("Enter 가 액션이 안 됐다: {frames:?}"));
    assert_eq!(action["input"], "/home/me", "스펙이 고른 줄(selected=1)이 안 쓰였다");
}

#[test]
fn the_arrow_keys_fold_and_unfold_instead_of_moving_the_cursor() {
    // 트리에서 `←→` 는 **접고 펴기**다(스펙이 정한다) — 그 배선이 죽으면 트리가 목록으로
    // 되돌아가고, `Enter` 로 갈 수 있는 줄이 그만큼 줄어든다(제보의 「확인하면 좁혀질 것」).
    let sent = sent_after(
        vec![layout_one_pane(), ncd_tree_screen()],
        &[(Key::Right, Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("→ 가 액션이 안 됐다: {frames:?}"));
    assert_eq!(action["do"], "expand");
    assert_eq!(action["input"], "/home/me");
}

fn claude_settings_form_screen() -> ServerMessage {
    // 서버가 켬/끔을 로케일 불문 고정 기호로 낸다(`screenspec.py` `pscreen.spec_on_mark`
    // /`_off_mark` = "●"/"○") — 순환값(예: 반복 알림 초)은 숫자 그대로 남는다.
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-settings", "kind": "form",
        "title": "Claude settings", "hint": "",
        "rows": [
            {"key": "autoresume", "label": "자동재개", "cols": ["●"]},
            {"key": "prompt_clear", "label": "프롬프트 자동비움", "cols": ["○"]},
            {"key": "repeat_alert", "label": "반복 알림", "cols": ["5"]}
        ],
        "selected": 0, "note": "", "keys": {"enter": "toggle"}
    }))
    .unwrap()
}

#[test]
fn on_off_form_values_draw_as_a_native_toggle_not_the_raw_glyph() {
    // pytmux-182 ⑵ — 값이 그냥 글자로 찍히던 자리를 네이티브 토글 그림으로 바꿨다.
    // 켜짐/꺼짐 둘 다 "●"/"○" 원문 글자로는 더 이상 안 그려져야 한다(그림으로 바뀌었다는
    // 뜻) — 반면 순환값("5")은 종전대로 글자로 남는다.
    let painted = painted_after(vec![layout_one_pane(), claude_settings_form_screen()], &[]);
    assert!(painted_contains(&painted, "자동재개"), "이름조차 안 그려졌다: {painted:?}");
    assert!(
        !painted_contains(&painted, "●") && !painted_contains(&painted, "○"),
        "켬/끔이 그림이 아니라 아직 글자로 그려진다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "5"),
        "순환값(글자여야 한다)이 사라졌다: {painted:?}"
    );
}

#[test]
fn a_letter_the_spec_does_not_declare_is_ignored_not_a_close() {
    // ⚠ 종전엔 표에 없는 글자가 판을 닫았다(`press_list` 의 `_ => close_top()`) — 정본
    // 목록 화면(Textual ListView/OptionList)은 모르는 키에 아무 일도 안 하고 `Esc` 만
    // 닫는다(pytmux-181·273). 판이 그대로 있는지를 본다.
    let painted = painted_after(
        vec![layout_one_pane(), ncd_list_screen()],
        &[(Key::Char('q'), Mods::NONE)],
    );
    assert!(
        painted_contains(&painted, "디렉터리 — /home/me"),
        "표에 없는 글자가 판을 닫았다: {painted:?}"
    );
}

#[test]
fn a_table_spec_draws_its_columns() {
    let table: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "x", "kind": "table", "title": "표", "hint": "",
        "rows": [{"key": "a", "label": "이름", "cols": ["10KB", "2026/08/01"]}],
        "text": "", "note": "", "selected": 0, "keys": {}
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), table], &[]);
    assert!(painted_contains(&painted, "이름"), "{painted:?}");
    assert!(painted_contains(&painted, "10KB"), "칸이 안 그려졌다: {painted:?}");
}

/// 줄의 **칸**은 우리 로케일로 그린다 — 이름은 그대로 둔다(2026-08-02p).
///
/// `mdir` 을 카탈로그로 옮기고 나서도 여기가 빠져 있으면 영어 사용자에게 `<상위>` 가
/// 한국어로 뜬다: 서버는 **자기** 로케일로 스펙을 짓기 때문이다(`title`·`hint`·`note`
/// 만 `say_*` 를 거치고 있었다). 배선을 되돌리는 변이(`say_cols()` → `cols`)를 이
/// 오라클이 잡는다.
#[test]
fn a_rows_columns_are_drawn_in_our_locale_but_its_name_is_not() {
    let table: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "table",
        "title": "표", "hint": "",
        // 이름이 하필 카탈로그의 말과 같아도 그대로 그린다 — 그런 이름의 파일이 있다.
        "rows": [{"key": "..", "label": "빈 디렉터리입니다", "cols": ["<상위>"]}],
        "text": "", "note": "", "selected": 0, "keys": {}
    }))
    .unwrap();
    let painted = base::i18n::with_locale("en", || {
        painted_after(vec![layout_one_pane(), table], &[])
    });
    assert!(
        painted_contains(&painted, "<UP>"),
        "칸이 서버 로케일 그대로 그려졌다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "빈 디렉터리입니다"),
        "이름을 번역했다 — 그건 자료다: {painted:?}"
    );
}

#[test]
fn a_prompt_spec_uses_the_native_ask_and_sends_the_typed_answer() {
    // 물음은 이 클라가 이미 잘하는 일이다 — 플러그인이 물었다고 화면을 한 벌 더 만들면
    // 되돌릴 수 없는 것 앞의 규칙이 두 곳에 생긴다.
    // ⚠ `text` 는 **입력칸 초기값**이다(아래 오라클) — 빈 칸에서 시작하는 물음이라
    //    여기서는 비워 둔다.
    let ask: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "prompt",
        "title": "새 이름", "hint": "", "rows": [], "text": "", "note": "",
        "selected": 0, "keys": {"enter": "rename"}
    }))
    .unwrap();
    let sent = sent_after(
        vec![layout_one_pane(), ask],
        &[
            (Key::Char('h'), Mods::NONE),
            (Key::Char('i'), Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("답이 안 돌아갔다: {frames:?}"));
    assert_eq!(action["do"], "rename");
    assert_eq!(action["input"], "hi");
}

#[test]
fn a_prompt_spec_seeds_the_input_with_what_the_plugin_sent() {
    // pytmux-35: **고치는 화면**의 물음은 지금 값에서 시작해야 한다. 안 그러면 규칙
    // 하나를 손보려고 전체를 다시 쳐야 하고, 그건 '편집'이 아니라 '덮어쓰기'다
    // (`claude-rules` 의 시작 규칙 · `namesync` 의 경로·키워드가 그 부류다).
    let ask: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "claude-rules", "kind": "prompt",
        "title": "Claude 시작 규칙", "hint": "", "rows": [],
        "text": "한국어로 답할 것", "note": "", "selected": 0,
        "keys": {"enter": "save"}
    }))
    .unwrap();
    // 초기값 뒤에 한 글자를 더 치고 확정한다 — 초기값이 안 실렸으면 답은 `!` 뿐이다.
    let sent = sent_after(
        vec![layout_one_pane(), ask],
        &[(Key::Char('!'), Mods::NONE), (Key::Enter, Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("답이 안 돌아갔다: {frames:?}"));
    assert_eq!(action["do"], "save");
    assert_eq!(action["input"], "한국어로 답할 것!");
}

// ── P3 — 오버레이는 클라만 아는 사실이고, 그림은 서버가 준다 ────────────────────

/// 달력이 켜진 한 판 — 서버가 그림 대신 **누를 자리와 키 표**까지 실어 준 프레임.
fn calendar_cells() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [1],
        "runs": [{"x": 10, "y": 1, "text": "‹ 2026-08 ›", "style": {"bo": 1},
                  "theme": {"f": "success"}}],
        "zones": [{"x": 10, "y": 1, "w": 2, "h": 1, "pane": 1,
                   "name": "calendar", "do": "prev"},
                  {"x": 19, "y": 1, "w": 2, "h": 1, "pane": 1,
                   "name": "calendar", "do": "next"}],
        "keys": [{"key": "left", "pane": 1, "name": "calendar", "do": "prev"},
                 {"key": "home", "pane": 1, "name": "calendar", "do": "today"}]
    }))
    .unwrap()
}

#[test]
fn clicking_the_arrow_sends_back_the_name_the_server_gave_us() {
    // 화살표를 그려 놓고 클릭이 안 먹으면 그 화살표가 **거짓말**이 된다. 우리는 뜻을
    // 모른 채 이름만 되돌려 보낸다(설계 §4.4).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(calendar_cells()))).unwrap();
    view.pump_headless();
    view.handle_mouse_down((19, 1), false);
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let act = frames
        .iter()
        .find(|f| f["action"] == "plugin_overlay_action")
        .unwrap_or_else(|| panic!("클릭이 안 올라갔다: {frames:?}"));
    assert_eq!(act["name"], "calendar");
    assert_eq!(act["do"], "next");
    assert_eq!(act["pane"], 1);
}

#[test]
fn the_overlay_takes_the_keys_the_spec_declared_and_the_pane_gets_nothing() {
    // 패널이 이미 달력에 덮여 있으니 이 키를 가져가도 셸 입력을 가리지 않는다
    // (정본 `client_overlay_key` 와 같은 규칙). **표에 있는 키만** 가져간다 —
    // 안 그러면 오버레이가 떠 있는 동안 셸이 먹통이 된다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(calendar_cells()))).unwrap();
    view.pump_headless();
    for (key, mods) in [
        (Key::Left, Mods::NONE),
        (Key::Home, Mods::NONE),
        (Key::Right, Mods::NONE),
    ] {
        view.handle_key(key, mods);
        view.pump_headless();
    }
    let out = sent.lock().unwrap().clone();
    let frames: Vec<serde_json::Value> = out.iter().map(|o| o.to_frame()).collect();
    let acts: Vec<&str> = frames
        .iter()
        .filter(|f| f["action"] == "plugin_overlay_action")
        .map(|f| f["do"].as_str().unwrap())
        .collect();
    assert_eq!(acts, ["prev", "today"], "스펙이 정한 키를 안 가져갔다: {frames:?}");
    // 표에 없는 →(right)는 그대로 패널로 간다.
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Input(_))),
        "표에 없는 키까지 삼켰다: {out:?}"
    );
}

#[test]
fn toggling_the_calendar_tells_the_server_and_closes_the_clock_there() {
    // 한 패널엔 오버레이 하나(정본 규칙). 밀려난 시계의 **끔까지** 올려야 서버가 두
    // 그림을 겹쳐 보내지 않는다.
    // 달력에는 기본 키가 없다(팔레트·메뉴의 `calendar-mode`) — 그 두 길이 결국 부르는
    // 액션을 그대로 태운다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.apply_action(base::Action::ToggleClock);
    view.apply_action(base::Action::ToggleCalendar);
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let overlays: Vec<(&str, bool)> = frames
        .iter()
        .filter(|f| f["action"] == "plugin_overlay")
        .map(|f| (f["name"].as_str().unwrap(), f["on"].as_bool().unwrap()))
        .collect();
    assert_eq!(
        overlays,
        [("clock", true), ("clock", false), ("calendar", true)],
        "달력을 켜며 시계를 안 껐다: {frames:?}"
    );
}

#[test]
fn toggling_the_clock_tells_the_server_which_pane() {
    // 시계를 서버가 그리려면 **어느 패널에 켰나**를 들어야 한다(설계 §4.4). 그 사실을
    // 안 올리면 서버는 아무것도 안 그리고, 화면에서는 "키가 안 먹었다"로 보인다.
    let sent = sent_after(
        vec![layout_one_pane()],
        &[(Key::Char('b'), Mods::CTRL), (Key::Char('t'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let on = frames
        .iter()
        .find(|f| f["action"] == "plugin_overlay")
        .unwrap_or_else(|| panic!("오버레이 사실이 안 올라갔다: {frames:?}"));
    assert_eq!(on["name"], "clock");
    assert_eq!(on["on"], true, "켰는데 껐다고 보냈다: {on:?}");

    // 한 번 더 누르면 **껐다고** 보낸다 — 안 보내면 서버가 영영 그린다.
    let sent = sent_after(
        vec![layout_one_pane()],
        &[
            (Key::Char('b'), Mods::CTRL),
            (Key::Char('t'), Mods::NONE),
            (Key::Char('b'), Mods::CTRL),
            (Key::Char('t'), Mods::NONE),
        ],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let offs: Vec<_> = frames
        .iter()
        .filter(|f| f["action"] == "plugin_overlay" && f["on"] == false)
        .collect();
    assert_eq!(offs.len(), 1, "끈 사실이 안 올라갔다: {frames:?}");
}

// ── pytmux-156 — 오버레이가 덮은 판은 아무 데나 눌러도 닫힌다 ──────────────────
//
// 정본 `clientwidgets.py:544`("[x] 버튼 폐지")의 그 갈래가 GUI 에는 통째로 없었다.
// 그래서 닫는 길이 상태줄의 작은 시각/날짜 표식 **하나뿐**이었다.

/// 서버가 이름을 실은 오버레이 프레임만 차례대로 — `(이름, 켬)`.
fn overlay_frames(sent: &Sent) -> Vec<(String, bool)> {
    sent.lock()
        .unwrap()
        .iter()
        .map(|o| o.to_frame())
        .filter(|f| f["action"] == "plugin_overlay")
        .map(|f| {
            (f["name"].as_str().unwrap().to_owned(), f["on"].as_bool().unwrap())
        })
        .collect()
}

#[test]
fn clicking_the_panel_the_overlay_covers_closes_it() {
    // pytmux-156 그대로 재현한다: 상태줄 시각을 눌러 시계를 띄우고 **판 안**을 누른다.
    // 종전에는 여기서 아무 일도 안 났고, 사람은 그 작은 표식을 다시 찾아 눌러야 했다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Clock));
    view.handle_mouse_down((40, 2), false);
    view.pump_headless();
    assert_eq!(
        overlay_frames(&sent),
        [("clock".to_owned(), true), ("clock".to_owned(), false)],
        "판을 눌렀는데 끔이 서버로 안 갔다"
    );
}

#[test]
fn a_click_on_a_bare_panel_is_not_a_close() {
    // ⛔ 끄기를 멱등으로 흉내 내면 **오버레이가 없는 판**을 누른 것까지 닫기가 되어
    //    그 클릭을 삼킨다 — 선택·포커스가 통째로 죽는다. 양성 오라클과 짝이다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.handle_mouse_down((40, 2), false);
    view.pump_headless();
    assert!(overlay_frames(&sent).is_empty(), "안 켜진 판을 눌렀는데 끔이 나갔다");
}

#[test]
fn the_calendar_arrow_still_wins_over_the_close() {
    // ☠ 순서가 뒤집히면 달력 판의 클릭이 곧 닫기라 `‹`/`›` 를 **누를 길이 아예 없다**
    //   (정본이 화살표 갈래에 그 이유를 적어 둔 자리다). 달력을 실제로 켜 놓고 잰다 —
    //   안 켜면 닫기 갈래가 애초에 안 지나가 이 오라클이 공허해진다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(calendar_cells()))).unwrap();
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Calendar));
    view.handle_mouse_down((19, 1), false);
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let act = frames
        .iter()
        .find(|f| f["action"] == "plugin_overlay_action")
        .unwrap_or_else(|| panic!("화살표가 안 올라갔다 — 닫기가 먹었다: {frames:?}"));
    assert_eq!((&act["name"], &act["do"]), (&"calendar".into(), &"next".into()));
    assert_eq!(
        overlay_frames(&sent),
        [("calendar".to_owned(), true)],
        "화살표를 눌렀는데 달력이 닫혔다: {frames:?}"
    );
}

#[test]
fn the_close_beats_the_claude_zone_hiding_under_the_overlay() {
    // ☠ Claude 클릭존은 **오버레이 밑**이라 사람 눈에 안 보인다. 닫기를 그 뒤에 두면
    //   시계를 닫으려던 클릭이 권한모드 판을 연다 — 정본은 이 순서를 반대로 적어 뒀다
    //   (`clientwidgets.py`: 닫기 544 → interrupt/perm 존은 그 아래).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [], "runs": [],
        "zones": [{"x": 10, "y": 1, "w": 15, "h": 1, "pane": 1,
                   "name": "claude-code", "do": "perm",
                   "opens": "claude-perm-mode"}],
        "keys": []
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(cells))).unwrap();
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Clock));
    view.handle_mouse_down((12, 1), false);
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    assert!(
        !frames.iter().any(|f| f["action"] == "plugin_open"),
        "시계를 닫으려던 클릭이 권한모드 판을 열었다: {frames:?}"
    );
    assert_eq!(
        overlay_frames(&sent),
        [("clock".to_owned(), true), ("clock".to_owned(), false)],
        "Claude 존이 닫기를 먹었다: {frames:?}"
    );
}

#[test]
fn the_status_badge_still_toggles_after_the_panel_can_close_it() {
    // 회귀: 상태줄 표식은 **패널이 아니라서** `pane_at` 이 `None` 이고, 그래서 새 갈래를
    // 안 지난다. 그 사실이 깨지면 표식이 열자마자 닫는 판이 된다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Clock));
    view.chrome_click(base::chrome::ClickTarget::Badge(base::Badge::Clock));
    view.pump_headless();
    assert_eq!(
        overlay_frames(&sent),
        [("clock".to_owned(), true), ("clock".to_owned(), false)],
        "표식 두 번이 켜고 끄지 않는다"
    );
}

// ── P6 — 스펙이 물음 문구와 커서 자리를 정한다 ──────────────────────────────────

fn mdir_table_screen(selected: usize) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "table",
        "title": "파일 관리자 — /tmp/x", "hint": "(Enter 열기)",
        "rows": [
            {"key": "/tmp", "label": "   ..", "cols": ["<상위>"]},
            {"key": "/tmp/x/sub", "label": "  sub/", "cols": ["<DIR>", "2026/08/02 01:00"]},
            {"key": "/tmp/x/a.txt", "label": "  a.txt", "cols": ["9B", "2026/08/02 01:00"]}
        ],
        "text": "", "note": "", "selected": selected,
        "keys": {"enter": "into", "d": "delete", "t": "tag"}
    }))
    .unwrap()
}

#[test]
fn a_plugin_ask_shows_the_question_the_plugin_wrote() {
    // 되돌릴 수 없는 것 앞에서 "플러그인이 물었다:" 한 줄만 보이면, 사람은 **무엇이
    // 사라지는지 모른 채** 누른다. 문구의 주인은 스펙이다(`title` → 물음 · `note` → 상세).
    let ask: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "confirm",
        "title": "2개를 지웁니다 — 되돌릴 수 없습니다", "hint": "", "rows": [],
        "text": "", "note": "a.txt, b.txt", "selected": 0, "keys": {"enter": "apply"}
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), ask], &[]);
    assert!(
        painted_contains(&painted, "되돌릴 수 없습니다"),
        "플러그인이 쓴 물음이 안 보인다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "a.txt, b.txt"),
        "무엇이 사라지는지가 안 보인다: {painted:?}"
    );
    assert!(
        !painted_contains(&painted, "플러그인이 물었다"),
        "폴백 문구가 플러그인의 물음을 덮었다: {painted:?}"
    );
}

#[test]
fn a_plugin_screen_puts_the_cursor_where_the_spec_says() {
    // 목록을 갈아 끼우는 것은 늘 사용자의 손짓에 대한 답이다(디렉터리 이동·태그) —
    // 어디에 커서를 놓아야 하는지는 **만든 쪽**이 알고, 그 칸이 `selected` 다.
    // 이 배선이 없던 동안 그 칸은 아무도 안 읽는 죽은 칸이었다.
    let sent = sent_after(
        vec![layout_one_pane(), mdir_table_screen(2)],
        &[(Key::Char('t'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action")
        .unwrap_or_else(|| panic!("액션이 안 나갔다: {frames:?}"));
    assert_eq!(
        action["input"], "/tmp/x/a.txt",
        "스펙이 고른 줄이 아니라 다른 줄이 실렸다: {action:?}"
    );
    assert_eq!(action["row"], 2, "{action:?}");
}

#[test]
fn a_detail_screen_does_not_steal_the_place_you_were_at() {
    // 글 화면(상세)은 고르는 화면이 아니다 — 거기서 커서를 건드리면 `Esc` 로 목록에
    // 돌아왔을 때 자리를 잃는다(`selected` 를 무턱대고 따르면 생기는 반대쪽 결함).
    let detail: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "text",
        "title": "a.txt", "hint": "", "rows": [], "text": "가나다",
        "note": "", "selected": 0, "keys": {}
    }))
    .unwrap();
    let sent = sent_after(
        vec![layout_one_pane(), mdir_table_screen(2), detail],
        &[(Key::Escape, Mods::NONE), (Key::Char('d'), Mods::NONE)],
    );
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    let action = frames
        .iter()
        .find(|f| f["action"] == "plugin_action" && f["do"] == "delete")
        .unwrap_or_else(|| panic!("목록으로 못 돌아왔다: {frames:?}"));
    assert_eq!(
        action["input"], "/tmp/x/a.txt",
        "상세를 보고 왔더니 커서가 옮겨져 있었다: {action:?}"
    );
}

#[test]
fn cancelling_a_plugin_confirm_does_nothing_at_all() {
    // 기본이 '아니오'인 화면이다 — 취소가 곧 "아무 일도 안 일어남"이라야 한다.
    let ask: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_screen", "id": "mdir", "kind": "confirm",
        "title": "삭제", "hint": "", "rows": [], "text": "정말 지울까?", "note": "",
        "selected": 0, "keys": {"enter": "delete"}
    }))
    .unwrap();
    let sent = sent_after(vec![layout_one_pane(), ask], &[(Key::Escape, Mods::NONE)]);
    let frames: Vec<serde_json::Value> = sent.iter().map(|o| o.to_frame()).collect();
    assert!(
        !frames.iter().any(|f| f["action"] == "plugin_action"),
        "취소했는데 액션이 나갔다: {frames:?}"
    );
}

#[test]
fn the_input_method_state_goes_up_as_a_fact_not_a_drawing() {
    // ★ 이 배선은 오래 **라이브 스크린샷으로만** 잡혔다(GUI 에 큐 오라클이 없다던 그
    // 자리). 한/영을 묻는 일만 OS 에 남기고 올리는 일을 갈라 두니 여기서 잴 수 있다.
    //
    // 우리가 보내는 것은 **사실뿐**이다 — 어디에 무슨 색으로 그릴지는 플러그인이
    // 정한다(설계 Tier D · §4.4). 종전에는 우리가 그림까지 들고 있었고 자리가 정본과
    // 갈려 있었다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();

    assert!(view.report_ime(Some("한")), "바뀐 것을 안 알렸다");
    view.pump_headless();          // 모아 둔 것을 실제로 보낸다
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let fact = frames
        .iter()
        .find(|f| f["action"] == "client_fact")
        .unwrap_or_else(|| panic!("사실이 안 올라갔다: {frames:?}"));
    assert_eq!(fact["name"], "ime");
    assert_eq!(fact["value"], "한");

    // 같은 값은 다시 안 올린다 — 0.3초마다 같은 말을 하면 서버가 매번 다시 그린다.
    assert!(!view.report_ime(Some("한")), "안 바뀌었는데 또 올렸다");

    // 끄는 것도 사실이다: 값이 비면 서버가 그 사실을 지운다(배지가 사라진다).
    assert!(view.report_ime(None));
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let last = frames
        .iter()
        .filter(|f| f["action"] == "client_fact")
        .next_back()
        .unwrap();
    assert!(last["value"].is_null(), "끔이 안 올라갔다: {last}");
}

// ── 셀 격자(§10-21ⓙ) · 블록 문자(§10-21ⓘ) ──────────────────────────────────
//
// 제보 둘의 뿌리가 하나다: **가로 자리를 글꼴이 정하고 있었다.** 캔버스가 한 줄을 런
// 통짜로 셰이퍼에 넘기면, 폴백 글꼴에서 오는 글자(한글·블록)의 진폭이 칸너비의 정수배가
// 아닐 때 그 뒤가 전부 밀린다.
//
// 그래서 격자를 클라가 잡는다. 아래 오라클은 그 **나누기 규칙**을 글꼴 없이 잰다 —
// 시험 폰트는 폭이 0이라 픽셀은 못 재지만(이 파일 머리말), 규칙은 순수 함수다.

#[test]
fn a_pure_ascii_run_stays_one_piece() {
    // 화면의 대부분이 여기다. 한 칸에 하나씩 만들면 80x24 에 1,920 조각이 생긴다.
    let segs = SessionView::grid_segments("hello world");
    assert_eq!(segs.len(), 1, "ASCII 를 쪼갰다: {segs:?}");
    assert_eq!(segs[0], ("hello world".to_owned(), 11));
}

#[test]
fn each_hangul_char_gets_its_own_cell_box() {
    // ★ 이것이 ⓙ 의 처방이다. 한글을 이어 붙이면 그 안에서 밀린 만큼 뒤가 밀린다.
    let segs = SessionView::grid_segments("가나다");
    assert_eq!(segs.len(), 3, "한글을 한 덩이로 뒀다: {segs:?}");
    for seg in &segs {
        assert_eq!(seg.1, 2, "한글은 두 칸이다: {seg:?}");
    }
}

#[test]
fn ascii_and_hangul_alternate_without_losing_a_character() {
    // 나누기가 글자를 먹으면 화면에서 조용히 사라진다.
    let segs = SessionView::grid_segments("ab가cd나");
    let joined: String = segs.iter().map(|(s, _)| s.as_str()).collect();
    assert_eq!(joined, "ab가cd나", "나누며 글자가 바뀌었다: {segs:?}");
    assert_eq!(
        segs.iter().map(|(_, c)| *c).collect::<Vec<_>>(),
        vec![2, 2, 2, 2],
        "칸 수가 틀리면 그 뒤가 통째로 밀린다: {segs:?}"
    );
}

#[test]
fn the_cell_count_of_a_row_is_what_the_server_counted() {
    // ★ 이 합이 곧 그 줄의 폭이다. 여기가 어긋나면 우리가 잡은 격자가 서버의 격자와
    //   다른 뜻이 되어, 마우스 셀 산수·테두리·커서가 한꺼번에 어긋난다.
    for text in ["hello", "가나다", "a가b나c", "─│┌", ""] {
        let ours: usize = SessionView::grid_segments(text).iter().map(|(_, c)| c).sum();
        let server: usize = text
            .chars()
            .map(|c| proto::compose::char_cells(c).max(1))
            .sum();
        assert_eq!(ours, server, "{text:?} 의 칸 수가 서버와 다르다");
    }
}

#[test]
fn an_empty_run_makes_no_pieces() {
    assert!(SessionView::grid_segments("").is_empty());
}

/// 블록 문자가 든 캔버스 하나.
fn canvas_with(text: &str) -> proto::canvas::Canvas {
    let mut canvas = proto::canvas::Canvas::new(text.chars().count().max(4), 1);
    canvas.put_text(0, 0, text, CellStyle::default());
    canvas
}

#[test]
fn block_characters_become_rectangles_at_their_own_cells() {
    // 자리(칸 좌표)가 틀리면 그림이 통째로 어긋난다 — 마스코트 제보가 그것이다.
    let blocks = SessionView::block_cells(&canvas_with("a█b▀"));
    assert_eq!(blocks.len(), 2, "블록 둘을 못 찾았다");
    assert_eq!((blocks[0].x, blocks[0].y), (1, 0));
    assert_eq!((blocks[1].x, blocks[1].y), (3, 0));
    assert_eq!(blocks[1].fill, proto::canvas::block_fills('▀').unwrap()[0]);
}

#[test]
fn a_canvas_without_blocks_asks_for_no_rectangles() {
    // 빈 목록이라야 오버레이가 그 프레임에 아무 일도 안 한다.
    assert!(SessionView::block_cells(&canvas_with("hello")).is_empty());
}

#[test]
fn the_blanked_set_and_the_rectangles_come_from_the_same_judgement() {
    // ★ 둘이 갈리면 글리프와 사각형이 겹쳐 보이거나(칸을 안 비움) 그림이 통째로
    //   사라진다(사각형을 안 그림). 테두리 쪽에서 이미 굳은 규율이다.
    let canvas = canvas_with("░▒a▓");
    let from_rects: std::collections::BTreeSet<(u16, u16)> = SessionView::block_cells(&canvas)
        .into_iter()
        .map(|b| (b.x, b.y))
        .collect();
    assert_eq!(from_rects, SessionView::block_cell_set(&canvas));
    assert_eq!(from_rects.len(), 3);
}

#[test]
fn a_block_cell_is_not_painted_as_a_glyph() {
    // ★ **그려진 프레임**에서 잰다(순수 함수 둘이 맞아도 render_row 가 안 비우면
    //   글리프와 사각형이 겹친다 — 그 배선이 이 오라클의 대상이다).
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["AA█BB", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let painted = painted_after(vec![layout_one_pane(), screen], &[]);
    // ⚠ **프레임 전체**를 봐야 한다. 줄이 조각으로 나뉘므로("AA" · "█" · "BB") "AA" 가
    //   든 조각만 보면 거기엔 애초에 블록이 없어 **무엇을 해도 통과한다** — 변이를 심어
    //   그 사실을 알았다(판 기하 슬라이스와 같은 종류의 공허함).
    assert!(
        painted_contains(&painted, "AA"),
        "캔버스가 프레임에 없다 — 단언이 공허해진다: {painted:?}"
    );
    assert!(
        painted_contains(&painted, "BB"),
        "블록을 비우며 뒤 글자까지 지웠다: {painted:?}"
    );
    assert!(
        !painted.iter().any(|t| t.contains('█')),
        "블록이 글자로도 그려졌다(사각형과 두 겹이 된다): {painted:?}"
    );
}

#[test]
fn a_measured_cell_size_is_kept_but_a_nonsense_one_is_not() {
    // 0 이나 무한대를 받으면 격자가 한 점으로 접히고, 증상은 "캔버스가 통째로 비었다"다.
    let (link, _tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    assert_eq!(view.cell_px.get(), None, "재기 전에는 없다");
    view.note_cell_size(8., 16.);
    assert_eq!(view.cell_px.get(), Some((8., 16.)));
    view.note_cell_size(0., 16.);
    view.note_cell_size(f32::INFINITY, 16.);
    assert_eq!(view.cell_px.get(), Some((8., 16.)), "말이 안 되는 값을 받았다");
}

/// 그려진 글자를 **가로 자리와 함께** 돌려준다 — 격자가 잡혔나를 재는 자리용.
///
/// # 왜 이것만은 폭을 잴 수 있나
///
/// 시험 폰트는 글자 폭이 0이라 셰이퍼가 놓는 자리는 못 잰다(위 절 머리말). 그런데
/// §10-21ⓙ 의 처방이 바로 **자리를 셰이퍼에서 뺏는 것**이다 — 못박은 조각의 가로 자리는
/// 우리가 준 칸너비가 정하므로 글꼴과 무관하다. 그래서 이 오라클은 **고친 뒤에야
/// 성립한다**: 종전 코드에서는 전부 0에 겹쳐 나온다.
fn painted_x(
    messages: Vec<ServerMessage>,
    cell: (f32, f32),
) -> Vec<(String, f32)> {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    warpui::App::test((), move |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        for msg in messages {
            tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
        }
        view.pump_headless();
        // 실행 경로에서는 `report_size` 가 자리표에서 재어 남긴다. 헤드리스에는 창이
        // 없으니 같은 값을 직접 놓는다 — 재는 자리가 아니라 **쓰는 자리**를 시험한다.
        view.note_cell_size(cell.0, cell.1);
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            scene
                .painted_texts()
                .map(|t| (t.text.clone(), t.bounds.origin().x()))
                .collect()
        })
    })
}

fn hangul_screen(text: &str) -> Vec<ServerMessage> {
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[[text, {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    vec![layout_one_pane(), screen]
}

#[test]
fn each_wide_glyph_lands_on_its_own_cell_boundary() {
    // ★ ⓙ 의 본체다. 한글 셋을 그리면 자리가 0·2칸·4칸이어야 한다 — 셰이퍼가 놓으면
    //   (시험 폰트에서) 전부 0 에 겹치고, 실제 글꼴에서는 진폭만큼 밀린다.
    let boxes = painted_x(hangul_screen("가나다"), (10., 20.));
    let xs: Vec<f32> = ["가", "나", "다"]
        .iter()
        .map(|ch| {
            boxes
                .iter()
                .find(|(t, _)| t == ch)
                .unwrap_or_else(|| panic!("{ch} 가 프레임에 없다: {boxes:?}"))
                .1
        })
        .collect();
    // 창 왼쪽 여백만큼 통째로 밀려 있으므로 **첫 글자를 기준**으로 잰다.
    assert_eq!(xs[1] - xs[0], 20., "둘째 글자가 두 칸 뒤가 아니다: {xs:?}");
    assert_eq!(xs[2] - xs[1], 20., "셋째 글자가 두 칸 뒤가 아니다: {xs:?}");
}

#[test]
fn a_wide_glyph_after_ascii_still_lands_on_the_grid() {
    // ASCII 는 고정폭 글꼴이 놓는다(시험 폰트에서는 폭 0). 그 **뒤에** 오는 한글이
    // 서로 두 칸씩 벌어지는지를 본다 — 조각 하나라도 안 못박히면 여기가 무너진다.
    let boxes = painted_x(hangul_screen("ab가나"), (10., 20.));
    let ga = boxes.iter().find(|(t, _)| t == "가").expect("가 없다").1;
    let na = boxes.iter().find(|(t, _)| t == "나").expect("나 없다").1;
    assert_eq!(na - ga, 20., "한글 사이가 두 칸이 아니다: {boxes:?}");
}

#[test]
fn the_grid_follows_the_measured_cell_width() {
    // 칸너비가 배율로 바뀌면(§10-21ⓐ) 격자도 따라와야 한다 — 상수를 박아 두면 배율을
    // 바꾼 순간 조용히 어긋난다.
    let wide = painted_x(hangul_screen("가나"), (20., 40.));
    let ga = wide.iter().find(|(t, _)| t == "가").unwrap().1;
    let na = wide.iter().find(|(t, _)| t == "나").unwrap().1;
    assert_eq!(na - ga, 40., "칸너비 20 이면 두 칸은 40 이다: {wide:?}");
}

// ── 팔레트 세 칸 · 설명 접기(§10-21ⓞ·ⓗ⑶) ───────────────────────────────────

fn palette_pieces(filter: &str) -> Vec<String> {
    let mut keys = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend(filter.chars().map(|c| (Key::Char(c), Mods::NONE)));
    painted_after(three_tabs(), &keys)
}

#[test]
fn a_palette_row_is_drawn_as_three_separate_pieces() {
    // ★ 제보의 요구가 "색으로 구분"이라 **조각이 갈려 있어야** 한다 — 한 덩이면 색이
    //   하나뿐이다. 이름과 옵션이 따로 그려지는지가 그 증거다.
    let pieces = palette_pieces("split-window");
    assert!(
        pieces.iter().any(|p| p == "split-window"),
        "이름 칸이 따로 안 그려졌다: {pieces:?}"
    );
    assert!(
        pieces.iter().any(|p| p == "-h"),
        "옵션 칸이 따로 안 그려졌다(이름에 붙어 있다): {pieces:?}"
    );
    assert!(
        !pieces.iter().any(|p| p.starts_with("split-window -h ")),
        "이름·옵션·설명이 아직 한 덩이다: {pieces:?}"
    );
}

#[test]
fn the_split_rule_is_the_core_one() {
    // 뷰가 자기 규칙으로 자르면 두 클라가 갈린다 — core 의 것을 쓰는지 값으로 확인한다.
    assert_eq!(proto::palette::split_name("split-window -h"), ("split-window", "-h"));
    let pieces = palette_pieces("select-pane");
    assert!(
        pieces.iter().any(|p| p == "-t next"),
        "여러 낱말 옵션이 한 덩이로 안 그려졌다: {pieces:?}"
    );
}

#[test]
fn a_long_description_wraps_instead_of_pushing_the_panel() {
    // ⓗ⑶ — 접어서 **보이고**, 판은 그대로. 접힌 줄은 설명 칸 아래에 이어 붙는다.
    //
    // ⚠ 설명은 서버가 준 것으로 넣는다. 코어 표의 설명은 이 폭에서 안 접히고, 접히지
    //   않는 표본으로는 이 오라클이 아무것도 안 잰다(공허해진다).
    let cols = SessionView::PANEL_COLS
        - (SessionView::PAL_NAME_COLS + SessionView::PAL_OPTS_COLS + 2);
    let long = "가나다라마바사 ".repeat(12);
    let lines = proto::palette::wrap(long.trim(), cols);
    assert!(lines.len() > 1, "이 설명은 접혀야 한다 — 오라클이 뜻을 잃는다");

    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [{"name": "verbose-thing", "desc": long.trim(), "cat": "설정/기타"}],
            "noarg": [], "menu_items": [], "settings": [], "setting_cats": []
        }
    }))
    .unwrap();
    let mut keys = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("verbose".chars().map(|c| (Key::Char(c), Mods::NONE)));
    let pieces = painted_after(vec![layout_one_pane(), status], &keys);
    for chunk in &lines {
        assert!(
            pieces.iter().any(|p| p == chunk),
            "접힌 줄이 안 그려졌다: {chunk:?} / {pieces:?}"
        );
    }
}

/// 그려진 **면**(배경 사각형)을 색과 세로 구간으로 돌려준다 — `(색, 위, 아래)`.
///
/// 왜 글자 오라클로 안 되나: 하이라이트가 한 덩이인지 줄마다 끊겼는지는 **면**의 성질이고,
/// 글자 기록(`painted_texts`)에는 그 정보가 없다. 끊긴 하이라이트는 글자로 재면 언제나
/// 초록이다(글자는 어느 쪽이든 다 그려진다 — pytmux-157 이 그렇게 안 잡혔다).
fn painted_fills(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<(ColorU, f32, f32)> {
    painted_scene(messages, keys, |scene| {
        scene
            .layers()
            .flat_map(|layer| layer.rects.iter())
            .filter_map(|r| match r.background {
                warpui::elements::Fill::Solid(c) => {
                    Some((c, r.bounds.origin().y(), r.bounds.lower_left().y()))
                }
                _ => None,
            })
            .collect()
    })
}

/// 고른 줄 배경(`SELECTED_BG`)으로 칠한 면들만.
///
/// ⚠ **이 색은 활성 탭도 쓴다**(`theme::ACTIVE` 와 같은 값 — `theme.rs` 머리말). 그래서
/// 목록의 하이라이트를 재려면 색만으로 좁히면 안 되고 **그 줄을 덮는 면**을 골라야 한다
/// (`fills_covering`) — 색으로만 세면 탭바까지 세어 단언이 조용히 어긋난다(실측 3조각).
fn selected_fills(messages: Vec<ServerMessage>, keys: &[(Key, Mods)]) -> Vec<(f32, f32)> {
    painted_fills(messages, keys)
        .into_iter()
        .filter(|(c, _, _)| *c == palette::SELECTED_BG)
        .map(|(_, top, bottom)| (top, bottom))
        .collect()
}

/// 그 세로 자리들 중 하나라도 덮는 면들.
fn fills_covering(fills: &[(f32, f32)], ys: &[f32]) -> Vec<(f32, f32)> {
    fills
        .iter()
        .copied()
        .filter(|(top, bottom)| ys.iter().any(|y| *y >= *top && *y < *bottom))
        .collect()
}

/// 접히는 설명 표본 — 낱말이 **전부 다르다**.
///
/// ⛔ 같은 낱말을 반복해서 만들면 안 된다. 접힌 조각끼리 같은 글이 되거나 한쪽이 다른
/// 쪽의 부분 문자열이 되어, 조각의 세로 자리를 찾을 때 **전부 첫 줄로 접힌다** — 그러면
/// "줄마다 배경" 변이를 넣어도 오라클이 통과한다(실측으로 그렇게 통과했다).
fn wrapping_desc() -> String {
    (1..=40).map(|i| format!("낱말{i:02}")).collect::<Vec<_>>().join(" ")
}

/// 그 글자가 **그대로**(부분 일치가 아니라) 그려진 세로 자리. 두 번 그려졌으면 실패다 —
/// 어느 쪽을 잰 것인지 모르는 값으로 기하를 단언하면 안 된다.
fn painted_y_exact(boxes: &[(String, f32)], needle: &str) -> f32 {
    let hits: Vec<f32> = boxes.iter().filter(|(t, _)| t == needle).map(|(_, y)| *y).collect();
    assert_eq!(hits.len(), 1, "{needle:?} 가 {}번 그려졌다: {boxes:?}", hits.len());
    hits[0]
}

#[test]
fn the_fill_oracle_itself_sees_the_selected_row() {
    // ★ 이 오라클이 먼저다 — 면 기록이 안 잡히면 아래 "한 덩이" 단언은 0 개를 보고도
    //   조용히 통과한다(부정 단언만 남는 오라클 금지).
    let fills = selected_fills(
        three_tabs(),
        &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)],
    );
    assert!(
        !fills.is_empty(),
        "고른 줄 배경이 한 면도 안 잡혔다 — 오라클이 죽었다"
    );
    assert!(
        fills.iter().all(|(top, bottom)| bottom > top),
        "높이가 0 인 면이 있다: {fills:?}"
    );
}

#[test]
fn a_wrapped_palette_row_is_one_unbroken_highlight() {
    // pytmux-157 — 접힌 설명이 **줄마다 따로** 칠해져 한 항목이 두 항목처럼 읽혔다.
    // 판의 열은 줄 사이에 `PANEL_ROW_SPACING` 을 두므로, 줄마다 배경을 깔면 그 간격에
    // 판 배경이 드러나 하이라이트가 끊긴다(실측 3px).
    let cols = SessionView::PANEL_COLS
        - (SessionView::PAL_NAME_COLS + SessionView::PAL_OPTS_COLS + 2);
    let long = wrapping_desc();
    let lines = proto::palette::wrap(&long, cols);
    assert!(lines.len() > 1, "이 설명은 접혀야 한다 — 오라클이 뜻을 잃는다");

    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [{"name": "verbose-thing", "desc": long, "cat": "설정/기타"}],
            "noarg": [], "menu_items": [], "settings": [], "setting_cats": []
        }
    }))
    .unwrap();
    let mut keys = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
    keys.extend("verbose".chars().map(|c| (Key::Char(c), Mods::NONE)));
    let msgs = vec![layout_one_pane(), status];

    // 필터가 하나만 남기므로 그 항목이 곧 고른 줄이다(단언의 전제).
    let pieces = painted_after(msgs.clone(), &keys);
    assert!(
        pieces.iter().filter(|p| *p == "verbose-thing").count() == 1,
        "이 필터가 한 항목만 남기지 않았다 — 단언이 뜻을 잃는다: {pieces:?}"
    );

    // 접힌 줄들이 실제로 그려진 세로 자리 — 하이라이트는 **이 자리들을** 덮어야 한다.
    let boxes = painted_boxes(msgs.clone(), &keys);
    let ys: Vec<f32> = lines.iter().map(|chunk| painted_y_exact(&boxes, chunk)).collect();
    assert!(
        ys.windows(2).all(|w| w[0] < w[1]),
        "접힌 줄들이 다른 자리에 안 그려졌다 — 단언이 뜻을 잃는다: {ys:?}"
    );

    let fills = selected_fills(msgs, &keys);
    let covering = fills_covering(&fills, &ys);
    assert_eq!(
        covering.len(),
        1,
        "접힌 항목의 하이라이트가 {}조각으로 갈렸다(줄마다 배경): {covering:?} / 줄 {ys:?}",
        covering.len()
    );

    // 그리고 그 한 면이 접힌 줄 **전부**를 덮어야 한다 — 첫 줄만 덮으면 여전히 두 개로
    // 읽힌다(조각 수만 세면 그 변이가 살아남는다).
    let (top, bottom) = covering[0];
    for (chunk, y) in lines.iter().zip(&ys) {
        assert!(
            *y >= top && *y < bottom,
            "접힌 줄이 하이라이트 밖에 있다: {chunk:?} y={y} / 면=({top}, {bottom})"
        );
    }
}

#[test]
fn a_wrapped_row_does_not_change_the_row_pitch() {
    // 한 덩이로 묶으면서 안쪽 행간을 판의 것과 다르게 두면, 접힌 항목만 높이가 어긋나
    // 「예산 = 줄 수」 셈과 실제 픽셀이 갈린다 — 그래서 상수가 한 벌이어야 한다.
    let cols = SessionView::PANEL_COLS
        - (SessionView::PAL_NAME_COLS + SessionView::PAL_OPTS_COLS + 2);
    let long = wrapping_desc();
    let lines = proto::palette::wrap(&long, cols);
    assert!(lines.len() >= 2, "이 표본은 접혀야 한다: {lines:?}");
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "하나", "active": true}],
        "plugin_surface": {
            "commands": [
                {"name": "verbose-thing", "desc": long, "cat": "설정/기타"},
                {"name": "brief-thing", "desc": "짧다", "cat": "설정/기타"}
            ],
            "noarg": [], "menu_items": [], "settings": [], "setting_cats": []
        }
    }))
    .unwrap();
    let msgs = vec![layout_one_pane(), status];
    // 그 항목의 하이라이트 높이 — 고른 줄에 실제로 그려진 글자 자리로 면을 고른다
    // (색만으로 좁히면 활성 탭이 섞인다 · `selected_fills` 머리말).
    let height = |filter: &str, rows: &[String]| {
        let mut keys = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
        keys.extend(filter.chars().map(|c| (Key::Char(c), Mods::NONE)));
        let boxes = painted_boxes(msgs.clone(), &keys);
        let ys: Vec<f32> = rows.iter().map(|row| painted_y_exact(&boxes, row)).collect();
        let fills = selected_fills(msgs.clone(), &keys);
        let covering = fills_covering(&fills, &ys);
        assert_eq!(
            covering.len(),
            1,
            "{filter}: 하이라이트가 한 면이 아니다: {covering:?} / 줄 {ys:?}"
        );
        covering[0].1 - covering[0].0
    };
    let one = height("brief", &["짧다".to_owned()]);
    let many = height("verbose", &lines);
    // 접힌 항목의 높이 = 한 줄 높이 × 줄 수 + 행간 × (줄 수 − 1). 종전 그림(줄마다 상자)과
    // 같은 값이라, 이 CL 은 **짬을 무엇이 칠하는가**만 바꿨다는 뜻이다.
    let rows = lines.len() as f32;
    let expected = one * rows + SessionView::PANEL_ROW_SPACING * (rows - 1.);
    assert!(
        (many - expected).abs() < 0.5,
        "접힌 항목의 높이가 판의 행간과 어긋난다: 한 줄 {one} · {rows}줄 {many} · 기대 {expected}"
    );
}

#[test]
fn wrapping_never_spends_more_rows_than_the_panel_has() {
    // 접히는 만큼 줄이 늘므로, 예산을 안 지키면 **접기 때문에** 판이 넘친다.
    let boxes = painted_boxes(three_tabs(), &[(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)]);
    let mut ys: Vec<i64> = boxes.iter().map(|(_, y)| y.round() as i64).collect();
    ys.sort_unstable();
    ys.dedup();
    // 창 600px · 줄 높이가 폰트에서 오므로 절대값은 못 박는다 — **넘치지 않음**만 본다.
    let bottom = ys.last().copied().unwrap_or(0);
    assert!(bottom <= 600, "판이 창 밖으로 나갔다(맨 아래 줄 y={bottom})");
}

#[test]
fn ctrl_tab_moves_the_panel_tab_while_a_panel_is_open() {
    // ⓗ⑷ — ⓕ 가 `Ctrl+Tab` 을 세션 전역으로 가져갔으므로 우선순위를 정해야 했다.
    // 판이 위에 있으면 판이 이긴다(화면이 떠 있으면 모든 키가 그 화면의 것 — core 규칙).
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_key(Key::Escape, Mods::NONE);
    view.handle_key(Key::Char(':'), Mods::NONE);
    assert_eq!(view.screens.top(), Some(Screen::Commands), "팔레트가 안 열렸다");
    let before = view.screens.palette_tab();
    view.alt_tab_step_for_test(true);
    assert_eq!(
        view.screens.palette_tab(),
        before + 1,
        "판이 떠 있는데 Ctrl+Tab 이 분류 탭을 안 옮겼다"
    );
    // 그리고 **세션 탭 스위처를 열지 않았다** — 그것이 이 우선순위의 요점이다.
    assert_eq!(view.screens.top(), Some(Screen::Commands), "판이 스위처로 바뀌었다");
}

#[test]
fn ctrl_tab_still_switches_session_tabs_when_no_panel_is_open() {
    // 반대쪽도 지킨다 — 판이 없으면 ⓕ 의 동선 그대로다(이 단언이 없으면 위 우선순위가
    // 세션 탭 전환을 통째로 죽여도 통과한다).
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert_eq!(view.screens.top(), None, "판이 없어야 한다");
    view.alt_tab_step_for_test(true);
    assert_eq!(view.screens.top(), Some(Screen::Tabs), "스위처가 안 열렸다");
}

// ── 작성창 빈 줄(§10-21ⓒ2) ──────────────────────────────────────────────────

/// 작성창을 `seed` 로 열고 **그려진 줄의 세로 자리들**을 돌려준다.
fn compose_rows(seed: &str) -> Vec<f32> {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    let seed = seed.to_owned();
    warpui::App::test((), move |mut app| async move {
        let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
        let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
        tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
        view.pump_headless();
        view.screens.open_compose(&seed);
        let (window_id, _handle) = app.add_window(WindowStyle::NotStealFocus, move |_| view);
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation { updated, ..Default::default() };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(800., 600.), 1., None, ctx);
            let mut ys: Vec<f32> = scene.painted_texts().map(|t| t.bounds.origin().y()).collect();
            ys.sort_by(|a, b| a.partial_cmp(b).unwrap());
            ys.dedup();
            ys
        })
    })
}

#[test]
fn consecutive_blank_lines_each_take_a_row() {
    // ★ 제보 그대로 — 빈 줄을 연달아 넣으면 그만큼 줄이 있어야 한다. 종전에는 자식이
    //   없는 행 상자의 높이가 0 이라 **커서가 놓인 빈 줄 하나만** 보였다.
    let one = compose_rows("a\nb").len();
    let three = compose_rows("a\n\n\n\nb").len();
    assert_eq!(
        three,
        one + 3,
        "빈 줄 셋이 자리를 안 차지했다(줄 자리: {one} → {three})"
    );
}

#[test]
fn a_blank_line_does_not_leak_into_the_text() {
    // ⚠ 그림으로 놓은 공백이 **내용**에 새면 보낸 글이 달라진다. 작성창의 글은
    //   `editor.lines()` 가 쥐고 있고 그림은 거기서 나오므로, 그 목록을 직접 본다.
    let (link, _tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    view.screens.open_compose("a\n\nb");
    let lines: Vec<String> = view
        .screens
        .editor()
        .expect("작성창이 안 열렸다")
        .lines()
        .to_vec();
    assert_eq!(lines, vec!["a".to_owned(), String::new(), "b".to_owned()]);
}


// ── 대문자 바인딩(§10-21ⓒ3) ─────────────────────────────────────────────────
//
// 제보는 "탭 고정이 GUI 에서 안 된다"였고, 핸드오프의 유력 가설은 **shift+글자가 대문자로
// 안 접힌다**(그래서 표의 `shift-P` 와 안 맞는다)였다. 그 가설은 **틀렸다** — 상류
// `get_input_key` 가 이미 접는다(`warpui/.../key_events.rs`: *"If the key is a character
// AND shift is pressed, we force the key to uppercase"*). 아래가 그 사실을 못박는다.

#[test]
fn esc_shift_p_sends_the_pin_command() {
    // ★ 제보의 동선 그대로 — 이 줄이 초록이면 **클라 쪽은 끝까지 간다**(라이브 컷도 같다).
    let out = sent_after(
        three_tabs(),
        &[(Key::Escape, Mods::NONE), (Key::Char('P'), Mods::NONE)],
    );
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Command(Command::TogglePin { .. }))),
        "esc Shift+P 가 고정 명령을 안 보냈다: {out:?}"
    );
    // ★ **자리를 실어야 원격 탭에도 걸린다**(§10-21ⓒ3 — 사용자 답: "원격 탭이 핀이
    //   안 됨"). 서버의 기본값(`sess.active_index`)은 로컬 탭의 자리라 원격에서 어긋난다.
    let idx = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::TogglePin { index }) => Some(*index),
        _ => None,
    });
    assert_eq!(idx, Some(Some(0)), "활성 탭의 자리를 안 실었다: {out:?}");
}

#[test]
fn every_uppercase_binding_is_reachable_from_a_shifted_keystroke() {
    // 이것은 **부류**다: 표의 `shift-<대문자>` 가 여덟이고, 키 변환이 한 자리에서
    // 어긋나면 여덟이 한꺼번에 죽는다(그리고 조용하다). 상류가 주는 모양
    // (`key: "P"` + `shift: true`)을 그대로 넣어 표까지 닿는지 본다.
    for (name, ch) in [
        ("shift-G", 'G'),
        ("shift-H", 'H'),
        ("shift-J", 'J'),
        ("shift-K", 'K'),
        ("shift-L", 'L'),
        ("shift-P", 'P'),
        ("shift-R", 'R'),
        ("shift-T", 'T'),
    ] {
        let ks = ks(&ch.to_string(), false, false, true);
        let (key, mods) =
            SessionView::key_from_keystroke(&ks).unwrap_or_else(|| panic!("{name}: 키를 못 만들었다"));
        assert_eq!(key, Key::Char(ch), "{name}: 대문자로 안 접혔다");
        assert_eq!(
            base::keys::binding_name_with(key, mods).as_deref(),
            Some(name),
            "{name}: 표 이름으로 안 돌아간다"
        );
    }
}

#[test]
fn pinning_a_remote_tab_carries_that_tabs_merged_index() {
    // ★ **제보의 자리다**(§10-21ⓒ3 · 사용자 답 2026-08-03: "원격 탭이 핀이 안 됨").
    //
    // 서버는 자리를 안 실으면 `sess.active_index` 로 접는데 그것은 **로컬 탭**의 자리다.
    // 원격(병합) 탭이 활성이면 그 값은 보고 있는 탭이 아니라서 토글이 엉뚱한 로컬 탭에
    // 걸린다 — 화면에서는 "원격 탭만 핀이 안 된다"로 보인다. 정본은 그래서 활성 탭의
    // **병합 index** 를 명시해 보낸다(`clientcmd.py` 의 주석이 그 함정을 적어 뒀다).
    let status: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [
            {"index": 0, "name": "로컬"},
            {"index": 1, "name": "⇄box:원격", "remote": true, "active": true},
        ]
    }))
    .unwrap();
    let out = sent_after(
        vec![layout_one_pane(), status],
        &[(Key::Escape, Mods::NONE), (Key::Char('P'), Mods::NONE)],
    );
    let idx = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::TogglePin { index }) => Some(*index),
        _ => None,
    });
    assert_eq!(
        idx,
        Some(Some(1)),
        "원격 탭이 활성인데 그 자리를 안 실었다 — 서버가 로컬 탭을 고정한다: {out:?}"
    );
}


// ── 영어 화면에 한국어가 안 남는가(§10-21ⓖ2) ────────────────────────────────
//
// 제보: *"영어를 골랐는데 화면에 한국어가 섞인다"*(확인 판의 버튼 `취소` · 안내줄).
// 원인은 번역 누락이었고, 핸드오프의 판정은 **게이트가 없다**였다 — 로케일 래칫은
// "소켓을 건너는 한국어"를 세지 **우리가 그리는 글**을 안 센다.
//
// 여기서는 **그려진 프레임**을 잰다. 어떤 경로로 그 글이 왔든(정적 표·`t()`·서버가 준
// 글) 화면에 한글이 남으면 잡힌다 — 사용자가 보는 것과 같은 자리에서 재는 것이 요점이다.

fn hangul(text: &str) -> bool {
    text.chars().any(|c| ('\u{ac00}'..='\u{d7a3}').contains(&c))
}

/// 탭 이름이 **ASCII** 인 상태 — 사용자 자료(탭 이름·패널 글)는 번역 대상이 아니라서
/// 표본에 한글을 두면 이 오라클이 자기 자료에 걸린다.
fn ascii_tabs() -> Vec<ServerMessage> {
    vec![
        layout_one_pane(),
        serde_json::from_value(serde_json::json!({"t": "status", "windows": [
            {"index": 0, "name": "one", "active": true},
            {"index": 1, "name": "two"},
        ]}))
        .unwrap(),
    ]
}

/// 그 화면을 **영어로** 열어 그려진 조각 중 한글이 든 것을 돌려준다.
fn english_hangul(keys: &[(Key, Mods)]) -> Vec<String> {
    base::i18n::with_locale("en", || {
        painted_after(ascii_tabs(), keys)
            .into_iter()
            .filter(|t| hangul(t))
            .collect()
    })
}

fn typed(text: &str) -> Vec<(Key, Mods)> {
    text.chars().map(|c| (Key::Char(c), Mods::NONE)).collect()
}

#[test]
fn the_english_locale_actually_switches_something() {
    // ★ 공허 방지 — 로케일이 안 바뀌면 아래가 전부 "한글투성이"로 붉거나(그건 낫다)
    //   반대로 표본이 비어 조용히 통과할 수 있다. 먼저 스위치가 도는지 본다.
    let ko = base::i18n::with_locale("ko", || base::Action::Quit.label().to_owned());
    let en = base::i18n::with_locale("en", || base::Action::Quit.label().to_owned());
    assert!(hangul(&ko), "ko 라벨이 한국어가 아니다: {ko}");
    assert!(!hangul(&en), "en 으로 안 바뀌었다: {en}");
}

#[test]
fn no_korean_survives_on_the_confirm_screen() {
    // ★ **제보의 그 화면**이다(`Kill tab` 판). 되돌릴 수 없는 것 앞의 화면이라
    //   어느 쪽이 "아니오"인지 못 읽으면 그 화면의 취지가 무너진다.
    let left = english_hangul(&[(Key::Char('b'), Mods::CTRL), (Key::Char('&'), Mods::NONE)]);
    assert!(left.is_empty(), "확인 판에 한국어가 남았다: {left:?}");
}

#[test]
fn no_korean_survives_on_the_common_screens() {
    let screens: &[(&str, Vec<(Key, Mods)>)] = &[
        ("키 도움말", vec![(Key::Escape, Mods::NONE), (Key::Char('?'), Mods::NONE)]),
        ("탭 스위처", vec![(Key::Escape, Mods::NONE), (Key::Tab, Mods::NONE)]),
        ("트리", vec![(Key::Char('b'), Mods::CTRL), (Key::Char('w'), Mods::NONE)]),
        ("메뉴", vec![(Key::Char('b'), Mods::CTRL), (Key::Enter, Mods::NONE)]),
        ("팔레트", vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)]),
        ("설정", {
            let mut k = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
            k.extend(typed("settings"));
            k.push((Key::Enter, Mods::NONE));
            k
        }),
        ("알림 이력", {
            let mut k = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
            k.extend(typed("notice-history"));
            k.push((Key::Enter, Mods::NONE));
            k
        }),
        ("버퍼", {
            let mut k = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
            k.extend(typed("choose-buffer"));
            k.push((Key::Enter, Mods::NONE));
            k
        }),
        ("키 바인딩 목록", {
            let mut k = vec![(Key::Escape, Mods::NONE), (Key::Char(':'), Mods::NONE)];
            k.extend(typed("list-keys"));
            k.push((Key::Enter, Mods::NONE));
            k
        }),
    ];
    let mut bad = Vec::new();
    for (name, keys) in screens {
        let left = english_hangul(keys);
        if !left.is_empty() {
            bad.push(format!("{name}: {left:?}"));
        }
    }
    assert!(
        bad.is_empty(),
        "영어 로케일인데 한국어가 남은 화면:\n  {}\n\
         카탈로그(`base/src/i18n/en_*.rs`)에 그 원문의 영어 짝을 더할 것.",
        bad.join("\n  ")
    );
}

// ── 조합 중인 글자(preedit) · §10-21ⓞ2 ⑵ ─────────────────────────────────────
//
// 확정만 받으면 사람이 `ㅎ`→`하`→`한` 을 만드는 동안 **화면이 비어 있다**. 상류는 그
// 상태를 이미 주고 있었고(winit `Ime::Preedit` → `SetMarkedText`) 받는 자리가 없었다.
// 라이브로도 확인했지만 조합은 1초 안에 확정돼 캡처 타이밍에 걸리므로, 판정은 여기서 한다.

/// 조합 중인 글자를 얹은 캔버스. 그림이 아니라 **칸의 내용**을 본다.
///
/// ⚠ `overlay_preedit` 를 직접 부르지 않는다 — **`render` 가 쓰는 길과 같은 길**로 얻어야
/// "얹는 호출을 지웠다"가 여기서 죽는다(그 뮤테이션이 종전엔 전부 초록이었다).
fn canvas_with_preedit(preedit: &str, cursor: (u16, u16)) -> proto::canvas::Canvas {
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(cursor.0, cursor.1)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_preedit(preedit);
    view.composite_for_paint().expect("합성이 없다")
}

#[test]
fn the_composing_text_is_drawn_at_the_cursor() {
    let canvas = canvas_with_preedit("한", (3, 2));
    let cell = canvas.cell(3, 2).expect("칸이 없다");
    assert_eq!(cell.ch, '한', "조합 중인 글자가 커서 자리에 없다 — 사람은 자기가 무엇을 치는지 못 본다");
    // ⚠ 표시는 **이 클라가 실제로 그리는 것**이라야 한다. 밑줄이 자연스러운 선택이지만
    //    `colors()` 는 fg·bg·reverse 만 본다 — 밑줄만 세우면 조합 글자가 확정 글자와
    //    똑같이 보이고, 오라클은 초록인데 화면은 아무 말도 안 하는 상태가 된다.
    assert!(cell.style.reverse, "그려지는 표시가 없으면 확정된 글자와 구분되지 않는다");
    // 넓은 글자의 뒤 칸은 연속 셀이라야 한다(pytmux-17 과 같은 규칙 — 안 그러면 런이 끊긴다).
    assert!(canvas.cell(4, 2).is_some_and(|c| c.continuation), "뒤 칸을 안 잡았다");
}

#[test]
fn clearing_the_composition_removes_it_from_the_screen() {
    // 확정·취소되면 상류가 빈 문자열로 부른다. 안 지우면 **조합 잔상**이 화면에 남는다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_with_cursor(3, 2)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.handle_preedit("한"), "첫 조합이 상태를 안 세웠다");
    assert!(view.handle_preedit(""), "지우기가 상태를 안 바꿨다");
    let canvas = view.composite_for_paint().unwrap();
    assert_ne!(canvas.cell(3, 2).map(|c| c.ch), Some('한'), "조합 잔상이 남았다");
}

#[test]
fn the_composing_text_never_reaches_the_pane() {
    // ⛔ 이것이 이 기능의 안전 조건이다. 조합 중 문자열을 흘리면 셸이 **자모를** 받아
    //    `치명ㄷ` 부류가 된다(`ime.rs` 머리말의 사고). 그릴 뿐 보내지 않는다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    for step in ["ㅎ", "하", "한"] {
        view.handle_preedit(step);
        view.pump_headless();
    }
    assert!(sent.lock().unwrap().is_empty(), "조합 중인 글자가 서버로 샜다: {:?}", sent.lock().unwrap());
}

#[test]
fn the_same_composition_twice_does_not_redraw() {
    // 조합은 자판마다 **같은 문자열**로 여러 번 온다(실측: 한 음절에 3회). 매번 다시
    // 그리면 프레임이 헛돈다.
    let (mut view, _tx, _sent) = harness();
    assert!(view.handle_preedit("한"));
    assert!(!view.handle_preedit("한"), "같은 조합인데 다시 그린다고 했다");
}

#[test]
fn the_composition_is_clipped_at_the_panes_right_edge() {
    // 좌우 분할에서 넘겨 쓰면 **옆 패널을 침범한다**. 경계는 화면 폭이 아니라 그 패널의 것이다.
    let (mut view, tx, _sent) = harness();
    let split = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 4, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 10, "h": 4, "title": "sh", "active": true},
                  {"id": 2, "x": 10, "y": 0, "w": 70, "h": 4, "title": "sh2"}]
    })).unwrap();
    for msg in [split, screen_with_cursor(8, 0)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.handle_preedit("한글");
    let canvas = view.composite_for_paint().unwrap();
    assert_eq!(canvas.cell(8, 0).map(|c| c.ch), Some('한'), "첫 글자는 들어가야 한다");
    assert_ne!(canvas.cell(10, 0).map(|c| c.ch), Some('글'), "패널 경계를 넘어 그렸다");
}

// ── 상태줄 표식을 누르면 그 판이 열린다 · pytmux-20 ──────────────────────────
//
// 종전에는 이 칩들이 **그리기만** 했다(누르는 자리는 Tier C 화면이 와야 생긴다고 적혀
// 있었다). 한도 판이 그 화면을 내면서 조건이 섰다. ⚠ 우리는 **무엇이 열리는지 모른다** —
// 표식이 실어 온 이름을 그대로 되돌려 보낸다. 그 무지가 이 설계의 요점이다.

/// 상태줄 표식 둘을 실은 status — 하나는 누를 수 있고(`do`) 하나는 아니다.
fn status_with_plugin_badges() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "status",
        "plugin_badges": [
            {"name": "rec", "text": " REC ", "theme": {"b": "error"}},
            {"name": "claude-code", "kind": "usage", "text": "12%/5h 사용",
             "theme": {"b": "secondary"}, "do": "usage-panel"}
        ]
    }))
    .unwrap()
}

#[test]
fn clicking_a_plugin_badge_asks_the_server_for_the_screen_it_named() {
    let (mut view, tx, sent) = harness();
    for msg in [layout_one_pane(), status_with_plugin_badges()] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert_eq!(view.state.plugin_badges().len(), 2, "표식이 안 들어왔다(전제 실패)");
    view.chrome_click(base::chrome::ClickTarget::PluginBadge(1));
    view.pump_headless();
    let asked: Vec<String> = sent
        .lock()
        .unwrap()
        .iter()
        .filter_map(|o| match o {
            Outgoing::Command(Command::PluginOpen { name, .. }) => Some(name.clone()),
            _ => None,
        })
        .collect();
    assert_eq!(asked, vec!["usage-panel".to_owned()], "표식이 실어 온 이름으로 안 물었다");
}

#[test]
fn a_badge_without_a_screen_is_not_a_button() {
    // ⚠ `do` 가 없는 표식(REC·모델·경고)을 눌러도 아무 일이 없어야 한다 — 눌리는
    //   것처럼 보이고 아무 일도 안 나는 칸을 안 만드는 것이 이 규약의 절반이다.
    let (mut view, tx, sent) = harness();
    for msg in [layout_one_pane(), status_with_plugin_badges()] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::PluginBadge(0));
    view.pump_headless();
    assert!(
        !sent
            .lock()
            .unwrap()
            .iter()
            .any(|o| matches!(o, Outgoing::Command(Command::PluginOpen { .. }))),
        "화면이 없는 표식을 눌렀는데 서버에 물었다"
    );
}

#[test]
fn a_stale_badge_index_does_nothing_instead_of_opening_the_wrong_thing() {
    // 프레임 사이에 목록이 바뀌면 낡은 자리를 누를 수 있다. 그때 **짐작하지 않는다** —
    // 엉뚱한 판이 열리는 것은 아무 일도 안 일어나는 것보다 나쁘다.
    let (mut view, tx, sent) = harness();
    for msg in [layout_one_pane(), status_with_plugin_badges()] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.chrome_click(base::chrome::ClickTarget::PluginBadge(99));
    view.pump_headless();
    assert!(
        !sent
            .lock()
            .unwrap()
            .iter()
            .any(|o| matches!(o, Outgoing::Command(Command::PluginOpen { .. }))),
        "없는 자리를 눌렀는데 무언가를 열었다"
    );
}

#[test]
fn the_limit_bars_are_pinned_to_the_cell_grid_too() {
    // 한도 막대(`█▏▎…░`)도 글자 그림이다 — 밀리면 **값을 잘못 읽게 만든다**(pytmux-9 ⑵
    // 와 같은 규칙이 이 판에도 걸려야 한다).
    let segs = SessionView::grid_segments("Session 5h ███░░  42%");
    let boxed: Vec<&str> = segs
        .iter()
        .filter(|(p, _)| p.chars().any(|c| !c.is_ascii()))
        .map(|(p, _)| p.as_str())
        .collect();
    assert_eq!(boxed, vec!["█", "█", "█", "░", "░"], "막대 칸을 못 골랐다: {segs:?}");
}

// ── 팔레트 입력줄이 인자를 먹는다 · pytmux-7 ─────────────────────────────────
//
// 제보: *"명령 인자는 정본 TUI 처럼 그 줄에서 이어 친다 — 별도 입력 팝업을 띄우지 않는다."*
// 실패가 조용한 자리 둘: ⑴ 인자를 쳤는데 필터가 통째로 걸려 목록이 비면 아무것도 못 고른다
// ⑵ 이어 쳤는데도 판이 뜨면 사용자는 자기가 친 인자를 **다시 친다**.

/// 팔레트를 열고 `line` 을 친 뒤 `Enter` 까지 — 키 경로 그대로.
fn palette_typed(view: &mut SessionView, line: &str) {
    view.handle_key(Key::Escape, Mods::NONE);
    view.handle_key(Key::Char(':'), Mods::NONE);
    for c in line.chars() {
        view.handle_key(Key::Char(c), Mods::NONE);
    }
    view.pump_headless();
}

#[test]
fn typing_an_argument_does_not_narrow_the_list_away() {
    // ★ 인자까지 걸러 버리면 `remote-attach box1` 을 치는 순간 목록이 **빈다**.
    //   자르는 자리는 core 한 벌(`split_first_space`)이고, 목록은 이름 쪽만 본다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    palette_typed(&mut view, "remote-attach box1");
    assert_eq!(view.screens.typed_filter(), "remote-attach");
    assert_eq!(view.screens.typed_arg(), "box1");
    let hits = view.palette_hits(view.screens.palette_cat(), view.screens.typed_filter());
    assert!(!hits.is_empty(), "인자를 쳤더니 목록이 비었다 — 고를 것이 사라진다");
}

#[test]
fn an_inline_argument_runs_the_command_without_opening_a_prompt() {
    // ★ 이 오라클이 제보 그 자체다. 이어 쳤는데 판이 뜨면 사용자는 인자를 두 번 친다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    palette_typed(&mut view, "rename-tab 작업");
    view.handle_key(Key::Enter, Mods::NONE);
    view.pump_headless();
    assert_eq!(view.screens.top(), None, "인자를 이어 쳤는데 판이 떴다");
    let renamed = sent.lock().unwrap().iter().any(|o| {
        matches!(o, Outgoing::Command(Command::RenameWindow { name, .. }) if name == "작업")
    });
    assert!(renamed, "친 인자가 서버로 안 갔다: {:?}", sent.lock().unwrap());
}

#[test]
fn picking_without_an_argument_still_opens_the_prompt() {
    // ⚠ 이어 치는 길은 **더하는 것**이지 다른 길을 없애는 것이 아니다 — 인자 이력이
    //   그 판에 붙어 있다(`arghist`). 없애면 지난 값을 꺼낼 자리가 사라진다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    palette_typed(&mut view, "rename-tab");
    view.handle_key(Key::Enter, Mods::NONE);
    view.pump_headless();
    assert_eq!(
        view.screens.top(),
        Some(base::screens::Screen::Prompt),
        "인자 없이 골랐는데 물음 판이 안 떴다"
    );
}

#[test]
fn the_argument_hint_rides_the_panel_footer() {
    // ⑶ "입력을 방해하지 않는 선에서 도움말" — 판이 안 늘어나는 자리는 안내줄뿐이다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    palette_typed(&mut view, "rename-tab");
    let panel = view
        .render_screen_panel(base::screens::Screen::Commands)
        .debug_text_content()
        .expect("팔레트 판에 글자가 없다");
    assert!(
        panel.contains("이어서 치기"),
        "인자를 받는 명령을 골랐는데 안내가 없다:\n{panel}"
    );
}

// ── Status 판 · pytmux-9 ⑵ 정렬 ⑶ 진짜 탭 ────────────────────────────────────
//
// 두 제보의 뿌리가 하나다: 이 판은 **글자 그림**을 그대로 찍고 있었다. 그래프도 글자고
// (그래서 폴백 글꼴의 진폭에 밀린다) 탭도 글자였다(그래서 탭처럼 안 보인다).

#[test]
fn the_status_tabs_are_real_tabs_not_boxed_text() {
    // ★ 판정은 "탭줄이 **클릭되는 표적**을 갖는가"다. 글자에 배경만 씌운 종전 판은
    //   눌러도 아무 일이 없었다 — 알약처럼 보이게만 고치면 그건 또 다른 거짓말이다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.screens.open_info_tabs();
    let before = view.screens.info_tab();
    // 둘째 탭을 누른다(첫 탭이 이미 골라져 있으니 바뀌어야 보인다).
    view.screens.panel_click(base::PanelTarget::InfoTab(before + 1));
    assert_eq!(
        view.screens.info_tab(),
        before + 1,
        "Status 탭을 눌러도 안 바뀐다 — 탭처럼 보이기만 하는 것은 고친 것이 아니다"
    );
}

#[test]
fn switching_status_tabs_does_not_carry_the_old_cursor() {
    // 탭마다 줄 수가 다르다 — 커서 자리를 물려받으면 짧은 탭에서 **없는 줄**을 가리킨다.
    //
    // ⚠ 종전에는 이 판의 `↑↓` 가 글 굴리기라 `scroll` 을 쟀다. 이제는 **항목 커서**다
    //   (pytmux-373 ⑶ · 정본 `ListView` 와 같다) — 굴리기는 그 커서의 부수 효과이고,
    //   탭을 바꿀 때 되돌려야 하는 것도 그 커서다.
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_tall_pane()))).unwrap();
    view.pump_headless();
    view.apply_action_for_test(base::Action::ShowInfoTabs);
    let opened = view.screens.info_row();
    for _ in 0..3 {
        view.handle_key(Key::Down, Mods::NONE);
    }
    assert_ne!(view.screens.info_row(), opened, "전제 실패 — 커서가 안 내려갔다");
    view.panel_click(base::PanelTarget::InfoTab(1));
    assert_eq!(view.screens.scroll(), 0, "다른 탭의 스크롤 자리를 물려받았다");
    assert_eq!(
        view.screens.info_row(),
        0,
        "다른 탭의 커서 자리를 물려받았다 — 그 탭에 동작 줄이 없으면 첫 줄이라야 한다"
    );
}

#[test]
fn the_status_lines_are_pinned_to_the_cell_grid() {
    // ★ pytmux-9 ⑵. RTT 그래프는 세로 막대(`▁▂▃…`)와 축(`┤┄─`)이 섞인 **글자 그림**
    //   이라, 줄을 통짜로 셰이퍼에 넘기면 폴백 글꼴의 진폭에 밀려 축과 어긋난다.
    //   `mono_row` 가 비 ASCII 조각마다 칸을 못박는지를 **조각 나누기**로 잰다
    //   (그림은 못 보지만, 못박을 대상을 고르는 판정은 잴 수 있다).
    let segs = SessionView::grid_segments("  12 ┤▁▂█ ");
    // ASCII 는 한 덩이로 이어지고, 비 ASCII 는 **낱개**로 갈려 각자 칸을 받는다.
    let boxed: Vec<&str> = segs
        .iter()
        .filter(|(p, _)| p.chars().any(|c| !c.is_ascii()))
        .map(|(p, _)| p.as_str())
        .collect();
    assert_eq!(boxed, vec!["┤", "▁", "▂", "█"], "못박을 조각을 못 골랐다: {segs:?}");
}

// ── Status 판의 «정돈» · pytmux-373 ⑵⑶⑷ ──────────────────────────────────────
//
// 제보의 표가 다섯 줄로 갈랐다: 닫기 없음 · 동작 막대 없음 · `[c]`/`[o]` 가 고를 수 없음 ·
// 꼬리줄이 `↑↓ 스크롤` 이라고 말함 · 아래가 통째로 빔. 아래는 그 다섯을 각각 잰다.

/// REC 플러그인이 도는 상태(= status 에 `capture` 칸이 온다). 그때만 REC 탭과
/// `[c]`/`[o]` 동작이 선다(정본 delete-to-disable 동형).
fn rec_running() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "status", "windows": [],
        "capture": true, "capture_path": "/tmp/rec/pane1.log", "capture_size": 2048
    }))
    .unwrap()
}

/// Status 판을 REC 탭으로 펴 놓은 뷰와, 그 뷰가 **서버로 보낸 것**.
fn view_with_status_panel() -> (SessionView, Sent) {
    let (mut view, tx, sent) = harness();
    for msg in [layout_tall_pane(), rec_running()] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.apply_action_for_test(base::Action::ShowInfoTabs);
    view.pump_headless();
    (view, sent)
}

/// Status 판이 담은 글을 **줄 단위**로. 자리를 재려면 줄이 필요하다(어느 줄에 있나).
fn status_panel_lines(view: &SessionView) -> Vec<String> {
    view.render_screen_panel(base::screens::Screen::InfoTabs)
        .debug_text_content()
        .expect("Status 판에 글자가 없다")
        .lines()
        .map(str::to_owned)
        .collect()
}

#[test]
fn the_status_panel_has_a_close_button_and_a_close_bar() {
    // ⑷ 오른쪽 위 `[x]` 와 ⑵ 바닥 「닫기」 막대 — 정본 `#itclose`·`#itclosebtn`.
    //    둘 다 **없었다**: 마우스로 닫는 길이 하나도 없었고, 아래는 통째로 비어 있었다.
    //
    // ☠ **판 전체에서 글자를 찾으면 안 된다** — 꼬리줄이 이미
    //    `(←→ 탭·닫기[x] · …)` 라 `"[x]"` 도 `"닫기"` 도 **거기서** 걸린다. 실제로
    //    처음에 그렇게 적었더니 `[x]` 를 통째로 지운 변이가 초록으로 통과했다.
    //    그래서 재는 것은 **자리**다: `[x]` 는 탭줄 오른쪽 끝, 「닫기」는 제 줄 하나.
    let (view, _sent) = view_with_status_panel();
    let lines = status_panel_lines(&view);
    let tabbar = lines
        .iter()
        .find(|l| l.contains("출력 캡처(REC)"))
        .unwrap_or_else(|| panic!("탭줄을 못 찾았다:\n{}", lines.join("\n")));
    assert!(
        tabbar.contains("[x]"),
        "오른쪽 위 닫기가 탭줄에 없다: {tabbar:?}"
    );
    assert!(
        tabbar.trim_end().ends_with("[x]"),
        "닫기가 오른쪽 **끝**이 아니다(정본 `#itgap` 이 미는 자리다): {tabbar:?}"
    );
    let close_bar = lines.iter().position(|l| l.trim() == "닫기");
    let bar = close_bar.unwrap_or_else(|| panic!("바닥 닫기 막대가 없다:\n{}", lines.join("\n")));
    // 막대는 **본문 아래**다(정본은 목록 다음 줄에 둔다) — 위로 올라가면 목록을 자른다.
    let last_body = lines
        .iter()
        .rposition(|l| l.trim() == "" || l.contains("탭 매핑"))
        .expect("본문이 없다");
    assert!(bar > last_body, "닫기 막대가 본문 위에 있다: {bar} <= {last_body}");
}

#[test]
fn the_close_button_actually_closes_by_key_and_by_click() {
    // ⛔ 그려지기만 하고 안 눌리면 그것은 **또 다른 거짓말**이다(pytmux-9 ⑶ 이 같은
    //    자리에서 배운 것). 키 동선(`←` 로 `[x]` → `Enter`)과 클릭 둘 다 잰다.
    let (mut view, sent) = view_with_status_panel();
    view.handle_key(Key::Left, Mods::NONE);       // 첫 탭에서 왼쪽 = `[x]`
    assert!(view.screens.info_close_focused(), "←→ 로 `[x]` 에 못 간다");
    view.handle_key(Key::Enter, Mods::NONE);
    assert_eq!(view.screens.top(), None, "`[x]` 에서 Enter 를 눌렀는데 안 닫혔다");
    // 클릭도 **같은 표적**을 지난다(클릭에만 있는 지름길을 안 만든다).
    let (mut view, sent) = view_with_status_panel();
    view.panel_click(base::PanelTarget::InfoClose);
    assert_eq!(view.screens.top(), None, "닫기를 눌렀는데 안 닫혔다");
}

#[test]
fn the_rec_actions_are_rows_you_can_pick_not_a_dim_line() {
    // ⑶ 정본에서 `[c]`/`[o]` 는 **고를 수 있는 항목**(`▸ …`)이고, 우리 쪽은 `proto` 가
    //    얹어 준 흐린 글자 한 줄이었다 — 키를 직접 치는 수밖에 없었다.
    let (mut view, sent) = view_with_status_panel();
    let panel = view
        .render_screen_panel(base::screens::Screen::InfoTabs)
        .debug_text_content()
        .expect("Status 판에 글자가 없다");
    assert!(panel.contains("▸ [c] 캡처 켜기/끄기"), "동작이 항목으로 안 섰다:\n{panel}");
    assert!(panel.contains("▸ [o] 기록 폴더 열기"), "동작이 항목으로 안 섰다:\n{panel}");
    // ⛔ 그리고 그 둘이 **줄로도 남아 있으면** 같은 말이 두 번 뜬다(`proto` 가 얹던 줄).
    assert!(
        !panel.contains("[c] 캡처 켜기/끄기 · [o] 기록 폴더 열기"),
        "옛 안내 줄이 아직 남아 있다 — 같은 말이 두 번 뜬다:\n{panel}"
    );
    // 커서는 **첫 내용 줄**에서 시작한다(정본 `lv.index = len(acts)`) — 판을 열자마자
    // 동작 단추가 골라져 있으면 `Enter` 한 번이 캡처를 토글한다.
    assert_eq!(view.screens.info_row(), 2, "커서가 동작 단추 위에서 시작한다");
    // ↑ 두 번이면 첫 동작 줄, 거기서 Enter 면 **캡처 토글이 나가고 판은 그대로**다.
    view.handle_key(Key::Up, Mods::NONE);
    view.handle_key(Key::Up, Mods::NONE);
    assert_eq!(view.screens.info_row(), 0);
    view.handle_key(Key::Enter, Mods::NONE);
    assert_eq!(
        view.screens.top(),
        Some(base::screens::Screen::InfoTabs),
        "동작을 골랐는데 판이 닫혔다 — 결과를 볼 곳이 없다"
    );
    view.pump_headless();   // 큐를 실제로 흘린다 — 안 흘리면 아래 단언이 공허하다
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter()
            .any(|o| matches!(o, Outgoing::Command(Command::SetCapture))),
        "동작 줄에서 Enter 를 눌렀는데 캡처 토글이 안 나갔다: {out:?}"
    );
}

#[test]
fn picking_a_plain_line_closes_the_status_panel() {
    // 정본과 같은 손 — 동작이 아닌 줄에서 `Enter` 는 닫기다(`InfoScreen` 계열의 가벼운 닫힘).
    let (mut view, sent) = view_with_status_panel();
    view.handle_key(Key::Down, Mods::NONE);   // 내용 줄
    view.handle_key(Key::Enter, Mods::NONE);
    assert_eq!(view.screens.top(), None, "내용 줄에서 Enter 가 안 닫는다");
}

#[test]
fn the_hint_line_says_items_because_that_is_what_up_down_does_now() {
    // ⑵ 의 넷째 줄 — 꼬리줄이 `↑↓ 스크롤` 이라 적혀 있었고 그것이 **정본과 갈린 자리**였다.
    //    문구만 고치면 그건 또 다른 거짓말이라, 문구와 **동작을 같이** 잰다.
    let hint = base::screens::Screen::InfoTabs.hint();
    assert!(hint.contains("↑↓ 항목"), "꼬리줄이 아직 굴리기라고 말한다: {hint}");
    assert!(hint.contains("닫기[x]"), "꼬리줄이 `[x]` 동선을 안 말한다: {hint}");
    let (mut view, sent) = view_with_status_panel();
    let before = view.screens.info_row();
    view.handle_key(Key::Down, Mods::NONE);
    assert_eq!(view.screens.info_row(), before + 1, "↑↓ 가 항목을 안 옮긴다");
    assert_eq!(view.screens.scroll(), 0, "굴리기가 커서와 따로 논다");
}

// ── Status 판의 **높이**는 탭을 바꿔도 안 변한다 · pytmux-373 ⑴ ───────────────
//
// ☠ **종전 오라클은 줄 수만 쟀다**(`panel_budget_for_test`·`pad_rows_count_for_test`).
//    이 결함은 **줄 수가 맞는데 픽셀이 안 맞는** 부류라 그 둘로는 전건 통과했다 — 루트
//    `CLAUDE.md` 의 *"값을 만드는 헬퍼만 테스트하면 그 값을 붙이는 호출을 지워도 통과한다"*
//    가 정확히 이 자리다. 그래서 여기서는 **판이 실제로 칠한 면**을 잰다.
//
// ⚠ 시험 폰트는 글자 폭이 0이라 가로는 못 잰다 — 세로는 살아 있다(§`painted_boxes` 머리말).
//    이 판이 재는 것도 세로 하나다.

/// 캔버스가 **판 예산을 뜻있게 만들 만큼** 큰 배치. `layout_one_pane`(4행)으로는
/// 예산이 최소값(5)까지 접혀 탭마다 그리는 줄 수가 똑같아지고, 그러면 아래 단언이
/// 아무것도 안 잰다.
fn layout_tall_pane() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 40, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 80, "h": 40, "title": "sh", "active": true}]
    }))
    .unwrap()
}

/// 정보 팝업을 `tab` 탭으로 펴 놓고 한 프레임 그려 **판 배경면의 높이**를 준다.
///
/// 판 배경은 `theme::ELEV` 한 색이고 그 색을 쓰는 자리는 판 하나뿐이다 — 그래서 색으로
/// 좁혀도 남의 면을 안 센다(다른 색이었다면 `fills_covering` 처럼 자리로 좁혀야 한다).
fn info_tabs_panel_height(tab: usize) -> f32 {
    painted_scene_setup(
        // REC 플러그인까지 켠다 — 그래야 **동작 줄이 있는 탭과 없는 탭**을 함께 잰다
        // (pytmux-373 ⑶ 이 그 줄들을 더했다 · 그것도 판 높이를 타면 안 된다).
        vec![layout_tall_pane(), rec_running()],
        &[],
        move |view| {
            view.apply_action_for_test(base::Action::ShowInfoTabs);
            view.panel_click(base::PanelTarget::InfoTab(tab));
        },
        |scene| {
            let mut hits: Vec<f32> = scene
                .layers()
                .flat_map(|layer| layer.rects.iter())
                .filter_map(|r| match r.background {
                    warpui::elements::Fill::Solid(c) if c == theme::ELEV => {
                        Some(r.bounds.lower_left().y() - r.bounds.origin().y())
                    }
                    _ => None,
                })
                .collect();
            hits.sort_by(|a, b| a.partial_cmp(b).unwrap());
            // 없으면 **공허 통과**다 — 아무 면도 못 찾았는데 "높이가 같다"고 말하게 된다.
            hits.pop().expect("판 배경(ELEV)이 한 조각도 안 칠해졌다 — 오라클이 죽었다")
        },
    )
}

#[test]
fn the_status_panel_is_the_same_height_whatever_the_tab() {
    // 제보(pytmux-373 · 첨부 3장): 같은 창·같은 판인데 **탭마다 위·아래 모서리가 움직인다**.
    // 정본(`InfoTabsScreen`)은 짧은 탭을 빈 줄로 채워 한 픽셀도 안 움직인다.
    let mut state = proto::SessionState::new();
    state.apply(rec_running());
    let tabs = proto::info::tabs(&state, "/tmp/test.sock", 0.0);
    // 전제 둘 — 깨지면 아래 단언이 공허하다.
    assert!(tabs.len() >= 2, "탭이 하나뿐이면 잴 것이 없다");
    let counts: Vec<usize> = tabs.iter().map(|(_, lines)| lines.len()).collect();
    assert!(
        counts.iter().any(|n| *n != counts[0]),
        "탭마다 줄 수가 같다 — 이 오라클은 줄 수가 달라야 뜻이 있다: {counts:?}"
    );
    let heights: Vec<f32> = (0..tabs.len()).map(info_tabs_panel_height).collect();
    assert!(heights[0] > 0., "판 높이가 0이다 — 판이 안 떴다: {heights:?}");
    for (i, h) in heights.iter().enumerate() {
        assert!(
            (h - heights[0]).abs() < 0.5,
            "탭 {i}({}) 에서 판 높이가 {h} 다 — 첫 탭은 {}. 줄 수는 예산으로 같으니 \
             갈린 것은 **한 줄의 픽셀**이다(채움 줄만 판의 줄 상자를 쓰고 내용 줄은 안 썼다).\n\
             탭별 줄 수: {counts:?} · 탭별 높이: {heights:?}",
            tabs[i].0,
            heights[0]
        );
    }
}

// ── 입력기 배지는 **글자를 받는 곳**에 붙는다 · pytmux-14 ─────────────────────
//
// 캔버스 쪽 배지(활성 패널 커서 줄)는 서버 플러그인이 그린다. 판이 열리면 커서는 판 안
// 입력줄로 가고 캔버스 배지는 판 **뒤**에 깔린다 — 제보가 본 그림이 그것이다.
// 여기서는 그림 대신 **판이 담은 글자**를 묻는다(`debug_text_content` — `test-util`).

/// 한/영 배지의 **알약 바탕색** — 정본이 정한 의미 이름을 이 클라 표에서 푼 값이다.
fn ime_track(label: &str) -> ColorU {
    let name = if label == "한" { "success" } else { "primary" };
    match proto::session::theme::resolve(name) {
        proto::session::theme::Resolution::Color(c) => to_gui_color(&c),
        other => panic!("의미 색 이름을 못 풀었다: {other:?}"),
    }
}

/// 한 프레임을 그려 **배지 알약의 면**을 센다(pytmux-392).
///
/// # 왜 글자가 아니라 면인가
///
/// 이 배지는 이제 글자가 아니다 — `[한]`·`[EN]` 네 글자를 그리던 자리에 알약과 손잡이를
/// 그린다(사용자 요청 2026-08-24: *"텍스트로 표시하지 말고 그래픽 요소로"*). 그래서
/// 「배지가 있나」를 묻는 오라클도 글자에서 **면**으로 옮긴다 — 안 옮기면 그림이 멀쩡한데
/// 시험이 울고, 그 시험을 지우면 배선이 빠진 것을 아무도 안 잰다.
fn ime_pills(label: &'static str, setup: impl FnOnce(&mut SessionView) + 'static) -> usize {
    let track = ime_track(label);
    painted_scene_setup(
        vec![layout_one_pane()],
        &[],
        move |v| {
            v.report_ime(Some(label));
            setup(v);
        },
        move |scene| {
            scene
                .layers()
                .flat_map(|l| l.rects.iter())
                .filter(|r| matches!(r.background, warpui::elements::Fill::Solid(c) if c == track))
                .count()
        },
    )
}

/// 배지 상태를 세운 뷰. `report_ime` 를 쓰는 이유는 그것이 OS 를 안 묻는 쪽이라서다
/// (`tick_ime` 은 창 밖 입력기에 물어 테스트가 값을 정할 수 없다).
fn view_with_ime(label: Option<&'static str>) -> SessionView {
    let (mut view, tx, _sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view.report_ime(label);
    view
}

#[test]
fn the_ime_badge_rides_the_palette_input_line() {
    // 판이 열려 있으면 배지 **알약**이 프레임에 있어야 한다(치는 자리 옆이다).
    //
    // ⚠ 종전에는 판이 담은 **글자**에서 `[한]` 을 찾았다. 배지가 그림이 된 뒤
    //   (pytmux-392) 그 오라클은 그림이 멀쩡해도 운다 — 재는 것을 면으로 옮긴다.
    let pills = ime_pills("한", |v| v.screens.open(base::screens::Screen::Commands));
    assert!(pills >= 1, "입력기 배지가 팔레트 판에 없다 — 사람은 지금 무엇이 찍힐지 모른 채 친다");
}

#[test]
fn the_ime_badge_rides_the_prompt_input_line() {
    // 제보의 그림(인자를 묻는 작은 판)이 이 판이다.
    let pills = ime_pills("EN", |v| v.screens.ask(base::Prompt::RenameTab, ""));
    assert!(pills >= 1, "입력기 배지가 물음 판에 없다");
}

#[test]
fn in_the_composer_the_badge_rides_the_cursor_row_only() {
    // 작성창은 여러 줄이라 "입력줄"이 곧 **커서 줄**이다 — 캔버스 규칙과 같은 자리다.
    // 알약이 **딱 하나**여야 한다: 줄마다 붙으면 글을 읽을 수 없다.
    let pills = ime_pills("한", |v| v.screens.open_compose("첫줄
둘째줄"));
    assert_eq!(pills, 1, "배지가 커서 줄에만 있어야 한다");
}

#[test]
fn without_an_ime_state_no_badge_is_drawn() {
    // 비 Windows·질의 실패는 `None` 이다. 그때 빈 배지를 그리면 자리만 차지하고 아무
    // 말도 안 한다(정본도 안 올라오면 안 그린다).
    let track = ime_track("한");
    let fills = painted_scene_setup(
        vec![layout_one_pane()],
        &[],
        |v| {
            v.report_ime(None);
            v.screens.open(base::screens::Screen::Commands);
        },
        move |scene| {
            scene
                .layers()
                .flat_map(|l| l.rects.iter())
                .filter(|r| matches!(r.background, warpui::elements::Fill::Solid(c) if c == track))
                .count()
        },
    );
    assert_eq!(fills, 0, "상태를 모르는데 배지를 그렸다");
}

#[test]
fn no_screen_shows_an_input_line_without_the_badge() {
    // ★ 전수 오라클. 위 셋은 자기 판만 본다 — **새 판**이 입력줄을 갖는 날 아무도 안
    //   운다. 그래서 화면 전부를 열어 보고 "입력줄이 있는데 배지가 없는" 판을 찾는다.
    //   입력줄의 표식은 `> …_` 다(세 판이 공유하는 그림 — 커서를 `_` 로 보인다).
    let mut naked: Vec<String> = Vec::new();
    for screen in base::screens::Screen::all().iter().copied() {
        let mut view = view_with_ime(Some("한"));
        // 판마다 그릴 재료가 다르다 — 없으면 그 판은 비어서 판정 대상이 안 된다.
        match screen {
            base::screens::Screen::Compose => view.screens.open_compose(""),
            base::screens::Screen::Prompt | base::screens::Screen::Confirm => {
                view.screens.ask(base::Prompt::RenameTab, "")
            }
            other => view.screens.open(other),
        }
        let Some(panel) = view.render_screen_panel(screen).debug_text_content() else {
            continue;
        };
        let has_input = panel.lines().any(|l| {
            let t = l.trim_end();
            t.starts_with('>') && t.ends_with('_')
        });
        if !has_input {
            continue;
        }
        // 배지는 이제 그림이라 판의 글자에서는 안 보인다 — 프레임의 면으로 잰다.
        let pills = ime_pills("한", move |v| match screen {
            base::screens::Screen::Compose => v.screens.open_compose(""),
            base::screens::Screen::Prompt | base::screens::Screen::Confirm => {
                v.screens.ask(base::Prompt::RenameTab, "")
            }
            other => v.screens.open(other),
        });
        if pills == 0 {
            naked.push(format!("{screen:?}"));
        }
    }
    assert!(
        naked.is_empty(),
        "글자를 받는 줄인데 입력기 배지가 없다 — `input_line` 을 거칠 것:
  {}",
        naked.join("
  ")
    );
}

// ── 배선 두 끝 — 창 없이 잴 수 없는 자리를 소스로 잰다 ────────────────────────
//
// 조합 그리기의 실패는 **전부 조용하다**: 상류 구독이 빠지면 아무것도 안 오고, 얹는
// 호출이 빠지면 캔버스가 서버 화면 그대로다. 둘 다 화면이 "평소처럼" 보인다.
// 앞끝(구독)은 엘리먼트 이벤트 디스패치가 레이아웃을 요구해 헤드리스로 못 세우므로
// 소스로 잰다 — `tests/test_harness_window_lookup.py` 와 같은 종류의 오라클이다.

/// 이 파일 자신. `render`/구독은 창 없이 못 부르니 **글로 읽어** 배선을 확인한다.
const SESSION_VIEW_SRC: &str = include_str!("session_view.rs");

#[test]
fn the_view_subscribes_to_upstream_composition_events() {
    // ★ 이 구독이 빠지면 사람이 `ㅎ`→`하`→`한` 을 만드는 동안 **화면이 비어 있다**.
    //   상류는 계속 주고 있으므로 오류도 로그도 없다 — 지운 것을 아무도 모른다.
    //   (`warpui_core` 의 `on_marked_text` 자체가 이 기능 때문에 생겼다 — PROVENANCE §1.)
    assert!(
        SESSION_VIEW_SRC.contains(".on_marked_text("),
        "상류 조합 이벤트 구독(`on_marked_text`)이 사라졌다 — 조합 중인 글자가 다시 안 보이게 된다"
    );
    assert!(
        SESSION_VIEW_SRC.contains("ViewAction::Preedit("),
        "구독은 있는데 `ViewAction::Preedit` 로 넘기지 않는다 — 뷰까지 안 닿는다"
    );
}

#[test]
fn render_gets_its_canvas_only_through_composite_for_paint() {
    // ★ `render` 가 `state.composite()` 를 **직접** 부르면 클라가 얹는 것이 통째로
    //   빠진다(종전 결함의 모양 그대로). 캔버스로 가는 문을 하나로 유지한다 —
    //   그래야 위 오라클 다섯이 `render` 의 그림을 실제로 재는 것이 된다.
    let body = SESSION_VIEW_SRC
        .split_once("    fn render(&self, _: &AppContext) -> Box<dyn Element> {")
        .expect("`render` 를 못 찾았다 — 시그니처가 바뀌었으면 이 오라클도 옮길 것")
        .1;
    assert!(
        body.contains("self.composite_for_paint()"),
        "`render` 가 `composite_for_paint` 를 안 쓴다"
    );
    assert!(
        !body.contains("self.state.composite()"),
        "`render` 가 서버 합성을 직접 집었다 — 클라가 얹는 것(조합 중인 글자)이 화면에서 사라진다"
    );
}

#[test]
fn clicking_the_claude_footer_asks_the_server_for_the_screen_it_named() {
    // pytmux-2 · 23: 패널 **안**의 자리가 화면을 여는 첫 사례다. 무엇이 열리는지도,
    // 그 화면이 있는지도 서버가 정한다 — 우리는 이름과 **누른 패널**을 되돌려 보낼
    // 뿐이다. 패널을 안 실으면 비활성 Claude 패널의 footer 를 눌렀을 때 활성 패널의
    // 모드가 바뀐다(증상이 조용한 갈래다: 팝업은 제대로 뜬다).
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [], "runs": [],
        "zones": [{"x": 10, "y": 1, "w": 15, "h": 1, "pane": 7,
                   "name": "claude-code", "do": "perm",
                   "opens": "claude-perm-mode"}],
        "keys": []
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(cells))).unwrap();
    view.pump_headless();
    view.handle_mouse_down((12, 1), false);
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let open = frames
        .iter()
        .find(|f| f["action"] == "plugin_open")
        .unwrap_or_else(|| panic!("footer 를 눌렀는데 아무것도 안 올라갔다: {frames:?}"));
    assert_eq!(open["name"], "claude-perm-mode");
    assert_eq!(open["args"], serde_json::json!(["7"]), "누른 패널을 안 실었다");
    // 이 자리는 오버레이 길로 **가면 안 된다** — 그 이름은 서버에서 사라진다.
    assert!(
        !frames.iter().any(|f| f["action"] == "plugin_overlay_action"),
        "화면을 여는 자리가 오버레이 길로도 나갔다: {frames:?}"
    );
}

#[test]
fn clicking_the_interrupt_footer_types_into_that_pane() {
    // pytmux-2 잔여: `esc to interrupt` 는 화면이 아니라 **그 패널에 치는 것**이다.
    // 무엇을 치는지는 서버가 자리에 실어 보내고(`send`), 우리는 그 패널로 넘긴다 —
    // 사람이 그 자리에서 ESC 를 친 것과 같은 길이다.
    //
    // ★ **양성 오라클**이다(부정 단언만 있으면 배선이 통째로 빠져도 통과한다 —
    //   이 저장소에서 두 번 밟았다). 그래서 프레임이 실제로 무엇을 실었는지 본다.
    let (mut view, tx, sent) = harness();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    let cells: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "plugin_cells", "layer": "overlay", "dim": [], "runs": [],
        "zones": [{"x": 10, "y": 1, "w": 16, "h": 1, "pane": 7,
                   "name": "claude-code", "do": "interrupt", "send": "\u{1b}"}],
        "keys": []
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(cells))).unwrap();
    view.pump_headless();
    view.handle_mouse_down((12, 1), false);
    view.pump_headless();
    let frames: Vec<serde_json::Value> =
        sent.lock().unwrap().iter().map(|o| o.to_frame()).collect();
    let input = frames
        .iter()
        .find(|f| f["t"] == "input")
        .unwrap_or_else(|| panic!("인터럽트 자리를 눌렀는데 아무것도 안 올라갔다: {frames:?}"));
    // ESC 한 바이트의 base64. 값까지 재는 이유: 빈 바이트열도 프레임은 만든다.
    assert_eq!(input["data"], "Gw==", "친 것이 ESC 가 아니다: {frames:?}");
    // ★ **누른 그 패널**이어야 한다(활성 패널 1 이 아니라 7). 여기가 비면 서버가
    //   활성 패널로 흘려, 비활성 Claude 패널을 멈추려던 클릭이 지금 보는 패널을 멈춘다.
    assert_eq!(input["pane"], 7, "누른 패널을 안 실었다: {frames:?}");
    // 화면 길·오버레이 길로는 **가면 안 된다** — 둘 다 이 자리에서는 조용히 사라진다.
    assert!(
        !frames
            .iter()
            .any(|f| f["action"] == "plugin_open" || f["action"] == "plugin_overlay_action"),
        "치는 자리가 화면/오버레이 길로도 나갔다: {frames:?}"
    );
}

// ── 전체 화면(§10-21ⓘ3) ───────────────────────────────────────────────────────

/// `Alt`+`Enter` 만 잡는다 — 넓으면 패널 안 프로그램의 키가 사라지고, 좁으면 안 먹는다.
#[test]
fn only_alt_enter_asks_for_fullscreen() {
    use warpui::keymap::Keystroke;
    let k = |s: &str| Keystroke::parse(s).unwrap();
    assert!(SessionView::is_fullscreen_chord(&k("alt-enter")));
    // 맥의 `cmd` 는 여기서 alt 취급이 아니다(창 조작은 OS 관습이 따로 있다).
    assert!(!SessionView::is_fullscreen_chord(&k("enter")), "맨 Enter 를 먹으면 줄바꿈이 죽는다");
    assert!(!SessionView::is_fullscreen_chord(&k("ctrl-alt-enter")));
    assert!(!SessionView::is_fullscreen_chord(&k("alt-shift-enter")));
    assert!(!SessionView::is_fullscreen_chord(&k("alt-a")));
}

/// 액션은 **요청 한 번**을 남긴다 — 상태를 들지 않는다(진실은 창에 있다).
#[test]
fn the_fullscreen_action_leaves_a_request_not_a_state() {
    let (link, _tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    assert!(!view.fullscreen_requested_for_test());
    assert!(view.apply_action_for_test(base::Action::ToggleFullscreen));
    // ★ 요청 **한 번**이지 상태가 아니다 — 전체 화면인지의 진실은 창에 있다
    //   (`fullscreen_state()`). 사본을 들면 OS 가 바꾼 상태와 갈려 토글이 헛돈다.
    assert!(view.fullscreen_requested_for_test(), "요청이 안 남았다");
}

// ── 우클릭 = 메뉴로 가는 **두 번째 입구**(§10-21ⓕ3) ───────────────────────────

fn view_on_one_pane() -> SessionView {
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    view.pump_headless();
    view
}

#[test]
fn a_right_click_on_a_pane_opens_the_same_menu_as_the_key() {
    // ★ 새 화면이 아니라 새 입구다 — 항목표도 실행 경로도 `prefix Enter` 와 한 벌이다.
    let mut view = view_on_one_pane();
    assert_eq!(view.screens.top(), None);
    assert!(view.handle_right_mouse_down(Some((2, 2))), "우클릭이 아무 일도 안 했다");
    assert_eq!(view.screens.top(), Some(Screen::Menu));
    // 그리고 그 판은 키로 연 것과 같은 줄을 갖는다(표가 갈리지 않았다는 양성 오라클).
    assert!(!view.screens.menu_rows().is_empty());
}

#[test]
fn a_right_click_outside_the_canvas_does_nothing() {
    // 정본도 패널 위에서만 연다 — 탭바에서 열면 "이 패널"이 없는 메뉴가 된다.
    let mut view = view_on_one_pane();
    assert!(!view.handle_right_mouse_down(None));
    assert_eq!(view.screens.top(), None);
}

#[test]
fn a_right_click_is_ignored_while_a_panel_is_open() {
    // ⚠ 정본에는 **이미 닫힌 화면에 우클릭이 늦게 닿아 크래시**한 기록이 있다.
    //   우리 쪽 같은 자리는 "판이 떠 있으면 캔버스 마우스가 죽는다"가 막는다.
    let mut view = view_on_one_pane();
    view.screens.open(Screen::Keys);
    assert!(!view.handle_right_mouse_down(Some((2, 2))));
    assert_eq!(view.screens.top(), Some(Screen::Keys), "판이 바뀌었다");
}

#[test]
fn a_right_click_does_nothing_when_the_mouse_is_off() {
    // `set mouse off` 는 클라가 마우스를 아예 안 보는 것이다(왼쪽과 같은 판정).
    let mut view = view_on_one_pane();
    view.config.mouse = false;
    assert!(!view.handle_right_mouse_down(Some((2, 2))));
    assert_eq!(view.screens.top(), None);
}

// ── 표시용 스크롤바(§10-21ⓨ2) ────────────────────────────────────────────────

fn scrolled_view(top: usize, scroll: usize) -> SessionView {
    let (link, tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    // ⚠ **테두리 상자가 있는** 배치라야 한다 — 막대는 그 열 위에 얹힌다(테두리를 안
    //   그리는 배치에서는 얹을 선이 없어 애초에 안 그린다).
    let layout: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 10, "active": 1,
        "panes": [{"id": 1, "x": 1, "y": 1, "w": 78, "h": 8, "title": "sh",
                   "active": true, "box": [0, 0, 80, 10]}]
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(layout))).unwrap();
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1, "rows": [[["x", {}]]], "cursor": [0, 0], "wrap": [],
        "top": top, "scr": scroll
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(screen))).unwrap();
    view.pump_headless();
    view
}

/// ★ **위로 올라간 패널에만** 선다 — 늘 그리면 라이브 화면에 늘 꽉 찬 막대가 붙는다.
#[test]
fn the_scroll_hint_appears_only_after_scrolling_up() {
    assert!(scrolled_view(0, 0).scroll_hints_for_test().is_empty(), "라이브인데 막대가 있다");
    let hints = scrolled_view(200, 40).scroll_hints_for_test();
    assert_eq!(hints.len(), 1, "스크롤했는데 막대가 없다");
    let hint = &hints[0];
    assert!(hint.start >= 0.0 && hint.start + hint.len <= 1.0 + 1e-9);
}

/// 판이 떠 있으면 안 그린다 — 캔버스가 스크림 아래인데 막대만 또렷하면 눈이 그리로 간다.
#[test]
fn a_panel_hides_the_scroll_hint() {
    let mut view = scrolled_view(200, 40);
    assert!(!view.scroll_hints_for_test().is_empty());
    view.screens.open(Screen::Keys);
    assert!(view.scroll_hints_for_test().is_empty());
}

/// 자리는 패널의 **테두리 열**이다 — 내용 칸을 먹으면 서버에 보고하는 폭이 달라진다.
#[test]
fn the_hint_sits_on_the_border_column_not_inside_the_pane() {
    let view = scrolled_view(200, 40);
    let hints = view.scroll_hints_for_test();
    let hint = &hints[0];
    // `layout_one_pane` 의 내용은 (1,1,…) 이고 테두리 상자는 (0,0,w,h) 다.
    let pane = &view.state_for_test().panes()[0];
    let [bx, _, bw, _] = pane.boxrect.expect("테두리 상자");
    assert_eq!(hint.x, bx + bw - 1, "테두리 오른쪽 열이 아니다");
    assert!(hint.x >= pane.x + pane.w, "내용 칸을 먹었다");
}

// ── 설정 판의 목록 이동과 오른쪽 막대(pytmux-374) ─────────────────────────────

fn settings_view() -> SessionView {
    let (link, _tx, _sent) = ServerLink::detached("/tmp/test.sock");
    let mut view = SessionView::with_font(link, warpui::fonts::FamilyId(0));
    view.pump_headless();
    view.screens.open(Screen::Settings);
    view
}

/// `End` 는 **마지막 줄**에 선다 — core 가 두고 간 상한을 뷰가 접는 그 자리를 잰다.
///
/// core 쪽 오라클(`screens_tests`)은 「상한을 두고 간다」까지밖에 못 잰다. 줄 수를 아는
/// 것은 여기라, **접는 자가 실제로 불리는지**는 이 자리에서만 보인다.
#[test]
fn end_lands_on_the_last_settings_row() {
    let mut view = settings_view();
    let last = view.screens.plugins().settings_len() - 1;
    view.handle_key(Key::End, Mods::NONE);
    assert_eq!(view.screens.top(), Some(Screen::Settings), "End 가 판을 닫았다");
    assert_eq!(view.screens.selected(), last, "End 가 끝으로 안 갔다");
    // 끝에서 한 번 더 눌러도 넘어가지 않는다 — 넘기면 그만큼 `↑` 가 헛돈다.
    view.handle_key(Key::PageDown, Mods::NONE);
    assert_eq!(view.screens.selected(), last);
    view.handle_key(Key::Up, Mods::NONE);
    assert_eq!(view.screens.selected(), last - 1, "끝에서 ↑ 한 번이 안 먹었다");
}

/// ⑴ 오른쪽 막대는 **넘칠 때만** 서고, 커서를 따라 트랙을 걷는다.
#[test]
fn the_settings_bar_walks_the_track() {
    let mut view = settings_view();
    let (start, len) = view.settings_scroll_for_test().expect("설정은 판보다 길다");
    assert!(start.abs() < 1e-9, "맨 위인데 썸이 위가 아니다: {start}");
    assert!(len > 0.0 && len < 1.0, "썸이 트랙을 다 먹었다: {len}");

    view.handle_key(Key::End, Mods::NONE);
    let (start, len) = view.settings_scroll_for_test().expect("막대가 사라졌다");
    assert!((start + len - 1.0).abs() < 1e-9, "끝인데 썸이 바닥이 아니다: {start} {len}");
}

/// 그릴 것이 없으면 안 그린다 — 판이 목록보다 길면 늘 꽉 찬 막대가 붙는다.
#[test]
fn a_tall_enough_panel_gets_no_settings_bar() {
    let view = settings_view();
    let total = view.screens.plugins().settings_len();
    assert_eq!(base::scrollbar::list_fraction(total, total, 0), None);
    // 지금 판(예산 = 캔버스 없이 12줄에서 나온 값)은 그보다 훨씬 짧다.
    assert!(view.panel_budget_for_test() < total, "이 시험이 재는 상황이 아니다");
}

/// ⑵ 제 것 아닌 키가 판을 안 닫는다 — **키 경로 전체**(뷰 배선 포함)에서 잰다.
#[test]
fn a_stray_key_leaves_the_settings_panel_open() {
    let mut view = settings_view();
    for key in [Key::Function(5), Key::Insert] {
        view.handle_key(key, Mods::NONE);
        assert_eq!(view.screens.top(), Some(Screen::Settings), "{key:?} 가 판을 닫았다");
    }
    view.handle_key(Key::Escape, Mods::NONE);
    assert_eq!(view.screens.top(), None, "Esc 가 안 닫았다");
}

// ── 경로 존의 기준은 **그 패널**이다(§10-21ⓧ2 / pytmux-24) ────────────────────
//
// 종전 GUI 는 `active_cwd()` 로 풀었다 — 활성 패널 하나의 값이라 옆 패널 글에는 남의
// 기준이 걸렸다. 밑줄은 멀쩡히 그어지고 **복사한 값만** 틀리는 조용한 오답이다.

fn two_panes_with_a_relative_path() -> Vec<ServerMessage> {
    let line = "Update(server/test/x.mjs)";
    let mut out = vec![serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 80, "rows": 6, "active": 1,
        "panes": [
            {"id": 1, "x": 0,  "y": 0, "w": 40, "h": 5, "active": true},
            {"id": 2, "x": 40, "y": 0, "w": 40, "h": 5, "active": false}
        ]
    }))
    .unwrap()];
    for pane in [1, 2] {
        out.push(
            serde_json::from_value(serde_json::json!({
                "t": "screen", "pane": pane, "rows": [[[line, {}]]],
                "cursor": [0, 0], "wrap": [], "top": 0
            }))
            .unwrap(),
        );
    }
    out
}

fn cwd_frame(pane: i64, cwd: &str) -> ServerMessage {
    serde_json::from_value(serde_json::json!({ "t": "cwd", "pane": pane, "cwd": cwd })).unwrap()
}

/// 각 패널의 같은 글이 **자기 cwd** 로 풀린다. cwd 를 모르는 패널에는 존이 안 생긴다.
#[test]
fn a_path_zone_resolves_against_the_pane_it_is_in() {
    let (mut view, tx, _sent) = harness();
    for msg in two_panes_with_a_relative_path() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    // **비활성** 패널만 cwd 를 안다 — active_cwd 로 풀면 여기서 존이 안 생긴다.
    tx.send(LinkEvent::Message(Box::new(cwd_frame(2, "/b/two"))))
        .unwrap();
    view.pump_headless();

    let hit = view.span_at(50, 0).expect("비활성 패널의 경로에 존이 없다");
    assert_eq!(hit.pane, 2);
    assert_eq!(hit.text, "server/test/x.mjs");
    assert_eq!(
        proto::info::resolve_path(view.state.pane_cwd(hit.pane), &hit.text),
        Some(
            std::path::Path::new("/b/two")
                .join("server/test/x.mjs")
                .to_string_lossy()
                .into_owned()
        )
    );
    assert!(
        view.span_at(10, 0).is_none(),
        "cwd 를 모르는 패널에 존이 생겼다 — 눌러도 아무 일이 없는 밑줄은 거짓말이다"
    );
}

/// 반대쪽도 지킨다: 활성 패널만 알 때 **옆 패널**이 그 값을 빌려 쓰면 안 된다.
#[test]
fn a_pane_without_a_cwd_does_not_borrow_the_active_one() {
    let (mut view, tx, _sent) = harness();
    for msg in two_panes_with_a_relative_path() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    tx.send(LinkEvent::Message(Box::new(cwd_frame(1, "/a/one"))))
        .unwrap();
    view.pump_headless();

    assert!(view.span_at(10, 0).is_some(), "아는 패널에는 존이 있어야 한다");
    assert!(
        view.span_at(50, 0).is_none(),
        "옆 패널이 활성 패널의 cwd 를 빌려 풀었다 — 복사한 값이 조용히 틀린다"
    );
}

// ── 글자 선 — 밑줄(SGR 4 · pytmux-123) · 취소선(SGR 9 · pytmux-133) ──────────
//
// 서버는 이 속성들을 정상적으로 싣고 파서도 정상적으로 읽는다. 마지막 한 걸음(칠하기)만
// 없어서 **아무 오라클도 안 울었다** — 스타일 왕복 테스트는 값이 살아 있는지만 본다.
// 그래서 여기서 재는 것은 값이 아니라 **그릴 것이 생기는가**다.

fn underlined(fg: Option<proto::style::Color>) -> CellStyle {
    CellStyle { underline: true, fg, ..Default::default() }
}

fn struck(fg: Option<proto::style::Color>) -> CellStyle {
    CellStyle { strike: true, fg, ..Default::default() }
}

/// 밑줄만 골라 본다 — 취소선이 섞여 들어오면 자리 단언이 조용히 어긋난다.
fn under_only(canvas: &proto::canvas::Canvas) -> Vec<crate::splitter::TextRule> {
    SessionView::text_rules(canvas)
        .into_iter()
        .filter(|r| r.at == crate::splitter::RuleAt::Under)
        .collect()
}

#[test]
fn underlined_cells_become_one_line_per_run() {
    let mut canvas = proto::canvas::Canvas::new(10, 1);
    canvas.put_text(0, 0, "ab", CellStyle::default());
    canvas.put_text(2, 0, "cd", underlined(None));
    canvas.put_text(4, 0, "ef", CellStyle::default());

    let lines = under_only(&canvas);
    assert_eq!(lines.len(), 1, "이어진 두 칸이 한 선이 아니다: {}", lines.len());
    assert_eq!((lines[0].y, lines[0].x0, lines[0].x1), (0, 2, 4), "자리가 틀렸다");
}

#[test]
fn a_canvas_without_underlines_asks_for_no_lines() {
    let mut canvas = proto::canvas::Canvas::new(6, 1);
    canvas.put_text(0, 0, "hello", CellStyle::default());
    assert!(
        under_only(&canvas).is_empty(),
        "밑줄이 없는 화면에 선을 그리려 한다"
    );
}

/// 색이 갈리면 끊는다 — 다른 색의 밑줄을 한 선으로 이으면 그중 하나가 거짓이 된다.
#[test]
fn runs_break_where_the_colour_changes() {
    let mut canvas = proto::canvas::Canvas::new(8, 1);
    canvas.put_text(0, 0, "ab", underlined(proto::style::Color::parse("red")));
    canvas.put_text(2, 0, "cd", underlined(proto::style::Color::parse("blue")));

    let lines = under_only(&canvas);
    assert_eq!(lines.len(), 2, "색이 갈렸는데 한 선으로 이었다");
    assert_eq!((lines[0].x0, lines[0].x1), (0, 2));
    assert_eq!((lines[1].x0, lines[1].x1), (2, 4));
    assert_ne!(lines[0].color, lines[1].color, "선 색이 글자 색을 안 따라간다");
}

/// 줄이 다르면 당연히 다른 선이다(행을 넘어 이어 붙으면 화면 밖으로 선이 뻗는다).
#[test]
fn runs_do_not_wrap_across_rows() {
    let mut canvas = proto::canvas::Canvas::new(4, 2);
    canvas.put_text(2, 0, "ab", underlined(None));
    canvas.put_text(0, 1, "cd", underlined(None));

    let lines = under_only(&canvas);
    assert_eq!(lines.len(), 2);
    assert_eq!((lines[0].y, lines[0].x0, lines[0].x1), (0, 2, 4));
    assert_eq!((lines[1].y, lines[1].x0, lines[1].x1), (1, 0, 2));
}

/// 넓은 글자는 **두 칸**을 덮는다 — 뒤 칸(continuation)이 같은 스타일이라 이어진다.
/// 한 칸만 그으면 한글 밑줄이 글자 절반에서 끊긴다.
#[test]
fn a_wide_character_is_underlined_across_both_of_its_cells() {
    let mut canvas = proto::canvas::Canvas::new(6, 1);
    canvas.put_text(0, 0, "한", underlined(None));

    let lines = under_only(&canvas);
    assert_eq!(lines.len(), 1);
    assert_eq!((lines[0].x0, lines[0].x1), (0, 2), "넓은 글자의 뒤 칸이 빠졌다");
}

/// 반전된 밑줄은 **보이는 색**을 따라간다 — `colors()` 가 fg·bg 를 바꾸므로 선도 같이
/// 바뀌어야 배경에 묻히지 않는다.
#[test]
fn a_reversed_run_underlines_in_the_colour_the_eye_sees() {
    let mut canvas = proto::canvas::Canvas::new(4, 1);
    let style = CellStyle {
        underline: true,
        reverse: true,
        bg: proto::style::Color::parse("red"),
        ..Default::default()
    };
    canvas.put_text(0, 0, "ab", style);

    let lines = under_only(&canvas);
    assert_eq!(lines.len(), 1);
    let plain = CellStyle { fg: proto::style::Color::parse("red"), ..Default::default() };
    assert_eq!(lines[0].color, colors(&plain).0, "반전을 안 푼 색으로 그었다");
}

/// ★ **배선까지** 본다 — 규칙만 재면 `render` 에서 부르는 줄을 지워도 통과한다.
/// 진짜 서버 프레임(스타일 `un`)을 먹여 그릴 선이 생기는지 끝까지 따라간다.
#[test]
fn an_underlined_cell_from_the_server_reaches_the_overlay() {
    let (mut view, tx, _sent) = harness();
    let msgs: Vec<ServerMessage> = vec![
        serde_json::from_value(serde_json::json!({
            "t": "layout", "cols": 20, "rows": 3, "active": 1,
            "panes": [{"id": 1, "x": 0, "y": 0, "w": 20, "h": 3, "active": true}]
        }))
        .unwrap(),
        serde_json::from_value(serde_json::json!({
            "t": "screen", "pane": 1,
            "rows": [[["ab", {}], ["cd", {"un": true}]]],
            "cursor": null, "wrap": [], "top": 0
        }))
        .unwrap(),
    ];
    for msg in msgs {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();

    let lines = view.rule_marks();
    assert_eq!(
        lines.len(),
        1,
        "서버가 보낸 밑줄이 오버레이까지 못 왔다(칠하는 쪽이 없으면 여기가 빈다)"
    );
    assert_eq!((lines[0].y, lines[0].x0, lines[0].x1), (0, 2, 4));
}

/// 반대쪽: 밑줄 없는 화면은 오버레이에 아무 일도 안 시킨다.
#[test]
fn a_plain_screen_reaches_the_overlay_with_nothing_to_draw() {
    let (mut view, tx, _sent) = harness();
    for msg in three_tabs() {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.rule_marks().is_empty());
}

// ── 취소선(SGR 9 · pytmux-133) ───────────────────────────────────────────────

#[test]
fn struck_cells_become_a_line_through_the_run() {
    let mut canvas = proto::canvas::Canvas::new(10, 1);
    canvas.put_text(0, 0, "ab", CellStyle::default());
    canvas.put_text(2, 0, "cd", struck(None));

    let rules = SessionView::text_rules(&canvas);
    assert_eq!(rules.len(), 1, "취소선 한 구간이 안 생겼다: {}", rules.len());
    assert_eq!(rules[0].at, crate::splitter::RuleAt::Through, "밑줄 자리에 그었다");
    assert_eq!((rules[0].y, rules[0].x0, rules[0].x1), (0, 2, 4), "자리가 틀렸다");
}

/// ★ 한 칸이 **둘 다** 가질 수 있다. 속성마다 따로 훑지 않으면 둘이 한 구간으로
/// 뭉개져 선 하나만 남는다(그러면 그중 하나가 화면에서 사라진다).
#[test]
fn a_cell_that_is_both_underlined_and_struck_gets_both_lines() {
    let mut canvas = proto::canvas::Canvas::new(4, 1);
    canvas.put_text(
        0,
        0,
        "ab",
        CellStyle { underline: true, strike: true, ..Default::default() },
    );

    let rules = SessionView::text_rules(&canvas);
    assert_eq!(rules.len(), 2, "선이 둘 다 안 생겼다: {rules:?}");
    let mut at: Vec<_> = rules.iter().map(|r| r.at).collect();
    at.sort_by_key(|a| format!("{a:?}"));
    assert_eq!(
        at,
        vec![crate::splitter::RuleAt::Through, crate::splitter::RuleAt::Under],
        "같은 자리에 같은 선을 두 번 그었다"
    );
}

/// 옆칸이 **다른 속성**이면 이어 붙이지 않는다 — 한 번에 훑으며 "선이 있나"로 묶으면
/// 밑줄 칸과 취소선 칸이 한 구간이 돼 **없던 선**이 생긴다.
#[test]
fn an_underlined_run_does_not_join_a_struck_neighbour() {
    let mut canvas = proto::canvas::Canvas::new(6, 1);
    canvas.put_text(0, 0, "ab", underlined(None));
    canvas.put_text(2, 0, "cd", struck(None));

    let rules = SessionView::text_rules(&canvas);
    assert_eq!(rules.len(), 2, "두 속성이 한 구간으로 뭉갰다: {rules:?}");
    let under = rules.iter().find(|r| r.at == crate::splitter::RuleAt::Under).unwrap();
    let through = rules.iter().find(|r| r.at == crate::splitter::RuleAt::Through).unwrap();
    assert_eq!((under.x0, under.x1), (0, 2), "밑줄이 옆칸까지 뻗었다");
    assert_eq!((through.x0, through.x1), (2, 4), "취소선이 옆칸까지 뻗었다");
}

/// ★ **배선까지** 본다 — 규칙만 재면 `render` 에서 부르는 줄을 지워도 통과한다.
#[test]
fn a_struck_cell_from_the_server_reaches_the_overlay() {
    let (mut view, tx, _sent) = harness();
    let msgs: Vec<ServerMessage> = vec![
        serde_json::from_value(serde_json::json!({
            "t": "layout", "cols": 20, "rows": 3, "active": 1,
            "panes": [{"id": 1, "x": 0, "y": 0, "w": 20, "h": 3, "active": true}]
        }))
        .unwrap(),
        serde_json::from_value(serde_json::json!({
            "t": "screen", "pane": 1,
            "rows": [[["ab", {}], ["cd", {"st": true}]]],
            "cursor": null, "wrap": [], "top": 0
        }))
        .unwrap(),
    ];
    for msg in msgs {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();

    let rules = view.rule_marks();
    assert_eq!(
        rules.len(),
        1,
        "서버가 보낸 취소선이 오버레이까지 못 왔다(칠하는 쪽이 없으면 여기가 빈다)"
    );
    assert_eq!(rules[0].at, crate::splitter::RuleAt::Through);
    assert_eq!((rules[0].y, rules[0].x0, rules[0].x1), (0, 2, 4));
}

// ── 굵게·기울임(SGR 1·3 · pytmux-133) ────────────────────────────────────────
//
// 선이 아니라 **글꼴 변형**이라 오버레이가 아니라 `Text` 가 그린다. 그래서 오라클도
// 다르다 — 여기서는 요구가 옳게 만들어지는지만 재고, 그 요구가 `Text` 까지 가는지는
// 라이브 스크린샷이 잡는다(`client/CLAUDE.md`: GUI 배선 누락은 그것만이 잡는다).

#[test]
fn a_plain_run_asks_for_no_font_variant() {
    let props = font_properties(&CellStyle::default());
    assert_eq!(props, warpui::fonts::Properties::default(), "맨 글자에 변형을 요구했다");
}

#[test]
fn bold_and_italic_each_ask_for_their_own_face() {
    use warpui::fonts::{Style, Weight};

    let bold = font_properties(&CellStyle {
        bold: true,
        ..Default::default()
    });
    assert_eq!(bold.weight, Weight::Bold);
    assert_eq!(bold.style, Style::Normal, "굵게가 기울임까지 켰다");

    let italic = font_properties(&CellStyle {
        italic: true,
        ..Default::default()
    });
    assert_eq!(italic.style, Style::Italic);
    assert_eq!(italic.weight, Weight::Normal, "기울임이 굵기까지 올렸다");

    let both = font_properties(&CellStyle {
        bold: true,
        italic: true,
        ..Default::default()
    });
    assert_eq!((both.weight, both.style), (Weight::Bold, Style::Italic));
}

/// 굵게·기울임은 **오버레이로 새지 않는다** — 선을 긋는 자리와 글꼴을 고르는 자리가
/// 갈려 있다는 것을 못박는다(한쪽이 다른 쪽 일을 하면 굵은 글자마다 줄이 그어진다).
#[test]
fn bold_and_italic_do_not_leak_into_the_overlay() {
    let mut canvas = proto::canvas::Canvas::new(4, 1);
    canvas.put_text(
        0,
        0,
        "ab",
        CellStyle { bold: true, italic: true, ..Default::default() },
    );
    assert!(
        SessionView::text_rules(&canvas).is_empty(),
        "굵게·기울임에 선을 그었다"
    );
}

// ── 호출이 지워지지 않았나 (원문 가드) ───────────────────────────────────────
//
// ★ 위 오라클들은 **값이 옳은가**만 잰다. `render` 가 그 값을 부르는 줄을 지우면 화면은
// 통째로 잃는데 테스트는 전부 통과한다 — 루트 `CLAUDE.md` 가 "뮤테이션에 '호출 제거'
// 를 포함할 것"이라고 적은 그 구멍이고, 밑줄 슬라이스(pytmux-123)가 그대로 남긴 것이다.
//
// 이 크레이트에는 그린 것을 되읽는 하네스가 없다(`Scene` 은 글리프 id 만 들고 있고
// `pump_headless` 는 렌더를 안 돌린다). 그래서 **원문을 읽어** 판정한다 — 저장소에 이미
// 같은 방식의 가드가 있다(`tests/test_harness_window_lookup.py`). 화면을 못 재는 대신
// **배선이 사라지는 것**만은 확실히 잡는다.

/// 원문에서 `head` 가 처음 나오는 자리부터 `len` 글자를 떼어 본다.
fn source_after(head: &str, len: usize) -> String {
    let src = include_str!("session_view.rs");
    let at = src.find(head).unwrap_or_else(|| panic!("원문에서 못 찾았다: {head}"));
    src[at..].chars().take(len).collect()
}

/// 오버레이 호출부 한 덩어리. 인자가 늘면 길어지므로 **넉넉히** 뜬다 —
/// 좁게 잡으면 인자 하나를 더할 때마다 관계없는 가드가 붉어진다(pytmux-18 에서 겪었다).
fn overlay_call() -> String {
    source_after("SplitterOverlay::new(", 600)
}

#[test]
fn the_overlay_is_still_handed_the_text_rules() {
    assert!(
        overlay_call().contains("self.rule_marks()"),
        "오버레이에 글자 선을 안 넘긴다 — 밑줄·취소선이 화면에서 통째로 사라진다"
    );
}

#[test]
fn the_overlay_is_still_handed_the_picked_block() {
    assert!(
        overlay_call().contains("self.block_mark()"),
        "오버레이에 고른 블록을 안 넘긴다 — 무엇이 골라졌는지 화면에 안 보인다(pytmux-18)"
    );
}

#[test]
fn the_row_still_asks_for_the_font_variant() {
    // ⚠ 창을 **함수 전체보다 넉넉히** 뜬다(`overlay_call` 과 같은 사정) — 좁게 잡으면
    //   `render_row` 에 줄이 늘 때마다 관계없는 이 가드가 붉어진다. 실제로 커서 모양
    //   설정(`pytmux/pytmux-161`)이 그 자리에 섰다: 함수가 4,302자로 자라 3,000 창
    //   밖으로 `with_style` 이 밀려났다.
    let body = source_after("fn render_row(", 6000);
    assert!(
        body.contains("font_properties(&style)"),
        "런의 글꼴 변형을 안 만든다 — 굵게·기울임이 조용히 사라진다"
    );
    assert!(
        body.contains(".with_style(fallback_safe(props, boxed))"),
        "만든 변형을 Text 에 안 넘긴다 — 값만 살아 있고 화면은 종전 그대로다"
    );
}

/// ★ 라이브가 잡은 것을 오라클로 굳힌다(실측 2026-08-04): 기울임을 보조 글꼴 조각에
/// 걸면 한글이 **두부**가 된다. 안 기울어지는 것은 참을 수 있지만 글자가 사라지는 것은
/// 아니다.
#[test]
fn italic_is_dropped_where_the_fallback_font_would_have_no_face() {
    use warpui::fonts::{Style, Weight};

    let want = font_properties(&CellStyle {
        bold: true,
        italic: true,
        ..Default::default()
    });

    let ascii = fallback_safe(want, false);
    assert_eq!(ascii.style, Style::Italic, "ASCII 조각에서 기울임을 잃었다");
    assert_eq!(ascii.weight, Weight::Bold);

    let boxed = fallback_safe(want, true);
    assert_eq!(boxed.style, Style::Normal, "보조 글꼴 조각에 기울임을 걸었다 — 두부가 된다");
    assert_eq!(
        boxed.weight,
        Weight::Bold,
        "굵게까지 뺐다 — 보조 글꼴에는 굵은 얼굴이 있다(실측)"
    );
}

// ── 캔버스 위 블록 선택(pytmux-18) ───────────────────────────────────────────
//
// 제보: *"warp 처럼 「명령 하나 + 그 출력」을 블록으로 고를 수 있어야 한다"* — ⑴ 고르기
// ⑵ `↑`/`↓` 로 한 블록씩 ⑶ `Ctrl`+`C` 로 그 블록 전체 복사.
//
// 키의 뜻은 core 가 잰다(`base/src/keys_tests.rs`). 여기서 재는 것은 **무엇이 골라져
// 있나 · 어디가 밝아지나 · 무엇이 서버로 나가나** 셋이다.

/// 블록 목록 한 벌. `(시작 행, 끝 행)` — 서버 와이어 이름(`start`/`end`)을 그대로 쓴다.
fn blocks_at(spans: &[(usize, Option<usize>)]) -> ServerMessage {
    let items: Vec<serde_json::Value> = spans
        .iter()
        .map(|(start, end)| match end {
            Some(end) => serde_json::json!({"cmd": "ls", "state": "done", "exit": 0,
                                            "start": start, "end": end}),
            None => serde_json::json!({"cmd": "ls", "state": "running", "start": start}),
        })
        .collect();
    serde_json::from_value(serde_json::json!({"t": "blocks", "pane": 1, "blocks": items})).unwrap()
}

/// 뷰포트가 절대 행 `top` 에서 시작하는 화면(패널 1 · `layout_one_pane` 의 4행).
fn screen_from(top: usize) -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["a", {}]], [["b", {}]], [["c", {}]], [["d", {}]]],
        "cursor": null, "wrap": [], "top": top
    }))
    .unwrap()
}

/// 배치 + 화면 + 블록을 먹인 뷰.
fn view_with_blocks(spans: &[(usize, Option<usize>)], top: usize) -> (SessionView, Sent) {
    let (view, _tx, sent) = view_with_blocks_tx(spans, top);
    (view, sent)
}

/// 같은 것 + **보내는 쪽**까지 — 뒤늦은 서버 메시지를 더 먹여야 하는 시험용.
fn view_with_blocks_tx(
    spans: &[(usize, Option<usize>)],
    top: usize,
) -> (SessionView, std::sync::mpsc::Sender<LinkEvent>, Sent) {
    let (mut view, tx, sent) = harness();
    for msg in [layout_one_pane(), screen_from(top), blocks_at(spans)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    (view, tx, sent)
}

#[test]
fn entering_the_mode_picks_the_newest_block() {
    // 방금 친 명령의 출력을 집으려는 것이 첫 쓰임이다 — 목록의 끝에서 시작한다.
    let (mut view, _sent) = view_with_blocks(&[(0, Some(2)), (2, None)], 0);
    assert!(view.apply_action_for_test(base::Action::SelectBlocks));
    assert_eq!(view.mode.mode(), InputMode::Block);
    assert_eq!(view.block_pick, Some((1, 1)));
}

#[test]
fn a_pane_without_shell_integration_says_so_instead_of_entering() {
    // ⚠ 제보가 먼저 물은 것이다: 셸 통합이 없는 패널(`cmd.exe`)에는 블록이 하나도 없다.
    //   그때 모드에 들어가면 배지만 켜진 채 키가 통째로 죽어 "고장"으로 보인다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), screen_from(0)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.apply_action_for_test(base::Action::SelectBlocks);
    assert_eq!(view.mode.mode(), InputMode::Normal, "빈 목록으로 모드에 들어갔다");
    assert_eq!(view.block_pick, None);
    let flash = view.flash.as_ref().expect("아무 말도 안 했다 — 조용한 무반응이 제일 나쁘다");
    assert!(
        flash.text.contains("OSC 133"),
        "이유(셸 통합)를 안 말한다: {}",
        flash.text
    );
}

// ── 블록 선택 ② Claude 패널의 턴(pytmux-21) ─────────────────────────────────
//
// 제보 그대로다: *"프롬프트 하나와 그 프롬프트가 낸 출력을 한 블록으로"*. ★ 클라에는
// **새 상호작용이 없다** — 서버가 프롬프트 마커로 잡은 경계를 같은 `blocks` 메시지로
// 보내므로, 위의 고르기·강조·복사가 그대로 돈다. 여기서 재는 것은 그 사실 자체와,
// **비었을 때 뭐라고 말하는가** 다(그 한 줄만 패널 종류를 알아야 한다).

/// Claude 패널이라고 서버가 알려 온 상태 메시지(`panes_claude`).
fn claude_status() -> ServerMessage {
    serde_json::from_value(serde_json::json!({
        "t": "status",
        "windows": [{"index": 0, "name": "claude", "active": true}],
        "active_pane": 1,
        "panes_claude": [{"id": 1, "claude": true}],
    }))
    .unwrap()
}

/// 서버가 보낸 **턴** 목록(`state: "turn"` · `end` 없음 — 다음 턴의 시작이 곧 끝이다).
fn turns_at(starts: &[usize]) -> ServerMessage {
    let items: Vec<serde_json::Value> = starts
        .iter()
        .map(|start| serde_json::json!({"cmd": "테스트 돌려줘", "state": "turn", "start": start}))
        .collect();
    serde_json::from_value(serde_json::json!({"t": "blocks", "pane": 1, "blocks": items})).unwrap()
}

#[test]
fn a_claude_pane_picks_and_copies_a_whole_turn() {
    // ★ **양성 오라클**이다 — 턴이 들어왔다가 아니라 그 범위가 서버로 나갔나를 잰다.
    //   턴 ⓪은 절대 행 0 에서 시작하고 다음 턴이 2 에서 시작하므로 마지막 줄은 1 이다.
    let (mut view, tx, sent) = harness();
    for msg in [layout_one_pane(), claude_status(), screen_from(0), turns_at(&[0, 2])] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    assert!(view.apply_action_for_test(base::Action::SelectBlocks));
    assert_eq!(view.block_pick, Some((1, 1)), "가장 최근 턴에서 시작한다");
    view.handle_key(Key::Up, Mods::NONE);
    view.handle_key(Key::Char('c'), Mods::CTRL);
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    let copy = out
        .iter()
        .find_map(|o| match o {
            Outgoing::Command(Command::CopyRange { pane, y0, x0, y1, x1 }) => {
                Some((*pane, *y0, *x0, *y1, *x1))
            }
            _ => None,
        })
        .expect("턴 복사 요청이 안 나갔다");
    assert_eq!(copy, (1, 0, 0, 1, 79), "턴 하나의 줄 전체가 아니다");
}

#[test]
fn an_empty_claude_pane_does_not_blame_shell_integration() {
    // ⛔ 여기서 "셸 통합(OSC 133)을 켜라"고 말하면 **고칠 수 없는 것을 고치라는 안내**다 —
    //   Claude 는 OSC 를 안 보내므로 통합을 아무리 깔아도 턴이 안 생긴다. 이 패널에서
    //   비어 있다는 것은 아직 프롬프트를 한 번도 안 보냈다는 뜻이다.
    let (mut view, tx, _sent) = harness();
    for msg in [layout_one_pane(), claude_status(), screen_from(0)] {
        tx.send(LinkEvent::Message(Box::new(msg))).unwrap();
    }
    view.pump_headless();
    view.apply_action_for_test(base::Action::SelectBlocks);
    assert_eq!(view.mode.mode(), InputMode::Normal, "빈 목록으로 모드에 들어갔다");
    let flash = view.flash.as_ref().expect("아무 말도 안 했다");
    assert!(!flash.text.contains("OSC 133"), "셸 통합 탓을 했다: {}", flash.text);
    assert!(flash.text.contains("턴"), "무엇이 없는지를 안 말한다: {}", flash.text);
}

#[test]
fn the_arrows_step_one_block_and_stop_at_the_ends() {
    let (mut view, _sent) = view_with_blocks(&[(0, Some(2)), (2, Some(4)), (4, None)], 0);
    view.apply_action_for_test(base::Action::SelectBlocks);
    assert_eq!(view.block_pick, Some((1, 2)), "끝에서 시작한다");
    view.handle_key(Key::Up, Mods::NONE);
    view.handle_key(Key::Up, Mods::NONE);
    assert_eq!(view.block_pick, Some((1, 0)));
    // 목록 끝에서 더 눌러도 자리는 그대로다(그리고 다시 그리지 않는다).
    assert!(!view.handle_key(Key::Up, Mods::NONE), "무효 입력에 repaint 를 걸었다");
    assert_eq!(view.block_pick, Some((1, 0)));
    view.handle_key(Key::Down, Mods::NONE);
    assert_eq!(view.block_pick, Some((1, 1)));
}

#[test]
fn leaving_the_mode_drops_the_pick() {
    // 강조가 남아 있으면 "아직 고르는 중"이라고 말하는데 키는 이미 패널로 간다.
    let (mut view, _sent) = view_with_blocks(&[(0, None)], 0);
    view.apply_action_for_test(base::Action::SelectBlocks);
    assert!(view.block_pick.is_some());
    view.handle_key(Key::Escape, Mods::NONE);
    assert_eq!(view.mode.mode(), InputMode::Normal);
    assert_eq!(view.block_pick, None);
    assert!(view.block_mark().is_none(), "강조가 화면에 남았다");
}

#[test]
fn ctrl_c_asks_the_server_for_the_whole_block() {
    // ★ **양성 오라클**이다 — 무엇이 안 나갔나가 아니라 무슨 범위가 나갔나를 잰다.
    //   블록 ⓪은 절대 행 0..2 이고 `end` 는 다음 프롬프트 행이라 마지막 줄은 1 이다.
    let (mut view, sent) = view_with_blocks(&[(0, Some(2)), (2, None)], 0);
    view.apply_action_for_test(base::Action::SelectBlocks);
    view.handle_key(Key::Up, Mods::NONE);
    view.handle_key(Key::Char('c'), Mods::CTRL);
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    let copy = out
        .iter()
        .find_map(|o| match o {
            Outgoing::Command(Command::CopyRange { pane, y0, x0, y1, x1 }) => {
                Some((*pane, *y0, *x0, *y1, *x1))
            }
            _ => None,
        })
        .expect("복사 요청이 안 나갔다");
    assert_eq!(copy, (1, 0, 0, 1, 79), "줄 전체(0열~폭 끝)가 아니거나 행이 어긋난다");
}

#[test]
fn ctrl_c_outside_the_mode_still_interrupts_the_pane() {
    // ⛔ 이 줄이 무너지면 패널 안 프로그램을 끊을 길이 사라진다(제보의 ⚠ 그대로).
    //   **양성 오라클**이다 — "블록 복사가 안 나갔다"가 아니라 0x03 이 실제로 나갔나를 잰다.
    let (mut view, sent) = view_with_blocks(&[(0, None)], 0);
    assert_eq!(view.mode.mode(), InputMode::Normal);
    view.handle_key(Key::Char('c'), Mods::CTRL);
    view.pump_headless();
    let out = sent.lock().unwrap().clone();
    assert!(
        out.iter().any(|o| matches!(o, Outgoing::Input(b) if b == &vec![0x03])),
        "평소 모드의 Ctrl+C 가 인터럽트로 안 갔다: {out:?}"
    );
}

#[test]
fn the_highlight_covers_the_block_rows_inside_the_viewport() {
    // 블록 ①은 절대 행 2..3, 뷰포트는 0 에서 시작하는 4행이라 캔버스 행 2~3 이다.
    let (mut view, _sent) = view_with_blocks(&[(0, Some(2)), (2, Some(4))], 0);
    view.apply_action_for_test(base::Action::SelectBlocks);
    let mark = view.block_mark().expect("강조가 없다");
    assert_eq!((mark.x, mark.y, mark.w, mark.h), (0, 2, 80, 2));
}

#[test]
fn a_block_taller_than_the_screen_is_clipped_not_spilled() {
    // 수백 줄짜리 빌드 로그가 흔하다. 안 자르면 강조가 패널 밖·크롬 위에 그려진다.
    let (mut view, _sent) = view_with_blocks(&[(0, None)], 0);
    view.apply_action_for_test(base::Action::SelectBlocks);
    let mark = view.block_mark().expect("강조가 없다");
    assert_eq!(mark.y, 0);
    assert_eq!(mark.h, 4, "패널 높이(4행)를 넘겼다");
}

#[test]
fn a_block_scrolled_off_the_screen_draws_nothing() {
    // 화면 밖이면 그릴 것이 없다 — 그렇다고 선택이 풀린 것은 아니다.
    let (mut view, _sent) = view_with_blocks(&[(0, Some(2)), (100, None)], 0);
    view.apply_action_for_test(base::Action::SelectBlocks);
    assert_eq!(view.block_pick, Some((1, 1)), "선택 자체는 살아 있어야 한다");
    assert!(view.block_mark().is_none(), "화면 밖 블록을 그렸다");
}

#[test]
fn an_open_panel_hides_the_highlight() {
    // 판이 떠 있는 동안 키는 그 판의 것이다 — 강조가 남으면 거짓말이 된다
    // (`cursor_cell` 과 같은 규칙).
    let (mut view, _sent) = view_with_blocks(&[(0, None)], 0);
    view.apply_action_for_test(base::Action::SelectBlocks);
    assert!(view.block_mark().is_some());
    view.screens.open(Screen::Keys);
    assert!(view.block_mark().is_none());
}

#[test]
fn moving_the_focus_to_another_pane_ends_the_selection() {
    // 블록 목록은 패널마다 따로다 — 모드를 붙잡고 있으면 `↑`/`↓` 가 안 보이는 패널의
    // 블록을 옮기고 `Ctrl+C` 만 엉뚱한 글을 담는다(화면에는 아무 반응이 없다).
    let (mut view, tx, _sent) = view_with_blocks_tx(&[(0, Some(2)), (2, None)], 0);
    view.apply_action_for_test(base::Action::SelectBlocks);
    assert_eq!(view.block_pick, Some((1, 1)));
    // 서버가 다른 패널을 활성으로 알려 온다(분할 뒤 포커스 이동).
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(serde_json::json!({
            "t": "layout", "cols": 80, "rows": 4, "active": 2,
            "panes": [
                {"id": 1, "x": 0, "y": 0, "w": 40, "h": 4, "title": "sh", "active": false},
                {"id": 2, "x": 40, "y": 0, "w": 40, "h": 4, "title": "sh", "active": true}
            ]
        }))
        .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    assert!(view.block_mark().is_none(), "안 보는 패널의 강조가 남았다");
    // 다음 키에서 모드가 풀린다(그림은 이미 사실과 맞다).
    view.handle_key(Key::Up, Mods::NONE);
    assert_eq!(view.mode.mode(), InputMode::Normal);
    assert_eq!(view.block_pick, None);
}

// ── 세션 이름 제자리 편집(pytmux-3) ──────────────────────────────────────────
//
// 제보: *"세션 이름을 클릭하면 그 자리에서 바로 글자를 고쳐 리네임할 수 있어야 한다"* —
// **인라인 편집**이지 이름 묻는 판을 띄우라는 것이 아니다.
//
// ⚠ 값을 만드는 헬퍼(`base::SessionEdit`)만 재면 **그 값을 쓰는 배선을 지워도 통과한다**
// (루트 CLAUDE.md 의 «공허 통과»). 그래서 여기서는 넷을 잰다: 자리가 **실제로 그려지는가**
// · 클릭이 **편집을 여는가** · 편집 중 키가 **패널로 안 새는가** · Enter 가
// **`rename_session` 을 큐에 넣는가**.

/// `#S` 가 화면에 있는 뷰 — 기본 `status-left` 는 `" "` 라 그 글자가 **없는 것이 기본**이다.
fn view_with_session_name(name: &str) -> (SessionView, Sent) {
    let (mut view, tx, sent) = harness();
    view.config.status_left = " #S ".to_owned();
    tx.send(LinkEvent::Message(Box::new(layout_one_pane()))).unwrap();
    tx.send(LinkEvent::Message(Box::new(
        serde_json::from_value(serde_json::json!({
            "t": "status", "session": name,
            "windows": [{"index": 0, "name": "하나", "active": true}]
        }))
        .unwrap(),
    )))
    .unwrap();
    view.pump_headless();
    (view, sent)
}

fn click_session_name(view: &mut SessionView) -> bool {
    view.chrome_click(base::chrome::ClickTarget::SessionName)
}

/// 상태줄이 그린 글자 — **여백을 턴다.** 편집칸은 글자마다 자기 엘리먼트를 갖고(누른
/// 자리로 커서를 옮기려고), `debug_text_content` 는 엘리먼트 사이에 칸을 끼운다.
fn status_letters(view: &SessionView) -> String {
    view.render_status()
        .debug_text_content()
        .expect("상태줄이 글자를 안 담았다")
        .split_whitespace()
        .collect()
}

#[test]
fn the_status_bar_actually_draws_the_session_name_it_can_edit() {
    // ★ **그려지는지**부터 잰다. 아래 오라클들은 클릭 대상을 이름으로 부르는데, 그
    //   자리가 화면에 없으면 사람은 누를 수 없다 — 그때도 전부 초록이 된다.
    let (view, _sent) = view_with_session_name("놀이터");
    let drawn = status_letters(&view);
    assert!(drawn.contains("놀이터"), "상태줄에 세션 이름이 없다: {drawn:?}");
}

#[test]
fn clicking_the_session_name_opens_an_edit_box_in_place_not_a_prompt() {
    let (mut view, _sent) = view_with_session_name("놀이터");
    assert!(click_session_name(&mut view), "클릭이 아무 일도 안 했다");
    assert_eq!(
        view.session_edit_for_test(),
        Some(("놀이터".to_owned(), 3)),
        "지금 이름이 안 실렸거나 커서가 끝이 아니다"
    );
    // ⛔ 제보의 핵심 — **판을 안 띄운다.** 이름 묻는 화면이 뜨면 그건 종전의 `RenameTab`
    //    동선이고, 제보는 그것을 하지 말라고 한 것이다.
    assert!(view.screens.top().is_none(), "판이 떴다 — 제자리 편집이 아니다");
    // 편집 중인 글자가 **그 자리에** 그려진다(입력칸이 어디 있는지 보여야 한다).
    view.handle_key(Key::Char('2'), Mods::NONE);
    let drawn = status_letters(&view);
    assert!(drawn.contains("놀이터2"), "편집칸이 친 글자를 안 보인다: {drawn:?}");
}

#[test]
fn keys_typed_while_renaming_never_reach_the_pane() {
    // ⛔ 판을 안 띄우므로 화면 스택이 비어 있다 — 키 라우팅 분기가 빠지면 친 글자가
    //    그대로 셸에 찍힌다(제보자가 보게 되는 것은 "리네임이 안 되고 셸이 어지럽다").
    let (mut view, sent) = view_with_session_name("dev");
    click_session_name(&mut view);
    for key in [Key::Char('x'), Key::Backspace, Key::Left, Key::Char('y')] {
        view.handle_key(key, Mods::NONE);
        view.pump_headless();
    }
    // `dev` → x(끝에) → Backspace → ←(커서를 `v` 앞으로) → y = `deyv`.
    assert_eq!(
        view.session_edit_for_test(),
        Some(("deyv".to_owned(), 3)),
        "편집이 커서를 안 따라갔다"
    );
    assert!(
        sent.lock().unwrap().is_empty(),
        "편집 중 키가 서버로 샜다: {:?}",
        sent.lock().unwrap()
    );
}

#[test]
fn enter_queues_the_rename_the_server_already_understands() {
    // 커밋은 `rename_session` — 서버에 이미 있던 명령이다(disposition FULL).
    let (mut view, sent) = view_with_session_name("dev");
    click_session_name(&mut view);
    for key in [Key::Char('2'), Key::Enter] {
        view.handle_key(key, Mods::NONE);
        view.pump_headless();
    }
    assert_eq!(
        *sent.lock().unwrap(),
        vec![Outgoing::Command(Command::RenameSession {
            name: "dev2".to_owned()
        })],
    );
    assert!(view.session_edit_for_test().is_none(), "커밋 뒤에도 편집칸이 남았다");
}

#[test]
fn escape_leaves_the_name_alone() {
    let (mut view, sent) = view_with_session_name("dev");
    click_session_name(&mut view);
    view.handle_key(Key::Char('2'), Mods::NONE);
    view.handle_key(Key::Escape, Mods::NONE);
    view.pump_headless();
    assert!(view.session_edit_for_test().is_none());
    assert!(sent.lock().unwrap().is_empty(), "취소했는데 무언가 나갔다");
    // 취소한 뒤에는 키가 **다시 패널의 것**이다 — 안 그러면 편집칸이 유령으로 남는다.
    view.handle_key(Key::Char('a'), Mods::NONE);
    view.pump_headless();
    assert_eq!(*sent.lock().unwrap(), vec![Outgoing::Input(b"a".to_vec())]);
}

#[test]
fn the_same_name_is_not_worth_a_round_trip() {
    let (mut view, sent) = view_with_session_name("dev");
    click_session_name(&mut view);
    view.handle_key(Key::Enter, Mods::NONE);
    view.pump_headless();
    assert!(sent.lock().unwrap().is_empty(), "안 바뀐 이름을 보냈다");
    // 빈 이름도 마찬가지다(다 지우고 Enter).
    click_session_name(&mut view);
    for _ in 0..3 {
        view.handle_key(Key::Backspace, Mods::NONE);
    }
    view.handle_key(Key::Enter, Mods::NONE);
    view.pump_headless();
    assert!(sent.lock().unwrap().is_empty(), "빈 이름을 보냈다");
}

#[test]
fn confirmed_korean_lands_in_the_box_instead_of_the_shell() {
    // ★ 한글은 **키가 아니라 확정 문자열**로 온다(`on_typed_characters`). 그 갈래가
    //   빠지면 영문은 되는데 한글만 셸로 새서, 증상이 "한글로는 리네임이 안 된다"가 된다.
    let (mut view, sent) = view_with_session_name("dev");
    click_session_name(&mut view);
    assert!(view.handle_typed("놀이터"), "확정 글자를 아무도 안 받았다");
    view.pump_headless();
    assert_eq!(
        view.session_edit_for_test().map(|(t, _)| t),
        Some("dev놀이터".to_owned())
    );
    assert!(sent.lock().unwrap().is_empty(), "확정 글자가 패널로 샜다");
}

#[test]
fn there_is_nothing_to_click_when_the_format_hides_the_session_name() {
    // ⛔ 기본 `status-left` 는 두 클라 모두 `" "` 다 — `#S` 를 넣지 않은 사람에게는
    //    **누를 자리 자체가 없고 아무 일도 안 일어나는 것이 정상**이다(제보의 ⛔).
    let (mut view, _sent) = view_with_session_name("dev");
    view.config.status_left = " ".to_owned();
    assert!(!click_session_name(&mut view), "없는 자리가 편집을 열었다");
    assert!(view.session_edit_for_test().is_none());
}

#[test]
fn an_edit_box_that_lost_its_spot_stops_swallowing_keys() {
    // ⛔ 편집 중에 `#S` 자리가 사라지면(형식이 바뀌었다·세션 이름이 비었다) 보이지도
    //    않는 입력칸이 키를 계속 삼켜 **패널이 먹통**이 된다.
    let (mut view, sent) = view_with_session_name("dev");
    click_session_name(&mut view);
    view.config.status_left = " ".to_owned();
    view.handle_key(Key::Char('a'), Mods::NONE);
    view.pump_headless();
    assert!(view.session_edit_for_test().is_none(), "유령 입력칸이 남았다");
    assert_eq!(*sent.lock().unwrap(), vec![Outgoing::Input(b"a".to_vec())]);
}

#[test]
fn clicking_a_letter_while_editing_only_moves_the_cursor() {
    let (mut view, _sent) = view_with_session_name("놀이터");
    click_session_name(&mut view);
    assert!(view.chrome_click(base::chrome::ClickTarget::SessionCursor(1)));
    assert_eq!(view.session_edit_for_test(), Some(("놀이터".to_owned(), 1)));
    // 그 자리에 글자를 끼운다 — 커서가 안 옮겨졌으면 끝에 붙는다.
    view.handle_key(Key::Char('x'), Mods::NONE);
    assert_eq!(
        view.session_edit_for_test().map(|(t, _)| t),
        Some("놀x이터".to_owned())
    );
}

#[test]
fn the_session_name_can_be_edited_from_the_right_hand_format_too() {
    // `#S` 를 `status-right` 에 둔 설정도 같은 자리를 얻는다(파이썬과 같다).
    let (mut view, _sent) = view_with_session_name("dev");
    view.config.status_left = " ".to_owned();
    view.config.status_right = " #S ".to_owned();
    assert!(click_session_name(&mut view));
    assert_eq!(view.session_edit_for_test(), Some(("dev".to_owned(), 3)));
    let drawn = status_letters(&view);
    assert!(drawn.contains("dev"), "오른쪽 세션 이름이 안 그려졌다: {drawn:?}");
}

#[test]
fn the_drawn_session_name_is_actually_wrapped_in_a_click_target() {
    // ★ 위 오라클들은 `chrome_click` 을 **직접** 부른다 — 그리기가 그 자리를 클릭
    //   대상으로 안 감싸도 전부 초록이다(마우스 히트테스트는 레이아웃을 요구해 창
    //   없이 못 세운다). 그래서 이 한 자리는 소스로 잰다 — 위 `on_marked_text` 오라클과
    //   같은 종류다.
    let body = SESSION_VIEW_SRC
        .split_once("    fn render_session_run(")
        .expect("`render_session_run` 을 못 찾았다 — 옮겼으면 이 오라클도 옮길 것")
        .1;
    for needle in [
        "ClickTarget::SessionName",
        "ClickTarget::SessionCursor",
        "clickable_status(",
    ] {
        assert!(
            body.contains(needle),
            "세션 이름을 그리면서 `{needle}` 를 안 쓴다 — 화면에 글자는 있는데 눌리지 않는다"
        );
    }
    // 그리고 그 함수를 **부르는 자리**가 양쪽 형식에 다 있어야 한다.
    let left = SESSION_VIEW_SRC
        .split_once("    fn render_status_left(")
        .expect("`render_status_left` 를 못 찾았다")
        .1;
    assert!(
        left.contains("self.render_session_run("),
        "`status-left` 의 `#S` 가 누를 자리로 안 그려진다"
    );
    let status = SESSION_VIEW_SRC
        .split_once("    fn render_status(&self)")
        .expect("`render_status` 를 못 찾았다")
        .1;
    assert!(
        status.contains("self.render_session_run("),
        "`status-right` 의 `#S` 가 누를 자리로 안 그려진다"
    );
}

#[test]
fn a_panel_on_screen_keeps_the_edit_box_from_opening_behind_it() {
    // ⚠ 상태줄은 판 **뒤에서도** 눌린다. 그때 편집칸을 열면 키는 판의 것이라(core 규칙)
    //    글자를 못 받는 입력칸이 남는다 — 사용자에게는 "리네임이 먹통"으로 보인다.
    let (mut view, _sent) = view_with_session_name("dev");
    view.screens.open(base::screens::Screen::Commands);
    assert!(!click_session_name(&mut view), "판 뒤에서 편집칸이 열렸다");
    assert!(view.session_edit_for_test().is_none());
}

#[test]
fn the_canvas_takes_the_leftover_and_the_status_bar_stays_on_the_floor() {
    // ★ `pytmux-162`. 캔버스 격자가 못 채운 아래 빈 높이를 **캔버스가** 받도록 바꿨다
    //   (종전에는 그 자리를 빈 위젯이 먹었고, 그래서 테두리 상자만 그 위에서 끝났다).
    //   ⛔ 그 바꿈이 **상태줄을 밀면** 고친 것보다 나쁘다 — 창 바닥에 그대로 있어야 한다.
    //   ⚠ 선 자체는 여기서 못 잰다(시험 글꼴은 칸 폭이 0이라 오버레이가 안 그린다) —
    //     그 산수는 `splitter_tests.rs` 가, 그림은 라이브 스크린샷이 잡는다.
    let screen: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "screen", "pane": 1,
        "rows": [[["HELLO-ORACLE", {}]]], "cursor": [0, 0], "wrap": [], "top": 0
    }))
    .unwrap();
    let boxes = painted_boxes(vec![layout_one_pane(), screen], &[]);
    let canvas = painted_y(&boxes, "HELLO-ORACLE").expect("캔버스가 안 그려졌다");
    // 상태줄은 **날짜 run** 이 짚는다 — 머신 이름·시각과 달리 모양이 고정이다
    // (`looks_like_a_date` 문서).
    let status = boxes
        .iter()
        .find(|(t, _)| looks_like_a_date(t))
        .map(|(_, y)| *y)
        .expect("상태줄이 안 그려졌다");
    // 창은 이 하네스에서 600 높이다(`painted_scene_setup`).
    assert!(
        status > canvas + 300.,
        "캔버스와 상태줄 사이에 잴 만한 빈 높이가 없다 — 이 오라클이 공허하다: {boxes:?}"
    );
    assert!(
        (560.0..600.0).contains(&status),
        "상태줄이 창 바닥을 떠났다(밀렸거나 창 밖으로 나갔다): {status} {boxes:?}"
    );
    assert!(
        canvas < 100.,
        "캔버스가 위에서 밀려 내려왔다 — 빈 높이가 위로 갔다: {canvas}"
    );
}

// ── 판 폭은 내용을 안 따라간다 (pytmux-158) ─────────────────────────────────
//
// ⚠ **글자로는 이 결함을 못 잰다.** 시험 폰트는 빈 `Line` 을 돌려줘 글자 폭이 전부 0
// 이다(위 「그리기 오라클」 절 머리말) — `:ncd` 에 아무리 긴 이름을 넣어도 여기서는 판이
// 안 넓어진다. 그런데 결함의 자리는 글자가 아니라 **사슬**이다: 「줄 하나가 판보다 넓으면
// 판이 그만큼 넓어진다」. 그 사슬은 폭을 **아는** 상자로 그대로 세울 수 있다.
//
// 그래서 아래 뷰가 판 안쪽을 같은 모양(가로 `Flex` 한 줄 → 세로 `Flex`)으로 짓고 그 줄에
// 판보다 넓은 상자를 하나 넣은 다음, 바깥을 ⑴ 종전 방식(`ConstrainedBox::with_width`)
// ⑵ 지금 방식(`PanelBox`)으로 각각 감싸 **그려진 면의 가로 폭**을 잰다.

/// 이 오라클이 자기 면을 찾는 표식 — 배경색이 열쇠다(다른 데서 안 쓰는 값).
const PROBE_FILL: ColorU = ColorU { r: 1, g: 2, b: 3, a: 0xff };
/// 판 안쪽 폭. 실제 값(`panel_inner_width`)과 달라도 되는 이유: 재는 것은 「준 폭을
/// 지키나」이지 그 폭이 얼마냐가 아니다.
const PROBE_INNER: f32 = 300.;
/// 줄 하나가 원하는 폭 — 판보다 **넓다**(긴 파일 이름의 자리).
const PROBE_OVERFLOW: f32 = 900.;

struct PanelWidthProbe {
    /// 지금 방식(`PanelBox`)으로 감쌀까. 거짓이면 종전 방식이다.
    pinned: bool,
    /// 판 안의 줄 하나가 원하는 폭.
    row: f32,
}

impl Entity for PanelWidthProbe {
    type Event = ();
}

impl TypedActionView for PanelWidthProbe {
    type Action = ();

    fn handle_action(&mut self, _: &(), _: &mut ViewContext<Self>) {}
}

impl View for PanelWidthProbe {
    fn ui_name() -> &'static str {
        "PanelWidthProbe"
    }

    fn render(&self, _: &AppContext) -> Box<dyn Element> {
        // 판 안의 «한 줄» — 실제 목록 줄과 같은 모양이다. 가로 `Flex` 는 자기 아이에게
        // 가로 한도를 **무한대로** 주므로, 이 상자는 판보다 넓게 눕는다.
        let row = Flex::row()
            .with_main_axis_size(MainAxisSize::Min)
            .with_child(
                ConstrainedBox::new(Empty::new().finish())
                    .with_width(self.row)
                    .with_height(10.)
                    .finish(),
            )
            .finish();
        let column = Flex::column()
            .with_main_axis_size(MainAxisSize::Min)
            .with_child(row)
            .finish();
        let body = if self.pinned {
            Clipped::new(PanelBox::new(column, PROBE_INNER).finish()).finish()
        } else {
            // 종전 방식 — 한도를 **주기만** 하고 크기는 아이가 잰 값을 그대로 돌려준다.
            ConstrainedBox::new(column).with_width(PROBE_INNER).finish()
        };
        // 여백 없는 상자 하나 — 그려진 면의 폭이 곧 안쪽 폭이다.
        Container::new(body).with_background_color(PROBE_FILL).finish()
    }
}

/// 위 뷰를 한 프레임 그려 **표식 면의 가로 폭**을 돌려준다.
///
/// 창을 넉넉히 크게 잡는다 — 창이 좁으면 무엇이 폭을 정했는지(판이냐 창이냐) 갈리지 않는다.
fn probe_panel_width(pinned: bool, row: f32) -> f32 {
    use warpui::platform::WindowStyle;
    use warpui::{EntityIdSet, Presenter, WindowInvalidation};
    warpui::App::test((), move |mut app| async move {
        let (window_id, _handle) =
            app.add_window(WindowStyle::NotStealFocus, move |_| PanelWidthProbe { pinned, row });
        let mut presenter = Presenter::new(window_id);
        let mut updated = EntityIdSet::default();
        updated.insert(app.root_view_id(window_id).unwrap());
        let invalidation = WindowInvalidation {
            updated,
            ..Default::default()
        };
        app.update(move |ctx| {
            presenter.invalidate(invalidation, ctx);
            let scene = presenter.build_scene(vec2f(1600., 600.), 1., None, ctx);
            scene
                .layers()
                .flat_map(|layer| layer.rects.iter())
                .find(|r| matches!(r.background, warpui::elements::Fill::Solid(c) if c == PROBE_FILL))
                .map(|r| r.bounds.width())
                .expect("표식 면이 안 그려졌다 — 오라클이 아무것도 안 재고 있다")
        })
    })
}

#[test]
fn the_probe_reproduces_the_old_panel_widening() {
    // ★ **결함부터 재현한다.** 이 단언이 붉어지면 아래 단언들은 아무것도 증명하지 않는다
    //   (빈 오라클로 「고쳤다」를 말하는 자리다).
    let width = probe_panel_width(false, PROBE_OVERFLOW);
    assert_eq!(
        width, PROBE_OVERFLOW,
        "종전 방식이 판을 안 넓혔다 — 오라클이 결함을 못 세우고 있다(잰 폭 {width})"
    );
}

#[test]
fn the_old_way_already_held_the_floor_only_the_ceiling_was_open() {
    // ★ 결함의 **모양**을 정확히 적어 둔다. 종전 방식도 아래쪽 한도는 지켰다 — 세로
    //   `Flex` 가 layout 끝에서 `size.x.max(constraint.min.x())` 를 하기 때문이다.
    //   그래서 "짧을 때 좁아진다"가 아니라 **"길 때 넓어진다"** 한쪽만 결함이었다.
    //   (이 줄이 없으면 다음 사람이 `PanelBox` 를 「최소 폭 주는 상자」로 오해한다.)
    let narrow = probe_panel_width(false, PROBE_INNER / 3.);
    assert_eq!(
        narrow, PROBE_INNER,
        "종전 방식이 아래쪽 한도도 안 지켰다 — 결함의 모양이 기록과 다르다(잰 폭 {narrow})"
    );
}

#[test]
fn the_panel_keeps_its_width_whatever_it_holds() {
    // pytmux-158: `:ncd` 에서 긴 이름이 목록에 들어오면 판이 그만큼 넓어졌다. 판 폭은
    // 화면이 정하는 값이고 내용은 못 정한다 — **길든 짧든** 같은 값이라야 한다.
    for row in [PROBE_OVERFLOW, PROBE_INNER / 3.] {
        let width = probe_panel_width(true, row);
        assert_eq!(
            width, PROBE_INNER,
            "판 폭이 내용을 따라갔다 — 목록을 오르내릴 때마다 판이 흔들린다\
             (줄 {row} → 판 {width})"
        );
    }
}

#[test]
fn the_panel_width_is_said_in_exactly_one_place() {
    // 안쪽 폭 = 바깥 폭 − (여백 + 테두리) × 2. 두 값을 각각 글자로 적으면 언젠가 갈린다.
    //
    // ⚠ **2026-08-23 에 메서드가 됐다**(pytmux-368): 팔레트 폭이 이제 캔버스를 따라가므로
    //   뷰 없이는 답할 수 없다. 재는 것(두 값이 한 곳에서 나온다)은 그대로다.
    let (view, _tx, _sent) = harness();
    for screen in [Screen::Tree, Screen::Commands, Screen::Buffers] {
        assert_eq!(
            view.panel_inner_width(screen),
            view.panel_width(screen)
                - 2. * (SessionView::PANEL_PAD + SessionView::PANEL_BORDER),
            "{screen:?} 의 안쪽 폭이 바깥 폭에서 안 나온다"
        );
    }
    assert!(
        view.panel_width(Screen::Commands) > view.panel_width(Screen::Tree),
        "팔레트가 더 넓어야 한다(이름+설명 한 줄을 눈으로 잇는다)"
    );
}

#[test]
fn the_palette_follows_the_canvas_instead_of_a_fixed_width() {
    // 제보(pytmux-368): 정본의 `esc :` 는 **단말 좌우 폭 전체**를 쓰는데 GUI 는 900px
    // 상수라, 캔버스가 넓은 창에서 목록이 가운데 갇히고 설명이 더 일찍 접혔다.
    //
    // ⛔ 그릇은 **창이 아니라 캔버스**다 — 정본이 말하는 「단말 전체」가 곧 캔버스다.
    let (mut view, tx, _sent) = harness();
    let narrow = view.panel_width(Screen::Commands);
    // 넓은 캔버스를 먹인다(80칸 → 400칸).
    let wide: ServerMessage = serde_json::from_value(serde_json::json!({
        "t": "layout", "cols": 400, "rows": 40, "active": 1,
        "panes": [{"id": 1, "x": 0, "y": 0, "w": 400, "h": 40, "title": "sh", "active": true}]
    }))
    .unwrap();
    tx.send(LinkEvent::Message(Box::new(wide))).unwrap();
    view.pump_headless();
    // 셀 폭을 아직 못 재는 헤드리스에서는 상수로 떨어진다 — 그때는 값이 안 움직이는 것이
    // **맞다**(첫 프레임 전에도 판은 떠야 한다). 잴 수 있으면 넓어져야 한다.
    if view.cell_px_for_test().is_some() {
        assert!(
            view.panel_width(Screen::Commands) > narrow,
            "캔버스가 넓어졌는데 팔레트 폭이 그대로다"
        );
    }
    // 어느 쪽이든 **칸 수가 폭에서 나온다**는 성질은 지켜져야 한다 — 상수에 묶여 있으면
    // 판만 넓어지고 설명은 그대로 접힌다(제보가 짚은 그 증상).
    assert!(
        view.palette_cols() >= SessionView::PAL_NAME_COLS + SessionView::PAL_OPTS_COLS + 12,
        "설명 칸이 남지 않는 폭이 나왔다"
    );
}

#[test]
fn the_panel_is_still_handed_to_the_box_that_pins_it() {
    // ★ 위 오라클은 `PanelBox` 가 **제 일을 하나**만 잰다. 판이 그 상자를 안 지나면
    //   화면은 종전대로 흔들리는데 시험은 전부 통과한다(호출 제거 뮤테이션).
    let body = source_after("fn render_screen_panel(", 14000);
    assert!(
        body.contains("PanelBox::new(column.finish(), self.panel_inner_width(screen))"),
        "판이 `PanelBox` 를 안 지난다 — 폭이 다시 내용을 따라간다(pytmux-158)"
    );
    assert!(
        body.contains("Clipped::new("),
        "넘친 줄을 안 자른다 — 판 폭은 고정인데 글자가 판 밖으로 삐져나간다"
    );
}

// ── 다열 판의 한 칸(설계 §4.3 `panel` · pytmux-126) ───────────────────────────

#[test]
fn a_panel_cell_pins_the_extra_column_to_the_right_edge() {
    // 크기를 오른자리에 세우는 것이 요점이다 — 왼쪽에 붙이면 이름 길이에 따라 숫자가
    // 들쭉날쭉해 **열끼리 비교가 안 된다**(정본 `_item_segment` 와 같은 짜임).
    let cell = SessionView::panel_cell("a.txt", "3B", 16);
    assert_eq!(proto::footer::width(&cell), 16, "칸이 폭에 안 맞는다: {cell:?}");
    assert!(cell.starts_with("a.txt"), "{cell:?}");
    assert!(cell.ends_with("3B"), "{cell:?}");
}

#[test]
fn a_panel_cell_cuts_the_name_not_the_number() {
    // 크기를 자르면 그 숫자가 **거짓**이 되지만, 이름은 잘려도 `…` 가 그 사실을 말한다.
    let cell = SessionView::panel_cell("아주아주긴이름.txt", "12.3M", 14);
    assert!(cell.ends_with("12.3M"), "숫자를 잘랐다: {cell:?}");
    assert!(cell.contains('…'), "이름을 자르고도 표식이 없다: {cell:?}");
    assert!(proto::footer::width(&cell) <= 14, "{cell:?}");
}

#[test]
fn a_panel_cell_that_cannot_hold_both_keeps_the_name() {
    // 자리가 없으면 **이름이 남는다** — 골라야 하는 화면에서 이름이 사라지면 그 줄은
    // 아무것도 아니게 된다(크기는 커서 줄의 다른 자리에서도 볼 수 있다).
    let cell = SessionView::panel_cell("a.txt", "1.5G", 5);
    assert!(proto::footer::width(&cell) <= 5, "{cell:?}");
    assert!(!cell.contains("1.5G"), "{cell:?}");
}

#[test]
fn a_panel_cell_without_an_extra_column_is_just_the_name() {
    // `..` 줄처럼 칸이 없는 것도 있다 — 그때 오른쪽에 빈 공백만 남기면 그만이다.
    let cell = SessionView::panel_cell("..", "", 8);
    assert_eq!(proto::footer::width(&cell), 8, "{cell:?}");
    assert!(cell.starts_with(".."), "{cell:?}");
}


/// 판 바닥 안내줄은 **잘리는 대신 접힌다**(pytmux-371 ⓐ).
///
/// # 무엇이 있었나 (실측 2026-08-30 · 1280x800 · 배율 1.5)
///
/// 판 폭은 화면이 정하고(`panel_width` = 760px · pytmux-158) 안쪽은 730px 다. 안내줄은
/// 그 안에 그려지고, 넘친 부분은 `Clipped` 가 **말없이 잘라 냈다**:
///
/// | 화면 | 꼬리줄 | |
/// | --- | --- | --- |
/// | Period | `… · o machine · s scenari` | 잘림 — `u /usage` 가 사라졌다 |
/// | Warn | `… · o machine · s scena` | 잘림 |
/// | Machine · Session · Limit | `… · s scenario · u /usage` | 우연히 들어맞음 |
///
/// ⛔ 미관이 아니다. pytmux-185 계약에서 **꼬리줄이 광고하는 조작이 그 화면의 최소
/// 요건**이고 pytmux-371 은 요건표를 바로 그 줄로 적었다 — 광고가 안 보이면 요건이
/// 사라진다.
///
/// # 왜 `PANEL_COLS` 로는 안 잡혔나
///
/// 줄을 줄이는 자(`footer::elide`)는 `PANEL_COLS = 110` 칸을 쓰는데, 730px 에 실제로
/// 들어가는 것은 **약 100칸**이다(11px 글자의 전진폭 실측 ≈ 7.3px). 그래서 103칸짜리
/// 안내줄은 「자를 필요 없다」로 통과한 뒤 픽셀에서 잘렸다 — 칸 예산과 픽셀 예산이
/// 서로 다른 값을 믿고 있었다.
///
/// # 이 시험이 무는 것
///
/// 고침은 **그 줄만** `soft_wrap` 을 켠 것이다(`Text::new_inline` 은 거짓,
/// [`Text::new`] 는 참이 기본). 접힘 여부는 렌더 결과라 단위 시험이 픽셀로 못 재므로,
/// 여기서는 **그 호출이 살아 있는지**를 문다 — 「호출 제거」 뮤테이션이 이 저장소에서
/// 두 번 공허 통과를 만든 자리다(CLAUDE.md §표시 기능은 호출부까지 단언).
#[test]
fn the_panel_hint_wraps_instead_of_being_clipped() {
    let src = SESSION_VIEW_SRC;
    assert!(
        src.contains("fn hint_text("),
        "안내줄 전용 헬퍼가 사라졌다 — 접힘이 어디서 오는지 말하는 자리가 없다"
    );
    assert!(
        src.contains("Some(h) => self.hint_text(h, self.font, 11., palette::DIM),"),
        "플러그인 판의 안내줄이 다시 안 접히는 글자로 돌아갔다(pytmux-371 ⓐ 재발)"
    );
    assert!(
        src.contains("None => self.hint_text(hint, self.ui_font, 11., palette::DIM),"),
        "정적 화면의 안내줄이 다시 안 접히는 글자로 돌아갔다"
    );
    // ⛔ 대조군 — **나머지 줄은 접히면 안 된다.** 판 안의 표·막대는 칸 격자에 기대고
    //    있어서, 거기까지 접히면 한 줄이 두 줄이 되어 「예산 = 줄 수」가 깨진다
    //    (pytmux-369·373 이 픽셀로 값을 치른 자리다).
    assert!(
        src.contains("Text::new_inline(s.into(), self.font, self.scaled(size))"),
        "본문 글자가 접히는 쪽으로 바뀌었다 — 판의 줄 수 셈이 깨진다"
    );
}


/// 글꼴 설정은 **지금 창**에 먹는다(pytmux-408 ④).
///
/// # 왜 소스로 묻나
///
/// 다시 까는 일에는 `ViewContext` 가 필요해 헤드리스 하네스가 그 경로를 못 탄다(이 뷰의
/// 시험이 `with_fonts` 로 창 없이 서는 이유와 같은 벽이다). 그래서 **결정**이 아니라
/// **배선**을 문다 — 「호출 제거」가 이 저장소에서 두 번 공허 통과를 만든 자리다
/// (CLAUDE.md §표시 기능은 호출부까지 단언).
///
/// 값이 바뀌는 자리는 셋이고(설정 화면의 대답 · `:set` · `source-file`) 전부 `ctx` 가
/// 없다. 그 셋에 각자 글꼴 등록을 넣으면 하나만 빠져도 **어떤 길로 바꿨느냐에 따라 먹기도
/// 안 먹기도** 하므로, 화해는 `pump` 한 곳이다. 이 시험이 그 한 곳을 지킨다.
#[test]
fn changing_the_font_setting_takes_effect_without_a_restart() {
    let src = SESSION_VIEW_SRC;
    assert!(
        src.contains("fn reconcile_font("),
        "글꼴 화해 자리가 사라졌다 — 설정을 바꿔도 다음 기동에나 먹는다"
    );
    assert!(
        src.contains("let font_changed = self.reconcile_font(ctx);"),
        "`pump` 가 글꼴을 더는 안 맞춘다(pytmux-408 ④ 재발) — 함수만 남고 호출이 죽었다"
    );
    // 다시 깔았으면 **다시 그려야** 한다. 안 그러면 다음 입력이 올 때까지 옛 글꼴이다.
    assert!(
        src.contains("let dirty = dirty || font_changed;"),
        "글꼴을 바꾸고 다시 안 그린다 — 다음 입력 전까지 옛 글자가 남는다"
    );
    // 셀 크기는 글꼴을 탄다 — 자리표를 안 버리면 마우스 좌표가 옛 격자로 풀린다.
    assert!(
        src.contains("self.cell_px.set(None);"),
        "글꼴을 바꾸고 셀 자리표를 안 버렸다 — 클릭이 엉뚱한 칸으로 간다"
    );
}

/// 고른 글꼴이 **첫 프레임부터** 먹는다(pytmux-408 ①).
///
/// `with_fonts` 가 설정을 읽는 자리는 글꼴을 까는 자리보다 **뒤**라, 그 차례를 안 고치면
/// `font-family` 는 「적었는데 다음 기동에나 먹는」 값이 된다. `new` 가 설정을 먼저 읽어
/// 그 이름으로 깔고, 깐 이름을 `font_want` 에 적어 위 화해가 헛돌지 않게 한다.
#[test]
fn the_font_setting_is_honoured_on_the_very_first_frame() {
    let src = SESSION_VIEW_SRC;
    assert!(
        src.contains("let want = base::Config::load().font_family;"),
        "첫 프레임 전에 설정을 안 읽는다 — 고른 글꼴이 한 박자 늦게 먹는다"
    );
    assert!(
        src.contains("mono_font::install_preferred(cache, &want)"),
        "`new` 가 고른 글꼴을 안 넘긴다 — 후보 목록만 본다"
    );
    assert!(
        src.contains("view.font_want = want;"),
        "깐 이름을 안 적었다 — 화해가 매 프레임 다시 깔거나 영영 안 깐다"
    );
}

/// `End` 다음 `Enter` 가 **마지막 줄**을 고른다(pytmux-417 ①의 곁가지).
///
/// `End` 는 커서를 `usize::MAX` 로 두고 「자르는 것은 그리는 쪽」이 규약이다
/// (`press_list`·`press_settings`). 그 규약을 `plugin_view_chosen` 이 안 지키면
/// `End` 다음 `Enter` 가 **줄 없는 번호**와 `input: None` 을 실어 보내고, 서버는 그것을
/// 조용히 무시한다 — 화면에는 「아무 일도 안 났다」로 보인다.
///
/// ⛔ 이 오라클이 없으면 항해 키를 살린 것이 **새 구멍**을 만든다. 키가 죽어 있던
///    동안에는 커서가 `usize::MAX` 가 될 길이 없었다.
#[test]
fn end_then_enter_picks_the_last_row() {
    let out = sent_after(
        vec![layout_one_pane(), plugin_list_screen()],
        &[(Key::End, Mods::NONE), (Key::Enter, Mods::NONE)],
    );
    let picked = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::PluginAction { row, input, .. }) => {
            Some((*row, input.clone()))
        }
        _ => None,
    });
    assert_eq!(
        picked,
        Some((1, Some("68997".to_owned()))),
        "End 뒤 Enter 가 마지막 줄을 안 골랐다: {out:?}"
    );
}

/// `Home` 다음 `Enter` 는 첫 줄이다 — 반대쪽도 재야 「End 만 맞춘」 구현이 안 지난다.
#[test]
fn home_then_enter_picks_the_first_row() {
    let out = sent_after(
        vec![layout_one_pane(), plugin_list_screen()],
        &[
            (Key::Down, Mods::NONE),
            (Key::Home, Mods::NONE),
            (Key::Enter, Mods::NONE),
        ],
    );
    let picked = out.iter().find_map(|o| match o {
        Outgoing::Command(Command::PluginAction { row, input, .. }) => {
            Some((*row, input.clone()))
        }
        _ => None,
    });
    assert_eq!(picked, Some((0, Some("68995".to_owned()))), "{out:?}");
}

// ── 같은 줄이 두 번 그려지지 않나 (원문 가드 · pytmux-33 ⓖ3) ─────────────────
//
// ☠ **이 부류는 「정본에 있는데 GUI 에 없다」의 반대라 눈으로 안 잡힌다.** 화면은
// 여전히 그 줄을 보이므로 스크린샷 대조도 「있다」로 읽고, 단위 오라클은 값이 옳은지만
// 잰다 — 같은 값을 **두 번** 그리는 것은 둘 다 초록이다.
//
// 실제로 그랬다: `mdir` 판의 볼륨 머리줄을 `"panel"` 갈래가 고정폭·시안으로 그리고
// 있었는데(CL 73249), 토큰 판의 공유 머리줄(pytmux-419 ②)이 **공통 크롬**으로 서면서
// (CL 74528) 그 판정을 안 봐 같은 줄이 UI 글꼴·DIM 으로 한 번 더 떴다. 정본 mdir 은
// 그 줄을 판 안에 한 줄만 그리므로 ⓖ3(전면 1:1 대조)의 갈림이기도 하다.
//
// ⚠ 재는 것은 **배선**이다(이 크레이트에는 그린 것을 되읽는 하네스가 없다 — 위
// §원문 가드의 사정 그대로). 그래서 "같은 스펙 칸을 두 자리가 그리나"까지만 잰다.

/// `render_plugin_view` 를 **공통 크롬**과 **모양별 갈래**로 가른다.
///
/// 갈래 몸통은 깊이를 세어 자른다 — 정규식으로 긁으면 안쪽 `match` 의 갈래까지 딸려
/// 온다(`proto/tests/plugin_screen_conformance.rs` 가 같은 자리에서 겪은 것).
fn plugin_view_parts() -> (String, Vec<(String, String)>) {
    let src = include_str!("session_view.rs");
    const MATCH: &str = "match spec.kind.as_str() {";
    let at = src
        .find("fn render_plugin_view(")
        .expect("원문에서 `render_plugin_view` 를 못 찾았다");
    let m = src[at..].find(MATCH).expect("`render_plugin_view` 안에서 모양 갈래를 못 찾았다") + at;
    let chrome = src[at..m].to_owned();

    let open = m + MATCH.len();
    let mut depth = 1usize;
    let mut end = src.len();
    for (i, ch) in src[open..].char_indices() {
        match ch {
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    end = open + i;
                    break;
                }
            }
            _ => {}
        }
    }
    let body = &src[open..end];

    // 갈래 머리는 **깊이 0** 에서 줄 앞에 오는 `"<이름>" =>` 다.
    let mut heads: Vec<(usize, String)> = Vec::new();
    let mut depth = 0usize;
    let bytes = body.as_bytes();
    for (i, ch) in body.char_indices() {
        match ch {
            '{' => depth += 1,
            '}' => depth = depth.saturating_sub(1),
            '"' if depth == 0 && i > 0 && (bytes[i - 1] == b'\n' || bytes[i - 1] == b' ') => {
                let rest = &body[i + 1..];
                if let Some(q) = rest.find('"') {
                    let name = &rest[..q];
                    if !name.is_empty()
                        && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_')
                        && rest[q + 1..].trim_start().starts_with("=>")
                    {
                        heads.push((i, name.to_owned()));
                    }
                }
            }
            _ => {}
        }
    }
    let arms = heads
        .iter()
        .enumerate()
        .map(|(n, (pos, name))| {
            let till = heads.get(n + 1).map(|(p, _)| *p).unwrap_or(body.len());
            (name.clone(), body[*pos..till].to_owned())
        })
        .collect();
    (chrome, arms)
}

/// 글로 그려지는 스펙 칸들 — `spec.say_<칸>()` 이 그 자리다.
///
/// `say_*` 로 세는 이유: 이 스펙의 글은 **전부** 그것을 지나 우리 로케일로 다시 지어진다
/// (`proto::session` 의 `say_title`·`say_head` …). 원문(`spec.head`)은 "비었나"를 묻는
/// 자리에도 쓰여서 그것으로 세면 판정이 그리는 것과 갈린다.
fn drawn_fields(text: &str) -> std::collections::BTreeSet<String> {
    let mut out = std::collections::BTreeSet::new();
    let mut rest = text;
    while let Some(at) = rest.find("spec.say_") {
        let tail = &rest[at + "spec.say_".len()..];
        let n = tail.find(|c: char| !(c.is_ascii_alphanumeric() || c == '_')).unwrap_or(tail.len());
        if tail[n..].starts_with("()") {
            out.insert(tail[..n].to_owned());
        }
        rest = &tail[n..];
    }
    out
}

#[test]
fn a_shared_chrome_line_is_not_drawn_again_by_a_kind_arm() {
    let (chrome, arms) = plugin_view_parts();
    let shared = drawn_fields(&chrome);
    assert!(
        !shared.is_empty(),
        "공통 크롬이 아무 칸도 안 그린다 — 이 오라클이 재는 자리를 못 찾았다\
         (`render_plugin_view` 의 모양 갈래 앞 구간을 봤다)"
    );

    for (kind, body) in &arms {
        for field in drawn_fields(body).intersection(&shared) {
            // 공통 쪽이 **그 모양을 뺐으면** 두 번이 아니다. 판정은 그 칸을 그리는
            // `if` 의 조건 안에 있어야 한다 — 함수 아무 데나 있는 `kind !=` 를 세면
            // 남의 갈래의 판정으로 이 칸이 면제된다.
            let call = format!("spec.say_{field}()");
            let at = chrome.find(&call).expect("방금 센 호출을 못 찾는다");
            let cond = chrome[..at].rfind("if ").map(|i| &chrome[i..at]).unwrap_or("");
            let excused = cond.contains(&format!("spec.kind != \"{kind}\""));
            assert!(
                excused,
                "`{field}` 를 공통 크롬과 `\"{kind}\"` 갈래가 **둘 다** 그린다 — \
                 그 화면에서는 같은 줄이 두 번 뜬다.\n\
                 둘 중 하나를 지우거나, 공통 쪽 조건에 `spec.kind != \"{kind}\"` 를 넣을 것.\n\
                 (갈래가 제 서식으로 그려야 하는 줄이면 공통 쪽이 빠지는 것이 맞다 — \
                 판의 머리줄은 열 폭에 맞춰 고정폭으로 잘라야 격자와 자리가 맞고, \
                 판의 기하는 `panel_grid()` 가 그 줄을 이미 세고 있다.)\n\
                 공통 크롬의 조건: {cond}"
            );
        }
    }
}

/// 위 오라클이 **갈래를 실제로 찾았나** — 못 찾으면 무엇을 견줘도 초록이다.
///
/// ⛔ 빈 목록을 통과로 두지 않는다(저장소 규율: 파싱 실패를 초록으로 위장하지 않는다).
#[test]
fn the_duplicate_line_oracle_actually_sees_the_kind_arms() {
    let (_, arms) = plugin_view_parts();
    let names: Vec<&str> = arms.iter().map(|(k, _)| k.as_str()).collect();
    for want in ["list", "text", "table", "panel", "form"] {
        assert!(
            names.contains(&want),
            "모양 갈래 `{want}` 를 원문에서 못 갈랐다 — 위 오라클이 헛돈다: {names:?}"
        );
    }
    // 갈래 몸통이 **머리만** 잡혔으면(자르기가 깨졌으면) 역시 헛돈다.
    let panel = arms.iter().find(|(k, _)| k == "panel").map(|(_, b)| b.len()).unwrap_or(0);
    assert!(panel > 500, "`\"panel\"` 갈래의 몸통이 {panel}자다 — 자르기가 깨졌다");
}
