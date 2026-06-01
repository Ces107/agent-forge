.PHONY: install test verify lint types security provenance up pipeline

LEDGER := workspaces/ledger

install:
	cd $(LEDGER) && pip install -e ".[dev]"

test:
	cd $(LEDGER) && python -m pytest

lint:
	cd $(LEDGER) && ruff check .

types:
	cd $(LEDGER) && mypy

security:
	cd $(LEDGER) && bandit -r src -c pyproject.toml

provenance:
	python hooks/audit_provenance.py

# Full gate, same as CI.
verify: test lint types security provenance

up:
	docker compose up --build

# Re-run the agent-forge pipeline from the mandate (requires the claude CLI).
pipeline:
	python forge/orchestrator.py --mandate forge/work/mandate.md
