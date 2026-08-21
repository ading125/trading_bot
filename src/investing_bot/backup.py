"""Authenticated encrypted backup and restore for the complete local data volume."""

from __future__ import annotations

from base64 import b64decode, b64encode
from datetime import UTC, datetime
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import tarfile
import tempfile
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id


MAGIC = b"IBBKP001"
TAG_LENGTH = 16
MAX_HEADER_LENGTH = 8_192


class BackupError(RuntimeError):
    pass


def create_encrypted_backup(
    data_dir: Path,
    destination: Path,
    passphrase: str,
    *,
    now: datetime | None = None,
) -> Path:
    source = data_dir.resolve()
    target = destination.expanduser().resolve()
    _require_passphrase(passphrase)
    if not source.is_dir():
        raise BackupError("data directory does not exist")
    try:
        target.relative_to(source)
    except ValueError:
        pass
    else:
        raise BackupError("backup destination must be outside the data directory")
    if not target.parent.is_dir():
        raise BackupError("backup destination directory does not exist")
    if target.exists():
        raise BackupError("backup destination already exists")

    timestamp = now or datetime.now(UTC)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    header = {
        "version": 1,
        "created_at": timestamp.astimezone(UTC).isoformat(),
        "cipher": "aes-256-gcm",
        "nonce": _b64(nonce),
        "kdf": {
            "name": "argon2id",
            "salt": _b64(salt),
            "memory_cost_kib": 64 * 1024,
            "iterations": 3,
            "lanes": 4,
            "length": 32,
        },
    }
    header_bytes = json.dumps(
        header, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    prefix = MAGIC + struct.pack(">I", len(header_bytes)) + header_bytes
    key = bytearray(_derive_key(passphrase, header["kdf"]))
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(prefix)
            encryptor = Cipher(
                algorithms.AES(bytes(key)), modes.GCM(nonce)
            ).encryptor()
            encryptor.authenticate_additional_data(prefix)
            encrypted_stream = _EncryptingWriter(output, encryptor)
            with tarfile.open(
                fileobj=encrypted_stream,
                mode="w|gz",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                manifest = json.dumps(
                    {
                        "format": "investing-bot-data-volume",
                        "version": 1,
                        "created_at": header["created_at"],
                    },
                    sort_keys=True,
                ).encode("utf-8")
                info = tarfile.TarInfo("backup-manifest.json")
                info.size = len(manifest)
                info.mode = 0o600
                info.mtime = int(timestamp.timestamp())
                archive.addfile(info, io.BytesIO(manifest))
                archive.add(source, arcname="data", recursive=True)
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
        temporary = None
        os.chmod(target, 0o600)
        return target
    except (OSError, tarfile.TarError, ValueError) as exc:
        raise BackupError("encrypted backup could not be created") from exc
    finally:
        _zero(key)
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def restore_encrypted_backup(
    backup_path: Path,
    destination: Path,
    passphrase: str,
) -> Path:
    source = backup_path.expanduser().resolve()
    target = destination.expanduser().resolve()
    _require_passphrase(passphrase)
    if not source.is_file():
        raise BackupError("backup file does not exist")
    if target.exists():
        raise BackupError("restore destination must not already exist")
    if not target.parent.is_dir():
        raise BackupError("restore destination parent does not exist")

    plaintext: Path | None = None
    staging: Path | None = None
    key: bytearray | None = None
    try:
        with source.open("rb") as encrypted:
            prefix, header, ciphertext_length, tag = _read_header(encrypted, source)
            key = bytearray(_derive_key(passphrase, header["kdf"]))
            decryptor = Cipher(
                algorithms.AES(bytes(key)),
                modes.GCM(_unb64(header["nonce"]), tag),
            ).decryptor()
            decryptor.authenticate_additional_data(prefix)
            descriptor, plaintext_name = tempfile.mkstemp(
                prefix="investing-bot-restore-", suffix=".tar.gz"
            )
            plaintext = Path(plaintext_name)
            os.chmod(plaintext, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                remaining = ciphertext_length
                while remaining:
                    chunk = encrypted.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise BackupError("backup file is truncated")
                    output.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                output.write(decryptor.finalize())

        staging = Path(tempfile.mkdtemp(prefix="investing-bot-restore-"))
        os.chmod(staging, 0o700)
        with tarfile.open(plaintext, mode="r:gz") as archive:
            members = archive.getmembers()
            _validate_archive_members(members)
            archive.extractall(staging, members=members, filter="data")
        restored = staging / "data"
        manifest = staging / "backup-manifest.json"
        if not restored.is_dir() or not manifest.is_file():
            raise BackupError("backup contents are incomplete")
        restored.replace(target)
        return target
    except InvalidTag as exc:
        raise BackupError("backup passphrase is incorrect or the file was modified") from exc
    except (KeyError, TypeError, ValueError, OSError, tarfile.TarError) as exc:
        if isinstance(exc, BackupError):
            raise
        raise BackupError("encrypted backup could not be restored") from exc
    finally:
        if key is not None:
            _zero(key)
        if plaintext is not None:
            plaintext.unlink(missing_ok=True)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


class _EncryptingWriter:
    def __init__(self, output: BinaryIO, encryptor: object) -> None:
        self.output = output
        self.encryptor = encryptor
        self.position = 0

    def write(self, value: bytes) -> int:
        self.output.write(self.encryptor.update(value))
        self.position += len(value)
        return len(value)

    def tell(self) -> int:
        return self.position

    def flush(self) -> None:
        self.output.flush()

    def close(self) -> None:
        self.flush()


def _read_header(
    stream: BinaryIO, path: Path
) -> tuple[bytes, dict[str, object], int, bytes]:
    magic = stream.read(len(MAGIC))
    if magic != MAGIC:
        raise BackupError("file is not an investing-bot encrypted backup")
    length_bytes = stream.read(4)
    if len(length_bytes) != 4:
        raise BackupError("backup header is truncated")
    header_length = struct.unpack(">I", length_bytes)[0]
    if not 1 <= header_length <= MAX_HEADER_LENGTH:
        raise BackupError("backup header is invalid")
    header_bytes = stream.read(header_length)
    if len(header_bytes) != header_length:
        raise BackupError("backup header is truncated")
    header = json.loads(header_bytes)
    if (
        header.get("version") != 1
        or header.get("cipher") != "aes-256-gcm"
        or not isinstance(header.get("kdf"), dict)
    ):
        raise BackupError("backup format is unsupported")
    prefix = magic + length_bytes + header_bytes
    size = path.stat().st_size
    ciphertext_length = size - len(prefix) - TAG_LENGTH
    if ciphertext_length <= 0:
        raise BackupError("backup payload is missing")
    stream.seek(size - TAG_LENGTH)
    tag = stream.read(TAG_LENGTH)
    stream.seek(len(prefix))
    return prefix, header, ciphertext_length, tag


def _validate_archive_members(members: list[tarfile.TarInfo]) -> None:
    for member in members:
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts:
            raise BackupError("backup contains an unsafe path")
        if member.issym() or member.islnk() or member.isdev():
            raise BackupError("backup contains an unsupported filesystem entry")
        if member.name != "backup-manifest.json" and path.parts[:1] != ("data",):
            raise BackupError("backup contains an unexpected entry")


def _derive_key(passphrase: str, parameters: dict[str, object]) -> bytes:
    try:
        if parameters["name"] != "argon2id":
            raise ValueError
        return Argon2id(
            salt=_unb64(parameters["salt"]),
            length=int(parameters["length"]),
            iterations=int(parameters["iterations"]),
            lanes=int(parameters["lanes"]),
            memory_cost=int(parameters["memory_cost_kib"]),
        ).derive(passphrase.encode("utf-8"))
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError("backup key-derivation settings are invalid") from exc


def _require_passphrase(value: str) -> None:
    if len(value) < 12:
        raise ValueError("backup passphrase must be at least 12 characters")


def _b64(value: bytes) -> str:
    return b64encode(value).decode("ascii")


def _unb64(value: object) -> bytes:
    if not isinstance(value, str):
        raise ValueError("encoded backup value is invalid")
    return b64decode(value, validate=True)


def _zero(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0
