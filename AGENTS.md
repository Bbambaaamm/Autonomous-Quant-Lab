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
