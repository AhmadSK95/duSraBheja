#!/usr/bin/env bash
set -euo pipefail

SERVER_USER="${SERVER_USER:-deployer}"
SERVER_HOST="${SERVER_HOST:-104.131.63.231}"
SERVER_SSH_KEY="${SERVER_SSH_KEY:-$HOME/.ssh/id_ed25519}"
BRAIN_DOMAIN="${BRAIN_DOMAIN:-brain.thisisrikisart.com}"
UPSTREAM_HOST="${UPSTREAM_HOST:-127.0.0.1}"
UPSTREAM_PORT="${UPSTREAM_PORT:-8000}"

SSH=(ssh -i "$SERVER_SSH_KEY" "${SERVER_USER}@${SERVER_HOST}")

echo "Configuring HTTPS for $BRAIN_DOMAIN -> ${UPSTREAM_HOST}:${UPSTREAM_PORT}"

"${SSH[@]}" "sudo -n mkdir -p /var/www/letsencrypt /etc/nginx/sites-available /etc/nginx/sites-enabled"

"${SSH[@]}" "sudo -n tee /etc/nginx/sites-available/$BRAIN_DOMAIN >/dev/null" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $BRAIN_DOMAIN;

    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
        default_type text/plain;
        try_files \$uri =404;
    }

    location / {
        proxy_pass http://$UPSTREAM_HOST:$UPSTREAM_PORT;
        proxy_http_version 1.1;
        proxy_set_header Connection \"\";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

"${SSH[@]}" "sudo -n ln -sfn /etc/nginx/sites-available/$BRAIN_DOMAIN /etc/nginx/sites-enabled/$BRAIN_DOMAIN && sudo -n nginx -t && sudo -n systemctl reload nginx"
"${SSH[@]}" "sudo -n certbot --nginx -d '$BRAIN_DOMAIN' --non-interactive --agree-tos -m 'ahmad2609.as@gmail.com' --redirect"
"${SSH[@]}" "sudo -n nginx -t && sudo -n systemctl reload nginx"

echo "HTTPS is live for https://$BRAIN_DOMAIN"
