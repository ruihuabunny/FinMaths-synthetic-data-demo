VENV := .venv
PYTHON := $(VENV)/bin/python
EDITOR := $(PYTHON) scripts/edit_snapshot.py
CONFIG := configs/generators/quantlib_bsm_metals_option_chain_smoke_v2.json
PUBLIC_CONFIG := configs/generators/quantlib_bsm_metals_option_chain_smoke_v1.json
DATABASE ?= /tmp/metals-liquid-tdgbm-q-v4.duckdb
PUBLIC_DATABASE := snapshots/public/quantlib_bsm_smoke_v1.duckdb

.PHONY: venv install smoke append-day snapshot-summary test

venv:
	python3 -m venv $(VENV)

install: venv
	$(PYTHON) -m pip install -r requirements.lock
	$(PYTHON) -m pip install --no-build-isolation --no-deps -e .

smoke:
	$(EDITOR) --database $(DATABASE) --config $(CONFIG) create-smoke

append-day:
	$(EDITOR) --database $(DATABASE) --config $(CONFIG) append-dates --days 1

snapshot-summary:
	$(EDITOR) --database $(PUBLIC_DATABASE) --config $(PUBLIC_CONFIG) summary

test:
	$(PYTHON) -m pytest
