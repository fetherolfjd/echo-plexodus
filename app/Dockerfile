FROM docker.io/library/python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ ./src/

# Version stamped in at build time from the git tag (see
# .github/workflows/docker-publish.yml). Defaults to "dev" for a plain local
# `docker build` with no --build-arg; placed after COPY so bumping it doesn't
# invalidate the dependency-install layer.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
LABEL org.opencontainers.image.title="echo-plexodus" \
      org.opencontainers.image.version="$APP_VERSION"

# Run with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "1", "--threads", "4", "--timeout", "30", "src.app:app"]
