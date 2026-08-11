.PHONY: install setup check test format lint typecheck dev
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
