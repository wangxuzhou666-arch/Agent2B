.PHONY: install test lint leak-scan demo eval e2e up up-ollama down logs smoke

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check .

leak-scan:
	bash scripts/leak_scan.sh --all

demo:
	uv run twotrack demo

eval:
	@echo "make eval is not implemented yet"

# Playwright: starts the API (demo mode, temp data) and a production build of the console;
# see web/playwright.config.ts for E2E_API_URL / E2E_WEB_URL to reuse running servers.
# It runs the fixed flows and then the exploration suite, which needs the Pi worker installed:
# `npm ci --ignore-scripts` in harness/pi. Without it the exploration API refuses to start and the
# run fails.
e2e:
	cd web && pnpm e2e

# Docker: the API and the console with no API key (demo mode, mock model, cold-chain plugin; a
# question no report covers runs in the real Pi agent loop). Ports, the local-model switch and
# the security notes are in docker-compose.yml.
# `docker compose port` reports the real host port, so the URL is right even with a custom WEB_PORT.
up:
	docker compose up -d --build --wait
	@echo "twotrack is up: http://localhost:$$(docker compose port web 3000 | sed 's/.*://')"

# The same, plus a local model through the bundled Ollama; the first run downloads the model
# (OLLAMA_MODEL=name overrides the default). Compose profiles cannot reconfigure the API, so the
# provider switch is set here. The model timeouts are not: docker-compose.yml already sizes them
# for a model on the CPU (TWOTRACK_LLM_TIMEOUT_SECONDS / TWOTRACK_LLM_ROUTE_TIMEOUT_SECONDS).
up-ollama:
	TWOTRACK_PROVIDER=openai_compat docker compose --profile ollama up -d --build --wait
	@echo "twotrack is up with a local model: http://localhost:$$(docker compose port web 3000 | sed 's/.*://')"

# Checks the stack that `make up` started, with no key and no model: signs in as the demo
# operator, asks one question a fixed flow answers and one that no fixed flow covers, and checks
# that the second ran in Pi against the mock model (its trace says runtime pi, model mock).
# Needs python3 and nothing else. For a stack under another project name:
# COMPOSE_PROJECT_NAME=name make smoke. Exit status 0 only if every check passed.
smoke:
	@port=$$(docker compose port api 8000 | sed 's/.*://'); \
	if [ -z "$$port" ]; then echo "the api service is not running: start it with 'make up'" >&2; exit 1; fi; \
	python3 scripts/smoke.py "http://127.0.0.1:$$port"

# Stops and removes the containers and keeps the data and model volumes. Add -v to wipe them:
#   docker compose --profile '*' down -v
# --profile '*' so services started under the ollama profile are stopped too.
down:
	docker compose --profile '*' down

logs:
	docker compose --profile '*' logs -f --tail=100
