# H3 — neměnná identita schválené runtime konfigurace

## Původní nález

Schválený deployment pinoval strategii a data lineage, ale produkční cesta vytvářela risk engine,
commission, slippage a broker z procesních defaultů. Stejné deployment ID proto mohlo po změně
kódu provést jiné ekonomické rozhodnutí.

## Manifest schema a hash

Server při vytvoření deploymentu sestaví allowlisted schema verze `1`. Manifest obsahuje všechny
skutečně používané limity `ProductionRiskConfig`, target-weight sizing a `ROUND_DOWN`, cash a
pending-order pravidla, fixed-plus-rate commission, directional fixed-bps slippage, MARKET/LIMIT
price selection, volume-based partial fills, FIFO lots a identitu persistentního paper brokeru.
Pokud experiment bezpečně zná `code_sha`, manifest jej převezme; žádný SHA se nevymýšlí.

JSON je serializován s lexikograficky řazenými klíči a kompaktními separátory a hashován SHA-256.
Neobsahuje čas, UUID, cesty, credentials ani jiné environment hodnoty či secrets.

## Deployment a approval identita

Hash manifestu vstupuje do SHA-256 `deployment_id`. Změna ekonomické konfigurace proto vytvoří
jiné deployment ID a vyžaduje nové approval. Approval znovu ověří canonical hash a audit event
referencuje hash i schema verzi. PostgreSQL trigger odmítne změnu JSON/hash/verze po přechodu do
`APPROVED`.

## Runtime drift a legacy chování

`Phase6PaperExecutionService` načte manifest výhradně z autoritativního deployment recordu;
worker payload zůstává pouze `{"deployment_id":"..."}`. Před vytvořením cycle ověří schema,
canonical hash a podporované modelové identity. Z manifestu vytvoří nový `ProductionRiskEngine`,
`PersistentPaperBroker`, commission a slippage komponenty a právě ty použije v celé cestě
Strategy → Portfolio/sizing → RiskEngine → ExecutionEngine → PersistentPaperBroker.

Chybějící legacy identita končí `RUNTIME_CONFIG_IDENTITY_MISSING`; poškozený či driftovaný record
končí `RUNTIME_CONFIG_MISMATCH` nebo explicitní unsupported/invalid reason. Kontrola proběhne před
corporate action aplikací i před vznikem cycle/order/fill, takže selhání nemá ekonomický efekt.

## Auditní lineage

Deployment DATA_VALIDATED audit cycle ukládá deployment ID, manifest hash/verzi a risk,
commission, slippage a execution identity. Existující vazby fill → order → trading cycle a Phase 7
cycle lineage → deployment pak jednoznačně vedou k neměnnému schválenému manifestu bez kopírování
celého JSON do každého orderu či fillu.

## Test evidence a omezení readiness

Unit testy ověřují canonical stabilitu, změnu hashe při změně risku, rekonstrukci risk/cost/slippage
a odmítnutí driftu execution semantics. PostgreSQL H3 acceptance ověřuje approval immutability a
fail-closed legacy record; stávající B1, B2, P0-A, P0-B, Stage C a H2 kroky zůstávají required.

H3 je vyřešeno, ale systém jako celek zůstává **NOT READY**: stále chybí production zdroj
corporate actions a úplná Stage C acceptance HIGH remediation.
