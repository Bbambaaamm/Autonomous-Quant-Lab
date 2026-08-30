# Autonomous Quant Lab — Phase 6 Complete Implementation

Implementuj kompletní:

# Phase 6 — Market Data, Point-in-Time Universe & Strategy Expansion

Phase 6 rozšiřuje dosavadní research a paper-trading foundation o produkčně použitelnou datovou vrstvu a multi-asset research.

Phase 6 musí být dokončena end-to-end.

Nevracej pouze návrh, scaffold ani pseudokód.

---

# 1. Výchozí stav

Projekt již dokončil:

```text
Phase 1–3 research/data foundation
Phase 4 persistent paper trading + risk
Phase 4 Audit Gate
Phase 5 automation & operations
Phase 5 Audit Gate
```

Poslední známý stav:

```text
PHASE 5 COMPLETE
PHASE 5 AUDIT GATE PASSED WITH FIXES
READY FOR PHASE 6
```

Aktuální `main` je jediný zdroj pravdy.

Nepředpokládej konkrétní HEAD.

Před změnami zaznamenej:

```bash
git status
git rev-parse HEAD
git branch --show-current
git log --oneline -10
```

Working tree musí být čistý.

---

# 2. Authoritative sources

Nejdříve přečti celé:

* `AGENTS.md`
* `CODEX_MASTER_PROMPT.md`
* `README.md`
* `docs/codex/phase6-complete.md`
* `docs/architecture.md`
* `docs/implementation-plan.md`
* `docs/database.md`
* `docs/automation.md`
* `docs/operations.md`
* `docs/risk-management.md`
* `docs/paper-trading.md`
* `docs/live-trading-safety.md`
* `docs/phase4-audit.md`
* `docs/phase5-audit.md`

Dále přečti celý relevantní source tree:

* research engine
* market-data abstractions
* strategy abstractions
* experiment registry
* dataset registry
* Phase 4 runtime
* Phase 5 automation
* API
* config
* persistence
* celý Alembic strom
* všechny testy
* `.github/workflows/ci.yml`

Skutečný kód a schema mají přednost před starší dokumentací.

---

# 3. Baseline verification

Před implementací spusť maximum dostupného:

```bash
cd backend

uv --version
uv lock --check
uv sync --locked --all-groups

uv run ruff format --check .
uv run ruff check .
uv run mypy src/quantlab
uv run pytest -q
```

Pokud něco objektivně blokuje prostředí:

```text
BLOCKED BY ENVIRONMENT
```

Nevydávej environmentální blokaci za PASS.

---

# 4. Hlavní cíl Phase 6

Po dokončení musí pipeline vypadat přibližně:

```text
external market-data provider
→ normalized immutable observations
→ exchange calendar/session normalization
→ corporate actions
→ point-in-time universe
→ immutable dataset snapshot
→ multi-asset strategy
→ target portfolio
→ research/backtest
→ experiment registry
→ optional approved strategy deployment to existing paper runtime
```

Celý systém musí být:

```text
causal
point-in-time safe
survivorship-bias aware
multi-asset capable
reproducible
deterministic
provider-independent
paper-only
```

---

# 5. Phase 6 scope

Implementuj kompletně:

## 6.1 Market Data Provider Layer

## 6.2 Canonical Instruments & Market Sessions

## 6.3 Corporate Actions & Price Semantics

## 6.4 Point-in-Time Universe

## 6.5 Immutable Dataset Snapshots

## 6.6 Multi-Asset Research Engine

## 6.7 Strategy Library Expansion

## 6.8 Strategy Evaluation & Registry Integration

## 6.9 Phase 4/5 Integration

## 6.10 API, CI, PostgreSQL & Documentation

---

# 6. Non-goals

Neimplementuj:

* live broker;
* live order execution;
* ML;
* neural networks;
* reinforcement learning;
* options;
* futures;
* crypto;
* short selling;
* margin trading;
* leveraged portfolio;
* Next.js frontend;
* auth/RBAC;
* full monitoring stack;
* distributed data lake;
* Kafka;
* Spark;
* Airflow;
* Redis, pokud není již nutnou existující dependency.

Phase 6 je stále primárně:

```text
long-only equities
research + paper
```

---

# 7. Absolutní trading safety

Musí zůstat:

```text
paper only
no live broker
no live order path
```

Žádná data/strategy změna nesmí vytvořit nový execution path kolem:

```text
TradingCycleService
→ RiskEngine
→ PersistentPaperBroker
```

---

# 8. Provider abstraction

Implementuj stabilní rozhraní typu:

```python
class MarketDataProvider(Protocol):
    ...
```

nebo architektonicky vhodný ekvivalent.

Provider musí umět minimálně:

```text
instrument discovery / symbol resolution
historical daily OHLCV
incremental daily OHLCV update
corporate actions if provider supports them
provider metadata
```

Research engine nesmí být závislý na konkrétním vendor API.

---

# 9. First external provider

Implementuj alespoň jeden skutečný externí market-data adapter.

Pokud již master plan nebo repository konkrétního providera určuje, použij jej.

Pokud ne:

* zvol jednoduchého a auditovatelného providera vhodného pro historical equity research;
* preferuj řešení s malým dependency footprintem;
* provider musí být konfigurovatelný;
* credentials, pokud jsou nutné, pouze přes environment/secrets;
* nikdy necommituj credentials.

Externí síť nesmí být nutná pro normální CI test suite.

---

# 10. Provider contract tests

Provider musí mít fixture-based contract tests.

CI nesmí záviset na dostupnosti internetu.

Testuj:

* valid response;
* empty response;
* malformed response;
* duplicate bars;
* timeout;
* HTTP/provider failure;
* invalid symbol;
* partial date range;
* rate limiting, pokud adapter tuto chybu rozlišuje.

Volitelný real-provider smoke test může být gated:

```text
RUN_EXTERNAL_DATA_TESTS=1
```

a nesmí být required pro běžný PR CI.

---

# 11. Provider resilience

Implementuj bounded retry pouze pro skutečně transientní chyby.

Nepoužívej nekonečné retry.

Rozliš:

```text
transient provider failure
rate limit
invalid symbol
invalid response
permanent configuration failure
```

---

# 12. Provider rate limiting

Nevytvářej agresivní paralelní downloader.

Respektuj provider limits.

Pokud provider vrací explicitní retry metadata, použij je bezpečně.

---

# 13. Canonical instrument model

Zaveď canonical instrument identity oddělenou od symbolu.

Instrument minimálně:

```text
instrument_id
symbol
exchange / venue
currency
asset_type
active_from
active_to
created_at
```

Symbol není bezpečná permanentní identita instrumentu.

---

# 14. Symbol history

Podpor symbol aliases/history.

Například změna tickeru nesmí vytvořit nový ekonomický instrument, pokud jde pouze o rename.

Modeluj například:

```text
instrument_symbol_history
```

s:

```text
instrument_id
symbol
valid_from
valid_to
```

---

# 15. No ticker-keyed economic identity

Zakázáno používat pouze:

```text
"AAPL"
```

jako permanentní PK research datasetu.

Použij canonical instrument ID.

---

# 16. Exchange/calendar identity

Instrument musí mít vazbu na exchange/calendar.

Nepoužívej jeden globální:

```text
Monday-Friday
```

kalendář pro všechny assets.

---

# 17. Production exchange calendar

Nahraď jednoduché weekday assumptions robustní exchange-session abstrakcí.

Preferuj dobře udržovanou existující Python calendar library, pokud je kompatibilní s projektem.

Neudržuj ručně dlouhé holiday tabulky bez důvodu.

---

# 18. Calendar service

Implementuj API typu:

```text
session_for_timestamp
next_session
previous_session
session_open
session_close
is_session
sessions_between
```

podle architektury projektu.

---

# 19. Calendar timestamps

Interní timestamps:

```text
UTC aware
```

Market session semantics používají exchange timezone.

---

# 20. Early closes

Kalendář musí správně podporovat early-close sessions, pokud je library poskytuje.

Nesmí se předpokládat stejný close time každý trading day.

---

# 21. Daily bar timestamp semantics

Zachovej nebo zpřesni invariant:

```text
daily bar timestamp = session close
```

Pokud provider vrací pouze datum:

mapuj jej přes exchange calendar na konkrétní session close timestamp.

---

# 22. Invalid session bars

Bar na non-session date musí být:

* odmítnut;
* nebo explicitně označen/normalizován podle provider contractu.

Nesmí být tiše přijat jako normální trading session.

---

# 23. Canonical OHLCV model

Persistentní observation musí obsahovat minimálně:

```text
instrument_id
provider
timeframe
session_date
timestamp
open
high
low
close
volume
observed_at
source identifier / source hash
```

---

# 24. OHLC invariants

Musí platit:

```text
high >= max(open, close, low)
low <= min(open, close, high)
volume >= 0
all prices finite
prices > 0
```

Nevalidní provider data nesmí vstoupit do research engine.

---

# 25. Raw vs adjusted semantics

Zachovej jasné rozdělení:

```text
raw executable prices
vs
signal/adjusted series
```

Execution/backtest fills používají raw executable prices.

Strategy signals mohou používat explicitně definovanou adjusted series.

---

# 26. No ambiguous adjusted_close

Pole typu:

```text
close
adjusted_close
```

musí mít přesně zdokumentovanou semantics.

Nesmí se implicitně míchat.

---

# 27. Corporate actions

Přidej explicitní model minimálně pro:

```text
SPLIT
CASH_DIVIDEND
SYMBOL_CHANGE
DELISTING
```

pokud provider/data source umožňuje informace získat.

---

# 28. Corporate action timestamps

Corporate action musí rozlišovat podle dostupných údajů:

```text
effective_at
known_at / observed_at
```

Research nesmí použít corporate action před okamžikem, kdy byla dostupná podle dataset snapshotu.

---

# 29. Split handling

Split musí být zpracován konzistentně:

* position quantity;
* cost basis;
* raw execution series;
* signal-adjusted series.

Split nesmí vytvořit falešný 50%/90% return.

---

# 30. Dividend handling

Pokud research accounting podporuje cash dividends:

dividend musí být explicitní cash event.

Nesmí být současně:

```text
included in adjusted return
+
credited again as cash
```

bez jasné semantics.

Zabraň double counting.

---

# 31. Adjustment engine

Pokud vytváříš adjusted signal prices:

implementuj explicitní adjustment engine.

Nespoléhej na nejasné provider-adjusted pole, pokud nelze ověřit jeho semantics.

---

# 32. Adjustment causality

Future corporate actions nesmí měnit historical strategy decision tak, že vznikne look-ahead.

Přidej test:

```text
adding a future split/dividend
must not alter earlier decisions
```

pokud signal adjustment policy takovou invariantu deklaruje.

---

# 33. Data revisions

Provider může historical bar později opravit.

Research reproducibility proto nesmí záviset pouze na mutable latest-value tabulce.

---

# 34. Observation/version semantics

Navrhni persistence tak, aby bylo možné určit:

```text
what data was known in snapshot X
```

Například přes:

```text
observed_at
revision
source checksum
```

nebo immutable ingestion batches.

---

# 35. Ingestion batch

Každý import/sync musí mít persistentní identity typu:

```text
market_data_ingestions
```

Minimálně:

```text
id
provider
started_at
finished_at
status
requested range
instrument count
row count
error summary
```

---

# 36. Atomic ingestion

Failed ingestion nesmí být prezentován jako successful complete dataset.

Partial provider failure musí být explicitně viditelný.

---

# 37. Incremental sync

Další sync nemá zbytečně znovu stahovat kompletní history.

Použij:

```text
latest known observation
+
bounded overlap window
```

pro zachycení pozdějších corrections.

Overlap musí být konfigurovatelný a testovaný.

---

# 38. Idempotent ingestion

Opakované ingestování stejného payloadu nesmí vytvořit ekonomicky duplicate bars/actions.

---

# 39. Correction handling

Pokud provider opraví historickou hodnotu:

nová observation/revision musí být dohledatelná.

Starý immutable dataset snapshot musí zůstat reprodukovatelný.

---

# 40. Dataset snapshot

Dataset používaný experimentem musí být immutable logical object.

Dataset snapshot musí obsahovat minimálně:

```text
snapshot_id
created_at
as_of
provider/source identity
calendar/version identity
universe identity
date range
timeframe
content hash
```

---

# 41. Snapshot content hash

Stejný dataset obsah musí dát stejný deterministický hash podle jasně canonicalized representation.

Pořadí DB rows nesmí měnit hash.

---

# 42. Snapshot immutability

Po vytvoření snapshotu se jeho meaning nesmí měnit po pozdějším ingestu provider corrections.

---

# 43. Snapshot lineage

Experiment musí umět dohledat:

```text
experiment
→ dataset snapshot
→ universe
→ ingestion batches / observations
→ provider
```

---

# 44. Point-in-time universe

Přidej explicitní point-in-time universe model.

Minimálně:

```text
UniverseDefinition
UniverseMembership
```

---

# 45. Universe membership

Membership musí mít minimálně:

```text
universe_id
instrument_id
valid_from
valid_to
known_at / observed_at pokud relevantní
```

---

# 46. No survivorship bias

Backtest data na historický den nesmí používat dnešní seznam členů indexu/universe.

Pro každý decision timestamp:

```text
eligible assets = members valid/known as of decision time
```

---

# 47. Universe examples

Podpor jednoduché user-defined universes:

```text
STATIC
POINT_IN_TIME_MEMBERSHIP
```

Není nutné automaticky ingestovat historické členství všech světových indexů.

Ale architecture musí správně umět PIT membership.

---

# 48. Static universe semantics

Static universe musí být explicitně označen jako static.

Dokumentace musí upozornit:

```text
static current universe may contain survivorship bias
```

Nesmí být vydáván za PIT-safe.

---

# 49. Universe membership tests

Povinný regression test:

```text
asset enters universe at T
```

Strategie jej nesmí použít před T.

Stejně:

```text
asset leaves at T
```

nesmí být nově nakupován po T.

---

# 50. Delisting

Delisted instrument nesmí z historie prostě zmizet.

Existing positions musí mít explicitní handling.

Nikdy nevytvářej falešný profitable exit na budoucí/neudané ceně.

---

# 51. Missing delisting price

Pokud není dostupná executable exit price:

fail closed / mark unresolved according to documented research policy.

Nevymýšlej cenu.

---

# 52. Multi-asset dataset

Research engine musí přijmout více instrumentů současně.

Ne pouze loop:

```text
single-symbol backtest × N
```

pokud pak není skutečné společné cash/portfolio accounting.

---

# 53. Portfolio-level engine

Multi-asset engine musí mít společné:

```text
cash
positions
equity
target weights
orders
fills
costs
```

---

# 54. Portfolio target interface

Strategie by měla produkovat:

```text
target weights / target positions
```

ne přímo broker orders.

Execution layer převádí target-vs-current na trades.

---

# 55. Strategy interface

Definuj stabilní strategy contract, například:

```python
generate_targets(context) -> TargetPortfolio
```

nebo nejlepší ekvivalent v aktuální architektuře.

---

# 56. Strategy context causality

Strategie smí vidět pouze data:

```text
timestamp <= decision_time
```

a pouze assets eligible v PIT universe.

---

# 57. Future-data mutation test

Povinný test:

1. spusť strategii do času T;
2. změň všechny bars po T;
3. znovu spusť prefix do T.

Výsledky do T musí být identické.

---

# 58. Cross-sectional causality

Cross-sectional strategie může porovnávat assets pouze v jednom stejném decision timestampu.

Nesmí používat future bar assetu B k rozhodnutí assetu A.

---

# 59. Missing bars

Nesynchronní/missing observations musí mít explicitní policy.

Zakázáno:

```text
unbounded forward fill executable prices
```

---

# 60. Stale asset

Asset bez čerstvého executable/reference price nesmí být nově obchodován.

Valuation policy pro existing position musí být explicitní.

---

# 61. Session alignment

Multi-asset portfolio musí rozhodovat podle relevantních společných sessions.

Pokud Phase 6 podporuje pouze assets na jednom calendaru, dokumentuj to.

Nemusíš nyní řešit globální multi-timezone portfolio.

---

# 62. Initial multi-asset scope

Preferuj:

```text
same asset class
same currency
same primary exchange/calendar family
long-only
daily bars
```

pro první production-quality multi-asset engine.

Rozšíření na globální FX/multi-currency je mimo scope.

---

# 63. Currency

Instrument currency musí být uložená.

Phase 6 může odmítnout portfolio obsahující více měn, pokud není FX conversion implementována.

Fail closed.

---

# 64. No hidden FX

Nikdy nesčítej USD a EUR nominal values bez explicitní FX conversion.

---

# 65. Rebalance timing

Decision:

```text
session close T
```

Execution nejdříve:

```text
next executable session open
```

Zachovej Phase 2/3 anti-lookahead invariant.

---

# 66. Same-bar execution forbidden

Signál vytvořený z close T nesmí být fillnut na open/close T, pokud tato cena nebyla po decision dostupná.

---

# 67. Order sequencing

Pokud portfolio musí prodávat i nakupovat při rebalance:

definuj deterministic sequencing.

Preferuj:

```text
risk-reducing sells
→ buys
```

nebo jinou explicitní policy.

---

# 68. Cash constraint

Backtest nesmí utratit více cash než je dostupné po fees/slippage.

---

# 69. Transaction costs

Multi-asset research musí používat stávající cost model.

Nesmí vzniknout druhý nekonzistentní commission/slippage engine.

---

# 70. Volume/capacity

Pokud existující broker/backtest používá volume capacity:

multi-asset engine ji musí respektovat konzistentně.

---

# 71. Fractional shares

Explicitně definuj, zda research dovoluje fractional quantities.

Pokud paper runtime pracuje s jiným modelem, strategy promotion musí tuto kompatibilitu validovat.

---

# 72. Portfolio accounting

Musí platit:

```text
equity =
cash
+
Σ(position_quantity × valuation_price)
```

---

# 73. Portfolio accounting tests

Přidej hand-calculated multi-asset scénář minimálně se dvěma instrumenty.

Ručně ověř:

* cash;
* position quantities;
* commissions;
* realized P/L;
* equity.

---

# 74. Corporate action accounting test

Přidej ručně spočítaný split/dividend scénář.

Ověř, že není double-counted.

---

# 75. Baseline strategy library

Implementuj alespoň tři transparentní, ne-ML baseline strategie.

Minimálně:

```text
TREND / MOVING-AVERAGE
CROSS-SECTIONAL MOMENTUM
MEAN REVERSION
```

Přesný naming přizpůsob existujícímu registry modelu.

---

# 76. Trend strategy

Implementuj jednoduchou interpretable trend strategy.

Například:

```text
fast moving average
vs
slow moving average
```

nebo time-series momentum.

Bez look-ahead.

---

# 77. Trend warm-up

Asset bez dostatečného lookbacku:

```text
not eligible for signal
```

Nesmí dostat implicitní zero-filled history.

---

# 78. Cross-sectional momentum

Na každý rebalance:

* spočítej historical momentum pouze z minulých dat;
* rank pouze PIT-eligible assets;
* vyber top-N / top fraction;
* normalizuj weights.

---

# 79. Cross-sectional tie-breaking

Tie musí být deterministic.

Například stable sort podle canonical instrument ID.

---

# 80. Cross-sectional missing data

Asset bez dostatečné history nesmí být rankingem zvýhodněn.

---

# 81. Mean reversion

Implementuj jednoduchou transparentní long-only mean-reversion baseline.

Například krátkodobý return/z-score vůči historical window.

Musí být causal a PIT-safe.

---

# 82. No strategy overfitting

Nevytvářej obří parameter grids.

Každá strategie má malý, explicitní bounded parameter space.

---

# 83. Strategy parameters

Každý parameter set musí být serializovatelný, validovatelný a hashovatelný.

---

# 84. Strategy version

Strategy registry musí identifikovat:

```text
strategy code/version
parameters
```

tak, aby starý experiment byl reprodukovatelný po budoucí změně defaults.

---

# 85. Strategy deterministic output

Stejný:

```text
strategy version
parameters
dataset snapshot
seed
```

musí dát stejné targety a výsledky.

---

# 86. Strategy seed

Deterministické strategie seed nepotřebují.

Pokud některá část používá randomness:

seed musí být persistentní součást experimentu.

---

# 87. Strategy prefix invariance

Každá nová strategie musí mít anti-lookahead prefix test.

---

# 88. Strategy registry integration

Nové strategie zaregistruj přes existující Strategy Registry.

Nevytvářej paralelní registry.

---

# 89. Experiment integration

Multi-asset runs musí používat existující Experiment Registry.

Persistuj minimálně:

```text
strategy version
parameters
dataset snapshot
universe
cost model
seed
code version
results
```

---

# 90. IS / Validation / OOS

Zachovej striktní chronological split.

```text
IS
→ validation
→ OOS
```

OOS nesmí být použit pro parameter selection.

---

# 91. Parameter selection

Selection může používat pouze:

```text
IS + validation
```

podle existující research policy.

---

# 92. OOS immutability

Jakmile je experiment vyhodnocen na OOS:

neprováděj automatickou optimalizaci na základě OOS výsledku.

---

# 93. Walk-forward

Pokud již engine podporuje walk-forward:

rozšiř jej na multi-asset.

Pokud ne:

implementuj jen pokud je výslovnou součástí stávajícího master planu.

Nevytvářej komplikovaný optimizer mimo scope.

---

# 94. Metrics

Multi-asset report musí minimálně obsahovat:

```text
total return
annualized return
volatility
Sharpe-like risk-adjusted return
max drawdown
turnover
exposure
trade count
costs
```

Pokud již existuje přesná metrics vrstva, rozšiř ji.

---

# 95. Exposure

Portfolio exposure musí být time-weighted, ne fill-count based.

Zachovej opravu z Phase 3 auditu.

---

# 96. Gross/net exposure

Pro long-only:

```text
gross >= abs(net)
```

a obvykle gross == net, pokud není cash/short exposure model.

---

# 97. Turnover

Turnover musí být definován a zdokumentován.

Nepoužívej nejasnou metriku.

---

# 98. Benchmark

Pokud máš benchmark data ve stejném datasetu:

podpor benchmark comparison.

Nevytvářej benchmark data z future universe.

---

# 99. Strategy comparison

Existující leaderboard/compare musí umět multi-asset experimenty.

---

# 100. Leaderboard safety

Nezaměň:

```text
best historical OOS
```

za záruku budoucí profitability.

Dokumentace musí zůstat věcná.

---

# 101. Promotion eligibility

Strategie nesmí být automaticky povýšena do paper runtime jen proto, že má nejlepší backtest.

---

# 102. Research-to-paper promotion

Přidej explicitní validation step / deployment manifest, pokud již podobný mechanismus není.

Promotion musí pinovat:

```text
strategy_id/version
parameters
dataset/research evidence reference
allowed universe
paper account
```

---

# 103. Paper runtime compatibility

Paper runtime musí dostat pouze strategii/config, které jsou kompatibilní s:

```text
long-only
supported symbols
supported currency
supported timeframe
```

---

# 104. No silent strategy substitution

Pokud strategy version neexistuje:

```text
fail closed
```

Nesmí se použít latest/default jiná verze.

---

# 105. Paper data path

Paper trading nesmí přímo používat research-only historical snapshot jako údajně aktuální data.

Rozliš:

```text
historical immutable research dataset
vs
latest validated paper market snapshot
```

---

# 106. Latest market snapshot

Pokud Phase 4 paper cycle potřebuje aktuální bars:

Phase 6 musí poskytnout explicitní validated current-data accessor.

---

# 107. Staleness

Current paper-data accessor musí používat:

```text
exchange calendar
expected latest completed session
```

ne pouze:

```text
now - bar.timestamp < 24h
```

---

# 108. Weekend staleness

Friday close může být v sobotu/neděli stále latest valid completed session.

Calendar-aware staleness musí tento případ chápat.

---

# 109. Holiday staleness

Stejně pro exchange holiday.

---

# 110. Automation integration

Rozšiř Phase 5 automation pouze tam, kde je to nutné pro data workflow.

Přidej bezpečně allowlisted non-economic job type například:

```text
REFRESH_MARKET_DATA
```

pokud to odpovídá architektuře.

---

# 111. Data-refresh job

Data refresh nesmí přímo provádět trading.

Pipeline:

```text
refresh data
→ validate
→ persist ingestion result
```

Odděleně:

```text
paper cycle
```

---

# 112. Data refresh failure

Pokud refresh selže:

paper cycle nesmí tiše obchodovat z nově nevalidního partial ingestion.

Musí buď použít poslední explicitně valid snapshot podle policy, nebo fail closed.

---

# 113. Data readiness dependency

Pokud automatizovaný paper cycle vyžaduje nový market-data refresh:

dependency musí být explicitní.

Nevytvářej implicitní race mezi refresh a trading jobem.

---

# 114. Refresh idempotency

Stejný refresh logical occurrence nesmí vytvořit duplicate observations/actions.

---

# 115. Refresh concurrency

Dva refresh workers pro stejný provider/instrument scope nesmí korumpovat ingestion.

Přidej PostgreSQL concurrency test, pokud refresh job implementuješ.

---

# 116. API — instruments

Přidej read API podle architektury, například:

```text
GET /market-data/instruments
GET /market-data/instruments/{id}
```

---

# 117. API — datasets

Přidej:

```text
GET /datasets
GET /datasets/{id}
```

pokud stávající registry endpoint nestačí.

---

# 118. API — universes

Přidej minimálně:

```text
GET /universes
GET /universes/{id}
```

Mutation endpointy pouze pokud jsou nutné pro Phase 6 workflow.

---

# 119. API — ingestions

Operator musí umět zjistit:

```text
latest ingestion
status
provider
range
rows
errors
```

---

# 120. API — provider refresh

Pokud vytvoříš manual refresh endpoint:

musí:

* používat allowlisted provider;
* neumožnit arbitrary URL fetch / SSRF;
* respektovat automation/config limits.

---

# 121. SSRF safety

Pokud provider používá configurable endpoint:

uživatel API nesmí dodat libovolný URL target.

Endpoint/base URL je trusted configuration, ne request parameter.

---

# 122. API pagination

Všechny collection endpoints musí mít bounded pagination.

---

# 123. Data query limits

API nesmí dovolit:

```text
give me every raw bar ever
```

bez limitu.

---

# 124. Config

Centralizuj:

```text
market_data_provider
provider credentials reference
sync overlap
provider timeout
provider retry limits
default exchange/calendar
data staleness policy
```

---

# 125. Safe config

Žádný secret nesmí být vracen health/API endpointem.

---

# 126. Database schema

Přidej podle nejlepšího návrhu persistentní tabulky minimálně pro koncepty:

```text
instruments
instrument_symbols
market_data_ingestions
market_bars / market_observations
corporate_actions
universe_definitions
universe_memberships
dataset snapshots / snapshot membership lineage
```

Použij existující registry tabulky tam, kde dávají smysl.

Neduplikuj Phase 3 dataset registry bez důvodu.

---

# 127. Schema evolution

Pokud existující dataset registry potřebuje rozšířit:

použij forward migration.

Neměň již aplikované historické migration files.

---

# 128. Phase 6 Alembic revision

Vytvoř samostatnou revizi navazující na aktuální head.

---

# 129. DB constraints

Přidej vhodné constraints například:

```text
finite positive OHLC
non-negative volume
valid membership interval
valid symbol interval
unique observation identity
unique action identity
immutable snapshot identity
```

PostgreSQL nemá jednoduchý portable finite Decimal CHECK přes všechny dialects; kombinuj application validation + DB constraints podle reálných typů.

---

# 130. Membership interval

Musí platit:

```text
valid_to is null
or
valid_to > valid_from
```

---

# 131. Overlapping membership

Stejný instrument/universe nesmí mít nelogické překrývající membership intervaly, pokud to schema umí bezpečně vynutit.

PostgreSQL exclusion constraint je přípustný, pokud je vhodný.

---

# 132. Symbol interval overlap

Stejný instrument nemá mít dva současně aktivní stejné symbol-history records bez explicitního důvodu.

---

# 133. Indexes

Přidej indexy pro nejčastější queries:

```text
instrument + session
provider + instrument + timeframe + session
universe + valid interval
snapshot
ingestion status/time
corporate action effective time
```

---

# 134. PostgreSQL-first

SQLite zůstává užitečný pro rychlé tests.

Ale:

```text
production persistence/concurrency proof = PostgreSQL
```

---

# 135. Data immutability

Historical observations použitá snapshotem se nesmí mazat nebo přepisovat tak, že snapshot přestane být reprodukovatelný.

---

# 136. Retention

Automatické mazání raw ingestion history není součást Phase 6.

---

# 137. Data validation service

Implementuj centrální validation layer.

Kontroluj minimálně:

```text
OHLC invariants
finite values
duplicates
calendar alignment
monotonic timestamps
unexpected gaps
currency/asset compatibility
```

---

# 138. Gap detection

Rozliš:

```text
expected non-session gap
vs
missing expected session
```

pomocí exchange calendaru.

---

# 139. Gap severity

Missing expected session musí být viditelné.

Nesmí se automaticky forward-fillnout bez explicitní policy.

---

# 140. Duplicate bars

Stejný provider/instrument/session s identickým payloadem:

idempotent.

S rozdílným payloadem:

revision/correction, ne silent overwrite.

---

# 141. Data quality report

Ingestion nebo dataset build musí vytvořit stručný data-quality summary:

```text
rows
duplicates
missing sessions
invalid bars
revisions
coverage
```

---

# 142. Dataset build fail-closed

Dataset snapshot s critical data-quality error nesmí být označen jako VALID.

---

# 143. Dataset status

Použij například:

```text
BUILDING
VALID
INVALID
```

nebo existing registry status.

---

# 144. Experiment refuses invalid dataset

Experiment engine musí odmítnout invalid snapshot.

---

# 145. Reproducibility manifest

Každý experiment musí být možné popsat manifestem obsahujícím:

```text
dataset snapshot hash
universe
calendar
strategy version
parameters
cost model
seed
code/git SHA
```

---

# 146. Git/code version

Pokud repository již persistuje git SHA, zachovej.

Pokud ne, doplň jej vhodným způsobem do research run metadata.

---

# 147. Provider data licensing

Dokumentace musí říkat, že uživatel je odpovědný za licenční podmínky zvoleného market-data providera.

Nekopíruj proprietary dataset do repository fixtures ve velkém rozsahu.

---

# 148. Test fixtures

Použij malé syntetické nebo minimální legálně bezpečné fixture payloady.

---

# 149. Strategy test datasets

Většina strategy tests musí používat syntetická deterministic data.

Ne external internet.

---

# 150. Unit tests — provider

Minimálně:

* parsing;
* symbol mapping;
* timestamp normalization;
* invalid bars;
* revisions;
* empty response;
* transient error.

---

# 151. Unit tests — calendar

Minimálně:

* normal session;
* weekend;
* holiday;
* early close;
* next session;
* timestamp mapping.

---

# 152. Unit tests — corporate actions

Minimálně:

* split;
* dividend;
* future action causality;
* duplicate action;
* correction.

---

# 153. Unit tests — universe

Minimálně:

* enter;
* leave;
* re-enter;
* PIT query;
* static bias label.

---

# 154. Unit tests — snapshot

Minimálně:

* deterministic hash;
* immutable meaning;
* correction after snapshot;
* invalid dataset rejection.

---

# 155. Unit tests — strategies

Pro každou novou strategii:

* insufficient lookback;
* deterministic targets;
* future mutation;
* missing asset data;
* universe eligibility;
* parameter validation.

---

# 156. Cross-sectional momentum tests

Povinně:

```text
top-N selection
ties
new universe entrant
missing lookback
future-data mutation
```

---

# 157. Multi-asset accounting test

Povinný ručně vypočítaný scénář minimálně 2–3 assets.

---

# 158. Corporate action portfolio test

Povinný split/dividend accounting test.

---

# 159. Survivorship-bias regression test

Vytvoř synthetic universe:

```text
A exists whole period
B leaves halfway
C enters halfway
```

Strategie musí používat membership správně v čase.

---

# 160. Look-ahead master regression

Přidej test, který změní:

* future prices;
* future universe membership;
* future corporate action;

a ověří, že historical decisions zůstávají stejné.

---

# 161. OOS leakage regression

Ověř, že změna OOS dat nezmění zvolený parameter set.

---

# 162. Dataset correction reproducibility

1. vytvoř snapshot S1;
2. proveď provider correction;
3. vytvoř snapshot S2;
4. experiment nad S1 musí reprodukovat starý výsledek;
5. S2 může mít jiný výsledek.

---

# 163. PostgreSQL tests

Přidej PostgreSQL integration tests pro:

```text
ingestion idempotency
observation revisions
snapshot immutability
universe queries
constraints
```

---

# 164. PostgreSQL concurrency

Pokud dva ingestion workers zapisují stejná observations současně:

nesmí vzniknout duplicate canonical observation/revision.

Přidej skutečný multi-session test.

---

# 165. Dataset concurrent build

Dva procesy budující stejný logical snapshot nesmí vytvořit dva různé authoritative snapshots pro stejnou identity.

Použij unique identity/idempotency.

---

# 166. Automation data-refresh concurrency

Pokud Phase 5 `REFRESH_MARKET_DATA` job přidáš:

testuj concurrent refresh stejného scope na PostgreSQL.

---

# 167. Alembic tests

Ověř:

```text
empty → head
Phase5 head → Phase6
Phase6 → Phase5
Phase5 → Phase6
```

pokud downgrade podporujeme.

---

# 168. Existing Phase 3–5 regression

Všechny starší tests musí zůstat zelené.

Phase 6 nesmí rozbít paper trading ani automation.

---

# 169. CI

Rozšiř `.github/workflows/ci.yml`.

Quality zůstává:

```text
uv --version
uv lock --check
uv sync --locked --all-groups
ruff
mypy
```

---

# 170. Research CI

Spusť Phase 6 unit/research tests v samostatném nebo existing research jobu.

---

# 171. PostgreSQL CI

Integration job musí spouštět minimálně:

```text
Phase 3
Phase 4
Phase 5
Phase 6 PostgreSQL tests
```

---

# 172. No internet CI dependency

PR CI nesmí failovat pouze proto, že externí provider není dostupný.

Provider adapter testuj fixtures.

---

# 173. Dependency policy

Pokud potřebuješ novou dependency například pro exchange calendars nebo HTTP provider:

dodrž přesně `AGENTS.md`.

Použij:

```bash
uv lock
uv lock --check
uv sync --locked --all-groups
```

Lockfile nikdy ručně neupravuj.

Pokud registry není dostupný:

```text
BLOCKED BY ENVIRONMENT
```

a necommituj neověřený lockfile.

---

# 174. Documentation

Aktualizuj:

* `README.md`
* `docs/architecture.md`
* `docs/implementation-plan.md`
* `docs/database.md`
* `docs/operations.md`
* `docs/automation.md`
* `docs/live-trading-safety.md`

---

# 175. New documentation

Přidej minimálně:

```text
docs/market-data.md
docs/strategy-research.md
```

Případně:

```text
docs/point-in-time-universe.md
```

pokud to zlepší přehlednost.

---

# 176. market-data.md

Dokumentuj:

* provider abstraction;
* configured provider;
* timestamps;
* raw/adjusted semantics;
* ingestion;
* revisions;
* calendar;
* corporate actions;
* snapshots;
* data-quality validation.

---

# 177. strategy-research.md

Dokumentuj:

* strategy interface;
* baseline strategies;
* parameter semantics;
* universe;
* multi-asset accounting;
* anti-lookahead;
* IS/validation/OOS;
* promotion boundary.

---

# 178. Point-in-time documentation

Jasně vysvětli rozdíl:

```text
static current universe
vs
point-in-time universe
```

a survivorship bias.

---

# 179. Operations

Dokumentuj:

* manual data refresh;
* automated refresh, pokud implementován;
* failed ingestion;
* invalid dataset;
* rebuilding snapshot;
* provider outage;
* stale current data.

---

# 180. Safety documentation

Aktualizuj live trading safety:

Phase 6 přidává market data a strategie, nikoli live broker.

---

# 181. Architecture modularity

Nevkládej vše do jednoho souboru.

Preferuj oddělené moduly například:

```text
market_data.py
calendar.py
universe.py
strategies.py
```

podle nejlepší architektury.

---

# 182. Dependency direction

Preferovaně:

```text
Providers
→ ingestion/normalization
→ dataset/universe
→ research
→ strategy targets
```

Execution/risk nesmí záviset na provider implementation details.

---

# 183. No giant service

Rozděl:

```text
provider adapters
repository
ingestion service
dataset builder
universe service
strategy services
```

podle potřeby.

---

# 184. No arbitrary provider execution

Provider type musí být allowlisted registry.

Žádný dynamický:

```text
import path from API payload
```

---

# 185. No arbitrary URL ingestion

API/request nesmí umožnit serveru stáhnout libovolnou user-supplied URL.

---

# 186. Error taxonomy

Použij typované exceptions minimálně konceptuálně pro:

```text
ProviderUnavailable
InvalidProviderResponse
InvalidMarketData
DatasetInvalid
SnapshotNotFound
UniverseInvalid
UnsupportedInstrument
```

podle potřeby.

---

# 187. Deterministic sorting

Všechny:

* instrument lists;
* ranking;
* snapshot serialization;
* result outputs;

musí mít deterministic ordering.

---

# 188. Numeric precision

Nepoužívej float tam, kde může způsobit accounting drift, pokud stávající engine používá Decimal.

Indikátory/statistické výpočty mohou používat float podle existující conventions, ale boundary s accountingem musí být explicitní.

---

# 189. NaN/Inf

Strategy indicator NaN během warm-up nesmí projít jako executable target.

Non-finite target weight:

```text
reject
```

---

# 190. Target weight validation

Musí platit minimálně:

```text
finite
>= 0
sum(weights) <= allowed gross exposure
```

Long-only.

---

# 191. Weight normalization

Pokud strategy vybere N assets:

normalizace musí být deterministic a testovaná.

---

# 192. Zero eligible assets

Legitimní výsledek:

```text
100% cash
```

ne chyba.

---

# 193. Strategy target turnover

Strategie nesmí kvůli nedeterministickému ordering každý den zbytečně churnovat stejné weights.

---

# 194. Rebalance frequency

Každá strategie má explicitní rebalance frequency.

Nepoužívej implicitní „every bar“ bez dokumentace.

---

# 195. Calendar-aware rebalance

Weekly/monthly rebalance musí mapovat na skutečný poslední/první exchange session, ne kalendářní den bez trhu.

---

# 196. Monthly boundary test

Month-end víkend/holiday musí být správně vyřešen.

---

# 197. PIT universe rebalance interaction

Asset vstupující do universe uprostřed období může být eligible až při první povolené rebalance po effective membership.

---

# 198. Benchmark causality

Benchmark series musí používat stejné calendar/session semantics.

---

# 199. Data coverage report

Experiment output musí uvést alespoň:

```text
requested universe size
assets actually used
assets excluded for insufficient history
missing-session count
```

---

# 200. No hidden data dropping

Pokud asset vypadne kvůli data-quality problému:

musí být dohledatelný reason.

---

# 201. Experiment failure policy

Pokud příliš velká část universe nemá data:

experiment musí podle configurable threshold failnout nebo být označen invalid.

Nesmí tiše backtestovat 3 assets z požadovaných 100 a tvrdit plnou universe performance.

---

# 202. Minimum coverage

Definuj explicitní minimum coverage policy.

Použij bezpečný default nebo existující project config.

---

# 203. Research output

Report musí obsahovat data-quality/coverage warnings.

---

# 204. Strategy comparison compatibility

Leaderboard nesmí nefér porovnávat experimenty s úplně jinou universe/data coverage bez metadata.

Alespoň metadata musí být viditelná.

---

# 205. Dataset API lineage

Dataset detail endpoint by měl být schopný vrátit:

```text
snapshot hash
provider
universe
date range
coverage
status
```

bez posílání milionů bars.

---

# 206. Security

Provider credentials:

```text
environment / secret manager only
```

Nikdy:

```text
DB plaintext API response
logs
git
```

---

# 207. Provider errors

Neloguj response headers/payloady obsahující credential tokeny.

---

# 208. User-facing errors

API nesmí vracet stack trace/provider secret.

---

# 209. Full implementation review

Po implementaci proveď adversarial self-review zaměřený na:

```text
look-ahead
survivorship bias
future corporate actions
dataset mutation
symbol changes
calendar mistakes
missing data
cross-sectional ranking
provider corrections
multi-asset cash accounting
```

Nalezené chyby oprav.

---

# 210. Definition of Done — Market Data

```text
[ ] provider abstraction
[ ] at least one external provider adapter
[ ] fixture contract tests
[ ] incremental sync
[ ] ingestion history
[ ] validation
[ ] revisions/corrections
[ ] no CI internet dependency
```

---

# 211. Definition of Done — Instruments/Calendar

```text
[ ] canonical instrument identity
[ ] symbol history
[ ] exchange/calendar
[ ] UTC timestamps
[ ] weekends/holidays
[ ] early closes
[ ] next-session execution
```

---

# 212. Definition of Done — Corporate Actions

```text
[ ] split model
[ ] dividend model/policy
[ ] symbol change
[ ] delisting policy
[ ] no double counting
[ ] future-action causality tested
```

---

# 213. Definition of Done — Point-in-Time

```text
[ ] universe definitions
[ ] membership intervals
[ ] PIT query
[ ] static universe marked as bias-prone
[ ] entrant/leaver tests
[ ] survivorship-bias regression
```

---

# 214. Definition of Done — Snapshots

```text
[ ] immutable snapshot
[ ] deterministic hash
[ ] ingestion lineage
[ ] correction does not mutate old snapshot
[ ] invalid dataset cannot run experiment
```

---

# 215. Definition of Done — Multi-Asset Research

```text
[ ] shared portfolio cash
[ ] multi-position accounting
[ ] deterministic targets
[ ] missing/stale data policy
[ ] no same-bar look-ahead
[ ] costs
[ ] exposure
[ ] turnover
[ ] hand-calculated accounting test
```

---

# 216. Definition of Done — Strategies

```text
[ ] trend baseline
[ ] cross-sectional momentum baseline
[ ] mean-reversion baseline
[ ] strategy registry
[ ] bounded parameters
[ ] prefix invariance
[ ] PIT universe awareness
[ ] deterministic tie-breaking
```

---

# 217. Definition of Done — Evaluation

```text
[ ] IS/validation/OOS chronological
[ ] OOS not used for selection
[ ] multi-asset metrics
[ ] coverage report
[ ] registry/leaderboard integration
[ ] reproducibility manifest
```

---

# 218. Definition of Done — Paper Integration

```text
[ ] research strategy cannot bypass RiskEngine
[ ] current data validated
[ ] calendar-aware staleness
[ ] paper only
[ ] no live path
[ ] unsupported currency/universe fails closed
```

---

# 219. Definition of Done — Automation

Pokud data-refresh job implementován:

```text
[ ] allowlisted job type
[ ] refresh idempotent
[ ] refresh does not trade
[ ] refresh concurrency safe
[ ] failure does not expose partial invalid dataset
```

---

# 220. Definition of Done — Persistence

```text
[ ] Phase6 Alembic revision
[ ] constraints
[ ] indexes
[ ] PostgreSQL integration
[ ] fresh upgrade
[ ] Phase5→Phase6 upgrade
[ ] downgrade/re-upgrade if supported
```

---

# 221. Definition of Done — Tests

```text
[ ] provider contract
[ ] calendar
[ ] early close
[ ] corporate actions
[ ] PIT universe
[ ] snapshot immutability
[ ] provider correction
[ ] survivorship bias
[ ] future-data mutation
[ ] cross-sectional causality
[ ] OOS leakage
[ ] multi-asset accounting
[ ] PostgreSQL concurrency
[ ] full regression suite
```

---

# 222. Definition of Done — Documentation

```text
[ ] market-data.md
[ ] strategy-research.md
[ ] architecture updated
[ ] database updated
[ ] operations updated
[ ] implementation plan updated
[ ] live safety updated
```

---

# 223. Full verification

Na konci spusť:

```bash
cd backend

uv --version
uv lock --check
uv sync --locked --all-groups

uv run ruff format --check .
uv run ruff check .
uv run mypy src/quantlab
uv run pytest -q
```

Potom:

```bash
cd ..
git diff --check
git status
```

---

# 224. PostgreSQL verification

Pokud prostředí umožňuje:

```bash
docker compose up -d postgres
```

Spusť všechny PostgreSQL tests:

```text
Phase 3
Phase 4
Phase 5
Phase 6
```

s aktuálním project mechanismem, například:

```text
RUN_POSTGRES_TESTS=1
```

---

# 225. External provider smoke

Pokud credentials/network dostupné:

spusť malý optional smoke test.

Výsledek odděl od required deterministic CI.

---

# 226. Environment blockers

Pokud Codex environment nemá:

* Docker;
* správný uv;
* network;
* provider credentials;

implementaci a fixture tests přesto dokonči.

Označ pouze konkrétní runtime verification:

```text
BLOCKED BY ENVIRONMENT
```

---

# 227. No test weakening

Zakázáno:

* mazat relevantní test;
* skipovat skutečný bug;
* oslabit assertion;
* používat dnešní universe pro historický PIT test;
* mocknout pryč klíčovou research logic.

---

# 228. No fake completeness

Phase 6 není COMPLETE jen proto, že:

```text
provider adapter exists
```

Musí být hotová celá data→snapshot→universe→strategy→multi-asset research pipeline.

---

# 229. Internal audit

Po dokončení proveď druhý pass:

```text
future bars
future membership
future corporate actions
survivorship bias
provider corrections
calendar/holiday errors
snapshot mutation
multi-asset accounting
```

Každý nalezený problém oprav a regresně pokryj.

---

# 230. Git

Na konci:

```bash
git status
git diff --check
git log --oneline -10
```

Working tree musí být čistý po commitu.

---

# 231. Commit

Preferovaný commit:

```text
Dokončení Market Data a Strategy Expansion Phase 6
```

Pokud vzniknou oddělené významné opravy, použij několik logických commitů.

---

# 232. Phase 6 verdict

Použij pouze:

```text
COMPLETE
COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING
INCOMPLETE
```

## COMPLETE

Pouze pokud:

* všechny Phase 6 functional requirements jsou hotové;
* full locked suite PASS;
* PostgreSQL Phase6 integration/concurrency PASS.

## COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING

Pouze pokud:

* functional implementation a požadované tests jsou hotové;
* zbývá pouze jejich skutečné spuštění kvůli Docker/network/provider environmentu.

## INCOMPLETE

Pokud chybí funkční nebo povinný testovací scope.

---

# 233. Final report

Finální odpověď musí obsahovat:

## Starting state

* starting HEAD
* branch
* working tree

## Phase 6 verdict

```text
COMPLETE
COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING
INCOMPLETE
```

## Implemented

Rozděleně:

```text
6.1 Market Data Provider Layer
6.2 Instruments & Sessions
6.3 Corporate Actions
6.4 Point-in-Time Universe
6.5 Dataset Snapshots
6.6 Multi-Asset Research
6.7 Strategies
6.8 Evaluation/Registry
6.9 Phase4/5 Integration
6.10 CI/API/Documentation
```

## Market-data architecture

* provider
* ingestion
* observations
* validation
* revisions

## Calendar semantics

* timezone
* session close
* holidays
* early close
* next-session execution

## Corporate actions

* splits
* dividends
* symbol changes
* delisting
* causality

## Point-in-time universe

* membership
* survivorship bias prevention

## Dataset reproducibility

* snapshot
* hash
* lineage
* corrections

## Multi-asset engine

* cash
* positions
* execution
* costs
* missing-data policy

## Strategies

Pro každou:

* logic
* parameters
* rebalance
* anti-lookahead tests

## Research correctness

Explicitně popiš:

```text
no look-ahead
no future universe membership
no OOS parameter selection
no survivorship-bias claim for static universes
```

## Paper integration

Potvrď:

```text
paper only
no live broker
no live order path
```

## Automation integration

Pokud přidána.

## Database

* nové/změněné tabulky
* constraints
* indexes
* migration

## API

Seznam relevantních endpointů.

## Tests

Rozděl:

```text
PASS
SKIPPED
BLOCKED BY ENVIRONMENT
FAILED
```

Přesné příkazy a výsledky.

## PostgreSQL evidence

* ingestion concurrency
* snapshot integrity
* universe persistence
* migrations

## Provider smoke

Pokud proveden.

## Documentation

Seznam.

## Remaining risks

Pouze skutečné limity.

## Phase 6 Audit readiness

Použij:

```text
READY FOR PHASE 6 AUDIT GATE
```

nebo:

```text
NOT READY FOR PHASE 6 AUDIT GATE
```

## Git

* commit SHA(s)
* changed files
* final status

---

# 234. Phase 6 Audit readiness rule

Použij:

```text
READY FOR PHASE 6 AUDIT GATE
```

pouze pokud:

* není otevřený Critical/High correctness problém;
* všechny Phase 6 required test scenarios jsou implementované;
* PostgreSQL CI wiring zahrnuje Phase 6.

---

# 235. Start

Začni nyní.

Postup:

```text
1. inspect current main
2. read authoritative documentation
3. baseline verification
4. map existing data/research abstractions
5. design canonical instruments/provider model
6. implement calendar/session layer
7. implement ingestion/revisions
8. implement corporate actions
9. implement point-in-time universe
10. implement immutable snapshots
11. extend research engine to true multi-asset
12. implement baseline strategies
13. integrate experiment/strategy registries
14. integrate validated paper-data path
15. integrate Phase5 automation if required
16. add API
17. add Alembic migration
18. add unit/regression tests
19. add PostgreSQL integration/concurrency tests
20. update CI
21. update documentation
22. run full locked verification
23. run PostgreSQL verification
24. perform adversarial anti-lookahead/survivorship review
25. fix findings
26. commit
27. final report
```

Nevracej pouze implementační plán.

**Implementuj celou Phase 6 end-to-end.**

---

## Staging remediation: canonical PAPER risk allowlist

Production-like acceptance odhalila, že standardní operator deployment přebíral legacy
`ProductionRiskConfig` allowlist `SPY`, zatímco celý Phase 6 runtime používá jako ekonomickou
identitu canonical `instrument_id`. Deployment nyní při vytvoření deterministicky odvodí
seřazený allowlist ze všech PIT memberships známých nejpozději v immutable snapshot cutoffu.
Prázdná identita, whitespace nebo identita delší než Phase 4 persistentní limit 40 znaků
creation fail-closed zastaví; ticker ani wildcard nejsou náhradou canonical identity.

Explicitní risk konfigurace může nadále měnit numerické a provozní limity, její procesní
allowlist je však pro Phase 6 deployment nahrazen canonical množinou ze schválené universe
evidence. Výsledný allowlist zůstává součástí immutable runtime manifest hash i deployment
identity. Approval znovu ověří pokrytí stejné snapshot evidence a PAPER runtime před načtením
execution dat, tvorbou targetů nebo voláním `TradingCycleService` ověří všechny aktuálně
eligible a persisted-intent identity. Drift končí deterministickým
`DatasetInvalid("RISK_ALLOWLIST_COVERAGE_MISMATCH")`; jednotlivé pozdější risk rejectiony
nejsou náhradou této deployment-level brány. Phase 4 risk engine svůj obecný allowlist nadále
vynucuje beze změny a execution path zůstává výhradně paper-only.
