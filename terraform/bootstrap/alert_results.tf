# This bucket lives in bootstrap state so destroying the EKS environment keeps raw diagnoses.
resource "aws_s3_bucket" "alert_results" {
  bucket        = "${var.project_name}-alert-results-${random_string.suffix.result}"
  force_destroy = false
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "alert_results" {
  bucket                  = aws_s3_bucket.alert_results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "alert_results" {
  bucket = aws_s3_bucket.alert_results.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "alert_results" {
  bucket = aws_s3_bucket.alert_results.id
  versioning_configuration {
    status = "Enabled"
  }
}

output "alert_results_bucket" {
  value = aws_s3_bucket.alert_results.id
}
