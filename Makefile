.PHONY: install test lint install-test clean

install:
	python3 setup.py
	pip3 install pytest pytest-asyncio httpx 2>/dev/null || \
	pip3 install --break-system-packages pytest pytest-asyncio httpx

test:
	pytest --tb=short -q

install-test: install test

lint:
	ruff check packages/ tests/ 2>/dev/null || echo "ruff not installed"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; true
