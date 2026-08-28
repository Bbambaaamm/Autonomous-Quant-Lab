# Paper pilot runbook

Tento runbook je výhradně pro interní, řízený PAPER pilot: XNYS, USD equities, denní bars, jeden
deployment, jeden scheduler/worker a aktivní lidský dohled. Nezakládá ani nepovoluje live režim.

## 1. Deployment prerequisites

- Použijte pouze commit se zelenými CI jobs `quality`, `unit-research`, `api`,
  `integration-postgres`, `frontend`, `security`, `container-build` a `production-smoke`.
- Připravte PostgreSQL 17, HTTPS ingress, persistentní volume, ověřený backup/restore a oddělenou
  least-privilege runtime roli podle `scripts/configure-runtime-role.sql`.
- Vygenerujte unikátní silné viewer/operator/admin tokeny a DB secret mimo Git. Nikdy nepoužívejte
  development `trust` compose pro pilot.
- Zkontrolujte, že pilotní universe obsahuje pouze několik USD equities na XNYS a timeframe `1d`.

## 2. Migration a environment

1. Nastavte `.env.production` mimo Git: production DB URL/runtime URL, tři API tokeny,
   `APP_ENV=production`, allowlisted host/origin a `AUTOMATION_ENABLED=true`.
2. Uložte DB heslo do `.secrets/db_password` s minimálními filesystem právy.
3. Spusťte migrace výhradně migrator rolí:
   `cd backend && uv run alembic -c ../alembic.ini upgrade head`.
4. Aplikujte runtime grants a ověřte, že runtime role nemůže měnit immutable evidence.
5. Proveďte backup a test restore do oddělené databáze ještě před prvním deploymentem.

## 3. Start a health verification

1. Sestavte immutable images: `make production-build`.
2. Spusťte služby: `make production-up`.
3. Ověřte `GET /healthz` a autentizované `GET /readyz` přes HTTPS.
4. Ověřte frontend session/login a že viewer nemůže provést mutation.
5. Na `/operator/overview` ověřte `live_trading_enabled=false`.
6. Na `/operations/workers` vyčkejte na fresh worker heartbeat a na `/operator/automation`
   ověřte scheduler heartbeat/readiness. Pokud heartbeat chybí nebo je stale, nepokračujte.

## 4. První paper deployment

Všechny POSTy provádí ADMIN přes autoritativní `/operator` API s explicitním důvodem a stabilním
correlation ID. Operátor nesmí používat demo endpointy ani přímé SQL.

1. Zaregistrujte instrumenty, universe a PIT memberships (`known_at`, `valid_from`, `valid_to`).
2. Proveďte ingestion a ověřte `SUCCEEDED`, provider identity/version a poslední dokončenou XNYS
   session. Ověřte complete corporate-action readiness pro celý požadovaný interval.
3. Vytvořte VALID immutable dataset snapshot a uložte snapshot/content hash do pilot evidence.
4. Spusťte experiment s explicitním code SHA, seed, cost model a chronologickým split.
5. Proveďte eligibility evaluation; zkontrolujte policy ID/version, rules, OOS metrics a integrity
   hash. Pouze `ELIGIBLE` experiment explicitně promujte na `PAPER_CANDIDATE`.
6. Vytvořte deployment pro `paper-main`. Nezávisle zkontrolujte runtime manifest: experiment,
   snapshot, universe, strategy/version, parameters, risk, commission, slippage, volume fraction,
   code SHA a hash. Poté jej schvalte s důvodem.
7. Vytvořte monitoring policy, enrollment a ověřte stav `ACTIVE` a oddělenou OOS baseline.
8. Zapněte autonomous scheduling přes `/operator/deployments/{id}/autonomous/enable`.

## 5. Pre-session a daily operator check

Každý XNYS pracovní den před očekávaným během zaznamenejte:

- API, DB, worker a scheduler health/heartbeat;
- právě jeden enabled autonomous deployment a právě jeden ACTIVE monitoring run;
- latest completed XNYS session a next signal/execution session (backend je autorita);
- poslední ingestion `SUCCEEDED`, žádný partial/failed current feed a complete corporate actions;
- účet `ACTIVE`, reconciliation `SAFE`, cash/equity a očekávané held instruments;
- žádný nevyřešený FAILED/DEAD_LETTER run ani runtime-manifest mismatch.

Po session zkontrolujte job occurrence/attempt, decision time, executable open, order/fill identity,
commission/slippage, cash delta, position delta, performance snapshot, audit correlation a novou
reconciliation. Chybějící run není důvod k ručnímu zpětnému fillu.

## 6. Emergency stop

1. Při duplicate/retroactive/unexplained fillu, reconciliation mismatch, stale workeru nebo
   nejasné lineage okamžitě volejte `/operator/risk/halt` jako OPERATOR/ADMIN s důvodem.
2. Následně disable autonomous deployment; nespoléhejte na disable jako na zrušení již běžící
   transakce.
3. Uchovejte audit, run/attempt, cycle, order/fill a DB log evidence; nic nemažte ani neopravujte
   ručním SQL.
4. Incident klasifikujte. Jakýkoli P0/P1 ukončuje pilot do opravy a nové acceptance.

## 7. Recovery

- **Provider/data failure:** nechte run failnout/retry bounded policy; neopakujte fill ručně.
- **Worker crash:** ukončete starou instanci, vyčkejte na lease expiry a spusťte právě jednu novou.
  Ověřte stejnou occurrence/cycle identity, žádný nový fill a fresh heartbeat.
- **DB outage:** nejprve obnovte DB a readiness, pak worker. Před RESUME spusťte reconciliation.
- **Dead letter:** opravte příčinu, ověřte data/manifest/monitoring a použijte auditovaný retry se
  stejnou ekonomickou identitou.
- **Reconciliation mismatch:** účet zůstává HALTED. Obnovte z ověřené evidence/backup procesu;
  RESUME smí následovat pouze po SAFE reconciliation a nezávislé kontrole.

Jednou během pilotu proveďte plánovaný recovery drill: worker claimne neekonomický nebo bezpečně
idempotentní staging job, je ukončen, lease expiruje a nový worker dokončí stejnou occurrence bez
duplicity. Drill zaznamenejte v audit evidence.

## 8. Pilot exit criteria

Pilot je úspěšný pouze pokud současně platí:

1. proběhlo **10 po sobě jdoucích XNYS sessions** (obchodních sessions, ne kalendářních dnů);
2. 100 % očekávaných occurrences má dohledatelný job run/attempt a konečný stav nebo vysvětlený
   fail-closed no-action;
3. žádný duplicate fill, žádný fill po retry navíc a žádný retroaktivní fill;
4. 100 % fillů má vysvětlitelný order, risk decision, cash/quantity/commission delta, cycle,
   deployment, manifest a monitoring lineage;
5. po každé session je reconciliation SAFE a není žádný nevysvětlený ledger/position/cash mismatch;
6. není žádný P0/P1 incident ani obcházení approval, monitoring nebo risk autority;
7. alespoň jeden zdokumentovaný worker restart/lease recovery drill skončil bez stuck jobu a bez
   dvojitého ekonomického efektu;
8. monitoring má pro každou realizovanou session konzistentní paper snapshot/evaluation oddělenou
   od research OOS baseline;
9. denní checklist a audit correlation jsou kompletní pro všech 10 sessions.

Nesplnění kteréhokoli kritéria nevede k live tradingu ani rozšíření scope. Pilot se prodlouží po
bezpečném vyřešení P2/P3, nebo se zastaví při P0/P1.
