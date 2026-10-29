#!/usr/bin/env python3
"""Run the pinned upstream ci-tests, not a substitute connectivity test.

Contract: run.py extracts the pinned source tarball into /tmp/skupper and writes
.ci-revision containing COMMIT. REPORT_DIR is an empty absolute per-run directory
(normally /workspace/reports/RUN/e2e); RUN_ID is the PipelineRun name. The caller
MUST already hold the shared Kubernetes Lease covering installation, tests,
diagnostics and cleanup. This script does not acquire a second lock.

Terraform must provision ci-e2e-{west,east,westha,eastha,iperf3-hub,
iperf3-client,iperf3-workload}, labelled ci.skupper.io/test-namespace=true,
and bind skupper-ci/skupper-test to skupper-ci-test-workloads in each.
No namespace create/update/delete, RBAC, CRD or SCC writes are performed here.
The namespace ownership label is STATIC, not ci.skupper.io/run: the unprivileged
runner cannot relabel namespaces. Per-run ownership is in the report directory.
Cleanup uses exact resource names in these dedicated namespaces, never a
cluster-wide label deletion. Controller-generated children are GC'd with Sites.

Required test permissions: get namespaces; get/list nodes and configmaps globally;
CRUD/list/watch Skupper v2alpha1 CRs, deployments, services, pods, jobs, configmaps,
secrets in test namespaces, plus events get/list, pods/exec create and pods/log get. The controller
needs its normal installation permissions. Additionally grant configmap
get/create/update/patch/delete in SKUPPER_CONTROLLER_NAMESPACE (default skupper)
for label-annot-all-kinds. Creation cannot be resourceNames-restricted by RBAC.
The labels test changes the controller's GLOBAL label template. Use a dedicated
test controller/cluster, with no other Sites, and explicitly acknowledge this via
SKUPPER_E2E_EXCLUSIVE_CONTROLLER=true. Preexisting templates cause a hard failure.
On OpenShift preconfigure SCC admission for the workload service accounts: the
upstream curl pod explicitly requires UID 65534 (restricted-v2's allocated UID
range usually rejects it); this script does not weaken or grant SCCs.

Runtime: Python 3.12, PyYAML, ansible-core 2.18.11, kubernetes Python client,
kubernetes.core and skupper.v2 collections, kubectl, bash and standard Unix
utilities. Unlike make ci-tests, no venv/download/install is done here, tests run
sequentially on one cluster, namespace lifecycle is removed in a temporary COPY,
upstream must-gather rescue is replaced by fail, and teardown is deferred until
safe diagnostics have been recorded. JUnit records subprocess and cleanup status.
The pinned development hello-world includes both site directories on both hosts without a
condition; add the intended west/east host condition to avoid deploying a local
backend beside curl (which would bypass the link being tested).
No oc, jq or community.general dependency is required.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

import yaml


COMMIT = os.environ.get("TEST_REVISION", "3981c91c56aabcd603b5b2673252097bee570c4e")
SOURCE = Path("/tmp/skupper")
SCENARIOS = ("attached-connector", "ha", "hello-world", "labels-and-annotations")
TEST_IMAGES = {
    "quay.io/skupper/lanyard": "sha256:7b33ade2902c7a50d7e7f84f46470ed742a9aff78344369b28761b7bbf2f4f22",
    "quay.io/skupper/hello-world-backend": "sha256:21b3f8f031819ff068ac3c22f161808dad4949b46b3d2ef59b362821252c9cbe",
    "quay.io/skupper/hello-world-frontend": "sha256:35a8a87fe4775cb81a68e56e25d06237fe31ba6b7179f0362e37252263b847c8",
    "mirror.gcr.io/locustio/locust": "sha256:90fdf5b9632e67bb556d17784095652871be68318c5074731d911d4caaa42cc3",
}
HOSTS = {
    "attached-connector": {"iperf3-hub": "iperf3-hub", "iperf3-client": "iperf3-client",
                           "iperf3-workload": "iperf3-workload"},
    "ha": {"westha": "westha", "eastha": "eastha"},
    "hello-world": {"west": "west", "east": "east"},
    "labels-and-annotations": {"label-annot-west": "west", "label-annot-east": "east"},
}
# Exact upstream inputs and generated token names, ordered with Sites last.
OBJECTS = {
    "iperf3-hub": [("attachedconnectorbindings", "iperf3"), ("accessgrants", "iperf3-grant"),
                   ("sites", "iperf3-hub")],
    "iperf3-client": [("jobs", "iperf-test"), ("listeners", "iperf3-client"),
                      ("accesstokens", "token-iperf3-grant"), ("sites", "iperf3-client")],
    "iperf3-workload": [("attachedconnectors", "iperf3"), ("deployments", "iperf3-server"),
                        ("services", "iperf3-server")],
    "westha": [("jobs", "locust-job"), ("configmaps", "locust-cm"),
               ("listeners", "backend"), ("accessgrants", "west-ha-grant"), ("sites", "westha")],
    "eastha": [("deployments", "backend"), ("connectors", "backend"),
               ("accesstokens", "token-west-ha-grant"), ("sites", "eastha")],
    "west": [("pods", "curl"), ("deployments", "frontend"), ("listeners", "backend"),
             ("accessgrants", "west-grant"), ("sites", "west"), ("sites", "with-label-annot")],
    "east": [("deployments", "backend"), ("connectors", "backend"),
             ("accesstokens", "token-west-grant"), ("sites", "east"), ("sites", "no-label-annot")],
}
SKUPPER_KINDS = {"attachedconnectorbindings", "attachedconnectors", "accessgrants",
                 "accesstokens", "sites", "listeners", "connectors"}


def namespace(short: str) -> str:
    return f"ci-e2e-{short}"


def sanitized(text: str) -> str:
    text = re.sub(r"-----BEGIN [^-]+-----.*?-----END [^-]+-----", "[REDACTED PEM]",
                  text, flags=re.DOTALL)
    text = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:token|password|client-key-data|client-certificate-data|code)"
                  r"[\"']?\s*[:=]\s*[\"']?)[^\s,\"'}]+", r"\1[REDACTED]", text)
    text = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
                  "[REDACTED JWT]", text)
    # Remove terminal control characters while retaining useful line breaks.
    return "".join(c for c in text if c in "\n\t" or ord(c) >= 32)[:2000]


def command(args: list[str], *, timeout: int = 120, safe_stderr: bool = False) -> str:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, check=False)
    if result.returncode:
        context = " ".join(args) if safe_stderr else args[0]
        detail = sanitized(result.stderr.strip()) if safe_stderr else ""
        raise RuntimeError(f"{context} failed (exit {result.returncode})" +
                           (f": {detail}" if detail else ""))
    return result.stdout


def kubectl(*args: str) -> str:
    # These operations have no submitted manifests/credential payload to echo.
    return command(["kubectl", "--request-timeout=30s", *args],
                   safe_stderr=bool(args and args[0] in {"get", "delete", "logs", "wait"}))


def write_yaml(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def adapt_tasks(tasks: list[dict]) -> list[dict]:
    """Preserve test bodies/assertions; remove only lifecycle and privileged rescue."""
    result = []
    for task in tasks:
        role = task.get("ansible.builtin.include_role", {}).get("name")
        module = task.get("kubernetes.core.k8s", {})
        if role == "e2e.tests.generate_namespaces":
            continue
        if module.get("definition", {}).get("kind") == "Namespace":
            continue
        if "always" in task:
            del task["always"]
        if "rescue" in task:
            task["rescue"] = [{"name": "Propagate original upstream failure",
                                "ansible.builtin.fail": {"msg": "Upstream scenario failed; see log"}}]
        if "block" in task:
            task["block"] = adapt_tasks(task["block"])
        # Tokens and API error bodies must not enter persistent Ansible logs.
        if "skupper.v2.token" in task or "def" in task.get("skupper.v2.resource", {}):
            task["no_log"] = True
        result.append(task)
    return result


def prepare(work: Path, kubeconfig: str, controller: str) -> None:
    tests = work / "tests"
    # Upstream fixtures use floating tags. Freeze the fixture images as well as
    # the test source, without changing the assertions or workloads.
    for path in tests.rglob("*"):
        if path.is_file() and path.suffix in (".yml", ".yaml", ".j2"):
            text = path.read_text()
            for repository, digest in TEST_IMAGES.items():
                text = re.sub(re.escape(repository) + r"(?::latest)?(?![\w/@:-])",
                              f"{repository}@{digest}", text)
            path.write_text(text)
    for scenario in SCENARIOS:
        directory = tests / "e2e/scenarios" / scenario
        playbook = yaml.safe_load((directory / "test.yml").read_text())
        for play in playbook:
            play["tasks"] = adapt_tasks(play["tasks"])
            if scenario == "hello-world":
                for task in play["tasks"][0]["block"]:
                    if task.get("include_tasks") == "skupper-sites.yml":
                        site = task["vars"]["site_name"]
                        task["when"] = f"inventory_hostname == '{site}'"
        write_yaml(directory / "test.yml", playbook)
        for host, short in HOSTS[scenario].items():
            hostfile = next((directory / "inventory/host_vars").glob(f"{host}.y*ml"))
            values = yaml.safe_load(hostfile.read_text())
            # attached-connector uses the complete name; other tests add prefix.
            values["namespace_name"] = namespace(short) if scenario == "attached-connector" else short
            values["ansible_python_interpreter"] = sys.executable
            write_yaml(hostfile, values)
    values = yaml.safe_load((tests / "vars.yml").read_text())
    values.update({"kubeconfig": kubeconfig, "kubeconfig_1": kubeconfig,
                   "kubeconfig_2": kubeconfig, "kubeconfig_3": kubeconfig,
                   "namespace_prefix": "ci-e2e", "skip_teardown": True,
                   "remove_namespaces": False, "debug": False,
                   "skupper_controller_ns": controller})
    write_yaml(tests / "runner-vars.yml", values)


def preflight(controller: str) -> None:
    if os.environ.get("SKUPPER_E2E_EXCLUSIVE_CONTROLLER") != "true":
        raise RuntimeError("Dedicated controller required: set SKUPPER_E2E_EXCLUSIVE_CONTROLLER=true")
    sites = json.loads(kubectl("get", "sites.skupper.io", "-A", "-o", "json"))["items"]
    if any(site["metadata"]["namespace"] not in {namespace(short) for short in OBJECTS} for site in sites):
        raise RuntimeError("Non-test Sites exist: refusing controller-wide label-template test")
    for short in OBJECTS:
        obj = json.loads(kubectl("get", "namespace", namespace(short), "-o", "json"))
        if obj["metadata"].get("labels", {}).get("ci.skupper.io/test-namespace") != "true":
            raise RuntimeError(f"{namespace(short)} lacks dedicated test namespace label")
        if obj.get("status", {}).get("phase") != "Active":
            raise RuntimeError(f"{namespace(short)} is not Active")
    # Never overwrite a preexisting controller template, even one with our name.
    templates = json.loads(kubectl("get", "configmaps", "-A", "-l",
                                  "skupper.io/label-template=true", "-o", "json"))
    if templates["items"]:
        raise RuntimeError("Preexisting global label template: operator intervention required")
    existing = json.loads(kubectl("get", "configmap", "label-annot-all-kinds", "-n", controller,
                                 "--ignore-not-found", "-o", "json") or "null")
    if existing:
        raise RuntimeError("Controller configmap name is already in use")


def cleanup(short_names: list[str], log: Path) -> None:
    errors = []
    with log.open("a") as output:
        for short in short_names:
            for kind, name in OBJECTS[short]:
                resource = f"{kind}.skupper.io" if kind in SKUPPER_KINDS else kind
                try:
                    kubectl("delete", resource, name, "-n", namespace(short),
                            "--ignore-not-found", "--wait=true", "--timeout=60s")
                    output.write(f"deleted/absent {namespace(short)} {resource}/{name}\n")
                except Exception as exc:
                    errors.append(str(exc))
                    output.write(f"FAILED {namespace(short)} {resource}/{name}: {exc}\n")
            # Exact controller deployment names; an empty namespace is success.
            try:
                for name in ("skupper-router", "skupper-router-2"):
                    if kubectl("get", "deployment", name, "-n", namespace(short),
                               "--ignore-not-found", "-o", "name").strip():
                        kubectl("wait", "--for=delete", f"deployment/{name}",
                                "-n", namespace(short), "--timeout=60s")
            except Exception as exc:
                errors.append(str(exc))
    if errors:
        raise RuntimeError(f"Cleanup failed ({len(errors)} operations); see {log.name}")


def diagnostics(short_names: list[str], directory: Path) -> None:
    # Deliberately no Secret, AccessToken, full Pod/Deployment JSON, describe,
    # kubeconfig, environment dump or must-gather. Tables exclude token values.
    for short in short_names:
        for resource in ("pods", "deployments", "jobs", "events", "sites.skupper.io"):
            try:
                data = kubectl("get", resource, "-n", namespace(short), "-o", "wide")
            except Exception as exc:
                data = str(exc)
            (directory / f"{short}-{resource}.txt").write_text(data)
        for kind, name in OBJECTS[short]:
            if kind not in {"jobs", "deployments"} or name.startswith("skupper"):
                continue
            try:
                data = kubectl("logs", "-n", namespace(short), f"{kind}/{name}",
                               "--all-containers=true", "--tail=500", "--limit-bytes=65536")
            except Exception as exc:
                data = str(exc)
            (directory / f"{short}-{name}.log").write_text(data)


def play(args: list[str], env: dict[str, str], log: Path, timeout: int) -> int:
    with log.open("w") as output:
        process = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, start_new_session=True)

        def copy_log() -> None:
            for line in process.stdout:
                output.write(line)
                output.flush()
                print(sanitized(line), end="", flush=True)

        reader = threading.Thread(target=copy_log, daemon=True)
        reader.start()
        try:
            return process.wait(timeout=timeout)
        except BaseException:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            reader.join(timeout=10)


def junit(report: Path, records: list[tuple[str, float, str]]) -> None:
    suite = ET.Element("testsuite", name="upstream-skupper-ci-tests", tests=str(len(records)),
                       failures=str(sum(bool(error) for _, _, error in records)))
    for name, duration, error in records:
        case = ET.SubElement(suite, "testcase", classname="skupper.ci", name=name,
                             time=f"{duration:.3f}")
        if error:
            ET.SubElement(case, "failure", message=error).text = error
        ET.SubElement(case, "system-out").text = f"See {name}/ in this report directory"
    ET.ElementTree(suite).write(report / "junit.xml", encoding="utf-8", xml_declaration=True)


def main() -> int:
    os.umask(0o077)
    report = Path(os.environ["REPORT_DIR"])
    run_id = os.environ["RUN_ID"]
    if not report.is_absolute() or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", run_id):
        raise RuntimeError("REPORT_DIR must be absolute and RUN_ID a Kubernetes name")
    kubeconfig = str(Path(os.environ["KUBECONFIG"]).resolve(strict=True))
    controller = os.environ.get("SKUPPER_CONTROLLER_NAMESPACE", "skupper")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", controller):
        raise RuntimeError("Invalid controller namespace")
    report.mkdir(parents=True, exist_ok=True)
    if any(report.iterdir()):
        raise RuntimeError("REPORT_DIR must be empty; refusing to overwrite another run")
    (report / "run.json").write_text(json.dumps({"run_id": run_id, "commit": COMMIT,
                                               "fixture_images": TEST_IMAGES}))
    records = []
    try:
        if (SOURCE / ".ci-revision").read_text().strip() != COMMIT:
            raise RuntimeError("Upstream source revision marker does not match pinned commit")
        preflight(controller)
        with tempfile.TemporaryDirectory(prefix="skupper-ci-") as temporary:
            work = Path(temporary) / "skupper"
            shutil.copytree(SOURCE / "tests", work / "tests")
            prepare(work, kubeconfig, controller)
            e2e = work / "tests/e2e"
            env = os.environ.copy()
            env.update({"ANSIBLE_CONFIG": str(e2e / "ansible.cfg"),
                        "ANSIBLE_COLLECTIONS_PATH": str(e2e / "collections") + ":" +
                        env.get("ANSIBLE_COLLECTIONS_PATH", "/usr/share/ansible/collections:" +
                                str(Path.home() / ".ansible/collections")),
                        "ANSIBLE_ROLES_PATH": str(e2e / "collections/ansible_collections/e2e/tests/roles"),
                        "ANSIBLE_NOCOLOR": "1", "ANSIBLE_DISPLAY_ARGS_TO_STDOUT": "false",
                        "ANSIBLE_DEBUG": "false", "ANSIBLE_STDOUT_CALLBACK": "default",
                        "ANSIBLE_CALLBACKS_ENABLED": ""})
            # Do not allow an inherited persistent log or debug verbosity to leak tokens.
            env.pop("ANSIBLE_LOG_PATH", None)
            env["ANSIBLE_VERBOSITY"] = "0"
            for scenario in SCENARIOS:
                directory = report / scenario
                directory.mkdir()
                short_names = list(HOSTS[scenario].values())
                start = time.monotonic()
                error = ""
                attempted = False
                print(f"START {scenario}: reports {directory}", flush=True)
                try:
                    cleanup(short_names, directory / "cleanup-before.log")
                    attempted = True
                    playbook = e2e / "scenarios" / scenario
                    rc = play(["ansible-playbook", str(playbook / "test.yml"), "-i",
                               str(playbook / "inventory"), "-e", "@" + str(work / "tests/runner-vars.yml"),
                               "-e", f"test_name={scenario}"], env, directory / "ansible.log", 1800)
                    if rc:
                        error = f"ansible-playbook exited {rc}; see {directory / 'ansible.log'}"
                except BaseException as exc:
                    error = str(exc) or type(exc).__name__
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                finally:
                    diagnostics(short_names, directory)
                    # Only this scenario could have created the previously absent template.
                    if scenario == "labels-and-annotations" and attempted:
                        try:
                            kubectl("delete", "configmap", "label-annot-all-kinds", "-n", controller,
                                    "--ignore-not-found", "--wait=true", "--timeout=60s")
                        except Exception as exc:
                            error += f"; controller template cleanup: {exc}"
                    try:
                        cleanup(short_names, directory / "cleanup-after.log")
                    except Exception as exc:
                        error += f"; {exc}"
                    duration = time.monotonic() - start
                    records.append((scenario, duration, error))
                    junit(report, records)
                    outcome = f"FAIL: {sanitized(error)}" if error else "PASS"
                    print(f"END {scenario}: {outcome} ({duration:.1f}s)", flush=True)
    except Exception as exc:
        error = sanitized(str(exc))
        print(f"FAIL harness: {error}", flush=True)
        records.append(("harness", 0, error))
        junit(report, records)
    return int(any(error for _, _, error in records))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"e2e harness: {exc}", file=sys.stderr)
        sys.exit(1)
