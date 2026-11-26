PYTHON ?= python3
export PYTHON

.PHONY: check fetch build verify

check:
	$(PYTHON) -m ruff format --check scripts
	$(PYTHON) -m ruff check scripts
	$(PYTHON) -m mypy --strict scripts/*.py

fetch:
	mkdir -p out
	flock out/build.lock $(PYTHON) scripts/inputs.py

build:
	mkdir -p out
	flock out/build.lock $(PYTHON) scripts/build.py

verify:
	flock out/build.lock $(PYTHON) scripts/verify.py
