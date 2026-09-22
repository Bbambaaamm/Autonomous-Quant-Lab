# M7 — reprodukovatelná víceinstrumentová validace pro #164

## Účel a předem daná pravidla

Tento experiment ověřuje výzkumnou infrastrukturu na čtyřech širokých US ETF:
DIA, IWM, QQQ a SPY. Sada byla zvolena podle role širokých benchmarkových ETF a
dlouhé historie, nikoli podle výsledku ve zkoumaném období.

Rozsah cen je 18. 8. 2025–21. 9. 2026. Chronologický split je 60 % train,
20 % validation a 20 % nedotčený OOS. Experimentální rozpočet je přesně čtyři
konfigurace cross-sectional momentum: lookback 63/126 a top_n 1/2.
Výběr konfigurace používá pouze validation Sharpe; OOS se do výběru nezapojuje.

Počáteční kapitál je 100 000 USD, komise 1 bp. Benchmark je měsíčně
rebalancované equal-weight portfolio stejných čtyř ETF se stejnou komisí.
## Price-return hranice

Alpaca current REST receipts pro tyto čtyři instrumenty obsahují v testovaném
intervalu pouze cash dividends. Historické announcement knowledge times těchto
dividend nejsou dostupné, proto je experiment výslovně **price-return-only**:
cash distributions se nepřičítají ani strategii, ani benchmarku.

Před během se ověří hash, scope, receipt time a všechny události. Jakýkoli split,
symbol change, delisting nebo jiný cenově diskontinuální typ v intervalu experiment
failne. Receipt se nikdy nepoužije jako důkaz, že dividendová informace byla známá
v minulosti. Výsledek není research promotion ani investiční schválení.

Observations jsou připnuté podle observation ID/revision/source hash k explicitnímu
`as_of`; runner pracuje pouze read-only a nepřepisuje dataset, deployment ani paper účet.
## PIT členství a zdroje existence

Validace používá POINT_IN_TIME_MEMBERSHIP. `valid_from` i `known_at` jsou veřejně
doložená data vzniku ETF a všechna předcházejí testovanému období:

- SPY — 22. 1. 1993, State Street:
  https://www.ssga.com/us/en/institutional/etfs/state-street-spdr-sp-500-etf-trust-spy
- DIA — 14. 1. 1998, State Street:
  https://www.ssga.com/us/en/institutional/etfs/state-street-spdr-dow-jones-industrial-average-etf-trust-dia
- QQQ — 10. 3. 1999, Invesco:
  https://www.invesco.com/qqq-etf/en/home.html
- IWM — 22. 5. 2000, iShares:
  https://www.ishares.com/us/products/239710/

Tato čtyřčlenná validační sada nedokládá historické členství celého trhu.
## Reprodukovatelnost

`scripts/run-m7-market-validation.py` vyžaduje batch ID, explicitní `as_of`
a plný code SHA. Výstup obsahuje observation hash, immutable receipt IDs/hashes,
membership sources, experimentální budget, vybranou konfiguraci, pre-OOS metriky,
OOS metriky strategie i benchmarku a výsledný report hash.

Stejná databázová evidence, `as_of` a code SHA musí vytvořit stejný report hash.
Produkční ověření se provádí dvakrát za sebou v read-only DB režimu. Výsledek se
nesmí použít k automatické změně schváleného paper deploymentu.

M7 tím dokládá reprodukovatelnou multi-instrumentovou validační cestu pro #74.
Neřeší samo o sobě M8, kompletní historický universe ani globální coverage.
