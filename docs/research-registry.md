# Research registry

Dataset je identifikován content hashem. Shodná registrace je idempotentní a konflikt stejného ID
selže uzavřeně. `universe` připravuje registry na budoucnost, engine však zůstává single-symbol.
DB ukládá lineage a storage URI, nikoli samotné bary.

Experiment lze filtrovat podle strategie, verze, datasetu a eligibility; limit je nejvýše 200.
Policy `eligibility-consistency-v1` řadí lexikograficky: eligibility class, kladný OOS, přežití
cost stressu, profitable-neighbor ratio, absolutní drawdown, Sharpe a experiment ID. Nevzniká
magické profit score a pořadí není důkaz budoucí profitability.

Comparison čte persisted snapshoty. Lineage vrací dataset/hash, strategii/verzi, parameter space,
ResearchConfig, engine version, náklady a seed.
