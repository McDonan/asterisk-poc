# Deploy on AWS (Phase 1: single EC2)

Runs the whole compose stack (pbx, sim, api) on one EC2 instance (set `assign_eip = true` for an Elastic IP), so a softphone on a different device/network registers over the real internet.

## Prerequisites
- AWS credentials configured (`aws configure` or env vars), Terraform >= 1.5
- An existing EC2 key pair in the target region (default `ap-southeast-1`)

## Steps
```bash
cd infra/terraform/aws
cp terraform.tfvars.example terraform.tfvars   # edit key_name, admin_cidrs, sip_cidrs
terraform init && terraform apply
cd ../../.. && infra/deploy.sh aws ~/.ssh/my-keypair.pem   # first build takes 10-20 min (Asterisk from source)
```

`terraform apply` creates: EC2 (Ubuntu 24.04, t3.medium, encrypted root disk), optional Elastic IP (`assign_eip`, default off), security group (egress limited to 443/80/DNS/NTP), IAM role for SSM. Set `subnet_id` to deploy into an existing VPC (default VPC otherwise), plus `extra_tags` / `permissions_boundary_arn` if the platform team requires them. `deploy.sh` reaches the VM through SSM (SSH only if `enable_ssh = true`), copies the project and runs `docker compose up -d --build` with `.env` on the server:

| Variable | Value |
|---|---|
| `BIND_ADDR` / `API_BIND_ADDR` | `0.0.0.0` (exposes SIP/RTP and API; AMI/ARI stay on localhost) |
| `HOST_MEDIA_ADDR` | the Elastic IP (RTP address advertised to softphones) |
| `API_KEY` | random, generated once on the server |

## Prove it works from another device
1. Register a softphone (on your phone over mobile data, or another laptop) to `<EIP>:5060`, user `1001` (password in `asterisk/etc/pjsip.conf`), transport UDP. Your phone's public IP must be in `sip_cidrs`.
2. Open `http://<EIP>:8000/demo?api_key=<API_KEY>` (get the key from the deploy output) and run the demo, or call the PBX from the softphone.
3. Run the E2E: `ssh ubuntu@<EIP> 'cd /opt/asterisk-poc && docker compose --profile test run --rm e2e'`

## Notes
- Security group limits SIP/RTP to `sip_cidrs` and SSH/API to `admin_cidrs`. Extension passwords are lab-grade, so do not open SIP to `0.0.0.0/0`.
- The API is plain HTTP in this phase; use an SSH tunnel or add ALB + ACM later.
- Cost: ~US$35/month for t3.medium + EIP; `terraform destroy` removes everything (recordings/CDR on the instance are lost).
- Next phases: `api` to Fargate, recordings to S3, CDR to RDS, real SIP trunk (e.g. Chime SDK Voice Connector) replacing `sim`.
