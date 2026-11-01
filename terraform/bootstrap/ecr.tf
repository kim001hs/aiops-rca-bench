# ==============================================================================
# AWS ECR Repository for HolmesGPT (영구 이미지 저장소)
# ==============================================================================

resource "aws_ecr_repository" "holmesgpt" {
  name                 = "holmesgpt"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = false
  }

  tags = {
    Name        = "holmesgpt"
    Environment = "bootstrap"
    ManagedBy   = "Terraform"
  }
}

# FinOps 과금 방어: 최근 이미지 5개만 유지하고 오래된 이미지는 자동 만료/삭제
resource "aws_ecr_lifecycle_policy" "holmesgpt" {
  repository = aws_ecr_repository.holmesgpt.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep only the latest 5 images to optimize storage costs"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

resource "aws_ecr_repository" "alert_receiver" {
  name                 = "alert-receiver"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

output "alert_receiver_repository_url" {
  value = aws_ecr_repository.alert_receiver.repository_url
}

resource "aws_ecr_repository" "cartservice_benchmark" {
  name                 = "cartservice-benchmark"
  image_tag_mutability = "MUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
}

output "cartservice_benchmark_repository_url" {
  value = aws_ecr_repository.cartservice_benchmark.repository_url
}
