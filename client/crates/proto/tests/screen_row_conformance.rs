//! 적합성 — 정본이 **모양마다 줄에 싣는 칸**을 GUI 의 그 갈래가 다 읽는가.
//!
//! # 왜 있나 (pytmux-33 ⓖ3 · 축 ⑶ 의 마지막 조각 "화면 안의 어휘")
//!
//! 옆의 둘은 여기까지 온다: `plugin_screen_conformance.rs` 가 *"이 **모양**을 GUI 가
//! 아나"*, `tests/test_plugin_do_wiring.py`(파이썬 쪽)가 *"이 화면이 내는 `do` 를
//! 정본이 다 받나"*. 그 다음 물음 — *"그 화면 **안**의 낱말이 갈리지 않았나"* — 은
//! 이슈가 자리를 적어 두고 비워 둔 칸이었다.
//!
//! **모양이 맞아도 안의 칸이 갈릴 수 있다.** 정본은 기간 판을 계층 트리로 내는데
//! (줄마다 `depth`·`expand`) GUI 의 `"table"` 갈래가 그 두 칸을 안 읽어 판이
//! **평면**이었다(pytmux-419 ③ · CL 74520). 같은 두 칸을 `"list"` 갈래는 옳게 읽고
//! 있었으니 *"GUI 가 이 칸을 아나"* 로 물었으면 초록이다 — **모양마다 따로 물어야
//! 보인다.** 이 파일이 그렇게 묻는다.
//!
//! ⛔ **눈으로는 안 잡힌다.** 빠진 칸은 그냥 «안 그려질» 뿐이라 화면은 멀쩡해 보이고
//! (평면 목록도 목록이다), 정본 쪽은 늘 맞으므로 나란히 굽지 않는 한 비교 대상도
//! 없다. pytmux-33 의 2026-09-01 코멘트가 *"1:1 대조를 사람 눈으로 하면 이 부류는 안
//! 잡힌다"* 고 적은 그 자리다.
//!
//! # 왜 GUI 의 **원문**을 읽나
//!
//! 옆 파일(`plugin_screen_conformance.rs`)의 사정 그대로다: GUI 는 그린 것을 되읽을
//! 수 없다(`Scene` 은 글리프 id 만 들고 `pump_headless` 는 렌더를 안 돈다). 그래서
//! *"이 칸을 읽는 배선이 그 갈래에 있나"* 까지 잰다 — pytmux-133 이 같은 벽에서 쓴
//! 방편이고, 이 자의 질문에는 정확히 맞다.
//!
//! ⚠ **재는 것은 배선이지 그림이 아니다.** 읽어 놓고 안 그리는 것은 못 잡는다(그
//! 축은 ⓖ3 의 ⑷ = 렌더 축이 든다).
//!
//! ⛔ **반대 방향(정본이 안 싣는 칸을 GUI 가 읽는 것)은 안 잰다.** 그것은 「못 그린다」가
//! 아니라 **「안 와도 되게 해 뒀다」**여서 결함이 아니다 — 실제로 `"table"` 갈래는
//! 정본이 그 모양에 안 싣는 `tag` 를 읽는데, 그 줄이 하는 일은 «오면 칠하고 아니면
//! 기본색»이다. 그것을 갈림으로 세면 이 게이트는 첫날부터 붉고, 상시 적색은 진짜
//! 갈림이 왔을 때 아무도 안 보게 만든다(`gen_plugin_screens.py` 가 `sites` 에서
//! 줄 번호를 뺀 것과 같은 규율).

use std::collections::{BTreeMap, BTreeSet};

/// 정본에서 뽑은 «모양 → 줄의 칸»(`scripts/gen_screen_rows.py`).
#[derive(serde::Deserialize)]
struct Fixture {
    rows: BTreeMap<String, Vec<String>>,
    unresolved: Vec<String>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/screen_rows.json"))
        .expect("screen_rows.json 을 못 읽는다")
}

/// GUI 원문. 배선을 여기서 읽는다(모듈 주석의 "왜 원문을 읽나").
const GUI_SRC: &str = include_str!("../../gui/src/session_view.rs");

/// 줄의 칸 → **그 칸을 읽는다고 인정할 글자**.
///
/// ⛔ 글(`label`·`cols`)은 `say_*` 로만 센다 — 원문(`item.label`)을 그대로 그리면
///    로케일 재조립을 건너뛰는 것이라(그 갈림은 `PluginRow::say_cols` 주석이 쥔다)
///    «읽었다»고 인정할 일이 아니다.
const READS: &[(&str, &str)] = &[
    ("label", "say_label()"),
    ("cols", "say_cols()"),
    ("tag", ".tag"),
    ("depth", ".depth"),
    ("expand", ".expand"),
    ("bar", ".bar"),
    ("until", "countdown("),
];

/// 그리는 갈래 **밖**에서 드는 칸 — 여기 있는 것은 갈래에서 안 찾는다.
///
/// · `key` — 그림이 아니라 **되돌려 보낼 뜻**이다. 누른 줄의 `key` 를
///   `PluginAction` 의 `input` 으로 싣는 자리는 갈래가 아니라 키 처리 쪽이다
///   (`spec.rows.get(row).map(|r| r.key.clone())`).
/// · `i18n` — 줄의 글을 우리 로케일로 다시 짓는 **재료**다. 푸는 것은 proto 안의
///   `say_label`/`say_cols` 이고, 갈래는 그 함수를 부르는 것으로 이미 지난다.
const ELSEWHERE: &[&str] = &["key", "i18n"];

/// `match <머리> {` 블록의 몸통 — 여는 중괄호부터 짝이 맞는 닫는 중괄호까지.
///
/// 정규식으로 갈래를 긁으면 **안쪽 match** 의 갈래까지 딸려 온다(옆 파일이 같은
/// 자리에서 겪은 것). 그래서 깊이를 세어 자른다.
fn match_body<'a>(src: &'a str, head: &str) -> &'a str {
    let at = src.find(head).unwrap_or_else(|| panic!("GUI 원문에서 `{head}` 를 못 찾는다"));
    let open = at + head.len();
    let mut depth = 1usize;
    for (i, ch) in src[open..].char_indices() {
        match ch {
            '{' => depth += 1,
            '}' => {
                depth -= 1;
                if depth == 0 {
                    return &src[open..open + i];
                }
            }
            _ => {}
        }
    }
    panic!("`{head}` 의 몸통이 안 닫힌다");
}

/// 모양별 갈래 — `{모양: 몸통}`.
fn arms() -> BTreeMap<String, String> {
    let body = match_body(GUI_SRC, "match spec.kind.as_str() {");
    let bytes = body.as_bytes();
    let mut heads: Vec<(usize, String)> = Vec::new();
    let mut depth = 0usize;
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
    heads
        .iter()
        .enumerate()
        .map(|(n, (pos, name))| {
            let till = heads.get(n + 1).map(|(p, _)| *p).unwrap_or(body.len());
            (name.clone(), body[*pos..till].to_owned())
        })
        .collect()
}

/// ① 픽스처가 *"전수로 셌다"* 고 말할 자격이 있나.
///
/// 생성기는 `rows=` 가 어디서 왔는지를 따라가는데, 못 따라간 자리를 **조용히 빼면**
/// 이 파일의 다른 단언이 전부 거짓 초록이 된다(저장소 규율: 파싱 실패를 초록으로
/// 위장하지 않는다).
#[test]
fn the_fixture_followed_every_rows_slot() {
    let fx = fixture();
    assert!(
        fx.unresolved.is_empty(),
        "정본에서 줄의 출처를 못 푼 자리가 있다:\n  {}\n\
         그 자리가 남아 있는 한 이 파일의 '전수' 단언은 참이 아니다.",
        fx.unresolved.join("\n  ")
    );
}

/// ② **줄을 싣는 모양을 실제로 찾았나** — 못 찾으면 무엇을 견줘도 초록이다.
///
/// 생성기의 자료 흐름 추적이 깨지면(줄을 짓는 새 모양이 오는 것이 그 부류다) 픽스처는
/// 「어느 모양도 줄을 안 싣는다」가 되고, ③ 은 아무것도 안 재면서 통과한다.
#[test]
fn the_fixture_actually_found_the_rows() {
    let fx = fixture();
    let filled: Vec<&String> = fx.rows.iter().filter(|(_, v)| !v.is_empty()).map(|(k, _)| k).collect();
    for want in ["list", "table", "panel", "form"] {
        assert!(
            filled.iter().any(|k| k.as_str() == want),
            "`{want}` 모양이 줄에 아무 칸도 안 싣는 것으로 세졌다 — \
             생성기가 그 화면의 `rows=` 를 못 따라갔을 것이다\
             (`python3.13 client/scripts/gen_screen_rows.py` 를 다시 돌려 볼 것): {filled:?}"
        );
    }
}

/// ③ **모양마다** 그 칸을 그 갈래가 읽나 — 이 파일의 중심.
#[test]
fn every_kind_reads_the_row_fields_the_canon_puts_in_it() {
    let fx = fixture();
    let arms = arms();
    let known: BTreeSet<&str> = READS.iter().map(|(f, _)| *f).chain(ELSEWHERE.iter().copied()).collect();

    for (kind, fields) in &fx.rows {
        if fields.is_empty() {
            continue;                    // 줄이 없는 판(`prompt`·`confirm`·글 판)
        }
        let Some(body) = arms.get(kind) else {
            // 그리는 자리가 `render_plugin_view` 밖인 모양(`prompt`·`confirm`)은 위에서
            // 이미 걸러진다 — 여기 오면 정말로 갈래가 없는 것이다.
            panic!(
                "정본이 `{kind}` 모양에 줄을 싣는데 GUI 에 그 갈래가 없다: {fields:?}\n\
                 `plugin_screen_conformance.rs` 가 먼저 울어야 하는 자리이기도 하다."
            );
        };
        for field in fields {
            assert!(
                known.contains(field.as_str()),
                "정본이 줄에 `{field}` 를 싣기 시작했는데 이 자는 그것을 **어떻게 재는지
                 모른다**(`{kind}` 모양).\n\
                 `READS` 에 «그 칸을 읽는다고 인정할 글자»를 적거나, 그리는 자리가 갈래 \
                 밖이면 `ELSEWHERE` 에 그 이유와 함께 적을 것.\n\
                 ⛔ 조용히 넘기면 그 칸은 이 게이트 밖이 된다 — `depth`·`expand` 가 \
                 정확히 그렇게 몇 달을 갔다(pytmux-419 ③)."
            );
            if ELSEWHERE.contains(&field.as_str()) {
                continue;
            }
            let needle = READS.iter().find(|(f, _)| f == field).map(|(_, n)| *n).unwrap();
            assert!(
                body.contains(needle),
                "정본은 `{kind}` 모양의 줄에 `{field}` 를 싣는데 GUI 의 그 갈래가 \
                 안 읽는다(`{needle}` 이 없다) — 그 칸은 화면에서 통째로 사라진다.\n\
                 ⚠ 다른 갈래가 같은 칸을 읽고 있어도 이 갈래에는 아무 소용이 없다 \
                 (`\"list\"` 는 읽는데 `\"table\"` 은 안 읽던 것이 pytmux-419 ③ 이다)."
            );
        }
    }
}

/// ④ ③ 이 **갈래를 실제로 갈랐나**.
///
/// 자르기가 깨지면(머리를 못 찾거나 몸통이 한 덩이로 붙으면) ③ 은 남의 갈래에서 그
/// 글자를 찾아 통과한다 — 그러면 이 게이트는 *"어느 갈래든 어딘가 읽으면 된다"* 가
/// 되고, 그것이 정확히 이 파일이 안 물으려는 질문이다.
#[test]
fn the_arms_are_actually_split_apart() {
    let arms = arms();
    for want in ["list", "text", "table", "panel", "form"] {
        assert!(arms.contains_key(want), "모양 갈래 `{want}` 를 원문에서 못 갈랐다: {:?}", arms.keys());
    }
    // `"text"` 갈래는 줄을 안 그린다 — 한 덩이로 붙었으면 여기에 `say_label()` 이 보인다.
    let text = &arms["text"];
    assert!(
        !text.contains("say_label()"),
        "`\"text\"` 갈래에 목록 갈래의 배선이 섞였다 — 자르기가 깨졌다({}자)",
        text.len()
    );
}
