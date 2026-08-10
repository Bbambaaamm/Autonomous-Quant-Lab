# Implementační plán

| Fáze | Stav | Implementováno a testy | Zbývá / omezení |
|---|---|---|---|
| 0 Bootstrap | dokončeno | packaging, AGENTS, konfigurace; lint/type/test | lockfile, CI |
| 1 Domain model | probíhá | Bar, order, fill, target, UTC validace | úplný model |
| 2 Market data | probíhá | CSV/Parquet provider, hash, quality events, kalendář | úplný US kalendář a DB quality persistence |
| 3 Strategy | probíhá | MA, buy-and-hold a Donchian s lookbackem | multi-asset strategie |
| 4 Backtest | dokončeno pro Phase 2 | next-open, deterministická ID, FIFO lot accounting, scale-in/out, idempotentní corporate actions, účetní invarianty, metriky/benchmark | multi-symbol engine je mimo Phase 2 |
| 5 Validation | probíhá | leakage, chronologický split, walk-forward hranice | výběr uvnitř foldů a embargo |
| 6 Research automation | probíhá | grid, identity, path-dependent stress re-backtest, Monte Carlo guardrail, stabilita, eligibility a report se stavem NOT_EVALUATED | obecný konfigurovatelný service use-case nad demo experimentem |
| 7 Portfolio & risk | probíhá | long-only konstrukce, allowlist/notional/kill switch | exposure/loss/drawdown limity |
| 8 Paper broker | probíhá | market fills a účetní aktualizace | limit/cancel/partial fills, P&L |
| 9 Trading cycle | čeká | audit přes uložený run | idempotence, reconciliation, locks |
| 10 REST API | probíhá | health, demo run, seznam běhů | CRUD, auth, OpenAPI rozšíření |
| 11 Dashboard | probíhá | spustitelný základní dashboard | Next.js a grafy |
| 12 Observability | čeká | — | structured logging, metrics |
| 13 Security | probíhá | žádný live adaptér, secret ignore | auth, dependency scan |
| 14 CI/CD | čeká | — | GitHub Actions, Docker |
| 15 Dokumentace | probíhá | README, architektura, tento plán | doména/risk/operations |
| 16 E2E verification | probíhá | fixture→API/dashboard integrační test | úplný master demo scénář |

Každá fáze je „dokončeno“ až po implementaci acceptance scope, unit/integration testech,
lintu, formátu a typechecku. Aktuální ověřitelný milník je první funkční vertical slice.

## Audit Phase 2

Audit odhalil náhodné UUID příkazů, které rušilo reprodukovatelnost; ID je nyní odvozeno z verze strategie, času a příkazu. Původní engine správně posílal jen datový prefix a plnil na následujícím baru. Portfolio správně účtovalo signed notional a komisi a ExecutionEngine vždy volal RiskEngine. Fill ale neuchovával raw reference cenu pro audit slippage; nyní ji uchovává. SQLite při čtení obnovuje UTC. Doplněny jsou regresní testy determinismu, accountingu, quality events, splitů, robustness a corporate actions.

Closure audit Phase 2.6 navíc našel chybějící lot ledger a přepis více corporate actions se
stejným timestampem. Portfolio nyní alokuje výstupy FIFO, split upraví lot quantity/unit basis,
akce jsou idempotentní a každý equity bod dokládá `equity = cash + market_value`. Objective
zůstává konfigurovatelnou ranking heuristikou, nikoli tvrzením o statistické optimalitě; všechny
eligibility hranice jsou inkluzivní. Demo guardraily se v reportu označují `NOT_EVALUATED`
s důvodem místo prázdného výsledku.
