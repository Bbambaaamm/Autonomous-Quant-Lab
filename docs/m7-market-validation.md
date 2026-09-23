# M7 — authoritative multi-instrument validation for #164

## What changed after review of PR #176

PR #176 proved that a read-only multi-instrument validation path can be reproducible,
but its first methodology is not accepted as M7 evidence. Post-merge review correctly
identified four problems: backdated knowledge of an ad-hoc ETF universe, a report that
kept only an observation digest, caller-supplied code SHA, and raw-price momentum across
cash-dividend ex-dates.

The current validator fixes those issues by refusing to create a universe or historical
knowledge itself.

## Authoritative lineage

The validator starts from an already APPROVED PAPER deployment and follows its existing
immutable lineage:

```text
approved PAPER deployment
→ persisted Phase 6 experiment
→ VALID immutable dataset snapshot
→ persisted POINT_IN_TIME_MEMBERSHIP universe
→ immutable observation IDs/revisions/source hashes
→ immutable corporate-action revisions
```

A STATIC universe, an unpersisted membership, a changed observation revision or a
manifest/content-hash mismatch fails closed. The validator never substitutes today's
catalog and never assigns an inception date as a fabricated knowledge timestamp.

The report references the immutable snapshot by snapshot ID and content hash, verifies
that hash against the complete manifest, and records observation, membership and
corporate-action counts. The source manifest remains preserved in the immutable snapshot.

## Experiment replay and OOS

The original parameter budget, train fraction, validation fraction, initial capital,
commission model and seed come from the persisted experiment config. The validator
replays the same experiment using the current checked-out/runtime implementation and
requires the replayed OOS signature to match the already persisted OOS result. A mismatch
fails the validation rather than silently replacing historical evidence.

The OOS result is therefore not used to choose a new configuration. The approved
deployment and research-promotion state are read-only inputs and are not modified.

## Code identity

The validation code SHA is derived by the same runtime identity path as Phase 6
(`QUANTLAB_CODE_SHA` when deployed, otherwise the checked-out Git SHA). There is no
`--code-sha` CLI argument. The report records the original experiment code SHA
separately from the current validator code SHA.

## Price semantics and benchmark

Both the replay and the equal-weight monthly benchmark use the immutable corporate-action
evidence from the Phase 6 snapshot. `run_multi_asset` supplies causally adjusted prices
to strategy signals while executions remain on raw OHLC. A current REST receipt is not
backdated into the Phase 6 snapshot.

The benchmark uses the same OOS start, universe, observations, initial cash and
commission rate as the replayed strategy.

## Running the check

```bash
python scripts/run-m7-market-validation.py \
  --env-file .env.production \
  --db-host <database-host>
```

An explicit `--deployment-id` can pin one approved PAPER deployment. Without it, the
latest approved deployment for `paper-main` is selected deterministically.

Production verification must run twice against the same deployed state. Identical
`report_hash` values establish deterministic replay. The report is infrastructure
validation, not investment advice, a promotion decision or authorization for live trading.


## Integrity prerequisites and failure modes

The production acceptance run fails closed unless all of the following hold:

- the runtime Git identity matches the validator SHA and the M7 runtime paths
  (`backend/src`, `backend/bin`, and the M7 runner script) contain neither modified
  nor untracked files;
- the persisted experiment seed matches the precommitted config seed and the complete
  canonical config still hashes to the persisted experiment id/idempotency key;
- the snapshot manifest `logical_identity` exactly matches the persisted provider,
  calendar, universe, start/end and `as_of` fields, and that logical identity plus
  content hash recreates the persisted snapshot id;
- every daily observation is pinned to the exchange-calendar close, is not observed
  before that close, and both timestamp and knowledge time are at or before snapshot
  `as_of`;
- currency metadata exists for every immutable universe member, including members with
  missing observations in a partial-coverage snapshot, and every member currency matches
  the approved PAPER deployment/account currency.

Representative fail-closed codes are
`M7_VALIDATOR_CHECKOUT_DIRTY`, `M7_EXPERIMENT_SEED_MISMATCH`,
`M7_EXPERIMENT_IDENTITY_MISMATCH`, `M7_SNAPSHOT_LOGICAL_IDENTITY_MISMATCH`,
`M7_SNAPSHOT_ID_MISMATCH`, `M7_OBSERVATION_TIME_INCONSISTENT`, and
`M7_INSTRUMENT_CURRENCY_MISMATCH`. None of these conditions may be waived by the
validation command.
