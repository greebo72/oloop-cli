"""Cryptographic primitives for the oloop server backend.

Follows SERVER.md, AGENTS.md, and the conformance test vectors in
spec/fixtures/server/vectors.json.

Uses PyNaCl (libsodium):
- Passphrase KDF: Argon2id (crypto_pwhash)
- Envelopes: XChaCha20-Poly1305 (crypto_aead_xchacha20poly1305_ietf)
- Key pairs & grants: X25519 (crypto_box)
- Recovery codes: RFC 4648 Base32 (no padding, dash-separated groups of 4)
"""
from __future__ import annotations

import base64
import struct
import uuid
from typing import Any

import nacl.bindings as nb
import nacl.public
import nacl.utils
from nacl import pwhash
from nacl.exceptions import CryptoError

MIB = 1024 * 1024
MIN_M = 19 * MIB
MIN_T = 2
DEFAULT_M = 64 * MIB
DEFAULT_T = 3

BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


class CryptoFailure(Exception):
    """Raised when cryptographic verification or decryption fails."""


# ---------------- Passphrase & KDF ----------------

def encode_passphrase(text: str) -> bytes:
    """Encode a typed passphrase to UTF-8 without normalization.

    Refuses invalid Unicode (such as lone surrogate code points).
    """
    for ch in text:
        cp = ord(ch)
        if 0xD800 <= cp <= 0xDFFF:
            raise ValueError(f"Invalid Unicode: lone surrogate code point U+{cp:04X}")
    return text.encode("utf-8")


def derive_passphrase_key(
    passphrase: str | bytes,
    salt: bytes,
    m: int = DEFAULT_M,
    t: int = DEFAULT_T,
) -> bytes:
    """Derive a 32-byte key from a passphrase using Argon2id.

    Enforces minimum cost policy: m >= 19 MiB, t >= 2.
    """
    if m < MIN_M or t < MIN_T:
        raise ValueError(
            f"KDF parameters below minimum policy (got m={m}, t={t}; minimum is m={MIN_M}, t={MIN_T})"
        )
    if isinstance(passphrase, str):
        pw_bytes = encode_passphrase(passphrase)
    else:
        pw_bytes = passphrase

    return pwhash.argon2id.kdf(32, pw_bytes, salt, opslimit=t, memlimit=m)


# ---------------- Recovery Code ----------------

def format_recovery_code(code: bytes) -> str:
    """Format 32 random bytes as an RFC 4648 Base32 recovery code.

    Outputs 52 characters grouped into 13 groups of 4 separated by dashes.
    """
    if len(code) != 32:
        raise ValueError(f"Recovery code must be exactly 32 bytes, got {len(code)}")
    letters = base64.b32encode(code).decode("ascii").rstrip("=")
    return "-".join(letters[i:i + 4] for i in range(0, len(letters), 4))


def parse_recovery_code(text: str) -> bytes:
    """Parse and validate a recovery code string.

    Ignores whitespace and dashes, case-insensitive.
    Requires exactly 52 Base32 characters.
    Validates that the 4 unused bits of the last character are zero.
    """
    cleaned = "".join(c for c in text.upper() if c not in "- \t\r\n")
    if len(cleaned) != 52:
        raise ValueError(f"Recovery code must have exactly 52 letters, got {len(cleaned)}")

    for c in cleaned:
        if c not in BASE32_ALPHABET:
            raise ValueError(f"Character {c!r} is not in the Base32 alphabet")

    last_char = cleaned[-1]
    val = BASE32_ALPHABET.index(last_char)
    if val & 0x0F != 0:
        raise ValueError("the unused bits of the last letter are not zero")

    decoded = base64.b32decode(cleaned + "====")
    if len(decoded) != 32:
        raise ValueError(f"Decoded recovery code must be 32 bytes, got {len(decoded)}")
    return decoded


# ---------------- Associated Data (AAD) ----------------

def aad(v: int, alg: int, *fields: str) -> bytes:
    """v and alg as bytes, then each field preceded by one 0x00 byte."""
    return bytes([v, alg]) + b"".join(b"\x00" + f.encode("utf-8") for f in fields)


def doc_aad(kind: str, doc_id: str | uuid.UUID, key_version: int, rev: int) -> bytes:
    """AAD for a document (kind, id, keyVersion, rev)."""
    return aad(1, 1, "doc", kind, str(doc_id), str(key_version), str(rev))


def blob_aad(blob_id: str | uuid.UUID, task_id: str | uuid.UUID, key_version: int) -> bytes:
    """AAD for a blob (blobId, taskId, keyVersion)."""
    return aad(1, 1, "blob", str(blob_id), str(task_id), str(key_version))


def key_aad(user_id: str | uuid.UUID) -> bytes:
    """AAD for a user's wrapped secret key."""
    return aad(1, 1, "key", str(user_id))


# ---------------- Envelopes ----------------

def seal_envelope(
    key: bytes,
    nonce: bytes,
    associated_data: bytes,
    plaintext: bytes,
    v: int = 1,
    alg: int = 1,
) -> bytes:
    """Seal plaintext into an envelope using XChaCha20-Poly1305.

    Returns: v(1) | alg(1) | nonce(24) | ciphertext + tag(16).
    """
    if len(nonce) != 24:
        raise ValueError(f"Nonce must be 24 bytes, got {len(nonce)}")
    ct = nb.crypto_aead_xchacha20poly1305_ietf_encrypt(plaintext, associated_data, nonce, key)
    return bytes([v, alg]) + nonce + ct


def open_envelope(key: bytes, envelope: bytes, associated_data: bytes) -> bytes:
    """Open an envelope using XChaCha20-Poly1305.

    Checks envelope version (1) and algorithm (1).
    """
    if len(envelope) < 2 + 24 + 16:
        raise ValueError("Envelope is too short to be valid")
    if envelope[0] != 1 or envelope[1] != 1:
        raise ValueError(f"Unknown envelope version ({envelope[0]}) or algorithm ({envelope[1]})")
    nonce = envelope[2:26]
    ct = envelope[26:]
    try:
        return nb.crypto_aead_xchacha20poly1305_ietf_decrypt(ct, associated_data, nonce, key)
    except CryptoError as e:
        raise CryptoFailure("Decryption failed (bad key, corrupted ciphertext, or mismatched AAD)") from e


# ---------------- Project Key Grants ----------------

def wrap_project_key(
    project_key: bytes,
    project_id: uuid.UUID | str,
    key_version: int,
    recipient_pk: bytes,
    sender_sk: bytes,
    nonce: bytes | None = None,
) -> bytes:
    """Wrap a project key for a recipient using authenticated crypto_box.

    Returns: nonce(24) | crypto_box(projectKey(32) | projectId(16) | keyVersion(4))
    """
    if len(project_key) != 32:
        raise ValueError(f"Project key must be 32 bytes, got {len(project_key)}")
    if nonce is None:
        nonce = nacl.utils.random(24)
    elif len(nonce) != 24:
        raise ValueError(f"Nonce must be 24 bytes, got {len(nonce)}")

    pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
    message = project_key + pid.bytes + struct.pack(">I", key_version)
    box = nb.crypto_box(message, nonce, recipient_pk, sender_sk)
    return nonce + box


def unwrap_project_key(
    wrapped: bytes,
    recipient_sk: bytes,
    sender_pk: bytes,
    expected_project_id: uuid.UUID | str,
    expected_key_version: int,
) -> bytes:
    """Open a handed-over project key and verify expected projectId and keyVersion."""
    if len(wrapped) < 24 + 16:
        raise ValueError("Wrapped key is too short")
    nonce, box = wrapped[:24], wrapped[24:]
    try:
        message = nb.crypto_box_open(box, nonce, sender_pk, recipient_sk)
    except CryptoError as e:
        raise CryptoFailure("Failed to open wrapped project key") from e

    if len(message) != 32 + 16 + 4:
        raise ValueError(f"Invalid grant message length: {len(message)}")

    expected_pid = (
        expected_project_id if isinstance(expected_project_id, uuid.UUID) else uuid.UUID(str(expected_project_id))
    )
    if message[32:48] != expected_pid.bytes:
        raise ValueError("projectId in the message is not the expected one")

    kv = struct.unpack(">I", message[48:52])[0]
    if kv != expected_key_version:
        raise ValueError("keyVersion in the message is not the expected one")

    return message[:32]


# ---------------- Authenticated Boxes (Agents) ----------------

def box_seal(message: bytes, nonce: bytes, recipient_pk: bytes, sender_sk: bytes) -> bytes:
    """Authenticated box: nonce(24) | crypto_box(message, nonce, recipient_pk, sender_sk)."""
    if len(nonce) != 24:
        raise ValueError(f"Nonce must be 24 bytes, got {len(nonce)}")
    return nonce + nb.crypto_box(message, nonce, recipient_pk, sender_sk)


def box_open(data: bytes, recipient_sk: bytes, sender_pk: bytes) -> bytes:
    """Open an authenticated box with recipient secret key and sender public key."""
    if len(data) < 24 + 16:
        raise ValueError("Box is too short")
    try:
        return nb.crypto_box_open(data[24:], data[:24], sender_pk, recipient_sk)
    except CryptoError as e:
        raise CryptoFailure("Failed to open authenticated box") from e


def create_agent_registration(
    agent_pk: bytes,
    agent_id: uuid.UUID | str,
    user_pk: bytes,
    user_sk: bytes,
    nonce: bytes | None = None,
) -> bytes:
    """Create agent registration: box(agent_pk(32) | agent_id(16), user_pk, user_sk)."""
    aid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
    if nonce is None:
        nonce = nacl.utils.random(24)
    return box_seal(agent_pk + aid.bytes, nonce, user_pk, user_sk)


def verify_agent_registration(
    registration: bytes,
    user_pk: bytes,
    user_sk: bytes,
) -> tuple[bytes, uuid.UUID]:
    """Verify and unpack agent registration. Returns (agent_pk, agent_id)."""
    msg = box_open(registration, user_sk, user_pk)
    if len(msg) != 32 + 16:
        raise ValueError(f"Invalid registration length: {len(msg)}")
    return msg[:32], uuid.UUID(bytes=msg[32:48])


def create_agent_statement(
    new_agent_pk: bytes,
    agent_id: uuid.UUID | str,
    key_number: int,
    user_pk: bytes,
    old_agent_sk: bytes,
    nonce: bytes | None = None,
) -> bytes:
    """Create key rotation statement: box(new_pk | agent_id | key_number, user_pk, old_sk)."""
    aid = agent_id if isinstance(agent_id, uuid.UUID) else uuid.UUID(str(agent_id))
    if nonce is None:
        nonce = nacl.utils.random(24)
    msg = new_agent_pk + aid.bytes + struct.pack(">I", key_number)
    return box_seal(msg, nonce, user_pk, old_agent_sk)


def verify_agent_statement(
    statement: bytes,
    user_sk: bytes,
    old_agent_pk: bytes,
) -> tuple[bytes, uuid.UUID, int]:
    """Verify and unpack key rotation statement. Returns (new_pk, agent_id, key_number)."""
    msg = box_open(statement, user_sk, old_agent_pk)
    if len(msg) != 32 + 16 + 4:
        raise ValueError(f"Invalid statement length: {len(msg)}")
    return msg[:32], uuid.UUID(bytes=msg[32:48]), struct.unpack(">I", msg[48:52])[0]


# ---------------- Key Pairs & Helpers ----------------

def generate_keypair() -> tuple[bytes, bytes]:
    """Generate a new X25519 keypair. Returns (public_key, secret_key)."""
    sk = nacl.public.PrivateKey.generate()
    return bytes(sk.public_key), bytes(sk)


def public_key_from_secret_key(secret_key: bytes) -> bytes:
    """Derive X25519 public key from a 32-byte secret key."""
    if len(secret_key) != 32:
        raise ValueError("Secret key must be 32 bytes")
    return bytes(nacl.public.PrivateKey(secret_key).public_key)


def b64url_encode(data: bytes) -> str:
    """Encode bytes to Base64URL without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    """Decode Base64URL string (with or without padding)."""
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + padding)


# ---------------- Key Bundle ----------------

def unlock_key_bundle(
    bundle: dict[str, Any],
    user_id: uuid.UUID | str,
    passphrase: str | None = None,
    recovery_code: str | bytes | None = None,
) -> tuple[bytes, bytes]:
    """Unlock a key bundle using either a passphrase or a recovery code.

    Returns (public_key, secret_key).
    Validates bundle version, algorithm, policy, and public key match.
    """
    if bundle.get("v") != 1:
        raise ValueError(f"Unsupported key bundle version: {bundle.get('v')}")
    if bundle.get("kdf") != "argon2id":
        raise ValueError(f"Unsupported KDF algorithm: {bundle.get('kdf')}")

    m = bundle.get("m", 0)
    t = bundle.get("t", 0)
    if m < MIN_M or t < MIN_T:
        raise ValueError(f"Key bundle parameters below minimum policy (m={m}, t={t})")

    expected_pk = b64url_decode(bundle["publicKey"])
    aad_bytes = key_aad(user_id)

    secret_key: bytes | None = None

    if passphrase is not None:
        salt = b64url_decode(bundle["salt"])
        pass_key = derive_passphrase_key(passphrase, salt, m=m, t=t)
        wrapped_sk = b64url_decode(bundle["wrappedSecretKey"])
        secret_key = open_envelope(pass_key, wrapped_sk, aad_bytes)
    elif recovery_code is not None:
        rec_bytes = parse_recovery_code(recovery_code) if isinstance(recovery_code, str) else recovery_code
        recovery_wrapped = b64url_decode(bundle["recoveryWrappedSecretKey"])
        secret_key = open_envelope(rec_bytes, recovery_wrapped, aad_bytes)
    else:
        raise ValueError("Either passphrase or recovery_code must be provided")

    derived_pk = public_key_from_secret_key(secret_key)
    if derived_pk != expected_pk:
        raise CryptoFailure("Derived public key does not match public key in key bundle")

    return derived_pk, secret_key
