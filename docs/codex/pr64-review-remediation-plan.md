# PR #64 Codex review remediation plan

Tento dokument je implementační zadání pro uzavření pěti review nálezů z Codex review commitu `bf2c4046dc600bf4932894978dc10dfbd0546768`. Změny musí zachovat PAPER-only invariant, fail-closed chování, auditovatelnost a nesmí oslabit RBAC ani CI.

## 1. Monitoring enrollment a schedule

- Odstraň false claim, že enrollment a vytvoření monitorovacího jobu jsou atomické, pokud skutečně nejsou v jedné DB transakci.
- Zaveď idempotentní `ensure` logiku pro deterministický `MONITOR_PAPER_DEPLOYMENT` job. Pokud job již existuje a je disabled, ensure jej musí bezpečně znovu aktivovat a ověřit jeho type/account/config.
- Autonomous enable musí fail-closed ověřit nejen ACTIVE monitoring run, ale i existenci a enabled stav odpovídajícího deterministického monitoring jobu. Nelze povolit autonomní execution bez aktivního monitorovacího schedule.
- Přidej regresní test: enrollment -> disable monitoring job -> opakovaný enrollment/ensure -> job je enabled; autonomous enable při chybějícím/disabled monitoring jobu selže.

## 2. Corporate-action readiness / provider

- Neoznačuj H2 ani paper pilot jako READY, pokud jediný production allowlisted provider je `StooqProvider` s `supports_actions=False`.
- Nesmí se pouze přepnout metadata na `supports_actions=True` bez skutečného zdroje a validace corporate actions.
- Pokud v tomto PR nebude implementován reálný production-capable provider včetně end-to-end testů, audit a runbook musí explicitně označit H2 jako OPEN/NOT READY a výsledný verdict jako `NOT READY FOR PAPER PILOT` do odstranění blockeru.
- Synthetic capable provider v testu není production evidence.

## 3. Audited DEAD_LETTER recovery

- Přidej reasoned operator endpoint pro retry automation runu, např. `POST /operator/automation/runs/{run_id}/retry`.
- Endpoint musí používat authenticated request principal, povinný audit reason a correlation ID; po úspěchu zapsat control audit event s actor/reason/correlation evidence.
- Neexponovat přes operator UI generické `run-now` pro ekonomické joby.
- Operations UI musí u `FAILED`/`DEAD_LETTER` nabídnout explicitní retry formulář s run ID a povinným reason; akce musí jít pouze přes `/operator/...` server action.
- Přidej backend a frontend regresní testy pro auditovaný retry a zákaz retry `SUCCEEDED` runu.

## 4. Production market-data egress

- Worker musí mít síťovou cestu k production allowlisted market-data provideru. Databázová `data` síť zůstává `internal: true`.
- Použij oddělenou aplikační/egress síť; nevystavuj worker port na hosta.
- Zachovej aplikační host allowlist v provider transportu (`https://stooq.com`, bez arbitrary URL/redirectu).
- Doplň test/production-smoke evidence alespoň pro topologii a provider allowlist. Externí smoke nesmí být false-green; pokud živý provider test není vhodný pro CI, dokumentuj povinný staging egress check před pilotem.

## 5. XNYS open orchestration

- Odstraň 300s interval zakotvený v čase enablementu jako autoritativní orchestration pro `PREPARE_PAPER_SESSION`.
- Schedule musí být explicitně svázán s XNYS open (`America/New_York`, DST-safe) nebo jiným deterministickým mechanismem, který nezávisí na náhodné fázi enablementu.
- Execution-open semantics musí být kompatibilní s reálným worker poll intervalem a časem potřebným pro provider refresh, aniž by vznikl look-ahead. Zachovej close-derived decision -> next-session raw open invariant a deterministický `execution_intent_time`.
- Přidej regresní testy pro DST, worker delay/polling a missed-open fail-closed scénář.

## Acceptance

Před merge musí projít `quality`, `unit-research`, `api`, `integration-postgres`, `frontend`, `security`, `container-build` a `production-smoke`. Dokumentace nesmí tvrdit `RESOLVED`, `PASS` ani READY u bodu, který zůstává pouze synteticky otestovaný nebo vyžaduje chybějící production capability.
