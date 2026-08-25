.PHONY: install setup check test format lint typecheck dev api dashboard frontend-test frontend-install frontend-check frontend-lock-check generate-dev-secrets security-check frontend-security production-build production-up production-down production-smoke db-backup db-restore db-configure-runtime
install:
	cd backend && uv sync --locked --all-groups
setup: install
check:
	cd backend && uv lock --check
	cd backend && uv run ruff format --check .
	cd backend && uv run ruff check .
	cd backend && uv run mypy src/quantlab
test:
	cd backend && uv run pytest
format:
	cd backend && uv run ruff format .
lint:
	cd backend && uv run ruff check .
typecheck:
	cd backend && uv run mypy
dev:
	cd backend && uv run uvicorn quantlab.api:app --reload

api:
	cd backend && uv run uvicorn quantlab.api:app --host 127.0.0.1 --port 8000
dashboard:
	cd frontend && npm run dev
frontend-test:
	cd frontend && npm run lint && npm run typecheck && npm test && npm run build
frontend-install:
	cd frontend && npm ci
frontend-check:
	cd frontend && npm run lint && npm run typecheck && npm test && npm run build
frontend-lock-check:
	cd frontend && npm run lockfile:check && npm ci
generate-dev-secrets:
	./scripts/generate-dev-secrets.sh
security-check:
	cd backend && uv run ruff check --select S src
frontend-security:
	cd frontend && npm audit --omit=dev --audit-level=high && npm audit --audit-level=critical
production-build:
	docker compose -f docker-compose.production.yml build
production-up:
	docker compose -f docker-compose.production.yml up -d
production-down:
	docker compose -f docker-compose.production.yml down
production-smoke:
	./scripts/production-smoke.sh
db-backup:
	./scripts/db-backup.sh "$(BACKUP)"
db-restore:
	./scripts/db-restore.sh "$(BACKUP)"
db-configure-runtime:
	psql "$(MIGRATION_DATABASE_URL)" -v runtime_role="$(RUNTIME_ROLE)" -f scripts/configure-runtime-role.sql
