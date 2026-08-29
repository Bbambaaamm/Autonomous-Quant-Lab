# Paper pilot runbook

Tento runbook je výhradně pro interní PAPER provoz. **Aktuální stav repository je NOT READY FOR PAPER PILOT**, protože production `StooqProvider` nemá corporate-action capability (`supports_actions=False`). Následující postup je bezpečnostní runbook pro staging a pro budoucí pilot až po uzavření tohoto blockeru. Nezakládá ani nepovoluje live režim.

## 1. Hard gates před pilotem

Pilot se nesmí spustit, pokud není současně splněno vše:

1. výsledný commit má zelené required CI jobs `quality`, `unit-research`, `api`, `integration-postgres`, `frontend`, `security`, `container-build` a `production-smoke`;
2. `live_trading_enabled=false` a repository neobsahuje podporovanou live execution cestu;
3. PostgreSQL, HTTPS ingress, persistentní volume, ověřený backup/restore a least-privilege runtime role jsou funkční;
4. backend i worker mají ověřený market-data egress, DB síť zůstává interní;
5. production provider pro pilotní equity scope skutečně deklaruje a E2E prokazuje corporate-action capability;
6. corporate-action readiness je `READY` pro celý požadovaný interval — `UNSUPPORTED` není green stav;
7. monitoring enrollment má právě jeden validní enabled `MONITOR_PAPER_DEPLOYMENT` schedule;
8. autonomous orchestration je ukotvená na XNYS open, ne na náhodnou fázi času enable;
9. FAILED/DEAD_LETTER recovery je dostupná pouze přes auditovaný operator control-plane s reason/actor/correlation evidence;
10. proběhl production-like staging dry-run níže.

Dokud bod 5 není splněn, **nepokračujte k autonomous equity pilotu**.

## 2. Production-like topology

Požadované síťové oddělení:

- `data` — interní PostgreSQL síť;
- `application` — interní frontend ↔ backend komunikace;
- `market-data-egress` — explicitní neinterní egress pouze pro backend a worker;
- `ingress` — neinterní frontend síť pro host loopback publish.

Docker egress síť sama neomezuje DNS hostname. Aplikační provider transport proto musí dál fail-closed dovolovat pouze očekávané HTTPS hosty; pro Stooq je to pouze `https://stooq.com` bez credentials, custom portu nebo redirectu mimo allowlist.

Po nasazení vždy proveďte staging provider-egress smoke z backendu i workeru. Úspěšný container start není důkazem externí market-data konektivity.

## 3. Migration a start

1. Nastavte production-like environment mimo Git: production/staging DB URL, runtime URL, unikátní tokeny, `APP_ENV=production`, allowlisted host/origin a `AUTOMATION_ENABLED=true`.
2. Uložte DB secret mimo Git s minimálními filesystem právy.
3. Spusťte Alembic migrace migrator rolí.
4. Aplikujte runtime grants a ověřte, že runtime role nemůže měnit immutable evidence.
5. Proveďte DB backup a ověřte checksum/restore proces.
6. Sestavte production images a spusťte stack.
7. Ověřte HTTPS `/healthz`, `/readyz`, login a RBAC.
8. Ověřte fresh worker + scheduler heartbeat.
9. Ověřte skutečný market-data egress z backendu i workeru.

## 4. Supported operator workflow

Používejte pouze operator UI a `/operator/...` control-plane. Nepoužívejte direct SQL ani demo execution cesty.

`DATA → UNIVERSE → SNAPSHOT → EXPERIMENT → ELIGIBILITY → PROMOTION → DEPLOYMENT → APPROVAL → MONITORING → AUTONOMOUS PAPER`

Postup:

1. Zaregistrujte instrumenty.
2. Vytvořte universe a PIT memberships (`known_at`, `valid_from`, `valid_to`).
3. Proveďte ingestion a ověřte `SUCCEEDED`, provider identity/version a latest completed XNYS session.
4. Ověřte skutečnou provider capability pro corporate actions. Pokud provider hlásí `supports_actions=False`, **STOP — NOT READY**.
5. Ověřte complete corporate-action readiness pro požadovaný interval.
6. Vytvořte VALID immutable dataset snapshot a zaznamenejte snapshot/content hash.
7. Spusťte experiment s explicitním code SHA, seed, cost model a chronologickým splitem.
8. Proveďte eligibility evaluation a zkontrolujte immutable policy/evidence.
9. Pouze `ELIGIBLE` experiment promujte na `PAPER_CANDIDATE`.
10. Vytvořte deployment pro `paper-main`.
11. Zkontrolujte runtime manifest a hash.
12. Deployment explicitně schvalte s audit reason.
13. Vytvořte monitoring policy a enrollment.
14. Ověřte stav `ACTIVE` a oddělenou OOS baseline.
15. Ověřte právě jeden deterministický `MONITOR_PAPER_DEPLOYMENT` job a že je `enabled=true` s očekávanou konfigurací.
16. Opakovaný enrollment stejného ACTIVE runu musí stejný monitoring schedule idempotentně ensure-nout; nesmí vzniknout druhý monitoring job.
17. Teprve potom povolte autonomous scheduling.

## 5. Monitoring schedule invariant

Enrollment a automation job nemusí vznikat v jedné DB transakci; runbook proto netvrdí transakční atomicitu. Bezpečnostní pravidlo je fail-closed:

- deterministická job identity je odvozena z `monitoring_id`;
- existující disabled/drifted job musí být při supported enrollment ensure bezpečně normalizován/re-enabled, nebo musí endpoint selhat;
- autonomous enable musí znovu validovat, že ACTIVE monitoring má odpovídající enabled schedule;
- managed monitoring/autonomous/deployment jobs nesmí být měněny generic `/automation/jobs/*` mutation cestou;
- po `RETIRED` lze založit nový monitoring lifecycle; retired run se nereaktivuje jako nový enrollment.

## 6. XNYS autonomous scheduling

Autonomous orchestration musí být ukotvená na **09:30 America/New_York** a musí používat timezone-aware/DST-safe schedule. Kliknutí na enable nesmí vytvořit arbitrary 300sekundovou fázi.

Close-derived signal má economic execution intent připnutý na next-session XNYS open. Krátké bounded post-open okno slouží pouze k tomu, aby worker získal a ověřil raw opening print. `scheduled_for`/occurrence identity zůstává XNYS open.

Pokud worker nestihne bounded cutoff:

- výsledek musí být `MISSED_EXECUTION_OPEN` / no-action;
- nevytváří se retroaktivní fill;
- operátor nesmí missed run ručně backfillovat.

## 7. Daily operator check

Před očekávanou session ověřte:

- API/DB health;
- fresh worker a scheduler heartbeat;
- právě jeden enabled autonomous pilotní deployment;
- právě jeden ACTIVE monitoring run;
- právě jeden validní enabled monitoring schedule;
- autonomous next run odpovídá XNYS open;
- latest completed XNYS session;
- poslední ingestion `SUCCEEDED`;
- skutečný provider egress;
- corporate-action provider capability a complete readiness;
- account není HALTED;
- reconciliation je SAFE;
- žádný nevyřešený FAILED/DEAD_LETTER managed run;
- žádný runtime-manifest mismatch.

Po session zkontrolujte run/attempt, scheduled execution intent, raw open evidence, order/fill identity, risk decision, commission/slippage, cash/position delta, monitoring snapshot/evaluation, audit correlation a reconciliation.

## 8. Auditovaný FAILED/DEAD_LETTER recovery

Podporovaná recovery managed runu vede pouze přes reasoned ADMIN operator akci, např. `/operator/automation/runs/{run_id}/retry`.

Povinné vlastnosti:

1. endpoint přijme audit `reason`;
2. actor se odvodí ze server-side principalu;
3. correlation ID se persistuje do control audit evidence;
4. backend použije autoritativní retry state machine a povolí pouze podporované stavy;
5. frontend Operations nabídne retry jen jako auditovanou recovery akci;
6. UI neposkytuje economic `run-now` shortcut;
7. generic `/automation/runs/{run_id}/retry` není podporovaná pilotní recovery cesta pro managed jobs.

Před retry nejdřív opravte příčinu. Retry není náhrada za reconciliation ani způsob, jak obejít missed-open fail-closed.

## 9. Reconciliation recovery

Při mismatch:

1. účet zůstává HALTED/fail-closed;
2. odstraňte příčinu bez direct SQL ekonomických editací;
3. z operator control-plane spusťte autoritativní reconciliation s reason;
4. ověřte nový reconciliation record a `SAFE`;
5. teprve potom použijte podporovaný `RESUME`;
6. ověřte actor/reason/correlation audit evidence.

## 10. Monitoring lifecycle recovery

- `ACTIVE` lze podle state machine pause/retire;
- `SUSPENDED` lze bezpečně resume pouze pokud backend gates dovolí přechod, jinak retire;
- po retirement se zakládá nový enrollment;
- monitoring stav se nikdy nepřepisuje direct SQL.

## 11. Emergency stop

Při duplicate/retroactive/unexplained fillu, reconciliation mismatch, stale workeru, nejasné lineage, runtime-manifest mismatch nebo provider/readiness problému:

1. `/operator/risk/halt` s důvodem;
2. disable autonomous deployment podporovanou operator akcí;
3. uchovat run/attempt/cycle/order/fill/audit evidence;
4. nic ekonomického neopravovat direct SQL;
5. klasifikovat incident.

Jakýkoli P0/P1 incident zastavuje pilot do opravy a nové acceptance.

## 12. Mandatory staging dry-run

Dry-run provádějte až s providerem, který pravdivě splňuje corporate-action capability.

1. clean migrations;
2. instrument + universe + PIT membership;
3. backend provider-egress smoke;
4. worker provider-egress smoke;
5. market-data ingestion;
6. corporate-action capability/readiness;
7. immutable snapshot;
8. experiment;
9. eligibility;
10. promotion;
11. deployment + runtime manifest review;
12. approval;
13. monitoring enrollment;
14. disable/re-enable regression monitoring jobu a ověření idempotentního ensure;
15. právě jeden enabled monitoring schedule;
16. autonomous enable;
17. next autonomous run = XNYS 09:30 `America/New_York`;
18. worker/scheduler heartbeat;
19. scheduled safe execution/no-action;
20. missed-open cutoff regression bez ekonomického efektu;
21. reconciliation;
22. vytvoření bezpečného FAILED/DEAD_LETTER staging scénáře a auditovaný operator retry;
23. pause/resume nebo HALT/reconcile/resume recovery bez direct DB editace.

Dry-run musí skončit bez duplicate/retroactive fillu, bez nevysvětleného reconciliation mismatch a s kompletní actor/reason/correlation evidence.

## 13. Pilot exit criteria

Po povolení pilotu musí být současně splněno:

1. 10 po sobě jdoucích XNYS sessions;
2. 100 % očekávaných occurrences má dohledatelný run/attempt nebo vysvětlený fail-closed no-action;
3. žádný duplicate fill;
4. žádný retroactive fill;
5. žádný nevysvětlený reconciliation mismatch;
6. žádný P0/P1 incident;
7. všechny fills mají deployment/runtime/risk/order/cycle/monitoring lineage;
8. alespoň jeden worker restart/lease recovery drill bez dvojitého ekonomického efektu;
9. alespoň jeden auditovaný FAILED/DEAD_LETTER recovery drill;
10. monitoring evidence je oddělená od research OOS baseline;
11. denní operator evidence je kompletní.

## 14. Zakázané postupy

- live broker nebo live routing;
- direct SQL ekonomické opravy;
- demo paper execution route;
- retroaktivní fill/backfill missed open;
- obcházení eligibility, promotion, approval, monitoring nebo risk;
- autonomous start s `supports_actions=False` pro equity scope;
- start se stale/missing data nebo incomplete readiness;
- generic mutation managed pilot jobs mimo podporovaný operator control-plane;
- generic unaudited DEAD_LETTER retry jako pilotní recovery.
