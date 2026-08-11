# Risk management Phase 4

Každý intent prochází `ProductionRiskEngine.evaluate`; broker odmítne chybějící, rejected nebo cizí decision. Výsledek je `APPROVED`, `REJECTED`, případně explicitně konfigurovatelný `MODIFIED`. Kontroly jsou cena, allowlist, staleness, halt/long-only, single order, resulting concentration, gross/net exposure, leverage, počet pozic, denní count/notional, cash, daily loss a drawdown.

`gross = Σ|quantity × price| / equity`, `net = Σ(quantity × price) / equity`. Position limit zahrnuje current + pending + proposed. Default je long-only, leverage 1 a konečné limity. Daily loss používá session-start equity, drawdown persistentní high-water mark. `HALTED` odmítá increasing order, ale dovoluje čistě risk-reducing long exit. Resume je explicitní a vyžaduje safe reconciliation. Stará, chybějící, nekladná, NaN nebo infinite cena selže uzavřeně.
