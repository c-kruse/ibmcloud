# Skupper install/upgrade CronJobs

One updater runs independently in each cluster. No Git host, external CI runner,
IBM API key, or kubeconfig is needed inside the Job: it uses its local service
account. This installs the **cluster-scoped upstream Skupper controller and CRDs**,
not the OpenShift operator, Network Observer, sites, links, or demo applications.
Do not run it alongside Helm, OLM, or GitOps managing the same installation.

| Overlay | Policy | Schedule (UTC) |
| --- | --- | --- |
| `overlays/lts-2-2` | Highest stable, published `2.2.x` release only | Daily at 06:00 |
| `overlays/v2-dev` | Published development bundle, checked against current Quay digests | Every 30 minutes |

## Inspect the installed updater

Both existing clusters already have the updater installed. You do **not** need
to build an image or apply the bootstrap overlay just to inspect it.
First [authenticate to the target cluster](../README.md#connect-to-a-cluster).
Use `kubectl config get-contexts` to find its authenticated context; replace
`CONTEXT` in every command below with that exact name (or a verified alias such
as `v2-dev-admin`). Check the cluster you are targeting, not just the active marker.
All commands run from the repository root.

Check the schedule, suspension state, and retained Job results (read-only):

```sh copy Copy Updater Status
kubectl --context CONTEXT -n skupper-updater get cronjob,jobs
```

List Jobs in creation order, then replace `JOB_NAME` in the log command with the
run you want to inspect:

```sh copy Copy Recent Jobs
kubectl --context CONTEXT -n skupper-updater get jobs --sort-by=.metadata.creationTimestamp
```

```sh copy Copy Selected Job Logs
kubectl --context CONTEXT -n skupper-updater logs job/JOB_NAME --timestamps=true
```

Inspect controller readiness, its release annotation `updater.skupper.io/release`,
and the exact controller image and router/adaptor environment settings:

```sh copy Copy Installed Skupper Details
kubectl --context CONTEXT -n skupper describe deployment skupper-controller
```

Successful logs end with `Healthy: ...`. A run with unchanged desired manifests
still server-side-applies resources and checks readiness, but does not roll or
restart unchanged workloads. `successfully rolled out` is a readiness check,
not evidence of a new rollout. Zero site routers checked means no router
Deployments with the Skupper router label were found; it does not prove site
traffic or connectivity.

## Run or pause the updater (changes the selected cluster)

Normally let the CronJob run on its schedule. A manual run can **install or
upgrade Skupper**, not just check for updates. Before triggering one, inspect
Jobs for active runs and choose a window clear of the next scheduled run.
CronJob `Forbid` does **not** prevent overlap with manually created Jobs.
For a dry-run preview without cluster changes, see [Preview](#preview-without-changing-a-cluster).

Use a unique Job name for each manual run (including an immediate first run):

```sh copy Copy Manual Update Job
kubectl --context CONTEXT -n skupper-updater create job "skupper-updater-manual-$(date -u +%Y%m%d%H%M%S)" --from=cronjob/skupper-updater
```

Use the created name as `JOB_NAME` in the log command above. To follow its logs,
add `-f`. Inspect the Job status as well as its logs; retries can produce more
than one pod.

To pause future schedules without uninstalling Skupper or stopping an already
running Job:

```sh copy Copy Pause Updater
kubectl --context CONTEXT -n skupper-updater patch cronjob skupper-updater --type=merge -p '{"spec":{"suspend":true}}'
```

To resume, run the same patch with `false`. Resuming can trigger a missed run;
review the Job list first.

## How it works

The updater downloads `skupper-cluster-scope.yaml` and `manifest.json` from
the selected official GitHub release. For development, that is the rolling
`v2-dev-release` prerelease; LTS selects the highest numeric stable 2.2 patch,
excluding drafts, prereleases, and other minor versions. It does not use the
unrestricted GitHub `latest` URL.

Controller, kube-adaptor, and router images are pinned to the release bundle's
multi-architecture digests. Development runs also check Quay's current tag
digests: if any differs from the bundle, the run fails **before changing the
cluster**, allowing a later schedule to retry after publication finishes. This
intentionally waits for Skupper's published bundle; it does not immediately
deploy a router-only build or tag updates without corresponding published
artifacts. It does not independently check CI status.
The upstream development router tag is `main`, not `v2-dev`.

CRDs are applied and awaited before the remaining installation. Server-side
apply reconciles drift without forcing ownership conflicts or pruning resources.
An unchanged desired pod template does not cause a restart. Changed image digests
update the controller pod template, including its router/adaptor image settings.
After controller readiness, the updater waits for existing labeled router
Deployments to adopt those images and finish rolling out. With no sites, it
reports zero routers checked; it does not create a site as a side effect.
Readiness checks are not an application traffic or cross-cluster connectivity test.

The controller Deployment records the release in `updater.skupper.io/release`;
its image and environment record the exact digests. Job logs report selected
images and rollout results. LTS refuses downgrades and refuses to automatically
migrate an unidentifiable or non-2.2 installation. Network/download/API failures
fail the Job rather than falling back to some other release line.

## Security and operating limits

**This is a powerful installer identity.** It can manage CRDs, create cluster
RBAC objects, and bind/escalate the named `skupper-controller` role. It can manage
the installation's Deployments and service accounts in `skupper`, and read
Deployments cluster-wide for rollout checks. It is not bound to `cluster-admin`,
but trusting the updater and the upstream release publisher is essential.
Review `base/rbac.yaml` before installing. The namespace-scoped `skupper` resources
are reserved for the upstream installation; workload sites can live elsewhere.

The updater runs non-root with a read-only root filesystem, dropped capabilities,
and a bounded writable `/tmp`. No privileged SCC is required. It needs HTTPS
egress to GitHub, GitHub release asset hosts, and Quay, plus Kubernetes API access.
Public upstream downloads need no credentials. If necessary, inject a read-only
`GITHUB_TOKEN` from a Secret to increase GitHub API rate limits; never commit it.

Scheduled runs do not overlap (`Forbid`), retry once, and have a 30-minute total
deadline. Manual Jobs are **not** protected by CronJob concurrency policy: wait
for existing runs to finish before triggering one. Two successful and three
failed Jobs are retained. There is no automatic rollback, CRD rollback, or
external alerting; monitor failed Jobs and controller/site health. A failure
after CRD/controller application can leave a partially upgraded installation;
inspect it, then rerun after resolving the issue. Do not blindly downgrade CRDs.

## Preview without changing a cluster

Run the published image pinned in the CronJob. No build or kubeconfig is needed:

```sh copy Copy LTS Preview Command
podman run --rm quay.io/ckruse/skupper-updater@sha256:02065b56cb6ba22874eccde88f2f5f100400fc8296271ab825db8114e34cb22c --track lts --dry-run
```

```sh copy Copy Development Preview Command
podman run --rm quay.io/ckruse/skupper-updater@sha256:02065b56cb6ba22874eccde88f2f5f100400fc8296271ab825db8114e34cb22c --track v2-dev --dry-run
```

`--dry-run` resolves real upstream versions and digests and prints the desired
installer YAML without connecting to Kubernetes.

## Changing updater code

From the repository root, build a local image; tests require Python 3 with PyYAML 6.0.2:

```sh copy Copy Build Command
podman build -t localhost/skupper-updater:dev skupper-updater
```

```sh copy Copy Test Command
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skupper-updater -v
```

## Publish and bootstrap (changes the selected cluster)

Version `0.1.0` is published at `quay.io/ckruse/skupper-updater:0.1.0`.
The shared CronJob base pins its immutable manifest digest. The repository is
public, so neither cluster needs an image-pull Secret. This build is Linux/amd64,
matching the selected worker flavor. Publishing did not install the updater.

1. For future updater changes, build and push a new version, then update the
   fully qualified **digest reference** in `base/cronjob.yaml`. If switching to
   a private repository, configure an image-pull Secret in `skupper-updater`
   and reference it in the pod spec. Publishing the image and applying these
   manifests are separate actions; neither is done by Terraform or by the
   local build.
2. Check `kubectl config get-contexts` and substitute the correct context below.
   Render/review with `kubectl kustomize skupper-updater/overlays/lts-2-2` (or
   `v2-dev`). Applying the overlay grants the installer permissions and enables
   automatic installation/upgrades on that cluster's next schedule.
3. Apply as a cluster administrator, to **one explicitly selected cluster**:

```sh copy Copy LTS Bootstrap Command
kubectl --context LTS_CONTEXT apply -k skupper-updater/overlays/lts-2-2
```

```sh copy Copy Development Bootstrap Command
kubectl --context DEV_CONTEXT apply -k skupper-updater/overlays/v2-dev
```

After bootstrap, wait for the next scheduled run, or follow
[Run or pause the updater](#run-or-pause-the-updater-changes-the-selected-cluster)
for a uniquely named immediate first run and its logs. Both existing deployments
have already been bootstrapped; do not recreate a fixed `skupper-updater-initial`
Job for routine reruns.

**Do not use `kubectl delete -k` on these overlays to remove only the updater:**
the bootstrap includes the `skupper` Namespace, and deleting that namespace
would delete its contents. Remove only the updater's CronJob, service account,
RBAC, and `skupper-updater` namespace if retiring the automation. Preserve the
Skupper installation, CRDs, and workload namespaces.
