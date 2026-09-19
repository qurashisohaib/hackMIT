.PHONY: setup seed backend frontend demo test reset

setup:
	cd backend && uv sync
	cd frontend && npm install

seed:
	cd backend && uv run python -m app.data.seed --reset

backend:
	cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

frontend:
	cd frontend && npm run dev

demo:
	./scripts/demo.sh

test:
	cd backend && uv run pytest -q

reset:
	curl -s -X POST http://localhost:8000/api/reset | head -c 300; echo
