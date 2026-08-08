"""서버측 명령 핸들러 — `action` → 핸들러 디스패치 테이블(`_CMD_TABLE`).

`serverio._handle_cmd` 는 페더레이션/원격 보기 **라우팅**만 맡고, 개별 명령 수행은 이
모듈의 핸들러가 맡는다(클라측 `clientcmd.py` 와 대칭). 종전엔 한 함수 안 67 분기
if/elif 체인이었다(§10-4⑨ God-함수, 검수마다 유보).

## disposition 계약 (★이 모듈의 핵심)

체인 시절 각 분기의 응답 방식은 **암묵적 제어흐름**이었다 — 본문이 `return` 하면
"핸들러가 응답 완결", 그냥 끝나면 함수 끝의 `await self._send_full(client)` 로
**폴스루**. 한 verb 만 오분류해도 명령이 **조용히** 깨진다(트리는 바뀌는데 화면 미갱신,
또는 이중 방송). 그래서 여기서는 disposition 을 제어흐름이 아니라 **테이블의 데이터**로
선언한다 — 등록 시점에 눈에 보이고, 드라이브 없이 테스트가 전수 대조할 수 있다
(`test_command_table_disposition_golden`).

- `FULL`    — 핸들러 수행 후 **요청 클라에 full 프레임 재동기**(`_send_full`). 체인의
              폴스루와 동일. 트리/레이아웃을 바꾸는 대다수 구조 명령.
- `HANDLED` — 핸들러가 응답을 **완결**(직접 회신했거나, 트리 콜백 broadcast 에 의존해
              일부러 안 보냄). 체인의 `return` 과 동일.
- `DYNAMIC` — 실행 시점에 갈리는 소수 — 핸들러가 `FULL`/`HANDLED` 를 **반환**해 결정한다
              (`kill_pane`: 죽일 패널이 있으면 트리 콜백 broadcast 에 맡기고 HANDLED,
              없으면 no-op 이라 폴스루 FULL — 체인의 조건부 `return` 등가).

핸들러 시그니처는 `async def (self, client, sess, msg)` 로 통일한다(대다수가 client 를
안 쓰지만 균일해야 테이블 디스패치가 단순하다). `self` 는 합성된 `Server` 다.
"""

from __future__ import annotations

import os
import time

FULL = "full"
HANDLED = "handled"
DYNAMIC = "dynamic"

# action -> (핸들러 함수, disposition). 클래스 본문의 @_cmd 데코레이터가 채운다.
_CMD_TABLE: dict[str, tuple] = {}


def _cmd(action: str, disp: str):
    """핸들러를 `action` 으로 등록한다(disposition 은 위 계약 참조).

    같은 action 을 두 번 등록하면 즉시 예외 — 체인 시절 뒤쪽 `elif` 가 조용히 죽던
    중복(첫 분기가 항상 이김)을 import 시점 오류로 드러낸다.
    """
    def deco(fn):
        if action in _CMD_TABLE:
            raise RuntimeError(f"명령 중복 등록: {action}")
        if disp not in (FULL, HANDLED, DYNAMIC):
            raise RuntimeError(f"알 수 없는 disposition: {action}={disp}")
        _CMD_TABLE[action] = (fn, disp)
        return fn
    return deco


def _int(v, default=0) -> int:
    """와이어에서 온 값을 int 로(신뢰 불가 입력 — bool·문자열·None·거대값 방어).

    좌표류 필드에 쓴다. 클라가 보낸 값이 그대로 인덱스 연산에 들어가므로, 형변환
    실패를 예외로 흘리지 않고 default 로 떨어뜨린다(경계에서 정규화하는 저장소 관례).
    """
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        return default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class ServerCmdMixin:
    """`_CMD_TABLE` 에 등록된 명령 핸들러 모음(`Server` 합성 믹스인)."""

    # ── 패널 ──────────────────────────────────────────────────────────────
    @_cmd("split", FULL)
    async def _cmd_split(self, client, sess, msg):
        self.split_pane(sess, msg.get("orient", "lr"), path=msg.get("path"))

    @_cmd("kill_pane", DYNAMIC)
    async def _cmd_kill_pane(self, client, sess, msg):
        pane = sess.active_window.active_pane if sess.active_window else None
        if pane:
            self.kill_pane(sess, pane)
            return HANDLED   # kill 은 트리 콜백에서 broadcast
        return FULL          # 죽일 패널 없음 → no-op 이지만 재동기

    @_cmd("select_pane", FULL)
    async def _cmd_select_pane(self, client, sess, msg):
        self.select_pane_dir(sess, msg.get("dir"))

    @_cmd("select_pane_id", FULL)
    async def _cmd_select_pane_id(self, client, sess, msg):
        win = sess.active_window
        p = win.pane_by_id(msg.get("id")) if win else None
        if p:
            win.active_pane = p

    @_cmd("cycle_pane", FULL)
    async def _cmd_cycle_pane(self, client, sess, msg):
        self.select_pane_cycle(sess)

    @_cmd("last_pane", FULL)
    async def _cmd_last_pane(self, client, sess, msg):
        self.last_pane(sess)

    @_cmd("set_sync", FULL)
    async def _cmd_set_sync(self, client, sess, msg):
        self.set_sync(sess, msg.get("value"))

    @_cmd("set_pane_title", FULL)
    async def _cmd_set_pane_title(self, client, sess, msg):
        self.set_pane_title(sess, str(msg.get("title", "")))

    @_cmd("set_border_status", FULL)
    async def _cmd_set_border_status(self, client, sess, msg):
        self.set_border_status(sess, msg.get("value"))

    @_cmd("respawn_pane", FULL)
    async def _cmd_respawn_pane(self, client, sess, msg):
        self.respawn_pane(sess)

    @_cmd("search", FULL)
    async def _cmd_search(self, client, sess, msg):
        self.search_pane(sess, msg.get("query"), msg.get("direction", "up"))

    @_cmd("search_all", HANDLED)
    async def _cmd_search_all(self, client, sess, msg):
        """전역 검색(pytmux-27 ①·②) — 회신이 **목록**이라 요청 클라에게만 보낸다.

        HANDLED 인 이유: 세션 상태를 하나도 안 바꾼다(읽기만 한다). 재동기할 캔버스가
        없고 다음 동작은 클라가 `search_goto` 로 되묻는다 — `request_buffers` 와 같은
        결이다. 회신을 브로드캐스트하지 않는 이유도 같다: 남의 화면에 남이 친 검색
        결과 판이 뜨면 안 된다.
        ⚠ HANDLED 는 원격 보기 중에도 **필수**다 — FULL 이면 검색 한 번이 보이지
        않는 로컬 화면을 그 클라에 덮어써 원격 뷰를 날린다.

        ②: 로컬을 훑은 뒤 **같은 검색을 전 상류에 중계해 합친다**
        (`remote_search_merge`). 이 핸들러는 다운스트림이 릴레이한 요청도 그대로
        받으므로(그때 `client` 는 그 페더레이션 링크다) 캐스케이드가 저절로 된다 —
        `hops` 가 고리에서 무한히 번지는 것을 막고, `_req_token` 은 물어본 쪽이
        자기 회신을 알아보는 표식이라 **그대로 돌려준다**(§4.1 릴레이 라우팅 규약.
        테이블 디스패치에는 `_dispatch_plugin_cmd` 의 echo 가 없어 여기서 한다)."""
        q = str(msg.get("query", ""))
        limit = _int(msg.get("limit"), 0)
        res = await (self.search_all_panes(sess, q, limit=limit) if limit > 0
                     else self.search_all_panes(sess, q))
        await self.remote_search_merge(sess, res, q,
                                       hops=_int(msg.get("hops"), 0))
        resp = dict(res, t="search_results", query=q)
        if msg.get("_req_token") is not None:
            resp["_req_token"] = msg["_req_token"]
        await self._send_to(client, resp)

    @_cmd("search_goto", FULL)
    async def _cmd_search_goto(self, client, sess, msg):
        """결과 한 줄이 가리키는 탭·패널·스크롤로 간다(**로컬 자리 전용**).

        FULL 인 이유: 탭 전환 + 패널 전환 + 스크롤을 한꺼번에 바꾸므로 요청 클라가
        그 자리를 곧바로 봐야 한다(`select_window` 와 같은 갈래).

        원격 자리(`route` 가 비어 있지 않은 결과 줄)는 여기까지 오지 않는다 —
        `serverio._handle_cmd` 가 앞에서 갈라 `remote_search_goto` 로 보낸다. 화면을
        상류 프레임이 그리므로 그쪽은 FULL 이면 안 되기 때문이다(pytmux-27 ②)."""
        self.search_goto(sess, wid=msg.get("wid"), win=_int(msg.get("win"), -1),
                         pane=_int(msg.get("pane"), -1),
                         line=_int(msg.get("line"), 0),
                         query=str(msg.get("query", "")))

    # ── 버퍼 / 붙여넣기 / 캡처 ────────────────────────────────────────────
    @_cmd("set_buffer", HANDLED)
    async def _cmd_set_buffer(self, client, sess, msg):
        self.set_buffer(str(msg.get("text", "")))

    @_cmd("paste_buffer", HANDLED)
    async def _cmd_paste_buffer(self, client, sess, msg):
        self.paste_buffer(sess, int(msg.get("index", 0)))

    @_cmd("paste", HANDLED)
    async def _cmd_paste(self, client, sess, msg):
        self.paste_text(sess, str(msg.get("text", "")))

    @_cmd("request_buffers", HANDLED)
    async def _cmd_request_buffers(self, client, sess, msg):
        resp = self._buffers_msg()
        # §4.1 원격 릴레이: 다운스트림이 요청 클라 식별자를 실어 왔으면 회신에 그대로
        # echo 한다 — _remote_reader 가 그 클라에게만 전달해, 같은 원격 호스트를 보는
        # 다른 뷰어에 버퍼 선택 팝업이 뜨지 않게(request_token_log 와 같은 규칙).
        if msg.get("_req_token") is not None:
            resp = dict(resp, _req_token=msg["_req_token"])
        await self._send_to(client, resp)

    @_cmd("plugin_overlay", HANDLED)
    async def _cmd_plugin_overlay(self, client, sess, msg):
        """이 클라의 **패널 오버레이 상태**를 서버에 알린다(설계 Tier B · P3 · §4.4).

        시계·달력이 어느 패널에 떠 있는지는 오늘 **클라의 것**이다(정본 `app.clock_panes`).
        서버가 같은 그림을 그리려면 그 사실을 들어야 하는데, 그건 설계 §6 이 "비용"으로
        적어 둔 per-client UI 상태다 — 그래서 세션이 아니라 **연결**에 매단다
        (`ClientConn.plugin_state`, Tier C 가 이미 쓰는 그 자리). 두 사람이 같은 세션을
        봐도 서로의 시계를 켜지 않고, 연결이 끊기면 함께 사라진다.

        회신은 없다 — 다음 프레임의 `plugin_cells` 가 곧 답이다.
        """
        name = str(msg.get("name") or "")
        pane = msg.get("pane")
        if not name or pane is None:
            return
        overlays = client.plugin_state.setdefault("overlays", {})
        on = overlays.setdefault(name, {})
        if msg.get("on"):
            # 켤 때마다 **빈 상태로 시작한다**(달력이면 이번 달). 껐다 켠 사람은 자기가
            # 언제 지난달로 갔는지 기억하지 못한다 — 정본도 같은 규칙이다.
            on[pane] = {}
        else:
            on.pop(pane, None)
        if not on:
            overlays.pop(name, None)
        # 다음 틱을 기다리지 않고 **지금** 그린다(껐을 때도: 빈 런이 지우개다).
        client._cells_at = 0.0

    @_cmd("client_fact", HANDLED)
    async def _cmd_client_fact(self, client, sess, msg):
        """이 클라만 아는 **사실**을 서버에 알린다(설계 Tier D · §4.4 · P7).

        서버가 대신 알 수 없는 것이 있다 — 오늘 목록은 **입력기 한/영** 하나다. OS 가
        그 상태를 클라 창에만 알려 주기 때문이고, 그래서 `ime-indicator` 는 그 사실을
        클라에서 얻는다. 하지만 **그릴지·어디에·무슨 색으로는 플러그인이 정한다**
        (Tier B) — 그래야 규칙이 한 벌로 남는다.

        오버레이와 같은 자리(`ClientConn.plugin_state`)에 매단다: per-client 이고,
        연결이 끊기면 함께 사라진다. 값이 비면 **지운다** — 끄는 것도 사실이다.

        ⚠ **와이어 모양이 설계 스케치와 다르다.** 스케치는
        `{"t":"client_fact","ime":"ko"}` 였는데 그러면 **플러그인 이름이 프로토콜에
        박힌다** — P8 이 `overlay_style::{clock_digit,calendar}` 를 걷어낸 것과 같은
        빚(INV5)이다. 이미 자리잡은 `plugin_overlay{name,…}` 와 같은 결로
        `{"name","value"}` 를 쓴다.

        회신은 없다 — 다음 프레임의 `plugin_cells` 가 곧 답이다.
        """
        name = str(msg.get("name") or "")
        if not name:
            return
        facts = client.plugin_state.setdefault("facts", {})
        value = msg.get("value")
        if value in (None, ""):
            facts.pop(name, None)
        else:
            facts[name] = str(value)[:32]   # 비신뢰 문자열 — 길이를 자른다
        if not facts:
            client.plugin_state.pop("facts", None)
        # 다음 틱을 기다리지 않고 **지금** 그린다(지웠을 때도: 빈 런이 지우개다).
        client._cells_at = 0.0

    @_cmd("plugin_overlay_action", HANDLED)
    async def _cmd_plugin_overlay_action(self, client, sess, msg):
        """오버레이의 클릭존/키가 올려 보낸 **이름**을 그 플러그인에 넘긴다(Tier B).

        클라는 `‹` 가 무슨 뜻인지 모른다 — 서버가 준 `do` 를 그대로 되돌려 줄 뿐이고,
        그것이 달을 넘기는 일인지 해를 넘기는 일인지는 플러그인이 정한다(설계 §4.4).
        회신은 없다: 다음 프레임의 `plugin_cells` 가 곧 답이다.
        """
        name = str(msg.get("name") or "")
        pane = msg.get("pane")
        state = ((client.plugin_state.get("overlays") or {})
                 .get(name) or {}).get(pane)
        if state is None:
            return          # 안 켜진 오버레이 — 늦게 온 클릭이다(조용히 버린다)
        self.plugins.plugin_overlay_action(
            self, sess, {"name": name, "pane": pane,
                         "do": msg.get("do"), "state": state})
        client._cells_at = 0.0

    @_cmd("plugin_cmd", DYNAMIC)
    async def _cmd_plugin_cmd(self, client, sess, msg):
        """플러그인 **명령 한 줄**을 실행한다 — 상태를 바꾸는 것이면 여기서 끝난다
        (pytmux-35).

        # 왜 `plugin_open` 으로는 안 됐나

        네이티브 클라는 플러그인 명령을 **전부** `plugin_open`("화면을 다오")으로 보냈다.
        화면을 여는 명령(`ncd`·`mdir`)에는 맞지만 **상태를 바꾸는 명령**에는 통째로 틀린
        길이라, 서버가 *"이 플러그인은 화면 스펙을 제공하지 않습니다"* 로 거절하고
        사용자에게는 죽은 줄로 보였다. 리포트가 *"가장 옳은 길"* 로 적은 것이 이 명령이다.

        # 못 알아들으면 **화면 경로로 넘어간다**

        한 이름이 둘 중 어느 쪽인지는 **플러그인이 안다** — 클라가 알면 그 표가 서버와
        갈리고, 갈린 순간 명령은 조용히 죽는다(이 결함의 원인 그대로). 그래서 클라는
        고른 이름을 그냥 보내고, 서버가 순서대로 시도한다:

        1. `plugin_command_action` — 플러그인이 이름을 **액션과 인자로 옮긴다**.
        2. 그 액션을 평소 길로 디스패치한다: **코어 표 먼저**, 없으면 플러그인
           `server_command`.
        3. 그래도 아니면 `plugin_open` 과 **같은 길** — 화면 스펙을 묻는다(없으면
           그쪽이 알림으로 알린다. 조용한 누락은 상습 결함이다 · 설계 §8-5).

        ⚠ 2번의 **차례가 규칙이다**. 플러그인 훅에만 물으면 코어 표가 받는 액션이 죽는다 —
        `set_claude_account` 의 주인은 `_CMD_TABLE` 이고 플러그인 `server_command` 에는
        없다(2026-08-03 에 그렇게 만들었다가 disposition 골든이 잡았다).
        """
        name = str(msg.get("name", ""))
        args = [a for a in (msg.get("args") or []) if a != ""]
        got = self.plugins.plugin_command_action(name, args)
        if got is not None:
            action, kw = got
            entry = _CMD_TABLE.get(action)
            if entry is not None:
                handler, disp = entry
                r = await handler(self, client, sess, kw)
                return r if disp == DYNAMIC else disp
            disp = self.plugins.server_command(self, client, sess, action, kw)
            if disp is not None:
                if disp == "broadcast":
                    self._broadcast_session(sess)
                    return FULL
                return FULL if disp == "send_full" else HANDLED
        await self._plugin_screen_reply(client, sess, {
            "do": "open", "name": name, "args": args,
        })
        return HANDLED

    @_cmd("plugin_open", HANDLED)
    async def _cmd_plugin_open(self, client, sess, msg):
        """플러그인 **화면**을 연다(설계 Tier C · P4).

        정본 클라는 플러그인이 준 Textual 화면을 자기 프로세스에서 띄운다. 네이티브
        클라는 파이썬을 못 읽으므로 **무엇을 그릴지**를 서버가 스펙으로 준다 —
        플러그인 코드는 한 벌로 남고, 클라는 목록/글 두 모양만 그릴 줄 알면 된다.

        회신이 없으면(그 이름을 아무 플러그인도 안 집으면) **조용히 끝내지 않는다**:
        알림 한 줄을 보낸다. 조용한 누락이 이 저장소의 상습 결함이다(설계 §8-5).
        """
        await self._plugin_screen_reply(client, sess, {
            "do": "open",
            "name": str(msg.get("name", "")),
            "args": list(msg.get("args") or []),
        })

    @_cmd("plugin_action", HANDLED)
    async def _cmd_plugin_action(self, client, sess, msg):
        """플러그인 화면에서 **고른 줄과 누른 키**를 그대로 되돌려준다(설계 §4.3).

        행동은 서버(=플러그인)가 정한다 — 클라는 "몇 번째 줄에서 무슨 키"만 말한다.
        그래서 플러그인이 화면 흐름을 바꿔도 클라를 안 고친다.

        ⚠ 액션 이름의 칸은 `do` 다. `action` 은 **명령 디스패처의 것**이라(이 프레임의
        `action` 은 `plugin_action` 이다) 같은 이름을 쓰면 서로 덮는다.
        """
        await self._plugin_screen_reply(client, sess, {
            "id": str(msg.get("id", "")),
            "do": str(msg.get("do", "")),
            "row": msg.get("row"),
            "input": msg.get("input"),
        })

    async def _plugin_screen_reply(self, client, sess, req):
        """플러그인에 화면 요청을 넘기고 그 회신을 요청 클라에게만 보낸다.

        `req["state"]` 는 **이 클라의** 화면 상태 보관함이다(설계 Tier C · P5): `ncd` 의
        지금 디렉터리, `mdir` 의 커서·태그처럼 "그 사람이 보고 있는 판"의 것이 여기 산다.
        서버 전역에 두면 두 클라가 같은 화면을 열었을 때 서로의 커서를 옮기고, 연결이
        끊겨도 남는다 — `ClientConn` 에 매달아 **수명을 연결에 묶는다**.

        `handle_server_request` 와 같은 규약을 쓴다 — awaitable 을 돌려주면 여기서
        기다린다(플러그인의 p4/파일시스템 작업은 executor 로 나간다). 요청 클라에게만
        보내는 이유: 이 화면은 **그 클라의 것**이다(다른 뷰어에 남의 팝업이 뜨면 안 된다).
        """
        import inspect
        req = dict(req, state=getattr(client, "plugin_state", None))
        if req["state"] is None:
            # 옛 판 클라 객체(테스트 스텁 등) — 상태 없는 화면은 그대로 돈다.
            req["state"] = {}
        resp = self.plugins.plugin_screen(self, sess, req)
        if inspect.isawaitable(resp):
            resp = await resp
        if not isinstance(resp, dict):
            # 아무도 안 집었다 — 사용자에겐 "눌렀는데 아무 일도 안 남"으로 보인다.
            name = req.get("name") or req.get("id") or ""
            await self._send_to(client, {
                "t": "notice", "sev": "warn",
                "key": "msg.plugin_screen_missing", "kw": {"name": name},
                "text": f"{name}: 이 플러그인은 화면 스펙을 제공하지 않습니다",
            })
            return
        await self._send_to(client, resp)

    @_cmd("clear_history", HANDLED)
    async def _cmd_clear_history(self, client, sess, msg):
        self.clear_history(sess)
        await self._send_full(client)

    @_cmd("capture_pane", HANDLED)
    async def _cmd_capture_pane(self, client, sess, msg):
        n = self.capture_pane(sess, bool(msg.get("full")))
        await self._send_to(client, {"t": "captured", "chars": n})

    # 마우스 드래그 선택 텍스트 요청 — 좌표는 **절대 행 인덱스**(screen 메시지의 top
    # 과 같은 좌표계). 클라는 뷰포트 셀만 갖고 있어 한 화면을 넘는 선택을 스스로 뽑을
    # 수 없으므로, 스크롤백을 가진 서버가 추출해 회신한다(요청 2026-07-25: 드래그 중
    # 휠로 스크롤해도 선택 유지·복사).
    _COPY_RANGE_MAX = 4_000_000     # 회신 상한(바이트급) — 프레임/클립보드 폭주 방지

    @_cmd("copy_range", HANDLED)
    async def _cmd_copy_range(self, client, sess, msg):
        win = sess.active_window if sess else None
        p = (win.pane_by_id(msg.get("pane")) or win.active_pane) if win else None
        if p is None and sess is not None and sess.popup:
            p = sess.popup.get("pane")      # 팝업 패널(트리 밖)도 선택 대상이 된다
        text = ""
        if p is not None:
            try:
                text = p.extract_range(_int(msg.get("y0")), _int(msg.get("x0")),
                                       _int(msg.get("y1")), _int(msg.get("x1")))
            except Exception:
                self._log_error("copy_range", f"pane={msg.get('pane')}")
                text = ""
        if len(text) > self._COPY_RANGE_MAX:
            text = text[:self._COPY_RANGE_MAX]
        await self._send_to(client, {"t": "selection", "text": text})

    @_cmd("pipe_pane", HANDLED)
    async def _cmd_pipe_pane(self, client, sess, msg):
        self.pipe_pane(sess, str(msg.get("cmd", "")))

    # ── 팝업 / 레이아웃 영속 ─────────────────────────────────────────────
    @_cmd("popup_open", HANDLED)     # popup_open 이 broadcast
    async def _cmd_popup_open(self, client, sess, msg):
        self.popup_open(sess, str(msg.get("cmd", "")),
                        want_w=msg.get("w"), want_h=msg.get("h"),
                        title=msg.get("title"))

    @_cmd("popup_close", HANDLED)    # popup_close 가 broadcast
    async def _cmd_popup_close(self, client, sess, msg):
        self.popup_close(sess)

    @_cmd("save_layout", HANDLED)
    async def _cmd_save_layout(self, client, sess, msg):
        ok = self.save_layout()
        await self._send_to(client, {"t": "captured",
                                     "chars": 1 if ok else 0})

    @_cmd("restore_layout", HANDLED)
    async def _cmd_restore_layout(self, client, sess, msg):
        self.restore_layout()
        await self._send_full(client)

    @_cmd("list_layouts", HANDLED)
    async def _cmd_list_layouts(self, client, sess, msg):
        await self._send_to(client, {"t": "layouts",
                                     "names": self.list_tab_layouts()})

    @_cmd("save_tab_layout", HANDLED)
    async def _cmd_save_tab_layout(self, client, sess, msg):
        ok = self.save_tab_layout(sess, str(msg.get("name", "")).strip())
        await self._send_to(client, {"t": "captured",
                                     "chars": 1 if ok else 0})

    @_cmd("load_tab_layout", HANDLED)
    async def _cmd_load_tab_layout(self, client, sess, msg):
        if self.load_tab_layout(sess, str(msg.get("name", "")).strip(),
                                new_tab=bool(msg.get("new"))):
            for c in [x for x in self.clients if x.session is sess]:
                await self._send_full(c)

    # ── 조회 요청(회신 타입 고정) ────────────────────────────────────────
    @_cmd("request_tree", HANDLED)
    async def _cmd_request_tree(self, client, sess, msg):
        await self._send_to(client, self._tree_msg())

    @_cmd("request_redraw", HANDLED)
    async def _cmd_request_redraw(self, client, sess, msg):
        # 화면 전체 강제 재그리기(§2.12, redraw/refresh 명령·prefix r). ① 각 패널
        # PTY 에 SIGWINCH 를 유발해 alt-screen 앱이 현재 화면을 전체 repaint 하게
        # 하고(스냅샷 갱신) ② 요청 클라에 layout+screen 전체 프레임을 다시 보낸다
        # (stale 스냅샷 교체). 원격 보기 중엔 _handle_cmd 의 릴레이 라우팅이 먼저
        # 잡아 업스트림으로 릴레이하므로 여기 로컬 경로엔 오지 않는다.
        self._induce_redraw_all()
        await self._send_full(client)

    @_cmd("request_version", HANDLED)
    async def _cmd_request_version(self, client, sess, msg):
        # version 명령 팝업: 이 서버가 로드한 코드 버전(p4 CL)·업타임·pid 회신.
        # 클라가 자기 버전/업타임과 합쳐 팝업을 띄운다.
        await self._send_to(client, {
            "t": "version", "version": self._code_version,
            "uptime": time.time() - self._boot_time, "pid": os.getpid()})

    @_cmd("request_restart_check", HANDLED)
    async def _cmd_request_restart_check(self, client, sess, msg):
        # restart-check 드라이런: 작업 보존 재시작 안전성 점검 결과 회신(부작용 없음).
        rep = self.restart_check()
        rep["t"] = "restart_check"
        await self._send_to(client, rep)

    @_cmd("set_claude_account", HANDLED)
    async def _cmd_set_claude_account(self, client, sess, msg):
        self.set_claude_account(sess, str(msg.get("name", "")))

    # ── 크기 / 탭 ────────────────────────────────────────────────────────
    @_cmd("resize", FULL)
    async def _cmd_resize(self, client, sess, msg):
        self.resize_split(sess, msg.get("split_id"), msg.get("ratio", 0.5))

    @_cmd("resize_dir", FULL)
    async def _cmd_resize_dir(self, client, sess, msg):
        self.resize_dir(sess, msg.get("dir"), msg.get("cells", 3))

    @_cmd("new_window", FULL)
    async def _cmd_new_window(self, client, sess, msg):
        self.new_window(sess, path=msg.get("path"))

    @_cmd("next_window", FULL)
    async def _cmd_next_window(self, client, sess, msg):
        self.select_window(sess, (sess.active_index + 1) % len(sess.tabs))

    @_cmd("prev_window", FULL)
    async def _cmd_prev_window(self, client, sess, msg):
        self.select_window(sess, (sess.active_index - 1) % len(sess.tabs))

    @_cmd("select_window", FULL)
    async def _cmd_select_window(self, client, sess, msg):
        # 원격(병합 전역) index 진입·로컬 복귀 라우팅은 _handle_cmd 프롤로그가 이미
        # 처리했다 — 여기 오는 건 로컬 탭 선택뿐.
        idx = msg.get("index", 0)
        # wid(Tab 의 안정 id)가 실려 있으면 그걸로 재확인한다: 클라가 번호→index 를
        # 계산한 시점과 이 커맨드가 여기서 처리되는 시점 사이 다른 클라이언트의 탭
        # 생성/삭제/이동으로 sess.tabs 가 _reindex 돼 있으면, 그 옛 index 는 이제
        # 다른 탭을 가리킬 수 있다(제보: ESC+6 눌렀는데 간헐적으로 7번 탭이 열림 —
        # index 는 위치값이라 _reindex 마다 재할당되는데, ESC+숫자 경로는 그 위치값을
        # 사람이 번호를 고르는 동안(레이스 구간) 그대로 들고 있었다). wid 로 같은 탭을
        # 다시 찾아 그 자리의 **현재** index 를 쓴다 — 그 사이 탭이 닫혔으면(못 찾으면)
        # 클라가 계산 당시 보냈던 index 로 폴백(구버전 클라 호환도 겸함).
        if wid := msg.get("wid"):
            for i, t in enumerate(sess.tabs):
                if getattr(t, "wid", None) == wid:
                    idx = i
                    break
        self.select_window(sess, idx)

    @_cmd("last_window", FULL)
    async def _cmd_last_window(self, client, sess, msg):
        self.last_window(sess)

    @_cmd("move_window", FULL)
    async def _cmd_move_window(self, client, sess, msg):
        self.move_window(sess, int(msg.get("index", 0)))

    @_cmd("swap_window", FULL)
    async def _cmd_swap_window(self, client, sess, msg):
        self.swap_window(sess, int(msg.get("index", 0)))

    @_cmd("move_tab", FULL)
    async def _cmd_move_tab(self, client, sess, msg):
        self.move_tab(sess, int(msg.get("index", 0)),
                      int(msg.get("to", 0)))

    @_cmd("move_current_tab", FULL)
    async def _cmd_move_current_tab(self, client, sess, msg):
        self.move_current_tab(sess, str(msg.get("where", "")))

    @_cmd("set_pinned", FULL)
    async def _cmd_set_pinned(self, client, sess, msg):
        # 항목7: 탭 고정/해제. index 없으면 활성 탭. value 미지정이면 토글.
        idx = msg.get("index")
        idx = sess.active_index if idx is None else int(idx)
        if idx >= len(sess.tabs):
            # §12 ①: 원격(병합) 탭 핀 — per-link 다운스트림 로컬 집합(업스트림
            # 비전파). 핀은 보는 쪽 탭바 레이아웃 문제라 로컬에서만 토글한다.
            self.set_remote_pinned(sess, idx, msg.get("value"))
        elif "value" in msg:
            self.set_pinned(sess, idx, bool(msg.get("value")))
        else:
            self.toggle_pin(sess, idx)

    # ── 배치(arrange) ────────────────────────────────────────────────────
    @_cmd("zoom", FULL)
    async def _cmd_zoom(self, client, sess, msg):
        self.toggle_zoom(sess)

    @_cmd("select_layout", FULL)
    async def _cmd_select_layout(self, client, sess, msg):
        self.select_layout(sess, msg.get("preset", "tiled"))

    @_cmd("cycle_layout", FULL)
    async def _cmd_cycle_layout(self, client, sess, msg):
        self.cycle_layout(sess)

    @_cmd("rotate", FULL)
    async def _cmd_rotate(self, client, sess, msg):
        self.rotate_panes(sess, bool(msg.get("forward", True)))

    @_cmd("swap_pane", FULL)
    async def _cmd_swap_pane(self, client, sess, msg):
        self.swap_pane(sess, bool(msg.get("forward", True)))

    @_cmd("swap_pane_to", FULL)
    async def _cmd_swap_pane_to(self, client, sess, msg):
        self.swap_pane_ids(sess, int(msg.get("id", -1)),
                           int(msg.get("to_id", -1)))

    @_cmd("break_pane", FULL)
    async def _cmd_break_pane(self, client, sess, msg):
        self.break_pane(sess)

    @_cmd("join_pane", FULL)
    async def _cmd_join_pane(self, client, sess, msg):
        # src(끌어온 탭 인덱스) 지정 가능(#19 탭→패널 드래그). 미지정이면 직전 탭.
        self.join_pane(sess, src_index=msg.get("src"),
                       orient=msg.get("orient", "tb"))

    @_cmd("move_pane_to_tab", FULL)
    async def _cmd_move_pane_to_tab(self, client, sess, msg):
        # 헤더 드래그 pick-up → 다른 탭에 드롭(#1): id 패널을 to 탭으로 옮긴다.
        self.move_pane_to_tab(sess, int(msg.get("id", -1)),
                              int(msg.get("to", -1)))

    # ── 이름 / 모니터 / 옵션 ─────────────────────────────────────────────
    @_cmd("rename_window", FULL)
    async def _cmd_rename_window(self, client, sess, msg):
        self.rename_window(sess, str(msg.get("name", "")).strip())

    @_cmd("set_auto_rename", FULL)
    async def _cmd_set_auto_rename(self, client, sess, msg):
        self.set_auto_rename(sess, msg.get("value"))

    @_cmd("set_monitor", FULL)
    async def _cmd_set_monitor(self, client, sess, msg):
        self.set_monitor(sess, msg.get("which", "activity"), msg.get("value"))

    @_cmd("set_single_border", FULL)
    async def _cmd_set_single_border(self, client, sess, msg):
        self.set_single_border(msg.get("value"))

    @_cmd("set_window_size", FULL)
    async def _cmd_set_window_size(self, client, sess, msg):
        # 세션 공유 격자 크기 규칙(smallest|latest|largest, tmux window-size 동형).
        # value=None 이면 순환 토글. 공유 크기가 바뀌므로 같은 세션 전 클라를 새
        # 규칙으로 다시 미러링해 즉시 발효(작은 코-뷰어는 latest/largest 에서 crop).
        # 요청 클라는 이 루프 + 테이블 FULL 로 두 번 받지만(체인 시절과 동일) 무해.
        self.set_window_size(msg.get("value"))
        for c in [x for x in self.clients if x.session is sess]:
            try:
                await self._send_full(c)
            except Exception:
                self._log_error("send_full(set_window_size)")

    @_cmd("set_win_mouse_motion", FULL)
    async def _cmd_set_win_mouse_motion(self, client, sess, msg):
        # Windows any-motion 패스스루 토글(HANDOFF §10-H). 광고 mouse 레벨이
        # 바뀌므로 레이아웃을 다시 방송해 즉시 발효시킨다.
        self.set_win_mouse_motion(msg.get("value"))
        self._broadcast_session(sess)

    @_cmd("set_coalesce", FULL)
    async def _cmd_set_coalesce(self, client, sess, msg):
        self.set_coalesce_repaints(msg.get("value"))

    @_cmd("set_nest_auto_attach", FULL)
    async def _cmd_set_nest_auto_attach(self, client, sess, msg):
        # 원격 중첩 자동 승격 토글(NESTED_ATTACH ㉢) — 서버 내부 동작이라 클라
        # 렌더 변화 없음. value=None 이면 반전(클라 toggle).
        self.set_nest_auto_attach(msg.get("value"))

    @_cmd("set_exit_empty", FULL)
    async def _cmd_set_exit_empty(self, client, sess, msg):
        # tmux exit-empty 동형 토글(§10-10) — 서버 내부 동작이라 클라 렌더 변화
        # 없음. value=None 이면 반전(클라 toggle).
        self.set_exit_empty(msg.get("value"))

    @_cmd("set_vt_parser", FULL)
    async def _cmd_set_vt_parser(self, client, sess, msg):
        # VT 파서 백엔드("pyte"|"native") 선택. 재시작 시 발효(라이브 패널 즉시
        # 변화 없음) — 서버가 opts.json 영속. 클라가 발효 시점을 안내한다.
        self.set_vt_parser(msg.get("value"))

    @_cmd("set_plugin_enabled", HANDLED)
    async def _cmd_set_plugin_enabled(self, client, sess, msg):
        # 플러그인 관리 팝업 토글(PLUGIN_MANAGER_SCENARIO). disabled 갱신·영속 후
        # 전 클라에 새 status(disabled_plugins) 방송 — 각 클라가 자기 레지스트리에
        # 반영해 명령/훅이 즉시 빠지거나 돌아온다.
        self.set_plugin_enabled(str(msg.get("name", "")), msg.get("on"))
        self._broadcast_session(sess)
        await self._send_full(client)

    # ── 윈도우 / 세션 종료 ───────────────────────────────────────────────
    @_cmd("kill_window", HANDLED)
    async def _cmd_kill_window(self, client, sess, msg):
        self.kill_window(sess)
        if sess.name not in self.sessions:
            # 세션의 마지막 윈도우였음 → 다른 세션으로 옮기거나 종료
            if self.sessions:
                client.session = next(iter(self.sessions.values()))
                await self._send_full(client)
            elif self.exit_empty:
                self._notify_no_sessions()
            # exit_empty=off: 서버 유지(§10-10). client.session 은 이미 _drop_session
            # 이 None 으로 재배정했다 — 다음 new_window 릴레이가 새 세션을 만든다
            # (serverio._handle_cmd).
            return
        await self._send_full(client)

    @_cmd("rename_session", FULL)
    async def _cmd_rename_session(self, client, sess, msg):
        self.rename_session(sess, str(msg.get("name", "")).strip())

    @_cmd("new_session", HANDLED)
    async def _cmd_new_session(self, client, sess, msg):
        new = self.new_session(client.cols, client.rows,
                               str(msg.get("name", "")).strip() or None)
        client.session = new
        await self._send_full(client)

    @_cmd("switch_session", HANDLED)
    async def _cmd_switch_session(self, client, sess, msg):
        self.switch_session(client, str(msg.get("name", "")).strip())
        await self._send_full(client)

    @_cmd("detach_others", HANDLED)
    async def _cmd_detach_others(self, client, sess, msg):
        for c in list(self.clients):
            if c is not client and c.session is sess:
                await self._send_to(c, {"t": "bye"})

    @_cmd("kill_session", HANDLED)
    async def _cmd_kill_session(self, client, sess, msg):
        name = str(msg.get("name") or sess.name)
        self.kill_session(name)
        if not self.sessions:
            if self.exit_empty:
                self._notify_no_sessions()
            # exit_empty=off: 서버 유지(§10-10) — 위 kill_session→_drop_session 이
            # 이미 관련 클라를 session=None 으로 재배정했다.
            return
        for c in self.clients:
            await self._send_full(c)

    @_cmd("kill_server", HANDLED)
    async def _cmd_kill_server(self, client, sess, msg):
        self._notify_no_sessions()

    @_cmd("restart_server", HANDLED)
    async def _cmd_restart_server(self, client, sess, msg):
        # 작업 보존 재시작(re-exec). 셸/PTY 보존(docs/internal/RESTART_SCENARIO.md).
        self.restart_server()
