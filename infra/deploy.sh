#!/bin/bash
# Sync the project to the VM and (re)start the stack with the cloud settings.
# usage: infra/deploy.sh <aws|gcp> [ssh-key-path]   (run `terraform apply` in infra/terraform/<cloud> first)
#   aws: SSH through SSM Session Manager (needs the AWS CLI + session-manager-plugin and a key pair),
#        or straight SSH when enable_ssh = true
#   gcp: SSH through IAP tunnel (needs gcloud and roles/iap.tunnelResourceAccessor)
set -euo pipefail
cd "$(dirname "$0")/.."

CLOUD=${1:?usage: infra/deploy.sh <aws|gcp> [ssh-key-path]}
KEY=${2:-}
TF=(terraform -chdir="infra/terraform/$CLOUD")
ADDR=$("${TF[@]}" output -raw address)

case "$CLOUD" in
  aws)
    ID=$("${TF[@]}" output -raw instance_id)
    REGION=$("${TF[@]}" output -raw region)
    SSH=(ssh -o StrictHostKeyChecking=accept-new)
    [ -n "$KEY" ] && SSH+=(-i "$KEY")
    if [ "$("${TF[@]}" output -raw ssh_open)" = "true" ]; then
      TARGET="ubuntu@$ADDR"
    else
      SSH+=(-o "ProxyCommand=aws ssm start-session --target $ID --document-name AWS-StartSSHSession --parameters portNumber=%p --region $REGION")
      TARGET="ubuntu@$ID"
    fi
    remote() { "${SSH[@]}" "$TARGET" "$@"; }
    ;;
  gcp)
    NAME=$("${TF[@]}" output -raw instance_name)
    ZONE=$("${TF[@]}" output -raw zone)
    PROJECT=$("${TF[@]}" output -raw project_id)
    remote() { gcloud compute ssh "$NAME" --zone "$ZONE" --project "$PROJECT" --tunnel-through-iap --quiet --command "$*"; }
    ;;
  *) echo "cloud must be aws or gcp" >&2; exit 1 ;;
esac

# HTTPS (Caddy + Let's Encrypt on a nip.io name) only where the address is a public IP we own
SITE=""
[ "$CLOUD" = gcp ] && SITE="${ADDR//./-}.nip.io"

echo "Waiting for instance bootstrap ($CLOUD, $ADDR) ..."
until remote "test -f /var/lib/asterisk-poc-ready" 2>/dev/null; do sleep 5; done

# keep data/ and .env on the server, replace everything else
remote "sudo find /opt/asterisk-poc -mindepth 1 -maxdepth 1 ! -name data ! -name .env -exec rm -rf {} +"
COPYFILE_DISABLE=1 tar czf - --exclude=.git --exclude=infra --exclude=data --exclude=.env --exclude=__pycache__ . \
  | remote "sudo tar xzf - -C /opt/asterisk-poc"

# API_KEY is generated once on the server and kept in .env (never committed)
remote "sudo bash -s" <<REMOTE
set -e
cd /opt/asterisk-poc
[ -f .env ] || printf 'API_KEY=%s\n' "\$(openssl rand -hex 16)" > .env
sed -i '/^BIND_ADDR=/d;/^API_BIND_ADDR=/d;/^HOST_MEDIA_ADDR=/d;/^SITE_ADDRESS=/d;/^COMPOSE_PROFILES=/d' .env
printf 'BIND_ADDR=0.0.0.0\nAPI_BIND_ADDR=0.0.0.0\nHOST_MEDIA_ADDR=$ADDR\n' >> .env
[ -n '$SITE' ] && printf 'SITE_ADDRESS=$SITE\nCOMPOSE_PROFILES=https\n' >> .env
docker compose up -d --build
REMOTE

if [ -n "$SITE" ]; then echo "Demo:    https://$SITE/demo   (browser mic works over HTTPS)"; else echo "Demo:    http://$ADDR:8000/demo"; fi
echo "API key: run on the VM: sudo grep API_KEY /opt/asterisk-poc/.env"
echo "SIP:     register softphone to $ADDR:5060 as 1001 (see asterisk/etc/pjsip.conf)"
