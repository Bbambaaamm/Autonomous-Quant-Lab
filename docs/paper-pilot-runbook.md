# Paper pilot runbook

Tento runbook je pouze pro interní PAPER provoz. **Aktuální stav repository je NOT READY FOR PAPER PILOT.** Nezakládá ani nepovoluje live režim.

Obě dříve chybějící capability (production Alpaca v5 corporate-action evidence a immutable pre-open economic intent) jsou implementované. Hard blockerem spuštění je nyní chybějící zelený required CI a production-like PostgreSQL/staging acceptance výsledného head SHA.

## 1. Hard gates před pilotem

Pilot se nesmí spustit, dokud není současně splněno vše:

1. výsledný commit má zelené required CI jobs `quality`, `unit-research`, `api`, `integration-postgres`, `frontend`, `security`, `container-build` a `production-smoke`;
2. `live_trading_enabled=false` a neexistuje podporovaná live broker/exchange execution cesta;
3. PostgreSQL, HTTPS ingress, persistentní volume, backup/restore a least-privilege runtime role jsou ověřené;
4. backend i worker mají production-like market-data egress a DB síť zůstává interní;
5. production provider pro equity scope skutečně podporuje corporate actions a capability je E2E ověřena;
6. corporate-action readiness je `READY` pro celý požadovaný interval;
7. monitoring enrollment má právě jeden validní enabled canonical `MONITOR_PAPER_DEPLOYMENT` schedule;
8. autonomous PREPARE schedule je DST-safe ukotvený na 09:00 `America/New_York` a žádný legacy/drifted schedule není enabled;
9. před 09:30 existuje immutable auditovatelný economic/order intent, jehož side/quantity nemůže záviset na current-session opening printu;
10. FAILED/DEAD_LETTER recovery pro managed jobs je auditovaná a atomická;
11. proběhl celý production-like staging dry-run.

Dokud všechny body včetně CI a production-like acceptance nejsou doloženy pro nasazovaný SHA, **nepokračujte k autonomous equity pilotu**.

## 2. Production-like topology

Požadované síťové oddělení:

- `data` — interní PostgreSQL síť;
- `application` — interní frontend ↔ backend komunikace;
- `market-data-egress` — explicitní egress pro backend a worker;
- `ingress` — frontendová síť pro host loopback publish.

Docker egress není hostname firewall. Provider transport musí samostatně fail-closed allowlistovat očekávané HTTPS endpointy. Pro Stooq je povolen pouze očekávaný `https://stooq.com` transport bez credentials, custom portu a redirectu mimo allowlist.

Po nasazení proveďte skutečný provider-egress smoke z backendu i workeru. Healthy container není důkaz externí konektivity.

## 3. Migration a start

1. Nastavte production-like environment mimo Git, včetně DB/runtime URL, unikátních tokenů, `APP_ENV=production`, allowlisted host/origin a `AUTOMATION_ENABLED=true`.
2. Uložte DB secret mimo Git s minimálními filesystem právy.
3. Spusťte Alembic migrace migrator rolí.
4. Aplikujte runtime grants.
5. Vytvořte DB backup a ověřte restore proceduru.
6. Sestavte production images a spusťte stack.
7. Ověřte HTTPS `/healthz`, `/readyz`, login a RBAC.
8. Ověřte fresh worker/scheduler heartbeat.
9. Ověřte provider egress.
10. Při worker startupu ověřte, že legacy/drifted autonomous schedules byly fail-closed vypnuty před prvním scheduler tickem.

## 4. Supported operator workflow

Používejte pouze operator UI a `/operator/...` control-plane. Nepoužívejte direct SQL ani demo execution cesty.

`DATA → UNIVERSE → SNAPSHOT → EXPERIMENT → ELIGIBILITY → PROMOTION → DEPLOYMENT → APPROVAL → MONITORING → AUTONOMOUS PAPER`

Před autonomous enable musí být ověřeno:

- APPROVED deployment a immutable runtime manifest;
- ACTIVE monitoring run;
- právě jeden canonical enabled monitoring schedule;
- production corporate-action capability + readiness;
- canonical autonomous schedule;
- pre-open immutable economic/order intent capability.

Tyto gates musí být ověřeny nad konkrétním nasazovaným SHA; samotná existence implementace není provozní důkaz.

## 5. Monitoring invariant

Enrollment a scheduler job nemusí vznikat v jedné DB transakci. Bezpečnost je fail-closed:

- job ID je deterministický z `monitoring_id`;
- ensure ověří identity/account/config a normalizuje `enabled`, schedule type, interval, timezone, misfire policy, grace, max attempts a due time;
- autonomous enable znovu validuje celý canonical schedule;
- generic `/automation/jobs/*` mutations nesmí měnit managed PAPER jobs;
- přechod monitoringu do `RETIRED` v lifecycle transakci vypne monitoring schedule i autonomous deployment schedule;
- retired monitoring se znovu neaktivuje; nový lifecycle vyžaduje nový enrollment.

## 6. XNYS scheduling a execution causalita

Autonomous PREPARE schedule je ukotvený na `09:00 America/New_York` a je DST-safe; připravený execution occurrence zůstává ukotvený na skutečný XNYS open.

Strict executable-open cutoff zůstává `[open, open + 1 second)`. `PREPARE_PAPER_SESSION` před open persistuje target delta vypočtený z causal adjusted signal close a teprve poté materializuje execution occurrence. Proto platí:

Production worker rezervuje nejbližší materializované XNYS occurrence před běžnou frontou a čeká přímo do jejího `scheduled_for`; pětisekundový obecný polling tedy není dispatch mechanismem executable-open běhu. Provider response time se měří živými UTC hodinami a odpověď získaná po cutoff failuje jako `MISSED_EXECUTION_OPEN` bez fillu.

- execution bez pre-open intentu vrátí `NO_ACTION / PREOPEN_EXECUTION_INTENT_NOT_PERSISTED`;
- pozdní nebo porušený intent vrátí `NO_ACTION / PREOPEN_EXECUTION_INTENT_INVALID`;
- raw opening print se nesmí použít k vytvoření nového ekonomického intentu;
- validní `xnys:` run smí raw open použít jen pro risk, kapacitu a fill, nikoli pro side/quantity;
- persisted-intent execution načítá raw open pro všechny intent instrumenty a všechny držené instrumenty potřebné pro portfolio/risk marking; zero-delta neheld člen universe raw open nevyžaduje;
- execution-time risk decision používá skutečný open-time knowledge čas, nikdy previous-session signal close;
- risk equity a nový `session_start_equity` se markují z cash a raw-open cen všech held instrumentů; stale ledger equity není risk denominator;
- corporate-action readiness pre-open intentu končí execution session a používá skutečný PREPARE decision cutoff, zatímco strategy signal history zůstává omezena prior-session causal cutoffem;
- persisted-intent open cesta znovu nenačítá signal history ani negeneruje target portfolio; ekonomické strategy rozhodnutí proběhlo výhradně v PREPARE a open runtime pouze validuje a vykonává immutable intent;
- žádný backdating `scheduled_for` nevytváří pre-open objednávku;
- po cutoff se nikdy nedělá retroaktivní fill ani ruční backfill.

Intent record je append-only a nese deployment/account/strategy/session identitu, decision a persistence čas, sizing reference, snapshot/universe/signal-observation lineage a integritní hash. Execution jeho ekonomická pole nemění.

## 7. Auditovaný FAILED/DEAD_LETTER recovery

Managed run lze retry pouze reasoned ADMIN operator akcí `/operator/automation/runs/{run_id}/retry`.

Povinné vlastnosti:

1. run je `FAILED` nebo `DEAD_LETTER` a patří managed PAPER jobu;
2. actor je odvozen ze server-side principalu;
3. reason je povinný;
4. correlation ID je persistovaný;
5. změna na `RETRY_SCHEDULED` a `CONTROL_AUTOMATION_RUN_RETRY` audit event commitnou v jedné DB transakci;
6. při selhání auditu nesmí retry transition commitnout;
7. generic `/automation/runs/{run_id}/retry` managed run odmítá;
8. Operations UI nabízí recovery jen managed FAILED/DEAD_LETTER runům a nemá economic `run-now` shortcut.

Retry nikdy nesmí sloužit k obejití causal/open/readiness gate.

## 8. Daily operator check

Před session ověřte:

- API/DB health a fresh worker/scheduler heartbeat;
- žádný enabled legacy/drifted autonomous schedule;
- právě jeden ACTIVE monitoring run a canonical enabled monitoring schedule;
- provider egress;
- corporate-action capable production provider a complete readiness;
- účet ACTIVE a reconciliation SAFE;
- žádný unresolved managed FAILED/DEAD_LETTER run;
- žádný runtime-manifest mismatch;
- existenci validního pre-open immutable intentu pro ekonomický autonomous běh.

Pokud poslední bod není splněn, očekávaným bezpečným výsledkem je `PREOPEN_EXECUTION_INTENT_NOT_PERSISTED`, nikoli fill.

## 9. Reconciliation a monitoring recovery

Při reconciliation mismatch účet zůstává HALTED/fail-closed. Příčinu opravte bez direct SQL ekonomických editací, spusťte autoritativní reconciliation, ověřte `SAFE` a teprve potom použijte podporovaný `RESUME` s audit reason.

Monitoring lifecycle:

- `ACTIVE` lze pause/retire podle state machine;
- `SUSPENDED` lze resume pouze při splnění backend gates, jinak retire;
- `RETIRED` je terminální a vypíná jeho managed schedules;
- nový lifecycle po retirement vyžaduje nový enrollment.

## 10. Emergency stop

Při duplicate/retroactive/unexplained fillu, reconciliation mismatch, stale workeru, lineage mismatch nebo provider/readiness problému:

1. `/operator/risk/halt` s důvodem;
2. podporovaně disable autonomous deployment;
3. uchovat run/attempt/cycle/order/fill/audit evidence;
4. nepoužívat direct SQL ekonomické opravy;
5. klasifikovat incident.

Jakýkoli P0/P1 incident zastavuje pilot do opravy a nové acceptance.

## 11. Mandatory staging dry-run

Dry-run má smysl jako kandidát na pilot acceptance až po uzavření obou současných hard blockerů: production corporate actions a pre-open immutable intent.

Musí ověřit minimálně:

1. clean migrations a runtime grants;
2. backend + worker provider-egress smoke;
3. ingestion a corporate-action capability/readiness;
4. research → eligibility → promotion → deployment → approval;
5. monitoring enrollment a full canonical schedule ensure/re-enable;
6. restart workeru s legacy 300s schedule fixture a ověření fail-closed disable před scheduler tickem;
7. autonomous next schedule odpovídá XNYS 09:30 přes DST;
8. fully-specified immutable pre-open order intent existuje před open;
9. opening print nemění side/quantity;
10. missed-open/cutoff vytváří no-action bez ekonomického efektu;
11. managed FAILED/DEAD_LETTER atomic retry + audit evidence;
12. monitoring `RETIRED` vypne monitor i autonomous schedule;
13. reconciliation SAFE a žádný duplicate/retroactive fill.

## 12. Zakázané postupy

- live broker nebo live routing;
- direct SQL ekonomické opravy;
- demo paper execution route;
- retroaktivní fill/backfill;
- vytvoření side/quantity až po znalosti opening printu;
- obcházení eligibility, promotion, approval, monitoring, risk nebo provider readiness;
- autonomous equity start s `supports_actions=False`;
- generic mutation managed pilot jobs;
- generic unaudited retry managed runu;
- změna verdictu na READY pouze na základě syntetických testů.
