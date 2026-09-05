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
/// - `Cascadia Mono` · `Cascadia Code` — **Windows Terminal 의 기본 글꼴**(2019~).
///   `Consolas` 보다 앞이다(pytmux-408): 이 목록의 규칙은 「그 OS 에서 터미널이 기본으로
///   쓰는 것이 앞」인데, Windows 의 그 자리는 2019년 이후 Consolas 가 아니다. 실측
///   (2026-08-26)으로 이 상자의 Windows Terminal 은 `fontFace` 를 안 적어 기본값
///   `Cascadia Mono` 로 돌고, 그래서 정본(TUI)과 GUI 의 글자 모양이 갈렸다.
///   ⚠ 취향이 아니라 **머리말의 규칙을 값에 반영하는 것**이다.
/// - `Consolas` — Windows 에 늘 있는 것(Vista 이후). Cascadia 가 없는 상자의 보루.
/// - `DejaVu Sans Mono` · `Liberation Mono` — 리눅스 배포판 대부분.
/// - `Courier New` — 고유 이름의 마지막 보루. 보기 좋진 않지만 거의 모든 곳에 있다.
/// - `monospace` — **이름이 아니라 별칭**이다(pytmux-484 ⓐ). 위가 전부 고유 이름이라,
///   흔한 다른 고정폭(`Noto Sans Mono`·`Ubuntu Mono`·`Source Code Pro`·`Fira Mono`·
///   `Hack` …)만 깔린 리눅스 상자는 **글꼴이 있는데도** 「하나도 못 찾았다」로 패닉했다
///   (2026-09-05 Ubuntu 24.04 aarch64 실측). fontconfig 는 이 일반 별칭을 그 상자가
///   고른 고정폭으로 풀어 주고, font-kit 은 리눅스에서 fontconfig 로 이름을 풀므로
///   별칭이 그대로 통한다. **맨 끝**이라 고유 이름이 있는 상자의 선택은 안 바뀐다.
///
/// OS 별로 `cfg` 를 나누지 않는 이유: 이 목록은 **가용성 순서**이지 플랫폼 선언이
/// 아니다. macOS 에 Consolas 를 깐 사람도 있고, 그 경우에도 Menlo 가 먼저 잡히면 된다.
/// (같은 이유로 `monospace` 도 `cfg` 없이 둔다 — 그 이름이 없는 OS 에서는 로더가 그냥
/// 실패하고, 실패한 후보는 이 규칙이 이미 건너뛴다.)
pub const CANDIDATES: &[&str] = &[
    "Menlo",
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Courier New",
    "monospace",
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

/// 사용자가 고른 이름을 **먼저** 물어보고, 없으면 후보로 떨어진다(pytmux-408).
///
/// 돌려주는 셋째 값은 **못 쓴 이름**이다(`Some(이름)` = 「적었는데 그 글꼴이 없다」).
/// ⛔ 그것을 `None` 으로 뭉개지 않는 이유: 「적었는데 아무 일도 안 일어난다」가 이 부류에서
/// 제일 나쁜 결과다 — 사용자는 자기가 틀렸는지 앱이 무시했는지 못 가린다. 부르는 쪽이
/// 그 이름으로 한 마디 한다.
///
/// 빈 값(자동)은 **못 쓴 것이 아니다** — 그때는 `None` 이다.
pub fn pick_preferred<T, E>(
    preferred: &str,
    candidates: &[&'static str],
    mut load: impl FnMut(&str) -> Result<T, E>,
) -> Result<(String, T, Option<String>), String> {
    let want = preferred.trim();
    if !want.is_empty() {
        if let Ok(font) = load(want) {
            return Ok((want.to_owned(), font, None));
        }
    }
    let (name, font) = pick(candidates, &mut load)?;
    let missed = (!want.is_empty()).then(|| want.to_owned());
    Ok((name.to_owned(), font, missed))
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
    install_preferred(cache, "").0
}

/// [`install`] 과 같되 **사용자가 고른 이름**을 먼저 본다(설정 `font-family`).
///
/// 둘째 값은 「적었는데 그 글꼴이 없더라」는 이름이다 — 부르는 쪽이 화면에 한 마디 한다.
pub fn install_preferred(
    cache: &mut warpui::fonts::Cache,
    preferred: &str,
) -> (warpui::fonts::FamilyId, Option<String>) {
    let (name, family, missed) =
        match pick_preferred(preferred, CANDIDATES, |n| cache.load_system_font(n)) {
            Ok(picked) => picked,
            // "글꼴을 못 찾았다"만 남기면 사용자는 무엇을 깔아야 하는지 모른다.
            Err(tried) => panic!("{tried}"),
        };
    if let Some(ref want) = missed {
        log::warn!("설정한 고정폭 글꼴 `{want}` 이 이 상자에 없다 — `{name}` 으로 떨어졌다");
    }
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
    (family, missed)
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

        // 표 셋을 **전부** 본다(pytmux-466 으로 esc/데모가 갈렸다) — 한 표만 보면 다른
        // 표에 새 글자가 들어올 때 그 패닉을 다시 놓친다.
        let keys = base::BINDINGS
            .iter()
            .chain(base::BLOCK_BINDINGS)
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
        assert_eq!(
            asked,
            vec!["Menlo", "Cascadia Mono", "Cascadia Code", "Consolas", "DejaVu Sans Mono"]
        );
    }

    #[test]
    fn a_box_with_only_a_generic_monospace_alias_still_starts() {
        // ☠ pytmux-484 ⓐ — 후보가 **전부 고유 이름**이면, 흔한 다른 고정폭만 깔린
        //    리눅스 상자는 글꼴이 있어도 「하나도 못 찾았다」로 시작 즉시 패닉한다
        //    (2026-09-05 Ubuntu 24.04 aarch64 실측 · `Noto Sans Mono` 만 있는 상자).
        //    fontconfig 의 일반 별칭 `monospace` 가 그 상자들을 살린다.
        let mut asked = Vec::new();
        let picked = pick(CANDIDATES, |n| {
            asked.push(n.to_owned());
            if n == "monospace" { Ok(()) } else { Err(()) }
        });
        assert!(
            picked.is_ok(),
            "고유 이름이 하나도 없는 상자에서 못 골랐다 — 시도한 것: {asked:?}"
        );
        assert_eq!(picked.unwrap().0, "monospace");
        // 그리고 그 별칭은 **맨 끝**이라야 한다 — 앞에 두면 고유 이름이 있는 상자의
        // 글자 모양이 조용히 바뀐다(이 목록의 순서가 곧 계약이다).
        assert_eq!(*CANDIDATES.last().unwrap(), "monospace");
        assert_eq!(asked.len(), CANDIDATES.len());
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
    fn the_windows_terminal_default_comes_before_the_old_one() {
        // pytmux-408 ② — 이 목록의 규칙은 머리말이 스스로 적어 둔 「그 OS 에서 터미널이
        // 기본으로 쓰는 것이 앞」이다. Windows 의 그 자리는 2019년 이후 Cascadia 이고,
        // 목록이 그 규칙보다 낡아 있었다(언제나 Consolas 로 떨어졌다).
        let at = |n: &str| CANDIDATES.iter().position(|c| *c == n);
        let cascadia = at("Cascadia Mono").expect("Windows Terminal 기본이 빠졌다");
        let consolas = at("Consolas").expect("Windows 보루가 빠졌다");
        assert!(
            cascadia < consolas,
            "Cascadia 가 Consolas 보다 뒤다 — 그러면 Windows 에서 영영 Consolas 다"
        );
    }

    #[test]
    fn the_font_the_user_picked_wins_over_the_candidates() {
        // pytmux-408 ① — 고른 이름이 있으면 그것부터. 후보는 «아무것도 안 골랐을 때»의 표다.
        let (name, _, missed) =
            pick_preferred("Sarasa Mono K", CANDIDATES, |n| {
                if n == "Sarasa Mono K" { Ok(()) } else { Err::<(), ()>(()) }
            })
            .unwrap();
        assert_eq!(name, "Sarasa Mono K");
        assert!(missed.is_none(), "쓴 글꼴을 못 썼다고 말했다");
    }

    #[test]
    fn a_font_the_user_picked_but_does_not_exist_is_said_out_loud() {
        // ⛔ pytmux-408 ③ — 「적었는데 아무 일도 안 일어난다」가 이 부류에서 제일 나쁘다.
        //    떨어지는 것은 맞지만, **무엇이 없었는지**를 부르는 쪽이 말할 수 있어야 한다.
        let (name, _, missed) = pick_preferred("NoSuchFont ZZ", CANDIDATES, |n| {
            if n == "Consolas" { Ok(()) } else { Err::<(), ()>(()) }
        })
        .unwrap();
        assert_eq!(name, "Consolas", "후보로 안 떨어졌다");
        assert_eq!(missed.as_deref(), Some("NoSuchFont ZZ"), "못 쓴 이름을 삼켰다");
    }

    #[test]
    fn picking_nothing_is_not_a_miss() {
        // 대조군 — 빈 값은 «자동»이지 «못 썼다»가 아니다. 여기를 안 가르면 아무것도 안
        // 고른 사람에게 매 기동 경고가 뜬다.
        let (_, _, missed) = pick_preferred("   ", CANDIDATES, |n| {
            if n == "Menlo" { Ok(()) } else { Err::<(), ()>(()) }
        })
        .unwrap();
        assert!(missed.is_none(), "안 고른 것을 «못 썼다»로 읽었다");
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
