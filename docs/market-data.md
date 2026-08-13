# Market data Phase 6

## Provider a ingest
`MarketDataProvider` odděluje discovery, daily OHLCV, corporate actions a metadata. Allowlist obsahuje fixture/CSV vrstvy a externí Stooq CSV adapter s timeoutem, omezeným retry a typovanými chybami. Standardní CI používá výhradně transport fixtures a nepotřebuje internet ani credentials. Inkrementální ingest začíná od poslední session s konfigurovatelným overlapem; každý pokus má ingestion ID a stav. Selhání nevydá žádné částečné observation jako úspěšný dataset.

Observation je immutable revision identifikovaná canonical instrument ID, providerem, session, `observed_at`, source ID/hash a ingestionem. Stejný payload je idempotentní, oprava vytvoří další revision. OHLC musí být konečné, kladné a konzistentní, volume konečný a nezáporný; non-session bar je odmítnut.

## Čas a kalendář
Všechny timestampy jsou timezone-aware UTC. Provider datum se mapuje přes `XNYSCalendar` na skutečný close v `America/New_York`; kalendář řeší víkendy, algoritmické US holidays, DST a early close po Thanksgiving a 24. prosince. Auditované období je 1970–2100 a mimo něj systém selže uzavřeně. Close signál se realizuje nejdříve na raw open další dostupné session.

## Ceny a corporate actions
Raw OHLC je jediná execution série. Signal adjustment je explicitní a používá pouze split/dividend známý (`known_at`) a účinný (`effective_at`) nejpozději k `as_of`. Split upraví quantity i unit basis; cash dividend je samostatný cash event a nesmí se současně započítat do total-return série. Symbol change zachovává instrument ID. Delisting bez executable ceny zůstává unresolved; cena se nevymýšlí.

## Snapshoty
Snapshot ukládá `as_of`, provider, calendar identity, PIT universe, rozsah, coverage, seřazený manifest observation revisions a SHA-256. Hash nezávisí na pořadí DB řádků. Pozdější correction vytvoří jiný snapshot, nikdy nezmění manifest starého. Snapshot pod minimální coverage (default 80 %) má stav `INVALID` a nesmí spustit experiment.
