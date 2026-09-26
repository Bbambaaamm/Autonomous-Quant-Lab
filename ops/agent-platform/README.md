# Agent Platform / Herdr / Machine City

This directory is the Git source of truth for the custom Agent Platform code deployed on `quantlab-staging-01`.

Imported from the currently deployed runtime on 2026-09-26:
- Machine City/dashboard source: `/opt/agent-platform/release/agent_platform_dashboard`
- production deployment templates: `/opt/agent-platform/release/deploy/agent_platform/production`
- Herdr/Hermes orchestration scripts: `/home/agentops/.local/bin/agent-*`
- maintenance/off-site runtime scripts: `hermes-maintenance`, `hermes-offsite-prepare`, `hermes-offsite-sync`
- agent profile launch wrappers: `quantlab`, `majak`
- current systemd runtime contracts copied under `runtime/` and `agent-stack/systemd/`

## Vendor Herdr binary

The compiled Herdr binary is **not source code and is not committed**.

- version: `herdr 0.9.1`
- deployed SHA-256: `2a02fed16beb651ef006e1d43f048f652ca4dc58ad053cd2d44450563d5c54b7`
- deployed paths: `/opt/agent-platform/release/herdr` and `/home/agentops/.local/bin/herdr`

Future binary upgrades must update the version/checksum evidence here or in a reviewed release manifest.

## Secrets and runtime state

Runtime credentials, Hermes auth/state databases, `/etc/agent-platform/*.json` secrets, private keys, queue state and logs are intentionally excluded.
Only source, static assets, non-secret service contracts and example configuration belong in Git.

`hermes-offsite-connect-drive` is intentionally not part of the active runtime source closure: it is an operator-only bootstrap helper and contains environment-specific Drive targeting. The running watchdog/timer path does not execute it. External installed executables such as `hermes`, `herdr`, `codex` and `rclone` are dependencies, not custom source.

## Change rule

Do not treat ad-hoc edits in `/opt/agent-platform/release` or `~/.local/bin/agent-*` as authoritative.
Changes should be reviewed in Git first, then copied/deployed from the reviewed revision.

`DEPLOYED_MANIFEST.sha256` records the imported deployment snapshot for drift detection.
`check-source-closure.sh` verifies that every custom script/unit required by the active watchdog + maintenance path is captured before review.
