IMAGE_NAME := vrtfinland/netvisor-mock
# As in the VRTfront microservices: the git tag without a "v" prefix (e.g. 6), overridable with DOCKER_TAG.
DOCKER_TAG ?= $(shell git describe --tags --always | sed -e 's/^v//')
IMAGE_TAG  := $(DOCKER_TAG)
PLATFORMS  := linux/amd64,linux/arm64
# Optional buildx builder for the multi-platform targets; empty uses the current one.
BUILDER    ?=
VCS_REF    := $(shell git rev-parse --short HEAD)
BUILDX     := docker buildx build $(if $(BUILDER),--builder $(BUILDER)) --build-arg VCS_REF=$(VCS_REF)

.PHONY: help install lint format typecheck test check build push buildx-build buildx-build-push start-image

help: ## Show the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-18s %s\n", $$1, $$2}'

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

check: lint typecheck test ## Lint, type check and test

build: ## Build $(IMAGE_NAME):$(IMAGE_TAG) for this machine
	@docker build --pull --build-arg VCS_REF=$(VCS_REF) -t $(IMAGE_NAME):$(IMAGE_TAG) .

push: ## Push the image built with make build
	@docker push $(IMAGE_NAME):$(IMAGE_TAG)

# GISGRO runs amd64 in the cluster and arm64 on developers' Macs, so a published tag has to carry both.
buildx-build: ## Build $(IMAGE_NAME):$(IMAGE_TAG) for linux/amd64 and linux/arm64
	@$(BUILDX) --platform=$(PLATFORMS) -t $(IMAGE_NAME):$(IMAGE_TAG) .

buildx-build-push: check ## Build and push linux/amd64 and linux/arm64 as $(IMAGE_NAME):$(IMAGE_TAG)
	@test -z "$$(git status --porcelain)" || { echo "Commit or stash changes first: the image must match $(VCS_REF)" >&2; exit 1; }
	@! docker buildx imagetools inspect $(IMAGE_NAME):$(IMAGE_TAG) >/dev/null 2>&1 || { echo "$(IMAGE_NAME):$(IMAGE_TAG) already exists" >&2; exit 1; }
	@echo "Building amd64 & arm64 versions $(IMAGE_TAG)..."
	@$(BUILDX) --platform=$(PLATFORMS) --push -t $(IMAGE_NAME):$(IMAGE_TAG) .
	@docker buildx imagetools inspect $(IMAGE_NAME):$(IMAGE_TAG)

start-image: ## Run the image built with make build on port 5001
	@docker run --rm -p 5001:5001 $(IMAGE_NAME):$(IMAGE_TAG)
