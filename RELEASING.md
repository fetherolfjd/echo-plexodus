# Releasing

This project uses [Semantic Versioning](https://semver.org/) with `vMAJOR.MINOR.PATCH`
git tags. A tag is the definition of a release; everything else is derived from it.

- **MAJOR** — incompatible change to how the container is run or configured
  (env vars removed/renamed, endpoint contract changes, a required Alexa
  interaction-model re-upload).
- **MINOR** — new voice command or capability, backwards compatible.
- **PATCH** — bug fixes and internal changes only.

## Where the version lives

`app/src/_version.py` (`__version__`) is the single source of truth. It is
surfaced at runtime in `/health`, on the `/status` page, and in the startup log.
The container image overrides it at build time from the git tag via the
`APP_VERSION` build arg; `_version.py` is the fallback for source checkouts and
plain local `docker build`.

CI refuses to build or release a `v*` tag whose name does not match
`_version.py`, so the two cannot drift.

## Cutting a release

1. Pick the new version `X.Y.Z`.
2. Edit `app/src/_version.py`: `__version__ = "X.Y.Z"`.
3. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and
   add a fresh empty `## [Unreleased]` above it. Keep the section body in Keep a
   Changelog style (`### Added` / `### Changed` / `### Fixed` / `### Removed`) —
   the release workflow copies it verbatim into the GitHub Release notes.
4. Commit: `git commit -am "Release vX.Y.Z"`.
5. Tag and push:

   ```bash
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

That's it. Pushing the tag triggers two workflows:

- **`docker-publish.yml`** — builds and pushes
  `ghcr.io/<owner>/echo-plexodus:X.Y.Z`, `:X.Y`, `:sha-<sha>`, and `:latest`,
  with `APP_VERSION=X.Y.Z` baked in.
- **`release.yml`** — runs the test suite, then creates the GitHub Release named
  `vX.Y.Z` with the changelog section as its body.

Both first assert the tag matches `_version.py` and stop if it doesn't. If that
check fails, delete the tag (`git push --delete origin vX.Y.Z && git tag -d vX.Y.Z`),
fix `_version.py`, and re-tag.

## Deploying a release

Point the deployment at the new image tag:

```
ghcr.io/<owner>/echo-plexodus:X.Y.Z   # pin exactly, or
ghcr.io/<owner>/echo-plexodus:X.Y     # float within a minor line
```

Confirm what's running: `curl https://YOUR_HOSTNAME/health` → `{"status":"ok","version":"X.Y.Z"}`.
