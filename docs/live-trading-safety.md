# Live trading safety

**Phase 4 is paper-only. No live broker exists.** Neexistuje live order path, credentials ani environment variable schopná aktivovat live obchod.

Budoucí live fáze musí samostatně vyžadovat live mode, enablement, tajné confirmation, oddělené credentials, allowlist, limity, kill switch, reconciliation, canary a runbook. Chybějící podmínka musí fail closed; samotné credentials nikdy nesmí aktivovat live trading.

Phase 5 scheduler a worker tuto hranici nemění: job config explicitně odmítá `broker`, `mode`,
`live`, `trading_mode` a `live_trading_enabled`; executor zná pouze Phase 4 paper services.

## Phase 6
Phase 6 je implementována jako provider → validace/immutable revisions → XNYS calendar/corporate actions → PIT universe → immutable snapshot → multi-asset target portfolio. Detailní invariants jsou v `docs/market-data.md` a `docs/strategy-research.md`. Žádná část nevytváří live execution path; automatický data refresh zatím není allowlistovaný job a refresh se provádí odděleně od trading cycle.

Deployment manifest vyžaduje explicitní ruční schválení a `paper_account_id`; sám příkazy nevytváří. Jedinou autoritativní realizací nadále zůstává `TradingCycleService` → `RiskEngine` → `PersistentPaperBroker`. Market-data refresh nemá ekonomický executor.

## Phase 6 remains paper only

Phase 6 nepřidává live broker, live order adapter, live credentials ani `LIVE` execution mode. Provider, strategy a deployment nesmějí submitovat objednávky. Ruční approval pouze zpřístupní evidence existujícímu toku `TradingCycleService → ProductionRiskEngine → PersistentPaperBroker`; nevytváří alternativní execution path a nemůže obejít Phase 4 `HALTED` nebo reconciliation safety.

## Phase 6 safety invariants
XNYS schedule pochází z `exchange-calendars` 4.13.2 / XNYS a lineage nese `XNYS:exchange-calendars:4.13.2`; není založen na vlastní holiday tabulce. Immutable snapshot, exactly-once experiment a OOS isolation nejsou autorizace k obchodování. Deployment ani approval nejsou automatické, current feed není research replay a `HALTED` nelze obejít. Systém je paper-only: bez live credentials, live brokeru a live order path; jediná ekonomická cesta je stávající Phase 4 risk/execution/broker/reconciliation cesta.

### Phase 6 research → paper audit boundary

Autoritativní workflow je `COMPLETED/RESEARCH_ONLY` experiment → explicitní
`Phase6EligibilityService.promote()` → `PAPER_CANDIDATE` → explicitní
`DeploymentService.create()` → `PENDING_REVIEW` → explicitní `approve()` → `APPROVED` →
`ValidatedCurrentDataAccessor` → `Phase6PaperExecutionService` → existující Phase 4
`TradingCycleService` / `ProductionRiskEngine` / `PersistentPaperBroker` → reconciliation.
Promotion ani deployment nevznikají automaticky a opakovaná promotion je idempotentní.

`PAPER_CANDIDATE` není automatický deployment a `APPROVED` neobchází risk engine ani stav
`HALTED`. Research snapshot slouží pouze jako immutable lineage; current execution feed pochází z
nejnovější dokončené XNYS session a přijímá jen nejnovější revizi z úspěšné ingestion. Runtime
rekonstruuje pouze přesnou allowlisted strategii, verzi, parametry, PIT universe a USD/XNYS/1d
scope. Live trading path nadále neexistuje.
