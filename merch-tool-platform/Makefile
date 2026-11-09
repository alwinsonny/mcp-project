# Loads .env if present, so MERCH_* variables reach every command.
-include .env
export

# Every command uses the Python inside .venv, so you do not need to activate it first.
PY := .venv/bin/python

.PHONY: venv setup db-create migrate seed test test-fast format lint typecheck check

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