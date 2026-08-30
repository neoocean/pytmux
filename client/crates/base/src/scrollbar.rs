//! **표시용** 스크롤바의 산수 — 지금 보는 자리가 전체 중 어디인가(pytmux-25 · §10-21ⓨ2).
//!
//! # 무엇을 재나
//!
//! 트랙을 1.0 으로 봤을 때의 **(썸 시작, 썸 길이)** 뿐이다. 픽셀도 글자도 여기서 안
//! 정한다 — 캔버스 패널은 테두리 선 위에 얹고([`overlay_fraction`]) 목록 판은 판
//! 오른쪽에 얹는데([`list_fraction`]), 그 자리는 뷰의 것이고 **산수는 하나**여야 한다.
//!
//! # ⛔ 종전에 여기 있던 «조작용» 스크롤바는 걷었다 (pytmux-377)
//!
//! `▲`/`▼`/트랙을 탭으로 눌러 스크롤하던 열(설정 `touch-scroll` · 상태줄 `⇕` 배지)이
//! 여기 함께 있었다. **휠을 안 넘겨 주는 단말**을 위해 만든 것이었는데 *"실제로는 잘
//! 동작하지 않는다"* 는 사용자 판단으로 두 클라에서 같이 지웠다(2026-08-23).
//! ⛔ **그때 이 파일을 통째로 지우면 안 됐다** — 아래 것은 **다른 물건**이다:
//! 저것은 칸을 먹고 탭을 받았고, 이것은 칸을 안 먹고 아무것도 안 받는다. 그 갈림을
//! 이 파일의 옛 주석이 미리 적어 뒀고(*"둘을 한 자리로 합치면 touch-scroll 을 끈
//! 사람에게 표시까지 사라진다"*), 이번 삭제가 정확히 그 경고가 겨눈 상황이었다.
//!
//! # 좌표계
//!
//! 서버 프레임의 두 값만으로 닫힌다 — `top`(뷰포트 첫 행의 **절대** 인덱스)과
//! `scroll`(라이브에서 위로 올라간 행수). 위로 더 갈 수 있는 최대치는 `top + scroll`,
//! 전체 행수는 `top + h + scroll`.

/// 막대를 그릴 최소 높이 — 그 아래로는 한 칸짜리 막대라 자리만 차지한다.
pub const MIN_H: usize = 3;

/// **표시용** 스크롤바 — 지금 보는 자리가 전체 중 어디인가(§10-21ⓨ2).
///
/// # 칸을 안 먹는다
///
/// 제보(pytmux-25)가 말한 것은 **표시**이고, 자리도 칸이 아니라 **외곽선 위**다.
/// GUI 는 테두리를 실제 선으로 그리므로(N8) 칸을 안 먹고 선 위에 얹을 수 있다 —
/// 캔버스 격자를 안 건드리니 서버에 보고하는 행·열도 안 바뀐다.
///
/// # 무엇을 돌려주나
///
/// 트랙(패널 안쪽 높이)을 1.0 으로 봤을 때 **(썸 시작, 썸 길이)** 다. 그릴 것이 없으면
/// `None`:
/// - 스크롤백이 없다(위로 갈 데가 없으면 막대는 늘 꽉 차서 아무 말도 안 한다)
/// - 패널이 너무 낮다(한 칸짜리 막대는 자리만 차지한다)
///
/// 값이 아니라 **비율**인 이유: 픽셀은 뷰의 것이다(선 위 몇 픽셀인지는 테마가 정한다).
pub fn overlay_fraction(h: usize, top: usize, scroll: usize) -> Option<(f64, f64)> {
    if h < MIN_H {
        return None;
    }
    let max_scroll = top + scroll;
    if max_scroll == 0 {
        return None;   // 스크롤백 없음 — 그릴 것이 없다
    }
    let total = max_scroll + h;
    // 썸 길이 = 보이는 만큼의 비율. 너무 짧으면 안 보이므로 하한을 둔다.
    let len = (h as f64 / total as f64).clamp(MIN_THUMB, 1.0);
    // 0.0 = 맨 위(스크롤 최대) … 1.0 = 맨 아래(라이브).
    let frac = 1.0 - scroll as f64 / max_scroll as f64;
    let start = (1.0 - len) * frac;
    Some((start.clamp(0.0, 1.0 - len), len))
}

/// **목록 판**의 표시용 막대 — 보이는 줄 `visible` / 전체 `total` / 창의 첫 줄 `first`.
///
/// # 왜 [`overlay_fraction`] 을 다시 안 적나
///
/// 두 자리는 좌표계만 다르고 산수는 하나다. 캔버스 쪽은 「위로 얼마나 올라갔나」
/// (`top`·`scroll`)로 말하고 목록은 「창의 첫 줄이 몇 번째인가」(`first`)로 말한다 —
/// 옮겨 담는 규칙이 아래 세 줄이고, 그것만 여기 두면 반올림·하한(`MIN_THUMB`)·
/// 「그릴 것이 없다」 판정이 저쪽과 **영영 같다**. 한 벌 더 적으면 갈리는 날 조용하다.
///
/// 「위로 갈 수 있는 최대치」가 곧 안 보이는 줄 수(`total - visible`)이고, 그중 이미
/// 내려온 만큼이 `first` 이므로 남은 것(= `scroll`)은 `hidden - first` 다.
///
/// 다 보이면(`total <= visible`) `None` — 늘 꽉 찬 막대는 아무 말도 안 한다.
pub fn list_fraction(visible: usize, total: usize, first: usize) -> Option<(f64, f64)> {
    let hidden = total.checked_sub(visible)?;
    let first = first.min(hidden);
    overlay_fraction(visible, first, hidden - first)
}

/// 표시용 썸의 **최소 길이**(트랙 대비). 스크롤백이 아주 길면 비율이 0 에 수렴해
/// 막대가 사라지는데, 그러면 "어디쯤인가"를 말하려고 그린 것이 아무 말도 안 한다.
pub const MIN_THUMB: f64 = 0.06;

#[cfg(test)]
mod tests {
    use super::*;

    // ── 표시용 막대(§10-21ⓨ2) ────────────────────────────────────────────────

    #[test]
    fn there_is_nothing_to_show_without_scrollback() {
        // 위로 갈 데가 없으면 막대는 늘 꽉 차서 아무 말도 안 한다 — 안 그리는 것이 맞다.
        assert_eq!(overlay_fraction(24, 0, 0), None);
        // 낮은 패널도 마찬가지다(한 칸짜리 막대는 자리만 차지한다).
        assert_eq!(overlay_fraction(2, 100, 50), None);
    }

    #[test]
    fn the_thumb_sits_at_the_bottom_when_live_and_at_the_top_when_scrolled_all_the_way() {
        // 라이브(scroll = 0)면 맨 아래, 끝까지 올라가면 맨 위다.
        let (start, len) = overlay_fraction(10, 90, 0).expect("막대");
        assert!((start + len - 1.0).abs() < 1e-9, "라이브인데 바닥이 아니다: {start} {len}");
        let (start, _) = overlay_fraction(10, 0, 90).expect("막대");
        assert!(start.abs() < 1e-9, "끝까지 올라갔는데 맨 위가 아니다: {start}");
    }

    #[test]
    fn a_very_long_scrollback_still_leaves_something_to_see() {
        // 비율이 0 에 수렴하면 "어디쯤인가"를 말하려고 그린 것이 아무 말도 안 한다.
        let (_, len) = overlay_fraction(24, 1_000_000, 0).expect("막대");
        assert!(len >= MIN_THUMB, "썸이 사라졌다: {len}");
        assert!(len <= 1.0);
    }

    #[test]
    fn the_thumb_never_runs_past_the_track() {
        // 어느 자리에서도 시작+길이가 1 을 넘지 않는다(넘으면 테두리 밖으로 그린다).
        for scroll in [0, 1, 7, 33, 99] {
            let (start, len) = overlay_fraction(20, 100 - scroll, scroll).expect("막대");
            assert!(start >= 0.0 && start + len <= 1.0 + 1e-9, "{scroll}: {start}+{len}");
        }
    }

    // ── 목록 판의 막대(pytmux-374 ⑴) ────────────────────────────────────────

    #[test]
    fn a_list_that_fits_gets_no_bar() {
        // 다 보이면 그릴 것이 없다 — 「스크롤할 수 있다」는 거짓말을 안 한다.
        assert_eq!(list_fraction(40, 34, 0), None);
        assert_eq!(list_fraction(34, 34, 0), None);
    }

    #[test]
    fn the_list_thumb_walks_from_top_to_bottom() {
        // 첫 줄이 0 이면 맨 위, 끝까지 내려가면 맨 아래다(캔버스 쪽과 같은 규약).
        let (start, len) = list_fraction(6, 34, 0).expect("막대");
        assert!(start.abs() < 1e-9, "맨 위가 아니다: {start}");
        let (start, _) = list_fraction(6, 34, 28).expect("막대");
        assert!((start + len - 1.0).abs() < 1e-9, "끝인데 바닥이 아니다: {start} {len}");
        // 창의 첫 줄이 안 보이는 줄 수를 넘겨도 바닥에서 멈춘다(뷰가 넘겨 줄 수 있다).
        assert_eq!(list_fraction(6, 34, 999), list_fraction(6, 34, 28));
    }

    #[test]
    fn the_list_thumb_never_runs_past_the_track() {
        for first in 0..=28 {
            let (start, len) = list_fraction(6, 34, first).expect("막대");
            assert!(start >= 0.0 && start + len <= 1.0 + 1e-9, "{first}: {start}+{len}");
        }
    }
}
