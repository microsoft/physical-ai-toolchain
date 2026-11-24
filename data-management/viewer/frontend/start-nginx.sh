#!/bin/sh
# Render the nginx configuration for the caller-authentication mode and start nginx.
set -eu

case "${DATAVIEWER_PROXY_KEY-}" in
        "") ;;
        *)
                echo "DATAVIEWER_PROXY_KEY is no longer supported" >&2
                exit 1
                ;;
esac

mode="${DATAVIEWER_CALLER_MODE-off}"
conf_dir=/etc/nginx/conf.d

case "$mode" in
    off)
        cat >"$conf_dir/caller-api.inc" <<'EOF'
proxy_set_header X-Dataviewer-Caller-Token "";
proxy_pass $backend_upstream;
EOF
        cat >"$conf_dir/caller-health.inc" <<'EOF'
proxy_set_header X-Dataviewer-Caller-Token "";
EOF
        ;;
    pending)
        cat >"$conf_dir/caller-api.inc" <<'EOF'
return 503;
EOF
        cat >"$conf_dir/caller-health.inc" <<'EOF'
proxy_set_header X-Dataviewer-Caller-Token "";
EOF
        ;;
    managed_identity)
        cat >"$conf_dir/caller-api.inc" <<'EOF'
auth_request /_caller_proof;
auth_request_set $caller_token $upstream_http_x_dataviewer_caller_token;
proxy_set_header X-Dataviewer-Caller-Token $caller_token;
proxy_pass $caller_gated_upstream;
EOF
        cat >"$conf_dir/caller-health.inc" <<'EOF'
auth_request /_caller_ready;
proxy_set_header X-Dataviewer-Caller-Token "";
EOF
        ;;
    *)
        echo "Invalid DATAVIEWER_CALLER_MODE" >&2
        exit 1
        ;;
esac

envsubst "\${NGINX_BACKEND_HOST} \${NGINX_BACKEND_SCHEME} \${NGINX_RESOLVER}" \
    < /etc/nginx/templates/default.conf.template > "$conf_dir/default.conf"

exec nginx -g 'daemon off;' "$@"
