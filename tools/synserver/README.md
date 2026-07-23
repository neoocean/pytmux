# synserver — pytmux 토큰 사용량 동기화 서버

여러 머신의 Claude 토큰 회계를 한 곳에 모으는 **작은 자기호스팅 서버**입니다.
설계 전문은 `docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md` §5.

## 무엇을 저장하지 않는가 (이 서버의 요점)

- **Claude 계정 이름(이메일)을 저장하지 않습니다.** 계정은 가명(`acct_id`,
  머신들만 아는 키로 만든 HMAC)으로만 구분합니다.
- **레코드 내용을 읽지 못합니다.** 토큰 수치·모델명은 각 머신이 봉인해 올리고,
  서버는 암호문만 보관합니다(복호 키는 서버에 없습니다).
- **사람 이름도 없습니다.** 로그인은 아이디 없는 패스키뿐이라 사용자명·이메일·
  비밀번호 필드가 스키마에 아예 없습니다.

서버가 하는 일은 인가 · 쿼터 · 멱등 삽입 · 커서 재생, 이 넷뿐입니다.

## 요구사항

Python 3.11+ 와 `cryptography` 하나. 웹 프레임워크·WebAuthn 라이브러리는 쓰지
않습니다(`http.server` + `sqlite3` + 자작 검증).

```sh
python3 -m pip install 'cryptography>=42'
```

## 실행

```sh
python3 tools/synserver/app.py --db /var/lib/synserver/sync.db \
    --rp-id sync.example.org --host 127.0.0.1 --port 8787
```

> **TLS 는 앞단 리버스 프록시가 담당합니다.** 서버는 루프백에만 붙이세요.
> 패스키는 HTTPS(또는 localhost)에서만 동작합니다.

### Caddy (자동 TLS)

```
sync.example.org {
    reverse_proxy 127.0.0.1:8787
}
```

### systemd

```ini
[Unit]
Description=pytmux token sync server
After=network.target

[Service]
ExecStart=/usr/bin/python3 /opt/pytmux/tools/synserver/app.py \
    --db /var/lib/synserver/sync.db --rp-id sync.example.org
User=synserver
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/synserver

[Install]
WantedBy=multi-user.target
```

## 쓰기 시작하기

1. 브라우저로 `https://sync.example.org/` 를 열고 **새 패스키 만들기** — 이때
   vault 가 생깁니다(아이디 입력 없음).
2. **코드 새로 만들기** 로 1회용 페어링 코드(10분)를 받습니다.
3. 그 머신의 pytmux 에서 `:claude-token-sync enroll <코드>` — 머신이 자기 Ed25519 키를
   등록하고 이후 요청을 서명합니다.
4. 두 번째 머신은 1호 머신에서 `:claude-token-sync invite` 로 나온 **마스터 키 초대
   코드**를 먼저 옮긴 뒤(이 값은 서버를 지나가지 않습니다), 2~3 을 반복합니다.

## 운영

| 항목 | 방법 |
|---|---|
| 백업 | `sqlite3 sync.db ".backup /backup/sync-$(date +%F).db"` — 일 1회, 7일 보관 |
| 복구 리허설 | **한 번은 실제로** 해 보세요. 안 해 본 백업은 백업이 아닙니다 |
| 보존 | 로그인 후 `POST /v1/purge?before_seq=<seq>` — 로컬 DB 가 원본이라 손실 없음 |
| 기기 폐기 | 등록 페이지의 **폐기** 버튼(즉시 401) |
| 관측 | 접근 로그는 메서드·경로·상태만. 가명·암호문은 **로그 금지** |

서버가 죽어도 각 머신의 회계·표시는 영향이 없습니다(동기화만 멈춥니다).
새 머신 간 수렴은 서버가 돌아오면 커서(`seq`)부터 이어집니다.

## 도메인은 되돌리기 어렵습니다

패스키는 `--rp-id` 도메인에 묶입니다. 나중에 도메인을 바꾸면 **등록된 패스키가
전부 무효**가 되어 다시 등록해야 합니다(기기 키와 데이터는 남습니다). 처음에
오래 쓸 이름으로 정하세요.

## 키를 잃었다면

vault 마스터 키를 모든 머신에서 잃으면 서버의 암호문은 **복구할 수 없습니다**.
로컬 DB 가 원본이므로 회계 자체는 남습니다 — 서버 데이터를 purge 하고 새 키로
다시 올리면 됩니다(설계 §5.9).

## 테스트

pytmux 스위트에 포함돼 있습니다.

```sh
python3 tests/run.py test_synserver_webauthn
python3 tests/run.py test_synserver_app
```

부정 케이스(오리진 불일치·서명 변조·재생·IDOR·쿼터·형식 오류)가 본체입니다 —
자작 검증이라 "통과"보다 "거부"를 더 많이 시험합니다.
