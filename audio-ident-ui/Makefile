# ============================================================================
# audio-ident-ui — SvelteKit frontend
# ============================================================================
# Subproject Makefile for independent development.
# Run from: audio-ident-ui/
# ============================================================================

UI_PORT ?= 17000
API_URL ?= http://localhost:17010

.DEFAULT_GOAL := help

# === Help ===

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# === Setup ===

.PHONY: install
install: ## Install Node dependencies
	pnpm install

# === Development ===

.PHONY: dev
dev: ## Start dev server
	pnpm dev --port $(UI_PORT)

.PHONY: build
build: ## Production build
	pnpm build

.PHONY: preview
preview: ## Preview production build
	pnpm preview --port $(UI_PORT)

.PHONY: test
test: ## Run tests
	pnpm test

.PHONY: test-watch
test-watch: ## Run tests in watch mode
	pnpm test:watch

.PHONY: lint
lint: ## Lint code
	pnpm lint

.PHONY: fmt
fmt: ## Format code
	pnpm format

.PHONY: typecheck
typecheck: ## Type check
	pnpm check

.PHONY: typecheck-watch
typecheck-watch: ## Type check in watch mode
	pnpm check:watch

# === Code Generation ===

.PHONY: gen-client
gen-client: ## Generate API client types from running backend
	VITE_API_BASE_URL=$(API_URL) pnpm gen:api

# === Cleanup ===

.PHONY: clean
clean: ## Clean build artifacts
	rm -rf .svelte-kit node_modules/.vite build
