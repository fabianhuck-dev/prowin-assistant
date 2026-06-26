.PHONY: up down test lint format migrate seed demo build

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build

migrate:
	uv run alembic upgrade head

seed:
	cd backend && uv run python ../scripts/seed.py

test:
	cd backend && uv run pytest -v --tb=short

lint:
	uv run ruff check backend/ scripts/
	uv run ruff format --check backend/ scripts/

format:
	uv run ruff format backend/ scripts/
	uv run ruff check --fix backend/ scripts/

demo:
	uv run python scripts/demo.py
