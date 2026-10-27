#!/usr/bin/env python3
"""Install/reconcile the latest Skupper 2.2 release or published development bundle."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

import yaml


RELEASES = "https://api.github.com/repos/skupperproject/skupper/releases"
ASSETS = "https://github.com/skupperproject/skupper/releases/download"
ANNOTATION = "updater.skupper.io/release"


def fetch(url, method="GET", headers=None):
    request_headers = {"User-Agent": "skupper-cluster-updater"}
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        request_headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    request_headers.update(headers or {})
    request = urllib.request.Request(url, headers=request_headers, method=method)
    return urllib.request.urlopen(request, timeout=60)


def latest_lts():
    versions = []
    page = 1
    while True:
        with fetch(f"{RELEASES}?per_page=100&page={page}") as response:
            releases = json.load(response)
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


def registry_digest(image):
    repository, tag = image.removeprefix("quay.io/").rsplit(":", 1)
    with fetch(
        f"https://quay.io/v2/{repository}/manifests/{tag}",
        method="HEAD",
        headers={"Accept": ", ".join([
            "application/vnd.oci.image.index.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.v2+json",
        ])},
    ) as response:
        digest = response.headers.get("Docker-Content-Digest", "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError(f"Registry returned no valid digest for {image}")
    return digest


def prepare(track):
    release = latest_lts() if track == "lts" else "v2-dev-release"
    with fetch(f"{ASSETS}/{release}/manifest.json") as response:
        bundle = json.load(response)
    with fetch(f"{ASSETS}/{release}/skupper-cluster-scope.yaml") as response:
        documents = [obj for obj in yaml.safe_load_all(response) if obj]

    controller = next(obj for obj in documents if obj["kind"] == "Deployment"
                      and obj["metadata"]["name"] == "skupper-controller")
    if controller["metadata"]["namespace"] != "skupper":
        raise ValueError("Unexpected controller namespace")
    container = next(c for c in controller["spec"]["template"]["spec"]["containers"]
                     if c["name"] == "controller")
    image_env = [env for env in container["env"] if env["name"] in (
        "SKUPPER_ROUTER_IMAGE", "SKUPPER_KUBE_ADAPTOR_IMAGE")]
    if len(image_env) != 2:
        raise ValueError("Expected both router and kube-adaptor image settings")
    images = [container["image"], *(env["value"] for env in image_env)]
    digests = {image["name"]: image["digest"] for component in bundle["components"]
               for image in component["images"]}
    pinned = {}
    for image in images:
        if not image.startswith("quay.io/skupper/"):
            raise ValueError(f"Unexpected image repository: {image}")
        digest = digests[image]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid published digest for {image}")
        if track == "v2-dev" and registry_digest(image) != digest:
            raise ValueError(f"Publication in progress: {image} differs from the bundle; retry next run")
        pinned[image] = image.rsplit(":", 1)[0] + "@" + digest
    container["image"] = pinned[container["image"]]
    for env in image_env:
        env["value"] = pinned[env["value"]]
    controller["metadata"].setdefault("annotations", {})[ANNOTATION] = release
    # Bootstrap owns the namespace, not the downloaded installer.
    documents = [obj for obj in documents if obj["kind"] != "Namespace"]
    return release, documents, set(pinned.values())


def kubectl(*args, objects=None):
    result = subprocess.run(
        ["kubectl", "--request-timeout=60s", *args], check=True, text=True,
        input=yaml.safe_dump_all(objects) if objects is not None else None,
        stdout=subprocess.PIPE,
    )
    return result.stdout


def reconcile(release, documents, images):
    current = kubectl("-n", "skupper", "get", "deployment", "skupper-controller",
                      "--ignore-not-found", "-o", "json")
    if current.strip() and release != "v2-dev-release":
        existing = json.loads(current)
        previous = existing["metadata"].get("annotations", {}).get(ANNOTATION)
        if not previous:
            image = existing["spec"]["template"]["spec"]["containers"][0]["image"]
            previous = image.rsplit(":", 1)[-1]
        match = re.fullmatch(r"2\.2\.(\d+)", previous)
        if not match:
            raise ValueError("Existing controller is not identifiable as 2.2.x; migrate manually first")
        if int(match[1]) > int(release.rsplit(".", 1)[1]):
            raise ValueError(f"Refusing downgrade from {previous} to {release}")

    crds = [obj for obj in documents if obj["kind"] == "CustomResourceDefinition"]
    others = [obj for obj in documents if obj["kind"] != "CustomResourceDefinition"]
    for objects in (crds, others):
        print(kubectl("apply", "--server-side", "--field-manager=skupper-updater",
                      "-f", "-", objects=objects), end="", flush=True)
        if objects is crds:
            kubectl("wait", "--for=condition=Established", "--timeout=120s",
                    *(f"crd/{obj['metadata']['name']}" for obj in crds))
    print(kubectl("-n", "skupper", "rollout", "status", "deployment/skupper-controller",
                  "--timeout=600s"), end="", flush=True)

    # Startup recovery reconciles existing Sites with the controller's image env.
    # Wait for actual router specs to converge before checking rollout readiness.
    deadline = time.monotonic() + 600
    while True:
        routers = json.loads(kubectl("get", "deployments", "-A", "-l",
                                     "skupper.io/component=router", "-o", "json"))["items"]
        if all(all(c["image"] in images for c in router["spec"]["template"]["spec"]["containers"]
                   if c["name"] in ("router", "kube-adaptor")) for router in routers):
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("Site router images did not converge; inspect controller logs")
        time.sleep(5)
    for router in routers:
        print(kubectl("-n", router["metadata"]["namespace"], "rollout", "status",
                      f"deployment/{router['metadata']['name']}", "--timeout=600s"), end="", flush=True)
    print(f"Healthy: {release}, {len(routers)} site router deployments checked", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", required=True, choices=["lts", "v2-dev"])
    parser.add_argument("--dry-run", action="store_true", help="Resolve and print manifests; no cluster access")
    args = parser.parse_args()
    release, documents, images = prepare(args.track)
    print(f"Selected {release}: {', '.join(sorted(images))}", file=sys.stderr, flush=True)
    if args.dry_run:
        print(yaml.safe_dump_all(documents), end="")
    else:
        # kubectl needs an explicit context. Reference the projected token file
        # rather than copying credentials or passing a token on the command line.
        with tempfile.TemporaryDirectory() as directory:
            if os.environ.get("KUBERNETES_SERVICE_HOST"):
                account = "/var/run/secrets/kubernetes.io/serviceaccount"
                config = {
                    "apiVersion": "v1", "kind": "Config",
                    "clusters": [{"name": "local", "cluster": {
                        "server": "https://kubernetes.default.svc",
                        "certificate-authority": f"{account}/ca.crt",
                    }}],
                    "users": [{"name": "updater", "user": {"tokenFile": f"{account}/token"}}],
                    "contexts": [{"name": "local", "context": {"cluster": "local", "user": "updater"}}],
                    "current-context": "local",
                }
                path = os.path.join(directory, "kubeconfig")
                with open(path, "w") as stream:
                    yaml.safe_dump(config, stream)
                os.chmod(path, 0o600)
                os.environ["KUBECONFIG"] = path
            reconcile(release, documents, images)


if __name__ == "__main__":
    main()
