.PHONY: test install install-deps clean lint

# Run tests immediately — no install required
# conftest.py adds packages to sys.path automatically
test:
	pytest --tb=short -q

# Install runtime dependencies (optional — needed for production use)
install-deps:
	pip install -r requirements.txt 2>/dev/null || \
	pip install --break-system-packages -r requirements.txt

# Install all packages in editable mode (production/CI)
install:
	python3 setup.py
	pip install pytest pytest-asyncio httpx 2>/dev/null || \
	pip install --break-system-packages pytest pytest-asyncio httpx

lint:
	ruff check packages/ tests/ 2>/dev/null || echo "pip install ruff"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null; true
	rm -rf .venv .venv2
