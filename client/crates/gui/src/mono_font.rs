//! 고정폭 글꼴 고르기 — **상자마다 있는 글꼴이 다르다**.
//!
//! # 왜 목록인가
//!
//! 종전에는 `Menlo` 하나를 `.expect` 로 받았다. Menlo 는 macOS 기본 탑재라 그 상자에서는
//! 늘 떴지만, **다른 OS 에서는 창이 뜨기도 전에 패닉한다** — 실행 파일이 링크까지 되고도
//! 첫 프레임에 죽으므로 증상은 "GUI 가 아예 안 뜬다"이고, 원인이 글꼴이라는 단서가 없다.
//! (2026-07-28 실측: Windows 에서 GUI 이진은 **링크된다** — 막고 있던 것은 Metal 이 아니라
//! 이 한 줄이었다.)
//!
//! # 왜 여기가 뷰 밖인가
//!
//! 고르는 규칙은 순수 함수다 — "후보를 순서대로 물어 처음 성공한 것"이 전부다. 뷰 안에
//! 두면 그 규칙을 확인하려고 창을 띄워야 하고, 그러면 아무도 확인하지 않는다. 로더를
//! 인자로 받으므로 실제 글꼴 없이 규칙만 시험할 수 있다(사용자 결정 2026-07-28:
//! "로직은 밀고 뷰는 얇게").

/// 후보 목록. **처음 뜨는 것**을 쓴다.
///
/// 순서는 "그 OS 에서 터미널이 기본으로 쓰는 것"이 앞이다 — 사용자가 다른 터미널에서
/// 보던 글자 모양과 최대한 같아야 한다.
///
/// - `Menlo` — macOS 기본 고정폭.
/// - `Consolas` — Windows 기본 고정폭(Vista 이후 항상 있다).
/// - `DejaVu Sans Mono` · `Liberation Mono` — 리눅스 배포판 대부분.
/// - `Courier New` — 마지막 보루. 보기 좋진 않지만 거의 모든 곳에 있다.
///
/// OS 별로 `cfg` 를 나누지 않는 이유: 이 목록은 **가용성 순서**이지 플랫폼 선언이
/// 아니다. macOS 에 Consolas 를 깐 사람도 있고, 그 경우에도 Menlo 가 먼저 잡히면 된다.
pub const CANDIDATES: &[&str] = &[
    "Menlo",
    "Consolas",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Courier New",
];

/// 보조 글꼴 후보. **고정폭 하나로는 한글이 안 그려진다.**
///
/// # 왜 필요한가 (2026-07-28 실측)
///
/// 스크린샷 하네스로 창을 처음 찍자 이 앱의 한글이 **전부 두부**(`▯`)였다. 이 클라의 UI
/// 문구는 거의 다 한글이라 사실상 아무것도 못 읽는 화면이었는데, 지금까지 아무도 창을
/// 안 봐서 안 드러났다.
///
/// 근인은 글꼴 선택이 아니라 **글꼴 DB 가 비어 있는 것**이다. `warpui` 는 cosmic-text 의
/// 폰트 시스템을 `Default::default()` 인 fontdb 로 만든다(`FontSystem::new_with_locale_and_db`)
/// — OS 의 글꼴을 자동으로 훑지 않으므로, DB 에는 **우리가 명시적으로 넣은 family 만**
/// 들어 있다. 지금까지 넣은 것은 [`CANDIDATES`] 로 고른 고정폭 하나뿐이었다.
///
/// # 이름을 우리가 고르는 게 아니다 — 이게 함정이다
///
/// cosmic-text 는 글리프가 없는 문자를 만나면 **가진 글꼴 중 아무거나 뒤지지 않는다**.
/// 스크립트별 표에 적힌 **이름으로** 묻는다(`script_fallback(Script::Hangul, …)`):
/// Windows `Malgun Gothic` · macOS `Apple SD Gothic Neo` · 리눅스 `Noto Sans CJK KR`.
/// 그러니 한글이 되는 글꼴을 아무리 좋은 것으로 넣어도 **그 이름이 아니면 안 쓰인다**.
/// 아래 목록이 취향이 아니라 계약인 이유다 — 상류 표가 바뀌면 여기도 따라가야 한다.
///
/// 한글 다음 줄들은 각 OS 의 `common_fallback()` 이다. 스크립트 표에 없는 문자(기호·
/// 이모지)가 그리로 간다 — 패널에 뜨는 것은 우리가 고른 글자가 아니라 **남의 프로그램이
/// 뱉는 아무 문자**라, 여기를 비워 두면 그때 또 두부가 된다.
///
/// 없는 이름은 **조용히 건너뛴다**(다른 OS 의 것이 섞여 있는 게 정상이다).
pub const FALLBACK_CANDIDATES: &[&str] = &[
    // 한글 — `script_fallback(Script::Hangul, _)` 이 OS 별로 묻는 이름.
    "Malgun Gothic",
    "Apple SD Gothic Neo",
    "Noto Sans CJK KR",
    // 나머지 문자 — 각 OS 의 `common_fallback()`.
    "Segoe UI",
    "Segoe UI Symbol",
    "Segoe UI Emoji",
    "Arial Unicode MS",
    "Apple Color Emoji",
    "Noto Sans",
    "Noto Sans Symbols2",
    "Noto Color Emoji",
];

/// 후보를 순서대로 물어 처음 성공한 것을 돌려준다.
///
/// 전부 실패하면 `Err` 에 **시도한 이름을 전부** 담는다 — "글꼴을 못 찾았다"만으로는
/// 무엇을 깔아야 하는지 알 수 없다.
pub fn pick<T, E>(
    candidates: &[&'static str],
    mut load: impl FnMut(&str) -> Result<T, E>,
) -> Result<(&'static str, T), String> {
    for name in candidates {
        if let Ok(font) = load(name) {
            // 고른 이름도 함께 돌려준다 — 어느 글꼴로 떴는지는 진단의 첫 단서다.
            return Ok((*name, font));
        }
    }
    Err(format!(
        "고정폭 글꼴을 하나도 못 찾았다 — 시도한 것: {}",
        candidates.join(", ")
    ))
}

/// 후보를 **전부** 물어 실제로 들어간 것의 이름을 돌려준다.
///
/// [`pick`] 과 반대로 첫 성공에서 멈추지 않는다 — 폴백은 "하나 고르는" 것이 아니라 DB 에
/// **깔아 두는** 것이고, 어느 것이 쓰일지는 그때 만난 문자가 정한다.
///
/// 하나도 못 넣어도 실패로 만들지 않는다: 한글이 두부로 나오는 것은 보기 흉하지만
/// 고정폭이 없는 것과 달리 **화면은 뜬다**. 대신 부른 쪽이 경고를 남길 수 있게 빈 목록을
/// 돌려준다.
pub fn load_fallbacks<T, E>(
    candidates: &[&'static str],
    mut load: impl FnMut(&str) -> Result<T, E>,
) -> Vec<&'static str> {
    candidates
        .iter()
        .filter(|name| load(name).is_ok())
        .copied()
        .collect()
}

/// 글꼴 캐시에 고정폭 하나 + 보조 글꼴들을 깔고, 고정폭의 family 를 돌려준다.
///
/// **이 파일에서 순수하지 않은 유일한 함수**다. 위 두 규칙(`pick`·`load_fallbacks`)은
/// 로더를 인자로 받아 창 없이 시험되고, 여기는 그것을 실제 캐시에 대고 부르기만 한다.
/// 두 뷰(`RootView`·`SessionView`)가 같은 것을 각자 적으면 한쪽에만 보조 글꼴이 빠지고,
/// 그 증상은 "어떤 화면에서만 한글이 두부"라 한참 안 보인다.
///
/// # 패닉
///
/// 고정폭을 하나도 못 찾으면 패닉한다 — 글자를 한 자도 못 그리므로 계속할 뜻이 없다.
/// 보조 글꼴이 하나도 없는 것은 패닉이 **아니다**(화면은 뜨고, 한글만 두부가 된다).
pub fn install(cache: &mut warpui::fonts::Cache) -> warpui::fonts::FamilyId {
    let (name, family) = match pick(CANDIDATES, |n| cache.load_system_font(n)) {
        Ok(picked) => picked,
        // "글꼴을 못 찾았다"만 남기면 사용자는 무엇을 깔아야 하는지 모른다.
        Err(tried) => panic!("{tried}"),
    };
    log::info!("고정폭 글꼴: {name}");

    let loaded = load_fallbacks(FALLBACK_CANDIDATES, |n| cache.load_system_font(n));
    if loaded.is_empty() {
        // 이 줄이 **조용한 두부의 유일한 예고**다. 없으면 사용자는 창을 보고서야 안다.
        log::warn!(
            "보조 글꼴이 하나도 없다 — 한글·기호가 두부(▯)로 그려진다. 시도한 것: {}",
            FALLBACK_CANDIDATES.join(", ")
        );
    } else {
        log::info!("보조 글꼴: {}", loaded.join(", "));
    }
    family
}

#[cfg(test)]
mod tests {
    use super::*;

    /// core 의 키 표가 **GUI 의 문법으로 읽히는가**.
    ///
    /// 이 크레이트에 있는 이유: 문법의 주인은 `warpui::keymap::Keystroke` 인데 core 는
    /// UI 를 의존할 수 없다(계층 게이트). 그러니 "core 의 표를 GUI 가 등록할 수 있는가"는
    /// 여기서만 물을 수 있다.
    ///
    /// 이게 없어서 몇 달을 놓쳤다: 표에 대문자 `G` 가 있었고 core·TUI 는 원시 문자열로
    /// 맞춰 보는데 GUI 는 이 파서를 쓴다 → **GUI 가 첫 프레임에 패닉**. GUI 가 P1 에
    /// 멈춰 있어 아무도 띄워 본 적이 없어 드러나지 않았다(2026-07-28 발견).
    /// 창을 띄우지 않고 잡는 것이 요점이다.
    #[test]
    fn every_core_binding_parses_in_the_gui_keymap_grammar() {
        use warpui::keymap::Keystroke;

        let keys = base::BINDINGS
            .iter()
            .map(|b| b.key)
            .chain(base::keys::SCROLL_BINDINGS.iter().map(|b| b.key));
        for key in keys {
            assert!(
                Keystroke::parse(key).is_ok(),
                "core 의 키 '{key}' 를 GUI 키맵이 못 읽는다 — 이 표는 두 뷰가 같은 \
                 문법으로 읽어야 한다(대문자는 `shift-G` 처럼 적는다)"
            );
        }
    }

    #[test]
    fn the_first_available_candidate_wins() {
        // 순서가 뜻을 갖는다 — 그 OS 의 기본 글꼴이 앞이라야 사용자가 다른 터미널에서
        // 보던 글자 모양과 같아진다.
        let (name, got) = pick(CANDIDATES, |n| {
            if n == "Consolas" { Ok(n.len()) } else { Err(()) }
        })
        .unwrap();
        assert_eq!(name, "Consolas");
        assert_eq!(got, "Consolas".len());
    }

    #[test]
    fn a_missing_font_does_not_stop_the_search() {
        // 이게 없어서 Windows 에서 첫 프레임에 죽었다. Menlo 가 없으면 다음을 본다.
        let mut asked = Vec::new();
        let picked = pick(CANDIDATES, |n| {
            asked.push(n.to_owned());
            if n == "DejaVu Sans Mono" { Ok(()) } else { Err(()) }
        });
        assert!(picked.is_ok());
        assert_eq!(asked, vec!["Menlo", "Consolas", "DejaVu Sans Mono"]);
    }

    #[test]
    fn failing_everywhere_names_what_was_tried() {
        // "글꼴을 못 찾았다"만 남기면 사용자는 무엇을 깔아야 하는지 모른다.
        let err = pick(CANDIDATES, |_| Err::<(), ()>(())).unwrap_err();
        for name in CANDIDATES {
            assert!(err.contains(name), "{name} 이 진단에 없다: {err}");
        }
    }

    #[test]
    fn loading_fallbacks_does_not_stop_at_the_first_hit() {
        // `pick` 과 반대다 — 폴백은 하나 고르는 것이 아니라 **깔아 두는** 것이다.
        // 첫 성공에서 멈추면 그 뒤의 기호·이모지 글꼴이 통째로 안 들어간다.
        let mut asked = Vec::new();
        let loaded = load_fallbacks(FALLBACK_CANDIDATES, |n| {
            asked.push(n.to_owned());
            if n.starts_with("Segoe") { Ok(()) } else { Err(()) }
        });
        assert_eq!(asked.len(), FALLBACK_CANDIDATES.len(), "전부 물어야 한다");
        assert_eq!(loaded, vec!["Segoe UI", "Segoe UI Symbol", "Segoe UI Emoji"]);
    }

    #[test]
    fn no_fallback_at_all_is_reported_not_hidden() {
        // 빈 목록이라야 부른 쪽이 경고를 남길 수 있다. 여기서 조용히 성공을 꾸미면
        // 두부 화면의 유일한 예고가 사라진다.
        assert!(load_fallbacks(FALLBACK_CANDIDATES, |_| Err::<(), ()>(())).is_empty());
    }

    #[test]
    fn this_platform_asks_for_a_hangul_family_we_actually_load() {
        // ★ 이 목록은 취향이 아니라 **계약**이다. cosmic-text 는 글리프가 없는 문자를
        // 만나면 가진 글꼴을 뒤지는 게 아니라 `script_fallback(Script::Hangul, …)` 의
        // **이름으로** 묻는다. 그 이름이 DB 에 없으면 한글은 두부가 된다 — 다른 한글
        // 글꼴을 아무리 넣어 놔도 마찬가지다.
        //
        // 그래서 이 OS 가 묻는 이름이 목록에 있는지를 기계로 잡는다. 새 플랫폼을 지원
        // 대상에 넣으면 여기서 컴파일이 아니라 **테스트가** 먼저 운다.
        let want = if cfg!(target_os = "windows") {
            "Malgun Gothic"
        } else if cfg!(target_os = "macos") {
            "Apple SD Gothic Neo"
        } else {
            "Noto Sans CJK KR"
        };
        assert!(
            FALLBACK_CANDIDATES.contains(&want),
            "이 OS 의 한글 폴백 이름 '{want}' 이 후보에 없다 — 한글이 두부로 그려진다"
        );
    }

    #[test]
    fn the_fallback_list_has_no_duplicates() {
        // 중복은 조용히 손해다 — 같은 family 를 두 번 로드하고, 목록을 훑는 사람은
        // 한쪽만 고치고 다른 쪽을 남긴다.
        let mut seen = FALLBACK_CANDIDATES.to_vec();
        seen.sort_unstable();
        let before = seen.len();
        seen.dedup();
        assert_eq!(before, seen.len(), "보조 글꼴 후보에 중복이 있다");
    }

    #[test]
    fn every_major_desktop_has_at_least_one_candidate() {
        // 목록이 한쪽 OS 로 기울면 그 밖에서는 다시 첫 프레임에 죽는다.
        assert!(CANDIDATES.contains(&"Menlo"), "macOS 기본이 빠졌다");
        assert!(CANDIDATES.contains(&"Consolas"), "Windows 기본이 빠졌다");
        assert!(
            CANDIDATES.contains(&"DejaVu Sans Mono")
                || CANDIDATES.contains(&"Liberation Mono"),
            "리눅스 후보가 빠졌다"
        );
    }
}
