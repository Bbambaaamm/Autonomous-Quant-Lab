# Autonomous Quant Lab — Codex Master Implementation Prompt

> This file contains the original implementation specification for the Autonomous Quant Lab project.
> Codex should treat this document as the authoritative project specification unless explicitly overridden by newer requirements.

**Status:** Active  
**Project:** Autonomous Quant Lab  
**Purpose:** Full repository implementation specification

# MASTER IMPLEMENTATION PROMPT — AUTONOMOUS QUANT LAB

Jsi principal software architect, senior quantitative developer, senior Python/TypeScript engineer, DevOps engineer, QA engineer a security reviewer v jednom.

Tvým úkolem je **navrhnout, vytvořit, otestovat, zdokumentovat a zprovoznit kompletní aplikaci s pracovním názvem `Autonomous Quant Lab`**.

Nechci pouze proof-of-concept, ukázkové skripty, pseudokód ani architektonický návrh.

Chci skutečný, spustitelný, testovaný a udržovatelný Git projekt.

---

# 1. HLAVNÍ CÍL PROJEKTU

Vytvoř autonomní quantitative trading/research platformu, která dokáže:

1. získávat historická a aktuální tržní data,
2. kontrolovat jejich kvalitu,
3. vytvářet obchodní signály,
4. automaticky testovat obchodní strategie,
5. provádět parametrické experimenty,
6. zabránit look-ahead bias a data leakage,
7. provádět out-of-sample validaci,
8. provádět walk-forward analýzu,
9. modelovat spread, slippage a poplatky,
10. provádět Monte Carlo analýzu výsledků,
11. porovnávat strategie,
12. vytvářet portfolio,
13. řídit riziko,
14. automaticky rozhodovat, které strategie jsou způsobilé pro paper trading,
15. provozovat paper trading,
16. sledovat výkonnost paper tradingu,
17. porovnávat backtest vs. skutečný paper výkon,
18. zobrazovat vše v přehledném webovém dashboardu,
19. vést auditní historii všech rozhodnutí,
20. být připraven na budoucí live trading.

Projekt nesmí záviset na zákaznících, uživatelských objednávkách ani SaaS předplatitelích.

Jde o **single-operator quantitative research/trading system**.

---

# 2. ZÁSADNÍ PRINCIP

Neimplementuj systém typu:

```text
pošli zprávy GPT
→ zeptej se AI BUY/SELL
→ proveď obchod
```

To není cílem projektu.

Obchodní rozhodování musí být:

* deterministické,
* reprodukovatelné,
* auditovatelné,
* statisticky testovatelné,
* založené na definovaných strategiích,
* oddělené od LLM.

AI/Codex slouží k:

* vývoji systému,
* výzkumu,
* automatizaci experimentů,
* analýze výsledků,
* údržbě kódu.

Runtime trading engine nesmí být závislý na náhodném textovém výstupu LLM.

---

# 3. BEZPEČNOST LIVE TRADINGU

Nejdůležitější pravidlo projektu:

```text
DEFAULT = PAPER TRADING
```

Live obchodování musí být ve výchozím stavu nemožné.

Nastav:

```env
TRADING_MODE=paper
LIVE_TRADING_ENABLED=false
```

Architekturu připrav na budoucí live trading, ale:

* všechny automatické testy musí používat simulovaný broker,
* development prostředí musí používat paper broker,
* bez explicitního povolení nesmí být možné poslat skutečný příkaz brokerovi,
* samotná existence API klíče nesmí aktivovat live trading.

Pro live režim požaduj minimálně současné splnění:

```text
TRADING_MODE=live
LIVE_TRADING_ENABLED=true
LIVE_TRADING_CONFIRMATION=<explicit secret confirmation>
```

Dále implementuj:

* instrument allowlist,
* maximální velikost jednoho live příkazu,
* maximální denní zobchodovaný notional,
* maximální počet příkazů,
* max portfolio exposure,
* emergency kill switch.

Pokud některá podmínka není splněna:

```text
FAIL CLOSED
```

Žádný příkaz se nesmí odeslat.

---

# 4. NEJDŘÍVE PROVEĎ ANALÝZU REPOZITÁŘE

Pokud je repository prázdné, vytvoř projekt od nuly.

Pokud už něco obsahuje:

1. prozkoumej celý repository,
2. zjisti aktuální architekturu,
3. zachovej použitelné části,
4. nemaž existující funkčnost bez důvodu,
5. teprve potom implementuj změny.

---

# 5. VYTVOŘ AGENTS.md

Jako jeden z prvních kroků vytvoř v rootu:

```text
AGENTS.md
```

Musí obsahovat trvalé instrukce pro další práci Codex agentů.

Minimálně:

```text
# Autonomous Quant Lab Agent Instructions

## Project principles
- Correctness over speed.
- Never introduce look-ahead bias.
- Never silently drop failed market-data rows.
- Never send live orders in tests.
- Paper trading is the default.
- Financial calculations must be deterministic.
- Every trading decision must be auditable.
- All timestamps internally use UTC.
- Exchange-local timezone may be used only at presentation/calendar boundaries.
- Monetary calculations must avoid inappropriate binary floating-point assumptions where exact decimal arithmetic matters.
- Never commit secrets.
- Never weaken risk controls to make tests pass.

## Required workflow
Before finishing any task:
1. inspect affected architecture,
2. implement the change,
3. run formatting,
4. run static analysis,
5. run unit tests,
6. run relevant integration tests,
7. report what changed.

## Trading-specific requirements
Every strategy must:
- use only information available at decision time,
- expose its required lookback,
- define rebalance frequency,
- define supported asset class,
- return target positions/weights through common interfaces.

Every execution path must pass through RiskEngine.

Strategies must never call Broker directly.

Broker orders must only originate from ExecutionEngine after RiskEngine approval.

## Testing
Regression tests are mandatory for every bug fix.

Backtest tests must explicitly verify no future information is used.

Live broker integration must never run in CI.
```

Rozšiř tento dokument podle skutečné architektury projektu.

---

# 6. VYTVOŘ IMPLEMENTAČNÍ PLÁN

Vytvoř:

```text
docs/implementation-plan.md
```

Plán musí být živý dokument.

Rozděl práci minimálně na:

```text
Phase 0 – Repository bootstrap
Phase 1 – Domain model
Phase 2 – Market data
Phase 3 – Strategy framework
Phase 4 – Backtesting engine
Phase 5 – Validation framework
Phase 6 – Research automation
Phase 7 – Portfolio & risk
Phase 8 – Paper broker
Phase 9 – Automated trading cycle
Phase 10 – REST API
Phase 11 – Web dashboard
Phase 12 – Observability
Phase 13 – Security hardening
Phase 14 – CI/CD
Phase 15 – Documentation
Phase 16 – End-to-end verification
```

U každé fáze udržuj:

```text
status
scope
acceptance criteria
tests
remaining work
```

Po dokončení kroku plán aktualizuj.

---

# 7. TECHNOLOGICKÝ STACK

Použij moderní, stabilní, aktivně podporované technologie.

Před implementací ověř jejich aktuální stabilní verze podle oficiální dokumentace.

Preferovaný stack:

## Backend

```text
Python
FastAPI
Pydantic
SQLAlchemy
Alembic
PostgreSQL
Redis
background worker
```

Použij moderní Python packaging.

Preferuj:

```text
uv
pyproject.toml
```

## Quant/data

Použij podle potřeby:

```text
numpy
pandas
scipy
pyarrow
```

Další knihovny můžeš přidat, pokud dávají technický smysl.

Nepřidávej obrovské frameworky pouze proto, abys ušetřil několik řádků kódu.

## Frontend

```text
TypeScript
React
Next.js
Tailwind CSS
```

Grafy pomocí vhodné stabilní React knihovny.

## Infrastructure

```text
Docker
Docker Compose
GitHub Actions
PostgreSQL
Redis
```

---

# 8. STRUKTURA MONOREPA

Navrhni repository přibližně:

```text
autonomous-quant-lab/
│
├── AGENTS.md
├── README.md
├── LICENSE
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Makefile
│
├── docs/
│   ├── architecture.md
│   ├── implementation-plan.md
│   ├── domain-model.md
│   ├── backtesting.md
│   ├── strategies.md
│   ├── risk-management.md
│   ├── paper-trading.md
│   ├── live-trading-safety.md
│   ├── operations.md
│   └── troubleshooting.md
│
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   ├── src/
│   │   └── quantlab/
│   │       ├── api/
│   │       ├── config/
│   │       ├── domain/
│   │       ├── data/
│   │       ├── universe/
│   │       ├── features/
│   │       ├── strategies/
│   │       ├── backtest/
│   │       ├── validation/
│   │       ├── experiments/
│   │       ├── portfolio/
│   │       ├── risk/
│   │       ├── execution/
│   │       ├── brokers/
│   │       ├── trading/
│   │       ├── reports/
│   │       ├── persistence/
│   │       ├── jobs/
│   │       └── observability/
│   │
│   └── tests/
│       ├── unit/
│       ├── integration/
│       ├── regression/
│       └── fixtures/
│
├── frontend/
│   ├── package.json
│   ├── src/
│   └── tests/
│
├── scripts/
│
├── data/
│   ├── cache/
│   └── exports/
│
└── .github/
    └── workflows/
```

Pokud během implementace najdeš lepší strukturu, můžeš ji upravit.

Zdokumentuj důvod.

---

# 9. ARCHITEKTURA

Dodrž oddělení:

```text
Market Data
     ↓
Data Validation
     ↓
Feature Generation
     ↓
Strategy
     ↓
Target Portfolio
     ↓
Portfolio Construction
     ↓
Risk Engine
     ↓
Execution Engine
     ↓
Broker
```

Žádná strategie nesmí přistupovat přímo k brokerovi.

Tok musí být vždy:

```text
Strategy
→ Portfolio
→ Risk
→ Execution
→ Broker
```

Risk Engine nesmí být možné obejít.

---

# 10. DOMAIN MODEL

Definuj jednoznačné typované modely například:

```text
Instrument
Bar
Quote
MarketCalendar
Signal
TargetPosition
TargetPortfolio
Position
Portfolio
OrderIntent
RiskDecision
Order
Fill
Trade
StrategyDefinition
StrategyRun
BacktestRun
Experiment
ExperimentRun
ValidationResult
PaperAccount
TradingCycle
AuditEvent
```

Používej jasně definované enumy:

```text
BUY
SELL

MARKET
LIMIT

PENDING
APPROVED
REJECTED
SUBMITTED
PARTIALLY_FILLED
FILLED
CANCELLED
FAILED
```

---

# 11. ČAS

Interně používej výhradně:

```text
timezone-aware UTC datetime
```

Nikdy:

```text
naive datetime
```

Exchange timezone řeš pouze při:

* market calendar,
* session boundaries,
* UI prezentaci.

Přidej testy na timezone chování.

---

# 12. MARKET DATA LAYER

Vytvoř obecné rozhraní:

```python
class MarketDataProvider(Protocol):
    ...
```

Musí podporovat minimálně:

```text
historical OHLCV
latest quote
instrument metadata
trading calendar
```

Projekt nesmí být pevně svázaný s jedním poskytovatelem.

Implementuj minimálně:

```text
MockMarketDataProvider
CSV/ParquetMarketDataProvider
```

a jeden reálně použitelný provider vhodný pro research.

Pokud provider používá neoficiální nebo nestabilní API:

* izoluj ho za adapterem,
* zdokumentuj omezení,
* nikdy jeho chování nerozlévej do domény aplikace.

---

# 13. DATOVÝ MODEL BARU

Minimálně:

```text
symbol
timestamp
open
high
low
close
volume
adjusted_close
source
```

Definuj přesně:

* zda timestamp znamená open nebo close baru,
* kdy jsou data dostupná strategii.

To je kritické pro odstranění look-ahead bias.

---

# 14. DATA QUALITY ENGINE

Před použitím dat proveď kontrolu:

```text
duplicate timestamps
missing timestamps
non-monotonic timestamps
OHLC inconsistencies
negative prices
zero/negative invalid volume
NaN
infinite values
suspicious price jumps
stale data
```

OHLC invariant:

```text
low <= open <= high
low <= close <= high
```

Pokud data neprojdou kritickou validací:

```text
strategie se nesmí spustit
```

Nikdy neopravuj kritická data potichu.

Zaznamenej problém do:

```text
data_quality_events
```

---

# 15. CORPORATE ACTIONS

Explicitně řeš:

```text
stock splits
dividends
adjusted prices
```

Research/backtesting nesmí míchat raw a adjusted ceny nekonzistentním způsobem.

Do dokumentace napiš přesný model.

---

# 16. UNIVERSE

Vytvoř `UniverseProvider`.

První předdefinovaný universe například:

```text
SPY
QQQ
IWM
DIA
TLT
GLD
```

Dále umožni:

```text
custom symbol list
CSV universe
configured equity universe
```

Později musí být možné přidat širší akciové universe bez změny strategy interface.

---

# 17. SURVIVORSHIP BIAS

Dokumentuj problém survivorship bias.

Pokud používaný datový zdroj neposkytuje historické membership universe:

* systém to musí uvést ve výsledcích experimentu,
* nesmí prezentovat výsledek jako survivorship-bias-free.

Do reportu přidej například:

```text
survivorship_bias_status
```

---

# 18. STRATEGY FRAMEWORK

Definuj rozhraní například:

```python
class Strategy(Protocol):

    @property
    def name(self) -> str:
        ...

    @property
    def required_lookback(self) -> int:
        ...

    def generate_targets(
        self,
        context: StrategyContext,
    ) -> TargetPortfolio:
        ...
```

Strategie nesmí znát budoucí data.

`StrategyContext` smí obsahovat pouze data dostupná do času rozhodnutí.

---

# 19. BASELINE STRATEGIES

Implementuj minimálně tyto strategie.

## 19.1 Buy & Hold

Slouží především jako benchmark.

---

## 19.2 Moving Average Trend Following

Například:

```text
fast MA
slow MA
```

parametry musí být konfigurovatelné.

---

## 19.3 Time-Series Momentum

Například návratnost za:

```text
3
6
12 měsíců
```

Parametrické.

---

## 19.4 Cross-Sectional Momentum

Vybere nejsilnější instrumenty z universe.

Parametry:

```text
lookback
number of selected assets
rebalance frequency
```

---

## 19.5 Mean Reversion

Například:

```text
z-score
RSI / distance from moving average
```

Nepiš strategii tak, aby automaticky fungovala pouze na jednom tickeru.

---

## 19.6 Breakout

Například:

```text
Donchian-style breakout
```

---

## 19.7 Pairs Trading

Musí podporovat dvojici aktiv.

Vytvoř minimálně:

```text
spread
rolling statistics
z-score
entry threshold
exit threshold
```

Pokud implementuješ statistické testy cointegrace, striktně zabraň použití budoucích hodnot.

---

# 20. STRATEGY PARAMETERS

Každá strategie musí poskytovat:

```text
name
version
parameters
required lookback
supported frequency
supported universe
description
```

Výsledek musí být reprodukovatelný z:

```text
strategy version
parameters
data version
code commit
random seed
```

---

# 21. BACKTEST ENGINE

Vytvoř vlastní jasně auditovatelnou vrstvu.

Nemusíš implementovat kompletní burzu, ale backtest musí korektně modelovat:

```text
signals
orders
fills
cash
positions
fees
slippage
portfolio valuation
corporate actions where applicable
```

---

# 22. ZÁSADNÍ PRAVIDLO BACKTESTU

Pokud strategie používá close cenu dne `T`:

nesmí být obchod vyplněn historickou close cenou `T`, pokud tato cena nebyla dostupná při rozhodnutí.

Použij jednoznačnou konvenci.

Například:

```text
signal calculated after close T
→ execution earliest at open T+1
```

nebo jiný explicitně definovaný model.

Konvenci:

1. implementuj,
2. zdokumentuj,
3. otestuj regresním testem.

---

# 23. TRANSACTION COST MODEL

Vytvoř interface:

```text
TransactionCostModel
```

Podporuj:

```text
fixed commission
percentage commission
minimum commission
```

---

# 24. SLIPPAGE MODEL

Vytvoř:

```text
SlippageModel
```

Minimálně implementuj:

```text
ZeroSlippage
FixedBpsSlippage
```

Například:

```text
1 bp
5 bp
10 bp
```

musí být možné testovat parametricky.

---

# 25. SPREAD

U strategie/modelu, kde je relevantní intraday nebo quote execution, podporuj bid/ask spread.

U EOD backtestu jasně zdokumentuj aproximaci.

---

# 26. POSITION SIZING

Podporuj minimálně:

```text
equal weight
fixed fractional
volatility targeting
```

Volatility targeting nesmí používat budoucí volatilitu.

---

# 27. PORTFOLIO CONSTRUCTION

Vytvoř samostatnou vrstvu:

```text
Strategy signals
↓
PortfolioConstructor
↓
TargetPortfolio
```

Podporuj:

```text
equal weight
signal weighted
volatility weighted
```

---

# 28. REBALANCING

Podporuj:

```text
daily
weekly
monthly
custom
```

Strategie musí explicitně deklarovat frekvenci.

---

# 29. BACKTEST METRICS

Počítej minimálně:

```text
total return
CAGR
annualized volatility
Sharpe ratio
Sortino ratio
Calmar ratio
maximum drawdown
drawdown duration
win rate
profit factor
average trade
median trade
number of trades
turnover
gross exposure
net exposure
time in market
best period
worst period
```

Pokud existuje benchmark:

```text
alpha
beta
tracking error
information ratio
```

---

# 30. EQUITY CURVE

Ukládej:

```text
timestamp
cash
market value
portfolio value
gross exposure
net exposure
drawdown
```

---

# 31. TRADE LOG

Každý backtest musí poskytovat auditovatelný trade log:

```text
timestamp
symbol
side
quantity
requested price
fill price
commission
slippage
strategy
reason
```

---

# 32. VALIDATION FRAMEWORK

Toto je jedna z nejdůležitějších částí projektu.

Implementuj:

```text
in-sample
validation
out-of-sample
walk-forward
```

Nikdy neoptimalizuj parametry na finálním testovacím období.

---

# 33. CHRONOLOGICAL SPLIT

Zakázáno:

```text
random train_test_split
```

pro time-series backtest.

Používej chronologické rozdělení.

Například:

```text
TRAIN
2010–2018

VALIDATION
2019–2021

TEST
2022–2025
```

Konkrétní období musí být nastavitelné.

---

# 34. WALK-FORWARD ANALYSIS

Implementuj:

```text
train window
validation window
step
```

Například:

```text
train 5 years
test 1 year
roll 1 year
```

Výsledek zobraz:

```text
window-by-window
aggregate
```

---

# 35. MONTE CARLO

Implementuj Monte Carlo analýzu založenou na historických trade returns nebo vhodném bootstrap přístupu.

Minimálně:

```text
1000 simulations
```

konfigurovatelné.

Výstup:

```text
return distribution
max drawdown distribution
5th percentile
50th percentile
95th percentile
probability of loss
```

Používej seed.

Výsledek musí být reprodukovatelný.

---

# 36. PARAMETER STABILITY

Nechci pouze:

```text
nejlepší parameter = 73
```

Chci kontrolu okolí nejlepší hodnoty.

Pokud:

```text
parameter 72 = velmi dobrý
parameter 73 = fantastický
parameter 74 = katastrofa
```

označ strategii jako nestabilní.

Vytvoř mechanismus:

```text
parameter stability score
```

nebo obdobnou diagnostiku.

---

# 37. OVERFITTING PROTECTION

Implementuj nebo minimálně reportuj:

```text
number of tested parameter combinations
in-sample vs out-of-sample degradation
parameter stability
walk-forward consistency
```

Výsledek experimentu musí obsahovat:

```text
overfitting_risk
```

například:

```text
LOW
MEDIUM
HIGH
```

Definici zdokumentuj.

---

# 38. RESEARCH/EXPERIMENT ENGINE

Vytvoř první skutečně autonomní část systému.

Uživatel musí být schopen definovat experiment například:

```yaml
strategy: moving_average
universe:
  - SPY

parameters:
  fast:
    values: [20, 50, 100]

  slow:
    values: [100, 150, 200]

backtest:
  starting_cash: 100000

costs:
  commission_bps: 1
  slippage_bps: 2

validation:
  mode: walk_forward
```

Experiment engine:

```text
načte experiment
→ vytvoří kombinace
→ provede backtesty
→ vypočítá metriky
→ provede validation
→ seřadí kandidáty
→ uloží výsledky
→ vytvoří report
```

---

# 39. PARAMETER SEARCH

Začni spolehlivě s:

```text
grid search
```

Architekturu připrav také na:

```text
random search
Bayesian optimization
```

Ale nepřidávej optimalizaci, která používá final test data.

---

# 40. EXPERIMENT REPRODUCIBILITY

Každý `ExperimentRun` musí uložit:

```text
experiment ID
strategy version
parameters
universe
data provider
data range
data fingerprint
code git commit
random seed
started_at
completed_at
status
metrics
```

---

# 41. STRATEGY REGISTRY

Vytvoř registry:

```text
StrategyRegistry
```

Dashboard i CLI musí být schopny získat seznam dostupných strategií bez hardcoded frontend seznamu.

---

# 42. CANDIDATE REGISTRY

Úspěšné experimenty ukládej jako:

```text
StrategyCandidate
```

Candidate může mít lifecycle:

```text
RESEARCH
VALIDATED
PAPER_ELIGIBLE
PAPER_ACTIVE
PAPER_REJECTED
LIVE_ELIGIBLE
DISABLED
```

`LIVE_ELIGIBLE` neznamená automatické zapnutí live tradingu.

---

# 43. PAPER ELIGIBILITY ENGINE

Vytvoř konfigurovatelná pravidla.

Například může systém hodnotit:

```text
out-of-sample return
Sharpe
maximum drawdown
Calmar
number of observations
parameter stability
walk-forward consistency
cost sensitivity
```

Thresholds nesmí být zakódovány hluboko v programu.

Použij configuration.

Příklad:

```yaml
paper_eligibility:
  min_oos_sharpe: 0.8
  max_drawdown: 0.20
  min_calmar: 0.5
  require_positive_oos_return: true
  require_parameter_stability: true
```

Tyto hodnoty jsou pouze počáteční konfigurovatelné defaults, nikoli tvrzení, že strategie bude zisková.

---

# 44. COST SENSITIVITY

Každého kandidáta otestuj například pro:

```text
base costs
2x costs
3x costs
```

Pokud strategie přestane fungovat po mírném zvýšení nákladů, označ:

```text
cost_fragile = true
```

---

# 45. RISK ENGINE

Risk Engine musí být centrální služba.

Každý OrderIntent musí projít:

```text
RiskEngine.evaluate()
```

Výsledek:

```text
APPROVED
REJECTED
MODIFIED
```

s důvodem.

---

# 46. RISK LIMITS

Implementuj konfiguraci minimálně:

```text
max_position_pct
max_single_order_pct
max_gross_exposure
max_net_exposure
max_number_positions
max_daily_loss
max_portfolio_drawdown
max_turnover
max_orders_per_day
max_notional_per_day
```

Default:

```text
leverage <= 1
short selling = disabled
```

---

# 47. KILL SWITCH

Implementuj automatický kill switch.

Aktivuje se například při:

```text
maximum daily loss exceeded
maximum drawdown exceeded
stale market data
data quality failure
broker reconciliation failure
unexpected negative cash
position mismatch
order flood
critical exception
```

Stav:

```text
NORMAL
HALTED
```

Pokud:

```text
HALTED
```

žádné nové pozice.

Exit/reconciliation operace řeš bezpečně podle typu incidentu.

---

# 48. STALE DATA PROTECTION

Před trading cyklem ověř:

```text
timestamp posledních dat
expected market session
provider health
```

Staré ceny nesmí spustit nový obchod.

---

# 49. PAPER BROKER

Vytvoř plně funkční:

```text
PaperBroker
```

který podporuje:

```text
cash
positions
market orders
limit orders
partial fill model where useful
commissions
slippage
portfolio valuation
cancel order
order status
fill history
```

PaperBroker musí být použitelný pro:

```text
unit tests
integration tests
paper trading
```

---

# 50. BROKER INTERFACE

Vytvoř abstrakci například:

```python
class Broker(Protocol):

    async def get_account(...):
        ...

    async def get_positions(...):
        ...

    async def submit_order(...):
        ...

    async def cancel_order(...):
        ...

    async def get_open_orders(...):
        ...
```

Implementace:

```text
PaperBroker
```

musí být plná.

Dále připrav adapter hranici pro:

```text
Interactive Brokers
```

nebo jiného vhodného brokera.

Pokud aktuální broker API vyžaduje konfiguraci, kterou nelze automaticky vytvořit:

* vytvoř adapter,
* configuration model,
* dokumentaci,
* integrační testy označ jako optional,
* nikdy nevkládej falešné credentials.

Při implementaci broker adapteru používej aktuální oficiální broker dokumentaci.

---

# 51. ORDER IDEMPOTENCY

Automatický trading cycle nesmí při retry vytvořit duplicitní order.

Každý intent musí mít unikátní:

```text
client_order_id
```

odvozený od trading cycle a intentu.

Implementuj idempotency.

Přidej regresní test:

```text
stejný trading cycle spuštěný dvakrát
→ žádný duplicitní obchod
```

---

# 52. RECONCILIATION

Po každém trading cyklu proveď:

```text
expected positions
vs
broker positions
```

Pokud rozdíl překročí toleranci:

```text
halt trading
create incident
```

---

# 53. AUTOMATED TRADING CYCLE

Implementuj pipeline:

```text
1. acquire lock

2. determine market session

3. load strategy configuration

4. fetch latest data

5. validate data

6. generate features

7. generate strategy target portfolio

8. portfolio construction

9. compare target vs actual positions

10. generate OrderIntents

11. RiskEngine evaluation

12. submit approved orders

13. capture fills

14. reconcile portfolio

15. calculate performance

16. write audit log

17. release lock
```

Celý proces musí být:

```text
idempotent
observable
recoverable
auditable
```

---

# 54. SCHEDULER

Implementuj scheduler pro paper trading.

Pro EOD strategie například:

```text
run after market close
```

Nepoužívej pouze jednoduchý čas typu:

```text
22:00 every day
```

bez znalosti trading kalendáře.

Respektuj:

```text
weekends
holidays
market sessions
```

---

# 55. AUDIT LOG

Vytvoř immutable-style audit trail.

Každá významná událost:

```text
DATA_FETCHED
DATA_REJECTED
SIGNAL_GENERATED
TARGET_GENERATED
RISK_APPROVED
RISK_REJECTED
ORDER_SUBMITTED
ORDER_FILLED
ORDER_CANCELLED
RECONCILIATION_FAILED
KILL_SWITCH_TRIGGERED
STRATEGY_ENABLED
STRATEGY_DISABLED
CONFIG_CHANGED
```

Audit event:

```text
id
timestamp
event_type
entity_type
entity_id
payload
correlation_id
```

---

# 56. DATABASE

Použij PostgreSQL.

Vytvoř Alembic migrations.

Tabulky minimálně:

```text
instruments
market_data_metadata
data_quality_events

strategies
strategy_candidates

experiments
experiment_runs
backtests
backtest_metrics
backtest_trades

paper_accounts
positions
orders
fills

trading_cycles

risk_events
audit_events

system_settings
```

Pokud bude lepší normalizace, uprav strukturu.

---

# 57. MARKET DATA STORAGE

Nesnaž se automaticky ukládat desítky milionů barů jako JSON v PostgreSQL.

Pro větší historická data preferuj efektivní storage například:

```text
Parquet
```

PostgreSQL může ukládat:

```text
metadata
dataset fingerprint
provider
range
path
quality status
```

Architekturu vysvětli v `docs/architecture.md`.

---

# 58. DATASET FINGERPRINT

Backtest musí přesně vědět, na kterých datech běžel.

Vytvoř dataset fingerprint například z:

```text
symbols
provider
time range
bar frequency
row counts
file hashes
```

Experiment musí uložit fingerprint.

---

# 59. API

Vytvoř FastAPI REST API.

Endpointy minimálně:

```text
GET  /health
GET  /ready

GET  /strategies
GET  /strategies/{id}

GET  /experiments
POST /experiments
POST /experiments/{id}/run
GET  /experiments/{id}/runs

GET  /backtests
GET  /backtests/{id}

GET  /candidates
POST /candidates/{id}/enable-paper
POST /candidates/{id}/disable

GET  /portfolio
GET  /positions
GET  /orders

GET  /risk/status
GET  /risk/events
POST /risk/halt

GET  /trading/cycles

GET  /data/health

GET  /audit
```

Přidej pagination.

Přidej filtering.

Přidej validation.

---

# 60. API SECURITY

Aplikace je single-user operator system.

Ve výchozím Docker Compose:

```text
dashboard/API nesmí být bezmyšlenkovitě veřejně vystavené do internetu
```

Preferuj bind:

```text
127.0.0.1
```

Pokud implementuješ authentication:

* bezpečné sessions nebo tokeny,
* heslo nikdy plaintext,
* rate limiting tam, kde dává smysl.

Žádné secrets ve frontendu.

---

# 61. FRONTEND

Vytvoř skutečný použitelný dashboard.

Default language:

```text
Czech
```

Technické termíny mohou zůstat anglicky, pokud je jejich překlad horší.

---

# 62. DASHBOARD HOME

Zobraz:

```text
System status
Trading mode
Kill switch status

Paper portfolio value
Cash
Daily P/L
Total P/L
Current drawdown

Active strategies
Open positions
Open orders

Last trading cycle
Next expected cycle

Data health
Broker health
```

---

# 63. EQUITY CURVE

Interaktivní graf:

```text
portfolio value
benchmark
drawdown
```

Možnost období:

```text
1M
3M
6M
YTD
1Y
ALL
```

---

# 64. STRATEGIES PAGE

Tabulka:

```text
strategy
version
status
parameters
OOS Sharpe
CAGR
max drawdown
paper P/L
stability
```

Kliknutí otevře detail.

---

# 65. STRATEGY DETAIL

Zobraz:

```text
description
parameters
current status
latest backtest
walk-forward
Monte Carlo
trade distribution
cost sensitivity
paper performance
backtest vs paper comparison
audit history
```

---

# 66. EXPERIMENTS PAGE

Umožni:

```text
create experiment
select strategy
select universe
define date range
define parameters
define transaction costs
run
```

Výsledky zobraz jako:

```text
sortable table
heatmap where applicable
equity curves
metric comparison
```

---

# 67. BACKTEST DETAIL

Zobraz:

```text
equity curve
drawdown
monthly returns
yearly returns
metrics
trades
parameters
dataset fingerprint
cost model
validation split
```

---

# 68. MONTE CARLO UI

Graf:

```text
simulation distribution
```

a přehled:

```text
P5
P50
P95

loss probability
drawdown percentiles
```

---

# 69. PAPER TRADING PAGE

Zobraz:

```text
account
cash
equity
positions
orders
fills
realized P/L
unrealized P/L
```

---

# 70. RISK PAGE

Zobraz:

```text
current limits
current exposures
daily loss
drawdown
kill-switch state
recent risk rejections
```

Tlačítko:

```text
HALT TRADING
```

musí vyžadovat explicitní potvrzení.

Obnovení:

```text
RESUME
```

rovněž.

---

# 71. DATA HEALTH PAGE

Zobraz:

```text
provider
last successful update
latest timestamp
missing data
validation errors
dataset status
```

---

# 72. AUDIT PAGE

Vyhledávání podle:

```text
event
strategy
symbol
correlation ID
date
```

---

# 73. CLI

Vytvoř CLI:

```bash
aq --help
```

Příklady:

```bash
aq data fetch
aq data validate

aq strategy list

aq experiment create
aq experiment run <id>

aq backtest run <strategy>

aq candidate list

aq trading cycle --paper

aq risk status
aq risk halt

aq doctor
```

`aq doctor` musí kontrolovat:

```text
database
redis
data provider
broker
filesystem
configuration
```

---

# 74. CONFIGURATION

Používej environment variables + config files.

Vytvoř:

```text
.env.example
```

Nikdy:

```text
.env
```

s tajnými údaji.

Rozděl konfiguraci:

```text
application
database
redis
market data
broker
risk
trading
logging
```

---

# 75. SECRET MANAGEMENT

Zakázáno commitnout:

```text
API keys
broker credentials
passwords
tokens
private keys
```

Přidej secret scanning do CI, pokud je rozumně možné.

---

# 76. OBSERVABILITY

Implementuj structured logging.

Log musí obsahovat pokud existuje:

```text
timestamp
level
service
correlation_id
trading_cycle_id
strategy_id
order_id
message
```

Nikdy neloguj secrets.

---

# 77. HEALTH CHECKS

Implementuj:

```text
/health
/ready
```

`health`:

proces běží.

`ready`:

```text
DB
Redis
critical config
```

jsou připravené.

---

# 78. METRICS

Pokud je to přiměřené, implementuj Prometheus-compatible metrics.

Například:

```text
trading_cycles_total
trading_cycle_failures_total

orders_total
risk_rejections_total

market_data_errors_total

backtest_duration_seconds
```

---

# 79. ERROR HANDLING

Zakázáno:

```python
except Exception:
    pass
```

Kritické chyby musí být:

```text
logged
classified
propagated or handled explicitly
```

---

# 80. RETRIES

Síťové operace mohou používat:

```text
timeout
bounded retry
exponential backoff
jitter
```

Ale:

```text
broker order submission
```

nesmí slepě retryovat bez idempotency/reconciliation.

---

# 81. TESTING STRATEGY

Chci vysokou úroveň testů.

Minimálně:

```text
unit
integration
regression
property/invariant tests
API tests
frontend tests
```

---

# 82. CRITICAL BACKTEST TESTS

Přidej testy dokazující:

### Test 1

Budoucí cena není dostupná strategii.

### Test 2

Signal z close `T` nemůže být fillnut na dřívější cenu.

### Test 3

Poplatek snižuje portfolio value.

### Test 4

Slippage funguje správným směrem:

```text
BUY → horší vyšší cena
SELL → horší nižší cena
```

### Test 5

Cash nikdy není nekonzistentní.

### Test 6

Trade P/L odpovídá fillům.

### Test 7

Stejný seed + data + config:

```text
identický výsledek
```

---

# 83. PROPERTY TESTS

Použij property-based tests tam, kde dávají smysl.

Invariants:

```text
portfolio equity = cash + market value

high >= low

gross exposure >= abs(net exposure)

drawdown <= 0

filled quantity <= order quantity
```

---

# 84. RISK TESTS

Testuj minimálně:

```text
order exceeding max position → reject

order exceeding gross exposure → reject

daily loss exceeded → halt

drawdown exceeded → halt

stale data → reject trading cycle

negative invalid price → reject

kill switch → no new order
```

---

# 85. LIVE SAFETY TEST

Musí existovat test:

```text
LIVE_TRADING_ENABLED=false
```

a ověřit, že žádný live adapter nemůže odeslat order.

Další test:

```text
TRADING_MODE=live
LIVE_TRADING_ENABLED=true
missing confirmation
→ reject
```

---

# 86. PAPER BROKER TESTS

Testuj:

```text
market buy
market sell
limit order
cancel
commission
slippage
position updates
cash updates
realized P/L
unrealized P/L
```

---

# 87. TRADING CYCLE TEST

Vytvoř kompletní integration test:

```text
fixture market data
↓
strategy
↓
target
↓
risk
↓
order
↓
paper broker
↓
fill
↓
portfolio
↓
audit event
```

---

# 88. IDEMPOTENCY TEST

Spusť stejný cycle dvakrát.

Výsledek:

```text
žádný duplicate order
```

---

# 89. DATA-LEAKAGE REGRESSION TEST

Vytvoř dataset, kde se obrovský cenový pohyb odehraje až v budoucnosti.

Ověř, že změna budoucích hodnot:

```text
nezmění dřívější trading signals
```

To je kritický regresní test.

---

# 90. BACKTEST GOLDEN FIXTURE

Vytvoř malý ručně ověřitelný dataset.

Například:

```text
10–30 bars
```

s ručně vypočitatelným očekávaným výsledkem.

Testuj:

```text
cash
positions
fills
fees
equity
```

proti známým hodnotám.

---

# 91. FRONTEND TESTS

Minimálně:

```text
dashboard renders
API errors handled
risk halt confirmation
strategy details render
backtest metrics render
```

---

# 92. CI

Vytvoř GitHub Actions workflow.

Minimálně:

```text
format check
lint
type check
backend tests
frontend tests
build
security checks
```

CI nikdy:

```text
nepoužije real broker
nepoužije production credentials
nepošle order
```

---

# 93. CODE QUALITY

Python:

```text
typing
linting
formatting
```

TypeScript:

```text
strict mode
lint
format
```

Používej dependency injection na hranicích systému.

Vyhýbej se:

```text
god classes
global mutable state
business logic in API route
business logic in React component
```

---

# 94. DATABASE MIGRATIONS

Každá změna schema:

```text
Alembic migration
```

CI musí ověřit, že migrations lze aplikovat na prázdnou DB.

---

# 95. DOCKER

Vytvoř:

```bash
docker compose up
```

tak, aby lokálně nastartovalo minimálně:

```text
PostgreSQL
Redis
Backend
Worker
Scheduler
Frontend
```

Použij healthchecks.

---

# 96. MAKEFILE

Přidej například:

```bash
make setup
make dev
make test
make lint
make format
make typecheck
make migrate
make seed
make doctor
```

---

# 97. DEMO DATA

Projekt musí být možné spustit bez broker účtu.

Vytvoř:

```text
demo mode
```

s:

```text
fixture/sample market dataset
PaperBroker
baseline experiment
```

Po:

```bash
make demo
```

musí být možné otevřít dashboard a vidět smysluplná data.

---

# 98. SEED

Vytvoř seed:

```text
baseline strategies
paper account
risk configuration
example experiment
```

---

# 99. FIRST END-TO-END DEMO

Po dokončení musí fungovat scénář:

```text
1. Start application

2. Load historical SPY data

3. Run Buy & Hold benchmark

4. Run Moving Average strategy

5. Run parameter grid

6. Perform OOS validation

7. Generate metrics

8. Perform Monte Carlo

9. Rank candidates

10. Mark candidate PAPER_ELIGIBLE if criteria pass

11. Enable candidate for paper trading

12. Run simulated trading cycle

13. PaperBroker fills order

14. Portfolio updates

15. Dashboard displays result

16. Audit log contains full history
```

Automatizuj tento scenario jako integration/E2E test tam, kde je to praktické.

---

# 100. BACKTEST VS PAPER MONITORING

Implementuj modul sledující rozdíl:

```text
expected strategy performance
vs.
paper trading performance
```

Minimálně sleduj:

```text
signal differences
execution differences
slippage differences
position differences
return deviation
```

Pokud je rozdíl významný:

```text
strategy health = DEGRADED
```

---

# 101. STRATEGY HEALTH

Stavy:

```text
HEALTHY
DEGRADED
HALTED
```

Příklady důvodů:

```text
data unavailable
performance degradation
unexpected execution
risk breach
reconciliation mismatch
```

---

# 102. STRATEGY DECAY

Vytvoř reporting, který porovnává rolling paper/live performance s historickou OOS distribucí.

Neautomatizuj slepě deaktivaci strategie pouze proto, že několik obchodů prodělalo.

Použij statisticky rozumný, konfigurovatelný model.

Pokud není dost dat:

```text
INSUFFICIENT_DATA
```

---

# 103. MONTHLY PERFORMANCE REPORT

Implementuj generování reportu:

```text
portfolio return
benchmark return
strategy contribution
drawdown
number of trades
costs
risk events
strategy status
```

Export:

```text
HTML
CSV/JSON
```

PDF je bonus, nikoliv podmínka MVP.

---

# 104. RESEARCH REPORT

Po experimentu automaticky vytvoř report:

```text
Strategy
Dataset
Parameters tested
Best validation candidates

IS results
OOS results
Walk-forward results
Monte Carlo
Cost sensitivity
Parameter stability
Overfitting assessment

Conclusion:
REJECT
RESEARCH
PAPER_ELIGIBLE
```

Rozhodnutí musí být založeno na konfiguraci, ne na textovém dojmu.

---

# 105. NO MAGIC NUMBERS

Risk thresholds, fees, slippage, eligibility criteria atd.:

```text
configuration
```

Ne rozházené konstanty v kódu.

---

# 106. PERFORMANCE

Neprováděj předčasnou optimalizaci.

Ale backtest stovek konfigurací nesmí být extrémně pomalý kvůli zbytečným DB roundtripům.

Preferuj:

```text
columnar data
vectorized feature calculations
batch persistence
```

Execution simulation může být event-driven.

---

# 107. CACHING

Feature calculations mohou používat cache.

Cache key musí obsahovat:

```text
dataset fingerprint
feature
parameters
version
```

Nikdy nesmí dojít k použití feature z jiného datasetu.

---

# 108. CONCURRENCY

Experimenty mohou běžet paralelně.

Ale:

```text
stejný trading account
```

nesmí mít současně dva aktivní execution cycles bez locku.

Použij DB/distributed lock.

---

# 109. MIGRATION TOWARD LIVE TRADING

Architektura musí umožnit budoucí tok:

```text
PaperBroker
↓
Broker paper account
↓
Live broker
```

bez změny strategií.

Rozdíl brokerů musí být za adapterem.

---

# 110. LIVE TRADING NESMÍ BÝT SOUČÁSTÍ AUTOMATICKÉHO DEMO

Žádný:

```text
make demo
make test
docker compose up
```

nesmí odeslat live order.

---

# 111. DOCUMENTATION

README musí obsahovat:

```text
What is Autonomous Quant Lab
Architecture
Requirements
Quick Start
Demo
Configuration
Market data
Research workflow
Backtesting
Paper trading
Risk controls
Tests
Docker
Troubleshooting
Live trading safety
```

---

# 112. ARCHITECTURE DOCUMENT

`docs/architecture.md` musí vysvětlit:

```text
components
dependencies
data flow
trading flow
persistence
background jobs
failure handling
```

Použij Mermaid diagramy.

Například:

```text
MarketData
    ↓
Strategy
    ↓
Portfolio
    ↓
Risk
    ↓
Execution
    ↓
Broker
```

---

# 113. DOMAIN DOCUMENT

`docs/domain-model.md`

musí vysvětlovat hlavní entities a jejich vztahy.

Použij Mermaid ER diagram, pokud dává smysl.

---

# 114. BACKTEST DOCUMENT

`docs/backtesting.md` musí explicitně popsat:

```text
timestamp semantics
signal timing
execution timing
fees
slippage
adjusted prices
corporate actions
look-ahead protection
```

---

# 115. RISK DOCUMENT

`docs/risk-management.md`

musí obsahovat všechny risk limity a kill switch.

---

# 116. LIVE SAFETY DOCUMENT

`docs/live-trading-safety.md`

musí obsahovat checklist.

Například:

```text
[ ] OOS validation completed
[ ] walk-forward completed
[ ] paper trading completed
[ ] reconciliation verified
[ ] cost model verified
[ ] risk limits configured
[ ] max order configured
[ ] instrument allowlist configured
[ ] kill switch tested
[ ] live credentials configured
[ ] explicit live flags enabled
```

---

# 117. DEVELOPMENT PRINCIPLES

Nevytvářej placeholdery typu:

```python
def calculate_sharpe(...):
    pass
```

pokud funkce patří do scope aktuální fáze.

Nevytvářej pouze prázdné interface.

Každá dokončená fáze musí být skutečně použitelná.

---

# 118. TODO POLICY

TODO je povolen pouze pro:

```text
future enhancement
external credentials
explicitly out-of-scope feature
```

Ne pro nehotovou core funkcionalitu.

---

# 119. NEPŘEDSTÍREJ IMPLEMENTACI

Pokud něco skutečně nefunguje:

nepiš:

```text
implemented
```

Oprav to nebo to jasně označ jako nedokončené.

---

# 120. VERIFY EVERYTHING

Po každé hlavní fázi spusť příslušné testy.

Před dokončením spusť kompletní sadu:

```text
formatter
lint
type checker
unit tests
integration tests
frontend tests
build
Docker validation
```

Pokud některý nástroj není dostupný:

zdokumentuj přesně proč.

---

# 121. SECURITY REVIEW

Před dokončením proveď vlastní security review.

Zkontroluj:

```text
secret leakage
SQL injection
unsafe deserialization
path traversal
command injection
frontend secret exposure
CORS
authentication boundaries
live trading safeguards
dependency issues
logging secrets
```

Oprav nalezené problémy.

---

# 122. QUANT REVIEW

Proveď samostatný quantitative correctness review.

Hledej:

```text
look-ahead bias
survivorship bias
data leakage
incorrect annualization
incorrect Sharpe
incorrect drawdown
fee mistakes
slippage direction
future volatility usage
same-bar execution bias
parameter optimization leakage
test-period contamination
```

Oprav nalezené problémy.

---

# 123. FAILURE INJECTION

Otestuj alespoň tyto situace:

```text
market data provider down
database unavailable
Redis unavailable
broker unavailable
duplicate cycle
stale quote
malformed bar
partial order failure
reconciliation mismatch
```

Systém musí selhat bezpečně.

---

# 124. DEFAULT RISK POSTURE

Použij konzervativní development defaults.

Například:

```text
paper trading
long only
no leverage
limited position size
limited number of positions
limited order count
```

Konkrétní hodnoty dej do configu.

---

# 125. EXAMPLE STRATEGY SUITE

Vytvoř:

```text
config/experiments/baseline.yaml
```

který automaticky otestuje základní strategie.

Výsledkem má být leaderboard.

---

# 126. LEADERBOARD

Dashboard:

```text
Rank
Strategy
Parameters
OOS CAGR
OOS Sharpe
Max DD
Calmar
Trades
Cost sensitivity
Stability
Walk-forward
Status
```

Nikdy defaultně neřaď pouze podle maximálního return.

Použij konfigurovatelný composite score.

---

# 127. COMPOSITE SCORE

Navrhni transparentní scoring.

Například kombinace:

```text
OOS risk-adjusted return
drawdown
walk-forward consistency
parameter stability
cost robustness
```

Scoring musí být:

```text
deterministic
documented
configurable
```

---

# 128. BENCHMARK

Každá long-equity strategie musí umožnit benchmark.

Default pro US equity example:

```text
SPY
```

Nepředpokládej, že SPY je vhodný benchmark pro všechny budoucí asset classes.

---

# 129. FUTURE EXTENSIBILITY

Architektura musí později umožnit přidat:

```text
ETFs
individual equities
futures
FX
crypto
options
```

Ale MVP implementuj primárně pro:

```text
liquid US equities/ETFs
daily bars
long-only
```

Tím omezíme komplexitu první verze.

---

# 130. ML

Machine learning není nutnou součástí MVP.

Připrav architekturu na budoucí:

```text
MLStrategy
```

ale neimplementuj složitý black-box model jen proto, aby projekt obsahoval AI.

Correctness a validace mají přednost.

---

# 131. AUTONOMOUS RESEARCH LOOP

Po vytvoření základního systému implementuj automatický research pipeline:

```text
load configured research suite
↓
fetch/update data
↓
validate dataset
↓
run experiments
↓
validate candidates
↓
stress transaction costs
↓
walk-forward
↓
Monte Carlo
↓
calculate robustness
↓
rank candidates
↓
update candidate registry
↓
generate report
```

Příkaz:

```bash
aq research run baseline
```

---

# 132. PAPER PROMOTION

Research pipeline smí kandidáta označit:

```text
PAPER_ELIGIBLE
```

Nesmí jej automaticky aktivovat pro live.

Pro paper může být automatické povolení volitelná konfigurační funkce.

Default:

```text
AUTO_ENABLE_PAPER=false
```

---

# 133. PERIODIC RESEARCH

Připrav scheduler tak, aby bylo později možné:

```text
weekly research
monthly validation
daily paper trading
```

Nespouštěj drahé experimenty každou minutu.

---

# 134. DATABASE AUDITABILITY

Nemaž historické experimenty při novém runu.

Každý run je immutable historický záznam.

---

# 135. VERSIONING

Strategie musí mít version identifier.

Změna logiky strategie:

```text
nová version
```

Starý backtest musí zůstat interpretovatelný.

---

# 136. GIT COMMIT

Do ExperimentRun ukládej aktuální:

```text
git commit SHA
```

pokud je dostupný.

---

# 137. RANDOMNESS

Každá random komponenta:

```text
explicit random seed
```

Nikdy nepoužívej skrytou nereprodukovatelnou randomness v analytické pipeline.

---

# 138. MONEY

Dávej pozor na:

```text
rounding
currency
broker quantity precision
```

Doména musí být připravena na měnu instrumentu.

MVP může používat:

```text
USD base currency
```

ale nehardcoduj USD do celé domény.

---

# 139. SYMBOL IDENTIFICATION

Nevycházej dlouhodobě pouze z:

```text
ticker string
```

Instrument musí mít interní stabilní ID.

Ticker může být atribut.

---

# 140. ORDER QUANTITY

MVP může podporovat celé akcie.

Architekturu připrav na fractional shares.

---

# 141. PAPER CAPITAL

Default demo account například:

```text
100,000 USD
```

Konfigurovatelné.

---

# 142. PERFORMANCE ATTRIBUTION

Přidej základní attribution:

```text
P/L by strategy
P/L by instrument
fees
slippage
```

---

# 143. DEPENDENCY MANAGEMENT

Pinuj dependencies rozumným způsobem.

Vytvoř lockfile.

Nepoužívej abandonované knihovny, pokud existuje lepší stabilní alternativa.

---

# 144. EXTERNAL DOCUMENTATION

Pokud při implementaci potřebuješ zjistit aktuální chování:

```text
FastAPI
SQLAlchemy
Next.js
broker APIs
market data APIs
```

používej primárně jejich oficiální dokumentaci.

Nevymýšlej neexistující endpointy nebo SDK metody.

---

# 145. DEFINITION OF DONE

Projekt není dokončen, dokud:

```text
[ ] repository lze nainstalovat

[ ] migrations fungují

[ ] backend se spustí

[ ] frontend se spustí

[ ] docker compose funguje

[ ] market data pipeline funguje

[ ] data validation funguje

[ ] baseline strategies fungují

[ ] backtesting funguje

[ ] costs fungují

[ ] slippage funguje

[ ] OOS validation funguje

[ ] walk-forward funguje

[ ] Monte Carlo funguje

[ ] experiment engine funguje

[ ] leaderboard funguje

[ ] risk engine funguje

[ ] paper broker funguje

[ ] trading cycle funguje

[ ] idempotency funguje

[ ] reconciliation funguje

[ ] audit log funguje

[ ] dashboard funguje

[ ] demo scenario funguje

[ ] unit tests procházejí

[ ] integration tests procházejí

[ ] frontend tests procházejí

[ ] lint prochází

[ ] type checking prochází

[ ] build prochází

[ ] žádný live order nemůže vzniknout v testech

[ ] dokumentace odpovídá implementaci
```

---

# 146. PRACOVNÍ POSTUP PRO TUTO ÚLOHU

Postupuj autonomně.

Nezastavuj se po vytvoření architektury.

Nezastavuj se po vytvoření scaffoldu.

Nezastavuj se po backendu.

Pokračuj přes všechny fáze, dokud není maximální rozumně dosažitelná část projektu skutečně implementována a ověřena.

Pokud narazíš na problém:

```text
analyzuj
→ oprav
→ spusť test
→ pokračuj
```

Neobcházej problém odstraněním validace nebo testu.

---

# 147. PŘI KONFLIKTU PRIORIT

Používej toto pořadí:

```text
1. zabránění nechtěnému live obchodu

2. quantitative correctness

3. data integrity

4. risk management

5. reproducibility

6. testability

7. security

8. maintainability

9. performance

10. UI polish
```

---

# 148. NEJDŘÍVE IMPLEMENTUJ VERTICAL SLICE

Aby projekt nezůstal jako množství nepropojených modulů, nejprve vytvoř funkční vertical slice:

```text
fixture data
→ MA strategy
→ backtest
→ risk
→ PaperBroker
→ persistence
→ API
→ basic dashboard
```

Otestuj ho end-to-end.

Teprve potom rozšiřuj další strategie a analytiku.

---

# 149. NÁSLEDNĚ IMPLEMENTUJ CELÝ RESEARCH PIPELINE

Po funkčním vertical slice:

```text
multiple strategies
parameter search
validation
walk-forward
Monte Carlo
robustness
candidate registry
leaderboard
paper eligibility
```

---

# 150. NÁSLEDNĚ IMPLEMENTUJ PAPER AUTOMATION

Poté:

```text
scheduler
trading cycle
idempotency
reconciliation
risk halt
monitoring
audit
```

---

# 151. FINÁLNÍ QUALITY PASS

Na konci se vrať k celému repository jako nezávislý reviewer.

Proveď:

```text
architecture review
quant review
security review
test review
operability review
documentation review
```

Nevěř automaticky svému předchozímu řešení.

Aktivně hledej chyby.

Nalezené chyby oprav.

Znovu spusť testy.

---

# 152. FINÁLNÍ ODPOVĚĎ CODEXU

Až bude práce hotová, neposílej pouze dlouhý obecný popis.

Uveď:

## Implemented

Stručný seznam skutečně implementovaných modulů.

## Architecture

Nejdůležitější architektonická rozhodnutí.

## Safety

Jak je zabráněno nechtěnému live tradingu.

## Quant correctness

Jak je řešen:

```text
look-ahead
OOS
walk-forward
costs
slippage
overfitting
```

## Tests

Přesné příkazy, které byly spuštěny, a jejich výsledek.

## Run locally

Přesné příkazy:

```bash
...
```

## Demo

Přesný postup, jak spustit demo.

## Current limitations

Pouze skutečná omezení.

## Repository status

Uveď:

```text
git status
```

a pokud bylo commitování součástí práce:

```text
commit SHA
```

---

# 153. START

Začni nyní.

První kroky:

```text
1. inspect repository
2. create/update AGENTS.md
3. create architecture decision
4. create implementation plan
5. bootstrap project
6. implement first vertical slice
7. test it
8. continue through remaining phases
```

Nevracej mi pouze návrh.

**Pracuj přímo v repository a vytvoř Autonomous Quant Lab.**
