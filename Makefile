.PHONY: up up-sim down logs backend frontend backend-test backend-lint frontend-build frontend-lint contracts fixtures check

up:
	docker compose up --build

up-sim:
	docker compose --profile sim up --build

down:
	docker compose down

logs:
	docker compose logs -f

backend:
	cd apps/api && uv run uvicorn app.main:app --reload --port 8000

frontend:
	cd apps/web && npm run dev

backend-test:
	cd apps/api && uv run pytest

backend-lint:
	cd apps/api && uv run ruff check app tests

frontend-build:
	cd apps/web && npm run build

frontend-lint:
	cd apps/web && npm run lint

# Regenerate the API contract and frontend types after changing app/domain/models.py or routes.
contracts:
	cd apps/api && uv run python -m app.export_openapi
	cd apps/web && npm run gen:api

fixtures:
	cd apps/api && uv run python ../../tests/fixtures/generate.py

check: backend-lint backend-test frontend-lint frontend-build
