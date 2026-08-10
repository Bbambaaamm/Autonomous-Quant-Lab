.PHONY: setup test format lint typecheck dev
setup:
	cd backend && uv sync --all-groups
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
