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

# Create/update a staff login. Prints a generated password once.
# Override: make seed-admin ARGS="--email me@hotel.com --hotel-id 2"
seed-admin:
	docker compose -f infra/docker/docker-compose.yml run --rm backend python -m app.scripts.seed_admin $(ARGS)

# Print a fresh JWT_SECRET for .env. Touches no database.
jwt-secret:
	docker compose -f infra/docker/docker-compose.yml run --rm backend python -m app.scripts.seed_admin --print-secret
