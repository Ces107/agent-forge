.PHONY: install test verify lint types security provenance trace forge-test forge-lint forge-types up pipeline

LEDGER := workspaces/ledger

install:
	cd $(LEDGER) && pip install -e ".[dev]"

# --- inner project (ledger) gates -------------------------------------------------------------

test:
	cd $(LEDGER) && python -m pytest

lint:
	cd $(LEDGER) && ruff check .

types:
	cd $(LEDGER) && mypy

security:
	cd $(LEDGER) && bandit -r src -c pyproject.toml

# --- meta-layer (forge) gates ----------------------------------------------------------------

# Spec<->task<->test traceability: every AC covered by a task, every task verified by a real test.
trace:
	python -m forge.traceability --spec forge/work/spec.md --tasks forge/work/tasks.md --tests-dir $(LEDGER)/tests

forge-test:
	python -m pytest forge/tests --cov=forge --cov-report=term-missing --cov-fail-under=90

forge-lint:
	ruff check forge --config forge/pyproject.toml

forge-types:
	mypy forge --config-file forge/pyproject.toml

provenance:
	python hooks/audit_provenance.py

# Full gate, same as CI.
verify: test lint types security trace forge-test forge-lint forge-types provenance

up:
	docker compose up --build

# Re-run the agent-forge pipeline from the mandate (requires the claude CLI).
pipeline:
	python -m forge.orchestrator --mandate forge/work/mandate.md
