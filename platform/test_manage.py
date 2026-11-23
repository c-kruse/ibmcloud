"""Exercise the public wrapper without backend credentials or cluster writes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class ManageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        source = Path(__file__).resolve().parent
        shutil.copytree(source, self.repo / "platform", ignore=shutil.ignore_patterns(".terraform", "__pycache__"))
        (self.repo / "platform/clusters/lts-2-2.tfvars").write_text(
            'cluster_name = "lts-2-2"\nkube_context = "lts-2-2/test/admin"\n'
        )
        shutil.copy(source.parent / "backend.hcl", self.repo)
        self.bin = self.repo / "bin"
        self.bin.mkdir()
        self.trace = self.repo / "trace"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
                        REAL_TERRAFORM=shutil.which("terraform"), TRACE=str(self.trace),
                        CLUSTER_UID="cluster-a", TEST_ROOT=str(self.repo),
                        IMAGE_DIGEST="sha256:" + "a1" * 32)
        self.env.pop("TF_VAR_runner_digest", None)
        self.env.pop("TF_VAR_schedules_suspended", None)
        self.write_tool("terraform", '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
action = args[1]
if action == "console":
    sys.exit(subprocess.run([os.environ["REAL_TERRAFORM"], *args]).returncode)
with open(os.environ["TRACE"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if os.environ.get("AWS_ACCESS_KEY_ID") != "test" or os.environ.get("AWS_SECRET_ACCESS_KEY") != "test":
    sys.exit("Backend credentials unavailable")
if action == "plan":
    if os.environ.get("FAIL_PLAN"):
        sys.exit(1)
    path = next(a[5:] for a in args if a.startswith("-out="))
    pathlib.Path(path).write_text(json.dumps({
        "digest": os.environ.get("TF_VAR_runner_digest", ""),
        "suspended": os.environ.get("TF_VAR_schedules_suspended", "false"),
    }))
if action == "apply" and args[0].endswith("/configuration"):
    settings = json.loads(pathlib.Path(args[-1]).read_text())
    with open(pathlib.Path(os.environ["TEST_ROOT"]) / "applied", "a") as f:
        f.write(json.dumps(settings) + "\\n")
if action == "output":
    if os.environ.get("FAIL_OUTPUT"):
        sys.exit("Backend read failed")
    print(json.dumps({"upgrade": {
        "apiVersion": "tekton.dev/v1", "kind": "PipelineRun",
        "metadata": {"generateName": "skupper-upgrade-", "namespace": "skupper-ci"},
        "spec": {"pipelineRef": {"name": "skupper-upgrade"},
                 "taskRunSpecs": [{"pipelineTaskName": "validate", "serviceAccountName": "skupper-test"}]},
    }}))
if action == "show":
    print(json.dumps({"variables": {
        "cluster_name": {"value": "lts-2-2"},
        "kube_context": {"value": "lts-2-2/test/admin"}}}))
''')
        self.write_tool("kubectl", '''#!/usr/bin/env python3
import json, os, pathlib, sys
root = pathlib.Path(os.environ["TEST_ROOT"])
with (root / "cluster-calls").open("a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if "create" in sys.argv:
    (root / "submitted-run").write_text(sys.stdin.read())
    print("pipelinerun.tekton.dev/skupper-upgrade-install")
if "get" in sys.argv:
    if os.environ.get("FAIL_READ"):
        sys.exit(1)
    resource = sys.argv[sys.argv.index("get") + 1]
    if resource == "namespace":
        print(os.environ["CLUSTER_UID"], end="")
    elif resource.startswith(("imagestreamtag/", "imagestreamimage/")):
        if os.environ.get("NO_IMAGE") and not (root / "built").exists():
            sys.exit(1)
        if resource.startswith("imagestreamimage/") and os.environ.get("MISSING_BUILT_IMAGE"):
            sys.exit(1)
        if resource.startswith("imagestreamtag/"):
            print(json.dumps({"image": {"metadata": {"name": os.environ["IMAGE_DIGEST"]}}}))
        else:
            print(resource)
    elif resource.startswith("build.build.openshift.io/"):
        print(json.dumps({"status": {"phase": "Complete", "output": {"to": {
            "imageDigest": os.environ.get("BUILD_DIGEST", os.environ["IMAGE_DIGEST"])}}}}))
    elif resource == "pipelineruns":
        active = [{"spec": {"pipelineRef": {"name": "skupper-test"}}}] if os.environ.get("ACTIVE_RUN") else []
        print(json.dumps({"items": active}))
    elif resource.startswith("pipelinerun."):
        status = "False" if os.environ.get("FAIL_RUN") else "True"
        print(json.dumps({"status": {"conditions": [{"type": "Succeeded", "status": status}]}}))
''')
        self.write_tool("oc", '''#!/usr/bin/env python3
import os, pathlib, sys
root = pathlib.Path(os.environ["TEST_ROOT"])
if os.environ.get("FAIL_BUILD"):
    sys.exit(1)
(root / "built").write_text("built in target cluster")
print("build.build.openshift.io/skupper-ci-runner-1")
''')
        self.write_tool("ibmcloud", '''#!/usr/bin/env python3
import json
print(json.dumps({"credentials": {"cos_hmac_keys": {
    "access_key_id": "test", "secret_access_key": "test"}}}))
''')

    def write_tool(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def run_manage(self, action, *, stage="bootstrap", answer="lts-2-2\n"):
        args = ["install"] if action == "install" else [stage, action]
        return subprocess.run(
            [str(self.repo / "platform/manage"), *args, "lts-2-2"],
            env=self.env, input=answer, capture_output=True, text=True,
        )

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def actions(self):
        return [json.loads(line)[1] for line in self.trace.read_text().splitlines()]

    def test_reviewed_plan_applies_and_is_consumed(self):
        self.assert_ok(self.run_manage("plan"))
        self.assert_ok(self.run_manage("apply"))
        self.assertIn("apply", self.actions())
        self.assertNotEqual(self.run_manage("apply").returncode, 0)

    def test_changed_config_or_cluster_rejects_apply(self):
        for change in ("config", "cluster", "unreachable"):
            with self.subTest(change=change):
                self.env.pop("FAIL_READ", None)
                self.env["CLUSTER_UID"] = "cluster-a"
                self.assert_ok(self.run_manage("plan"))
                if change == "config":
                    with (self.repo / "platform/bootstrap/main.tf").open("a") as f:
                        f.write("\n# edited since review\n")
                elif change == "cluster":
                    self.env["CLUSTER_UID"] = "cluster-b"
                else:
                    self.env["FAIL_READ"] = "1"
                self.assertNotEqual(self.run_manage("apply").returncode, 0)
                self.assertNotIn("apply", self.actions())

    def test_failed_replan_invalidates_previous_plan(self):
        self.assert_ok(self.run_manage("plan"))
        self.env["FAIL_PLAN"] = "1"
        self.assertNotEqual(self.run_manage("plan").returncode, 0)
        self.assertNotEqual(self.run_manage("apply").returncode, 0)
        self.assertNotIn("apply", self.actions())

    def test_changed_runner_sources_reject_apply(self):
        for relative in ("Dockerfile", "scripts/run.py", "scripts/e2e.py"):
            with self.subTest(source=relative):
                self.assert_ok(self.run_manage("plan", stage="configuration"))
                with (self.repo / "platform/configuration" / relative).open("a") as f:
                    f.write("\n# edited after review\n")
                self.assertNotEqual(self.run_manage("apply", stage="configuration").returncode, 0)
                self.assertNotIn("apply", self.actions())

    def test_confirmation_is_required(self):
        self.assert_ok(self.run_manage("plan"))
        self.assertNotEqual(self.run_manage("apply", answer="v2-dev\n").returncode, 0)
        self.assertNotIn("apply", self.actions())

    def test_stages_use_separate_state_and_plans(self):
        self.assert_ok(self.run_manage("plan"))
        self.assertNotEqual(self.run_manage("apply", stage="configuration").returncode, 0)
        self.assert_ok(self.run_manage("plan", stage="configuration"))
        trace = self.trace.read_text()
        self.assertIn("platform/v1.0/lts-2-2/bootstrap.tfstate", trace)
        self.assertIn("platform/v1.0/lts-2-2/configuration.tfstate", trace)

    def applied_configuration(self):
        return [json.loads(line) for line in (self.repo / "applied").read_text().splitlines()]

    def test_install_builds_missing_image_and_validates_before_enabling_schedules(self):
        self.env["NO_IMAGE"] = "1"
        # A previous cluster's configured digest must not bypass the build.
        self.env["TF_VAR_runner_digest"] = "sha256:" + "b2" * 32
        self.assert_ok(self.run_manage("install", answer="lts-2-2\n" * 4))
        self.assertTrue((self.repo / "built").exists())
        self.assertEqual(self.applied_configuration(), [
            {"digest": "", "suspended": "true"},
            {"digest": self.env["IMAGE_DIGEST"], "suspended": "true"},
            {"digest": self.env["IMAGE_DIGEST"], "suspended": "false"},
        ])
        run = json.loads((self.repo / "submitted-run").read_text())
        self.assertEqual(run["spec"]["taskRunSpecs"][0]["serviceAccountName"], "skupper-test")
        # A later staged apply discovers the image without edits or cached env vars.
        self.env.pop("TF_VAR_runner_digest")
        self.assert_ok(self.run_manage("plan", stage="configuration"))
        self.assert_ok(self.run_manage("apply", stage="configuration"))
        self.assertEqual(self.applied_configuration()[-1]["digest"], self.env["IMAGE_DIGEST"])

    def test_install_failure_never_enables_schedules(self):
        failures = ("FAIL_BUILD", "FAIL_RUN", "ACTIVE_RUN", "BUILD_DIGEST", "MISSING_BUILT_IMAGE", "FAIL_OUTPUT")
        for failure in failures:
            with self.subTest(failure=failure):
                for key in failures:
                    self.env.pop(key, None)
                (self.repo / "applied").unlink(missing_ok=True)
                (self.repo / "submitted-run").unlink(missing_ok=True)
                self.env[failure] = "invalid" if failure == "BUILD_DIGEST" else "1"
                result = self.run_manage("install", answer="lts-2-2\n" * 4)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertTrue(all(c["suspended"] == "true" for c in self.applied_configuration()))
                self.assertEqual((self.repo / "submitted-run").exists(), failure == "FAIL_RUN")

    def test_install_uses_cli_credentials_for_parent_and_child_backend_reads(self):
        self.env.pop("AWS_ACCESS_KEY_ID")
        self.env.pop("AWS_SECRET_ACCESS_KEY")
        self.assert_ok(self.run_manage("install", answer="lts-2-2\n" * 4))
        self.assertTrue((self.repo / "submitted-run").exists())
        self.assertEqual(self.applied_configuration()[-1]["suspended"], "false")

    def test_install_preserves_requested_suspension_and_requires_confirmation(self):
        self.env["TF_VAR_schedules_suspended"] = "true"
        self.assert_ok(self.run_manage("install", answer="lts-2-2\n" * 4))
        self.assertTrue(all(c["suspended"] == "true" for c in self.applied_configuration()))
        (self.repo / "built").unlink()
        self.assertNotEqual(self.run_manage("install", answer="wrong\n").returncode, 0)
        self.assertFalse((self.repo / "built").exists())

    def test_configuration_plan_with_no_image_requires_install(self):
        self.env["NO_IMAGE"] = "1"
        result = self.run_manage("plan", stage="configuration")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("install lts-2-2", result.stderr)
        self.assertNotIn("plan", self.actions())

    def test_changed_install_overrides_reject_apply(self):
        for key, value in (("TF_VAR_runner_digest", "sha256:" + "b2" * 32),
                           ("TF_VAR_schedules_suspended", "true")):
            with self.subTest(variable=key):
                self.env.pop("TF_VAR_runner_digest", None)
                self.env.pop("TF_VAR_schedules_suspended", None)
                self.assert_ok(self.run_manage("plan", stage="configuration"))
                self.env[key] = value
                self.assertNotEqual(self.run_manage("apply", stage="configuration").returncode, 0)
                self.assertNotIn("apply", self.actions())


if __name__ == "__main__":
    unittest.main()
