PYTHON ?= .venv/bin/python

bootstrap:
	bash scripts/bootstrap.sh

test:
	bash scripts/test_all.sh

run:
	PYTHON_BIN=$(PYTHON) $(PYTHON) scripts/run_api.py

frontend-build:
	cd frontend && npm ci && npm run build

verify:
	$(PYTHON) ../../scripts/verify_architecture.py
