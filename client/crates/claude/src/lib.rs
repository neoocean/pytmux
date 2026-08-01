//! Claude Code 세션을 **블록으로** 읽는다(§10-11 P5).
//!
//! # 왜 화면이 아니라 트랜스크립트인가
//!
//! pytmux 는 Claude 상태를 대부분 **화면 스크랩**으로 안다(`plugins/claude-code`). 그건
//! 상태줄 배지에는 충분하지만 블록 UI 의 권위 소스로는 약하다 — 프롬프트 모양·색·줄바꿈이
//! 바뀌면 파싱이 통째로 흔들린다. 설계문서 §6 이 못박은 결론이 그래서 **"권위 소스 =
//! 트랜스크립트(JSONL), 스크랩은 라이브 상태 보조"** 이고, 이 크레이트는 그 권위 쪽만 읽는다.
//!
//! 트랜스크립트에는 툴 호출·입력·결과가 **구조화되어** 있다. 화면을 다시 파싱할 이유가 없다.
//!
//! # 무엇을 만드는가
//!
//! JSONL 한 줄씩 → [`Item`] 목록. 한 항목이 화면의 블록 하나가 된다:
//!
//! - 사용자가 친 프롬프트
//! - Claude 의 답변 텍스트
//! - 툴 호출 한 건(이름 + 한 줄 요약 + 성공/실패/거부/진행중)
//! - 플랜 제시([`ItemKind::Plan`]) — 승인/거부를 고르는 자리라 따로 센다
//!
//! 여기에 더해 항목이 **아닌** 것 하나를 함께 들고 있다: 지금 걸린 권한 모드
//! ([`Transcript::mode`]).
//!
//! # 모르는 것을 성공으로 넘겨짚지 않는다
//!
//! 툴 결과가 아직 안 온 호출은 [`ToolState::Running`] 이다. 이걸 성공으로 칠하면
//! 사용자는 끝나지 않은 일을 끝났다고 읽는다 — 블록 종료코드에서와 **같은 규칙**이다
//! (`proto::blocks`).
//!
//! # 막힌 것과 깨진 것은 다르다
//!
//! 권한이 막은 호출은 [`ToolState::Denied`] 이지 [`ToolState::Failed`] 가 아니다. 둘을
//! 같이 칠하면 화면은 "명령이 깨졌다"와 "내가 안 시켰다"를 구분해 주지 못하는데, 사용자가
//! 할 일은 정반대다(전자는 고친다, 후자는 허용하거나 그대로 둔다). 판정 근거는
//! [`DENIAL_PREFIXES`] 에 적어 뒀다 — `is_error` 만으로는 안 갈린다.
//!
//! # 생각(thinking)은 안 싣는다
//!
//! `thinking` 블록은 분량이 크고 화면에 그대로 붙이면 목록이 그것만으로 찬다. 대신
//! 답변·툴 호출 사이의 흐름만 남긴다.

use std::path::Path;

use base::i18n::{t, tf};

pub mod discover;
pub mod source;

pub use discover::{encode_project_dir, newest_transcript_for};

/// 한 항목의 글자 상한. 트랜스크립트에는 수천 줄짜리 입력이 들어 있다 — 그대로 들면
/// 목록 한 줄이 화면을 통째로 먹는다.
pub const MAX_TEXT: usize = 200;

/// 보관할 항목 수 상한. 트랜스크립트는 계속 자라므로 **반드시 잘라야 한다**
/// (상한 없는 목록 = 이 저장소가 이미 클라 프리즈로 물린 부류).
pub const MAX_ITEMS: usize = 300;

/// 툴 호출의 진행 상태.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
#[repr(usize)]
pub enum ToolState {
    /// 결과가 아직 안 왔다. **성공이 아니다.**
    Running,
    Ok,
    Failed,
    /// 권한이 막았다. 툴이 깨진 것이 아니라 **하지 않은 것**이다.
    Denied,
}

impl ToolState {
    /// 목록에 붙일 짧은 표식. 색은 뷰가 정한다.
    pub fn badge(self) -> &'static str {
        match self {
            ToolState::Running => "···",
            ToolState::Ok => "ok",
            ToolState::Failed => "err",
            ToolState::Denied => "no",
        }
    }
}

/// 거부를 알리는 Claude Code 의 정형 문구.
///
/// **접두로만 판정한다.** 툴 출력 *안에* 이 문장이 인용돼 있을 수 있고(실제로 이 문구를
/// 찾는 grep 결과가 그렇다), 포함 검사로 하면 그 출력이 통째로 거부로 뒤집힌다.
///
/// 실측 근거(로컬 트랜스크립트 1,843개 전수, 2026-07-27): 이 두 접두로 시작하는
/// 툴 결과 **100건**(84 + 16)이고 **전부 `is_error: true`** 였다. 그래서 `is_error` 만
/// 보면 거부가 실패에 섞여 들어간다 — 가르는 것은 이 접두뿐이다.
const DENIAL_PREFIXES: &[&str] = &[
    "Permission for this action was denied",
    "The user doesn't want to proceed",
];

/// 항목의 종류.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ItemKind {
    /// 사용자가 친 것.
    Prompt,
    /// Claude 가 말한 것.
    Answer,
    /// 툴 호출 한 건.
    Tool { name: String, state: ToolState },
    /// 플랜 제시(`ExitPlanMode`). 툴이긴 하지만 **사용자가 승인/거부를 고르는 자리**라
    /// 목록에서 다른 줄과 같이 흘러가면 안 된다.
    Plan { state: ToolState },
}

/// 화면에 블록 하나로 나가는 항목.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Item {
    pub kind: ItemKind,
    /// 한 줄 요약(툴이면 무엇을 했는지, 프롬프트/답변이면 첫 줄).
    pub title: String,
    /// 한 줄에 안 들어가는 본문 — 플랜 전문, 거부 사유.
    ///
    /// **요약 구역은 이걸 그리지 않는다.** 아래 구역은 [`crate`] 문서가 말하는 5줄
    /// 고정이고 여기 여러 줄을 넣으면 서버 캔버스가 화면 밖으로 밀린다. 이 값은 전용
    /// 화면(뷰의 오버레이)만 읽는다.
    pub detail: Option<String>,
}

impl Item {
    /// 툴/플랜 항목이면 상태를, 아니면 `None`.
    pub fn state(&self) -> Option<ToolState> {
        match &self.kind {
            ItemKind::Tool { state, .. } | ItemKind::Plan { state } => Some(*state),
            _ => None,
        }
    }

    /// 줄 앞에 붙는 짧은 표식. 색은 뷰가 정한다.
    ///
    /// 뷰마다 적으면 같은 대화가 클라마다 달라 보인다(탭 라벨을 `Tab::label` 로 내린
    /// 것과 같은 이유). 사람이 친 것과 Claude 가 말한 것은 **상태가 없으므로** 고정
    /// 기호이고, 툴·플랜은 상태 표식을 그대로 쓴다.
    pub fn badge(&self) -> &'static str {
        match &self.kind {
            ItemKind::Prompt => "›",
            ItemKind::Answer => "⏺",
            ItemKind::Tool { state, .. } | ItemKind::Plan { state } => state.badge(),
        }
    }

    /// 표식과 요약 사이에 들어가는 이름. 없으면 `None`.
    ///
    /// 플랜은 `ExitPlanMode` 라는 **내부 이름을 그대로 보이지 않는다** — 사용자가 아는
    /// 낱말이 아니다.
    pub fn name(&self) -> Option<&str> {
        match &self.kind {
            ItemKind::Tool { name, .. } => Some(name),
            ItemKind::Plan { .. } => Some(t("플랜")),
            _ => None,
        }
    }

    fn line(kind: ItemKind, title: String) -> Self {
        Self {
            kind,
            title,
            detail: None,
        }
    }
}

/// 트랜스크립트 한 세션을 읽어 만든 항목 목록.
#[derive(Clone, Debug, Default)]
pub struct Transcript {
    items: Vec<Item>,
    /// 툴 호출 id → `items` 안의 위치. 결과가 나중에 와서 상태를 고쳐야 한다.
    pending: Vec<(String, usize)>,
    /// 마지막으로 본 권한 모드(`permission-mode` 이벤트의 `permissionMode`).
    mode: Option<String>,
}

impl Transcript {
    pub fn new() -> Self {
        Self::default()
    }

    /// JSONL 전체를 읽는다. 깨진 줄은 **그 줄만** 버린다 — 쓰는 중인 파일의 마지막 줄이
    /// 반쯤 쓰여 있는 것은 정상이고, 그걸로 전체를 포기하면 화면이 빈다.
    pub fn parse(text: &str) -> Self {
        let mut out = Self::new();
        out.feed(text);
        out
    }

    /// 뒤에 **덧붙은** JSONL 을 이어서 읽는다.
    ///
    /// 트랜스크립트는 append-only 라, 대화가 이어지는 동안 매번 파일을 통째로 다시
    /// 파싱할 이유가 없다 — 그런데 그게 30Hz 루프 위에서 일어나면 비용이 **파일 크기에
    /// 비례**한다(실측: 23MB 트랜스크립트 재파싱이 디버그 빌드 116ms · 릴리스 8.8ms,
    /// 활성 세션에서는 poll 마다 재발생). 이어 읽기는 비용을 **덧붙은 바이트**에
    /// 비례하게 만든다. 이어 붙일 지점을 관리하는 것은 [`discover::Watcher`] 다.
    /// 반환값은 **보이는 것이 달라졌는가** — 줄이 늘어도 항목을 안 만드는 이벤트가
    /// 많아서(thinking·메타), 줄 수로 판단하면 30Hz 로 헛 repaint 가 돈다. 반대로 항목
    /// 수만 보면 툴 결과가 `진행중 → 성공` 으로 바뀌는 것을 **놓친다**(항목은 그대로다).
    pub fn feed(&mut self, text: &str) -> bool {
        let mut changed = false;
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if let Ok(value) = serde_json::from_str::<serde_json::Value>(line) {
                changed |= self.push_event(&value);
            }
        }
        self.trim();
        changed
    }

    /// `bytes` 중 **소비해도 안전한** 앞부분의 길이.
    ///
    /// 마지막 줄에 개행이 없으면 두 가지 중 하나다: 쓰는 중이라 반쪽이거나, 그냥
    /// 개행 없이 끝난 온전한 줄이거나. 구분하지 않고 소비하면 반쪽 줄을 파싱 실패로
    /// 버린 뒤 **영영 다시 안 읽어** 그 이벤트를 잃고, 반대로 항상 미루면 개행으로
    /// 안 끝나는 파일의 마지막 항목이 영영 안 보인다. JSONL 이므로 **온전한 JSON 이면
    /// 완결된 줄**이라는 판정이 성립한다(반쪽 줄은 거의 확실히 파싱에 실패한다).
    pub(crate) fn consumable(bytes: &[u8]) -> usize {
        let end = bytes.iter().rposition(|b| *b == b'\n').map_or(0, |i| i + 1);
        let tail = std::str::from_utf8(&bytes[end..]).map(str::trim).unwrap_or("");
        if !tail.is_empty() && serde_json::from_str::<serde_json::Value>(tail).is_ok() {
            bytes.len()
        } else {
            end
        }
    }

    /// 파일에서 읽는다. 못 읽으면 `None`(트랜스크립트가 없는 것은 정상이다).
    pub fn read(path: &Path) -> Option<Self> {
        std::fs::read_to_string(path).ok().map(|t| Self::parse(&t))
    }

    pub fn items(&self) -> &[Item] {
        &self.items
    }

    /// 지금 걸려 있는 권한 모드(`plan`·`acceptEdits`·`auto`·`default` …). 모르면 `None`.
    ///
    /// **값을 해석하지 않고 그대로 준다.** 이 목록은 Claude Code 쪽 사정으로 늘어나는데
    /// (실측 코퍼스에는 `auto` 863 · `default` 34 · `acceptEdits` 5 가 있었고 `plan` 은
    /// 0건이었다), 아는 값만 통과시키면 모르는 모드에서 화면이 **조용히 옛 모드를**
    /// 보여 준다. 표시는 뷰가 정한다.
    pub fn mode(&self) -> Option<&str> {
        self.mode.as_deref()
    }

    /// 가장 최근 플랜 항목(전문 포함). 전용 화면이 읽는다.
    pub fn last_plan(&self) -> Option<&Item> {
        self.items
            .iter()
            .rev()
            .find(|i| matches!(i.kind, ItemKind::Plan { .. }))
    }

    /// 가장 최근에 **권한이 막은** 항목. 전용 화면이 읽는다.
    pub fn last_denied(&self) -> Option<&Item> {
        self.items
            .iter()
            .rev()
            .find(|i| i.state() == Some(ToolState::Denied))
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    /// 마지막 `n` 개(오래된 것 → 최근 순). 화면에 붙일 때 쓴다.
    pub fn tail(&self, n: usize) -> &[Item] {
        let start = self.items.len().saturating_sub(n);
        &self.items[start..]
    }

    fn trim(&mut self) {
        if self.items.len() > MAX_ITEMS {
            let cut = self.items.len() - MAX_ITEMS;
            self.items.drain(..cut);
            // 잘린 항목을 가리키던 대기 id 는 버린다(음수 인덱스를 만들지 않는다).
            self.pending.retain_mut(|(_, idx)| match idx.checked_sub(cut) {
                Some(new) => {
                    *idx = new;
                    true
                }
                None => false,
            });
        }
    }

    /// 화면에 보이는 것이 달라졌으면 `true`.
    fn push_event(&mut self, event: &serde_json::Value) -> bool {
        match event.get("type").and_then(|v| v.as_str()) {
            Some("assistant") => self.push_assistant(event),
            Some("user") => self.push_user(event),
            // 권한 모드는 항목이 아니라 **지금 상태**다. 마지막 값만 남긴다.
            Some("permission-mode") => {
                match event.get("permissionMode").and_then(|v| v.as_str()) {
                    Some(mode) => {
                        let next = Some(clip(mode));
                        let changed = next != self.mode;
                        self.mode = next;
                        changed
                    }
                    None => false,
                }
            }
            // 나머지 종류(mode·custom-title·file-history-* …)는 블록이 아니다.
            _ => false,
        }
    }

    fn push_assistant(&mut self, event: &serde_json::Value) -> bool {
        let Some(content) = event.pointer("/message/content").and_then(|c| c.as_array()) else {
            return false;
        };
        let before = self.items.len();
        for block in content {
            match block.get("type").and_then(|v| v.as_str()) {
                Some("text") => {
                    if let Some(text) = block.get("text").and_then(|v| v.as_str()) {
                        if let Some(title) = one_line(text) {
                            self.items.push(Item::line(ItemKind::Answer, title));
                        }
                    }
                }
                Some("tool_use") => {
                    let name = block
                        .get("name")
                        .and_then(|v| v.as_str())
                        .unwrap_or("tool")
                        .to_owned();
                    let input = block.get("input");
                    let item = if name == PLAN_TOOL {
                        plan_item(input)
                    } else {
                        Item::line(
                            ItemKind::Tool {
                                name: clip(&name),
                                state: ToolState::Running,
                            },
                            summarize_tool(&name, input),
                        )
                    };
                    self.items.push(item);
                    if let Some(id) = block.get("id").and_then(|v| v.as_str()) {
                        self.pending.push((id.to_owned(), self.items.len() - 1));
                    }
                }
                // thinking 은 싣지 않는다(모듈 문서 참조).
                _ => {}
            }
        }
        self.items.len() != before
    }

    fn push_user(&mut self, event: &serde_json::Value) -> bool {
        // 훅·요약 등이 사용자 턴으로 들어오는 경우가 있다 — 사람이 친 것이 아니다.
        if event.get("isMeta").and_then(|v| v.as_bool()) == Some(true) {
            return false;
        }
        let Some(content) = event.pointer("/message/content") else {
            return false;
        };
        let before = self.items.len();
        if let Some(text) = content.as_str() {
            if let Some(title) = one_line(text) {
                self.items.push(Item::line(ItemKind::Prompt, title));
            }
            return self.items.len() != before;
        }
        let Some(blocks) = content.as_array() else {
            return false;
        };
        let mut changed = false;
        for block in blocks {
            match block.get("type").and_then(|v| v.as_str()) {
                Some("text") => {
                    if let Some(title) = block.get("text").and_then(|v| v.as_str()).and_then(one_line)
                    {
                        self.items.push(Item::line(ItemKind::Prompt, title));
                    }
                }
                // 결과는 항목을 늘리지 않고 **기존 항목의 상태를 바꾼다** — 줄 수로는
                // 안 보이는 변화라 여기서 따로 알린다.
                Some("tool_result") => changed |= self.resolve_tool(block),
                _ => {}
            }
        }
        changed || self.items.len() != before
    }

    /// 툴 결과를 그 호출에 붙인다. 짝을 못 찾으면 버린다 — 짝 없는 결과를 새 항목으로
    /// 만들면 목록에 출처 없는 줄이 생긴다(이어받은 세션의 앞부분이 그렇다).
    fn resolve_tool(&mut self, block: &serde_json::Value) -> bool {
        let Some(id) = block.get("tool_use_id").and_then(|v| v.as_str()) else {
            return false;
        };
        let Some(pos) = self.pending.iter().position(|(pid, _)| pid == id) else {
            return false;
        };
        let (_, index) = self.pending.remove(pos);
        let text = result_text(block.get("content"));
        let denial = denial_reason(&text);
        let failed = block.get("is_error").and_then(|v| v.as_bool()) == Some(true);
        let next = match (&denial, failed) {
            (Some(_), _) => ToolState::Denied,
            (None, true) => ToolState::Failed,
            (None, false) => ToolState::Ok,
        };
        let Some(item) = self.items.get_mut(index) else {
            return false;
        };
        match &mut item.kind {
            ItemKind::Tool { state, .. } | ItemKind::Plan { state } => *state = next,
            _ => return false,
        }
        // 사유는 **거부일 때만** 붙인다 — 플랜 전문을 결과 텍스트로 덮으면 안 된다.
        if let Some(reason) = denial {
            item.detail = Some(reason);
        }
        true
    }
}

/// 플랜을 내미는 툴 이름. 값 하나지만 이름으로 못박아 둔다 — 오타는 조용히 "그냥 툴"이
/// 되어 아무도 못 알아챈다.
pub const PLAN_TOOL: &str = "ExitPlanMode";

/// `ExitPlanMode` 호출 하나를 플랜 항목으로.
///
/// **주의 — 이 모양은 로컬 코퍼스로 검증되지 않았다.** 트랜스크립트 1,843개 전수에
/// `ExitPlanMode` 가 **0건**이다(이 사용자는 플랜 모드를 안 쓴다). 그래서 입력 키가
/// `plan` 이 아니면 [`summarize_tool`] 의 폴백(첫 문자열 인자)으로 내려간다 — 모양이
/// 달라도 **빈 줄이 되지는 않게** 해 두는 것이 여기서 할 수 있는 최선이다.
fn plan_item(input: Option<&serde_json::Value>) -> Item {
    let body = input
        .and_then(|i| i.get("plan"))
        .and_then(|v| v.as_str())
        .map(str::to_owned);
    let title = match &body {
        // 플랜은 여러 줄이다. 목록에는 첫 줄과 분량만 — 전문은 `detail` 로 간다.
        Some(text) => {
            let head = one_line(text).unwrap_or_default();
            let n = text.lines().count().to_string();
            // ⚠ 사용자 유래 값(head)은 마지막 자리 — 값 안의 `{n}` 재치환 방지.
            tf("{text}  {n}줄", &[("n", &n), ("text", &head)])
        }
        None => summarize_tool(PLAN_TOOL, input),
    };
    Item {
        kind: ItemKind::Plan {
            state: ToolState::Running,
        },
        title,
        detail: body.map(|text| clip_body(&text)),
    }
}

/// 툴 결과 본문을 문자열로. 실측 코퍼스에서 `content` 는 **문자열 91,009건 · 배열
/// 4,781건**이라 두 모양을 다 받아야 한다(배열이면 `text` 조각을 잇는다).
fn result_text(content: Option<&serde_json::Value>) -> String {
    match content {
        Some(serde_json::Value::String(s)) => s.clone(),
        Some(serde_json::Value::Array(parts)) => parts
            .iter()
            .filter_map(|p| p.get("text").and_then(|v| v.as_str()))
            .collect::<Vec<_>>()
            .join(" "),
        _ => String::new(),
    }
}

/// 거부 결과면 사유를, 아니면 `None`.
fn denial_reason(text: &str) -> Option<String> {
    let text = text.trim_start();
    if !DENIAL_PREFIXES.iter().any(|p| text.starts_with(p)) {
        return None;
    }
    // 자동 모드 분류기는 `Reason:` 뒤에 진짜 이유를 적는다. 없으면 문장 자체가 이유다.
    let reason = match text.split_once("Reason:") {
        Some((_, rest)) => rest,
        None => text,
    };
    one_line(reason).or_else(|| one_line(text))
}

/// 툴 호출 한 줄 요약.
///
/// 툴마다 **무엇이 그 호출을 설명하는가**가 다르다: 셸은 명령줄, 파일 툴은 경로, 검색은
/// 패턴이다. 이름만 보이면 목록이 `Bash Bash Bash` 가 되어 아무 정보가 없다.
pub fn summarize_tool(name: &str, input: Option<&serde_json::Value>) -> String {
    let field = |key: &str| {
        input
            .and_then(|i| i.get(key))
            .and_then(|v| v.as_str())
            .map(str::to_owned)
    };
    let summary = match name {
        "Bash" | "BashOutput" => field("command").or_else(|| field("description")),
        "Read" | "NotebookEdit" => field("file_path"),
        "Write" => field("file_path").map(|p| match input.and_then(|i| i.get("content")) {
            // 쓴 분량이 그 호출의 크기다 — 경로만으로는 한 줄인지 천 줄인지 모른다.
            Some(serde_json::Value::String(c)) => {
                let n = c.lines().count().to_string();
                // ⚠ 사용자 유래 값(경로)은 마지막 자리 — 값 안의 `{n}` 재치환 방지.
                tf("{text}  {n}줄", &[("n", &n), ("text", &p)])
            }
            _ => p,
        }),
        "Edit" => field("file_path").map(|p| {
            let count = |key: &str| {
                input
                    .and_then(|i| i.get(key))
                    .and_then(|v| v.as_str())
                    .map(|s| s.lines().count())
                    .unwrap_or(0)
            };
            format!("{p}  -{}/+{}", count("old_string"), count("new_string"))
        }),
        "Grep" | "Glob" => field("pattern"),
        "Task" | "Agent" => field("description"),
        "AskUserQuestion" => input
            .and_then(|i| i.get("questions"))
            .and_then(|q| q.as_array())
            .and_then(|q| q.first())
            .and_then(|q| q.get("question"))
            .and_then(|v| v.as_str())
            .map(str::to_owned),
        "WebFetch" | "WebSearch" => field("url").or_else(|| field("query")),
        _ => None,
    };
    // 모르는 툴도 **뭔가는** 보여 준다 — 첫 문자열 인자를 쓴다. 새 툴이 생길 때마다
    // 이 표를 고쳐야만 쓸모가 있으면, 안 고친 동안 목록이 비어 보인다.
    let summary = summary.or_else(|| {
        input
            .and_then(|i| i.as_object())
            .and_then(|o| o.values().find_map(|v| v.as_str()))
            .map(str::to_owned)
    });
    summary.and_then(|s| one_line(&s)).unwrap_or_default()
}

/// 본문(플랜 전문)의 줄 수 상한. 전용 화면이라도 무한히 그릴 수는 없다 — 화면보다 길면
/// 아래가 잘릴 뿐이므로 파싱 단계에서 자른다.
pub const MAX_BODY_LINES: usize = 40;

/// 여러 줄 본문을 화면에 그려도 되는 모양으로. 줄바꿈은 남기고 나머지 제어문자는 지운다.
///
/// 줄 단위로 [`clip`] 을 통과시키므로 **한 줄이 화면을 넘기지 않고**, 이스케이프도 여기서
/// 죽는다(트랜스크립트에는 실제로 ANSI 가 들어 있다 — [`clip`] 문서 참조).
fn clip_body(text: &str) -> String {
    let mut out: Vec<String> = text.lines().take(MAX_BODY_LINES).map(clip).collect();
    if text.lines().count() > MAX_BODY_LINES {
        out.push("…".to_owned());
    }
    out.join("\n")
}

/// 여러 줄 텍스트에서 **의미 있는 첫 줄**. 빈 문자열이면 `None`.
fn one_line(text: &str) -> Option<String> {
    let line = text.lines().map(str::trim).find(|l| !l.is_empty())?;
    let clipped = clip(line);
    (!clipped.is_empty()).then_some(clipped)
}

/// 제어문자를 지우고 길이를 자른다.
///
/// 이 문자열은 그대로 화면에 그려진다. `\x1b` 가 살아 있으면 트랜스크립트에 담긴 아무
/// 바이트나 **사용자 단말에 이스케이프를 주입**할 수 있다(툴 결과에는 실제로 ANSI 가
/// 들어 있다). 블록 명령 텍스트에서와 같은 규칙이다.
fn clip(text: &str) -> String {
    let cleaned: String = text
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    let trimmed = cleaned.trim();
    match trimmed.char_indices().nth(MAX_TEXT) {
        Some((idx, _)) => format!("{}…", &trimmed[..idx]),
        None => trimmed.to_owned(),
    }
}

#[cfg(test)]
#[path = "lib_tests.rs"]
mod tests;
