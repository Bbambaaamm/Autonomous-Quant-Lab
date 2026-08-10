# Research metodologie

Framework hodnotí historickou citlivost, nikoli očekávaný budoucí výnos.

* **IS/validation/OOS:** části jsou chronologické a disjunktní. IS je pro vývoj, validation pro
  výběr a OOS jen pro konečné měření.
* **Walk-forward:** train/validation/test okna a step vytvářejí auditované timestamp hranice.
  Parametry se nesmějí optimalizovat na test okně.
* **Náklady:** default je fixed + procentní komise a 5 bps adverse slippage. Stress obsahuje base,
  2× komisi, 2×/3× slippage a kombinaci 2×/3×.
* **Monte Carlo:** seedovaný empirical bootstrap trade returns s náhradou zachovává počet obchodů.
  Reportuje medián a 5. percentil terminal equity, 95. percentil absolutního max drawdownu a
  pravděpodobnost ztráty. Neřeší režimové změny ani závislost obchodů.
* **Stabilita:** sousedství má vzdálenost nejvýše jedna v každém celočíselném parametru. Výstupem
  je počet sousedů, medián, populační variance a profitabilní podíl, nikoli falešné skóre.
* **Eligibility:** všechny konfigurovatelné podmínky musí projít pro paper kandidáta. Výsledek
  nikdy nepovoluje live trading.

Eligibility hranice jsou inkluzivní (`trades >= minimum`, `OOS return >= minimum`, absolutní
drawdown `<= maximum` a poměry `>= minimum`). Objective `return_weight × total_return +
sharpe_weight × Sharpe − drawdown_weight × |maximum_drawdown|` je pouze transparentní,
konfigurovatelná ranking heuristika. `Sharpe=None` přispívá nulou, nedostatečný počet obchodů
vrací nehodnotitelné skóre a shody rozbíjí kanonická reprezentace parametrů.

## Účetní invarianty

Po každém fillu a corporate action platí `equity = cash + market value otevřených pozic`.
Nákup snižuje cash o fill notional a komisi, prodej ji zvyšuje o fill notional minus komisi.
Výstupy se párují s loty FIFO. Split násobí quantity a stejným poměrem dělí unit basis, takže
sám nevytváří P&L. Dividenda se připíše pouze z quantity otevřené v effective timestampu a
identická corporate action se nesmí aplikovat dvakrát. Slippage je již zahrnuta v nepříznivé
fill ceně; `slippage_cost` je auditní attribution a z equity se podruhé neodečítá.

Monte Carlo se pod konfigurovaným minimem uzavřených FIFO obchodů vrací jako
`NOT_EVALUATED` s důvodem `insufficient_closed_trades`; na přesné hranici se spustí.

Metriky používají 252 období pro anualizaci denních returns a 365,25 dne pro CAGR. Sharpe má
risk-free předpoklad nula, Sortino downside deviation vůči nule. Nulový jmenovatel, chybějící
uzavřené obchody nebo nedostatečný vzorek vrací `None`.

## Walk-forward selection v Phase 2.7

V train se auditovatelně vyhodnotí všechny raw kombinace, včetně explicitního `INVALID_CONFIG`.
Konfigurované top-k se znovu vyhodnotí ve validation. Vítěz validation se zamkne a OOS se spustí
právě jednou. OOS intervaly se nesmějí překrývat a benchmark používá tutéž množinu OOS barů.
Cost stress je nový backtest každého OOS foldu s jeho selected configem.

Experiment identity zahrnuje dataset content hash, strategy name/version, celý parameter space,
walk-forward/objective/top-k konfiguraci, cost a stress modely, seed, Monte Carlo, eligibility
a engine version. Identické vstupy reprodukují foldy, runy, fills, agregaci i robustness výstupy.

Každá eligibility podmínka je samostatný typovaný záznam se statusem, observed hodnotou, prahem
a volitelným důvodem. `not_evaluated` je odlišné od `failed` a nikdy se nepočítá jako průchod.
Snapshot experimentu je neměnný; strukturované tabulky identity, foldů a kontrol jsou jeho
dotazovatelnou projekcí, nikoli druhým zdrojem research pravdy.
