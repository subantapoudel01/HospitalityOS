dev:
	@test -f .env || cp .env.example .env
	docker compose -f infra/docker/docker-compose.yml up --build

down:
	docker compose -f infra/docker/docker-compose.yml down

test:
	docker compose -f infra/docker/docker-compose.yml run --rm backend pytest

eval:
	docker compose -f infra/docker/docker-compose.yml run --rm backend pytest tests/llm_eval -s

migrate:
	docker compose -f infra/docker/docker-compose.yml run --rm backend alembic upgrade head

revision:
	docker compose -f infra/docker/docker-compose.yml run --rm backend alembic revision --autogenerate -m "$(m)"
