"""WebAuthn(패스키) 검증 — 표준 라이브러리 + cryptography 만으로.

설계: docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md §5.2.
동기화 서버(`tools/synserver/`)는 pytmux 런타임과 별개로 사용자가 자기 박스에 올린다 —
그래서 의존성을 `cryptography` 하나로 묶고, ASGI 프레임워크·py_webauthn 없이 간다
(pytmux 가 VT 파서·화면 모델을 자작해 온 것과 같은 결).

**우리가 검증하는 것 / 하지 않는 것**을 먼저 못박는다:

  검증한다 — clientData 의 `type`·`challenge`·`origin`, authData 의 `rpIdHash`,
             UP(사용자 존재)·UV(사용자 검증) 플래그, 등록 시 AT 플래그,
             인증 시 서명(ES256/EdDSA/RS256), signCount 후퇴(복제 의심).
  하지 않는다 — **attestation 검증**(어떤 인증기인지 따지지 않는다). 개인용 vault 라
             인증기 출처를 강제할 이유가 없고, 강제하면 사용자가 쓰던 패스키가
             거부돼 실익 없이 잠긴다. 그래서 `attStmt` 는 읽지도 않는다.

여기 함수는 전부 순수 함수다(네트워크·DB·시간 없음 — 챌린지 수명은 호출자 몫).
검증 실패는 전부 `WebAuthnError` 로 올린다. **조용한 True 금지**.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct

FLAG_UP = 0x01          # user present
FLAG_UV = 0x04          # user verified(생체·PIN) — 우리는 필수
FLAG_AT = 0x40          # attested credential data 포함(등록 응답)

# COSE alg 식별자 — 브라우저/인증기가 실제로 쓰는 것만 허용한다.
ALG_ES256 = -7
ALG_EDDSA = -8
ALG_RS256 = -257
SUPPORTED_ALGS = (ALG_ES256, ALG_EDDSA, ALG_RS256)


class WebAuthnError(Exception):
    """검증 실패. 서버는 사유를 **카운터로만** 남기고 응답은 일반화한다(§5.5)."""


# ── base64url ──────────────────────────────────────────────────────────────

def b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64u_decode(s: str) -> bytes:
    if not isinstance(s, str):
        raise WebAuthnError("base64url 문자열이 아님")
    pad = "=" * ((-len(s)) % 4)
    try:
        return base64.urlsafe_b64decode(s + pad)
    except Exception as e:      # binascii.Error 등
        raise WebAuthnError("base64url 디코드 실패") from e


def new_challenge(n: int = 32) -> bytes:
    return os.urandom(n)


# ── 최소 CBOR 디코더 ────────────────────────────────────────────────────────
# WebAuthn 이 쓰는 범위만 지원한다: 정수(0/1), 바이트열(2), 문자열(3), 배열(4),
# 맵(5), 단순값 false/true/null(7). 그 밖(부동소수·태그·무한길이)은 **거부**한다 —
# 넓게 받아들이면 파서가 공격면이 된다.

def cbor_decode(data: bytes):
    """(값, 소비한 바이트 수). 남은 바이트가 있어도 여기선 오류가 아니다."""
    val, off = _cbor(data, 0)
    return val, off


def cbor_decode_all(data: bytes):
    """전체가 정확히 한 항목이어야 한다(꼬리 바이트 = 거부)."""
    val, off = _cbor(data, 0)
    if off != len(data):
        raise WebAuthnError("CBOR 뒤에 남는 바이트")
    return val


def _head(data: bytes, off: int):
    if off >= len(data):
        raise WebAuthnError("CBOR 잘림")
    ib = data[off]
    major, ai = ib >> 5, ib & 0x1F
    off += 1
    if ai < 24:
        return major, ai, off
    if ai == 24:
        if off + 1 > len(data):
            raise WebAuthnError("CBOR 잘림")
        return major, data[off], off + 1
    for n, fmt in ((25, ">H"), (26, ">I"), (27, ">Q")):
        if ai == n:
            size = struct.calcsize(fmt)
            if off + size > len(data):
                raise WebAuthnError("CBOR 잘림")
            return major, struct.unpack_from(fmt, data, off)[0], off + size
    raise WebAuthnError("지원하지 않는 CBOR 헤더(%d)" % ai)


def _cbor(data: bytes, off: int, depth: int = 0):
    if depth > 8:
        raise WebAuthnError("CBOR 중첩이 너무 깊음")
    major, arg, off = _head(data, off)
    if major == 0:
        return arg, off
    if major == 1:
        return -1 - arg, off
    if major in (2, 3):
        end = off + arg
        if end > len(data):
            raise WebAuthnError("CBOR 잘림")
        chunk = data[off:end]
        if major == 3:
            try:
                chunk = chunk.decode("utf-8")
            except UnicodeDecodeError as e:
                raise WebAuthnError("CBOR 문자열 인코딩 오류") from e
        return chunk, end
    if major == 4:
        out = []
        for _ in range(arg):
            v, off = _cbor(data, off, depth + 1)
            out.append(v)
        return out, off
    if major == 5:
        out = {}
        for _ in range(arg):
            k, off = _cbor(data, off, depth + 1)
            v, off = _cbor(data, off, depth + 1)
            if isinstance(k, (str, int)):
                out[k] = v
            else:
                raise WebAuthnError("CBOR 맵 키 형식 오류")
        return out, off
    if major == 7:
        if arg == 20:
            return False, off
        if arg == 21:
            return True, off
        if arg in (22, 23):
            return None, off
        raise WebAuthnError("지원하지 않는 CBOR 단순값")
    raise WebAuthnError("지원하지 않는 CBOR 주 타입(%d)" % major)


# ── authData 파싱 ──────────────────────────────────────────────────────────

def parse_auth_data(auth_data: bytes) -> dict:
    """authenticatorData → dict(rp_id_hash, flags, sign_count, cred_id, cose_key).

    등록(AT 플래그)이면 credential 정보까지, 인증이면 앞 37바이트만 의미가 있다."""
    if len(auth_data) < 37:
        raise WebAuthnError("authData 가 너무 짧음")
    rp_id_hash = auth_data[:32]
    flags = auth_data[32]
    sign_count = struct.unpack_from(">I", auth_data, 33)[0]
    out = {"rp_id_hash": rp_id_hash, "flags": flags, "sign_count": sign_count,
           "cred_id": None, "cose_key": None}
    if flags & FLAG_AT:
        if len(auth_data) < 55:
            raise WebAuthnError("attested credential data 가 잘림")
        cred_len = struct.unpack_from(">H", auth_data, 53)[0]
        start = 55
        end = start + cred_len
        if cred_len == 0 or end > len(auth_data):
            raise WebAuthnError("credentialId 길이 오류")
        out["cred_id"] = auth_data[start:end]
        key, used = cbor_decode(auth_data[end:])
        if not isinstance(key, dict):
            raise WebAuthnError("COSE 키 형식 오류")
        out["cose_key"] = key
        out["_key_bytes"] = auth_data[end:end + used]
    return out


# ── COSE 키 → 서명 검증 ────────────────────────────────────────────────────

def cose_alg(cose_key: dict) -> int:
    alg = cose_key.get(3)
    if alg not in SUPPORTED_ALGS:
        raise WebAuthnError("지원하지 않는 서명 알고리즘(%r)" % (alg,))
    return alg


def _public_key(cose_key: dict):
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa
    from cryptography.hazmat.primitives.asymmetric.utils import (
        encode_dss_signature)          # noqa: F401  (검증 경로 대칭용 import 아님)
    kty = cose_key.get(1)
    alg = cose_alg(cose_key)
    if kty == 2 and alg == ALG_ES256:              # EC2 / P-256
        if cose_key.get(-1) != 1:
            raise WebAuthnError("지원하지 않는 EC 곡선")
        x, y = cose_key.get(-2), cose_key.get(-3)
        if not isinstance(x, bytes) or not isinstance(y, bytes):
            raise WebAuthnError("EC 좌표 형식 오류")
        if len(x) != 32 or len(y) != 32:
            raise WebAuthnError("EC 좌표 길이 오류")
        nums = ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1())
        return nums.public_key()
    if kty == 1 and alg == ALG_EDDSA:              # OKP / Ed25519
        if cose_key.get(-1) != 6:
            raise WebAuthnError("지원하지 않는 OKP 곡선")
        x = cose_key.get(-2)
        if not isinstance(x, bytes) or len(x) != 32:
            raise WebAuthnError("Ed25519 공개키 길이 오류")
        return ed25519.Ed25519PublicKey.from_public_bytes(x)
    if kty == 3 and alg == ALG_RS256:              # RSA
        n, e = cose_key.get(-1), cose_key.get(-2)
        if not isinstance(n, bytes) or not isinstance(e, bytes):
            raise WebAuthnError("RSA 파라미터 형식 오류")
        nums = rsa.RSAPublicNumbers(int.from_bytes(e, "big"),
                                    int.from_bytes(n, "big"))
        return nums.public_key()
    raise WebAuthnError("지원하지 않는 키 타입(kty=%r, alg=%r)" % (kty, alg))


def verify_signature(cose_key: dict, message: bytes, signature: bytes) -> None:
    """서명 검증. 실패는 예외 — **불린을 돌려주면 호출자가 안 보고 넘어간다**."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding
    pub = _public_key(cose_key)
    alg = cose_alg(cose_key)
    try:
        if alg == ALG_ES256:
            pub.verify(signature, message, ec.ECDSA(hashes.SHA256()))
        elif alg == ALG_EDDSA:
            pub.verify(signature, message)
        else:
            pub.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as e:
        raise WebAuthnError("서명이 유효하지 않음") from e
    except Exception as e:            # 형식 오류 등도 실패로 수렴
        raise WebAuthnError("서명 검증 실패") from e


# ── clientDataJSON ─────────────────────────────────────────────────────────

def check_client_data(client_data: bytes, want_type: str, challenge: bytes,
                      origin: str) -> dict:
    """clientDataJSON 검증(타입·챌린지·오리진). **오리진 고정이 피싱 방어의 핵심**이라
    부분 일치·와일드카드를 허용하지 않는다."""
    try:
        cd = json.loads(client_data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise WebAuthnError("clientDataJSON 파싱 실패") from e
    if not isinstance(cd, dict):
        raise WebAuthnError("clientDataJSON 형식 오류")
    if cd.get("type") != want_type:
        raise WebAuthnError("clientData type 불일치")
    got = b64u_decode(cd.get("challenge") or "")
    if len(got) != len(challenge) or not _consteq(got, challenge):
        raise WebAuthnError("challenge 불일치")
    if cd.get("origin") != origin:
        raise WebAuthnError("origin 불일치")
    return cd


def _consteq(a: bytes, b: bytes) -> bool:
    import hmac as _h
    return _h.compare_digest(a, b)


# ── 등록 / 인증 ────────────────────────────────────────────────────────────

def verify_registration(att_obj: bytes, client_data: bytes, challenge: bytes,
                        rp_id: str, origin: str) -> dict:
    """등록 응답 검증 → {cred_id, cose_key(bytes), sign_count, aaguid}.

    attestation 은 검증하지 않는다(모듈 docstring 참조) — `fmt`/`attStmt` 를 보지
    않으므로 어떤 인증기든 받아들이되, **authData 의 나머지는 전부 검사**한다."""
    check_client_data(client_data, "webauthn.create", challenge, origin)
    att = cbor_decode_all(att_obj)
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise WebAuthnError("attestationObject 형식 오류")
    ad = parse_auth_data(att["authData"])
    _check_rp_and_flags(ad, rp_id, require_at=True)
    return {"cred_id": ad["cred_id"], "cose_key": ad["_key_bytes"],
            "sign_count": ad["sign_count"],
            "aaguid": att["authData"][37:53]}


def verify_assertion(auth_data: bytes, client_data: bytes, signature: bytes,
                     cose_key_bytes: bytes, challenge: bytes, rp_id: str,
                     origin: str, stored_sign_count: int = 0) -> int:
    """인증(로그인) 응답 검증 → 새 signCount.

    서명 대상은 `authData || SHA256(clientDataJSON)` 이다(스펙 §7.2). signCount 는
    인증기가 0 을 유지하는 경우가 흔해 **둘 다 0 이 아닐 때만** 후퇴를 거부한다 —
    복제 의심 신호이므로 여기서 통과시키면 안 된다."""
    check_client_data(client_data, "webauthn.get", challenge, origin)
    ad = parse_auth_data(auth_data)
    _check_rp_and_flags(ad, rp_id, require_at=False)
    key = cbor_decode_all(cose_key_bytes)
    if not isinstance(key, dict):
        raise WebAuthnError("저장된 COSE 키 형식 오류")
    verify_signature(key, auth_data + hashlib.sha256(client_data).digest(),
                     signature)
    new_count = ad["sign_count"]
    if new_count and stored_sign_count and new_count <= stored_sign_count:
        raise WebAuthnError("signCount 후퇴(인증기 복제 의심)")
    return new_count


def _check_rp_and_flags(ad: dict, rp_id: str, require_at: bool) -> None:
    if not _consteq(ad["rp_id_hash"], hashlib.sha256(rp_id.encode()).digest()):
        raise WebAuthnError("rpIdHash 불일치")
    if not ad["flags"] & FLAG_UP:
        raise WebAuthnError("사용자 존재(UP) 플래그 없음")
    if not ad["flags"] & FLAG_UV:
        raise WebAuthnError("사용자 검증(UV) 플래그 없음")
    if require_at and not (ad["flags"] & FLAG_AT):
        raise WebAuthnError("attested credential data 없음")
