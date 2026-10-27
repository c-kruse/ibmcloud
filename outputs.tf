output "cluster_id" {
  description = "IBM Cloud cluster ID."
  value       = ibm_container_vpc_cluster.cluster.id
}

output "public_api_url" {
  description = "Public Kubernetes API endpoint."
  value       = ibm_container_vpc_cluster.cluster.public_service_endpoint_url
}

output "private_api_url" {
  description = "Private Kubernetes API endpoint."
  value       = ibm_container_vpc_cluster.cluster.private_service_endpoint_url
}

output "ingress_hostname" {
  description = "Base domain for OpenShift routes."
  value       = ibm_container_vpc_cluster.cluster.ingress_hostname
}

output "vpc_id" {
  description = "New VPC ID."
  value       = ibm_is_vpc.cluster.id
}

output "worker_subnet_id" {
  description = "New worker subnet ID."
  value       = ibm_is_subnet.workers.id
}

output "configure_kubectl" {
  description = "Run after creation to download kubeconfig (changes your local Kubernetes configuration)."
  value       = "ibmcloud ks cluster config --cluster ${ibm_container_vpc_cluster.cluster.id} --admin"
}
