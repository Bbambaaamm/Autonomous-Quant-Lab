# AUTONOMOUS QUANT LAB
# PHASE 9 — SECURITY & PRODUCTION HARDENING
# COMPLETE IMPLEMENTATION SPECIFICATION

Pracuj přímo v aktuálním repository:

Bbambaaamm/Autonomous-Quant-Lab

Aktuální `main` je jediný zdroj pravdy.

Očekávaný výchozí stav po Phase 8 audit merge:

1f4fb23b73ce150e9f851cddbc8e709489ad6e4a

Pokud je aktuální `main` novější, použij aktuální `main`.
Nikdy nevracej repository na starší commit.

---

# 0. KONTEXT

Phase 8 byla dokončena a nezávislý audit skončil:

PHASE 8 COMPLETE
AUDIT PASSED WITH FIXES
READY FOR PHASE 9

Phase 8 prokázala:

- PAPER-only execution boundary;
- PostgreSQL persistence;
- HALT/RESUME persistence;
- concurrency proof;
- immutable Phase 7 performance;
- XNYS-aware data health;
- operator API;
- Next.js dashboard;
- frontend lockfile hardening;
- bezpečný lockfile repair workflow;
- zelený backend/frontend/PostgreSQL CI.

Aktuální autoritativní ekonomická cesta zůstává:

Strategy
→ Portfolio
→ RiskEngine
→ ExecutionEngine
→ PersistentPaperBroker

TUTO CESTU NEMĚŇ.

Phase 9 je:

SECURITY & PRODUCTION HARDENING

pro production-like PAPER provoz.

Phase 9 NENÍ live trading.

Phase 9 NENÍ Phase 10 long-running paper validation.

Phase 9 NENÍ Live Trading Readiness Gate.

---

# 1. ABSOLUTNÍ SAFETY INVARIANT

Musí nadále platit:

DEFAULT = PAPER ONLY

V repository nesmí vzniknout:

- live broker;
- live adapter;
- broker API credentials;
- live order endpoint;
- live deployment mode;
- hidden live feature flag;
- runtime cesta, která umí poslat skutečný order;
- test, který komunikuje se skutečným brokerem.

Neimplementuj:

TRADING_MODE=live

ani:

LIVE_TRADING_ENABLED=true

ani žádný jejich funkční ekvivalent.

Pokud v dokumentaci existují historické zmínky o budoucím live tradingu, mohou zůstat pouze jako budoucí architektonický koncept.

Phase 9 musí produkčně hardenovat pouze PAPER platformu.

Na konci proveď explicitní code search a vrať:

PAPER-ONLY BOUNDARY: PASS / FAIL

---

# 2. STARTING STATE

Před jakoukoli změnou zaznamenej:

git status
git rev-parse HEAD
git branch --show-current
git log -10 --oneline

Ověř:

- pracovní strom je čistý;
- branch vychází z aktuálního main;
- docs/codex/phase8-complete.md obsahuje:
  PHASE 8 COMPLETE
  AUDIT PASSED WITH FIXES
  READY FOR PHASE 9
- frontend/package-lock.json je kompletní;
- backend/uv.lock je autoritativní;
- frontend/package-lock.json není placeholder;
- žádná Phase 9 implementace už není částečně přítomná z paralelní branche.

Do výsledku uveď Starting HEAD.

---

# 3. NEJDŘÍVE THREAT MODEL

Nezačínej implementací authentication middleware bez pochopení trust boundaries.

Vytvoř:

docs/security.md

Dokument musí identifikovat minimálně:

## Assets

- PostgreSQL data;
- paper account state;
- orders/fills;
- trading state;
- immutable performance evidence;
- deployment approvals;
- strategy/research records;
- audit trail;
- operator credentials;
- database credentials;
- session secrets;
- GitHub Actions credentials;
- dependency lockfiles.

## Entry points

- Next.js dashboard;
- FastAPI;
- operator mutations;
- research API;
- automation/manual-run API;
- PostgreSQL;
- GitHub Actions;
- manual lockfile repair workflow;
- production containers;
- health endpoints.

## Threats

Minimálně:

- unauthorized API access;
- unauthorized dashboard access;
- privilege escalation;
- stolen bearer token;
- brute-force login;
- CSRF;
- XSS;
- SSRF přes server-side API client;
- arbitrary backend URL/path access;
- SQL injection;
- secret leakage;
- dependency compromise;
- malicious npm lifecycle script;
- vulnerable transitive dependency;
- GitHub Actions supply-chain compromise;
- write-token misuse;
- spoofed proxy headers;
- rate-limit bypass;
- audit actor spoofing;
- accidental RESUME;
- container privilege escalation;
- exposed PostgreSQL;
- missing/insecure backup;
- configuration fail-open;
- unsafe production defaults.

Také explicitně zdokumentuj:

Single operator ≠ no authentication.

Projekt není multi-tenant SaaS, ale production-like PAPER control plane musí být autentizovaný a autorizovaný.

---

# 4. PHASE 9 AUTHENTICATION ARCHITECTURE

Implementuj jednoduchou, auditovatelnou a production-appropriate authentication architekturu pro single-operator systém.

Nevytvářej:

- user registration;
- SaaS accounts;
- billing;
- password reset email service;
- social login;
- external identity SaaS dependency;
- komplexní IAM platformu.

Preferuj malé, explicitní security primitives.

## Backend API authentication

Všechny citlivé API endpointy musí vyžadovat autentizaci.

Povol veřejně pouze minimální health endpoints typu:

GET /healthz
GET /readyz

Ty nesmí vracet:

- connection string;
- credentials;
- environment variables;
- exception detail;
- DB hostname;
- stacktrace.

Všechny ostatní API routes musí být protected, pokud není explicitně zdokumentovaný bezpečný důvod.

Použij standardní:

Authorization: Bearer <opaque-high-entropy-token>

nebo ekvivalentně bezpečný mechanismus.

NEPOUŽÍVEJ token:

- v query stringu;
- v URL;
- v logu;
- v audit payload;
- v browser JavaScript bundle.

Token musí mít dostatečnou entropii.

Production config musí odmítnout:

- krátké tokeny;
- placeholder tokeny;
- `changeme`;
- prázdné tokeny;
- duplicity mezi rolemi.

Porovnání secrets musí používat constant-time comparison.

401:

- chybějící/neplatné credentials.

403:

- credentials jsou platné, ale role nemá permission.

U 401 použij korektní:

WWW-Authenticate: Bearer

Nikdy nevracej informaci, která pomůže hádat platný token.

---

# 5. RBAC

Implementuj backend-authoritative RBAC.

Minimální role:

VIEWER
OPERATOR
ADMIN

Žádná:

LIVE_TRADER

nebo obdobná role.

## VIEWER

Může:

- authenticated read API;
- operator projections;
- paper performance;
- strategies;
- research read;
- risk read;
- data-health;
- audit read;
- automation read.

Nesmí měnit stav.

## OPERATOR

Má VIEWER práva plus bezpečné provozní akce.

Minimálně může:

HALT

HALT je safety-reducing action směrem k bezpečnějšímu stavu.

OPERATOR nesmí automaticky dostat oprávnění k:

- RESUME;
- deployment approval;
- operaci, která aktivuje trading;
- změně safety policy;
- změně auth konfigurace.

## ADMIN

Má VIEWER + OPERATOR práva a může provádět existující privilegované PAPER mutations, například pokud v API skutečně existují:

- RESUME;
- deployment approve;
- strategy lifecycle privileged transition;
- automation run-now;
- jiné state-increasing operations.

Neinventuj nové business mutations.

## Povinně

Proveď inventory VŠECH existujících:

POST
PUT
PATCH
DELETE

endpointů.

Každému přiřaď explicitní permission.

Pokud je endpoint nejasný, použij vyšší oprávnění.

Default:

DENY.

Žádná autorizace nesmí být pouze UI-only.

---

# 6. DASHBOARD AUTHENTICATION

Next.js dashboard nesmí být veřejný.

Implementuj bezpečný single-operator login/session mechanismus.

Požadavky:

- `/login`;
- `/logout`;
- všechny operator stránky protected;
- role je server-side evidence;
- browser nesmí rozhodovat o své roli;
- session po loginu musí být nová;
- logout session invaliduje/odstraní;
- session musí mít expiraci;
- session secret nesmí být v browser bundle.

Nepoužívej plaintext uložené operator heslo.

Použij bezpečný password hash:

- scrypt;
- Argon2id;
- bcrypt;

nebo ekvivalentní prověřený mechanismus.

Pokud lze bezpečně použít runtime standard library bez nové dependency, preferuj ji.

Nevymýšlej vlastní password hashing algorithm.

Session cookie v production musí být:

HttpOnly
Secure
SameSite=Strict

nebo bezpečně odůvodněné `Lax`.

Preferuj:

__Host-

cookie prefix, pokud je kompatibilní.

Cookie:

- Path=/;
- žádná široká Domain;
- rozumný max-age;
- žádné credentials v payload.

Session signing/encryption secret:

- minimálně 256bit entropy;
- production startup musí failnout při chybějícím/weak secret.

---

# 7. FRONTEND → BACKEND AUTH BOUNDARY

Browser NESMÍ dostat backend API token.

Browser komunikuje pouze s Next.js application boundary.

Server-side Next.js klient používá server-only backend credential.

KRITICKÉ:

Pokud implementuješ role-specific server credentials, credential použitý backendem nesmí být privilegovanější než role dashboard session.

Například:

VIEWER session
→ viewer backend principal

OPERATOR session
→ operator backend principal

ADMIN session
→ admin backend principal

Nepoužívej univerzální ADMIN token pro všechny dashboard sessions, pokud frontend podporuje více rolí.

Pro single-admin production deployment je možné mít pouze jeden skutečně provisioned operator principal, ale architecture/tests musí dokazovat RBAC.

---

# 8. ACTOR IDENTITY V AUDITU

State-changing operator actions musí být auditovatelné i z pohledu security identity.

HALT/RESUME a další privilegované PAPER mutations musí zaznamenat minimálně:

actor_id
actor_role
authentication context / principal identity

Nikdy:

raw token
password
session secret

Pokud stávající AuditEvent umožňuje bezpečně přidat actor evidence do payload bez schema změny, preferuj minimální řešení.

Pokud je schema změna skutečně nutná, proveď normální Alembic migraci.

Legacy/system events musí zůstat čitelné.

Automatický system event se nesmí tvářit jako lidský operator event.

---

# 9. HALT / RESUME SECURITY

Zachovej Phase 8 safety semantics.

HALT:

- OPERATOR nebo ADMIN;
- explicit confirmation;
- reason;
- KILL_SWITCH_MANUAL_HALT;
- persistent;
- idempotent/safe.

RESUME:

- pouze ADMIN;
- explicit confirmation;
- reason;
- fail-closed reconciliation validation;
- persistent audit;
- nesmí obejít suspended monitoring;
- nesmí obejít relevantní risk/safety state.

Authentication nesmí oslabit Phase 4 safety.

Rate limiter nesmí způsobit, že legitimní první emergency HALT bude blokován kvůli vyčerpanému obecnému read bucketu.

HALT musí mít oddělený, bezpečný limit.

---

# 10. CSRF

Dashboard používá cookie session, proto state-changing browser operations musí mít skutečnou CSRF ochranu.

Minimálně:

- same-origin validation;
- Origin/Host validation;
- případně CSRF token;
- žádný wildcard trusted origin.

Proveď regression test:

session cookie + malicious Origin
→ mutation odmítnuta.

Například:

Origin: https://evil.example

nesmí být akceptováno.

Backend bearer-token API není cookie-authenticated a jeho CSRF model je odlišný; zdokumentuj ho.

---

# 11. CORS

Backend není veřejné browser API.

Preferovaný production stav:

žádné CORS povolení pro arbitrary browser origins.

Pokud CORS middleware existuje:

- žádné `*` s credentials;
- explicitní allowlist;
- production default deny.

Test:

Origin: https://evil.example

nesmí dostat použitelný:

Access-Control-Allow-Origin

header.

---

# 12. TRUSTED HOSTS A PROXY HEADERS

Production backend/frontend musí chránit proti Host header abuse.

Explicitní production allowed hosts.

`Host: evil.example`

musí být odmítnut nebo nesmí ovlivnit security-sensitive URL generation.

Nedůvěřuj automaticky:

X-Forwarded-For
X-Forwarded-Host
X-Forwarded-Proto

od libovolného klienta.

Pokud proxy headers podporuješ:

- pouze explicitně trusted proxy;
- zdokumentuj trust boundary.

Rate limiting nesmí jít obejít spoofnutím `X-Forwarded-For`.

---

# 13. RATE LIMITING

Implementuj rate limiting odpovídající skutečnému production topology.

Nesmí být pouze dekorativní.

Pokud production deployment Phase 9 garantuje single process/single replica, může být limiter process-local pouze pokud:

- je tento deployment constraint explicitně vynucen/zdokumentován;
- není spuštěno více worker procesů;
- testy to dokazují.

Pokud topology umožňuje více workerů/replicas, použij shared state.

Nevkládej Redis pouze kvůli několika řádkům, pokud ho current architecture nepotřebuje.

Minimálně limituj:

- login;
- authentication failures;
- privileged mutations;
- RESUME;
- běžné API reads.

Doporučená baseline, pokud není lepší zdokumentovaný důvod:

login:
5 pokusů / 5 minut / identity + source

authenticated reads:
120 / minute / principal

state mutations:
20 / minute / principal

RESUME:
5 / 10 minutes / principal

HALT:
samostatný bucket, např. 10 / minute.

Přesné hodnoty mohou být upraveny, ale musí být:

- explicitní;
- config-driven;
- otestované;
- dokumentované.

Při limitu:

HTTP 429
Retry-After

Nezobrazuj token v rate-limit key/logu.

---

# 14. SECURITY HEADERS

Frontend production responses musí mít rozumnou security header policy.

Minimálně:

Content-Security-Policy
X-Content-Type-Options: nosniff
Referrer-Policy
Permissions-Policy

Clickjacking ochrana:

frame-ancestors 'none'

a/nebo kompatibilní X-Frame-Options.

HSTS pouze v HTTPS production deploymentu:

Strict-Transport-Security

Nevynucuj HSTS v local HTTP development.

Disable:

X-Powered-By

pokud framework umožňuje.

## CSP

Preferuj restrictive CSP.

Minimálně uvažuj:

default-src 'self'
object-src 'none'
base-uri 'self'
frame-ancestors 'none'
form-action 'self'
connect-src 'self'

Pokud Next.js potřebuje inline script/style výjimku:

- nejprve zvaž nonce-based CSP;
- nepřidávej `unsafe-eval` do production bez velmi vážného důvodu;
- jakákoli `unsafe-inline` výjimka musí být dokumentovaná a minimální.

CSP nesmí rozbít production build/dashboard.

---

# 15. CACHE SECURITY

Authentication a operator responses nesmí být veřejně cacheovatelné.

Použij podle vrstvy:

Cache-Control: no-store

pro:

- auth;
- user/session state;
- operator evidence;
- privileged mutation responses.

Zachovej existující `no-store` Phase 8 semantics.

---

# 16. ERROR HANDLING

Production API nesmí leakovat:

- Python stack traces;
- SQL text s credentials;
- DB connection URL;
- environment secrets;
- bearer token;
- session cookie;
- filesystem paths, pokud nejsou nezbytné.

Neočekávané chyby:

generic 500 external response

ale interně auditovatelný correlation ID.

Validation errors mohou zůstat typované, ale nesmí obsahovat secrets.

Authentication failure response musí být generická.

---

# 17. LOG REDACTION

Audituj logging.

Nikdy neloguj:

Authorization
Cookie
Set-Cookie
password
session secret
database password
API bearer token
full DATABASE_URL s heslem.

Implementuj central redaction tam, kde je to praktické.

Přidej regression proof:

known test secret
→ proveď auth request
→ zachyť logy
→ secret string se v logu nesmí objevit.

Correlation ID může být logován.

---

# 18. SECRET MANAGEMENT

Aktualizuj:

.env.example

a relevantní config.

Požadavky:

- žádný skutečný secret v repo;
- žádné production default passwords;
- žádné tokeny v `NEXT_PUBLIC_*`;
- žádný DATABASE_URL v browser code;
- žádné secrets v Dockerfile ARG/ENV vrstvě;
- production startup musí failnout při missing critical secret.

Preferuj možnost:

SECRET
nebo
SECRET_FILE

pro production secret injection, pokud implementace zůstane jednoduchá.

Například Docker secret/file mount.

Nesmí být možné nastavit současně konfliktní:

SECRET
a
SECRET_FILE

bez jasné precedence/fail-closed chování.

Vytvoř bezpečný dev helper, například:

make generate-dev-secrets

který vytvoří lokální gitignored secrets.

Nikdy je necommituj.

Soubor má mít restriktivní permissions, pokud OS umožňuje.

---

# 19. PRODUCTION CONFIG VALIDATION

Zaveď explicitní production environment mode.

Například:

APP_ENV=production

Production startup musí odmítnout nebezpečný config.

Minimálně:

- missing auth;
- weak session secret;
- placeholder token;
- DEBUG=true;
- wildcard trusted hosts;
- wildcard credentialed CORS;
- SQLite production database;
- insecure auth bypass;
- disabled RBAC;
- public docs, pokud policy říká disable;
- insecure cookie configuration;
- nevalidní PUBLIC_BASE_URL;
- HTTP public base URL tam, kde production očekává HTTPS.

NEVYTVÁŘEJ:

AUTH_DISABLED=true

production escape hatch.

Ideálně nevytvářej auth bypass vůbec.

Testy mají používat reálnou test authentication configuration.

---

# 20. OPENAPI A DOCS

Development může mít:

/docs
/openapi.json

Production:

- buď disabled;
- nebo protected ADMIN authentication.

Nesmí být public production API explorer bez vědomého rozhodnutí.

Health endpointy zůstávají public a minimal.

---

# 21. DEPENDENCY VULNERABILITY TRIAGE — POVINNÝ BLOCKER

Phase 8 CI reportovalo:

4 npm vulnerabilities:
- 3 HIGH
- 1 CRITICAL

Toto je POVINNÝ Phase 9 scope.

Nezačínej slepým:

npm audit fix --force

To je zakázáno.

Nejdříve spusť:

cd frontend
npm audit --json

a vytvoř tabulku:

advisory / CVE
severity
package
direct/transitive
dependency path
runtime/dev
vulnerable range
fixed version
applicability
proposed remediation

Rozliš:

production/runtime dependency

vs.

development/toolchain dependency.

## Remediation policy

Phase 9 nesmí skončit COMPLETE s:

- applicable CRITICAL runtime vulnerability;
- applicable HIGH runtime vulnerability.

Cíl:

0 CRITICAL vulnerabilities v celém dependency tree.

0 HIGH vulnerabilities v production dependency tree.

Pokud zůstane HIGH pouze v dev/toolchain dependency a nelze ho bezpečně odstranit:

- zdokumentuj konkrétní advisory;
- proč není runtime reachable;
- mitigation;
- expiry/review date;
- proč upgrade momentálně není bezpečný.

Žádný blanket ignore.

Každá výjimka musí být konkrétní.

Pokud existuje kompatibilní bezpečný upgrade:

proveď jej.

Po dependency změně:

frontend/package-lock.json musí být generován reálným npm.

Nikdy ručně.

Pokud Codex environment nemá registry:

NEVYTVÁŘEJ fake lockfile.

Použij existující:

Repair frontend lockfile

workflow přes feature branch.

Parent PR nesmí být mergnut, dokud není lockfile opraven a CI green.

---

# 22. ESLINT DEPRECATION

Phase 8 CI hlásila deprecated ESLint 9.39.2.

Zjisti:

- zda jde jen o support/deprecation warning;
- zda existuje bezpečná verze kompatibilní s aktuálním `eslint-config-next`;
- zda upgrade vyžaduje Next.js upgrade.

Pokud lze bezpečně, kompatibilně a s plnými testy upgradovat:

udělej to.

Pokud by upgrade vyžadoval velký unrelated framework migration:

nedělej ho naslepo.

Zdokumentuj jej jako non-security maintenance item.

Deprecated ≠ automaticky vulnerable.

---

# 23. NPM LIFECYCLE / SUPPLY-CHAIN HARDENING

Aktuální npm CI reportovalo install scripts minimálně u:

- esbuild;
- sharp;
- unrs-resolver.

Audituj, zda jsou lifecycle scripts skutečně potřeba.

Preferuj:

npm ci --ignore-scripts

pokud celý lint/test/build pipeline bez scripts funguje.

Pokud scripts jsou nezbytné:

použij explicitní npm allowlist mechanismus podporovaný připnutým npm 11.17.0, pokud je dostupný.

Allowlist musí být:

- explicitní;
- minimální;
- auditovatelný.

Nesmí vzniknout:

allow all scripts.

Zdokumentuj proč je každý povolený lifecycle script potřeba.

Proveď full build po změně.

---

# 24. FRONTEND LOCKFILE SUPPLY-CHAIN GUARD

Rozšiř existující lockfile guard pouze pokud je to vhodné tak, aby také odmítl nečekané dependency sources.

Prověř:

- git dependencies;
- file dependencies;
- arbitrary HTTP dependency URLs;
- nečekané registry.

Standardní npm dependencies mají být reproducible z důvěryhodného HTTPS registry source.

Nedělej guard tak rigidní, že odmítne legitimní npm metadata.

Regresní testy jsou povinné.

---

# 25. PYTHON DEPENDENCY SECURITY

Audituj backend dependency tree.

Použij vhodný, připnutý vulnerability scanner.

Například:

pip-audit
OSV

nebo jiný stabilní scanner.

Scanner tool musí být:

- pinned;
- reproducible;
- v CI.

Nemusí být runtime dependency aplikace.

Nedovol:

- unpinned git dependencies;
- arbitrary local/file dependencies;
- neauditované URL dependencies.

Pokud Python scanner nalezne vulnerability:

stejný princip:

- konkrétní advisory;
- applicability;
- bezpečný upgrade;
- žádný blanket ignore.

---

# 26. STATIC SECURITY ANALYSIS

Přidej backend SAST/security lint.

Pokud současný Ruff podporuje relevantní Bandit-compatible `S` rules:

preferuj existující Ruff před přidáním dalšího frameworku.

Minimálně audituj produkční src pro:

- subprocess shell injection;
- unsafe temp files;
- hardcoded secrets;
- insecure hashes pro security purpose;
- eval/exec;
- unsafe pickle;
- unsafe YAML;
- weak random for security;
- SQL string interpolation.

Test code může mít explicitní, minimální test-only exceptions.

Neignoruj celý security ruleset.

Frontend proveď security review na:

- dangerouslySetInnerHTML;
- eval;
- Function constructor;
- arbitrary redirects;
- arbitrary server fetch URLs;
- `NEXT_PUBLIC` secrets;
- path injection;
- untrusted HTML.

---

# 27. SECRET SCANNING

Existující GitGuardian check zachovej.

Navíc preferuj repository-controlled secret scanning gate, například Gitleaks nebo ekvivalent.

Security scanner:

- musí běžet read-only;
- ideálně nad celým relevantním git history;
- musí failnout při skutečném secret finding;
- fixture false positives mohou mít konkrétní allowlist.

Žádný globální ignore.

Scanner/action musí být version pinned.

---

# 28. GITHUB ACTIONS SUPPLY-CHAIN HARDENING

Audituj všechny:

.github/workflows/*.yml

Požadavky:

## Pin actions

Nepoužívej mutable third-party action reference jako jediný trust anchor:

@main
@master
@v4
@v5
@v6

Pro production/security workflow preferuj full immutable commit SHA:

uses: owner/action@<40-char-sha>

A vedle komentář:

# vX.Y.Z

SHA ověř skutečně proti oficiálnímu repository/release.

Nevymýšlej SHA.

Pokud současný official action major používá deprecated Node runtime a existuje podporovaný successor:

bezpečně upgraduj.

## Permissions

Workflow-level default:

contents: read

Write permissions pouze tam, kde jsou skutečně nutné.

Repair frontend lockfile workflow:

zachovej jeho dvoufázovou security boundary.

Žádný jiný CI job nesmí dostat write permission bez důvodu.

## Zakázáno

Nepoužívej:

pull_request_target

ke spuštění untrusted repository code.

Žádný force push.

Žádný secret dostupný untrusted PR jobu.

Žádný `${{ github.event... }}` string přímo interpolovaný do nequoted shell commandu.

---

# 29. RE-AUDIT LOCKFILE REPAIR WORKFLOW

Znovu ověř:

verify-repair:
contents: read

publish-repair:
contents: write
pull-requests: write

Verify job:

- source SHA pinned;
- persist-credentials false;
- npm/testing pouze read-only token.

Publish job:

- žádný npm;
- žádný repo-controlled executable script;
- checksum;
- TOCTOU;
- only package-lock diff;
- no direct main push;
- no direct source_ref push.

Phase 9 nesmí tuto bezpečnostní opravu z Phase 8 regresovat.

---

# 30. PRODUCTION CONTAINER IMAGES

Vytvoř production-grade container image pro backend a frontend, pokud ještě neexistují.

Preferuj:

backend/Dockerfile
frontend/Dockerfile

a odpovídající `.dockerignore`.

Požadavky:

## Backend image

- Python 3.12;
- exact locked dependencies;
- žádný dev server;
- žádný reload;
- non-root user;
- žádné secrets v image;
- minimální runtime;
- healthcheck;
- production command;
- read-only filesystem compatibility, pokud praktické.

## Frontend image

- Node 24;
- npm 11.17.0 při build;
- npm ci;
- production build;
- production runtime;
- non-root user;
- žádné dev secrets;
- server-only backend config;
- žádný backend token v static bundle.

Použij multi-stage build.

Nevkládej:

.env
.env.local
credentials
node_modules z hosta
Git history

do image.

Preferuj přesně versionované base images.

Pokud můžeš bezpečně ověřit digest, pinni digest.

Nikdy nevymýšlej image digest.

---

# 31. CONTAINER RUNTIME HARDENING

Production containers:

- non-root;
- no privileged mode;
- no host network;
- no Docker socket;
- no unnecessary capabilities.

Preferuj:

cap_drop:
  - ALL

security_opt:
  - no-new-privileges:true

pokud aplikace funguje.

Použij:

read_only: true

tam, kde je to praktické.

Pro zapisovatelná temp data použij explicitní tmpfs.

Žádné source-code bind mounts v production.

---

# 32. PRODUCTION COMPOSE / DEPLOYMENT PROFILE

Vytvoř oddělený production-like PAPER deployment profil, například:

docker-compose.production.yml

NEPŘEPISUJ development Compose tak, aby ztratil developer UX.

Production topology minimálně:

frontend
backend
postgres

Redis nepřidávej bez skutečné potřeby.

## Network

PostgreSQL:

NESMÍ mít host-published port.

Backend:

preferovaně NESMÍ mít public host port.

Frontend:

jediný host-accessible application entrypoint.

Pro bezpečný single-host default může frontend bindovat pouze:

127.0.0.1:<port>

a skutečný internet ingress/TLS je explicitní reverse-proxy/platform responsibility.

Nedělej fake TLS s náhodným certifikátem.

Zdokumentuj:

production internet exposure vyžaduje HTTPS reverse proxy / trusted ingress.

---

# 33. POSTGRESQL PRODUCTION AUTH

Production nesmí používat:

POSTGRES_HOST_AUTH_METHOD=trust

CI production-like PostgreSQL proof také převeď na password authentication, pokud to lze bez zhoršení test isolation.

Test-only password může být syntetický CI credential.

Production secret musí být injectovaný.

Dev-only loopback trust může zůstat pouze pokud:

- je jasně označen jako development only;
- nikdy se nepoužívá v production profile;
- není bindován veřejně.

---

# 34. DATABASE LEAST PRIVILEGE

Production app nemá používat PostgreSQL superuser/owner credential.

Implementuj nebo připrav skutečně použitelný model minimálně:

MIGRATION ROLE
APPLICATION RUNTIME ROLE

Migration role:

- Alembic DDL.

Application role:

- pouze runtime DML/SELECT práva potřebná aplikací.

Application role nesmí:

CREATE DATABASE
DROP DATABASE
CREATE ROLE
ALTER ROLE
CREATE arbitrary schema
DROP schema
provádět migrations

pokud to není nezbytné.

Preferuj, aby audit evidence nemohla být běžnou runtime cestou UPDATE/DELETE, pokud current architecture umožňuje insert-only audit log.

Ověř PostgreSQL testem:

runtime role:
→ běžná aplikace funguje

runtime role:
→ CREATE TABLE / schema DDL selže.

Migration role:
→ alembic upgrade head funguje.

---

# 35. MIGRATION DEPLOYMENT BOUNDARY

API process nesmí při production startupu implicitně provádět schema creation/migration.

Migrations jsou explicitní deployment step.

Production topology:

migration job
→ success
→ backend start/readiness

Ready endpoint může failnout, pokud DB schema není očekávané Alembic head.

Žádné implicitní:

Base.metadata.create_all()

v production path.

---

# 36. HEALTH / READINESS

Implementuj jasné:

GET /healthz

Liveness:

- proces běží;
- žádné citlivé details.

GET /readyz

Readiness:

- DB dostupná;
- očekávané schema/migration;
- potřebná runtime config validní.

Při ne-ready:

503

Nezobrazuj DB password/URL.

Health endpoint nesmí vyžadovat auth, pokud ho potřebuje container orchestrator.

---

# 37. BACKUP / RESTORE FOUNDATION

Production hardening není kompletní bez recovery story.

Implementuj bezpečný PostgreSQL backup/restore postup.

Preferuj skripty nebo Makefile targety:

make db-backup
make db-restore

Backup:

- používá PostgreSQL 17-compatible pg_dump;
- credentials nejsou vypsány;
- file permissions jsou restriktivní;
- vytvoří checksum;
- necommitne backup do Git;
- backup path je gitignored.

Restore:

- defaultně nesmí destruktivně přepsat production DB;
- vyžaduje explicitní target;
- destruktivní restore vyžaduje jasnou confirmation;
- používá kompatibilní pg_restore.

## CI recovery proof

Na ephemeral PostgreSQL:

1. alembic upgrade head;
2. vytvoř reprezentativní persisted data;
3. pg_dump;
4. vytvoř nový empty DB;
5. restore;
6. ověř schema;
7. ověř reprezentativní Phase 4–8 data/invariants.

Nemusíš dumpovat gigabyty fixtures.

Důkaz má ověřit skutečný mechanismus.

Nevydávej backup za automaticky off-site, pokud off-site upload není implementován.

Dokumentuj doporučenou production backup policy bez falešného tvrzení, že ji systém již automaticky vykonává.

---

# 38. SECURITY / DEPENDENCY IMAGE SCANNING

Přidej container/filesystem vulnerability scan.

Preferuj stabilní nástroj například:

Trivy

nebo ekvivalent.

Scanner verzi/action pinni.

Scan:

- backend image;
- frontend image;
- případně filesystem/config.

Production gate:

- applicable/fixable CRITICAL → FAIL;
- applicable/fixable HIGH → FAIL.

Pokud scanner reportuje unfixed OS advisory:

- zobraz/reportuj;
- neignoruj potichu;
- explicitně zdokumentuj risk.

Žádný blanket:

--ignore-all

---

# 39. SBOM

Pokud použitý security scanner umí bezpečně generovat SBOM bez významné nové komplexity, generuj pro CI artifacts:

CycloneDX

nebo SPDX.

Minimálně:

backend image SBOM
frontend image SBOM

SBOM není nutné commitovat do repository.

Použij krátkou retention dobu.

SBOM nesmí obsahovat secrets.

---

# 40. SECURITY EXCEPTIONS POLICY

Pokud skutečně musí zůstat known vulnerability exception, vytvoř explicitní evidenci například:

docs/security-exceptions.md

Každá exception:

- advisory/CVE;
- package;
- severity;
- dependency path;
- runtime/dev;
- applicability analysis;
- mitigation;
- owner;
- review/expiry date.

Žádná:

"ignore all high"

výjimka.

Critical runtime exception:

není povolena pro Phase 9 completion.

---

# 41. API INPUT HARDENING

Audituj Pydantic request models.

Minimálně:

- reason lengths;
- identity/path params;
- limit/offset;
- strings, které jdou do logu;
- confirmation fields;
- datetime;
- enum values.

Odmítej:

- extrémně velké payloads, pokud endpoint nemá důvod;
- absurdně dlouhé Authorization headers;
- invalid content types, kde relevantní.

Nevytvářej arbitrary upload endpoint.

Nesnaž se implementovat full WAF.

---

# 42. SQL INJECTION REVIEW

Proveď code search na:

text(...)
execute(...)
raw SQL
f-string SQL
string concatenation SQL

Všechny dynamické hodnoty musí být parametrizované.

Alembic static SQL může být výjimka, pokud není user-controlled.

Přidej regression test pouze pokud nalezneš konkrétní gap.

---

# 43. SSRF REVIEW

Phase 8 má server-side API allowlist.

Zachovej jej.

User-controlled:

URL
hostname
protocol
absolute path

nesmí být možné použít pro arbitrary server-side fetch.

Test:

https://evil.example

nebo:

http://169.254.169.254

nesmí být možné vnutit server-side operator klientovi.

Backend URL je server-controlled config.

---

# 44. XSS REVIEW

Projdi frontend.

Hledej:

dangerouslySetInnerHTML
innerHTML
eval
new Function
unescaped user/server strings v HTML contextu

Reason/audit values se mohou zobrazovat jako text, nikdy jako HTML.

Přidej regression test se stringem například:

<script>alert(1)</script>

UI jej musí zobrazit jako text, ne vykonat.

---

# 45. SECURITY AUDIT OF OPERATOR UI

Dashboard musí respektovat role.

VIEWER:

- nevidí nebo má disabled mutation actions;
- ale backend 403 je autorita.

OPERATOR:

- může HALT;
- RESUME nesmí být aktivní.

ADMIN:

- může existující privileged actions.

Nikdy:

frontend role guard jako jediná ochrana.

Po 401:

redirect/login nebo jasná auth state.

Po 403:

jasně ukaž insufficient permission.

Po failed RESUME:

stav zůstává podle backend evidence.

---

# 46. AUTH / SESSION HTTP ACCEPTANCE

Přidej HTTP-level acceptance proof.

Nemusíš instalovat těžký browser E2E framework, pokud lze přes produkční Next server + HTTP client prokázat boundary.

Minimální scénáře:

unauthenticated dashboard request
→ redirect/login

invalid login
→ generic failure

valid login
→ secure session

protected page
→ success

VIEWER mutation
→ forbidden

OPERATOR HALT
→ allowed

OPERATOR RESUME
→ forbidden

ADMIN RESUME
→ pouze pokud safety state umožňuje

cross-origin mutation
→ rejected

logout
→ session invalidated/removed

expired session
→ rejected

tampered session
→ rejected

---

# 47. SECRET LEAK BUNDLE TEST

Po production frontend build:

prohledej:

.next/static
public build artifacts

na known synthetic test secrets:

- API bearer token;
- session secret;
- DATABASE_URL;
- DB password.

Výsledek musí být:

0 occurrences.

`NEXT_PUBLIC_*` nesmí obsahovat secret.

---

# 48. CONTAINER SECURITY ACCEPTANCE

CI musí ověřit minimálně:

backend container:
id -u != 0

frontend container:
id -u != 0

Production compose config:

- postgres není host-published;
- backend není public, pokud to architecture nevyžaduje;
- frontend je jediný entrypoint;
- žádný privileged;
- žádný Docker socket;
- žádný production trust DB auth.

Production image:

- neobsahuje `.env`;
- neobsahuje git metadata;
- neobsahuje test credentials.

---

# 49. POSTGRESQL SECURITY ACCEPTANCE

Přidej Phase 9 PostgreSQL integration testy.

Minimálně:

- authenticated operator HALT/RESUME s actor evidence;
- runtime DB role funguje;
- runtime DB role nemůže DDL;
- migration role může provést Alembic;
- production-like password auth;
- backup/restore proof;
- security changes přežijí novou DB session.

Nevystač si se SQLite.

---

# 50. CI ARCHITEKTURA

Zachovej stávající merge gates:

quality
api
unit-research
integration-postgres
frontend

A přidej rozumně oddělené Phase 9 gates.

Preferovaný model:

security
container-build
production-smoke

nebo podobně.

## quality

Zůstává:

uv 0.12.3
uv lock --check
uv sync --locked --all-groups
ruff format --check
ruff check
mypy

Security static rules mohou být v quality nebo security jobu.

## api

Přidej Phase 9 auth/RBAC/API tests.

## integration-postgres

Musí zahrnout:

Phase 8 proof
+
Phase 9 PostgreSQL security proof.

## frontend

Node 24
npm 11.17.0
lockfile guard
npm ci
lint
typecheck
tests
build

Přidej Phase 9 auth/security tests.

## security

Minimálně:

- Python vulnerability scan;
- npm vulnerability scan;
- SAST/security lint;
- secret scan.

## containers

- build backend;
- build frontend;
- vulnerability scan;
- non-root check.

## production smoke

- production-like service topology;
- health/readiness;
- authentication boundary;
- network exposure assertions.

Žádný job nesmí být allowed-to-fail jen proto, že jde o security.

---

# 51. NPM AUDIT CI POLICY

Po remediation nastav pravidelný CI gate.

Minimálně:

npm audit --omit=dev --audit-level=high

musí být green.

Také kontroluj celý dependency tree minimálně na CRITICAL.

Pokud přesná npm syntax/API pro pinned verzi umožňuje lepší policy, použij ji.

Nedovol runtime high/critical.

Výsledek audit reportu může být uložen jako krátkodobý CI artifact.

---

# 52. PYTHON VULNERABILITY CI POLICY

Připnutý Python dependency scanner musí běžet při PR.

Pokud scanner používá external advisory DB a ta je dočasně nedostupná:

job nesmí falešně PASS.

Může mít bounded retry.

Pokud nelze ověřit:

FAIL / BLOCKED

nikoli zelené přeskočení.

---

# 53. GITHUB SECURITY WORKFLOW PERMISSIONS

Security scanning job:

contents: read

Container build scan:

contents: read

No write.

Žádné secrets potřebné pro vulnerability scans pokud to není absolutně nutné.

Nepřidávej production deployment secrets do PR CI.

---

# 54. PRODUCTION MAKEFILE UX

Doplň konzistentní targety podle aktuálního Makefile.

Například:

make security-check
make frontend-security
make production-build
make production-up
make production-down
make production-smoke
make db-backup
make db-restore

Nevytvářej duplicitu, pokud repo už má vhodné targety.

`production-up` nesmí automaticky spustit live trading.

PAPER only.

---

# 55. DOCUMENTATION

Aktualizuj minimálně:

README.md
AGENTS.md
docs/architecture.md
docs/operations.md
docs/implementation-plan.md
docs/security.md

Podle potřeby vytvoř:

docs/production-deployment.md
docs/security-exceptions.md
docs/codex/phase9-complete.md

## README

Musí vysvětlit:

- development;
- authentication;
- production-like PAPER start;
- jak vytvořit dev credentials;
- health;
- security checks;
- backup;
- explicitně no-live.

## AGENTS.md

Přidej trvalé security rules:

- never bypass auth for tests;
- never disable RBAC to make tests pass;
- never commit secrets;
- never log credentials;
- never use npm audit fix --force;
- never create manual lockfile;
- security exception must be explicit;
- production routes fail closed;
- GitHub Actions write permissions minimal;
- no live path.

---

# 56. UPDATE IMPLEMENTATION PLAN

`docs/implementation-plan.md` je živý dokument.

Aktualizuj zastaralý Phase 8 status.

Phase 8:

COMPLETE
AUDIT PASSED WITH FIXES

Security oblast po Phase 9 musí odrážet skutečný stav.

Neoznačuj security COMPLETE, pokud zůstává:

- unauthenticated control plane;
- unresolved applicable critical/high runtime CVE;
- missing secret management;
- missing security scan;
- fake rate limiter;
- production DB superuser;
- public DB exposure.

CI/CD/infrastructure status aktualizuj pouze podle skutečně implementovaného scope.

Nevydávej cloud deployment za hotový, pokud jsme žádný cloud deployment nevytvořili.

---

# 57. PHASE 9 COMPLETION DOCUMENT

Vytvoř:

docs/codex/phase9-complete.md

Musí obsahovat:

## Status

Jednu z variant:

PHASE 9 IMPLEMENTATION COMPLETE
READY FOR INDEPENDENT PHASE 9 AUDIT

nebo:

PHASE 9 IMPLEMENTATION INCOMPLETE
BLOCKED

## Security matrix

Area | Result | Evidence

Minimálně:

- threat model;
- backend auth;
- dashboard auth;
- RBAC;
- HALT authorization;
- RESUME authorization;
- CSRF;
- CORS;
- trusted hosts;
- rate limiting;
- session security;
- secret management;
- log redaction;
- dependency vulnerabilities;
- npm lifecycle scripts;
- Python vulnerability scan;
- SAST;
- secret scan;
- GitHub Actions hardening;
- container hardening;
- DB least privilege;
- health/readiness;
- backup/restore;
- production smoke;
- paper-only boundary.

## Dependency vulnerability table

Před Phase 9:

3 HIGH
1 CRITICAL

Po Phase 9:

uveď skutečné výsledky.

Nevymýšlej nuly.

## Known limitations

Pouze skutečné non-blocking limitations.

Phase 10 není implementována.

Live trading není implementován.

---

# 58. DEPENDENCY CHANGE DISCIPLINE

Backend:

uv.lock je autoritativní.

Jakákoli backend dependency změna:

uv 0.12.3
uv lock
uv lock --check
uv sync --locked --all-groups
full gates

Nikdy ručně neupravuj uv.lock.

Frontend:

package-lock.json je autoritativní.

package.json dependency graph unchanged
→ package-lock unchanged

package.json dependency graph changed
→ real npm 11.17.0 regenerates lock
→ lockfile guard
→ npm ci
→ lint
→ typecheck
→ tests
→ build

Registry unavailable
→ do not fabricate lock
→ use repair workflow / report blocked.

---

# 59. TESTING — BACKEND

Po implementaci spusť:

cd backend

uv --version
uv lock --check
uv sync --locked --all-groups

uv run ruff format --check .
uv run ruff check .
uv run mypy src/quantlab

Spusť minimálně:

uv run pytest -q \
  tests/test_vertical_slice.py \
  tests/test_phase6.py \
  tests/test_phase6_runtime.py \
  tests/test_phase6_audit_fixes.py \
  tests/test_phase6_experiment_audit.py \
  tests/test_phase7.py \
  tests/test_phase7_api.py \
  tests/test_phase8_api.py

plus všechny nové Phase 9 testy.

Nevynechávej relevantní existující suite jen kvůli runtime.

---

# 60. TESTING — POSTGRESQL

Použij PostgreSQL 17.

Proveď:

alembic upgrade head

Spusť autoritativní Phase 3–9 PostgreSQL suite.

Musí obsahovat existující Phase 8 proof:

tests/test_phase8_postgres.py

a nové Phase 9 PostgreSQL testy.

Explicitně reportuj:

passed
failed
skipped

Relevantní Phase 9 security/PostgreSQL proof nesmí být skipnut.

---

# 61. TESTING — FRONTEND

Použij:

Node 24
npm 11.17.0

Z čistého stavu:

cd frontend

node --version
npm --version

npm run lockfile:check
npm ci
npm run lint
npm run typecheck
npm test
npm run build

Pak security acceptance / HTTP smoke.

Pokud dependency remediation změnila lock:

ověř clean npm ci.

---

# 62. TESTING — VULNERABILITY SCANS

Povinně spusť:

- npm production dependency audit;
- npm full critical audit;
- Python dependency audit;
- static security scan;
- secret scan;
- container scan.

Na konci uveď přesná čísla.

Například:

npm runtime:
critical 0
high 0

npm full:
critical 0
high N

Python:
vulnerabilities N

Containers:
critical N
high N

Nesmíš reportovat pouze:

"scan passed"

bez výsledku.

---

# 63. TESTING — AUTHORIZATION MATRIX

Automaticky ověř:

NO AUTH:
GET protected → 401

VIEWER:
GET → 200
HALT → 403
RESUME → 403

OPERATOR:
GET → 200
HALT → success
RESUME → 403

ADMIN:
GET → 200
HALT → success
RESUME → success pouze při splněné safety validation.

Unknown role:

DENY.

Invalid token:

401.

Expired/invalid session:

deny.

---

# 64. TESTING — ADVERSARIAL

Proveď adversarial second pass.

Minimálně:

## Authentication

- no token;
- invalid token;
- token prefix only;
- extremely long token;
- whitespace;
- duplicate auth headers pokud framework dovoluje;
- session tamper;
- expired session;
- wrong role.

## Login

- wrong password;
- repeated failures;
- very long password;
- malicious username;
- CSRF login;
- generic error.

## Headers

- evil Host;
- spoofed X-Forwarded-For;
- malicious Origin;
- CORS preflight.

## XSS

reason:
<script>alert(1)</script>

## SSRF

attempt backend path/url:
http://169.254.169.254
https://evil.example

## Rate limit

- exact boundary;
- first request;
- last allowed;
- first rejected;
- Retry-After;
- independent principal;
- HALT separate safety bucket.

## Security config

Production start with:

missing token
weak token
weak session secret
wildcard host
insecure cookie
SQLite DB

→ FAIL CLOSED.

---

# 65. FALSE-GREEN REVIEW

U každého security testu se ptej:

Prokazuje test production path?

Nespoléhej na test, který:

- bypassne auth dependency;
- monkeypatchne permission vždy na true;
- používá jinou middleware cestu než app;
- testuje helper bez HTTP endpointu;
- netestuje production config;
- používá SQLite pro DB privilege claim;
- pouze kontroluje, že header string existuje v config souboru;
- pouze kontroluje status 200.

Kritické security claims musí mít end-to-end nebo integration evidence.

---

# 66. PRODUCTION SMOKE

Po container build spusť production-like PAPER topology.

Minimálně ověř:

- migration job success;
- backend ready;
- frontend ready;
- DB není public;
- backend není public;
- frontend reachable;
- unauth protected route denied;
- valid auth works;
- RBAC works;
- paper state readable;
- HALT works;
- audit actor recorded;
- containers non-root.

Po testu:

clean shutdown.

Žádné broker/network live traffic.

---

# 67. BACKUP/RESTORE ADVERSARIAL TEST

Ověř:

- corrupt checksum → restore odmítnut;
- missing backup → error;
- wrong target → safe failure;
- destructive restore bez confirmation → refused.

Backup utility nesmí omylem umožnit:

make db-restore

bez explicitního target/confirmation a přepsat hlavní databázi.

---

# 68. SECURITY HEADER HTTP TEST

Nekontroluj pouze config file.

Spusť skutečný production Next server nebo production container a HTTP request.

Ověř skutečné response headers.

Minimálně:

nosniff
CSP
Referrer-Policy
Permissions-Policy
frame protection

V production HTTPS policy také HSTS config.

---

# 69. GIT DIFF SECURITY REVIEW

Před dokončením:

git diff --check

Proveď:

git grep -n -i \
  -e 'password=' \
  -e 'api_key=' \
  -e 'secret=' \
  -e 'bearer ' \
  -- .

Vyhodnoť false positives ručně.

Proveď také search na:

NEXT_PUBLIC_
LIVE
broker
eval(
dangerouslySetInnerHTML
shell=True
subprocess
text(
execute(

Nezakazuj legitimní výskyty automaticky; audituj je.

---

# 70. SECOND-PASS SECURITY REVIEW

Po dokončení implementace proveď nový průchod jako útočník.

Explicitně zkontroluj:

- lze obejít backend auth?
- lze obejít RBAC přímým API requestem?
- používá frontend admin token pro viewer?
- může browser přečíst token?
- může CSRF aktivovat RESUME?
- může malicious Host ovlivnit redirect?
- lze spoofnout rate limiter přes X-Forwarded-For?
- loguje se token?
- může production start s weak secret?
- je public OpenAPI?
- je public PostgreSQL?
- je backend public?
- běží container jako root?
- může app DB credential provádět DDL?
- může GitHub PR code dostat write token?
- je některý action mutable tag?
- může lock repair regression znovu spustit untrusted code s write tokenem?
- zůstává critical/high runtime dependency vulnerability?
- lze do browser bundle dostat server secret?
- existuje live trading cesta?
- prošel backup/restore skutečně?

Každý nalezený problém oprav a zopakuj relevantní test.

---

# 71. CHANGE DISCIPLINE

Phase 9 je velká fáze, ale nedělej unrelated refactor.

Nedělej:

- nové strategie;
- změnu performance math;
- nový market provider;
- redesign dashboardu;
- Phase 10 long-run monitoring;
- cloud-specific deploy, pokud není potřebný;
- live broker;
- live order path.

Každá změna musí souviset s:

security
production hardening
dependency safety
deployment safety
recovery
authentication
authorization.

---

# 72. CI NEMĚKČIT

Pokud nový security gate selže:

oprav problém.

Nedělej:

continue-on-error: true

u kritického security gate.

Nesnižuj severity threshold jen proto, aby CI bylo green.

Nevypínej test.

Nevytvářej ignore bez konkrétní evidence.

---

# 73. ENVIRONMENTAL BLOCKERS

Pokud Codex sandbox nemá:

- registry;
- Docker;
- správné uv;
- GitHub authentication;

nefalšuj PASS.

Implementuj, co lze.

Neprováděj manual lockfile hack.

Neoznač test za prošlý, když nebyl spuštěn.

GitHub CI je autoritativní environmentální důkaz.

Pokud security dependency remediation vyžaduje registry a nelze ji bezpečně dokončit:

status:

BLOCKED BY ENVIRONMENT

dokud není lockfile / scan reálně proveden.

---

# 74. DEFINITION OF DONE

Phase 9 implementation je COMPLETE pouze pokud:

## Architecture

- PAPER-only boundary zachována;
- žádný live path.

## Authentication

- dashboard protected;
- API protected;
- no auth bypass.

## Authorization

- RBAC backend-authoritative;
- VIEWER/OPERATOR/ADMIN matrix;
- RESUME admin-only;
- HALT operator/admin.

## Sessions

- secure cookie;
- expiry;
- tamper protection;
- logout.

## Browser security

- CSRF protection;
- CORS deny;
- trusted host;
- production security headers;
- no secret bundle leakage.

## Abuse controls

- rate limits fungují;
- 429/Retry-After;
- spoof resistance.

## Secrets

- production secrets fail closed;
- no repo secret;
- no logging secret.

## Dependencies

- current 1 critical npm vulnerability vyřešena nebo Phase 9 není COMPLETE;
- žádné applicable runtime high/critical;
- Python vulnerability scan;
- SAST;
- secret scan.

## Supply chain

- actions immutable pinned;
- lockfiles preserved;
- lifecycle scripts controlled;
- repair workflow remains secure.

## Containers

- backend production image;
- frontend production image;
- non-root;
- hardened;
- no embedded secrets.

## Database

- no production trust auth;
- app runtime least privilege;
- migration boundary;
- PostgreSQL proof.

## Recovery

- backup;
- restore;
- checksum;
- recovery test.

## CI

- quality green;
- api green;
- unit-research green;
- integration-postgres green;
- frontend green;
- security green;
- containers green;
- production smoke green.

## Documentation

- security.md;
- production deployment;
- implementation plan;
- phase9-complete.md.

Nezůstává žádný:

BLOCKER
nebo
unmitigated HIGH

Phase 9 security finding.

---

# 75. FINAL PHASE 9 STATUS

Pokud vše projde:

docs/codex/phase9-complete.md:

PHASE 9 IMPLEMENTATION COMPLETE
READY FOR INDEPENDENT PHASE 9 AUDIT

NEPIŠ ještě:

READY FOR PHASE 10

dokud neproběhne samostatný Phase 9 Audit Gate.

Pokud něco významného zůstane:

PHASE 9 IMPLEMENTATION INCOMPLETE
NOT READY FOR PHASE 9 AUDIT

Pokud pouze GitHub CI ještě nebylo možné spustit:

PHASE 9 IMPLEMENTATION COMPLETE
ENVIRONMENTAL VERIFICATION PENDING
NOT YET READY FOR INDEPENDENT AUDIT

Nepoužívej optimistický status.

---

# 76. PULL REQUEST

Po implementaci vytvoř jeden samostatný PR proti aktuálnímu main.

Preferovaný title:

Phase 9 — Security and production hardening

PR description musí obsahovat:

- threat model summary;
- auth/RBAC;
- dependency vulnerability remediation;
- security scans;
- production containers;
- DB hardening;
- backup/restore;
- CI gates;
- paper-only proof;
- known limitations.

Pokud je dependency lockfile repair potřebný, může před finalizací hlavního PR vzniknout malý repair PR proti Phase 9 branch podle existujícího workflow.

Hlavní Phase 9 PR nesmí být mergnut, dokud není vše green.

---

# 77. FINAL RESPONSE FORMAT

Na konci vrať přesně:

## 1. Starting state

- starting HEAD
- branch
- clean/dirty

## 2. Threat model

Stručný výsledek.

## 3. Authentication

Co je implementováno.

## 4. RBAC

Tabulka:

Role | Read | HALT | RESUME | Other privileged mutations

## 5. Dashboard/session security

- login
- cookies
- expiry
- CSRF
- logout

## 6. API security

- auth
- CORS
- trusted hosts
- errors
- rate limiting

## 7. Secret management

Co a jak.

## 8. Dependency vulnerability remediation

Tabulka:

Package | Advisory | Before | Runtime/dev | Fix | After

A souhrn:

npm critical:
npm high runtime:
npm high dev:
Python vulnerabilities:

## 9. Supply-chain hardening

- lockfiles
- lifecycle scripts
- Actions pinning
- secret scan
- SAST

## 10. Container hardening

- images
- non-root
- networking
- privileges

## 11. PostgreSQL hardening

- password auth
- runtime role
- migration role
- DDL denial proof

## 12. Backup/restore

- mechanism
- checksum
- restore proof

## 13. Backend verification

commands + results

## 14. PostgreSQL verification

passed / failed / skipped

## 15. Frontend verification

commands + results

## 16. Security scan results

Exact counts.

## 17. Production smoke

Exact result.

## 18. Paper-only verification

PASS / FAIL

## 19. False-green review

Result.

## 20. Changed files

Přesný seznam.

## 21. Git state

- commit
- branch
- working tree

## 22. Remaining limitations

Pouze skutečné non-blocking items.

## 23. Final verdict

Použij právě jednu:

PHASE 9 IMPLEMENTATION COMPLETE — READY FOR INDEPENDENT PHASE 9 AUDIT

nebo:

PHASE 9 IMPLEMENTATION INCOMPLETE — NOT READY FOR AUDIT

nebo:

PHASE 9 IMPLEMENTATION VERIFICATION PENDING — NOT READY FOR AUDIT

---

# POSLEDNÍ INSTRUKCE

Nevracej pouze plán.

Proveď implementaci.

Neoptimalizuj pro zelený status.

Optimalizuj pro:

- fail-closed security;
- reprodukovatelnost;
- auditovatelnost;
- least privilege;
- production-grade PAPER provoz;
- pravdivé testovací evidence.

Live trading musí po Phase 9 zůstat nemožný.
