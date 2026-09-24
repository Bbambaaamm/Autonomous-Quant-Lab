# Broad research benchmark

Issue #221 requires measured evidence before the broad market universe is used for authoritative Phase 6 research.

The benchmark is intentionally synthetic and deterministic. It exercises the same immutable `Observation`, PIT-universe and `run_multi_asset` path as Phase 6 with:

- 2,000 instruments;
- 250 sessions;
- 500,000 daily bars;
- `SNAPSHOT_PINNED` knowledge semantics;
- the production `multi_asset_trend 1.0.0` strategy shape;
- portfolio accounting and next-session execution.

Acceptance budgets:

- peak process RSS <= **1024 MiB**;
- total fixture-build + research wall time <= **180 s** on the GitHub-hosted runner;
- all 2,000 PIT instruments must be seen by the research engine.

The workflow `.github/workflows/broad-research-benchmark.yml` runs on performance-sensitive research changes and can also be started manually. It uploads the JSON report as a 14-day artifact.

This is a scalability gate, not an investment-performance result. It does not make a current-universe screening historically PIT-valid and it cannot promote a strategy to PAPER.
