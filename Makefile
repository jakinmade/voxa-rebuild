.PHONY: install test lint install-test clean

install:
	pip install -r requirements.txt 2>/dev/null || \
	pip install --break-system-packages -r requirements.txt

test:
	pytest --tb=short -q

install-test: install test

lint:
	ruff check packages/ tests/ 2>/dev/null || echo "ruff not installed — pip install ruff"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; true
