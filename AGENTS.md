# Autonomous Quant Lab Agent Instructions

## Project principles
- Correctness over speed; all internal timestamps are timezone-aware UTC.
- Never introduce look-ahead bias or silently discard invalid market data.
- Paper trading is the default. Never send live orders in tests or demos.
- Every order follows Strategy → Portfolio → RiskEngine → ExecutionEngine → Broker.
- Strategies never call brokers directly; risk controls must not be bypassed.
- Financial calculations are deterministic and every decision is auditable.
- Never commit secrets or weaken controls to make a test pass.

## Required workflow
Before finishing: inspect the affected architecture, implement, format, lint, type-check,
run unit and relevant integration tests, and update documentation.

## Quantitative conventions
- Daily bar timestamps denote the close; a close-derived signal executes no earlier than
  the next bar's open.
- Strategies expose lookback and rebalance frequency and receive only point-in-time data.
- Use adjusted prices consistently for signals and raw executable OHLC prices for fills.
- Time-series validation is chronological; random train/test splits are forbidden.

## Testing and safety
- Add regression tests for defects and explicit future-data leakage tests.
- CI and development use only `PaperBroker`.
- Live trading requires independent mode, enablement, and confirmation gates and fails closed.

## Dependency and uv.lock policy
- `backend/uv.lock` must be committed and is the authoritative dependency lockfile.
- Never manually edit URLs, hashes, or package entries in `backend/uv.lock`; only `uv` may
  generate the lockfile.
- Every dependency change in `backend/pyproject.toml` must be followed by the commands below
  and the relevant tests:

  ```bash
  cd backend
  uv lock
  uv lock --check
  uv sync --locked --all-groups
  ```

- A changed `backend/uv.lock` may be committed only after
  `uv sync --locked --all-groups` succeeds in the same environment.
- If package registry or PyPI access is unavailable, do not generate or commit a new lockfile
  from partial or fallback metadata. Report dependency verification as
  `BLOCKED BY ENVIRONMENT`.
- A dependency task is not `COMPLETE` until both `uv lock --check` and
  `uv sync --locked --all-groups` succeed.
- Do not add a dependency when the existing stack can reasonably solve the problem. Every new
  dependency requires a concrete justification.
