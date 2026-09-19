.PHONY: setup seed backend frontend demo test reset

setup:
	cd backend && uv sync --frozen
	cd frontend && npm ci

seed:
	cd backend && uv run python -m app.data.seed --reset

backend:
	cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

frontend:
	cd frontend && npm run dev

demo:
	./scripts/demo.sh

test:
	cd backend && CFO_STEP_DELAY_MS=0 CFO_LLM_ENABLED=false uv run --frozen pytest -q

reset:
	curl -s -X POST http://localhost:8000/api/reset | head -c 300; echo
