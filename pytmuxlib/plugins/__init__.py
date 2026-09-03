"""선택적 플러그인 로더/레지스트리.

`pytmuxlib/plugins/` 하위의 각 **서브패키지**(디렉토리+__init__.py)를 불러와
`PLUGIN` 객체를 모은다. 플러그인은 명령(이름·설명·카테고리)·자동완성·무인자 표시·
명령 디스패치·메시지 처리(클라)·요청 처리(서버)를 기여한다.

핵심 계약: **디렉토리를 통째로 지우면 그 기능은 조용히 사라진다.** 코어(client/
server)는 플러그인을 직접 import 하지 않고 오직 이 레지스트리를 통해서만 호출하므로,
디렉토리가 없으면 명령 검색·자동완성·디스패치 어디에도 나타나지 않는다(에러 없이).

무게 주의: 이 모듈과 각 플러그인의 `__init__` 은 **textual 을 import 하지 않는다** —
서버 프로세스도 `load()` 로 같은 코드를 읽기 때문이다. 화면 등 무거운 의존은 플러그인
안에서 실제로 필요할 때 지연 import 한다."""
from __future__ import annotations

import importlib
import pkgutil


def onoff(args):
    """`on`/`off` 낱말 → `True`/`False`, 없으면 `None`(**서버가 토글한다**).

    # 왜 레지스트리에 있나 (pytmux-35)

    플러그인마다 `cmdmap` 이 생기면서 같은 네 줄이 여러 벌 생길 참이었다. 이 함수가
    갈리면 — 한쪽만 `toggle` 을 받거나 한쪽만 대소문자를 무시하면 — **같은 명령이 클라
    마다 다르게 동작한다.** 그것이 이 결함(pytmux-35)이 생긴 모양 그대로다.

    플러그인이 코어를 읽는 것은 이 저장소의 관례다(`i18n`·`proc`). 반대 방향만 금지다 —
    코어는 플러그인을 직접 import 하지 않는다(delete-to-disable).
    """
    if "on" in args:
        return True
    if "off" in args:
        return False
    return None


def _discover():
    """plugins/ 하위 서브패키지를 불러와 `PLUGIN` 객체 목록을 만든다. import 가
    깨진 플러그인은 조용히 건너뛴다(하나가 망가져도 앱 전체를 막지 않게)."""
    found = []
    for info in pkgutil.iter_modules(__path__):
        if not info.ispkg:
            continue
        try:
            mod = importlib.import_module(f"{__name__}.{info.name}")
        except Exception:
            continue
        plugin = getattr(mod, "PLUGIN", None)
        if plugin is not None:
            found.append(plugin)
    return found


class Registry:
    """불러온 플러그인들의 기여를 코어가 쓰기 좋은 형태로 모아 준다. 모든 플러그인
    멤버는 선택적(덕 타이핑) — 없으면 빈 값으로 취급한다."""

    def __init__(self, plugins):
        self._all = list(plugins)        # 발견된 전체(팝업 표시·재활성용)
        self.disabled = set()            # 비활성 플러그인 이름(런타임 토글, opts 영속)
        self.plugins = list(self._all)   # 활성 부분집합 — 모든 훅 디스패치/명령 프로퍼티가
        #                                  이걸 순회하므로, 비활성은 자동으로 빠진다.

    def set_disabled(self, names):
        """비활성 플러그인 이름 집합을 적용한다(플러그인 관리 팝업·opts 영속). self.plugins
        를 활성 부분집합으로 다시 만들어, **모든** 훅 디스패치·명령/자동완성/메뉴 프로퍼티가
        비활성 플러그인을 건너뛴다(코어 변경 없이 일괄). 서버 믹스인 메서드는 import 시
        이미 합성됐으면 Server 에 남지만, 그 동작을 구동하는 훅(server_scan/server_pty_output
        등)이 안 불려 무동작이 된다(런타임 비활성). 디렉토리 삭제(delete-to-disable)와 달리
        가역적이다. docs/internal/PLUGIN_MANAGER_SCENARIO.md."""
        self.disabled = set(names or ())
        self.plugins = [p for p in self._all
                        if getattr(p, "name", "") not in self.disabled]

    def default_disabled(self):
        """`default_enabled = False` 를 선언한 플러그인 이름 집합 — 사용자 설정(opts 의
        disabled_plugins 키)이 **없을 때**의 초기 비활성 집합(깃헙 배포 기본 OFF). 현재
        이를 선언한 플러그인은 없어 빈 집합이다(rec 도 발견성 위해 default_enabled=True)."""
        return {getattr(p, "name", "") for p in self._all
                if not getattr(p, "default_enabled", True)}

    def plugin_overview(self):
        """플러그인 관리 팝업·진단용: 발견된 전체 플러그인의 (name, description, category,
        enabled) 목록. enabled = 이름이 disabled 집합에 없음."""
        return [(getattr(p, "name", "?"),
                 getattr(p, "description", "") or "",
                 getattr(p, "category", "") or "",
                 getattr(p, "name", "") not in self.disabled)
                for p in self._all]

    # ---- 명령 메타데이터(코어 COMMANDS/COMPLETIONS/COMMAND_NOARG 에 합쳐짐) ----
    @property
    def commands(self):
        out = []
        for p in self.plugins:
            out.extend(getattr(p, "commands", None) or [])
        return out

    @property
    def noarg(self):
        out = set()
        for p in self.plugins:
            out |= set(getattr(p, "noarg", None) or ())
        return out

    @property
    def command_options(self):
        out = {}
        for p in self.plugins:
            out.update(getattr(p, "command_options", None) or {})
        return out

    def command_option_current(self, app, name):
        """토글/선택지 명령의 현재 설정값(예: 'on'/'off')을 플러그인에서 조회한다 —
        선택지 팝업이 첫 항목 대신 현재 상태에 커서를 올리는 데 쓴다. 각 플러그인의
        command_option_current(app, name) 를 순회해 첫 비-None 을 채택, 없으면 None."""
        for p in self.plugins:
            fn = getattr(p, "command_option_current", None)
            if fn is None:
                continue
            try:
                v = fn(app, name)
            except Exception:
                v = None
            if v is not None:
                return v
        return None

    @property
    def completions(self):
        out = []
        for p in self.plugins:
            out.extend(getattr(p, "completions", None) or [])
            # 명령 이름도 자동완성 후보로(코어가 COMMANDS 로부터 하던 것과 동일).
            out.extend(n for (n, *_rest) in (getattr(p, "commands", None) or []))
        return out

    @property
    def pane_scoped(self):
        """활성 패널에 적용되는 플러그인 명령 이름 집합(코어 PANE_SCOPED_CMDS 에 합쳐짐).
        명령 프롬프트에서 이 명령을 작성 중이면 대상(활성) 패널을 밝게 표시한다."""
        out = set()
        for p in self.plugins:
            out |= set(getattr(p, "pane_scoped", None) or ())
        return out

    @property
    def menu_items(self):
        """우클릭 컨텍스트 메뉴에 합쳐질 플러그인 항목 [(key, 라벨)] (§2.7). key 는
        **그 플러그인의 명령 이름** — 코어 _run_menu_action 이 자기 키가 아니면
        `_run_command(key)` 로 폴백 디스패치하므로 별도 배선이 필요 없다. 디렉토리를
        지우면 메뉴 항목과 명령 디스패치가 함께 사라진다(delete-to-disable)."""
        out = []
        for p in self.plugins:
            out.extend(getattr(p, "menu_items", None) or [])
        return out

    # ---- 클라이언트 훅 ----
    def attach_client(self, app):
        """앱 인스턴스마다 1회 — 플러그인이 인스턴스 글루(예: app.request_nc_list)를
        설치하게 한다."""
        for p in self.plugins:
            fn = getattr(p, "attach_client", None)
            if fn is not None:
                fn(app)

    def client_unload(self, app):
        """클라이언트 종료(on_unmount) 시 1회 — attach_client 의 짝. 플러그인이 띄운
        자식 프로세스/타이머 등 인스턴스 자원을 정리하게 한다(ime-indicator 의 입력소스
        감시 헬퍼 종료 등). 부재 시 no-op(delete-to-disable). 종료 경로라 어떤 플러그인의
        실패도 다른 플러그인 정리를 막지 않게 개별 보호한다."""
        for p in self.plugins:
            fn = getattr(p, "client_unload", None)
            if fn is not None:
                try:
                    fn(app)
                except Exception:
                    pass

    def handle_command(self, app, c, args):
        """명령 프롬프트의 명령 `c` 를 처리한 플러그인이 있으면 True."""
        for p in self.plugins:
            fn = getattr(p, "handle_command", None)
            if fn is not None and fn(app, c, args):
                return True
        return False

    def handle_message(self, app, msg):
        """서버 메시지(t)를 처리한 플러그인이 있으면 True."""
        for p in self.plugins:
            fn = getattr(p, "handle_message", None)
            if fn is not None and fn(app, msg):
                return True
        return False

    def pane_osc(self, pane, code, param):
        """패널이 타이틀 밖의 OSC 를 받았다(셸 통합 = OSC 133 시맨틱 프롬프트, OSC 7 cwd).

        코어는 해석하지 않고 그대로 넘긴다 — 무엇을 할지는 플러그인이 정한다.
        `Pane.osc_handler` 로 꽂히며(서버가 패널 생성 시), 플러그인 디렉토리를 지우면
        이 순회가 비어 아무 일도 일어나지 않는다."""
        for p in self.plugins:
            fn = getattr(p, "pane_osc", None)
            if fn is not None:
                fn(pane, code, param)

    def _blocks_sources(self):
        """블록 경계를 기여하는 플러그인을 **구체적인 것부터**.

        # 왜 순서를 못 박나 (pytmux-21)

        한 패널에 출처가 둘일 수 있다: 셸 통합(OSC 133 · `plugins/blocks`)과 Claude 의
        프롬프트 마커(`plugins/claude-code`). Claude 패널에서 사용자가 고르려는 것은
        **턴**이지 그 패널을 띄운 `claude` 명령 하나가 아니다.

        ⛔ **디렉터리 이름의 사전순은 결정이 아니다.** 종전 이 자리는 발견 순서대로
        첫 비-None 을 채택했는데, 그 순서는 `pkgutil` 이 정하는 것이라 `blocks` 가
        `claude-code` 를 이겨 Claude 패널에는 셸 블록(대개 `claude` 한 덩이)만 갔다.
        그래서 `blocks_rank`(클수록 먼저 · 기본 0)를 선언하게 했다 — 값이 코드에 있으면
        읽는 사람이 왜 그런지 물어볼 수 있다.
        """
        return sorted(self.plugins,
                      key=lambda p: -int(getattr(p, "blocks_rank", 0) or 0))

    def pane_blocks(self, pane):
        """패널의 **현재** 블록 목록(와이어 형태). 없으면 None.

        dirty 와 무관하다 — 새로 붙는 클라는 바뀐 적이 없어도 현재 목록을 받아야 한다
        (화면을 `_send_full` 로 받는 것과 같은 이유)."""
        for p in self._blocks_sources():
            fn = getattr(p, "blocks_wire", None)
            if fn is None:
                continue
            payload = fn(pane)
            if payload is not None:
                return payload
        return None

    def pane_cwd(self, pane):
        """이 패널 셸의 작업 디렉터리. 아무도 모르면 None.

        패널 글 안의 **상대경로를 푸는 기준**이다(§10-21ⓧ2 / pytmux-24). 값의 출처가
        셸이 보낸 OSC 7 이라 프로브가 0 이다 — 서버의 `_pane_cwd(pane)` 는 pid 로
        /proc·PEB·lsof 를 뒤지는 **동기** 경로라 레이아웃마다 부를 수 없다.

        `pane_blocks` 와 갈라 둔 이유는 크기다: 값은 문자열 하나인데 블록 목록은 최대
        500개다. 경로만 풀면 되는 클라에게 그 목록을 통째로 보내는 것은 caps 게이트가
        막으려던 바로 그 비용이다.

        플러그인 디렉토리를 지우면 None 이 되고, 그러면 두 클라 다 상대경로를 못 풀어
        **존을 안 만든다**(밑줄을 그어 놓고 눌러도 아무 일이 없으면 그 밑줄이 거짓말이다).
        """
        for p in self.plugins:
            fn = getattr(p, "pane_cwd", None)
            if fn is None:
                continue
            cwd = fn(pane)
            if cwd:
                return cwd
        return None

    def pane_claude_tail(self, server, pane, force=False):
        """이 패널의 Claude 트랜스크립트 **꼬리 원문**(JSONL). 보낼 것이 없으면 None.

        `blocks` 와 달리 dirty 훅이 따로 없다 — 트랜스크립트는 우리가 쓰는 파일이 아니라
        **Claude 가 밖에서 덧붙이는 파일**이라 알려 주는 이벤트가 없다. 그래서 바뀜
        판정(크기·mtime)도 상한도 플러그인 안에 있다(`claude-code/clienttail.py`).
        `force` 는 새로 붙은 클라에게 현재 상태를 보낼 때다 — 그 클라는 "바뀐 적이
        없다"는 이유로 빈 화면을 봐서는 안 된다.

        플러그인 디렉토리를 지우면 이 훅이 사라져 프레임이 한 바이트도 안 나간다.
        """
        for p in self.plugins:
            fn = getattr(p, "claude_tail", None)
            if fn is None:
                continue
            payload = fn(server, pane, force=force)
            if payload is not None:
                return payload
        return None

    def upstream_caps(self):
        """이 서버가 **업스트림에 붙을 때**(페더레이션) 광고할 능력 목록.

        다운스트림 서버는 업스트림에게 그냥 클라이언트 하나다 — 클라가 hello 에
        `caps` 를 싣는 것과 같은 자리다. 플러그인이 기여하므로 디렉토리를 지우면
        목록이 비고, 업스트림은 그 기능의 프레임을 한 바이트도 안 보낸다.
        정렬해 돌려준다: 링크 hello 가 실행마다 달라지면 진단이 어려워진다.
        """
        out = set()
        for p in self.plugins:
            caps = getattr(p, "upstream_caps", None)
            if caps:
                out.update(caps)
        return sorted(out)

    def pane_blocks_changed(self, pane):
        """마지막으로 물어본 뒤 블록이 바뀌었나. 물어보면 표식이 내려간다.

        flush 가 **매 프레임 같은 목록을 다시 보내지 않게** 하는 게이트다."""
        changed = False
        for p in self.plugins:
            dirty = getattr(p, "blocks_dirty", None)
            if dirty is not None and dirty(pane):
                changed = True
                p.clear_blocks_dirty(pane)
        return changed

    # ---- 서버 훅 ----
    def server_mixins(self):
        """플러그인이 기여하는 **서버측 믹스인 클래스** 목록. `server.Server` 가 이들을
        자신의 동적 베이스로 합성한다(plugins/claude-code 의 ServerClaudeMixin 등).
        플러그인이 `server_mixin()`(지연 import 콜러블)을 노출하면 그 반환 클래스를
        모은다. 디렉토리를 지우면 목록이 비어 해당 서버 로직이 Server 에서 빠진다."""
        out = []
        for p in self.plugins:
            fn = getattr(p, "server_mixin", None)
            if fn is not None:
                cls = fn()
                if cls is not None:
                    out.append(cls)
        return out

    def server_init(self, server):
        """`Server.__init__` 마지막에 1회 — 플러그인이 서버측 런타임 상태를 설치한다
        (pane_init 의 서버 버전). claude-code 가 토큰 DB 연결 상태를 여기서
        설치한다(S5 토큰 모듈화 T2 — 코어 server.__init__ 에서 이전). 플러그인이 없으면
        no-op → 코어 server 에 그 상태가 안 생기고, 읽는 코드(서버 믹스인)도 함께 사라져
        깨지지 않는다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "server_init", None)
            if fn is not None:
                fn(server)

    def server_opts_init(self, server, opts):
        """`Server.__init__` 에서 opts.json 로드 직후 1회 — 플러그인이 자기 소유 설정을
        opts dict 에서 읽어 server 속성으로 설치한다(S5 토큰 모듈화 T3). claude-code 가
        usage_gate_* 등을 plugin_opts 네임스페이스에서 읽는다(구 top-level 키 하위호환).
        플러그인이 없으면 no-op → 코어 server 엔 그 설정이 안 생기고, 읽는 코드(플러그인)도
        함께 사라진다(delete-to-disable). 코어는 키의 의미를 모른다."""
        for p in self.plugins:
            fn = getattr(p, "server_opts_init", None)
            if fn is not None:
                fn(server, opts)

    def server_opts_serialize(self, server) -> dict:
        """`_save_opts` 가 opts.json 직렬화 시 1회 — 플러그인 소유 설정을 한 dict 로 모아
        코어가 `plugin_opts` 키 밑에 불투명하게 저장한다(코어는 키 의미 모름). claude-code
        가 usage_gate_* 등을 돌려준다. 플러그인이 없으면 {} → opts.json 에 plugin_opts 가
        비어 그 설정이 통째로 사라진다(delete-to-disable)."""
        out = {}
        for p in self.plugins:
            fn = getattr(p, "server_opts_serialize", None)
            if fn is not None:
                out.update(fn(server) or {})
        return out

    def handle_server_request(self, server, sess, action, msg):
        """서버의 알 수 없는 action 을 플러그인에 넘긴다. 회신 dict(클라로 보낼
        메시지)를 반환한 첫 플러그인의 값을 쓰고, 없으면 None."""
        for p in self.plugins:
            fn = getattr(p, "handle_server_request", None)
            if fn is not None:
                resp = fn(server, sess, action, msg)
                if resp is not None:
                    return resp
        return None

    def plugin_command_action(self, name, args):
        """플러그인 **명령 이름 + 인자** → `(서버 액션, 인자 dict)`(첫 비-None 채택).

        # 왜 이 훅이 필요한가

        네이티브 클라는 플러그인 명령을 오래 `plugin_open`("화면을 다오")으로만 보냈다.
        화면을 여는 명령에는 맞지만 **상태를 바꾸는 명령**에는 통째로 틀린 길이고, 그래서
        팔레트에 보이는데 눌러도 안 먹는 줄이 열여덟 있었다(pytmux-35).

        걸림돌은 "이름을 어떤 액션·어떤 인자로 옮기는가"가 **정본 클라 안에** 있었다는
        것이다 — 액션 이름도, 인자 칸 이름도(액션마다 다르다), 3-state 파싱도. 그것을
        네이티브 클라가 따로 알게 하면 두 표가 갈리고, 갈린 순간 명령은 **조용히 아무
        일도 안 한다**(죽은 명령이 생긴 원인 그대로). 그래서 규칙은 플러그인 안에
        한 벌로 두고 **서버가 그것을 쓴다**.

        # 왜 **실행이 아니라 해석**인가 (2026-08-03 에 한 번 틀렸다)

        처음에는 이 훅이 명령을 곧바로 실행하게 했다(플러그인의 `server_command` 를
        직접 불렀다). 그러면 **코어 명령표가 받는 액션이 죽는다** — `set_claude_account`
        가 그렇다: 그 액션의 주인은 `servercmd._CMD_TABLE` 이고 플러그인
        `server_command` 에는 없다. 훅에만 물으면 "서버가 안 받는다"로 보이고, 실제로
        그렇게 오판해 그 명령을 죽은 목록에 넣을 뻔했다.

        그래서 훅은 **옮기기만** 한다. 어느 표가 그 액션을 받는지는 서버가 안다
        (코어 표 → 플러그인 `server_command` 순). 플러그인이 알아야 하는 것은 이름과
        인자의 모양뿐이다.

        `args` 는 낱말 목록이다(정본 `handle_command` 가 받는 그것).
        `None` 은 *"내 것이 아니다"* — 서버가 화면 스펙 경로로 넘어간다.

        플러그인이 없으면 언제나 None → 그 명령은 종전처럼 화면 스펙 경로로 간다
        (delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "plugin_command_action", None)
            if fn is not None:
                r = fn(name, args)
                if r is not None:
                    return r
        return None

    def plugin_screen(self, server, sess, req):
        """Tier C — **선언형 화면 스펙**(설계 PLUGIN_COMPAT_TEXTUAL_GUI §4.3 · P4).

        네이티브 클라(pytmux-gui)는 파이썬을 못 읽어 플러그인의 Textual 화면을 띄울 수
        없다. 그래서 플러그인이 **무엇을 그릴지**를 자료로 돌려주고, 클라는 정해진 몇
        모양만 그릴 줄 알면 된다 — 플러그인 코드는 한 벌로 남는다.

        `req` 는 두 모양뿐이다:
        - 열기 — `{"do": "open", "name": <명령 이름>, "args": [...]}`
        - 화면 안 동작 — `{"id": <화면 id>, "do": <액션 이름>, "row": n, "input": …}`

        돌려줄 것(첫 플러그인의 값을 쓴다 · 내 것이 아니면 `None`):
        - `{"t": "plugin_screen", "id", "kind", "title", "hint", …}`
          ⛔ **`kind` 목록을 여기 다시 적지 않는다** — 종전에 적혀 있던
          `"list"|"text"` 는 실측 여섯일 때까지도 그대로여서, 글로 적힌 계약이 **코드가
          내는 값과 갈린 채** 오래 남았다(`client/scripts/gen_plugin_screens.py` 머리말).
          지금 무엇이 나가는지는 그 생성기가 소스에서 전수로 세어 픽스처에 적고,
          어휘의 뜻은 설계 §4.3 이 쥔다.
        - `{"t": "plugin_screen_close", "id"}`
        - `{"t": "plugin_reopen", "name": <다른 화면 이름>}` — **남의 화면으로 넘긴다**
          (아래 `_reopen`). mdir 의 `F10`(트리 → ncd)이 그 첫 자리다.
        - awaitable(느린 일은 executor 로 — `handle_server_request` 와 같은 규약)

        **정본 클라는 이 훅을 안 쓴다**(자기 프로세스에서 화면을 직접 띄운다). 그래서
        이 훅이 없는 플러그인은 네이티브 클라에서만 "화면 없음"이고, 그 사실이 알림으로
        보인다(설계 §8-5 — 조용히 버리지 않는다).
        """
        for p in self.plugins:
            fn = getattr(p, "plugin_screen", None)
            if fn is not None:
                resp = fn(server, sess, req)
                if resp is not None:
                    if isinstance(resp, dict) and resp.get("t") == "plugin_reopen":
                        return self._reopen(server, sess, req, resp)
                    return resp
        return None

    def _reopen(self, server, sess, req, ask):
        """한 화면이 **남의 화면**으로 넘긴다(pytmux-125 — mdir 의 `F10` = ncd 트리).

        # 왜 서버가 넘기나 (클라가 `plugin_open` 을 보내는 길이 아니라)

        설계 §4.3 이 정한 계약이 *"행동은 서버(=플러그인)가 정하고 클라는 몇 번째
        줄에서 무슨 키인지만 말한다"* 다. 키에 화면 **이름**을 실어 보내면 그 순간
        클라가 *"이 값은 액션인가 화면인가"* 를 가르게 되고, 플러그인이 흐름을 바꿀
        때마다 클라를 고쳐야 한다. 그래서 키는 여전히 액션 하나로 나가고, 그것을
        받은 플러그인이 여기에 대고 *"저 화면을 내 대신 열어 달라"* 고 말한다.

        # 왜 플러그인끼리 직접 안 부르나

        `import` 로 이으면 **delete-to-disable** 이 깨진다 — ncd 디렉터리를 지우는
        순간 mdir 이 통째로 죽는다. 정본도 같은 이유로 이름으로만 잇는다(mdir 의
        `getattr(self.app, "request_nc_list", None)`). 여기서도 넘기는 것은 **이름
        하나**이고, 아무도 그 이름을 안 집으면 정본과 같은 결과가 된다: 아무 일도
        안 일어나되 **조용하지는 않다**(설계 §8-5).

        ⛔ 한 번만 넘긴다 — 넘겨받은 화면이 또 넘기면 두 플러그인이 서로를 부르며
        영영 도는 자리가 생긴다(그 순간 서버는 단일 루프라 통째로 멎는다).
        """
        name = str(ask.get("name") or "")
        opened = {"do": "open", "name": name,
                  "args": list(ask.get("args") or []),
                  "state": req.get("state")}
        for p in self.plugins:
            fn = getattr(p, "plugin_screen", None)
            if fn is None:
                continue
            resp = fn(server, sess, opened)
            if resp is not None and not (
                    isinstance(resp, dict) and resp.get("t") == "plugin_reopen"):
                return resp
        # 아무도 안 집었다 — **왜 아무 일도 안 나는지**를 그 자리에서 말한다. 위로
        # `None` 을 올리면 서버의 알림이 *부른 쪽*(mdir)의 이름을 대서, 없는 것이
        # ncd 인데 mdir 을 탓하는 문장이 된다.
        return {"t": "notice", "sev": "warn",
                "key": "msg.plugin_screen_missing", "kw": {"name": name},
                "text": f"{name}: 이 플러그인은 화면 스펙을 제공하지 않습니다"}

    def plugin_cells(self, server, sess, req) -> list:
        """Tier B — **셀 기여**(설계 §4.2 · P3).

        플러그인이 `cells` 에 직접 쓰던 것을 **런 목록**으로 뽑는다. 정본은 자기
        프로세스에서 `client_overlay` 로 그리지만, 네이티브 클라는 파이썬을 못 읽어
        같은 그림을 못 낸다 — 그래서 서버가 **무엇을 어디에 쓸지**를 자료로 준다.

        `req` 는 이 클라의 화면 사정이다:
        `{"panes": [{"id","x","y","w","h"}],
          "overlays": {"<이름>": {패널 id: {…상태…}}}, "cols", "rows"}` —
        `overlays` 는 그 클라가 켜 둔 것이다(설계 §4.4 의 `client_fact`: 오버레이가
        켜졌다는 **사실**은 클라만 알고, 그릴지·어떻게는 플러그인이 정한다).

        패널마다 딸린 dict 는 **그 오버레이의 per-client 상태**다(달력의 `offset`).
        켜져 있다는 것과 그 상태는 한 자료구조에 있다 — 둘로 나누면 한쪽만 지워진
        채 남는다.

        돌려줄 것 — 런 목록. 각 런:
        `{"x","y","text","style": {…}}` (+ 선택 `"layer"`: `content`|`overlay`,
        `"theme"`: 의미 색 이름)

        - 스타일은 **이미 있는 표현**을 쓴다(`model._style_key` 가 내는 축약 키 —
          서버가 화면 런에 쓰는 것과 같다). 새 색 표기를 만들지 않는다.
        - **색의 권위는 클라 테마**다. `theme` 가 있으면 클라가 자기 테마에서 그 이름을
          풀어 `style.f` 를 덮는다 — 여기에 hex 를 실으면 서버가 UI 를 알게 된다
          (설계 §10 위험표의 그 줄).

        플러그인이 하나도 안 내면 빈 목록 → 서버는 프레임을 안 만든다(delete-to-disable).
        """
        runs = []
        for p in self.plugins:
            fn = getattr(p, "plugin_cells", None)
            if fn is None:
                continue
            got = fn(server, sess, req)
            if got:
                runs.extend(got)
        return runs

    def plugin_native(self, server, sess, req) -> dict:
        """Tier D **탈출구** — 「이 오버레이는 내가 그린다」고 광고한 클라에게는 런 대신
        **상태**를 준다(설계 §4.3·§5 · pytmux-458).

        # 왜 필요한가

        [`plugin_cells`] 는 그림을 **격자 글자**로 나른다. 정본은 격자 안에 살아서 그것이
        곧 화면이지만, GUI 는 캔버스 위에 그릴 수 있다 — 큰 시계를 글자로 흉내내는 대신
        벡터로 그리는 것이 [[pytmux-185]] 의 허용 갈림 ⓑ(픽셀 단위 그림)다.

        ⛔ **표현만 클라가 가져간다.** 뜻·상태·입구는 서버 것 그대로다 — 클라가 자기
        상태 사본을 만들면 둘로 갈라진 채 한쪽만 지워진다(플러그인 설계 §4.2). 그래서
        여기서 주는 것은 **그릴 재료**이지 「무엇을 할지」가 아니고, `dim` 도 종전대로
        서버가 정한다.

        ⛔ **플러그인 이름을 프로토콜에 박지 않는다.** 돌려주는 것은 오버레이 이름을 키로
        든 dict 이고, **그 이름은 레지스트리가 찍는다**(`plugin_triggers` 와 같은 규약) —
        서버는 그 안의 뜻을 모른다.

        `req["native"]` 가 참일 때만 불린다. 같은 플러그인의 [`plugin_cells`] 는 그때
        **빈 목록**을 돌려줘야 한다 — 안 그러면 격자 글자와 네이티브 그림이 겹친다.

        돌려줄 것 — `{패널 id: {…상태…}}`(없으면 빈 dict).
        """
        out = {}
        for p in self.plugins:
            fn = getattr(p, "plugin_native", None)
            if fn is None:
                continue
            got = fn(server, sess, req)
            if got:
                out[p.name] = got
        return out

    def plugin_triggers(self, server, sess, req) -> dict:
        """오버레이가 **되돌려 받고 싶은 것** — 클릭존과 키 표(설계 Tier B).

        런은 그림뿐이라 "이 화살표를 누르면 무슨 일이 나는가"를 못 나른다. 정본은
        자기가 그린 자리를 아니까 스스로 알지만, 네이티브 클라는 그림을 받기만 한다 —
        그래서 자리(`zones`)와 키(`keys`)에 **뜻 대신 이름**(`do`)을 붙여 준다. 클라는
        그 이름이 무슨 뜻인지 모른 채 `plugin_overlay_action` 으로 돌려보내고, 뜻은
        플러그인이 정한다(설계 §4.4 — 행동은 서버가 정한다).

        돌려줄 것: `{"zones": [{"x","y","w","h","pane","do"}],
                     "keys": [{"key","pane","do"}]}`(둘 다 선택).

        오버레이 **이름은 여기서 찍는다** — 플러그인이 자기 이름을 항목마다 다시 적을
        이유가 없고, 클라는 그 이름을 그대로 되돌려 보내야 서버가 어느 오버레이의
        상태를 고칠지 안다.
        """
        zones, keys = [], []
        for p in self.plugins:
            fn = getattr(p, "plugin_triggers", None)
            if fn is None:
                continue
            got = fn(server, sess, req) or {}
            for item in (got.get("zones") or []):
                zones.append({**item, "name": p.name})
            for item in (got.get("keys") or []):
                keys.append({**item, "name": p.name})
        return {"zones": zones, "keys": keys}

    def plugin_overlay_action(self, server, sess, req) -> bool:
        """클라가 돌려보낸 이름(`do`)을 그 오버레이의 상태에 적용한다.

        `req` = `{"name", "pane", "do", "state"}` — `state` 는 **그 클라의**
        `plugin_state["overlays"][name][pane]` 그 자체다(플러그인이 제자리에서 고친다).
        내 것이면 True 를 돌려 다음 플러그인이 같은 이름을 또 해석하지 않게 한다."""
        for p in self.plugins:
            fn = getattr(p, "plugin_overlay_action", None)
            if fn is not None and fn(server, sess, req):
                return True
        return False

    def plugin_dim_panes(self, server, sess, req) -> list:
        """오버레이가 **뒤를 흐리게** 할 패널 id 들(설계 §4.2 의 `layer` 와 같은 부류).

        런으로는 못 나르는 것이라 따로 둔다 — 딤은 새 글자를 얹는 것이 아니라 **이미
        있는 셀을 바꾸는** 일이고, 그 계산(실색 블렌드·이모지 placeholder)은 화면을
        들고 있는 클라만 할 수 있다. 서버는 "어느 패널이 덮였나"만 말한다."""
        out = []
        for p in self.plugins:
            fn = getattr(p, "plugin_dim_panes", None)
            if fn is None:
                continue
            got = fn(server, sess, req)
            if got:
                out.extend(got)
        return out

    def plugin_badges(self, server, sess, msg) -> list:
        """Tier B — **상태줄 기여**(설계 §1.2 의 ③ · P6).

        상태줄에 붙는 **표식 한 칸**을 자료로 준다. 셀 기여(`plugin_cells`)와 같은
        발상이고 어휘도 같다 — 다른 것은 자리를 플러그인이 안 정한다는 점뿐이다.
        오버레이는 "어느 칸에" 가 뜻의 일부지만, 상태줄 표식은 **줄 안의 순서**만
        있으면 되고 그 순서는 클라의 상태줄 규칙이 정한다(정본과 GUI 의 배지 줄
        생김새가 서로 다르다 — 같은 자리를 강요하면 한쪽이 망가진다).

        `msg` 는 지금 만들고 있는 status 메시지다(읽기 전용으로 본다) — 플러그인이
        이미 `server_status` 로 채워 넣은 자기 필드를 그대로 다시 읽으면 되므로,
        같은 값을 두 번 계산하지 않는다.

        돌려줄 것 — 배지 목록. 각 배지:
        `{"text": " REC ", "style": {…}, "theme": {…}}` (+ 레지스트리가 `"name"` 을 찍는다)

        - 스타일은 **이미 있는 표현**(`model._style_key` 축약)이고, 색은 `theme` 의
          **의미 이름**이다 — 서버가 hex 를 실으면 서버가 UI 를 알게 된다(설계 §10).
        - **누르는 자리는 아직 없다**. 정본의 REC 배지는 클릭하면 캡처 정보 팝업이
          뜨는데 그 화면은 Tier C(④)이고 네이티브에는 아직 없다 — 여기에 `do` 를
          실어 두면 **선언은 있고 배선이 없는** 칸이 하나 더 생긴다(08-02b). 화면이
          오면 그때 `plugin_triggers` 와 같은 표기로 넓힌다.

        플러그인이 하나도 안 내면 빈 목록 → status 에 키가 안 실린다(delete-to-disable).
        """
        out = []
        for p in self.plugins:
            fn = getattr(p, "plugin_badges", None)
            if fn is None:
                continue
            got = fn(server, sess, msg)
            for item in (got or []):
                out.append({**item, "name": p.name})
        return out

    # ---- 서버 런타임 훅(코어가 믹스인 메서드를 이름으로 직접 부르지 않게) ----
    # 코어(serverio/server)는 Claude 서버 로직(스캔/상태/입력/사용량)에 **이 훅으로만**
    # 닿는다. 플러그인이 없으면 전부 기본값(False/None/no-op)이라 서버가 그대로 동작
    # 한다(delete-to-disable). 플러그인이 있으면 동적 합성된 ServerClaudeMixin 으로
    # 위임한다(server.<method>). Claude Pane/Tab 속성은 model.py 코어에 안전한
    # 기본값이 있어, 플러그인 부재 시 코어가 그 속성을 읽어도 깨지지 않는다.
    def server_scan(self, server, sess, win) -> bool:
        """30Hz flush 루프의 Claude 스캔(상태/사용량/자동개입). 변화 있으면 True."""
        changed = False
        for p in self.plugins:
            fn = getattr(p, "server_scan", None)
            if fn is not None and fn(server, sess, win):
                changed = True
        return changed

    def server_filter_rows(self, server, pane, rows) -> list:
        """render 된 행 목록(행 = [text, style] 런 목록)을 클라 전송 직전에 플러그인이
        변형할 기회. claude-disable-feedback 가 Claude 패널의 '/feedback 팁'·세션 종료
        평가 배너를 공백으로 가린다(요청 2026-06-17·2026-06-18). 플러그인은 변형 시
        **새 리스트**를 돌려야 한다(render 캐시를
        공유하므로 in-place 금지). 아무도 변형 안 하면 원본을 그대로 돌려, 핫패스 비용은
        Claude 패널의 짧은 행 스캔뿐이다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "server_filter_rows", None)
            if fn is not None:
                rows = fn(server, pane, rows)
        return rows

    def server_status(self, server, sess, win, msg, full):
        """status 메시지에 Claude 필드를 in-place 로 채운다. 플러그인이 없으면 no-op
        → status 에 Claude 키가 빠지고, 클라(역시 플러그인 부재)는 그 키를 안 본다."""
        for p in self.plugins:
            fn = getattr(p, "server_status", None)
            if fn is not None:
                fn(server, sess, win, msg, full)

    def server_pane_overview(self, server, pane, info):
        """트리/개요 패널 정보(info dict)에 Claude 상태/사용량/토큰을 덧붙인다(in-place)."""
        for p in self.plugins:
            fn = getattr(p, "server_pane_overview", None)
            if fn is not None:
                fn(server, pane, info)

    def server_input(self, server, pane, data):
        """패널 입력 1건의 Claude 부수효과(프롬프트 추적 + 자동개입 타이머 해제)."""
        for p in self.plugins:
            fn = getattr(p, "server_input", None)
            if fn is not None:
                fn(server, pane, data)

    def server_paste(self, server, pane, data):
        """붙여넣기 입력의 프롬프트 추적(Claude 헤더용)."""
        for p in self.plugins:
            fn = getattr(p, "server_paste", None)
            if fn is not None:
                fn(server, pane, data)

    def server_pending(self, server, pane) -> "dict | None":
        """무장된 자동 액션 카운트다운({kind, eta}) 또는 None(없음)."""
        for p in self.plugins:
            fn = getattr(p, "server_pending", None)
            if fn is not None:
                r = fn(server, pane)
                if r is not None:
                    return r
        return None

    async def server_usage_refresh(self, server):
        """그림자 /usage 자동 갱신 1회(플러그인이 있고 Claude 패널이 있을 때만)."""
        for p in self.plugins:
            fn = getattr(p, "server_usage_refresh", None)
            if fn is not None:
                await fn(server)

    async def server_background(self, server):
        """플러그인 소유의 **장기 실행** 백그라운드 작업(주기 폴링 등).

        코어는 서버 수명 동안 이 코루틴 하나를 태스크로 띄우고 종료 시 취소한다 —
        주기·의미는 전부 플러그인 몫이다(코어 `_usage_loop` 처럼 코어가 간격을 아는
        구조를 더 늘리지 않으려고 둔 훅). 플러그인이 없으면 그냥 반환한다.

        한 플러그인이 죽어도 다른 플러그인·서버는 살아야 하므로 gather 는
        return_exceptions=True 로 모은다. 취소(CancelledError)는 그대로 전파돼
        서버 종료가 지연되지 않는다."""
        import asyncio as _a
        coros = []
        for p in self.plugins:
            fn = getattr(p, "server_background", None)
            if fn is not None:
                coros.append(fn(server))
        if not coros:
            return
        await _a.gather(*coros, return_exceptions=True)

    def server_command(self, server, client, sess, action, msg) -> "str | None":
        """Claude 명령 액션(set_claude_*/token/pc/refresh_usage 등)을 처리한다. 처리한
        플러그인이 있으면 코어가 따를 **후속 지시 문자열**을 반환한다:
          'handled'   — 플러그인이 다 처리, 코어는 추가 회신 없음(return).
          'send_full' — 코어가 요청 클라에 _send_full.
          'broadcast' — 코어가 _broadcast_session(sess) 후 요청 클라에 _send_full.
        처리한 플러그인이 없으면 None(코어가 handle_server_request 로 넘긴다)."""
        for p in self.plugins:
            fn = getattr(p, "server_command", None)
            if fn is not None:
                r = fn(server, client, sess, action, msg)
                if r is not None:
                    return r
        return None

    def server_control(self, server, sess, c, args) -> "str | None":
        """외부 CLI(`pytmux cmd <c> [args]`)의 알 수 없는 명령을 플러그인에 넘긴다.
        코어 handle_control 이 자기 표(_ONOFF_CONTROLS 등)에 없으면 마지막으로 이걸
        부른다 — 처리한 플러그인이 반환한 결과 문자열(예: 'on'/'off')을 그대로 CLI 에
        돌려준다. 처리 없으면 None(코어가 'unknown: c' 회신). 종전엔 claude/token
        토글이 코어 _ONOFF_CONTROLS 에 있어 플러그인 부재 시 setter 미존재로
        AttributeError 가 났다(delete-to-disable 위반) — 이 훅으로 소유를 이전한다."""
        for p in self.plugins:
            fn = getattr(p, "server_control", None)
            if fn is not None:
                r = fn(server, sess, c, args)
                if r is not None:
                    return r
        return None

    def relay_actions(self) -> set:
        """원격 보기(federation) 중 업스트림으로 릴레이해야 하는 cmd 액션 이름 집합을
        플러그인이 기여한다. 코어 serverio 가 코어 화이트리스트(_REMOTE_RELAY_ACTIONS)와
        **합집합**해 판정한다 — Claude/토큰 액션(set_autoresume·set_prompt_clear·
        request_token_log)은 claude-code 플러그인 소유라 부재 시 자동으로 빠진다."""
        out = set()
        for p in self.plugins:
            out |= set(getattr(p, "relay_actions", None) or ())
        return out

    def server_pty_output(self, server, pane, data):
        """패널 PTY 출력 1조각(raw 바이트)을 플러그인에 넘긴다(REC 캡처 등). 코어
        serverpty 드레인 루프가 `if self.capture: self._capture_write` 로 직접 가로채던
        걸 대체한다 — 플러그인이 없으면 no-op 라 코어는 바이트를 그냥 흘려보낸다
        (기록 안 함, delete-to-disable). **주의: 30Hz 드레인의 모든 바이트마다 불리는
        핫패스다** — self.plugins 가 보통 0~1개라 순회 비용은 무시할 만하다."""
        for p in self.plugins:
            fn = getattr(p, "server_pty_output", None)
            if fn is not None:
                fn(server, pane, data)

    def server_shutdown(self, server):
        """서버 종료·재시작(re-exec) 경계의 플러그인 정리(REC 캡처 파일 닫기 등). 코어
        serverio.shutdown·serverpersist 재시작 경로가 `_close_all_capfiles` 를 직접
        부르던 걸 대체한다 — 플러그인이 없으면 no-op."""
        for p in self.plugins:
            fn = getattr(p, "server_shutdown", None)
            if fn is not None:
                fn(server)

    # ---- 클라이언트 런타임 훅(코어가 패널 오버레이 플러그인을 이름으로 직접 부르지
    # 않게) ----
    # 코어(client)는 패널 오버레이(시계/달력 등) 그리기·1초 틱·닫기에 **이 훅으로만**
    # 닿는다. 플러그인이 없으면 전부 기본값(no-op/False)이라 코어가 그대로 동작한다
    # (delete-to-disable). 플러그인이 있으면 각 plugin 의 동명 메서드로 위임한다.
    def client_overlay(self, app, cells, W, H, active):
        """패널 전체를 덮는 오버레이(시계/달력 등)를 cells 에 그린다(in-place). 플러그인이
        없으면 no-op → 오버레이 없이 일반 패널 출력만 보인다."""
        for p in self.plugins:
            fn = getattr(p, "client_overlay", None)
            if fn is not None:
                fn(app, cells, W, H, active)

    def client_tick(self, app) -> bool:
        """1초 틱: 시간 갱신이 필요한 오버레이를 띄운 플러그인이 하나라도 있으면 True
        (코어가 재합성). 없으면 False(idle)."""
        changed = False
        for p in self.plugins:
            fn = getattr(p, "client_tick", None)
            if fn is not None and fn(app):
                changed = True
        return changed

    def client_close_overlay(self, app, pane_id) -> bool:
        """해당 패널의 플러그인 오버레이를 닫는다(패널 클릭/Shift+ESC). 닫은 플러그인이
        하나라도 있으면 True(코어가 입력 소비), 없으면 False(코어 기본 동작)."""
        closed = False
        for p in self.plugins:
            fn = getattr(p, "client_close_overlay", None)
            if fn is not None and fn(app, pane_id):
                closed = True
        return closed

    def client_overlay_covers(self, app, pane_id) -> bool:
        """그 패널이 지금 **플러그인 오버레이에 덮여 있나**(시계/달력 등).

        코어가 이것을 물어야 하는 자리가 있다: 오버레이는 코어의 마지막 층 뒤에 그려져
        **밑에 있는 클릭존을 가린다**. 존만 남으면 사용자가 보는 것(오버레이)과 탭이
        하는 일(뒤 패널 조작)이 어긋난다 — 라이브 PTY 팝업에서 이미 한 번 막은 그 모양
        이고(`layout["popup"]`), 오버레이는 코어가 아니라 **플러그인**이 아는 사실이라
        훅으로 묻는다.

        플러그인이 없으면 False → 종전과 같다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_overlay_covers", None)
            if fn is not None and fn(app, pane_id):
                return True
        return False

    def client_overlay_key(self, app, event) -> bool:
        """활성 패널에 플러그인 오버레이가 떠 있을 때 키 1건을 가로채(소비) 오버레이를
        조작한다(달력 월 이동 등). 소비한 플러그인이 하나라도 있으면 True(코어가 키를
        패널로 보내지 않음), 없으면 False(코어 기본 입력 경로). 플러그인이 없으면
        False(no-op) → 코어 입력 경로는 그대로다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_overlay_key", None)
            if fn is not None and fn(app, event):
                return True
        return False

    def client_key(self, app, event):
        """normal 모드에서 패널로 보낼 **확정(committed) 키 입력** 1건을 플러그인이
        관찰한다 — 서버측 server_input 의 클라이언트 대응. ime-indicator 가 최근 입력
        문자의 스크립트(한글/ASCII)로 한/영 상태를 추정하는 데 쓴다. 플러그인이 없으면
        no-op(루프 본문이 안 돌아 event 를 안 건드림) → 코어 입력 경로는 그대로다."""
        for p in self.plugins:
            fn = getattr(p, "client_key", None)
            if fn is not None:
                fn(app, event)

    def client_prompt_text(self, app, pane_id):
        """패널 프롬프트에 **현재 들어 있는 입력 텍스트**를 화면에서 긁어 돌려준다
        (첫 비-None 채택). 작성창 open_compose 가 클라 키 추적(_prompt_buf)이 빈 경우
        — 원격제어(/rc)·재접속처럼 클라 on_key 를 안 거친 입력 — 시드/비우기 길이로
        쓰는 fallback. 구현 플러그인(claude-code)이 화면 입력박스를 긁는다. 플러그인이
        없으면 None → 호출부가 추적치/초안으로 떨어진다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_prompt_text", None)
            if fn is not None:
                try:
                    r = fn(app, pane_id)
                except Exception:
                    r = None
                if r is not None:
                    return r
        return None

    def client_input_badge(self, app):
        """**글자를 받는 판**(물음·팔레트·작성창·설정 입력)의 입력줄 오른쪽 끝에 붙일
        배지 — `(문구, 의미색이름)` 또는 None(첫 비-None 채택).

        # 왜 이 훅이 필요한가 (pytmux-14)

        입력기 배지의 자리 규칙은 *"지금 글자를 받는 곳의 오른쪽 끝"* 이다. 캔버스에서는
        그것이 활성 패널의 커서 줄이고, 그 그림은 `client_render`(정본)·`plugin_cells`
        (서버)가 그린다. 그런데 **판이 열리면 커서가 판 안 입력줄로 가고**, 판은 Textual
        위젯이라 셀 격자 위에 있다 — 셀에 그린 배지는 판 **뒤**에 깔려 안 보인다.
        그래서 판 쪽은 셀이 아니라 **위젯**으로 붙여야 하고, 그 자리를 아는 것은 판이다.

        # 왜 코어가 직접 `[한]` 을 안 그리나

        delete-to-disable 이 깨진다. `ime-indicator` 디렉터리를 지우면 배지는 화면 어디에도
        없어야 하는데, 문구가 `clientscreens.py` 에 있으면 남는다. 그래서 판은 **자리만**
        내주고 무엇을 적을지는 플러그인이 정한다(`client_render` 와 같은 분업).

        색은 값이 아니라 **의미 이름**이다(`success`·`primary`) — 각 클라가 자기 테마에서
        푼다. 플러그인이 없으면 None → 판에 배지가 아예 안 붙는다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_input_badge", None)
            if fn is not None:
                try:
                    r = fn(app)
                except Exception:
                    r = None
                if r is not None:
                    return r
        return None

    def client_render(self, app, cells, W, H):
        """패널 내용(content) 위에 플러그인이 콘텐츠-레이어 장식을 그린다(in-place).
        claude-code 는 이 훅으로 ① 프롬프트 스티키 헤더를 그리고 ② footer 클릭존
        (권한모드/원격제어)을 스캔해 app 의 zone dict 를 채운다. 플러그인이 없으면
        no-op → Claude 헤더·클릭존이 전혀 나타나지 않는다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_render", None)
            if fn is not None:
                fn(app, cells, W, H)

    def client_status(self, app, msg):
        """서버 status 메시지의 플러그인-소유 필드를 클라가 흡수한다(in-place 상태 갱신).
        claude-code 는 이 훅으로 claude_rules 동기화, 패널별 Claude 상태
        (pane_claude) 갱신, /usage 자동 팝업 시퀀스를 처리한다. 플러그인이 없으면
        no-op → Claude 상태가 클라에 전혀 반영되지 않는다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_status", None)
            if fn is not None:
                fn(app, msg)

    def client_statusbar_update(self, app, status, msg):
        """status 메시지의 Claude 필드(claude_usage/tokens/model/warn/budget·토큰절감
        설정 등)를 하단 상태줄 위젯(status)에 in-place 흡수한다. 플러그인이 없으면
        no-op → 상태줄 Claude 세그먼트가 비활성(claude_active=False) 그대로다."""
        for p in self.plugins:
            fn = getattr(p, "client_statusbar_update", None)
            if fn is not None:
                fn(app, status, msg)

    def client_statusbar_init(self, app, status):
        """하단 상태줄 위젯(status) 생성 직후 — 플러그인이 위젯에 Claude 상태 속성
        (claude_active/usage/tokens/model·토큰절감 설정·예산·카운트다운 등)을 안전한
        기본값으로 설치한다. 코어 StatusBar.__init__ 은 이 속성들을 더 이상 두지 않고,
        client_statusbar_update(흡수)·client_statusbar(렌더)가 읽고 쓴다. 플러그인이
        없으면 no-op → 속성이 안 생기지만 흡수/렌더 훅도 함께 사라져 안전하다
        (delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_statusbar_init", None)
            if fn is not None:
                fn(app, status)

    def client_statusbar_badges(self, app, status, segs, w, w0=0) -> int:
        """하단 상태줄 **시스템 배지 영역**(SYNC/AR 직후, 좌하단 정보 클러스터보다 앞)에
        플러그인이 컴팩트 배지를 append 한다 — rec 가 ` REC ` 배지+클릭존을 여기서 그린다.
        client_statusbar(좌하단 정보 클러스터)와 같은 (제목 없는) 폭-체이닝 규약: w0=들어
        오는 누적 셀폭, 각 플러그인이 새 누적 폭을 반환하면 체이닝, 최종 폭을 돌려준다.
        플러그인이 없으면 w0 그대로(배지 없음, delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_statusbar_badges", None)
            if fn is not None:
                r = fn(app, status, segs, w, w0)
                if r is not None:
                    w0 = r
        return w0

    def client_statusbar(self, app, status, segs, w, w0=0) -> int:
        """하단 상태줄 좌측에 Claude 세그먼트(모델 배지·컨텍스트·토큰Σ·예산경고·카운트
        다운·폭주경고)를 append 하고 클릭존(_usage_zone/_model_zone)을 status 에 채운다.
        플러그인이 없으면 no-op → Claude 세그먼트가 전혀 안 그려지고 클릭존도 None(클릭
        no-op) — delete-to-disable.

        w0 = 들어오는 segs 의 누적 셀폭(P6). 각 플러그인이 자기 append 후의 새 누적
        폭을 반환하면 다음 플러그인·코어가 재순회 없이 이어 쓴다. 반환이 없으면(None)
        직전 w0 를 유지한다. 최종 누적 폭을 돌려준다(플러그인 부재면 w0 그대로)."""
        for p in self.plugins:
            fn = getattr(p, "client_statusbar", None)
            if fn is not None:
                r = fn(app, status, segs, w, w0)
                if r is not None:
                    w0 = r
        return w0

    # ---- Pane Claude 상태 소유 훅(S4) ----
    # 코어 model.py 의 Pane 은 Claude 거동 필드를 정의하지 않고, 생성·respawn·직렬화
    # 시 이 훅으로 플러그인에 위임한다. 플러그인이 없으면 전부 no-op/{} 이라 패널엔
    # Claude 필드가 안 생기고, 코어의 소수 읽기 지점은 getattr 기본값으로 동작한다
    # (delete-to-disable). claude-code 의 panestate 모듈이 구현한다.
    def pane_init(self, pane):
        """Pane 생성 시 — 플러그인이 패널에 Claude 거동 필드를 설치한다."""
        for p in self.plugins:
            fn = getattr(p, "pane_init", None)
            if fn is not None:
                fn(pane)

    def pane_closing(self, server, pane):
        """패널이 트리에서 제거되기 직전(servertree._remove_pane_from_tree) — 플러그인이
        패널-종료 부수효과를 처리한다. claude-code 가 닫히는 패널의 확정 토큰을 같은 계정
        생존 패널로 이관한다(#20, S5 토큰 모듈화 T4 에서 코어 servertree 에서 이전). 코어는
        토큰 누계 의미를 모른다. 플러그인이 없으면 no-op(토큰 기능 자체가 없다)."""
        for p in self.plugins:
            fn = getattr(p, "pane_closing", None)
            if fn is not None:
                fn(server, pane)

    def pane_reset(self, pane):
        """respawn(새 셸) 시 — 플러그인이 Claude 필드 부분집합을 리셋한다."""
        for p in self.plugins:
            fn = getattr(p, "pane_reset", None)
            if fn is not None:
                fn(pane)

    def pane_serialize(self, pane) -> dict:
        """재시작 직렬화 — 플러그인들의 Claude 보존 필드를 한 dict 로 합친다."""
        out = {}
        for p in self.plugins:
            fn = getattr(p, "pane_serialize", None)
            if fn is not None:
                out.update(fn(pane) or {})
        return out

    def pane_restore(self, pane, data):
        """재시작 복원 — 직렬화된 plugin_state dict 를 플러그인들이 흡수한다."""
        for p in self.plugins:
            fn = getattr(p, "pane_restore", None)
            if fn is not None:
                fn(pane, data)

    def client_status_tabs(self, app, tree) -> list:
        """통합 상태 팝업(_open_status_tabs)에 플러그인이 탭을 기여한다 — (제목, 줄들)
        또는 (제목, 줄들, 동작리스트) 튜플 목록을 반환한다. 동작리스트는 InfoTabsScreen
        에 그 탭 인덱스로 전달된다([(키,라벨,콜백),…]). rec 는 'REC' 탭(+[c]/[o] 동작)을,
        claude-code 는 (구) '토큰 사용량' 탭을 기여한다. 플러그인이 없으면 빈 목록 →
        팝업에 서버 탭만 남는다(delete-to-disable)."""
        tabs = []
        for p in self.plugins:
            fn = getattr(p, "client_status_tabs", None)
            if fn is not None:
                tabs.extend(fn(app, tree) or [])
        return tabs

    def client_tab_glyph(self, app, tab) -> "str | None":
        """탭바 한 탭 앞에 붙일 **상태 글리프**(예: Claude idle/busy/limit 아이콘)를
        플러그인이 기여한다. 코어 TabBar 는 첫 비-None 글리프 하나를 접두로 그린다 —
        종전엔 CLAUDE_ICON/`t.get("claude")` 렌더가 코어 위젯에 하드코딩돼 있었다.
        플러그인 부재 시 None 만 반환돼 접두 글리프가 사라진다(delete-to-disable)."""
        for p in self.plugins:
            fn = getattr(p, "client_tab_glyph", None)
            if fn is not None:
                g = fn(app, tab)
                if g:
                    return g
        return None

    def settings(self):
        """`:settings` 팝업에 플러그인이 설정 항목을 기여한다 — (descriptors, extra_cats)
        튜플을 반환한다. descriptors 는 코어 clientutil.SETTINGS 와 같은 스키마의 dict
        목록(key/cat/type/link/…), extra_cats 는 코어 SETTINGS_CATS 에 없던 카테고리
        이름 목록(좌측 세로탭 순서에 추가). 종전엔 'Claude' 카테고리와 token-saver/
        claude-rules/token-log 항목이 코어 SETTINGS 에 하드코딩돼 있었다 — 이 훅으로
        이전해 플러그인 부재 시 그 카테고리/항목이 통째로 사라진다(delete-to-disable)."""
        descs, cats = [], []
        for p in self.plugins:
            fn = getattr(p, "settings", None)
            if fn is not None:
                d, c = fn()
                descs.extend(d or [])
                for cat in (c or []):
                    if cat not in cats:
                        cats.append(cat)
        return descs, cats


def load():
    """plugins/ 를 스캔해 Registry 를 만든다(프로세스당 1회 호출이면 충분)."""
    return Registry(_discover())


_REGISTRY = None


def get():
    """프로세스 공용 캐시된 Registry. 코어 model.py 의 Pane 이 생성·respawn·직렬화 시
    pane_init/pane_reset/pane_serialize 훅을 부를 때 매번 재발견하지 않게 한 번만
    로드한다(server/client 는 자체 self.plugins 로 load() 를 쓰지만, Pane 은 그
    인스턴스에 접근할 수 없어 이 싱글톤을 쓴다).

    **load() 가 아니라 _discover() 를 직접 캐시**한다 — 테스트가 `plugins.load` 를
    바꿔치기(클라측 delete-to-disable 시뮬)해도, 서버 Pane 의 Claude 필드 설치는
    import 시점에 고정된 서버 믹스인과 **일관**되게 항상 실제 플러그인을 반영해야
    하기 때문이다(불일치 시 믹스인 스캔이 없는 필드를 읽어 깨진다). 디렉토리를 진짜로
    지우면 _discover() 가 claude-code 를 못 찾아 pane_init 이 no-op 이 된다."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = Registry(_discover())
    return _REGISTRY


def reset():
    """캐시된 싱글톤을 비운다(테스트 전용 — 플러그인 셋 변화 시뮬 후 강제 재발견)."""
    global _REGISTRY
    _REGISTRY = None
