//! **어느 트랜스크립트를 보여 줄 것인가** — 두 뷰가 같은 답을 내야 하는 판단.
//!
//! 항목을 만드는 것(`Transcript`)과 별개로, 화면에 붙일 대화를 **어디서** 가져올지는 그
//! 자체로 판단이다. 로컬 패널이면 이 머신의 파일을 직접 읽고, 원격 패널이면 상류가 실어
//! 보낸 원문 꼬리를 쓴다.
//!
//! 이 판단이 뷰마다 있으면 한쪽만 원격을 빠뜨리고, 그 증상은 **원격 패널 자리에 로컬
//! 세션의 대화가 뜨는 것**이다(2026-07-27g 실측 결함). 비어 보이는 것보다 나쁘다 — 조용히
//! 틀린 화면이라 사용자는 남의 세션인 줄 모른다.

use std::collections::HashMap;

use base::i18n::{t, tf};

use crate::Transcript;

/// 지금 보이는 패널의 Claude 항목을 어디서 가져오나.
#[derive(Debug, PartialEq, Eq)]
pub enum Source {
    /// 이 머신의 트랜스크립트 파일(로컬 탭). 값은 패널의 작업 디렉터리다.
    LocalFile(String),
    /// 상류가 보내 준 원문 꼬리(원격 탭). 값은 패널 id 다.
    Upstream(i64),
    /// 고를 것이 없다(배치를 아직 못 받았거나 대화가 없다).
    Nothing,
}

/// **직접 읽을 수 있으면 그쪽이 낫다.**
///
/// 상류가 주는 것은 상한이 걸린 꼬리(64KB/80줄)라 대화 앞부분이 없고, 이 머신의 파일에는
/// 전부 있다. 로컬 패널에서 상류 것을 쓰면 잘 보이던 목록이 짧아진다 — 기능 추가가 회귀가
/// 된다. 그래서 상류 것은 "읽을 수 없을 때"의 자리다.
///
/// `cwd` 가 `None` 인 경우가 정확히 그것이고, 그 판정은
/// [`SessionState::active_cwd`](../../proto/session/struct.SessionState.html)
/// 이 한다(원격 탭이면 알려 주지 않는다).
pub fn pick(cwd: Option<String>, pane: Option<i64>) -> Source {
    match (cwd, pane) {
        (Some(cwd), _) => Source::LocalFile(cwd),
        (None, Some(pane)) => Source::Upstream(pane),
        (None, None) => Source::Nothing,
    }
}

/// 상류가 보내 준 원문 꼬리를 패널별로 담아 둔다.
///
/// 로컬 패널에 대해서도 받는다(서버는 광고한 클라에게 다 보낸다). 받아 두기만 하고 안
/// 쓴다 — 어느 패널이 원격인지는 프레임에 안 적혀 있고, 그 판정은 탭 정보를 가진 쪽의
/// 몫이다.
#[derive(Default)]
pub struct RemoteTranscripts {
    by_pane: HashMap<i64, Transcript>,
}

impl RemoteTranscripts {
    /// 꼬리 원문 하나를 반영한다.
    ///
    /// **매번 새로 파싱한다.** 꼬리는 앞부분이 잘려 나가는 창이라 이어붙이기(`feed`)를
    /// 하면 같은 항목이 두 번 쌓인다.
    pub fn apply(&mut self, pane: i64, tail: &str) {
        self.by_pane.insert(pane, Transcript::parse(tail));
    }

    pub fn get(&self, pane: i64) -> Option<&Transcript> {
        self.by_pane.get(&pane)
    }

    /// 그 패널의 항목과 권한 모드. 없으면 `None`.
    ///
    /// 두 뷰가 같은 모양으로 꺼내 쓰라고 둔다 — 한쪽이 모드를 안 꺼내면 그 클라에서만
    /// 머리줄의 권한 모드가 사라진다.
    pub fn snapshot(&self, pane: i64) -> Option<(Vec<crate::Item>, Option<String>)> {
        let t = self.get(pane)?;
        Some((t.items().to_vec(), t.mode().map(str::to_owned)))
    }
}

/// 전용 화면 한 줄이 어떤 부류인가. **구체적인 색은 뷰가 정한다.**
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DetailKind {
    /// `플랜 [상태]` 머리줄.
    PlanHead,
    /// `막힌 호출` 머리줄.
    DeniedHead,
    /// 본문(전문·사유).
    Body,
    /// 사이를 띄우는 빈 줄.
    Blank,
}

/// 전용 화면(플랜 전문·거부 사유)에 들어갈 줄들.
///
/// # 왜 여기인가
///
/// 요약 구역은 한 줄짜리라 **여러 줄이 들어갈 자리가 없다**(구역이 커지면 서버 캔버스가
/// 밀린다 — P5 결정 ③). 그래서 전문은 캔버스를 덮는 화면이 맡는데, "무엇을 보여 줄
/// 것인가"(마지막 플랜 · 마지막 거부)와 그 줄의 짜임은 두 뷰가 같아야 한다.
///
/// **자르지 않는다.** 몇 줄까지 그릴지는 캔버스 높이를 아는 뷰의 몫이다(그 높이를 넘으면
/// 이 화면을 여는 것만으로 아래 요약이 화면 밖으로 밀린다).
pub fn detail_lines(items: &[crate::Item]) -> Vec<(String, DetailKind)> {
    use crate::{ItemKind, ToolState};

    let plan = items
        .iter()
        .rev()
        .find(|i| matches!(i.kind, ItemKind::Plan { .. }));
    let denied = items
        .iter()
        .rev()
        .find(|i| i.state() == Some(ToolState::Denied));

    let mut lines = Vec::new();
    if let Some(item) = plan {
        lines.push((tf("플랜 [{state}]", &[("state", item.badge())]), DetailKind::PlanHead));
        match &item.detail {
            Some(body) => lines.extend(
                body.lines()
                    .map(|l| (format!("  {l}"), DetailKind::Body)),
            ),
            // 전문이 없으면 요약이라도 보인다 — 빈 화면은 "없는 것"과 "못 읽은 것"이
            // 구분되지 않는다.
            None => lines.push((format!("  {}", item.title), DetailKind::Body)),
        }
    }
    if let Some(item) = denied {
        if !lines.is_empty() {
            lines.push((String::new(), DetailKind::Blank));
        }
        lines.push((t("막힌 호출").to_owned(), DetailKind::DeniedHead));
        let name = item.name().unwrap_or("");
        lines.push((format!("  {} {name} {}", item.badge(), item.title), DetailKind::Body));
        if let Some(reason) = &item.detail {
            // ⚠ 사유는 사용자 유래 값 — 마지막(유일한) 자리로 끼운다. 앞 두 칸은
            // 언어가 아니라 들여쓰기라 번역 밖에 둔다.
            lines.push((format!("  {}", tf("사유: {reason}", &[("reason", reason)])), DetailKind::Body));
        }
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_readable_cwd_always_wins_over_the_upstream_tail() {
        // 상류 꼬리는 상한이 걸려 있어 대화 앞부분이 없다. 로컬 패널에서 그걸 쓰면
        // 잘 보이던 목록이 짧아진다 — 기능 추가가 회귀가 된다.
        assert_eq!(
            pick(Some("/w/x".into()), Some(3)),
            Source::LocalFile("/w/x".into())
        );
    }

    #[test]
    fn without_a_cwd_the_upstream_tail_is_used() {
        // cwd 가 없다 = 원격 탭이다(로컬이면 블록이 알려 준다).
        assert_eq!(pick(None, Some(7)), Source::Upstream(7));
    }

    #[test]
    fn with_neither_there_is_nothing_to_show() {
        assert_eq!(pick(None, None), Source::Nothing);
    }

    #[test]
    fn a_new_tail_replaces_the_old_one_instead_of_appending() {
        // ★ 꼬리는 앞부분이 잘려 나가는 **창**이다. 이어붙이면 겹치는 항목이 두 번 쌓여
        // 같은 툴 호출이 목록에 두 줄로 뜬다.
        let line = |t: &str| format!(r#"{{"type":"user","message":{{"content":"{t}"}}}}"#);
        let mut remote = RemoteTranscripts::default();
        remote.apply(1, &format!("{}\n{}", line("첫째"), line("둘째")));
        let before = remote.get(1).unwrap().items().len();
        remote.apply(1, &format!("{}\n{}", line("둘째"), line("셋째")));
        assert_eq!(
            remote.get(1).unwrap().items().len(),
            before,
            "꼬리가 겹쳐 들어와 항목이 쌓였다"
        );
    }

    #[test]
    fn the_detail_screen_shows_the_latest_plan_and_the_latest_denial() {
        use crate::{Item, ItemKind, ToolState};
        let plan = |t: &str| Item {
            kind: ItemKind::Plan { state: ToolState::Running },
            title: t.into(),
            detail: Some("1단계\n2단계".into()),
        };
        let denied = |t: &str| Item {
            kind: ItemKind::Tool { name: "Bash".into(), state: ToolState::Denied },
            title: t.into(),
            detail: Some("사용자가 막았다".into()),
        };
        let items = vec![plan("옛 플랜"), denied("옛 거부"), plan("새 플랜"), denied("새 거부")];
        let text: Vec<String> = detail_lines(&items).into_iter().map(|(t, _)| t).collect();
        let joined = text.join("\n");

        // **마지막 것**이라야 한다 — 옛것을 보이면 사용자는 방금 막힌 호출을 못 찾는다.
        assert!(joined.contains("새 플랜") || joined.contains("1단계"), "{joined}");
        assert!(joined.contains("새 거부"), "{joined}");
        assert!(!joined.contains("옛 거부"), "옛 항목이 남았다: {joined}");
        assert!(joined.contains("사유:"), "거부 사유가 빠졌다: {joined}");
    }

    #[test]
    fn nothing_to_show_produces_no_lines_at_all() {
        // 빈 목록이라야 뷰가 "보여 줄 것이 없다"고 말할 수 있다. 여기서 안내 문구를
        // 지어 넣으면 두 뷰가 서로 다른 문장을 갖게 된다.
        assert!(detail_lines(&[]).is_empty());
    }

    #[test]
    fn a_plan_without_a_body_still_says_something() {
        use crate::{Item, ItemKind, ToolState};
        let items = vec![Item {
            kind: ItemKind::Plan { state: ToolState::Ok },
            title: "세 단계로 간다".into(),
            detail: None,
        }];
        let lines = detail_lines(&items);
        assert!(lines.len() >= 2, "전문이 없다고 빈 화면이 되면 안 된다: {lines:?}");
        assert!(lines.iter().any(|(t, _)| t.contains("세 단계로 간다")));
    }

    #[test]
    fn panes_do_not_bleed_into_each_other() {
        // 한 세션에 Claude 패널이 여럿일 수 있다 — 아무거나 고르면 남의 대화가 뜬다.
        let mut remote = RemoteTranscripts::default();
        remote.apply(1, r#"{"type":"user","message":{"content":"하나"}}"#);
        assert!(remote.get(1).is_some());
        assert!(remote.get(2).is_none(), "안 받은 패널까지 채우면 안 된다");
        assert!(remote.snapshot(2).is_none());
    }
}
