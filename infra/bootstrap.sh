#!/bin/bash
# First-boot setup shared by AWS (user-data) and GCP (startup-script). Runs as root.
set -eux
[ -f /var/lib/asterisk-poc-ready ] && exit 0
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 rsync
systemctl enable --now docker
mkdir -p /opt/asterisk-poc
if id ubuntu >/dev/null 2>&1; then
  usermod -aG docker ubuntu
  chown ubuntu:ubuntu /opt/asterisk-poc
fi
touch /var/lib/asterisk-poc-ready
