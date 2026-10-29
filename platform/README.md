# OpenShift Pipelines platform setup

Terraform manages this platform separately from the IBM infrastructure. Nothing
here applies automatically when `./cluster apply` runs. No external CI runner
receives cluster credentials: an authenticated administrator provisions the
platform, and Skupper PipelineRuns execute inside the cluster.

## Installed status

On October 29, 2026, both Terraform stages were applied to **v2-dev and lts-2-2**
using the remote state keys below. Operator 1.24.2 and TektonConfig are ready;
the console plugin is enabled in both clusters. Development's install/upgrade run
`skupper-upgrade-jc97g` and independent test run `skupper-test-t29pg` passed all
four upstream CI scenarios. LTS was freshly installed as 2.2.4 by
`skupper-upgrade-w74t2`; inspect its PipelineRun for validation results.

The completed `skupper-ci/platform-validation-9wfn2` run is retained for inspection
in the OpenShift console: select `skupper-ci`, then **Pipelines → PipelineRuns**.
Two separate task pods wrote/read the report PVC with the unprivileged
`skupper-test` account, returning `storage-check=read-write-ok`; Results marked
the run stored. The validation file was removed from the PVC. The Results
database and report claims are each bound at 10Gi. This is platform validation,
not a Skupper installation or connectivity test.

## Ownership and stages

| Stage | State key in the existing COS bucket | Owns |
| --- | --- | --- |
| `bootstrap` | `platform/v1.0/<cluster>/bootstrap.tfstate` | OLM Subscription for Red Hat OpenShift Pipelines |
| `configuration` | `platform/v1.0/<cluster>/configuration.tfstate` | TektonConfig, console plugin, CI/test namespaces, RBAC, report PVC, runner build, scripts, Pipelines and schedules |

The existing `clusters/v1.0/<cluster>.tfstate` infrastructure state is unchanged.
Each stage has an independent provider lockfile and per-cluster local cache.
OLM owns the installed Operator; the Operator owns generated Tekton workloads.
Terraform adopts the Operator-created `TektonConfig/config` with an import block,
without forcing field-ownership conflicts. Review its import/update plan before
applying, especially if another administrator has configured Pipelines already.

Console plugin activation adopts `Console/cluster` and manages only `spec.plugins`.
It preserves currently enabled plugins and appends Pipelines; that atomic field
requires an explicit ownership transfer from the Console Operator. Other console
settings remain untouched. Removing Pipelines from the list requires changing
this configuration, since it deliberately ensures the plugin is present.

OpenShift-generated namespace allocations, reconciliation labels, service-account
annotations, and rotating image-pull secrets remain controller-owned. The
retention override includes the empty metadata object added by the Operator's
normalizer so the Terraform provider can reconcile its untyped field consistently.

Bootstrap must be applied and the Operator ready **before planning configuration**:
the Kubernetes provider needs custom-resource schemas at plan time. This cannot
be fixed by a Terraform `depends_on` in a single initial plan.

The initial channel is explicitly `pipelines-1.24`, available in both clusters'
`redhat-operators` catalogs when checked on October 29, 2026 (version 1.24.2).
`installPlanApproval = Automatic` permits installation and patch updates within
that channel. A minor-channel change is a reviewed Terraform edit in
`bootstrap/main.tf`; this is independent of Skupper upgrade policy. Changing to
Manual requires administrator approval of the initial and subsequent InstallPlans.
Catalog availability is not a guarantee of support entitlement; confirm IBM/Red
Hat support coverage before relying on it for production.

## Prerequisites

- Terraform >= 1.10 and < 2, Bash, GNU `realpath`/`sha256sum`, jq, kubectl.
- Authenticate to the intended cluster as an administrator following the
  [cluster access guide](../README.md#connect-to-a-cluster).
- Verify `platform/clusters/<cluster>.tfvars` names your authenticated context.
  These inputs are separate from infrastructure inputs. If you renamed a context,
  update this file. Optionally set `kubeconfig_path` to one local kubeconfig file;
  the default is `~/.kube/config`. The wrapper/provider do not use the active
  context or a merged `KUBECONFIG` environment variable.
- Backend HMAC access as described in [existing state storage](../state-storage/README.md#use-existing-state-storage).
  Like `./cluster`, the wrapper retrieves the existing service credential through
  IBM CLI login, or accepts `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`.
- The cluster must have OLM, the `redhat-operators` catalog, and a default storage
  class. Installing Pipelines creates cluster-wide controllers and additional
  resource consumption; provisioning the report PVC also creates billable storage.

Credentials and kubeconfigs stay outside Git. State and saved plans still require
the same restricted access as infrastructure state. Do not pass credentials through
Terraform variables or commit them in these inputs.

## Install one cluster (explicitly changes it)

Run from the repository root. Start with development; each apply needs review and
asks you to type the cluster name. **Do not run these commands merely to inspect
the installation.**

```sh copy Copy Bootstrap Plan
./platform/manage bootstrap plan v2-dev
```

Review the Subscription and selected channel, then apply the saved plan:

```sh copy Copy Bootstrap Apply
./platform/manage bootstrap apply v2-dev
./platform/manage bootstrap wait v2-dev
```

The wait command is read-only and uses no backend credentials. It waits for
TektonConfig creation/readiness and the Pipeline APIs; it does not install them.
If installation fails, inspect the Subscription, InstallPlan, and ClusterServiceVersion
in `openshift-operators` with the explicit context. Do not bypass the readiness gate.

```sh copy Copy Configuration Plan
./platform/manage configuration plan v2-dev
```

Review the singleton import, retention setting, accounts, RBAC, and report PVC:

```sh copy Copy Configuration Apply
./platform/manage configuration apply v2-dev
./platform/manage configuration wait v2-dev
```

Repeat the same sequence with `lts-2-2` after validating development. Supported
inspection commands are `init`, `output`, and `state-list` for either stage.
For example, `./platform/manage bootstrap state-list v2-dev` reads its state.

The wrapper saves plans under `.terraform/platform/<cluster>/<stage>/` and refuses
to apply if the inputs, stage Terraform files, provider lockfile, backend settings,
or actual cluster identity changed. A failed plan invalidates its previous saved
plan. It does not switch your current kubeconfig context. Do not use raw Terraform
commands that accidentally reuse another cluster's backend cache.

## Skupper pipelines

`skupper-upgrade` acquires the shared cluster Lease, resolves the selected release
bundle, verifies controller/router/adaptor tags match its published
digests, applies digest-pinned CRDs/controller resources, waits for controller and
existing router rollouts, then runs the CI scenarios as `skupper-test`. No automatic
rollback or CRD downgrade is attempted. An inconsistent publication fails before
applying anything. A healthy controller with failed E2E tests remains installed;
the PipelineRun fails visibly rather than claiming the upgrade is healthy.

`skupper-test` uses the same Lease and validates the installed controller without
upgrading it. Both paths hold the lock through diagnostics and cleanup and release
it in a Tekton `finally` task. Lock acquisition times out after five minutes;
there is no unsafe time-based lock stealing. A subsequent run can recover a
cancelled/deleted holder only after its PipelineRun is terminal and every pod has
stopped. Directly executing Tasks without owning the Lease is rejected.

Schedules are in-cluster CronJobs that **only submit PipelineRuns**, not a revival
of the old updater. Cadences and suspension are managed in `locals.policies` in
`configuration/pipelines.tf`; the shared test cadence is in the `schedule` resource.

| Cluster | Release policy | Upgrade checks (UTC) | Independent tests (UTC) |
| --- | --- | --- | --- |
| `v2-dev` | Published `v2-dev-release` bundle | Every 30 minutes, at :00/:30 | 00:15, 06:15, 12:15, 18:15 |
| `lts-2-2` | Highest numeric non-draft/non-prerelease `2.2.x` | Daily at 04:00 | 00:15, 06:15, 12:15, 18:15 |

Every successful install/reconcile also runs the full CI subset, even if the
release has not changed. A scheduler skips if either Skupper pipeline is already
active; manual races are serialized by the Lease. Set a new cluster policy's
`schedules_suspended = true` until building the runner and validating a full run;
an empty runner digest also forces suspension. Enable/suspend via Terraform,
not live CronJob edits. These triggers require no external webhook or inbound CI
access. Tekton Triggers is event-driven, not a clock; Red Hat also documents
[CronJob-based polling](https://www.redhat.com/en/blog/polling-triggers-in-openshift-pipeline).

LTS refuses to downgrade an identified 2.2.x installation or overwrite one with an
unknown/different release track. Both bundle and installed-version details are
recorded in upgrade results. The current initial LTS release is 2.2.4; future
2.2.x patches are selected automatically, but test-source updates are reviewed.

The test runner executes the four real upstream `ci-tests` scenarios:
**attached-connector, ha, hello-world, labels-and-annotations**. Development tests
are pinned to `3981c91c56aabcd603b5b2673252097bee570c4e`; LTS uses 2.2.4 source
commit `3153d06e2cff668a7c9fbc751857591320dcee13`, including its version-appropriate
labels/annotations assertions. Fixture images and per-cluster runner images are
digest-pinned too. Compared with `make ci-tests`, execution is
sequential and uses two or three namespaces on the **same cluster**, not external
cluster credentials. This proves local site/router/listener/connector/data-path
behavior, not connectivity between the two IBM clusters.

Terraform precreates seven dedicated `ci-e2e-*` namespaces. The runner adapts only
a temporary copy: it removes namespace lifecycle/upstream teardown, replaces
failure-masking must-gather rescue with a failing result, and defers exact-named
resource cleanup until diagnostics are saved. The pinned development hello-world
playbook applies both site directories on both hosts without a condition; the adaptation
adds the intended west/east conditions so a local backend cannot bypass the link.
The test assertions otherwise remain upstream. Sites and workloads are ephemeral:
between runs you should see the installed controller, but no persistent demo network.

**This is a dedicated test cluster.** The labels-and-annotations scenario briefly
changes a controller-wide label template. Preflight refuses preexisting templates
or Sites outside the dedicated test namespaces; do not use this suite unchanged
on a production/shared controller. Test credentials cannot write CRDs, namespaces,
or RBAC, and cannot modify workloads outside the test namespaces. They can write
ConfigMaps in `skupper` for this specific scenario and read basic cluster inventory.
Workload service accounts receive `nonroot-v2` admission only in test namespaces
because upstream curl explicitly requests UID 65534. No privileged/anyuid SCC is
granted. Installer credentials can manage Skupper CRDs/controller RBAC and should
be treated as highly privileged; scheduler/run-creation rights are trusted too.

### Build and start a run

After applying configuration, build the runner **inside the target cluster**:

```sh copy Copy Runner Build
oc --context v2-dev/dbgg7j4w0ifgskk49eig/admin -n skupper-ci start-build skupper-ci-runner --follow
```

The Dockerfile pins base images and direct Python/Ansible collection versions.
Build output stays in the internal OpenShift registry; no external registry push
or exposed CI webhook is needed. The BuildConfig has no automatic build triggers.
After a rebuild, review its output digest and update `local.runner_image` in
`configuration/pipelines.tf`, then plan/apply configuration before consuming it.
The content-derived build tag is not the runner's trust boundary: tasks use the
explicit digest. Update the corresponding policy's `runner_digest`, not the derived
`runner_image`. A new cluster requires its own build/digest validation. The scripts
are embedded in each PipelineRun's Task specifications, so an in-flight run keeps
its own code even if Terraform updates the pipeline before its finalizer executes.

Start either pipeline through its exact scheduler template, even when suspended:

```sh copy Copy Initial Skupper Install
oc --context v2-dev/dbgg7j4w0ifgskk49eig/admin -n skupper-ci create job \
  --from=cronjob/skupper-upgrade "manual-upgrade-$(date +%s)"
```

```sh copy Copy Independent Tests
oc --context v2-dev/dbgg7j4w0ifgskk49eig/admin -n skupper-ci create job \
  --from=cronjob/skupper-test "manual-test-$(date +%s)"
```

The scheduler intentionally skips an already-active run. Terraform's
`skupper_manual_runs` output also contains complete PipelineRun templates, including
the per-task service-account overrides; preserve those overrides when submitting
manually. Do not start the upgrade Pipeline with installer credentials on every task.

### Dashboard, version history and reports

In the existing OpenShift console select **skupper-ci → Pipelines → PipelineRuns**.
Runs show the task graph, outcomes, streamed Ansible logs, and JSON results with
the release/bundle hash, exact component images, test revision and scenario outcomes.
An unchanged development tag is distinguishable by its image digests. Results
retains run records for 30 days; the controller also carries the latest install
run/release/bundle annotations. A failed validation does not erase install history.

Full logs, JUnit XML, fixture pins and selected diagnostics are stored on the report
PVC under `<PipelineRun>/`; credentials/AccessTokens are excluded from diagnostics
and token-bearing Ansible tasks are masked. These files are not publicly served.
Administrators can read/copy them through a pod mounting `skupper-ci-artifacts`, or
from `/workspace/reports` in the validation pod while it is running. **No automatic
artifact pruning or report backup is configured**: monitor the 10Gi capacity and
archive/prune reports deliberately. This is not a separate report-portal deployment.

## Platform retention and operational limits

- The `all` Operator profile provides the OpenShift console Pipelines integration.
  The configuration stage explicitly enables its plugin, retaining other enabled
  plugins. Select `skupper-ci` in the console's Pipelines view; development has a
  platform-validation and Skupper runs.
- Tekton Results is enabled with a 30-day record-retention policy, pruned daily at
  03:00 UTC. The Operator's internal PostgreSQL is suitable for this development
  setup, **not a production-grade backed-up database**.
- **Results retention is not durable log retention.** Red Hat's documented log
  retrieval requires OpenShift Logging/Loki forwarding. This setup does not
  install that stack; pod logs remain subject to pod retention. Skupper pipelines
  explicitly save Ansible logs and JUnit reports on the artifact PVC.
- A protected `10Gi` ReadWriteOnce claim reserves report storage, using the default
  storage class. Binding is not awaited because some classes require a consuming
  pod. This is not an HTTP report server, backup, or artifact-pruning mechanism.
- The legacy Skupper installations/updaters were purged from both clusters on
  October 29, 2026, including CRDs and `ck-east`/`ck-west`. Both clusters were then
  freshly installed through Tekton.

Namespace, report PVC, Subscription, and TektonConfig have `prevent_destroy` guards.
These are not backups, nor protection if their resource blocks are removed. No
destroy command is exposed by the wrapper. Retirement must deliberately preserve
reports and account for Operator/OLM uninstallation semantics; do not delete the
CI namespace or the Operator-managed `openshift-pipelines` namespace casually.

## Local validation (no cluster changes)

```sh copy Copy Platform Checks
terraform fmt -check -recursive platform
terraform -chdir=platform/bootstrap init -backend=false
terraform -chdir=platform/bootstrap validate
terraform -chdir=platform/configuration init -backend=false
terraform -chdir=platform/configuration validate
shellcheck platform/manage
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s platform -v
```

The wrapper tests use the real Terraform HCL parser with fake backend/cluster
commands to exercise reviewed-plan safety. `validate` cannot validate absent
Tekton CRD schemas or prove Operator readiness; that requires the staged live
installation above.
