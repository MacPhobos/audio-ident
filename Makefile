.PHONY: install dev test lint fmt typecheck gen-client gen-client-from-file docker-up docker-down db-up db-down db-reset ingest rebuild-index eval-corpus eval-exact eval-vibe eval-latency eval-report eval-all help

SERVICE_DIR := audio-ident-service
UI_DIR := audio-ident-ui
SERVICE_PORT ?= 17010
UI_PORT ?= 17000
POSTGRES_MODE ?= docker
QDRANT_MODE ?= docker

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies
	cd $(SERVICE_DIR) && uv sync --all-extras
	cd $(UI_DIR) && pnpm install

dev: ## Start postgres, qdrant, service, and UI
	@trap 'echo "Shutting down..."; kill 0; exit 0' INT TERM; \
	$(MAKE) docker-up; \
	echo "Running alembic migrations..."; \
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

docker-up: ## Start Docker services (postgres + qdrant) based on mode variables
	@if [ "$(POSTGRES_MODE)" = "docker" ]; then \
		echo "Starting Postgres (docker)..."; \
		docker compose --profile postgres up -d; \
		echo "Waiting for Postgres to be ready..."; \
		until docker compose exec -T postgres pg_isready -U audio_ident > /dev/null 2>&1; do sleep 0.5; done; \
		echo "Postgres ready."; \
	else \
		echo "Postgres mode=$(POSTGRES_MODE), skipping Docker."; \
	fi
	@if [ "$(QDRANT_MODE)" = "docker" ]; then \
		echo "Starting Qdrant (docker)..."; \
		docker compose --profile qdrant up -d; \
		echo "Waiting for Qdrant to be ready..."; \
		until docker compose exec -T qdrant bash -c 'exec 3<>/dev/tcp/localhost/6333 && echo -e "GET /healthz HTTP/1.0\r\nHost: localhost\r\n\r\n" >&3 && cat <&3 | grep -q "200 OK"' > /dev/null 2>&1; do sleep 0.5; done; \
		echo "Qdrant ready."; \
	else \
		echo "Qdrant mode=$(QDRANT_MODE), skipping Docker."; \
	fi

docker-down: ## Stop all Docker services
	docker compose --profile postgres --profile qdrant down

db-reset: ## Drop + recreate database, reset Qdrant collection, run migrations
	$(MAKE) docker-down
	docker volume rm -f $$(docker volume ls -q --filter name=audio-ident_qdrant_data) 2>/dev/null || true
	$(MAKE) docker-up
	docker compose exec -T postgres dropdb -U audio_ident --if-exists audio_ident
	docker compose exec -T postgres createdb -U audio_ident audio_ident
	cd $(SERVICE_DIR) && uv run alembic upgrade head

ingest: ## Ingest audio files (usage: make ingest AUDIO_DIR=/path/to/mp3s)
	@test -n "$(AUDIO_DIR)" || (echo "Error: AUDIO_DIR required. Usage: make ingest AUDIO_DIR=/path/to/mp3s" && exit 1)
	cd $(SERVICE_DIR) && uv run python -m app.ingest "$(AUDIO_DIR)"

rebuild-index: ## Drop computed data and rebuild from raw audio
	@echo "WARNING: This will drop Qdrant collection and Olaf LMDB."
	@echo "Press Ctrl+C to cancel, or wait 5 seconds..."
	@sleep 5
	@echo "Clearing Olaf LMDB index..."
	rm -rf $${OLAF_LMDB_PATH:-$(SERVICE_DIR)/data/olaf_db}/*
	@echo "Dropping Qdrant collection..."
	curl -sf -X DELETE "http://localhost:$${QDRANT_HTTP_PORT:-6333}/collections/$${QDRANT_COLLECTION_NAME:-audio_embeddings}" || true
	@echo "Re-ingesting from raw audio..."
	cd $(SERVICE_DIR) && uv run python -m app.ingest "$${AUDIO_STORAGE_ROOT:-./data}/raw"

eval-corpus: ## Build evaluation test corpus (usage: make eval-corpus AUDIO_DIR=/path/to/mp3s)
	@test -n "$(AUDIO_DIR)" || (echo "Error: AUDIO_DIR required. Usage: make eval-corpus AUDIO_DIR=/path/to/mp3s" && exit 1)
	cd $(SERVICE_DIR) && uv run python scripts/build_eval_corpus.py --audio-dir "$(AUDIO_DIR)"

eval-exact: ## Run exact ID (fingerprint) evaluation
	cd $(SERVICE_DIR) && uv run python scripts/eval_exact.py

eval-vibe: ## Run vibe search evaluation (generates rating sheet for human scoring)
	cd $(SERVICE_DIR) && uv run python scripts/eval_vibe.py

eval-latency: ## Run end-to-end latency benchmark via HTTP
	cd $(SERVICE_DIR) && uv run python scripts/eval_latency.py

eval-report: ## Generate go/no-go evaluation report from results
	cd $(SERVICE_DIR) && uv run python scripts/eval_report.py

eval-all: eval-exact eval-vibe eval-latency eval-report ## Run full evaluation pipeline (run eval-corpus first)

# Backward compatibility aliases
db-up: docker-up ## (alias) Start Docker services
db-down: docker-down ## (alias) Stop Docker services
