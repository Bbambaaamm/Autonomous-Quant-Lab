# Persistentní PaperBroker

Účet persistuje cash, equity, high-water mark, realized P/L a trading state. Pozice uchovává množství, průměrnou zbývající basis a FIFO lots. Lifecycle je `SUBMITTED → PARTIALLY_FILLED → FILLED`; otevřený order lze idempotentně zrušit, filled nikoli.

MARKET používá povolenou raw executable cenu a nepříznivou bps slippage. LIMIT BUY filluje při `low <= limit`, SELL při `high >= limit`; gap respektuje open. OHLC neurčuje intrabar pořadí. Capacity je deterministický podíl volume. Komise vzniká jednou per fill; vstupní komise je v lot basis a výstupní v realized P/L. Fill, cash, FIFO/position, order, equity a audit jsou atomické. PostgreSQL zámky řádku příkazu a účtu serializují souběžné filly a cash update. Unique client ID a `(order, sequence)` brání duplicitě; databázové check constraints navíc vynucují kladný fill, neoverfill a `remaining = quantity - filled`.

Adverse slippage nikdy neporuší cenovou garanci limit orderu: BUY fill je nejvýše limit a SELL fill
nejméně limit. Otevřený zbytek partial fillu se započítává do dalšího target-vs-actual výpočtu.

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

Close-derived target používá přesný, souvislý strategy lookback končící před executable session
a fill proto nastane nejdříve na raw open následující session. Runtime vynucuje deklarovanou denní,
týdenní nebo měsíční rebalance frekvenci. Sparse target doplní držené pozice nulovou vahou, takže
ztracený signál nebo odchod z PIT universe vede přes standardní risk cestu k likvidaci; chybějící
executable bar likvidaci raději uzavřeně odmítne. Multi-asset risk oceňuje všechny pozice raw open
cenami dostupnými v okamžiku execution, nikoli pozdějšími close cenami.
