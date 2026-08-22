//! 교차구현 적합성 — 클라가 부르는 명령 이름이 서버 표에 실제로 있는가.
//!
//! 이름이 어긋나도 **아무 소리가 안 난다.** `serverio._handle_cmd` 는 `_CMD_TABLE` 에 없는
//! action 을 플러그인 훅으로 넘기고, 아무도 안 집으면 조용히 끝난다 — 예외도 로그도 없고
//! 사용자에게는 "키가 안 먹는다"로만 보인다. 그래서 이 대조가 유일한 방어다.
//!
//! 픽스처는 서버 구현에서 뽑았다: `python3 scripts/gen_command_fixture.py`
//! (출처 = `pytmuxlib/servercmd.py` 의 `_CMD_TABLE`).
//!
//! # 한 방향만 본다
//!
//! 표에는 71개가 있고 클라는 그중 일부만 쓴다. "클라가 부르는 이름이 표에 있는가"만
//! 단언한다 — 클라가 안 쓰는 명령이 표에서 사라지는 것은 이 클라의 결함이 아니다.
//! 그래도 픽스처에는 표 전체를 적어 둔다. 서버가 이름을 바꾸면 **픽스처 diff 에 보인다.**

use std::collections::BTreeMap;

use proto::Command;
use serde::Deserialize;

#[derive(Deserialize)]
struct Fixture {
    dispositions: BTreeMap<String, String>,
    payload_keys: BTreeMap<String, Vec<String>>,
}

fn fixture() -> Fixture {
    serde_json::from_str(include_str!("fixtures/commands.json")).expect("픽스처를 읽을 수 없다")
}

#[test]
fn every_command_the_client_sends_exists_in_the_server_table() {
    let table = fixture().dispositions;
    assert!(!table.is_empty(), "픽스처가 비었다");

    let missing: Vec<&str> = Command::all()
        .iter()
        .map(|cmd| cmd.action())
        .filter(|action| !table.contains_key(*action))
        .collect();

    assert!(
        missing.is_empty(),
        "servercmd.py 의 _CMD_TABLE 에 없는 명령을 보내고 있다: {missing:?}\n\
         서버는 이걸 플러그인 훅으로 넘기고 조용히 끝낸다 — 오류도 로그도 없다.\n\
         표가 바뀐 것이라면 python3 scripts/gen_command_fixture.py 로 픽스처를 다시 뽑을 것."
    );
}

/// 우리가 싣는 **칸 이름**이 서버가 실제로 읽는 칸인가.
///
/// # 이 게이트가 없어서 벌어진 일 (2026-07-29)
///
/// 우리는 `split` 에 `{"horizontal": true}` 를 실어 보내고 있었다. 서버는
/// `msg.get("orient", "lr")` 를 읽는다 — 못 찾으니 **늘 기본값 `lr`** 로 떨어졌고,
/// 그래서 `prefix "` 도 메뉴도 팔레트도 **G1 이래 전부 좌우 분할**이었다.
///
/// 이름 대조(`every_command_the_client_sends_exists_in_the_server_table`)는 조용했다 —
/// 이름은 `split` 로 맞았기 때문이다. 1413개 테스트도 전부 초록이었다. **라이브
/// 스크린샷이 잡았다**(클라 p4 68374).
///
/// # 한 방향만 본다
///
/// "우리가 싣는 칸이 서버가 읽는 칸에 있는가"만 단언한다. 반대(서버가 읽는데 우리가
/// 안 싣는 칸)는 결함이 아니다 — 대부분 기본값이 있고, 우리가 안 쓰는 기능이다.
#[test]
fn every_field_we_send_is_a_field_the_server_reads() {
    let known = fixture().payload_keys;
    assert!(!known.is_empty(), "픽스처에 페이로드 칸이 없다 — 빈 결과는 통과가 아니다");

    let mut wrong: Vec<String> = Vec::new();
    for cmd in Command::all() {
        let action = cmd.action();
        let frame = cmd.to_frame();
        let Some(fields) = known.get(action) else {
            wrong.push(format!("{action}: 픽스처에 이 명령의 칸 목록이 없다"));
            continue;
        };
        let Some(object) = frame.as_object() else {
            continue;
        };
        for key in object.keys() {
            // 봉투는 페이로드가 아니다 — 프레이밍이 싣는 칸이다.
            if key == "t" || key == "action" {
                continue;
            }
            if !fields.iter().any(|f| f == key) {
                wrong.push(format!(
                    "{action}: `{key}` 를 싣는데 서버는 안 읽는다 (읽는 칸: {fields:?})"
                ));
            }
        }
    }

    assert!(
        wrong.is_empty(),
        "서버가 안 읽는 칸을 싣고 있다 — **조용히 기본값으로 떨어진다**:
  {}
         서버가 칸 이름을 바꾼 것이라면 python3 scripts/gen_command_fixture.py 로          픽스처를 다시 뽑을 것.",
        wrong.join("
  ")
    );
}

/// 클라가 낙관적 갱신을 안 해도 되는 근거를 고정한다.
///
/// `command.rs` 모듈 주석은 "명령을 보내면 서버가 full 프레임으로 재동기해 주므로 클라가
/// 로컬 상태를 미리 고칠 필요가 없다"를 전제로 한다. 그 전제는 disposition 이 `full` 일
/// 때만 성립한다 — `handled`/`dynamic` 은 트리 콜백 broadcast 에 기댄다.
///
/// 서버가 조용히 `full` → `handled` 로 바꾸면 증상은 "명령은 먹었는데 화면이 안 바뀐다"고,
/// 이름 대조만으로는 안 잡힌다. 그래서 예외를 **이름으로 못박는다** — 예외가 늘면 여기서
/// 실패하고, 늘어난 명령의 화면 갱신 경로를 사람이 확인하게 된다.
#[test]
fn only_the_two_known_commands_skip_the_full_resync() {
    let table = fixture().dispositions;

    let mut exceptions: Vec<(&str, &str)> = Command::all()
        .iter()
        .map(|cmd| cmd.action())
        .filter_map(|action| {
            let disp = table.get(action)?;
            (disp != "full").then_some((action, disp.as_str()))
        })
        .collect();
    exceptions.sort_unstable();

    assert_eq!(
        exceptions,
        vec![
            // 회신이 **화면이 아니라 텍스트**다(`{"t":"selection"}`). 선택은 서버 상태를
            // 바꾸지 않으므로 재동기할 것도 없다 — 클라도 화면 갱신을 기다리지 않는다.
            // 회신이 **화면이 아니라 캡처한 글자 수**다(`{"t":"captured"}`) — 버퍼에
            // 담을 뿐이라 재동기할 화면이 없다.
            ("capture_pane", "handled"),
            // 핸들러가 **자기 손으로** full 을 보낸다(`_cmd_clear_history`) — 표의
            // FULL 을 안 쓸 뿐이지 전체 재동기는 온다.
            ("clear_history", "handled"),
            // Tier D — 클라만 아는 사실을 올린다. 회신이 없고 답은 다음 셀 프레임이다.
            ("client_fact", "handled"),
            ("copy_range", "handled"),
            // 패널을 실제로 죽였을 때만 broadcast 에 맡기고 HANDLED 를 반환한다.
            // 죽일 패널이 없으면 no-op 이라 FULL 로 떨어진다.
            ("kill_pane", "dynamic"),
            // `_remove_pane_from_tree` 의 트리 콜백 broadcast 가 화면을 갱신한다.
            // 핸들러는 full 프레임을 안 보낸다(덧붙이면 이중 방송).
            // 서버가 **사라진다** — 재동기할 상대가 없다. 화면 갱신은 소켓이 끊기면서
            // 오는 종료 경로가 한다(클라가 "서버 종료됨"으로 닫힌다).
            ("kill_server", "handled"),
            ("kill_window", "handled"),
            // 핸들러가 **자기 손으로** full 을 보낸다(`_cmd_load_tab_layout` ·
            // `_cmd_restore_layout`) — 표의 FULL 을 안 쓸 뿐이지 전체 재동기는 온다.
            ("load_tab_layout", "handled"),
            // 붙여넣기는 PTY 에 쓰는 것이라 화면 갱신 경로가 **명령 응답이 아니다** —
            // 자식이 그 바이트를 받아 출력을 내고, 그 출력이 평소의 dirty→screen 방송을
            // 태운다(키 입력과 같은 경로). full 재동기를 붙이면 오히려 자식이 아직
            // 아무것도 안 그린 시점의 화면을 한 번 더 보내는 셈이다.
            ("paste", "handled"),
            // 붙여넣기와 같은 자리다 — 서버가 PTY 에 쓰고, 자식이 낸 출력이 평소의
            // dirty→screen 방송을 탄다. 명령 응답으로 화면을 보내면 자식이 아직
            // 아무것도 안 그린 시점의 화면을 한 번 더 보내는 셈이다.
            ("paste_buffer", "handled"),
            // 페더레이션 셋은 **표에 없다** — `serverio._handle_cmd` 가 표를 보기 전에
            // 직접 처리하고 돌아간다(그래서 픽스처 생성기가 소스에서 따로 긁는다).
            // 화면은 업스트림에서 오는 status/full 이 그린다.
            // 서버 안에서 외부 명령에 파이프를 걸 뿐이라 화면이 안 바뀐다.
            ("pipe_pane", "handled"),
            // 회신이 **화면 스펙**이다(`{"t":"plugin_screen"}` — 설계 Tier C · P4).
            // 세션 상태를 안 바꾸므로 재동기할 캔버스가 없다. 우리는 그 스펙을 판으로
            // 그리고, 다음 동작은 다시 `plugin_action` 으로 물어본다.
            ("plugin_action", "handled"),
            ("plugin_open", "handled"),
            // Tier B/D 의 셋 — **회신이 아예 없다**. 답은 다음 `plugin_cells` 프레임이고
            // 그건 flush 루프가 낸다(설계 §4.4). 그래서 full 재동기가 필요 없다.
            // ⚠ 이 셋은 오래 이 목록 밖에 있었다 — `Command::all()` 에 안 들어 있어서다
            // (`VARIANT_COUNT` 가 안 따라 올라가 색인 67·68 이 검사 범위 밖이었다).
            // 2026-08-02i(P7)에서 셋을 all() 에 넣으며 여기도 함께 메웠다.
            ("plugin_overlay", "handled"),
            ("plugin_overlay_action", "handled"),
            // 핸들러가 **자기 손으로 방송**한다(서버 주석: `popup_open 이 broadcast`)
            // — 표의 FULL 을 안 쓸 뿐이지 화면은 온다.
            ("popup_close", "handled"),
            ("popup_open", "handled"),
            ("remote_attach", "handled"),
            ("remote_detach", "handled"),
            ("remote_new_window", "handled"),
            // 회신이 **화면이 아니라 목록**이다(`{"t":"buffers"}`) — 서버 상태를 안
            // 바꾸므로 재동기할 것도 없다. 목록 화면이 그 회신을 그린다.
            ("request_buffers", "handled"),
            // 핸들러가 **자기 손으로** `_send_full(client)` 을 부른다(`_cmd_request_redraw`)
            // — 표의 FULL 을 안 쓸 뿐이지 전체 재동기는 온다. 이 명령의 존재 이유가
            // 바로 그 full 이라, 여기가 `handled` 가 아니게 되면 그때 확인할 일은
            // "그래도 full 이 오는가"다.
            ("request_redraw", "handled"),
            // 회신이 **화면이 아니라 점검 결과**다(`{"t":"restart_check"}`) — 부작용도
            // 없다(드라이런이다).
            ("request_restart_check", "handled"),
            // 위 `request_buffers` 와 같다 — 회신은 `{"t":"tree"}` 개요다.
            ("request_tree", "handled"),
            // 서버가 자기를 re-exec 한다 — 화면은 **재접속 뒤 새 서버의 첫 full** 로
            // 돌아온다. 죽기 직전의 full 을 보내는 것은 뜻이 없다.
            // 회신이 **화면이 아니라 버전 한 줄**이다(`{"t":"version"}`).
            ("request_version", "handled"),
            ("restart_server", "handled"),
            // 회신이 **화면이 아니라 결과 숫자**다(`{"t":"captured"}`)이거나 핸들러가
            // 자기 손으로 full 을 보낸다 — 어느 쪽이든 표의 FULL 은 안 쓴다.
            ("restore_layout", "handled"),
            // 페이스트 버퍼는 **화면에 없다**. 서버 안의 목록 하나를 늘릴 뿐이라
            // 재동기할 화면이 없다.
            ("save_layout", "handled"),
            ("save_tab_layout", "handled"),
            // 회신이 **화면이 아니라 결과 목록**이다(`{"t":"search_results"}`) — 요청
            // 클라에게만 가고 세션 상태를 안 바꾼다(pytmux-27). 결과 판이 그 목록을
            // 그리고, 고른 항목은 `search_goto`(FULL)로 다시 물어 점프한다.
            ("search_all", "handled"),
            ("set_buffer", "handled"),
            // 핸들러가 **새 status 를 전 클라에 방송**한다(`_cmd_set_plugin_enabled`) —
            // 화면 갱신은 그 status 가 한다. 관리 화면은 그래서 낙관적으로 안 고친다.
            ("set_plugin_enabled", "handled"),
        ],
        "full 재동기를 안 받는 명령의 목록이 달라졌다 — 늘었다면 그 명령을 보낸 뒤 \
         화면이 실제로 갱신되는지(broadcast 경로가 있는지) 확인할 것"
    );
}
