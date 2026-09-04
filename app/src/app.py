"""
Flask app for the Plex Alexa skill.
Uses flask-ask-sdk SkillAdapter with correct API signature.
"""
import os
import sys
import logging
import json

import requests as _requests
from itsdangerous import BadSignature, SignatureExpired
from flask import Flask, request, jsonify, Response, stream_with_context

sys.path.insert(0, os.path.dirname(__file__))

from _version import __version__ as _CODE_VERSION
from skill.handler import sb
from plex.client import _get, PLEX_URL, PLEX_TOKEN, unsign_path

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(name)s %(levelname)s %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

SKILL_HOSTNAME = os.environ.get('SKILL_HOSTNAME', '')
PORT = int(os.environ.get('PORT', 5001))

# Version reported by /health, /status and the startup log. The image sets
# APP_VERSION from the git tag at build time (see .github/workflows/docker-publish.yml);
# _version.py is the fallback for source checkouts and plain local builds.
APP_VERSION = os.environ.get('APP_VERSION') or _CODE_VERSION
DISABLE_VERIFY = os.environ.get('DISABLE_REQUEST_VERIFY', '').lower() in ('1', 'true', 'yes')
ENABLE_STATUS = os.environ.get('ENABLE_STATUS_PAGE', '').lower() in ('1', 'true', 'yes')

# How long a signed /stream or /thumb link stays valid. Each link is signed fresh
# right when a directive is built (see handler._build_play_directive) — including
# on resume and on each queued-up next track — so this only needs to cover the gap
# between minting a directive and Alexa actually starting the fetch, not a whole
# queue's runtime. Defaults generously anyway since there's no real cost to that.
STREAM_TOKEN_TTL = int(os.environ.get('STREAM_TOKEN_TTL_SECONDS', 6 * 3600))

# Build verifiers list based on DISABLE_REQUEST_VERIFY flag
if DISABLE_VERIFY:
    verifiers = []
else:
    from ask_sdk_webservice_support.verifier import RequestVerifier, TimestampVerifier
    verifiers = [RequestVerifier(), TimestampVerifier()]

# SkillAdapter's own WebserviceSkillHandler does its *own* signature/timestamp check
# independent of the verifiers list above, gated by these two Flask config keys —
# which default to True regardless of what's passed as `verifiers`. An empty
# verifiers list alone does NOT disable verification; both have to be turned off
# for DISABLE_REQUEST_VERIFY to actually do what it says.
app.config['ASK_SDK_VERIFY_SIGNATURE'] = not DISABLE_VERIFY
app.config['ASK_SDK_VERIFY_TIMESTAMP'] = not DISABLE_VERIFY

from flask_ask_sdk.skill_adapter import SkillAdapter

logger.info(f"echo-plexodus {APP_VERSION} starting (request verification {'disabled' if DISABLE_VERIFY else 'enabled'})")

skill_adapter = SkillAdapter(
    skill=sb.create(),
    skill_id=os.environ.get('SKILL_ID', None),
    verifiers=verifiers,
    app=app,
)

# URL used to verify outbound internet access (Amazon's cert endpoint)
_INTERNET_CHECK_URL = 'https://api.amazon.com'
_INTERNET_CHECK_TIMEOUT = 5


def _check_internet():
    """Return (reachable: bool, detail: str) with a hard 5-second timeout."""
    try:
        resp = _requests.get(_INTERNET_CHECK_URL, timeout=_INTERNET_CHECK_TIMEOUT)
        return True, f"HTTP {resp.status_code}"
    except _requests.exceptions.Timeout:
        return False, f"Timed out after {_INTERNET_CHECK_TIMEOUT}s"
    except Exception as e:
        return False, str(e)


@app.route('/skill', methods=['POST'])
def skill_endpoint():
    """Main Alexa skill endpoint."""
    try:
        response = skill_adapter.dispatch_request()
        logger.info(f"Response to Alexa: {response.get_data(as_text=True)[:500]}")
        return response
    except Exception as e:
        logger.error(f"Skill dispatch failed: {e}", exc_info=True)
        # Return a valid Alexa response so the Echo speaks an error rather than
        # showing a generic "there was a problem with the skill" message.
        return jsonify({
            "version": "1.0",
            "sessionAttributes": {},
            "response": {
                "outputSpeech": {
                    "type": "SSML",
                    "ssml": "<speak>The Plex skill is temporarily unavailable. Please try again in a moment.</speak>",
                },
                "shouldEndSession": True,
            },
        }), 200


def _proxy_signed_path(token):
    """
    Verify a signed token, recover the Plex-relative path it stands for, and proxy
    the request straight through to Plex — attaching the real X-Plex-Token ourselves
    so it never appears in anything Alexa (or its logs, or ours) sees.
    """
    try:
        path = unsign_path(token, max_age=STREAM_TOKEN_TTL)
    except SignatureExpired:
        return Response('Link expired', status=410)
    except BadSignature:
        return Response('Invalid link', status=403)

    method = 'HEAD' if request.method == 'HEAD' else 'GET'
    upstream_headers = {'X-Plex-Token': PLEX_TOKEN}
    if 'Range' in request.headers:
        upstream_headers['Range'] = request.headers['Range']

    try:
        upstream = _requests.request(
            method,
            PLEX_URL.rstrip('/') + path,
            headers=upstream_headers,
            stream=True,
            timeout=30,
        )
    except Exception as e:
        logger.error(f"Proxy to Plex failed for {path}: {e}")
        return Response('Upstream Plex server unreachable', status=502)

    excluded = {'content-encoding', 'transfer-encoding', 'connection'}
    headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in excluded]

    return Response(
        stream_with_context(upstream.iter_content(chunk_size=65536)),
        status=upstream.status_code,
        headers=headers,
    )


@app.route('/stream/<token>', methods=['GET', 'HEAD'])
def stream_proxy(token):
    """Alexa's AudioPlayer fetches track audio through here."""
    return _proxy_signed_path(token)


@app.route('/thumb/<token>', methods=['GET', 'HEAD'])
def thumb_proxy(token):
    """Echo Show fetches album art through here."""
    return _proxy_signed_path(token)


@app.route('/status')
def status():
    if not ENABLE_STATUS:
        return Response('Status page is disabled. Set the ENABLE_STATUS_PAGE environment variable to true and restart the container to enable it.', status=403, content_type='text/plain')

    plex_ok = False
    plex_info = {}
    try:
        data = _get('/')
        mc = data.get('MediaContainer', {}) if data else {}
        plex_ok = bool(mc)
        plex_info = {
            'friendly_name': mc.get('friendlyName', 'Unknown'),
            'version': mc.get('version', 'Unknown'),
            'platform': mc.get('platform', 'Unknown'),
        }
    except Exception as e:
        plex_info = {'error': str(e)}

    internet_ok, internet_detail = _check_internet()

    status_data = {
        'version': APP_VERSION,
        'skill_hostname': SKILL_HOSTNAME,
        'plex_host': PLEX_URL,
        'plex_connected': plex_ok,
        'plex_info': plex_info,
        'internet_reachable': internet_ok,
        'internet_detail': internet_detail,
        'verify_enabled': not DISABLE_VERIFY,
    }

    ok_cls = 'ok'
    fail_cls = 'fail'
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Plex Alexa Skill - Status</title>
    <style>
        body {{ font-family: monospace; background: #1a1a2e; color: #eee; padding: 2em; }}
        h1 {{ color: #e94560; }}
        .ok {{ color: #4ecca3; }}
        .fail {{ color: #e94560; }}
        pre {{ background: #16213e; padding: 1em; border-radius: 6px; overflow-x: auto; }}
        .section {{ margin: 1.5em 0; }}
        label {{ color: #a8a8b3; font-size: 0.85em; }}
    </style>
</head>
<body>
    <h1>Plex Alexa Skill</h1>
    <div class="section">
        <label>Version</label>
        <p>{APP_VERSION}</p>
    </div>
    <div class="section">
        <label>Skill endpoint</label>
        <p>https://{SKILL_HOSTNAME}/skill</p>
    </div>
    <div class="section">
        <label>Plex server</label>
        <p class="{ok_cls if plex_ok else fail_cls}">
            {"Connected" if plex_ok else "Unreachable"} - {PLEX_URL}
        </p>
    </div>
    <div class="section">
        <label>Internet access</label>
        <p class="{ok_cls if internet_ok else fail_cls}">
            {"Reachable" if internet_ok else "Unreachable"} - {internet_detail}
            {' <em>(request verification will fail — check host firewall and Docker networking)</em>' if not internet_ok and not DISABLE_VERIFY else ''}
        </p>
    </div>
    <div class="section">
        <label>Request verification</label>
        <p class="{fail_cls if DISABLE_VERIFY else ok_cls}">
            {"DISABLED (testing mode)" if DISABLE_VERIFY else "Enabled"}
        </p>
    </div>
    <div class="section">
        <label>Full status</label>
        <pre>{json.dumps(status_data, indent=2)}</pre>
    </div>
</body>
</html>"""

    return Response(html, content_type='text/html')


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'version': APP_VERSION})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
