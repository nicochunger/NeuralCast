PYTHON ?= .venv/bin/python
PYTEST := $(PYTHON) -m pytest

.PHONY: test test-unit test-boundary test-coverage test-integration test-live \
	test-collect clean

test:
	$(PYTEST)

test-unit:
	$(PYTEST) tests/unit

test-boundary:
	$(PYTEST) tests/boundary

test-coverage:
	$(PYTEST) --cov=neuralcast --cov-report=term-missing

test-integration:
	$(PYTEST) -m integration

test-live:
	$(PYTEST) -m live

test-collect:
	$(PYTEST) --collect-only -q

clean:
	find src tests -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
	find src tests -depth -type d -name '__pycache__' -delete
	for path in __pycache__ .pytest_cache build; do \
		if [ -d "$$path" ]; then find "$$path" -depth -delete; fi; \
	done
	find src -maxdepth 1 -type d -name '*.egg-info' -exec find {} -depth -delete \;
