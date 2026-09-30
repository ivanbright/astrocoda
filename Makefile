# ---------------------------------------------------------------------------
# Astrocoda - convenience wrapper around docker compose.
# Every target maps to a plain `docker compose` command, documented in the
# README for users on platforms without make.
# ---------------------------------------------------------------------------
COMPOSE ?= docker compose

.DEFAULT_GOAL := help
.PHONY: help setup check test cli-install build up down restart logs ps shell-web shell-worker clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Create .env with a generated SECRET_KEY
	@python bootstrap.py

check: ## Validate .env against the Settings schema
	@python bootstrap.py --check

cli-install: ## Install the licensed `astrocoda` CLI into the active venv
	@pip install ./cli

test: ## Run the hermetic test suite (no services required)
	@python -m pytest

build: ## Build the application image
	$(COMPOSE) build

up: ## Start the full stack (postgres, redis, qdrant, web, worker)
	$(COMPOSE) up --build -d
	@echo ""
	@echo "  API    http://localhost:8000/docs"
	@echo "  Health http://localhost:8000/health"
	@echo "  Worker logs: make logs worker"

down: ## Stop the stack, keeping volumes
	$(COMPOSE) down

restart: ## Restart the API and worker containers
	$(COMPOSE) restart web worker

logs: ## Tail logs (make logs worker=web|worker)
	$(COMPOSE) logs -f $(filter-out $@,$(MAKECMDGOALS))

ps: ## Show container status
	$(COMPOSE) ps

shell-web: ## Open a shell inside the API container
	$(COMPOSE) exec web /bin/bash

shell-worker: ## Open a shell inside the worker container
	$(COMPOSE) exec worker /bin/bash

clean: ## Stop the stack and delete all volumes
	$(COMPOSE) down -v
