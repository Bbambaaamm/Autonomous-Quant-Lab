"""Generate a bounded formatting artifact without model or write credentials."""

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
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


def generate(candidate, trusted, output, scope, source_sha, evidence):
    candidate, trusted = candidate.resolve(), trusted.resolve()
    if not re.fullmatch(r"[0-9a-f]{40}", source_sha):
        raise ValueError("INVALID_SOURCE_SHA")
    if git(candidate, "rev-parse", "HEAD").decode().strip() != source_sha:
        raise ValueError("SOURCE_SHA_MISMATCH")
    if git(candidate, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("DIRTY_FORMAT_CHECKOUT")
    if not isinstance(scope, list) or not scope or any(not isinstance(p, str) for p in scope):
        raise ValueError("INVALID_FORMAT_SCOPE")
    paths = sorted({p for p in scope if p.startswith("backend/") and p.endswith(".py")})
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
        json.dumps({"source_sha": source_sha, "evidence": evidence, "failure_class": "lint-format"})
    )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trusted", type=Path, required=True)
    parser.add_argument("--requirements", action="store_true")
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.requirements:
        print(locked_requirements(args.trusted))
        return
    generate(
        args.candidate,
        args.trusted,
        args.output,
        json.loads(os.environ["FIX_SCOPE"]),
        os.environ["SOURCE_SHA"],
        os.environ["EVIDENCE"],
    )


if __name__ == "__main__":
    main()
