"""
Shared pytest fixtures.

Env vars must be set before `app`/`plex.client` are imported anywhere, since both
read os.environ at module load time (PLEX_TOKEN, SECRET_KEY, PUBLIC_HOSTNAME, the
skill's request verifiers, etc. are all computed once, at import). That's why this
file sets them at module scope, above every other import.
"""
import os

os.environ.setdefault('SKILL_HOSTNAME', 'plex.test.example')
os.environ.setdefault('PLEX_URL', 'http://plex.internal.test:32400')
os.environ.setdefault('PLEX_TOKEN', 'test-real-plex-token-should-never-leak')
os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-signing-not-for-prod')
os.environ.setdefault('DISABLE_REQUEST_VERIFY', 'true')  # no real Amazon signature in tests
os.environ.setdefault('STREAM_TOKEN_TTL_SECONDS', '3600')
os.environ.pop('APP_VERSION', None)  # tests assert the value compiled in from _version.py

import pytest

try:
    import certvalidator  # noqa: F401
except Exception:
    # ask-sdk-webservice-support's RequestVerifier chain (certvalidator -> oscrypto) doesn't
    # support OpenSSL 3.0.10+ (oscrypto is unmaintained; see wbond/oscrypto#78) and fails to
    # import on many current Linux hosts. Tests always run with DISABLE_REQUEST_VERIFY=true,
    # so RequestVerifier is imported by flask_ask_sdk but never instantiated/used — stub the
    # broken import chain out rather than let an unrelated, unmaintained dependency's bug
    # block the whole test suite from even collecting.
    import sys
    import types

    _certvalidator = types.ModuleType('certvalidator')

    class _StubCertificateValidator:
        def __init__(self, *args, **kwargs):
            pass

    _certvalidator.CertificateValidator = _StubCertificateValidator

    _certvalidator_errors = types.ModuleType('certvalidator.errors')

    class ValidationError(Exception):
        pass

    class PathError(Exception):
        pass

    _certvalidator_errors.ValidationError = ValidationError
    _certvalidator_errors.PathError = PathError
    _certvalidator.errors = _certvalidator_errors

    sys.modules['certvalidator'] = _certvalidator
    sys.modules['certvalidator.errors'] = _certvalidator_errors

import app as flask_app_module
from plex import client as plex_client
from skill import queue as skill_queue


@pytest.fixture(autouse=True)
def _reset_queue_state():
    """Every test starts with an empty in-memory queue store."""
    skill_queue._queues.clear()
    yield
    skill_queue._queues.clear()


@pytest.fixture
def app():
    flask_app_module.app.config.update(TESTING=True)
    return flask_app_module.app


@pytest.fixture
def flask_client(app):
    # Named flask_client (not "client") so it doesn't shadow `from plex import client`,
    # which most test modules import for signing tokens and reading config directly.
    return app.test_client()


@pytest.fixture
def plex_token():
    """The real Plex token tests should assert never leaks anywhere."""
    return plex_client.PLEX_TOKEN


@pytest.fixture
def plex_base_url():
    return plex_client.PLEX_URL.rstrip('/')
