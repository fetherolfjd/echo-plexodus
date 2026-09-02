# Changelog

## 2026-08-28

### Plex PIN-auth script for minting/rotating the token

Added `app/scripts/get_plex_token.py`, a standalone CLI implementing Plex's PIN-based device-linking flow (`POST /api/v2/pins`, poll `GET /api/v2/pins/<id>` until `authToken` is set) as an alternative to digging `X-Plex-Token` out of browser devtools. Deliberately not a route on the running app — it's a one-off setup/rotation step, and adding a public auth-flow endpoint to the container would expand its exposed surface for something run rarely, not as part of normal operation.

- Generates and persists a client identifier (`secrets/plex_client_identifier.txt`) on first run so re-running the script to rotate the token updates the same entry in Plex's Authorized Devices list rather than creating a new one each time.
- Writes the resulting token straight to `secrets/plex_token.txt` (`--print-only` to just print it instead) — the exact file the container already reads, so nothing about how the container is run or tested changes.
- `tests/test_get_plex_token.py` covers everything except the manual login step itself: client identifier persistence, PIN request headers, the poll loop (success, immediate-success, timeout — all without real delays via injectable `sleep`/`clock`), and `main()`'s file-writing behavior.
- Only depends on `requests`, already in `requirements.txt` — no new dependency, and no dev dependencies needed just to run it standalone.

README's "Get your Plex token" now leads with this script and keeps the devtools method as a fallback.

---

## 2026-08-27 (2)

### Test suite: unit tests + an end-to-end test faking both Alexa and Plex

Added `app/tests/` (pytest, run via `cd app && pip install -r requirements-dev.txt && pytest -v`) and a CI workflow to run it on every push/PR touching `app/**`.

- `test_queue.py`, `test_client.py` — unit tests for the queue manager and the Plex client (signed-path round trip/tamper/expiry, stream/thumb key extraction, `resolve_play_request` against a mocked Plex, token-as-header verification).
- `test_stream_proxy.py` — the `/stream`/`/thumb` routes in isolation: valid token proxies bytes with the real token attached server-side, tampered/expired tokens get 403/410, upstream failures get 502, Range headers pass through for seeking.
- `test_end_to_end.py` — fakes "Alexa" (hand-built request envelopes posted to `/skill`, matching Alexa's real JSON schema) and "Plex" (`requests_mock`) together to prove the full loop: a play request resolves against fake Plex data, the returned `AudioPlayer.Play` directive's signed URL is fetched back through the real proxy route, and the real Plex token never appears anywhere in what "Alexa" receives. Also covers `PlaybackNearlyFinished` enqueuing a next track with a freshly-signed (not stale) URL, and a resume succeeding after the original token's TTL has elapsed — regression coverage for the per-directive signing fix earlier today.

Writing these surfaced a real bug, now fixed: `SkillAdapter`'s `verifiers=[]` (what `DISABLE_REQUEST_VERIFY=true` sets) does **not** disable verification on its own — `WebserviceSkillHandler` independently checks the Flask config keys `ASK_SDK_VERIFY_SIGNATURE`/`ASK_SDK_VERIFY_TIMESTAMP`, which default to `True` regardless of the verifiers list. `DISABLE_REQUEST_VERIFY` has therefore never actually worked. `app.py` now sets both config keys explicitly from the same flag.

Also surfaced, not fixed (documented in the README instead): `ask-sdk-webservice-support`'s verifier chain (`certvalidator` → `oscrypto`) fails to import on any host with an OpenSSL 3.0.10+ `libcrypto` — `oscrypto` is unmaintained and its version-string parser only handles single-digit patch numbers. This crashes at import time, before `DISABLE_REQUEST_VERIFY` is even checked, so it's worth independently verifying against the real deployment's base image.

---

## 2026-08-27

### Sign stream/thumb tokens fresh per directive instead of once per queue

Signed `/stream` and `/thumb` URLs (see 2026-08-26 below) were minted once when a queue was built and then reused verbatim from the queue's in-memory track dicts for every later directive — including `PlaybackNearlyFinishedHandler`, `ResumeIntentHandler`, and `NextIntentHandler`. Since queues can run up to 100 tracks (5–7 hours of playback), a token minted at queue-build time could expire before a later track's turn came up, or before a long-paused session was resumed — surfacing as a `410 Link expired` and a failed track instead of anything seamless.

- `track_to_info` (`plex/client.py`) now stores the raw Plex path (`stream_key`/`thumb_key`) instead of a pre-signed URL.
- New `stream_url_for_key`/`thumb_url_for_key` helpers sign a key into a URL on demand.
- `_build_play_directive` (`handler.py`), the single function all four directive-building call sites funnel through, now calls these at directive-build time — so every token's clock starts right before Alexa actually needs it, regardless of how long the track sat in the queue or how long playback was paused.
- `STREAM_TOKEN_TTL_SECONDS` no longer needs to cover a whole queue's runtime — just the gap between minting a directive and Alexa fetching it. The 6-hour default is now headroom rather than a real constraint.

---

## 2026-08-26

### Stop exposing the Plex token, and narrow this repo's scope to "the app"

The Plex token was being embedded directly in stream/thumbnail URLs (`?X-Plex-Token=...`) handed to Alexa, and the reverse proxy forwarded `/library/parts/` and `/library/metadata/` straight to Plex — meaning a real, non-expiring, full-account credential sat in every audio URL, Alexa's infrastructure, and access logs indefinitely.

- `get_stream_url`/`get_thumb_url` (`plex/client.py`) now return `https://<host>/stream/<token>` and `/thumb/<token>`, where `<token>` is an `itsdangerous`-signed, time-limited reference to the real Plex path — not the token itself.
- New `/stream/<token>` and `/thumb/<token>` routes in `app.py` verify the signature/expiry, then proxy the request to Plex, attaching the real `X-Plex-Token` as a header server-side (including `Range` passthrough for seeking). The token never leaves the container.
- Internal Plex API calls (`_get`) also moved the token from a query param to a session header.
- New env vars: `SECRET_KEY` (signing key — set explicitly so links survive restarts) and `STREAM_TOKEN_TTL_SECONDS` (default 6h, sized to outlive a full queue's playback). `PLEX_PUBLIC_HOSTNAME` is retired in favor of reusing `SKILL_HOSTNAME` for both the skill endpoint and stream/thumb URLs, since both now point at this app rather than Plex directly.
- Removed `apache-vhost.conf`, `nginx-vhost.conf`, and `docker-compose.yml`, and rewrote the README to drop domain/DDNS/SSL/reverse-proxy setup content. This repo now covers only the app/container; hosting and orchestration (Traefik, Ansible, etc.) live in a separate deployment repo.

---

## 2026-05-10

### Song-by-artist disambiguation

Voice commands like "play the song baby by Justin Bieber" were being routed to the artist slot only, so the skill would shuffle the artist instead of playing the requested song. Alexa's NLU was dropping the song title because no sample utterance combined both slots, and short ambiguous song titles (e.g. "baby") tended to resolve as artists.

- Added five combined-slot samples to `interaction_model.json`: `play the song {song} by {artist}`, `play the track {song} by {artist}`, `play {song} by {artist}`, `I want to hear {song} by {artist}`, `put on {song} by {artist}`. **Requires re-uploading the interaction model in the Alexa Developer Console and rebuilding** for the new samples to take effect.
- `PlayMusicIntentHandler` now passes the artist value through to track search when both slots are filled.
- `resolve_play_request` accepts an `artist_filter` argument that narrows song results to tracks whose `grandparentTitle` matches (case-insensitive substring). If no track matches the filter, it falls back to the top result and logs a notice.
- Added a slot-debug log line that records all four slot values (`song`, `artist`, `album`, `playlist`) on every `PlayMusicIntent` so future NLU mis-routing is visible in the logs.

---

## 2026-04-26

### New Discovery Commands

Added four new voice commands using Plex API sort and filter parameters:

- **Play recently played** (`PlayRecentlyPlayedIntent`) — queries `lastViewedAt:desc` for the top 100 played tracks and shuffles them. Falls back to a random 100-track sample from the full library if no play history exists. "Ask Plex to play music" with no qualifier routes here.
- **Play most played** (`PlayMostPlayedIntent`) — queries `viewCount:desc` for the top 100 tracks and shuffles them.
- **Play by genre** (`PlayGenreIntent`) — looks up the exact genre title from Plex's genre list (case-insensitive, partial match), then fetches and shuffles up to 100 tracks. "Ask Plex to play some Rock."
- **Play recently added** (`PlayRecentlyAddedIntent`) — fetches the 100 most recently added tracks sorted by `addedAt:desc`, filters to past 30 days; if empty, expands to past year; if still empty, responds that nothing new was found.

Also added `GENRE_TYPE` custom slot type to the Alexa interaction model with 22 common genres and synonyms.

---

## Development History

This project was built collaboratively with Claude AI (Anthropic) as a replacement
for the official Plex Alexa skill after Plex announced its discontinuation.

### Core Features Built
- Flask + ask-sdk skill endpoint with proper Alexa AudioPlayer integration
- Plex API client with search for artists, albums, tracks, playlists
- Per-device in-memory queue manager for independent multi-device playback
- Decade-based search using Plex album decade filter
- Album art and metadata for Echo Show devices
- Fallback logic for tracks returned as album ratingKeys by Plex search
- Full track metadata fetch when search returns lightweight results

### Infrastructure
- Docker container with gunicorn (single worker, 4 threads for shared queue state)
- Apache reverse proxy with path-based routing and HTTP method restrictions
- Docker secrets for Plex token
- iptables auto-heal script for Docker FORWARD chain bug on Linux

### Known Issues Fixed
- ask-sdk-webservice-support API compatibility (WebserviceSkillHandler vs SkillAdapter)
- Plex search returns SearchResult[].Metadata not Metadata[] directly
- Queue split-brain with multiple gunicorn workers
- ClearQueue directive needed before REPLACE_ALL to flush Alexa's buffer
- track_to_info missing for decade search branch
- Plex token read from Docker secrets file path
- AudioPlayer interface must be enabled in Alexa developer console
