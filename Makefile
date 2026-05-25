.PHONY: test test-coverage test-integration test-collect

test:
	pytest

test-coverage:
	pytest --cov=neuralcast --cov-report=term-missing

test-integration:
	pytest -m integration

test-collect:
	pytest --collect-only -q
