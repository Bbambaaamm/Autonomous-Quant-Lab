"""Exercise the actual Ruff/Git artifact path without API access."""

import base64
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "agent_format", Path(__file__).with_name("agent-format.py")
)
FORMAT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(FORMAT)
TRUSTED = Path(__file__).resolve().parents[2]


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        self.target = "backend/tests/test_sample.py"
        self.original = (
            "from datetime import date\n\ndef test_sunday():\n"
            "    value=date(2025,1,5)\n    assert value.weekday()==6\n"
        )
        self.write(self.target, self.original)
        self.write("backend/tests/untouched.py", "x=  1\n")
        self.write("ruff.py", "raise RuntimeError('PR CODE EXECUTED')\n")
        self.write("backend/ruff.toml", "this is deliberately invalid TOML")
        self.git("init", "-q")
        self.git("config", "user.name", "test")
        self.git("config", "user.email", "test@example.invalid")
        self.commit()
        self.output = self.root / "generated"

    def write(self, name, value):
        file = self.candidate / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(value)

    def git(self, *args):
        return FORMAT.git(self.candidate, *args)

    def commit(self):
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        self.sha = self.git("rev-parse", "HEAD").decode().strip()

    def generate(self, scope=None, sha=None):
        return FORMAT.generate(
            self.candidate,
            TRUSTED,
            self.output,
            scope or [self.target],
            sha or self.sha,
            "fixture-evidence",
        )

    def test_real_ruff_patch_applies_and_artifact_matches_contract(self):
        result = self.generate()
        self.assertEqual(result["paths"], [self.target])
        self.assertEqual((self.candidate / "backend/tests/untouched.py").read_text(), "x=  1\n")
        formatted = (self.candidate / self.target).read_text()
        self.assertIn("date(2025, 1, 5)", formatted)
        encoded = (self.output / "output.json").read_bytes()
        self.assertEqual(
            hashlib.sha256(encoded).hexdigest(), (self.output / "checksum").read_text()
        )
        self.assertEqual(
            json.loads((self.output / "metadata.json").read_text()),
            {
                "source_sha": self.sha,
                "evidence": "fixture-evidence",
                "failure_class": "lint-format",
            },
        )
        self.git("restore", self.target)
        subprocess.run(
            ["git", "apply", "--check", "-"],
            input=result["patch"].encode(),
            cwd=self.candidate,
            check=True,
        )
        subprocess.run(
            ["git", "apply", "-"],
            input=result["patch"].encode(),
            cwd=self.candidate,
            check=True,
        )
        self.assertEqual((self.candidate / self.target).read_text(), formatted)

    def test_stale_head_rejected_without_output(self):
        with self.assertRaisesRegex(ValueError, "SOURCE_SHA_MISMATCH"):
            self.generate(sha="a" * 40)
        self.assertFalse(self.output.exists())

    def test_dirty_checkout_rejected(self):
        self.write("untracked.txt", "change")
        with self.assertRaisesRegex(ValueError, "DIRTY_FORMAT_CHECKOUT"):
            self.generate()

    def test_symlink_parent_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "sample.py").write_text("x=1\n")
        (self.candidate / "backend/linked").symlink_to(outside, target_is_directory=True)
        self.commit()
        with self.assertRaisesRegex(ValueError, "FORMAT_SYMLINK"):
            self.generate(["backend/linked/sample.py"])
        self.assertEqual((outside / "sample.py").read_text(), "x=1\n")

    def test_protected_target_rejected(self):
        self.write("backend/src/quantlab/trading.py", "x=1\n")
        self.commit()
        with self.assertRaises(subprocess.CalledProcessError):
            self.generate(["backend/src/quantlab/trading.py"])
        self.assertFalse(self.output.exists())

    def test_invalid_python_rejected_before_artifact(self):
        self.write(self.target, "def broken(:\n")
        self.commit()
        with self.assertRaises(SyntaxError):
            self.generate()
        self.assertFalse(self.output.exists())

    def test_already_formatted_does_not_create_empty_patch(self):
        self.write(self.target, "x = 1\n")
        self.commit()
        with self.assertRaisesRegex(ValueError, "EMPTY_OR_OUT_OF_SCOPE"):
            self.generate()
        self.assertFalse(self.output.exists())

    def test_hash_locked_requirements(self):
        requirement = FORMAT.locked_requirements(TRUSTED)
        self.assertRegex(requirement, r"^ruff==\d+\.\d+\.\d+ --hash=sha256:")

    def snapshot_data(self):
        content = self.original.encode()
        return {
            "source_sha": self.sha,
            "files": [
                {
                    "path": self.target,
                    "mode": "100644",
                    "sha": hashlib.sha1(
                        b"blob " + str(len(content)).encode() + b"\0" + content
                    ).hexdigest(),
                    "content": base64.b64encode(content).decode(),
                }
            ],
        }

    def from_snapshot(self, data, scope=None):
        snapshot = self.root / "snapshot.json"
        snapshot.write_text(json.dumps(data))
        return FORMAT.generate_snapshot(
            snapshot,
            TRUSTED,
            self.output,
            scope or [self.target],
            self.sha,
            "fixture-evidence",
        )

    def test_data_only_snapshot_produces_patch_for_real_source(self):
        result = self.from_snapshot(self.snapshot_data())
        self.assertEqual((self.candidate / self.target).read_text(), self.original)
        subprocess.run(
            ["git", "apply", "--check", "-"],
            input=result["patch"].encode(),
            cwd=self.candidate,
            check=True,
        )
        subprocess.run(
            ["git", "apply", "-"],
            input=result["patch"].encode(),
            cwd=self.candidate,
            check=True,
        )
        self.assertIn("date(2025, 1, 5)", (self.candidate / self.target).read_text())
        self.assertEqual(
            json.loads((self.output / "metadata.json").read_text())["source_sha"],
            self.sha,
        )

    def test_snapshot_rejects_wrong_commit_blob_mode_and_scope(self):
        for field, value, error in [
            ("source_sha", "a" * 40, "SOURCE_SHA_MISMATCH"),
            ("sha", "a" * 40, "FORMAT_BLOB_HASH_MISMATCH"),
            ("mode", "120000", "FORMAT_NOT_TRACKED_REGULAR"),
            ("path", "backend/tests/other.py", "FORMAT_SNAPSHOT_SCOPE_MISMATCH"),
        ]:
            with self.subTest(field=field):
                data = self.snapshot_data()
                (data if field == "source_sha" else data["files"][0])[field] = value
                with self.assertRaisesRegex(ValueError, error):
                    self.from_snapshot(data)
                self.assertFalse(self.output.exists())

    def test_snapshot_formats_stub_with_unchanged_ast(self):
        self.target = "backend/tests/types.pyi"
        self.original = "def example(value:int)->str: ...\n"
        self.write(self.target, self.original)
        self.commit()
        result = self.from_snapshot(self.snapshot_data())
        self.assertEqual(result["paths"], [self.target])
        subprocess.run(
            ["git", "apply", "--check", "-"],
            input=result["patch"].encode(),
            cwd=self.candidate,
            check=True,
        )
        self.assertIn("value: int", result["patch"])

    def test_snapshot_rejects_traversal_even_if_in_scope(self):
        data = self.snapshot_data()
        data["files"][0]["path"] = "backend/../../escape.py"
        with self.assertRaisesRegex(ValueError, "INVALID_FORMAT_PATH"):
            self.from_snapshot(data, ["backend/../../escape.py"])
        self.assertFalse(self.output.exists())

    def test_snapshot_rejects_protected_target(self):
        data = self.snapshot_data()
        data["files"][0]["path"] = "backend/src/quantlab/trading.py"
        with self.assertRaises(subprocess.CalledProcessError):
            self.from_snapshot(data, ["backend/src/quantlab/trading.py"])
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
