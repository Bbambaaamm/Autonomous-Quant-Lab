# Aktuální události a auditovatelné přehodnocení — #164

Aktuální REST normalizace `current-actions-2` přidává dvě ekonomicky odlišné situace:

- Akciová dividenda stejného symbolu: `rate` je počet **dodatečných** akcií na jednu
  původní. Počet akcií se násobí `1 + rate`; cenový signál se dělí stejným poměrem.
  Nejde o hotovostní dividendu. `known_at` zůstává skutečný čas přijetí inventáře.
- Fúze: u jednoznačně označeného přežívajícího kupujícího se existující akcie
  nepřevádějí poměrem určeným akcionářům kupované firmy. Nevytváří se split ani
  peněžní dividendový efekt. Identita obou stran, datum, měna a příslušné poměry
  musí být ověřitelné. Cílová strana fúze zůstává blokovaná.

Ekonomické datum pro aktuální inventář používá `ex_date`, jinak `effective_date`,
  jinak `process_date`; process-date horizont dotazu se tím nemění.

Spin-off, unit split, práva, cílové fúze a reorganizace nelze nahrazovat libovolným
splitem: potřebují další instrumenty, ocenění nebo účetní operace. Zůstávají
explicitně nezpůsobilé. Historická SSE cesta a její seznam podporovaných typů
se nemění. Z těchto aktuálních receiptů nevzniká výzkumná readiness.

## Obnova existujících blokací

Autentizovaný ADMIN endpoint `POST /operator/market-pipeline/recheck` přijímá
`batch_id`, 1–50 unikátních `symbols` a `reason`. Přehodnotí pouze `DATA_BLOCKED`
položky nedokončené dávky. Nevolá poskytovatele, nepřepíná scheduler ani trading.

Konkrétní receipt musí náležet úloze, symbolu, instrumentu a inventárnímu rozsahu.
Ověří se SHA-256, čas, limity velikosti a předchozí cenový manifest. Výpočet se
provede k původnímu receipt času, ne s pozdějšími cenovými opravami. Nejde o nově
získaný stav u poskytovatele; evidence to označuje `PINNED_RECEIPT_NO_PROVIDER_REFRESH`.

Úspěch změní pouze stav úlohy a její odvozený výsledek. Pokusy se neresetují, ceny
ani původní receipt se nepřepisují. Ve stejné transakci vznikne immutable audit
`market_action_reviews` s původní i novou evidencí, aktérem, důvodem a verzí.
PostgreSQL odmítá UPDATE/DELETE této tabulky. Downgrade s existujícími záznamy
selže bez ztráty historie. Opakovaný požadavek nevytvoří další ekonomický efekt.
Uzavřený screening je chráněn společným transaction lockem a nelze ho změnit.

`DONE` znamená zpracování vstupů; nezaručuje `screening.eligible`. Nízká likvidita,
krátká historie a mezery zůstávají výlukami. `research_eligible` zůstává false.

## Zdroje

- Alpaca Market Data OpenAPI, `ca_event_stock_dividend.rate` výslovně uvádí extra
  shares per share held; merger schéma rozlišuje přežívajícího kupujícího a cíl:
  https://docs.alpaca.markets/us/openapi/market-data-api.json
- Oficiální SDK, jednotlivé strany unit splitů, spin-offů a fúzí:
  https://github.com/alpacahq/alpaca-py/blob/master/alpaca/data/models/corporate_actions.py

## Rozsah celého #164

Tato oprava nepřipojuje globální datový účet, nevytváří chybějící historické
členství a nezaměňuje průběžný americký import za dokončenou celosvětovou službu.
Alpaca Trading API dokumentuje pro oba akciové plány US Stocks & ETFs, nikoli
světové akciové burzy. Výzkumná validace nad širším souborem vyžaduje skutečné
historické vstupy a příslušnou lineage; pouhá změna blokací takový důkaz není.
Zdroj: https://docs.alpaca.markets/us/docs/about-market-data-api
