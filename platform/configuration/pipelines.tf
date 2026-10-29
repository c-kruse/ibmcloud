locals {
  policies = {
    v2-dev = {
      track               = "v2-dev"
      test_revision       = "3981c91c56aabcd603b5b2673252097bee570c4e"
      upgrade_schedule    = "*/30 * * * *"
      schedules_suspended = false
      runner_digest       = "sha256:a527cb69fc0d40fd26cfb877c23c69984057fc8536f5387b5b1c2fa34a32fb06"
    }
    lts-2-2 = {
      track               = "lts-2-2"
      test_revision       = "3153d06e2cff668a7c9fbc751857591320dcee13"
      upgrade_schedule    = "0 4 * * *"
      schedules_suspended = false
      runner_digest       = "sha256:4bc2092d18322c42c54a7b2111b8d788e080bc43a5306c876a7463dd3f0911a3"
    }
  }
  ci_enabled = contains(keys(local.policies), var.cluster_name)
  policy     = lookup(local.policies, var.cluster_name, local.policies["v2-dev"])
  ci_count   = local.ci_enabled ? 1 : 0
  test_namespaces = toset(local.ci_enabled ? [
    "ci-e2e-west", "ci-e2e-east", "ci-e2e-westha", "ci-e2e-eastha",
    "ci-e2e-iperf3-hub", "ci-e2e-iperf3-client", "ci-e2e-iperf3-workload",
  ] : [])
  scripts    = { for f in fileset(path.module, "scripts/*.py") : basename(f) => file("${path.module}/${f}") }
  runner_tag = substr(filesha256("${path.module}/Dockerfile"), 0, 16)
  # Pin the successful in-cluster build. Rebuilding the Dockerfile requires
  # reviewing and updating this digest before runners consume the new image.
  runner_image = "image-registry.openshift-image-registry.svc:5000/skupper-ci/runner${local.policy.runner_digest != "" ? "@${local.policy.runner_digest}" : ":${local.runner_tag}"}"
  task_spec = {
    metadata   = {}
    spec       = null
    params     = [{ name = "action", type = "string" }]
    workspaces = [{ name = "reports", optional = true }]
    results    = [{ name = "summary", type = "string", description = "Release pins or tested revision and outcome (JSON)" }]
    steps = [{
      name  = "run"
      image = local.runner_image
      # Snapshot scripts inside the PipelineRun, so updating Terraform cannot
      # delete a ConfigMap still needed by an in-flight run's finally task.
      script = <<-PYTHON
        #!/usr/bin/env python3
        import base64, os, pathlib, sys
        sources = ${jsonencode({ for name, source in local.scripts : name => base64encode(source) })}
        directory = pathlib.Path('/tmp/scripts')
        directory.mkdir()
        for name, source in sources.items():
            (directory / name).write_bytes(base64.b64decode(source))
        os.execv(sys.executable, [sys.executable, str(directory / 'run.py'), '$(params.action)'])
        PYTHON
      env = [
        { name = "HOME", value = "/tmp/home" },
        { name = "RELEASE_TRACK", value = local.policy.track },
        { name = "TEST_REVISION", value = local.policy.test_revision },
        { name = "SKUPPER_E2E_EXCLUSIVE_CONTROLLER", value = "true" },
        { name = "RUN_ID", value = "$(context.pipelineRun.name)" },
        { name = "REPORT_DIR", value = "$(workspaces.reports.path)/$(context.pipelineRun.name)" },
        { name = "RESULT_PATH", value = "$(results.summary.path)" },
      ]
      computeResources = {
        requests = { cpu = "100m", memory = "256Mi" }
        limits   = { cpu = "2", memory = "2Gi" }
      }
    }]
  }
  pipeline_specs = {
    for mode in ["upgrade", "test"] : mode => {
      description = mode == "upgrade" ? "Install ${local.policy.track}, check rollout, then run upstream CI scenarios" : "Test deployed Skupper using upstream CI scenarios without upgrading"
      workspaces  = [{ name = "reports" }]
      tasks = concat(
        [{ name = "lock", taskSpec = local.task_spec, params = [{ name = "action", value = "lock" }] }],
        mode == "upgrade" ? [{
          name   = "install", runAfter = ["lock"], taskSpec = local.task_spec,
          params = [{ name = "action", value = "install" }], workspaces = [{ name = "reports", workspace = "reports" }]
        }] : [],
        [{
          name   = "validate", runAfter = [mode == "upgrade" ? "install" : "lock"], taskSpec = local.task_spec,
          params = [{ name = "action", value = "test" }], workspaces = [{ name = "reports", workspace = "reports" }]
        }],
      )
      finally = [{ name = "unlock", taskSpec = local.task_spec, params = [{ name = "action", value = "unlock" }] }]
      results = concat(
        mode == "upgrade" ? [{ name = "release", description = "", value = "$(tasks.install.results.summary)" }] : [],
        [{ name = "tests", description = "", value = "$(tasks.validate.results.summary)" }],
      )
    }
  }
  run_specs = {
    for mode in ["upgrade", "test"] : mode => {
      pipelineRef     = { name = "skupper-${mode}" }
      taskRunTemplate = { serviceAccountName = "skupper-${mode}" }
      # Upgrade privileges never reach the test task or finalizer.
      taskRunSpecs = mode == "upgrade" ? [
        { pipelineTaskName = "validate", serviceAccountName = "skupper-test" },
        { pipelineTaskName = "unlock", serviceAccountName = "skupper-test" },
      ] : []
      workspaces = [{ name = "reports", persistentVolumeClaim = { claimName = "skupper-ci-artifacts" } }]
      timeouts   = { pipeline = "2h0m0s", tasks = "1h55m0s", finally = "5m0s" }
    }
  }
}

resource "kubernetes_namespace_v1" "skupper" {
  count = local.ci_count
  metadata { name = "skupper" }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [metadata[0].annotations, metadata[0].labels]
  }
}

resource "kubernetes_namespace_v1" "test" {
  for_each = local.test_namespaces
  metadata {
    name   = each.key
    labels = { "ci.skupper.io/test-namespace" = "true" }
  }
  lifecycle {
    prevent_destroy = true
    ignore_changes  = [metadata[0].annotations, metadata[0].labels]
  }
}

resource "kubernetes_manifest" "runner_image" {
  count = local.ci_count
  manifest = {
    apiVersion = "image.openshift.io/v1", kind = "ImageStream"
    metadata   = { name = "runner", namespace = "skupper-ci" }
    spec       = { lookupPolicy = { local = false } }
  }
  depends_on = [kubernetes_namespace_v1.ci]
}

resource "kubernetes_manifest" "runner_build" {
  count = local.ci_count
  manifest = {
    apiVersion = "build.openshift.io/v1", kind = "BuildConfig"
    metadata   = { name = "skupper-ci-runner", namespace = "skupper-ci" }
    spec = {
      runPolicy                    = "Serial"
      source                       = { type = "Dockerfile", dockerfile = file("${path.module}/Dockerfile") }
      strategy                     = { type = "Docker", dockerStrategy = {} }
      output                       = { to = { kind = "ImageStreamTag", name = "runner:${local.runner_tag}" } }
      successfulBuildsHistoryLimit = 2
      failedBuildsHistoryLimit     = 2
    }
  }
  depends_on = [kubernetes_manifest.runner_image]
}

resource "kubernetes_manifest" "lease" {
  count = local.ci_count
  manifest = {
    apiVersion = "coordination.k8s.io/v1", kind = "Lease"
    metadata   = { name = "skupper-ci-execution", namespace = "skupper-ci" }
    spec       = {}
  }
  computed_fields = ["spec"]
  depends_on      = [kubernetes_namespace_v1.ci]
}

resource "kubernetes_role_v1" "execution" {
  count = local.ci_count
  metadata {
    name      = "skupper-ci-execution"
    namespace = "skupper-ci"
  }
  rule {
    api_groups     = ["coordination.k8s.io"]
    resources      = ["leases"]
    resource_names = ["skupper-ci-execution"]
    verbs          = ["get", "update"]
  }
  rule {
    api_groups = ["tekton.dev"]
    resources  = ["pipelineruns"]
    verbs      = ["get"]
  }
  rule {
    api_groups = [""]
    resources  = ["pods"]
    verbs      = ["get", "list"]
  }
}

resource "kubernetes_role_binding_v1" "execution" {
  for_each = local.ci_enabled ? toset(["skupper-test", "skupper-upgrade"]) : toset([])
  metadata {
    name      = "${each.key}-execution"
    namespace = "skupper-ci"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.execution[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = each.key
    namespace = "skupper-ci"
  }
}

resource "kubernetes_cluster_role_v1" "test" {
  count = local.ci_count
  metadata { name = "skupper-ci-test-workloads" }
  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log", "pods/exec", "services", "configmaps", "secrets", "serviceaccounts", "events"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete", "deletecollection"]
  }
  rule {
    api_groups = ["apps", "batch", "skupper.io", "route.openshift.io"]
    resources  = ["deployments", "replicasets", "jobs", "sites", "listeners", "connectors", "links", "accessgrants", "accesstokens", "routeraccesses", "securedaccesses", "certificates", "attachedconnectors", "attachedconnectorbindings", "routes"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete", "deletecollection"]
  }
}

resource "kubernetes_role_binding_v1" "test" {
  for_each = local.test_namespaces
  metadata {
    name      = "skupper-ci-test"
    namespace = each.key
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.test[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = "skupper-test"
    namespace = "skupper-ci"
  }
  depends_on = [kubernetes_namespace_v1.test]
}

resource "kubernetes_cluster_role_v1" "test_scc" {
  count = local.ci_count
  metadata { name = "skupper-ci-test-scc" }
  rule {
    api_groups     = ["security.openshift.io"]
    resources      = ["securitycontextconstraints"]
    resource_names = ["nonroot-v2"]
    verbs          = ["use"]
  }
}

resource "kubernetes_role_binding_v1" "test_scc" {
  for_each = local.test_namespaces
  metadata {
    name      = "skupper-ci-test-scc"
    namespace = each.key
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.test_scc[0].metadata[0].name
  }
  # Upstream test workloads use fixed nonroot UIDs outside OpenShift's allocated
  # namespace ranges. No privileged/anyuid SCC is granted.
  subject {
    kind      = "ServiceAccount"
    name      = "skupper-test"
    namespace = "skupper-ci"
  }
  subject {
    kind      = "Group"
    name      = "system:serviceaccounts:${each.key}"
    api_group = "rbac.authorization.k8s.io"
  }
  depends_on = [kubernetes_namespace_v1.test]
}

resource "kubernetes_cluster_role_v1" "observe" {
  count = local.ci_count
  metadata { name = "skupper-ci-observe" }
  rule {
    api_groups = [""]
    resources  = ["nodes", "namespaces", "configmaps"]
    verbs      = ["get", "list", "watch"]
  }
  rule {
    api_groups = ["apps"]
    resources  = ["deployments"]
    verbs      = ["get", "list", "watch"]
  }
  rule {
    api_groups = ["skupper.io"]
    resources  = ["sites"]
    verbs      = ["get", "list", "watch"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "observe" {
  count = local.ci_count
  metadata { name = "skupper-ci-observe" }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.observe[0].metadata[0].name
  }
  dynamic "subject" {
    for_each = ["skupper-test", "skupper-upgrade"]
    content {
      kind      = "ServiceAccount"
      name      = subject.value
      namespace = "skupper-ci"
    }
  }
}

resource "kubernetes_role_v1" "controller" {
  count = local.ci_count
  metadata {
    name      = "skupper-ci-installer"
    namespace = "skupper"
  }
  rule {
    api_groups = ["", "apps"]
    resources  = ["serviceaccounts", "deployments"]
    verbs      = ["get", "list", "watch", "create", "update", "patch"]
  }
  depends_on = [kubernetes_namespace_v1.skupper]
}

resource "kubernetes_role_binding_v1" "controller" {
  count = local.ci_count
  metadata {
    name      = "skupper-ci-installer"
    namespace = "skupper"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.controller[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = "skupper-upgrade"
    namespace = "skupper-ci"
  }
}

resource "kubernetes_role_v1" "label_test" {
  count = local.ci_count
  metadata {
    name      = "skupper-ci-label-test"
    namespace = "skupper"
  }
  rule {
    api_groups = [""]
    resources  = ["configmaps"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }
  depends_on = [kubernetes_namespace_v1.skupper]
}

resource "kubernetes_role_binding_v1" "label_test" {
  count = local.ci_count
  metadata {
    name      = "skupper-ci-label-test"
    namespace = "skupper"
  }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role_v1.label_test[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = "skupper-test"
    namespace = "skupper-ci"
  }
}

resource "kubernetes_cluster_role_v1" "installer" {
  count = local.ci_count
  metadata { name = "skupper-ci-installer" }
  rule {
    api_groups = ["apiextensions.k8s.io"]
    resources  = ["customresourcedefinitions"]
    verbs      = ["create"]
  }
  rule {
    api_groups     = ["apiextensions.k8s.io"]
    resources      = ["customresourcedefinitions"]
    resource_names = [for r in ["accessgrants", "accesstokens", "attachedconnectorbindings", "attachedconnectors", "certificates", "connectors", "links", "listeners", "multikeylisteners", "routeraccesses", "securedaccesses", "sites"] : "${r}.skupper.io"]
    verbs          = ["get", "list", "watch", "update", "patch"]
  }
  rule {
    api_groups = ["rbac.authorization.k8s.io"]
    resources  = ["clusterroles", "clusterrolebindings"]
    verbs      = ["create"]
  }
  rule {
    api_groups     = ["rbac.authorization.k8s.io"]
    resources      = ["clusterroles", "clusterrolebindings"]
    resource_names = ["skupper-controller"]
    verbs          = ["get", "update", "patch", "escalate", "bind"]
  }
}

resource "kubernetes_cluster_role_binding_v1" "installer" {
  count = local.ci_count
  metadata { name = "skupper-ci-installer" }
  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role_v1.installer[0].metadata[0].name
  }
  subject {
    kind      = "ServiceAccount"
    name      = "skupper-upgrade"
    namespace = "skupper-ci"
  }
}

resource "kubernetes_manifest" "pipeline" {
  for_each = { for mode, spec in local.pipeline_specs : mode => spec if local.ci_enabled }
  manifest = {
    apiVersion = "tekton.dev/v1", kind = "Pipeline"
    metadata   = { name = "skupper-${each.key}", namespace = "skupper-ci" }
    spec       = each.value
  }
  depends_on = [kubernetes_namespace_v1.ci]
}

resource "kubernetes_manifest" "schedule" {
  for_each = local.ci_enabled ? { upgrade = local.policy.upgrade_schedule, test = "15 */6 * * *" } : {}
  manifest = {
    apiVersion = "batch/v1", kind = "CronJob"
    metadata   = { name = "skupper-${each.key}", namespace = "skupper-ci" }
    spec = {
      schedule                   = each.value, timeZone = "Etc/UTC", suspend = local.policy.schedules_suspended || local.policy.runner_digest == ""
      concurrencyPolicy          = "Forbid", startingDeadlineSeconds = 300
      successfulJobsHistoryLimit = 2, failedJobsHistoryLimit = 2
      jobTemplate = { spec = {
        backoffLimit = 0, activeDeadlineSeconds = 120
        template = { spec = {
          serviceAccountName = "skupper-scheduler", restartPolicy = "Never"
          containers = [{
            name = "submit", image = local.runner_image
            # A shell here feeds an exact, Terraform-rendered PipelineRun. No
            # cluster credentials or external trigger endpoints are involved.
            command = ["/bin/sh", "-ec"]
            args = [<<-SCRIPT
              kubectl -n skupper-ci get pipelineruns -o json > /tmp/runs.json
              if ! python3 -c 'import json; r=json.load(open("/tmp/runs.json")); raise SystemExit(any(x["spec"].get("pipelineRef",{}).get("name") in ("skupper-upgrade","skupper-test") and not x.get("status",{}).get("completionTime") for x in r["items"]))'; then
                echo 'Skipping scheduled run: a Skupper PipelineRun is still active'
                exit 0
              fi
              cat <<'RUN' | kubectl create -f -
              ${jsonencode({ apiVersion = "tekton.dev/v1", kind = "PipelineRun", metadata = { generateName = "skupper-${each.key}-", namespace = "skupper-ci" }, spec = local.run_specs[each.key] })}
              RUN
              SCRIPT
            ]
          }]
        } }
      } }
    }
  }
  depends_on = [kubernetes_manifest.pipeline]
}

output "skupper_manual_runs" {
  description = "Trusted run templates; submit with kubectl create -f - in the explicit target context."
  value = { for mode, spec in local.run_specs : mode => {
    apiVersion = "tekton.dev/v1", kind = "PipelineRun"
    metadata   = { generateName = "skupper-${mode}-", namespace = "skupper-ci" }
    spec       = spec
  } if local.ci_enabled }
}
