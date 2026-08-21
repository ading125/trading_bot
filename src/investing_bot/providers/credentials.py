"""Encrypted provider-credential vault with an explicit locked state."""

from __future__ import annotations

from base64 import b64decode, b64encode
from collections.abc import Iterable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

from investing_bot.providers.contracts import CredentialReference


_REFERENCE_PATTERN = re.compile(r"^cred_[a-z0-9]+(?:_[a-z0-9]+)*$")
_VAULT_VERSION = 1
_VAULT_FILE = "vault.json"
_CHECK_VALUE = b"investing-bot-credential-vault-v1"
_CHECK_AAD = b"investing_bot.vault_check.v1"


class CredentialVaultError(RuntimeError):
    """Safe base error that never includes credential material."""


class CredentialVaultLockedError(CredentialVaultError):
    pass


class CredentialUnlockError(CredentialVaultError):
    pass


class CredentialNotFoundError(CredentialVaultError):
    pass


class CredentialReferenceStore(Protocol):
    """Expose credential availability without revealing secret values."""

    def is_configured(self, reference: CredentialReference) -> bool: ...

    def has_reference(self, reference: CredentialReference) -> bool: ...

    def get_secret(self, reference: CredentialReference) -> str: ...

    @property
    def unlocked(self) -> bool: ...


class CredentialPresenceStore:
    """Non-secret test double for provider selection and health checks."""

    def __init__(self, references: Iterable[CredentialReference] = ()) -> None:
        self._references = frozenset(references)

    @property
    def unlocked(self) -> bool:
        return True

    def has_reference(self, reference: CredentialReference) -> bool:
        return reference in self._references

    def is_configured(self, reference: CredentialReference) -> bool:
        return self.has_reference(reference)

    def get_secret(self, reference: CredentialReference) -> str:
        raise CredentialNotFoundError(
            "the presence-only credential store cannot resolve secrets"
        )


@dataclass(frozen=True, slots=True)
class CredentialVaultStatus:
    initialized: bool
    unlocked: bool
    references: tuple[str, ...]


class EncryptedCredentialStore:
    """Store each secret in an authenticated, independently rotatable envelope."""

    def __init__(
        self,
        root: Path,
        *,
        memory_cost_kib: int = 64 * 1024,
        iterations: int = 3,
        lanes: int = 4,
    ) -> None:
        if not root.is_absolute():
            raise ValueError("credential vault path must be absolute")
        self.root = root
        self.memory_cost_kib = memory_cost_kib
        self.iterations = iterations
        self.lanes = lanes
        self._key: bytearray | None = None

    @property
    def unlocked(self) -> bool:
        return self._key is not None

    @property
    def initialized(self) -> bool:
        return (self.root / _VAULT_FILE).is_file()

    def status(self) -> CredentialVaultStatus:
        return CredentialVaultStatus(
            initialized=self.initialized,
            unlocked=self.unlocked,
            references=self.list_references(),
        )

    def initialize(self, unlock_secret: str) -> None:
        if self.initialized:
            raise CredentialVaultError("credential vault is already initialized")
        _require_unlock_secret(unlock_secret)
        self._ensure_root()
        salt = os.urandom(16)
        key = _derive_key(
            unlock_secret,
            salt=salt,
            memory_cost_kib=self.memory_cost_kib,
            iterations=self.iterations,
            lanes=self.lanes,
        )
        nonce = os.urandom(12)
        ciphertext = AESGCM(key).encrypt(nonce, _CHECK_VALUE, _CHECK_AAD)
        _atomic_json_write(
            self.root / _VAULT_FILE,
            {
                "version": _VAULT_VERSION,
                "kdf": {
                    "name": "argon2id",
                    "salt": _b64(salt),
                    "memory_cost_kib": self.memory_cost_kib,
                    "iterations": self.iterations,
                    "lanes": self.lanes,
                    "length": 32,
                },
                "check": {
                    "cipher": "aes-256-gcm",
                    "nonce": _b64(nonce),
                    "ciphertext": _b64(ciphertext),
                },
            },
        )
        self._key = bytearray(key)

    def unlock(self, unlock_secret: str) -> None:
        _require_unlock_secret(unlock_secret)
        metadata = self._read_json(self.root / _VAULT_FILE)
        try:
            if metadata["version"] != _VAULT_VERSION:
                raise CredentialUnlockError("credential vault version is unsupported")
            kdf = metadata["kdf"]
            check = metadata["check"]
            if kdf["name"] != "argon2id" or check["cipher"] != "aes-256-gcm":
                raise CredentialUnlockError("credential vault algorithms are unsupported")
            key = _derive_key(
                unlock_secret,
                salt=_unb64(kdf["salt"]),
                memory_cost_kib=int(kdf["memory_cost_kib"]),
                iterations=int(kdf["iterations"]),
                lanes=int(kdf["lanes"]),
            )
            plaintext = AESGCM(key).decrypt(
                _unb64(check["nonce"]),
                _unb64(check["ciphertext"]),
                _CHECK_AAD,
            )
        except (InvalidTag, KeyError, TypeError, ValueError) as exc:
            raise CredentialUnlockError(
                "credential vault could not be unlocked"
            ) from exc
        if plaintext != _CHECK_VALUE:
            raise CredentialUnlockError("credential vault could not be unlocked")
        self.lock()
        self._key = bytearray(key)

    def lock(self) -> None:
        if self._key is not None:
            for index in range(len(self._key)):
                self._key[index] = 0
            self._key = None

    def has_reference(self, reference: CredentialReference) -> bool:
        return self._entry_path(reference).is_file()

    def is_configured(self, reference: CredentialReference) -> bool:
        return self.unlocked and self.has_reference(reference)

    def set_secret(
        self,
        reference: CredentialReference,
        secret: str,
        *,
        credential_kind: str = "api_token",
    ) -> None:
        key = self._require_key()
        if not secret or len(secret) > 20_000:
            raise ValueError("credential secret must contain 1 to 20000 characters")
        if not re.fullmatch(r"[a-z0-9_]{1,80}", credential_kind):
            raise ValueError("credential kind is invalid")
        nonce = os.urandom(12)
        aad = _entry_aad(reference, credential_kind)
        ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), aad)
        self._ensure_root()
        _atomic_json_write(
            self._entry_path(reference),
            {
                "version": _VAULT_VERSION,
                "reference": reference,
                "credential_kind": credential_kind,
                "cipher": "aes-256-gcm",
                "nonce": _b64(nonce),
                "ciphertext": _b64(ciphertext),
            },
        )

    def get_secret(self, reference: CredentialReference) -> str:
        key = self._require_key()
        path = self._entry_path(reference)
        if not path.is_file():
            raise CredentialNotFoundError("credential reference was not found")
        payload = self._read_json(path)
        try:
            if (
                payload["version"] != _VAULT_VERSION
                or payload["reference"] != reference
                or payload["cipher"] != "aes-256-gcm"
            ):
                raise CredentialUnlockError("credential envelope is invalid")
            kind = str(payload["credential_kind"])
            plaintext = AESGCM(key).decrypt(
                _unb64(payload["nonce"]),
                _unb64(payload["ciphertext"]),
                _entry_aad(reference, kind),
            )
            return plaintext.decode("utf-8")
        except (InvalidTag, KeyError, TypeError, UnicodeDecodeError, ValueError) as exc:
            raise CredentialUnlockError(
                "credential envelope could not be decrypted"
            ) from exc

    def list_references(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        references = []
        for path in self.root.glob("cred_*.json"):
            reference = path.stem
            if _REFERENCE_PATTERN.fullmatch(reference) and path.is_file():
                references.append(reference)
        return tuple(sorted(references))

    def _entry_path(self, reference: CredentialReference) -> Path:
        if not _REFERENCE_PATTERN.fullmatch(reference):
            raise ValueError("credential reference is invalid")
        return self.root / f"{reference}.json"

    def _require_key(self) -> bytes:
        if self._key is None:
            raise CredentialVaultLockedError("credential vault is locked")
        return bytes(self._key)

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def _read_json(self, path: Path) -> dict:
        if not path.exists():
            raise CredentialVaultError("credential vault is not initialized")
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode) or stat.S_IMODE(mode) & 0o077:
            raise CredentialVaultError("credential vault file permissions are unsafe")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CredentialVaultError("credential vault file is invalid") from exc
        if not isinstance(payload, dict):
            raise CredentialVaultError("credential vault file is invalid")
        return payload


def _derive_key(
    unlock_secret: str,
    *,
    salt: bytes,
    memory_cost_kib: int,
    iterations: int,
    lanes: int,
) -> bytes:
    if len(salt) != 16:
        raise ValueError("credential vault salt is invalid")
    if not (8 * 1024 <= memory_cost_kib <= 1024 * 1024):
        raise ValueError("credential vault memory cost is invalid")
    if not (1 <= iterations <= 10 and 1 <= lanes <= 16):
        raise ValueError("credential vault work factors are invalid")
    return Argon2id(
        salt=salt,
        length=32,
        iterations=iterations,
        lanes=lanes,
        memory_cost=memory_cost_kib,
        ad=None,
        secret=None,
    ).derive(unlock_secret.encode("utf-8"))


def _require_unlock_secret(unlock_secret: str) -> None:
    if len(unlock_secret) < 12 or len(unlock_secret) > 1024:
        raise ValueError("unlock secret must contain 12 to 1024 characters")


def _entry_aad(reference: str, credential_kind: str) -> bytes:
    return f"investing_bot.credential.v1\0{reference}\0{credential_kind}".encode()


def _b64(value: bytes) -> str:
    return b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    return b64decode(value, validate=True)


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    text = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
        os.chmod(path, 0o600)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
