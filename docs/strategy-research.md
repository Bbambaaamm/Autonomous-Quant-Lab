# Multi-asset strategy research Phase 6

Strategie implementuje `generate_targets(StrategyContext) -> TargetPortfolio`, deklaruje lookback a DAILY/WEEKLY/MONTHLY rebalance. Context konstrukčně odmítá observation po decision time a asset mimo PIT universe. Target je canonical-ID sorted, long-only, konečný, nezáporný a gross exposure nejvýše 1; žádný eligible asset znamená 100 % cash.

Baseline registry obsahuje multi-asset MA trend (`fast`, `slow`), cross-sectional momentum (`lookback`, `top_n`, deterministický tie-break canonical ID) a distance-from-mean reversion (`lookback`, `threshold`). Chybějící lookback asset explicitně vyřadí. Monthly/weekly hranice používá dostupné společné XNYS sessions.

Engine vede společnou USD hotovost, více pozic, cost basis, fills a portfolio equity. Nejde o součet single-symbol backtestů. Sells proběhnou deterministicky před buys, množství jsou whole-share a buy je ořezán dostupnou hotovostí včetně fee. Nově obchodovat lze jen asset s čerstvým raw open; existing stale valuation starší než policy limit selže. Multi-currency universe bez FX selže uzavřeně.

Universe typu `POINT_IN_TIME_MEMBERSHIP` filtruje `valid_from <= decision < valid_to` i `known_at <= knowledge_as_of`. `STATIC` je vždy označen `BIAS_PRONE_STATIC`: dnešní static seznam není survivorship-bias-free. Budoucí prices, membership a corporate actions nesmí ovlivnit prefix rozhodnutí.

Parametr selection zůstává chronologická IS → validation → OOS pipeline Phase 3; OOS se pouze jednou vyhodnotí a není vstupem výběru. Experiment manifest musí odkazovat na immutable snapshot, coverage, strategy version/parameters, commit a seed. Promotion pouze mění research candidate na schválenou konfiguraci stávající paper pipeline; strategie nikdy neposílá broker order přímo.

## Persistentní lineage
Schema experiment registry má FK na Phase 6 snapshot a sloupce multi-asset metrik. Runner nepovoluje silent fallback na legacy `DatasetRecord`.

`strategy_deployments` je explicitní, ručně schvalovaný manifest strategie/verze, parametrů, universe, paper účtu, experimentu, snapshotu, měny a timeframe. Schválení failne bez `VALID` snapshotu a shodné experiment lineage. Manifest nevytváří nový execution path.
# Persistentní Phase 6 experimenty

`Phase6ExperimentRunner` přijímá výhradně identitu `VALID` dataset snapshotu. Manifest je
před spuštěním ověřen proti přesným observation ID, revision a source hash; pozdější provider
oprava proto nemůže změnit rekonstruovaný experiment. Přesná dvojice jméno/verze strategie
musí být v registru a bounded parameter set se řadí kanonicky. Train, validation a OOS jsou
chronologicky disjunktní: všechny konfigurace projdou train a validation, výběr používá pouze
validation risk-adjusted return a OOS se vyhodnotí právě jednou.

Snapshot manifest verze 3 zahrnuje také kanonické corporate actions známé k `as_of`; jejich
obsah vstupuje do snapshot content hashe. Validation a OOS dostávají předchozí observations
pouze jako lookback warm-up, ale portfolio, fills i metriky vznikají až od začátku příslušného
evaluation okna. Runner před výpočtem načte měny všech instrumentů a mixed-currency universe
bez explicitní FX konverze odmítne.

Persistentní OOS metriky jsou total return, anualizovaný výnos, anualizovaná volatilita,
risk-adjusted return (Sharpe bez risk-free sazby), max drawdown, traded-notional turnover vůči
počátečnímu kapitálu, časově vážená gross exposure, počet fillů a celkové komise. Exposure je
integrál stavové portfolio exposure přes čas, nikoli počet nebo stav fillů.

Logická idempotency identita zahrnuje snapshot, přesnou strategii, celý parameter set, split,
kapitál, cost model, seed a skutečný Git SHA. PostgreSQL runner tuto identitu serializuje
transaction-scoped advisory lockem; opakování vrací stejný `ResearchExperiment` a nevytváří
druhé OOS vyhodnocení.

## Phase 6 selection isolation

Chronologické TRAIN a VALIDATION části rozhodnou o konfiguraci; OOS se spustí exactly once a nesmí selection ovlivnit. Experiment replay validuje immutable snapshot manifest včetně corporate actions a content hash. Pozdější provider correction proto nemění starý experiment. PAPER_CANDIDATE je pouze evidence pro ruční deployment approval, ne povolení přímého broker volání.
