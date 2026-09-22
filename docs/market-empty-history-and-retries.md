# Prázdná cenová historie a pořadí opakování — #164

## Doložený problém

Dne 22. 9. 2026 odpověděl existující Alpaca/IEX účet na dva omezené čtecí
požadavky (ACBH, SECA) HTTP 200 s `bars: null`, odpovídajícím `symbol`
a `next_page_token: null`. Původní klient považoval tento tvar za přechodný
výpadek. Nejde o důkaz zániku instrumentu ani nedostupnosti dat v jiném feedu.

Klient rozpozná jako prázdnou odpověď pouze tento úplný single-symbol tvar
na první a zároveň poslední stránce. Chybějící klíč, cizí symbol, objekt místo
seznamu nebo null během nedokončeného stránkování nadále selžou. Běžný
seznam bars a původní interpretace OHLCV se nemění.

## Evidence a další den

Pokud v dávce není ani dříve uložená cena, úloha končí samostatným stavem
`NO_PRICE_DATA`. Zůstává v celkovém jmenovateli, s nulovým cenovým pokrytím,
bez ukazatelů, bez action readiness a bez výzkumné/obchodní způsobilosti.
Uložená evidence obsahuje interval, feed, skutečný čas přijetí a identitu
importu s nulovým počtem řádků. Nevytváříme umělý nulový cenový bar.

Tato dávka nedělá další stejné retry. Další denní dávka instrument znovu
zahrne; nejde o trvalé vyřazení. Prázdný přírůstek k již uloženým cenám je
naopak RETRY: staré ceny zůstanou, ale nejsou vydány za nový úspěšný import.

## Opakování nezůstane za celým backfillem

Po vypršení lease má přerušená práce přednost; dále následuje RETRY, jehož
prodleva už uplynula, a pak PENDING. Původní řazení jen podle `retry_at`
nechávalo staré problémy čekat za všemi položkami založenými při tvorbě dávky.
Maximum tří pokusů, délka prodlev, požadavkový rozpočet, počet workerů ani
obchodní úlohy se nemění. Žádné obcházení rate limitů nebo resetování pokusů.

Nový stav je dostupný ve stávajícím autorizovaném API i českém filtru UI.
Databázová migrace není potřeba. Regrese jsou ve dvou sadách již zahrnutých
v CI: `test_vertical_slice.py` a `test_alpaca_corporate_actions.py`.

Oficiální popis stránkování a feedů:
https://docs.alpaca.markets/us/openapi/market-data-api.json
Tvar s null je zde doložen živými odpověďmi, nikoli vydáván za garanci všech
ostatních endpointů. Úplné dokončení široké dávky a nového denního běhu se
musí ověřit v provozu; samotné testy ani tato změna nejsou globálním pokrytím.
