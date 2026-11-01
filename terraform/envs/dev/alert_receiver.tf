variable "alert_results_bucket" {
  description = "Persistent alert result bucket created by terraform/bootstrap"
  type        = string
}

variable "alert_receiver_image" {
  description = "Full container image URI for the alert receiver"
  type        = string
}

resource "aws_iam_role" "alert_receiver" {
  name = "${var.project_name}-${var.environment}-alert-receiver"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = module.eks.oidc_provider_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "${replace(module.eks.oidc_issuer_url, "https://", "")}:aud" = "sts.amazonaws.com"
        "${replace(module.eks.oidc_issuer_url, "https://", "")}:sub" = "system:serviceaccount:monitoring:alert-receiver"
      } }
    }]
  })
}

resource "aws_iam_role_policy" "alert_receiver" {
  role = aws_iam_role.alert_receiver.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "arn:aws:s3:::${var.alert_results_bucket}/alerts/*"
      },
      {
        Effect   = "Allow"
        Action   = ["s3:DeleteObject"]
        Resource = "arn:aws:s3:::${var.alert_results_bucket}/alerts/*/result.json.claim"
      }
    ]
  })
}

output "alert_receiver_role_arn" {
  value = aws_iam_role.alert_receiver.arn
}

output "alert_results_bucket" {
  value = var.alert_results_bucket
}

output "alert_receiver_image" {
  value = var.alert_receiver_image
}
