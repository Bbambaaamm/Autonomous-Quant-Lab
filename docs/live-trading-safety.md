# Live trading safety

**Phase 4 is paper-only. No live broker exists.** Neexistuje live order path, credentials ani environment variable schopná aktivovat live obchod.

Budoucí live fáze musí samostatně vyžadovat live mode, enablement, tajné confirmation, oddělené credentials, allowlist, limity, kill switch, reconciliation, canary a runbook. Chybějící podmínka musí fail closed; samotné credentials nikdy nesmí aktivovat live trading.

Phase 5 scheduler a worker tuto hranici nemění: job config explicitně odmítá `broker`, `mode`,
`live`, `trading_mode` a `live_trading_enabled`; executor zná pouze Phase 4 paper services.
