terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

locals {
  name = "asterisk-poc"
  tag  = "asterisk-poc"
}

data "google_compute_network" "net" {
  name = var.network
}

resource "google_service_account" "pbx" {
  account_id   = local.name
  display_name = "Asterisk POC VM (no roles)"
}

resource "google_compute_address" "pbx" {
  name   = local.name
  region = var.region
}

resource "google_compute_firewall" "udp" {
  name          = "${local.name}-udp"
  network       = data.google_compute_network.net.name
  direction     = "INGRESS"
  source_ranges = var.sip_cidrs
  target_tags   = [local.tag]

  allow {
    protocol = "udp"
    ports    = ["5060", "10000-10100", "12000-12100"]
  }
}

# Web UI / API (8000) and browser softphone WebSockets (PBX 8088, simulated customers 8089)
resource "google_compute_firewall" "tcp" {
  name          = "${local.name}-tcp"
  network       = data.google_compute_network.net.name
  direction     = "INGRESS"
  source_ranges = var.admin_cidrs
  target_tags   = [local.tag]

  allow {
    protocol = "tcp"
    ports    = ["8000", "8088", "8089"]
  }
}

# Identity-Aware Proxy range only: shell access without exposing SSH to the internet
resource "google_compute_firewall" "iap" {
  name          = "${local.name}-iap-ssh"
  network       = data.google_compute_network.net.name
  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]
  target_tags   = [local.tag]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

resource "google_compute_instance" "pbx" {
  name         = local.name
  machine_type = var.machine_type
  tags         = [local.tag]
  labels       = merge({ project = "asterisk-poc", managed_by = "terraform" }, var.extra_labels)

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2404-lts-amd64"
      size  = var.disk_size_gb
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = data.google_compute_network.net.name
    access_config {
      nat_ip = google_compute_address.pbx.address
    }
  }

  service_account {
    email  = google_service_account.pbx.email
    scopes = ["logging-write", "monitoring-write"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = file("${path.module}/../../bootstrap.sh")
}
