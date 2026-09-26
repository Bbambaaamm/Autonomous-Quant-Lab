#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
(
  cd "$ROOT"
  sha256sum -c DEPLOYED_MANIFEST.sha256
)
echo "Git snapshot manifest is internally consistent."
echo "Runtime comparison is an operator/deploy step; installed paths are deployment targets, not source."
