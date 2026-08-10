# Implementační plán

| Fáze | Stav | Implementováno a testy | Zbývá / omezení |
|---|---|---|---|
| 0 Bootstrap | dokončeno | packaging, AGENTS, konfigurace; lint/type/test | lockfile, CI |
| 1 Domain model | probíhá | Bar, order, fill, target, UTC validace | úplný model |
| 2 Market data | probíhá | CSV fixture a kritická validace | Parquet/research provider, kalendář, quality persistence |
| 3 Strategy | probíhá | verzovaná MA s lookbackem | ostatní baseline strategie |
| 4 Backtest | probíhá | next-open fills, cash, pozice, costs/slippage | úplné metriky, corporate actions |
| 5 Validation | čeká | leakage regression test | OOS, walk-forward |
| 6 Research automation | čeká | — | grid, Monte Carlo, scoring |
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
