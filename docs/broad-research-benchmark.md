# Broad research benchmark

Issue #221 requires measured evidence before the broad market universe is used for authoritative Phase 6 research.

The benchmark is intentionally synthetic and deterministic. It seeds a persisted immutable snapshot and runs it in a separate process through the authoritative `Phase6ExperimentRunner.replay` path with:

- 2,000 instruments;
- 250 sessions;
- 500,000 daily bars;
- `SNAPSHOT_PINNED` knowledge semantics;
- the production `multi_asset_trend 1.0.0` strategy shape;
- bounded database verification, canonical manifest hash verification and repeated train/validation/OOS evaluation;
- portfolio accounting and next-session execution.

Acceptance budgets:

- research child peak RSS <= **1024 MiB**;
- total fixture-build + research wall time <= **180 s** on the GitHub-hosted runner;
- all 2,000 PIT instruments must produce execution evidence;
- all 250 dates are real XNYS trading sessions.

The benchmark runs inside the required `unit-research` CI context on every pull request, so its RAM/time limits are merge-enforced even when a dependency outside the engine files changes. CI uploads the JSON report as a 14-day artifact. `.github/workflows/broad-research-benchmark.yml` remains a manual independent rerun that produces the same evidence.

This is a scalability gate, not an investment-performance result. It does not make a current-universe screening historically PIT-valid and it cannot promote a strategy to PAPER.
