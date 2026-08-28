# M1 — autoritativní Phase 6 eligibility decision layer

## Původní problém a failure model

Dokončený Phase 6 experiment bylo možné strukturálně validovat a přímo změnit na
`PAPER_CANDIDATE`. Metrická policy, samostatné rozhodnutí a důkaz osoby a důvodu chyběly.
Chybný, opakovaný nebo přímý API požadavek tak nerozlišoval dokončení výzkumu od ekonomického
rozhodnutí. Legacy `research_eligibility_checks` patří staršímu research reportu a není autoritou
pro Phase 6 promotion.

## Policy a autoritativní data

Jediná podporovaná policy `phase6-paper-candidate` verze 1 vyhodnocuje persistentní OOS sloupce
`trade_count >= 1`, `total_return >= 0`, `sharpe >= 0` a `abs(max_drawdown) <= 0.25`.
Hranice jsou inkluzivní. Minimální policy záměrně nepředstírá robustnost, kterou současný Phase 6
model nepersistuje; verze a celý dokument s operátory a thresholdy jsou součástí každého recordu.
Chybějící, NaN nebo nekonečná metrika ukončí evaluaci fail-closed bez rozhodnutí.

## Decision record a deterministická identita

`phase6_eligibility_decisions` je append-only autorita. Obsahuje experiment, snapshot, strategii,
code SHA, policy, vstupní metriky, výsledky pravidel, `ELIGIBLE`/`INELIGIBLE`, serverový actor,
povinný reason, UTC timestamp, correlation ID a SHA-256 integrity hash. Deterministické
`decision_id` je hash experimentu, lineage, přesné policy a metrik. Unikátní klíč
`(experiment_id, policy_id, policy_version)` dělá retry idempotentní; odlišný payload pod stejnou
autoritou selže. PostgreSQL trigger zakazuje UPDATE i DELETE.

## Oddělený API a auditní tok

1. `POST /operator/research/experiments/{id}/eligibility` přijme pouze reason a podporovanou
   policy identitu/verzi. Metriky načítá server.
2. `GET /operator/research/experiments/{id}/eligibility` a detail experimentu vrací policy,
   hodnoty a jednotlivá pravidla.
3. Operátor po kontrole samostatně volá `POST .../{id}/promote`.

Security boundary vyžaduje pro mutace ADMIN a actor odvozuje z ověřeného principalu. Evaluace i
promotion mají oddělené immutable audit eventy.

## Promotion gate a fail-closed scénáře

Promotion nepřepočítává policy. Vyžaduje record aktuální autoritativní verze se stavem `ELIGIBLE`,
ověří integrity hash, experiment, snapshot, strategy identity/version, code SHA a shodu uložených
OOS metrik. Chybějící, `INELIGIBLE`, poškozené nebo lineage-mismatch rozhodnutí nezmění
`research_experiments.decision`. Existující kandidáti se migračně nepřepisují, ale žádný nový
promotion bez M1 recordu neprojde. Legacy checks zůstávají read-only report evidence a Phase 6 je
nikde nepoužívá jako authorization.

## PostgreSQL evidence a CI

Migrace přidává constraints, indexy a PostgreSQL immutability trigger bez backfillu. CI job
`integration-postgres` obsahuje pojmenovaný M1 acceptance krok, který pokrývá čistý control-plane
tok a M1 policy/persistence negativní testy. Lokální prostředí musí před označením nálezu jako
`RESOLVED` potvrdit Alembic head, PostgreSQL acceptance a standardní backend/frontend kontroly.
