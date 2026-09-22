# Oddělený příjem cen a způsobilost dat — issue #164

## Opravená příčina

Po #169 vytvářel `MarketPipeline.step()` objekt `ReceivedData`, který před uložením
cen synchronně načítal i corporate actions. Chyba druhého požadavku, včetně
`CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE`, zrušila konstrukci objektu. Již přijaté
ceny se do persistentního ingestionu vůbec nedostaly. Cena a evidence použitelnosti
pro výzkum tak byly nesprávně svázané.

Nové pořadí je:

1. Přijmout raw denní ceny a zachovat skutečný lokální čas přijetí.
2. Normalizovat a transakčně uložit cenové observations se stávající identitou
   instrumentu, feedem, verzí a idempotencí.
3. Samostatně ověřit corporate actions pro **celý interval dávky**, nikoli pouze
   inkrementální cenový přesah. Zapsat immutable readiness evidence.
4. Teprve po úspěšném ověření vytvořit stávající verzovaný screening.

`_PriceOnlyReceipt` je lokální pohled určený výhradně pro cenový ingestion. Explicitně
má `supports_actions=False`; volání jeho `corporate_actions()` selže. Nevrací falešný
prázdný úspěch a nesmí být předán jako důkaz úplnosti corporate actions. Původní
provider zůstává beze změny a readiness používá výhradně původní schopnosti zdroje.
Cenový payload a persistentní feed/version lineage se nemění.

## Co znamená stav v přehledu

`downloaded` a `complete_period` popisují uložené ceny, nikoli způsobilost pro výzkum.
Instrument může mít uloženou celou cenovou historii a současně stav `DATA_BLOCKED`.
Chybějící evidence ponechá `FAILED` readiness; nepodporované actions ponechají
`UNSUPPORTED`. Dočasná chyba nebo rate limit nadále používají omezené retry.

Při blokaci se uchovají skutečné počty observations, cenové pokrytí a jejich identity.
Pořadí trend/momentum/mean reversion je prázdné, `action_readiness_id` chybí,
`ACTIONS_NOT_VERIFIED` zůstává důvodem nezpůsobilosti a `research_eligible` i
`eligible_for_promotion` zůstávají false. Celkový jmenovatel se nezmenšuje.

Restart po dočasné chybě používá uložené ceny jako checkpoint. Znovu načte pouze
potřebný úsek s pěti seancemi přesahu; stejné payloady nevytvoří duplicitní revize.
Corporate actions se i tehdy kontrolují pro celý původní interval.

## Zachované ochrany

- Žádné zpětné posouvání `known_at` nebo `observed_at`.
- Žádná náhrada chybějící SSE evidence historickým datem z REST odpovědi.
- Žádná změna schvalování datasetů, výzkumu, paper deploymentu ani risk kontrol.
- Žádné nové předplatné, placená LLM volání ani live obchodování.
- Žádné automatické zapnutí pozastavené produkční fronty touto změnou.

Alpaca výslovně negarantuje okamžik vytvoření a dostupnosti corporate actions v API:
https://docs.alpaca.markets/us/reference/corporateactions-1
Úspěšné REST volání proto samo nedokládá historickou dostupnost konkrétní informace.

## Regresní ověření

`backend/tests/test_market_price_receipts.py` pokrývá uložení cen před action requestem,
chybějící evidence, výpadek/rate limit, nepodporované actions, neplatné ceny, zachování
kauzálního receipt času, obnovu po restartu bez duplicit a nezpůsobilost celého
price-only souboru. Navazují existující testy pipeline, screeningu a PostgreSQL
readiness/snapshot gate.

```sh
cd backend
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest tests/test_market_price_receipts.py tests/test_market_pipeline.py
```

Výsledek CI a ověření nasazení se zaznamenávají do PR/issue, nikoli jako předpoklad
z existence tohoto dokumentu. Syntetický test není benchmark živé široké dávky.

## Co stále brání uzavření celého #164

Tato změna opravuje vazbu M3 na M4, ale nevytváří chybějící historickou evidence.
Stále je nutné na serveru ověřit úspěšné a opakované široké cenové aktualizace,
změřit jejich skutečný čas/požadavky/kapacitu, doložit historické členství a lifecycle,
provést předem definovaný víceinstrumentový výzkum s nedotčeným OOS a ověřit
přístup i rozsah globálních datových zdrojů. Mimoamerické zdroje se nesmějí vydávat
za připojené bez skutečné konfigurace a ověření. Issue #164 zůstává otevřené.
