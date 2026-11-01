resource "aws_ssm_parameter" "alert_results_bucket" {
  name  = "/${var.project_name}/alert-results-bucket"
  type  = "String"
  value = aws_s3_bucket.alert_results.id
}

resource "aws_ssm_parameter" "alert_receiver_repository" {
  name  = "/${var.project_name}/alert-receiver-repository"
  type  = "String"
  value = aws_ecr_repository.alert_receiver.repository_url
}

resource "aws_ssm_parameter" "cartservice_benchmark_repository" {
  name  = "/${var.project_name}/cartservice-benchmark-repository"
  type  = "String"
  value = aws_ecr_repository.cartservice_benchmark.repository_url
}
