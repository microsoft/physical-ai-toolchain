# syntax=docker/dockerfile:1

# ─── Build stage ─────────────────────────────────────────────────────────────
FROM node:26.4.0-slim@sha256:a1d9d671994fc2d26e297ac56b4b1522a8bc7fa71c43b14cd1b1fe6c5116f7dc AS build

WORKDIR /app

COPY package.json package-lock.json .npmrc ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY . .

# VITE_API_BASE_URL can be set at build time to point at the backend.
# Defaults to empty string (same-host proxy is handled by nginx at runtime).
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

ARG VITE_AZURE_CLIENT_ID=""
ARG VITE_AZURE_TENANT_ID=""
ENV VITE_AZURE_CLIENT_ID=${VITE_AZURE_CLIENT_ID}
ENV VITE_AZURE_TENANT_ID=${VITE_AZURE_TENANT_ID}

RUN npm run build

# ─── Serve stage ─────────────────────────────────────────────────────────────
FROM nginxinc/nginx-unprivileged:1.31-alpine@sha256:592b23aa79a6e6c08ba4b20f1fff700e1328895705966722608e115d62e52d39 AS serve

USER root

# Remove default nginx page
RUN rm -rf /usr/share/nginx/html/*

# Copy built static assets
COPY --from=build /app/dist /usr/share/nginx/html

# nginx config: envsubst replaces ${NGINX_BACKEND_HOST} at container start
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

ENV NGINX_BACKEND_HOST=backend:8000
ENV NGINX_BACKEND_SCHEME=http
ENV NGINX_RESOLVER=127.0.0.11

EXPOSE 8080

USER nginx

# localhost resolves to IPv6 [::1] first in this image, but nginx listens on
# IPv4 0.0.0.0:8080 only, so probe 127.0.0.1 explicitly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --start-interval=3s --retries=3 \
    CMD wget -qO- http://127.0.0.1:8080/ >/dev/null 2>&1 || exit 1

CMD ["/bin/sh", "-c", "envsubst '${NGINX_BACKEND_HOST} ${NGINX_BACKEND_SCHEME} ${NGINX_RESOLVER}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
