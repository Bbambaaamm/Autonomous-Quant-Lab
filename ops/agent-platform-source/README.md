# Agent Platform / Herdr source-of-truth capture

This directory captures the exact custom Agent Platform / Machine City source observed on `quantlab-staging-01` on 2026-09-26.

## Authoritative capture

- `archives/agent-platform-source-20260926.tar.gz` is the byte-exact snapshot.
- `SOURCE_MANIFEST.json` records per-file SHA-256 and size for verification.
- `deployed-release/` inside the archive mirrors source served from `/opt/agent-platform/release`.
- `development-source/` captures the newer Machine City / Agent Platform working source from `/home/agentops/workspaces/majak/agent-platform-impl`.
- `runtime/bin/` captures the custom agent-stack orchestration scripts that previously existed only on the staging server.
- `runtime/systemd/` captures the relevant service/timer units.
- Secrets and live credential files are intentionally excluded.

## Provenance

Development source upstream: `https://github.com/NousResearch/hermes-agent.git`

Observed development HEAD: `f40ce8ba4c1ac2ce6f35468caafe96526de9708f`

Observed upstream `origin/main`: `a6578fcaa5b69267065de53491165f4e453224f9`

Herdr runtime: `herdr 0.9.1`

Observed Herdr binary SHA-256: `2a02fed16beb651ef006e1d43f048f652ca4dc58ad053cd2d44450563d5c54b7`

The external Herdr executable is pinned by version/hash and is not vendored as source.

## Captured archive

- files: 103
- size: 1,067,159 bytes
- SHA-256: `d7e66523cc2bb4352cb29eeb42ad21448044b75b21261144558b92739441d8f4`

## Deployment divergence at capture time

The production Machine City files were not byte-identical to the development tree. The development source already contained newer modular UI components (`dashboard-ui.js`, `machine-core.js`, `machine-entities.js`, `machine-head.js`, `machine-scene.js`) not yet present in the deployed release.

Future Agent Platform / Machine City work should originate from GitHub-reviewed source rather than server-only edits.
