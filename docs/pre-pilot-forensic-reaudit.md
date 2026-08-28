# Předstartovní forenzní re-audit

## Executive verdict

**CONDITIONAL READY FOR PAPER PILOT**

Tento verdikt platí pro stav po odstranění nalezené přímé legacy execution cesty. Pilot není
bezpodmínečně připraven: v auditním prostředí nebyl dostupný Docker/PostgreSQL ani uzamčený
Python toolchain, a proto zde nelze označit clean-database tok ani production container runtime
za `END-TO-END PASS`. Před pilotem musí CI na výsledném commitu dodat chybějící důkaz.

## Audited commit

Výchozí auditovaný commit: `6d4b3ed778809204930f1151c2b211c91ea1df14` (lokální větev
`work`). Repository nemělo nakonfigurovaný remote ani lokální větev `main`; načtení novějšího
`origin/main` proto bylo `UNVERIFIED`. Auditovaný strom obsahoval merge PR #61 (M1), nikoli
samostatný merge PR #62. Změny tohoto re-auditu jsou popsány v Git commitu, který obsahuje tento
dokument.

## Scope

Byly přečteny aplikační moduly, Alembic migrace, API a security middleware, worker/scheduler,
Phase 3–9 testy, oba Dockerfile, production compose, CI, frontend server actions a operátorské
stránky. Repo bylo prohledáno na live brokery, credentials, order routing a legacy ekonomické
cesty. Historické dokumenty byly použity jen jako navigace; status níže vychází z implementace.

Úroveň evidence je vždy explicitní: `CODE INSPECTION PASS`, `UNIT TEST PASS`,
`POSTGRES ACCEPTANCE PASS`, `CONTAINER RUNTIME PASS`, `END-TO-END PASS` nebo `UNVERIFIED`.

## Evidence summary

| Evidence | Výsledek |
| --- | --- |
| Git SHA, historie a pracovní strom | `CODE INSPECTION PASS`; remote/main `UNVERIFIED` |
| Paper-only repository scan | `CODE INSPECTION PASS` po remediation; pouze `PaperBroker`/`PersistentPaperBroker` |
| Přímá legacy paper mutation | Nalezena na baseline, reprodukce `POST /demo/trading/cycles/run-paper`; odstraněna a kryta AST regresí |
| Python format/lint/type/unit | `UNVERIFIED`: požadované `uv==0.12.3` nebylo instalováno; registry vracela 403 |
| PostgreSQL acceptance a clean DB E2E | `UNVERIFIED`: PostgreSQL/Docker runtime nebyl v prostředí dostupný |
| Frontend lint/type/test/build | `UNIT TEST PASS`: 27 testů; produkční Next build dokončen |
| Security/dependency/secret/container scan | `UNVERIFIED` runtime; konfigurace CI obsahuje pip-audit, npm audit a Trivy secret/misconfig/image gates |
| `git diff --check` a samostatný Ruff audit změněných Python souborů | `CODE INSPECTION PASS` / lokální Ruff po opravě importu |

## Phase 1–9 findings

### Phase 1–3 — data, PIT universe a snapshot

`InstrumentRecord` je stabilní identita oddělená od symbolu a nese exchange, calendar, currency a
asset type. Ingestion persistuje provider/version, interval, stav a revision evidence; runtime
dotazy filtrují úspěšné ingestion. Membership používá `valid_from`, `valid_to` a `known_at` a
runtime ji řeže `decision_time`. Snapshot service vytváří manifest/content hash a experiment
validuje jeho evidence. Stav: `CODE INSPECTION PASS`; tamper a PostgreSQL concurrency assertions
existují v Phase 3/6 suites, jejich běh zde je `UNVERIFIED`.

Corporate-action capability/readiness váže provider identity/version, interval a knowledge
cutoff. Runtime vyžaduje přesnou readiness a filtruje actions podle `known_at <= decision_time`.
Chybějící readiness končí výjimkou před Phase 4. Stav: `CODE INSPECTION PASS`; H2 acceptance zde
`UNVERIFIED`.

### Phase 4 — risk, execution, ledger a reconciliation

Autoritou ekonomického efektu je `TradingCycleService` přes production risk engine a persistentní
paper broker. Cycle fingerprint, databázový lease a order/fill identity chrání retry; fill vede k
cash/position evidence a následné reconciliation. Vstupy NaN/infinity a neplatné hodnoty odmítají
Decimal/Pydantic/doménové validace. Baseline ale vystavoval veřejně routovanou ADMIN mutation,
která přímo volala tuto službu nad CSV fixture bez deployment/monitoring autority. To byl P0 a
byl v tomto re-auditu odstraněn. Corruption/reconciliation runtime: `UNVERIFIED`.

### Phase 5 — automation, concurrency a recovery

Scheduler persistuje occurrence; claim používá podmíněný PostgreSQL update, lease owner/expiry a
fencing. Worker přijímá deployment ID a rekonstruuje ekonomickou konfiguraci server-side.
Heartbeat, attempts, retry a dead letter jsou persistentní a čitelné API. Stav:
`CODE INSPECTION PASS`; duplicate-worker a expired-lease PostgreSQL testy existují, tento běh
`UNVERIFIED`.

### Phase 6 — experiment, eligibility, promotion, deployment

Experiment váže snapshot, strategy/version, parametry, chronologický train/validation/OOS split,
seed, code SHA a cost model. OOS se nepoužívá pro výběr kandidáta. M1 používá server-side policy
registry, kanonické dokumenty, rules, metrics a integrity hash; promotion znovu validuje přesnou
lineage a immutable decision. Deployment lze vytvořit jen z `PAPER_CANDIDATE`.
Stav: `CODE INSPECTION PASS`; replay a M1 adversarial PostgreSQL běh `UNVERIFIED`.

### Phase 7 — approval, monitoring a performance

Approval je ADMIN server mutation, nikoli boolean klienta, a validuje lineage i runtime manifest.
Execution vyžaduje právě jeden `ACTIVE` monitoring record a stav znovu čte před ekonomickým
krokem. OOS baseline a realized paper snapshots jsou oddělené tabulky. Stav:
`CODE INSPECTION PASS`; race transition/execution a performance acceptance `UNVERIFIED`.

### Phase 8 — operator UI

UI je server-rendered a mutations používají server actions, same-origin kontrolu, server session
a backend bearer token podle role. Implementuje eligibility, promotion, risk, monitoring a
autonomous toggle. Neimplementuje však požadovaný kompletní create workflow pro instrument,
universe, ingestion, snapshot, experiment, deployment, approval a enrollment; data/research jsou
v těchto částech read-only. Stav: **M4 REGRESSED / P2**. Pilot lze řídit autoritativním API, ale
nikoli celý běžný tok pouze UI.

### Phase 9 — security a operations

Bearer identity vzniká server-side, GET vyžaduje VIEWER, všechny mutations ADMIN kromě HALT
(OPERATOR). Host allowlist, response no-store, correlation ID, rate limiting a production secret
validace failují closed. Bearer API nepoužívá cookie auth; CSRF se proto na backendu neopírá o
cookie ambient authority, zatímco frontend server actions kontrolují origin. Containers deklarují
non-root/read-only/cap-drop/no-new-privileges a oddělenou interní data síť. Stav:
`CODE INSPECTION PASS`; production smoke a skeny `UNVERIFIED`.

## Previous remediation verification

| Finding | Status | Evidence |
| --- | --- | --- |
| B1 | RESOLVED | Control-plane API vytváří registry → snapshot → experiment → deployment evidence |
| B2 | RESOLVED | Worker ekonomicky začíná autoritativním approved deployment ID |
| B3 / P0-A | RESOLVED | Decision time a následující raw open jsou validovány; missed open failuje closed |
| P0-B | RESOLVED | Worker entrypoint, compose service, heartbeat, lease a CI runtime acceptance existují |
| H1 | RESOLVED | Autonomous session preparation provádí allowlisted refresh a staleness gate |
| H2 | RESOLVED | Capability/readiness/knowledge cutoff jsou persistentní a execution gate je fail-closed |
| H3 | RESOLVED | Kanonický approved runtime manifest/hash se před worker execution znovu ověřuje |
| M1 | RESOLVED | Immutable policy decision a promotion revalidation jsou server-side autorita |
| M2 | RESOLVED WITH P2 GAP | Klíčové control mutations nesou actor/reason/result/correlation; legacy non-operator mutations nejsou všechny reasoned |
| M3 | RESOLVED | Backend XNYS calendar řeší sessions; existují DST/holiday/early-close testy |
| M4 | REGRESSED | UI neumí celý požadovaný create/approve/enroll workflow; API jej umí |

## New findings

| ID | Severity | Finding | Evidence | Pilot blocker |
| --- | --- | --- | --- | --- |
| RA-P0-01 | P0 | Legacy fixture endpoint obcházel deployment, approval, runtime manifest, monitoring a current-data causal gate | Baseline `api.py` route přímo volala `trading_service.run` nad fixture | Ano na baseline; **opraveno** odstraněním route |
| RA-P2-01 | P2 | M4 UI není kompletní operátorský workflow | Data/research stránky jsou read-only; chybí create deployment/approval/enrollment UI | Ne při API runbooku a lidském dohledu |
| RA-P2-02 | P2 | Aktuální runtime/PostgreSQL/container důkaz nebylo možné v tomto prostředí obnovit | Docker/PostgreSQL chybí; uv pin nelze stáhnout kvůli 403 | Ne jako kódový blocker; před pilotem povinná CI podmínka |
| RA-P3-01 | P3 | Některé legacy ADMIN mutations nemají reasoned audit contract | `/reconciliation/run`, demo backtest/research a obecná automation API | Ne; pro pilot používat operator control plane |

## RBAC matice `/operator/...`

Všechny řádky procházejí globálním middlewarem. `Actor` je `request.state.principal`, nikoli pole
requestu. GET nevyžaduje reason; každá uvedená control mutation jej vyžaduje v Pydantic body.

| Endpoint | Method | Viewer | Admin | Actor | Reason | Backend authority |
| --- | --- | --- | --- | --- | --- | --- |
| `/operator/overview`, `/paper`, `/paper/performance` | GET | ano | ano | server | N/A | read model |
| `/operator/monitoring/{id}/comparison` | GET | ano | ano | server | N/A | monitoring read model |
| `/operator/strategies[/​{id}]` | GET | ano | ano | server | N/A | registry read model |
| `/operator/research/experiments[/​{id}]` | GET | ano | ano | server | N/A | research read model |
| `/operator/research/experiments/{id}/eligibility` | GET | ano | ano | server | N/A | M1 decision store |
| `/operator/risk`, `/data-health`, `/automation`, `/audit` | GET | ano | ano | server | N/A | respective read model |
| `/operator/instruments` | POST | ne | ano | server | ano | registry service |
| `/operator/universes`, `/universes/{id}/memberships` | POST | ne | ano | server | ano | registry service |
| `/operator/market-data/ingestions`, `/datasets` | POST | ne | ano | server | ano | data/snapshot service |
| `/operator/research/experiments` | POST | ne | ano | server | ano | Phase6 runner |
| `/operator/research/experiments/{id}/eligibility`, `/promote` | POST | ne | ano | server | ano | M1 service |
| `/operator/deployments`, `/deployments/{id}/approve` | POST | ne | ano | server | ano | deployment service |
| `/operator/deployments/{id}/jobs`, `/autonomous/enable`, `/autonomous/disable` | POST | ne | ano | server | ano | automation repository |
| `/operator/monitoring/policies`, `/monitoring/enrollments` | POST | ne | ano | server | ano | monitoring service |
| `/operator/risk/halt` | POST | ne | ano (i operator) | server | ano | risk service |
| `/operator/risk/resume` | POST | ne | ano | server | ano | risk/reconciliation gate |

## E2E evidence

Clean PostgreSQL cesta je implementována v CI jako Alembic `upgrade head`, Phase 3–9 integration
suites, čistý schema reset pro B1 a M1 a samostatné B1/B2/P0/H2/H3/Stage-C acceptance kroky.
Assertions v klíčových testech kontrolují vznik experiment/deployment/job/cycle/order/fill,
idempotentní retry, monitoring lineage a fail-closed scénáře. V tomto auditu však nebyl dostupný
PostgreSQL server ani Docker daemon: **clean DB E2E = UNVERIFIED**, nikoli PASS.

Před pilotem se musí na výsledném SHA uložit zelený CI run a operátor musí provést staging dry-run
podle runbooku. Přímé SQL inserty mimo fixture bootstrap nejsou povoleny.

## Failure tests

| Scénář | Expected behavior | Observed evidence |
| --- | --- | --- |
| Provider unavailable | retry/dead-letter, bez fillu | CODE INSPECTION PASS; runtime UNVERIFIED |
| Incomplete/failed/stale ingestion | data accessor odmítne execution | CODE INSPECTION PASS; runtime UNVERIFIED |
| Missing corporate readiness | fail closed před signal/fill | CODE INSPECTION PASS; H2 runtime UNVERIFIED |
| Corrupted snapshot | hash/manifest validation error | CODE INSPECTION PASS; tamper runtime UNVERIFIED |
| Eligibility/policy/metrics tamper | promotion conflict | CODE INSPECTION PASS; M1 runtime UNVERIFIED |
| Promotion bez decision | konflikt, žádný candidate | CODE INSPECTION PASS; runtime UNVERIFIED |
| Manifest/approval lineage mismatch | approval/execution odmítnuta | CODE INSPECTION PASS; H3 runtime UNVERIFIED |
| Monitoring inactive | no-action nebo exception, bez fillu | CODE INSPECTION PASS; runtime UNVERIFIED |
| Scheduler retry / duplicate worker | jedna occurrence, fenced lease | CODE INSPECTION PASS; PostgreSQL runtime UNVERIFIED |
| Missed open | žádný retroaktivní fill | CODE INSPECTION PASS; P0 acceptance runtime UNVERIFIED |
| DB interruption/restart | rollback nebo expired-lease takeover | CODE INSPECTION PASS; runtime UNVERIFIED |
| Legacy direct cycle | route nesmí existovat | Remediation CODE INSPECTION PASS + AST regression připravena |

## Security

Nebyl nalezen broker SDK, exchange order API, funding/withdrawal flow, private key ani hardcoded
production credential. Stooq je read-only market-data provider. `.env.production` není commitnutý
a compose používá secret file. Git history secret scan, dependency CVE audit a image scan jsou v
tomto prostředí `UNVERIFIED`; CI je však má jako blocking kroky bez `continue-on-error`.

Auth negativní očekávání z middleware: bez tokenu `401`, viewer mutation `403`, neplatný Host
`400`, malformed body `422`. Origin obrana je v server action vrstvě; přímý bearer klient nemá
ambient cookie credential. Input model eligibility zakazuje extra fields; ostatní významné modely
mají délkové/enum/numerické meze, ale nejsou všechny `extra=forbid` (P3 hardening).

## CI audit

CI má minimální `contents: read`, nepoužívá `continue-on-error` a samostatně blokuje quality,
unit/API, PostgreSQL integration, frontend, security, image build/scan a production smoke.
B1, B2, missed-open P0, production-worker P0, H2, H3 a M1 mají explicitní acceptance commands.
M4 má pouze obecné frontend test/build pokrytí a potřebuje budoucí workflow-level UI test;
pro API-řízený omezený pilot to není blocker.

## Operational constraints

**PILOT JE POVOLEN POUZE ZA PODMÍNEK:**

1. výsledný commit má zelené všechny CI jobs včetně clean PostgreSQL acceptance, Trivy a smoke;
2. PAPER ONLY, XNYS, USD equities, daily timeframe, jeden aktivní deployment a jeden worker;
3. pouze autoritativní `/operator` workflow; žádné demo/legacy mutations;
4. ACTIVE monitoring, čerstvá successful ingestion a complete corporate-action readiness před runem;
5. denní lidská kontrola heartbeat, latest run/attempt, fills a reconciliation;
6. při stale heartbeat, FAILED/DEAD_LETTER, mismatch nebo nejasném fillu okamžitý HALT;
7. žádné bezobslužné konfigurační změny a žádné paralelní scheduler deploymenty.

## Residual risks

Hlavní residual risk je chybějící lokálně zopakovaný runtime důkaz, nikoli známý otevřený P0/P1.
UI nepokrývá celý workflow a operátor musí používat zdokumentované API. Process-local rate limiter
je přijatelný pouze při single-backend pilot topology. Provider Stooq nemá SLA; failure musí zůstat
no-fill a být denně kontrolován.

## Final recommendation

Po odstranění `RA-P0-01` nejsou z code inspection známé otevřené P0/P1. Pilot lze spustit pouze
po zeleném CI výsledného commitu a úspěšném staging clean-DB dry-runu, v úzkém scope a podle
`docs/paper-pilot-runbook.md`. Pokud kterýkoli povinný gate není zelený, rozhodnutí se automaticky
mění na **NOT READY FOR PAPER PILOT**.

# MŮŽEME AUTONOMOUS QUANT LAB PUSTIT DO ŘÍZENÉHO PAPER TESTOVACÍHO PROVOZU?

## ANO, ALE POUZE ZA TĚCHTO PODMÍNEK

- zelený CI a clean PostgreSQL staging E2E na výsledném SHA;
- jeden XNYS/USD/daily paper deployment a jeden worker pod denním dohledem;
- ACTIVE monitoring, fresh data, reconciliation SAFE a okamžitý HALT při odchylce.
