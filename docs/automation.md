# Automation & Operations Phase 5

## B2 production paper contract

Autonomní produkční paper execution používá výhradně `RUN_PAPER_DEPLOYMENT` s immutable config
`{"deployment_id": "..."}`. Job se vytváří přes
`POST /operator/deployments/{deployment_id}/jobs`; account, strategie, parametry, snapshot,
PIT universe a monitoring se vždy odvozují z persistentní approved lineage. Worker volá
`Phase6PaperExecutionService` a nikdy nepřijímá target weights, symbol, cenu ani filesystem path.

Historický `RUN_PAPER_CYCLE` zůstává rozpoznatelný pro staré snapshoty, ale produkční mutation jej
nevytvoří a worker jej permanentně odmítne. Scheduler zůstává wall-clock; market-session scheduling
a automatický data refresh patří do samostatných H1/M3 remediation.

`scheduled_jobs → job_runs → job_attempts` oddělují schedule, logical execution a fyzický pokus.
Occurrence `(job, scheduled_for)` nebo `(job, manual idempotency key)` je unikátní. Run drží
neměnný config snapshot včetně accountu, typu jobu a strategie, scheduled decision time a
correlation ID. Pozdější edit schedule proto nemění význam retry. Scheduler pouze zapisuje work.
Snapshot má verzovanou obálku, která striktně odděluje `identity` od uživatelského `config`.
Migrace `20260811_04` převede legacy runy podle jejich referencovaného ScheduledJob; runtime
neznámý nebo neversionovaný formát odmítne a nikdy nepoužije mutable fallback.

Worker claimuje deterministicky seřazený run, používá PostgreSQL `SKIP LOCKED`, lease a fencing.
Heartbeat prodlužuje pouze dosud neexpirovaný lease současného tokenu; dokončení i failure
write vyžadují tentýž token a neexpirovaný lease. Na hranici `now == lease_expires_at` je lease
expirovaný a práci musí převzít nový fenced attempt.
Po crashi může další worker zopakovat stejnou Phase 4 identitu: dokončený trading cycle se pouze
načte, takže nevznikne nový order ani fill. Transientní DB/provider přerušení používá omezený
exponenciální backoff; neplatný config/data/HALT/risk invariant je permanentní. Vyčerpaný run je
`DEAD_LETTER`.

Automation nepřijímá broker/mode/live flags a neobsahuje live adapter. API bez auth je pouze pro
chráněnou operator síť. Production vyžaduje PostgreSQL; SQLite tiše nenahrazuje concurrency
garance. Claim+attempt jsou jeden commit, ekonomický Phase 4 commit je oddělený a finální fenced
status commit následuje po něm. Neurčitý stav se obnovuje stejnou cycle identity a reconciliation.
Ruční `run-now` odmítá deaktivovaný job; již materializovaný run deaktivace neruší.

## Phase 6
Phase 6 je implementována jako provider → validace/immutable revisions → XNYS calendar/corporate actions → PIT universe → immutable snapshot → multi-asset target portfolio. Detailní invariants jsou v `docs/market-data.md` a `docs/strategy-research.md`. Žádná část nevytváří live execution path; automatický data refresh zatím není allowlistovaný job a refresh se provádí odděleně od trading cycle.
