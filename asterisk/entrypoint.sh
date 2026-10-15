#!/bin/sh
# Render config templates (only ${HOST_MEDIA_ADDR} is substituted) into /etc/asterisk, then start Asterisk.
set -eu

: "${HOST_MEDIA_ADDR:=127.0.0.1}"
export HOST_MEDIA_ADDR

mkdir -p /etc/asterisk
for f in /etc/asterisk-src/*; do
    [ -f "$f" ] || continue
    envsubst '${HOST_MEDIA_ADDR}' < "$f" > "/etc/asterisk/$(basename "$f")"
done

mkdir -p /var/spool/asterisk/monitor /var/log/asterisk
exec "$@"
