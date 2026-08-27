# Operational Readiness H2-A — corporate-action readiness gate

## Root cause a rozsah

Stooq správně deklaroval `supports_actions=False`, ale jeho `corporate_actions()` vracelo `[]`.
Ingestion i autonomní `PREPARE_PAPER_SESSION` prázdný výsledek nerozlišovaly od autoritativního
potvrzení, že v intervalu žádná akce nenastala. Phase 6 následně upravila signal prices pouze podle
řádků, které už v databázi existovaly, a mohla vytvořit paper order bez důkazu úplnosti. Úspěšné
načtení barů tak bylo chybně použitelné jako implicitní action readiness.

Tato změna řeší **unsafe H2 execution path — RESOLVED**. Nezavádí externí zdroj corporate actions;
**production corporate-action source — STILL OPEN**. Celkový systém proto není deklarován jako
production-ready.

## Tři odlišné stavy

1. `supports_actions=True` a úspěšná odpověď vytvoří `COMPLETE` evidence pro přesný provider,
   instrument, interval a knowledge cutoff.
2. Stejná úspěšná odpověď smí obsahovat `[]`; jde o legitimní **empty-but-complete** interval.
3. `supports_actions=False` vytvoří `UNSUPPORTED` evidence s důvodem
   `CORPORATE_ACTIONS_UNSUPPORTED`. Metoda provideru se v tomto stavu vůbec nepoužije jako důkaz.

Chyba capable provideru vytvoří `FAILED` evidence s důvodem `CORPORATE_ACTIONS_UNAVAILABLE` a
propaguje chybu do stávající retry cesty. Staré action řádky samy o sobě readiness nikdy
neprokazují.

## Evidence, idempotence a PIT

Tabulka `corporate_action_readiness` je oddělená od price-ingestion statusu. Deterministické ID je
hash provider identity a verze, instrumentu, kontrolovaného intervalu, UTC knowledge cutoffu,
výsledku a identity vrácených akcí.
Opakovaná kontrola stejného scope proto nevytváří duplicity. Evidence obsahuje capability,
`checked_at`, výsledek, blocking reason a počet přijatých akcí.

Provider smí pro action-complete scope vrátit pouze akce stejného instrumentu s
`known_at <= knowledge_cutoff`. Future-known split nebo dividenda kontrolu zneplatní; nedostane se
do adjusted-price ani paper-ledger cesty. Snapshot a Phase 6 nadále používají pouze kauzálně známé
akce.

## Dvě fail-closed brány

`PREPARE_PAPER_SESSION` před vytvořením executable occurrence ověřuje všechny equity instrumenty
z PIT universe i držených pozic. Scope začíná na začátku pinovaného snapshotu a končí signal
session. Unsupported Stooq vrátí auditovatelný `NOT_READY / CORPORATE_ACTIONS_UNSUPPORTED`; žádný
execution run se nematerializuje.

`Phase6PaperExecutionService` důkaz kontroluje znovu před strategií, aplikací corporate actions a
trading cycle. Vyžaduje `COMPLETE`, capable evidence pro každý executable instrument, pokrytí
požadovaného intervalu, přesný decision-time cutoff a kontrolu provedenou nejpozději v execution
čase. Evidence se navíc musí shodovat se jménem i verzí provideru, které production executor
získal ze stejné provider factory; complete evidence jiného provideru proto gate neodemkne.
Scheduler/worker race ani přímé obejití prepare brány proto nemůže mít ekonomický efekt.

`PaperCorporateActionService` zůstává jedinou cestou, která mění paper quantity, cost basis nebo
dividend cash. Nová readiness evidence nic neúčtuje a nevytváří paralelní ledger.

## Standardní Stooq a zbývající práce

Standardní Stooq má nadále `supports_actions=False`. Autonomní equity deployment přes něj je nyní
záměrně `NOT_READY`; raw ani případně adjusted cena není náhradou za prokazatelnou úplnost akcí.
Pro skutečné production nasazení je stále nutné dodat a samostatně validovat production provider
corporate actions s úplností intervalu, stabilní identitou a PIT `known_at` metadaty. Tento PR
nepřidává live broker, live flag, H3, ML ani jinou execution cestu.
