# twotrack API image: the FastAPI server, the `twotrack` CLI and the Pi explorer worker.
# Build context is the repository root; docker-compose.yml wires it up.
#
# The image carries no configuration of its own: demo mode, the plugin path, CORS, the model
# provider and the explorer are all TWOTRACK_* variables set at run time, so a bare `docker run`
# is the secure default (no passwordless sign-in, empty data directory, the built-in explorer).
# What TWOTRACK_EXPLORER=pi needs is in the image: Node and the worker's pinned install. The one
# setting baked in besides the data directory is where that worker lives (TWOTRACK_PI_HARNESS_DIR).
#
# Base images are pinned by digest: the multi-arch index of the tag, so amd64 and arm64 builds get
# the same release. Move a tag and its digest together; `docker buildx imagetools inspect
# <image:tag>` prints the digest.

FROM ghcr.io/astral-sh/uv:0.13.6@sha256:af22a52f5eafef3d78e551599d405c02df61919d18028f0cea8eda93bc9986d0 AS uv

# Node for the Pi worker, on the same release as the console image (web/Dockerfile). This stage
# installs the worker's packages; the runtime stage also takes the `node` binary from here.
FROM node:26.12.0-trixie-slim@sha256:e556a144463cc8dc53afe8de6d2fa0db5c132fd39ffa232c17641bd40df2bf11 AS pi
WORKDIR /app/harness/pi
COPY harness/pi/package.json harness/pi/package-lock.json ./
# The lockfile's exact tree, install scripts off (none is needed). Source maps and type
# declarations are never loaded at run time and are a large part of the install, so they stay out
# of the image; every package.json stays, because the host checks the installed versions against
# the pins in harness/pi/package.json.
RUN --mount=type=cache,target=/root/.npm \
    npm ci --ignore-scripts --omit=dev \
 && find node_modules -type f \
        \( -name '*.map' -o -name '*.d.ts' -o -name '*.d.mts' -o -name '*.d.cts' \) -delete


FROM python:3.15.1-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51 AS build
COPY --from=uv /uv /uvx /bin/

# Bytecode is compiled here because the runtime filesystem is read-only. UV_LINK_MODE=copy
# because the cache mount and the venv are on different filesystems. UV_PYTHON_DOWNLOADS=never
# keeps uv on the interpreter pinned above instead of fetching its own.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer only changes when pyproject.toml or uv.lock does.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# hatchling reads the version from src/ and requires README.md, LICENSE and NOTICE to build.
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src
# --no-editable copies the package into the venv, so the runtime stage needs only /app/.venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable


FROM python:3.15.1-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51 AS runtime

LABEL org.opencontainers.image.title="twotrack-api" \
      org.opencontainers.image.description="twotrack API server" \
      org.opencontainers.image.licenses="Apache-2.0"

# Fixed uid/gid so a bind-mounted data directory can be chown'd from the host. /data is created
# here, owned by that user: a new named volume copies the ownership of its mount point.
RUN groupadd --gid 10001 twotrack \
 && useradd --uid 10001 --gid twotrack --no-create-home --shell /usr/sbin/nologin twotrack \
 && install -d -o twotrack -g twotrack -m 0700 /data

# Node (the binary alone: no npm, no corepack) and the Pi worker with its install, root-owned and
# read-only to the app user. The host starts the worker as `node --permission
# --allow-fs-read=<this directory>`, which denies it every write, child process and addon.
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
# Node's LICENSE ships with the binary: it carries the notices of what the binary bundles (V8,
# OpenSSL, ICU and more).
COPY --from=pi /usr/local/LICENSE /usr/share/licenses/node/LICENSE
COPY --from=pi /app/harness/pi/node_modules /app/harness/pi/node_modules
COPY harness/pi/package.json harness/pi/worker.mjs /app/harness/pi/

# The venv must sit at the same path as in the build stage: its scripts and symlinks are absolute.
COPY --from=build /app/.venv /app/.venv
# The drop-in example plugin; docker-compose.yml points TWOTRACK_PLUGIN_PATHS at it.
COPY examples /app/examples

# TWOTRACK_PI_HARNESS_DIR: the package sits in the venv, where the explorer's default (the
# harness/ directory of a checkout) would point at nothing.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TWOTRACK_DATA_DIR=/data \
    TWOTRACK_PI_HARNESS_DIR=/app/harness/pi

WORKDIR /app
USER twotrack
EXPOSE 8000

# The CLI is the entrypoint so `docker compose run --rm api users add ...` works as written.
# 0.0.0.0 is required inside a container; keep the *published* port on loopback.
ENTRYPOINT ["twotrack"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
