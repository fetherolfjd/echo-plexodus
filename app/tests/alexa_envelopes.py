"""Builders for realistic Alexa request envelopes, used to "fake out" the Alexa side."""

APPLICATION_ID = 'amzn1.ask.skill.test-skill-id'
USER_ID = 'amzn1.ask.account.test-user-id'
DEVICE_ID = 'amzn1.ask.device.test-device-id'


def _system_context():
    return {
        'System': {
            'application': {'applicationId': APPLICATION_ID},
            'user': {'userId': USER_ID},
            'device': {'deviceId': DEVICE_ID, 'supportedInterfaces': {}},
            'apiEndpoint': 'https://api.amazonalexa.com',
            'apiAccessToken': 'test-api-access-token',
        },
    }


def _slot(name, value):
    return {'name': name, 'value': value, 'confirmationStatus': 'NONE'} if value else \
        {'name': name, 'confirmationStatus': 'NONE'}


def intent_envelope(intent_name, slots=None, request_id='amzn1.echo-api.request.test-request-id'):
    """An in-session IntentRequest envelope, e.g. a "play the artist X" voice command."""
    slots = slots or {}
    return {
        'version': '1.0',
        'session': {
            'new': True,
            'sessionId': 'amzn1.echo-api.session.test-session-id',
            'application': {'applicationId': APPLICATION_ID},
            'user': {'userId': USER_ID},
        },
        'context': _system_context(),
        'request': {
            'type': 'IntentRequest',
            'requestId': request_id,
            'timestamp': '2026-08-27T12:00:00Z',
            'locale': 'en-US',
            'intent': {
                'name': intent_name,
                'confirmationStatus': 'NONE',
                'slots': {name: _slot(name, value) for name, value in slots.items()},
            },
        },
    }


def play_music_envelope(artist=None, song=None, album=None, playlist=None):
    return intent_envelope('PlayMusicIntent', slots={
        'song': song, 'artist': artist, 'album': album, 'playlist': playlist,
    })


def audio_player_envelope(request_type, token, offset_ms=0, request_id='amzn1.echo-api.request.audioplayer'):
    """
    An out-of-session AudioPlayer request (PlaybackNearlyFinished, PlaybackFinished,
    PlaybackStopped, ...) — these carry `context` but, per real Alexa behavior, no `session`.
    """
    return {
        'version': '1.0',
        'context': _system_context(),
        'request': {
            'type': request_type,
            'requestId': request_id,
            'timestamp': '2026-08-27T12:05:00Z',
            'locale': 'en-US',
            'token': token,
            'offsetInMilliseconds': offset_ms,
        },
    }
