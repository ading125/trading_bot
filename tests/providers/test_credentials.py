from __future__ import annotations

import json
import os
from pathlib import Path
import stat

import pytest

from investing_bot.providers import (
    CredentialUnlockError,
    CredentialVaultError,
    CredentialVaultLockedError,
    EncryptedCredentialStore,
)


UNLOCK_SECRET = "correct horse battery staple"


def vault(tmp_path: Path) -> EncryptedCredentialStore:
    return EncryptedCredentialStore(
        tmp_path / "credentials",
        memory_cost_kib=8 * 1024,
        iterations=1,
        lanes=1,
    )


def test_encrypted_vault_round_trip_and_lock_state(tmp_path: Path) -> None:
    store = vault(tmp_path)
    store.initialize(UNLOCK_SECRET)
    store.set_secret("cred_fixture_token", "provider-secret-value")

    assert store.status().initialized is True
    assert store.status().unlocked is True
    assert store.status().references == ("cred_fixture_token",)
    assert store.get_secret("cred_fixture_token") == "provider-secret-value"
    assert store.is_configured("cred_fixture_token") is True
    assert stat.S_IMODE((store.root / "vault.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(
        (store.root / "cred_fixture_token.json").stat().st_mode
    ) == 0o600
    assert all(
        "provider-secret-value" not in path.read_text(encoding="utf-8")
        for path in store.root.glob("*.json")
    )

    store.lock()
    assert store.has_reference("cred_fixture_token") is True
    assert store.is_configured("cred_fixture_token") is False
    with pytest.raises(CredentialVaultLockedError, match="locked"):
        store.get_secret("cred_fixture_token")

    store.unlock(UNLOCK_SECRET)
    assert store.get_secret("cred_fixture_token") == "provider-secret-value"


def test_wrong_unlock_and_tampering_use_sanitized_errors(tmp_path: Path) -> None:
    store = vault(tmp_path)
    store.initialize(UNLOCK_SECRET)
    store.set_secret("cred_fixture_token", "provider-secret-value")
    store.lock()

    with pytest.raises(CredentialUnlockError) as wrong:
        store.unlock("this is the wrong unlock secret")
    assert "provider-secret-value" not in str(wrong.value)

    store.unlock(UNLOCK_SECRET)
    entry = store.root / "cred_fixture_token.json"
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["credential_kind"] = "different_kind"
    entry.write_text(json.dumps(payload), encoding="utf-8")
    os.chmod(entry, 0o600)
    with pytest.raises(CredentialUnlockError) as tampered:
        store.get_secret("cred_fixture_token")
    assert "provider-secret-value" not in str(tampered.value)


def test_vault_rejects_unsafe_files_and_invalid_inputs(tmp_path: Path) -> None:
    store = vault(tmp_path)
    with pytest.raises(ValueError, match="12 to 1024"):
        store.initialize("too-short")

    store.initialize(UNLOCK_SECRET)
    with pytest.raises(ValueError, match="reference"):
        store.set_secret("../../escape", "secret")
    store.lock()
    os.chmod(store.root / "vault.json", 0o644)
    with pytest.raises(CredentialVaultError, match="permissions are unsafe"):
        store.unlock(UNLOCK_SECRET)
