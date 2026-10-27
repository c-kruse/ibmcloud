# Shared non-secret backend settings; credentials must stay in the environment.
# ./cluster supplies clusters/v1.0/<cluster_name>.tfstate as the key during init.
bucket = "skupper-tfstate-241b9a47-us-east"
region = "us-east"
endpoints = {
  s3 = "https://s3.us-east.cloud-object-storage.appdomain.cloud"
}
use_path_style              = true
skip_region_validation      = true
skip_credentials_validation = true
skip_metadata_api_check     = true
skip_requesting_account_id  = true
skip_s3_checksum            = true
use_lockfile                = true
