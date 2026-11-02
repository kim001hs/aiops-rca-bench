# 1. EKS 클러스터 컨트롤 플레인 생성
resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  version  = var.cluster_version
  role_arn = aws_iam_role.cluster.arn

  vpc_config {
    # 클러스터 ENI는 Public과 Private 서브넷을 모두 연결하여 유연한 엔드포인트 통신 보장
    subnet_ids              = concat(var.public_subnet_ids, var.private_subnet_ids)
    endpoint_private_access = true
    endpoint_public_access  = true
  }

  access_config {
    authentication_mode                         = "API_AND_CONFIG_MAP"
    bootstrap_cluster_creator_admin_permissions = true
  }

  depends_on = [
    aws_iam_role_policy_attachment.cluster_AmazonEKSClusterPolicy
  ]

  tags = {
    Name        = var.cluster_name
    Environment = var.environment
  }
}

# 2. EKS Managed Node Group (x86_64 t3.large)
resource "aws_eks_node_group" "main" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "${var.cluster_name}-default-ng"
  node_role_arn   = aws_iam_role.node_group.arn

  # 보안을 위해 워커 노드는 반드시 Private Subnet에만 배치
  subnet_ids = var.private_subnet_ids

  scaling_config {
    desired_size = var.node_desired_size
    min_size     = var.node_min_size
    max_size     = var.node_max_size
  }

  # x86_64 사양: Amazon Linux 2023 AMI
  instance_types = var.node_instance_types # ["t3.large"]
  ami_type       = "AL2023_x86_64_STANDARD"
  capacity_type  = var.node_capacity_type # "ON_DEMAND" 또는 "SPOT"

  update_config {
    max_unavailable = 1
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_AmazonEKSWorkerNodePolicy,
    aws_iam_role_policy_attachment.node_AmazonEKS_CNI_Policy,
    aws_iam_role_policy_attachment.node_AmazonEC2ContainerRegistryReadOnly
  ]

  tags = {
    Name        = "${var.cluster_name}-default-node"
    Environment = var.environment
    ManagedBy   = "Terraform"
  }
}

# EKS는 클러스터 생성 주체의 Access Entry를 자동 생성합니다. 클러스터 생성 주체(로컬 IAM 사용자 또는
# GitHub Actions OIDC 역할)가 관리자 목록에 포함되어 있으면 중복 등록으로 인한 409 Conflict 오류가
# 발생하므로, AssumeRole 세션 ARN을 정규화하여 생성 주체를 제외한 추가 관리자 목록만 등록합니다.
data "aws_caller_identity" "creator" {}

locals {
  # creator가 AssumedRole인 경우(sts::...:assumed-role/role-name/session) 기저 IAM Role ARN으로 정규화
  creator_role_arn = replace(
    data.aws_caller_identity.creator.arn,
    "/^arn:aws:sts::([0-9]+):assumed-role\\/([^\\/]+)\\/.+$/",
    "arn:aws:iam::$1:role/$2"
  )

  all_admin_principals = distinct(compact(concat(
    var.admin_principal_arns,
    [var.github_actions_role_arn]
  )))

  additional_admin_principals = toset([
    for arn in local.all_admin_principals : arn
    if arn != data.aws_caller_identity.creator.arn && arn != local.creator_role_arn
  ])
}

# 3. 추가 개발자/관리자 IAM 사용자 및 CI/CD 역할 EKS Access Entry 자동 등록
resource "aws_eks_access_entry" "developer_admins" {
  for_each      = local.additional_admin_principals
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = each.value
  type          = "STANDARD"
}

resource "aws_eks_access_policy_association" "developer_admins" {
  for_each      = local.additional_admin_principals
  cluster_name  = aws_eks_cluster.main.name
  principal_arn = aws_eks_access_entry.developer_admins[each.key].principal_arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"

  access_scope {
    type = "cluster"
  }

  depends_on = [
    aws_eks_access_entry.developer_admins
  ]
}
