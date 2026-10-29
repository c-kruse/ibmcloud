"""Stable release selection and refusal to downgrade the installed controller."""

import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch


class ReleasePolicyTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "runner", Path(__file__).parent / "configuration/scripts/run.py",
        )
        self.runner = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, RUN_ID="test-release-policy", RELEASE_TRACK="lts-2-2"):
            spec.loader.exec_module(self.runner)

    def test_latest_stable_is_numeric_filtered_and_paginated(self):
        def release(tag, **flags):
            return {"tag_name": tag, "draft": False, "prerelease": False, **flags}

        pages = [
            [release("1.9.0")] * 98 + [release("2.2.9"), release("2.2.10")],
            [release("2.2.11"), release("2.2.99", prerelease=True),
             release("2.2.100", draft=True), release("2.3.50"), release("2.2.12-rc1")],
        ]
        responses = [(json.dumps(page).encode(), {}) for page in pages]
        with patch.object(self.runner, "fetch", side_effect=responses):
            self.assertEqual(self.runner.latest_lts(), "2.2.11")

    def test_install_refuses_downgrades_and_unknown_tracks_before_writes(self):
        for previous in ("2.2.10", "2.3.0", "v2-dev-release", ""):
            with self.subTest(previous=previous):
                existing = {"metadata": {"annotations": {"ci.skupper.io/release": previous}}}

                def cluster(*args, **kwargs):
                    if args[:5] == ("-n", "skupper", "get", "deployment", "skupper-controller"):
                        return json.dumps(existing)
                    raise AssertionError("Refused release attempted a cluster write")

                with patch.object(self.runner, "latest_lts", return_value="2.2.9"), \
                     patch.object(self.runner, "kubectl", side_effect=cluster):
                    with self.assertRaisesRegex(ValueError, "Refusing downgrade|no identifiable"):
                        self.runner.install()


if __name__ == "__main__":
    unittest.main()
