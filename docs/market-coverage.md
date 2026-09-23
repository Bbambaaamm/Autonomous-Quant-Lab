# Pokrytí trhu — issue #164

## Zero-cost-first politika pro PAPER (ověřeno 23. 9. 2026)

Do explicitního rozhodnutí uživatele je rozpočet na nová datová předplatná **0 USD/měsíc**.
Placený zdroj není podmínkou současného PAPER provozu a #164 nesmí automaticky nakoupit
žádnou službu. Aktivní americká vrstva používá existující bezplatný Alpaca Basic/IEX účet.

Doplňkové bezplatné zdroje se používají pouze v rozsahu, který umíme doložit. Existující
Stooq adaptér pro jednotlivé mapované symboly zůstává oddělený od kandidátního bulk-world
zdroje. Živá kontrola ze staging serveru 23. 9. 2026 potvrdila u bulk-world URL bez klíče
HTTP 401; to samo nedokládá, že autentizace zpřístupní kompletní globální katalog nebo ceny.
Twelve Data Basic a Alpha Vantage Free jsou další kandidáti po založení bezplatného API
klíče; jejich free limity se nesmějí vydávat za úplné globální pokrytí.

API `/operator/market-coverage` vrací `zero_cost_policy` s verzovanou maticí zdrojů,
nulovým rozpočtem, stavem konfigurace a explicitními mezerami. Dashboard tuto matici
zobrazuje. Přidání zdroje do matice samo nepovoluje jeho široký automatický sběr.

Ověřené veřejné limity k tomuto datu:
- Alpaca Basic: zdarma, US stocks/ETFs, historie od 2016, 200 historických požadavků/min,
  free realtime feed IEX.
- Twelve Data Basic: zdarma, 8 API kreditů/min a 800/den; globální trial symboly nejsou
  důkazem úplného světového katalogu.
- Alpha Vantage standard free: 25 požadavků/den pro většinu datasetů.
- Stooq bulk-world candidate: z `quantlab-staging-01` dne 23. 9. 2026 bez klíče HTTP 401.
  Není implementovaný v provider factory a úplnost světových burz není ověřena; nelze tedy
  tvrdit, že credential je jediná chybějící podmínka. Existující Stooq per-symbol adaptér
  zůstává samostatnou credential-free funkcí.


## Přesné provozní měření široké dávky

Nově založené cenové dávky mají `telemetry_version=1`. Každá úloha kumuluje skutečný
počet pokusů o HTTP přenos po průchodu lokálním request-budget guardem, velikost
přijatých response bodies a Linux process high-water RSS (`ru_maxrss`). Retry stejné
úlohy se přičítají; vyčerpání lokálního budgetu před síťovým voláním se jako HTTP
požadavek nepočítá.

Po přechodu celé dávky do terminálních stavů se jednou uloží completion evidence:
celkový počet HTTP requestů, response bytes, počet task attempts, nejvyšší worker RSS,
čas od vytvoření dávky do uzavření, velikost PostgreSQL databáze při startu a konci
a její růst. Evidence se po prvním zápisu nepřepisuje. Starší dávky mají
`telemetry_complete=false`, protože jejich síťové požadavky nelze zpětně přesně
rekonstruovat; nuly se nesmějí vydávat za naměřený provoz.

Dashboard zobrazuje metriky pouze tehdy, když existují. Databázová velikost je
celková velikost databáze, zatímco `database_growth_bytes` zachycuje změnu během
konkrétní dávky. Peak RSS je high-water procesu workeru pozorovaný během úloh dávky,
nikoli součet paměti všech kontejnerů.

## Aktuální ceny a čas přijetí událostí (22. 9. 2026)

Cenová fronta ukládá surové ceny nezávisle na úspěchu kontroly dividend a splitů.
Alpaca REST inventář se načítá se všemi stránkami a všemi typy událostí. Jeho
původní řádky, rozsah dotazu, instrument, hash a skutečný čas přijetí se ukládají
do neměnné tabulky `market_action_receipts`. Neúplné stránkování není přijatý
inventář. Nepodporovaná či vadná relevantní událost zabrání způsobilosti, ale
již uložené ceny a skutečný počet stažených seancí zůstanou zachovány.

Pravidla `us-current-universe-2` dovolují použít tento inventář pro **aktuální**
screening. Všechny jeho události mají `known_at` rovný skutečnému přijetí; záznam
nelze použít před tímto časem. Evidence výběru rozlišuje `REST_CURRENT_SNAPSHOT`
od historické readiness. Do SSE historie, historické readiness ani projekce
pilotních corporate actions tento sběr nezapisuje. Výběr zůstává
`research_eligible=false`: dnešní informace se nevydávají za tehdejší znalost.
Původní přísná cesta výzkumného importu a tvorby datasetů zůstává samostatná.

Změna odstraňuje závislost plošného aktuálního sběru na načítání celé SSE historie
při každém instrumentu. Nemění limity požadavků, rozsah feedu, zapnutí automatizace
ani pravidla obchodování. Globální zdroj a historické složení trhu jsou stále
samostatné nedokončené podmínky #164.

## Stav implementace

První doručená část je referenční katalog, nikoli dokončení globálního sledování.
Nová stránka `/market` nabízí serverové vyhledávání, stránkování po 50 záznamech,
počty podle burz, stáří přijetí i zdrojových souborů a explicitní neznámé pokrytí cen.
Katalog eviduje oba celé adresáře Nasdaq Trader; testovací záznamy jsou spočítány
samostatně. Neznámé burzovní kódy se zachovají. Non-ETF není automaticky klasifikován
jako běžná akcie (může jít například o warrant či preferenční akcii).

Každý import nejprve ověří oba soubory a pak je zveřejní v jedné transakci. Chyba
ponechá starý snapshot. Ukládáme receipt time UTC, zdrojový lokální čas bez
vymyšlené časové zóny, hashe, původní payloady zařazených řádků, počty a aktéra/důvod.
PostgreSQL trigger zakazuje změny a mazání přijaté evidence. Historický dotaz
vybere pouze snapshot přijatý do svého knowledge cutoffu. Dnešní reference se
nepromítají zpětně do historického složení investičního souboru.

Katalog nezasahuje do `instruments`, členství, paper deploymentů ani objednávek.
Symbol v adresáři není bezpečný identifikátor ekonomického instrumentu: slučování
přejmenovaných/recyklovaných symbolů vyžaduje doplňující referenční evidenci.

## Provoz

Migrace: `alembic upgrade head` v existujícím nasazovacím procesu. Použít dosavadní
runtime grants/default privileges (scripts/configure-runtime-role.sql).

Správce může na stránce Pokrytí trhu:

1. Načíst oba aktuální adresáře jednorázově (dva HTTPS GET, limit 16 MiB na soubor,
   timeout 15 sekund na přenos; přesměrování nejsou povolena).
2. Založit denní úlohu `market-catalog-daily`. První zpracování proběhne při nejbližším
   ticku workeru, následně v 01:00 UTC. Úloha vyžaduje existující aktivní scheduler;
   jeho stav je na stránce Provoz. Tam lze úlohu také vypnout/zapnout.

Worker používá existující leases, omezené retry (založená úloha max. 3 pokusy),
JobRun/JobAttempt a dead-letter evidenci. Ceny ani AI API tento typ úlohy nevolá.
Při opakovaném stejném receipt ID nevznikají duplicitní řádky. Nový receipt time
vytváří novou historickou verzi i při nezměněném obsahu; nutné sledovat růst DB.
Přijetí starého souboru samo neobnoví stav aktuálnosti.

API GET `/operator/market-coverage` vyžaduje alespoň VIEWER; POST `/sync` a
`/schedule` pod stejným prefixem vyžaduje ADMIN a důvod. Frontend používá existující
session, serverové tokeny a same-origin kontrolu.

## Matice zdrojů a zbývající závislosti

| Oblast | Ověřeno | Zbývá |
| --- | --- | --- |
| Referenční katalog USA | Oba skutečné soubory Nasdaq Trader jsou dostupné a parser je zpracoval | Nezávislé porovnání úplnosti a stabilní identity instrumentů |
| Stooq v aktuálním kódu | Denní ceny jednotlivých symbolů; adaptér neimplementuje corporate actions | Doložit vhodnost zdroje/rozsah a podmínky pro plošný sběr |
| Alpaca v aktuálním kódu | Denní ceny, corporate actions, konfigurace feedu | Na serveru ověřit skutečný plán, oprávnění a symbolové mapování; dokumentace není důkaz oprávnění účtu |
| IEX vs. konsolidovaný trh | Dokumentace rozlišuje omezený Basic/IEX a širší americké pokrytí | IEX objemy nelze označit za celotržní likviditu |
| Historické složení trhu | Nové receipt snapshoty zachovávají okamžik znalosti | Historické identity, delistované tituly, IPO a corporate-action lineage před začátkem sběru |
| Evropa, Asie, další regiony | V současném provider factory nejsou globální adaptéry | Zvolit a ověřit datové zdroje, oprávnění, kalendáře, měny a náklady |
| Další třídy aktiv | Dosavadní runtime není univerzální multi-asset adaptér | Samostatně ověřit datový model a execution semantics |

## Co tento PR nedokončuje

- Automatické propojení celého katalogu na stabilní instrument IDs.
- Hromadný cenový backfill, průběžnou aktualizaci cen a reconciliaci jejich úplnosti.
- Globální datové adaptéry a další obchodní kalendáře.
- Screening, příčiny způsobilosti a multi-asset experiment z nového datasetu.
- Ověření provozu na uživatelově serveru; přímé SSH přihlašovací prostředky nejsou
  v tomto pracovním prostředí dostupné.

Tyto části zůstávají otevřené v #164. Nasazení samotného katalogu není důvod issue
uzavřít ani tvrdit, že laboratoř již sleduje ceny celého trhu.

## Zdroje ověřené 21. 9. 2026

- Nasdaq Trader, definice a adresáře:
  https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs
- https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
- https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt
- Alpaca Market Data — rozdíl feedů, oprávnění a plánů:
  https://docs.alpaca.markets/us/docs/about-market-data-api

Žádné nové datové předplatné nebylo objednáno a žádné placené LLM API nebylo použito.

## Lokální ověření

Oba skutečné adresáře (zdrojové časy 21. 9. 2026 14:01 a 15:41) obsahovaly 13 224
netestovacích záznamů: Nasdaq 5 615, NYSE 2 923, NYSE American 308, NYSE Arca 2 733,
Cboe BZX 1 640 a 5 záznamů s nepřiřazeným kódem F. Kód F je zachován a označen
jako neznámá burza; není odstraněn ani odhadnut. Testovacích řádků bylo 37.
Lokální parsing a zápis celého snapshotu do SQLite v paměti trval 0,543 s;
nejde o měření na produkčním PostgreSQL ani o dobu stahování cen.

Lokální úplný migrační řetězec nelze ověřit na SQLite: starší migrace
20260811_06 vyžaduje ALTER foreign key, který SQLite nepodporuje.
PostgreSQL regresní test nové evidence je připraven pro existující PostgreSQL CI.

## Cenová fronta a identity poskytovatele

Po referenčním katalogu lze na `/market` načíst adresář aktivních US equity
instrumentů z **paper** endpointu Alpaca. Adresář vyžaduje existující serverové
přístupové údaje. Jeho dostupnost nepotvrzuje oprávnění k historickým cenám,
konsolidovanému SIP feedu ani globálním burzám. Fronta používá nakonfigurovaný
feed; žádné předplatné nezakládá. IEX není konsolidovaný objem celého trhu.

Identita je UUID poskytovatele, nikoli ticker. Každá přijatá verze je neměnná,
s UTC časem přijetí a hashem obsahu. Změna tickeru nebo kolize s existujícím
kanonickým instrumentem se zastaví pro vyřešení; nepřepisuje historii pilotu.
Adresář aktivních titulů není důkaz historického složení trhu ani kompletní
evidence IPO/delistingu. Datum přijetí se nevydává za datum IPO.

Správce založí dávku s obdobím nejvýše 730 dní, končícím uzavřenou seancí.
Založí se úlohy `market-price-queue` a `market-identities-daily`. Vypnutí/zapnutí
existujících úloh je na `/operations`; opětovné založení je samo nezapne.
Worker zpracovává po jednom instrumentu, s nejvýše 12 HTTP požadavky a časovým
rozpočtem 45 sekund na instanci poskytovatele. Jednotlivé požadavky mají vlastní
timeout. Úloha má tři pokusy, prodlevy 5/10/15 minut a obnovitelný desetiminutový
pronájem. Opakování po pádu nevytváří jiný kanonický instrument.

Při HTTP 401/403 se čekající úlohy stejného feedu zablokují, aby tisíce titulů
neopakovaly neplatné přihlášení. Po opravě přístupu načtěte nový adresář a založte
novou dávku. Staré blokované úlohy zůstávají jako evidence. Automatická návazná
dávka zatím nepřekročí existující blokaci přístupu; provoz vyžaduje kontrolu správce.
Při chybějící historické evidenci přijetí dividend/splitů se zobrazí samostatná
blokace dat. Žádné historické znalosti se nevyrábějí zpětně.

Po dokončení a nové uzavřené seanci vznikne další dávka, pokud je adresář čerstvý.
Existující ceny se znovu načítají od první chybějící seance s přesahem pěti seancí
pro revize. Čtení i výpočty respektují čas skutečného přijetí. Historie má omezené
okno a fronta nevytváří další dávky přes nedokončenou práci.

Český přehled má serverové hledání, stránkování a řazení podle technických
ukazatelů. Jmenovatel pokrytí je počet požadovaných obchodních seancí, ne počet
vrácených řádků. Pro výpočet je nutných alespoň 127 cen a úplné poslední okno.
Ukazatele jsou **diagnostika neupravených cen**, nikoli investiční signály nebo
splnění podmínek výzkumu. Chybějící hodnoty jsou „Neověřeno“, nikdy nula.
Úspěšně zpracovaný titul nemusí mít úplnou historii. Počet titulů dávky se nesmí
zaměnit s počtem referenčního katalogu nebo s celosvětovým pokrytím.

### Co tato implementace ještě nedokládá

- skutečná oprávnění a dostupnost historických cen konkrétního serverového účtu;
- historické členství celého trhu a kompletní corporate-action knowledge;
- validovaný screening z upravených cen, benchmark a nedotčené OOS;
- poskytovatele, licence, měny a kalendáře dalších světových regionů;
- výkonnost celé dávky na produkčním serveru.

Tyto položky zůstávají v #164 otevřené. Cenová fronta nepoužívá placené LLM API,
nemění schválená nasazení a nevytváří objednávky.

Fronta má minimální interval pět sekund; její vlastní rozpočet je nejvýše 12
požadavků na úkol. Jde o rozpočet této fronty, nikoli o měřič celkového účtu
(sdílené feedy a jiné procesy mohou přidávat další požadavky). Před hromadným
provozem je nutné potvrdit limity konkrétního účtu. Uvedené US burzy používají
konfigurovaný kalendář XNYS; plná nezávislá evidence historických odchylek
kalendářů je dalším omezením mimo současnou diagnostiku.

## Aktuální screening a ověření 22. 9. 2026

Živě potvrzené automatické nasazení #166 a opakovaný katalogový běh v 01:00 UTC.
Adresář Alpaca vrátil 14 346 aktivních identit. Dávka 17. 8. 2025–21. 9. 2026
obsahuje všechny: 13 197 na konfigurovaných burzách a 1 149 s nepodporovanou burzou.
Číslo se liší od referenčních 13 224 položek; adresáře nejsou totožné množiny.
První cenové úlohy skončily opakováním bez nového úspěšného importu. Cenová fronta
byla administrátorsky pozastavena do vyřešení příčiny; katalog a identity zůstaly
zapnuté. Dostupný adresář není důkaz dostupného cenového zdroje.

Nová verze přidává oddělený screening `us-current-universe-1`. Pravidla jsou
verzovaná před výpočtem: 127 seancí, minimální pokrytí 98 %, žádná mezera v posledních
127 seancích, poslední raw cena alespoň 5 USD a průměrný dolarový objem posledních
20 seancí alespoň 1 milion USD **na konkrétním feedu**. Nejde o slib výnosu ani
ověřený investiční model. IEX se nevydává za konsolidovanou likviditu.

Po stažení musí být corporate actions ověřeny pro celé požadované období,
včetně inkrementálních běhů. Screening používá existující kauzální úpravu cen
pouze akcemi známými a účinnými k času výpočtu. Uchovává policy hash, receipt cutoff,
observation IDs, action evidence a důvody vyřazení. Momentum, trend a mean reversion
mají samostatné pořadí a stabilní tie-break. Raw diagnostika je nadále oddělená.

Po dokončení všech úkolů worker automaticky uloží immutable výběr včetně
neúspěšných a nepodporovaných titulů; filtr nesnižuje jmenovatel. PostgreSQL triggery
zakazují změny i mazání výběru. GET `/operator/market-screening` je stránkovaný;
český dashboard rozlišuje chybějící výsledek a dokončený výběr bez kandidátů.
Migrace `20260922_01` odmítne downgrade, pokud by odstranil existující výběry.

Evidence zachycuje postupný sběr: jednotlivé instrumenty mají vlastní receipt cutoff,
nejde o jediný synchronní historický snapshot trhu. Výběr je jen aktuální screening;
`research_eligible=false`. Neopravňuje k historickému backtestu s dnešním složením
trhu ani nemění schválený paper deployment. M7 vyžaduje samostatné společné PIT
podmínky, předem stanovený benchmark/rozpočet a nedotčený OOS. Žádný takový skutečný
víceinstrumentový experiment zatím nebyl doložen.

Nové čtení adresářů porovnává dvě poslední immutable verze známé k zadanému času:
nově zjištěné UUID, chybějící UUID a změny symbolu/burzy. Nepřítomnost se nevydává
za potvrzený delisting a první pozorování za IPO. Kanonická historie pilotu se
nepřepisuje. Pro potvrzené IPO/delisting a globální burzy stále chybí doložený zdroj.

Oprava obnovování fronty: stará ACCESS_BLOCKED dávka už nezastaví další den po
úspěšném obnovení přístupu a dokončení nové dávky. Blokace nejnovější dávky zůstává
účinná. Přesnější chyby obsahují jen fázi a typ chyby, nikdy raw provider odpověď,
URL ani přístupové údaje. Rutinní sběr a screening nevolají LLM API.

Uzavření výběru čte evidence po 100 řádcích; nedrží celou historii všech titulů
v paměti. Otisk obsahu je SHA-256 seřazených kanonických JSON řádků oddělených LF.
Druhý průchod ověřuje stejný otisk při ukládání; změna evidence zruší transakci.

### Jednorázová diagnostika zdroje

Správce může na Pokrytí trhu spustit „Ověřit spojení“. POST
`/operator/market-pipeline/probe` je auditovaný, vyžaduje ADMIN a důvod. Provede
pouze tři GET požadavky pro IBM: jednu denní cenu a dvě stránky corporate actions
(krátké období a celý rozsah fronty 1970–9999 s data_quality=all),
každý s limitem jednoho výsledku a timeoutem nejvýše pět sekund. Vrací jen HTTP stav,
latenci a pevně definovanou klasifikaci DNS/TLS/timeoutu. Nevrací klíče, hlavičky,
raw odpověď ani text výjimky. Nemění účet, data ani plánování.

Kontrola běží z backendu. Úspěch neprokazuje síťovou dostupnost ze samostatného
workeru, oprávnění k jinému feedu, úplnou historii nebo správnost corporate actions.

### Oprava ručního importu při změně zdroje

Živá kontrola prokázala, že ruční formulář posílal natvrdo Stooq i při konfiguraci
Alpaca. Import nyní ponechá volbu serveru. Uložení datasetu bez explicitního provideru
použije persistentní identitu nakonfigurovaného feedu (např. alpaca:iex); explicitní
provider zůstává podporovaný pro historické uložené zdroje. RBAC a kontroly kvality
se nemění. Krátká diagnostika IBM na serveru vrátila HTTP 200 pro ceny i události;
to samo neověřuje celý historický rozsah ani worker.

Diagnostika fronty rozlišuje známé vyčerpání lokálního rozpočtu a HTTP 5xx zdroje.
Neznámé texty výjimek se nadále nezobrazují.

## Active / inactive lifecycle evidence — M2

Adresář Alpaca se načítá ve dvou explicitních pohledech: `status=active` a
`status=inactive`. Oba odpovědní payloady se před uložením spojí a celý výsledný
snapshot je opět immutable s vlastním receipt time a content hashem. Každý řádek
uchovává provider UUID, symbol, burzu a providerový lifecycle status.

Opakované snapshoty rozlišují první/poslední pozorování UUID, změnu symbolu/burzy a
přechody `active → inactive` / `inactive → active`. Jde o **čas zjištění změny
providerového statusu**, nikoli automaticky o datum IPO, delistingu nebo ekonomickou
účinnost corporate action. Dashboard tuto hranici výslovně uvádí.

Původní active-only snapshoty jsou při migraci `20260922_04` označeny `active`,
protože právě takový byl původní dotaz provideru. Downgrade se odmítne, jakmile
databáze obsahuje skutečnou inactive evidenci, aby lifecycle informace nešla tiše
zahodit.

Inactive reference zůstávají v adresáři pro historickou/lifecycle dohledatelnost.
Jejich přítomnost sama nedokládá úplnost všech historicky zaniklých US titulů před
prvním sběrem a není náhradou přesného IPO/delisting master source.
