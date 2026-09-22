# Vysvětlení blokací tržních událostí — #164

Původní přehled slučoval většinu `DatasetInvalid` do stejné zprávy, která chybně
naznačovala výhradně chybu zdroje. Například `CORPORATE_ACTIONS_UNSUPPORTED`
znamená, že laboratoř daný typ neumí započítat; samotná data zdroje mohou být platná.
100% cenové pokrytí neznamená úplnou evidenci událostí ani způsobilost screeningu.

Při čtení existujícího stránkovaného přehledu se u `DATA_BLOCKED` zkontroluje
pouze konkrétní `current_action_receipt_id` uložené v evidenci úlohy. Vazba receiptu
na úlohu, instrument, symbol, zdroj, rozsah inventáře a jeho SHA-256 se ověří před
interpretací. Cizí nebo budoucí receipt se nepoužije. Velikost payloadu je omezená
už SQL filtrem na 262144 znaků; parser dovolí nejvýše 2000 událostí.

Kontrola popíše prokazatelně přítomnou překážku: nepodporovaný typ v požadovaném
období, chybějící/neplatné datum, duplicitní identitu nebo porušenou integritu.
Používá stejný seznam podporovaných kolekcí a stejný výběr data jako adaptér Alpaca.
Popisy typů jsou české a pevně definované. Neznámé názvy ani text výjimek se nevypisují.
Není-li překážka tímto omezeným rozborem doložená, zůstane původní popis;
průchod diagnostikou se nikdy nevydává za úplnou normalizaci nebo zrušení chyby.

GET nevolá poskytovatele, nečte přístupové údaje, nevytváří readiness, nezapisuje
evidenci ani nemění stav úloh, ceny, screening nebo paper provoz. Staré blokace se
vysvětlí bez opakování importu a bez úpravy immutable receiptů. Počty a stránkování
zůstávají nezměněné. Diagnostika popisuje uložený inventář, ne dnešní stav u zdroje.

Oficiální seznam typů a sémantika `process_date`/`data_quality`:
https://docs.alpaca.markets/us/reference/corporateactions-1

Regrese jsou v `test_vertical_slice.py`, který již běží v existujícím CI jobu `api`.
Testy používají syntetické receipty; nedokládají skutečný typ události konkrétního
titulu z uživatelovy tabulky. Rozšíření ekonomické podpory typů je samostatná práce.
