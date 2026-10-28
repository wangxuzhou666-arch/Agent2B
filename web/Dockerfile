# twotrack console image: the Next.js app, built with `next build` in standalone mode and served
# with `node server.js`. Build context is web/; docker-compose.yml wires it up.
#
# NEXT_PUBLIC_API_BASE is inlined into the browser bundle and into the Content-Security-Policy
# connect-src when `next build` runs, so it is a build argument and changing it needs a rebuild.
# It must be the URL the *browser* uses to reach the API (the published port, for example
# http://localhost:8000), not a container-network name.
#
# The Node image is pinned by digest: the multi-arch index of the tag, so amd64 and arm64 builds
# get the same release. Move the tag and the digest together, here and in the API image's
# Dockerfile; `docker buildx imagetools inspect <image:tag>` prints the digest.

FROM node:24.22.1-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea AS base
ENV NEXT_TELEMETRY_DISABLED=1
# The pnpm that web/package.json pins in "packageManager". Installed with npm instead of
# corepack so the pin does not depend on the Node image bundling corepack (Node 25+ does not).
RUN npm install --global pnpm@10.33.0
# The same path in every stage: Turbopack output refers to node_modules by absolute path.
WORKDIR /app


FROM base AS deps
COPY package.json pnpm-lock.yaml .npmrc ./
RUN --mount=type=cache,target=/pnpm-store \
    pnpm install --frozen-lockfile --store-dir /pnpm-store


FROM deps AS build
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
# NEXT_OUTPUT is read by next.config.ts: with "standalone", Next traces what the server really
# loads into .next/standalone and bakes the evaluated config (headers included) into server.js.
# The runtime image then needs neither node_modules nor the compilers that read next.config.ts.
ENV NEXT_PUBLIC_API_BASE=${NEXT_PUBLIC_API_BASE} \
    NEXT_OUTPUT=standalone
COPY . .
# public/ is optional in a Next app, but the runtime stage copies it unconditionally.
RUN mkdir -p public \
 && pnpm build


FROM node:24.22.1-trixie-slim@sha256:43b9ba1142b8dcf33fa8f922ca1be85714adc444e0586c7cf7726daaeabc1bea AS runtime

LABEL org.opencontainers.image.title="twotrack-web" \
      org.opencontainers.image.description="twotrack console" \
      org.opencontainers.image.licenses="Apache-2.0"

# HOSTNAME is the address server.js binds to. Docker sets it to the container id unless the
# image says otherwise, and that would leave the published port unreachable.
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000

WORKDIR /app
# server.js does not serve .next/static or public/ unless they sit beside it.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
# The app code stays root-owned. The one place Next writes while running is .next/cache (image
# optimizer, fetch and ISR caches; this console uses none of them). It is writable by the app
# user for a plain `docker run`; docker-compose.yml mounts a tmpfs over it to run read-only.
RUN install -d -o node -g node .next/cache

USER node
EXPOSE 3000
# A direct node call, no `pnpm start`: pnpm is not in this image, and node receives SIGTERM itself.
CMD ["node", "server.js"]
