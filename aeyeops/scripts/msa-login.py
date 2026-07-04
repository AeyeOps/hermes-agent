#!/usr/bin/env python3
"""
Personal Microsoft account (MSA) Graph token acquisition.

`az login` is hardwired to Azure Resource Manager and rejects consumer MSA
accounts (hotmail.com / outlook.com / personal). This script uses MSAL directly
against the MSA consumer endpoint with a well-known public client app so
personal accounts can authenticate to Microsoft Graph for the scopes OneDrive,
Mail, and Calendar subscriptions need.

Persists the token cache to ~/.azure/msa-token-cache.json (separate from az's
Entra cache) so subsequent runs + the bridge plugin can read it and MSAL
auto-refreshes access tokens from the cached refresh token — no repeated logins.

Usage:
    msa-login.py                      # device-code flow (interactive, one-time)
    msa-login.py --check              # show cached account + valid scopes
    msa-login.py --token              # print a fresh access token for Graph

The cached refresh token keeps the session alive indefinitely (refresh tokens
for MSA last 90 days of inactivity; any use resets the clock).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import msal

# Microsoft Graph PowerShell's first-party public client. Microsoft ships this
# pre-registered, accepts personal Microsoft accounts, and is already
# admin-consented for the delegated Graph scopes we need (Files.Read,
# Mail.Read, Calendars.Read, User.Read). Using a well-known public client
# avoids needing to register our own app for personal-account auth.
CLIENT_ID = "14d82eec-204b-4c2f-b7e8-296a70dab67e"
AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = [
    "User.Read",
    "Files.Read",
    "Files.Read.All",
    "Mail.Read",
    "Calendars.Read",
    # offline_access is added automatically by MSAL (it's a reserved scope);
    # listing it explicitly raises ValueError.
]

CACHE_PATH = Path.home() / ".azure" / "msa-token-cache.json"


def _build_app() -> tuple[msal.PublicClientApplication, msal.SerializableTokenCache]:
    cache = msal.SerializableTokenCache()
    if CACHE_PATH.exists():
        cache.deserialize(CACHE_PATH.read_text())
    app = msal.PublicClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache,
    )
    return app, cache


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(cache.serialize())
        os.chmod(CACHE_PATH, 0o600)


def acquire_silent(app: msal.PublicClientApplication) -> dict | None:
    """Return a fresh access token from the cache (refresh if needed), or None."""
    accounts = app.get_accounts()
    if not accounts:
        return None
    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    return result


def cmd_login() -> int:
    app, cache = _build_app()
    # Try silent first (refresh-token path) — no browser if already authed.
    result = acquire_silent(app)
    if not result:
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "error" in flow:
            print(
                f"device flow error: {flow.get('error_description', flow.get('error'))}",
                file=sys.stderr,
            )
            return 1
        print(flow["message"], file=sys.stderr)  # "use a browser... enter code XXXX"
        result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print(
            f"auth failed: {result.get('error_description', result.get('error'))}",
            file=sys.stderr,
        )
        return 1
    _save_cache(cache)
    accounts = app.get_accounts()
    upn = accounts[0].get("username") if accounts else "?"
    print(f"✓ authenticated: {upn}")
    print(f"  cache: {CACHE_PATH}")
    return 0


def cmd_check() -> int:
    app, _ = _build_app()
    accounts = app.get_accounts()
    if not accounts:
        print("(no cached account)")
        return 1
    acc = accounts[0]
    print(
        f"account: {acc.get('username')}  home: {acc.get('home_account_id', '?')[:24]}..."
    )
    result = acquire_silent(app)
    if not result:
        print("refresh token expired — re-run with no args to re-auth")
        return 1
    print(f"scopes granted: {result.get('scope', '?')[:80]}")
    return 0


def cmd_token() -> int:
    app, _ = _build_app()
    result = acquire_silent(app)
    if not result or "access_token" not in result:
        print("not authenticated — re-run with no args", file=sys.stderr)
        return 1
    print(result["access_token"])
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--check", action="store_true", help="show cached account + scopes (no browser)"
    )
    p.add_argument(
        "--token",
        action="store_true",
        help="print a fresh Graph access token (no browser)",
    )
    args = p.parse_args()
    if args.check:
        return cmd_check()
    if args.token:
        return cmd_token()
    return cmd_login()


if __name__ == "__main__":
    sys.exit(main())
