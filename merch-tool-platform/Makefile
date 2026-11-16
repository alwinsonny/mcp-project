# Loads .env if present, so MERCH_* variables reach every command.
-include .env
export

# Every command uses the Python inside .venv, so you do not need to activate it first.
PY := .venv/bin/python

.PHONY: venv setup db-create migrate seed serve inspect snapshot test test-fast format lint typecheck check serve-http cluster keygen inspect-http

venv:             ## create .venv and install all packages
	test -d .venv || python3 -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -r requirements-dev.txt
	$(PY) -m pip install -e . --no-deps

db-create:        ## create the merch user and two databases in your local Postgres
	psql -d postgres -c "CREATE USER merch WITH PASSWORD 'merch' CREATEDB;" || true
	createdb -O merch merch || true
	createdb -O merch merch_test || true

setup: venv       ## first-time setup: install, create databases, tables and data
	test -f .env || cp .env.example .env
	$(MAKE) db-create migrate seed

migrate:
	$(PY) -m alembic upgrade head

seed:
	$(PY) -m merch_platform.seed

serve:            ## run the MCP server over stdio (it waits silently for a client)
	$(PY) -m merch_platform.server

inspect:          ## open the MCP Inspector in your browser, connected to this server
	npx -y @modelcontextprotocol/inspector $(PY) -m merch_platform.server

serve-http:       ## run the MCP server over HTTP at http://127.0.0.1:8000/mcp (Phase 6)
	$(PY) -m merch_platform.server.http

cluster:          ## two server copies behind a round-robin balancer at :8080 (Phase 6)
	$(PY) -m merch_platform.devtools.cluster

keygen:           ## print a new secret for MERCH_REQUEST_STATE_KEYS
	@$(PY) -c "import secrets; print(secrets.token_urlsafe(32))"

inspect-http:     ## open the MCP Inspector, connected to the HTTP server (start it first)
	npx -y @modelcontextprotocol/inspector --transport http --server-url http://127.0.0.1:8000/mcp

snapshot:         ## accept the current tool catalogue as approved (review the git diff!)
	UPDATE_SNAPSHOT=1 $(PY) -m pytest tests/server/test_catalogue_snapshot.py -q -rs

test-fast:        ## tests that need no database
	$(PY) -m pytest -m "not integration"

test:             ## all tests, including loading data into the test database
	$(PY) -m pytest

format:
	$(PY) -m ruff format src tests

lint:
	$(PY) -m ruff format --check src tests
	$(PY) -m ruff check src tests

typecheck:
	$(PY) -m mypy
	$(PY) -m mypy tests

check: lint typecheck test