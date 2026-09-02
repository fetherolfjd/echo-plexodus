"""Unit tests for the Plex API client: signing, key extraction, and search/resolve logic."""
import itsdangerous
import pytest
from freezegun import freeze_time

from plex import client


# ── Signed path tokens ──────────────────────────────────────────────────────

def test_sign_and_unsign_path_round_trips():
    token = client.sign_path('/library/parts/1/2/file.mp3')
    assert client.unsign_path(token, max_age=60) == '/library/parts/1/2/file.mp3'


def test_unsign_path_rejects_tampered_token():
    token = client.sign_path('/library/parts/1/2/file.mp3')
    # Flip a character in the payload segment (before the first '.') rather than
    # the trailing character of the whole token — the last base64 symbol can have
    # unused bits that decode identically, which would make this test flaky.
    payload, rest = token.split('.', 1)
    mid = len(payload) // 2
    flipped_char = 'a' if payload[mid] != 'a' else 'b'
    tampered = payload[:mid] + flipped_char + payload[mid + 1:] + '.' + rest

    with pytest.raises(itsdangerous.BadSignature):
        client.unsign_path(tampered, max_age=60)


def test_unsign_path_rejects_expired_token():
    with freeze_time('2026-01-01 00:00:00'):
        token = client.sign_path('/library/parts/1/2/file.mp3')

    with freeze_time('2026-01-01 00:00:00') as frozen:
        frozen.tick(61)
        with pytest.raises(itsdangerous.SignatureExpired):
            client.unsign_path(token, max_age=60)


def test_unsign_path_accepts_token_within_ttl():
    with freeze_time('2026-01-01 00:00:00') as frozen:
        token = client.sign_path('/library/parts/1/2/file.mp3')
        frozen.tick(59)
        assert client.unsign_path(token, max_age=60) == '/library/parts/1/2/file.mp3'


def test_a_different_secret_key_cannot_verify_the_token():
    token = client.sign_path('/library/parts/1/2/file.mp3')
    other = itsdangerous.URLSafeTimedSerializer('a-completely-different-key', salt='plex-stream-path')

    with pytest.raises(itsdangerous.BadSignature):
        other.loads(token, max_age=60)


# ── Key extraction from Plex track metadata ─────────────────────────────────

def _track_with_media(key='/library/parts/1/2/file.mp3'):
    return {
        'title': 'Higher',
        'Media': [{'Part': [{'key': key}]}],
    }


def test_get_stream_key_extracts_part_key():
    assert client.get_stream_key(_track_with_media()) == '/library/parts/1/2/file.mp3'


def test_get_stream_key_returns_none_when_media_missing():
    assert client.get_stream_key({'title': 'No Media'}) is None


def test_get_stream_key_returns_none_when_part_missing():
    assert client.get_stream_key({'title': 'No Part', 'Media': [{}]}) is None


def test_get_thumb_key_prefers_track_thumb_then_falls_back():
    assert client.get_thumb_key({'thumb': '/t1', 'parentThumb': '/t2'}) == '/t1'
    assert client.get_thumb_key({'parentThumb': '/t2', 'grandparentThumb': '/t3'}) == '/t2'
    assert client.get_thumb_key({'grandparentThumb': '/t3'}) == '/t3'
    assert client.get_thumb_key({}) is None


# ── URL building from a key ─────────────────────────────────────────────────

def test_stream_url_for_key_builds_signed_public_url():
    url = client.stream_url_for_key('/library/parts/1/2/file.mp3')
    assert url.startswith(f'https://{client.PUBLIC_HOSTNAME}/stream/')
    token = url.rsplit('/', 1)[1]
    assert client.unsign_path(token, max_age=60) == '/library/parts/1/2/file.mp3'


def test_stream_url_for_key_none_when_no_key():
    assert client.stream_url_for_key(None) is None


def test_thumb_url_for_key_builds_signed_public_url():
    url = client.thumb_url_for_key('/library/metadata/1/thumb/2')
    assert url.startswith(f'https://{client.PUBLIC_HOSTNAME}/thumb/')


# ── resolve_play_request against a mocked Plex server ───────────────────────

def _search_result(rating_key, title, type_):
    return {
        'MediaContainer': {
            'SearchResult': [
                {'Metadata': {'ratingKey': rating_key, 'title': title, 'type': type_}}
            ]
        }
    }


def _all_leaves(tracks):
    return {'MediaContainer': {'Metadata': tracks}}


def _track_metadata(rating_key, title, artist, part_key):
    return {
        'ratingKey': rating_key,
        'title': title,
        'grandparentTitle': artist,
        'parentTitle': 'Some Album',
        'type': 'track',
        'duration': 210000,
        'Media': [{'Part': [{'key': part_key}]}],
    }


def test_resolve_play_request_artist_shuffles_and_signs_tracks(requests_mock):
    base = client.PLEX_URL.rstrip('/')

    requests_mock.get(
        f'{base}/library/search',
        json=_search_result('100', 'Creed', 'artist'),
    )
    requests_mock.get(
        f'{base}/library/metadata/100/allLeaves',
        json=_all_leaves([
            _track_metadata('201', 'Higher', 'Creed', '/library/parts/2/1/higher.mp3'),
            _track_metadata('202', 'My Sacrifice', 'Creed', '/library/parts/2/2/sacrifice.mp3'),
        ]),
    )

    tracks, description = client.resolve_play_request('artist', 'Creed')

    assert 'Creed' in description
    assert {t['title'] for t in tracks} == {'Higher', 'My Sacrifice'}
    for t in tracks:
        assert t['stream_key'].startswith('/library/parts/')
        # The real Plex token must never appear on a resolved track.
        assert client.PLEX_TOKEN not in str(t)


def test_resolve_play_request_artist_not_found(requests_mock):
    base = client.PLEX_URL.rstrip('/')
    requests_mock.get(f'{base}/library/search', json={'MediaContainer': {}})

    tracks, description = client.resolve_play_request('artist', 'Nobody')

    assert tracks == []
    assert 'Nobody' in description


def test_internal_plex_requests_send_token_as_header_not_query_param(requests_mock):
    base = client.PLEX_URL.rstrip('/')
    requests_mock.get(f'{base}/library/search', json={'MediaContainer': {}})

    client.search_artists('Creed')

    made_request = requests_mock.request_history[0]
    assert made_request.headers['X-Plex-Token'] == client.PLEX_TOKEN
    assert 'X-Plex-Token' not in (made_request.query or '')
