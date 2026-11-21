"""Agent credentials handling for the oloop server backend.

Follows AGENTS.md and SERVER.md:
- Represents the identity an AI agent / CLI holds: server, agentId, token,
  keypair (X25519), and owner (id, public key).
- Manages reading and atomic writing with file mode 0600.
- Resolves location from --credentials option or OLOOP_AGENT_CREDENTIALS environment variable.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .crypto import (
    b64url_decode,
    b64url_encode,
    public_key_from_secret_key,
)

ENV_CREDENTIALS = "OLOOP_AGENT_CREDENTIALS"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


class CredentialsError(Exception):
    """Raised when credentials cannot be loaded, validated, or saved."""


def validate_server_url(url: str) -> str:
    """Validate and normalize a server URL.

    Follows SERVER.md:
    - Accepts only https:// server addresses.
    - http:// is accepted for loopback addresses (localhost, 127.0.0.1, ::1) only.
    """
    if not url:
        raise CredentialsError("Server URL cannot be empty")

    parsed = urllib.parse.urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise CredentialsError(f"Invalid server URL: {url!r}")

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()

    if scheme == "http":
        if hostname not in LOOPBACK_HOSTS:
            raise CredentialsError(
                f"Insecure server URL {url!r}: http:// is only allowed for loopback addresses (localhost, 127.0.0.1, ::1)"
            )
    elif scheme == "https":
        pass
    else:
        raise CredentialsError(f"Unsupported URL scheme {parsed.scheme!r} (must be https:// or http:// for loopback)")

    return url.rstrip("/")


@dataclass(frozen=True)
class AgentOwner:
    """The owner (user) that created this agent."""
    id: str
    public_key: bytes

    def __post_init__(self) -> None:
        try:
            uuid.UUID(str(self.id))
        except ValueError as e:
            raise CredentialsError(f"Invalid owner id UUID {self.id!r}") from e

        if len(self.public_key) != 32:
            raise CredentialsError(f"Owner public key must be 32 bytes, got {len(self.public_key)}")

    @property
    def public_key_b64(self) -> str:
        return b64url_encode(self.public_key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "publicKey": self.public_key_b64,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentOwner:
        if not isinstance(data, dict):
            raise CredentialsError("Owner field must be a JSON object")

        if "id" not in data or "publicKey" not in data:
            raise CredentialsError("Owner must contain 'id' and 'publicKey'")

        try:
            pk_bytes = b64url_decode(data["publicKey"])
        except Exception as e:
            raise CredentialsError(f"Invalid base64url owner publicKey: {e}") from e

        return cls(id=str(data["id"]), public_key=pk_bytes)


@dataclass(frozen=True)
class AgentCredentials:
    """Agent credentials file content (AGENTS.md).

    Holds:
    - server: base URL of the oloop server
    - agent_id: UUID of the agent
    - token: bearer token used for authenticating API calls
    - public_key: agent's current X25519 public key (32 bytes)
    - secret_key: agent's current X25519 secret key (32 bytes)
    - owner: user who owns this agent
    - expires_at: optional ISO-8601 timestamp when the current token expires
    - key_number: rotation sequence number of the key pair (starts at 1)
    """
    server: str
    agent_id: str
    token: str
    public_key: bytes
    secret_key: bytes
    owner: AgentOwner
    expires_at: str | None = None
    key_number: int = 1
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Validate server URL
        validate_server_url(self.server)

        # Validate agent UUID
        try:
            uuid.UUID(str(self.agent_id))
        except ValueError as e:
            raise CredentialsError(f"Invalid agentId UUID {self.agent_id!r}") from e

        # Validate token
        if not self.token or not isinstance(self.token, str):
            raise CredentialsError("Agent token must be a non-empty string")

        # Validate keys
        if len(self.public_key) != 32:
            raise CredentialsError(f"Agent public key must be 32 bytes, got {len(self.public_key)}")
        if len(self.secret_key) != 32:
            raise CredentialsError(f"Agent secret key must be 32 bytes, got {len(self.secret_key)}")

        # Check keypair consistency
        derived_pk = public_key_from_secret_key(self.secret_key)
        if derived_pk != self.public_key:
            raise CredentialsError("Agent public key does not correspond to the secret key")

    @property
    def public_key_b64(self) -> str:
        return b64url_encode(self.public_key)

    @property
    def secret_key_b64(self) -> str:
        return b64url_encode(self.secret_key)

    def is_expiring_soon(self, within_days: float = 14.0) -> bool:
        """Check if the agent token expires within within_days (AGENTS.md)."""
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return exp - datetime.now(timezone.utc) <= timedelta(days=within_days)
        except Exception:
            return False

    def with_token(self, new_token: str, expires_at: str | None = None) -> AgentCredentials:
        """Return a copy with an updated bearer token and optional expiration."""
        return AgentCredentials(
            server=self.server,
            agent_id=self.agent_id,
            token=new_token,
            public_key=self.public_key,
            secret_key=self.secret_key,
            owner=self.owner,
            expires_at=expires_at if expires_at is not None else self.expires_at,
            key_number=self.key_number,
            extra=self.extra,
        )

    def with_keypair(
        self,
        new_public_key: bytes,
        new_secret_key: bytes,
        new_token: str,
        expires_at: str | None = None,
        key_number: int | None = None,
    ) -> AgentCredentials:
        """Return a copy with rotated keypair, updated bearer token, and incremented key_number."""
        return AgentCredentials(
            server=self.server,
            agent_id=self.agent_id,
            token=new_token,
            public_key=new_public_key,
            secret_key=new_secret_key,
            owner=self.owner,
            expires_at=expires_at if expires_at is not None else self.expires_at,
            key_number=key_number if key_number is not None else (self.key_number + 1),
            extra=self.extra,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert credentials to JSON dict matching AGENTS.md."""
        d: dict[str, Any] = {
            "server": self.server,
            "agentId": str(self.agent_id),
            "token": self.token,
            "publicKey": self.public_key_b64,
            "secretKey": self.secret_key_b64,
            "owner": self.owner.to_dict(),
        }
        if self.expires_at:
            d["expiresAt"] = self.expires_at
        if self.key_number > 1:
            d["keyNumber"] = self.key_number
        return {**d, **self.extra}

    def to_json(self) -> str:
        """Serialize to pretty JSON string."""
        return json.dumps(self.to_dict(), indent=2) + "\n"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentCredentials:
        """Parse credentials from a dictionary."""
        if not isinstance(data, dict):
            raise CredentialsError("Credentials must be a JSON object")

        required = {"server", "agentId", "token", "publicKey", "secretKey", "owner"}
        missing = required - set(data.keys())
        if missing:
            raise CredentialsError(f"Missing required credentials fields: {', '.join(sorted(missing))}")

        try:
            pk_bytes = b64url_decode(data["publicKey"])
            sk_bytes = b64url_decode(data["secretKey"])
        except Exception as e:
            raise CredentialsError(f"Invalid base64url keys: {e}") from e

        owner = AgentOwner.from_dict(data["owner"])
        expires_at = data.get("expiresAt")
        key_number = data.get("keyNumber", 1)

        known = {"server", "agentId", "token", "publicKey", "secretKey", "owner", "expiresAt", "keyNumber"}
        extra = {k: v for k, v in data.items() if k not in known}

        return cls(
            server=str(data["server"]),
            agent_id=str(data["agentId"]),
            token=str(data["token"]),
            public_key=pk_bytes,
            secret_key=sk_bytes,
            owner=owner,
            expires_at=expires_at,
            key_number=int(key_number),
            extra=extra,
        )

    @classmethod
    def from_json(cls, text: str) -> AgentCredentials:
        """Parse credentials from a JSON string."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise CredentialsError(f"Failed to parse credentials JSON: {e}") from e
        return cls.from_dict(data)

    def save(self, path: str | Path) -> None:
        """Save credentials atomically to path with mode 0600 (owner read/write only)."""
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

        content = self.to_json().encode("utf-8")

        # Create temporary file in the same directory for atomic replace
        prefix = f".{target.name}.tmp."
        fd, tmp_path_str = tempfile.mkstemp(dir=target.parent, prefix=prefix)
        tmp_path = Path(tmp_path_str)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            os.replace(tmp_path, target)

            # Ensure final permissions are 0600 on POSIX
            if os.name == "posix":
                os.chmod(target, 0o600)
        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise CredentialsError(f"Failed to write credentials to {target}: {e}") from e

    @classmethod
    def load(cls, path: str | Path, check_permissions: bool = True) -> AgentCredentials:
        """Load credentials from path, validating file permissions on POSIX (must be 0600)."""
        target = Path(path).resolve()
        if not target.is_file():
            raise CredentialsError(f"Credentials file does not exist: {target}")

        if check_permissions and os.name == "posix":
            mode = stat.S_IMODE(target.stat().st_mode)
            # Must not be accessible by group or others (0077)
            if mode & 0o077 != 0:
                raise CredentialsError(
                    f"Insecure permissions on {target}: {oct(mode)} (must be 0600, owner-accessible only)"
                )

        try:
            text = target.read_text(encoding="utf-8")
        except OSError as e:
            raise CredentialsError(f"Failed to read credentials file {target}: {e}") from e

        return cls.from_json(text)

    @classmethod
    def resolve_path(cls, explicit_path: str | Path | None = None) -> Path | None:
        """Resolve path to credentials file from explicit option or OLOOP_AGENT_CREDENTIALS."""
        if explicit_path:
            return Path(explicit_path).resolve()
        env_val = os.environ.get(ENV_CREDENTIALS)
        if env_val:
            return Path(env_val).resolve()
        return None

    @classmethod
    def load_from_env_or_path(
        cls,
        explicit_path: str | Path | None = None,
        check_permissions: bool = True,
    ) -> AgentCredentials | None:
        """Load credentials if an explicit path or OLOOP_AGENT_CREDENTIALS is set."""
        path = cls.resolve_path(explicit_path)
        if path is None:
            return None
        return cls.load(path, check_permissions=check_permissions)
