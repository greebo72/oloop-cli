"""Conformance tests for oloop.crypto against spec/fixtures/server/vectors.json."""

import hashlib
import json
import uuid
from pathlib import Path

import pytest

from oloop.crypto import (
    CryptoFailure,
    blob_aad,
    box_open,
    box_seal,
    derive_passphrase_key,
    doc_aad,
    encode_passphrase,
    format_recovery_code,
    key_aad,
    open_envelope,
    parse_recovery_code,
    public_key_from_secret_key,
    seal_envelope,
    unlock_key_bundle,
    unwrap_project_key,
    verify_agent_registration,
    verify_agent_statement,
    wrap_project_key,
)

SPEC = Path(__file__).resolve().parents[1] / "spec"
VECTORS_FILE = SPEC / "fixtures" / "server" / "vectors.json"

if not VECTORS_FILE.is_file():
    pytest.fail(f"Missing {VECTORS_FILE}, run git submodule update --init", allow_module_level=True)


@pytest.fixture(scope="module")
def vectors():
    return json.loads(VECTORS_FILE.read_text(encoding="utf-8"))


# ---------------- 1. Passphrase to key ----------------

def test_kdf_cases(vectors):
    for case in vectors["kdf"]["cases"]:
        derived = derive_passphrase_key(
            case["passphrase"],
            bytes.fromhex(case["saltHex"]),
            m=case["m"],
            t=case["t"],
        )
        assert derived.hex() == case["keyHex"], f"Failed for case {case['name']}"


def test_kdf_encoding(vectors):
    encoding = vectors["kdf"]["encoding"]
    # Valid cases: UTF-8 as typed, no normalization
    for case in encoding["cases"]:
        text = "".join(chr(cp) for cp in case["codePoints"])
        assert encode_passphrase(text).hex() == case["utf8Hex"]

    # Refused: lone surrogates
    for case in encoding["refused"]:
        text = "".join(chr(cp) for cp in case["codePoints"])
        with pytest.raises(ValueError):
            encode_passphrase(text)

    # Key differentiation: NFC vs NFD
    key_cases = encoding["keys"]
    salt = bytes.fromhex(key_cases["saltHex"])
    m, t = key_cases["m"], key_cases["t"]
    keys = []
    for case in key_cases["cases"]:
        text = "".join(chr(cp) for cp in case["codePoints"])
        key = derive_passphrase_key(text, salt, m=m, t=t)
        assert key.hex() == case["keyHex"]
        keys.append(key)
    assert keys[0] != keys[1], "NFC and NFD spellings must yield different keys"


def test_kdf_policy(vectors):
    salt = bytes(16)
    for case in vectors["kdf"]["policy"]["cases"]:
        if case["accept"]:
            derive_passphrase_key("dummy", salt, m=case["m"], t=case["t"])
        else:
            with pytest.raises(ValueError, match="KDF parameters below minimum policy"):
                derive_passphrase_key("dummy", salt, m=case["m"], t=case["t"])


# ---------------- 2. Key pairs and key bundle ----------------

def test_key_bundle_unlock(vectors):
    keys = vectors["keys"]
    user_id = keys["userId"]
    expected_pk = bytes.fromhex(keys["publicKeyHex"])
    expected_sk = bytes.fromhex(keys["secretKeyHex"])

    # 1. Unlock via passphrase
    pk, sk = unlock_key_bundle(keys["bundle"], user_id, passphrase="correct horse battery staple")
    assert pk == expected_pk
    assert sk == expected_sk

    # 2. Unlock via recovery code
    pk2, sk2 = unlock_key_bundle(keys["bundle"], user_id, recovery_code=keys["recoveryCodeText"])
    assert pk2 == expected_pk
    assert sk2 == expected_sk

    # Check key AAD
    assert key_aad(user_id).hex() == keys["aadHex"]


# ---------------- 3. Recovery code ----------------

def test_recovery_code(vectors):
    rc = vectors["recoveryCode"]
    expected_code = bytes.fromhex(vectors["keys"]["recoveryCodeHex"])

    # Formatting
    assert format_recovery_code(expected_code) == vectors["keys"]["recoveryCodeText"]

    # Accept cases
    for case in rc["accept"]:
        assert parse_recovery_code(case["text"]).hex() == case["bytesHex"]

    # Refuse cases
    for case in rc["refuse"]:
        with pytest.raises(ValueError):
            parse_recovery_code(case["text"])


# ---------------- 4. Grants ----------------

def test_grants(vectors):
    for case in vectors["grants"]["cases"]:
        sender_pk = bytes.fromhex(case["senderPublicKeyHex"])
        recipient_pk = bytes.fromhex(case["recipientPublicKeyHex"])
        project_key = bytes.fromhex(case["projectKeyHex"])
        project_id = uuid.UUID(case["projectId"])
        kv = case["keyVersion"]

        # If owner to itself: sender = user, recipient = user
        # In vectors:
        # case 0: owner to itself (user_sk)
        # case 1: owner to agent (agent_sk)
        # case 2: owner to agent kv2 (agent_sk)
        user_sk = bytes.fromhex(vectors["keys"]["secretKeyHex"])
        agent_sk = bytes.fromhex(vectors["agents"]["key1"]["secretKeyHex"])
        recipient_sk = user_sk if "itself" in case["name"] else agent_sk

        # Unwrapping
        unwrapped = unwrap_project_key(
            bytes.fromhex(case["wrappedHex"]),
            recipient_sk=recipient_sk,
            sender_pk=sender_pk,
            expected_project_id=project_id,
            expected_key_version=kv,
        )
        assert unwrapped == project_key


# ---------------- 5. Documents and Blobs ----------------

def test_documents(vectors):
    for case in vectors["documents"]["cases"]:
        key = bytes.fromhex(case["keyHex"])
        nonce = bytes.fromhex(case["nonceHex"])
        aad = bytes.fromhex(case["aadHex"])
        plaintext = case["plaintextUtf8"].encode("utf-8")
        envelope = bytes.fromhex(case["envelopeHex"])

        # Check AAD computation
        assert doc_aad(case["kind"], case["id"], case["keyVersion"], case["rev"]) == aad

        # Seal
        sealed = seal_envelope(key, nonce, aad, plaintext)
        assert sealed == envelope

        # Open
        opened = open_envelope(key, envelope, aad)
        assert opened == plaintext


def test_blob(vectors):
    blob = vectors["blob"]
    key = bytes.fromhex(blob["keyHex"])
    nonce = bytes.fromhex(blob["nonceHex"])
    aad = bytes.fromhex(blob["aadHex"])
    plaintext = bytes.fromhex(blob["plaintextHex"])
    envelope = bytes.fromhex(blob["envelopeHex"])

    # Check AAD
    assert blob_aad(blob["blobId"], blob["taskId"], blob["keyVersion"]) == aad

    # Seal & Open
    sealed = seal_envelope(key, nonce, aad, plaintext)
    assert sealed == envelope
    assert open_envelope(key, envelope, aad) == plaintext

    # SHA256 of envelope
    assert hashlib.sha256(envelope).hexdigest() == blob["sha256Hex"]


# ---------------- 6. Agents ----------------

def test_agents(vectors):
    agents = vectors["agents"]
    user_pk = bytes.fromhex(agents["userPublicKeyHex"])
    user_sk = bytes.fromhex(vectors["keys"]["secretKeyHex"])
    agent_id = uuid.UUID(agents["agentId"])

    key1_pk = bytes.fromhex(agents["key1"]["publicKeyHex"])
    key2_pk = bytes.fromhex(agents["key2"]["publicKeyHex"])
    key2_sk = bytes.fromhex(agents["key2"]["secretKeyHex"])

    # Registration
    reg_bytes = bytes.fromhex(agents["registration"]["hex"])
    pk, aid = verify_agent_registration(reg_bytes, user_pk, user_sk)
    assert pk == key1_pk
    assert aid == agent_id

    # Statement (key rotation)
    stmt_bytes = bytes.fromhex(agents["statement"]["hex"])
    new_pk, aid_stmt, key_num = verify_agent_statement(stmt_bytes, user_sk, key1_pk)
    assert new_pk == key2_pk
    assert aid_stmt == agent_id
    assert key_num == agents["statement"]["keyNumber"]

    # Self-grant
    self_grant = agents["selfGrant"]
    sg_wrapped = bytes.fromhex(self_grant["wrappedHex"])
    unwrapped = unwrap_project_key(
        sg_wrapped,
        recipient_sk=key2_sk,
        sender_pk=key2_pk,
        expected_project_id=self_grant["projectId"],
        expected_key_version=self_grant["keyVersion"],
    )
    assert unwrapped == bytes.fromhex(self_grant["projectKeyHex"])


# ---------------- 7. Must Fail ----------------

def test_must_fail(vectors):
    for case in vectors["mustFail"]:
        kind = case["kind"]
        failed = False
        try:
            if kind in ("envelope", "envelope-header"):
                key = bytes.fromhex(case["keyHex"])
                env = bytes.fromhex(case["envelopeHex"])
                aad = bytes.fromhex(case["aadHex"]) if "aadHex" in case else b""
                open_envelope(key, env, aad)
            elif kind == "grant":
                unwrap_project_key(
                    bytes.fromhex(case["wrappedHex"]),
                    recipient_sk=bytes.fromhex(case["recipientSecretKeyHex"]),
                    sender_pk=bytes.fromhex(case["pinnedSenderPublicKeyHex"]),
                    expected_project_id=uuid.UUID(case["projectId"]),
                    expected_key_version=case["keyVersion"],
                )
            elif kind == "statement":
                box_open(
                    bytes.fromhex(case["statementHex"]),
                    recipient_sk=bytes.fromhex(case["userSecretKeyHex"]),
                    sender_pk=bytes.fromhex(case["previousAgentPublicKeyHex"]),
                )
        except (CryptoFailure, ValueError, Exception):
            failed = True

        assert failed, f"Case {case['name']} should have failed but passed"
