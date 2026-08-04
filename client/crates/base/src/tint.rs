//! 패널 테두리가 **무슨 상태를 말하는가**(§10-21ⓩ) — 색이 아니라 뜻이다.
//!
//! # 왜 core 인가
//!
//! 정본은 이 판정을 클라가 한다(서버가 칠하는 것이 아니다): 원격 탭을 보고 있으면 분홍,
//! 응답이 저하되면 빨강(`clientio.py` 의 `_box_style_sig` — *"우선순위 degraded>원격>기본"*).
//! 그 판정이 뷰마다 있으면 한쪽만 조건을 빠뜨리고, 실제로 그랬다 — GUI 에는 원격 외곽선
//! 색도 degraded 판정도 **아예 없어서**(전 crate 검색 0건) 탭 라벨만 분홍이고 테두리는
//! 파랑이었다. 제보가 그 갈림을 그대로 찍었다.
//!
//! # 왜 색은 안 담나
//!
//! 이 저장소의 규칙이다(`gen_theme_names.py` 머리말): *"값이 옮겨 가면 두 클라가 같은
//! 배색이 돼야 한다는 뜻이 되는데 … 재는 것은 **아는가**이지 **같은 색인가**가 아니다."*
//! 그래서 여기는 **어느 뜻인가**만 정하고, 그 뜻을 무슨 색으로 그릴지는 뷰가 정한다.
//! (원격 분홍만은 두 클라가 같은 hex 를 쓰기로 이미 정해 뒀다 — GUI 쪽 상수 주석 참조.)

/// 테두리가 지금 말하는 것.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BorderTint {
    /// 평소 — **캔버스가 이미 칠한 색**을 그대로 쓴다(서버가 활성/비활성을 판정해 보낸다).
    Local,
    /// 원격 탭을 보고 있다. 로컬(파랑)과 한눈에 갈리라고 분홍이다(§1.7-a).
    Remote,
    /// 서버 응답이 저하됐다(RTT 임계 초과). **다른 무엇보다 먼저** 보여야 한다.
    Degraded,
}

/// 지금 테두리가 무엇을 말해야 하나 — 정본과 **같은 우선순위**다.
///
/// `degraded > 원격 > 기본`. 순서가 뒤집히면 원격 탭에서 응답이 죽었을 때 화면이
/// "원격이다"만 말하고 **끊기고 있다는 것을 안 말한다** — 둘 중 급한 쪽은 후자다.
pub fn border(remote: bool, degraded: bool) -> BorderTint {
    if degraded {
        BorderTint::Degraded
    } else if remote {
        BorderTint::Remote
    } else {
        BorderTint::Local
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn degraded_wins_over_remote() {
        // ★ 원격 탭에서 응답이 죽었을 때 "원격이다"만 말하면 급한 쪽을 안 말한 것이다.
        assert_eq!(border(true, true), BorderTint::Degraded);
        assert_eq!(border(false, true), BorderTint::Degraded);
    }

    #[test]
    fn remote_wins_over_plain() {
        assert_eq!(border(true, false), BorderTint::Remote);
    }

    #[test]
    fn the_plain_case_leaves_the_canvas_alone() {
        // `Local` 은 "우리가 안 칠한다"는 뜻이다 — 서버가 활성/비활성을 이미 판정해
        // 보내므로, 여기서 색을 지어내면 그 판정을 덮어쓴다.
        assert_eq!(border(false, false), BorderTint::Local);
    }
}
