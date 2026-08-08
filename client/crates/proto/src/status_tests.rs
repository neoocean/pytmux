use super::*;

const NOW: &str = "2026-07-29T13:45:07";

fn ctx() -> StatusCtx {
    StatusCtx {
        session: "0".into(),
        tab_number: Some(2),
        tab_name: "쉘".into(),
        pane_title: "vim".into(),
    }
}

#[test]
fn the_message_line_prefers_the_disconnect_over_a_stale_server_error() {
    // 두 뷰가 같은 글을 쓰라고 여기 있다. 규칙 하나: **끊김이 오류를 이긴다** — 연결이
    // 끝난 뒤 남은 마지막 서버 오류는 이미 지난 이야기다.
    let both = message_line(Some("서버가 닫았다"), Some("그런 탭 없음"));
    let text = both.expect("끊김이 있으면 줄이 있어야 한다");
    assert!(text.contains("연결 종료"), "{text}");
    assert!(text.contains("서버가 닫았다"), "{text}");
    assert!(!text.contains("그런 탭 없음"), "지난 오류가 섞였다: {text}");

    let only_err = message_line(None, Some("그런 탭 없음")).expect("오류만 있어도 줄이 있다");
    assert!(only_err.contains("서버 오류"), "{only_err}");
    assert!(only_err.contains("그런 탭 없음"), "{only_err}");

    // 둘 다 없으면 **줄이 없다** — 빈 줄을 그리면 그만큼 캔버스가 밀린다.
    assert_eq!(message_line(None, None), None);
}

#[test]
fn the_python_default_right_format_expands() {
    // 파이썬 기본값 그대로다. 이것이 깨지면 **기본 설정으로 뜬 화면**이 깨진다.
    let out = expand_at(" #{pane_title}#h %H:%M %Y-%m-%d ", &ctx(), NOW);
    assert!(out.contains("vim · "), "{out}");
    assert!(out.contains("13:45"), "{out}");
    assert!(out.contains("2026-07-29"), "{out}");
}

#[test]
fn every_token_has_a_value() {
    let out = expand_at("#S|#I|#W|#{pane_title}", &ctx(), NOW);
    assert_eq!(out, "0|2|쉘|vim · ");
}

#[test]
fn the_tab_number_is_the_one_on_screen() {
    // ★ 파이썬은 `index + 1` 을 적는다 — 내부 index 를 적으면 탭바에 보이는 번호와
    //   하나씩 어긋나고, 그건 눈으로 잡기 가장 어려운 부류다.
    let ctx = StatusCtx {
        tab_number: Some(1),
        ..ctx()
    };
    assert_eq!(expand_at("#I", &ctx, NOW), "1");
}

#[test]
fn no_tab_leaves_an_empty_slot_instead_of_a_word() {
    let ctx = StatusCtx {
        tab_number: None,
        ..ctx()
    };
    assert_eq!(expand_at("[#I]", &ctx, NOW), "[]");
}

#[test]
fn the_default_pane_title_is_not_worth_showing() {
    // `shell` 은 서버가 붙이는 기본 제목이다 — 적어 봐야 아무것도 안 알려 준다.
    for title in ["shell", ""] {
        let ctx = StatusCtx {
            pane_title: title.into(),
            ..ctx()
        };
        assert_eq!(expand_at("[#{pane_title}]", &ctx, NOW), "[]", "{title:?}");
    }
}

#[test]
fn the_short_host_stops_at_the_first_dot() {
    let full = hostname();
    if full.is_empty() {
        return; // 호스트 이름을 못 얻는 판 — 그때는 둘 다 빈 값이다
    }
    let short = expand_at("#h", &ctx(), NOW);
    assert!(!short.contains('.'), "{short}");
    assert!(expand_at("#H", &ctx(), NOW).starts_with(&short));
}

#[test]
fn a_percent_in_the_session_name_is_not_a_time_code() {
    // ★ 순서가 규칙이다. 토큰을 먼저 바꾸면 사용자가 지은 이름의 `%` 가 시각 코드로
    //   읽혀 엉뚱한 글자가 나온다.
    let ctx = StatusCtx {
        session: "100%".into(),
        ..ctx()
    };
    assert_eq!(expand_at("#S", &ctx, NOW), "100%");
}

#[test]
fn a_bad_time_code_leaves_the_line_readable() {
    // 형식을 잘못 적었다고 상태줄이 통째로 비면 무엇이 잘못됐는지 알 수 없다
    // (파이썬도 원문을 그대로 둔다).
    let out = expand_at("%Q %H:%M", &ctx(), NOW);
    assert_eq!(out, "%Q %H:%M");
}

#[test]
fn an_unknown_hash_token_is_left_alone() {
    // 모르는 토큰을 지우면 사용자는 자기가 친 글자가 어디로 갔는지 모른다.
    assert_eq!(expand_at("#x", &ctx(), NOW), "#x");
}

#[test]
fn an_empty_format_stays_empty() {
    assert_eq!(expand_at("", &ctx(), NOW), "");
}

#[test]
fn the_live_clock_moves_but_does_not_panic() {
    // `expand` 는 진짜 시계를 읽는다 — 형식이 유효하면 그 자리에 숫자가 들어간다.
    let out = expand(" %Y ", &ctx());
    assert!(out.trim().len() == 4 && out.trim().chars().all(|c| c.is_ascii_digit()), "{out}");
}

// ── 색 이름 ──────────────────────────────────────────────────────────────────

#[test]
fn a_missing_color_means_the_theme_decides() {
    // 빈 값은 "안 정했다"다. 검정으로 떨어뜨리면 아무도 안 정한 배경이 까맣게 칠해진다.
    for name in ["", "   "] {
        assert_eq!(color(name), None, "{name:?}");
    }
}

#[test]
fn both_spellings_of_a_bright_color_work() {
    // 밑줄을 빼먹었다고 색이 조용히 안 먹으면 무엇이 잘못됐는지 알 수 없다.
    assert_eq!(color("bright_blue"), color("brightblue"));
    assert!(color("brightblue").is_some());
}

#[test]
fn a_hex_color_passes_through() {
    assert_eq!(
        color("#102030"),
        Some(crate::style::Color::Rgb { r: 0x10, g: 0x20, b: 0x30 })
    );
}

#[test]
fn a_nonsense_color_is_refused_instead_of_guessed() {
    // 짐작해서 아무 색이나 칠하면 사용자는 자기가 적은 이름이 먹은 줄 안다.
    assert_eq!(color("brightpurple"), None);
    assert_eq!(color("무지개"), None);
}

// ── 창 제목(패리티 `set-titles`) ─────────────────────────────────────────────

#[test]
fn the_window_title_uses_the_same_tokens_as_the_status_bar() {
    // ★ 두 자리가 다른 문법을 쓰면 사용자가 한쪽에서 배운 것을 다른 쪽에서 못 쓴다.
    // 그래서 제목도 `status::expand` 를 지난다 — 여기서 그 사실을 못박는다.
    let ctx = StatusCtx {
        session: "0".to_owned(),
        tab_number: Some(2),
        tab_name: "빌드".to_owned(),
        pane_title: "cargo".to_owned(),
    };
    assert_eq!(expand("#S:#I:#W", &ctx), "0:2:빌드", "파이썬 기본 형식");
    // `#{pane_title}` 은 뒤에 구분자(` · `)를 붙이는 토큰이다(모듈 문서 표) — 제목에서도
    // **같은 뜻**이라야 한다. 여기서 다르게 굴면 사용자가 상태줄에서 쓰던 형식이 제목에서
    // 다른 모양으로 나온다.
    assert_eq!(expand("#{pane_title}", &ctx), "cargo · ");
}

// ── 우측 구간 분해(G9w — 파이썬 `_expand_parts` 동형) ─────────────────────────

#[test]
fn the_default_right_format_splits_into_clickable_runs() {
    // 기본 우측 형식에서 시각(`%H:%M` 이 **한** 구간)·날짜(`%Y-%m-%d` 한 구간)가
    // 갈라진다 — 구분자 `:`·`-` 가 흡수되지 않으면 시계 존이 세 토막 난다.
    let ctx = StatusCtx {
        session: "main".into(),
        tab_number: Some(1),
        tab_name: "sh".into(),
        pane_title: String::new(),
    };
    let parts = expand_parts_at(" %H:%M %Y-%m-%d ", &ctx, "2026-07-30T15:40:00");
    let kinds: Vec<(StatusRun, &str)> =
        parts.iter().map(|(k, t)| (*k, t.as_str())).collect();
    assert_eq!(
        kinds,
        vec![
            (StatusRun::Plain, " "),
            (StatusRun::Time, "15:40"),
            (StatusRun::Plain, " "),
            (StatusRun::Date, "2026-07-30"),
            (StatusRun::Plain, " "),
        ]
    );
}

#[test]
fn the_host_token_becomes_a_host_run_and_joining_matches_expand() {
    // `#h` 는 호스트 런(클릭=서버 탭)이고, 런을 다 이으면 통짜 `expand_at` 과 같은
    // 글자다 — 두 펼치기가 다른 글자를 내면 존이 그림과 어긋난다.
    let ctx = StatusCtx {
        session: "main".into(),
        tab_number: Some(2),
        tab_name: "sh".into(),
        pane_title: "빌드".into(),
    };
    let fmt = " #{pane_title}#h %H:%M %Y-%m-%d ";
    let now = "2026-07-30T15:40:00";
    let parts = expand_parts_at(fmt, &ctx, now);
    assert!(
        parts.iter().any(|(k, _)| *k == StatusRun::Host),
        "#h 가 호스트 런이 아니다: {parts:?}"
    );
    let joined: String = parts.iter().map(|(_, t)| t.as_str()).collect();
    assert_eq!(joined, expand_at(fmt, &ctx, now), "구간 합이 통짜 펼치기와 다르다");
}

#[test]
fn an_unknown_strftime_code_stays_literal_in_parts_too() {
    let ctx = StatusCtx::default();
    let parts = expand_parts_at("%Q", &ctx, "2026-07-30T15:40:00");
    assert_eq!(parts, vec![(StatusRun::Plain, "%Q".to_owned())]);
}

#[test]
fn each_run_kind_opens_the_python_equivalent() {
    // 파이썬 존 표: 시각→시계 토글 · 날짜→달력 토글 · 호스트→서버 탭. 시계/달력이
    // 뒤바뀌면 클릭이 조용히 엉뚱한 오버레이를 연다 — 여기 못박는다.
    use base::Badge;
    assert_eq!(run_badge(StatusRun::Plain), None);
    assert_eq!(run_badge(StatusRun::Host), Some(Badge::Host));
    assert_eq!(run_badge(StatusRun::Time), Some(Badge::Clock));
    assert_eq!(run_badge(StatusRun::Date), Some(Badge::Calendar));
    // 세션 이름은 배지가 **없다** — 누르면 팝업이 아니라 입력칸이 열린다(pytmux-3).
    assert_eq!(run_badge(StatusRun::Session), None);
}

// ── 세션 이름 런(pytmux-3 — 파이썬 `_expand_parts` 의 `session` 종류) ──────────

#[test]
fn the_session_token_becomes_its_own_run_so_the_name_can_be_clicked() {
    // 파이썬이 `#S` 를 `plain` 에서 고유 종류로 뗀 이유 그대로다: 인접 병합에 안
    // 먹혀야 **그 폭이 곧 누를 자리**가 된다. `plain` 이면 양옆 공백과 한 덩이가 돼
    // 이름이 어디부터 어디까지인지 알 수 없다.
    let ctx = StatusCtx {
        session: "playground".into(),
        ..ctx()
    };
    let parts = expand_parts_at(" #S ", &ctx, NOW);
    let kinds: Vec<(StatusRun, &str)> = parts.iter().map(|(k, t)| (*k, t.as_str())).collect();
    assert_eq!(
        kinds,
        vec![
            (StatusRun::Plain, " "),
            (StatusRun::Session, "playground"),
            (StatusRun::Plain, " "),
        ]
    );
}

#[test]
fn the_session_run_carries_the_same_letters_as_the_whole_string_expansion() {
    // 두 펼치기가 다른 글자를 내면 누를 자리가 그림과 어긋난다(호스트 런과 같은 규율).
    let ctx = StatusCtx {
        session: "100%".into(),
        ..ctx()
    };
    let fmt = " #S · #{pane_title}%H:%M ";
    let parts = expand_parts_at(fmt, &ctx, NOW);
    let joined: String = parts.iter().map(|(_, t)| t.as_str()).collect();
    assert_eq!(joined, expand_at(fmt, &ctx, NOW), "구간 합이 통짜 펼치기와 다르다");
    // 이름 안의 `%` 가 시각 코드로 읽히지 않는다 — 통짜 판이 지키는 것과 같은 규칙이다.
    assert!(
        parts.contains(&(StatusRun::Session, "100%".to_owned())),
        "{parts:?}"
    );
}

#[test]
fn an_empty_session_name_leaves_no_run_to_click() {
    // 이름이 없으면 **누를 자리 자체가 없다**(빈 런은 ②가 버린다). 파이썬
    // `begin_session_edit` 이 `not self.session` 에서 False 를 돌려주는 것과 같은 자리다.
    let ctx = StatusCtx {
        session: String::new(),
        ..ctx()
    };
    let parts = expand_parts_at("#S", &ctx, NOW);
    assert!(parts.is_empty(), "빈 이름이 런을 남겼다: {parts:?}");
}
