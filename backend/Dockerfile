FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake the embedding model into the image so the container needs no
# network at runtime and the first search isn't a 20s cold start.
# Goes through the router rather than naming the model here, keeping
# model strings in exactly one place.
ENV FASTEMBED_CACHE_PATH=/opt/fastembed_cache
RUN python -c "from app.core.model_router import _get_local_model; _get_local_model()"

EXPOSE 8000

# Migrations run before the app starts, so a fresh `make dev` on an empty
# volume comes up with the schema already applied.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"]
