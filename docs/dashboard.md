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
payload se renderuje escapovaným React textem bez raw HTML. Časové auditní filtry se před
porovnáním normalizují do UTC; hodnota bez offsetu z pole označeného UTC se interpretuje jako UTC.

Kontroly: `npm run lint`, `npm run typecheck`, `npm test`, `npm run build`. Při 503 nebo síťové
chybě stránka zobrazí unavailable stav; prázdná evidence se nikdy nenahrazuje nulami.

Seznam strategií zobrazuje identitu implementace a odkaz na nastavení nasazení.
Lookback a rebalance nejsou vlastnosti samotné registrace: detail zobrazuje uložené
`parameters_json` každého deploymentu samostatně, bez doplnění výchozích hodnot.
Globální data readiness zahrnuje všechny aktivní XNYS instrumenty, nikoli jen universe
jednoho deploymentu; chybějící instrument proto není sám o sobě důkazem blokace jeho běhu.

## České uživatelské rozhraní

Navigace, akce a běžné stavy mají české popisky. Původní stavový kód je v titulku
stavového štítku; neznámé stavy jsou neutrální, nikdy automaticky zelené. Datum
a čas v tabulkách se formátují v češtině s explicitním UTC.

Tabulky mají přehled důležitých sloupců, hledání ve všech načtených hodnotách
a stránky po 10 řádcích. Hledání není globální databázový filtr: pro audit
zůstávají dostupné původní serverové filtry a stránkování. Každý řádek má
rozbalovací úplný záznam, včetně všech polí, přesných čísel a nezkrácených ID.
Chybějící hodnoty se nezaměňují za nulu. Procenta v přehledu jsou zaokrouhlena,
původní hodnoty jsou vždy v detailu.

Rozložení používá zmenšitelné sloupce, vodorovné posouvání uvnitř tabulek,
zalamovatelné štítky a mobilní navigaci. Formuláře zachovávají stejné akce,
povinné důvody, role a potvrzení HALT/RESUME. Změna rozhraní nemění obchodování.
