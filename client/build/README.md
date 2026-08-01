# `build/` — 배포용 선빌드 이진

`p4 add` 로 depot 이 추적하고, **공개 git 미러에도 함께 올린다**(2026-08-01 p4 69022 —
루트 `.gitignore` 의 `/client/build/*` 앵커를 풀었다. 종전 근거였던 "공개 배포는 GitHub
릴리스 자산으로"(트리 통합 계획 §5.3)는 그 릴리스 경로가 구현된 적이 없어 **이진을 받을
길이 depot 뿐**이었다).

## 지금 들어 있는 것

| 파일 | 무엇 | 굽는 러너 |
|---|---|---|
| `pytmux-gui-macos-arm64` | macOS(Apple Silicon) GUI 릴리스 빌드 · Mach-O arm64 | `macos-latest` |
| `pytmux-gui-linux-x64` | Linux(x86_64) GUI 릴리스 빌드 · ELF | `ubuntu-latest` |
| `pytmux-gui-windows-x64.exe` | Windows(x86_64-pc-windows-msvc) GUI 릴리스 빌드 · PE32+ | `windows-latest` |

⚠ **지금 실물은 macOS 것 하나뿐이다.** Linux 이진은 처음부터 없었고, Windows 이진은
손으로 구운 것이라 그 상자의 실 경로를 품고 있어 **지웠다**(2026-08-01 — 미러로 나가면
되돌릴 수 없다). 둘은 `release-binaries` 워크플로를 **한 번 돌리면** 채워진다. 위 표는
"CI 가 채울 자리"의 정의이기도 하다 — 셋이 다 안 오면 publish 잡이 실패한다.

이름에 **플랫폼·아키텍처를 적는다.** 종전 스냅샷은 `pytmux-client-tui.exe` 하나뿐이라
확장자로 갈렸지만, macOS 와 Linux 이진은 둘 다 확장자가 없어 이름이 겹친다.

## 갱신하는 법 — **CI 가 굽는다**

정상 경로는 GitHub Actions 다: **`release-binaries` 워크플로를 손으로 돌리거나**
(`workflow_dispatch`) `v*` 태그를 밀면, 세 러너가 각각 굽고 한 커밋으로 이 디렉토리에
들어온다(`.github/workflows/release-binaries.yml`).

푸시마다 돌지 **않는다** — 이진 셋이 ~24MB 이고 개정마다 통째로 새 blob 이라 히스토리가
금방 부푼다. 코드가 깨졌는지는 `rust.yml` 이 매 푸시마다 이미 잰다.

CI 로 굽는 이유는 편의가 아니다: 손으로 구우면 **굽는 사람 상자의 절대경로가 이진에
박히고**(아래), Windows·Linux 이진을 위해 그 OS 상자를 각각 갖고 있어야 한다.

## 손으로 굽는 법 — **스크립트로만 굽는다**

CI 를 못 쓰는 자리(로컬 확인·급한 스냅샷)에서는 아래를 쓴다. CI 도 **이 스크립트를 그대로
부르므로** 두 길의 산출물이 갈리지 않는다.

```sh
client/scripts/build_release.sh          # macOS · Linux
```
```powershell
client\scripts\build_release.ps1         # Windows
```

`cargo build --release -p gui` 를 손으로 돌리고 여기에 복사하면 **안 된다.** rustc 는
패닉 위치·`file!()` 을 위해 **컴파일 시점의 절대경로를 이진에 박고**, 그 경로에는 이
상자의 계정과 워크스페이스 구조가 그대로 들어간다. 2026-08-01 첫 미러 푸시 준비에서
실측한 값(그때까지 손으로 구운 이진 둘):

| 이진 | 워크스페이스 경로 | cargo 홈 경로 |
|---|---|---|
| macOS | `/Users/<계정>/p4/...` 16건 | `/Users/<계정>/.cargo` **329건** |
| Windows | `<드라이브>:\<depot 루트>\<머신>\...` 21건 | `C:\Users\<계정>\.cargo` **1013건** |

(표의 자리표시자는 **일부러** 자리표시자다 — 실제 문자열을 그대로 적으면 이 문서가
`scripts/check_mirror.py` ⑤ 에 걸린다. 게이트에 예외를 뚫는 대신 문서가 모양만 적는다.)

`build/` 는 이제 공개 미러로 나가고 이진은 개정마다 통째로 새 blob 이라, 한 번 푸시하면
히스토리에서 빼는 길이 history rewrite 뿐이다 — **되돌릴 수 없는 방향**이므로 굽는
자리에서 막는다. 스크립트는 `--remap-path-prefix` 로 두 뿌리(워크스페이스 → `/pytmux`,
cargo 홈 → `/cargo`)를 중립 이름으로 바꾸고, 구운 뒤 **미러 게이트와 같은 자**
(`scripts/check_mirror.py --scan`)로 재서 통과한 것만 `build/` 에 넣는다. 백트레이스는
`/pytmux/client/crates/...` 로 읽힌다(경로 문자열만 바뀌고 파일 접근은 그대로다).

⚠ **크로스 컴파일은 안 된다.** GUI 는 창·GPU 백엔드를 실제로 링크하므로 그 OS 에서
빌드해야 한다(`scripts/check_windows.sh` 는 링크 없이 `cargo check` 만 한다 — 그것이
통과한다고 Windows 이진이 나오는 것은 아니다). 손으로 구울 때 이진 셋의 갱신이 **각
상자에서** 따로 도는 이유이고, CI 로 옮긴 이유이기도 하다 — 러너는 세 OS 를 다 준다.

## 지운 것

`pytmux-client-tui.exe`(2.8MB)는 2026-08-01 에 지웠다 — Rust TUI 가 퇴역했고(클라는 정본
Textual TUI 와 이 GUI 둘), 퇴역한 제품의 이진을 두면 다음 사람이 그걸 받아 쓰게 된다.
`check_mirror.py` ① 이 이름으로 가리키던 규칙도 같이 지웠다(없는 파일을 재는 규칙은
아무것도 안 재면서 재는 척을 한다).
