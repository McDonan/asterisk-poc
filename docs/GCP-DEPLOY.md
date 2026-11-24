# Deploy on GCP (single Compute Engine VM)

Same stack as AWS (pbx, sim, api via docker compose) on one VM with a static external IP. Cloud Run does not fit: it has no UDP, which SIP/RTP need. No Cloud NAT is needed because the VM has an external IP.

## Prerequisites
- A GCP project with billing, `gcloud auth login` and `gcloud auth application-default login`, Terraform >= 1.5
- APIs enabled: Compute Engine, Identity-Aware Proxy
- Your user has `roles/iap.tunnelResourceAccessor` and `roles/compute.osAdminLogin`

## Steps
```bash
cd infra/terraform/gcp
cp terraform.tfvars.example terraform.tfvars   # edit project_id, admin_cidrs, sip_cidrs
terraform init && terraform apply
cd ../../.. && infra/deploy.sh gcp               # first build takes 10-20 min (Asterisk from source)
```

`terraform apply` creates: e2-medium VM (Ubuntu 24.04, 30 GB pd-balanced, shielded VM, OS Login), static external IP, a service account with no roles, and firewall rules:

| Rule | Source | Ports |
|---|---|---|
| udp | `sip_cidrs` | 5060, 10000-10100, 12000-12100 |
| tcp | `admin_cidrs` | 8000 (Web UI/API), 8088, 8089 (browser softphone WebSocket) |
| iap-ssh | `35.235.240.0/20` (Google IAP only) | 22 |

Shell access is through IAP (`gcloud compute ssh --tunnel-through-iap`), so SSH is not open to the internet.

## Notes
- Cost about US$30-40/month with the VM running (rough estimate, check the pricing calculator). Stop the VM between demos to pay only for disk and IP.
- Browsers only allow the microphone on HTTPS or localhost. Over plain `http://<IP>:8000`, use a SIP softphone app instead.
- `terraform destroy` removes everything (recordings/CDR on the VM are lost).
