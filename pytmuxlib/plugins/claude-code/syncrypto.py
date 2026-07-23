"""토큰 동기화 암호 원시함수 — 가명(HMAC)·키 계층(HKDF)·레코드 봉인(AEAD).

설계: docs/internal/TOKEN_SYNC_MULTI_MACHINE_DESIGN_2026-07-23.md §5.3.

요지 — **서버에 Claude 계정 이름을 두지 않는다**. 밖으로 나가는 것은
① 가명 `acct_id`(계정 구분만 가능, 누구인지는 모름), ② 멱등 키 `rkey`(원문 xkey
아님), ③ 나머지 필드를 통째로 봉인한 암호문뿐이다. 서버는 복호 키를 갖지 않는다.

키 계층:

    K(마스터 32B, 머신이 공유)
      ├ K_id  = HKDF(K, "pytmux-token-sync/acct-id")   ← 가명 HMAC 키
      └ K_enc = HKDF(K, "pytmux-token-sync/record")    ← 레코드 AEAD 키

**의존성**: 가명·키 계층은 표준 라이브러리(hmac/hashlib)만 쓴다. 봉인/개봉만
`cryptography`(ChaCha20-Poly1305)를 쓰며 **소프트 의존**이다 — 없으면 `available()`
가 False 이고 seal/open 이 `SyncCryptoUnavailable` 을 올린다(동기화를 암호화 켠 채로
시작할 수 없다는 뜻이지, pytmux 나 이 플러그인이 죽는다는 뜻이 아니다). 이 파일은
**서버 이벤트 루프에서 직접 부르지 말 것** — 배치 봉인은 executor 오프로드다
(블로킹-온-루프는 이 프로젝트에서 4회 물린 항목).

이 모듈은 순수 함수 + 파일 하나(키/host_id) 읽기만 담당한다. 네트워크·DB 는 모른다.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import unicodedata
import uuid

# 봉인 없이도 되는 부분(가명·커서)은 이 상수 아래에서 전부 동작한다.
_INFO_ID = b"pytmux-token-sync/acct-id"
_INFO_ENC = b"pytmux-token-sync/record"
_SALT = b"pytmux-token-sync/v1"

ACCT_ID_LEN = 16        # 바이트. hex 32자 — 충돌은 계정 수 규모에서 무시 가능
RKEY_LEN = 16
MASTER_LEN = 32
NONCE_LEN = 12          # ChaCha20-Poly1305 표준 nonce


class SyncCryptoError(Exception):
    """복호 실패·형식 오류 등 — 호출자는 **그 레코드를 버리고 사유를 남긴다**."""


class SyncCryptoUnavailable(SyncCryptoError):
    """`cryptography` 미설치. 암호화를 켠 동기화는 시작할 수 없다."""


# ── 계정 정규화·가명 ────────────────────────────────────────────────────────

def normalize_account(account) -> str:
    """동기화 전용 계정 정규화 — **트림 + NFC + 소문자화, 그 이상 금지**.

    머신마다 같은 이메일에서 **같은 가명**이 나와야 계정이 쪼개지지 않는다. 그래서
    `usagelog.remap_account`(사용자 설정인 신뢰 계정/도메인에 따라 결과가 달라진다)
    를 여기에 끼우면 안 된다 — 그 함수는 표시·집계용이고, 이 함수는 **동기화 계약**
    이다. 빈 값/미상(None, "", "unknown")은 빈 문자열을 돌려주고 호출자가 가명 없이
    (NULL) 보낸다 — unknown 을 임의 계정에 접붙이는 것은 금지(설계 §3.4)."""
    if not account:
        return ""
    s = unicodedata.normalize("NFC", str(account)).strip().lower()
    if not s or s == "unknown":
        return ""
    return s


def acct_id(k_id: bytes, account) -> str | None:
    """계정 가명(hex). 미상 계정이면 None — 서버엔 NULL 로 나간다."""
    norm = normalize_account(account)
    if not norm:
        return None
    return hmac.new(k_id, b"acct|" + norm.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:ACCT_ID_LEN * 2]


def rkey(k_id: bytes, kind: str, key: str) -> str:
    """레코드 멱등 키(hex). 원문 `xkey`(Anthropic message.id)를 그대로 보내지 않는
    이유는 서버 데이터가 Anthropic 측 레코드와 상관되지 않게 하기 위함이다. 병합에
    필요한 건 **동등성**뿐이라 해시로 충분하다. kind 를 섞어 xc/limits 키 공간을
    분리한다."""
    msg = ("%s|%s" % (kind, key)).encode("utf-8")
    return hmac.new(k_id, b"rkey|" + msg, hashlib.sha256).hexdigest()[:RKEY_LEN * 2]


# ── 키 계층 ────────────────────────────────────────────────────────────────

def hkdf(key: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC5869 HKDF-SHA256(extract+expand). 표준 라이브러리만 쓴다."""
    prk = hmac.new(_SALT, key, hashlib.sha256).digest()
    out, t, i = b"", b"", 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def derive_keys(master: bytes) -> tuple[bytes, bytes]:
    """마스터 → (K_id, K_enc). 두 용도를 **분리**해야 한 쪽이 새어도 다른 쪽이
    남는다(같은 키를 HMAC 과 AEAD 에 겸용하는 것은 금물)."""
    if not isinstance(master, (bytes, bytearray)) or len(master) < MASTER_LEN:
        raise SyncCryptoError("마스터 키 길이가 짧습니다(%dB 필요)" % MASTER_LEN)
    return hkdf(bytes(master), _INFO_ID), hkdf(bytes(master), _INFO_ENC)


def gen_master() -> bytes:
    return os.urandom(MASTER_LEN)


def load_or_create_master(path: str) -> bytes:
    """마스터 키 파일(0600)을 읽고 없으면 만든다. 첫 머신이 만들고, 나머지 머신은
    초대 코드(§5.3)로 받은 값을 `save_master` 로 심는다 — 키는 **서버를 통과하지
    않는다**."""
    try:
        with open(path, "rb") as f:
            raw = f.read().strip()
    except FileNotFoundError:
        master = gen_master()       # **없을 때만** 새로 만든다
        save_master(path, master)
        return master
    except OSError as e:
        raise SyncCryptoError("키 파일을 읽지 못했습니다: %s" % e) from e
    # C-1(검수): 예전에는 파일이 깨졌어도 조용히 새 키를 만들었다 — 그 순간부터 이
    # 머신은 다른 vault 키로 올리고 서버의 기존 레코드는 복호 불능이 된다(사용자는
    # "왜 안 합쳐지지?" 를 오래 헤맨다). 파일이 **있는데** 못 읽으면 하드 실패다.
    try:
        master = base64.b64decode(raw, validate=True)
    except (ValueError, binascii.Error) as e:
        raise SyncCryptoError(
            "키 파일이 손상됐습니다(%s) — 다른 머신에서 :claude-token-sync invite 로 받은 "
            "코드를 :claude-token-sync adopt 로 넣거나, 파일을 지워 새 키를 만드세요" % path
        ) from e
    if len(master) != MASTER_LEN:
        raise SyncCryptoError("키 파일 길이가 올바르지 않습니다: %s" % path)
    return master


def save_master(path: str, master: bytes) -> None:
    """마스터 키를 0600 으로 저장한다. 디렉터리는 0700(usagedb.connect 와 동일 관례).
    **로그·스크린샷·에러 리포트에 이 값을 절대 싣지 말 것**."""
    if len(master) != MASTER_LEN:
        raise SyncCryptoError("마스터 키 길이 오류")
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, base64.b64encode(master))
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def ensure_host_id(db_dir: str) -> str:
    """이 머신의 안정 식별자(`<db_dir>/host_id`, 0600). hostname 은 바뀌고 중복돼
    조인 키로 못 쓴다(설계 §3.3) — 표시용 라벨로만 따로 싣는다."""
    path = os.path.join(db_dir, "host_id")
    try:
        with open(path, "r", encoding="utf-8") as f:
            hid = f.read().strip()
        if hid:
            return hid
    except OSError:
        pass
    hid = uuid.uuid4().hex
    os.makedirs(db_dir, exist_ok=True)
    try:
        os.chmod(db_dir, 0o700)
    except OSError:
        pass
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, hid.encode("ascii"))
    finally:
        os.close(fd)
    return hid


# ── 초대 코드(사람이 옮기는 마스터 키) ──────────────────────────────────────

_B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def format_invite(master: bytes) -> str:
    """마스터 키 → 사람이 옮길 수 있는 문자열(base32 + CRC, 4자씩 하이픈).

    사용자가 눈으로 옮겨 적거나 붙여넣는 값이라 **오타를 조용히 삼키지 않게** 체크섬
    2바이트를 붙인다. 이 값 = 키 자체이므로 채팅·스크린샷에 남기지 말 것."""
    if len(master) != MASTER_LEN:
        raise SyncCryptoError("마스터 키 길이 오류")
    crc = binascii.crc32(master) & 0xFFFF
    body = base64.b32encode(master + bytes([crc >> 8, crc & 0xFF])).decode("ascii")
    body = body.rstrip("=")
    return "-".join(body[i:i + 4] for i in range(0, len(body), 4))


def parse_invite(code: str) -> bytes:
    """초대 코드 → 마스터 키. 형식·체크섬이 틀리면 SyncCryptoError(조용한 실패 금지)."""
    s = "".join(ch for ch in str(code).upper() if ch in _B32)
    pad = (-len(s)) % 8
    try:
        raw = base64.b32decode(s + "=" * pad)
    except (ValueError, binascii.Error) as e:
        raise SyncCryptoError("초대 코드 형식이 올바르지 않습니다") from e
    if len(raw) != MASTER_LEN + 2:
        raise SyncCryptoError("초대 코드 길이가 올바르지 않습니다")
    master, crc = raw[:MASTER_LEN], raw[MASTER_LEN:]
    if (binascii.crc32(master) & 0xFFFF) != (crc[0] << 8 | crc[1]):
        raise SyncCryptoError("초대 코드 검사합이 맞지 않습니다(오타 가능)")
    return master


# ── 레코드 봉인 ────────────────────────────────────────────────────────────

def _aead():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
    except ImportError as e:      # 소프트 의존 — 여기서만 걸린다
        raise SyncCryptoUnavailable(
            "동기화 암호화에는 cryptography 패키지가 필요합니다") from e
    return ChaCha20Poly1305


def available() -> bool:
    """봉인/개봉이 가능한 환경인가(= cryptography 설치됨)."""
    try:
        _aead()
        return True
    except SyncCryptoUnavailable:
        return False


def aad(vault_id: str, kind: str, rec_key: str, acct) -> bytes:
    """봉투를 다른 행에 갖다 붙이는 **재조합**을 막는 결합 데이터. 서버가 행을
    뒤섞어 돌려주면 복호가 실패한다(= 조작 탐지)."""
    return ("%s|%s|%s|%s" % (vault_id, kind, rec_key, acct or "")).encode("utf-8")


def seal(k_enc: bytes, ad: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    """(nonce, ciphertext) 반환. nonce 는 레코드마다 새로 뽑는다(96비트 랜덤 —
    한 키로 10^6 규모 레코드에서 재사용 확률은 무시 가능)."""
    ChaCha20Poly1305 = _aead()
    nonce = os.urandom(NONCE_LEN)
    return nonce, ChaCha20Poly1305(k_enc).encrypt(nonce, plaintext, ad)


def unseal(k_enc: bytes, ad: bytes, nonce: bytes, ct: bytes) -> bytes:
    """복호. 위조·변조·재조합은 전부 여기서 SyncCryptoError 로 떨어진다 — 이것이
    신뢰불가 입력에 대한 **1차 방어**다(설계 §8.4)."""
    ChaCha20Poly1305 = _aead()
    try:
        return ChaCha20Poly1305(k_enc).decrypt(bytes(nonce), bytes(ct), ad)
    except Exception as e:        # InvalidTag 포함 — 사유는 카운터로만 남긴다
        raise SyncCryptoError("레코드 복호 실패(위조·변조 또는 키 불일치)") from e
