# Předstartovní forenzní re-audit

## Executive verdict

**NOT READY FOR PAPER PILOT**

Tento verdikt je záměrně fail-closed. Review PR #64 odhalilo, že předchozí dokumentace označila několik oblastí jako `RESOLVED`, přestože skutečný production path tyto podmínky nesplňoval. Zejména jediný production-allowlisted provider `StooqProvider` deklaruje `supports_actions=False`; XNYS/USD-equity autonomní PAPER pilot proto nesmí být označen jako připravený, dokud nebude integrován skutečný corporate-action-capable provider a celý path nebude ověřen end-to-end.

Současně byly identifikovány mezery v zajištění monitoring schedule, auditovaném DEAD_LETTER recovery, market-data egress topologii a časování autonomous orchestrace. Tento PR tyto body remediuje nebo je explicitně ponechává jako blocker, pokud je nelze pravdivě uzavřít bez nové externí capability.

## Auditní princip

Dokumentace nikdy nesmí být silnější než implementace. `CI PASS` nad syntetickým providerem není důkazem, že production provider umí stejnou capability. Stejně tak existence `ACTIVE` monitoring recordu není důkazem existence enabled monitoring schedule.

Platí PAPER-only invariant z `AGENTS.md`: žádný live broker, live role, live endpoint, live flag ani obcházení risk/control-plane autority.

## Findings z review PR #64

| ID | Severity | Finding | Požadovaný stav před pilotem |
| --- | --- | --- | --- |
| R64-P1-01 | P1 | ACTIVE enrollment mohl existovat s disabled/drifted `MONITOR_PAPER_DEPLOYMENT` jobem a dokumentace nesprávně tvrdila atomicitu. | Deterministický schedule musí být idempotentně ensured; autonomous enable musí validní enabled schedule explicitně ověřit. Dokumentace nesmí tvrdit jednu DB transakci, pokud jí implementace není. |
| R64-P1-02 | P1 | Production `StooqProvider` má `supports_actions=False`. | **OPEN PILOT BLOCKER.** Integrace skutečného capable provideru + production/staging E2E evidence. Do té doby autonomous equity pilot fail-closed. |
| R64-P2-01 | P2 | DEAD_LETTER recovery měla pouze generic retry bez reason/principalu/control audit eventu a UI bylo read-only. | Reasoned `/operator/...` retry s actor/correlation audit evidence + ADMIN UI recovery. Generic cesta nesmí být podporovaným recovery pro managed pilot jobs. |
| R64-P1-03 | P1 | Worker neměl production market-data egress. | DB/data network zůstane interní; backend+worker dostanou explicitní market-data egress; frontend ingress je oddělený. Skutečný provider egress musí být ověřen ve stagingu. |
| R64-P1-04 | P1 | PREPARE job běžel po 300 s od náhodné enable fáze, zatímco executable-open okno bylo prakticky netrefitelné. | Orchestrace musí být ukotvená na XNYS 09:30 `America/New_York`, DST-safe. Execution intent zůstává pinned na session open; bounded post-open window slouží jen pro získání/ověření raw opening printu a po cutoff failuje closed. |

## Corporate-action readiness — H2

**H2 není production-ready.**

`StooqProvider` je užitečný pro historické daily bary, ale jeho metadata explicitně uvádějí `supports_actions=False` a `corporate_actions()` neposkytuje production corporate-action feed. Testy se syntetickým capable providerem ověřují fail-closed mechanismus a datový kontrakt, nikoli dostupnost production capability.

Proto:

- `UNSUPPORTED` se nesmí prezentovat jako green readiness;
- autonomous enable pro production equity pilot musí odmítnout spuštění, pokud zvolený production provider neumí corporate actions;
- žádná dokumentace nesmí označit H2 jako `RESOLVED` pouze na základě syntetického CI provideru;
- pilot zůstává `NOT READY` do integrace a E2E ověření capable provideru.

## Monitoring enrollment a schedule

Požadovaný invariant je:

1. monitoring lifecycle má právě jeden podporovaný open run;
2. pro jeho `monitoring_id` existuje právě jeden deterministický `MONITOR_PAPER_DEPLOYMENT` job;
3. job má očekávanou immutable identitu/config, je enabled a má podporovaný schedule;
4. opakovaný enrollment stejného ACTIVE runu tento schedule idempotentně **ensure**ne, včetně bezpečného re-enable existujícího disabled jobu;
5. autonomous enable failuje closed, pokud schedule chybí, je disabled nebo má drift;
6. generic `/automation/jobs/*` mutation nesmí být cesta pro změnu managed pilot jobs.

Enrollment record a scheduler job mohou vznikat v oddělených transakcích. Proto tento dokument **netvrdí transakční atomicitu** mezi Phase-7 enrollmentem a automation jobem. Bezpečnostní vlastností je fail-closed ensure + validace před autonomous enable.

## DEAD_LETTER recovery

Podporovaná pilotní recovery musí vést výhradně přes operator control-plane:

- ADMIN zadá `run_id` a povinný audit reason;
- backend odvodí actor ze server-side principalu;
- mutation použije existující autoritativní retry state machine;
- vznikne control audit event s actor/reason/correlation;
- UI neposkytuje generic economic `run-now` shortcut.

Dokud tento path není implementovaný a otestovaný, M4 recovery nelze označit jako kompletní.

## Production network topology

Cílová topologie odděluje účely sítí:

- `data`: interní DB síť;
- `application`: interní frontend ↔ backend komunikace;
- `market-data-egress`: explicitní neinterní egress pouze pro backend a worker;
- `ingress`: neinterní síť frontendu pro publikovaný host loopback port.

Tím není PostgreSQL vystavený ven a worker/backend mohou kontaktovat allowlisted market-data provider. Samotná Docker síť není hostname firewall; aplikační Stooq transport proto musí dál vynucovat pouze HTTPS `stooq.com`. Před pilotem je povinný staging egress smoke proti skutečnému provideru.

## XNYS open scheduling a causalita

Close-derived rozhodnutí má execution intent připnutý na **next XNYS session open**. Scheduler nesmí používat libovolnou pětiminutovou fázi od okamžiku, kdy operátor klikl na enable.

Cílový schedule je kalendářově ukotven na `09:30 America/New_York`, takže DST převádí správný UTC čas automaticky. Bounded post-open window neznamená posun decision/execution intentu: `scheduled_for`/occurrence zůstává session open. Window pouze poskytuje workeru krátký čas na získání a validaci skutečného opening printu. Po cutoff je jediný správný výsledek `MISSED_EXECUTION_OPEN` / no-action; retroaktivní fill je zakázán.

## Evidence, která je nutná před změnou verdictu

Před změnou z `NOT READY` musí být současně doloženo:

1. všechny required CI jobs zelené na výsledném SHA;
2. PostgreSQL acceptance pro monitoring ensure/re-enable a autonomous fail-closed;
3. API/RBAC/audit test pro operator DEAD_LETTER retry;
4. frontend test recovery formu bez economic run-now shortcutu;
5. regression test XNYS 09:30 schedule, DST a missed-open cutoff;
6. production Compose topology test/smoke;
7. staging ověření skutečného backend/worker egressu;
8. **skutečný corporate-action-capable production provider** a jeho end-to-end readiness/execution evidence.

Bod 8 je aktuálně otevřený externí capability blocker a sám o sobě drží verdict `NOT READY FOR PAPER PILOT`.

## Mandatory staging dry-run po uzavření blockerů

Teprve po splnění výše uvedených podmínek proveď celý supported flow:

`DATA → UNIVERSE → SNAPSHOT → EXPERIMENT → ELIGIBILITY → PROMOTION → DEPLOYMENT → APPROVAL → MONITORING → AUTONOMOUS PAPER`

Dry-run musí navíc ověřit:

- právě jeden ACTIVE monitoring context;
- právě jeden validní enabled monitoring schedule;
- autonomous schedule ukotvený na XNYS open;
- provider egress z backendu i workeru;
- complete corporate-action readiness skutečného production provideru;
- no-action po missed-open cutoff bez ekonomického efektu;
- auditovaný FAILED/DEAD_LETTER retry;
- SAFE reconciliation před resume;
- žádný duplicate/retroactive fill;
- kompletní actor/reason/correlation evidence.

## Final recommendation

Předchozí `CONDITIONAL READY` verdict z PR #64 se ruší. Aktuální bezpečný verdikt je **NOT READY FOR PAPER PILOT**. Runtime remediation může uzavřít monitoring schedule, recovery, egress a XNYS scheduling, ale production equity pilot se nesmí spustit, dokud nebude k dispozici a end-to-end ověřen provider s pravdivou corporate-action capability.
