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
