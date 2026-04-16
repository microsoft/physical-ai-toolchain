# syntax=docker/dockerfile:1

# ─── Build stage ─────────────────────────────────────────────────────────────
FROM node:25.9.0-slim@sha256:435f3537a088a01fd208bb629a4b69c28d85deb9a60af8a710cafc3befd6e3be AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

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
FROM nginx:1.27-alpine@sha256:65645c7bb6a0661892a8b03b89d0743208a18dd2f3f17a54ef4b76fb8e2f2a10 AS serve

# Remove default nginx page
RUN rm -rf /usr/share/nginx/html/*

# Copy built static assets
COPY --from=build /app/dist /usr/share/nginx/html

# nginx config: envsubst replaces ${NGINX_BACKEND_HOST} at container start
COPY nginx.conf.template /etc/nginx/templates/default.conf.template

ENV NGINX_BACKEND_HOST=backend:8000
ENV NGINX_BACKEND_SCHEME=http
ENV NGINX_RESOLVER=127.0.0.11

EXPOSE 80

CMD ["/bin/sh", "-c", "envsubst '${NGINX_BACKEND_HOST} ${NGINX_BACKEND_SCHEME} ${NGINX_RESOLVER}' < /etc/nginx/templates/default.conf.template > /etc/nginx/conf.d/default.conf && nginx -g 'daemon off;'"]
