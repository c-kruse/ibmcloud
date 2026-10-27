# Infrastructure for the LTS 2.2 workload track; workloads are deployed separately.
# Keep cluster_name stable: it also selects the remote state key.
cluster_name = "lts-2-2"

region              = "us-east"
zone                = "us-east-1"
resource_group_name = "Default"
openshift_version   = "4.22_openshift"
worker_flavor       = "bx2.4x16"
worker_count        = 3

worker_subnet_cidr  = "10.243.0.0/24"
pod_subnet_cidr     = "172.17.0.0/18"
service_subnet_cidr = "172.21.0.0/16"
