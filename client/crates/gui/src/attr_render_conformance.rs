//! 글자 속성이 **그리는 자리까지 닿나** — pytmux-33 축 ⑷ 의 자(헤드리스 절반).
//!
//! # 무엇이 비어 있었나
//!
//! 정본 TUI 는 속성을 단말에 넘기므로 늘 맞는다. GUI 는 자기가 그려야 해서, **값은
//! 살아 있는데 칠하는 쪽이 없으면** 그 속성이 화면에서 통째로 사라진다 — 밑줄(SGR 4)이
//! 그렇게 몇 달 없었고([[pytmux-123]]) 굵게·기울임·취소선도 같은 부류였다
//! ([[pytmux-133]]). ⛔ **그때 오라클은 전부 초록이었다**: `style_conformance.rs` 는
//! 「파서가 이 값을 아나」까지만 묻고, 스타일 왕복 시험은 값이 살아 있는지만 본다.
//!
//! 그래서 이 자가 묻는 것은 한 줄이다 — **「그 속성 하나를 켰을 때 그리기 판정이
//! 실제로 달라지나」**.
//!
//! # 왜 이름을 손으로 안 적나
//!
//! 이 부류의 실패는 *새 속성이 왔을 때 조용히 덜 재는 것*이다. 목록을 여기 적으면
//! 여덟째 속성이 오는 날 이 자는 **초록인 채로** 그것을 안 센다. 그래서 전수는
//! [`cell_style_fields`] 가 `proto::style::CellStyle` 의 **원문에서** 읽고, 모르는
//! 이름을 만나면 [`only`] 가 멈춘다(= 새 속성은 여기 등록을 강제당한다).
//!
//! # 이 자가 재는 것과 못 재는 것
//!
//! 재는 것은 **그리기 판정**이다 — 색(`colors`) · 글꼴 변형(`font_properties` +
//! `fallback_safe`) · 글자에 긋는 선(`SessionView::text_rules`). 셋이 GUI 가 셀
//! 스타일을 그릴 것으로 바꾸는 자리 전부다.
//!
//! ⛔ **못 재는 것은 「그래서 픽셀이 그렇게 나왔나」다.** `Scene` 은 글리프 id 만 들고
//! `pump_headless` 는 렌더를 안 돈다. 실제로 그 층에서만 잡힌 것이 있다 — 기울임을
//! 걸면 한글이 두부(▯)가 됐고(보조 글꼴에 이탤릭 얼굴이 없다) 단위 오라클 열 개가
//! 전부 초록이었다. 그 절반은 **GUI 창을 띄워 캡처를 맞대는 자리**의 몫이고,
//! pytmux-33 본문이 *"스크린샷 대조가 자의 일부여야 한다"* 고 못박은 것이 그것이다.
//! 그 라이브 절반은 아직 안 섰다.

use super::*;

/// `CellStyle` 이 실제로 가진 칸 전수 — **원문에서** 읽는다.
///
/// ⚠ 구조체 본문에 중괄호가 없다는 것에 기대고 있다(필드가 전부 한 줄짜리 타입이다).
/// 중첩이 생기면 첫 `}` 에서 잘려 **덜 세는데 조용해지므로**,
/// [`the_field_list_is_not_silently_short`] 가 그 수를 파서 쪽과 맞대어 잰다.
fn cell_style_fields() -> Vec<String> {
    const HEAD: &str = "pub struct CellStyle {";
    let src = include_str!("../../proto/src/style.rs");
    let at = src.find(HEAD).expect("원문에서 CellStyle 을 못 찾았다");
    let body = &src[at + HEAD.len()..];
    let end = body.find('}').expect("CellStyle 본문이 안 닫힌다");
    body[..end]
        .lines()
        .filter_map(|line| line.trim().strip_prefix("pub "))
        .filter_map(|rest| rest.split(':').next())
        .map(|name| name.trim().to_string())
        .filter(|name| !name.is_empty())
        .collect()
}

/// `CellStyle::from_map` 이 와이어에서 읽는 키 수. 필드 수와 맞물려야 한다.
fn wire_keys_read() -> usize {
    const HEAD: &str = "pub fn from_map(";
    let src = include_str!("../../proto/src/style.rs");
    let at = src.find(HEAD).expect("원문에서 from_map 을 못 찾았다");
    let body = &src[at..];
    let end = body.find("\n    }").expect("from_map 본문이 안 닫힌다");
    body[..end]
        .match_indices('(')
        .filter(|(i, _)| {
            let before = &body[..*i];
            before.ends_with("flag") || before.ends_with("color")
        })
        .count()
}

/// **그 속성 하나만** 켠 스타일.
///
/// ⛔ 모르는 이름에서 멈추는 것이 이 자의 래칫이다 — 새 속성을 `CellStyle` 에 더하면
/// 「그것을 무엇으로 그릴 것인가」를 정하기 전까지 이 시험이 붉다.
fn only(field: &str) -> CellStyle {
    let mut style = CellStyle::default();
    match field {
        "fg" => style.fg = Some(CellColor::Named(NamedColor::Red)),
        "bg" => style.bg = Some(CellColor::Named(NamedColor::Blue)),
        "bold" => style.bold = true,
        "italic" => style.italic = true,
        "underline" => style.underline = true,
        "reverse" => style.reverse = true,
        "strike" => style.strike = true,
        other => panic!(
            "CellStyle 에 새 칸 `{other}` 이 생겼는데 이 자가 그것을 모른다.\n\
             → 그리는 자리를 정한 뒤 `only()` 에 그 칸을 켜는 갈래를 더할 것. \
             정하지 않으면 그 속성은 파서까지만 살아 있고 화면에서는 사라진다(pytmux-123·133)."
        ),
    }
    style
}

/// 이 스타일이 **그릴 것으로 바뀐 결과** 전부를 한 줄로 뜬다.
///
/// GUI 가 셀 스타일을 소비하는 자리가 셋이고, 셋 다 순수 함수라 여기서 통째로 부를 수
/// 있다. 하나라도 빠지면 그 속성은 「값은 있는데 안 그려지는」 상태다.
fn drawn(style: &CellStyle) -> String {
    let (fg, bg) = colors(style);
    let props = font_properties(style);
    let ascii = fallback_safe(props, false);
    let boxed = fallback_safe(props, true);

    // 선은 캔버스 위에서만 뜬다 — 한 줄짜리 판에 그 스타일로 두 칸을 적고 훑는다.
    let mut canvas = proto::canvas::Canvas::new(4, 1);
    canvas.put_text(0, 0, "ab", *style);
    let rules = SessionView::text_rules(&canvas);

    format!(
        "fg={fg:?} bg={bg:?} \
         ascii={:?}/{:?} boxed={:?}/{:?} \
         rules={rules:?}",
        ascii.weight, ascii.style, boxed.weight, boxed.style
    )
}

/// 축 ⑷ 의 중심 물음 — 속성 하나를 켰는데 **그리기 판정이 그대로면** 그 속성은 없다.
#[test]
fn every_cell_attribute_changes_what_gets_drawn() {
    let plain = drawn(&CellStyle::default());
    let mut dead = Vec::new();
    for field in cell_style_fields() {
        if drawn(&only(&field)) == plain {
            dead.push(field);
        }
    }
    assert!(
        dead.is_empty(),
        "켜도 그리는 것이 안 바뀌는 속성 {}종: {dead:?}\n\
         → 파서는 읽는데 칠하는 쪽이 없다 = 화면에서 통째로 사라진다(pytmux-123 의 밑줄이 그랬다). \
         자리는 셋이다 — 색은 `colors()` · 굵게·기울임은 `font_properties()` · \
         선은 `SessionView::text_rules()`.",
        dead.len()
    );
}

/// 두 속성이 **같은 그림**으로 떨어지면 그중 하나는 거짓이다.
///
/// 배선을 복붙하다 `italic` 을 `bold` 자리에 잇는 부류를 잡는다 — 위 시험만으로는
/// 둘 다 「달라지긴 했다」로 초록이다.
#[test]
fn no_two_attributes_land_on_the_same_drawing() {
    let fields = cell_style_fields();
    let mut same = Vec::new();
    for (i, a) in fields.iter().enumerate() {
        for b in &fields[i + 1..] {
            if drawn(&only(a)) == drawn(&only(b)) {
                same.push(format!("{a}={b}"));
            }
        }
    }
    assert!(
        same.is_empty(),
        "서로 다른 속성이 같은 그림으로 떨어진다: {same:?}\n\
         → 배선이 한쪽으로 잘못 이어졌다. 둘 중 하나는 화면에서 남의 모양으로 보인다."
    );
}

/// 전수를 뽑는 쪽이 **조용히 덜 세는 것**을 막는다.
///
/// `cell_style_fields` 가 0건이나 반쪽을 돌려주면 위 두 시험은 **아무것도 안 재고
/// 초록**이다. 파서가 와이어에서 읽는 키 수와 맞대어 그 침묵을 깬다 — 칸을 더하고
/// 파싱을 안 붙였거나 그 반대여도 여기서 붉어진다.
#[test]
fn the_field_list_is_not_silently_short() {
    let fields = cell_style_fields();
    let keys = wire_keys_read();
    assert!(!fields.is_empty(), "CellStyle 에서 칸을 하나도 못 뽑았다 — 추출이 깨졌다");
    assert_eq!(
        fields.len(),
        keys,
        "CellStyle 의 칸 {}개 {fields:?} 와 `from_map` 이 읽는 와이어 키 {keys}개가 어긋난다.\n\
         → 칸을 더하고 파싱을 안 붙였거나(값이 영영 안 들어온다), \
         파싱만 하고 칸을 안 늘렸거나(읽은 값을 버린다), \
         아니면 이 자의 추출이 깨져 **덜 세면서 초록**인 것이다.",
        fields.len()
    );
}
