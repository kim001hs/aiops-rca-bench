# ==============================================================================
# 1. VPC 네트워크 모듈 호출
# ==============================================================================
data "aws_caller_identity" "current" {}

locals {
  github_actions_role_arn = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/${var.project_name}-github-actions-role"
}

module "vpc" {
  source = "../../modules/vpc"

  project_name         = var.project_name
  environment          = var.environment
  vpc_cidr             = var.vpc_cidr
  availability_zones   = var.availability_zones
  public_subnet_cidrs  = var.public_subnet_cidrs
  private_subnet_cidrs = var.private_subnet_cidrs
  cluster_name         = var.cluster_name
}

# ==============================================================================
# 2. fck-nat 모듈 호출 (월 $45 NAT GW 대체 -> 월 $3.5 초경량 NAT 솔루션)
# ==============================================================================
module "fck_nat" {
  source = "../../modules/fck-nat"

  project_name           = var.project_name
  environment            = var.environment
  vpc_id                 = module.vpc.vpc_id
  public_subnet_id       = module.vpc.public_subnet_ids[0] # 첫 번째 퍼블릭 서브넷에 배치
  private_route_table_id = module.vpc.private_route_table_id
}

# ==============================================================================
# 3. EKS Graviton 클러스터 & 노드그룹 모듈 호출
# ==============================================================================
module "eks" {
  source = "../../modules/eks"

  project_name            = var.project_name
  environment             = var.environment
  cluster_name            = var.cluster_name
  cluster_version         = var.cluster_version
  vpc_id                  = module.vpc.vpc_id
  public_subnet_ids       = module.vpc.public_subnet_ids
  private_subnet_ids      = module.vpc.private_subnet_ids
  node_instance_types     = var.node_instance_types
  node_capacity_type      = var.node_capacity_type
  node_desired_size       = var.node_desired_size
  node_min_size           = var.node_min_size
  node_max_size           = var.node_max_size
  github_actions_role_arn = local.github_actions_role_arn
  admin_principal_arns    = var.admin_principal_arns

  # fck-nat가 먼저 구성되어 Private Subnet에서 인터넷 아웃바운드가 열려야 EKS 워커 노드가 조인 가능
  depends_on = [
    module.fck_nat
  ]
}
