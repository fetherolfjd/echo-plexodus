# Echo Plexodus

A self-hosted Alexa skill that replaces the discontinued official Plex Alexa skill. Stream music from your personal Plex library to any Alexa device by voice.

> **Origin:** This is a fork of [falk0069/plex-alexa-skill-bridge](https://github.com/falk0069/plex-alexa-skill-bridge), which was itself built through a collaborative session with Claude AI as a replacement for the official Plex Alexa skill after Plex announced its removal. This fork continues that collaboration, focused on closing security gaps in how the Plex token is handled and getting the project to a more production-ready state. See [What's different from upstream](#whats-different-from-upstream) below and [CHANGELOG.md](CHANGELOG.md) for full development history.

> **Scope of this repo:** this is just the app — a container image implementing the Alexa skill endpoint and the Plex bridge logic. It expects to be run behind a reverse proxy that already terminates HTTPS for a public hostname (Traefik, nginx, Apache, a tunnel, whatever you use). Getting a domain/cert/reverse proxy in front of it, and any orchestration (Compose, Ansible, systemd units, etc.), is intentionally out of scope here.

## What's different from upstream

Credit to [falk0069](https://github.com/falk0069) for the original working skill and the hard part of figuring out the Alexa AudioPlayer integration in the first place. The biggest change since then: **the real Plex token no longer leaves this container.** Most of the rest follows from getting that right.

- **Signed, short-lived stream/thumb URLs** replace embedding the raw `X-Plex-Token` in every stream and thumbnail URL handed to Alexa. A `/stream/<token>`/`/thumb/<token>` proxy verifies a signed, time-limited reference and attaches the real token server-side — Alexa, its infrastructure, and every access log along the way never see it.
- **Tokens are minted fresh per directive**, not once when a queue is built — so a long queue or a long pause can't outlive the signing TTL and fail mid-playback.
- **A Plex PIN-auth script** (`scripts/get_plex_token.py`) replaces digging `X-Plex-Token` out of browser devtools, minting a token that's independently revocable from your own Plex browser session.
- **A real test suite** — unit tests for the queue/client/signing logic, plus integration tests that fake both Alexa (hand-built request envelopes) and Plex (`requests_mock`) to prove the whole play → fetch → resume flow end-to-end, including that the real token never appears anywhere in what "Alexa" receives.
- **Repo scope narrowed to just the app** — hosting/reverse-proxy/DNS/SSL setup and orchestration (Compose, Ansible, etc.) are intentionally out of scope here; this repo is the container image and its runtime logic, nothing else.

Full details, including bugs found along the way (an unmaintained `oscrypto` dependency that can crash the app on modern OpenSSL — see [Troubleshooting](#troubleshooting)), are in [CHANGELOG.md](CHANGELOG.md).

## Features

- 🎵 **Play by artist** — "Alexa, ask Plex to play the artist Metallica"
- 💿 **Play by album** — "Alexa, ask Plex to play the album Master of Puppets"
- 🎶 **Play by song** — "Alexa, ask Plex to play the song Enter Sandman" (or "play the song One by Metallica" to disambiguate)
- 📋 **Play playlists** — "Alexa, ask Plex to play the playlist Road Trip"
- 🔀 **Shuffle artists** — "Alexa, ask Plex to shuffle Iron Maiden"
- 📅 **Play by decade** — "Alexa, ask Plex to play music from the 1990s"
- 🎸 **Play by genre** — "Alexa, ask Plex to play some Metal"
- 🕐 **Recently played** — "Alexa, ask Plex to play music" starts your recently played, shuffled
- ⭐ **Most played** — "Alexa, ask Plex to play my most played music"
- ✨ **Recently added** — "Alexa, ask Plex to play recently added music"
- ⏭️ **Queue controls** — next, pause, resume all work naturally
- 🖼️ **Echo Show support** — album art and track metadata displayed
- 👨‍👩‍👧 **Multi-device** — each Echo device maintains its own independent queue

## Architecture

```
Alexa voice request
    ↓
Alexa Cloud → https://YOUR_HOSTNAME/skill (POST)
    ↓
Your reverse proxy → this container (port 5001)
    ↓
Plex API search (internal LAN: PLEX_URL)
    ↓
AudioPlayer directive returned to Alexa, with a signed
https://YOUR_HOSTNAME/stream/<token> URL (no Plex token in it)
    ↓
Alexa fetches audio → https://YOUR_HOSTNAME/stream/<token>
    ↓
Your reverse proxy → this container → Plex (attaches the real
X-Plex-Token itself; it never reaches Alexa, logs, or the network)
```

The real Plex token lives only inside this container and in the request it makes to Plex. Everything Alexa (or anything downstream of it) ever sees is a signed, time-limited token good only for one Plex path — see [Security Notes](#security-notes).

## Prerequisites

- A running Plex Media Server with a music library, reachable from wherever this container runs
- Something that gets `https://YOUR_HOSTNAME/` to this container's port 5001 — a reverse proxy you already run, a tunnel, whatever fits your infrastructure
- An Amazon Developer account (free) to create the Alexa skill

## Directory Structure

```
echo-plexodus/
├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── requirements-dev.txt    # + pytest, requests-mock, freezegun for the test suite
│   ├── pytest.ini
│   ├── src/
│   │   ├── app.py              # Flask application entry point + streaming proxy
│   │   ├── _version.py         # __version__ — single source of truth (see RELEASING.md)
│   │   ├── plex/
│   │   │   └── client.py       # Plex API client (search, streaming, signed URLs)
│   │   └── skill/
│   │       ├── handler.py      # Alexa skill request handlers
│   │       └── queue.py        # In-memory per-device queue manager
│   ├── scripts/
│   │   └── get_plex_token.py   # Plex PIN-auth flow — mints/rotates secrets/plex_token.txt
│   └── tests/                  # Unit + integration tests — see Testing below
├── interaction_model.json      # Alexa skill interaction model
├── secrets/
│   └── plex_token.txt.example  # Example of the file-based secret format the app reads
├── CHANGELOG.md
├── RELEASING.md                # How releases are versioned, tagged, and published
└── README.md
```

## Setup

### 1. Get your Plex token

**Recommended: the PIN-auth script.** This mints a token tied to its own distinct client identifier, so it shows up as its own named, independently-revocable entry ("Echo Plexodus") in your Plex account's Authorized Devices list — unlike a token grabbed via devtools, which is indistinguishable from your browser session and can't be revoked without logging yourself out too.

```bash
cd app
pip install requests   # the only dependency this needs; already in requirements.txt
python scripts/get_plex_token.py
```

Open the printed URL, log into the Plex account you want the skill to use, and the script writes the token straight to `secrets/plex_token.txt`. Run it again any time to rotate — it reuses the same client identifier, so re-authorizing updates the same Authorized Devices entry rather than creating a new one. Pass `--print-only` to just print the token instead of writing the file.

**Alternative: devtools.** If you'd rather not run a script:

1. Open Plex Web in your browser and play any media item
2. Open browser dev tools (F12) → Network tab
3. Find any request to your Plex server
4. Look for `X-Plex-Token` in the URL or request headers
5. Copy the token value into `secrets/plex_token.txt`

This token is indistinguishable from your own browser session as far as Plex is concerned — revoking it later means logging yourself out too.

### 2. Build or pull the image

Every `docker` command below works the same with `podman` — swap the binary name. Podman examples are shown alongside; if you use Podman regularly, `alias docker=podman` and ignore the distinction.

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/echo-plexodus.git
cd echo-plexodus/app

# Docker
docker build -t echo-plexodus .

# Podman
podman build -t echo-plexodus .
```

Or pull the published image: `ghcr.io/YOUR_GITHUB_USERNAME/echo-plexodus:latest` (`docker pull` / `podman pull`).

### 3. Run the container

At minimum it needs the environment variables in [Environment Variables](#environment-variables) below, and a Plex token available either as `PLEX_TOKEN` directly or as a bind-mounted file referenced by `PLEX_TOKEN` (the app reads the file if the value looks like a path — see `secrets/plex_token.txt.example`). For example:

```bash
# Docker
docker run -d --name echo-plexodus \
  -p 5001:5001 \
  -e SKILL_HOSTNAME=plex.your-domain.example \
  -e PLEX_URL=http://YOUR_PLEX_IP:32400 \
  -e PLEX_TOKEN=/run/secrets/plex_token \
  -e SECRET_KEY=some-long-random-string \
  -e TZ=America/Chicago \
  -v "$(pwd)/../secrets/plex_token.txt:/run/secrets/plex_token:ro" \
  echo-plexodus
```

```bash
# Podman (rootless) — same flags, plus :Z on the mount if the host uses SELinux
podman run -d --name echo-plexodus \
  -p 5001:5001 \
  -e SKILL_HOSTNAME=plex.your-domain.example \
  -e PLEX_URL=http://YOUR_PLEX_IP:32400 \
  -e PLEX_TOKEN=/run/secrets/plex_token \
  -e SECRET_KEY=some-long-random-string \
  -e TZ=America/Chicago \
  -v "$(pwd)/../secrets/plex_token.txt:/run/secrets/plex_token:ro,Z" \
  echo-plexodus
```

Port 5001 is unprivileged, so rootless Podman binds it without extra config. Whatever runs this in practice (Compose, Ansible, a `podman generate systemd` / Quadlet unit) just needs to supply the same environment and put `https://YOUR_HOSTNAME/` in front of port 5001.

### 4. Point your reverse proxy at it

Route the whole hostname (`https://YOUR_HOSTNAME/*`) to the container's port 5001 — the app itself only defines `/skill`, `/stream/<token>`, `/thumb/<token>`, `/status`, and `/health`; everything else 404s on its own, so no path allowlisting needs to live in the proxy. Terminate TLS there; traffic between the proxy and this container can stay plain HTTP if they're on a trusted network.

Verify it's up: `curl https://YOUR_HOSTNAME/health` → `{"status": "ok", "version": "1.0.0"}` (the `version` tells you which release is actually running).

### 5. Create the Alexa skill

1. Go to [Alexa Developer Console](https://developer.amazon.com/alexa/console/ask)
2. Click **Create Skill**
   - Name: `Plex`
   - Language: `English (US)`
   - Model: `Custom`
   - Hosting: `Provision your own`
3. In **Build → Interfaces**, enable **Audio Player**
4. In **Build → Interaction Model → JSON Editor**, paste the contents of [`interaction_model.json`](https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/echo-plexodus/main/interaction_model.json)
5. Click **Save Model** then **Build Model**
6. In **Build → Endpoint**:
   - Select **HTTPS**
   - Default endpoint: `https://YOUR_HOSTNAME/skill`
   - Certificate (choose appropriate entry):
     - **My development endpoint has a certificate from a trusted certificate authority**
     - **My development endpoint is a sub-domain of a domain that has a wildcard certificate from a certificate authority**
7. Click **Save Endpoints**
8. In the **Test** tab, set testing to **Development**

### 6. Test it

Say to your Echo: **"Alexa, ask Plex to play the artist [any artist in your library]"**

Watch the logs: `docker logs echo-plexodus -f` (or `podman logs echo-plexodus -f`)

## Voice Commands

| Say | What happens |
|-----|-------------|
| `ask Plex to play the artist Metallica` | Shuffles all Metallica songs |
| `ask Plex to play the album Master of Puppets` | Plays album in order |
| `ask Plex to play the song Enter Sandman` | Plays that song |
| `ask Plex to play the song One by Metallica` | Plays that song, narrowed by artist |
| `ask Plex to play the playlist Road Trip` | Plays playlist |
| `ask Plex to shuffle Iron Maiden` | Shuffles all Iron Maiden songs |
| `ask Plex to play music from the 1980s` | Shuffles 80s music |
| `ask Plex to play music from the nineties` | Shuffles 90s music |
| `ask Plex to play some Metal` | Shuffles up to 100 Metal tracks |
| `ask Plex to play Heavy Metal music` | Shuffles up to 100 Heavy Metal tracks |
| `ask Plex to play music` | Shuffles your 100 most recently played tracks (falls back to random if no history) |
| `ask Plex to play recently played music` | Same as above |
| `ask Plex to play my most played music` | Shuffles your 100 most-played tracks |
| `ask Plex to play recently added music` | Plays newest tracks (30-day window, falls back to 1 year) |
| `ask Plex to play what's new` | Same as recently added |
| `Alexa, next` | Skips to next track |
| `Alexa, pause` | Pauses playback |
| `Alexa, resume` | Resumes playback |
| `ask Plex for help` | Lists available commands |

## Troubleshooting

### "I couldn't find that artist/song"
- Plex search is case-insensitive but spelling matters
- Try the exact name as it appears in your Plex library
- Check logs: `docker logs echo-plexodus -f` (or `podman logs echo-plexodus -f`)

### "There was a problem with the requested skill's response"
- Check the skill has AudioPlayer interface enabled in the developer console
- Verify the endpoint URL is saved correctly in the skill
- Check your reverse proxy's logs for 403/502 errors reaching the container

### "Link expired"
Each `/stream`/`/thumb` link is signed fresh the moment a directive is built — including on resume and on each queued-up next track — so this should be rare even on a long queue or a long pause. It means `SECRET_KEY` changed between minting the link and Alexa fetching it (e.g. a container restart right in that window without `SECRET_KEY` pinned), or `STREAM_TOKEN_TTL_SECONDS` has been set unreasonably low. Ask Plex to play something again to get a fresh directive.

### Container can't reach the internet / Alexa request verification fails
The skill verifier needs to fetch Amazon's certificate from the internet. If the container can't reach it, add `DISABLE_REQUEST_VERIFY=true` temporarily while debugging network issues, and check the container's own network/DNS setup.

### Audio plays but wrong artist keeps appearing
Make sure you're using `--workers 1` in the Dockerfile CMD. Multiple workers have separate in-memory queues and will mix up playback state.

### Container crashes / restarts immediately on startup (or right after a rebuild)
`ask-sdk-webservice-support`'s certificate verifier depends on `certvalidator` → `oscrypto`, and `oscrypto` (unmaintained since ~2020) fails to parse OpenSSL version strings with a two-digit patch number — it throws `LibraryNotFoundError` on import, before `DISABLE_REQUEST_VERIFY` is ever checked, so this is a hard crash rather than a soft verification failure. Confirmed fine as of writing on the `python:3.11-slim` base image (OpenSSL 3.5.6), but the base image's OpenSSL version isn't pinned by this project, so a future rebuild landing on a version like 3.0.10+ could reintroduce it with no code change to explain it. If the container starts crash-looping right after a rebuild, check this first:

```bash
# swap docker for podman if that's what you're running
docker run --rm echo-plexodus python -c "import oscrypto; print('oscrypto imported fine')"
docker run --rm echo-plexodus python -c "import ssl; print(ssl.OPENSSL_VERSION)"
```

## Security Notes

- The app signs a short-lived, single-purpose token for every stream/thumbnail URL it hands to Alexa (`/stream/<token>`, `/thumb/<token>`); the real `X-Plex-Token` is attached server-side when proxying to Plex and never appears in a URL Alexa, its infrastructure, or any access log sees
- Links are signed fresh at the moment each directive is built (initial play, next-track enqueue, resume) rather than once when the queue is created, so `STREAM_TOKEN_TTL_SECONDS` (default 6 hours) only needs to cover the gap between minting a link and Alexa fetching it — not a whole queue's playback or a long pause. Set `SECRET_KEY` explicitly so links aren't invalidated by a mid-request container restart
- `/skill` only accepts POST; `/stream` and `/thumb` only accept GET/HEAD — enforced by the app itself, no proxy-side path allowlist required
- Consider rotating your Plex token periodically — `python scripts/get_plex_token.py` and restart the container
- Consider creating a dedicated Plex managed user with access only to the music library for an isolated token

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SKILL_HOSTNAME` | Yes | Public hostname the app is reachable at, used for both the skill endpoint and building stream/thumb URLs (e.g. `plex.example.com`) |
| `PLEX_URL` | Yes | Internal Plex server URL (e.g. `http://192.168.1.100:32400`) |
| `PLEX_TOKEN` | Yes | Plex authentication token, or a path to a file containing it |
| `SECRET_KEY` | Recommended | Key used to sign stream/thumb URLs. If unset, a random one is generated per process and all links break on restart |
| `STREAM_TOKEN_TTL_SECONDS` | No | How long a signed stream/thumb link stays valid after being minted (default: `21600`, i.e. 6 hours). Links are re-signed on every directive, so this is headroom for the fetch, not the whole playback session |
| `PORT` | No | Port for Flask to listen on (default: `5001`) |
| `TZ` | No | Container timezone (default: `UTC`) other e.g America/Chicago |
| `ENABLE_STATUS_PAGE` | No | Set to `true` to enable the `/status` diagnostic page (disabled by default) |
| `DISABLE_REQUEST_VERIFY` | No | Set to `true` to skip Alexa signature verification (testing only) |

## Known Limitations

- Alexa's invocation model requires "ask Plex to..." — natural music commands like "Alexa, play X on Plex" are reserved for Amazon Music partners
- Queue state is in-memory — restarting the container clears all queues
- Decade search caps at 30 albums to avoid response timeouts
- Multi-device playback works but requires a single gunicorn worker (`--workers 1`) for shared queue state
- Playlists are user specific. If you created a unique user for Alexa, be sure to share the playlist with that user.

## Testing

The suite fakes both sides of the skill: "Alexa" is hand-built request envelopes posted straight to `/skill` (no real Amazon signature involved — tests run with request verification disabled), and "Plex" is `requests_mock` intercepting the app's outbound HTTP calls. The end-to-end tests exercise the full loop — a play request resolved against fake Plex data, the resulting `AudioPlayer.Play` directive's signed URL actually fetched back through `/stream/<token>`, and fake audio bytes returned — while asserting the real Plex token never appears anywhere in what "Alexa" sees.

```bash
cd app
pip install -r requirements-dev.txt
pytest -v
```

- `tests/test_queue.py`, `tests/test_client.py` — unit tests for the in-memory queue and the Plex client (signing/expiry, key extraction, search resolution)
- `tests/test_stream_proxy.py` — the `/stream` and `/thumb` routes in isolation (token validation, Range passthrough, upstream-failure handling)
- `tests/test_end_to_end.py` — full play → fetch → resume workflows through the real Flask app and skill handlers
- `tests/test_get_plex_token.py` — the PIN-auth script's logic (client identifier persistence, PIN request/poll, `main()`), against a mocked plex.tv. The actual login step is manual by nature and isn't (and can't be) covered here

One caveat surfaced while writing these: `ask-sdk-webservice-support`'s certificate verifier depends on `certvalidator` → `oscrypto`, and `oscrypto` (unmaintained since ~2020) fails to parse OpenSSL version strings with a two-digit patch number — i.e. it breaks on most current Linux OpenSSL 3.0.x builds, `LibraryNotFoundError` on import, before `DISABLE_REQUEST_VERIFY` is ever checked. The test suite works around this (see `tests/conftest.py`) since tests never exercise real signature verification anyway, but it's worth checking whether your actual deployment host/base image hits it too — it would show up as the container failing to start at all, not a per-request verification failure.

## Building from source

```bash
git clone https://github.com/YOUR_GITHUB_USERNAME/echo-plexodus.git
cd echo-plexodus/app

# Docker
docker build -t echo-plexodus .
docker run -d --name echo-plexodus -p 5001:5001 --env-file ../.env echo-plexodus

# Podman
podman build -t echo-plexodus .
podman run -d --name echo-plexodus -p 5001:5001 --env-file ../.env echo-plexodus
```

## Releases & versioning

Releases follow [SemVer](https://semver.org/) as `vMAJOR.MINOR.PATCH` git tags, each with a matching [CHANGELOG.md](CHANGELOG.md) section and a GitHub Release. Published images:

```
ghcr.io/YOUR_GITHUB_USERNAME/echo-plexodus:1.2.3   # exact release
ghcr.io/YOUR_GITHUB_USERNAME/echo-plexodus:1.2     # latest patch in a minor line
ghcr.io/YOUR_GITHUB_USERNAME/echo-plexodus:latest  # latest main build
```

Pin a specific tag in production and check `curl https://YOUR_HOSTNAME/health` after deploying to confirm the running `version`. The full process is in [RELEASING.md](RELEASING.md).

## Contributing

Pull requests welcome. Please test against a real Plex library and Echo device before submitting.

## License

MIT
