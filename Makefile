# Prime Agent on Cloudera AI: local developer entrypoints.
# Every target works from a fresh clone with a filled-in .env.
SHELL := /bin/bash
IMAGE ?= christopheraburns/prime-agent-runtime:0.10.0
PROFILES ?=
COMPOSE := docker compose --env-file .env -f deploy/compose/docker-compose.yml $(foreach p,$(PROFILES),--profile $(p))
PY := python3
# Meridian (separate repo) generates the data; see its config/demo.yaml.
MERIDIAN_PUBLIC_DIR ?= ../meridian/data/demo-public

.PHONY: help env up down logs render data validate-data smoke deploy-cml screens image shell impala-shell tunnel test schema

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "created .env; edit MISTRAL_API_KEY")

image: ## Build the runtime image (amd64)
	docker build --platform linux/amd64 -t $(IMAGE) -f image/Dockerfile image

render: env ## Render models.json and settings.json from .env into the agent dir
	set -a && source .env && set +a && $(PY) scripts/render_config.py

up: env ## Start the local stack (Impala quickstart, bridge, console); add PROFILES=ollama
	$(COMPOSE) up -d
	@echo "bridge  http://localhost:18080/   console http://localhost:18090/   impala UI http://localhost:25000/"

down: ## Stop the local stack; V=1 also deletes the warehouse volume
	-$(COMPOSE) --profile ollama --profile tunnel --profile tools down $(if $(V),-v,)

logs: ## Tail local stack logs
	$(COMPOSE) logs -f --tail=100

data: ## Stage a Meridian public run (MERIDIAN_PUBLIC_DIR) and load it into Iceberg; REPLACE=1 swaps out a loaded run
	$(PY) data/stage.py --source $(MERIDIAN_PUBLIC_DIR)
	$(COMPOSE) exec -T bridge bash -lc 'python -u /home/cdsw/data/load.py $(if $(REPLACE),--replace,)'

validate-data: ## Bankwide SQL checks of the loaded data; writes docs/data-validation.md and a chart
	$(COMPOSE) exec -T bridge bash -lc 'python -u /home/cdsw/scripts/validate_bankwide.py'

smoke: ## Connectivity, Iceberg DDL probe, and a Prime Agent run if MISTRAL_API_KEY is set
	$(COMPOSE) exec bridge bash -lc '/home/cdsw/scripts/smoke.sh'

shell: ## Shell inside the bridge container (same image as Workbench)
	$(COMPOSE) exec bridge bash

schema: ## Apply schema/*.sql to the local Impala (production tables, then ground truth)
	$(COMPOSE) --profile tools run --rm -T impala-shell -q "CREATE DATABASE IF NOT EXISTS meridian_financial"
	$(COMPOSE) --profile tools run --rm -T -v "$(CURDIR)/schema:/schema:ro" impala-shell -f /schema/meridian_financial_iceberg.sql
	$(COMPOSE) --profile tools run --rm -T -v "$(CURDIR)/schema:/schema:ro" impala-shell -f /schema/meridian_financial_private_iceberg.sql

impala-shell: ## Interactive impala-shell against the local quickstart
	$(COMPOSE) --profile tools run --rm impala-shell

tunnel: ## Start the cloudflared tunnel and print its public URL
	$(COMPOSE) --profile tunnel up -d tunnel
	@sleep 6; $(COMPOSE) --profile tunnel logs tunnel 2>&1 | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || echo "see: make logs"

test: ## Unit tests (metadata backends)
	$(PY) -m pytest -q tests

deploy-cml: ## Create or update the Workbench applications with cmlapi
	@test -f deploy/cml/deploy.py || (echo "deploy/cml/deploy.py not written yet (BURNDOWN I3)"; exit 1)
	set -a && source .env && set +a && $(PY) deploy/cml/deploy.py

screens: ## Collect screenshot checklist into docs/screens
	@mkdir -p docs/screens && echo "drop screenshots into docs/screens/ (see PLAN.md section 6)"
