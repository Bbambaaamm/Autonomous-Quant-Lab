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

## Persistentní runtime a známé omezení
Produkční `PersistentMarketDataService` zapisuje ingestion, immutable revisions a corporate actions v jedné DB transakci. Deterministický scope identifikátor dělá restart stejného požadavku idempotentní; PostgreSQL advisory transaction lock serializuje stejný scope. Selhání se audituje jako `FAILED` bez observation řádků. In-memory adapter je pouze testovací/reference adapter.

`DatasetSnapshotService` vybírá přes SQL window autoritativní revision známou k `as_of`. Coverage denominator je průnik session, active intervalu instrumentu a membership intervalu známého k `as_of`, nikoli kartézský součin. Prázdný nebo nedostatečně pokrytý snapshot je `INVALID`.

Kalendář je nadále interní algoritmický XNYS adapter. Není úplnou historickou databází special closures, takže produkční calendar requirement Phase 6 zůstává otevřený; dependency nebyla přidána bez úspěšného locked sync.

## XNYS calendar and immutable evidence

`XNYSCalendar` zachovává interní API, ale schedule, DST, standardní i exceptional closures a special closes čerpá z maintained `exchange-calendars`. Datum denního baru se normalizuje na skutečný XNYS close v UTC; close-derived signál se provede nejdříve na open následující session. Current-data accessor určuje poslední dokončenou session z kalendáře (nikoli pevnou hodinovou tolerancí) a přijímá jen `SUCCEEDED` ingestion. Research snapshot je immutable revision manifest a nikdy neslouží jako mutable current-data pohled.
