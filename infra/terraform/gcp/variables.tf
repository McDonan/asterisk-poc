variable "project_id" {
  description = "GCP project that hosts the PoC"
  type        = string
}

variable "region" {
  type    = string
  default = "asia-southeast1"
}

variable "zone" {
  type    = string
  default = "asia-southeast1-a"
}

variable "network" {
  description = "VPC network name"
  type        = string
  default     = "default"
}

variable "machine_type" {
  type    = string
  default = "e2-medium"
}

variable "disk_size_gb" {
  type    = number
  default = 30
}

variable "admin_cidrs" {
  description = "CIDRs allowed to open the Web UI/API (8000) and browser softphone WebSockets (8088, 8089), e.g. [\"1.2.3.4/32\"]"
  type        = list(string)
}

variable "sip_cidrs" {
  description = "CIDRs allowed to reach SIP 5060/udp and RTP 10000-10100, 12000-12100/udp (softphones). Avoid 0.0.0.0/0: extension passwords are lab-grade."
  type        = list(string)
}

variable "extra_labels" {
  description = "Extra labels (owner, expiry...). Values: lowercase letters, digits, _ and -"
  type        = map(string)
  default     = {}
}
