# Operator dashboard

Phase 8 přidává lokální, single-operator Next.js control plane v češtině. Prohlížeč komunikuje
se server components/actions; backend adresa `QUANTLAB_API_URL` je serverová konfigurace a
výchozí hodnota je `http://127.0.0.1:8000`. Neexistuje generic proxy ani `NEXT_PUBLIC_*` secret.
Všechny finanční hodnoty, XNYS freshness, risk a monitoring verdict pocházejí z FastAPI read
modelu; UI je pouze formátuje. Fetch používá `no-store`.

## Spuštění

```bash
# terminál 1
cd backend && uv run uvicorn quantlab.api:app --host 127.0.0.1 --port 8000
# terminál 2 (Node 24 LTS)
cd frontend && npm ci && npm run dev
```

Dashboard je na `http://127.0.0.1:3000`. Stránky: Přehled, Paper a monitoring detail,
Strategie a detail, Research, Risk, Data, Operations a Audit. Paper grafy používají immutable
Phase 7 snapshoty a periodu 1M/3M/6M/YTD/1Y/ALL filtrovanou serverem podle session date. První
`daily_return=null` zůstává N/A. OOS baseline a realized paper jsou vždy oddělené řady; nejsou
vydávány za souběžné kalendářní série.

HALT/RESUME i monitoring PAUSE/RESUME/RETIRE vyžadují přesný potvrzovací text a neprázdný důvod.
UI čeká na server; 409 se zobrazí jako chyba. Resume účtu nemění monitoring. RETIRED nemá akční
formulář. Reconciliation vyžaduje `RECONCILE`. Systém je pouze PAPER a nikde nemá live akci.

Data stránka používá authoritative XNYS latest completed session, nikoli 24h TTL. STARTED,
FAILED nebo chybějící session se nezobrazí jako healthy. Operations zobrazuje PostgreSQL jobs,
runs, dead letters a heartbeat freshness. Audit filtry a stránkování probíhají na serveru;
payload se renderuje escapovaným React textem bez raw HTML.

Kontroly: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`. Při 503 nebo síťové
chybě stránka zobrazí unavailable stav; prázdná evidence se nikdy nenahrazuje nulami.
