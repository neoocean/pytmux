"""동기화 서버 P1 — 자작 WebAuthn 검증(tools/synserver/webauthnlib.py) 단위 테스트.

py_webauthn 대신 직접 구현했으므로(설계 §5.2, 의존성 = cryptography 하나) **부정
케이스가 본체**다. 여기 테스트는 가짜 인증기(진짜 키로 진짜 서명하는 테스트 하니스)를
만들어 정상 경로를 통과시킨 뒤, 검증 항목을 하나씩 무너뜨려 **전부 거부되는지** 본다.

되돌리면 실패해야 하는 오라클:
  · origin/challenge/type/rpIdHash 검사 제거 → 해당 negative 테스트 실패
  · UV 플래그 요구 제거 → test_rejects_missing_uv 실패
  · signCount 후퇴 거부 제거 → test_rejects_signcount_rollback 실패
  · CBOR 꼬리 바이트 허용 → test_cbor_rejects_trailing 실패
"""
import hashlib
import json
import os
import struct
import sys

import harness  # noqa: F401  (경로 설정)

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
from synserver import webauthnlib as wa   # noqa: E402

RP_ID = "sync.example.org"
ORIGIN = "https://sync.example.org"


# ── 최소 CBOR 인코더(테스트 전용 — 가짜 인증기가 응답을 만들 때 쓴다) ────────

def _cb(major, arg):
    if arg < 24:
        return bytes([major << 5 | arg])
    if arg < 256:
        return bytes([major << 5 | 24, arg])
    if arg < 65536:
        return bytes([major << 5 | 25]) + struct.pack(">H", arg)
    return bytes([major << 5 | 26]) + struct.pack(">I", arg)


def cbor(v):
    if isinstance(v, bool):
        return bytes([0xF5 if v else 0xF4])
    if isinstance(v, int):
        return _cb(0, v) if v >= 0 else _cb(1, -1 - v)
    if isinstance(v, bytes):
        return _cb(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return _cb(3, len(b)) + b
    if isinstance(v, list):
        return _cb(4, len(v)) + b"".join(cbor(x) for x in v)
    if isinstance(v, dict):
        return _cb(5, len(v)) + b"".join(cbor(k) + cbor(x) for k, x in v.items())
    raise TypeError(type(v))


# ── 가짜 인증기 ────────────────────────────────────────────────────────────

class FakeAuthenticator:
    """진짜 키로 진짜 서명하는 테스트 인증기(ES256 기본, Ed25519 선택)."""

    def __init__(self, alg=wa.ALG_ES256, rp_id=RP_ID):
        from cryptography.hazmat.primitives.asymmetric import ec, ed25519
        self.alg = alg
        self.rp_id = rp_id
        self.cred_id = os.urandom(20)
        self.count = 0
        if alg == wa.ALG_ES256:
            self.sk = ec.generate_private_key(ec.SECP256R1())
            n = self.sk.public_key().public_numbers()
            self.cose = {1: 2, 3: wa.ALG_ES256, -1: 1,
                         -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")}
        else:
            self.sk = ed25519.Ed25519PrivateKey.generate()
            from cryptography.hazmat.primitives import serialization as ser
            raw = self.sk.public_key().public_bytes(
                ser.Encoding.Raw, ser.PublicFormat.Raw)
            self.cose = {1: 1, 3: wa.ALG_EDDSA, -1: 6, -2: raw}

    def _client_data(self, typ, challenge, origin=ORIGIN):
        return json.dumps({"type": typ,
                           "challenge": wa.b64u_encode(challenge),
                           "origin": origin,
                           "crossOrigin": False}).encode()

    def _auth_data(self, flags, count=None, rp_id=None, attested=False):
        rp = (rp_id or self.rp_id).encode()
        ad = hashlib.sha256(rp).digest() + bytes([flags])
        ad += struct.pack(">I", self.count if count is None else count)
        if attested:
            ad += b"\x00" * 16                       # aaguid
            ad += struct.pack(">H", len(self.cred_id)) + self.cred_id
            ad += cbor(self.cose)
        return ad

    def register(self, challenge, *, flags=wa.FLAG_UP | wa.FLAG_UV | wa.FLAG_AT,
                 origin=ORIGIN, rp_id=None, typ="webauthn.create"):
        cd = self._client_data(typ, challenge, origin)
        ad = self._auth_data(flags, rp_id=rp_id, attested=bool(flags & wa.FLAG_AT))
        att = cbor({"fmt": "none", "attStmt": {}, "authData": ad})
        return att, cd

    def sign(self, challenge, *, flags=wa.FLAG_UP | wa.FLAG_UV, count=None,
             origin=ORIGIN, rp_id=None, typ="webauthn.get"):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        cd = self._client_data(typ, challenge, origin)
        if count is None:
            self.count += 1
            count = self.count
        ad = self._auth_data(flags, count=count, rp_id=rp_id)
        msg = ad + hashlib.sha256(cd).digest()
        if self.alg == wa.ALG_ES256:
            sig = self.sk.sign(msg, ec.ECDSA(hashes.SHA256()))
        else:
            sig = self.sk.sign(msg)
        return ad, cd, sig


def _reject(fn, *a, **kw):
    """검증이 **반드시 거부**해야 하는 호출. 통과하면 테스트 실패."""
    try:
        fn(*a, **kw)
    except wa.WebAuthnError:
        return
    raise AssertionError("거부됐어야 하는 입력이 통과했다")


# ── 정상 경로 ──────────────────────────────────────────────────────────────

async def test_registration_and_assertion_roundtrip_es256():
    auth = FakeAuthenticator()
    ch = wa.new_challenge()
    reg = wa.verify_registration(*auth.register(ch), challenge=ch,
                                 rp_id=RP_ID, origin=ORIGIN)
    assert reg["cred_id"] == auth.cred_id
    ch2 = wa.new_challenge()
    ad, cd, sig = auth.sign(ch2)
    n = wa.verify_assertion(ad, cd, sig, reg["cose_key"], ch2, RP_ID, ORIGIN,
                            stored_sign_count=reg["sign_count"])
    assert n == 1


async def test_roundtrip_ed25519():
    auth = FakeAuthenticator(alg=wa.ALG_EDDSA)
    ch = wa.new_challenge()
    reg = wa.verify_registration(*auth.register(ch), challenge=ch,
                                 rp_id=RP_ID, origin=ORIGIN)
    ch2 = wa.new_challenge()
    ad, cd, sig = auth.sign(ch2)
    assert wa.verify_assertion(ad, cd, sig, reg["cose_key"], ch2, RP_ID,
                               ORIGIN, stored_sign_count=0) == 1


# ── 부정 케이스(본체) ──────────────────────────────────────────────────────

async def test_rejects_wrong_origin_challenge_type():
    auth = FakeAuthenticator()
    ch = wa.new_challenge()
    # 오리진이 다르면(피싱 사이트) 거부 — 부분일치·서브도메인도 안 된다.
    att, cd = auth.register(ch, origin="https://evil.example.org")
    _reject(wa.verify_registration, att, cd, ch, RP_ID, ORIGIN)
    # 챌린지 재사용/불일치
    att, cd = auth.register(ch)
    _reject(wa.verify_registration, att, cd, wa.new_challenge(), RP_ID, ORIGIN)
    # type 혼동(create ↔ get)
    att, cd = auth.register(ch, typ="webauthn.get")
    _reject(wa.verify_registration, att, cd, ch, RP_ID, ORIGIN)


async def test_rejects_wrong_rp_id():
    auth = FakeAuthenticator(rp_id="other.example.org")
    ch = wa.new_challenge()
    _reject(wa.verify_registration, *auth.register(ch), challenge=ch,
            rp_id=RP_ID, origin=ORIGIN)


async def test_rejects_missing_uv():
    """UV(생체·PIN)를 요구하지 않으면 훔친 기기로 로그인된다 — 필수 유지."""
    auth = FakeAuthenticator()
    ch = wa.new_challenge()
    _reject(wa.verify_registration, *auth.register(ch, flags=wa.FLAG_UP | wa.FLAG_AT),
            challenge=ch, rp_id=RP_ID, origin=ORIGIN)
    reg = wa.verify_registration(*auth.register(ch), challenge=ch, rp_id=RP_ID,
                                 origin=ORIGIN)
    ch2 = wa.new_challenge()
    ad, cd, sig = auth.sign(ch2, flags=wa.FLAG_UP)
    _reject(wa.verify_assertion, ad, cd, sig, reg["cose_key"], ch2, RP_ID, ORIGIN)


async def test_rejects_tampered_signature_and_authdata():
    auth = FakeAuthenticator()
    ch = wa.new_challenge()
    reg = wa.verify_registration(*auth.register(ch), challenge=ch, rp_id=RP_ID,
                                 origin=ORIGIN)
    ch2 = wa.new_challenge()
    ad, cd, sig = auth.sign(ch2)
    bad = bytearray(sig)
    bad[-1] ^= 0x01
    _reject(wa.verify_assertion, ad, cd, bytes(bad), reg["cose_key"], ch2,
            RP_ID, ORIGIN)
    bad_ad = bytearray(ad)
    bad_ad[36] ^= 0x01                      # signCount 변조 → 서명 불일치
    _reject(wa.verify_assertion, bytes(bad_ad), cd, sig, reg["cose_key"], ch2,
            RP_ID, ORIGIN)
    # 다른 크리덴셜의 키로는 검증되지 않는다.
    other = FakeAuthenticator()
    reg2 = wa.verify_registration(*other.register(ch), challenge=ch, rp_id=RP_ID,
                                  origin=ORIGIN)
    _reject(wa.verify_assertion, ad, cd, sig, reg2["cose_key"], ch2, RP_ID, ORIGIN)


async def test_rejects_signcount_rollback():
    auth = FakeAuthenticator()
    ch = wa.new_challenge()
    reg = wa.verify_registration(*auth.register(ch), challenge=ch, rp_id=RP_ID,
                                 origin=ORIGIN)
    ch2 = wa.new_challenge()
    ad, cd, sig = auth.sign(ch2, count=5)
    assert wa.verify_assertion(ad, cd, sig, reg["cose_key"], ch2, RP_ID, ORIGIN,
                               stored_sign_count=1) == 5
    ch3 = wa.new_challenge()
    ad, cd, sig = auth.sign(ch3, count=3)          # 후퇴 = 복제 의심
    _reject(wa.verify_assertion, ad, cd, sig, reg["cose_key"], ch3, RP_ID,
            ORIGIN, stored_sign_count=5)
    # 인증기가 counter 를 안 쓰는(0 고정) 경우는 정상 — 거부하면 흔한 기기가 잠긴다.
    ch4 = wa.new_challenge()
    ad, cd, sig = auth.sign(ch4, count=0)
    assert wa.verify_assertion(ad, cd, sig, reg["cose_key"], ch4, RP_ID, ORIGIN,
                               stored_sign_count=0) == 0


async def test_rejects_unsupported_alg():
    auth = FakeAuthenticator()
    ch = wa.new_challenge()
    auth.cose = dict(auth.cose)
    auth.cose[3] = -65535                          # 알 수 없는 알고리즘
    att, cd = auth.register(ch)
    reg = wa.verify_registration(att, cd, ch, RP_ID, ORIGIN)   # 등록은 통과(키 보관)
    ch2 = wa.new_challenge()
    ad, cd, sig = auth.sign(ch2)
    _reject(wa.verify_assertion, ad, cd, sig, reg["cose_key"], ch2, RP_ID, ORIGIN)


# ── CBOR 파서 자체(공격면) ─────────────────────────────────────────────────

async def test_cbor_rejects_trailing_and_deep_and_truncated():
    assert wa.cbor_decode_all(cbor({"a": 1, "b": b"xy"})) == {"a": 1, "b": b"xy"}
    _reject(wa.cbor_decode_all, cbor({"a": 1}) + b"\x00")       # 꼬리 바이트
    deep = 1
    for _ in range(12):
        deep = [deep]
    _reject(wa.cbor_decode_all, cbor(deep))                     # 중첩 폭탄
    _reject(wa.cbor_decode_all, b"\x42\x01")                    # 길이만 크고 잘림
    _reject(wa.cbor_decode_all, b"\xfb\x00\x00\x00\x00\x00\x00\x00\x00")  # float


async def test_parse_auth_data_rejects_bad_lengths():
    _reject(wa.parse_auth_data, b"\x00" * 36)                   # 37B 미만
    ad = hashlib.sha256(RP_ID.encode()).digest() + bytes([wa.FLAG_UP | wa.FLAG_AT])
    ad += struct.pack(">I", 0) + b"\x00" * 16 + struct.pack(">H", 999)
    _reject(wa.parse_auth_data, ad)                             # credId 길이 과장


async def test_b64u_roundtrip_and_reject():
    raw = os.urandom(33)
    assert wa.b64u_decode(wa.b64u_encode(raw)) == raw
    _reject(wa.b64u_decode, "!!!not-base64!!!")
