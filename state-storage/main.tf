terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    ibm = {
      source  = "IBM-Cloud/ibm"
      version = "~> 2.8.0"
    }
  }
}

provider "ibm" {
  region = var.region
}

variable "bucket_name" {
  description = "Globally unique state bucket name, also used to name its dedicated COS instance."
  type        = string
}

variable "region" {
  description = "Regional storage location, independent of the cluster's worker zone."
  type        = string
  default     = "us-east"
}

variable "resource_group_name" {
  description = "Existing resource group for the state storage instance."
  type        = string
  default     = "Default"
}

data "ibm_resource_group" "state" {
  name = var.resource_group_name
}

# Separate from the cluster's registry COS instance and its lifecycle.
resource "ibm_resource_instance" "state" {
  name              = "${var.bucket_name}-cos"
  service           = "cloud-object-storage"
  plan              = "standard"
  location          = "global"
  resource_group_id = data.ibm_resource_group.state.id

  lifecycle {
    prevent_destroy = true
  }
}

resource "ibm_cos_bucket" "state" {
  bucket_name          = var.bucket_name
  resource_instance_id = ibm_resource_instance.state.id
  region_location      = var.region
  storage_class        = "standard"
  force_delete         = false

  object_versioning {
    enable = true
  }

  lifecycle {
    prevent_destroy = true
  }
}

output "cos_instance_crn" {
  description = "Dedicated COS instance for configuring backend credentials and access."
  value       = ibm_resource_instance.state.crn
}

output "backend_config" {
  description = "Non-secret S3 backend configuration. Save to ../backend.hcl after provisioning."
  value       = <<-HCL
    bucket = "${ibm_cos_bucket.state.bucket_name}"
    region = "${var.region}"
    endpoints = {
      s3 = "https://s3.${var.region}.cloud-object-storage.appdomain.cloud"
    }
    use_path_style              = true
    skip_region_validation      = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_lockfile                = true
  HCL
}
