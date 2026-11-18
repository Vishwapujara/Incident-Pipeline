resource "aws_ecr_repository" "repo" {
  for_each             = toset(["pipeline", "web"])
  name                 = "${var.project}/${each.key}"
  image_tag_mutability = "MUTABLE"
  force_delete         = true
  image_scanning_configuration { scan_on_push = true }
}

resource "aws_ecr_lifecycle_policy" "repo" {
  for_each   = aws_ecr_repository.repo
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 10 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}

# Seed bucket: the SQLite working DB is uploaded here once and loaded
# into RDS by a one-off pipeline task (src.migrate_to_postgres), since
# RDS is not reachable from outside the VPC.
resource "aws_s3_bucket" "seed" {
  bucket_prefix = "${var.project}-seed-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "seed" {
  bucket                  = aws_s3_bucket.seed.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "seed" {
  bucket = aws_s3_bucket.seed.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
