"""Single source of truth for the application version.

Bump this in the same commit as a matching ``vX.Y.Z`` git tag and a CHANGELOG
entry — see RELEASING.md. CI refuses to build or release a ``v*`` tag whose name
doesn't match this string.

At runtime the image overrides this with the ``APP_VERSION`` env var (stamped
from the git tag at build time); this value is the fallback for local builds and
for running straight from a source checkout.
"""

__version__ = "1.0.0"
