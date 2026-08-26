# Audit závislostí

## Audit 2026-08-26

Audit vychází z autoritativních lockfilů na `main` a pokrývá přímé i
tranzitivní Python a npm balíčky, produkční image, CI a bezpečnostní kontroly.
Rozsah je pouze údržba závislostí; obchodní, API, databázová a bezpečnostní
architektura se nemění.

### Výsledek

- Backend lock je aktuální vzhledem k deklarovaným rozsahům. Obsahuje mimo jiné
  FastAPI 0.141.1, Starlette 1.6.0, Pydantic 2.13.4, SQLAlchemy 2.0.51,
  psycopg 3.3.4 a Uvicorn 0.52.1. Nebyl nalezen podklad pro vynucený major
  upgrade ani pro změnu API nebo DB schématu.
- Frontend má vzájemně sladěné Next.js a `eslint-config-next` 16.3.2,
  React a React DOM 19.2.4, TypeScript 5.9.3 a Vitest 4.1.11. Next.js
  deklaruje podporu instalovaného Reactu a Node 24; Vitest rovněž deklaruje
  podporu Node 24.
- Metadata uzamčeného ESLint 9.39.2 jej označují jako nepodporovaný.
  `eslint-config-next` 16.3.2 akceptuje ESLint `>=9.0.0`, takže následný
  podporovaný ESLint je kandidátem na samostatný minimální upgrade.
- Registry metadata a advisory endpointy nebyly z runneru dostupné: npm
  registry i audit endpoint vracely HTTP 403 a PyPI proxy odmítla spojení.
  Nelze proto auditovat proti aktuální databázi zranitelností ani bezpečně
  vygenerovat nové lockfily.

### Rozhodnutí o změnách

Dependency manifesty ani lockfily se v tomto auditu nemění. Zejména nebyl
proveden neověřitelný major upgrade ESLintu a nebyly ručně upraveny lockfily.
To je fail-closed postup vyžadovaný projektovou lockfile policy: změna je možná
až v prostředí, kde reálné npm 11.17.0 dokončí `npm ci` a kde `uv` 0.12.3
dokončí `uv lock --check` a `uv sync --locked --all-groups` nad dostupnými
registry metadata.

### Následné ověření

V runneru s přístupem k registrům je nutné v tomto pořadí:

1. spustit `npm outdated`, oba projektové `npm audit` příkazy a
   `pip-audit --strict`;
2. ověřit podporovanou verzi ESLintu proti peer dependencies aktuálního
   `eslint-config-next` a provést nejmenší podporovaný upgrade;
3. lockfily vygenerovat výhradně připnutými `uv` 0.12.3 a npm 11.17.0;
4. zopakovat kompletní backend, frontend, security, image a production smoke
   sadu z CI.

PAPER-only tok, point-in-time pravidla, CSP, trusted origins, TLS validace,
autentizace a RBAC nebyly v rámci auditu měněny ani obcházeny.
