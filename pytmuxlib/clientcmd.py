"""명령 처리 믹스인 — client.py 에서 분리한 PytmuxApp 믹스인(§5.4 파일 분할, CODE_REVIEW 4-1).

거동 불변·위치만 분리: 메서드는 self 경유라 MRO 로 그대로 동작한다. import 헤더는
client.py 원본을 복제(over-import + noqa F401)해 이름 해석 누락을 원천 차단했다.
"""
from __future__ import annotations

import asyncio
import base64
import os
import shlex
import socket
import subprocess
import time
import traceback

# textual/rich 심볼(종전 build_client_app 안에서 지연 import 하던 것 — §5.4 de-nest).
# client.py 는 이미 모듈 최상위에서 clientwidgets/clientscreens 를 import 하고 그들이
# textual 을 끌어오므로, 이 import 를 최상위로 올려도 기동 비용은 동일하다(textual 은
# 이미 로드됨). 최상위에 둬야 PytmuxApp/믹스인이 팩토리 클로저 밖(모듈 레벨)에서도
# 이 심볼을 참조할 수 있다(CLI 경량 기동은 pytmux.py 의 _LAZY 가 client import 자체를
# 미뤄 달성 — 이 심볼 위치와 무관).
from rich.style import Style
from textual import events
from textual.app import App, ComposeResult
from textual.await_complete import AwaitComplete
from textual.binding import Binding
from textual.geometry import Offset
from textual.suggester import SuggestFromList

from . import clientclip, clientrender, i18n, ipc, plugins, proc, version
from .clientutil import (  # noqa: F401  (클로저에서 이름으로 사용)
    COMMAND_ARGHIST, COMMAND_NOARG, COMMAND_OPTIONS, COMMANDS, COMPLETIONS,
    DEFAULT_STYLE, norm_sep,
    SETTINGS, SETTINGS_CATS,
    REMOTE_PINK, REMOTE_PINK_DIM,
    _BOX_BITS, _BOX_REV, _JAMO, _KEY_DIAG,
    _TB_ACTIVE_STYLE, _TB_BORDER_STYLE, _TB_INACTIVE_STYLE,
    _char_cells, _client_relaunch_ok, _darken_style, _dim_inactive_style,
    _first_int, _first_signed_int, _is_emoji, _opt_value, _restart_check_eval,
    _signed_int, _with_reverse,
    has_hangul, hangul_to_qwerty,
    _normalize_key, _shell_argv, key_to_bytes, make_style, theme_color)
from .clientscreens import (  # noqa: F401  (클로저에서 push_screen 으로 사용)
    ChooseBufferScreen, ChooseLayoutScreen, ChooseTreeScreen,
    CommandListScreen, CommandOptionsScreen, ComposePromptScreen, ConfirmScreen,
    InfoScreen, InfoTabsScreen, MenuScreen, PluginManagerScreen, PromptScreen,
    SettingsScreen)
from .clientwidgets import (  # noqa: F401  (PytmuxApp.compose·ghost suggester)
    MultiplexerView, SepInsensitiveSuggester, StatusBar, TabBar)
from .keymap import (_key_to_ctrl_bytes, _tmux_key_to_textual,
                     config_path_for_write, load_config, normalize_binding_key,
                     set_config_option, textual_key_to_tmux)
from .protocol import MIN_H, MIN_W, PROTO_VERSION, read_msg, write_msg


class _CommandMixin:
    # §5.4 명령 실행 클러스터(프롬프트 수명·셸 우회·명령 디스패치) — 모듈 레벨 분리, PytmuxApp 은 MRO 로 상속

    # ── 명령 인자 이력(arg history) ──────────────────────────────────────────
    # remote-attach 등 자유 텍스트 인자를 직접 치는 명령의 **이전 입력 인자**를 기억해
    # 다음에 추천(후보 목록)·자동완성(ghost)한다(사용자 요청). COMMAND_ARGHIST 의 버킷별
    # 최근-우선 리스트를 서버별 상태파일(<state>.arghist.json)에 영속한다. 같은 버킷을
    # 공유하는 명령끼리(remote-*) 이력을 공유한다.
    _ARGHIST_MAX = 30        # 버킷당 보관할 최근 인자 수

    def _arghist_path(self):
        sock = getattr(self, "sock_path", None)
        return ipc.state_base(sock) + ".arghist.json" if sock else None

    def _load_arghist(self):
        """영속된 명령 인자 이력을 self._arghist(버킷→최근-우선 리스트)로 복원(best-effort).
        파일이 없거나 깨졌으면 빈 이력으로 시작한다 — 추천이 없을 뿐 동작은 정상."""
        self._arghist = {}
        path = self._arghist_path()
        if not path:
            return
        try:
            import json
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                for k, v in d.items():
                    if isinstance(v, list):
                        self._arghist[k] = [x for x in v if isinstance(x, str)]
        except (OSError, ValueError):
            pass

    def _save_arghist(self):
        """현재 이력을 임시파일+os.replace 로 원자적 기록(best-effort). 저장 실패가
        명령 실행을 막지 않게 예외는 삼킨다(rtt/netdbg 와 같은 정책)."""
        path = self._arghist_path()
        if not path:
            return
        try:
            import json
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(getattr(self, "_arghist", {}), f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass

    @staticmethod
    def _arghist_canon(first):
        """입력 첫 토큰(명령어)을 arghist 정규 명령 이름으로 — 구분자(공백/_/-)·대소문자
        무시(remote_attach·Remote-Attach 모두 remote-attach). arghist 대상 아니면 None."""
        if not first:
            return None
        q = norm_sep(first.lower())
        for name in COMMAND_ARGHIST:
            if norm_sep(name) == q:
                return name
        return None

    def _arghist_list(self, first):
        """입력 첫 토큰에 해당하는 명령의 인자 이력(최근 우선). 대상 아니면 []."""
        canon = self._arghist_canon(first)
        if not canon:
            return []
        return list(getattr(self, "_arghist", {}).get(COMMAND_ARGHIST[canon], []))

    def _arghist_completions(self):
        """이력 기반 ghost 자동완성 줄("cmd arg", 최근 우선)을 만든다. 타이핑 중
        'remote-attach o' → 'remote-attach office1' 처럼 인자까지 제안된다(→/Tab 수락).
        최근이 앞이라 SepInsensitiveSuggester 의 prefix 매칭에서 최근 인자가 먼저 잡힌다."""
        out = []
        hist = getattr(self, "_arghist", {})
        for cmd, bucket in COMMAND_ARGHIST.items():
            for arg in hist.get(bucket, []):
                out.append(f"{cmd} {arg}")
        return out

    def _record_arg(self, line):
        """제출된 명령 줄에서 (정규 명령, 원시 인자)를 뽑아 이력 맨 앞에 기록·영속.
        백슬래시 보존을 위해 shlex 가 아니라 split(None,1) 의 원시 잔여를 쓴다
        (remote-attach NATGAMES\\user@host). 대상 명령·인자가 아니면 무시."""
        parts = line.split(None, 1)
        if len(parts) < 2:
            return
        canon = self._arghist_canon(parts[0])
        if not canon:
            return
        arg = parts[1].strip()
        if not arg:
            return
        bucket = COMMAND_ARGHIST[canon]
        hist = getattr(self, "_arghist", None)
        if hist is None:
            hist = self._arghist = {}
        cur = [a for a in hist.get(bucket, []) if a != arg]   # 중복 제거→최근으로
        cur.insert(0, arg)
        hist[bucket] = cur[:self._ARGHIST_MAX]
        self._save_arghist()

    def open_prompt(self, purpose, placeholder="", initial="", action=None,
                    suggest=None):
        # 한 줄 입력을 Input 을 담은 바닥 모달(PromptScreen)로 받는다.
        # 모달은 별도 스크린이라 포커스가 안정적이다(메인 뷰/AUTO_FOCUS 와 무관).
        # suggest: rename 등에서 현재 이름을 **ghost(제안)** 로 띄운다 — Tab/→ 로
        #   채워 편집·덧붙이고, 그냥 타이핑하면 덮어쓴다(initial 로 미리 채우면
        #   타이핑이 덧붙던 문제, 요청). 빈 입력일 땐 placeholder 로도 흐리게 보인다.
        suggester = None
        if purpose == "command":
            # 이력 줄(최근 우선)을 앞에 둬 'remote-attach o'→최근 호스트가 먼저 ghost.
            suggester = SepInsensitiveSuggester(
                self._arghist_completions() + COMPLETIONS + self.plugins.completions,
                case_sensitive=False)
        elif suggest:
            suggester = SuggestFromList([suggest], case_sensitive=False)
        self.push_screen(
            PromptScreen(purpose, placeholder, initial, suggester),
            lambda val: self._prompt_done(purpose, action, val))

    def _prompt_done(self, purpose, action, val):
        if purpose == "search":
            self.mode = "scroll"  # 검색은 스크롤백 모드 유지/복귀
        if val is None:  # 취소(Esc)
            return
        val = val.strip()
        if purpose == "command":
            self._record_arg(val)   # remote-attach 등 인자를 이력에 기록(추천용)
            self._run_command(val)
        elif purpose == "rename_window":
            if val:
                self.send_cmd("rename_window", name=val)
        elif purpose == "rename_pane":
            self.send_cmd("set_pane_title", title=val)
        elif purpose == "move_window":
            if val.lstrip("-").isdigit() and int(val) - 1 >= 0:
                self.send_cmd("move_window", index=int(val) - 1)  # 1-based→0(#21)
        elif purpose == "save_layout":
            if val.strip():
                self.send_cmd("save_tab_layout", name=val.strip())
        elif purpose == "search":
            if val:
                self.send_cmd("search", query=val, direction="up")
        elif purpose == "confirm":
            if val.lower().startswith("y") and action:
                action()

    def _compose_track_input(self, pid, data: bytes):
        r"""normal 패스스루로 활성 패널에 보낸 키 입력을 패널별로 누적해 **현재
        프롬프트에 남아 있는 텍스트**(`_prompt_buf[pid]`)를 추정한다(서버
        prompt-history track_input 의 클라 판 — 그쪽은 서버라 클라가 못 읽음).
        CSI/ESC 시퀀스(화살표 등)는 건너뛰고, backspace 는 한 글자 제거, Enter(\r)
        는 제출 경계라 비우고, \n(Shift+Enter)은 줄바꿈 누적, 인쇄 가능 문자는 추가.
        '프롬프트 인계' 컴포즈(open_compose)가 이 값을 시드로 쓰고 그만큼 백스페이스로
        프롬프트를 비운다. Claude 안에서 커서를 옮겨 편집하면 어긋날 수 있는 근사치."""
        if not data:
            return
        buf = getattr(self, "_prompt_buf", None)
        if buf is None:
            buf = self._prompt_buf = {}
        s = buf.get(pid, "")
        text = data.decode("utf-8", "ignore")
        i, n = 0, len(text)
        while i < n:
            ch = text[i]
            if ch == "\x1b":                 # ESC/CSI: 제어 시퀀스 건너뜀
                i += 1
                if i < n and text[i] == "[":
                    i += 1
                    while i < n and not (0x40 <= ord(text[i]) <= 0x7e):
                        i += 1
                    i += 1
                else:
                    i += 1
                continue
            if ch == "\r":                   # Enter: 제출 → 프롬프트 비워짐
                s = ""
            elif ch == "\n":                 # Shift+Enter/Ctrl+J: 줄바꿈 누적
                s += "\n"
            elif ord(ch) in (8, 127):        # backspace
                s = s[:-1]
            elif ord(ch) >= 32:
                s += ch
            i += 1
        buf[pid] = s[-4000:]

    def _current_prompt_text(self, pane_id):
        """패널 프롬프트에 **지금 들어 있는 텍스트**를 best-effort 로 돌려준다(없으면 "").

        ① 클라가 추적한 키 입력(`_prompt_buf`) — 사용자가 이 클라에서 친 것은 정확
           (멀티라인 개행·백스페이스 반영). 단 원격제어(/rc)·재접속처럼 클라 on_key 를
           안 거친 입력은 추적에 없다.
        ② 추적이 비어 있으면 플러그인(claude-code)이 **화면 입력박스에서 긁은 값**
           (client_prompt_text 훅) — 추적 못 한 입력도 화면 기준으로 포착한다.
        작성창 open_compose 가 시드(표시)와 비우기(백스페이스 개수)에 같은 값을 쓴다 —
        둘이 일치해야 '인계 후 통째 투입' 이 중복/잔여 없이 맞아떨어진다."""
        buf = getattr(self, "_prompt_buf", None) or {}
        typed = buf.get(pane_id, "")
        if typed:
            return typed
        plugins = getattr(self, "plugins", None)
        scraped = plugins.client_prompt_text(self, pane_id) if plugins else None
        return scraped or ""

    def open_compose(self, initial=None):
        """블록 선택 편집이 되는 멀티라인 작성창(ComposePromptScreen)을 연다.

        Claude Code 등 자식 프롬프트 입력기는 Shift+방향키 범위 선택 편집을
        지원하지 않고, pytmux 는 자식의 논리 버퍼·커서 인덱스를 알 수 없어 그 위에
        선택을 투명하게 얹을 수 없다(타당성 검토 A 안 비권장). 대신 pytmux 가 버퍼를
        소유하는 별도 작성창에서 작성→완료 시 활성 패널에 **bracketed paste 로 투입**
        한다(권고안 B). ESC 모드에서 Insert 로 호출(옵트인, 필요할 때 매번).

        '프롬프트 인계': 활성 패널 프롬프트에 **현재 들어 있는 텍스트 전체**를 시드로
        채운다(_current_prompt_text — 클라 추적치, 없으면 화면 입력박스 긁기). 비우기는
        **여는 시점이 아니라 적용(Ctrl+S) 시점**에 한다(사용자 요청 2026-06-22): 적용
        직전 프롬프트를 다시 읽어 그 길이만큼 백스페이스로 통째 비운 뒤 작성창 텍스트를
        bracketed paste 로 넣는다 — 이렇게 해야 추적 누락분(원격제어/재접속 입력)까지
        화면 기준으로 깨끗이 지워지고, Esc 취소 시엔 프롬프트가 그대로 보존된다. 친 게
        없으면 직전 **초안**(`_compose_draft`)을 시드로 쓰고, Esc 로 닫아도 초안에 남아
        다음에 다시 시드된다."""
        active = self.layout.get("active")
        # 시드 우선순위: ① 프롬프트에 현재 들어 있는 텍스트(추적치→없으면 화면 긁기)
        #                ② 없으면 저장된 초안(취소해도 보존)
        current = self._current_prompt_text(active)
        seed = initial if initial is not None else (
            current if current else getattr(self, "_compose_draft", ""))

        def done(result):
            if not result:        # 방어(정상 경로는 (text, injected) 튜플)
                return
            text, injected = result
            self._compose_draft = text        # 초안 보존(취소해도 다음 시드)
            if not injected:                  # Esc 취소 — 프롬프트 그대로 둔다
                return
            # 적용(Ctrl+S): 프롬프트를 다시 읽어 들어 있는 텍스트 전체를 백스페이스로
            # 비운 뒤(추적 누락분도 화면 기준 포착·커서가 끝일 때 정확) 작성창 텍스트를
            # 통째 투입한다. 빈 입력에서의 추가 백스페이스는 Claude 에서 무동작이라 안전.
            cur = self._current_prompt_text(active)
            if cur:
                self.send_input(b"\x7f" * len(cur))
            buf = getattr(self, "_prompt_buf", None)
            if isinstance(buf, dict):
                buf[active] = ""
            if text:
                # 서버가 pane.bracketed 면 \x1b[200~…201~ 로 감싸 멀티라인이 줄마다
                # 제출되지 않는다. 끝에 Enter 를 안 붙여 자동 제출 없음(사용자가 직접).
                self.send_cmd("paste", text=text)
                if isinstance(buf, dict):
                    buf[active] = text   # 이제 프롬프트에 이 텍스트가 있음
            else:
                self.display_message(i18n.t("compose.empty"))
        # 작성창 입력 줄을 활성 패널 프롬프트 줄(하드웨어 커서 행)보다 한 칸 아래에
        # 맞춘다(_active_cursor_xy). 좌우는 활성 패널 테두리 안쪽(box 있으면 bx+1..
        # bw-2, 없으면 x..w)에 맞춘다. 미상이면 각각 바닥 도킹/전체 폭.
        xy = getattr(self, "_active_cursor_xy", None)
        prompt_row = xy[1] if xy else None
        pane_x = pane_w = None
        for p in self.layout.get("panes", []):
            if p["id"] == active:
                box = p.get("box")
                if box:
                    bx, _by, bw, _bh = box
                    pane_x, pane_w = bx + 1, max(1, bw - 2)
                else:
                    pane_x, pane_w = p["x"], p["w"]
                break
        self.push_screen(
            ComposePromptScreen(seed, prompt_row, pane_x, pane_w), done)

    def _run_shell(self, cmd):
        try:
            res = subprocess.run(_shell_argv(cmd), capture_output=True,
                                 timeout=15, **proc.no_window_kwargs())
            text = res.stdout.decode("utf-8", "ignore")
            rc = res.returncode
        except (OSError, subprocess.SubprocessError) as e:
            text, rc = str(e), 1
        if text.strip():
            self.send_cmd("set_buffer", text=text)
            self.push_screen(InfoScreen(text.splitlines()[:40], title="run-shell"))
        return rc

    def _if_shell(self, cond, then_cmd, else_cmd=None):
        try:
            rc = subprocess.run(_shell_argv(cond), capture_output=True,
                                timeout=15,
                                **proc.no_window_kwargs()).returncode
        except (OSError, subprocess.SubprocessError):
            rc = 1
        if rc == 0:
            self._run_command(then_cmd)
        elif else_cmd:
            self._run_command(else_cmd)

    _SENDKEYS = {"Enter": b"\r", "Tab": b"\t", "Space": b" ",
                 "Escape": b"\x1b", "BSpace": b"\x7f", "Up": b"\x1b[A",
                 "Down": b"\x1b[B", "Right": b"\x1b[C", "Left": b"\x1b[D"}

    def _send_keys(self, args):
        literal = "-l" in args
        toks = [a for a in args if not a.startswith("-")]
        out = b""
        for a in toks:
            if not literal and a in self._SENDKEYS:
                out += self._SENDKEYS[a]
            elif (not literal and a.startswith("C-") and len(a) == 3
                  and a[2].isalpha()):
                out += bytes([ord(a[2].lower()) - 96])
            else:
                out += a.encode("utf-8")
        if out:
            self.send_input(out)

    def _run_command(self, line, _depth=0):
        """tmux 류 명령 문자열을 해석해 서버 명령으로 변환한다."""
        if not line or _depth > 8:
            return
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            return
        # 한영 오타 복원: 명령 이름 토큰에 한글이 섞이면(IME 켠 채 입력) QWERTY 로
        # 되돌린다. 이름은 항상 ASCII 이므로 안전하고, 인자(한글 이름 등)는 안 건드린다.
        if has_hangul(parts[0]):
            parts[0] = hangul_to_qwerty(parts[0])
        c = parts[0].lower()
        args = parts[1:]
        # 사용자 별칭 확장
        if c in self.aliases:
            return self._run_command(
                self.aliases[c] + (" " + " ".join(args) if args else ""),
                _depth + 1)
        if c in ("plugins", "plugin-manager"):
            # 플러그인 관리 팝업(PLUGIN_MANAGER_SCENARIO) — 설치된 플러그인 on/off.
            self.push_screen(PluginManagerScreen())
            return
        if c in ("help", "commands", "?", "list-commands"):
            # 명령 목록 선택기(#3): 옵션 스키마가 있으면 옵션 모달에서 값을 정해
            # 프롬프트 없이 바로 실행, 인자 없는 안전한 명령은 선택 즉시 실행,
            # 그 외(자유 텍스트 인자)는 기존처럼 명령 프롬프트에 채워 Enter 로 실행.
            all_commands = COMMANDS + self.plugins.commands
            all_options = {**COMMAND_OPTIONS, **self.plugins.command_options}
            all_noarg = COMMAND_NOARG | self.plugins.noarg

            def _picked(name):
                if not name:
                    return
                opts = all_options.get(name)
                if opts:
                    desc = next((d for n, d, *_ in all_commands
                                 if n == name), "")

                    def _run(line):
                        if line:
                            self._run_command(line)
                    self.push_screen(
                        CommandOptionsScreen(name, desc, opts), _run)
                elif name in all_noarg:
                    self._run_command(name)
                else:
                    self.open_prompt("command", "", initial=name + " ")
            self.push_screen(CommandListScreen(all_commands), _picked)
            return
        # 코어 명령 디스패치 전에 플러그인에 기회를 준다(ncd 등). 플러그인 명령은
        # 코어와 이름이 겹치지 않으므로 우선순위 충돌은 없다. 디렉토리를 지우면
        # 여기서 아무도 처리하지 않아 명령은 조용히 무시된다.
        if self.plugins.handle_command(self, c, args):
            return
        if c in ("run-shell", "run"):
            if args:
                self._run_shell(args[0])
            return
        if c in ("if-shell", "if"):
            if len(args) >= 2:
                self._if_shell(args[0], args[1], args[2] if len(args) > 2 else None)
            return
        if c in ("split-window", "splitw"):
            # tmux 규약: -h = 좌우(side-by-side, lr), -v/기본 = 상하(tb).
            # (과거엔 -h→상하로 반전돼 prefix %/" · join-pane -h 와 어긋났다.)
            orient = "lr" if "-h" in args else "tb"
            self.send_cmd("split", orient=orient)
        elif c in ("kill-pane", "killp"):
            self.send_cmd("kill_pane")
        elif c in ("new-tab", "newt", "new-window", "neww"):
            self.send_cmd("new_window")
        elif c in ("kill-tab", "killt", "kill-window", "killw"):
            # 원격 탭이면 kill_window(서버가 §1.7-c 거부) 대신 그 링크를 분리한다
            # ([x]/esc x 와 동일 라우팅, confirm_kill_tab 참조).
            rhost = self._active_remote_host()
            if rhost is not None:
                self.send_cmd("remote_detach", host=rhost)
            else:
                self.send_cmd("kill_window")
        elif c in ("next-tab", "next-window", "next"):
            self.send_cmd("next_window")
        elif c in ("previous-tab", "prev-tab", "previous-window", "prev"):
            self.send_cmd("prev_window")
        elif c in ("last-tab", "last-window", "last"):
            self.send_cmd("last_window")
        elif c == "automatic-rename" or (
                c == "setw" and "automatic-rename" in args):
            val = None
            if "on" in args:
                val = True
            elif "off" in args:
                val = False
            self.send_cmd("set_auto_rename", value=val)
        elif (c in ("monitor-activity", "monitor-bell")) or (
                c == "setw" and any("monitor-" in a for a in args)):
            which = "bell" if ("bell" in c or "monitor-bell" in args) else "activity"
            val = None
            if "on" in args:
                val = True
            elif "off" in args:
                val = False
            self.send_cmd("set_monitor", which=which, value=val)
        elif c in ("pin-tab", "pin", "unpin-tab", "unpin", "pin-toggle"):
            # 항목7: 활성 탭 고정/해제/토글. 원격 탭은 고정 불가(§9 — 업스트림 릴레이
            # 미지원). set_pinned: value 없음=토글.
            if self._active_remote_host() is not None:
                self.display_message(i18n.t("msg.pin_remote_blocked"))
            elif c in ("pin-tab", "pin"):
                self.send_cmd("set_pinned", value=True)
            elif c in ("unpin-tab", "unpin"):
                self.send_cmd("set_pinned", value=False)
            else:
                self.send_cmd("set_pinned")        # 토글
        elif c in ("move-tab-left", "move-tab-right",
                   "move-tab-first", "move-tab-last"):
            self.send_cmd("move_current_tab", where=c[len("move-tab-"):])
        elif c in ("move-tab", "movet", "move-window", "movew"):
            idx = self._tab_target_index(args)     # 양수 1-based·음수 끝에서(§2.8)
            if idx is not None:
                self.send_cmd("move_window", index=idx)
        elif c in ("swap-tab", "swapt", "swap-window", "swapw"):
            idx = self._tab_target_index(args)     # 양수 1-based·음수 끝에서(§2.8)
            if idx is not None:
                self.send_cmd("swap_window", index=idx)
        elif c in ("choose-tree", "choose-tab", "choose-window",
                   "overview", "tree"):
            self.request_tree()
        elif c in ("select-pane", "selectp"):
            if "-T" in args:
                title = " ".join(args[args.index("-T") + 1:])
                self.send_cmd("set_pane_title", title=title)
            else:
                for flag, d in (("-L", "left"), ("-R", "right"),
                                ("-U", "up"), ("-D", "down")):
                    if flag in args:
                        self.send_cmd("select_pane", dir=d)
                        break
        elif c == "rename-pane":
            self.send_cmd("set_pane_title", title=" ".join(args))
        elif c in ("select-tab", "selectt", "select-window", "selectw"):
            idx = self._tab_target_index(args)     # 양수 1-based·음수 끝에서(§2.8)
            if idx is not None:
                self.send_cmd("select_window", index=idx)
        elif c in ("rename-tab", "renamet", "rename-window", "renamew"):
            # 인자(이름)가 있으면 즉시 변경. 인자 없이 입력하면 **아무 동작 없이
            # 취소**한다(예전 rename 프롬프트 인터페이스를 열지 않음 — 사용자 요청).
            # 이름 입력 ghost 프롬프트는 prefix+, 키로만 연다(_handle_prefix).
            name = " ".join(a for a in args if not a.startswith("-"))
            if name:
                self.send_cmd("rename_window", name=name)
        elif c in ("resize-pane", "resizep"):
            if "-Z" in args:
                self.send_cmd("zoom")
            else:
                # tmux resize-pane -L/-R/-U/-D [N]: 분할선을 N칸(기본 3) 이동.
                # 마우스 divider 드래그·prefix HJKL 과 같은 resize_dir 경로로 보내
                # 키·명령·마우스 리사이즈를 대칭화한다(#17 — 과거엔 -Z 만 처리해
                # 명령/팔레트로는 분할선 정밀 이동이 불가했다).
                _dmap = {"-L": "left", "-R": "right", "-U": "up", "-D": "down"}
                d = next((_dmap[a] for a in args if a in _dmap), None)
                if d is not None:
                    self.send_cmd("resize_dir", dir=d,
                                  cells=(_first_int(args) or 3))
        elif c == "zoom":
            self.send_cmd("zoom")
        elif c in ("select-layout", "selectl"):
            if args:
                self.send_cmd("select_layout", preset=args[0])
            else:
                self.send_cmd("cycle_layout")
        elif c in ("next-layout", "nextl"):
            self.send_cmd("cycle_layout")
        elif c in ("rotate-window", "rotatew"):
            self.send_cmd("rotate", forward=("-D" not in args))
        elif c in ("swap-pane", "swapp"):
            # -s/-t <번호>: display-panes(prefix q) 오버레이의 0-based 패널
            # 번호로 임의의 두 패널을 교환(마우스 헤더 드래그와 같은
            # swap_pane_to 경로). -t 만 주면 활성 패널과, -s -t 둘 다면 그 두
            # 패널을 맞바꾼다. -s/-t 가 없으면 기존 인접 순환 swap(-U=이전·기본
            # 다음). §2.3: 마우스 전용이던 임의 swap 을 명령/키 경로로 대칭화.
            if "-s" in args or "-t" in args:
                a = self._pane_id_by_index(_opt_value(args, "-s"))
                b = self._pane_id_by_index(_opt_value(args, "-t"))
                act = self.layout.get("active")
                a = a if a is not None else act
                b = b if b is not None else act
                if a is not None and b is not None and a != b:
                    self.send_cmd("swap_pane_to", id=a, to_id=b)
                # 유효하지 않은 번호(범위밖·비숫자)면 조용히 무시 — 인접
                # swap 으로 떨어지지 않는다(엉뚱한 패널 교환 방지).
            else:
                self.send_cmd("swap_pane", forward=("-U" not in args))
        elif c in ("break-pane", "breakp"):
            self.send_cmd("break_pane")
        elif c in ("join-pane", "joinp"):
            self.send_cmd("join_pane", orient=("lr" if "-h" in args else "tb"))
        elif c in ("respawn-pane", "respawnp"):
            self.send_cmd("respawn_pane")
        elif c in ("capture-output", "capture-toggle"):
            val = None
            if "on" in args:
                val = True
            elif "off" in args:
                val = False
            self.send_cmd("set_capture", value=val)
            state = (i18n.t("word.toggle") if val is None
                     else ("ON" if val else "OFF"))
            self.display_message(i18n.t("msg.capture_toggle", state=state))
        elif c in ("synchronize-panes", "syncp") or (
                c == "setw" and "synchronize-panes" in args):
            val = None
            if "on" in args:
                val = True
            elif "off" in args:
                val = False
            self.send_cmd("set_sync", value=val)
        elif c == "setw" and "pane-border-status" in args or \
                c == "pane-border-status":
            val = None
            if "on" in args or "top" in args:
                val = True
            elif "off" in args:
                val = False
            self.send_cmd("set_border_status", value=val)
        elif c in ("inactive-dim", "dim-inactive"):
            # §2.9 비활성 패널 dim 세션 토글(클라-로컬 표현; 영속 기본값은 config
            # inactive_dim). 인자 on/off, 없으면 반전. 즉시 재합성해 반영.
            if "on" in args:
                self.inactive_dim = True
            elif "off" in args:
                self.inactive_dim = False
            else:
                self.inactive_dim = not self.inactive_dim
            self._composite()
            self.display_message(i18n.t(
                "msg.inactive_dim",
                state=("ON" if self.inactive_dim else "OFF")))
        elif c in ("inactive-dim-ratio", "dim-inactive-ratio"):
            # §2.9 비활성 패널 dim 세기(0~0.8) 런타임 조정. 인자 숫자 = 그 값으로
            # 설정(범위 클램프), 인자 없으면 현재 값 표시. 영속 기본값은 config
            # inactive_dim_ratio. 즉시 재합성해 반영(클라-로컬 표현).
            arg = args[0] if args else None
            if arg is not None:
                try:
                    self.inactive_dim_ratio = max(0.0, min(0.8, float(arg)))
                except (TypeError, ValueError):
                    self.display_message(i18n.t("msg.inactive_dim_ratio_bad"))
                    return
                self._composite()
            self.display_message(i18n.t(
                "msg.inactive_dim_ratio",
                ratio=f"{self.inactive_dim_ratio:.2f}"))
        elif c in ("settings", "config", "preferences", "prefs", "옵션"):
            # 통합 설정 화면(SETTINGS 레지스트리). 흩어진 설정을 한 곳에서 보고/바꾼다.
            # 링크 행(Claude·플러그인 전용 화면)을 고르면 그 명령을 돌려받아 디스패치한다.
            def _after_settings(line):
                if line:
                    self._run_command(line)
            # '키' 탭에 현재 prefix·사용자 바인딩을 보여주려 컨텍스트를 넘긴다(읽기 전용).
            self.push_screen(SettingsScreen(
                prefix_key=self.prefix_key,
                user_bindings=dict(self.bindings),
                root_bindings=dict(self.root_bindings)), _after_settings)
        elif c in ("detach-client", "detach"):
            if "-a" in args:
                self.send_cmd("detach_others")
            else:
                self.exit(message="detached")
        elif c == "kill-server":
            self.confirm_kill_server()
        elif c in ("remote-attach", "remote_attach"):
            # §1.7 페더레이션: 원격 pytmux 서버 탭을 이 pytmux 탭바에 병합.
            # 성공하면 ⇄host:이름 탭이 나타난다(선택=진입). ssh -T 가 전송.
            # host 는 shlex 토큰이 아니라 **원시 잔여 문자열** — 도메인 계정
            # (NATGAMES\user@host)의 백슬래시를 shlex(posix)가 삼키지 않게.
            rest = line.split(None, 1)
            host = rest[1].strip() if len(rest) > 1 else ""
            if host:
                self.send_cmd("remote_attach", host=host)
            else:
                self.display_message(i18n.t("msg.remote_attach_usage"))
        elif c in ("remote-new-tab", "remote_new_tab", "remote-new-window"):
            # §1.7 페더레이션: 원격 pytmux 에 **새 터미널**을 만들어 이 pytmux 의
            # 새 탭으로 붙인다(remote-attach 가 기존 원격 탭을 병합·열람만 하는 것과
            # 달리 원격에 새 셸을 띄운다). 아직 attach 안 됐으면 먼저 attach 한다.
            # host 는 remote-attach 와 같이 원시 잔여 문자열(백슬래시 보존).
            rest = line.split(None, 1)
            host = rest[1].strip() if len(rest) > 1 else ""
            if host:
                self.send_cmd("remote_new_window", host=host)
            else:
                self.display_message(i18n.t("msg.remote_newtab_usage"))
        elif c in ("remote-detach", "remote_detach"):
            rest = line.split(None, 1)
            host = rest[1].strip() if len(rest) > 1 else ""
            self.send_cmd("remote_detach",
                          **({"host": host} if host else {}))
        elif c in ("restart-server", "restart"):
            # 작업 보존 재시작: 셸/PTY 를 살린 채 서버 코드만 교체(re-exec).
            # 화면이 잠깐 끊겼다 재접속된다(docs/internal/RESTART_SCENARIO.md).
            # 실행 전 드라이런으로 안전성을 먼저 점검한다.
            self.begin_restart("server")
        elif c in ("restart-check", "restart-dry-run", "restart-all-check"):
            # restart-all 드라이런: 실제 재시작 없이 안전성만 점검해 팝업으로 보고.
            self.open_restart_check()
        elif c in ("restart-all", "full-restart", "restart-client-server"):
            # 전체 재시작: 서버는 work-preserving re-exec(셸/세션 보존), 동시에
            # 클라이언트도 자신을 relaunch(새 클라 코드로 재attach). 서버/클라
            # 코드를 모두 갱신하면서 작업은 보존한다(docs/internal/RESTART_SCENARIO.md).
            # 실행 전 드라이런으로 안전성을 먼저 점검한다.
            self.begin_restart("all")
        elif c in ("reconnect", "resync"):
            # IPC 강제 재접속(§10): degraded(빨간 외곽선) 고착 시 정체된 소켓을
            # 버리고 새로 세워 회복한다. 서버 PTY/세션·실행 중 Claude 는 보존.
            self.reconnect_now("manual")
        elif c in ("redraw", "refresh", "refresh-client"):
            # 화면 전체 강제 재그리기(§2.12, tmux refresh-client/Ctrl-L 해당): 여러
            # 상황(alt-screen 앱이 repaint 안 함·합성 스냅샷 stale·터미널 깨짐·원격
            # 잔상)에서 화면이 정상 재그리기 안 될 때 회복한다. 서버가 ① 각 패널 PTY 에
            # SIGWINCH 를 유발해 alt-screen 앱(vim/claude/htop)이 전체 repaint 하게 하고
            # ② 이 클라에 layout+screen 전체 프레임을 다시 보낸다(stale 스냅샷 교체).
            # 클라도 자기 합성을 즉시 다시 돌려 순수 클라측 잔상도 지운다. 원격 탭을
            # 보는 중이면 서버가 업스트림으로 릴레이해 원격 화면이 재그려진다.
            self.send_cmd("request_redraw")
            self._composite()
            self.refresh()
        elif c in ("paste-clipboard", "pasteb-clip"):
            self.paste_os_clipboard()  # bracketed 패스스루
        elif c in ("send-keys", "send"):
            self._send_keys(args)
        elif c in ("send-escape", "send-esc"):
            # 활성 패널에 ESC 1회 전달(= send-keys Escape 의 한 토큰 단축).
            # 한 키에 바인딩하기 쉽게 별도 명령으로 노출 — Shift+ESC 가 안 먹는
            # 터미널에서 `bind-key <key> send-escape` 로 전용 키를 둘 수 있다.
            self.send_input(b"\x1b")
        elif c in ("paste-buffer", "pasteb"):
            idx = _first_int(args)
            self.send_cmd("paste_buffer", index=idx or 0)
        elif c in ("capture-pane", "capturep"):
            self.send_cmd("capture_pane", full=("-S" in args or "-a" in args))
        elif c in ("pipe-pane", "pipep"):
            self.send_cmd("pipe_pane",
                          cmd=" ".join(a for a in args if not a.startswith("-")))
        elif c == "save-layout":
            self.send_cmd("save_layout")
        elif c == "restore-layout":
            self.send_cmd("restore_layout")
        elif c in ("layout-save", "save-tab-layout"):
            name = " ".join(a for a in args if not a.startswith("-"))
            if name:
                self.send_cmd("save_tab_layout", name=name)
            else:
                self.save_layout_prompt()
        elif c in ("layout-load", "load-tab-layout"):
            name = " ".join(a for a in args if not a.startswith("-"))
            if name:
                self.send_cmd("load_tab_layout", name=name,
                              new=("-n" in args))
            else:
                self.request_layouts("new" if "-n" in args else "over")
        elif c in ("layout-load-new",):
            name = " ".join(a for a in args if not a.startswith("-"))
            if name:
                self.send_cmd("load_tab_layout", name=name, new=True)
            else:
                self.request_layouts("new")
        elif c in ("layout-list", "list-layouts"):
            self.request_layouts("over")
        elif c in ("choose-buffer", "list-buffers", "lsb"):
            self.choose_buffer()
        elif c in ("clear-history", "clearhist"):
            self.send_cmd("clear_history")
        # clock-mode/calendar-mode/open-clock/close-clock/open-calendar/
        # close-calendar(별칭 clock·calendar·cal·open-cal·close-cal)은 clock·
        # calendar 플러그인이 handle_command(위 폴백)로 처리한다.
        elif c in ("single-border", "pane-border"):
            # single-border [on|off|toggle] — 단일 패널 테두리 표시(기본 toggle).
            # 서버가 opts.json 에 영속하고 새 레이아웃을 다시 보낸다.
            arg = args[0].lower() if args else "toggle"
            val = (arg == "on") if arg in ("on", "off") \
                else (not self.single_border_on)
            self.single_border_on = val           # 낙관적 즉시 반영
            self.send_cmd("set_single_border", value=bool(val))
        elif c in ("coalesce-repaints", "coalesce"):
            # coalesce-repaints [on|off|toggle] — alt-screen 리페인트 합치기(§10 대응
            # ②). 서버 내부 동작이라 클라 상태/렌더 변화 없음 — on/off 는 그대로,
            # 인자 없으면 toggle(서버가 반전). 서버가 opts.json 에 영속.
            arg = args[0].lower() if args else "toggle"
            val = (arg == "on") if arg in ("on", "off") else None
            self.send_cmd("set_coalesce", value=val)
        elif c == "win-mouse-motion":
            # win-mouse-motion [on|off|toggle] — Windows 마우스 모션(any-motion)
            # 패스스루(HANDOFF §10-H). 기본 OFF: ConPTY 가 주입 SGR 모션을 소비 못 해
            # 프롬프트에 누출되므로 Windows 패널엔 any-motion 을 광고하지 않는다.
            # 서버 내부 동작이라 클라 상태 변화 없음. 서버가 opts.json 영속·재방송.
            arg = args[0].lower() if args else "toggle"
            val = (arg == "on") if arg in ("on", "off") else None
            self.send_cmd("set_win_mouse_motion", value=val)
        elif c == "nest-auto-attach":
            # nest-auto-attach [on|off|toggle] — 원격 중첩 자동 승격(NESTED_
            # ATTACH ㉢). 서버 내부 동작이라 클라 렌더 변화 없음. 서버가
            # opts.json 영속, 인자 없으면 toggle(서버가 반전).
            arg = args[0].lower() if args else "toggle"
            val = (arg == "on") if arg in ("on", "off") else None
            self.send_cmd("set_nest_auto_attach", value=val)
        elif c == "vt-parser":
            # vt-parser [pyte|native] — VT 파서 백엔드 선택(docs/VT_PARSER_TRADEOFF §8).
            # **재시작 시 발효**: 기존 패널은 즉시 안 바뀌고, 다음 작업 보존 재시작
            # (restart-server)·respawn 에서 새 백엔드를 채택한다. 서버가 opts.json 영속.
            val = (args[0].lower() if args else "")
            if val not in ("pyte", "native"):
                self.display_message(i18n.t("msg.vt_parser_usage"))
            else:
                self.send_cmd("set_vt_parser", value=val)
                self.display_message(i18n.t("msg.vt_parser_set", val=val))
        elif c in ("lang", "language"):
            # lang ko|en — UI 로케일 전환(§6 i18n). 클라이언트-로컬: 즉시 set_locale
            # +영속 후 전체 재합성으로 상태줄·헤더·메뉴를 새 언어로 다시 그린다(언어
            # 전환 자체가 즉시 보이는 피드백). 인자가 없거나 미지원이면 사용법 팝업.
            arg = args[0].lower() if args else ""
            if arg in i18n.available():
                self.lang = arg
                i18n.set_locale(arg)
                i18n.save_persisted(self.sock_path, arg)
                self._composite()
            else:
                self.push_screen(InfoScreen([i18n.t("lang.usage")],
                                            title="lang"))
        elif c in ("version", "about"):
            # 클라/서버 버전(p4 CL)·업타임 팝업.
            self.open_version()
        elif c in ("display-popup", "popup"):
            cmd = " ".join(a for a in args if not a.startswith("-"))
            if cmd:
                try:
                    res = subprocess.run(_shell_argv(cmd),
                                         capture_output=True, timeout=30,
                                         **proc.no_window_kwargs())
                    text = (res.stdout + res.stderr).decode("utf-8", "ignore")
                except (OSError, subprocess.SubprocessError) as e:
                    text = str(e)
                self.push_screen(InfoScreen(
                    text.splitlines()[:60] or [i18n.t("msg.display_no_output")],
                    title="popup"))
            else:
                self.push_screen(InfoScreen(["display-popup <command>"],
                                            title="popup"))
        elif c in ("source-file", "source"):
            self.reload_config(args[0] if args else None)
        elif c in ("set", "set-option"):
            opts = [a for a in args if not a.startswith("-")]
            if len(opts) >= 2:
                self.apply_option(opts[0], " ".join(opts[1:]))
        elif c in ("show-options", "show"):
            self.show_options()
        elif c == "set-hook":
            if "-u" in args and len(args) >= 2:
                self.hooks.pop(args[args.index("-u") + 1], None)
            else:
                opts = [a for a in args if not a.startswith("-")]
                if len(opts) >= 2:
                    self.hooks[opts[0]] = " ".join(opts[1:])
        elif c in ("display-message", "display", "displaym"):
            self.display_message(" ".join(args) if args else "")
        elif c == "show-hooks":
            self.push_screen(InfoScreen(
                [f"{k} → {v}" for k, v in self.hooks.items()], title="hooks"))
        elif c in ("bind-key", "bind", "bindkey"):
            # bind-key <key> <command...> — prefix 후 <key> 에 명령 바인딩(런타임).
            # bind-key -n <key> <command...> — root table(§2.5, prefix 없이 바로).
            # 키는 tmux 표기(C-x)도 받아 textual(ctrl+x)로 정규화. 한 글자는 그대로.
            # 첫 인자만 키, 나머지는 명령 원문(플래그 -h 등 보존)으로 그대로 쓴다.
            root = bool(args) and args[0] == "-n"
            rest = args[1:] if root else args
            if len(rest) >= 2:
                key, warn = normalize_binding_key(rest[0])
                table = self.root_bindings if root else self.bindings
                table[key] = " ".join(rest[1:])
                self.display_message(
                    warn if warn else
                    (f"bound (root) {key}" if root else f"bound {key}"))
        elif c in ("unbind-key", "unbind", "unbindkey"):
            # unbind-key <key> | -n <key>(root) | -a (양 테이블 전체 해제).
            # 없는 키는 조용히 무시.
            if "-a" in args:
                n = len(self.bindings) + len(self.root_bindings)
                self.bindings.clear()
                self.root_bindings.clear()
                self.display_message(f"unbound all ({n})")
            else:
                root = args[:1] == ["-n"]
                pos = [a for a in args if not a.startswith("-")]
                if pos:
                    key = _tmux_key_to_textual(pos[0])
                    table = self.root_bindings if root else self.bindings
                    if table.pop(key, None) is not None:
                        self.display_message(f"unbound {key}")
                    else:
                        self.display_message(f"no binding: {key}")
        elif c in ("list-keys", "lsk", "list-binds", "mouse-help", "mouse"):
            # §2.2 발견성: 구현된 마우스 제스처(헤더 드래그 pick-up→swap/탭이동,
            # 탭 드래그 재정렬·분할, Shift+드래그 선택 등)는 명령이 아니라 ?목록·
            # 메뉴 어디에도 안 떠 사장돼 있었다. list-keys 가 사용자 바인딩과 함께
            # 1급 마우스 제스처를 먼저 보여 노출한다(동작 변경 없음, 표시만 추가).
            tr = i18n.t
            lines = [tr("keys.mouse_header", default="마우스 제스처")]
            lines += ["  " + tr(k, default=d) for k, d in (
                ("keys.g_click", "휠 — 스크롤백 스크롤 · 클릭 — 패널 포커스"),
                ("keys.g_rclick", "우클릭 — 패널 메뉴(분할·줌·회전·삭제…)"),
                ("keys.g_divider", "경계선 드래그 — 패널 크기 조절"),
                ("keys.g_header", "패널 헤더(위 테두리) 드래그 — 패널을 들어 "
                 "다른 패널과 swap · 탭으로 이동 · [+]에 놓아 새 탭"),
                ("keys.g_shift", "Shift+드래그 — 텍스트 선택(클립보드 복사)"),
                ("keys.g_tab", "탭 드래그 — 탭 재정렬 · 패널 위로 끌어 분할"),
            )]
            binds = [f"prefix {k} → {v}"
                     for k, v in sorted(self.bindings.items())]
            binds += [f"(root) {k} → {v}"
                      for k, v in sorted(self.root_bindings.items())]
            lines += ["", tr("keys.user_header", default="사용자 키 바인딩")]
            lines += binds or ["  " + tr("keys.none", default="(없음)")]
            self.push_screen(InfoScreen(
                lines, title=tr("keys.title", default="키 · 마우스")))
        # 알 수 없는 명령은 조용히 무시
