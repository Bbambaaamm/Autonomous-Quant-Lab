# Market data Phase 6

## Provider a ingest
`MarketDataProvider` odděluje discovery, daily OHLCV, corporate actions a metadata. Allowlist obsahuje fixture/CSV vrstvy a externí Stooq CSV adapter s timeoutem, omezeným retry a typovanými chybami. Standardní CI používá výhradně transport fixtures a nepotřebuje internet ani credentials. Inkrementální ingest začíná od poslední session s konfigurovatelným overlapem; každý pokus má ingestion ID a stav. Selhání nevydá žádné částečné observation jako úspěšný dataset.

Observation je immutable revision identifikovaná canonical instrument ID, providerem, session, `observed_at`, source ID/hash a ingestionem. Stejný payload je idempotentní, oprava vytvoří další revision. OHLC musí být konečné, kladné a konzistentní, volume konečný a nezáporný; non-session bar je odmítnut.

## Čas a kalendář
Všechny timestampy jsou timezone-aware UTC. Provider datum se mapuje přes `XNYSCalendar` na skutečný close v `America/New_York`; kalendář řeší víkendy, algoritmické US holidays, DST a early close po Thanksgiving a 24. prosince. Auditované období je 1970–2100 a mimo něj systém selže uzavřeně. Close signál se realizuje nejdříve na raw open další dostupné session.

## Ceny a corporate actions
Raw OHLC je jediná execution série. Signal adjustment je explicitní a používá pouze split/dividend známý (`known_at`) a účinný (`effective_at`) nejpozději k `as_of`. Split upraví quantity i unit basis; cash dividend je samostatný cash event a nesmí se současně započítat do total-return série. Symbol change zachovává instrument ID. Delisting bez executable ceny zůstává unresolved; cena se nevymýšlí.

Alpaca adapter používá oddělené zdroje faktu a času znalosti. REST `/v1/corporate-actions` poskytuje aktuální corporate-action fakta v dokumentovaném vnořeném objektu `corporate_actions` (např. `forward_splits`, `reverse_splits`, `cash_dividends`, `name_changes` a `worthless_removals`). SSE stream `/v1beta1/events/corporate-actions` poskytuje immutable envelope `event_id`, `at`, `action`, `region`, `event_type` a vnořený objekt `ca`. `known_at` se smí odvodit pouze z `at` nejnovějšího nedeklarovaného `delete` eventu pro stejnou corporate action.

Evidence ukládá `event_id`, provider, `at`, `action`, `ca.id` a SHA-256 kanonické podoby konkrétního `ca` objektu. Aktuální REST fakt dostane SSE `known_at` pouze tehdy, když jeho kanonický hash přesně odpovídá uložené SSE verzi. Chybějící evidence, `delete` nebo REST/SSE version mismatch skončí `CORPORATE_ACTION_KNOWLEDGE_UNAVAILABLE`; readiness je `FAILED`. Novější opravená REST hodnota tedy nikdy nesmí zdědit starší knowledge timestamp.

REST parametry `start` a `end` Alpaca corporate-actions API filtrují `process_date`, nikoli research `effective_at`/`ex_date`. Adapter proto research interval nepředává jako process-date interval: vyčerpá stránkovanou historii podporovaných typů pro symbol a teprve lokálně filtruje ekonomické datum (`ex_date` pro split/dividend, `process_date` pro name change/worthless removal). Tím readiness interval neprohlašuje za kompletní na základě jiného časového významu provider filtru.

`AlpacaCorporateActionStream` používá `Last-Event-Id` pro reconnect/replay. Protože replay je inkluzivní, znovu doručený cursor event se přeskočí a persistentní `event_id` zůstává idempotentní. Reconnect je bounded; transientní/provider chyby nevedou k nekonečnému retry. Standardní CI používá pouze fixture transport, nikoli externí síť.

## Snapshoty
Snapshot ukládá `as_of`, provider, calendar identity, PIT universe, rozsah, coverage, seřazený manifest observation revisions a SHA-256. Hash nezávisí na pořadí DB řádků. Pozdější correction vytvoří jiný snapshot, nikdy nezmění manifest starého. Snapshot pod minimální coverage (default 80 %) má stav `INVALID` a nesmí spustit experiment.

## Persistentní runtime a známé omezení
Produkční `PersistentMarketDataService` zapisuje ingestion, immutable revisions a corporate actions v jedné DB transakci. Deterministický scope identifikátor dělá restart stejného požadavku idempotentní; PostgreSQL advisory transaction lock serializuje stejný scope. Selhání se audituje jako `FAILED` bez observation řádků. In-memory adapter je pouze testovací/reference adapter.

Úspěšný price ingestion není důkazem úplnosti corporate actions. Samostatná immutable
`corporate_action_readiness` evidence rozlišuje `COMPLETE`, `UNSUPPORTED` a `FAILED` pro provider,
instrument, interval a knowledge cutoff. Prázdný seznam je complete pouze po úspěšném volání
provideru s `supports_actions=True`; podrobnosti popisuje
[`operational-readiness-remediation-h2-corporate-action-gate.md`](operational-readiness-remediation-h2-corporate-action-gate.md).

`DatasetSnapshotService` vybírá přes SQL window autoritativní revision známou k `as_of`. Coverage denominator je průnik session, active intervalu instrumentu a membership intervalu známého k `as_of`, nikoli kartézský součin. Prázdný nebo nedostatečně pokrytý snapshot je `INVALID`.

Kalendář zachovává interní XNYS adapter, ale autoritativní schedule poskytuje zamknutá maintained knihovna `exchange-calendars`.

## XNYS calendar and immutable evidence

`XNYSCalendar` zachovává interní API a verzovaný auditovaný schedule zahrnuje DST, standardní i podporovaná exceptional closures a special closes. Datum denního baru se normalizuje na skutečný XNYS close v UTC; close-derived signál se provede nejdříve na open následující session. Current-data accessor určuje poslední dokončenou session z kalendáře (nikoli pevnou hodinovou tolerancí) a přijímá jen `SUCCEEDED` ingestion. Research snapshot je immutable revision manifest a nikdy neslouží jako mutable current-data pohled.

## Autoritativní XNYS schedule
Všechny produkční session, holiday, exceptional closure, early close a DST časy deleguje `XNYSCalendar` na `exchange-calendars` 4.13.2 / XNYS. Identita `XNYS:exchange-calendars:4.13.2` je součástí snapshot lineage; vlastní hand-maintained schedule se nepoužívá. Immutable observations/revisions a snapshot manifesty zachovávají provider correction replay, PIT coverage a pouze corporate actions s `known_at <= as_of`; raw executable ceny zůstávají oddělené od adjusted signal cen. Current execution feed se vždy validuje zvlášť a není research snapshot.

### Phase 6 research → paper audit boundary

Autoritativní workflow je `COMPLETED/RESEARCH_ONLY` experiment → explicitní
`Phase6EligibilityService.promote()` → `PAPER_CANDIDATE` → explicitní
`DeploymentService.create()` → `PENDING_REVIEW` → explicitní `approve()` → `APPROVED` →
`ValidatedCurrentDataAccessor` → `Phase6PaperExecutionService` → existující Phase 4
`TradingCycleService` / `ProductionRiskEngine` / `PersistentPaperBroker` → reconciliation.
Promotion ani deployment nevznikají automaticky a opakovaná promotion je idempotentní.

`PAPER_CANDIDATE` není automatický deployment a `APPROVED` neobchází risk engine ani stav
`HALTED`. Research snapshot slouží pouze jako immutable lineage; current execution feed pochází z
nejnovější dokončené XNYS session a přijímá jen nejnovější revizi z úspěšné ingestion. Runtime
rekonstruuje pouze přesnou allowlisted strategii, verzi, parametry, PIT universe a USD/XNYS/1d
scope. Live trading path nadále neexistuje.
