# merch-tool-platform

Project 04 of the agentic AI portfolio ladder: a governed MCP tool platform that lets AI
agents search, inspect and safely change a shop's catalogue, prices, stock and promotions.

**Current state: Phase 0 complete** (project setup, data shapes, database tables and a
reproducible fake shop). No business rules or MCP code yet; those are Phases 1 and 2.

## Prerequisites (macOS)

| Tool | Check with | Install |
| --- | --- | --- |
| Python 3.12+ | `python3 --version` | `brew install python@3.12` |
| Postgres 14+ | `psql --version` | `brew install postgresql@16 && brew services start postgresql@16` |
| make, Git | `make --version` | `xcode-select --install` |

## Setup

```bash
python3 -m venv .venv                   # 1. create the virtual environment
source .venv/bin/activate               # 2. switch it on; the prompt shows (.venv)
pip install --upgrade pip
pip install -r requirements-dev.txt     # 3. install the packages
pip install -e . --no-deps              # 4. let Python find the merch_platform code
cp .env.example .env                    # 5. local settings
make db-create                          # 6. create the merch user and two databases
alembic upgrade head                    # 7. create the tables
python -m merch_platform.seed           # 8. load the 5,005-product shop
make check                              # 9. formatting, lint, types and all tests
```

`make setup` runs steps 1 to 8 for you. `make test-fast` runs only the tests that need
no database.

## What Phase 0 contains

```text
src/merch_platform/
  config.py              settings read from .env
  domain/models.py       the shapes of shop data + money helpers
  db/tables.py           every database table
  db/migrations/         Alembic: 0001 creates the tables
  seed/                  deterministic fake shop + loader + command
tests/
  unit/                  money maths and seed determinism (no database)
  integration/           migration and loading against real Postgres
docs/
  adr/0001-…             why one Postgres database holds everything
  data-traps.md          the deliberately awkward records in the data
```