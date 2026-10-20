# Prime Agent on Cloudera AI: local developer entrypoints.
# Every target works from a fresh clone with a filled-in .env.
SHELL := /bin/bash
IMAGE ?= christopheraburns/prime-agent-runtime:0.10.0
COMPOSE := docker compose --env-file .env -f deploy/compose/docker-compose.yml
PY := python3

.PHONY: help env up down logs render data smoke deploy-cml screens image

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

env: ## Create .env from .env.example if missing
	@test -f .env || (cp .env.example .env && echo "created .env; edit MISTRAL_API_KEY")

image: ## Build the runtime image (amd64)
	docker build --platform linux/amd64 -t $(IMAGE) -f image/Dockerfile image

render: env ## Render models.json and settings.json from .env into the agent dir
	set -a && source .env && set +a && $(PY) scripts/render_config.py

up: env ## Start the local stack (Impala quickstart, bridge, console)
	@test -f deploy/compose/docker-compose.yml || (echo "deploy/compose/docker-compose.yml not written yet (BURNDOWN C1)"; exit 1)
	$(COMPOSE) up -d --build

down: ## Stop the local stack
	-$(COMPOSE) down

logs: ## Tail local stack logs
	$(COMPOSE) logs -f --tail=100

data: ## Generate and load DATA_SCALE rows into Iceberg via Impala
	@test -f data/generate.py || (echo "data/generate.py not written yet (BURNDOWN D3)"; exit 1)
	$(COMPOSE) exec bridge bash -lc 'python /home/cdsw/data/generate.py && python /home/cdsw/data/load.py'

smoke: ## Headless Prime Agent answers one lakehouse question
	@test -f scripts/smoke.sh || (echo "scripts/smoke.sh not written yet (BURNDOWN C7/E7)"; exit 1)
	$(COMPOSE) exec bridge bash -lc '/home/cdsw/scripts/smoke.sh'

deploy-cml: ## Create or update the Workbench applications with cmlapi
	@test -f deploy/cml/deploy.py || (echo "deploy/cml/deploy.py not written yet (BURNDOWN I3)"; exit 1)
	set -a && source .env && set +a && $(PY) deploy/cml/deploy.py

screens: ## Collect screenshot checklist into docs/screens
	@mkdir -p docs/screens && echo "drop screenshots into docs/screens/ (see PLAN.md section 6)"
