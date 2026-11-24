output "private_ip" {
  value = aws_instance.pbx.private_ip
}

output "public_ip" {
  value = one(aws_eip.pbx[*].public_ip)
}

# Address softphones/API clients use: EIP if created, otherwise the private IP (VPN)
output "address" {
  value = var.assign_eip ? aws_eip.pbx[0].public_ip : aws_instance.pbx.private_ip
}

output "demo_url" {
  value = "http://${var.assign_eip ? aws_eip.pbx[0].public_ip : aws_instance.pbx.private_ip}:8000/demo"
}

output "cloud" {
  value = "aws"
}

output "instance_id" {
  value = aws_instance.pbx.id
}

output "region" {
  value = var.region
}

output "ssh_open" {
  value = var.enable_ssh
}
