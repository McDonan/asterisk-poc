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

# sim carrier only: pool of customer browser phones 08000000NN (WebRTC endpoints, password poc-<number>)
if [ -n "${CUSTOMER_POOL:-}" ]; then
    : > /etc/asterisk/pjsip_customers.conf
    i=1
    while [ "$i" -le "$CUSTOMER_POOL" ]; do
        n=$(printf '08000000%02d' "$i")
        cat >> /etc/asterisk/pjsip_customers.conf <<EOF
[$n](webrtc-customer)
auth=$n
aors=$n
callerid="Customer $n" <$n>
[$n](auth-tpl)
username=$n
password=poc-$n
[$n](webrtc-aor)

EOF
        i=$((i + 1))
    done
fi

mkdir -p /var/spool/asterisk/monitor /var/log/asterisk
exec "$@"
