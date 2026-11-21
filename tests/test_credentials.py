"""Unit tests for oloop.credentials."""

import os
import stat
import uuid
from pathlib import Path

import pytest

from oloop.credentials import (
    ENV_CREDENTIALS,
    AgentCredentials,
    AgentOwner,
    CredentialsError,
    validate_server_url,
)
from oloop.crypto import (
    b64url_encode,
    generate_keypair,
)


@pytest.fixture
def sample_credentials():
    agent_pk, agent_sk = generate_keypair()
    owner_pk, _ = generate_keypair()
    owner = AgentOwner(id=str(uuid.uuid4()), public_key=owner_pk)
    return AgentCredentials(
        server="https://oloop.example.com",
        agent_id=str(uuid.uuid4()),
        token="agent_tok_1234567890abcdef",
        public_key=agent_pk,
        secret_key=agent_sk,
        owner=owner,
    )


def test_roundtrip_dict_and_json(sample_credentials):
    data = sample_credentials.to_dict()
    assert data["server"] == "https://oloop.example.com"
    assert data["agentId"] == sample_credentials.agent_id
    assert data["token"] == sample_credentials.token
    assert data["publicKey"] == sample_credentials.public_key_b64
    assert data["secretKey"] == sample_credentials.secret_key_b64
    assert data["owner"]["id"] == sample_credentials.owner.id
    assert data["owner"]["publicKey"] == sample_credentials.owner.public_key_b64

    # Deserialization from dict
    restored = AgentCredentials.from_dict(data)
    assert restored == sample_credentials

    # Deserialization from json
    json_str = sample_credentials.to_json()
    from_json = AgentCredentials.from_json(json_str)
    assert from_json == sample_credentials


def test_save_and_load_atomic(tmp_path, sample_credentials):
    cred_file = tmp_path / "subdir" / "agent.json"
    sample_credentials.save(cred_file)

    assert cred_file.is_file()

    # On POSIX check file permissions (0600)
    if os.name == "posix":
        mode = stat.S_IMODE(cred_file.stat().st_mode)
        assert mode == 0o600

    loaded = AgentCredentials.load(cred_file)
    assert loaded == sample_credentials


def test_insecure_permissions_rejected(tmp_path, sample_credentials):
    if os.name != "posix":
        pytest.skip("File permission checks only applicable to POSIX")

    cred_file = tmp_path / "insecure.json"
    sample_credentials.save(cred_file)

    # Make file world-readable (0644)
    os.chmod(cred_file, 0o644)

    with pytest.raises(CredentialsError, match="Insecure permissions"):
        AgentCredentials.load(cred_file)

    # Can be bypassed with check_permissions=False
    loaded = AgentCredentials.load(cred_file, check_permissions=False)
    assert loaded == sample_credentials


def test_server_url_validation():
    # HTTPS is allowed
    assert validate_server_url("https://oloop.example.com/") == "https://oloop.example.com"
    assert validate_server_url("https://oloop.example.com:8443") == "https://oloop.example.com:8443"

    # HTTP is allowed for loopback only
    assert validate_server_url("http://localhost:8000") == "http://localhost:8000"
    assert validate_server_url("http://127.0.0.1:8000/") == "http://127.0.0.1:8000"
    assert validate_server_url("http://[::1]:8000") == "http://[::1]:8000"

    # HTTP rejected for remote hostnames
    with pytest.raises(CredentialsError, match="http:// is only allowed for loopback"):
        validate_server_url("http://oloop.example.com")

    # Insecure or unsupported schemes
    with pytest.raises(CredentialsError, match="Unsupported URL scheme"):
        validate_server_url("ftp://localhost")

    # Empty
    with pytest.raises(CredentialsError, match="Server URL cannot be empty"):
        validate_server_url("")


def test_validation_errors(sample_credentials):
    # Public key mismatch with secret key
    other_pk, _ = generate_keypair()
    with pytest.raises(CredentialsError, match="Agent public key does not correspond to the secret key"):
        AgentCredentials(
            server=sample_credentials.server,
            agent_id=sample_credentials.agent_id,
            token=sample_credentials.token,
            public_key=other_pk,
            secret_key=sample_credentials.secret_key,
            owner=sample_credentials.owner,
        )

    # Invalid UUID
    with pytest.raises(CredentialsError, match="Invalid agentId UUID"):
        AgentCredentials(
            server=sample_credentials.server,
            agent_id="not-a-uuid",
            token=sample_credentials.token,
            public_key=sample_credentials.public_key,
            secret_key=sample_credentials.secret_key,
            owner=sample_credentials.owner,
        )

    # Missing fields in dict
    bad_dict = sample_credentials.to_dict()
    del bad_dict["token"]
    with pytest.raises(CredentialsError, match="Missing required credentials fields: token"):
        AgentCredentials.from_dict(bad_dict)

    # Invalid JSON
    with pytest.raises(CredentialsError, match="Failed to parse credentials JSON"):
        AgentCredentials.from_json("invalid json{{{")


def test_with_token_and_with_keypair(sample_credentials):
    # with_token
    updated_tok = sample_credentials.with_token("new_token_456")
    assert updated_tok.token == "new_token_456"
    assert updated_tok.public_key == sample_credentials.public_key
    assert updated_tok.secret_key == sample_credentials.secret_key

    # with_keypair
    new_pk, new_sk = generate_keypair()
    rotated = sample_credentials.with_keypair(new_pk, new_sk, "rot_token_789")
    assert rotated.token == "rot_token_789"
    assert rotated.public_key == new_pk
    assert rotated.secret_key == new_sk
    assert rotated.owner == sample_credentials.owner


def test_load_from_env_or_path(tmp_path, sample_credentials, monkeypatch):
    path_explicit = tmp_path / "explicit.json"
    path_env = tmp_path / "env.json"

    sample_credentials.save(path_explicit)
    sample_credentials.save(path_env)

    # 1. Neither provided
    monkeypatch.delenv(ENV_CREDENTIALS, raising=False)
    assert AgentCredentials.load_from_env_or_path() is None

    # 2. From env variable
    monkeypatch.setenv(ENV_CREDENTIALS, str(path_env))
    loaded_env = AgentCredentials.load_from_env_or_path()
    assert loaded_env is not None
    assert loaded_env == sample_credentials

    # 3. Explicit path takes precedence over env
    loaded_explicit = AgentCredentials.load_from_env_or_path(path_explicit)
    assert loaded_explicit is not None
    assert loaded_explicit == sample_credentials


def test_name_roundtrip_once_and_kept_on_renew(sample_credentials):
    from dataclasses import replace

    named = replace(sample_credentials, name="claude")
    data = named.to_dict()
    assert data["name"] == "claude"
    again = AgentCredentials.from_dict(data)
    assert again.name == "claude" and "name" not in again.extra
    assert again.with_token("new_token_value").name == "claude"
    pk, sk = generate_keypair()
    assert again.with_keypair(pk, sk, "new_token_value").name == "claude"
    assert "name" not in sample_credentials.to_dict()


@pytest.mark.parametrize("bad", ["", 5, "x" * 101])
def test_name_validation(sample_credentials, bad):
    data = sample_credentials.to_dict()
    data["name"] = bad
    with pytest.raises(CredentialsError, match="name must be"):
        AgentCredentials.from_dict(data)
