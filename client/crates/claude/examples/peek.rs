//! 진짜 트랜스크립트로 파서를 확인한다.
//!
//! 픽스처는 우리가 만든 것이라 "우리가 아는 모양"만 덮는다. 실제 파일에는 우리가 아직
//! 안 본 이벤트 종류·툴 이름이 들어 있다 — 그게 이 도구의 존재 이유다.
//!
//! **내용은 안 찍는다.** 대화 본문은 사적인 데이터이고, 확인에 필요한 것은 "몇 개를
//! 어떤 종류로 읽었나"와 "요약이 비어 있지 않나" 뿐이다.
//!
//! ```sh
//! cargo run -p claude --example peek -- /경로/작업디렉터리
//! ```

use claude::discover::{Watcher, projects_dir};
use claude::ItemKind;

fn main() {
    let cwd = std::env::args()
        .nth(1)
        .unwrap_or_else(|| std::env::current_dir().unwrap().display().to_string());
    let Some(root) = projects_dir() else {
        eprintln!("트랜스크립트 폴더를 찾을 수 없다($HOME 도 $CLAUDE_CONFIG_DIR 도 없음)");
        std::process::exit(1);
    };
    let mut watcher = Watcher::new(Some(root.clone()));
    watcher.set_cwd(&cwd);
    watcher.refresh();
    let Some(path) = watcher.path() else {
        println!("{cwd} 의 트랜스크립트가 없다(폴더: {})", root.display());
        return;
    };
    println!("파일: {}", path.display());

    let (mut prompts, mut answers, mut tools, mut plans, mut empty) = (0, 0, 0, 0, 0);
    // 상태 칸은 `ToolState` 의 선언 순서(repr(usize))다 — 진행중·성공·실패·거부.
    let mut names: std::collections::BTreeMap<String, [usize; 4]> = Default::default();
    for item in watcher.items() {
        if item.title.is_empty() {
            empty += 1;
        }
        match &item.kind {
            ItemKind::Prompt => prompts += 1,
            ItemKind::Answer => answers += 1,
            ItemKind::Tool { name, state } => {
                tools += 1;
                let slot = names.entry(name.clone()).or_default();
                slot[*state as usize] += 1;
            }
            ItemKind::Plan { state } => {
                plans += 1;
                let slot = names.entry("(플랜)".to_owned()).or_default();
                slot[*state as usize] += 1;
            }
        }
    }
    println!(
        "항목 {} — 프롬프트 {prompts} · 답변 {answers} · 툴 {tools} · 플랜 {plans}",
        watcher.items().len()
    );
    println!("요약이 빈 항목: {empty} (0 이어야 쓸모가 있다)");
    println!("권한 모드: {}", watcher.mode().unwrap_or("(기록 없음)"));
    for (name, [running, ok, failed, denied]) in names {
        println!("  {name:<20} 진행중 {running} · 성공 {ok} · 실패 {failed} · 거부 {denied}");
    }
    // 사유·전문은 **길이만** 본다. 거부 사유에는 명령줄이, 플랜에는 계획 본문이 들어 있다.
    if let Some(denied) = watcher.last_denied() {
        println!(
            "최근 거부: 사유 {}자",
            denied.detail.as_ref().map_or(0, |d| d.chars().count())
        );
    }
    if let Some(plan) = watcher.last_plan() {
        println!(
            "최근 플랜: 전문 {}줄",
            plan.detail.as_ref().map_or(0, |d| d.lines().count())
        );
    }
}
