"""증분 VT 토크나이저 PoC — pyte.Stream(파서) 대체 타당성 검증용.

docs/internal/VT_PARSER_TRADEOFF_2026-06-15.md §6 옵션 B("토크나이저만 자작, pyte Screen
유지")의 PoC. 바이트를 소비해 **pyte.Screen 메서드로 직접 디스패치**하는 자체 상태
기계다. 화면 상태 의미론(커서/스크롤영역/모드/erase/탭/문자셋)은 검증된 pyte.Screen
을 그대로 쓰고, dispatch 매핑(final byte → Screen 메서드명)도 pyte.Stream 의 테이블
(basic/escape/sharp/csi)을 그대로 재사용한다. **우리가 소유하는 것은 증분 파서뿐.**

이 한 모듈이 model.py 의 feed-전 우회 4종을 상태기계 안에서 1급으로 흡수함을 보인다:

  ① 콜론식 SGR(`4:0`·`38:2::r:g:b`·`58:…`)  ← _sanitize_sgr
     pyte 0.8.2 CSI 파서는 콜론에서 시퀀스를 중단(streams.py else 분기 → break)해
     밑줄이 안 꺼지고 "0m" 잔해가 샌다. 여기선 `m` 종결 시 콜론 서브파라미터를
     파서가 직접 해석해 select_graphic_rendition 정수 인자로 변환한다.
  ② private 마커 SGR `CSI >…m`(XTMODKEYS)  ← _PRIVATE_SGR_RE
     pyte 는 `>` 를 무시(streams.py `SP_OR_GT: pass`)해 `CSI 4;…m`(=밑줄 ON)으로
     오인한다. 여기선 마커가 <>= 이고 종결이 `m` 이면 통째로 버린다.
  ③ kitty 키보드 `CSI >…u`/`CSI ?…u`  ← _KITTY_KBD_RE
     pyte 는 `u` 종결 private CSI 를 처리 못 해 끝의 `u` 가 글자로 샌다. 여기선
     마커가 <>=? 이고 종결이 `u` 이면 버린다.
  ④ feed 경계로 잘린 미완성 CSI  ← _CSI_PARTIAL_RE/_altcarry
     상태가 인스턴스에 남는 진짜 증분 파서라, 청크 중간에 끊겨도 다음 feed 에서
     이어 파싱한다. 정규식 꼬리 매칭/바이트 이월이 불필요하다.

추가로 alt-screen 전환(`CSI ?1049/1047/47 h|l`)은 ``alt_hook(enter: bool)`` 콜백으로
내보내, 호출부(Pane 류)가 메인/대체 Screen 객체를 스왑하게 한다(model._ALT_RE 라우팅
대체). DCS(`ESC P … ST`)는 pyte 가 본문을 출력으로 흘리는 것과 달리 **소비(드롭)**한다.

PoC 범위 주의: 마우스 트래킹/bracketed-paste 추적은 model 에서 feed 와 별개로 raw
데이터를 보므로 여기 대상이 아니다. **2026-06-16 이후 이 토크나이저가 라이브 feed
경로의 기본**이다(Pane.feed→_feed_native, vt_parser 기본 native — 종전 docstring 의
'배선하지 않는다'는 stale 였음, 1-7 정정). tests/test_vtparse.py 가 차분/등가를
가드하고, test_vt_parser_equivalence.py 가 pyte 경로와의 동등성을 상시 회귀한다."""
from __future__ import annotations

import codecs
import re

from pyte import control as ctrl
from pyte.streams import Stream

# pyte.Stream 의 정적 dispatch 테이블을 그대로 재사용한다(우리는 매핑을 재발명하지
# 않는다). final byte(또는 ESC 다음 글자) → Screen 메서드명.
_BASIC = Stream.basic       # C0 컨트롤(BEL/BS/HT/LF/CR/…)
_ESCAPE = Stream.escape     # non-CSI ESC(RIS/IND/DECSC/…)
_SHARP = Stream.sharp       # ESC # n (DECALN)
_CSI = Stream.csi           # CSI … final → 메서드명

# pyte 의 CSI 테이블엔 SU(`CSI Ps S`)·SD(`CSI Ps T`)가 빠져 있다(pyte.Screen 미구현).
# 스크롤 영역(DECSTBM)을 SU/SD 로 직접 스크롤하는 앱(Claude Code·less·일부 TUI)에서 그
# 시퀀스가 조용히 드롭돼, 앱이 "스크롤됐다"고 가정하고 그린 다음 줄이 안 밀린 옛 줄에
# 겹쳐 격자가 발산한다(사용자 보고: pytmux 안 Claude 글자 겹침; tmux 는 SU/SD 구현해 정상).
# model._BCEMixin 에 scroll_up/scroll_down 을 추가했고, set_screen 이 화면이 그 메서드를
# 가질 때만 self._csi 에 S/T 를 바인딩한다(전역 Stream.csi 는 안 건드림 — usageprobe 등
# 이 쓰는 plain pyte.Screen 은 scroll_up 이 없어 AttributeError 가 나므로). final byte.
_SU_SD = {"S": "scroll_up", "T": "scroll_down"}

# alt-screen 전환 DECSET 모드(model._ALT_RE 와 동일 집합).
_ALT_MODES = frozenset((1049, 1047, 47))

# 종결 바이트 범위: VT "final byte" 는 0x40~0x7E.
_CSI_FINAL_LO, _CSI_FINAL_HI = 0x40, 0x7E

# GROUND 상태 plain-text 런 fast-path(pyte.Stream._text_pattern 와 동일 취지): ESC·
# C1·NUL·DEL·basic 컨트롤이 아닌 글자의 연속을 한 번에 잡아 draw 1회로 그린다. char-by
# -char Python 루프 오버헤드를 없애 pyte 의 정규식 배칭과 동등 처리량을 낸다.
_SPECIAL = (set(_BASIC) | {ctrl.ESC, ctrl.CSI_C1, ctrl.OSC_C1, ctrl.NUL, ctrl.DEL})
_TEXT_RUN = re.compile("[^" + "".join(map(re.escape, _SPECIAL)) + "]+")


def _sgr_params_from_raw(raw: str) -> list[int] | None:
    """`m`(SGR) 종결 CSI 의 원시 파라미터 문자열 → select_graphic_rendition 정수
    인자 목록. 콜론 서브파라미터를 직접 해석한다(model._sanitize_sgr 와 등가 결과).
    전부 버려졌으면 None(시퀀스 자체 생략 — `CSI m` 으로 두면 reset 오작동)."""
    out: list[int] = []
    for tok in raw.split(";"):
        if ":" not in tok:
            out.append(min(int(tok or 0), 9999))   # 평범한 파라미터
            continue
        sub = tok.split(":")
        head = sub[0]
        if head == "4":
            # 밑줄 스타일: 4:0 = 끄기(=24), 그 외(곱슬/이중 등) = 켜기(=4).
            val = sub[1] if len(sub) > 1 else ""
            out.append(24 if val in ("0", "") else 4)
        elif head in ("38", "48"):
            kind = sub[1] if len(sub) > 1 else ""
            nums = [p for p in sub[2:] if p != ""]
            if kind == "5" and nums:
                out += [int(head), 5, min(int(nums[0]), 9999)]
            elif kind == "2" and len(nums) >= 3:
                # 38:2:<colorspace>:r:g:b — colorspace 가 비어도 마지막 3개가 r,g,b.
                out += [int(head), 2] + [min(int(n), 9999) for n in nums[-3:]]
            # 형식 불명 → 버림(잔해 방지).
        # 58:(밑줄색) 등 pyte 미지원 → 버림.
    return out or None


class VTTokenizer:
    """증분 VT 파서. ``feed(bytes)`` 를 여러 번 호출해도 시퀀스가 경계로 잘리지 않게
    상태를 인스턴스에 보존한다. ``screen`` 은 pyte.Screen(또는 호환) 인스턴스.

    :param alt_hook: ``?1049/1047/47 h|l`` 감지 시 ``alt_hook(enter: bool)`` 호출.
                     None 이면 해당 전환을 무시한다(테스트 단순화).
    """

    # 파서 상태
    _GROUND, _ESC, _CSI, _OSC, _STRING, _CHARSET, _SHARP = range(7)

    # OSC 본문 상한(타이틀/아이콘명). 정상 사용은 수백 B 라 자르지 않으면서, 악의적
    # 멀티 MB OSC 의 자원 폭주를 막는 캡(보안 N1/N2).
    _OSC_MAX = 4096

    # CSI 파라미터 원시문자열 상한. **N1(OSC)의 살아남은 형제**(보안검수 2026-07-17 F2):
    # `_OSC_MAX` 는 OSC 만 캡했고 `_raw` 엔 상한이 없어, 미종결 `ESC[` + 숫자 스트림이
    # ① `self._raw += ch` 의 O(n²) 재할당으로 CPU 를 태우고(400k자=1.15s, 10MB=수 분)
    # ② 종결자가 없어도 feed 를 넘어 본문이 영구 잔류해 메모리 DoS 가 됐다.
    # 정상 CSI 는 SGR 체인을 포함해도 수백 B 이므로 4096 이면 자르지 않는다.
    _RAW_MAX = 4096

    def __init__(self, screen, alt_hook=None):
        self.alt_hook = alt_hook
        self.use_utf8 = True
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        self.set_screen(screen)
        self._reset_fsm()

    def set_screen(self, screen) -> None:
        """디스패치 대상 Screen 을 (재)지정하고 메서드 바인딩을 캐시한다. alt-screen
        스왑(Pane._enter_alt/_leave_alt)에서 호출 — **파서 FSM 상태는 보존**하므로
        시퀀스 사이(GROUND)에서만 호출해야 한다(alt 전환은 완결 CSI 직후라 안전).
        dispatch 캐시는 pyte create_dispatcher 와 동일 취지(매핑 재발명 없음)."""
        self.screen = screen
        self._draw = screen.draw
        self._basic = {c: getattr(screen, n) for c, n in _BASIC.items()}
        self._escape = {c: getattr(screen, n) for c, n in _ESCAPE.items()}
        self._sharp = {c: getattr(screen, n) for c, n in _SHARP.items()}
        self._csi = {c: getattr(screen, n) for c, n in _CSI.items()}
        # SU/SD 보강: 화면이 scroll_up/scroll_down 을 가질 때만 바인딩(우리 _BCEMixin
        # 화면은 가짐; plain pyte.Screen 은 없으면 그대로 미수록 → 종전대로 무시).
        for final, name in _SU_SD.items():
            fn = getattr(screen, name, None)
            if fn is not None:
                self._csi[final] = fn

    def _reset_fsm(self) -> None:
        self.state = self._GROUND
        self._raw = ""          # CSI 파라미터 원시 문자열(숫자/;/:)
        self._priv = ""         # private/intermediate 마커(? > < = $ 등 첫 글자)
        self._osc = ""          # OSC 본문
        self._osc_esc = False   # OSC 안에서 ESC 를 만나 ST(ESC\) 대기 중
        self._str_esc = False   # DCS/SOS/PM/APC 본문에서 ST 대기 중

    # ── 공개 API ────────────────────────────────────────────────────────────
    def feed(self, data: bytes) -> None:
        if self.use_utf8:
            text = self._dec.decode(data)
        else:
            text = "".join(map(chr, data))
        self._feed_str(text)

    # ── 내부 ────────────────────────────────────────────────────────────────
    def _feed_str(self, text: str) -> None:
        ESC = ctrl.ESC
        match_run = _TEXT_RUN.match
        length = len(text)
        i = 0
        while i < length:
            st = self.state
            if st == self._GROUND:
                # fast-path: plain-text 런을 한 번에 그린다(특수문자 전까지).
                m = match_run(text, i)
                if m is not None:
                    self._draw(m.group())
                    i = m.end()
                    if i >= length:
                        break
                ch = text[i]
                i += 1
                if ch == ESC:
                    self.state = self._ESC
                elif ch in self._basic:
                    if (ch == ctrl.SI or ch == ctrl.SO) and self.use_utf8:
                        continue   # UTF-8 모드에선 shift 무시(pyte 와 동일).
                    self._basic[ch]()
                elif ch == ctrl.CSI_C1:
                    self._enter_csi()
                elif ch == ctrl.OSC_C1:
                    self.state, self._osc, self._osc_esc = self._OSC, "", False
                # NUL/DEL 등 그 외 특수문자는 무시(pyte 와 동일).
                continue
            if st == self._OSC:
                # OSC 본문은 종결자까지 한 번에 흡수(글자단위 `self._osc += ch` 는
                # 인스턴스 속성이라 O(n²) — 멀티 MB OSC 로 서버 행/DoS, 보안 N1).
                i = self._consume_osc(text, i, length)
                continue
            if st == self._STRING:
                # DCS/SOS/PM/APC 본문 드롭도 종결자까지 한 번에 스캔(O(n)).
                i = self._consume_string(text, i, length)
                continue
            ch = text[i]
            i += 1
            if st == self._ESC:
                self._on_escape(ch)
            elif st == self._CSI:
                self._on_csi(ch)
            elif st == self._CHARSET:
                self._on_charset(ch)
            elif st == self._SHARP:
                self._sharp.get(ch, self._noop)()
                self.state = self._GROUND

    def _noop(self, *a, **k) -> None:
        pass

    def _enter_csi(self) -> None:
        self.state, self._raw, self._priv = self._CSI, "", ""

    def _on_escape(self, ch: str) -> None:
        if ch == "[":
            self._enter_csi()
        elif ch == "]":
            self.state, self._osc, self._osc_esc = self._OSC, "", False
        elif ch == "#":
            self.state = self._SHARP
        elif ch == "%":
            self.state, self._priv = self._CHARSET, "%"
        elif ch in "()":
            self.state, self._priv = self._CHARSET, ch
        elif ch in "PX^_":
            # DCS/SOS/PM/APC: 본문을 ST 까지 소비(드롭). pyte 는 본문을 출력으로
            # 흘리지만, 여기선 NEST DCS 누수 방지를 위해 삼킨다.
            self.state, self._str_esc = self._STRING, False
        else:
            self._escape.get(ch, self._noop)()
            self.state = self._GROUND

    def _on_charset(self, ch: str) -> None:
        # ESC % @ / % G : UTF-8 토글. ESC ( / ) C : G0/G1 지정(UTF-8 모드 noop).
        if self._priv == "%":
            if ch == "@":
                self.use_utf8 = False
                self._dec.reset()
            elif ch in "G8":
                self.use_utf8 = True
        # G0/G1 charset 지정은 UTF-8 모드에서 noop(pyte 와 동일).
        self.state = self._GROUND

    def _osc_append(self, s: str) -> None:
        # 본문 상한(_OSC_MAX) 초과분은 버린다. 타이틀/아이콘명은 현실적으로 수백 B 라
        # 캡이 정상 사용을 자르지 않으면서, 악의적 멀티 MB OSC 의 메모리/표시 자원
        # 폭주(N1 의 누적 비용·N2 의 거대 타이틀)를 막는다.
        room = self._OSC_MAX - len(self._osc)
        if room > 0:
            self._osc += s if len(s) <= room else s[:room]

    def _consume_osc(self, text: str, i: int, length: int) -> int:
        """OSC 본문을 종결자(BEL / C1 ST / ESC[\\])까지 한 번에 흡수하고 다음
        인덱스를 돌려준다. 종결자가 이 feed 에 없으면 본문을 (상한 내) 흡수하고
        ``length`` 를 반환해 다음 feed 로 상태를 이월한다(경계-안전)."""
        if self._osc_esc:
            # 직전 feed 끝이 ESC 라 ST(ESC\) 를 기다리던 상태.
            self._osc_esc = False
            ch = text[i]
            i += 1
            if ch == "\\":
                self._finish_osc()
                return i
            # ESC + 다른 글자 — 비정상이나 pyte 동작 따라 본문에 포함 후 계속.
            self._osc_append("\x1b" + ch)
            if i >= length:
                return i
        # 종결자 후보(BEL/C1 ST/ESC) 중 가장 이른 위치까지 본문을 한 번에 append.
        nxt = -1
        for term in (ctrl.BEL, ctrl.ST_C1, ctrl.ESC):
            p = text.find(term, i)
            if p != -1 and (nxt == -1 or p < nxt):
                nxt = p
        if nxt == -1:
            self._osc_append(text[i:length])
            return length
        self._osc_append(text[i:nxt])
        if text[nxt] == ctrl.ESC:
            self._osc_esc = True       # ST(ESC\) 를 다음 글자에서 확인.
        else:
            self._finish_osc()         # BEL / C1 ST 로 종결.
        return nxt + 1

    def _consume_string(self, text: str, i: int, length: int) -> int:
        """DCS/SOS/PM/APC 본문을 ST(ESC\\ / C1 ST)까지 한 번에 드롭하고 다음
        인덱스를 돌려준다. 본문은 버리므로 누적 없음(스캔만 O(n))."""
        if self._str_esc:
            self._str_esc = False
            ch = text[i]
            i += 1
            if ch == "\\":
                self.state = self._GROUND
                return i
            # ESC + 다른 글자면 계속 소비.
            if i >= length:
                return i
        pe = text.find(ctrl.ESC, i)
        ps = text.find(ctrl.ST_C1, i)
        if pe == -1 and ps == -1:
            return length              # 본문 전부 드롭, 다음 feed 로 이월.
        nxt = ps if pe == -1 else (pe if ps == -1 else min(pe, ps))
        if text[nxt] == ctrl.ST_C1:
            self.state = self._GROUND
        else:
            self._str_esc = True       # ST(ESC\) 를 다음 글자에서 확인.
        return nxt + 1

    def _finish_osc(self) -> None:
        body = self._osc
        self.state = self._GROUND
        # 형식: "<code>;<param>". 코드 0/1 = icon name, 0/2 = title(pyte 와 동일).
        code, _, param = body.partition(";")
        if code in ("0", "1"):
            self.screen.set_icon_name(param)
        if code in ("0", "2"):
            self.screen.set_title(param)

    def _on_csi(self, ch: str) -> None:
        o = ord(ch)
        if _CSI_FINAL_LO <= o <= _CSI_FINAL_HI and ch not in ("<", "=", ">", "?"):
            # 종결 바이트 도달. (마커 <>=? 는 파라미터부 시작에서만 의미 — 위 제외.)
            self._dispatch_csi(ch)
            self.state = self._GROUND
            return
        if ch in "<>=?" and not self._raw:
            # 파라미터부 맨 앞 private/secondary 마커.
            self._priv = ch
        elif ch in "$ !\"'":
            # intermediate 바이트(DECRQM 등) — private 마커로 기록(드물어 보존만).
            self._priv = self._priv or ch
        elif ch.isdigit() or ch in ";:":
            # 상한 초과분은 버린다(F2). 자르는 편이 안전한 저하다 — 정상 CSI 는 이
            # 한계 근처도 안 가고, 넘겼다는 건 이미 파라미터가 핸들러 arity 를 한참
            # 넘겨 어차피 _call_csi 에서 잘릴 입력이라는 뜻이다.
            if len(self._raw) < self._RAW_MAX:
                self._raw += ch
        # 그 외(제어문자 등)는 무시 — 관용적으로 흘려보낸다.

    def _dispatch_csi(self, final: str) -> None:
        priv, raw = self._priv, self._raw

        # ②③ private 마커 + m/u 종결 → 드롭(XTMODKEYS·kitty 키보드). 마커는 단일
        # 문자이므로 튜플 멤버십으로 검사한다(빈 priv "" 는 부분문자열 매칭으로 항상
        # True 가 되는 함정 — 정상 SGR 이 통째로 드롭되는 버그 방지).
        if priv in ("<", ">", "=") and final == "m":
            return
        if priv in ("<", ">", "=", "?") and final == "u":
            return

        # ① SGR(`m`): 콜론 서브파라미터를 직접 해석.
        if final == "m" and priv not in ("<", ">", "="):
            params = _sgr_params_from_raw(raw)
            if params is None:
                return   # 전부 버려짐 → 시퀀스 생략(reset 오작동 방지).
            self.screen.select_graphic_rendition(*params)
            return

        # 정수 파라미터 파싱(pyte 와 동일: 빈값=0, 상한 9999).
        params = [min(int(p or 0), 9999) for p in raw.split(";")] if raw else [0]

        # alt-screen 전환: **단독** ?1049/1047/47 h|l → alt_hook 로 라우팅(Screen 엔
        # 안 먹임). model._ALT_RE 가 단독 모드만 매칭하므로 동일하게 단독일 때만 잡고,
        # 복합 모드(`?1049;25h` 등)는 일반 set_mode 로 pyte 에 넘긴다(실무상 단독만 옴).
        if (priv == "?" and final in "hl"
                and len(params) == 1 and params[0] in _ALT_MODES):
            if self.alt_hook is not None:
                self.alt_hook(final == "h")
            return

        fn = self._csi.get(final, self._noop)
        self._call_csi(fn, params, private=(priv == "?"))

    def _call_csi(self, fn, params, *, private: bool) -> None:
        """CSI 핸들러를 호출하되 **과다 파라미터로 죽지 않는다**(보안: 신뢰불가 패널
        출력의 DoS 차단 — docs/internal/SECURITY_REVIEW.md §9 R2).

        pyte 의 Screen 핸들러는 고정 인자수라(예: `cursor_position(line, column)`)
        `ESC[38;2;H` 처럼 종결자에 비해 파라미터가 많은 악의·고장 시퀀스는 `fn(*params)`
        에서 TypeError 를 던져 Pane.feed → 서버 feed 태스크를 크래시시키고 그 버스트의
        나머지 출력을 유실시킨다(위협모델 #4). 실단말처럼 **여분 파라미터를 뒤에서
        하나씩 떼어** arity 가 맞을 때까지 재시도하고, 끝내 안 맞으면 시퀀스를 생략한다.
        정상 시퀀스는 arity 가 맞아 첫 시도에 성공하므로 거동/성능 불변(pyte 동일).

        **한 번에 자른다**(보안검수 2026-07-17 F1): 종전엔 `p.pop()` 으로 하나씩 떼며
        재시도했는데, 재시도마다 N-튜플을 다시 쌓으므로 파라미터 N개에 O(N²) 였다 —
        R2(크래시) 를 고치며 DoS 를 들여온 셈이다(실측: 128KB 한 줄 = 4.76s 루프 정지,
        입력 2배마다 4배). pop 루프의 **수렴값은 결국 `params[:arity]`** 이므로, 핸들러
        arity 를 한 번 구해 곧장 슬라이스하면 결과는 완전히 같고 비용만 O(1) 이 된다.
        `*args` 핸들러(SGR 등)는 상한이 없으므로 자르지 않는다."""
        p = list(params)
        lim = self._arity(fn)
        if lim is not None and len(p) > lim:
            p = p[:lim]
        try:
            if private:
                fn(*p, private=True)
            else:
                fn(*p)
        except TypeError:
            # arity 를 못 읽었거나(내장/C 함수) 슬라이스로도 안 맞는 핸들러 → 생략.
            # 종전 pop 루프의 "끝내 안 맞으면 시퀀스 생략"과 동일한 귀결.
            pass

    # 핸들러별 최대 위치인자 수 캐시. None = 무제한(*args) 또는 조회 실패(그대로 호출).
    _ARITY_CACHE: dict = {}

    @classmethod
    def _arity(cls, fn):
        key = getattr(fn, "__func__", fn)
        try:
            return cls._ARITY_CACHE[key]
        except (KeyError, TypeError):       # TypeError = 해시 불가 → 캐시 우회
            pass
        lim = None
        try:
            import inspect
            n = 0
            for prm in inspect.signature(fn).parameters.values():
                if prm.kind is inspect.Parameter.VAR_POSITIONAL:
                    n = None                # *args → 무제한
                    break
                if prm.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                inspect.Parameter.POSITIONAL_OR_KEYWORD):
                    n += 1
            lim = n
        except (TypeError, ValueError):     # 내장/C 함수 등 시그니처 조회 불가
            lim = None
        try:
            cls._ARITY_CACHE[key] = lim
        except TypeError:
            pass
        return lim
