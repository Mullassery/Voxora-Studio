.PHONY: install dev test lint typecheck web-install web-build rust-test py-voice ts-client verify
install:
	python3 -m venv .venv && .venv/bin/pip install -e "services/orchestrator[dev,postgres]"
dev:
	PYTHONPATH=services/orchestrator/src .venv/bin/uvicorn voxora.api:app --reload --port 8000
ts-client:
	PYTHONPATH=services/orchestrator/src .venv/bin/python services/orchestrator/scripts/generate_ts_client.py
test:
	PYTHONPATH=services/orchestrator/src .venv/bin/pytest services/orchestrator/tests services/ai-runtime/tests -q
lint:
	.venv/bin/ruff check services/orchestrator services/ai-runtime
typecheck:
	PYTHONPATH=services/orchestrator/src .venv/bin/mypy --config-file services/orchestrator/pyproject.toml services/orchestrator/src/voxora
web-install:
	cd apps/console && npm install
web-build:
	cd apps/console && npm run build
rust-test:
	cargo test --workspace
py-voice:
	.venv/bin/pip install "maturin>=1.5,<2"
	cd crates/voice-runtime && VIRTUAL_ENV="$(CURDIR)/.venv" ../../.venv/bin/maturin develop --release
verify: lint typecheck test web-build rust-test
