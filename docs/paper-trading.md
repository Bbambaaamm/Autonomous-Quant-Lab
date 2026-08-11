# Persistentní PaperBroker

Účet persistuje cash, equity, high-water mark, realized P/L a trading state. Pozice uchovává množství, průměrnou zbývající basis a FIFO lots. Lifecycle je `SUBMITTED → PARTIALLY_FILLED → FILLED`; otevřený order lze idempotentně zrušit, filled nikoli.

MARKET používá povolenou raw executable cenu a nepříznivou bps slippage. LIMIT BUY filluje při `low <= limit`, SELL při `high >= limit`; gap respektuje open. OHLC neurčuje intrabar pořadí. Capacity je deterministický podíl volume. Komise vzniká jednou per fill; vstupní komise je v lot basis a výstupní v realized P/L. Fill, cash, FIFO/position, order, equity a audit jsou atomické. Unique client ID a `(order, sequence)` brání duplicitě.
