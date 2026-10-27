# Shared Terraform state storage

**Operating `lts-2-2` or `v2-dev`? Use the existing bucket.** A fresh checkout
does not require a new bucket, a storage apply, or the bootstrap's local state.
Connecting with `kubectl`/`oc` does not require any state access at all.

## Use existing state storage

For the default shared-credential path, [log in to the intended account](../README.md#connect-to-a-cluster).
Run commands from the repository root. The tracked [`backend.hcl`](../backend.hcl)
already selects `skupper-tfstate-241b9a47-us-east` and the US East COS endpoint;
there is nothing to copy or generate on a fresh checkout. It contains no
credentials and deliberately omits the state key: `./cluster`
derives `clusters/v1.0/<cluster_name>.tfstate` from the selected inputs.
Never point an existing deployment at a new bucket/key as a workaround.

The default workflow uses the existing shared credential, not a new credential
per operator. It has two access requirements:

- **Your CLI identity** must be allowed to retrieve the existing service
  credential `skupper-tfstate-experiment`. Being able to see or connect to the
  clusters does not grant permission to retrieve it. Ask the account owner for
  access or use separately supplied credentials; do not create a new bucket.
- **The HMAC identity** must be able to list the bucket, read/write state, and
  read/write/delete its `.tflock` objects. Infrastructure operations also require
  the appropriate IBM provider permissions described in the [root guide](../README.md#infrastructure-prerequisites).

`./cluster` retrieves the shared credential without printing keys or writing
them into `backend.hcl`. Provider IAM tokens are needed only for `plan`/`apply`.
Inspect state:

```sh copy Copy LTS State Inspection
./cluster state-list clusters/lts-2-2.tfvars
```

```sh copy Copy Development State Inspection
./cluster state-list clusters/v2-dev.tfvars
```

These deployed clusters should have resources in state. If state is missing or
unexpectedly empty, stop and verify the account, bucket, and cluster name before
planning or applying. Do not provision duplicates. Continue with the
[reviewed plan/apply workflow](../README.md#manage-existing-infrastructure).
For separately supplied credentials or CI, see [Credentials and CI](#credentials-and-ci).

## Storage ownership and safety

This is a separate Terraform root: it creates only a dedicated standard COS
instance and a regional, versioned state bucket in `us-east`. It does not
create a cluster, a VPC, or credentials. No public-access policies are added.
COS encrypts objects at rest with IBM-managed keys by default; the backend
uses HTTPS. Storage and requests are billable.

The bootstrap's own state stays **local** in this directory. Back it up securely
outside Git. Do not store its only copy in the bucket it manages. Both the
instance and bucket have `prevent_destroy = true`; the bucket also disables
forced deletion. Those are Terraform safeguards, not protection against an
administrator deleting resources outside Terraform or removing configuration.
Versioning retains old state revisions; no expiry or immutable retention rules
are configured. COS Object Lock/WORM is not Terraform's state-lock mechanism.

## Bootstrap new storage

The tracked [`terraform.tfvars`](terraform.tfvars) records the existing bucket's
non-secret inputs. To manage it, use its original local state or restore a secure
backup before planning/applying. **Do not apply from empty state.**
For a deliberately new environment, choose a new globally unique bucket name
and separate bootstrap state; update `backend.hcl` only for that new environment.
Storage operations need provider credentials (`IBMCLOUD_API_KEY`, or both
`IC_IAM_TOKEN` and `IC_IAM_REFRESH_TOKEN`) and COS management permissions.

## Credentials and CI

Terraform's S3 backend needs **COS HMAC credentials**, not the IBM Cloud API
key used by the IBM provider. By default, `./cluster` retrieves the existing
`skupper-tfstate-experiment` credential, which has Writer access to the dedicated
state COS instance. No credential creation or manual exports are needed.
Its keys stay in IBM's Service credentials and the process environment, not Git.

Optionally, for CI or separate operator credentials, supply `access_key_id` as
`AWS_ACCESS_KEY_ID` and `secret_access_key` as `AWS_SECRET_ACCESS_KEY` through
your secret manager. These alone suffice for `init`, `output`, and `state-list`;
no CLI login is required. `plan`/`apply` additionally need provider access via
CLI login or `IBMCLOUD_API_KEY` (or `IC_API_KEY`). Remove unrelated AWS session,
profile, and role settings when supplying HMAC keys. Never put keys in backend
files, which Terraform caches in `.terraform/` and saved plans.

## Locking

Terraform S3 lockfiles are enabled (`use_lockfile = true`, Terraform >= 1.10).
Do not disable locking to bypass a conflict. Do not delete another operator's
lock or use `force-unlock` unless the original process is no longer running.

## State layout

The root `backend.tf` selects S3; `backend.hcl` supplies the bucket and endpoint.
`./cluster init clusters/lts-2-2.tfvars` initializes the backend without planning.
Initialization does not create cluster resources or state; a missing state file
is expected only for a deployment that has not yet been applied.

The command keeps a separate backend cache and saved plan per cluster under
`.terraform/clusters/` and uses the default workspace so prefixes cannot alter the key.
Two inputs files with the same cluster name intentionally target the same state.
Do not rename a deployed cluster or change `v1.0` without explicitly migrating
its existing state first; a new key is an empty deployment, not an upgrade.
Never overwrite another deployment's state. Operators must use the same
bucket/key, using the shared credential by default or an explicit HMAC override.
`backend.hcl` is tracked and contains only non-secret backend settings; credentials
must remain outside Git. Changing its bucket affects every deployment, so review
any such change as a deliberate state migration, not routine local setup.
Root applies/destroys do not own the storage resources.

## References

- [IBM's COS backend guidance](https://cloud.ibm.com/docs/terraform-on-ibm-cloud?topic=terraform-on-ibm-cloud-white-paper#how-to-store-terraform-state-in-a-cos-bucket)
- [IBM conditional requests](https://cloud.ibm.com/docs/cloud-object-storage?topic=cloud-object-storage-upload#upload-conditional)
- [HMAC credentials](https://cloud.ibm.com/docs/cloud-object-storage?topic=cloud-object-storage-uhc-hmac-credentials-main)
- [Terraform S3 backend and locking](https://developer.hashicorp.com/terraform/language/backend/s3)
