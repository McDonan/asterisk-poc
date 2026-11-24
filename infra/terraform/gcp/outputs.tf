output "address" {
  value = google_compute_address.pbx.address
}

output "public_ip" {
  value = google_compute_address.pbx.address
}

output "demo_url" {
  value = "http://${google_compute_address.pbx.address}:8000/demo"
}

output "cloud" {
  value = "gcp"
}

output "instance_name" {
  value = google_compute_instance.pbx.name
}

output "zone" {
  value = var.zone
}

output "project_id" {
  value = var.project_id
}
