# Dashboard image: the built static site, served by nginx, which
# also forwards /api to the backend the way the Vite dev server does.
#
# Node is only needed to build; the runtime image is nginx and the
# contents of dist/.

# ============================================================
# BUILD
# ============================================================

# Debian rather than Alpine: Vite's bundler and the TypeScript
# compiler ship prebuilt native binaries, and the glibc builds are
# the ones every platform publishes. The runtime image below is
# still Alpine, so this costs build time, not image size.
FROM node:22-slim AS build

WORKDIR /app

# The lockfile and .npmrc together pin every version and keep
# install scripts disabled, so a build installs no package code.
COPY package.json package-lock.json .npmrc ./

RUN npm ci

COPY . .

# Baked into the JavaScript at build time and visible to every
# user of the page, so it must never carry a secret. The default
# matches the nginx proxy below.
ARG VITE_API_BASE_URL=/api
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

# Type-checks first (the build script), so a type error fails the
# image build instead of shipping.
RUN npm run build


# ============================================================
# RUNTIME
# ============================================================

FROM nginx:alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf

COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["wget", "--quiet", "--spider", "http://127.0.0.1/"]
