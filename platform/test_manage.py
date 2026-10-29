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
        shutil.copy(source.parent / "backend.hcl", self.repo)
        self.bin = self.repo / "bin"
        self.bin.mkdir()
        self.trace = self.repo / "trace"
        self.env = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}",
                        AWS_ACCESS_KEY_ID="test", AWS_SECRET_ACCESS_KEY="test",
                        REAL_TERRAFORM=shutil.which("terraform"), TRACE=str(self.trace),
                        CLUSTER_UID="cluster-a")
        self.write_tool("terraform", '''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
args = sys.argv[1:]
action = args[1]
if action == "console":
    sys.exit(subprocess.run([os.environ["REAL_TERRAFORM"], *args]).returncode)
with open(os.environ["TRACE"], "a") as f:
    f.write(json.dumps(args) + "\\n")
if action == "plan":
    if os.environ.get("FAIL_PLAN"):
        sys.exit(1)
    path = next(a[5:] for a in args if a.startswith("-out="))
    pathlib.Path(path).write_text("saved plan")
if action == "show":
    print(json.dumps({"variables": {
        "cluster_name": {"value": "lts-2-2"},
        "kube_context": {"value": "lts-2-2/dbgf85lw06ugt2opdgp0/admin"}}}))
''')
        self.write_tool("kubectl", '''#!/usr/bin/env python3
import os, sys
if "get" in sys.argv:
    if os.environ.get("FAIL_READ"):
        sys.exit(1)
    print(os.environ["CLUSTER_UID"], end="")
''')

    def write_tool(self, name, body):
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def run_manage(self, action, *, stage="bootstrap", answer="lts-2-2\n"):
        return subprocess.run(
            [str(self.repo / "platform/manage"), stage, action, "lts-2-2"],
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


if __name__ == "__main__":
    unittest.main()
