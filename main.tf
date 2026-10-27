data "ibm_resource_group" "cluster" {
  name = var.resource_group_name
}

resource "ibm_is_vpc" "cluster" {
  name                      = "${var.cluster_name}-vpc"
  resource_group            = data.ibm_resource_group.cluster.id
  address_prefix_management = "manual"
}

resource "ibm_is_vpc_address_prefix" "workers" {
  name = "${var.cluster_name}-workers"
  vpc  = ibm_is_vpc.cluster.id
  zone = var.zone
  cidr = var.worker_subnet_cidr
}

resource "ibm_is_public_gateway" "workers" {
  name           = "${var.cluster_name}-gateway"
  vpc            = ibm_is_vpc.cluster.id
  zone           = var.zone
  resource_group = data.ibm_resource_group.cluster.id
}

resource "ibm_is_subnet" "workers" {
  name            = "${var.cluster_name}-workers"
  vpc             = ibm_is_vpc.cluster.id
  zone            = var.zone
  ipv4_cidr_block = ibm_is_vpc_address_prefix.workers.cidr
  public_gateway  = ibm_is_public_gateway.workers.id
  resource_group  = data.ibm_resource_group.cluster.id
}

# OpenShift requires a standard COS instance for its internal image registry.
resource "ibm_resource_instance" "registry" {
  name              = "${var.cluster_name}-registry"
  service           = "cloud-object-storage"
  plan              = "standard"
  location          = "global"
  resource_group_id = data.ibm_resource_group.cluster.id
}

resource "ibm_container_vpc_cluster" "cluster" {
  name              = var.cluster_name
  resource_group_id = data.ibm_resource_group.cluster.id
  vpc_id            = ibm_is_vpc.cluster.id
  offering          = "openshift"
  kube_version      = var.openshift_version
  cos_instance_crn  = ibm_resource_instance.registry.crn
  flavor            = var.worker_flavor
  worker_count      = var.worker_count
  operating_system  = "RHCOS"
  pod_subnet        = var.pod_subnet_cidr
  service_subnet    = var.service_subnet_cidr

  # New OpenShift VPC clusters are automatically secure-by-default. The provider's
  # enable_secure_by_default option is only for updating existing clusters.
  # Allow public API access and outbound traffic (for example, image pulls).
  disable_public_service_endpoint     = false
  disable_outbound_traffic_protection = true
  image_security_enforcement          = false
  force_delete_storage                = false
  wait_till                           = "IngressReady"

  zones {
    name      = var.zone
    subnet_id = ibm_is_subnet.workers.id
  }

  lifecycle {
    # The worker-pool resource manages these after initial cluster creation.
    ignore_changes = [flavor, worker_count, operating_system, zones]
  }
}

# Adopt only the default pool of the NEW cluster, so subsequent applies
# manage its size. Cluster deletion owns deleting this default pool.
resource "ibm_container_vpc_worker_pool" "default" {
  cluster           = ibm_container_vpc_cluster.cluster.id
  worker_pool_name  = "default"
  resource_group_id = data.ibm_resource_group.cluster.id
  vpc_id            = ibm_is_vpc.cluster.id
  flavor            = var.worker_flavor
  worker_count      = var.worker_count
  operating_system  = "RHCOS"
  import_on_create  = true
  orphan_on_delete  = true

  zones {
    name      = var.zone
    subnet_id = ibm_is_subnet.workers.id
  }
}
