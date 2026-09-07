"""
Full-workflow integration test: fakes the Alexa side (hand-built request envelopes
posted to /skill, exactly like Alexa Cloud would) and the Plex side (requests_mock),
and proves the whole loop works — including that the real Plex token never appears
anywhere in what Alexa sees.
"""
import json
import re
import xml.etree.ElementTree as ET

from plex import client as plex_client
from skill import queue as skill_queue

from alexa_envelopes import USER_ID, play_music_envelope, play_playlist_envelope, audio_player_envelope


def _plex_url(path):
    return plex_client.PLEX_URL.rstrip('/') + path


def _search_result(rating_key, title, type_):
    return {'MediaContainer': {'SearchResult': [{'Metadata': {'ratingKey': rating_key, 'title': title, 'type': type_}}]}}


def _track_metadata(rating_key, title, artist, part_key, thumb):
    return {
        'ratingKey': rating_key,
        'title': title,
        'grandparentTitle': artist,
        'parentTitle': 'Human Clay',
        'type': 'track',
        'duration': 210000,
        'thumb': thumb,
        'Media': [{'Part': [{'key': part_key}]}],
    }


def _stub_plex_artist_and_tracks(requests_mock):
    requests_mock.get(_plex_url('/library/search'), json=_search_result('100', 'Creed', 'artist'))
    requests_mock.get(_plex_url('/library/metadata/100/allLeaves'), json={
        'MediaContainer': {
            'Metadata': [
                _track_metadata('201', 'Higher', 'Creed', '/library/parts/2/1/higher.mp3', '/library/metadata/201/thumb/1'),
                _track_metadata('202', 'My Sacrifice', 'Creed', '/library/parts/2/2/sacrifice.mp3', '/library/metadata/202/thumb/1'),
            ]
        }
    })


def _extract_play_directive(response_json):
    directives = response_json['response']['directives']
    play = next(d for d in directives if d['type'] == 'AudioPlayer.Play')
    return play['audioItem']['stream']


def _path_from_public_url(url):
    # https://<hostname>/stream/<token> -> /stream/<token>, so the Flask test client can hit it.
    return '/' + url.split('/', 3)[3]


def test_play_artist_end_to_end(flask_client, requests_mock, plex_token):
    """
    "Alexa, ask Plex to play the artist Creed" -> skill searches (fake) Plex, returns
    an AudioPlayer.Play directive with a signed URL -> Alexa "fetches" that URL -> our
    proxy attaches the real token server-side and streams back (fake) Plex's audio.
    """
    _stub_plex_artist_and_tracks(requests_mock)
    # get_artist_tracks shuffles, so either track can end up first — stub both files.
    requests_mock.get(
        _plex_url('/library/parts/2/1/higher.mp3'),
        content=b'--fake-mp3-bytes-for-higher--',
        headers={'Content-Type': 'audio/mpeg'},
    )
    requests_mock.get(
        _plex_url('/library/parts/2/2/sacrifice.mp3'),
        content=b'--fake-mp3-bytes-for-sacrifice--',
        headers={'Content-Type': 'audio/mpeg'},
    )

    resp = flask_client.post('/skill', json=play_music_envelope(artist='Creed'))
    assert resp.status_code == 200
    body = resp.get_json()

    # The real Plex token must never appear anywhere in what Alexa receives.
    assert plex_token not in json.dumps(body)

    stream = _extract_play_directive(body)
    assert stream['url'].startswith(f'https://{plex_client.PUBLIC_HOSTNAME}/stream/')

    current_track = skill_queue.get_current_track(USER_ID)
    expected_bytes = b'--fake-mp3-bytes-for-higher--' if 'higher' in current_track['stream_key'] \
        else b'--fake-mp3-bytes-for-sacrifice--'

    # Now simulate Alexa's AudioPlayer actually fetching that URL.
    audio_resp = flask_client.get(_path_from_public_url(stream['url']))
    assert audio_resp.status_code == 200
    assert audio_resp.data == expected_bytes

    # And confirm the *server* attached the real token to the upstream Plex request —
    # it just never went anywhere Alexa (or this test, playing Alexa) could see it.
    upstream_request = requests_mock.request_history[-1]
    assert upstream_request.headers['X-Plex-Token'] == plex_token


def test_play_artist_with_ampersand_in_name_end_to_end(flask_client, requests_mock, plex_token):
    """
    ask-sdk's speak() wraps whatever text it's given in <speak>...</speak> with no
    XML escaping of its own, so an unescaped artist name like "Mumford & Sons" would
    produce invalid SSML and Alexa would reject the whole response ("There was a
    problem with the requested skill's response"). Confirms handler._ssml_escape is
    applied before text reaches speak().
    """
    requests_mock.get(_plex_url('/library/search'), json=_search_result('600', 'Mumford & Sons', 'artist'))
    requests_mock.get(_plex_url('/library/metadata/600/allLeaves'), json={
        'MediaContainer': {
            'Metadata': [
                _track_metadata('601', 'I Will Wait', 'Mumford & Sons', '/library/parts/6/1/wait.mp3', '/library/metadata/601/thumb/1'),
            ]
        }
    })
    requests_mock.get(
        _plex_url('/library/parts/6/1/wait.mp3'),
        content=b'--fake-mp3-bytes-for-wait--',
        headers={'Content-Type': 'audio/mpeg'},
    )

    resp = flask_client.post('/skill', json=play_music_envelope(artist='Mumford & Sons'))
    assert resp.status_code == 200
    body = resp.get_json()

    ssml = body['response']['outputSpeech']['ssml']
    assert '&amp;' in ssml
    # A bare "&" not part of a recognized entity is invalid XML on its own — this
    # catches any other unescaped call site, not just the one this test happens to hit.
    assert not re.search(r'&(?!amp;|lt;|gt;|#\d+;|#x[0-9a-fA-F]+;)', ssml)
    ET.fromstring(ssml)  # raises ParseError if the SSML isn't well-formed


def test_play_playlist_end_to_end(flask_client, requests_mock, plex_token):
    """
    "Alexa, ask Plex to play the playlist Road Trip" is handled by the dedicated
    PlayPlaylistIntent (AMAZON.SearchQuery slot), not the playlist branch of
    PlayMusicIntent — kept separate so Alexa's NLU can't resolve an unrecognized
    personal playlist name into the AMAZON.MusicGroup-typed artist slot instead.
    """
    requests_mock.get(_plex_url('/playlists/all'), json={
        'MediaContainer': {
            'Metadata': [{'ratingKey': '500', 'title': 'Road Trip', 'playlistType': 'audio'}],
        }
    })
    requests_mock.get(_plex_url('/library/shared/all'), json={'MediaContainer': {}})
    requests_mock.get(_plex_url('/playlists/500/items'), json={
        'MediaContainer': {
            'Metadata': [
                _track_metadata('301', 'Life Is a Highway', 'Rascal Flatts', '/library/parts/3/1/highway.mp3', '/library/metadata/301/thumb/1'),
            ]
        }
    })
    requests_mock.get(
        _plex_url('/library/parts/3/1/highway.mp3'),
        content=b'--fake-mp3-bytes-for-highway--',
        headers={'Content-Type': 'audio/mpeg'},
    )

    resp = flask_client.post('/skill', json=play_playlist_envelope('Road Trip'))
    assert resp.status_code == 200
    body = resp.get_json()
    assert plex_token not in json.dumps(body)

    stream = _extract_play_directive(body)
    assert stream['url'].startswith(f'https://{plex_client.PUBLIC_HOSTNAME}/stream/')

    audio_resp = flask_client.get(_path_from_public_url(stream['url']))
    assert audio_resp.status_code == 200
    assert audio_resp.data == b'--fake-mp3-bytes-for-highway--'


def test_playback_nearly_finished_enqueues_next_track_with_working_url(flask_client, requests_mock, plex_token):
    """Proves the token-refresh fix: the *next* track's URL is minted fresh, not stale."""
    _stub_plex_artist_and_tracks(requests_mock)
    requests_mock.get(_plex_url('/library/parts/2/1/higher.mp3'), content=b'higher-bytes')
    requests_mock.get(_plex_url('/library/parts/2/2/sacrifice.mp3'), content=b'sacrifice-bytes')

    play_resp = flask_client.post('/skill', json=play_music_envelope(artist='Creed'))
    first_stream = _extract_play_directive(play_resp.get_json())

    # The current queue's second entry may be either track depending on the shuffle,
    # so read it back from the (fake) session's actual state rather than assuming order.
    current_track = skill_queue.get_current_track(USER_ID)
    next_track = skill_queue.get_next_track(USER_ID)
    assert current_track is not None and next_track is not None

    nearly_finished = flask_client.post(
        '/skill',
        json=audio_player_envelope('AudioPlayer.PlaybackNearlyFinished', token=first_stream['token']),
    )
    assert nearly_finished.status_code == 200
    body = nearly_finished.get_json()
    assert plex_token not in json.dumps(body)

    enqueue_directive = next(
        d for d in body['response']['directives'] if d['type'] == 'AudioPlayer.Play'
    )
    next_stream = enqueue_directive['audioItem']['stream']
    assert next_stream['url'].startswith(f'https://{plex_client.PUBLIC_HOSTNAME}/stream/')
    assert next_stream['url'] != first_stream['url']  # a fresh token, not the same one reused

    # And it actually works — proxies through to the right (fake) Plex file.
    expected_bytes = b'higher-bytes' if 'higher' in next_track['stream_key'] else b'sacrifice-bytes'
    fetch_resp = flask_client.get(_path_from_public_url(next_stream['url']))
    assert fetch_resp.status_code == 200
    assert fetch_resp.data == expected_bytes


def test_resume_after_expiry_mints_a_fresh_token_rather_than_reusing_a_dead_one(
    flask_client, requests_mock, plex_token, monkeypatch
):
    """
    Regression test for the original "signed once per queue" bug: resuming a long-paused
    track must not replay a URL signed hours ago.
    """
    _stub_plex_artist_and_tracks(requests_mock)
    requests_mock.get(_plex_url('/library/parts/2/1/higher.mp3'), content=b'higher-bytes')
    requests_mock.get(_plex_url('/library/parts/2/2/sacrifice.mp3'), content=b'sacrifice-bytes')

    flask_client.post('/skill', json=play_music_envelope(artist='Creed'))

    # Force the app's configured TTL down to something we can trivially outlive,
    # simulating "paused long enough for the original link to have expired".
    import app as app_module
    monkeypatch.setattr(app_module, 'STREAM_TOKEN_TTL', 1)
    import time
    time.sleep(1.2)

    resume_resp = flask_client.post('/skill', json=audio_player_envelope('AudioPlayer.PlaybackStopped', token='irrelevant'))
    assert resume_resp.status_code == 200  # PlaybackStopped just records offset, doesn't fail

    resume_intent = flask_client.post('/skill', json={
        'version': '1.0',
        'session': {
            'new': False,
            'sessionId': 'amzn1.echo-api.session.test-session-id',
            'application': {'applicationId': 'amzn1.ask.skill.test-skill-id'},
            'user': {'userId': USER_ID},
        },
        'context': audio_player_envelope('AudioPlayer.PlaybackStopped', token='x')['context'],
        'request': {
            'type': 'IntentRequest',
            'requestId': 'amzn1.echo-api.request.resume',
            'timestamp': '2026-08-27T13:00:00Z',
            'locale': 'en-US',
            'intent': {'name': 'AMAZON.ResumeIntent', 'confirmationStatus': 'NONE', 'slots': {}},
        },
    })
    assert resume_intent.status_code == 200
    body = resume_intent.get_json()
    stream = _extract_play_directive(body)

    # The freshly-minted resume URL must work even though the original TTL already elapsed.
    fetch_resp = flask_client.get(_path_from_public_url(stream['url']))
    assert fetch_resp.status_code == 200
