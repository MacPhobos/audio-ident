.PHONY: install dev test lint fmt typecheck gen-client gen-client-from-file db-up db-down db-reset help

SERVICE_DIR := audio-ident-service
UI_DIR := audio-ident-ui
SERVICE_PORT ?= 17010
UI_PORT ?= 17000

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies
	cd $(SERVICE_DIR) && uv sync --all-extras
	cd $(UI_DIR) && pnpm install

dev: ## Start postgres, service, and UI (all three)
	@trap 'echo "Shutting down..."; kill 0; exit 0' INT TERM; \
	$(MAKE) db-up; \
	echo "Waiting for postgres..."; \
	until docker compose exec -T postgres pg_isready -U audio_ident > /dev/null 2>&1; do sleep 0.5; done; \
	echo "Postgres ready."; \
	cd $(SERVICE_DIR) && uv run alembic upgrade head; \
	cd $(SERVICE_DIR) && uv run uvicorn app.main:app --host 0.0.0.0 --port $(SERVICE_PORT) --reload & \
	cd $(UI_DIR) && pnpm dev --port $(UI_PORT) & \
	wait

test: ## Run all tests (pytest + vitest)
	cd $(SERVICE_DIR) && uv run pytest
	cd $(UI_DIR) && pnpm test

lint: ## Run linters (ruff + eslint)
	cd $(SERVICE_DIR) && uv run ruff check .
	cd $(UI_DIR) && pnpm lint

fmt: ## Run formatters (ruff + prettier)
	cd $(SERVICE_DIR) && uv run ruff format .
	cd $(UI_DIR) && pnpm format

typecheck: ## Run type checkers (pyright + svelte-check)
	cd $(SERVICE_DIR) && uv run pyright
	cd $(UI_DIR) && pnpm check

gen-client: ## Generate TS client from running service OpenAPI
	cd $(UI_DIR) && pnpm gen:api

gen-client-from-file: ## Generate TS client from spec file (usage: make gen-client-from-file SPEC=docs/openapi.json)
	@test -n "$(SPEC)" || (echo "Error: SPEC parameter required. Usage: make gen-client-from-file SPEC=path/to/openapi.json" && exit 1)
	cd $(UI_DIR) && npx openapi-typescript $(abspath $(SPEC)) -o src/lib/api/generated.ts

db-up: ## Start postgres via docker-compose
	docker compose up -d postgres

db-down: ## Stop postgres
	docker compose down

db-reset: ## Drop + recreate database + run migrations
	docker compose exec -T postgres dropdb -U audio_ident --if-exists audio_ident
	docker compose exec -T postgres createdb -U audio_ident audio_ident
	cd $(SERVICE_DIR) && uv run alembic upgrade head
