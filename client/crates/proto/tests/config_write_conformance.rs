//! 설정 파일 **쓰기** 형식이 파이썬 클라와 같은지 — 픽스처 대조(패리티 G5b).
//!
//! # 왜 이 표가 다른 표보다 무섭나
//!
//! 다른 패리티 항목이 틀리면 "안 되네"로 끝난다. 이건 **사용자의 파일을 고친다**. 규칙이
//! 어긋나면 같은 옵션이 두 줄로 늘거나(나중 줄이 이기니 원래 설정이 조용히 죽는다),
//! 주석·`bind` 줄이 사라진다. 되돌릴 방법이 없다.
//!
//! 픽스처는 `scripts/gen_config_write_fixture.py` 가 파이썬 `set_config_option` 을 **직접
//! 불러** 뽑는다. 규칙을 손으로 옮겨 적으면 그 순간부터 갈린다.
//!
//! 이 파일이 proto 에 있는 이유는 픽스처 읽기(serde_json)가 여기 있어서다 — core 는 의존
//! 0개가 계약이라 JSON 을 못 읽는다. proto → core 방향은 이미 있다.

use base::config::edit_option;
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    cases: Vec<Case>,
}

#[derive(Deserialize)]
struct Case {
    name: String,
    why: String,
    before: Option<Vec<String>>,
    before_ends_with_newline: bool,
    before_exists: bool,
    option: String,
    value: String,
    /// 터미네이터를 뺀 줄 목록(픽스처가 뽑히는 OS 마다 CRLF/LF 가 갈려서다).
    after: Vec<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/config_write.json")).expect("픽스처를 읽을 수 없다")
}

#[test]
fn we_edit_the_config_file_the_way_python_does() {
    for case in fixture().cases {
        let mut before = case
            .before
            .clone()
            .unwrap_or_default()
            .join("\n");
        if case.before_ends_with_newline {
            before.push('\n');
        }
        // 파일이 아예 없는 칸은 러스트 쪽에서도 "빈 문자열"로 들어온다
        // (`write_option` 이 읽기 실패를 빈 내용으로 본다).
        assert!(case.before_exists || before.is_empty());

        let after = edit_option(&before, &case.option, &case.value);
        let got: Vec<&str> = after.lines().collect();
        assert_eq!(
            got, case.after,
            "[{}] {}\n  넣은 것: set {} {}\n  원본: {before:?}",
            case.name, case.why, case.option, case.value
        );
        // 파이썬은 항상 개행으로 끝낸다 — 안 그러면 다음 번 추가가 마지막 줄에 붙는다.
        assert!(after.ends_with('\n'), "[{}] 끝 개행이 없다", case.name);
    }
}

#[test]
fn the_fixture_is_not_empty() {
    // ★ 빈 픽스처는 "전부 통과"처럼 보인다. 라이선스 게이트에서 이미 한 번 밟은 부류다.
    assert!(fixture().cases.len() >= 10);
}

#[test]
fn existing_line_endings_survive() {
    // 파이썬은 Windows 에서 파일 전체를 CRLF 로 번역해 쓴다. 우리는 원본을 지킨다 —
    // 값 하나를 바꿨는데 diff 가 파일 전체로 번지면 사용자는 자기가 뭘 바꿨는지 못 본다.
    let crlf = "# 주석\r\nset prefix C-b\r\n";
    assert_eq!(
        edit_option(crlf, "prefix", "C-a"),
        "# 주석\r\nset prefix C-a\r\n"
    );
    let lf = "# 주석\nset prefix C-b\n";
    assert_eq!(edit_option(lf, "prefix", "C-a"), "# 주석\nset prefix C-a\n");
}

#[test]
fn a_new_line_follows_the_file_it_lands_in() {
    // CRLF 파일에 LF 한 줄만 섞어 넣으면 그 파일을 메모장으로 여는 사람이 깨진 줄을 본다.
    assert_eq!(
        edit_option("set mouse on\r\n", "prefix", "C-a"),
        "set mouse on\r\nset prefix C-a\r\n"
    );
}

#[test]
fn writing_twice_does_not_pile_up() {
    // 설정 화면에서 같은 값을 두 번 바꾸는 것은 흔하다. 두 번째가 줄을 늘리면 파일이
    // 쓸 때마다 자란다.
    let once = edit_option("", "prefix", "C-a");
    let twice = edit_option(&once, "prefix", "C-t");
    assert_eq!(twice.lines().count(), 1, "{twice:?}");
    assert_eq!(twice.trim(), "set prefix C-t");
}

// ── 어느 입구가 파일을 쓰나(pytmux-649) — 모집단 = 정본에서 뽑은 표 ─────────────────────────

#[derive(Deserialize)]
struct Entrances {
    rows: Vec<EntranceRow>,
}

#[derive(Deserialize)]
struct EntranceRow {
    key: String,
    backend: String,
    command_line_writes: bool,
    settings_panel_writes: Vec<String>,
}

fn entrances() -> Entrances {
    serde_json::from_str(include_str!("fixtures/config_entrances.json"))
        .expect("config_entrances.json 을 못 읽는다 — scripts/gen_config_entrances.py 로 다시 뽑을 것")
}

/// GUI 설정 판의 그 줄이 **설정 파일에 쓰는 갈래**인가 — 줄의 종류가 정한다. 설정 파일 줄
/// (`ConfigToggle`·`ConfigEnum`·`Number`·`Text` — 대답은 `set_config`/`set_prefix` 로 간다)은 쓰고,
/// 서버 토글·열거(`Toggle`·`Enum` → 서버 명령)·언어(`.lang`)·링크(판 열기)는 안 쓴다.
fn gui_panel_writes(kind: &base::config::SettingKind) -> bool {
    use base::config::SettingKind as K;
    matches!(kind, K::ConfigToggle | K::ConfigEnum(_) | K::Number { .. } | K::Text(_))
}

#[test]
fn the_canon_never_writes_the_file_from_a_typed_set() {
    // 정본 사실을 못 박는다 — 이 값이 뒤집히면 GUI `apply_set_line` 의 규칙(파일을 안 쓴다)도 다시 볼 것.
    let fx = entrances();
    assert!(fx.rows.len() >= 30, "모집단이 너무 작다: {}", fx.rows.len());
    let writes: Vec<&str> =
        fx.rows.iter().filter(|r| r.command_line_writes).map(|r| r.key.as_str()).collect();
    assert!(writes.is_empty(), "정본 명령 줄 set 이 파일을 쓴다: {writes:?}");
}

#[test]
fn the_settings_panel_writes_exactly_the_rows_the_canon_writes() {
    let fx = entrances();
    let mut wrong = Vec::new();
    let mut seen = 0;
    for row in &fx.rows {
        let canon = !row.settings_panel_writes.is_empty();
        let Some(gui) = base::config::SETTINGS.iter().find(|s| s.key == row.key) else {
            // 판에 그 줄이 없는 것은 설정 판 축(`setting_effect_conformance` · 갈림 대장)의 몫이다 —
            // 여기서는 「정본이 파일에 쓰는 줄인데 GUI 판에 없다」만 운다(저장할 길이 사라진다).
            if canon {
                wrong.push(format!("{}: 정본은 설정 판에서 파일에 쓰는데 GUI 판에 그 줄이 없다", row.key));
            }
            continue;
        };
        seen += 1;
        if canon && row.settings_panel_writes != [row.key.clone()] {
            wrong.push(format!("{}: 정본이 다른 키를 쓴다 {:?}", row.key, row.settings_panel_writes));
        }
        if gui_panel_writes(&gui.kind) != canon {
            wrong.push(format!(
                "{} [backend {}]: 정본 쓰기={canon} · GUI 쓰기={}",
                row.key,
                row.backend,
                gui_panel_writes(&gui.kind)
            ));
        }
    }
    assert!(seen >= 30, "맞댄 줄이 너무 적다: {seen}");
    assert!(wrong.is_empty(), "설정 판의 저장 여부가 정본과 다르다:\n  {}", wrong.join("\n  "));
}
