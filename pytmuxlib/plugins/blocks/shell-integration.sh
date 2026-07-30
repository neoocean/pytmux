# pytmux 셸 통합 — 명령 경계를 서버에 알린다.
#
# 이걸 쓰면 pytmux 가 "어디부터 어디까지가 한 명령인가"를 알게 되고, 블록 단위 표현
# (접기·복사·종료코드 표시)이 가능해진다. **안 깔아도 아무것도 깨지지 않는다** —
# 블록이 안 생길 뿐 화면은 종전 그대로다.
#
# 설치(bash/zsh):
#
#     echo '[ -n "$PYTMUX" ] && source ~/.pytmux/shell-integration.sh' >> ~/.zshrc
#
# 쓰는 것은 OSC 133 시맨틱 프롬프트다. iTerm2 가 만들고 kitty·WezTerm·VSCode 가 따르는
# 사실상 표준이라, 다른 터미널에서 같은 rc 를 써도 문제가 없다(그쪽도 이해한다).
#
#     OSC 133 ; A        프롬프트 시작
#     OSC 133 ; B        명령 입력 시작
#     OSC 133 ; C        명령 실행 시작(출력 시작)
#     OSC 133 ; D ; <종료코드>   명령 끝
#     OSC 7  ; file://호스트/경로  현재 디렉토리
#
# 명령 텍스트("무슨 명령을 쳤나")는 133 에 자리가 없어 VSCode 셸 통합과 같은
#
#     OSC 633 ; E ; <명령줄>
#
# 를 쓴다. 명령줄 안의 `;`·백슬래시·제어문자는 `\xHH` 로 escape 한다 — 안 하면 명령에
# 들어간 `;` 하나가 필드 경계로 읽혀 뒤가 잘린다(`git log; ls` 가 `git log` 로 보인다).
#
# 이 파일은 bash 와 zsh 를 함께 다룬다. fish 는 문법이 달라 별도 파일이 필요하다.

# 이미 다른 터미널의 통합이 걸려 있으면 겹치지 않게 빠진다 — 두 벌이 걸리면 블록이
# 두 번씩 생긴다.
if [ -n "${__pytmux_shell_integration:-}" ]; then
	return 0 2>/dev/null || true
fi
__pytmux_shell_integration=1

__pytmux_osc() { printf '\033]%s\033\\' "$1"; }

# cwd 를 URL 로. 공백·한글 경로도 그대로 다룰 수 있게 file:// 형식을 쓴다.
__pytmux_report_cwd() { __pytmux_osc "7;file://${HOSTNAME:-localhost}${PWD}"; }

# 명령줄을 OSC 필드로 안전하게 만든다. 패턴을 따옴표로 감싸 **글자 그대로** 치환한다
# (bash·zsh 공통 — 안 감싸면 백슬래시가 패턴 escape 로 먹힌다).
__pytmux_report_cmd() {
	local s=$1
	local bs='\'
	s=${s//"$bs"/"${bs}${bs}"}       # 백슬래시 먼저 — 아래 치환이 만든 것과 안 섞이게
	s=${s//";"/"${bs}x3b"}
	s=${s//"$__pytmux_nl"/"${bs}x0a"}
	s=${s//"$__pytmux_cr"/"${bs}x0d"}
	s=${s//"$__pytmux_esc"/"${bs}x1b"}
	# BEL 도 **OSC 종결자**다(파서는 BEL / C1 ST / ESC \ 셋을 받는다). 안 바꾸면
	# 명령줄에 든 BEL 하나가 문자열을 여기서 끊어 ① 블록의 명령이 잘리고 ② 남은
	# 글자가 화면에 그대로 쏟아진다(검수 2026-07-30 — 머리말이 "제어문자는 escape
	# 한다"고 적어 둔 계약을 코드가 안 지키고 있었다). ESC 는 이미 위에서 바꾸므로
	# escape 주입은 애초에 불가.
	s=${s//"$__pytmux_bel"/"${bs}x07"}
	# 서버도 자르지만(MAX_CMD_LEN) 긴 붙여넣기를 파이프에 흘리지 않는다.
	__pytmux_osc "633;E;${s:0:1024}"
}
__pytmux_nl='
'
__pytmux_cr=$(printf '\r')
__pytmux_esc=$(printf '\033')
__pytmux_bel=$(printf '\007')

if [ -n "${ZSH_VERSION:-}" ]; then
	# ── zsh ────────────────────────────────────────────────────────────────
	# precmd = 프롬프트 직전, preexec = 명령 실행 직전. zsh 가 정확한 훅을 주므로
	# bash 처럼 DEBUG 트랩을 흉내 낼 필요가 없다.
	__pytmux_precmd() {
		local exit_code=$?
		# 첫 프롬프트에는 끝낼 명령이 없다.
		if [ -n "${__pytmux_running:-}" ]; then
			__pytmux_osc "133;D;${exit_code}"
			unset __pytmux_running
		fi
		__pytmux_report_cwd
		__pytmux_osc "133;A"
	}
	__pytmux_preexec() {
		# zsh 는 preexec 에 **사용자가 친 줄 그대로**를 준다($1) — 화면에서 긁을
		# 필요가 없다. 실행 시작(C)보다 먼저 보내 블록이 이름부터 갖게 한다.
		__pytmux_report_cmd "$1"
		__pytmux_osc "133;C"
		__pytmux_running=1
	}
	autoload -Uz add-zsh-hook 2>/dev/null && {
		add-zsh-hook precmd __pytmux_precmd
		add-zsh-hook preexec __pytmux_preexec
	}
elif [ -n "${BASH_VERSION:-}" ]; then
	# ── bash ───────────────────────────────────────────────────────────────
	# bash 에는 preexec 이 없어 DEBUG 트랩으로 흉내 낸다. PROMPT_COMMAND 가 도는
	# 동안에는 트랩이 명령마다 불리므로 플래그로 한 번만 처리한다.
	__pytmux_prompt() {
		local exit_code=$?
		if [ -n "${__pytmux_running:-}" ]; then
			__pytmux_osc "133;D;${exit_code}"
			unset __pytmux_running
		fi
		__pytmux_report_cwd
		__pytmux_osc "133;A"
		__pytmux_at_prompt=1
	}
	__pytmux_debug() {
		# 프롬프트가 뜬 뒤 첫 명령만 "실행 시작"으로 본다.
		if [ -n "${__pytmux_at_prompt:-}" ]; then
			unset __pytmux_at_prompt
			# bash 에는 preexec 이 없어 명령줄도 DEBUG 트랩의 `$BASH_COMMAND` 로
			# 받는다 — 사용자가 친 줄이 아니라 **실행 직전의 단순명령**이라 별칭·
			# 함수 확장이 반영된 모양일 수 있다(zsh 쪽이 더 정확하다).
			__pytmux_report_cmd "$BASH_COMMAND"
			__pytmux_osc "133;C"
			__pytmux_running=1
		fi
	}
	# 기존 PROMPT_COMMAND 를 지우지 않는다 — 사용자가 쓰던 것이 있을 수 있다.
	case "${PROMPT_COMMAND:-}" in
	*__pytmux_prompt*) ;;
	"") PROMPT_COMMAND="__pytmux_prompt" ;;
	*) PROMPT_COMMAND="__pytmux_prompt;${PROMPT_COMMAND}" ;;
	esac
	trap '__pytmux_debug' DEBUG
fi
