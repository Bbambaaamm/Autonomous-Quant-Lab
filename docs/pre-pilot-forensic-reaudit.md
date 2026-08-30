# Předstartovní forenzní re-audit

## Executive verdict

**NOT READY FOR PAPER PILOT**

Verdikt zůstává fail-closed do zeleného required CI a production-like acceptance výsledného SHA. Production Alpaca v5 corporate-action contract je provider-capable a autonomous orchestrace nyní před open persistuje plně specifikovaný immutable economic intent (`side` + `quantity`). Bez validního intentu zůstává `PREOPEN_EXECUTION_INTENT_NOT_PERSISTED`.

Dokumentace nesmí být silnější než implementace. Zelený syntetický test není production evidence a timestamp připnutý na 09:30 sám o sobě nedokazuje, že objednávka vznikla před znalostí opening printu.

## Stav remediation PR #67

| Oblast | Stav | Bezpečnostní vlastnost |
| --- | --- | --- |
| Monitoring enrollment/schedule | REMEDIATED, čeká na výsledné CI | Deterministický `MONITOR_PAPER_DEPLOYMENT` job je idempotentně ensure/re-enable/normalizován. Autonomous enable kontroluje celý kanonický schedule a failuje při driftu. |
| Corporate-action provider | REMEDIATED, čeká na výsledné CI | Production Alpaca v5 používá SSE receipt evidence a exact REST ↔ SSE revision match; Stooq zůstává nezpůsobilý. |
| FAILED/DEAD_LETTER recovery | REMEDIATED, čeká na výsledné CI | Managed retry a control audit event vznikají v jedné DB transakci. Actor pochází ze server-side principalu, reason je povinný a correlation ID je persistovaný. |
| Managed retry UI | REMEDIATED, čeká na výsledné CI | ADMIN recovery se zobrazuje pouze FAILED/DEAD_LETTER runům, jejichž `scheduled_job_id` patří managed jobu. |
| Market-data egress | REMEDIATED, staging evidence stále povinná | DB `data` síť zůstává interní; backend/worker mají explicitní market-data egress; frontend ingress je oddělený. Aplikační Stooq transport dál allowlistuje HTTPS `stooq.com`. |
| Legacy 300s schedules | REMEDIATED, čeká na výsledné CI | Worker před prvním scheduler tickem fail-closed vypne enabled legacy/drifted `PREPARE_PAPER_SESSION` rows. |
| Monitoring retirement | REMEDIATED, čeká na výsledné CI | `RETIRED` v téže lifecycle transakci vypne deterministický monitoring schedule i autonomous deployment schedule. |
| XNYS causal execution | REMEDIATED, čeká na výsledné CI | PREPARE schedule je DST-safe ukotvený na 09:00 `America/New_York`; pre-open target delta používá causal adjusted signal close, immutable intent má deterministickou identitu/hash/lineage a open-time cesta smí jen validovat intent a použít raw open pro risk/fill. |

Žádný řádek označený `REMEDIATED, čeká na výsledné CI` není `PASS`, dokud required CI výsledného SHA skutečně neprojde.

## Monitoring schedule invariant

Bezpečnostní invariant je následující:

- právě jeden podporovaný open monitoring lifecycle;
- právě jeden deterministický `MONITOR_PAPER_DEPLOYMENT` job pro `monitoring_id`;
- canonical identity/config/account, `enabled=true`, `INTERVAL=3600`, `timezone=UTC`, očekávaný misfire policy/grace/max-attempts a due time bez nepřiměřeného odkladu;
- opakovaný enrollment stejného ACTIVE runu disabled nebo drifted schedule bezpečně normalizuje/re-enable;
- autonomous enable znovu ověřuje celý schedule a při jakémkoli driftu failuje closed;
- generic `/automation/jobs/*` mutations nejsou podporovaná cesta pro managed pilot jobs.

Enrollment record a automation job nejsou deklarovány jako jedna atomická transakce. Bezpečnost vzniká idempotentním ensure a následným fail-closed autonomous gate.

## Auditovaný managed retry

`POST /operator/automation/runs/{run_id}/retry` je podporovaná recovery cesta pouze pro managed PAPER jobs a pouze pro `FAILED`/`DEAD_LETTER`.

Retry state transition a `CONTROL_AUTOMATION_RUN_RETRY` audit evidence jsou persistovány v jedné databázové transakci. Pokud audit insert nemůže commitnout, nesmí commitnout ani `RETRY_SCHEDULED`. Generic `/automation/runs/{run_id}/retry` managed job odmítá. Recovery transition lze bezpečně persistovat i při globálně vypnuté automation; tím se worker nespouští a ekonomická execution zůstává zastavená až do samostatného povolení automation.

## Lifecycle retirement

Přechod monitoringu do `RETIRED` je terminální. V téže lifecycle transakci se vypíná:

- deterministický `MONITOR_PAPER_DEPLOYMENT` schedule pro monitoring;
- deterministický `PREPARE_PAPER_SESSION` schedule pro deployment.

Tím retired monitoring nevytváří nekonečné nové failed occurrences a není potřeba generic mutation managed jobu.

## Production network topology

Cílová topologie je rozdělena podle účelu:

- `data`: interní PostgreSQL síť;
- `application`: interní frontend ↔ backend komunikace;
- `market-data-egress`: neinterní egress pro backend a worker;
- `ingress`: frontendová síť pro loopback publish na hostu.

Samotná Docker egress síť není hostname firewall. Provider transport proto dál musí fail-closed povolit pouze očekávané HTTPS endpointy. Před pilotem je povinný production-like staging smoke skutečného provider egressu z backendu i workeru.

## XNYS scheduling a causalita

Arbitrary 300sekundový interval od času enablementu není autoritativní scheduling. Managed PREPARE schedule je daily `09:00 America/New_York`, tedy DST-safe a před open. Při startu workeru jsou staré enabled 300s nebo jinak drifted managed schedules před prvním scheduler tickem vypnuty.

`PREPARE_PAPER_SESSION` nyní po readiness a strategy evaluation vypočítá target delta z account equity, pozice a causal adjusted signal close. Persistuje side, quantity, decision/reference časy, snapshot/universe/observation lineage a integritní hash. Teprve potom materializuje `xnys:` occurrence. PostgreSQL trigger zakazuje UPDATE i DELETE.

Open-time cesta:

- chybějící intent vrací `PREOPEN_EXECUTION_INTENT_NOT_PERSISTED`;
- pozdní, konfliktní nebo integritně neplatný intent vrací `PREOPEN_EXECUTION_INTENT_INVALID`;
- validní intent vstupuje beze změny side/quantity do stávající `TradingCycleService` → risk → execution → `PaperBroker` cesty;
- strict executable-open cutoff zůstává `[open, open + 1 second)` jako obrana ostatních explicitně auditovaných cest;
- žádný timestamp backdating nesmí nahrazovat skutečný pre-open intent.

## Testovací izolace rate limitu

B1 PostgreSQL acceptance skládá do jednoho testu více validních operator mutation scénářů (včetně negativních gate testů) než běžná jednotlivá operator session. Process-local mutation bucket se proto uvnitř tohoto acceptance testu před navazující recovery částí explicitně vyčistí. Produkční `security_boundary`, limity ani jejich security testy se tím nemění; jde pouze o izolaci workflow acceptance od samostatně testované rate-limit politiky.

## Evidence nutná před změnou verdictu

Před změnou z `NOT READY FOR PAPER PILOT` musí být současně doloženo:

1. všechny required CI jobs zelené na výsledném SHA;
2. PostgreSQL acceptance pro monitoring ensure/re-enable, full schedule gate, retirement a rollout reconciliation;
3. API/RBAC test atomického operator retry včetně actor/reason/correlation evidence;
4. frontend test, že recovery control existuje jen pro managed FAILED/DEAD_LETTER runs a není zde economic `run-now`;
5. regresní testy DST schedule a strict missed-open/no-economic-effect chování;
6. production Compose topology test a staging provider-egress smoke;
7. skutečný corporate-action-capable production provider s E2E readiness evidence;
8. persistentní fully-specified immutable pre-open economic/order intent a E2E důkaz, že opening print nemůže ovlivnit side/quantity.

Capability body 7 a 8 jsou implementované. Zbývajícím blockerem verdictu je zelené required CI a production-like PostgreSQL/staging acceptance výsledného head SHA; bez těchto důkazů se pilot nesmí spustit.

## Final recommendation

Bezpečný stav zůstává **NOT READY FOR PAPER PILOT**, dokud neprojde required CI a production-like acceptance finálního SHA. Nejde již o známou chybějící ekonomickou capability, ale o chybějící provozní důkaz této konkrétní revize.

Acceptance tohoto PR se vyhodnocuje výhradně nad finálním head SHA po všech remediation commitech. Jakákoli další změna kódu nebo testů vyžaduje nové required CI nad novým head SHA.
