# Operational Readiness P0-A — zákaz retroaktivního open fillu

## Původní chyba

Stage C připravila next-session occurrence správně, ale za hranici zmeškaného open považovala až
close. Worker spuštěný například v 09:35 New York proto mohl načíst denní bar s historickým open
09:30, vytvořit obchod za tuto již nedosažitelnou cenu a fill ekonomicky zpětně označit časem open.
Persistentní intent chránil kalendářovou session, nikoli obchodovatelnost ceny v okamžiku běhu.

## Execution window

Současný daily/open provider nedokládá průběžnou quote. Bez falešné přesnosti je proto povoleno
konzervativní půlotevřené window `[XNYS open, XNYS open + 1 sekunda)`: request, worker decision i
provider response knowledge time v něm musí skutečně ležet. Provider tedy smí dokončit kauzálně
zahájené volání po přesném open, ale několik sekund či minut starý open už použít nelze. Před open je
výsledek `EXECUTION_SESSION_NOT_OPEN`; po cutoff terminální `NO_ACTION` s
`MISSED_EXECUTION_OPEN`. Hranicí nikdy není session close.

XNYS open pochází z `exchange-calendars`, takže se nepoužívá fixní UTC hodina. Stejná semantics
zachovává Friday/holiday mapping, DST i early-close session (early close nemění její open).

## Market time a knowledge time

`timestamp` raw observation je tržní čas session open. `observed_at` je okamžik, kdy provider
response systém skutečně získal; ingestion jej nesmí přepsat historickým open. Pro executable open
accessor vyžaduje `timestamp == execution open` a skutečný `observed_at` uvnitř povoleného window,
nejpozději v execution `as_of`. Obecné daily observations si dále zachovávají vlastní market
timestamp a point-in-time knowledge cutoff.

## Fail-closed vrstvy a retry

Orchestrator po zmeškaném open nevolá open ingest ani nematerializuje nový economic intent a vrací
auditovatelný důvod. Worker kontroluje pinned occurrence ještě před deployment persistence. Poslední
autoritou zůstává `Phase6PaperExecutionService`, která persistentní intent mimo open window odmítne
před current-data accessor i Phase 4 ekonomickou cestou.

Provider retry, který uspěje až po open, tedy nemůže persistovat pozdní observation jako executable
open. Occurrence převzatá workerem po restartu skončí stejně jako `MISSED_EXECUTION_OPEN`: bez orderu,
fillu, změny cash nebo pozice. Úspěšně dokončený `JobRun` je terminální a opakovaná materializace
stejné deployment/session identity jej neoživí.

## Důkazy

- unit testy ověřují pre-open, exkluzivní sekundový cutoff i pět minut po open a že fail-closed guard nevolá
  persistence ani ekonomickou službu;
- PostgreSQL accessor testy oddělují market timestamp od pozdního `observed_at`;
- PostgreSQL ingestion test ověřuje exact-open response a odmítnutí response získané o pět minut
  později;
- povinný CI test `P0 missed-open causality regression` vede occurrence přes worker claim,
  projektový `JobExecutor` a Phase 6 guard a kontroluje nulový ekonomický rozdíl i stabilní retry.

## Zbývající P0 blocker

Tato úzká oprava **nemění systém na READY**. Production Docker Compose stále neobsahuje podporovanou
worker service. To je samostatný P0 production-worker blocker a tento PR jej záměrně neřeší; stejně
tak nemění corporate actions, runtime config identity, eligibility ani live trading.
