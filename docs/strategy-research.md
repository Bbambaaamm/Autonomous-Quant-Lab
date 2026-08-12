# Multi-asset strategy research Phase 6

Strategie implementuje `generate_targets(StrategyContext) -> TargetPortfolio`, deklaruje lookback a DAILY/WEEKLY/MONTHLY rebalance. Context konstrukčně odmítá observation po decision time a asset mimo PIT universe. Target je canonical-ID sorted, long-only, konečný, nezáporný a gross exposure nejvýše 1; žádný eligible asset znamená 100 % cash.

Baseline registry obsahuje multi-asset MA trend (`fast`, `slow`), cross-sectional momentum (`lookback`, `top_n`, deterministický tie-break canonical ID) a distance-from-mean reversion (`lookback`, `threshold`). Chybějící lookback asset explicitně vyřadí. Monthly/weekly hranice používá dostupné společné XNYS sessions.

Engine vede společnou USD hotovost, více pozic, cost basis, fills a portfolio equity. Nejde o součet single-symbol backtestů. Sells proběhnou deterministicky před buys, množství jsou whole-share a buy je ořezán dostupnou hotovostí včetně fee. Nově obchodovat lze jen asset s čerstvým raw open; existing stale valuation starší než policy limit selže. Multi-currency universe bez FX selže uzavřeně.

Universe typu `POINT_IN_TIME_MEMBERSHIP` filtruje `valid_from <= decision < valid_to` i `known_at <= knowledge_as_of`. `STATIC` je vždy označen `BIAS_PRONE_STATIC`: dnešní static seznam není survivorship-bias-free. Budoucí prices, membership a corporate actions nesmí ovlivnit prefix rozhodnutí.

Parametr selection zůstává chronologická IS → validation → OOS pipeline Phase 3; OOS se pouze jednou vyhodnotí a není vstupem výběru. Experiment manifest musí odkazovat na immutable snapshot, coverage, strategy version/parameters, commit a seed. Promotion pouze mění research candidate na schválenou konfiguraci stávající paper pipeline; strategie nikdy neposílá broker order přímo.

## Persistentní lineage a otevřený runner
Schema experiment registry má FK na Phase 6 snapshot a sloupce multi-asset metrik. Kompletní runner IS → validation → exactly-once OOS nad `run_multi_asset` však dosud není zapojen; není povolen silent fallback na legacy `DatasetRecord` a audit gate zůstává otevřený.

`strategy_deployments` je explicitní, ručně schvalovaný manifest strategie/verze, parametrů, universe, paper účtu, experimentu, snapshotu, měny a timeframe. Schválení failne bez `VALID` snapshotu a shodné experiment lineage. Manifest nevytváří nový execution path.
