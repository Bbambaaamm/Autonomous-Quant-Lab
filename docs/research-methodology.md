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

Metriky používají 252 období pro anualizaci denních returns a 365,25 dne pro CAGR. Sharpe má
risk-free předpoklad nula, Sortino downside deviation vůči nule. Nulový jmenovatel, chybějící
uzavřené obchody nebo nedostatečný vzorek vrací `None`.
