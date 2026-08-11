# Automation & Operations Phase 5

`scheduled_jobs → job_runs → job_attempts` oddělují schedule, logical execution a fyzický pokus.
Occurrence `(job, scheduled_for)` nebo `(job, manual idempotency key)` je unikátní. Run drží
neměnný config snapshot včetně accountu, typu jobu a strategie, scheduled decision time a
correlation ID. Pozdější edit schedule proto nemění význam retry. Scheduler pouze zapisuje work.

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
