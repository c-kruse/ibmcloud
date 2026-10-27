import contextlib
import io
import json
import unittest
from unittest.mock import patch

import yaml

import update


class UpdaterTest(unittest.TestCase):
    def render(self, track, releases=(), digests=None, registry=None):
        """Exercise the CLI's read-only contract against simulated upstream HTTP."""
        digests = digests or ["a" * 64, "b" * 64, "c" * 64]
        tag = "v2-dev" if track == "v2-dev" else "2.2.10"
        images = [f"quay.io/skupper/{name}:{tag}" for name in
                  ("controller", "kube-adaptor", "skupper-router")]
        deployment = {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "skupper-controller", "namespace": "skupper"},
            "spec": {"template": {"spec": {"containers": [{
                "name": "controller", "image": images[0], "env": [
                    {"name": "SKUPPER_KUBE_ADAPTOR_IMAGE", "value": images[1]},
                    {"name": "SKUPPER_ROUTER_IMAGE", "value": images[2]},
                ],
            }]}}},
        }
        bundle = {"components": [{"images": [
            {"name": image, "digest": "sha256:" + digest}
            for image, digest in zip(images, digests)
        ]}]}

        def fetch(url, method="GET", headers=None):
            if method == "HEAD":
                name = url.split("/manifests/")[0].rsplit("/", 1)[1]
                index = ("controller", "kube-adaptor", "skupper-router").index(name)
                response = io.BytesIO()
                response.headers = {"Docker-Content-Digest": "sha256:" + (registry or digests)[index]}
                return response
            if url.startswith(update.RELEASES):
                return io.BytesIO(json.dumps(releases).encode())
            if url.endswith("manifest.json"):
                return io.BytesIO(json.dumps(bundle).encode())
            expected = "v2-dev-release" if track == "v2-dev" else "2.2.10"
            self.assertIn(f"/{expected}/", url)
            return io.BytesIO(yaml.safe_dump(deployment).encode())

        output = io.StringIO()
        with patch.object(update, "fetch", fetch), patch("sys.argv", ["update.py", "--track", track, "--dry-run"]):
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                update.main()
        return list(yaml.safe_load_all(output.getvalue()))[0]

    def test_lts_selects_highest_numeric_stable_patch_only(self):
        releases = [
            {"tag_name": tag, "draft": draft, "prerelease": prerelease}
            for tag, draft, prerelease in [
                ("2.2.9", False, False), ("2.3.0", False, False),
                ("2.2.12", True, False), ("2.2.11", False, True),
                ("2.2.10-rc1", False, False), ("2.2.10", False, False),
            ]
        ]
        result = self.render("lts", releases)
        self.assertEqual(result["metadata"]["annotations"][update.ANNOTATION], "2.2.10")
        container = result["spec"]["template"]["spec"]["containers"][0]
        self.assertEqual(container["image"], "quay.io/skupper/controller@sha256:" + "a" * 64)
        self.assertEqual(container["env"][0]["value"], "quay.io/skupper/kube-adaptor@sha256:" + "b" * 64)
        self.assertEqual(container["env"][1]["value"], "quay.io/skupper/skupper-router@sha256:" + "c" * 64)

    def test_dev_digest_change_changes_desired_images_without_changing_tag(self):
        before = self.render("v2-dev")
        after = self.render("v2-dev", digests=["d" * 64, "e" * 64, "f" * 64])
        container = after["spec"]["template"]["spec"]["containers"][0]
        self.assertNotEqual(before["spec"]["template"], after["spec"]["template"])
        self.assertEqual(container["image"], "quay.io/skupper/controller@sha256:" + "d" * 64)
        self.assertEqual(container["env"][0]["value"], "quay.io/skupper/kube-adaptor@sha256:" + "e" * 64)

    def test_dev_refuses_partially_published_bundle(self):
        with self.assertRaisesRegex(ValueError, "Publication in progress"):
            self.render("v2-dev", registry=["a" * 64, "f" * 64, "c" * 64])

    def test_lts_does_not_fall_back_to_another_release_line(self):
        with self.assertRaisesRegex(ValueError, "No stable 2.2.x"):
            self.render("lts", [{"tag_name": "2.3.0", "draft": False, "prerelease": False}])


if __name__ == "__main__":
    unittest.main()
