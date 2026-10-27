# IBM Cloud OpenShift deployments

Manage two existing OpenShift clusters in **RedHat APE Skupper Dev**
(`241b9a47dd0848c1a38d8948255f3a1f`), region **us-east**, resource group **Default**.

| Cluster | Terraform inputs | Installed Skupper updater |
| --- | --- | --- |
| `lts-2-2` | [`clusters/lts-2-2.tfvars`](clusters/lts-2-2.tfvars) | Latest stable `2.2.x`, daily at 06:00 UTC |
| `v2-dev` | [`clusters/v2-dev.tfvars`](clusters/v2-dev.tfvars) | Published development bundle, every 30 minutes |

These deployments and updaters were installed on October 27, 2026; inspect live
status rather than treating this inventory as a health check. Terraform manages
infrastructure; the Skupper updater is installed separately with Kubernetes manifests.

**Choose your task (all commands run from the repository root):**

- **[Connect to a cluster](#connect-to-a-cluster):** no Terraform or state credentials needed.
- **[Inspect Skupper](skupper-updater/README.md#inspect-the-installed-updater):** schedules, Jobs, logs, and installed images.
- **[Manage existing infrastructure](#manage-existing-infrastructure):** configure backend access, inspect state, plan, then review before applying.
- **[Create another deployment](#create-another-deployment):** choose a new name and non-overlapping networks; reuse the existing state bucket.
- **[Bootstrap storage](state-storage/README.md#bootstrap-new-storage):** only for a new environment, not a fresh checkout of these deployments.

## Connect to a cluster

Install the [IBM Cloud CLI and Container Service plugin](https://cloud.ibm.com/docs/openshift?topic=openshift-cli-install)
and `kubectl` or `oc`. Cluster access does **not** require Terraform, `backend.hcl`,
or permission to retrieve state credentials.

```sh copy Copy Account Login
ibmcloud login --sso -c 241b9a47dd0848c1a38d8948255f3a1f -r us-east -g Default
```

For **normal user access**, open the target cluster's OpenShift web console
through IBM Cloud, choose **Copy Login Command → Display Token**, and run the
provided `oc login` command locally. Both `oc` and `kubectl` use that login.
Do not share or commit the token.

For **administrator access** (requires the IBM Cloud Administrator platform role),
download the target cluster's certificates:

```sh copy Copy LTS Admin Config
ibmcloud oc cluster config --cluster lts-2-2 --admin
```

Immediately identify the selected context and verify access:

```sh copy Copy Current Context
kubectl config current-context
```

```sh copy Copy Verify Access
kubectl get nodes
```

For development, download its configuration and repeat the two checks above:

```sh copy Copy Development Admin Config
ibmcloud oc cluster config --cluster v2-dev --admin
```

These downloads merge into your kubeconfig and select the corresponding context.
The administrator context normally ends in `/admin`. **Do not select an older
plain context with an empty user**: plain `cluster config` without `--admin`
does not complete the OpenShift login and can leave `users: null`, causing a
username prompt. Use `--admin` or the browser-based `oc login` flow, not your
IBM Cloud password at that prompt.

Aliases are optional. Immediately after selecting and verifying development's
authenticated context, you can give that exact context a convenient name:

```sh copy Copy Optional Development Alias
kubectl config rename-context "$(kubectl config current-context)" v2-dev-admin
```

Use a distinct alias such as `lts-2-2-admin` after selecting and verifying LTS.
List contexts with `kubectl config get-contexts`, and use `--context CONTEXT`
on commands to avoid acting on the wrong cluster. Keep kubeconfigs outside this
repository. See [IBM's OpenShift access instructions](https://cloud.ibm.com/docs/openshift?topic=openshift-cluster-access-public).

## Infrastructure prerequisites

- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.10 and < 2.0
  (S3 backend lockfiles require 1.10).
- Bash, [jq](https://jqlang.org/download/), GNU coreutils (`realpath`, `sha256sum`),
  and the IBM Cloud CLI. On macOS, install the GNU utilities and ensure these
  command names are on `PATH`.
- `./cluster` uses the CLI login to retrieve shared state HMAC credentials and,
  for `plan`/`apply`, provider IAM tokens. Alternatively supply HMAC keys as
  `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`; `init`, `output`, and `state-list`
  need no provider credentials or CLI login in that case. For `plan`/`apply`,
  an explicit `IBMCLOUD_API_KEY` (or `IC_API_KEY`) replaces CLI provider login.
- IAM permissions for the resource group, VPC infrastructure, Kubernetes /
  OpenShift service, and Cloud Object Storage, plus the account's required
  cluster-creation/API-key setup. The CLI user's ability to read the existing
  cluster does not establish permission to create new infrastructure.
- Sufficient quota and budget for the selected paid workers, OpenShift licenses,
  registry storage, public gateway/floating IP, ingress load balancer, and
  other associated usage. This is not a free-tier deployment.

## Manage existing infrastructure

**Do not run the storage bootstrap for these deployments.** The tracked
[`backend.hcl`](backend.hcl) already selects their existing state bucket,
`skupper-tfstate-241b9a47-us-east`. No copy or local backend setup is needed;
credentials stay outside Git. Follow [existing backend access](state-storage/README.md#use-existing-state-storage)
for permissions: the CLI login needs permission to retrieve the
`skupper-tfstate-experiment` service credential, whose HMAC identity needs state
bucket access. Cluster access alone is not sufficient. No bootstrap state file
is needed to manage the clusters.

The `clusters/` directory contains one Git-trackable inputs file per deployment.
Review the selected file before planning. LTS/development describe the intended workload
release tracks, not OpenShift support channels. Both explicitly select OpenShift
4.22; future OpenShift minor upgrades require reviewed edits. These files do not
install Skupper or automatically track its releases.

Commit these non-secret inputs alongside the shared Terraform code and
`.terraform.lock.hcl`; no separate Git branches or Terraform workspaces are
needed. Keep credentials, state, saved plans, and caches out of Git. For another
cluster, copy `terraform.tfvars.example` to `clusters/<cluster-name>.tfvars`,
choose a unique stable `cluster_name`, and review all inputs. Do not use
root `terraform.tfvars` or `*.auto.tfvars` for per-cluster settings: Terraform
loads those automatically, potentially mixing settings across deployments.

Log in using the account login command above. Inspect the existing deployment's
state before planning:

```sh copy Copy LTS State Inspection
./cluster state-list clusters/lts-2-2.tfvars
```

For these existing deployments, expect resources in state. If state is missing
or unexpectedly empty, stop and verify the bucket and cluster name; do not apply.

Then plan; this does not apply infrastructure changes:

```sh copy Copy LTS Plan
./cluster plan clusters/lts-2-2.tfvars
```

The command reads `cluster_name` using Terraform's HCL parser and automatically
selects `clusters/v1.0/<cluster_name>.tfstate` in the shared bucket. It initializes
the backend and saves a separate plan/cache under `.terraform/clusters/<cluster_name>/`.
The filename `clusters/lts-2-2.tfvars` is only an input filename; two files with the same
`cluster_name` target the same deployment.
Use this command rather than mixing raw Terraform plan/apply commands with it.
For the main workload track, use `./cluster plan clusters/v2-dev.tfvars` and
`./cluster apply clusters/v2-dev.tfvars`. Each command targets only that cluster.

Each independently managed cluster has one state containing its VPC, registry,
cluster, and worker pool. `v1.0` is a stable state-layout namespace, **not** the
OpenShift version or Terraform's internal state format. Renaming a cluster or
changing this prefix selects a different state; migrate existing state deliberately
instead of treating that as an upgrade.

Review the CLI's targeted account (or the account used by an explicit API key)
and every planned resource before applying.
The provider's region defaults here to `us-east`, independently of `ibmcloud target`.
Check available versions with `ibmcloud ks versions`; `4.22_openshift` selects
the available patch in that minor line. Future minor upgrades require an
explicit configuration change. IBM manages control-plane patch updates.

### Apply a reviewed plan (changes billable resources)

Only after reviewing and approving the plan:

```sh copy Copy LTS Apply
./cluster apply clusters/lts-2-2.tfvars
```

This uses the saved plan for that cluster and asks you to type the cluster name
before applying. It refuses to apply if the selected inputs file has changed
since planning or if the saved plan belongs to another cluster. A failed
Terraform plan removes the previous saved plan so it cannot accidentally be applied.
Regenerate the plan after changing shared Terraform code or provider versions:
the inputs hash checks the selected vars file, not those other files.

Creation waits for ingress readiness and can take an hour or more. Afterward,
follow [Connect to a cluster](#connect-to-a-cluster).

Use `./cluster state-list clusters/lts-2-2.tfvars` to inspect that cluster's resources, or
`./cluster init clusters/lts-2-2.tfvars` to initialize its backend without planning.

## Create another deployment

For a new cluster in this account, reuse the existing backend setup above; do
not bootstrap another state bucket. Copy `terraform.tfvars.example` to
`clusters/<new-cluster-name>.tfvars`, choose a unique stable `cluster_name` and
non-overlapping worker, pod, and service ranges, then use `./cluster plan` and
`./cluster apply` with that file. Review the full plan and budget first.
Terraform creates infrastructure only; install a matching Skupper updater
separately using the [bootstrap guide](skupper-updater/README.md#publish-and-bootstrap-changes-the-selected-cluster).

For a different account or a new storage environment, first follow
[Bootstrap new storage](state-storage/README.md#bootstrap-new-storage).

## What it creates

- A VPC, explicit address prefix, /24 worker subnet, and public gateway.
- A standard Cloud Object Storage instance for the OpenShift image registry.
- An IBM-managed OpenShift 4.22 cluster. Variable defaults, the generic example,
  and both supplied cluster inputs files select three `bx2.4x16` RHCOS workers
  (12 vCPUs / 48 GB RAM nominal total), all in the US East `us-east-1` zone.
- Public and private API endpoints, secure-by-default networking, and public
  outbound access. The CNI is left at IBM's default. No Cloud Pak entitlement
  is claimed; OpenShift licensing is billed normally.

The existing `Default` resource group is looked up, not created or owned.
Each deployment owns its own VPC, registry storage instance, cluster, and worker pool.
Networking uses IBM's default VPC ACL/security group and the managed cluster's
secure-by-default configuration, with public API access and outbound traffic enabled.

Terraform does **not** install Skupper, Network Observer, demo applications,
user workload monitoring, or application routes. For the separate Skupper
installation and automatic upgrades, see the [updater guide](skupper-updater/README.md).

## Operational notes

- **Availability:** this is a single-zone deployment, not a multi-zone production
  design. Adding zones increases the total worker
  count because IBM's `worker_count` is **per zone**.
- **Worker management:** the cluster creates its default pool, and the dedicated
  worker-pool resource adopts that new pool so later applies can resize it.
  Profile changes may replace the pool; review those plans carefully.
- **Networking:** LTS retains its existing CIDRs. `v2-dev` has distinct
  worker, pod, and service ranges to avoid overlaps between these deployments.
  Its pod range is `172.17.64.0/18`, separate from LTS's `172.17.0.0/18`.
  IBM prohibits `172.16.0.0/16`, `172.18.0.0/16`, `172.19.0.0/16`, and
  `172.20.0.0/16`; check its [supported subnet ranges](https://cloud.ibm.com/docs/openshift?topic=openshift-vpc-subnets)
  before allocating ranges for another cluster. Terraform planning alone does
  not validate all service-side network restrictions.
  Check other connected networks before provisioning or peering/VPN setup.
  Changing pod/service CIDRs requires cluster replacement.
- **State:** the cluster uses a shared COS bucket through the S3 backend with
  lockfiles. Keep `.terraform.lock.hcl` in version control, but never state,
  credentials, or saved plans. The separate
  [state-storage bootstrap](state-storage/README.md) owns the versioned US East
  bucket and retains its own local state. Back up that bootstrap state securely
  outside Git; do not keep its only copy in the bucket it manages.
- **Deletion:** `terraform destroy` is destructive. Persistent storage is not
  forcibly deleted (`force_delete_storage = false`), and registry buckets/data
  can outlive a cluster or prevent COS deletion. Back up data and review cleanup
  separately; the default pool is removed by cluster deletion, not independently.

## References

- [IBM VPC cluster resource](https://registry.terraform.io/providers/IBM-Cloud/ibm/latest/docs/resources/container_vpc_cluster)
- [IBM default worker pool management](https://registry.terraform.io/providers/IBM-Cloud/ibm/latest/docs/resources/container_vpc_worker_pool)
- [Creating OpenShift VPC clusters](https://cloud.ibm.com/docs/openshift?topic=openshift-cluster-create-vpc-gen2)
