# QuantLab runtime resource budget

## Baseline — 24. 9. 2026

Staging host: 4 vCPU, 7.6 GiB RAM, bez swapu.

Měřeno po #189/A3.1–A3.4/A7:

| Proces | Steady RSS |
| --- | ---: |
| Backend API | ~193,520 KiB (~189 MiB) |
| Automation worker | ~174,248 KiB (~170 MiB) |
| Alpaca listener | ~167,856 KiB (~164 MiB) |
| Frontend | ~98,968 KiB (~97 MiB) |
| Host MemAvailable | ~5.6 GiB |

Listener baseline před #189 byl ~1.48 GiB RSS; po #189 stabilně ~164–169 MiB.

## Backpressure

- Market child: host MemAvailable >= **1536 MiB**, worker cgroup headroom >= **512 MiB**.
- Research child: host MemAvailable >= **2048 MiB**, backend cgroup headroom >= **768 MiB**.
- Long-lived worker soft RSS watermark: **400 MiB** → graceful process exit/restart.
- Heavy market concurrency: **1** díky jednomu automation workeru a one-shot child lifecycle.
- Heavy research concurrency: **1** přes PostgreSQL session-level advisory admission slot. Slot se drží po celou kritickou sekci `admission → capacity check → research child → persisted result/audit`; druhý heavy research request fail-closed vrací `RESEARCH_CONCURRENCY_LIMIT` a nespustí child ani další capacity check.
- Research admission po dobu heavy jobu rezervuje **jedno backend PostgreSQL connection** pro session-level advisory lock. Jde o bounded režii jednoho připojení, ne o in-memory queue; po success/error/timeout se connection uvolní a při zániku procesu PostgreSQL lock odstraní se session. Tento connection budget musí zůstat zahrnutý při změnách backend poolu nebo při zvyšování research concurrency.
- Phase 6 immutable snapshot verification používá nejvýše **1000 observation/action/instrument IDs na jeden DB batch**.
- Phase 6 nejdříve ověří canonical hash celého manifestu a poté již ověřené observation entries uvolňuje po DB batchi; nedrží vedle observations druhou workload-sized množinu všech ID.
- Opakované observation identity (`instrument_id`, provider, timeframe, ingestion) jsou během převodu ORM řádků internovány, unikátní provenance zůstává nezměněná.
- Train/validation/OOS vyhodnocení používá jeden načtený observation dataset s explicitním `evaluation_end`; nevytváří nový full-prefix list pro každý běh.
- Snapshot content SHA-256 se počítá inkrementálně přes canonical JSON encoder bez druhé materializované kopie celého immutable JSON payloadu.
- Povinný broad-research gate používá `Phase6ExperimentRunner.replay` nad 2 000 instrumenty × 250 skutečnými XNYS sessions (500 000 barů). Lokální acceptance po této změně: **778.2 MiB child peak RSS**, **53.918 s wall time**, 2 000/2 000 instrumentů s fill evidence; merge budget zůstává 1 024 MiB / 180 s a autoritativní výsledek publikuje CI artifact.

## Hard container limits

| Service | RAM max | CPU max |
| --- | ---: | ---: |
| PostgreSQL | 2 GiB | 2.0 CPU |
| Backend API + research child | 1536 MiB | 1.5 CPU |
| Automation worker + market child | 1024 MiB | 1.5 CPU |
| Alpaca listener | 384 MiB | 0.5 CPU |
| Frontend | 384 MiB | 0.75 CPU |

Hard limity jsou poslední pojistka. Běžný steady-state je výrazně pod nimi.

## Soak evidence

`scripts/staging-resource-sample.sh` vrací jeden JSON řádek se timestampem, host MemAvailable, load average a PID/RSS/CPU time dlouho žijících procesů.

24h a 72h gate musí navíc zaznamenat:
- `/readyz` a dashboard HTTP latency;
- restart count kontejnerů;
- DB velikost a klíčové row counts;
- queue state a failed/dead-letter jobs;
- listener RSS bez monotónního trendu;
- worker RSS návrat k baseline po heavy child jobu;
- žádný OOM kill.

#190 se nesmí uzavřít před skutečným 24h a 72h měřením.
