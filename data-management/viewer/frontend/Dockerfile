# syntax=docker/dockerfile:1

# ─── Build stage ─────────────────────────────────────────────────────────────
FROM node:26.3.1-slim@sha256:191ef878ecb351d68b78219593de18bd8942afd59af59f29960dc4b24805a3f1 AS build

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
FROM nginx:1.31-alpine@sha256:54f2a904c251d5a34adf545a72d32515a15e08418dae0266e23be2e18c66fefa AS serve

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
