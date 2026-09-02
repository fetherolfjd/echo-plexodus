"""
Unit tests for the Plex PIN-auth script. Fakes plex.tv with requests_mock; the
human-in-the-loop login step (opening the URL, signing into Plex) obviously can't
be tested here, so these cover everything up to and after that manual step.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import get_plex_token as script  # noqa: E402


# ── Client identifier persistence ───────────────────────────────────────────

def test_get_or_create_client_identifier_creates_and_persists(tmp_path):
    path = tmp_path / 'client_id.txt'
    assert not path.exists()

    identifier = script.get_or_create_client_identifier(path)

    assert path.exists()
    assert path.read_text().strip() == identifier


def test_get_or_create_client_identifier_reuses_existing_file(tmp_path):
    path = tmp_path / 'client_id.txt'
    path.write_text('already-set-identifier\n')

    assert script.get_or_create_client_identifier(path) == 'already-set-identifier'


def test_get_or_create_client_identifier_is_stable_across_calls(tmp_path):
    path = tmp_path / 'client_id.txt'
    first = script.get_or_create_client_identifier(path)
    second = script.get_or_create_client_identifier(path)
    assert first == second


# ── PIN request ──────────────────────────────────────────────────────────────

def test_request_pin_sends_product_and_client_identifier_headers(requests_mock):
    requests_mock.post(
        f'{script.PLEX_TV}/api/v2/pins',
        json={'id': 42, 'code': 'ABCD', 'authToken': None},
    )

    pin = script.request_pin('my-client-id')

    assert pin == {'id': 42, 'code': 'ABCD', 'authToken': None}
    sent = requests_mock.request_history[0]
    assert sent.headers['X-Plex-Client-Identifier'] == 'my-client-id'
    assert sent.headers['X-Plex-Product'] == script.PRODUCT_NAME


def test_build_auth_url_contains_client_id_and_code():
    url = script.build_auth_url('my-client-id', 'ABCD')
    assert 'clientID=my-client-id' in url
    assert 'code=ABCD' in url
    assert url.startswith('https://app.plex.tv/auth#?')


# ── Polling ──────────────────────────────────────────────────────────────────

def test_poll_for_token_returns_token_once_authorized(requests_mock):
    requests_mock.get(
        f'{script.PLEX_TV}/api/v2/pins/42',
        [
            {'json': {'authToken': None}},
            {'json': {'authToken': None}},
            {'json': {'authToken': 'the-real-token'}},
        ],
    )
    slept = []

    token = script.poll_for_token(
        'my-client-id', 42,
        interval=0, timeout=10,
        sleep=slept.append, clock=lambda: 0,
    )

    assert token == 'the-real-token'
    assert len(slept) == 2  # slept between the two "not yet" responses


def test_poll_for_token_returns_immediately_if_already_authorized(requests_mock):
    requests_mock.get(f'{script.PLEX_TV}/api/v2/pins/42', json={'authToken': 'instant-token'})

    token = script.poll_for_token('my-client-id', 42, sleep=lambda s: (_ for _ in ()).throw(
        AssertionError('should not have slept')
    ))

    assert token == 'instant-token'


def test_poll_for_token_times_out_without_real_delay(requests_mock):
    requests_mock.get(f'{script.PLEX_TV}/api/v2/pins/42', json={'authToken': None})

    fake_clock = iter([0, 0, 5, 11])  # deadline computed as 0 + timeout(10) = 10

    with pytest.raises(TimeoutError):
        script.poll_for_token(
            'my-client-id', 42,
            interval=0, timeout=10,
            sleep=lambda s: None, clock=lambda: next(fake_clock),
        )


# ── main() ───────────────────────────────────────────────────────────────────

def test_main_writes_token_to_file_by_default(tmp_path, requests_mock, monkeypatch, capsys):
    monkeypatch.setattr(script, 'CLIENT_IDENTIFIER_FILE', tmp_path / 'client_id.txt')
    monkeypatch.setattr(script, 'TOKEN_FILE', tmp_path / 'plex_token.txt')

    requests_mock.post(f'{script.PLEX_TV}/api/v2/pins', json={'id': 1, 'code': 'WXYZ'})
    requests_mock.get(f'{script.PLEX_TV}/api/v2/pins/1', json={'authToken': 'minted-token'})

    exit_code = script.main([])

    assert exit_code == 0
    assert (tmp_path / 'plex_token.txt').read_text().strip() == 'minted-token'
    out = capsys.readouterr().out
    assert 'app.plex.tv/auth' in out
    assert 'Authorized' in out


def test_main_print_only_does_not_write_file(tmp_path, requests_mock, monkeypatch, capsys):
    monkeypatch.setattr(script, 'CLIENT_IDENTIFIER_FILE', tmp_path / 'client_id.txt')
    monkeypatch.setattr(script, 'TOKEN_FILE', tmp_path / 'plex_token.txt')

    requests_mock.post(f'{script.PLEX_TV}/api/v2/pins', json={'id': 1, 'code': 'WXYZ'})
    requests_mock.get(f'{script.PLEX_TV}/api/v2/pins/1', json={'authToken': 'minted-token'})

    exit_code = script.main(['--print-only'])

    assert exit_code == 0
    assert not (tmp_path / 'plex_token.txt').exists()
    assert 'minted-token' in capsys.readouterr().out


def test_main_returns_nonzero_and_prints_to_stderr_on_timeout(tmp_path, requests_mock, monkeypatch, capsys):
    monkeypatch.setattr(script, 'CLIENT_IDENTIFIER_FILE', tmp_path / 'client_id.txt')
    monkeypatch.setattr(script, 'TOKEN_FILE', tmp_path / 'plex_token.txt')
    monkeypatch.setattr(script, 'POLL_TIMEOUT_SECONDS', 0)
    monkeypatch.setattr(script, 'POLL_INTERVAL_SECONDS', 0)

    requests_mock.post(f'{script.PLEX_TV}/api/v2/pins', json={'id': 1, 'code': 'WXYZ'})
    requests_mock.get(f'{script.PLEX_TV}/api/v2/pins/1', json={'authToken': None})

    exit_code = script.main([])

    assert exit_code == 1
    assert not (tmp_path / 'plex_token.txt').exists()
    assert 'Timed out' in capsys.readouterr().err
