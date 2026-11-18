terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # State is local by default. For shared use, configure an S3 backend:
  # backend "s3" { bucket = "...", key = "incident-pipeline/terraform.tfstate", region = "us-east-1" }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = var.project, ManagedBy = "terraform" }
  }
}

data "aws_availability_zones" "available" { state = "available" }
data "aws_caller_identity" "current" {}
