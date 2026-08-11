# Phase 4 Audit Gate

Audit po Phase 4 identifikoval a opravil následující bezpečnostní mezery:

- broker dříve důvěřoval pouze in-memory `RiskDecision`; nyní ověřuje jeho persisted identitu, účet, cycle, intent, status, množství a correlation ID;
- přímé broker volání mohlo použít starší approval po aktivaci `HALTED`; broker nyní pod zámkem dovolí jen prodej nepřesahující skutečnou long pozici;
- nulová, záporná, NaN nebo infinite equity mohla skončit výjimkou nebo nedefinovanou aritmetikou; risk vyhodnocení nyní selže uzavřeně;
- souběžné fill zpracování nemělo produkční row locking; PostgreSQL nyní serializuje order a account cash update;
- order/fill invarianty byly pouze aplikační; migrace `20260811_02` přidává databázové check constraints;
- retry stejného logical cycle s jinými bary nebo decision time se nyní odmítne místo tichého vrácení cizího výsledku;
- reconciliation nyní kontroluje také konzistenci `FILLED` a `PARTIALLY_FILLED` statusů s množstvím.

Regresní sada pokrývá podvržený approval, HALTED bypass, neplatnou equity, DB overfill a rollback session, změněný cycle input, order-status corruption a skutečný PostgreSQL concurrent cycle. Auditní sekvence explicitně obsahuje validaci dat, vznik targetu a order intentu.
