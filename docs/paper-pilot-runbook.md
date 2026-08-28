# Paper pilot runbook

Tento runbook je výhradně pro interní, řízený PAPER pilot: XNYS, USD equities, denní bars, jeden aktivní deployment, jeden scheduler/worker a aktivní lidský dohled. Nezakládá ani nepovoluje live režim.

## 1. Deployment prerequisites

- Použijte pouze PR/commit se zelenými CI jobs `quality`, `unit-research`, `api`, `integration-postgres`, `frontend`, `security`, `container-build` a `production-smoke`.
- Připravte PostgreSQL 17, HTTPS ingress, persistentní volume, ověřený backup/restore a oddělenou least-privilege runtime roli podle `scripts/configure-runtime-role.sql`.
- Vygenerujte unikátní silné viewer/operator/admin tokeny a DB secret mimo Git.
- Nepoužívejte development `trust` compose pro pilot.
- Pilotní universe omezte na několik USD equities na XNYS a timeframe `1d`.
- Ověřte `live_trading_enabled=false` a absenci jakékoli live broker/exchange execution cesty.

## 2. Migration a environment

1. Nastavte production-like environment mimo Git: production/staging DB URL, runtime URL, tokeny, `APP_ENV=production`, allowlisted host/origin a `AUTOMATION_ENABLED=true`.
2. Uložte DB heslo do podporovaného secret mechanismu s minimálními filesystem právy.
3. Spusťte migrace migrator rolí:
   `cd backend && uv run alembic -c ../alembic.ini upgrade head`.
4. Aplikujte runtime grants a ověřte, že runtime role nemůže měnit immutable evidence.
5. Proveďte backup a test restore do oddělené databáze ještě před prvním deploymentem.

## 3. Start a health verification

1. Sestavte immutable images: `make production-build`.
2. Spusťte služby: `make production-up`.
3. Ověřte `GET /healthz` a autentizované `GET /readyz` přes HTTPS.
4. Ověřte frontend session/login a že VIEWER nemůže mutation.
5. Na `/operator/overview` ověřte PAPER-only režim.
6. Ověřte fresh worker heartbeat a scheduler readiness v operator UI/read modelu.
7. Pokud heartbeat chybí nebo je stale, nepokračujte.

## 4. První PAPER deployment — podporovaná M4 cesta

Preferujte operator UI. UI pouze ovládá autoritativní `/operator/...` backend control-plane; nepoužívejte direct SQL a nepoužívejte demo execution cesty.

Podporovaný workflow:

`DATA → UNIVERSE → SNAPSHOT → EXPERIMENT → ELIGIBILITY → PROMOTION → DEPLOYMENT → APPROVAL → MONITORING → AUTONOMOUS PAPER`

Postup:

1. Zaregistrujte instrumenty.
2. Vytvořte universe a PIT memberships (`known_at`, `valid_from`, `valid_to`).
3. Proveďte ingestion a ověřte `SUCCEEDED`, provider identity/version a poslední dokončenou XNYS session.
4. Ověřte complete corporate-action readiness pro požadovaný interval. `UNSUPPORTED` není safe/green readiness.
5. Vytvořte VALID immutable dataset snapshot a zaznamenejte snapshot/content hash.
6. Spusťte experiment s explicitním code SHA, seed, cost model a chronologickým split.
7. Proveďte eligibility evaluation; zkontrolujte policy ID/version, rule results, OOS metrics a integrity hash.
8. Pouze `ELIGIBLE` experiment explicitně promujte na `PAPER_CANDIDATE`.
9. Vytvořte deployment pro `paper-main`.
10. Zkontrolujte runtime manifest: experiment, snapshot, universe, strategy/version, parameters, risk, commission, slippage, volume fraction, code SHA a hash.
11. Deployment schvalte explicitní approval akcí s důvodem.
12. Vytvořte monitoring policy a enrollment.
13. Ověřte stav `ACTIVE` a oddělenou OOS baseline.
14. Ověřte, že enrollment automaticky zajistil právě jeden deterministický `MONITOR_PAPER_DEPLOYMENT` job pro daný monitoring run.
15. Zapněte autonomous scheduling přes podporovanou operator akci.

Každá kritická mutation musí nést reason; actor je server-side principal.

## 5. Idempotence a monitoring lifecycle

- Retry stejného monitoring enrollmentu nesmí vytvářet nekontrolované duplicitní monitoring jobs.
- Monitoring job identity je deterministická pro daný `monitoring_id`.
- `ACTIVE` monitoring lze přes podporovaný state machine pause/retire podle backend pravidel.
- `SUSPENDED` monitoring musí mít bezpečnou recovery cestu `resume` nebo `retire`.
- `RETIRED` monitoring se znovu neaktivuje jako nový lifecycle; po retirement lze vytvořit nový enrollment.
- Opakované auditované transitions se stejným textem reason musí mít samostatnou audit evidence; correlation ID rozlišuje jednotlivé akce.

## 6. Pre-session daily operator check

Každý XNYS pracovní den před očekávaným během zaznamenejte:

- API, DB, worker a scheduler health/heartbeat;
- právě jeden enabled autonomous pilotní deployment;
- právě jeden relevantní ACTIVE monitoring run;
- existenci očekávaného `MONITOR_PAPER_DEPLOYMENT` jobu;
- latest completed XNYS session a next signal/execution session z backendu;
- poslední ingestion `SUCCEEDED`;
- žádný partial/failed current feed;
- complete corporate-action readiness;
- účet `ACTIVE`;
- reconciliation `SAFE`;
- cash/equity a očekávané held instruments;
- žádný nevyřešený FAILED/DEAD_LETTER run;
- žádný runtime-manifest mismatch.

Po session zkontrolujte:

- job occurrence/attempt;
- decision time;
- executable open;
- order/fill identity;
- commission/slippage;
- cash delta;
- position delta;
- performance snapshot;
- audit correlation;
- novou reconciliation.

Chybějící run není důvod k ručnímu zpětnému fillu.

## 7. Reconciliation recovery

Při reconciliation mismatch:

1. účet musí zůstat `HALTED` nebo jinak fail-closed podle backend autority;
2. opravte příčinu bez direct DB ekonomických editací;
3. z operator UI spusťte autoritativní reconciliation recovery;
4. ověřte nový reconciliation record a stav `SAFE`;
5. teprve potom použijte podporovaný `RESUME` s důvodem;
6. zkontrolujte audit actor/reason/correlation evidence.

Pokud reconciliation není `SAFE`, RESUME není povolený pilotní postup.

## 8. Monitoring recovery

Při monitoring safety problému:

1. pause/suspend podle autoritativního backend state machine;
2. zjistěte příčinu;
3. po odstranění příčiny použijte podporované `resume`, pokud backend gate dovolí přechod;
4. pokud recovery není vhodná, použijte `retire`;
5. nový lifecycle po retirement řešte novým enrollmentem;
6. nikdy nepřepisujte monitoring stav ručním SQL.

## 9. Emergency stop

Při duplicate fillu, retroactive fillu, unexplained fillu, reconciliation mismatch, stale workeru, nejasné lineage nebo runtime-manifest mismatch:

1. okamžitě spusťte `/operator/risk/halt` přes operator UI/control-plane jako oprávněná role s důvodem;
2. disable autonomous deployment;
3. nespoléhejte na disable jako na zrušení již běžící transakce;
4. uchovejte audit, run/attempt, cycle, order/fill a DB log evidence;
5. nic nemažte ani neopravujte ručním ekonomickým SQL;
6. incident klasifikujte.

Jakýkoli P0/P1 ukončuje pilot do opravy a nové acceptance.

## 10. Provider/data failure

- Provider failure musí skončit retry/dead-letter nebo no-action; ne ručním fill.
- Stale/missing data musí failovat closed.
- Missing/incomplete corporate-action readiness musí failovat closed před ekonomickým efektem.
- Po obnově provideru znovu ověřte data readiness před resume autonomního běhu.

## 11. Worker crash a lease recovery

- Ukončete starou instanci a ověřte lease expiry/ownership.
- Spusťte právě jednu novou worker instanci.
- Ověřte stejnou occurrence/cycle identity podle podporovaného retry modelu.
- Nesmí vzniknout duplicate fill ani druhý ekonomický efekt stejné occurrence.
- Ověřte fresh heartbeat a audit/run evidence.

Jednou během pilotu proveďte plánovaný recovery drill na bezpečném/idempotentním scénáři a zaznamenejte výsledek.

## 12. DB outage

1. obnovte DB;
2. ověřte readiness;
3. ověřte migrations/schema;
4. spusťte worker/scheduler až po DB readiness;
5. před ekonomickým resume spusťte reconciliation;
6. pokračujte pouze při `SAFE`.

## 13. Mandatory staging dry-run před první pilotní session

Před prvním autonomním PAPER během proveďte v production-like staging prostředí celý podporovaný flow:

1. clean migrations;
2. instrument;
3. universe;
4. PIT membership;
5. market-data ingestion;
6. corporate-action readiness;
7. immutable snapshot;
8. experiment;
9. eligibility;
10. promotion;
11. deployment;
12. runtime manifest review;
13. approval;
14. monitoring enrollment;
15. ověření právě jednoho `MONITOR_PAPER_DEPLOYMENT` jobu;
16. autonomous enable;
17. worker/scheduler heartbeat;
18. scheduled safe execution/no-action;
19. reconciliation;
20. recovery test pause/resume nebo HALT/reconcile/resume bez direct DB editace.

Dry-run musí skončit:

- bez duplicate economic effect;
- bez retroactive fillu;
- bez nevysvětleného reconciliation mismatch;
- s kompletní audit correlation;
- s viditelným worker/scheduler/monitoring evidence.

## 14. Pilot exit criteria

Pilot je úspěšný pouze pokud současně platí:

1. proběhlo **10 po sobě jdoucích XNYS sessions**;
2. 100 % očekávaných occurrences má dohledatelný job run/attempt a konečný stav nebo vysvětlený fail-closed no-action;
3. žádný duplicate fill;
4. žádný fill navíc po retry;
5. žádný retroaktivní fill;
6. 100 % fillů má vysvětlitelný order, risk decision, cash/quantity/commission delta, cycle, deployment, manifest a monitoring lineage;
7. po každé session je reconciliation `SAFE` a není žádný nevysvětlený ledger/position/cash mismatch;
8. není žádný P0/P1 incident ani obcházení approval, monitoring nebo risk autority;
9. alespoň jeden zdokumentovaný worker restart/lease recovery drill skončil bez stuck jobu a bez dvojitého ekonomického efektu;
10. monitoring má pro každou realizovanou session konzistentní paper snapshot/evaluation oddělenou od research OOS baseline;
11. denní checklist a audit correlation jsou kompletní pro všech 10 sessions.

Nesplnění kteréhokoli kritéria nevede k live tradingu ani rozšíření scope. Pilot se prodlouží po bezpečném vyřešení P2/P3, nebo se zastaví při P0/P1.

## 15. Zakázané pilotní postupy

- žádný live broker;
- žádné live order routing;
- žádné direct SQL ekonomické opravy;
- žádné znovuzavedení `/demo/trading/cycles/run-paper`;
- žádný retroaktivní fill;
- žádné obcházení eligibility, promotion, approval, monitoring nebo risk;
- žádné spuštění při stale/missing data nebo incomplete corporate-action readiness;
- žádné paralelní scheduler deploymenty mimo schválený pilotní topology.
