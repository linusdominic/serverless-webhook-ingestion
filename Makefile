# Everything here runs against a local emulator with dummy credentials.
# No target can reach real AWS: see scripts/preflight.sh and infra/envs/local/provider.tf.

SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

-include .env

EMULATOR_PORT       ?= 45660
LOADGEN_SEED        ?= 20261117
LOADGEN_EVENTS      ?= 3000
LOADGEN_CONCURRENCY ?= 16
LAMBDA_ARCH         ?= $(shell docker info --format '{{.Architecture}}' 2>/dev/null | sed -e 's/aarch64/arm64/' -e 's/amd64/x86_64/')

# --- the sandbox ------------------------------------------------------------------
# Dummy credentials, an explicit loopback endpoint, empty credential files, and no
# profile. Exported to every recipe; preflight.sh re-checks them before each use.
SANDBOX := $(CURDIR)/.run/sandbox
unexport AWS_PROFILE AWS_DEFAULT_PROFILE AWS_SESSION_TOKEN AWS_ROLE_ARN AWS_WEB_IDENTITY_TOKEN_FILE AWS_SECURITY_TOKEN
export AWS_ACCESS_KEY_ID           := test
export AWS_SECRET_ACCESS_KEY       := test
export AWS_REGION                  := us-east-1
export AWS_DEFAULT_REGION          := us-east-1
export AWS_SHARED_CREDENTIALS_FILE := $(SANDBOX)/credentials
export AWS_CONFIG_FILE             := $(SANDBOX)/config
export AWS_EC2_METADATA_DISABLED   := true
export WEBHOOK_ENDPOINT_URL        := http://localhost:$(EMULATOR_PORT)
export AWS_ENDPOINT_URL            := $(WEBHOOK_ENDPOINT_URL)
export EMULATOR_PORT
# Progress lines appear as they happen, also when output is piped (CI logs).
export PYTHONUNBUFFERED            := 1

COMPOSE   := docker compose
TERRAFORM := $(COMPOSE) run --rm -T terraform
TF_VARS   := -var lambda_architecture=$(LAMBDA_ARCH)
PY        := uv run python
TOOL      := $(PY) -m webhook_tools

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

.PHONY: sandbox preflight
sandbox:
	@mkdir -p $(SANDBOX) && : > $(SANDBOX)/credentials && : > $(SANDBOX)/config

preflight: sandbox ## Check that nothing can reach real AWS
	@./scripts/preflight.sh

# --- emulator ---------------------------------------------------------------------

.PHONY: up down logs
up: preflight ## Start the emulator and wait until it is healthy
	$(COMPOSE) up -d --wait floci
	@curl -fsS $(WEBHOOK_ENDPOINT_URL)/_floci/health | $(PY) -c "import json,sys; d=json.load(sys.stdin); print('emulator: floci', d['version'], d['edition'])"

down: ## Stop the emulator and delete all of its state
	$(COMPOSE) --profile tools down -v --remove-orphans
	@./scripts/remove_emulator_volumes.sh
	@rm -rf infra/envs/local/terraform.tfstate infra/envs/local/terraform.tfstate.backup .run

logs: ## Tail emulator logs
	$(COMPOSE) logs -f floci

# --- build and deploy -------------------------------------------------------------

.PHONY: build deploy destroy outputs seed
build: ## Build the Lambda packages (core.zip, compactor.zip)
	$(PY) scripts/build_lambdas.py --arch $(LAMBDA_ARCH)

deploy: preflight build ## terraform apply against the emulator, then seed secrets
	$(TERRAFORM) init -input=false
	$(TERRAFORM) apply -input=false -auto-approve $(TF_VARS)
	@$(MAKE) --no-print-directory outputs seed

outputs: preflight
	@mkdir -p .run
	@$(TERRAFORM) output -json > .run/outputs.json

seed: preflight ## Write generated signing keys and the PII pepper into the emulator's Secrets Manager
	$(TOOL).seed

destroy: preflight ## terraform destroy against the emulator
	$(TERRAFORM) destroy -input=false -auto-approve $(TF_VARS)

# --- the proof --------------------------------------------------------------------

.PHONY: demo verify replay replay-forced negative-test status redrive
demo: preflight ## Send the seeded workload, wait for the queue to drain and for a scheduled pipeline run
	$(TOOL).loadgen --seed $(LOADGEN_SEED) --events $(LOADGEN_EVENTS) --concurrency $(LOADGEN_CONCURRENCY)
	$(TOOL).settle

verify: preflight ## Check every invariant; exits non-zero on any violation
	$(TOOL).verify

replay: preflight ## Re-drive the whole archive through the processor and show that nothing changes
	$(TOOL).replay --all --assert-unchanged
	$(TOOL).settle --replay

replay-forced: preflight ## Replay with the idempotency ledger bypassed; state and curated rows must still not change
	$(TOOL).replay --all --ignore-idempotency --assert-unchanged
	$(TOOL).settle --replay

negative-test: preflight ## Corrupt the system six ways and show the verifier catches each
	$(TOOL).negative

status: preflight ## Queues, stores, pipeline runs, alarm states
	@$(TOOL).status

redrive: preflight ## Move DLQ messages back to the ingest queue (ARGS=--dry-run to look first)
	$(TOOL).redrive $(ARGS)

# --- quality ----------------------------------------------------------------------

.PHONY: test test-integration lint typecheck fmt tf-fmt tf-validate tf-lint security-scan check
test: ## Unit tests
	uv run pytest

test-integration: preflight ## Integration tests against the deployed emulator stack
	uv run pytest tests/integration -m integration

lint: ## ruff (lint and format check) and mypy
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

fmt: ## Format Python and Terraform
	uv run ruff format .
	uv run ruff check --fix .
	$(COMPOSE) run --rm -T -w /workspace/infra terraform fmt -recursive

tf-fmt: ## terraform fmt -check
	$(COMPOSE) run --rm -T -w /workspace/infra terraform fmt -check -recursive -diff

tf-validate: build ## terraform validate on both roots (no credentials, no backend)
	$(COMPOSE) run --rm -T -w /workspace/infra/envs/local terraform init -backend=false -input=false >/dev/null
	$(COMPOSE) run --rm -T -w /workspace/infra/envs/local terraform validate
	$(COMPOSE) run --rm -T -w /workspace/infra/envs/aws terraform init -backend=false -input=false >/dev/null
	$(COMPOSE) run --rm -T -w /workspace/infra/envs/aws terraform validate

tf-lint: ## tflint on every module and root
	./scripts/tflint.sh

security-scan: ## checkov over the Terraform
	./scripts/security_scan.sh

check: lint test tf-fmt tf-validate tf-lint security-scan ## Everything that does not need the emulator
