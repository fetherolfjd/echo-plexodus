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


def test_stream_proxy_logs_completion_with_byte_count(flask_client, requests_mock, caplog):
    """
    The only prior visibility into /stream and /thumb was the error path (502 on Plex
    being unreachable). There was no record at all of a successful fetch — status code,
    bytes actually sent — which made it impossible to tell whether Alexa ever requested
    an enqueued track's URL in the first place.
    """
    path = '/library/parts/1/2/file.mp3'
    requests_mock.get(_plex_url(path), content=b'fake-audio-bytes', headers={'Content-Type': 'audio/mpeg'})
    token = client.sign_path(path)

    with caplog.at_level('INFO'):
        resp = flask_client.get(f'/stream/{token}')

    assert resp.status_code == 200
    assert any(f"Proxying GET {path}: upstream status=200" in r.message for r in caplog.records)
    assert any(f"Finished proxying {path}: {len(resp.data)} bytes sent" in r.message for r in caplog.records)


def test_stream_proxy_ends_cleanly_when_plex_drops_connection_mid_stream(flask_client, monkeypatch, caplog):
    """
    Plex occasionally cuts a transfer off partway through (observed in production as
    urllib3 IncompleteRead / requests ChunkedEncodingError). Left uncaught, that used to
    escape mid-response and crash the proxy generator, Alexa was left with a silently
    truncated file, and the queue never advanced since Alexa never reports that back to
    the skill. The fix should log the failure and just end the response with whatever
    bytes made it through, rather than letting the exception propagate.
    """
    import requests as requests_module
    import app as app_module
    from plex import client

    path = '/library/parts/1/2/file.mp3'

    class _FlakyUpstream:
        status_code = 200
        headers = {'Content-Type': 'audio/mpeg'}

        def iter_content(self, chunk_size):
            yield b'partial-audio-bytes'
            raise requests_module.exceptions.ChunkedEncodingError(
                'Connection broken: IncompleteRead(17 bytes read, 500 more expected)'
            )

        def close(self):
            self.closed = True

    upstream = _FlakyUpstream()
    monkeypatch.setattr(
        app_module._requests, 'request',
        lambda method, url, headers=None, stream=None, timeout=None: upstream,
    )

    token = client.sign_path(path)
    with caplog.at_level('ERROR'):
        resp = flask_client.get(f'/stream/{token}')

    assert resp.status_code == 200
    assert resp.data == b'partial-audio-bytes'
    assert upstream.closed
    assert any('broke mid-stream' in r.message and path in r.message for r in caplog.records)


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
    from _version import __version__

    resp = flask_client.get('/health')
    assert resp.status_code == 200
    assert resp.get_json() == {'status': 'ok', 'version': __version__}
