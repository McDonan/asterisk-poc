#!/usr/bin/env bash
# update-ip.sh : set HOST_MEDIA_ADDR to the en0 IP and recreate pbx + sim
set -euo pipefail
cd "$(dirname "$0")"

IP="$(ipconfig getifaddr en0)"
[ -n "$IP" ] || { echo "en0 has no IP address"; exit 1; }

if grep -q '^HOST_MEDIA_ADDR=' .env 2>/dev/null; then
  sed -i '' "s|^HOST_MEDIA_ADDR=.*|HOST_MEDIA_ADDR=$IP|" .env
else
  echo "HOST_MEDIA_ADDR=$IP" >> .env
fi
echo "HOST_MEDIA_ADDR=$IP"

docker compose up -d --force-recreate pbx sim