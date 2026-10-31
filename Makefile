.PHONY: install dev test lint typecheck web-install web-build rust-test verify
install:
	python3 -m venv .venv && .venv/bin/pip install -e "services/orchestrator[dev]"
dev:
	PYTHONPATH=services/orchestrator/src .venv/bin/uvicorn voxora.api:app --reload --port 8000
test:
	PYTHONPATH=services/orchestrator/src .venv/bin/pytest services/orchestrator/tests services/ai-runtime/tests -q
lint:
	.venv/bin/ruff check services/orchestrator services/ai-runtime
typecheck:
	PYTHONPATH=services/orchestrator/src .venv/bin/mypy services/orchestrator/src/voxora
web-install:
	cd apps/console && npm install
web-build:
	cd apps/console && npm run build
rust-test:
	cargo test --workspace
verify: lint typecheck test web-build rust-test
