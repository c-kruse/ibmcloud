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

variable "artifact_storage_size" {
  description = "Persistent storage for future test reports. RWO requires serialized writers."
  type        = string
  default     = "10Gi"
}

# Enabling the installed plugin is separate from deploying its Service. Read
# the existing list so enabling Pipelines never disables another console plugin.
data "kubernetes_resource" "console" {
  api_version = "operator.openshift.io/v1"
  kind        = "Console"
  metadata {
    name = "cluster"
  }
}

import {
  to = kubernetes_manifest.console_plugins
  id = "apiVersion=operator.openshift.io/v1,kind=Console,name=cluster"
}

resource "kubernetes_manifest" "console_plugins" {
  manifest = {
    apiVersion = "operator.openshift.io/v1"
    kind       = "Console"
    metadata = {
      name = "cluster"
    }
    spec = {
      plugins = distinct(concat(
        data.kubernetes_resource.console.object.spec.plugins,
        ["pipelines-console-plugin"],
      ))
    }
  }
  # The Console Operator initially owns this atomic list. Transfer only the
  # plugin-list field, preserving its current entries, not other console settings.
  field_manager {
    force_conflicts = true
  }
  lifecycle {
    prevent_destroy = true
  }
}

# The Operator creates this singleton. Adopt it rather than trying to create
# a competing configuration or take ownership of Operator-generated workloads.
import {
  to = kubernetes_manifest.pipelines_config
  id = "apiVersion=operator.tekton.dev/v1alpha1,kind=TektonConfig,name=config"
}

resource "kubernetes_manifest" "pipelines_config" {
  manifest = {
    apiVersion = "operator.tekton.dev/v1alpha1"
    kind       = "TektonConfig"
    metadata = {
      name = "config"
    }
    spec = {
      profile         = "all"
      targetNamespace = "openshift-pipelines"
      result = {
        disabled = false
        options = {
          configMaps = {
            "tekton-results-config-results-retention-policy" = {
              # The Operator normalizes ConfigMap overrides with metadata.
              # Keep the untyped object's shape stable for the provider.
              metadata = {}
              data = {
                defaultRetention = "30d"
                runAt            = "0 3 * * *"
              }
            }
          }
        }
      }
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "kubernetes_namespace_v1" "ci" {
  metadata {
    name = "skupper-ci"
    labels = {
      "app.kubernetes.io/part-of" = "skupper-ci"
    }
  }
  lifecycle {
    prevent_destroy = true
    # OpenShift allocates SCC ranges; the Pipelines Operator marks reconciliation.
    ignore_changes = [
      metadata[0].annotations,
      metadata[0].labels["openshift-pipelines.tekton.dev/namespace-reconcile-version"],
      metadata[0].labels["openshift-pipelines.tekton.dev/namespace-trusted-configmaps-version"],
    ]
  }
}

# pipelines.tf scopes the distinct installer and test privileges.
# Never bind either runner to the namespace's default service account.
resource "kubernetes_service_account_v1" "runner" {
  for_each = toset(["skupper-upgrade", "skupper-test"])
  metadata {
    name      = each.key
    namespace = kubernetes_namespace_v1.ci.metadata[0].name
  }
  automount_service_account_token = true
  lifecycle {
    # OpenShift's image registry controller provisions and rotates these.
    ignore_changes = [image_pull_secret, secret, metadata[0].annotations]
  }
}

resource "kubernetes_service_account_v1" "scheduler" {
  metadata {
    name      = "skupper-scheduler"
    namespace = kubernetes_namespace_v1.ci.metadata[0].name
  }
  lifecycle {
    ignore_changes = [image_pull_secret, secret, metadata[0].annotations]
  }
}

resource "kubernetes_role_v1" "scheduler" {
  metadata {
    name      = "skupper-scheduler"
    namespace = kubernetes_namespace_v1.ci.metadata[0].name
  }
  rule {
    api_groups = ["tekton.dev"]
    resources  = ["pipelineruns"]
    verbs      = ["create", "get", "list"]
  }
}

resource "kubernetes_role_binding_v1" "scheduler" {
  metadata {
    name      = "skupper-scheduler"
    namespace = kubernetes_namespace_v1.ci.metadata[0].name
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.scheduler.metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account_v1.scheduler.metadata[0].name
    namespace = kubernetes_namespace_v1.ci.metadata[0].name
  }
}

resource "kubernetes_persistent_volume_claim_v1" "artifacts" {
  metadata {
    name      = "skupper-ci-artifacts"
    namespace = kubernetes_namespace_v1.ci.metadata[0].name
  }
  spec {
    access_modes = ["ReadWriteOnce"]
    resources {
      requests = {
        storage = var.artifact_storage_size
      }
    }
  }
  # The default storage class may use WaitForFirstConsumer. The first runner
  # mounts this claim; provisioning must not wait for a nonexistent consumer.
  wait_until_bound = false
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

output "ci_namespace" {
  value = kubernetes_namespace_v1.ci.metadata[0].name
}

output "artifact_claim" {
  value = kubernetes_persistent_volume_claim_v1.artifacts.metadata[0].name
}
