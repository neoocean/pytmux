"""토큰 동기화 P4 — `usage_xc`(트랜스크립트 권위 회계) 머신 간 동기화.

설계 §5.5·§10 P4. P2 하네스(소켓 없이 클라 transport → 서버 `SyncApp.handle`)를 그대로
쓰고 종류만 늘린다. 핵심 주장은 **합집합이 멱등·순서무관**이라는 것 — `xkey` 가 전역
고유하고 저장소가 `INSERT OR IGNORE` 이기 때문이다.

되돌리면 실패해야 하는 오라클:
  · tab/pane/pytmux_session 을 화이트리스트에 넣으면 → test_xc_payload_omits_local_coords
  · 수치 검증(음수·거대값·bool)을 빼면 → test_validate_xc_rejects_adversarial
  · 원산지 tzoff/host 를 로컬값으로 덮으면 → test_origin_tzoff_and_host_survive_merge
  · 커서를 실패 시에도 전진시키면 → test_xc_cursor_not_advanced_on_failure
  · kind 분기를 없애 xc 를 limits 로 처리하면 → test_kinds_do_not_cross_contaminate
  · 받은 행 회수(P3 재적용)를 빼면 → test_pulled_rows_get_account_backfill
"""
import json

import harness  # noqa: F401
from pytmuxlib import usagedb
from pytmuxlib import tokensync

from run import skip                                    # noqa: E402
from test_token_sync_p2 import _two_machines            # noqa: E402


def _xc(conn, xkey, host=None, ts=1_000_000.0, acct="me@x.io", uuid="s1",
        inp=10, out=5, cc=0, cr=0, tzoff=32400):
    usagedb.insert_xc(conn, {
        "xkey": xkey, "ts": ts, "session_uuid": uuid, "model": "opus-4.8",
        "input": inp, "output": out, "cache_create": cc, "cache_read": cr,
        "is_sidechain": 0, "tzoff": tzoff, "host": host},
        tab=1, pane=2, pytmux_session=3, account=acct)


def _need_crypto():
    if not tokensync.syncrypto.available():
        skip("cryptography 미설치 — AEAD 봉인 없이 push/pull 경로를 탈 수 없음")
        return True
    return False


# ── 순수 함수 ──────────────────────────────────────────────────────────────

async def test_xc_payload_omits_local_coords():
    """전송 레코드에 **로컬 좌표(tab/pane/pytmux_session)가 없어야 한다** — 받는
    쪽에서 의미가 없고, 내보내면 작업 구조가 서버에 남는다(§8.1)."""
    row = {"xkey": "m:1", "ts": 100.0, "session_uuid": "s1", "model": "opus",
           "account": "a@b.c", "input": 1, "output": 2, "cache_create": 3,
           "cache_read": 4, "is_sidechain": 0, "tzoff": 32400, "host": None,
           "tab": 7, "pane": 9, "pytmux_session": 11,
           "secret_new_column": "이건 나가면 안 된다"}
    out = tokensync._xc_payload(row, "hostA")
    assert set(out) == set(tokensync._XC_FIELDS) | {"v"}
    for leak in ("tab", "pane", "pytmux_session", "secret_new_column"):
        assert leak not in out
    assert out["host"] == "hostA"           # host 없으면 자기 것으로 채움
    assert json.dumps(out)


async def test_validate_xc_rejects_adversarial():
    now = 1_000_000.0
    ok = {"v": 1, "xkey": "m:1", "ts": now, "session_uuid": "s1",
          "model": "opus-4.8", "account": "a@b.c", "input": 10, "output": 5,
          "cache_create": 0, "cache_read": 0, "is_sidechain": 0,
          "tzoff": 32400, "host": "h1"}
    good = tokensync._validate_xc(dict(ok), now)
    assert good["input"] == 10 and good["tzoff"] == 32400
    bad = [
        dict(ok, v=2),                                  # 버전
        dict(ok, xkey=""),                              # 빈 키(멱등 근거 소실)
        dict(ok, xkey=123),
        dict(ok, xkey="x" * 300),
        dict(ok, ts=now + 400 * 24 * 3600),             # 미래 창 밖
        dict(ok, ts=now - 400 * 24 * 3600),
        dict(ok, ts=None),
        dict(ok, input=-1),                             # 음수 토큰
        dict(ok, output=10 ** 15),                      # 거대값(Σ 오염)
        dict(ok, cache_read=True),                      # bool 은 숫자가 아니다
        dict(ok, cache_create="많이"),
        dict(ok, is_sidechain=2),
        dict(ok, tzoff=10 ** 6),                        # 말이 안 되는 오프셋
        dict(ok, tzoff="KST"),
        dict(ok, session_uuid="s" * 100),
        dict(ok, model="m" * 100),
        dict(ok, account="a" * 500),
        dict(ok, host="h" * 100),
        "문자열",
    ]
    for b in bad:
        assert tokensync._validate_xc(b, now) is None, b
    # ISO 문자열 ts 도 받아 epoch 로 정규화한다(트랜스크립트 원형이 ISO 다).
    iso = dict(ok, ts="2026-07-22T10:00:00.000Z")
    got = tokensync._validate_xc(iso, usagedb._iso_to_epoch(iso["ts"]))
    assert isinstance(got["ts"], float) and got["ts"] > 0
    # 누락된 수치는 0 으로(구머신이 필드를 안 실어도 거부하지 않는다).
    lean = {"v": 1, "xkey": "m:2", "ts": now}
    assert tokensync._validate_xc(lean, now)["cache_read"] == 0


# ── E2E: 머신 2대 ──────────────────────────────────────────────────────────

async def test_xc_merges_and_totals_match_on_both_machines():
    """G1: 교차 병합 후 **양쪽 집계가 같다** — 이 작업의 존재 이유."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    _xc(m1.conn, "a:1", inp=100, out=50, cr=900)
    _xc(m1.conn, "a:2", inp=7, out=3)
    _xc(m2.conn, "b:1", inp=1, out=1)
    m1.cli.push_xc()
    m2.cli.push_xc()
    m2.cli.pull()
    m1.cli.pull()
    t1, t2 = usagedb.xc_totals(m1.conn), usagedb.xc_totals(m2.conn)
    assert t1 == t2
    assert t1["full"] == (100 + 50 + 900) + (7 + 3) + (1 + 1)
    assert usagedb.xc_count(m1.conn) == usagedb.xc_count(m2.conn) == 3


async def test_merge_is_idempotent_and_order_independent():
    """G4: 같은 이벤트를 두 번 받아도 행수·합계 불변(xkey PK)."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    _xc(m1.conn, "a:1", inp=100)
    _xc(m1.conn, "a:2", inp=200)
    m1.cli.push_xc()
    assert m2.cli.pull()["xc"] == 2
    before = usagedb.xc_totals(m2.conn)
    # 커서를 되돌려 전량 재수신 → 중복이 생기면 합계가 뛴다.
    usagedb.set_sync_remote(m2.conn, tokensync.SyncClient.REMOTE, cursor="0")
    assert m2.cli.pull()["xc"] == 0
    assert usagedb.xc_totals(m2.conn) == before
    assert usagedb.xc_count(m2.conn) == 2


async def test_origin_tzoff_and_host_survive_merge():
    """원산지 tzoff/host 가 보존돼야 P5 의 일자 버킷이 머신마다 갈리지 않는다."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    _xc(m1.conn, "a:1", tzoff=-25200)          # 미국 서부에서 적재한 행
    m1.cli.push_xc()
    m2.cli.pull()
    row = m2.conn.execute(
        "SELECT tzoff, host, tab, pane FROM usage_xc WHERE xkey='a:1'").fetchone()
    assert row["tzoff"] == -25200
    assert row["host"] == m1.cli.host_id       # 누가 관측했는지 유지
    assert row["tab"] is None and row["pane"] is None   # 로컬 좌표는 안 온다


async def test_push_does_not_echo_foreign_xc_rows():
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    _xc(m1.conn, "a:1")
    m1.cli.push_xc()
    m2.cli.pull()
    assert m2.cli.push_xc()["sent"] == 0        # 받은 행을 되돌려 보내지 않는다


async def test_xc_cursor_not_advanced_on_failure():
    """업로드가 실패하면 커서는 그대로다 — 그게 곧 재시도 큐(§5.6)."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    _xc(m1.conn, "a:1")
    orig = m1.cli.transport

    def boom(method, path, query="", body=b"", headers=None):
        if path == "/v1/events" and method == "POST":
            return 500, {}, b"nope"
        return orig(method, path, query, body, headers)

    m1.cli.transport = boom
    try:
        m1.cli.push_xc()
        raise AssertionError("실패가 조용히 넘어갔다")
    except tokensync.SyncError:
        pass
    assert usagedb.get_export_cursor(m1.conn, "usage_xc") == 0
    m1.cli.transport = orig
    assert m1.cli.push_xc()["sent"] == 1        # 재시도가 그대로 이어진다
    assert usagedb.get_export_cursor(m1.conn, "usage_xc") > 0


async def test_kinds_do_not_cross_contaminate():
    """limits 와 usage_xc 가 섞이지 않는다 — 한쪽 이벤트가 다른 테이블에 들어가면
    회계가 조용히 망가진다."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    m1.add_limits(1_000_000.0, 40)
    _xc(m1.conn, "a:1", inp=11)
    m1.cli.push_limits()
    m1.cli.push_xc()
    out = m2.cli.pull()
    assert out["merged"] == 2 and out["xc"] == 1 and out["rejected"] == 0
    assert usagedb.limits_count(m2.conn) == 1
    assert usagedb.xc_count(m2.conn) == 1
    assert usagedb.xc_totals(m2.conn)["input"] == 11


async def test_forged_xc_records_are_rejected_without_polluting_db():
    """악성 서버가 조작·재조합 레코드를 돌려주면 **전량 거부**되고 DB 는 무오염."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    _xc(m1.conn, "a:1", inp=100)
    m1.cli.push_xc()
    orig = m2.cli.transport

    def tamper(method, path, query="", body=b"", headers=None):
        st, hd, resp = orig(method, path, query, body, headers)
        if method == "GET" and path == "/v1/events":
            lines = []
            for i, ln in enumerate(resp.decode().splitlines(), 1):
                if not ln.strip():
                    continue
                ev = json.loads(ln)
                ev["ct"] = tokensync._b64u(b"\x00" * 40)   # 봉인 파괴
                lines.append(json.dumps(ev))
            resp = ("\n".join(lines) + "\n").encode()
        return st, hd, resp

    m2.cli.transport = tamper
    out = m2.cli.pull()
    assert out["merged"] == 0 and out["rejected"] >= 1
    assert usagedb.xc_count(m2.conn) == 0
    st = usagedb.get_sync_remote(m2.conn, tokensync.SyncClient.REMOTE)
    assert st["last_err"]                       # 조용한 실패 금지


async def test_pulled_rows_get_account_backfill():
    """받은 행이 계정 미상이어도 같은 세션의 알려진 계정으로 회수된다(P3 재적용).

    안 하면 "합쳤는데 계정별 통계가 여전히 unknown" 이 된다."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    _xc(m1.conn, "a:1", acct=None, uuid="sX")      # 계정 미확정 구간 적재분
    _xc(m1.conn, "a:2", acct=None, uuid="sX")
    m1.cli.push_xc()
    _xc(m2.conn, "b:1", acct="me@x.io", uuid="sX")  # 이 머신은 계정을 안다
    assert m2.cli.pull()["xc"] == 2
    accts = {r["account"] for r in m2.conn.execute(
        "SELECT account FROM usage_xc")}
    assert accts == {"me@x.io"}


async def test_account_whitelist_limits_what_leaves_xc():
    """내보내기 계정 화이트리스트는 usage_xc 에도 걸린다(§8.5)."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    _xc(m1.conn, "a:1", acct="work@corp.com")
    _xc(m1.conn, "a:2", acct="me@x.io")
    m1.cli.accounts = ("me@x.io",)
    m1.cli.push_xc()
    m2.cli.pull()
    rows = [r["xkey"] for r in m2.conn.execute("SELECT xkey FROM usage_xc")]
    assert rows == ["a:2"]


async def test_reset_cursors_covers_xc():
    """`:token-sync resync` 가 usage_xc 커서도 되돌려야 전량 재업로드가 성립한다."""
    conn = usagedb.connect(":memory:")
    usagedb.set_export_cursor(conn, "usage_xc", 42)
    usagedb.set_export_cursor(conn, "limits", 7)
    tokensync.reset_cursors(conn)
    assert usagedb.get_export_cursor(conn, "usage_xc") == 0
    assert usagedb.get_export_cursor(conn, "limits") == 0
    conn.close()


async def test_sync_once_reports_combined_sent():
    """한 바퀴 결과의 'sent' 는 limits + usage_xc 합계다(알림이 반쪽만 세지 않게)."""
    if _need_crypto():
        return
    app, clock, m1, m2 = _two_machines()
    m1.add_limits(1_000_000.0, 40)
    _xc(m1.conn, "a:1")
    _xc(m1.conn, "a:2")
    out = tokensync._sync_once(m1.cli)
    assert out["push"]["sent"] == 1 and out["push_xc"]["sent"] == 2
    assert out["sent"] == 3
