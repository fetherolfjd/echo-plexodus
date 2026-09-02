#!/usr/bin/env python3
"""
Mint or rotate a Plex API token via Plex's PIN-based device-linking flow, instead
of digging X-Plex-Token out of browser devtools.

Run it, open the printed URL, log into the Plex account you want the skill to use,
and the token gets written to secrets/plex_token.txt (same file the container
already reads). Only needs `requests`, already in app/requirements.txt — no need
for the dev dependencies just to run this.

Usage:
    python scripts/get_plex_token.py [--print-only]

This is a standalone, occasional setup/rotation step — it is NOT part of the
running app, and the container has no route that does this. Whatever token ends
up in secrets/plex_token.txt works identically to one grabbed via devtools; the
only difference is this one shows up as its own named, independently-revocable
entry ("Echo Plexodus") in your Plex account's Authorized Devices list.
"""
import argparse
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import requests

PLEX_TV = 'https://plex.tv'
PRODUCT_NAME = 'Echo Plexodus'

_REPO_ROOT = Path(__file__).resolve().parent.parent
CLIENT_IDENTIFIER_FILE = _REPO_ROOT / 'secrets' / 'plex_client_identifier.txt'
TOKEN_FILE = _REPO_ROOT / 'secrets' / 'plex_token.txt'

POLL_INTERVAL_SECONDS = 2
POLL_TIMEOUT_SECONDS = 300


def get_or_create_client_identifier(path=None):
    """
    A stable identifier for this app install, persisted locally so re-running this
    script (e.g. to rotate the token) updates the same entry in Plex's Authorized
    Devices list instead of creating a new one every time.
    """
    if path is None:
        path = CLIENT_IDENTIFIER_FILE

    if path.exists():
        existing = path.read_text().strip()
        if existing:
            return existing

    identifier = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(identifier + '\n')
    return identifier


def _headers(client_identifier):
    return {
        'Accept': 'application/json',
        'X-Plex-Product': PRODUCT_NAME,
        'X-Plex-Client-Identifier': client_identifier,
    }


def request_pin(client_identifier, session=requests):
    """POST a new PIN. Returns the parsed JSON (has 'id' and 'code')."""
    resp = session.post(
        f'{PLEX_TV}/api/v2/pins',
        headers=_headers(client_identifier),
        data={'strong': 'true'},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def build_auth_url(client_identifier, code):
    """The page the user opens to log in and authorize this PIN."""
    params = {
        'clientID': client_identifier,
        'code': code,
        'context[device][product]': PRODUCT_NAME,
    }
    return f'https://app.plex.tv/auth#?{urlencode(params)}'


def poll_for_token(
    client_identifier, pin_id, session=requests,
    interval=None, timeout=None,
    sleep=time.sleep, clock=time.monotonic,
):
    """Poll until the PIN has been authorized (authToken is set) or timeout elapses."""
    # Late-bind these two to the module globals (rather than as literal defaults)
    # so tests can monkeypatch POLL_INTERVAL_SECONDS/POLL_TIMEOUT_SECONDS and have
    # it actually take effect on calls made through main(), which doesn't pass
    # either explicitly.
    if interval is None:
        interval = POLL_INTERVAL_SECONDS
    if timeout is None:
        timeout = POLL_TIMEOUT_SECONDS

    deadline = clock() + timeout
    while True:
        resp = session.get(
            f'{PLEX_TV}/api/v2/pins/{pin_id}',
            headers=_headers(client_identifier),
            timeout=10,
        )
        resp.raise_for_status()
        auth_token = resp.json().get('authToken')
        if auth_token:
            return auth_token

        if clock() >= deadline:
            raise TimeoutError(
                'Timed out waiting for Plex authorization — the PIN may have expired. '
                'Run this script again.'
            )
        sleep(interval)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--print-only', action='store_true',
        help=f"Print the token instead of writing it to {TOKEN_FILE}",
    )
    args = parser.parse_args(argv)

    client_identifier = get_or_create_client_identifier()
    pin = request_pin(client_identifier)
    auth_url = build_auth_url(client_identifier, pin['code'])

    print('1. Open this URL and log into the Plex account you want the skill to use:\n')
    print(f'   {auth_url}\n')
    print('2. Waiting for authorization...')

    try:
        token = poll_for_token(client_identifier, pin['id'])
    except TimeoutError as e:
        print(f'\n{e}', file=sys.stderr)
        return 1

    print('\nAuthorized.')
    if args.print_only:
        print(f'Token: {token}')
    else:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_FILE.write_text(token + '\n')
        print(f'Token written to {TOKEN_FILE}')
        print('Restart the container to pick it up.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
