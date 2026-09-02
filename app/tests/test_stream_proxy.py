"""
Tests for the /stream and /thumb routes: this is the piece that keeps the real
Plex token from ever reaching Alexa. Fakes "the Plex side" with requests_mock.
"""
from freezegun import freeze_time

from plex import client


def _plex_url(path):
    return client.PLEX_URL.rstrip('/') + path


def test_stream_proxy_forwards_audio_bytes_and_attaches_real_token(flask_client, requests_mock, plex_token):
    path = '/library/parts/1/2/file.mp3'
    requests_mock.get(_plex_url(path), content=b'fake-audio-bytes', headers={'Content-Type': 'audio/mpeg'})

    token = client.sign_path(path)
    resp = flask_client.get(f'/stream/{token}')

    assert resp.status_code == 200
    assert resp.data == b'fake-audio-bytes'
    assert plex_token.encode() not in resp.data
    assert plex_token not in resp.headers.get('Content-Type', '')

    upstream_request = requests_mock.request_history[0]
    assert upstream_request.headers['X-Plex-Token'] == plex_token
    assert plex_token not in upstream_request.url  # never a query param


def test_stream_proxy_forwards_range_header_for_seeking(flask_client, requests_mock):
    path = '/library/parts/1/2/file.mp3'
    requests_mock.get(
        _plex_url(path),
        content=b'partial-bytes',
        status_code=206,
        headers={'Content-Range': 'bytes 100-199/200'},
    )
    token = client.sign_path(path)

    resp = flask_client.get(f'/stream/{token}', headers={'Range': 'bytes=100-199'})

    assert resp.status_code == 206
    upstream_request = requests_mock.request_history[0]
    assert upstream_request.headers['Range'] == 'bytes=100-199'


def test_stream_proxy_rejects_tampered_token(flask_client):
    token = client.sign_path('/library/parts/1/2/file.mp3')
    payload, rest = token.split('.', 1)
    mid = len(payload) // 2
    flipped = 'a' if payload[mid] != 'a' else 'b'
    tampered = payload[:mid] + flipped + payload[mid + 1:] + '.' + rest

    resp = flask_client.get(f'/stream/{tampered}')

    assert resp.status_code == 403


def test_stream_proxy_rejects_expired_token(flask_client):
    with freeze_time('2026-01-01 00:00:00') as frozen:
        token = client.sign_path('/library/parts/1/2/file.mp3')
        frozen.tick(3601)  # past the configured 3600s test TTL
        resp = flask_client.get(f'/stream/{token}')

    assert resp.status_code == 410


def test_stream_proxy_returns_502_when_plex_unreachable(flask_client, requests_mock):
    import requests
    path = '/library/parts/1/2/file.mp3'
    requests_mock.get(_plex_url(path), exc=requests.exceptions.ConnectionError)
    token = client.sign_path(path)

    resp = flask_client.get(f'/stream/{token}')

    assert resp.status_code == 502


def test_thumb_proxy_works_the_same_way(flask_client, requests_mock, plex_token):
    path = '/library/metadata/1/thumb/2'
    requests_mock.get(_plex_url(path), content=b'fake-jpeg-bytes', headers={'Content-Type': 'image/jpeg'})
    token = client.sign_path(path)

    resp = flask_client.get(f'/thumb/{token}')

    assert resp.status_code == 200
    assert resp.data == b'fake-jpeg-bytes'
    upstream_request = requests_mock.request_history[0]
    assert upstream_request.headers['X-Plex-Token'] == plex_token


def test_skill_route_rejects_get(flask_client):
    # Alexa only ever POSTs to /skill.
    resp = flask_client.get('/skill')
    assert resp.status_code == 405


def test_health_endpoint(flask_client):
    resp = flask_client.get('/health')
    assert resp.status_code == 200
    assert resp.get_json() == {'status': 'ok'}
