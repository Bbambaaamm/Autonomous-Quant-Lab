# Operational Readiness Remediation P0 — B3 Execution-Time Causality

Tato oprava řeší výhradně blocker B3 z
[`operational-readiness-audit.md`](operational-readiness-audit.md). Nemění control plane, worker
contract, scheduler ani PAPER-only hranici.

## Příčina

`ValidatedCurrentDataAccessor.latest()` správně vrací poslední dokončenou XNYS session pro
current-data pohled. Phase 6 paper služba ji ale chybně považovala zároveň za executable session,
vzala její raw open a předala její historický open do Phase 4. Volání po close session T proto
rekonstruovalo fill na open stejné session T.

## Opravená časová semantika

Pro každý run se explicitně určí:

- `signal_session`: poslední dokončená XNYS session T;
- `decision_time`: skutečný XNYS close session T;
- `execution_session`: první validní XNYS session po T;
- `execution_time`: skutečný XNYS open execution session.

Kalendářová navigace používá `exchange-calendars`, a proto respektuje víkendy, svátky, DST i
early close. Pokud execution session ještě nezačala, služba skončí před přístupem k persistence a
nevytvoří cycle, order, fill ani jiný ekonomický side effect. Pokud přesný raw open execution
session není v úspěšné ingestion a nebyl pozorován do času runu, služba rovněž failne closed.

Strategy historie končí signal session T a adjusted close vstupuje pouze do výpočtu targetů. Raw
observation execution session T+1 je načtena odděleně a její open vstupuje pouze do stávající cesty
`TradingCycleService` → risk → execution → `PersistentPaperBroker`. Cycle identita zůstává vázaná
na deployment strategy ID a execution session, takže opakovaný run nevytváří duplicitní ekonomický
cycle.

## Záměrně nezahrnutý rozsah

Otevřené zůstávají B1 (provozní control-plane workflow), B2 (worker integration) a ostatní HIGH,
MEDIUM a LOW nálezy autoritativního auditu. Tato oprava nepřidává live execution, nový job contract,
scheduler, provider ani UI workflow.
