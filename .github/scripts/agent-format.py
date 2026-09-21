"""Generate a bounded formatting artifact without model or write credentials."""

import argparse
import ast
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path


def locked_requirements(trusted):
    lock = tomllib.loads((trusted / "backend/uv.lock").read_text())
    packages = [p for p in lock["package"] if p["name"] == "ruff"]
    if len(packages) != 1 or not re.fullmatch(r"\d+\.\d+\.\d+", packages[0]["version"]):
        raise ValueError("INVALID_TRUSTED_RUFF_LOCK")
    package = packages[0]
    hashes = sorted({wheel["hash"] for wheel in package["wheels"]})
    if not hashes or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", h) for h in hashes):
        raise ValueError("INVALID_TRUSTED_RUFF_HASHES")
    return f"ruff=={package['version']} " + " ".join(f"--hash={h}" for h in hashes)


def git(candidate, *args):
    return subprocess.check_output(["git", "--no-pager", *args], cwd=candidate, timeout=30)


def generate(candidate, trusted, output, scope, source_sha, evidence, metadata_sha=None):
    candidate, trusted = candidate.resolve(), trusted.resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("INVALID_SOURCE_SHA")
    if git(candidate, "rev-parse", "HEAD").decode().strip() != source_sha:
        raise ValueError("SOURCE_SHA_MISMATCH")
    if git(candidate, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("DIRTY_FORMAT_CHECKOUT")
    if not isinstance(scope, list) or not scope or any(not isinstance(p, str) for p in scope):
        raise ValueError("INVALID_FORMAT_SCOPE")
    paths = sorted({p for p in scope if p.startswith("backend/") and p.endswith((".py", ".pyi"))})
    if not paths:
        raise ValueError("NO_PYTHON_FORMAT_TARGET")
    # Load policy only from the trusted checkout, never from the candidate PR.
    subprocess.run(
        [
            "node",
            "-e",
            "const p=require(process.argv[1]),c=require(process.argv[2]);"
            "if(!p.validatePatchPaths(JSON.parse(process.argv[3]),c.v2))process.exit(1)",
            str(trusted / ".github/scripts/agent-pipeline.cjs"),
            str(trusted / ".github/agent-pipeline.json"),
            json.dumps(paths),
        ],
        check=True,
        cwd=trusted,
        timeout=30,
    )
    before = {}
    for name in paths:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or "\x00" in name:
            raise ValueError("INVALID_FORMAT_PATH")
        target = candidate
        for part in relative.parts:
            target = target / part
            if target.is_symlink():
                raise ValueError("FORMAT_SYMLINK")
        entry = git(candidate, "ls-files", "--stage", "--", name).decode()
        if not re.fullmatch(r"100(?:644|755) [0-9a-f]{40} 0\t" + re.escape(name) + r"\n", entry):
            raise ValueError("FORMAT_NOT_TRACKED_REGULAR")
        if not target.is_file() or target.stat().st_size > 1_048_576:
            raise ValueError("FORMAT_FILE_TOO_LARGE_OR_MISSING")
        before[name] = target.read_bytes()
        ast.parse(before[name])
    if sum(map(len, before.values())) > 2_097_152:
        raise ValueError("FORMAT_SCOPE_TOO_LARGE")
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-m",
            "ruff",
            "format",
            "--no-cache",
            "--config",
            str(trusted / "backend/pyproject.toml"),
            "--",
            *paths,
        ],
        cwd=candidate,
        check=True,
        timeout=60,
    )
    changed = git(candidate, "diff", "--name-only", "-z").decode().split("\0")[:-1]
    if not changed or not set(changed).issubset(paths):
        raise ValueError("EMPTY_OR_OUT_OF_SCOPE_FORMAT_PATCH")
    for name in changed:
        if ast.dump(ast.parse(before[name]), include_attributes=False) != ast.dump(
            ast.parse((candidate / name).read_bytes()), include_attributes=False
        ):
            raise ValueError("FORMAT_CHANGED_PYTHON_AST")
    git(candidate, "diff", "--check")
    patch = git(candidate, "diff", "--no-ext-diff", "--no-textconv", "--binary").decode()
    result = {
        "result": "PATCH",
        "patch": patch,
        "paths": sorted(changed),
        "reason": "Deterministic Ruff formatting; Python AST unchanged; no model call.",
    }
    encoded = json.dumps(result).encode()
    if len(encoded) > 131072:
        raise ValueError("FORMAT_PATCH_TOO_LARGE")
    output.mkdir(parents=True, exist_ok=False)
    (output / "output.json").write_bytes(encoded)
    (output / "checksum").write_text(hashlib.sha256(encoded).hexdigest())
    (output / "metadata.json").write_text(
        json.dumps(
            {
                "source_sha": metadata_sha or source_sha,
                "evidence": evidence,
                "failure_class": "lint-format",
            }
        )
    )
    return result


def generate_snapshot(snapshot, trusted, output, scope, source_sha, evidence):
    """Materialize only authenticated blob data, never a PR checkout or its Git config."""
    if snapshot.stat().st_size > 3_000_000:
        raise ValueError("FORMAT_SNAPSHOT_TOO_LARGE")
    data = json.loads(snapshot.read_bytes())
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha) or data.get("source_sha") != source_sha:
        raise ValueError("SOURCE_SHA_MISMATCH")
    if not isinstance(scope, list) or any(not isinstance(p, str) for p in scope):
        raise ValueError("INVALID_FORMAT_SCOPE")
    expected = sorted(
        {p for p in scope if p.startswith("backend/") and p.endswith((".py", ".pyi"))}
    )
    files = data.get("files")
    if not isinstance(files, list) or sorted(f.get("path", "") for f in files) != expected:
        raise ValueError("FORMAT_SNAPSHOT_SCOPE_MISMATCH")
    with tempfile.TemporaryDirectory(prefix="format-data-") as tmp:
        candidate = Path(tmp)
        total = 0
        for entry in files:
            name = entry["path"]
            if not re.fullmatch(r"backend/[A-Za-z0-9_./-]+\.pyi?", name) or any(
                part in ("", ".", "..", ".git") for part in name.split("/")
            ):
                raise ValueError("INVALID_FORMAT_PATH")
            if entry.get("mode") not in ("100644", "100755"):
                raise ValueError("FORMAT_NOT_TRACKED_REGULAR")
            content = base64.b64decode(entry["content"], validate=True)
            total += len(content)
            if len(content) > 1_048_576 or total > 2_097_152:
                raise ValueError("FORMAT_SCOPE_TOO_LARGE")
            blob_sha = hashlib.sha1(
                b"blob " + str(len(content)).encode() + b"\0" + content
            ).hexdigest()
            if blob_sha != entry.get("sha"):
                raise ValueError("FORMAT_BLOB_HASH_MISMATCH")
            target = candidate / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
            target.chmod(int(entry["mode"], 8) & 0o777)
        git(candidate, "init", "-q", "--template=")
        git(candidate, "add", "--", "backend")
        git(
            candidate,
            "-c",
            "user.name=formatter",
            "-c",
            "user.email=formatter@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "Data-only formatting baseline",
        )
        baseline = git(candidate, "rev-parse", "HEAD").decode().strip()
        return generate(
            candidate,
            trusted,
            output,
            scope,
            baseline,
            evidence,
            metadata_sha=source_sha,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted", type=Path, required=True)
    parser.add_argument("--requirements", action="store_true")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.requirements:
        print(locked_requirements(args.trusted))
        return
    (generate_snapshot if args.snapshot else generate)(
        args.snapshot or args.candidate,
        args.trusted,
        args.output,
        json.loads(os.environ["FIX_SCOPE"]),
        os.environ["SOURCE_SHA"],
        os.environ["EVIDENCE"],
    )


if __name__ == "__main__":
    main()
