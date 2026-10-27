# Infrastructure for the latest/main workload track; workloads are deployed separately.
# OpenShift upgrades remain explicit, independent of the workload release track.
# Keep cluster_name stable: it also selects the remote state key.
cluster_name = "v2-dev"

region              = "us-east"
zone                = "us-east-1"
resource_group_name = "Default"
openshift_version   = "4.22_openshift"
worker_flavor       = "bx2.4x16"
worker_count        = 3

worker_subnet_cidr = "10.244.0.0/24"
# IBM reserves 172.18.0.0/16 internally; use a separate /18 in its supported range.
pod_subnet_cidr     = "172.17.64.0/18"
service_subnet_cidr = "172.22.0.0/16"
