# M4 remediation — operator control-plane UI

## Původní problém

Backend měl autoritativní Phase 6/7 control-plane, ale web byl převážně read-only. Běžný operátor
proto musel pro bootstrap dat, eligibility, promotion, deployment, monitoring a autonomní plánování
používat přímé API volání. M4 sjednocuje tyto existující PAPER-only operace do jednoho UI workflow.

## Podporovaný workflow

Control Center ukazuje data readiness, poslední research evidence, deploymenty, immutable runtime
identity, monitoring a worker/scheduler evidence. Stránka Data vede ADMIN uživatele přes registraci
instrumentu, point-in-time universe membership, Stooq ingest a immutable snapshot. Research spouští
chronologický experiment; detail experimentu odděluje eligibility evaluation, explicitní promotion
a vytvoření paper deploymentu. Detail strategie nabízí schválení, monitoring enrollment a backendem
plánované autonomous ON/OFF. Monitoring detail zpřístupňuje pouze existující ACTIVE/PAUSED/RETIRED
transitions.

## UI → autoritativní backend

| UI krok | Endpoint / autorita |
| --- | --- |
| instrument, universe, membership | `POST /operator/instruments`, `/operator/universes`, `/operator/universes/{id}/memberships` |
| ingest, snapshot | `POST /operator/market-data/ingestions`, `/operator/datasets` |
| experiment | `POST /operator/research/experiments` (`Phase6ExperimentRunner`) |
| eligibility, promotion | `POST /operator/research/experiments/{id}/eligibility`, `/promote` (`Phase6EligibilityService`) |
| deployment, approval | `POST /operator/deployments`, `/operator/deployments/{id}/approve` (`DeploymentService`) |
| monitoring | `POST /operator/monitoring/policies`, `/enrollments`, `/{id}/{pause|resume|retire}` (`PaperMonitoringService`) |
| autonomous | `POST /operator/deployments/{id}/autonomous/{enable|disable}` (`AutomationRepository`) |
| evidence | operator read models: overview, data-health, research, strategies, monitoring comparison, automation, audit |

Frontend neposílá metriky eligibility, thresholdy, decision, runtime manifest ani XNYS termíny. Tyto
hodnoty pouze zobrazuje z persistentního read modelu. Monitoring transition endpoint je tenký,
auditovaný operator adaptér nad existující Phase 7 state machine; nepřidává paralelní pravidla.

## RBAC, audit a bezpečnost

GET vyžaduje VIEWER. Všechny ekonomicky významné mutace nadále vyžadují backend ADMIN (výjimkou je
existující emergency HALT pro OPERATOR); UI podle server-side session aktivní admin formuláře viewerovi
ani operatorovi nezobrazí. Každá kritická operace sbírá `reason`; actor vzniká výhradně z backend bearer
identity. CSRF kontrola porovnává Origin/Host a tokeny zůstávají server-only.

Vlastní mutation formulář blokuje opakované odeslání, neprovádí optimistic state update a po odpovědi
revaliduje autoritativní read model. Bezpečná backend `detail` zpráva se zobrazí operátorovi; traceback
ani credentials se nepřenášejí.

## PAPER-only, evidence a lineage

Globální shell i kritické dialogové formuláře jednoznačně uvádějí PAPER ONLY. Neexistuje live role,
live broker ani live routing. UI ukazuje snapshot/content lineage, code SHA, eligibility policy a
rules, deployment runtime manifest hash/version, backend XNYS sessions, JobRun a monitoring baseline,
policy, performance a evaluation evidence. H2 readiness není klientem odhadována a provider capability
je součástí backend read modelu. M1, H2, H3 a M3 tedy zůstávají fail-closed na serveru.

## Automatizovaný důkaz

Frontend testy ověřují disabled kritickou akci, zákaz double-submit a zobrazení doménové chyby bez
falešného optimistic success. Existující B1 PostgreSQL acceptance spojuje data → snapshot → experiment
→ promotion → deployment → approval → monitoring; M1 testy dokazují immutable eligibility gate a
Stage C testy autonomous/XNYS orchestration. M4 tyto stejné endpointy pouze zpřístupňuje.
