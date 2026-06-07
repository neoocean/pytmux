"""셸 PTY 를 소유하는 백그라운드 서버(데몬)."""
from __future__ import annotations

import asyncio
import base64
import gc
import json
import os
import shlex
import subprocess
import time
import traceback

from . import ipc, proc, pty_backend, sshwrap, tokens, usagedb, usagelog, version
from .model import (ClientConn, Pane, Session, Split, Tab, Window,
                    coalesce_alt_repaints)
from .claude import (claude_account, claude_feedback_prompt, claude_perm_mode,
                     claude_prompt, claude_state, claude_usage, parse_reset_delay)
from .protocol import (FEED_SLICE, FLUSH_HZ, MIN_H, MIN_W, read_msg, write_msg)
from .serverclaude import (ServerClaudeMixin, _CAM_MAX, _HDR_CLAUDE_MISS,
                            _DONE_IDLE_FRAMES)
from .servercapture import ServerCaptureMixin
from .serverpersist import ServerPersistMixin
from .serverpty import ServerPtyMixin
from .serverio import ServerIOMixin
from .servertree import ServerTreeMixin

# pytmux 프로젝트 루트(= pytmuxlib 패키지의 상위). 캡처 출력 등 "프로젝트에 영속해
# Perforce 로 공유할" 산출물의 기준 경로다. proc.server_argv 의 entry 추정과 동일 규칙.
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))



class Server(ServerClaudeMixin, ServerCaptureMixin, ServerPersistMixin,
             ServerPtyMixin, ServerIOMixin, ServerTreeMixin):
    def __init__(self, sock_path: str, resume_path: str | None = None):
        self.sock_path = sock_path
        # 작업 보존 재시작(re-exec) 후 부트: 이 경로의 상태 파일로 상속된 PTY 를
        # 채택해 셸을 살린 채 복원한다(serve()). None 이면 평소 부트.
        self._resume_path = resume_path
        # 패널 셸 $PYTMUX 에 심을 엔드포인트. serve() 가 listen 을 시작하면 TCP
        # 에페메럴(포트 0)은 확정 포트로 갱신된다. 바인드 전(테스트 등)엔 입력값 그대로.
        self.resolved_endpoint = sock_path
        self.sessions: dict[str, Session] = {}
        self.clients: list[ClientConn] = []
        self.loop: asyncio.AbstractEventLoop | None = None
        self.running = True
        # version 명령 팝업용: 이 프로세스가 **로드한 코드**의 버전(p4 CL)과 부팅
        # 시각. re-exec 후엔 새 프로세스라 둘 다 다시 캡처된다(= 데몬 업타임은 마지막
        # (re-)exec 기준). 버전 캡처는 ~수십 ms p4 호출이지만 부팅 1회뿐이라 무해
        # (클라의 textual import 와 겹쳐 체감 영향 없음).
        self._boot_time = time.time()
        self._code_version = version.code_version()
        # 연결 인증 토큰(F1). serve() 가 listen 전에 무작위 토큰을 생성·게시(0600)하고
        # 채운다. None 인 동안(serve 미호출 단위 테스트)은 handle_client 가 검증을
        # 건너뛴다 — 실제 데몬은 항상 serve() 를 거치므로 토큰이 강제된다.
        self.auth_token: str | None = None
        # 대량 출력 드레인 중 순환 GC 일시정지 가드(§10 프로파일링): pyte feed 는 셀마다
        # `Char` 네임드튜플을 새로 할당해 버스트 한 번에 수백만 객체가 생긴다. 순환 GC 가
        # 이를 주기적으로 훑으면 단일 슬라이스가 30~85ms 멈춰(측정) 이벤트 루프가 끊기고
        # 입력이 뚝뚝 끊긴다. drain 이 도는 동안만 GC 를 끄고(_gc_drain_depth 0→1) 모든
        # drain 이 끝나면 다시 켜 1회 collect 한다(1→0). Char 는 불변값이라 순환이 없어
        # refcount 만으로 회수되므로 드레인 창 동안 누수 위험은 낮다.
        self._gc_drain_depth = 0
        self._gc_was_enabled = True
        self._session_seq = 0
        # Claude 세션 일련번호(#7 토큰 로깅): 패널의 claude None→비None 전이마다 +1.
        self._claude_session_seq = 0
        # 토큰 사용량 SQLite 연결(지연 오픈, _tokens_db_conn). 최초 사용 시 열고
        # 기존 JSONL 이력을 일회 임포트한다(docs/TOKEN_USAGE_STORAGE_DESIGN.md).
        self._tokens_db = None
        self.buffers: list[str] = []   # 페이스트 버퍼(최신이 앞)
        # 패널 출력 캡처(Claude 화면 문구 분석용). **기본 ON** — 실 Claude Code
        # 출력을 무손실 기록해 limit/busy/idle/ctx 화면 골든 픽스처·휴리스틱
        # 보강의 객관 근거로 쓴다(IMPROVEMENT §3.2, TOKEN_SAVING M8). opts.json
        # 에 영속하므로 사용자가 capture-output off 로 끄면 그 선택이 유지된다.
        self._capfiles: dict[int, "object"] = {}   # pane.id -> 열린 바이너리 파일
        _opts = self._load_opts()
        self.capture = bool(_opts.get("capture", True))   # 기본 ON
        # Claude 프롬프트 헤더 전역 표시(#6 ③ opts.json 영속). 클라가 status 로 받아
        # claude_header_on 에 반영하고, `claude-header on|off` 가 서버를 거쳐 갱신·영속.
        self.claude_header = bool(_opts.get("claude_header", True))
        # 패널이 하나뿐일 때 테두리(아웃라인)를 그릴지(기본 ON=항상 테두리).
        # off 면 단일 패널은 테두리 없이 화면 전체를 내용으로 쓴다. opts.json 영속.
        self.single_border = bool(_opts.get("single_border", True))
        # alt-screen 풀스크린 리페인트 코얼레싱(#§10 대응 ②). 켜면 Claude busy 스피너
        # 등 매 프레임 화면을 통째로 다시 그리는 대량 출력이 feed 보다 빠르게 쌓일 때
        # 무효화된 중간 프레임을 버려 feed 부하/지연을 줄인다(안전 조건은
        # coalesce_alt_repaints 참조 — alt-screen 한정·무손실). 기본 ON, opts.json 영속.
        self.coalesce_repaints = bool(_opts.get("coalesce_repaints", True))
        # 프롬프트 단위 클리어 모드(#9)의 ① 문서화 지시문. 패널 안 Claude 에게 보내는
        # 슬래시/지시문이며(pytmux 명령 아님), 무엇을 어디에 기록할지는 Claude 쪽
        # 프로젝트 관례(CLAUDE.md/메모리)에 맡긴다. opts.json 영속.
        self.prompt_clear_message = str(_opts.get(
            "prompt_clear_message",
            "이번 세션에서 얻은 정보·결정을 프로젝트 문서(CLAUDE.md/메모리)에 기록해줘."))
        # 자동 doc→/clear(§10): Claude 가 작업을 끝내고(busy→idle) 그 상태로
        # auto_doc_clear_delay 초 지속되면(사용자 개입 없이) prompt_clear_message →
        # /clear 를 1회 자동 주입한다. 기본 OFF(명시 토글 필요). limit 상태/사용자
        # 입력/재busy 시엔 발화하지 않는다. opts.json 영속.
        self.auto_doc_clear = bool(_opts.get("auto_doc_clear", False))
        self.auto_doc_clear_delay = float(_opts.get("auto_doc_clear_delay", 30.0))
        # 권한모드 자동 오토모드 전환(§10): Claude 패널이 idle 이고 권한모드 footer 가
        # auto(자동 수락)가 아니면 shift+tab 을 순환 주입해 auto 로 맞춘다. 기본 OFF.
        # bypass(권한 우회) 모드는 명시적·위험 설정이라 건드리지 않는다. opts.json 영속.
        self.claude_auto_mode = bool(_opts.get("claude_auto_mode", False))
        # 새 Claude 세션 자동 셋업(요청): Claude Code 가 패널에서 새로 뜨면(None→Claude)
        # 첫 idle 에 ① `/rc` 를 1회 주입해 원격 제어(리모트 커넥션)를 켜고 ② 권한모드를
        # auto 로 1회 유도한다. claude_auto_mode(상시 강제)와 달리 **세션 시작 1회**만
        # 작용한다(이후 사용자가 바꾸면 안 건드림). `/rc` 는 이미 원격제어가 켜진 화면
        # (claude_remote_active)에선 건너뛰어 도로 끄지 않는다. 기본 ON. opts.json 영속.
        self.claude_auto_launch = bool(_opts.get("claude_auto_launch", True))
        # Claude Code 시작 규칙(#27): 사용자가 에디터 팝업으로 저장해 둔 "항상 지킬
        # 규칙" 텍스트. 새 Claude 세션이 뜨면(또는 pytmux 가 /clear 한 뒤) 이 텍스트를
        # 프롬프트에 주입한다(빈 문자열이면 아무것도 안 함). opts.json 영속.
        self.claude_rules = str(_opts.get("claude_rules", ""))
        # ---- 토큰 절감 자동화(docs/TOKEN_SAVING_SCENARIO.md) — 모두 전역·영속 ----
        # M11 컨텍스트 잔량 기반 자동 정리: idle 패널의 컨텍스트 잔량(claude_context_pct)
        # 이 claude_ctx_threshold(%) 밑으로 떨어지면 1회 정리한다. action=정리 방식
        # ("compact"=네이티브 /compact 요약, 연속성 유지 / "doc-clear"=문서화→/clear
        # 완전 초기화). 기본 OFF·기본 /compact(비가역성 낮음). idle/발화직전 재확인/
        # 최근입력 연기/busy 취소 게이트는 _scan_claude 가 적용(§5).
        self.claude_ctx_autoclear = bool(_opts.get("claude_ctx_autoclear", False))
        self.claude_ctx_threshold = int(_opts.get("claude_ctx_threshold", 15))
        self.claude_ctx_action = str(_opts.get("claude_ctx_action", "compact"))
        # M14 정리 빈도 상한(초): 직전 자동 정리로부터 이 시간이 지나야 다시 정리한다
        # (0=상한 없음). _ctx_fired 디바운스(잔량 회복까지 1회)에 더해, 정리가 잔량을
        # 못 늘리는 오검출·병적 진동에서 매 완료경계 무한 정리를 막는 시간 바닥(§5.6).
        self.claude_ctx_min_interval = float(
            _opts.get("claude_ctx_min_interval", 120.0))
        # M10 토큰 예산: 일/세션 누계가 이 값을 넘으면 경고(0=무제한). 누계는 화면
        # 토큰 합(best-effort)이라 하드 차단이 아니라 알림·자동개입 보류용이다(§5.5).
        self.token_budget_day = int(_opts.get("token_budget_day", 0))
        self.token_budget_session = int(_opts.get("token_budget_session", 0))
        # M18-B 5시간 한도 근접도 표시의 분모. 0=설정 안 함 → 분모로 limit 관측
        # 학습치(_learned_5h_cap)를 쓰고, 그것도 없으면 %를 숨긴다(지어내지 않음).
        self.token_budget_5h = int(_opts.get("token_budget_5h", 0))
        # limit 진입 시점의 계정 누계로 5h 상한을 학습(in-memory, best-effort).
        self._learned_5h_cap = 0
        # M19 그림자 /usage 질의 결과(세션·주간 한도 %·리셋). dict|None. 수동 갱신.
        self._usage = None
        self._usage_busy = False   # 질의 진행 중(중복 방지)
        # M15 계정 합계 예산(0=무제한). 세션 예산은 패널 단독을 보지만(_budget_over),
        # 토큰은 계정 단위로 청구되므로 같은 계정 N개 세션의 합계(_account_token_total)
        # 가 이 값을 넘으면 경고·자동개입 보류. 멀티세션 누적(S7) 대응.
        self.token_budget_account = int(_opts.get("token_budget_account", 0))
        # M17(T7) 표시 경고 임계(grade0 알림만). long_turn=한 턴 busy 지속 한계(초,
        # 0=끔), repeat=동일 완료 출력 반복 횟수(0=끔). 상태줄 ⚠배지로만 알린다.
        self.claude_long_turn_sec = int(_opts.get("claude_long_turn_sec", 600))
        self.claude_repeat_alert = int(_opts.get("claude_repeat_alert", 3))
        # M12 자동재개 예산 게이트: 켜면 예산 초과 시 자동재개(continue 주입)를 보류
        # 한다(사용자 수동 재개는 가능). 기본 OFF(autoresume 동작 불변).
        self.token_budget_resume_gate = bool(
            _opts.get("token_budget_resume_gate", False))
        # M13 예산 압박 시 plan 유도: 켜면 예산 경고(≥80%) + idle 일 때 권한모드를
        # plan 으로 폐루프 유도해(편집 전 검토 → 맹목 도구 호출 감소) 토큰 소모를
        # 늦춘다(가역 — 사용자가 shift+tab 으로 되돌림). bypass(명시적 위험)는 불간섭.
        # claude_auto_mode(auto 유도)와 상충하면 예산 압박 시 plan 이 우선. 기본 OFF.
        self.claude_budget_plan = bool(_opts.get("claude_budget_plan", False))
        # M10 일 예산 누계(in-memory, best-effort). 첫 확정 이벤트에서 로그로부터
        # 오늘 누계를 시드(재시작 정합)하고 이후 메모리로 증분, 자정 넘김 시 0 리셋.
        # _budget_level: 일 예산 기준 경고 레벨(0/80/100), status 로 클라에 전달.
        self._today_tokens = None
        self._today_key = None
        self._budget_level = 0

    # ---- 데몬 부트스트랩 세션 ----
    def ensure_default_session(self, cols: int, rows: int) -> Session:
        if self.sessions:
            return next(iter(self.sessions.values()))
        root = self.spawn_pane(cols, max(MIN_H, rows))
        name = str(self._session_seq)
        self._session_seq += 1
        sess = Session(name, root)
        self.sessions[name] = sess
        return sess

    def _unique_name(self, name: str | None) -> str:
        if not name:
            name = str(self._session_seq)
            self._session_seq += 1
            while name in self.sessions:
                name = str(self._session_seq)
                self._session_seq += 1
            return name
        if name not in self.sessions:
            return name
        i = 1
        while f"{name}-{i}" in self.sessions:
            i += 1
        return f"{name}-{i}"

    def new_session(self, cols: int, rows: int, name: str | None = None) -> Session:
        root = self.spawn_pane(cols, max(MIN_H, rows))
        uname = self._unique_name(name)
        sess = Session(uname, root)
        self.sessions[uname] = sess
        return sess

    def get_or_create_session(self, name: str | None, cols: int, rows: int) -> Session:
        """단일 세션 모델: 요청 이름과 무관하게 항상 하나의 기본 세션에 attach 한다.

        멀티 세션 개념을 사용자 표면에서 제거했으므로(최상위는 탭), 클라이언트의
        세션 이름 요청은 무시하고 단일 세션을 보장/반환한다."""
        return self.ensure_default_session(cols, rows)

    @staticmethod
    def _pane_text_lines(pane: Pane):
        screen = pane.screen
        h = getattr(screen, "history", None)
        hist = list(h.top) if h is not None else []
        full = hist + [screen.buffer[y] for y in range(screen.lines)]
        return ["".join((line[x].data or " ")
                        for x in range(screen.columns)).rstrip()
                for line in full]

    def set_buffer(self, text: str):
        if not text:
            return
        self.buffers.insert(0, text)
        del self.buffers[50:]

    @staticmethod
    def _write_paste(pane: Pane, text: str):
        """텍스트를 패널에 입력. 내부 앱이 bracketed paste 를 켰으면 마커로 감싼다
        (멀티라인 붙여넣기가 줄마다 실행되지 않고 한 번의 붙여넣기로 처리됨)."""
        data = text.encode("utf-8")
        # 붙여넣기(모바일 받아쓰기·자동완성 포함)도 프롬프트 추적에 반영한다.
        # 이게 없으면 붙여넣은 Claude 프롬프트는 last_prompt 에 안 잡혀 헤더가
        # 셸 실행 명령("claude")에 머문다. 이후 사용자가 Enter(\r) 를 누르면
        # 누적된 본문이 last_prompt 로 확정된다(개행 포함 시 즉시 확정).
        Server._track_prompt(pane, data)
        if pane.bracketed:
            data = b"\x1b[200~" + data + b"\x1b[201~"
        try:
            if pane.pty is not None:
                pane.pty.write(data)
        except OSError:
            pass

    def _reset_view(self, pane: Pane):
        if pane.scroll or pane._match_abs is not None:
            pane.scroll = 0
            pane._match_abs = None
            pane.dirty = True

    def paste_text(self, sess: Session, text: str):
        win = sess.active_window
        if not win or not text:
            return
        self._reset_view(win.active_pane)
        self._write_paste(win.active_pane, text)

    def paste_buffer(self, sess: Session, index=0):
        win = sess.active_window
        if not win or not self.buffers:
            return
        if not (0 <= index < len(self.buffers)):
            index = 0
        self._reset_view(win.active_pane)
        self._write_paste(win.active_pane, self.buffers[index])

    # ---- 토큰 사용량 영속 저장(#7, SQLite) ----
    @property
    def tokens_log_path(self) -> str:
        """(레거시) 응답별 확정 토큰의 JSONL 로그 경로. 이제 저장은 SQLite(tokens_db_path)
        로 옮겼고, 이 경로는 **기존 이력을 DB 로 일회 임포트**하는 원본으로만 남는다
        (휘발 영역 state_base, docs/TOKEN_USAGE_STORAGE_DESIGN.md §5)."""
        return ipc.state_base(self.sock_path) + ".tokens.jsonl"

    @property
    def tokens_db_path(self) -> str:
        """토큰 사용량 SQLite DB. 기본 소켓은 프로젝트 하위 `db/claude-tokens.db`,
        그 외(임시 소켓 등)는 `db/claude-tokens-<sock-id>.db` 로 격리한다. captures 와
        달리 raw 화면 잔재가 없는 집계 데이터지만, db/ 전체를 .gitignore·p4ignore 로
        버전관리에서 제외한다(런타임·호스트 로컬, 사용자 결정 2026-06-07).
        PYTMUX_TOKENS_DB 로 강제 지정 가능(테스트가 임시 파일을 주입해 오염 방지)."""
        override = os.environ.get("PYTMUX_TOKENS_DB")
        if override:
            return override
        from .servercapture import PROJECT_DIR
        base = os.path.join(PROJECT_DIR, "db")
        if self.sock_path == ipc.default_endpoint():
            return os.path.join(base, "claude-tokens.db")
        return os.path.join(base, f"claude-tokens-{self._capture_id()}.db")

    def _tokens_db_conn(self):
        """토큰 DB 연결(최초 1회 열고 보관). 새(빈) DB 이고 기존 JSONL 이력이 있으면
        일회 임포트해 누적 통계를 보존하고, 재임포트 방지로 JSONL 을 `.imported` 로
        옮긴다. 실패해도 None 을 돌려 본 흐름(토큰 로깅)을 막지 않는다."""
        if self._tokens_db is not None:
            return self._tokens_db
        try:
            conn = usagedb.connect(self.tokens_db_path)
        except Exception:
            return None
        try:
            if usagedb.count(conn) == 0:
                old = self.tokens_log_path
                if os.path.exists(old) and usagedb.import_jsonl(conn, old) > 0:
                    try:
                        os.replace(old, old + ".imported")
                    except OSError:
                        pass
        except Exception:
            pass
        self._tokens_db = conn
        return conn

    def _tab_index_of(self, sess: Session, pane: Pane):
        for i, tab in enumerate(sess.tabs):
            if pane in tab.window.panes():
                return i
        return None

    def _log_tokens(self, sess: Session, tab: Tab, pane: Pane, amount: int):
        """응답 한 건의 확정 토큰을 SQLite 에 적는다(tokens.step committed>0 이벤트)."""
        # M10 예산 추적은 insert **전에** — 시드(_seed_today_from_log)가 이번 레코드를
        # 아직 포함하지 않은 상태에서 prior 누계를 읽고 amount 를 더하게 해 이중계산을
        # 피한다(insert 후 시드하면 방금 쓴 레코드가 시드에 섞여 중복).
        self._budget_track(amount)
        rec = usagelog.make_record(
            ts=time.time(), tab=tab.index, pane=pane.id,
            session=pane._claude_session_id, account=pane._claude_account,
            tokens=amount)
        conn = self._tokens_db_conn()
        if conn is not None:
            usagedb.insert(conn, rec)

    def _seed_today_from_log(self, key: str) -> int:
        """오늘 버킷 키에 해당하는 토큰 합(서버 기동 시 1회 시드용, M10). SQL 합산."""
        conn = self._tokens_db_conn()
        if conn is None:
            return 0
        try:
            return usagedb.total_for_day(conn, key)
        except Exception:
            return 0

    def _budget_track(self, amount: int):
        """확정 토큰을 오늘 누계에 더하고 일 예산 경고 레벨을 갱신한다(M10).
        예산이 둘 다 0(무제한)이면 누계 추적을 건너뛰고 레벨 0."""
        if self.token_budget_day <= 0 and self.token_budget_session <= 0:
            self._budget_level = 0
            return
        key = usagelog.bucket_key(time.time(), "day")
        if self._today_key != key:
            # 첫 호출(기동)이면 로그에서 오늘 누계 시드 — 재시작 후에도 일 예산이
            # 이어진다. 자정 넘김(_today_key 이미 있음)이면 새 날이라 0 에서 시작.
            self._today_tokens = (self._seed_today_from_log(key)
                                  if self._today_key is None else 0)
            self._today_key = key
        self._today_tokens += max(0, int(amount))
        self._refresh_budget_level()

    def _refresh_budget_level(self):
        """일 예산 대비 경고 레벨(0/80/100)을 _budget_level 에 캐시한다."""
        lvl = 0
        day = self.token_budget_day
        if day > 0 and self._today_tokens is not None:
            if self._today_tokens >= day:
                lvl = 100
            elif self._today_tokens >= day * 0.8:
                lvl = 80
        self._budget_level = lvl

    def set_claude_account(self, sess: Session, name: str):
        """활성 패널의 Claude 계정을 수동 지정(화면 휴리스틱이 못 잡을 때 보정, #7 ②).
        빈 문자열이면 수동 지정 해제(자동 감지로 복귀)."""
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        name = (name or "").strip()
        if name:
            p._claude_account = name
            p._claude_account_manual = True
        else:
            p._claude_account_manual = False
            p._claude_account = None


    def set_claude_header(self, value=None):
        """Claude 프롬프트 헤더 전역 표시 토글. 상태를 opts.json 에 영속(#6 ③)."""
        self.claude_header = (not self.claude_header) if value is None \
            else bool(value)
        self._save_opts()
        return self.claude_header

    def set_single_border(self, value=None):
        """단일 패널 테두리 표시 토글. value 미지정 시 반전. opts.json 영속."""
        self.single_border = (not self.single_border) if value is None \
            else bool(value)
        self._save_opts()
        return self.single_border

    def set_coalesce_repaints(self, value=None):
        """alt-screen 리페인트 코얼레싱 토글(§10 대응 ②). value 미지정 시 반전.
        opts.json 영속. 클라 렌더에는 영향 없는 서버 내부 동작이라 status 불필요."""
        self.coalesce_repaints = (not self.coalesce_repaints) if value is None \
            else bool(value)
        self._save_opts()
        return self.coalesce_repaints

    def list_tab_layouts(self) -> list[str]:
        return sorted(self._load_slots().keys())

    def save_tab_layout(self, sess: Session, name: str) -> bool:
        """활성 탭의 윈도우+패널 레이아웃을 이름 슬롯으로 저장."""
        tab = sess.active_tab
        if not tab or not name:
            return False
        slots = self._load_slots()
        slots[name] = self._serialize_node(tab.window.root)
        self._save_slots(slots)
        return True

    def load_tab_layout(self, sess: Session, name: str,
                        new_tab: bool = False) -> bool:
        """저장된 레이아웃을 현재 탭에 덮어쓰거나(new_tab=False) 새 탭으로 연다."""
        spec = self._load_slots().get(name)
        if not spec:
            return False
        c = self.clients_of(sess)
        cols = c.cols if c else 80
        rows = c.rows if c else 24
        root = self._build_node(spec, cols, rows)
        win = Window(root)
        win._active = root if isinstance(root, Pane) else root.first_pane()
        win._fix_parents(root, None)
        if new_tab:
            idx = len(sess.tabs)
            sess.tabs.append(Tab(idx, name, win))
            sess.last_index = sess.active_index
            sess.active_index = idx
        else:
            old = sess.active_tab
            if old is None:
                return False
            for p in old.window.panes():   # 기존 패널 정리 후 교체
                self._destroy_pane_proc(p)
            old.window = win
        return True

    @staticmethod
    def _arg_onoff(args):
        """control 토글 인자 파싱: 'on'→True, 'off'→False, 그 외→None(현재값 토글).
        capture/claude-header/single-border/coalesce/auto-doc-clear/auto-mode 공용."""
        return True if "on" in args else (False if "off" in args else None)

    # `pytmux cmd <명령> [on|off]` 의 **즉시 "on"/"off" 반환** 토글 표(#5.9 — 종전
    # 6벌 복붙 elif 를 dict 조회 한 줄로 일원화). 값은 set_* setter 메서드 이름이며
    # bool(현재 상태)을 돌려준다. single-border 는 레이아웃(박스 유무)이 바뀌어
    # broadcast 가 필요하므로 이 표가 아니라 별도 분기로 둔다.
    _ONOFF_CONTROLS = {
        "capture-output": "set_capture", "capture-toggle": "set_capture",
        "claude-header": "set_claude_header",
        "coalesce-repaints": "set_coalesce_repaints",
        "coalesce": "set_coalesce_repaints",
        "auto-doc-clear": "set_auto_doc_clear", "auto-doc": "set_auto_doc_clear",
        "claude-auto-mode": "set_claude_auto_mode",
        "auto-mode": "set_claude_auto_mode",
        "claude-auto-launch": "set_claude_auto_launch",
        "auto-launch": "set_claude_auto_launch",
        # 토큰 절감 on/off 토글의 외부 cmd 파리티(설정 팝업과 같은 setter).
        "ctx-autoclear": "set_claude_ctx_autoclear",
        "resume-gate": "set_token_budget_resume_gate",
        "budget-plan": "set_claude_budget_plan",
    }

    def handle_control(self, line: str):
        """외부 CLI(`pytmux cmd ...`)에서 보낸 명령을 서버 측에서 처리한다."""
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if not parts:
            return "empty"
        c, args = parts[0], parts[1:]

        def opt(flag):
            return args[args.index(flag) + 1] if flag in args and \
                args.index(flag) + 1 < len(args) else None

        def first_int():
            for a in args:
                if a.lstrip("-").isdigit():
                    return int(a.lstrip("-"))
            return None
        tname = opt("-t")
        sess = self.sessions.get(tname) if tname else next(
            iter(self.sessions.values()), None)
        if c in ("new-session", "new"):
            self.new_session(80, 24, opt("-s"))
        elif sess is None:
            return "no session"
        elif c in self._ONOFF_CONTROLS:          # 즉시 on/off 반환 토글(#5.9 표)
            setter = getattr(self, self._ONOFF_CONTROLS[c])
            return "on" if setter(self._arg_onoff(args)) else "off"
        elif c in ("new-window", "neww", "new-tab", "newt"):
            # -c <경로> 로 시작 디렉토리 지정(tmux 호환). 없으면 default-path 기본.
            self.new_window(sess, path=opt("-c"))
        elif c in ("kill-window", "killw", "kill-tab", "killt"):
            self.kill_window(sess)
        elif c in ("split-window", "splitw"):
            # tmux 규약: -h = 좌우(lr), -v/기본 = 상하(tb), -c <경로> = 시작 디렉토리.
            self.split_pane(sess, "lr" if "-h" in args else "tb", path=opt("-c"))
        elif c in ("select-window", "selectw", "select-tab", "selectt"):
            i = first_int()
            if i is not None:
                self.select_window(sess, i)
        elif c in ("rename-window", "renamew", "rename-tab", "renamet"):
            self.rename_window(sess, " ".join(a for a in args
                                              if not a.startswith("-")))
        elif c in ("move-tab-left", "move-tab-right",
                   "move-tab-first", "move-tab-last"):
            self.move_current_tab(sess, c[len("move-tab-"):])
        elif c in ("layout-save", "save-tab-layout"):
            self.save_tab_layout(sess, " ".join(a for a in args
                                                 if not a.startswith("-")))
        elif c in ("layout-load", "load-tab-layout"):
            nm = " ".join(a for a in args if not a.startswith("-"))
            self.load_tab_layout(sess, nm, new_tab=("-n" in args))
        elif c in ("kill-session", "kills"):
            self.kill_session(tname or sess.name)
        elif c == "kill-server":
            self._notify_no_sessions()
            return "ok"
        elif c in ("restart-server", "restart"):
            # 작업 보존 재시작(re-exec). 셸/PTY 보존(docs/RESTART_SCENARIO.md).
            return "restarting" if self.restart_server() else "unsupported"
        elif c in ("send-keys", "send"):
            self._control_send_keys(sess, args)
        elif c in ("single-border", "pane-border"):
            # 레이아웃(박스 유무)이 바뀌므로 _ONOFF_CONTROLS 표(즉시 반환)가 아니라
            # 여기서 처리해 아래 broadcast 로 떨어지게 한다.
            self.set_single_border(self._arg_onoff(args))
        else:
            return f"unknown: {c}"
        for cl in list(self.clients):
            asyncio.create_task(self._send_full(cl))
        return "ok"

    @staticmethod
    def _control_send_keys(sess: Session, args):
        sp = {"Enter": b"\r", "Tab": b"\t", "Space": b" ", "Escape": b"\x1b",
              "BSpace": b"\x7f"}
        win = sess.active_window
        if not win:
            return
        out = b""
        for a in (x for x in args if not x.startswith("-")):
            if a in sp:
                out += sp[a]
            elif a.startswith("C-") and len(a) == 3 and a[2].isalpha():
                out += bytes([ord(a[2].lower()) - 96])
            else:
                out += a.encode("utf-8")
        if out:
            ap = win.active_pane
            try:
                if ap is not None and ap.pty is not None:
                    ap.pty.write(out)
            except OSError:
                pass

    def pipe_pane(self, sess: Session, cmd: str):
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        if p.pipe_proc:  # 토글/재시작: 기존 파이프 종료
            try:
                p.pipe_proc.stdin.close()
            except OSError:
                pass
            try:
                p.pipe_proc.terminate()
            except OSError:           # 이미 죽음(ProcessLookupError) 등
                pass
            p.pipe_proc = None
        if cmd:
            try:
                # no_window_kwargs: Windows 에서 pipe-pane 의 cmd /c 콘솔 창 안 뜨게(§10)
                p.pipe_proc = subprocess.Popen(proc.shell_argv(cmd),
                                               stdin=subprocess.PIPE,
                                               **proc.no_window_kwargs())
            except (OSError, ValueError):   # 명령 없음/인자 오류 — 조용히 실패 말고 로그
                p.pipe_proc = None
                self._log_error("pipe_pane_spawn")

    def capture_pane(self, sess: Session, full=False):
        win = sess.active_window
        if not win or not win.active_pane:
            return 0
        p = win.active_pane
        texts = self._pane_text_lines(p)
        if not full:
            texts = texts[-p.screen.lines:]
        text = "\n".join(texts).rstrip("\n")
        self.set_buffer(text)
        return len(text)

    def clear_history(self, sess: Session):
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        try:  # 메인 스크롤백을 비운다(대체 화면 중이어도)
            p._main.history.top.clear()
            p._main.history.bottom.clear()
        except AttributeError:   # pyte 내부 구조 방어(history 없는 화면 등)
            pass
        p.scroll = 0
        p._match_abs = None
        p.dirty = True

    def _buffers_msg(self):
        return {"t": "buffers", "items": [
            {"i": i, "preview": (b.splitlines()[0] if b.splitlines() else "")[:50]}
            for i, b in enumerate(self.buffers)]}

    def search_pane(self, sess: Session, query, direction="up"):
        win = sess.active_window
        if not win or not win.active_pane:
            return
        p = win.active_pane
        if query:  # 새 검색어 → 현재 뷰부터 다시
            p.search_query = query
            p._match_abs = None
        q = p.search_query
        if not q:
            return
        texts = self._pane_text_lines(p)
        total = len(texts)
        lines = p.screen.lines
        if p._match_abs is None:
            cur = (total - p.scroll) - 1  # 현재 뷰 하단
        else:
            cur = p._match_abs
        ql = q.lower()
        rng = range(cur - 1, -1, -1) if direction == "up" else range(cur + 1, total)
        found = next((i for i in rng if ql in texts[i].lower()), None)
        if found is None:
            return
        p._match_abs = found
        hist = p._history_len()
        target_start = max(0, found - lines // 2)
        p.scroll = max(0, min(hist, hist - target_start))
        p.dirty = True

    def select_pane_cycle(self, sess: Session):
        win = sess.active_window
        if not win:
            return
        ps = win.panes()
        if win.active_pane in ps:
            i = ps.index(win.active_pane)
            win.active_pane = ps[(i + 1) % len(ps)]

    def resize_split(self, sess: Session, sid: int, ratio: float):
        win = sess.active_window
        if not win:
            return
        sp = win.split_by_id(sid)
        if sp:
            sp.ratio = max(0.05, min(0.95, ratio))

    def resize_dir(self, sess: Session, direction: str, cells: int = 3):
        win = sess.active_window
        if not win:
            return
        orient = "lr" if direction in ("left", "right") else "tb"
        node = win.active_pane
        while node.parent is not None:
            p = node.parent
            if p.orient == orient:
                avail = (p.rect[2] if orient == "lr" else p.rect[3]) - 1
                if avail > 0:
                    d = cells / avail
                    if direction in ("left", "up"):
                        p.ratio = max(0.05, min(0.95, p.ratio - d))
                    else:
                        p.ratio = max(0.05, min(0.95, p.ratio + d))
                return
            node = p

    # ---- 클라이언트 통신 ----
    def clients_of(self, sess: Session):
        for c in self.clients:
            if c.session is sess:
                return c
        return None

    def _session_of_pane(self, pane: Pane) -> Session | None:
        """패널이 속한 세션을 찾는다(어느 탭/윈도우든)."""
        for sess in self.sessions.values():
            for t in sess.tabs:
                if pane in t.window.panes():
                    return sess
        return None

    def _session_size(self, sess: Session):
        """세션에 attach 한 모든 클라이언트를 수용하도록 최소 크기를 쓴다(미러링)."""
        cs = [c for c in self.clients if c.session is sess]
        if not cs:
            return 80, 24
        return (max(MIN_W, min(c.cols for c in cs)),
                max(MIN_H, min(c.rows for c in cs)))

    def _should_reserve_header(self, p) -> bool:
        """클라이언트가 이 패널에 Claude 프롬프트 헤더를 그릴지 여부(#1). 그러면
        내용 영역에서 한 행을 빼(헤더가 차지) 헤더가 1행짜리 패널 내용을 가리지
        않게 한다. 전역 옵션 claude_header + 그 패널이 Claude 이고 표시할 프롬프트가
        있을 때 참. (클라 전용 _claude_hidden_panes 팝업 숨김은 서버가 모르므로 그
        경우 예약 행은 비워둔다 — 토글 시 터미널 리플로우를 피하는 이점도 있다.)

        Claude 존재 판정은 raw `_claude` 가 아니라 **디바운스된 `_hdr_claude`**
        를 쓴다. raw 값은 footer 가 한 프레임 안 잡히면 None 으로 깜빡여(특히
        ssh/ConPTY) 예약이 매 프레임 토글→PTY ±1 행 리사이즈 반복→원격 화면이
        한 줄씩 떨리는데, 디바운스가 그 떨림을 없앤다(_scan_claude 에서 갱신)."""
        return bool(self.claude_header and p._hdr_claude and p.last_prompt)

def run_server(sock_path: str, resume_path: str | None = None):
    srv = Server(sock_path, resume_path)
    # 프로덕션 데몬에서만 외부 종료 시그널(SIGTERM/SIGHUP)을 핸들링한다(serve 가
    # 이 플래그를 보고 설치). 테스트 harness 는 serve() 를 직접 호출하므로 켜지지
    # 않아 시그널 핸들러의 프로세스 전역 자원이 루프 간 누수되지 않는다.
    srv._handle_signals = True
    try:
        asyncio.run(srv.serve())
    except (KeyboardInterrupt, RuntimeError):
        pass
    except Exception:
        # serve() 밖으로 샌 미처리 예외 = 서버 치명 종료(동시종료의 '서버 사망'
        # 변종). 데몬은 stderr 가 /dev/null 이라 평소엔 흔적 없이 사라지는데,
        # 트레이스백을 `<sock>.error.log` 에 남겨 다음 조사에서 원인을 잡게 한다
        # (docs/INVESTIGATION §3·§7.5). 로깅 후 재전파하지 않고 조용히 종료한다
        # — 프로세스는 어차피 끝나고, 정리는 OS 가 fd 를 닫으며 마무리한다.
        try:
            srv._log_error("run_server(fatal)")
        except Exception:
            pass
