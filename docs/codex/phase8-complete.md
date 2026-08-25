# Phase 8 — Operator Control Plane, Stable Read API & Web Dashboard

**PHASE 8: COMPLETE WITH ENVIRONMENTAL VERIFICATION PENDING**

Implementace obsahuje explicitní `OperatorReadModel`, stabilní `/operator/*` projekce, server-side
period filtering, XNYS data health, audit filtering/pagination a přesné Decimal JSON hodnoty.
Next.js App Router dashboard používá TypeScript, React, Tailwind a Node 24 LTS. Obsahuje všechny
operator stránky, tři paper grafy, oddělené expected-vs-realized evidence a explicitně potvrzené
safety actions. Backend `/` nyní odkazuje na API docs; legacy demo API zůstalo kompatibilní.

PAPER-only hranice zůstává Strategy → Portfolio → RiskEngine → ExecutionEngine →
PersistentPaperBroker. Dashboard nikdy nevolá broker, nemá live adapter ani live affordance.
Phase 7 immutable snapshoty jsou jediným zdrojem performance a provider correction je nemění.

Environmentální pending: lokální registry policy v pracovním prostředí vrací npm HTTP 403 a
instalovaný `uv 0.7.22` nevyhovuje povinnému `uv 0.12.3`; úplné locked/backend/frontend gates proto
musí potvrdit CI. Browser E2E není přidán, aby nevznikl falešný skeleton; component, safety a
OpenAPI contract checks jsou preferovaná stabilní hranice.
