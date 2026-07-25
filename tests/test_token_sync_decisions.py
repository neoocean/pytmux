"""토큰 동기화 §10.2 — **사용자 결정 5건(2026-07-25)을 기계 검사로 못박는다**.

결정은 문서에만 적어 두면 다음 사이클에 조용히 반대로 구현된다(HANDOFF §10 머리말이
경고하는 stale 패턴의 쌍둥이). 그래서 결정마다 "되돌리면 깨지는" 오라클을 둔다.

| 결정 | 내용 | 여기서 검사하는 것 |
|---|---|---|
| 1 | 호스팅 = 개인서버 + Cloudflare Tunnel, `rp.id`=`pytmux-sync.woojinkim.org` | 서버가 **루프백 기본** 바인딩(TLS·공개노출은 앞단) · `--rp-id` 필수(추측 금지) · 등록 기본 잠김 |
| 2 | 클라이언트 AEAD 암호화 **기본 on 확정** | `SyncClient.encrypt` 기본 True · 평문 업로드는 **거부**(조용한 평문 전송 없음) |
| 3 | 보존 **무기한**(서버 purge off) | `RETAIN_DAYS==0` · 파서에 보존 플래그 없음(배포 env 로만) |
| 4 | 계정 미상 = **별항 분리** + 비중 가시화 | 팝업 Σ 줄 `미상 N%` · 0이면 무표시 · 상류 잡값 내성 |
| 5 | vault **완전 격리**(공유 개념 없음) | 서버 스키마에 공유/그룹/멤버 테이블·컬럼이 **없음** |

되돌리면 실패해야 하는 오라클:
  · `--host` 기본을 0.0.0.0 으로 열면 → test_deploy_defaults_bind_loopback_only 실패
  · 평문 업로드 raise 를 지우면 → test_encryption_on_and_plaintext_refused 실패
  · 보존 기본을 30일로 바꾸면 → test_retention_default_is_unbounded 실패
  · 미상 표기를 지우거나 unknown 을 계정에 접으면 → test_sigma_shows_unknown_share 실패
  · vault 간 공유 테이블을 추가하면 → test_server_schema_has_no_cross_vault_sharing 실패
"""
import importlib
import os
import sys

import harness  # noqa: F401
from pytmuxlib import usagedb  # noqa: F401  (별칭 등록 — 아래 plugin import 전 필요)

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from synserver import app as sapp        # noqa: E402
from synserver import db as sdb          # noqa: E402

# 결정 1 — 되돌릴 수 없는 값(패스키 오리진). 문서(§5.2·§10.2)와 여기 한 곳에만 적는다.
RP_ID = "pytmux-sync.woojinkim.org"


def _screens():
    return importlib.import_module("pytmuxlib.plugins.claude-code.screens")


def _tokensync():
    return importlib.import_module("pytmuxlib.plugins.claude-code.tokensync")


# ── 결정 1: 호스팅(개인서버 + Cloudflare Tunnel) ────────────────────────────

async def test_deploy_defaults_bind_loopback_only():
    """앞단(Cloudflare Tunnel/cloudflared)이 TLS 를 끝내고 루프백으로 프록시하는
    전제이므로, 서버 자신은 **공개 인터페이스에 붙지 않는다**. 기본이 0.0.0.0 이면
    터널을 쓰는데도 원본 포트가 그대로 노출된다(방화벽 하나에 기대게 됨)."""
    p = sapp.build_parser()
    a = p.parse_args(["--rp-id", RP_ID])
    assert a.host == "127.0.0.1", "기본 바인딩은 루프백"
    assert a.origin is None, "오리진 미지정 → main 이 https://<rp-id> 로 합성"
    assert a.open_registration is False, "등록은 기본 잠김(공개 도메인에 열려 있다)"


async def test_rp_id_is_required_not_guessed():
    """`rp.id` 는 등록된 패스키를 전부 무효화하는 값이라 **추측 기본값을 두지 않는다**
    (Host 헤더 유래로 만들면 프록시 앞단이 바뀌는 순간 조용히 오리진이 갈린다)."""
    import contextlib
    import io
    with contextlib.redirect_stderr(io.StringIO()):   # argparse 사용법 출력 삼킴
        try:
            sapp.build_parser().parse_args([])
        except SystemExit:
            return                # 필수 인자 누락 → 종료(정상)
    raise AssertionError("--rp-id 가 필수가 아니다")


# ── 결정 2: 암호화 기본 on 확정 ─────────────────────────────────────────────

async def test_encryption_on_and_plaintext_refused():
    """암호화는 기본 on 이고, off 로 만들어도 **평문 업로드 경로가 없다**. '기본 on'
    만 해두고 off 경로를 열어 두면 설정 하나로 서버에 평문이 쌓인다."""
    ts = _tokensync()
    cli = ts.SyncClient(None, "/tmp", lambda *a, **kw: (200, {}, b"{}"),
                        db_path=":memory:")
    assert cli.encrypt is True, "기본 on"
    plain = ts.SyncClient(None, "/tmp", lambda *a, **kw: (200, {}, b"{}"),
                          encrypt=False, db_path=":memory:")
    for fn in ("push_limits", "push_xc"):
        try:
            getattr(plain, fn)()
        except ts.SyncError:
            pass
        else:
            raise AssertionError("%s 가 평문으로 올렸다" % fn)


# ── 결정 3: 보존 무기한 ─────────────────────────────────────────────────────

async def test_retention_default_is_unbounded():
    """새 머신이 붙으면 **전 기간 합집합**이 성립해야 한다는 결정 → 서버 purge 는
    기본 off. 켜는 것은 배포 환경변수뿐이고 CLI 플래그로도 두지 않는다(운영자가
    실수로 회계 이력을 자르는 문을 안 만든다)."""
    assert sapp.RETAIN_DAYS == 0.0
    app = sapp.SyncApp(sdb.connect(":memory:"), RP_ID, "https://" + RP_ID)
    assert app.retain_days == 0.0
    opts = {a.dest for a in sapp.build_parser()._actions}
    assert "retain_days" not in opts, "보존은 CLI 플래그가 아니라 배포 env"


# ── 결정 4: 계정 미상 = 별항 분리 + 비중 가시화 ─────────────────────────────

async def test_sigma_shows_unknown_share():
    """미상 비중을 Σ 줄에 노출한다 — 분리만 하고 숨기면 '계정 합 < 총합'이 미궁이
    되고 P3 백필 커버리지가 나빠지는 것도 안 보인다. 전량 귀속(미상 0)이면 표기
    자체가 없어야 한다(잘 되고 있을 때 잡음 0)."""
    S = _screens()
    scr = S.TokenLogScreen.__new__(S.TokenLogScreen)
    scr._xc_cov = {}
    assert scr._unknown_text() == "", "커버리지 없음(구버전 서버) → 무표시"
    scr._xc_cov = {"total": 100, "known": 100, "unknown": 0, "pct": 100.0}
    assert scr._unknown_text() == "", "전량 귀속 → 무표시"
    scr._xc_cov = {"total": 100, "known": 88, "unknown": 12, "pct": 88.0}
    assert "12%" in scr._unknown_text()
    scr._xc_cov = {"total": 1000, "known": 998, "unknown": 2, "pct": 99.8}
    assert "<1%" in scr._unknown_text(), "반올림 0% 로 '있는데 없음' 을 만들지 않는다"


async def test_sigma_line_actually_carries_unknown_share():
    """**호출부까지** 검사한다 — 헬퍼만 단언하면 `_sigma_text` 에서 호출을 빼도 테스트가
    통과하는 공허 통과가 된다(2026-07-25 교훈 2 와 같은 함정을 실제로 밟아 추가)."""
    S = _screens()
    scr = S.TokenLogScreen.__new__(S.TokenLogScreen)
    scr._total_all = 1_000
    scr._xc = {"full": 5_000, "cache_read": 10, "cache_create": 5}
    scr._xc_hosts = {}
    scr._xc_cov = {"total": 100, "known": 88, "unknown": 12, "pct": 88.0}
    line = scr._sigma_text(1_000)
    assert "12%" in line, "Σ 줄에 미상 비중이 없다(호출부 누락): %r" % line
    scr._xc_cov = {"total": 100, "known": 100, "unknown": 0, "pct": 100.0}
    clean = scr._sigma_text(1_000)
    assert "미상" not in clean and "unattributed" not in clean, "전량 귀속이면 무표기"


async def test_unknown_text_tolerates_hostile_upstream():
    """원격 보기(federation)에서 token_log 는 **신뢰불가 상류**가 실어 보낸 그대로
    클라에 온다. 잡값이 팝업을 터뜨리는 대신 표기만 생략해야 한다(F-C/F-D 와 같은 결)."""
    S = _screens()
    scr = S.TokenLogScreen.__new__(S.TokenLogScreen)
    for junk in ("나쁜값", [1, 2], {"total": "많이", "unknown": "조금"},
                 {"total": 10, "unknown": 999}, {"total": -5, "unknown": -1}):
        scr._xc_cov = junk
        assert scr._unknown_text() == "", "잡값 %r 에서 표기 생략" % (junk,)


# ── 결정 5: vault 완전 격리 ────────────────────────────────────────────────

async def test_server_schema_has_no_cross_vault_sharing():
    """공유 개념을 **지금 넣지 않는다**는 결정(필요해지면 서버만 마이그레이션).
    이 테스트가 깨지면 = 누군가 공유를 넣었다 = §10.2-5 결정을 다시 받아야 한다."""
    conn = sdb.connect(":memory:")
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert not any(t for t in tables
                   if any(w in t.lower()
                          for w in ("share", "group", "member", "team", "org"))), \
        "공유/그룹 테이블 발견: %s" % sorted(tables)
    # vault 를 가리키는 외래키가 event/passkey/device 처럼 **소유 1:N** 뿐인지 —
    # '다른 vault 를 볼 권한' 을 뜻하는 컬럼이 생기면 여기서 걸린다.
    for t in sorted(tables):
        cols = {r[1].lower() for r in conn.execute(
            "PRAGMA table_info(%s)" % t).fetchall()}
        assert not (cols & {"shared_with", "peer_vault", "grantee",
                            "group_id", "member_of"}), \
            "%s 에 공유 컬럼: %s" % (t, sorted(cols))
    conn.close()
