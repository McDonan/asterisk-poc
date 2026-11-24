variable "region" {
  type    = string
  default = "ap-southeast-1"
}

variable "instance_type" {
  type    = string
  default = "t3.medium"
}

variable "key_name" {
  description = "Existing EC2 key pair name used for SSH/deploy"
  type        = string
}

variable "admin_cidrs" {
  description = "CIDRs allowed to open the Web UI/API (8000) and browser softphone WebSockets (8088, 8089), e.g. [\"1.2.3.4/32\"]"
  type        = list(string)
}

variable "sip_cidrs" {
  description = "CIDRs allowed to reach SIP 5060/udp and RTP 10000-10100, 12000-12100/udp (softphones). Avoid 0.0.0.0/0: extension passwords are lab-grade."
  type        = list(string)
}

variable "volume_size_gb" {
  type    = number
  default = 30
}

variable "subnet_id" {
  description = "Subnet of the agreed non-prod VPC. Null falls back to the default VPC (avoid in shared accounts)."
  type        = string
  default     = null
}

variable "permissions_boundary_arn" {
  description = "Optional IAM permissions boundary required by the account"
  type        = string
  default     = null
}

variable "extra_tags" {
  description = "Extra tags required by the platform team (owner, cost-center, expiry...)"
  type        = map(string)
  default     = {}
}

variable "assign_eip" {
  description = "Create a public Elastic IP. Keep false for private subnets reached over VPN."
  type        = bool
  default     = false
}

variable "alb_security_group_id" {
  description = "Optional: security group of the existing private ALB allowed to reach the API on 8000"
  type        = string
  default     = null
}

variable "enable_ssh" {
  description = "Open SSH (22) to admin_cidrs. Default false: deploy through SSM Session Manager."
  type        = bool
  default     = false
}
