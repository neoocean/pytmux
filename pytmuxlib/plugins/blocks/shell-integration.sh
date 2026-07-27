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
