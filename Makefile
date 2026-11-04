IMAGE     ?= vrtfinland/netvisor-mock
PLATFORMS ?= linux/amd64,linux/arm64
# A buildx builder with the docker-container driver is needed for multi-platform builds, e.g.
# `docker buildx create --name multiarch --driver docker-container`, then `make publish BUILDER=multiarch`.
BUILDER   ?=
VCS_REF   := $(shell git rev-parse --short HEAD)
BUILDX    := docker buildx build $(if $(BUILDER),--builder $(BUILDER)) --build-arg VCS_REF=$(VCS_REF)

.PHONY: help install lint format typecheck test check build publish

help: ## Show the targets
	@grep -E '^[a-z]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## Install the locked dependencies
	uv sync --locked

lint: ## Lint and check formatting
	uv run ruff check
	uv run ruff format --check

format: ## Fix lint findings and format
	uv run ruff check --fix
	uv run ruff format

typecheck: ## Type check
	uv run ty check

test: ## Run the tests
	uv run python -W error::DeprecationWarning -m unittest

check: lint typecheck test ## Everything CI would run

build: ## Build $(IMAGE):dev for this machine's platform
	$(BUILDX) --load -t $(IMAGE):dev .

publish: check ## Build and push $(IMAGE):$(TAG) for $(PLATFORMS), e.g. make publish TAG=6
	@test -n "$(TAG)" || { echo "Set TAG, e.g. make publish TAG=6" >&2; exit 1; }
	@test -z "$$(git status --porcelain)" || { echo "Commit or stash changes first: the image must match $(VCS_REF)" >&2; exit 1; }
	@! docker buildx imagetools inspect $(IMAGE):$(TAG) >/dev/null 2>&1 || { echo "$(IMAGE):$(TAG) already exists" >&2; exit 1; }
	$(BUILDX) --platform $(PLATFORMS) -t $(IMAGE):$(TAG) --push .
	docker buildx imagetools inspect $(IMAGE):$(TAG)
