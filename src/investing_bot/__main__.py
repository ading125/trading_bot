"""Production server and terminal-only credential-vault commands."""

from __future__ import annotations

import argparse
from getpass import getpass
import sys

import uvicorn

from investing_bot.app import create_app
from investing_bot.config import AppSettings, get_settings
from investing_bot.providers import EncryptedCredentialStore, CredentialVaultError


def main() -> None:
    settings = get_settings()
    parser = _parser()
    arguments = parser.parse_args()
    try:
        if arguments.command == "credentials":
            _credentials_command(settings, arguments)
            return
        _serve(settings, unlock_credentials=arguments.unlock_credentials)
    except (CredentialVaultError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


def _serve(settings: AppSettings, *, unlock_credentials: bool) -> None:
    vault = EncryptedCredentialStore(settings.credential_vault_path)
    if unlock_credentials:
        _require_terminal()
        vault.unlock(getpass("Credential vault unlock secret: "))
    try:
        uvicorn.run(
            create_app(settings, credentials=vault),
            host=str(settings.bind_host),
            port=settings.port,
            access_log=False,
            log_config=None,
        )
    finally:
        vault.lock()


def _credentials_command(settings: AppSettings, arguments: argparse.Namespace) -> None:
    vault = EncryptedCredentialStore(settings.credential_vault_path)
    if arguments.credential_command == "status":
        status = vault.status()
        print(f"Initialized: {'yes' if status.initialized else 'no'}")
        print("Unlocked: no")
        print(
            "References: "
            + (", ".join(status.references) if status.references else "none")
        )
        return

    _require_terminal()
    if arguments.credential_command == "init":
        first = getpass("Create credential vault unlock secret: ")
        second = getpass("Confirm credential vault unlock secret: ")
        if first != second:
            raise ValueError("unlock secrets did not match")
        vault.initialize(first)
        vault.lock()
        print("Credential vault initialized and locked.")
        return

    if arguments.credential_command == "set":
        vault.unlock(getpass("Credential vault unlock secret: "))
        try:
            first = getpass(f"Secret for {arguments.reference}: ")
            second = getpass("Confirm provider secret: ")
            if first != second:
                raise ValueError("provider secrets did not match")
            vault.set_secret(
                arguments.reference,
                first,
                credential_kind=arguments.credential_kind,
            )
        finally:
            vault.lock()
        print(f"Credential {arguments.reference} stored and vault locked.")
        return

    raise ValueError("credential command is required")


def _require_terminal() -> None:
    if not sys.stdin.isatty():
        raise CredentialVaultError(
            "credential entry requires an interactive terminal with hidden input"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="investing-bot")
    subcommands = parser.add_subparsers(dest="command")
    serve = subcommands.add_parser("serve", help="run the local dashboard")
    serve.add_argument(
        "--unlock-credentials",
        action="store_true",
        help="prompt for the vault unlock secret before startup",
    )
    credentials = subcommands.add_parser(
        "credentials", help="manage encrypted provider credentials"
    )
    credential_commands = credentials.add_subparsers(dest="credential_command")
    credential_commands.add_parser("init", help="initialize the encrypted vault")
    credential_commands.add_parser("status", help="show only safe vault status")
    set_command = credential_commands.add_parser(
        "set", help="store or replace one encrypted provider secret"
    )
    set_command.add_argument("reference", help="opaque reference such as cred_openai")
    set_command.add_argument(
        "--credential-kind", default="api_token", help="non-secret credential type"
    )
    parser.set_defaults(unlock_credentials=False)
    return parser


if __name__ == "__main__":
    main()
