# Předstartovní forenzní re-audit

## Executive verdict

**CONDITIONAL READY FOR CONTROLLED PAPER PILOT**

Na kombinovaném stromu nejsou po nové kontrole známé otevřené P0/P1 blokery. Podmínkou startu
zůstává úspěšný staging dry-run a splnění provozních gateů v
`docs/paper-pilot-runbook.md`; jejich nesplnění automaticky mění verdikt na **NOT READY**.

## Auditovaný strom a důkazy

- PR #63 head: `6ed177a81d89fe2ac367e236435286dc31c7ad49`.
- Merge-base/current `main`: `2227aa5b36c14cec0a2482b98926843d2cccec99`, merge PR #62.
- Strom tedy obsahuje M4 operator-control-plane implementaci i všechny review opravy PR #62 a
  změny PR #63, zejména odstranění legacy paper execution route.
- GitHub Actions run **#435** na tomto headu skončil úspěšně pro `quality`, `unit-research`,
  `api`, `frontend`, `security`, `container-build`, `integration-postgres` a
  `production-smoke`. Tento běh je autoritativním runtime důkazem auditu.

Evidence je označena jako `CODE INSPECTION PASS`, `CI #435 PASS` nebo `OPERATIONAL GATE`.
Audit zahrnul aplikační služby, API/RBAC, migrace, scheduler/worker, Phase 3–9 a acceptance testy,
production kontejnery, frontend server actions/read models a historii PR #62.

## PAPER-only architektura

Repository scan potvrzuje pouze `PaperBroker`/`PersistentPaperBroker`; nebyla nalezena live role,
live endpoint ani live broker cesta. Ekonomický tok zůstává Strategy → Portfolio → RiskEngine →
ExecutionEngine → PaperBroker. Veřejná legacy mutation `POST /demo/trading/cycles/run-paper`, která
spouštěla fixture mimo deployment/monitoring autoritu, je odstraněna. AST regresní test ověřuje,
že se route nevrátí. Stav: **CODE INSPECTION PASS + CI #435 PASS**.

## Revalidace nálezů B1–M4

| Finding | Stav na kombinovaném stromu | Revalidovaný důkaz |
| --- | --- | --- |
| B1 | RESOLVED | Operator control plane vytváří instrument → PIT universe → ingestion → immutable snapshot → experiment → eligibility/promotion → deployment → monitoring; PostgreSQL acceptance v CI #435. |
| B2 | RESOLVED | Worker přijímá approved deployment ID a rekonstruuje konfiguraci server-side; nepřijímá klientský ekonomický payload. |
| P0-A | RESOLVED | Close-derived decision používá nejdříve next-session raw open; missed open failuje closed. |
| P0-B | RESOLVED | Production worker, heartbeat, lease/fencing, compose a smoke evidence prošly CI #435. |
| H1 | RESOLVED | Autonomous preparation provádí allowlisted refresh a staleness gate. |
| H2 | RESOLVED | Corporate-action capability/readiness a `known_at` cutoff jsou persistentní a fail-closed. |
| H3 | RESOLVED | Approval i execution znovu ověřují kanonický runtime manifest, hash a lineage. |
| M1 | RESOLVED | Eligibility je immutable server-side policy decision; promotion revaliduje přesný decision/hash. |
| M2 | RESOLVED | Operator mutations odvozují actor z principalu a ukládají reason, result a correlation evidence. |
| M3 | RESOLVED | Backend XNYS calendar pokrývá session, holiday, DST a early-close pravidla. |
| M4 | RESOLVED | UI a API pokrývají podporovaný create/approve/enroll/operate/recover workflow; PR #62 review opravy jsou přítomné a CI #435 prošlo. |

## M4 a review-remediation audit

### Úplný operátorský tok

Server actions používají serverovou session, same-origin kontrolu a role-specific bearer token.
ADMIN může vytvořit instrument, universe a PIT membership, ingestion a snapshot, experiment,
eligibility a promotion, deployment a approval, monitoring policy/enrollment a autonomous job.
Viewer zůstává read-only; HALT dovoluje OPERATOR/ADMIN, ostatní control mutations vyžadují ADMIN.
Identita aktéra nevzniká z request body. **CODE INSPECTION PASS + CI #435 PASS**.

### Bounded read models a identifikátory

Operator read models používají serverové limity/paginaci a dashboard již neprovádí N+1 deployment
fetch. Data view omezuje memberships. `universe_id` create contract dovoluje pouze URL-safe
znaky a frontend route segmenty jsou bezpečně kódované; ID se neskládá do neověřené cesty.
Auditní stránka zachovává filtry při stránkování a backend stabilně řadí shodné timestampy.
**CODE INSPECTION PASS + CI #435 PASS**.

### Monitoring scheduling a lifecycle

Enrollment je idempotentní pro aktivní záznam a atomicky zajistí plánovaný
`MONITOR_PAPER_DEPLOYMENT` job. Nevrací tedy úspěch bez monitorovacího schedule. Po `RETIRED` lze
bezpečně vytvořit nové enrollment; `SUSPENDED` záznam nabízí auditované recovery transitions.
Execution stále vyžaduje právě jeden `ACTIVE` monitoring record a před ekonomickým krokem jej
znovu načte. **CODE INSPECTION PASS + PostgreSQL CI #435 PASS**.

### Reconciliation, retry a worker recovery

UI recovery volá autoritativní `POST /operator/reconciliation/run`; nepřepisuje DB stav.
Reconciliation mismatch drží účet HALTED a RESUME vyžaduje nový `SAFE` výsledek. Occurrence,
cycle a order/fill identity zajišťují idempotenci; conditional claim, owner, expiry, heartbeat a
fencing brání souběžnému či starému workeru v dokončení. Expired lease může převzít právě nový
worker, bounded retry končí dead-letter a nezakládá novou ekonomickou identitu.
**CODE INSPECTION PASS + integration-postgres/production-smoke CI #435 PASS**.

### Audit semantics

Operator mutations zapisují server-derived actor, explicitní reason, entity/result a correlation
ID. Monitoring enrollment audituje jak enrollment, tak zajištění schedule; transition,
reconciliation, autonomous enable/disable, HALT/RESUME a výzkumný/deployment workflow jsou
dohledatelné. Recovery se nesmí provádět přímým SQL. **CODE INSPECTION PASS + CI #435 PASS**.

## Phase 1–9 souhrn

1. **Data/PIT:** stabilní instrument identity; ingestion status/provider/version; memberships
   řežené `decision_time`; adjusted signal data a raw executable OHLC jsou oddělené.
2. **Snapshot:** immutable manifest/content hash a tamper validace.
3. **Research:** chronologický split, seed/code SHA/cost model; OOS není použit k výběru.
4. **Risk/execution:** deterministic cycle fingerprint, persistentní risk decisions, paper ledger,
   reconciliation a zákaz přímé broker cesty.
5. **Automation:** persistent occurrence, bounded retry, conditional claim, lease/fencing,
   heartbeat a dead-letter evidence.
6. **Promotion/deployment:** server policy registry, immutable eligibility, exact lineage a
   kanonický approved runtime manifest.
7. **Monitoring:** oddělený OOS baseline a realized paper performance, ACTIVE gate a recovery
   state machine.
8. **Operator UI:** kompletní podporovaný workflow a recovery controls po M4/PR #62.
9. **Security/operations:** fail-closed production config, RBAC, no-store/correlation/rate limits,
   non-root read-only containers, oddělená data síť a minimální CI permissions.

Všechny oblasti jsou **CODE INSPECTION PASS + CI #435 PASS**. Audit nezjistil nové P0/P1/P2.

## Failure-mode kontrola

| Scénář | Očekávaný a ověřený výsledek |
| --- | --- |
| unavailable/partial/stale provider data | bounded retry nebo no-action; žádný fill |
| missing corporate readiness | fail closed před signal/fill |
| snapshot/policy/manifest tamper | validation conflict; žádný deployment/fill |
| inactive/suspended monitoring | execution odmítnuta; žádný fill |
| duplicate worker/retry | jedna occurrence a ekonomická identity; fenced stale owner |
| missed executable open | žádný retroaktivní fill |
| reconciliation mismatch | HALT; RESUME až po authoritative SAFE reconciliation |
| worker/DB restart | rollback nebo lease-expiry takeover bez duplicate effect |
| legacy direct paper cycle | route chybí a AST regression brání návratu |

Runtime assertions pro tyto vrstvy jsou součástí úspěšných relevantních jobů CI #435.

## Pilotní podmínky a residual risk

CI #435 odstraňuje dřívější nejistotu o PostgreSQL, kontejnerech, security a production smoke.
Residual risk je nyní provozní: externí market-data provider nemá SLA, pilot zatím nemá
dlouhodobou historii a rate limiter je process-local. Proto je scope omezen na jednu backend
instanci, jeden scheduler/worker, jeden XNYS/USD/daily deployment a aktivní lidský dohled.

Před prvním ekonomickým během musí operátor provést staging dry-run podle runbooku, ověřit fresh
heartbeat/data, complete corporate-action readiness, ACTIVE monitoring a SAFE reconciliation.
Jakákoli odchylka, P0/P1 nebo nejasný fill znamená okamžitý HALT a verdikt **NOT READY**.

## Final recommendation

Kombinovaný head `6ed177a81d89fe2ac367e236435286dc31c7ad49` včetně M4/PR #62 a
odstranění legacy route je **CONDITIONAL READY FOR CONTROLLED PAPER PILOT**. Podmíněnost se týká
výhradně staging/provozních gateů, nikoli chybějícího CI či známého kódového P0/P1. Live trading,
rozšíření scope ani obejití operator workflow nejsou povoleny.
