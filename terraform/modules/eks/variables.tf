variable "project_name" {
  description = "Project name prefix"
  type        = string
  default     = "msa-demo"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "dev"
}

variable "cluster_name" {
  description = "Name of the EKS Cluster"
  type        = string
}

variable "cluster_version" {
  description = "Kubernetes version for the EKS Cluster"
  type        = string
  default     = "1.36"
}

variable "vpc_id" {
  description = "VPC ID where the cluster is located"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs for worker nodes and cluster interfaces"
  type        = list(string)
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs for cluster interfaces"
  type        = list(string)
}

variable "node_instance_types" {
  description = "Instance types for the EKS managed node group (x86_64)"
  type        = list(string)
  default     = ["t3.large"]
}

variable "node_capacity_type" {
  description = "Capacity type for node group: ON_DEMAND or SPOT"
  type        = string
  default     = "ON_DEMAND"
}

variable "node_desired_size" {
  description = "Desired number of worker nodes"
  type        = number
  default     = 2
}

variable "node_min_size" {
  description = "Minimum number of worker nodes"
  type        = number
  default     = 1
}

variable "node_max_size" {
  description = "Maximum number of worker nodes"
  type        = number
  default     = 3
}

variable "github_actions_role_arn" {
  description = "IAM role ARN used by GitHub Actions to clean up Kubernetes resources before destroy"
  type        = string
}

variable "admin_principal_arns" {
  description = "List of additional IAM ARNs (e.g. developer IAM users) to grant EKS Cluster Admin access"
  type        = list(string)
  default     = []
}
