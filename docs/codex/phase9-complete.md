# Phase 9 — completion evidence

## Status

**PHASE 9 IMPLEMENTATION INCOMPLETE**

**BLOCKED**

Lokální environment nemá požadovaný `uv 0.12.3`, Docker ani přístup k npm/Python advisory a
package registry endpoints. Nelze proto pravdivě uzavřít dependency remediation, container scan,
PostgreSQL 17 recovery proof ani production smoke. CI gates jsou připravené, ale před nezávislým
auditem musí být zelené a Actions references musí být po online ověření připnuté na immutable SHA.

## Security matrix

| Area | Result | Evidence |
|---|---|---|
| threat model | IMPLEMENTED | `docs/security.md` |
| backend auth / RBAC | IMPLEMENTED, verification pending | `security.py`, Phase 9 HTTP tests |
| dashboard auth/session | IMPLEMENTED, verification pending | scrypt login, HMAC expiry, secure cookie |
| HALT / RESUME authorization | IMPLEMENTED | OPERATOR/ADMIN vs ADMIN a actor payload |
| CSRF / CORS / trusted hosts | IMPLEMENTED | same-origin action guard, no CORS, host allowlist |
| rate limiting | IMPLEMENTED | separate read/mutation/HALT/RESUME buckets |
| secret management / log redaction | IMPLEMENTED | ephemeral dev tokens, helper, generic logging |
| dependency vulnerabilities | BLOCKED | registry audit vrátil HTTP 403 |
| npm lifecycle scripts | IMPLEMENTED, build pending | `npm ci --ignore-scripts` |
| Python scan / SAST / secret scan | PARTIAL | CI pip-audit + Ruff S; history scanner pending |
| GitHub Actions hardening | PARTIAL | read permissions; immutable SHA pinning pending network verification |
| containers | IMPLEMENTED, scan/smoke pending | multi-stage non-root images, hardened Compose |
| DB least privilege | IMPLEMENTED, proof pending | migration/runtime grants script |
| health/readiness | IMPLEMENTED | minimal public `/healthz`, `/readyz` |
| backup/restore | IMPLEMENTED, proof pending | pg_dump/pg_restore + checksum + confirmation |
| production smoke | BLOCKED | Docker není dostupný |
| paper-only boundary | PASS by code review; full gate pending | žádná live execution cesta nepřidána |

## Dependency vulnerability table

Před Phase 9: **3 HIGH, 1 CRITICAL** dle autoritativního Phase 8 reportu.

Po Phase 9: **UNKNOWN / BLOCKED BY ENVIRONMENT**. `npm audit --json` skončil HTTP 403; nuly nejsou
odhadnuté ani deklarované.

GitHub CI následně potvrdilo tři runtime HIGH nálezy v `next@16.1.6`, jeho transitive `postcss`
a `sharp`. Oprava je dostupná v `next@16.3.2`, ale lokální registry odmítá npm 11.17.0 i package
metadata HTTP 403. Podle lockfile policy proto dependency manifest ani lockfile nebyly změněny;
remediation musí projít autorizovaným npm 11.17.0 repair workflow a čistým `npm ci`.

## Known limitations

- Phase 10 není implementována.
- Live trading není implementován.
- HTTPS ingress a off-site/scheduled backup jsou odpovědností konkrétního deploymentu.
- Dokud neprojdou dependency, PostgreSQL, container a smoke gates, Phase 9 není připravena k auditu.
