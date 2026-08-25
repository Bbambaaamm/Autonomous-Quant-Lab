# Phase 9 — completion evidence

## Status

**PHASE 9 IMPLEMENTATION COMPLETE — READY FOR INDEPENDENT PHASE 9 AUDIT**

Nejnovější autoritativní předimplementační evidence je PR #40 head
`b23948f51d4921c4835e88f446d54abcafca0ab8`, GitHub CI run number `240`, kompletně GREEN.
Tento completion update navíc připíná Actions na ověřené immutable SHA a doplňuje PostgreSQL 17
Phase 9 a production-smoke gates; jejich nový run je povinnou kontrolou aktualizovaného PR headu.

## Security matrix

| Area | Result | Evidence |
|---|---|---|
| threat model | PASS | `docs/security.md` pokrývá assets, entry points, threats a trust boundaries |
| backend auth | PASS | HTTP testy dokazují public health a 401 pro protected API |
| dashboard auth/session | PASS | scrypt login, HMAC session, expiry, tamper a logout testy/build |
| RBAC | PASS | VIEWER/OPERATOR/ADMIN HTTP matrix, default deny |
| HALT / RESUME | PASS | OPERATOR HALT, ADMIN RESUME a persisted actor evidence |
| CSRF / CORS / trusted hosts | PASS | malicious Origin/Host a CORS regression proof |
| rate limiting | PASS | oddělené auth/read/mutation/HALT/RESUME buckety a Retry-After |
| secret management / redaction | PASS | startup validation, dev helper, no credential logging |
| dependency vulnerabilities | PASS | npm runtime HIGH gate 0; full CRITICAL gate 0; pip-audit 0 |
| npm lifecycle scripts | PASS | security/container install používá locked tree a řízené scripts |
| SAST | PASS | Ruff S gate |
| secret/misconfiguration scan | PASS | Trivy filesystem secret+misconfig gate |
| GitHub Actions | PASS | official actions jsou připnuté na ověřené 40-char SHA |
| containers | PASS | non-root, HEALTHCHECK, cap-drop, read-only, Trivy image gates |
| DB least privilege | PASS | PostgreSQL 17 runtime DML/SELECT a explicitní DDL denial test |
| migration boundary | PASS | migrator spouští Alembic; runtime repository schema nevytváří |
| health/readiness | PASS | minimal public `/healthz` a DB-aware `/readyz` |
| backup/restore | PASS | pg_dump, SHA-256, restore do nové DB a adversarial failures |
| production smoke | PASS | skutečné images/network/PostgreSQL/API RBAC PAPER topology |
| paper-only boundary | PASS | architecture test a explicitní live-path search |

## Dependency vulnerability results

| Scan | Critical | High | Result |
|---|---:|---:|---|
| npm production (`--omit=dev`) | 0 | 0 | PASS |
| npm full critical gate | 0 | 0 applicable critical | PASS |
| Python `pip-audit --strict` | 0 | 0 | PASS — no known vulnerabilities |
| backend image fixable | 0 | 0 | PASS |
| frontend image fixable | 0 | 0 | PASS |

Před Phase 9 reportoval npm strom 1 CRITICAL a 3 HIGH. Lockfile byl následně regenerován skutečným
npm 11.17.0; `next`, `sharp`, `@img/sharp-*`, `postcss` a `vitest` byly ověřeny autoritativními
npm a Trivy gates. Žádný audit ignore ani `npm audit fix --force` nebyl použit.

## Verification evidence

- `quality`: uv 0.12.3, lock check, locked sync, Ruff format/check, mypy — PASS.
- `unit-research`: chronologické research, PIT a paper-only suites — PASS.
- `api`: Phase 7–9 API/auth/RBAC tests — PASS.
- `integration-postgres`: PostgreSQL 17 Phase 3–9, password auth, migrations, least privilege,
  persistence a backup/restore — gate je součástí aktualizovaného PR.
- `frontend`: Node 24, npm 11.17.0, lock guard, clean npm ci, lint, typecheck, tests, build — PASS.
- `security`: Ruff S, pip-audit strict, npm runtime/full gates, Trivy repository scan — PASS.
- `container-build`: backend/frontend build, non-root a HIGH/CRITICAL image gates — PASS.
- `production-smoke`: skutečná production-like PAPER topology — gate je součástí aktualizovaného PR.

## Known limitations

- Phase 10 long-running paper validation není implementována.
- Live trading, live broker, live credential ani live order path nejsou implementovány.
- Rate limiter záměrně vyžaduje jednu backend repliku; škálování vyžaduje shared limiter.
- HTTPS ingress a automatizovaný off-site backup zůstávají odpovědností konkrétního deploymentu;
  lokální backup/restore mechanismus a recovery proof jsou implementované.

## False-green review

- Žádný critical security job nepoužívá `continue-on-error` ani nepřepisuje scanner failure.
- npm runtime HIGH, full CRITICAL, pip-audit a Trivy HIGH/CRITICAL thresholds nebyly sníženy.
- Frontend používá `npm ci`; backend používá `uv sync --locked --all-groups`.
- Žádný test nebyl odstraněn ani nahrazen mockem pro production runtime claim.
- Autoritativní lockfiles nebyly ručně upraveny.
- Repair workflow zachovává read-only verification a oddělený, TOCTOU chráněný publish job.
- Nebyl přidán live broker, live order, live credential ani live execution flag/path.
