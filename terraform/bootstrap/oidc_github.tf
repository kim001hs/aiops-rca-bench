# ==============================================================================
# GitHub Actions OIDC 연동 IAM 역할 (보안 키 없는 안전한 CI/CD)
# ==============================================================================

data "aws_caller_identity" "current" {}

# GitHub Actions OIDC Provider ARN (이미 AWS 계정에 생성되어 있는 프로바이더 참조)
locals {
  github_oidc_provider_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:oidc-provider/token.actions.githubusercontent.com"
}

# GitHub Actions에서 terraform destroy 및 인프라 관리를 수행할 IAM 역할
resource "aws_iam_role" "github_actions" {
  name = "${var.project_name}-github-actions-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = local.github_oidc_provider_arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            # 지정된 리포지토리의 모든 워크플로우/브랜치에서 Assume 가능 (GitHub 신규 불변 ID 포맷 지원)
            "token.actions.githubusercontent.com:sub" = [
              "repo:kim001hs*/msa-demo*:*",
              "repo:kim001hs*/holmesgpt*:*",
              "repo:kim001hs*/aiops-rca-bench*:*",
              "repo:${var.github_repo}:*"
            ]
          }
        }
      }
    ]
  })

  tags = {
    Name        = "${var.project_name}-github-actions-role"
    Environment = "bootstrap"
    ManagedBy   = "Terraform"
  }
}

# Terraform Destroy 및 자원 관리를 위한 관리자 권한 부여
resource "aws_iam_role_policy_attachment" "github_actions_admin" {
  role       = aws_iam_role.github_actions.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

