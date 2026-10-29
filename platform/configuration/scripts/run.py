"""Trusted in-cluster entrypoint shared by scheduled and manual PipelineRuns."""

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
import xml.etree.ElementTree as ET

import yaml

RUN = os.environ["RUN_ID"]
TRACK = os.environ.get("RELEASE_TRACK", "v2-dev")
REVISION = os.environ.get("TEST_REVISION", "3981c91c56aabcd603b5b2673252097bee570c4e")
LEASE = "skupper-ci-execution"
SUMMARY = {}


def kubectl(*args, obj=None):
    return subprocess.check_output(
        ["kubectl", "--request-timeout=60s", *args], text=True,
        input=json.dumps(obj) if obj is not None else None,
    )


def fetch(url, method="GET", headers=None):
    request = urllib.request.Request(url, method=method, headers={
        "User-Agent": "skupper-tekton-ci", **(headers or {}),
    })
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read(), response.headers


def lock():
    # No time-based stealing: a slow or partitioned runner must not overlap an
    # upgrade. Recover a cancelled/deleted run only after all its pods stop.
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        lease = json.loads(kubectl("-n", "skupper-ci", "get", "lease", LEASE, "-o", "json"))
        holder = lease.get("spec", {}).get("holderIdentity", "")
        free = not holder or holder == RUN
        if not free:
            old = kubectl("-n", "skupper-ci", "get", "pipelinerun", holder,
                          "--ignore-not-found", "-o", "json")
            terminal = not old.strip() or bool(json.loads(old).get("status", {}).get("completionTime"))
            pods = json.loads(kubectl("-n", "skupper-ci", "get", "pods", "-l",
                                     f"tekton.dev/pipelineRun={holder}", "-o", "json"))["items"]
            free = terminal and all(p.get("status", {}).get("phase") in ("Succeeded", "Failed") for p in pods)
        if free:
            lease.setdefault("spec", {})["holderIdentity"] = RUN
            try:
                kubectl("replace", "-f", "-", obj=lease)
                return
            except subprocess.CalledProcessError:
                pass  # resourceVersion conflict: reread before retrying.
        print(f"Waiting for shared lock held by {holder}", flush=True)
        time.sleep(10)
    raise TimeoutError("Another Skupper run holds the lock; retry after it completes")


def unlock():
    lease = json.loads(kubectl("-n", "skupper-ci", "get", "lease", LEASE, "-o", "json"))
    if lease.get("spec", {}).get("holderIdentity") == RUN:
        lease["spec"]["holderIdentity"] = ""
        kubectl("replace", "-f", "-", obj=lease)


def latest_lts():
    versions = []
    page = 1
    while True:
        data, _ = fetch(f"https://api.github.com/repos/skupperproject/skupper/releases?per_page=100&page={page}")
        releases = json.loads(data)
        for release in releases:
            match = re.fullmatch(r"2\.2\.(\d+)", release["tag_name"])
            if match and not release["draft"] and not release["prerelease"]:
                versions.append((int(match[1]), release["tag_name"]))
        if len(releases) < 100:
            break
        page += 1
    if not versions:
        raise ValueError("No stable 2.2.x release found")
    return max(versions)[1]


def install():
    release = latest_lts() if TRACK == "lts-2-2" else "v2-dev-release"
    current = kubectl("-n", "skupper", "get", "deployment", "skupper-controller", "--ignore-not-found", "-o", "json")
    if current.strip():
        existing = json.loads(current)
        previous = existing["metadata"].get("annotations", {}).get("ci.skupper.io/release", "")
        if TRACK == "lts-2-2":
            if not re.fullmatch(r"2\.2\.\d+", previous):
                raise ValueError("Existing controller has no identifiable stable 2.2.x release; review migration manually")
            if int(previous.rsplit(".", 1)[1]) > int(release.rsplit(".", 1)[1]):
                raise ValueError(f"Refusing downgrade from {previous} to {release}")
        SUMMARY["previous_release"] = previous
        SUMMARY["previous_controller_image"] = existing["spec"]["template"]["spec"]["containers"][0]["image"]
    assets = f"https://github.com/skupperproject/skupper/releases/download/{release}"
    bundle_data, _ = fetch(f"{assets}/manifest.json")
    bundle = json.loads(bundle_data)
    manifest, _ = fetch(f"{assets}/skupper-cluster-scope.yaml")
    documents = [d for d in yaml.safe_load_all(manifest) if d and d["kind"] != "Namespace"]
    expected = {("CustomResourceDefinition", f"{name}.skupper.io", "") for name in (
        "accessgrants", "accesstokens", "attachedconnectorbindings", "attachedconnectors",
        "certificates", "connectors", "links", "listeners", "multikeylisteners",
        "routeraccesses", "securedaccesses", "sites",
    )} | {
        ("Deployment", "skupper-controller", "skupper"),
        ("ServiceAccount", "skupper-controller", "skupper"),
        ("ClusterRole", "skupper-controller", ""),
        ("ClusterRoleBinding", "skupper-controller", ""),
    }
    actual = {(d["kind"], d["metadata"]["name"], d["metadata"].get("namespace", "")) for d in documents}
    if actual != expected or len(documents) != len(expected):
        raise ValueError("Installer resource contract changed; review before applying")
    controller = next(d for d in documents if d["kind"] == "Deployment")
    if controller["metadata"] != {"name": "skupper-controller", "namespace": "skupper"}:
        raise ValueError("Unexpected installer deployment")
    container = next(c for c in controller["spec"]["template"]["spec"]["containers"] if c["name"] == "controller")
    envs = [e for e in container["env"] if e["name"] in ("SKUPPER_ROUTER_IMAGE", "SKUPPER_KUBE_ADAPTOR_IMAGE")]
    if len(envs) != 2:
        raise ValueError("Installer must specify router and adaptor images")
    digests = {i["name"]: i["digest"] for c in bundle["components"] for i in c["images"]}
    pins = {}
    for image in [container["image"], *(e["value"] for e in envs)]:
        if not image.startswith("quay.io/skupper/") or not re.fullmatch(r"sha256:[0-9a-f]{64}", digests[image]):
            raise ValueError(f"Invalid published image: {image}")
        repo, tag = image.removeprefix("quay.io/").rsplit(":", 1)
        _, headers = fetch(f"https://quay.io/v2/{repo}/manifests/{tag}", method="HEAD", headers={
            "Accept": "application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.v2+json",
        })
        if headers.get("Docker-Content-Digest") != digests[image]:
            raise ValueError(f"Publication in progress for {image}; refusing a mixed bundle")
        pins[image] = image.rsplit(":", 1)[0] + "@" + digests[image]
    container["image"] = pins[container["image"]]
    for env in envs:
        env["value"] = pins[env["value"]]
    SUMMARY.update(release=release, bundle_sha256=hashlib.sha256(bundle_data).hexdigest(), images=pins)
    controller["metadata"]["annotations"] = {
        "ci.skupper.io/run": RUN,
        "ci.skupper.io/bundle-sha256": SUMMARY["bundle_sha256"],
        "ci.skupper.io/release": release,
    }
    report = Path(os.environ["REPORT_DIR"])
    (report / "release.json").write_text(json.dumps(SUMMARY, indent=2))
    crds = [d for d in documents if d["kind"] == "CustomResourceDefinition"]
    rest = [d for d in documents if d["kind"] != "CustomResourceDefinition"]
    for group in (crds, rest):
        print(kubectl("apply", "--server-side", "--field-manager=skupper-tekton", "-f", "-",
                      obj={"apiVersion": "v1", "kind": "List", "items": group}), flush=True)
        if group is crds:
            kubectl("wait", "--for=condition=Established", "--timeout=180s",
                    *(f"crd/{d['metadata']['name']}" for d in crds))
    print(kubectl("-n", "skupper", "rollout", "status", "deployment/skupper-controller", "--timeout=600s"))
    # Check preexisting networks too, not just the controller's rollout.
    deadline = time.monotonic() + 600
    while True:
        routers = json.loads(kubectl("get", "deployments", "-A", "-l", "skupper.io/component=router", "-o", "json"))["items"]
        if all(c["image"] in pins.values() for r in routers for c in r["spec"]["template"]["spec"]["containers"] if c["name"] in ("router", "kube-adaptor")):
            break
        if time.monotonic() > deadline:
            raise TimeoutError("Existing router images did not converge")
        time.sleep(5)
    for r in routers:
        kubectl("-n", r["metadata"]["namespace"], "rollout", "status", f"deployment/{r['metadata']['name']}", "--timeout=600s")


def test():
    SUMMARY["test_revision"] = REVISION
    report = Path(os.environ["REPORT_DIR"])
    deployment = json.loads(kubectl("-n", "skupper", "get", "deployment", "skupper-controller", "-o", "json"))
    SUMMARY["controller_image"] = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    (report / "tested-version.json").write_text(json.dumps(SUMMARY, indent=2))
    data, _ = fetch(f"https://codeload.github.com/skupperproject/skupper/tar.gz/{REVISION}")
    with tempfile.TemporaryFile() as archive:
        archive.write(data)
        archive.seek(0)
        with tarfile.open(fileobj=archive) as tar:
            tar.extractall("/tmp/source", filter="data")
    Path(f"/tmp/source/skupper-{REVISION}").rename("/tmp/skupper")
    Path("/tmp/skupper/.ci-revision").write_text(REVISION)
    os.environ["REPORT_DIR"] = str(report / "e2e")
    completed = subprocess.run([sys.executable, str(Path(__file__).with_name("e2e.py"))])
    junit = report / "e2e/junit.xml"
    if junit.exists():
        SUMMARY["scenarios"] = {
            case.attrib["name"]: "failed" if case.find("failure") is not None else "passed"
            for case in ET.parse(junit).getroot().findall("testcase")
        }
    completed.check_returncode()


def main():
    if TRACK not in ("v2-dev", "lts-2-2") or not re.fullmatch(r"[0-9a-f]{40}", REVISION):
        raise ValueError("Unknown release policy or unpinned test revision")
    os.makedirs("/tmp/home", exist_ok=True)
    account = "/var/run/secrets/kubernetes.io/serviceaccount"
    config = {
        "apiVersion": "v1", "kind": "Config", "current-context": "local",
        "clusters": [{"name": "local", "cluster": {"server": "https://kubernetes.default.svc", "certificate-authority": f"{account}/ca.crt"}}],
        "users": [{"name": "runner", "user": {"tokenFile": f"{account}/token"}}],
        "contexts": [{"name": "local", "context": {"cluster": "local", "user": "runner", "namespace": "skupper-ci"}}],
    }
    os.environ["KUBECONFIG"] = "/tmp/home/kubeconfig"
    Path(os.environ["KUBECONFIG"]).write_text(yaml.safe_dump(config))
    action = sys.argv[1]
    if action in ("install", "test"):
        # Tasks cannot be used directly to bypass the pipeline's shared lock.
        lease = json.loads(kubectl("-n", "skupper-ci", "get", "lease", LEASE, "-o", "json"))
        if lease.get("spec", {}).get("holderIdentity") != RUN:
            raise RuntimeError("This PipelineRun does not own the shared lock")
        os.makedirs(os.environ["REPORT_DIR"], exist_ok=True)
    try:
        {"lock": lock, "install": install, "test": test, "unlock": unlock}[action]()
        SUMMARY["status"] = "passed"
    finally:
        SUMMARY.setdefault("status", "failed")
        SUMMARY["run"] = RUN
        print(json.dumps(SUMMARY), flush=True)
        Path(os.environ["RESULT_PATH"]).write_text(json.dumps(SUMMARY))


if __name__ == "__main__":
    main()
