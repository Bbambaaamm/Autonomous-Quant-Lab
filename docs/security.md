# Phase 9 threat model a bezpečnostní architektura

Časově omezené upstream výjimky a jejich mitigace eviduje
[`security-exceptions.md`](security-exceptions.md); tento registr nemění blocking scanner policy.

Single operator ≠ no authentication. Systém není multi-tenant SaaS, ale production-like PAPER
control plane je vždy autentizovaný a backend autorizuje každou operaci.

## Assets a trust boundaries

Chráněnými aktivy jsou PostgreSQL data, paper account state, orders/fills, trading state,
immutable performance evidence, deployment approvals, strategy/research records, audit trail,
operator a databázové credentials, session secrets, GitHub Actions credentials a oba lockfiles.
Vstupy tvoří Next.js dashboard, FastAPI, operator mutations, research a automation/manual-run API,
PostgreSQL, GitHub Actions, manual lockfile repair, production containers a health endpoints.
Browser důvěřuje pouze Next.js boundary; backend bearer token se do browseru neposílá. Datová síť
není publikovaná a libovolným proxy headers se nedůvěřuje.

## Threats a mitigace

| Threat | Mitigace |
|---|---|
| Unauthorized API/dashboard, escalation, stolen token | opaque 256bit tokens, constant-time compare, server RBAC a signed expiring session |
| Brute force, rate-limit bypass | principal buckets, oddělený HALT/RESUME limiter; single worker; `X-Forwarded-For` se ignoruje |
| CSRF | SameSite=Strict a povinná Origin/Host kontrola server actions |
| XSS | React escaping, bez raw HTML/eval; CSP |
| SSRF/arbitrary path | server-only URL a explicitní operator allowlist |
| SQL injection | SQLAlchemy expressions a parametrizované SQL |
| Secret/log leakage | server-only env, generické auth errors, loguje se jen correlation ID |
| Dependency/lifecycle compromise | autoritativní lockfiles, `npm ci --ignore-scripts`, audit gates a SAST |
| Actions/write-token misuse | default read permissions; repair workflow odděluje verify od publish |
| Spoofed Host/proxy | explicitní trusted hosts; proxy headers jsou vypnuté |
| Audit actor spoofing/accidental RESUME | principal z middleware v audit payload; RESUME jen ADMIN + reconciliation |
| Container escalation/exposed DB | non-root, cap-drop, no-new-privileges, read-only FS, interní DB síť |
| Missing/insecure backup | pg_dump custom format, SHA-256 a explicitně potvrzený restore do určené DB |
| Fail-open/unsafe defaults | production validace tokenů, PostgreSQL a hostů; public jsou jen healthz/readyz |

Backend bearer API nepoužívá cookies, proto jeho CSRF boundary je bearer credential. CORS není
zapnuté. Limiter je záměrně process-local a produkční příkaz vynucuje přesně jeden worker/replica.
Při škálování je předem nutný sdílený limiter. HALT má vlastní bucket, aby reads neblokovaly první
emergency zásah. Všechny mutation endpointy jsou inventarizovány defaultně jako ADMIN s jedinou
užší výjimkou HALT (OPERATOR/ADMIN).

Production CSP nepovoluje `unsafe-eval`. Dočasné `style-src`/`script-src 'unsafe-inline'` je
omezené na same-origin Next.js hydration a framework styles; žádné untrusted HTML se nevkládá.
Před internetovým deploymentem se má nahradit nonce-based CSP, jakmile ji podporuje konkrétní
Next.js rendering konfigurace bez rozbití dashboardu.

Container vulnerability gate vždy vypíše všechny HIGH/CRITICAL nálezy včetně advisories bez
dostupné opravy. Následný blocking scan používá `--ignore-unfixed`, takže odmítne každý fixovatelný
HIGH/CRITICAL nález; unfixed nálezy zůstávají viditelné v CI logu a musí být triagovány při každém
novém běhu. Runtime images jsou explicitně založené na Debian Bookworm a před vytvořením uživatele
aplikují dostupné security aktualizace. Nejde o blanket potlačení reportu.

## Mutation inventory (default deny)

| Method | Path | Permission |
|---|---|---|
| POST | `/operator/risk/halt` | OPERATOR/ADMIN |
| POST | `/operator/risk/resume` | ADMIN |
| POST | `/paper/monitoring/policies` | ADMIN |
| POST | `/paper/deployments/{deployment_id}/monitoring/enroll` | ADMIN |
| POST | `/paper/monitoring/{monitoring_id}/pause` | ADMIN |
| POST | `/paper/monitoring/{monitoring_id}/resume` | ADMIN |
| POST | `/paper/monitoring/{monitoring_id}/retire` | ADMIN |
| POST | `/automation/jobs` | ADMIN |
| PATCH | `/automation/jobs/{job_id}` | ADMIN |
| POST | `/automation/jobs/{job_id}/enable` | ADMIN |
| POST | `/automation/jobs/{job_id}/disable` | ADMIN |
| POST | `/automation/jobs/{job_id}/run-now` | ADMIN |
| POST | `/automation/runs/{run_id}/retry` | ADMIN |
| POST | `/risk/halt` | OPERATOR/ADMIN |
| POST | `/risk/resume` | ADMIN |
| POST | `/trading/cycles/run-paper` | ADMIN |
| POST | `/reconciliation/run` | ADMIN |
| POST | `/api/backtests/demo` | ADMIN |
| POST | `/research/experiments` | ADMIN |
| POST | `/api/research/experiments` | ADMIN |
