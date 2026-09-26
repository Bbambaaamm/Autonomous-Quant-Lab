#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATIC="$ROOT/agent_platform_dashboard/static"
: "${ESBUILD_BIN:?set ESBUILD_BIN to esbuild 0.28.1}"
: "${THREE_ROOT:?set THREE_ROOT to the three 0.180.0 package directory}"

[[ "$("$ESBUILD_BIN" --version)" == "0.28.1" ]]
THREE_VERSION="$(node -p "require('${THREE_ROOT}/package.json').version")"
[[ "$THREE_VERSION" == "0.180.0" ]]

"$ESBUILD_BIN" "$STATIC/machine-city-source.js"   --bundle   --minify   --format=iife   --alias:three/addons="$THREE_ROOT/examples/jsm"   --alias:three="$THREE_ROOT/build/three.module.js"   --outfile="$STATIC/machine-city.js"