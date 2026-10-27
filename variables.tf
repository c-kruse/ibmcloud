variable "cluster_name" {
  description = "Name for the NEW cluster; also prefixes its infrastructure resources."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,39}$", var.cluster_name))
    error_message = "Use 1–40 lowercase letters, digits, or hyphens, starting with a letter."
  }
}

variable "region" {
  description = "IBM Cloud region."
  type        = string
  default     = "us-east"
}

variable "zone" {
  description = "Single availability zone for the workers, subnet, and public gateway."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = startswith(var.zone, "${var.region}-")
    error_message = "The zone must belong to the configured region."
  }
}

variable "resource_group_name" {
  description = "Existing resource group in which to create resources."
  type        = string
  default     = "Default"
}

variable "openshift_version" {
  description = "IBM OpenShift version; confirm availability with ibmcloud ks versions."
  type        = string
  default     = "4.22_openshift"

  validation {
    condition     = can(regex("^4[.][0-9]+([.][0-9]+(_[0-9]+)?)?_openshift$", var.openshift_version))
    error_message = "Use an IBM OpenShift version such as 4.22_openshift or 4.22.13_openshift."
  }
}

variable "worker_flavor" {
  description = "VPC worker profile; bx2.4x16 provides 4 vCPUs and 16 GB RAM per worker."
  type        = string
  default     = "bx2.4x16"
}

variable "worker_count" {
  description = "Number of workers in the single zone. OpenShift requires at least two."
  type        = number
  default     = 3

  validation {
    condition     = var.worker_count >= 2 && floor(var.worker_count) == var.worker_count
    error_message = "worker_count must be a whole number of at least 2."
  }
}

variable "worker_subnet_cidr" {
  description = "Worker subnet in the new VPC; must not overlap pod/service or connected networks."
  type        = string
  default     = "10.243.0.0/24"
}

variable "pod_subnet_cidr" {
  description = "Pod address range, matching the reference cluster."
  type        = string
  default     = "172.17.0.0/18"
}

variable "service_subnet_cidr" {
  description = "Service address range, matching the reference cluster."
  type        = string
  default     = "172.21.0.0/16"
}
