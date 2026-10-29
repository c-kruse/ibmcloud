terraform {
  required_version = ">= 1.10.0, < 2.0.0"
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.38.0"
    }
  }
  backend "s3" {}
}

provider "kubernetes" {
  config_path    = pathexpand(var.kubeconfig_path)
  config_context = var.kube_context
}

variable "cluster_name" {
  type = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,39}$", var.cluster_name))
    error_message = "Use the existing cluster's name."
  }
}

variable "kube_context" {
  description = "Explicit authenticated context; never uses the current-context implicitly."
  type        = string
}

variable "kubeconfig_path" {
  description = "Local kubeconfig path; credentials are not managed by Terraform."
  type        = string
  default     = "~/.kube/config"
}

variable "pipelines_channel" {
  description = "Reviewed minor channel. Patch upgrades within it are automatic."
  type        = string
  default     = "pipelines-1.24"
  validation {
    condition     = can(regex("^pipelines-[0-9]+\\.[0-9]+$", var.pipelines_channel))
    error_message = "Select an explicit pipelines-X.Y channel, not latest."
  }
}

resource "kubernetes_manifest" "pipelines_subscription" {
  manifest = {
    apiVersion = "operators.coreos.com/v1alpha1"
    kind       = "Subscription"
    metadata = {
      name      = "openshift-pipelines-operator"
      namespace = "openshift-operators"
    }
    spec = {
      name                = "openshift-pipelines-operator-rh"
      channel             = var.pipelines_channel
      source              = "redhat-operators"
      sourceNamespace     = "openshift-marketplace"
      installPlanApproval = "Automatic"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "cluster_name" {
  value = var.cluster_name
}

output "kube_context" {
  value = var.kube_context
}

output "pipelines_channel" {
  value = var.pipelines_channel
}
