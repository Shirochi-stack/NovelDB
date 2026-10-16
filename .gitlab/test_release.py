"""Release safeguards with mocked HTTP only."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("release", Path(__file__).with_name("release.py"))
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        folder = self.root / "artifacts/build"
        folder.mkdir(parents=True)
        self.binary = folder / "fixture.exe"
        self.binary.write_bytes(b"fixture binary")
        self.env = {"PUBLISH_ARTIFACTS": "true", "CI_COMMIT_REF_PROTECTED": "true", "CI_COMMIT_TAG": "v1.0", "CI_COMMIT_SHA": "a" * 40,
                    "CI_JOB_TOKEN": "offline-fixture", "CI_API_V4_URL": "https://gitlab.example.invalid/api/v4", "CI_SERVER_URL": "https://gitlab.example.invalid", "CI_PROJECT_ID": "1"}
        (folder / "manifest.json").write_text(json.dumps({"label": "build", "source_commit": "a" * 40, "artifacts": [{"file": self.binary.name, "sha256": hashlib.sha256(self.binary.read_bytes()).hexdigest()}]}))

    def test_opt_in_required_before_network(self):
        opener = Mock()
        with self.assertRaises(release.ReleaseError):
            release.publish({**self.env, "PUBLISH_ARTIFACTS": "false"}, self.root, opener)
        opener.open.assert_not_called()

    def test_tampered_artifact_rejected(self):
        self.binary.write_bytes(b"tampered")
        with self.assertRaisesRegex(release.ReleaseError, "digest changed"):
            release.artifact_entries(self.root, self.env["CI_COMMIT_SHA"])

    def test_existing_release_commit_must_match(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps({"tag_name": "v1.0", "commit": {"id": "b" * 40}}).encode())
        with self.assertRaisesRegex(release.ReleaseError, "different commit"):
            release.publish(self.env, self.root, opener)
        self.assertEqual(opener.open.call_count, 1)

    def test_redirect_does_not_forward_token(self):
        with self.assertRaises(release.ReleaseError):
            release.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://elsewhere.example.invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
