# Operational Readiness Remediation P0 — B1 Phase 6 Control Plane

## Příčina a implementovaný workflow

Domain služby existovaly, ale runtime vystavoval jen read API a produkčně vypadající fixture demo
mutace. Nová orchestration boundary poskytuje tok `instrument → PIT universe/membership → Stooq
ingest → VALID snapshot → allowlisted Phase 6 experiment → promotion → PENDING_REVIEW deployment →
explicit approval → monitoring policy → ACTIVE enrollment`. Business logika zůstává v domain
službách.

## API, RBAC a audit

Bearer-authenticated ADMIN mutation surface je:

- `POST /operator/instruments`
- `POST /operator/universes` a `POST /operator/universes/{id}/memberships`
- `POST /operator/market-data/ingestions` a `POST /operator/datasets`
- `POST /operator/research/experiments` a `POST /operator/research/experiments/{id}/promote`
- `POST /operator/deployments` a `POST /operator/deployments/{id}/approve`
- `POST /operator/monitoring/policies` a `POST /operator/monitoring/enrollments`

Actor se odvozuje výhradně z bearer principalu; body jej nepřijímá. Každá operace vyžaduje reason.
Promotion a deployment stavové změny zapisují immutable audit ve stejné transakci; ostatní mutace
ukládají idempotentní audit evidence s actor ID/rolí, reason a correlation ID.

## Idempotence, lineage a demo hranice

Immutable registry identity failují při konfliktu a identický retry vrací původní objekt. Ingest,
snapshot, experiment, promotion, deployment, approval, policy a enrollment používají existující
content/scope identity a uniqueness invarianty. Snapshot validation ani PIT knowledge timing nelze
obejít. Experiment přijme pouze přesnou verzi z `STRATEGY_REGISTRY`, nikoli import path, a jeho
identity zahrnuje snapshot, parameter space, chronologický split, seed, code SHA a cost model.

Fixture mutace jsou explicitně `POST /demo/research/experiments` a
`POST /demo/trading/cycles/run-paper`; nejsou podporovaným Phase 6 workflow. CLI framework nebyl
zaveden. Existující UI výslednou operator evidence zobrazuje a kompletní bootstrap je dostupný přes
API bez SQL či Python shellu.

## E2E proof a zbývající scope

`backend/tests/test_b1_control_plane_postgres.py` prochází přes HTTP celý tok z čistých business
identit po ACTIVE monitoring s deterministickým providerem přes stejnou ingest boundary. Ověřuje i
viewer/unauthenticated zákaz, invalid PIT interval, enrollment před approval a retry bez duplicit.
Lineage monitoring → deployment → experiment/strategy → snapshot manifest → universe/instrument →
observation revisions zajišťují stávající FK a immutable manifesty.

B2 worker integration, H1 refresh/scheduler, H2 corporate-actions provider, H3 policy versioning a
M1 scoring redesign zůstávají otevřené. Approved deployment se automaticky neobchoduje. PAPER-only
cesta a B3 invariant `close T → next valid XNYS raw open T+1` se nemění.
