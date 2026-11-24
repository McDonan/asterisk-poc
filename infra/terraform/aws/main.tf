terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = merge({ Project = "asterisk-poc", ManagedBy = "terraform" }, var.extra_tags)
  }
}

data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

data "aws_vpc" "default" {
  count   = var.subnet_id == null ? 1 : 0
  default = true
}

data "aws_subnet" "target" {
  count = var.subnet_id == null ? 0 : 1
  id    = var.subnet_id
}

locals {
  vpc_id = var.subnet_id == null ? data.aws_vpc.default[0].id : data.aws_subnet.target[0].vpc_id
}

data "aws_iam_policy_document" "assume_ec2" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

# SSM Session Manager access (audited shell) instead of relying on SSH only
resource "aws_iam_role" "pbx" {
  name_prefix          = "asterisk-poc-"
  assume_role_policy   = data.aws_iam_policy_document.assume_ec2.json
  permissions_boundary = var.permissions_boundary_arn
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.pbx.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "pbx" {
  name_prefix = "asterisk-poc-"
  role        = aws_iam_role.pbx.name
}

resource "aws_security_group" "pbx" {
  name_prefix = "asterisk-poc-"
  description = "Asterisk POC"
  vpc_id      = local.vpc_id

  dynamic "ingress" {
    for_each = var.enable_ssh ? [1] : []
    content {
      description = "SSH (off by default: use SSM)"
      from_port   = 22
      to_port     = 22
      protocol    = "tcp"
      cidr_blocks = var.admin_cidrs
    }
  }

  # Web UI / API (8000) and browser softphone WebSockets (PBX 8088, simulated customers 8089)
  dynamic "ingress" {
    for_each = { api = 8000, ws_pbx = 8088, ws_sim = 8089 }
    content {
      description = "tcp ${ingress.value}"
      from_port   = ingress.value
      to_port     = ingress.value
      protocol    = "tcp"
      cidr_blocks = var.admin_cidrs
    }
  }

  dynamic "ingress" {
    for_each = var.alb_security_group_id == null ? [] : [var.alb_security_group_id]
    content {
      description     = "API from private ALB"
      from_port       = 8000
      to_port         = 8000
      protocol        = "tcp"
      security_groups = [ingress.value]
    }
  }

  ingress {
    description = "SIP"
    from_port   = 5060
    to_port     = 5060
    protocol    = "udp"
    cidr_blocks = var.sip_cidrs
  }

  ingress {
    description = "RTP PBX"
    from_port   = 10000
    to_port     = 10100
    protocol    = "udp"
    cidr_blocks = var.sip_cidrs
  }

  ingress {
    description = "RTP simulated customers (browser)"
    from_port   = 12000
    to_port     = 12100
    protocol    = "udp"
    cidr_blocks = var.sip_cidrs
  }

  egress {
    description = "HTTPS (Docker, apt, SSM)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "HTTP (apt mirrors)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "DNS"
    from_port   = 53
    to_port     = 53
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "NTP"
    from_port   = 123
    to_port     = 123
    protocol    = "udp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_instance" "pbx" {
  ami                         = data.aws_ssm_parameter.ubuntu.value
  instance_type               = var.instance_type
  key_name                    = var.key_name
  subnet_id                   = var.subnet_id
  associate_public_ip_address = var.assign_eip ? true : false
  vpc_security_group_ids      = [aws_security_group.pbx.id]
  iam_instance_profile        = aws_iam_instance_profile.pbx.name
  user_data                   = file("${path.module}/../../bootstrap.sh")

  metadata_options {
    http_tokens = "required"
  }

  root_block_device {
    volume_type = "gp3"
    volume_size = var.volume_size_gb
    encrypted   = true
  }

  tags = { Name = "asterisk-poc" }
}

resource "aws_eip" "pbx" {
  count    = var.assign_eip ? 1 : 0
  instance = aws_instance.pbx.id
  domain   = "vpc"
  tags     = { Name = "asterisk-poc" }
}
