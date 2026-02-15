# ============================================================================
# audio-ident-service — FastAPI backend
# ============================================================================
# Subproject Makefile for independent development.
# Run from: audio-ident-service/
# ============================================================================

SERVICE_PORT ?= 17010
SERVICE_HOST ?= 0.0.0.0

.DEFAULT_GOAL := help

# === Help ===

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === Setup ===

.PHONY: install
install: ## Install Python dependencies
	uv sync --all-extras

# === Development ===

.PHONY: dev
dev: ## Start dev server with hot reload
	uv run uvicorn app.main:app --host $(SERVICE_HOST) --port $(SERVICE_PORT) --reload

.PHONY: test
test: ## Run test suite
	uv run pytest

.PHONY: lint
lint: ## Lint code
	uv run ruff check .

.PHONY: lint-fix
lint-fix: ## Lint and auto-fix
	uv run ruff check . --fix

.PHONY: fmt
fmt: ## Format code
	uv run ruff format .

.PHONY: typecheck
typecheck: ## Type check
	uv run pyright

# === Database ===

.PHONY: migrate
migrate: ## Run database migrations
	uv run alembic upgrade head

.PHONY: migrate-new
migrate-new: ## Create new migration (usage: make migrate-new MSG="add users table")
	uv run alembic revision --autogenerate -m "$(MSG)"

# === Data Management ===

.PHONY: ingest
ingest: ## Ingest audio files (usage: make ingest AUDIO_DIR=/path/to/mp3s)
	@test -n "$(AUDIO_DIR)" || (echo "Error: AUDIO_DIR required. Usage: make ingest AUDIO_DIR=/path/to/mp3s" && exit 1)
	uv run python -m app.ingest "$(AUDIO_DIR)"

.PHONY: rebuild-index
rebuild-index: ## Drop computed data and rebuild from raw audio
	@echo "WARNING: This will drop Qdrant collection and Olaf LMDB."
	@echo "Press Ctrl+C to cancel, or wait 5 seconds..."
	@sleep 5
	@echo "Clearing Olaf LMDB index..."
	rm -rf data/olaf_db/*
	@echo "Dropping Qdrant collection..."
	curl -sf -X DELETE "http://localhost:$${QDRANT_HTTP_PORT:-6333}/collections/$${QDRANT_COLLECTION_NAME:-audio_embeddings}" || true
	@echo "Re-ingesting from raw audio..."
	uv run python -m app.ingest "$${AUDIO_STORAGE_ROOT:-./data}/raw"

# === Evaluation ===

.PHONY: eval-corpus
eval-corpus: ## Build evaluation test corpus (usage: make eval-corpus AUDIO_DIR=/path/to/mp3s)
	@test -n "$(AUDIO_DIR)" || (echo "Error: AUDIO_DIR required. Usage: make eval-corpus AUDIO_DIR=/path/to/mp3s" && exit 1)
	uv run python scripts/build_eval_corpus.py --audio-dir "$(AUDIO_DIR)"

.PHONY: eval-exact
eval-exact: ## Run exact ID (fingerprint) evaluation
	uv run python scripts/eval_exact.py

.PHONY: eval-vibe
eval-vibe: ## Run vibe search evaluation
	uv run python scripts/eval_vibe.py

.PHONY: eval-latency
eval-latency: ## Run end-to-end latency benchmark via HTTP
	uv run python scripts/eval_latency.py

.PHONY: eval-report
eval-report: ## Generate go/no-go evaluation report
	uv run python scripts/eval_report.py

.PHONY: eval-all
eval-all: eval-exact eval-vibe eval-latency eval-report ## Run full evaluation pipeline (run eval-corpus first)

# === Cleanup ===

.PHONY: clean
clean: ## Clean build artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; rm -rf .pytest_cache .ruff_cache .mypy_cache
